"""Hydstra `get_ts_traces` client for the NSW, VIC and QLD state water services.

The endpoints, data sources, variable codes and output columns follow `mdba-gauge-getter` (`call_state_api` and
`extract_data`); the request handling and parsing replace the getter's because it sends `requests.get` with no timeout,
session or retry, treats any reply without `return.traces` as "no data" and silently falls back to BOM, and keeps the
services' placeholder values for days without data (quality 255 "Data Unavailable", sent as `"0.000"`).

Every service answers with HTTP 200 and JSON, including errors: `error_num` is set at the top level (126 = no data in
the period) or per trace (20 = invalid station/datasource). Captured replies are in tests/fixtures/observed/.
"""

from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests

log = logging.getLogger(__name__)

# mdba-gauge-getter 0.5.1: STATE_URLS, the data source each state is queried with in gauge_pull, and the QLD .pl script.
ENDPOINTS = {
    "NSW": "https://realtimedata.waternsw.com.au/cgi/webservice.exe",
    "VIC": "https://data.water.vic.gov.au/cgi/webservice.exe",
    "QLD": "https://water-monitoring.information.qld.gov.au/cgi/webservice.pl",
}
DATASOURCES = {"NSW": "CP", "VIC": "PUBLISH", "QLD": "AT"}
# (varfrom, varto) per variable; NSW level is sent unpadded as the getter does ("nsw L is strange about 100.00").
VARIABLES = {"F": ("100.00", "141.00"), "L": ("100.00", "100.00")}
NSW_LEVEL = ("100", "100")
EXPECTED_UNITS = {"F": "ML/d", "L": "m"}
RAW_COLUMNS = ["DATASOURCEID", "SITEID", "SUBJECTID", "DATETIME", "VALUE", "QUALITYCODE", "UNITS"]

NO_DATA = 126  # top-level or per-trace: the station exists but has no data in the requested period
INVALID_STATION = 20  # per trace: the station/datasource pair is unknown to this service
GETTER_QUALITY_CUTOFF = 999  # extract_data drops q >= 999
UNAVAILABLE_QUALITY = 255  # Hydstra's "no data" code on every service; its description is blank on VIC and QLD
UNAVAILABLE_DESCRIPTION = re.compile(r"\b(?:data\s+)?unavailable\b|\bdata\s+lost\b|\bno\s+data\b|\bmissing\b", re.I)
QUEUE_FULL = re.compile(r"too many requests in queue", re.I)

USER_AGENT = "ewr-analysis-dashboard (observed gauge data)"
CONNECT_TIMEOUT = 10.0  # reads get the whole remaining deadline: a long trace takes the services up to a minute to start
MAX_BODY_BYTES = 64 * 1024 * 1024  # a full daily record since 1900 is about 2 MB
RETRY_STATUSES = {500, 502, 503, 504}
BACKOFF_BASE, BACKOFF_CAP = 0.5, 4.0


class ObservedSourceError(Exception):
    """A state-service failure; `status` is the GaugeResult status it is reported as."""

    status = "error"
    retryable = False


class DeadlineExceeded(ObservedSourceError, TimeoutError):
    status = "timeout"


class RateLimited(ObservedSourceError):
    """HTTP 429, or the MDBA queue-full reply (HTTP 408 or Hydstra error text). Not retried: retrying feeds the queue."""

    status = "rate_limited"


class StationNotFound(ObservedSourceError):
    status = "unsupported"


class TransientError(ObservedSourceError):
    """Connection drops, gateway errors, empty or truncated bodies: worth another attempt within the deadline."""

    retryable = True


class UnexpectedResponse(ObservedSourceError):
    """A reply that is not the documented JSON (an HTML page, other units, a compressed trace, a missing trace)."""


class ServiceError(ObservedSourceError):
    """A non-zero Hydstra error_num, or an HTTP status that another attempt will not fix."""


class NoData(ObservedSourceError):
    """Not a failure: the station exists but returned no data for the period (reported as "empty")."""

    status = "empty"


@dataclass(frozen=True)
class Trace:
    frame: pd.DataFrame  # RAW_COLUMNS, one row per day with data, sorted by DATETIME
    units: str
    dropped_unavailable: int  # placeholder points for days without data (quality 255 and its equivalents)
    dropped_invalid: int  # points with a blank/non-numeric value or a malformed timestamp


def request_url(state: str, sites: list[str], start: date, end: date, var: str, endpoint: str | None = None) -> str:
    """The getter's JSON-in-query `get_ts_traces` request, percent-encoded (the getter only replaces spaces)."""
    if var not in VARIABLES:
        raise ValueError(f"Unsupported variable {var!r}; expected one of {sorted(VARIABLES)}")
    if start > end:
        raise ValueError("start must not follow end")
    var_from, var_to = NSW_LEVEL if (state, var) == ("NSW", "L") else VARIABLES[var]
    payload = {
        "params": {
            "site_list": ",".join(sites),
            "start_time": start.strftime("%Y%m%d") + "000000",
            "varfrom": var_from,
            "interval": "day",
            "varto": var_to,
            "datasource": DATASOURCES[state],
            "end_time": end.strftime("%Y%m%d") + "000000",
            "data_type": "mean",
            "multiplier": "1",
        },
        "function": "get_ts_traces",
        "version": "2",
    }
    return f"{endpoint or ENDPOINTS[state]}?{quote(json.dumps(payload, separators=(',', ':')), safe='')}"


def normalise_units(units: str) -> str:
    text = re.sub(r"[\s.]+", "", units).lower().replace("\\/", "/")
    if text in {"ml/d", "ml/day", "megalitres/day", "megaliters/day", "megalitresperday"}:
        return "ML/d"
    if text in {"m", "metres", "meters", "metre", "meter"}:
        return "m"
    return units.strip()


def _html_title(text: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    heading = re.search(r"<h[12][^>]*>(.*?)</h[12]>", text, re.I | re.S)
    found = match or heading
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", found.group(1))).strip() if found else "untitled page"


def decode_body(state: str, body: bytes, content_type: str = "") -> dict[str, Any]:
    """Bytes -> the reply object, rejecting the non-JSON shapes a gateway or maintenance page produces."""
    text = body.decode("utf-8", errors="replace").lstrip("﻿").strip()
    if not text:
        raise TransientError(f"{state} service returned an empty body")
    if "html" in content_type.lower() or text[:1] == "<":
        raise UnexpectedResponse(f"{state} service returned an HTML page instead of data: {_html_title(text)!r}")
    if text[:1] not in "{[":  # e.g. QLD's IIS "The page cannot be displayed because the client took too long ..."
        raise TransientError(f"{state} service returned text instead of JSON: {' '.join(text[:200].split())!r}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TransientError(f"{state} service returned malformed JSON ({exc.msg} at byte {exc.pos} of {len(body)})") from exc
    if not isinstance(data, dict):
        raise UnexpectedResponse(f"{state} service returned JSON {type(data).__name__}, expected an object")
    return data


def _error(state: str, number: int, message: str, site: str | None = None) -> ObservedSourceError:
    where = f"{state} service" + (f" for {site}" if site else "")
    text = f"{where}: error {number}: {message or 'no message'}"
    if number == NO_DATA:
        return NoData(f"{where}: {message or 'no data within specified period'}")
    if number == INVALID_STATION:
        return StationNotFound(text)
    if QUEUE_FULL.search(message):
        return RateLimited(text)
    return ServiceError(text)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_traces(state: str, data: Mapping[str, Any], site: str, var: str) -> Trace:
    """One site's trace from a decoded `get_ts_traces` reply, as the getter's columns plus UNITS.

    Days without data are dropped rather than kept as the services' zero placeholders: quality 255, any code the
    reply's own `quality_codes` describes as unavailable/lost/missing, and the getter's q >= 999 cutoff.
    """
    trace = _site_entry(state, data, "traces", site)
    if (number := _int(trace.get("error_num"))) != 0:
        raise _error(state, number, str(trace.get("error_msg", "")), site)
    if str(trace.get("compressed", "0")).strip() not in {"0", ""}:
        raise UnexpectedResponse(f"{state} service sent a compressed trace for {site}; only uncompressed daily traces are parsed")

    varto = trace.get("varto_details") or {}
    units = normalise_units(str(varto.get("units", "")))
    if units != EXPECTED_UNITS[var]:
        raise UnexpectedResponse(f"{state} service sent {site} in {varto.get('units')!r} (variable {varto.get('variable')}); expected {EXPECTED_UNITS[var]}")

    descriptions = trace.get("quality_codes") or {}
    unavailable = {UNAVAILABLE_QUALITY} | {_int(code, -1) for code, text in descriptions.items() if UNAVAILABLE_DESCRIPTION.search(str(text))} - {-1}
    rows: list[tuple[date, float, int]] = []
    dropped_unavailable = dropped_invalid = 0
    for point in trace.get("trace") or []:
        quality = _int(point.get("q"), -1) if isinstance(point, Mapping) else -1
        if quality in unavailable or quality >= GETTER_QUALITY_CUTOFF:
            dropped_unavailable += 1
            continue
        try:
            day = datetime.strptime(str(point["t"]), "%Y%m%d%H%M%S").date()
            value = float(str(point["v"]).strip())
        except (KeyError, TypeError, ValueError):
            dropped_invalid += 1
            continue
        if value != value or quality < 0:  # NaN, or a point with no usable quality code
            dropped_invalid += 1
            continue
        rows.append((day, value, quality))
    if dropped_invalid:
        log.warning("%s %s %s: dropped %d malformed points", state, site, var, dropped_invalid)

    frame = pd.DataFrame(rows, columns=["DATETIME", "VALUE", "QUALITYCODE"])
    frame["DATETIME"] = pd.to_datetime(frame["DATETIME"])
    frame = frame.drop_duplicates("DATETIME", keep="last").sort_values("DATETIME").reset_index(drop=True)
    frame.insert(0, "DATASOURCEID", state)
    frame.insert(1, "SITEID", str(trace.get("site", site)).strip())
    frame.insert(2, "SUBJECTID", "WATER")
    frame["QUALITYCODE"] = frame["QUALITYCODE"].astype("int64")
    frame["UNITS"] = units
    return Trace(frame[RAW_COLUMNS], units, dropped_unavailable, dropped_invalid)


def _site_entry(state: str, data: Mapping[str, Any], key: str, site: str) -> Mapping[str, Any]:
    if (number := _int(data.get("error_num"))) != 0:
        raise _error(state, number, str(data.get("error_msg", "")))
    body = data.get("return", data.get("_return"))  # the getter maps the older `_return` key
    entries = body.get(key) if isinstance(body, Mapping) else None
    if not isinstance(entries, list):
        raise UnexpectedResponse(f"{state} service reply has no return.{key} list (keys: {sorted(data)})")
    entry = next((e for e in entries if isinstance(e, Mapping) and str(e.get("site", "")).strip().upper() == site.strip().upper()), None)
    if entry is None:
        returned = [str(e.get("site")) for e in entries if isinstance(e, Mapping)]
        raise UnexpectedResponse(f"{state} service reply has no {key[:-1]} for {site} (returned: {returned or 'none'})")
    return entry


def parse_record_start(state: str, data: Mapping[str, Any], site: str, var: str) -> date | None:
    """The first day of a station's record from a `get_variable_list` reply; flow is rated from level (100), so both count."""
    entry = _site_entry(state, data, "sites", site)
    variables = [v for v in entry.get("variables") or [] if isinstance(v, Mapping)]
    if not variables:
        raise StationNotFound(f"{state} service has no variables for {site}")
    wanted = {100.0, 141.0} if var == "F" else {100.0}
    starts = []
    for variable in variables:
        try:
            if float(variable["variable"]) in wanted:
                starts.append(datetime.strptime(str(variable["period_start"])[:8], "%Y%m%d").date())
        except (KeyError, TypeError, ValueError):
            continue
    return min(starts, default=None)


class StateClient:
    """Sends `get_ts_traces` requests with one keep-alive session per thread, a whole-call deadline and backoff."""

    def __init__(self, endpoints: Mapping[str, str] | None = None, *, sleep: Any = time.sleep) -> None:
        self.endpoints = dict(endpoints or ENDPOINTS)
        self._local = threading.local()
        self._sleep = sleep
        self._record_starts: dict[tuple[str, str, str], date | None] = {}

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
            self._local.session = session
        return session

    def _get(self, state: str, url: str, deadline: float) -> dict[str, Any]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DeadlineExceeded(f"{state} request exceeded its deadline before it was sent")
        try:
            response = self._session().get(url, timeout=(min(CONNECT_TIMEOUT, remaining), remaining), stream=True)
        except requests.Timeout as exc:
            raise TransientError(f"{state} service did not respond in time ({type(exc).__name__})") from exc
        except requests.ConnectionError as exc:
            raise TransientError(f"{state} service connection failed: {exc}") from exc
        with response:
            chunks, size = [], 0
            try:
                for chunk in response.iter_content(64 * 1024):
                    size += len(chunk)
                    if size > MAX_BODY_BYTES:
                        raise UnexpectedResponse(f"{state} service reply exceeded {MAX_BODY_BYTES // (1024 * 1024)} MB")
                    if time.monotonic() > deadline:
                        raise DeadlineExceeded(f"{state} service was still sending after the deadline ({size} bytes read)")
                    chunks.append(chunk)
            except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as exc:
                raise TransientError(f"{state} service dropped the connection mid-reply ({type(exc).__name__})") from exc
            body = b"".join(chunks)
            content_type = response.headers.get("Content-Type", "")
            status = response.status_code
            retry_after = response.headers.get("Retry-After")
        snippet = re.sub(r"\s+", " ", body[:300].decode("utf-8", errors="replace")).strip()
        if status == 429 or (status == 408 and QUEUE_FULL.search(snippet)):
            wait = f", retry after {retry_after}s" if retry_after else ""
            raise RateLimited(f"{state} service rate-limited the request (HTTP {status}{wait}): {snippet}")
        if status in RETRY_STATUSES:
            detail = _html_title(body.decode("utf-8", errors="replace")) if body.lstrip()[:1] == b"<" else snippet
            raise TransientError(f"{state} service returned HTTP {status}: {detail}")
        if status != 200:
            raise ServiceError(f"{state} service returned HTTP {status}: {snippet}")
        return decode_body(state, body, content_type)

    def _call(self, state: str, site: str, var: str, url: str, deadline: float, retries: int, parse: Any) -> Any:
        """GET and parse with exponential backoff (full jitter) for retryable failures, all within `deadline`."""
        for attempt in range(retries + 1):
            try:
                return parse(state, self._get(state, url, deadline), site, var)
            except ObservedSourceError as exc:
                delay = min(BACKOFF_CAP, BACKOFF_BASE * 2**attempt) * random.uniform(0.5, 1.0)
                if not exc.retryable or attempt >= retries:
                    raise
                if time.monotonic() + delay >= deadline:
                    raise DeadlineExceeded(f"{state} {site}: no time left to retry after: {exc}") from exc
                log.warning("%s %s %s: attempt %d of %d failed (%s); retrying in %.1fs", state, site, var, attempt + 1, retries + 1, exc, delay)
                self._sleep(delay)
        raise AssertionError("unreachable")

    def record_start(self, state: str, site: str, var: str, *, timeout: float = 45.0, retries: int = 2) -> date | None:
        """First day of the station's record (memoised); None when the service does not say. StationNotFound if unknown."""
        key = (state, site.upper(), var)
        if key not in self._record_starts:
            payload = {"function": "get_variable_list", "version": "1", "params": {"site_list": site, "datasource": DATASOURCES[state]}}
            url = f"{self.endpoints[state]}?{quote(json.dumps(payload, separators=(',', ':')), safe='')}"
            try:
                self._record_starts[key] = self._call(state, site, var, url, time.monotonic() + timeout, retries, parse_record_start)
            except (ServiceError, UnexpectedResponse) as exc:
                log.warning("%s %s: record period unavailable (%s); requesting the full range", state, site, exc)
                return None
        return self._record_starts[key]

    def fetch(
        self, state: str, site: str, start: date, end: date, var: str, *, timeout: float = 45.0, retries: int = 2, clamp_to_record: bool = False
    ) -> Trace:
        """One site's daily mean trace; raises NoData, StationNotFound, RateLimited, DeadlineExceeded or another ObservedSourceError.

        With `clamp_to_record` the request starts at the station's first recorded day: asking from 1800 makes the
        service generate (and send) a placeholder point for every earlier day, which takes up to a minute.
        """
        if state not in self.endpoints:
            raise StationNotFound(f"No state service for {state}")
        deadline = time.monotonic() + timeout
        if clamp_to_record and (first := self.record_start(state, site, var, timeout=timeout, retries=retries)) and first > start:
            if first > end:
                raise NoData(f"{state} service for {site}: record starts {first.isoformat()}, after the requested period")
            start = first
        url = request_url(state, [site], start, end, var, self.endpoints[state])
        return self._call(state, site, var, url, deadline, retries, parse_traces)
