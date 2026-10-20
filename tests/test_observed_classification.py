"""Retry decisions at the gauge_pull boundary and in streamed/final job payloads."""

import errno
import json
import shutil
import socket
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from requests import Response
from requests.exceptions import ConnectionError, HTTPError, InvalidHeader, InvalidSchema, InvalidURL, MissingSchema, Timeout, URLRequired

import observed

START, END = date(2000, 1, 1), date(2000, 1, 2)


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (FileNotFoundError("~/bom_water/cache/stations.json"), False),
        (RuntimeError("~/bom_water/cache/stations.json is missing"), False),
        (RuntimeError("BoM station cache missing"), False),
        (FileNotFoundError("another local setup file"), False),
        (PermissionError("station list is unreadable"), False),
        (NotADirectoryError("invalid cache directory"), False),
        (IsADirectoryError("station list is a directory"), False),
        (OSError("local disk unavailable"), False),
        (ModuleNotFoundError("optional dependency missing"), False),
        (ImportError("dependency cannot load"), False),
        (ValueError("invalid configuration"), False),
        (TypeError("invalid configuration type"), False),
        (InvalidURL("invalid service URL"), False),
        (InvalidSchema("invalid service URL scheme"), False),
        (MissingSchema("service URL scheme missing"), False),
        (InvalidHeader("invalid service header"), False),
        (URLRequired("service URL missing"), False),
        (RuntimeError("too many requests in queue"), True),
        (RuntimeError("error_num 218"), True),
        (ValueError("too many requests in queue"), True),
        (ValueError('response: {"error_num": 218}'), True),
        (ValueError("source busy"), True),
        (TimeoutError("query timed out"), True),
        (Timeout("query timed out"), True),
        (ConnectionError("connection lost"), True),
        (socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution"), True),
        (OSError(errno.ENETUNREACH, "Network is unreachable"), True),
        (OSError("Network is unreachable"), True),
        (OSError(errno.EHOSTUNREACH, "No route to host"), True),
        (ConnectionResetError("connection reset"), True),
        (HTTPError("HTTP 400"), False),
        (HTTPError("HTTP 401"), False),
        (HTTPError("HTTP 403"), False),
        (HTTPError("HTTP 404"), False),
        (HTTPError("HTTP 408"), True),
        (HTTPError("HTTP 425"), True),
        (HTTPError("HTTP 429"), True),
        (HTTPError("HTTP 500"), True),
        (HTTPError("HTTP 503"), True),
        (json.JSONDecodeError("bad response JSON", "x", 0), True),
        (RuntimeError("unexpected failure"), True),
    ],
)
def test_fetch_failure_retryability(monkeypatch, tmp_path, error, retryable):
    calls = []

    def pull(*args, **kwargs):
        calls.append((args, kwargs))
        raise error

    monkeypatch.setattr("mdba_gauge_getter.gauge_getter.gauge_pull", pull)
    monkeypatch.setattr(observed, "CACHE_DIR", tmp_path)

    result = observed.fetch_gauge("412033", START, END, retries=0)

    assert result.status == "error"  # A failed query must never become an empty record.
    assert result.retryable is retryable
    if isinstance(error, TimeoutError):
        assert result.message.startswith("TimeoutError:") and "advisory timeout" in result.message
    else:
        assert result.message == f"{type(error).__name__}: {error}"
    assert result.frame.empty
    assert len(calls) == 1
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("code", [400, 401, 403, 404, 408, 425, 429, 500, 502, 503, 504, 599])
def test_http_response_status_overrides_message(code):
    response = Response()
    response.status_code = code
    # A 4xx Response is falsey, so checking `if exc.response` would lose the code.
    error = HTTPError("HTTP 503" if code < 500 else "HTTP 404", response=response)
    assert observed._error_result(error).retryable is (code in {408, 425, 429} or code >= 500)
    response.status_code = 503
    assert observed._error_result(HTTPError("~/bom_water/cache/stations.json missing", response=response)).retryable is False


@pytest.mark.parametrize(
    "error,retryable",
    [
        (ImportError("requests unavailable"), False),
        (TimeoutError("timeout"), True),
        (OSError("Network is unreachable"), True),
        (RuntimeError("error_num 218"), True),
        (RuntimeError("~/bom_water/cache/stations.json missing"), False),
    ],
)
def test_error_handler_works_without_requests(monkeypatch, tmp_path, error, retryable):
    monkeypatch.setitem(sys.modules, "requests.exceptions", None)
    result = observed._error_result(error)
    assert result.status == "error" and result.retryable is retryable
    assert result.message == f"{type(error).__name__}: {error}"
    monkeypatch.setattr(observed, "CACHE_DIR", tmp_path)
    streamed = []
    frames, results = observed.fetch_many(["412033"], START, END, status_progress=lambda *args: streamed.append(args))
    assert not frames and len(results) == len(streamed) == 2
    assert all(result.status == "error" and result.retryable is False for result in results.values())


def test_message_rules_map_to_codes_and_javascript_only_reads_codes():
    root = Path(__file__).resolve().parents[1]
    template = (root / "src/dashboard_template.html").read_text()
    cases = json.loads((root / "tests/fixtures/observed_message_cases.json").read_text())
    start, end = "// ---- observed result classification", "function observedPanel(){"
    assert template.count(start) == template.count(end) == 1
    helpers = template[template.index(start) : template.index(end)]
    for case in cases:  # Python is the only place that reads exception text
        result = observed._error_result(RuntimeError(case["message"]))
        assert result.retryable is case["retryable"] and result.error["code"] == case["code"]
        assert result.error["retryable"] is case["retryable"]
    for code in {case["code"] for case in cases} | {"CONFIGURATION_REQUIRED", "INVALID_REQUEST", "NETWORK_TIMEOUT"}:
        assert code in helpers  # every backend code has front-end copy
    node = shutil.which("node") or str(Path(sys.executable).with_name("node"))
    script = """
const fs=require('node:fs'),vm=require('node:vm');
const [helpers,cases]=JSON.parse(fs.readFileSync(0,'utf8')),ctx=vm.createContext({});
vm.runInContext(helpers,ctx);ctx.cases=cases;
process.stdout.write(JSON.stringify(vm.runInContext(`cases.map(c=>[
  describeResult('f',{status:'error',retryable:c.retryable,error:{code:c.code,retryable:c.retryable}}),
  describeResult('f',{status:'error',retryable:true,message:c.message})])`,ctx)));
"""
    result = subprocess.run([node, "-e", script], input=json.dumps([helpers, cases]), text=True, capture_output=True, check=True)
    for case, (by_code, legacy) in zip(cases, json.loads(result.stdout), strict=True):
        assert by_code["retryable"] is case["retryable"] and by_code["label"] == case["label"]
        assert legacy["label"] == "Unexpected problem"  # legacy message text is never parsed


def test_empty_records_and_invalid_requests_are_not_retryable(monkeypatch, tmp_path):
    calls = []

    def pull(*args, **kwargs):
        calls.append((args, kwargs))
        return pd.DataFrame()

    monkeypatch.setattr("mdba_gauge_getter.gauge_getter.gauge_pull", pull)
    monkeypatch.setattr(observed, "CACHE_DIR", tmp_path)

    for _ in range(2):  # Fresh empty result and the cached empty result agree.
        result = observed.fetch_gauge("412033", START, END)
        assert result.status == "empty"
        assert result.retryable is False
    assert len(calls) == 1
    for start, end, var, status in [(END, START, "F", "error"), (START, END, "X", "unsupported")]:
        result = observed.fetch_gauge("412033", start, end, var)
        assert result.status == status
        assert result.retryable is False
    assert len(calls) == 1


@pytest.mark.parametrize("error,retryable", [(FileNotFoundError("~/bom_water/cache/stations.json"), False), (TimeoutError("timeout"), True)])
def test_fetch_many_preserves_retryability_on_unhandled_worker_failures(monkeypatch, error, retryable):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(observed, "fetch_gauge", fail)
    streamed = []
    frames, results = observed.fetch_many(["412033"], START, END, status_progress=lambda *args: streamed.append(args))

    assert not frames
    assert len(streamed) == len(results) == 2
    for gauge, var, result in streamed:
        assert results[(gauge, var)] is result
        assert result.status == "error"
        assert result.retryable is retryable


@pytest.mark.parametrize("error,retryable", [(FileNotFoundError("~/bom_water/cache/stations.json"), False), (RuntimeError("error_num 218"), True)])
def test_retryability_survives_streamed_and_final_job_payloads(monkeypatch, tmp_path, error, retryable):
    import server

    monkeypatch.setattr(observed, "GAUGE_STAGE", "Renamed download stage")
    monkeypatch.setattr(observed.time, "sleep", lambda delay: None)

    def pull(gauges, start, end, *, var, **kwargs):
        if var == "L":
            raise error
        return pd.DataFrame({"DATASOURCEID": ["NSW"], "SITEID": gauges, "SUBJECTID": ["WATER"], "DATETIME": [start], "VALUE": [1.0], "QUALITYCODE": [140]})

    monkeypatch.setattr("mdba_gauge_getter.gauge_getter.gauge_pull", pull)
    monkeypatch.setattr(observed, "CACHE_DIR", tmp_path)
    table = pd.DataFrame({"Gauge": ["412033"], "Multigauge": [""], "WeirpoolGauge": [""]})
    monkeypatch.setattr(server, "parameter_sheet", lambda: table)
    job: dict[str, Any] = {"status": "running", "t0": time.time()}
    monkeypatch.setattr(server, "JOBS", {"classification-test": job})
    streamed = []

    def evaluate(*args, **kwargs):
        assert job["progress"]["stage"] == "Renamed download stage"
        assert job["progress"]["stage_code"] == "gauges"
        streamed.append(json.loads(json.dumps(job["progress"]["gauges"])))
        return [], {}

    monkeypatch.setattr(server, "run_scenario", evaluate)
    server.observed_worker("classification-test", "Test SDL", ["412033"], START, END, False, False)

    assert job["status"] == "done", job
    final = json.loads(json.dumps(job["stats"]["gauge_status"]))
    assert streamed == [final]
    assert final == job["gauge_status"]
    flow = dict(final["412033"]["f"])
    coverage = flow.pop("coverage")  # Phase 3: terminal ok/empty/error carry a coverage block (checked in test_observed.py)
    assert flow == {"status": "ok", "message": "", "retryable": False, "error": None, "attempts": 1, "max_attempts": 3, "schema_version": 2}
    assert coverage["coverage_status"] == "assessed" and coverage["policy_version"].startswith("bom-hrs-grades-ABCE-")  # the server passes the default policy
    assert final["412033"]["l"]["coverage"]["coverage_status"] == "unavailable_error"
    level = final["412033"]["l"]  # legacy keys are unchanged; `error` is the additive envelope
    assert {k: level[k] for k in ("status", "message", "retryable")} == {
        "status": "error",
        "message": f"{type(error).__name__}: {error}",
        "retryable": retryable,
    }
    assert level["error"]["retryable"] is retryable and level["error"]["measurement"] == "level" and level["max_attempts"] == 3


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("too many requests in queue"),
        ValueError("error_num 218"),
        HTTPError("HTTP 408"),
        HTTPError("HTTP 425"),
        Timeout("early timeout"),
        TimeoutError("early timeout"),
    ],
)
def test_transient_service_failures_use_existing_two_retry_budget(monkeypatch, tmp_path, error):
    calls: list[str] = []
    delays: list[float] = []

    def pull(*args, **kwargs):
        calls.append(kwargs["var"])
        raise error

    monkeypatch.setattr("mdba_gauge_getter.gauge_getter.gauge_pull", pull)
    monkeypatch.setattr(observed, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(observed.time, "sleep", delays.append)
    result = observed.fetch_gauge("412033", START, END)
    assert calls == ["F", "F", "F"]
    assert delays == [10.0, 30.0]
    assert result.status == "error" and result.retryable is True
    assert not list(tmp_path.iterdir())


def test_long_retry_after_is_not_waited_out_but_stays_retryable_for_the_user(monkeypatch, tmp_path):
    calls: list[str] = []
    delays: list[float] = []

    def pull(*args, **kwargs):
        calls.append(kwargs["var"])
        raise HTTPError("HTTP 429; retry after 500 seconds")

    monkeypatch.setattr("mdba_gauge_getter.gauge_getter.gauge_pull", pull)
    monkeypatch.setattr(observed, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(observed.time, "sleep", delays.append)
    result = observed.fetch_gauge("412033", START, END)
    assert calls == ["F"] and delays == []  # one attempt: the server asked for far longer than the per-gauge deadline
    assert result.status == "error" and result.retryable is True


def test_short_retry_after_is_honoured_over_the_default_backoff(monkeypatch, tmp_path):
    delays: list[float] = []

    def pull(gauges, start, end, *, var, **kwargs):
        if len(delays) < 1:
            raise HTTPError("HTTP 429; retry after 25 seconds")
        return pd.DataFrame({"DATASOURCEID": ["NSW"], "SITEID": gauges, "SUBJECTID": ["WATER"], "DATETIME": [start], "VALUE": [1.0], "QUALITYCODE": [140]})

    monkeypatch.setattr("mdba_gauge_getter.gauge_getter.gauge_pull", pull)
    monkeypatch.setattr(observed, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(observed.time, "sleep", delays.append)
    assert observed.fetch_gauge("412033", START, END).status == "ok"
    assert delays == [25.0]


def test_fetch_many_forwards_retry_budget_and_recovers_busy_service(monkeypatch, tmp_path):
    calls = {"F": 0, "L": 0}
    delays: list[float] = []

    def pull(*args, **kwargs):
        var = kwargs["var"]
        calls[var] += 1
        if calls[var] == 1:
            raise RuntimeError("source busy")
        return pd.DataFrame()

    monkeypatch.setattr("mdba_gauge_getter.gauge_getter.gauge_pull", pull)
    monkeypatch.setattr(observed, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(observed.time, "sleep", delays.append)
    _, results = observed.fetch_many(["412033"], START, END, retries=1)
    assert calls == {"F": 2, "L": 2}
    assert delays == [10.0, 10.0]
    assert all(result.status == "empty" and result.retryable is False for result in results.values())


def test_retry_wait_never_exceeds_advisory_deadline(monkeypatch, tmp_path):
    calls: list[dict[str, Any]] = []
    delays: list[float] = []

    def pull(*args, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("source busy")

    monkeypatch.setattr("mdba_gauge_getter.gauge_getter.gauge_pull", pull)
    monkeypatch.setattr(observed, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(observed.time, "sleep", delays.append)
    result = observed.fetch_gauge("412033", START, END, timeout=1)
    assert result.status == "error" and result.retryable is True
    assert len(calls) == 1 and delays == []


def test_non_gauge_progress_does_not_keep_the_fetch_stage_code():
    import server

    job: dict[str, Any] = {}
    server._progress_reporter(job, stage_code="gauges")("Any renamed gauge stage", 1, 2)
    server._progress_reporter(job)("Evaluating gauges", 0, 2)
    assert job["progress"] == {"stage": "Evaluating gauges", "done": 0, "total": 2}
