"""Independent slice 3 answers; README display rules exercise the unchanged JS."""

import json
import subprocess
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pandas as pd
import pytest

from ewr_analysis import build_dashboard as bd
from ewr_analysis.core.calculations.assessment_years import record_coverage
from ewr_analysis.core.calculations.event_gaps import ewr_gaps
from ewr_analysis.core.calculations.ewr_parameters import LOW_FLOW_HANDLERS, _dispatch_key, _is_multigauge, ewr_params
from ewr_analysis.core.calculations.frequency_ratio import (
    CLIMATES,
    NS,
    POLICIES,
    _assessment_year_stats,
    _group_by_ewr,
    _json_safe,
    build_row_table,
    detect_variant_conflicts,
    fix_theme,
    objective_themes,
    objectives_of,
    scenario_index,
)
from ewr_analysis.paths import ROOT
from scripts.compare_implementations import BUILD_CONSTANTS, BUILD_FUNCTIONS, load_old


@pytest.mark.parametrize(
    ("value", "expected"), [("123", True), (" 123 ", True), (" ", False), ("", False), (None, False), (np.nan, False), (123, False), (["123"], False)]
)
def test_is_multigauge_string_membership_only(value, expected):
    # A nonblank string names a partner; numeric/non-string values never do.
    assert _is_multigauge(value) is expected


@pytest.mark.parametrize("multi", [False, True])
@pytest.mark.parametrize(("kind", "suffix"), [("F", "F"), ("L", "L"), ("V", "V"), (None, "None"), (np.nan, "nan"), (0, "0"), ("unexpected", "unexpected")])
def test_dispatch_key_unvalidated_kind(multi, kind, suffix):
    # Dispatch keys concatenate code, one of two literal labels, and str(kind).
    # None/NaN/unexpected kinds are formatted rather than validated or defaulted.
    label = "multigauge" if multi else "single"
    assert _dispatch_key("BF1", multi, kind) == f"BF1-{label}-{suffix}"
    assert LOW_FLOW_HANDLERS == ("lowflow_handle", "lowflow_handle_multi")


@pytest.mark.parametrize(
    ("policy", "climate", "expected"),
    [
        ("Without Development", "Historical Climate", 0),
        ("Pre Basin Plan", "Historical Climate", 7),
        ("Basin plan full implementation", "Hotter and Much Drier 2.0 C", 27),
    ],
)
def test_scenario_index_policy_major(policy, climate, expected):
    # Four policy blocks contain seven climates each: first=0, second=7, last=27.
    assert scenario_index(policy, climate) == expected
    assert NS == 28 and len(POLICIES) == 4 and len(CLIMATES) == 7


def test_variant_conflict_and_empty_frame():
    frame = pd.DataFrame({"PlanningUnitName": ["p"] * 4, "SWSDLName": ["s"] * 4, "Gauge": ["001"] * 4, "EWR": ["BF1"] * 4, "FR": [0.5, np.nan, 0.75, 0.5]})
    # One key has two distinct observations; NaN and repeated .5 add no variant.
    assert detect_variant_conflicts(frame, bd.KEY, "FR") == [{"PlanningUnitName": "p", "SWSDLName": "s", "Gauge": "001", "EWR": "BF1", "values": [0.5, 0.75]}]
    assert detect_variant_conflicts(frame.iloc[:0], bd.KEY, "FR") == []


def test_objective_themes_priority_tie_and_reference():
    fr = pd.DataFrame({"EcoObj": ["A-1", "A-1", "B-1"], "Target": ["Waterbirds", "Native Fish", "Other species"]})
    et = pd.DataFrame({"EcoObj": ["A-1"], "Target": ["Other Species"]})
    ref = pd.DataFrame({"Target": ["Waterbirds", None]}, index=["C-1", "D-1"])
    # The tied FR mode picks lexically first Native Fish; FR precedes ETP.
    # Missing results use the reference, and non-string/missing reference is unclassified.
    assert objective_themes(fr, et, ["A-1", "B-1", "C-1", "D-1", "E-1"], ref) == {
        "A-1": "Native Fish",
        "B-1": "Other Species",
        "C-1": "Waterbirds",
        "D-1": "Unclassified",
        "E-1": "Unclassified",
    }
    assert fix_theme("Other species") == "Other Species"
    assert fix_theme("other species") == "other species"
    assert fix_theme(cast(Any, None)) is None
    # Codes sort by prefix then complete code, not by numeric suffix.
    assert objectives_of({("p", "s", "g", "x"): {"ob": {"B-1", "A-2", "A-10"}}}) == ["A-10", "A-2", "B-1"]


def test_ewr_params_first_row_coercion_and_empty():
    frame = pd.DataFrame(
        {
            "Gauge": ["001", "001"],
            "Code": ["BF1", "BF1"],
            "PlanningUnitName": ["p", "p"],
            "TargetFrequency": [50, 80],
            "Duration": [2.9, 9],
            "MaxInter-event": [1.23456, 9],
        }
    )
    # First duplicated key wins; int(2.9)=2, MRIP rounds to four places.
    assert ewr_params(frame) == {("001", "BF1", "p"): {"T": 50.0, "dur": 2, "mrip": 1.2346, "kind": "F"}}
    assert ewr_params(frame[["Gauge", "Code", "PlanningUnitName"]]) == {("001", "BF1", "p"): {"T": None, "dur": None, "mrip": None, "kind": "F"}}
    assert ewr_params(None) == {} and ewr_params(frame.iloc[:0]) == {}


def test_group_by_ewr_real_summary_columns():
    frame = pd.DataFrame({"Gauge": [123, 123, 456], "PlanningUnit": ["p", "p", "q"], "Code": ["BF1", "BF1", "LF1"], "interEventLength": [3, 7, np.nan]})
    # Two groups, with gauge IDs stringified; rows and their original indices stay aligned.
    grouped = _group_by_ewr(frame)
    assert list(grouped) == [("123", "p", "BF1"), ("456", "q", "LF1")]
    pd.testing.assert_frame_equal(grouped[("123", "p", "BF1")], frame.iloc[:2])
    assert _group_by_ewr(frame.iloc[:0]) == {} and _group_by_ewr(pd.DataFrame()) == {}


def test_json_safe_tuple_and_numpy_scalar_oddity():
    # JSON cannot carry nonfinite Python floats; tuples become ordinary lists.
    assert _json_safe({"fr": np.nan, "et": (np.inf, -np.inf, 0.25)}) == {"fr": None, "et": [None, None, 0.25]}
    # np.float32 is not a Python float and is deliberately left untouched.
    value = np.float32(np.nan)
    assert _json_safe(value) is value


def test_record_coverage_absent_day_nan_and_empty():
    flow = pd.Series([1.0, np.nan, 1.0], index=pd.to_datetime(["2000-02-28", "2000-03-01", "2000-03-02"]))
    # Feb29 is absent and Mar1 is NaN: two missing dates in a four-day span.
    assert record_coverage(flow, [1, 0]) == {"start": "2000-02-28", "end": "2000-03-02", "missing_days": 2, "complete_years": 1, "years": 2}
    assert record_coverage(flow.iloc[:0], []) == {"start": None, "end": None, "missing_days": 0, "complete_years": 0, "years": 0}


def test_assessment_year_stats_fr_etp_independent_arithmetic():
    params = SimpleNamespace(StartMonth=7, EndMonth=6, FlowLevelVolume="F", Multigauge=None, Duration=4, EventsPerYear=1)
    yearly = pd.DataFrame({"BF1_eventYears": [1], "BF1_maxEventDays": [2]}, index=[1999])
    flow = pd.Series(1.0, index=pd.date_range("1999-07-01", "2000-06-30"))
    gaps = [{"start": "1999-07-01", "end": "1999-07-10", "days": 10, "censored": False}]
    result = _assessment_year_stats("BF1", params, yearly, {}, flow, gaps, 8, 50, {}, False)
    # README.md opening FR/ETP definitions and server.py module docstring:
    # FR=achieved frequency/target frequency, ETP=L/record days. This complete
    # leap year achieved 1/1 (100%): FR=100/50=2, kept RAW here. Ten gap days
    # with MRIP8 give L=2, so window ETP=2/366 -> .0055; severity=10/8=1.25.
    # Event duration 2/required4=.5 gives one near miss out of one failure.
    assert result["dr"] == 0.5 and result["nm"] == [1, 1]
    assert result["win"]["mil"] == {"n": 1, "fr": 2.0, "et": 0.0055, "sev": 1.25, "dr": 0.5}


def test_etp_fraction_and_percent_of_record():
    flow = pd.Series(1.0, index=pd.date_range("2000-01-01", periods=10))
    # README.md opening ETP definition and server.py module docstring: no success
    # gives one 10-day gap. MRIP8 leaves 2 excess days: 2/10=.2, displayed as 20%.
    gaps, etp = ewr_gaps({"flow": flow, "mrip_days": 8, "inter": pd.DataFrame(), "succ": pd.DataFrame()})
    assert gaps[0]["excess"] == 2 and etp == 0.2
    assert etp * 100 == 20


def test_all_nan_raw_group_and_row_table_alias_contract():
    fr = pd.DataFrame(
        {
            "PlanningUnitName": ["p"],
            "SWSDLName": ["s"],
            "Gauge": ["g"],
            "EWR": ["BF1"],
            "EcoObj": ["O-1"],
            "FlowRegime": ["Baseflow"],
            "scn": [0],
            "FR": [np.nan],
            "EventYears": [np.nan],
            "AverageEventLength": [np.nan],
            "TotalDays": [np.nan],
        }
    )
    et = pd.DataFrame(columns=bd.KEY + ["EcoObj", "State", "scn", "ETP"])
    # aggregate_ewr_rows remains outside core because of its mutating helper.
    # An observed all-NaN mean is NaN, not the absent-scenario None sentinel.
    rows = bd.aggregate_ewr_rows(fr, et)
    values = rows[("p", "s", "g", "BF1")]
    assert np.isnan(values["fr"][0]) and values["fr"][1] is None
    assert values["ey"][0] is None and values["ny"] is None
    table, lookups = build_row_table(rows, {"O-1": "Native Fish"})
    assert lookups["gids"] == ["g"] and table[0][3] == "BF1"
    # The existing compact table reuses the row's metric lists, without editing them.
    assert table[0][6] is values["fr"]
    assert build_row_table({}, {})[0] == [] and bd.aggregate_ewr_rows(fr.iloc[:0], et) == {}


NODE_BRIDGE = r"""
const fs=require('node:fs'), vm=require('node:vm');
const input=JSON.parse(process.argv[1]), text=fs.readFileSync(input.template,'utf8');
const from='// ---- values and averages', to='// ---- colours';
for(const marker of [from,to]) if(text.split(marker).length!==2) throw Error('ambiguous section marker');
const core=text.slice(text.indexOf(from),text.indexOf(to));
const ctx=vm.createContext({ROWS:input.rows,settings:input.settings,levels:input.levels});
vm.runInContext(`const META={climates:['c0','c1'],policies:['Pre Basin Plan','Basin plan full implementation']};
 const NC=2; const S=Object.assign({thr:1,ethr:0,fbthr:.5,band:2,mode:'single',pol:1,cli:0,bpol:0,bcli:0,metric:'fr',sel:{type:'none'}},settings);`,ctx);
vm.runInContext(core,ctx);
console.log(JSON.stringify(vm.runInContext('({aggregate:agg(ROWS,levels,S.metric,2),counts:cnts(ROWS,S.metric),classes:ROWS.map(r=>diffClass(r,S.metric))})',ctx)));
"""


def display_answer(rows, settings=None, levels=None):
    # Use the same source section/VM isolation as tests/test_dashboard_core.js.
    # No browser/DOM or JS source edits; expected answers below are hand-derived.
    request = {"template": str(ROOT / "src/ewr_analysis/dashboard_template.html"), "rows": rows, "settings": settings or {}, "levels": levels or []}
    return json.loads(subprocess.check_output(["node", "-e", NODE_BRIDGE, json.dumps(request, allow_nan=False)], text=True))


def display_row(base, comp, et_base=0, et_comp=0):
    return [
        0,
        0,
        0,
        "BF1",
        [],
        [],
        [base, None, comp, None],
        [et_base, None, et_comp, None],
        {"T": 50, "ny": 100},
        [None if base is None else base * 50, None, None if comp is None else comp * 50, None],
    ]


@pytest.mark.parametrize(("achieved", "expected"), [(40, 0.8), (80, 1.0)])
def test_readme_single_ewr_fr_and_cap(achieved, expected):
    # README.md opening paragraph and server.py module docstring: raw FR is
    # achieved/target frequency. Target50 gives .8 or1.6; the single-scenario
    # display mean caps each EWR at1, hence .8 and1. No Python cap is invented.
    result = display_answer([display_row(0, achieved / 50)])
    assert result["aggregate"] == {"n": 1, "mean": expected}


def test_readme_surplus_does_not_offset_shortfall_single_gauge():
    # README.md opening: cap EACH EWR before means. At one gauge, 80/50=1.6
    # and20/50=.4 give (1+.4)/2=.7, rather than the uncapped mean1.
    result = display_answer([display_row(0, 1.6), display_row(0, 0.4)], levels=["gauge"])
    assert result["aggregate"] == {"n": 2, "mean": 0.7}
    assert display_answer([display_row(None, None)], levels=["gauge"])["aggregate"] == {"n": 0, "mean": None}


@pytest.mark.parametrize(("years", "up", "same"), [(2, 1, 0), (1, 0, 1), (1.999, 0, 1)])
def test_readme_differential_change_band_boundary(years, up, same):
    # README.md differential paragraph: at least2 achieved years changes class.
    # Target50,100-year record: FR .5 means25 achieved; comparison25+delta.
    # Both remain below target so the target-crossing exception cannot apply.
    result = display_answer([display_row(0.5, (25 + years) / 50)], {"mode": "diff"})
    assert result["aggregate"]["mean"] == pytest.approx(years)
    assert result["counts"] == {"up": up, "dn": 0, "same": same, "met": 0, "gain": 0, "loss": 0, "n": 1}


def test_readme_differential_met_in_both_and_etp_year_band():
    # README.md differential paragraph: FR>=1 in both scenarios is counted
    # separately and omitted from the mean, even with a growing surplus.
    result = display_answer([display_row(1.2, 1.5)], {"mode": "diff"})
    assert result["aggregate"] == {"n": 0, "mean": None}
    assert result["counts"] == {"up": 0, "dn": 0, "same": 0, "met": 1, "gain": 0, "loss": 0, "n": 0}
    # ETP .02 -> .01 improves by .01*100=1 year of this100-year record:
    # exactly the separate ETP band in README.md. Both exceed the zero target.
    result = display_answer([display_row(0, 0, 0.02, 0.01)], {"mode": "diff", "metric": "et"})
    assert result["counts"]["up"] == 1
    assert result["aggregate"]["mean"] == 0.01


def test_mini_payload_before_and_after_moves(monkeypatch):
    # Re-run the same I/O orchestration with original pre-refactor calculation
    # globals, then core shims. Inputs and all payload/HTML expectations stay fixed.
    old = load_old("src/build_dashboard.py", set(BUILD_FUNCTIONS), BUILD_CONSTANTS)
    mini = ROOT / "tests/fixtures/mini"
    with monkeypatch.context() as before:
        for name in BUILD_FUNCTIONS.keys() | BUILD_CONSTANTS:
            before.setattr(bd, name, getattr(old, name))
        expected = bd.build_payload(mini)
    actual = bd.build_payload(mini)
    assert actual == expected
    template = (ROOT / "src/ewr_analysis/dashboard_template.html").read_text()
    assert bd.render_html(template, actual) == bd.render_html(template, expected)
    # Strict JSON exercises every slot, not just the row table.
    assert json.dumps(actual, allow_nan=False) == json.dumps(expected, allow_nan=False)
