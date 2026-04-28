"""Phase 8: classify each entity in the merged roster.

Two labels added:
  - entity_type:  university | hospital | govt_lab | research_inst | nonprofit | startup
  - ls_subcategory: pharma | biotech | medtech | diagnostics | chemistry |
                    digital_health | services | unknown

Rules are keyword-based on (name, tagline, industry, entity_type field from Form D, source mix).
After classification:
  - tier becomes:
      "operating_company"  (Form D + funding signals, not univ/hospital)
      "grant_only_company" (SBIR-only, not univ/hospital)
      "research_inst"      (universities, hospitals, govt labs)
      "tto_spinout"        (only seen via Phase 4 TTO scrape, not yet in federal records)

Inputs:
  data/raw/form_d_filings.parquet
  data/raw/sbir_awards.parquet
  data/raw/nih_awards.parquet
  data/raw/tto_portfolio.parquet (optional)

Outputs:
  output/companies_final.csv / .xlsx
  output/companies_final_counts.csv
  output/companies_final_by_msa/{msa}.csv
  output/companies_final_startups_only.csv  ← the list to show the CEO
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUTPUT_DIR, RAW_DIR
from phase7a_interim_roster import (
    load_form_d, load_sbir, load_nih_orgs, merge_rosters, normalize_name,
)

# -------------------- classifiers --------------------

UNIVERSITY_RE = re.compile(
    r"\b(university|universities|college|colleges|institute of technology|"
    r"polytechnic|school of medicine|school of public health|school of nursing|"
    r"medical school|graduate school|seminary)\b",
    re.IGNORECASE,
)
HOSPITAL_RE = re.compile(
    r"\b(hospital|hospitals|medical center|health system|clinic|health network|"
    r"healthcare system|childrens|cancer center|heart institute|eye institute|"
    r"orthopaed?ic institute|rehabilitation)\b",
    re.IGNORECASE,
)
GOVT_RE = re.compile(
    r"\b(department of (?:defense|energy|agriculture|veterans)|naval|army|air force|"
    r"national institutes? of|national laboratory|national lab|federal|"
    r"u\.?s\.? department|usda|noaa|nasa|cdc|fda|epa|veterans affairs|"
    r"public health (?:dept|department))\b",
    re.IGNORECASE,
)
NONPROFIT_RE = re.compile(
    r"\b(foundation|trust|coalition|society|association|consortium|institute for|"
    r"research institute|chari?table|nonprofit)\b",
    re.IGNORECASE,
)

PHARMA_KW = ["pharma", "therapeutic", "biopharm", "drug", "rx", "medicine"]
BIOTECH_KW = ["bio ", "bioscience", "biotech", "biolog", "biophys", "bioengineer",
              "microbi", "protein", "enzyme",
              "genomics", "genetic", "cell ", "cellular",
              "gene ", "rna", "dna", "crispr", "vaccine", "immuno", "oncology", "cancer",
              "neuro", "stem cell", "antibod", "peptide"]
MEDTECH_KW = ["medical device", "medtech", "biomed",
              "robotics", "imaging", "surgical", "implant",
              "wearable", "monitor", "sensor",
              "orthopedic", "orthopaedic", "dental",
              "cardio", "cardiac", "catheter", "stent", "prosthe", "infusion"]
DIAGNOSTICS_KW = ["diagnost", "molecular dx", "screening", "biomarker", "assay",
                  "liquid biopsy", "pathology"]
CHEM_KW = ["chemic", "chemistry", "polymer", "material science", "specialty chem",
           "petrochem", "catalys", "coating", "fluorochem",
           "nanotech", "nanomater"]
DIGITAL_KW = ["software", "platform", "ai-powered", "ai for", "digital health",
              "telehealth", "telemedicine", "saas", "data platform", "machine learning",
              "ml ", "ehr"]
SERVICES_KW = ["consulting", "services", "cro ", "contract research", "cdmo",
               "manufacturing services", "supply chain"]


def classify_entity_type(row) -> str:
    """Decide university / hospital / govt / nonprofit / research_inst / startup."""
    name = str(row.get("name", "")) + " " + str(row.get("name_norm", ""))
    if UNIVERSITY_RE.search(name):
        return "university"
    if HOSPITAL_RE.search(name):
        return "hospital"
    if GOVT_RE.search(name):
        return "govt_lab"
    if NONPROFIT_RE.search(name) and not row.get("source_form_d"):
        return "nonprofit"
    # NIH-only with no Form D and no SBIR → likely research institution
    if row.get("source_nih") and not row.get("source_form_d") and not row.get("source_sbir"):
        # Could be a small consulting shop, but heuristically: NIH grants almost always
        # go to research orgs unless SBIR.
        return "research_inst"
    # Form D entity_type field
    et = str(row.get("entity_type") or "").lower()
    if "non-profit" in et or "nonprofit" in et:
        return "nonprofit"
    return "startup"


def classify_ls_subcategory(row) -> str:
    blob = (
        str(row.get("name", "")) + " " +
        str(row.get("industry", "")) + " " +
        str(row.get("tagline", ""))
    ).lower()

    def has(kws): return any(k in blob for k in kws)

    # Order matters — most specific first
    if has(CHEM_KW):       return "chemistry"
    if has(DIAGNOSTICS_KW): return "diagnostics"
    if has(MEDTECH_KW):    return "medtech"
    if has(PHARMA_KW):     return "pharma"
    if has(BIOTECH_KW):    return "biotech"
    if has(DIGITAL_KW):    return "digital_health"
    if has(SERVICES_KW):   return "services"
    # Default by Form D industry field
    ind = str(row.get("industry") or "").lower()
    if "pharm" in ind:     return "pharma"
    if "biotech" in ind:   return "biotech"
    if "health" in ind:    return "medtech"
    return "unknown"


def assign_tier(row) -> str:
    et = row.get("entity_type")
    if et in ("university", "hospital", "govt_lab", "research_inst", "nonprofit"):
        return "research_inst"
    if row.get("source_form_d"):
        return "operating_company"
    if row.get("source_sbir"):
        return "grant_only_company"
    if row.get("source_tto"):
        return "tto_spinout"
    return "research_inst"


# -------------------- main --------------------

def load_tto() -> pd.DataFrame:
    p = RAW_DIR / "tto_portfolio.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    if df.empty:
        return df
    df = df.rename(columns={"location": "tto_location"})
    df["source_tto"] = True
    df["tto_source_name"] = df["source"]
    df = df.drop(columns=["source"])
    return df


def main() -> None:
    print("Loading sources ...")
    fd = load_form_d()
    sbir = load_sbir()
    nih = load_nih_orgs()
    tto = load_tto()
    print(f"  Form D rows : {len(fd):,}")
    print(f"  SBIR rows   : {len(sbir):,}")
    print(f"  NIH rows    : {len(nih):,}")
    print(f"  TTO rows    : {len(tto):,}")

    merged = merge_rosters(fd, sbir, nih, tto)
    print(f"\nMerged unique entities: {len(merged):,}")

    for c in [c for c in merged.columns if c.startswith("source_")]:
        merged[c] = merged[c].fillna(False).astype(bool)

    # Classify
    merged["entity_type"]    = merged.apply(classify_entity_type, axis=1)
    merged["ls_subcategory"] = merged.apply(classify_ls_subcategory, axis=1)
    merged["tier"]           = merged.apply(assign_tier, axis=1)

    # Order columns
    front = ["msa", "name", "name_norm", "tier", "entity_type", "ls_subcategory",
             "city", "state", "zip",
             "source_form_d", "source_sbir", "source_nih", "source_tto",
             "form_d_filings", "sbir_awards", "sbir_total_usd",
             "nih_grants", "nih_total_usd",
             "year_incorp", "industry", "tagline", "tto_source_name",
             "cik", "uei", "duns", "website", "employees",
             "sbir_first_year", "sbir_last_year"]
    cols = [c for c in front if c in merged.columns] + \
           [c for c in merged.columns if c not in front]
    merged = merged[cols].sort_values(["msa", "tier", "entity_type", "name"])

    # ---- WRITE FULL ROSTER ----
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    full_csv = OUTPUT_DIR / "companies_final.csv"
    merged.to_csv(full_csv, index=False)
    try:
        merged.to_excel(OUTPUT_DIR / "companies_final.xlsx", index=False, engine="openpyxl")
    except Exception as e:
        print(f"WARN xlsx: {e}", file=sys.stderr)

    # Per-MSA splits (full)
    by_msa_dir = OUTPUT_DIR / "companies_final_by_msa"
    by_msa_dir.mkdir(exist_ok=True)
    for msa, sub in merged.groupby("msa"):
        sub.to_csv(by_msa_dir / f"{msa}.csv", index=False)

    # ---- STARTUPS-ONLY VIEW (the list for the CEO) ----
    startups = merged[merged["tier"].isin(["operating_company", "grant_only_company", "tto_spinout"])].copy()
    startups_csv = OUTPUT_DIR / "companies_final_startups_only.csv"
    startups.to_csv(startups_csv, index=False)
    try:
        startups.to_excel(OUTPUT_DIR / "companies_final_startups_only.xlsx",
                          index=False, engine="openpyxl")
    except Exception:
        pass

    # ---- COUNTS ----
    overall = (
        merged.groupby(["msa", "tier"]).size().unstack(fill_value=0).reset_index()
    )
    overall["TOTAL"] = overall.drop(columns="msa").sum(axis=1)
    overall.to_csv(OUTPUT_DIR / "companies_final_counts.csv", index=False)

    sub_counts = (
        startups.groupby(["msa", "ls_subcategory"]).size().unstack(fill_value=0).reset_index()
    )
    sub_counts["TOTAL_STARTUPS"] = sub_counts.drop(columns="msa").sum(axis=1)
    sub_counts.to_csv(OUTPUT_DIR / "companies_final_startup_subcategory_counts.csv", index=False)

    # ---- PRINT ----
    print("\n=== Tier breakdown by MSA ===")
    print(overall.to_string(index=False))
    print(f"\nTotal entities classified: {len(merged):,}")
    print(f"Operating companies (Form D-funded startups): {(merged.tier == 'operating_company').sum():,}")
    print(f"Grant-only companies (SBIR startups)        : {(merged.tier == 'grant_only_company').sum():,}")
    print(f"TTO-only spinouts                           : {(merged.tier == 'tto_spinout').sum():,}")
    print(f"Research institutions (filtered out)        : {(merged.tier == 'research_inst').sum():,}")

    print(f"\n=== Startup-only count by LS subcategory & MSA ===")
    print(sub_counts.to_string(index=False))

    print(f"\nWrote: {full_csv}")
    print(f"Wrote: {startups_csv}  ← THE FINAL LIST FOR CEO")


if __name__ == "__main__":
    main()
