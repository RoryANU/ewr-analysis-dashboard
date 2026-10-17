"""Consistency of the committed result files (data/results/*.csv) that the dashboard is built from.

These check the arithmetic relationships the files promise (FR = Frequency / TargetFrequency, ETP = L / TotalDays, ...), so a bad refresh of
the data is caught before it is baked into the HTML. Known upstream defects are pinned explicitly so a *new* one fails the test.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import build_dashboard as bd

ROOT = Path(__file__).resolve().parents[1]
TOTAL_DAYS = 47117  # 129 water years (1 Jul 1895 - 30 Jun 2024) x 365.25 days


@pytest.fixture(scope="module")
def fr() -> pd.DataFrame:
    return pd.read_csv(ROOT / "data/results/frequency_ratio.csv", dtype={"Gauge": str})


@pytest.fixture(scope="module")
def et() -> pd.DataFrame:
    d = pd.read_csv(ROOT / "data/results/etp.csv", dtype={"Gauge": str})
    d.columns = d.columns.str.strip()
    return d


class TestFrequencyRatioFile:
    def test_fr_is_frequency_over_target_frequency(self, fr):
        assert (fr.TargetFrequency > 0).all()
        recomputed = fr.Frequency / fr.TargetFrequency
        assert (recomputed - fr.FR).abs().max() < 1e-8  # the file stores FR to 9 decimal places

    def test_no_missing_or_negative_values_in_the_metric_columns(self, fr):
        for col in ("FR", "Frequency", "TargetFrequency", "EventYears", "TotalDays"):
            assert fr[col].notna().all() and (fr[col] >= 0).all(), col

    def test_frequency_is_a_percentage_of_years(self, fr):
        assert fr.Frequency.between(0, 100).all() and fr.TargetFrequency.between(0, 100).all()

    def test_every_row_covers_the_same_129_water_years(self, fr):
        assert (fr.NoDataDays == 0).all()
        assert fr.TotalDays.between(TOTAL_DAYS - 1, TOTAL_DAYS).all()
        assert (fr.EventYears <= 130).all()  # see PR notes: up to 130 > 129 years appear in 252 rows

    def test_scenarios_are_known_and_the_expected_grid_cells_are_present(self, fr):
        assert set(fr.Policy) <= set(bd.POLICIES) and set(fr.ClimateModel) <= set(bd.CLIMATES)
        cells = {bd.scenario_index(p, c) for p, c in zip(fr.Policy, fr.ClimateModel, strict=False)}
        assert len(cells) == 10  # the review ran 10 of the 28 policy x climate combinations
        assert bd.scenario_index("Pre Basin Plan", "Historical Climate") in cells

    def test_one_flow_regime_name_set(self, fr):
        regimes = set(fr.FlowRegime.dropna())
        assert "Baseflow" in regimes and "Overbank" in regimes and "" not in regimes


class TestEtpFile:
    def test_etp_is_l_over_total_days(self, et):
        assert et.ETP.notna().all() and et.L.notna().all() and (et.L >= 0).all() and (et.ETP >= 0).all()
        assert (et.ETP - et.L / TOTAL_DAYS).abs().max() < 1e-6

    def test_mrip_days_are_365_per_year(self, et):
        assert et.MRIP_years.notna().all()
        assert (et.MRIP_days - et.MRIP_years * 365).abs().max() < 1e-9

    def test_etp_is_a_proportion_except_for_known_upstream_defects(self, et):
        """L (days beyond the maximum inter-event period) cannot exceed the 47,117-day record, so ETP > 1 is impossible.
        The MDBA pipeline output has 340 such rows at gauges 422001 and 422025 (LF1_P, OB2, OB3); pinned here, reported in the PR."""
        over = et[et.ETP > 1]
        assert set(zip(over.Gauge, over.EWR, strict=False)) == {("422001", "LF1_P"), ("422001", "OB2"), ("422001", "OB3"), ("422025", "LF1_P")}
        assert len(over) == 340
        assert over.ETP.max() < 1.5
        assert (et[~et.index.isin(over.index)].ETP <= 1).all()

    def test_scenarios_are_known(self, et):
        assert set(et.Policy) <= set(bd.POLICIES) and set(et.ClimateModel) <= set(bd.CLIMATES)
        assert len({bd.scenario_index(p, c) for p, c in zip(et.Policy, et.ClimateModel, strict=False)}) == 10


class TestBothFiles:
    def test_union_of_ewr_keys_is_the_dashboard_row_count(self, fr, et):
        a = set(zip(fr.PlanningUnitName, fr.SWSDLName, fr.Gauge.replace(bd.REKEY), fr.EWR, strict=False))
        b = set(zip(et.PlanningUnitName, et.SWSDLName, et.Gauge.replace(bd.REKEY), et.EWR, strict=False))
        assert len(a | b) == 2277  # the number of rows in the dashboard: an EWR present in only one file still gets a row
        assert a - b and b - a  # ... and each file has EWRs the other lacks (their other metric is null in the page)

    def test_gauge_ids_are_text_without_decimals(self, fr, et):
        for d in (fr, et):
            assert not d.Gauge.astype(str).str.contains(r"\.").any()

    def test_values_are_not_percentages(self, fr, et):
        # FR and ETP are ratios/proportions: a x100 slip would put their means near 100 / 22
        assert 0.5 < fr.FR.mean() < 1.5 and 0.05 < et.ETP.mean() < 0.5
        assert np.isfinite(fr.FR).all() and np.isfinite(et.ETP).all()
