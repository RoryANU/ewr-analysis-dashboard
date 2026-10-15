# EWR analysis dashboard

An interactive page for exploring environmental water requirement (EWR) results from the 2026 Basin Plan review. It averages two metrics for each EWR, up the hierarchy EWR → gauge → planning unit → SDL unit:

- **FR**, the frequency ratio (achieved ÷ target frequency)
- **ETP**, the MRIP exceedance time proportion

It can show a single scenario or the difference between a base and a comparison scenario, with an objective × EWR matrix for each SDL unit, planning unit and gauge.

**To explore the review results, download [`ewr_analysis_dashboard.html`](ewr_analysis_dashboard.html) and open it in a web browser.** To run your own model output through it ("Your data"), start the local server as described under [Your data](#your-data-run-your-own-scenarios). It is a single self-contained file with all the data embedded. It loads Leaflet, Plotly and the map tiles from the internet, so it needs a connection.

## Repository layout

```
ewr_analysis_dashboard.html      the built dashboard (open this)
src/
  server.py                      local server: serves the page and runs py_ewr on your own model output
  build_dashboard.py             build script: reads data/, fills the template, writes the HTML
  dashboard_template.html        the page itself (markup, styles, JavaScript) with data placeholders
  flow_regime_mappings.py        maps an EWR code to its flow regime when the results do not give one
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

## Notes

- The "Your data" mode needs the local server in `src/server.py` (see below); without it the page works in Demo data mode only.
- The input data comes from the MDBA's 2026 Basin Plan review outputs and public datasets. Check the original licences before reusing it.
