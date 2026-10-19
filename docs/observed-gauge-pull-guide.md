# Implementation guide: `gauge_pull()` is the only observed-data path

Branch: `collapsible-matrix` (written against `dc500c1`). Package: `mdba-gauge-getter==0.5.1`.

## The fix

All observed data, and any information about observed data, must come from one function:

```python
from mdba_gauge_getter.gauge_getter import gauge_pull

gauge_pull(gauge_numbers, start_time_user, end_time_user, var="F", interval="day", data_type="mean", data_source="state")
```

Nothing else in this repo may contact a state water service, read the getter's bundled catalog, or re-implement part of the getter's request or parse logic.

`dc500c1` ("replace getter requests with a bounded state-service client") broke this rule. It added `src/observed_source.py`, a second client that builds its own Hydstra URLs, parses its own replies and calls `get_variable_list`. It also made `src/observed.py` read the getter's `data/bom_gauge_data.csv` directly to route gauges. This guide undoes that and keeps the useful parts around `gauge_pull`.

## Current violations

| Where | What it does | Action |
| --- | --- | --- |
| `src/observed_source.py` (whole file) | Own HTTP client, endpoints, payloads, parsing, retry, `get_variable_list` record-start lookup | Delete |
| `src/observed.py:35` `_CLIENT = observed_source.StateClient()` | Instantiates that client | Remove |
| `src/observed.py` `_fetch_source` | Calls `_CLIENT.fetch(...)` per candidate state | Replace with one `gauge_pull` call |
| `src/observed.py` `catalog_states`, `sheet_states`, `candidate_states`, `_ROUTES` | Route gauges using the getter's bundled CSV and `parameter_sheet.csv` | Remove from the fetch path. `gauge_pull` routes by itself |
| `src/audit_observed_routing.py` | Reads `catalog_states` / `candidate_states` | Delete, with `docs/observed-routing-mismatches.csv` |
| `tests/test_observed_source.py`, `tests/fixtures/observed/*` | Test the replacement client and its payloads | Delete or rewrite against a stubbed `gauge_pull` |

`src/observed.py` imports `observed_source` at module level. `src/server.py:69` imports `observed`, so any leftover reference breaks the server on start.

## What to keep

These are not access paths, so they stay:

- The raw-range cache in `observed.py` (`_read_cache`, `_write_cache`, `_slice`, extension and expiry logic). It stores what `gauge_pull` returned.
- `fetch_gauge` per-key locking, `fetch_many` thread pool and progress callbacks, `to_standard_frame`.
- `sdl_gauges`. It reads `parameter_sheet.csv`, which is the repo's own data.
- `GaugeResult` and its statuses (`ok`, `empty`, `unsupported`, `error`).
- The `importlib.util.find_spec("mdba_gauge_getter")` availability checks in `server.py:2019` and `server.py:2138`. They only test that the package is installed.

## Target design

### 1. One adapter function

In `src/observed.py`, replace `_fetch_source` with a thin wrapper. It is the only place `gauge_pull` is called.

```python
def _fetch_source(gauge: str, start: date, end: date, var: str, timeout: float, retries: int) -> pd.DataFrame:
    from mdba_gauge_getter.gauge_getter import gauge_pull  # imported lazily: observed support is optional

    raw = gauge_pull([gauge], start, end, var=var, interval="day", data_type="mean", data_source="state")
    return _normalise(raw)
```

Rules for this wrapper:

- Always pass `data_source="state"`. `"bom"` forces the BOM path, which fails in this environment (see "Known limits").
- Pass one gauge per call. The cache is keyed per gauge and variable, and one failing gauge then cannot blank the others.
- Do not pass or pre-compute a state. `data_source` does not select a state. `gauge_pull` sorts gauges by its own catalog.

### 2. Normalise the output

`gauge_pull` returns columns `DATASOURCEID, SITEID, SUBJECTID, DATETIME, VALUE, QUALITYCODE`. `VALUE` is text and `DATETIME` holds `datetime.date` objects. `_normalise` should:

- Convert `VALUE` with `pd.to_numeric(..., errors="coerce")` and `DATETIME` with `pd.to_datetime`.
- Drop placeholder days. `dc500c1` found that services return `"0.000"` for days with no data, under quality 255 (all services), 201 (NSW) and 151 (VIC). The getter only drops quality >= 999, so these show up as zero flow (29,650 of 46,313 days for 412033). Drop `QUALITYCODE in {255, 201, 151}` and `>= 999` here, after the call.
- Return an empty frame with the standard columns when `gauge_pull` returns no rows.

The `UNITS` column that `dc500c1` added comes from the reply's trace metadata, which `gauge_pull` does not expose. Drop it from `RAW_COLUMNS` and bump `CACHE_VERSION` so old cache files are not reused.

### 3. Failure handling around the call

`gauge_pull` has no timeout, retry, or typed errors. Wrap it; do not reimplement it.

- **Hard timeout.** `requests.get` inside the getter has no timeout, and a thread cannot be killed. If a deadline must be enforced, run the call in a spawned subprocess and terminate it on timeout. That was the Phase 1 design. It stays compliant because the subprocess still only calls `gauge_pull`. If the deadline is only advisory, a worker thread with a future timeout is enough, but a hung call keeps its thread.
- **Retries.** Retry on `requests.ConnectionError` and `requests.HTTPError` whose message carries 500, 502, 503 or 504. Use jittered backoff. Do not retry a 429 or 408.
- **Error mapping.** `requests.HTTPError` and `JSONDecodeError` propagate from `gauge_pull`. Map them to `GaugeResult(status="error", message=...)`. `fetch_gauge` already catches `Exception`, so no new failure reaches the thread pool.
- **Empty means ambiguous.** An empty frame can mean no data in the period, an unknown station, or an unsupported state. Report it as `empty` with the message "No data returned for this gauge and period". Do not claim a cause that the call did not reveal.

### 4. Unsupported states

Remove the `candidate_states` pre-check that reports "BOM and SA are not enabled". `gauge_pull` sends SA gauges to BOM, and BOM currently fails (below). Let it run: a BOM failure arrives as an exception, and the mapping above turns it into an `error` result with the real message.

### 5. Request ranges

The earliest record in any gauge is from 1890. Decision: "all available" means 1890-01-01 to today. In `server.py` (near line 2125), replace the `date(1800, 1, 1)` start with a named constant `EARLIEST_RECORD = date(1890, 1, 1)`. Use that constant everywhere else the old 1800 start appears. Do not call `get_variable_list` to find a gauge's record start: `gauge_pull` is the only access path.

Requests that long may still be slow (`dc500c1` measured 35 to 60 s before the first byte from 1800). Keep `DEFAULT_TIMEOUT` (90 s) at least that large, and raise it if a live fetch from 1890 times out.

## Known limits of `gauge_pull` 0.5.1

`gauge_pull` is the source of truth. These limits are accepted, not bugs to work around. They come from `docs/observed-data-spike.md` and the code in the installed package:

- **BOM fallback is broken.** When a state returns nothing, `gauge_pull` falls back to BOM, and `bomwater` 0.0.8 raises `FileNotFoundError: ~/bom_water/cache/stations.json` on a fresh cache. Out-of-record periods therefore surface as an error, not an empty frame. Map that exception to `empty` only after you have confirmed the message; do not blanket-catch `FileNotFoundError`.
- **Wrong zero flows** are fixed only by the post-filter in step 2.
- **No per-state selection.** A gauge the catalog files under the wrong state (422016 is NSW in the catalog and NSW;QLD in the parameter sheet; 422030 is listed QLD in the sheet but served by WaterNSW) is routed by the catalog alone. The 21 known differences are in `docs/observed-routing-mismatches.csv`. Do not work around this in code. Document it where the observed feature is described.

## Enforcing the rule

Add a test that fails if anything else reaches the data:

1. Search `src/` for the strings `realtimedata.waternsw`, `data.water.vic`, `water-monitoring.information.qld`, `webservice.exe`, `webservice.pl`, `get_ts_traces`, `get_variable_list` and `bom_gauge_data.csv`. The only hits allowed are in this guide and the spike notes.
2. Search `src/` for `import requests` and `from mdba_gauge_getter` outside `observed.py`. Importing `mdba_gauge_getter.gauge_getter.gauge_pull` inside the one adapter function is the only allowed use of the package.
3. Run `fetch_gauge` with `gauge_pull` monkeypatched at `mdba_gauge_getter.gauge_getter.gauge_pull`, and assert the stub received `data_source="state"`, a one-item gauge list, and the requested dates.

Tests that replace `StateClient` with `FakeClient` (`tests/test_observed.py`) move to the same stub. Keep the quality-code, cache-extension and status-mapping cases; they exercise code that stays.

## Order of work

1. Add the wrapper and `_normalise`; point `_fetch_source` at it.
2. Delete `observed_source.py`, `audit_observed_routing.py`, the routing CSV and the routing functions in `observed.py`.
3. Rewrite the tests and delete the HTTP-stub and fixture tests that only covered the removed client.
4. Add the enforcement test.
5. Update `README.md` (line 100 area) and the Phase 2 section of `docs/observed-data-spike.md` so neither describes the replacement client.
6. Run the full suite, then run one live fetch for NSW 412033 and one for VIC 415203 and check that the zero-flow placeholder days are gone.

## Not verified

I did not run `gauge_pull` or the test suite while writing this. The quality codes 255, 201 and 151 and the 35 to 60 s figure come from the `dc500c1` commit message and the spike notes. Confirm them against a live call in step 6.
