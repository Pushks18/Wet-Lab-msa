"""Phase 3: SEC Form D quarterly bulk → MSA-scoped LS/biotech/pharma issuers.

Source: https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets
  Quarterly ZIPs at: https://www.sec.gov/files/dera/data/form-d-data-sets/{YYYYqQ}_d.zip
  Each ZIP contains tab-separated files:
    FORMDSUBMISSION.tsv  — accession, file_num, filer info
    ISSUERS.tsv          — issuer name, address, city, state, zip, ENTITYTYPE, YEARINCFROM
    OFFERING.tsv         — totalOfferingAmount, INDUSTRYGROUPTYPE, offered/sold amounts
    RECIPIENTS.tsv       — broker/finder info (lead investors lookup)

How the mapping works, per quarter:
  1. download {YYYY}q{Q}_d.zip from SEC (1 req/sec, User-Agent w/ email — SEC ToS)
  2. extract 4 TSVs into data/raw/form_d/{YYYY}q{Q}/
  3. join ISSUERS ⋈ OFFERING ⋈ FORMDSUBMISSION on ACCESSIONNUMBER
     → one row per (filing × issuer); a Form D can have multiple issuers
  4. apply 3 AND-ed filters (all must pass to keep the row):
       a) state          ∈ MSA states (PA, NJ, DE, MD, GA, TX)
       b) (city, state)  ∈ city_allowlist  (1,243 tuples from Phase 1 HUD crosswalk)
       c) industry_group ∈ {Pharmaceuticals, Biotechnology, Other Health Care}
  5. tag row with _quarter, append to all_filtered, repeat next quarter
  6. concat all quarters → data/raw/form_d_filings.parquet

Funnel — actual numbers under the previous 2020+ floor (24 quarters, 2020-Q1 → 2025-Q4):
  raw issuer-offering rows across all quarters   ~120,000  (median ~5,000 / quarter)
  ↓ state + city allowlist filter
  ↓ industry filter (Pharma / Biotech / Other Health Care)
  ↓ kept                                            1,408 filings
                                                      686 unique CIKs
                                                      718 unique company names

  Per MSA (committed 2020+ data):
              filings  unique CIKs   Biotech / Other HC / Pharma
    philadelphia 488     242            189 /  229 /  70
    dallas       367     213             95 /  246 /  26
    atlanta      229      95             90 /  128 /  11
    baltimore    179      77             93 /   63 /  23
    pittsburgh   145      61             44 /   75 /  26

  After expanding to START_YEAR = 2015 (44 quarters), counts are expected to roughly
  scale with the wider window — recompute from output/form_d_filings.csv after
  the next --force run; the Phase 9 funnel in the README will reflect the new totals.

Outputs:
  data/raw/form_d/{YYYYqQ}/*.tsv         (extracted, one folder per quarter)
  data/raw/form_d_filings.parquet        (filtered, joined ISSUERS+OFFERING+SUBMISSION)
  data/checkpoints/phase_3.manifest.json

SEC ToS: User-Agent must include contact email; rate limit ≤10 req/sec (we use 1).
"""
from __future__ import annotations

import csv
import io
import os
import sys
import time
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    CONFIG_DIR, RAW_DIR, RateLimiter, http_get, manifest_exists, write_manifest,
)

PHASE = 3
SEC_BASE = "https://www.sec.gov/files/structureddata/data/form-d-data-sets"
RAW_FORMD = RAW_DIR / "form_d"
RAW_FORMD.mkdir(parents=True, exist_ok=True)

IN_SCOPE_INDUSTRY = {
    "Pharmaceuticals",
    "Biotechnology",
    "Other Health Care",
    # Some quarters use shortened/upper variants — normalized comparison handles them.
}

START_YEAR = 2015
START_Q = 1


def quarters_through(end_year: int, end_q: int) -> list[tuple[int, int]]:
    out = []
    y, q = START_YEAR, START_Q
    while (y, q) <= (end_year, end_q):
        out.append((y, q))
        q += 1
        if q > 4:
            q = 1
            y += 1
    return out


def current_quarter() -> tuple[int, int]:
    today = date.today()
    # SEC publishes ~30–45 days after quarter close; we target the LAST fully-published quarter.
    # Heuristic: subtract 60 days from today, then derive that date's quarter.
    from datetime import timedelta
    d = today - timedelta(days=60)
    return d.year, (d.month - 1) // 3 + 1


def download_quarter(year: int, q: int, limiter: RateLimiter) -> Path:
    qdir = RAW_FORMD / f"{year}q{q}"
    if qdir.exists() and any(qdir.iterdir()):
        return qdir
    url = f"{SEC_BASE}/{year}q{q}_d.zip"
    print(f"  GET {url}", flush=True)
    limiter.wait()
    r = http_get(url, source="sec_formd")
    qdir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        zf.extractall(qdir)
    return qdir


def load_quarter(qdir: Path) -> pd.DataFrame | None:
    """Join FORMDSUBMISSION + ISSUERS + OFFERING; return one row per (accession, issuer)."""
    # Some ZIPs extract files flat into qdir; others extract into a subfolder like 2020Q1_d/.
    files = {p.name.upper(): p for p in qdir.rglob("*") if p.is_file()}
    sub = next((files[k] for k in files if k.startswith("FORMDSUBMISSION")), None)
    iss = next((files[k] for k in files if k.startswith("ISSUERS")), None)
    off = next((files[k] for k in files if k.startswith("OFFERING")), None)
    if not (sub and iss and off):
        print(f"  WARN missing tables in {qdir.name}", file=sys.stderr)
        return None

    def read_tsv(p: Path) -> pd.DataFrame:
        return pd.read_csv(p, sep="\t", dtype=str, on_bad_lines="skip", encoding="latin-1")

    s = read_tsv(sub)
    i = read_tsv(iss)
    o = read_tsv(off)

    # Normalize column names to uppercase for cross-quarter consistency
    for df in (s, i, o):
        df.columns = [c.upper() for c in df.columns]

    # Join on ACCESSIONNUMBER (always present in all 3 tables)
    merged = (
        i.merge(o, on="ACCESSIONNUMBER", how="left", suffixes=("", "_o"))
         .merge(s[["ACCESSIONNUMBER", "FILING_DATE"]] if "FILING_DATE" in s.columns
                else s[["ACCESSIONNUMBER"]], on="ACCESSIONNUMBER", how="left")
    )
    return merged


def filter_msa_ls(df: pd.DataFrame, city_state_to_msa: dict[tuple[str, str], str]) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    # Normalize
    df["_city_norm"] = df.get("ISSUER_CITY", df.get("CITY", "")).fillna("").str.strip().str.lower()
    df["_state_norm"] = df.get("ISSUER_STATEORCOUNTRY", df.get("STATEORCOUNTRY", "")).fillna("").str.strip().str.upper()
    industry_col = next((c for c in df.columns if "INDUSTRY" in c and "TYPE" in c), None)
    if industry_col:
        df["_industry_norm"] = df[industry_col].fillna("").str.strip()
    else:
        df["_industry_norm"] = ""

    df["_msa"] = df.apply(
        lambda r: city_state_to_msa.get((r["_city_norm"], r["_state_norm"])),
        axis=1,
    )
    in_scope_industry = df["_industry_norm"].isin(IN_SCOPE_INDUSTRY)
    in_scope_msa = df["_msa"].notna()
    return df[in_scope_msa & in_scope_industry].copy()


def main(force: bool = False) -> None:
    if manifest_exists(PHASE) and not force:
        print(f"Phase {PHASE} already complete. Use --force to rerun.")
        return
    if not os.getenv("USER_AGENT_EMAIL"):
        print("ERROR: set USER_AGENT_EMAIL in .env (SEC ToS requires it).", file=sys.stderr)
        sys.exit(1)

    # Load city allowlist into (city, state) -> msa map
    city_csv = CONFIG_DIR / "city_allowlist.csv"
    if not city_csv.exists():
        print("ERROR: run Phase 1 first.", file=sys.stderr)
        sys.exit(1)

    city_state_to_msa: dict[tuple[str, str], str] = {}
    with city_csv.open() as f:
        for row in csv.DictReader(f):
            key = (row["city_normalized"].strip().lower(), row["state"].strip().upper())
            # First-write wins; MSAs do not overlap (separate states for all 5)
            city_state_to_msa.setdefault(key, row["msa"])

    print(f"Loaded {len(city_state_to_msa)} (city,state) keys")

    end_y, end_q = current_quarter()
    quarters = quarters_through(end_y, end_q)
    print(f"Targeting {len(quarters)} quarters: {quarters[0]} → {quarters[-1]}")

    limiter = RateLimiter(per_sec=1)
    all_filtered: list[pd.DataFrame] = []
    quarter_summary: dict[str, dict] = {}

    for y, q in quarters:
        tag = f"{y}q{q}"
        print(f"\n=== {tag} ===", flush=True)
        try:
            qdir = download_quarter(y, q, limiter)
        except Exception as e:
            print(f"  FAIL download: {e}", file=sys.stderr, flush=True)
            quarter_summary[tag] = {"status": "download_failed", "error": str(e)}
            continue
        df = load_quarter(qdir)
        if df is None:
            quarter_summary[tag] = {"status": "no_tables"}
            continue
        filt = filter_msa_ls(df, city_state_to_msa)
        n_total = len(df)
        n_kept = len(filt)
        print(f"  {n_total:,} total issuer-offering rows → {n_kept} in MSA + LS scope", flush=True)
        quarter_summary[tag] = {"status": "ok", "total_rows": n_total, "kept": n_kept}
        if n_kept:
            filt["_quarter"] = tag
            all_filtered.append(filt)

    if not all_filtered:
        print("\nNo in-scope rows found across all quarters.", file=sys.stderr)
        sys.exit(2)

    out = pd.concat(all_filtered, ignore_index=True)
    out_path = RAW_DIR / "form_d_filings.parquet"
    out.to_parquet(out_path, index=False)
    print(f"\nWrote {len(out):,} rows → {out_path}")

    write_manifest(PHASE, {
        "quarters_processed": len(quarters),
        "quarter_summary": quarter_summary,
        "total_in_scope_rows": len(out),
        "output": str(out_path.relative_to(RAW_DIR.parent.parent)),
    })


if __name__ == "__main__":
    main(force="--force" in sys.argv)
