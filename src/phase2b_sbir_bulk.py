"""Phase 2b: SBIR.gov bulk CSV → MSA-scoped awards (offline filter).

Used because the SBIR Public API is returning 429s globally.

Source: https://data.www.sbir.gov/mod_awarddatapublic_no_abstract/award_data_no_abstract.csv
  (~70-100 MB, all SBIR/STTR awards across all agencies, no abstract column)

Filter: (city, state) ∈ city_allowlist[msa]

Outputs:
  data/raw/sbir_awards_bulk.csv          (cached download)
  data/raw/sbir_awards.parquet           (filtered)
  output/sbir_awards.{csv,xlsx}
  data/checkpoints/phase_2b.manifest.json
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    CONFIG_DIR, OUTPUT_DIR, RAW_DIR, http_get, manifest_exists, write_manifest,
)

PHASE = 22  # 2b → unique manifest id
BULK_URL = "https://data.www.sbir.gov/mod_awarddatapublic_no_abstract/award_data_no_abstract.csv"
BULK_CACHE = RAW_DIR / "sbir_awards_bulk.csv"


def load_city_state_to_msa() -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    with (CONFIG_DIR / "city_allowlist.csv").open() as f:
        for row in csv.DictReader(f):
            key = (row["city_normalized"].strip().lower(), row["state"].strip().upper())
            out.setdefault(key, row["msa"])
    return out


def download_bulk() -> Path:
    if BULK_CACHE.exists() and BULK_CACHE.stat().st_size > 1_000_000:
        print(f"  cached: {BULK_CACHE} ({BULK_CACHE.stat().st_size:,} bytes)")
        return BULK_CACHE
    print(f"  GET {BULK_URL} (this may take 1-3 min) ...")
    r = http_get(BULK_URL, source="sbir_bulk", stream=True)
    with BULK_CACHE.open("wb") as f:
        for chunk in r.iter_content(chunk_size=1 << 20):
            f.write(chunk)
    print(f"  wrote {BULK_CACHE.stat().st_size:,} bytes")
    return BULK_CACHE


def main(force: bool = False) -> None:
    if manifest_exists(PHASE) and not force:
        print(f"Phase 2b already complete. Use --force to rerun.")
        return

    cs_to_msa = load_city_state_to_msa()
    print(f"Loaded {len(cs_to_msa)} (city,state) keys")

    path = download_bulk()

    print("Reading CSV ...")
    # SBIR bulk uses standard CSV; some rows may have embedded commas in firm names → use quoting
    df = pd.read_csv(path, dtype=str, low_memory=False, on_bad_lines="warn",
                     encoding_errors="replace")
    print(f"  {len(df):,} rows, {len(df.columns)} columns")
    print(f"  columns: {list(df.columns)[:20]}")

    # Find city/state columns (header names vary by export year)
    city_col = next((c for c in df.columns
                     if c.strip().lower() in ("city", "company city", "firm city", "addressline_2_city")),
                    None)
    state_col = next((c for c in df.columns
                      if c.strip().lower() in ("state", "company state", "firm state")),
                     None)
    if not (city_col and state_col):
        print(f"ERROR: could not locate city/state columns. Cols: {list(df.columns)}",
              file=sys.stderr)
        sys.exit(1)

    STATE_NAME_TO_ABBR = {
        "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
        "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
        "district of columbia": "DC", "florida": "FL", "georgia": "GA", "hawaii": "HI",
        "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
        "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME",
        "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
        "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
        "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
        "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH",
        "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
        "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
        "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
        "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    }

    df["_city_norm"] = df[city_col].fillna("").str.strip().str.lower()
    raw_state = df[state_col].fillna("").str.strip().str.lower()
    # If full name, map to abbr; else assume already-abbr and uppercase.
    df["_state_norm"] = raw_state.map(STATE_NAME_TO_ABBR).fillna(raw_state.str.upper())
    df["_msa"] = df.apply(lambda r: cs_to_msa.get((r["_city_norm"], r["_state_norm"])), axis=1)
    n_total = len(df)
    out = df[df["_msa"].notna()].copy()
    print(f"  → {len(out):,} awards in MSA scope (filtered from {n_total:,})")

    out_path = RAW_DIR / "sbir_awards.parquet"
    out.to_parquet(out_path, index=False)
    out.to_csv(OUTPUT_DIR / "sbir_awards.csv", index=False)
    try:
        # Strip any pathological chars before xlsx write
        out_safe = out.copy()
        for c in out_safe.select_dtypes(include="object").columns:
            out_safe[c] = out_safe[c].astype(str).str.replace(
                r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", regex=True
            )
        out_safe.to_excel(OUTPUT_DIR / "sbir_awards.xlsx", index=False, engine="openpyxl")
    except Exception as e:
        print(f"  WARN xlsx failed: {e}", file=sys.stderr)

    by_msa = out.groupby("_msa").size().to_dict()
    write_manifest(PHASE, {
        "total_in_scope": len(out),
        "total_bulk_rows": n_total,
        "by_msa": by_msa,
        "source": "sbir bulk csv (api 429 fallback)",
    })
    print("\nBy MSA:")
    for k, v in sorted(by_msa.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v:,}")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
