# SPDX-License-Identifier: LGPL-2.1-or-later
"""F6 static grounding: pyright against freecad-stubs rejects invented API calls before exec."""

import unittest

import FreeCAD

from AgentCore.refactor import grounding

GOOD = """
import FreeCAD
import Part

doc = FreeCAD.newDocument("g")
box = Part.makeBox(1, 2, 3)
doc.addObject("Part::Feature", "A")
doc.recompute()
"""

INVENTED_MODULE_FN = GOOD.replace("Part.makeBox(", "Part.makeBoxx(")
INVENTED_METHOD = GOOD.replace("doc.addObject(", "doc.addObjectt(")
INVENTED_MODULE = GOOD + "\nimport Partt\n"
WRONG_ARGS = GOOD + "\nPart.makeBox(1, 2)\n"


@unittest.skipIf(grounding.find_pyright()[0] is None, "pyright not installed")
class TestGrounding(unittest.TestCase):
    def test_valid_macro_passes(self):
        res = grounding.check_source(GOOD)
        self.assertTrue(res["ok"], res["errors"])

    def test_invented_calls_are_rejected(self):
        for label, src in (("module fn", INVENTED_MODULE_FN), ("document method", INVENTED_METHOD),
                           ("module", INVENTED_MODULE), ("arguments", WRONG_ARGS)):
            res = grounding.check_source(src)
            self.assertFalse(res["ok"], label)
            self.assertTrue(res["errors"], label)
        names = grounding.check_source(INVENTED_MODULE_FN)["errors"][0]
        self.assertEqual(names["rule"], "reportAttributeAccessIssue")
        self.assertEqual(names["line"], 6)

    def test_rejected_source_never_executes(self):
        marker = FreeCAD.newDocument("G1")
        try:
            src = "import FreeCAD\nFreeCAD.getDocument('G1').addObject('Part::Box', 'ShouldNotExist')\nFreeCAD.nonexistentFn()\n"
            with self.assertRaises(grounding.GroundingError):
                grounding.grounded_exec(src)
            self.assertIsNone(marker.getObject("ShouldNotExist"))
            ok = "import FreeCAD\nFreeCAD.getDocument('G1').addObject('Part::Box', 'Exists')\n"
            grounding.grounded_exec(ok)
            self.assertIsNotNone(marker.getObject("Exists"))
        finally:
            FreeCAD.closeDocument("G1")

    def test_ignore_rules_and_unavailable(self):
        res = grounding.check_source(INVENTED_MODULE_FN, ignore_rules=("reportAttributeAccessIssue",))
        self.assertTrue(res["ok"])


class TestGroundingUnavailable(unittest.TestCase):
    def test_missing_pyright_is_not_a_pass(self):
        import os
        old = os.environ.get("CADFORK_PYRIGHT")
        os.environ["CADFORK_PYRIGHT"] = "/nonexistent/pyright"
        try:
            self.assertEqual(grounding.find_pyright(), (None, None))
            res = grounding.check_source(GOOD)
            self.assertFalse(res["ok"])
            self.assertTrue(res["unavailable"])
        finally:
            if old is None:
                os.environ.pop("CADFORK_PYRIGHT")
            else:
                os.environ["CADFORK_PYRIGHT"] = old


if __name__ == "__main__":
    unittest.main()


REAL_ATTRS = """
import Part
s = Part.makeBox(1, 2, 3)
v = s.Volume
a = s.Area
b = s.BoundBox
w = b.XLength
f = s.fuse(Part.makeBox(1, 1, 1))
n = len(s.Faces)
print(Part.makeBox(2, 2, 2).Volume, s.CenterOfMass)
"""
INVENTED_ATTRS = {
    "Volumee": "import Part\nx = Part.makeBox(1, 2, 3).Volumee\n",
    "Areaa": "import Part\ns = Part.makeBox(1, 2, 3)\nprint(s.Areaa)\n",
    "BoundBoxx": "import Part\ns = Part.makeBox(1, 2, 3)\nprint(s.BoundBoxx.XLength)\n",
    "XLenght": "import Part\ns = Part.makeBox(1, 2, 3)\nprint(s.BoundBox.XLenght)\n",
    "fusee": "import Part\ns = Part.makeBox(1, 2, 3)\ns2 = s.fusee(Part.makeBox(1, 1, 1))\n",
    "Facess": "import Part\nsolid = Part.makeCylinder(1, 2)\nfor f in solid.Facess:\n    pass\n",
}


@unittest.skipIf(grounding.find_pyright()[0] is None, "pyright not installed")
class TestTopoShapeAttributes(unittest.TestCase):
    """Part.makeBox(1, 2, 3).Volumee used to pass: the generated stubs left every Part.Shape subclass with an unknown base."""

    def test_invented_attributes_fail_in_pyright_alone(self):
        for name, src in INVENTED_ATTRS.items():
            real = grounding.introspect_source
            grounding.introspect_source = lambda s: []          # pyright on its own, no fallback
            try:
                res = grounding.check_source(src)
            finally:
                grounding.introspect_source = real
            self.assertFalse(res["ok"], name)
            self.assertTrue(any(e["rule"] == "reportAttributeAccessIssue" and name in e["message"] for e in res["errors"]), (name, res["errors"]))

    def test_invented_attributes_fail_in_introspection_alone(self):
        for name, src in INVENTED_ATTRS.items():
            errs = grounding.introspect_source(src)
            self.assertTrue(any(name in e["message"] for e in errs), (name, errs))

    def test_real_attributes_pass_both_layers(self):
        self.assertEqual(grounding.introspect_source(REAL_ATTRS), [])
        res = grounding.check_source(REAL_ATTRS)
        self.assertTrue(res["ok"], res["errors"])

    def test_grounded_exec_rejects_invented_attribute(self):
        with self.assertRaises(grounding.GroundingError):
            grounding.grounded_exec(INVENTED_ATTRS["Volumee"], {})

    def test_introspection_ignores_shadowed_and_unknown_names(self):
        src = "import Part\ns = Part.makeBox(1, 2, 3)\ndef f(s):\n    return s.whatever\ns = 4\nprint(s.real)\n"
        self.assertEqual(grounding.introspect_source(src), [])


@unittest.skipIf(grounding.find_pyright()[0] is None, "pyright not installed")
class TestStubPatch(unittest.TestCase):
    def test_patched_stubs_resolve_the_shape_base(self):
        exe, _ = grounding.find_pyright()
        path = grounding.patched_stub_path(exe)
        self.assertTrue(path)
        text = open(path + "/Part/__init__.pyi", encoding="utf-8").read()
        self.assertNotIn("PartModule.Geom2d", text)
        self.assertIn("import Part.Geom2d", text)
