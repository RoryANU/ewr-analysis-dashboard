# EWR analysis dashboard

An interactive page for exploring environmental water requirement (EWR) results from the 2026 Basin Plan review. It averages two metrics for each EWR, up the hierarchy EWR → gauge → planning unit → SDL unit:

- **FR**, the frequency ratio (achieved ÷ target frequency)
- **ETP**, the MRIP exceedance time proportion

It can show a single scenario or the difference between a base and a comparison scenario, with an objective × EWR matrix for each SDL unit, planning unit and gauge.

**To explore the review results, download [`ewr_analysis_dashboard.html`](ewr_analysis_dashboard.html) and open it in a web browser.** To run your own model output through it ("Your data"), either start the local server ([Your data](#your-data-run-your-own-scenarios)) or, without a command line, use the Databricks notebook ([Working from Databricks](#working-from-databricks-no-command-line-no-server)). It is a single self-contained file with all the data embedded. It loads Leaflet, Plotly and the map tiles from the internet, so it needs a connection.

## Repository layout

```
ewr_analysis_dashboard.html      the built dashboard (open this)
src/
  embed_scenarios.py             runs py_ewr on your files and embeds the results in a copy of the page (used by the notebook)
  server.py                      local server: serves the page and runs py_ewr on your own model output
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
  reference/
    objective_reference.csv      ecological objective codes, descriptions and themes
  example_scenarios/             small made-up daily flow files for trying "Your data" (Broken and Goulburn gauges)
requirements.txt                 Python packages for the build and the server
```

## Rebuild the HTML

You need Python 3.9 or later.

```bash
git clone https://github.com/RoryANU/ewr-analysis-dashboard.git
cd ewr-analysis-dashboard
pip install pandas numpy shapely
python3 src/build_dashboard.py
```

The script prints a short summary (EWR rows, SDL units, planning units and gauges plotted) and overwrites `ewr_analysis_dashboard.html` in the repository root (about 3.7 MB). Run it again whenever you change the template or any file in `data/`. It finds its inputs relative to its own location, so keep the folder layout as it is.

## Your data: run your own scenarios

The page cannot run the EWR tool by itself, so "Your data" uses a small server on your own computer. Python 3.9 or later.

```bash
pip install -r requirements.txt
python3 src/server.py
```

Then open <http://localhost:8792/ewr_analysis_dashboard.html> (the page must be opened from this address, not as a file). Choose **Your data**, then **Add scenario**, pick the data format, and choose your file. You do not choose an SDL unit: the file is scanned for every gauge in the review, so one file can cover several SDL units. The scenario name defaults to the file name. The results are FR and ETP per EWR, and they appear as an extra scenario in the pickers (Historical climate only).

- Formats: Standard time-series, Bigmod, FIRM, res.csv (MDBA), Source NSW res.csv, IQQM netCDF and ten-thousand-year runs. A Standard time-series file has a `Date` column and one column per gauge named `<gauge>_flow` or `<gauge>_level`, e.g. `404216_flow`.
- Try it with the files in `data/example_scenarios/` (`dummy_broken_scenario_A.csv` and `..._B_wetter.csv` for Differential mode).
- The server listens on localhost only. Uploaded flows are kept in `.user_scenarios/` (not committed) so the flow charts can be drawn.
- The EWR calculations are done by [py_ewr](https://pypi.org/project/py-ewr/) (version 2.4.11 or later).

## Working from Databricks (no command line, no server)

If you can only run code in Databricks notebooks, use the notebook `notebooks/run_your_scenarios.ipynb`. It runs the EWR tool on your model output inside Databricks and writes a copy of the dashboard with your results built in. You then download that one HTML file and open it in your browser: no localhost and no terminal.

1. **Add the repository as a Git folder** (Workspace, Create, Git folder, paste `https://github.com/RoryANU/ewr-analysis-dashboard.git`). Keep the folder layout.
2. **Upload your model output file(s)** to a Unity Catalog volume (Catalog, your volume, Upload to this volume), or into the workspace. Note the path, e.g. `/Volumes/<catalog>/<schema>/<volume>/my_run.csv`. Any of `/Volumes/...`, `/Workspace/...`, `/dbfs/...` or `dbfs:/...` works; give the full path of the file, not a folder.
3. **Open `notebooks/run_your_scenarios.ipynb`** and attach it to a cluster (Python 3.9 or later; serverless is fine).
4. **Edit the `SCENARIOS` list** in the `SCENARIOS` cell (the first cell with a list in it): one entry per file, with the file `path`, its `format` (the notebook lists the options) and an optional `name` (it defaults to the file name). You do not choose an SDL unit: each file is scanned for every gauge in the review, so one file can cover several SDL units.
5. **Run all.** The first cells install `py_ewr` (about a minute). The notebook prints how many EWR results and gauges it found in each file, saves `ewr_dashboard_with_scenarios.html` in the Git folder, and shows the dashboard under the last cell.
6. **Download `ewr_dashboard_with_scenarios.html`** (Git folder, the file's menu, Download) and open it in your browser. It opens in **Your data** with your scenarios in the Scenario / Base / Comparison pickers. For differences between two runs, put both files in `SCENARIOS` and switch to Differential. To share it, send the HTML file: everything is inside it (it loads the map libraries from the internet).

Limits of this route: the flow charts (heat map, hydrograph, flow duration curves) need the local server, so they are not in the page; the page itself cannot add a new scenario, so to add or change a scenario edit `SCENARIOS` and run the notebook again. The file is not committed (it is in `.gitignore`).

To rebuild the base dashboard itself after changing the template or `data/`, run in a notebook cell: `%pip install pandas numpy shapely`, then `!python src/build_dashboard.py` from the repository folder, and commit the new `ewr_analysis_dashboard.html`.

If you can run a terminal somewhere (for example a laptop or a Databricks web terminal), the "Your data" section above (`python3 src/server.py`) lets you add scenarios inside the page and use the flow charts.

## Notes

- The "Your data" mode needs the local server in `src/server.py` (see below); without it the page works in Demo data mode only.
- The input data comes from the MDBA's 2026 Basin Plan review outputs and public datasets. Check the original licences before reusing it.
