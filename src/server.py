"""Local server for the EWR analysis dashboard: serves the page and runs py_ewr on an uploaded scenario.

    python3 src/server.py [port]        (default 8792, localhost only; then open http://localhost:8792/)

Endpoints (used by ewr_analysis_dashboard.html):
    GET  /api/ping                     -> {"ok": true}
    POST /api/run                      body = a model output file in one of the EWR tool's formats (FORMATS below)
                                       headers X-Format (model_format), X-Filename, X-Scn-Id, X-Gauges (gauge ids to look for in the file),
                                       X-Merge (1 = add the flows to the scenario stored under X-Scn-Id)
                                       -> {"job": id}
    GET  /api/job?id=<id>              -> {"status": "running" | "done" | "error", ...}

    GET  /api/heatmap?base=<id>[&comp=<id>]&gauge=<g>&xlim=&dmax=&sm=&em=   threshold x duration achievement heat map data
    GET  /api/ewrs?gauge=<g>                                               EWRs at a gauge handled by flow_handle / lowflow_handle
    GET  /api/interevents?scn=<id>&gauge=<g>&ewr=<code>&pu=<name>          gaps between successful events longer than the EWR's max inter-event period
    GET  /api/ewryears?base=<id>[&comp=<id>]&gauge=<g>&ewr=<code>&pu=<name> one EWR by water year: achievement, events (duration, volume, timing), gaps vs MRIP
    GET  /api/spells?base=<id>[&comp=<id>]&gauge=<g>&thr=<ML/d>            low-flow spells (consecutive days below thr) by water year
    GET  /api/regimes?base=<id>[&comp=<id>]&gauge=<g>                     flow regime components (thresholds from the EWR tool) by water year
    GET  /api/curves?base=<id>[&comp=<id>]&gauge=<g>&kind=hydro|fdc|dfdc|mdfdc[&thr=400,2000][&ymin=][&ymax=]
                                       hydrograph, flow duration curve, differential flow duration curve, monthly differential flow duration
    POST /api/forget?id=<id>           delete a stored scenario

An uploaded scenario's daily flows are kept in .user_scenarios/<id>.csv (repository root) so the flow charts can be drawn later.

For each EWR in the uploaded scenario the job returns the two metrics the tool uses:
    FR  = Frequency / TargetFrequency                    (py_ewr summary results, as in the MDBA outputs)
    ETP = L / TotalDays, where L = sum over the gaps between successful events of max(0, gap days - MRIP days)
          and MRIP days = MaxInterEventYears x 365       (py_ewr successful inter-events)
"""

import ipaddress
import json
import math
import re
import sys
import threading
import time
import traceback
import uuid
import warnings
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlparse, urlsplit

HERE = Path(__file__).parent
ROOT = HERE.parent  # repository root: the page is served from here; py_ewr comes from pip (pip install -r requirements.txt)
warnings.filterwarnings("ignore")

JOBS: dict[str, dict[str, Any]] = {}
STORE = ROOT / ".user_scenarios"
FLOWS: dict[str, Any] = {}  # scenario id -> flow DataFrame (date index, one column per gauge)
MAX_BYTES = 400 * 1024 * 1024


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


def load_frames(fmt: str, path: str, sub: str = "") -> tuple[Any, Any]:
    """The reading step of ScenarioHandler.process_scenarios for one model_format: file -> (flow frame, level frame),
    one column per gauge number (py_ewr's own cleaners and node-matching tables do the work)."""
    import pandas as pd
    from py_ewr import data_inputs
    from py_ewr import scenario_handling as sc

    ps = None  # default parameter sheet
    if fmt in ("Bigmod - MDBA", "FIRM - MDBA"):
        data, header = sc.unpack_model_file(path, "Dy", "Field")
        df_clean = sc.cleaner_MDBA(sc.build_MDBA_columns(data, header))
        df_F, df_L, _ = sc.match_MDBA_nodes(df_clean, data_inputs.get_MDBA_codes(fmt), ps)
    elif fmt == "res.csv - MDBA":
        df_F, df_L, _ = sc.cleaner_res_csv_MDBA(sc.unpack_MDBA_res_csv_file(path, "Date", "Field"), ps)
    elif fmt == "Standard time-series":
        df = pd.read_csv(path, index_col="Date")
        df_F, df_L, _ = sc.cleaner_standard_timeseries(df, ps)
    elif fmt == "Source - NSW (res.csv)":
        data, header = sc.unpack_model_file(path, "Date", "Field")
        df_clean = sc.cleaner_NSW(sc.build_NSW_columns(data, header))
        df_F, df_L = sc.match_NSW_nodes(df_clean, data_inputs.get_NSW_codes(), ps)
    elif fmt == "IQQM - netcdf":
        df_F, df_L = sc.cleaner_netcdf_werp(sc.unpack_netcdf_as_dataframe(path), data_inputs.get_iqqm_codes(), ps)
    elif fmt == "ten thousand year":
        df = pd.read_csv(path, index_col="Date")
        df_F, df_L = sc.cleaner_ten_thousand_year(df, ps)
    elif fmt == "Northern connectivity":
        import northern_connectivity as nc

        scenarios = nc.unpack_northern_connectivity(path)  # one workbook, several scenarios: pick one by its code
        key = next((k for k in scenarios if k.lower() == sub.lower()), None) if sub else next(iter(scenarios), None)
        if key is None:
            raise ValueError(f"scenario {sub!r} not found in the workbook; available: {', '.join(scenarios)}")
        df_F, df_L = nc.cleaner_northern_connectivity(scenarios[key], ps)
    else:
        raise ValueError(f"unknown data format {fmt!r}")
    return df_F, df_L


def run_scenario(
    csv_bytes: bytes,
    sdl_gauges: list[str],
    scn_id: str | None = None,
    fmt: str = "Standard time-series",
    filename: str = "",
    sub: str = "",
    merge: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """py_ewr on the uploaded file (only the gauges the SDL unit needs); returns (rows, stats)."""
    import tempfile

    import numpy as np
    import pandas as pd
    from py_ewr import data_inputs, evaluate_EWRs
    from py_ewr.scenario_handling import ScenarioHandler

    if fmt not in FORMATS:
        raise ValueError(f"unknown data format {fmt!r}")
    table = data_inputs.get_EWR_table(None)
    sel = table[table.Gauge.isin(sdl_gauges)]
    partners = set()
    for col in ("Multigauge", "WeirpoolGauge"):
        partners |= {g for g in sel[col].dropna().astype(str) if g.strip()}
    wanted = set(sdl_gauges) | partners
    suffix = Path(filename).suffix.lower() or (".nc" if fmt == "IQQM - netcdf" else ".csv")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"scenario{suffix}"
        path.write_bytes(csv_bytes)
        try:
            df_F, df_L = load_frames(fmt, str(path), sub)
        except Exception as e:  # py_ewr's own readers fail in obscure ways on the wrong format
            detail = str(e).strip().splitlines()[0][:160] if str(e).strip() else type(e).__name__
            raise ValueError(f"Could not read this file as '{fmt}'. Check that you chose the right data format. ({type(e).__name__}: {detail})") from e
    df_F = df_F[[g for g in df_F.columns if str(g) in wanted]]
    df_L = df_L[[g for g in df_L.columns if str(g) in wanted]]
    if df_F.shape[1] + df_L.shape[1] == 0:
        raise ValueError(f"No columns in this '{fmt}' file match gauges used in this review.")
    found = sorted({str(g) for g in df_F.columns} & set(sdl_gauges))
    # A gauge that cannot be evaluated (e.g. level EWRs with no level data in the file) is skipped and reported
    # instead of stopping the whole run.
    calc_config = data_inputs.get_ewr_calc_config(None)
    results, events, skipped = {}, {}, {}
    for gauge in sorted(set(df_F.columns) | set(df_L.columns)):
        try:
            results[gauge], events[gauge] = evaluate_EWRs.calc_sorter(df_F, df_L, gauge, table, calc_config)
        except Exception as e:
            skipped[gauge] = f"{type(e).__name__}: {e}"
    if not results:
        raise ValueError("No gauge in the file could be evaluated: " + "; ".join(f"{g} ({m})" for g, m in skipped.items()))
    flow_gauges = sorted({str(g) for g in df_F.columns} & set(sdl_gauges))
    if scn_id and flow_gauges and isinstance(df_F.index, pd.DatetimeIndex):  # flows are kept for the flow charts (not for 10,000-year runs)
        STORE.mkdir(exist_ok=True)
        new = df_F[[g for g in df_F.columns if str(g) in flow_gauges]]
        old_path = STORE / f"{scn_id}.csv"
        if merge and old_path.exists():  # add to an existing scenario: keep its other gauges, replace overlapping ones
            old = pd.read_csv(old_path, index_col=0, parse_dates=True)
            new = old.drop(columns=[c for c in old.columns if c in new.columns]).join(new, how="outer")
        new.to_csv(old_path)
        forget_cached(scn_id)
    sh = ScenarioHandler("scenario.csv", "Standard time-series")
    sh.pu_ewr_statistics, sh.yearly_events, sh.flow_data, sh.level_data = {"scenario": results}, {"scenario": events}, df_F, df_L
    res = sh.get_ewr_results()
    # py_ewr's inter-event summary fails (KeyError 'Gauge') when no EWR in the file has any event at all, e.g. a completely dry
    # scenario (or none has a successful event): with no successful events there are no gaps, so use an empty table (as ewr_eval does).
    has_events = any(lst for by_pu in events.values() for by_code in by_pu.values() for by_year in by_code.values() for lst in by_year.values())
    inter = pd.DataFrame()
    if has_events:
        try:
            inter = sh.get_all_successful_interEvents()
        except KeyError as e:  # events exist but none succeeded (all shorter than MinSpell): py_ewr's summary is empty and fails the same way
            if e.args != ("Gauge",):
                raise
    res = res.copy()
    freq = pd.to_numeric(res["Frequency"], errors="coerce")
    target = pd.to_numeric(res["TargetFrequency"], errors="coerce")
    res["FR"] = np.where(target > 0, freq / target, np.nan)
    mrip = pd.to_numeric(res["MaxInterEventYears"], errors="coerce") * 365
    days = pd.to_numeric(res["TotalDays"], errors="coerce")
    gaps = {}
    if len(inter):
        inter = inter.copy()
        inter["interEventLength"] = pd.to_numeric(inter["interEventLength"], errors="coerce")
        for k, g in inter.groupby(["Gauge", "PlanningUnit", "Code"]):
            gaps[k] = g.interEventLength.dropna().to_numpy()
    etp: list[float | None] = []
    for k, m, d in zip(zip(res.Gauge, res.PlanningUnit, res.Code, strict=True), mrip, days, strict=False):
        if not (m > 0 and d > 0):
            etp.append(None)
            continue
        gap_days = gaps.get(k)
        etp.append(0.0 if gap_days is None else float(np.clip(gap_days - m, 0, None).sum() / d))
    res["ETP"] = etp
    rows = [
        {
            "pu": r.PlanningUnit,
            "gauge": str(r.Gauge),
            "ewr": r.Code,
            "fr": None if pd.isna(r.FR) else round(float(r.FR), 4),
            "et": None if r.ETP is None or pd.isna(r.ETP) else round(float(r.ETP), 4),
        }
        for r in res.itertuples()
    ]
    stats = {
        "gauges_in_file": len(found),
        "gauges_requested": len(sdl_gauges),
        "ewr_rows": len(rows),
        "missing_gauges": sorted(set(sdl_gauges) - set(found)),
        "skipped": skipped,
        "flow_gauges": flow_gauges if scn_id and isinstance(df_F.index, pd.DatetimeIndex) else [],
        "format": fmt,
    }
    return rows, stats


def worker(
    job_id: str,
    body: bytes,
    gauges: list[str],
    scn_id: str | None = None,
    fmt: str = "Standard time-series",
    filename: str = "",
    sub: str = "",
    merge: bool = False,
) -> None:
    job = JOBS[job_id]
    try:
        job["rows"], job["stats"] = run_scenario(body, gauges, scn_id, fmt, filename, sub, merge)
        job["status"] = "done"
    except Exception as e:  # reported to the page
        job["status"], job["message"] = "error", f"{type(e).__name__}: {e}"
        traceback.print_exc()
    job["elapsed"] = round(time.time() - job["t0"], 1)


# ---- flow analysis on stored scenario flows -------------------------------------------------------------------------------
def load_flows(scn_id: str | None) -> Any:
    import pandas as pd

    scn_id = re.sub(r"[^A-Za-z0-9_-]", "", scn_id or "")
    if scn_id not in FLOWS:
        path = STORE / f"{scn_id}.csv"
        if not path.exists():
            raise ValueError("the time series for this scenario is not on the server (re-upload it)")
        df = pd.read_csv(path, index_col=0, parse_dates=True).sort_index()
        # one row per day: the completeness checks count rows, so a repeated date would pass for a missing one. Exact repeats are
        # dropped; a date given twice with different flows is an error rather than a silent pick.
        dup = df.index.duplicated(keep=False)
        if dup.any():
            if (df[dup].groupby(level=0).nunique(dropna=False) > 1).any().any():
                raise ValueError("the stored time series has dates repeated with different flows (re-upload it)")
            df = df[~df.index.duplicated()]
        FLOWS[scn_id] = df
    return FLOWS[scn_id]


def forget_cached(scn_id: str) -> None:
    """Drop everything computed from a scenario's stored flows (after it is replaced, merged or deleted)."""
    FLOWS.pop(scn_id, None)
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


def gauge_series(scn_id: str, gauge: str) -> Any:
    df = load_flows(scn_id)
    if gauge not in df.columns:
        raise ValueError(f"gauge {gauge} has no flow series in this scenario")
    return df[gauge]


def nice_ceiling(x: float) -> int:
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

    months, cal = index.month.to_numpy(), index.year.to_numpy()
    wy = np.where(months >= 7, cal + 1, cal)
    year_lookup = np.unique(wy, return_inverse=True)[1]
    n_years = year_lookup.max() + 1
    mask = (months >= sm) & (months <= em) if sm <= em else (months >= sm) | (months <= em)
    fl = np.where(mask[:, None] & ~np.isnan(flows), flows, -np.inf)
    bounds = np.searchsorted(year_lookup, np.arange(n_years + 1))
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
        annual = np.full((len(durations), n_years), -np.inf)
        for y in np.flatnonzero(counted):
            x = fl[bounds[y] : bounds[y + 1], s]
            if consecutive:
                m, cur = x.copy(), 1  # m[i] = min(x[i : i + cur])
                for k, d in enumerate(durations):
                    while cur < d and len(m) > 1:
                        m, cur = np.minimum(m[:-1], x[cur:]), cur + 1
                    if d <= len(x) and cur == d and len(m):
                        annual[k, y] = m.max()
            else:
                vals = np.sort(x)[::-1]
                for k, d in enumerate(durations):
                    if d <= len(vals):
                        annual[k, y] = vals[d - 1]
        for k in range(len(durations)):
            if not n_counted:
                out[s, :, k] = np.nan
                continue
            n_unsuccessful = np.searchsorted(np.sort(annual[k][counted]), thresholds, side="left")
            out[s, :, k] = (n_counted - n_unsuccessful) * 100.0 / n_counted
    return out, n_counted


_EWR_CACHE: dict[str, Any] = {}


def ewr_list(gauge: str) -> list[dict[str, Any]]:
    """EWRs at a gauge that the EWR tool evaluates with flow_handle (consecutive days) or lowflow_handle (non-consecutive days)."""
    import pandas as pd
    from py_ewr import data_inputs

    if not _EWR_CACHE:
        cfg = data_inputs.get_ewr_calc_config(None)
        _EWR_CACHE["handler"] = {k: h for h in ("flow_handle", "lowflow_handle") for k in cfg.get(h, [])}
        _EWR_CACHE["table"] = data_inputs.get_EWR_table(None)
    t = _EWR_CACHE["table"]
    t = t[t.Gauge.astype(str) == gauge]
    out = []
    mi = {(c, pu): v for c, pu, v in zip(t.Code, t.PlanningUnitName, pd.to_numeric(t["MaxInter-event"], errors="coerce"), strict=True)}
    for r in t.itertuples():
        multi = isinstance(r.Multigauge, str) and r.Multigauge.strip()
        maxint = mi.get((r.Code, r.PlanningUnitName))
        h = _EWR_CACHE["handler"].get(f"{r.Code}-{'multigauge' if multi else 'single'}-{r.FlowLevelVolume}")
        num = lambda v: pd.to_numeric(v, errors="coerce")  # noqa: E731
        thr, dur, sm, em, tgt = num(r.FlowThresholdMin), num(r.Duration), num(r.StartMonth), num(r.EndMonth), num(r.TargetFrequency)
        if h is None or multi or pd.isna(thr) or pd.isna(dur) or pd.isna(sm) or pd.isna(em):
            continue
        out.append(
            {
                "code": r.Code,
                "pu": r.PlanningUnitName,
                "state": r.State,
                "handler": h,
                "consecutive": h == "flow_handle",
                "thr": float(thr),
                "dur": int(dur),
                "sm": int(sm),
                "em": int(em),
                "target": None if pd.isna(tgt) else float(tgt),
                "maxint": 0.0 if maxint is None or pd.isna(maxint) else float(maxint),
            }
        )
    # the same EWR is often defined in several planning units with identical settings: list it once
    uniq: dict[tuple[Any, ...], dict[str, Any]] = {}
    for e in sorted(out, key=lambda e: (e["code"], e["pu"])):
        k = (e["code"], e["thr"], e["dur"], e["sm"], e["em"], e["target"], e["maxint"], e["handler"])
        if k in uniq:
            uniq[k]["pus"].append(e["pu"])
        else:
            uniq[k] = {**e, "pus": [e["pu"]]}
    return list(uniq.values())


_EVAL_CACHE: dict[tuple[str, str, str, str], dict[str, Any]] = {}  # (scenario id, gauge, code, planning unit) -> one EWR evaluated by py_ewr (see ewr_eval)


def _water_year(d: Any) -> int:
    """py_ewr's water year label: the calendar year it starts in (1 Jul Y - 30 Jun Y+1)."""
    return d.year if d.month >= 7 else d.year - 1


def ewr_eval(scn_id: str, gauge: str, code: str, pu: str = "") -> dict[str, Any]:
    """py_ewr on one EWR at one gauge of a stored scenario: its yearly statistics, every threshold event, the successful events and
    the gaps between successful events, exactly as py_ewr computes them (calc_sorter + ScenarioHandler). Missing days stay
    missing (py_ewr counts them per year in missingDays); nothing is filled in."""
    import pandas as pd
    from py_ewr import data_inputs, evaluate_EWRs
    from py_ewr.scenario_handling import ScenarioHandler

    key = (scn_id, gauge, code, pu)
    if key in _EVAL_CACHE:
        return _EVAL_CACHE[key]
    table = data_inputs.get_EWR_table(None)
    sel = table[(table.Gauge.astype(str) == gauge) & (table.Code == code) & ((table.PlanningUnitName == pu) if pu else True)]
    if sel.empty:
        raise ValueError(f"EWR {code} is not defined at gauge {gauge}")
    sel = sel.iloc[:1]
    pu = sel.PlanningUnitName.iloc[0]
    num = lambda c: pd.to_numeric(sel[c], errors="coerce").iloc[0]  # noqa: E731
    df_F = daily_series(scn_id, gauge).to_frame(gauge)
    df_F.index.name = "Date"
    df_L = pd.DataFrame(index=df_F.index)
    results, events = evaluate_EWRs.calc_sorter(df_F, df_L, gauge, sel, data_inputs.get_ewr_calc_config(None))
    sh = ScenarioHandler("scenario.csv", "Standard time-series")
    sh.pu_ewr_statistics, sh.yearly_events, sh.flow_data, sh.level_data = {"scenario": {gauge: results}}, {"scenario": {gauge: events}}, df_F, df_L
    cols = ["startDate", "endDate", "interEventLength", "eventDuration"]
    pick = lambda d: d[(d.Gauge == gauge) & (d.Code == code) & (d.PlanningUnit == pu)].copy() if len(d) and "Gauge" in d else pd.DataFrame(columns=cols)  # noqa: E731
    if any(lst for lst in events[pu][code].values()):
        inter = pick(sh.get_all_successful_interEvents())
        succ = pick(sh.get_all_successful_events())
    else:  # py_ewr's summaries fail on an empty event ledger: no events, so none succeeded
        inter = succ = pd.DataFrame(columns=cols)
    yr = results[pu]
    yr = yr[[c for c in yr.columns if c.startswith(code + "_")]].rename(columns=lambda c: c[len(code) + 1 :])
    mi = num("MaxInter-event")
    out = {
        "pu": pu,
        "years": yr,
        "events": events[pu][code],
        "flow": df_F[gauge],
        "succ": succ,
        "inter": inter,
        "mrip_days": int(round(float(mi) * 365)) if mi and not pd.isna(mi) and mi > 0 else 0,
        "thr": float(num("FlowThresholdMin")),
        "dur": int(num("Duration")),
        "sm": int(num("StartMonth")),
        "em": int(num("EndMonth")),
        "target": None if pd.isna(num("TargetFrequency")) else float(num("TargetFrequency")),
    }
    opt = lambda c, f: None if pd.isna(num(c)) else f(num(c))  # noqa: E731
    out.update(
        {
            "min_spell": opt("MinSpell", int),
            "per_year": opt("EventsPerYear", int),
            "gap_tol": opt("WithinEventGapTolerance", int),
            "thr_max": opt("FlowThresholdMax", float),
        }
    )
    # py_ewr's assessment year: 1 July - 30 June, except when the timing window runs from the first half of the year into the second
    # (StartMonth <= 6 and EndMonth >= 7, e.g. Jun-Nov): then it starts on the 1st of StartMonth (evaluate_EWRs.wateryear_daily)
    out["ystart"] = out["sm"] if out["sm"] <= 6 and out["em"] >= 7 else 7
    _EVAL_CACHE[key] = out
    return out


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

    flow, mrip = ev["flow"], ev["mrip_days"]
    fdate = lambda d: pd.Timestamp(d).strftime("%Y-%m-%d")  # noqa: E731
    first, last = flow.index[0], flow.index[-1]
    inter = ev["inter"]
    rows = [(pd.Timestamp(a), pd.Timestamp(b)) for a, b in zip(inter.startDate, inter.endDate, strict=True)] if len(inter) else []
    nosucc = not len(ev["succ"])
    if nosucc:
        rows = [(first, last)]
    gaps = []
    for a, b in rows:
        n = (b - a).days + 1
        cen = bool(flow.loc[a:b].isna().any())
        gaps.append(
            {
                "start": fdate(a),
                "end": fdate(b),
                "days": n,
                "censored": cen,
                "boundary": bool(a <= first or b >= last),
                "excess": None if cen else (max(0, n - mrip) if mrip else 0),
                "no_success": nosucc,
            }
        )
    days = len(flow) - sum(g["days"] for g in gaps if g["censored"])
    etp = round(sum(g["excess"] for g in gaps if not g["censored"]) / days, 4) if mrip and days > 0 else None
    return gaps, etp


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


def ewr_years(q: dict[str, str]) -> dict[str, Any]:
    """One EWR, year by year and event by event, for one scenario or a base / comparison pair (py_ewr's own results throughout).
    years:  py_ewr's yearly statistics; a water year is complete when py_ewr reports no missing days and a full year of days.
    events: every threshold event py_ewr found (ok = it is one of py_ewr's successful events, i.e. at least MinSpell days long), with
            its calendar length (days = start to end; qual = the days at or above the threshold, fewer when the event bridges short
            dips within its gap tolerance), total volume (sum of the daily flows over every day from start to end, ML) and peak.
    gaps:   see ewr_gaps.
    paired: two scenarios: achievement compared over the water years complete in BOTH, year by year."""
    import pandas as pd

    gauge, code, pu = q["gauge"], q["ewr"], q.get("pu", "")
    ids = [("base", q["base"])] + ([("comp", q["comp"])] if q.get("comp") else [])
    fdate = lambda d: pd.Timestamp(d).strftime("%Y-%m-%d")  # noqa: E731
    out: dict[str, Any] = {"code": code, "gauge": gauge, "scenarios": {}}
    for k, sid in ids:
        ev = ewr_eval(sid, gauge, code, pu)
        y, flow = ev["years"], ev["flow"]
        col = lambda c: pd.to_numeric(y[c], errors="coerce").fillna(0).astype(int).tolist() if c in y else [0] * len(y)  # noqa: B023, E731
        wy = [int(v) for v in y.index]
        # a year counts when every day of the EWR's timing window in that assessment year has a flow (a missing day elsewhere in the
        # year does not matter); the window's days are taken from the calendar, so a window cut off by the record's ends is incomplete
        ys, sm, em = ev["ystart"], ev["sm"], ev["em"]
        in_win = lambda m: (m >= sm) & (m <= em) if sm <= em else (m >= sm) | (m <= em)  # noqa: B023, E731
        complete = []
        for w in wy:
            days = pd.date_range(pd.Timestamp(w, ys, 1), pd.Timestamp(w + 1, ys, 1) - pd.Timedelta(days=1))
            days = days[in_win(days.month)]
            complete.append(int(len(days) > 0 and flow.reindex(days).notna().all()))
        years = {
            "wy": wy,
            "achieved": col("eventYears"),
            "n_events": col("numEvents"),
            "n_all": col("numEventsAll"),
            "event_days": col("totalEventDays"),
            "max_event": col("maxEventDays"),
            "missing": col("missingDays"),
            "complete": complete,
        }
        ok = set(pd.to_datetime(ev["succ"].startDate).dt.strftime("%Y-%m-%d")) if len(ev["succ"]) else set()
        events = []
        for w, lst in ev["events"].items():
            for e in lst:
                if not e:
                    continue
                a, b = pd.Timestamp(e[0][0]), pd.Timestamp(e[-1][0])
                span = flow.loc[a:b]  # every calendar day from start to end (an event may skip days within its gap tolerance)
                events.append(
                    {
                        "wy": int(w),
                        "start": fdate(a),
                        "end": fdate(b),
                        "days": (b - a).days + 1,
                        "qual": len(e),
                        "vol": None if span.isna().any() else round(float(span.sum()), 1),
                        "peak": round(float(span.max()), 2),
                        "ok": fdate(a) in ok,
                    }
                )
        events.sort(key=lambda e: e["start"])
        gaps, etp = ewr_gaps(ev)
        out["scenarios"][k] = {"years": years, "events": events, "gaps": gaps, "etp": etp}
        out.update({kk: ev[kk] for kk in ("pu", "thr", "thr_max", "dur", "min_spell", "per_year", "gap_tol", "sm", "em", "ystart", "target", "mrip_days")})
    if "comp" in out["scenarios"]:
        b, c = out["scenarios"]["base"]["years"], out["scenarios"]["comp"]["years"]
        bi = {w: i for i, w in enumerate(b["wy"]) if b["complete"][i]}
        ci = {w: i for i, w in enumerate(c["wy"]) if c["complete"][i]}
        common = sorted(set(bi) & set(ci))
        pb = [b["achieved"][bi[w]] for w in common]
        pc = [c["achieved"][ci[w]] for w in common]
        n = len(common)
        out["paired"] = {
            "wy": common,
            "n": n,
            "base": int(sum(pb)),
            "comp": int(sum(pc)),
            "gained": sum(1 for x, z in zip(pb, pc, strict=True) if not x and z),
            "lost": sum(1 for x, z in zip(pb, pc, strict=True) if x and not z),
            "pct_base": round(100 * sum(pb) / n, 3) if n else None,
            "pct_comp": round(100 * sum(pc) / n, 3) if n else None,
        }
    return out


def _feb29(w: int) -> bool:
    """True when water year w (1 Jul w - 30 Jun w+1) holds a 29 February."""
    import calendar

    return calendar.isleap(w + 1)


def _runs_by_year(s: Any, hit: Any) -> tuple[dict[str, list[Any]], list[dict[str, Any]]]:
    """Runs of consecutive days where `hit` is true in the daily series `s` (missing days are never hits and break a run; a run
    beside a missing day or the record's ends is `censored`: its true length is unknown). Per water year (1 Jul - 30 Jun,
    labelled by its first calendar year as in py_ewr): the days that are hits, the longest run inside that year (a run crossing
    30 June is split at the year boundary for this) and the number of runs starting in it; numbers only for complete water years
    (every day observed). Returns (years, runs)."""
    import numpy as np
    import pandas as pd

    fdate = lambda d: pd.Timestamp(d).strftime("%Y-%m-%d")  # noqa: E731
    v = s.to_numpy(dtype=float)
    miss = np.isnan(v)
    hit = hit & ~miss
    wy = np.array([_water_year(d) for d in s.index])
    runs, i, n = [], 0, len(v)
    while i < n:
        if not hit[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and hit[j + 1]:
            j += 1
        runs.append(
            {
                "start": fdate(s.index[i]),
                "end": fdate(s.index[j]),
                "days": j - i + 1,
                "censored": bool((i > 0 and miss[i - 1]) or (j + 1 < n and miss[j + 1]) or i == 0 or j == n - 1),
                "wy": int(wy[i]),
            }
        )
        i = j + 1
    starts: dict[int, int] = {}
    for r in runs:
        starts[r["wy"]] = starts.get(r["wy"], 0) + 1
    years: dict[str, list[Any]] = {"wy": [], "days": [], "longest": [], "n": [], "complete": []}
    for w in np.unique(wy):
        m = wy == w
        comp = bool(m.sum() == (366 if _feb29(int(w)) else 365) and not miss[m].any())
        h, best, cur = hit[m], 0, 0
        for x in h:
            cur = cur + 1 if x else 0
            best = max(best, cur)
        years["wy"].append(int(w))
        years["complete"].append(int(comp))
        years["days"].append(int(h.sum()) if comp else None)
        years["longest"].append(best if comp else None)
        years["n"].append(starts.get(int(w), 0) if comp else None)
    return years, runs


def _pair_years(out: dict[str, Any]) -> dict[str, Any]:
    """Two scenarios: the water years complete in both."""
    if "comp" in out["scenarios"]:
        b, c = out["scenarios"]["base"]["years"], out["scenarios"]["comp"]["years"]
        bi = {w for w, k in zip(b["wy"], b["complete"], strict=True) if k}
        ci = {w for w, k in zip(c["wy"], c["complete"], strict=True) if k}
        out["paired"] = {"wy": sorted(bi & ci)}
    return out


def spells(q: dict[str, str]) -> dict[str, Any]:
    """Low-flow spells: runs of consecutive days with flow below `thr` ML/d (see _runs_by_year); with two scenarios they are compared
    over the water years complete in both."""
    gauge, thr = q["gauge"], float(q["thr"])
    ids = [("base", q["base"])] + ([("comp", q["comp"])] if q.get("comp") else [])
    out: dict[str, Any] = {"gauge": gauge, "thr": thr, "scenarios": {}}
    for k, sid in ids:
        s = daily_series(sid, gauge)
        years, runs = _runs_by_year(s, s.to_numpy(dtype=float) < thr)
        years["below"] = years.pop("days")
        out["scenarios"][k] = {
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
    from flow_regime_mappings import regimes_for

    gauge = q["gauge"]
    by: dict[str, list[dict[str, Any]]] = {}
    for e in ewr_list(gauge):
        for rg in regimes_for(e["code"], e.get("state") or ""):
            if rg != "excluded":
                by.setdefault(rg, []).append(e)
    comps = []
    for rg, es in by.items():
        thr = min(e["thr"] for e in es)
        comps.append({"regime": rg, "thr": thr, "codes": sorted({e["code"] for e in es}), "at_thr": sorted({e["code"] for e in es if e["thr"] == thr})})
    comps.sort(key=lambda c: c["thr"])
    ids = [("base", q["base"])] + ([("comp", q["comp"])] if q.get("comp") else [])
    out: dict[str, Any] = {"gauge": gauge, "regimes": comps, "scenarios": {}}
    for k, sid in ids:
        s = daily_series(sid, gauge)
        v = s.to_numpy(dtype=float)
        per = []
        for c in comps:
            years, runs = _runs_by_year(s, v >= c["thr"])
            per.append(years)
        out["scenarios"][k] = {"regimes": per, "years": per[0] if per else {"wy": [], "complete": []}}
    return _pair_years(out)


def heatmap(q: dict[str, str]) -> dict[str, Any]:
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
    spacing = max(1, int(math.ceil((xlim + 1) / 400)))
    thresholds = np.arange(0, xlim + 1, spacing)
    durations = np.arange(1, dmax + 1)
    af, n_years = achievement_frequencies(df.to_numpy(dtype=float), df.index, thresholds, durations, sm, em, consec)
    z = lambda a: np.round(a.T, 3).tolist()  # (durations x thresholds), as plotted  # noqa: E731
    out = {
        "thresholds": thresholds.tolist(),
        "durations": durations.tolist(),
        "xlim": xlim,
        "spacing": spacing,
        "years": n_years,
        "dates": [df.index[0].strftime("%Y-%m-%d"), df.index[-1].strftime("%Y-%m-%d")],
        "base": z(af[0]),
    }
    if len(ids) == 2:
        out["comp"], out["diff"] = z(af[1]), z(af[1] - af[0])
    if q.get("mthr") and q.get("mdur"):  # exact % of years at one (threshold, duration), e.g. an EWR's own requirement
        one, _ = achievement_frequencies(df.to_numpy(dtype=float), df.index, np.array([float(q["mthr"])]), np.array([int(float(q["mdur"]))]), sm, em, consec)
        mark = [round(float(v), 3) for v in one[:, 0, 0]]
        if all(math.isfinite(v) for v in mark):  # no complete shared year -> no mark (the page then shows no EWR table)
            out["mark"] = mark
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
    dfdc (differential flow duration curve: change in % of time flow >= x, comparison - base).
    thr (comma-separated flows): % of time exceeded at each, for each scenario."""
    import numpy as np
    import pandas as pd

    gauge = q["gauge"]
    kind = q.get("kind") or ("fdc" if q.get("comp") else "hydro")
    paired = _paired(q, gauge)
    if kind == "hydro":
        # daily flows (missing days stay null so the line breaks there) and the volume of each complete water year; with two
        # scenarios both lines are on the dates they share, and a water year's volumes are compared only when both are complete
        df = pd.concat({k: daily_series(q[k], gauge) for k in ("base", "comp") if q.get(k)}, axis=1, join="inner" if q.get("comp") else "outer")
        if df.empty:
            raise ValueError("the scenarios share no dates at this gauge")
        out: dict[str, Any] = {"kind": "hydro", "dates": [d.strftime("%Y-%m-%d") for d in df.index]}
        for k in df.columns:
            out[k] = [None if np.isnan(v) else round(float(v), 2) for v in df[k].to_numpy(dtype=float)]
        out["flow"] = out["base"]
        wy = np.array([_water_year(d) for d in df.index])
        full = df.notna().all(axis=1).to_numpy()
        year_volumes: list[dict[str, Any]] = []
        for w in np.unique(wy):
            m = wy == w
            if m.sum() == (366 if _feb29(int(w)) else 365) and full[m].all():
                year_volumes.append({"wy": int(w), **{k: round(float(df[k].to_numpy()[m].sum()), 1) for k in df.columns}})
        out["years"] = year_volumes
        out["missing_days"] = int((~full).sum())
        return out
    if kind == "mdfdc":
        # monthly differential flow duration: for each calendar month, the change (comparison - base) in the % of that month's days at or above each flow
        if not q.get("comp"):
            raise ValueError("the monthly differential flow duration needs two scenarios")
        ser = paired
        order = [7, 8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6]
        by = {k: {m: np.sort(v[v.index.month == m].to_numpy(dtype=float)) for m in order} for k, v in ser.items()}
        top = 0.0
        for k in by:
            for m in order:
                a = by[k][m]
                if len(a):
                    top = max(top, float(np.percentile(a, 99)))  # flow still reached on 1% of that month's days
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
            if len(by["base"][m]):
                base[:, j] = exceed_pct(by["base"][m], flows)
            if len(by["comp"][m]):
                comp[:, j] = exceed_pct(by["comp"][m], flows)
        diff = comp - base
        z = lambda a: [[None if np.isnan(v) else round(float(v), 3) for v in row] for row in a]  # noqa: E731
        return {"kind": "mdfdc", "months": order, "flows": np.round(flows, 2).tolist(), "diff": z(diff), "base": z(base), "comp": z(comp), "ymax": ymax}
    sm, em = int(q.get("sm") or 0), int(q.get("em") or 0)  # optional timing window (months, may wrap the year end): only days in it are used

    def windowed(series: Any) -> Any:
        series = series.dropna()
        if not (1 <= sm <= 12 and 1 <= em <= 12):
            return series
        m = series.index.month
        return series[(m >= sm) & (m <= em)] if sm <= em else series[(m >= sm) | (m <= em)]

    vals = {k: np.sort(windowed(v).to_numpy(dtype=float)) for k, v in paired.items()}
    if any(len(v) == 0 for v in vals.values()):
        raise ValueError("no flow data inside that timing window")
    thrs = [float(t) for t in (q.get("thr") or "").split(",") if t.strip()]
    marks = [{"thr": t, **{k: round(float(exceed_pct(v, t)), 3) for k, v in vals.items()}} for t in thrs]
    if kind == "fdc":
        p = np.linspace(0, 100, 401)
        fdc: dict[str, Any] = {"kind": "fdc", "p": np.round(p, 2).tolist(), "marks": marks}
        for k, v in vals.items():
            fdc[k] = np.round(np.percentile(v, 100 - p), 2).tolist()  # flow equalled or exceeded p% of the time
        if "comp" in vals:
            fdc["diff"] = np.round(np.array(fdc["comp"]) - np.array(fdc["base"]), 2).tolist()
        return fdc
    if kind == "dfdc":
        if "comp" not in vals:
            raise ValueError("the differential flow duration curve needs two scenarios")
        allv = np.concatenate(list(vals.values()))
        pos = allv[allv > 0]
        if not len(pos):
            raise ValueError("no positive flows to plot")
        lo = max(float(pos.min()), 0.01)
        xs = np.geomspace(lo, float(allv.max()), 500)
        pb, pc = exceed_pct(vals["base"], xs), exceed_pct(vals["comp"], xs)
        return {
            "kind": "dfdc",
            "x": np.round(xs, 3).tolist(),
            "base": np.round(pb, 3).tolist(),
            "comp": np.round(pc, 3).tolist(),
            "diff": np.round(pc - pb, 3).tolist(),
            "marks": marks,
        }
    raise ValueError(f"unknown curve kind {kind!r}")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, directory=str(ROOT), **k)

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

    def do_GET(self) -> None:
        u = urlparse(self.path)
        if u.path == "/api/forget":
            return self._json({"message": "method not allowed"}, 405, {"Allow": "POST"})
        if u.path == "/api/ping":
            return self._json({"ok": True, "formats": FORMATS})
        if u.path == "/api/job":
            job = JOBS.get(parse_qs(u.query).get("id", [""])[0])
            if not job:
                return self._json({"status": "error", "message": "unknown job"}, 404)
            out = {k: v for k, v in job.items() if k != "t0"}
            if job["status"] == "running":
                out["elapsed"] = round(time.time() - job["t0"], 1)
            return self._json(out)
        if u.path in ("/api/heatmap", "/api/curves", "/api/ewrs", "/api/interevents", "/api/ewryears", "/api/spells", "/api/regimes"):
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if u.path == "/api/interevents":
                    return self._json(interevents(q))
                if u.path == "/api/ewrs":
                    return self._json({"ewrs": ewr_list(q.get("gauge", ""))})
                if u.path == "/api/ewryears":
                    return self._json(ewr_years(q))
                if u.path == "/api/spells":
                    return self._json(spells(q))
                if u.path == "/api/regimes":
                    return self._json(regimes(q))
                return self._json(heatmap(q) if u.path == "/api/heatmap" else curves(q))
            except Exception as e:
                traceback.print_exc()
                return self._json({"error": f"{type(e).__name__}: {e}"}, 400)
        return super().do_GET()

    def do_POST(self) -> None:
        u = urlparse(self.path)
        if u.path == "/api/forget":
            if not _state_change_allowed(self.headers.get("Origin"), self.headers.get("Host"), cast(tuple[Any, ...], self.server.server_address)):
                return self._json({"message": "origin or host not allowed"}, 403)
            sid = re.sub(r"[^A-Za-z0-9_-]", "", parse_qs(u.query).get("id", [""])[0])
            forget_cached(sid)
            (STORE / f"{sid}.csv").unlink(missing_ok=True)
            return self._json({"ok": True})
        if u.path != "/api/run":
            return self._json({"message": "not found"}, 404)
        if not _state_change_allowed(self.headers.get("Origin"), self.headers.get("Host"), cast(tuple[Any, ...], self.server.server_address)):
            return self._json({"message": "origin or host not allowed"}, 403)
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 < n <= MAX_BYTES:
            return self._json({"message": "file is empty or too large"}, 400)
        body = self.rfile.read(n)
        gauges = [g for g in re.split(r"[,\s]+", self.headers.get("X-Gauges", "")) if g]
        if not gauges:
            return self._json({"message": "no gauges given"}, 400)
        jid = uuid.uuid4().hex[:10]
        JOBS[jid] = {"status": "running", "t0": time.time()}
        threading.Thread(
            target=worker,
            args=(
                jid,
                body,
                gauges,
                re.sub(r"[^A-Za-z0-9_-]", "", self.headers.get("X-Scn-Id", "")) or None,
                self.headers.get("X-Format", "Standard time-series"),
                self.headers.get("X-Filename", ""),
                self.headers.get("X-Subscenario", ""),
                self.headers.get("X-Merge") == "1",
            ),
            daemon=True,
        ).start()
        self._json({"job": jid})

    def end_headers(self) -> None:
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:
        if "/api/job" not in str(args[0] if args else ""):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8792
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"EWR analysis tool on http://localhost:{port}/ewr_analysis_dashboard.html")
    srv.serve_forever()
