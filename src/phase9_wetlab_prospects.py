"""Phase 9: Filter the startup roster down to wet-lab tenant prospects.

Input:  output/companies_final_startups_only.csv   (4,001 rows from Phase 8
                                                    after the 2015+ backfill;
                                                    was 3,563 under 2020+ floor)
Output:
  output/wet_lab_prospects.csv                 full filtered list
  output/wet_lab_demand_analysis.xlsx          4-sheet Excel deliverable
  output/phase9_audit_log.csv                  funnel row-by-row log
  output/dropped_geography.csv                 Step 3 drops
  output/dropped_public_companies.csv          Step 8 drops

Funnel (most recent run, 2015+ recency floor):
    Step 1  re-dedup           4,001 →  3,956   (-45)
    Step 2  fuzzy merge        3,956 →  3,948   (-8)
    Step 3  geography cleanup  3,948 →  3,903   (-45)
    Step 4  wet-lab subcat     3,903 →  1,771   (-2,132)  largest single drop
    Step 5  recency >=2015     1,771 →  1,434   (-337)
    Step 6  mature contractor  1,434 →  1,425   (-9)
    Step 7  SPV / fund vehicle 1,425 →  1,367   (-58)
    Step 8  public companies   1,367 →  1,301   (-66)
    Step 9  non-wet-lab excl.  1,301 →  1,290   (-11)
    -------------------------------------------------
    Final wet-lab prospects:                    1,290

Per MSA (final 1,290):
    philadelphia    427
    dallas          359
    baltimore       197
    atlanta         182
    pittsburgh      125

Tier composition:
    operating_company (Form D-funded):  1,008
    grant_only_company (SBIR):            255
    tto_spinout (university/incubator):    27

Run:
  python src/phase9_wetlab_prospects.py            (skip if manifest exists)
  python src/phase9_wetlab_prospects.py --force    (always re-run)
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    CONFIG_DIR, LOG_DIR, OUTPUT_DIR,
    http_get, manifest_exists, write_manifest,
)

PHASE = 9
INPUT_CSV = OUTPUT_DIR / "companies_final_startups_only.csv"
AUDIT_LOG = OUTPUT_DIR / "phase9_audit_log.csv"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "phase9.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── normalisation (same rules as Phase 7a) ────────────────────────────────────

_SUFFIX_RE = re.compile(
    r"\b(inc|incorporated|llc|l\.l\.c\.|ltd|limited|corp|corporation|co|company|"
    r"plc|pbc|p\.c\.|pc|holdings|group|the|gmbh|ag|lp|llp|lllp|pllc|sa|nv|bv)\b\.?",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^\w\s]+")
_WS_RE    = re.compile(r"\s+")


def _norm(s: object) -> str:
    if pd.isna(s) or not str(s).strip():
        return ""
    x = str(s).lower().strip()
    x = _PUNCT_RE.sub(" ", x)
    x = _SUFFIX_RE.sub(" ", x)
    x = _WS_RE.sub(" ", x).strip()
    return x


# ── audit log helpers ──────────────────────────────────────────────────────────

_audit_rows: list[dict] = []


def _audit(step: str, rows_in: int, rows_out: int, reason: str) -> None:
    removed = rows_in - rows_out
    rec = dict(step_name=step, rows_in=rows_in, rows_out=rows_out,
               rows_removed=removed, reason=reason)
    _audit_rows.append(rec)
    log.info("%-42s  in=%5d  out=%5d  removed=%5d  (%s)",
             step, rows_in, rows_out, removed, reason)


def _write_audit() -> None:
    with AUDIT_LOG.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["step_name", "rows_in", "rows_out",
                                          "rows_removed", "reason"])
        w.writeheader()
        w.writerows(_audit_rows)
    log.info("Wrote audit log -> %s", AUDIT_LOG)


# ── Step 1: union-find re-dedup ────────────────────────────────────────────────

class _UF:
    def __init__(self) -> None:
        self._p: dict[int, int] = {}

    def find(self, x: int) -> int:
        # Walk to root
        root = x
        while self._p.get(root, root) != root:
            root = self._p[root]
        # Path compression
        while self._p.get(x, x) != root:
            nxt = self._p.get(x, x)
            self._p[x] = root
            x = nxt
        return root

    def union(self, a: int, b: int) -> None:
        a, b = self.find(a), self.find(b)
        if a != b:
            self._p[b] = a


def _step1_dedup(df: pd.DataFrame) -> pd.DataFrame:
    """Union-find merge on (msa, norm_name), then CIK, UEI, DUNS."""
    df = df.copy()
    df["_norm"] = df["name"].map(_norm)

    uf = _UF()
    idx = list(df.index)

    # (a) msa + normalised name
    key_to_idx: dict[tuple, int] = {}
    for i in idx:
        k = (df.at[i, "msa"], df.at[i, "_norm"])
        if k[1] == "":
            continue
        if k in key_to_idx:
            uf.union(key_to_idx[k], i)
        else:
            key_to_idx[k] = i

    # (b-d) identifier columns
    for col in ("cik", "uei", "duns"):
        if col not in df.columns:
            continue
        id_to_idx: dict[Any, int] = {}
        for i in idx:
            v = df.at[i, col]
            if pd.isna(v) or str(v).strip() in ("", "nan", "0"):
                continue
            v = str(v).strip()
            if v in id_to_idx:
                uf.union(id_to_idx[v], i)
            else:
                id_to_idx[v] = i

    # Assign cluster roots
    df["_cluster"] = [uf.find(i) for i in idx]

    bool_cols  = [c for c in df.columns if c.startswith("source_")]
    sum_cols   = [c for c in ("form_d_filings", "sbir_awards", "sbir_total_usd",
                               "nih_grants", "nih_total_usd") if c in df.columns]
    first_cols = [c for c in df.columns if c not in bool_cols + sum_cols +
                  ["_cluster", "_norm", "msa"]]

    agg: dict[str, Any] = {"msa": "first"}
    for c in bool_cols:
        agg[c] = lambda s: bool(s.fillna(False).any())
    for c in sum_cols:
        agg[c] = "sum"
    for c in first_cols:
        agg[c] = "first"

    merged = df.groupby("_cluster", sort=False).agg(agg).reset_index(drop=True)
    merged.drop(columns=["_norm"], errors="ignore", inplace=True)
    return merged


# ── Step 2: fuzzy near-duplicate merge ────────────────────────────────────────

_GENERIC_TOKENS = frozenset(
    "medical health pharma bio therapeutics labs sciences technologies "
    "group holdings therapy systems solutions partners services care".split()
)


def _first_non_generic(name: str) -> str:
    tokens = _norm(name).split()
    for t in tokens:
        if t and t not in _GENERIC_TOKENS:
            return t
    return tokens[0] if tokens else ""


def _step2_fuzzy(df: pd.DataFrame) -> pd.DataFrame:
    try:
        import jellyfish
    except ImportError:
        log.warning("jellyfish not installed — skipping fuzzy merge (Step 2)")
        return df

    df = df.copy().reset_index(drop=True)
    df["_norm2"] = df["name"].map(_norm)
    df["_block"] = df.apply(
        lambda r: (r["msa"], _first_non_generic(r["name"])), axis=1
    )

    uf = _UF()

    blocks: dict[tuple, list[int]] = {}
    for i, b in df["_block"].items():
        blocks.setdefault(b, []).append(i)

    for members in blocks.values():
        if len(members) < 2:
            continue
        for a_pos in range(len(members)):
            for b_pos in range(a_pos + 1, len(members)):
                i, j = members[a_pos], members[b_pos]
                na, nb = df.at[i, "_norm2"], df.at[j, "_norm2"]
                if not na or not nb:
                    continue
                # veto if both have conflicting hard IDs
                for col in ("cik", "uei", "duns"):
                    if col not in df.columns:
                        continue
                    va, vb = df.at[i, col], df.at[j, col]
                    both = (
                        not pd.isna(va) and str(va).strip() not in ("", "nan", "0") and
                        not pd.isna(vb) and str(vb).strip() not in ("", "nan", "0")
                    )
                    if both and str(va).strip() != str(vb).strip():
                        break
                else:
                    tok_count = max(len(na.split()), len(nb.split()))
                    threshold = 0.95 if tok_count <= 2 else 0.92
                    sim = jellyfish.jaro_winkler_similarity(na, nb)
                    if sim >= threshold:
                        uf.union(i, j)

    df["_cluster2"] = [uf.find(i) for i in df.index]

    bool_cols = [c for c in df.columns if c.startswith("source_")]
    sum_cols  = [c for c in ("form_d_filings", "sbir_awards", "sbir_total_usd",
                              "nih_grants", "nih_total_usd") if c in df.columns]
    first_cols = [c for c in df.columns if c not in bool_cols + sum_cols +
                  ["_cluster2", "_norm2", "_block", "msa"]]

    agg: dict[str, Any] = {"msa": "first"}
    for c in bool_cols:
        agg[c] = lambda s: bool(s.fillna(False).any())
    for c in sum_cols:
        agg[c] = "sum"
    for c in first_cols:
        agg[c] = "first"

    merged = df.groupby("_cluster2", sort=False).agg(agg).reset_index(drop=True)
    merged.drop(columns=["_norm2", "_block"], errors="ignore", inplace=True)
    return merged


# ── Step 3: geography cleanup for TTO rows ────────────────────────────────────

# City / state fragments that clearly place a firm outside its assigned MSA.
# Keys are the 5 MSA slugs; values are fragments that indicate OUT-OF-MSA.
_MSA_ANTI_KEYWORDS: dict[str, list[str]] = {
    "atlanta": [
        "san francisco", "sf, ca", "austin", "boston", "new york", "nyc",
        "seattle", "chicago", "los angeles", "l.a.", "denver", "portland",
        "miami", "charlotte", "raleigh", "durham", "nashville", "houston",
        "san diego", "minneapolis", "st. louis", "detroit", "phoenix",
        "salt lake", "las vegas", "oklahoma", "tallahassee", "fort lauderdale",
        "auburn, al", "norman, ok", "alameda",
    ],
    "philadelphia": [
        "san francisco", "austin", "boston", "new york", "nyc", "seattle",
        "chicago", "los angeles", "denver", "portland", "miami", "atlanta",
        "dallas", "pittsburgh", "baltimore", "houston", "san diego",
    ],
    "pittsburgh": [
        "san francisco", "austin", "boston", "new york", "nyc", "seattle",
        "chicago", "los angeles", "denver", "portland", "miami", "atlanta",
        "dallas", "philadelphia", "baltimore", "houston", "san diego",
    ],
    "baltimore": [
        "san francisco", "austin", "boston", "new york", "nyc", "seattle",
        "chicago", "los angeles", "denver", "portland", "miami", "atlanta",
        "dallas", "pittsburgh", "philadelphia", "houston", "san diego",
    ],
    "dallas": [
        "san francisco", "austin", "boston", "new york", "nyc", "seattle",
        "chicago", "los angeles", "denver", "portland", "miami", "atlanta",
        "pittsburgh", "philadelphia", "baltimore", "san diego", "houston",
    ],
}


def _step3_geo(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    tto_mask = df["source_tto"].fillna(False).astype(bool)
    loc_col_present = "tto_location" in df.columns

    if not loc_col_present or not tto_mask.any():
        return df, pd.DataFrame()

    def _bad_geo(row) -> bool:
        if not row.get("source_tto"):
            return False
        loc = str(row.get("tto_location", "") or "").lower().strip()
        if not loc or loc in ("nan", "none"):
            return False
        msa = str(row.get("msa", "")).lower()
        anti = _MSA_ANTI_KEYWORDS.get(msa, [])
        return any(kw in loc for kw in anti)

    bad_mask = df.apply(_bad_geo, axis=1)
    dropped = df[bad_mask].copy()
    dropped["drop_reason"] = "Step3_geo: tto_location outside assigned MSA"
    return df[~bad_mask].copy(), dropped


# ── Step 4: wet-lab subcategory filter ────────────────────────────────────────

_WETLAB_CATS = frozenset({"biotech", "pharma", "diagnostics", "chemistry", "medtech"})
_DROP_CATS   = frozenset({"digital_health", "services"})

_WETLAB_NAME_RE = re.compile(
    r"bio|genom|genet|therap|pharma|cell|rna|dna|protein|chem|catalys|polymer|"
    r"molecul|assay|diagnos|vaccine|immun|onco|neuro|antibody|peptide|enzyme|"
    r"fermen|culture|tissue|stem|crispr|gene|drug|formul|synthe|reagent|"
    r"crystal|nano|bioprocess|mab|adc",
    re.IGNORECASE,
)


def _step4_subcat(df: pd.DataFrame) -> pd.DataFrame:
    cat = df["ls_subcategory"].fillna("unknown")
    keep_cat     = cat.isin(_WETLAB_CATS)
    unknown_mask = cat == "unknown"
    name_match   = df["name"].str.contains(_WETLAB_NAME_RE, na=False)
    keep_unknown = unknown_mask & name_match
    drop_cat     = cat.isin(_DROP_CATS)
    return df[keep_cat | keep_unknown].copy()


# ── Step 5: recency filter ────────────────────────────────────────────────────

def _step5_recency(df: pd.DataFrame) -> pd.DataFrame:
    fd   = df["source_form_d"].fillna(False).astype(bool)
    tto  = df["source_tto"].fillna(False).astype(bool)
    sly  = pd.to_numeric(df.get("sbir_last_year", pd.Series(dtype=float)),
                         errors="coerce")
    sbir_recent = sly >= 2015
    return df[fd | tto | sbir_recent].copy()


# ── Step 6: mature govt contractor filter ─────────────────────────────────────

def _step6_stage(df: pd.DataFrame) -> pd.DataFrame:
    sly  = pd.to_numeric(df.get("sbir_last_year",  pd.Series(dtype=float)), errors="coerce")
    sfy  = pd.to_numeric(df.get("sbir_first_year", pd.Series(dtype=float)), errors="coerce")
    amt  = pd.to_numeric(df.get("sbir_total_usd",  pd.Series(dtype=float)), errors="coerce").fillna(0)
    span = (sly - sfy).fillna(0)
    mature = (span > 20) & (amt > 20_000_000)
    return df[~mature].copy()


# ── Step 7: SPV / fund vehicle removal ────────────────────────────────────────

_SPV_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bSeries\s*#?\s*\d+\b",                          re.IGNORECASE),
    re.compile(
        r"\bFund\s+(Alpha|Beta|Gamma|Delta|Epsilon|Zeta|Eta|Theta|Iota|Kappa|"
        r"Lambda|Lamda|Mu|Mi|Nu|Ni|Xi|Ksi|Omicron|Pi|Rho|Sigma|Tau|Upsilon|"
        r"Phi|Chi|Psi|Omega)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bFund\s+(I{1,3}|IV|VI{0,3}|IX|XI{0,3}|XIV|XV)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bQP\s+Investors\b",          re.IGNORECASE),
    re.compile(r"\bInvestors\s+LLC\b",         re.IGNORECASE),
    re.compile(r"\bAid\s+Fund\b",              re.IGNORECASE),
    re.compile(r"\bSPV\b",                     re.IGNORECASE),
    re.compile(r"\bMaster\s+Fund\b",           re.IGNORECASE),
    re.compile(r"\bFeeder\s+Fund\b",           re.IGNORECASE),
    re.compile(r"\bRE\s+Holdings\b",           re.IGNORECASE),
    re.compile(r"\bAcquisition\s+Corp\b",      re.IGNORECASE),
    re.compile(r"\bAcquisitions?,?\s*LLC$",    re.IGNORECASE),
    re.compile(r"\bDistressed\s+Fund\b",       re.IGNORECASE),
    re.compile(r"\bAlternative\s+Finance\b",   re.IGNORECASE),
    re.compile(r"\bInvestment\s+Holdings\b",   re.IGNORECASE),
    re.compile(r"\bHoldco\s+Management\b",     re.IGNORECASE),
]


def _load_pe_exclusions() -> set[str]:
    p = CONFIG_DIR / "pe_rollup_exclusions.json"
    if not p.exists():
        return set()
    data = json.loads(p.read_text(encoding="utf-8"))
    return {_norm(n) for n in data.get("excluded_names", [])}


def _step7_spv(df: pd.DataFrame, pe_norms: set[str]) -> pd.DataFrame:
    def _is_vehicle(name: str) -> bool:
        for pat in _SPV_PATTERNS:
            if pat.search(name):
                return True
        return _norm(name) in pe_norms

    mask = df["name"].map(_is_vehicle)
    return df[~mask].copy()


# ── Step 8: public-company removal ────────────────────────────────────────────

def _fetch_public_ciks() -> dict[str, str]:
    """Return {str(cik): ticker} for all SEC-listed public companies."""
    url = "https://www.sec.gov/files/company_tickers.json"
    try:
        r = http_get(url, source="SEC_tickers")
        data = r.json()
        return {str(v["cik_str"]): v["ticker"] for v in data.values()}
    except Exception as exc:
        log.warning("Could not fetch SEC ticker list (%s); skipping Step 8", exc)
        return {}


def _step8_public(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    public_ciks = _fetch_public_ciks()
    if not public_ciks:
        return df, pd.DataFrame()

    def _lookup(row) -> str | None:
        cik = row.get("cik")
        if pd.isna(cik):
            return None
        return public_ciks.get(str(int(cik)))

    df = df.copy()
    df["_ticker"] = df.apply(_lookup, axis=1)
    is_public = df["_ticker"].notna()
    dropped = df[is_public].copy()
    dropped["drop_reason"] = "Step8_public: listed on SEC exchange"
    dropped = dropped.rename(columns={"_ticker": "ticker"})
    kept = df[~is_public].drop(columns=["_ticker"])
    return kept, dropped


# ── Step 9: manual non-wet-lab exclusions ─────────────────────────────────────

def _load_nonwetlab_exclusions() -> set[str]:
    p = CONFIG_DIR / "non_wetlab_exclusions.json"
    if not p.exists():
        return set()
    data = json.loads(p.read_text(encoding="utf-8"))
    return {_norm(n) for n in data.get("excluded_names", [])}


def _step9_nonwetlab(df: pd.DataFrame, excl_norms: set[str]) -> pd.DataFrame:
    mask = df["name"].map(lambda n: _norm(n) in excl_norms)
    return df[~mask].copy()


# ── Step 10: priority score ────────────────────────────────────────────────────

_HIGH_SUBCAT = frozenset({"biotech", "pharma", "diagnostics", "chemistry"})


def _step10_score(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    score = pd.Series(0, index=df.index)

    score += df["source_form_d"].fillna(False).astype(bool) * 3

    sly = pd.to_numeric(df.get("sbir_last_year", pd.Series(dtype=float)), errors="coerce")
    score += (sly >= 2024).fillna(False) * 3
    score += ((sly >= 2022) & (sly <= 2023)).fillna(False) * 2
    score += ((sly >= 2020) & (sly <= 2021)).fillna(False) * 1
    # 2015-2019 passes the recency gate but earns 0 here (older = lower priority)

    score += df["source_tto"].fillna(False).astype(bool) * 2

    score += df["ls_subcategory"].isin(_HIGH_SUBCAT) * 2

    yi = pd.to_numeric(df.get("year_incorp", pd.Series(dtype=float)), errors="coerce")
    score += (yi >= 2015).fillna(False) * 1

    df["priority_score"] = score.astype(int)
    return df


# ── Step 11: founded_year column ──────────────────────────────────────────────

def _step11_founded(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    yi = pd.to_numeric(df.get("year_incorp", pd.Series(dtype=float)), errors="coerce")
    # Use SEC year_incorp only; blanks if out of plausible range
    df["founded_year"] = yi.where((yi >= 1900) & (yi <= 2030))
    return df


# ── Excel output ──────────────────────────────────────────────────────────────

_FOUNDED_BUCKETS = ["2015-2019", "2020", "2021", "2022", "2023", "2024",
                    "2025-2026", "Unknown"]


def _bucket_year(y) -> str:
    if pd.isna(y):
        return "Unknown"
    y = int(y)
    if y <= 2019:
        return "2015-2019"
    if y >= 2025:
        return "2025-2026"
    return str(y)


def _write_excel(prospects: pd.DataFrame, path: Path) -> None:
    msas = sorted(prospects["msa"].unique())

    # ── Summary sheet ──────────────────────────────────────────────────────

    # Funnel table (from audit log)
    funnel_df = pd.DataFrame(_audit_rows)

    # Per-MSA counts
    msa_counts = prospects.groupby("msa").size().reset_index(name="count")
    msa_counts.columns = ["MSA", "Prospects"]

    # Subcategory × MSA pivot
    subcat_pivot = (
        prospects.groupby(["msa", "ls_subcategory"])
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )

    # Founded year × MSA pivot
    prospects_copy = prospects.copy()
    prospects_copy["_yr_bucket"] = prospects_copy["founded_year"].map(_bucket_year)
    yr_pivot = (
        prospects_copy.groupby(["msa", "_yr_bucket"])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=_FOUNDED_BUCKETS, fill_value=0)
        .reset_index()
    )

    methodology_rows = [
        ["Phase 9: Wet-lab Prospect Filtering — Methodology"],
        [],
        ["Step", "Description", "Reason"],
        ["Step 1 — Re-dedup",
         "Union-find merge on (msa, norm_name), SEC CIK, UEI, DUNS.",
         "Phase 7a dedup was exact-name only; cross-source ID links can still leave duplicates."],
        ["Step 2 — Fuzzy merge",
         "Jaro-Winkler ≥ 0.95 (≤2 tokens) or ≥ 0.92 (longer), blocked by (msa, first non-generic token). Vetoed if hard IDs conflict.",
         "Catches pluralization errors and minor OCR differences in names."],
        ["Step 3 — Geography cleanup",
         "TTO-flagged rows with tto_location clearly outside the assigned MSA are dropped.",
         "Engage Ventures (Atlanta) and similar portfolios contain companies nationally; "
         "we only want those physically in the target MSA."],
        ["Step 4 — Wet-lab subcategory",
         "Keep biotech/pharma/diagnostics/chemistry/medtech. Keep 'unknown' only if name matches wet-lab keyword regex. Drop digital_health and services.",
         "Filters to companies that would need lab bench space, not office/SaaS tenants."],
        ["Step 5 — Recency",
         "Keep if Form D filed (2015+ scoped), SBIR last year >= 2015, or TTO-listed.",
         "Removes dormant/dissolved firms unlikely to be active tenants."],
        ["Step 6 — Stage filter",
         "Drop if SBIR span > 20 yr AND total SBIR > $20M.",
         "Large long-running govt contractors have their own facilities."],
        ["Step 7 — SPV/fund vehicles",
         "Drop names matching paper-vehicle regex patterns and PE rollup exclusion list (config/pe_rollup_exclusions.json).",
         "Form D filings include fund formation vehicles that are not operating companies."],
        ["Step 8 — Public companies",
         "Drop if SEC CIK appears in the public-company ticker file.",
         "Publicly traded companies manage their own real estate; not a target market."],
        ["Step 9 — Non-wet-lab exclusions",
         "Drop names in config/non_wetlab_exclusions.json.",
         "Defense/IT/robotics firms incorrectly tagged as life-sci by the keyword classifier."],
        ["Step 10 — Priority score",
         "+3 Form D, +3/2/1 SBIR recency (2024+/2022-23/2020-21; 2015-19 passes the gate but scores 0), +2 TTO, +2 high subcat, +1 founded>=2015.",
         "Helps prioritise outreach order within each MSA."],
        ["Step 11 — founded_year",
         "SEC year_incorp only; blank if outside [1900, 2030].",
         "No proxy sources used; SBIR self-reported dates are unreliable for founding year."],
        [],
        ["Known Limitations"],
        ["1. Unknown subcategory (~75% of SBIR-only rows) is filtered by name keyword, "
         "not abstract text — some wet-lab firms will be missed if their name is generic."],
        ["2. Fuzzy merge (Step 2) uses Jaro-Winkler, not semantic similarity — "
         "subsidiaries with different names will not be merged."],
        ["3. Geography check (Step 3) only fires on rows with a non-null tto_location. "
         "Form D / SBIR rows use city/state from federal filings which may lag relocations."],
        ["4. Public-company check requires a live SEC fetch; if offline, Step 8 is skipped."],
        ["5. PE rollup and non-wet-lab exclusion lists (config/*.json) must be manually "
         "maintained as new entities appear."],
    ]

    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        # Sheet 1 — Summary
        row_offset = 0
        funnel_df.to_excel(xl, sheet_name="Summary", index=False, startrow=row_offset)
        row_offset += len(funnel_df) + 3

        pd.DataFrame([["Per-MSA prospect counts"]]).to_excel(
            xl, sheet_name="Summary", index=False, header=False, startrow=row_offset)
        row_offset += 1
        msa_counts.to_excel(xl, sheet_name="Summary", index=False, startrow=row_offset)
        row_offset += len(msa_counts) + 3

        pd.DataFrame([["Subcategory × MSA"]]).to_excel(
            xl, sheet_name="Summary", index=False, header=False, startrow=row_offset)
        row_offset += 1
        subcat_pivot.to_excel(xl, sheet_name="Summary", index=False, startrow=row_offset)
        row_offset += len(subcat_pivot) + 3

        pd.DataFrame([["Founded year bucket × MSA"]]).to_excel(
            xl, sheet_name="Summary", index=False, header=False, startrow=row_offset)
        row_offset += 1
        yr_pivot.to_excel(xl, sheet_name="Summary", index=False, startrow=row_offset)

        # Sheet 2 — Top Prospects (top 25 per MSA)
        tops = (
            prospects.sort_values("priority_score", ascending=False)
            .groupby("msa", group_keys=False)
            .head(25)
            .sort_values(["msa", "priority_score"], ascending=[True, False])
        )
        tops.to_excel(xl, sheet_name="Top Prospects", index=False)

        # Sheet 3 — All Prospects with auto-filter + frozen header
        prospects_sorted = prospects.sort_values(
            ["msa", "priority_score"], ascending=[True, False]
        )
        sheet_title = f"All Prospects ({len(prospects_sorted)})"
        prospects_sorted.to_excel(xl, sheet_name=sheet_title, index=False)
        ws = xl.sheets[sheet_title]
        ws.freeze_panes = ws["A2"]
        ws.auto_filter.ref = ws.dimensions

        # Sheet 4 — Methodology
        meth_df = pd.DataFrame(methodology_rows)
        meth_df.to_excel(xl, sheet_name="Methodology", index=False, header=False)

    log.info("Wrote Excel -> %s", path)


# ── main ──────────────────────────────────────────────────────────────────────

def main(force: bool = False) -> None:
    if not force and manifest_exists(PHASE):
        log.info("Phase 9 already complete (manifest exists). Use --force to re-run.")
        return

    if not INPUT_CSV.exists():
        sys.exit(f"ERROR: input file not found: {INPUT_CSV}")

    log.info("Phase 9 — Wet-lab prospect filtering")
    log.info("Loading %s …", INPUT_CSV)
    df = pd.read_csv(INPUT_CSV, low_memory=False)
    log.info("Input: %d rows", len(df))

    # Ensure boolean source columns are actually bool
    for col in [c for c in df.columns if c.startswith("source_")]:
        df[col] = df[col].fillna(False).astype(bool)

    # ── Step 1
    n_before = len(df)
    df = _step1_dedup(df)
    _audit("Step1_rededup", n_before, len(df), "union-find on norm_name + CIK/UEI/DUNS")

    # ── Step 2
    n_before = len(df)
    df = _step2_fuzzy(df)
    _audit("Step2_fuzzy_merge", n_before, len(df),
           "Jaro-Winkler >=0.95/0.92 within (msa, first-non-generic-token) block")

    # ── Step 3
    n_before = len(df)
    df, dropped_geo = _step3_geo(df)
    _audit("Step3_geo_cleanup", n_before, len(df),
           "TTO rows where tto_location is clearly outside assigned MSA")
    if not dropped_geo.empty:
        dropped_geo.to_csv(OUTPUT_DIR / "dropped_geography.csv", index=False)
        log.info("  Dropped geography -> output/dropped_geography.csv (%d rows)", len(dropped_geo))

    # ── Step 4
    n_before = len(df)
    df = _step4_subcat(df)
    _audit("Step4_wetlab_subcat", n_before, len(df),
           "keep biotech/pharma/dx/chem/medtech + unknown-but-name-matches; drop digital_health/services")

    # ── Step 5
    n_before = len(df)
    df = _step5_recency(df)
    _audit("Step5_recency", n_before, len(df),
           "keep Form-D / SBIR-last>=2015 / TTO; drop dormant firms")

    # ── Step 6
    n_before = len(df)
    df = _step6_stage(df)
    _audit("Step6_stage", n_before, len(df),
           "drop SBIR span>20yr AND total>$20M (mature govt contractors)")

    # ── Step 7
    pe_norms = _load_pe_exclusions()
    n_before = len(df)
    df = _step7_spv(df, pe_norms)
    _audit("Step7_spv_vehicles", n_before, len(df),
           "regex SPV/fund patterns + config/pe_rollup_exclusions.json")

    # ── Step 8
    n_before = len(df)
    df, dropped_public = _step8_public(df)
    _audit("Step8_public_cos", n_before, len(df),
           "SEC public-company ticker list by CIK")
    if not dropped_public.empty:
        dropped_public.to_csv(OUTPUT_DIR / "dropped_public_companies.csv", index=False)
        log.info("  Dropped public cos -> output/dropped_public_companies.csv (%d rows)",
                 len(dropped_public))

    # ── Step 9
    excl_norms = _load_nonwetlab_exclusions()
    n_before = len(df)
    df = _step9_nonwetlab(df, excl_norms)
    _audit("Step9_nonwetlab_excl", n_before, len(df),
           "config/non_wetlab_exclusions.json (defense/IT/robotics firms)")

    # ── Step 10 + 11
    df = _step10_score(df)
    df = _step11_founded(df)

    log.info("Final wet-lab prospect count: %d", len(df))

    # ── Outputs
    out_csv = OUTPUT_DIR / "wet_lab_prospects.csv"
    df.sort_values(["msa", "priority_score"], ascending=[True, False]).to_csv(
        out_csv, index=False
    )
    log.info("Wrote prospects -> %s", out_csv)

    _write_audit()

    excel_path = OUTPUT_DIR / "wet_lab_demand_analysis.xlsx"
    _write_excel(df, excel_path)

    write_manifest(PHASE, {
        "input_rows": sum(a["rows_in"] for a in _audit_rows[:1]),
        "output_rows": len(df),
        "steps": len(_audit_rows),
    })
    log.info("Phase 9 complete. Manifest written.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 9 — wet-lab prospect filter")
    parser.add_argument("--force", action="store_true",
                        help="Re-run even if manifest exists")
    args = parser.parse_args()
    main(force=args.force)
