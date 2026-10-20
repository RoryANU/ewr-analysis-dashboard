"""Controlled fixtures for inventory, HTTP contracts, daily coverage and safe embedding."""

import http.client
import json
import threading
from unittest.mock import Mock

import pandas as pd
import pytest

from ewr_analysis import build_dashboard, server


@pytest.fixture
def parameters(monkeypatch):
    from py_ewr import data_inputs

    common = dict(
        Gauge="123",
        PlanningUnitName="Asset",
        State="NSW",
        Multigauge="",
        FlowLevelVolume="F",
        FlowThresholdMin="10",
        Duration="2",
        StartMonth="7",
        EndMonth="6",
        TargetFrequency="50",
        **{"MaxInter-event": "2"},
    )
    table = pd.DataFrame(
        [
            {**common, "Code": "F1"},
            {**common, "Code": "L1", "FlowLevelVolume": "L"},
            {**common, "Code": "V1", "FlowLevelVolume": "V"},
            {**common, "Code": "Unknown"},
            {**common, "Code": "Multi", "Multigauge": "456"},
        ]
    )
    config = Mock(return_value={"flow_handle": ["F1-single-F", "Multi-multigauge-F"], "level_handle": ["L1-single-L"], "volume_handle": ["V1-single-V"]})
    monkeypatch.setattr(server, "_EWR_CACHE", {})
    monkeypatch.setattr(server, "parameter_sheet", lambda: table)
    monkeypatch.setattr(data_inputs, "get_ewr_calc_config", config)
    return table, config


def test_inventory_support_and_specialised_rules(parameters):
    rows = {r["code"]: r for r in server.diagnostic_inventory("123")}
    assert rows["F1"]["detailed"]
    assert rows["L1"]["handler"] == "level_handle" and not rows["L1"]["detailed"]
    assert rows["V1"]["handler"] == "volume_handle" and not rows["V1"]["detailed"]
    assert rows["Unknown"]["handler"] == "unmapped" and not rows["Unknown"]["detailed"]
    assert not rows["Multi"]["detailed"]
    json.dumps(rows, allow_nan=False)


def test_cache_reuses_payload_and_handler_config(parameters):
    _, config = parameters
    a = server.diagnostic_payload("123")
    b = server.diagnostic_payload("123")
    assert a is b
    server.ewr_list("123")
    config.assert_called_once_with(None)


def test_unknown_gauge_is_empty_and_not_cached(parameters):
    assert server.diagnostic_payload("999") == {"ewrs": [], "inventory": []}
    assert "999" not in server._EWR_CACHE["diagnostics"]


@pytest.mark.parametrize("gauge", ["", "../123", "a" * 65, "12 3"])
def test_invalid_gauge_fails_explicitly(parameters, gauge):
    with pytest.raises(ValueError, match="station identifier"):
        server.diagnostic_payload(gauge)


def test_changed_parameter_schema_is_actionable(parameters):
    table, _ = parameters
    table.drop(columns=["FlowLevelVolume"], inplace=True)
    with pytest.raises(ValueError, match="missing required columns: FlowLevelVolume"):
        server.diagnostic_inventory("123")


def test_daily_coverage_counts_absent_dates_and_nulls():
    flow = pd.Series([1.0, float("nan")], index=pd.to_datetime(["2000-01-01", "2000-01-03"]))
    assert server.record_coverage(flow, [1, 0]) == {"start": "2000-01-01", "end": "2000-01-03", "missing_days": 2, "complete_years": 1, "years": 2}
    assert server.record_coverage(flow.iloc[:0], []) == {"start": None, "end": None, "missing_days": 0, "complete_years": 0, "years": 0}


@pytest.fixture
def annual_fixture(monkeypatch):
    # Exercise the real annual response assembly without evaluating py_ewr or loading its datasets.
    flow = pd.Series(10.0, index=pd.date_range("2000-07-01", "2001-06-30"))
    flow = flow.drop(pd.Timestamp("2000-08-02"))  # absent, not already a NaN
    ev = dict(
        years=pd.DataFrame({"eventYears": [1]}, index=[2000]),
        flow=flow,
        ystart=7,
        sm=7,
        em=6,
        succ=pd.DataFrame(),
        events={},
        pu="Asset",
        thr=10,
        thr_max=None,
        dur=2,
        min_spell=2,
        per_year=1,
        gap_tol=0,
        target=50,
        mrip_days=730,
    )
    monkeypatch.setattr(server, "ewr_eval", lambda *args: ev)
    monkeypatch.setattr(server, "ewr_gaps", lambda ev: ([], 0.0))


@pytest.fixture
def endpoint():
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    def get(path):
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1])
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    yield get
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def test_real_inventory_http_contract(parameters, endpoint):
    status, payload = endpoint("/api/ewrs?gauge=123")
    assert status == 200
    assert len(payload["ewrs"]) == 1 and len(payload["inventory"]) == 5
    assert set(payload["inventory"][0]) == {"code", "pu", "kind", "handler", "detailed"}
    assert endpoint("/api/ewrs?gauge=999") == (200, {"ewrs": [], "inventory": []})
    status, payload = endpoint("/api/ewrs?gauge=../")
    assert status == 400 and "station identifier" in payload["error"]


def test_real_annual_http_coverage(annual_fixture, endpoint):
    status, payload = endpoint("/api/ewryears?gauge=123&ewr=F1&base=test")
    assert status == 200
    scenario = payload["scenarios"]["base"]
    assert scenario["coverage"] == {"start": "2000-07-01", "end": "2001-06-30", "missing_days": 1, "complete_years": 0, "years": 1}
    assert scenario["years"]["complete"] == [0]
    json.dumps(payload, allow_nan=False)


def test_embedded_json_cannot_close_script():
    value = '</script><script>alert("x")</script><!-- & >'
    rendered = build_dashboard.render_html("const x=__X__;", {"__X__": value})
    assert "</script>" not in rendered and "<!--" not in rendered
    assert json.loads(rendered[len("const x=") : -1]) == value
