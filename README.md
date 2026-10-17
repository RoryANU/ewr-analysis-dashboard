# EWR analysis dashboard

An interactive page for exploring environmental water requirement (EWR) results from the 2026 Basin Plan review. It averages two metrics for each EWR, up the hierarchy EWR → gauge → planning unit → SDL unit:

- **FR**, the frequency ratio (achieved ÷ target frequency)
- **ETP**, the MRIP exceedance time proportion

It can show a single scenario or the difference between a base and a comparison scenario, with an objective × EWR matrix for each SDL unit, planning unit and gauge.

## Viewing the dashboard (no Python, no server)

**Download [`index.html`](index.html) and double-click it.** It opens in your web browser straight from your computer (a `file://` address). Nothing to install, no command line, no server.

- Everything is inside that one file: the review results, the map layers, the chart and map libraries, and any scenarios of your own that were built into it, including the daily flows behind the flow charts.
- It works offline. The only thing it loads from the internet is the grey background map (place names, terrain); without a connection the page says so, and the SDL units, rivers, gauges, tables and charts still work.
- To share it, send the file. It is about 10 MB.
- **Demo data** shows the review's results (all policies and climates). **Your data** shows scenarios of your own run through the EWR tool, with the flow charts. The `index.html` in this repository includes two small made-up example scenarios so you can try it. In Your data, **Your scenarios** (top right) lists what is in the page.
- The page cannot run the EWR tool itself. To add your own model output, someone with Python rebuilds the page with it (next section) and sends you the new file.

## Building the dashboard (for builders)

You need Python 3.9 or later.

```bash
git clone https://github.com/RoryANU/ewr-analysis-dashboard.git
cd ewr-analysis-dashboard
pip install -r requirements.txt
python build.py
```

That one command writes `index.html` in the repository root. It reads the review results and map layers in `data/`, runs [py_ewr](https://pypi.org/project/py-ewr/) (version 2.4.11 or later) on every scenario listed in `scenarios.json`, and embeds everything in the page: the data as inline JSON, and Leaflet and Plotly inlined from `vendor/` (so the build itself needs no internet). The page makes no requests to a server. A build takes about 20 seconds plus the EWR runs.

Your own model output:

```bash
python build.py --scenario my_run.csv                      # one scenario per file; the page opens in "Your data"
python build.py --scenario base.csv --scenario option.csv  # two, for Differential mode
python build.py --scenario run.csv --format "Bigmod - MDBA"
python build.py --no-scenarios                             # review results only (py_ewr not needed)
python build.py --out somewhere/else.html
```

For several files per scenario, other formats per file, or fixed names, list them in `scenarios.json` (paths relative to that file) and run `python build.py` (or `python build.py --config my_scenarios.json`):

```json
{
  "open_in": "user",
  "scenarios": [
    {"name": "Base case", "format": "Standard time-series", "path": "runs/base.csv"},
    {"name": "Option 1", "format": "Standard time-series", "path": ["runs/opt1_north.csv", "runs/opt1_south.csv"]}
  ]
}
```

- `open_in`: `"demo"` (the review results; the default) or `"user"` (your scenarios) when the page opens.
- `format`: Standard time-series, Bigmod - MDBA, FIRM - MDBA, res.csv - MDBA, Source - NSW (res.csv), IQQM - netcdf, ten thousand year or Northern connectivity (add `"sub": "CP1A"` to pick the workbook's scenario). A Standard time-series file has a `Date` column and one column per gauge named `<gauge>_flow` or `<gauge>_level`, e.g. `404216_flow`; the page's **Your scenarios** dialog can download a template header.
- Each file is scanned for every gauge in the review, so one file can cover several SDL units; several files in one scenario are merged. The results are FR and ETP per EWR, and they appear as extra scenarios in the pickers (Historical climate only).
- Flow charts (daily flows are kept for every gauge with a flow series; not for ten-thousand-year runs): open a gauge, then **Flow analysis**, and pick a chart from the **Chart** dropdown. *Flow*: threshold × duration heat map, hydrograph with annual volumes, and (Differential) the differential and monthly differential flow duration curves. *EWR performance* (py_ewr's own results for one EWR): achievement by year, events, event timing, gaps between events against the maximum inter-event period. *Flow regime*: flow regime components (each regime's threshold is the lowest EWR threshold for it at the gauge, from the EWR tool's parameter sheet) and low-flow spells. In Differential mode, comparisons use only the dates or years both scenarios cover. Whatever needs py_ewr is computed at build time; the charts' adjustable parts (thresholds, durations, timing windows, zoom) are computed in the page by `src/flow_engine.js`, which is tested against the Python reference in `src/ewr_engine.py`.
- Run the build again whenever you change the template, `data/` or your scenarios, and commit the new `index.html`.

Checks (for development): `pip install -r requirements-dev.txt`, then `python -m pytest tests` and `ruff check build.py tests/test_standalone_build.py tests/test_flow_engine_parity.py`.

## Repository layout

```
index.html                       the built dashboard (open this)
build.py                         the build command: python build.py -> index.html
scenarios.json                   scenarios to run through py_ewr and build into the page (the two made-up examples)
src/
  build_dashboard.py             the build: reads data/, runs the scenarios, embeds everything in the template
  dashboard_template.html        the page itself (markup, styles, JavaScript) with placeholders for the libraries and data
  flow_engine.js                 the flow charts' calculations, run in the page on the embedded flows
  ewr_engine.py                  py_ewr runs and the Python reference for the flow charts (used by the build)
  embed_scenarios.py             runs py_ewr on your files and puts the results into a built page (used by the notebook)
  flow_regime_mappings.py        maps an EWR code to its flow regime when the results do not give one
  northern_connectivity.py       reader for the northern connectivity workbook format
vendor/                          Leaflet 1.9.4 and Plotly 2.27.0 (copies of the cdnjs files, inlined into the page; sha256-checked)
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
  example_scenarios/             small made-up daily flow files (Broken and Goulburn gauges), used by scenarios.json
requirements.txt                 Python packages for the build
requirements-dev.txt             test and lint tools
```

## Working from Databricks (no command line)

If you can only run code in Databricks notebooks, use the notebook `notebooks/run_your_scenarios.ipynb`. It runs the EWR tool on your model output inside Databricks and writes a copy of the dashboard with your results built in. You then download that one HTML file and open it in your browser.

1. **Add the repository as a Git folder** (Workspace, Create, Git folder, paste `https://github.com/RoryANU/ewr-analysis-dashboard.git`). Keep the folder layout.
2. **Upload your model output file(s)** to a Unity Catalog volume (Catalog, your volume, Upload to this volume), or into the workspace. Note the path, e.g. `/Volumes/<catalog>/<schema>/<volume>/my_run.csv`. Any of `/Volumes/...`, `/Workspace/...`, `/dbfs/...` or `dbfs:/...` works; give the full path of the file, not a folder.
3. **Open `notebooks/run_your_scenarios.ipynb`** and attach it to a cluster (Python 3.9 or later; serverless is fine).
4. **Edit the `SCENARIOS` list** in the `SCENARIOS` cell (the first cell with a list in it): one entry per file, with the file `path`, its `format` (the notebook lists the options) and an optional `name` (it defaults to the file name). You do not choose an SDL unit: each file is scanned for every gauge in the review, so one file can cover several SDL units.
5. **Run all.** The first cells install `py_ewr` (about a minute). The notebook prints how many EWR results and gauges it found in each file, saves `ewr_dashboard_with_scenarios.html` in the Git folder, and shows the dashboard under the last cell.
6. **Download `ewr_dashboard_with_scenarios.html`** (Git folder, the file's menu, Download) and double-click it. It opens in **Your data** with your scenarios in the Scenario / Base / Comparison pickers, flow charts included. For differences between two runs, put both files in `SCENARIOS` and switch to Differential. To share it, send the HTML file: everything is inside it.

The notebook starts from the built `index.html` in the Git folder, so it does not rebuild the review data. To add or change a scenario, edit `SCENARIOS` and run the notebook again. The output file is not committed (it is in `.gitignore`).

To rebuild the base dashboard itself after changing the template or `data/`, run in a notebook cell: `%pip install -r requirements.txt`, then `!python build.py` from the repository folder, and commit the new `index.html`.

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

## Notes

- The "Your data" mode shows only scenarios built into the page (`python build.py`, or the Databricks notebook); a page built without any works in Demo data mode only.
- The input data comes from the MDBA's 2026 Basin Plan review outputs and public datasets. Check the original licences before reusing it.
