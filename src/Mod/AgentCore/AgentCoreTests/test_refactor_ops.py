# SPDX-License-Identifier: LGPL-2.1-or-later
"""F6 refactor operations: relabel, lift into VarSet or Spreadsheet, across bodies, conservative merge."""

import os
import unittest

import FreeCAD

from AgentCore import refactor
from AgentCoreTests.fixtures.refactor import refactor_builders as rb


def shapes_equal(a, b):
    from AgentCore.refactor.ops import shapes_identical
    return shapes_identical(a.Shape, b.Shape)


class Doc:
    def __enter__(self):
        self.doc = FreeCAD.newDocument("F6ops")
        return self.doc

    def __exit__(self, *exc):
        FreeCAD.closeDocument(self.doc.Name)


class TestRelabel(unittest.TestCase):
    def test_label_changes_name_does_not(self):
        with Doc() as doc:
            rb.plate_with_slot(doc)
            names = [o.Name for o in doc.Objects]
            r = refactor.invariant(doc, lambda d: refactor.relabel_all(d))
            self.assertTrue(r["ok"], r["violations"])
            self.assertEqual([o.Name for o in doc.Objects], names)
            self.assertGreaterEqual(r["result"]["count"], 5)
            self.assertEqual(doc.getObject("Pad").Label, "Body_Pad1")

    def test_relabel_explicit(self):
        with Doc() as doc:
            rb.plate_with_slot(doc)
            out = refactor.relabel(doc, {"Pad": "Base plate"})
            self.assertEqual(out["changed"]["Pad"], {"old": "Pad", "new": "Base plate"})
            self.assertEqual(doc.getObject("Pad").Name, "Pad")
            self.assertEqual(refactor.relabel(doc, {"Nope": "x"})["skipped"], {"Nope": "no such object"})


class TestLift(unittest.TestCase):
    def test_lift_into_varset_is_invariant_and_live(self):
        with Doc() as doc:
            rb.plate_with_slot(doc)
            r = refactor.invariant(doc, lambda d: refactor.lift_all(d, target="VarSet"))
            self.assertTrue(r["ok"], r["violations"])
            lifted = {(x["object"], x["property"]): x for x in r["result"]["lifted"]}
            self.assertIn(("Pad", "Length"), lifted)
            self.assertIn(("Base", "Width"), lifted)
            self.assertIn(("Length", "Parameters.Pad_Length"), doc.getObject("Pad").ExpressionEngine)
            vs = doc.getObject("Parameters")
            self.assertEqual(vs.TypeId, "App::VarSet")
            # the binding is live: edit the VarSet and the body follows
            before = doc.getObject("Body").Shape.Volume
            vs.Pad_Length = 20
            doc.recompute()
            self.assertAlmostEqual(doc.getObject("Body").Shape.Volume - before, 40 * 20 * 10, places=6)

    def test_lift_into_spreadsheet_and_constraints(self):
        with Doc() as doc:
            rb.plate_with_slot(doc)
            r = refactor.invariant(doc, lambda d: refactor.lift_all(d, target="Spreadsheet"))
            self.assertTrue(r["ok"], r["violations"])
            sheet = doc.getObject("Parameters")
            self.assertEqual(sheet.TypeId, "Spreadsheet::Sheet")
            sk = doc.getObject("Base")
            self.assertIn(("Constraints.Width", "Parameters.Base_Width"), [(p.lstrip("."), e) for p, e in sk.ExpressionEngine])
            alias = sheet.getAlias("A1")
            self.assertTrue(alias)

    def test_lift_skips_already_bound_and_foreign_families(self):
        with Doc() as doc:
            rb.plate_with_slot(doc)
            refactor.lift_all(doc)
            self.assertEqual(refactor.lift_all(doc)["count"], 0)
            self.assertFalse([c for c in refactor.lift_candidates(doc) if c["object"] in ("Parameters", "Body")])

    def test_alias_probe_survives_digit_names(self):
        # "1 F6Noop_Length" in FreeCAD's unit parser used to kill the process; the alias probe must not call it.
        with Doc() as doc:
            body, sk, pad, sk2, pk = rb.plate_with_slot(doc)
            lp = rb.add_noop_pattern(body, pk, sk, name="F6Noop")
            for target in ("VarSet", "Spreadsheet"):
                rec = refactor.lift_literal(doc, lp, "Length", target=target, container=f"P{target}")
                self.assertEqual(rec["value"], 10.0)
            self.assertTrue(lp.ExpressionEngine)

    def test_lift_exact_value_guard(self):
        with Doc() as doc:
            body, sk, pad, *_ = rb.plate_with_slot(doc)
            pad.Length = 12.345678901234567
            rec = refactor.lift_literal(doc, pad, "Length")
            self.assertEqual(rec["value"], 12.345678901234567)
            self.assertEqual(doc.getObject("Parameters").Pad_Length.Value, 12.345678901234567)


class _Proxy:
    def __init__(self, obj):
        obj.Proxy = self

    def execute(self, obj):
        pass


class TestPythonProxyGuard(unittest.TestCase):
    """Corpus finding: touching an Arch section plane's inputs changed its geometry headless (SAFETY L6, B3)."""

    def test_lift_and_relabel_skip_python_objects_and_their_inputs(self):
        with Doc() as doc:
            box = doc.addObject("Part::Box", "Upstream")
            free = doc.addObject("Part::Box", "Free")
            py = doc.addObject("App::FeaturePython", "ProxyObj")
            _Proxy(py)
            py.addProperty("App::PropertyLink", "Source", "Base")
            py.Source = box
            doc.recompute()
            names = {c["object"] for c in refactor.lift_candidates(doc)}
            self.assertIn("Free", names)
            self.assertNotIn("Upstream", names)
            self.assertNotIn("ProxyObj", names)
            scheme = refactor.relabel_scheme(doc)
            self.assertIn("Free", scheme)
            self.assertNotIn("ProxyObj", scheme)


class TestAcrossBodies(unittest.TestCase):
    def test_standardise_every_body(self):
        with Doc() as doc:
            rb.two_bodies(doc)
            r = refactor.invariant(doc, lambda d: refactor.across_all_bodies(d))
            self.assertTrue(r["ok"], r["violations"])
            self.assertEqual(r["result"]["bodies"], ["BodyA", "BodyB"])
            self.assertEqual(doc.getObject("BPad").Label, "BodyB_BPad")
            self.assertTrue(doc.getObject("BPad").ExpressionEngine)

    def test_payload_failure_rolls_back(self):
        with Doc() as doc:
            rb.two_bodies(doc)

            def payload(body):
                if body.Name == "BodyB":
                    raise RuntimeError("no")
                refactor.relabel(body.Document, {body.Group[-1].Name: "X"})

            r = refactor.invariant(doc, lambda d: refactor.apply_across_bodies(d, payload))
            self.assertEqual(r["status"], "error")
            self.assertTrue(r["rolled_back"])


class TestLiftMismatchCleanup(unittest.TestCase):
    def test_mismatch_leaves_nothing_behind(self):
        real = refactor.ops._read

        def wrong_unit(obj, prop, kind):
            v, unit, tid = real(obj, prop, kind)
            return v, "m", tid          # stored in metres, so the read-back differs from the literal

        for target in ("VarSet", "Spreadsheet"):
            with Doc() as doc:
                body, sk, pad, *_ = rb.plate_with_slot(doc)
                cont = refactor.ops._container(doc, target, None)
                props_before = list(cont.PropertiesList)
                cells_before = list(cont.getUsedCells()) if target == "Spreadsheet" else None
                refactor.ops._read = wrong_unit
                try:
                    with self.assertRaises(ValueError):
                        refactor.lift_literal(doc, pad, "Length", target=target)
                finally:
                    refactor.ops._read = real
                self.assertFalse(pad.ExpressionEngine)
                self.assertEqual(list(cont.PropertiesList), props_before)
                if target == "Spreadsheet":
                    self.assertEqual(list(cont.getUsedCells()), cells_before)
                    self.assertEqual(cont.getAlias("A1") if hasattr(cont, "getAlias") else None, None)


class TestDeviationCoverage(unittest.TestCase):
    def _boxes(self, doc, n):
        for i in range(n):
            b = doc.addObject("Part::Box", f"B{i:03d}")
            b.Placement = FreeCAD.Placement(FreeCAD.Vector(30 * i, 0, 0), FreeCAD.Rotation())
        doc.recompute()

    def test_body_past_the_old_cap_of_40_is_covered(self):
        with Doc() as doc:
            self._boxes(doc, 45)

            def nudge(d):
                o = d.getObject("B044")
                o.Placement = FreeCAD.Placement(o.Placement.Base + FreeCAD.Vector(1e-3, 0, 0), o.Placement.Rotation)   # I3's coordinate term allows 4e-4 at x=1300

            r = refactor.invariant(doc, nudge)
            self.assertEqual(r["before"]["deviation_covered"], 45)
            self.assertFalse(r["before"]["deviation_truncated"])
            self.assertFalse(r["ok"])
            self.assertTrue(any(v["field"] in ("deviation", "bbox", "com") or "dev" in str(v) for v in r["violations"]), r["violations"])

    def test_truncation_is_reported_not_silent(self):
        with Doc() as doc:
            self._boxes(doc, 5)
            r = refactor.invariant(doc, lambda d: None, max_deviation_bodies=2)
            self.assertTrue(r["before"]["deviation_truncated"])
            self.assertEqual(r["before"]["deviation_covered"], 2)


class TestMerge(unittest.TestCase):
    def test_noop_pattern_is_merged_invariantly(self):
        with Doc() as doc:
            body, sk, pad, *_ = rb.plate_with_slot(doc)
            vol = body.Shape.Volume
            rb.add_noop_pattern(body, doc.getObject("Pocket"), sk)
            found = refactor.find_redundant(doc)
            self.assertEqual([c["object"] for c in found], ["NoopPattern"])
            r = refactor.invariant(doc, lambda d: refactor.merge_redundant(d))
            self.assertTrue(r["ok"], r["violations"])
            self.assertEqual(r["result"]["removed"], ["NoopPattern"])
            self.assertIsNone(doc.getObject("NoopPattern"))
            self.assertEqual(body.Tip.Name, "Pocket")
            self.assertAlmostEqual(body.Shape.Volume, vol, places=6)

    def test_duplicate_pocket_is_not_merged_by_r8(self):
        # Equal to its base today, but a different depth would cut deeper: parameter-sensitive, so R8 keeps it.
        with Doc() as doc:
            body, *_ = rb.plate_with_slot(doc)
            rb.add_duplicate_pocket(body)
            self.assertEqual(refactor.find_redundant(doc), [])
            self.assertEqual(refactor.merge_redundant(doc)["removed"], [])
            self.assertIsNotNone(doc.getObject("PocketAgain"))

    def test_air_pocket_is_not_merged(self):
        # A pocket cutting only air at depth 3 that would cut the plate at depth 30 (the R7 risk the verifier probed).
        with Doc() as doc:
            body, sk, pad, sk2, pk = rb.plate_with_slot(doc)
            air = rb.rect_sketch(body, 4.0, 4.0, "AirSketch", offset=13.0)   # 3 mm above the plate top
            a = rb.pocket(body, air, 3.0, "AirPocket")
            doc.recompute()
            self.assertEqual(refactor.find_redundant(doc), [])
            r = refactor.invariant(doc, lambda d: refactor.merge_redundant(d))
            self.assertTrue(r["ok"])
            self.assertEqual(r["result"]["removed"], [])
            self.assertIsNotNone(doc.getObject("AirPocket"))

    def _air_through_all(self, doc, mode):
        body, sk, pad, sk2, pk = rb.plate_with_slot(doc)
        air = rb.rect_sketch(body, 4.0, 4.0, "AirSketch", offset=13.0)   # 3 mm above the plate top
        a = rb.pocket(body, air, 3.0, "AirPocket")
        a.Type = mode
        a.Reversed = True                                              # cuts upward, into air, in any mode
        doc.recompute()
        return body, a

    def test_open_ended_modes_are_never_merged(self):
        # R9: ThroughAll and UpToFirst pockets (UpToFace is the upstream PocketTest below) that cut only air today are parameter- or
        # context-sensitive (a later edit to the plate or a sketch move makes them cut), so they stay.
        for mode in ("ThroughAll", "UpToFirst"):
            with Doc() as doc:
                body, a = self._air_through_all(doc, mode)
                if mode == "ThroughAll":
                    self.assertTrue(shapes_equal(a, a.BaseFeature), mode)   # the premise: a geometric no-op today
                self.assertEqual(refactor.find_redundant(doc), [], mode)
                self.assertEqual(refactor.merge_redundant(doc)["removed"], [], mode)
                self.assertIsNotNone(doc.getObject("AirPocket"), mode)

    def test_zero_size_length_pocket_is_not_merged(self):
        # R8 used to pass any feature with no non-zero size property without probing anything.
        with Doc() as doc:
            body, a = self._air_through_all(doc, "Length")
            a.Length = 0.0
            doc.recompute()
            self.assertEqual(refactor.find_redundant(doc), [])

    def test_allow_list_and_removal_check_gate_the_merge(self):
        from AgentCore.refactor import ops
        with Doc() as doc:
            body, sk, pad, sk2, pk = rb.plate_with_slot(doc)
            self.assertTrue(ops.mode_allowed(pk))           # a Length-mode pocket is on the list
            lp = rb.add_noop_pattern(body, pk, sk)
            self.assertTrue(ops.mode_allowed(lp))
            lp.Occurrences = 2
            self.assertFalse(ops.mode_allowed(lp))          # a real pattern is not a no-op candidate
            pad.Type = "UpToLast"
            self.assertFalse(ops.mode_allowed(pad))

    def test_upstream_pocket_test_document_keeps_pocket001(self):
        # The verifier's finding: data/tests/PocketTest.fcstd from upstream FreeCAD lost Pocket001 (an UpToFace pocket).
        path = os.path.expanduser("~/src/cad-fork/freecad/data/tests/PocketTest.fcstd")
        if not os.path.exists(path):
            if os.environ.get("CADFORK_ALLOW_MISSING_CORPUS") == "1":
                self.skipTest("upstream PocketTest.fcstd not found")
            self.fail("upstream PocketTest.fcstd not found: " + path)
        doc = FreeCAD.openDocument(path)
        try:
            doc.recompute(None, True)
            self.assertIsNotNone(doc.getObject("Pocket001"))
            self.assertEqual(refactor.find_redundant(doc), [])
            self.assertEqual(refactor.merge_redundant(doc)["removed"], [])
            self.assertIsNotNone(doc.getObject("Pocket001"))
        finally:
            FreeCAD.closeDocument(doc.Name)

    def test_real_features_are_never_merged(self):
        with Doc() as doc:
            rb.plate_with_slot(doc)
            self.assertEqual(refactor.find_redundant(doc), [])
            self.assertEqual(refactor.merge_redundant(doc)["removed"], [])

    def test_safety_rules_block_merge(self):
        # R4 suppressed, R5 own expression, R6 referenced by an expression, R3 other consumer
        def build():
            doc = FreeCAD.newDocument("F6safety")
            body, sk, pad, sk2, pk = rb.plate_with_slot(doc)
            rb.add_noop_pattern(body, pk, sk)
            return doc, body, sk

        doc, body, sk = build()
        try:
            self.assertEqual(len(refactor.find_redundant(doc)), 1)
            doc.getObject("NoopPattern").Suppressed = True
            doc.recompute()
            self.assertEqual(refactor.find_redundant(doc), [])
        finally:
            FreeCAD.closeDocument(doc.Name)
        doc, body, sk = build()
        try:
            doc.getObject("NoopPattern").setExpression("Length", "10 mm")
            self.assertEqual(refactor.find_redundant(doc), [])
        finally:
            FreeCAD.closeDocument(doc.Name)
        doc, body, sk = build()
        try:
            v = doc.addObject("App::VarSet", "Watcher")
            v.addProperty("App::PropertyLength", "X", "Base")
            v.setExpression("X", "NoopPattern.Length")
            doc.recompute()
            self.assertEqual(refactor.find_redundant(doc), [])
        finally:
            FreeCAD.closeDocument(doc.Name)
        doc, body, sk = build()
        try:
            other = body.newObject("Sketcher::SketchObject", "AttachedToPattern")
            other.AttachmentSupport = (doc.getObject("NoopPattern"), ["Face1"])
            other.MapMode = "FlatFace"
            doc.recompute()
            self.assertEqual(refactor.find_redundant(doc), [])
        finally:
            FreeCAD.closeDocument(doc.Name)

    def test_unsafe_removal_is_caught_by_the_oracle(self):
        with Doc() as doc:
            rb.plate_with_slot(doc)

            def remove_real(d):
                b = d.getObject("Body")
                b.removeObject(d.getObject("Pocket"))
                d.removeObject("Pocket")
                return {"removed": ["Pocket"]}

            r = refactor.invariant(doc, remove_real)
            self.assertFalse(r["ok"])
            self.assertTrue(r["restored_identical"])
            self.assertIsNotNone(doc.getObject("Pocket"))


if __name__ == "__main__":
    unittest.main()
