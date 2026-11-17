# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with this program; if not, write to the Free Software   *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# *                                                                         *
# ***************************************************************************

"""Tests for SurfaceAnalysis: analytic cases with closed-form truth."""

import math
import random
import unittest

import FreeCAD
import Part

import SurfaceAnalysis as SA

V = FreeCAD.Vector


def _lateral(shape, name):
    return next(f for f in shape.Faces if f.Surface.TypeId == name)


def _reversed(face):
    r = face.copy()
    r.reverse()
    return r


def _moved(shape):
    m = shape.copy()
    m.rotate(V(1, 2, -1), V(0.3, 1.0, 2.0), 37.0)
    m.translate(V(3.0, -4.0, 7.5))
    return m


def _close(testcase, a, b, rel=1e-6, ab=1e-9, msg=""):
    testcase.assertLessEqual(abs(a - b), max(ab, rel * abs(b)), f"{msg}: {a} vs {b}")


def _samples(face, count=50, seed=1):
    rng = random.Random(seed)
    u0, u1, v0, v1 = face.ParameterRange
    return [
        (u0 + (0.05 + 0.9 * rng.random()) * (u1 - u0),
         v0 + (0.05 + 0.9 * rng.random()) * (v1 - v0))
        for _ in range(count)
    ]


def _truth_sphere(R):
    return lambda p: (1 / R**2, 1 / R, 1 / R, 1 / R)


def _truth_cylinder(R):
    return lambda p: (0.0, 0.5 / R, 1 / R, 0.0)


def _truth_cone(R1, R2, h):
    cos_a = h / math.hypot(R1 - R2, h)

    def truth(p):
        k = cos_a / math.hypot(p.x, p.y)
        return (0.0, k / 2, k, 0.0)

    return truth


def _truth_torus(R, r):
    def truth(p):
        rho = math.hypot(p.x, p.y)
        k1, k2 = 1 / r, (rho - R) / (r * rho)
        return (k1 * k2, (k1 + k2) / 2, max(k1, k2), min(k1, k2))

    return truth


def _split_cylinder():
    a = Part.makeCylinder(5, 10, V(), V(0, 0, 1), 180)
    b = a.copy()
    b.rotate(V(), V(0, 0, 1), 180)
    fa = _lateral(a, "Part::GeomCylinder")
    fb = _lateral(b, "Part::GeomCylinder")
    return _sewn([fa, fb])


def _split_sphere():
    a = Part.makeSphere(10, V(), V(0, 0, 1), -90, 90, 180)
    b = a.copy()
    b.rotate(V(), V(0, 0, 1), 180)
    return _sewn([a.Faces[0], b.Faces[0]])


def _sewn(faces):
    sew = Part.Compound(faces)
    shell = Part.Shell(faces)
    shell.sewShape()
    return shell if shell.isValid() else sew


def _shared_edges(shape):
    out = []
    for e in shape.Edges:
        if len(shape.ancestorsOfType(e, Part.Face)) == 2 and e.Length > 1e-3:
            out.append(e)
    return out


def _cubic_join():
    bez = Part.BezierCurve()
    bez.setPoles([V(-1, -1, 0), V(-2 / 3, 0, 0), V(-1 / 3, 0, 0), V(0, 0, 0)])
    fa = bez.toShape().extrude(V(0, 0, 5))
    pts = [V(0, 0, 0), V(1, 0, 0), V(1, 0, 5), V(0, 0, 5), V(0, 0, 0)]
    fb = Part.Face(Part.makePolygon(pts))
    return _sewn([fa.Faces[0], fb])


def _kink(deg):
    a = math.radians(deg)
    p0, p1 = V(0, 0, 0), V(0, 5, 0)
    fa = Part.Face(Part.makePolygon([p0, p1, p1 + V(-4, 0, 0), p0 + V(-4, 0, 0), p0]))
    q = V(4 * math.cos(a), 0, 4 * math.sin(a))
    fb = Part.Face(Part.makePolygon([p0, p0 + q, p1 + q, p1, p0]))
    return _sewn([fa, fb])


class TestSurfaceAnalysis(unittest.TestCase):
    def setUp(self):
        self.sphere = Part.makeSphere(10).Faces[0]
        self.cyl = _lateral(Part.makeCylinder(5, 10), "Part::GeomCylinder")
        self.cone = _lateral(Part.makeCone(10, 4, 12), "Part::GeomCone")
        self.torus = Part.makeTorus(20, 5).Faces[0]
        self.plane = Part.makePlane(10, 10).Faces[0]

    def _check_exact(self, face, truth, count=50):
        for u, v in _samples(face, count):
            c = SA.curvature(face, u, v)
            g, m, kx, kn = truth(c["point"])
            for name, want in (("gauss", g), ("mean", m), ("kmax", kx), ("kmin", kn)):
                _close(self, c[name], want, msg=name)
            self.assertGreaterEqual(c["kmax"], c["kmin"])

    def test_sphere_cylinder_cone_torus_plane(self):
        self._check_exact(self.sphere, _truth_sphere(10))
        self._check_exact(self.cyl, _truth_cylinder(5))
        self._check_exact(self.cone, _truth_cone(10, 4, 12))
        self._check_exact(self.torus, _truth_torus(20, 5))
        self._check_exact(self.plane, lambda p: (0.0, 0.0, 0.0, 0.0))

    def test_rational_bspline_sphere(self):
        face = Part.makeSphere(10).Faces[0].toNurbs().Faces[0]
        self._check_exact(face, _truth_sphere(10))

    def test_interpolated_bspline(self):
        sph = self.sphere.Surface
        n, m = 13, 9
        pts = []
        for i in range(n):
            row = []
            for j in range(m):
                u = 0.3 + 1.0 * i / (n - 1)
                v = -0.6 + 1.2 * j / (m - 1)
                row.append(sph.value(u, v))
            pts.append(row)
        bs = Part.BSplineSurface()
        bs.interpolate(pts)
        face = bs.toShape()
        for u, v in _samples(face, 50, seed=3):
            c = SA.curvature(face, u, v)
            for name, want in (("gauss", 0.01), ("mean", 0.1), ("kmax", 0.1)):
                self.assertLess(abs(c[name] - want) / want, 0.01, name)

    def test_principal_directions(self):
        for u, v in _samples(self.cyl, 20):
            c = SA.curvature(self.cyl, u, v)
            self.assertLess(abs(c["dmax"].Length - 1.0), 1e-9)
            axis_dir = c["dmin"]
            self.assertLess(abs(abs(axis_dir.z) - 1.0), 1e-4)  # kmin along the ruling
            circ = c["dmax"]
            self.assertLess(abs(circ.dot(c["normal"])), 1e-9)
            self.assertLess(abs(circ.z), 1e-4)

    def test_sign_convention_and_reversal(self):
        # convex faces: positive mean; concave hole wall: negative
        for face in (self.sphere, self.cyl, self.cone):
            u, v = _samples(face, 1)[0]
            self.assertGreater(SA.curvature(face, u, v)["mean"], 0)
        box = Part.makeBox(30, 30, 30).cut(Part.makeCylinder(5, 30, V(15, 15, 0)))
        hole = _lateral(box, "Part::GeomCylinder")
        for u, v in _samples(hole, 10):
            c = SA.curvature(hole, u, v)
            _close(self, c["mean"], -0.1, msg="hole mean")
            _close(self, c["kmin"], -0.2, msg="hole kmin")
            self.assertEqual(c["kmax"], 0.0)
            self.assertLess(c["normal"].dot(V(c["point"].x - 15, c["point"].y - 15, 0)), 0)
        # torus inner half has negative Gauss; reversed copies flip mean, keep gauss
        neg = 0
        for u, v in _samples(self.torus, 50):
            c = SA.curvature(self.torus, u, v)
            r = SA.curvature(_reversed(self.torus), u, v)
            neg += c["gauss"] < 0
            _close(self, r["gauss"], c["gauss"], msg="gauss")
            _close(self, r["mean"], -c["mean"], msg="mean")
            _close(self, r["kmax"], -c["kmin"], msg="kmax")
            _close(self, r["kmin"], -c["kmax"], msg="kmin")
            self.assertGreaterEqual(r["kmax"], r["kmin"])
            self.assertLess((r["dmax"] - c["dmin"]).Length, 1e-9)
            self.assertLess((r["normal"] + c["normal"]).Length, 1e-12)
        self.assertGreater(neg, 5)

    def test_placement(self):
        for shape, truth in (
            (Part.makeSphere(10), _truth_sphere(10)),
            (Part.makeCylinder(5, 10), _truth_cylinder(5)),
        ):
            face = next(f for f in shape.Faces if f.Surface.TypeId != "Part::GeomPlane")
            moved = _moved(shape)
            mface = next(f for f in moved.Faces if f.Surface.TypeId != "Part::GeomPlane")
            for u, v in _samples(face, 10):
                c, m = SA.curvature(face, u, v), SA.curvature(mface, u, v)
                for name in ("gauss", "mean", "kmax", "kmin"):
                    _close(self, m[name], c[name], rel=1e-9, msg=name)
                self.assertLess((m["point"] - mface.valueAt(u, v)).Length, 1e-9)
                self.assertLess((m["normal"] - mface.normalAt(u, v)).Length, 1e-9)
                self.assertLess(abs(m["normal"].Length - 1.0), 1e-9)

    def test_degenerate_points(self):
        for v in (math.pi / 2, -math.pi / 2, math.pi / 2 - 1e-9, -math.pi / 2 + 1e-9):
            c = SA.curvature(self.sphere, 1.0, v)
            for name in ("gauss", "mean", "kmax", "kmin"):
                self.assertNotEqual(c[name], c[name], name)
            self.assertIsNone(c["dmax"])
            self.assertIsNone(c["dmin"])
        apex = Part.makeCone(5, 0, 10).Faces[0]
        for v in (math.hypot(5, 10), math.hypot(5, 10) - 1e-9):
            c = SA.curvature(apex, 1.0, v)
            self.assertNotEqual(c["kmax"], c["kmax"])
            self.assertIsNone(c["dmax"])

    def test_maps_never_raise(self):
        faces = [self.sphere, self.cyl, self.cone, self.torus, self.plane,
                 Part.makeCone(5, 0, 10).Faces[0], self.sphere.toNurbs().Faces[0]]
        for face in faces:
            for n in (3, 20):
                SA.curvature_map(face, n, n)
                SA.draft_map(face, V(0, 0, 1), n, n)
        SA.curvature_map(self.sphere, 50, 50)

    def test_map_sampling_untrimmed(self):
        for face in (self.sphere, self.cyl, self.plane):
            m = SA.curvature_map(face, 7, 5)
            self.assertEqual(len(m), 35)
            u0, u1, v0, v1 = face.ParameterRange
            for s in m:
                self.assertTrue(u0 < s["u"] < u1 and v0 < s["v"] < v1)
                self.assertLess((face.valueAt(s["u"], s["v"]) - s["point"]).Length, 1e-12)

    def test_map_sampling_trimmed(self):
        disk = Part.makePlane(40, 40, V(-20, -20, 0)).cut(Part.makeCylinder(8, 5, V(0, 0, -1)))
        pf = disk.Faces[0]
        cut = Part.makeBox(30, 30, 30).cut(Part.makeCylinder(10, 30, V(15, 15, 0), V(0, 0, 1)))
        for face, hole in ((pf, lambda p: math.hypot(p.x, p.y) < 8),
                           (next(f for f in cut.Faces if f.Surface.TypeId == "Part::GeomPlane"
                                 and abs(f.CenterOfMass.z - 30) < 1e-6),
                            lambda p: math.hypot(p.x - 15, p.y - 15) < 10)):
            m = SA.curvature_map(face, 40, 40)
            self.assertGreater(len(m), 100)
            self.assertLess(len(m), 1600)
            for s in m:
                self.assertFalse(hole(s["point"]))
                self.assertLess(Part.Vertex(s["point"]).distToShape(face)[0], 1e-7)

    def test_draft_box_cone_cylinder(self):
        box = Part.makeBox(10, 10, 10)
        pull = V(0, 0, 1)
        for f in box.Faces:
            n = f.normalAt(*f.ParameterRange[0::2])
            want = 90.0 * n.z
            c = SA.draft_map(f, pull, 3, 3)
            for s in c:
                _close(self, s["angle"], want, rel=0, ab=1e-6)
                if want > 1:
                    self.assertEqual(s["cls"], "positive")
                elif want < -1:
                    self.assertEqual(s["cls"], "negative")
                else:
                    self.assertEqual(s["cls"], "insufficient")
        cone = _lateral(Part.makeCone(10, 4, 12), "Part::GeomCone")
        half = math.degrees(math.atan2(6, 12))
        for sign, face in ((1, cone), (-1, _reversed(cone))):
            for s in SA.draft_map(face, pull, 4, 4, min_draft=3.0):
                # outward normal of a cone narrowing upwards leans along +z by the half angle
                _close(self, s["angle"], sign * half, rel=0, ab=1e-6)
                self.assertEqual(s["cls"], "positive" if sign > 0 else "negative")
        for s in SA.draft_map(self.cyl, pull, 5, 5):
            self.assertLess(abs(s["angle"]), 1e-9)
            self.assertEqual(s["cls"], "insufficient")
        _close(self, SA.draft_angle(self.cyl, 1.0, 5.0, pull), 0.0, rel=0, ab=1e-9)
        with self.assertRaises(ValueError):
            SA.draft_angle(self.cyl, 1.0, 5.0, V(0, 0, 0))

    def test_deviation(self):
        patch = Part.makeSphere(10, V(), V(0, 0, 1), -30, 30, 90).Faces[0]
        big = Part.makeSphere(10.1).Faces[0]
        d = SA.deviation(patch, big, 10, 10)
        self.assertEqual(d["count"], 100)
        for key in ("min", "max", "mean", "rms"):
            _close(self, d[key], 0.1, rel=0, ab=1e-6, msg=key)
        d = SA.deviation(self.sphere, self.sphere, 8, 8)
        self.assertLess(d["max"], 1e-6)
        off = Part.makePlane(10, 10, V(0, 0, 2.5)).Faces[0]
        d = SA.deviation(self.plane, off, 6, 6)
        for key in ("min", "max", "mean", "rms"):
            _close(self, d[key], 2.5, rel=0, ab=1e-6, msg=key)
        pts = [V(0, 0, 12), V(0, 0, 11), V(10.5, 0, 0)]
        d = SA.deviation(self.sphere, pts)
        self.assertEqual(d["count"], 3)
        _close(self, d["min"], 0.5, rel=0, ab=1e-6)
        _close(self, d["max"], 2.0, rel=0, ab=1e-6)
        _close(self, d["mean"], 3.5 / 3, rel=0, ab=1e-6)
        _close(self, d["rms"], math.sqrt((4 + 1 + 0.25) / 3), rel=0, ab=1e-6)

    def test_edge_box_and_fillet(self):
        box = Part.makeBox(10, 10, 10)
        for e in box.Edges:
            r = SA.edge_continuity(box, e)
            self.assertEqual(r["level"], "G0")
            _close(self, r["angle"], 90.0, rel=0, ab=1e-6)
        fil = box.makeFillet(2, [box.Edges[0]])
        levels = {}
        for e in _shared_edges(fil):
            r = SA.edge_continuity(fil, e)
            levels[r["level"]] = levels.get(r["level"], 0) + 1
            if r["level"] == "G1":
                _close(self, r["curvature_jump"], 1.0, rel=0, ab=1e-6)
                self.assertLess(r["angle"], 1e-6)
        self.assertEqual(levels.get("G1"), 2)
        self.assertNotIn("G2", levels)

    def test_edge_split_surfaces_are_g3(self):
        for shape in (_split_cylinder(), _split_sphere()):
            edges = _shared_edges(shape)
            self.assertTrue(edges)
            for e in edges:
                r = SA.edge_continuity(shape, e)
                self.assertEqual(r["level"], "G3", r)
                self.assertLess(r["angle"], 1e-6)

    def test_edge_cubic_join_is_g2_not_g3(self):
        shape = _cubic_join()
        edges = [e for e in _shared_edges(shape) if abs(e.Length - 5) < 1e-6]
        self.assertEqual(len(edges), 1)
        r = SA.edge_continuity(shape, edges[0])
        self.assertEqual(r["level"], "G2", r)
        self.assertGreater(r["g3_jump"], 0.5)

    def test_edge_kink_and_gap(self):
        shape = _kink(1.0)
        edges = [e for e in _shared_edges(shape) if abs(e.Length - 5) < 1e-6]
        r = SA.edge_continuity(shape, edges[0])
        self.assertEqual(r["level"], "G0")
        _close(self, r["angle"], 1.0, rel=0, ab=1e-6)
        fa = Part.Face(Part.makePolygon([V(0, 0, 0), V(5, 0, 0), V(5, 5, 0), V(0, 5, 0), V(0, 0, 0)]))
        fb = Part.Face(Part.makePolygon([V(5.01, 0, 0), V(10, 0, 0), V(10, 5, 0), V(5.01, 5, 0), V(5.01, 0, 0)]))
        edge = next(e for e in fa.Edges if abs(e.CenterOfMass.x - 5) < 1e-9)
        r = SA.edge_continuity(fa, edge, faces=(fa, fb))
        self.assertEqual(r["level"], "none")
        self.assertIsNone(r["faces"])
        _close(self, r["gap"], 0.01, rel=0, ab=1e-6)
        r = SA.edge_continuity(fa, edge, faces=(fa, fb), tol={"gap": 0.02})
        self.assertIn(r["level"], ("G1", "G2", "G3"))

    def _scaled_cases(self, k):
        """Labels and measures of three joins scaled uniformly by k."""
        out = []
        box = Part.makeBox(10, 10, 10)
        box.scale(k)
        out.append(SA.edge_continuity(box, box.Edges[0]))
        raw = Part.makeBox(10, 10, 10)
        fil = raw.makeFillet(2, [raw.Edges[0]])
        fil.scale(k)
        for e in _shared_edges(fil):
            out.append(SA.edge_continuity(fil, e))
        for shape in (_split_cylinder(), _split_sphere()):
            shape.scale(k)
            for e in _shared_edges(shape):
                out.append(SA.edge_continuity(shape, e))
        return out

    def test_edge_floor_scale_invariance(self):
        base = self._scaled_cases(1.0)
        for k in (1e-2, 1e2, 1e4):
            got = self._scaled_cases(k)
            self.assertEqual(len(got), len(base))
            for a, b in zip(got, base):
                self.assertEqual(a["level"], b["level"], (k, a, b))
                _close(self, a["curvature_jump"], b["curvature_jump"], rel=1e-4, ab=1e-9)
                _close(self, a["g3_jump"], b["g3_jump"], rel=1e-4, ab=1e-9)

    def test_edge_large_fillet_reads_g1(self):
        for radius in (1e5, 1e6):
            side = 20 * radius
            box = Part.makeBox(side, side, side)
            fil = box.makeFillet(radius, [box.Edges[0]])
            g1 = [
                SA.edge_continuity(fil, e)
                for e in _shared_edges(fil)
                if SA.edge_continuity(fil, e)["angle"] < 1e-3
            ]
            self.assertEqual(len(g1), 2, radius)
            for r in g1:
                self.assertEqual(r["level"], "G1", (radius, r))
                _close(self, r["curvature_jump"], 1.0, rel=0, ab=1e-6)

    def test_edge_floor_key_and_gap_tolerance(self):
        shape = _cubic_join()
        e = next(e for e in _shared_edges(shape) if abs(e.Length - 5) < 1e-6)
        r_lo = SA.edge_continuity(shape, e, tol={"floor": 1e-6})
        r_hi = SA.edge_continuity(shape, e, tol={"floor": 1e6})
        self.assertGreater(r_lo["g3_jump"], r_hi["g3_jump"] + 0.1)
        self.assertLess(r_hi["g3_jump"], 1e-3)
        big = Part.makeBox(1e6, 1e6, 1e6)
        r = SA.edge_continuity(big, big.Edges[0])
        self.assertEqual(r["level"], "G0")

    def test_edge_errors(self):
        box = Part.makeBox(10, 10, 10)
        other = Part.makeBox(5, 5, 5, V(100, 0, 0))
        with self.assertRaises(ValueError):
            SA.edge_continuity(box, other.Edges[0])
        with self.assertRaises(ValueError):
            SA.edge_continuity(self.plane, self.plane.Edges[0])
        cyl = Part.makeCylinder(5, 10)
        seam = next(e for e in cyl.Edges if len(cyl.ancestorsOfType(e, Part.Face)) == 1
                    and abs(e.Length - 10) < 1e-9)
        try:
            r = SA.edge_continuity(cyl, seam)
            self.assertEqual(r["level"], "G3")
        except ValueError as exc:
            self.assertIn("seam", str(exc))

    def test_no_side_effects(self):
        shapes = [Part.makeBox(10, 10, 10), Part.makeSphere(10), Part.makeCylinder(5, 10)]
        before = [s.exportBrepToString() for s in shapes]
        docs = list(FreeCAD.listDocuments())
        for s in shapes:
            for f in s.Faces:
                SA.curvature_map(f, 5, 5)
                SA.draft_map(f, V(0, 0, 1), 5, 5)
                SA.deviation(f, shapes[0], 4, 4)
            for e in s.Edges:
                try:
                    SA.edge_continuity(s, e, samples=3)
                except ValueError:
                    pass
        self.assertEqual(before, [s.exportBrepToString() for s in shapes])
        self.assertEqual(docs, list(FreeCAD.listDocuments()))
