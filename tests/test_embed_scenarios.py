"""Tests for src/embed_scenarios.py: path handling and embedding scenario results into a copy of the page."""

import json
from pathlib import Path

import pytest

import embed_scenarios as em

ROOT = Path(__file__).resolve().parents[1]


class TestResolvePath:
    def test_existing_file(self, tmp_path):
        f = tmp_path / "run.csv"
        f.write_text("x")
        assert em.resolve_path(str(f)) == f
        assert em.resolve_path(f"  {f}  ") == f  # surrounding whitespace from a notebook cell

    def test_home_is_expanded(self, tmp_path, monkeypatch):
        (tmp_path / "run.csv").write_text("x")
        monkeypatch.setenv("HOME", str(tmp_path))
        assert em.resolve_path("~/run.csv") == tmp_path / "run.csv"

    def test_dbfs_uri_becomes_the_fuse_mount_path(self):
        with pytest.raises(FileNotFoundError, match=r"/dbfs/Volumes/a/b\.csv"):
            em.resolve_path("dbfs:/Volumes/a/b.csv")
        with pytest.raises(FileNotFoundError, match=r"/dbfs/x\.csv"):
            em.resolve_path("dbfs://x.csv")  # extra slashes collapse

    def test_a_folder_is_not_a_file_and_the_message_lists_its_siblings(self, tmp_path):
        (tmp_path / "b.csv").write_text("x")
        (tmp_path / "a.csv").write_text("x")
        (tmp_path / "sub").mkdir()
        with pytest.raises(FileNotFoundError, match=r"Files in .*: a\.csv, b\.csv, sub"):
            em.resolve_path(str(tmp_path / "sub"))

    def test_missing_folder(self, tmp_path):
        with pytest.raises(FileNotFoundError, match=r"that folder does not exist"):
            em.resolve_path(str(tmp_path / "nope" / "run.csv"))

    def test_listing_is_capped_at_eight_names(self, tmp_path):
        for i in range(12):
            (tmp_path / f"f{i:02d}.csv").write_text("x")
        with pytest.raises(FileNotFoundError) as err:
            em.resolve_path(str(tmp_path / "missing.csv"))
        assert "f07.csv" in str(err.value) and "f08.csv" not in str(err.value)


@pytest.fixture
def template(tmp_path) -> Path:
    t = tmp_path / "page.html"
    t.write_text(f"<html><script>{em.PLACEHOLDER}</script></html>")
    return t


@pytest.fixture
def fake_run(monkeypatch):
    calls = []

    def run_file(path, fmt="Standard time-series", gauges=None):
        calls.append((str(path), fmt))
        return [["PU", "405201", "BF1", 0.5, 0.25]], {"gauges_in_file": 1, "skipped": {}}

    monkeypatch.setattr(em, "run_file", run_file)
    monkeypatch.setattr(em, "review_gauges", lambda: ["405201"])
    return calls


class TestBuildWithScenarios:
    def test_results_are_embedded_as_valid_json(self, tmp_path, template, fake_run):
        f = tmp_path / "My Run.csv"
        f.write_text("x")
        out = tmp_path / "out.html"
        html = em.build_with_scenarios([{"path": str(f), "format": "Bigmod - MDBA"}], out, template)
        assert out.read_text() == html and em.PLACEHOLDER not in html
        payload = html.split("const PRELOADED=")[1].split(";</script>")[0]
        assert json.loads(payload) == [{"name": "My Run", "fmt": "Bigmod - MDBA", "vals": [["PU", "405201", "BF1", 0.5, 0.25]]}]
        assert fake_run == [(str(f), "Bigmod - MDBA")]

    def test_default_format_and_explicit_name_truncated_to_40_characters(self, tmp_path, template, fake_run):
        f = tmp_path / "a.csv"
        f.write_text("x")
        html = em.build_with_scenarios([{"path": str(f), "name": "N" * 60}], tmp_path / "o.html", template)
        data = json.loads(html.split("const PRELOADED=")[1].split(";</script>")[0])
        assert data[0]["name"] == "N" * 40 and data[0]["fmt"] == "Standard time-series"

    def test_script_closing_sequence_in_a_name_cannot_break_out_of_the_script_tag(self, tmp_path, template, fake_run):
        f = tmp_path / "a.csv"
        f.write_text("x")
        html = em.build_with_scenarios([{"path": str(f), "name": "</script><b>"}], tmp_path / "o.html", template)
        assert html.count("</script>") == 1  # only the template's own closing tag
        assert "<\\/script>" in html

    def test_duplicate_names_are_rejected_before_any_run(self, tmp_path, template, fake_run):
        f = tmp_path / "a.csv"
        f.write_text("x")
        with pytest.raises(ValueError, match="two scenarios are called 'a'"):
            em.build_with_scenarios([{"path": str(f)}, {"path": str(f)}], tmp_path / "o.html", template)
        assert len(fake_run) == 1  # the second was refused before it was run

    def test_template_without_the_slot(self, tmp_path, fake_run):
        t = tmp_path / "bare.html"
        t.write_text("<html></html>")
        with pytest.raises(ValueError, match="no embedded-scenarios slot"):
            em.build_with_scenarios([], tmp_path / "o.html", t)

    def test_no_scenarios_leaves_an_empty_list(self, tmp_path, template, fake_run):
        html = em.build_with_scenarios([], tmp_path / "o.html", template)
        assert "const PRELOADED=[];" in html

    def test_unwritable_destination_falls_back_to_the_temp_folder(self, tmp_path, template, fake_run, capsys):
        out = em.build_with_scenarios([], tmp_path / "no_such_dir" / "o.html", template)
        assert "const PRELOADED=[];" in out
        assert "written" in capsys.readouterr().out


class TestRunFile:
    def test_unknown_format(self, tmp_path):
        f = tmp_path / "a.csv"
        f.write_text("x")
        with pytest.raises(ValueError, match="format must be one of"):
            em.run_file(str(f), "Excel macro")

    def test_rows_are_flattened_to_lists(self, tmp_path, monkeypatch):
        f = tmp_path / "a.csv"
        f.write_bytes(b"data")
        seen: dict[str, object] = {}

        def fake(body, gauges, scn_id, fmt, name):
            seen.update(body=body, gauges=gauges, scn_id=scn_id, fmt=fmt, name=name)
            return [{"pu": "P", "gauge": "1", "ewr": "E", "fr": 0.1, "et": None, "ey": 3, "ny": 25.0}], {"s": 1}

        monkeypatch.setattr(em, "run_scenario", fake)
        rows, stats = em.run_file(str(f), "Standard time-series", gauges=["1"])
        assert rows == [["P", "1", "E", 0.1, None, 3, 25.0]] and stats == {"s": 1}  # in server.VAL_KEYS order (the page's VALK)
        assert seen == {"body": b"data", "gauges": ["1"], "scn_id": None, "fmt": "Standard time-series", "name": "a.csv"}

    def test_review_gauges_are_the_distinct_sorted_gauges_of_the_results(self):
        g = em.review_gauges()
        assert g == sorted(set(g)) and len(g) == 156
        assert "A4261002" in g and "409025" in g

    def test_every_review_gauge_has_ewrs_in_the_parameter_table(self):
        from py_ewr import data_inputs

        t = data_inputs.get_EWR_table(None)
        known = set(t.Gauge.astype(str)) | set(t.Multigauge.dropna().astype(str)) | set(t.WeirpoolGauge.dropna().astype(str))
        assert [g for g in em.review_gauges() if em.REKEY.get(g, g) not in known] == []


def test_committed_page_has_exactly_one_embedded_scenarios_slot():
    assert (ROOT / "ewr_analysis_dashboard.html").read_text().count(em.PLACEHOLDER) == 1


def test_the_page_stores_upload_rows_in_the_servers_key_order():
    """The page's VALK (how it reads a scenario's vals) is server.VAL_KEYS: a new field has to be added to both."""
    import re

    import server

    text = (Path(__file__).resolve().parents[1] / "src/dashboard_template.html").read_text()
    valk = re.search(r"const VALK=(\[[^\]]*\])", text)
    assert valk and json.loads(valk.group(1)) == list(server.VAL_KEYS)
