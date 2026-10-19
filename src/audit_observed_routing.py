"""Compare repo gauge state labels with mdba-gauge-getter's bundled router."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def audit() -> list[dict[str, str]]:
    """Gauges whose parameter-sheet states differ from the getter catalog's, with the services the adapter asks, in order."""
    import observed

    sheet, catalog = observed.sheet_states(), observed.catalog_states()
    output = []
    for gauge in sorted(sheet):
        expected, found = set(sheet[gauge]), set(catalog.get(gauge, ()))
        if found != expected:
            order = observed.candidate_states(gauge)
            row = {"gauge": gauge, "parameter_sheet_state": ";".join(sorted(expected)), "getter_catalog_state": ";".join(sorted(found)) or "missing"}
            output.append(row | {"routing": " then ".join(order) if order else "unavailable"})
    return output


def main() -> None:
    path = ROOT / "docs/observed-routing-mismatches.csv"
    rows = audit()
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["gauge", "parameter_sheet_state", "getter_catalog_state", "routing"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} mismatches to {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
