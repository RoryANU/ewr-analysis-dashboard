"""Optional observed-gauge acquisition, routing audit support, and raw range cache."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".observed_cache"
PARAMETER_SHEET = ROOT / "data/reference/parameter_sheet.csv"
DEFAULT_TIMEOUT = 45.0
DEFAULT_TTL = 24 * 60 * 60
DEFAULT_RETRIES = 2
_CACHE_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_CACHE_LOCKS_GUARD = threading.Lock()


@dataclass
class GaugeResult:
    frame: pd.DataFrame
    status: str
    message: str = ""


def sdl_gauges(sdl: str) -> list[str]:
    """Return gauges in an SDL, including partner gauges named by Multigauge."""
    params = pd.read_csv(PARAMETER_SHEET, dtype=str).fillna("")
    selected = params[params["SWSDLName"].str.strip() == sdl.strip()]
    gauges = {value for value in selected["Gauge"].str.strip() if re.fullmatch(r"(?:A)?\d+[A-Z]*", value, flags=re.I)}
    return sorted(gauges)


def _source_for(gauge: str) -> str | None:
    params = pd.read_csv(PARAMETER_SHEET, dtype=str).fillna("")
    rows = params[params["Gauge"].str.strip() == gauge]
    sources = {s.strip().upper() for s in rows["State"] if s.strip()}
    catalog = _catalog_sources(gauge)
    if catalog.intersection(sources):
        return next(iter(sorted(catalog.intersection(sources))))
    return next(iter(sources)) if len(sources) == 1 else None


def _catalog_sources(gauge: str) -> set[str]:
    from mdba_gauge_getter import gauge_getter

    return set(gauge_getter.get_states_for_gauge(gauge))


def _raw_fetch(gauge: str, start: date, end: date, var: str) -> pd.DataFrame:
    """Fetch through the package router, bypassing it for audited state mismatches."""
    from mdba_gauge_getter import gauge_getter as getter

    source = _source_for(gauge)
    if source not in {"NSW", "VIC", "QLD"}:
        raise NotImplementedError(f"Observed source unavailable for {gauge} ({source or 'unknown'})")
    catalog = _catalog_sources(gauge)
    if source in catalog:
        return getter.gauge_pull([gauge], start, end, var=var, interval="day", data_type="mean")

    # Use the package's endpoint/payload implementation while choosing the repo's audited state.
    source_codes = {"NSW": "CP", "VIC": "PUBLISH", "QLD": "AT"}
    response = getter.call_state_api(source, [gauge], start, end, source_codes[source], var, "day", "mean")
    rows = getter.extract_data(source, response)
    return pd.DataFrame(rows, columns=["DATASOURCEID", "SITEID", "SUBJECTID", "DATETIME", "VALUE", "QUALITYCODE"])


def _fetch_child(conn: Any, gauge: str, start: date, end: date, var: str) -> None:
    try:
        frame = _raw_fetch(gauge, start, end, var)
        conn.send(("ok", frame))
    except Exception as exc:  # child failures are returned as simple, picklable data
        conn.send((type(exc).__name__, str(exc)))
    finally:
        conn.close()


def _fetch_isolated(gauge: str, start: date, end: date, var: str, timeout: float) -> pd.DataFrame:
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_fetch_child, args=(child, gauge, start, end, var), daemon=True)
    process.start()
    child.close()
    if not parent.poll(timeout):
        process.terminate()
        process.join(5)
        if process.is_alive() and hasattr(process, "kill"):
            process.kill()
            process.join()
        parent.close()
        raise TimeoutError(f"Gauge request exceeded {timeout:g}s")
    kind, payload = parent.recv()
    process.join(5)
    if process.is_alive():
        process.terminate()
        process.join()
    parent.close()
    if kind != "ok":
        raise RuntimeError(f"{kind}: {payload}")
    return payload


def _cache_paths(gauge: str, var: str) -> tuple[Path, Path]:
    key = hashlib.sha256(f"{gauge}\0{var}".encode()).hexdigest()
    return CACHE_DIR / f"{key}.csv", CACHE_DIR / f"{key}.json"


def _read_cache(gauge: str, var: str) -> tuple[pd.DataFrame, dict[str, Any]] | None:
    data_path, meta_path = _cache_paths(gauge, var)
    if not data_path.exists() or not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        frame = pd.read_csv(data_path, parse_dates=["DATETIME"])
        return frame, meta
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _write_cache(gauge: str, var: str, frame: pd.DataFrame, start: date, end: date) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data_path, meta_path = _cache_paths(gauge, var)
    tmp_data = data_path.with_suffix(".csv.tmp")
    tmp_meta = meta_path.with_suffix(".json.tmp")
    frame.to_csv(tmp_data, index=False)
    tmp_meta.write_text(json.dumps({"start": start.isoformat(), "end": end.isoformat(), "fetched_at": time.time()}), encoding="utf-8")
    os.replace(tmp_data, data_path)
    os.replace(tmp_meta, meta_path)


def _slice(frame: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    dates = pd.to_datetime(frame["DATETIME"]).dt.date
    return frame.loc[(dates >= start) & (dates <= end)].reset_index(drop=True)


def _fetch_with_retries(gauge: str, start: date, end: date, var: str, timeout: float, retries: int) -> pd.DataFrame:
    for attempt in range(retries + 1):
        try:
            return _fetch_isolated(gauge, start, end, var, timeout)
        except NotImplementedError:
            raise
        except Exception:
            if attempt >= retries:
                raise
            time.sleep(min(0.5 * (2**attempt), 4.0))
    raise RuntimeError("unreachable retry state")


def _fetch_gauge_cached(
    gauge: str,
    start: date,
    end: date,
    var: str = "F",
    *,
    refresh: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    ttl: float = DEFAULT_TTL,
    retries: int = DEFAULT_RETRIES,
) -> GaugeResult:
    """Fetch one gauge with a hard subprocess deadline and an extending raw-data cache."""
    if var not in {"F", "L"}:
        return GaugeResult(pd.DataFrame(), "unsupported", f"Unsupported variable {var}")
    if start > end:
        return GaugeResult(pd.DataFrame(), "error", "Start date must not follow end date")
    cached = None if refresh else _read_cache(gauge, var)
    needed: list[tuple[date, date]] = []
    cached_frame = pd.DataFrame()
    if cached:
        cached_frame, meta = cached
        covered_start, covered_end = date.fromisoformat(meta["start"]), date.fromisoformat(meta["end"])
        fresh = time.time() - float(meta["fetched_at"]) < ttl
        if fresh:
            if start < covered_start:
                needed.append((start, min(end, covered_start - timedelta(days=1))))
            if end > covered_end:
                needed.append((max(start, covered_end + timedelta(days=1)), end))
            if not needed:
                result = _slice(cached_frame, start, end)
                return GaugeResult(result, "ok" if not result.empty else "empty", "" if not result.empty else "No data in requested period")
    if refresh or not cached or (cached and time.time() - float(cached[1]["fetched_at"]) >= ttl):
        needed = [(start, end)]
        cached_frame = pd.DataFrame()

    fetched: list[pd.DataFrame] = []
    try:
        for left, right in needed:
            if left <= right:
                fetched.append(_fetch_with_retries(gauge, left, right, var, timeout, retries))
        combined = (
            pd.concat([cached_frame, *fetched], ignore_index=True)
            if (not cached_frame.empty or any(not f.empty for f in fetched))
            else pd.DataFrame(columns=["DATASOURCEID", "SITEID", "SUBJECTID", "DATETIME", "VALUE", "QUALITYCODE"])
        )
        if not combined.empty:
            combined["DATETIME"] = pd.to_datetime(combined["DATETIME"])
            combined = combined.drop_duplicates(["DATASOURCEID", "SITEID", "SUBJECTID", "DATETIME"], keep="last").sort_values("DATETIME").reset_index(drop=True)
        covered_start = min(start, date.fromisoformat(cached[1]["start"])) if cached and not refresh else start
        covered_end = max(end, date.fromisoformat(cached[1]["end"])) if cached and not refresh else end
        _write_cache(gauge, var, combined, covered_start, covered_end)
        result = _slice(combined, start, end)
        if result.empty:
            return GaugeResult(result, "empty", "No data in requested period")
        return GaugeResult(result, "ok")
    except TimeoutError as exc:
        return GaugeResult(pd.DataFrame(), "timeout", str(exc))
    except NotImplementedError as exc:
        return GaugeResult(pd.DataFrame(), "unsupported", str(exc))
    except Exception as exc:
        message = str(exc)
        if re.search(r"(?:status(?: code)?[=: ]+|response code\s+)429\b|\b429\b", message, re.I):
            status = "rate_limited"
        else:
            status = "error"
        return GaugeResult(pd.DataFrame(), status, f"{type(exc).__name__}: {message}")


def fetch_gauge(
    gauge: str,
    start: date,
    end: date,
    var: str = "F",
    *,
    refresh: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    ttl: float = DEFAULT_TTL,
    retries: int = DEFAULT_RETRIES,
) -> GaugeResult:
    """Deduplicate concurrent fetches for the same gauge/variable cache entry."""
    key = (gauge, var)
    with _CACHE_LOCKS_GUARD:
        lock = _CACHE_LOCKS.setdefault(key, threading.Lock())
    with lock:
        return _fetch_gauge_cached(gauge, start, end, var, refresh=refresh, timeout=timeout, ttl=ttl, retries=retries)


def fetch_many(
    gauges: list[str],
    start: date,
    end: date,
    progress: Callable[[str, int, int], None] | None = None,
    workers: int = 4,
    *,
    refresh: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    status_progress: Callable[[str, str, GaugeResult], None] | None = None,
) -> tuple[dict[tuple[str, str], pd.DataFrame], dict[tuple[str, str], GaugeResult]]:
    """Fetch flow and level independently; report progress once both variables finish per gauge."""
    frames: dict[tuple[str, str], pd.DataFrame] = {}
    statuses: dict[tuple[str, str], GaugeResult] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(gauges) or 1))) as pool:
        pending = {pool.submit(fetch_gauge, gauge, start, end, var, refresh=refresh, timeout=timeout): (gauge, var) for gauge in gauges for var in ("F", "L")}
        completed_gauges: set[str] = set()
        done = 0
        for future in as_completed(pending):
            gauge, var = pending[future]
            try:
                result = future.result()
            except Exception as exc:
                result = GaugeResult(pd.DataFrame(), "error", f"{type(exc).__name__}: {exc}")
            statuses[(gauge, var)] = result
            if status_progress:
                status_progress(gauge, var, result)
            if result.status == "ok":
                frames[(gauge, var)] = result.frame
            if gauge not in completed_gauges and all((gauge, item) in statuses for item in ("F", "L")):
                completed_gauges.add(gauge)
                done += 1
                if progress:
                    progress("Fetching gauges", done, len(gauges))
    return frames, statuses


def to_standard_frame(results: dict[tuple[str, str], pd.DataFrame]) -> pd.DataFrame:
    """Outer-join raw gauge results onto a daily Date index with Standard time-series columns."""
    series: list[pd.Series] = []
    for (gauge, var), frame in results.items():
        if frame.empty:
            continue
        name = f"{gauge}_{'flow' if var == 'F' else 'level'}"
        dates = pd.to_datetime(frame["DATETIME"]).dt.normalize()
        values = pd.to_numeric(frame["VALUE"], errors="coerce").to_numpy()
        series.append(pd.Series(values, index=dates, name=name))
    if not series:
        return pd.DataFrame(columns=["Date"])
    joined = pd.concat(series, axis=1).sort_index()
    joined = joined.reindex(pd.date_range(joined.index.min(), joined.index.max(), freq="D"))
    joined.index.name = "Date"
    joined.insert(0, "Date", joined.index.strftime("%Y-%m-%d"))
    return joined.reset_index(drop=True)
