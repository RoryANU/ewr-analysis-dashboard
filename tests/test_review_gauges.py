"""review_gauges (used by the notebook route) must request every gauge in the review: FR and ETP results."""

from pathlib import Path

import pandas as pd

from ewr_analysis import embed_scenarios

ROOT = Path(__file__).resolve().parents[1]


def gauges_in(name):
    d = pd.read_csv(ROOT / "data/results" / name, dtype=str, usecols=lambda c: c.strip() == "Gauge")
    return set(d.iloc[:, 0].dropna().str.strip())


def test_includes_etp_only_gauges():
    fr, etp = gauges_in("frequency_ratio.csv"), gauges_in("etp.csv")
    etp_only = etp - fr
    assert "422030" in etp_only and len(etp_only) == 11  # the gauges that were never requested from py_ewr
    got = set(embed_scenarios.review_gauges())
    assert etp_only <= got and fr <= got


def test_includes_rekeyed_ids_and_is_sorted_unique():
    got = embed_scenarios.review_gauges()
    assert "A4260524" in got and "A4260527" in got
    assert got == sorted(set(got))
