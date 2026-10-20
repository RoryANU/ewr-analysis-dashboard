"""Run py_ewr on your own model output files and write a copy of the dashboard with the results embedded.

No server and no command line are needed, so this works from a Databricks notebook (see notebooks/run_your_scenarios.ipynb).

    from ewr_analysis.embed_scenarios import build_with_scenarios
    build_with_scenarios([{"name": "My option", "path": "/Volumes/.../run.csv", "format": "Standard time-series"}], "dashboard.html")

The file is scanned for every gauge in the review, so one file can cover several SDL units. The output page opens in "Your data" mode
with the scenarios already loaded. The flow charts need a server, so they are not available in this page.
"""

import json
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
from ewr_analysis.server import FORMATS, VAL_KEYS, run_scenario  # the same EWR run as the local server (server.py), imported without starting it

PLACEHOLDER = "const PRELOADED=[];"
REKEY = {"A4260527": "A4260524"}  # same re-keying as build_dashboard.py (not imported: importing it runs the build)


def resolve_path(path):
    """A file path as given in a notebook: /Volumes/..., /Workspace/..., /dbfs/... or dbfs:/..., a relative path or ~/...
    Raises a clear error if it is not a file."""
    text = str(path).strip()
    if text.startswith("dbfs:/"):  # Spark-style URI -> the FUSE mount Python can open
        text = "/dbfs/" + text[len("dbfs:/") :].lstrip("/")
    file = Path(text).expanduser()
    if not file.is_file():
        nearby = ", ".join(sorted(x.name for x in file.parent.iterdir())[:8]) if file.parent.is_dir() else "(that folder does not exist)"
        raise FileNotFoundError(f"No file at {str(file)!r}. Give the full path of the file (not a folder). Files in {str(file.parent)!r}: {nearby}")
    return file


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
    file = resolve_path(path)
    rows, stats = run_scenario(file.read_bytes(), gauges or review_gauges(), None, fmt, file.name)
    return [[r.get(k) for k in VAL_KEYS] for r in rows], stats


def build_with_scenarios(scenarios, out_path, template=None):
    """scenarios: list of {"path": ..., "format": ..., "name": ... (optional: defaults to the file name)}. Returns the HTML text."""
    template = Path(template or ROOT / "ewr_analysis_dashboard.html")
    html = template.read_text()
    if PLACEHOLDER not in html:
        raise ValueError(f"{template} has no embedded-scenarios slot; rebuild it with src/ewr_analysis/build_dashboard.py")
    gauges, preloaded, used_names = review_gauges(), [], set()
    for scenario in scenarios:
        name = (scenario.get("name") or resolve_path(scenario["path"]).stem)[:40]
        if name in used_names:
            raise ValueError(f"two scenarios are called {name!r}; give each a different name")
        used_names.add(name)
        fmt = scenario.get("format", "Standard time-series")
        vals, stats = run_file(scenario["path"], fmt, gauges)
        print(
            f"{name}: {len(vals)} EWR results, {stats['gauges_in_file']} gauges found in the file"
            + (f", could not evaluate {sorted(stats['skipped'])}" if stats.get("skipped") else "")
        )
        preloaded.append({"name": name, "fmt": fmt, "vals": vals})
    # escape "</" so no value can close the page's <script> element
    html = html.replace(PLACEHOLDER, "const PRELOADED=" + json.dumps(preloaded, separators=(",", ":")).replace("</", "<\\/") + ";")
    out_path = Path(out_path).expanduser()
    try:
        out_path.write_text(html)
    except OSError:  # a folder that cannot be written (e.g. read-only): fall back to the temp folder
        out_path = Path(tempfile.gettempdir()) / out_path.name
        out_path.write_text(html)
    print("written", out_path, f"({len(html) // 1024} KB)")
    return html
