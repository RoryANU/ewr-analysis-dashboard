# Observed data spike: `mdba-gauge-getter`

Date: 2026-10-19
Environment: macOS arm64, Python 3.13.0; `mdba-gauge-getter` 0.5.1, `bomwater` 0.0.8, `py_ewr` 2.4.11, pandas 3.0.6.

## Findings

- The package installs and imports on Python 3.13.0 alongside the currently required `py_ewr` 2.4.11. Pip resolved `numpy==1.26.4` for py_ewr and `pandas==3.0.6`; both imported and the getter worked for state-backed queries. This is a smoke check, not full application compatibility. The package metadata classifiers still stop at Python 3.11.
- Call `mdba_gauge_getter.gauge_getter.gauge_pull(gauge_numbers, start_time_user, end_time_user, var='F', interval='day', data_type='mean', data_source='state')`. It returns a pandas DataFrame with columns `DATASOURCEID`, `SITEID`, `SUBJECTID`, `DATETIME`, `VALUE`, `QUALITYCODE`. In the live state probes, its index was a `RangeIndex`; `DATETIME` contained Python `datetime.date` values, `VALUE` was text, and `QUALITYCODE` was int64. Example quality values included 140 and 150. Normalize `VALUE` to numeric and dates to a proper date/datetime column in our adapter.
- Four-day live calls returned five daily rows for NSW gauge 412033 and VIC gauge 415203. A 2020 request returned flow values formatted as text (including `0.000`); this is valid package output and should be preserved as numeric values by the adapter.
- Empty intervals are expected to produce an empty DataFrame: this is corroborated by a published example for an out-of-record NSW period. The two local 1900/BOM empty-range probes did not reach that behavior because `bomwater` failed while opening its local station cache (details below).
- Gauge routing is inferred from the package's bundled `data/bom_gauge_data.csv`. The `data_source` argument does **not** select NSW/VIC/QLD; it only has a special case for forcing BOM. In our probe, repo gauge 422016 is marked QLD in `parameter_sheet.csv`, but the installed getter catalog classified it as NSW and returned `DATASOURCEID=NSW`. Therefore a checked-in source map cannot currently steer the getter to each state API. We need to compare all repo gauges against the bundled catalog and decide whether to patch/wrap its router, use an alternate API, or accept the library routing. SA is redirected to BOM by the library.
- State API calls use `requests.get(url)` without a timeout. A hung request can therefore occupy a worker indefinitely; a `Future` timeout alone cannot stop the underlying thread. A safe adapter needs a process boundary for hard cancellation, or a narrowly scoped requests timeout injection/patch. Do not call the package directly in a cancellable thread pool and assume timeout means the request stopped.
- Non-200 state responses raise `requests.HTTPError` with the status and response body embedded in the message; the exception does not provide a structured status response. A 429 can be recognized from that message only, unless the adapter intercepts the underlying response. JSON parse and connection exceptions also propagate. Package code has no retries, request rate limiter, or time-series cache. `bomwater` does maintain a persistent station/capabilities metadata cache.
- BOM requests currently fail in this fresh environment with `FileNotFoundError: ~/bom_water/cache/stations.json`. In `bomwater` 0.0.8, `check_cache_status()` returns early when the capabilities file exists without checking whether `stations.json` exists, leaving a partially initialized cache unusable. This needs a preflight/repair or an upstream fix before BOM can serve as the unknown-source fallback. This also prevented an empirical BOM response/quality check in the spike.

## Phase 1 routing decision

The routing audit is generated with `python src/audit_observed_routing.py`; its current report is `docs/observed-routing-mismatches.csv`. It records 21 differences, including 422016 (`NSW;QLD` in the parameter sheet versus `NSW` in the getter catalog). Mismatches are routed through the package catalog when the catalog state is among the parameter-sheet states; an unambiguous mismatch uses the package's direct state API URL and payload helper with the parameter-sheet state. Ambiguous or unsupported sources (including SA/BOM) are marked unavailable. This avoids silently forcing a potentially wrong state for gauges listed under multiple states.

The Phase 1 adapter runs each package fetch in a spawned subprocess so the parent can terminate a hung request. It retries transient failures, preserves raw response columns including quality codes in the disk cache, coerces `DATETIME` and `VALUE` when constructing the standard frame, and recognizes 429 best-effort from the package's exception message. BOM remains unavailable pending a verified repair of the `bomwater` metadata cache.

`requirements-observed.txt` records the observed integration's package pin without adding it directly to `requirements.txt`. However, `py_ewr` 2.4.11 currently declares `mdba-gauge-getter` as a transitive dependency, so this is not optional in an environment that installs the existing `py_ewr` dependency unless that upstream dependency relationship changes. The package was installed only in the ignored local `.venv` for the spike.

## References

- [mdba-gauge-getter on PyPI](https://pypi.org/project/mdba-gauge-getter/)
- [Example of empty-period behavior and the returned columns](https://galenholt.github.io/data_acquisition/gauge_data_pre_gauge.html)

## Phase 2 client (2026-10-19)

Captured replies (`tests/fixtures/observed/`) changed the adapter:

- All three services return JSON with HTTP 200 for errors too. Top-level `error_num` 126 means no data in the period; per-trace `error_num` 20 means the station/datasource is unknown. The getter turns both into an empty result and then falls back to BOM, so out-of-record periods failed with the `bomwater` cache error, and an unknown station read as "empty".
- Days without data come back as `"0.000"` under quality 255 ("Data Unavailable"), NSW 201 or VIC 151 ("Data lost"). The getter only drops quality >= 999, so these became zero flow: 29,650 of 46,313 days for 412033 between 1900 and 2026. The client drops quality 255, any code the reply's own `quality_codes` describes as unavailable, lost or missing, and the getter's >= 999 cutoff.
- 422030 is served by WaterNSW (`CP`). Queensland answers "Invalid station", so the Phase 1 direct-QLD route returned nothing. Routing now asks the catalog's state first, then the parameter sheet's.
- Asking from 1800 makes a service generate a point for every day, which takes 35-60 s before the first byte. The 45 s subprocess deadline timed these out. The client asks `get_variable_list` for the record start (1-2 s, memoised) and starts the trace there.
- WaterNSW reports a full queue as HTTP 408 with `{"error_num":218,"error_msg":"Too many requests in queue (21), limit is 20"}`, seen live. Queensland once sent the plain-text IIS reply "The page cannot be displayed because the client took too long ..." in place of JSON.

`src/observed_source.py` replaces the getter's `requests.get` with a per-thread session, a connect timeout, and a whole-call deadline that also covers streaming the body. It percent-encodes the JSON query and retries with jittered exponential backoff on connection drops, HTTP 500/502/503/504, empty bodies, malformed JSON and plain-text replies. Rate limits, HTML pages, other HTTP errors and Hydstra errors raise typed exceptions without a retry. Because every network call is now bounded, the spawned subprocess is gone. One gap remains: DNS resolution is outside the `requests` timeout.
