"""Unit tests for every stage of src/build_dashboard.py.

The stages are exercised on the small hand-built dataset in tests/fixtures/mini (same layout as data/), so every expected value below
was worked out by hand from the CSV rows rather than copied from the code's output.

Scenario index = policy index * 7 + climate index (policy-major, 7 climates):
  Without Development / Hotter and Much Drier 2.0 C -> 0 * 7 + 6 = 6
  Pre Basin Plan / Historical Climate                -> 1 * 7 + 0 = 7
  Pre Basin Plan / Warmer and Wetter 1.5 C           -> 1 * 7 + 1 = 8
  Basin plan 2024 / Historical Climate               -> 2 * 7 + 0 = 14
"""

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

import build_dashboard as bd

MINI = Path(__file__).parent / "fixtures" / "mini"
NS = 28


def blank() -> list[None]:
    return [None] * NS


def with_values(**by_index: float) -> list[float | None]:
    out: list[float | None] = [None] * NS
    for idx, value in by_index.items():
        out[int(idx.lstrip("i"))] = value
    return out


# ---------------------------------------------------------------------------------------------------------------------
# small pure helpers
# ---------------------------------------------------------------------------------------------------------------------
class TestHelpers:
    def test_norm_keeps_lower_case_alphanumerics_only(self):
        assert bd.norm("S.D.L. B") == "sdlb"
        assert bd.norm("Murray-Darling  Basin 2") == "murraydarlingbasin2"
        assert bd.norm("") == ""
        assert bd.norm("---") == ""

    def test_scenario_grid_is_policy_major_and_complete(self):
        assert bd.NS == len(bd.POLICIES) * len(bd.CLIMATES) == NS
        assert bd.scenario_index("Without Development", "Historical Climate") == 0
        assert bd.scenario_index("Without Development", "Hotter and Much Drier 2.0 C") == 6
        assert bd.scenario_index("Pre Basin Plan", "Historical Climate") == 7
        assert bd.scenario_index("Basin plan 2024", "Hotter and Much Drier 2.0 C") == 20
        assert bd.scenario_index("Basin plan full implementation", "Hotter and Much Drier 2.0 C") == 27
        every = sorted(bd.scenario_index(p, c) for p in bd.POLICIES for c in bd.CLIMATES)
        assert every == list(range(NS))  # a bijection onto 0..27: no two scenarios share a slot

    def test_unknown_scenario_names_are_rejected_not_defaulted(self):
        with pytest.raises(ValueError):
            bd.scenario_index("Basin plan 2025", "Historical Climate")
        with pytest.raises(ValueError):
            bd.scenario_index("Pre Basin Plan", "Colder and Drier")

    def test_fix_theme(self):
        assert bd.fix_theme("Other species") == "Other Species"
        assert bd.fix_theme("Native Fish") == "Native Fish"

    def test_rekey_and_proxy_tables(self):
        assert bd.REKEY == {"A4260527": "A4260524"}
        assert set(bd.PROXY_LOC) == set(bd.PROXY_NAME) == {"A4261002"}


# ---------------------------------------------------------------------------------------------------------------------
# variant-conflict detection
# ---------------------------------------------------------------------------------------------------------------------
class TestVariantConflicts:
    COLS = ["g", "s", "FR"]

    def test_identical_values_are_not_conflicts(self):
        df = pd.DataFrame([("a", "x", 0.5), ("a", "x", 0.5)], columns=self.COLS)
        assert bd.detect_variant_conflicts(df, ["g", "s"], "FR") == []

    def test_distinct_values_are_reported_sorted(self):
        df = pd.DataFrame([("a", "x", 0.9), ("a", "x", 0.1), ("a", "x", 0.9), ("b", "x", 1.0)], columns=self.COLS)
        assert bd.detect_variant_conflicts(df, ["g", "s"], "FR") == [{"g": "a", "s": "x", "values": [0.1, 0.9]}]

    def test_nulls_are_ignored_in_the_comparison(self):
        df = pd.DataFrame([("a", "x", 0.5), ("a", "x", np.nan)], columns=self.COLS)
        assert bd.detect_variant_conflicts(df, ["g", "s"], "FR") == []

    def test_all_null_group_and_empty_frame(self):
        df = pd.DataFrame([("a", "x", np.nan)], columns=self.COLS)
        assert bd.detect_variant_conflicts(df, ["g", "s"], "FR") == []
        assert bd.detect_variant_conflicts(df.iloc[:0], ["g", "s"], "FR") == []

    def test_single_key_column(self):
        df = pd.DataFrame([("a", "x", 1.0), ("a", "y", 2.0)], columns=self.COLS)
        assert bd.detect_variant_conflicts(df, ["g"], "FR") == [{"g": "a", "values": [1.0, 2.0]}]

    def test_report_prints_counts_and_examples(self, capsys):
        cols = bd.KEY + ["Policy", "ClimateModel"]
        base = ("PU", "SDL", "1", "E", "Pre Basin Plan", "Historical Climate")
        fr = pd.DataFrame([(*base, 0.4), (*base, 0.6)], columns=cols + ["FR"])
        et = pd.DataFrame([(*base, 0.2), (*base, 0.2)], columns=cols + ["ETP"])
        bd.report_variant_conflicts(fr, et)
        out = capsys.readouterr().out
        assert "FR: 1 variant conflict groups" in out
        assert "ETP: 0 variant conflict groups" in out
        assert "distinct FR values=[0.4, 0.6]" in out


# ---------------------------------------------------------------------------------------------------------------------
# reading and normalising the result files
# ---------------------------------------------------------------------------------------------------------------------
class TestLoadResults:
    def test_files_are_read_with_gauge_ids_as_text_and_rekeyed(self):
        fr, et = bd.load_results(MINI)
        assert len(fr) == 7 and len(et) == 5
        assert pd.api.types.is_string_dtype(fr.Gauge) and all(isinstance(g, str) for g in fr.Gauge)
        assert "409025" in set(fr.Gauge)  # not 409025.0 / an int
        assert "A4260527" not in set(fr.Gauge) and "A4260524" in set(fr.Gauge)  # relocated lake-level gauge
        assert "A4261002" in set(et.Gauge)  # other ids untouched

    def test_etp_header_whitespace_is_stripped(self):
        _, et = bd.load_results(MINI)
        assert "MRIP_years" not in et.columns  # not selected, but the strip must not break selection of the others
        assert {"ETP", "State", "EcoObj", "Target", "scn"} <= set(et.columns)

    def test_scenario_index_column(self):
        fr, _ = bd.load_results(MINI)
        assert sorted(fr.scn.tolist()) == [6, 7, 7, 7, 7, 8, 14]

    def test_theme_tidying(self):
        fr, _ = bd.load_results(MINI)
        by_obj = dict(zip(fr.EcoObj, fr.Target, strict=False))
        assert by_obj["SA-W1"] == "Unclassified"  # missing Target
        assert by_obj["N-OT1"] == "Other Species"  # spelling variant folded
        assert by_obj["V-NF1"] == "Native Fish"

    def test_unknown_policy_in_results_stops_the_build(self):
        df = pd.DataFrame(
            {
                "Gauge": ["1"],
                "Target": ["x"],
                "Policy": ["Made-up policy"],
                "ClimateModel": ["Historical Climate"],
            }
        )
        with pytest.raises(ValueError):
            bd.normalise_results(df)

    def test_empty_input(self):
        df = pd.DataFrame({"Gauge": [], "Target": [], "Policy": [], "ClimateModel": []}, dtype=object)
        out = bd.normalise_results(df)
        assert len(out) == 0 and "scn" in out.columns


# ---------------------------------------------------------------------------------------------------------------------
# aggregation to one row per EWR
# ---------------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def mini_rows() -> dict[tuple[str, str, str, str], dict[str, Any]]:
    fr, et = bd.load_results(MINI)
    return bd.aggregate_ewr_rows(fr, et)


class TestAggregate:
    def test_one_row_per_planning_unit_sdl_gauge_ewr(self, mini_rows):
        assert sorted(mini_rows) == [
            ("PU A", "SDL A", "409025", "BF1"),
            ("PU A", "SDL A", "409025", "LF1"),
            ("PU B", "SDL B", "A4260524", "CLLMM1c"),
            ("PU C", "SDL A", "888888", "OB1"),
            ("PU D", "SDL B", "999999", "ZZ9"),
            ("PU E", "SDL B", "A4261002", "IC2"),
        ]

    def test_objective_repeats_are_averaged_per_scenario(self, mini_rows):
        r = mini_rows[("PU A", "SDL A", "409025", "LF1")]
        # FR Pre Basin Plan / Historical: mean(0.4, 0.6) = 0.5; Basin plan 2024: 0.75; Without Development / Hotter and Much Drier: 1.0
        assert r["fr"] == with_values(i6=1.0, i7=0.5, i14=0.75)
        # ETP Pre Basin Plan / Historical: mean(0.2, 0.4) = 0.3 (0.30000000000000004 before rounding); Basin plan 2024: 0.0 (zero is a value, not missing)
        assert r["et"] == with_values(i7=0.3, i14=0.0)
        assert r["ob"] == {"V-NF1", "V-NF2"}
        assert r["state"] == "VIC"

    def test_values_are_rounded_to_four_places(self, mini_rows):
        assert mini_rows[("PU C", "SDL A", "888888", "OB1")]["fr"] == with_values(i7=0.3333)  # 0.3333333333 -> 0.3333

    def test_mean_event_length_keeps_four_places_per_scenario(self):
        fr, et = bd.load_results(MINI)
        fr.loc[fr.EWR == "LF1", "AverageEventLength"] = [1.23456, 2.34567, 9.87654, np.nan]
        row = bd.aggregate_ewr_rows(fr, et)[("PU A", "SDL A", "409025", "LF1")]
        assert row["ael"] == with_values(i7=1.7901, i14=9.8765)  # mean(1.23456, 2.34567) = 1.790115

    def test_missing_scenarios_stay_none_never_zero(self, mini_rows):
        r = mini_rows[("PU B", "SDL B", "A4260524", "CLLMM1c")]
        assert r["fr"] == with_values(i8=2.0) and r["et"] == blank()
        etp_only = mini_rows[("PU A", "SDL A", "409025", "BF1")]
        assert etp_only["fr"] == blank() and etp_only["et"] == with_values(i7=0.1)

    def test_flow_regime_from_results_else_pipeline_rule_else_unmapped(self, mini_rows):
        assert mini_rows[("PU A", "SDL A", "409025", "LF1")]["rg"] == {"Large fresh"}  # given by the FR file
        assert mini_rows[("PU A", "SDL A", "409025", "BF1")]["rg"] == {"Baseflow"}  # ETP-only: regimes_for("BF1", "VIC")
        assert mini_rows[("PU E", "SDL B", "A4261002", "IC2")]["rg"] == {"Small fresh"}  # SA rule
        assert mini_rows[("PU D", "SDL B", "999999", "ZZ9")]["rg"] == {"Unmapped"}  # no regime anywhere

    def test_every_row_has_one_slot_per_scenario(self, mini_rows):
        for r in mini_rows.values():
            assert len(r["fr"]) == len(r["et"]) == len(r["ey"]) == NS

    def test_achieved_years_and_record_length(self, mini_rows):
        r = mini_rows[("PU A", "SDL A", "409025", "LF1")]
        assert r["ey"] == with_values(i6=50.0, i7=25.5, i14=38.0)  # mean(20, 31) for Pre Basin Plan / Historical
        assert r["ny"] == 100.0  # 36,525 days / 365.25
        assert mini_rows[("PU B", "SDL B", "A4260524", "CLLMM1c")]["ny"] == 100.0  # 36,524 days: 99.997 rounds to 100.0
        assert mini_rows[("PU D", "SDL B", "999999", "ZZ9")]["ny"] is None  # no TotalDays

    def test_empty_results_give_no_rows(self):
        cols = bd.KEY + ["EcoObj", "Target", "FlowRegime", "Policy", "ClimateModel", "FR", "ETP", "State", "scn"]
        empty = pd.DataFrame({c: [] for c in cols})
        assert bd.aggregate_ewr_rows(empty, empty) == {}


# ---------------------------------------------------------------------------------------------------------------------
# lookup lists, themes, the ROWS table
# ---------------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def mini_ref() -> pd.DataFrame:
    return pd.read_csv(MINI / "data/reference/objective_reference.csv", dtype=str).drop_duplicates("EcoObj").set_index("EcoObj")


class TestRowTable:
    def test_objectives_ordered_by_prefix_then_code(self, mini_rows):
        # prefixes: N-OT1 -> N, SA-W1/SA-X1 -> SA, V-NF1..3 -> V
        assert bd.objectives_of(mini_rows) == ["N-OT1", "SA-W1", "SA-X1", "V-NF1", "V-NF2", "V-NF3"]

    def test_themes_come_from_the_results_then_the_reference(self, mini_rows, mini_ref):
        fr, et = bd.load_results(MINI)
        themes = bd.objective_themes(fr, et, bd.objectives_of(mini_rows), mini_ref)
        assert themes == {
            "V-NF1": "Native Fish",
            "V-NF2": "Native Fish",
            "V-NF3": "Native Fish",
            "SA-X1": "Waterbirds",
            "N-OT1": "Other Species",
            # The results carry no Target for SA-W1 and normalise_results has already turned that into "Unclassified", so the
            # objective_reference.csv value ("Ecosystem Functions") is never consulted: see the PR notes on the unreachable fallback.
            "SA-W1": "Unclassified",
        }

    def test_reference_theme_is_used_for_an_objective_missing_from_the_results(self, mini_ref):
        empty = pd.DataFrame({"EcoObj": [], "Target": []})
        themes = bd.objective_themes(empty, empty, ["SA-W1", "NOT-IN-REF"], mini_ref)
        assert themes == {"SA-W1": "Ecosystem Functions", "NOT-IN-REF": "Unclassified"}

    def test_most_common_target_wins_when_results_disagree(self, mini_ref):
        d = pd.DataFrame({"EcoObj": ["X1", "X1", "X1"], "Target": ["Waterbirds", "Native Fish", "Native Fish"]})
        assert bd.objective_themes(d, d.iloc[:0], ["X1"], mini_ref) == {"X1": "Native Fish"}

    def test_table_and_lookups(self, mini_rows, mini_ref):
        fr, et = bd.load_results(MINI)
        themes = bd.objective_themes(fr, et, bd.objectives_of(mini_rows), mini_ref)
        table, lk = bd.build_row_table(mini_rows, themes)
        assert lk["pus"] == ["PU A", "PU B", "PU C", "PU D", "PU E"]
        assert lk["sdls"] == ["SDL A", "SDL B"]
        assert lk["gids"] == ["409025", "888888", "999999", "A4260524", "A4261002"]
        assert lk["objs"] == ["N-OT1", "SA-W1", "SA-X1", "V-NF1", "V-NF2", "V-NF3"]
        assert lk["regimes"] == ["Baseflow", "Large fresh", "Overbank", "Small fresh", "Unmapped", "lower lakes"]
        assert lk["themes"] == [
            "Flows & Connectivity",
            "Ecosystem Functions",
            "Waterbirds",
            "Native Fish",
            "Native Vegetation",
            "Other Species",
            "Unclassified",
        ]
        assert lk["objTheme"] == [5, 6, 2, 3, 3, 3]
        # columns 0-7 are fixed (the page and performance.js index them)
        assert [r[:8] for r in table] == [
            [0, 0, 0, "BF1", [5], [0], blank(), with_values(i7=0.1)],
            [0, 0, 0, "LF1", [3, 4], [1], with_values(i6=1.0, i7=0.5, i14=0.75), with_values(i7=0.3, i14=0.0)],
            [1, 1, 3, "CLLMM1c", [1], [5], with_values(i8=2.0), blank()],
            [2, 0, 1, "OB1", [0], [2], with_values(i7=0.3333), blank()],
            [3, 1, 2, "ZZ9", [3], [4], with_values(i7=0.9), blank()],
            [4, 1, 4, "IC2", [2], [3], blank(), with_values(i7=0.5)],
        ]
        # 8: {T, ny, dur, mrip} (no parameters passed: unknown); 9: achieved years per scenario (EventYears, mean over objective repeats)
        none = {"T": None, "dur": None, "mrip": None}
        assert [r[8] for r in table] == [{**none, "ny": ny} for ny in (None, 100.0, 100.0, 100.0, None, None)]
        assert all(len(r) == 13 and r[10] is None and r[12] is None for r in table)  # 10, 12: gap severity and drought windows, uploads only
        assert all(r[11] == blank() for r in table)  # 11: duration ratio; no parameters passed, so no required duration
        assert [r[9] for r in table] == [
            blank(),
            with_values(i6=50.0, i7=25.5, i14=38.0),
            with_values(i8=99.0),
            with_values(i7=10.0),
            with_values(i7=45.0),
            blank(),
        ]

    def test_parameters_are_joined_on_gauge_code_and_planning_unit(self, mini_rows):
        sheet = pd.DataFrame(
            {
                "Gauge": ["409025", "409025", "409025"],
                "Code": ["LF1", "LF1", "LF1"],
                "PlanningUnitName": ["PU A", "PU A", "PU Z"],
                "TargetFrequency": ["70", "10", "5"],
                "Duration": [5, 9, 9],
                "MaxInter-event": [3.0, 1.0, 1.0],
                "FlowLevelVolume": ["F", "F", "F"],
            }
        )
        params = bd.ewr_params(sheet)
        assert params[("409025", "LF1", "PU A")] == {"T": 70.0, "dur": 5, "mrip": 3.0, "kind": "F"}  # the first row of a repeated key
        assert bd.dr_approx([4.0, None], {"dur": 2, "kind": "V"}) == [None, None]  # volume and level EWRs: no duration ratio
        table, _ = bd.build_row_table(mini_rows, {o: "Native Fish" for o in bd.objectives_of(mini_rows)}, params)
        lf1 = next(r for r in table if r[3] == "LF1")
        assert lf1[8] == {"T": 70.0, "dur": 5, "mrip": 3.0, "ny": 100.0}
        # 11: mean event length / required duration, capped at 2: PBP Historical mean(6, 10) / 5 = 1.6; BP2024 12.5 / 5 -> 2; WOD missing
        assert lf1[11] == with_values(i7=1.6, i14=2.0)
        assert all(r[8]["T"] is None for r in table if r[3] != "LF1")
        assert bd.ewr_params(None) == {}

    def test_new_theme_names_are_appended_after_the_fixed_ones_in_order(self, mini_rows):
        themes = {o: "Zebra Theme" if o == "V-NF1" else "Aardvark Theme" for o in bd.objectives_of(mini_rows)}
        _, lk = bd.build_row_table(mini_rows, themes)
        assert lk["themes"][7:] == ["Aardvark Theme", "Zebra Theme"]
        assert lk["objTheme"][3] == lk["themes"].index("Zebra Theme")

    def test_empty_rows(self):
        table, lk = bd.build_row_table({}, {})
        assert table == []
        assert lk["pus"] == lk["sdls"] == lk["gids"] == lk["objs"] == lk["regimes"] == lk["objTheme"] == []
        assert len(lk["themes"]) == 7


# ---------------------------------------------------------------------------------------------------------------------
# gauges, constraint sites, polygons
# ---------------------------------------------------------------------------------------------------------------------
class TestMapData:
    GIDS = ["409025", "888888", "999999", "A4260524", "A4261002"]

    def test_station_list_drops_rows_without_coordinates(self):
        bom = bd.load_gauge_stations(MINI)
        assert "777777" not in bom.index
        assert bom.at["409025", "station_latitude"] == -36.0

    def test_duplicate_station_numbers_keep_the_first(self, tmp_path):
        (tmp_path / "data/map").mkdir(parents=True)
        (tmp_path / "data/map/bom_gauge_stations.csv").write_text(
            "station_name,station_no,station_latitude,station_longitude\nFirst,1,-30.0,140.0\nSecond,1,-31.0,141.0\n"
        )
        bom = bd.load_gauge_stations(tmp_path)
        assert len(bom) == 1 and bom.at["1", "station_name"] == "First"

    def test_gauges_plotted_unplotted_and_proxy_offset(self):
        gauges, unplotted = bd.build_gauges(self.GIDS, bd.load_gauge_stations(MINI))
        assert unplotted == ["888888", "999999"]  # 888888: latitude +10 is outside Australia; 999999: no station
        assert gauges == [
            {"i": 0, "id": "409025", "name": "Murray at Yarrawonga", "lat": -36.0, "lon": 146.0},
            {"i": 3, "id": "A4260524", "name": "Milang", "lat": -35.4, "lon": 138.97},
            # virtual barrage gauge: beside A4260527 (-35.6, 138.9) shifted by (+0.012, -0.012)
            {"i": 4, "id": "A4261002", "name": bd.PROXY_NAME["A4261002"], "lat": -35.588, "lon": 138.888},
        ]

    def test_index_i_points_back_into_the_gauge_list(self):
        gauges, _ = bd.build_gauges(self.GIDS, bd.load_gauge_stations(MINI))
        assert all(self.GIDS[g["i"]] == g["id"] for g in gauges)

    @pytest.mark.parametrize(
        ("lat", "lon", "plotted"),
        [
            (-44.9, 140.0, True),
            (-45.0, 140.0, False),
            (-9.1, 140.0, True),
            (-9.0, 140.0, False),
            (-30.0, 110.0, False),
            (-30.0, 154.9, True),
            (-30.0, 155.0, False),
        ],
    )
    def test_australia_bounding_box_is_exclusive(self, lat, lon, plotted):
        bom = pd.DataFrame({"station_latitude": [lat], "station_longitude": [lon], "station_name": ["n"]}, index=pd.Index(["g"], name="station_no"))
        gauges, unplotted = bd.build_gauges(["g"], bom)
        assert bool(gauges) is plotted and (unplotted == []) is plotted

    def test_constraint_sites(self):
        assert bd.load_constraints(MINI) == [
            {"name": "Test constraint", "river": "Murray River", "lat": -36.1, "lon": 147.0, "site": "", "cur": "25,000", "rel": "30,000"},
            {"name": "Second constraint", "river": "Goulburn River", "lat": -37.0, "lon": 145.5, "site": "Site X", "cur": "10,000", "rel": "15,000"},
        ]

    def test_polygons_are_matched_to_sdl_units_by_normalised_name(self):
        gj = json.loads((MINI / "data/map/sdl_resource_units.geojson").read_text())
        out = bd.build_polygons(gj, ["SDL A", "SDL B"])
        props = [f["properties"] for f in out["features"]]
        assert props == [
            {"name": "SDL A", "id": "S1", "state": "VIC", "sdl": 0},
            {"name": "S.D.L. B", "id": "S2", "state": "SA", "sdl": 1},  # "S.D.L. B" and "SDL B" normalise to the same key
            {"name": "SDL Z", "id": "S3", "state": "NSW", "sdl": -1},  # in the boundary file but not in the results
        ]

    def test_simplified_geometry_keeps_closed_rings_and_bounds(self):
        gj = json.loads((MINI / "data/map/sdl_resource_units.geojson").read_text())
        ring = bd.build_polygons(gj, ["SDL A"])["features"][0]["geometry"]["coordinates"][0]
        assert ring[0] == ring[-1]
        assert {tuple(p) for p in ring} == {(145.0, -37.0), (147.0, -37.0), (147.0, -35.0), (145.0, -35.0)}


# ---------------------------------------------------------------------------------------------------------------------
# META and HTML rendering
# ---------------------------------------------------------------------------------------------------------------------
class TestMetaAndRender:
    def test_meta(self, mini_rows, mini_ref):
        fr, et = bd.load_results(MINI)
        table, lk = bd.build_row_table(mini_rows, bd.objective_themes(fr, et, bd.objectives_of(mini_rows), mini_ref))
        meta = bd.build_meta(lk, mini_ref)
        assert meta["policies"] == bd.POLICIES and meta["climates"] == bd.CLIMATES
        assert meta["gauges"] == lk["gids"]
        assert meta["objDesc"] == ["", "Lower lakes water level", "", "Maintain native fish populations", "", "Fish objective three"]
        assert len(meta["objDesc"]) == len(meta["objTheme"]) == len(meta["objs"])

    def test_render_replaces_every_slot_with_compact_json(self):
        html = bd.render_html("a=__X__;b=__Y__;c=__X__", {"__X__": {"k": [1, 2.5, None]}, "__Y__": []})
        assert html == 'a={"k":[1,2.5,null]};b=[];c={"k":[1,2.5,null]}'

    def test_render_leaves_unknown_slots_alone(self):
        assert bd.render_html("__A__ __B__", {"__A__": 1}) == "1 __B__"

    def test_render_escapes_nothing_unexpected_in_text(self):
        assert bd.render_html("__A__", {"__A__": "naïve"}) == '"na\\u00efve"'  # ASCII-only output regardless of file encoding


# ---------------------------------------------------------------------------------------------------------------------
# the whole pipeline on the mini data set
# ---------------------------------------------------------------------------------------------------------------------
class TestBuildPayload:
    def test_payload_end_to_end(self, capsys):
        p = bd.build_payload(MINI)
        assert list(p) == ["__DATA__", "__GAUGES__", "__ROWS__", "__RIVERS__", "__CONSTRAINTS__", "__META__", "__EWRIDX__", "__SCENARIOS__"]
        assert p["__SCENARIOS__"] == []
        assert p["__EWRIDX__"]["n"] > 3000
        assert len(p["__ROWS__"]) == 6
        assert [g["id"] for g in p["__GAUGES__"]] == ["409025", "A4260524", "A4261002"]
        assert p["__RIVERS__"]["features"][0]["geometry"]["coordinates"] == [[146.0, -36.0], [146.5, -36.5]]
        out = capsys.readouterr().out
        assert "6 EWR rows; 2 SDL units; 5 planning units; 5 gauges" in out
        assert "3 gauges plotted; unplotted (in tables only): ['888888', '999999']" in out
        assert "SDL names in results with no polygon: []" in out

    def test_payload_is_strict_json(self):
        text = json.dumps(bd.build_payload(MINI), allow_nan=False)  # raises on NaN / Infinity
        assert "NaN" not in text

    def test_missing_input_file_is_an_error(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            bd.build_payload(tmp_path)
