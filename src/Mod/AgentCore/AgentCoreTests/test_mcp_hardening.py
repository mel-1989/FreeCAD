# SPDX-License-Identifier: LGPL-2.1-or-later
"""F4 hardening on the real F3 AgentTurn.

- import_export: no export of an open or pending change unless pre_turn=true
  (which exports the before-state), no overwrite without overwrite=true, never
  the session's own source file, and a silent no-op write is an error.
- exec_code that tampers with the change (undo inside it, closing or
  self-accepting the turn) aborts the change; the bookkeeping follows F3.
- the verifier's self-approval probe, run in a sealed worker, is refused.
- the legitimate path: a human approves in the approver process, the worker
  accepts, and the server reports the decision.
"""

import hashlib
import json
import os
import shutil
import signal
import time
import unittest
from unittest import mock

import FreeCAD as App

from AgentCoreTests.fixtures.mcp import mcp_common as mc
from AgentCoreTests.test_diff_approver import Approver, short_dir
from AgentCore.mcp import session as sess
from AgentCore.mcp.bridge import BridgeError


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _length_in(path):
    d = App.openDocument(path, hidden=True)
    try:
        return float(d.getObject("Box").Length.Value)
    finally:
        App.closeDocument(d.Name)


class _RealSession(unittest.TestCase):
    def setUp(self):
        self.dir = short_dir("acmcp-h-")
        self.fixture = mc.save_fixtures(["box"], self.dir)["box"]
        self.s = sess.Session()
        with mock.patch.dict(os.environ, {sess.DIFF_MODULE_ENV: ""}):
            self.s.m_open(path=self.fixture)
        self.l0 = float(self.s.doc.getObject("Box").Length.Value)
        self.env = mock.patch.dict(os.environ, {sess.DIFF_MODULE_ENV: ""})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.s.m_close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _err(self, fn, *a, **kw):
        with self.assertRaises(BridgeError) as cm:
            fn(*a, **kw)
        return cm.exception.code


class TestExportGuards(_RealSession):
    def test_export_refused_during_change_and_pre_turn_exports_before_state(self):
        s, out = self.s, lambda n: os.path.join(self.dir, n)
        s.m_begin()
        s.m_exec("doc.getObject('Box').Length = 77")
        self.assertEqual(self._err(s.m_export_file, out("open.FCStd")), "change_pending")
        self.assertFalse(os.path.exists(out("open.FCStd")))
        r = s.m_export_file(out("pre.FCStd"), pre_turn=True)
        self.assertTrue(r["pre_turn"])
        self.assertEqual(_length_in(out("pre.FCStd")), self.l0)        # the before-state, not 77
        self.assertEqual(s.doc.getObject("Box").Length.Value, 77)       # the live change is untouched
        s.m_propose()
        for ext in (".FCStd", ".step", ".brep"):
            self.assertEqual(self._err(s.m_export_file, out("pending" + ext)), "change_pending")
            self.assertFalse(os.path.exists(out("pending" + ext)))
        r = s.m_export_file(out("pre.step"), pre_turn=True)
        self.assertGreater(r["bytes"], 100)
        s.m_begin(discard_pending=True)
        s.m_propose()
        s.m_begin(discard_pending=True)
        s._reject_quietly()
        r = s.m_export_file(out("after.FCStd"))
        self.assertFalse(r["pre_turn"])
        self.assertEqual(_length_in(out("after.FCStd")), self.l0)

    def test_no_overwrite_without_flag_and_never_the_source(self):
        s, target = self.s, os.path.join(self.dir, "x.step")
        s.m_export_file(target)
        h = _sha(target)
        self.assertEqual(self._err(s.m_export_file, target), "file_exists")
        self.assertEqual(_sha(target), h)
        r = s.m_export_file(target, overwrite=True)
        self.assertTrue(r["overwrote"])
        src = _sha(self.fixture)
        for kw in ({}, {"overwrite": True}):
            self.assertEqual(self._err(s.m_export_file, self.fixture, **kw), "refused_target")
        self.assertEqual(_sha(self.fixture), src)
        self.assertEqual(self._err(s.m_export_file, os.path.join(self.dir, "x.obj")), "unsupported_format")


class TestTamperingAbortsChange(_RealSession):
    """The static scan is advisory; these run with it bypassed, as determined code would."""

    def setUp(self):
        super().setUp()
        self.scan = mock.patch.object(sess.policy, "decide", return_value=(True, ""))
        self.scan.start()

    def tearDown(self):
        self.scan.stop()
        super().tearDown()

    def test_undo_inside_exec_aborts_and_keeps_human_history(self):
        s = self.s
        s.doc.openTransaction("human")
        s.doc.getObject("Box").Width = 33
        s.doc.commitTransaction()
        s.m_begin()
        r = s.m_exec("b = doc.getObject('Box')\nb.Length = 50\ndoc.undo()\ndoc.undo()\nb.Height = 12")
        self.assertEqual(r["status"], "change_aborted", r)
        self.assertTrue(any("undo" in x for x in r["change_aborted"]), r)
        self.assertIsNone(s.turn)
        self.assertEqual(s.m_info()["change"], None)
        self.assertEqual(s.last_decision["decision"], "rejected")
        b = s.doc.getObject("Box")
        self.assertEqual((b.Length.Value, b.Width.Value), (self.l0, 33))
        self.assertEqual(list(s.doc.UndoNames), ["human"])

    def test_turn_closed_by_exec_code_is_aborted(self):
        s = self.s
        s.m_begin()
        r = s.m_exec("import gc\nfrom AgentCore.diff import turn as T\n"
                     "t = [o for o in gc.get_objects() if isinstance(o, T.AgentTurn) and o.status == 'open'][0]\n"
                     "doc.getObject('Box').Length = 66\nt.__exit__(None, None, None)")
        self.assertEqual(r["status"], "change_aborted", r)
        self.assertIsNone(s.turn)
        self.assertEqual(s.doc.getObject("Box").Length.Value, self.l0)


class TestThroughSealedWorker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = short_dir("acmcp-w-")
        cls.fixture = mc.save_fixtures(["box"], cls.dir)["box"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def _length(self, s, doc):
        st, _ = mc.call(s, "get_state", doc=doc)
        box = next(o for o in st["objects"] if o["name"] == "Box")
        v = next(p["value"] for p in box["properties"] if p["name"] == "Length")
        return v["value"] if isinstance(v, dict) else v

    def test_verifier_self_approval_probe_is_refused(self):
        """The F4 verifier's probe (install an authority, find the turn, close it, accept via getattr),
        hidden from the static scan by hex-encoding, run in a real sealed session worker."""
        probe = (
            "import gc\nfrom AgentCore.diff import authority as A\nfrom AgentCore.diff import turn as T\n"
            "out = {}\n"
            "try:\n    au = A.ApplyAuthority.install(); out['install'] = 'ok'\n"
            "except Exception as e:\n    out['install'] = str(e)\n"
            "t = [o for o in gc.get_objects() if isinstance(o, T.AgentTurn) and o.status == 'open'][0]\n"
            "doc.getObject('Box').Length = 77\n"
            "t.__exit__(None, None, None)\n"
            "tok = object.__new__(A.ApplyToken)\n"
            "tok.turn_id, tok.digest, tok.nonce, tok.mac = t.turn_id, t.digest(), '00' * 16, 'ab' * 32\n"
            "try:\n    getattr(t, 'acc' + 'ept')(tok); out['accept'] = 'ok'\n"
            "except Exception as e:\n    out['accept'] = type(e).__name__ + ': ' + str(e)\n"
            "out['status'] = t.status\nresult = out\n")
        code = f"exec(bytes.fromhex({probe.encode().hex()!r}).decode())"
        s = mc.make_server(stub=False)
        try:
            doc = mc.call(s, "open_document", path=self.fixture)[0]["doc"]
            l0 = self._length(s, doc)
            mc.call(s, "begin_change", doc=doc)
            r, err = mc.call(s, "exec_code", doc=doc, code=code)
            self.assertTrue(err, r)
            self.assertIn("agent code", r["result"]["install"])
            self.assertTrue(r["result"]["accept"].startswith("TokenRefused"), r["result"])
            self.assertEqual(r["result"]["status"], "proposed")
            self.assertEqual(r["status"], "change_aborted")
            docs, _ = mc.call(s, "list_documents")
            entry = docs["documents"][0]
            self.assertIsNone(entry["change"])                         # bookkeeping follows the turn
            self.assertEqual(entry["last_decision"]["decision"], "rejected")
            self.assertEqual(self._length(s, doc), l0)
        finally:
            s.close()

    def test_human_approval_in_the_approver_applies_the_change(self):
        a = Approver(self.dir)
        s = mc.make_server(stub=False, approver_socket=a.socket)
        try:
            doc = mc.call(s, "open_document", path=self.fixture)[0]["doc"]
            mc.call(s, "begin_change", doc=doc)
            self.assertFalse(mc.call(s, "exec_code", doc=doc, code="doc.getObject('Box').Length = 15")[1])
            prop, err = mc.call(s, "propose_change", doc=doc, summary="approve me")
            self.assertFalse(err, prop)
            ap, err = mc.call(s, "approve_change", proposal_id=prop["proposal_id"])   # the agent cannot
            self.assertTrue(err)
            time.sleep(1.0)
            entry = mc.call(s, "list_documents")[0]["documents"][0]
            self.assertEqual(entry["change"], "proposed")               # nothing applied without the human
            self.assertTrue(a.say(f"approve {prop['turn_id']} {prop['digest']}").startswith("APPROVED"))
            deadline = time.time() + 20
            while time.time() < deadline:
                entry = mc.call(s, "list_documents")[0]["documents"][0]
                if entry["change"] is None:
                    break
                time.sleep(0.2)
            self.assertEqual(entry["last_decision"]["decision"], "accepted", entry)
            self.assertEqual(a.say(f"status {prop['turn_id']} {prop['digest']}").split()[-1], "spent")
            self.assertEqual(self._length(s, doc), 15)
            out = os.path.join(self.dir, "approved.FCStd")
            r, err = mc.call(s, "import_export", doc=doc, action="export", path=out)
            self.assertFalse(err, r)
            self.assertEqual(_length_in(out), 15)
        finally:
            s.close()
            a.close()


class TestCrashRecoveryCheckpoint(unittest.TestCase):
    """The respawn source is always the last committed state: pre-change while a change is
    pending, post-accept once a human approved it (a stale checkpoint silently reverted accepted work)."""

    @classmethod
    def setUpClass(cls):
        cls.dir = short_dir("acmcp-ck-")
        cls.fixture = mc.save_fixtures(["box"], cls.dir)["box"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    _length = TestThroughSealedWorker._length

    def _kill(self, s, doc):
        w = s.docs[doc].worker
        pid = w.pid
        os.kill(pid, signal.SIGKILL)
        deadline = time.time() + 10
        while w.alive() and time.time() < deadline:
            time.sleep(0.05)
        self.assertFalse(w.alive())
        return pid

    def _propose(self, s, doc, length):
        mc.call(s, "begin_change", doc=doc)
        self.assertFalse(mc.call(s, "exec_code", doc=doc, code=f"doc.getObject('Box').Length = {length}")[1])
        prop, err = mc.call(s, "propose_change", doc=doc, summary="x")
        self.assertFalse(err, prop)
        return prop

    def _approve(self, s, a, doc, prop):
        self.assertTrue(a.say(f"approve {prop['turn_id']} {prop['digest']}").startswith("APPROVED"))
        deadline = time.time() + 20
        while time.time() < deadline:
            entry = mc.call(s, "list_documents")[0]["documents"][0]
            if entry["change"] is None:
                break
            time.sleep(0.2)
        self.assertEqual(entry["last_decision"]["decision"], "accepted", entry)
        self.assertNotIn("checkpoint_error", entry["last_decision"])

    def test_crash_after_approved_accept_restores_the_accepted_state(self):
        a = Approver(self.dir)
        s = mc.make_server(stub=False, approver_socket=a.socket)
        try:
            doc = mc.call(s, "open_document", path=self.fixture)[0]["doc"]
            l0 = self._length(s, doc)
            self._approve(s, a, doc, self._propose(s, doc, 15))
            self.assertEqual(self._length(s, doc), 15)
            pid = self._kill(s, doc)
            self.assertEqual(self._length(s, doc), 15)           # not l0
            self.assertNotEqual(s.docs[doc].worker.pid, pid)
            self.assertNotEqual(l0, 15)
            self._kill(s, doc)                                    # a second crash must not fall back to the source
            self.assertEqual(self._length(s, doc), 15)
        finally:
            s.close()
            a.close()

    def test_crash_during_a_pending_change_restores_the_pre_change_state(self):
        a = Approver(self.dir)
        s = mc.make_server(stub=False, approver_socket=a.socket)
        try:
            doc = mc.call(s, "open_document", path=self.fixture)[0]["doc"]
            l0 = self._length(s, doc)
            self._approve(s, a, doc, self._propose(s, doc, 15))
            self._propose(s, doc, 33)                             # second change stays unapproved
            self._kill(s, doc)
            self.assertEqual(self._length(s, doc), 15)            # last committed state, not 33, not l0
            self.assertNotEqual(l0, 15)
            entry = mc.call(s, "list_documents")[0]["documents"][0]
            self.assertIsNone(entry["change"])
        finally:
            s.close()
            a.close()

    def test_crash_during_first_pending_change_restores_the_original(self):
        s = mc.make_server(stub=False)
        try:
            doc = mc.call(s, "open_document", path=self.fixture)[0]["doc"]
            l0 = self._length(s, doc)
            mc.call(s, "begin_change", doc=doc)
            mc.call(s, "exec_code", doc=doc, code="doc.getObject('Box').Length = 41")
            self._kill(s, doc)
            self.assertEqual(self._length(s, doc), l0)
        finally:
            s.close()


if __name__ == "__main__":
    unittest.main()
