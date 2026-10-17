"""Tests for src/northern_connectivity.py: reading the expert-panel workbook layout and mapping site names to gauges.

The workbook is replaced by an in-memory frame with the same layout (pd.read_excel is patched), so no Excel file or openpyxl is needed.
Layout (0-based rows): 2 = scenario labels, 4 = 'Date' + site names, 5 = units, 6.. = daily data.
"""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

import northern_connectivity as nc

NAN = np.nan


def workbook() -> pd.DataFrame:
    """Two scenarios side by side (CP1A: cols 1-2, CP2B: cols 4-5) separated by a blank column 3."""
    rows = [
        [NAN, NAN, NAN, NAN, NAN, NAN],  # 0 title rows
        [NAN, NAN, NAN, NAN, NAN, NAN],
        [NAN, "Scenario CP1A - base", NAN, NAN, "Scenario CP2B", NAN],  # 2 scenario labels
        [NAN, NAN, NAN, NAN, NAN, NAN],
        ["Date", "Wilcannia", "Bourke", NAN, "Wilcannia", "Bourke"],  # 4 site names
        [NAN, "ML/d", "ML/d", NAN, "ML/d", "ML/d"],  # 5 units
        [datetime(2000, 1, 1), 10.0, 20.0, NAN, 11.0, 21.0],
        ["02/01/2000", 0.0, "--", NAN, 12.0, 22.0],  # dd/mm/yyyy text date; '--' is not a number
        [datetime(2000, 1, 3), 30.0, 40.0, NAN, NAN, 23.0],
        ["28/02/1900", 1.0, 2.0, NAN, 3.0, 4.0],  # Excel's phantom leap day shows up as a repeated 28/02/1900
        ["28/02/1900", 9.0, 9.0, NAN, 9.0, 9.0],  # the repeat is dropped (first kept)
        ["not a date", 5.0, 5.0, NAN, 5.0, 5.0],  # unparseable: dropped
    ]
    return pd.DataFrame(rows, dtype=object)


@pytest.fixture
def scenarios(monkeypatch):
    monkeypatch.setattr(pd, "read_excel", lambda *a, **k: workbook())
    return nc.unpack_northern_connectivity("book.xlsx")


class TestUnpack:
    def test_scenario_codes_and_sites(self, scenarios):
        assert list(scenarios) == ["CP1A", "CP2B"]  # 'Scenario CP1A - base' -> CP1A
        for df in scenarios.values():
            assert list(df.columns) == ["Wilcannia", "Bourke"]  # the blank column between blocks is skipped
            assert df.index.name == "Date"

    def test_dates_are_parsed_deduplicated_and_in_sheet_order(self, scenarios):
        expected = pd.DatetimeIndex(["2000-01-01", "2000-01-02", "2000-01-03", "1900-02-28"])
        for df in scenarios.values():
            assert df.index.equals(expected.rename("Date"))
            assert df.index.is_unique

    def test_values_by_hand(self, scenarios):
        a, b = scenarios["CP1A"], scenarios["CP2B"]
        assert a["Wilcannia"].tolist() == [10.0, 0.0, 30.0, 1.0]  # zero flow kept as 0, the dropped repeats (9.0) and bad date (5.0) absent
        assert a["Bourke"].isna().tolist() == [False, True, False, False]  # '--' -> NaN
        assert a["Bourke"].dropna().tolist() == [20.0, 40.0, 2.0]
        assert b["Wilcannia"].tolist()[0:2] == [11.0, 12.0] and np.isnan(b["Wilcannia"].iloc[2]) and b["Wilcannia"].iloc[3] == 3.0
        assert b["Bourke"].tolist() == [21.0, 22.0, 23.0, 4.0]

    def test_every_scenario_has_the_same_rows(self, scenarios):
        assert len({len(df) for df in scenarios.values()}) == 1

    def test_unlabelled_text_is_used_as_the_scenario_name(self, monkeypatch):
        raw = workbook()
        raw.iat[2, 1] = "Reference case"
        monkeypatch.setattr(pd, "read_excel", lambda *a, **k: raw)
        assert list(nc.unpack_northern_connectivity("book.xlsm")) == ["Reference case", "CP2B"]

    def test_wrong_file_type(self):
        with pytest.raises(ValueError, match="requires an excel"):
            nc.unpack_northern_connectivity("flows.csv")

    def test_missing_header_row(self, monkeypatch):
        raw = workbook()
        raw.iat[4, 0] = "Day"
        monkeypatch.setattr(pd, "read_excel", lambda *a, **k: raw)
        with pytest.raises(ValueError, match="Could not find the 'Date' header row"):
            nc.unpack_northern_connectivity("book.xlsx")

    def test_header_row_is_found_case_insensitively(self, monkeypatch):
        raw = workbook()
        raw.iat[4, 0] = " DATE "
        monkeypatch.setattr(pd, "read_excel", lambda *a, **k: raw)
        assert list(nc.unpack_northern_connectivity("book.xlsx")) == ["CP1A", "CP2B"]


class TestStationsAndCleaner:
    def test_site_to_gauge_table(self):
        codes = nc.get_northern_connectivity_codes()
        assert codes["Wilcannia"] == "425008" and codes["Bourke"] == "425003" and codes["Mungindi"] == "416001"
        assert len(codes) == 9
        assert all(g.isdigit() for g in codes.values())

    def test_sites_are_renamed_to_gauges_and_unknown_sites_skipped(self):
        idx = pd.DatetimeIndex(["2000-01-01", "2000-01-02"], name="Date")
        df = pd.DataFrame({"Wilcannia": [1.0, 0.0], "Bourke": [np.nan, 5.0], "Nowhere": [9.0, 9.0]}, index=idx)
        flow, level = nc.cleaner_northern_connectivity(df)
        assert list(flow.columns) == ["425008", "425003"]
        assert flow["425008"].tolist() == [1.0, 0.0]
        assert flow["425003"].isna().tolist() == [True, False]
        assert level.shape == (2, 0) and flow.index.name == level.index.name == "Date"

    def test_output_is_a_copy(self):
        idx = pd.DatetimeIndex(["2000-01-01"], name="Date")
        df = pd.DataFrame({"Wilcannia": [1.0]}, index=idx)
        flow, _ = nc.cleaner_northern_connectivity(df)
        flow.iloc[0, 0] = 99.0
        assert df.iloc[0, 0] == 1.0

    def test_empty_input(self):
        flow, level = nc.cleaner_northern_connectivity(pd.DataFrame(index=pd.DatetimeIndex([], name="Date")))
        assert flow.shape == level.shape == (0, 0)
