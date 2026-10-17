"""Build-time packaging for the no-runtime, no-network laptop artifact."""

import base64
import hashlib
import importlib.metadata
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent


def json_text(value):
    return json.dumps(value, separators=(",", ":"), allow_nan=False).replace("<", "\\u003c")


def metadata():
    from server import parameter_sheet

    return {
        "py_ewr": importlib.metadata.version("py_ewr"),
        "parameter_sha256": hashlib.sha256(parameter_sheet().to_csv(index=False).encode()).hexdigest(),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "parameter_hash_format": "parameter_sheet().to_csv(index=False), UTF-8",
    }


def inventory(gauges):
    from flow_regime_mappings import regimes_for
    from server import diagnostic_payload

    data = {"version": 1, "metadata": metadata(), "scenarios": {}, "inventories": {}, "regimes": {}}
    for gauge in gauges:
        inv = diagnostic_payload(gauge)
        data["inventories"][gauge] = inv
        by: dict[str, list[dict[str, Any]]] = {}
        for e in inv["ewrs"]:
            for rg in regimes_for(e["code"], e.get("state") or ""):
                if rg != "excluded":
                    by.setdefault(rg, []).append(e)
        comps = []
        for rg, es in by.items():
            thr = min(e["thr"] for e in es)
            comps.append(
                {
                    "regime": rg,
                    "thr": thr,
                    "codes": [c for c, _ in sorted({(e["code"], e["thr"]) for e in es}, key=lambda x: (x[1], x[0]))],
                    "at_thr": sorted({e["code"] for e in es if e["thr"] == thr}),
                }
            )
        data["regimes"][gauge] = sorted(comps, key=lambda c: c["thr"])
    return data


def encode_flow(series):
    """Lossless float64 delta stream. Keep exact residuals where subtraction loses bits.

    Threshold comparisons must not change due to quantisation. Scale 1 retains the
    source precision; overrides restore the rare non-reversible floating-point delta.
    NaNs have their own sentinel and never update the previous observed value.
    """
    import numpy as np

    values = series.to_numpy(dtype=float)
    deltas = np.empty(len(values), dtype="<f8")
    prev = 0.0
    overrides = {}
    for i, value in enumerate(values):
        if not np.isfinite(value):
            deltas[i] = np.nan
        else:
            deltas[i] = value - prev
            if prev + deltas[i] != value:
                overrides[str(i)] = float(value)
            prev = value
    return {
        "start": int(series.index[0].value // 86_400_000_000_000),
        "count": len(values),
        "scale": 1,
        "encoding": "float64-le-delta",
        "data": base64.b64encode(deltas.tobytes()).decode(),
        "overrides": overrides,
    }


def embed_diagnostics(data, sid, flow_gauges):
    from server import _json_safe, daily_series, ewr_years, interevents

    sc: dict[str, Any] = {"flows": {}, "years": {}, "interevents": {}}
    for gauge in flow_gauges:
        if gauge not in data["inventories"]:
            extra = inventory([gauge])
            data["inventories"].update(extra["inventories"])
            data["regimes"].update(extra["regimes"])
        sc["flows"][gauge] = encode_flow(daily_series(sid, gauge))
        for e in data["inventories"][gauge]["ewrs"]:
            for pu in e.get("pus", [e["pu"]]):
                q = {"base": sid, "scn": sid, "gauge": gauge, "ewr": e["code"], "pu": pu}
                key = json.dumps([gauge, e["code"], pu], separators=(",", ":"))
                # Fail the build rather than ship a silently incomplete diagnostic.
                sc["years"][key] = _json_safe(ewr_years(q))
                sc["interevents"][key] = _json_safe(interevents(q))
    data["scenarios"][sid] = sc


def secure_html(html):
    """Refresh script hashes after every embedding pass. Styles need runtime Plotly/Leaflet attributes."""
    html = re.sub(r'\n?<meta http-equiv="Content-Security-Policy"[^>]*>', "", html)
    hashes = []
    for attrs, body in re.findall(r"<script([^>]*)>(.*?)</script>", html, re.S):
        if "application/json" not in attrs:
            hashes.append("'sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() + "'")
    policy = "default-src 'none'; connect-src 'none'; script-src " + " ".join(hashes)
    policy += "; style-src 'unsafe-inline'; img-src data: blob:; worker-src blob:; font-src data:; base-uri 'none'; form-action 'none'; object-src 'none'"
    return html.replace("<head>", '<head>\n<meta http-equiv="Content-Security-Policy" content="' + policy + '">', 1)


def package_html(html, data):
    manifest = json.loads((HERE / "vendor/manifest.json").read_text())
    for asset in manifest:
        raw = (HERE / "vendor" / asset["file"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != asset["sha256"]:
            raise ValueError("Vendor checksum mismatch: " + asset["file"])
    data["third_party"] = {"manifest": manifest, "licenses": {name: (HERE / "vendor" / name).read_text() for name in ("Leaflet-LICENSE", "Plotly-LICENSE")}}
    css = (HERE / "vendor/leaflet-1.9.4.css").read_text()
    css = re.sub(r"url\([^)]*\)", "none", css)  # div markers and a text layer switch need no image assets
    html = html.replace('<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.css">', "<style>" + css + "</style>")
    for lib, url in [("leaflet-1.9.4.js", "leaflet/1.9.4/leaflet.js"), ("plotly-2.27.0.min.js", "plotly.js/2.27.0/plotly.min.js")]:
        js = (HERE / "vendor" / lib).read_text()
        js = re.sub(r"//# sourceMappingURL=.*", "", js)
        html = html.replace(
            '<script src="https://cdnjs.cloudflare.com/ajax/libs/' + url + '"></script>', "<script>" + js.replace("</script", "<\\/script") + "</script>"
        )
    numerical = (HERE / "offline_numerics.js").read_text()
    adapter = (HERE / "data_access.js").read_text()
    block = '<script type="application/json" id="offline-data">' + json_text(data) + "</script>"
    worker = (
        numerical + "\nonmessage=({data:m})=>{try{postMessage({id:m.id,result:OfflineNumerics.calculate(m.kind,m.q,m.data)});}"
        "catch(e){postMessage({id:m.id,error:e.message});}};"
    )
    block += '<script type="application/json" id="offline-worker">' + json_text(worker) + "</script>"
    block += "<script>" + numerical + "\n" + adapter + "</script>"
    html = html.replace("<!-- OFFLINE_DATA -->", block)
    return secure_html(html)
