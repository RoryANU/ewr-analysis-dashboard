"""Compare the browser numeric engine with the existing Python reference."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import server
from offline_build import encode_flow

ROOT = Path(__file__).resolve().parents[1]
NODE = os.environ.get("NODE_BINARY") or shutil.which("node")


def assert_close(a, b, path=""):
    if isinstance(a, dict):
        assert a.keys() == b.keys(), path
        for k in a:
            assert_close(a[k], b[k], path + "." + k)
    elif isinstance(a, list):
        assert len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            assert_close(x, y, path + f"[{i}]")
    elif isinstance(a, (float, int)) and not isinstance(a, bool):
        assert a == pytest.approx(b, abs=0.0011), path
    else:
        assert a == b, path


@pytest.fixture
def flow_data(monkeypatch):
    dates = pd.date_range("1999-06-20", "2003-07-10")
    v = (np.arange(len(dates)) % 83).astype(float) * 10.13
    # Runs spanning water years, leap day, missing day, partial years.
    v[5:45] = 0
    v[900] = np.nan
    a = pd.DataFrame({"409025": v}, index=dates)
    b = pd.DataFrame({"409025": v * 1.2}, index=dates)
    b.iloc[1000] = np.nan
    monkeypatch.setitem(server.FLOWS, "offline-a", a)
    monkeypatch.setitem(server.FLOWS, "offline-b", b)
    regimes = server.regimes({"base": "offline-a", "gauge": "409025"})["regimes"]
    return {
        "scenarios": {sid: {"flows": {"409025": encode_flow(server.daily_series(sid, "409025"))}} for sid in ("offline-a", "offline-b")},
        "regimes": {"409025": regimes},
    }


@pytest.mark.parametrize(
    "kind,extra",
    [
        ("spells", {"thr": "150"}),
        ("regimes", {}),
        *[("curves", {"kind": k, "thr": "0,200,500", "sm": "11", "em": "3"}) for k in ["hydro", "fdc", "dfdc", "mdfdc"]],
        ("curves", {"kind": "mdfdc", "ymin": "100", "ymax": "700"}),
        ("heatmap", {"dmax": "20", "cols": "40"}),
        ("heatmap", {"days": "consec", "dmax": "35", "sm": "11", "em": "3", "mthr": "150", "mdur": "4"}),
        ("heatmap", {"xmin": "120", "xlim": "600", "dmin": "5", "dmax": "20"}),
        ("heatmap", {"sm": "2", "em": "2", "dmax": "31"}),
    ],
)
@pytest.mark.parametrize("paired", [False, True])
def test_numeric_parity(flow_data, kind, extra, paired):
    if not NODE:
        pytest.skip("Set NODE_BINARY to run Python/JavaScript parity")
    if not paired and extra.get("kind") in ("dfdc", "mdfdc"):
        return
    q = {"base": "offline-a", "gauge": "409025", **extra}
    if paired:
        q["comp"] = "offline-b"
    expected = server._json_safe(getattr(server, kind)(q))
    script = (
        "const fs=require('fs'),n=require('./src/offline_numerics.js');"
        "const m=JSON.parse(fs.readFileSync(0,'utf8'));console.log(JSON.stringify(n.calculate(m.kind,m.q,m.data)));"
    )
    actual = json.loads(subprocess.check_output([NODE, "-e", script], input=json.dumps({"kind": kind, "q": q, "data": flow_data}).encode(), cwd=ROOT))
    assert_close(expected, actual)


def test_encoding_preserves_small_residuals_and_missing_days():
    if not NODE:
        pytest.skip("Node required")
    s = pd.Series([1e12, 0.1, np.nan, 1e-9, 125.123456789], index=pd.date_range("2000-01-01", periods=5))
    script = (
        "const fs=require('fs'),n=require('./src/offline_numerics.js');"
        "console.log(JSON.stringify(Array.from(n.decode(JSON.parse(fs.readFileSync(0,'utf8'))).values)));"
    )
    actual = json.loads(subprocess.check_output([NODE, "-e", script], input=json.dumps(encode_flow(s)).encode(), cwd=ROOT))
    assert actual == [1e12, 0.1, None, 1e-9, 125.123456789]


def test_script_hashes_and_safe_data_block():
    import base64
    import hashlib
    import re

    from offline_build import json_text, secure_html

    value = "</ScRiPt><!--<script>"
    html = '<html><head></head><body><script>const a=1;</script><script type="application/json">' + json_text(value) + "</script></body></html>"
    html = secure_html(html)
    assert value not in html
    assert html.count("</script>") == 2
    match = re.search(r'Content-Security-Policy" content="([^"]+)', html)
    assert match
    policy = match.group(1)
    digest = base64.b64encode(hashlib.sha256(b"const a=1;").digest()).decode()
    assert f"'sha256-{digest}'" in policy
    assert "connect-src 'none'" in policy
    assert secure_html(html) == html


def test_ewr_pairing_intersects_complete_years():
    if not NODE:
        pytest.skip("Node required")
    payload = {
        "scenarios": {
            "base": {"years": {"wy": [1999, 2000, 2001, 2002], "complete": [1, 1, 0, 1], "achieved": [1, 0, 1, 1]}},
            "comp": {"years": {"wy": [2000, 2001, 2002, 2003], "complete": [1, 1, 1, 1], "achieved": [1, 1, 0, 1]}},
        }
    }
    script = "const n=require('./src/offline_numerics.js'),fs=require('fs');console.log(JSON.stringify(n.pair(JSON.parse(fs.readFileSync(0,'utf8')),true)));"
    actual = json.loads(subprocess.check_output([NODE, "-e", script], input=json.dumps(payload).encode(), cwd=ROOT))
    assert actual["paired"] == {"wy": [2000, 2002], "n": 2, "base": 1, "comp": 1, "gained": 1, "lost": 1, "pct_base": 50, "pct_comp": 50}
