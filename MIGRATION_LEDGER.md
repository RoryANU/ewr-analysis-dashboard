# Migration ledger

Phase 0 inventory, updated for Phase 1 slice 1 only. Destinations are relative to
`src/ewr_analysis/` and follow REORGANIZATION_PLAN.md §§2 and 3.1. Old symbols
refer to `src/server.py` at `pre-refactor`. One row covers each function,
including class methods and nested helpers (117 initial server rows). Slice 1
adds rows for the drought/flow-regime functions and lookup constants.

`TBD` means unverified or undecided; a directory is a proposed destination,
not a decision about a future symbol name. Mixed functions still require the
parse/load/compute split in §3.1. Hidden inputs list obvious module globals,
query dictionaries and request/closure state; this is not a complete audit.
Unverified callers and per-symbol baseline coverage remain TBD. Existing
snapshots stay unchanged. Only the listed slice 1 symbols are marked moved.

The user authorized `DROUGHTS` and `flow_mappings` as module-level constants
for this slice after a grep/read audit found no mutations. `DROUGHTS` is slated
for `config/drought_windows.toml`; `flow_mappings` is slated for `config/*.toml`
(filename TBD). Neither table is converted to TOML in this slice. No symbols
were excluded after this explicit authorization. Slice 2 remains pending.

| old symbol | new symbol | category | hidden inputs | callers | baseline test | status |
|---|---|---|---|---|---|---|
| `server._safe_id` | TBD: core / data_io (§3.1) | helper | TBD | TBD | TBD | pending |
| `server._iso_date` | TBD: core / data_io (§3.1) | helper | TBD | TBD | TBD | pending |
| `server._is_multigauge` | TBD: core / data_io (§3.1) | helper | TBD | TBD | TBD | pending |
| `server._dispatch_key` | TBD: core / data_io (§3.1) | helper | TBD | TBD | TBD | pending |
| `server._mrip_days` | `ewr_analysis.core.calculations.event_gaps._mrip_days` | pure calculation | no application state | `server.summarise_rows`; `server.ewr_eval` | tests/test_pyewr_wrappers.py::TestRunScenario::test_fr_and_etp_agree_with_an_independent_recomputation_from_py_ewr (indirect); tests/core/test_leaf_calculations.py::test_mrip_days_365_day_year_and_even_rounding | moved (shim in place) |
| `server._authority` | interfaces/server/security.py | HTTP security | TBD | TBD | TBD | pending |
| `server._loopback_host` | interfaces/server/security.py | HTTP security | TBD | TBD | TBD | pending |
| `server._state_change_allowed` | interfaces/server/security.py | HTTP security | TBD | TBD | TBD | pending |
| `server.load_frames` | data_io/scenario_files.py | file I/O | TBD | TBD | TBD | pending |
| `server._salinity_frame` | data_io/scenario_files.py | file I/O | TBD | TBD | TBD | pending |
| `server._pool_init` | core/ewr_evaluation.py (pool orchestration split TBD) | evaluation / orchestration | `_POOL` | TBD | TBD | pending |
| `server._eval_gauge` | core/ewr_evaluation.py (pool orchestration split TBD) | evaluation / orchestration | `_POOL` | TBD | TBD | pending |
| `server.evaluate_gauges` | core/ewr_evaluation.py (pool orchestration split TBD) | evaluation / orchestration | TBD | TBD | TBD | pending |
| `server.summarise_rows` | core/calculations/frequency_ratio.py (gap/year helpers split TBD) | science | TBD | TBD | TBD | pending |
| `server._has_any_event` | core/calculations/ (module TBD) | science helper | TBD | TBD | TBD | pending |
| `server._successful_summary` | core/calculations/ (module TBD) | science helper | TBD | TBD | TBD | pending |
| `server._group_by_ewr` | core/calculations/ (module TBD) | science helper | TBD | TBD | TBD | pending |
| `server._dateless_gap_stats` | core/calculations/event_gaps.py | science | TBD | TBD | TBD | pending |
| `server._assessment_year_stats` | core/calculations/achievement_years.py | science | `LOW_FLOW_HANDLERS` | TBD | TBD | pending |
| `server.run_scenario` | workflows/run_analysis.py | analysis orchestration | `FORMATS`; `SERIES_SUFFIX`; `STORE` | TBD | TBD | pending |
| `server._partner_gauges` | TBD | TBD | TBD | TBD | TBD | pending |
| `server._read_upload` | data_io/scenario_files.py | file I/O | TBD | TBD | TBD | pending |
| `server._store_series` | data_io/series_store.py | series I/O / cache | `SERIES_SUFFIX`; `STORE` | TBD | TBD | pending |
| `server.worker` | workflows/jobs.py | job orchestration | `JOBS` | TBD | TBD | pending |
| `server.observed_worker` | workflows/jobs.py | job orchestration | `JOBS`; `OBSERVED_ACTIVE`; `OBSERVED_ACTIVE_LOCK` | TBD | TBD | pending |
| `server.observed_worker._measurement_payload` | TBD (nested helper of observed_worker) | nested helper | `enclosing function state (TBD)` | TBD | TBD | pending |
| `server.observed_worker.report_gauge` | TBD (nested helper of observed_worker) | nested helper | `enclosing function state (TBD)` | TBD | TBD | pending |
| `server.observed_worker.report_progress` | TBD (nested helper of observed_worker) | nested helper | `enclosing function state (TBD)` | TBD | TBD | pending |
| `server._observed_scenario_id` | TBD: data_io/series_store.py / workflows/jobs.py (§3.1) | mixed I/O / orchestration | TBD | TBD | TBD | pending |
| `server.observed_units` | TBD: data_io/series_store.py / workflows/jobs.py (§3.1) | mixed I/O / orchestration | `ROOT` | TBD | TBD | pending |
| `server.uewr_worker` | workflows/jobs.py | job orchestration | `JOBS` | TBD | TBD | pending |
| `server._progress_reporter` | workflows/jobs.py | job orchestration | TBD | TBD | TBD | pending |
| `server._progress_reporter.progress` | TBD (nested helper of _progress_reporter) | nested helper | `enclosing function state (TBD)` | TBD | TBD | pending |
| `server._record_job_error` | workflows/jobs.py | job orchestration | TBD | TBD | TBD | pending |
| `server.load_series` | data_io/series_store.py | series I/O / cache | `FLOWS`; `SERIES_SUFFIX`; `STORE` | TBD | TBD | pending |
| `server.load_flows` | data_io/series_store.py | series I/O / cache | TBD | TBD | TBD | pending |
| `server.forget_cached` | data_io/series_store.py | series I/O / cache | `FLOWS`; `SERIES_SUFFIX`; `_EVAL_CACHE` | TBD | TBD | pending |
| `server.daily_series` | data_io/series_store.py | series I/O / cache | TBD | TBD | TBD | pending |
| `server.gauge_series` | data_io/series_store.py | series I/O / cache | TBD | TBD | TBD | pending |
| `server.nice_ceiling` | `ewr_analysis.core.calculations.axis_limits.nice_ceiling` | pure calculation | no application state | `server.heatmap`; `server._monthly_differential_fdc` | tests/test_server_numerics.py::TestScalarHelpers::test_nice_ceiling; tests/core/test_leaf_calculations.py::test_nice_ceiling_by_decade | moved (shim in place) |
| `server._json_safe` | TBD | TBD | TBD | TBD | TBD | pending |
| `server._window_days` | `ewr_analysis.core.calculations.assessment_years._window_days` | pure calculation | no application state | `server.achievement_frequencies` | tests/test_server_numerics.py::TestScalarHelpers::test_window_days; tests/core/test_leaf_calculations.py::test_window_days_uses_ending_label_and_inclusive_months | moved (shim in place) |
| `server.achievement_frequencies` | core/calculations/achievement_years.py | science | TBD | TBD | TBD | pending |
| `server.parameter_sheet` | data_io/parameter_sheet.py | parameter I/O / cache | `_EWR_CACHE` | TBD | TBD | pending |
| `server._builtin_ewr_list` | TBD: core / data_io (§3.1) | mixed parameter access / science | TBD | TBD | TBD | pending |
| `server._builtin_ewr_list.number` | TBD (nested helper of _builtin_ewr_list) | nested helper | `enclosing function state (TBD)` | TBD | TBD | pending |
| `server.ewr_list` | TBD: core / data_io (§3.1) | mixed parameter access / science | TBD | TBD | TBD | pending |
| `server.validate_diagnostic_gauge` | TBD: core / data_io (§3.1) | mixed parameter access / science | TBD | TBD | TBD | pending |
| `server.diagnostic_handlers` | TBD: core / data_io (§3.1) | mixed parameter access / science | `_EWR_CACHE`; `_EWR_LOCK` | TBD | TBD | pending |
| `server._builtin_diagnostic_inventory` | TBD: core / data_io (§3.1) | mixed parameter access / science | TBD | TBD | TBD | pending |
| `server.diagnostic_inventory` | TBD: core / data_io (§3.1) | mixed parameter access / science | TBD | TBD | TBD | pending |
| `server.diagnostic_payload` | TBD: core / data_io (§3.1) | mixed parameter access / science | `_EWR_CACHE`; `_EWR_LOCK` | TBD | TBD | pending |
| `server.record_coverage` | core/calculations/achievement_years.py | science | TBD | TBD | TBD | pending |
| `server._water_year` | `ewr_analysis.core.calculations.assessment_years._water_year` | pure calculation | no application state | `server._runs_by_year`; `server._hydrograph` | tests/test_server_numerics.py::TestScalarHelpers::test_water_year_label_is_the_starting_calendar_year; tests/core/test_leaf_calculations.py::test_water_year_start_label_and_july_boundary | moved (shim in place) |
| `server.ewr_eval` | core/ewr_evaluation.py (I/O/cache split TBD) | evaluation / cache | `LOW_FLOW_HANDLERS`; `_EVAL_CACHE` | TBD | TBD | pending |
| `server.ewr_eval.param` | TBD (nested helper of ewr_eval) | nested helper | `enclosing function state (TBD)` | TBD | TBD | pending |
| `server.ewr_eval.optional_param` | TBD (nested helper of ewr_eval) | nested helper | `enclosing function state (TBD)` | TBD | TBD | pending |
| `server._calculate_stock_ewr` | core/ewr_evaluation.py (I/O/cache split TBD) | evaluation / cache | TBD | TBD | TBD | pending |
| `server._calculate_user_ewr` | core/ewr_evaluation.py (I/O/cache split TBD) | evaluation / cache | TBD | TBD | TBD | pending |
| `server._successful_tables` | core/ewr_evaluation.py (I/O/cache split TBD) | evaluation / cache | TBD | TBD | TBD | pending |
| `server._successful_tables.pick` | TBD (nested helper of _successful_tables) | nested helper | `enclosing function state (TBD)` | TBD | TBD | pending |
| `server.ewr_gaps` | core/calculations/event_gaps.py | science | TBD | TBD | TBD | pending |
| `server.gap_severity` | core/calculations/event_gaps.py | science | TBD | TBD | TBD | pending |
| `server.record_days` | `ewr_analysis.core.calculations.assessment_years.record_days` | pure calculation | no application state | `server.summarise_rows` | tests/test_pyewr_wrappers.py::TestRunScenario::test_matches_the_baseline_snapshot (indirect); tests/core/test_leaf_calculations.py::test_record_days_trims_and_fills_without_mutating | moved (shim in place) |
| `server.assessment_start` | core/calculations/achievement_years.py | science | TBD | TBD | TBD | pending |
| `server._timing_mask` | core/calculations/achievement_years.py | science | TBD | TBD | TBD | pending |
| `server.complete_years` | core/calculations/achievement_years.py | science | TBD | TBD | TBD | pending |
| `server.dr_years` | core/calculations/duration_ratio.py | science | TBD | TBD | TBD | pending |
| `server.vr_years` | core/calculations/duration_ratio.py | science | TBD | TBD | TBD | pending |
| `server.year_summary` | core/calculations/achievement_years.py | science | TBD | TBD | TBD | pending |
| `server.window_metrics` | core/calculations/achievement_years.py | science | TBD | TBD | TBD | pending |
| `server.interevents` | TBD: routes → workflow → core split (§3.1) | mixed endpoint / science | `q (raw HTTP query)` | TBD | TBD | pending |
| `server._scenario_ids` | TBD: routes → workflow → core split (§3.1) | mixed endpoint / science | `q (raw HTTP query)` | TBD | TBD | pending |
| `server.ewr_years` | TBD: routes → workflow → core split (§3.1) | mixed endpoint / science | `q (raw HTTP query)` | TBD | TBD | pending |
| `server._ewr_year_table` | TBD: routes → workflow → core split (§3.1) | mixed endpoint / science | TBD | TBD | TBD | pending |
| `server._ewr_year_table.column` | TBD (nested helper of _ewr_year_table) | nested helper | `enclosing function state (TBD)` | TBD | TBD | pending |
| `server._ewr_events` | TBD: routes → workflow → core split (§3.1) | mixed endpoint / science | TBD | TBD | TBD | pending |
| `server._paired_achievement` | core/calculations/paired_comparison.py | science | TBD | TBD | TBD | pending |
| `server.mcnemar_p` | `ewr_analysis.core.calculations.paired_comparison.mcnemar_p` | pure calculation | no application state | `server._paired_achievement` | tests/test_server_numerics.py::TestMcNemar::test_exact_two_sided_p_by_hand; tests/core/test_leaf_calculations.py::test_mcnemar_against_exact_enumerated_binomial | moved (shim in place) |
| `server._feb29` | `ewr_analysis.core.calculations.assessment_years._feb29` | pure calculation | no application state | `core.calculations.assessment_years._water_year_days` | tests/test_server_numerics.py::TestScalarHelpers::test_feb29_belongs_to_the_water_year_that_ends_in_a_leap_year; tests/core/test_leaf_calculations.py::test_feb29_gregorian_century_rules | moved (shim in place) |
| `server._water_year_days` | `ewr_analysis.core.calculations.assessment_years._water_year_days` | pure calculation | no application state | `server._runs_by_year`; `server._hydrograph` | tests/test_gauge_analysis.py::SpellLeapYearTest::test_leap_water_year_is_complete_with_366_days (indirect); tests/core/test_leaf_calculations.py::test_water_year_days_from_calendar_length | moved (shim in place) |
| `server._runs_by_year` | core/calculations/spells.py | science | TBD | TBD | TBD | pending |
| `server._pair_years` | core/calculations/paired_comparison.py | science | TBD | TBD | TBD | pending |
| `server.spells` | TBD: routes → workflow → core split (§3.1) | mixed endpoint / science | `q (raw HTTP query)` | TBD | TBD | pending |
| `server.regimes` | TBD: routes → workflow → core split (§3.1) | mixed endpoint / science | `q (raw HTTP query)` | TBD | TBD | pending |
| `server.heatmap` | TBD: routes → workflow → core split (§3.1) | mixed endpoint / science | `q (raw HTTP query)` | TBD | TBD | pending |
| `server.heatmap.as_grid` | TBD (nested helper of heatmap) | nested helper | `enclosing function state (TBD)` | TBD | TBD | pending |
| `server.exceed_pct` | `ewr_analysis.core.calculations.flow_duration_curves.exceed_pct` | pure calculation | no application state | `server._monthly_differential_fdc`; `server._window_sorted_flows`; `server._duration_curves` | tests/test_server_numerics.py::TestScalarHelpers::test_exceed_pct; tests/core/test_leaf_calculations.py::test_exceed_pct_includes_equal_values | moved (shim in place) |
| `server._paired` | TBD: routes → workflow → core split (§3.1) | mixed endpoint / science | `q (raw HTTP query)` | TBD | TBD | pending |
| `server.curves` | TBD: routes → workflow → core split (§3.1) | mixed endpoint / science | `q (raw HTTP query)` | TBD | TBD | pending |
| `server._hydrograph` | TBD: routes → workflow → core split (§3.1) | mixed endpoint / science | `q (raw HTTP query)` | TBD | TBD | pending |
| `server._monthly_differential_fdc` | TBD: routes → workflow → core split (§3.1) | mixed endpoint / science | `q (raw HTTP query)` | TBD | TBD | pending |
| `server._monthly_differential_fdc.as_grid` | TBD (nested helper of _monthly_differential_fdc) | nested helper | `enclosing function state (TBD)` | TBD | TBD | pending |
| `server._window_sorted_flows` | TBD: routes → workflow → core split (§3.1) | mixed endpoint / science | `q (raw HTTP query)` | TBD | TBD | pending |
| `server._window_sorted_flows.windowed` | TBD (nested helper of _window_sorted_flows) | nested helper | `enclosing function state (TBD)` | TBD | TBD | pending |
| `server._duration_curves` | TBD: routes → workflow → core split (§3.1) | mixed endpoint / science | `q (raw HTTP query)` | TBD | TBD | pending |
| `server._analysis_endpoint` | TBD: routes → workflow → core split (§3.1) | mixed endpoint / science | TBD | TBD | TBD | pending |
| `server.Handler.__init__` | interfaces/server/ (module TBD) | HTTP interface | `ROOT`; `self request/server state` | TBD | TBD | pending |
| `server.Handler._authorized` | interfaces/server/ (module TBD) | HTTP interface | `self request/server state` | TBD | TBD | pending |
| `server.Handler._deny` | interfaces/server/ (module TBD) | HTTP interface | `self request/server state` | TBD | TBD | pending |
| `server.Handler._origin_allowed` | interfaces/server/ (module TBD) | HTTP interface | `self request/server state` | TBD | TBD | pending |
| `server.Handler._json` | interfaces/server/ (module TBD) | HTTP interface | `self request/server state` | TBD | TBD | pending |
| `server.Handler._read_json` | interfaces/server/ (module TBD) | HTTP interface | `MAX_JSON_BYTES`; `self request/server state` | TBD | TBD | pending |
| `server.Handler._start_job` | interfaces/server/ (module TBD) | HTTP interface | `JOBS`; `self request/server state` | TBD | TBD | pending |
| `server.Handler._user_ewr_post` | interfaces/server/ (module TBD) | HTTP interface | `MAX_JSON_BYTES`; `self request/server state` | TBD | TBD | pending |
| `server.Handler.do_GET` | interfaces/server/ (module TBD) | HTTP interface | `FORMATS`; `PAGE`; `self request/server state` | TBD | TBD | pending |
| `server.Handler._job_status` | interfaces/server/ (module TBD) | HTTP interface | `JOBS`; `self request/server state` | TBD | TBD | pending |
| `server.Handler.do_HEAD` | interfaces/server/ (module TBD) | HTTP interface | `self request/server state` | TBD | TBD | pending |
| `server.Handler.do_POST` | interfaces/server/ (module TBD) | HTTP interface | `USER_EWR_POST_PATHS`; `self request/server state` | TBD | TBD | pending |
| `server.Handler._forget` | interfaces/server/ (module TBD) | HTTP interface | `SERIES_SUFFIX`; `STORE`; `self request/server state` | TBD | TBD | pending |
| `server.Handler._run_upload` | interfaces/server/ (module TBD) | HTTP interface | `MAX_BYTES`; `self request/server state` | TBD | TBD | pending |
| `server.Handler._run_observed` | interfaces/server/ (module TBD) | HTTP interface | `EARLIEST_RECORD`; `JOBS`; `OBSERVED_ACTIVE`; `OBSERVED_ACTIVE_LOCK`; `self request/server state` | TBD | TBD | pending |
| `server.Handler.end_headers` | interfaces/server/ (module TBD) | HTTP interface | `self request/server state` | TBD | TBD | pending |
| `server.Handler.log_message` | interfaces/server/ (module TBD) | HTTP interface | `self request/server state` | TBD | TBD | pending |
| `server.find_free_port` | interfaces/server/ (module TBD) | server setup | `DEFAULT_PORT` | TBD | TBD | pending |
| `server.make_server` | interfaces/server/ (module TBD) | server setup | `DEFAULT_PORT` | TBD | TBD | pending |
| `server.main` | interfaces/cli.py | CLI orchestration | `DEFAULT_PORT`; `PAGE` | TBD | TBD | pending |
| `drought_windows.DROUGHTS` | `ewr_analysis.core.calculations.drought_windows.DROUGHTS` | lookup constant; later config/drought_windows.toml | none; constant-table exception authorized | drought_windows.window_dates; drought_windows.meta; server._assessment_year_stats | tests/test_server_numerics.py::TestDroughtWindows::test_window_dates_follow_the_assessment_year; tests/core/test_leaf_calculations.py::test_drought_constants_default_window_and_metadata | moved (shim in place) |
| `drought_windows.window_dates` | `ewr_analysis.core.calculations.drought_windows.window_dates` | calendar calculation | DROUGHTS (read only) | server._assessment_year_stats | tests/test_server_numerics.py::TestDroughtWindows::test_window_dates_follow_the_assessment_year; tests/core/test_leaf_calculations.py::test_drought_window_dates_from_inclusive_labels | moved (shim in place) |
| `drought_windows.meta` | `ewr_analysis.core.calculations.drought_windows.meta` | catalog projection | DROUGHTS (read only) | build_dashboard.build_meta | tests/test_dashboard_html.py::TestRowsAndMeta::test_meta_lookup_lists; tests/core/test_leaf_calculations.py::test_drought_constants_default_window_and_metadata | moved (shim in place) |
| `flow_regime_mappings.flow_mappings` | `ewr_analysis.core.calculations.flow_regimes.flow_mappings` | lookup constant; later config/*.toml (filename TBD) | none; constant-table exception authorized | flow_regime_mappings.regimes_for | tests/test_flow_regime_rules.py::TestMappingTable::test_states_are_the_expected_set; tests/core/test_leaf_calculations.py::test_flow_regime_return_is_a_copy | moved (shim in place) |
| `flow_regime_mappings.regimes_for` | `ewr_analysis.core.calculations.flow_regimes.regimes_for` | catalog lookup | flow_mappings (read only) | build_dashboard; custom_ewr; server.regimes; existing tests | tests/test_flow_regime_rules.py::TestRegimesFor::test_known_codes; tests/core/test_leaf_calculations.py::test_flow_regime_lookup_specificity_and_ties | moved (shim in place) |
