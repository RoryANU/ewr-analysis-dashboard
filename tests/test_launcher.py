"""Launcher (run.py) and server start-up: free-port choice, venv/requirements bookkeeping, friendly errors, server endpoints."""

import http.client
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import run  # noqa: E402
import server  # noqa: E402


def listening(port):
    s = socket.socket()
    s.bind(("127.0.0.1", port))
    s.listen()
    return s


class PortSelectionTest(unittest.TestCase):
    def test_free_preferred_port_is_used(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        self.assertEqual(server.find_free_port(port), port)

    def test_busy_port_moves_to_the_next_free_one(self):
        busy = listening(0)
        self.addCleanup(busy.close)
        port = busy.getsockname()[1]
        got = server.find_free_port(port)
        self.assertGreater(got, port)
        with socket.socket() as s:
            s.bind(("127.0.0.1", got))  # really free

    def test_all_candidates_busy_falls_back_to_an_os_chosen_port(self):
        busy = listening(0)
        self.addCleanup(busy.close)
        port = busy.getsockname()[1]
        got = server.find_free_port(port, tries=1)
        self.assertNotEqual(got, port)
        self.assertGreater(got, 0)

    def test_make_server_binds_a_different_port_when_preferred_is_busy(self):
        busy = listening(0)
        self.addCleanup(busy.close)
        srv = server.make_server(busy.getsockname()[1])
        self.addCleanup(srv.server_close)
        self.assertNotEqual(srv.server_address[1], busy.getsockname()[1])
        self.assertEqual(srv.server_address[0], "127.0.0.1")  # localhost only


class PortRaceTest(unittest.TestCase):
    def test_make_server_retries_when_the_probed_port_is_taken_before_bind(self):
        busy = listening(0)
        self.addCleanup(busy.close)
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
        probe.close()
        answers = iter(
            [busy.getsockname()[1], free]
        )  # the probe said "free" for a port that is in fact taken
        old = server.find_free_port
        self.addCleanup(setattr, server, "find_free_port", old)
        server.find_free_port = lambda preferred, tries=50, host="127.0.0.1": next(
            answers
        )
        srv = server.make_server(8792)
        self.addCleanup(srv.server_close)
        self.assertEqual(srv.server_address[1], free)


class MainTest(unittest.TestCase):
    def run_main(self, argv):
        opened, made = [], []

        class FakeServer:
            server_address = ("127.0.0.1", 9999)

            def serve_forever(self):
                time.sleep(1)  # long enough for the browser timer
                raise KeyboardInterrupt

        def fake_make(preferred, host="127.0.0.1"):
            made.append((preferred, host))
            return FakeServer()

        old = server.make_server, server.webbrowser.open
        self.addCleanup(
            lambda: (
                setattr(server, "make_server", old[0]),
                setattr(server.webbrowser, "open", old[1]),
            )
        )
        server.make_server, server.webbrowser.open = fake_make, opened.append
        server.main(argv)
        return opened, made

    def test_opens_the_default_browser_on_the_chosen_port(self):
        opened, made = self.run_main([])
        self.assertEqual(opened, ["http://localhost:9999/ewr_analysis_dashboard.html"])
        self.assertEqual(made, [(server.DEFAULT_PORT, "127.0.0.1")])

    def test_no_browser_and_port_and_host_arguments(self):
        opened, made = self.run_main(["--no-browser", "9000", "--host=0.0.0.0"])
        self.assertEqual(opened, [])
        self.assertEqual(made, [(9000, "0.0.0.0")])


class LauncherTest(unittest.TestCase):
    def test_old_python_gets_a_friendly_message(self):
        with self.assertRaises(run.LaunchError) as cm:
            run.check_python((3, 7))
        self.assertIn("python.org", str(cm.exception))
        run.check_python((3, 9))
        run.check_python((3, 13))

    def test_venv_python_path_per_os(self):
        self.assertEqual(
            run.venv_python("v", windows=True), Path("v/Scripts/python.exe")
        )
        self.assertEqual(run.venv_python("v", windows=False), Path("v/bin/python"))

    def test_requirements_install_is_skipped_until_the_file_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            venv, req = Path(tmp), Path(tmp) / "requirements.txt"
            req.write_text("pandas\n")
            self.assertTrue(run.needs_install(venv, req))  # never installed
            run.mark_installed(venv, req)
            self.assertFalse(run.needs_install(venv, req))
            req.write_text("pandas\nnumpy\n")
            self.assertTrue(run.needs_install(venv, req))  # requirements changed

    def test_failed_install_is_reported_not_stamped(self):
        with tempfile.TemporaryDirectory() as tmp:
            venv, req = Path(tmp), Path(tmp) / "requirements.txt"
            req.write_text("pandas\n")
            # "python -m pip" with a nonsense interpreter option fails: stands in for no internet
            fake = Path(tmp) / "fake_python"
            fake.write_text("#!/bin/sh\nexit 1\n")
            fake.chmod(0o755)
            with self.assertRaises(run.LaunchError):
                run.install_requirements(fake, venv, req)
            self.assertTrue(run.needs_install(venv, req))


class StaticAccessMixin:
    """Only the dashboard page is served; nothing else in the repository, no directory listings, no stored uploads."""

    def hdr(self, extra=None):
        return {**self.base_headers, **(extra or {})}

    def test_repository_files_and_uploads_cannot_be_retrieved_or_listed(self):
        store = ROOT / ".user_scenarios"
        store.mkdir(exist_ok=True)
        secret = store / "secret_test.csv"
        secret.write_text("Date,1_flow\n2000-01-01,1\n")
        self.addCleanup(secret.unlink)
        for path in [
            "/.user_scenarios/secret_test.csv",
            "/.user_scenarios/",
            "/.user_scenarios",
            "/src/server.py",
            "/src/",
            "/README.md",
            "/run.py",
            "/.git/config",
            "/.gitignore",
            "/data/",
            "/data/results/etp.csv",
            "/tests/",
            "/",
            "/..%2f..%2fetc/passwd",
            "/%2e%2e/%2e%2e/etc/passwd",
            "/../../etc/passwd",
            "//etc/passwd",
            "/ewr_analysis_dashboard.html/../run.py",
            "/ewr_analysis_dashboard.html/",
            "/ewr_analysis_dashboard.html.bak",
        ]:
            status, body = self.request("GET", path, headers=self.hdr())
            if path == "/":
                self.assertEqual(status, 200)
                continue
            self.assertIn(status, (301, 400, 404), path)
            self.assertNotIn(b"secret_test", body, path)
            self.assertNotIn(b"Index of", body, path)
            self.assertNotIn(b"ThreadingHTTPServer", body, path)
        self.assertEqual(
            self.request("GET", "/ewr_analysis_dashboard.html", headers=self.hdr())[0],
            200,
        )
        self.assertEqual(
            self.request("HEAD", "/src/server.py", headers=self.hdr())[0], 405
        )


class EndpointTest(StaticAccessMixin, unittest.TestCase):
    base_headers = {}

    @classmethod
    def setUpClass(cls):
        cls.httpd = server.make_server(server.DEFAULT_PORT)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request(method, path, body=body, headers=headers or {})
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, data

    def test_root_serves_the_dashboard(self):
        status, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>", body)

    def test_ping(self):
        status, body = self.request("GET", "/api/ping")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_run_rejects_empty_body_and_missing_gauges(self):
        h = {"Host": f"127.0.0.1:{self.port}"}
        self.assertEqual(
            self.request("POST", "/api/run", b"", {**h, "Content-Length": "0"})[0], 400
        )
        self.assertEqual(self.request("POST", "/api/run", b"Date,1_flow\n", h)[0], 400)

    def test_unknown_job_is_404(self):
        self.assertEqual(self.request("GET", "/api/job?id=nope")[0], 404)

    def test_csv_upload_runs_py_ewr_and_returns_results(self):
        csv = (ROOT / "data/example_scenarios/dummy_broken_scenario_A.csv").read_bytes()
        h = {
            "Host": f"127.0.0.1:{self.port}",
            "X-Gauges": "404216,404243",
            "X-Format": "Standard time-series",
            "X-Filename": "a.csv",
            "X-Scn-Id": "launchertest",
        }
        old_store = server.STORE
        with tempfile.TemporaryDirectory() as tmp:
            server.STORE = Path(tmp)
            self.addCleanup(setattr, server, "STORE", old_store)
            self.addCleanup(server.FLOWS.pop, "launchertest", None)
            status, body = self.request("POST", "/api/run", csv, h)
            self.assertEqual(status, 200)
            job = json.loads(body)["job"]
            for _ in range(600):
                out = json.loads(self.request("GET", f"/api/job?id={job}")[1])
                if out["status"] != "running":
                    break
                time.sleep(0.5)
            self.assertEqual(out["status"], "done", out)
            self.assertGreater(len(out["rows"]), 0)
            self.assertTrue({"pu", "gauge", "ewr", "fr", "et"} <= set(out["rows"][0]))
            status, body = self.request("GET", "/api/ewrs?gauge=404216")
            self.assertEqual(status, 200)
            self.assertGreater(len(json.loads(body)["ewrs"]), 0)


class TokenServerTest(StaticAccessMixin, unittest.TestCase):
    """A server bound beyond localhost needs the random access token on every request."""

    base_headers = {}

    @classmethod
    def setUpClass(cls):
        cls.httpd = server.make_server(server.DEFAULT_PORT)
        cls.httpd.token = "tok-123456"
        cls.port = cls.httpd.server_address[1]
        cls.base_headers = {"X-Token": cls.httpd.token}
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()

    request = EndpointTest.request

    def test_requests_without_or_with_wrong_token_are_refused(self):
        for path in [
            "/",
            "/ewr_analysis_dashboard.html",
            "/api/ping",
            "/api/ewrs?gauge=404216",
            "/src/server.py",
        ]:
            self.assertEqual(self.request("GET", path)[0], 401, path)
            self.assertEqual(
                self.request("GET", path, headers={"X-Token": "wrong"})[0], 401, path
            )
        self.assertEqual(
            self.request("POST", "/api/run", b"x", {"X-Gauges": "1"})[0], 401
        )
        self.assertEqual(self.request("POST", "/api/forget?id=a")[0], 401)

    def test_query_token_serves_page_and_sets_cookie_used_by_api(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/?token=tok-123456")
        r = conn.getresponse()
        r.read()
        conn.close()
        self.assertEqual(r.status, 200)
        cookie = r.getheader("Set-Cookie")
        self.assertIn("ewr_token=tok-123456", cookie)
        self.assertIn("HttpOnly", cookie)
        status, _ = self.request(
            "GET", "/api/ping", headers={"Cookie": "ewr_token=tok-123456"}
        )
        self.assertEqual(status, 200)


class VenvCheckTest(unittest.TestCase):
    def test_broken_interpreter_is_detected_with_guidance(self):
        with tempfile.TemporaryDirectory() as tmp:
            py = run.venv_python(tmp)
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("#!/bin/sh\nexit 3\n")
            py.chmod(0o755)
            self.assertFalse(run.interpreter_works(py))
            with self.assertRaises(run.LaunchError) as cm:
                run.ensure_venv(tmp)
            self.assertIn(".venv", str(cm.exception))
        self.assertFalse(run.interpreter_works(Path("/no/such/python")))
        self.assertTrue(run.interpreter_works(sys.executable))


if __name__ == "__main__":
    unittest.main()
