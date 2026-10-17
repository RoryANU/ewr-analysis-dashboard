"""Run py_ewr on your own model output files and write a copy of the dashboard with the results embedded.

No server and no command line are needed, so this works from a Databricks notebook (see notebooks/run_your_scenarios.ipynb).

    from embed_scenarios import build_with_scenarios
    build_with_scenarios([{"name": "My option", "path": "/Volumes/.../run.csv", "format": "Standard time-series"}], "dashboard.html")

The file is scanned for every gauge in the review, so one file can cover several SDL units. The output page opens in "Your data"
mode with the scenarios already loaded, flow charts included. It starts from an already built page (index.html), so only py_ewr,
pandas and numpy are needed here.
"""

import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from build_dashboard import (  # noqa: E402
    bake_scenarios,
    read_payload,
    replace_payload,
    review_gauges,
)
from ewr_engine import FORMATS, run_scenario  # noqa: E402


def resolve_path(path):
    """A file path as given in a notebook: /Volumes/..., /Workspace/..., /dbfs/... or dbfs:/..., a relative path or ~/...  Raises a clear error if it is not a file."""
    p = str(path).strip()
    if p.startswith("dbfs:/"):
        p = "/dbfs/" + p[len("dbfs:/") :].lstrip("/")
    f = Path(p).expanduser()
    if not f.is_file():
        near = (
            ", ".join(sorted(x.name for x in f.parent.iterdir())[:8])
            if f.parent.is_dir()
            else "(that folder does not exist)"
        )
        raise FileNotFoundError(
            f"No file at {str(f)!r}. Give the full path of the file (not a folder). Files in {str(f.parent)!r}: {near}"
        )
    return f


def run_file(path, fmt="Standard time-series", gauges=None):
    """py_ewr on one model output file -> (list of [planning unit, gauge, EWR, FR, ETP], stats)."""
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {FORMATS}")
    f = resolve_path(path)
    rows, stats = run_scenario(
        f.read_bytes(), gauges or review_gauges(), None, fmt, f.name
    )
    return [[r["pu"], r["gauge"], r["ewr"], r["fr"], r["et"]] for r in rows], stats


def build_with_scenarios(scenarios, out_path, template=None):
    """scenarios: list of {"path": ..., "format": ..., "name": ... (optional: defaults to the file name)}. Returns the HTML text."""
    template = Path(template or ROOT / "index.html")
    html = template.read_text()
    try:
        payload = read_payload(html)
    except ValueError:
        raise ValueError(
            f"{template} has no embedded data; rebuild it with python build.py"
        ) from None
    for sc in scenarios:
        if (sc.get("format") or "Standard time-series") not in FORMATS:
            raise ValueError(f"format must be one of {FORMATS}")
    resolved = [
        {
            **sc,
            "path": [
                str(resolve_path(p))
                for p in (sc["path"] if isinstance(sc["path"], list) else [sc["path"]])
            ],
        }
        for sc in scenarios
    ]
    payload.update(bake_scenarios(resolved))
    payload["openIn"] = "user"
    html = replace_payload(html, payload)
    out_path = Path(out_path).expanduser()
    try:
        out_path.write_text(html)
    except (
        OSError
    ):  # a folder that cannot be written (e.g. read-only): fall back to the temp folder
        import tempfile

        out_path = Path(tempfile.gettempdir()) / out_path.name
        out_path.write_text(html)
    print("written", out_path, f"({len(html) // 1024} KB)")
    return html
