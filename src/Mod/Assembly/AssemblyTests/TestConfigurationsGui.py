# SPDX-License-Identifier: LGPL-2.1-or-later
"""G06 slice 5b: the Configurations command, its task panel and the view provider of a configuration.

Run with the GUI binary: ``FreeCAD -t TestAssemblyConfigurationsGui``. The document is the slice 5a rig
(``configuration_gui_fixture``); every panel action is compared with the same 5a call made on a twin document,
and Cancel is compared with the full signature (every object and property) of the document before the panel.
"""

import json
import unittest

import FreeCAD as App
import FreeCADGui as Gui

import AssemblyConfiguration as AC
import ViewProviderAssemblyConfiguration as VPC
from AssemblyTests import configuration_gui_fixture as F


class Prompts:
    """Scripted answers for the questions the GUI asks; an unexpected question fails the test."""

    def __init__(self):
        self.asked = []
        self.discard_and_delete = None

    def ask_discard_and_delete(self, cfg, drift):
        self.asked.append(("discard_and_delete", cfg.Label, [d["property"] for d in drift]))
        if self.discard_and_delete is None:
            raise AssertionError("unexpected question: discard and delete")
        return self.discard_and_delete


class GuiCase(unittest.TestCase):
    def setUp(self):
        self.rig = None
        self.prompts = Prompts()
        self._saved = {"discard": VPC.ask_discard_and_delete}
        VPC.ask_discard_and_delete = self.prompts.ask_discard_and_delete

    def tearDown(self):
        VPC.ask_discard_and_delete = self._saved["discard"]
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


if __name__ == "__main__":
    unittest.main()
