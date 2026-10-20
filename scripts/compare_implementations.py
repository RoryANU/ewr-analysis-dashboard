"""Compare slice 1 calculations with untouched definitions from the pre-refactor tag."""

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

SERVER_FUNCTIONS = {
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


def load_old(path: str, names: set[str] | None = None) -> types.ModuleType:
    """Execute a temporary module; server extraction avoids unrelated HTTP/I/O imports."""
    source = subprocess.check_output(["git", "show", f"pre-refactor:{path}"], cwd=ROOT, text=True)
    tree = ast.parse(source, filename=f"pre-refactor:{path}")
    if names is not None:
        definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        if {node.name for node in definitions} != names:
            raise RuntimeError(f"Missing baseline functions in {path}")
        # Original bodies, signatures and operation order are retained; only their
        # stdlib module namespace is supplied, without importing the old server.
        tree.body = [*ast.parse("import math\nfrom typing import Any\n").body, *definitions]
    module = types.ModuleType(f"_pre_refactor_{path.replace('/', '_').replace('.', '_')}")
    exec(compile(tree, f"pre-refactor:{path}", "exec"), module.__dict__)
    return module


def compare_values(old: Any, new: Any) -> tuple[float, float]:
    """Require exact structure, metadata and values; also accumulate numeric deviations."""
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
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            result = function(*copy.deepcopy(args))
            error = None
        except Exception as exc:
            result, error = None, (type(exc), str(exc))
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


def main() -> int:
    old_server = load_old("src/server.py", set(SERVER_FUNCTIONS))
    old_drought = load_old("src/drought_windows.py")
    old_regimes = load_old("src/flow_regime_mappings.py")
    new_drought = importlib.import_module("ewr_analysis.core.calculations.drought_windows")
    new_regimes = importlib.import_module("ewr_analysis.core.calculations.flow_regimes")
    if compare_values(old_drought.DROUGHTS, new_drought.DROUGHTS) != (0.0, 0.0):
        raise AssertionError("DROUGHTS differ")
    if compare_values(old_regimes.flow_mappings, new_regimes.flow_mappings) != (0.0, 0.0):
        raise AssertionError("flow_mappings differ")
    print("DROUGHTS / flow_mappings: exact values and order match pre-refactor")
    failures = []
    for name, inputs in cases().items():
        if name in SERVER_FUNCTIONS:
            old_module = old_server
            new_module = importlib.import_module(f"ewr_analysis.core.calculations.{SERVER_FUNCTIONS[name]}")
        elif name in ("window_dates", "meta"):
            old_module, new_module = old_drought, new_drought
        else:
            old_module, new_module = old_regimes, new_regimes
        absolute = relative = 0.0
        matched_errors = 0
        for index, args in enumerate(inputs):
            try:
                old_value, old_error, old_warnings = invoke(getattr(old_module, name), args)
                new_value, new_error, new_warnings = invoke(getattr(new_module, name), args)
                if old_error != new_error or old_warnings != new_warnings:
                    raise AssertionError(f"Exception/warning mismatch: {old_error}, {old_warnings} != {new_error}, {new_warnings}")
                if old_error is not None:
                    matched_errors += 1
                deviation = compare_values(old_value, new_value)
                absolute, relative = max(absolute, deviation[0]), max(relative, deviation[1])
                if deviation != (0.0, 0.0):
                    failures.append(f"{name} case {index}: abs={deviation[0]}, rel={deviation[1]}")
            except AssertionError as error:
                failures.append(f"{name} case {index}: {error}")
        print(f"{name}: cases={len(inputs)}, max_abs={absolute:g}, max_rel={relative:g}, matched_exceptions={matched_errors}")
    if failures:
        print("FAIL\n" + "\n".join(failures))
        return 1
    print("PASS: all 12 moved functions match pre-refactor exactly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
