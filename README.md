# EWR analysis dashboard

An interactive page for exploring environmental water requirement (EWR) results from the 2026 Basin Plan review. It averages two metrics for each EWR, up the hierarchy EWR → gauge → planning unit → SDL unit:

- **FR**, the frequency ratio (achieved ÷ target frequency)
- **ETP**, the MRIP exceedance time proportion

It can show a single scenario or the difference between a base and a comparison scenario, with an objective × EWR matrix for each SDL unit, planning unit and gauge.

## Quick start (no technical knowledge needed)

The tool runs on your own computer: you start it, and it opens in your web browser. You can load your own model output (a CSV or other supported file), run the EWR calculations on it, and see the charts update straight away.

1. **Install Python** (once). Download version 3.9 or newer from <https://www.python.org/downloads/>. On Windows, tick **"Add python.exe to PATH"** in the installer.
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

You need Python 3.9 or later.

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

4. **Open the dashboard** in your browser at:

   ```
   https://<compute-name>-8792.<region>.instances.azureml.ms/ewr_analysis_dashboard.html
   ```

   Find `<compute-name>` under **Compute → Compute instances** in Azure ML Studio. Find `<region>` in your workspace's **Overview → Location** (e.g. `australiaeast`). If you already have JupyterLab or VS Code open from that compute, the browser URL bar contains both values.


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

## Notes

- The "Your data" mode needs the local server (start it with `start.command` / `start.bat` / `python3 run.py`); without it the page works in Demo data mode only.
- The input data comes from the MDBA's 2026 Basin Plan review outputs and public datasets. Check the original licences before reusing it.
