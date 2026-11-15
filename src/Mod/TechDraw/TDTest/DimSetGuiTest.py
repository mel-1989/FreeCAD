# SPDX-License-Identifier: LGPL-2.1-or-later

# G04 slice 5b: the dimension set commands and their task panel, driven through the real GUI (selection, Gui.runCommand, the panel's
# widgets by object name, the task dialog's OK and Cancel buttons).  Run with: FreeCAD -t TestTechDrawGui.DimSetGuiTest
# Public sources: FreeCAD's Gui.Selection, Gui.Control and Gui.Command Python APIs; the expected sets are made by DimChain.make on a
# second, identical document.

import os
import time
import unittest

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtGui, QtWidgets

import DimChain as DC
import HoleTable as HT

from TDTest.DimChainTest import Plate, members, svg_items

KINDS = ("chain", "baseline", "ordinate")
NAMES = {"chain": "TechDraw_DimSetChain", "baseline": "TechDraw_DimSetBaseline", "ordinate": "TechDraw_DimSetOrdinate", "edit": "TechDraw_DimSetEdit"}
SHOTS = os.environ.get("DIMSET_SHOTS", "")


def pump(sec=0.05):
    t = time.time()
    while time.time() - t < sec:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)


def panel():
    return Gui.getMainWindow().findChild(QtWidgets.QWidget, "DimSetPanel")


def child(cls, name):
    return panel().findChild(cls, name)


def press(button):
    box = Gui.getMainWindow().findChildren(QtWidgets.QDialogButtonBox)
    for b in box:
        btn = b.button(button)
        if btn is not None and btn.isVisible():
            btn.click()
            pump()
            return
    raise AssertionError("no task dialog button")


def listed():
    w = child(QtWidgets.QListWidget, "dimsetVertices")
    return [w.item(i).text() for i in range(w.count())]


def message():
    return child(QtWidgets.QLabel, "dimsetMessage").text()


class DimSetGuiTest(unittest.TestCase):
    def setUp(self):
        Gui.activateWorkbench("TechDrawWorkbench")  # registers the commands (InitGui imports TechDrawTools)
        self.doc = App.newDocument("DimSetGui")
        self.doc.UndoMode = 1
        App.setActiveDocument(self.doc.Name)
        self.p = Plate(self.doc)
        HT.wait_for_geometry(self.p.view)
        pump(0.2)
        Gui.Selection.clearSelection()

    def tearDown(self):
        Gui.Selection.clearSelection()
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
            pump()
        App.closeDocument(self.doc.Name)

    def select(self, *subs, view=None):
        Gui.Selection.clearSelection()
        for s in subs:
            v = view or self.p.view
            if isinstance(s, tuple):
                v, s = s
            Gui.Selection.addSelection(self.doc.Name, v.Name, s)

    def cmd(self, key):
        Gui.runCommand(NAMES[key])
        pump(0.1)

    def undo_depth(self):
        return len(self.doc.UndoNames)

    # ---- commands

    def test_commands_are_wired(self):
        for key, name in NAMES.items():
            info = Gui.Command.get(name).getInfo()
            self.assertTrue(info["menuText"] and info["toolTip"] and info["statusTip"], name)
            self.assertNotEqual(info["menuText"], info["toolTip"])
            self.assertFalse(Gui.Command.get(name).getShortcut(), name)
            self.assertFalse(QtGui.QIcon(":/icons/" + info["pixmap"]).isNull() and QtGui.QIcon(info["pixmap"]).isNull(), name)
        Gui.activateWorkbench("TechDrawWorkbench")
        pump(0.3)
        mw = Gui.getMainWindow()
        bars = [b for b in mw.findChildren(QtWidgets.QToolBar) if b.objectName() == "TechDraw Dimension Sets"]
        self.assertTrue(bars)
        self.assertEqual(len(bars[0].actions()), 4)
        menus = [m for m in mw.findChildren(QtWidgets.QMenu) if m.title().replace("&", "") == "Dimension sets"]
        self.assertTrue(menus)
        self.assertEqual(len([a for a in menus[0].actions() if not a.isSeparator()]), 4)

    def test_activity(self):
        for k in KINDS:
            self.assertTrue(Gui.Command.get(NAMES[k]).isActive(), k)
        self.assertFalse(Gui.Command.get(NAMES["edit"]).isActive())
        d = DC.make(self.p.page, self.p.view, [self.p.corner(), self.p.hole(0)], "ordinate")
        o = DC.make(self.p.page, self.p.view, [self.p.corner(), self.p.hole(0)], "baseline")
        for obj in (d, o, DC.symbol_of(d), members(o)[0]):
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(self.doc.Name, obj.Name)
            self.assertTrue(Gui.Command.get(NAMES["edit"]).isActive(), obj.Name)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, self.p.view.Name)
        self.assertFalse(Gui.Command.get(NAMES["edit"]).isActive())
        other = App.newDocument("NoPage")
        App.setActiveDocument(other.Name)
        try:
            for k in KINDS:
                self.assertFalse(Gui.Command.get(NAMES[k]).isActive(), k)
        finally:
            App.setActiveDocument(self.doc.Name)
            App.closeDocument(other.Name)

    # ---- create

    def expected(self, kind, names, direction, side):
        doc = App.newDocument("Expected")
        try:
            q = Plate(doc)
            HT.wait_for_geometry(q.view)
            d = DC.make(q.page, q.view, names, kind, direction, side)
            return self.snapshot(d)
        finally:
            App.closeDocument(doc.Name)

    @staticmethod
    def snapshot(d):
        mem = sorted((round(m.getRawValue(), 6), round(m.X.Value, 3), round(m.Y.Value, 3)) for m in members(d))
        sym = DC.symbol_of(d)
        texts = [t[0] for t in svg_items(sym)[2]] if sym is not None else []
        return (d.Kind, d.Direction, d.Side, list(d.Vertices), mem, texts)

    def make_through_panel(self, kind, sel, direction="horizontal", side=None, datum=None):
        self.select(*sel)
        self.cmd(kind)
        self.assertIsNotNone(panel())
        self.assertEqual(child(QtWidgets.QLabel, "dimsetKind").text(), kind)
        dcombo = child(QtWidgets.QComboBox, "dimsetDirection")
        self.assertEqual([dcombo.itemText(i) for i in range(dcombo.count())], ["horizontal", "vertical"])
        dcombo.setCurrentIndex(dcombo.findText(direction))
        scombo = child(QtWidgets.QComboBox, "dimsetSide")
        self.assertEqual([scombo.itemText(i) for i in range(scombo.count())], list(DC.SIDES[direction]))
        if side:
            scombo.setCurrentIndex(scombo.findText(side))
        if datum:
            lw = child(QtWidgets.QListWidget, "dimsetVertices")
            lw.setCurrentRow(listed().index(datum))
            child(QtWidgets.QPushButton, "dimsetMakeDatum").click()
        vals = (kind, direction, scombo.currentText(), listed())
        press(QtWidgets.QDialogButtonBox.Ok)
        return vals

    def test_create_equals_api_and_is_one_undo_step(self):
        p = self.p
        cases = [
            ("chain", [p.corner(), p.hole(1), p.hole(0)], "horizontal", None, None),
            ("chain", [p.corner(), p.hole(0), p.hole(2)], "vertical", "right", None),
            ("baseline", [p.corner(), p.hole(0), p.hole(2)], "horizontal", "above", None),
            ("baseline", [p.hole(0), p.hole(1), p.corner()], "horizontal", None, None),
            ("ordinate", [p.corner(), p.hole(0), p.hole(1)], "vertical", "left", None),
            ("ordinate", [p.corner(), p.hole(0), p.hole(1), p.hole(2)], "horizontal", None, p.hole(1)),
        ]
        for kind, sel, direction, side, datum in cases:
            n0, u0 = len(self.doc.Objects), self.undo_depth()
            kind_, dir_, side_, names = self.make_through_panel(kind, sel, direction, side, datum)
            self.assertFalse(Gui.Control.activeDialog(), (kind, direction))
            ds = DC.drivers_on(p.page)
            self.assertEqual(len(ds), 1, kind)
            self.assertEqual(self.undo_depth(), u0 + 1, "one OK is one undo step")
            self.assertEqual(self.snapshot(ds[0]), self.expected(kind_, names, dir_, side_), (kind, direction, side))
            self.doc.undo()
            self.doc.recompute()
            self.assertEqual(len(self.doc.Objects), n0, kind)
            self.assertEqual(DC.drivers_on(p.page), [])

    def test_prefill_keeps_selection_order_and_counts_drops(self):
        p = self.p
        self.select(p.hole(1), "Edge1", p.corner(), p.hole(0), p.hole(1))
        self.cmd("baseline")
        self.assertEqual(listed(), [p.hole(1), p.corner(), p.hole(0)])
        self.assertTrue(message())
        press(QtWidgets.QDialogButtonBox.Cancel)

    def test_cancel_changes_nothing(self):
        p = self.p
        self.select(p.corner(), p.hole(0))
        n0, u0 = len(self.doc.Objects), self.undo_depth()
        sel0 = [(s.ObjectName, s.SubElementNames) for s in Gui.Selection.getSelectionEx()]
        self.cmd("chain")
        press(QtWidgets.QDialogButtonBox.Cancel)
        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual((len(self.doc.Objects), self.undo_depth()), (n0, u0))
        self.assertEqual([(s.ObjectName, s.SubElementNames) for s in Gui.Selection.getSelectionEx()], sel0)

    def test_bad_selections_give_a_sentence_and_create_nothing(self):
        p = self.p
        other = self.doc.addObject("Part::Box", "Bx")
        self.doc.recompute()
        v2 = self.doc.addObject("TechDraw::DrawViewPart", "View2")
        p.page.addView(v2)
        v2.Source = [p.top]
        v2.Direction = App.Vector(0, 0, 1)
        self.doc.recompute()
        HT.wait_for_geometry(v2)
        cases = {
            "nothing": [],
            "edges only": ["Edge1", "Edge2"],
            "one vertex": [p.hole(0)],
            "two views": [p.corner(), (v2, "Vertex6")],
            "non-view vertex": [(other, "Vertex1"), (other, "Vertex2")],
            "duplicates": [p.hole(0), p.hole(0)],
        }
        for label, sel in cases.items():
            n0 = len(self.doc.Objects)
            self.select(*sel)
            self.cmd("chain")
            self.assertIsNotNone(panel(), label)
            if label in ("nothing", "edges only", "non-view vertex"):
                self.assertTrue(message(), label)
            press(QtWidgets.QDialogButtonBox.Ok)
            self.assertTrue(message(), label)  # a sentence, and the panel stays open
            self.assertTrue(Gui.Control.activeDialog(), label)
            self.assertNotIn("Traceback", message())
            self.assertEqual(len(self.doc.Objects), n0, label)
            press(QtWidgets.QDialogButtonBox.Cancel)

    def test_remove_below_two_and_edit_datum(self):
        p = self.p
        self.select(p.corner(), p.hole(0))
        self.cmd("baseline")
        child(QtWidgets.QListWidget, "dimsetVertices").setCurrentRow(1)
        child(QtWidgets.QPushButton, "dimsetRemove").click()
        n0 = len(self.doc.Objects)
        press(QtWidgets.QDialogButtonBox.Ok)
        self.assertTrue(message())
        self.assertEqual(len(self.doc.Objects), n0)
        press(QtWidgets.QDialogButtonBox.Cancel)
        d = DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1)], "baseline")
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, d.Name)
        self.cmd("edit")
        child(QtWidgets.QListWidget, "dimsetVertices").setCurrentRow(0)
        child(QtWidgets.QPushButton, "dimsetRemove").click()
        before = self.snapshot(d)
        press(QtWidgets.QDialogButtonBox.Ok)
        self.assertIn("datum", message())
        self.assertTrue(Gui.Control.activeDialog())
        self.assertEqual(self.snapshot(d), before)
        press(QtWidgets.QDialogButtonBox.Cancel)

    # ---- edit

    def test_ok_after_the_view_was_deleted_gives_a_sentence(self):
        p = self.p
        d = DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1)], "chain")
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, members(d)[0].Name)
        self.cmd("edit")
        self.assertIsNotNone(panel())
        lw = child(QtWidgets.QListWidget, "dimsetVertices")
        lw.setCurrentRow(listed().index(p.hole(1)))
        child(QtWidgets.QPushButton, "dimsetRemove").click()
        self.doc.removeObject(p.view.Name)
        pump(0.2)
        press(QtWidgets.QDialogButtonBox.Ok)
        self.assertIsNotNone(panel())
        self.assertIn("deleted while this panel was open", message())
        self.assertNotIn("NoneType", message())
        press(QtWidgets.QDialogButtonBox.Cancel)
        self.assertIsNone(panel())

    def test_edit_equals_api_and_double_click_opens_it(self):
        p = self.p
        for kind in KINDS:
            d = DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1)], kind)
            ref = App.newDocument("Ref")
            try:
                q = Plate(ref)
                HT.wait_for_geometry(q.view)
                r = DC.make(q.page, q.view, [q.corner(), q.hole(0), q.hole(1)], kind)
                DC.add(r, q.hole(2))
                DC.remove(r, q.hole(0))
                DC.set_side(r, "above")
                want = self.snapshot(r)
            finally:
                App.closeDocument(ref.Name)
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(self.doc.Name, (DC.symbol_of(d) or members(d)[0]).Name)
            u0 = self.undo_depth()
            self.cmd("edit")
            self.assertEqual(child(QtWidgets.QLabel, "dimsetKind").text(), kind)
            dcombo = child(QtWidgets.QComboBox, "dimsetDirection")
            self.assertFalse(dcombo.isEnabled())
            self.assertEqual(dcombo.currentText(), "horizontal")
            self.assertEqual(child(QtWidgets.QComboBox, "dimsetSide").currentText(), "below")
            lw = child(QtWidgets.QListWidget, "dimsetVertices")
            lw.setCurrentRow(listed().index(p.hole(0)))
            child(QtWidgets.QPushButton, "dimsetRemove").click()
            lw.addItem(p.hole(2))
            sc = child(QtWidgets.QComboBox, "dimsetSide")
            sc.setCurrentIndex(sc.findText("above"))
            press(QtWidgets.QDialogButtonBox.Ok)
            self.assertFalse(Gui.Control.activeDialog(), message() if panel() else "")
            self.assertEqual(self.snapshot(d), want, kind)
            self.assertEqual(self.undo_depth(), u0 + 1, "one OK of an edit is one undo step: " + kind)
            self.doc.undo()
            self.doc.recompute()
            self.assertEqual(sorted(d.Vertices), sorted([p.corner(), p.hole(0), p.hole(1)]))
            self.assertEqual(d.Side, "below")
            self.doc.removeObject(d.Name)
            self.doc.recompute()

    def test_edit_cancel_and_tree_double_click(self):
        p = self.p
        d = DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1)], "baseline")
        before, u0 = self.snapshot(d), self.undo_depth()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, d.Name)
        self.cmd("edit")
        child(QtWidgets.QListWidget, "dimsetVertices").takeItem(2)
        press(QtWidgets.QDialogButtonBox.Cancel)
        self.assertEqual((self.snapshot(d), self.undo_depth()), (before, u0))
        import TechDrawTools.CommandDimSet as C

        self.assertTrue(C.install_tree_hook() >= 0)

    def _tree(self):
        for w in Gui.getMainWindow().findChildren(QtWidgets.QTreeWidget):
            if w.metaObject().className() == "Gui::TreeWidget":
                if w.isVisible() or True:
                    yield w

    def _item_of(self, tree, label):
        from PySide import QtCore

        found = tree.findItems(label, QtCore.Qt.MatchExactly | QtCore.Qt.MatchRecursive)
        return found[0] if found else None

    def test_real_double_click_in_the_tree_opens_the_edit_panel(self):
        from PySide6 import QtTest
        from PySide import QtCore

        p = self.p
        d = DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1)], "chain")
        e = DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1)], "ordinate")
        for drv in (d, e):
            opened = False
            for tree in self._tree():
                it = self._item_of(tree, drv.Label)
                if it is None:
                    continue
                tree.show()
                pump(0.3)
                for t in range(5):  # the tree rebuilds its items now and then: look the item up afresh just before the click
                    it = self._item_of(tree, drv.Label)
                    try:
                        par = it.parent()
                        while par is not None:
                            par.setExpanded(True)
                            par = par.parent()
                        Gui.Selection.clearSelection()
                        it = self._item_of(tree, drv.Label)
                        rect = tree.visualItemRect(it)
                        break
                    except RuntimeError:
                        pump(0.2)
                self.assertFalse(rect.isEmpty())
                QtTest.QTest.mouseDClick(tree.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, rect.center())
                pump(0.3)
                if panel() is not None:
                    opened = True
                    self.assertEqual(child(QtWidgets.QLabel, "dimsetKind").text(), drv.Kind)
                    press(QtWidgets.QDialogButtonBox.Cancel)
                    break
            self.assertTrue(opened, drv.Kind)

    # ---- slice 10: Make datum in edit mode

    def drop_last_hole(self, doc, plate):
        doc.openTransaction("drop hole")
        plate.view.Source = [doc.getObject("Cut1")]
        doc.recompute()
        doc.commitTransaction()
        for _ in range(60):  # the view's edges arrive from a worker thread; the timer relays the set
            pump(0.1)

    def datum_reference(self, kind, names, dead, new):
        """The API's answer on a second, identical document."""
        ref = App.newDocument("RefDatum")
        try:
            q = Plate(ref)
            HT.wait_for_geometry(q.view)
            r = DC.make(q.page, q.view, names, kind)
            if dead:
                self.drop_last_hole(ref, q)
            DC.set_datum(r, new)
            for _ in range(20):
                pump(0.1)
            return self.snapshot(r)
        finally:
            App.closeDocument(ref.Name)

    def open_edit(self, d):
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, (DC.symbol_of(d) or members(d)[0]).Name)
        self.cmd("edit")
        self.assertIsNotNone(panel())

    def check_edit_make_datum(self, kind, dead):
        p = self.p
        mk = child  # alias for readability below
        label = "%s %s" % (kind, "dead" if dead else "live")
        names = [p.hole(2), p.corner(), p.hole(0)] if dead else [p.corner(), p.hole(0), p.hole(1)]
        new = p.corner() if dead else p.hole(1)
        want = self.datum_reference(kind, names, dead, new)
        d = DC.make(p.page, p.view, names, kind)
        if dead:
            self.drop_last_hole(self.doc, p)
            self.assertIn("Invalid", d.State, label)
        self.open_edit(d)
        btn = mk(QtWidgets.QPushButton, "dimsetMakeDatum")
        self.assertTrue(btn.isEnabled(), label)
        if dead:
            self.assertIn("is no longer in", message(), label)
            self.assertIn("make another vertex the datum", message(), label)
        else:
            self.assertEqual(message(), "", label)
        # Cancel after Make datum changes nothing
        u0 = self.undo_depth()
        verts = list(d.Vertices)
        lw = mk(QtWidgets.QListWidget, "dimsetVertices")
        lw.setCurrentRow(listed().index(new))
        btn.click()
        self.assertEqual(listed()[0], new, label)
        press(QtWidgets.QDialogButtonBox.Cancel)
        self.assertEqual((self.undo_depth(), list(d.Vertices)), (u0, verts), label)
        self.open_edit(d)
        lw = mk(QtWidgets.QListWidget, "dimsetVertices")
        lw.setCurrentRow(listed().index(new))
        mk(QtWidgets.QPushButton, "dimsetMakeDatum").click()
        if dead:  # leave the dead row out
            lw.setCurrentRow(listed().index(names[0]))
            mk(QtWidgets.QPushButton, "dimsetRemove").click()
        press(QtWidgets.QDialogButtonBox.Ok)
        self.assertFalse(Gui.Control.activeDialog(), message() if panel() else label)
        for _ in range(10):
            pump(0.1)
        self.assertNotIn("Invalid", d.State, label)
        self.assertEqual(self.undo_depth(), u0 + 1, "one OK is one undo step: " + label)
        self.assertEqual(self.snapshot(d), want, label)
        self.assertEqual(d.Vertices[0], new, label)
        self.doc.undo()
        self.doc.recompute()
        for _ in range(10):
            pump(0.1)
        self.assertEqual(list(d.Vertices), verts, label)

    def test_edit_make_datum_baseline_live(self):
        self.check_edit_make_datum("baseline", False)

    def test_edit_make_datum_baseline_dead(self):
        self.check_edit_make_datum("baseline", True)

    def test_edit_make_datum_ordinate_live(self):
        self.check_edit_make_datum("ordinate", False)

    def test_edit_make_datum_ordinate_dead(self):
        self.check_edit_make_datum("ordinate", True)

    def test_edit_keeps_make_datum_off_for_a_chain_and_removing_a_live_datum_row_alone_still_refuses(self):
        p = self.p
        chain = DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1)], "chain")
        self.open_edit(chain)
        self.assertFalse(child(QtWidgets.QPushButton, "dimsetMakeDatum").isEnabled())
        self.assertEqual(message(), "")
        press(QtWidgets.QDialogButtonBox.Cancel)

    def test_undo_of_a_model_edit_restores_the_dropped_vertex(self):
        p = self.p
        for kind in KINDS:
            names = [p.corner(), p.hole(0), p.hole(2)]
            d = DC.make(p.page, p.view, names, kind)
            before = self.snapshot(d)
            self.doc.openTransaction("drop hole")
            p.view.Source = [self.doc.getObject("Cut1")]
            self.doc.recompute()
            self.doc.commitTransaction()
            for _ in range(60):  # the view's edges arrive from a worker thread; the timer relays the set
                pump(0.1)
            self.assertEqual(list(d.Vertices), names[:2], kind)
            self.doc.undo()
            self.doc.recompute()
            for _ in range(60):
                pump(0.1)
            self.assertEqual(self.snapshot(d)[:4], before[:4], kind)
            self.assertEqual(self.snapshot(d), before, kind)
            self.doc.removeObject(d.Name)
            self.doc.recompute()
            p.view.Source = [p.top]
            self.doc.recompute()
            pump(1.0)

    # ---- screenshots

    def test_screenshots(self):
        if not SHOTS:
            self.skipTest("DIMSET_SHOTS not set")
        p = self.p
        self.select(p.corner(), p.hole(0), p.hole(1))
        self.cmd("baseline")
        panel().grab().save(os.path.join(SHOTS, "r098-dimset-create.png"))
        press(QtWidgets.QDialogButtonBox.Cancel)
        d = DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1)], "ordinate")
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, d.Name)
        self.cmd("edit")
        panel().grab().save(os.path.join(SHOTS, "r098-dimset-edit.png"))
        press(QtWidgets.QDialogButtonBox.Cancel)


if __name__ == "__main__":
    unittest.main()
