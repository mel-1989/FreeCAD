# SPDX-License-Identifier: LGPL-2.1-or-later
"""F3 hardening: accept re-checks the reviewed state, reject without a
snapshot, undo/redo/clearUndos inside a turn, no redo after reject."""

import os
import sys
import unittest

import FreeCAD as App

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "diff")
if FIX not in sys.path:
    sys.path.insert(0, FIX)

import diff_builders as db  # noqa: E402
from AgentCore.diff import AgentTurn, ApplyAuthority, TurnError  # noqa: E402


def _human(doc, name, obj, prop, value):
    doc.openTransaction(name)
    setattr(getattr(doc, obj), prop, value)
    doc.commitTransaction()


class _Case(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.auth = ApplyAuthority.install()

    @classmethod
    def tearDownClass(cls):
        cls.auth.uninstall()

    def setUp(self):
        self.doc = db.build_holed_block(f"f3_hard_{self._testMethodName[-20:]}")
        self.doc.clearUndos()

    def tearDown(self):
        db.close(self.doc)


class TestAcceptRechecksLiveState(_Case):
    """Defect A: accept kept a change made after the proposal."""

    def test_accept_refuses_a_change_made_after_proposal(self):
        with AgentTurn(self.doc, label="one segment", verify=False) as t:
            self.doc.Block.Length = 77
        tok = self.auth.mint(t)
        self.doc.Block.Height = 99                  # outside any transaction, after review
        self.doc.recompute()
        with self.assertRaises(TurnError) as cm:
            t.accept(tok)
        self.assertIn("reviewed after-state", str(cm.exception))
        self.assertEqual(t.status, "proposed")
        self.doc.Block.Height = 10                  # back to what the human reviewed
        self.doc.recompute()
        res = t.accept(tok)                         # the refused attempt did not spend the token
        self.assertEqual(res["decision"], "accepted")
        self.assertEqual(self.doc.Block.Length.Value, 77)

    def test_accept_refuses_on_the_flatten_path_and_reject_restores(self):
        with AgentTurn(self.doc, label="two segments", verify=False) as t:
            self.doc.Block.Length = 77
            self.doc.openTransaction("nested")
            self.doc.Hole.Radius = 4
            self.doc.commitTransaction()
        tok = self.auth.mint(t)
        self.doc.Block.Height = 99
        self.doc.recompute()
        with self.assertRaises(TurnError):
            t.accept(tok)
        res = t.reject()
        self.assertTrue(res["equal"], res["differences"])
        self.assertEqual((self.doc.Block.Length.Value, self.doc.Block.Height.Value, self.doc.Hole.Radius.Value),
                         (40, 10, 3))

    def test_accept_refuses_a_geometry_only_change(self):
        with AgentTurn(self.doc, label="geo", verify=False) as t:
            self.doc.Block.Length = 77
        tok = self.auth.mint(t)
        import Part
        self.doc.Cut.Shape = Part.makeBox(1, 1, 1)  # output overwritten, no input changed
        self.assertTrue(t.live_differences())
        with self.assertRaises(TurnError):
            t.accept(tok)

    def test_failed_flatten_rolls_back_to_the_before_state(self):
        with AgentTurn(self.doc, label="flatten fails", verify=False) as t:
            self.doc.Block.Length = 77
            self.doc.openTransaction("nested")
            self.doc.Hole.Radius = 4
            self.doc.commitTransaction()
        tok = self.auth.mint(t)
        from AgentCore.diff import transplant
        real = transplant.apply

        def broken(doc, cap, **kw):
            real(doc, cap, **kw)
            doc.getObject("Block").Height = 55       # the replay drifts from the after-state
            return []
        transplant.apply = broken
        try:
            with self.assertRaises(Exception):
                t.accept(tok)
        finally:
            transplant.apply = real
        self.assertEqual(t.status, "rejected")
        self.assertTrue(t.result["equal"], t.result["differences"])
        self.assertEqual((self.doc.Block.Length.Value, self.doc.Block.Height.Value), (40, 10))


class TestRejectWithoutSnapshot(_Case):
    """Defect E: reject raised and left the edits when the stack moved and no store was given."""

    def test_reject_falls_back_to_the_before_capture(self):
        with AgentTurn(self.doc, label="no store", verify=False) as t:
            self.doc.Block.Length = 50
        _human(self.doc, "on top", "Block", "Width", 21)   # the stack no longer ends with the turn
        res = t.reject()
        self.assertEqual(res["method"], "capture")
        self.assertTrue(res["equal"], res["differences"])
        self.assertEqual(self.doc.Block.Length.Value, 40)
        self.assertEqual(t.status, "rejected")

    def test_reject_after_clear_undos_inside_turn(self):
        _human(self.doc, "human", "Block", "Width", 33)
        with self.assertRaises(TurnError):
            with AgentTurn(self.doc, label="clears", verify=False) as t:
                self.doc.Block.Length = 50
                self.doc.clearUndos()
                self.doc.Block.Height = 12
        self.assertEqual(t.status, "rejected")
        self.assertTrue(t.result["equal"], t.result["differences"])
        self.assertEqual((self.doc.Block.Length.Value, self.doc.Block.Height.Value, self.doc.Block.Width.Value),
                         (40, 10, 33))
        self.assertTrue(any("clearUndos" in x for x in t.tampered), t.tampered)


class TestHistoryInsideTurn(_Case):
    """Defect B: undo inside a turn destroyed the human's committed history."""

    def test_undo_inside_turn_keeps_pre_turn_history_and_aborts(self):
        _human(self.doc, "human", "Block", "Width", 33)
        with self.assertRaises(TurnError):
            with AgentTurn(self.doc, label="undoes", verify=False) as t:
                self.doc.Block.Length = 50
                self.doc.undo()                      # its own segment
                self.doc.undo()                      # the human's entry
                self.doc.Block.Height = 12           # would open a transaction and clear redo
        self.assertEqual(t.status, "rejected")
        self.assertTrue(t.result["equal"], t.result["differences"])
        self.assertEqual(self.doc.UndoNames, ["human"])
        self.assertEqual(self.doc.Block.Width.Value, 33)
        self.assertEqual((self.doc.Block.Length.Value, self.doc.Block.Height.Value), (40, 10))
        self.doc.undo()                              # the human can still undo their own edit
        self.assertEqual(self.doc.Block.Width.Value, 30)

    def test_redo_inside_turn_is_tampering(self):
        with self.assertRaises(TurnError):
            with AgentTurn(self.doc, label="redoes", verify=False) as t:
                self.doc.Block.Length = 50
                self.doc.undo()
                self.doc.redo()
        self.assertTrue(any("redo" in x for x in t.tampered), t.tampered)
        self.assertEqual(self.doc.Block.Length.Value, 40)
        self.assertEqual(self.doc.RedoCount, 0)


class TestNoRedoAfterReject(_Case):
    """Defect: reject left the turn's segments on the redo stack."""

    def test_turn_undone_after_proposal_is_not_redoable(self):
        with AgentTurn(self.doc, label="undone later", verify=False) as t:
            self.doc.Block.Length = 50
        self.doc.undo()                              # the turn's entry goes onto the redo stack
        self.assertIn(t.undo_name, self.doc.RedoNames)
        res = t.reject()
        self.assertTrue(res["equal"], res["differences"])
        self.assertNotIn(t.undo_name, self.doc.RedoNames)
        self.doc.redo()
        self.assertEqual(self.doc.Block.Length.Value, 40)

    def test_snapshotless_capture_reject_drops_turn_redo(self):
        with AgentTurn(self.doc, label="segments", verify=False) as t:
            self.doc.Block.Length = 50
            self.doc.openTransaction("nested")
            self.doc.Hole.Radius = 4
            self.doc.commitTransaction()
        self.doc.undo()                              # one of the two segments onto the redo stack
        res = t.reject()
        self.assertTrue(res["equal"], res["differences"])
        self.assertEqual(self.doc.RedoCount, 0)
        self.doc.redo()
        self.assertEqual((self.doc.Block.Length.Value, self.doc.Hole.Radius.Value), (40, 3))


if __name__ == "__main__":
    unittest.main()
