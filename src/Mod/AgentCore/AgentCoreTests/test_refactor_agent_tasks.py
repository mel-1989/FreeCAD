# SPDX-License-Identifier: LGPL-2.1-or-later
"""The F6 eval task banks are well formed: unique ids, compilable checks, and no check that an empty document satisfies."""

import unittest

import FreeCAD

from AgentCoreTests.fixtures.refactor import agent_tasks


class TestTaskBanks(unittest.TestCase):
    def test_macro_bank(self):
        tasks = agent_tasks.macro_tasks(100)
        self.assertEqual(len(tasks), 100)
        self.assertEqual(len({t["id"] for t in tasks}), 100)
        self.assertGreaterEqual(len({t["family"] for t in tasks}), 20)
        doc = FreeCAD.newDocument("T")
        try:
            for t in tasks:
                code = compile(t["check"], t["id"], "eval")
                try:
                    holds = bool(eval(code, {"FreeCAD": FreeCAD, "doc": doc, "App": FreeCAD}))
                except Exception:
                    holds = False
                self.assertFalse(holds, f"{t['id']}: check is already true on an empty document")
        finally:
            FreeCAD.closeDocument("T")

    def test_macro_bank_is_deterministic(self):
        self.assertEqual(agent_tasks.macro_tasks(100), agent_tasks.macro_tasks(100))

    def test_reference_solutions_satisfy_their_checks(self):
        by_family = {t["family"]: t for t in agent_tasks.macro_tasks(100)}
        doc = FreeCAD.newDocument("T")
        try:
            t = by_family["box"]
            import re
            a, b, c = map(int, re.search(r"Length=(\d+), Width=(\d+), Height=(\d+)", t["task"]).groups())
            box = doc.addObject("Part::Box", "Box")
            box.Length, box.Width, box.Height = a, b, c
            doc.recompute()
            self.assertTrue(eval(t["check"], {"FreeCAD": FreeCAD, "doc": doc}))
            box.Height = c + 1
            doc.recompute()
            self.assertFalse(eval(t["check"], {"FreeCAD": FreeCAD, "doc": doc}))
        finally:
            FreeCAD.closeDocument("T")

    def test_refactor_bank_shape(self):
        listing = "Sketcher::SketchObject PartDesign::Pad PartDesign::Pocket PartDesign::Body"
        tasks = agent_tasks.refactor_tasks({f"m{i:02d}_x.FCStd": listing for i in range(13)}, 100)
        self.assertEqual(len(tasks), 100)
        self.assertEqual(len({t["id"] for t in tasks}), 100)
        for t in tasks:
            compile(t["check"], t["id"], "eval")


if __name__ == "__main__":
    unittest.main()
