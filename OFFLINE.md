# Laptop distribution (Option A)

The deliverable is one HTML file, opened from disk in Edge or Chrome. It needs no
Python, local server, launcher, installation, internet connection or WebAssembly.
Scenarios are prepared centrally. Upload and server probes are disabled even when
someone serves this artifact over HTTP. Existing Python server code remains the
analyst/CI reference, not part of the laptop distribution.

## Analyst build

On an analyst machine or Databricks with the repository and Python dependencies:

```sh
python src/build_dashboard.py
```

The default page contains the review's summary results and per-gauge parameter
inventory. Summary CSVs do not contain daily series: detailed flow charts require
centrally prepared scenarios:

```python
import sys
sys.path.insert(0, 'src')
from embed_scenarios import build_with_scenarios
build_with_scenarios([
    {'name': 'Baseline', 'path': 'data/example_scenarios/dummy_goulburn_scenario_A.csv'},
    {'name': 'Wetter', 'path': 'data/example_scenarios/dummy_goulburn_scenario_B_wetter.csv'},
], 'dashboard with scenarios.html')
```

The existing notebook calls this same function. Standard time-series and supported
py_ewr input formats are read centrally. Formats outside the calendar datetime range
can still supply summary results but do not supply daily charts. Diagnostic failures
fail the build; they are not silently omitted. Build into a new destination to retain
the previous release if input evaluation fails.

```sh
python src/package_offline.py 'dashboard with scenarios.html' dist/dashboard.zip
```

Send the ZIP. Recipients extract it and open its HTML file. The ZIP contains no
runtime, executable, shell script, Python code or dependencies.

## Embedded format and computation

`offline-data` is a version-1 `application/json` block, with `<` escaped to prevent
script termination. It contains parameter inventory, regime thresholds, and
per-scenario flow, yearly EWR, event and gap records. Per-EWR keys are JSON arrays
of gauge, code and planning unit, avoiding delimiter collisions. Comparison pairs
are formed at runtime using the intersection of complete assessment years.

Flows include only the gauges present in each scenario, from first to last observed
day, retaining missing calendar days. Little-endian float64 deltas are base64 encoded;
NaN is a missing-day sentinel and exact-value overrides preserve rare subtraction
residuals. We deliberately retain source precision instead of 0.1 ML/d quantisation:
rounding could change EWR threshold classifications. The ZIP compresses repetitive
deltas. The browser uses typed arrays and a Blob worker for numeric diagnostics;
py_ewr itself never runs in the browser. Arbitrary new EWR evaluations are not
possible without rebuilding centrally.

The footer records py_ewr's version, build UTC time and the SHA-256 of the canonical
parameter-table CSV used by this build. `src/vendor/manifest.json` pins source URLs,
versions and SHA-256 checksums. Leaflet 1.9.4 and Plotly 2.27.0, plus their licenses,
are stored locally; packaging verifies checksums. Maps draw embedded GeoJSON using
Leaflet canvas, with no background tiles. Marker/layer-control images are unused.

Meta CSP denies connections, default resource loads, objects, forms and base URL
changes. Executable inline scripts have SHA-256 hashes refreshed after embedding.
Blob workers and data/blob images are allowed. Runtime style attributes require
`style-src 'unsafe-inline'` for Leaflet/Plotly. Scenario selectors and lists use DOM
text nodes; dynamic rich chart labels escape text. Storage access is guarded and
saved server scenarios are never restored into an offline release.

## Size measurements

Measured with py_ewr 2.4.11 on the supplied Goulburn reference scenarios:

| Artifact | Uncompressed bytes | ZIP bytes |
|---|---:|---:|
| Two reference scenarios, four flow gauges each | 10,812,532 | 2,306,317 |
| Synthetic sizing payload: 144 gauges × 2 scenarios × 36,525 days | 114,513,523 | 39,224,506 |

The sizing payload repeats the supplied flow pattern to 100 years. It is an actual
serialized flow block, not a measured production dataset, and excludes EWR diagnostics,
map and libraries. Compression of production flows can differ substantially. This
supports trimming to gauges actually present in each scenario (implemented), rather
than padding every scenario to every review gauge. A production release containing
all gauges and long records must also be checked for load time and memory on the
target laptop. `encode_flow` and the package command print/permit exact artifact
size measurement without shipping any runtime.

## Verification and release acceptance

Run Python/JS numerical parity with Node on PATH (or set `NODE_BINARY`), covering
paired dates, missing days, water-year boundaries, leap days, seasonal windows,
consecutive runs, zoom and all curve types:

```sh
pytest tests/test_offline.py -q
node tests/test_performance.js
EWR_OFFLINE_HTML='dashboard with scenarios.html' node tests/test_offline_browser.cjs
```

The browser check requires Playwright and an installed browser. Optionally set
`EWR_BROWSER_EXECUTABLE` to Chrome/Edge's executable. It fails on page errors,
CSP violations or subresource requests, except the in-memory `blob:` worker load
(which performs no network I/O). It also runs with localStorage denied.

The workflow template `ci/offline.yml` builds the reference scenarios, exercises the
actual worker, and packages a ZIP. To activate it, move it to
`.github/workflows/offline.yml` using a GitHub credential with `workflow` scope.
The publishing credential available for this change did not have that scope, so
the template is supplied without claiming an active Actions run.
The Python server regression tests remain as the numerical reference. Neither those
tests nor CI substitute for the following checks on the managed Windows laptop:

- Open the extracted HTML from Downloads, OneDrive and a path containing spaces,
  in both Edge and Chrome. Exercise the map and all charts and change scenarios.
- Capture network traffic: the page must cause **zero network requests**. Reading
  its own local file and loading its in-memory worker are local operations.
- Compare the reference scenario plots and values against the Python reference.
- Deliver the ZIP through the actual SharePoint/Teams/email route and test the
  resulting downloaded artifact with Mark-of-the-Web preserved. Record browser
  versions, policies, release hash and results. Do not label this acceptance passed
  based only on a developer Mac or CI run.

Option B (Pyodide/WASM) is not implemented. It needs a separate IT decision that
explicitly permits running Python in the browser.
