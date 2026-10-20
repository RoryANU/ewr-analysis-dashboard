from typing import Any

import pandas as pd

LOW_FLOW_HANDLERS = ("lowflow_handle", "lowflow_handle_multi")


def _is_multigauge(value: Any) -> bool:
    """A parameter-sheet Multigauge cell names a partner gauge (blank cells read as NaN or whitespace)."""
    return isinstance(value, str) and bool(value.strip())


def _dispatch_key(code: str, multigauge: bool, kind: Any) -> str:
    """The key py_ewr's calc config uses to pick an EWR's handler, e.g. 'BF1_a-single-F'."""
    return f"{code}-{'multigauge' if multigauge else 'single'}-{kind}"


def ewr_params(sheet: pd.DataFrame | None) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Per (gauge, code, planning unit): target frequency (% of years), required duration (days) and maximum inter-event period (years),
    from the parameter sheet (the first row where a key repeats); empty without a sheet."""
    if sheet is None:
        return {}

    def number(column: str) -> pd.Series:
        return pd.to_numeric(sheet[column], errors="coerce") if column in sheet else pd.Series(float("nan"), index=sheet.index)

    d = pd.DataFrame({"g": sheet.Gauge.astype(str), "c": sheet.Code.astype(str), "pu": sheet.PlanningUnitName.astype(str)})
    d["T"], d["dur"], d["mrip"] = number("TargetFrequency"), number("Duration"), number("MaxInter-event")
    d["kind"] = sheet.FlowLevelVolume.astype(str) if "FlowLevelVolume" in sheet else "F"
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in d.drop_duplicates(["g", "c", "pu"]).itertuples():
        out[(r.g, r.c, r.pu)] = {
            "T": None if pd.isna(r.T) else float(r.T),
            "dur": None if pd.isna(r.dur) else int(r.dur),
            "mrip": None if pd.isna(r.mrip) else round(float(r.mrip), 4),
            "kind": r.kind,  # F / L / V (flow, level, volume); not carried into ROWS
        }
    return out
