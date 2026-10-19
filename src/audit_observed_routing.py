"""Compare repo gauge state labels with mdba-gauge-getter's bundled router."""

from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def audit() -> list[dict[str, str]]:
    import pandas as pd
    from mdba_gauge_getter import gauge_getter

    params = pd.read_csv(ROOT / "data/reference/parameter_sheet.csv", dtype=str)
    states: dict[str, set[str]] = defaultdict(set)
    for _, row in params[["Gauge", "State"]].dropna().drop_duplicates().iterrows():
        gauge = row["Gauge"].strip()
        if not re.fullmatch(r"(?:A)?\d+[A-Z]*", gauge, flags=re.I):
            continue
        states[gauge].add(row["State"].strip().upper())

    catalog = pd.read_csv(gauge_getter.gauge_data_uri, skiprows=1, skipfooter=1, names=["name", "gauge", "owner", "lat", "lon"], engine="python", dtype=str)
    catalog_states: dict[str, set[str]] = defaultdict(set)
    for _, row in catalog.iterrows():
        state = row["owner"].strip().split(" ", 1)[0].upper()
        catalog_states[row["gauge"].strip()].add(state)

    output = []
    for gauge in sorted(states):
        expected = ";".join(sorted(states[gauge]))
        found = ";".join(sorted(catalog_states.get(gauge, set())))
        expected_states = set(expected.split(";"))
        getter_states = set(found.split(";")) if found else set()
        if getter_states != expected_states:
            if getter_states.intersection(expected_states):
                route = "package_catalog"
            else:
                route = "direct_state_api" if len(expected_states) == 1 and expected in {"NSW", "VIC", "QLD"} else "unavailable"
            output.append({"gauge": gauge, "parameter_sheet_state": expected, "getter_catalog_state": found or "missing", "routing": route})
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
