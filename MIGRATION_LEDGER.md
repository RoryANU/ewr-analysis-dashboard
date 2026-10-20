# Migration ledger

Phase 0 inventory only; no extraction has started. Destinations are relative to
`src/ewr_analysis/` and follow REORGANIZATION_PLAN.md §§2 and 3.1. Old symbols
refer to `src/server.py` at `pre-refactor`. One row covers each function,
including class methods and nested helpers (117 rows).

`TBD` means unverified or undecided; a directory is a proposed destination,
not a decision about a future symbol name. Mixed functions still require the
parse/load/compute split in §3.1. Hidden inputs list obvious module globals,
query dictionaries and request/closure state; this is not a complete audit.
Callers and per-symbol baseline coverage remain TBD. Existing snapshots stay
unchanged. All statuses are pending.

| old symbol | new symbol | category | hidden inputs | callers | baseline test | status |
|---|---|---|---|---|---|---|
| `server._safe_id` | TBD: core / data_io (§3.1) | helper | TBD | TBD | TBD | pending |
| `server._iso_date` | TBD: core / data_io (§3.1) | helper | TBD | TBD | TBD | pending |
| `server._is_multigauge` | TBD: core / data_io (§3.1) | helper | TBD | TBD | TBD | pending |
| `server._dispatch_key` | TBD: core / data_io (§3.1) | helper | TBD | TBD | TBD | pending |
| `server._mrip_days` | TBD: core / data_io (§3.1) | helper | TBD | TBD | TBD | pending |
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
| `server.nice_ceiling` | TBD | TBD | TBD | TBD | TBD | pending |
| `server._json_safe` | TBD | TBD | TBD | TBD | TBD | pending |
| `server._window_days` | core/calculations/achievement_years.py | science | TBD | TBD | TBD | pending |
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
| `server._water_year` | core/calculations/achievement_years.py | science | TBD | TBD | TBD | pending |
| `server.ewr_eval` | core/ewr_evaluation.py (I/O/cache split TBD) | evaluation / cache | `LOW_FLOW_HANDLERS`; `_EVAL_CACHE` | TBD | TBD | pending |
| `server.ewr_eval.param` | TBD (nested helper of ewr_eval) | nested helper | `enclosing function state (TBD)` | TBD | TBD | pending |
| `server.ewr_eval.optional_param` | TBD (nested helper of ewr_eval) | nested helper | `enclosing function state (TBD)` | TBD | TBD | pending |
| `server._calculate_stock_ewr` | core/ewr_evaluation.py (I/O/cache split TBD) | evaluation / cache | TBD | TBD | TBD | pending |
| `server._calculate_user_ewr` | core/ewr_evaluation.py (I/O/cache split TBD) | evaluation / cache | TBD | TBD | TBD | pending |
| `server._successful_tables` | core/ewr_evaluation.py (I/O/cache split TBD) | evaluation / cache | TBD | TBD | TBD | pending |
| `server._successful_tables.pick` | TBD (nested helper of _successful_tables) | nested helper | `enclosing function state (TBD)` | TBD | TBD | pending |
| `server.ewr_gaps` | core/calculations/event_gaps.py | science | TBD | TBD | TBD | pending |
| `server.gap_severity` | core/calculations/event_gaps.py | science | TBD | TBD | TBD | pending |
| `server.record_days` | core/calculations/achievement_years.py | science | TBD | TBD | TBD | pending |
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
| `server.mcnemar_p` | core/calculations/paired_comparison.py | science | TBD | TBD | TBD | pending |
| `server._feb29` | TBD: routes → workflow → core split (§3.1) | mixed endpoint / science | TBD | TBD | TBD | pending |
| `server._water_year_days` | TBD: routes → workflow → core split (§3.1) | mixed endpoint / science | TBD | TBD | TBD | pending |
| `server._runs_by_year` | core/calculations/spells.py | science | TBD | TBD | TBD | pending |
| `server._pair_years` | core/calculations/paired_comparison.py | science | TBD | TBD | TBD | pending |
| `server.spells` | TBD: routes → workflow → core split (§3.1) | mixed endpoint / science | `q (raw HTTP query)` | TBD | TBD | pending |
| `server.regimes` | TBD: routes → workflow → core split (§3.1) | mixed endpoint / science | `q (raw HTTP query)` | TBD | TBD | pending |
| `server.heatmap` | TBD: routes → workflow → core split (§3.1) | mixed endpoint / science | `q (raw HTTP query)` | TBD | TBD | pending |
| `server.heatmap.as_grid` | TBD (nested helper of heatmap) | nested helper | `enclosing function state (TBD)` | TBD | TBD | pending |
| `server.exceed_pct` | core/calculations/flow_duration_curves.py | science | TBD | TBD | TBD | pending |
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
