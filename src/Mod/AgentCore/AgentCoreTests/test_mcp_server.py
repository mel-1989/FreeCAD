# SPDX-License-Identifier: LGPL-2.1-or-later
"""F4 acceptance through the server with real FreeCADCmd workers (F3 stubbed).

- scripted session (read, begin, edit, verify, propose) replayed twice gives an
  identical golden document (F1 dump), stored in fixtures/mcp/golden_session.json;
  AGENTCORE_UPDATE_GOLDEN=1 rewrites it
- killing a FreeCADCmd worker mid-verify returns an error result and the next call works
- killing the session worker mid-exec returns session_lost and the next call respawns it
- approval over MCP or the bridge is refused
"""

import json
import os
import signal
import threading
import time
import unittest

from AgentCoreTests.fixtures.mcp import mcp_common as mc
from AgentCoreTests.test_state_golden import canonical, diff
from AgentCore.mcp.bridge import BridgeClient, BridgeError

UPDATE = os.environ.get("AGENTCORE_UPDATE_GOLDEN") == "1"


def golden_view(run):
    after = dict(run["after"])
    for k in ("generator", "doc", "schema", "recovered"):
        after.pop(k, None)
    return {
        "document": canonical(after),
        "change_set": run["proposal"]["change_set"],
        "exec_result": run["exec"]["result"],
        "verify": [{"check": r["check"], "ok": r["ok"], "status": r["status"]} for r in run["verify"]["results"]],
        "log": run["log"],
    }


class TestGoldenSession(unittest.TestCase):
    def test_replay_twice_identical_golden(self):
        views = []
        for _ in range(2):
            with mc.tmpdir("mcp_golden_") as d:
                path = mc.save_fixtures(["box"], d)["box"]
                s = mc.make_server()
                try:
                    run = mc.scripted_session(s, path)
                finally:
                    s.close()
            views.append(golden_view(run))
        a, b = views
        self.assertEqual(diff(a, b), [], "two replays differ")
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertEqual(a["change_set"]["added"], ["Hole", "Plate"])
        self.assertEqual([m["name"] for m in a["change_set"]["modified"]], ["Box"])
        self.assertTrue(all(v["ok"] for v in a["verify"]))
        if UPDATE or not os.path.exists(mc.GOLDEN_SESSION):
            with open(mc.GOLDEN_SESSION, "w", encoding="utf-8") as fh:
                json.dump(a, fh, indent=1, sort_keys=True)
                fh.write("\n")
        with open(mc.GOLDEN_SESSION, encoding="utf-8") as fh:
            golden = json.load(fh)
        self.assertEqual(diff(golden, json.loads(json.dumps(a))), [], "replay differs from the stored golden")


class TestServerWorkers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = mc.tmpdir("mcp_srv_")
        cls.paths = mc.save_fixtures(["box", "pair_overlap"], cls.tmp.name)
        cls.s = mc.make_server(parts_path=[cls.tmp.name], verify_timeout=60)

    @classmethod
    def tearDownClass(cls):
        cls.s.close()
        cls.tmp.cleanup()

    def open(self, name="box"):
        out, err = mc.call(self.s, "open_document", path=self.paths[name])
        self.assertFalse(err, out)
        return out["doc"]

    def test_kill_pool_worker_mid_verify(self):
        doc = self.open()
        pool = self.s.pool  # created here: the lazy property raced the server thread's own creation
        res = {}
        t = threading.Thread(target=lambda: res.update(
            zip(("out", "err"), mc.call(self.s, "verify", doc=doc, checks=["validity", {"check": "_selftest_sleep", "seconds": 30}]))))
        t0 = time.time()
        t.start()
        victim = None
        while time.time() - t0 < 30 and victim is None:
            for pid, name in list(pool._live.items()):
                if name == "_selftest_sleep":
                    victim = pid
            time.sleep(0.05)
        self.assertIsNotNone(victim, "no verify worker appeared")
        time.sleep(0.5)
        os.kill(victim, signal.SIGKILL)
        t.join(60)
        self.assertFalse(t.is_alive())
        self.assertLess(time.time() - t0, 25)
        out, err = res["out"], res["err"]
        self.assertTrue(err, out)
        self.assertEqual(out["error"], "verify_crash")
        by = {r["check"]: r for r in out["results"]}
        self.assertEqual(by["_selftest_sleep"]["status"], "crash")
        self.assertEqual(by["_selftest_sleep"]["details"]["signal"], signal.SIGKILL)
        self.assertTrue(by["validity"]["ok"])
        # the server keeps serving
        out, err = mc.call(self.s, "verify", doc=doc, checks=["validity", "mass"])
        self.assertFalse(err, out)
        self.assertTrue(out["ok"])
        out, err = mc.call(self.s, "get_state", doc=doc)
        self.assertFalse(err)
        self.assertEqual(out["document"]["object_count"], 1)

    def test_kill_session_worker_mid_exec_respawns(self):
        doc = self.open()
        mc.call(self.s, "begin_change", doc=doc)
        pid = self.s.worker_pids()[doc]
        res = {}
        t = threading.Thread(target=lambda: res.update(zip(("out", "err"), mc.call(
            self.s, "exec_code", doc=doc, code="doc.getObject('Box').Length = 99\nimport time\ntime.sleep(20)", timeout=60))))
        t.start()
        time.sleep(1.0)
        os.kill(pid, signal.SIGKILL)
        t.join(60)
        out, err = res["out"], res["err"]
        self.assertTrue(err)
        self.assertEqual(out["error"], "session_lost")
        self.assertEqual(out["worker_exit"]["signal"], signal.SIGKILL)
        out, err = mc.call(self.s, "get_state", doc=doc)  # respawned from the pre-change checkpoint
        self.assertFalse(err, out)
        self.assertIn("recovered", out)
        self.assertNotEqual(self.s.worker_pids()[doc], pid)
        box = next(o for o in out["objects"] if o["name"] == "Box")
        length = next(p["value"] for p in box["properties"] if p["name"] == "Length")
        self.assertNotEqual(length, 99)
        out, err = mc.call(self.s, "list_documents")
        entry = next(e for e in out["documents"] if e["doc"] == doc)
        self.assertIsNone(entry["change"])  # the open change died with the worker
        out, err = mc.call(self.s, "begin_change", doc=doc)
        self.assertFalse(err, out)

    def test_uninterruptible_exec_hard_timeout(self):
        doc = self.open()
        mc.call(self.s, "begin_change", doc=doc)
        code = "import signal, time\nsignal.pthread_sigmask(signal.SIG_BLOCK, [signal.SIGALRM])\ntime.sleep(60)"
        t0 = time.time()
        out, err = mc.call(self.s, "exec_code", doc=doc, code=code, timeout=0.5)
        self.assertTrue(err)
        self.assertEqual(out["error"], "timeout")
        self.assertLess(time.time() - t0, 40)
        out, err = mc.call(self.s, "get_state", doc=doc)
        self.assertFalse(err, out)

    def test_soft_timeout_keeps_worker(self):
        doc = self.open()
        mc.call(self.s, "begin_change", doc=doc)
        pid = self.s.worker_pids()[doc]
        out, err = mc.call(self.s, "exec_code", doc=doc, code="while True:\n    pass", timeout=0.5)
        self.assertTrue(err)
        self.assertEqual(out["status"], "timeout")
        self.assertEqual(self.s.worker_pids()[doc], pid)

    def test_approval_refused_over_mcp_and_bridge(self):
        doc = self.open()
        mc.call(self.s, "begin_change", doc=doc)
        mc.call(self.s, "exec_code", doc=doc, code="doc.getObject('Box').Length = 12")
        prop, err = mc.call(self.s, "propose_change", doc=doc)
        self.assertFalse(err)
        self.assertEqual(prop["status"], "pending_human_approval")
        out, err = mc.call(self.s, "approve_change", proposal_id=prop["proposal_id"])
        self.assertTrue(err)
        self.assertEqual(out["error"], "approval_refused")
        out, err = mc.call(self.s, "exec_code", doc=doc, code="turn.accept('x')")
        self.assertTrue(err)
        self.assertEqual(out["error"], "policy_refused")
        w = self.s.docs[doc].worker
        with self.assertRaises(BridgeError) as cm:
            w.call("accept", {"token": "anything"})
        self.assertEqual(cm.exception.code, "approval_refused")
        # wrong bridge token: refused and disconnected
        bad = BridgeClient(w.socket, "0" * 48)
        with self.assertRaises(BridgeError) as cm:
            bad.call("info")
        self.assertEqual(cm.exception.code, "auth")
        bad.close()
        out, err = mc.call(self.s, "list_documents")
        self.assertEqual(next(e for e in out["documents"] if e["doc"] == doc)["change"], "proposed")

    def test_import_export_round_trip_and_search(self):
        doc = self.open("pair_overlap")
        d = self.tmp.name
        for ext in (".step", ".brep", ".FCStd"):
            out, err = mc.call(self.s, "import_export", doc=doc, action="export", path=os.path.join(d, "rt" + ext))
            self.assertFalse(err, out)
        out, err = mc.call(self.s, "import_export", doc=doc, action="import", path=os.path.join(d, "rt.brep"))
        self.assertTrue(err)
        self.assertEqual(out["error"], "no_change")
        mc.call(self.s, "begin_change", doc=doc)
        out, err = mc.call(self.s, "import_export", doc=doc, action="import", path=os.path.join(d, "rt.step"))
        self.assertFalse(err, out)
        self.assertTrue(out["imported"])
        for ext in (".step", ".brep", ".FCStd"):
            out, err = mc.call(self.s, "open_document", path=os.path.join(d, "rt" + ext))
            self.assertFalse(err, out)
            self.assertGreaterEqual(out["object_count"], 1)
            v, err = mc.call(self.s, "verify", doc=out["doc"], checks=["validity"])
            self.assertTrue(v["ok"], v)
        out, err = mc.call(self.s, "search_parts", query="pair overlap")
        self.assertFalse(err)
        top = [os.path.basename(r["path"]) for r in out["results"][:2]]
        self.assertEqual(sorted(top), ["pair_overlap.FCStd", "rt.step"], out)  # file name; STEP product name
        out, err = mc.call(self.s, "describe_entity", doc=doc, entity="A:Face1")
        self.assertFalse(err, out)
        self.assertEqual(out["element_type"], "face")

    def test_diff_unavailable_through_server(self):
        s = mc.make_server(stub=False, worker_env={"CADFORK_MCP_DIFF_MODULE": "mcp_no_such_diff_module"})
        try:
            out, err = mc.call(s, "open_document", new_name="NoDiff")
            out, err = mc.call(s, "begin_change")
            self.assertTrue(err)
            self.assertEqual(out["error"], "diff_unavailable")
            out, err = mc.call(s, "get_state")
            self.assertFalse(err)
        finally:
            s.close()

    def test_real_diff_through_server(self):
        """F3 is merged: the full loop on the real AgentTurn in a worker, discard restores."""
        from AgentCore import diff as real_diff

        s = mc.make_server(stub=False)
        try:
            out, err = mc.call(s, "open_document", path=self.paths["box"])
            doc = out["doc"]
            out, err = mc.call(s, "begin_change", doc=doc)
            self.assertFalse(err, out)                     # no diff_unavailable branch any more
            out, err = mc.call(s, "exec_code", doc=doc, code=mc.SCRIPT_EDIT)
            self.assertFalse(err, out)
            prop, err = mc.call(s, "propose_change", doc=doc, summary="real F3")
            self.assertFalse(err, prop)
            self.assertEqual(prop["status"], "pending_human_approval")
            cs = prop["change_set"]
            self.assertEqual(cs["schema"], real_diff.SCHEMA)
            self.assertEqual(sorted(f["name"] for f in cs["features"]["added"]), ["Hole", "Plate"])
            self.assertGreaterEqual(cs["transactions"]["segments"], 1)
            self.assertTrue(prop["turn_id"] and prop["digest"])
            out, err = mc.call(s, "begin_change", doc=doc, discard_pending=True)
            self.assertFalse(err, out)
            st, err = mc.call(s, "get_state", doc=doc)
            self.assertEqual(sorted(o["name"] for o in st["objects"]), ["Box"])
        finally:
            s.close()


if __name__ == "__main__":
    unittest.main()
