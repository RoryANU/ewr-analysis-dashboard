# EWR analysis dashboard

An interactive page for exploring environmental water requirement (EWR) results from the 2026 Basin Plan review. It averages two metrics for each EWR, up the hierarchy EWR → gauge → planning unit → SDL unit:

- **FR**, the frequency ratio (achieved ÷ target frequency)
- **ETP**, the MRIP exceedance time proportion

It can show a single scenario or the difference between a base and a comparison scenario, with an objective × EWR matrix for each SDL unit, planning unit and gauge.

**To use it, download [`ewr_analysis_dashboard.html`](ewr_analysis_dashboard.html) and open it in a web browser.** It is a single self-contained file with all the data embedded. It loads Leaflet, Plotly and the map tiles from the internet, so it needs a connection.

## Repository layout

```
ewr_analysis_dashboard.html      the built dashboard (open this)
src/
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

## Notes

- The "Your data" mode of the dashboard (adding your own model scenarios, with the flow charts) needs a small local server that runs the EWR tool. It is not part of this repository, so the page here works in Demo data mode only.
- The input data comes from the MDBA's 2026 Basin Plan review outputs and public datasets. Check the original licences before reusing it.
