"""Keep file access, process state and caches out of scientific calculations."""

import re
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "src" / "ewr_analysis" / "core"
FORBIDDEN = re.compile(r"open\(|read_csv|read_excel|os\.environ|__file__|lru_cache|Lock\(")


def test_core_has_no_io_or_state_patterns():
    violations = [
        f"{path.relative_to(CORE)}:{line_number}: {match.group()}"
        for path in sorted(CORE.rglob("*.py"))
        for line_number, line in enumerate(path.read_text().splitlines(), 1)
        for match in FORBIDDEN.finditer(line)
    ]
    assert not violations, "Core must not own I/O or state:\n" + "\n".join(violations)
