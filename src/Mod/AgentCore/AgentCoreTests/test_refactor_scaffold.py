# SPDX-License-Identifier: LGPL-2.1-or-later
"""F6 scaffold generator: macros and FeaturePython modules saved, loaded and run headless."""

import os
import sys
import tempfile
import unittest

import FreeCAD

from AgentCore.refactor import grounding, scaffold

needs_pyright = unittest.skipIf(grounding.find_pyright()[0] is None, "pyright not installed")


class TestMacro(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="f6-macro-")
        self.addCleanup(self.tmp.cleanup)

    def test_write_register_and_run(self):
        path = scaffold.write_macro(self.tmp.name, "make-box", "Make a 12 mm box.",
                                    "box = doc.addObject('Part::Box', 'MacroBox')\nbox.Length = 12")
        self.assertTrue(path.endswith("make_box.FCMacro"))
        reg = scaffold.register(self.tmp.name, "macro", "extra", "extra.FCMacro")
        self.assertEqual(reg["make_box"]["kind"], "macro")
        doc = FreeCAD.newDocument("F6macro")
        try:
            scaffold.run_macro_file(path, doc)
            self.assertAlmostEqual(doc.getObject("MacroBox").Length.Value, 12.0)
            self.assertAlmostEqual(doc.getObject("MacroBox").Shape.Volume, 12 * 10 * 10, places=6)
        finally:
            FreeCAD.closeDocument(doc.Name)

    def test_macro_runs_as_main_without_a_document(self):
        path = scaffold.write_macro(self.tmp.name, "solo", "No document given.", "doc.addObject('Part::Sphere', 'S')")
        scaffold.run_macro_file(path)
        self.assertIsNotNone(FreeCAD.getDocument("solo").getObject("S"))
        FreeCAD.closeDocument("solo")

    @needs_pyright
    def test_generated_macro_is_grounded(self):
        src = scaffold.macro_source("m", "d", "doc.addObject('Part::Box', 'B')")
        self.assertTrue(grounding.check_source(src)["ok"])


class TestFeaturePython(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="f6-feature-")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: [sys.modules.pop(n, None) for n in ("F6Box", "F6Tube")])

    def test_no_gui_import_headless(self):
        scaffold.write_feature_module(self.tmp.name, "F6Box")
        src = open(os.path.join(self.tmp.name, "F6Box.py"), encoding="utf-8").read()
        self.assertIn("if FreeCAD.GuiUp:", src)
        self.assertIn("class ViewProviderF6Box", src)
        before = "FreeCADGui" in sys.modules
        mod = scaffold.load_feature_module(self.tmp.name, "F6Box")
        self.assertFalse(FreeCAD.GuiUp)
        self.assertFalse(hasattr(mod, "ViewProviderF6Box"))
        self.assertEqual("FreeCADGui" in sys.modules, before)

    def test_create_save_reload_recompute(self):
        scaffold.write_feature_module(self.tmp.name, "F6Box")
        mod = scaffold.load_feature_module(self.tmp.name, "F6Box")
        doc = FreeCAD.newDocument("F6feat")
        obj = mod.make(doc, "Feat")
        self.assertAlmostEqual(obj.Shape.Volume, 10 * 20 * 30, places=6)
        obj.Length = 5
        doc.recompute()
        self.assertAlmostEqual(obj.Shape.Volume, 5 * 20 * 30, places=6)
        obj.Proxy.version = 7
        path = os.path.join(self.tmp.name, "feat.FCStd")
        doc.saveAs(path)
        FreeCAD.closeDocument(doc.Name)
        again = FreeCAD.openDocument(path)
        try:
            o2 = again.getObject("Feat")
            self.assertEqual(type(o2.Proxy).__name__, "F6Box")
            self.assertEqual(o2.Proxy.version, 7)          # dumps/loads round trip
            self.assertAlmostEqual(o2.Length.Value, 5.0)
            again.recompute(None, True)
            self.assertNotIn("Error", o2.State)
            self.assertAlmostEqual(o2.Shape.Volume, 5 * 20 * 30, places=6)
        finally:
            FreeCAD.closeDocument(again.Name)

    def test_custom_properties_and_execute(self):
        props = [{"name": "Outer", "type": "App::PropertyLength", "group": "Tube", "doc": "Outer radius", "default": 10.0},
                 {"name": "Inner", "type": "App::PropertyLength", "group": "Tube", "doc": "Inner radius", "default": 6.0},
                 {"name": "High", "type": "App::PropertyLength", "group": "Tube", "doc": "Height", "default": 20.0}]
        body = ("outer = Part.makeCylinder(obj.Outer.Value, obj.High.Value)\n"
                "inner = Part.makeCylinder(obj.Inner.Value, obj.High.Value)\n"
                "obj.Shape = outer.cut(inner)")
        changed = "if prop == 'Inner' and obj.Inner.Value >= obj.Outer.Value:\n    obj.Inner = obj.Outer.Value / 2"
        scaffold.write_feature_module(self.tmp.name, "F6Tube", properties=props, execute_body=body, on_changed_body=changed)
        mod = scaffold.load_feature_module(self.tmp.name, "F6Tube")
        doc = FreeCAD.newDocument("F6tube")
        try:
            obj = mod.make(doc, "T")
            import math
            self.assertAlmostEqual(obj.Shape.Volume, math.pi * (100 - 36) * 20, places=4)
            obj.Inner = 12
            self.assertAlmostEqual(obj.Inner.Value, 5.0)      # onChanged ran
        finally:
            FreeCAD.closeDocument(doc.Name)

    @needs_pyright
    def test_generated_module_is_grounded(self):
        res = grounding.check_source(scaffold.featurepython_source("Grounded"))
        self.assertTrue(res["ok"], res["errors"])


if __name__ == "__main__":
    unittest.main()
