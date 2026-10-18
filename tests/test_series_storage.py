"""Phase 1: parsing, persistence and lifecycle of flow, level and EC series."""

import http.client
import threading
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import server

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/example_scenarios/dummy_broken_scenario_A_level_salinity.csv"
G, OTHER = "404216", "404243"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "STORE", tmp_path)
    server.FLOWS.clear()
    server._EVAL_CACHE.clear()
    yield tmp_path
    server.FLOWS.clear()
    server._EVAL_CACHE.clear()


def raw(columns, start="2000-07-01"):
    count = len(next(iter(columns.values())))
    return pd.DataFrame(columns, index=pd.date_range(start, periods=count, name="Date")).to_csv().encode()


@pytest.fixture
def storage_run(store, monkeypatch):
    """Use real parsers/storage, isolating expensive py_ewr evaluation (covered end to end below)."""
    monkeypatch.setattr(server, "evaluate_gauges", lambda *args: ({G: {}}, {}, {}))
    monkeypatch.setattr(server, "summarise_rows", lambda *args, **kwargs: [{"gauge": G}])

    def run(columns, **kwargs):
        return server.run_scenario(raw(columns), [G, OTHER], "a", **kwargs)

    return run


@pytest.mark.parametrize("fmt", ["Standard time-series", "ten thousand year"])
def test_salinity_aliases_and_index_alignment(tmp_path, fmt):
    path = tmp_path / "series.csv"
    path.write_bytes(raw({f"{G}_flow": [1, 2, 3], f"{G}_level": [2.5, 2.6, np.nan], f"{G}_ec": [600, np.nan, 800], f"{OTHER}_tds": [300, 360, 420]}))
    flow, level, salinity = server.load_frames(fmt, str(path), tds_factor=2.0)
    assert flow[G].tolist() == [1, 2, 3]
    assert level[G].iloc[:2].tolist() == [2.5, 2.6] and pd.isna(level[G].iloc[2])
    assert salinity[G].iloc[[0, 2]].tolist() == [600, 800] and pd.isna(salinity[G].iloc[1])
    assert salinity[OTHER].tolist() == [600, 720, 840]
    assert salinity.attrs["tds_factor"] == 2.0
    assert salinity.index.equals(flow.index) and salinity.index.equals(level.index)
    assert salinity.index.name == "Date"
    assert isinstance(salinity.index, pd.PeriodIndex if fmt == "ten thousand year" else pd.DatetimeIndex)


@pytest.mark.parametrize("dates", [["2000-07-01", "2000-07-03"], ["01/07/2000", "03/07/2000"]])
def test_salinity_missing_days_stay_missing(tmp_path, dates):
    path = tmp_path / "series.csv"
    pd.DataFrame({"Date": dates, f"{G}_salinity": [600, 800]}).to_csv(path, index=False)
    flow, level, salinity = server.load_frames("Standard time-series", str(path))
    assert flow.shape[1] == level.shape[1] == 0
    assert len(salinity) == 3 and salinity[G].isna().tolist() == [False, True, False]


def test_duplicate_salinity_units_for_one_gauge_are_rejected(tmp_path):
    path = tmp_path / "series.csv"
    path.write_bytes(raw({f"{G}_ec": [600], f"{G}_tds": [360]}))
    with pytest.raises(ValueError, match="only one salinity column"):
        server.load_frames("Standard time-series", str(path))


@pytest.mark.parametrize("factor", [0, -1, float("nan"), float("inf")])
def test_invalid_tds_factor_is_rejected(tmp_path, factor):
    with pytest.raises(ValueError, match="finite positive"):
        server.load_frames("Standard time-series", str(tmp_path / "unused.csv"), tds_factor=factor)


def test_all_series_are_stored_and_gauges_reported(storage_run, store):
    _, stats = storage_run({f"{G}_flow": [1.0, 2.0], f"{G}_level": [2.5, 2.6], f"{G}_salinity": [600.0, np.nan], "999999_ec": [1, 1]})
    assert stats["flow_gauges"] == stats["level_gauges"] == stats["salinity_gauges"] == [G]
    assert stats["gauges_in_file"] == 1 and stats["missing_gauges"] == [OTHER]
    for kind, expected in (("flow", [1.0, 2.0]), ("level", [2.5, 2.6]), ("salinity", [600.0, np.nan])):
        actual = server.gauge_series("a", G, kind)
        np.testing.assert_allclose(actual.to_numpy(), expected, equal_nan=True)
        assert list(server.load_series("a", kind).columns) == [G]
        assert (store / f"a{server.SERIES_SUFFIX[kind]}.csv").exists()
    assert server.load_flows("a") is server.load_series("a", "flow")
    assert set(server.FLOWS) == {("a", kind) for kind in server.SERIES_SUFFIX}


@pytest.mark.parametrize("kind", ["flow", "level", "salinity"])
def test_merges_replace_overlapping_columns_and_outer_join_dates(storage_run, kind):
    columns = {f"{G}_{kind}": [1.0, 2.0], f"{OTHER}_{kind}": [3.0, 4.0], f"{G}_flow": [5.0, 6.0]}
    storage_run(columns)
    cached = server.load_series("a", kind)
    server._EVAL_CACHE[("a", G, "F1", "PU")] = {"old": True}
    columns = {f"{G}_{kind}": [7.0, np.nan, 9.0]}
    if kind == "flow":
        columns[f"{G}_flow"] = [7.0, np.nan, 9.0]
    server.run_scenario(raw(columns, start="2000-07-02"), [G, OTHER], "a", merge=True)
    merged = server.load_series("a", kind)
    assert merged is not cached and not server._EVAL_CACHE
    assert list(merged.columns) == [OTHER, G]
    np.testing.assert_allclose(merged[G], [np.nan, 7, np.nan, 9], equal_nan=True)
    np.testing.assert_allclose(merged[OTHER], [3, 4, np.nan, np.nan], equal_nan=True)


@pytest.mark.parametrize("kind", ["level", "salinity"])
def test_supplemental_only_merge_does_not_evaluate_or_change_flows(storage_run, store, monkeypatch, kind):
    storage_run({f"{G}_flow": [1.0, 2.0]})
    original = (store / "a.csv").read_bytes()

    def no_evaluation(*args, **kwargs):
        pytest.fail("Supplemental series must not evaluate built-in flow EWRs")

    monkeypatch.setattr(server, "evaluate_gauges", no_evaluation)
    monkeypatch.setattr(server, "summarise_rows", no_evaluation)
    rows, stats = storage_run({f"{OTHER}_{kind}": [600.0, 700.0]}, merge=True)
    assert rows == [] and stats["ewr_rows"] == 0 and stats[f"{kind}_gauges"] == [OTHER]
    assert stats["gauges_in_file"] == 1 and stats["missing_gauges"] == [G]
    assert (store / "a.csv").read_bytes() == original
    assert server.gauge_series("a", OTHER, kind).tolist() == [600.0, 700.0]


def test_supplemental_only_merge_requires_existing_scenario(storage_run, store):
    with pytest.raises(ValueError, match="existing stored scenario"):
        storage_run({f"{G}_salinity": [600.0, 700.0]}, merge=True)
    assert not list(store.glob("*.csv"))


def test_tds_factor_is_applied_and_recorded_in_upload_stats(storage_run):
    storage_run({f"{G}_flow": [1.0, 2.0]})
    _, stats = storage_run({f"{G}_tds": [300.0, 360.0]}, merge=True, tds_factor=1.5)
    assert stats["tds_factor"] == 1.5
    assert server.gauge_series("a", G, "salinity").tolist() == [450.0, 540.0]


def test_replacement_removes_omitted_series_and_caches(storage_run, store):
    storage_run({f"{G}_flow": [1.0], f"{G}_level": [2.0], f"{G}_salinity": [600.0]})
    for kind in server.SERIES_SUFFIX:
        server.load_series("a", kind)
    storage_run({f"{OTHER}_flow": [3.0]})
    assert {p.name for p in store.glob("*.csv")} == {"a.csv"} and not server.FLOWS
    assert list(server.load_flows("a").columns) == [OTHER]


def test_forget_cached_invalidates_every_kind_but_preserves_series_files(storage_run, store):
    storage_run({f"{G}_flow": [1.0], f"{G}_level": [2.0], f"{G}_salinity": [600.0]})
    for kind in server.SERIES_SUFFIX:
        server.load_series("a", kind)
        server.FLOWS[("other", kind)] = object()
    server._EVAL_CACHE[("a", G, "F1", "PU")] = {}
    server._EVAL_CACHE[("other", G, "F1", "PU")] = {}
    server.forget_cached("a")
    assert set(server.FLOWS) == {("other", kind) for kind in server.SERIES_SUFFIX}
    assert list(server._EVAL_CACHE) == [("other", G, "F1", "PU")]
    assert len(list(store.glob("*.csv"))) == 3


def test_forget_endpoint_deletes_all_series_and_cached_evaluations(storage_run, store):
    storage_run({f"{G}_flow": [1.0], f"{G}_level": [2.0], f"{G}_salinity": [600.0]})
    for kind in server.SERIES_SUFFIX:
        server.load_series("a", kind)
    server._EVAL_CACHE[("a", G, "F1", "PU")] = {}
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1])
    try:
        conn.request("POST", "/api/forget?id=a")
        response = conn.getresponse()
        assert response.status == 200 and response.read() == b'{"ok": true}'
        assert not list(store.glob("*.csv")) and not server.FLOWS and not server._EVAL_CACHE
    finally:
        conn.close()
        httpd.shutdown()
        httpd.server_close()
        thread.join()


@pytest.mark.parametrize("kind", ["flow", "level", "salinity"])
def test_stored_duplicate_dates_and_missing_gauge(store, kind):
    path = store / f"a{server.SERIES_SUFFIX[kind]}.csv"
    path.write_text(f"Date,{G}\n2000-07-01,1\n2000-07-01,1\n2000-07-03,3\n")
    assert len(server.load_series("a", kind)) == 2
    with pytest.raises(ValueError, match=f"no {kind} series"):
        server.gauge_series("a", OTHER, kind)
    server.forget_cached("a")
    path.write_text(f"Date,{G}\n2000-07-01,1\n2000-07-01,2\n")
    with pytest.raises(ValueError, match="dates repeated"):
        server.load_series("a", kind)


def test_unknown_series_kind_is_rejected(store):
    with pytest.raises(ValueError, match="unknown series kind"):
        server.load_series("a", "../other")


def test_date_free_frames_are_not_stored(storage_run, store):
    _, stats = storage_run({f"{G}_flow": [1.0], f"{G}_level": [2.0], f"{G}_ec": [600.0]}, fmt="ten thousand year")
    assert not list(store.glob("*.csv"))
    assert stats["flow_gauges"] == stats["level_gauges"] == stats["salinity_gauges"] == []


@pytest.mark.slow
def test_synthetic_fixture_runs_and_retains_levels_and_salinity(store):
    rows, stats = server.run_scenario(FIXTURE.read_bytes(), [G, OTHER], "fixture")
    original = pd.read_csv(FIXTURE, index_col="Date", parse_dates=True)
    assert rows and stats["skipped"] == {}
    assert stats["level_gauges"] == stats["salinity_gauges"] == [G]
    for kind in ("level", "salinity"):
        actual = server.gauge_series("fixture", G, kind)
        np.testing.assert_array_equal(actual.to_numpy(), original[f"{G}_{kind}"].to_numpy())
        assert actual.index.equals(original.index)
