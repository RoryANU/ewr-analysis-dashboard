"""Local server for the EWR analysis dashboard: serves the page and runs py_ewr on an uploaded scenario.

    python3 src/server.py [port]        (default 8792, localhost only; then open http://localhost:8792/)

Endpoints (used by ewr_analysis_dashboard.html):
    GET  /api/ping                     -> {"ok": true}
    POST /api/run                      body = a model output file in one of the EWR tool's formats (FORMATS below)
                                       headers X-Format (model_format), X-Filename, X-Scn-Id, X-Merge (1 = add the flows to the scenario stored under X-Scn-Id), X-Gauges (gauge ids to look for in the file)
                                       -> {"job": id}
    GET  /api/job?id=<id>              -> {"status": "running" | "done" | "error", ...}

    GET  /api/heatmap?base=<id>[&comp=<id>]&gauge=<g>&xlim=&dmax=&sm=&em=   threshold x duration achievement heat map data
    GET  /api/ewrs?gauge=<g>                                               EWRs at a gauge handled by flow_handle / lowflow_handle
    GET  /api/interevents?scn=<id>&gauge=<g>&ewr=<code>&pu=<name>          gaps between successful events longer than the EWR's max inter-event period
    GET  /api/curves?base=<id>[&comp=<id>]&gauge=<g>&kind=hydro|fdc|dfdc|mdfdc[&thr=400,2000][&ymin=][&ymax=]   hydrograph, flow duration curve, differential flow duration curve, monthly differential flow duration
    POST /api/forget?id=<id>           delete a stored scenario

An uploaded scenario's daily flows are kept in .user_scenarios/<id>.csv (repository root) so the flow charts can be drawn later.

For each EWR in the uploaded scenario the job returns the two metrics the tool uses:
    FR  = Frequency / TargetFrequency                    (py_ewr summary results, as in the MDBA outputs)
    ETP = L / TotalDays, where L = sum over the gaps between successful events of max(0, gap days - MRIP days)
          and MRIP days = MaxInterEventYears x 365       (py_ewr successful inter-events)
"""
import io
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
from urllib.parse import parse_qs, urlparse, urlsplit

HERE = Path(__file__).parent
ROOT = HERE.parent                            # repository root: the page is served from here; py_ewr comes from pip (pip install -r requirements.txt)
warnings.filterwarnings("ignore")

JOBS = {}
STORE = ROOT / ".user_scenarios"
FLOWS = {}          # scenario id -> flow DataFrame (date index, one column per gauge)
MAX_BYTES = 400 * 1024 * 1024


def _authority(hostport, default_port=None):
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


def _loopback_host(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _state_change_allowed(origin, host, server_address):
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
        if (parsed_origin.scheme.lower() not in ("http", "https") or parsed_origin.path
                or parsed_origin.query or parsed_origin.fragment):
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
FORMATS = ["Standard time-series", "Bigmod - MDBA", "FIRM - MDBA", "res.csv - MDBA", "Source - NSW (res.csv)", "IQQM - netcdf", "ten thousand year", "Northern connectivity"]


def load_frames(fmt, path, sub=""):
    """The reading step of ScenarioHandler.process_scenarios for one model_format: file -> (flow frame, level frame),
    one column per gauge number (py_ewr's own cleaners and node-matching tables do the work)."""
    import pandas as pd
    from py_ewr import data_inputs
    from py_ewr import scenario_handling as sc
    ps = None                                                        # default parameter sheet
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
        scenarios = nc.unpack_northern_connectivity(path)         # one workbook, several scenarios: pick one by its code
        key = next((k for k in scenarios if k.lower() == sub.lower()), None) if sub else next(iter(scenarios), None)
        if key is None:
            raise ValueError(f"scenario {sub!r} not found in the workbook; available: {', '.join(scenarios)}")
        df_F, df_L = nc.cleaner_northern_connectivity(scenarios[key], ps)
    else:
        raise ValueError(f"unknown data format {fmt!r}")
    return df_F, df_L


def run_scenario(csv_bytes, sdl_gauges, scn_id=None, fmt="Standard time-series", filename="", sub="", merge=False):
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
        except Exception as e:                                       # py_ewr's own readers fail in obscure ways on the wrong format
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
    if scn_id and flow_gauges and isinstance(df_F.index, pd.DatetimeIndex):      # flows are kept for the flow charts (not for 10,000-year runs)
        STORE.mkdir(exist_ok=True)
        new = df_F[[g for g in df_F.columns if str(g) in flow_gauges]]
        old_path = STORE / f"{scn_id}.csv"
        if merge and old_path.exists():                                              # add to an existing scenario: keep its other gauges, replace overlapping ones
            old = pd.read_csv(old_path, index_col=0, parse_dates=True)
            new = old.drop(columns=[c for c in old.columns if c in new.columns]).join(new, how="outer")
        new.to_csv(old_path)
        FLOWS.pop(scn_id, None)
    sh = ScenarioHandler("scenario.csv", "Standard time-series")
    sh.pu_ewr_statistics, sh.yearly_events, sh.flow_data, sh.level_data = {"scenario": results}, {"scenario": events}, df_F, df_L
    res = sh.get_ewr_results()
    inter = sh.get_all_successful_interEvents()
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
    etp = []
    for k, m, d in zip(zip(res.Gauge, res.PlanningUnit, res.Code), mrip, days):
        if not (m > 0 and d > 0):
            etp.append(None)
            continue
        e = gaps.get(k)
        etp.append(0.0 if e is None else float(np.clip(e - m, 0, None).sum() / d))
    res["ETP"] = etp
    rows = [{"pu": r.PlanningUnit, "gauge": str(r.Gauge), "ewr": r.Code,
             "fr": None if pd.isna(r.FR) else round(float(r.FR), 4),
             "et": None if r.ETP is None or pd.isna(r.ETP) else round(float(r.ETP), 4)} for r in res.itertuples()]
    stats = {"gauges_in_file": len(found), "gauges_requested": len(sdl_gauges), "ewr_rows": len(rows),
             "missing_gauges": sorted(set(sdl_gauges) - set(found)), "skipped": skipped, "flow_gauges": flow_gauges if scn_id and isinstance(df_F.index, pd.DatetimeIndex) else [], "format": fmt}
    return rows, stats


def worker(job_id, body, gauges, scn_id=None, fmt="Standard time-series", filename="", sub="", merge=False):
    job = JOBS[job_id]
    try:
        job["rows"], job["stats"] = run_scenario(body, gauges, scn_id, fmt, filename, sub, merge)
        job["status"] = "done"
    except Exception as e:                                    # reported to the page
        job["status"], job["message"] = "error", f"{type(e).__name__}: {e}"
        traceback.print_exc()
    job["elapsed"] = round(time.time() - job["t0"], 1)


# ---- flow analysis on stored scenario flows -------------------------------------------------------------------------------
def load_flows(scn_id):
    import pandas as pd
    scn_id = re.sub(r"[^A-Za-z0-9_-]", "", scn_id or "")
    if scn_id not in FLOWS:
        path = STORE / f"{scn_id}.csv"
        if not path.exists():
            raise ValueError("the time series for this scenario is not on the server (re-upload it)")
        FLOWS[scn_id] = pd.read_csv(path, index_col=0, parse_dates=True)
    return FLOWS[scn_id]


def gauge_series(scn_id, gauge):
    df = load_flows(scn_id)
    if gauge not in df.columns:
        raise ValueError(f"gauge {gauge} has no flow series in this scenario")
    return df[gauge]


def nice_ceiling(x):
    if not x or x <= 0:
        return 100
    k = 10 ** max(int(math.floor(math.log10(x))) - 1, 0)
    return int(math.ceil(x / k) * k)


def _json_safe(obj):
    """Recursively replace non-finite floats (NaN, +-inf) with None so the response is strict JSON (null in the browser)."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def _window_days(wy_label, sm, em):
    """Calendar days of the timing window (months sm..em, may wrap the year end) inside water year `wy_label` (1 Jul - 30 Jun)."""
    import pandas as pd
    m = pd.date_range(f"{wy_label - 1}-07-01", f"{wy_label}-06-30").month.to_numpy()
    return int(((m >= sm) & (m <= em) if sm <= em else (m >= sm) | (m <= em)).sum())


def achievement_frequencies(flows, index, thresholds, durations, sm, em, consecutive=False):
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
    complete = np.stack([(np.bincount(year_lookup, weights=mask & ~np.isnan(flows[:, s]), minlength=n_years) == expected) & (expected > 0)
                         for s in range(flows.shape[1])])
    counted = complete.all(axis=0)
    n_counted = int(counted.sum())
    out = np.zeros((flows.shape[1], len(thresholds), len(durations)), dtype=np.float32)
    for s in range(flows.shape[1]):
        annual = np.full((len(durations), n_years), -np.inf)
        for y in np.flatnonzero(counted):
            x = fl[bounds[y]:bounds[y + 1], s]
            if consecutive:
                m, cur = x.copy(), 1                       # m[i] = min(x[i : i + cur])
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


_EWR_CACHE = {}


def ewr_list(gauge):
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
    mi = {(c, pu): v for c, pu, v in zip(t.Code, t.PlanningUnitName, pd.to_numeric(t["MaxInter-event"], errors="coerce"))}
    for r in t.itertuples():
        multi = isinstance(r.Multigauge, str) and r.Multigauge.strip()
        h = _EWR_CACHE["handler"].get(f"{r.Code}-{'multigauge' if multi else 'single'}-{r.FlowLevelVolume}")
        num = lambda v: pd.to_numeric(v, errors="coerce")
        thr, dur, sm, em, tgt = num(r.FlowThresholdMin), num(r.Duration), num(r.StartMonth), num(r.EndMonth), num(r.TargetFrequency)
        if h is None or multi or pd.isna(thr) or pd.isna(dur) or pd.isna(sm) or pd.isna(em):
            continue
        out.append({"code": r.Code, "pu": r.PlanningUnitName, "handler": h, "consecutive": h == "flow_handle", "thr": float(thr), "dur": int(dur),
                    "sm": int(sm), "em": int(em), "target": None if pd.isna(tgt) else float(tgt),
                    "maxint": 0.0 if pd.isna(mi.get((r.Code, r.PlanningUnitName))) else float(mi.get((r.Code, r.PlanningUnitName)))})
    # the same EWR is often defined in several planning units with identical settings: list it once
    uniq = {}
    for e in sorted(out, key=lambda e: (e["code"], e["pu"])):
        k = (e["code"], e["thr"], e["dur"], e["sm"], e["em"], e["target"], e["maxint"], e["handler"])
        if k in uniq:
            uniq[k]["pus"].append(e["pu"])
        else:
            uniq[k] = {**e, "pus": [e["pu"]]}
    return list(uniq.values())


def interevents(q):
    """Gaps between successful events of one EWR (py_ewr successful inter-events) that exceed its maximum inter-event period."""
    import pandas as pd
    from py_ewr import data_inputs, evaluate_EWRs
    from py_ewr.scenario_handling import ScenarioHandler
    gauge, code, pu = q["gauge"], q["ewr"], q.get("pu", "")
    s = gauge_series(q["scn"], gauge).dropna()
    table = data_inputs.get_EWR_table(None)
    sel = table[(table.Gauge.astype(str) == gauge) & (table.Code == code) & ((table.PlanningUnitName == pu) if pu else True)]
    if sel.empty:
        raise ValueError(f"EWR {code} is not defined at gauge {gauge}")
    mi = pd.to_numeric(sel["MaxInter-event"], errors="coerce").iloc[0]
    if not mi or pd.isna(mi) or mi <= 0:
        return {"mrip_days": 0, "exceed": [], "n_gaps": 0}
    pu = sel.PlanningUnitName.iloc[0]
    df_F = s.to_frame(gauge)
    df_F.index.name = "Date"
    df_F = df_F.reindex(pd.date_range(df_F.index[0], df_F.index[-1]))
    df_F.index.name = "Date"
    df_L = pd.DataFrame(index=df_F.index)
    calc_config = data_inputs.get_ewr_calc_config(None)
    results, events = evaluate_EWRs.calc_sorter(df_F, df_L, gauge, sel, calc_config)
    sh = ScenarioHandler("scenario.csv", "Standard time-series")
    sh.pu_ewr_statistics, sh.yearly_events, sh.flow_data, sh.level_data = {"scenario": {gauge: results}}, {"scenario": {gauge: events}}, df_F, df_L
    inter = sh.get_all_successful_interEvents()
    inter = inter[(inter.Gauge == gauge) & (inter.Code == code) & (inter.PlanningUnit == pu)].copy()
    inter["interEventLength"] = pd.to_numeric(inter["interEventLength"], errors="coerce")
    mrip = int(round(float(mi) * 365))
    big = inter[inter.interEventLength > mrip]
    fdate = lambda d: pd.Timestamp(d).strftime("%Y-%m-%d")
    return {"mrip_days": mrip, "maxint_years": float(mi), "n_gaps": int(len(inter)),
            "exceed": [{"start": fdate(a), "end": fdate(b), "days": int(n)} for a, b, n in zip(big.startDate, big.endDate, big.interEventLength)]}


def heatmap(q):
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
    z = lambda a: np.round(a.T, 3).tolist()          # (durations x thresholds), as plotted
    out = {"thresholds": thresholds.tolist(), "durations": durations.tolist(), "xlim": xlim, "spacing": spacing, "years": n_years,
           "dates": [df.index[0].strftime("%Y-%m-%d"), df.index[-1].strftime("%Y-%m-%d")], "base": z(af[0])}
    if len(ids) == 2:
        out["comp"], out["diff"] = z(af[1]), z(af[1] - af[0])
    if q.get("mthr") and q.get("mdur"):               # exact % of years at one (threshold, duration), e.g. an EWR's own requirement
        one, _ = achievement_frequencies(df.to_numpy(dtype=float), df.index, np.array([float(q["mthr"])]), np.array([int(float(q["mdur"]))]), sm, em, consec)
        mark = [round(float(v), 3) for v in one[:, 0, 0]]
        if all(math.isfinite(v) for v in mark):       # no complete shared year -> no mark (the page then shows no EWR table)
            out["mark"] = mark
    out["consecutive"] = consec
    return out


def exceed_pct(sorted_vals, x):
    """% of days the flow is equal to or above x (sorted_vals ascending)."""
    import numpy as np
    n = len(sorted_vals)
    return 100.0 * (n - np.searchsorted(sorted_vals, x, side="left")) / n


def curves(q):
    """kind: hydro (daily flow, one scenario) | fdc (flow duration curve, one or two scenarios) |
    dfdc (differential flow duration curve: change in % of time flow >= x, comparison - base).
    thr (comma-separated flows): % of time exceeded at each, for each scenario."""
    import numpy as np
    gauge = q["gauge"]
    kind = q.get("kind") or ("fdc" if q.get("comp") else "hydro")
    if kind == "hydro":
        s = gauge_series(q["base"], gauge).dropna()
        return {"kind": "hydro", "dates": [d.strftime("%Y-%m-%d") for d in s.index], "flow": np.round(s.to_numpy(), 2).tolist()}
    if kind == "mdfdc":
        # monthly differential flow duration: for each calendar month, the change (comparison - base) in the % of that month's days at or above each flow
        if not q.get("comp"):
            raise ValueError("the monthly differential flow duration needs two scenarios")
        ser = {k: gauge_series(q[k], gauge).dropna() for k in ("base", "comp")}
        order = [7, 8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6]
        by = {k: {m: np.sort(v[v.index.month == m].to_numpy(dtype=float)) for m in order} for k, v in ser.items()}
        top = 0.0
        for k in by:
            for m in order:
                a = by[k][m]
                if len(a):
                    top = max(top, float(np.percentile(a, 99)))          # flow still reached on 1% of that month's days
        ymax = float(q["ymax"]) if q.get("ymax") else top
        if ymax <= 0:
            raise ValueError("no positive flows to plot")
        if q.get("ymin") not in (None, ""):                          # zoomed in: the same 200 steps spread over the chosen range
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
        z = lambda a: [[None if np.isnan(v) else round(float(v), 3) for v in row] for row in a]
        return {"kind": "mdfdc", "months": order, "flows": np.round(flows, 2).tolist(), "diff": z(diff), "base": z(base), "comp": z(comp), "ymax": ymax}
    sm, em = int(q.get("sm") or 0), int(q.get("em") or 0)       # optional timing window (months, may wrap the year end): only days in it are used

    def windowed(series):
        series = series.dropna()
        if not (1 <= sm <= 12 and 1 <= em <= 12):
            return series
        m = series.index.month
        return series[(m >= sm) & (m <= em)] if sm <= em else series[(m >= sm) | (m <= em)]
    vals = {k: np.sort(windowed(gauge_series(q[k], gauge)).to_numpy(dtype=float)) for k in ("base", "comp") if q.get(k)}
    if any(len(v) == 0 for v in vals.values()):
        raise ValueError("no flow data inside that timing window")
    thrs = [float(t) for t in (q.get("thr") or "").split(",") if t.strip()]
    marks = [{"thr": t, **{k: round(float(exceed_pct(v, t)), 3) for k, v in vals.items()}} for t in thrs]
    if kind == "fdc":
        p = np.linspace(0, 100, 401)
        out = {"kind": "fdc", "p": np.round(p, 2).tolist(), "marks": marks}
        for k, v in vals.items():
            out[k] = np.round(np.percentile(v, 100 - p), 2).tolist()        # flow equalled or exceeded p% of the time
        if "comp" in vals:
            out["diff"] = np.round(np.array(out["comp"]) - np.array(out["base"]), 2).tolist()
        return out
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
        return {"kind": "dfdc", "x": np.round(xs, 3).tolist(), "base": np.round(pb, 3).tolist(), "comp": np.round(pc, 3).tolist(),
                "diff": np.round(pc - pb, 3).tolist(), "marks": marks}
    raise ValueError(f"unknown curve kind {kind!r}")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(ROOT), **k)

    def _json(self, obj, code=200, headers=None):
        data = json.dumps(_json_safe(obj), allow_nan=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
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
        if u.path in ("/api/heatmap", "/api/curves", "/api/ewrs", "/api/interevents"):
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if u.path == "/api/interevents":
                    return self._json(interevents(q))
                if u.path == "/api/ewrs":
                    return self._json({"ewrs": ewr_list(q.get("gauge", ""))})
                return self._json(heatmap(q) if u.path == "/api/heatmap" else curves(q))
            except Exception as e:
                traceback.print_exc()
                return self._json({"error": f"{type(e).__name__}: {e}"}, 400)
        return super().do_GET()

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/forget":
            if not _state_change_allowed(self.headers.get("Origin"), self.headers.get("Host"), self.server.server_address):
                return self._json({"message": "origin or host not allowed"}, 403)
            sid = re.sub(r"[^A-Za-z0-9_-]", "", parse_qs(u.query).get("id", [""])[0])
            FLOWS.pop(sid, None)
            (STORE / f"{sid}.csv").unlink(missing_ok=True)
            return self._json({"ok": True})
        if u.path != "/api/run":
            return self._json({"message": "not found"}, 404)
        if not _state_change_allowed(self.headers.get("Origin"), self.headers.get("Host"), self.server.server_address):
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
        threading.Thread(target=worker, args=(jid, body, gauges, re.sub(r"[^A-Za-z0-9_-]", "", self.headers.get("X-Scn-Id", "")) or None,
                                           self.headers.get("X-Format", "Standard time-series"), self.headers.get("X-Filename", ""), self.headers.get("X-Subscenario", ""), self.headers.get("X-Merge") == "1"), daemon=True).start()
        self._json({"job": jid})

    def end_headers(self):
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        if "/api/job" not in str(args[0] if args else ""):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8792
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"EWR analysis tool on http://localhost:{port}/ewr_analysis_dashboard.html")
    srv.serve_forever()
