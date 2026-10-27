# SPDX-License-Identifier: LGPL-2.1-or-later
"""F4: FreeCAD-side session (tool bodies) driven in-process, with F3 stubbed."""

import os
import tempfile
import unittest
from unittest import mock

import FreeCAD

from AgentCoreTests.fixtures.mcp import mcp_common as mc
import mcp_stub_diff  # noqa: E402  (on sys.path via mcp_common)
from AgentCore import diff as real_diff
from AgentCore.mcp import session as sess
from AgentCore.mcp.bridge import BridgeError


class TestSession(unittest.TestCase):
    def setUp(self):
        self.s = sess.Session(turn_factory=mcp_stub_diff.AgentTurn)
        self.s.m_open(name="McpSession")

    def tearDown(self):
        self.s.m_close()

    def test_turn_loop_in_process(self):
        self.assertEqual(self.s.m_begin(), {"change": 1, "state": "open"})
        r = self.s.m_exec("b = doc.addObject('Part::Box', 'Box')\nb.Length = 4\nresult = b.Length")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["result"], 4.0)
        self.assertEqual(r["policy"]["static_flags"], [])
        p = self.s.m_propose(summary="add box")
        self.assertEqual(p["status"], "pending_human_approval")
        self.assertEqual(p["change_set"]["added"], ["Box"])
        with self.assertRaises(BridgeError) as cm:
            self.s.m_exec("doc.Box.Length = 5")
        self.assertEqual(cm.exception.code, "change_proposed")
        with self.assertRaises(BridgeError) as cm:
            self.s.m_begin()
        self.assertEqual(cm.exception.code, "change_pending")
        self.s.m_begin(discard_pending=True)  # rejects the proposal
        self.assertIsNone(self.s.doc.getObject("Box"))

    def test_exec_requires_change(self):
        with self.assertRaises(BridgeError) as cm:
            self.s.m_exec("x = 1")
        self.assertEqual(cm.exception.code, "no_change")

    def test_exec_error_and_soft_timeout_keep_session(self):
        self.s.m_begin()
        r = self.s.m_exec("raise ValueError('boom')")
        self.assertEqual(r["status"], "error")
        self.assertIn("boom", r["error"])
        r = self.s.m_exec("while True:\n    pass", timeout=0.5)
        self.assertEqual(r["status"], "timeout")
        r = self.s.m_exec("result = 2 + 2")
        self.assertEqual(r["result"], 4)

    def test_runtime_flags(self):
        self.s.m_begin()
        with tempfile.TemporaryDirectory() as d:
            code = "import os\nwith open(os.path.join(%r, 'x.txt'), 'w') as f:\n    f.write('1')" % d
            r = self.s.m_exec(code)
        self.assertTrue(r["ok"])
        self.assertIn("file", {f["category"] for f in r["policy"]["static_flags"]})
        self.assertTrue(any(e["category"] == "file" and "x.txt" in e["detail"] for e in r["policy"]["runtime_events"]))

    def test_strict_policy_refuses_flagged_code(self):
        self.s.policy_mode = "strict"
        self.s.m_begin()
        with self.assertRaises(BridgeError) as cm:
            self.s.m_exec("import subprocess")
        self.assertEqual(cm.exception.code, "policy_refused")

    def test_approval_never_reaches_the_turn(self):
        self.s.m_begin()
        for m in ("accept", "approve", "apply", "accept_change"):
            with self.assertRaises(BridgeError) as cm:
                self.s.handle(m, {"token": "guess"})
            self.assertEqual(cm.exception.code, "approval_refused")
        with self.assertRaises(BridgeError) as cm:
            self.s.m_exec("import AgentCore\nturn = None\nturn.accept('t')")
        self.assertEqual(cm.exception.code, "policy_refused")
        with self.assertRaises(BridgeError) as cm:
            self.s.handle("frob", {})
        self.assertEqual(cm.exception.code, "unknown_method")
        # the stub turn itself refuses a wrong token: nobody headless holds one
        with self.assertRaises(PermissionError):
            self.s.turn.accept("guess")

    def test_diff_unavailable_without_agent_turn(self):
        s = sess.Session()
        s.m_open(name="McpNoDiff")
        try:
            with mock.patch.dict(os.environ, {sess.DIFF_MODULE_ENV: ""}), \
                    mock.patch.object(real_diff, "AgentTurn", None, create=True):
                with self.assertRaises(BridgeError) as cm:
                    s.m_begin()
            self.assertEqual(cm.exception.code, "diff_unavailable")
            self.assertIn("F3", cm.exception.message)
        finally:
            s.m_close()

    def test_real_diff_when_present(self):
        """After F3 merges, begin/propose run on the real AgentTurn; before, they report diff_unavailable."""
        s = sess.Session()
        s.m_open(name="McpRealDiff")
        try:
            with mock.patch.dict(os.environ, {sess.DIFF_MODULE_ENV: ""}):
                if getattr(real_diff, "AgentTurn", None) is None:
                    with self.assertRaises(BridgeError) as cm:
                        s.m_begin()
                    self.assertEqual(cm.exception.code, "diff_unavailable")
                    return
                s.m_begin()
                self.assertTrue(s.m_exec("doc.addObject('Part::Box', 'Box')")["ok"])
                p = s.m_propose()
                self.assertEqual(p["status"], "pending_human_approval")
                self.assertIsInstance(p["change_set"], dict)
        finally:
            s.m_close()

    def test_state_describe_selection_export(self):
        self.s.m_begin()
        self.s.m_exec("b = doc.addObject('Part::Box', 'Box')\nb.Length = 7")
        st = self.s.m_state(page=0, page_size=1)
        self.assertEqual(st["paging"]["total"], 1)
        d = self.s.m_describe("Box:Face1")
        self.assertEqual(d["element_type"], "face")
        with self.assertRaises(BridgeError):
            self.s.m_describe("Nope:Face1")
        sel = self.s.m_selection()
        self.assertTrue(sel["headless"])
        self.assertEqual(sel["selection"], [])
        with tempfile.TemporaryDirectory() as d:
            for ext, fmt in ((".step", "STEP"), (".brep", "BREP"), (".FCStd", "FCStd")):
                r = self.s.m_export_file(os.path.join(d, "out" + ext))
                self.assertEqual(r["format"], fmt)
                self.assertGreater(os.path.getsize(r["path"]), 100)
            imp = self.s.m_import_file(os.path.join(d, "out.brep"))
            self.assertEqual(len(imp["imported"]), 1)
            with self.assertRaises(BridgeError):
                self.s.m_export_file(os.path.join(d, "out.obj"))


if __name__ == "__main__":
    unittest.main()
