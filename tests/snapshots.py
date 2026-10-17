"""Tiny snapshot helper: compare a JSON-able structure with a stored baseline in tests/fixtures/snapshots/.

Regenerate the baselines after an intended change with:  UPDATE_SNAPSHOTS=1 pytest -m "slow or not slow" tests/<file>
and review the diff before committing it.
"""

import json
import os
from pathlib import Path
from typing import Any

SNAPSHOT_DIR = Path(__file__).parent / "fixtures" / "snapshots"


def assert_snapshot(name: str, value: Any) -> None:
    path = SNAPSHOT_DIR / f"{name}.json"
    text = json.dumps(value, indent=1, sort_keys=True, allow_nan=False) + "\n"
    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return
    assert path.exists(), f"no baseline {path}; create it with UPDATE_SNAPSHOTS=1"
    expected = json.loads(path.read_text())
    actual = json.loads(text)
    assert actual == expected, (
        f"{name} differs from its baseline {path.name}; if the change is intended, regenerate with UPDATE_SNAPSHOTS=1 and review the diff"
    )
