# Observed-data fixtures

Response bodies from the Kisters Hydstra `get_ts_traces` web services that `mdba-gauge-getter` calls (WaterNSW `realtimedata.waternsw.com.au/cgi/webservice.exe`, Victoria `data.water.vic.gov.au/cgi/webservice.exe`, Queensland `water-monitoring.information.qld.gov.au/cgi/webservice.pl`), using the getter's own request payload.

Every service answers in JSON with HTTP 200, including errors (`error_num` at the top level or per trace). The services do not return HTML tables, ASP.NET view state or form tokens; HTML appears only on their landing pages.

**Captured live on 2026-10-19, unmodified:** `nsw_412033_flow.json`, `nsw_412033_level.json`, `nsw_412033_412002_flow.json`, `nsw_no_data_in_period.json` (top-level `error_num` 126), `nsw_invalid_station.json` (per-trace `error_num` 20), `nsw_missing_version.json` (`error_num` 120, the reply to a call without a payload), `nsw_412033_variable_list.json` and `nsw_invalid_station_variable_list.json` (`get_variable_list`, which gives each variable's record period), `vic_405201_flow.json`, `qld_422213A_flow.json`, `qld_invalid_station.json` (422030 asked of Queensland; WaterNSW serves it).

**Captured live, status not recorded:** `qld_iis_client_timeout.txt`, the plain-text IIS reply Queensland sent twice in a row after a slow full-record request, in place of JSON.

**Captured live, trace list trimmed** (points and quality-code descriptions are a subset of the live reply; nothing else edited): `nsw_412033_flow_full_record_trimmed.json` and `vic_405201_flow_full_record_trimmed.json`, 1900-01-01 to 2026-10-19. They show that days without data come back as `"0.000"`/`"0.00"` under quality 255 ("Data Unavailable"), 201 ("Data unavailable", NSW) or 151 ("Data lost", VIC), and that the current day carries a partial value coded 255.

**Synthetic** (failure shapes not reproducible on demand): `synthetic_maintenance_200.html` (an HTML banner served with 200), `synthetic_gateway_502.html` (a front-door error page), `synthetic_queue_full.json` (the MDBA queue-full message the branch already handled), `synthetic_truncated.json`, `synthetic_legacy_underscore_return.json` (the `_return` key the getter handles), `synthetic_unexpected_units.json`, `synthetic_compressed_trace.json` and `synthetic_messy_points.json` (unsorted/duplicate days, blank and non-numeric values, a malformed timestamp, a quality code at or above the getter's 999 cutoff).
