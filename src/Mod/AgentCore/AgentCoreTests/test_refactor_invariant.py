# SPDX-License-Identifier: LGPL-2.1-or-later
"""F6 invariance checker: no-op passes, every seeded mutant is caught (mutation score 100%), rollback restores."""

import json
import os
import unittest

import FreeCAD
import Part

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


class TestTolerancePolicy(unittest.TestCase):
    """I3's rule (harness/golden/compare.py): relative to the quantity, not to the bbox diagonal cubed."""

    def violations(self, a, b):
        return geomhash.compare_metrics("x", geomhash.shape_metrics(a), geomhash.shape_metrics(b), geomhash.load_tolerances())

    def test_thin_plate_height_change_is_caught(self):
        # 100 x 100 x 0.5 against 0.50005 high: a 1e-4 relative volume change that the old diag**3 scale (about 11 mm3) hid.
        v = self.violations(Part.makeBox(100, 100, 0.5), Part.makeBox(100, 100, 0.50005))
        self.assertIn("volume", [x["field"] for x in v], v)

    def test_noise_inside_policy_passes(self):
        # 1e-7 relative is far inside rel_tol 4e-6
        self.assertEqual(self.violations(Part.makeBox(100, 100, 0.5), Part.makeBox(100, 100, 0.5 * (1 + 1e-7))), [])

    def test_invariant_flags_thin_plate_edit_end_to_end(self):
        with Doc() as doc:
            box = doc.addObject("Part::Box", "Plate")
            box.Length, box.Width, box.Height = 100, 100, 0.5
            doc.recompute()
            r = refactor.invariant(doc, lambda d: setattr(d.getObject("Plate"), "Height", 0.50005))
            self.assertFalse(r["ok"])
            self.assertTrue(any(v["field"] == "volume" for v in r["violations"]), r["violations"])

    def test_vendored_policy_matches_control_repo(self):
        ctl = os.path.expanduser(os.environ.get("CADFORK_CTL", "~/_agents/repos/app development/cad-fork"))
        path = os.path.join(ctl, "fixtures", "golden", "tolerances.json")
        if not os.path.exists(path):
            if os.environ.get("CADFORK_ALLOW_MISSING_CORPUS") == "1":
                self.skipTest("control repo tolerances not found")
            self.fail("control repo tolerances not found: " + path)
        ctl_pol = json.load(open(path))["platforms"]
        for cls, block in ctl_pol.items():
            mine = geomhash.load_tolerances(cls)
            self.assertEqual(mine, {k: v for k, v in block.items() if k != "calibrated"}, cls)
