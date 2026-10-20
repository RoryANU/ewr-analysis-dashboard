from typing import Any

import pandas as pd

LOW_FLOW_HANDLERS = ("lowflow_handle", "lowflow_handle_multi")


def _is_multigauge(value: Any) -> bool:
    """Test whether a parameter cell names a nonblank string partner gauge.

    Equation / rule: isinstance(value, str) and bool(value.strip()).

    Parameters
    ----------
    value : scalar
        Partner-gauge cell [identifier], often blank/NaN; no index/timezone.

    Returns
    -------
    bool
        Multigauge flag [dimensionless]. None, NaN, numeric and list values are
        False; whitespace-only strings are False.

    Assumptions
    -----------
    Only the predicate uses stripped text; it does not normalize or return the
    identifier. No scientific threshold/date boundary applies. No input is mutated.

    References
    ----------
    pre-refactor:src/server.py::_is_multigauge. Scientific provenance or
    suitability of this retained convention: TBD, needs scientist review."""
    return isinstance(value, str) and bool(value.strip())


def _dispatch_key(code: str, multigauge: bool, kind: Any) -> str:
    """Format the legacy EWR handler dispatch key from explicit arguments.

    Equation / rule: Concatenate code, multigauge or single, and kind with hyphens.

    Parameters
    ----------
    code : str
        EWR code [identifier], scalar.
    multigauge : bool
        Partner-gauge flag [dimensionless]; truthiness selects the literal label.
    kind : scalar
        Flow/level/volume kind [label]; formatted with Python string conversion.

    Returns
    -------
    str
        Dispatch identifier [label]. None becomes None text, NaN becomes nan text,
        and unexpected kind values are formatted unchanged rather than validated.

    Assumptions
    -----------
    No module constant or handler registry is consulted. No index, timezone or
    scientific boundary applies. Formatting arbitrary objects retains their own
    string behavior. No input is mutated.

    References
    ----------
    pre-refactor:src/server.py::_dispatch_key. Scientific provenance or
    suitability of this retained convention: TBD, needs scientist review."""
    return f"{code}-{'multigauge' if multigauge else 'single'}-{kind}"


def ewr_params(sheet: pd.DataFrame | None) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Project a supplied parameter sheet to per-EWR display parameters.

    Equation / rule: Stringify gauge/code/planning-unit keys, keep the first duplicate, coerce
    numeric fields, truncate duration via int and round MRIP to four places.

    Parameters
    ----------
    sheet : DataFrame or None
        Gauge, Code, PlanningUnitName [labels], TargetFrequency [% years], Duration
        [days], MaxInter-event [years], optional FlowLevelVolume [kind label].
        Arbitrary row index/timezone is ignored; None means no parameters.

    Returns
    -------
    dict[tuple[str, str, str], dict]
        T [% years], dur [days], mrip [years], kind [label] per first key.
        Missing/invalid numeric cells become None; absent kind column defaults F.
        None/empty sheet yields {} if required key columns exist.

    Assumptions
    -----------
    Present missing kind values are stringified, not defaulted. First duplicates
    win even when missing; negative/fractional values are not range checked, and
    infinite duration can raise during int conversion. No dates or inclusive
    thresholds are evaluated. No input is mutated.

    References
    ----------
    pre-refactor:src/build_dashboard.py::ewr_params. Scientific provenance or
    suitability of this retained convention: TBD, needs scientist review."""
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
