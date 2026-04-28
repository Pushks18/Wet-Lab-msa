# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A reproducible Python pipeline that builds a roster of life-sciences/chemistry startups across 5 U.S. MSAs (Philadelphia, Pittsburgh, Baltimore, Atlanta, Dallas–Fort Worth) from free federal sources (SEC Form D, SBIR.gov, NIH RePORTER, HUD ZIP crosswalk, university TTO portfolios). Final deliverable: ~3,563 startups; downstream Phase 9 filters to ~816 wet-lab tenant prospects.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

`.env` (required):
```
USER_AGENT_EMAIL=you@example.com    # SEC ToS requires this
HUD_API_TOKEN=<free token from huduser.gov>
```

## Run the pipeline

Phases are sequential; each writes a manifest under `data/checkpoints/` so reruns skip completed work. Use `--force` to redo a phase.

```bash
python src/phase1_config.py            # HUD county→ZIP/city crosswalk
python src/phase2_federal_grants.py    # NIH RePORTER
python src/phase2b_sbir_bulk.py        # SBIR bulk CSV (Public API fallback)
python src/phase3_sec_form_d.py        # SEC Form D 24 quarters
python src/phase4_tto_scrape.py        # University TTO portfolios
python src/phase7a_interim_roster.py   # Cross-source merge
python src/phase8_classify.py          # entity_type + ls_subcategory + tier
python src/phase9_wetlab_prospects.py  # Wet-lab tenant filter
```

## Tests

```bash
python -m pytest tests/test_phase9.py -v -k "not integration"   # fast unit tests
python -m pytest tests/test_phase9.py -v -m integration          # end-to-end; requires Phase 8 output
```

The integration test asserts the final wet-lab row count is in [800, 900].

## Architecture — the things you must know

### Single source of truth for geography
`config/msa_config.json` defines the 5 MSAs as OMB CBSA codes + county FIPS lists. **Every downstream filter keys off this file** — no MSA can leak in or out without changing it. Phase 1 expands FIPS → ZIP/city via the HUD crosswalk and writes `config/zip_allowlist.csv` and `config/city_allowlist.csv`, which all later phases consume.

### HTTP discipline lives in `src/common.py`
All external requests go through `common.http_get` / `http_post`. That module owns: User-Agent with contact email (SEC ToS), per-host rate limits (SEC 1 rps, NIH 3 rps, SBIR 1 rps), tenacity retries (3x exponential backoff 1s→16s), and dotenv loading. **Do not call `requests` directly** from phase modules — add new behavior here so the policy stays uniform.

### Phase contract
Each phase: (1) reads previous-phase outputs from `output/` or `data/raw/`, (2) writes a manifest JSON to `data/checkpoints/` to support resume, (3) emits both parquet (for the next phase) and CSV+XLSX (for non-technical reviewers). Honor `--force` to redo work.

### Dedup logic (Phase 7a)
Normalization: lowercase → strip punctuation → strip legal suffixes (`inc, llc, ltd, corp, co, plc, pbc, holdings, group, gmbh, ag, the`) → collapse whitespace. Merge keys, in priority order: `(msa, normalized_name)` → SEC CIK → UEI/DUNS. Aggregation: source flags OR'd, counts/dollars summed, other fields take first non-null. Phase 7a is intentionally conservative (exact match only); fuzzy matching is deferred to Phase 9 step 2.

### Classification (Phase 8) — three labels per entity
- `entity_type`: regex over name → `university | hospital | govt_lab | nonprofit | research_inst | startup` (default).
- `ls_subcategory`: keyword match → `pharma | biotech | medtech | diagnostics | chemistry | digital_health | services | unknown`. The large `unknown` bucket comes from SBIR bulk CSV omitting abstracts.
- `tier`: `operating_company` (Form D + startup) | `grant_only_company` (SBIR-only startup) | `tto_spinout` | `research_inst`. Research institutions are excluded from the startup roster.

### Phase 9 — wet-lab prospect filter (11 steps)
Reads `output/companies_final_startups_only.csv`. Steps in order: (1) re-dedup union-find on normalized name + IDs; (2) Jaro-Winkler fuzzy merge ≥0.95 (≤2 tokens) or ≥0.92 (longer), blocked by `(msa, first non-generic token)`, vetoed on conflicting hard IDs; (3) drop TTO rows with `tto_location` outside assigned MSA → `dropped_geography.csv`; (4) keep wet-lab subcategories (biotech/pharma/diagnostics/chemistry/medtech, plus `unknown` matching wet-lab regex); (5) recency; (6) drop large long-running SBIR contractors (>20 yr AND >$20M); (7) SPV/fund-vehicle regex + `config/pe_rollup_exclusions.json`; (8) drop SEC ticker-file CIKs → `dropped_public_companies.csv`; (9) `config/non_wetlab_exclusions.json`; (10) priority score; (11) `founded_year` from SEC `year_incorp` only (no proxies).

**Exclusion lists are data, not code.** When defense/IT/PE-rollup firms slip through the classifier, add them to `config/non_wetlab_exclusions.json` or `config/pe_rollup_exclusions.json` (case-insensitive on normalized name) — do not edit phase code.

### Outputs are committed; raw caches are not
`output/` is part of the repo (final CSV/XLSX deliverables). `data/raw/` (NIH ~526 MB, SBIR ~6 MB, Form D quarterly ZIPs), `data/checkpoints/`, and `logs/` are gitignored — the pipeline regenerates them.

## MCP Tools: code-review-graph

This repo has a knowledge graph. Prefer `semantic_search_nodes` / `query_graph` / `detect_changes` / `get_impact_radius` over Grep/Glob/Read for exploration, impact analysis, and review. The graph auto-updates via hooks. Fall back to file scanning only when the graph doesn't cover what you need.
