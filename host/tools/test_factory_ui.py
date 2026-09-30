#!/usr/bin/env python3
"""Tests for factory_ui.py: HTTP API and auto mode against the fake adapter."""

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import factory_program as fp  # noqa: E402
import factory_ui  # noqa: E402
import fake_adapter  # noqa: E402

KEY = bytes.fromhex("0f1e2d3c4b5a69788796a5b4c3d2e1f0")
BLOCK = "00:50:c2:aa:00:00-00:50:c2:aa:00:09"


class UiTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = os.path.join(tmp.name, "log.csv")
        self.adapter, path, stop = fake_adapter.start()
        self.addCleanup(stop)
        self.station = factory_ui.Station(path, KEY, self.log)
        self.server = factory_ui.make_server(self.station, port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Content-Type": "application/json", "X-Station": "1"} if method == "POST" else {}
        h.update(headers or {})
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, (json.loads(data) if r.getheader("Content-Type", "").startswith("application/json") else data)

    def start_run(self, **kw):
        body = {"id": "R1", "block": BLOCK, "quantity": 2}
        body.update(kw)
        return self.req("POST", "/api/run", body)

    def test_serves_page(self):
        status, body = self.req("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Production Station", body)

    def test_state_never_contains_key(self):
        self.start_run()
        self.req("POST", "/api/program", {})
        status, body = self.req("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertNotIn(KEY.hex(), json.dumps(body))
        self.assertEqual(body["station"]["key_fp"], fp.nessumctl.key_fingerprint(KEY))

    def test_program_flow(self):
        status, body = self.req("POST", "/api/program", {})
        self.assertEqual(status, 400)  # no run yet
        self.assertIn("select a production run", body["error"])
        status, st = self.start_run()
        self.assertEqual(status, 200)
        self.assertEqual(st["run"]["done"], 0)
        status, st = self.req("POST", "/api/program", {})
        self.assertEqual(st["last"]["outcome"], "ok")
        self.assertEqual(st["last"]["mac"], "00:50:c2:aa:00:00")
        self.assertEqual(st["run"]["done"], 1)
        self.assertEqual(st["recent"][0]["mac"], "00:50:c2:aa:00:00")
        self.assertTrue(self.adapter.locked)
        # Same (now locked) unit again: reported, not reprogrammed.
        status, st = self.req("POST", "/api/program", {})
        self.assertEqual(st["last"]["outcome"], "already")
        self.assertEqual(st["run"]["done"], 1)

    def test_run_complete(self):
        self.start_run(quantity=1)
        self.req("POST", "/api/program", {})
        self.adapter.__init__(serial="NEXT")  # "plug in" a fresh unit
        status, st = self.req("POST", "/api/program", {})
        self.assertEqual(st["last"]["outcome"], "complete")
        self.assertTrue(st["run"]["complete"])

    def test_bad_run_rejected(self):
        status, body = self.start_run(block="junk")
        self.assertEqual(status, 400)
        status, body = self.start_run(quantity=50)
        self.assertEqual(status, 400)
        self.assertIn("fewer than --quantity", body["error"])
        status, body = self.start_run(id="bad,id")
        self.assertEqual(status, 400)

    def test_resume_lists_runs_from_log(self):
        self.start_run()
        self.req("POST", "/api/program", {})
        self.req("POST", "/api/run/close", {})
        status, st = self.req("GET", "/api/state")
        self.assertIsNone(st["run"])
        self.assertEqual(st["runs"], [{"id": "R1", "block": BLOCK, "quantity": 2, "done": 1, "failed": 0, "verify_failed": 0}])

    def test_csrf_and_host_checks(self):
        status, _ = self.req("POST", "/api/program", {}, {"X-Station": ""})
        self.assertEqual(status, 403)
        status, _ = self.req("POST", "/api/program", {}, {"Content-Type": "text/plain"})
        self.assertEqual(status, 403)
        status, _ = self.req("GET", "/api/state", headers={"Host": "evil.example:80"})
        self.assertEqual(status, 403)

    def test_auto_mode_programs_each_unit_once(self):
        self.start_run()
        status, st = self.req("POST", "/api/auto", {"on": True})
        self.assertTrue(st["station"]["auto"])
        res = self.station.auto_step()
        self.assertEqual(res["outcome"], "ok")
        # The same unit (after its REBOOT) is not programmed again.
        self.station._need_check = True
        self.assertIsNone(self.station.auto_step())
        # A new unit is.
        self.adapter.__init__(serial="SIM0002")
        self.station._need_check = True
        res = self.station.auto_step()
        self.assertEqual(res["outcome"], "ok")
        self.assertEqual(res["mac"], "00:50:c2:aa:00:01")

    def test_auto_requires_run(self):
        status, body = self.req("POST", "/api/auto", {"on": True})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
