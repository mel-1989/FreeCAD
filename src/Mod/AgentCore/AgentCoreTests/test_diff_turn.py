# SPDX-License-Identifier: LGPL-2.1-or-later
"""F3: one transaction per agent turn; reject, accept, nested transactions."""

import os
import sys
import tempfile
import unittest

import FreeCAD as App

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "diff")
if FIX not in sys.path:
    sys.path.insert(0, FIX)

import diff_builders as db  # noqa: E402
from AgentCore.diff import AgentTurn, ApplyAuthority, SnapshotStore, differences, snapshot_dump  # noqa: E402


def settled(doc):
    if App.getActiveTransaction() is None:
        doc.recompute()
    return snapshot_dump(doc)


def restored(doc, before):
    """Dump after an undo, judged the way ``reject`` judges one: Touched flags
    are cleared when they are the only gap, otherwise one settling recompute.
    (A blanket recompute would re-run objects undo merely touched; Draft's
    WPProxy, for one, rebuilds as a 1 x 1 plane when recomputed headless.)"""
    from AgentCore.diff import transplant
    gaps = differences(before, snapshot_dump(doc), rel_tol=0.0, abs_tol=0.0)
    if all("/state" in g.split(":", 1)[0] and "Touched" in g for g in gaps):
        transplant.align_touched(doc, before)
        return snapshot_dump(doc)
    return settled(doc)


class _AuthCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.auth = ApplyAuthority.install()

    @classmethod
    def tearDownClass(cls):
        cls.auth.uninstall()


class TestRejectCorpus(_AuthCase):
    """Scripted add/edit/delete turns on every F1 fixture, then reject."""

    def test_reject_restores_pre_turn_dump_on_every_f1_fixture(self):
        report, exact = {}, {}
        for key in db.F1_FIXTURES:
            doc = db.open_f1_fixture(key)
            try:
                with AgentTurn(doc, label=f"scripted {key}", verify=False) as t:
                    done = db.scripted_turn(doc)
                cs = t.change_set()
                self.assertTrue(cs["features"]["added"], key)
                self.assertGreaterEqual(cs["transactions"]["segments"], 2, key)  # the nested split happened
                base = (t.base_undo, list(t.base_names))
                res = t.reject()
                report[key] = res
                self.assertTrue(res["equal"], f"{key}: {res['differences'][:5]} after {done}")
                # No tolerance at all: exact F1 dump always; byte-equal persistent
                # properties whenever no settling recompute was needed.
                self.assertEqual(differences(t.before, snapshot_dump(doc), rel_tol=0.0, abs_tol=0.0), [], key)
                if not res["settled"][doc.Name]:
                    self.assertTrue(res["exact"], key)
                exact[key] = (res["exact"], res["settled"][doc.Name])
                self.assertEqual(res["method"], "undo", key)
                self.assertEqual(doc.RedoCount, 0, key)  # the rejected segments cannot be redone
                if "deleted" in done:                 # a pre-existing feature was removed
                    self.assertIn(done["deleted"], [f["name"] for f in cs["features"]["removed"]], key)
                self.assertEqual((doc.UndoCount, list(doc.UndoNames)), base, key)
                self.assertEqual(differences(t.before, settled(doc)), [], key)
            finally:
                db.close(doc)
        self.assertEqual(sorted(report), sorted(db.F1_FIXTURES))
        # the Assembly example re-solves differently on a recompute: reject must not recompute it
        self.assertEqual(exact["upstream_assembly_example"], (True, False))
        self.assertEqual(sum(1 for e, s in exact.values() if not s), len(exact) - 1, exact)
        print(f"F3 reject corpus: (exact, settled) {exact}")

    def test_accept_then_undo_equals_reject_on_every_f1_fixture(self):
        drift = {}
        for key in db.F1_FIXTURES:
            doc = db.open_f1_fixture(key)
            try:
                with AgentTurn(doc, label=f"accept {key}", verify=False) as t:
                    db.scripted_turn(doc)
                base = t.base_undo
                res = t.accept(self.auth.mint(t))
                self.assertTrue(res["flattened"], key)
                self.assertEqual(doc.UndoCount, base + 1, key)  # exactly one undo entry for the turn
                self.assertEqual(doc.UndoNames[0], t.undo_name, key)
                after = snapshot_dump(doc)  # no extra recompute: broken fixtures are not fixed points
                diffs = differences(t.after, after)
                self.assertEqual(diffs, res["detail_drift"], key)
                kinds = {o["name"]: o["type"] for o in after["objects"]}
                cs = t.change_set()
                changed = set(cs["features"]["modified"]) | {f["name"] for f in cs["features"]["added"]}
                for d in diffs:  # only derived data of changed objects may drift (see AgentTurn._flatten)
                    parts = d.split(":", 1)[0].split("/")
                    idx = int(parts[2])
                    self.assertIn(after["objects"][idx]["name"], changed, f"{key}: {d}")
                    if parts[3] == "detail":
                        self.assertTrue(after["objects"][idx]["type"].startswith("TechDraw::"), f"{key}: {d} {kinds}")
                    else:
                        self.assertEqual(parts[3], "state", f"{key}: {d}")
                drift[key] = len(diffs)
                doc.undo()
                self.assertEqual(differences(t.before, restored(doc, t.before)), [], f"{key}: accept-then-undo != reject")
                self.assertEqual(doc.UndoCount, base, key)
            finally:
                db.close(doc)
        self.assertEqual(sorted(drift), sorted(db.F1_FIXTURES))
        print(f"F3 accept corpus: {len(drift)} fixtures, TechDraw detail drift {drift}")


class TestNested(_AuthCase):
    def setUp(self):
        self.doc = db.build_holed_block("f3_nested")
        self.doc.clearUndos()

    def tearDown(self):
        db.close(self.doc)

    def _nested_body(self):
        doc = self.doc
        doc.Block.Length = 41
        doc.openTransaction("GUI command 1")          # like Gui.doCommand under openCommand
        doc.Hole.Radius = 4
        doc.commitTransaction()
        doc.Block.Width = 31                           # after a nested commit: still recorded
        tid = App.setActiveTransaction("GUI command 2")
        doc.addObject("Part::Sphere", "Ball")
        App.closeActiveTransaction(False, tid)
        App.closeActiveTransaction()                   # an empty close must not stop recording either
        doc.Block.Height = 12
        doc.recompute()

    def test_nested_open_still_one_undo_entry(self):
        with AgentTurn(self.doc, label="nested", verify=False) as t:
            self._nested_body()
        cs = t.change_set()
        self.assertIn("GUI command 1", cs["transactions"]["nested_opened"])
        self.assertGreaterEqual(cs["transactions"]["segments"], 3)
        t.accept(self.auth.mint(t))
        self.assertEqual(self.doc.UndoCount, 1)
        self.assertEqual(self.doc.UndoNames, [t.undo_name])
        self.assertEqual((self.doc.Block.Length.Value, self.doc.Block.Width.Value, self.doc.Block.Height.Value),
                         (41, 31, 12))
        self.doc.undo()
        self.assertEqual(differences(t.before, settled(self.doc)), [])
        self.assertEqual(self.doc.Block.Width.Value, 30)
        self.assertIsNone(self.doc.getObject("Ball"))

    def test_nested_reject_records_every_change(self):
        with AgentTurn(self.doc, label="nested reject", verify=False) as t:
            self._nested_body()
        res = t.reject()
        self.assertTrue(res["equal"], res["differences"])
        self.assertEqual(self.doc.Block.Height.Value, 10)

    def test_nested_abort_rolls_back_only_the_nested_part(self):
        with AgentTurn(self.doc, label="nested abort", verify=False) as t:
            self.doc.Block.Length = 45
            self.doc.openTransaction("GUI command that fails")
            self.doc.Hole.Radius = 5
            self.doc.abortTransaction()
            self.doc.Block.Height = 11
        self.assertEqual(self.doc.Hole.Radius.Value, 3)
        cs = t.change_set()
        self.assertEqual(cs["transactions"]["nested_aborts"], 1)
        changed = {(p["object"], p["property"]) for p in cs["properties"]}
        self.assertIn(("Block", "Length"), changed)
        self.assertIn(("Block", "Height"), changed)
        self.assertNotIn(("Hole", "Radius"), changed)
        self.assertTrue(t.reject()["equal"])

    def test_single_segment_turn_is_not_replayed(self):
        with AgentTurn(self.doc, label="plain", verify=False) as t:
            self.doc.Block.Length = 50
        self.assertEqual(t.change_set()["transactions"]["segments"], 1)
        res = t.accept(self.auth.mint(t))
        self.assertFalse(res["flattened"])
        self.assertEqual(self.doc.UndoNames, [t.undo_name])

    def test_exception_in_turn_rejects_and_reraises(self):
        with self.assertRaises(ZeroDivisionError):
            with AgentTurn(self.doc, label="boom", verify=False) as t:
                self.doc.Block.Length = 99
                self.doc.addObject("Part::Sphere", "Ball")
                1 / 0
        self.assertEqual(t.status, "rejected")
        self.assertTrue(t.result["equal"], t.result["differences"])
        self.assertEqual(self.doc.Block.Length.Value, 40)

    def test_turn_leaves_no_recording_behind(self):
        with AgentTurn(self.doc, label="t", verify=False) as t:
            self.doc.Block.Length = 50
        t.accept(self.auth.mint(t))
        n = self.doc.UndoCount
        self.doc.openTransaction("human")
        self.doc.Block.Width = 5
        self.doc.commitTransaction()
        self.doc.Block.Height = 5                       # no observer re-books after the turn
        self.assertFalse(self.doc.HasPendingTransaction)
        self.assertIsNone(App.getActiveTransaction())
        self.assertEqual(self.doc.UndoCount, n + 1)

    def test_undo_stack_overflow_falls_back_to_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SnapshotStore(tmp)
            with AgentTurn(self.doc, label="many commands", verify=False, store=store) as t:
                for i in range(25):                      # more segments than UndoMaxStackSize (20)
                    self.doc.openTransaction(f"cmd {i}")
                    self.doc.Block.Length = 41 + i
                    self.doc.commitTransaction()
            self.assertFalse(t.change_set()["transactions"]["undo_complete"])
            res = t.reject()
            self.assertEqual(res["method"], "snapshot")
            self.assertTrue(res["equal"], res["differences"])
            self.assertEqual(self.doc.Block.Length.Value, 40)


class TestRedoAfterReject(_AuthCase):
    """A rejected turn must not come back through redo (that would bypass the token)."""

    def setUp(self):
        self.doc = db.build_holed_block("f3_redo")
        self.doc.clearUndos()

    def tearDown(self):
        db.close(self.doc)

    def test_redo_after_reject_is_empty(self):
        self.doc.openTransaction("human")
        self.doc.Block.Width = 33
        self.doc.commitTransaction()
        with AgentTurn(self.doc, label="redo bypass", verify=False) as t:
            self.doc.Block.Length = 77
            self.doc.openTransaction("nested")
            self.doc.Hole.Radius = 4
            self.doc.commitTransaction()
            self.doc.Block.Height = 15
        self.assertGreaterEqual(t.change_set()["transactions"]["segments"], 2)
        res = t.reject()
        self.assertTrue(res["exact"], res)
        self.assertEqual(self.doc.RedoCount, 0)
        self.assertEqual(self.doc.RedoNames, [])
        self.assertEqual(self.doc.UndoNames, ["human"])        # the human's entry is untouched
        self.doc.redo()
        self.doc.redo()
        self.assertEqual((self.doc.Block.Length.Value, self.doc.Hole.Radius.Value, self.doc.Block.Height.Value),
                         (40, 3, 10))
        self.assertEqual(self.doc.Block.Width.Value, 33)
        self.assertIsNone(self.doc.getObject("AgentCoreDropRedo"))

    def test_redo_after_exception_reject_is_empty(self):
        with self.assertRaises(ValueError):
            with AgentTurn(self.doc, label="boom", verify=False):
                self.doc.Block.Length = 66
                raise ValueError("agent failed")
        self.assertEqual(self.doc.RedoCount, 0)
        self.doc.redo()
        self.assertEqual(self.doc.Block.Length.Value, 40)

    def test_snapshot_reject_keeps_the_humans_redo(self):
        with tempfile.TemporaryDirectory() as tmp:
            with AgentTurn(self.doc, label="snap", verify=False, store=SnapshotStore(tmp)) as t:
                self.doc.Block.Length = 55
            for name, prop in (("human A", "Width"), ("human B", "Height")):  # edits land on top of the turn
                self.doc.openTransaction(name)
                setattr(self.doc.Block, prop, 21)
                self.doc.commitTransaction()
            self.doc.undo()                                    # the human undoes B: B is on the redo stack
            res = t.reject()
            self.assertEqual(res["method"], "snapshot")
            self.assertTrue(res["equal"], res["differences"])
            self.assertEqual(self.doc.RedoNames, ["human B"])  # not the turn's to drop

    def test_human_redo_survives_an_empty_turn(self):
        self.doc.openTransaction("human")
        self.doc.Block.Width = 33
        self.doc.commitTransaction()
        self.doc.undo()
        with AgentTurn(self.doc, label="no-op", verify=False) as t:
            pass
        t.reject()
        self.assertEqual(self.doc.RedoNames, ["human"])        # a turn that changed nothing drops nothing


class TestFlattenDriftRule(unittest.TestCase):
    """Which flatten differences ``accept`` tolerates (``turn._derived``)."""

    DUMP = {"objects": [{"name": "Sketch", "type": "Sketcher::SketchObject"},
                        {"name": "View", "type": "TechDraw::DrawViewPart"},
                        {"name": "Box", "type": "Part::Box"}]}

    def test_rule(self):
        from AgentCore.diff.turn import _derived
        allowed = {"Sketch", "View", "Box"}
        self.assertTrue(_derived("/objects/0/state/0: 'Up-to-date' != 'Invalid'", self.DUMP, allowed))
        self.assertTrue(_derived("/objects/1/detail/visible_edges: 4 != 0", self.DUMP, allowed))
        self.assertFalse(_derived("/objects/2/detail/volume: 1 != 2", self.DUMP, allowed))     # not TechDraw
        self.assertFalse(_derived("/objects/0/properties/3/value: 1 != 2", self.DUMP, allowed))  # a property
        self.assertFalse(_derived("/objects/0/expressions: x", self.DUMP, allowed))
        # an object the turn did not change may not drift at all
        self.assertFalse(_derived("/objects/0/state/0: 'Up-to-date' != 'Invalid'", self.DUMP, {"View"}))
        self.assertFalse(_derived("/objects/1/detail/visible_edges: 4 != 0", self.DUMP, {"Sketch"}))
        self.assertFalse(_derived("/structure/x: 1 != 2", self.DUMP, allowed))


class TestChangeSet(_AuthCase):
    def setUp(self):
        self.doc = db.build_holed_block("f3_changeset")

    def tearDown(self):
        db.close(self.doc)

    def test_properties_expressions_features_and_verify(self):
        doc = self.doc
        with AgentTurn(doc, label="cs") as t:
            doc.Block.Length = 50
            doc.Hole.setExpression("Height", "Block.Height * 3")
            s = doc.addObject("Part::Box", "Overlap")    # interferes with the block: F2 must flag it
            s.Placement.Base = App.Vector(35, 0, 0)
            doc.addObject("Part::Sphere", "Loose")
            doc.removeObject("Loose")
        cs = t.change_set()
        props = {(p["object"], p["property"]): p for p in cs["properties"]}
        self.assertEqual(props[("Block", "Length")]["after"]["value"], 50.0)
        self.assertIn({"object": "Hole", "path": "Height", "before": None, "after": "Block.Height * 3"},
                      cs["expressions"])
        self.assertEqual([f["name"] for f in cs["features"]["added"]], ["Overlap"])
        self.assertEqual(cs["features"]["removed"], [])
        self.assertIn("Block", cs["features"]["modified"])
        self.assertIn("Cut", cs["features"]["modified"])
        self.assertTrue(cs["verify"]["before"]["ok"], cs["verify"]["before"])
        self.assertIn("interference", cs["verify"]["regressions"])
        self.assertEqual(cs["geometry"]["Overlap"]["status"], "added")
        self.assertEqual(cs["geometry"]["Cut"]["status"], "modified")
        self.assertTrue(t.reject()["equal"])


if __name__ == "__main__":
    unittest.main()
