"""Rebuild detection follows Python calculations moved below the package root."""

import os
import shutil
from pathlib import Path

import pytest

from ewr_analysis import build_dashboard as bd


def test_moved_calculation_change_makes_dashboard_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    checkout = tmp_path / "checkout"
    package = checkout / "src" / "ewr_analysis"
    shutil.copytree(bd.HERE, package, ignore=shutil.ignore_patterns("__pycache__"))
    for path in package.rglob("*"):
        if path.is_file():
            os.utime(path, (1000, 1000))
    output = checkout / "ewr_analysis_dashboard.html"
    output.write_text("generated before the calculation edit")
    os.utime(output, (2000, 2000))
    monkeypatch.setattr(bd, "HERE", package)
    monkeypatch.setattr(bd, "ROOT", checkout)
    monkeypatch.setattr(bd, "OUTPUT", output)
    assert bd.is_stale() is False

    # Only the temporary copy is touched. The generated page predates this
    # nested Python input even though every top-level script is still older.
    calculation = package / "core" / "calculations" / "assessment_years.py"
    os.utime(calculation, (3000, 3000))
    assert bd.is_stale() is True
