"""Phase 7a (interim): Merge Form D + SBIR firms + NIH recipient orgs into a single
roster keyed by MSA, with cross-source counts.

Source confidence (used to set tier later):
  - Form D filing  → tier "confirmed_funded"
  - SBIR award     → tier "confirmed_grant"
  - NIH grant only → tier "research_org" (most are universities/hospitals — non-startup)

Outputs:
  output/companies_interim.csv               (one row per company)
  output/companies_interim_counts.csv        (per-MSA counts)
  output/companies_interim_by_msa/{msa}.csv  (per-MSA roster, easy to skim)

Dedup keys (in order):
  1. Normalized name + state (lowercased, suffixes stripped, whitespace collapsed)
  2. SEC CIK (Form D)
  3. UEI / DUNS (SBIR)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUTPUT_DIR, RAW_DIR

PHASE = 71

NAME_SUFFIX = re.compile(
    r"\b(inc|incorporated|llc|l\.l\.c\.|ltd|limited|corp|corporation|co|company|"
    r"plc|pbc|p\.c\.|pc|holdings|group|the|a\s+/\s+s|gmbh|ag)\b\.?",
    re.IGNORECASE,
)
PUNCT = re.compile(r"[^\w\s]+")
WS = re.compile(r"\s+")


def normalize_name(s: object) -> str:
    if pd.isna(s) or not str(s).strip():
        return ""
    x = str(s).lower().strip()
    x = PUNCT.sub(" ", x)
    x = NAME_SUFFIX.sub(" ", x)
    x = WS.sub(" ", x).strip()
    return x


def load_form_d() -> pd.DataFrame:
    df = pd.read_parquet(RAW_DIR / "form_d_filings.parquet")
    out = (
        df.groupby(["_msa", "ENTITYNAME"], dropna=False)
          .agg(
              cik=("CIK", "first"),
              city=("CITY", "first"),
              state=("STATEORCOUNTRY", "first"),
              zip=("ZIPCODE", "first"),
              year_incorp=("YEAROFINC_VALUE_ENTERED", "first"),
              entity_type=("ENTITYTYPE", "first"),
              industry=("_industry_norm", "first"),
              form_d_filings=("ACCESSIONNUMBER", "nunique"),
          )
          .reset_index()
          .rename(columns={"_msa": "msa", "ENTITYNAME": "name"})
    )
    out["source_form_d"] = True
    return out


def load_sbir() -> pd.DataFrame:
    df = pd.read_parquet(RAW_DIR / "sbir_awards.parquet")
    # Convert award amount to numeric for sum
    if "Award Amount" in df.columns:
        df["_amt"] = pd.to_numeric(
            df["Award Amount"].astype(str).str.replace(r"[\$,]", "", regex=True),
            errors="coerce",
        ).fillna(0)
    else:
        df["_amt"] = 0
    out = (
        df.groupby(["_msa", "Company"], dropna=False)
          .agg(
              uei=("UEI", "first"),
              duns=("Duns", "first"),
              city=("City", "first"),
              state=("_state_norm", "first"),
              zip=("Zip", "first"),
              website=("Company Website", "first"),
              employees=("Number Employees", "first"),
              sbir_awards=("Contract", "nunique"),
              sbir_total_usd=("_amt", "sum"),
              sbir_first_year=("Award Year", "min"),
              sbir_last_year=("Award Year", "max"),
          )
          .reset_index()
          .rename(columns={"_msa": "msa", "Company": "name"})
    )
    out["source_sbir"] = True
    return out


def load_nih_orgs() -> pd.DataFrame:
    df = pd.read_parquet(RAW_DIR / "nih_awards.parquet")
    # NIH RePORTER project rows have organization sub-fields after json_normalize
    org_name_col = next(
        (c for c in df.columns
         if c.lower() in ("organization.org_name", "organization_org_name", "org_name")),
        None,
    )
    org_city_col = next(
        (c for c in df.columns
         if c.lower() in ("organization.org_city", "organization_org_city", "org_city")),
        None,
    )
    org_state_col = next(
        (c for c in df.columns
         if c.lower() in ("organization.org_state", "organization_org_state", "org_state")),
        None,
    )
    org_zip_col = next(
        (c for c in df.columns
         if c.lower() in ("organization.org_zipcode", "organization_org_zipcode", "org_zipcode")),
        None,
    )
    award_col = next(
        (c for c in df.columns if c.lower() in ("award_amount", "total_cost")), None,
    )

    if not org_name_col:
        print(f"WARN: cannot find NIH org name column. Cols sample: {list(df.columns)[:30]}",
              file=sys.stderr)
        return pd.DataFrame()

    df["_amt"] = pd.to_numeric(df[award_col], errors="coerce").fillna(0) if award_col else 0
    out = (
        df.groupby(["_msa", org_name_col], dropna=False)
          .agg(
              city=(org_city_col, "first") if org_city_col else (org_name_col, "first"),
              state=(org_state_col, "first") if org_state_col else (org_name_col, "first"),
              zip=(org_zip_col, "first") if org_zip_col else (org_name_col, "first"),
              nih_grants=(org_name_col, "size"),
              nih_total_usd=("_amt", "sum"),
          )
          .reset_index()
          .rename(columns={"_msa": "msa", org_name_col: "name"})
    )
    if not org_city_col:
        out["city"] = ""
    if not org_state_col:
        out["state"] = ""
    if not org_zip_col:
        out["zip"] = ""
    out["source_nih"] = True
    return out


def merge_rosters(*dfs: pd.DataFrame) -> pd.DataFrame:
    """Combine on (msa, name_norm). Last-write wins for non-bool cols; bools OR'd."""
    all_rows: list[pd.DataFrame] = []
    for df in dfs:
        if df is None or df.empty:
            continue
        df = df.copy()
        df["name_norm"] = df["name"].map(normalize_name)
        df = df[df["name_norm"] != ""]
        all_rows.append(df)
    if not all_rows:
        return pd.DataFrame()
    cat = pd.concat(all_rows, ignore_index=True)

    # Aggregate per (msa, name_norm)
    agg_funcs: dict = {}
    for col in cat.columns:
        if col in ("msa", "name_norm"):
            continue
        if col.startswith("source_"):
            agg_funcs[col] = lambda s: bool(s.fillna(False).any())
        elif col in ("form_d_filings", "sbir_awards", "nih_grants",
                     "sbir_total_usd", "nih_total_usd"):
            agg_funcs[col] = "sum"
        else:
            agg_funcs[col] = "first"
    merged = cat.groupby(["msa", "name_norm"], as_index=False).agg(agg_funcs)
    return merged


def main() -> None:
    fd = load_form_d()
    print(f"Form D rows : {len(fd):,}")
    sbir = load_sbir()
    print(f"SBIR rows   : {len(sbir):,}")
    nih = load_nih_orgs()
    print(f"NIH org rows: {len(nih):,}")

    merged = merge_rosters(fd, sbir, nih)
    print(f"\nMerged unique companies: {len(merged):,}")

    # Fill source flag NaN with False
    for c in [c for c in merged.columns if c.startswith("source_")]:
        merged[c] = merged[c].fillna(False).astype(bool)

    # Tier
    def tier(r):
        if r.get("source_form_d") and (r.get("source_sbir") or r.get("source_nih")):
            return "confirmed_strong"
        if r.get("source_form_d"):
            return "confirmed_funded"
        if r.get("source_sbir"):
            return "confirmed_grant"
        return "research_org"  # NIH-only — mostly universities/hospitals
    merged["tier"] = merged.apply(tier, axis=1)

    # Order columns sensibly
    front = ["msa", "name", "name_norm", "tier", "city", "state", "zip",
             "source_form_d", "source_sbir", "source_nih",
             "form_d_filings", "sbir_awards", "sbir_total_usd",
             "nih_grants", "nih_total_usd",
             "year_incorp", "entity_type", "industry", "cik", "uei", "duns",
             "website", "employees", "sbir_first_year", "sbir_last_year"]
    cols = [c for c in front if c in merged.columns] + \
           [c for c in merged.columns if c not in front]
    merged = merged[cols].sort_values(["msa", "tier", "name"])

    # Outputs
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    main_csv = OUTPUT_DIR / "companies_interim.csv"
    merged.to_csv(main_csv, index=False)
    try:
        merged.to_excel(OUTPUT_DIR / "companies_interim.xlsx", index=False, engine="openpyxl")
    except Exception as e:
        print(f"WARN xlsx failed: {e}", file=sys.stderr)

    # Per-MSA splits
    by_msa_dir = OUTPUT_DIR / "companies_interim_by_msa"
    by_msa_dir.mkdir(exist_ok=True)
    for msa, sub in merged.groupby("msa"):
        sub.to_csv(by_msa_dir / f"{msa}.csv", index=False)

    # Counts table
    counts = (
        merged.groupby("msa")
        .agg(
            total=("name", "size"),
            funded_form_d=("source_form_d", "sum"),
            sbir_recipients=("source_sbir", "sum"),
            nih_recipients=("source_nih", "sum"),
            confirmed_strong=("tier", lambda s: (s == "confirmed_strong").sum()),
        )
        .reset_index()
        .sort_values("total", ascending=False)
    )
    counts.to_csv(OUTPUT_DIR / "companies_interim_counts.csv", index=False)

    print("\n=== Per-MSA counts ===")
    print(counts.to_string(index=False))
    print(f"\nWrote: {main_csv}")
    print(f"Wrote: {OUTPUT_DIR / 'companies_interim_counts.csv'}")
    print(f"Wrote: {by_msa_dir}/{{msa}}.csv")


if __name__ == "__main__":
    main()
