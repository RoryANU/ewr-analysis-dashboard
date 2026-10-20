"""Gauge completion progress, worker fallback and isolation between overlapping jobs."""

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from unittest.mock import MagicMock, Mock

import pandas as pd
import pytest
from py_ewr import evaluate_EWRs

from ewr_analysis import server


@pytest.mark.parametrize("explicit", [False, True])
def test_eval_gauge_accepts_explicit_inputs_and_worker_defaults(monkeypatch, explicit):
    inputs: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, object]] = (pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {})
    monkeypatch.setattr(server, "_POOL", dict(zip(("df_F", "df_L", "table", "calc_config"), inputs, strict=True)) if not explicit else {})
    calc = Mock(return_value=({"result": 1}, {"events": 2}))
    monkeypatch.setattr(evaluate_EWRs, "calc_sorter", calc)

    output = server._eval_gauge("a", *inputs) if explicit else server._eval_gauge("a")
    assert output == ("a", calc.return_value, None)
    args = calc.call_args.args
    assert args[2] == "a"
    assert all(actual is expected for actual, expected in zip((args[0], args[1], args[3], args[4]), inputs, strict=True))

    calc.side_effect = ValueError("bad gauge")
    assert (server._eval_gauge("a", *inputs) if explicit else server._eval_gauge("a")) == ("a", None, "ValueError: bad gauge")


def test_progress_follows_completion_and_outputs_follow_gauge_order(monkeypatch):
    gauges = ["a", "b", "c", "d", "e"]
    futures: dict[str, Future] = {g: Future() for g in gauges}
    completion_order = ["e", "d", "c", "b", "a"]
    pool = MagicMock()
    pool.__enter__.return_value = pool
    pool.submit.side_effect = lambda fn, g: futures[g]
    monkeypatch.setattr(server, "ProcessPoolExecutor", Mock(return_value=pool))
    monkeypatch.setattr(server.os, "cpu_count", lambda: 2)
    updates = []

    def complete(g):
        futures[g].set_result((g, None, f"error {g}") if g in ("b", "d") else (g, (f"result {g}", f"events {g}"), None))

    def progress(stage, done, total):
        updates.append((stage, done, total))
        if done:
            assert futures[completion_order[done - 1]].done()
            # The first submitted gauge must still be pending when earlier completions are reported.
            assert futures["a"].done() is (done == total)
            if done < total:
                complete(completion_order[done])

    complete(completion_order[0])
    results, events, skipped = server.evaluate_gauges(pd.DataFrame(columns=["e", "b", "a"]), pd.DataFrame(columns=["d", "c"]), {}, {}, progress)

    assert [call.args[1] for call in pool.submit.call_args_list] == gauges
    assert list(results.items()) == [(g, f"result {g}") for g in ("a", "c", "e")]
    assert list(events.items()) == [(g, f"events {g}") for g in ("a", "c", "e")]
    assert list(skipped.items()) == [(g, f"error {g}") for g in ("b", "d")]
    assert updates == [("Evaluating gauges", done, 5) for done in range(6)]


@pytest.mark.parametrize("pool_error", [None, OSError("no workers"), BrokenProcessPool("no workers")])
def test_overlapping_serial_jobs_keep_their_inputs(monkeypatch, pool_error):
    monkeypatch.setattr(server, "_POOL", {})
    monkeypatch.setattr(server.os, "cpu_count", lambda: 1 if pool_error is None else 2)
    monkeypatch.setattr(server, "ProcessPoolExecutor", Mock(side_effect=pool_error))
    jobs: dict[str, tuple[pd.DataFrame, pd.DataFrame, dict[str, str], dict[str, object]]] = {
        name: (pd.DataFrame(columns=["b", "a"]), pd.DataFrame(columns=["l"]), {"job": name}, {}) for name in ("first", "second")
    }
    paused, resume = threading.Event(), threading.Event()

    def calc(df_f, df_l, gauge, table, config):
        name = table["job"]
        assert all(actual is expected for actual, expected in zip((df_f, df_l, table, config), jobs[name], strict=True))
        return f"{name} result {gauge}", f"{name} events {gauge}"

    def progress(stage, done, total):
        if done == 1:
            paused.set()
            assert resume.wait(5), "second job did not finish"

    monkeypatch.setattr(evaluate_EWRs, "calc_sorter", calc)
    with ThreadPoolExecutor(2) as threads:
        first = threads.submit(server.evaluate_gauges, *jobs["first"], progress)
        try:
            assert paused.wait(5), "first job did not report progress"
            second = threads.submit(server.evaluate_gauges, *jobs["second"], lambda *args: None)
            second_output = second.result(timeout=5)
        finally:
            resume.set()
        first_output = first.result(timeout=5)

    for name, (results, events, skipped) in (("first", first_output), ("second", second_output)):
        assert results == {g: f"{name} result {g}" for g in ("a", "b", "l")}
        assert events == {g: f"{name} events {g}" for g in ("a", "b", "l")}
        assert skipped == {}
    assert server._POOL == {}


def test_broken_pool_retries_only_uncollected_gauges(monkeypatch):
    inputs: tuple[pd.DataFrame, pd.DataFrame, dict[str, object], dict[str, object]] = (pd.DataFrame(columns=["c", "a", "b"]), pd.DataFrame(), {}, {})
    futures: dict[str, Future] = {g: Future() for g in ("a", "b", "c")}
    futures["c"].set_result(("c", ("pool result", "pool events"), None))
    pool = MagicMock()
    pool.__enter__.return_value = pool
    pool.submit.side_effect = lambda fn, g: futures[g]
    monkeypatch.setattr(server, "ProcessPoolExecutor", Mock(return_value=pool))
    monkeypatch.setattr(server.os, "cpu_count", lambda: 2)
    monkeypatch.setattr(server, "_POOL", {})
    calc = Mock(side_effect=lambda df_f, df_l, g, table, config: (f"serial result {g}", f"serial events {g}"))
    monkeypatch.setattr(evaluate_EWRs, "calc_sorter", calc)
    updates = []

    def progress(stage, done, total):
        updates.append(done)
        if done == 1:
            futures["a"].set_exception(BrokenProcessPool("worker stopped"))

    results, events, skipped = server.evaluate_gauges(*inputs, progress)

    assert list(results.items()) == [("a", "serial result a"), ("b", "serial result b"), ("c", "pool result")]
    assert list(events.items()) == [("a", "serial events a"), ("b", "serial events b"), ("c", "pool events")]
    assert skipped == {} and updates == [0, 1, 2, 3]
    assert [call.args[2] for call in calc.call_args_list] == ["a", "b"]
    for call in calc.call_args_list:
        args = call.args
        assert all(actual is expected for actual, expected in zip((args[0], args[1], args[3], args[4]), inputs, strict=True))
    assert server._POOL == {}
