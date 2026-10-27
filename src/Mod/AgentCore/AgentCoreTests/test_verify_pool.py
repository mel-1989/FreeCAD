# SPDX-License-Identifier: LGPL-2.1-or-later
"""Worker pool: JSON results, timeouts, crash capture, kill test."""

import json
import os
import signal
import tempfile
import threading
import time
import unittest

from AgentCore import verify
from AgentCoreTests._verify_common import builders


class TestPool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="verify_fx_")
        cls.paths = {n: builders.save(n, cls.tmp.name) for n in ("box", "pair_overlap", "bad_shape", "plate")}
        cls.pool = verify.Pool(size=3, timeout=60)

    @classmethod
    def tearDownClass(cls):
        cls.pool.close()
        cls.tmp.cleanup()

    def test_results_match_in_process_and_are_json(self):
        out = self.pool.run(self.paths["pair_overlap"], ["recompute", "validity", "interference", "mass"])
        json.dumps(out)
        self.assertEqual(out["status"], "ok")
        by = {r["check"]: r for r in out["results"]}
        self.assertEqual([r["check"] for r in out["results"]], ["recompute", "validity", "interference", "mass"])
        self.assertTrue(by["recompute"]["ok"] and by["validity"]["ok"] and by["mass"]["ok"])
        self.assertFalse(by["interference"]["ok"])
        self.assertEqual(by["interference"]["entities"], ["A", "B"])
        self.assertAlmostEqual(by["interference"]["details"]["interferences"][0]["volume_mm3"], 500.0, places=6)

    def test_validity_mutant_through_pool(self):
        out = self.pool.run(self.paths["bad_shape"], ["validity"])
        self.assertFalse(out["results"][0]["ok"])
        self.assertEqual(out["results"][0]["entities"], ["Open"])

    def test_thickness_through_pool(self):
        out = self.pool.run(self.paths["plate"], [{"check": "thickness", "min_required": 1.5}])
        r = out["results"][0]
        self.assertTrue(r["ok"], r)
        self.assertAlmostEqual(r["details"]["min_thickness_mm"], 2.0, places=3)

    def test_missing_file_is_structured_error(self):
        out = self.pool.run("/nonexistent/none.FCStd", ["validity"])
        self.assertEqual(out["status"], "error")
        self.assertFalse(out["ok"])

    def _still_serving(self):
        out = self.pool.run(self.paths["box"], ["validity", "mass"])
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["ok"], out)

    def test_abort_mid_check_is_crash_result_and_pool_keeps_serving(self):
        out = self.pool.run(self.paths["box"], ["validity", "_selftest_abort", "mass"])
        self.assertEqual(out["status"], "crash")
        by = {r["check"]: r for r in out["results"]}
        self.assertEqual(by["_selftest_abort"]["status"], "crash")
        self.assertFalse(by["_selftest_abort"]["ok"])
        self.assertEqual(by["_selftest_abort"]["details"]["signal"], signal.SIGABRT)
        self.assertTrue(by["validity"]["ok"] and by["mass"]["ok"])  # siblings unaffected
        self._still_serving()

    def test_sigkill_self_is_crash_result(self):
        out = self.pool.run(self.paths["box"], ["_selftest_kill"])
        self.assertEqual(out["results"][0]["status"], "crash")
        self.assertEqual(out["results"][0]["details"]["signal"], signal.SIGKILL)
        self._still_serving()

    def test_external_kill_of_worker_mid_check(self):
        res = {}
        t = threading.Thread(target=lambda: res.update(self.pool.run(self.paths["box"], [{"check": "_selftest_sleep", "seconds": 30}])))
        t.start()
        deadline = time.time() + 20
        while time.time() < deadline and not self.pool.live_pids():
            time.sleep(0.05)
        pids = self.pool.live_pids()
        self.assertTrue(pids, "worker never started")
        time.sleep(0.5)
        os.kill(pids[0], signal.SIGKILL)
        t.join(20)
        self.assertFalse(t.is_alive())
        self.assertEqual(res["results"][0]["status"], "crash")
        self.assertEqual(res["results"][0]["details"]["signal"], signal.SIGKILL)
        self._still_serving()

    def test_timeout_kills_worker_and_pool_survives(self):
        t0 = time.time()
        out = self.pool.run(self.paths["box"], [{"check": "_selftest_sleep", "seconds": 30, "timeout": 2}])
        self.assertLess(time.time() - t0, 15)
        self.assertEqual(out["status"], "timeout")
        self.assertEqual(out["results"][0]["details"]["timeout_s"], 2.0)
        self.assertEqual(self.pool.live_pids(), [])
        self._still_serving()

    def test_find_freecadcmd(self):
        self.assertTrue(os.path.exists(verify.find_freecadcmd()))
