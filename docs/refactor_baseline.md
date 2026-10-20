# Phase 0 refactor baseline

Recorded on 20 October 2026 (Australia/Sydney), before changing application source.

- Starting commit: `af9123e` (`pre-refactor`).
- Branch: `refactor/science-boundaries`.
- Python: 3.13.0 (existing `.venv`).
- `py_ewr`: 2.4.11.
- Optional `mdba-gauge-getter`: 0.5.1, installed.
- Node: v24.19.0.
- OS: `macOS-27.0.1-arm64-arm-64bit-Mach-O`.
- Pre-commit: 4.7.0.

## Commands and baseline results

Commands run from the repository root, using the existing environment:

```sh
git switch -c refactor/science-boundaries
git tag pre-refactor
source .venv/bin/activate
python --version
python -c "import importlib.metadata, platform; print('py_ewr:', importlib.metadata.version('py-ewr')); print('OS:', platform.platform()); print('mdba-gauge-getter:', importlib.metadata.version('mdba-gauge-getter'))"
node --version
pre-commit --version
pytest -q > /tmp/ewr-phase0-pytest-baseline.log 2>&1
node --test tests/test_performance.js tests/test_dashboard_core.js tests/test_ewr_guide.js tests/test_custom_ewr.js tests/test_heatmap_selection.js > /tmp/ewr-phase0-node-baseline.log 2>&1
```

The initial sandboxed Python run returned **757 passed, 6 failed, 30 errors,
1 skipped** in 136.14 seconds. Every failure/error was a socket-bind
`PermissionError: [Errno 1] Operation not permitted`. The network test
`tests/test_parameter_sheet_sync.py::test_matches_upstream` skipped because
GitHub DNS was unavailable. No optional-observed tests skipped.

The JavaScript baseline returned **205 passed, 0 failed, 0 skipped**
(205 tests; 0 cancelled; 0 todo).

The socket restriction was reported before proceeding. The unchanged full
Python suite was retried with permission to bind local sockets and use the
network:

```sh
source .venv/bin/activate
pytest -q > /tmp/ewr-phase0-pytest-baseline-unrestricted.log 2>&1
```

Retry result: **794 passed in 138.36s (0:02:18)**. This unrestricted run is the
authoritative Python baseline. No network or optional-observed tests skipped.

## Observed issues

- Baseline HTTP tests require permission to bind loopback sockets; the initial
  sandbox did not allow this. The network test skips when GitHub is unreachable.
- The README, launchers and launcher tests accept Python 3.10, while existing
  project metadata and tooling target Python 3.11. All existing `src/*.py` files
  parse with Python 3.10 grammar, but `custom_ewr.py` imports `datetime.UTC`,
  which requires Python 3.11. The final metadata and tooling retain Python 3.11.
  The launcher's existing 3.10 check is left unchanged as required by its tests;
  this existing launcher/documentation discrepancy is not fixed in Phase 0.
- The README references an untracked/missing `notebooks/run_your_scenarios.ipynb`.
- No pre-commit hook is installed in `.git/hooks/pre-commit` at the starting
  commit. The configured pre-commit hook was installed during verification,
  and the all-files checks are also run explicitly.
- Mypy on the untouched `pre-refactor` checkout reported **59 errors in 5 files**.
  The moved package initially reported the same 59 errors, with no new errors
  after retaining the existing Python 3.11 target. The user explicitly authorized
  a separate annotation-only cleanup in this chat. Commit `c2139c7` adds type
  annotations and identity-preserving `typing.cast` calls, including casts for
  deliberately invalid/immutable test inputs. It does not change expectations,
  error handling, thresholds, operation order or returned runtime values.

### Phase 1 slice 2 — preserved observations

All observations below describe existing behaviour, not corrections. The move
retains statement order, values, label conventions, sentinels and rounding.

- `_iso_date` accepts numeric nanosecond timestamps and local timezone-aware
  dates. None/NaT/blank raise ValueError rather than return None. **Clock
  dependency:** pandas relative strings `"now"` / `"today"` are accepted and
  depend on wall time. The user explicitly authorized moving this helper
  unchanged. Every call site was checked: event endpoints are converted to
  Timestamp first, or come from daily-series index entries; no direct raw q
  value is formatted. User scenario dates still reach upstream pandas parsing,
  so relative-input validation belongs at the interface in a later phase.
- `assessment_start` selects the timing start when sm <= 6 and em >= 7,
  otherwise July. Other starting-year helpers retain their starting label,
  while `achievement_frequencies` uses an ending-year label. No convention
  was unified.
- `_timing_mask` treats day 0 as unspecified (1/31); equal-month reversed day
  bounds select nothing, rather than wrapping within the month. Month/day
  bounds are not validated; local timezone fields are used without conversion.
- `complete_years` constructs a timezone-naive daily calendar, so a timezone-aware
  series can align as entirely missing. Missing dates are inserted as NaN;
  missingness outside the timing window is ignored. No selected dates gives
  0 rather than vacuous completeness; an empty requested-year list gives [].
- `year_summary` ignores None but keeps NaN in its median (3 decimals); NaN
  compares false to the failure bounds. Near misses include equality to .5
  and exclude equality to 1. No retained ratio yields dr/nm None; mismatched
  list lengths raise through strict zip.
- `window_metrics` counts only whole assessment years inside inclusive bounds.
  FR/ET round to 4 decimals, severity to 2, DR median to 3. None DR is excluded,
  NaN retained. Antecedent gaps contribute from start+MRIP, and severity uses
  the entire overlapping gap even when record bounds clip the ET denominator.
  Empty gaps leave ET/severity None even with explicit record bounds. Unlike
  `ewr_gaps`, ET is not capped; overlapping gaps can double-count days. No
  span merge or clipping correction was introduced.
- `achievement_frequencies` counts equality to the threshold, pairs all scenarios
  over their intersection of complete years, returns float32, and uses NaN
  when there are no shared complete years. Empty index raises ValueError;
  zero scenarios also raises. It counts rows and assumes sorted unique daily
  dates. Consecutive rolling width only grows: unsorted durations can yield
  unexpected results, and zero/negative durations are not validated. No row
  continuity or duration validation was added.
- `ewr_gaps` needs a nonempty flow index (empty raises IndexError). With no
  success it uses one full-record gap. A NaN censors the entire inclusive gap,
  but absent dates are not inserted. Counted days subtract full censored spans
  from row count, assuming regular daily nonoverlapping inputs. ETP is capped
  at 1, rounds to 4 decimals, and is None with no MRIP/countable denominator.
- `gap_severity` prefers the longest interior gap, falling back to all known
  gaps; boundary-inclusive severity is separate. Both round to 2 decimals.
  Episode count uses supplied truthy excess, without recomputing days > MRIP.
  No maximum/no uncensored gap returns three None values.
- `_dateless_gap_stats` determines success by key membership, even if that
  summary value is an empty DataFrame. It coerces gap lengths and drops NaN.
  With success it may return positive ETP but no severity/episode metrics:
  only the no-success case constructs gap objects for severity.
- `_has_any_event` checks yearly list length only: `[[]]` counts as containing
  an event although the event itself is empty. No contents are validated.
- `_runs_by_year` clears NaN hits and censors runs beside NaN or record edges.
  Adjacent rows are treated as consecutive days; annual completeness counts
  365/366 nonmissing rows, not unique dates. A boundary-crossing run is counted
  in its starting year; days/longest are split between years. Incomplete annual
  metrics are None; empty input returns empty lists.
- `dr_years` caps at 2 and rounds to 3 decimals. Nonpositive/missing duration
  gives None; missing lengths give None, while insufficient k events gives 0.
  Low-flow total days ignore the k-event rule. Fractional numeric per_year is
  truncated; the numeric string `"2.0"` passes numeric coercion then raises at
  int conversion. Negative event lengths are not floored at zero.
- `vr_years` caps at 2 and rounds to 3 decimals. NaN/out-of-window rows break
  full rolling windows and assessment years separate accumulation. Fractional
  acc >= 1 is truncated; missing calendar days are not inserted, so row count
  is assumed to mean days. No timezone conversion occurs.
- `dr_approx` caps at 2 and rounds to 3 decimals; only None event lengths are
  excluded. It accepts truthy negative durations, whereas dr_years rejects
  them; truthy NaN can propagate, and a numeric string can raise on division.
  Nonflow kind or falsy duration yields all None. No input policy was unified.
- `_paired_achievement` keeps the last complete entry for duplicate year labels.
  Gain/loss uses truthiness but totals sum actual values, assuming binary
  achievement without normalizing it. Percentages round to 3 decimals; no
  common year yields zero counts and None percentages/p-value.
- `_pair_years` mutates its input payload by assigning out["paired"]. It stays
  in server; a later slice must separate common-year computation from payload
  assignment. No partial extraction was attempted.

### Phase 1 slice 3 — preserved observations

- The read-only POLICIES/CLIMATES catalogue order defines policy-major scenario
  positions (4 x 7 = 28). Lookup is case-sensitive and unknown policy/climate
  raises ValueError. NS stays the same derived expression. No catalogue value
  or ordering changed; TOML remains later work.
- `_is_multigauge` accepts only a nonblank string, stripping whitespace solely
  for that test; numeric IDs, None and NaN are false. `_dispatch_key` applies
  string formatting without validating kind: None becomes "None", NaN "nan",
  and unexpected kinds pass through. Both have exactly one definition in core
  and the same-name server re-exports; four original callers were audited.
- `detect_variant_conflicts` reports sorted distinct non-null metric values,
  preserving group encounter order and including null grouping keys. Mixed
  incomparable metric types can fail sorting; no type policy was added.
- `fix_theme` changes exactly "Other species". Other spelling/case variants,
  None and NaN are not normalized. `objective_themes` prefers FR over ETP,
  picks the first (lexically sorted) mode on a tie, and only then uses string
  reference metadata or Unclassified. An upstream Unclassified fill can make
  reference fallback unreachable. `objectives_of` sorts string prefixes then
  full codes, so A-10 precedes A-2 rather than numerical suffix order.
- `build_row_table` keeps 13 fixed columns, several None placeholders, fixed
  themes followed by sorted additions, and raw metric lists. Returned FR/ETP/
  achieved lists alias the input row lists; no defensive copy was introduced.
  Parameter kind is hidden from info, ny is taken from the row, and arbitrary
  other parameter keys still pass through. Empty rows retain seven themes.
- `ewr_params` uses the first duplicate gauge/code/planning-unit key, even if
  later rows have valid values where the first is missing. Keys are stringified,
  missing numeric columns become None, duration truncates via int, and MRIP
  rounds to 4 decimals. Missing kind column defaults to F; present missing kind
  is stringified. No range checks or numeric infinity guard were added.
- `_group_by_ewr` uses pandas' default sorted/drop-null grouping, then stringifies
  gauge keys. This can collapse distinct source groups such as numeric and
  string representations of the same gauge; no key normalization was changed.
  Empty/missing-Gauge tables return {}, while other missing required columns
  can still raise.
- `_json_safe` recognizes Python floats, recursively converts dict values and
  list/tuple elements, and turns tuples into lists. Non-float NumPy scalars
  (e.g. float32 NaN), dictionary keys, sets and other objects remain unchanged;
  it is not a general JSON-type validator.
- `_assessment_year_stats` retains numeric-to-int conversions and missing
  achievement -> zero, conditional user day bounds, dispatch-based low-flow
  choice, and all inherited ratio/window rounding. It assumes nonempty ordered
  daily data (first/last indexing otherwise raises), and uses starting-year
  labels plus the existing drought catalogue. No completeness/ratio policy changed.
- `record_coverage` counts both NaN and absent dates between min/max, but trusts
  the supplied complete flags independently of that count. Empty flow still
  reports their sum/length. Duplicate or non-date indexes are not repaired;
  date formatting stays in the existing timezone.
- `normalise_results` is intentionally in-place and returns its argument;
  `_fill_by_scenario` mutates a list and returns None. Both stay in the builder.
  `aggregate_ewr_rows` itself edits only newly allocated structures but depends
  on that excluded mutator, so it also stays. All-NaN FR/ETP group means become
  NaN despite its docstring's broad “None where a scenario has no result”; a
  wholly absent scenario remains None. No NaN/sentinel normalization was added.
- `summarise_rows` retains cached handler discovery, file-backed py_ewr joins,
  object assignments and report callbacks. `_successful_summary` can invoke an
  arbitrary side-effecting fetch. `ewr_thresholds` retains its implicit sheet
  load when None; `ewr_handlers` retains config reads and printing on failure.
  Their pure portions need explicit data/config boundaries before extraction.

**README comparison (definitions checked, no scientific change):** README's
opening FR/ETP definitions and the server module docstring agree: raw FR is
achieved/target frequency; ETP is excess days / record days, shown as a percent
by the UI. Python's raw table averaging does NOT cap FR; the unchanged JavaScript
caps each EWR for single-scenario display means and excludes “met in both” from
differential means. This is a layer distinction, not a newly added Python cap.
The builder module intro's plain-mean description omits the UI's hierarchy/cap.
There are two details not stated in README's simplified change-band wording:
JS always counts target crossings as gain/loss even below the band, and applies
1e-9 numerical tolerance to threshold/band equality. Existing JS tests explicitly
cover crossings; new tests cover exact 2-year, 1-year and 1.999-year below-target
changes, “met in both”, and ETP's 1-year band. Preserve these exceptions and ask
scientists to review the documentation discrepancy later.

## Phase 0 implementation

The move-only commit uses `git mv` and reports 100% similarity for all 13 files:

- Python: `server.py`, `observed.py`, `custom_ewr.py`, `build_dashboard.py`,
  `embed_scenarios.py`, `drought_windows.py`, `flow_regime_mappings.py`,
  `northern_connectivity.py`, `sync_parameter_sheet.py`.
- Web assets: `dashboard_template.html`, `performance.js`, `ewr_guide.js`,
  `custom_ewr.js`.

All now live in `src/ewr_analysis/`. Package sibling imports use `ewr_analysis`;
tests import the package and no longer edit `sys.path`. Pytest's standard
`pythonpath = ["."]` setting makes the existing root `run.py` importable in its
launcher tests. The `__main__` server alias now uses the package module name so
custom-EWR callbacks share the running server's state.

Setuptools supplies the editable build backend. Runtime dependencies exactly
match `requirements.txt`; requirements files continue working. JS/HTML assets
are declared as package data. `run.py` launches the package with `-m` from `src/`,
so the existing double-click launchers work without requiring an editable
installation. Documented direct module commands use `python -m ewr_analysis.…`
after `pip install -e .`. Hook paths and asset-match patterns follow the move.

`paths.py` owns the checkout root, package assets, `data/`, `.user_scenarios`,
`.observed_cache` and generated dashboard locations. The six path-owning
modules route through it. This phase deliberately preserves editable-checkout
locations; dataset distribution, user-data relocation and publication remain
later work.

`MIGRATION_LEDGER.md` has 117 pending rows: every server function, including
methods and nested helpers. Destinations follow §§2 and 3.1; unresolved module
splits, callers and per-symbol test coverage are marked TBD. O3, O4 and O5 remain
undecided. No Phase 1 extraction has started.

## Verification commands and output

The full suites use the same socket/network permissions as the authoritative
baseline. No test expectations or snapshots were updated.

```sh
source .venv/bin/activate
pytest -q > /tmp/ewr-phase0-pytest-final.log 2>&1
# First post-move run: 794 passed in 156.40s (0:02:36), zero skips/failures.
pytest -q > /tmp/ewr-phase0-pytest-verified.log 2>&1
# Final run after annotation-only cleanup: 794 passed in 140.93s (0:02:20), zero skips/failures.
node --test tests/test_performance.js tests/test_dashboard_core.js tests/test_ewr_guide.js tests/test_custom_ewr.js tests/test_heatmap_selection.js > /tmp/ewr-phase0-node-final.log 2>&1
# 205 tests; 205 passed; 0 failed; 0 skipped; 0 cancelled; 0 todo.
mypy
# Success: no issues found in 37 source files
ruff check src/ewr_analysis tests
# All checks passed!
ruff format --check src/ewr_analysis tests
# 37 files already formatted
pre-commit install --hook-type pre-commit
pre-commit run --all-files > /tmp/ewr-phase0-precommit-verified.log 2>&1
# Exit 0: every applicable hook passed; the symlink hook had no files to check.
```

To establish the pre-existing mypy errors without altering the checkout:

```sh
mkdir -p /tmp/ewr-phase0-original
git archive pre-refactor pyproject.toml src tests run.py | tar -x -C /tmp/ewr-phase0-original
cd /tmp/ewr-phase0-original
/Users/roryhackett/Desktop/ewr-analysis-dashboard/.venv/bin/mypy --cache-dir /tmp/ewr-phase0-original-mypy-cache > /tmp/ewr-phase0-original-mypy.log 2>&1
# Found 59 errors in 5 files (checked 35 source files)
```

Fresh editable installation (new venv without system site packages):

```sh
/opt/homebrew/bin/python3 -m venv /tmp/ewr-phase0-fresh-venv
/tmp/ewr-phase0-fresh-venv/bin/python -m pip install -e /Users/roryhackett/Desktop/ewr-analysis-dashboard > /tmp/ewr-phase0-fresh-install.log 2>&1
# Successfully built ewr-analysis-dashboard
# Successfully installed ... ewr-analysis-dashboard-0.0.0 ... py_ewr-2.4.11 ...
cd /private/tmp
/tmp/ewr-phase0-fresh-venv/bin/python -c "import ewr_analysis; print('Outside-repository import:', ewr_analysis.__file__)"
# Outside-repository import: /Users/roryhackett/Desktop/ewr-analysis-dashboard/src/ewr_analysis/__init__.py
```

Launcher smoke test: `/tmp/ewr-phase0-smoke.py` starts the following command
from `/private/tmp`, waits for the printed URL, fetches the page and ping with
`urllib.request.urlopen`, then sends SIGINT to the process group and waits for
shutdown:

```sh
/Users/roryhackett/Desktop/ewr-analysis-dashboard/.venv/bin/python /Users/roryhackett/Desktop/ewr-analysis-dashboard/run.py --no-browser 9870
# GET http://localhost:9870/ewr_analysis_dashboard.html: HTTP 200, 5396600 bytes
# GET http://localhost:9870/api/ping: HTTP 200, ok=True
```

Mechanical checks:

```sh
git diff --stat pre-refactor..HEAD -M
# All 13 source/asset moves appear as src/{ => ewr_analysis}/... renames.
git grep -n '__file__' -- src
# src/ewr_analysis/paths.py:5:PACKAGE_DIR = Path(__file__).resolve().parent
git diff --name-only pre-refactor -- data tests/fixtures .github .omnigent .coderabbit.yaml scripts/run_omnigent_review.py
# No output.
git diff --check
# No output.
.venv/bin/python /tmp/ewr-phase0-ast-check.py
# Executable ASTs match across 211 top-level functions/classes after normalizing
# package imports, annotations/casts, docstrings and command/path strings.
# All three standalone JS assets are byte-identical; all 30 protected input
# files retain their SHA-256 hashes.
```

The untracked `.claude/` directory and `REORGANIZATION_PLAN.md` remain untracked
and unchanged. Generated HTML, caches, `.venv/` and editable-install egg metadata
are ignored and not committed. No protected review tooling was changed.

The first final all-files attempt detected a concurrent edit to these notes;
all code/test hooks already passed. Repeating the entire gate with files held
fixed returned exit 0. No hook-driven source or permission change remains.

## Implementation commits

Every commit message states "no behaviour change". The documentation-only
verification commit follows these implementation commits:

```text
10c981c docs: record Phase 0 baseline; no behaviour change
e0de2eb refactor: move flat modules and web assets into package; no behaviour change
6ab577f refactor: use package imports and preserve server module identity; no behaviour change
ffa0d78 build: add editable packaging and update entry-point paths; no behaviour change
3839636 refactor: centralize checkout data and state locations in paths.py; no behaviour change
e803ee7 test: use package imports and relocated assets; no behaviour change
c2139c7 chore: resolve existing mypy errors with annotations and casts; no behaviour change
14b14f0 docs: seed 117 pending server function ledger rows; no behaviour change
```


## Phase 1, slice 1: Step A acceptance check

Commands: `git show --stat c2139c7` and `git show --format=fuller c2139c7`.
The complete diff was reviewed: 5 files, 62 insertions and 59 deletions.
Every executable-expression change is an identity-preserving `typing.cast`;
the remaining changes are annotations and their supporting `typing` imports.
There are no new assertions/guards, reordered/removed statements, changed
defaults, altered operations or changed runtime values. The added ordinary
`typing` imports are needed to resolve casts and type names; they are the only
supporting imports beyond the user's literal annotation/cast categories.
No reversion was needed, so there is no Step A reversion commit.

`ast.dump` of each complete module at `c2139c7^` and `c2139c7`, after removing
annotations, supporting `typing` imports and two-argument `typing.cast` wrappers,
was identical. This comparison did not remove docstrings, change constants or
normalize any other runtime statements. Output:

```text
src/ewr_analysis/custom_ewr.py: equivalent after stripping annotations, typing imports and typing.cast
src/ewr_analysis/server.py: equivalent after stripping annotations, typing imports and typing.cast
tests/test_embed_scenarios.py: equivalent after stripping annotations, typing imports and typing.cast
tests/test_observed.py: equivalent after stripping annotations, typing imports and typing.cast
tests/test_observed_classification.py: equivalent after stripping annotations, typing imports and typing.cast
PASS: all 5 complete module ASTs are equivalent; no runtime statement changes outside typing support.
```

```sh
source .venv/bin/activate
pytest -q > /tmp/ewr-slice1-step-a-pytest.log 2>&1
# 794 passed in 143.15s (0:02:23), zero failures/skips
node --test tests/test_performance.js tests/test_dashboard_core.js tests/test_ewr_guide.js tests/test_custom_ewr.js tests/test_heatmap_selection.js > /tmp/ewr-slice1-step-a-node.log 2>&1
# 205 tests, 205 passed, 0 failed, 0 skipped
```

Step A passed and was reported before the extraction began.

## Phase 1, slice 1: purity and unchanged move

Before moving, `git grep -nE "DROUGHTS|flow_mappings"` was run and every hit
reviewed, including the table-reader tests. No table mutation was found.
`test_a_list_value_is_returned_as_a_copy` appends to the returned copy, then
verifies the mapping's list is unchanged. The dashboard's JavaScript `DROUGHTS`
is a separate derived array and is only read.

The user explicitly authorized moving these two module-level lookup constants
and their readers unchanged, with later TOML migration recorded in the ledger.
The other nine functions have no application-state globals, I/O or locks; their
only dependencies are stdlib/numpy/pandas operations and another pure helper.
All nine function ASTs (including signatures, original docstrings, casts and
operation order) matched the pre-slice versions exactly in the move commit.
The drought and flow-regime table/reader modules were byte-identical copies.
No other logic was extracted. Both package `__init__.py` files are empty.

### Old/new comparison

```sh
python scripts/compare_implementations.py
```

The script loads original sources with `git show pre-refactor:src/<module>.py`
into temporary in-memory modules. For `server.py`, it compiles only the nine
original function ASTs, with their original names/signatures/bodies and the
required stdlib namespace. This avoids executing the old server's unrelated
I/O, imports and state initialization. It compares 270 input cases, exact
structure/types, Series metadata, table order, exceptions and warnings.
No numerical tolerance is used. Output (exit 0):

```text
DROUGHTS / flow_mappings: exact values and order match pre-refactor
mcnemar_p: cases=84, max_abs=0, max_rel=0, matched_exceptions=0
exceed_pct: cases=24, max_abs=0, max_rel=0, matched_exceptions=0
nice_ceiling: cases=18, max_abs=0, max_rel=0, matched_exceptions=2
_mrip_days: cases=9, max_abs=0, max_rel=0, matched_exceptions=0
record_days: cases=7, max_abs=0, max_rel=0, matched_exceptions=0
_feb29: cases=8, max_abs=0, max_rel=0, matched_exceptions=0
_water_year: cases=7, max_abs=0, max_rel=0, matched_exceptions=0
_water_year_days: cases=8, max_abs=0, max_rel=0, matched_exceptions=0
_window_days: cases=24, max_abs=0, max_rel=0, matched_exceptions=0
window_dates: cases=17, max_abs=0, max_rel=0, matched_exceptions=0
meta: cases=1, max_abs=0, max_rel=0, matched_exceptions=0
regimes_for: cases=63, max_abs=0, max_rel=0, matched_exceptions=0
PASS: all 12 moved functions match pre-refactor exactly
```

A negative control patched only the new `nice_ceiling` result in memory to
add one. The script returned 1 and printed `max_abs=1, max_rel=1`. No source,
fixture or data file was changed by this check.

### Observed oddities (preserved)

- `_window_days` uses the year the water year ends; `_water_year`, `_feb29` and
  `_water_year_days` use its starting year. Their conventions remain distinct.
- `_mrip_days` maps NaN/nonpositive values to zero, but `None` and an empty
  string raise `TypeError` before conversion, despite the old "blank" wording.
- `exceed_pct` on an empty record returns NaN values and emits NumPy's invalid
  divide warning. No empty-input policy has been invented.
- `nice_ceiling` returns 100 for nonpositive values (and `None`), but raises
  for NaN or positive infinity. These results/exceptions are unchanged.


## Phase 1, slice 1: architecture and final verification

Added **70 core test cases**, **1 architecture test** and **1 rebuild-detection
regression test**, beyond the unchanged 794-test Step A baseline. The McNemar reference enumerates equiprobable binary
sequences and sums outcomes no more likely than the observation; it does not
reuse the implementation's binomial-coefficient/tail-sum expression and adds
no SciPy dependency. Other expected results are derived in test comments.

The only new declared dependency is the requested development tool
`import-linter==2.15`, whose executable is `lint-imports`. Runtime requirements
are unchanged. The forbidden contract uses `include_external_packages = true`
and forbids all application siblings of `ewr_analysis.core`, plus the ten named
external packages. The sibling wildcard includes future config/data_io/
workflows/interfaces/visualization packages without creating placeholders;
core may still import its own descendants. This uses the documented
[forbidden-contract wildcard/overlap behavior](https://github.com/seddonym/import-linter/blob/main/docs/contract_types/forbidden.md).

Synthetic in-memory import graphs verified that all five prohibited application
layers and all ten named external targets are rejected for both direct and
indirect imports (**30 negative controls**). These checks create no source
folders. The required text-pattern test scans every core Python source file,
including docstrings, with no allowlist. Both core initializer files are empty.

Final commands (repository root, existing activated venv):

```sh
source .venv/bin/activate
pytest -q > /tmp/ewr-slice1-final-pytest.log 2>&1
# 866 passed in 139.23s (0:02:19), zero failures/skips
node --test tests/test_performance.js tests/test_dashboard_core.js tests/test_ewr_guide.js tests/test_custom_ewr.js tests/test_heatmap_selection.js > /tmp/ewr-slice1-final-node.log 2>&1
# 205 passed, 0 failed, 0 skipped; 205 total
python scripts/compare_implementations.py > /tmp/ewr-slice1-final-compare.log
# Exit 0; 270 cases; all 12 functions have max_abs=0 and max_rel=0
lint-imports > /tmp/ewr-slice1-final-imports.log
# Contracts: 1 kept, 0 broken
mypy
# Success: no issues found in 49 source files
pytest -q tests/core tests/architecture
# 71 passed in 0.05s
pre-commit run --all-files > /tmp/ewr-slice1-final-precommit.log 2>&1
# Exit 0: every applicable hook passed; the symlink hook had no files to check.
git grep -n '__file__' src/ewr_analysis/core
# No output; exit 1 (no matches).
git grep -nE 'open\(|read_csv|read_excel|os\.environ|__file__|lru_cache|Lock\(' -- src/ewr_analysis/core
# No output; exit 1 (no matches).
git diff --name-only 29370e3 -- data tests/fixtures .github .omnigent .coderabbit.yaml scripts/run_omnigent_review.py
# No output.
git diff --check
# No output.
```

Additional complete-module AST checks against `29370e3` confirmed:

- Removing only the nine extracted function definitions from the old server,
  and only their compatibility imports from the new server, makes the complete
  module ASTs identical. No other server statements were changed.
- All nine extracted function signatures/bodies are identical after removing
  only docstrings. Both table/reader module ASTs are likewise identical after
  removing only function docstrings.
- The docstring commit changes only twelve function docstrings across seven
  modules: the seven complete module ASTs match its parent after stripping
  those docstrings, without any other normalization.
- Old table shims expose the same objects as core, and private server helper
  names still resolve to the extracted functions.

The documentation uses these local audit identifiers for rules visible in the
legacy source; these are traceability labels, not claims of reviewed scientific
provenance. Formal sources and scientific suitability remain explicitly
**TBD, needs scientist review** in the docstrings:

| Identifier | Core symbol | Preserved rule |
|---|---|---|
| EQ-PAIR-01 | paired_comparison.mcnemar_p | Exact two-sided fair-binomial tail; four-decimal rounding |
| EQ-FDC-01 | flow_duration_curves.exceed_pct | Percent greater than or equal; left search in sorted observations |
| EQ-AXIS-01 | axis_limits.nice_ceiling | Ceiling on the original power-of-ten grid; sentinel 100 |
| EQ-MRIP-01 | event_gaps._mrip_days | Positive years times 365, rounded to an integer day |
| EQ-WINDOW-01 | assessment_years._window_days | Inclusive month mask in an ending-labeled water year |
| EQ-WY-START-01 | assessment_years._water_year | Starting calendar year of a July-start interval |
| EQ-RECORD-DAYS-01 | assessment_years.record_days | Trim nonmissing endpoints, then daily reindex without interpolation |
| EQ-LEAP-01 | assessment_years._feb29 | Gregorian leap test on start year plus one |
| EQ-WY-DAYS-01 | assessment_years._water_year_days | 365 or 366 according to the included February |
| EQ-DROUGHT-DATES-01 | drought_windows.window_dates | First/last inclusive assessment-year dates for catalog labels |
| EQ-DROUGHT-META-01 | drought_windows.meta | Catalog projection in insertion order |
| EQ-REGIME-01 | flow_regimes.regimes_for | Longest substring key; first declared key breaks ties |

All listed symbols moved after the explicit constant-table authorization;
none were excluded. Table TOML conversion, other scientific logic and slice 2
remain deferred. The observed oddities above were documented, not fixed.
No fixture, snapshot, data or protected review-tooling file changed. The
pre-existing untracked `.claude/` and plan remain untouched.

Slice implementation commits (the separately authorized rebuild fix uses the
requested "no scientific behaviour change" label):

```text
cda5036 refactor: extract slice 1 leaf calculations with compatibility shims; no behaviour change
c941547 test: compare slice 1 calculations with pre-refactor and record Step A; no behaviour change
aebd552 test: add independent known answers for slice 1 calculations; no behaviour change
5309111 build: enforce core import and I/O boundaries with import-linter; no behaviour change
3227c8f docs: record slice 1 destinations, tests and later TOML migration; no behaviour change
08ec2e4 docs: add units, assumptions and provenance to leaf docstrings; no behaviour change
1050275 build: rebuild-detection fix, no scientific behaviour change
```

A final documentation-only commit records these verification results.


### Authorized rebuild-detection follow-up

The original `is_stale()` scanned only the top-level package Python scripts.
After moving the two table sources into core/calculations, later edits there
would not have invalidated generated HTML. This path-dependent regression was
reported, and the user explicitly authorized a recursive scan plus a focused
test. Commit `1050275` changes only `HERE.glob("*.py")` to `HERE.rglob("*.py")`
in the existing input list; no scientific calculation is changed. This also
covers later config/data_io/workflows subpackages, as requested.

The regression test copies the source package to pytest's temporary directory,
sets copied inputs older than a dummy generated page, then touches only the
temporary `core/calculations/assessment_years.py` to make it newer. It confirms
freshness before that touch and staleness afterward. It does not touch the real
tables, data, fixtures or snapshots.

```sh
pytest -q tests/test_rebuild_detection.py
# Before the fix: 1 failed in 0.12s; the final is_stale() assertion got False.
# After the fix: 1 passed in 0.08s.
```

Before this follow-up, the full slice suite passed **865 tests in 141.59s**.
The final rerun includes the additional regression case: **866 passed in 139.23s (0:02:19)**,
with zero failures/skips.
All 30 protected data/fixture files also retain their original Phase 0 SHA-256
hashes. The existing builder prints 301 FR variant-conflict groups and retains
its existing mean aggregation; this policy and the data remain unchanged.

## Phase 1 slice 2 — comparison evidence

Scope starts at `2801c86`; move/shim commit `e66093c` extracts 16 functions,
including the explicitly authorized `_iso_date` helper. `_pair_years` stays in
server because it mutates its caller's payload. No table values move or change
in this slice; the existing recursive staleness scan covers the new module.

Commands (existing `.venv`, repository root):

```sh
source .venv/bin/activate
python scripts/compare_implementations.py
```

Exit 0. Exact comparisons include explicit date strings, timezone-aware dates,
numeric nanosecond timestamps, and the None/NaT/blank error cases. The two
`now`/`today` cases assert acceptance by both implementations only; their return
values are never compared. DatetimeIndex Series/DataFrames cover leap years,
empty/single-event records, NaN breaks, timing boundaries and paired missingness.

```text
DROUGHTS / flow_mappings: exact values and order match pre-refactor
mcnemar_p: cases=84, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
exceed_pct: cases=24, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
nice_ceiling: cases=18, max_abs=0, max_rel=0, matched_exceptions=2, clock_acceptance_only=0
_mrip_days: cases=9, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
record_days: cases=7, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
_feb29: cases=8, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
_water_year: cases=7, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
_water_year_days: cases=8, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
_window_days: cases=24, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
window_dates: cases=17, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
meta: cases=1, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
regimes_for: cases=63, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
_iso_date: cases=12, max_abs=0, max_rel=0, matched_exceptions=4, clock_acceptance_only=2
assessment_start: cases=16, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
_timing_mask: cases=20, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
complete_years: cases=20, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
year_summary: cases=6, max_abs=0, max_rel=0, matched_exceptions=1, clock_acceptance_only=0
window_metrics: cases=20, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
achievement_frequencies: cases=31, max_abs=0, max_rel=0, matched_exceptions=6, clock_acceptance_only=0
ewr_gaps: cases=24, max_abs=0, max_rel=0, matched_exceptions=6, clock_acceptance_only=0
gap_severity: cases=12, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
_dateless_gap_stats: cases=18, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
_runs_by_year: cases=18, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
_has_any_event: cases=4, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
dr_years: cases=24, max_abs=0, max_rel=0, matched_exceptions=4, clock_acceptance_only=0
vr_years: cases=20, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
dr_approx: cases=21, max_abs=0, max_rel=0, matched_exceptions=1, clock_acceptance_only=0
_paired_achievement: cases=4, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
PASS: all 28 moved functions match pre-refactor exactly for deterministic inputs; 2 relative-date cases check acceptance only
```

Deliberate-mutation proof (process-local, no source/fixture/snapshot edits):

```sh
python - <<'PY'
import runpy
from ewr_analysis.core.calculations import duration_ratio
module = runpy.run_path("scripts/compare_implementations.py")
original = duration_ratio.dr_approx
def changed(*args):
    return [None if x is None else x + 0.01 for x in original(*args)]
duration_ratio.dr_approx = changed
raise SystemExit(module["main"]())
PY
```

Exit **1**, as required. Relevant output:

```text
dr_approx: cases=21, max_abs=0.01, max_rel=inf, matched_exceptions=1, clock_acceptance_only=0
FAIL
dr_approx case 14: abs=0.010000000000000009, rel=inf
dr_approx case 18: abs=0.010000000000000009, rel=inf
```

`lint-imports --no-cache`: 1 contract kept, 0 broken (65 files, 138
dependencies at move time). The existing boundary needs no extension.

## Phase 1 slice 2 — final verification and scientist review

No slice 3 work was started. The five requested commits are:

```text
e66093c Move slice 2 assessment and gap calculations with shims; no behaviour change
c5d49bf Extend slice 2 baseline comparison and record negative control; no behaviour change
3c079d7 Add independent slice 2 known-answer tests; no behaviour change
938b14b Record slice 2 moves, purity exception and preserved issues; no behaviour change
7c5343d Document slice 2 calculations using the plan template; no behaviour change
```

Commands use the existing `.venv` from the repository root, with the same socket
and network access as the accepted baseline. No expectations were changed:

```sh
source .venv/bin/activate
pytest -q
node --test tests/test_performance.js tests/test_dashboard_core.js tests/test_ewr_guide.js tests/test_custom_ewr.js tests/test_heatmap_selection.js
pre-commit run --all-files
lint-imports --no-cache
python scripts/compare_implementations.py
```

```text
901 passed in 140.25s (0:02:20)
node: tests 205; pass 205; fail 0; skipped 0; cancelled 0; todo 0
lint-imports: 1 kept, 0 broken (65 files, 138 dependencies)
compare_implementations.py: exit 0; all 28 max_abs=0, max_rel=0
  _iso_date: 12 cases; 4 matching exceptions; 2 clock-acceptance-only cases
```

901 = 866 accepted tests + 35 new known-answer cases; **zero skips**. The full
suite includes both unchanged golden checks:
`tests/test_dashboard_html.py::TestBaseline::test_aggregates_match_the_stored_baseline`
and `tests/test_pyewr_wrappers.py::TestRunScenario::test_matches_the_baseline_snapshot`.
Their JSON bytes and every protected baseline hash remain unchanged.

All-files hook output (exit 0):

```text
check python ast..........................................................Passed
check yaml................................................................Passed
check toml................................................................Passed
check json................................................................Passed
check for merge conflicts.................................................Passed
check for broken symlinks.............................(no files to check)Skipped
check that scripts with shebangs are executable...........................Passed
detect private key........................................................Passed
don't commit to branch....................................................Passed
check for case conflicts..................................................Passed
debug statements (python).................................................Passed
mixed line ending.........................................................Passed
fix end of files..........................................................Passed
trim trailing whitespace..................................................Passed
check for added large files...............................................Passed
ruff check................................................................Passed
ruff format...............................................................Passed
mypy......................................................................Passed
lint-imports (core dependency boundary)...................................Passed
pytest (fast, not slow)...................................................Passed
node --test (dashboard JavaScript)........................................Passed
parameter sheet matches MDBA EWR_tool (when edited).......................Passed
```

AST audit is reproducible with this script (run with `python` from repo root):

```python
import ast
import copy
import subprocess
from pathlib import Path

START = "2801c86"
MOVE = "e66093c"
DOC = "7c5343d"
MOVED = {
    "assessment_years": ["_iso_date", "assessment_start", "_timing_mask", "complete_years", "year_summary", "window_metrics", "achievement_frequencies"],
    "event_gaps": ["ewr_gaps", "gap_severity", "_dateless_gap_stats", "_runs_by_year", "_has_any_event"],
    "duration_ratio": ["dr_years", "vr_years", "dr_approx"],
    "paired_comparison": ["_paired_achievement"],
}


def tree(ref, path):
    return ast.parse(Path(path).read_text() if ref is None else subprocess.check_output(["git", "show", f"{ref}:{path}"], text=True))


def function(module, name):
    return copy.deepcopy(next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == name))


class NoDocs(ast.NodeTransformer):
    def visit_FunctionDef(self, node):
        self.generic_visit(node)
        if ast.get_docstring(node, clean=False) is not None:
            node.body.pop(0)
        return node


def dump(node):
    return ast.dump(node, include_attributes=False)


for module, names in MOVED.items():
    path = f"src/ewr_analysis/core/calculations/{module}.py"
    for name in names:
        old_file = "build_dashboard" if name == "dr_approx" else "server"
        legacy = function(tree("pre-refactor", f"src/{old_file}.py"), name)
        previous = function(tree(START, f"src/ewr_analysis/{old_file}.py"), name)
        moved = function(tree(MOVE, path), name)
        current = function(tree(None, path), name)
        assert dump(previous) == dump(moved), name
        if name == "_iso_date":
            # Only the previously accepted Phase 0 identity cast differs.
            call = moved.body[-1].value
            assert isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "cast"
            assert len(call.args) == 2 and isinstance(call.args[0], ast.Name) and call.args[0].id == "str"
            moved.body[-1].value = call.args[1]
            current.body[-1].value = current.body[-1].value.args[1]
        assert dump(legacy) == dump(moved), name
        assert dump(NoDocs().visit(legacy)) == dump(NoDocs().visit(current)), name
        print(f"{name}: move AST exact; final AST exact excluding docs and prior _iso_date cast")
    assert dump(NoDocs().visit(tree(f"{DOC}^", path))) == dump(NoDocs().visit(tree(DOC, path))), module

for filename in ("server", "build_dashboard"):
    path = f"src/ewr_analysis/{filename}.py"
    previous, current = tree(START, path), tree(None, path)
    names = {name for group in MOVED.values() for name in group if (name == "dr_approx") == (filename == "build_dashboard")}
    previous.body = [n for n in previous.body if not isinstance(n, (ast.Import, ast.ImportFrom)) and not (isinstance(n, ast.FunctionDef) and n.name in names)]
    current.body = [n for n in current.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
    assert dump(previous) == dump(current), filename
    print(f"{filename}.py: all remaining non-import statements unchanged")
print("PASS: 16 unchanged moves; docstring commit has no other AST changes; shims are import-only")
```

Output (exit 0):

```text
_iso_date: move AST exact; final AST exact excluding docs and prior _iso_date cast
assessment_start: move AST exact; final AST exact excluding docs and prior _iso_date cast
_timing_mask: move AST exact; final AST exact excluding docs and prior _iso_date cast
complete_years: move AST exact; final AST exact excluding docs and prior _iso_date cast
year_summary: move AST exact; final AST exact excluding docs and prior _iso_date cast
window_metrics: move AST exact; final AST exact excluding docs and prior _iso_date cast
achievement_frequencies: move AST exact; final AST exact excluding docs and prior _iso_date cast
ewr_gaps: move AST exact; final AST exact excluding docs and prior _iso_date cast
gap_severity: move AST exact; final AST exact excluding docs and prior _iso_date cast
_dateless_gap_stats: move AST exact; final AST exact excluding docs and prior _iso_date cast
_runs_by_year: move AST exact; final AST exact excluding docs and prior _iso_date cast
_has_any_event: move AST exact; final AST exact excluding docs and prior _iso_date cast
dr_years: move AST exact; final AST exact excluding docs and prior _iso_date cast
vr_years: move AST exact; final AST exact excluding docs and prior _iso_date cast
dr_approx: move AST exact; final AST exact excluding docs and prior _iso_date cast
_paired_achievement: move AST exact; final AST exact excluding docs and prior _iso_date cast
server.py: all remaining non-import statements unchanged
build_dashboard.py: all remaining non-import statements unchanged
PASS: 16 unchanged moves; docstring commit has no other AST changes; shims are import-only
```

The move commit preserves complete function ASTs against the accepted pre-slice
source, including signatures, docstrings and casts. Fifteen are also literally
equal to pre-refactor; `_iso_date` retains only its previously accepted Phase 0
`typing.cast(str, ...)` difference. After the separate docstrings commit, only
function docstrings and that prior cast are excluded from comparison. Every
remaining non-import statement in server.py/build_dashboard.py is unchanged
from `2801c86` after extracted definitions are removed. The docstrings-only
commit was also checked as a whole-module AST equivalence with docs removed.

Boundary and protected-path commands:

```sh
git grep -nE 'open\(|read_csv|read_excel|os\.environ|__file__|lru_cache|Lock\(' -- src/ewr_analysis/core
git grep -n '__file__' -- src/ewr_analysis
git diff --name-only 2801c86 -- data/ tests/fixtures/ .github/ .omnigent/ .coderabbit.yaml scripts/run_omnigent_review.py
git diff --check 2801c86
git grep -n 'def _iso_date' -- src/
git grep -n 'import _iso_date as _iso_date' -- src/ewr_analysis/server.py
```

```text
core forbidden-pattern grep: no matches (exit 1, as expected)
__file__: src/ewr_analysis/paths.py:5 only
protected-path diff: no output
whitespace/error check: no output
_iso_date definition: assessment_years.py only; server.py re-export shim present
30/30 Phase 0 protected SHA-256 hashes unchanged
```

The static core import inventory contains calendar, datetime, math, typing,
numpy, pandas and core-internal imports only. No import-linter extension was
needed. No files under snapshots/data/fixtures or review-tooling paths changed;
no new package folders, dependency changes, staleness changes, or generated
artifacts were committed. Existing `.claude/` and REORGANIZATION_PLAN.md remain
untracked and untouched.

Scientist review checklist (all observations remain unchanged):

- Review each of the 17 preserved observations in the slice 2 subsection
  under **Observed issues**: relative-input wall clock; assessment-year labels;
  timing-day sentinels; naive-calendar completeness; None versus NaN summary;
  full-gap severity and uncapped window ET; frequency pairing/duration order;
  whole-gap censoring and empty records; truthy excess episodes; dateless missing
  severity; empty-event membership; row adjacency/year-split runs; k-event numeric
  coercion; fractional volume accumulation; negative approximate duration;
  duplicate/nonbinary pairing; and deferred payload mutation.
- Review every new **TBD, needs scientist review** docstring: assessment_years
  (`_iso_date`, `assessment_start`, `_timing_mask`, `complete_years`, `year_summary`,
  `window_metrics`, `achievement_frequencies`); event_gaps (`ewr_gaps`,
  `gap_severity`, `_dateless_gap_stats`, `_runs_by_year`, `_has_any_event`);
  duration_ratio (`dr_years`, `vr_years`, `dr_approx`); paired_comparison
  (`_paired_achievement`). Scientific provenance/suitability is not inferred
  from matching legacy outputs; no reference was invented.
- The one excluded candidate, `_pair_years`, stays in server because it mutates
  the caller's response. Split pure common-year selection from interface
  assignment first. `_iso_date` input validation also belongs at that later
  interface boundary. The read-only tables remain slated for config/*.toml.

## Phase 1 slice 3 — comparison evidence

Accepted starting point: `2340d29`. Move/shim commit: `e0023a7`.
Read every candidate before moving: 13 functions (including the two explicitly
authorized dispatch helpers) and POLICIES, CLIMATES, NS, LOW_FLOW_HANDLERS.
No py_ewr or shapely import is added to core. No import contract change is needed.

```sh
source .venv/bin/activate
python scripts/compare_implementations.py
```

Exit 0; per-function output:

```text
DROUGHTS / flow_mappings: exact values and order match pre-refactor
POLICIES / CLIMATES / NS / LOW_FLOW_HANDLERS: exact values and order match pre-refactor
mcnemar_p: cases=84, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
exceed_pct: cases=24, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
nice_ceiling: cases=18, max_abs=0, max_rel=0, matched_exceptions=2, clock_acceptance_only=0
_mrip_days: cases=9, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
record_days: cases=7, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
_feb29: cases=8, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
_water_year: cases=7, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
_water_year_days: cases=8, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
_window_days: cases=24, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
window_dates: cases=17, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
meta: cases=1, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
regimes_for: cases=63, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
_iso_date: cases=12, max_abs=0, max_rel=0, matched_exceptions=4, clock_acceptance_only=2
assessment_start: cases=16, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
_timing_mask: cases=20, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
complete_years: cases=20, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
year_summary: cases=6, max_abs=0, max_rel=0, matched_exceptions=1, clock_acceptance_only=0
window_metrics: cases=20, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
achievement_frequencies: cases=31, max_abs=0, max_rel=0, matched_exceptions=6, clock_acceptance_only=0
ewr_gaps: cases=24, max_abs=0, max_rel=0, matched_exceptions=6, clock_acceptance_only=0
gap_severity: cases=12, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
_dateless_gap_stats: cases=18, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
_runs_by_year: cases=18, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
_has_any_event: cases=4, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
dr_years: cases=24, max_abs=0, max_rel=0, matched_exceptions=4, clock_acceptance_only=0
vr_years: cases=20, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
dr_approx: cases=21, max_abs=0, max_rel=0, matched_exceptions=1, clock_acceptance_only=0
_paired_achievement: cases=4, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
detect_variant_conflicts: cases=12, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
scenario_index: cases=12, max_abs=0, max_rel=0, matched_exceptions=8, clock_acceptance_only=0
fix_theme: cases=6, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
objective_themes: cases=6, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
objectives_of: cases=3, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
build_row_table: cases=8, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
ewr_params: cases=6, max_abs=0, max_rel=0, matched_exceptions=1, clock_acceptance_only=0
_is_multigauge: cases=9, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
_dispatch_key: cases=16, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
_group_by_ewr: cases=4, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
_json_safe: cases=9, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
record_coverage: cases=12, max_abs=0, max_rel=0, matched_exceptions=0, clock_acceptance_only=0
_assessment_year_stats: cases=17, max_abs=0, max_rel=0, matched_exceptions=1, clock_acceptance_only=0
PASS: all 41 moved functions match pre-refactor exactly for deterministic inputs; 2 relative-date cases check acceptance only; all inputs unchanged
```

The script snapshots every old/new invocation's inputs, including exception
cases, and checks contents, dictionary order, Series/DataFrame indexes, dtypes,
and numeric values afterward. Relative date keywords retain acceptance-only
output checks from slice 2, but their inputs are still checked for mutation.
Parameter-sheet rows in `_assessment_year_stats` cases use namedtuples just as
pandas itertuples() does. No production source/fixture/data is changed by these
checks. Constants are compared against original AST-loaded assignments.

Deliberate-mutation control (changes only an argument in a temporary process,
after computing the original return value):

```sh
python - <<'PY'
import runpy
from ewr_analysis.core.calculations import ewr_parameters
module = runpy.run_path("scripts/compare_implementations.py")
original = ewr_parameters.ewr_params
def mutated(sheet):
    result = original(sheet)
    if sheet is not None and not sheet.empty:
        sheet.iloc[0, 0] = "deliberate mutation"
    return result
ewr_parameters.ewr_params = mutated
raise SystemExit(module["main"]())
PY
```

Exit **1**, as required. Relevant output:

```text
ewr_params: cases=6, max_abs=0, max_rel=0, matched_exceptions=1, clock_acceptance_only=0
FAIL
ewr_params case 1: mutated mutated its inputs: Values differ: 'g' != 'deliberate mutation'
ewr_params case 3: mutated mutated its inputs: Values differ: 'g' != 'deliberate mutation'
ewr_params case 4: mutated mutated its inputs: Values differ: 'g' != 'deliberate mutation'
```

The calculation's original return values are unchanged in this control; it fails
specifically because of the no-mutation assertion.

## Phase 1 slice 3 — final verification and scientist review

This slice stops at its audited candidate boundary; no slice 4 work is started.
Commits (all through installed hooks, no bypass):

```text
e0023a7 Move slice 3 pure aggregation and parameter helpers with shims; no behaviour change
a4ab056 Extend slice 3 comparisons and input mutation audit; no behaviour change
5db4ab6 Add slice 3 known answers and mini payload equality test; no behaviour change
0e1d347 Record slice 3 moves, mutations and README differences; no behaviour change
2315b78 Document slice 3 calculations using plan template; no behaviour change
```

The moved set is **13 functions**, plus **4 read-only constants**. Seven of the
original candidates remain at their old locations, with reasons and exact
mutation/side-effect locations in MIGRATION_LEDGER.md. No speculative split or
scientific behavior correction was introduced.

Commands from repository root using the existing environment and baseline
socket/network permissions:

```sh
source .venv/bin/activate
pytest -q
node --test tests/test_performance.js tests/test_dashboard_core.js tests/test_ewr_guide.js tests/test_custom_ewr.js tests/test_heatmap_selection.js
pre-commit run --all-files
lint-imports --no-cache
python scripts/compare_implementations.py
```

```text
943 passed in 143.30s (0:02:23)
node: tests 205; pass 205; fail 0; skipped 0; cancelled 0; todo 0
lint-imports: 1 kept, 0 broken; 67 files, 150 dependencies
compare: exit 0; 41 functions / 660 cases; max_abs=0 and max_rel=0 per function
inputs: unchanged in all 1320 old/new calls, including matched exception cases
relative dates: 2 acceptance-only cases; returned values never compared
```

42 new Python cases add to the accepted **901**. They include 13-function known
answers, existing raw Python aggregation boundary cases, unchanged JS display
rules invoked through a Node VM, and the mini fixture payload regression. The
payload test substitutes original pre-refactor calculation globals into the
unchanged builder, builds the fixture payload, restores the core shims, and
asserts identical dictionaries, strict JSON and complete rendered HTML.

The complete suite includes both untouched golden snapshot checks:
`tests/test_dashboard_html.py::TestBaseline::test_aggregates_match_the_stored_baseline`
and `tests/test_pyewr_wrappers.py::TestRunScenario::test_matches_the_baseline_snapshot`.
Snapshot JSON bytes and all 30 Phase 0 protected SHA-256 hashes remain unchanged.

All-files hook output (exit 0):

```text
check python ast..........................................................Passed
check yaml................................................................Passed
check toml................................................................Passed
check json................................................................Passed
check for merge conflicts.................................................Passed
check for broken symlinks.............................(no files to check)Skipped
check that scripts with shebangs are executable...........................Passed
detect private key........................................................Passed
don't commit to branch....................................................Passed
check for case conflicts..................................................Passed
debug statements (python).................................................Passed
mixed line ending.........................................................Passed
fix end of files..........................................................Passed
trim trailing whitespace..................................................Passed
check for added large files...............................................Passed
ruff check................................................................Passed
ruff format...............................................................Passed
mypy......................................................................Passed
lint-imports (core dependency boundary)...................................Passed
pytest (fast, not slow)...................................................Passed
node --test (dashboard JavaScript)........................................Passed
parameter sheet matches MDBA EWR_tool (when edited).......................Passed
```

Reproducible AST audit (run with Python from repository root):

```python
import ast, copy, subprocess
from pathlib import Path

START = "2340d29"
MOVED = {
    "frequency_ratio": {
        "build_dashboard": ["detect_variant_conflicts", "scenario_index", "fix_theme", "objective_themes", "objectives_of", "build_row_table"],
        "server": ["_group_by_ewr", "_json_safe", "_assessment_year_stats"],
    },
    "ewr_parameters": {"build_dashboard": ["ewr_params"], "server": ["_is_multigauge", "_dispatch_key"]},
    "assessment_years": {"server": ["record_coverage"]},
}
CONSTANTS = {"frequency_ratio": {"build_dashboard": ["POLICIES", "CLIMATES", "NS"]}, "ewr_parameters": {"server": ["LOW_FLOW_HANDLERS"]}}


def tree(ref, path):
    return ast.parse(Path(path).read_text() if ref is None else subprocess.check_output(["git", "show", f"{ref}:{path}"], text=True))


def f(t, name):
    return copy.deepcopy(next(n for n in t.body if isinstance(n, ast.FunctionDef) and n.name == name))


class NoDocs(ast.NodeTransformer):
    def visit_FunctionDef(self, n):
        self.generic_visit(n)
        if ast.get_docstring(n, clean=False) is not None:
            n.body.pop(0)
        return n


def dump(n):
    return ast.dump(n, include_attributes=False)


for mod, files in MOVED.items():
    p = f"src/ewr_analysis/core/calculations/{mod}.py"
    for file, names in files.items():
        for name in names:
            previous = f(tree(START, f"src/ewr_analysis/{file}.py"), name)
            old = f(tree("pre-refactor", f"src/{file}.py"), name)
            new = f(tree(None, p), name)
            assert dump(NoDocs().visit(previous)) == dump(NoDocs().visit(new)), name
            assert dump(NoDocs().visit(old)) == dump(NoDocs().visit(new)), f"pre-refactor: {name}"
            print(f"{name}: AST identical to pre-refactor and pre-slice source (docstrings excluded)")
for file in ("server", "build_dashboard"):
    previous, current = tree(START, f"src/ewr_analysis/{file}.py"), tree(None, f"src/ewr_analysis/{file}.py")
    names = {n for group in MOVED.values() for n in group.get(file, [])}
    constants = {n for group in CONSTANTS.values() for n in group.get(file, [])}
    previous.body = [
        n
        for n in previous.body
        if not isinstance(n, (ast.Import, ast.ImportFrom))
        and not (isinstance(n, ast.FunctionDef) and n.name in names)
        and not (isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id in constants)
    ]
    current.body = [n for n in current.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
    assert dump(previous) == dump(current), file
    print(f"{file}.py: only imports differ after extracted functions/constants removed")
for mod, files in CONSTANTS.items():
    for file, names in files.items():
        for name in names:

            def c(t):
                return next(n for n in t.body if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == name)

            assert dump(c(tree(START, f"src/ewr_analysis/{file}.py"))) == dump(c(tree(None, f"src/ewr_analysis/core/calculations/{mod}.py"))), name
print("PASS: 13 moved functions, 4 unchanged constants, import-only shims")

# The move commit includes the original docstrings/signatures too.
for mod, files in MOVED.items():
    for file, names in files.items():
        for name in names:
            original = f(tree("pre-refactor", f"src/{file}.py"), name)
            moved = f(tree("e0023a7", f"src/ewr_analysis/core/calculations/{mod}.py"), name)
            assert dump(original) == dump(moved), name
    path = f"src/ewr_analysis/core/calculations/{mod}.py"
    assert dump(NoDocs().visit(tree("2315b78^", path))) == dump(NoDocs().visit(tree("2315b78", path))), mod
print("PASS: 13 full original ASTs; docstrings commit has no other AST changes")
```

Output (exit 0):

```text
detect_variant_conflicts: AST identical to pre-refactor and pre-slice source (docstrings excluded)
scenario_index: AST identical to pre-refactor and pre-slice source (docstrings excluded)
fix_theme: AST identical to pre-refactor and pre-slice source (docstrings excluded)
objective_themes: AST identical to pre-refactor and pre-slice source (docstrings excluded)
objectives_of: AST identical to pre-refactor and pre-slice source (docstrings excluded)
build_row_table: AST identical to pre-refactor and pre-slice source (docstrings excluded)
_group_by_ewr: AST identical to pre-refactor and pre-slice source (docstrings excluded)
_json_safe: AST identical to pre-refactor and pre-slice source (docstrings excluded)
_assessment_year_stats: AST identical to pre-refactor and pre-slice source (docstrings excluded)
ewr_params: AST identical to pre-refactor and pre-slice source (docstrings excluded)
_is_multigauge: AST identical to pre-refactor and pre-slice source (docstrings excluded)
_dispatch_key: AST identical to pre-refactor and pre-slice source (docstrings excluded)
record_coverage: AST identical to pre-refactor and pre-slice source (docstrings excluded)
server.py: only imports differ after extracted functions/constants removed
build_dashboard.py: only imports differ after extracted functions/constants removed
PASS: 13 moved functions, 4 unchanged constants, import-only shims
PASS: 13 full original ASTs; docstrings commit has no other AST changes
```

Thus every moved function is literally identical to its pre-refactor AST in the
move commit, including signatures/docstrings. The final versions differ only in
function docstrings. No new cast normalization is needed for these 13 functions.
After extraction, every remaining non-import statement in the old modules is
unchanged from `2340d29`; all four constant assignment expressions match too.

Boundary/protection commands and output:

```sh
git grep -nE 'open\(|read_csv|read_excel|os\.environ|__file__|lru_cache|Lock\(' -- src/ewr_analysis/core
git grep -n '__file__' -- src/ewr_analysis
git diff --name-only 2340d29 -- data/ tests/fixtures/ .github/ .omnigent/ .coderabbit.yaml scripts/run_omnigent_review.py
git diff --check 2340d29
git grep -nE 'def (_is_multigauge|_dispatch_key)' -- src/
```

```text
core forbidden-pattern grep: no matches (exit 1, expected)
__file__: paths.py:5 only
protected-path diff: no output; snapshot paths unchanged
whitespace/error check: no output
_is_multigauge / _dispatch_key: one definition each in core/ewr_parameters.py
30/30 baseline protected hashes unchanged
```

No import-linter extension or py_ewr allow-list is needed. Core imports only
stdlib, numpy/pandas, and other core modules; no shapely or application/I/O
modules. No dependencies or staleness rules changed; the existing recursive
scan covers these files. Only the two requested module files were added, with
no new package folders. Pre-existing untracked `.claude/` and
REORGANIZATION_PLAN.md remain untouched; no generated artifacts are committed.

Scientist review checklist:

- New preserved observations under **Observed issues / Phase 1 slice 3**:
  catalogue order/unknown labels; multigauge string-only detection and unvalidated
  kind formatting; conflict sorting/null policy; exact theme spelling and mode
  ties/reference fallback; lexical objective order; compact-table alias/default
  contracts; first-row parameter coercion/truncation; grouping null/string-key
  collisions; non-float JSON edge cases; inherited annual/date rounding policies;
  supplied completeness flags; in-place normalization/list filling and raw NaN
  sentinels; callback/config/file dependencies. No item was fixed.
- README/code review: raw Python FR and display-capped means are different layers;
  ETP is a fraction displayed as percent. The builder intro omits hierarchical
  UI weighting/capping; README's change-band summary omits target-crossing
  exceptions and 1e-9 equality tolerance. The new tests preserve the existing
  single-EWR cap, surplus/shortfall mean, met-in-both exclusion, exact 2-year,
  1-year and 1.999-year cases, plus ETP's 1-year boundary.
- **TBD, needs scientist review** docstrings: frequency_ratio
  (`detect_variant_conflicts`, `scenario_index`, `fix_theme`, `objective_themes`,
  `objectives_of`, `build_row_table`, `_group_by_ewr`, `_json_safe`,
  `_assessment_year_stats`); ewr_parameters (`ewr_params`, `_is_multigauge`,
  `_dispatch_key`); assessment_years (`record_coverage`). Rules transcribe code;
  no equation or scientific reference was invented.
- Left candidates: `normalise_results` (frame mutation/identity),
  `_fill_by_scenario` (relied-on list mutation), `aggregate_ewr_rows` (excluded
  mutator dependency), `ewr_thresholds` (implicit sheet read), `ewr_handlers`
  (config read/print), `summarise_rows` (file-backed adapter/cache/reporting),
  `_successful_summary` (arbitrary callback). The ledger lists needed splits
  and destinations for these and every explicitly excluded builder/I/O endpoint.
- POLICIES, CLIMATES, NS and LOW_FLOW_HANDLERS are defined once in core, imported
  back at old locations, and still slated for config/*.toml in a later phase.
