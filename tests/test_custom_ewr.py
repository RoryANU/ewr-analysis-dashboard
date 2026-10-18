"""Phase 2: validation, real py_ewr injection/equivalence, registry lifecycle and HTTP contracts."""

import copy
import http.client
import json
import os
import threading
import time
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest
from py_ewr import data_inputs

import custom_ewr as c
import server

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/example_scenarios/dummy_broken_scenario_A_level_salinity.csv"
G, SECOND = "404216", "404243"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "STORE", tmp_path)
    monkeypatch.setattr(server, "FLOWS", {})
    monkeypatch.setattr(server, "_EVAL_CACHE", {})
    return tmp_path


@pytest.fixture
def spec(store):
    pu = c.base_sheet_raw().loc[lambda d: d.Gauge == G, "PlanningUnitName"].iloc[0]
    return {
        "code": "U_FRESH1",
        "name": "Spring fresh",
        "kind": "F",
        "handler": "flow_handle",
        "gauge": G,
        "pus": [pu],
        "objectives": [],
        "regime": None,
        "params": {"FlowThresholdMin": 200, "Duration": 5, "TargetFrequency": 50, "MaxInter-event": 2},
    }


@pytest.fixture
def series(store):
    flow, level, salinity = server.load_frames("Standard time-series", str(FIXTURE))
    # Two whole water years suffice to exercise every handler; full-length equivalence is tested separately below.
    server._store_series("a", {"flow": flow.iloc[:731], "level": level.iloc[:731], "salinity": salinity.iloc[:731]}, [G, SECOND], False)
    return store


def test_schema_uses_pyewr_components_and_has_all_offered_handlers():
    for kind, handlers in c.HANDLERS.items():
        for handler, category in handlers.items():
            schema = c.schema()[kind][handler]
            assert schema["category"] == category
            assert set(schema["fields"]) == set(data_inputs.get_EWR_components(category)) - {"FlowLevelVolume", "Multigauge"} | set(c.TARGETS)
            assert set(schema["labels"]) == set(schema["fields"])
            assert schema["defaults"]["MinSpell"] == (1 if handler.startswith("lowflow_handle") else "Duration")


@pytest.mark.parametrize("handler,expected", [("flow_handle", 5), ("lowflow_handle", 1), ("ctf_handle", 5)])
@pytest.mark.parametrize("value", [None, "", "missing"])
def test_minspell_is_always_filled(spec, handler, expected, value):
    spec["handler"] = handler
    if value != "missing":
        spec["params"]["MinSpell"] = value
    validated = c.validate_spec(spec)
    assert validated["params"]["MinSpell"] == expected
    rows = c.sheet_rows(validated, c.base_sheet_raw())
    assert rows.MinSpell.iloc[0] == str(expected)
    cleaned = c.read_sheet(str(c.merged_sheet_path([validated])))
    assert cleaned.loc[cleaned.Code == spec["code"], "MinSpell"].iloc[0] == expected


@pytest.mark.parametrize(
    "field,value",
    [
        ("code", "U_bad-code"),
        ("code", "BF1"),
        ("code", "U_"),
        ("gauge", "999999"),
        ("pus", ["wrong"]),
        ("objectives", ["wrong"]),
        ("regime", "wrong"),
        ("name", "x" * 81),
        ("name", ""),
        ("handler", "barrage_handle"),
        ("kind", "X"),
        ("second_gauge", SECOND),
        ("unknown", True),
        ("id", "../../registry"),
    ],
)
def test_invalid_top_level_fields_have_field_errors(spec, field, value):
    spec[field] = value
    with pytest.raises(c.ValidationError) as exc:
        c.validate_spec(spec)
    assert field in exc.value.errors


@pytest.mark.parametrize(
    "field,value",
    [
        ("MinSpell", 0),
        ("MinSpell", -1),
        ("MinSpell", 6),
        ("MinSpell", 1.5),
        ("MinSpell", "nan"),
        ("MinSpell", True),
        ("Duration", 0),
        ("Duration", 367),
        ("Duration", 2.5),
        ("Duration", None),
        ("Duration", "inf"),
        ("EventsPerYear", 0),
        ("EventsPerYear", 13),
        ("WithinEventGapTolerance", 31),
        ("MaxInter-event", -1),
        ("TargetFrequency", -1),
        ("TargetFrequency", 101),
        ("TargetFrequency", None),
        ("FlowThresholdMin", -1),
        ("FlowThresholdMax", 100),
        ("StartMonth", 13),
        ("EndMonth", 0),
        ("StartDay", 32),
        ("VolumeThreshold", 1),
    ],
)
def test_invalid_parameters_have_field_errors(spec, field, value):
    spec["params"][field] = value
    with pytest.raises(c.ValidationError) as exc:
        c.validate_spec(spec)
    assert f"params.{field}" in exc.value.errors


@pytest.mark.parametrize(
    "field", ["MaxSpell", "NonFlowSpell", "FlowThresholdMin", "FlowThresholdMax", "VolumeThreshold", "CtfThreshold", "DrawdownRate", "MaxInter-event"]
)
def test_open_ended_bounds_are_capped_for_pyewr(field):
    assert c.BOUNDS[field][1] == c.MAX_PARAM
    assert all(entry["max"] is not None for kinds in c.schema().values() for h in kinds.values() for f, entry in h["bounds"].items() if f == field)


def test_huge_threshold_is_rejected_before_pyewr_cannot_read_the_sheet(spec):
    # 1e20 used to validate, then py_ewr raised "cannot safely cast non-equivalent float64 to int64" on the merged sheet.
    spec["params"]["FlowThresholdMax"] = 10**20
    with pytest.raises(c.ValidationError) as exc:
        c.validate_spec(spec)
    assert "params.FlowThresholdMax" in exc.value.errors
    spec["params"]["FlowThresholdMax"] = c.MAX_PARAM
    validated = c.validate_spec(spec)
    cleaned = c.read_sheet(str(c.merged_sheet_path([validated])))
    assert cleaned.loc[cleaned.Code == spec["code"], "FlowThresholdMax"].iloc[0] == c.MAX_PARAM


def test_window_dates_and_day_encoding(spec):
    spec["params"].update(StartMonth=9, StartDay=1, EndMonth=9, EndDay=10)
    path = c.merged_sheet_path([spec])
    raw = pd.read_csv(path, dtype=str, encoding="cp1252").fillna("")
    assert raw.loc[raw.Code == spec["code"], "StartMonth"].iloc[0] == "9.01"
    assert raw.loc[raw.Code == spec["code"], "EndMonth"].iloc[0] == "9.10"
    row = c.read_sheet(str(path)).loc[lambda d: d.Code == spec["code"]].iloc[0]
    assert (row.StartMonth, row.StartDay, row.EndMonth, row.EndDay) == (9, 1, 9, 10)
    spec["params"]["EndDay"] = 3
    with pytest.raises(c.ValidationError, match="longer than"):
        c.validate_spec(spec)
    spec["params"].update(StartMonth=2, StartDay=30)
    with pytest.raises(c.ValidationError, match="Invalid day"):
        c.validate_spec(spec)


def test_anytime_requires_whole_year_and_targets_are_ordered(spec):
    spec["handler"] = "flow_handle_anytime"
    spec["params"]["StartMonth"] = 8
    with pytest.raises(c.ValidationError, match="whole-year"):
        c.validate_spec(spec)
    spec["params"]["StartMonth"] = 7
    spec["params"]["TargetFrequencyMin"] = 60
    with pytest.raises(c.ValidationError, match="acceptable frequency"):
        c.validate_spec(spec)


def test_multigauge_validation(spec, series):
    spec["handler"] = "flow_handle_multi"
    for second in (None, G, "999999"):
        spec["second_gauge"] = second
        with pytest.raises(c.ValidationError) as exc:
            c.validate_spec(spec)
        assert "second_gauge" in exc.value.errors
    spec["second_gauge"] = SECOND
    assert c.validate_spec(spec)["second_gauge"] == SECOND


def test_level_and_volume_bounds(spec):
    spec.update(kind="L", handler="level_handle")
    spec["params"] = {"Duration": 5, "TargetFrequency": 50, "LevelThresholdMin": 2, "LevelThresholdMax": 1, "MaxSpell": 4}
    with pytest.raises(c.ValidationError) as exc:
        c.validate_spec(spec)
    assert {"params.LevelThresholdMax", "params.MaxSpell"} <= exc.value.errors.keys()
    spec.update(kind="V", handler="cumulative_handle")
    spec["params"] = {"Duration": 5, "TargetFrequency": 50, "AccumulationPeriod": 4, "VolumeThreshold": 0}
    with pytest.raises(c.ValidationError) as exc:
        c.validate_spec(spec)
    assert {"params.AccumulationPeriod", "params.VolumeThreshold"} <= exc.value.errors.keys()


def test_user_specifies_salinity_units_and_conversion(spec):
    spec.update(kind="S", salinity={"unit": "TDS", "tds_factor": 2})
    spec["params"].update(FlowThresholdMin=0, FlowThresholdMax=400)
    clean = c.validate_spec(spec)
    assert clean["salinity"] == {"unit": "EC", "tds_factor": 2}
    assert clean["params"]["FlowThresholdMax"] == 800
    assert c.validate_spec(clean)["params"]["FlowThresholdMax"] == 800  # no double conversion when saving/re-reading
    spec["salinity"] = {"unit": "guess"}
    with pytest.raises(c.ValidationError, match="Specify EC or TDS"):
        c.validate_spec(spec)
    spec["salinity"] = {"unit": "EC", "tds_factor": float("nan")}
    with pytest.raises(c.ValidationError, match="finite and positive"):
        c.validate_spec(spec)


def test_sheet_is_content_addressed_and_inherits_only_location_metadata(spec):
    raw = c.base_sheet_raw()
    before = raw.copy(deep=True)
    rows = c.sheet_rows(spec, raw)
    base = raw[(raw.Gauge == G) & (raw.PlanningUnitName == spec["pus"][0])].iloc[0]
    assert all(rows.iloc[0][col] == base[col] for col in c.HIDDEN)
    assert rows.WeirpoolGauge.iloc[0] == rows.RateOfRiseMax1.iloc[0] == ""
    first = c.merged_sheet_path([spec])
    assert first == c.merged_sheet_path([copy.deepcopy(spec)])
    assert len(first.stem) == 16
    spec["params"]["Duration"] = 6
    assert first != c.merged_sheet_path([spec])
    pd.testing.assert_frame_equal(raw, before)
    original = data_inputs.get_ewr_calc_config(None)
    snapshot = copy.deepcopy(original)
    cfg = c.calc_config([spec])
    assert f"{spec['code']}-single-F" in cfg["flow_handle"]
    assert original == snapshot


def test_registry_atomic_versioning_uniqueness_and_deletion(spec, store, monkeypatch):
    replace = Mock(wraps=os.replace)
    monkeypatch.setattr(c.os, "replace", replace)
    saved = c.save_spec(spec)
    assert saved["ver"] == 1 and c.find(saved["code"], G) == saved
    path = c.merged_sheet_path([saved])
    with pytest.raises(c.ValidationError, match="already in use"):
        c.save_spec(spec)
    with pytest.raises(c.ValidationError, match="already in use"):
        raw = c.base_sheet_raw()
        raw.loc[0, "Code"] = spec["code"]
        c.validate_spec(spec, raw)
    server._EVAL_CACHE[("a", G, saved["code"], "PU", 1)] = {}
    saved["params"]["Duration"] = 6
    edited = c.save_spec(saved)
    assert edited["ver"] == 2 and edited["created"] == saved["created"] and not server._EVAL_CACHE
    assert path != c.merged_sheet_path([edited])
    server._EVAL_CACHE[("a", G, edited["code"], "PU", 2)] = {}
    c.delete_spec(saved["id"])
    assert c.load_registry() == {} and not server._EVAL_CACHE and not list((store / "uewr_sheets").glob("*.csv"))
    assert replace.call_count >= 5 and not list(store.rglob("*.tmp"))


def test_startup_sweep_removes_only_old_sheets(spec):
    old = c.merged_sheet_path([spec])
    spec["params"]["Duration"] = 6
    recent = c.merged_sheet_path([spec])
    os.utime(old, (0, 0))
    c.sweep_sheets()
    assert not old.exists() and recent.exists()


def test_frames_preserve_gaps_and_select_the_right_series(spec, series):
    spec.update(kind="S", salinity={"unit": "EC"})
    path = series / "a_salinity.csv"
    data = pd.read_csv(path, index_col=0, parse_dates=True).drop(pd.Timestamp("1995-08-01"))
    data.to_csv(path)
    flow, level = c.frames("a", spec)
    assert level.shape[1] == 0 and pd.isna(flow.loc["1995-08-01", G]) and flow.index.name == "Date"
    spec["kind"] = "L"
    flow, level = c.frames("a", spec)
    assert flow.shape[1] == 0 and level[G].max() < 3
    with pytest.raises(c.SkipScenario, match="no level series"):
        c.frames("absent", spec)
    with pytest.raises(c.ValidationError, match="scenario id"):
        c.frames("../a", spec)


OFFERED = [(kind, handler) for kind, handlers in c.HANDLERS.items() for handler in handlers]


@pytest.mark.slow
@pytest.mark.parametrize("kind,handler", OFFERED)
def test_every_offered_handler_runs_through_real_summaries(spec, series, kind, handler):
    spec.update(kind=kind, handler=handler)
    if handler.endswith("_multi"):
        spec["second_gauge"] = SECOND
    if kind == "V":
        spec["params"].update(VolumeThreshold=1000, AccumulationPeriod=10)
    if kind == "L":
        spec["params"].pop("FlowThresholdMin")
        if handler == "level_handle":
            spec["params"].update(LevelThresholdMin=1, LevelThresholdMax=2, DrawdownRate=0.05, MaxSpell=366)
    if kind == "S":
        spec["salinity"] = {"unit": "EC"}
        spec["params"].update(FlowThresholdMin=0, FlowThresholdMax=800)
    if handler == "rise_and_fall_handle":
        spec["params"].update(
            {"RateOfRiseRiverLevel": 0.001}
            if kind == "L"
            else {
                "RateOfRiseMax1": 1.1,
                "RateOfRiseMax2": 1.2,
                "RateOfRiseThreshold1": 0,
                "RateOfRiseThreshold2": 1000,
            }
        )
    rows = c.evaluate(spec, "a")
    assert len(rows) == 1 and rows[0]["ewr"] == spec["code"]
    assert rows[0]["fr"] is not None and rows[0]["win"] is not None
    json.dumps(server._json_safe(rows), allow_nan=False)


@pytest.mark.slow
@pytest.mark.parametrize("gauge,code", [(G, "F1_b"), (G, "BF2_S"), ("405201", "F1"), ("412033", "CF1_b"), ("418078", "OB1_S")])
def test_custom_code_matches_builtin_results(spec, store, monkeypatch, gauge, code):
    table = server.parameter_sheet()
    selected = table[(table.Gauge == gauge) & (table.Code == code)].iloc[:1].copy()
    row = selected.iloc[0]
    handler = server.diagnostic_handlers()[f"{code}-single-{row.FlowLevelVolume}"]
    index = pd.date_range("1995-07-01", "2020-06-30", name="Date")
    # A deterministic wet/dry seasonal series exercises successes and gaps without relying on the copied custom parameters.
    flows = np.where(np.arange(len(index)) % 365 < 180, 5000, 0)
    raw = pd.DataFrame({f"{gauge}_flow": flows}, index=index).to_csv().encode()
    monkeypatch.setattr(server, "parameter_sheet", lambda: selected)
    builtin, _ = server.run_scenario(raw, [gauge], "a")
    kind = row.FlowLevelVolume
    fields = c.schema()[kind][handler]["fields"]
    params = {key: None if pd.isna(row[key]) or row[key] == "" else row[key] for key in fields if key in row}
    # Upstream volume rows use 0 for these unused spell fields. User specs require >=1; every actual event has at least one day.
    for field in ("Duration", "MinSpell"):
        if params.get(field) == 0:
            params[field] = 1
    spec.update(gauge=gauge, pus=[row.PlanningUnitName], kind=kind, handler=handler, params=params)
    custom = c.evaluate(spec, "a")
    assert custom == [{**original, "ewr": spec["code"]} for original in builtin]


@pytest.mark.slow
def test_salinity_exceedance_cap_and_chart_units(spec, store):
    index = pd.date_range("2000-07-01", "2002-06-30", name="Date")
    values = np.full(len(index), 600.0)
    values[:10] = 1000
    values[365:385] = 1000
    pd.DataFrame({G: values}, index=index).to_csv(store / "a_salinity.csv")
    spec.update(kind="S", handler="lowflow_handle", salinity={"unit": "EC"})
    spec["params"].update(Duration=350, FlowThresholdMin=0, FlowThresholdMax=800)
    saved = c.save_spec(spec)
    rows = c.evaluate(saved, "a")
    assert rows[0]["ey"] == 1 and rows[0]["fr"] == 1
    chart = server.ewr_years({"base": "a", "gauge": G, "ewr": saved["code"]})
    assert chart["kind"] == "S" and chart["unit"] == "EC"
    assert all(event["vol"] is None for event in chart["scenarios"]["base"]["events"])
    assert chart["scenarios"]["base"]["years"]["achieved"] == [1, 0]


@pytest.mark.slow
def test_level_chart_and_versioned_cache(spec, series):
    spec.update(
        kind="L",
        handler="level_handle",
        params={"Duration": 5, "MinSpell": 5, "TargetFrequency": 50, "LevelThresholdMin": 1, "LevelThresholdMax": 2, "DrawdownRate": 0.05},
    )
    saved = c.save_spec(spec)
    first = server.ewr_eval("a", G, saved["code"])
    assert server.ewr_eval("a", G, saved["code"]) is first
    assert list(server._EVAL_CACHE)[0][-1] == 1
    chart = server.ewr_years({"base": "a", "gauge": G, "ewr": saved["code"]})
    assert chart["kind"] == "L" and chart["unit"] == "m"
    assert all(e["vol"] is None and e["peak"] < 3 for e in chart["scenarios"]["base"]["events"])
    saved["params"]["LevelThresholdMin"] = 1.8
    saved = c.save_spec(saved)
    assert not server._EVAL_CACHE
    assert server.ewr_eval("a", G, saved["code"]) is not first
    assert list(server._EVAL_CACHE)[0][-1] == 2
    assert server.interevents({"scn": "a", "gauge": G, "ewr": saved["code"]})["n_gaps"] >= 0


def test_inventory_merges_outside_builtin_cache(spec, series):
    before = server.diagnostic_payload(G)
    saved = c.save_spec(spec)
    after = server.diagnostic_payload(G)
    assert any(e["code"] == saved["code"] and e["custom"] for e in after["ewrs"])
    assert all(not e.get("custom") for e in before["ewrs"])
    assert all(not e.get("custom") for e in server._EWR_CACHE["diagnostics"][G]["ewrs"])
    c.delete_spec(saved["id"])
    assert server.diagnostic_payload(G) == before


@pytest.mark.parametrize("parallel", [False, True])
def test_jobs_collect_failures_and_fallback_without_aborting_other_pairs(spec, series, monkeypatch, parallel):
    first = c.save_spec(spec)
    spec.update(code="U_OTHER", kind="S", salinity={"unit": "EC"})
    second = c.save_spec(spec)
    # Missing salinity is a skip, not a job failure.
    (series / "a_salinity.csv").unlink()
    monkeypatch.setattr(c.os, "cpu_count", lambda: 2 if parallel else 1)
    if parallel:
        monkeypatch.setattr(c, "ProcessPoolExecutor", Mock(side_effect=BrokenProcessPool("unavailable")))
    monkeypatch.setattr(
        c, "evaluate", lambda spec, scn: (_ for _ in ()).throw(c.SkipScenario("no salinity")) if spec["kind"] == "S" else [{"ewr": spec["code"]}]
    )
    progress = []
    out = c.run_job([first["id"], second["id"]], ["a"], lambda *p: progress.append(p))
    assert out["results"]["a"] == [{"ewr": first["code"], "uewr_id": first["id"], "uewr_ver": 1}]
    assert out["skipped"] == {"a": {second["code"]: "no salinity"}} and out["errors"] == {}
    assert progress[-1] == ("Summarising", 2, 2)
    monkeypatch.setattr(c, "evaluate", Mock(side_effect=RuntimeError("broken")))
    out = c.run_job([first["id"]], ["a"], lambda *p: None)
    assert out["errors"] == {"a": {first["code"]: "RuntimeError: broken"}}


@pytest.mark.slow
def test_real_process_pool_job(spec, series, monkeypatch):
    executor = Mock(wraps=c.ProcessPoolExecutor)
    monkeypatch.setattr(c, "ProcessPoolExecutor", executor)
    monkeypatch.setattr(c.os, "cpu_count", lambda: 2)
    first = c.save_spec(spec)
    spec["code"] = "U_OTHER"
    second = c.save_spec(spec)
    out = c.run_job([first["id"], second["id"]], ["a"], lambda *p: None)
    assert executor.call_count == 1
    assert executor.call_args.args == (2,)
    assert executor.call_args.kwargs["mp_context"].get_start_method() == "spawn"
    assert out["errors"] == out["skipped"] == {}
    assert {r["ewr"] for r in out["results"]["a"]} == {first["code"], second["code"]}


@pytest.fixture
def endpoint():
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    def request(method, path, data=None, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1])
        try:
            conn.request(method, path, body=body if body is not None else json.dumps(data).encode() if data is not None else None, headers=headers or {})
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    yield request, httpd
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def test_http_origin_auth_and_json_validation(spec, endpoint):
    request, httpd = endpoint
    assert request("POST", "/api/uewr", spec, {"Origin": "https://evil.example"})[0] == 403
    assert request("GET", "/api/uewr") == (200, {"specs": []})
    assert request("GET", "/api/uewr/schema")[1] == c.schema()
    for body in (b"{", b"[]", b"NaN", b"x" * (64 * 1024 + 1)):
        status, payload = request("POST", "/api/uewr", body=body)
        assert status == 400 and "errors" in payload
    invalid = copy.deepcopy(spec)
    invalid["params"]["MinSpell"] = 0
    status, payload = request("POST", "/api/uewr", invalid)
    assert status == 400 and "params.MinSpell" in payload["errors"]
    httpd.token = "test-token"
    assert request("POST", "/api/uewr", spec)[0] == 401
    assert request("POST", "/api/uewr", spec, {"X-Token": "test-token"})[0] == 200


def test_http_errors_always_return_json(spec, endpoint, monkeypatch):
    request, _ = endpoint
    nested = ('{"a":' + "[" * 30000 + "]" * 30000 + "}").encode()
    assert len(nested) <= 64 * 1024
    status, payload = request("POST", "/api/uewr", body=nested)
    assert status == 400 and "request" in payload["errors"]

    def crash(*args):
        raise TypeError("cannot safely cast non-equivalent float64 to int64")

    monkeypatch.setattr(c, "evaluate", crash)
    status, payload = request("POST", "/api/uewr/preview", {"spec": spec, "scn": "a"})
    assert status == 500 and payload["errors"]["request"].startswith("TypeError: cannot safely cast")


@pytest.mark.slow
def test_http_save_preview_run_poll_and_delete(spec, series, endpoint):
    request, _ = endpoint
    status, preview = request("POST", "/api/uewr/preview", {"spec": spec, "scn": "a"})
    assert status == 200 and preview["row"]["ewr"] == spec["code"] and c.load_registry() == {}
    status, payload = request("POST", "/api/uewr", spec)
    assert status == 200
    saved = payload["spec"]
    assert request("GET", "/api/uewr")[1] == {"specs": [saved]}
    assert request("POST", "/api/uewr/run", {"ids": [saved["id"]], "scns": ["demo"]})[0] == 400
    status, payload = request("POST", "/api/uewr/run", {"ids": [saved["id"]], "scns": ["a"]})
    assert status == 200
    job_id = payload["job"]
    try:
        for _ in range(1000):
            status, job = request("GET", "/api/job?id=" + job_id)
            if job["status"] != "running":
                break
            time.sleep(0.01)
        assert status == 200 and job["status"] == "done" and job["results"]["a"][0]["uewr_ver"] == 1
        assert job["errors"] == {} and job["progress"]["stage"] == "Summarising"
        assert request("GET", f"/api/ewryears?base=a&gauge={G}&ewr={saved['code']}")[0] == 200
        assert request("POST", "/api/uewr/delete?id=" + saved["id"])[0] == 200
        assert request("GET", "/api/uewr")[1] == {"specs": []}
    finally:
        server.JOBS.pop(job_id, None)


def test_missing_multigauge_flows_are_rejected_before_save(spec):
    spec.update(handler="flow_handle_multi", second_gauge=SECOND)
    with pytest.raises(c.ValidationError, match="Both gauges need stored flows"):
        c.save_spec(spec)


def test_atomic_failure_preserves_registry_and_removes_temporary_file(spec, store, monkeypatch):
    saved = c.save_spec(spec)
    before = (store / "uewr.json").read_bytes()
    saved["name"] = "Changed name"
    monkeypatch.setattr(c.os, "replace", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        c.save_spec(saved)
    assert (store / "uewr.json").read_bytes() == before and not list(store.glob("*.tmp"))


def test_delete_keeps_other_definitions_sheets(spec):
    first = c.save_spec(spec)
    c.merged_sheet_path([first])
    spec["code"] = "U_OTHER"
    second = c.save_spec(spec)
    keep = c.merged_sheet_path([second])
    c.delete_spec(first["id"])
    assert keep.exists() and set(c.load_registry()) == {second["id"]}
    assert list(keep.parent.glob("*.csv")) == [keep]


def test_jobs_do_not_publish_results_after_edit_or_delete(spec, series, monkeypatch):
    saved = c.save_spec(spec)

    def delete_during_evaluation(*args):
        c.delete_spec(saved["id"])
        return [{"ewr": saved["code"]}]

    monkeypatch.setattr(c, "evaluate", delete_during_evaluation)
    out = c.run_job([saved["id"]], ["a"], lambda *p: None)
    assert out["results"]["a"] == []
    assert "definition changed" in out["skipped"]["a"][saved["code"]]


@pytest.mark.slow
@pytest.mark.parametrize("kind,field", [("F", "RateOfFallMin"), ("L", "RateOfFallRiverLevel")])
def test_rate_fall_works_with_arbitrary_public_codes(spec, series, kind, field):
    spec.update(kind=kind, handler="rise_and_fall_handle")
    spec["params"][field] = 0.9 if kind == "F" else 0.001
    assert c.evaluate(spec, "a")[0]["ewr"] == spec["code"]


@pytest.mark.slow
def test_one_custom_definition_summarises_all_selected_planning_units(spec, series):
    gauge = "418078"
    raw = c.base_sheet_raw()
    pus = raw.loc[raw.Gauge == gauge, "PlanningUnitName"].unique().tolist()
    spec.update(gauge=gauge, pus=pus, handler="lowflow_handle")
    frame = server.load_flows("a")[[G]].rename(columns={G: gauge})
    frame.to_csv(series / "multi_pu.csv")
    rows = c.evaluate(spec, "multi_pu")
    assert len(rows) == len(pus) > 1 and {r["pu"] for r in rows} == set(pus)


@pytest.mark.slow
def test_short_events_are_not_mistaken_for_successful_events(spec, series):
    # A real calc result with an event shorter than MinSpell used to make py_ewr raise KeyError('Gauge') in its summary joins.
    spec["params"].update(MinSpell=5, Duration=5)
    saved = c.save_spec(spec)
    index = pd.date_range("2000-07-01", "2001-06-30", name="Date")
    flow = pd.Series(0.0, index=index)
    flow.iloc[10:12] = 500
    flow.to_frame(G).to_csv(series / "short.csv")
    ev = server.ewr_eval("short", G, saved["code"])
    assert ev["succ"].empty
    assert server.ewr_years({"base": "short", "gauge": G, "ewr": saved["code"]})["scenarios"]["base"]["years"]["achieved"] == [0]


@pytest.mark.slow
@pytest.mark.parametrize("day_field,month,expected", [("StartDay", "StartMonth", (15, 30)), ("EndDay", "EndMonth", (1, 15))])
def test_one_optional_day_is_honoured_by_pyewr(spec, series, day_field, month, expected):
    spec["params"].update(StartMonth=9, EndMonth=9, FlowThresholdMin=0)
    spec["params"][day_field] = 15
    path = c.merged_sheet_path([spec])
    params = c.read_sheet(str(path)).loc[lambda d: d.Code == spec["code"]].iloc[0]
    assert (params.StartDay, params.EndDay) == expected
    saved = c.save_spec(spec)
    ev = server.ewr_eval("a", G, saved["code"])
    assert (ev["sd"], ev["ed"]) == expected
    for events in ev["events"].values():
        for event in events:
            assert all(expected[0] <= entry[0].day <= expected[1] for entry in event)


def test_day_window_completeness_and_volume_exclude_outside_days():
    index = pd.date_range("2000-07-01", "2001-06-30")
    series = pd.Series(1.0, index=index)
    series.loc["2000-09-01"] = np.nan
    series.loc["2000-09-02"] = 10000
    assert server.complete_years(series, [2000], 7, 9, 9, 15, 30) == [1]
    assert server.complete_years(series, [2000], 7, 9, 9) == [0]
    assert server.vr_years(series, [2000], 7, 9, 9, 2, 10, 15, 30) == [0.2]
    assert server.vr_years(series, [2000], 7, 9, 9, 2, 10) == [2.0]


_SHARED_VALIDATION = json.loads((ROOT / "tests/fixtures/uewr_validation_cases.json").read_text())


@pytest.mark.parametrize("case", _SHARED_VALIDATION["cases"], ids=lambda case: case["name"])
def test_shared_frontend_backend_validation_cases(store, case):
    base = copy.deepcopy(_SHARED_VALIDATION["base"])
    patch = case["patch"]
    base.update({key: value for key, value in patch.items() if key != "params"})
    base["params"].update(patch.get("params", {}))
    try:
        result = c.validate_spec(base)
    except c.ValidationError as exc:
        assert sorted(exc.errors) == sorted(case["errors"])
    else:
        assert not case["errors"]
        assert 1 <= result["params"]["MinSpell"] <= result["params"]["Duration"]


def test_backend_accepts_dashboard_regime_labels_from_etp_only_rows(spec):
    for regime in ("Cease to flow", "Unmapped"):
        spec["regime"] = regime
        assert c.validate_spec(spec)["regime"] == regime


def test_diagnostics_cannot_repopulate_cache_after_edit_or_delete(spec):
    saved = c.save_spec(spec)
    key = ("a", G, saved["code"], spec["pus"][0], saved["ver"])
    assert c.cache_evaluation(saved, key, {"years": []})
    edited = c.save_spec(saved)
    assert not c.cache_evaluation(saved, key, {"years": ["obsolete"]})
    c.delete_spec(edited["id"])
    assert not c.cache_evaluation(edited, (*key[:-1], edited["ver"]), {})
    assert not server._EVAL_CACHE


def test_delete_purges_completed_job_results_and_preserves_other_codes(spec, monkeypatch):
    saved = c.save_spec(spec)
    jobs: dict[str, Any] = {
        "old": {"results": {"a": [{"ewr": saved["code"]}, {"ewr": "U_OTHER"}]}, "errors": {"a": {saved["code"]: "old error", "U_OTHER": "keep"}}}
    }
    monkeypatch.setattr(server, "JOBS", jobs)
    c.delete_spec(saved["id"])
    assert jobs["old"]["results"]["a"] == [{"ewr": "U_OTHER"}]
    assert jobs["old"]["errors"]["a"] == {"U_OTHER": "keep"}


def test_publication_after_delete_discards_results_and_removes_recreated_sheets(spec):
    saved = c.save_spec(spec)
    c.delete_spec(saved["id"])
    # A spawned worker holding the old spec may finish and recreate its sheet after the delete request.
    orphan = c.merged_sheet_path([saved])
    job: dict[str, Any] = {}
    c.publish_job(job, {"results": {"a": [{"gauge": G, "ewr": saved["code"], "uewr_id": saved["id"], "uewr_ver": saved["ver"]}]}})
    assert job == {"status": "done", "results": {"a": []}}
    assert not orphan.exists()


def test_publication_matches_definitions_by_id_not_reused_code(spec):
    old = c.save_spec(spec)
    c.delete_spec(old["id"])
    new = c.save_spec(spec)  # same gauge and code, and also version 1
    assert (new["code"], new["ver"]) == (old["code"], old["ver"]) and new["id"] != old["id"]
    row = {"gauge": G, "ewr": spec["code"], "pu": spec["pus"][0], "uewr_ver": 1}
    job: dict[str, Any] = {}
    c.publish_job(job, {"results": {"a": [{**row, "uewr_id": old["id"]}, {**row, "uewr_id": new["id"]}]}})
    assert job["results"]["a"] == [{**row, "uewr_id": new["id"]}]


def test_job_rows_carry_definition_id(spec, series, monkeypatch):
    saved = c.save_spec(spec)
    monkeypatch.setattr(c, "evaluate", lambda *args: [{"gauge": G, "ewr": saved["code"], "pu": spec["pus"][0]}])
    out = c.run_job([saved["id"]], ["a"], lambda *p: None)
    assert [(r["uewr_id"], r["uewr_ver"]) for r in out["results"]["a"]] == [(saved["id"], saved["ver"])]


def test_pruning_tolerates_a_stored_definition_that_no_longer_validates(spec, store):
    broken = c.save_spec(spec)
    spec["code"] = "U_OTHER"
    other = c.save_spec(spec)
    registry = json.loads((store / "uewr.json").read_text())
    registry[broken["id"]]["params"]["NoLongerOffered"] = 1  # e.g. a py_ewr upgrade dropped a component
    (store / "uewr.json").write_text(json.dumps(registry))
    c.delete_spec(other["id"])  # used to raise ValidationError after the registry write
    assert set(c.load_registry()) == {broken["id"]}
    job: dict[str, Any] = {}
    c.publish_job(job, {"results": {"a": []}})
    assert job["status"] == "done"


def test_sheet_caches_are_bounded_and_pruned_paths_leave_pyewr_cache(spec):
    assert c._clean_sheet.cache_info().maxsize == 8
    saved = c.save_spec(spec)
    path = str(c.merged_sheet_path([saved]))
    data_inputs.get_EWR_table(path)  # what ScenarioHandler's summaries do with the merged sheet
    key = data_inputs.get_EWR_table.cache_key(path)
    assert key in data_inputs.get_EWR_table.cache
    c.delete_spec(saved["id"])
    assert key not in data_inputs.get_EWR_table.cache and not Path(path).exists()
