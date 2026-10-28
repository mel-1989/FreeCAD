# SPDX-License-Identifier: LGPL-2.1-or-later
"""F4 replay and kill-worker checks on the real F3 path, end to end.

Run 001 ran these against the stub diff layer (``mcp_stub_diff``).  Here every
step goes through the real ``AgentCore.diff.AgentTurn`` in a sealed
FreeCADCmd session worker: propose, approval by the separate approver
process, the worker's accept, then verify.  The stub-layer versions in
test_mcp_server.py remain: they pin the stub's golden document and the
server's worker plumbing, which does not depend on F3.
"""

import json
import os
import shutil
import signal
import threading
import time
import unittest

from AgentCoreTests.fixtures.mcp import mcp_common as mc
from AgentCoreTests.test_diff_approver import Approver, short_dir
from AgentCoreTests.test_state_golden import canonical, diff

CHECKS = ["recompute", "validity", "mass"]


def _approve(case, s, a, doc, prop, timeout=30):
    """The human side: type the approval into the approver process, wait for the worker's accept."""
    case.assertEqual(prop["status"], "pending_human_approval")
    case.assertTrue(a.say(f"approve {prop['turn_id']} {prop['digest']}").startswith("APPROVED"))
    deadline = time.time() + timeout
    entry = None
    while time.time() < deadline:
        entry = mc.call(s, "list_documents")[0]["documents"][0]
        if entry["change"] is None:
            break
        time.sleep(0.2)
    case.assertEqual(entry["last_decision"]["decision"], "accepted", entry)
    return entry["last_decision"]


def real_session(case, s, a, fcstd):
    """read, begin, edit, verify, propose, approve in the approver, accept, verify again, read."""
    log = []

    def step(name, **kw):
        out, err = mc.call(s, name, **kw)
        log.append({"tool": name, "isError": err})
        case.assertFalse(err, out)
        return out

    doc = step("open_document", path=fcstd)["doc"]
    step("get_state", doc=doc)
    step("begin_change", doc=doc)
    ex = step("exec_code", doc=doc, code=mc.SCRIPT_EDIT)
    ver_pre = step("verify", doc=doc, checks=CHECKS)
    prop = step("propose_change", doc=doc, summary="lengthen Box, cut a hole")
    decision = _approve(case, s, a, doc, prop)
    ver_post = step("verify", doc=doc, checks=CHECKS)      # after the accept: the committed state verifies
    after = step("get_state", doc=doc)
    return {"log": log, "exec": ex, "verify_pre": ver_pre, "verify_post": ver_post, "proposal": prop,
            "decision": decision, "after": after}


def real_view(run):
    """The deterministic part of a real-F3 session: ids, digests and timings are dropped, the rest must match."""
    after = dict(run["after"])
    for k in ("generator", "doc", "schema", "recovered"):
        after.pop(k, None)
    cs = json.loads(json.dumps(run["proposal"]["change_set"]))
    return {
        "document": canonical(after),
        "added": sorted(f["name"] for f in cs["features"]["added"]),
        "modified": sorted(cs["features"]["modified"]) if isinstance(cs["features"]["modified"], list) else cs["features"]["modified"],
        "exec_result": run["exec"]["result"],
        "verify_pre": [(r["check"], r["ok"], r["status"]) for r in run["verify_pre"]["results"]],
        "verify_post": [(r["check"], r["ok"], r["status"]) for r in run["verify_post"]["results"]],
        "decision": run["decision"]["decision"],
        "log": run["log"],
    }


class TestRealF3Replay(unittest.TestCase):
    def test_replay_twice_identical_document_through_approval(self):
        views = []
        for _ in range(2):
            d = short_dir("acmcp-rp-")
            a = None
            try:
                path = mc.save_fixtures(["box"], d)["box"]
                a = Approver(d)
                s = mc.make_server(stub=False, approver_socket=a.socket)
                try:
                    run = real_session(self, s, a, path)
                finally:
                    s.close()
            finally:
                if a:
                    a.close()
                shutil.rmtree(d, ignore_errors=True)
            views.append(run)
        a, b = (real_view(r) for r in views)
        self.assertEqual(diff(a, b), [], "two real-F3 replays differ")
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertEqual(a["added"], ["Hole", "Plate"])
        self.assertEqual(a["decision"], "accepted")
        self.assertTrue(all(ok for _, ok, _ in a["verify_pre"] + a["verify_post"]))
        names = sorted(o["name"] for o in views[0]["after"]["objects"])
        self.assertEqual(names, ["Box", "Hole", "Plate"])      # the accepted change really is in the document
        # real F3, not the stub: the proposal carries the F3 schema and a bound turn id and digest
        from AgentCore import diff as real_diff
        self.assertEqual(views[0]["proposal"]["change_set"]["schema"], real_diff.SCHEMA)
        self.assertTrue(views[0]["proposal"]["turn_id"] and views[0]["proposal"]["digest"])


class TestRealF3KillWorker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = short_dir("acmcp-kw-")
        cls.fixture = mc.save_fixtures(["box"], cls.dir)["box"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def _length(self, s, doc):
        st, _ = mc.call(s, "get_state", doc=doc)
        box = next(o for o in st["objects"] if o["name"] == "Box")
        v = next(p["value"] for p in box["properties"] if p["name"] == "Length")
        return v["value"] if isinstance(v, dict) else v

    def test_kill_pool_worker_mid_verify_then_propose_approve_accept(self):
        """Verify runs inside an open real-F3 change; killing its pool worker errors that call only:
        the change survives, and propose -> human approval -> accept still lands."""
        a = Approver(self.dir)
        s = mc.make_server(stub=False, approver_socket=a.socket, verify_timeout=120)
        try:
            doc = mc.call(s, "open_document", path=self.fixture)[0]["doc"]
            mc.call(s, "begin_change", doc=doc)
            self.assertFalse(mc.call(s, "exec_code", doc=doc, code="doc.getObject('Box').Length = 21")[1])
            pool = s.pool      # create the lazy pool here: polling it from this thread first would race the server's own creation
            res = {}
            t = threading.Thread(target=lambda: res.update(zip(("out", "err"), mc.call(
                s, "verify", doc=doc, checks=["validity", {"check": "_selftest_sleep", "seconds": 60}]))))
            t0 = time.time()
            t.start()
            victim = None
            while time.time() - t0 < 90 and victim is None:
                for pid, name in list(pool._live.items()):
                    if name == "_selftest_sleep":
                        victim = pid
                time.sleep(0.05)
            self.assertIsNotNone(victim, f"no verify worker appeared; verify call returned {res!r}, pool {pool._live!r}")
            time.sleep(0.5)
            os.kill(victim, signal.SIGKILL)
            t.join(120)
            self.assertFalse(t.is_alive())
            out, err = res["out"], res["err"]
            self.assertTrue(err, out)
            self.assertEqual(out["error"], "verify_crash")
            by = {r["check"]: r for r in out["results"]}
            self.assertEqual(by["_selftest_sleep"]["status"], "crash")
            self.assertTrue(by["validity"]["ok"])
            entry = mc.call(s, "list_documents")[0]["documents"][0]
            self.assertEqual(entry["change"], "open")             # the real turn is still open
            self.assertEqual(self._length(s, doc), 21)             # and holds the edit
            prop, err = mc.call(s, "propose_change", doc=doc, summary="after the verifier crash")
            self.assertFalse(err, prop)
            _approve(self, s, a, doc, prop)
            self.assertEqual(self._length(s, doc), 21)
            out, err = mc.call(s, "verify", doc=doc, checks=CHECKS)
            self.assertFalse(err, out)
            self.assertTrue(out["ok"])
        finally:
            s.close()
            a.close()

    def test_kill_session_worker_mid_exec_real_turn_respawns_and_accepts(self):
        """SIGKILL of the session worker inside a real turn: session_lost, respawn from the pre-change
        checkpoint with no open change, and a fresh real-F3 change then goes through approval."""
        a = Approver(self.dir)
        s = mc.make_server(stub=False, approver_socket=a.socket)
        try:
            doc = mc.call(s, "open_document", path=self.fixture)[0]["doc"]
            l0 = self._length(s, doc)
            mc.call(s, "begin_change", doc=doc)
            pid = s.worker_pids()[doc]
            res = {}
            t = threading.Thread(target=lambda: res.update(zip(("out", "err"), mc.call(
                s, "exec_code", doc=doc, timeout=60,
                code="doc.getObject('Box').Length = 99\nimport time\ntime.sleep(20)"))))
            t.start()
            time.sleep(1.5)
            os.kill(pid, signal.SIGKILL)
            t.join(60)
            out, err = res["out"], res["err"]
            self.assertTrue(err)
            self.assertEqual(out["error"], "session_lost")
            self.assertEqual(self._length(s, doc), l0)             # respawned from the pre-change checkpoint
            self.assertNotEqual(s.worker_pids()[doc], pid)
            entry = mc.call(s, "list_documents")[0]["documents"][0]
            self.assertIsNone(entry["change"])
            mc.call(s, "begin_change", doc=doc)
            self.assertFalse(mc.call(s, "exec_code", doc=doc, code="doc.getObject('Box').Length = 17")[1])
            prop, err = mc.call(s, "propose_change", doc=doc)
            self.assertFalse(err, prop)
            _approve(self, s, a, doc, prop)
            self.assertEqual(self._length(s, doc), 17)
        finally:
            s.close()
            a.close()


if __name__ == "__main__":
    unittest.main()
