import math
from typing import Any

import pandas as pd

from ewr_analysis.core.calculations.assessment_years import assessment_start, complete_years, window_metrics, year_summary
from ewr_analysis.core.calculations.drought_windows import DROUGHTS, window_dates
from ewr_analysis.core.calculations.duration_ratio import dr_approx, dr_years, vr_years
from ewr_analysis.core.calculations.ewr_parameters import LOW_FLOW_HANDLERS, _dispatch_key, _is_multigauge


def _group_by_ewr(table: Any) -> dict[tuple[str, str, str], Any]:
    """Partition a supplied successful-event summary by EWR key.

    Equation / rule: Group by Gauge, PlanningUnit, Code and stringify the gauge in each output key.

    Parameters
    ----------
    table : DataFrame
        Py_ewr summary rows with Gauge, PlanningUnit, Code [labels], plus arbitrary
        summary columns [original units]. Row index/timezone is retained in groups.

    Returns
    -------
    dict[tuple[str, str, str], DataFrame]
        Group frames under (gauge, planning unit, code). Empty input or missing Gauge
        yields {}; other absent required columns can raise.

    Assumptions
    -----------
    Pandas default group sorting/drop-null behavior is retained. Gauge string
    conversion can collapse formerly distinct keys. No metric NaN filtering or date
    boundary operation occurs. No input is mutated; group alias semantics follow pandas.

    References
    ----------
    pre-refactor:src/server.py::_group_by_ewr. Scientific provenance or
    suitability of this retained convention: TBD, needs scientist review."""
    if not len(table) or "Gauge" not in table:
        return {}
    return {(str(gauge), pu, code): group for (gauge, pu, code), group in table.groupby(["Gauge", "PlanningUnit", "Code"])}


def _json_safe(obj: Any) -> Any:
    """Replace nonfinite Python float values in nested payload containers.

    Equation / rule: Return None for nonfinite Python floats; recurse over dict values and
    list/tuple elements, returning lists for both sequence types.

    Parameters
    ----------
    obj : object
        Scalar or nested payload [original units/labels]; arbitrary structure, no
        required shape/index/timezone. Dictionary keys are not converted.

    Returns
    -------
    object
        A finite float, None, reconstructed dict/list, or the original other object.
        Python float NaN/positive/negative infinity become None.

    Assumptions
    -----------
    Non-float numpy scalars, sets and other types pass through; this is not a
    complete JSON validator. Cyclic structures are not handled. No numerical/date
    boundary changes or input mutation occur.

    References
    ----------
    pre-refactor:src/server.py::_json_safe. Scientific provenance or
    suitability of this retained convention: TBD, needs scientist review."""
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
    """Report groups with more than one distinct non-null metric value.

    Equation / rule: Group by the supplied keys, preserving encounter order, and sort distinct
    non-null values; emit only groups with more than one value.

    Parameters
    ----------
    df : DataFrame
        Rows of result observations; key labels [identifiers] and metric values
        [value_column units]. Index/timezone is ignored and retained on input.
    key_columns : list[str]
        Grouping column names [labels]; null grouping keys are included.
    value_column : str
        Metric column name [label], e.g. FR [dimensionless] or ETP [record fraction].

    Returns
    -------
    list[dict]
        One key-to-label mapping plus sorted values per conflicting group. Empty
        frames with the required key columns yield [].

    Assumptions
    -----------
    NaN metrics are excluded; repeated equal values are one value. Group boundaries
    are exact key equality, not approximate numeric comparison. Values are sortable;
    mixed incomparable types can raise. No input is mutated.

    References
    ----------
    pre-refactor:src/build_dashboard.py::detect_variant_conflicts. Scientific provenance or
    suitability of this retained convention: TBD, needs scientist review."""
    conflicts = []
    for key, group in df.groupby(key_columns, dropna=False, sort=False):
        values = sorted(group[value_column].dropna().unique().tolist())
        if len(values) > 1:
            key = key if isinstance(key, tuple) else (key,)
            conflicts.append({**dict(zip(key_columns, key, strict=True)), "values": values})
    return conflicts


def scenario_index(policy: str, climate: str) -> int:
    """Locate a policy/climate scenario in the fixed catalogue order.

    Equation / rule: POLICIES.index(policy) * len(CLIMATES) + CLIMATES.index(climate).

    Parameters
    ----------
    policy, climate : str
        Case-sensitive catalogue labels [identifiers], scalars; no index/timezone.

    Returns
    -------
    int
        Zero-based policy-major position [scenario slot]; current catalogue has
        28 slots. Unknown labels, including None, raise ValueError.

    Assumptions
    -----------
    Catalogue lists are read-only and their order is significant. NaN labels are
    not missing-value defaults. First/last slots are 0/27 for the current catalogue.
    No input is mutated.

    References
    ----------
    pre-refactor:src/build_dashboard.py::scenario_index. Scientific provenance or
    suitability of this retained convention: TBD, needs scientist review."""
    return POLICIES.index(policy) * len(CLIMATES) + CLIMATES.index(climate)


def fix_theme(t: str) -> str:
    """Fold one known theme spelling to the existing display label.

    Equation / rule: Return Other Species only for exact equality to Other species.

    Parameters
    ----------
    t : str
        Theme label [identifier], scalar; no index or timezone applies.

    Returns
    -------
    str
        Normalized label [identifier]; other values pass through unchanged. The
        runtime also accepts None/NaN and does not coerce them to strings.

    Assumptions
    -----------
    Only this exact case variant is folded; no trimming or general case conversion.
    Comparison uses Python equality; unusual objects can raise. No input is mutated.
    Date boundaries are inapplicable.

    References
    ----------
    pre-refactor:src/build_dashboard.py::fix_theme. Scientific provenance or
    suitability of this retained convention: TBD, needs scientist review."""
    return "Other Species" if t == "Other species" else t


def objective_themes(fr: pd.DataFrame, et: pd.DataFrame, objs: list[str], ref: pd.DataFrame) -> dict[str, str]:
    """Assign objective themes from result modes, then reference metadata.

    Equation / rule: For each objective take the first FR mode, else first ETP mode; fill absent
    objectives from a string reference Target, else Unclassified.

    Parameters
    ----------
    fr, et : DataFrame
        EcoObj and Target [objective/theme labels]; arbitrary row index/timezone
        is ignored. Rows missing either field are excluded from modes.
    objs : list[str]
        Objective labels to fill [identifiers]. Extra result objectives are retained.
    ref : DataFrame
        Target [theme label], indexed by objective [identifier].

    Returns
    -------
    dict[str, str]
        Objective-to-theme labels. The fixed spelling fold uses fix_theme; empty
        results still permit reference/default assignments.

    Assumptions
    -----------
    FR precedes ETP, and mode ties select pandas' first (sorted) mode. A present
    Unclassified label prevents reference fallback. Reference values must be strings;
    no timezone or numerical threshold applies. No input is mutated.

    References
    ----------
    pre-refactor:src/build_dashboard.py::objective_themes. Scientific provenance or
    suitability of this retained convention: TBD, needs scientist review."""
    obj_theme: dict[str, str] = {}
    for d in (fr, et):
        for o, t in d.dropna(subset=["EcoObj", "Target"]).groupby("EcoObj").Target.agg(lambda x: x.mode().iat[0]).items():
            obj_theme.setdefault(o, fix_theme(t))
    for o in objs:
        if o not in obj_theme:
            obj_theme[o] = fix_theme(ref.at[o, "Target"]) if o in ref.index and isinstance(ref.at[o, "Target"], str) else "Unclassified"
    return obj_theme


def objectives_of(rows: dict[tuple[str, str, str, str], dict[str, Any]]) -> list[str]:
    """List distinct objective codes ordered by prefix then full code.

    Equation / rule: Sort the union of row ob sets by (code.split("-")[0], code).

    Parameters
    ----------
    rows : dict
        EWR-keyed mappings with ob [set/list of objective labels]. No date index
        or timezone applies; labels are assumed strings.

    Returns
    -------
    list[str]
        Unique objective labels [identifiers]; empty rows give [].

    Assumptions
    -----------
    Ordering is lexical, not numerical suffix ordering (A-10 precedes A-2).
    Missing/NaN labels are not filtered. There are no numeric boundaries.
    No input is mutated.

    References
    ----------
    pre-refactor:src/build_dashboard.py::objectives_of. Scientific provenance or
    suitability of this retained convention: TBD, needs scientist review."""
    return sorted({o for r in rows.values() for o in r["ob"]}, key=lambda o: (o.split("-")[0], o))


def build_row_table(
    rows: dict[tuple[str, str, str, str], dict[str, Any]], obj_theme: dict[str, str], params: dict[tuple[str, str, str], dict[str, Any]] | None = None
) -> tuple[list[list[Any]], dict[str, Any]]:
    """Encode EWR rows into the existing compact table and lookup lists.

    Equation / rule: Sort EWR keys and lookup labels; retain raw metric lists, join parameter
    metadata by (gauge, code, planning unit), and derive DR through dr_approx.

    Parameters
    ----------
    rows : dict
        Keys (planning unit, SDL unit, gauge, EWR) [labels]. Values contain ob/rg
        label sets and scenario lists: fr [dimensionless], et [record fraction],
        ey [achieved years], ael [days], ny [record years]. No date index/timezone.
    obj_theme : dict[str, str]
        Objective-to-theme labels, required for every row objective.
    params : dict or None
        Keys (gauge, EWR, planning unit); T [% years], dur [days], mrip [years],
        kind [label], plus any other existing metadata fields.

    Returns
    -------
    (list[list], dict)
        Sorted 13-column compact rows and lookup arrays. Metric lists keep their
        scenario order; None placeholders and the fixed seven themes remain on
        empty input. DR [dimensionless] follows dr_approx's cap/rounding.

    Assumptions
    -----------
    No FR cap or NaN normalization occurs here. FR/ETP/achieved lists are returned
    by reference, while inputs are not mutated. Extra themes append in sorted order;
    parameter kind is omitted and row ny overrides metadata ny. Label/index bounds
    are assumed valid. Numeric/date boundaries are delegated to existing helpers.

    References
    ----------
    pre-refactor:src/build_dashboard.py::build_row_table. Scientific provenance or
    suitability of this retained convention: TBD, needs scientist review."""
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
    """Aggregate annual EWR ratios and completeness into summary/window metrics.

    Equation / rule: Choose the existing flow/volume ratio rule from kind/handler, compute timing
    completeness, summarize DR/near misses and apply window_metrics per drought.
    The scientific interpretation of this composition: TBD, needs scientist review.

    Parameters
    ----------
    code : str
        EWR identifier matching yearly column prefixes [label].
    params : parameter row
        StartMonth/EndMonth [months], optional StartDay/EndDay [days of month], kind
        and Multigauge [labels], Duration/AccumulationPeriod [days], EventsPerYear
        [count], VolumeThreshold [ML for daily ML/d flow].
    yearly : DataFrame
        Assessment-start-year index [year labels]; <code>_eventYears [flags] and
        relevant maxEventDays/totalEventDays [days] columns. NaN achievement -> 0.
    ewr_events : dict or None
        Year-to-event lists; lengths [days] consumed by dr_years.
    daily : Series
        Nonempty ordered daily DatetimeIndex observations [caller units]; timezone
        must match inherited calendar/window contracts. No normalization is added.
    gaps : list[dict]
        Inclusive start/end [dates], days [days], censored [flag].
    mrip, target : scalar
        Maximum inter-event period [days] and target frequency [% years].
    handlers : dict
        Explicit dispatch-key-to-handler labels; read-only LOW_FLOW_HANDLERS applies.
    use_days : bool
        Use supplied optional day boundaries [flag], otherwise whole months.

    Returns
    -------
    dict
        dr [dimensionless median], nm [near-miss/failure year counts], win keyed by
        existing DROUGHTS with n [years], fr/et/sev/dr [dimensionless or None].
        Ratio/window rounding and caps are inherited unchanged.

    Assumptions
    -----------
    Starting-year labels and inclusive timing/date bounds are preserved. None/NaN
    policies are delegated to existing helpers; daily first/last must exist. Unknown
    kind gives None ratios. Global catalogues are read-only. No input is mutated.

    References
    ----------
    pre-refactor:src/server.py::_assessment_year_stats. Scientific provenance or
    suitability of this retained convention: TBD, needs scientist review."""
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
