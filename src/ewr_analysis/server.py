"""Local server for the EWR analysis dashboard: serves the page and runs py_ewr on an uploaded scenario.

    python3 -m ewr_analysis.server [port] [--no-browser] [--host=0.0.0.0]   (default 8792, localhost only; if that port is busy the next free one is used,
                                                   and the dashboard opens in your default browser). Easiest start: python3 run.py

Endpoints (used by ewr_analysis_dashboard.html):
    GET  /api/ping                     -> {"ok": true}
    GET  /api/observed/units            -> SDL units and locally known gauge names
    POST /api/observed/run              body = {sdl, gauges[], start, end, refresh, merge?} -> {job}
    POST /api/run                      body = a model output file in one of the EWR tool's formats (FORMATS below)
                                       headers X-Format (model_format), X-Filename, X-Scn-Id, X-Gauges (gauge ids to look for in the file),
                                       X-Merge (1 = add series to the scenario stored under X-Scn-Id),
                                       X-TDS-Factor (optional positive multiplier converting *_tds values to EC; default 0.6)
                                       -> {"job": id}
    GET  /api/job?id=<id>              -> {"status": "running" | "done" | "error", ...}

    GET  /api/heatmap?base=<id>[&comp=<id>]&gauge=<g>&xlim=&dmax=&xmin=&dmin=&sm=&em=   threshold x duration achievement heat map data
    GET  /api/ewrs?gauge=<g>                                               EWRs at a gauge handled by flow_handle / lowflow_handle
    GET  /api/interevents?scn=<id>&gauge=<g>&ewr=<code>&pu=<name>          gaps between successful events longer than the EWR's max inter-event period
    GET  /api/ewryears?base=<id>[&comp=<id>]&gauge=<g>&ewr=<code>&pu=<name> one EWR by water year: achievement, events (duration, volume, timing), gaps vs MRIP
    GET  /api/spells?base=<id>[&comp=<id>]&gauge=<g>&thr=<ML/d>            low-flow spells (consecutive days below thr) by water year
    GET  /api/regimes?base=<id>[&comp=<id>]&gauge=<g>                     flow regime components (thresholds from the EWR tool) by water year
    GET  /api/curves?base=<id>[&comp=<id>]&gauge=<g>&kind=hydro|fdc|dfdc|mdfdc[&thr=400,2000][&ymin=][&ymax=]
                                       hydrograph, flow duration curve, differential flow duration curve, monthly differential flow duration
    POST /api/forget?id=<id>           delete a stored scenario
    GET  /api/uewr/schema              user EWR handler/parameter schema from py_ewr
    GET  /api/uewr                     saved user EWR definitions
    POST /api/uewr                     save a validated definition (JSON, up to 64 KB)
    POST /api/uewr/preview             evaluate an unsaved definition on one uploaded scenario
    POST /api/uewr/run                 evaluate saved definitions on uploaded scenarios as a job
    POST /api/uewr/delete?id=<id>      delete a definition and its cached evaluations/sheets

Uploaded daily series are kept in .user_scenarios/ (repository root): <id>.csv for flows, <id>_level.csv for levels,
and <id>_salinity.csv for salinity in EC (µS/cm). Date-free ten-thousand-year series are evaluated but not stored.

For each EWR in the uploaded scenario the job returns the two metrics the tool uses:
    FR  = Frequency / TargetFrequency                    (py_ewr summary results, as in the MDBA outputs)
    ETP = L / TotalDays, where L = sum over the gaps between successful events of max(0, gap days - MRIP days)
          and MRIP days = MaxInterEventYears x 365       (py_ewr successful inter-events)
"""

import hmac
import importlib.util
import ipaddress
import json
import math
import os
import re
import secrets
import socket
import sys
import tempfile
import threading
import time
import traceback
import uuid
import warnings
import webbrowser
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from datetime import date, timedelta
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlparse, urlsplit

from ewr_analysis import observed
from ewr_analysis.drought_windows import DROUGHTS, window_dates
from ewr_analysis.paths import PACKAGE_DIR, ROOT
from ewr_analysis.paths import USER_SCENARIOS as STORE

HERE = PACKAGE_DIR
warnings.filterwarnings("ignore")

JOBS: dict[str, dict[str, Any]] = {}
OBSERVED_ACTIVE: dict[str, str] = {}
OBSERVED_ACTIVE_LOCK = threading.Lock()
FLOWS: dict[tuple[str, str], Any] = {}  # (scenario id, kind) -> DataFrame (date index, one column per gauge)
SERIES_SUFFIX = {"flow": "", "level": "_level", "salinity": "_salinity"}
MAX_BYTES = 400 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024
EARLIEST_RECORD = date(1890, 1, 1)
DEFAULT_PORT = 8792
PAGE = "/ewr_analysis_dashboard.html"
# py_ewr handlers that count any days at or above the threshold rather than one consecutive run
LOW_FLOW_HANDLERS = ("lowflow_handle", "lowflow_handle_multi")


def _safe_id(value: str | None) -> str:
    """Strip everything but letters, digits, underscores and hyphens, so a client-supplied id is safe in a file name."""
    return re.sub(r"[^A-Za-z0-9_-]", "", value or "")


def _iso_date(value: Any) -> str:
    import pandas as pd

    return cast(str, pd.Timestamp(value).strftime("%Y-%m-%d"))


def _is_multigauge(value: Any) -> bool:
    """A parameter-sheet Multigauge cell names a partner gauge (blank cells read as NaN or whitespace)."""
    return isinstance(value, str) and bool(value.strip())


def _dispatch_key(code: str, multigauge: bool, kind: Any) -> str:
    """The key py_ewr's calc config uses to pick an EWR's handler, e.g. 'BF1_a-single-F'."""
    return f"{code}-{'multigauge' if multigauge else 'single'}-{kind}"


def _mrip_days(max_inter_event_years: Any) -> int:
    """Maximum inter-event period (MRIP) in days; 0 when the EWR has none (blank, NaN or not positive)."""
    return int(round(float(max_inter_event_years) * 365)) if max_inter_event_years > 0 else 0


def _authority(hostport: str | None, default_port: int | None = None) -> tuple[str, int | None] | None:
    """Parse and normalize a Host-style authority into (host, port), or return None if invalid."""
    if not hostport:
        return None
    try:
        parsed = urlsplit("//" + hostport)
        if parsed.username is not None or parsed.password is not None or parsed.path or parsed.query or parsed.fragment:
            return None
        host = parsed.hostname
        port = parsed.port if parsed.port is not None else default_port
    except ValueError:
        return None
    if not host:
        return None
    host = host.rstrip(".").lower()
    try:
        host = ipaddress.ip_address(host).compressed
    except ValueError:
        pass
    return host, port


def _loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _state_change_allowed(origin: str | None, host: str | None, server_address: tuple[Any, ...]) -> bool:
    """Allow absent Origin; otherwise require same authority, including loopback aliases."""
    bind_host, server_port = server_address[0], server_address[1]
    try:
        loopback_bind = bind_host.lower() == "localhost" or ipaddress.ip_address(bind_host).is_loopback
    except ValueError:
        loopback_bind = False

    request_authority = _authority(host, server_port)
    if loopback_bind and host:
        if request_authority is None or not _loopback_host(request_authority[0]) or request_authority[1] != server_port:
            return False
    if not origin:
        return True

    try:
        parsed_origin = urlsplit(origin)
        if parsed_origin.scheme.lower() not in ("http", "https") or parsed_origin.path or parsed_origin.query or parsed_origin.fragment:
            return False
        origin_authority = _authority(parsed_origin.netloc, 80 if parsed_origin.scheme.lower() == "http" else 443)
    except ValueError:
        return False
    if origin_authority is None:
        return False

    if loopback_bind:
        origin_host, origin_port = origin_authority
        if not _loopback_host(origin_host) or origin_port != server_port:
            return False
        return request_authority is None or request_authority[1] == origin_port

    if request_authority is None:
        return False
    # For proxied/external binds, Host is the authority the browser connected to.
    request_authority = _authority(host, origin_authority[1])
    return request_authority == origin_authority


# Model output formats, as named in the EWR tool (ScenarioHandler model_format). 'All Bigmod' needs many files, so it is not offered.
FORMATS = [
    "Standard time-series",
    "Bigmod - MDBA",
    "FIRM - MDBA",
    "res.csv - MDBA",
    "Source - NSW (res.csv)",
    "IQQM - netcdf",
    "ten thousand year",
    "Northern connectivity",
]


# a run_scenario row as the page stores it (an uploaded or embedded scenario's `vals`): these keys, in this order (VALK in the page)
VAL_KEYS = ("pu", "gauge", "ewr", "fr", "et", "ey", "ny", "sev", "dr", "nm", "win")


def load_frames(fmt: str, path: str, sub: str = "", tds_factor: float = 0.6) -> tuple[Any, Any, Any]:
    """Read flow, level and salinity frames, one column per gauge, with py_ewr's cleaners and node mappings.

    Standard/ten-thousand-year *_salinity and *_ec columns are EC (µS/cm); *_tds values are multiplied by tds_factor.
    Keep the cleaners' daily index and missing values; other formats have an empty salinity frame.
    """
    import pandas as pd
    from py_ewr import data_inputs
    from py_ewr import scenario_handling as sc

    if not math.isfinite(tds_factor) or tds_factor <= 0:
        raise ValueError("TDS-to-EC factor must be a finite positive number")

    default_sheet = None  # py_ewr's own parameter sheet
    if fmt in ("Bigmod - MDBA", "FIRM - MDBA"):
        data, header = sc.unpack_model_file(path, "Dy", "Field")
        df_clean = sc.cleaner_MDBA(sc.build_MDBA_columns(data, header))
        df_F, df_L, _ = sc.match_MDBA_nodes(df_clean, data_inputs.get_MDBA_codes(fmt), default_sheet)
    elif fmt == "res.csv - MDBA":
        df_F, df_L, _ = sc.cleaner_res_csv_MDBA(sc.unpack_MDBA_res_csv_file(path, "Date", "Field"), default_sheet)
    elif fmt == "Standard time-series":
        raw = pd.read_csv(path, index_col="Date")
        df_F, df_L, _ = sc.cleaner_standard_timeseries(raw, default_sheet)
    elif fmt == "Source - NSW (res.csv)":
        data, header = sc.unpack_model_file(path, "Date", "Field")
        df_clean = sc.cleaner_NSW(sc.build_NSW_columns(data, header))
        df_F, df_L = sc.match_NSW_nodes(df_clean, data_inputs.get_NSW_codes(), default_sheet)
    elif fmt == "IQQM - netcdf":
        df_F, df_L = sc.cleaner_netcdf_werp(sc.unpack_netcdf_as_dataframe(path), data_inputs.get_iqqm_codes(), default_sheet)
    elif fmt == "ten thousand year":
        raw = pd.read_csv(path, index_col="Date")
        df_F, df_L = sc.cleaner_ten_thousand_year(raw, default_sheet)
    elif fmt == "Northern connectivity":
        from ewr_analysis import northern_connectivity as nc

        scenarios = nc.unpack_northern_connectivity(path)  # one workbook, several scenarios: pick one by its code
        key = next((k for k in scenarios if k.lower() == sub.lower()), None) if sub else next(iter(scenarios), None)
        if key is None:
            raise ValueError(f"scenario {sub!r} not found in the workbook; available: {', '.join(scenarios)}")
        df_F, df_L = nc.cleaner_northern_connectivity(scenarios[key], default_sheet)
    else:
        raise ValueError(f"unknown data format {fmt!r}")

    # only these two formats can carry salinity columns
    df_S = _salinity_frame(raw, df_F.index, fmt, tds_factor) if fmt in ("Standard time-series", "ten thousand year") else pd.DataFrame()
    df_S.index.name = "Date"
    return df_F, df_L, df_S


def _salinity_frame(raw: Any, flow_index: Any, fmt: str, tds_factor: float) -> Any:
    """The *_salinity / *_ec / *_tds columns of a raw upload as one EC frame, one column per gauge, on the flow frame's dates."""
    import pandas as pd

    salinity_cols = [c for c in raw.columns if c.endswith(("_salinity", "_ec", "_tds"))]
    gauges = [c.rsplit("_", 1)[0] for c in salinity_cols]
    if len(set(gauges)) != len(gauges):
        raise ValueError("Provide only one salinity column per gauge (_salinity, _ec or _tds)")

    # Build the whole block once, rather than inserting columns repeatedly in a large frame.
    df_S = raw[salinity_cols].apply(pd.to_numeric, errors="raise")
    if fmt == "Standard time-series":
        # the standard format accepts either date style, as py_ewr's cleaner does
        try:
            df_S.index = pd.to_datetime(df_S.index, format="%d/%m/%Y")
        except ValueError:
            df_S.index = pd.to_datetime(df_S.index, format="%Y-%m-%d")
        df_S = df_S.reindex(flow_index)
    else:
        df_S.index = flow_index

    tds_cols = [c for c in salinity_cols if c.endswith("_tds")]
    if tds_cols:
        df_S[tds_cols] = df_S[tds_cols] * tds_factor
        df_S.attrs["tds_factor"] = tds_factor
    df_S.columns = gauges
    return df_S


_POOL: dict[str, Any] = {}  # per worker process: the frames and parameter sheet shared by every gauge (set once by _pool_init)


def _pool_init(df_F: Any, df_L: Any, table: Any, calc_config: Any) -> None:
    """Store flow and level frames, parameters, and calculation config for gauge evaluation in this process."""
    _POOL.update(df_F=df_F, df_L=df_L, table=table, calc_config=calc_config)


def _eval_gauge(gauge: Any, df_F: Any = None, df_L: Any = None, table: Any = None, calc_config: Any = None) -> tuple[Any, Any, str | None]:
    """Evaluate one gauge using explicit inputs, or the worker's _POOL defaults, returning (gauge, result, error)."""
    from py_ewr import evaluate_EWRs

    try:
        return (
            gauge,
            evaluate_EWRs.calc_sorter(
                df_F if df_F is not None else _POOL["df_F"],
                df_L if df_L is not None else _POOL["df_L"],
                gauge,
                table if table is not None else _POOL["table"],
                calc_config if calc_config is not None else _POOL["calc_config"],
            ),
            None,
        )
    except Exception as e:
        return gauge, None, f"{type(e).__name__}: {e}"


def evaluate_gauges(
    df_F: Any, df_L: Any, table: Any, calc_config: Any, progress: Callable[[str, int, int], None]
) -> tuple[dict[Any, Any], dict[Any, Any], dict[Any, str]]:
    """calc_sorter for every gauge in the file, one process per gauge (the gauges are independent and this is most of the run time).
    Falls back to one gauge at a time when worker processes cannot be started. Returns (results, events, skipped) by gauge."""
    gauges = sorted(set(df_F.columns) | set(df_L.columns))
    outcomes: dict[Any, tuple[Any, str | None]] = {}  # gauge -> (calc_sorter's (results, events), error)
    progress("Evaluating gauges", 0, len(gauges))

    workers = os.cpu_count() or 1
    if len(gauges) > 1 and workers > 1:
        try:
            with ProcessPoolExecutor(min(len(gauges), workers), initializer=_pool_init, initargs=(df_F, df_L, table, calc_config)) as pool:
                for future in as_completed([pool.submit(_eval_gauge, g) for g in gauges]):
                    gauge, result, error = future.result()
                    outcomes[gauge] = (result, error)
                    progress("Evaluating gauges", len(outcomes), len(gauges))
        except (BrokenProcessPool, OSError):
            pass  # finish whatever the pool did not, one gauge at a time below

    for gauge in [g for g in gauges if g not in outcomes]:
        _, result, error = _eval_gauge(gauge, df_F, df_L, table, calc_config)
        outcomes[gauge] = (result, error)
        progress("Evaluating gauges", len(outcomes), len(gauges))

    evaluated = {g: result for g in gauges if (result := outcomes[g][0]) is not None}
    results = {g: result[0] for g, result in evaluated.items()}
    events = {g: result[1] for g, result in evaluated.items()}
    return results, events, {g: error for g in gauges if (error := outcomes[g][1]) is not None}


def summarise_rows(
    results: dict,
    events: dict,
    df_F: Any,
    df_L: Any,
    table: Any,
    sheet_path: str | None = None,
    series_override: dict | None = None,
    report: Callable[[str, int, int], None] | None = None,
    handler_map: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Turn py_ewr statistics and events into dashboard rows, using the supplied parameter table and sheet path.

    sheet_path is also passed to ScenarioHandler's file-based parameter joins (None uses py_ewr's default sheet).
    handler_map overrides stock dispatch keys; series_override supplies gauge series for gap/completeness metrics, e.g. salinity.
    """
    import numpy as np
    import pandas as pd
    from py_ewr.scenario_handling import ScenarioHandler

    report = report if report is not None else (lambda stage, done, total: None)
    handlers = diagnostic_handlers() if handler_map is None else handler_map

    sh = ScenarioHandler("scenario.csv", "Standard time-series", parameter_sheet=sheet_path)
    sh.pu_ewr_statistics, sh.yearly_events, sh.flow_data, sh.level_data = {"scenario": results}, {"scenario": events}, df_F, df_L
    ewr_table = sh.get_ewr_results().copy()
    frequency = pd.to_numeric(ewr_table["Frequency"], errors="coerce")
    target = pd.to_numeric(ewr_table["TargetFrequency"], errors="coerce")
    ewr_table["FR"] = np.where(target > 0, frequency / target, np.nan)

    # ETP and gap severity come from the same gap list as the flow charts (ewr_gaps)
    has_events = _has_any_event(events)
    no_gaps = pd.DataFrame(columns=["startDate", "endDate", "interEventLength"])
    inter_by_ewr = _group_by_ewr(_successful_summary(sh.get_all_successful_interEvents, has_events, no_gaps))
    succ_by_ewr = _group_by_ewr(_successful_summary(sh.get_all_successful_events, has_events, no_gaps))

    # each gauge's daily series: flow, else level, unless the caller supplies its own (e.g. salinity)
    flow_names = {str(g) for g in df_F.columns}
    series = {str(g): df_F[g] for g in df_F.columns} | {str(g): df_L[g] for g in df_L.columns if str(g) not in flow_names}
    if series_override is not None:
        series.update({str(g): values for g, values in series_override.items()})
    daily_by_gauge: dict[str, Any] = {}

    yearly_by_gauge = {str(g): v for g, v in results.items()}
    events_by_gauge = {str(g): v for g, v in events.items()}
    params_by_ewr = {(str(p.Gauge), p.PlanningUnitName, p.Code): p for p in table.drop_duplicates(["Gauge", "Code", "PlanningUnitName"]).itertuples()}

    gap_stats: dict[tuple[str, str, str], dict[str, Any]] = {}
    for i, r in enumerate(ewr_table.itertuples()):
        if i % 5 == 0:
            report("Summarising results", i, len(ewr_table))
        key = (str(r.Gauge), r.PlanningUnit, r.Code)
        gauge, pu, code = key
        mrip = _mrip_days(pd.to_numeric(r.MaxInterEventYears, errors="coerce"))
        if gauge not in daily_by_gauge:
            daily_by_gauge[gauge] = record_days(series.get(gauge))
        daily = daily_by_gauge[gauge]

        if daily is None or daily.empty:
            gap_stats[key] = _dateless_gap_stats(key, pd.to_numeric(r.TotalDays, errors="coerce"), mrip, inter_by_ewr, succ_by_ewr)
            continue

        gaps, etp = ewr_gaps({"flow": daily, "mrip_days": mrip, "inter": inter_by_ewr.get(key, no_gaps), "succ": succ_by_ewr.get(key, no_gaps)})
        gap_stats[key] = {"et": etp, **gap_severity(gaps, mrip)}

        params, yearly = params_by_ewr.get(key), yearly_by_gauge.get(gauge, {}).get(pu)
        if params is None or yearly is None or f"{code}_eventYears" not in yearly:
            continue
        ewr_events = events_by_gauge.get(gauge, {}).get(pu, {}).get(code)
        use_days = sheet_path is not None  # only a user sheet sets StartDay / EndDay
        gap_stats[key] |= _assessment_year_stats(code, params, yearly, ewr_events, daily, gaps, mrip, r.TargetFrequency, handlers, use_days)

    ewr_table["ETP"] = [gap_stats.get((str(g), pu, c), {}).get("et") for g, pu, c in zip(ewr_table.Gauge, ewr_table.PlanningUnit, ewr_table.Code, strict=True)]
    return [
        {
            "pu": r.PlanningUnit,
            "gauge": str(r.Gauge),
            "ewr": r.Code,
            "fr": None if pd.isna(r.FR) else round(float(r.FR), 4),
            "et": None if r.ETP is None or pd.isna(r.ETP) else round(float(r.ETP), 4),
            "ey": None if pd.isna(pd.to_numeric(r.EventYears, errors="coerce")) else int(r.EventYears),  # achieved years
            "ny": round(float(r.TotalDays) / 365.25, 2) if pd.to_numeric(r.TotalDays, errors="coerce") > 0 else None,  # record length in years
            # gap severity (gap_severity), median duration ratio and near misses (year_summary), drought windows (window_metrics)
            **{s: gap_stats.get((str(r.Gauge), r.PlanningUnit, r.Code), {}).get(s) for s in ("sev", "sev_b", "eps", "dr", "nm", "win")},
        }
        for r in ewr_table.itertuples()
    ]


def _has_any_event(events: dict) -> bool:
    """True when any EWR at any gauge has at least one threshold event (events: gauge -> pu -> code -> year -> event list)."""
    return any(len(lst) > 0 for by_pu in events.values() for by_code in by_pu.values() for by_year in by_code.values() for lst in by_year.values())


def _successful_summary(fetch: Callable[[], Any], has_events: bool, empty: Any) -> Any:
    """One of py_ewr's successful-event summaries, or `empty` where py_ewr cannot build one.

    py_ewr's summaries fail with KeyError('Gauge') when no EWR in the file has any event at all (e.g. a completely dry scenario),
    and the same way when events exist but none succeeded (all shorter than MinSpell). Either way there are no successful
    events and so no gaps, which `empty` represents (as ewr_eval does).
    """
    if not has_events:
        return empty
    try:
        return fetch()
    except KeyError as exc:
        if exc.args != ("Gauge",):
            raise
        return empty


def _group_by_ewr(table: Any) -> dict[tuple[str, str, str], Any]:
    """Split a py_ewr event summary into one frame per (gauge, planning unit, EWR code)."""
    if not len(table) or "Gauge" not in table:
        return {}
    return {(str(gauge), pu, code): group for (gauge, pu, code), group in table.groupby(["Gauge", "PlanningUnit", "Code"])}


def _dateless_gap_stats(key: tuple[str, str, str], record_days_total: Any, mrip: int, inter_by_ewr: dict, succ_by_ewr: dict) -> dict[str, Any]:
    """ETP and gap severity for a run with no dates (ten-thousand-year runs): py_ewr's gap lengths, or the whole record as one
    gap when nothing succeeded."""
    import pandas as pd

    succeeded = key in succ_by_ewr
    if not succeeded:
        gap_lengths = [record_days_total]
    elif key in inter_by_ewr:
        gap_lengths = pd.to_numeric(inter_by_ewr[key].interEventLength, errors="coerce").dropna().tolist()
    else:
        gap_lengths = []

    countable = mrip and record_days_total > 0
    etp = round(min(1.0, sum(max(0.0, x - mrip) for x in gap_lengths) / record_days_total), 4) if countable else None
    gaps = [{"days": record_days_total, "boundary": True, "censored": False, "excess": max(0, record_days_total - mrip)}] if not succeeded and countable else []
    return {"et": etp, **gap_severity(gaps, mrip)}


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


def run_scenario(
    csv_bytes: bytes,
    sdl_gauges: list[str],
    scn_id: str | None = None,
    fmt: str = "Standard time-series",
    filename: str = "",
    sub: str = "",
    merge: bool = False,
    progress: Callable[[str, int, int], None] | None = None,
    tds_factor: float = 0.6,
    storage_gauges: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """py_ewr on the uploaded file (only the gauges the SDL unit needs); returns (rows, stats). `progress(stage, done, total)`
    is called as the run advances."""
    report = progress or (lambda stage, done, total: None)
    report("Reading the file", 0, 1)
    from py_ewr import data_inputs

    if fmt not in FORMATS:
        raise ValueError(f"unknown data format {fmt!r}")
    table = parameter_sheet()
    # partner gauges (multigauge and weir pool EWRs) are read too, though only the review gauges are reported
    wanted = set(sdl_gauges) | _partner_gauges(table[table.Gauge.isin(sdl_gauges)])

    df_F, df_L, df_S = _read_upload(csv_bytes, fmt, filename, sub, tds_factor)
    df_F = df_F[[g for g in df_F.columns if str(g) in wanted]]
    df_L = df_L[[g for g in df_L.columns if str(g) in wanted]]
    df_S = df_S[[g for g in df_S.columns if str(g) in sdl_gauges]]
    if df_F.shape[1] + df_L.shape[1] + df_S.shape[1] == 0:
        raise ValueError(f"No columns in this '{fmt}' file match gauges used in this review.")
    found = sorted({str(g) for df in (df_F, df_L, df_S) for g in df.columns} & set(sdl_gauges))

    # A gauge that cannot be evaluated (e.g. level EWRs with no level data in the file) is skipped and reported
    # instead of stopping the whole run.
    skipped: dict[Any, str]
    series_only = merge and df_F.shape[1] == 0
    if series_only:
        if not scn_id or not any((STORE / f"{scn_id}{series_suffix}.csv").exists() for series_suffix in SERIES_SUFFIX.values()):
            raise ValueError("A level/salinity-only merge needs an existing stored scenario")
        # Supplemental series have no built-in flow results to replace; user EWR evaluation comes later.
        rows, skipped = [], {}
    else:
        calc_config = data_inputs.get_ewr_calc_config(None)
        results, events, skipped = evaluate_gauges(df_F, df_L, table, calc_config, report)
        if not results:
            raise ValueError("No gauge in the file could be evaluated: " + "; ".join(f"{g} ({m})" for g, m in skipped.items()))
        rows = summarise_rows(results, events, df_F, df_L, table, report=report)
    stored = _store_series(scn_id, {"flow": df_F, "level": df_L, "salinity": df_S}, storage_gauges or sdl_gauges, merge)
    stats = {
        "gauges_in_file": len(found),
        "gauges_requested": len(sdl_gauges),
        "ewr_rows": len(rows),
        "missing_gauges": sorted(set(sdl_gauges) - set(found)),
        "skipped": skipped,
        "flow_gauges": stored["flow"],
        "level_gauges": stored["level"],
        "salinity_gauges": stored["salinity"],
        "format": fmt,
    }
    if "tds_factor" in df_S.attrs:
        stats["tds_factor"] = df_S.attrs["tds_factor"]
    return rows, stats


def _partner_gauges(rows: Any) -> set[str]:
    """Gauges that parameter-sheet rows name as a multigauge or weir pool partner."""
    partners: set[str] = set()
    for column in ("Multigauge", "WeirpoolGauge"):
        partners |= {g for g in rows[column].dropna().astype(str) if g.strip()}
    return partners


def _read_upload(body: bytes, fmt: str, filename: str, sub: str, tds_factor: float) -> tuple[Any, Any, Any]:
    """load_frames on an uploaded file (written to a temporary file, as py_ewr's readers need a path), with a friendly error."""
    suffix = Path(filename).suffix.lower() or (".nc" if fmt == "IQQM - netcdf" else ".csv")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"scenario{suffix}"
        path.write_bytes(body)
        try:
            return load_frames(fmt, str(path), sub, tds_factor)
        except Exception as e:  # py_ewr's own readers fail in obscure ways on the wrong format
            detail = str(e).strip().splitlines()[0][:160] if str(e).strip() else type(e).__name__
            raise ValueError(f"Could not read this file as '{fmt}'. Check that you chose the right data format. ({type(e).__name__}: {detail})") from e


def _store_series(scn_id: str | None, frames: dict[str, Any], gauges: list[str], merge: bool) -> dict[str, list[str]]:
    """Store each calendar-dated series block once; merges keep other gauges and replace overlapping columns.

    Replacements also remove omitted series files, so old levels/salinity cannot outlive a replaced scenario.
    Non-calendar (ten-thousand-year) frames retain the existing no-storage behaviour.
    """
    import pandas as pd

    stored: dict[str, list[str]] = {kind: [] for kind in SERIES_SUFFIX}
    if not scn_id:
        return stored
    STORE.mkdir(exist_ok=True)
    for kind, frame in frames.items():
        path = STORE / f"{scn_id}{SERIES_SUFFIX[kind]}.csv"
        cols = [g for g in frame.columns if str(g) in gauges]
        if cols and isinstance(frame.index, pd.DatetimeIndex):
            new = frame[cols]
            if merge and path.exists():
                old = pd.read_csv(path, index_col=0, parse_dates=True)
                new = old.drop(columns=old.columns.intersection(new.columns)).join(new, how="outer")
            new.to_csv(path)
            stored[kind] = sorted(map(str, cols))
        elif not merge:
            path.unlink(missing_ok=True)
    forget_cached(scn_id)
    return stored


def worker(
    job_id: str,
    body: bytes,
    gauges: list[str],
    scn_id: str | None = None,
    fmt: str = "Standard time-series",
    filename: str = "",
    sub: str = "",
    merge: bool = False,
    tds_factor: float = 0.6,
) -> None:
    """Run a scenario job, recording progress, results or errors, and elapsed time in JOBS."""
    job = JOBS[job_id]
    try:
        job["rows"], job["stats"] = run_scenario(body, gauges, scn_id, fmt, filename, sub, merge, _progress_reporter(job), tds_factor)
        job["status"] = "done"
    except Exception as e:  # reported to the page
        _record_job_error(job, e)
    job["elapsed"] = round(time.time() - job["t0"], 1)


def observed_worker(job_id: str, sdl: str, gauges: list[str], start: Any, end: Any, refresh: bool, merge: bool, filter_period: bool = True) -> None:
    """Fetch observed flow/level series, then evaluate them through the standard upload path."""
    import pandas as pd

    job = JOBS[job_id]
    scn_id = _observed_scenario_id(sdl)
    try:
        table = parameter_sheet()
        partner_rows = table[table.Gauge.astype(str).isin(gauges)]
        all_gauges = sorted(set(gauges) | _partner_gauges(partner_rows))

        measurement_lock = threading.RLock()  # in-flight retry callbacks arrive from worker threads

        def _measurement_payload(result: observed.GaugeResult) -> dict:
            """Serialize a measurement result for streamed and final job responses."""
            # Legacy keys (status, message, retryable) stay; `error` is the additive envelope.
            return {
                "status": result.status,
                "message": result.message,
                "retryable": result.retryable,
                "error": result.error,
                "attempts": result.attempts,
                "max_attempts": observed.MAX_ATTEMPTS,
                "schema_version": 2,
                "coverage": result.coverage,  # null while a measurement is still running/retrying
            }

        def report_gauge(gauge: str, var: str, result: observed.GaugeResult) -> None:
            """Update one measurement in job progress under the shared measurement lock."""
            with measurement_lock:
                progress = dict(job.get("progress", {}))
                gauge_progress = dict(progress.get("gauges", {}))
                per_gauge = dict(gauge_progress.get(gauge, {}))
                per_gauge[var.lower()] = _measurement_payload(result)
                gauge_progress[gauge] = per_gauge
                progress["gauges"] = gauge_progress
                job["progress"] = progress

        report_stage = _progress_reporter(job, stage_code="gauges")

        def report_progress(*args) -> None:
            """Record gauge-stage counts under the shared measurement lock."""
            with measurement_lock:
                report_stage(*args)

        _progress_reporter(job, stage_code="gauges")(observed.GAUGE_STAGE, 0, len(all_gauges))
        results, statuses = observed.fetch_many(
            all_gauges,
            start,
            end,
            progress=report_progress,
            refresh=refresh,
            status_progress=report_gauge,
            inflight_progress=report_gauge,
            policy=observed.default_quality_policy(),
            filter_period=filter_period,
            reporting_cutoff=date.today() - timedelta(days=1),  # today's daily mean is not complete yet; disclosed in coverage.period
        )
        job["gauge_status"] = {
            gauge: {var.lower(): _measurement_payload(result) for (g, var), result in statuses.items() if g == gauge} for gauge in all_gauges
        }
        if not results:
            raise ValueError("No selected gauges returned flow or level data")
        observed_ranges: dict[str, dict[str, str]] = {}
        for (gauge, _var), result_frame in results.items():
            if result_frame.empty or "DATETIME" not in result_frame:
                continue
            dates = pd.to_datetime(result_frame["DATETIME"], errors="coerce").dropna()
            if dates.empty:
                continue
            record = observed_ranges.setdefault(gauge, {"start": dates.min().date().isoformat(), "end": dates.max().date().isoformat()})
            record["start"] = min(record["start"], dates.min().date().isoformat())
            record["end"] = max(record["end"], dates.max().date().isoformat())
        frame = observed.to_standard_frame(results)
        body = frame.to_csv(index=False).encode("utf-8")
        rows, stats = run_scenario(
            body,
            gauges,
            scn_id,
            "Standard time-series",
            f"Observed - {sdl}",
            merge=merge,
            progress=_progress_reporter(job),
            storage_gauges=all_gauges,
        )
        stats["gauge_status"] = job["gauge_status"]
        stats["observed_sdl"] = sdl
        stats["observed_ranges"] = observed_ranges
        stats["partial_water_years"] = {
            "start": filter_period and (start.month, start.day) != (7, 1),
            "end": filter_period and (end.month, end.day) != (6, 30),
        }
        job["rows"], job["stats"], job["status"] = rows, stats, "done"
    except Exception as exc:
        _record_job_error(job, exc)
    finally:
        job["elapsed"] = round(time.time() - job["t0"], 1)
        with OBSERVED_ACTIVE_LOCK:
            if OBSERVED_ACTIVE.get(scn_id) == job_id:
                OBSERVED_ACTIVE.pop(scn_id, None)


def _observed_scenario_id(sdl: str) -> str:
    return "obs_" + re.sub(r"[^a-z0-9]+", "_", sdl.lower()).strip("_")[:48]


def observed_units() -> list[dict[str, Any]]:
    """Local-only observed selector data; no upstream request is made."""
    import pandas as pd

    params = parameter_sheet()
    station_path = ROOT / "data/map/bom_gauge_stations.csv"
    stations = pd.read_csv(station_path, dtype=str).fillna("")
    names = {row.station_no.strip(): row.station_name.strip().strip('"') for row in stations.itertuples() if row.station_no.strip()}
    units = []
    for sdl in sorted(params.SWSDLName.dropna().astype(str).str.strip().unique()):
        gauges = observed.sdl_gauges(sdl)
        if not gauges:
            continue
        units.append({"sdl": sdl, "gauges": [{"id": gauge, "name": names.get(gauge, "")} for gauge in gauges]})
    return units


def uewr_worker(job_id: str, ids: list[str], scns: list[str]) -> None:
    """Evaluate saved user EWRs via the existing job/polling contract."""
    from ewr_analysis import custom_ewr

    job = JOBS[job_id]
    try:
        custom_ewr.publish_job(job, custom_ewr.run_job(ids, scns, _progress_reporter(job)))
    except Exception as exc:
        _record_job_error(job, exc)
    job["elapsed"] = round(time.time() - job["t0"], 1)


def _progress_reporter(job: dict[str, Any], stage_code: str | None = None) -> Callable[[str, int, int], None]:
    """A progress(stage, done, total) callback that records the job's current stage and work counts for /api/job."""

    def progress(stage: str, done: int, total: int) -> None:
        """Replace stage counts while preserving any recorded measurement progress."""
        current = job.get("progress", {})
        job["progress"] = {
            "stage": stage,
            "done": done,
            "total": total,
            **({"stage_code": stage_code} if stage_code else {}),
            **({"gauges": current["gauges"]} if "gauges" in current else {}),
        }

    return progress


def _record_job_error(job: dict[str, Any], exc: Exception) -> None:
    job["status"], job["message"] = "error", f"{type(exc).__name__}: {exc}"
    traceback.print_exc()


# ---- flow analysis on stored scenario flows -------------------------------------------------------------------------------
def load_series(scn_id: str | None, kind: str = "flow") -> Any:
    """Load one stored series kind, caching each (scenario, kind) independently."""
    import pandas as pd

    if kind not in SERIES_SUFFIX:
        raise ValueError(f"unknown series kind {kind!r}")
    scn_id = _safe_id(scn_id)
    key = (scn_id, kind)
    if key not in FLOWS:
        path = STORE / f"{scn_id}{SERIES_SUFFIX[kind]}.csv"
        if not path.exists():
            raise ValueError("the time series for this scenario is not on the server (re-upload it)")
        df = pd.read_csv(path, index_col=0, parse_dates=True).sort_index()
        # one row per day: the completeness checks count rows, so a repeated date would pass for a missing one. Exact repeats are
        # dropped; a date given twice with different flows is an error rather than a silent pick.
        dup = df.index.duplicated(keep=False)
        if dup.any():
            if (df[dup].groupby(level=0).nunique(dropna=False) > 1).any().any():
                raise ValueError(f"the stored time series has dates repeated with different {kind}s (re-upload it)")
            df = df[~df.index.duplicated()]
        FLOWS[key] = df
    return FLOWS[key]


def load_flows(scn_id: str | None) -> Any:
    """Compatibility wrapper for existing flow charts."""
    return load_series(scn_id, "flow")


def forget_cached(scn_id: str) -> None:
    """Invalidate all series and evaluation caches after a scenario is replaced, merged or deleted; keep stored files."""
    for kind in SERIES_SUFFIX:
        FLOWS.pop((scn_id, kind), None)
    for k in [k for k in _EVAL_CACHE if k[0] == scn_id]:
        _EVAL_CACHE.pop(k, None)


def daily_series(scn_id: str, gauge: str) -> Any:
    """A gauge's flows on every calendar day from its first to its last observed day; days with no flow stay NaN (never filled)."""
    import pandas as pd

    s = gauge_series(scn_id, gauge)
    s = s.loc[s.first_valid_index() : s.last_valid_index()] if s.notna().any() else s.iloc[:0]
    if s.empty:
        raise ValueError(f"gauge {gauge} has no flows in this scenario")
    return s.reindex(pd.date_range(s.index[0], s.index[-1]))


def gauge_series(scn_id: str, gauge: str, kind: str = "flow") -> Any:
    """One gauge's stored series of the given kind, as stored (only observed days)."""
    df = load_series(scn_id, kind)
    if gauge not in df.columns:
        raise ValueError(f"gauge {gauge} has no {kind} series in this scenario")
    return df[gauge]


def nice_ceiling(x: float) -> int:
    """Round x up to two significant figures, and at least to a whole number (1234 -> 1300, 57.2 -> 58), for a tidy axis limit; 100 when x is not positive."""
    if not x or x <= 0:
        return 100
    k = 10 ** max(int(math.floor(math.log10(x))) - 1, 0)
    return int(math.ceil(x / k) * k)


def _json_safe(obj: Any) -> Any:
    """Recursively replace non-finite floats (NaN, +-inf) with None so the response is strict JSON (null in the browser)."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def _window_days(wy_label: int, sm: int, em: int) -> int:
    """Calendar days of the timing window (months sm..em, may wrap the year end) inside water year `wy_label` (1 Jul - 30 Jun)."""
    import pandas as pd

    m = pd.date_range(f"{wy_label - 1}-07-01", f"{wy_label}-06-30").month.to_numpy()
    return int(((m >= sm) & (m <= em) if sm <= em else (m >= sm) | (m <= em)).sum())


def achievement_frequencies(flows: Any, index: Any, thresholds: Any, durations: Any, sm: int, em: int, consecutive: bool = False) -> tuple[Any, int]:
    """% of water years in which each threshold is exceeded for at least `duration` days within the timing window.
    flows: (days, scenarios). Same result as the numba reference (non-consecutive days): the d-th largest in-window flow of a year
    is the highest threshold that is exceeded on d days of that year.  consecutive=True requires the d days to be consecutive (the
    reference's calc_annual_max_threshold_consecutive: the largest, over windows of d days, of the window minimum).
    returns (scenarios, thresholds, durations)."""
    import numpy as np

    months, calendar_years = index.month.to_numpy(), index.year.to_numpy()
    wy = np.where(months >= 7, calendar_years + 1, calendar_years)  # labelled by the year the water year ends in
    year_lookup = np.unique(wy, return_inverse=True)[1]
    n_years = year_lookup.max() + 1
    mask = (months >= sm) & (months <= em) if sm <= em else (months >= sm) | (months <= em)
    window_flows = np.where(mask[:, None] & ~np.isnan(flows), flows, -np.inf)
    bounds = np.searchsorted(year_lookup, np.arange(n_years + 1))  # each year's first row (the index is in date order)
    # Denominator: only water years whose timing window is fully observed count. A water year is complete for a scenario when
    # every calendar day of the window (Jul-Jun water year, as above) is in the index with a non-NaN flow. Partial first/last
    # years and years with no (or incomplete) data are excluded rather than scored as failures; a complete year that simply
    # stays below the threshold is still an unsuccessful year.
    # All scenarios use the SAME years: the intersection of every scenario's complete years, so base/comparison rates (and
    # their difference) are paired over one year set. That shared count is returned as the number of years. No shared complete
    # year -> NaN. (No separate cap on the duration is needed: days outside the window are -inf, so a duration longer than
    # the window's days in a year can never be met, in either mode.)
    expected = np.array([_window_days(wl, sm, em) for wl in np.unique(wy)])
    complete = np.stack(
        [(np.bincount(year_lookup, weights=mask & ~np.isnan(flows[:, s]), minlength=n_years) == expected) & (expected > 0) for s in range(flows.shape[1])]
    )
    counted = complete.all(axis=0)
    n_counted = int(counted.sum())
    out = np.zeros((flows.shape[1], len(thresholds), len(durations)), dtype=np.float32)
    for s in range(flows.shape[1]):
        # annual[k, y]: the highest threshold that year y's flows exceed for durations[k] days
        annual = np.full((len(durations), n_years), -np.inf)
        for y in np.flatnonzero(counted):
            year_flows = window_flows[bounds[y] : bounds[y + 1], s]
            if consecutive:
                # sliding-window minimum grown one day at a time: run_min[i] = min(year_flows[i : i + width])
                run_min, width = year_flows.copy(), 1
                for k, d in enumerate(durations):
                    while width < d and len(run_min) > 1:
                        run_min, width = np.minimum(run_min[:-1], year_flows[width:]), width + 1
                    if d <= len(year_flows) and width == d and len(run_min):
                        annual[k, y] = run_min.max()
            else:
                descending = np.sort(year_flows)[::-1]
                for k, d in enumerate(durations):
                    if d <= len(descending):
                        annual[k, y] = descending[d - 1]
        for k in range(len(durations)):
            if not n_counted:
                out[s, :, k] = np.nan
                continue
            n_unsuccessful = np.searchsorted(np.sort(annual[k][counted]), thresholds, side="left")
            out[s, :, k] = (n_counted - n_unsuccessful) * 100.0 / n_counted
    return out, n_counted


_EWR_CACHE: dict[str, Any] = {}
_EWR_LOCK = threading.RLock()


def parameter_sheet():
    """The EWR tool's parameter sheet as read from py_ewr, in a private copy. py_ewr's own get_EWR_table() returns one cached
    table that its ScenarioHandler later edits in place (get_all_successful_interEvents writes 0 into blank TargetFrequency
    cells), so reading EWR definitions from it would depend on what ran before."""
    from py_ewr import data_inputs

    if "table" not in _EWR_CACHE:
        read = getattr(data_inputs.get_EWR_table, "__wrapped__", data_inputs.get_EWR_table)  # bypass py_ewr's cache
        _EWR_CACHE["table"] = read(None).copy(deep=True)
    return _EWR_CACHE["table"]


def _builtin_ewr_list(gauge: str) -> list[dict[str, Any]]:
    """EWRs at a gauge that the EWR tool evaluates with flow_handle (consecutive days) or lowflow_handle (non-consecutive days)."""
    import pandas as pd

    validate_diagnostic_gauge(gauge)
    handlers = diagnostic_handlers()
    t = parameter_sheet()
    required = {
        "Gauge",
        "Code",
        "PlanningUnitName",
        "State",
        "Multigauge",
        "FlowLevelVolume",
        "MaxInter-event",
        "FlowThresholdMin",
        "Duration",
        "StartMonth",
        "EndMonth",
        "TargetFrequency",
    }
    if missing := required - set(t.columns):
        raise ValueError(f"EWR parameter table missing required columns: {', '.join(sorted(missing))}")
    t = t[t.Gauge.astype(str) == gauge]
    # where a (code, planning unit) pair has several rows, the last row's maximum inter-event period applies to all of them
    max_inter = {(c, pu): v for c, pu, v in zip(t.Code, t.PlanningUnitName, pd.to_numeric(t["MaxInter-event"], errors="coerce"), strict=True)}

    def number(value: Any) -> Any:
        return pd.to_numeric(value, errors="coerce")

    entries = []
    for r in t.itertuples():
        multi = _is_multigauge(r.Multigauge)
        handler = handlers.get(_dispatch_key(r.Code, multi, r.FlowLevelVolume))
        thr, dur, sm, em, target = number(r.FlowThresholdMin), number(r.Duration), number(r.StartMonth), number(r.EndMonth), number(r.TargetFrequency)
        if handler not in ("flow_handle", "lowflow_handle") or multi or pd.isna(thr) or pd.isna(dur) or pd.isna(sm) or pd.isna(em):
            continue
        maxint = max_inter.get((r.Code, r.PlanningUnitName))
        entries.append(
            {
                "code": r.Code,
                "pu": r.PlanningUnitName,
                "state": r.State,
                "handler": handler,
                "consecutive": handler == "flow_handle",
                "thr": float(thr),
                "dur": int(dur),
                "sm": int(sm),
                "em": int(em),
                "target": None if pd.isna(target) else float(target),
                "maxint": 0.0 if maxint is None or pd.isna(maxint) else float(maxint),
            }
        )

    # the same EWR is often defined in several planning units with identical settings: list it once
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for e in sorted(entries, key=lambda e: (e["code"], e["pu"])):
        settings = (e["code"], e["thr"], e["dur"], e["sm"], e["em"], e["target"], e["maxint"], e["handler"])
        if settings in unique:
            unique[settings]["pus"].append(e["pu"])
        else:
            unique[settings] = {**e, "pus": [e["pu"]]}
    return sorted(unique.values(), key=lambda e: (e["thr"], e["code"], e["pu"]))  # increasing flow threshold, i.e. increasing flow regime


def ewr_list(gauge: str) -> list[dict[str, Any]]:
    """Stock flow rules plus every user EWR kind, merged afresh outside the immutable diagnostics cache."""
    from ewr_analysis import custom_ewr

    return _builtin_ewr_list(gauge) + custom_ewr.ewr_list(gauge)


def validate_diagnostic_gauge(gauge: str) -> None:
    """Accept bounded station identifiers; unknown well-formed gauges return empty inventories."""
    if not isinstance(gauge, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", gauge):
        raise ValueError("gauge must be a station identifier (1–64 letters, digits, underscores or hyphens)")


def diagnostic_handlers() -> dict[str, str]:
    """Cache the complete dispatch map once, including specialised/level/volume handlers."""
    from py_ewr import data_inputs

    with _EWR_LOCK:
        if "all_handlers" not in _EWR_CACHE:
            cfg = data_inputs.get_ewr_calc_config(None)
            _EWR_CACHE["all_handlers"] = {key: handler for handler, keys in cfg.items() for key in keys}
        return cast(dict[str, str], _EWR_CACHE["all_handlers"])


def _builtin_diagnostic_inventory(gauge: str) -> list[dict[str, Any]]:
    """Read all parameter rules directly, without relying on ewr_list's cache side effects."""
    validate_diagnostic_gauge(gauge)
    table = parameter_sheet()
    required = {"Gauge", "Code", "PlanningUnitName", "Multigauge", "FlowLevelVolume"}
    if missing := required - set(table.columns):
        raise ValueError(f"EWR parameter table missing required columns: {', '.join(sorted(missing))}")
    handlers = diagnostic_handlers()
    supported = {(e["code"], pu) for e in _builtin_ewr_list(gauge) for pu in e["pus"]}
    result = []
    for r in table[table.Gauge.astype(str) == gauge].itertuples():
        key = _dispatch_key(r.Code, _is_multigauge(r.Multigauge), r.FlowLevelVolume)
        result.append(
            {
                "code": r.Code,
                "pu": r.PlanningUnitName,
                "kind": r.FlowLevelVolume,
                "handler": handlers.get(key, "unmapped"),
                "detailed": (r.Code, r.PlanningUnitName) in supported,
            }
        )
    return result


def diagnostic_inventory(gauge: str) -> list[dict[str, Any]]:
    """Every EWR rule at a gauge, stock and user-defined, with its handler and whether detailed diagnostics are offered."""
    from ewr_analysis import custom_ewr

    return _builtin_diagnostic_inventory(gauge) + custom_ewr.inventory(gauge)


def diagnostic_payload(gauge: str) -> dict[str, Any]:
    """Cache stock diagnostics only; registry definitions are merged at request time."""
    from ewr_analysis import custom_ewr

    validate_diagnostic_gauge(gauge)
    with _EWR_LOCK:
        cache = _EWR_CACHE.setdefault("diagnostics", {})
        if gauge not in cache:
            inventory = _builtin_diagnostic_inventory(gauge)
            result = {"ewrs": _builtin_ewr_list(gauge), "inventory": inventory}
            if inventory:
                cache[gauge] = result
        else:
            result = cache[gauge]
    user = custom_ewr.ewr_list(gauge)
    return {"ewrs": result["ewrs"] + user, "inventory": result["inventory"] + custom_ewr.inventory(gauge, user)} if user else result


def record_coverage(flow: Any, complete: list[int]) -> dict[str, Any]:
    """Count nulls AND absent calendar dates; handle empty records without formatting NaT."""
    import pandas as pd

    if flow.empty:
        return {"start": None, "end": None, "missing_days": 0, "complete_years": sum(complete), "years": len(complete)}
    start, end = flow.index.min(), flow.index.max()
    daily = flow.reindex(pd.date_range(start, end))
    return {
        "start": start.strftime("%Y-%m-%d"),
        "end": end.strftime("%Y-%m-%d"),
        "missing_days": int(daily.isna().sum()),
        "complete_years": sum(complete),
        "years": len(complete),
    }


# (scenario id, gauge, code, planning unit[, user version]) -> one EWR evaluated by py_ewr (see ewr_eval)
_EVAL_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}


def _water_year(d: Any) -> int:
    """py_ewr's water year label: the calendar year it starts in (1 Jul Y - 30 Jun Y+1)."""
    return cast(int, d.year if d.month >= 7 else d.year - 1)


def ewr_eval(scn_id: str, gauge: str, code: str, pu: str = "") -> dict[str, Any]:
    """py_ewr on one EWR at one gauge of a stored scenario: its yearly statistics, every threshold event, the successful events and
    the gaps between successful events, exactly as py_ewr computes them (calc_sorter + ScenarioHandler). Missing days stay
    missing (py_ewr counts them per year in missingDays); nothing is filled in."""
    import pandas as pd
    from py_ewr.scenario_handling import ScenarioHandler

    from ewr_analysis import custom_ewr

    is_user_ewr = code.startswith("U_")
    spec = custom_ewr.find(code, gauge) if is_user_ewr else None
    if is_user_ewr and spec is None:
        raise ValueError(f"EWR {code} is not defined at gauge {gauge}")
    # a user EWR's cache entry is keyed by its version, so editing the definition never serves stale results
    key = (scn_id, gauge, code, pu, spec["ver"]) if spec is not None else (scn_id, gauge, code, pu)
    if key in _EVAL_CACHE:
        return _EVAL_CACHE[key]

    sheet_path = str(custom_ewr.merged_sheet_path([spec])) if spec is not None else None
    table = custom_ewr.read_sheet(sheet_path) if sheet_path is not None else parameter_sheet()
    matches = table[(table.Gauge.astype(str) == gauge) & (table.Code == code) & ((table.PlanningUnitName == pu) if pu else True)]
    if matches.empty:
        raise ValueError(f"EWR {code} is not defined at gauge {gauge}")
    ewr_row = matches.iloc[:1].copy()  # py_ewr gets its own copy to change
    pu = ewr_row.PlanningUnitName.iloc[0]

    if spec is not None:
        df_F, df_L, results, events, series, handlers = _calculate_user_ewr(scn_id, gauge, spec, ewr_row)
    else:
        df_F, df_L, results, events, series, handlers = _calculate_stock_ewr(scn_id, gauge, ewr_row)

    sh = ScenarioHandler("scenario.csv", "Standard time-series", parameter_sheet=sheet_path)
    sh.pu_ewr_statistics, sh.yearly_events, sh.flow_data, sh.level_data = {"scenario": {gauge: results}}, {"scenario": {gauge: events}}, df_F, df_L
    inter, succ = _successful_tables(sh, events[pu][code], gauge, code, pu)

    yearly = results[pu]
    yearly = yearly[[c for c in yearly.columns if c.startswith(code + "_")]].rename(columns=lambda c: c[len(code) + 1 :])

    def param(column: str) -> Any:
        return pd.to_numeric(ewr_row[column], errors="coerce").iloc[0]

    def optional_param(column: str, cast: Callable[[Any], Any]) -> Any:
        return None if pd.isna(param(column)) else cast(param(column))

    is_level = spec is not None and spec["kind"] == "L"
    out = {
        "pu": pu,
        "years": yearly,
        "events": events[pu][code],
        "flow": series,
        "succ": succ,
        "inter": inter,
        "mrip_days": _mrip_days(param("MaxInter-event")),
        "thr": float(param("LevelThresholdMin" if is_level else "FlowThresholdMin")),
        "dur": int(param("Duration")),
        "sm": int(param("StartMonth")),
        "em": int(param("EndMonth")),
        "target": optional_param("TargetFrequency", float),
        "min_spell": optional_param("MinSpell", int),
        "per_year": optional_param("EventsPerYear", int),
        "gap_tol": optional_param("WithinEventGapTolerance", int),
        "thr_max": optional_param("LevelThresholdMax" if is_level else "FlowThresholdMax", float),
        "acc": optional_param("AccumulationPeriod", int),
        "vol": optional_param("VolumeThreshold", float),
    }
    out["ystart"] = assessment_start(out["sm"], out["em"])  # py_ewr's assessment year

    multi = _is_multigauge(ewr_row.Multigauge.iloc[0]) if "Multigauge" in ewr_row else False
    dispatch_kind = str(ewr_row.FlowLevelVolume.iloc[0]) if "FlowLevelVolume" in ewr_row else "F"
    out["kind"] = spec["kind"] if spec is not None else dispatch_kind
    out["low"] = handlers.get(_dispatch_key(code, multi, dispatch_kind)) in LOW_FLOW_HANDLERS

    if spec is None:
        _EVAL_CACHE[key] = out
        return out
    out.update(sd=optional_param("StartDay", int), ed=optional_param("EndDay", int))
    if not custom_ewr.cache_evaluation(spec, key, out):
        raise ValueError("User EWR definition changed during evaluation; re-run")
    return out


def _calculate_stock_ewr(scn_id: str, gauge: str, ewr_row: Any) -> tuple[Any, Any, Any, Any, Any, dict[str, str]]:
    """calc_sorter on one stock EWR over a gauge's stored daily flows: (df_F, df_L, results, events, series, handlers)."""
    import pandas as pd
    from py_ewr import data_inputs, evaluate_EWRs

    df_F = daily_series(scn_id, gauge).to_frame(gauge)
    df_F.index.name = "Date"
    df_L = pd.DataFrame(index=df_F.index)
    results, events = evaluate_EWRs.calc_sorter(df_F, df_L, gauge, ewr_row, data_inputs.get_ewr_calc_config(None))
    return df_F, df_L, results, events, df_F[gauge], diagnostic_handlers()


def _calculate_user_ewr(scn_id: str, gauge: str, spec: dict[str, Any], ewr_row: Any) -> tuple[Any, Any, Any, Any, Any, dict[str, str]]:
    """custom_ewr's evaluation of one user EWR: (df_F, df_L, results, events, series, handlers). The series is the level for a
    level EWR, and the two gauges' summed flow for a two-gauge EWR."""
    from ewr_analysis import custom_ewr

    df_F, df_L = custom_ewr.frames(scn_id, spec)
    config = custom_ewr.calc_config([spec])
    results, events = custom_ewr.calculate(spec, df_F, df_L, ewr_row, config)
    series = df_L[gauge] if spec["kind"] == "L" else df_F[gauge]
    if spec.get("second_gauge"):
        series = df_F.sum(axis=1, min_count=len(df_F.columns))
    handlers = {key: handler for handler, keys in config.items() for key in keys}
    return df_F, df_L, results, events, series, handlers


def _successful_tables(sh: Any, events_by_year: dict[Any, list[Any]], gauge: str, code: str, pu: str) -> tuple[Any, Any]:
    """One EWR's rows of py_ewr's successful inter-events and successful events, (inter, succ).

    py_ewr's summaries fail (KeyError 'Gauge') on an empty event ledger, or when no event succeeded: both mean there are no
    successful events, so both tables are empty.
    """
    import pandas as pd

    columns = ["startDate", "endDate", "interEventLength", "eventDuration"]
    empty = pd.DataFrame(columns=columns)
    if not any(len(lst) > 0 for lst in events_by_year.values()):
        return empty, empty

    def pick(summary: Any) -> Any:
        if not len(summary) or "Gauge" not in summary:
            return pd.DataFrame(columns=columns)
        return summary[(summary.Gauge == gauge) & (summary.Code == code) & (summary.PlanningUnit == pu)].copy()

    try:
        return pick(sh.get_all_successful_interEvents()), pick(sh.get_all_successful_events())
    except KeyError as exc:
        if exc.args != ("Gauge",):
            raise
        return empty, empty


def ewr_gaps(ev: dict[str, Any]) -> tuple[list[dict[str, Any]], float | None]:
    """py_ewr's gaps between successful events (including the gaps from the start of the record to the first event and from the
    last event to the end, as py_ewr counts them), each with the days beyond the maximum inter-event period. Two corrections to
    what py_ewr returns:
    - an EWR with no successful event in the whole record gets one gap covering the record (py_ewr returns none, which would read
      as "never longer than the maximum");
    - a gap containing a missing day has an unknown length: it is kept but marked `censored` and counts nowhere.
    Returns (gaps, etp): etp = days beyond the maximum in the uncensored gaps / days of record outside the censored gaps (the same
    spans in both), or None when the EWR has no maximum or nothing is left to count."""
    import pandas as pd

    flow, mrip, inter = ev["flow"], ev["mrip_days"], ev["inter"]
    first, last = flow.index[0], flow.index[-1]
    no_success = not len(ev["succ"])
    if no_success:
        spans = [(first, last)]
    else:
        spans = [(pd.Timestamp(a), pd.Timestamp(b)) for a, b in zip(inter.startDate, inter.endDate, strict=True)] if len(inter) else []

    gaps = []
    for start, end in spans:
        days = (end - start).days + 1
        censored = bool(flow.loc[start:end].isna().any())
        gaps.append(
            {
                "start": _iso_date(start),
                "end": _iso_date(end),
                "days": days,
                "censored": censored,
                "boundary": bool(start <= first or end >= last),
                "excess": None if censored else (max(0, days - mrip) if mrip else 0),
                "no_success": no_success,
            }
        )

    counted_days = len(flow) - sum(g["days"] for g in gaps if g["censored"])
    etp = round(min(1.0, sum(g["excess"] for g in gaps if not g["censored"]) / counted_days), 4) if mrip and counted_days > 0 else None
    return gaps, etp


def gap_severity(gaps: list[dict[str, Any]], mrip: int) -> dict[str, Any]:
    """How far the gaps between successful events overshoot the maximum inter-event period, from the uncensored gaps:
    sev = longest interior gap / MRIP (all gaps when none is interior), sev_b = longest gap including those from the start or to the end of
    the record / MRIP, eps = gaps longer than MRIP (episodes). All None without a maximum or an uncensored gap."""
    known = [g for g in gaps if not g["censored"]]
    if not mrip or not known:
        return {"sev": None, "sev_b": None, "eps": None}
    inner = [g["days"] for g in known if not g["boundary"]] or [g["days"] for g in known]
    return {"sev": round(max(inner) / mrip, 2), "sev_b": round(max(g["days"] for g in known) / mrip, 2), "eps": sum(1 for g in known if g["excess"])}


def record_days(s: Any) -> Any:
    """A gauge's series from its first to its last observed day on every calendar day (missing days NaN), as ewr_eval sees it; None when the
    index is not dates (ten-thousand-year runs)."""
    import pandas as pd

    if s is None or not isinstance(s.index, pd.DatetimeIndex):
        return None
    s = s.loc[s.first_valid_index() : s.last_valid_index()] if s.notna().any() else s.iloc[:0]
    return s if s.empty else s.reindex(pd.date_range(s.index[0], s.index[-1]))


def assessment_start(sm: int, em: int) -> int:
    """py_ewr's assessment year starts on 1 July, except when the timing window runs from the first half of the year into the second
    (StartMonth <= 6 and EndMonth >= 7, e.g. Jun-Nov): then on the 1st of StartMonth (evaluate_EWRs.wateryear_daily)."""
    return sm if sm <= 6 and em >= 7 else 7


def _timing_mask(index: Any, sm: int, em: int, sd: int | None = None, ed: int | None = None) -> Any:
    """Calendar window mask; optional day boundaries are used by user EWRs, while stock summaries retain whole months."""
    month, day = index.month, index.day
    after = (month > sm) | ((month == sm) & (day >= (sd or 1)))
    before = (month < em) | ((month == em) & (day <= (ed or 31)))
    return after & before if sm <= em else after | before


def complete_years(flow: Any, wys: list[int], ys: int, sm: int, em: int, sd: int | None = None, ed: int | None = None) -> list[int]:
    """1 for each assessment year (label w, starting on the 1st of month ys) in which every day of the timing window sm-em has a value;
    the window's days come from the calendar, so a window cut off by the record's ends is incomplete."""
    import pandas as pd

    if not wys:
        return []
    full = pd.date_range(pd.Timestamp(min(wys), ys, 1), pd.Timestamp(max(wys) + 1, ys, 1) - pd.Timedelta(days=1))
    m = full.month
    win = _timing_mask(full, sm, em, sd, ed)
    ok = pd.Series(flow.reindex(full).notna().to_numpy()[win], index=(full.year - (m < ys))[win])
    done = ok.groupby(level=0).all()
    return [int(bool(done.get(w, False))) for w in wys]


def dr_years(yr: Any, code: str, dur: Any, low: bool, per_year: Any, events: dict[Any, list[Any]] | None = None) -> list[float | None]:
    """Duration ratio of each assessment year (py_ewr's yearly results `yr`, columns [<code>_]maxEventDays / totalEventDays): the longest
    event in the year ÷ the required duration, capped at 2. Low-flow (any-days) EWRs use the days at or above the threshold in the year;
    an EWR needing k > 1 events a year uses its k-th longest event (from `events`, py_ewr's events by year; 0 when fewer than k)."""
    import pandas as pd

    dur = pd.to_numeric(dur, errors="coerce")
    if pd.isna(dur) or dur <= 0:
        return [None] * len(yr)
    k = int(per_year) if not pd.isna(pd.to_numeric(per_year, errors="coerce")) and int(per_year) > 1 else 1
    col = (f"{code}_" if code else "") + ("totalEventDays" if low else "maxEventDays")  # ewr_eval's years drop the code prefix
    out: list[float | None] = []
    for w in yr.index:
        days: Any
        if k > 1 and not low and events is not None:
            lens = sorted((len(e) for e in events.get(w, []) if e), reverse=True)
            days = lens[k - 1] if len(lens) >= k else 0
        else:
            days = pd.to_numeric(yr.at[w, col], errors="coerce") if col in yr else float("nan")
        out.append(None if pd.isna(days) else round(min(float(days) / float(dur), 2.0), 3))
    return out


def vr_years(flow: Any, wys: list[int], ys: int, sm: int, em: int, acc: Any, vol: Any, sd: int | None = None, ed: int | None = None) -> list[float | None]:
    """Volume ratio of each assessment year for a volume EWR: the largest sum of daily flow over AccumulationPeriod consecutive days within
    the timing window ÷ VolumeThreshold, capped at 2."""
    import pandas as pd

    acc, vol = pd.to_numeric(acc, errors="coerce"), pd.to_numeric(vol, errors="coerce")
    if pd.isna(acc) or pd.isna(vol) or acc < 1 or vol <= 0:
        return [None] * len(wys)
    m = flow.index.month
    win = flow.where(_timing_mask(flow.index, sm, em, sd, ed))
    total = win.groupby(flow.index.year - (m < ys)).rolling(int(acc), min_periods=int(acc)).sum()
    best = total.groupby(level=0).max()
    return [None if pd.isna(best.get(w)) else round(min(float(best[w]) / float(vol), 2.0), 3) for w in wys]


def year_summary(dr: list[float | None], complete: list[int]) -> dict[str, Any]:
    """Median duration ratio over the complete years, and the near misses: years with 0.5 <= DR < 1 and the years with DR < 1."""
    import numpy as np

    v = [x for x, c in zip(dr, complete, strict=True) if c and x is not None]
    fail = [x for x in v if x < 1]
    return {"dr": round(float(np.median(v)), 3) if v else None, "nm": [sum(1 for x in fail if x >= 0.5), len(fail)] if v else None}


def window_metrics(
    years: dict[str, Any], gaps: list[dict[str, Any]], mrip: int, target: Any, w0: Any, w1: Any, record: tuple[Any, Any] | None = None
) -> dict[str, Any]:
    """An EWR's metrics inside one drought window, the days w0..w1 (dates, inclusive; drought_windows.window_dates), from its yearly results
    (`years`: wy labels, achieved, complete, dr, ystart) and its gaps (ewr_gaps):
      n   complete assessment years inside the window;  fr = (achieved / n) / (target / 100)
      et  days of the window beyond MRIP in an uncensored gap (a gap is beyond MRIP from its start + MRIP days, so a gap that began before
          the window counts: antecedent dryness) ÷ the window's days within the record, less days inside censored gaps
      sev longest uncensored gap overlapping the window, from its true start to its end, ÷ MRIP
      dr  median duration ratio over the window's complete years.
    `record`: the first and last day of the record (default: the span of the gaps)."""
    import numpy as np
    import pandas as pd

    w0, w1, ys = pd.Timestamp(w0), pd.Timestamp(w1), years.get("ystart", 7)
    inside = [i for i, w in enumerate(years["wy"]) if pd.Timestamp(w, ys, 1) >= w0 and pd.Timestamp(w + 1, ys, 1) - pd.Timedelta(days=1) <= w1]
    done = [i for i in inside if years["complete"][i]]
    n, ach, tgt = len(done), sum(years["achieved"][i] for i in done), pd.to_numeric(target, errors="coerce")
    drs = [years["dr"][i] for i in done if years.get("dr") and years["dr"][i] is not None]
    out: dict[str, Any] = {
        "n": n,
        "fr": round(ach / n / (tgt / 100), 4) if n and tgt > 0 else None,
        "et": None,
        "sev": None,
        "dr": round(float(np.median(drs)), 3) if drs else None,
    }
    span = [(pd.Timestamp(g["start"]), pd.Timestamp(g["end"]), g) for g in gaps]
    if mrip and span:
        first, last = (pd.Timestamp(record[0]), pd.Timestamp(record[1])) if record else (min(a for a, _, _ in span), max(b for _, b, _ in span))
        lo, hi = max(w0, first), min(w1, last)  # the window within the record
        overlap = lambda a, b: max(0, (min(b, hi) - max(a, lo)).days + 1)  # noqa: E731  (days of a..b inside lo..hi)
        days = overlap(lo, hi) - sum(overlap(a, b) for a, b, g in span if g["censored"])
        if days > 0:
            beyond = sum(overlap(a + pd.Timedelta(days=mrip), b) for a, b, g in span if not g["censored"] and g["days"] > mrip)
            out["et"] = round(beyond / days, 4)
        hit = [g["days"] for a, b, g in span if not g["censored"] and a <= w1 and b >= w0]
        out["sev"] = round(max(hit) / mrip, 2) if hit else None
    return out


def interevents(q: dict[str, str]) -> dict[str, Any]:
    """Gaps between successful events of one EWR (py_ewr successful inter-events) that exceed its maximum inter-event period."""
    ev = ewr_eval(q["scn"], q["gauge"], q["ewr"], q.get("pu", ""))
    if not ev["mrip_days"]:
        return {"mrip_days": 0, "exceed": [], "n_gaps": 0}
    gaps, _ = ewr_gaps(ev)
    return {
        "mrip_days": ev["mrip_days"],
        "maxint_years": ev["mrip_days"] / 365,
        "n_gaps": len(gaps),
        "n_censored": sum(g["censored"] for g in gaps),
        "exceed": [{"start": g["start"], "end": g["end"], "days": g["days"]} for g in gaps if g["excess"]],
    }


def _scenario_ids(q: dict[str, str]) -> list[tuple[str, str]]:
    """[("base", id)] plus ("comp", id) when the query names a comparison scenario."""
    return [("base", q["base"])] + ([("comp", q["comp"])] if q.get("comp") else [])


def ewr_years(q: dict[str, str]) -> dict[str, Any]:
    """One EWR, year by year and event by event, for one scenario or a base / comparison pair (py_ewr's own results throughout).
    years:  py_ewr's yearly statistics; a water year is complete when py_ewr reports no missing days and a full year of days.
    events: every threshold event py_ewr found (ok = it is one of py_ewr's successful events, i.e. at least MinSpell days long), with
            its calendar length (days = start to end; qual = the days at or above the threshold, fewer when the event bridges short
            dips within its gap tolerance), total volume (sum of the daily flows over every day from start to end, ML) and peak.
    gaps:   see ewr_gaps.
    paired: two scenarios: achievement compared over the water years complete in BOTH, year by year."""
    gauge, code, pu = q["gauge"], q["ewr"], q.get("pu", "")
    out: dict[str, Any] = {"code": code, "gauge": gauge, "scenarios": {}}
    for role, scn_id in _scenario_ids(q):
        ev = ewr_eval(scn_id, gauge, code, pu)
        years = _ewr_year_table(ev)
        gaps, etp = ewr_gaps(ev)
        out["scenarios"][role] = {
            "years": years,
            "events": _ewr_events(ev),
            "gaps": gaps,
            "etp": etp,
            **gap_severity(gaps, ev["mrip_days"]),
            "coverage": record_coverage(ev["flow"], years["complete"]),
        }
        if code.startswith("U_"):
            out["kind"], out["unit"] = ev["kind"], {"F": "ML/d", "V": "ML/d", "L": "m", "S": "EC"}[ev["kind"]]
        # the EWR's settings (the same for both scenarios)
        out.update({k: ev[k] for k in ("pu", "thr", "thr_max", "dur", "min_spell", "per_year", "gap_tol", "sm", "em", "ystart", "target", "mrip_days")})

    if "comp" in out["scenarios"]:
        out["paired"] = _paired_achievement(out["scenarios"]["base"]["years"], out["scenarios"]["comp"]["years"])
    return out


def _ewr_year_table(ev: dict[str, Any]) -> dict[str, Any]:
    """An evaluated EWR's (ewr_eval) statistics by assessment year, as parallel lists."""
    import pandas as pd

    yearly, flow = ev["years"], ev["flow"]
    water_years = [int(w) for w in yearly.index]

    def column(name: str) -> list[int]:
        return pd.to_numeric(yearly[name], errors="coerce").fillna(0).astype(int).tolist() if name in yearly else [0] * len(yearly)

    # a year counts when every day of the EWR's timing window in that assessment year has a flow (a missing day elsewhere in the
    # year does not matter); the window's days are taken from the calendar, so a window cut off by the record's ends is incomplete
    complete = complete_years(flow, water_years, ev["ystart"], ev["sm"], ev["em"], ev.get("sd"), ev.get("ed"))

    kind = ev.get("kind", "F")
    if kind in ("F", "S"):
        ratios = dr_years(yearly, "", ev["dur"], ev.get("low", False), ev["per_year"], ev["events"])
    elif kind == "V":
        ratios = vr_years(flow, water_years, ev["ystart"], ev["sm"], ev["em"], ev["acc"], ev["vol"], ev.get("sd"), ev.get("ed"))
    else:
        ratios = [None] * len(water_years)

    return {
        "wy": water_years,
        "achieved": column("eventYears"),
        "n_events": column("numEvents"),
        "n_all": column("numEventsAll"),
        "event_days": column("totalEventDays"),
        "max_event": column("maxEventDays"),
        "missing": column("missingDays"),
        "complete": complete,
        "dr": ratios,
    }


def _ewr_events(ev: dict[str, Any]) -> list[dict[str, Any]]:
    """Every threshold event of an evaluated EWR (ewr_eval) in date order, with its length, volume, peak and success."""
    import pandas as pd

    flow = ev["flow"]
    successful_starts = set(pd.to_datetime(ev["succ"].startDate).dt.strftime("%Y-%m-%d")) if len(ev["succ"]) else set()
    has_volume = ev.get("kind", "F") in ("F", "V")  # levels and salinity have no volume
    events = []
    for water_year, year_events in ev["events"].items():
        for event in year_events:
            if not event:
                continue
            start, end = pd.Timestamp(event[0][0]), pd.Timestamp(event[-1][0])
            span = flow.loc[start:end]  # every calendar day from start to end (an event may skip days within its gap tolerance)
            events.append(
                {
                    "wy": int(water_year),
                    "start": _iso_date(start),
                    "end": _iso_date(end),
                    "days": (end - start).days + 1,
                    "qual": len(event),
                    "vol": None if not has_volume or span.isna().any() else round(float(span.sum()), 1),
                    "peak": round(float(span.max()), 2),
                    "ok": _iso_date(start) in successful_starts,
                }
            )
    events.sort(key=lambda e: e["start"])
    return events


def _paired_achievement(base: dict[str, Any], comp: dict[str, Any]) -> dict[str, Any]:
    """Achievement in two scenarios over the water years complete in both, with the years gained and lost and McNemar's test."""
    base_index = {w: i for i, w in enumerate(base["wy"]) if base["complete"][i]}
    comp_index = {w: i for i, w in enumerate(comp["wy"]) if comp["complete"][i]}
    common = sorted(set(base_index) & set(comp_index))
    base_achieved = [base["achieved"][base_index[w]] for w in common]
    comp_achieved = [comp["achieved"][comp_index[w]] for w in common]
    n = len(common)
    gained = sum(1 for b, c in zip(base_achieved, comp_achieved, strict=True) if not b and c)
    lost = sum(1 for b, c in zip(base_achieved, comp_achieved, strict=True) if b and not c)
    return {
        "wy": common,
        "n": n,
        "base": int(sum(base_achieved)),
        "comp": int(sum(comp_achieved)),
        "gained": gained,
        "lost": lost,
        "pct_base": round(100 * sum(base_achieved) / n, 3) if n else None,
        "pct_comp": round(100 * sum(comp_achieved) / n, 3) if n else None,
        "p_mcnemar": mcnemar_p(gained, lost),
    }


def mcnemar_p(gained: int, lost: int) -> float | None:
    """Exact two-sided McNemar p-value for paired yes/no years: under no change the discordant years (achieved in one scenario only) split
    50:50, so p = min(1, 2 * P(X <= min(gained, lost))) with X ~ Binomial(gained + lost, 1/2). None when no year differs."""
    n = gained + lost
    if n == 0:
        return None
    return cast(float, round(min(1.0, 2 * sum(math.comb(n, k) for k in range(min(gained, lost) + 1)) / 2**n), 4))


def _feb29(w: int) -> bool:
    """True when water year w (1 Jul w - 30 Jun w+1) holds a 29 February."""
    import calendar

    return calendar.isleap(w + 1)


def _water_year_days(w: int) -> int:
    """Calendar days in water year w (1 Jul w - 30 Jun w+1)."""
    return 366 if _feb29(w) else 365


def _runs_by_year(s: Any, hit: Any) -> tuple[dict[str, list[Any]], list[dict[str, Any]]]:
    """Runs of consecutive days where `hit` is true in the daily series `s` (missing days are never hits and break a run; a run
    beside a missing day or the record's ends is `censored`: its true length is unknown). Per water year (1 Jul - 30 Jun,
    labelled by its first calendar year as in py_ewr): the days that are hits, the longest run inside that year (a run crossing
    30 June is split at the year boundary for this) and the number of runs starting in it; numbers only for complete water years
    (every day observed). Returns (years, runs)."""
    import numpy as np

    values = s.to_numpy(dtype=float)
    missing = np.isnan(values)
    hit = hit & ~missing
    wy = np.array([_water_year(d) for d in s.index])
    n = len(values)

    runs: list[dict[str, Any]] = []
    i = 0
    while i < n:
        if not hit[i]:
            i += 1
            continue
        j = i  # extend to the run's last day
        while j + 1 < n and hit[j + 1]:
            j += 1
        touches_gap_or_edge = i == 0 or j == n - 1 or missing[i - 1] or (j + 1 < n and missing[j + 1])
        runs.append({"start": _iso_date(s.index[i]), "end": _iso_date(s.index[j]), "days": j - i + 1, "censored": bool(touches_gap_or_edge), "wy": int(wy[i])})
        i = j + 1

    starts: dict[int, int] = {}
    for r in runs:
        starts[r["wy"]] = starts.get(r["wy"], 0) + 1

    years: dict[str, list[Any]] = {"wy": [], "days": [], "longest": [], "n": [], "complete": []}
    for w in np.unique(wy):
        in_year = wy == w
        complete = bool(in_year.sum() == _water_year_days(int(w)) and not missing[in_year].any())
        year_hits, longest, current = hit[in_year], 0, 0
        for is_hit in year_hits:
            current = current + 1 if is_hit else 0
            longest = max(longest, current)
        years["wy"].append(int(w))
        years["complete"].append(int(complete))
        years["days"].append(int(year_hits.sum()) if complete else None)
        years["longest"].append(longest if complete else None)
        years["n"].append(starts.get(int(w), 0) if complete else None)
    return years, runs


def _pair_years(out: dict[str, Any]) -> dict[str, Any]:
    """Two scenarios: the water years complete in both."""
    if "comp" in out["scenarios"]:
        base, comp = out["scenarios"]["base"]["years"], out["scenarios"]["comp"]["years"]
        base_complete = {w for w, ok in zip(base["wy"], base["complete"], strict=True) if ok}
        comp_complete = {w for w, ok in zip(comp["wy"], comp["complete"], strict=True) if ok}
        out["paired"] = {"wy": sorted(base_complete & comp_complete)}
    return out


def spells(q: dict[str, str]) -> dict[str, Any]:
    """Low-flow spells: runs of consecutive days with flow below `thr` ML/d (see _runs_by_year); with two scenarios they are compared
    over the water years complete in both."""
    gauge, thr = q["gauge"], float(q["thr"])
    out: dict[str, Any] = {"gauge": gauge, "thr": thr, "scenarios": {}}
    for role, scn_id in _scenario_ids(q):
        s = daily_series(scn_id, gauge)
        years, runs = _runs_by_year(s, s.to_numpy(dtype=float) < thr)
        years["below"] = years.pop("days")
        out["scenarios"][role] = {
            "years": years,
            "longest_runs": sorted(runs, key=lambda r: -r["days"])[:10],
            "n_runs": len(runs),
            "missing_days": int(s.isna().sum()),
        }
    return _pair_years(out)


def regimes(q: dict[str, str]) -> dict[str, Any]:
    """Flow regime components at a gauge (baseflow, freshes, bankfull, overbank, ...). Each regime's threshold comes from the EWR
    tool's parameter sheet: the lowest flow threshold among the gauge's flow EWRs in that regime, with EWR codes assigned to regimes
    by the MDBA pipeline mapping (flow_regime_mappings). For each threshold and complete water year (whole year, not each EWR's
    timing window): days at or above it, events (runs of consecutive days at or above) starting in the year and the longest one."""
    from ewr_analysis.flow_regime_mappings import regimes_for

    gauge = q["gauge"]
    ewrs_by_regime: dict[str, list[dict[str, Any]]] = {}
    for e in _builtin_ewr_list(gauge):
        for regime in regimes_for(e["code"], e.get("state") or ""):
            if regime != "excluded":
                ewrs_by_regime.setdefault(regime, []).append(e)

    components = []
    for regime, ewrs in ewrs_by_regime.items():
        thr = min(e["thr"] for e in ewrs)
        components.append(
            {
                "regime": regime,
                "thr": thr,
                "codes": [c for c, _ in sorted({(e["code"], e["thr"]) for e in ewrs}, key=lambda x: (x[1], x[0]))],
                "at_thr": sorted({e["code"] for e in ewrs if e["thr"] == thr}),
            }
        )
    components.sort(key=lambda c: c["thr"])

    out: dict[str, Any] = {"gauge": gauge, "regimes": components, "scenarios": {}}
    for role, scn_id in _scenario_ids(q):
        s = daily_series(scn_id, gauge)
        values = s.to_numpy(dtype=float)
        per_regime = [_runs_by_year(s, values >= c["thr"])[0] for c in components]
        out["scenarios"][role] = {"regimes": per_regime, "years": per_regime[0] if per_regime else {"wy": [], "complete": []}}
    return _pair_years(out)


def heatmap(q: dict[str, str]) -> dict[str, Any]:
    """Return annual achievement frequencies over a flow-threshold and duration grid.

    Query parameters select the gauge, base and optional comparison scenarios, season, day-count mode and visible bounds.
    The requested column count (default 400, clamped to 40–1500) sets threshold spacing. Return the grids and, for a comparison,
    their difference; raise ValueError when the scenarios share no dates at the gauge.
    """
    import numpy as np
    import pandas as pd

    gauge = q["gauge"]
    ids = [q["base"]] + ([q["comp"]] if q.get("comp") else [])
    df = pd.concat([gauge_series(i, gauge).rename(i) for i in ids], axis=1, join="inner").dropna(how="all")
    if df.empty:
        raise ValueError("the scenarios share no dates at this gauge")
    xlim = int(float(q["xlim"])) if q.get("xlim") else nice_ceiling(float(np.nanpercentile(df.to_numpy(), 99.5)))
    xlim = max(10, min(xlim, 200000))
    dmax = max(2, min(int(float(q.get("dmax") or 120)), 366))
    sm, em = int(q.get("sm") or 7), int(q.get("em") or 6)
    consec = q.get("days") == "consec"
    # a zoomed view asks for just its own threshold / duration window, so the same ~400 columns are spread over the visible range
    xmin = max(0, min(int(float(q["xmin"])), xlim - 1)) if q.get("xmin") else 0
    dmin = max(1, min(int(float(q["dmin"])), dmax)) if q.get("dmin") else 1
    # the browser sends the number of columns that fit the plot's pixel width (default 400), so the grid follows the plot size
    cols = max(40, min(int(float(q.get("cols") or 400)), 1500))
    spacing = max(1, int(math.ceil((xlim - xmin + 1) / cols)))
    thresholds = np.arange(xmin, xlim + 1, spacing)
    durations = np.arange(dmin, dmax + 1)
    af, n_years = achievement_frequencies(df.to_numpy(dtype=float), df.index, thresholds, durations, sm, em, consec)

    def as_grid(a: Any) -> list[list[float]]:
        return cast(list[list[float]], np.round(a.T, 3).tolist())  # (durations x thresholds), as plotted

    out = {
        "thresholds": thresholds.tolist(),
        "durations": durations.tolist(),
        "xlim": xlim,
        "xmin": xmin,
        "spacing": spacing,
        "years": n_years,
        "dates": [df.index[0].strftime("%Y-%m-%d"), df.index[-1].strftime("%Y-%m-%d")],
        "base": as_grid(af[0]),
    }
    if len(ids) == 2:
        out["comp"], out["diff"] = as_grid(af[1]), as_grid(af[1] - af[0])
    if q.get("mthr") or q.get("mdur"):
        # Evaluate exact selected points in one batch; grid cells may use coarser thresholds.
        mark_thresholds = [float(v) for v in q.get("mthr", "").split(",")]
        mark_durations = [float(v) for v in q.get("mdur", "").split(",")]
        if len(mark_thresholds) != len(mark_durations):
            raise ValueError("Each EWR threshold must have a matching duration")
        if any(not math.isfinite(v) or v < 0 for v in mark_thresholds):
            raise ValueError("EWR thresholds must be finite and non-negative")
        if any(not math.isfinite(v) or not v.is_integer() or not 1 <= v <= 366 for v in mark_durations):
            raise ValueError("EWR durations must be whole days between 1 and 366")
        th, ti = np.unique(mark_thresholds, return_inverse=True)
        du, di = np.unique(np.asarray(mark_durations, dtype=int), return_inverse=True)
        points, _ = achievement_frequencies(df.to_numpy(dtype=float), df.index, th, du, sm, em, consec)
        out["marks"] = []
        for i, (threshold, duration) in enumerate(zip(mark_thresholds, mark_durations, strict=True)):
            values = [round(float(v), 3) for v in points[:, ti[i], di[i]]]
            if all(math.isfinite(v) for v in values):
                out["marks"].append({"thr": threshold, "dur": int(duration), "values": values})
        if len(mark_thresholds) == 1 and out["marks"]:
            out["mark"] = out["marks"][0]["values"]  # retain the original single-point API
    out["consecutive"] = consec
    return out


def exceed_pct(sorted_vals: Any, x: Any) -> Any:
    """% of days the flow is equal to or above x (sorted_vals ascending)."""
    import numpy as np

    n = len(sorted_vals)
    return 100.0 * (n - np.searchsorted(sorted_vals, x, side="left")) / n


def _paired(q: dict[str, str], gauge: str) -> dict[str, Any]:
    """The flows the duration curves are built from: one scenario's observed days, or for two scenarios only the days on which
    BOTH have a flow, so a difference never mixes in a difference in coverage."""
    import pandas as pd

    ser = {k: gauge_series(q[k], gauge) for k in ("base", "comp") if q.get(k)}
    if len(ser) == 2:
        df = pd.concat(ser, axis=1, join="inner").dropna()
        if df.empty:
            raise ValueError("the scenarios share no observed dates at this gauge")
        return {k: df[k] for k in ser}
    return {k: v.dropna() for k, v in ser.items()}


def curves(q: dict[str, str]) -> dict[str, Any]:
    """kind: hydro (daily flow and water-year volumes, one or two scenarios, on shared dates) | fdc (flow duration curve, one or two scenarios) |
    dfdc (differential flow duration curve: change in % of time flow >= x, comparison - base) | mdfdc (dfdc by calendar month).
    thr (comma-separated flows): % of time exceeded at each, for each scenario."""
    gauge = q["gauge"]
    kind = q.get("kind") or ("fdc" if q.get("comp") else "hydro")
    paired = _paired(q, gauge)  # also checks, for every kind, that two scenarios share observed dates
    if kind == "hydro":
        return _hydrograph(q, gauge)
    if kind == "mdfdc":
        return _monthly_differential_fdc(q, paired)
    if kind in ("fdc", "dfdc"):
        return _duration_curves(q, kind, paired)
    _window_sorted_flows(q, paired)  # an unknown kind still reports a bad timing window or threshold first
    raise ValueError(f"unknown curve kind {kind!r}")


def _hydrograph(q: dict[str, str], gauge: str) -> dict[str, Any]:
    """Daily flows (missing days stay null so the line breaks there) and the volume of each complete water year; with two
    scenarios both lines are on the dates they share, and a water year's volumes are compared only when both are complete."""
    import numpy as np
    import pandas as pd

    df = pd.concat({k: daily_series(q[k], gauge) for k in ("base", "comp") if q.get(k)}, axis=1, join="inner" if q.get("comp") else "outer")
    if df.empty:
        raise ValueError("the scenarios share no dates at this gauge")
    out: dict[str, Any] = {"kind": "hydro", "dates": [d.strftime("%Y-%m-%d") for d in df.index]}
    for k in df.columns:
        out[k] = [None if np.isnan(v) else round(float(v), 2) for v in df[k].to_numpy(dtype=float)]
    out["flow"] = out["base"]

    wy = np.array([_water_year(d) for d in df.index])
    all_observed = df.notna().all(axis=1).to_numpy()
    year_volumes: list[dict[str, Any]] = []
    for w in np.unique(wy):
        in_year = wy == w
        if in_year.sum() == _water_year_days(int(w)) and all_observed[in_year].all():
            year_volumes.append({"wy": int(w), **{k: round(float(df[k].to_numpy()[in_year].sum()), 1) for k in df.columns}})
    out["years"] = year_volumes
    out["missing_days"] = int((~all_observed).sum())
    return out


def _monthly_differential_fdc(q: dict[str, str], paired: dict[str, Any]) -> dict[str, Any]:
    """For each calendar month, the change (comparison - base) in the % of that month's days at or above each flow."""
    import numpy as np

    if not q.get("comp"):
        raise ValueError("the monthly differential flow duration needs two scenarios")
    order = [7, 8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6]  # water-year order
    by_month = {k: {m: np.sort(v[v.index.month == m].to_numpy(dtype=float)) for m in order} for k, v in paired.items()}

    top = 0.0
    for months in by_month.values():
        for values in months.values():
            if len(values):
                top = max(top, float(np.percentile(values, 99)))  # flow still reached on 1% of that month's days
    ymax = float(q["ymax"]) if q.get("ymax") else top
    if ymax <= 0:
        raise ValueError("no positive flows to plot")
    if q.get("ymin") not in (None, ""):  # zoomed in: the same 200 steps spread over the chosen range
        ymin = max(0.0, float(q["ymin"]))
        if ymax <= ymin:
            raise ValueError("the flow range is empty")
        flows = np.linspace(ymin, ymax, 200)
    else:
        ymax = nice_ceiling(ymax)
        flows = np.linspace(0, ymax, 201)[1:]

    base = np.full((len(flows), 12), np.nan)
    comp = np.full((len(flows), 12), np.nan)
    for j, m in enumerate(order):
        if len(by_month["base"][m]):
            base[:, j] = exceed_pct(by_month["base"][m], flows)
        if len(by_month["comp"][m]):
            comp[:, j] = exceed_pct(by_month["comp"][m], flows)

    def as_grid(a: Any) -> list[list[float | None]]:
        return [[None if np.isnan(v) else round(float(v), 3) for v in row] for row in a]

    return {
        "kind": "mdfdc",
        "months": order,
        "flows": np.round(flows, 2).tolist(),
        "diff": as_grid(comp - base),
        "base": as_grid(base),
        "comp": as_grid(comp),
        "ymax": ymax,
    }


def _window_sorted_flows(q: dict[str, str], paired: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Each scenario's flows inside the optional timing window (sm..em, may wrap the year end), sorted ascending, and the
    % of time each requested threshold (`thr`) is equalled or exceeded."""
    import numpy as np

    sm, em = int(q.get("sm") or 0), int(q.get("em") or 0)

    def windowed(series: Any) -> Any:
        series = series.dropna()
        if not (1 <= sm <= 12 and 1 <= em <= 12):
            return series
        m = series.index.month
        return series[(m >= sm) & (m <= em)] if sm <= em else series[(m >= sm) | (m <= em)]

    sorted_flows = {k: np.sort(windowed(v).to_numpy(dtype=float)) for k, v in paired.items()}
    if any(len(v) == 0 for v in sorted_flows.values()):
        raise ValueError("no flow data inside that timing window")
    thresholds = [float(t) for t in (q.get("thr") or "").split(",") if t.strip()]
    marks = [{"thr": t, **{k: round(float(exceed_pct(v, t)), 3) for k, v in sorted_flows.items()}} for t in thresholds]
    return sorted_flows, marks


def _duration_curves(q: dict[str, str], kind: str, paired: dict[str, Any]) -> dict[str, Any]:
    """The flow duration curve (fdc) of one or two scenarios, or the differential curve (dfdc) of two."""
    import numpy as np

    sorted_flows, marks = _window_sorted_flows(q, paired)
    if kind == "fdc":
        p = np.linspace(0, 100, 401)
        fdc: dict[str, Any] = {"kind": "fdc", "p": np.round(p, 2).tolist(), "marks": marks}
        for k, v in sorted_flows.items():
            fdc[k] = np.round(np.percentile(v, 100 - p), 2).tolist()  # flow equalled or exceeded p% of the time
        if "comp" in sorted_flows:
            fdc["diff"] = np.round(np.array(fdc["comp"]) - np.array(fdc["base"]), 2).tolist()
        return fdc

    if "comp" not in sorted_flows:
        raise ValueError("the differential flow duration curve needs two scenarios")
    all_flows = np.concatenate(list(sorted_flows.values()))
    positive = all_flows[all_flows > 0]
    if not len(positive):
        raise ValueError("no positive flows to plot")
    # log-spaced flows (from a positive minimum), so the curve stays smooth when the page draws it on a log axis
    xs = np.geomspace(max(float(positive.min()), 0.01), float(all_flows.max()), 500)
    pct_base, pct_comp = exceed_pct(sorted_flows["base"], xs), exceed_pct(sorted_flows["comp"], xs)
    return {
        "kind": "dfdc",
        "x": np.round(xs, 3).tolist(),
        "base": np.round(pct_base, 3).tolist(),
        "comp": np.round(pct_comp, 3).tolist(),
        "diff": np.round(pct_comp - pct_base, 3).tolist(),
        "marks": marks,
    }


def _analysis_endpoint(path: str) -> Callable[[dict[str, str]], dict[str, Any]] | None:
    """The function answering a read-only analysis endpoint (looked up per request, so module functions stay patchable)."""
    endpoints: dict[str, Callable[[dict[str, str]], dict[str, Any]]] = {
        "/api/heatmap": heatmap,
        "/api/curves": curves,
        "/api/ewrs": lambda q: diagnostic_payload(q.get("gauge", "")),
        "/api/interevents": interevents,
        "/api/ewryears": ewr_years,
        "/api/spells": spells,
        "/api/regimes": regimes,
    }
    return endpoints.get(path)


USER_EWR_POST_PATHS = ("/api/uewr", "/api/uewr/preview", "/api/uewr/run", "/api/uewr/delete")


class Handler(SimpleHTTPRequestHandler):
    """Serves the dashboard page and the JSON API described in the module docstring; nothing else in the repository."""

    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, directory=str(ROOT), **k)

    def _authorized(self):
        """On a localhost-only server anyone on this computer may connect (no token). When bound to other addresses the server has
        a random token that every request must present: header X-Token, cookie ewr_token, or ?token= (which sets the cookie)."""
        token = getattr(self.server, "token", None)
        if not token:
            return True
        cookies = {k: v.value for k, v in SimpleCookie(self.headers.get("Cookie", "")).items()}
        given = [self.headers.get("X-Token"), cookies.get("ewr_token"), parse_qs(urlparse(self.path).query).get("token", [None])[0]]
        matches = [g is not None and hmac.compare_digest(g.encode(), token.encode()) for g in given]
        self._set_cookie = matches[2]  # opened from the printed address: remember the token for the page's API calls
        return any(matches)

    def _deny(self) -> None:
        self._json({"message": "access token required: use the address printed when the server started"}, 401)

    def _origin_allowed(self) -> bool:
        """Guard for requests that change state: the browser's Origin (if any) must be this server (see _state_change_allowed)."""
        return _state_change_allowed(self.headers.get("Origin"), self.headers.get("Host"), cast(tuple[Any, ...], self.server.server_address))

    def _json(self, obj: Any, code: int = 200, headers: dict[str, str] | None = None) -> None:
        data = json.dumps(_json_safe(obj), allow_nan=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if not 0 < length <= MAX_JSON_BYTES:
            raise ValueError("JSON body must be between 1 byte and 64 KB")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("Incomplete JSON body")
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _start_job(self, target: Callable[..., None], *args: Any) -> None:
        """Run `target(job_id, *args)` in a background thread and answer with the job id the page polls (/api/job)."""
        job_id = uuid.uuid4().hex[:10]
        JOBS[job_id] = {"status": "running", "t0": time.time()}
        threading.Thread(target=target, args=(job_id, *args), daemon=True).start()
        self._json({"job": job_id})

    def _user_ewr_post(self, path: str, query: str) -> None:
        from ewr_analysis import custom_ewr

        if not self._origin_allowed():
            return self._json({"message": "origin or host not allowed"}, 403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= MAX_JSON_BYTES:
                raise ValueError("JSON body must not exceed 64 KB")
            if path == "/api/uewr/delete":
                custom_ewr.delete_spec(parse_qs(query).get("id", [""])[0])
                return self._json({"ok": True})

            data = self._read_json()
            if path == "/api/uewr":
                return self._json({"spec": custom_ewr.save_spec(data)})
            if path == "/api/uewr/preview":
                spec = custom_ewr.validate_spec(data.get("spec"))
                rows = custom_ewr.evaluate(spec, data.get("scn", ""))
                return self._json({"row": rows[0] if rows else None, "rows": rows})
            # /api/uewr/run
            custom_ewr.job_inputs(data.get("ids"), data.get("scns"))
            return self._start_job(uewr_worker, data["ids"], data["scns"])
        except custom_ewr.ValidationError as exc:
            return self._json({"errors": exc.errors}, 400)
        except (ValueError, UnicodeError, RecursionError) as exc:  # RecursionError: JSON nested beyond the parser's limit
            return self._json({"errors": {"request": str(exc) or type(exc).__name__}}, 400)
        except Exception as exc:  # py_ewr failures: always answer with JSON rather than dropping the connection
            traceback.print_exc()
            return self._json({"errors": {"request": f"{type(exc).__name__}: {exc}"}}, 500)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if not self._authorized():
            return self._deny()

        if url.path in ("/api/uewr", "/api/uewr/schema"):
            from ewr_analysis import custom_ewr

            return self._json({"specs": list(custom_ewr.load_registry().values())} if url.path == "/api/uewr" else custom_ewr.schema())
        if url.path == "/api/forget":
            return self._json({"message": "method not allowed"}, 405, {"Allow": "POST"})
        if url.path == "/api/ping":
            return self._json(
                {"ok": True, "formats": FORMATS, "observed": {"available": importlib.util.find_spec("mdba_gauge_getter") is not None, "bom": False}}
            )
        if url.path == "/api/observed/units":
            try:
                return self._json({"units": observed_units()})
            except Exception as exc:
                traceback.print_exc()
                return self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
        if url.path == "/api/job":
            return self._job_status(parse_qs(url.query).get("id", [""])[0])

        endpoint = _analysis_endpoint(url.path)
        if endpoint is not None:
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                return self._json(endpoint(query))
            except Exception as e:
                traceback.print_exc()
                return self._json({"error": f"{type(e).__name__}: {e}"}, 400)

        if url.path in ("/", PAGE):  # the only file served: no directory listings, no repository files, no stored uploads
            self.path = PAGE
            return super().do_GET()
        return self._json({"message": "not found"}, 404)

    def _job_status(self, job_id: str) -> None:
        job = JOBS.get(job_id)
        if not job:
            return self._json({"status": "error", "message": "unknown job"}, 404)
        out = {k: v for k, v in job.items() if k != "t0"}
        if job["status"] == "running":
            out["elapsed"] = round(time.time() - job["t0"], 1)
        return self._json(out)

    def do_HEAD(self):
        self.send_error(405)

    def do_POST(self) -> None:
        url = urlparse(self.path)
        if not self._authorized():
            return self._deny()
        if url.path in USER_EWR_POST_PATHS:
            return self._user_ewr_post(url.path, url.query)
        if url.path == "/api/forget":
            return self._forget(parse_qs(url.query).get("id", [""])[0])
        if url.path == "/api/observed/run":
            return self._run_observed()
        if url.path != "/api/run":
            return self._json({"message": "not found"}, 404)
        return self._run_upload()

    def _forget(self, raw_id: str) -> None:
        """Delete a stored scenario's series files and cached results."""
        if not self._origin_allowed():
            return self._json({"message": "origin or host not allowed"}, 403)
        scn_id = _safe_id(raw_id)
        forget_cached(scn_id)
        for suffix in SERIES_SUFFIX.values():
            (STORE / f"{scn_id}{suffix}.csv").unlink(missing_ok=True)
        return self._json({"ok": True})

    def _run_upload(self) -> None:
        """Start a py_ewr job on an uploaded model output file (POST /api/run; options in the X- headers)."""
        if not self._origin_allowed():
            return self._json({"message": "origin or host not allowed"}, 403)
        size = int(self.headers.get("Content-Length") or 0)
        if not 0 < size <= MAX_BYTES:
            return self._json({"message": "file is empty or too large"}, 400)
        body = self.rfile.read(size)
        gauges = [g for g in re.split(r"[,\s]+", self.headers.get("X-Gauges", "")) if g]
        if not gauges:
            return self._json({"message": "no gauges given"}, 400)
        try:
            tds_factor = float(self.headers.get("X-TDS-Factor", "0.6"))
            if not math.isfinite(tds_factor) or tds_factor <= 0:
                raise ValueError
        except ValueError:
            return self._json({"message": "TDS-to-EC factor must be a finite positive number"}, 400)
        return self._start_job(
            worker,
            body,
            gauges,
            _safe_id(self.headers.get("X-Scn-Id", "")) or None,
            self.headers.get("X-Format", "Standard time-series"),
            self.headers.get("X-Filename", ""),
            self.headers.get("X-Subscenario", ""),
            self.headers.get("X-Merge") == "1",
            tds_factor,
        )

    def _run_observed(self) -> None:
        """Validate an observed request and start one deterministic scenario job per SDL."""
        if not self._origin_allowed():
            return self._json({"message": "origin or host not allowed"}, 403)
        try:
            data = self._read_json()
            sdl = data.get("sdl")
            if not isinstance(sdl, str) or not sdl.strip():
                raise ValueError("sdl is required")
            filter_period = data.get("filter_period", True)
            if not isinstance(filter_period, bool):
                raise ValueError("filter_period must be a boolean")
            if filter_period:
                start = date.fromisoformat(data.get("start", ""))
                end = date.fromisoformat(data.get("end", ""))
            else:
                # The getter requires explicit dates; this deliberately wide request means "all available".
                start, end = EARLIEST_RECORD, date.today()
            if start > end:
                raise ValueError("start must not follow end")
            if not isinstance(data.get("refresh", False), bool) or not isinstance(data.get("merge", False), bool):
                raise ValueError("refresh and merge must be booleans")
            requested = data.get("gauges")
            if not isinstance(requested, list) or not requested or any(not isinstance(gauge, str) for gauge in requested):
                raise ValueError("gauges must be a non-empty list of gauge IDs")
            allowed = set(observed.sdl_gauges(sdl))
            gauges = sorted(set(requested))
            if not gauges or not set(gauges) <= allowed:
                raise ValueError("one or more gauges do not belong to the selected SDL unit")
            if importlib.util.find_spec("mdba_gauge_getter") is None:
                return self._json({"message": "Observed data support is unavailable; install requirements-observed.txt"}, 503)
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            return self._json({"message": str(exc) or type(exc).__name__}, 400)

        scenario_id = _observed_scenario_id(sdl)
        with OBSERVED_ACTIVE_LOCK:
            current = OBSERVED_ACTIVE.get(scenario_id)
            if current and JOBS.get(current, {}).get("status") == "running":
                return self._json({"message": "an observed run for this SDL is already active", "job": current}, 409)
            job_id = uuid.uuid4().hex[:10]
            JOBS[job_id] = {"status": "running", "t0": time.time(), "progress": {"stage": "Preparing gauges", "done": 0, "total": len(gauges), "gauges": {}}}
            OBSERVED_ACTIVE[scenario_id] = job_id
        threading.Thread(
            target=observed_worker, args=(job_id, sdl, gauges, start, end, data.get("refresh", False), data.get("merge", False), filter_period), daemon=True
        ).start()
        return self._json({"job": job_id})

    def end_headers(self) -> None:
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
            if getattr(self, "_set_cookie", False):
                self.send_header("Set-Cookie", f"ewr_token={getattr(self.server, 'token', '')}; Path=/; HttpOnly; SameSite=Strict")
        super().end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:
        # the page polls /api/job repeatedly while a job runs: keep those requests out of the console
        if "/api/job" not in str(args[0] if args else ""):
            super().log_message(fmt, *args)


def find_free_port(preferred=DEFAULT_PORT, tries=50, host="127.0.0.1"):
    """The first port from `preferred` up that nothing is listening on; falls back to an OS-chosen port. A port of 0 asks the OS."""
    if preferred:
        for port in range(preferred, min(preferred + tries, 65536)):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind((host, port))
                except OSError:
                    continue
                return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def make_server(preferred=DEFAULT_PORT, host="127.0.0.1"):
    """A ThreadingHTTPServer (localhost only unless `host` says otherwise) at the first free port from `preferred`
    (retries if another program grabs it first)."""
    for _ in range(5):
        try:
            return ThreadingHTTPServer((host, find_free_port(preferred, host=host)), Handler)
        except OSError:
            continue
    return ThreadingHTTPServer((host, 0), Handler)


def main(argv=None):
    """Start the server: `[port] [--no-browser] [--host=ADDRESS]` (see the module docstring)."""
    args = list(sys.argv[1:] if argv is None else argv)
    open_browser = "--no-browser" not in args
    port_args = [a for a in args if a.isdigit()]
    host = next((a.split("=", 1)[1] for a in args if a.startswith("--host=")), "127.0.0.1")
    from ewr_analysis import build_dashboard, custom_ewr

    build_dashboard.ensure_built()  # the page is generated, not committed
    custom_ewr.sweep_sheets()
    srv = make_server(int(port_args[0]) if port_args else DEFAULT_PORT, host)
    port = srv.server_address[1]
    url = f"http://localhost:{port}{PAGE}"
    if not _loopback_host(host):  # reachable from other computers: require a random access token
        srv.token = secrets.token_urlsafe(16)
        url += f"?token={srv.token}"
        print(f"Listening on {host}: anyone with this address can use the tool and see uploaded data. Do not share the link.")
    if port_args and int(port_args[0]) != port:
        print(f"Port {port_args[0]} is busy, using {port} instead.")
    print(f"EWR analysis tool running at {url}\nLeave this window open while you use it; close it (or press Ctrl+C) to stop.")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    sys.modules.setdefault("ewr_analysis.server", sys.modules[__name__])
    main()
