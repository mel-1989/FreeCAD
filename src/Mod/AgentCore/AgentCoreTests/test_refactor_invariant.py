# SPDX-License-Identifier: LGPL-2.1-or-later
"""F6 invariance checker: no-op passes, every seeded mutant is caught (mutation score 100%), rollback restores."""

import unittest

import FreeCAD

from AgentCore import refactor
from AgentCore.refactor import geomhash
from AgentCoreTests.fixtures.refactor import refactor_builders as rb


class Doc:
    def __enter__(self):
        self.doc = FreeCAD.newDocument("F6inv")
        return self.doc

    def __exit__(self, *exc):
        FreeCAD.closeDocument(self.doc.Name)


class TestInvariantBasics(unittest.TestCase):
    def test_noop_passes_and_hash_is_stable(self):
        with Doc() as doc:
            rb.plate_with_slot(doc)
            r = refactor.invariant(doc, lambda d: None)
            self.assertTrue(r["ok"], r["violations"])
            self.assertEqual(r["before"]["hash"], r["after"]["hash"])
            self.assertEqual(r["status"], "pass")
            self.assertEqual(r["max_deviation"], 0.0)
            again = geomhash.snapshot(doc).hash()
            self.assertEqual(again, r["before"]["hash"])

    def test_snapshot_reports_metrics(self):
        with Doc() as doc:
            rb.plate_with_slot(doc, w=40, h=20, t=10, slot=6, slot_depth=3)
            snap = geomhash.snapshot(doc)
            m = snap.objects["Body"]["shape"]
            self.assertAlmostEqual(m["volume"], 40 * 20 * 10 - 6 * 6 * 3, places=6)
            self.assertEqual(snap.bodies, ["Body"])
            self.assertIn("Body", snap.shapes)

    def test_raising_fn_fails_and_rolls_back(self):
        with Doc() as doc:
            body, sk, pad, *_ = rb.plate_with_slot(doc)

            def boom(d):
                d.getObject("Pad").Length = 99
                raise RuntimeError("boom")

            r = refactor.invariant(doc, boom)
            self.assertFalse(r["ok"])
            self.assertEqual(r["status"], "error")
            self.assertIn("boom", r["error"])
            self.assertTrue(r["rolled_back"])
            self.assertTrue(r["restored_identical"])
            self.assertEqual(doc.getObject("Pad").Length.Value, 10.0)


class TestMutationScore(unittest.TestCase):
    """Each seeded mutant changes geometry in a different way; the checker must fail every one."""

    def mutants(self):
        def pad_len(d): d.getObject("Pad").Length = 11
        def sketch_w(d): d.getObject("Base").setDatum("Width", FreeCAD.Units.Quantity("41 mm"))
        def pocket_depth(d): d.getObject("Pocket").Length = 4
        def remove_pocket(d):
            b = d.getObject("Body"); f = d.getObject("Pocket"); b.removeObject(f); d.removeObject("Pocket")
        def move_body(d):
            d.getObject("Body").Placement = FreeCAD.Placement(FreeCAD.Vector(0.001, 0, 0), FreeCAD.Rotation())
        def hole_added(d):
            b = d.getObject("Body")
            sk = rb.rect_sketch(b, 2, 2, "ExtraSketch", offset=10)
            rb.pocket(b, sk, 1, "ExtraPocket")
        def delete_body(d): d.removeObject("Body")
        def reversed_pad(d): d.getObject("Pad").Reversed = True
        return {"pad_len": pad_len, "sketch_w": sketch_w, "pocket_depth": pocket_depth, "remove_pocket": remove_pocket,
                "move_body": move_body, "hole_added": hole_added, "delete_body": delete_body, "reversed_pad": reversed_pad}

    def test_every_mutant_is_caught(self):
        caught = {}
        for name, fn in self.mutants().items():
            with Doc() as doc:
                rb.plate_with_slot(doc)
                r = refactor.invariant(doc, fn)
                caught[name] = not r["ok"]
                if not r["ok"]:
                    self.assertTrue(r["rolled_back"], name)
                    self.assertTrue(r["restored_identical"], name)
        self.assertTrue(all(caught.values()), caught)
        self.assertEqual(len(caught), 8)

    def test_body_may_not_vanish_even_if_declared(self):
        with Doc() as doc:
            rb.plate_with_slot(doc)
            r = refactor.invariant(doc, lambda d: (d.removeObject("Body"), {"removed": ["Body"]})[1])
            self.assertFalse(r["ok"])
            self.assertIn("body_missing", [v["field"] for v in r["violations"]])

    def test_new_shape_object_must_be_declared(self):
        with Doc() as doc:
            rb.plate_with_slot(doc)
            r = refactor.invariant(doc, lambda d: d.addObject("Part::Box", "Extra"))
            self.assertIn("added", [v["field"] for v in r["violations"]])
            with Doc() as doc2:
                rb.plate_with_slot(doc2)
                r2 = refactor.invariant(doc2, lambda d: (d.addObject("Part::Box", "Extra"), {"added": ["Extra"]})[1])
                self.assertTrue(r2["ok"], r2["violations"])

    def test_deviation_catches_a_shape_move_metrics_miss(self):
        """A shape displaced by less than metric tolerance but more than the deviation limit fails on deviation."""
        with Doc() as doc:
            rb.plate_with_slot(doc)
            r = refactor.invariant(doc, lambda d: setattr(d.getObject("Body"), "Placement",
                                                          FreeCAD.Placement(FreeCAD.Vector(0, 5e-5, 0), FreeCAD.Rotation())))
            self.assertFalse(r["ok"])
            fields = {v["field"] for v in r["violations"]}
            self.assertIn("deviation", fields)


if __name__ == "__main__":
    unittest.main()
