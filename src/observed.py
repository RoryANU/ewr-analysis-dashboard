"""Optional observed-gauge acquisition through gauge_pull and raw range cache."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import random
import re
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".observed_cache"
CACHE_VERSION = "3"  # gauge_pull output only; no trace-metadata units or alternate routing
RAW_COLUMNS = ["DATASOURCEID", "SITEID", "SUBJECTID", "DATETIME", "VALUE", "QUALITYCODE"]
PARAMETER_SHEET = ROOT / "data/reference/parameter_sheet.csv"
GAUGE_ID = re.compile(r"(?:A)?\d+[A-Z]*", re.I)
DEFAULT_TIMEOUT = 90.0  # per gauge and variable; a full record takes the services 10-60 s to start sending
DEFAULT_TTL = 24 * 60 * 60
DEFAULT_RETRIES = 2
GAUGE_STAGE = "Fetching gauges"
RETRY_AFTER = re.compile(r"retry after (\d+(?:\.\d+)?) seconds", re.I)
BUSY_ERROR = re.compile(r"too many requests in queue|error_num\D*218|\bsource busy\b", re.I)
_CACHE_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_CACHE_LOCKS_GUARD = threading.Lock()


@dataclass
class GaugeResult:
    frame: pd.DataFrame
    status: str
    message: str = ""
    retryable: bool = False


def _http_status(exc: Exception) -> int | None:
    code = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(code, int):
        return code
    match = re.search(r"\bHTTP(?:\s+(?:status|error))?[:\s]+(\d{3})\b", str(exc), re.I)
    return int(match[1]) if match else None


def _error_result(exc: Exception) -> GaugeResult:
    """Keep query failures distinct from empty records and local setup failures."""
    request_errors: tuple[type[Exception], ...] = ()
    invalid_requests: tuple[type[Exception], ...] = ()
    http_errors: tuple[type[Exception], ...] = ()
    try:
        from requests.exceptions import HTTPError, InvalidHeader, InvalidSchema, InvalidURL, MissingSchema, RequestException, URLRequired
    except ImportError:
        pass  # Reporting a missing dependency must not require that dependency.
    else:
        request_errors = (RequestException,)
        invalid_requests = (InvalidHeader, InvalidSchema, InvalidURL, MissingSchema, URLRequired)
        http_errors = (HTTPError,)

    message = f"{type(exc).__name__}: {exc}"
    # Keep station/busy message rules aligned with describeResult in src/dashboard_template.html.
    missing_stations = (re.search(r"stations\.json", message, re.I) and re.search(r"bom_water", message, re.I)) or re.search(
        r"BoM station cache missing", message, re.I
    )
    busy = BUSY_ERROR.search(message)
    network_error = isinstance(exc, (TimeoutError, ConnectionError, socket.gaierror)) or (
        isinstance(exc, OSError)
        and (
            exc.errno
            in {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN, errno.ENETRESET, errno.ECONNRESET, errno.ECONNABORTED, errno.ETIMEDOUT, errno.EPIPE}
            or re.search(r"network is unreachable|no route to host|name resolution|connection (?:reset|aborted|refused)", message, re.I) is not None
        )
    )
    transient = bool(busy) or network_error or isinstance(exc, (*request_errors, json.JSONDecodeError))
    retryable = transient or not isinstance(exc, (OSError, ImportError, ValueError, TypeError))
    code = _http_status(exc)
    if code is not None or isinstance(exc, http_errors):
        retryable = code in {408, 425, 429} or (code is not None and 500 <= code < 600)
    if missing_stations or isinstance(exc, (*invalid_requests, FileNotFoundError, PermissionError, NotADirectoryError, IsADirectoryError)):
        retryable = False
    return GaugeResult(pd.DataFrame(), "error", message, retryable=bool(retryable))


def sdl_gauges(sdl: str) -> list[str]:
    """Return gauges in an SDL, including partner gauges named by Multigauge."""
    params = pd.read_csv(PARAMETER_SHEET, dtype=str).fillna("")
    selected = params[params["SWSDLName"].str.strip() == sdl.strip()]
    gauges = {value for value in selected["Gauge"].str.strip() if GAUGE_ID.fullmatch(value)}
    return sorted(gauges)


def _fetch_source(gauge: str, start: date, end: date, var: str, timeout: float, retries: int) -> pd.DataFrame:
    """Call the getter with advisory timeout and transient retries, leaving all routing to it.

    A timeout stops waiting, but cannot kill the getter's worker thread. Daemon workers
    can finish in the background without writing cache entries or blocking shutdown.
    """
    from mdba_gauge_getter.gauge_getter import gauge_pull
    from requests.exceptions import ConnectionError as RequestsConnectionError
    from requests.exceptions import RequestException
    from requests.exceptions import Timeout as RequestTimeout

    deadline = time.monotonic() + timeout
    for attempt in range(retries + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"Gauge {gauge} exceeded {timeout:g} s advisory timeout")
        future: Future = Future()

        def pull(result=future):
            try:
                raw = gauge_pull([gauge], start, end, var=var, interval="day", data_type="mean", data_source="state")
                result.set_result(raw)
            except Exception as exc:
                result.set_exception(exc)

        threading.Thread(target=pull, daemon=True).start()
        try:
            return _normalise(future.result(timeout=remaining))
        except Exception as exc:
            busy = BUSY_ERROR.search(str(exc)) is not None
            code = _http_status(exc)
            timed_out = isinstance(exc, (TimeoutError, RequestTimeout))
            network = isinstance(exc, (ConnectionError, RequestsConnectionError, socket.gaierror)) or (
                isinstance(exc, OSError) and not isinstance(exc, (RequestException, json.JSONDecodeError))
            )
            transient = _error_result(exc).retryable and (busy or timed_out or network or code is not None)
            delay = (10.0 if attempt == 0 else 30.0) if busy or timed_out or code in {408, 425, 429} else 0.5 * 2**attempt * random.uniform(0.5, 1.5)
            if (asked := RETRY_AFTER.search(str(exc))) and code in {429, 503}:
                delay = max(delay, float(asked[1]))  # honour the server's own back-off; a long one outlasts the deadline and is not retried here
            if not transient or attempt == retries or delay >= deadline - time.monotonic():
                if isinstance(exc, TimeoutError):
                    raise TimeoutError(f"Gauge {gauge} exceeded {timeout:g} s advisory timeout") from exc
                raise
            time.sleep(delay)
    raise ValueError("retries must be nonnegative")


def _normalise(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalise getter columns and discard quality-coded placeholder days."""
    if raw.empty:
        return pd.DataFrame(columns=RAW_COLUMNS)
    frame = raw.loc[:, RAW_COLUMNS].copy()
    frame["VALUE"] = pd.to_numeric(frame["VALUE"], errors="coerce")
    frame["DATETIME"] = pd.to_datetime(frame["DATETIME"])
    quality = pd.to_numeric(frame["QUALITYCODE"], errors="coerce")
    return frame.loc[~(quality.isin({255, 201, 151}) | (quality >= 999))].reset_index(drop=True)


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
                return GaugeResult(result, "ok" if not result.empty else "empty", "" if not result.empty else "No data returned for this gauge and period")

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
            return GaugeResult(result, "empty", "No data returned for this gauge and period")
        return GaugeResult(result, "ok")
    except Exception as exc:  # a bug here must not take down the other gauges' fetches
        return _error_result(exc)


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
    retries: int = DEFAULT_RETRIES,
) -> tuple[dict[tuple[str, str], pd.DataFrame], dict[tuple[str, str], GaugeResult]]:
    """Fetch flow and level independently; report progress once both variables finish per gauge."""
    frames: dict[tuple[str, str], pd.DataFrame] = {}
    statuses: dict[tuple[str, str], GaugeResult] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(gauges) or 1))) as pool:
        pending = {
            pool.submit(fetch_gauge, gauge, start, end, var, refresh=refresh, timeout=timeout, retries=retries): (gauge, var)
            for gauge in gauges
            for var in ("F", "L")
        }
        completed_gauges: set[str] = set()
        done = 0
        for future in as_completed(pending):
            gauge, var = pending[future]
            try:
                result = future.result()
            except Exception as exc:
                result = _error_result(exc)
            statuses[(gauge, var)] = result
            if status_progress:
                status_progress(gauge, var, result)
            if result.status == "ok":
                frames[(gauge, var)] = result.frame
            if gauge not in completed_gauges and all((gauge, item) in statuses for item in ("F", "L")):
                completed_gauges.add(gauge)
                done += 1
                if progress:
                    progress(GAUGE_STAGE, done, len(gauges))
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
