"""Build index.html: one self-contained page of the 2026 Basin Plan review EWR results that opens by double-click (file://).

Run from the repository root:  python build.py   (or python3 src/build_dashboard.py; see build.py --help for the options)

Everything the page needs is computed here and written into the file: the review results, the map layers, the chart and map
libraries (vendor/, inlined) and any scenarios of your own (scenarios.json): py_ewr is run on each scenario file at BUILD time,
and its daily flows and per-EWR results are embedded so the flow charts are drawn in the browser (src/flow_engine.js). The page
makes no requests to a server; only the background map tiles come from the internet (the page says so when they cannot load).

Only two raw metrics are carried, no ratings, scaling or scores:
  * FR  - frequency ratio per EWR (achieved / target frequency)        data/results/frequency_ratio.csv
  * ETP - MRIP exceedance time proportion per EWR                      data/results/etp.csv
The page averages them (plain mean over EWRs) by SDL unit, planning unit, gauge, theme, flow regime or EWR, for any
policy x climate scenario. Nothing is scaled between Pre Basin Plan and Without Development; no likelihood / theme / grade
logic is included. Gauge positions: BoM Water Data Online station list.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent  # src/
ROOT = HERE.parent  # repository root; inputs are in data/, the page is written here
sys.path.insert(0, str(HERE))
from flow_regime_mappings import regimes_for  # noqa: E402

norm = lambda s: "".join(c for c in s.lower() if c.isalnum())

POLICIES = [
    "Without Development",
    "Pre Basin Plan",
    "Basin plan 2024",
    "Basin plan full implementation",
]
CLIMATES = [
    "Historical Climate",
    "Warmer and Wetter 1.5 C",
    "Warmer and Drier 1.5 C",
    "Warmer and Much Drier 1.5 C",
    "Hotter and Wetter 2.0 C",
    "Hotter and Drier 2.0 C",
    "Hotter and Much Drier 2.0 C",
]
NS = len(POLICIES) * len(CLIMATES)
# The model outputs report the lake-level EWRs under A4260527 (Tauwitchere); the parameter sheet puts them on A4260524 (Milang).
REKEY = {"A4260527": "A4260524"}
# Virtual gauge with no BoM station: plotted beside a real one
PROXY_LOC = {"A4261002": ("A4260527", 0.012, -0.012)}
PROXY_NAME = {
    "A4261002": "Barrages near the Murray Mouth (total barrage flow, virtual gauge; position approximate)"
}
KEY = ["PlanningUnitName", "SWSDLName", "Gauge", "EWR"]

OUT = ROOT / "index.html"
TEMPLATE = HERE / "dashboard_template.html"
SCENARIOS_FILE = ROOT / "scenarios.json"
# Libraries inlined into the page (copies of the cdnjs files, kept in vendor/ so the build needs no internet). sha256 checked.
VENDOR = {
    "__LIB_LEAFLET_CSS__": (
        "leaflet.css",
        "a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6",
    ),
    "__LIB_LEAFLET_JS__": (
        "leaflet.js",
        "db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a",
    ),
    "__LIB_PLOTLY_JS__": (
        "plotly.min.js",
        "7f4930eba8f8541dbec28dca5bd5f787f8eef1cde0369ac9657b70bed230b3e0",
    ),
}
PAYLOAD_TAG = '<script id="ewr-payload" type="application/json">'


def detect_variant_conflicts(df, key_columns, value_column):
    """Return groups keyed by key_columns that contain multiple distinct non-null metric values."""
    conflicts = []
    for key, group in df.groupby(key_columns, dropna=False, sort=False):
        values = sorted(group[value_column].dropna().unique().tolist())
        if len(values) > 1:
            key = key if isinstance(key, tuple) else (key,)
            conflicts.append(
                {**dict(zip(key_columns, key, strict=False)), "values": values}
            )
    return conflicts


def review_payload():
    """The review's results and map layers (data/), as embedded in the page."""
    from shapely.geometry import mapping, shape

    fr = pd.read_csv(
        ROOT / "data/results/frequency_ratio.csv",
        dtype={"Gauge": str},
        usecols=KEY
        + ["EcoObj", "Target", "FlowRegime", "Policy", "ClimateModel", "FR"],
    )
    et = pd.read_csv(
        ROOT / "data/results/etp.csv",
        dtype={"Gauge": str},
        usecols=lambda c: c.strip()
        in set(KEY + ["EcoObj", "Target", "Policy", "ClimateModel", "ETP", "State"]),
    )
    et.columns = et.columns.str.strip()
    for d in (fr, et):
        d["Gauge"] = d.Gauge.replace(REKEY)
        d["Target"] = d.Target.fillna("Unclassified").replace(
            {"Other species": "Other Species"}
        )
        d["scn"] = d.Policy.map(POLICIES.index) * len(CLIMATES) + d.ClimateModel.map(
            CLIMATES.index
        )
        assert d.scn.notna().all()

    # Report source variants without changing the existing aggregation or writing an additional artifact.
    variant_keys = KEY + ["Policy", "ClimateModel"]
    for metric, data in (("FR", fr), ("ETP", et)):
        conflicts = detect_variant_conflicts(data, variant_keys, metric)
        print(
            f"{metric}: {len(conflicts)} variant conflict groups; aggregation remains the existing mean."
        )
        for conflict in conflicts[:10]:
            key = {name: conflict[name] for name in KEY}
            scenario = (conflict["Policy"], conflict["ClimateModel"])
            print(
                f"  key={key}, scenario={scenario}, distinct {metric} values={conflict['values']}"
            )

    # one row per (planning unit, SDL unit, gauge, EWR): both files repeat each EWR once per ecological objective
    rows = {}

    def row(k):
        return rows.setdefault(
            k,
            {
                "ob": set(),
                "rg": set(),
                "fr": [None] * NS,
                "et": [None] * NS,
                "state": None,
            },
        )

    for k, v in fr.groupby(KEY):
        r = row(k)
        r["ob"] |= set(v.EcoObj.dropna())
        r["rg"] |= set(v.FlowRegime.dropna())
        for s, x in v.groupby("scn").FR.mean().items():
            r["fr"][int(s)] = round(float(x), 4)
    for k, v in et.groupby(KEY):
        r = row(k)
        r["ob"] |= set(v.EcoObj.dropna())
        r["state"] = v.State.dropna().iat[0] if v.State.notna().any() else None
        for s, x in v.groupby("scn").ETP.mean().items():
            r["et"][int(s)] = round(float(x), 4)
    for k, r in rows.items():
        if not r[
            "rg"
        ]:  # ETP-only EWR: use the MDBA pipeline's EWR-code -> flow regime rule
            r["rg"] = set(regimes_for(k[3], r["state"]))
        if not r["rg"]:
            r["rg"] = {"Unmapped"}

    pus = sorted({k[0] for k in rows})
    sdls = sorted({k[1] for k in rows})
    gids = sorted({k[2] for k in rows})
    # objective -> theme (the model outputs' Target, else the parameter metadata reference) and objective text
    ref = (
        pd.read_csv(ROOT / "data/reference/objective_reference.csv", dtype=str)
        .drop_duplicates("EcoObj")
        .set_index("EcoObj")
    )
    fix = lambda t: "Other Species" if t == "Other species" else t
    obj_theme = {}
    for d in (fr, et):
        for o, t in (
            d.dropna(subset=["EcoObj", "Target"])
            .groupby("EcoObj")
            .Target.agg(lambda x: x.mode().iat[0])
            .items()
        ):
            obj_theme.setdefault(o, fix(t))
    objs = sorted(
        {o for r in rows.values() for o in r["ob"]}, key=lambda o: (o.split("-")[0], o)
    )
    for o in objs:
        if o not in obj_theme:
            obj_theme[o] = (
                fix(ref.at[o, "Target"])
                if o in ref.index and isinstance(ref.at[o, "Target"], str)
                else "Unclassified"
            )
    themes = [
        "Flows & Connectivity",
        "Ecosystem Functions",
        "Waterbirds",
        "Native Fish",
        "Native Vegetation",
        "Other Species",
        "Unclassified",
    ]
    themes += sorted(set(obj_theme.values()) - set(themes))
    regimes = sorted({g for r in rows.values() for g in r["rg"]})
    ROWS = [
        [
            pus.index(k[0]),
            sdls.index(k[1]),
            gids.index(k[2]),
            k[3],
            sorted(objs.index(o) for o in r["ob"]),
            sorted(regimes.index(g) for g in r["rg"]),
            r["fr"],
            r["et"],
        ]
        for k, r in sorted(rows.items())
    ]
    print(
        len(ROWS),
        "EWR rows;",
        len(sdls),
        "SDL units;",
        len(pus),
        "planning units;",
        len(gids),
        "gauges",
    )

    # --- gauges ---------------------------------------------------------------------------------------------------------
    bom = pd.read_csv(ROOT / "data/map/bom_gauge_stations.csv", dtype=str)
    for c in ("station_latitude", "station_longitude"):
        bom[c] = pd.to_numeric(bom[c], errors="coerce")
    bom = (
        bom.dropna(subset=["station_latitude"])
        .drop_duplicates("station_no")
        .set_index("station_no")
    )
    # constraint sites (data/map/constraint_sites.csv): points drawn on the map; the file is hand-maintained
    CONSTRAINTS = [
        {
            "name": r.name,
            "river": r.river,
            "lat": float(r.lat),
            "lon": float(r.lon),
            "site": "" if pd.isna(r.site) else r.site,
            "cur": r.current_ml_d,
            "rel": r.relaxed_ml_d,
        }
        for r in pd.read_csv(ROOT / "data/map/constraint_sites.csv").itertuples()
    ]
    GAUGES, unplotted = [], []
    for gid in gids:
        if gid in bom.index:
            lat, lon, name = (
                bom.at[gid, "station_latitude"],
                bom.at[gid, "station_longitude"],
                bom.at[gid, "station_name"],
            )
        elif gid in PROXY_LOC:
            ref_id, dlat, dlon = PROXY_LOC[gid]
            lat, lon = (
                bom.at[ref_id, "station_latitude"] + dlat,
                bom.at[ref_id, "station_longitude"] + dlon,
            )
            name = PROXY_NAME[gid]
        else:
            unplotted.append(gid)
            continue
        if not (-45 < lat < -9 and 110 < lon < 155):
            unplotted.append(gid)
            continue
        GAUGES.append(
            {
                "i": gids.index(gid),
                "id": gid,
                "name": name,
                "lat": round(float(lat), 5),
                "lon": round(float(lon), 5),
            }
        )
    print(len(GAUGES), "gauges plotted; unplotted (in tables only):", unplotted)

    # --- SDL unit polygons ----------------------------------------------------------------------------------------------
    gj = json.load(open(ROOT / "data/map/sdl_resource_units.geojson"))
    by_norm = {norm(n): i for i, n in enumerate(sdls)}
    for f in gj["features"]:
        p = f["properties"]
        f["properties"] = {
            "name": p["SWSDLName"],
            "id": p["SWSDLID"],
            "state": p["StateID"],
            "sdl": by_norm.get(norm(p["SWSDLName"]), -1),
        }
        f["geometry"] = mapping(
            shape(f["geometry"]).simplify(0.005, preserve_topology=True)
        )
    print(
        "SDL names in results with no polygon:",
        [
            n
            for n in sdls
            if norm(n) not in {norm(f["properties"]["name"]) for f in gj["features"]}
        ],
    )

    META = {
        "policies": POLICIES,
        "climates": CLIMATES,
        "pus": pus,
        "sdls": sdls,
        "gauges": gids,
        "themes": themes,
        "regimes": regimes,
        "objs": objs,
        "objTheme": [themes.index(obj_theme[o]) for o in objs],
        "objDesc": [
            ref.at[o, "Objective"]
            if o in ref.index and isinstance(ref.at[o, "Objective"], str)
            else ""
            for o in objs
        ],
    }
    return {
        "data": gj,
        "gauges": GAUGES,
        "rows": ROWS,
        "rivers": json.load(open(ROOT / "data/map/rivers.geojson")),
        "constraints": CONSTRAINTS,
        "meta": META,
    }


def review_gauges():
    """Every gauge in the review results: a scenario file is scanned for all of them."""
    return sorted(
        pd.read_csv(
            ROOT / "data/results/frequency_ratio.csv",
            dtype={"Gauge": str},
            usecols=["Gauge"],
        )
        .Gauge.dropna()
        .unique()
    )


# one EWR's definition, as returned by ewr_engine.ewr_years next to each scenario's results
EWR_DEF_KEYS = (
    "pu",
    "thr",
    "thr_max",
    "dur",
    "min_spell",
    "per_year",
    "gap_tol",
    "sm",
    "em",
    "ystart",
    "target",
    "mrip_days",
)


def columns(records):
    """[{a: 1, b: 2}, {a: 3, b: 4}] -> {"cols": ["a", "b"], "rows": [[1, 2], [3, 4]]} (src/flow_engine.js turns it back)."""
    cols = list(records[0]) if records else []
    return {"cols": cols, "rows": [[rec[c] for c in cols] for rec in records]}


def add_flow_charts(out, sid, flow_gauges):
    """Add what the flow charts need for scenario sid (its flows already kept by ewr_engine.register_flows) to out: the daily
    flows, and per gauge the EWR list, each EWR's py_ewr results (ewr_years) and the flow regime thresholds."""
    import ewr_engine as eng

    df = eng.load_flows(sid)[flow_gauges]
    df = df.reindex(
        pd.date_range(df.index.min(), df.index.max())
    )  # one value per calendar day; a missing day is null
    out["flows"][sid] = {
        "start": df.index[0].strftime("%Y-%m-%d"),
        "gauges": {
            g: [None if pd.isna(v) else float(v) for v in df[g]] for g in flow_gauges
        },
    }
    for g in flow_gauges:
        ewrs = out["ewrs"].setdefault(g, eng.ewr_list(g))
        out["regimes"].setdefault(g, eng.regime_components(g))
        per = out["ewryears"].setdefault(sid, {}).setdefault(g, {})
        for e in ewrs:
            try:
                r = eng.ewr_years(
                    {"base": sid, "gauge": g, "ewr": e["code"], "pu": e["pu"]}
                )
                sc_out = dict(r["scenarios"]["base"])
                for k in (
                    "events",
                    "gaps",
                ):  # columns, not one dict per event: about half the size
                    sc_out[k] = columns(sc_out[k])
                per[f"{e['code']}|{e['pu']}"] = {
                    "def": {k: r[k] for k in EWR_DEF_KEYS},
                    "scenario": sc_out,
                }
            except (
                Exception
            ) as ex:  # shown in the page where that EWR's charts would be
                per[f"{e['code']}|{e['pu']}"] = {"error": f"{type(ex).__name__}: {ex}"}


def bake_scenarios(scenarios, base_dir=ROOT, gauges=None):
    """Run py_ewr on each scenario's file(s) now, at build time, and return what the page needs to show them without a server:
        scenarios  [{id, name, fmt, vals: [[planning unit, gauge, EWR, FR, ETP], ...], flowGauges}]
        flows      {id: {start, gauges: {gauge: [daily flow or null, ...]}}}      for the flow charts (src/flow_engine.js)
        ewrs       {gauge: [EWR, ...]}                                             ewr_engine.ewr_list
        ewryears   {id: {gauge: {"code|planning unit": {def, scenario} or {error}}}}  ewr_engine.ewr_years for one scenario
        regimes    {gauge: [flow regime component, ...]}                           ewr_engine.regime_components
    scenarios: [{"name": ..., "path": file or [files], "format": ... (default Standard time-series), "sub": ... (Northern
    connectivity only)}]; relative paths are taken from base_dir. Several files in one scenario are merged (a later file's EWR
    results and gauges replace an earlier one's)."""
    import ewr_engine as eng

    gauges = list(gauges or review_gauges())
    out = {"scenarios": [], "flows": {}, "ewrs": {}, "ewryears": {}, "regimes": {}}
    used = set()
    for i, sc in enumerate(scenarios):
        paths = sc["path"] if isinstance(sc["path"], list) else [sc["path"]]
        paths = [
            p if Path(p).is_absolute() else Path(base_dir) / p for p in map(Path, paths)
        ]
        fmt = sc.get("format") or "Standard time-series"
        name = str(sc.get("name") or paths[0].stem)[:40]
        if name in used:
            raise ValueError(
                f"two scenarios are called {name!r}; give each a different name"
            )
        used.add(name)
        sid, vals, flow_gauges = f"s{i}", {}, set()
        for k, path in enumerate(paths):
            if not path.is_file():
                raise FileNotFoundError(f"scenario {name!r}: no file at {str(path)!r}")
            rows, stats = eng.run_scenario(
                path.read_bytes(),
                gauges,
                sid,
                fmt,
                path.name,
                sc.get("sub", ""),
                merge=k > 0,
            )
            for r in rows:
                vals[(r["pu"], r["gauge"], r["ewr"])] = [
                    r["pu"],
                    r["gauge"],
                    r["ewr"],
                    r["fr"],
                    r["et"],
                ]
            flow_gauges |= set(stats["flow_gauges"])
            print(
                f"{name}: {path.name}: {len(rows)} EWR results, {stats['gauges_in_file']} gauges found in the file"
                + (
                    f", could not evaluate {sorted(stats['skipped'])}"
                    if stats.get("skipped")
                    else ""
                )
            )
        flow_gauges = sorted(flow_gauges)
        out["scenarios"].append(
            {
                "id": sid,
                "name": name,
                "fmt": fmt,
                "vals": list(vals.values()),
                "flowGauges": flow_gauges,
            }
        )
        if not flow_gauges:
            continue
        add_flow_charts(out, sid, flow_gauges)
        print(
            f"{name}: daily flows and py_ewr results kept for the flow charts at {len(flow_gauges)} gauge(s)"
        )
    return out


def script_json(obj):
    """obj as strict JSON that is safe inside a <script> element: no NaN / Infinity (null instead), and '<' (so no '</script>' or
    '<!--'), U+2028 and U+2029 written as \\u escapes. JSON.parse gives back exactly obj."""
    import ewr_engine as eng

    text = json.dumps(
        eng._json_safe(obj), separators=(",", ":"), allow_nan=False, ensure_ascii=False
    )
    return (
        text.replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def read_payload(html):
    """The JSON payload embedded in a built page."""
    a = html.index(PAYLOAD_TAG) + len(PAYLOAD_TAG)
    return json.loads(html[a : html.index("</script>", a)])


def replace_payload(html, payload):
    a = html.index(PAYLOAD_TAG) + len(PAYLOAD_TAG)
    return html[:a] + script_json(payload) + html[html.index("</script>", a) :]


def vendor_text(name, sha256):
    data = (ROOT / "vendor" / name).read_bytes()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError(
            f"vendor/{name} does not match its expected sha256; restore it from git (or update VENDOR in {Path(__file__).name})"
        )
    text = data.decode("utf-8")
    return re.sub(
        r"</(script|style)", r"<\\/\1", text, flags=re.IGNORECASE
    )  # cannot end its element early


def render(payload, template=TEMPLATE):
    """The page: the template with the libraries, the flow engine and the payload written in."""
    html = Path(template).read_text()
    parts = {k: vendor_text(*v) for k, v in VENDOR.items()}
    parts["__FLOW_ENGINE_JS__"] = (HERE / "flow_engine.js").read_text()
    parts["__PAYLOAD__"] = script_json(payload)
    for tag in parts:
        if html.count(tag) != 1:
            raise ValueError(f"{template} must contain {tag} exactly once")
    return re.sub(
        "|".join(map(re.escape, parts)), lambda m: parts[m.group(0)], html
    )  # one pass: inserted text is never searched


def build(out_path=OUT, scenarios=(), open_in="demo", base_dir=ROOT):
    """Write the page to out_path and return its text."""
    payload = review_payload()
    payload.update(
        bake_scenarios(scenarios, base_dir)
        if scenarios
        else {"scenarios": [], "flows": {}, "ewrs": {}, "ewryears": {}, "regimes": {}}
    )
    payload["openIn"] = open_in
    html = render(payload)
    out_path = Path(out_path)
    out_path.write_text(html)
    print(len(html) // 1024, "KB written to", out_path)
    return html


def load_scenarios_file(path):
    cfg = json.loads(Path(path).read_text())
    return cfg.get("scenarios", []), cfg.get("open_in", "demo")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Build index.html: the EWR dashboard as one file that opens by double-click, with no server."
    )
    ap.add_argument(
        "--out",
        default=str(OUT),
        help="where to write the page (default: index.html in the repository root)",
    )
    ap.add_argument(
        "--config",
        default=str(SCENARIOS_FILE),
        help="scenarios to run through py_ewr and embed (default: scenarios.json)",
    )
    ap.add_argument(
        "--scenario",
        action="append",
        metavar="FILE",
        help="a model output file to embed as a scenario (repeat for more); used instead of --config",
    )
    ap.add_argument(
        "--format",
        default="Standard time-series",
        help="data format of the --scenario files (default: Standard time-series)",
    )
    ap.add_argument(
        "--no-scenarios",
        action="store_true",
        help="the review results only: no scenarios, py_ewr not needed",
    )
    ap.add_argument(
        "--open-in",
        choices=["demo", "user"],
        help="which data the page opens on (default: demo, or open_in from the config)",
    )
    a = ap.parse_args(argv)
    scenarios, open_in, base_dir = [], "demo", ROOT
    if a.scenario:
        scenarios, open_in, base_dir = (
            [{"path": str(Path(p).resolve()), "format": a.format} for p in a.scenario],
            "user",
            Path.cwd(),
        )
    elif not a.no_scenarios and Path(a.config).is_file():
        scenarios, open_in = load_scenarios_file(a.config)
        base_dir = Path(a.config).resolve().parent
    build(a.out, scenarios, a.open_in or open_in, base_dir)


if __name__ == "__main__":
    main()
