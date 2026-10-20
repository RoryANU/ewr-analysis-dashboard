import math
from typing import Any

import pandas as pd

from ewr_analysis.core.calculations.assessment_years import assessment_start, complete_years, window_metrics, year_summary
from ewr_analysis.core.calculations.drought_windows import DROUGHTS, window_dates
from ewr_analysis.core.calculations.duration_ratio import dr_approx, dr_years, vr_years
from ewr_analysis.core.calculations.ewr_parameters import LOW_FLOW_HANDLERS, _dispatch_key, _is_multigauge


def _group_by_ewr(table: Any) -> dict[tuple[str, str, str], Any]:
    """Split a py_ewr event summary into one frame per (gauge, planning unit, EWR code)."""
    if not len(table) or "Gauge" not in table:
        return {}
    return {(str(gauge), pu, code): group for (gauge, pu, code), group in table.groupby(["Gauge", "PlanningUnit", "Code"])}


def _json_safe(obj: Any) -> Any:
    """Recursively replace non-finite floats (NaN, +-inf) with None so the response is strict JSON (null in the browser)."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


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


def _assessment_year_stats(
    code: str,
    params: Any,
    yearly: Any,
    ewr_events: Any,
    daily: Any,
    gaps: list[dict[str, Any]],
    mrip: int,
    target: Any,
    handlers: dict[str, str],
    use_days: bool,
) -> dict[str, Any]:
    """An EWR's results by assessment year (achieved, complete, duration or volume ratio), summarised as the median duration
    ratio and near misses (year_summary) and as metrics inside each drought window (`win`, window_metrics)."""
    import pandas as pd

    start_month, end_month = int(params.StartMonth), int(params.EndMonth)
    start_day, end_day = (int(getattr(params, f)) if use_days and not pd.isna(getattr(params, f, None)) else None for f in ("StartDay", "EndDay"))
    year_start = assessment_start(start_month, end_month)
    water_years = [int(w) for w in yearly.index]
    kind = str(params.FlowLevelVolume)

    if kind == "F":
        low = handlers.get(_dispatch_key(code, _is_multigauge(params.Multigauge), kind)) in LOW_FLOW_HANDLERS
        ratios = dr_years(yearly, code, params.Duration, low, params.EventsPerYear, ewr_events)
    elif kind == "V":
        ratios = vr_years(daily, water_years, year_start, start_month, end_month, params.AccumulationPeriod, params.VolumeThreshold, start_day, end_day)
    else:
        ratios = [None] * len(water_years)

    years = {
        "wy": water_years,
        "achieved": pd.to_numeric(yearly[f"{code}_eventYears"], errors="coerce").fillna(0).astype(int).tolist(),
        "ystart": year_start,
        "dr": ratios,
        "complete": complete_years(daily, water_years, year_start, start_month, end_month, start_day, end_day),
    }
    record = (daily.index[0], daily.index[-1])
    windows = {w: window_metrics(years, gaps, mrip, target, *window_dates(w, year_start), record=record) for w in DROUGHTS}
    return {**year_summary(ratios, years["complete"]), "win": windows}
