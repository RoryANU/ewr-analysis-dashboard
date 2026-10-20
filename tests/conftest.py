"""Shared pytest configuration: ensure the generated dashboard is current."""


def pytest_sessionstart(session):
    """The dashboard page is generated, not committed: make sure the tests see a current one."""
    from ewr_analysis import build_dashboard

    build_dashboard.ensure_built()
