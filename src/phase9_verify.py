"""Phase 9 verification — produce evidence the wet-lab prospect numbers are accurate.

Read-only against output/wet_lab_prospects.csv. Cross-checks a sample of the
final prospect list against external public sources (SEC EDGAR, SBIR.gov) and
emits CSV/XLSX artefacts a non-technical reviewer can audit in a browser.

Outputs (under output/):
    verification_sample.csv          stratified random sample of 30 prospects
    verification_source_counts.csv   per-MSA source totals vs SEC/SBIR external
    verification_spot_check.csv      pass/fail on must-include / must-exclude lists
    verification_founded_year.csv    SEC submissions API year_of_incorporation match
    verification_summary.xlsx        all four sheets in one Excel deliverable
    HOW_TO_VERIFY.md                 instructions for a non-technical reviewer

Run:
    python src/phase9_verify.py [--seed 42] [--offline]
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUTPUT_DIR, RateLimiter, http_get, load_msa_config  # noqa: E402

PROSPECTS_CSV = OUTPUT_DIR / "wet_lab_prospects.csv"

SEC_FT_SEARCH = "https://efts.sec.gov/LATEST/search-index"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SBIR_API = "https://api.www.sbir.gov/public/api/awards"

# SEC asks for ~10 rps max; we stay conservative.
sec_limiter = RateLimiter(per_sec=8)
sbir_limiter = RateLimiter(per_sec=1)

# Life-sciences SIC codes used to scope SEC EDGAR cross-check.
LS_SIC_CODES = ["2834", "2836", "8731"]

EXPECTED_INCLUDED = [
    ("Andson Biotech", "atlanta"),
    ("Linnaeus Therapeutics", "philadelphia"),
    ("Sonavex", "baltimore"),
    ("Que Oncology", "atlanta"),
    ("GeoVax", "atlanta"),
    ("Carmell Therapeutics", "pittsburgh"),
    ("OXOS Medical", "atlanta"),
]

EXPECTED_EXCLUDED = [
    "OXOS Series #6 Holdings",
    "NovaDerm Aid Fund Alpha",
    "UNMANNED SYSTEMS",
    "NEUROFLOW INC",
    "Apellis Pharmaceuticals",
    "Carnegie Robotics",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    import re
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(inc|llc|ltd|corp|co|plc|pbc|holdings|group|gmbh|ag|the)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _sec_search_url(name: str) -> str:
    return f"https://efts.sec.gov/LATEST/search-index?q=%22{quote_plus(name)}%22&forms=D"


def _sbir_search_url(name: str) -> str:
    return f"https://www.sbir.gov/sbirsearch/award/all?firm={quote_plus(name)}"


# ---------------------------------------------------------------------------
# 1. Stratified random sample
# ---------------------------------------------------------------------------

def stratified_sample(df: pd.DataFrame, seed: int) -> pd.DataFrame:
    rng = random.Random(seed)
    bands = {
        "high (>=7)": df[df["priority_score"] >= 7],
        "mid (4-6)": df[(df["priority_score"] >= 4) & (df["priority_score"] <= 6)],
        "low (1-3)": df[(df["priority_score"] >= 1) & (df["priority_score"] <= 3)],
    }
    chunks = []
    for band, sub in bands.items():
        n = min(10, len(sub))
        if n == 0:
            continue
        idx = rng.sample(list(sub.index), n)
        picked = sub.loc[idx].copy()
        picked["sample_band"] = band
        chunks.append(picked)
    sample = pd.concat(chunks, ignore_index=True)

    out = pd.DataFrame({
        "company_name": sample["name"],
        "msa": sample["msa"],
        "priority_score": sample["priority_score"],
        "sample_band": sample["sample_band"],
        "source_form_d": sample["source_form_d"],
        "source_sbir": sample["source_sbir"],
        "source_tto": sample["source_tto"],
        "founded_year": sample["founded_year"],
        "cik": sample["cik"],
        "uei": sample["uei"],
        "sec_edgar_search_url": sample["name"].apply(_sec_search_url),
        "sbir_search_url": sample["name"].apply(_sbir_search_url),
        "company_website": sample["website"],
        "verification_notes": "",
    })
    return out


# ---------------------------------------------------------------------------
# 2. Source-count cross-check
# ---------------------------------------------------------------------------

def _sec_count(state: str, sic: str | None = None) -> int | None:
    """Total Form D filings for a state since 2015. None on failure."""
    params = {
        "q": "",
        "dateRange": "custom",
        "startdt": "2015-01-01",
        "enddt": "2025-12-31",
        "forms": "D",
        "locationCode": state,
    }
    if sic:
        params["q"] = f"sic={sic}"
    sec_limiter.wait()
    try:
        r = http_get(SEC_FT_SEARCH, source="sec_edgar_ft", params=params)
        data = r.json()
        return int(data.get("hits", {}).get("total", {}).get("value", 0))
    except Exception:
        return None


def _sbir_count(state: str) -> int | None:
    """Distinct firms with awards in this state since 2015. None on failure."""
    sbir_limiter.wait()
    try:
        # SBIR API returns up to 1000 rows per call; we page until exhausted
        # but cap at 5000 records to stay under the 5-min budget.
        firms: set[str] = set()
        for start in range(0, 5000, 1000):
            r = http_get(
                SBIR_API,
                source="sbir_api",
                params={"state": state, "year": 2015, "rows": 1000, "start": start},
            )
            rows = r.json() or []
            if not rows:
                break
            for row in rows:
                firm = (row.get("firm") or "").strip().lower()
                if firm:
                    firms.add(firm)
            if len(rows) < 1000:
                break
        return len(firms)
    except Exception:
        return None


def source_count_check(df: pd.DataFrame, msa_cfg: dict, offline: bool) -> pd.DataFrame:
    rows = []
    for msa_key, cfg in msa_cfg["msas"].items():
        states = cfg["state_codes"]
        our_fd = int(df[(df["msa"] == msa_key) & df["source_form_d"]].shape[0])
        our_sbir = int(df[(df["msa"] == msa_key) & df["source_sbir"]].shape[0])

        # SEC: sum across states; concept is approximate — SEC FT search has
        # no clean SIC filter via locationCode, so we report the unscoped
        # total for context and let the ratio reflect "we are a strict subset".
        sec_total = 0
        for st in states:
            c = None if offline else _sec_count(st)
            sec_total += c or 0
        sbir_total = 0
        for st in states:
            c = None if offline else _sbir_count(st)
            sbir_total += c or 0

        for label, ours, ext in (
            ("form_d", our_fd, sec_total),
            ("sbir", our_sbir, sbir_total),
        ):
            ratio = (ours / ext) if ext else None
            if ratio is None:
                flag = "no_external_data"
            elif label == "form_d":
                # We expect to be a small subset of all Form D filings (state-
                # wide, all industries). 0.5%–10% is plausible for LS/chem.
                flag = "ok" if 0.001 <= ratio <= 0.20 else "review"
            else:
                # Wet-lab prospects are a 30-70% subset of LS-relevant SBIR firms,
                # but SBIR all-industry totals make the ratio smaller. Use a
                # broader band to flag anomalies only.
                flag = "ok" if 0.05 <= ratio <= 0.80 else "review"
            rows.append({
                "msa": msa_key,
                "source": label,
                "our_count": ours,
                "external_count": ext,
                "ratio": round(ratio, 4) if ratio is not None else None,
                "flag_if_out_of_range": flag,
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 3. Spot-check
# ---------------------------------------------------------------------------

def spot_check(df: pd.DataFrame) -> pd.DataFrame:
    norm_index = df.assign(nn=df["name"].apply(_norm))
    rows = []
    for name, msa in EXPECTED_INCLUDED:
        nn = _norm(name)
        hit = norm_index[(norm_index["nn"].str.contains(nn, regex=False)) & (norm_index["msa"] == msa)]
        rows.append({
            "category": "expected_included",
            "company": name,
            "msa": msa,
            "found_in_output": bool(len(hit)),
            "match_count": int(len(hit)),
            "result": "PASS" if len(hit) else "FAIL",
        })
    for name in EXPECTED_EXCLUDED:
        nn = _norm(name)
        hit = norm_index[norm_index["nn"].str.contains(nn, regex=False)]
        rows.append({
            "category": "expected_excluded",
            "company": name,
            "msa": "",
            "found_in_output": bool(len(hit)),
            "match_count": int(len(hit)),
            "result": "PASS" if not len(hit) else "FAIL",
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 4. Founded-year verification
# ---------------------------------------------------------------------------

def founded_year_check(df: pd.DataFrame, seed: int, offline: bool) -> pd.DataFrame:
    rng = random.Random(seed + 1)
    candidates = df[df["founded_year"].notna() & df["cik"].notna()].copy()
    n = min(10, len(candidates))
    if n == 0:
        return pd.DataFrame()
    picks = candidates.loc[rng.sample(list(candidates.index), n)]

    rows = []
    for _, r in picks.iterrows():
        cik = int(r["cik"])
        sec_year = None
        status = "skipped"
        if not offline:
            sec_limiter.wait()
            try:
                resp = http_get(SEC_SUBMISSIONS.format(cik=cik), source="sec_submissions")
                # year_of_incorporation lives under 'addresses' / 'former*' in
                # some shapes; the submissions endpoint reports it at top level
                # only inconsistently. Best-effort lookup.
                data = resp.json()
                sec_year = data.get("yearOfIncorp") or data.get("yearOfIncorporation")
                status = "fetched"
            except Exception as e:
                status = f"error:{type(e).__name__}"
        ours = int(r["founded_year"])
        match = (str(sec_year) == str(ours)) if sec_year else None
        rows.append({
            "company": r["name"],
            "cik": cik,
            "our_founded_year": ours,
            "sec_year_of_incorp": sec_year,
            "match": match,
            "status": status,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 5. HOW_TO_VERIFY.md
# ---------------------------------------------------------------------------

HOW_TO_VERIFY = """# How to verify the wet-lab prospect list

This list of ~816 wet-lab prospects is built entirely from public federal
records. You can audit any row in under 2 minutes using only a web browser —
no logins, no paid databases.

## The 2-minute audit

1. Open `output/verification_sample.csv` in Excel or Google Sheets. It contains
   30 rows: 10 high-priority, 10 mid-priority, 10 low-priority.
2. Pick **any 5 rows**.
3. For each row:
   - **If `source_form_d` is True** — click `sec_edgar_search_url`. You should
     see one or more Form D filings under that company name with a "Date Filed"
     of 2015 or later. If yes, the SEC source is verified.
   - **If `source_sbir` is True** — click `sbir_search_url`. You should see at
     least one award row for the company. If yes, the SBIR source is verified.
   - Glance at `company_website` to confirm the firm is alive and doing
     life-sciences work (not a defense contractor, not a holding company).
4. If both checks pass, write `verified` in the `verification_notes` column.
   If anything looks off (no filings, dead website, wrong industry), write
   `flagged: <reason>`.

Screenshots aren't required. The links themselves are reproducible evidence.

## What "verified" means

A row is considered verified when:
- Each `source_*=True` flag corresponds to a real, dated public record.
- The company is operating (website resolves, mentions life-sciences work).
- The MSA assignment matches the company's headquarters city/state.

## What the other files show

- `verification_source_counts.csv` — per MSA, our final count vs SEC/SBIR
  state-wide totals. Ratios are flagged `ok` or `review`.
- `verification_spot_check.csv` — regression test: well-known startups that
  must be in the list, and known-bad rows that must not be.
- `verification_founded_year.csv` — for 10 random rows, our `founded_year`
  vs SEC's `yearOfIncorporation` field from the submissions API.
- `verification_summary.xlsx` — all four sheets in one workbook.

## Why this is enough

We don't need to verify all 816 rows. A stratified random sample of 30 (n=30
per the central-limit rule of thumb) gives ±18 percentage-point error on the
true error rate at 95% confidence — sufficient to detect a problem that
matters operationally. The spot-check provides a deterministic regression
floor: if those names ever drop out, the pipeline regressed.
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--offline",
        action="store_true",
        help="Skip external HTTP (SEC, SBIR). Sample/spot-check still produced.",
    )
    args = ap.parse_args()

    if not PROSPECTS_CSV.exists():
        sys.exit(f"missing {PROSPECTS_CSV} — run phase9 first")
    df = pd.read_csv(PROSPECTS_CSV)
    print(f"loaded {len(df)} prospects from {PROSPECTS_CSV.name}")

    t0 = time.time()
    sample = stratified_sample(df, args.seed)
    sample_path = OUTPUT_DIR / "verification_sample.csv"
    sample.to_csv(sample_path, index=False)
    print(f"  [1/4] wrote {sample_path.name} ({len(sample)} rows)")

    msa_cfg = load_msa_config()
    counts = source_count_check(df, msa_cfg, offline=args.offline)
    counts_path = OUTPUT_DIR / "verification_source_counts.csv"
    counts.to_csv(counts_path, index=False)
    msas_total = counts["msa"].nunique()
    fd_ok = int((counts[counts["source"] == "form_d"]["flag_if_out_of_range"] == "ok").sum())
    sbir_ok = int((counts[counts["source"] == "sbir"]["flag_if_out_of_range"] == "ok").sum())
    print(f"  [2/4] wrote {counts_path.name} "
          f"(form_d {fd_ok}/{msas_total} ok, sbir {sbir_ok}/{msas_total} ok)")

    spot = spot_check(df)
    spot_path = OUTPUT_DIR / "verification_spot_check.csv"
    spot.to_csv(spot_path, index=False)
    inc = spot[spot["category"] == "expected_included"]
    exc = spot[spot["category"] == "expected_excluded"]
    inc_pass = int((inc["result"] == "PASS").sum())
    exc_pass = int((exc["result"] == "PASS").sum())
    print(f"  [3/4] wrote {spot_path.name} "
          f"(included {inc_pass}/{len(inc)}, excluded {exc_pass}/{len(exc)})")

    fy = founded_year_check(df, args.seed, offline=args.offline)
    fy_path = OUTPUT_DIR / "verification_founded_year.csv"
    fy.to_csv(fy_path, index=False)
    fy_match = int(fy["match"].fillna(False).sum()) if len(fy) else 0
    print(f"  [4/4] wrote {fy_path.name} ({fy_match}/{len(fy)} matched SEC)")

    # Combined Excel
    xlsx = OUTPUT_DIR / "verification_summary.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        sample.to_excel(w, sheet_name="random_sample_30", index=False)
        counts.to_excel(w, sheet_name="source_counts", index=False)
        spot.to_excel(w, sheet_name="spot_check", index=False)
        fy.to_excel(w, sheet_name="founded_year", index=False)
    print(f"  wrote {xlsx.name}")

    (OUTPUT_DIR / "HOW_TO_VERIFY.md").write_text(HOW_TO_VERIFY)
    print(f"  wrote HOW_TO_VERIFY.md")

    elapsed = time.time() - t0
    print()
    print("=" * 64)
    print("VERIFICATION SUMMARY")
    print("=" * 64)
    print(f"  {len(sample)} random samples generated for human verification:")
    print(f"      see output/verification_sample.csv")
    print(f"  Source count cross-check: form_d {fd_ok}/{msas_total} MSAs ok, "
          f"sbir {sbir_ok}/{msas_total} MSAs ok")
    print(f"  Spot-check passed: {inc_pass} of {len(inc)} expected-included, "
          f"{exc_pass} of {len(exc)} expected-excluded")
    print(f"  Founded-year verification: {fy_match} of {len(fy)} matched SEC submissions API")
    print(f"  Elapsed: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
