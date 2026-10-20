# Migration ledger

Phase 0 inventory, updated through Phase 1 slice 2 only. Destinations are relative to
`src/ewr_analysis/` and follow REORGANIZATION_PLAN.md §§2 and 3.1. Old symbols
refer to `src/server.py` at `pre-refactor`. One row covers each function,
including class methods and nested helpers (117 initial server rows). Slice 1
adds rows for the drought/flow-regime functions and lookup constants.

`TBD` means unverified or undecided; a directory is a proposed destination,
not a decision about a future symbol name. Mixed functions still require the
parse/load/compute split in §3.1. Hidden inputs list obvious module globals,
query dictionaries and request/closure state; this is not a complete audit.
Unverified callers and per-symbol baseline coverage remain TBD. Existing
snapshots stay unchanged. Only the listed slice 1 and slice 2 symbols are marked moved.

The user authorized `DROUGHTS` and `flow_mappings` as module-level constants
for this slice after a grep/read audit found no mutations. `DROUGHTS` is slated
for `config/drought_windows.toml`; `flow_mappings` is slated for `config/*.toml`
(filename TBD). Neither table is converted to TOML in this slice. No slice 1 symbols
were excluded after this explicit authorization. Slice 2 decisions follow below.

| old symbol | new symbol | category | hidden inputs | callers | baseline test | status |
|---|---|---|---|---|---|---|
| `server._safe_id` | TBD: core / data_io (§3.1) | helper | TBD | TBD | TBD | pending |
| `server._iso_date` | `ewr_analysis.core.calculations.assessment_years._iso_date` | helper; explicit clock exception | pandas wall clock for relative strings; no application state | server._ewr_events; core.event_gaps.ewr_gaps; core.event_gaps._runs_by_year | tests/core/test_assessment_and_gaps.py::test_iso_date_explicit_calendar_and_nanoseconds; tests/core/test_assessment_and_gaps.py::test_iso_date_missing_inputs_raise; tests/core/test_assessment_and_gaps.py::test_iso_date_relative_strings_old_and_new_accept_only | moved (shim in place) |
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
| `server._has_any_event` | `ewr_analysis.core.calculations.event_gaps._has_any_event` | science helper | none | server.summarise_rows | tests/core/test_assessment_and_gaps.py::test_dateless_gap_stats_and_has_any_event | moved (shim in place) |
| `server._successful_summary` | core/calculations/ (module TBD) | science helper | TBD | TBD | TBD | pending |
| `server._group_by_ewr` | core/calculations/ (module TBD) | science helper | TBD | TBD | TBD | pending |
| `server._dateless_gap_stats` | `ewr_analysis.core.calculations.event_gaps._dateless_gap_stats` | science | none | server.summarise_rows | tests/core/test_assessment_and_gaps.py::test_dateless_gap_stats_and_has_any_event | moved (shim in place) |
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
| `server.achievement_frequencies` | `ewr_analysis.core.calculations.assessment_years.achievement_frequencies` | science | none | server.heatmap; server._paired | tests/core/test_assessment_and_gaps.py::test_achievement_frequencies_threshold_equality_and_leap_month; tests/core/test_assessment_and_gaps.py::test_achievement_empty_series_preserves_error | moved (shim in place) |
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
| `server.ewr_gaps` | `ewr_analysis.core.calculations.event_gaps.ewr_gaps` | science | _iso_date clock exception; normal caller inputs are dates | server.interevents; server.ewr_years; server.summarise_rows | tests/core/test_assessment_and_gaps.py::test_ewr_gaps_single_event_threshold_equality_and_censoring; tests/core/test_assessment_and_gaps.py::test_ewr_gaps_no_event_and_empty_series | moved (shim in place) |
| `server.gap_severity` | `ewr_analysis.core.calculations.event_gaps.gap_severity` | science | none | server.ewr_years; core.event_gaps._dateless_gap_stats | tests/core/test_assessment_and_gaps.py::test_gap_severity_interior_preference_and_truthy_excess | moved (shim in place) |
| `server.record_days` | `ewr_analysis.core.calculations.assessment_years.record_days` | pure calculation | no application state | `server.summarise_rows` | tests/test_pyewr_wrappers.py::TestRunScenario::test_matches_the_baseline_snapshot (indirect); tests/core/test_leaf_calculations.py::test_record_days_trims_and_fills_without_mutating | moved (shim in place) |
| `server.assessment_start` | `ewr_analysis.core.calculations.assessment_years.assessment_start` | calendar calculation | none | server._assessment_year_stats; server.ewr_eval | tests/core/test_assessment_and_gaps.py::test_assessment_start_calendar_convention | moved (shim in place) |
| `server._timing_mask` | `ewr_analysis.core.calculations.assessment_years._timing_mask` | calendar calculation | none | core.assessment_years.complete_years; core.duration_ratio.vr_years | tests/core/test_assessment_and_gaps.py::test_timing_mask_first_last_day_and_leap_day; tests/core/test_assessment_and_gaps.py::test_timing_mask_wrapped_months_and_zero_day_sentinel | moved (shim in place) |
| `server.complete_years` | `ewr_analysis.core.calculations.assessment_years.complete_years` | calendar calculation | none | server._ewr_year_table; server._assessment_year_stats | tests/core/test_assessment_and_gaps.py::test_complete_years_leap_water_year_and_no_complete_record; tests/core/test_assessment_and_gaps.py::test_complete_years_day_bounds_ignore_missing_outside_window | moved (shim in place) |
| `server.dr_years` | `ewr_analysis.core.calculations.duration_ratio.dr_years` | science | none | server._ewr_year_table; server._assessment_year_stats | tests/core/test_assessment_and_gaps.py::test_duration_ratios_longest_kth_low_flow_and_approximation | moved (shim in place) |
| `server.vr_years` | `ewr_analysis.core.calculations.duration_ratio.vr_years` | science | none | server._ewr_year_table; server._assessment_year_stats | tests/core/test_assessment_and_gaps.py::test_volume_ratio_nan_break_and_timing_boundaries | moved (shim in place) |
| `server.year_summary` | `ewr_analysis.core.calculations.assessment_years.year_summary` | science | none | server._assessment_year_stats | tests/core/test_assessment_and_gaps.py::test_year_summary_thresholds_and_missing_values | moved (shim in place) |
| `server.window_metrics` | `ewr_analysis.core.calculations.assessment_years.window_metrics` | science | none | server._assessment_year_stats | tests/core/test_assessment_and_gaps.py::test_window_metrics_antecedent_gap_and_inclusive_edges; tests/core/test_assessment_and_gaps.py::test_window_metrics_full_year_and_censored_denominator | moved (shim in place) |
| `server.interevents` | TBD: routes → workflow → core split (§3.1) | mixed endpoint / science | `q (raw HTTP query)` | TBD | TBD | pending |
| `server._scenario_ids` | TBD: routes → workflow → core split (§3.1) | mixed endpoint / science | `q (raw HTTP query)` | TBD | TBD | pending |
| `server.ewr_years` | TBD: routes → workflow → core split (§3.1) | mixed endpoint / science | `q (raw HTTP query)` | TBD | TBD | pending |
| `server._ewr_year_table` | TBD: routes → workflow → core split (§3.1) | mixed endpoint / science | TBD | TBD | TBD | pending |
| `server._ewr_year_table.column` | TBD (nested helper of _ewr_year_table) | nested helper | `enclosing function state (TBD)` | TBD | TBD | pending |
| `server._ewr_events` | TBD: routes → workflow → core split (§3.1) | mixed endpoint / science | TBD | TBD | TBD | pending |
| `server._paired_achievement` | `ewr_analysis.core.calculations.paired_comparison._paired_achievement` | science | none | server.ewr_years | tests/core/test_assessment_and_gaps.py::test_paired_achievement_hand_count_and_no_common_year | moved (shim in place) |
| `server.mcnemar_p` | `ewr_analysis.core.calculations.paired_comparison.mcnemar_p` | pure calculation | no application state | `server._paired_achievement` | tests/test_server_numerics.py::TestMcNemar::test_exact_two_sided_p_by_hand; tests/core/test_leaf_calculations.py::test_mcnemar_against_exact_enumerated_binomial | moved (shim in place) |
| `server._feb29` | `ewr_analysis.core.calculations.assessment_years._feb29` | pure calculation | no application state | `core.calculations.assessment_years._water_year_days` | tests/test_server_numerics.py::TestScalarHelpers::test_feb29_belongs_to_the_water_year_that_ends_in_a_leap_year; tests/core/test_leaf_calculations.py::test_feb29_gregorian_century_rules | moved (shim in place) |
| `server._water_year_days` | `ewr_analysis.core.calculations.assessment_years._water_year_days` | pure calculation | no application state | `server._runs_by_year`; `server._hydrograph` | tests/test_gauge_analysis.py::SpellLeapYearTest::test_leap_water_year_is_complete_with_366_days (indirect); tests/core/test_leaf_calculations.py::test_water_year_days_from_calendar_length | moved (shim in place) |
| `server._runs_by_year` | `ewr_analysis.core.calculations.event_gaps._runs_by_year` | science | _iso_date clock exception; normal caller inputs are dates | server.spells; server.regimes | tests/core/test_assessment_and_gaps.py::test_runs_nan_break_single_event_and_empty_series; tests/core/test_assessment_and_gaps.py::test_runs_complete_leap_year_event_crosses_year_boundary | moved (shim in place) |
| `server._pair_years` | core/calculations/paired_comparison.py (split required) | payload mutation / comparison | mutates caller-owned out["paired"] | server.spells; server.regimes | tests/test_server_numerics.py::TestSpellsPayload::test_year_lists_are_aligned_and_sorted | left in server: separate common-year computation from payload mutation first; preserve statement order |
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
| `build_dashboard.dr_approx` | `ewr_analysis.core.calculations.duration_ratio.dr_approx` | science approximation | none | build_dashboard.build_row_table | tests/test_dashboard_html.py::TestBaseline::test_aggregates_match_the_stored_baseline (indirect); tests/core/test_assessment_and_gaps.py::test_duration_ratios_longest_kth_low_flow_and_approximation | moved (shim in place) |

## Slice 2 audit and scope decisions

Read each candidate body and its helper dependencies before extraction. Moved
unchanged: `assessment_start`, `_timing_mask`, `complete_years`, `year_summary`,
`window_metrics`, `achievement_frequencies`, `ewr_gaps`, `gap_severity`,
`_dateless_gap_stats`, `_runs_by_year`, `_has_any_event`, `_paired_achievement`,
`dr_years`, `vr_years`, and `build_dashboard.dr_approx`. Added `_iso_date` to this
slice by explicit user authorization. Each old location re-exports the same name,
including private helpers. No moved body accesses q, ROOT, STORE, file/network
I/O, locks or caches. None uses additional module-level tables/constants, so no
new table move or duplication was necessary. Earlier DROUGHTS/flow_mappings
config/*.toml deferral remains in force.

**Left behind:** `_pair_years` mutates the caller's `out["paired"]`. It is small
but is not a pure function; separating that assignment would change its body
and statement structure. A later slice must compute common complete years in
core and assign the response payload at the interface. No partial extraction
or logic rewrite was attempted. This is the only excluded candidate. Endpoint
functions (`spells`, `regimes`, `ewr_years`, `interevents`, `heatmap`, `_paired`)
and evaluation/loading/cache orchestration remain outside this slice: parse q
at the interface, load series in data_io, and then call the calculations.

**Authorized clock exception:** `git grep -n "_iso_date"` found exactly one
source definition before the move, in server.py. All six formatting call sites
were read: two in `ewr_gaps`, three in `_ewr_events`, one in `_runs_by_year`.
The first pair uses flow-index endpoints or `pd.Timestamp(inter.*Date)`;
`_ewr_events` converts event endpoints with `pd.Timestamp` before all three
calls; `_runs_by_year` formats index entries, and current spells/regimes callers
supply the DatetimeIndex from `daily_series`'s `pd.date_range`. No site passes a
raw HTTP query string straight to `_iso_date`. User-supplied scenario dates can
reach upstream pandas parsing and in-memory callers can supply relative strings
in event tables; those are parsed before formatting. Thus absence of a direct
query-string call is not an input-validation guarantee. **Risk:** pandas accepts
`"now"` / `"today"` and reads wall time, either upstream or in a direct helper
call. The user explicitly authorized moving the helper unchanged despite this
exception to strict purity. Input validation belongs at the interface in a
later phase; no guard was added here. Numeric nanosecond timestamps are accepted;
None/NaT/blank raise ValueError. Explicit dates retain their local timezone's
date. Deterministic old/new cases compare exactly; relative-date cases check
acceptance only and never compare the returned values.

**Coverage:** all 16 slice 2 functions have independent known-answer tests in
`tests/core/test_assessment_and_gaps.py` (35 cases, derivations in comments) and
pre-refactor comparisons. Existing TestAchievementFrequencies, TestDroughtWindows,
TestDurationRatio, TestEwrGaps, TestRunsByYear, py_ewr wrapper and golden tests
continue through the shims. The recursive src/ewr_analysis/**/*.py staleness scan
already includes duration_ratio.py and the extended modules; no change needed.

**Scientist review:** see the slice 2 subsection under "Observed issues" in
`docs/refactor_baseline.md`. All 16 new template docstrings mark scientific
provenance/suitability as "TBD, needs scientist review"; their rules transcribe
the existing code, not new scientific references. `_pair_years`'s deferred split
and `_iso_date`'s wall-clock/input-validation exception also need review. Slice 3
has not started.
