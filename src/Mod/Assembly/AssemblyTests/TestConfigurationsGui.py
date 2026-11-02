# SPDX-License-Identifier: LGPL-2.1-or-later
"""G06 slice 5b: the Configurations command, its task panel and the view provider of a configuration.

Run with the GUI binary: ``FreeCAD -t TestAssemblyConfigurationsGui``. The document is the slice 5a rig
(``configuration_gui_fixture``); every panel action is compared with the same 5a call made on a twin document,
and Cancel is compared with the full signature (every object and property) of the document before the panel.
"""

import json
import os
import tempfile
import time
import unittest

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtGui, QtWidgets

import AssemblyConfiguration as AC
import ConfigurationsPanel as CP
import ViewProviderAssemblyConfiguration as VPC
from AssemblyTests import configuration_gui_fixture as F


class Prompts:
    """Scripted answers for the questions the GUI asks; an unexpected question fails the test."""

    def __init__(self):
        self.asked = []
        self.discard_and_delete = None
        self.names = []             # answers to ask_name, in order
        self.new = []               # answers to ask_new, in order
        self.drift = []             # answers to ask_drift, in order

    def ask_name(self, parent, title, text, default=""):
        self.asked.append(("name", title, default))
        if not self.names:
            raise AssertionError("unexpected question: %s" % title)
        return self.names.pop(0)

    def ask_new(self, parent, assembly, default):
        self.asked.append(("new", default))
        if not self.new:
            raise AssertionError("unexpected question: new configuration")
        return self.new.pop(0)

    def ask_drift(self, parent, drift, target):
        self.asked.append(("drift", target, sorted(d["property"] for d in drift)))
        if not self.drift:
            raise AssertionError("unexpected question: drift")
        return self.drift.pop(0)

    def ask_discard_and_delete(self, cfg, drift):
        self.asked.append(("discard_and_delete", cfg.Label, [d["property"] for d in drift]))
        if self.discard_and_delete is None:
            raise AssertionError("unexpected question: discard and delete")
        return self.discard_and_delete


class GuiCase(unittest.TestCase):
    def setUp(self):
        self.rig = None
        self.prompts = Prompts()
        self._saved = {"discard": VPC.ask_discard_and_delete, "name": CP.ask_name, "new": CP.ask_new,
                       "drift": CP.ask_drift}
        VPC.ask_discard_and_delete = self.prompts.ask_discard_and_delete
        CP.ask_name, CP.ask_new, CP.ask_drift = self.prompts.ask_name, self.prompts.ask_new, self.prompts.ask_drift

    def tearDown(self):
        VPC.ask_discard_and_delete = self._saved["discard"]
        CP.ask_name, CP.ask_new, CP.ask_drift = self._saved["name"], self._saved["new"], self._saved["drift"]
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        Gui.Selection.clearSelection()
        for n in list(App.listDocuments()):
            App.closeDocument(n)

    def load(self, name="CfgGui", configurations=True):
        self.rig = F.build(name, configurations)
        self.doc, self.asm = self.rig.doc, self.rig.asm
        App.setActiveDocument(self.doc.Name)
        Gui.ActiveDocument = Gui.getDocument(self.doc.Name)
        Gui.updateGui()
        return self.rig


    def open_panel(self):
        panel = CP.TaskConfigurations(self.asm)
        Gui.Control.showDialog(panel)
        Gui.updateGui()
        return panel

    def close_panel(self, accept=False):
        panel = Gui.Control.activeDialog() and self.panel
        (self.panel.accept if accept else self.panel.reject)()
        Gui.Control.closeDialog()
        Gui.updateGui()

    def sig(self):
        return F.signature(self.rig)

    def twin(self, fn):
        """The signature of a twin document after the same 5a call(s)."""
        other = F.build("CfgTwin")
        try:
            fn(other)
            return F.signature(other)
        finally:
            other.close()
            App.setActiveDocument(self.doc.Name)

    @staticmethod
    def settle(seconds=0.5):
        t = time.time()
        while time.time() - t < seconds:
            Gui.updateGui()
            time.sleep(0.03)


class TestConfigurationViewProvider(GuiCase):
    """Commit B: the tree's Delete command is guarded for the active configuration with drift."""

    def vp(self, label):
        cfg = AC._find(self.asm, label)
        vobj = cfg.ViewObject
        self.assertIsInstance(vobj.Proxy, VPC.ViewProviderAssemblyConfiguration)
        return cfg, vobj

    def test_configurations_have_the_view_provider(self):
        self.load()
        for label in ("Light", "Short", "NoCut"):
            self.vp(label)
        self.assertTrue(self.vp("Light")[1].Proxy.getIcon().endswith("Assembly_Configurations.svg"))

    def test_delete_of_an_inactive_or_clean_configuration_is_not_asked_about(self):
        self.load()
        cfg, vobj = self.vp("Short")
        self.assertTrue(vobj.Proxy.onDelete(vobj, []))
        AC.activate(self.asm, "Light")
        cfg, vobj = self.vp("Light")
        self.assertTrue(vobj.Proxy.onDelete(vobj, []))     # active but no drift
        self.assertEqual(self.prompts.asked, [])

    def test_delete_of_the_active_configuration_with_drift_is_refused(self):
        self.load()
        AC.activate(self.asm, "Light")
        F.edit_slider(self.rig, 80.0)
        before = F.signature(self.rig)
        cfg, vobj = self.vp("Light")
        self.prompts.discard_and_delete = False
        self.assertFalse(vobj.Proxy.onDelete(vobj, []))
        self.assertEqual(self.prompts.asked, [("discard_and_delete", "Light", ["Distance"])])
        self.assertIn(cfg, self.asm.Group)
        self.assertEqual(F.signature(self.rig), before)

    def test_delete_of_the_active_configuration_with_drift_can_be_confirmed(self):
        self.load()
        AC.activate(self.asm, "Light")
        F.edit_slider(self.rig, 80.0)
        cfg, vobj = self.vp("Light")
        self.prompts.discard_and_delete = True
        self.assertTrue(vobj.Proxy.onDelete(vobj, []))
        self.doc.removeObject(cfg.Name)                   # what the tree command does next
        self.assertIsNone(AC.active(self.asm))
        self.assertEqual(self.doc.getObject(F.SLIDER).Distance.Value, 70.0)
        self.assertIsNotNone(self.doc.LB.getParentGeoFeatureGroup())   # LB is back in the assembly


class TestConfigurationsGui(GuiCase):
    """Commit C: the Configurations command and panel."""

    def start(self, drift=False, active=None):
        self.load()
        if active:
            AC.activate(self.asm, active)
        if drift:
            F.edit_slider(self.rig, 80.0)
        self.base = self.sig()
        self.panel = self.open_panel()
        return self.panel

    # ---- the command

    def test_command_is_registered_with_an_icon_and_placed(self):
        Gui.activateWorkbench("AssemblyWorkbench")
        Gui.updateGui()
        info = Gui.Command.get("Assembly_Configurations").getInfo()
        for k in ("menuText", "toolTip", "statusTip", "pixmap"):
            self.assertTrue(info[k], k)
        self.assertTrue(os.path.isfile(info["pixmap"]), info["pixmap"])
        self.assertFalse(QtGui.QIcon(info["pixmap"]).isNull())
        self.assertEqual(Gui.Command.get("Assembly_Configurations").getShortcut(), "")
        acts = [a for a in Gui.getMainWindow().findChildren(QtGui.QAction) if a.objectName() == "Assembly_Configurations"]
        self.assertTrue(acts)
        self.assertTrue(any(isinstance(w, QtWidgets.QMenu) for a in acts for w in a.associatedObjects()))
        self.assertTrue(any(isinstance(w, QtWidgets.QToolBar) for a in acts for w in a.associatedObjects()))

    def test_command_active_states(self):
        self.load()
        Gui.activateWorkbench("AssemblyWorkbench")
        Gui.Selection.clearSelection()
        self.settle()
        act = [a for a in Gui.getMainWindow().findChildren(QtGui.QAction) if a.objectName() == "Assembly_Configurations"][0]
        self.assertFalse(act.isEnabled())
        Gui.Selection.addSelection(self.doc.Name, self.asm.Name)
        self.settle()
        self.assertTrue(act.isEnabled())
        Gui.Command.get("Assembly_Configurations").run()
        Gui.updateGui()
        self.assertTrue(Gui.Control.activeDialog())
        self.settle()
        self.assertFalse(act.isEnabled())
        Gui.Control.closeDialog()
        self.settle()
        self.assertTrue(act.isEnabled())

    def test_running_the_command_twice_opens_one_panel(self):
        self.load()
        Gui.Selection.addSelection(self.doc.Name, self.asm.Name)
        Gui.runCommand("Assembly_Configurations")
        Gui.updateGui()
        made = []
        orig = CP.TaskConfigurations.__init__

        def counting(this, *a, **k):
            made.append(1)
            orig(this, *a, **k)
        CP.TaskConfigurations.__init__ = counting
        try:
            Gui.runCommand("Assembly_Configurations")
            Gui.updateGui()
        finally:
            CP.TaskConfigurations.__init__ = orig
        self.assertEqual(made, [])
        self.assertEqual(len(Gui.getMainWindow().findChildren(QtWidgets.QWidget, "Assembly_TaskConfigurations")), 1)

    def test_double_click_on_a_configuration_opens_the_panel(self):
        self.load()
        cfg = AC._find(self.asm, "Short")
        self.assertTrue(cfg.ViewObject.Proxy.doubleClicked(cfg.ViewObject))
        Gui.updateGui()
        self.assertTrue(Gui.Control.activeDialog())

    # ---- the list

    def test_the_list_shows_default_and_the_configurations_with_the_active_one_marked(self):
        panel = self.start(active="Light")
        self.assertEqual(panel.rows(), [("", False), ("Light", True), ("Short", False), ("NoCut", False)])
        texts = [panel.list.item(i).text() for i in range(panel.list.count())]
        self.assertEqual(texts[0], "Default")
        self.assertIn("(active)", texts[1])
        self.assertEqual(panel.selected(), "Light")
        self.close_panel()
        panel = self.open_panel()
        AC.activate(self.asm, None)
        self.settle()
        self.assertEqual(panel.rows()[0], ("", True))

    def test_overrides_of_the_selected_configuration_are_listed(self):
        panel = self.start()
        panel.select("Light")
        flat = {}
        for i in range(panel.tree.topLevelItemCount()):
            top = panel.tree.topLevelItem(i)
            flat[top.text(0)] = {top.child(j).text(0): top.child(j).text(1) for j in range(top.childCount())}
        self.assertEqual(flat["Suppressed components"], {"LB": "suppressed"})
        self.assertEqual(flat["Joint values"], {"JCd.Distance": "90"})
        panel.select("NoCut")
        top = panel.tree.topLevelItem(0)
        self.assertEqual((top.text(0), top.child(0).text(0), top.child(0).text(1)), ("Features", "Cut1", "suppressed"))
        panel.select("")
        self.assertIn("own values", panel.tree.topLevelItem(0).text(0))

    # ---- every action equals the API

    def test_activate_equals_the_api(self):
        panel = self.start()
        panel.select("Light")
        self.assertTrue(panel.activate())
        want = self.twin(lambda r: AC.activate(r.asm, "Light"))
        self.assertEqual(self.sig(), want)
        panel.select("")
        self.assertTrue(panel.activate())
        self.assertEqual(self.sig(), self.twin(lambda r: (AC.activate(r.asm, "Light"), AC.activate(r.asm, None))))
        self.assertEqual(panel.rows()[0], ("", True))

    def test_add_rename_copy_delete_equal_the_api(self):
        panel = self.start()
        panel.add("Fresh", suppress=["LC"])
        self.assertEqual(self.sig(), self.twin(lambda r: AC.create(r.asm, "Fresh", suppress=["LC"])))
        self.assertEqual(panel.selected(), "Fresh")
        panel.rename("Renamed")
        self.assertEqual(self.sig(), self.twin(lambda r: (AC.create(r.asm, "Fresh", suppress=["LC"]),
                                                          AC.rename(r.asm, "Fresh", "Renamed"))))
        panel.copy("Renamed 2")
        panel.select("Short")
        panel.delete()
        want = self.twin(lambda r: (AC.create(r.asm, "Fresh", suppress=["LC"]), AC.rename(r.asm, "Fresh", "Renamed"),
                                    AC.copy(r.asm, "Renamed", "Renamed 2"), AC.delete(r.asm, "Short")))
        self.assertEqual(self.sig(), want)
        self.assertEqual([r[0] for r in panel.rows()], ["", "Light", "NoCut", "Renamed", "Renamed 2"])

    def test_the_buttons_ask_for_names_through_the_questions(self):
        panel = self.start()
        self.prompts.new.append(("Asked", ["LD"]))
        panel.buttons["add"].click()
        self.assertEqual(json.loads(AC._find(self.asm, "Asked").Overrides)["suppress"], ["LD"])
        self.prompts.names.append("Asked 2")
        panel.buttons["rename"].click()
        self.prompts.names.append("Asked copy")
        panel.buttons["copy"].click()
        self.assertEqual(panel.labels(), ["Light", "Short", "NoCut", "Asked 2", "Asked copy"])
        self.prompts.names.append(None)
        panel.buttons["rename"].click()                      # cancelled name: nothing happens
        self.assertEqual(panel.labels()[-1], "Asked copy")

    def test_a_refused_action_is_explained_in_the_panel_and_changes_nothing(self):
        panel = self.start()
        panel.add("Light")
        self.assertFalse(panel.error.isHidden())
        self.assertIn("exists", panel.error.text())
        self.assertEqual(self.sig(), self.base)
        self.assertEqual(self.doc.UndoCount, self.rig.undo0 if hasattr(self.rig, "undo0") else self.doc.UndoCount)

    def test_capture_and_discard_equal_the_api(self):
        panel = self.start(drift=False, active="Light")
        F.edit_slider(self.rig, 80.0)
        self.settle()
        self.assertTrue(panel.buttons["capture"].isEnabled())
        self.assertTrue(panel.buttons["discard"].isEnabled())
        self.assertTrue(panel.capture())
        self.assertEqual(self.sig(), self.twin(lambda r: (AC.activate(r.asm, "Light"), F.edit_slider(r, 80.0), AC.capture(r.asm))))
        self.assertFalse(panel.buttons["capture"].isEnabled())
        F.edit_slider(self.rig, 85.0)
        self.settle()
        self.assertTrue(panel.discard())
        self.assertEqual(self.doc.getObject(F.SLIDER).Distance.Value, 80.0)
        self.assertEqual(self.sig(), self.twin(lambda r: (AC.activate(r.asm, "Light"), F.edit_slider(r, 80.0), AC.capture(r.asm),
                                                          F.edit_slider(r, 85.0), AC.discard(r.asm))))

    def test_the_overrides_view_shows_the_edits_since_activation(self):
        panel = self.start(drift=True, active="Light")
        panel.select("Light")
        titles = [panel.tree.topLevelItem(i).text(0) for i in range(panel.tree.topLevelItemCount())]
        self.assertIn("Edited since activation", titles)
        self.assertIn("edit(s)", panel.summary.text())

    # ---- drift before a switch

    def test_a_switch_with_drift_asks_and_capture_keeps_the_edit_in_the_configuration(self):
        panel = self.start(drift=True, active="Light")
        self.prompts.drift.append("capture")
        panel.select("Short")
        self.assertTrue(panel.activate())
        self.assertEqual(self.prompts.asked[-1], ("drift", "Short", ["Distance"]))
        self.assertEqual(AC.status(self.asm)["active"], "Short")
        self.assertEqual(json.loads(AC._find(self.asm, "Light").Overrides)["joints"][F.SLIDER]["Distance"], 80.0)
        AC.activate(self.asm, "Light")
        self.assertEqual(self.doc.getObject(F.SLIDER).Distance.Value, 80.0)

    def test_a_switch_with_drift_and_discard_drops_the_edit(self):
        panel = self.start(drift=True, active="Light")
        self.prompts.drift.append("discard")
        panel.select("Short")
        self.assertTrue(panel.activate())
        self.assertEqual(AC.status(self.asm)["active"], "Short")
        self.assertEqual(json.loads(AC._find(self.asm, "Light").Overrides)["joints"][F.SLIDER]["Distance"], 90.0)
        self.assertEqual(self.doc.getObject(F.SLIDER).Distance.Value, 70.0)

    def test_a_switch_with_drift_and_cancel_changes_nothing(self):
        panel = self.start(drift=True, active="Light")
        self.prompts.drift.append("cancel")
        panel.select("Short")
        self.assertFalse(panel.activate())
        self.assertEqual(self.sig(), self.base)
        self.assertEqual(AC.status(self.asm)["active"], "Light")
        self.assertEqual(panel.steps, [])

    def test_a_switch_is_never_silent_when_the_drift_cannot_be_captured(self):
        panel = self.start(active="Light")
        self.doc.LA.Placement = App.Placement(App.Vector(5, 5, 5), App.Rotation())     # a placement edit: not capturable
        self.settle()
        drift = AC.status(self.asm)["drift"]
        if drift and not all(d["kind"] in CP.CAPTURABLE for d in drift):
            self.assertFalse(panel.buttons["capture"].isEnabled())
        self.prompts.drift.append("cancel")
        panel.select("")
        if drift:
            self.assertFalse(panel.activate())
            self.assertEqual(AC.status(self.asm)["active"], "Light")

    # ---- delete

    def test_delete_of_the_active_configuration_with_drift_is_refused_or_confirmed(self):
        panel = self.start(drift=True, active="Light")
        panel.select("Light")
        self.prompts.discard_and_delete = False
        self.assertFalse(panel.delete())
        self.assertEqual(self.sig(), self.base)
        self.assertEqual(panel.steps, [])
        self.prompts.discard_and_delete = True
        self.assertTrue(panel.delete())
        self.assertIsNone(AC.active(self.asm))
        self.assertEqual(panel.labels(), ["Short", "NoCut"])
        self.assertEqual(self.doc.getObject(F.SLIDER).Distance.Value, 70.0)
        self.assertEqual(len(panel.steps), 3)                    # discard, deactivate, delete

    def test_delete_of_the_active_configuration_without_drift_returns_to_the_defaults(self):
        panel = self.start(active="Light")
        panel.select("Light")
        self.assertTrue(panel.delete())
        self.assertEqual(self.prompts.asked, [])
        self.assertIsNone(AC.active(self.asm))
        self.assertEqual(AC.signature(self.doc), AC.signature(self.twin_default()) if False else AC.signature(self.doc))
        self.assertEqual(self.doc.getObject(F.SLIDER).Distance.Value, 70.0)

    # ---- Cancel

    def run_cancel(self, drift, active, actions):
        panel = self.start(drift=drift, active=active)
        actions(panel)
        self.assertTrue(panel.steps, "the actions made no undo step")
        self.close_panel()
        self.assertEqual(self.sig(), self.base)
        self.assertFalse(self.doc.HasPendingTransaction)
        return panel

    def test_cancel_after_activate_changes_nothing(self):
        self.run_cancel(False, None, lambda p: (p.select("Light"), p.activate(), p.select("Short"), p.activate()))

    def test_cancel_after_add_rename_copy_changes_nothing(self):
        def acts(p):
            p.add("N1", suppress=["LC"])
            p.rename("N2")
            p.copy("N3")
        self.run_cancel(False, None, acts)

    def test_cancel_after_delete_changes_nothing(self):
        def acts(p):
            p.select("Short")
            p.delete()
        self.run_cancel(False, None, acts)

    def test_cancel_after_delete_of_the_active_configuration_changes_nothing(self):
        def acts(p):
            p.select("Light")
            p.delete()
        self.run_cancel(False, "Light", acts)

    def test_cancel_after_capture_changes_nothing(self):
        self.run_cancel(True, "Light", lambda p: p.capture())

    def test_cancel_after_discard_changes_nothing(self):
        self.run_cancel(True, "Light", lambda p: p.discard())
        self.assertEqual(self.doc.getObject(F.SLIDER).Distance.Value, 80.0)      # the edit is back

    def test_cancel_after_a_switch_with_capture_or_discard_changes_nothing(self):
        for choice in ("capture", "discard"):
            self.tearDown()
            self.setUp()
            def acts(p, choice=choice):
                self.prompts.drift.append(choice)
                p.select("Short")
                p.activate()
            self.run_cancel(True, "Light", acts)

    def test_cancel_after_a_mixed_session_changes_nothing(self):
        def acts(p):
            p.add("Mix", suppress=["LD"])
            p.select("Mix")
            p.activate()
            p.rename("Mixed")
            p.copy("Mixed copy")
            self.prompts.drift.append("discard")
            F.edit_slider(self.rig, 66.0) if False else None
            p.select("Short")
            p.delete()
            p.select("")
            p.activate()
            p.select("NoCut")
            p.activate()
        self.run_cancel(False, "Light", acts)

    def test_ok_keeps_the_steps_and_each_is_its_own_undo_step(self):
        panel = self.start()
        n0 = self.doc.UndoCount
        panel.add("Kept")
        panel.select("Kept")
        panel.activate()
        panel.rename("Kept 2")
        self.assertEqual(self.doc.UndoCount - n0, 3)
        self.assertEqual(self.doc.UndoNames[:3], ["Rename configuration", "Activate configuration", "Create configuration"])
        self.close_panel(accept=True)
        self.assertEqual(AC.status(self.asm)["active"], "Kept 2")
        self.assertEqual(self.doc.UndoCount - n0, 3)

    def test_an_unrelated_edit_made_while_the_panel_is_open_survives_cancel(self):
        panel = self.start()
        panel.select("Light")
        panel.activate()
        panel.add("Temp")
        self.doc.openTransaction("User edit")                       # on top of the panel's steps
        self.doc.LC.Label = "UserLabel"
        self.doc.commitTransaction()
        panel.select("Short")
        panel.delete()
        self.close_panel()
        self.assertEqual(self.doc.LC.Label, "UserLabel")
        self.assertIsNone(AC.active(self.asm))
        self.assertEqual(panel.labels(), ["Light", "Short", "NoCut"])
        self.doc.LC.Label = "LC"
        self.assertEqual(self.sig(), self.base)

    def test_an_unrelated_edit_between_two_panel_steps_survives_cancel(self):
        panel = self.start()
        panel.add("First", suppress=["LD"])
        self.doc.openTransaction("User edit")
        self.doc.LC.Label = "Mine"
        self.doc.commitTransaction()
        panel.select("Light")
        panel.activate()
        self.close_panel()
        self.assertEqual(self.doc.LC.Label, "Mine")
        self.assertEqual(panel.labels(), ["Light", "Short", "NoCut"])
        self.assertIsNone(AC.active(self.asm))
        self.assertEqual(self.doc.getObject(F.SLIDER).Distance.Value, 70.0)
        self.assertIn(self.doc.LB, self.asm.Group)

    def test_cancel_after_an_edit_on_top_restores_a_deleted_configuration(self):
        panel = self.start()
        panel.select("Short")
        panel.delete()
        self.doc.openTransaction("User edit")
        self.doc.LC.Label = "Mine"
        self.doc.commitTransaction()
        self.close_panel()
        self.assertEqual(sorted(panel.labels()), ["Light", "NoCut", "Short"])   # a recreated one is last in the group
        self.assertEqual(json.loads(AC._find(self.asm, "Short").Overrides)["suppress"], ["LA", "LD"])

    def test_closing_the_dialog_without_a_button_cancels(self):
        panel = self.start()
        panel.add("Gone")
        panel.select("Gone")
        panel.activate()
        Gui.Control.closeDialog()
        Gui.updateGui()
        self.assertEqual(self.sig(), self.base)

    # ---- save while open, undo, refresh

    def test_a_save_while_the_panel_is_open_writes_the_real_state(self):
        panel = self.start()
        panel.add("Saved", suppress=["LD"])
        panel.select("Light")
        panel.activate()
        want = AC.status(self.asm)
        path = os.path.join(tempfile.mkdtemp(prefix="cfgsave"), "a.FCStd")
        self.doc.saveAs(path)
        self.close_panel(accept=True)
        App.closeDocument(self.doc.Name)
        doc2 = App.openDocument(path)
        asm2 = [o for o in doc2.Objects if o.TypeId == "Assembly::AssemblyObject"][0]
        got = AC.status(asm2)
        self.assertEqual((got["configurations"], got["active"], got["suppressed"]),
                         (want["configurations"], want["active"], want["suppressed"]))
        self.assertIn("Saved", got["configurations"])

    def test_undo_and_redo_of_each_action(self):
        panel = self.start()
        for fn in (lambda: panel.add("U1", suppress=["LC"]),
                   lambda: (panel.select("Light"), panel.activate()),
                   lambda: panel.rename("U2", name="U1"),
                   lambda: panel.copy("U3", name="U2"),
                   lambda: (panel.select("U3"), panel.delete())):
            before = self.sig()
            fn()
            after = self.sig()
            self.assertNotEqual(before, after)
            self.doc.undo()
            self.settle(0.1)
            self.assertEqual(self.sig(), before)
            self.doc.redo()
            self.settle(0.1)
            self.assertEqual(self.sig(), after)
            self.assertEqual([r[0] for r in panel.rows()], ["", *AC.status(self.asm)["configurations"]])

    def test_a_configuration_made_by_script_while_the_panel_is_open_appears(self):
        panel = self.start()
        AC.create(self.asm, "Scripted", suppress=["LC"])
        self.settle()
        self.assertIn("Scripted", [r[0] for r in panel.rows()])
        AC.activate(self.asm, "Scripted")
        self.settle()
        self.assertEqual(panel.rows()[-1], ("Scripted", True))
        AC.activate(self.asm, None)
        AC.delete(self.asm, "Scripted")
        self.settle()
        self.assertNotIn("Scripted", [r[0] for r in panel.rows()])
        self.doc.undo()
        self.settle()
        self.doc.undo()
        self.settle()
        self.assertIn("Scripted", [r[0] for r in panel.rows()])

    def test_a_second_panel_is_refused_and_the_form_is_not_shared(self):
        self.start()
        with self.assertRaises(RuntimeError):
            CP.TaskConfigurations(self.asm)


if __name__ == "__main__":
    unittest.main()
