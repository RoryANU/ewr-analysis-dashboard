"""Optional observed-gauge acquisition through gauge_pull and raw range cache."""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import random
import re
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, timedelta
from math import isfinite
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

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
MAX_ATTEMPTS = DEFAULT_RETRIES + 1  # initial request + automatic retries
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
    error: dict | None = None  # additive envelope (code, retryable, retry_after_s, ...); None unless status is error/retry_wait
    attempts: int = 1


class _SourceFailure(Exception):
    """Carry source-attempt metadata without changing public fetch signatures."""

    def __init__(self, cause: Exception, *, var: str, attempts: int, last_delay: float | None = None):
        super().__init__(str(cause))
        self.cause = cause
        self.var = var
        self.attempts = attempts
        self.last_delay = last_delay


@dataclass
class _MeasurementState:
    var: str
    callback: Callable[[GaugeResult], None] | None = None
    max_attempts_used: int = 1


_FETCH_STATE: ContextVar[_MeasurementState | None] = ContextVar("observed_measurement_state", default=None)


def _emit_inflight(result: GaugeResult) -> None:
    state = _FETCH_STATE.get()
    if state is None or state.callback is None:
        return
    try:
        state.callback(result)
    except Exception as exc:
        # Progress reporting must not turn a successful request into a failure; do not log the callback's own text.
        logging.getLogger(__name__).warning("Gauge progress callback failed (%s)", type(exc).__name__)


def _http_status(exc: Exception) -> int | None:
    code = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(code, int):
        return code
    match = re.search(r"\bHTTP(?:\s+(?:status|error))?[:\s]+(\d{3})\b", str(exc), re.I)
    return int(match[1]) if match else None


def _redact_detail(text: str) -> str:
    """Remove URL credentials/query strings and common labelled credentials."""

    def redact_url(match: re.Match[str]) -> str:
        try:
            parts = urlsplit(match[0])
            return str(urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", "")))
        except ValueError:
            return "[REDACTED URL]"

    text = re.sub(r"""(?i)\b[a-z][a-z0-9+.-]*://[^\s<>'"]+""", redact_url, text)
    text = re.sub(
        r"""(?ix)
        (["']?\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|
        id[_-]?token|token|password|passwd|secret|client[_-]?secret|
        subscription[_-]?key)\b["']?\s*[:=]\s*)
        (?:"[^"]*"|'[^']*'|[^\s,;}\]]+)
        """,
        lambda match: match[1] + "[REDACTED]",
        text,
    )
    text = re.sub(r"""(?im)(["']?\b(?:proxy-)?authorization\b["']?\s*[:=]\s*)[^\r\n]+""", lambda match: match[1] + "[REDACTED]", text)
    return re.sub(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/-]+=*", r"\1 [REDACTED]", text)


def _parsed_retry_after(exc: Exception) -> float | None:
    # Same status restriction as _fetch_source.
    if _http_status(exc) not in {429, 503}:
        return None
    asked = RETRY_AFTER.search(str(exc))
    if asked is None:
        return None
    seconds = float(asked[1])
    return seconds if isfinite(seconds) else None


def _error_result(exc: Exception, *, var: str | None = None, attempts: int = 1) -> GaugeResult:
    """Classify raw exceptions here; clients consume `error.code`, never exception text."""
    last_delay = None
    if isinstance(exc, _SourceFailure):
        attempts = exc.attempts
        last_delay = exc.last_delay
        if var is None:
            var = exc.var
        exc = exc.cause
    if var is None:
        state = _FETCH_STATE.get()
        var = state.var if state is not None else "F"

    request_errors: tuple[type[Exception], ...] = ()
    invalid_requests: tuple[type[Exception], ...] = ()
    http_errors: tuple[type[Exception], ...] = ()
    request_network_errors: tuple[type[Exception], ...] = ()
    try:
        from requests.exceptions import ConnectionError as RequestsConnectionError
        from requests.exceptions import HTTPError, InvalidHeader, InvalidSchema, InvalidURL, MissingSchema, RequestException, URLRequired
        from requests.exceptions import Timeout as RequestTimeout
    except ImportError:
        pass  # Reporting a missing dependency must not require that dependency.
    else:
        request_errors = (RequestException,)
        invalid_requests = (InvalidHeader, InvalidSchema, InvalidURL, MissingSchema, URLRequired)
        http_errors = (HTTPError,)
        request_network_errors = (RequestsConnectionError, RequestTimeout)

    message = f"{type(exc).__name__}: {exc}"
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
    # Retryability is unchanged from the pre-envelope rules.
    transient = bool(busy) or network_error or isinstance(exc, (*request_errors, json.JSONDecodeError))
    retryable = transient or not isinstance(exc, (OSError, ImportError, ValueError, TypeError))
    http_code = _http_status(exc)
    if http_code is not None or isinstance(exc, http_errors):
        retryable = http_code in {408, 425, 429} or (http_code is not None and 500 <= http_code < 600)
    if missing_stations or isinstance(exc, (*invalid_requests, FileNotFoundError, PermissionError, NotADirectoryError, IsADirectoryError)):
        retryable = False
    retryable = bool(retryable)

    local_configuration = isinstance(exc, ImportError) or (isinstance(exc, OSError) and not isinstance(exc, request_errors))
    if missing_stations:
        error_code = "STATION_LIST_SETUP_REQUIRED"
    elif not retryable and (http_code in {401, 403} or local_configuration):
        error_code = "CONFIGURATION_REQUIRED"
    elif not retryable and (isinstance(exc, (*invalid_requests, ValueError, TypeError)) or (http_code is not None and 400 <= http_code < 500)):
        error_code = "INVALID_REQUEST"
    elif busy or http_code in {425, 429, 503}:
        error_code = "SERVICE_BUSY"
    elif network_error or isinstance(exc, request_network_errors) or http_code in {408, 504}:
        error_code = "NETWORK_TIMEOUT"
    else:
        error_code = "UNEXPECTED_ERROR"

    retry_after_s = None
    if error_code == "SERVICE_BUSY":
        retry_after_s = _parsed_retry_after(exc)
        if retry_after_s is None:
            retry_after_s = last_delay if last_delay is not None else 60.0

    detail = _redact_detail(message)
    attempts = max(1, attempts)
    error = {
        "code": error_code,
        "retryable": retryable,
        "retry_after_s": retry_after_s,
        "measurement": "level" if var in {"L", "level"} else "flow",
        "attempts": attempts,
        "max_attempts": MAX_ATTEMPTS,
        "diagnostic_id": uuid4().hex,
        "detail": detail,
    }
    # The legacy message is redacted too, otherwise it would bypass detail redaction.
    return GaugeResult(pd.DataFrame(), "error", detail, retryable=retryable, error=error, attempts=attempts)


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

    if retries < 0:
        raise ValueError("retries must be nonnegative")
    retry_limit = min(retries, DEFAULT_RETRIES)  # the contract promises at most MAX_ATTEMPTS attempts
    deadline = time.monotonic() + timeout
    attempts_used = 0
    last_delay = None
    for attempt in range(retry_limit + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            expired = TimeoutError(f"Gauge {gauge} exceeded {timeout:g} s advisory timeout")
            raise _SourceFailure(expired, var=var, attempts=max(1, attempts_used), last_delay=last_delay) from expired
        attempts_used = attempt + 1
        state = _FETCH_STATE.get()
        if state is not None:
            state.max_attempts_used = max(state.max_attempts_used, attempts_used)
        _emit_inflight(GaugeResult(pd.DataFrame(), "running", attempts=attempts_used))
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
            transient = _error_result(exc, var=var).retryable and (busy or timed_out or network or code is not None)
            delay = (10.0 if attempt == 0 else 30.0) if busy or timed_out or code in {408, 425, 429} else 0.5 * 2**attempt * random.uniform(0.5, 1.5)
            if (asked := RETRY_AFTER.search(str(exc))) and code in {429, 503}:
                delay = max(delay, float(asked[1]))  # honour the server's own back-off; a long one outlasts the deadline and is not retried here
            if not transient or attempt == retry_limit or delay >= deadline - time.monotonic():
                if isinstance(exc, TimeoutError):
                    final_exc: Exception = TimeoutError(f"Gauge {gauge} exceeded {timeout:g} s advisory timeout")
                else:
                    final_exc = exc
                raise _SourceFailure(final_exc, var=var, attempts=attempts_used, last_delay=last_delay) from exc
            waiting = _error_result(_SourceFailure(exc, var=var, attempts=attempts_used, last_delay=delay), var=var)
            waiting.status = "retry_wait"
            _emit_inflight(waiting)
            time.sleep(delay)
            last_delay = delay
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
        result = _error_result(ValueError(f"Unsupported variable {var}"), var=var)
        result.status = "unsupported"
        result.error = None  # an unavailable variable is not a failure: no envelope
        return result
    if start > end:
        result = _error_result(ValueError("Start date must not follow end date"), var=var)
        result.message = "Start date must not follow end date"  # legacy text stays exact
        return result
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
        return _error_result(exc, var=var)


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
    state = _FETCH_STATE.get()
    token = None
    if state is None:
        state = _MeasurementState(var)
        token = _FETCH_STATE.set(state)
    try:
        key = (gauge, var)
        with _CACHE_LOCKS_GUARD:
            lock = _CACHE_LOCKS.setdefault(key, threading.Lock())
        with lock:
            result = _fetch_gauge_cached(gauge, start, end, var, refresh=refresh, timeout=timeout, ttl=ttl, retries=retries)
        if result.status in {"ok", "empty"}:
            result.attempts = state.max_attempts_used  # worst source range, not the sum of head/tail requests
        return result
    finally:
        if token is not None:
            _FETCH_STATE.reset(token)


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
    inflight_progress: Callable[[str, str, GaugeResult], None] | None = None,
) -> tuple[dict[tuple[str, str], pd.DataFrame], dict[tuple[str, str], GaugeResult]]:
    """Fetch flow and level independently; report progress once both variables finish per gauge."""
    frames: dict[tuple[str, str], pd.DataFrame] = {}
    statuses: dict[tuple[str, str], GaugeResult] = {}
    callback_lock = threading.Lock()

    def notify(callback, gauge: str, var: str, result: GaugeResult) -> None:
        if callback is not None:
            with callback_lock:
                callback(gauge, var, result)

    def fetch_one(gauge: str, var: str) -> GaugeResult:
        callback = (lambda result: notify(inflight_progress, gauge, var, result)) if inflight_progress is not None else None
        token = _FETCH_STATE.set(_MeasurementState(var, callback))
        try:
            return fetch_gauge(gauge, start, end, var, refresh=refresh, timeout=timeout, retries=retries)
        finally:
            _FETCH_STATE.reset(token)

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(gauges) or 1))) as pool:
        pending = {pool.submit(fetch_one, gauge, var): (gauge, var) for gauge in gauges for var in ("F", "L")}
        completed_gauges: set[str] = set()
        done = 0
        for future in as_completed(pending):
            gauge, var = pending[future]
            try:
                result = future.result()
            except Exception as exc:
                result = _error_result(exc, var=var)
            statuses[(gauge, var)] = result
            notify(status_progress, gauge, var, result)
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
