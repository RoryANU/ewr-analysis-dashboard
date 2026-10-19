"""Observed-data adapter and cache tests; all network calls are replaced by fixtures."""

import time
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import observed


def raw_frame(gauge: str, dates: list[str], values: list[str] | None = None) -> pd.DataFrame:
    values = values or ["1.25"] * len(dates)
    return pd.DataFrame(
        {
            "DATASOURCEID": ["NSW"] * len(dates),
            "SITEID": [gauge] * len(dates),
            "SUBJECTID": ["WATER"] * len(dates),
            "DATETIME": pd.to_datetime(dates),
            "VALUE": values,
            "QUALITYCODE": [140] * len(dates),
        }
    )


def test_optional_getter_imports_on_current_python():
    pytest.importorskip("mdba_gauge_getter")
    import mdba_gauge_getter.gauge_getter as getter

    assert callable(getter.gauge_pull)


def test_routing_audit_records_the_known_422016_mismatch():
    from audit_observed_routing import audit

    rows = {row["gauge"]: row for row in audit()}

    assert rows["422016"]["parameter_sheet_state"] == "NSW;QLD"
    assert rows["422016"]["getter_catalog_state"] == "NSW"
    assert rows["422016"]["routing"] == "package_catalog"
    assert rows["422030"]["routing"] == "direct_state_api"


def test_known_unambiguous_mismatch_uses_direct_state_endpoint(monkeypatch):
    getter = pytest.importorskip("mdba_gauge_getter.gauge_getter")
    monkeypatch.setattr(observed, "_source_for", lambda gauge: "QLD")
    monkeypatch.setattr(observed, "_catalog_sources", lambda gauge: {"NSW"})
    request = []

    def call(*args):
        request.append(args)
        return {"stub": True}

    monkeypatch.setattr(getter, "call_state_api", call)
    monkeypatch.setattr(getter, "extract_data", lambda source, body: [[source, "422030", "WATER", date(2000, 1, 1), "1", 140]])
    monkeypatch.setattr(getter, "gauge_pull", lambda *args, **kwargs: pytest.fail("router should be bypassed"))

    frame = observed._raw_fetch("422030", date(2000, 1, 1), date(2000, 1, 2), "F")

    assert request[0][:5] == ("QLD", ["422030"], date(2000, 1, 1), date(2000, 1, 2), "AT")
    assert frame.loc[0, "DATASOURCEID"] == "QLD"


def test_cache_hit_and_raw_quality_codes(monkeypatch, tmp_path):
    monkeypatch.setattr(observed, "CACHE_DIR", tmp_path)
    calls = []

    def fetch(gauge, start, end, var, timeout):
        calls.append((start, end))
        return raw_frame(gauge, ["2000-01-01", "2000-01-02"])

    monkeypatch.setattr(observed, "_fetch_isolated", fetch)
    start, end = date(2000, 1, 1), date(2000, 1, 2)

    first = observed.fetch_gauge("412033", start, end)
    second = observed.fetch_gauge("412033", start, end)

    assert first.status == second.status == "ok"
    assert len(calls) == 1
    assert second.frame["QUALITYCODE"].tolist() == [140, 140]


def test_cache_extends_only_missing_head_and_tail(monkeypatch, tmp_path):
    monkeypatch.setattr(observed, "CACHE_DIR", tmp_path)
    calls = []

    def fetch(gauge, start, end, var, timeout):
        calls.append((start, end))
        days = pd.date_range(start, end, freq="D").strftime("%Y-%m-%d").tolist()
        return raw_frame(gauge, days)

    monkeypatch.setattr(observed, "_fetch_isolated", fetch)
    observed.fetch_gauge("412033", date(2000, 1, 3), date(2000, 1, 5))
    observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 7))

    assert calls == [(date(2000, 1, 3), date(2000, 1, 5)), (date(2000, 1, 1), date(2000, 1, 2)), (date(2000, 1, 6), date(2000, 1, 7))]
    cached = observed._read_cache("412033", "F")
    assert cached is not None and len(cached[0]) == 7


def test_expired_cache_refreshes_range(monkeypatch, tmp_path):
    monkeypatch.setattr(observed, "CACHE_DIR", tmp_path)
    start, end = date(2000, 1, 1), date(2000, 1, 2)
    observed._write_cache("412033", "F", raw_frame("412033", ["2000-01-01", "2000-01-02"]), start, end)
    data_path, meta_path = observed._cache_paths("412033", "F")
    meta = __import__("json").loads(meta_path.read_text())
    meta["fetched_at"] = 0
    meta_path.write_text(__import__("json").dumps(meta))
    calls = []

    def fetch(gauge, left, right, var, timeout):
        calls.append((left, right))
        return raw_frame(gauge, ["2000-01-01", "2000-01-02"], ["2", "2"])

    monkeypatch.setattr(observed, "_fetch_isolated", fetch)

    result = observed.fetch_gauge("412033", start, end, ttl=1)
    assert result.status == "ok" and result.frame["VALUE"].tolist() == ["2", "2"]
    assert calls == [(start, end)]


def test_timeout_empty_and_429_are_classified(monkeypatch, tmp_path):
    monkeypatch.setattr(observed, "CACHE_DIR", tmp_path)
    start, end = date(2000, 1, 1), date(2000, 1, 2)
    monkeypatch.setattr(observed, "_fetch_isolated", lambda *args: (_ for _ in ()).throw(TimeoutError("deadline")))
    assert observed.fetch_gauge("412033", start, end).status == "timeout"

    monkeypatch.setattr(
        observed, "_fetch_isolated", lambda *args: pd.DataFrame(columns=["DATASOURCEID", "SITEID", "SUBJECTID", "DATETIME", "VALUE", "QUALITYCODE"])
    )
    assert observed.fetch_gauge("412034", start, end).status == "empty"

    monkeypatch.setattr(observed, "_fetch_isolated", lambda *args: (_ for _ in ()).throw(RuntimeError("HTTPError: Request failed HTTP Response code 429")))
    assert observed.fetch_gauge("412035", start, end).status == "rate_limited"


def test_transient_fetch_error_retries(monkeypatch, tmp_path):
    monkeypatch.setattr(observed, "CACHE_DIR", tmp_path)
    calls = 0

    def fetch(gauge, start, end, var, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("temporary network failure")
        return raw_frame(gauge, ["2000-01-01"])

    monkeypatch.setattr(observed, "_fetch_isolated", fetch)
    result = observed.fetch_gauge("412036", date(2000, 1, 1), date(2000, 1, 1), retries=1)

    assert result.status == "ok"
    assert calls == 2


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
