"""Shared pytest configuration: puts src/ on the import path so tests can `import server`, `import build_dashboard`, etc."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def pytest_sessionstart(session):
    """The dashboard page is generated, not committed: make sure the tests see a current one."""
    import build_dashboard

    build_dashboard.ensure_built()
