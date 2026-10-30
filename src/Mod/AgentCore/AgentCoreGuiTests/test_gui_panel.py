# SPDX-License-Identifier: LGPL-2.1-or-later
"""F3 GUI: the change-set panel, its overlay, and the human-only apply path (needs FreeCADGui)."""

import os
import sys
import tempfile
import unittest

import FreeCAD as App
import FreeCADGui as Gui

FIX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "AgentCoreTests", "fixtures", "diff")
if FIX not in sys.path:
    sys.path.insert(0, FIX)

import diff_builders as db  # noqa: E402
from AgentCore.diff import AgentTurn, differences, snapshot_dump  # noqa: E402
from AgentCore.diff.authority import ApplyAuthority, ApplyToken, TokenRefused  # noqa: E402
from AgentCore.diff.turn import TurnError  # noqa: E402
from AgentCore.gui import backends, model, overlay, panel, session  # noqa: E402

V = App.Vector


def pump(n=4):
    for _ in range(n):
        Gui.updateGui()


def build_doc(name="f3gui"):
    doc = App.newDocument(name)
    doc.UndoMode = 1
    box = doc.addObject("Part::Box", "Block")
    box.Length, box.Width, box.Height = 40, 30, 10
    ball = doc.addObject("Part::Sphere", "Ball")
    ball.Radius = 5
    ball.Placement.Base = V(60, 0, 0)
    doc.recompute()
    doc.clearUndos()
    return doc


def scripted(doc):
    """Add a feature, edit one, delete one."""
    doc.getObject("Block").Length = 55
    wedge = doc.addObject("Part::Cylinder", "Post")
    wedge.Radius, wedge.Height = 4, 25
    wedge.Placement.Base = V(10, 10, 0)
    doc.removeObject("Ball")
    doc.recompute()


def turn_for(doc, fn=scripted, **kw):
    t = AgentTurn(doc, label="scripted", **kw)
    with t:
        fn(doc)
    return t


class GuiCase(unittest.TestCase):
    def setUp(self):
        Gui.Control.closeDialog() if Gui.Control.activeDialog() else None
        self.doc = build_doc()
        self.pre = snapshot_dump(self.doc)

    def tearDown(self):
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        pump()
        session._reset()
        db.close(self.doc)

    def view(self):
        return Gui.getDocument(self.doc.Name).ActiveView

    def settled(self):
        self.doc.recompute()
        return snapshot_dump(self.doc)

    def click(self, button):
        button.click()
        pump(6)


class TestPanelContent(GuiCase):
    def test_rows_match_the_change_set(self):
        t = turn_for(self.doc)
        p = panel.review(t)
        pump()
        cs = t.change_set()
        n = p.row_counts()
        self.assertEqual(n["added"], 1)
        self.assertEqual(n["removed"], 1)
        self.assertGreaterEqual(n["modified"], 1)
        self.assertEqual(n["properties"], len(cs["properties"]))
        self.assertGreaterEqual(n["geometry"], 3)       # Block modified, Post added, Ball removed
        self.assertEqual(n["transactions"], 1)
        self.assertEqual(p.groups["added"].childCount(), 1)
        self.assertEqual(p.groups["added"].text(0), "Features added (1)")
        self.assertIn("Post", p.groups["added"].child(0).text(0))
        self.assertIs(panel.current(), p)
        self.assertTrue(Gui.Control.activeDialog())

    def test_selecting_a_row_selects_the_object(self):
        t = turn_for(self.doc)
        p = panel.review(t)
        pump()
        p.tree.setCurrentItem(p.groups["modified"].child(0))
        pump()
        self.assertEqual([o.Name for o in Gui.Selection.getSelection(self.doc.Name)],
                         [p.groups["modified"].child(0).data(0, 0x0100)])
        p.tree.setCurrentItem(p.groups["removed"].child(0))     # Ball is gone: nothing to select
        pump()
        self.assertEqual(Gui.Selection.getSelection(self.doc.Name), [])

    def test_verify_regression_row(self):
        cs = {"features": {"added": [], "removed": [], "modified": []}, "properties": [], "expressions": [],
              "geometry": {}, "verify": {"regressions": ["thickness"], "fixes": ["watertight"]},
              "transactions": {"segments": 1}}
        rows = model.build_rows(cs)
        self.assertEqual([r["text"] for r in rows["verify"]], ["regression: thickness", "fixed: watertight"])

    def test_form_grab_is_stable(self):
        t = turn_for(self.doc)
        p = panel.review(t, with_overlay=False)
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "a.png"), os.path.join(d, "b.png")
            self.assertEqual(p.grab(a), panel.PANEL_SIZE)
            p.grab(b)
            self.assertEqual(open(a, "rb").read(), open(b, "rb").read())


class TestOverlay(GuiCase):
    def test_pieces_cover_added_and_removed_material(self):
        t = turn_for(self.doc)
        kinds = {(n, k) for n, k, _ in t.overlay_pieces()}
        self.assertIn(("Post", "added"), kinds)
        self.assertIn(("Ball", "removed"), kinds)
        self.assertIn(("Block", "added"), kinds)         # the box grew: added material, no removed
        self.assertNotIn(("Block", "removed"), kinds)

    def test_consumed_features_are_drawn_once(self):
        doc = db.build_holed_block("f3gui_cut", 3.0)
        try:
            doc.clearUndos()
            with AgentTurn(doc, label="bigger hole") as t:
                doc.Hole.Radius = 6
                doc.recompute()
            names = {n for n, _, _ in t.overlay_pieces()}
            self.assertEqual(names, {"Cut"})             # Block and Hole feed the Cut and are not drawn
            self.assertEqual({k for _, k, _ in t.overlay_pieces()}, {"removed"})
            t.reject()
        finally:
            db.close(doc)

    def test_nodes_exist_while_open_and_are_gone_after_each_exit(self):
        for how in ("accept", "reject", "close", "closeDialog"):
            with self.subTest(how=how):
                self.setUp()
                try:
                    t = turn_for(self.doc)
                    p = panel.review(t)
                    pump()
                    found = overlay.find_overlays(self.view())
                    self.assertEqual(len(found), 1)
                    self.assertGreaterEqual(found[0].getNumChildren(), 5)
                    if how == "accept":
                        self.click(p.accept_button)
                    elif how == "reject":
                        self.click(p.reject_button)
                    elif how == "close":
                        self.assertTrue(p.reject())
                    else:
                        Gui.Control.closeDialog()
                    pump(6)
                    self.assertEqual(overlay.find_overlays(self.view()), [], how)
                    if how != "close":      # FreeCAD closes the dialog itself after reject() returns True
                        self.assertFalse(Gui.Control.activeDialog(), how)
                    self.assertIsNone(panel.current())
                finally:
                    self.tearDown()

    def test_overlay_reaches_saveImage(self):
        t = turn_for(self.doc)
        with tempfile.TemporaryDirectory() as d:
            plain, shown = os.path.join(d, "a.png"), os.path.join(d, "b.png")
            v = self.view()
            v.fitAll()
            pump(6)
            v.saveImage(plain, 240, 180, "White")
            panel.review(t)
            pump(6)
            v.saveImage(shown, 240, 180, "White")
            self.assertNotEqual(open(plain, "rb").read(), open(shown, "rb").read())

            def count(path):
                from PySide import QtGui
                img, green, red = QtGui.QImage(path), 0, 0
                for y in range(img.height()):
                    for x in range(img.width()):
                        c = QtGui.QColor(img.pixel(x, y))
                        green += c.green() > c.red() + 40 and c.green() > c.blue() + 40
                        red += c.red() > c.green() + 60 and c.red() > c.blue() + 60
                return green, red

            self.assertEqual(count(plain), (0, 0))
            green, red = count(shown)
            self.assertGreater(green, 200)      # the added material (Post, grown Block) is green in the capture
            self.assertGreater(red, 200)        # the deleted Ball is red


class TestDecisions(GuiCase):
    def test_reject_equals_pre_turn_state(self):
        t = turn_for(self.doc)
        p = panel.review(t)
        self.assertNotEqual(differences(self.pre, self.settled()), [])
        self.click(p.reject_button)
        self.assertEqual(t.status, "rejected")
        self.assertEqual(differences(self.pre, self.settled()), [])

    def test_accept_then_gui_undo_equals_reject(self):
        t = turn_for(self.doc)
        p = panel.review(t)
        self.click(p.accept_button)
        self.assertEqual(t.status, "accepted", p.message)
        self.assertEqual(self.doc.UndoCount, 1)
        self.assertNotEqual(differences(self.pre, self.settled()), [])
        Gui.setActiveDocument(self.doc.Name)
        Gui.runCommand("Std_Undo")
        pump()
        self.assertEqual(self.doc.UndoCount, 0)
        undone = self.settled()
        self.assertEqual(differences(self.pre, undone), [])
        # and the reject of the same turn on a fresh document gives the same state
        db.close(self.doc)
        self.doc = build_doc()
        t2 = turn_for(self.doc)
        t2.reject()
        self.assertEqual(differences(undone, self.settled()), [])

    def test_stale_document_refuses_accept(self):
        t = turn_for(self.doc)
        p = panel.review(t)
        self.doc.Block.Width = 31          # the document moved after the panel was shown
        self.doc.recompute()
        self.click(p.accept_button)
        self.assertEqual(t.status, "proposed")
        self.assertIsNone(p.decision)
        self.assertIn("Accept refused", p.message)
        self.assertTrue(Gui.Control.activeDialog())
        self.assertEqual(len(overlay.find_overlays(self.view())), 1)    # still reviewable
        self.assertNotIn(session.approver().status(t.turn_id, p.backend.shown_digest()), ("approved", "spent"))
        self.assertIsNone(session.approver().poll(t.turn_id, p.backend.shown_digest()))

    def test_stale_change_set_refuses_before_a_token_exists(self):
        t = turn_for(self.doc)
        p = panel.review(t)
        t._cs = dict(t._cs, label="edited after it was shown")      # the change set changed
        self.click(p.accept_button)
        self.assertEqual(t.status, "proposed")
        self.assertIn("changed after it was shown", p.message)
        self.assertEqual(session.approver().status(t.turn_id, p.backend.shown_digest()), "pending")

    def test_token_for_another_digest_is_refused_by_the_turn(self):
        t = turn_for(self.doc)
        wire = session.approver().approve(t.turn_id, "0" * 64)       # a token for some other change set
        with self.assertRaises(TokenRefused):
            t.accept(ApplyToken.from_wire(wire))
        self.assertEqual(t.status, "proposed")
        t.reject()

    def test_no_token_without_the_button_path(self):
        t = turn_for(self.doc)
        for bad in (None, "token", object()):
            with self.assertRaises(TokenRefused):
                t.accept(bad)
        with self.assertRaises(TypeError):
            ApplyToken()
        with self.assertRaises(TypeError):
            ApplyAuthority()
        p = panel.review(t)
        pump()
        # opening the panel mints nothing: the approver has no record for the shown change set
        self.assertEqual(session.approver().status(t.turn_id, p.backend.shown_digest()), "pending")
        self.assertIsNone(session.approver().poll(t.turn_id, p.backend.shown_digest()))
        self.assertEqual(t.status, "proposed")
        # no panel, backend or module attribute holds an authority or a token
        for obj in (p, p.backend, session, panel, backends, overlay, model):
            for k, v in vars(obj).items():
                self.assertNotIsInstance(v, (ApplyAuthority, ApplyToken), f"{obj}.{k}")
        self.click(p.accept_button)             # only the human press mints one
        self.assertEqual(t.status, "accepted")

    def test_refused_accept_leaves_nothing_approved(self):
        t = turn_for(self.doc)
        p = panel.review(t)
        self.doc.Block.Width = 31
        self.doc.recompute()
        self.click(p.accept_button)
        self.assertIsNone(session.approver().poll(t.turn_id, p.backend.shown_digest()))


class TestRemoteBackend(GuiCase):
    def make(self):
        t = turn_for(self.doc)
        state = {"cs": t.change_set()}
        p = panel.review_remote(t.turn_id, lambda: state["cs"], label="remote")
        return t, state, p

    def test_digest_agrees_with_the_turn_and_the_overlay_is_local_only(self):
        t, state, p = self.make()
        self.assertEqual(p.backend.shown_digest(), t.digest())
        self.assertEqual(overlay.find_overlays(self.view()), [])
        self.assertEqual(p.row_counts()["added"], 1)
        t.reject()

    def test_accept_approves_and_reject_denies_at_the_approver(self):
        t, state, p = self.make()
        d = p.backend.shown_digest()
        self.assertEqual(session.approver().status(t.turn_id, d), "pending")
        self.click(p.accept_button)
        self.assertEqual(session.approver().status(t.turn_id, d), "approved")
        self.assertIsNotNone(session.approver().poll(t.turn_id, d))
        # the worker then accepts with the token it polled
        t.accept(ApplyToken.from_wire(session.approver().poll(t.turn_id, d)))
        self.assertEqual(t.status, "accepted")

    def test_reject_denies(self):
        t, state, p = self.make()
        d = p.backend.shown_digest()
        self.click(p.reject_button)
        self.assertEqual(session.approver().status(t.turn_id, d), "denied")
        t.reject()

    def test_accept_refuses_when_the_worker_changed_the_change_set(self):
        t, state, p = self.make()
        d = p.backend.shown_digest()
        state["cs"] = dict(state["cs"], label="the agent kept working")
        self.click(p.accept_button)
        self.assertIn("changed after it was shown", p.message)
        self.assertEqual(session.approver().status(t.turn_id, d), "pending")
        t.reject()


if __name__ == "__main__":
    unittest.main()
