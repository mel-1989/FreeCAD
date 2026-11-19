# SPDX-License-Identifier: LGPL-2.1-or-later

"""Tests of ``SurfaceMatch.matchSurface`` (the G2 match-surface fitter, G09 slice 5a).

Public sources: the second fundamental form and the normal curvature of a surface (M. P. do Carmo,
*Differential Geometry of Curves and Surfaces*, 1976, chapter 3); B-spline knot insertion and degree
elevation (L. Piegl and W. Tiller, *The NURBS Book*, 2nd ed., 1997, chapters 3 and 5); the linkage-curve
theorem (A. Pegna and F. Wolter, 1992). The truths are closed form: a plane has normal curvature 0 in
every direction, a cylinder of radius R has ``(w . e)^2 / R`` against its inward normal for a unit
tangent ``w`` and the circumferential unit vector ``e``, a sphere of radius R has ``1 / R``. The tests
evaluate them with their own formulas (surface derivatives from ``getDN``), not through ``SurfaceMatch``.
"""

import math
import os
import tempfile
import unittest

import FreeCAD
import Part
import SurfaceAnalysis as SA
import SurfaceMatch as SM
import SurfacePoleEdit as PE

V = FreeCAD.Vector


# ------------------------------------------------------------------------- constructions


def unit(v):
    return v * (1.0 / v.Length)


def pick_edge(face, p):
    return min(face.Edges, key=lambda e: e.distToShape(Part.Vertex(p))[0])


def net_surface(P, pu=3, pv=3):
    nu, nv = len(P), len(P[0])

    def knots(n, p):
        inner = n - p - 1
        k = [0.0] * (p + 1) + [(i + 1) / (inner + 1.0) for i in range(inner)] + [1.0] * (p + 1)
        ks = sorted(set(k))
        return ks, [k.count(x) for x in ks]

    uk, um = knots(nu, pu)
    vk, vm = knots(nv, pv)
    s = Part.BSplineSurface()
    s.buildFromPolesMultsKnots(P, um, vm, uk, vk, False, False, pu, pv)
    return s


def source_surface(base, step, bump_row0=False, bump=V(0, 0, 1), amp=3.0, nv=5, pitch=7.5):
    """A bicubic 6 by 5 net: row 0 is ``base`` (6 points), the rows follow ``step`` (a unit vector) by
    ``pitch`` and carry a smooth bump along ``bump``."""
    P = []
    for i, b in enumerate(base):
        col = []
        for j in range(nv):
            h = amp * math.sin(1.7 * i + 0.9 * j + 0.5) if (j > 0 or bump_row0) else 0.0
            col.append(b + step * (j * pitch) + bump * h)
        P.append(col)
    return net_surface(P)


def line_base(n=6, length=40.0):
    return [V(length * i / (n - 1.0), 0, 0) for i in range(n)]


def as_side(surface, side):
    """The V0-built ``surface`` turned so that its V0 side is ``side``: the net is transposed and, for
    the far sides, reversed. Geometry is identical, only the parametrisation changes."""
    nu, nv = surface.NbUPoles, surface.NbVPoles
    poles = surface.getPoles()
    P = [[poles[i][j] for j in range(nv)] for i in range(nu)]
    if side == "V1":
        P = [col[::-1] for col in P]
    elif side == "U0":
        P = [[P[i][j] for i in range(nu)] for j in range(nv)]
    elif side == "U1":
        P = [[P[i][nv - 1 - j] for i in range(nu)] for j in range(nv)]
    return net_surface(P)


def poly_face(pts):
    return Part.Face(Part.makePolygon(pts + [pts[0]]))


def plane_target(theta=30.0, origin=V(0, 0, 0), length=40.0, width=30.0):
    """A planar face with the edge ``origin`` to ``origin + (length, 0, 0)``, extending backwards."""
    t = V(0, -math.cos(math.radians(theta)), math.sin(math.radians(theta)))
    f = poly_face([origin, origin + V(length, 0, 0), origin + V(length, 0, 0) + t * width, origin + t * width])
    return f, pick_edge(f, origin + V(length / 2.0, 0, 0))


def cylinder_arc_case():
    """A cylinder face (radius 20, quarter turn) with its end arc as target edge and a source whose V0
    side follows the arc with a radial wobble. Returns source face, target face, edge, axis point, axis."""
    th = math.radians(20.0)
    t = V(0, -math.cos(th), math.sin(th))
    n = V(0, math.sin(th), math.cos(th))
    c = n * 20.0
    line = Part.makeLine(V(0, 0, 0), V(40, 0, 0))
    face = None
    for ang in (90, -90):
        face = line.revolve(c, V(1, 0, 0), ang).Faces[0]
        if face.CenterOfMass.dot(t) > 0:
            break

    def arc_point(phi):
        return c + (n * -math.cos(phi) + t * math.sin(phi)) * 20.0

    edge = pick_edge(face, arc_point(math.pi / 4))
    base = []
    for i in range(6):
        phi = math.pi / 2 * i / 5.0
        radial = n * -math.cos(phi) + t * math.sin(phi)
        base.append(arc_point(phi) + radial * (0.8 * math.sin(2.1 * i + 0.3)))
    src = source_surface(base, unit(V(-1, 0.15, 0.1)), bump=V(0, 0, 0.5))
    return src.toShape(), face, edge, c, V(1, 0, 0)


def sphere_arc_case():
    sph = Part.makeSphere(25.0, V(0, 0, 0), V(0, 0, 1), 0, 40, 60)
    face = [f for f in sph.Faces if f.Surface.TypeId == "Part::GeomSphere"][0]

    def eq(a):
        return V(25 * math.cos(a), 25 * math.sin(a), 0)

    edge = pick_edge(face, eq(math.pi / 6))
    rad = V(math.cos(math.pi / 6), math.sin(math.pi / 6), 0)
    base = [eq(math.pi / 3 * i / 5.0) + V(math.cos(math.pi / 3 * i / 5.0), math.sin(math.pi / 3 * i / 5.0), 0) * (0.8 * math.sin(2.1 * i + 0.3)) for i in range(6)]
    src = source_surface(base, unit(V(0, 0, -1) + rad * 0.45), bump=rad)
    return src.toShape(), face, edge


def freeform_target():
    """A degree 4 by 3 Bezier face and its v = 0 boundary edge."""
    P = []
    for i in range(5):
        P.append([V(10.0 * i, -10.0 * j, 1.5 * math.sin(1.3 * i + 1.4) + 3.0 * j + math.sin(0.8 * i + 1.1 * j)) for j in range(4)])
    s = Part.BSplineSurface()
    s.buildFromPolesMultsKnots(P, [5, 5], [4, 4], [0.0, 1.0], [0.0, 1.0], False, False, 4, 3)
    face = s.toShape()
    return face, pick_edge(face, V(20, 0, 0))


def free_source():
    return source_surface(line_base(), V(0, 1, 0), bump_row0=True).toShape()


def flat_source():
    return source_surface(line_base(), V(0, 1, 0)).toShape()


# ------------------------------------------------------------------------- independent measures


def side_samples(surface, side="V0", n=21):
    """Off-grid ``(u, v)`` on the side, by arc length of the side's iso curve."""
    u0, u1, v0, v1 = surface.bounds()
    iso = {"V0": surface.vIso(v0), "V1": surface.vIso(v1), "U0": surface.uIso(u0), "U1": surface.uIso(u1)}[side].toShape()
    out = []
    for j in range(n):
        t = iso.getParameterByLength((j + 0.37) / n * iso.Length)
        out.append({"V0": (t, v0), "V1": (t, v1), "U0": (u0, t), "U1": (u1, t)}[side])
    return out


def normal_at(surface, u, v):
    return unit(surface.getDN(u, v, 1, 0).cross(surface.getDN(u, v, 0, 1)))


def normal_curvature(surface, u, v, w, n):
    """II(w, w) / I(w, w) against the unit normal ``n`` for the tangent-plane part of ``w``."""
    su, sv = surface.getDN(u, v, 1, 0), surface.getDN(u, v, 0, 1)
    suu, suv, svv = surface.getDN(u, v, 2, 0), surface.getDN(u, v, 1, 1), surface.getDN(u, v, 0, 2)
    e, f, g = su.dot(su), su.dot(sv), sv.dot(sv)
    det = e * g - f * f
    r1, r2 = w.dot(su), w.dot(sv)
    a, b = (r1 * g - r2 * f) / det, (r2 * e - r1 * f) / det
    return n.dot(suu * (a * a) + suv * (2 * a * b) + svv * (b * b)) / (a * a * e + 2 * a * b * f + b * b * g)


def cross_derivative(surface, side, u, v):
    d = surface.getDN(u, v, 0, 1) if side[0] == "V" else surface.getDN(u, v, 1, 0)
    return d * -1.0 if side[1] == "1" else d


def worst_over_side(result, side, target_normal_and_curvature, edge):
    """Maxima over the side of: the gap to the edge, the angle to the target's normal (degrees) and the
    difference of the normal curvatures along the cross derivative."""
    s = result["surface"]
    gap = angle = dk = 0.0
    for u, v in side_samples(s, side):
        p = s.value(u, v)
        gap = max(gap, Part.Vertex(p).distToShape(edge)[0])
        n = normal_at(s, u, v)
        tn, tk = target_normal_and_curvature(p, cross_derivative(s, side, u, v))
        sign = 1.0 if n.dot(tn) >= 0 else -1.0
        angle = max(angle, math.degrees(math.atan2(n.cross(tn).Length, n.dot(tn) * sign)))
        dk = max(dk, abs(normal_curvature(s, u, v, cross_derivative(s, side, u, v), tn) - tk))
    return gap, angle, dk


def plane_truth(normal):
    return lambda p, w: (normal, 0.0)


def refined_original(source_face, side, edge_is_straight, level):
    """The test's own degree-raised, refined original: degree 5 along the side unless the edge is
    straight, then the midpoint of every along knot span inserted ``level`` times."""
    s = PE.editable_surface(source_face)
    along_u = side[0] == "V"
    if not edge_is_straight:
        s.increaseDegree(max(5, s.UDegree), s.VDegree) if along_u else s.increaseDegree(s.UDegree, max(5, s.VDegree))
    for _ in range(level):
        kn = s.getUKnots() if along_u else s.getVKnots()
        mids = [(kn[i] + kn[i + 1]) / 2.0 for i in range(len(kn) - 1)]
        s.insertUKnots(mids, [1] * len(mids), 1e-12) if along_u else s.insertVKnots(mids, [1] * len(mids), 1e-12)
    return s


# ------------------------------------------------------------------------- tests


class TestMatchPlane(unittest.TestCase):
    """A plane 2 mm away from the side's end, on each of the four sides, at each continuity."""

    def setUp(self):
        self.face, self.edge = plane_target(30.0, V(0, 0, -2.0))
        self.normal = unit(self.face.normalAt(*self.face.Surface.parameter(V(10, 0, -2))))

    def test_every_side_and_continuity_meets_the_plane(self):
        base = flat_source()
        for side in ("V0", "V1", "U0", "U1"):
            src = as_side(base.Surface, side).toShape()
            for k in (0, 1, 2):
                r = SM.matchSurface(src, side, self.face, self.edge, k)
                self.assertEqual(r["level"], 0)
                self.assertFalse(r["approximate"])
                gap, angle, dk = worst_over_side(r, side, plane_truth(self.normal), self.edge)
                self.assertLess(gap, 1e-4)
                if k >= 1:
                    self.assertLess(angle, 0.05)
                    self.assertLess(r["angle"], 0.05)
                if k == 2:
                    self.assertLess(dk, 0.01 * 0.1 / 60.0 + 1e-4)
                    self.assertLess(r["curvature_jump"], 0.01)
                self.assertTrue(r["face"].isValid())

    def test_the_gap_is_the_measured_one(self):
        r = SM.matchSurface(flat_source(), "V0", self.face, self.edge, 2)
        s = r["surface"]
        worst = max(Part.Vertex(s.value(u, v)).distToShape(self.edge)[0] for u, v in side_samples(s))
        self.assertLess(abs(r["gap"] - worst), 1e-5)
        self.assertLess(r["gap"], 1e-4)

    def test_only_the_first_rows_move_and_the_rest_are_the_original(self):
        src = flat_source()
        for k in (0, 1, 2):
            r = SM.matchSurface(src, "V0", self.face, self.edge, k)
            orig = refined_original(src, "V0", True, r["level"])
            self.assertEqual(r["poles_along"], orig.NbUPoles)
            self.assertEqual(r["degree_along"], orig.UDegree)
            new_poles, old_poles = r["surface"].getPoles(), orig.getPoles()
            moved = 0.0
            for i in range(len(old_poles)):
                for j in range(len(old_poles[0])):
                    d = (new_poles[i][j] - old_poles[i][j]).Length
                    if j > k:
                        self.assertLess(d, 1e-12)
                    else:
                        moved = max(moved, d)
            self.assertAlmostEqual(r["max_move"], moved, places=9)
            self.assertGreater(moved, 0.5)

    def test_the_result_keeps_the_source_orientation(self):
        src = flat_source()
        rev = src.copy()
        rev.reverse()
        a = SM.matchSurface(src, "V0", self.face, self.edge, 2)["face"]
        b = SM.matchSurface(rev, "V0", self.face, self.edge, 2)["face"]
        self.assertEqual(a.Orientation, src.Orientation)
        self.assertEqual(b.Orientation, rev.Orientation)
        self.assertLess(a.normalAt(0.5, 0.5).dot(b.normalAt(0.5, 0.5)), -0.5)

    def test_an_edge_of_the_face_names_the_side(self):
        src = flat_source()
        edge = pick_edge(src, V(20, 0, 0))
        by_edge = SM.matchSurface(src, edge, self.face, self.edge, 1)
        by_name = SM.matchSurface(src, "V0", self.face, self.edge, 1)
        self.assertLess((by_edge["surface"].getPole(3, 1) - by_name["surface"].getPole(3, 1)).Length, 1e-12)
        self.assertLess((by_edge["surface"].getPole(3, 2) - by_name["surface"].getPole(3, 2)).Length, 1e-12)


class TestMatchCurved(unittest.TestCase):
    def check_arc_levels(self, case, k=2):
        src, face, edge = case
        r = SM.matchSurface(src, "V0", face, edge, k)
        self.assertFalse(r["approximate"])
        self.assertEqual(r["degree_along"], 5)
        self.assertGreaterEqual(r["level"], 1)
        # rows beyond k are the refined original's, exactly
        orig = refined_original(src, "V0", False, r["level"])
        self.assertEqual(r["poles_along"], orig.NbUPoles)
        new_poles, old_poles = r["surface"].getPoles(), orig.getPoles()
        for i in range(len(old_poles)):
            for j in range(k + 1, len(old_poles[0])):
                self.assertLess((new_poles[i][j] - old_poles[i][j]).Length, 1e-12)
        return r

    def test_cylinder_end_arc_reaches_g2_and_matches_the_closed_form(self):
        src, face, edge, c, axis = cylinder_arc_case()
        r = self.check_arc_levels((src, face, edge))
        self.assertLess(r["gap"], 1e-4)
        self.assertLess(r["angle"], 0.05)
        self.assertLess(r["curvature_jump"], 0.01)

        def truth(p, w):
            rv = p - c
            rv = rv - axis * rv.dot(axis)
            radius = rv.Length
            rv = rv * (1.0 / radius)
            tn = rv * (1.0 if face.normalAt(*face.Surface.parameter(p)).dot(rv) > 0 else -1.0)
            e = unit(axis.cross(rv))
            wn = unit(w)
            return tn, (wn.dot(e)) ** 2 / radius * (1.0 if tn.dot(rv) < 0 else -1.0)

        gap, angle, dk = worst_over_side(r, "V0", truth, edge)
        self.assertLess(gap, 1.5e-4)
        self.assertLess(angle, 0.075)
        self.assertLess(dk, 0.01 / 20.0 * 1.5 + 1e-4)

    def test_sphere_arc_reaches_g2_and_matches_the_closed_form(self):
        src, face, edge = sphere_arc_case()
        r = self.check_arc_levels((src, face, edge))

        def truth(p, w):
            rv = unit(p)
            tn = rv * (1.0 if face.normalAt(*face.Surface.parameter(p)).dot(rv) > 0 else -1.0)
            return tn, 1.0 / 25.0 * (1.0 if tn.dot(rv) < 0 else -1.0)

        gap, angle, dk = worst_over_side(r, "V0", truth, edge)
        self.assertLess(gap, 1.5e-4)
        self.assertLess(angle, 0.075)
        self.assertLess(dk, 0.01 / 25.0 * 1.5 + 1e-4)

    def test_freeform_target_edge_reaches_g2_by_the_continuity_measure(self):
        face, edge = freeform_target()
        r = SM.matchSurface(free_source(), "V0", face, edge, 2)
        self.assertFalse(r["approximate"])
        mine = r["face"]
        if mine.normalAt(0.5, 0.5).dot(face.normalAt(*face.Surface.parameter(edge.valueAt(0.5 * (edge.FirstParameter + edge.LastParameter))))) < 0:
            mine = mine.copy()
            mine.reverse()
        m = SA.edge_continuity(None, edge, samples=41, faces=(mine, face))
        self.assertLessEqual(m["gap"], 1e-4)
        self.assertLessEqual(m["angle"], 0.0501)
        self.assertLessEqual(m["curvature_jump"], 0.0101)
        # the least change: the rows beyond the second stay
        self.assertLess(r["max_move"], 40.0)

    def test_the_level_rule_and_the_approximate_flag(self):
        src, face, edge, _, _ = cylinder_arc_case()
        with self.assertRaises(ValueError) as cm:
            SM.matchSurface(src, "V0", face, edge, 2, max_level=0)
        self.assertTrue(str(cm.exception).startswith("DeviationExceeded"))
        r = SM.matchSurface(src, "V0", face, edge, 2, max_level=0, approximate=True)
        self.assertTrue(r["approximate"])
        self.assertEqual(r["level"], 0)
        self.assertTrue(r["face"].isValid())
        loose = SM.matchSurface(src, "V0", face, edge, 0, tolerance=1.0, max_level=0)
        self.assertFalse(loose["approximate"])

    def test_the_degree_rule_raises_to_five_only_for_g1_and_g2_on_a_curved_edge(self):
        src, face, edge, _, _ = cylinder_arc_case()
        self.assertEqual([SM.matchSurface(src, "V0", face, edge, k)["degree_along"] for k in (0, 1, 2)], [3, 5, 5])
        straight, sedge = plane_target(30.0, V(0, 0, -2.0))
        self.assertEqual(SM.matchSurface(flat_source(), "V0", straight, sedge, 2)["degree_along"], 3)

    def test_a_source_already_continuous_with_the_target_is_not_moved(self):
        # two quarter-turn pieces of one cylinder: the shared generator is G2 already
        cyl = Part.makeCylinder(15.0, 30.0, V(0, 0, 0), V(0, 0, 1), 180)
        piece = [f for f in cyl.Faces if f.Surface.TypeId == "Part::GeomCylinder"][0]
        quarter = Part.makeCylinder(15.0, 30.0, V(0, 0, 0), V(0, 0, 1), 90)
        quarter.rotate(V(0, 0, 0), V(0, 0, 1), -90)
        other = [f for f in quarter.Faces if f.Surface.TypeId == "Part::GeomCylinder"][0]
        edge = next(e for e in other.Edges if e.Length > 29.0 and (e.valueAt(e.FirstParameter) - V(15, 0, 0)).Length < 1e-6 or (e.valueAt(e.LastParameter) - V(15, 0, 0)).Length < 1e-6 and e.Length > 29.0)
        src = PE.editable_surface(piece)
        self.assertGreaterEqual(src.NbUPoles, 4)
        r = SM.matchSurface(piece, "U0", other, edge, 2)
        self.assertEqual(r["level"], 0)
        self.assertLess(r["max_move"], 1e-9)
        self.assertLess(r["gap"], 1e-9)


class TestMatchFold(unittest.TestCase):
    def match(self, origin, theta=30.0, k=2):
        face, edge = plane_target(theta, origin)
        return SM.matchSurface(flat_source(), "V0", face, edge, k)

    def test_a_target_edge_deep_inside_the_patch_is_refused(self):
        for y in (7.0, 15.0):
            with self.assertRaises(ValueError) as cm:
                self.match(V(0, y, 0))
            self.assertTrue(str(cm.exception).startswith("FoldedResult"), str(cm.exception))

    def test_a_near_collapse_succeeds_and_reports_the_stretch(self):
        r = self.match(V(0, 4.0, 0))
        self.assertLess(r["stretch"][0], 0.25)
        self.assertGreater(r["stretch"][0], 0.0)
        self.assertEqual(r["warnings"] == ["StretchWarning"], r["stretch"][0] < 0.2 or r["stretch"][1] > 4.0)
        self.assertEqual(self.match(V(0, 1.0, 0))["warnings"], [])

    def test_a_far_edge_stretches_the_rows_without_a_fold(self):
        r = self.match(V(0, -15.0, 0))
        self.assertGreater(r["stretch"][0], 2.0)
        self.assertEqual(r["warnings"], ["StretchWarning"] if r["stretch"][1] > 4.0 else [])

    def test_a_target_tilted_steeply_folds_the_source_over_it(self):
        with self.assertRaises(ValueError) as cm:
            self.match(V(0, 0, -2.0), theta=85.0)
        self.assertTrue(str(cm.exception).startswith("FoldedResult"))
        with self.assertRaises(ValueError) as cm:
            self.match(V(0, 0, -2.0), theta=150.0)
        self.assertTrue(str(cm.exception).startswith("FoldedResult"))


class TestMatchRefusals(unittest.TestCase):
    def setUp(self):
        self.face, self.edge = plane_target(30.0, V(0, 0, -2.0))
        self.src = flat_source()

    def refused(self, name, *args, **kw):
        with self.assertRaises(ValueError) as cm:
            SM.matchSurface(*args, **kw)
        self.assertTrue(str(cm.exception).startswith(name), str(cm.exception))

    def test_argument_types(self):
        for args in (
            (self.src.Surface, "V0", self.face, self.edge),
            (self.src, "V0", self.edge, self.edge),
            (self.src, "V0", self.face, self.face),
            (self.src, "V0", self.face, None),
        ):
            with self.assertRaises(TypeError):
                SM.matchSurface(*args)

    def test_numeric_arguments(self):
        a = (self.src, "V0", self.face, self.edge)
        for tol in (0.0, -1.0, float("nan"), float("inf"), True, "1e-4"):
            self.refused("InvalidTolerance", *a, tolerance=tol)
        for c in (3, -1, 1.5, True, None):
            self.refused("InvalidContinuity", *a, continuity=c)
        for lv in (9, -1, 2.5, True, None):
            self.refused("InvalidMaxLevel", *a, max_level=lv)

    def test_sides(self):
        for side in ("U2", "v0", 0, None, pick_edge(self.face, V(20, 0, -2))):
            self.refused("InvalidSide", self.src, side, self.face, self.edge)

    def test_trimmed_and_periodic_sources(self):
        trimmed = flat_source().Surface.toShape(0.1, 0.9, 0.1, 0.9)
        self.refused("TrimmedFace", trimmed, "V0", self.face, self.edge)

    def test_a_periodic_cross_direction_is_refused(self):
        cyl = Part.makeCylinder(15.0, 30.0)
        full = [f for f in cyl.Faces if f.Surface.TypeId == "Part::GeomCylinder"][0]
        self.assertTrue(PE.editable_surface(full).isUPeriodic())
        self.refused("PeriodicSurface", full, "U0", self.face, self.edge, continuity=1)

    def test_rows_in_the_cross_direction(self):
        two = Part.makePlane(10, 10).toNurbs().Faces[0]
        self.assertEqual(PE.editable_surface(two).NbVPoles, 2)
        self.refused("InsufficientRows", two, "V0", self.face, self.edge, continuity=2)
        self.refused("InsufficientRows", two, "V0", self.face, self.edge, continuity=1)
        self.assertTrue(SM.matchSurface(two, "V0", *plane_target(0.0, V(0, -1, 0)), continuity=0)["face"].isValid())
        deg1 = net_surface([[V(10 * i, 5 * j, 0) for j in range(5)] for i in range(5)], 3, 1).toShape()
        self.assertEqual(PE.editable_surface(deg1).VDegree, 1)
        self.refused("InsufficientRows", deg1, "V0", self.face, self.edge, continuity=2)
        self.assertTrue(SM.matchSurface(deg1, "V0", self.face, self.edge, continuity=1)["face"].isValid())

    def test_target_edges(self):
        a = (self.src, "V0", self.face)
        self.refused("ClosedEdge", *a, Part.makeCircle(5.0))
        disc = Part.Face(Part.Wire(Part.makeCircle(5.0)))
        self.refused("ClosedEdge", self.src, "V0", disc, disc.Edges[0])
        other, other_edge = plane_target(30.0, V(100, 100, 100))
        self.refused("EdgeNotOnFace", *a, other_edge)
        sliver = Part.makeLine(V(0, 0, 0), V(1e-6, 0, 0))
        self.refused("DegenerateEdge", *a, sliver)

    def test_rational_sides_need_constant_weights_for_g2(self):
        cyl = Part.makeCylinder(15.0, 30.0, V(0, 0, 0), V(0, 0, 1), 90)
        side = [f for f in cyl.Faces if f.Surface.TypeId == "Part::GeomCylinder"][0]
        s = PE.editable_surface(side)  # u: angle, rational with 3 poles; v: height, 2 poles
        s.increaseDegree(2, 3)
        s.insertVKnots([0.5 * (s.getVKnots()[0] + s.getVKnots()[-1])], [1], 1e-12)
        src = s.toShape()
        # a coaxial cylinder of radius 15.5 below the arc: tangent planes agree
        wide = Part.makeCylinder(15.5, 10.0, V(0, 0, -10), V(0, 0, 1), 90)
        tface = [f for f in wide.Faces if f.Surface.TypeId == "Part::GeomCylinder"][0]
        tedge = pick_edge(tface, V(15.5 * math.cos(math.pi / 4), 15.5 * math.sin(math.pi / 4), 0))
        # the side of the source that is the arc at z = 0
        arc_side = "V0" if abs(s.vIso(s.bounds()[2]).value(s.bounds()[0]).z) < 1e-6 else "V1"
        self.refused("RationalG2", src, arc_side, tface, tedge, continuity=2)
        r = SM.matchSurface(src, arc_side, tface, tedge, continuity=1)
        self.assertFalse(r["approximate"])
        self.assertLess(r["gap"], 1e-4)
        self.assertLess(r["angle"], 0.05)


class TestMatchUsable(unittest.TestCase):
    def check_usable(self, r, target_face):
        face = r["face"]
        self.assertTrue(face.isValid())
        for d in (1.0, -1.0):
            self.assertTrue(face.makeOffsetShape(d, 1e-6).isValid())
        sewn = Part.Compound([face.copy(), target_face.copy()])
        sewn.sewShape(1e-4)
        self.assertTrue(sewn.isValid())
        self.assertEqual(len(sewn.Shells), 1)
        shared = [e for e in sewn.Edges if len(sewn.ancestorsOfType(e, Part.Face)) == 2]
        self.assertEqual(len(shared), 1)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "f.step")
            face.exportStep(path)
            back = Part.read(path)
            self.assertLess(abs(back.Area - face.Area) / face.Area, 1e-6)

    def test_plane(self):
        face, edge = plane_target(30.0, V(0, 0, -2.0))
        self.check_usable(SM.matchSurface(flat_source(), "V0", face, edge, 2), face)

    # OCCT's sewing joins two faces only when their shared edges agree to a few 1e-6 whatever tolerance
    # it is given, so the curved cases ask for a gap of 1e-6 instead of the default 1e-4.
    def test_cylinder_arc(self):
        src, face, edge, _, _ = cylinder_arc_case()
        self.check_usable(SM.matchSurface(src, "V0", face, edge, 2, tolerance=1e-6), face)

    def test_freeform(self):
        face, edge = freeform_target()
        self.check_usable(SM.matchSurface(free_source(), "V0", face, edge, 2, tolerance=1e-6), face)

    def test_speed(self):
        import time

        face, edge = plane_target(30.0, V(0, 0, -2.0))
        t = time.time()
        SM.matchSurface(flat_source(), "V0", face, edge, 2)
        self.assertLess(time.time() - t, 3.0)
        face, edge = freeform_target()
        t = time.time()
        SM.matchSurface(free_source(), "V0", face, edge, 2)
        self.assertLess(time.time() - t, 30.0)
