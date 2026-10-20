"""Compare slices 1–3 calculations with untouched definitions from the pre-refactor tag."""

import ast
import copy
import importlib
import math
import subprocess
import types
import warnings
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from ewr_analysis.paths import ROOT

BUILD_FUNCTIONS = {
    "dr_approx": "duration_ratio",
    "detect_variant_conflicts": "frequency_ratio",
    "scenario_index": "frequency_ratio",
    "fix_theme": "frequency_ratio",
    "objective_themes": "frequency_ratio",
    "objectives_of": "frequency_ratio",
    "build_row_table": "frequency_ratio",
    "ewr_params": "ewr_parameters",
}
BUILD_CONSTANTS = {"POLICIES", "CLIMATES", "NS"}

SERVER_FUNCTIONS = {
    "_is_multigauge": "ewr_parameters",
    "_dispatch_key": "ewr_parameters",
    "_group_by_ewr": "frequency_ratio",
    "_json_safe": "frequency_ratio",
    "_assessment_year_stats": "frequency_ratio",
    "record_coverage": "assessment_years",
    "_iso_date": "assessment_years",
    "assessment_start": "assessment_years",
    "_timing_mask": "assessment_years",
    "complete_years": "assessment_years",
    "year_summary": "assessment_years",
    "window_metrics": "assessment_years",
    "achievement_frequencies": "assessment_years",
    "ewr_gaps": "event_gaps",
    "gap_severity": "event_gaps",
    "_dateless_gap_stats": "event_gaps",
    "_runs_by_year": "event_gaps",
    "_has_any_event": "event_gaps",
    "dr_years": "duration_ratio",
    "vr_years": "duration_ratio",
    "_paired_achievement": "paired_comparison",
    "mcnemar_p": "paired_comparison",
    "exceed_pct": "flow_duration_curves",
    "nice_ceiling": "axis_limits",
    "_mrip_days": "event_gaps",
    "record_days": "assessment_years",
    "_feb29": "assessment_years",
    "_water_year": "assessment_years",
    "_water_year_days": "assessment_years",
    "_window_days": "assessment_years",
}


def load_old(path: str, names: set[str] | None = None, constants: set[str] | None = None) -> types.ModuleType:
    """Execute a temporary module; server extraction avoids unrelated HTTP/I/O imports."""
    source = subprocess.check_output(["git", "show", f"pre-refactor:{path}"], cwd=ROOT, text=True)
    tree = ast.parse(source, filename=f"pre-refactor:{path}")
    if names is not None:
        definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        if {node.name for node in definitions} != names:
            raise RuntimeError(f"Missing baseline functions in {path}")
        assignments = [
            node for node in tree.body if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in (constants or set())
        ]
        if {node.targets[0].id for node in assignments if isinstance(node.targets[0], ast.Name)} != (constants or set()):
            raise RuntimeError(f"Missing baseline constants in {path}")
        # Bodies and constant expressions remain untouched; supply their import
        # namespace without importing the old I/O/HTTP modules.
        tree.body = [*ast.parse("import math\nfrom typing import Any\nimport pandas as pd\n").body, *assignments, *definitions]
    module = types.ModuleType(f"_pre_refactor_{path.replace('/', '_').replace('.', '_')}")
    exec(compile(tree, f"pre-refactor:{path}", "exec"), module.__dict__)
    return module


def compare_values(old: Any, new: Any) -> tuple[float, float]:
    """Require exact structure, metadata and values; also accumulate numeric deviations."""
    if old is pd.NaT or old is pd.NA:
        if old is not new:
            raise AssertionError("Missing-value sentinels differ")
        return 0.0, 0.0
    if isinstance(old, pd.DataFrame):
        pd.testing.assert_index_equal(old.index, new.index, exact=True)
        pd.testing.assert_index_equal(old.columns, new.columns, exact=True)
        if not old.dtypes.equals(new.dtypes):
            raise AssertionError("DataFrame dtypes differ")
        return compare_values(old.to_numpy(), new.to_numpy())
    if isinstance(old, pd.Index):
        pd.testing.assert_index_equal(old, new, exact=True)
        if getattr(old, "freq", None) != getattr(new, "freq", None):
            raise AssertionError("Index frequency differs")
        return 0.0, 0.0
    if isinstance(old, pd.Series):
        pd.testing.assert_index_equal(old.index, new.index, exact=True)
        if old.dtype != new.dtype or old.name != new.name or getattr(old.index, "freq", None) != getattr(new.index, "freq", None):
            raise AssertionError("Series metadata differ")
        return compare_values(old.tolist(), new.tolist())
    if isinstance(old, np.ndarray):
        if old.shape != new.shape or old.dtype != new.dtype:
            raise AssertionError("Array shape/dtype differ")
        return compare_values(old.tolist(), new.tolist())
    if type(old) is not type(new):
        raise AssertionError(f"Types differ: {type(old)} != {type(new)}")
    if isinstance(old, dict):
        if list(old) != list(new):
            raise AssertionError("Dictionary keys/order differ")
        pairs = [compare_values(value, new[key]) for key, value in old.items()]
    elif isinstance(old, (list, tuple)):
        if len(old) != len(new):
            raise AssertionError("Lengths differ")
        pairs = [compare_values(a, b) for a, b in zip(old, new, strict=True)]
    elif isinstance(old, (int, float, np.number)):
        if math.isnan(float(old)) and math.isnan(float(new)):
            return 0.0, 0.0
        if old == new:
            return 0.0, 0.0
        if not math.isfinite(float(old)) or not math.isfinite(float(new)):
            return math.inf, math.inf
        absolute = abs(float(old) - float(new))
        relative = absolute / abs(float(old)) if old else math.inf
        return absolute, relative
    else:
        if old != new:
            raise AssertionError(f"Values differ: {old!r} != {new!r}")
        return 0.0, 0.0
    return max((a for a, _ in pairs), default=0.0), max((r for _, r in pairs), default=0.0)


def invoke(function: Any, args: tuple) -> tuple[Any, Any, list]:
    arguments = copy.deepcopy(args)
    before = copy.deepcopy(arguments)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            result = function(*arguments)
            error = None
        except Exception as exc:
            result, error = None, (type(exc), str(exc))
    try:
        if compare_values(before, arguments) != (0.0, 0.0):
            raise AssertionError("numeric values changed")
    except AssertionError as exc:
        raise AssertionError(f"{function.__name__} mutated its inputs: {exc}") from exc
    return result, error, [(item.category, str(item.message)) for item in caught]


def cases() -> dict[str, list[tuple]]:
    dates = [date(1900, 6, 30), date(1900, 7, 1), date(2000, 2, 29), date(2000, 6, 30), date(2000, 7, 1), date(2024, 12, 31)]
    series = [
        None,
        pd.Series([1.0, 2.0]),
        pd.Series([], index=pd.DatetimeIndex([]), dtype=float),
        pd.Series([np.nan, np.nan], index=pd.date_range("2020-01-01", periods=2)),
        pd.Series([np.nan, 2.0, 4.0, np.nan], index=pd.to_datetime(["2020-02-27", "2020-02-28", "2020-03-01", "2020-03-02"]), name="flow"),
        pd.Series([1.0], index=pd.to_datetime(["2020-02-29"])),
        pd.Series([1.0, 3.0], index=pd.to_datetime(["2020-02-28", "2020-03-01"], utc=True)),
    ]
    return {
        "mcnemar_p": [(g, lost) for g in range(9) for lost in range(9)] + [(20, 0), (0, 20), (100, 100)],
        "exceed_pct": [
            (np.array(values, dtype=float), threshold)
            for values in ([], [0], [-5, 0, 0, 2, 8], [1, 1, 1])
            for threshold in (-5.0, 0.0, 1.0, 8.0, np.array([]), np.array([-np.inf, -5, 0, 1, 8, np.inf, np.nan]))
        ],
        "nice_ceiling": [(x,) for x in (None, -math.inf, -10, 0, 0.01, 0.999, 1, 1.01, 9.9, 10, 10.01, 99.01, 100, 101, 1234, 9999, math.nan, math.inf)],
        "_mrip_days": [(x,) for x in (-1, 0, 0.1, 0.3, 0.5, 1, 2, 2.5, math.nan)],
        "record_days": [(s,) for s in series],
        "_feb29": [(year,) for year in (1599, 1899, 1900, 1999, 2000, 2003, 2099, 2399)],
        "_water_year": [(d,) for d in dates] + [(pd.Timestamp("2020-07-01", tz="Australia/Sydney"),)],
        "_water_year_days": [(year,) for year in (1599, 1899, 1900, 1999, 2000, 2003, 2099, 2399)],
        "_window_days": [(year, start, end) for year in (1900, 2000, 2001, 2024) for start, end in ((7, 6), (1, 12), (12, 2), (2, 2), (6, 6), (7, 9))],
        "window_dates": [(key, month) for key in ("fed", "ww2", "mil", "tin") for month in (1, 6, 7, 12)] + [("mil",)],
        "meta": [()],
        "regimes_for": [
            (code, state)
            for state in ("QLD", "NSW", "VIC", "ACT", "SA", "WA", "")
            for code in ("", "ZZ9", "FD1", "BF1", "BF-FD1", "OB-WL", "OB-L1", "CLLMM1a", "bf1")
        ],
    }


def slice_two_cases() -> dict[str, list[tuple]]:
    """In-memory daily records, incomplete years, equality and window edges."""
    leap = pd.Series(10.0, index=pd.date_range("1999-07-01", "2000-06-30"), name="flow")
    empty = leap.iloc[:0]
    single = leap.iloc[:1]
    missing = leap.copy()
    missing.loc["2000-02-29"] = np.nan
    short = pd.Series([0.0, 10.0, 10.0, np.nan, 10.0, 10.0, 0.0], index=pd.date_range("2000-02-26", periods=7))
    boundary = pd.Series([0.0, 10.0, 10.0, 0.0], index=pd.date_range("2000-06-29", periods=4))
    key = ("g", "pu", "e")
    years = {"wy": [1998, 1999, 2000], "complete": [1, 1, 0], "achieved": [0, 1, 1], "dr": [0.5, 1.0, None], "ystart": 7}
    gaps = [
        {"start": "1999-06-25", "end": "1999-07-05", "days": 11, "censored": False, "boundary": False, "excess": 8},
        {"start": "2000-06-29", "end": "2000-07-02", "days": 4, "censored": False, "boundary": True, "excess": 1},
        {"start": "2000-02-28", "end": "2000-03-01", "days": 3, "censored": True, "boundary": False, "excess": None},
    ]
    yearly = pd.DataFrame({"maxEventDays": [0, 3, 6, np.nan], "totalEventDays": [6, 9, 12, np.nan]}, index=[1998, 1999, 2000, 2001])
    by_date = yearly.set_axis(pd.date_range("1998-07-01", periods=4, freq="YS-JUL"))
    events = {1999: [[1] * 6, [1] * 3, []], 2000: [[1] * 9]}
    inter = pd.DataFrame({"startDate": ["2000-02-26", "2000-02-29"], "endDate": ["2000-02-28", "2000-03-03"]}, index=pd.date_range("2000-02-26", periods=2))
    out: dict[str, list[tuple]] = {
        "_iso_date": [
            (value,)
            for value in (
                date(2000, 2, 29),
                pd.Timestamp("2024-07-01T00:30:00+10:00"),
                "2000-02-29",
                0,
                86_400_000_000_000,
                -1,
                None,
                pd.NaT,
                "",
                "not-a-date",
                "now",
                "today",
            )
        ],
        "assessment_start": [(sm, em) for sm in (1, 6, 7, 12) for em in (1, 6, 7, 12)],
        "_timing_mask": [
            (s.index, sm, em, sd, ed)
            for s in (leap, empty, short, boundary)
            for sm, em, sd, ed in ((7, 6, None, None), (2, 3, 28, 1), (12, 2, 31, 29), (2, 2, 29, 28), (7, 7, 0, 0))
        ],
        "complete_years": [
            (s, wys, ys, sm, em, sd, ed)
            for s in (leap, missing, empty, single, short)
            for wys, ys, sm, em, sd, ed in (
                ([1999], 7, 7, 6, None, None),
                ([1999, 2000], 7, 2, 3, 28, 1),
                ([], 7, 7, 6, None, None),
                ([2000], 1, 1, 12, None, None),
            )
        ],
        "year_summary": [([], []), ([None], [1]), ([0, 0.5, 1, 2, None], [1, 1, 1, 0, 1]), ([np.nan, 0.5], [1, 1]), ([1], []), ([0.4999, 0.9999], [1, 1])],
        "window_metrics": [
            (years, gs, mrip, tgt, w0, w1, rec)
            for gs in ([], gaps, gaps[:1], gaps[2:])
            for mrip, tgt, w0, w1, rec in (
                (3, 50, "1999-07-01", "2000-06-30", None),
                (3, 50, "1999-07-01", "2000-06-30", ("1999-08-01", "2000-05-31")),
                (0, None, "2000-01-01", "2000-01-01", None),
                (3, 0, "2002-01-01", "2002-12-31", None),
                (3, "", "1999-07-01", "2000-06-29", None),
            )
        ],
        "achievement_frequencies": [
            (np.column_stack([s.to_numpy(), s.to_numpy()]), s.index, np.array([0.0, 10.0, 11.0]), np.array([1, 2, 29, 366, 367]), sm, em, consecutive)
            for s in (leap, missing, empty, single, short)
            for sm, em in ((7, 6), (2, 2), (12, 2))
            for consecutive in (False, True)
        ],
        "ewr_gaps": [
            ({"flow": s, "mrip_days": mrip, "inter": frame, "succ": succ},)
            for s in (leap, short, empty, single)
            for mrip in (0, 3)
            for frame, succ in ((inter.iloc[:0], inter.iloc[:0]), (inter, inter.iloc[:1]), (inter.iloc[:0], inter.iloc[:1]))
        ],
        "gap_severity": [(gs, mrip) for gs in ([], gaps, gaps[:1], gaps[2:]) for mrip in (0, 3, 11)],
        "_dateless_gap_stats": [
            (key, total, mrip, inters, successes)
            for total in (0, 10, np.nan)
            for mrip in (0, 3)
            for inters, successes in (
                ({}, {}),
                ({key: pd.DataFrame({"interEventLength": [3, 7, np.nan, "bad"]}, index=pd.date_range("2000-01-01", periods=4))}, {key: pd.DataFrame()}),
                ({}, {key: pd.DataFrame()}),
            )
        ],
        "_runs_by_year": [(s, s.to_numpy() >= threshold) for s in (empty, single, leap, missing, short, boundary) for threshold in (0.0, 10.0, 11.0)],
        "_has_any_event": [
            ({},),
            ({"g": {"pu": {"e": {1999: []}}}},),
            ({"g": {"pu": {"e": {1999: [[]]}}}},),
            ({"g": {"pu": {"e": {1999: [[(date(1999, 7, 1), 10)]]}}}},),
        ],
        "dr_years": [
            (frame, code, dur, low, per_year, ev)
            for frame, code in ((yearly, ""), (yearly.add_prefix("e_"), "e"), (by_date, ""), (yearly.iloc[:0], ""))
            for dur, low, per_year, ev in (
                (3, False, 1, None),
                (3, True, 2, events),
                (3, False, 2, events),
                (0, False, 1, None),
                (None, False, 1, None),
                (3, False, "2.0", None),
            )
        ],
        "vr_years": [
            (s, [1999, 2000], 7, sm, em, acc, vol, sd, ed)
            for s in (leap, missing, short, empty)
            for sm, em, acc, vol, sd, ed in (
                (7, 6, 2, 20, None, None),
                (2, 3, 2, 20, 28, 1),
                (7, 6, 1, 0, None, None),
                (7, 6, None, 20, None, None),
                (7, 6, 2.5, 20, None, None),
            )
        ],
        "dr_approx": [
            (values, params)
            for values in ([], [None], [0, 1.5, 3, 9, None, np.nan])
            for params in ({"dur": 3}, {"dur": 0}, {"dur": None}, {"dur": 3, "kind": "V"}, {"dur": -3}, {"dur": np.nan}, {"dur": "3"})
        ],
        "_paired_achievement": [
            (base, comp)
            for base, comp in (
                (years, years),
                (years, {"wy": [2000, 1999, 1998], "complete": [1, 1, 1], "achieved": [1, 0, 1]}),
                ({"wy": [], "complete": [], "achieved": []}, years),
                ({"wy": [1999, 1999], "complete": [1, 1], "achieved": [0, 2]}, years),
            )
        ],
    }
    # Missingness in only one scenario must still remove the year for BOTH.
    out["achievement_frequencies"].append((np.column_stack([leap.to_numpy(), missing.to_numpy()]), leap.index, np.array([10.0]), np.array([1]), 7, 6, False))
    return out


def slice_three_cases() -> dict[str, list[tuple]]:
    """Project-column frames and read-only inputs for aggregation/parameter helpers."""
    index = pd.date_range("2000-01-01", periods=4)
    frame = pd.DataFrame(
        {
            "PlanningUnitName": ["p", "p", "p", "q"],
            "SWSDLName": ["s"] * 4,
            "Gauge": ["g"] * 4,
            "EWR": ["BF1"] * 4,
            "FR": [0.5, 0.5, 0.75, np.nan],
            "ETP": [0.1, 0.2, np.nan, np.nan],
            "EcoObj": ["O-1", "O-1", "O-2", None],
            "Target": ["Native Fish", "Other species", "Other species", None],
        },
        index=index,
    )
    rows = {
        ("p", "s", "g", "BF1"): {
            "ob": {"O-1", "O-2"},
            "rg": {"Baseflow"},
            "fr": [0.5, 2.0] + [None] * 26,
            "et": [0.1, 0.0] + [None] * 26,
            "ey": [25, 50] + [None] * 26,
            "ael": [3.0, 9.0] + [None] * 26,
            "ny": 100.0,
        }
    }
    theme = {"O-1": "Native Fish", "O-2": "Other Species"}
    ref = pd.DataFrame({"Target": ["Waterbirds", "Native Fish"]}, index=["O-1", "O-3"])
    sheet = pd.DataFrame(
        {
            "Gauge": ["g", "g", "g", None],
            "Code": ["BF1", "BF1", "BF1", "X"],
            "PlanningUnitName": ["p", "p", "q", "p"],
            "TargetFrequency": [50, 90, "bad", None],
            "Duration": [3, 9, 2.9, None],
            "MaxInter-event": [1.23456, 9, None, None],
            "FlowLevelVolume": ["F", "V", None, "unexpected"],
        },
        index=index,
    )
    events = pd.DataFrame(
        {
            "Gauge": [123, 123, "g", None],
            "PlanningUnit": ["p", "p", "q", "p"],
            "Code": ["BF1", "BF1", "X", "X"],
            "startDate": pd.date_range("2000-01-01", periods=4),
            "interEventLength": [3, np.nan, 5, 0],
        },
        index=index,
    )
    daily = pd.Series(10.0, index=pd.date_range("1999-07-01", "2000-06-30"))
    yearly = pd.DataFrame({"BF1_eventYears": [1], "BF1_maxEventDays": [3], "BF1_totalEventDays": [366]}, index=[1999])
    params = {
        "StartMonth": 7,
        "EndMonth": 6,
        "StartDay": 1,
        "EndDay": 30,
        "FlowLevelVolume": "F",
        "Multigauge": None,
        "Duration": 3,
        "EventsPerYear": 1,
        "AccumulationPeriod": 2,
        "VolumeThreshold": 20,
    }
    gaps = [{"start": "1999-07-01", "end": "1999-07-05", "days": 5, "censored": False}]
    stats: list[tuple[Any, ...]] = []
    for kind in ("F", "V", "L", None):
        for low in (False, True):
            for use_days in (False, True):
                prm = types.SimpleNamespace(**(params | {"FlowLevelVolume": kind}))
                stats.append(
                    ("BF1", prm, yearly, {1999: [[1] * 3]}, daily, gaps, 3, 50, {f"BF1-single-{kind}": "lowflow_handle" if low else "flow_handle"}, use_days)
                )
    stats.append(("BF1", types.SimpleNamespace(**params), yearly, {}, daily.iloc[:0], [], 0, None, {}, False))
    # Plain namespaces have identity equality, so use namedtuple rows as real
    # parameter-sheet itertuples() does; structural input checks can compare them.
    stats = [(a, next(pd.DataFrame([vars(b)]).itertuples(index=False)), *rest) for a, b, *rest in stats]
    json_inputs: list[Any] = [None, np.nan, np.inf, -np.inf, (1, np.nan), {"fr": np.nan, "et": [0, np.inf, None]}, np.float32(np.nan), {"ewrs": []}, frame]
    return {
        "detect_variant_conflicts": [
            (f, keys, value)
            for f in (frame, frame.iloc[:0], frame.assign(FR=np.nan))
            for keys in (["PlanningUnitName", "SWSDLName", "Gauge", "EWR"], ["Gauge"])
            for value in ("FR", "ETP")
        ],
        "scenario_index": [
            (policy, climate)
            for policy in ("Without Development", "Basin plan full implementation", None, "unknown")
            for climate in ("Historical Climate", "Hotter and Much Drier 2.0 C", "unknown")
        ],
        "fix_theme": [(value,) for value in ("Other species", "Other Species", "Native Fish", "", None, np.nan)],
        "objective_themes": [
            (a, b, objs, ref)
            for a, b in ((frame, frame.iloc[:0]), (frame.iloc[:0], frame), (frame.iloc[:0], frame.iloc[:0]))
            for objs in (["O-1", "O-2", "O-3", "unknown"], [])
        ],
        "objectives_of": [(rows,), ({},), ({("p", "s", "g", "X"): {"ob": {"V-2", "A-9", "A-1"}}},)],
        "build_row_table": [
            (r, themes, p)
            for r, themes in ((rows, theme), ({}, {}))
            for p in (None, {}, {("g", "BF1", "p"): {"T": 50, "dur": 3, "mrip": 1, "kind": "F"}}, {("g", "BF1", "p"): {"dur": 3, "kind": "V"}})
        ],
        "ewr_params": [
            (f,)
            for f in (
                None,
                sheet,
                sheet.iloc[:0],
                sheet.drop(columns=["FlowLevelVolume", "Duration"]),
                sheet[["Gauge", "Code", "PlanningUnitName"]],
                pd.DataFrame(),
            )
        ],
        "_is_multigauge": [(value,) for value in ("g", "  g  ", " ", "", None, np.nan, 0, 123, ["g"])],
        "_dispatch_key": [("BF1", multi, kind) for multi in (False, True) for kind in ("F", "L", "V", None, np.nan, "unexpected", 0, {"kind": "F"})],
        "_group_by_ewr": [(f,) for f in (events, events.iloc[:0], pd.DataFrame(), events.assign(Gauge=np.nan))],
        "_json_safe": [(value,) for value in json_inputs],
        "record_coverage": [
            (s, complete)
            for s in (daily, daily.iloc[:0], daily.drop(pd.Timestamp("2000-02-29")), daily * np.nan, daily.iloc[:1], daily.tz_localize("UTC"))
            for complete in ([1, 0], [])
        ],
        "_assessment_year_stats": stats,
    }


def main() -> int:
    old_server = load_old("src/server.py", set(SERVER_FUNCTIONS), {"LOW_FLOW_HANDLERS"})
    old_build = load_old("src/build_dashboard.py", set(BUILD_FUNCTIONS), BUILD_CONSTANTS)
    old_drought = load_old("src/drought_windows.py")
    old_server.__dict__.update(DROUGHTS=old_drought.DROUGHTS, window_dates=old_drought.window_dates)
    old_regimes = load_old("src/flow_regime_mappings.py")
    new_drought = importlib.import_module("ewr_analysis.core.calculations.drought_windows")
    new_regimes = importlib.import_module("ewr_analysis.core.calculations.flow_regimes")
    if compare_values(old_drought.DROUGHTS, new_drought.DROUGHTS) != (0.0, 0.0):
        raise AssertionError("DROUGHTS differ")
    if compare_values(old_regimes.flow_mappings, new_regimes.flow_mappings) != (0.0, 0.0):
        raise AssertionError("flow_mappings differ")
    print("DROUGHTS / flow_mappings: exact values and order match pre-refactor")
    for name in BUILD_CONSTANTS:
        assert compare_values(getattr(old_build, name), getattr(importlib.import_module("ewr_analysis.core.calculations.frequency_ratio"), name)) == (
            0.0,
            0.0,
        ), name
    assert compare_values(old_server.LOW_FLOW_HANDLERS, importlib.import_module("ewr_analysis.core.calculations.ewr_parameters").LOW_FLOW_HANDLERS) == (
        0.0,
        0.0,
    )
    print("POLICIES / CLIMATES / NS / LOW_FLOW_HANDLERS: exact values and order match pre-refactor")
    failures = []
    for name, inputs in (cases() | slice_two_cases() | slice_three_cases()).items():
        if name in SERVER_FUNCTIONS:
            old_module = old_server
            new_module = importlib.import_module(f"ewr_analysis.core.calculations.{SERVER_FUNCTIONS[name]}")
        elif name in BUILD_FUNCTIONS:
            old_module = old_build
            new_module = importlib.import_module(f"ewr_analysis.core.calculations.{BUILD_FUNCTIONS[name]}")
        elif name in ("window_dates", "meta"):
            old_module, new_module = old_drought, new_drought
        else:
            old_module, new_module = old_regimes, new_regimes
        absolute = relative = 0.0
        matched_errors = relative_acceptances = 0
        for index, args in enumerate(inputs):
            try:
                old_value, old_error, old_warnings = invoke(getattr(old_module, name), args)
                new_value, new_error, new_warnings = invoke(getattr(new_module, name), args)
                if old_error != new_error or old_warnings != new_warnings:
                    raise AssertionError(f"Exception/warning mismatch: {old_error}, {old_warnings} != {new_error}, {new_warnings}")
                # These accepted pandas keywords read the wall clock. Per the
                # explicit migration exception, compare acceptance, NEVER values.
                if name == "_iso_date" and isinstance(args[0], str) and args[0] in ("now", "today"):
                    if old_error is not None or new_error is not None:
                        raise AssertionError("Relative date must be accepted by both implementations")
                    relative_acceptances += 1
                    continue
                if old_error is not None:
                    matched_errors += 1
                deviation = compare_values(old_value, new_value)
                absolute, relative = max(absolute, deviation[0]), max(relative, deviation[1])
                if deviation != (0.0, 0.0):
                    failures.append(f"{name} case {index}: abs={deviation[0]}, rel={deviation[1]}")
            except AssertionError as error:
                failures.append(f"{name} case {index}: {error}")
        print(
            f"{name}: cases={len(inputs)}, max_abs={absolute:g}, max_rel={relative:g}, "
            f"matched_exceptions={matched_errors}, clock_acceptance_only={relative_acceptances}"
        )
    if failures:
        print("FAIL\n" + "\n".join(failures))
        return 1
    print("PASS: all 41 moved functions match pre-refactor exactly for deterministic inputs; 2 relative-date cases check acceptance only; all inputs unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
