"""The page's flow engine (src/flow_engine.js, run in V8 through mini-racer) gives the
same results as the Python reference (src/ewr_engine.py) for every flow chart, on
synthetic flows with gaps, offset date ranges and timing windows that wrap the year end.
Skipped when mini-racer is not installed (pip install -r requirements-dev.txt)."""

import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import build_dashboard  # noqa: E402
import ewr_engine as engine  # noqa: E402

try:
    from py_mini_racer import MiniRacer
except ImportError:  # pragma: no cover - optional test dependency
    MiniRacer = None

# Gwydir: EWRs from 5 ML/d baseflow to 19,000 ML/d overbank, windows that wrap the year
G = "416001"


def synthetic(seed, start, end, scale):
    """Daily flows with multi-day events (AR(1) in log space) and a few missing days."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, end)
    x = np.empty(len(idx))
    x[0] = 0.0
    for i in range(1, len(idx)):
        x[i] = 0.9 * x[i - 1] + rng.normal(0, 0.55)
    season = 0.8 * np.sin(2 * np.pi * (idx.dayofyear.to_numpy() - 200) / 365.25)
    flow = np.round(scale * np.exp(6.7 + x + season), 1)
    flow[rng.choice(len(idx), 6, replace=False)] = np.nan  # single missing days
    flow[400:412] = np.nan  # a missing stretch
    return pd.DataFrame({G: flow}, index=idx)


def close(a, b, path="$"):
    """Equal up to float noise (the reference rounds float32; JS uses float64)."""
    if isinstance(a, dict) and isinstance(b, dict):
        assert set(a) == set(b), f"{path}: keys {sorted(set(a) ^ set(b))} differ"
        for k in a:
            close(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list):
        assert len(a) == len(b), f"{path}: length {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b, strict=False)):
            close(x, y, f"{path}[{i}]")
    elif isinstance(a, bool) or isinstance(b, bool):
        assert a == b, f"{path}: {a!r} != {b!r}"
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        assert math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-9), f"{path}: {a!r} != {b!r}"
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


@unittest.skipIf(MiniRacer is None, "mini-racer is not installed")
class FlowEngineParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        engine.FLOWS.clear()
        engine._EVAL_CACHE.clear()
        engine.register_flows("a", synthetic(1, "1990-07-01", "2001-06-30", 1.0))
        engine.register_flows(
            "b", synthetic(2, "1991-01-01", "2002-12-31", 1.3)
        )  # offset: partial first and last years
        payload = {"flows": {}, "ewrs": {}, "ewryears": {}, "regimes": {}}
        for sid in ("a", "b"):
            build_dashboard.add_flow_charts(payload, sid, [G])
        cls.ewrs = payload["ewrs"][G]
        cls.js = MiniRacer()
        cls.js.eval((ROOT / "src/flow_engine.js").read_text())
        cls.js.eval(
            "var fe = FlowEngine.create("
            + json.dumps(engine._json_safe(payload), allow_nan=False)
            + ");"
        )

    @classmethod
    def tearDownClass(cls):
        engine.FLOWS.clear()
        engine._EVAL_CACHE.clear()

    def run_js(self, kind, q):
        return json.loads(
            self.js.eval(f"JSON.stringify(fe.run({json.dumps(kind)}, {json.dumps(q)}))")
        )

    def check(self, kind, fn, q):
        with self.subTest(kind=kind, q=q):
            close(engine._json_safe(fn(dict(q))), self.run_js(kind, q))

    def test_heatmap(self):
        for comp in ({}, {"comp": "b"}):
            for extra in (
                {},
                {"days": "consec"},
                {"sm": "10", "em": "3", "xlim": "5000", "dmax": "40"},
                {"sm": "9", "em": "4", "days": "consec", "mthr": "540", "mdur": "14"},
                {"mthr": "7900", "mdur": "5", "dmax": "10"},
            ):
                self.check(
                    "heatmap",
                    engine.heatmap,
                    {"base": "a", "gauge": G, **comp, **extra},
                )

    def test_curves(self):
        base = {"base": "a", "gauge": G}
        for q in (
            {"kind": "hydro"},
            {"kind": "hydro", "comp": "b"},
            {"kind": "fdc"},
            {"kind": "fdc", "comp": "b", "thr": "540,3000"},
            {"kind": "dfdc", "comp": "b", "thr": "160", "sm": "9", "em": "3"},
            {"kind": "mdfdc", "comp": "b"},
            {"kind": "mdfdc", "comp": "b", "ymax": "2500"},
            {"kind": "mdfdc", "comp": "b", "ymin": "100", "ymax": "900"},
        ):
            self.check("curves", engine.curves, {**base, **q})

    def test_spells_and_regimes(self):
        for comp in ({}, {"comp": "b"}):
            for thr in ("5", "160", "1000"):
                self.check(
                    "spells",
                    engine.spells,
                    {"base": "a", "gauge": G, "thr": thr, **comp},
                )
            self.check("regimes", engine.regimes, {"base": "a", "gauge": G, **comp})

    def test_ewr_years_and_interevents(self):
        self.assertGreater(len(self.ewrs), 10)
        for e in self.ewrs:
            q = {"gauge": G, "ewr": e["code"], "pu": e["pu"]}
            self.check("ewryears", engine.ewr_years, {"base": "a", **q})
            self.check("ewryears", engine.ewr_years, {"base": "a", "comp": "b", **q})
            self.check("interevents", engine.interevents, {"scn": "b", **q})

    def test_ewr_list(self):
        close(
            engine._json_safe({"ewrs": engine.ewr_list(G)}),
            self.run_js("ewrs", {"gauge": G}),
        )

    def test_same_errors(self):
        for kind, fn, q in (
            ("curves", engine.curves, {"base": "a", "gauge": G, "kind": "dfdc"}),
            ("curves", engine.curves, {"base": "a", "gauge": G, "kind": "mdfdc"}),
            ("heatmap", engine.heatmap, {"base": "a", "gauge": "999999"}),
            ("spells", engine.spells, {"base": "nope", "gauge": G, "thr": "1"}),
        ):
            with self.subTest(kind=kind, q=q):
                with self.assertRaises(ValueError) as py:
                    fn(dict(q))
                with self.assertRaises(Exception) as js:
                    self.run_js(kind, q)
                self.assertIn(str(py.exception), str(js.exception))


if __name__ == "__main__":
    unittest.main()
