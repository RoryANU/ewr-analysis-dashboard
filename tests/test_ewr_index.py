"""The EWR index the page embeds for the EWR guide: built from data/reference/parameter_sheet.csv, and in step with the committed HTML."""

import json
import re
from pathlib import Path

import pandas as pd

import build_dashboard as bd

ROOT = Path(__file__).resolve().parents[1]
SHEET = ROOT / "data/reference/parameter_sheet.csv"


def decoded(idx: dict, key: str, i: int):
    v = idx["cols"][key][i]
    return idx["strs"][v] if v is not None and key in bd.EWR_STR else v


def test_index_has_one_row_per_sheet_row_and_matching_values():
    sheet = pd.read_csv(SHEET, dtype={"Gauge": str}, low_memory=False)
    idx = bd.ewr_index(ROOT)
    assert idx["n"] == len(sheet)
    assert all(len(col) == len(sheet) for col in idx["cols"].values())
    for i in (0, 500, len(sheet) - 1):
        r = sheet.iloc[i]
        assert decoded(idx, "c", i) == r["Code"]
        assert decoded(idx, "g", i) == str(r["Gauge"])
        assert decoded(idx, "pu", i) == r["PlanningUnitName"]
        assert decoded(idx, "st", i) == r["State"]
        assert decoded(idx, "dur", i) == r["Duration"]


def test_start_and_end_days_follow_pyewr():
    """A month such as "9.15" is 15 September; no day means the whole month (start day 1, end day null = the month's last day)."""
    raw = pd.read_csv(SHEET, dtype=str, usecols=["StartMonth", "EndMonth"])
    idx = bd.ewr_index(ROOT)
    for i, (sm, em) in enumerate(zip(raw.StartMonth, raw.EndMonth, strict=True)):
        s_month, _, s_day = sm.partition(".")
        e_month, _, e_day = em.partition(".")
        assert (idx["cols"]["sm"][i], idx["cols"]["sd"][i]) == (int(s_month), int(s_day) if s_day else 1)
        assert (idx["cols"]["em"][i], idx["cols"]["ed"][i]) == (int(e_month), int(e_day) if e_day else None)


def test_committed_html_embeds_the_current_index():
    html = (ROOT / "ewr_analysis_dashboard.html").read_text()
    m = re.search(r"^const EWRIDX=(\{.*\});", html, re.M)
    assert m, "the page has no EWRIDX: run python3 src/build_dashboard.py"
    assert json.loads(m.group(1)) == json.loads(json.dumps(bd.ewr_index(ROOT))), "stale page: run python3 src/build_dashboard.py"


def test_handlers_come_from_the_pyewr_calc_config():
    idx = bd.ewr_index(ROOT)
    handlers = {decoded(idx, "h", i) for i in range(idx["n"])}
    assert {"flow_handle", "lowflow_handle"} <= handlers  # the EWRs the guide has tutorials for
    assert "cumulative_handle" in handlers and None not in handlers  # others exist, and every row was looked up
