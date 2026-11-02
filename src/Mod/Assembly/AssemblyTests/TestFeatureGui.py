# SPDX-License-Identifier: LGPL-2.1-or-later
"""G06 slice 4b: the assembly cut and hole commands, their task panel, the preview and Cancel.

Run with the GUI binary: ``FreeCAD -t TestAssemblyFeatureGui``. The cases come from the slice 4a corpus
(``feature_corpus``): each is built without its feature, the panel is filled in with the feature's parameters,
and OK has to make a feature whose ``affected()`` equals the analytic truth of the case.
"""

import os
import time
import unittest

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets
from pivy import coin

import AssemblyFeature as AF
from AssemblyTests import feature_gui_fixture as F

REL, ABS = 1e-6, 1e-9


def preview_nodes(doc_name):
    sg = Gui.getDocument(doc_name).ActiveView.getSceneGraph()
    act = coin.SoSearchAction()
    act.setName(coin.SbName("AssemblyFeaturePreview"))
    act.setInterest(coin.SoSearchAction.ALL)
    act.apply(sg)
    return act.getPaths().getLength()


def scene_children(doc_name):
    return Gui.getDocument(doc_name).ActiveView.getSceneGraph().getNumChildren()


def selection_root_children(doc_name):
    sg = Gui.getDocument(doc_name).ActiveView.getSceneGraph()
    for i in range(sg.getNumChildren()):
        ch = sg.getChild(i)
        if ch.getTypeId().getName().getString().endswith("SoFCUnifiedSelection"):
            return ch.getNumChildren()
    return -1


def close_enough(got, want):
    return abs(got - want) <= max(REL * abs(want), ABS)


class TestFeatureGui(unittest.TestCase):
    def setUp(self):
        self.ctx = None

    def tearDown(self):
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        Gui.Selection.clearSelection()
        for n in list(App.listDocuments()):
            App.closeDocument(n)

    def load(self, case):
        self.ctx, self.truth, self.recipe = F.build(case)
        self.doc, self.asm = self.ctx.doc, self.ctx.asm
        App.setActiveDocument(self.doc.Name)
        Gui.ActiveDocument = Gui.getDocument(self.doc.Name)
        Gui.updateGui()
        return self.ctx

    def open_panel(self, kind=None, feature=None, drive=True):
        from AssemblyFeaturePanel import TaskAssemblyFeature
        panel = TaskAssemblyFeature(self.asm, kind=kind or self.recipe["kind"], feature=feature, selection=[])
        if drive and feature is None:
            F.drive(panel, self.recipe, self.doc)
        Gui.Control.showDialog(panel)
        Gui.updateGui()
        return panel

    def analytic(self, aff):
        got = {c["path"]: c["removed_mm3"] for c in aff["components"]}
        self.assertEqual(sorted(got), sorted(p for p, v in self.truth["removed"].items() if v > 0))
        for path, want in self.truth["removed"].items():
            self.assertTrue(close_enough(got[path], want), "%s: %r want %r" % (path, got[path], want))

    # ---- OK creates what the API would

    def test_ok_hole_through_a_link_stack_equals_the_truth(self):
        self.load("hole_through_stack")
        panel = self.open_panel()
        self.assertIsNone(panel.problem)
        self.assertEqual(sorted(p for p, _ in panel.rows), ["P1", "P2", "P3"])
        self.assertTrue(panel.accept())
        Gui.Control.closeDialog()
        feats = AF.features(self.asm)
        self.assertEqual(len(feats), 1)
        self.analytic(AF.affected(feats[0]))
        self.assertEqual(feats[0].Spec, F.json.dumps(self.recipe["spec"], sort_keys=True))

    def test_ok_counterbore_hole_in_a_box(self):
        self.load("hole_counterbore")
        panel = self.open_panel()
        panel.accept()
        Gui.Control.closeDialog()
        self.analytic(AF.affected(AF.features(self.asm)[0]))

    def test_ok_extrude_through_three_plates(self):
        self.load("extrude_through_three_plates")
        panel = self.open_panel()
        panel.accept()
        Gui.Control.closeDialog()
        feat = AF.features(self.asm)[0]
        self.assertEqual(feat.ToolObject.Name, "Sk")
        self.analytic(AF.affected(feat))

    def test_ok_solid_tool_across_a_link_array(self):
        self.load("array_solid_tool")
        panel = self.open_panel()
        self.assertEqual(sorted(p for p, _ in panel.rows), ["Arr.0", "Arr.1", "Arr.2"])
        panel.accept()
        Gui.Control.closeDialog()
        self.analytic(AF.affected(AF.features(self.asm)[0]))

    def test_ok_oblique_extrude_symmetric_and_counterbore_cases(self):
        for case in ("extrude_symmetric", "extrude_oblique_direction", "extrude_circle_blind", "hole_blind"):
            for n in list(App.listDocuments()):
                App.closeDocument(n)
            self.load(case)
            panel = self.open_panel()
            self.assertTrue(panel.accept(), case)
            Gui.Control.closeDialog()
            self.analytic(AF.affected(AF.features(self.asm)[0]))

    # ---- the preview

    def test_preview_list_equals_affected_of_the_created_feature(self):
        self.load("array_solid_tool")
        panel = self.open_panel()
        shown = [(panel.table.item(i, 0).text(), panel.table.item(i, 1).text()) for i in range(panel.table.rowCount())]
        panel.accept()
        Gui.Control.closeDialog()
        aff = AF.affected(AF.features(self.asm)[0])
        want = [(" / ".join(c["link_path"]), "%.3f" % c["removed_mm3"]) for c in aff["components"]]
        self.assertEqual(shown, want)
        self.assertEqual(sorted(p for p, _ in panel.rows), sorted(c["path"] for c in aff["components"]))

    def test_preview_draws_and_removes_its_nodes(self):
        self.load("hole_through_stack")
        before = selection_root_children(self.doc.Name)
        panel = self.open_panel()
        self.assertEqual(preview_nodes(self.doc.Name), 1)
        self.assertEqual(selection_root_children(self.doc.Name), before + 1)
        panel.diameter.setValue(8.0)
        self.assertEqual(preview_nodes(self.doc.Name), 1, "a new preview replaces the old one")
        Gui.Control.closeDialog()
        Gui.updateGui()
        self.assertEqual(preview_nodes(self.doc.Name), 0)
        self.assertEqual(selection_root_children(self.doc.Name), before)

    def test_preview_updates_when_a_parameter_changes(self):
        self.load("hole_blind")
        panel = self.open_panel()
        vol = panel.rows[0][1]
        panel.depth.setValue(2.0)
        self.assertLess(panel.rows[0][1], vol)
        self.assertTrue(close_enough(panel.rows[0][1], 3.141592653589793 * 9 * 2))
        panel.through.setChecked(True)
        self.assertTrue(close_enough(panel.rows[0][1], 3.141592653589793 * 9 * 10))
        self.assertFalse(panel.depth.isEnabled())

    # ---- Cancel changes nothing

    def test_cancel_after_editing_every_parameter_changes_nothing(self):
        self.load("hole_through_stack")
        before = F.doc_signature(self.doc)
        n_nodes = selection_root_children(self.doc.Name)
        panel = self.open_panel()
        for w in panel.pos + panel.axis:
            w.setValue(w.value() + 0.5)
        panel.diameter.setValue(3.0)
        panel.depth.setValue(7.0)
        panel.through.setChecked(False)
        panel.through.setChecked(True)
        panel.cbore.setChecked(True)
        panel.cbore_diameter.setValue(9.0)
        panel.cbore_depth.setValue(2.0)
        panel.set_explicit_scope(["P1"])
        panel.set_scope_all()
        self.assertTrue(panel.rows)
        self.assertTrue(panel.reject())
        Gui.Control.closeDialog()
        Gui.updateGui()
        self.assertEqual(F.doc_signature(self.doc), before)
        self.assertFalse(self.doc.HasPendingTransaction)
        self.assertEqual(self.doc.UndoCount, before["undo"])
        self.assertEqual(AF.features(self.asm), [])
        self.assertEqual(selection_root_children(self.doc.Name), n_nodes)
        self.assertEqual(preview_nodes(self.doc.Name), 0)

    def test_cancel_a_cut_after_editing_every_parameter_changes_nothing(self):
        self.load("extrude_through_three_plates")
        before = F.doc_signature(self.doc)
        panel = self.open_panel()
        panel.reverse_direction()
        panel.through.setChecked(False)
        panel.length.setValue(3.0)
        panel.symmetric.setChecked(True)
        panel.tool_kind.setCurrentIndex(1)
        panel.set_explicit_scope(["P2"])
        panel.reject()
        Gui.Control.closeDialog()
        self.assertEqual(F.doc_signature(self.doc), before)
        self.assertFalse(self.doc.HasPendingTransaction)

    def test_closing_the_dialog_without_a_button_also_leaves_nothing_open(self):
        self.load("hole_blind")
        before = F.doc_signature(self.doc)
        self.open_panel()
        Gui.Control.closeDialog()
        Gui.updateGui()
        self.assertFalse(self.doc.HasPendingTransaction)
        self.assertEqual(F.doc_signature(self.doc), before)

    # ---- undo

    def test_undo_after_ok_removes_the_feature_exactly_and_redo_restores_it(self):
        self.load("hole_through_stack")
        before = F.doc_signature(self.doc)
        self.open_panel().accept()
        Gui.Control.closeDialog()
        after = F.doc_signature(self.doc)
        self.assertEqual(after["undo"], before["undo"] + 1, "OK is one undo step")
        self.doc.undo()
        got = F.doc_signature(self.doc)
        self.assertEqual({k: v for k, v in got.items() if k not in ("undo", "redo")},
                         {k: v for k, v in before.items() if k not in ("undo", "redo")})
        self.assertEqual(AF.features(self.asm), [])
        self.doc.redo()
        feats = AF.features(self.asm)
        self.assertEqual(len(feats), 1)
        self.analytic(AF.affected(feats[0]))

    # ---- scope

    def test_scope_picker_lists_components_with_their_link_path(self):
        self.load("link_to_part_with_two_solids")
        from AssemblyFeaturePanel import TaskAssemblyFeature
        panel = TaskAssemblyFeature(self.asm, kind="cut", selection=[])
        texts = [panel.components.item(i).text() for i in range(panel.components.count())]
        self.assertEqual(texts, ["LP / b1", "LP / b2"])
        panel.reject()

    def test_scope_picker_excludes_a_reached_part(self):
        self.load("hole_through_stack")
        panel = self.open_panel()
        self.assertEqual(sorted(panel.reached), ["P1", "P2", "P3"])
        self.assertFalse(panel.components.isEnabled())
        panel.scope_some.setChecked(True)
        self.assertEqual(sorted(panel.checked_keys()), ["P1", "P2", "P3"], "explicit scope starts from what the tool reaches")
        for i in range(panel.components.count()):
            if panel.components.item(i).text() == "P2":
                panel.components.item(i).setCheckState(QtCore.Qt.Unchecked)
        self.assertEqual(sorted(p for p, _ in panel.rows), ["P1", "P3"])
        panel.accept()
        Gui.Control.closeDialog()
        feat = AF.features(self.asm)[0]
        self.assertEqual(sorted(feat.Scope), ["P1", "P3"])
        aff = AF.affected(feat)
        self.assertEqual(sorted(c["path"] for c in aff["components"]), ["P1", "P3"])
        self.assertIn("P2", aff["not_cut"])
        self.assertAlmostEqual(AF.effective_shape(self.asm, "P2").Volume, 20 * 20 * 5, places=6)

    def test_empty_explicit_scope_cuts_nothing_and_ok_is_refused(self):
        self.load("hole_through_stack")
        panel = self.open_panel()
        panel.scope_some.setChecked(True)
        for i in range(panel.components.count()):
            panel.components.item(i).setCheckState(QtCore.Qt.Unchecked)
        self.assertEqual(panel.rows, [])
        self.assertIn("No component is checked", panel.summary.text())
        self.assertFalse(panel.accept(), "OK must refuse an empty scope instead of cutting every component")
        self.assertTrue(Gui.Control.activeDialog())
        panel.set_scope_all()
        self.assertEqual(len(panel.rows), 3)
        self.assertTrue(panel.accept())
        Gui.Control.closeDialog()

    def test_tool_that_reaches_nothing_is_allowed_with_a_notice(self):
        self.load("hole_blind")
        panel = self.open_panel()
        panel.pos[0].setValue(500.0)
        self.assertEqual(panel.rows, [])
        self.assertIn("reaches no component", panel.summary.text())
        self.assertTrue(panel.accept())
        Gui.Control.closeDialog()

    def test_invalid_input_keeps_ok_from_creating_anything(self):
        self.load("hole_blind")
        panel = self.open_panel()
        panel.cbore.setChecked(True)
        panel.cbore_diameter.setValue(5.0)      # narrower than the 6 mm hole
        self.assertIn("wider than the hole", panel.summary.text())
        self.assertFalse(panel.accept())
        panel.reject()
        Gui.Control.closeDialog()
        self.assertEqual(AF.features(self.asm), [])
        self.assertFalse(self.doc.HasPendingTransaction)

    # ---- editing an existing feature

    def made_feature(self, case):
        self.load(case)
        self.open_panel().accept()
        Gui.Control.closeDialog()
        return AF.features(self.asm)[0]

    def test_edit_then_cancel_restores_every_parameter(self):
        feat = self.made_feature("hole_through_stack")
        before = F.doc_signature(self.doc)
        panel = self.open_panel(feature=feat)
        self.assertEqual(sorted(p for p, _ in panel.rows), ["P1", "P2", "P3"])
        panel.diameter.setValue(2.0)
        panel.pos[0].setValue(3.0)
        panel.through.setChecked(False)
        panel.depth.setValue(1.0)
        panel.set_explicit_scope(["P3"])
        self.assertNotEqual(feat.Spec, F.json.dumps(self.recipe["spec"], sort_keys=True))
        panel.reject()
        Gui.Control.closeDialog()
        self.assertEqual(F.doc_signature(self.doc), before)
        self.assertEqual(list(feat.Scope), [])
        self.analytic(AF.affected(feat))

    def test_edit_then_ok_changes_the_feature_in_one_undo_step(self):
        feat = self.made_feature("hole_blind")
        before = F.doc_signature(self.doc)
        panel = self.open_panel(feature=feat)
        panel.depth.setValue(2.0)
        self.assertTrue(panel.accept())
        Gui.Control.closeDialog()
        self.assertTrue(close_enough(AF.affected(feat)["components"][0]["removed_mm3"], 3.141592653589793 * 9 * 2))
        self.doc.undo()
        got = F.doc_signature(self.doc)
        self.assertEqual({k: v for k, v in got.items() if k not in ("undo", "redo")},
                         {k: v for k, v in before.items() if k not in ("undo", "redo")})

    def test_edit_cut_restores_the_tool_object_and_scope(self):
        feat = self.made_feature("extrude_through_three_plates")
        before = F.doc_signature(self.doc)
        panel = self.open_panel(feature=feat)
        self.assertEqual(panel.tool_obj.Name, "Sk")
        panel.tool_kind.setCurrentIndex(1)
        panel.set_explicit_scope(["P1"])
        panel.reject()
        Gui.Control.closeDialog()
        self.assertEqual(F.doc_signature(self.doc), before)

    def test_double_click_and_edit_command_reopen_the_panel(self):
        feat = self.made_feature("hole_blind")
        import ViewProviderAssemblyFeature as VP
        self.assertIsInstance(feat.ViewObject.Proxy, VP.ViewProviderAssemblyFeature)
        self.assertTrue(os.path.isfile(feat.ViewObject.Proxy.getIcon()))
        feat.ViewObject.Proxy.doubleClicked(feat.ViewObject)
        Gui.updateGui()
        dlg = Gui.Control.activeDialog()
        self.assertTrue(dlg)
        Gui.Control.closeDialog()
        Gui.updateGui()
        self.assertFalse(self.doc.HasPendingTransaction)
        Gui.Selection.addSelection(self.doc.Name, feat.Name)
        Gui.updateGui()
        self.assertTrue(Gui.Command.get("Assembly_EditAssemblyFeature").isActive())
        Gui.runCommand("Assembly_EditAssemblyFeature")
        Gui.updateGui()
        self.assertTrue(Gui.Control.activeDialog())

    # ---- the commands

    def test_commands_are_registered_with_icons_and_placed(self):
        Gui.activateWorkbench("AssemblyWorkbench")
        Gui.updateGui()
        for name in ("Assembly_CreateAssemblyCut", "Assembly_CreateAssemblyHole", "Assembly_EditAssemblyFeature"):
            info = Gui.Command.get(name).getInfo()
            for k in ("menuText", "toolTip", "statusTip", "pixmap"):
                self.assertTrue(info[k], "%s %s" % (name, k))
            self.assertTrue(os.path.isfile(info["pixmap"]), info["pixmap"])
            self.assertFalse(QtGui.QIcon(info["pixmap"]).isNull())
            self.assertEqual(Gui.Command.get(name).getShortcut(), "")
            acts = [a for a in Gui.getMainWindow().findChildren(QtGui.QAction) if a.objectName() == name]
            self.assertTrue(acts, name + " is in no toolbar or menu")
            in_menu = any(isinstance(w, QtWidgets.QMenu) for a in acts for w in a.associatedObjects())
            self.assertTrue(in_menu, name + " is not in a menu")
            if name != "Assembly_EditAssemblyFeature":
                in_bar = any(isinstance(w, QtWidgets.QToolBar) for a in acts for w in a.associatedObjects())
                self.assertTrue(in_bar, name + " is not in a toolbar")

    @staticmethod
    def settle(seconds=0.7):
        t = time.time()
        while time.time() - t < seconds:
            Gui.updateGui()
            time.sleep(0.03)

    def test_command_active_states(self):
        self.load("hole_blind")
        Gui.activateWorkbench("AssemblyWorkbench")
        Gui.Selection.clearSelection()
        self.settle()
        acts = {n: [a for a in Gui.getMainWindow().findChildren(QtGui.QAction) if a.objectName() == n][0]
                for n in ("Assembly_CreateAssemblyCut", "Assembly_CreateAssemblyHole")}
        for n, a in acts.items():
            self.assertFalse(a.isEnabled(), n + " enabled with no assembly active or selected")
        Gui.Selection.addSelection(self.doc.Name, self.asm.Name)
        self.settle()
        for n, a in acts.items():
            self.assertTrue(a.isEnabled(), n + " disabled with an assembly selected")
        Gui.Command.get("Assembly_CreateAssemblyHole").run()
        Gui.updateGui()
        self.assertTrue(Gui.Control.activeDialog())
        self.settle()
        for n, a in acts.items():
            self.assertFalse(a.isEnabled(), n + " enabled while a panel is open")
        Gui.Control.closeDialog()
        self.settle()
        for n, a in acts.items():
            self.assertTrue(a.isEnabled(), n + " stays disabled after the panel closed")

    def test_running_a_command_twice_opens_one_panel(self):
        self.load("hole_blind")
        Gui.Selection.addSelection(self.doc.Name, self.asm.Name)
        Gui.runCommand("Assembly_CreateAssemblyCut")
        Gui.updateGui()
        first = Gui.Control.activeDialog()
        self.assertTrue(first)
        import AssemblyFeaturePanel as P
        made = []
        orig = P.TaskAssemblyFeature.__init__

        def counting(this, *a, **k):
            made.append(1)
            orig(this, *a, **k)
        P.TaskAssemblyFeature.__init__ = counting
        try:
            Gui.runCommand("Assembly_CreateAssemblyCut")
            Gui.runCommand("Assembly_CreateAssemblyHole")
            Gui.updateGui()
        finally:
            P.TaskAssemblyFeature.__init__ = orig
        self.assertEqual(made, [], "a second panel was built while one was open")

    def test_the_command_opens_the_right_panel_and_uses_the_selection(self):
        self.load("extrude_through_three_plates")
        sk = self.doc.getObject("Sk")
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, self.asm.Name)
        Gui.runCommand("Assembly_CreateAssemblyCut")
        Gui.updateGui()
        panel = Gui.Control.activeDialog()
        self.assertTrue(panel)
        Gui.Control.closeDialog()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, sk.Name)
        from AssemblyFeaturePanel import TaskAssemblyFeature
        p = TaskAssemblyFeature(self.asm, kind="cut")
        self.assertIs(p.tool_obj, sk, "a selected sketch is the profile by default")
        self.assertEqual((p.dir_x.value(), p.dir_y.value(), p.dir_z.value()), (0.0, 0.0, -1.0), "default: into the part, against the profile normal")
        p.reject()

    def test_icons_are_installed_beside_the_modules(self):
        import CommandCreateAssemblyFeature as C
        for n in ("Assembly_CreateAssemblyCut", "Assembly_CreateAssemblyHole", "Assembly_EditAssemblyFeature"):
            self.assertTrue(os.path.isfile(os.path.join(C.ICONS, n + ".svg")))

    def test_hole_from_a_selected_face_sets_position_and_axis(self):
        self.load("hole_blind")
        plate = self.asm.getObject("Plate")
        top = [i + 1 for i, f in enumerate(plate.Shape.Faces) if abs(f.CenterOfMass.z - 10) < 1e-6][0]
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(plate, "Face%d" % top, 4.0, 5.0, 10.0)
        from AssemblyFeaturePanel import TaskAssemblyFeature
        p = TaskAssemblyFeature(self.asm, kind="hole", selection=[])
        self.assertAlmostEqual(p.pos[0].value(), 4.0, places=4, msg="the picked point is the entry point")
        self.assertAlmostEqual(p.pos[2].value(), 10.0, places=4)
        self.assertAlmostEqual(p.axis[2].value(), -1.0, places=4)
        p.reject()
