"""data/reference/parameter_sheet.csv must be the same file as the MDBA EWR tool's copy on GitHub."""

import urllib.error
from pathlib import Path

import pytest

import sync_parameter_sheet as sync

VALID_UPSTREAM = b"PlanningUnitName,Code,StartMonth,EndMonth,Multigauge,FlowLevelVolume\nExample,BF1,7,6,,F\n"


def test_local_copy_exists_and_has_the_columns_the_page_uses():
    """Verify the local sheet contains the columns required by the guide."""
    header = sync.LOCAL.read_text().splitlines()[0].split(",")
    for col in ("PlanningUnitName", "Gauge", "Code", "StartMonth", "EndMonth", "TargetFrequency", "Duration", "FlowThresholdMin", "State"):
        assert col in header


def test_normalise_ignores_line_endings_and_trailing_newlines():
    """Verify equivalent CSV bytes compare equally despite newline differences."""
    assert sync.normalise(b"a,b\r\n1,2\r\n\r\n") == sync.normalise(b"a,b\n1,2")


@pytest.mark.parametrize("upstream", [b"", b"<html>error</html>\nPlanningUnitName,Code\n", b"PlanningUnitName,Gauge\n", b"Gauge,Code\n", b"PlanningUnitName,Code\nExample,BF1\n"])
def test_update_rejects_invalid_header_without_changing_local_copy(tmp_path, monkeypatch, capsys, upstream):
    """Verify invalid upstream headers leave the local sheet and directory unchanged."""
    local = tmp_path / "parameter_sheet.csv"
    original = b"original local content\n"
    local.write_bytes(original)
    monkeypatch.setattr(sync, "LOCAL", local)
    monkeypatch.setattr(sync, "fetch_upstream", lambda: upstream)

    assert sync.main(["--update"]) == 1
    assert "invalid upstream header" in capsys.readouterr().err
    assert local.read_bytes() == original
    assert list(tmp_path.iterdir()) == [local]


def test_update_replaces_local_copy_atomically(tmp_path, monkeypatch):
    """Verify updates replace a complete temporary file beside the existing sheet."""
    local = tmp_path / "parameter_sheet.csv"
    original = b"original local content\n"
    upstream = VALID_UPSTREAM
    local.write_bytes(original)
    monkeypatch.setattr(sync, "LOCAL", local)
    monkeypatch.setattr(sync, "fetch_upstream", lambda: upstream)
    replace = Path.replace
    replacements = []

    def checked_replace(source, target):
        """Check that the complete temporary copy exists before replacing the original."""
        assert source.parent == local.parent
        assert source != local
        assert source.read_bytes() == upstream
        assert local.read_bytes() == original
        replacements.append((source, target))
        return replace(source, target)

    monkeypatch.setattr(Path, "replace", checked_replace)

    assert sync.main(["--update"]) == 0
    assert len(replacements) == 1
    assert local.read_bytes() == upstream
    assert list(tmp_path.iterdir()) == [local]


def test_failed_update_preserves_local_copy_and_removes_temporary_file(tmp_path, monkeypatch):
    """Verify a failed replacement preserves the original sheet and cleans up its temporary copy."""
    local = tmp_path / "parameter_sheet.csv"
    original = b"original local content\n"
    local.write_bytes(original)
    monkeypatch.setattr(sync, "LOCAL", local)
    monkeypatch.setattr(sync, "fetch_upstream", lambda: VALID_UPSTREAM)

    def failed_replace(source, target):
        """Simulate a filesystem failure during the atomic replacement."""
        raise OSError("replacement failed")

    monkeypatch.setattr(Path, "replace", failed_replace)

    with pytest.raises(OSError, match="replacement failed"):
        sync.main(["--update"])
    assert local.read_bytes() == original
    assert list(tmp_path.iterdir()) == [local]


def test_interrupted_write_preserves_local_copy_and_removes_temporary_file(tmp_path, monkeypatch):
    """Verify an interrupted write preserves the original sheet and removes the partial copy."""
    local = tmp_path / "parameter_sheet.csv"
    original = b"original local content\n"
    local.write_bytes(original)
    monkeypatch.setattr(sync, "LOCAL", local)
    monkeypatch.setattr(sync, "fetch_upstream", lambda: VALID_UPSTREAM)
    temp = sync.tempfile.NamedTemporaryFile(dir=tmp_path, delete=False)
    write = temp.write

    def interrupted_write(content):
        """Write a partial sheet before simulating a keyboard interruption."""
        write(content[:5])
        raise KeyboardInterrupt

    monkeypatch.setattr(temp, "write", interrupted_write)
    monkeypatch.setattr(sync.tempfile, "NamedTemporaryFile", lambda **kwargs: temp)

    with pytest.raises(KeyboardInterrupt):
        sync.main(["--update"])
    assert local.read_bytes() == original
    assert list(tmp_path.iterdir()) == [local]


@pytest.mark.network
def test_matches_upstream():
    """Compare the local sheet with GitHub, skipping when the upstream is unreachable."""
    try:
        upstream = sync.fetch_upstream(timeout=20)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        pytest.skip(f"GitHub unreachable: {e}")
    assert sync.local_copy() == upstream, "out of date: run `python3 src/sync_parameter_sheet.py --update && python3 src/build_dashboard.py`"
