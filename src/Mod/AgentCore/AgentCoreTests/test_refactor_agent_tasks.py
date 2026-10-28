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
        ref = {"lift_varset": 3, "lift_sheet": 3, "bodies": {"Body": 3}}
        tasks = agent_tasks.refactor_tasks({f"m{i:02d}_x.FCStd": listing for i in range(13)}, 100, {f"m{i:02d}_x.FCStd": ref for i in range(13)})
        self.assertEqual(len(tasks), 100)
        self.assertEqual(len({t["id"] for t in tasks}), 100)
        for t in tasks:
            compile(t["check"], t["id"], "eval")

    def test_tasks_without_a_reference_binding_are_dropped(self):
        listing = "PartDesign::Pad PartDesign::Body"
        tasks = agent_tasks.refactor_tasks({"m01_x.FCStd": listing}, 100, {"m01_x.FCStd": {"lift_varset": 0, "lift_sheet": 0, "bodies": {"Body": 0}}})
        self.assertFalse({t["family"] for t in tasks} & {"lift_all_varset", "lift_all_sheet", "across_bodies"})


def _task(family, ref):
    listing = "Sketcher::SketchObject PartDesign::Pad PartDesign::Pocket PartDesign::Body"
    return next(t for t in agent_tasks.refactor_tasks({"m01_x.FCStd": listing}, 100, {"m01_x.FCStd": ref}) if t["family"] == family)


def _holds(t, doc):
    from AgentCore import refactor
    return bool(eval(t["check"], {"FreeCAD": FreeCAD, "doc": doc, "App": FreeCAD, "refactor": refactor, "ref": t.get("ref") or {}}))


class TestStrongChecks(unittest.TestCase):
    """lift_all_* needs literals really replaced by expressions, across_bodies needs every body changed."""

    def setUp(self):
        self.doc = FreeCAD.newDocument("F6chk")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def _ref_lift(self, target):
        from AgentCore import refactor
        from AgentCoreTests.fixtures.refactor import refactor_builders as rb
        rb.plate_with_slot(self.doc)
        scratch = FreeCAD.newDocument("F6chkref")
        try:
            rb.plate_with_slot(scratch)
            return refactor.lift_all(scratch, target=target)["count"]
        finally:
            FreeCAD.closeDocument(scratch.Name)

    def test_empty_container_does_not_satisfy_lift_all(self):
        # The old check was "the container exists": creating it empty passed.
        for fam, target, typ in (("lift_all_varset", "VarSet", "App::VarSet"), ("lift_all_sheet", "Spreadsheet", "Spreadsheet::Sheet")):
            n = self._ref_lift(target)
            self.assertGreater(n, 0)
            t = _task(fam, {"lift_varset": n, "lift_sheet": n, "bodies": {"Body": 1}})
            self.assertFalse(_holds(t, self.doc), "no-op")
            self.doc.addObject(typ, "Parameters")
            self.assertFalse(_holds(t, self.doc), "empty container")
            self.doc.removeObject("Parameters")

    def test_partial_lift_fails_and_full_lift_passes(self):
        from AgentCore import refactor
        n = self._ref_lift("VarSet")
        t = _task("lift_all_varset", {"lift_varset": n, "lift_sheet": n, "bodies": {"Body": 1}})
        refactor.lift_all(self.doc, target="VarSet", limit=n - 1)
        self.assertFalse(_holds(t, self.doc), "one literal still unlifted")
        refactor.lift_all(self.doc, target="VarSet")
        self.assertTrue(_holds(t, self.doc))

    def test_across_bodies_needs_every_body(self):
        from AgentCore import refactor
        from AgentCoreTests.fixtures.refactor import refactor_builders as rb
        a, b = rb.two_bodies(self.doc)
        t = _task("across_bodies", {"lift_varset": 1, "lift_sheet": 1, "bodies": {"BodyA": 1, "BodyB": 1}})
        self.assertFalse(_holds(t, self.doc), "no-op")
        refactor.apply_across_bodies(self.doc, refactor.ops.standardise_body, bodies_=[a])
        self.doc.recompute()
        self.assertFalse(_holds(t, self.doc), "only the first body changed")
        refactor.across_all_bodies(self.doc)
        self.doc.recompute()
        self.assertTrue(_holds(t, self.doc))


class TestPromptsHideTheCheck(unittest.TestCase):
    def test_macro_and_refactor_prompts_do_not_contain_the_check(self):
        from AgentCoreTests.fixtures.refactor import agent_eval
        for t in agent_tasks.macro_tasks(100):
            self.assertNotIn(t["check"], agent_eval.macro_prompt(t), t["id"])
            self.assertNotIn(t["check"], agent_eval.macro_prompt(t, "pass\n", "the macro ran without error"), t["id"])
            self.assertNotIn("abs(", agent_eval.macro_prompt(t, "pass\n", "x"), t["id"])
        t = _task("lift_all_varset", {"lift_varset": 2, "lift_sheet": 2, "bodies": {"Body": 1}})
        self.assertNotIn(t["check"], agent_eval.refactor_prompt("listing", t))
        self.assertNotIn("ExpressionEngine", agent_eval.refactor_prompt("listing", t))


class TestPortableResults(unittest.TestCase):
    """Committed result files carry no machine paths; the writers scrub them."""

    def test_scrub_rewrites_source_and_scratch_roots(self):
        from AgentCoreTests.fixtures.refactor import portable

        t = (
            '{"a": "/home/x/src/freecad/data/a.FCStd", "b": "/work/run1/m01.FCStd", '
            '"c": "/private/tmp/claude-501/-p/sid/scratchpad/q.FCStd"}'
        )
        out = portable.scrub_text(t, fc_src="/home/x/src/freecad", work_dirs=["/work/run1"])
        self.assertIn("$FC_SRC/data/a.FCStd", out)
        self.assertIn("$WORK/m01.FCStd", out)
        self.assertIn("$WORK/q.FCStd", out)

    def test_committed_results_have_no_machine_paths(self):
        import os

        d = os.path.join(os.path.dirname(__file__), "fixtures", "refactor", "results")
        for n in sorted(os.listdir(d)):
            with open(os.path.join(d, n), encoding="utf-8") as fh:
                txt = fh.read()
            for bad in ("/Users/", "/private/tmp/claude", "/home/", "/var/folders"):
                self.assertNotIn(bad, txt, f"{n} contains {bad}")


if __name__ == "__main__":
    unittest.main()
