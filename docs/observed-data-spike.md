# Observed data spike: `mdba-gauge-getter`

Date: 2026-10-19
Environment of the original spike: macOS arm64, Python 3.13.0; `mdba-gauge-getter` 0.5.1, `bomwater` 0.0.8, `py_ewr` 2.4.11, pandas 3.0.6.

## Findings from the original spike

- The package installed and imported on Python 3.13.0 alongside `py_ewr` 2.4.11. Pip resolved `numpy==1.26.4` for py_ewr and `pandas==3.0.6`; both imported and the getter worked for state-backed queries. This was a smoke check, not full application compatibility. The package metadata classifiers stopped at Python 3.11.
- `mdba_gauge_getter.gauge_getter.gauge_pull(gauge_numbers, start_time_user, end_time_user, var="F", interval="day", data_type="mean", data_source="state")` returned `DATASOURCEID`, `SITEID`, `SUBJECTID`, `DATETIME`, `VALUE`, `QUALITYCODE`. Its index was a `RangeIndex`; `DATETIME` contained Python `datetime.date` values, `VALUE` was text, and `QUALITYCODE` was int64. Example quality values included 140 and 150.
- Four-day live calls returned five daily rows for NSW gauge 412033 and VIC gauge 415203. A 2020 request returned text values including `0.000`; valid zeros must survive numeric conversion.
- Empty results are ambiguous: no data, an unknown gauge and an unsupported state cannot be distinguished from the returned frame. The original 1900/BOM probes raised the BOM cache error below before returning a frame.
- The getter routes gauges using its own catalog. `data_source` does not select NSW/VIC/QLD; its special case forces BOM. The prior audit recorded 21 differences from the repository's parameter sheet. Examples are 422016 (`NSW;QLD` in the sheet versus NSW in the getter) and 422030 (QLD in the sheet, served by WaterNSW). These limitations are accepted. The application does not inspect the getter's catalog or override its routes. SA is redirected to BOM by the getter.
- The getter's internal requests have no timeout. A future timeout stops waiting but cannot stop the underlying call. Hard cancellation would require a spawned subprocess around the same public function.
- Non-200 state responses raise `requests.HTTPError` with the status and body in the message, without a structured response status. JSON decode and connection exceptions also propagate. The getter does not retry calls or cache time series.
- With `bomwater` 0.0.8, BOM fallback raised `FileNotFoundError: ~/bom_water/cache/stations.json` on a fresh cache. An out-of-record state period can therefore surface as an error. The application lets the getter perform its fallback and reports the exception. It does not repair BOM metadata, force another source, or blanket-map file errors to empty results.

`requirements-observed.txt` pins the integration dependency without adding it directly to `requirements.txt`. `py_ewr` 2.4.11 also declares the getter transitively, so the existing py_ewr installation may already provide it. Application availability checks use `importlib.util.find_spec`; the adapter imports the getter lazily.

## Phase 2 adapter (2026-10-19)

`gauge_pull` is the sole observed-data and metadata access path. `src/observed.py` calls it once per gauge and variable, always with `data_source="state"`, daily intervals and mean data. The getter owns request construction, parsing, routing and BOM fallback. The application has no second service client or record-start lookup. “All available” uses `server.EARLIEST_RECORD = date(1890, 1, 1)` through today.

After the getter returns, `_normalise` converts `DATETIME` to pandas timestamps and `VALUE` with `pd.to_numeric(errors="coerce")`. It removes quality codes 255, 201 and 151, plus codes >= 999. Earlier observations recorded unavailable/lost days with these codes as zero values: 29,650 of 46,313 days for 412033 in the historical probe. These counts are historical evidence, not a fresh verification. Valid zero flows are retained. Empty results have the six standard columns. Cache version 3 invalidates earlier entries and omits `UNITS`, which the getter does not expose.

Each missing range has a 90 s advisory timeout shared by all attempts and backoff. Calls run in daemon worker threads: a timed-out call can continue in the background until it returns, but it cannot write a late result into the cache. Connection failures and HTTP errors whose messages carry 500, 502, 503 or 504 are retried with jittered exponential backoff, at most twice. HTTP 429/408, JSON decode errors and other exceptions are not retried. Exceptions become `GaugeResult(status="error", message=...)`; empty frames become `empty` with “No data returned for this gauge and period”. Unsupported variables remain `unsupported`.

The per-key locks, extending raw-range cache, expiry, `fetch_many` progress callbacks and standard-series conversion remain in place. `sdl_gauges` reads only the repository's parameter sheet. Tests stub `mdba_gauge_getter.gauge_getter.gauge_pull`; enforcement tests reject direct endpoints, service payload operations, bundled-catalog reads and other getter imports under `src/`.

Long all-record calls can be slow: the earlier probe measured 35–60 s before the first byte. The default timeout remains 90 s after the live checks below completed within it.

## Live verification (2026-10-19)

Both flow checks used the adapter with a temporary cache, one gauge per `gauge_pull` call, `data_source="state"`, and the full 1890-01-01 to 2026-10-19 range. The getter output was captured at that same call boundary to compare quality counts before and after normalisation; no separate service or metadata request was made.

| Gauge | Elapsed | Getter rows | Rows retained | Flagged days removed | Valid zero flows retained |
| --- | --- | --- | --- | --- | --- |
| NSW 412033 | 32.58 s | 49,965 | 15,813 | 34,152 (33,302 code 255; 850 code 201) | 2,123 |
| VIC 415203 | 15.37 s | 49,965 | 30,009 | 19,956 (19,878 code 255; 78 code 151) | 10,745 |

Neither normalised result retained codes 255, 201, 151 or >= 999. All valid zero flows in the getter output survived. Of the removed days, 34,073 NSW days and 19,870 VIC days had zero values; flagged nonzero values were also removed as required. Both returned `ok`; no retries or alternate routing were needed. The getter warned that 415203 has two VIC catalog results and handled that internally. These successful state calls do not verify BOM fallback.

## References

- [mdba-gauge-getter on PyPI](https://pypi.org/project/mdba-gauge-getter/)
- [Example of empty-period behavior and the returned columns](https://galenholt.github.io/data_acquisition/gauge_data_pre_gauge.html)
