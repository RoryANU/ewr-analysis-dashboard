"""data/reference/parameter_sheet.csv must be the same file as the MDBA EWR tool's copy on GitHub."""

import urllib.error

import pytest

import sync_parameter_sheet as sync


def test_local_copy_exists_and_has_the_columns_the_page_uses():
    header = sync.LOCAL.read_text().splitlines()[0].split(",")
    for col in ("PlanningUnitName", "Gauge", "Code", "StartMonth", "EndMonth", "TargetFrequency", "Duration", "FlowThresholdMin", "State"):
        assert col in header


def test_normalise_ignores_line_endings_and_trailing_newlines():
    assert sync.normalise(b"a,b\r\n1,2\r\n\r\n") == sync.normalise(b"a,b\n1,2")


@pytest.mark.network
def test_matches_upstream():
    try:
        upstream = sync.fetch_upstream(timeout=20)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        pytest.skip(f"GitHub unreachable: {e}")
    assert sync.local_copy() == upstream, "out of date: run `python3 src/sync_parameter_sheet.py --update && python3 src/build_dashboard.py`"
