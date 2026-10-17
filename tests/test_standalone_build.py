"""The built page is one self-contained file that works from file://: the data
(including scenarios run through py_ewr at build time) is embedded as inline JSON, the
libraries are inlined, and nothing in the page's own code talks to a server."""

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import build_dashboard  # noqa: E402

EXAMPLE = ROOT / "data/example_scenarios/dummy_broken_scenario_A.csv"
SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.S | re.I)


def app_scripts(html):
    """The page's own inline scripts: every <script> except the vendored libraries
    (which ship generic network code of their own, e.g. Plotly's mapbox loader, that
    the page never calls)."""
    libs = {
        build_dashboard.vendor_text(*v)
        for k, v in build_dashboard.VENDOR.items()
        if k.endswith("_JS__")
    }
    return [
        body
        for attrs, body in SCRIPT.findall(html)
        if body not in libs and "application/json" not in attrs
    ]


class StandaloneChecks:
    """Checks shared by a fresh build and the committed index.html."""

    html = ""

    def test_payload_is_embedded_and_complete(self):
        p = build_dashboard.read_payload(self.html)
        self.assertGreater(len(p["rows"]), 1000)
        self.assertGreater(len(p["data"]["features"]), 10)
        self.assertEqual(len(p["meta"]["policies"]), 4)
        for k in (
            "gauges",
            "rivers",
            "constraints",
            "scenarios",
            "flows",
            "ewrs",
            "ewryears",
            "regimes",
            "openIn",
        ):
            self.assertIn(k, p)

    def test_no_external_resources(self):
        self.assertNotRegex(self.html, r"(?i)<script\b[^>]*\bsrc\s*=")
        self.assertNotRegex(self.html, r"(?i)<link\b[^>]*\bhref\s*=")
        self.assertNotRegex(
            self.html, r"(?i)<(img|iframe)\b[^>]*\bsrc\s*=\s*[\"']?https?:"
        )
        self.assertIn("plotly.js v2.27.0", self.html)  # the libraries are inlined
        self.assertIn("Leaflet 1.9.4", self.html)

    def test_no_server_calls(self):
        for word in ("localhost", "127.0.0.1"):
            self.assertNotIn(word, self.html)
        code = "\n".join(app_scripts(self.html))
        for word in (
            "/api/",
            "server.py",
        ):  # the old local server's endpoints (Plotly has a mapbox "/api/" link)
            self.assertNotIn(word, code)
        self.assertIn("FlowEngine.create(PAYLOAD)", code)
        for call in (
            "fetch(",
            "XMLHttpRequest",
            "WebSocket",
            "EventSource",
            "sendBeacon",
            "import(",
        ):
            self.assertNotIn(call, code)
        hosts = set(re.findall(r"https?://([^/\"'`\s]+)", code))
        self.assertEqual(
            hosts, {"server.arcgisonline.com"}
        )  # background map tiles only (the page says so when offline)

    def test_every_script_closes_where_it_should(self):
        self.assertEqual(
            len(re.findall(r"(?i)<script\b", self.html)),
            len(re.findall(r"(?i)</script>", self.html)),
        )
        self.assertEqual(
            len(SCRIPT.findall(self.html)), 5
        )  # leaflet, plotly, flow engine, payload, the page
        self.assertNotIn(" ", self.html)
        self.assertNotIn(" ", self.html)
        for tag in [*build_dashboard.VENDOR, "__FLOW_ENGINE_JS__", "__PAYLOAD__"]:
            self.assertNotIn(tag, self.html)  # every placeholder filled


class FreshBuildTest(StandaloneChecks, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "index.html"
        build_dashboard.main(["--out", str(cls.out), "--scenario", str(EXAMPLE)])
        cls.html = cls.out.read_text()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_scenario_was_run_through_py_ewr_at_build_time(self):
        p = build_dashboard.read_payload(self.html)
        self.assertEqual(p["openIn"], "user")
        (sc,) = p["scenarios"]
        self.assertEqual(
            (sc["name"], sc["fmt"]), ("dummy_broken_scenario_A", "Standard time-series")
        )
        self.assertEqual(sc["flowGauges"], ["404216", "404243"])
        self.assertGreater(
            len(sc["vals"]), 10
        )  # [planning unit, gauge, EWR, FR, ETP] from py_ewr
        self.assertTrue(any(v[3] is not None for v in sc["vals"]))
        flows = p["flows"][sc["id"]]
        self.assertEqual(flows["start"], "1995-07-01")
        self.assertEqual(
            flows["gauges"]["404216"][:2], [34.9, 23.7]
        )  # the file's first two days
        ewrs = p["ewryears"][sc["id"]]["404216"]
        self.assertTrue(ewrs)
        self.assertFalse([k for k, v in ewrs.items() if "error" in v])
        self.assertTrue(p["regimes"]["404216"])

    def test_embed_scenarios_patches_a_built_page(self):
        import embed_scenarios

        out = Path(self.tmp.name) / "with_scenarios.html"
        html = embed_scenarios.build_with_scenarios(
            [{"name": "B", "path": str(EXAMPLE).replace("_A.csv", "_B_wetter.csv")}],
            out,
            template=self.out,
        )
        p = build_dashboard.read_payload(html)
        self.assertEqual([s["name"] for s in p["scenarios"]], ["B"])
        self.assertEqual(p["openIn"], "user")
        self.assertEqual(
            len(p["rows"]), len(build_dashboard.read_payload(self.html)["rows"])
        )
        self.assertEqual(html.count(build_dashboard.PAYLOAD_TAG), 1)


class CommittedPageTest(StandaloneChecks, unittest.TestCase):
    """index.html as committed (what a user downloads and double-clicks)."""

    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "index.html").read_text()


class ScriptJsonTest(unittest.TestCase):
    def test_hostile_strings_cannot_end_the_script_element(self):
        obj = {
            "name": "</script><script>alert(1)</script>",
            "c": "<!-- -->",
            "ls": "a b c",
            "x": float("nan"),
        }
        text = build_dashboard.script_json(obj)
        for bad in ("</", "<!--", "<", " ", " ", "NaN"):
            self.assertNotIn(bad, text)
        self.assertEqual(json.loads(text), {**obj, "x": None})

    def test_vendored_libraries_are_checked(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "vendor").mkdir()
            (Path(d) / "vendor" / "leaflet.js").write_text("tampered")
            old = build_dashboard.ROOT
            build_dashboard.ROOT = Path(d)
            try:
                with self.assertRaises(ValueError):
                    build_dashboard.vendor_text(
                        "leaflet.js", build_dashboard.VENDOR["__LIB_LEAFLET_JS__"][1]
                    )
            finally:
                build_dashboard.ROOT = old


if __name__ == "__main__":
    unittest.main()
