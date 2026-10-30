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


# Cone with fixTolerance(1e-2) (face tolerance 0.01), from the r015 verifier.
CONE96_BREP = """DBRep_DrawableShape

CASCADE Topology V1, (c) Matra-Datavision
Locations 0
Curve2ds 4
1 0 20.591260281974002 1 0 
1 6.2831853071795862 -0 0 1 
1 0 -0 0 1 
1 0 0 1 0 
Curves 3
2 2.9999999999999991 2.9999999999999991 10 0 0 -1 2.2204460492503136e-16 -1 -0 -1 2.2204460492503136e-16 -0 1.999999999999998
1 3.0000000000000084 -16.999999999999996 0 -4.082086890494704e-16 0.87415727612153771 0.48564293117863216 
2 2.9999999999999991 2.9999999999999991 0 0 0 -1 2.2204460492503136e-16 -1 -0 -1 2.2204460492503136e-16 -0 20
Polygon3D 0
PolygonOnTriangulations 0
Surfaces 3
3 2.9999999999999991 2.9999999999999991 0 -0 -0 1 2.2204460492503136e-16 -1 -0 -1 2.2204460492503136e-16 -0 20
-1.0636978224025597
1 2.9999999999999991 2.9999999999999991 10 -0 -0 1 2.2204460492503136e-16 -1 -0 -1 2.2204460492503136e-16 -0 
1 2.9999999999999991 2.9999999999999991 0 -0 -0 1 2.2204460492503136e-16 -1 -0 -1 2.2204460492503136e-16 -0 
Triangulations 0

TShapes 13
Ve
0.01
3 1 10
0 0

0101101
*
Ed
 0.01 1 1 0
1  1 0 0 6.28318530717959
2  1 1 0 0 6.28318530717959
0

0101000
+13 0 -13 0 *
Ve
0.01
3.00000000000001 -17 0
0 0

0101101
*
Ed
 0.01 1 1 0
1  2 0 0 20.591260281974
3  2 3CN 1 0 0 20.591260281974
0

0101000
-13 0 +11 0 *
Ed
 0.01 1 1 0
1  3 0 0 6.28318530717959
2  4 1 0 0 6.28318530717959
0

0101000
+11 0 -11 0 *
Wi

0101100
-12 0 +10 0 +9 0 -10 0 *
Fa
0  0.01 1 0

0111000
+8 0 *
Wi

0101100
+12 0 *
Fa
0  0.01 2 0

0111000
+6 0 *
Wi

0101100
-9 0 *
Fa
0  0.01 3 0

0111000
-4 0 *
Sh

0101100
-7 0 -5 0 +3 0 *
So

1100000
+2 0 *

+1 0 
"""


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

    def test_walls_thinner_than_1e7_of_diagonal(self):
        # The start face is skipped by identity, not by distance, so these walls are seen.
        self.check(Part.makeBox(1000, 1000, 0.05), 0.05)
        self.check(Part.makeBox(1e6, 1e6, 0.05), 0.05)
        self.check(Part.makeBox(1e6, 1e6, 1e-3), 1e-3)
        self.check(Part.makeBox(1000, 1000, 1e-5), 1e-5)

    def test_micron_scale_wall_on_small_box(self):
        b = Part.makeBox(10, 10, 1e-6)
        self.assertTrue(b.isValid())
        r = self.measure(b)
        self.assertIsNotNone(r["min"])
        self.assertAlmostEqual(r["min"], 1e-6, delta=1e-9)

    def test_thick_wall_on_huge_part(self):
        # A 10 mm wall on a 1 km part must read 10, not the part size.
        self.check(Part.makeBox(1000000, 1000000, 10), 10.0)
        tube = Part.makeCylinder(500000, 1000000).cut(Part.makeCylinder(499990, 1000000))
        self.check(tube, 10.0, samples=7)

    def test_curved_far_side_of_same_face_is_a_wall(self):
        # The opposite side of the same face (inner cylinder, sphere, torus) is a real wall.
        self.check(Part.makeCylinder(10, 30).cut(Part.makeCylinder(8.5, 30)), 1.5)
        self.check(Part.makeSphere(10).cut(Part.makeSphere(8)), 2.0)
        self.check(Part.makeTorus(20, 5).cut(Part.makeTorus(20, 3.5)), 1.5)
        self.check(Part.makeSphere(10), 20.0)

    def test_sample_on_face_boundary_does_not_read_as_zero(self):
        # A sample on an edge also meets the adjacent face at the ray start; that is not a wall.
        for tol in (1e-5, 1e-4, 1e-2):
            s = Part.makeCone(10, 4, 20).cut(Part.makeCone(8, 2.5, 20))
            s.fixTolerance(tol)
            # Values from the Python reference kernel (independent of the start-face logic).
            for n, ref in ((3, 0.6061), (4, 0.9773), (5, 1.485), (6, 0.9392), (7, 0.2018)):
                r = self.measure(s, samples=n)
                self.assertAlmostEqual(r["min"], ref, delta=1e-3, msg=(tol, n))
        for n in range(2, 13):
            wedge = Part.makeCone(10, 0, 20, V(0, 0, 0), V(0, 0, 1), 5)
            self.assertGreater(self.measure(wedge, samples=n)["min"], 0.03, n)
        wedge = Part.makeCone(10, 0, 20, V(0, 0, 0), V(0, 0, 1), 5)
        self.assertAlmostEqual(self.measure(wedge, samples=2)["min"], 0.2187, delta=1e-3)

    def test_sliver_near_rim_on_tolerant_cone(self):
        # Sliver between the base plane and the cone face (0.0688 thick) at a rim sample,
        # thinner than 10x the face tolerance: it is a wall, not the neighbour at the ray start.
        s = Part.Shape()
        s.importBrepFromString(CONE96_BREP)
        self.assertAlmostEqual(self.measure(s, samples=9)["min"], 0.0688, delta=1e-3)

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
