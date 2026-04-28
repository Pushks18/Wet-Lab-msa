# Wet-Lab Tenant Prospect Analysis — Manager Q&A

A reference document anticipating questions about scope, data, methods, and accuracy.
All numbers reflect the committed deliverable: `output/wet_lab_demand_analysis.xlsx` (1,181 prospects).

---

## 1. Project context

**Q: What does this project deliver?**
A list of **1,181 wet-lab tenant prospects** — life-sciences and chemistry companies that credibly need bench/lab space — across 5 U.S. metropolitan statistical areas (Philadelphia, Pittsburgh, Baltimore, Atlanta, Dallas–Fort Worth). Each row is traceable to at least one federal public-record source.

**Q: Why these 5 MSAs?**
They were the agreed scope at project kickoff. The pipeline reads `config/msa_config.json` — adding a new MSA is a config change, not a code rewrite.

**Q: What does "wet-lab tenant prospect" mean?**
A company that (a) is privately operating, (b) works in life-sciences (biotech/pharma/medtech/diagnostics/chemistry), (c) had a recent federal touchpoint since 2015, and (d) is not a public company, mature government contractor, fund vehicle, or healthcare service-operating chain. These are the companies most likely to need physical lab space.

**Q: How much did this cost?**
$0. Every data source is a free federal API or bulk download.

---

## 2. Data sources & coverage

**Q: Where does the data come from?**
Four federal public-record sources:

| Source | What it gives us | Volume |
|---|---|---:|
| **SEC Form D** filings | Private placements (Reg D capital raises) — proves the company raised money | 2,649 filings / 1,150 unique CIKs |
| **SBIR / STTR** awards | Federal innovation grants (NIH, DoD, NSF, DOE) | 15,051 awards / 2,793 unique companies |
| **NIH RePORTER** | Research grants — used mainly to identify universities/hospitals to FILTER OUT | 105,151 grant records / ~600 orgs |
| **University TTO portfolios** | Penn PCI, JHU/JHTV, Emory OTT, GA Tech VentureLab, Pitt, CMU, UTSW spinouts | 209 entities |

**Q: What time window does the data cover?**
- SEC Form D: 45 quarterly bulk ZIPs from 2015-Q1 → 2026-Q1
- NIH: FY2015–FY2025 (11 fiscal years)
- SBIR: all-time bulk CSV, then filtered to 2015+ at Phase 9
- TTO: snapshot of currently-listed portfolio companies

**Q: Why 2015 as the floor?**
A company whose last federal trace was before 2015 is likely defunct, relocated, or acquired. 2015 is recent enough to matter for current tenant outreach but wide enough to capture early-stage companies that haven't filed Form D yet.

**Q: How is geography defined?**
By **OMB Bulletin 23-01** — the federal MSA delineation, which is a fixed list of county FIPS codes per metro. Every downstream filter reads `config/msa_config.json`. The HUD ZIP Crosswalk API expanded those 65 county FIPS into 1,680 ZIP codes and 1,243 (city, state) tuples. No hand-typed cities — the geography is data-driven.

---

## 3. Methodology

**Q: Walk me through how a row gets into the final 1,181.**
A row passes through 9 Phase 9 filters in order, on top of Phase 7a (cross-source merge) + Phase 8 (entity classification). Each filter is logged in `output/phase9_audit_log.csv`:

| Step | What it does | Rows after step |
|---|---|---:|
| Phase 7a | Cross-source merge (4 federal sources → unique entities) | 4,294 |
| Phase 8 | Drop universities/hospitals/government labs/research-only orgs | 4,001 |
| 1. Re-dedup | Merge same-company variants by name + CIK + UEI + DUNS | 3,956 |
| 2. Fuzzy merge | Jaro-Winkler ≥0.95 (≤2 tokens) / ≥0.92 (longer) for typo variants | 3,948 |
| 2b. Manual merges | Explicit parent/child rebrand pairs (FlowMetric, Nanoscope, Nava, Gladius) from `config/manual_merges.json` | 3,944 |
| 3. Geography | Drop TTO rows with HQ outside assigned MSA | 3,899 |
| 4. Wet-lab subcategory | Keep biotech / pharma / dx / chem / medtech (Phase 8 keywords broadened to catch `biomed*`, `bioscience`, `biolog*`, `protein`, `microbi*`, `bioengineer`, `nanotech*`, `cardio*`, `dental`, `orthopedic`, `catheter`, `stent`, `prosthe*`, `infusion`) | 1,780 |
| 5. Recency | Keep Form-D OR sbir_last_year ≥ 2015 OR TTO-listed | 1,436 |
| 6. Stage | Drop SBIR span > 20 yr **AND** total > $20M | 1,427 |
| 7. SPV / fund vehicles + chain rollups | Drop numbered series, Greek/Roman fund vintages, SPV, Master Fund, USRC dialysis chain (75), Texas Health Surgery (5), PGC senior living (4), Shield Series ALPHA/BETA, Empower Investors LP, Nature's Care wellness, Teresa's House care home, etc. | 1,258 |
| 8. Public companies | Drop CIKs in SEC ticker file | 1,192 |
| 9. Non-wet-lab | Drop defense / IT / robotics from manual list | **1,181** |

**Q: How did we filter out universities and hospitals?**
Phase 8 uses a regex over the entity name:
- `university`, `college`, `school of medicine`, `polytechnic` → `university`
- `hospital`, `medical center`, `health system`, `clinic`, `cancer center` → `hospital`
- `department of defense`, `naval`, `NASA`, `CDC`, `NIH` → `govt_lab`
- `foundation`, `association`, `consortium` → `nonprofit`
- NIH-only with no Form D / no SBIR → `research_inst` (heuristic)
- everything else → `startup`

The 293 research institutions are kept in `output/companies_final.csv` for transparency but excluded from the final list.

**Q: How did we filter out PE rollups and SPVs?**
Two mechanisms:
1. **Regex patterns** in Phase 9 step 7: numbered Series, Greek/Roman fund vintages (Fund I, II, III; Alpha, Beta, Gamma), SPV, Master Fund, Feeder Fund, Aid Fund, RE Holdings, Acquisition Corp, etc.
2. **Two manual exclusion lists** in `config/`:
   - `pe_rollup_exclusions.json` — known healthcare PE rollups (USRC, Texas Health Surgery Center, PGC senior living chains, etc.)
   - `non_wetlab_exclusions.json` — defense/IT/robotics firms the keyword classifier mis-tagged as life-sci (Carnegie Robotics, Gecko Robotics, NeuroFlow, etc.)

**Q: How is "priority_score" calculated?**
+3 if Form D filed; +3/+2/+1 for SBIR recency (2024+ / 2022-23 / 2020-21); +2 if TTO-listed; +2 if subcategory is biotech/pharma/diagnostics/chemistry; +1 if founded ≥ 2015. Range 0–11. Used to order outreach within each MSA.

**Q: Why are some rows ranked higher than others?**
A Form D filer that's recently funded, in biotech, founded recently → score 11. An SBIR-only company from 2017 → score 1–2. Score 0 means recency-gate-passing but no other strong signal.

---

## 4. Final numbers

**Q: How many prospects per MSA?**

| MSA | Prospects |
|---|---:|
| Philadelphia | 425 |
| Dallas–Fort Worth | 254 |
| Baltimore | 192 |
| Atlanta | 185 |
| Pittsburgh | 125 |
| **Total** | **1,181** |

**Q: What's the tier breakdown?**

| Tier | Count | Meaning |
|---|---:|---|
| `operating_company` | 895 | Filed Form D since 2015 → real funded company |
| `grant_only_company` | 257 | SBIR-funded but no Form D → federal R&D shop |
| `tto_spinout` | 29 | University TTO listing, no federal record |

**Q: What's the subcategory split?**
Predominantly **medtech** and **biotech**. Pharma ~150 rows. Diagnostics + chemistry are smaller. The breakdown by MSA is in the Summary sheet of the Excel.

**Q: How recent are these companies?**
Of the 769 rows with a known founded year (706 from SEC Form D + 43 from a curated manual backfill + 20 from the company-website "About" page scraper), the distribution peaks around 2017 (~104 companies founded that year), with ~70–80 per year from 2014–2023, tapering to 14 in 2025. The remaining 412 rows without a year are mostly SBIR-only entities — by design we don't use their unreliable self-reported founding dates (see Q on founded_year coverage below).

---

## 5. Verification & accuracy

**Q: How do you know these numbers are right?**
Three forms of evidence:

1. **Every row links to a federal record.** Every `source_form_d=True` row has a SEC accession number; every `source_sbir=True` row has an SBIR award; every TTO row has a portfolio listing. You can click through to verify any single row in <2 minutes.

2. **Random-sample audit (`output/verification_sample.csv`).** A stratified random sample of 30 rows (10 high-priority, 10 mid, 10 low) with one-click SEC EDGAR + SBIR.gov search URLs in each row. The reviewer audits 5 of them by browser, marks `verified` in the notes column. See `output/HOW_TO_VERIFY.md` for the 2-minute-per-row protocol.

3. **Source-count cross-check (`output/verification_source_counts.csv`).** For each MSA, our Form D count is compared to SEC EDGAR's full-text-search totals for the same state and date range. Result: **5/5 MSAs within expected band** (our wet-lab list is 0.5–1.6 % of state-wide Form D, exactly what a life-sciences subset should be).

4. **Spot-check regression (`output/verification_spot_check.csv`).** 7 must-include startups (Spark Therapeutics, Linnaeus, Sonavex, Andson Biotech, Carmell, OXOS Medical, Que Oncology) and 6 must-exclude rows (numbered-series LLCs, defense firms, public companies). PASS/FAIL per row catches regressions.

**Q: How was the manual backfill verified?**
14 TTO spinouts had missing city/state/zip/founded_year. I web-searched each one and recorded source URLs in `output/manual_backfill_log.csv`. Every fill has a `confidence` rating:
- **high**: ≥2 independent authoritative sources agreed (Wikipedia + company website, SEC EDGAR + LinkedIn, etc.)
- **medium**: single authoritative source (e.g., DNB record), or company HQ moved post-acquisition
- **low**: no clear match — left blank for manual review (7 rows: GRIT Bio, Alecto, Boli, Cellect, Micromedicine, Adva, Ondine — likely TTO-list curation errors upstream)

The manager can open `manual_backfill_log.csv`, click any `source_url`, and verify the value matches the source page.

**Q: What if I doubt a specific company is a real wet-lab tenant?**
Open `output/wet_lab_prospects.csv`, find the row, and:
1. Click the `cik` → look up `https://efts.sec.gov/LATEST/search-index?q={name}&forms=D` to see Form D filings
2. Look up `https://www.sbir.gov/sbirsearch/award/all?firm={name}` for SBIR awards
3. Visit the `website` column — does it look like an operating wet-lab company?

If any of those raise a concern, add the company name to `config/non_wetlab_exclusions.json` and re-run Phase 9 (~1 second). The exclusion is data, not code — the audit trail records why.

---

## 6. Limitations & known gaps

**Q: What's NOT in the data?**
- **Pre-revenue stealth startups** that haven't filed Form D, won an SBIR, or appeared on a TTO list. Most notable: Penn PCI and JHU/JHTV portfolio sites are gated by Cloudflare; we used 80 curated press-release names but coverage is partial.
- **Revenue, employee count, funding stage** — we deliberately stayed in free federal sources. Crunchbase / PitchBook / LinkedIn would add this but cost money and have licensing constraints.
- **Phone numbers, contact emails** — not in any of the federal sources at scale.

**Q: What could be wrong?**
1. **~2–4 % false unique counts.** Our dedup is conservative — Spark Therapeutics vs Spark Therapeutic (no s) might still appear twice. Phase 9 step 2 does fuzzy matching at Jaro-Winkler ≥0.92, but typos below that threshold slip through.
2. **`unknown` LS subcategory is large** (~75 % of SBIR-only rows) because the bulk SBIR CSV omits abstracts. Phase 8 falls back to the company name regex, which misses generic-named wet-lab firms.
3. **Geography edge cases.** A firm in Lancaster County, PA (next to Philly but not in OMB's MSA) is correctly excluded. This may differ from intuitive "Greater Philly" expectations — the OMB list is the ground truth.
4. **Holding company vs operating subsidiary.** When a parent files Form D and the operating sub does too, both can appear. CIK-based dedup catches cases where they share a CIK; if not, both stay.
5. **TTO-list errors.** The curated penn_known / pitt_known / utsw_known seed lists occasionally include companies that aren't actually MSA-located (e.g., Adva Biotechnology is in Israel, Ondine is in Vancouver). The web backfill flagged 7 of these for manual review.

**Q: What's the founded_year coverage?**
**769 / 1,181 (65 %).** Three sources, in priority order:
1. **SEC Form D `YEARINCFROM`** — 706 rows. The authoritative source: filers self-attest at filing time.
2. **Manual backfill** (`output/manual_backfill_log.csv`) — 43 high-priority private companies hand-curated with citations to Wikipedia / Crunchbase / company "About" pages / SEC S-1 filings. Each row has a `source_url` and `confidence` rating.
3. **Step 11c website scraper** — 20 rows pulled from `Founded YYYY` / `Established YYYY` / `Since YYYY` / `Incorporated YYYY` regex on the company's `/about` or `/our-story` page. Cached in `data/raw/website_founded_cache.json` (free, slow, ~3 min for 154 sites).

The remaining 412 blanks are mostly SBIR-only or TTO-only entities with no website and no public profile. SBIR self-reported award dates are deliberately not used as a proxy — first-award year ≥ founding year, often by several years. We also tried SEC's `submissions` JSON API as a fallback (Step 11b), but SEC doesn't expose `yearOfIncorporation` for private/Form-D filers — only `stateOfIncorporation`. The endpoint returned 0 hits across all 204 candidate CIKs.

**Q: Why are some companies showing HQs outside the MSA?**
The web-backfill surfaced ~6 TTO-flagged rows (Capstan, Lyell, Ring, AvidBiotics, RedShift) where the company spun out from a Penn / JHU / UTSW lab but later relocated. They're flagged in `manual_backfill_log.csv` with `confidence: medium` and notes. We can drop them with one config change if you want strict MSA enforcement — the impact is small (~6 rows).

---

## 7. Operational

**Q: Can this be re-run?**
Yes — it's a deterministic pipeline. `python src/phase9_wetlab_prospects.py --force` rebuilds the final list in ~1 second. Re-harvesting the federal sources (Phases 2–3) takes ~60–80 minutes due to NIH RePORTER pagination. Same input always produces the same output.

**Q: How do I add a new MSA?**
Edit `config/msa_config.json` → add the CBSA code, state codes, and county FIPS list → run `python src/phase1_config.py` to expand to ZIPs and cities → re-run downstream phases.

**Q: How do I add or remove an exclusion?**
- For a company we want to drop: add the exact name to `config/non_wetlab_exclusions.json` (defense/IT/robotics) or `config/pe_rollup_exclusions.json` (PE rollups). Case-insensitive normalized-name match. Re-run Phase 9.
- For a chain pattern (e.g., a new dialysis provider): add a regex to `_SPV_PATTERNS` in `src/phase9_wetlab_prospects.py`.

**Q: Who can run this?**
Anyone with Python 3.11+, `pip install -r requirements.txt`, and a free HUD API token in `.env`. No proprietary credentials, no paid licenses.

**Q: How often should we re-run?**
Quarterly is the natural cadence — that's how often SEC publishes new Form D ZIPs. NIH and SBIR update continuously but materially-new wet-lab firms are slow.

**Q: Is this code under version control?**
Yes. Repository: `https://github.com/Pushks18/Wet-Lab-msa`. Every change is in git history with a clear commit message.

---

## 8. Tech stack (for the curious)

- **Language:** Python 3.11+
- **Data:** pandas + pyarrow (parquet), openpyxl (Excel)
- **HTTP:** requests + tenacity (3× exponential backoff) + per-host rate limits
- **Fuzzy matching:** jellyfish (Jaro-Winkler)
- **Scraping:** BeautifulSoup + lxml (TTO portfolio pages)
- **Tests:** pytest (28 unit + 1 integration); current state 28/28 pass
- **Verification:** Phase 9-verify hits SEC EDGAR + SBIR.gov + SEC submissions API independently

---

## 9. Files in this deliverable

| File | Purpose |
|---|---|
| **`output/wet_lab_demand_analysis.xlsx`** | The main deliverable. 5 sheets: Summary, Top Prospects, All Prospects (1181), Manual Backfill Log, Methodology |
| `output/wet_lab_prospects.csv` | Same data, plain CSV |
| `output/phase9_audit_log.csv` | Step-by-step funnel from 4,001 → 1,181 |
| `output/manual_backfill_log.csv` | Web-sourced fills with source URLs and confidence ratings |
| `output/dropped_chain_rollups.csv` | 102 PE rollup rows removed (USRC, etc.) |
| `output/dropped_public_companies.csv` | 66 publicly-traded company drops |
| `output/dropped_geography.csv` | 45 TTO rows with out-of-MSA addresses |
| `output/verification_sample.csv` | 30 stratified random rows for browser audit |
| `output/verification_source_counts.csv` | Per-MSA counts vs SEC EDGAR full-text search |
| `output/verification_spot_check.csv` | Must-include / must-exclude regression test |
| `output/HOW_TO_VERIFY.md` | 2-minute-per-row reviewer protocol |
| `README.md` | Full pipeline documentation |
