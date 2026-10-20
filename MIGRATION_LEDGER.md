# Migration ledger

Phase 0 inventory, updated through Phase 1 slice 3 only. Destinations are relative to
`src/ewr_analysis/` and follow REORGANIZATION_PLAN.md §§2 and 3.1. Old symbols
refer to `src/server.py` at `pre-refactor`. One row covers each function,
including class methods and nested helpers (117 initial server rows). Slice 1
adds rows for the drought/flow-regime functions and lookup constants.

`TBD` means unverified or undecided; a directory is a proposed destination,
not a decision about a future symbol name. Mixed functions still require the
parse/load/compute split in §3.1. Hidden inputs list obvious module globals,
query dictionaries and request/closure state; this is not a complete audit.
Unverified callers and per-symbol baseline coverage remain TBD. Existing
snapshots stay unchanged. Only the listed slice 1–3 symbols are marked moved.

The user authorized `DROUGHTS` and `flow_mappings` as module-level constants
for this slice after a grep/read audit found no mutations. `DROUGHTS` is slated
for `config/drought_windows.toml`; `flow_mappings` is slated for `config/*.toml`
(filename TBD). Neither table is converted to TOML in this slice. No slice 1 symbols
were excluded after this explicit authorization. Slice 2 decisions follow below.

| old symbol | new symbol | category | hidden inputs | callers | baseline test | status |
|---|---|---|---|---|---|---|
| `server._safe_id` | TBD: core / data_io (§3.1) | helper | TBD | TBD | TBD | pending |
| `server._iso_date` | `ewr_analysis.core.calculations.assessment_years._iso_date` | helper; explicit clock exception | pandas wall clock for relative strings; no application state | server._ewr_events; core.event_gaps.ewr_gaps; core.event_gaps._runs_by_year | tests/core/test_assessment_and_gaps.py::test_iso_date_explicit_calendar_and_nanoseconds; tests/core/test_assessment_and_gaps.py::test_iso_date_missing_inputs_raise; tests/core/test_assessment_and_gaps.py::test_iso_date_relative_strings_old_and_new_accept_only | moved (shim in place) |
| `server._is_multigauge` | `ewr_analysis.core.calculations.ewr_parameters._is_multigauge` | parameter helper; added to slice | none | frequency_ratio._assessment_year_stats; server._builtin_ewr_list; server._builtin_diagnostic_inventory; server.ewr_eval | tests/core/test_frequency_ratio.py::test_is_multigauge_string_membership_only | moved (shim in place) |
| `server._dispatch_key` | `ewr_analysis.core.calculations.ewr_parameters._dispatch_key` | parameter helper; added to slice | none; reads no constants | frequency_ratio._assessment_year_stats; server._builtin_ewr_list; server._builtin_diagnostic_inventory; server.ewr_eval | tests/core/test_frequency_ratio.py::test_dispatch_key_unvalidated_kind | moved (shim in place) |
| `server._mrip_days` | `ewr_analysis.core.calculations.event_gaps._mrip_days` | pure calculation | no application state | `server.summarise_rows`; `server.ewr_eval` | tests/test_pyewr_wrappers.py::TestRunScenario::test_fr_and_etp_agree_with_an_independent_recomputation_from_py_ewr (indirect); tests/core/test_leaf_calculations.py::test_mrip_days_365_day_year_and_even_rounding | moved (shim in place) |
| `server._authority` | interfaces/server/security.py | HTTP security | TBD | TBD | TBD | pending |
| `server._loopback_host` | interfaces/server/security.py | HTTP security | TBD | TBD | TBD | pending |
| `server._state_change_allowed` | interfaces/server/security.py | HTTP security | TBD | TBD | TBD | pending |
| `server.load_frames` | data_io/scenario_files.py | file I/O | TBD | TBD | TBD | pending |
| `server._salinity_frame` | data_io/scenario_files.py | file I/O | TBD | TBD | TBD | pending |
| `server._pool_init` | core/ewr_evaluation.py (pool orchestration split TBD) | evaluation / orchestration | `_POOL` | TBD | TBD | pending |
| `server._eval_gauge` | core/ewr_evaluation.py (pool orchestration split TBD) | evaluation / orchestration | `_POOL` | TBD | TBD | pending |
| `server.evaluate_gauges` | core/ewr_evaluation.py (pool orchestration split TBD) | evaluation / orchestration | TBD | TBD | TBD | pending |
| `server.summarise_rows` | workflows/run_analysis.py; pure arithmetic later core/calculations/frequency_ratio.py | evaluation/orchestration mixed with science | cached diagnostic_handlers; file-based ScenarioHandler joins; report callback | server.run_scenario; tests | tests/test_pyewr_wrappers.py::TestRunScenario::test_matches_the_baseline_snapshot | left: load/evaluate first and pass in-memory summaries/handlers, separate reporting; see mutation audit below |
| `server._has_any_event` | `ewr_analysis.core.calculations.event_gaps._has_any_event` | science helper | none | server.summarise_rows | tests/core/test_assessment_and_gaps.py::test_dateless_gap_stats_and_has_any_event | moved (shim in place) |
| `server._successful_summary` | workflows/run_analysis.py / py_ewr adapter (split TBD) | side-effecting callback adapter | arbitrary fetch callback; py_ewr summaries can mutate cached tables | server.summarise_rows | tests/test_pyewr_wrappers.py::TestRunScenario::test_empty_successful_event_summary_is_tolerated | left: 2340d29 server.py:452 calls fetch(); resolve side effects in orchestration before pure summary grouping |
| `server._group_by_ewr` | `ewr_analysis.core.calculations.frequency_ratio._group_by_ewr` | summary grouping | none | server.summarise_rows | tests/core/test_frequency_ratio.py::test_group_by_ewr_real_summary_columns | moved (shim in place) |
| `server._dateless_gap_stats` | `ewr_analysis.core.calculations.event_gaps._dateless_gap_stats` | science | none | server.summarise_rows | tests/core/test_assessment_and_gaps.py::test_dateless_gap_stats_and_has_any_event | moved (shim in place) |
| `server._assessment_year_stats` | `ewr_analysis.core.calculations.frequency_ratio._assessment_year_stats` | annual/window aggregation | read-only LOW_FLOW_HANDLERS and DROUGHTS; earlier window date/clock contracts retained | server.summarise_rows | tests/core/test_frequency_ratio.py::test_assessment_year_stats_fr_etp_independent_arithmetic | moved (shim in place) |
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
| `server._json_safe` | `ewr_analysis.core.calculations.frequency_ratio._json_safe` | payload value normalization | none | server.Handler._json | tests/core/test_frequency_ratio.py::test_json_safe_tuple_and_numpy_scalar_oddity | moved (shim in place) |
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
| `server.record_coverage` | `ewr_analysis.core.calculations.assessment_years.record_coverage` | coverage calculation | none | server.ewr_years | tests/core/test_frequency_ratio.py::test_record_coverage_absent_day_nan_and_empty | moved (shim in place) |
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

## Slice 3 inventory

| old symbol | new symbol | category | hidden inputs | callers | baseline test | status |
|---|---|---|---|---|---|---|
| `build_dashboard.detect_variant_conflicts` | `ewr_analysis.core.calculations.frequency_ratio.detect_variant_conflicts` | pure in-memory helper | read-only catalogues where used; no I/O | build_dashboard; tests/test_build_pipeline.py; tests/test_dashboard_html.py | tests/core/test_frequency_ratio.py::test_variant_conflict_and_empty_frame | moved (shim in place) |
| `build_dashboard.scenario_index` | `ewr_analysis.core.calculations.frequency_ratio.scenario_index` | pure in-memory helper | read-only catalogues where used; no I/O | build_dashboard; tests/test_build_pipeline.py; tests/test_dashboard_html.py | tests/core/test_frequency_ratio.py::test_scenario_index_policy_major | moved (shim in place) |
| `build_dashboard.fix_theme` | `ewr_analysis.core.calculations.frequency_ratio.fix_theme` | pure in-memory helper | read-only catalogues where used; no I/O | build_dashboard; tests/test_build_pipeline.py; tests/test_dashboard_html.py | tests/core/test_frequency_ratio.py::test_objective_themes_priority_tie_and_reference | moved (shim in place) |
| `build_dashboard.objective_themes` | `ewr_analysis.core.calculations.frequency_ratio.objective_themes` | pure in-memory helper | read-only catalogues where used; no I/O | build_dashboard; tests/test_build_pipeline.py; tests/test_dashboard_html.py | tests/core/test_frequency_ratio.py::test_objective_themes_priority_tie_and_reference | moved (shim in place) |
| `build_dashboard.objectives_of` | `ewr_analysis.core.calculations.frequency_ratio.objectives_of` | pure in-memory helper | read-only catalogues where used; no I/O | build_dashboard; tests/test_build_pipeline.py; tests/test_dashboard_html.py | tests/core/test_frequency_ratio.py::test_objective_themes_priority_tie_and_reference | moved (shim in place) |
| `build_dashboard.build_row_table` | `ewr_analysis.core.calculations.frequency_ratio.build_row_table` | pure in-memory helper | read-only catalogues where used; no I/O | build_dashboard; tests/test_build_pipeline.py; tests/test_dashboard_html.py | tests/core/test_frequency_ratio.py::test_all_nan_raw_group_and_row_table_alias_contract; tests/core/test_frequency_ratio.py::test_mini_payload_before_and_after_moves | moved (shim in place) |
| `build_dashboard.ewr_params` | `ewr_analysis.core.calculations.ewr_parameters.ewr_params` | pure in-memory helper | read-only catalogues where used; no I/O | build_dashboard; tests/test_build_pipeline.py; tests/test_dashboard_html.py | tests/core/test_frequency_ratio.py::test_ewr_params_first_row_coercion_and_empty | moved (shim in place) |
| `build_dashboard.normalise_results` | data_io/result tables normalization | argument mutation; returns same frame | see reason | build_dashboard; existing tests | tests/test_build_pipeline.py; tests/test_ewr_index.py | left: 2340d29:87–89 assign d["Gauge"], d["Target"], d["scn"]; copy would break documented in-place/identity contract |
| `build_dashboard._fill_by_scenario` | core/calculations/frequency_ratio.py after caller contract split | argument mutation; returns None | see reason | build_dashboard; existing tests | tests/test_build_pipeline.py; tests/test_ewr_index.py | left: 2340d29:147 writes values[int(scenario)]; aggregate_ewr_rows relies on these writes, so a defensive copy loses the output |
| `build_dashboard.aggregate_ewr_rows` | core/calculations/frequency_ratio.py after helper split | local aggregation with mutating helper dependency | see reason | build_dashboard; existing tests | tests/test_build_pipeline.py; tests/test_ewr_index.py | left: 2340d29:124–126,134 call _fill_by_scenario on fresh local lists; the wrapper does not mutate its input frames, but importing its excluded helper into core or rewriting calls would violate this slice’s unchanged/import-only gate |
| `build_dashboard.ewr_thresholds` | data_io parameter loading wrapper + core/calculations/ewr_parameters.py computation later | implicit I/O default | see reason | build_dashboard; existing tests | tests/test_build_pipeline.py; tests/test_ewr_index.py | left: 2340d29:314 calls parameter_sheet() for None; move pure threshold block after an explicit sheet argument boundary in a later extraction |
| `build_dashboard.ewr_handlers` | data_io config loader + core/calculations/ewr_parameters.py dispatch computation later | configuration read and printing | see reason | build_dashboard; existing tests | tests/test_build_pipeline.py; tests/test_ewr_index.py | left: 2340d29:358 get_ewr_calc_config(None), :360 print on failure; later pass resolved cfg to the pure key lookup; current full AST/default behavior cannot move unchanged |
| `build_dashboard.POLICIES` | `ewr_analysis.core.calculations.frequency_ratio.POLICIES` | read-only catalogue; later config/*.toml | none; NS is derived from the two lists | build_dashboard; core helpers; tests | tests/core/test_frequency_ratio.py::test_scenario_index_policy_major; exact pre-refactor comparison | moved unchanged, shim in place; TOML deferred |
| `build_dashboard.CLIMATES` | `ewr_analysis.core.calculations.frequency_ratio.CLIMATES` | read-only catalogue; later config/*.toml | none; NS is derived from the two lists | build_dashboard; core helpers; tests | tests/core/test_frequency_ratio.py::test_scenario_index_policy_major; exact pre-refactor comparison | moved unchanged, shim in place; TOML deferred |
| `build_dashboard.NS` | `ewr_analysis.core.calculations.frequency_ratio.NS` | read-only catalogue; later config/*.toml | none; NS is derived from the two lists | build_dashboard; core helpers; tests | tests/core/test_frequency_ratio.py::test_scenario_index_policy_major; exact pre-refactor comparison | moved unchanged, shim in place; TOML deferred |
| `server.LOW_FLOW_HANDLERS` | `ewr_analysis.core.calculations.ewr_parameters.LOW_FLOW_HANDLERS` | read-only dispatch tuple; later config/*.toml | none | core._assessment_year_stats; server.ewr_eval | tests/core/test_frequency_ratio.py::test_dispatch_key_unvalidated_kind; exact pre-refactor comparison | moved unchanged, shim in place; TOML deferred |
| `build_dashboard.load_results` | data_io/results_tables.py | I/O / orchestration / presentation | CSV reads; ETP clipping and in-place normalization stay at this boundary | build_dashboard; existing tests | existing golden/pipeline tests | explicitly left outside slice |
| `build_dashboard.load_gauge_stations` | data_io/results_tables.py | I/O / orchestration / presentation | CSV station reads and cleaning | build_dashboard; existing tests | existing golden/pipeline tests | explicitly left outside slice |
| `build_dashboard.load_constraints` | data_io/results_tables.py | I/O / orchestration / presentation | CSV constraint reads | build_dashboard; existing tests | existing golden/pipeline tests | explicitly left outside slice |
| `build_dashboard.build_gauges` | interfaces/visualization map preparation (exact module TBD) | I/O / orchestration / presentation | explicitly excluded; map/proxy positions, not FR/ETP aggregation | build_dashboard; existing tests | existing golden/pipeline tests | explicitly left outside slice |
| `build_dashboard.build_polygons` | visualization/map preparation (exact module TBD) | I/O / orchestration / presentation | explicitly excluded; shapely simplification and caller GeoJSON mutation | build_dashboard; existing tests | existing golden/pipeline tests | explicitly left outside slice |
| `build_dashboard.parameter_sheet` | data_io/parameter_sheet.py | I/O / orchestration / presentation | py_ewr file-backed sheet read | build_dashboard; existing tests | existing golden/pipeline tests | explicitly left outside slice |
| `build_dashboard.render_html` | interfaces/dashboard rendering | I/O / orchestration / presentation | HTML rendering explicitly excluded | build_dashboard; existing tests | existing golden/pipeline tests | explicitly left outside slice |
| `build_dashboard.build_payload` | workflows/build_dashboard.py | I/O / orchestration / presentation | CSV/GeoJSON reads, Path-derived data, reporting and composition | build_dashboard; existing tests | existing golden/pipeline tests | explicitly left outside slice |
| `build_dashboard.is_stale` | workflows/build_dashboard.py | I/O / orchestration / presentation | filesystem mtime checks; existing recursive scan covers moves | build_dashboard; existing tests | existing golden/pipeline tests | explicitly left outside slice |
| `build_dashboard.ensure_built` | workflows/build_dashboard.py | I/O / orchestration / presentation | filesystem/build orchestration | build_dashboard; existing tests | existing golden/pipeline tests | explicitly left outside slice |
| `build_dashboard.main` | interfaces/cli.py + workflow | I/O / orchestration / presentation | entry-point reads, printing and generated HTML write | build_dashboard; existing tests | existing golden/pipeline tests | explicitly left outside slice |
| `build_dashboard.ewr_index` | data_io/parameter_sheet.py + interfaces index encoding | I/O / orchestration / presentation | file-backed CSV and config/handler read; keeps EWR_COLS/EWR_STR | build_dashboard; existing tests | existing golden/pipeline tests | explicitly left outside slice |
| `build_dashboard.report_variant_conflicts` | workflows/build_dashboard.py | I/O / orchestration / presentation | prints report; pure detector moved | build_dashboard; existing tests | existing golden/pipeline tests | explicitly left outside slice |

## Slice 3 purity, mutation and scope audit

Read all 18 original candidates; 11 pass the whole-function/dependency check.
The user additionally authorized `_is_multigauge` and `_dispatch_key`, giving
13 moved functions. No computation was rewritten and no defensive copy added.
No source or equation is guessed. Four constants move exactly once to core and
are imported back; no duplicate value definitions are introduced.

`git grep -nE "_is_multigauge|_dispatch_key"` before moving showed one definition
each, both pure and dependent only on their arguments. Every caller was read:
`_assessment_year_stats` (moves), `_builtin_ewr_list`,
`_builtin_diagnostic_inventory`, `ewr_eval` (the latter three stay in server and
use explicit same-name re-exports). `_dispatch_key` itself reads no constant.
`_assessment_year_stats` reads LOW_FLOW_HANDLERS; its only other caller is
server.ewr_eval. Catalogue grep found no writes to POLICIES, CLIMATES, NS or
LOW_FLOW_HANDLERS. POLICIES/CLIMATES/NS move to frequency_ratio; LOW_FLOW_HANDLERS
to ewr_parameters. All are deferred to config/*.toml. KEY, REKEY, PROXY_LOC,
PROXY_NAME, EWR_COLS and EWR_STR stay with their excluded readers; their values
are unchanged. _assessment_year_stats lives in frequency_ratio alongside its
aggregation outputs; placing it in assessment_years would create an import cycle
with duration_ratio's existing timing-mask dependency. No py_ewr or shapely import
is required in core; the existing import-linter contract is unchanged.

Mutation locations below refer to accepted source at **2340d29**, so line numbers
remain stable after extraction:

- normalise_results, build_dashboard.py:87,88,89: `d["Gauge"] = ...`,
  `d["Target"] = ...`, `d["scn"] = ...`. It promises and returns the caller's
  same frame. Copying changes this public in-place/identity contract.
- _fill_by_scenario, build_dashboard.py:147:
  `values[int(scenario)] = round(float(mean), places)`. It returns None and its
  caller consumes the mutated list, so defensive copying cannot preserve output.
- aggregate_ewr_rows only mutates its newly allocated rows/lists, not either
  input frame. It stays because it calls the excluded mutator at :124–126,134.
  A later change must replace the fill contract explicitly, then move aggregation.
- summarise_rows, server.py:377 assigns ScenarioHandler attributes to the supplied
  result/event/frame objects. That adapter's file-backed summary calls and cached
  handler discovery (:374) prevent a purity guarantee. It also mutates newly
  copied/local tables at :381 (FR) and :425 (ETP), and its local gap_stats/daily
  cache; these local writes are not argument mutations. report(:403) is an
  arbitrary callback. Separate py_ewr load/evaluation/reporting before extracting
  arithmetic; do not add a copy and silently change adapter/caller aliases.
- Explicitly excluded build_polygons, build_dashboard.py:273–274, assigns
  `f["properties"]` and `f["geometry"]` inside caller-owned GeoJSON and uses
  shapely; leave at the map/presentation boundary.
- build_row_table does not edit its input, but deliberately returns references to
  its FR/ETP/achieved lists. The alias contract is tested and preserved. Other
  moved helpers only allocate/mutate local containers. Old/new compare calls
  assert all inputs unchanged, including DataFrame dtypes, index and row order.

No partial extraction is attempted for ewr_thresholds/ewr_handlers: their pure
arithmetic can be exposed later with explicit resolved-sheet/config arguments,
but doing so now would introduce a new wrapper signature/call and fail the
requested full-function AST and import-only shim audit. `_successful_summary`
cannot guarantee purity of its fetch callback. The endpoint functions already
listed in the ledger (`ewr_years`, `interevents`, `spells`, `regimes`, `heatmap`,
`curves`, `_paired` and other q consumers) remain at the future interface ->
workflow -> data_io/core boundary. No query dictionary enters core.

The independent tests cite README's opening FR/ETP/differential definitions and
the server module docstring. Raw Python FR remains achieved/target; capping each
EWR for scenario means, “met in both”, and change bands are existing JavaScript
rules. New tests invoke that unchanged source through Node in a VM, without
moving JS science or deciding O3. The mini payload test substitutes original
pre-refactor build helpers, builds the fixture payload, restores the core shims,
then compares payload dictionaries, complete rendered HTML and strict JSON.
No fixture, snapshot, data, or generated artifact is committed.

Scientist review: see slice 3 preserved observations under "Observed issues" in
docs/refactor_baseline.md. All 13 new template docstrings mark source/suitability
where uncertain as "TBD, needs scientist review". Seven candidates remain for the
reasons above; read-only catalogues remain scheduled for TOML later. Slice 4 has
not started.
