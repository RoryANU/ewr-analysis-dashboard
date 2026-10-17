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
from server import FORMATS, run_scenario  # noqa: E402  (the same EWR run as the local server (server.py), imported without starting it)

PLACEHOLDER = "const PRELOADED=[];"
REKEY = {"A4260527": "A4260524"}  # same re-keying as build_dashboard.py (not imported: importing it runs the build)


def resolve_path(path):
    """A file path as given in a notebook: /Volumes/..., /Workspace/..., /dbfs/... or dbfs:/..., a relative path or ~/...
    Raises a clear error if it is not a file."""
    p = str(path).strip()
    if p.startswith("dbfs:/"):
        p = "/dbfs/" + p[len("dbfs:/") :].lstrip("/")
    f = Path(p).expanduser()
    if not f.is_file():
        near = ", ".join(sorted(x.name for x in f.parent.iterdir())[:8]) if f.parent.is_dir() else "(that folder does not exist)"
        raise FileNotFoundError(f"No file at {str(f)!r}. Give the full path of the file (not a folder). Files in {str(f.parent)!r}: {near}")
    return f


def review_gauges():
    """Every gauge in the review results, FR and ETP (some gauges have ETP results only): a scenario file is scanned for all
    of them. Both the ids as given in the results and as the page keys them (REKEY) are included."""
    import pandas as pd

    gauges = set()
    for name in ("frequency_ratio.csv", "etp.csv"):
        d = pd.read_csv(ROOT / "data/results" / name, dtype=str, usecols=lambda c: c.strip() == "Gauge")
        gauges |= set(d.iloc[:, 0].dropna().str.strip())
    return sorted(gauges | {REKEY.get(g, g) for g in gauges})


def run_file(path, fmt="Standard time-series", gauges=None):
    """py_ewr on one model output file -> (list of [planning unit, gauge, EWR, FR, ETP], stats)."""
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {FORMATS}")
    f = resolve_path(path)
    rows, stats = run_scenario(f.read_bytes(), gauges or review_gauges(), None, fmt, f.name)
    return [[r["pu"], r["gauge"], r["ewr"], r["fr"], r["et"]] for r in rows], stats


def build_with_scenarios(scenarios, out_path, template=None):
    """scenarios: list of {"path": ..., "format": ..., "name": ... (optional: defaults to the file name)}. Returns the HTML text."""
    template = Path(template or ROOT / "ewr_analysis_dashboard.html")
    html = template.read_text()
    if PLACEHOLDER not in html:
        raise ValueError(f"{template} has no embedded-scenarios slot; rebuild it with src/build_dashboard.py")
    gauges, out, used = review_gauges(), [], set()
    for sc in scenarios:
        name = (sc.get("name") or resolve_path(sc["path"]).stem)[:40]
        if name in used:
            raise ValueError(f"two scenarios are called {name!r}; give each a different name")
        used.add(name)
        vals, stats = run_file(sc["path"], sc.get("format", "Standard time-series"), gauges)
        print(
            f"{name}: {len(vals)} EWR results, {stats['gauges_in_file']} gauges found in the file"
            + (f", could not evaluate {sorted(stats['skipped'])}" if stats.get("skipped") else "")
        )
        out.append({"name": name, "fmt": sc.get("format", "Standard time-series"), "vals": vals})
    html = html.replace(PLACEHOLDER, "const PRELOADED=" + json.dumps(out, separators=(",", ":")).replace("</", "<\\/") + ";")
    out_path = Path(out_path).expanduser()
    try:
        out_path.write_text(html)
    except OSError:  # a folder that cannot be written (e.g. read-only): fall back to the temp folder
        import tempfile

        out_path = Path(tempfile.gettempdir()) / out_path.name
        out_path.write_text(html)
    print("written", out_path, f"({len(html) // 1024} KB)")
    return html
