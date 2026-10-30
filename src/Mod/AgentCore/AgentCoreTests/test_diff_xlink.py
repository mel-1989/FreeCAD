# SPDX-License-Identifier: LGPL-2.1-or-later
"""F3: agent turns across documents joined by App::Link XLinks."""

import os
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

import FreeCAD as App

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "diff")
if FIX not in sys.path:
    sys.path.insert(0, FIX)

import diff_builders as db  # noqa: E402
from AgentCore.diff import AgentTurn, ApplyAuthority, SnapshotStore, differences  # noqa: E402
from AgentCore.diff.turn import TurnError, _dump  # noqa: E402


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

    def test_accept_gives_every_document_the_same_transaction_id(self):
        """Independent of F3's own ``shared_transaction`` flag: observe the transaction id FreeCAD
        itself reports in each document while the turn is replayed, and the application-level
        active transaction, and require one id, equal to the one accept returned."""
        from AgentCore.diff import transplant
        seen = []
        real = transplant.apply

        def spy(doc, cap, **kw):
            out = real(doc, cap, **kw)
            active = App.getActiveTransaction()
            seen.append((doc.Name, doc.getBookedTransactionID(), active[1] if active else None,
                         active[0] if active else None))
            return out
        t = self._cross_turn(self.asm)
        transplant.apply = spy
        try:
            res = t.accept(self.auth.mint(t))
        finally:
            transplant.apply = real
        self.assertEqual(sorted(n for n, *_ in seen), sorted([self.src.Name, self.asm.Name]))
        ids = {booked for _, booked, _, _ in seen}
        self.assertEqual(len(ids), 1, seen)                        # both documents booked into one transaction
        (tid,) = ids
        self.assertGreater(tid, 0)
        self.assertEqual({a for _, _, a, _ in seen}, {tid}, seen)  # the application-level active transaction
        self.assertEqual({n for _, _, _, n in seen}, {t.undo_name}, seen)
        self.assertEqual(res["transaction_id"], tid)
        self.assertTrue(res["shared_transaction"])
        for d in (self.src, self.asm):                             # and the entry exists in each document
            self.assertEqual(d.UndoNames, [t.undo_name], d.Name)
        self.assertIsNone(App.getActiveTransaction())              # nothing left open at application level
        self.asm.undo()
        self.src.undo()
        self.assert_pre()

    def test_failed_flatten_rolls_every_document_back(self):
        """Accept is atomic: a flatten failure in the second document undoes
        the first document's flattened entry too and ends the turn rejected."""
        self._failed_flatten(fail_on=2)

    def test_failed_flatten_on_first_document_rolls_back_the_unreached_one(self):
        """A failure in the first replay leaves the second document undone but
        never replayed; it must count as rolled back by undo, not as failed."""
        self._failed_flatten(fail_on=1)

    def _failed_flatten(self, fail_on):
        from AgentCore.diff import FlattenError, transplant
        t = self._cross_turn(self.asm)
        real, calls = transplant.apply, []

        def failing(doc, cap, **kw):          # replay the first document, fail on the second
            calls.append(doc.Name)
            out = real(doc, cap, **kw)
            if len(calls) == fail_on:
                raise transplant.TransplantError("injected failure in the second document")
            return out
        transplant.apply = failing
        try:
            with self.assertRaises(transplant.TransplantError):
                t.accept(self.auth.mint(t))
        finally:
            transplant.apply = real
        self.assertEqual(len(calls), fail_on)
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


def _gui_binary():
    """The FreeCAD GUI executable next to the running FreeCADCmd, or None."""
    cand = os.path.join(App.getHomePath(), "bin", "FreeCAD")
    return cand if os.path.isfile(cand) else None


@unittest.skipUnless(os.environ.get("AGENTCORE_GUI_TESTS") == "1" and _gui_binary(),
                     "opens the FreeCAD GUI offscreen: set AGENTCORE_GUI_TESTS=1 (mini only, never the laptop)")
class TestXLinkGuiUndo(unittest.TestCase):
    def test_one_application_undo_reverts_both_documents(self):
        """A turn across two documents, accepted, is reverted in both by a single GUI Std_Undo
        (Gui::Document::checkTransactionID groups entries that share a transaction id)."""
        probe = os.path.join(FIX, "gui_undo_probe.py")
        with tempfile.TemporaryDirectory(prefix="f3gui") as d:
            result = os.path.join(d, "out.json")
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PROBE_OUT=result, PROBE_FIX=FIX,
                       PROBE_SRC=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            subprocess.run([_gui_binary(), probe], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=180, cwd=d)
            with open(result) as fh:
                out = json.load(fh)
        self.assertNotIn("error", out, out.get("error"))
        self.assertEqual(out["gui_documents"], [True, True])
        self.assertTrue(out["shared_transaction"])
        self.assertEqual(out["undo_counts_after_accept"], [1, 1])
        self.assertNotEqual(out["changed"], out["before"])
        self.assertEqual(out["undo_counts_after_undo"], [0, 0], out)    # one Std_Undo emptied both stacks
        self.assertEqual(out["after_undo"], out["before"], out)         # and both documents are back


class _RefusingOnce:
    """Document proxy whose guarded end refuses a commit once, as if a worker recompute had been
    queued for it between the pre-check and the end."""

    def __init__(self, doc):
        object.__setattr__(self, "_d", doc)
        object.__setattr__(self, "refused", 0)

    def __getattr__(self, name):
        return getattr(self._d, name)

    def __setattr__(self, name, value):
        setattr(self._d, name, value)

    def endAgentTurn(self, commit=True, timeout=60.0):
        if commit and not self.refused:
            object.__setattr__(self, "refused", 1)
            rep = self._d.agentTurnStatus()
            rep["refused"], rep["reason"] = True, "simulated worker race"
            return rep
        return self._d.endAgentTurn(commit, timeout)


@unittest.skipUnless(hasattr(App.Document, "beginAgentTurn"), "needs the C++ agent turn guard")
class TestRacedMultiDocumentEnd(unittest.TestCase):
    """A commit that refuses after another document already committed: the turn ends rejected."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="f3race")
        self.src, self.asm = db.build_xlink_pair(self.tmp)
        self.asm.openTransaction("human edit")
        self.asm.getObject("LinkBox").Label = "human"
        self.asm.commitTransaction()
        self.src.openTransaction("human src")
        self.src.getObject("SrcCyl").Radius = 3
        self.src.commitTransaction()
        self.src.recompute()
        self.pre = {d.Name: (settled(d), list(d.UndoNames)) for d in (self.asm, self.src)}

    def tearDown(self):
        db.close(self.asm, self.src)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _state(self):
        for d in (self.asm, self.src):
            self.assertEqual(differences(self.pre[d.Name][0], settled(d)), [], d.Name)
            self.assertEqual(list(d.UndoNames), self.pre[d.Name][1], d.Name)   # the human's entries intact
            self.assertEqual(list(d.RedoNames), [], d.Name)                    # nothing of the turn to redo
            self.assertFalse(d.isAgentTurnActive(), d.Name)

    def test_refusal_after_another_document_committed_rejects_the_whole_turn(self):
        wrapped = _RefusingOnce(self.src)
        t = AgentTurn(self.asm, label="raced", verify=False, geometry_diff=False, docs=[self.asm, wrapped])
        with self.assertRaises(TurnError):
            with t:
                self.asm.openTransaction("y")
                self.asm.getObject("LinkBox").LinkPlacement = App.Placement(App.Vector(9, 9, 9), App.Rotation())
                self.asm.commitTransaction()
                self.src.openTransaction("x")
                self.src.getObject("SrcBox").Length = 20
                self.src.commitTransaction()
                self.src.recompute()
                self.asm.recompute()
        self.assertEqual(wrapped.refused, 1)
        self.assertEqual(t.status, "rejected")
        self.assertIsNotNone(t.result)
        self._state()
        # a later reject, or leaving the block again, changes nothing (no second undo)
        before = {d.Name: (list(d.UndoNames), list(d.RedoNames)) for d in (self.asm, self.src)}
        self.assertIs(t.reject(), t.result)
        t.__exit__(RuntimeError, RuntimeError("late"), None)
        self.assertEqual(before, {d.Name: (list(d.UndoNames), list(d.RedoNames)) for d in (self.asm, self.src)})
        self._state()


if __name__ == "__main__":
    unittest.main()
