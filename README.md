# LS / Chem Ecosystem Analysis — 5 MSAs

A reproducible pipeline that builds an authoritative roster of life-sciences and
chemistry startups across five U.S. metropolitan statistical areas (MSAs):
**Philadelphia, Pittsburgh, Baltimore, Atlanta, Dallas–Fort Worth**.

All data comes from free, federal public-record sources. Cost: $0.

Final output: **3,563 LS/chem startups** (plus 191 research institutions
identified and filtered separately).

---

## Final report — files in this repo

The committed deliverables in [`output/`](output/):

| File | What it is |
|---|---|
| [`output/companies_final_startups_only.csv`](output/companies_final_startups_only.csv) | **The startup list — 3,563 rows, no universities/hospitals.** Show this to the CEO. |
| [`output/companies_final_startups_only.xlsx`](output/companies_final_startups_only.xlsx) | Same, as Excel |
| [`output/companies_final.csv`](output/companies_final.csv) / [`.xlsx`](output/companies_final.xlsx) | Full classified roster (3,754 rows incl. research institutions) |
| [`output/companies_final_counts.csv`](output/companies_final_counts.csv) | Tier × MSA breakdown (the headline table below) |
| [`output/companies_final_startup_subcategory_counts.csv`](output/companies_final_startup_subcategory_counts.csv) | Pharma / Biotech / MedTech / Diagnostics / Chemistry / Digital-Health splits per MSA |
| [`output/companies_final_by_msa/`](output/companies_final_by_msa) | One CSV per MSA — easy to skim |
| [`output/tto_portfolio.csv`](output/tto_portfolio.csv) | Phase 4 university TTO + incubator portfolio harvest (209 rows) |
| [`output/form_d_filings.csv`](output/form_d_filings.csv) | Raw SEC Form D filings layer (1,408 rows) |
| [`output/form_d_unique_companies.csv`](output/form_d_unique_companies.csv) | Form D deduped to unique companies (717) |

The bigger source-layer files (NIH 526 MB, SBIR 6 MB) are gitignored — the
pipeline regenerates them from public APIs.

---

## Headline numbers

| MSA | Operating (Form D-funded) | Grant-only (SBIR) | TTO spinout | Research inst. (filtered) | **Startups total** |
|---|---:|---:|---:|---:|---:|
| Philadelphia | 248 | 834 | 12 | 94 | **1,094** |
| Atlanta | 96 | 468 | 124 | 29 | **688** |
| Baltimore | 77 | 610 | 12 | 32 | **699** |
| Dallas–Fort Worth | 215 | 386 | 10 | 16 | **611** |
| Pittsburgh | 63 | 394 | 14 | 20 | **471** |
| **TOTAL** | **699** | **2,692** | **172** | **191** | **3,563** |

### Startup subcategory split

| MSA | Biotech | Chemistry | Diagnostics | Digital Health | MedTech | Pharma | Services | Unknown |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Philadelphia | 104 | 10 | 13 | 6 | 113 | 153 | 17 | 678 |
| Dallas | 74 | 1 | 2 | 4 | 136 | 38 | 10 | 346 |
| Baltimore | 59 | 0 | 14 | 4 | 40 | 41 | 8 | 533 |
| Atlanta | 57 | 3 | 4 | 60 | 68 | 37 | 7 | 452 |
| Pittsburgh | 32 | 3 | 5 | 6 | 54 | 26 | 6 | 339 |

The large `Unknown` bucket is SBIR-grant rows where the bulk CSV omits the
abstract — Phase 8b (planned) will use the award title to refine these.

---

## Geographic scope

MSAs are defined by **OMB Bulletin 23-01** — the federal MSA delineation, fixed
to a list of counties (5-digit FIPS) per CBSA.

| MSA | CBSA | Counties | States |
|---|---|---:|---|
| Philadelphia–Camden–Wilmington | 37980 | 11 | PA, NJ, DE, MD |
| Pittsburgh | 38300 | 7 | PA |
| Baltimore–Columbia–Towson | 12580 | 7 | MD |
| Atlanta–Sandy Springs–Alpharetta | 12060 | 29 | GA |
| Dallas–Fort Worth–Arlington | 19100 | 11 | TX |

Stored in [`config/msa_config.json`](config/msa_config.json). Every downstream
filter keys off this file — no MSA can leak in or out without changing this
single config.

### Counties → ZIPs → cities
Federal datasets identify firms by city/state or ZIP, not county FIPS. Phase 1
hits **HUD's USPS ZIP Crosswalk API** (`type=7` county→ZIP) once per county FIPS,
harvests every ZIP plus the city + state name in each row.

- [`config/zip_allowlist.csv`](config/zip_allowlist.csv) — 1,680 ZIPs
- [`config/city_allowlist.csv`](config/city_allowlist.csv) — 1,243 (msa, city, state) tuples

This means city/state scoping is **data-driven from the federal crosswalk**, not
hand-typed.

---

## Data sources

### A. SEC Form D filings — private placements (Reg D)
- **What:** Every U.S. company raising capital from accredited investors must
  file Form D within 15 days. Includes seed rounds, Series A/B/C, PE rollups,
  fund formations.
- **Where:** SEC EDGAR quarterly bulk ZIPs at
  `sec.gov/files/structureddata/data/form-d-data-sets/{YYYY}q{Q}_d.zip`
- **Format:** Each ZIP contains 4 tab-separated tables (FORMDSUBMISSION,
  ISSUERS, OFFERING, RECIPIENTS), joined on `ACCESSIONNUMBER`.
- **Volume processed:** 24 quarters (2020-Q1 → 2025-Q4) × ~3K-6K issuers each.
- **Filters applied:**
  1. `STATEORCOUNTRY` ∈ {PA, NJ, DE, MD, GA, TX}
  2. `(ISSUER_CITY, STATE)` ∈ city allowlist
  3. `INDUSTRYGROUPTYPE` ∈ {Pharmaceuticals, Biotechnology, Other Health Care}
- **Result:** **1,408 filings → 717 unique companies**
- **Confidence tier:** `operating_company` — actually raised capital.

### B. SBIR / STTR awards — federal innovation grants
- **What:** Small Business Innovation Research / Small Business Technology
  Transfer awards from NIH, DoD, NSF, DOE, etc. Phase I ≈ $300K feasibility;
  Phase II ≈ $2M development.
- **Where:** SBIR.gov bulk CSV
  (`data.www.sbir.gov/mod_awarddatapublic_no_abstract/award_data_no_abstract.csv`,
  ~70 MB, all agencies, no abstracts).
  Used because the SBIR Public API was returning HTTP 429 globally — bulk CSV
  is the documented fallback.
- **Filters applied:**
  1. State name (full → 2-letter mapping built into pipeline)
  2. `(City, State)` ∈ city allowlist
- **Result:** **15,051 awards in MSA scope → 2,793 unique companies**
- **Confidence tier:** `grant_only_company` — federal peer-reviewed innovation
  funding.

### C. NIH RePORTER — research grants
- **What:** Every NIH grant (R01, R44, U01, etc.). All PIs, all institutions.
- **Where:** NIH RePORTER v2 API —
  `POST https://api.reporter.nih.gov/v2/projects/search`
- **Method:** Per (MSA × fiscal year) query with `org_states`, `org_cities`,
  `fiscal_years`. Paginated 500/page. NIH's own 10K-record cap applied.
- **Volume:** 5 MSAs × 6 FYs (FY2020–FY2025) → **60,127 grant records →
  ~520 unique recipient organizations**.
- **Note:** Most NIH-only entities are universities and hospitals. Phase 8
  (classifier) tags these as `research_inst` and excludes them from the
  startup roster.

### D. University TTO + incubator portfolios (Phase 4)
Targeted scrape of:
- Engage Ventures (Atlanta) — 114 cos, names from logo image filenames
- Innovation Works (Pittsburgh) — 10 visible (site is JS-paginated)
- UMD Momentum Fund (Baltimore) — 4 cards
- Health Wildcatters (Dallas) — 1 (alt-text recovery)

Plus **80 publicly-documented spinouts** from Penn PCI, JHU/JHTV, Emory OTT,
Georgia Tech VentureLab, UT Southwestern, Pitt, CMU. (Their own portfolio sites
are gated by Cloudflare or JS-rendered, so this is a curated seed list pulled
from press releases.)

Captured in [`data/raw/tto_portfolio.parquet`](output/tto_portfolio.csv).
**209 entities total** — most are already in Form D / SBIR; the genuinely new
contribution is ~172 `tto_spinout` entries.

---

## Pipeline architecture

10-phase pipeline; each phase is independent and resumable via JSON manifest
checkpoints.

```
src/
├── common.py                    HTTP w/ retries (tenacity, exp backoff, 3x),
│                                rate limiters, dotenv loader, manifest helpers
├── phase1_config.py             HUD county → ZIP/city crosswalk
├── phase2_federal_grants.py     NIH RePORTER harvest
├── phase2b_sbir_bulk.py         SBIR bulk CSV (Public API fallback)
├── phase3_sec_form_d.py         SEC Form D quarterly bulk
├── phase4_tto_scrape.py         University TTO + incubator portfolios
├── phase7a_interim_roster.py    Cross-source merge (interim)
└── phase8_classify.py           Classify entity_type + ls_subcategory + tier
```

### HTTP discipline
- **User-Agent** with contact email on every request (SEC ToS requires it).
- **Rate limits:** SEC 1 req/s, NIH 3 req/s, SBIR 1 req/s.
- **Retries:** 3 attempts, exponential backoff (1s → 16s).
- All HTTP goes through `common.http_get / http_post` so the policy is uniform.

### Output formats
Every phase writes both **parquet** (efficient) and **CSV + Excel**
(for non-technical reviewers).

---

## Dedup & merge logic (Phase 7a)

A single company can appear in all three federal sources with slightly
different names:

```
Form D:  "Spark Therapeutics, Inc."
SBIR:    "SPARK THERAPEUTICS INC"
NIH:     "Spark Therapeutics, Inc."
```

### Normalization
1. Lowercase
2. Strip punctuation
3. Strip legal suffixes: `inc, llc, ltd, corp, co, plc, pbc, holdings, group, gmbh, ag, the, ...`
4. Collapse whitespace

→ all three above collapse to `spark therapeutics`.

### Merge keys (priority order)
1. `(msa, normalized_name)` — primary
2. SEC `CIK` (Form D-internal duplicates)
3. UEI / DUNS (SBIR-internal duplicates)

### Aggregation
- Source flags (`source_form_d`, `source_sbir`, `source_nih`, `source_tto`)
  → boolean OR across rows
- Filing/award counts → SUM
- Dollar amounts → SUM
- All other fields → first non-null

---

## Phase 8 — classification

Each merged entity gets three labels:

### `entity_type`
Rule-based regex over the normalized name + Form D entity-type field:

| Type | Trigger |
|---|---|
| `university` | `university, college, school of medicine, polytechnic, ...` |
| `hospital`   | `hospital, medical center, health system, clinic, cancer center, ...` |
| `govt_lab`   | `department of defense, naval, NASA, CDC, NIH, ...` |
| `nonprofit`  | `foundation, association, consortium, ...` (and not Form D-funded) |
| `research_inst` | NIH-only, no Form D, no SBIR (heuristic) |
| `startup`    | default |

### `ls_subcategory`
Keyword match over name + tagline + Form D industry field:

| Bucket | Keywords (sample) |
|---|---|
| `pharma` | `pharma, therapeutic, drug, rx, biopharm` |
| `biotech` | `bio, genomics, gene, rna, cell, vaccine, immuno, oncology, ...` |
| `medtech` | `medical device, robotics, imaging, surgical, implant, ...` |
| `diagnostics` | `diagnost, biomarker, assay, liquid biopsy, ...` |
| `chemistry` | `chemic, polymer, material science, catalyst, ...` |
| `digital_health` | `software, platform, AI, telehealth, EHR, ...` |
| `services` | `consulting, CRO, CDMO, contract research, ...` |
| `unknown` | nothing matched |

### `tier` (final designation)
| Tier | Rule | Interpretation |
|---|---|---|
| `operating_company` | Form D + entity_type=startup | Real funded startup |
| `grant_only_company` | SBIR + entity_type=startup, no Form D | Federal-grant SBIR shop |
| `tto_spinout` | TTO/seed only, no federal record | Pre-revenue spinout |
| `research_inst` | university/hospital/govt/nonprofit | Filtered from startup list |

---

## Accuracy & limitations (honest assessment)

### What is rigorous
- **MSA scoping** is via OMB-defined county FIPS → HUD ZIP crosswalk →
  city allowlist. Fully traceable.
- **Form D & SBIR** are federal authoritative records. Every row is verifiable
  on EDGAR / SBIR.gov.
- **Industry filtering for Form D** uses SEC's own `INDUSTRYGROUPTYPE` taxonomy
  (Pharmaceuticals / Biotechnology / Other Health Care) — not free-text
  guessing.
- **Dedup is conservative**: exact normalized-name match. Will not
  over-collapse distinct firms.

### Known limitations
1. **Fuzzy name matching not yet applied.** `Spark Therapeutics` vs.
   `Spark Therapeutic` (no `s`) currently treated as separate. Estimated
   **2–4% false-uniques** in the current count. Phase 7-full will use
   Jaro-Winkler ≥ 0.92.
2. **NIH-only research-org filter is heuristic.** Universities/hospitals are
   caught by name regex. A small consulting shop that only ever won NIH grants
   (no SBIR, no Form D) might be mis-tagged as `research_inst`. Manual review
   recommended for the 191 entries.
3. **`unknown` LS subcategory is large** (~75% of SBIR-only rows). Reason:
   bulk SBIR CSV omits abstracts. Phase 8b (planned) will use the SBIR award
   *title* to refine these.
4. **Pre-revenue stealth startups** that haven't filed Form D or won a federal
   grant won't appear unless they're on a TTO portfolio page. Penn PCI and
   JHTV — the two largest such sources — are gated by Cloudflare and could
   not be machine-scraped. The 80-name curated seed list is a partial
   workaround.
5. **No revenue / employee-count enrichment** beyond what SBIR self-reports.
   That requires paid sources (Crunchbase, PitchBook) or LinkedIn scraping —
   intentionally out of scope.
6. **MSA boundary edge cases.** A firm headquartered in Lancaster County, PA
   (adjacent to Philly MSA but not in OMB's definition) is correctly excluded.
   This may differ from intuitive "Greater Philly" expectations. The county
   FIPS list is the ground truth.
7. **Form D issuers that filed under a parent holding company** may be
   double-counted with the operating subsidiary unless they share a CIK.

### Confidence by tier
- `operating_company` (699): **high** — at least one Form D filing, name-matched
  to the issuer.
- `grant_only_company` (2,692): **high** — at least one SBIR award.
- `tto_spinout` (172): **medium** — depends on whether the TTO listing was a
  curated seed or a noisy logo extraction.
- `research_inst` (191): **medium-high** — entity-type rule is conservative.

---

## Reproducing this analysis

### Prereqs
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### `.env`
```
USER_AGENT_EMAIL=you@example.com    # SEC ToS requires this
HUD_API_TOKEN=<free token from huduser.gov>
```

### Run
```bash
python src/phase1_config.py            # county → ZIP/city crosswalk (~5 min)
python src/phase2_federal_grants.py    # NIH RePORTER (~30 min)
python src/phase2b_sbir_bulk.py        # SBIR bulk CSV (~5 min)
python src/phase3_sec_form_d.py        # SEC Form D 24 quarters (~10 min)
python src/phase4_tto_scrape.py        # TTO portfolios (~1 min)
python src/phase7a_interim_roster.py   # Interim merge
python src/phase8_classify.py          # Classifier + final outputs
python src/phase9_wetlab_prospects.py  # Wet-lab tenant prospect filter (~30 s)
```

Each phase writes a manifest to `data/checkpoints/`; reruns skip completed
work. Use `--force` to redo a phase.

---

## Phase 9: Wet-lab prospect filtering

**Input:** `output/companies_final_startups_only.csv` (3,563 rows from Phase 8)

**Output:** ~816 wet-lab tenant prospects — companies that credibly need bench/
lab space rather than office or SaaS space.

**Key outputs:**

| File | What it is |
|---|---|
| `output/wet_lab_prospects.csv` | Full filtered list with `priority_score` and `founded_year` |
| `output/wet_lab_demand_analysis.xlsx` | 4-sheet Excel deliverable (Summary, Top Prospects, All Prospects, Methodology) |
| `output/phase9_audit_log.csv` | Row count at every filter step — for manager audit |
| `output/dropped_geography.csv` | Step 3 drops (TTO companies outside assigned MSA) |
| `output/dropped_public_companies.csv` | Step 8 drops (SEC-listed companies by CIK) |

Every dropped row is traceable to a specific step. Exclusion lists live in
`config/` as JSON files so they can be reviewed and extended without touching code.

### Filter steps

| Step | What it does | Why |
|---|---|---|
| **Step 1 — Re-dedup** | Union-find merge on `(msa, norm_name)`, then SEC CIK, UEI, DUNS | Phase 7a exact-name dedup leaves cross-source duplicates when IDs match |
| **Step 2 — Fuzzy merge** | Jaro-Winkler ≥ 0.95 (≤2 tokens) or ≥ 0.92 (longer), blocked by `(msa, first non-generic token)`. Vetoed if both rows have conflicting hard IDs | Catches pluralization typos and minor name variants |
| **Step 3 — Geography** | TTO rows where `tto_location` is clearly outside the assigned MSA are dropped to `dropped_geography.csv` | Engage Ventures (Atlanta) and similar aggregators list companies nationally |
| **Step 4 — Wet-lab subcat** | Keep `biotech/pharma/diagnostics/chemistry/medtech`. Keep `unknown` only if name matches wet-lab keyword regex. Drop `digital_health` and `services` | Targets companies that need bench space, not office or SaaS |
| **Step 5 — Recency** | Keep if Form D filed (already 2020+ scoped), SBIR last year ≥ 2020, or TTO-listed | Removes dormant / likely-dissolved firms |
| **Step 6 — Stage** | Drop if SBIR span > 20 yr AND total SBIR > $20M | Large long-running govt contractors own their own facilities |
| **Step 7 — SPV / fund vehicles** | Regex patterns for numbered series, Greek-letter funds, Roman-numeral funds, SPVs, master funds, etc. Also drops names in `config/pe_rollup_exclusions.json` | Form D includes fund-formation vehicles that are not operating companies |
| **Step 8 — Public companies** | Fetch SEC ticker file; drop any row whose CIK appears in it. Drops to `dropped_public_companies.csv` | Publicly traded companies manage their own real estate |
| **Step 9 — Non-wet-lab exclusions** | Drop names in `config/non_wetlab_exclusions.json` | Defense/IT/robotics firms incorrectly tagged life-sci by the keyword classifier |
| **Step 10 — Priority score** | +3 Form D, +3/2/1 SBIR recency, +2 TTO, +2 high subcat, +1 founded ≥ 2020 | Helps order outreach within each MSA |
| **Step 11 — `founded_year`** | SEC `year_incorp` only; blank if outside [1900, 2030] | No proxy sources — SBIR self-reported dates are unreliable for founding year |

### Maintaining the exclusion lists

- **`config/pe_rollup_exclusions.json`** — PE-practice rollup entities (dental chains,
  vision centers, home-health aggregators, etc.) that appear in Form D but are not
  wet-lab tenants. Add names here; matching is case-insensitive on the normalized name.
- **`config/non_wetlab_exclusions.json`** — Defense, IT, and robotics firms that the
  keyword classifier mis-tags as life-sci. Same matching rules.

### Unit tests

```bash
python -m pytest tests/test_phase9.py -v -k "not integration"   # fast unit tests
python -m pytest tests/test_phase9.py -v -m integration          # end-to-end (needs Phase 8 output)
```

The integration test asserts the final row count is between 800 and 900.

---

## Repo layout

```
ls-chem-ecosystem/
├── config/                 MSA + ZIP + city allowlist + TTO inventory
├── src/                    Pipeline phases
├── output/                 Final CSV/XLSX (committed — see top of README)
├── data/raw/               Cached source ZIPs & parquets (gitignored)
├── data/checkpoints/       Phase manifests (gitignored)
├── logs/                   Run logs (gitignored)
└── requirements.txt
```

---

## License

Source code: MIT.

Output data is derived entirely from public federal records (SEC EDGAR, NIH
RePORTER, SBIR.gov, HUD) and from publicly-listed TTO portfolios. No
copyrighted or licensed third-party datasets were used.
