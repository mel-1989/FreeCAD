# SPDX-License-Identifier: LGPL-2.1-or-later
"""F3: reject never discards a change the turn did not make, and never leaves a half-reverted document."""

import os
import shutil
import sys
import tempfile
import unittest

import FreeCAD as App

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "diff")
if FIX not in sys.path:
    sys.path.insert(0, FIX)

import diff_builders as db  # noqa: E402
from AgentCore.diff import AgentTurn, ApplyAuthority, RejectFailed, SnapshotStore, TurnStale  # noqa: E402
from AgentCore.diff import compare  # noqa: E402


def frozen(*docs):
    """Byte-level state of documents plus their undo and redo stacks."""
    return [(compare.raw_state(d), d.UndoCount, list(d.UndoNames), d.RedoCount, list(d.RedoNames)) for d in docs]


class TestRejectNeverDiscardsLaterWork(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.auth = ApplyAuthority.install()

    @classmethod
    def tearDownClass(cls):
        cls.auth.uninstall()

    def setUp(self):
        self.doc = db.build_holed_block(f"f3_stale_{self._testMethodName[-18:]}")
        self.doc.clearUndos()
        self.t = None

    def tearDown(self):
        db.close(self.doc)

    def propose(self):
        with AgentTurn(self.doc, label="enlarge", verify=False) as t:
            self.doc.Block.Length = 50
        return t

    def test_human_transaction_on_top_is_refused_untouched(self):
        t = self.propose()
        self.doc.openTransaction("human")
        self.doc.Block.Placement.Base = App.Vector(5, 5, 5)
        self.doc.commitTransaction()
        self.doc.recompute()
        before = frozen(self.doc)
        for _ in range(2):                                   # a second click refuses again, never "rejected"
            with self.assertRaises(TurnStale) as cm:
                t.reject()
            self.assertIn("undo your own edits", str(cm.exception))
            self.assertEqual(frozen(self.doc), before)
            self.assertEqual(t.status, "proposed")
        self.assertIsNone(t.result)
        self.assertEqual(self.doc.Block.Placement.Base, App.Vector(5, 5, 5))
        self.doc.undo()                                      # human undoes their own edit, retry works
        self.doc.recompute()
        res = t.reject()
        self.assertTrue(res["exact"] and res["equal"], res["differences"])
        self.assertEqual((t.status, self.doc.Block.Length.Value), ("rejected", 40))

    def test_untransacted_human_edit_is_refused_untouched(self):
        t = self.propose()
        self.doc.Block.Height = 11
        self.doc.recompute()
        before = frozen(self.doc)
        with self.assertRaises(TurnStale):
            t.reject()
        self.assertEqual(frozen(self.doc), before)
        self.assertEqual((t.status, self.doc.Block.Height.Value, self.doc.Block.Length.Value), ("proposed", 11, 50))
        self.doc.Block.Height = 10                           # back to the reviewed state: reject goes through
        self.doc.recompute()
        res = t.reject()
        self.assertTrue(res["equal"], res["differences"])
        self.assertEqual((self.doc.Block.Length.Value, self.doc.Block.Height.Value), (40, 10))

    def test_user_text_cannot_pose_as_an_exempt_path(self):
        # the exemptions (Touched flags, /document/*) are judged on the difference's path, never on its values
        for prop, text in (("Label", "x]/document/y"), ("Label2", "]/document/"), ("Label", "Touched")):
            t = self.propose()
            setattr(self.doc.Block, prop, text)
            before = frozen(self.doc)
            with self.assertRaises(TurnStale):
                t.reject()
            self.assertEqual(frozen(self.doc), before)
            self.assertEqual((t.status, getattr(self.doc.Block, prop)), ("proposed", text))
            setattr(self.doc.Block, prop, "Block" if prop == "Label" else "")
            t.reject()
            self.assertEqual((t.status, self.doc.Block.Length.Value), ("rejected", 40))
            self.doc.clearUndos()

    def test_user_keyed_value_cannot_pose_as_a_touched_flag(self):
        # a PropertyMap key "state" dumps under .../value/state; only the object's own state list is exempt
        self.doc.Block.addProperty("App::PropertyMap", "m", "Test", "")
        self.doc.Block.m = {"state": "a"}
        self.doc.recompute()
        self.doc.clearUndos()
        for text in ("Touched", "my Touched note"):
            t = self.propose()
            self.doc.Block.m = {"state": text}
            before = frozen(self.doc)
            with self.assertRaises(TurnStale):
                t.reject()
            self.assertEqual(frozen(self.doc), before)
            self.assertEqual((t.status, self.doc.Block.m), ("proposed", {"state": text}))
            self.doc.Block.m = {"state": "a"}
            t.reject()
            self.assertEqual((t.status, self.doc.Block.Length.Value), ("rejected", 40))
            self.doc.clearUndos()

    def test_human_undo_of_the_turn_then_reject_still_works(self):
        t = self.propose()
        self.doc.undo()                                      # nothing but the turn's own entry moved
        res = t.reject()
        self.assertTrue(res["equal"], res["differences"])
        self.assertEqual(self.doc.RedoCount, 0)

    def _refused_keeping(self, t):
        before = frozen(self.doc)
        with self.assertRaises(TurnStale):
            t.reject()
        self.assertEqual(frozen(self.doc), before)
        self.assertEqual((t.status, t.result), ("proposed", None))

    def test_undo_of_the_turn_then_untransacted_edit_is_refused(self):
        t = self.propose()
        self.doc.undo()
        self.doc.recompute()
        self.doc.Block.Height = 11
        self.doc.recompute()
        self._refused_keeping(t)
        self.assertEqual((self.doc.Block.Height.Value, self.doc.Block.Length.Value), (11, 40))
        self.doc.Block.Height = 10                           # back to the before-state: reject goes through
        self.doc.recompute()
        res = t.reject()
        self.assertTrue(res["equal"], res["differences"])
        self.assertEqual(self.doc.RedoCount, 0)

    def test_undo_of_the_turn_then_new_object_is_refused(self):
        t = self.propose()
        self.doc.undo()
        self.doc.addObject("Part::Box", "Mine")
        self.doc.recompute()
        self._refused_keeping(t)
        self.assertIsNotNone(self.doc.getObject("Mine"))

    def test_undo_of_the_turn_then_edit_of_the_same_property_is_refused(self):
        t = self.propose()
        self.doc.undo()
        self.doc.Block.Length = 77
        self.doc.recompute()
        self._refused_keeping(t)
        self.assertEqual(self.doc.Block.Length.Value, 77)

    def test_partial_undo_of_a_two_segment_turn(self):
        with AgentTurn(self.doc, label="two", verify=False) as t:
            self.doc.openTransaction("s1")
            self.doc.Block.Length = 50
            self.doc.commitTransaction()
            self.doc.openTransaction("s2")
            self.doc.Block.Width = 33
            self.doc.commitTransaction()
        self.doc.undo()
        self.doc.recompute()
        self.doc.Block.Height = 12                           # untransacted, on top of a half-undone turn
        self.doc.recompute()
        self._refused_keeping(t)
        self.assertEqual(self.doc.Block.Height.Value, 12)

    def test_undo_of_the_turn_leaving_only_touched_flags_still_rejects(self):
        t = self.propose()
        self.doc.undo()
        self.doc.Hole.touch()
        res = t.reject()
        self.assertTrue(res["equal"], res["differences"])
        self.assertEqual(self.doc.Block.Length.Value, 40)

    def test_trimmed_stack_without_human_edits_rejects_from_the_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            # use_guard=False: only the Python fallback splits into more entries than UndoMaxStackSize (20)
            with AgentTurn(self.doc, label="many", verify=False, store=SnapshotStore(tmp), use_guard=False) as t:
                for i in range(25):
                    self.doc.openTransaction(f"s{i}")
                    self.doc.Block.Length = 41 + i
                    self.doc.commitTransaction()
            self.assertFalse(t.change_set()["transactions"]["undo_complete"])
            res = t.reject()                                 # no human edit anywhere: not stale
            self.assertEqual((res["method"], self.doc.Block.Length.Value), ("snapshot", 40))
            self.assertTrue(res["equal"], res["differences"])

    def test_trimmed_stack_without_snapshot_rejects_from_the_capture(self):
        with AgentTurn(self.doc, label="many", verify=False, use_guard=False) as t:
            for i in range(25):
                self.doc.openTransaction(f"s{i}")
                self.doc.Block.Length = 41 + i
                self.doc.commitTransaction()
        res = t.reject()
        self.assertTrue(res["equal"], res["differences"])
        self.assertIn("capture", res["method"])
        self.assertEqual(self.doc.Block.Length.Value, 40)

    def test_reject_after_save_close_and_reopen_is_a_clear_refusal(self):
        t = self.propose()
        path = os.path.join(tempfile.mkdtemp(prefix="f3rc"), "x.FCStd")
        try:
            self.doc.saveAs(path)
            App.closeDocument(self.doc.Name)
            doc2 = App.openDocument(path)
            with self.assertRaises(TurnStale) as cm:
                t.reject()
            self.assertIn("closed", str(cm.exception))
            self.assertEqual(doc2.Block.Length.Value, 50)
            App.closeDocument(doc2.Name)
        finally:
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)
        self.doc = db.build_holed_block("f3_stale_after_close")   # tearDown closes this one

    def test_failure_mid_reject_restores_the_after_state(self):
        t = self.propose()
        before = frozen(self.doc)
        t._drop_redos = lambda rec, force=False: (_ for _ in ()).throw(RuntimeError("boom"))
        with self.assertRaises(RejectFailed) as cm:
            t.reject()
        self.assertIn("still proposed", str(cm.exception))
        self.assertEqual(self.doc.Block.Length.Value, 50)    # not half-reverted
        self.assertEqual(t.status, "proposed")
        self.assertIsNone(t.result)
        self.assertEqual(frozen(self.doc)[0][1:3], before[0][1:3])   # the turn's undo entry is back
        self.assertEqual(self.doc.RedoCount, 0)
        del t._drop_redos                                    # the fault clears: the same turn rejects normally
        res = t.reject()
        self.assertTrue(res["equal"], res["differences"])
        self.assertEqual(self.doc.Block.Length.Value, 40)

    def test_failure_after_the_redo_stack_was_emptied_falls_back_to_the_capture(self):
        t = self.propose()
        t._exact = lambda rec: (_ for _ in ()).throw(RuntimeError("late failure"))   # after rewind and redo drop
        with self.assertRaises(RejectFailed):
            t.reject()
        self.assertEqual(self.doc.Block.Length.Value, 50)
        self.assertEqual((t.status, t.result), ("proposed", None))
        self.assertEqual(compare.differences(t._main()["after"], compare.snapshot_dump(self.doc)), [])
        del t._exact
        res = t.reject()                                   # the capture path still rejects it
        self.assertTrue(res["equal"], res["differences"])
        self.assertEqual(self.doc.Block.Length.Value, 40)


class TestRejectAcrossDocuments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.auth = ApplyAuthority.install()

    @classmethod
    def tearDownClass(cls):
        cls.auth.uninstall()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="f3rs")
        self.src, self.asm = db.build_xlink_pair(self.tmp)
        self.src.clearUndos()
        self.asm.clearUndos()

    def tearDown(self):
        db.close(self.asm, self.src)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_edit_in_the_linked_document_only_refuses_both(self):
        with AgentTurn(self.asm, label="cross", verify=False) as t:
            self.asm.LinkBox.Placement.Base = App.Vector(1, 2, 3)
            self.asm.LinkBox.LinkedObject.Length = 20          # lands in the linked document
        self.src.openTransaction("human")
        self.src.SrcCyl.Height = 99
        self.src.commitTransaction()
        self.src.recompute()
        before = frozen(self.src, self.asm)
        with self.assertRaises(TurnStale) as cm:
            t.reject()
        self.assertIn(self.src.Name, str(cm.exception))
        self.assertEqual(frozen(self.src, self.asm), before)   # neither document moved
        self.assertEqual((t.status, self.src.SrcCyl.Height.Value, self.asm.LinkBox.Placement.Base),
                         ("proposed", 99, App.Vector(1, 2, 3)))
        self.src.undo()
        res = t.reject()
        self.assertTrue(res["equal"], res["differences"])

    def test_undo_then_untransacted_edit_in_the_linked_document_refuses(self):
        with AgentTurn(self.asm, label="cross", verify=False) as t:
            self.asm.LinkBox.LinkedObject.Length = 20
        self.src.undo()
        self.asm.undo()
        self.src.SrcCyl.Height = 77
        self.src.recompute()
        before = frozen(self.src, self.asm)
        with self.assertRaises(TurnStale):
            t.reject()
        self.assertEqual(frozen(self.src, self.asm), before)
        self.assertEqual(self.src.SrcCyl.Height.Value, 77)

    def test_trimmed_cross_document_turn_rejects_from_snapshots(self):
        store = SnapshotStore(os.path.join(self.tmp, "snaps"))
        with AgentTurn(self.asm, label="cross", verify=False, store=store, use_guard=False) as t:
            for i in range(25):
                self.src.openTransaction(f"s{i}")
                self.src.SrcBox.Length = 21 + i
                self.src.commitTransaction()
                self.asm.openTransaction(f"a{i}")
                self.asm.LinkBox.Placement.Base = App.Vector(i + 1, 0, 0)
                self.asm.commitTransaction()
        open_before = sorted(App.listDocuments())
        res = t.reject()                                     # no human edit anywhere: not stale
        self.assertEqual(res["methods"][self.asm.Name], "snapshot")
        self.assertTrue(res["equal"], res["differences"])
        self.assertEqual(sorted(App.listDocuments()), open_before)
        self.assertEqual((self.src.SrcBox.Length.Value, self.asm.LinkBox.Placement.Base), (12, App.Vector(0, 50, 0)))

    def test_failure_in_the_second_document_puts_the_first_back(self):
        with AgentTurn(self.asm, label="cross", verify=False) as t:
            self.asm.LinkBox.Placement.Base = App.Vector(1, 2, 3)
            self.asm.LinkBox.LinkedObject.Length = 20
        real = t._rewind
        calls = []

        def flaky(rec):
            calls.append(rec["name"])
            if len(calls) == 2:
                raise RuntimeError("second document fails")
            return real(rec)

        t._rewind = flaky
        with self.assertRaises(RejectFailed):
            t.reject()
        self.assertEqual(self.asm.LinkBox.Placement.Base, App.Vector(1, 2, 3))   # first document is not reverted
        self.assertEqual(self.src.SrcBox.Length.Value, 20)
        self.assertEqual(t.status, "proposed")
        del t._rewind
        self.assertTrue(t.reject()["equal"])


if __name__ == "__main__":
    unittest.main()
