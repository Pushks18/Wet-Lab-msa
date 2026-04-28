# How to verify the wet-lab prospect list

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
     of 2020 or later. If yes, the SEC source is verified.
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
