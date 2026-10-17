# EWR analysis dashboard

An interactive page for exploring environmental water requirement (EWR) results from the 2026 Basin Plan review. It averages two metrics for each EWR, up the hierarchy EWR → gauge → planning unit → SDL unit:

- **FR**, the frequency ratio (achieved ÷ target frequency)
- **ETP**, the MRIP exceedance time proportion

It can show a single scenario or the difference between a base and a comparison scenario, with an objective × EWR matrix for each SDL unit, planning unit and gauge.

## Quick start (no technical knowledge needed)

The tool runs on your own computer: you start it, and it opens in your web browser. You can load your own model output (a CSV or other supported file), run the EWR calculations on it, and see the charts update straight away.

1. **Install Python** (once). Download version 3.10 or newer from <https://www.python.org/downloads/>. On Windows, tick **"Add python.exe to PATH"** in the installer.
2. **Download this project**: on the GitHub page click **Code → Download ZIP**, then unzip it somewhere (your Desktop is fine).
3. **Start it** by double-clicking:
   - **Mac:** `start.command` (the first time, right-click it, choose **Open**, then **Open** again to get past the security prompt)
   - **Windows:** `start.bat`
   - **Linux:** run `./start.sh` in a terminal (or double-click `start.sh` if your file manager offers "Run")
4. **Wait for your browser to open.** The first start takes a few minutes: it sets up a private Python environment inside the project folder (`.venv`) and downloads the packages it needs, so it needs internet. Later starts take a few seconds.
5. **Use it.** Choose **Your data**, then **Add scenario**, pick the data format and choose your file ([details below](#your-data-run-your-own-scenarios)).
6. **Stop it** by closing the black window (or pressing Ctrl+C in it).

If port 8792 is busy the tool quietly uses the next free one; the address is shown in the window and in your browser. If something goes wrong, the window stays open with a message saying what to do (for example, install Python).

Prefer a command line? From the project folder, `python3 run.py` does the same thing on any system (`py run.py` on Windows). Add `--no-browser` to skip opening the browser, or a port number (`python3 run.py 9000`) to prefer one. The page needs an internet connection to load the map and chart libraries (Leaflet, Plotly) and map tiles.

## Repository layout

```
start.command, start.sh, start.bat   double-click launchers (Mac, Linux, Windows)
run.py                           the launcher they call: sets up .venv, installs requirements, starts the server, opens the browser
ewr_analysis_dashboard.html      the built dashboard page (served by the local server)
src/
  embed_scenarios.py             runs py_ewr on your files and embeds the results in a copy of the page (used by the notebook)
  server.py                      local server: serves the page and runs py_ewr on your own model output (python3 src/server.py also works)
  build_dashboard.py             build script: reads data/, fills the template, writes the HTML
  dashboard_template.html        the page itself (markup, styles, JavaScript) with data placeholders
  flow_regime_mappings.py        maps an EWR code to its flow regime when the results do not give one
notebooks/
  run_your_scenarios.ipynb        Databricks notebook: your model output in, a dashboard HTML with your results out
data/
  results/
    frequency_ratio.csv          FR per EWR, gauge, planning unit, policy and climate scenario
    etp.csv                      ETP per EWR, gauge, planning unit, policy and climate scenario
  map/
    sdl_resource_units.geojson   SDL unit boundaries
    rivers.geojson               rivers drawn on the map
    bom_gauge_stations.csv       gauge names and locations (Bureau of Meteorology Water Data Online)
    constraint_sites.csv         constraint sites drawn on the map (MDBA constraint focus areas; hand-maintained, approximate locations)
  reference/
    objective_reference.csv      ecological objective codes, descriptions and themes
  example_scenarios/             small made-up daily flow files for trying "Your data" (Broken and Goulburn gauges)
requirements.txt                 Python packages for the build and the server (run.py installs them for you)
```

## Rebuild the HTML

You need Python 3.10 or later.

```bash
git clone https://github.com/RoryANU/ewr-analysis-dashboard.git
cd ewr-analysis-dashboard
pip install pandas numpy shapely
python3 src/build_dashboard.py
```

The script prints a short summary (EWR rows, SDL units, planning units and gauges plotted) and overwrites `ewr_analysis_dashboard.html` in the repository root (about 3.7 MB). Run it again whenever you change the template or any file in `data/`. It finds its inputs relative to its own location, so keep the folder layout as it is.

## Your data: run your own scenarios

The page cannot run the EWR tool by itself, so "Your data" uses a small server on your own computer. Start it with the [quick start](#quick-start-no-technical-knowledge-needed) above (or `python3 run.py`); it opens the page for you at an address like <http://localhost:8792/> (the page must be opened from that address, not as a file). Choose **Your data**, then **Add scenario**, pick the data format, and choose your file. You do not choose an SDL unit: the file is scanned for every gauge in the review, so one file can cover several SDL units. The scenario name defaults to the file name. The results are FR and ETP per EWR, and they appear as an extra scenario in the pickers (Historical climate only).

- Formats: Standard time-series, Bigmod, FIRM, res.csv (MDBA), Source NSW res.csv, IQQM netCDF and ten-thousand-year runs. A Standard time-series file has a `Date` column and one column per gauge named `<gauge>_flow` or `<gauge>_level`, e.g. `404216_flow`.
- Try it with the files in `data/example_scenarios/` (`dummy_broken_scenario_A.csv` and `..._B_wetter.csv` for Differential mode).
- The server listens on localhost only. Uploaded flows are kept in `.user_scenarios/` (not committed) so the flow charts can be drawn.
- Flow charts: open a gauge, then **Flow analysis** and pick a chart from the **Chart** dropdown. *Flow*: threshold × duration heat map, hydrograph with annual volumes, and (Differential) the differential and monthly differential flow duration curves. *EWR performance* (py_ewr's own results for one EWR): achievement by year, events, event timing, gaps between events against the maximum inter-event period. *Flow regime*: flow regime components (each regime's threshold is the lowest EWR threshold for it at the gauge, from the EWR tool's parameter sheet) and low-flow spells. In Differential mode, comparisons use only the dates or years both scenarios cover.
- The EWR calculations are done by [py_ewr](https://pypi.org/project/py-ewr/) (version 2.4.11 or later).

## Running on Azure ML compute (flow charts enabled)

If you have an Azure ML compute instance, you can run the server there to get the full dashboard including flow charts (heat map, hydrograph, flow duration curves). Choose **Terminal** from the compute's application list.

1. **Clone the repo and install dependencies:**

   ```bash
   git clone https://github.com/RoryANU/ewr-analysis-dashboard.git
   cd ewr-analysis-dashboard
   pip install -r requirements.txt
   ```

2. **Copy your data files** onto the compute. The model output files live on Databricks mounts (`/dbfs/mnt/...`), which are not reachable from Azure ML. Either copy them from the underlying Azure Storage account using `azcopy` / Azure CLI, or download them from Databricks and upload to the compute.

3. **Start the server** listening on all addresses so Azure ML's port proxy can reach it (by default it listens on `127.0.0.1` only, which blocks external access):

   ```bash
   python3 run.py --host=0.0.0.0 --no-browser
   ```

   Because this makes the tool reachable by other computers, it prints an address ending in `?token=...`. A random access token is required for every page and API request, so use that exact address and do not share it. Without `--host` (the default) the server only accepts connections from your own computer and needs no token. The tool serves only the dashboard page (never repository files or the stored uploads in `.user_scenarios/`) and has no other login: for anything beyond a private compute instance, put it behind an authenticated proxy.

4. **Open the dashboard** in your browser at:

   ```
   https://<compute-name>-8792.<region>.instances.azureml.ms/ewr_analysis_dashboard.html?token=<the token printed at start-up>
   ```

   Find `<compute-name>` under **Compute → Compute instances** in Azure ML Studio. Find `<region>` in your workspace's **Overview → Location** (e.g. `australiaeast`). If you already have JupyterLab or VS Code open from that compute, the browser URL bar contains both values.


## Working from Databricks (no command line, no server)

If you can only run code in Databricks notebooks, use the notebook `notebooks/run_your_scenarios.ipynb`. It runs the EWR tool on your model output inside Databricks and writes a copy of the dashboard with your results built in. You then download that one HTML file and open it in your browser: no localhost and no terminal.

1. **Add the repository as a Git folder** (Workspace, Create, Git folder, paste `https://github.com/RoryANU/ewr-analysis-dashboard.git`). Keep the folder layout.
2. **Upload your model output file(s)** to a Unity Catalog volume (Catalog, your volume, Upload to this volume), or into the workspace. Note the path, e.g. `/Volumes/<catalog>/<schema>/<volume>/my_run.csv`. Any of `/Volumes/...`, `/Workspace/...`, `/dbfs/...` or `dbfs:/...` works; give the full path of the file, not a folder.
3. **Open `notebooks/run_your_scenarios.ipynb`** and attach it to a cluster (Python 3.10 or later; serverless is fine).
4. **Edit the `SCENARIOS` list** in the `SCENARIOS` cell (the first cell with a list in it): one entry per file, with the file `path`, its `format` (the notebook lists the options) and an optional `name` (it defaults to the file name). You do not choose an SDL unit: each file is scanned for every gauge in the review, so one file can cover several SDL units.
5. **Run all.** The first cells install `py_ewr` (about a minute). The notebook prints how many EWR results and gauges it found in each file, saves `ewr_dashboard_with_scenarios.html` in the Git folder, and shows the dashboard under the last cell.
6. **Download `ewr_dashboard_with_scenarios.html`** (Git folder, the file's menu, Download) and open it in your browser. It opens in **Your data** with your scenarios in the Scenario / Base / Comparison pickers. For differences between two runs, put both files in `SCENARIOS` and switch to Differential. To share it, send the HTML file: everything is inside it (it loads the map libraries from the internet).

Limits of this route: the flow charts (heat map, hydrograph, flow duration curves) need the local server, so they are not in the page; the page itself cannot add a new scenario, so to add or change a scenario edit `SCENARIOS` and run the notebook again. The file is not committed (it is in `.gitignore`).

To rebuild the base dashboard itself after changing the template or `data/`, run in a notebook cell: `%pip install pandas numpy shapely`, then `!python src/build_dashboard.py` from the repository folder, and commit the new `ewr_analysis_dashboard.html`.

If you can run a terminal somewhere (for example a laptop or a Databricks web terminal), the "Your data" section above (`python3 run.py`) lets you add scenarios inside the page and use the flow charts.

## TODO

- [x] Heatmap contours should be dynamically resized based on the range of values; no more than 3–4 levels, 0 should not be a level, and levels should use 5/10/20% increments only
- [x] EWR codes on the heatmap need to be more readable
- [x] The EWR description in the dropdown should not say "max gap"
- [x] The EWR overlay explanation on the heatmap (e.g. "▢ OB1 (flow_handle, consecutive): ≥20000 ML/d for 4 days …") should not restate the EWR definition; replace it with a simple table showing achievement % for scenario A, scenario B, the difference, and the target
- [x] The differential flow duration curve should not default to log scale; log scale should be optional
- [x] UpSet plots should not display values of 1; bars should just be circles (matching the drilled-in style)
- [x] Differential flow duration curves should be filtered by the EWR's timing window; only EWR thresholds that share a common timing window should be selectable together
- [x] Hydrograph with interevent exceedance display should include a "years in 10" summary statistic that dynamically updates as the visible time range is filtered
- [x] Add quick-filter buttons for key drought periods: Millennium, Tinderbox, Federation, WWII
- [x] Y-axis range should auto-resize to the new max when the hydrograph time range is filtered
- [x] Add manual start/end date entry fields for the hydrograph in addition to the Plotly drag-to-zoom selection
- [x] Frequency ratio colour scale should use red → white → blue (matching the standard Excel version): 0 = red, 0.9 = white, 1 = blue
- [x] Interevent colour scale should use the standard Excel green → yellow/orange → red regime: 0 = green, 10 = red
- [x] Heatmap colour scale min/max should rescale dynamically based on the data in the current field of view, snapping in 5% increments only
- [ ] *(Backburner)* Replace the binary consecutive/any-days toggle with a sliding scale: consecutive (no gaps) → increasing max allowed gaps within an event → any days
- [x] Prevent selecting the same scenario as both base and comparison in differential mode
- [x] When loading new data, allow adding it to an existing scenario (not just creating a new one)
- [x] Allow manually adding custom constraints / flow regimes to the heatmap (user-defined thresholds and timing windows)
- [ ] *(Backburner)* Expand the map to show key constraint sites, storages, towns, etc.
- [x] The right-hand info panel should be resizable (draggable divider) to adjust how much of the window it occupies; flow analysis plots should resize accordingly
- [ ] *(Backburner)* Document / understand the inner workings of the HTML+JS that produce the heatmap and flow plots (how data flows from the embedded JSON through to Plotly rendering)
- [x] Heatmap occasionally throws "Could not draw: Maximum call stack size exceeded"; if this occurs, automatically reduce the date density (coarser x-axis) until the plot renders within stack limits
- [x] *(Backburner)* Monthly differential FDC heatmap

<!-- VALIDATION_FRAMEWORK_START -->
## Testing, linting and git hooks

The checks guard the numbers the page shows: the build from `data/` to the embedded JSON, the server's py_ewr wrappers and chart payloads, and the committed HTML itself.

### Set up

```bash
pip install -r requirements-dev.txt   # runtime packages plus pytest, ruff, mypy, pre-commit
pre-commit install                    # run the checks on every `git commit`
```

### Run the tests

```bash
pytest                    # everything (about 30 s)
pytest -m "not slow"      # what the pre-commit hook runs (about 12 s)
pytest -m slow            # only the slow tests: py_ewr over many years of data, and a full rebuild of the HTML
pytest tests/test_server_numerics.py -k heatmap   # one file / one topic
```

| File | What it checks |
|---|---|
| `test_build_pipeline.py` | each stage of `src/build_dashboard.py` (loading, scenario index, per-EWR means, themes, ROWS table, gauges, polygons, META, HTML slots) against hand-computed values on the small data set in `tests/fixtures/mini/` |
| `test_dashboard_html.py` | the data embedded in `ewr_analysis_dashboard.html`: index bounds, ordering, units, map geometry, every FR / ETP cell recomputed independently from the CSVs, a stored baseline of the aggregates, and (slow) that rebuilding reproduces the committed page byte for byte |
| `test_source_data.py` | arithmetic promised by `data/results/*.csv` (FR = Frequency ÷ Target, ETP = L ÷ record days, ...) |
| `test_server_numerics.py` | exact values for the heat map, duration curves, hydrograph, spells, gaps and runs in `src/server.py`, plus strict-JSON output |
| `test_pyewr_wrappers.py` | `ewr_list`, `load_frames`, `run_scenario` and `ewr_eval` against py_ewr's parameter table and an independent recomputation; a baseline of the example scenario's results |
| `test_flow_regime_rules.py`, `test_northern_connectivity.py`, `test_embed_scenarios.py` | the flow-regime rule, the workbook reader and the notebook helper |

Baselines live in `tests/fixtures/snapshots/`. After an *intended* change, regenerate them with `UPDATE_SNAPSHOTS=1 pytest -m "slow or not slow"` and review the diff before committing. If the HTML test says the page is stale, run `python3 src/build_dashboard.py` and commit the result.

A few tests are marked `xfail(strict=True)`: they record known discrepancies (see the pull request that added them). Fixing one makes its test fail until the marker is removed.

### Lint, format and type-check

```bash
ruff check .             # lint (pyflakes, pycodestyle, isort, bugbear)
ruff format --check .    # formatting; `ruff format .` rewrites
mypy                     # type-checks src/ and tests/
pre-commit run --all-files
```

The hooks (`.pre-commit-config.yaml`) run syntax and file checks, ruff, mypy and the fast tests. Some hooks fix files in place (ruff, whitespace, end-of-file), so a commit can fail after rewriting them: review the changes, `git add` them and commit again. The tools are configured in `pyproject.toml`; `ruff` is pinned to the same version in `requirements-dev.txt` and the hook, because formatter output changes between releases.
<!-- VALIDATION_FRAMEWORK_END -->

## Notes

- The "Your data" mode needs the local server (start it with `start.command` / `start.bat` / `python3 run.py`); without it the page works in Demo data mode only.
- The input data comes from the MDBA's 2026 Basin Plan review outputs and public datasets. Check the original licences before reusing it.

### Joint performance and climate robustness

Selecting an SDL unit, planning unit or gauge now adds a joint FR–MRIP view and
policy-by-climate matrix. Frequency target compliance is FR ≥ 1.0; 0.9 ≤ FR < 1.0
is explicitly a tolerance band. The joint views always use 1.0, even if the existing
frequency-threshold control is changed. MRIP compliance uses the current exceedance
threshold (default zero); ETP is a time proportion, not a calendar-year count.
Differential mode draws base-to-comparison arrows using absolute values. Locked
scatter axes use FR 0–2 and ETP 0–1. Both out-of-range endpoints are counted; arrows
require both endpoints inside the plot. Unlocking expands FR only. ETP is a time
proportion, so any ETP above 1 (in the source results or a computed upload) is
treated as 1, i.e. exceeded for the whole record. Matrix colours
always use a fixed 0–100% joint-score scale.

`src/performance.js` contains the reusable views and is embedded by the builder.
The existing `(planning unit, SDL unit, gauge, EWR)` pairing of FR and ETP remains
unchanged, including the documented averaging of source variants. Only EWRs with
both finite, nonnegative metrics (ETP capped at 1) enter joint denominators. Raw EWR mean,
minimum, linearly interpolated 10th percentile, target percentage and coverage accompany
the existing hierarchical averages. “Severe” means FR below the configurable floor
(default 0.5), not a new ecological classification. Resilience requires compliance
in every expected climate. Threshold crossings also require complete climate coverage
and identify compliant/noncompliant climates; they do not imply a causal tipping point.
The matrix separately reports incomplete rules and assessed/expected climate counts. Uploaded records lack climate
metadata and therefore receive a record comparison, not inferred climate robustness.

At an uploaded gauge, **Load annual raster** evaluates all supported selected rules
through the existing py_ewr annual endpoint, using each selected planning unit even
when the event selector groups equivalent definitions. Rows are grouped by ecological theme;
assessment windows are labelled because they may differ. Time since success is
unknown before the first observed success or following incomplete years. Rolling
frequency requires ten consecutive complete assessment years. Dates, missing days,
complete assessment years and MRIP limits come from the evaluated daily records.
Bundled summaries cannot supply these temporal diagnostics. The diagnostic inventory
includes level, volume and specialised rules even when their detailed annual plots
are unsupported. Existing event-gap plots retain longest-gap diagnostics.

Optional observation charts accept a CSV up to **2 MiB / 20,000 observations**, with
quoted fields supported, `date` (ISO YYYY-MM-DD), and at least one of `stage_m`, `temperature_c`, `dissolved_oxygen_mg_l`. Blank measurements stay
missing; duplicates, malformed quoting, invalid dates and non-decimal values are
rejected. Whitespace-only measurements, hexadecimal and exponent notation are not
accepted; use empty fields for missing values. Gaps add one separator per interval,
never one row per absent day. Stage changes use adjacent
daily observations only. Uploaded observations apply to the current selection and
are not saved or used to infer ecological safety. No observations are fabricated.

Versioned share links (`v=2`) always encode the frequency threshold. Existing
unversioned links without `thr` retain the old 0.9 default; opening a fresh page uses
1.0. Explicit thresholds are preserved. Unversioned links produced by the rejected
PR #4 cannot be distinguished from older links, so they follow the legacy rule;
choose 1.0 and copy the link again if needed.

Routine sorting, matrix expansion and scatter-scale changes retain the performance
panel and its in-flight work. New selections cancel stale annual requests. Annual
loading uses at most four concurrent requests, offers cancellation and restores its
controls on failure. Each chart render owns a separate DOM node, so a delayed render
cannot overwrite a newer upload or retry. Inventory requests and server parameter metadata are cached;
failed requests can be retried. Matrix contents and exact rule strings are built on
opening/interaction and memoised for the current selection.

Run `node --test tests/test_performance.js` for calculations, link compatibility,
async failures/cancellation/stale responses, CSV limits and largest-gauge timings.
`node --test tests/test_dashboard_core.js` covers the page's per-EWR values, differential
signs, hierarchical averages and selections. Both run in the pre-commit hook; `node` comes
from `nodejs-wheel` in requirements-dev.txt.
`pytest tests/test_performance_backend.py` uses controlled parameter/annual fixtures
and real HTTP handlers for schema, coverage, caching and safe JSON embedding tests.
Rebuild with `python src/build_dashboard.py`.

An optional real-browser check is available as
`node tests/test_performance_browser.cjs` with Playwright installed. It uses an
isolated headless browser and temporary local HTTP server, the generated dashboard,
and controlled diagnostic responses. Set `EWR_BROWSER_EXECUTABLE` to an installed
Chromium/Chrome binary if Playwright browsers are not installed. The existing CDN
chart libraries must be reachable. It checks lazy matrix contents, retained DOM,
request counts/concurrency, annual and observation charts, and browser errors.
Set `EWR_REVIEW_SCREENSHOT` to select the screenshot path (default `/tmp/ewr-review.png`).
