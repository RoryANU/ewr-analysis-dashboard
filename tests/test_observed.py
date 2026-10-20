"""Observed adapter and cache tests; only gauge_pull is stubbed at the data boundary."""

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from requests.exceptions import ConnectionError, HTTPError

import observed


def raw_frame(gauge: str, dates, values=None) -> pd.DataFrame:
    values = values if values is not None else ["1.25"] * len(dates)
    return pd.DataFrame(
        {
            "DATASOURCEID": ["NSW"] * len(dates),
            "SITEID": [gauge] * len(dates),
            "SUBJECTID": ["WATER"] * len(dates),
            "DATETIME": pd.to_datetime(dates).date,
            "VALUE": values,
            "QUALITYCODE": [140] * len(dates),
        }
    )


@pytest.fixture
def getter_stub(monkeypatch, tmp_path):
    """All fetch tests go through the real adapter to the package's public function."""
    calls = []
    answer = {"fetch": lambda gauge, start, end, var: raw_frame(gauge, pd.date_range(start, end))}

    def pull(gauges, start, end, *, var, interval, data_type, data_source):
        calls.append((gauges, start, end, var, interval, data_type, data_source))
        return answer["fetch"](gauges[0], start, end, var)

    monkeypatch.setattr("mdba_gauge_getter.gauge_getter.gauge_pull", pull)
    monkeypatch.setattr(observed, "CACHE_DIR", tmp_path)
    return calls, answer


def test_optional_getter_imports_on_current_python():
    pytest.importorskip("mdba_gauge_getter")
    import mdba_gauge_getter.gauge_getter as getter

    assert callable(getter.gauge_pull)


def test_cache_hit_and_raw_quality_codes(getter_stub):
    calls, _ = getter_stub
    start, end = date(2000, 1, 1), date(2000, 1, 2)
    first = observed.fetch_gauge("412033", start, end)
    second = observed.fetch_gauge("412033", start, end)

    assert first.status == second.status == "ok"
    assert len(calls) == 1
    assert second.frame["QUALITYCODE"].tolist() == [140, 140]
    assert second.frame["VALUE"].tolist() == [1.25, 1.25]
    assert list(second.frame) == observed.RAW_COLUMNS


def test_quality_placeholders_are_dropped_but_valid_zero_is_kept(getter_stub):
    _, answer = getter_stub
    raw = raw_frame("412033", pd.date_range("2000-01-01", periods=9), ["0.000"] * 8 + ["invalid"])
    raw["QUALITYCODE"] = ["255", 201, 151, 999, 1000, 140, 150, 998, 140]
    answer["fetch"] = lambda *args: raw

    result = observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 9))

    assert result.status == "ok"
    assert result.frame["QUALITYCODE"].tolist() == [140, 150, 998, 140]
    assert result.frame["VALUE"].iloc[:3].tolist() == [0.0, 0.0, 0.0]
    assert pd.isna(result.frame["VALUE"].iloc[3])
    assert pd.api.types.is_datetime64_any_dtype(result.frame["DATETIME"])


def test_old_cache_version_is_not_reused(getter_stub, tmp_path):
    calls, _ = getter_stub
    old_key = hashlib.sha256("v2\0".encode() + b"412033\0F").hexdigest()
    raw_frame("412033", ["2000-01-01"], ["999"]).to_csv(tmp_path / f"{old_key}.csv", index=False)
    (tmp_path / f"{old_key}.json").write_text(json.dumps({"start": "2000-01-01", "end": "2000-01-01", "fetched_at": time.time()}))

    result = observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 1))

    assert len(calls) == 1 and result.frame["VALUE"].tolist() == [1.25]
    assert observed._cache_paths("412033", "F")[0].stem != old_key


def test_cache_extends_only_missing_head_and_tail(getter_stub):
    calls, _ = getter_stub
    observed.fetch_gauge("412033", date(2000, 1, 3), date(2000, 1, 5))
    observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 7))

    assert [(call[1], call[2]) for call in calls] == [
        (date(2000, 1, 3), date(2000, 1, 5)),
        (date(2000, 1, 1), date(2000, 1, 2)),
        (date(2000, 1, 6), date(2000, 1, 7)),
    ]
    cached = observed._read_cache("412033", "F")
    assert cached is not None and len(cached[0]) == 7


def test_disjoint_request_replaces_the_cache_instead_of_leaving_a_hole(getter_stub):
    calls, _ = getter_stub
    observed.fetch_gauge("412033", date(2020, 1, 1), date(2020, 1, 4))
    observed.fetch_gauge("412033", date(1990, 1, 1), date(1990, 1, 4))
    observed.fetch_gauge("412033", date(1990, 1, 5), date(1990, 1, 6))
    wide = observed.fetch_gauge("412033", date(1990, 1, 1), date(2020, 1, 4))

    assert [(call[1], call[2]) for call in calls] == [
        (date(2020, 1, 1), date(2020, 1, 4)),
        (date(1990, 1, 1), date(1990, 1, 4)),
        (date(1990, 1, 5), date(1990, 1, 6)),
        (date(1990, 1, 7), date(2020, 1, 4)),
    ]
    assert len(wide.frame) == (date(2020, 1, 4) - date(1990, 1, 1)).days + 1


def test_expired_cache_does_not_keep_its_wider_coverage(getter_stub):
    observed._write_cache("412033", "F", raw_frame("412033", ["1990-01-01", "2020-01-02"]), date(1990, 1, 1), date(2020, 1, 2))
    _, meta_path = observed._cache_paths("412033", "F")
    meta_path.write_text(json.dumps(json.loads(meta_path.read_text()) | {"fetched_at": 0}))

    observed.fetch_gauge("412033", date(2020, 1, 2), date(2020, 1, 2), ttl=1)

    cached = observed._read_cache("412033", "F")
    assert cached is not None and (cached[1]["start"], cached[1]["end"]) == ("2020-01-02", "2020-01-02")


@pytest.mark.parametrize("refresh", [False, True])
def test_expired_or_explicit_refresh_fetches_again(getter_stub, refresh):
    calls, answer = getter_stub
    start, end = date(2000, 1, 1), date(2000, 1, 2)
    observed.fetch_gauge("412033", start, end)
    answer["fetch"] = lambda gauge, *args: raw_frame(gauge, ["2000-01-01", "2000-01-02"], ["2", "2"])

    result = observed.fetch_gauge("412033", start, end, ttl=0 if not refresh else observed.DEFAULT_TTL, refresh=refresh)

    assert result.status == "ok" and result.frame["VALUE"].tolist() == [2, 2]
    assert len(calls) == 2


@pytest.mark.parametrize(
    "error",
    [
        HTTPError("HTTP 429"),
        HTTPError("HTTP 408"),
        HTTPError("HTTP 502"),
        ConnectionError("connection lost"),
        json.JSONDecodeError("bad JSON", "x", 0),
        FileNotFoundError("~/bom_water/cache/stations.json"),
        KeyError("bug"),
    ],
)
def test_source_errors_map_to_error_and_are_not_cached(getter_stub, tmp_path, error):
    calls, answer = getter_stub

    def fail(*args):
        raise error

    answer["fetch"] = fail
    result = observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 2), retries=0)

    assert result.status == "error" and result.frame.empty and str(error) in result.message
    assert len(calls) == 1 and not any(tmp_path.iterdir())


@pytest.mark.parametrize("error", [ConnectionError("connection lost"), *[HTTPError(f"HTTP {code}") for code in (500, 502, 503, 504)]])
def test_transient_errors_retry_with_jittered_backoff(getter_stub, monkeypatch, error):
    """Verify connection and HTTP failures retry with bounded exponential jitter."""
    calls, answer = getter_stub
    delays: list[float] = []
    monkeypatch.setattr(observed.random, "uniform", lambda left, right: 1.25)
    monkeypatch.setattr(observed.time, "sleep", delays.append)

    def flaky(gauge, start, end, var):
        if len(calls) < 3:
            raise error
        return raw_frame(gauge, pd.date_range(start, end))

    answer["fetch"] = flaky
    result = observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 2))

    assert result.status == "ok" and len(calls) == 3
    assert delays == [0.625, 1.25]


@pytest.mark.parametrize(
    "error", [HTTPError("HTTP 400"), HTTPError("HTTP 401"), FileNotFoundError("~/bom_water/cache/stations.json"), json.JSONDecodeError("bad JSON", "x", 0)]
)
def test_nontransient_errors_are_not_retried(getter_stub, error):
    """Verify invalid requests, missing setup and malformed JSON stop without automatic retries."""
    calls, answer = getter_stub

    def fail(*args):
        raise error

    answer["fetch"] = fail
    result = observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 2))

    assert result.status == "error" and len(calls) == 1


def test_retry_exhaustion_is_an_error(getter_stub, monkeypatch):
    calls, answer = getter_stub
    monkeypatch.setattr(observed.time, "sleep", lambda delay: None)

    def fail(*args):
        raise ConnectionError("connection lost")

    answer["fetch"] = fail
    result = observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 2))

    assert result.status == "error" and len(calls) == 3


def test_advisory_timeout_does_not_cache_a_late_result(getter_stub, tmp_path):
    calls, answer = getter_stub
    release, finished = threading.Event(), threading.Event()

    def blocked(gauge, *args):
        release.wait(5)
        finished.set()
        return raw_frame(gauge, ["2000-01-01"])

    answer["fetch"] = blocked
    try:
        result = observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 2), timeout=0.02)
        assert result.status == "error" and "advisory timeout" in result.message
        assert len(calls) == 1 and not finished.is_set()
    finally:
        release.set()
        assert finished.wait(5)
    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize("gauge", ["412033", "unknown", "A4261002"])
@pytest.mark.parametrize("placeholders", [False, True])
def test_no_data_is_ambiguous_empty_and_cached(getter_stub, gauge, placeholders):
    calls, answer = getter_stub
    raw = raw_frame(gauge, ["1900-01-01"], ["0.000"]) if placeholders else pd.DataFrame()
    if placeholders:
        raw["QUALITYCODE"] = 255
    answer["fetch"] = lambda *args: raw

    first = observed.fetch_gauge(gauge, date(1900, 1, 1), date(1900, 1, 4))
    second = observed.fetch_gauge(gauge, date(1900, 1, 1), date(1900, 1, 4))

    assert first.status == second.status == "empty"
    assert first.message == second.message == "No data returned for this gauge and period"
    assert list(first.frame) == list(second.frame) == observed.RAW_COLUMNS
    assert len(calls) == 1


def test_invalid_requests_do_not_call_getter(getter_stub):
    calls, _ = getter_stub
    assert observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 2), var="X").status == "unsupported"
    assert observed.fetch_gauge("412033", date(2000, 1, 2), date(2000, 1, 1)).status == "error"
    assert not calls


def test_same_gauge_variable_is_deduplicated(getter_stub):
    """Verify concurrent requests for one measurement share a single source fetch."""
    calls, _ = getter_stub
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 2)), range(4)))
    assert all(result.status == "ok" for result in results)
    assert len(calls) == 1


def test_fetch_many_isolates_failures_and_reports_progress(getter_stub, monkeypatch):
    """Verify failed gauges leave successful results intact and report terminal progress."""
    calls, answer = getter_stub
    progress, statuses = [], []
    monkeypatch.setattr(observed.time, "sleep", lambda delay: None)

    def fetch(gauge, start, end, var):
        """Fail the bad gauge and return daily readings for the successful gauge."""
        if gauge == "bad":
            raise HTTPError("HTTP 429")
        return raw_frame(gauge, pd.date_range(start, end))

    answer["fetch"] = fetch
    frames, results = observed.fetch_many(
        ["412033", "bad"],
        date(2000, 1, 1),
        date(2000, 1, 2),
        progress=lambda *args: progress.append(args),
        status_progress=lambda *args: statuses.append(args),
    )

    assert set(frames) == {("412033", "F"), ("412033", "L")}
    assert results[("bad", "F")].status == results[("bad", "L")].status == "error"
    assert len(calls) == 8  # Two successes and three attempts for each failed measurement.
    assert len(statuses) == 4
    assert progress == [("Fetching gauges", 1, 2), ("Fetching gauges", 2, 2)]


def test_standard_frame_coerces_values_and_fills_daily_dates():
    frame = raw_frame("412033", ["2000-01-01", "2000-01-03"], ["1.5", "3.5"])
    standard = observed.to_standard_frame({("412033", "F"): frame})

    assert standard["Date"].tolist() == ["2000-01-01", "2000-01-02", "2000-01-03"]
    assert standard.loc[0, "412033_flow"] == 1.5
    assert pd.isna(standard.loc[1, "412033_flow"])
    assert standard.loc[2, "412033_flow"] == 3.5


def test_standard_frame_passes_the_existing_standard_timeseries_cleaner(tmp_path):
    import server

    standard = observed.to_standard_frame({("412033", "F"): raw_frame("412033", ["2000-01-01", "2000-01-03"], ["1", "3"])})
    path = tmp_path / "observed.csv"
    standard.to_csv(path, index=False)

    flow, level, salinity = server.load_frames("Standard time-series", str(path))

    assert list(flow.columns) == ["412033"]
    assert len(flow) == 3 and flow.iloc[1, 0] != flow.iloc[1, 0]
    assert level.empty and salinity.empty


@pytest.mark.slow
def test_observed_worker_matches_standard_upload_results_and_stored_series(monkeypatch, tmp_path):
    """Verify observed and uploaded readings produce matching analysis and stored series."""
    import server

    scenario = Path(__file__).resolve().parents[1] / "data/example_scenarios/dummy_goulburn_scenario_A.csv"
    gauges = ["405201", "405202", "405203", "405232"]
    flow, level, _ = server.load_frames("Standard time-series", str(scenario))
    table = server.parameter_sheet()
    partner = server._partner_gauges(table[table.Gauge.astype(str).isin(gauges)])
    all_gauges = sorted(set(gauges) | partner)

    def raw_series(frame, gauge):
        series = frame[gauge].dropna()
        return pd.DataFrame(
            {
                "DATASOURCEID": "NSW",
                "SITEID": gauge,
                "SUBJECTID": "WATER",
                "DATETIME": series.index,
                "VALUE": series.astype(str).to_numpy(),
                "QUALITYCODE": 140,
            }
        )

    fetched = {(gauge, variable): raw_series(frame, gauge) for variable, frame in (("F", flow), ("L", level)) for gauge in all_gauges if gauge in frame.columns}
    statuses = {key: observed.GaugeResult(value, "ok") for key, value in fetched.items()}
    monkeypatch.setattr(observed, "fetch_many", lambda requested, *args, **kwargs: (fetched, statuses))
    monkeypatch.setattr(server, "STORE", tmp_path)
    server.FLOWS.clear()
    server.JOBS.clear()

    server.JOBS["upload-job"] = {"status": "running", "t0": time.time()}
    server.worker("upload-job", scenario.read_bytes(), gauges, "baseline", "Standard time-series", scenario.name)
    expected_rows = server.JOBS["upload-job"]["rows"]
    server.JOBS["observed-job"] = {"status": "running", "t0": time.time()}
    sdl = str(table.loc[table.Gauge.astype(str).eq(gauges[0]), "SWSDLName"].iloc[0])
    server.observed_worker("observed-job", sdl, gauges, date(2000, 7, 1), date(2020, 6, 30), False, False)
    actual = server.JOBS["observed-job"]

    assert actual["status"] == "done", actual
    assert actual["stats"]["partial_water_years"] == {"start": False, "end": False}

    def project(rows):
        return sorted(tuple(row.get(key) for key in server.VAL_KEYS) for row in rows)

    assert project(actual["rows"]) == project(expected_rows)
    expected_flow = server.load_series("baseline", "flow").sort_index().sort_index(axis=1)
    actual_flow = server.load_series(server._observed_scenario_id(sdl), "flow").sort_index().sort_index(axis=1)
    pd.testing.assert_frame_equal(actual_flow, expected_flow)


# ---- error envelope (additive; legacy status/message/retryable stay) ----------------------------------------------
@pytest.mark.parametrize(
    "exc,code,retryable",
    [
        (FileNotFoundError("~/bom_water/cache/stations.json"), "STATION_LIST_SETUP_REQUIRED", False),
        (ImportError("Missing gauge dependency"), "CONFIGURATION_REQUIRED", False),
        (ValueError("Invalid date range"), "INVALID_REQUEST", False),
        (RuntimeError("source busy"), "SERVICE_BUSY", True),
        (TimeoutError("No response"), "NETWORK_TIMEOUT", True),
        (KeyError("bug"), "UNEXPECTED_ERROR", True),
    ],
)
def test_error_envelope_codes(exc, code, retryable):
    """Verify exception classification populates the public error envelope consistently."""
    result = observed._error_result(exc, var="L")
    assert result.status == "error"
    assert result.message == f"{type(exc).__name__}: {exc}"
    assert result.retryable is retryable
    assert result.attempts == 1
    assert result.error["code"] == code
    assert result.error["retryable"] is retryable
    assert result.error["measurement"] == "level"
    assert result.error["attempts"] == 1
    assert result.error["max_attempts"] == 3
    assert len(result.error["diagnostic_id"]) == 32 and int(result.error["diagnostic_id"], 16) >= 0
    assert result.error["detail"] == result.message
    assert result.error["retry_after_s"] == (60.0 if code == "SERVICE_BUSY" else None)


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (400, "INVALID_REQUEST", False),
        (401, "CONFIGURATION_REQUIRED", False),
        (403, "CONFIGURATION_REQUIRED", False),
        (404, "INVALID_REQUEST", False),
        (408, "NETWORK_TIMEOUT", True),
        (425, "SERVICE_BUSY", True),
        (429, "SERVICE_BUSY", True),
        (502, "UNEXPECTED_ERROR", True),
        (503, "SERVICE_BUSY", True),
        (504, "NETWORK_TIMEOUT", True),
    ],
)
def test_http_envelope_preserves_retryability(status, code, retryable):
    """Verify HTTP status codes determine both error codes and retryability."""
    result = observed._error_result(HTTPError(f"HTTP {status}"), var="F")
    assert result.error["code"] == code
    assert result.retryable is retryable and result.error["retryable"] is retryable


def test_requests_and_json_error_classification():
    """Verify network and JSON failures receive the expected retryable error codes."""
    from requests.exceptions import Timeout as RequestTimeout

    for exc in (RequestTimeout("timed out"), ConnectionError("connection lost")):
        result = observed._error_result(exc, var="F")
        assert result.error["code"] == "NETWORK_TIMEOUT" and result.retryable is True
    result = observed._error_result(json.JSONDecodeError("bad JSON", "", 0), var="F")
    assert result.error["code"] == "UNEXPECTED_ERROR" and result.retryable is True


def test_error_details_and_legacy_message_are_redacted():
    """Verify both diagnostic text fields remove URL and labelled credentials."""
    exc = RuntimeError(
        "GET https://alice:password123@example.test/data?token=query-secret#fragment-secret "
        'api_key="key-secret" token=token-secret client_secret=client-secret '
        "Authorization: Bearer bearer-secret"
    )
    result = observed._error_result(exc, var="F")
    for text in (result.message, result.error["detail"]):
        assert "https://example.test/data" in text and "[REDACTED]" in text
        for secret in ("alice", "password123", "query-secret", "fragment-secret", "key-secret", "token-secret", "client-secret", "bearer-secret"):
            assert secret not in text
    assert result.message == result.error["detail"]


def test_setup_error_uses_one_attempt_without_sleep(getter_stub, monkeypatch):
    """Verify a missing station cache fails once without scheduling a retry."""
    calls, answer = getter_stub
    sleeps = []
    monkeypatch.setattr(observed.time, "sleep", sleeps.append)

    def fail(*args):
        """Simulate a missing local station cache."""
        raise FileNotFoundError("~/bom_water/cache/stations.json")

    answer["fetch"] = fail
    result = observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 2), var="L")
    assert len(calls) == 1 and sleeps == []
    assert result.attempts == result.error["attempts"] == 1
    assert result.error["measurement"] == "level"
    assert result.error["code"] == "STATION_LIST_SETUP_REQUIRED" and result.retryable is False


@pytest.mark.parametrize("retries", [2, 10])
def test_busy_exhaustion_reports_actual_attempts_and_last_sleep(getter_stub, monkeypatch, retries):
    """Verify busy retries are capped at three attempts and disclose the final backoff."""
    calls, answer = getter_stub
    sleeps = []
    monkeypatch.setattr(observed.time, "sleep", sleeps.append)

    def fail(*args):
        """Simulate a persistently busy HTTP service."""
        raise HTTPError("HTTP 503 source busy")

    answer["fetch"] = fail
    result = observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 2), retries=retries)
    assert len(calls) == 3 and sleeps == [10.0, 30.0]
    assert result.status == "error" and result.retryable is True
    assert result.error["code"] == "SERVICE_BUSY"
    assert result.attempts == result.error["attempts"] == 3 and result.error["max_attempts"] == 3
    assert result.error["retry_after_s"] == 30.0


@pytest.mark.parametrize("seconds", [0.0, 7.0, 45.0])
def test_busy_retry_after_takes_precedence_over_last_sleep(getter_stub, monkeypatch, seconds):
    """Verify the service retry delay overrides the last sleep in the error envelope."""
    calls, answer = getter_stub
    sleeps = []
    inflight: list[observed.GaugeResult] = []
    monkeypatch.setattr(observed.time, "sleep", sleeps.append)
    monkeypatch.setattr(observed, "_emit_inflight", inflight.append)

    def fail(*args):
        """Simulate a busy service requesting the parametrized retry delay."""
        raise HTTPError(f"HTTP 503 source busy; retry after {seconds:g} seconds")

    answer["fetch"] = fail
    result = observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 2))
    assert len(calls) == 3
    assert sleeps == [max(10.0, seconds), max(30.0, seconds)]
    assert [result.error["retry_after_s"] for result in inflight if result.status == "retry_wait" and result.error is not None] == sleeps
    assert result.error["retry_after_s"] == seconds and result.attempts == 3


def test_busy_without_header_or_sleep_uses_sixty_seconds():
    """Verify busy errors without delay metadata advertise a sixty-second cooldown."""
    assert observed._error_result(RuntimeError("source busy"), var="F").error["retry_after_s"] == 60.0


def test_deadline_can_end_busy_query_before_three_attempts(getter_stub, monkeypatch):
    """Verify the advisory deadline prevents a retry that cannot fit within it."""
    calls, answer = getter_stub
    sleeps = []
    monkeypatch.setattr(observed.time, "sleep", sleeps.append)
    monkeypatch.setattr(observed.time, "monotonic", lambda: 0.0)

    def fail(*args):
        """Simulate a busy service whose backoff exceeds the query deadline."""
        raise HTTPError("HTTP 503 source busy")

    answer["fetch"] = fail
    result = observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 2), timeout=1.0)
    assert len(calls) == 1 and sleeps == []
    assert result.status == "error" and result.retryable is True and result.attempts == 1
    assert result.error["retry_after_s"] == 60.0


def test_timeout_exhaustion_reports_three_attempts(getter_stub, monkeypatch):
    """Verify repeated timeouts disclose three attempts and the advisory timeout message."""
    calls, answer = getter_stub
    sleeps = []
    monkeypatch.setattr(observed.time, "sleep", sleeps.append)

    def fail(*args):
        """Simulate an immediate timeout on each source attempt."""
        raise TimeoutError("upstream timed out")

    answer["fetch"] = fail
    result = observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 2))
    assert len(calls) == 3 and sleeps == [10.0, 30.0]
    assert result.error["code"] == "NETWORK_TIMEOUT" and result.error["retry_after_s"] is None
    assert result.attempts == result.error["attempts"] == 3
    assert result.message == "TimeoutError: Gauge 412033 exceeded 90 s advisory timeout"


def test_inflight_order_and_completion_only_status_progress(getter_stub, monkeypatch):
    """Verify retry notifications precede terminal callbacks and gauge completion counts."""
    calls, answer = getter_stub
    events, completions, progress = [], [], []

    def fail(*args):
        """Simulate a busy source throughout the retry sequence."""
        raise RuntimeError("source busy")

    def sleep(delay):
        """Record a retry delay together with its measurement context."""
        events.append((observed._FETCH_STATE.get().var, "sleep", delay))

    def inflight(gauge, var, result):
        """Record and validate running and retry-wait notifications."""
        assert gauge == "412033"
        events.append((var, result.status, result.attempts))
        if result.status == "retry_wait":
            assert result.error["code"] == "SERVICE_BUSY" and result.error["attempts"] == result.attempts
            assert result.error["retry_after_s"] == (10.0 if result.attempts == 1 else 30.0)
        else:
            assert result.status == "running" and result.error is None

    def completed(gauge, var, result):
        """Record the terminal callback in the shared event sequence."""
        completions.append((gauge, var, result))
        events.append((var, result.status, result.attempts))

    answer["fetch"] = fail
    monkeypatch.setattr(observed.time, "sleep", sleep)
    frames, statuses = observed.fetch_many(
        ["412033"],
        date(2000, 1, 1),
        date(2000, 1, 2),
        workers=1,
        progress=lambda *args: progress.append(args),
        status_progress=completed,
        inflight_progress=inflight,
    )
    assert frames == {} and len(calls) == 6
    assert len(statuses) == len(completions) == 2
    assert progress == [(observed.GAUGE_STAGE, 1, 1)]
    for var in ("F", "L"):
        assert [(status, value) for item, status, value in events if item == var] == [
            ("running", 1),
            ("retry_wait", 1),
            ("sleep", 10.0),
            ("running", 2),
            ("retry_wait", 2),
            ("sleep", 30.0),
            ("running", 3),
            ("error", 3),
        ]


@pytest.mark.parametrize("terminal_status", ["ok", "empty", "error"])
def test_attempts_across_two_cache_ranges(getter_stub, monkeypatch, terminal_status):
    """Verify attempts reflect the worst successful range or the failing range, rather than their sum."""
    calls, answer = getter_stub
    frame = pd.DataFrame({"value": [1.0]})
    outcomes = [
        ConnectionError("connection lost"),
        frame,
        FileNotFoundError("BoM station cache missing") if terminal_status == "error" else frame,
    ]
    monkeypatch.setattr(observed.time, "sleep", lambda delay: None)
    monkeypatch.setattr(observed, "_normalise", lambda raw: raw)

    def fetch(*args):
        """Return or raise the next scripted source outcome."""
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome.copy()

    def cached(gauge, start, end, var="F", **kwargs):
        """Simulate two missing cache ranges using the real source retry logic."""
        try:
            for left, right in ((start, start), (end, end)):
                observed._fetch_source(gauge, left, right, var, kwargs["timeout"], kwargs["retries"])
            return observed.GaugeResult(frame if terminal_status == "ok" else pd.DataFrame(), terminal_status)
        except Exception as exc:
            return observed._error_result(exc, var=var)

    answer["fetch"] = fetch
    monkeypatch.setattr(observed, "_fetch_gauge_cached", cached)
    result = observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 2))
    assert len(calls) == 3 and result.status == terminal_status
    if terminal_status == "error":
        assert result.attempts == result.error["attempts"] == 1  # the failing range had one attempt
        assert result.error["code"] == "STATION_LIST_SETUP_REQUIRED" and result.retryable is False
    else:
        assert result.attempts == 2 and result.error is None and result.retryable is False  # worst range, not 3 requests


def test_invalid_date_range_keeps_legacy_message_and_adds_envelope(getter_stub):
    """Verify invalid dates preserve legacy wording while adding structured error metadata."""
    calls, _ = getter_stub
    result = observed.fetch_gauge("412033", date(2000, 1, 2), date(2000, 1, 1))
    assert calls == [] and result.status == "error"
    assert result.message == "Start date must not follow end date" and result.retryable is False
    assert result.error["code"] == "INVALID_REQUEST" and result.attempts == result.error["attempts"] == 1


def test_ok_and_empty_results_have_no_envelope(getter_stub):
    """Verify successful and empty measurements carry no error envelope."""
    _, answer = getter_stub
    ok = observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 2))
    assert (ok.status, ok.error, ok.attempts) == ("ok", None, 1)
    answer["fetch"] = lambda gauge, start, end, var: pd.DataFrame(columns=observed.RAW_COLUMNS)
    empty = observed.fetch_gauge("412034", date(2000, 1, 1), date(2000, 1, 2))
    assert (empty.status, empty.error) == ("empty", None)


# ---- coverage (daily source; no approved quality policy exists, so nothing may be called complete without one) --------
# Two agency tables: the same number means different things (code 12 is F in T1 and A in T2; 10 is A in both).
POLICY = observed.QualityPolicy(frozenset("ABCE"), {"T1": {10: "A", 12: "F", 42: "F", 130: "E"}, "T2": {10: "A", 12: "A", 99: "B"}}, "test-v1")
D = date


def _cov(days, values, qualities=None, **kwargs):
    """Build a daily readings frame and assess coverage, defaulting to flow units."""
    frame = pd.DataFrame({"DATETIME": pd.to_datetime(days), "VALUE": values, "QUALITYCODE": [10] * len(days) if qualities is None else qualities})
    kwargs.setdefault("var", "F")
    return observed.assess_coverage(frame, kwargs.pop("start"), kwargs.pop("end"), **kwargs)


def test_coverage_zero_is_a_real_observation_and_flow_level_are_independent():
    """Verify zero readings count toward coverage and flow and level are assessed separately."""
    zero = _cov(["2024-01-01"], [0], start=D(2024, 1, 1), end=D(2024, 1, 1), policy=POLICY)
    assert (zero["coverage_pct"], zero["coverage_complete"], zero["zero_count"], zero["all_zero"], zero["units"]) == (100.0, True, 1, True, "ML/d")
    assert zero["last_reading"] == {"timestamp": "2024-01-01", "value": 0.0, "quality_code": 10}
    missing_flow = observed.assess_coverage(pd.DataFrame(), D(2024, 1, 1), D(2024, 1, 2), var="F", policy=POLICY)
    level = _cov(["2024-01-01", "2024-01-02"], [1.2, 1.3], start=D(2024, 1, 1), end=D(2024, 1, 2), var="L", policy=POLICY)
    assert (missing_flow["coverage_pct"], missing_flow["coverage_complete"], missing_flow["last_reading"]) == (0.0, False, None)
    assert (level["units"], level["coverage_complete"]) == ("m", True)


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -float("inf"), "abc", "NaN", "0", "1.5", True, pd.NA])
def test_coverage_invalid_values_are_never_zero_or_present(value):
    """Verify invalid readings count as neither usable values nor zero observations."""
    cov = _cov(["2024-01-01"], [value], start=D(2024, 1, 1), end=D(2024, 1, 1), policy=POLICY)
    assert (cov["valid_count"], cov["zero_count"], cov["all_zero"], cov["last_reading"], cov["coverage_complete"]) == (0, 0, None, None, False)


def test_coverage_placeholders_duplicates_and_ordering():
    """Verify placeholder filtering, last-duplicate selection and chronological last readings."""
    cov = _cov(
        ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-06"],
        [1, 1, 1, 1, 1, "abc"],
        [255, 201, 151, 999, 1200, 10],
        start=D(2024, 1, 1),
        end=D(2024, 1, 6),
    )
    assert (cov["valid_count"], cov["coverage_pct"], cov["excluded_quality_count"], cov["unknown_quality_count"]) == (0, 0.0, 5, 1)
    assert cov["gaps"] == [{"start": "2024-01-01", "end_exclusive": "2024-01-07", "missing_count": 6}]
    dup = _cov(["2024-01-03", "2024-01-01", "2024-01-02", "2024-01-01"], [3, 99, 2, 1], start=D(2024, 1, 1), end=D(2024, 1, 3), policy=POLICY)
    assert (dup["expected_count"], dup["valid_count"], dup["coverage_pct"]) == (3, 3, 100.0)  # duplicates never raise coverage
    assert dup["last_reading"]["timestamp"] == "2024-01-03"  # chosen by date, not row order
    bad_last = _cov(["2024-01-01", "2024-01-01"], [7, None], start=D(2024, 1, 1), end=D(2024, 1, 1), policy=POLICY)
    assert bad_last["valid_count"] == 0  # an invalid final duplicate does not fall back to an earlier value


def test_coverage_expected_zero_empty_and_cutoff():
    """Verify empty periods and reporting cutoffs produce consistent coverage counts."""
    none_expected = _cov(["2024-01-02"], [1], start=D(2024, 1, 2), end=D(2024, 1, 2), policy=POLICY, reporting_cutoff=D(2024, 1, 1))
    assert (none_expected["expected_count"], none_expected["coverage_pct"], none_expected["coverage_complete"], none_expected["gaps"]) == (0, None, None, [])
    empty = observed.assess_coverage(pd.DataFrame(), D(2024, 1, 1), D(2024, 1, 3), var="F")
    assert (empty["expected_count"], empty["coverage_pct"], empty["coverage_complete"], empty["all_zero"]) == (3, 0.0, None, None)
    cut = _cov(["2024-01-01", "2024-01-02", "2024-01-03"], [1, 2, 0], start=D(2024, 1, 1), end=D(2024, 1, 3), policy=POLICY, reporting_cutoff=D(2024, 1, 2))
    assert (cut["expected_count"], cut["zero_count"], cut["last_reading"]["timestamp"], cut["period"]["end_exclusive"]) == (2, 0, "2024-01-02", "2024-01-03")
    trusted = _cov(["2024-01-01", "2024-01-02"], [1, 2], start=D(2024, 1, 1), end=D(2024, 1, 2))
    assert trusted["period"]["end_exclusive"] == "2024-01-03"


def test_coverage_policy_gates_completeness_and_98_percent_is_never_complete():
    """Verify completeness requires a quality policy and every expected daily reading."""
    days = [d.date().isoformat() for d in pd.date_range("2024-01-01", "2024-02-19") if d.date() != D(2024, 1, 25)]
    for policy, complete in ((None, None), (POLICY, False)):
        cov = _cov(days, [1] * len(days), start=D(2024, 1, 1), end=D(2024, 2, 19), policy=policy)
        assert (cov["expected_count"], cov["valid_count"], cov["coverage_pct"], cov["coverage_complete"]) == (50, 49, 98.0, complete)
        assert cov["gaps"] == [{"start": "2024-01-25", "end_exclusive": "2024-01-26", "missing_count": 1}]
    full = {p: _cov(["2024-01-01", "2024-01-02"], [1, 2], start=D(2024, 1, 1), end=D(2024, 1, 2), policy=p) for p in (None, POLICY)}
    assert full[None]["coverage_pct"] == 100.0 and full[None]["coverage_complete"] is None and full[None]["coverage_status"] == "unassessed_quality_policy"
    assert full[POLICY]["coverage_complete"] is True and full[POLICY]["coverage_status"] == "assessed" and full[POLICY]["policy_version"] == "test-v1"
    mixed = _cov(
        ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"], [1, 2, 3, 4], ["10", 42, None, "abc"], start=D(2024, 1, 1), end=D(2024, 1, 4), policy=POLICY
    )
    # F (42) is cleaned out; a missing or unparseable code is kept and counted as unknown, never as bad data
    assert (mixed["valid_count"], mixed["excluded_quality_count"], mixed["unknown_quality_count"], mixed["coverage_pct"]) == (3, 1, 2, 75.0)
    provisional = _cov(["2024-01-01", "2024-01-02", "2024-01-03"], [0, 2, None], [10, None, 42], start=D(2024, 1, 1), end=D(2024, 1, 3))
    assert (provisional["valid_count"], provisional["unknown_quality_count"], provisional["all_zero"], "policy_version" in provisional) == (2, 3, False, False)


def test_coverage_returns_every_gap_without_a_cap():
    """Verify the coverage payload retains every missing-day interval."""
    days = list(pd.date_range("2024-01-01", "2024-02-09"))
    present = [d.date().isoformat() for d in days[::2]]
    cov = _cov(present, [1] * len(present), start=D(2024, 1, 1), end=D(2024, 2, 9), policy=POLICY)
    assert len(cov["gaps"]) == 20 and cov["gaps_truncated"] is False
    assert sum(g["missing_count"] for g in cov["gaps"]) == 20


@pytest.mark.parametrize("with_policy", [False, True])
def test_coverage_unfiltered_runs_are_not_assessed_against_a_retrieval_boundary(with_policy):
    """Verify unfiltered runs retain reading counts without claiming period coverage."""
    cov = _cov(
        ["2024-01-01", "2024-01-02"],
        [0, 2],
        start=D(1890, 1, 1),
        end=D(2024, 1, 3),
        policy=POLICY if with_policy else None,
        filter_period=False,
        reporting_cutoff=D(2024, 1, 2),
    )
    assert cov["coverage_status"] == "unassessed_open_period"
    assert (cov["coverage_pct"], cov["coverage_complete"], cov["expected_count"], cov["gaps"]) == (None, None, None, None)
    assert (cov["valid_count"], cov["zero_days_count"], cov["last_reading"]["timestamp"]) == (2, 1, "2024-01-02")
    empty = observed.assess_coverage(pd.DataFrame(), D(1890, 1, 1), D(2024, 1, 1), var="F", filter_period=False)
    assert (empty["valid_count"], empty["coverage_pct"], empty["expected_count"]) == (0, None, None)


@pytest.mark.parametrize(("var", "units"), [("X", None), ("F", "m"), ("L", "feet")])
def test_coverage_unknown_or_incompatible_units_block_assessment(var, units):
    """Verify unsupported variables and incompatible declared units prevent assessment."""
    frame = pd.DataFrame({"DATETIME": pd.to_datetime(["2024-01-01"]), "VALUE": [1], "QUALITYCODE": [10]})
    if units:
        frame.attrs["units"] = units
    cov = observed.assess_coverage(frame, D(2024, 1, 1), D(2024, 1, 1), var=var, policy=POLICY)
    assert cov["coverage_status"] == "unassessed_units" and cov["units"] is None
    assert (cov["coverage_pct"], cov["coverage_complete"], cov["expected_count"], cov["valid_count"], cov["gaps"]) == (None,) * 5


def test_coverage_policy_is_validated():
    """Verify invalid grades, missing tables and blank policy versions are rejected."""
    for grades, tables, version in (
        (frozenset(), {"T": {1: "A"}}, "v"),
        (frozenset("X"), {"T": {1: "A"}}, "v"),
        (frozenset("A"), {}, "v"),
        (frozenset("A"), {"T": {1: "A"}}, " "),
    ):
        with pytest.raises(ValueError):
            observed.QualityPolicy(grades, tables, version)


def test_policy_cleans_only_clear_missing_data_and_keeps_mismatches():
    """Verify agency inference removes disallowed grades while retaining unlisted codes."""
    days = [f"2024-01-0{n}" for n in range(1, 8)]
    # T1 matches 6 of the 7 codes, T2 matches 3: T1 wins, so 12 and 42 are F (removed). 7 is listed nowhere (kept); 130 is "not quality coded" (E, kept).
    cov = _cov(days, [1, 1, 1, 1, 1, 1, 1], [10, 10, 12, 42, 7, 130, 130], start=D(2024, 1, 1), end=D(2024, 1, 7), policy=POLICY)
    assert (cov["quality_table"], cov["quality_table_match_pct"]) == ("T1", 85.7)
    assert (cov["valid_count"], cov["excluded_quality_count"], cov["unknown_quality_count"]) == (5, 2, 1)
    assert (
        cov["gaps"] == [{"start": "2024-01-03", "end_exclusive": "2024-01-05", "missing_count": 2}] and cov["coverage_complete"] is False
    )  # adjacent removed days form one gap
    # the same number graded by the other agency's table: 12 is now A and kept
    other = _cov(days[:3], [1, 1, 1], [12, 99, 12], start=D(2024, 1, 1), end=D(2024, 1, 3), policy=POLICY)
    assert (other["quality_table"], other["valid_count"], other["excluded_quality_count"], other["coverage_complete"]) == ("T2", 3, 0, True)
    # nothing matches any table: everything is a mismatch and is kept; no table is claimed
    none = _cov(days[:2], [1, 1], [500, 501], start=D(2024, 1, 1), end=D(2024, 1, 2), policy=POLICY)
    assert (none["quality_table"], none["quality_table_match_pct"], none["valid_count"], none["unknown_quality_count"]) == (None, 0.0, 2, 2)
    # a NaN/NA value is still removed: that is the other half of "clear missing data"
    na = _cov(days[:2], [None, 1], [10, 10], start=D(2024, 1, 1), end=D(2024, 1, 2), policy=POLICY)
    assert (na["valid_count"], na["gaps"]) == (1, [{"start": "2024-01-01", "end_exclusive": "2024-01-02", "missing_count": 1}])


def test_default_policy_is_grades_a_to_e_over_the_bureau_tables():
    """Verify the bundled Bureau policy, version digest and agency selection metadata."""
    policy = observed.default_quality_policy()
    assert policy is not None and policy.allowed_grades == frozenset("ABCE") and len(policy.tables) == 12
    assert policy.version.startswith("bom-hrs-grades-ABCE-") and len(policy.version.rsplit("-", 1)[1]) == 10  # content digest
    dilw = policy.tables["NSW DILW"]
    assert dilw[130] == "E" and dilw[255] == "F" and 999 not in dilw
    # sentinels dropped upstream are grade F in every table that lists them (so dropping them agrees with the policy); 151 is the exception
    for code in (255, 201):
        assert {t[code] for t in policy.tables.values() if code in t} == {"F"}
    assert {t[151] for t in policy.tables.values() if 151 in t} == {"E", "F"}
    few = policy.select_table([130, 130, 26, 38])  # a handful of common codes is listed by several agencies: tied, and no table is claimed
    assert few["selection"] == "equivalent_tie" and "NSW DILW" in few["candidates"] and few["table"] is None
    sel = policy.select_table([38, 130, 26, 32, 140, 58, 16, 254, 11, 3, 51, 6, 34])  # a realistic NSW code mix
    assert (sel["selection"], sel["table"], sel["match_count"], sel["code_count"]) == ("unique_best", "NSW DILW", 11, 13)
    assert policy.select_table([])["selection"] == "no_codes"


def test_table_ties_are_inspected_not_settled_by_listing_order():
    """Verify tied agency tables are classified by removal decisions independent of ordering."""
    grades = frozenset("ABCE")
    both = observed.QualityPolicy(grades, {"A1": {10: "A", 12: "F"}, "B1": {10: "A", 12: "F"}}, "v")
    swapped = observed.QualityPolicy(grades, {"B1": {12: "F", 10: "A"}, "A1": {12: "F", 10: "A"}}, "v")
    for policy in (both, swapped):  # same decisions: an equivalent tie keeps working, whatever the JSON order
        sel = policy.select_table([10, 12])
        assert (sel["selection"], sel["candidates"], sel["table"]) == ("equivalent_tie", ["A1", "B1"], None)  # no single table is named when none is proven
    split = observed.QualityPolicy(grades, {"A1": {10: "A", 12: "F"}, "B1": {10: "A", 12: "A"}}, "v")
    sel = split.select_table([10, 12])
    assert (sel["selection"], sel["table"], sel["candidates"]) == ("ambiguous", None, ["A1", "B1"])
    assert split.removes(12, sel["candidates"]) is False  # disputed readings are kept
    assert split.removes(12, ["A1"]) is True and split.removes(12, ["B1"]) is False
    third = observed.QualityPolicy(grades, {"A1": {10: "A", 12: "F"}, "B1": {10: "A"}}, "v")  # a missing mapping counts as "keep"
    assert third.select_table([10, 12])["selection"] == "unique_best"
    runner = observed.QualityPolicy(grades, {"A1": {10: "A", 11: "A"}, "B1": {10: "A"}}, "v").select_table([10, 11, 11])
    assert (runner["match_count"], runner["runner_up_match_count"], runner["margin_count"], runner["code_count"]) == (3, 1, 2, 3)


def test_ambiguous_selection_keeps_disputed_readings_and_never_certifies_completeness():
    """Verify disputed readings remain usable while ambiguous agency selection withholds completeness."""
    policy = observed.QualityPolicy(frozenset("ABCE"), {"A1": {10: "A", 12: "F"}, "B1": {10: "A", 12: "A"}}, "v")
    cov = _cov(["2024-01-01", "2024-01-02"], [1, 1], [10, 12], start=D(2024, 1, 1), end=D(2024, 1, 2), policy=policy)
    assert (cov["quality_table_selection"], cov["quality_table"], cov["valid_count"], cov["coverage_pct"], cov["coverage_complete"]) == (
        "ambiguous",
        None,
        2,
        100.0,
        None,
    )
    assert cov["quality_table_candidates"] == ["A1", "B1"] and cov["quality_table_match_count"] == 2 and cov["quality_table_code_count"] == 2


def test_policy_is_frozen_and_validated():
    """Verify published policy tables are immutable copies and reject malformed mappings."""
    source = {"T": {1: "A"}}
    policy = observed.QualityPolicy(frozenset("A"), source, "v")
    source["T"][2] = "F"  # later edits to the caller's dict never reach a published policy
    assert 2 not in policy.tables["T"]
    with pytest.raises(TypeError):
        policy.tables["T"][3] = "A"
    with pytest.raises(TypeError):
        policy.tables["U"] = {1: "A"}
    for bad in ({"T": {}}, {"T": {"1": "A"}}, {"T": {1: "Z"}}, {"T": {True: "A"}}):
        with pytest.raises(ValueError):
            observed.QualityPolicy(frozenset("A"), bad, "v")


def test_unknown_count_only_counts_readings_that_were_actually_kept():
    """Verify unknown quality counts exclude readings with unusable values."""
    cov = _cov(["2024-01-01", "2024-01-02"], [None, 1], [500, 501], start=D(2024, 1, 1), end=D(2024, 1, 2), policy=POLICY)
    assert (cov["valid_count"], cov["unknown_quality_count"], cov["quality_table_selection"]) == (1, 1, "no_match")  # the NaN slot is not "kept"


def test_clean_frame_removes_f_rows_so_downstream_uses_the_counted_readings():
    """Verify downstream filtering removes grade F readings and retains unlisted codes."""
    frame = pd.DataFrame(
        {"DATETIME": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]), "VALUE": [1.0, 2.0, 3.0, 4.0], "QUALITYCODE": [10, 12, 7, 130]}
    )
    cleaned = observed.clean_frame(frame, POLICY)
    assert list(cleaned["VALUE"]) == [1.0, 3.0, 4.0]  # 12 is F in the winning table; 7 (unlisted) and 130 (E) stay
    assert observed.clean_frame(frame, None) is frame and observed.clean_frame(pd.DataFrame(), POLICY).empty


def test_fetch_many_cleans_the_series_it_returns_and_reports_empty_when_nothing_usable_is_left(monkeypatch):
    """Verify returned series use quality filtering and wholly excluded measurements become empty."""

    def fake_fetch_gauge(gauge, start, end, var, **kwargs):
        """Return mixed-quality flow readings and entirely excluded level readings."""
        codes = [10, 42] if var == "F" else [42, 42]
        return observed.GaugeResult(pd.DataFrame({"DATETIME": pd.to_datetime(["2024-01-01", "2024-01-02"]), "VALUE": [1.0, 2.0], "QUALITYCODE": codes}), "ok")

    monkeypatch.setattr(observed, "fetch_gauge", fake_fetch_gauge)
    frames, statuses = observed.fetch_many(["g"], D(2024, 1, 1), D(2024, 1, 2), policy=POLICY)
    assert list(frames[("g", "F")]["VALUE"]) == [1.0] and statuses[("g", "F")].status == "ok"
    assert statuses[("g", "L")].status == "empty" and ("g", "L") not in frames  # every reading was grade F
    assert statuses[("g", "L")].coverage["valid_count"] == 0 and statuses[("g", "L")].coverage["excluded_quality_count"] == 2


def test_policy_load_failure_is_loud_and_not_cached(monkeypatch, tmp_path, caplog):
    """Verify policy loading logs failures and recovers after the policy file is repaired."""
    monkeypatch.setattr(observed, "_DEFAULT_POLICY", None)
    monkeypatch.setattr(observed, "QUALITY_CODES", tmp_path / "missing.json")
    with caplog.at_level("ERROR"):
        assert observed.default_quality_policy() is None
    assert "completeness is disabled" in caplog.text
    (tmp_path / "ok.json").write_text(json.dumps({"retrieved": "2026-01-01", "tables": {"T": {"1": "A"}}}), encoding="utf-8")
    monkeypatch.setattr(observed, "QUALITY_CODES", tmp_path / "ok.json")
    assert observed.default_quality_policy() is not None  # a repaired file is picked up without a restart
    monkeypatch.setattr(observed, "_DEFAULT_POLICY", None)


def test_bomwater_cache_lives_in_the_repo_and_needs_no_download(monkeypatch):
    """The package's default (~/bom_water/cache, built by a download that now fails) is replaced by files shipped in the repository."""
    pytest.importorskip("bom_water")
    import requests

    def no_network(*args, **kwargs):
        """Reject any attempt to download the shipped station metadata."""
        raise AssertionError("bomwater tried to download its station list")

    monkeypatch.setattr(requests, "get", no_network)
    monkeypatch.setattr(requests, "post", no_network)
    observed._use_repo_bom_cache()
    observed._use_repo_bom_cache()  # idempotent
    import bom_water

    client = bom_water.BomWater()
    assert Path(client.cache) == observed.BOM_WATER_HOME / "bom_water" / "cache"
    assert client.properties.Water_Course_Discharge and client.procedures.Pat4_C_B_1_DailyMean
    cache = Path(client.cache)
    assert json.loads((cache / "stations.json").read_text()) == {"type": "FeatureCollection", "features": []}
    assert "sos:Capabilities" in json.loads((cache / "waterML_GetCapabilities.json").read_text())
    import os

    assert os.path.expanduser("~") != str(observed.BOM_WATER_HOME)  # only bomwater's own module is redirected


def test_a_missing_shipped_station_file_is_still_reported_as_a_setup_error():
    """Verify missing repository station metadata remains a nonretryable setup failure."""
    result = observed._error_result(FileNotFoundError(f"[Errno 2] No such file or directory: '{observed.BOM_WATER_HOME}/bom_water/cache/stations.json'"))
    assert result.error["code"] == "STATION_LIST_SETUP_REQUIRED" and result.retryable is False


def test_attach_coverage_is_terminal_only_and_errors_are_unavailable():
    """Verify only terminal results carry coverage and failures expose unavailable metrics."""
    err = observed._attach_coverage(
        observed.GaugeResult(pd.DataFrame(), "error", "x", True, {"code": "UNEXPECTED_ERROR"}, 2), D(2024, 1, 1), D(2024, 1, 3), var="F", policy=POLICY
    )
    assert err.attempts == 2 and err.coverage["coverage_status"] == "unavailable_error" and err.coverage["units"] == "ML/d"
    assert all(
        err.coverage[k] is None
        for k in ("coverage_pct", "coverage_complete", "expected_count", "valid_count", "last_reading", "gaps", "zero_count", "all_zero")
    )
    for status in ("running", "retry_wait"):
        assert observed._attach_coverage(observed.GaugeResult(pd.DataFrame(), status), D(2024, 1, 1), D(2024, 1, 3), var="F").coverage is None


def test_fetch_many_attaches_coverage_before_terminal_callbacks_and_passes_period_flags(monkeypatch):
    """Verify callbacks receive coverage computed with the requested cutoff and period flags."""

    def fake_fetch_gauge(gauge, start, end, var, **kwargs):
        """Return empty flow data and one usable level reading for coverage callbacks."""
        if var == "F":
            return observed.GaugeResult(pd.DataFrame(), "empty")
        return observed.GaugeResult(pd.DataFrame({"DATETIME": pd.to_datetime(["2024-01-01"]), "VALUE": [1.2], "QUALITYCODE": [10]}), "ok")

    monkeypatch.setattr(observed, "fetch_gauge", fake_fetch_gauge)
    seen = []
    _, statuses = observed.fetch_many(
        ["g"],
        D(2024, 1, 1),
        D(2024, 1, 2),
        status_progress=lambda g, v, r: seen.append((g, v, r.coverage is not None)),
        policy=POLICY,
        reporting_cutoff=D(2024, 1, 1),
    )
    assert sorted(seen) == [("g", "F", True), ("g", "L", True)]
    assert statuses[("g", "F")].coverage["coverage_complete"] is False and statuses[("g", "L")].coverage["coverage_complete"] is True
    _, open_period = observed.fetch_many(["g"], D(1890, 1, 1), D(2024, 1, 1), filter_period=False)
    assert all(r.coverage["coverage_status"] == "unassessed_open_period" for r in open_period.values())


def test_coverage_ignores_rows_outside_the_window():
    """Verify readings outside the assessment period do not affect coverage metrics."""
    inside = _cov(["2024-01-02", "2024-01-03"], [1, 0], start=D(2024, 1, 2), end=D(2024, 1, 3), policy=POLICY)
    noisy = _cov(["2023-12-31", "2024-01-02", "2024-01-03", "2024-01-04"], [0, 1, 0, 9], start=D(2024, 1, 2), end=D(2024, 1, 3), policy=POLICY)
    assert noisy == inside  # zeros, later timestamps or placeholders outside [start, end] change nothing


def test_stale_cache_placeholder_rows_never_come_back_as_zero_readings(getter_stub, tmp_path):
    """Cache entries written before the placeholder filter hold 'No Record' (255/201) rows with VALUE 0."""
    calls, _ = getter_stub
    frame = raw_frame("412033", pd.date_range("2000-01-01", periods=4))
    frame["QUALITYCODE"] = [140, 255, 201, 151]  # 151 is dropped by the no-policy rule at request time, not stored as removed
    frame["VALUE"] = ["5.0", "0", "0", "0"]
    data_path, meta_path = observed._cache_paths("412033", "F")
    observed.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    frame.to_csv(data_path, index=False)
    meta_path.write_text(
        json.dumps({"start": "2000-01-01", "end": "2000-01-04", "fetched_at": time.time(), "normalisation": observed.NORMALISATION}), encoding="utf-8"
    )
    result = observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 4))
    assert calls == []  # served from cache
    assert result.status == "ok" and list(result.frame["VALUE"]) == [5.0]  # the three placeholder zeros are gone


def test_151_and_999_are_graded_by_the_matched_table_not_dropped_before_grading(getter_stub):
    """With a policy 151 follows its table's grade (E in one agency, F in others) and 999, listed nowhere, is kept as a mismatch;
    with no policy the older 151 / >=999 rule still applies. 255 and 201 are grade F in every table that lists them and always go."""
    _, answer = getter_stub
    raw = raw_frame("412033", pd.date_range("2000-01-01", periods=6), ["1.0"] * 6)
    raw["QUALITYCODE"] = [10, 151, 999, 255, 201, 10]
    answer["fetch"] = lambda *args: raw
    grades = frozenset("ABCE")
    e_table = observed.QualityPolicy(grades, {"WaterLike": {10: "A", 151: "E"}, "Other": {151: "F", 77: "A"}}, "v")
    f_table = observed.QualityPolicy(grades, {"Other": {10: "A", 151: "F"}}, "v")
    legacy = observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 6))
    assert legacy.frame["QUALITYCODE"].tolist() == [10, 10]  # no policy: 151, 999, 255 and 201 all go (the earlier rule)
    for policy, expected in ((e_table, [10, 151, 999, 10]), (f_table, [10, 999, 10])):
        answer["fetch"] = lambda *args: raw
        result = observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 6), policy=policy, refresh=True)
        assert result.frame["QUALITYCODE"].tolist() == expected
    # WaterLike matches 3 of 4 eligible codes and Other 1, so 151 is E (kept); when two tables tie and disagree it is kept rather than guessed
    tie = observed.QualityPolicy(grades, {"A1": {10: "A", 151: "E"}, "B1": {10: "A", 151: "F"}}, "v")
    result = observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 6), policy=tie, refresh=True)
    assert result.frame["QUALITYCODE"].tolist() == [10, 151, 999, 10]


def test_cache_entries_from_an_older_normalisation_are_refetched_on_access(getter_stub):
    """Verify cached readings refresh when their normalisation metadata is obsolete."""
    calls, _ = getter_stub
    observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 2))
    assert len(calls) == 1
    observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 2))
    assert len(calls) == 1  # current entry served from cache
    _, meta_path = observed._cache_paths("412033", "F")
    meta = json.loads(meta_path.read_text())
    assert meta["normalisation"] == observed.NORMALISATION
    meta.pop("normalisation")  # an entry written before 151 and >=999 stopped being dropped upstream
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 2))
    assert len(calls) == 2  # refreshed lazily, only when accessed
