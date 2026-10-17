"""Fidelity of the data embedded in ewr_analysis_dashboard.html (what the map, tables and charts read) to the source files in data/.

The page embeds six constants in one statement: DATA (SDL polygons), GAUGES, ROWS (one row per EWR with FR / ETP for every scenario),
META (lookup lists), RIVERS and CONSTRAINTS, plus PRELOADED (scenarios embedded by embed_scenarios.py). The tests parse them back out of
the committed HTML and check them against (a) structural invariants the JavaScript relies on, (b) an independent recomputation from the
CSVs, and (c) a stored baseline of the aggregates the page displays.
"""

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from snapshots import assert_snapshot

import build_dashboard as bd

ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "ewr_analysis_dashboard.html"
TEMPLATE_PATH = ROOT / "src/dashboard_template.html"
SLOTS = ["__DATA__", "__GAUGES__", "__ROWS__", "__RIVERS__", "__CONSTRAINTS__", "__META__", "__SCENARIOS__"]
NS = 28
# Australia: the bounds used by build_dashboard.build_gauges
LAT, LON = (-45.0, -9.0), (110.0, 155.0)


def parse_embedded(html: str) -> dict[str, Any]:
    """The values of `const DATA=...,GAUGES=...,ROWS=...,META=...,RIVERS=...,CONSTRAINTS=...;` as Python objects."""
    dec = json.JSONDecoder()
    pos = html.index("const DATA=") + len("const ")
    out: dict[str, Any] = {}
    while True:
        m = re.compile(r"(\w+)=").match(html, pos)
        assert m, f"unexpected text at {html[pos : pos + 40]!r}"
        out[m.group(1)], pos = dec.raw_decode(html, m.end())
        sep = re.compile(r",\s*").match(html, pos)
        if not sep:
            break
        pos = sep.end()
    assert html[pos] == ";"
    return out


@pytest.fixture(scope="module")
def html() -> str:
    return HTML_PATH.read_text()


@pytest.fixture(scope="module")
def page(html) -> dict[str, Any]:
    return parse_embedded(html)


@pytest.fixture(scope="module")
def decoded(page) -> list[dict[str, Any]]:
    """ROWS with the lookup indices resolved to names."""
    m = page["META"]
    return [
        {
            "key": (m["pus"][r[0]], m["sdls"][r[1]], m["gauges"][r[2]], r[3]),
            "objs": [m["objs"][i] for i in r[4]],
            "regimes": [m["regimes"][i] for i in r[5]],
            "fr": r[6],
            "et": r[7],
        }
        for r in page["ROWS"]
    ]


# ---------------------------------------------------------------------------------------------------------------------
# the template and the built page
# ---------------------------------------------------------------------------------------------------------------------
class TestTemplateAndBuild:
    def test_template_has_each_data_slot_exactly_once(self):
        t = TEMPLATE_PATH.read_text()
        assert {s: t.count(s) for s in SLOTS} == dict.fromkeys(SLOTS, 1)

    def test_built_page_has_no_unfilled_slots(self, html):
        assert not [s for s in SLOTS if s != "__SCENARIOS__" and s in html]
        assert "const PRELOADED=[];" in html  # the committed page embeds no user scenarios

    def test_embedded_constants_are_all_present(self, page):
        assert list(page) == ["DATA", "GAUGES", "ROWS", "META", "RIVERS", "CONSTRAINTS"]

    def test_embedded_json_has_no_nan_or_infinity(self, html):
        i = html.index("const DATA=")
        j = html.index("const PRELOADED")
        blob = html[i:j]
        assert not re.search(r"\b(NaN|Infinity)\b", blob)

    @pytest.mark.slow
    def test_rebuilding_from_data_reproduces_the_committed_page_byte_for_byte(self):
        built = bd.render_html(TEMPLATE_PATH.read_text(), bd.build_payload())
        assert built == HTML_PATH.read_text(), "ewr_analysis_dashboard.html is stale: run `python3 src/build_dashboard.py` and commit the result"


# ---------------------------------------------------------------------------------------------------------------------
# ROWS / META structure
# ---------------------------------------------------------------------------------------------------------------------
class TestRowsAndMeta:
    def test_meta_lookup_lists(self, page):
        m = page["META"]
        assert m["policies"] == bd.POLICIES and m["climates"] == bd.CLIMATES
        assert set(m) == {"policies", "climates", "pus", "sdls", "gauges", "themes", "regimes", "objs", "objTheme", "objDesc", "ewrThr"}
        for key in ("pus", "sdls", "gauges", "objs", "regimes", "themes"):
            assert m[key] == sorted(m[key]) or key == "themes" or key == "objs"  # sorted lookups (themes: fixed order; objs: by prefix)
            assert len(set(m[key])) == len(m[key]), f"duplicates in {key}"
        assert len(m["objs"]) == len(m["objTheme"]) == len(m["objDesc"])
        assert all(0 <= t < len(m["themes"]) for t in m["objTheme"])
        assert m["themes"][:7] == [
            "Flows & Connectivity",
            "Ecosystem Functions",
            "Waterbirds",
            "Native Fish",
            "Native Vegetation",
            "Other Species",
            "Unclassified",
        ]

    def test_headline_counts(self, page):
        m = page["META"]
        assert (len(page["ROWS"]), len(m["sdls"]), len(m["pus"]), len(m["gauges"]), len(page["GAUGES"])) == (2277, 18, 150, 155, 155)

    def test_row_shape_and_index_bounds(self, page):
        m = page["META"]
        for r in page["ROWS"]:
            assert len(r) == 8
            pu, sdl, gauge, ewr, objs, regimes, fr, et = r
            assert 0 <= pu < len(m["pus"]) and 0 <= sdl < len(m["sdls"]) and 0 <= gauge < len(m["gauges"])
            assert isinstance(ewr, str) and ewr
            assert objs == sorted(set(objs)) and all(0 <= i < len(m["objs"]) for i in objs)
            assert regimes and regimes == sorted(set(regimes)) and all(0 <= i < len(m["regimes"]) for i in regimes)
            assert len(fr) == len(et) == NS

    def test_rows_are_sorted_and_unique(self, decoded):
        keys = [r["key"] for r in decoded]
        assert keys == sorted(keys) and len(set(keys)) == len(keys)

    def test_every_lookup_entry_is_used(self, page):
        m = page["META"]
        assert {r[0] for r in page["ROWS"]} == set(range(len(m["pus"])))
        assert {r[1] for r in page["ROWS"]} == set(range(len(m["sdls"])))
        assert {r[2] for r in page["ROWS"]} == set(range(len(m["gauges"])))
        assert {i for r in page["ROWS"] for i in r[4]} == set(range(len(m["objs"])))
        assert {i for r in page["ROWS"] for i in r[5]} == set(range(len(m["regimes"])))

    def test_every_row_has_at_least_one_value(self, page):
        assert all(any(v is not None for v in r[6]) or any(v is not None for v in r[7]) for r in page["ROWS"])

    def test_values_are_finite_non_negative_and_rounded_to_four_places(self, page):
        for r in page["ROWS"]:
            for v in r[6] + r[7]:
                assert v is None or (isinstance(v, (int, float)) and v >= 0 and round(v, 4) == v)

    def test_unit_scales(self, page):
        """FR is a ratio (about 1 when the target is just met), ETP a proportion of the record: neither is a percentage."""
        fr = [v for r in page["ROWS"] for v in r[6] if v is not None]
        et = [v for r in page["ROWS"] for v in r[7] if v is not None]
        assert 0.5 < sum(fr) / len(fr) < 1.5 and max(fr) < 10
        assert 0.05 < sum(et) / len(et) < 0.5 and max(et) < 1.5

    def test_etp_above_one_only_where_the_source_file_has_it(self, decoded):
        over = {(r["key"][2], r["key"][3]) for r in decoded if any(v is not None and v > 1 for v in r["et"])}
        assert over <= {("422001", "LF1_P"), ("422001", "OB2"), ("422001", "OB3"), ("422025", "LF1_P")}

    def test_only_the_ten_modelled_scenarios_carry_values(self, page):
        used = {i for r in page["ROWS"] for i in range(NS) if r[6][i] is not None or r[7][i] is not None}
        assert len(used) == 10
        assert bd.scenario_index("Pre Basin Plan", "Historical Climate") in used

    def test_no_duplicate_gauge_aliases(self, page):
        assert "A4260527" not in page["META"]["gauges"]  # relocated to A4260524 by REKEY
        assert "A4260524" in page["META"]["gauges"]

    def test_unmapped_flow_regime_is_rare(self, decoded):
        unmapped = [r for r in decoded if r["regimes"] == ["Unmapped"]]
        assert len(unmapped) / len(decoded) < 0.1


# ---------------------------------------------------------------------------------------------------------------------
# independent recomputation from the CSVs
# ---------------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def expected() -> dict[str, dict[tuple[str, str, str, str], dict[int, float]]]:
    """Mean of each metric per (planning unit, SDL unit, gauge, EWR, scenario), computed with pivot_table rather than the build's loops."""
    fr = pd.read_csv(ROOT / "data/results/frequency_ratio.csv", dtype={"Gauge": str}, usecols=[*bd.KEY, "Policy", "ClimateModel", "FR"])
    et = pd.read_csv(ROOT / "data/results/etp.csv", dtype={"Gauge": str})
    et.columns = et.columns.str.strip()
    et = et[[*bd.KEY, "Policy", "ClimateModel", "ETP"]]
    et["ETP"] = et.ETP.clip(upper=1)  # ETP is a time proportion: source values above 1 count as 1
    out: dict[str, dict] = {}
    for name, d, col in (("fr", fr, "FR"), ("et", et, "ETP")):
        d = d.copy()
        d["Gauge"] = d["Gauge"].replace(bd.REKEY)
        d["scn"] = [bd.scenario_index(p, c) for p, c in zip(d.Policy, d.ClimateModel, strict=False)]
        piv = d.pivot_table(index=bd.KEY, columns="scn", values=col, aggfunc="mean")
        cells: dict[tuple, dict[int, float]] = defaultdict(dict)
        for key, row in piv.iterrows():
            for scn, v in row.items():
                if pd.notna(v):
                    cells[key][int(scn)] = float(v)
        out[name] = dict(cells)
    return out


class TestRowsMatchSourceData:
    @pytest.mark.parametrize("metric", ["fr", "et"])
    def test_every_cell_equals_the_mean_of_the_source_rows(self, metric, decoded, expected):
        exp = expected[metric]
        mismatches = []
        for r in decoded:
            want = exp.get(r["key"], {})
            for i, got in enumerate(r[metric]):
                if i in want:
                    if got is None or abs(got - want[i]) > 5.0001e-5:  # the page stores the mean rounded to 4 places
                        mismatches.append((r["key"], i, got, want[i]))
                elif got is not None:
                    mismatches.append((r["key"], i, got, None))
        assert not mismatches, mismatches[:5]

    @pytest.mark.parametrize("metric", ["fr", "et"])
    def test_no_source_group_is_missing_from_the_page(self, metric, decoded, expected):
        page_cells = {(r["key"], i) for r in decoded for i, v in enumerate(r[metric]) if v is not None}
        source_cells = {(k, i) for k, cells in expected[metric].items() for i in cells}
        assert page_cells == source_cells
        assert len(page_cells) > 15000

    def test_objective_lists_match_the_source_files(self, decoded):
        fr = pd.read_csv(ROOT / "data/results/frequency_ratio.csv", dtype={"Gauge": str}, usecols=[*bd.KEY, "EcoObj"])
        et = pd.read_csv(ROOT / "data/results/etp.csv", dtype={"Gauge": str})
        et.columns = et.columns.str.strip()
        objs: dict[tuple, set[str]] = defaultdict(set)
        for d in (fr, et):
            d = d.dropna(subset=["EcoObj"]).copy()
            d["Gauge"] = d["Gauge"].replace(bd.REKEY)
            for k, o in zip(zip(d.PlanningUnitName, d.SWSDLName, d.Gauge, d.EWR, strict=False), d.EcoObj, strict=False):
                objs[k].add(o)
        assert {r["key"]: set(r["objs"]) for r in decoded} == {k: v for k, v in objs.items()} | {r["key"]: set() for r in decoded if r["key"] not in objs}


# ---------------------------------------------------------------------------------------------------------------------
# map layers
# ---------------------------------------------------------------------------------------------------------------------
def in_australia(lon: float, lat: float) -> bool:
    return LON[0] < lon < LON[1] and LAT[0] < lat < LAT[1]


def ring_area(ring: list[list[float]]) -> float:
    """Shoelace area (degrees squared); non-zero for a real polygon."""
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:], strict=False))) / 2


class TestMapLayers:
    def test_gauges(self, page):
        m = page["META"]
        ids = [g["id"] for g in page["GAUGES"]]
        assert len(set(ids)) == len(ids) == 155
        for g in page["GAUGES"]:
            assert set(g) == {"i", "id", "name", "lat", "lon"}
            assert m["gauges"][g["i"]] == g["id"]  # `i` indexes META.gauges
            assert in_australia(g["lon"], g["lat"]) and isinstance(g["name"], str) and g["name"]
            assert round(g["lat"], 5) == g["lat"] and round(g["lon"], 5) == g["lon"]

    def test_every_results_gauge_is_plotted(self, page):
        assert sorted(g["id"] for g in page["GAUGES"]) == page["META"]["gauges"]

    def test_virtual_barrage_gauge_sits_beside_its_reference_station(self, page):
        by_id = {g["id"]: g for g in page["GAUGES"]}
        assert "A4261002" in by_id
        bom = pd.read_csv(ROOT / "data/map/bom_gauge_stations.csv", dtype=str).set_index("station_no")
        ref = bom.loc[bd.PROXY_LOC["A4261002"][0]]
        _, dlat, dlon = bd.PROXY_LOC["A4261002"]
        assert by_id["A4261002"]["lat"] == pytest.approx(float(ref.station_latitude) + dlat, abs=1e-5)
        assert by_id["A4261002"]["lon"] == pytest.approx(float(ref.station_longitude) + dlon, abs=1e-5)

    def test_sdl_polygons(self, page):
        n_sdl = len(page["META"]["sdls"])
        feats = page["DATA"]["features"]
        assert page["DATA"]["type"] == "FeatureCollection" and feats
        matched = []
        for f in feats:
            p = f["properties"]
            assert set(p) == {"name", "id", "state", "sdl"} and -1 <= p["sdl"] < n_sdl
            if p["sdl"] >= 0:
                matched.append(p["sdl"])
            g = f["geometry"]
            assert g["type"] in ("Polygon", "MultiPolygon")
            polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
            for poly in polys:
                for ring in poly:
                    assert len(ring) >= 4 and ring[0] == ring[-1]  # closed
                    assert all(in_australia(x, y) for x, y in ring)
                assert ring_area(poly[0]) > 0
        assert sorted(matched) == list(range(n_sdl))  # each SDL unit in the results has exactly one polygon feature

    def test_sdl_polygon_names_match_the_results(self, page):
        sdls = page["META"]["sdls"]
        for f in page["DATA"]["features"]:
            p = f["properties"]
            if p["sdl"] >= 0:
                assert bd.norm(sdls[p["sdl"]]) == bd.norm(p["name"])

    def test_rivers(self, page):
        feats = page["RIVERS"]["features"]
        assert len(feats) > 100
        for f in feats:
            assert set(f["properties"]) == {"o", "q"} and f["properties"]["o"] >= 0 and f["properties"]["q"] >= 0
            g = f["geometry"]
            lines = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
            for line in lines:
                assert len(line) >= 2 and all(in_australia(x, y) for x, y in line)

    def test_constraint_sites(self, page):
        sites = page["CONSTRAINTS"]
        assert len(sites) == len(pd.read_csv(ROOT / "data/map/constraint_sites.csv"))
        for s in sites:
            assert set(s) == {"name", "river", "lat", "lon", "site", "cur", "rel"}
            assert s["name"] and in_australia(s["lon"], s["lat"])


# ---------------------------------------------------------------------------------------------------------------------
# baseline of the aggregates the page shows
# ---------------------------------------------------------------------------------------------------------------------
def summary(page: dict[str, Any]) -> dict[str, Any]:
    """Plain mean of the per-EWR values for each SDL unit and scenario (the 'SDL unit' map colouring) with the number of EWRs behind each."""
    m = page["META"]
    acc: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0, 0.0, 0]))
    for r in page["ROWS"]:
        sdl = m["sdls"][r[1]]
        for i in range(NS):
            a = acc[sdl][i]
            if r[6][i] is not None:
                a[0] += r[6][i]
                a[1] += 1
            if r[7][i] is not None:
                a[2] += r[7][i]
                a[3] += 1
    by_sdl = {
        sdl: {
            str(i): {"fr": round(a[0] / a[1], 6) if a[1] else None, "n_fr": a[1], "etp": round(a[2] / a[3], 6) if a[3] else None, "n_etp": a[3]}
            for i, a in sorted(scn.items())
            if a[1] or a[3]
        }
        for sdl, scn in sorted(acc.items())
    }
    return {
        "counts": {
            "rows": len(page["ROWS"]),
            "sdls": len(m["sdls"]),
            "pus": len(m["pus"]),
            "gauges": len(m["gauges"]),
            "objs": len(m["objs"]),
            "regimes": len(m["regimes"]),
        },
        "themes": m["themes"],
        "regimes": m["regimes"],
        "by_sdl_scenario": by_sdl,
    }


class TestBaseline:
    def test_aggregates_match_the_stored_baseline(self, page):
        assert_snapshot("dashboard_summary", summary(page))

    def test_every_sdl_unit_has_a_value_in_every_modelled_scenario(self, page):
        s = summary(page)["by_sdl_scenario"]
        assert len(s) == 18
        n_scn = {len(v) for v in s.values()}
        assert max(n_scn) == 10


class TestEwrThresholds:
    def test_every_threshold_is_a_non_negative_number_for_a_listed_gauge_and_code(self, page):
        m = page["META"]
        listed = {(m["gauges"][r[2]], r[3]) for r in page["ROWS"]}
        for key, v in m["ewrThr"].items():
            gauge, code = key.split("|")
            assert (gauge, code) in listed and v >= 0
