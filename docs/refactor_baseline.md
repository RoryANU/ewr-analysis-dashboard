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
