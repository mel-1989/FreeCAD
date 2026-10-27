# SPDX-License-Identifier: LGPL-2.1-or-later
"""F3: before-state .FCStd snapshots and multi-turn revert."""

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
from AgentCore.diff import AgentTurn, ApplyAuthority, SnapshotStore, differences  # noqa: E402
from AgentCore.diff.turn import _dump  # noqa: E402


def settled(doc):
    doc.recompute()
    return _dump(doc)


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.auth = ApplyAuthority.install()

    @classmethod
    def tearDownClass(cls):
        cls.auth.uninstall()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="f3s")
        self.store = SnapshotStore(os.path.join(self.tmp, "snaps"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def accepted(self, doc, label, fn):
        with AgentTurn(doc, label=label, verify=False, store=self.store) as t:
            fn(doc)
        t.accept(self.auth.mint(t))
        return t


def _t1(doc):
    doc.Block.Length = 45


def _t2(doc):
    doc.openTransaction("GUI command")
    doc.Hole.Radius = 4
    doc.commitTransaction()
    doc.addObject("Part::Sphere", "Ball")


def _t3(doc):
    doc.removeObject("Ball")
    doc.Block.Height = 14


class TestMultiTurnRevert(_Base):
    def setUp(self):
        super().setUp()
        self.doc = db.build_holed_block("f3_snap")
        self.doc.clearUndos()

    def tearDown(self):
        db.close(self.doc)
        super().tearDown()

    def test_snapshot_written_per_turn(self):
        t1 = self.accepted(self.doc, "t1", _t1)
        e = self.store.entry(t1.turn_id)
        self.assertTrue(os.path.isfile(e["path"]))
        self.assertTrue(e["path"].endswith(f"{self.doc.Name}.FCStd"))
        self.assertEqual(e["status"], "accepted")
        self.assertEqual(self.doc.FileName, "")           # saveCopy never rebinds the live document
        self.assertEqual(SnapshotStore(self.store.root).entry(t1.turn_id)["undo_name"], t1.undo_name)

    def test_revert_two_turns_by_undo(self):
        t1 = self.accepted(self.doc, "t1", _t1)
        t2 = self.accepted(self.doc, "t2", _t2)
        t3 = self.accepted(self.doc, "t3", _t3)
        self.assertEqual(self.doc.UndoNames, [t3.undo_name, t2.undo_name, t1.undo_name])
        self.assertEqual(self.store.revert(self.doc, t2.turn_id), "undo")
        self.assertEqual(differences(t2.before, settled(self.doc)), [])
        self.assertEqual(differences(t1.after, settled(self.doc)), [])
        self.assertEqual(self.doc.UndoNames, [t1.undo_name])
        self.assertEqual([e["status"] for e in self.store.entries], ["accepted", "reverted", "reverted"])

    def test_revert_by_snapshot_when_stack_moved_on(self):
        t1 = self.accepted(self.doc, "t1", _t1)
        self.accepted(self.doc, "t2", _t2)
        self.accepted(self.doc, "t3", _t3)
        self.doc.openTransaction("human edit")
        self.doc.Block.Width = 33
        self.doc.commitTransaction()
        self.doc.recompute()
        human = settled(self.doc)
        n = self.doc.UndoCount
        self.assertEqual(self.store.revert(self.doc, t1.turn_id), "snapshot")
        self.assertEqual(differences(t1.before, settled(self.doc)), [])
        self.assertEqual(self.doc.UndoCount, n + 1)        # the revert is one undo entry
        self.assertTrue(self.doc.UndoNames[0].startswith("Revert to before"))
        self.doc.undo()
        self.assertEqual(differences(human, settled(self.doc)), [])

    def test_revert_after_reopen(self):
        path = os.path.join(self.tmp, "f3_snap.FCStd")
        self.doc.saveAs(path)
        t1 = self.accepted(self.doc, "t1", _t1)
        self.accepted(self.doc, "t2", _t2)
        self.doc.save()
        App.closeDocument(self.doc.Name)
        self.doc = App.openDocument(path)
        self.doc.UndoMode = 1
        store = SnapshotStore(self.store.root)             # index reloaded from disk
        self.assertEqual(store.revert(self.doc, t1.turn_id), "snapshot")
        self.assertEqual(differences(t1.before, settled(self.doc)), [])


class TestSnapshotCorpus(_Base):
    """Accept a scripted turn on every F1 fixture, move the stack on, revert from the snapshot."""

    def test_snapshot_revert_on_every_f1_fixture(self):
        done = {}
        for key in db.F1_FIXTURES:
            doc = db.open_f1_fixture(key)
            try:
                with AgentTurn(doc, label=key, verify=False, store=self.store) as t:
                    db.scripted_turn(doc)
                t.accept(self.auth.mint(t))
                doc.openTransaction("human edit")
                doc.addObject("Part::Box", "HumanBox")
                doc.commitTransaction()
                method = self.store.revert(doc, t.turn_id)
                self.assertEqual(method, "snapshot", key)
                diffs = differences(t.before, settled(doc))
                for d in diffs:  # only solver history that a plain save and reopen loses too
                    self.assertIn("/detail/dependent_parameters", d, f"{key}: {diffs}")
                done[key] = len(diffs)
            finally:
                db.close(doc)
        self.assertEqual(sorted(done), sorted(db.F1_FIXTURES))
        print(f"F3 snapshot corpus: {len(done)} fixtures, solver-history drift lines {done}")

    def test_dependent_parameters_are_not_persistent(self):
        """Why the corpus test tolerates ``dependent_parameters``: FreeCAD's
        own save and reopen loses it for the over-constrained sketch."""
        doc = db.open_f1_fixture("built_sketch_states")
        path = os.path.join(self.tmp, "sk.FCStd")
        try:
            doc.recompute()
            live = doc.Over.getGeometryWithDependentParameters()
            doc.saveCopy(path)
            again = App.openDocument(path, True, True)
            try:
                again.recompute()
                self.assertTrue(live)
                self.assertNotEqual(again.getObject("Over").getGeometryWithDependentParameters(), live)
            finally:
                App.closeDocument(again.Name)
        finally:
            db.close(doc)


if __name__ == "__main__":
    unittest.main()
