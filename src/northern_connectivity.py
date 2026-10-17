"""Reader for the northern connectivity expert panel workbook ('River flows' sheet), used by the 'Northern connectivity' data format.

The sheet holds several scenarios side by side; each is cleaned into flow/level frames (one column per gauge number)
exactly as py_ewr's own cleaners do. Site names are mapped to gauges with data/reference/northern_connectivity_stations.csv.
"""

import logging
import re
from pathlib import Path
from typing import Any

import pandas as pd
from py_ewr import data_inputs

log = logging.getLogger(__name__)
STATIONS = Path(__file__).resolve().parent.parent / "data" / "reference" / "northern_connectivity_stations.csv"


def get_northern_connectivity_codes() -> dict:
    metadf = pd.read_csv(STATIONS, dtype=str)
    return dict(zip(metadf["site"].str.strip(), metadf["gauge"].str.strip(), strict=True))


def unpack_northern_connectivity(excel_file: str, sheet_name: str = "River flows") -> dict:
    """Ingests the 'River flows' sheet of the northern connectivity expert panel workbook.
    The sheet holds several scenarios side by side. Each scenario block has its name in
    row 3 (above its first gauge column), site names in row 5, units in row 6 and daily data
    from row 7 on, with the shared dates in the first column. Blocks are separated by a blank column.

    Args:
        excel_file (str): location of the northern connectivity workbook (.xlsx)
        sheet_name (str): sheet containing the river flows

    Results:
        dict: scenario code (e.g. 'CP1A') -> dataframe indexed by Date with one column per site name
    """
    if not str(excel_file).lower().endswith((".xlsx", ".xlsm")):
        raise ValueError("""Incorrect file type selected, northern connectivity format requires an excel (.xlsx) file.
                         Rerun the program and try again.""")

    raw = pd.read_excel(excel_file, sheet_name=sheet_name, header=None)

    # Locate the header row (site names) and the scenario label row above it
    header_rows = raw.index[raw.iloc[:, 0].astype(str).str.strip().str.lower() == "date"]
    if len(header_rows) == 0:
        raise ValueError(f"Could not find the 'Date' header row in sheet '{sheet_name}'")
    header_row = header_rows[0]
    scenario_row = header_row - 2
    data_start = header_row + 2  # skip the units row

    # Dates: a mix of real datetimes and dd/mm/yyyy text
    dates = raw.iloc[data_start:, 0]
    parsed = pd.Series(
        [pd.to_datetime(d.strip(), format="%d/%m/%Y", errors="coerce") if isinstance(d, str) else pd.to_datetime(d, errors="coerce") for d in dates],
        index=dates.index,
    )
    # Excel's phantom 29/02/1900 leap day comes through as a repeated 28/02/1900, so keep the first of any repeated date
    valid = parsed.notna() & ~parsed.duplicated()
    if (parsed.notna() & parsed.duplicated()).any():
        log.info("Dropped repeated dates in the northern connectivity river flows (Excel 1900 leap-day artefact).")
    parsed = parsed[valid]

    # Walk the columns, starting a new scenario each time a scenario label appears
    scenarios: dict[str, dict[str, Any]] = {}
    current = None
    for col in range(1, raw.shape[1]):
        label = raw.iat[scenario_row, col]
        site = raw.iat[header_row, col]
        if isinstance(label, str) and label.strip():
            match = re.match(r"\s*Scenario\s+([A-Za-z0-9]+)", label)
            current = match.group(1) if match else label.strip()
            scenarios[current] = {}
        if current is None or pd.isna(site):
            continue
        values = pd.to_numeric(raw.iloc[data_start:, col][valid], errors="coerce")
        scenarios[current][str(site).strip()] = values.values

    return {name: pd.DataFrame(cols, index=pd.DatetimeIndex(parsed.values, name="Date")) for name, cols in scenarios.items()}


def cleaner_northern_connectivity(input_df: pd.DataFrame, ewr_table_path: str | None = None) -> tuple:
    """Ingests one scenario from the northern connectivity workbook, maps site names to gauge numbers
    and allocates them to flow/level dataframes. Sites without a gauge mapping or without EWRs are skipped.

    Args:
        input_df (pd.DataFrame): one scenario's flows, indexed by Date, columns are site names
        ewr_table_path (str): optional path to a custom parameter sheet

    Results:
        tuple[pd.DataFrame, pd.DataFrame]: Cleaned flow dataframe; cleaned water level dataframe
    """
    stations = get_northern_connectivity_codes()
    flow_gauges = data_inputs.get_gauges("flow gauges", ewr_table_path=ewr_table_path)

    df_flow = pd.DataFrame(index=input_df.index)
    df_level = pd.DataFrame(index=input_df.index)
    df_flow.index.name = "Date"
    df_level.index.name = "Date"

    for site in input_df.columns:
        gauge = stations.get(site)
        if gauge is None:
            log.info(f"No gauge mapping for site {site}, skipping analysis of data in this column.")
        elif gauge in flow_gauges:
            df_flow[gauge] = input_df[site].copy(deep=True)
        else:
            log.info(f"Gauge {gauge} ({site}) has no flow EWRs, skipping.")
    return df_flow, df_level
