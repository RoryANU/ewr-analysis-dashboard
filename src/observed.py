"""Optional observed-gauge acquisition through gauge_pull and raw range cache."""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import math
import os
import random
import re
import socket
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import isfinite
from numbers import Integral, Real
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".observed_cache"
NORMALISATION = "2"  # cache metadata: entries from an older normalisation (which also dropped 151 and >=999 before grading) are refetched on access
CACHE_VERSION = "3"  # gauge_pull output only; no trace-metadata units or alternate routing
RAW_COLUMNS = ["DATASOURCEID", "SITEID", "SUBJECTID", "DATETIME", "VALUE", "QUALITYCODE"]
PARAMETER_SHEET = ROOT / "data/reference/parameter_sheet.csv"
BOM_WATER_HOME = ROOT / "data/reference"  # bomwater caches under <home>/bom_water/cache; see data/reference/bom_water/README.md
QUALITY_CODES = ROOT / "data/reference/bom_quality_codes.json"  # Bureau agency-code -> grade tables (qc_doc.shtml)
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
    coverage: dict[str, Any] | None = None  # terminal ok/empty/error only; see assess_coverage


@dataclass(frozen=True, eq=False)
class QualityPolicy:
    """Bureau-grade quality policy, built to clean out clear missing/NA data and nothing more.

    Agency numbers mean different things in different agency tables, so each measurement is graded with the table(s) that
    match the most of its readings. That table is INFERRED from the codes (match share is not agency confidence). Readings
    graded outside `allowed_grades` (F: missing / not of release quality) are removed. A code the table does not list, or a
    reading with no usable code, is KEPT: a mismatch is not evidence of bad data. If several tables tie on matches and
    disagree about what to remove, the disputed readings are kept and the selection is reported as ambiguous.
    Both mapping levels are frozen on construction; a published policy is never mutated.
    """

    allowed_grades: frozenset[str]
    tables: Mapping[str, Mapping[int, str]]
    version: str

    def __post_init__(self) -> None:
        """Validate the policy and freeze copies of its grades and agency tables."""
        if not self.allowed_grades or not set(self.allowed_grades) <= set("ABCEFG"):
            raise ValueError("allowed_grades must be a non-empty subset of A, B, C, E, F, G")
        if not self.tables:
            raise ValueError("A policy needs at least one agency table")
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("A non-empty policy version is required")
        frozen: dict[str, Mapping[int, str]] = {}
        for name, table in self.tables.items():
            if not table or any(isinstance(c, bool) or not isinstance(c, int) or g not in set("ABCEFG") for c, g in table.items()):
                raise ValueError(f"Agency table {name!r} must map integer codes to grades A, B, C, E, F or G")
            frozen[name] = MappingProxyType(dict(table))
        object.__setattr__(self, "allowed_grades", frozenset(self.allowed_grades))
        object.__setattr__(self, "tables", MappingProxyType(frozen))

    def select_table(self, codes: list[int]) -> dict[str, Any]:
        """Infer the agency table(s) with the most matching codes. Ties are inspected, never settled by listing order."""
        total = len(codes)
        base: dict[str, Any] = {
            "selection": "no_codes",
            "table": None,
            "candidates": [],
            "match_count": 0,
            "code_count": total,
            "runner_up_match_count": None,
            "margin_count": None,
        }
        if not total:
            return base
        hits = {name: sum(1 for code in codes if code in table) for name, table in self.tables.items()}
        top = max(hits.values())
        if top == 0:
            return {**base, "selection": "no_match"}
        candidates = sorted(name for name, h in hits.items() if h == top)
        lower = [h for h in hits.values() if h < top]
        runner = max(lower) if lower else None
        # A missing mapping counts as "keep", as the data owner specified, when comparing what candidates would remove.
        ambiguous = len(candidates) > 1 and any(len({self.removes(code, [name]) for name in candidates}) > 1 for code in set(codes))
        selection = "unique_best" if len(candidates) == 1 else "ambiguous" if ambiguous else "equivalent_tie"
        return {
            **base,
            "selection": selection,
            "table": candidates[0] if selection == "unique_best" else None,
            "candidates": candidates,
            "match_count": top,
            "runner_up_match_count": runner,
            "margin_count": None if runner is None else top - runner,
        }

    def removes(self, code: int | None, candidates: list[str]) -> bool:
        """True only when EVERY candidate table lists the code and grades it outside the allowed grades."""
        return code is not None and bool(candidates) and all(code in self.tables[n] and self.tables[n][code] not in self.allowed_grades for n in candidates)

    def lists(self, code: int | None, candidates: list[str]) -> bool:
        """Return whether any candidate agency table lists the quality code."""
        return code is not None and any(code in self.tables[n] for n in candidates)


_DEFAULT_POLICY: QualityPolicy | None = None


def default_quality_policy() -> QualityPolicy | None:
    """Grades A-E over the Bureau's agency tables. A failure is logged loudly and NOT cached, so a fixed file is picked up; None means
    "quality policy unavailable: completeness is disabled" (coverage then reads unassessed_quality_policy), never a permissive fallback."""
    global _DEFAULT_POLICY
    if _DEFAULT_POLICY is not None:
        return _DEFAULT_POLICY
    try:
        raw = json.loads(QUALITY_CODES.read_text(encoding="utf-8"))
        tables: dict[str, dict[int, str]] = {}
        for name, table in raw["tables"].items():
            converted = {int(code): grade for code, grade in table.items()}
            if len(converted) != len(table):
                raise ValueError(f"duplicate code after conversion in {name}")
            tables[name] = converted
        grades = frozenset(raw.get("accepted_grades_default", "ABCE"))
        digest = hashlib.sha256(json.dumps([sorted(grades), {n: sorted(t.items()) for n, t in sorted(tables.items())}, "select-v2"]).encode()).hexdigest()[:10]
        _DEFAULT_POLICY = QualityPolicy(grades, tables, f"bom-hrs-grades-{''.join(sorted(grades))}-{raw['retrieved']}-{digest}")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        logging.getLogger(__name__).error("Quality policy unavailable (%s: %s); coverage completeness is disabled", type(exc).__name__, exc)
        return None
    return _DEFAULT_POLICY


def clean_frame(frame: pd.DataFrame, policy: QualityPolicy | None) -> pd.DataFrame:
    """Remove rows that must not be used: no-record sentinels always; then, with a policy, rows its matched table grades outside the
    allowed grades (so the readings used downstream are the readings counted); without one, the older 151 / >=999 rule."""
    if frame.empty or "QUALITYCODE" not in frame.columns:
        return frame
    codes = [_coverage_code(q) for q in frame["QUALITYCODE"].tolist()]
    candidates: list[str] = []
    if policy is not None:
        candidates = policy.select_table([c for c in codes if c is not None and not _is_sentinel(c, policy)])["candidates"]
    drop = [code is not None and (_is_sentinel(code, policy) or (policy is not None and policy.removes(code, candidates))) for code in codes]
    return frame.loc[[not d for d in drop]].reset_index(drop=True) if any(drop) else frame


class _SourceFailure(Exception):
    """Carry source-attempt metadata without changing public fetch signatures."""

    def __init__(self, cause: Exception, *, var: str, attempts: int, last_delay: float | None = None):
        """Retain the original exception and metadata from the last source attempt."""
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
    """Notify the current measurement callback without propagating callback failures."""
    state = _FETCH_STATE.get()
    if state is None or state.callback is None:
        return
    try:
        state.callback(result)
    except Exception as exc:
        # Progress reporting must not turn a successful request into a failure; do not log the callback's own text.
        logging.getLogger(__name__).warning("Gauge progress callback failed (%s)", type(exc).__name__)


def _is_sentinel(code: int, policy: QualityPolicy | None) -> bool:
    """No-record codes. 255 and 201 are grade F in every Bureau table that lists them, so they go whatever the policy says.
    With no policy the older rule also applies (151 and anything >= 999); with a policy those are graded by the matched table
    like any other code (151 is grade E in NSW Water, and 999 is in no table, so it is kept as a mismatch)."""
    return code in {255, 201} or (policy is None and (code == 151 or code >= 999))


def _use_repo_bom_cache() -> None:
    """Point bomwater's cache at data/reference/bom_water/cache instead of ~/bom_water/cache (idempotent, scoped to that module).

    The package builds its cache path from os.path.expanduser("~") and only checks that one file exists before reading another, so a
    failed first download leaves a permanent half-built cache in the home folder. Shipping the files in the repository removes the
    download, the home-folder dependency and that failure mode. Only bom_water.bom_water's own `os` name is replaced, so no other
    code (and no environment variable) is affected.
    """
    import importlib

    module: Any = importlib.import_module("bom_water.bom_water")
    if getattr(module, "_repo_cache_patched", False):
        return
    real_os: Any = module.os

    class _Path:
        def __getattr__(self, name: str) -> Any:
            """Delegate unchanged path operations to the original os.path module."""
            return getattr(real_os.path, name)

        @staticmethod
        def expanduser(path: str) -> str:
            """Redirect only the home-directory lookup to the repository reference data."""
            return str(BOM_WATER_HOME) if path == "~" else str(real_os.path.expanduser(path))

    class _Os:
        path = _Path()

        def __getattr__(self, name: str) -> Any:
            """Delegate unchanged operating-system operations to the original os module."""
            return getattr(real_os, name)

    module.os = _Os()
    module._repo_cache_patched = True


def _http_status(exc: Exception) -> int | None:
    """Read an HTTP status from the response, falling back to exception text."""
    code = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(code, int):
        return code
    match = re.search(r"\bHTTP(?:\s+(?:status|error))?[:\s]+(\d{3})\b", str(exc), re.I)
    return int(match[1]) if match else None


def _redact_detail(text: str) -> str:
    """Remove URL credentials/query strings and common labelled credentials."""

    def redact_url(match: re.Match[str]) -> str:
        """Strip credentials, query parameters and fragments from a matched URL."""
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
    """Return a finite retry delay from HTTP 429 or 503 exception text."""
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
    _use_repo_bom_cache()
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
            """Resolve this attempt's future with the getter result or exception."""
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
            if waiting.error is not None:
                waiting.error["retry_after_s"] = delay
            _emit_inflight(waiting)
            time.sleep(delay)
            last_delay = delay
    raise ValueError("retries must be nonnegative")


def _normalise(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalise getter columns and discard the no-record rows (codes 255 and 201: grade F in every Bureau table that lists them)."""
    if raw.empty:
        return pd.DataFrame(columns=RAW_COLUMNS)
    frame = raw.loc[:, RAW_COLUMNS].copy()
    frame["VALUE"] = pd.to_numeric(frame["VALUE"], errors="coerce")
    frame["DATETIME"] = pd.to_datetime(frame["DATETIME"])
    quality = pd.to_numeric(frame["QUALITYCODE"], errors="coerce")
    return frame.loc[~quality.isin({255, 201})].reset_index(drop=True)


def _cache_paths(gauge: str, var: str) -> tuple[Path, Path]:
    """Return versioned data and metadata paths for one gauge and variable."""
    key = hashlib.sha256(f"v{CACHE_VERSION}\0{gauge}\0{var}".encode()).hexdigest()
    return CACHE_DIR / f"{key}.csv", CACHE_DIR / f"{key}.json"


def _read_cache(gauge: str, var: str) -> tuple[pd.DataFrame, dict[str, Any]] | None:
    """Read and normalise cached readings, returning None for missing or invalid files."""
    data_path, meta_path = _cache_paths(gauge, var)
    if not data_path.exists() or not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        # Re-apply the placeholder filter: entries written before it existed hold "No Record" rows with a VALUE of 0.
        frame = _normalise(pd.read_csv(data_path, parse_dates=["DATETIME"]))
        return frame, meta
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _write_cache(gauge: str, var: str, frame: pd.DataFrame, start: date, end: date) -> None:
    """Replace the cached readings and range metadata using temporary files."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data_path, meta_path = _cache_paths(gauge, var)
    tmp_data = data_path.with_suffix(".csv.tmp")
    tmp_meta = meta_path.with_suffix(".json.tmp")
    frame.to_csv(tmp_data, index=False)
    tmp_meta.write_text(
        json.dumps({"start": start.isoformat(), "end": end.isoformat(), "fetched_at": time.time(), "normalisation": NORMALISATION}), encoding="utf-8"
    )
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
        fresh = time.time() - float(meta["fetched_at"]) < ttl and meta.get("normalisation") == NORMALISATION
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


def _clean_result(result: GaugeResult, policy: QualityPolicy | None) -> GaugeResult:
    """Apply quality filtering in place and mark an exhausted successful result empty."""
    cleaned = clean_frame(result.frame, policy)
    if len(cleaned) != len(result.frame):
        result.frame = cleaned
        if cleaned.empty and result.status == "ok":
            result.status, result.message = "empty", "No usable readings once missing data was removed"
    return result


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
    policy: QualityPolicy | None = None,
    apply_policy: bool = True,
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
            result.attempts = state.max_attempts_used
            if apply_policy:  # the cache keeps everything graded later; cleaning happens here, per request
                result = _clean_result(result, policy)  # worst source range, not the sum of head/tail requests
        return result
    finally:
        if token is not None:
            _FETCH_STATE.reset(token)


# ---- coverage (daily source; calendar dates, no timezone is claimed) -------------------------------------------------
_UNITS = {"F": "ML/d", "L": "m"}


def _coverage_day(value: object) -> date | None:
    """Parse a timezone-free calendar date, returning None for invalid values."""
    if isinstance(value, datetime):
        return None if pd.isna(value) or value.tzinfo is not None else value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _coverage_window(start: date, end: date, reporting_cutoff: date | None) -> tuple[date, date]:
    """Return [first, stop): `end` and `reporting_cutoff` are inclusive, `stop` is exclusive."""
    first, last = _coverage_day(start), _coverage_day(end)
    if first is None or last is None:
        raise ValueError("Coverage boundaries must be timezone-free calendar dates")
    if last < first:
        raise ValueError("Coverage end must not precede start")
    if reporting_cutoff is not None:
        cutoff = _coverage_day(reporting_cutoff)
        if cutoff is None:
            raise ValueError("reporting_cutoff must be a calendar date")
        last = min(last, cutoff)
    return (first, first) if last < first else (first, last + timedelta(days=1))


def _coverage_units(var: str, frame: pd.DataFrame) -> str | None:
    """Units come from the F/L contract; a frame that declares anything else blocks coverage (no conversion)."""
    expected = _UNITS.get(var)
    if expected is None:
        return None
    declared = frame.attrs.get("units", expected)
    return expected if declared == expected else None


def _coverage_value(value: object) -> float | None:
    """Finite real numbers only. Strings and booleans are rejected, never coerced (0 must stay a real observation)."""
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _coverage_code(value: object) -> int | None:
    """Parse integral quality codes while rejecting booleans and non-finite values."""
    if isinstance(value, bool):
        return None
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        number = float(value)
        return int(number) if math.isfinite(number) and number.is_integer() else None
    if isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]+", value.strip()):
        return int(value.strip())
    return None


def _coverage_template(first: date, stop: date, *, units: str | None, status: str, policy: QualityPolicy | None) -> dict[str, Any]:
    """Build an unassessed coverage payload with its period and optional policy metadata."""
    coverage: dict[str, Any] = {
        "units": units,
        "coverage_status": status,
        "coverage_pct": None,
        "coverage_complete": None,
        "expected_count": None,
        "valid_count": None,
        "last_reading": None,
        "gaps": None,
        "gaps_truncated": False,
        "zero_count": None,
        "zero_days_count": None,
        "all_zero": None,
        "excluded_quality_count": None,
        "unknown_quality_count": None,
        "period": {"start": first.isoformat(), "end_exclusive": stop.isoformat(), "timezone": None},
    }
    if policy is not None:
        coverage["policy_version"] = policy.version
        coverage.update(
            quality_table=None,
            quality_table_selection=None,
            quality_table_candidates=[],
            quality_table_match_count=None,
            quality_table_code_count=None,
            quality_table_runner_up_match_count=None,
            quality_table_margin_count=None,
            quality_table_match_pct=None,
        )
    return coverage


def assess_coverage(
    frame: pd.DataFrame,
    start: date,
    end: date,
    *,
    var: str,
    policy: QualityPolicy | None = None,
    filter_period: bool = True,
    reporting_cutoff: date | None = None,
) -> dict[str, Any]:
    """Assess one gauge/variable frame of DAILY readings; pure (never reads the clock).

    Unique calendar-day slots only (the last duplicate wins; an invalid last duplicate does not fall back).
    Without a policy, finite non-placeholder readings qualify provisionally, `coverage_complete` stays null even at 100 %,
    and every retained slot counts as unknown quality. With a policy, readings graded outside its allowed grades (or no-record
    placeholders) are removed, unlisted/missing codes are kept and counted as unknown, and complete is true only when every
    expected day has a qualifying reading. An unfiltered run has no meaningful expected period, so its
    expected_count, percentage and gaps are null. Zero is an observation: it never lowers coverage.
    """
    first, stop = _coverage_window(start, end, reporting_cutoff)
    units = _coverage_units(var, frame)
    if units is None:
        return _coverage_template(first, stop, units=None, status="unassessed_units", policy=policy)
    status = "unassessed_open_period" if not filter_period else "unassessed_quality_policy" if policy is None else "assessed"
    coverage = _coverage_template(first, stop, units=units, status=status, policy=policy)

    slots: dict[date, tuple[object, object]] = {}
    if len(frame.index):
        if not {"DATETIME", "VALUE"} <= set(frame.columns):
            raise ValueError("Coverage frame needs DATETIME and VALUE columns")
        qualities = frame["QUALITYCODE"].tolist() if "QUALITYCODE" in frame.columns else [None] * len(frame.index)
        for stamp, value, quality in zip(frame["DATETIME"].tolist(), frame["VALUE"].tolist(), qualities, strict=True):
            day = _coverage_day(stamp)
            if day is not None and first <= day < stop:
                slots[day] = (value, quality)

    usable: dict[date, tuple[float, int | None]] = {}
    excluded = unknown = 0
    codes = {day: _coverage_code(quality) for day, (_, quality) in slots.items()}
    candidates: list[str] = []
    if policy is not None:
        sel = policy.select_table([code for code in codes.values() if code is not None and not _is_sentinel(code, policy)])
        candidates = sel["candidates"]
        coverage.update(
            quality_table=sel["table"],
            quality_table_selection=sel["selection"],
            quality_table_candidates=candidates,
            quality_table_match_count=sel["match_count"],
            quality_table_code_count=sel["code_count"],
            quality_table_runner_up_match_count=sel["runner_up_match_count"],
            quality_table_margin_count=sel["margin_count"],
            quality_table_match_pct=None if not sel["code_count"] else round(100.0 * sel["match_count"] / sel["code_count"], 1),
        )
    for day, (raw_value, _) in slots.items():
        code = codes[day]
        if code is not None and _is_sentinel(code, policy):
            excluded += 1  # no-record sentinels (255/201 are grade F in every table that lists them), dropped upstream too
            continue
        kept_unmatched = False
        if policy is None:
            unknown += 1
        elif policy.removes(code, candidates):
            excluded += 1
            continue
        else:
            kept_unmatched = not policy.lists(code, candidates)  # no usable code, or no candidate table lists it: kept
        value = _coverage_value(raw_value)
        if kept_unmatched and value is not None:
            unknown += 1  # counts only readings that were actually retained without a matching code
        if value is not None:
            usable[day] = (value, code)

    zeros = sum(1 for value, _ in usable.values() if value == 0.0)
    last = max(usable) if usable else None
    coverage.update(
        valid_count=len(usable),
        last_reading=None if last is None else {"timestamp": last.isoformat(), "value": usable[last][0], "quality_code": usable[last][1]},
        zero_count=zeros,
        zero_days_count=zeros,  # daily source with unique slots: the two counts are equal by construction
        all_zero=(zeros == len(usable)) if usable else None,
        excluded_quality_count=excluded,
        unknown_quality_count=unknown,
    )
    if not filter_period:
        return coverage

    expected = (stop - first).days
    gaps: list[dict[str, Any]] = []  # every gap; never silently capped
    run_start: date | None = None
    for offset in range(expected + 1):
        day = first + timedelta(days=offset)
        missing = offset < expected and day not in usable
        if missing and run_start is None:
            run_start = day
        elif not missing and run_start is not None:
            gaps.append({"start": run_start.isoformat(), "end_exclusive": day.isoformat(), "missing_count": (day - run_start).days})
            run_start = None
    coverage.update(expected_count=expected, gaps=gaps)
    if expected > 0:
        coverage["coverage_pct"] = 100.0 * len(usable) / expected
        if policy is not None and coverage.get("quality_table_selection") != "ambiguous":  # an unsettled table cannot certify completeness
            coverage["coverage_complete"] = len(usable) == expected
    return coverage


def _attach_coverage(
    result: GaugeResult,
    start: date,
    end: date,
    *,
    var: str,
    policy: QualityPolicy | None = None,
    filter_period: bool = True,
    reporting_cutoff: date | None = None,
) -> GaugeResult:
    """Terminal results only; in-flight (running/retry_wait) notifications carry no coverage."""
    if result.status in {"ok", "empty"}:
        result.coverage = assess_coverage(result.frame, start, end, var=var, policy=policy, filter_period=filter_period, reporting_cutoff=reporting_cutoff)
    elif result.status not in {"running", "retry_wait", "pending"}:
        first, stop = _coverage_window(start, end, reporting_cutoff)
        result.coverage = _coverage_template(first, stop, units=_coverage_units(var, result.frame), status="unavailable_error", policy=policy)
    return result


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
    policy: QualityPolicy | None = None,
    filter_period: bool = True,
    reporting_cutoff: date | None = None,
) -> tuple[dict[tuple[str, str], pd.DataFrame], dict[tuple[str, str], GaugeResult]]:
    """Fetch flow and level independently; report progress once both variables finish per gauge."""
    _coverage_window(start, end, reporting_cutoff)  # reject bad boundaries before any network work
    frames: dict[tuple[str, str], pd.DataFrame] = {}
    statuses: dict[tuple[str, str], GaugeResult] = {}
    callback_lock = threading.Lock()

    def notify(callback, gauge: str, var: str, result: GaugeResult) -> None:
        """Serialize calls to a supplied measurement progress callback."""
        if callback is not None:
            with callback_lock:
                callback(gauge, var, result)

    def fetch_one(gauge: str, var: str) -> GaugeResult:
        """Fetch one measurement with an isolated context for attempts and notifications."""
        callback = (lambda result: notify(inflight_progress, gauge, var, result)) if inflight_progress is not None else None
        token = _FETCH_STATE.set(_MeasurementState(var, callback))
        try:
            return fetch_gauge(gauge, start, end, var, refresh=refresh, timeout=timeout, retries=retries, apply_policy=False)
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
            # Attach once, before the callback, so streamed and final payloads carry the same assessment.
            _attach_coverage(result, start, end, var=var, policy=policy, filter_period=filter_period, reporting_cutoff=reporting_cutoff)
            if result.status in {"ok", "empty"}:
                result = _clean_result(result, policy)  # after coverage was recorded from the raw frame
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
