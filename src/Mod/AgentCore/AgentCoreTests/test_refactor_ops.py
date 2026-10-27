# SPDX-License-Identifier: LGPL-2.1-or-later
"""F6 refactor operations: relabel, lift into VarSet or Spreadsheet, across bodies, conservative merge."""

import unittest

import FreeCAD

from AgentCore import refactor
from AgentCoreTests.fixtures.refactor import refactor_builders as rb


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
            self.assertGreater(r["result"]["count"], 5)
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

    def test_lift_exact_value_guard(self):
        with Doc() as doc:
            body, sk, pad, *_ = rb.plate_with_slot(doc)
            pad.Length = 12.345678901234567
            rec = refactor.lift_literal(doc, pad, "Length")
            self.assertEqual(rec["value"], 12.345678901234567)
            self.assertEqual(doc.getObject("Parameters").Pad_Length.Value, 12.345678901234567)


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

    def test_duplicate_pocket_is_merged(self):
        with Doc() as doc:
            body, *_ = rb.plate_with_slot(doc)
            rb.add_duplicate_pocket(body)
            r = refactor.invariant(doc, lambda d: refactor.merge_redundant(d))
            self.assertTrue(r["ok"], r["violations"])
            self.assertEqual(r["result"]["removed"], ["PocketAgain"])

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
