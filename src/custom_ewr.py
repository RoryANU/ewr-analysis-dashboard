"""User EWRs: validated definitions, file-based parameter injection and py_ewr evaluation of uploaded daily series.

The server registry is authoritative; no browser state or JSON sharing is implemented here.
"""

import calendar
import copy
import hashlib
import json
import math
import multiprocessing
import os
import re
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
from py_ewr import data_inputs, evaluate_EWRs

HANDLERS = {
    "F": {
        "flow_handle": "flow",
        "lowflow_handle": "low flow",
        "ctf_handle": "cease to flow",
        "flow_handle_anytime": "flow",
        "flow_handle_check_ctf": "flow-ctf",
        "rise_and_fall_handle": "rise_fall",
        "flow_handle_multi": "multi-gauge-flow",
        "lowflow_handle_multi": "multi-gauge-low flow",
        "ctf_handle_multi": "multi-gauge-cease to flow",
    },
    "V": {"cumulative_handle": "cumulative", "cumulative_handle_multi": "multi-gauge-cumulative"},
    "L": {"level_handle": "level", "rise_and_fall_handle": "rise_fall"},
    "S": {"flow_handle": "flow", "lowflow_handle": "low flow"},
}
SERIES = {"F": "flow", "V": "flow", "L": "level", "S": "salinity"}
CODE_RE = re.compile(r"^U_[A-Za-z0-9]{1,20}(_[A-Za-z0-9]{1,8})?$")
ID_RE = re.compile(r"^[a-z0-9_]{1,24}$")
SCN_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_LOCK = threading.RLock()
TARGETS = ["TargetFrequency", "TargetFrequencyMin", "TargetFrequencyMax"]
HIDDEN = {
    "PlanningUnitID",
    "PlanningUnitName",
    "LTWPShortName",
    "CompliancePoint/Node",
    "Gauge",
    "State",
    "SWSDLName",
    "Catchment_BPRegion",
    "GaugeType",
    "Latitude",
    "Longitude",
}
INTEGER_FIELDS = {
    "StartMonth",
    "EndMonth",
    "StartDay",
    "EndDay",
    "Duration",
    "MinSpell",
    "MaxSpell",
    "WithinEventGapTolerance",
    "EventsPerYear",
    "AccumulationPeriod",
    "NonFlowSpell",
    "FlowThresholdMin",
    "FlowThresholdMax",
    "VolumeThreshold",
}
LABELS = {
    "StartMonth": "Window starts: month",
    "StartDay": "Window starts: day",
    "EndMonth": "Window ends: month",
    "EndDay": "Window ends: day",
    "FlowThresholdMin": "Lower limit",
    "FlowThresholdMax": "Upper limit",
    "LevelThresholdMin": "Lower level",
    "LevelThresholdMax": "Upper level",
    "Duration": "Required days",
    "MinSpell": "Shortest run that counts",
    "MaxSpell": "Longest acceptable spell",
    "WithinEventGapTolerance": "Days below threshold allowed inside an event",
    "EventsPerYear": "Events needed per year",
    "VolumeThreshold": "Volume (ML)",
    "AccumulationPeriod": "Accumulation period (days)",
    "DrawdownRate": "Maximum level fall per day",
    "NonFlowSpell": "Preceding dry spell (days)",
    "CtfThreshold": "Dry flow limit",
    "Multigauge": "Second gauge",
    "TargetFrequency": "Target: percent of years achieved",
    "TargetFrequencyMin": "Acceptable frequency minimum",
    "TargetFrequencyMax": "Acceptable frequency maximum",
    "MaxInter-event": "Maximum gap between successful events (years)",
    "RateOfRiseMax1": "Flow rise ratio below the second threshold",
    "RateOfRiseMax2": "Flow rise ratio above the second threshold",
    "RateOfFallMin": "Flow fall ratio",
    "RateOfRiseThreshold1": "Minimum flow for a rise event",
    "RateOfRiseThreshold2": "Second flow rise threshold",
    "RateOfRiseRiverLevel": "Level rise per day",
    "RateOfFallRiverLevel": "Level fall per day",
}
DEFAULTS: dict[str, Any] = {
    "StartMonth": 7,
    "EndMonth": 6,
    "FlowThresholdMin": 0,
    "FlowThresholdMax": 1000000,
    "WithinEventGapTolerance": 0,
    "EventsPerYear": 1,
    "MaxInter-event": 0,
    "CtfThreshold": 5,
    "NonFlowSpell": 0,
}
# Open-ended limits are capped so every value survives py_ewr's Int64/Float64 coercion of the merged sheet.
MAX_PARAM = 10**9
BOUNDS = {
    "StartMonth": (1, 12),
    "EndMonth": (1, 12),
    "StartDay": (1, 31),
    "EndDay": (1, 31),
    "Duration": (1, 366),
    "MinSpell": (1, 366),
    "MaxSpell": (1, MAX_PARAM),
    "WithinEventGapTolerance": (0, 30),
    "EventsPerYear": (1, 12),
    "AccumulationPeriod": (1, 366),
    "NonFlowSpell": (0, MAX_PARAM),
    "FlowThresholdMin": (0, MAX_PARAM),
    "FlowThresholdMax": (0, MAX_PARAM),
    "VolumeThreshold": (0, MAX_PARAM),
    "CtfThreshold": (0, MAX_PARAM),
    "DrawdownRate": (0, MAX_PARAM),
    "MaxInter-event": (0, MAX_PARAM),
    **{key: (0, 100) for key in TARGETS},
}


class ValidationError(ValueError):
    """Invalid user input, with a message per field (`errors`) for the page to show beside each one."""

    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("; ".join(f"{key}: {value}" for key, value in errors.items()))


class SkipScenario(ValueError):
    """An uploaded scenario lacks the series needed by this definition."""


def _store() -> Path:
    """The server's upload store, read at call time so tests (and worker processes) can redirect it."""
    import server

    return server.STORE


def _registry_path() -> Path:
    return _store() / "uewr.json"


def _sheets() -> Path:
    return _store() / "uewr_sheets"


@lru_cache(maxsize=1)
def _base_sheet() -> pd.DataFrame:
    """py_ewr's own parameter sheet, every cell as text (blank cells empty), as the base for merged user sheets."""
    return pd.read_csv(Path(data_inputs.BASE_PATH) / "parameter_metadata/parameter_sheet.csv", dtype=str, encoding="cp1252").fillna("")


def base_sheet_raw() -> pd.DataFrame:
    """A private copy of the raw base sheet, safe to modify."""
    return _base_sheet().copy(deep=True)


@lru_cache(maxsize=1)
def review_context() -> dict[str, Any]:
    """Use the review's gauges, gauge/PU pairs, objectives and regimes, with the page's gauge alias."""
    import server
    from flow_regime_mappings import regimes_for

    pairs: set[tuple[str, str]] = set()
    objectives: set[str] = set()
    regimes: set[str] = set()
    for name in ("frequency_ratio.csv", "etp.csv"):
        frame = pd.read_csv(
            server.ROOT / "data/results" / name,
            dtype=str,
            usecols=lambda c: c in {"Gauge", "PlanningUnitName", "EcoObj", "FlowRegime", "EWR", "State"},
        ).fillna("")
        pairs.update((str(g).replace("A4260527", "A4260524"), pu) for g, pu in zip(frame.Gauge, frame.PlanningUnitName, strict=True))
        objectives.update(o for value in frame.EcoObj for o in value.split("+") if o)
        if "FlowRegime" in frame:
            regimes.update(value for value in frame.FlowRegime if value)
        else:
            # ETP-only rows get these same labels in build_dashboard.aggregate_ewr_rows.
            for row in frame[["EWR", "State"]].drop_duplicates().itertuples():
                regimes.update(regimes_for(row.EWR, row.State) or ["Unmapped"])
    return {"pairs": pairs, "gauges": {g for g, _ in pairs}, "objectives": objectives, "regimes": regimes}


@lru_cache(maxsize=1)
def schema() -> dict[str, Any]:
    """For each kind and handler: its parameter fields (from py_ewr), labels, defaults, bounds and whether it takes a second gauge."""
    result: dict[str, Any] = {}
    for kind, handlers in HANDLERS.items():
        result[kind] = {}
        for handler, category in handlers.items():
            components = data_inputs.get_EWR_components(category)
            fields = list(dict.fromkeys([f for f in components if f not in {"FlowLevelVolume", "Multigauge"}] + TARGETS))
            defaults = {f: DEFAULTS[f] for f in fields if f in DEFAULTS}
            defaults["MinSpell"] = 1 if handler.startswith("lowflow_handle") else "Duration"
            result[kind][handler] = {
                "category": category,
                "fields": fields,
                "labels": {f: LABELS.get(f, f) for f in fields},
                "defaults": defaults,
                "bounds": {f: {"min": BOUNDS.get(f, (None, None))[0], "max": BOUNDS.get(f, (None, None))[1], "integer": f in INTEGER_FIELDS} for f in fields},
                "required": ["Duration", "TargetFrequency"],
                "second_gauge": handler.endswith("_multi"),
            }
    return result


def load_registry() -> dict[str, dict[str, Any]]:
    """The saved user EWR definitions by id (empty before the first save)."""
    with _LOCK:
        path = _registry_path()
        return json.loads(path.read_text()) if path.exists() else {}


def _number(value: Any) -> float:
    """value as a finite float; ValueError for booleans (which float() would accept), NaN and infinities."""
    if isinstance(value, bool):
        raise ValueError
    result = float(value)
    if not math.isfinite(result):
        raise ValueError
    return result


SPEC_FIELDS = {
    "id",
    "ver",
    "code",
    "name",
    "kind",
    "handler",
    "gauge",
    "second_gauge",
    "pus",
    "objectives",
    "regime",
    "params",
    "salinity",
    "created",
    "updated",
}


def validate_spec(d: Any, sheet_raw: pd.DataFrame | None = None) -> dict[str, Any]:
    """Whitelist and normalise a spec; reject invalid values before any py_ewr call, especially blank MinSpell.

    Every problem found is reported at once, keyed by field (`params.<Field>` for parameters), in a ValidationError.
    """
    errors: dict[str, str] = {}
    if not isinstance(d, dict):
        raise ValidationError({"spec": "Expected an object"})
    for key in d.keys() - SPEC_FIELDS:
        errors[key] = "Unknown field"

    kind, handler, code = d.get("kind"), d.get("handler"), d.get("code")
    if not isinstance(kind, str) or kind not in HANDLERS:
        errors["kind"] = "Choose F, V, L or S"
    if not isinstance(handler, str) or handler not in HANDLERS.get(kind if isinstance(kind, str) else "", {}):
        errors["handler"] = "Handler is not offered for this kind"
    if not isinstance(code, str) or not CODE_RE.fullmatch(code):
        errors["code"] = "Use U_ followed by 1–20 letters/digits and an optional underscore suffix of 1–8 letters/digits; no hyphens"
    spec_id = d.get("id")
    if spec_id is not None and (not isinstance(spec_id, str) or not ID_RE.fullmatch(spec_id)):
        errors["id"] = "Invalid user EWR id"
    name = d.get("name", "")
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        errors["name"] = "A name of 1–80 characters is required"

    # where it applies: a review gauge and planning units, with the review's objectives and regime
    raw = base_sheet_raw() if sheet_raw is None else sheet_raw
    context = review_context()
    gauge = d.get("gauge")
    if not isinstance(gauge, str) or gauge not in context["gauges"] or gauge not in set(raw.Gauge):
        errors["gauge"] = "Choose a review gauge with parameter-sheet rows"
    pus = d.get("pus")
    available = set(raw.loc[raw.Gauge == gauge, "PlanningUnitName"]) if isinstance(gauge, str) else set()
    if not isinstance(pus, list) or not pus or any(not isinstance(pu, str) or pu not in available or (gauge, pu) not in context["pairs"] for pu in pus):
        errors["pus"] = "Choose at least one review planning unit at this gauge"
    objectives = d.get("objectives", [])
    if not isinstance(objectives, list) or any(not isinstance(o, str) or o not in context["objectives"] for o in objectives):
        errors["objectives"] = "Choose objectives from the review"
    regime = d.get("regime")
    if regime is not None and (not isinstance(regime, str) or regime not in context["regimes"]):
        errors["regime"] = "Choose a regime from the review"

    # a code must be unique among other user EWRs and must not shadow a stock EWR
    registry = load_registry()
    if any(s["code"] == code and s["id"] != spec_id for s in registry.values()) or (isinstance(code, str) and (raw.Code == code).any()):
        errors["code"] = "Code is already in use"

    params_input = d.get("params", {})
    if not isinstance(params_input, dict):
        errors["params"] = "Expected an object"
        params_input = {}
    if "handler" in errors or "kind" in errors:
        raise ValidationError(errors)  # the parameters cannot be checked without a handler's schema
    assert isinstance(kind, str) and isinstance(handler, str)

    tds_input = kind == "S" and isinstance(d.get("salinity"), dict) and d["salinity"].get("unit") == "TDS"
    params = _parse_params(params_input, schema()[kind][handler]["fields"], handler, tds_input, errors)
    _check_param_rules(params, kind, handler, errors)
    second = _check_second_gauge(d.get("second_gauge"), gauge, spec_id, handler, context, raw, errors)
    if handler == "rise_and_fall_handle":
        _check_rise_or_fall(params, kind, errors)
    salinity = _normalise_salinity(d.get("salinity"), kind, params, errors)

    if errors:
        raise ValidationError(errors)
    assert isinstance(pus, list)
    return {
        "id": spec_id,
        "code": code,
        "name": name.strip(),
        "kind": kind,
        "handler": handler,
        "gauge": gauge,
        "second_gauge": second,
        "pus": list(dict.fromkeys(pus)),
        "objectives": list(dict.fromkeys(objectives)),
        "regime": regime,
        "params": params,
        "salinity": salinity,
    }


def _parse_params(params_input: dict, fields: list[str], handler: str, tds_input: bool, errors: dict[str, str]) -> dict[str, Any]:
    """Each of the handler's fields as a bounded number (an int for integer fields), its default when blank, or None."""
    for key in params_input.keys() - set(fields):
        errors[f"params.{key}"] = "Parameter is not used by this handler"

    params: dict[str, Any] = {}
    for field in fields:
        value = params_input.get(field)
        if value is None or value == "":
            value = DEFAULTS.get(field)
        if field == "MinSpell" and value is None:
            # Duration precedes MinSpell in every handler's field list, so it has already been parsed
            value = 1 if handler.startswith("lowflow_handle") else params.get("Duration")
        if value is None:
            params[field] = None
            continue
        try:
            number = _number(value)
            # TDS thresholds are converted to EC later; only the converted value has to be whole
            integral = field in INTEGER_FIELDS and not (tds_input and field in ("FlowThresholdMin", "FlowThresholdMax"))
            if integral and not number.is_integer():
                raise ValueError
            lower, upper = BOUNDS.get(field, (None, None))
            if (lower is not None and number < lower) or (upper is not None and number > upper):
                raise ValueError
            params[field] = int(number) if integral else number
        except (ValueError, TypeError, OverflowError):
            errors[f"params.{field}"] = "Enter a finite number within the allowed bounds" + (" (integer required)" if field in INTEGER_FIELDS else "")
            params[field] = None
    return params


def _check_param_rules(params: dict[str, Any], kind: str, handler: str, errors: dict[str, str]) -> None:
    """Rules between parameters: required fields, spell lengths, the timing window, threshold ranges, targets and volumes."""
    for field in ("Duration", "MinSpell", "TargetFrequency"):
        if params.get(field) is None:
            errors.setdefault(f"params.{field}", "Required")

    duration, minspell = params.get("Duration"), params.get("MinSpell")
    if duration is not None and minspell is not None and minspell > duration:
        errors["params.MinSpell"] = "MinSpell must be between 1 and Duration"
    if params.get("MaxSpell") is not None and duration is not None and params["MaxSpell"] < duration:
        errors["params.MaxSpell"] = "MaxSpell must be at least Duration"
    if params.get("DrawdownRate") is not None and params["DrawdownRate"] <= 0:
        errors["params.DrawdownRate"] = "Must be positive"

    _check_timing_window(params, handler, duration, errors)

    for prefix in ("Flow", "Level"):
        low, high = params.get(prefix + "ThresholdMin"), params.get(prefix + "ThresholdMax")
        if prefix == "Level" and handler == "level_handle" and (low is None or high is None):
            errors[f"params.{prefix}ThresholdMin"] = "Both level limits are required"
        if low is not None and high is not None and high <= low:
            errors[f"params.{prefix}ThresholdMax"] = "Upper limit must exceed lower limit"

    target = params.get("TargetFrequency")
    target_min, target_max = params.get("TargetFrequencyMin"), params.get("TargetFrequencyMax")
    if target is not None and ((target_min is not None and target_min > target) or (target_max is not None and target_max < target)):
        errors["params.TargetFrequency"] = "Target must lie within the acceptable frequency range"

    if kind == "V":
        if params.get("VolumeThreshold") is None or params["VolumeThreshold"] <= 0:
            errors["params.VolumeThreshold"] = "A positive volume is required"
        if params.get("AccumulationPeriod") is None or (duration is not None and params["AccumulationPeriod"] < duration):
            errors["params.AccumulationPeriod"] = "AccumulationPeriod must be at least Duration"


def _check_timing_window(params: dict[str, Any], handler: str, duration: Any, errors: dict[str, str]) -> None:
    """Valid calendar days, a window that can hold the required duration, and a whole-year window for across-year spells."""
    for prefix in ("Start", "End"):
        month, day = params.get(prefix + "Month"), params.get(prefix + "Day")
        if month is not None and day is not None and day > calendar.monthrange(2000, month)[1]:
            errors[f"params.{prefix}Day"] = "Invalid day for this month"
    if any(f"params.{f}" in errors for f in ("StartMonth", "EndMonth", "StartDay", "EndDay")):
        return

    # 2000 is a leap year, so 29 February is a valid day and a whole-year window has 366 days
    sm, em = params["StartMonth"], params["EndMonth"]
    sd, ed = params.get("StartDay") or 1, params.get("EndDay") or calendar.monthrange(2000, em)[1]
    start, end = date(2000, sm, sd), date(2000, em, ed)
    if sm == em and sd > ed:
        errors["params.EndDay"] = "End day must not precede start day within the same month"
    window_days = (end - start).days + 1 if end >= start else 366 - (start - end).days + 1  # the second form wraps the year end
    if duration is not None and duration > window_days:
        errors["params.Duration"] = "Duration is longer than the timing window"
    if handler == "flow_handle_anytime" and (sm, sd, em, ed) != (7, 1, 6, 30):
        errors["params.StartMonth"] = "Across-year spells require a July–June whole-year window"


def _check_second_gauge(second: Any, gauge: Any, spec_id: Any, handler: str, context: dict[str, Any], raw: pd.DataFrame, errors: dict[str, str]) -> Any:
    """A multigauge handler needs a different review gauge (with stored flows, for a new definition); others accept none."""
    if not handler.endswith("_multi"):
        if second is not None:
            errors["second_gauge"] = "Only multigauge handlers accept a second gauge"
        return second
    if not isinstance(second, str) or second == gauge or second not in context["gauges"] or second not in set(raw.Gauge):
        errors["second_gauge"] = "Choose a different review gauge with stored flows"
    elif spec_id is None and not _flows_stored_together(gauge, second):
        # Existing definitions remain valid after scenarios are forgotten; evaluation reports missing series as a skip.
        errors["second_gauge"] = "Both gauges need stored flows in at least one uploaded scenario"
    return second


def _flows_stored_together(gauge: Any, second: str) -> bool:
    """True when some uploaded scenario's stored flows include both gauges."""
    for path in _store().glob("*.csv"):
        if path.stem.endswith(("_level", "_salinity")):
            continue
        columns = pd.read_csv(path, nrows=0).columns
        if gauge in columns and second in columns:
            return True
    return False


def _check_rise_or_fall(params: dict[str, Any], kind: str, errors: dict[str, str]) -> None:
    """A rise/fall rule limits exactly one direction: all of the rise limits, or the fall limit."""
    rise = ["RateOfRiseRiverLevel"] if kind == "L" else ["RateOfRiseMax1", "RateOfRiseMax2", "RateOfRiseThreshold1", "RateOfRiseThreshold2"]
    fall = "RateOfFallRiverLevel" if kind == "L" else "RateOfFallMin"
    has_rise = any(params.get(f) is not None for f in rise)
    has_fall = params.get(fall) is not None
    if has_rise == has_fall or (has_rise and any(params.get(f) is None for f in rise)):
        errors["params.rates"] = "Supply all rise limits or one fall limit, for exactly one direction"


def _normalise_salinity(salinity: Any, kind: str, params: dict[str, Any], errors: dict[str, str]) -> dict[str, Any] | None:
    """A salinity EWR's units as {"unit": "EC", "tds_factor": f}, converting TDS thresholds in `params` to EC in place."""
    if kind != "S":
        if salinity is not None:
            errors["salinity"] = "Only salinity EWRs accept salinity units"
        return None
    if not isinstance(salinity, dict) or salinity.get("unit") not in ("EC", "TDS") or salinity.keys() - {"unit", "tds_factor"}:
        errors["salinity"] = "Specify EC or TDS and a positive TDS-to-EC factor"
        return salinity
    try:
        factor = _number(salinity.get("tds_factor", 0.6))
        if factor <= 0:
            raise ValueError
        if salinity["unit"] == "TDS":
            _convert_tds_thresholds(params, factor, errors)
        return {"unit": "EC", "tds_factor": factor}
    except (ValueError, TypeError, OverflowError):
        errors["salinity.tds_factor"] = "Factor must be finite and positive"
        return salinity


def _convert_tds_thresholds(params: dict[str, Any], factor: float, errors: dict[str, str]) -> None:
    # py_ewr coerces FlowThreshold* to Int64: reject lossy conversions rather than silently rounding EC.
    for key in ("FlowThresholdMin", "FlowThresholdMax"):
        if params.get(key) is None:
            continue
        value = params[key] * factor
        if not math.isclose(value, round(value), rel_tol=0, abs_tol=1e-9):
            errors[f"params.{key}"] = "Converted EC must be a whole number (py_ewr threshold requirement)"
        elif value > MAX_PARAM:
            errors[f"params.{key}"] = "Converted EC is above the allowed maximum"
        else:
            params[key] = round(value)


def _atomic_write(path: Path, data: bytes) -> None:
    """Write via a temporary file and rename, so readers never see a partly written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_bytes(data)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _invalidate(code: str) -> None:
    """Drop every cached evaluation and job result of a user EWR code after its definition changes or is deleted."""
    import server

    for key in list(server._EVAL_CACHE):
        if key[2] == code:
            server._EVAL_CACHE.pop(key, None)

    # Completed HTTP jobs are another source of old result rows after a browser reconnect.
    for job in list(server.JOBS.values()):
        for scn, rows in job.get("results", {}).items():
            job["results"][scn] = [row for row in rows if row.get("ewr") != code]
        for family in ("skipped", "errors"):
            for codes in job.get(family, {}).values():
                codes.pop(code, None)


def cache_evaluation(spec: dict, key: tuple, result: dict) -> bool:
    """Publish diagnostics under the registry lock so an in-flight request cannot undo deletion."""
    import server

    with _LOCK:
        current = load_registry().get(spec["id"])
        if current is None or current["ver"] != spec["ver"]:
            _prune_sheets()
            return False
        server._EVAL_CACHE[key] = result
        return True


def save_spec(d: dict) -> dict[str, Any]:
    """Validate and save a new definition, or a new version of an existing one (same id); return the saved definition."""
    with _LOCK:
        registry = load_registry()
        spec = validate_spec(d)
        spec_id = spec["id"]
        if spec_id is not None and spec_id not in registry:
            raise ValidationError({"id": "User EWR does not exist"})
        old = registry.get(spec_id, {})
        spec["id"] = spec_id or "u_" + uuid.uuid4().hex[:12]
        spec["ver"] = old.get("ver", 0) + 1
        now = datetime.now(timezone.utc).isoformat()
        spec["created"], spec["updated"] = old.get("created", now), now
        registry[spec["id"]] = spec
        _write_registry(registry)
        if old:
            _invalidate(old["code"])
        return copy.deepcopy(spec)


def _write_registry(registry: dict[str, dict[str, Any]]) -> None:
    _atomic_write(_registry_path(), json.dumps(registry, ensure_ascii=True, allow_nan=False, indent=2).encode())


def _merged_bytes(specs: list[dict]) -> bytes:
    """The base parameter sheet with the given user EWRs' rows appended, as py_ewr reads it (cp1252 CSV)."""
    raw = base_sheet_raw()
    rows = [sheet_rows(spec, raw) for spec in sorted(specs, key=lambda s: s["code"])]
    return pd.concat([raw, *rows], ignore_index=True).to_csv(index=False, lineterminator="\n").encode("cp1252")


def sheet_rows(spec: dict, sheet_raw: pd.DataFrame) -> pd.DataFrame:
    """Keep only location metadata from the base row; never inherit an unrelated EWR's parameters."""
    spec = validate_spec(spec, sheet_raw)
    rows = []
    for pu in sorted(spec["pus"]):
        base = sheet_raw[(sheet_raw.Gauge == spec["gauge"]) & (sheet_raw.PlanningUnitName == pu)].iloc[0]
        row = {column: base[column] if column in HIDDEN else "" for column in sheet_raw.columns}
        row.update(Code=spec["code"], FlowLevelVolume="F" if spec["kind"] == "S" else spec["kind"], Multigauge=spec["second_gauge"] or "")
        for field, value in spec["params"].items():
            if field in ("StartDay", "EndDay") or value is None:
                continue
            row[field] = format(value, ".15g")
        params = spec["params"]
        if params.get("StartDay") is not None or params.get("EndDay") is not None:
            # py_ewr ignores BOTH day limits if either is blank. Fill the other calendar boundary explicitly.
            start_day = params.get("StartDay") or 1
            end_day = params.get("EndDay") or calendar.monthrange(2000, params["EndMonth"])[1]
            row["StartMonth"] += f".{start_day:02d}"
            row["EndMonth"] += f".{end_day:02d}"
        row["EcoObj"] = "+".join(spec["objectives"])
        rows.append(row)
    return pd.DataFrame(rows, columns=sheet_raw.columns)


def merged_sheet_path(specs: list[dict]) -> Path:
    """A parameter sheet holding these user EWRs, named by its content hash so identical definitions share one file."""
    with _LOCK:
        content = _merged_bytes(specs)
        path = _sheets() / (hashlib.sha1(content).hexdigest()[:16] + ".csv")
        if not path.exists():
            _atomic_write(path, content)
        return path


@lru_cache(maxsize=8)  # each cleaned sheet is ~3 MB; every edit or tested draft has its own content-hashed path
def _clean_sheet(path: str) -> pd.DataFrame:
    """py_ewr's cleaned parameter table for a sheet file, bypassing py_ewr's own cache (which ScenarioHandler mutates)."""
    read = getattr(data_inputs.get_EWR_table, "__wrapped__", data_inputs.get_EWR_table)
    return read(path)


def read_sheet(path: str) -> pd.DataFrame:
    """Private table: ScenarioHandler mutates py_ewr's separate cached parameter tables during summaries."""
    return _clean_sheet(path).copy(deep=True)


def calc_config(specs: list[dict]) -> dict:
    """py_ewr's handler dispatch config with each user EWR's dispatch key added under its chosen handler."""
    cfg = copy.deepcopy(data_inputs.get_ewr_calc_config(None))
    for spec in specs:
        kind = "F" if spec["kind"] == "S" else spec["kind"]  # salinity runs through py_ewr's flow handlers
        key = f"{spec['code']}-{'multigauge' if spec.get('second_gauge') else 'single'}-{kind}"
        cfg.setdefault(spec["handler"], []).append(key)
    return cfg


def frames(scn_id: str, spec: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(df_F, df_L) for evaluating a user EWR on a stored scenario: its gauge(s)' series, trimmed to the observed span and with
    every calendar day present. Raises SkipScenario when the scenario cannot be evaluated for this definition."""
    import server

    if not isinstance(scn_id, str) or not SCN_RE.fullmatch(scn_id):
        raise ValidationError({"scn": "Invalid uploaded scenario id"})
    kind = SERIES[spec["kind"]]
    try:
        frame = server.load_series(scn_id, kind)
    except ValueError as exc:
        raise SkipScenario(f"no {kind} series for {spec['gauge']} in this scenario") from exc
    gauges = [spec["gauge"]] + ([spec["second_gauge"]] if spec.get("second_gauge") else [])
    if any(g not in frame or not frame[g].notna().any() for g in gauges):
        raise SkipScenario(f"no {kind} series for {', '.join(gauges)} in this scenario")
    frame = frame[gauges].copy()
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise SkipScenario("user EWRs need calendar-dated uploaded series")
    observed = frame.notna().any(axis=1)
    frame = frame.loc[observed[observed].index[0] : observed[observed].index[-1]]
    frame = frame.reindex(pd.date_range(frame.index[0], frame.index[-1], name="Date"))
    if len(frame) < 3 and spec["handler"] in ("level_handle", "rise_and_fall_handle"):
        raise SkipScenario("this handler needs at least three daily values")
    if len(gauges) > 1 and not frame.notna().all(axis=1).any():
        raise SkipScenario("the two gauges have no overlapping daily flows")
    empty = pd.DataFrame(index=frame.index)
    return (empty, frame) if kind == "level" else (frame, empty)


def calculate(spec: dict, df_F: pd.DataFrame, df_L: pd.DataFrame, rows: pd.DataFrame, cfg: dict) -> tuple[dict, dict]:
    """Dispatch through py_ewr, adapting its code-based rise/fall selection without changing public U_ codes or global handlers."""
    code, alias = spec["code"], spec["code"]
    if spec["handler"] == "rise_and_fall_handle":
        # py_ewr picks rise or fall (and flow or level) from the EWR code, so evaluate under a code that names the direction
        params = spec["params"]
        if spec["kind"] == "L":
            direction = "RRL" if params.get("RateOfRiseRiverLevel") is not None else "RFL"
        else:
            direction = "RRF" if params.get("RateOfRiseMax1") is not None else "RFF"
        alias = "U_CALC" + direction
        rows = rows.copy()
        rows["Code"] = alias
        cfg = copy.deepcopy(cfg)
        cfg[spec["handler"]].append(f"{alias}-single-{spec['kind']}")
        if spec["kind"] == "L":
            df_F = df_L  # the upstream level-rate handler uses df_F for its calendar and missing-day accounting
    results, events = evaluate_EWRs.calc_sorter(df_F, df_L, spec["gauge"], rows, cfg)
    if not results or not any(not frame.empty for frame in results.values()):
        raise SkipScenario("py_ewr returned no result")
    if alias != code:
        results = {pu: frame.rename(columns=lambda c: code + c[len(alias) :] if c.startswith(alias + "_") else c) for pu, frame in results.items()}
        events = {pu: {code if c == alias else c: values for c, values in by_code.items()} for pu, by_code in events.items()}
    return results, events


def evaluate(spec: dict, scn_id: str) -> list[dict]:
    """A user EWR on one stored scenario, as dashboard rows (server.summarise_rows), one per planning unit."""
    import server

    spec = validate_spec(spec)
    df_F, df_L = frames(scn_id, spec)
    path = merged_sheet_path([spec])
    table = read_sheet(str(path))
    rows = table[(table.Gauge == spec["gauge"]) & (table.Code == spec["code"])].copy()
    cfg = calc_config([spec])
    results, events = calculate(spec, df_F, df_L, rows, cfg)
    handler_map = {key: handler for handler, keys in cfg.items() for key in keys}
    return server.summarise_rows(
        {spec["gauge"]: results},
        {spec["gauge"]: events},
        df_F,
        df_L,
        table,
        sheet_path=str(path),
        handler_map=handler_map,
        series_override={spec["gauge"]: df_F.sum(axis=1, min_count=len(df_F.columns))} if spec["kind"] == "S" or spec.get("second_gauge") else None,
    )


def find(code: str, gauge: str) -> dict | None:
    """The saved definition with this code at this gauge, if any."""
    return next((spec for spec in load_registry().values() if spec["code"] == code and spec["gauge"] == gauge), None)


def delete_spec(spec_id: str) -> None:
    """Delete a definition with its cached evaluations and any merged sheets no other definition needs."""
    with _LOCK:
        registry = load_registry()
        old = registry.pop(spec_id, None)
        if old is None:
            raise ValidationError({"id": "User EWR does not exist"})
        _write_registry(registry)
        _invalidate(old["code"])
        _clean_sheet.cache_clear()
        _prune_sheets()


def _prune_sheets() -> None:
    """Called with the registry lock, after deletion or the last in-flight evaluation."""
    keep = set()
    for spec in load_registry().values():
        try:
            keep.add(hashlib.sha1(_merged_bytes([spec])).hexdigest()[:16])
        except ValidationError:
            pass  # no longer valid (e.g. after a py_ewr upgrade): it cannot be evaluated, and re-saving rebuilds its sheet
    pyewr_cache = getattr(data_inputs.get_EWR_table, "cache", None)
    for path in _sheets().glob("*.csv"):
        if path.stem not in keep:
            path.unlink(missing_ok=True)
            if pyewr_cache is not None:  # ScenarioHandler summaries cache each sheet path in py_ewr's TTL cache for 30 minutes
                pyewr_cache.pop(data_inputs.get_EWR_table.cache_key(str(path)), None)


def publish_job(job: dict, result: dict) -> None:
    """Recheck versions when publishing, atomically with edits/deletes and their cache purge."""
    with _LOCK:
        # Identify definitions by id: a deleted code re-created during the job also starts at version 1.
        current = load_registry()
        codes_now = {s["code"] for s in current.values()}
        for scn, rows in result.get("results", {}).items():
            result["results"][scn] = [
                row
                for row in rows
                if (spec := current.get(row.get("uewr_id"))) and row["uewr_ver"] == spec["ver"] and (row["gauge"], row["ewr"]) == (spec["gauge"], spec["code"])
            ]
        for family in ("skipped", "errors"):
            for scn, codes in result.get(family, {}).items():
                result[family][scn] = {code: reason for code, reason in codes.items() if code in codes_now}
        _prune_sheets()
        job.update(result)
        job["status"] = "done"


def sweep_sheets() -> None:
    """Startup sweep of sheets older than seven days; files can always be regenerated from the registry."""
    with _LOCK:
        for path in _sheets().glob("*.csv"):
            if path.stat().st_mtime < time.time() - 7 * 86400:
                path.unlink(missing_ok=True)


def _pair(spec: dict, scn_id: str, store_path: str) -> tuple[list[dict], str | None, str | None]:
    """Evaluate one (definition, scenario) pair as (rows, skip reason, error); runs in a worker process."""
    import server

    server.STORE = Path(store_path)  # explicit for spawned workers, including tests with an isolated STORE
    try:
        return evaluate(spec, scn_id), None, None
    except SkipScenario as exc:
        return [], str(exc), None
    except Exception as exc:
        return [], None, f"{type(exc).__name__}: {exc}"


def job_inputs(ids: Any, scns: Any) -> tuple[list[dict], list[str]]:
    """Validate a run request: saved definition ids and stored scenario ids, each list de-duplicated in order."""
    errors = {}
    registry = load_registry()
    if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or i not in registry for i in ids):
        errors["ids"] = "Choose at least one saved user EWR"
    if not isinstance(scns, list) or not scns or any(not isinstance(s, str) or not SCN_RE.fullmatch(s) for s in scns):
        errors["scns"] = "Choose at least one uploaded scenario id"
    elif any(not any((_store() / f"{s}{suffix}.csv").exists() for suffix in ("", "_level", "_salinity")) for s in scns):
        errors["scns"] = "Only uploaded scenarios with stored series can be evaluated"
    if errors:
        raise ValidationError(errors)
    return [registry[i] for i in dict.fromkeys(ids)], list(dict.fromkeys(scns))


def run_job(spec_ids: list[str], scn_ids: list[str], progress: Callable[[str, int, int], None]) -> dict[str, Any]:
    """Evaluate every definition on every scenario (in parallel where possible): rows by scenario, plus skips and errors."""
    specs, scns = job_inputs(spec_ids, scn_ids)
    pairs = [(spec, scn) for spec in specs for scn in scns]
    total = len(pairs)
    out: dict[str, Any] = {"results": {scn: [] for scn in scns}, "skipped": {}, "errors": {}}
    completed: set[int] = set()
    progress("Reading series", 0, total)

    def collect(index: int, result: tuple[list[dict], str | None, str | None]) -> None:
        spec, scn = pairs[index]
        rows, skip, error = result
        # Edits/deletion during a job must not publish obsolete results.
        current = load_registry().get(spec["id"])
        if current is None or current["ver"] != spec["ver"]:
            rows, skip, error = [], "definition changed during evaluation; re-run", None
        if skip or error:
            out["skipped" if skip else "errors"].setdefault(scn, {})[spec["code"]] = skip or error
        else:
            out["results"][scn].extend({**row, "uewr_id": spec["id"], "uewr_ver": spec["ver"]} for row in rows)
        completed.add(index)
        progress("Evaluating", len(completed), total)

    workers = os.cpu_count() or 1
    if total > 1 and workers > 1:
        try:
            with ProcessPoolExecutor(min(total, workers), mp_context=multiprocessing.get_context("spawn")) as pool:
                futures = {pool.submit(_pair, spec, scn, str(_store())): i for i, (spec, scn) in enumerate(pairs)}
                for future in as_completed(futures):
                    collect(futures[future], future.result())
        except (BrokenProcessPool, OSError):
            pass  # retry only unfinished pairs serially
    for i, (spec, scn) in enumerate(pairs):
        if i not in completed:
            collect(i, _pair(spec, scn, str(_store())))
    progress("Summarising", total, total)
    return out


def ewr_list(gauge: str) -> list[dict[str, Any]]:
    """All user kinds are discoverable; detailed flow overlays only apply to flow/volume rules."""
    result = []
    for spec in load_registry().values():
        if spec["gauge"] != gauge:
            continue
        params = spec["params"]
        result.append(
            {
                "code": spec["code"],
                "pu": spec["pus"][0],
                "pus": spec["pus"],
                "handler": spec["handler"],
                "kind": spec["kind"],
                "custom": True,
                "detailed": spec["kind"] in ("F", "V"),
                "name": spec["name"],
                "ver": spec["ver"],
                "consecutive": not spec["handler"].startswith("lowflow_handle"),
                "thr": params.get("LevelThresholdMin") if spec["kind"] == "L" else params.get("FlowThresholdMin"),
                "thr_max": params.get("LevelThresholdMax") if spec["kind"] == "L" else params.get("FlowThresholdMax"),
                "dur": params["Duration"],
                "sm": params["StartMonth"],
                "em": params["EndMonth"],
                "target": params["TargetFrequency"],
                "maxint": params["MaxInter-event"],
            }
        )
    return result


def inventory(gauge: str, entries: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """One inventory row per (user EWR, planning unit) at a gauge; pass `entries` to reuse an ewr_list result."""
    return [
        {"code": e["code"], "pu": pu, "kind": e["kind"], "handler": e["handler"], "custom": True, "detailed": e["detailed"]}
        for e in (ewr_list(gauge) if entries is None else entries)
        for pu in e["pus"]
    ]
