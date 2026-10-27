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
