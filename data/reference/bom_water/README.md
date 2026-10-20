# bomwater cache, shipped with the repository

`bomwater` (a dependency of `mdba_gauge_getter`) insists on a cache folder and, by default, builds it in `~/bom_water/cache` by
downloading the Bureau's station list. That download is slow and currently fails (the service no longer returns the SOAP
envelope the package expects), which leaves a half-built cache and makes every Bureau fallback raise
`FileNotFoundError: ~/bom_water/cache/stations.json`.

`src/observed.py` points the package at this folder instead (`_use_repo_bom_cache`), so nothing outside the repository is read or
written and nothing is downloaded at run time.

- `waterML_GetCapabilities.json`: the Bureau's SOS capabilities document (observable properties and procedures that the package
  maps to `Water_Course_Discharge`, `Pat4_C_B_1_DailyMean`, and so on). Retrieved 2026-10-19 from the Bureau's water data service.
- `stations.json`: deliberately an empty GeoJSON `FeatureCollection`. The package only uses it to build a name lookup
  (`BomWater.features`) that `mdba_gauge_getter` never reads; observations are requested by station number.

To refresh the capabilities file, delete it, run `HOME=/some/empty/dir python -c "import bom_water; bom_water.BomWater()"`
and copy the generated `waterML_GetCapabilities.json` here (the station list step will fail; keep the empty `stations.json`).
