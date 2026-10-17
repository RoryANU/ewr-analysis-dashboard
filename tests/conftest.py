"""Shared pytest configuration: puts src/ on the import path so tests can `import server`, `import build_dashboard`, etc."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
