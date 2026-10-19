# EWR analysis dashboard

An interactive page for exploring environmental water requirement (EWR) results from the 2026 Basin Plan review. It averages two metrics for each EWR, up the hierarchy EWR → gauge → planning unit → SDL unit:

- **FR**, the frequency ratio (achieved ÷ target frequency). A single scenario's means cap each EWR at 1, so a surplus cannot offset a shortfall elsewhere; differences are in achieved years per century of record
- **ETP**, the MRIP exceedance time proportion, shown as the **% of record beyond MRIP**

It can show a single scenario or the difference between a base and a comparison scenario, with an objective × EWR matrix for each SDL unit, planning unit and gauge. In Differential mode an EWR that meets its target in both scenarios is counted apart ("met in both") and left out of the means; an EWR counts as changed when it gains or loses at least the change band (default 2 achieved years; 1 year of record beyond MRIP for ETP).

The **More** menu adds the share of EWRs meeting the frequency threshold, failing badly or achieving their MRIP, the **duration ratio** (event length ÷ required duration, capped at 2; for the review results an approximation from the mean event length) and, for your own scenarios, the **worst gap** between successful events ÷ MRIP (the worst EWR sets each cell). Your own scenarios can also be viewed over the **drought windows** (Federation 1895–96 to 1902–03, WWII 1937–38 to 1945–46, Millennium 1996–97 to 2009–10, Tinderbox 2017–18 to 2019–20; defined in `src/drought_windows.py`).

## EWR guide and hideable panels

The **📘 EWR guide** button in the bar opens a panel on the left that mirrors the results panel (same cards, resizable by its own divider). Search the EWR parameter sheet by free text (code, gauge ID or name, planning unit, catchment, objective), state, planning unit, flow / level / volume, gauge and code, then **Add** up to 5 EWRs. Each becomes a dropdown such as "EWR · BF1_a · 425008 · Darling River at Wilcannia". Only EWRs using the `flow_handle` or `lowflow_handle` handlers get an animated tutorial; other EWRs show "Demo still in development". The tutorial follows the style of the explainer video in `watering_viz_code`: what the EWR is, the threshold, the EWR window, the duration, a year that misses, the frequency target and frequency ratio, the maximum gap between events, and a summary. Play, pause, step and replay with the controls; the dots jump to a step. The tutorial's flow record is **illustrative**, generated from the EWR's own parameters (threshold or range, duration, window, events per year, accumulation volume, target frequency, maximum inter-event period), not gauge data. Your selection and panel states are remembered in the browser.

The left panel, the results panel and the title/options bar can each be hidden completely with the small handles (◂ ▸ on the dividers, ▴ in the bar) so the map fills the window; a tab at the edge brings each back.

The guide reads `data/reference/parameter_sheet.csv`, a copy of [`parameter_sheet.csv` in MDBAuth/EWR_tool](https://github.com/MDBAuth/EWR_tool/blob/main/py_ewr/parameter_metadata/parameter_sheet.csv). `python3 src/sync_parameter_sheet.py` checks the copy against GitHub (exit 1 when it differs; a warning only when offline) and `python3 src/sync_parameter_sheet.py --update` refreshes it; follow with `python3 src/build_dashboard.py`. The same check runs as a pre-commit hook when the file is edited, as a **pre-push hook** (`pre-commit install --hook-type pre-push`) and as the `network` test `tests/test_parameter_sheet_sync.py`.

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
  ewr_guide.js                   the EWR guide panel: search, selection and the tutorial animations (inlined into the page)
  custom_ewr.js                  user EWR registry, four-section form, JSON sharing, animated guidance and unsaved tests
  custom_ewr.py                  validated user definitions, parameter injection, py_ewr evaluation and server registry
  performance.js                 joint performance, policy/climate tables and annual diagnostics (inlined into the page)
  sync_parameter_sheet.py        checks / updates data/reference/parameter_sheet.csv against the MDBA EWR tool on GitHub
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
    parameter_sheet.csv          the EWR tool's parameter sheet (copy of MDBAuth/EWR_tool; the EWR guide's source, kept in step by sync_parameter_sheet.py)
  example_scenarios/             made-up flow files, plus a documented synthetic level/salinity fixture, for trying "Your data"
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
- Heatmap: use the **EWRs** dropdown to select several EWRs with the same timing window. Each gets a labelled point and a row of exact achievement percentages. Clear the selection to choose a different window; the chart’s **Any days / Consecutive** setting applies to all selected points.
- Try it with the files in `data/example_scenarios/` (`dummy_broken_scenario_A.csv` and `..._B_wetter.csv` for Differential mode).
- The server listens on localhost only. Uploaded daily flows, levels and salinity are kept in `.user_scenarios/` (not committed) for charts and user EWR evaluation.
- Flow charts: open a gauge, then **Flow analysis** and pick a chart from the **Chart** dropdown. *Flow*: threshold × duration heat map, hydrograph with annual volumes, and (Differential) the differential and monthly differential flow duration curves. *EWR performance* (py_ewr's own results for one EWR): achievement by year, events, event timing, gaps between events against the maximum inter-event period. *Flow regime*: flow regime components (each regime's threshold is the lowest EWR threshold for it at the gauge, from the EWR tool's parameter sheet) and low-flow spells. In Differential mode, comparisons use only the dates or years both scenarios cover.
- The EWR calculations are done by [py_ewr](https://pypi.org/project/py-ewr/) (version 2.4.11 or later).

## Observed gauge data

**Observed** fetches flow and level records for gauges in one SDL unit through `mdba_gauge_getter.gauge_getter.gauge_pull`, then evaluates them through the same `py_ewr` path as uploaded daily series. Choose an SDL unit, keep all gauges or select a subset, set the date range, and run. “All available” requests `EARLIEST_RECORD` (1890-01-01) to today. Each call passes one gauge and `data_source="state"`; the getter controls routing and its BOM fallback. The adapter does not query record-start metadata or read the getter's catalog.

The adapter converts values to numbers and dates to timestamps, and drops placeholder days with quality codes 255, 201, 151 or >= 999, preserving valid zero flows. Raw daily records are cached in `.observed_cache/` (cache version 3, without `UNITS`); evaluated series are stored in `.user_scenarios/`. Each missing range has a 90 s **advisory** timeout including retries: a timeout returns an `error`, but a hung getter call can retain its daemon worker thread until it returns. Connection failures and HTTP 500/502/503/504 receive jittered retries; HTTP 429/408 and JSON failures are not retried. Empty results are reported as “No data returned for this gauge and period”, without inferring why.

The direct integration dependency is listed in `requirements-observed.txt`; the application imports it only when observed support is used. `py_ewr` may also install it transitively, depending on the package version. The local server reports whether the getter package is importable at `/api/ping`. The browser talks only to the local server.

The getter's routing limitations are accepted: for example, 422016 is NSW in its catalog and NSW;QLD in the parameter sheet, while 422030 is listed as QLD in the sheet but served by WaterNSW. There is no alternate-state retry in the adapter. The getter redirects SA gauges to BOM and can fall back to BOM for empty state responses. With `bomwater` 0.0.8, that fallback can raise `FileNotFoundError: ~/bom_water/cache/stations.json` on a fresh cache, so an out-of-record request may report an `error`. The adapter exposes the exception without repairing the cache or bypassing the getter. See `docs/observed-data-spike.md` for the integration limits.

## Custom EWR Builder (User EWRs)

User EWRs let you define flow spells, total-day rules, cease-to-flow rules, accumulated volumes, levels and salinity limits, with supported advanced multigauge and rate rules. They run through py_ewr and appear in the dashboard as `U_` codes. Open the **EWR guide** in **Your data**, then choose **Add EWR**. The four sections cover the gauge and planning units, rule type, parameters, and review/run options. The form validates as you type; the server validates again before saving or testing. `MinSpell` is always a positive integer no greater than Duration: its visible default is Duration for spell rules and 1 for total-day rules.

This feature requires the local analysis server and uploaded scenarios with stored daily series. It is hidden in Demo data. Review summaries and notebook-embedded scenarios have no server-side daily records, so they cannot be evaluated with this builder. Existing **Custom constraints** remain available as unevaluated flow overlays; they have not been retired.

### Series and units

For **Standard time-series**, use a UTF-8 CSV with `Date` and columns such as `404216_flow` (ML/day), `404216_level` (metres, using your model's AHD or gauge-height datum) and `404216_salinity` or `404216_ec` (EC, µS/cm). `_tds` inputs are converted by the documented server multiplier, 0.6 by default. The rule form lets you choose EC or mg/L TDS and specify the positive **TDS → EC multiplier**; thresholds are converted to canonical EC before saving. Match the convention used to prepare your series. Level and salinity series are stored alongside flows, including when adding another file into a scenario. Missing series are reported as skipped rather than evaluated as zero.

`data/example_scenarios/dummy_broken_scenario_A_level_salinity.csv` is a test fixture with made-up levels and salinity, not observations or an approved ecological rule. See its adjacent README for how it was constructed.

### Test, save and evaluate

- **Test on one scenario** evaluates the unsaved form against one compatible uploaded record. It shows FR, achieved/complete record years and the percentage of record beyond MRIP for each planning unit, without saving the definition or changing dashboard results. Cancel test stops waiting in the browser; an evaluation already started on the server may finish. Changing the definition clears the previous test result.
- **Play / Pause, Previous, Next and Replay** explain simple flow and salinity spell/total-day rules with illustrative records. These are generated examples, not gauge results; use the scenario test for the full py_ewr calculation. Advanced rules show textual guidance. Reduced-motion settings disable animated playback.
- **Save** stores a new definition without evaluating it. **Save & run** evaluates the checked uploaded scenarios. Run all is also available from each Your EWRs card. Uploading another scenario offers a run on the new record.
- **Edit** marks old results stale. Saving an evaluated definition reruns the previously evaluated uploaded scenarios; Save & run uses the checked scenarios. Stale values are excluded from aggregates and performance charts until evaluation completes. Changing code, gauge or planning units clears the previous identity's results. **Duplicate** opens a new draft with a distinct code.

Results propagate to map fills, SDL/planning-unit/gauge tables, objective and regime matrices, drought windows, joint FR–MRIP charts, annual diagnostics and EWR event charts. Flow overlays accept appropriate flow/volume rules; level and salinity event charts use metres and EC and omit flow-only volume metrics. Flow-regime component thresholds continue to describe the built-in parameter sheet. A zero MRIP leaves ETP empty and excludes that rule from joint FR–MRIP denominators.

### Export and import definitions

Choose **Export JSON** in a Your EWRs card to download `<U_CODE>.json`. Choose **Import JSON** above the cards to open an exported definition as a new draft. Review, test, then Save or Save & run. Import never overwrites an existing rule; if the code already exists, choose another `U_` code before saving. Cancelling an import draft leaves the registry unchanged.

Files contain a versioned envelope, `{"format":"ewr-user-definition","version":1,"spec":{...}}`. They contain the rule definition only: no server identity, timestamps, scenario files or evaluation results. Imported fields must match the supported schema, review gauges/planning units/objectives and handler parameters. Numeric parameters must be finite JSON numbers; Duration, MinSpell, StartMonth, EndMonth and TargetFrequency must be present. Unknown fields, invalid UTF-8, duplicate or unsafe object keys, excessive nesting, unsupported versions and files over 64 KiB are rejected with a message. Salinity imports explicitly include their unit and positive numeric conversion factor. Use an exported file as the starting example.

A copied dashboard URL contains view selections, not custom definitions or daily data. On another installation, upload the scenarios and import the JSON separately. An unknown `U_` selection falls back to an available rule or no selection.

### Persistence and deletion

The server registry is `.user_scenarios/uewr.json`. The browser mirrors definitions in `localStorage["ewrUserEwrs"]` and keeps evaluated values with its saved scenario records. Offline definitions/results can be read and definitions can be exported; saving, importing and evaluation require reconnection. Clearing browser storage removes its scenario summaries; exporting definitions does not back up those summaries or daily files.

**Delete** asks for confirmation, then removes the rule from selections, rows, indices, results, caches, guide cards and all dashboard views. It also deletes its server definition, cached diagnostics and unused merged parameter sheets. If the server is unavailable, a browser tombstone queues deletion for the next reconnect. Version and generation checks prevent delayed jobs or chart/save responses from restoring obsolete results.

### Browser acceptance check

1. Start the dashboard with a launcher, `python run.py`, or `python src/server.py`. Use the printed HTTP URL rather than opening the HTML as a file.
2. Choose **Your data → Add scenario → Standard time-series** and upload `data/example_scenarios/dummy_broken_scenario_A_level_salinity.csv`.
3. Open **EWR guide → Add EWR**. Choose gauge **404216**, its offered planning unit, Flow / Continuous spell, code `U_TRIAL`, a name, lower limit **200**, Duration **5**, MinSpell **5**, TargetFrequency **50**, MRIP **2** and a July–June window. Leave other parameters at their displayed defaults.
4. Try the tutorial controls and **Test on one scenario**. Verify the test table appears while the rule is still absent from Your EWRs. Then choose **Save & run**.
5. Select gauge 404216. Find `U_TRIAL` in the matrix, open its cell table, and inspect Flow analysis → EWR performance. In Extra data, load annual diagnostics. Reload the page and confirm the definition and evaluated results remain.
6. Export the JSON, import it, and confirm the existing code is flagged. Rename it `U_TRIAL_COPY`, save/run, and verify there are two separate rules. Try importing an edited file with MinSpell 0 or missing MinSpell: it should be rejected before saving.
7. Edit a rule's Duration/MinSpell together and save. Observe stale state while reevaluation runs. Delete the copy and verify that every selection, chart and table removes it. Switch to Demo data and confirm the builder controls and user rules disappear.
8. Optionally repeat with Level / Level spell (use limits 1–2 m) and Salinity / Continuous spell (use an upper limit of 800 EC and lower limit 0); the fixture contains all three series. These values are for exercising the tool, not management advice.

## Running on Azure ML compute (flow charts enabled)

If you have an Azure ML compute instance, you can run the server there to get the full dashboard including flow charts (heat map, hydrograph, flow duration curves). Choose **Terminal** from the compute's application list.

1. **Clone the repo and install dependencies:**

   ```bash
   git clone https://github.com/RoryANU/ewr-analysis-dashboard.git
   cd ewr-analysis-dashboard
   pip install -r requirements.txt
   ```

2. **Start the server** listening on all addresses so Azure ML's port proxy can reach it (by default it listens on `127.0.0.1` only, which blocks external access):

   ```bash
   python3 run.py --host=0.0.0.0 --no-browser
   ```

   Because this makes the tool reachable by other computers, it prints an address ending in `?token=...`. A random access token is required for every page and API request, so use that exact address and do not share it. Without `--host` (the default) the server only accepts connections from your own computer and needs no token. The tool serves only the dashboard page (never repository files or the stored uploads in `.user_scenarios/`) and has no other login: for anything beyond a private compute instance, put it behind an authenticated proxy.

3. **Open the dashboard** in your browser at:

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
pytest                    # entire Python suite, including real py_ewr runs and HTML rebuild
pytest -m "not slow"      # fast Python suite used by pre-commit
pytest -m slow            # only the slow tests: py_ewr over many years of data, and a full rebuild of the HTML
pytest tests/test_server_numerics.py -k heatmap   # one file / one topic
node --test tests/test_custom_ewr.js tests/test_dashboard_core.js tests/test_performance.js tests/test_ewr_guide.js tests/test_heatmap_selection.js
```

| File | What it checks |
|---|---|
| `test_build_pipeline.py` | each stage of `src/build_dashboard.py` (loading, scenario index, per-EWR means, themes, ROWS table, gauges, polygons, META, HTML slots) against hand-computed values on the small data set in `tests/fixtures/mini/` |
| `test_dashboard_html.py` | the data embedded in `ewr_analysis_dashboard.html`: index bounds, ordering, units, map geometry, every FR / ETP cell recomputed independently from the CSVs, a stored baseline of the aggregates, and (slow) that rebuilding reproduces the committed page byte for byte |
| `test_source_data.py` | arithmetic promised by `data/results/*.csv` (FR = Frequency ÷ Target, ETP = L ÷ record days, ...) |
| `test_server_numerics.py` | exact values for the heat map, duration curves, hydrograph, spells, gaps and runs in `src/server.py`, plus strict-JSON output |
| `test_pyewr_wrappers.py` | `ewr_list`, `load_frames`, `run_scenario` and `ewr_eval` against py_ewr's parameter table and an independent recomputation; a baseline of the example scenario's results |
| `test_ewr_index.py`, `test_ewr_guide.js` | the EWR index embedded in the page (against the CSV, including month.day windows) and the EWR guide: search, the 5-EWR cap, and for every row of the sheet that the illustrative met years meet the rule, the missed years do not, and every tutorial step renders |
| `test_custom_ewr.py`, `test_series_storage.py`, `test_custom_ewr.js` | stored flow/level/salinity series, handler equivalence, validation parity, strict JSON sharing, versioned registry, HTTP jobs, dynamic indices and deletion/race protections |
| `test_parameter_sheet_sync.py` | the local parameter sheet equals the one on GitHub (`network` test, skipped offline) |
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


The Custom EWR Builder adds no runtime or required test dependencies. The Node suites use the built-in test runner (Node 22+ from `requirements-dev.txt`). For the optional browser regressions, install Playwright in a location Node can resolve (`npm install --no-save --package-lock=false playwright`) and either install its Chromium (`npx playwright install chromium`) or set `EWR_BROWSER_EXECUTABLE` to your installed Chrome/Chromium binary. The browser tests use isolated profiles and temporary local servers with controlled API responses; they do not access your saved scenarios. The CDN chart libraries must be reachable.

```bash
node tests/test_custom_ewr_browser.cjs          # form, preview, edit/rerun, reload, Demo and deletion
node tests/test_custom_ewr_displays.cjs         # all kinds across dashboard displays; delayed deletion responses
node tests/test_custom_ewr_sharing_browser.cjs  # JSON round-trip/rejection, tutorial, unsaved tests, cancellation and mobile
node tests/test_performance_browser.cjs        # existing performance regression
mypy
pre-commit run --all-files
```

Rebuild with `python src/build_dashboard.py` before testing source changes. Finish that command before starting pytest: its byte-for-byte HTML check expects a fixed generated dashboard throughout the run.
