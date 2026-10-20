"""Repository checkout locations, preserved for the Phase 0 package migration."""

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
ROOT = PACKAGE_DIR.parents[1]
DATA_DIR = ROOT / "data"
USER_SCENARIOS = ROOT / ".user_scenarios"
OBSERVED_CACHE = ROOT / ".observed_cache"
DASHBOARD_HTML = ROOT / "ewr_analysis_dashboard.html"
