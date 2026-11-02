# SPDX-License-Identifier: LGPL-2.1-or-later
"""G06 slice 1b: the Assembly_CheckInterference command, its task panel and the temporary highlight.

Run with the GUI binary: ``FreeCAD -t TestAssemblyInterferenceGui``. The no-dirty test is the important one: opening the
panel, running the check, selecting rows and closing must leave the document exactly as it was.
"""

import os
import tempfile
import unittest

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets
from pivy import coin

from AssemblyTests import interference_gui_fixture as F


def highlight_nodes(doc_name):
    """Every node named like the highlight under the document's scene graph root."""
    sg = Gui.getDocument(doc_name).ActiveView.getSceneGraph()
    act = coin.SoSearchAction()
    act.setName(coin.SbName("AssemblyInterferenceHighlight"))
    act.setInterest(coin.SoSearchAction.ALL)
    act.apply(sg)
    return act.getPaths().getLength()


def node_points(doc_name, node_name):
    """Coordinates (list of xyz tuples) of the SoCoordinate3 inside the highlight node called ``node_name``; None if absent."""
    sg = Gui.getDocument(doc_name).ActiveView.getSceneGraph()
    act = coin.SoSearchAction()
    act.setName(coin.SbName(node_name))
    act.apply(sg)
    path = act.getPath()
    if path is None:
        return None
    node = path.getTail()
    for i in range(node.getNumChildren()):
        ch = node.getChild(i)
        if isinstance(ch, coin.SoCoordinate3):
            return [tuple(ch.point[k].getValue()) for k in range(ch.point.getNum())]
    return None


def bbox_of(points):
    xs, ys, zs = zip(*points)
    return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def bbox_tuple(bb):
    return (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)


def scene_children(doc_name):
    return Gui.getDocument(doc_name).ActiveView.getSceneGraph().getNumChildren()


def doc_state(doc):
    return {
        "objects": sorted((o.Name, o.TypeId, tuple(o.PropertiesList)) for o in doc.Objects),
        "undo": doc.UndoCount,
        "redo": doc.RedoCount,
        "vis": {o.Name: o.ViewObject.Visibility for o in doc.Objects if hasattr(o, "ViewObject") and o.ViewObject},
        "colors": {o.Name: tuple(o.ViewObject.ShapeColor) for o in doc.Objects
                   if getattr(o, "ViewObject", None) and hasattr(o.ViewObject, "ShapeColor")},
        "transp": {o.Name: o.ViewObject.Transparency for o in doc.Objects
                   if getattr(o, "ViewObject", None) and hasattr(o.ViewObject, "Transparency")},
    }


class TestInterferenceGui(unittest.TestCase):
    def setUp(self):
        self.doc, self.asm, self.truth = F.scene_doc("InterfGui")
        App.setActiveDocument(self.doc.Name)
        Gui.ActiveDocument = Gui.getDocument(self.doc.Name)
        Gui.updateGui()
        self.tmp = tempfile.mkdtemp(prefix="interf-gui-")

    def tearDown(self):
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        for n in list(App.listDocuments()):
            App.closeDocument(n)

    def open_panel(self, **kw):
        panel = F.scene_panel(self.asm, kw) if kw else F.scene_panel(self.asm)
        Gui.Control.showDialog(panel)
        Gui.updateGui()
        return panel

    # ---- the panel lists what the API reports

    def test_panel_lists_the_three_classes_and_the_clearance_violation(self):
        panel = F.scene_panel(self.asm)
        self.assertEqual([r["class"] for r in panel.report["pairs"]], ["interfering", "touching", "close"])
        t = panel.pairs
        self.assertEqual(t.rowCount(), 3)
        self.assertEqual([t.item(i, 0).text() for i in range(3)], ["Interference", "Touching", "Too close"])
        self.assertEqual((t.item(0, 1).text(), t.item(0, 2).text()), ("A", "B"))
        self.assertEqual(t.item(0, 3).text(), "400.000")
        self.assertEqual(t.item(2, 4).text(), "2.500")
        self.assertEqual(panel.sets.rowCount(), 1)
        self.assertEqual(panel.sets.item(0, 0).text(), "Keep clear")
        self.assertEqual(panel.sets.item(0, 4).text(), "5.000")
        self.assertIn("1 interfering, 1 touching, 1 too close", panel.summary.text())
        self.assertTrue(panel.error.isHidden())

    # ---- the highlight

    def test_row_selection_draws_and_removes_the_highlight(self):
        n0 = scene_children(self.doc.Name)
        panel = self.open_panel()
        self.assertEqual(highlight_nodes(self.doc.Name), 0)
        panel.select_pair(0)
        self.assertEqual(highlight_nodes(self.doc.Name), 1)
        self.assertIn("400.000 mm", panel.details.text())
        panel.select_pair(2)
        self.assertEqual(highlight_nodes(self.doc.Name), 1, "a new row replaces the highlight, it does not add to it")
        panel.select_violation(0)
        self.assertEqual(highlight_nodes(self.doc.Name), 1)
        Gui.Control.closeDialog()
        Gui.updateGui()
        self.assertEqual(highlight_nodes(self.doc.Name), 0)
        self.assertEqual(scene_children(self.doc.Name), n0)

    def test_highlight_does_not_depend_on_gui_selection(self):
        Gui.Selection.clearSelection()
        panel = self.open_panel()
        panel.select_pair(0)
        self.assertEqual(len(Gui.Selection.getSelection()), 0)
        self.assertEqual(highlight_nodes(self.doc.Name), 1)

    def test_escape_return_and_close_remove_the_highlight(self):
        for how in ("escape", "return", "reject", "accept"):
            with self.subTest(how):
                panel = self.open_panel()
                panel.select_pair(0)
                self.assertEqual(highlight_nodes(self.doc.Name), 1)
                if how in ("escape", "return"):
                    key = QtCore.Qt.Key_Escape if how == "escape" else QtCore.Qt.Key_Return
                    target = panel.clearance if how == "return" else panel.pairs
                    if how == "return":
                        QtWidgets.QApplication.sendEvent(target, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key, QtCore.Qt.NoModifier))
                        for _ in range(10):
                            Gui.updateGui()
                        self.assertFalse(Gui.Control.activeDialog())
                    else:
                        Gui.Control.closeDialog()
                elif how == "reject":
                    panel.reject()
                    Gui.Control.closeDialog()
                else:
                    panel.accept()
                    Gui.Control.closeDialog()
                Gui.updateGui()
                self.assertEqual(highlight_nodes(self.doc.Name), 0)

    # ---- the notice for parts that hold no solid

    def switch_case(self, case):
        App.closeDocument(self.doc.Name)
        self.doc, self.asm, self.truth = F.scene_doc("InterfGui2", case)
        App.setActiveDocument(self.doc.Name)
        Gui.ActiveDocument = Gui.getDocument(self.doc.Name)
        Gui.updateGui()

    def test_non_solid_parts_are_named_in_a_notice(self):
        self.switch_case("face_wire_shell_sketch_listed")
        panel = self.open_panel()
        skipped = panel.report["skipped_non_solid"]
        self.assertGreaterEqual(len(skipped), 3)
        self.assertFalse(panel.notice.isHidden())
        text = panel.notice.text()
        for rec in skipped:
            self.assertIn(rec["label"], text)
            self.assertIn(rec["shape_type"], text)
        self.assertIn("no solid", text)
        self.assertTrue(panel.error.isHidden(), "a skipped non-solid part is not an error")
        self.assertIn("1 parts checked", panel.summary.text())

    def test_notice_is_hidden_when_every_part_is_a_solid(self):
        panel = self.open_panel()
        self.assertTrue(panel.notice.isHidden())
        self.assertEqual(panel.notice.text(), "")

    # ---- the highlight geometry is in assembly placement

    def check_highlight_geometry(self, case):
        self.switch_case(case)
        panel = self.open_panel()
        rows = [i for i, r in enumerate(panel.pair_rows) if r["class"] == "interfering"]
        self.assertTrue(rows, "%s %s %s" % (case, panel.report["pairs"], panel.report["stats"]))
        tol = 1e-3
        for row in rows:
            rec = panel.pair_rows[row]
            panel.select_pair(row)
            Gui.updateGui()
            a, b = panel.shapes[rec["a"]["path"]], panel.shapes[rec["b"]["path"]]
            common = node_points(self.doc.Name, "AssemblyInterferenceHighlightCommon")
            self.assertIsNotNone(common, "no common-solid geometry in the highlight")
            for got, want in zip(bbox_of(common), bbox_tuple(a.common(b).BoundBox)):
                self.assertAlmostEqual(got, want, delta=tol, msg="common solid, %s row %d" % (case, row))
            for node, shape in (("AssemblyInterferenceHighlightOutlineA", a), ("AssemblyInterferenceHighlightOutlineB", b)):
                pts = node_points(self.doc.Name, node)
                self.assertIsNotNone(pts, node)
                for got, want in zip(bbox_of(pts), bbox_tuple(shape.BoundBox)):
                    self.assertAlmostEqual(got, want, delta=tol, msg="%s, %s row %d" % (node, case, row))
        return panel

    def test_highlight_geometry_link_part_placed(self):
        panel = self.check_highlight_geometry("link_part_placed")
        # independent of the API: LP is turned 180 degrees about z at (20,0,0); X is the box (12,-4,0) of size 4
        rec = panel.pair_rows[0]
        panel.select_pair(0)
        common = node_points(self.doc.Name, "AssemblyInterferenceHighlightCommon")
        for got, want in zip(bbox_of(common), (12, -4, 0, 14, 0, 4)):
            self.assertAlmostEqual(got, want, delta=1e-3)

    def test_highlight_geometry_array_link_overlap(self):
        self.check_highlight_geometry("array_link_overlap")

    def test_highlight_geometry_nested_subassembly(self):
        panel = self.check_highlight_geometry("nested_subassembly")
        # Sub sits at z=15, its box at (3,0,-6): the 10 mm box A and the sub box share x[3,10], y[0,10], z[9,10]
        panel.select_pair(0)
        common = node_points(self.doc.Name, "AssemblyInterferenceHighlightCommon")
        for got, want in zip(bbox_of(common), (3, 0, 9, 10, 10, 10)):
            self.assertAlmostEqual(got, want, delta=1e-3)

    # ---- sphere outlines, no 3D view, a second panel

    def test_sphere_gets_a_tinted_overlay(self):
        self.switch_case("sph_contained")
        panel = self.open_panel()
        panel.select_pair(0)
        for node in ("TintA", "TintB"):
            pts = node_points(self.doc.Name, "AssemblyInterferenceHighlight" + node)
            self.assertTrue(pts and len(pts) > 20, node)
        self.assertIsNone(node_points(self.doc.Name, "AssemblyInterferenceHighlightTintC"))

    def test_boxes_get_no_tinted_overlay(self):
        panel = self.open_panel()
        panel.select_pair(0)
        self.assertIsNone(node_points(self.doc.Name, "AssemblyInterferenceHighlightTintA"))
        self.assertIsNotNone(node_points(self.doc.Name, "AssemblyInterferenceHighlightOutlineA"))

    def test_details_do_not_claim_a_3d_view_without_one(self):
        panel = self.open_panel()
        panel.highlight.show = lambda *a, **k: False
        for row, word in ((0, "red"), (1, "magenta"), (2, "magenta")):
            panel.select_pair(row)
            self.assertNotIn(word, panel.details.text())
            self.assertNotIn("3D view", panel.details.text())
        panel.select_violation(0)
        self.assertNotIn("magenta", panel.details.text())
        del panel.highlight.show
        panel.select_pair(0)
        self.assertIn("red in the 3D view", panel.details.text())

    def test_running_the_command_twice_opens_one_panel(self):
        Gui.Selection.addSelection(self.doc.Name, self.asm.Name)
        Gui.runCommand("Assembly_CheckInterference")
        Gui.updateGui()
        first = Gui.Control.activeDialog()
        self.assertTrue(first)
        made = []
        import InterferencePanel as P
        orig = P.TaskCheckInterference.__init__

        def counting(this, *a, **k):
            made.append(1)
            orig(this, *a, **k)
        P.TaskCheckInterference.__init__ = counting
        try:
            Gui.runCommand("Assembly_CheckInterference")
            Gui.updateGui()
        finally:
            P.TaskCheckInterference.__init__ = orig
        self.assertEqual(made, [], "a second panel was built while one was open")
        self.assertTrue(Gui.Control.activeDialog())

    # ---- the check must not dirty the document

    def test_check_does_not_dirty_the_document(self):
        path = os.path.join(self.tmp, "doc.FCStd")
        self.doc.saveAs(path)       # the saved, clean state
        self.doc.recompute()
        self.doc.purgeTouched()
        gdoc = Gui.getDocument(self.doc.Name)
        gdoc.Modified = False
        before = doc_state(self.doc)
        modified_date = self.doc.LastModifiedDate
        self.doc.saveCopy(os.path.join(self.tmp, "before.FCStd"))
        n_children = scene_children(self.doc.Name)

        panel = self.open_panel()
        panel.clearance.setValue(4.0)
        panel.run()
        for i in range(panel.pairs.rowCount()):
            panel.select_pair(i)
        panel.select_violation(0)
        Gui.Control.closeDialog()
        Gui.updateGui()

        self.assertEqual(doc_state(self.doc), before)
        self.assertEqual(self.doc.UndoCount, before["undo"])
        self.assertFalse(self.doc.isTouched(), "document touched")
        self.assertFalse(gdoc.Modified, "document marked modified")
        self.assertEqual(self.doc.LastModifiedDate, modified_date)
        self.assertEqual(self.doc.FileName, path)
        self.assertEqual(scene_children(self.doc.Name), n_children)
        self.assertEqual([o.Name for o in self.doc.Objects if "Touched" in o.State], [])
        # save a copy and reopen both copies: nothing extra
        self.doc.saveCopy(os.path.join(self.tmp, "after.FCStd"))
        self.assertEqual(self.saved_signature("after.FCStd"), self.saved_signature("before.FCStd"))

    def saved_signature(self, name):
        doc = App.openDocument(os.path.join(self.tmp, name))
        try:
            return sorted((o.Name, o.TypeId, tuple(o.PropertiesList),
                           round(o.Shape.Volume, 6) if hasattr(o, "Shape") and not o.Shape.isNull() else None)
                          for o in doc.Objects)
        finally:
            App.closeDocument(doc.Name)

    # ---- the command

    def test_command_is_registered_and_placed(self):
        info = Gui.Command.get("Assembly_CheckInterference").getInfo()
        for k in ("menuText", "toolTip", "statusTip", "pixmap"):
            self.assertTrue(info[k], k)
        self.assertTrue(os.path.isfile(info["pixmap"]), info["pixmap"])
        self.assertEqual(Gui.Command.get("Assembly_CheckInterference").getShortcut(), "")
        Gui.activateWorkbench("AssemblyWorkbench")
        Gui.updateGui()
        acts = [a for a in Gui.getMainWindow().findChildren(QtGui.QAction) if a.objectName() == "Assembly_CheckInterference"]
        self.assertTrue(acts, "no QAction: the command is in no toolbar or menu")
        in_toolbar = any(isinstance(w, QtWidgets.QToolBar) for a in acts for w in a.associatedObjects())
        in_menu = any(isinstance(w, QtWidgets.QMenu) for a in acts for w in a.associatedObjects())
        self.assertTrue(in_toolbar, "not in a toolbar")
        self.assertTrue(in_menu, "not in a menu")

    def test_command_active_only_with_an_assembly(self):
        Gui.activateWorkbench("AssemblyWorkbench")
        Gui.Selection.clearSelection()
        cmd = Gui.Command.get("Assembly_CheckInterference")
        Gui.updateGui()
        act = [a for a in Gui.getMainWindow().findChildren(QtGui.QAction) if a.objectName() == "Assembly_CheckInterference"][0]
        self.settle()
        self.assertFalse(act.isEnabled(), "enabled with no assembly active or selected")
        Gui.Selection.addSelection(self.doc.Name, self.asm.Name)
        self.settle()
        self.assertTrue(act.isEnabled(), "disabled with an assembly selected")
        cmd.run()
        Gui.updateGui()
        self.assertTrue(Gui.Control.activeDialog())
        self.settle()
        self.assertFalse(act.isEnabled(), "enabled while its own panel is open")

    @staticmethod
    def settle(seconds=0.7):
        import time
        t = time.time()
        while time.time() - t < seconds:
            Gui.updateGui()
            time.sleep(0.03)

    def test_icon_file_is_installed_beside_the_modules(self):
        import CommandCheckInterference as C
        self.assertTrue(os.path.isfile(C.ICON))
        self.assertFalse(QtGui.QIcon(C.ICON).isNull())
