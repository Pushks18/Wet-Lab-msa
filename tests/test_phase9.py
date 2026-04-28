"""Tests for Phase 9 wet-lab prospect filter.

Run with:
    python -m pytest tests/test_phase9.py -v

The integration test (test_final_count_in_range) requires the committed
output/companies_final_startups_only.csv to be present and runs the full
pipeline end-to-end (offline, skipping the SEC ticker fetch).
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

# Ensure src/ is importable
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import phase9_wetlab_prospects as p9


# ── unit tests for individual steps ───────────────────────────────────────────

def _make_df(**cols) -> pd.DataFrame:
    """Build a minimal DataFrame from keyword-argument column→list mappings."""
    return pd.DataFrame(cols)


class TestNorm:
    def test_strips_suffix(self):
        assert p9._norm("Spark Therapeutics, Inc.") == "spark therapeutics"

    def test_strips_punct(self):
        assert p9._norm("A.B. Corp LLC") == "a b"

    def test_empty(self):
        assert p9._norm("") == ""
        assert p9._norm(None) == ""  # type: ignore[arg-type]
        assert p9._norm(float("nan")) == ""


class TestStep1Dedup:
    def test_merges_same_norm_name(self):
        df = _make_df(
            msa=["phila", "phila"],
            name=["Acme Bio, Inc.", "ACME BIO INC"],
            source_form_d=[True, False],
            source_sbir=[False, True],
            sbir_awards=[0.0, 3.0],
            sbir_total_usd=[0.0, 500_000.0],
        )
        out = p9._step1_dedup(df)
        assert len(out) == 1
        assert out.iloc[0]["source_form_d"]
        assert out.iloc[0]["source_sbir"]
        assert out.iloc[0]["sbir_total_usd"] == 500_000.0

    def test_keeps_different_msa(self):
        df = _make_df(
            msa=["phila", "atlanta"],
            name=["Acme Bio Inc", "Acme Bio Inc"],
            source_form_d=[True, True],
        )
        out = p9._step1_dedup(df)
        assert len(out) == 2

    def test_merges_on_cik(self):
        df = _make_df(
            msa=["phila", "phila"],
            name=["Alpha Therapeutics LLC", "Alpha Therapeutics Holdings"],
            cik=[12345.0, 12345.0],
            source_form_d=[True, False],
            source_tto=[False, True],
        )
        out = p9._step1_dedup(df)
        assert len(out) == 1


class TestStep4Subcat:
    def _base_row(self, subcat, name="Acme Bio") -> dict:
        return dict(ls_subcategory=subcat, name=name)

    def test_keeps_wetlab_cats(self):
        for cat in ("biotech", "pharma", "diagnostics", "chemistry", "medtech"):
            df = pd.DataFrame([self._base_row(cat)])
            assert len(p9._step4_subcat(df)) == 1

    def test_drops_digital_services(self):
        for cat in ("digital_health", "services"):
            df = pd.DataFrame([self._base_row(cat)])
            assert len(p9._step4_subcat(df)) == 0

    def test_unknown_kept_on_name_match(self):
        df = pd.DataFrame([self._base_row("unknown", name="Genomics Solutions LLC")])
        assert len(p9._step4_subcat(df)) == 1

    def test_unknown_dropped_on_no_match(self):
        df = pd.DataFrame([self._base_row("unknown", name="Software Metrics Inc")])
        assert len(p9._step4_subcat(df)) == 0


class TestStep5Recency:
    def test_keeps_form_d(self):
        df = _make_df(source_form_d=[True], source_tto=[False],
                      sbir_last_year=[2010.0])
        assert len(p9._step5_recency(df)) == 1

    def test_keeps_recent_sbir(self):
        df = _make_df(source_form_d=[False], source_tto=[False],
                      sbir_last_year=[2021.0])
        assert len(p9._step5_recency(df)) == 1

    def test_keeps_2015_sbir(self):
        df = _make_df(source_form_d=[False], source_tto=[False],
                      sbir_last_year=[2015.0])
        assert len(p9._step5_recency(df)) == 1

    def test_drops_pre_2015_sbir_no_fd(self):
        df = _make_df(source_form_d=[False], source_tto=[False],
                      sbir_last_year=[2010.0])
        assert len(p9._step5_recency(df)) == 0


class TestStep6Stage:
    def test_drops_mature_contractor(self):
        df = _make_df(sbir_first_year=[1995.0], sbir_last_year=[2020.0],
                      sbir_total_usd=[25_000_000.0])
        assert len(p9._step6_stage(df)) == 0

    def test_keeps_long_but_small(self):
        df = _make_df(sbir_first_year=[1995.0], sbir_last_year=[2020.0],
                      sbir_total_usd=[5_000_000.0])
        assert len(p9._step6_stage(df)) == 1

    def test_keeps_large_but_short(self):
        df = _make_df(sbir_first_year=[2015.0], sbir_last_year=[2020.0],
                      sbir_total_usd=[25_000_000.0])
        assert len(p9._step6_stage(df)) == 1


class TestStep7SPV:
    def test_drops_numbered_series(self):
        df = _make_df(name=["OXOS Series #6 Holdings, LLC"])
        assert len(p9._step7_spv(df, set())) == 0

    def test_drops_greek_fund(self):
        df = _make_df(name=["NovaDerm Aid Fund Alpha, L.L.C."])
        assert len(p9._step7_spv(df, set())) == 0

    def test_drops_roman_fund(self):
        df = _make_df(name=["PRIME ER FUND I LLC"])
        assert len(p9._step7_spv(df, set())) == 0

    def test_drops_spv(self):
        df = _make_df(name=["Cancer Check SPV LLC"])
        assert len(p9._step7_spv(df, set())) == 0

    def test_keeps_normal_name(self):
        df = _make_df(name=["Spark Therapeutics Inc"])
        assert len(p9._step7_spv(df, set())) == 1

    def test_drops_pe_exclusion(self):
        pe = {p9._norm("Pan-Am Dental Holdings")}
        df = _make_df(name=["Pan-Am Dental Holdings"])
        assert len(p9._step7_spv(df, pe)) == 0


class TestStep10Score:
    def test_max_score(self):
        df = _make_df(
            source_form_d=[True],
            source_tto=[True],
            sbir_last_year=[2025.0],
            ls_subcategory=["biotech"],
            year_incorp=[2022.0],
        )
        out = p9._step10_score(df)
        # +3 form_d +3 sbir_recent +2 tto +2 subcat +1 incorp = 11
        assert out.iloc[0]["priority_score"] == 11

    def test_zero_score(self):
        df = _make_df(
            source_form_d=[False],
            source_tto=[False],
            sbir_last_year=[2010.0],
            ls_subcategory=["unknown"],
            year_incorp=[2005.0],
        )
        out = p9._step10_score(df)
        assert out.iloc[0]["priority_score"] == 0


class TestStep11Founded:
    def test_valid_year(self):
        df = _make_df(year_incorp=[2019.0])
        out = p9._step11_founded(df)
        assert out.iloc[0]["founded_year"] == 2019.0

    def test_out_of_range_blanked(self):
        df = _make_df(year_incorp=[1850.0])
        out = p9._step11_founded(df)
        assert pd.isna(out.iloc[0]["founded_year"])

    def test_missing_blanked(self):
        df = _make_df(year_incorp=[float("nan")])
        out = p9._step11_founded(df)
        assert pd.isna(out.iloc[0]["founded_year"])


# ── integration test ───────────────────────────────────────────────────────────

@pytest.mark.integration
def test_final_count_in_range():
    """Full pipeline integration test.

    Requires output/companies_final_startups_only.csv (committed to repo).
    Mocks the SEC ticker fetch so the test is offline-safe.
    Expected final row count: 1100–1500 after the full 2015+ backfill of all
    sources (NIH FY2015-2025, SEC Form D 2015-Q1 → 2026-Q1, SBIR all-time, TTO).
    Was 800-900 under the original 2020+ floor; landed at ~1,290 after backfill.
    """
    input_csv = REPO_ROOT / "output" / "companies_final_startups_only.csv"
    if not input_csv.exists():
        pytest.skip("companies_final_startups_only.csv not present — run Phase 8 first")

    # Reload module fresh so audit log accumulator is clean
    importlib.reload(p9)

    with (
        patch.object(p9, "_fetch_public_ciks", return_value={}),
        patch.object(p9, "write_manifest"),
    ):
        p9.main(force=True)

    out_csv = REPO_ROOT / "output" / "wet_lab_prospects.csv"
    assert out_csv.exists(), "wet_lab_prospects.csv was not written"

    result = pd.read_csv(out_csv)
    count = len(result)
    assert 1100 <= count <= 1500, (
        f"Expected 1100–1500 wet-lab prospects, got {count}. "
        "Check audit log at output/phase9_audit_log.csv for which step caused drift."
    )
