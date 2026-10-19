"""Observed-data adapter and cache tests; all network calls are replaced by fixtures."""

import time
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import observed
import observed_source
from observed_source import DeadlineExceeded, NoData, RateLimited, StationNotFound


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
            "UNITS": ["ML/d"] * len(dates),
        }
    )


def test_optional_getter_imports_on_current_python():
    pytest.importorskip("mdba_gauge_getter")
    import mdba_gauge_getter.gauge_getter as getter

    assert callable(getter.gauge_pull)


def test_routing_audit_records_the_known_mismatches():
    from audit_observed_routing import audit

    rows = {row["gauge"]: row for row in audit()}

    assert rows["422016"]["parameter_sheet_state"] == "NSW;QLD"
    assert rows["422016"]["getter_catalog_state"] == "NSW"
    assert rows["422016"]["routing"] == "NSW then QLD"
    assert rows["A4261002"]["routing"] == "unavailable"


def test_catalog_state_is_asked_before_the_parameter_sheet(monkeypatch):
    monkeypatch.setattr(observed, "catalog_states", lambda: {"422030": frozenset({"NSW"}), "A4261002": frozenset({"SA"})})
    monkeypatch.setattr(observed, "sheet_states", lambda: {"422030": frozenset({"QLD"}), "A4261002": frozenset({"SA"}), "409207": frozenset({"NSW", "VIC"})})

    assert observed.candidate_states("422030") == ["NSW", "QLD"]
    assert observed.candidate_states("409207") == ["NSW", "VIC"]
    assert observed.candidate_states("A4261002") == []


class FakeClient:
    """Stands in for StateClient.fetch: `answers[state]` is a frame or an exception to raise."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def fetch(self, state, gauge, start, end, var, *, timeout, retries, clamp_to_record):
        self.calls.append(state)
        answer = self.answers[state]
        if isinstance(answer, Exception):
            raise answer
        return observed_source.Trace(answer, "ML/d", 0, 0)


def test_station_unknown_to_one_service_is_asked_of_the_next(monkeypatch, tmp_path):
    monkeypatch.setattr(observed, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(observed, "_ROUTES", {})
    monkeypatch.setattr(observed, "candidate_states", lambda gauge: ["QLD", "NSW"])
    fake = FakeClient({"QLD": StationNotFound("QLD: Invalid station"), "NSW": raw_frame("422030", ["2000-01-01"])})
    monkeypatch.setattr(observed, "_CLIENT", fake)

    first = observed.fetch_gauge("422030", date(2000, 1, 1), date(2000, 1, 1))
    observed.fetch_gauge("422030", date(2000, 1, 1), date(2000, 1, 1), var="L")

    assert first.status == "ok"
    assert fake.calls == ["QLD", "NSW", "NSW"]  # the service that answered is asked first next time


def test_station_unknown_everywhere_is_unsupported(monkeypatch, tmp_path):
    monkeypatch.setattr(observed, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(observed, "_ROUTES", {})
    monkeypatch.setattr(observed, "candidate_states", lambda gauge: ["NSW", "VIC"])
    monkeypatch.setattr(observed, "_CLIENT", FakeClient({"NSW": StationNotFound("x"), "VIC": StationNotFound("y")}))

    result = observed.fetch_gauge("1AS", date(2000, 1, 1), date(2000, 1, 1))

    assert result.status == "unsupported" and result.message == "1AS is not a station on the NSW, VIC services"


def test_cache_hit_and_raw_quality_codes(monkeypatch, tmp_path):
    monkeypatch.setattr(observed, "CACHE_DIR", tmp_path)
    calls = []

    def fetch(gauge, start, end, var, timeout, retries):
        calls.append((start, end))
        return raw_frame(gauge, ["2000-01-01", "2000-01-02"])

    monkeypatch.setattr(observed, "_fetch_source", fetch)
    start, end = date(2000, 1, 1), date(2000, 1, 2)

    first = observed.fetch_gauge("412033", start, end)
    second = observed.fetch_gauge("412033", start, end)

    assert first.status == second.status == "ok"
    assert len(calls) == 1
    assert second.frame["QUALITYCODE"].tolist() == [140, 140]


def test_cache_extends_only_missing_head_and_tail(monkeypatch, tmp_path):
    monkeypatch.setattr(observed, "CACHE_DIR", tmp_path)
    calls = []

    def fetch(gauge, start, end, var, timeout, retries):
        calls.append((start, end))
        days = pd.date_range(start, end, freq="D").strftime("%Y-%m-%d").tolist()
        return raw_frame(gauge, days)

    monkeypatch.setattr(observed, "_fetch_source", fetch)
    observed.fetch_gauge("412033", date(2000, 1, 3), date(2000, 1, 5))
    observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 7))

    assert calls == [(date(2000, 1, 3), date(2000, 1, 5)), (date(2000, 1, 1), date(2000, 1, 2)), (date(2000, 1, 6), date(2000, 1, 7))]
    cached = observed._read_cache("412033", "F")
    assert cached is not None and len(cached[0]) == 7


def test_disjoint_request_replaces_the_cache_instead_of_leaving_a_hole(monkeypatch, tmp_path):
    """A 1900 request after a cached 2020 range used to record 1900-2020 as covered with nothing fetched in between."""
    monkeypatch.setattr(observed, "CACHE_DIR", tmp_path)
    calls = []

    def fetch(gauge, start, end, var, timeout, retries):
        calls.append((start, end))
        return raw_frame(gauge, pd.date_range(start, end, freq="D").strftime("%Y-%m-%d").tolist())

    monkeypatch.setattr(observed, "_fetch_source", fetch)
    observed.fetch_gauge("412033", date(2020, 1, 1), date(2020, 1, 4))
    observed.fetch_gauge("412033", date(1990, 1, 1), date(1990, 1, 4))
    observed.fetch_gauge("412033", date(1990, 1, 5), date(1990, 1, 6))  # adjoins: extends
    wide = observed.fetch_gauge("412033", date(1990, 1, 1), date(2020, 1, 4))

    assert calls == [
        (date(2020, 1, 1), date(2020, 1, 4)),
        (date(1990, 1, 1), date(1990, 1, 4)),
        (date(1990, 1, 5), date(1990, 1, 6)),
        (date(1990, 1, 7), date(2020, 1, 4)),
    ]
    assert len(wide.frame) == (date(2020, 1, 4) - date(1990, 1, 1)).days + 1


def test_expired_cache_does_not_keep_its_wider_coverage(monkeypatch, tmp_path):
    monkeypatch.setattr(observed, "CACHE_DIR", tmp_path)
    observed._write_cache("412033", "F", raw_frame("412033", ["1990-01-01", "2020-01-02"]), date(1990, 1, 1), date(2020, 1, 2))
    _, meta_path = observed._cache_paths("412033", "F")
    meta_path.write_text(__import__("json").dumps(__import__("json").loads(meta_path.read_text()) | {"fetched_at": 0}))
    monkeypatch.setattr(observed, "_fetch_source", lambda gauge, start, end, *args: raw_frame(gauge, ["2020-01-02"]))

    observed.fetch_gauge("412033", date(2020, 1, 2), date(2020, 1, 2), ttl=1)

    cached = observed._read_cache("412033", "F")
    assert cached is not None and (cached[1]["start"], cached[1]["end"]) == ("2020-01-02", "2020-01-02")


def test_expired_cache_refreshes_range(monkeypatch, tmp_path):
    monkeypatch.setattr(observed, "CACHE_DIR", tmp_path)
    start, end = date(2000, 1, 1), date(2000, 1, 2)
    observed._write_cache("412033", "F", raw_frame("412033", ["2000-01-01", "2000-01-02"]), start, end)
    data_path, meta_path = observed._cache_paths("412033", "F")
    meta = __import__("json").loads(meta_path.read_text())
    meta["fetched_at"] = 0
    meta_path.write_text(__import__("json").dumps(meta))
    calls = []

    def fetch(gauge, left, right, var, timeout, retries):
        calls.append((left, right))
        return raw_frame(gauge, ["2000-01-01", "2000-01-02"], ["2", "2"])

    monkeypatch.setattr(observed, "_fetch_source", fetch)

    result = observed.fetch_gauge("412033", start, end, ttl=1)
    assert result.status == "ok" and result.frame["VALUE"].tolist() == ["2", "2"]
    assert calls == [(start, end)]


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (DeadlineExceeded("deadline"), "timeout"),
        (RateLimited("HTTP 429"), "rate_limited"),
        (StationNotFound("unknown"), "unsupported"),
        (observed_source.TransientError("HTTP 502"), "error"),
        (KeyError("bug"), "error"),
    ],
)
def test_source_errors_map_to_gauge_statuses(monkeypatch, tmp_path, error, status):
    monkeypatch.setattr(observed, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(observed, "_fetch_source", lambda *args: (_ for _ in ()).throw(error))

    result = observed.fetch_gauge("412033", date(2000, 1, 1), date(2000, 1, 2))

    assert result.status == status and result.frame.empty and str(error.args[0]) in result.message
    assert not any(tmp_path.iterdir())  # failures are never cached


def test_no_data_in_period_is_empty_and_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(observed, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(observed, "_ROUTES", {})
    monkeypatch.setattr(observed, "candidate_states", lambda gauge: ["NSW"])
    fake = FakeClient({"NSW": NoData("NSW service: No data within specified period")})
    monkeypatch.setattr(observed, "_CLIENT", fake)

    first = observed.fetch_gauge("412033", date(1900, 1, 1), date(1900, 1, 4))
    second = observed.fetch_gauge("412033", date(1900, 1, 1), date(1900, 1, 4))

    assert first.status == second.status == "empty"
    assert fake.calls == ["NSW"]


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
