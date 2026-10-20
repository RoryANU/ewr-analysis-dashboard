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
  parse with Python 3.10 grammar. No Python support changes have been made.
- The README references an untracked/missing `notebooks/run_your_scenarios.ipynb`.
- No pre-commit hook is installed in `.git/hooks/pre-commit` at the starting
  commit. The configured checks will be run explicitly.
