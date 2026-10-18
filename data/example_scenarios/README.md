# Synthetic level and salinity fixture

`dummy_broken_scenario_A_level_salinity.csv` retains the dates and flows from
`dummy_broken_scenario_A.csv` and adds two **made-up** daily series at gauge 404216:

- `404216_level`: arbitrary gauge height in metres, not surveyed m AHD.
- `404216_salinity`: electrical conductivity in EC (µS/cm).

These are test data, not observations or modelled water quality. With zero-based
day number `d` and `s = 2πd / 365.25`, the series are generated as:

- Level: `1.5 + 0.4 sin(s) + 0.1 sin(s / 5)`, rounded to 3 decimal places.
- Salinity: `700 + 300 sin(s) + 100 cos(s / 3)`, rounded to 1 decimal place.

The fixture covers 1 July 1995 to 30 June 2020 without gaps and can be uploaded as
Standard time-series. It supplies level and salinity data for subsequent user EWR
engine tests. Salinity imports also accept `_ec` (EC) and `_tds` (multiplied by the
user's TDS-to-EC factor; backend default 0.6). No unit is inferred from values.
