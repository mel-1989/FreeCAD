# SPDX-License-Identifier: LGPL-2.1-or-later
"""F3: agent turns across documents joined by App::Link XLinks."""

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


class TestXLink(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.auth = ApplyAuthority.install()

    @classmethod
    def tearDownClass(cls):
        cls.auth.uninstall()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="f3x")
        self.src, self.asm = db.build_xlink_pair(self.tmp)
        self.src.clearUndos()
        self.asm.clearUndos()
        self.pre = {d.Name: settled(d) for d in (self.src, self.asm)}

    def tearDown(self):
        db.close(self.asm, self.src)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def assert_pre(self):
        for d in (self.src, self.asm):
            self.assertEqual(differences(self.pre[d.Name], settled(d)), [], d.Name)

    def _cross_turn(self, doc):
        """Edit the assembly, add an XLink, and edit the source through the link."""
        with AgentTurn(doc, label="cross-document", verify=False) as t:
            self.asm.LinkBox.Placement.Base = App.Vector(1, 2, 3)
            lk = self.asm.addObject("App::Link", "LinkCyl")
            lk.LinkedObject = self.src.SrcCyl
            self.asm.LinkBox.LinkedObject.Length = 20        # lands in the other document
            self.src.openTransaction("GUI command in source")  # nested, in the linked document
            self.src.addObject("Part::Sphere", "SrcBall")
            self.src.commitTransaction()
            self.src.SrcCyl.Radius = 3
        return t

    def test_linked_document_is_covered(self):
        self.assertEqual(self.asm.OutList[0].Name, self.src.Name)   # fixture really is an XLink pair
        t = self._cross_turn(self.asm)
        cs = t.change_set()
        self.assertEqual(sorted(cs["covered_documents"]), sorted([self.src.Name, self.asm.Name]))
        self.assertEqual([f["name"] for f in cs["features"]["added"]], ["LinkCyl"])
        src_cs = cs["linked_documents"][self.src.Name]
        self.assertEqual([f["name"] for f in src_cs["features"]["added"]], ["SrcBall"])
        changed = {(p["object"], p["property"]) for p in src_cs["properties"]}
        self.assertIn(("SrcBox", "Length"), changed)
        self.assertIn(("SrcCyl", "Radius"), changed)
        self.assertEqual(src_cs["geometry"]["SrcBox"]["status"], "modified")
        res = t.reject()
        self.assertTrue(res["equal"], res["differences"])
        self.assertEqual(res["methods"], {self.asm.Name: "undo", self.src.Name: "undo"})
        self.assertEqual(self.src.SrcBox.Length.Value, 12)
        self.assertIsNone(self.asm.getObject("LinkCyl"))
        self.assert_pre()

    def test_turn_started_in_source_covers_the_assembly(self):
        t = self._cross_turn(self.src)
        self.assertIn(self.asm.Name, t.change_set()["linked_documents"])
        self.assertTrue(t.reject()["equal"])
        self.assert_pre()

    def test_accept_then_undo_equals_reject_across_documents(self):
        t = self._cross_turn(self.asm)
        res = t.accept(self.auth.mint(t))
        self.assertIn(self.src.Name, res["flattened_documents"])   # nested split in the source: flattened
        for d in (self.src, self.asm):
            self.assertEqual(d.UndoNames, [t.undo_name], d.Name)   # one entry per document
        self.assertEqual(self.src.SrcBox.Length.Value, 20)
        self.assertIsNotNone(self.src.getObject("SrcBall"))
        self.assertTrue(res["shared_transaction"], res)            # both entries carry one transaction id,
        self.assertEqual(res["undo_entries_by_document"], {self.asm.Name: 1, self.src.Name: 1})
        self.asm.undo()                                             # which the GUI's undo reverts together
        self.src.undo()
        self.assert_pre()

    def test_failed_flatten_rolls_every_document_back(self):
        """Accept is atomic: a flatten failure in the second document undoes
        the first document's flattened entry too and ends the turn rejected."""
        from AgentCore.diff import FlattenError, transplant
        t = self._cross_turn(self.asm)
        real, calls = transplant.apply, []

        def failing(doc, cap, **kw):          # replay the first document, fail on the second
            calls.append(doc.Name)
            out = real(doc, cap, **kw)
            if len(calls) == 2:
                raise transplant.TransplantError("injected failure in the second document")
            return out
        transplant.apply = failing
        try:
            with self.assertRaises(transplant.TransplantError):
                t.accept(self.auth.mint(t))
        finally:
            transplant.apply = real
        self.assertEqual(len(calls), 2)
        self.assertEqual(t.status, "rejected")
        self.assertIn("injected failure", t.result["accept_error"])
        self.assertTrue(t.result["equal"], t.result["differences"])
        self.assertEqual(t.result["methods"], {self.asm.Name: "undo", self.src.Name: "undo"})
        for d in (self.src, self.asm):
            self.assertEqual(d.UndoCount, 0, d.Name)               # nothing of the turn is left
            self.assertEqual(d.RedoCount, 0, d.Name)               # and nothing can be redone
        self.assertIsNone(self.src.getObject("SrcBall"))
        self.assert_pre()
        self.assertTrue(issubclass(FlattenError, Exception))

    def test_reject_leaves_nothing_to_redo_in_either_document(self):
        t = self._cross_turn(self.asm)
        t.reject()
        for d in (self.src, self.asm):
            self.assertEqual(d.RedoCount, 0, d.Name)
            d.redo()
        self.assertEqual(self.src.SrcBox.Length.Value, 12)
        self.assertIsNone(self.asm.getObject("LinkCyl"))
        self.assert_pre()

    def test_reject_from_snapshots_across_documents(self):
        store = SnapshotStore(os.path.join(self.tmp, "snaps"))
        t = None
        with AgentTurn(self.asm, label="snap", verify=False, store=store) as t:
            self.asm.LinkBox.Placement.Base = App.Vector(5, 5, 5)
            self.src.SrcBox.Length = 30
        self.src.openTransaction("human")  # someone else's edit lands on top: the undo path is gone
        self.src.SrcCyl.Height = 99
        self.src.commitTransaction()
        open_before = sorted(App.listDocuments())
        res = t.reject()
        self.assertEqual(sorted(App.listDocuments()), open_before)  # snapshot documents closed again
        self.assertEqual(res["methods"][self.src.Name], "snapshot")
        self.assertTrue(res["equal"], res["differences"])
        self.assert_pre()
        self.assertEqual(self.src.SrcCyl.Height.Value, 10)              # the snapshot is the before-state


if __name__ == "__main__":
    unittest.main()
