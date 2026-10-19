"""Keep gauge_pull as the sole observed-data and metadata access path."""

import ast
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import observed

SRC = Path(__file__).resolve().parents[1] / "src"
FORBIDDEN = (
    "realtimedata.waternsw",
    "data.water.vic",
    "water-monitoring.information.qld",
    "webservice.exe",
    "webservice.pl",
    "get_ts_traces",
    "get_variable_list",
    "bom_gauge_data.csv",
)


def test_no_service_endpoints_payloads_or_bundled_catalog_in_src():
    violations: list[str] = []
    for path in SRC.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        violations.extend(f"{path.relative_to(SRC)}: {token}" for token in FORBIDDEN if token in source)
    assert not violations, "Observed access must go through gauge_pull:\n" + "\n".join(violations)


def test_getter_import_and_call_exist_only_in_the_adapter():
    imports, calls = [], []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text())
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                names = []
            if any(name.split(".")[0] == "requests" for name in names):
                assert path.name == "observed.py", f"Unexpected requests import in {path}"
            if any(name.split(".")[0] == "mdba_gauge_getter" for name in names):
                assert path.name == "observed.py"
                assert isinstance(node, ast.ImportFrom) and node.module == "mdba_gauge_getter.gauge_getter"
                assert [(alias.name, alias.asname) for alias in node.names] == [("gauge_pull", None)]
                parent = parents[node]
                assert isinstance(parent, ast.FunctionDef) and parent.name == "_fetch_source"
                imports.append(node)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "gauge_pull":
                ancestor = parents[node]
                while not isinstance(ancestor, ast.Module):
                    if isinstance(ancestor, ast.FunctionDef) and ancestor.name == "_fetch_source":
                        break
                    ancestor = parents[ancestor]
                assert path.name == "observed.py" and isinstance(ancestor, ast.FunctionDef) and ancestor.name == "_fetch_source"
                calls.append(node)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "mdba_gauge_getter" in node.value:
                # Only the existing optional-package availability checks may reference the package by name.
                parent = parents[node]
                assert path.name == "server.py" and node.value == "mdba_gauge_getter"
                assert isinstance(parent, ast.Call) and ast.unparse(parent.func) == "importlib.util.find_spec"
    assert len(imports) == len(calls) == 1


@pytest.mark.parametrize("var", ["F", "L"])
def test_fetch_gauge_passes_one_gauge_state_source_and_exact_dates(monkeypatch, tmp_path, var):
    calls = []

    def pull(gauges, start, end, **kwargs):
        calls.append((gauges, start, end, kwargs))
        return pd.DataFrame()

    monkeypatch.setattr("mdba_gauge_getter.gauge_getter.gauge_pull", pull)
    monkeypatch.setattr(observed, "CACHE_DIR", tmp_path)
    start, end = date(1890, 1, 1), date(2026, 10, 19)
    result = observed.fetch_gauge("412033", start, end, var=var)

    assert result.status == "empty"
    assert calls == [(["412033"], start, end, {"var": var, "interval": "day", "data_type": "mean", "data_source": "state"})]
