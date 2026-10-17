import http.client
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import server  # noqa: E402


class ForgetEndpointTest(unittest.TestCase):
    httpd: server.ThreadingHTTPServer
    port: int
    thread: threading.Thread

    @classmethod
    def setUpClass(cls):
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old_store, old_flows = server.STORE, server.FLOWS.copy()
        self.addCleanup(setattr, server, "STORE", old_store)
        self.addCleanup(self._restore_flows, old_flows)
        server.STORE = Path(self.tmp.name)
        server.FLOWS.clear()
        self.host = f"127.0.0.1:{self.port}"

    @staticmethod
    def _restore_flows(old_flows):
        server.FLOWS.clear()
        server.FLOWS.update(old_flows)

    def request(self, method, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request(method, path, headers=headers or {})
        response = conn.getresponse()
        body = response.read()
        result = response.status, dict(response.getheaders()), body
        conn.close()
        return result

    def make_scenario(self, scenario_id):
        path = server.STORE / f"{scenario_id}.csv"
        path.write_text("Date,123_flow\n2000-01-01,1\n")
        server.FLOWS[scenario_id] = "cached"
        return path

    def test_get_forget_is_405_and_does_not_delete(self):
        path = self.make_scenario("get-case")

        status, headers, _ = self.request("GET", "/api/forget?id=get-case")

        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "POST")
        self.assertTrue(path.exists())
        self.assertIn("get-case", server.FLOWS)

    def test_foreign_origin_post_is_forbidden_and_does_not_delete(self):
        path = self.make_scenario("foreign-case")

        status, _, _ = self.request(
            "POST",
            "/api/forget?id=foreign-case",
            {"Origin": f"http://evil.example:{self.port}"},
        )

        self.assertEqual(status, 403)
        self.assertTrue(path.exists())
        self.assertIn("foreign-case", server.FLOWS)

    def test_matching_loopback_origin_post_deletes(self):
        path = self.make_scenario("matching-case")

        status, _, body = self.request(
            "POST",
            "/api/forget?id=matching-case",
            {"Origin": f"http://localhost:{self.port}"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(body, b'{"ok": true}')
        self.assertFalse(path.exists())
        self.assertNotIn("matching-case", server.FLOWS)

    def test_missing_origin_post_deletes(self):
        path = self.make_scenario("no-origin-case")

        status, _, _ = self.request("POST", "/api/forget?id=no-origin-case")

        self.assertEqual(status, 200)
        self.assertFalse(path.exists())

    def test_unexpected_loopback_host_is_rejected(self):
        path = self.make_scenario("bad-host-case")

        status, _, _ = self.request(
            "POST",
            "/api/forget?id=bad-host-case",
            {"Host": f"evil.example:{self.port}", "Origin": f"http://localhost:{self.port}"},
        )

        self.assertEqual(status, 403)
        self.assertTrue(path.exists())

    def test_foreign_origin_post_run_is_forbidden_before_body_validation(self):
        status, _, _ = self.request(
            "POST",
            "/api/run",
            {"Origin": f"http://evil.example:{self.port}"},
        )

        self.assertEqual(status, 403)

    def test_matching_origin_post_run_passes_origin_check(self):
        status, _, _ = self.request(
            "POST",
            "/api/run",
            {"Origin": f"http://localhost:{self.port}"},
        )

        # Empty body is rejected by run validation only after the origin check passes.
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
