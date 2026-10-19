"""Optional observed-gauge acquisition, routing audit support, and raw range cache."""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from importlib import resources
from pathlib import Path
from typing import Any, Callable

import pandas as pd

import observed_source
from observed_source import RAW_COLUMNS, NoData, ObservedSourceError, StationNotFound

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".observed_cache"
CACHE_VERSION = "2"  # 2: unavailable-day placeholders dropped, numeric VALUE, UNITS column
PARAMETER_SHEET = ROOT / "data/reference/parameter_sheet.csv"
GAUGE_ID = re.compile(r"(?:A)?\d+[A-Z]*", re.I)
SUPPORTED_STATES = tuple(observed_source.ENDPOINTS)
DEFAULT_TIMEOUT = 90.0  # per gauge and variable; a full record takes the services 10-60 s to start sending
DEFAULT_TTL = 24 * 60 * 60
DEFAULT_RETRIES = 2
_CACHE_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_CACHE_LOCKS_GUARD = threading.Lock()
_CLIENT = observed_source.StateClient()
_ROUTES: dict[str, str] = {}  # gauge -> the state service that answered for it in this process


@dataclass
class GaugeResult:
    frame: pd.DataFrame
    status: str
    message: str = ""


def sdl_gauges(sdl: str) -> list[str]:
    """Return gauges in an SDL, including partner gauges named by Multigauge."""
    params = pd.read_csv(PARAMETER_SHEET, dtype=str).fillna("")
    selected = params[params["SWSDLName"].str.strip() == sdl.strip()]
    gauges = {value for value in selected["Gauge"].str.strip() if GAUGE_ID.fullmatch(value)}
    return sorted(gauges)


@functools.lru_cache(maxsize=1)
def sheet_states() -> dict[str, frozenset[str]]:
    """Gauge -> the states parameter_sheet.csv lists it under."""
    params = pd.read_csv(PARAMETER_SHEET, dtype=str).fillna("")
    states: dict[str, set[str]] = {}
    for gauge, state in zip(params["Gauge"].str.strip(), params["State"].str.strip().str.upper(), strict=True):
        if GAUGE_ID.fullmatch(gauge) and state:
            states.setdefault(gauge, set()).add(state)
    return {gauge: frozenset(found) for gauge, found in states.items()}


@functools.lru_cache(maxsize=1)
def catalog_states() -> dict[str, frozenset[str]]:
    """Gauge -> states in mdba-gauge-getter's bundled catalog, read as its `init()` does (without importing bom_water)."""
    try:
        source = resources.files("mdba_gauge_getter") / "data/bom_gauge_data.csv"
        with resources.as_file(source) as path:
            catalog = pd.read_csv(path, skiprows=1, skipfooter=1, names=["name", "gauge", "owner", "lat", "lon"], engine="python", dtype=str)
    except (ModuleNotFoundError, FileNotFoundError):
        return {}
    states: dict[str, set[str]] = {}
    for gauge, owner in zip(catalog["gauge"].fillna("").str.strip(), catalog["owner"].fillna("").str.strip(), strict=True):
        if gauge and owner:
            states.setdefault(gauge, set()).add(owner.split(" ", 1)[0].upper())
    return {gauge: frozenset(found) for gauge, found in states.items()}


def candidate_states(gauge: str) -> list[str]:
    """State services to ask, in order: the getter catalog's, then the parameter sheet's. Only NSW, VIC and QLD are served.

    The catalog comes first because it names the service that holds the record: 422030 is listed under QLD in the
    parameter sheet, but Queensland answers "Invalid station" and WaterNSW serves it.
    """
    ordered = [*sorted(catalog_states().get(gauge, ())), *sorted(sheet_states().get(gauge, ()))]
    return [state for state in dict.fromkeys(ordered) if state in SUPPORTED_STATES]


def _fetch_source(gauge: str, start: date, end: date, var: str, timeout: float, retries: int) -> pd.DataFrame:
    """Ask each candidate state service in turn; a station unknown to one service moves on to the next."""
    states = candidate_states(gauge)
    if not states:
        listed = sorted(sheet_states().get(gauge, frozenset()) | catalog_states().get(gauge, frozenset()))
        raise StationNotFound(f"Observed source unavailable for {gauge} ({', '.join(listed) or 'unknown'}; BOM and SA are not enabled)")
    if gauge in _ROUTES and _ROUTES[gauge] in states:
        states.remove(_ROUTES[gauge])
        states.insert(0, _ROUTES[gauge])
    deadline = time.monotonic() + timeout
    rejected = []
    for state in states:
        try:
            remaining = max(0.0, deadline - time.monotonic())
            frame = _CLIENT.fetch(state, gauge, start, end, var, timeout=remaining, retries=retries, clamp_to_record=True).frame
        except StationNotFound:
            rejected.append(state)
            continue
        except NoData:
            frame = pd.DataFrame(columns=RAW_COLUMNS)
        _ROUTES[gauge] = state
        return frame
    raise StationNotFound(f"{gauge} is not a station on the {', '.join(rejected)} service{'s' if len(rejected) > 1 else ''}")


def _cache_paths(gauge: str, var: str) -> tuple[Path, Path]:
    key = hashlib.sha256(f"v{CACHE_VERSION}\0{gauge}\0{var}".encode()).hexdigest()
    return CACHE_DIR / f"{key}.csv", CACHE_DIR / f"{key}.json"


def _read_cache(gauge: str, var: str) -> tuple[pd.DataFrame, dict[str, Any]] | None:
    data_path, meta_path = _cache_paths(gauge, var)
    if not data_path.exists() or not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        frame = pd.read_csv(data_path, parse_dates=["DATETIME"])
        return frame, meta
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _write_cache(gauge: str, var: str, frame: pd.DataFrame, start: date, end: date) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data_path, meta_path = _cache_paths(gauge, var)
    tmp_data = data_path.with_suffix(".csv.tmp")
    tmp_meta = meta_path.with_suffix(".json.tmp")
    frame.to_csv(tmp_data, index=False)
    tmp_meta.write_text(json.dumps({"start": start.isoformat(), "end": end.isoformat(), "fetched_at": time.time()}), encoding="utf-8")
    os.replace(tmp_data, data_path)
    os.replace(tmp_meta, meta_path)


def _slice(frame: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    dates = pd.to_datetime(frame["DATETIME"]).dt.date
    return frame.loc[(dates >= start) & (dates <= end)].reset_index(drop=True)


def _fetch_gauge_cached(
    gauge: str,
    start: date,
    end: date,
    var: str = "F",
    *,
    refresh: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    ttl: float = DEFAULT_TTL,
    retries: int = DEFAULT_RETRIES,
) -> GaugeResult:
    """Fetch one gauge (each missing date range within `timeout`) through an extending raw-data cache."""
    if var not in {"F", "L"}:
        return GaugeResult(pd.DataFrame(), "unsupported", f"Unsupported variable {var}")
    if start > end:
        return GaugeResult(pd.DataFrame(), "error", "Start date must not follow end date")
    # The cache holds one contiguous covered interval. It is extended only by a fresh entry that the request overlaps or
    # adjoins; otherwise (refresh, expired, disjoint) the request replaces it, so coverage never claims unfetched days.
    cached = None if refresh else _read_cache(gauge, var)
    cached_frame = pd.DataFrame()
    covered_start, covered_end = start, end
    needed = [(start, end)]
    if cached:
        frame, meta = cached
        lo, hi = date.fromisoformat(meta["start"]), date.fromisoformat(meta["end"])
        fresh = time.time() - float(meta["fetched_at"]) < ttl
        if fresh and start <= hi + timedelta(days=1) and end >= lo - timedelta(days=1):
            cached_frame, covered_start, covered_end = frame, min(start, lo), max(end, hi)
            needed = [(start, lo - timedelta(days=1)), (hi + timedelta(days=1), end)]
            needed = [(left, right) for left, right in needed if left <= right]
            if not needed:
                result = _slice(cached_frame, start, end)
                return GaugeResult(result, "ok" if not result.empty else "empty", "" if not result.empty else "No data in requested period")

    fetched: list[pd.DataFrame] = []
    try:
        for left, right in needed:
            fetched.append(_fetch_source(gauge, left, right, var, timeout, retries))
        combined = (
            pd.concat([cached_frame, *fetched], ignore_index=True)
            if (not cached_frame.empty or any(not f.empty for f in fetched))
            else pd.DataFrame(columns=RAW_COLUMNS)
        )
        if not combined.empty:
            combined["DATETIME"] = pd.to_datetime(combined["DATETIME"])
            combined = combined.drop_duplicates(["DATASOURCEID", "SITEID", "SUBJECTID", "DATETIME"], keep="last").sort_values("DATETIME").reset_index(drop=True)
        _write_cache(gauge, var, combined, covered_start, covered_end)
        result = _slice(combined, start, end)
        if result.empty:
            return GaugeResult(result, "empty", "No data in requested period")
        return GaugeResult(result, "ok")
    except ObservedSourceError as exc:
        return GaugeResult(pd.DataFrame(), exc.status, str(exc))
    except Exception as exc:  # a bug here must not take down the other gauges' fetches
        return GaugeResult(pd.DataFrame(), "error", f"{type(exc).__name__}: {exc}")


def fetch_gauge(
    gauge: str,
    start: date,
    end: date,
    var: str = "F",
    *,
    refresh: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    ttl: float = DEFAULT_TTL,
    retries: int = DEFAULT_RETRIES,
) -> GaugeResult:
    """Deduplicate concurrent fetches for the same gauge/variable cache entry."""
    key = (gauge, var)
    with _CACHE_LOCKS_GUARD:
        lock = _CACHE_LOCKS.setdefault(key, threading.Lock())
    with lock:
        return _fetch_gauge_cached(gauge, start, end, var, refresh=refresh, timeout=timeout, ttl=ttl, retries=retries)


def fetch_many(
    gauges: list[str],
    start: date,
    end: date,
    progress: Callable[[str, int, int], None] | None = None,
    workers: int = 4,
    *,
    refresh: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    status_progress: Callable[[str, str, GaugeResult], None] | None = None,
) -> tuple[dict[tuple[str, str], pd.DataFrame], dict[tuple[str, str], GaugeResult]]:
    """Fetch flow and level independently; report progress once both variables finish per gauge."""
    frames: dict[tuple[str, str], pd.DataFrame] = {}
    statuses: dict[tuple[str, str], GaugeResult] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(gauges) or 1))) as pool:
        pending = {pool.submit(fetch_gauge, gauge, start, end, var, refresh=refresh, timeout=timeout): (gauge, var) for gauge in gauges for var in ("F", "L")}
        completed_gauges: set[str] = set()
        done = 0
        for future in as_completed(pending):
            gauge, var = pending[future]
            try:
                result = future.result()
            except Exception as exc:
                result = GaugeResult(pd.DataFrame(), "error", f"{type(exc).__name__}: {exc}")
            statuses[(gauge, var)] = result
            if status_progress:
                status_progress(gauge, var, result)
            if result.status == "ok":
                frames[(gauge, var)] = result.frame
            if gauge not in completed_gauges and all((gauge, item) in statuses for item in ("F", "L")):
                completed_gauges.add(gauge)
                done += 1
                if progress:
                    progress("Fetching gauges", done, len(gauges))
    return frames, statuses


def to_standard_frame(results: dict[tuple[str, str], pd.DataFrame]) -> pd.DataFrame:
    """Outer-join raw gauge results onto a daily Date index with Standard time-series columns."""
    series: list[pd.Series] = []
    for (gauge, var), frame in results.items():
        if frame.empty:
            continue
        name = f"{gauge}_{'flow' if var == 'F' else 'level'}"
        dates = pd.to_datetime(frame["DATETIME"]).dt.normalize()
        values = pd.to_numeric(frame["VALUE"], errors="coerce").to_numpy()
        series.append(pd.Series(values, index=dates, name=name))
    if not series:
        return pd.DataFrame(columns=["Date"])
    joined = pd.concat(series, axis=1).sort_index()
    joined = joined.reindex(pd.date_range(joined.index.min(), joined.index.max(), freq="D"))
    joined.index.name = "Date"
    joined.insert(0, "Date", joined.index.strftime("%Y-%m-%d"))
    return joined.reset_index(drop=True)
