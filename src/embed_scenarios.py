"""Run py_ewr on your own model output files and write a copy of the dashboard with the results embedded.

No server and no command line are needed, so this works from a Databricks notebook (see notebooks/run_your_scenarios.ipynb).

    from embed_scenarios import build_with_scenarios
    build_with_scenarios([{"name": "My option", "path": "/Volumes/.../run.csv", "format": "Standard time-series"}], "dashboard.html")

The file is scanned for every gauge in the review, so one file can cover several SDL units. The output page opens in "Your data" mode
with the scenarios already loaded. The flow charts need a server, so they are not available in this page.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from server import FORMATS, run_scenario        # the same EWR run as the local server (server.py), imported without starting it

PLACEHOLDER = "const PRELOADED=[];"


def review_gauges():
    import pandas as pd
    return sorted(pd.read_csv(ROOT / "data/results/frequency_ratio.csv", dtype={"Gauge": str}, usecols=["Gauge"]).Gauge.dropna().unique())


def run_file(path, fmt="Standard time-series", gauges=None):
    """py_ewr on one model output file -> (list of [planning unit, gauge, EWR, FR, ETP], stats)."""
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {FORMATS}")
    body = Path(path).read_bytes()
    rows, stats = run_scenario(body, gauges or review_gauges(), None, fmt, Path(path).name)
    return [[r["pu"], r["gauge"], r["ewr"], r["fr"], r["et"]] for r in rows], stats


def build_with_scenarios(scenarios, out_path, template=None):
    """scenarios: list of {"path": ..., "format": ..., "name": ... (optional: defaults to the file name)}. Returns the HTML text."""
    template = Path(template or ROOT / "ewr_analysis_dashboard.html")
    html = template.read_text()
    if PLACEHOLDER not in html:
        raise ValueError(f"{template} has no embedded-scenarios slot; rebuild it with src/build_dashboard.py")
    gauges, out, used = review_gauges(), [], set()
    for sc in scenarios:
        name = (sc.get("name") or Path(sc["path"]).stem)[:40]
        if name in used:
            raise ValueError(f"two scenarios are called {name!r}; give each a different name")
        used.add(name)
        vals, stats = run_file(sc["path"], sc.get("format", "Standard time-series"), gauges)
        print(f"{name}: {len(vals)} EWR results, {stats['gauges_in_file']} gauges found in the file"
              + (f", could not evaluate {sorted(stats['skipped'])}" if stats.get("skipped") else ""))
        out.append({"name": name, "fmt": sc.get("format", "Standard time-series"), "vals": vals})
    html = html.replace(PLACEHOLDER, "const PRELOADED=" + json.dumps(out, separators=(",", ":")).replace("</", "<\\/") + ";")
    Path(out_path).write_text(html)
    print("written", out_path, f"({len(html) // 1024} KB)")
    return html
