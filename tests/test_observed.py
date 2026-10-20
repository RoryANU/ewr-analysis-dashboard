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
    calls, _ = getter_stub
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 2)), range(4)))
    assert all(result.status == "ok" for result in results)
    assert len(calls) == 1


def test_fetch_many_isolates_failures_and_reports_progress(getter_stub, monkeypatch):
    calls, answer = getter_stub
    progress, statuses = [], []
    monkeypatch.setattr(observed.time, "sleep", lambda delay: None)

    def fetch(gauge, start, end, var):
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
    result = observed._error_result(HTTPError(f"HTTP {status}"), var="F")
    assert result.error["code"] == code
    assert result.retryable is retryable and result.error["retryable"] is retryable


def test_requests_and_json_error_classification():
    from requests.exceptions import Timeout as RequestTimeout

    for exc in (RequestTimeout("timed out"), ConnectionError("connection lost")):
        result = observed._error_result(exc, var="F")
        assert result.error["code"] == "NETWORK_TIMEOUT" and result.retryable is True
    result = observed._error_result(json.JSONDecodeError("bad JSON", "", 0), var="F")
    assert result.error["code"] == "UNEXPECTED_ERROR" and result.retryable is True


def test_error_details_and_legacy_message_are_redacted():
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
    calls, answer = getter_stub
    sleeps = []
    monkeypatch.setattr(observed.time, "sleep", sleeps.append)

    def fail(*args):
        raise FileNotFoundError("~/bom_water/cache/stations.json")

    answer["fetch"] = fail
    result = observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 2), var="L")
    assert len(calls) == 1 and sleeps == []
    assert result.attempts == result.error["attempts"] == 1
    assert result.error["measurement"] == "level"
    assert result.error["code"] == "STATION_LIST_SETUP_REQUIRED" and result.retryable is False


@pytest.mark.parametrize("retries", [2, 10])
def test_busy_exhaustion_reports_actual_attempts_and_last_sleep(getter_stub, monkeypatch, retries):
    calls, answer = getter_stub
    sleeps = []
    monkeypatch.setattr(observed.time, "sleep", sleeps.append)

    def fail(*args):
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
    calls, answer = getter_stub
    sleeps = []
    monkeypatch.setattr(observed.time, "sleep", sleeps.append)

    def fail(*args):
        raise HTTPError(f"HTTP 503 source busy; retry after {seconds:g} seconds")

    answer["fetch"] = fail
    result = observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 2))
    assert len(calls) == 3
    assert sleeps == [max(10.0, seconds), max(30.0, seconds)]
    assert result.error["retry_after_s"] == seconds and result.attempts == 3


def test_busy_without_header_or_sleep_uses_sixty_seconds():
    assert observed._error_result(RuntimeError("source busy"), var="F").error["retry_after_s"] == 60.0


def test_deadline_can_end_busy_query_before_three_attempts(getter_stub, monkeypatch):
    calls, answer = getter_stub
    sleeps = []
    monkeypatch.setattr(observed.time, "sleep", sleeps.append)
    monkeypatch.setattr(observed.time, "monotonic", lambda: 0.0)

    def fail(*args):
        raise HTTPError("HTTP 503 source busy")

    answer["fetch"] = fail
    result = observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 2), timeout=1.0)
    assert len(calls) == 1 and sleeps == []
    assert result.status == "error" and result.retryable is True and result.attempts == 1
    assert result.error["retry_after_s"] == 60.0


def test_timeout_exhaustion_reports_three_attempts(getter_stub, monkeypatch):
    calls, answer = getter_stub
    sleeps = []
    monkeypatch.setattr(observed.time, "sleep", sleeps.append)

    def fail(*args):
        raise TimeoutError("upstream timed out")

    answer["fetch"] = fail
    result = observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 2))
    assert len(calls) == 3 and sleeps == [10.0, 30.0]
    assert result.error["code"] == "NETWORK_TIMEOUT" and result.error["retry_after_s"] is None
    assert result.attempts == result.error["attempts"] == 3
    assert result.message == "TimeoutError: Gauge 412033 exceeded 90 s advisory timeout"


def test_inflight_order_and_completion_only_status_progress(getter_stub, monkeypatch):
    calls, answer = getter_stub
    events, completions, progress = [], [], []

    def fail(*args):
        raise RuntimeError("source busy")

    def sleep(delay):
        events.append((observed._FETCH_STATE.get().var, "sleep", delay))

    def inflight(gauge, var, result):
        assert gauge == "412033"
        events.append((var, result.status, result.attempts))
        if result.status == "retry_wait":
            assert result.error["code"] == "SERVICE_BUSY" and result.error["attempts"] == result.attempts
            assert result.error["retry_after_s"] == (10.0 if result.attempts == 1 else 30.0)
        else:
            assert result.status == "running" and result.error is None

    def completed(gauge, var, result):
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
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome.copy()

    def cached(gauge, start, end, var="F", **kwargs):
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
    calls, _ = getter_stub
    result = observed.fetch_gauge("412033", date(2000, 1, 2), date(2000, 1, 1))
    assert calls == [] and result.status == "error"
    assert result.message == "Start date must not follow end date" and result.retryable is False
    assert result.error["code"] == "INVALID_REQUEST" and result.attempts == result.error["attempts"] == 1


def test_ok_and_empty_results_have_no_envelope(getter_stub):
    _, answer = getter_stub
    ok = observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 2))
    assert (ok.status, ok.error, ok.attempts) == ("ok", None, 1)
    answer["fetch"] = lambda gauge, start, end, var: pd.DataFrame(columns=observed.RAW_COLUMNS)
    empty = observed.fetch_gauge("412034", date(2000, 1, 1), date(2000, 1, 2))
    assert (empty.status, empty.error) == ("empty", None)
