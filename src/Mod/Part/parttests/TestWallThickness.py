# SPDX-License-Identifier: LGPL-2.1-or-later

import unittest

import FreeCAD as App
import Part

V = App.Vector
TOL = 1e-6


def solid_with_cavity():
    outer = Part.makeBox(40, 40, 40)
    inner = Part.makeBox(36, 36, 36, V(2, 2, 2))
    return outer.cut(inner)


class TestWallThickness(unittest.TestCase):
    """Part.Shape.minWallThickness against analytic walls (C++ kernel, Part/App/WallThickness.cpp)."""

    def measure(self, shape, **kw):
        r = shape.minWallThickness(**kw)
        self.assertEqual(r["kernel"], "part-cpp/1")
        return r

    def check(self, shape, truth, **kw):
        r = self.measure(shape, **kw)
        self.assertTrue(r["complete"])
        self.assertIsNotNone(r["min"])
        self.assertAlmostEqual(r["min"], truth, delta=TOL)
        self.assertGreater(r["rays"], 0)
        self.assertTrue(1 <= r["face"] <= len(shape.Faces))
        return r

    def test_plate(self):
        self.check(Part.makeBox(100, 60, 2), 2.0)

    def test_tube(self):
        outer = Part.makeCylinder(10, 30)
        inner = Part.makeCylinder(8.5, 30)
        self.check(outer.cut(inner), 1.5)

    def test_sphere_shell(self):
        self.check(Part.makeSphere(10).cut(Part.makeSphere(8)), 2.0)

    def test_box_with_cavity(self):
        # Inner void: its shell is reversed, so inward must still mean into the material.
        s = solid_with_cavity()
        self.assertEqual(len(s.Shells), 2)
        self.check(s, 2.0)

    def test_solid_box_is_shortest_side(self):
        self.check(Part.makeBox(10, 20, 30), 10.0)

    def test_l_bracket(self):
        leg_a = Part.makeBox(50, 20, 3)
        leg_b = Part.makeBox(4, 20, 40)
        bracket = leg_a.fuse(leg_b).removeSplitter()
        self.check(bracket, 3.0)

    def test_compound_of_two_plates(self):
        comp = Part.makeCompound([Part.makeBox(50, 50, 4), Part.makeBox(50, 50, 1.5, V(100, 0, 0))])
        r = self.check(comp, 1.5)
        self.assertEqual(len(comp.Solids), 2)
        self.assertEqual(r["face"] in range(1, len(comp.Faces) + 1), True)

    def test_filleted_box_bounded_by_flat_walls(self):
        box = Part.makeBox(30, 20, 6)
        filleted = box.makeFillet(1.0, box.Edges)
        r = self.measure(filleted)
        self.assertIsNotNone(r["min"])
        self.assertLessEqual(r["min"], 6.0 + TOL)
        self.assertGreater(r["min"], 5.9)

    def test_non_solid_input(self):
        box = Part.makeBox(10, 10, 10)
        for shp in (Part.Shell(box.Faces), box.Faces[0], box.Wires[0], box.Edges[0], Part.Vertex(1, 2, 3)):
            r = self.measure(shp)
            self.assertIsNone(r["min"])
            self.assertIsNone(r["point"])
            self.assertIsNone(r["face"])
            self.assertEqual(r["rays"], 0)

    def test_degenerate_faces(self):
        # Cone apex and sphere poles are degenerate points in UV space.
        cone = Part.makeCone(10, 0, 20)
        r = self.measure(cone)
        self.assertTrue(r["complete"])
        sph = self.check(Part.makeSphere(5), 10.0)
        self.assertGreater(sph["rays"], 0)

    def test_null_shape_raises(self):
        with self.assertRaises(Exception):
            Part.Shape().minWallThickness()

    def test_time_budget(self):
        r = self.measure(solid_with_cavity(), samples=9, timeBudget=0.0)
        self.assertFalse(r["complete"])

    def test_point_lies_on_reported_face(self):
        s = solid_with_cavity()
        r = self.measure(s)
        p = V(*r["point"])
        face = s.Faces[r["face"] - 1]
        self.assertLess(face.distToShape(Part.Vertex(p))[0], 1e-6)

    def test_thin_walls_relative_to_diagonal(self):
        # Walls thinner than 1e-5 of the diagonal must still be seen.
        self.check(Part.makeBox(1e6, 1e6, 10), 10.0)
        self.check(Part.makeBox(1000, 1000, 0.01), 0.01)
        self.check(Part.makeBox(1000, 1000, 0.1), 0.1)

    def test_time_budget_semantics(self):
        s = solid_with_cavity()
        for unlimited in (None, -1.0, float("nan")):
            self.assertTrue(self.measure(s, timeBudget=unlimited)["complete"])
        self.assertFalse(self.measure(s, timeBudget=0.0)["complete"])

    def test_budget_enforced_while_sampling_and_samples_capped(self):
        sph = Part.makeSphere(10).cut(Part.makeSphere(8, V(1, 0, 0)))
        r = self.measure(sph, samples=5000, timeBudget=0.3)
        self.assertFalse(r["complete"])
        self.assertLess(r["elapsed_s"], 0.3 + 0.5)
        # huge sample counts are clamped rather than exhausting memory
        r = self.measure(Part.makeBox(1, 1, 1), samples=2**30, timeBudget=1.0)
        self.assertIn("rays", r)
