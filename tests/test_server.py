import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from paperclaw.core import CrawlError, atomic_json, normalize
from paperclaw.instance import InstanceLock
from paperclaw.server import Application, make_server, validate_options
from test_core import record, dblp, ieee


OPTIONS = {"source": "dblp", "conferences": ["INFOCOM"], "years": [2025]}


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.app = Application(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def await_job(self):
        deadline = time.monotonic() + 3
        while self.app.snapshot()["status"] == "running" and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertNotEqual(self.app.snapshot()["status"], "running")

    def test_partial_results_survive_failure_restart_and_never_save_key(self):
        calls = []
        def get(*args):
            calls.append(args)
            if len(calls) == 1:
                return dblp([record()], 2)
            raise CrawlError("Simulated rate limit")
        self.app.client.get = get
        self.app.start({**OPTIONS, "conferences": ["INFOCOM", "ICDE"], "ieee_key": "supersecret"})
        self.await_job()
        state = self.app.snapshot()
        self.assertEqual(state["status"], "partial")
        self.assertEqual(state["groups"][1]["status"], "not_run")
        self.assertEqual(len(state["papers"]), 1)
        self.assertEqual(len(calls), 2)
        restored = Application(self.path)
        self.assertEqual(len(restored.snapshot()["papers"]), 1)
        self.assertNotIn("supersecret", (self.path / "results.json").read_text(encoding="utf-8"))

    def test_import_dedup_and_partial_file_label(self):
        body = {**OPTIONS, "data": dblp([record()], 9)}
        self.app.import_data(body)
        self.app.import_data(body)
        self.assertEqual(len(self.app.snapshot()["papers"]), 1)
        self.assertIn("1/9", self.app.snapshot()["message"])

    def test_restart_of_running_job_is_interrupted(self):
        state = self.app.snapshot()
        state.update(status="running", groups=[{"status": "running"}, {"status": "pending"}])
        atomic_json(self.path / "results.json", state)
        restored = Application(self.path)
        self.assertEqual(restored.snapshot()["status"], "interrupted")
        self.assertTrue(all(g["status"] == "interrupted" for g in restored.snapshot()["groups"]))

    def test_preserve_richer_metadata_and_summary(self):
        p = normalize(ieee(), "ieee", "ICDE", 2025)
        p["ai_summary"] = "Existing analysis"
        self.app.merge([p])
        raw = record(venue="ICDE")
        raw["doi"] = p["doi"]
        self.app.merge([normalize(raw, "dblp", "ICDE", 2025)])
        self.assertEqual(self.app.snapshot()["papers"][0]["abstract"], p["abstract"])
        self.assertEqual(self.app.snapshot()["papers"][0]["ai_summary"], "Existing analysis")

    def test_ai_requires_abstract_and_https(self):
        self.app.merge([normalize(record(), "dblp", "INFOCOM", 2025)])
        p = self.app.snapshot()["papers"][0]
        for url in ["http://remote.example/v1", "https://key@example.com/v1", "https://example.com/v1?key=123"]:
            with self.assertRaises(ValueError): self.app.analyze({"id": p["id"], "base_url": url, "model": "x"})
        with self.assertRaises(ValueError): self.app.analyze({"id": p["id"], "base_url": "https://example.com/v1", "model": "x"})

    def test_ai_sends_only_selected_title_abstract_and_does_not_store_key(self):
        p = normalize(ieee(), "ieee", "ICDE", 2025)
        self.app.merge([p])
        def fake(url, body, headers):
            self.assertEqual(url, "https://example.com/v1/chat/completions")
            payload = json.loads(body)
            self.assertEqual(json.loads(payload["messages"][1]["content"]), {"title":p["title"],"abstract":p["abstract"]})
            self.assertEqual(headers["Authorization"], "Bearer supersecret")
            return 200, {}, b'{"choices":[{"message":{"content":"Summary"}}]}'
        with patch("paperclaw.server.request_json", fake):
            self.app.analyze({"id":p["id"],"base_url":"https://example.com/v1","model":"test","key":"supersecret"})
        self.assertEqual(self.app.snapshot()["papers"][0]["ai_summary"], "Summary")
        self.assertNotIn("supersecret", (self.path / "results.json").read_text(encoding="utf-8"))

    def test_validation_and_single_instance(self):
        for bad in [{**OPTIONS, "years": []}, {**OPTIONS, "years": [True]}, {**OPTIONS, "conferences": ["FAKE"]}]:
            with self.assertRaises(ValueError): validate_options(bad)
        with InstanceLock(self.path):
            with self.assertRaises(OSError):
                with InstanceLock(self.path): pass
        with InstanceLock(self.path): pass


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Application(self.temp.name)
        self.server = make_server(self.app, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, body=None, token=True, headers=None):
        h = {"X-PaperClaw-Token": self.app.token} if token else {}
        if body is not None: h["Content-Type"] = "application/json"
        h.update(headers or {})
        req = urllib.request.Request(self.base+path, data=json.dumps(body).encode() if body is not None else None, headers=h)
        return urllib.request.urlopen(req, timeout=5)

    def test_assets_config_host_csrf_and_error_response(self):
        with self.request("/") as r:
            self.assertIn(b"PaperClaw", r.read())
            self.assertIn("frame-ancestors 'none'", r.headers["Content-Security-Policy"])
        with self.request("/api/config", token=False) as r:
            self.assertEqual(json.load(r)["token"], self.app.token)
        for path, body, token, headers in [("/api/start", OPTIONS, False, {}), ("/api/config", None, False, {"Host":"attacker.example"}), ("/api/stop", {}, True, {"Origin":"https://attacker.example"})]:
            with self.assertRaises(urllib.error.HTTPError) as error: self.request(path, body, token, headers)
            self.assertEqual(error.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as error: self.request("/api/start", {**OPTIONS,"source":"ieee"})
        self.assertEqual(error.exception.code, 400)

    def test_import_filter_export_and_poll_revision(self):
        with self.request("/api/import", {**OPTIONS, "data": dblp([record("a"),record("b")])}) as r:
            self.assertEqual(json.load(r)["imported"], 2)
        with self.request("/api/state") as r:
            state = json.load(r)
        with self.request("/api/export", {"format":"json","ids":[state["papers"][0]["id"]]}) as r:
            self.assertEqual(len(json.load(r)), 1)
        with self.request("/api/state?revision="+str(state["revision"])) as r:
            self.assertNotIn("papers", json.load(r))


if __name__ == "__main__": unittest.main()
