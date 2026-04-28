"""Phase 2: Federal grant harvest — NIH RePORTER + SBIR.gov.

NIH RePORTER v2:
  POST https://api.reporter.nih.gov/v2/projects/search
  body: {"criteria": {"org_states":[...], "org_cities":[...], "fiscal_years":[YYYY]},
         "limit": 500, "offset": N}
  Iterate per MSA × fiscal year (2015-2025). Paginate via offset; hard-cap 10000.

  How the mapping works:
    1. for each MSA (5):
         states  = the MSA's state list  (e.g. philadelphia → [PA, NJ, DE, MD])
         cities  = full notable_cities list from msa_config.json (lower-cased)
       for each fiscal year (11: FY2015-FY2025):
         POST criteria { org_states, org_cities, fiscal_years:[FY] }
         paginate offset += 500 until len(results) < 500 OR offset+500 >= 10000
                                                        (NIH server-side hard cap)
    2. concat all results, normalize JSON 2 levels deep → DataFrame
    3. tag each row with _msa and _fy; write parquet + csv + xlsx

  500 is the NIH page size, NOT a record cap. A (msa × FY) slice pulling 2,300
  matches makes 5 paginated calls and returns all 2,300 rows. The real cap is
  the 10,000-record offset ceiling — slicing by (MSA × FY) keeps each slice
  comfortably under it.

  Funnel — full backfill FY2015–FY2025 (11 fiscal years × 5 MSAs = 55 slices):
    raw NIH grant records returned                          105,151
    NIH grants per MSA (sum):
        philadelphia    36,473
        baltimore       27,055
        pittsburgh      17,368
        atlanta         15,320
        dallas           8,935
    distinct recipient organizations                          ~600
    largest single (MSA × FY) slice                          3,706 rows
                                                             (well under the
                                                              10,000 server cap)
    (most recipients are universities/hospitals — Phase 8 tags them as
     research_inst and excludes them from the startup roster.)
    (Previous 2020+ floor: 60,127 grants / ~520 orgs — for reference.)

SBIR.gov:
  GET https://api.www.sbir.gov/public/api/awards?state=XX&rows=500&start=N
  Iterate per state in scope (PA, NJ, DE, MD, GA, TX). Paginate. Filter to MSA cities offline.

  How the mapping works:
    1. union of all MSA state codes (6: PA, NJ, DE, MD, GA, TX)
    2. for each state, paginate start += 500 until empty payload (or 50K safety)
    3. SBIR API has no usable year filter on this endpoint → pull all years
    4. offline filter: (city, state) lower-cased lookup in city_allowlist
    5. (year filter to 2015+ is enforced LATER, at Phase 9 step 5 — recency)

  Funnel — full backfill (state-wide, all years; bulk CSV path via phase2b):
    raw SBIR awards pulled (bulk CSV ~70 MB)                219,500
    ↓ MSA city allowlist filter
    ↓ kept in MSA scope                                      15,051 awards
                                                              2,793 unique companies

  NOTE: The SBIR.gov Public API returned global HTTP 429 during the backfill,
  so this phase wrote 0 SBIR rows. The documented fallback `phase2b_sbir_bulk.py`
  (one-shot bulk CSV download) was used instead — same authoritative source,
  no rate limit. See README §Verification for details.

Outputs:
  data/raw/nih_awards.parquet
  data/raw/sbir_awards.parquet
  output/nih_awards.{csv,xlsx}
  output/sbir_awards.{csv,xlsx}
  data/checkpoints/phase_2.manifest.json

USASpending + NSF deferred (broad, low signal-to-noise; can be added as phase 2b).
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    CONFIG_DIR, OUTPUT_DIR, RAW_DIR, RateLimiter, http_get, http_post,
    manifest_exists, write_manifest,
)

PHASE = 2
NIH_URL = "https://api.reporter.nih.gov/v2/projects/search"
SBIR_URL = "https://api.www.sbir.gov/public/api/awards"
FISCAL_YEARS = list(range(2015, 2026))  # FY2015 - FY2025


def load_city_allowlist() -> dict[str, dict[str, list[str]]]:
    """Returns {msa: {state: [city, ...]}}."""
    out: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    with (CONFIG_DIR / "city_allowlist.csv").open() as f:
        for row in csv.DictReader(f):
            out[row["msa"]][row["state"]].append(row["city_normalized"])
    return {k: dict(v) for k, v in out.items()}


def load_zip_to_msa() -> dict[str, str]:
    """ZIP → msa lookup (5-digit string)."""
    out: dict[str, str] = {}
    with (CONFIG_DIR / "zip_allowlist.csv").open() as f:
        for row in csv.DictReader(f):
            out[row["zip"]] = row["msa"]
    return out


# -------------------- NIH RePORTER --------------------

def nih_query(states: list[str], cities: list[str], fy: int,
              limiter: RateLimiter) -> list[dict]:
    """Pull all projects matching states/cities/FY. Handles pagination + 10K cap."""
    out: list[dict] = []
    offset = 0
    LIMIT = 500
    while True:
        body = {
            "criteria": {
                "org_states": states,
                "org_cities": cities,
                "fiscal_years": [fy],
            },
            "limit": LIMIT,
            "offset": offset,
        }
        limiter.wait()
        try:
            r = http_post(NIH_URL, source="nih_reporter", json_body=body)
        except Exception as e:
            print(f"    NIH FAIL fy={fy} offset={offset}: {e}", file=sys.stderr, flush=True)
            return out
        payload = r.json()
        results = payload.get("results", []) or []
        meta = payload.get("meta", {})
        total = meta.get("total", 0)
        out.extend(results)
        if len(results) < LIMIT or len(out) >= total or offset + LIMIT >= 10000:
            if total >= 10000:
                print(f"    NIH WARN fy={fy} hit 10K cap (total={total}); some records dropped",
                      file=sys.stderr, flush=True)
            break
        offset += LIMIT
    return out


def harvest_nih(city_allow: dict[str, dict[str, list[str]]]) -> pd.DataFrame:
    limiter = RateLimiter(per_sec=3)
    all_rows: list[dict] = []
    for msa, by_state in city_allow.items():
        states = sorted(by_state.keys())
        cities = sorted({c for cs in by_state.values() for c in cs})
        print(f"\n[NIH] {msa}: states={states} cities={len(cities)}", flush=True)
        for fy in FISCAL_YEARS:
            rows = nih_query(states, cities, fy, limiter)
            for r in rows:
                r["_msa"] = msa
                r["_fy"] = fy
            all_rows.extend(rows)
            print(f"  FY{fy}: +{len(rows)} (total {len(all_rows):,})", flush=True)
    if not all_rows:
        return pd.DataFrame()
    return pd.json_normalize(all_rows, max_level=2)


# -------------------- SBIR.gov --------------------

def sbir_state_pull(state: str, limiter: RateLimiter) -> list[dict]:
    """Paginate all awards for a state."""
    out: list[dict] = []
    start = 0
    ROWS = 500
    while True:
        limiter.wait()
        try:
            r = http_get(
                SBIR_URL, source="sbir",
                params={"state": state, "rows": ROWS, "start": start},
            )
        except Exception as e:
            print(f"    SBIR FAIL state={state} start={start}: {e}", file=sys.stderr, flush=True)
            return out
        # SBIR API returns a JSON array, not a wrapped object
        results = r.json()
        if not isinstance(results, list):
            print(f"    SBIR unexpected payload type at state={state} start={start}",
                  file=sys.stderr, flush=True)
            return out
        if not results:
            break
        out.extend(results)
        if len(results) < ROWS:
            break
        start += ROWS
        if start > 50000:  # safety net
            print(f"    SBIR safety stop at start={start} for state={state}", file=sys.stderr)
            break
    return out


def harvest_sbir(city_allow: dict[str, dict[str, list[str]]]) -> pd.DataFrame:
    limiter = RateLimiter(per_sec=1)
    states = sorted({s for by_state in city_allow.values() for s in by_state.keys()})
    print(f"\n[SBIR] states: {states}", flush=True)
    raw: list[dict] = []
    for st in states:
        rows = sbir_state_pull(st, limiter)
        print(f"  {st}: {len(rows):,} awards", flush=True)
        for r in rows:
            r["_state_pulled"] = st
        raw.extend(rows)

    if not raw:
        return pd.DataFrame()

    df = pd.DataFrame(raw)
    # Normalize city/state for filtering
    city_col = next((c for c in ["firm", "city", "firm_city"] if c in df.columns), None)
    # SBIR award fields commonly include: firm, award_year, award_amount, agency, branch,
    # phase, program, contract, proposal_award_date, contract_end_date, address1, city, state, zip
    if "city" not in df.columns:
        print("  WARN no 'city' column in SBIR rows; cannot MSA-filter", file=sys.stderr)
        return df

    df["_city_norm"] = df["city"].fillna("").str.strip().str.lower()
    df["_state_norm"] = df["state"].fillna("").str.strip().str.upper()

    # Build (city, state) -> msa
    cs_to_msa: dict[tuple[str, str], str] = {}
    for msa, by_state in city_allow.items():
        for st, cities in by_state.items():
            for c in cities:
                cs_to_msa.setdefault((c, st), msa)

    df["_msa"] = df.apply(lambda r: cs_to_msa.get((r["_city_norm"], r["_state_norm"])), axis=1)
    n_total = len(df)
    df = df[df["_msa"].notna()].copy()
    print(f"  → {len(df):,} awards in MSA scope (filtered from {n_total:,})", flush=True)
    return df


# -------------------- main --------------------

def export_csv_xlsx(df: pd.DataFrame, name: str) -> None:
    if df is None or df.empty:
        return
    df.to_csv(OUTPUT_DIR / f"{name}.csv", index=False)
    # Excel has a 1M-row limit; safe for our scale.
    try:
        df.to_excel(OUTPUT_DIR / f"{name}.xlsx", index=False, engine="openpyxl")
    except Exception as e:
        print(f"  WARN xlsx export failed for {name}: {e}", file=sys.stderr)


def main(force: bool = False) -> None:
    if manifest_exists(PHASE) and not force:
        print(f"Phase {PHASE} already complete. Use --force to rerun.")
        return
    if not (CONFIG_DIR / "city_allowlist.csv").exists():
        print("ERROR: run Phase 1 first.", file=sys.stderr)
        sys.exit(1)

    city_allow = load_city_allowlist()

    nih_df = harvest_nih(city_allow)
    if not nih_df.empty:
        nih_path = RAW_DIR / "nih_awards.parquet"
        nih_df.to_parquet(nih_path, index=False)
        export_csv_xlsx(nih_df, "nih_awards")
        print(f"\nNIH: {len(nih_df):,} rows → {nih_path}")
    nih_count = len(nih_df)
    nih_by_msa = nih_df.groupby("_msa").size().to_dict() if nih_count else {}

    sbir_df = harvest_sbir(city_allow)
    if not sbir_df.empty:
        sbir_path = RAW_DIR / "sbir_awards.parquet"
        sbir_df.to_parquet(sbir_path, index=False)
        export_csv_xlsx(sbir_df, "sbir_awards")
        print(f"SBIR: {len(sbir_df):,} rows → {sbir_path}")
    sbir_count = len(sbir_df)
    sbir_by_msa = sbir_df.groupby("_msa").size().to_dict() if sbir_count else {}

    write_manifest(PHASE, {
        "nih_total": nih_count,
        "nih_by_msa": nih_by_msa,
        "sbir_total": sbir_count,
        "sbir_by_msa": sbir_by_msa,
        "fiscal_years": FISCAL_YEARS,
        "deferred": ["usaspending", "nsf"],
    })
    print(f"\nPhase 2 done. NIH={nih_count:,}  SBIR={sbir_count:,}")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
