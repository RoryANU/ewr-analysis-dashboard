"""Build ewr_analysis_dashboard.html: a stripped-back analysis view of the 2026 Basin Plan review EWR results.

Run from anywhere:  python3 src/build_dashboard.py   (writes ewr_analysis_dashboard.html in the repository root)

Only two raw metrics are carried, no ratings, scaling or scores:
  * FR  - frequency ratio per EWR (achieved / target frequency)        data/results/frequency_ratio.csv
  * ETP - MRIP exceedance time proportion per EWR                      data/results/etp.csv
The page averages them (plain mean over EWRs) by SDL unit, planning unit, gauge, theme, flow regime or EWR, for any
policy x climate scenario. Nothing is scaled between Pre Basin Plan and Without Development; no likelihood / theme / grade
logic is included. Gauge positions: BoM Water Data Online station list.
"""

import json
from pathlib import Path
from typing import Any

import pandas as pd
from shapely.geometry import mapping, shape

from ewr_analysis.drought_windows import meta as drought_meta
from ewr_analysis.flow_regime_mappings import regimes_for

HERE = Path(__file__).parent  # src/
ROOT = HERE.parent  # repository root; inputs are in data/, the page is written here


def norm(s: str) -> str:
    """Lower-case letters and digits only: how SDL unit names are matched between the results and the boundary file."""
    return "".join(c for c in s.lower() if c.isalnum())


POLICIES = ["Without Development", "Pre Basin Plan", "Basin plan 2024", "Basin plan full implementation"]
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
PROXY_NAME = {"A4261002": "Barrages near the Murray Mouth (total barrage flow, virtual gauge; position approximate)"}
KEY = ["PlanningUnitName", "SWSDLName", "Gauge", "EWR"]


def detect_variant_conflicts(df: pd.DataFrame, key_columns: list[str], value_column: str) -> list[dict[str, Any]]:
    """Return groups keyed by key_columns that contain multiple distinct non-null metric values."""
    conflicts = []
    for key, group in df.groupby(key_columns, dropna=False, sort=False):
        values = sorted(group[value_column].dropna().unique().tolist())
        if len(values) > 1:
            key = key if isinstance(key, tuple) else (key,)
            conflicts.append({**dict(zip(key_columns, key, strict=True)), "values": values})
    return conflicts


def scenario_index(policy: str, climate: str) -> int:
    """Position of a policy x climate scenario in the per-row FR / ETP arrays (policy-major)."""
    return POLICIES.index(policy) * len(CLIMATES) + CLIMATES.index(climate)


def load_results(root: Path = ROOT) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read the FR and ETP result files, normalising gauge ids, themes and the scenario index (column `scn`)."""
    fr = pd.read_csv(
        root / "data/results/frequency_ratio.csv",
        dtype={"Gauge": str},
        usecols=KEY + ["EcoObj", "Target", "FlowRegime", "Policy", "ClimateModel", "FR", "EventYears", "TotalDays", "AverageEventLength"],
    )
    et = pd.read_csv(
        root / "data/results/etp.csv",
        dtype={"Gauge": str},
        usecols=lambda c: c.strip() in set(KEY + ["EcoObj", "Target", "Policy", "ClimateModel", "ETP", "State"]),
    )
    et.columns = et.columns.str.strip()
    et["ETP"] = et.ETP.clip(upper=1)  # a time proportion: source values above 1 mean exceeded for the whole record
    return normalise_results(fr), normalise_results(et)


def normalise_results(d: pd.DataFrame) -> pd.DataFrame:
    """Re-key relocated gauges, fill and tidy the theme (`Target`) and add the scenario index `scn` (in place; returns d)."""
    d["Gauge"] = d.Gauge.replace(REKEY)
    d["Target"] = d.Target.fillna("Unclassified").replace({"Other species": "Other Species"})
    d["scn"] = d.Policy.map(POLICIES.index) * len(CLIMATES) + d.ClimateModel.map(CLIMATES.index)
    assert d.scn.notna().all()
    return d


def report_variant_conflicts(fr: pd.DataFrame, et: pd.DataFrame) -> None:
    """Report source variants without changing the existing aggregation or writing an additional artifact."""
    variant_keys = KEY + ["Policy", "ClimateModel"]
    for metric, data in (("FR", fr), ("ETP", et)):
        conflicts = detect_variant_conflicts(data, variant_keys, metric)
        print(f"{metric}: {len(conflicts)} variant conflict groups; aggregation remains the existing mean.")
        for conflict in conflicts[:10]:
            key = {name: conflict[name] for name in KEY}
            scenario = (conflict["Policy"], conflict["ClimateModel"])
            print(f"  key={key}, scenario={scenario}, distinct {metric} values={conflict['values']}")


def aggregate_ewr_rows(fr: pd.DataFrame, et: pd.DataFrame) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    """One row per (planning unit, SDL unit, gauge, EWR): both files repeat each EWR once per ecological objective, so the
    metric is the plain mean over those repeats for each scenario (rounded to 4 places); None where a scenario has no result.
    Also per EWR: the achieved years (EventYears, same mean, 2 places) and the mean event length (AverageEventLength: py_ewr's threshold days
    over all its events, successful or not, ÷ their number) per scenario, and the record length in years (`ny`, TotalDays / 365.25).
    """
    rows: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    def row(key: tuple[str, str, str, str]) -> dict[str, Any]:
        return rows.setdefault(
            key, {"ob": set(), "rg": set(), "fr": [None] * NS, "et": [None] * NS, "ey": [None] * NS, "ael": [None] * NS, "ny": None, "state": None}
        )

    for key, group in fr.groupby(KEY):
        r = row(key)
        r["ob"] |= set(group.EcoObj.dropna())
        r["rg"] |= set(group.FlowRegime.dropna())
        by_scenario = group.groupby("scn")
        _fill_by_scenario(r["fr"], by_scenario.FR.mean(), 4)
        _fill_by_scenario(r["ey"], by_scenario.EventYears.mean().dropna(), 2)
        _fill_by_scenario(r["ael"], by_scenario.AverageEventLength.mean().dropna(), 4)
        days = group.TotalDays.dropna()
        r["ny"] = round(float(days.mean()) / 365.25, 2) if len(days) else None

    for key, group in et.groupby(KEY):
        r = row(key)
        r["ob"] |= set(group.EcoObj.dropna())
        r["state"] = group.State.dropna().iat[0] if group.State.notna().any() else None
        _fill_by_scenario(r["et"], group.groupby("scn").ETP.mean(), 4)

    for key, r in rows.items():
        if not r["rg"]:  # ETP-only EWR: use the MDBA pipeline's EWR-code -> flow regime rule
            r["rg"] = set(regimes_for(key[3], r["state"]))
        if not r["rg"]:
            r["rg"] = {"Unmapped"}
    return rows


def _fill_by_scenario(values: list[float | None], means: pd.Series, places: int) -> None:
    """Write each scenario's mean (indexed by scenario number) into its slot of `values`, rounded to `places`."""
    for scenario, mean in means.items():
        values[int(scenario)] = round(float(mean), places)


def dr_approx(ael: list[float | None], prm: dict[str, Any]) -> list[float | None]:
    """The review results have no yearly event lengths, so the duration ratio is approximated per scenario as the mean event length ÷ the
    required duration, capped at 2 (py_ewr's AverageEventLength averages every event, successful or not). Flow EWRs only."""
    dur = prm.get("dur")
    if prm.get("kind", "F") != "F" or not dur:
        return [None] * len(ael)
    return [None if a is None else round(min(a / dur, 2.0), 3) for a in ael]


def fix_theme(t: str) -> str:
    """One spelling for the "Other Species" theme (the sources use both cases)."""
    return "Other Species" if t == "Other species" else t


def objective_themes(fr: pd.DataFrame, et: pd.DataFrame, objs: list[str], ref: pd.DataFrame) -> dict[str, str]:
    """objective -> theme: the model outputs' Target (most common), else the parameter metadata reference, else Unclassified."""
    obj_theme: dict[str, str] = {}
    for d in (fr, et):
        for o, t in d.dropna(subset=["EcoObj", "Target"]).groupby("EcoObj").Target.agg(lambda x: x.mode().iat[0]).items():
            obj_theme.setdefault(o, fix_theme(t))
    for o in objs:
        if o not in obj_theme:
            obj_theme[o] = fix_theme(ref.at[o, "Target"]) if o in ref.index and isinstance(ref.at[o, "Target"], str) else "Unclassified"
    return obj_theme


def objectives_of(rows: dict[tuple[str, str, str, str], dict[str, Any]]) -> list[str]:
    """Every ecological objective in the rows, ordered by its prefix then code."""
    return sorted({o for r in rows.values() for o in r["ob"]}, key=lambda o: (o.split("-")[0], o))


def build_row_table(
    rows: dict[tuple[str, str, str, str], dict[str, Any]], obj_theme: dict[str, str], params: dict[tuple[str, str, str], dict[str, Any]] | None = None
) -> tuple[list[list[Any]], dict[str, Any]]:
    """The compact ROWS table the page reads, and the lookup lists (pus, sdls, gids, objs, themes, regimes, objTheme) its indices refer to.
    Columns 0-7 are fixed (the page and performance.js index them); 8 = {T, ny, dur, mrip}: target % of years, record years, required days and
    MRIP years (from `params`, keyed (gauge, code, planning unit); None when unknown); 9 = achieved years (EventYears) per scenario;
    10 = gap severity per scenario: None here, as the results hold the MRIP parameter, not the observed gaps (uploads fill it in the page);
    11 = duration ratio per scenario, approximated as mean event length ÷ required duration, capped at 2 (flow EWRs only; dr_approx);
    12 = drought-window metrics: None here (no yearly results; uploads fill it)."""
    params = params or {}
    pus = sorted({k[0] for k in rows})
    sdls = sorted({k[1] for k in rows})
    gids = sorted({k[2] for k in rows})
    objs = objectives_of(rows)
    themes = ["Flows & Connectivity", "Ecosystem Functions", "Waterbirds", "Native Fish", "Native Vegetation", "Other Species", "Unclassified"]
    themes += sorted(set(obj_theme.values()) - set(themes))
    regimes = sorted({g for r in rows.values() for g in r["rg"]})

    table = []
    for (pu, sdl, gauge, code), r in sorted(rows.items()):
        prm = params.get((gauge, code, pu), {})
        info = {"T": None, "dur": None, "mrip": None, **{k: v for k, v in prm.items() if k != "kind"}, "ny": r.get("ny")}
        table.append(
            [
                pus.index(pu),
                sdls.index(sdl),
                gids.index(gauge),
                code,
                sorted(objs.index(o) for o in r["ob"]),
                sorted(regimes.index(g) for g in r["rg"]),
                r["fr"],
                r["et"],
                info,
                r.get("ey", [None] * NS),
                None,
                dr_approx(r.get("ael", [None] * NS), prm),
                None,
            ]
        )
    lookups = {
        "pus": pus,
        "sdls": sdls,
        "gids": gids,
        "objs": objs,
        "themes": themes,
        "regimes": regimes,
        "objTheme": [themes.index(obj_theme[o]) for o in objs],
    }
    return table, lookups


def load_gauge_stations(root: Path = ROOT) -> pd.DataFrame:
    """BoM gauge stations with a known latitude, one per station number (the index)."""
    bom = pd.read_csv(root / "data/map/bom_gauge_stations.csv", dtype=str)
    for c in ("station_latitude", "station_longitude"):
        bom[c] = pd.to_numeric(bom[c], errors="coerce")
    return bom.dropna(subset=["station_latitude"]).drop_duplicates("station_no").set_index("station_no")


def load_constraints(root: Path = ROOT) -> list[dict[str, Any]]:
    """Constraint sites (data/map/constraint_sites.csv): points drawn on the map; the file is hand-maintained."""
    return [
        {
            "name": r.name,
            "river": r.river,
            "lat": float(r.lat),
            "lon": float(r.lon),
            "site": "" if pd.isna(r.site) else r.site,
            "cur": r.current_ml_d,
            "rel": r.relaxed_ml_d,
        }
        for r in pd.read_csv(root / "data/map/constraint_sites.csv").itertuples()
    ]


def build_gauges(gids: list[str], bom: pd.DataFrame) -> tuple[list[dict[str, Any]], list[str]]:
    """Plottable gauges (BoM station list, plus the virtual proxy gauges) and the ids that cannot be placed on the map."""
    gauges: list[dict[str, Any]] = []
    unplotted: list[str] = []
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
        gauges.append({"i": gids.index(gid), "id": gid, "name": name, "lat": round(float(lat), 5), "lon": round(float(lon), 5)})
    return gauges, unplotted


def build_polygons(gj: dict[str, Any], sdls: list[str]) -> dict[str, Any]:
    """SDL unit polygons: slimmed properties (`sdl` = index into sdls, -1 when the results have no such unit) and simplified geometry (in place)."""
    by_norm = {norm(n): i for i, n in enumerate(sdls)}
    for f in gj["features"]:
        props = f["properties"]
        f["properties"] = {"name": props["SWSDLName"], "id": props["SWSDLID"], "state": props["StateID"], "sdl": by_norm.get(norm(props["SWSDLName"]), -1)}
        f["geometry"] = mapping(shape(f["geometry"]).simplify(0.005, preserve_topology=True))
    return gj


def parameter_sheet() -> pd.DataFrame | None:
    """py_ewr's EWR parameter sheet (a fresh read, not its shared cached copy), or None when py_ewr is not installed."""
    try:
        from py_ewr import data_inputs

        return getattr(data_inputs.get_EWR_table, "__wrapped__", data_inputs.get_EWR_table)(None)
    except Exception:  # noqa: BLE001 - the page still works without the parameters
        return None


def ewr_params(sheet: pd.DataFrame | None) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Per (gauge, code, planning unit): target frequency (% of years), required duration (days) and maximum inter-event period (years),
    from the parameter sheet (the first row where a key repeats); empty without a sheet."""
    if sheet is None:
        return {}

    def number(column: str) -> pd.Series:
        return pd.to_numeric(sheet[column], errors="coerce") if column in sheet else pd.Series(float("nan"), index=sheet.index)

    d = pd.DataFrame({"g": sheet.Gauge.astype(str), "c": sheet.Code.astype(str), "pu": sheet.PlanningUnitName.astype(str)})
    d["T"], d["dur"], d["mrip"] = number("TargetFrequency"), number("Duration"), number("MaxInter-event")
    d["kind"] = sheet.FlowLevelVolume.astype(str) if "FlowLevelVolume" in sheet else "F"
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in d.drop_duplicates(["g", "c", "pu"]).itertuples():
        out[(r.g, r.c, r.pu)] = {
            "T": None if pd.isna(r.T) else float(r.T),
            "dur": None if pd.isna(r.dur) else int(r.dur),
            "mrip": None if pd.isna(r.mrip) else round(float(r.mrip), 4),
            "kind": r.kind,  # F / L / V (flow, level, volume); not carried into ROWS
        }
    return out


def ewr_thresholds(lookups: dict[str, Any], table: list[list[Any]], sheet: pd.DataFrame | None = None) -> dict[str, float]:
    """Lowest flow threshold (ML/d) of each EWR at each gauge in the table, from py_ewr's parameter sheet, keyed "gauge|code".
    The page sorts EWRs by it (increasing flow regime). Empty when py_ewr is not installed; EWRs with no threshold sort last."""
    sheet = parameter_sheet() if sheet is None else sheet
    if sheet is None:
        return {}
    thr = pd.to_numeric(sheet["FlowThresholdMin"], errors="coerce")
    low = thr.groupby([sheet["Gauge"].astype(str), sheet["Code"].astype(str)]).min().dropna()
    wanted = {(lookups["gids"][r[2]], r[3]) for r in table}
    return {f"{g}|{c}": float(v) for (g, c), v in sorted(low.items()) if (g, c) in wanted}


EWR_COLS = [  # parameter_sheet.csv column -> short key in the page's EWR index (the guide's search and tutorials)
    ("PlanningUnitName", "pu"),
    ("State", "st"),
    ("Gauge", "g"),
    ("CompliancePoint/Node", "gn"),
    ("Code", "c"),
    ("Catchment_BPRegion", "ca"),
    ("SWSDLName", "sdl"),
    ("FlowLevelVolume", "k"),
    ("EcoObj", "eo"),
    ("TargetFrequency", "tf"),
    ("TargetFrequencyMin", "tfl"),
    ("TargetFrequencyMax", "tfh"),
    ("EventsPerYear", "epy"),
    ("Duration", "dur"),
    ("MinSpell", "ms"),
    ("FlowThresholdMin", "tmin"),
    ("FlowThresholdMax", "tmax"),
    ("MaxInter-event", "mi"),
    ("WithinEventGapTolerance", "gap"),
    ("LevelThresholdMin", "lmin"),
    ("LevelThresholdMax", "lmax"),
    ("VolumeThreshold", "vol"),
    ("AccumulationPeriod", "acc"),
    ("Multigauge", "mg"),
]
EWR_STR = {"pu", "st", "g", "gn", "c", "ca", "sdl", "k", "eo", "mg", "h"}


def ewr_handlers(sheet: pd.DataFrame) -> list[str | None]:
    """py_ewr's handler for each sheet row, from its calc config (key "<code>-<single|multigauge>-<F|L|V>"); None when py_ewr is not installed.
    The EWR guide only has a tutorial for flow_handle and lowflow_handle rows."""
    try:
        from py_ewr import data_inputs

        cfg = data_inputs.get_ewr_calc_config(None)
    except Exception:  # noqa: BLE001 - the guide then shows "demo still in development" for every EWR
        print("py_ewr not available: the EWR guide will have no tutorials")
        return [None] * len(sheet)
    by_key = {key: handler for handler, keys in cfg.items() for key in keys}
    multi = sheet["Multigauge"].notna() & sheet["Multigauge"].astype(str).str.strip().ne("")
    keys = sheet["Code"].astype(str) + "-" + multi.map({True: "multigauge", False: "single"}) + "-" + sheet["FlowLevelVolume"].astype(str)
    return [by_key.get(k, "unmapped") for k in keys]


def ewr_index(root: Path = ROOT) -> dict[str, Any]:
    """Every row of data/reference/parameter_sheet.csv (kept equal to the MDBA EWR tool's copy by src/sync_parameter_sheet.py), column-wise:
    {"keys": [...], "cols": {key: [value per row]}}. Repeated strings are stored once in "strs" and referenced by index."""
    path = root / "data/reference/parameter_sheet.csv"
    if not path.exists():  # a test fixture tree has no copy of the sheet: use the repository's
        path = ROOT / "data/reference/parameter_sheet.csv"
    sheet = pd.read_csv(path, dtype={"Gauge": str, "Multigauge": str}, low_memory=False)
    cols: dict[str, list[Any]] = {}

    # StartMonth / EndMonth are text such as "9.15" = 15 September; py_ewr splits them at the "." and reads the rest as the day.
    # No day means the whole month: sd defaults to 1 and ed to null (the page uses the month's last day).
    raw = pd.read_csv(path, dtype=str, usecols=["StartMonth", "EndMonth"])
    for column, month_key, day_key in (("StartMonth", "sm", "sd"), ("EndMonth", "em", "ed")):
        parts = raw[column].fillna("").str.split(".", n=1, expand=True).reindex(columns=[0, 1]).fillna("")
        cols[month_key] = [int(m) if m else None for m in parts[0]]
        cols[day_key] = [int(d) if d else (1 if day_key == "sd" else None) for d in parts[1]]

    sheet["_handler"] = ewr_handlers(sheet)
    strs: list[str] = []  # each distinct string once; string columns hold indices into it
    string_index: dict[str, int] = {}
    for column, key in [*EWR_COLS, ("_handler", "h")]:
        values = sheet[column] if column in sheet else pd.Series([None] * len(sheet))
        cols[key] = [_encode_value(v, key in EWR_STR, strs, string_index) for v in values]
    return {"n": len(sheet), "strs": strs, "cols": cols}


def _encode_value(value: Any, is_string: bool, strs: list[str], string_index: dict[str, int]) -> Any:
    """One EWR index cell: None when blank, else a string's index in `strs` (added on first sight), else a compact number."""
    if pd.isna(value):
        return None
    if is_string:
        text = str(value)
        if text not in string_index:
            string_index[text] = len(strs)
            strs.append(text)
        return string_index[text]
    number = float(value)
    return int(number) if number.is_integer() else round(number, 4)


def build_meta(lookups: dict[str, Any], ref: pd.DataFrame, ewr_thr: dict[str, float] | None = None) -> dict[str, Any]:
    """The page's META: scenario names, the lookup lists ROWS indexes into, objective descriptions and EWR thresholds."""
    objs = lookups["objs"]
    return {
        "ewrThr": ewr_thr or {},
        "droughts": drought_meta(),
        "policies": POLICIES,
        "climates": CLIMATES,
        "pus": lookups["pus"],
        "sdls": lookups["sdls"],
        "gauges": lookups["gids"],
        "themes": lookups["themes"],
        "regimes": lookups["regimes"],
        "objs": objs,
        "objTheme": lookups["objTheme"],
        "objDesc": [ref.at[o, "Objective"] if o in ref.index and isinstance(ref.at[o, "Objective"], str) else "" for o in objs],
    }


def render_html(template_text: str, payload: dict[str, Any]) -> str:
    """Fill the template's `__NAME__` slots with compact JSON, in the order given."""
    html = template_text.replace("__PERFORMANCE_JS__", (HERE / "performance.js").read_text(encoding="utf-8"))
    html = html.replace("__EWR_GUIDE_JS__", (HERE / "ewr_guide.js").read_text(encoding="utf-8"))
    html = html.replace("__CUSTOM_EWR_JS__", (HERE / "custom_ewr.js").read_text(encoding="utf-8"))
    for tag, val in payload.items():
        html = html.replace(tag, json.dumps(val, separators=(",", ":")).replace("<", "\\u003c"))
    return html


def build_payload(root: Path = ROOT) -> dict[str, Any]:
    """Everything the page embeds, keyed by template slot, read from data/ under root (prints the same summary as always)."""
    fr, et = load_results(root)
    report_variant_conflicts(fr, et)
    rows = aggregate_ewr_rows(fr, et)
    ref = pd.read_csv(root / "data/reference/objective_reference.csv", dtype=str).drop_duplicates("EcoObj").set_index("EcoObj")
    sheet = parameter_sheet()
    table, lookups = build_row_table(rows, objective_themes(fr, et, objectives_of(rows), ref), ewr_params(sheet))
    print(len(table), "EWR rows;", len(lookups["sdls"]), "SDL units;", len(lookups["pus"]), "planning units;", len(lookups["gids"]), "gauges")
    gauges, unplotted = build_gauges(lookups["gids"], load_gauge_stations(root))
    print(len(gauges), "gauges plotted; unplotted (in tables only):", unplotted)
    with open(root / "data/map/sdl_resource_units.geojson") as fh:
        gj = build_polygons(json.load(fh), lookups["sdls"])
    print("SDL names in results with no polygon:", [n for n in lookups["sdls"] if norm(n) not in {norm(f["properties"]["name"]) for f in gj["features"]}])
    with open(root / "data/map/rivers.geojson") as fh:
        rivers = json.load(fh)
    return {
        "__DATA__": gj,
        "__GAUGES__": gauges,
        "__ROWS__": table,
        "__RIVERS__": rivers,
        "__CONSTRAINTS__": load_constraints(root),
        "__META__": build_meta(lookups, ref, ewr_thresholds(lookups, table, sheet)),
        "__EWRIDX__": ewr_index(root),
        "__SCENARIOS__": [],
    }


OUTPUT = ROOT / "ewr_analysis_dashboard.html"


def is_stale() -> bool:
    """True when the generated page is missing or older than anything it is built from (the template, scripts and data/)."""
    if not OUTPUT.exists():
        return True
    built = OUTPUT.stat().st_mtime
    inputs = [*HERE.glob("*.js"), *HERE.glob("*.py"), HERE / "dashboard_template.html", *(p for p in (ROOT / "data").rglob("*") if p.is_file())]
    return any(p.stat().st_mtime > built for p in inputs)


def ensure_built() -> None:
    """Generate the page if it is missing or out of date. It is not committed: it is made here, on start-up, and served by server.py."""
    if is_stale():
        print("Building the dashboard page (a few seconds)...")
        main()


def main() -> None:
    """Rebuild ewr_analysis_dashboard.html from the template and data/."""
    payload = build_payload()
    html = render_html((HERE / "dashboard_template.html").read_text(), payload)
    out_name = "ewr_analysis_dashboard.html"
    (ROOT / out_name).write_text(html)
    print(len(html) // 1024, "KB written to", out_name)


if __name__ == "__main__":
    main()
