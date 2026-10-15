"""Build ewr_analysis_tool.html: a stripped-back analysis view of the 2026 Basin Plan review EWR results.

Only two raw metrics are carried, no ratings, scaling or scores:
  * FR  - frequency ratio per EWR (achieved / target frequency)        all_frequency_raw.csv
  * ETP - MRIP exceedance time proportion per EWR                      all_interevent_comparison_ewr_level.csv
The page averages them (plain mean over EWRs) by SDL unit, planning unit, gauge, theme, flow regime or EWR, for any
policy x climate scenario. Nothing is scaled between Pre Basin Plan and Without Development; no likelihood / theme / grade
logic is included. Gauge positions: BoM Water Data Online station list.
"""
import json
import pandas as pd
from shapely.geometry import shape, mapping
from flow_regime_mappings import regimes_for

HERE = __import__("pathlib").Path(__file__).parent
norm = lambda s: "".join(c for c in s.lower() if c.isalnum())

POLICIES = ["Without Development", "Pre Basin Plan", "Basin plan 2024", "Basin plan full implementation"]
CLIMATES = ["Historical Climate", "Warmer and Wetter 1.5 C", "Warmer and Drier 1.5 C", "Warmer and Much Drier 1.5 C",
            "Hotter and Wetter 2.0 C", "Hotter and Drier 2.0 C", "Hotter and Much Drier 2.0 C"]
NS = len(POLICIES) * len(CLIMATES)
# The model outputs report the lake-level EWRs under A4260527 (Tauwitchere); the parameter sheet puts them on A4260524 (Milang).
REKEY = {"A4260527": "A4260524"}
# Virtual gauge with no BoM station: plotted beside a real one
PROXY_LOC = {"A4261002": ("A4260527", 0.012, -0.012)}
PROXY_NAME = {"A4261002": "Barrages near the Murray Mouth (total barrage flow, virtual gauge; position approximate)"}
KEY = ["PlanningUnitName", "SWSDLName", "Gauge", "EWR"]

fr = pd.read_csv(HERE / "all_frequency_raw.csv", dtype={"Gauge": str},
                 usecols=KEY + ["EcoObj", "Target", "FlowRegime", "Policy", "ClimateModel", "FR"])
et = pd.read_csv(HERE / "all_interevent_comparison_ewr_level.csv", dtype={"Gauge": str},
                 usecols=lambda c: c.strip() in set(KEY + ["EcoObj", "Target", "Policy", "ClimateModel", "ETP", "State"]))
et.columns = et.columns.str.strip()
for d in (fr, et):
    d["Gauge"] = d.Gauge.replace(REKEY)
    d["Target"] = d.Target.fillna("Unclassified").replace({"Other species": "Other Species"})
    d["scn"] = d.Policy.map(POLICIES.index) * len(CLIMATES) + d.ClimateModel.map(CLIMATES.index)
    assert d.scn.notna().all()

# one row per (planning unit, SDL unit, gauge, EWR): both files repeat each EWR once per ecological objective
rows = {}
def row(k):
    return rows.setdefault(k, {"ob": set(), "rg": set(), "fr": [None] * NS, "et": [None] * NS, "state": None})

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
    if not r["rg"]:                                   # ETP-only EWR: use the MDBA pipeline's EWR-code -> flow regime rule
        r["rg"] = set(regimes_for(k[3], r["state"]))
    if not r["rg"]:
        r["rg"] = {"Unmapped"}

pus = sorted({k[0] for k in rows})
sdls = sorted({k[1] for k in rows})
gids = sorted({k[2] for k in rows})
# objective -> theme (the model outputs' Target, else the parameter metadata reference) and objective text
ref = pd.read_csv(HERE.parent / "py_ewr/parameter_metadata/objective_reference.csv", dtype=str).drop_duplicates("EcoObj").set_index("EcoObj")
fix = lambda t: "Other Species" if t == "Other species" else t
obj_theme = {}
for d in (fr, et):
    for o, t in d.dropna(subset=["EcoObj", "Target"]).groupby("EcoObj").Target.agg(lambda x: x.mode().iat[0]).items():
        obj_theme.setdefault(o, fix(t))
objs = sorted({o for r in rows.values() for o in r["ob"]}, key=lambda o: (o.split("-")[0], o))
for o in objs:
    if o not in obj_theme:
        obj_theme[o] = fix(ref.at[o, "Target"]) if o in ref.index and isinstance(ref.at[o, "Target"], str) else "Unclassified"
themes = ["Flows & Connectivity", "Ecosystem Functions", "Waterbirds", "Native Fish", "Native Vegetation", "Other Species", "Unclassified"]
themes += sorted(set(obj_theme.values()) - set(themes))
regimes = sorted({g for r in rows.values() for g in r["rg"]})
ROWS = [[pus.index(k[0]), sdls.index(k[1]), gids.index(k[2]), k[3],
         sorted(objs.index(o) for o in r["ob"]), sorted(regimes.index(g) for g in r["rg"]), r["fr"], r["et"]]
        for k, r in sorted(rows.items())]
print(len(ROWS), "EWR rows;", len(sdls), "SDL units;", len(pus), "planning units;", len(gids), "gauges")

# --- gauges ---------------------------------------------------------------------------------------------------------
bom = pd.read_csv(HERE / "bom_gauges/bom_wdo_station_list.csv", dtype=str)
for c in ("station_latitude", "station_longitude"):
    bom[c] = pd.to_numeric(bom[c], errors="coerce")
bom = bom.dropna(subset=["station_latitude"]).drop_duplicates("station_no").set_index("station_no")
GAUGES, unplotted = [], []
for gid in gids:
    if gid in bom.index:
        lat, lon, name = bom.at[gid, "station_latitude"], bom.at[gid, "station_longitude"], bom.at[gid, "station_name"]
    elif gid in PROXY_LOC:
        ref_id, dlat, dlon = PROXY_LOC[gid]
        lat, lon = bom.at[ref_id, "station_latitude"] + dlat, bom.at[ref_id, "station_longitude"] + dlon
        name = PROXY_NAME[gid]
    else:
        unplotted.append(gid)
        continue
    if not (-45 < lat < -9 and 110 < lon < 155):
        unplotted.append(gid)
        continue
    GAUGES.append({"i": gids.index(gid), "id": gid, "name": name, "lat": round(float(lat), 5), "lon": round(float(lon), 5)})
print(len(GAUGES), "gauges plotted; unplotted (in tables only):", unplotted)

# --- SDL unit polygons ----------------------------------------------------------------------------------------------
gj = json.load(open(HERE / "sdl_resource_units/surface_water_sdl_resource_units.geojson"))
by_norm = {norm(n): i for i, n in enumerate(sdls)}
for f in gj["features"]:
    p = f["properties"]
    f["properties"] = {"name": p["SWSDLName"], "id": p["SWSDLID"], "state": p["StateID"], "sdl": by_norm.get(norm(p["SWSDLName"]), -1)}
    f["geometry"] = mapping(shape(f["geometry"]).simplify(0.005, preserve_topology=True))
print("SDL names in results with no polygon:", [n for n in sdls if norm(n) not in {norm(f['properties']['name']) for f in gj['features']}])

META = {"policies": POLICIES, "climates": CLIMATES, "pus": pus, "sdls": sdls, "gauges": gids,
                              "themes": themes, "regimes": regimes, "objs": objs,
                              "objTheme": [themes.index(obj_theme[o]) for o in objs],
                              "objDesc": [ref.at[o, "Objective"] if o in ref.index and isinstance(ref.at[o, "Objective"], str) else "" for o in objs]}


def build(template_text, out_name):
    html = template_text
    for tag, val in {"__DATA__": gj, "__GAUGES__": GAUGES, "__ROWS__": ROWS,
                     "__RIVERS__": json.load(open(HERE / "sdl_resource_units/hydrorivers_mdb_strahler3_plus.geojson")), "__META__": META}.items():
        html = html.replace(tag, json.dumps(val, separators=(",", ":")))
    open(HERE / out_name, "w").write(html)
    print(len(html) // 1024, "KB written to", out_name)


template = open(HERE / "ewr_analysis_tool.template.html").read()
build(template, "ewr_analysis_tool.html")
