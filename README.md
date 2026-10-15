# EWR analysis dashboard

An interactive page for exploring environmental water requirement (EWR) results from the 2026 Basin Plan review. It averages two metrics for each EWR, up the hierarchy EWR → gauge → planning unit → SDL unit:

- **FR**, the frequency ratio (achieved ÷ target frequency)
- **ETP**, the MRIP exceedance time proportion

It can show a single scenario or the difference between a base and a comparison scenario, with an objective × EWR matrix for each SDL unit, planning unit and gauge.

The dashboard is one self-contained HTML file (`ewr_analysis_tool.html`). This repository holds the script, the page template and the input data needed to generate it.

## Generate the HTML

You need Python 3.9 or later.

1. Clone the repository and install the dependencies:

   ```bash
   git clone https://github.com/RoryANU/ewr-analysis-dashboard.git
   cd ewr-analysis-dashboard
   pip install pandas numpy shapely
   ```

2. Run the build script from the repository root:

   ```bash
   python3 basin_plan_review_2026/build_analysis_tool.py
   ```

   It prints a short summary (EWR rows, SDL units, planning units and gauges plotted) and writes the page to
   `basin_plan_review_2026/ewr_analysis_tool.html` (about 3.7 MB).

3. Open `basin_plan_review_2026/ewr_analysis_tool.html` in a web browser. No server is needed. The page loads Leaflet, Plotly and the map tiles from the internet, so it needs a connection.

Re-run the script whenever you change the template or any input file. The output file is overwritten.

## What the build reads

| File | Used for |
| --- | --- |
| `basin_plan_review_2026/ewr_analysis_tool.template.html` | the page itself: markup, styles and JavaScript, with placeholders for the data |
| `basin_plan_review_2026/all_frequency_raw.csv` | FR per EWR, gauge, planning unit, policy and climate scenario |
| `basin_plan_review_2026/all_interevent_comparison_ewr_level.csv` | ETP per EWR, gauge, planning unit, policy and climate scenario |
| `basin_plan_review_2026/sdl_resource_units/surface_water_sdl_resource_units.geojson` | SDL unit boundaries for the map |
| `basin_plan_review_2026/sdl_resource_units/hydrorivers_mdb_strahler3_plus.geojson` | rivers drawn on the map |
| `basin_plan_review_2026/bom_gauges/bom_wdo_station_list.csv` | gauge names and locations (Bureau of Meteorology Water Data Online) |
| `py_ewr/parameter_metadata/objective_reference.csv` | ecological objective codes, descriptions and themes |
| `basin_plan_review_2026/flow_regime_mappings.py` | maps an EWR code to its flow regime when the results do not give one |

The script keeps the paths relative to its own location, so keep the folder layout as it is (`py_ewr/parameter_metadata/` sits next to `basin_plan_review_2026/`).

## Notes

- The build embeds all the data in the HTML, so the generated file can be moved or shared on its own.
- Adding your own scenarios (the "Your data" mode and the flow charts) needs a small local server that runs the EWR tool, which is not part of this repository.
- The input data comes from the MDBA's 2026 Basin Plan review outputs and public datasets. Check the original licences before reusing it.
