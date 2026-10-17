"""Regression tests for server.achievement_frequencies: only fully observed water years form the denominator."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from server import achievement_frequencies as af

T, D = np.array([500.0]), np.array([10])


def run(idx, flow, sm=7, em=6):
    out, n = af(np.asarray(flow, dtype=float).reshape(-1, 1), idx, T, D, sm, em)
    return float(out[0, 0, 0]), n


def test_full_water_years():
    idx = pd.date_range("2000-07-01", "2010-06-30")
    assert run(idx, np.full(len(idx), 1000.0)) == (100.0, 10)


def test_calendar_years_partial_edges_excluded():
    idx = pd.date_range("2001-01-01", "2010-12-31")  # first and last water years are partial
    pct, n = run(idx, np.full(len(idx), 1000.0), sm=7, em=8)
    # WY2001 has no Jul-Aug days (excluded); WY2011 (Jul-Dec 2010) fully covers the Jul-Aug window, so it counts
    assert pct == 100.0 and n == 10
    # full Jul-Jun window: WY2001 and WY2011 are partial and excluded, leaving WY2002-2010
    assert run(idx, np.full(len(idx), 1000.0)) == (100.0, 9)


def test_complete_failing_year_still_counts():
    idx = pd.date_range("2000-07-01", "2010-06-30")
    f = np.full(len(idx), 1000.0)
    f[(idx >= "2003-07-01") & (idx <= "2004-06-30")] = 100.0  # one complete year below threshold
    assert run(idx, f) == (90.0, 10)


def test_all_nan_year_excluded_not_failed():
    idx = pd.date_range("2000-07-01", "2010-06-30")
    f = np.full(len(idx), 1000.0)
    f[(idx >= "2003-07-01") & (idx <= "2004-06-30")] = np.nan
    assert run(idx, f) == (100.0, 9)


def test_partial_range_nov_feb_long_duration_not_suppressed():
    idx = pd.date_range("2000-05-01", "2002-02-28")  # WY2001 and WY2002 hold complete Nov-Feb windows (120 days)
    f = np.full((len(idx), 1), 1000.0)
    for consec in (False, True):
        out, n = af(f, idx, T, np.array([100]), 11, 2, consec)
        assert n == 2 and float(out[0, 0, 0]) == 100.0, consec
    out, _ = af(f, idx, T, np.array([130]), 11, 2)  # longer than the 120-day window: never achievable
    assert float(out[0, 0, 0]) == 0.0


def test_nan_becomes_null_strict_json():
    import json

    from server import _json_safe

    idx = pd.date_range("2000-07-01", "2000-12-31")  # no complete year -> NaN
    out, n = af(np.full((len(idx), 1), 1000.0), idx, T, D, 7, 6)
    assert n == 0 and np.isnan(out).all()
    payload = {"years": n, "base": np.round(out[0].T, 3).tolist(), "mark": [float("nan"), float("inf")]}
    text = json.dumps(_json_safe(payload), allow_nan=False)
    back = json.loads(text, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    assert back["base"] == [[None]] * len(D) and back["mark"] == [None, None]


def test_scenarios_share_the_same_complete_years():
    idx = pd.date_range("2000-07-01", "2010-06-30")
    a, b = np.full(len(idx), 1000.0), np.full(len(idx), 1000.0)
    b[idx == "2005-07-15"] = np.nan  # scenario B misses one Jul-Aug day in WY2006
    a[(idx >= "2002-07-01") & (idx <= "2003-06-30")] = 100.0  # A genuinely fails WY2003
    out, n = af(np.column_stack([a, b]), idx, T, D, 7, 8)
    assert n == 9  # WY2006 dropped for BOTH scenarios
    assert abs(float(out[0, 0, 0]) - 100.0 * 8 / 9) < 1e-4 and float(out[1, 0, 0]) == 100.0  # float32 output


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("ok", k)
