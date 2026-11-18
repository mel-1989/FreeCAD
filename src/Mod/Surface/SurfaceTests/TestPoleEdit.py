# SPDX-License-Identifier: LGPL-2.1-or-later

"""Tests of ``SurfacePoleEdit`` (control-point editing of B-spline faces, G09 slice 4a).

Public sources: the B-spline basis and its derivatives (L. Piegl and W. Tiller, *The NURBS Book*, 2nd ed.,
1997, chapters 2 and 3). The truths are closed form: a surface is linear in its poles, so an edit adds
the offset times the product of the basis functions; the clamped end knots make the derivatives across a
boundary depend on the first rows only. The test basis below is its own Cox-de Boor recursion, not
OCCT's.
"""

import math
import os
import tempfile
import unittest

import FreeCAD
import Part
import SurfaceBlendSurface as SBS
import SurfacePoleEdit as PE

V = FreeCAD.Vector


def height_grid(nu, nv, bump=1.0):
    return [[V(2.0 * i, 3.0 * j, bump * math.sin(0.9 * i) * math.cos(0.7 * j) + 0.1 * i * j) for j in range(nv)] for i in range(nu)]


def clamped_face(nu, nv, du, dv):
    """A clamped B-spline face with nu by nv poles (simple interior knots) and degrees du, dv."""

    def knots(n, d):
        k = n - d + 1  # distinct knots
        return [i / (k - 1.0) for i in range(k)], [d + 1] + [1] * (k - 2) + [d + 1]

    uk, um = knots(nu, du)
    vk, vm = knots(nv, dv)
    s = Part.BSplineSurface()
    s.buildFromPolesMultsKnots(height_grid(nu, nv), um, vm, uk, vk, False, False, du, dv)
    return s.toShape()


def cubic_face(nu=6, nv=8):
    """A clamped cubic face with a non-square pole grid."""
    return clamped_face(nu, nv, 3, 3)


def mixed_degree_face():
    """Degree 2 in u and 5 in v, interior knots of multiplicity 2."""
    poles = [[V(i * 2.0, j * 1.5, math.sin(0.8 * i + 0.5 * j)) for j in range(10)] for i in range(5)]
    s = Part.BSplineSurface()
    s.buildFromPolesMultsKnots(poles, [3, 2, 3], [6, 2, 2, 6], [0.0, 0.5, 1.0], [0.0, 1.0 / 3, 2.0 / 3, 1.0], False, False, 2, 5)
    return s.toShape()


def same_surface(a, b, tol=1e-9, n=21):
    u0, u1, v0, v1 = a.bounds()
    for i in range(n):
        for j in range(n):
            u, v = u0 + (u1 - u0) * i / (n - 1.0), v0 + (v1 - v0) * j / (n - 1.0)
            if (a.value(u, v) - b.value(u, v)).Length > tol:
                return False
    return True


def knot_vector(knots, mults):
    out = []
    for k, m in zip(knots, mults):
        out.extend([k] * m)
    return out


def basis(deg, knots, i, u, last):
    """Cox-de Boor N_{i,deg}(u) on the full knot vector; the last span is closed at its right end."""
    if deg == 0:
        if knots[i] <= u < knots[i + 1]:
            return 1.0
        return 1.0 if (last and u == knots[-1] and knots[i] < knots[i + 1] == knots[-1]) else 0.0
    out = 0.0
    d1 = knots[i + deg] - knots[i]
    if d1 > 0:
        out += (u - knots[i]) / d1 * basis(deg - 1, knots, i, u, last)
    d2 = knots[i + deg + 1] - knots[i + 1]
    if d2 > 0:
        out += (knots[i + deg + 1] - u) / d2 * basis(deg - 1, knots, i + 1, u, last)
    return out


def own_delta(surface, moves, u, v):
    """sum N_i(u) N_j(v) d_ij for a non-rational clamped surface, from the surface's own knots."""
    ku = knot_vector(surface.getUKnots(), surface.getUMultiplicities())
    kv = knot_vector(surface.getVKnots(), surface.getVMultiplicities())
    out = V()
    for (i, j), d in moves.items():
        w = basis(surface.UDegree, ku, i - 1, u, True) * basis(surface.VDegree, kv, j - 1, v, True)
        out = out + d * w
    return out


class TestPoleEditApi(unittest.TestCase):
    def test_edit_adds_the_offset_times_the_basis(self):
        for face in (cubic_face(), mixed_degree_face()):
            src = PE.editable_surface(face)
            moves = {(2, 3): V(0.3, -0.2, 0.7), (src.NbUPoles, src.NbVPoles): V(0, 0, -1.5), (3, 1): V(1, 1, 1)}
            r = PE.edit_poles(face, moves)
            new = r["surface"]
            u0, u1, v0, v1 = src.bounds()
            worst = 0.0
            for a in range(9):
                for b in range(9):
                    u, v = u0 + (u1 - u0) * a / 8.0, v0 + (v1 - v0) * b / 8.0
                    got = new.value(u, v) - src.value(u, v)
                    self.assertLess((got - own_delta(src, moves, u, v)).Length, 1e-9)
            for a in range(21):
                for b in range(21):
                    u, v = u0 + (u1 - u0) * a / 20.0, v0 + (v1 - v0) * b / 20.0
                    worst = max(worst, (new.value(u, v) - src.value(u, v)).Length)
            self.assertAlmostEqual(r["max_deviation"], worst, places=9)
            self.assertEqual(r["moved"], 3)
            self.assertTrue(r["face"].isValid())

    def test_index_is_i_along_u_and_poles_are_one_based(self):
        face = cubic_face(6, 8)
        src = PE.editable_surface(face)
        r = PE.edit_poles(face, [((2, 7), V(0, 0, 5))])
        for i in range(1, 7):
            for j in range(1, 9):
                moved = (r["surface"].getPole(i, j) - src.getPole(i, j)).Length
                self.assertAlmostEqual(moved, 5.0 if (i, j) == (2, 7) else 0.0, places=12)

    def test_weights_are_never_changed(self):
        cyl = Part.makeCylinder(3, 10).Faces[0]
        src = PE.editable_surface(cyl)
        r = PE.edit_poles(cyl, {(2, 2): V(0.5, 0.5, 0.5), (4, 1): V(0, 0, 1)})
        for row_new, row_old in zip(r["surface"].getPolesAndWeights(), src.getPolesAndWeights()):
            for a, b in zip(row_new, row_old):
                self.assertEqual(a[3], b[3])
        sph = Part.makeSphere(5).Faces[0]
        s0 = PE.editable_surface(sph)
        s1 = PE.edit_poles(sph, {(3, 3): V(1, 0, 0)})["surface"]
        self.assertTrue(s0.isURational() or s0.isVRational())
        for row_new, row_old in zip(s1.getPolesAndWeights(), s0.getPolesAndWeights()):
            for a, b in zip(row_new, row_old):
                self.assertEqual(a[3], b[3])

    def test_conversion_is_exact_for_the_analytic_faces(self):
        solids = (Part.makeCylinder(3, 10), Part.makeSphere(5), Part.makeCone(3, 1, 5), Part.makeTorus(10, 2), Part.makeBox(4, 5, 6))
        faces = [f for s in solids for f in s.Faces if len(f.Edges) != 1 or f.Surface.TypeId != "Part::GeomPlane"]
        faces.extend([cubic_face(), mixed_degree_face()])
        self.assertGreaterEqual(len(faces), 10)
        discs = [f for s in solids[:3] for f in s.Faces if f.Surface.TypeId == "Part::GeomPlane" and len(f.Edges) == 1]
        self.assertTrue(discs)
        for f in discs:  # a disc is a plane trimmed by a circle, not a patch
            with self.assertRaises(ValueError):
                PE.editable_surface(f)
        for f in faces:
            s = PE.editable_surface(f)
            self.assertEqual(s.TypeId, "Part::GeomBSplineSurface")
            # the rational parametrisation of a circle is not the angle, so the points of the editable
            # surface are compared with the face's own surface by projection
            u0, u1, v0, v1 = s.bounds()
            for a in range(21):
                for b in range(21):
                    p = s.value(u0 + (u1 - u0) * a / 20.0, v0 + (v1 - v0) * b / 20.0)
                    self.assertLess(f.distToShape(Part.Vertex(p))[0], 1e-9, f.Surface.TypeId)

    def test_a_trimmed_face_is_refused(self):
        box = Part.makeBox(10, 10, 10)
        cut = box.cut(Part.makeCylinder(2, 20, V(5, 5, -5)))
        holed = [f for f in cut.Faces if len(f.Wires) == 2]
        self.assertTrue(holed)
        for f in holed:
            with self.assertRaises(ValueError) as cm:
                PE.editable_surface(f)
            self.assertTrue(str(cm.exception).startswith("TrimmedFace"))
        plane = Part.makePlane(10, 10)
        diag = Part.makeLine(V(0, 0, 0), V(10, 10, 0))
        from BOPTools import SplitAPI

        for f in SplitAPI.slice(plane, [diag], "Split").Faces:
            with self.assertRaises(ValueError) as cm:
                PE.edit_poles(f, [])
            self.assertTrue(str(cm.exception).startswith("TrimmedFace"))
        self.assertEqual(PE.editable_surface(box.Faces[0]).NbUPoles, 2)  # a whole box face is not trimmed

    def test_refusals_carry_their_status_name(self):
        face = cubic_face()
        with self.assertRaises(TypeError):
            PE.editable_surface(face.Edges[0])
        cases = (
            ({(0, 1): V(1, 0, 0)}, "InvalidIndex"),
            ({(7, 1): V(1, 0, 0)}, "InvalidIndex"),
            ({(1, 9): V(1, 0, 0)}, "InvalidIndex"),
            ({(1.5, 2): V(1, 0, 0)}, "InvalidIndex"),
            ({(True, 2): V(1, 0, 0)}, "InvalidIndex"),
            ([((2, 2), V(1, 0, 0)), ((2, 2), V(0, 1, 0))], "DuplicatePole"),
            ({(2, 2): V(float("nan"), 0, 0)}, "InvalidOffset"),
            ({(2, 2): V(0, float("inf"), 0)}, "InvalidOffset"),
            ({(2, 2): "x"}, "InvalidOffset"),
        )
        for moves, name in cases:
            with self.assertRaises(ValueError, msg=name) as cm:
                PE.edit_poles(face, moves)
            self.assertTrue(str(cm.exception).startswith(name), str(cm.exception))
        for locks in ({"U2": 1}, {"U0": 3}, {"U0": "G1"}, {"U0": True}, {"U0": 1.0}, "U0"):
            with self.assertRaises(ValueError) as cm:
                PE.edit_poles(face, [], locks)
            self.assertTrue(str(cm.exception).startswith("InvalidLock"), str(cm.exception))

    def test_a_periodic_face_edits_and_refuses_a_lock_on_its_periodic_side(self):
        cyl = Part.makeCylinder(3, 10).Faces[0]
        s = PE.editable_surface(cyl)
        self.assertTrue(s.isUPeriodic() and not s.isVPeriodic())
        for side in ("U0", "U1"):
            with self.assertRaises(ValueError) as cm:
                PE.edit_poles(cyl, [], {side: 0})
            self.assertTrue(str(cm.exception).startswith("InvalidLock"))
        PE.edit_poles(cyl, [], {"U0": None, "V0": 1})  # free on a periodic side, a lock on the clamped one
        r = PE.edit_poles(cyl, {(3, 2): V(0, 0, 1)}, {"V0": 0})
        self.assertEqual(r["moved"], 1)
        self.assertTrue(r["face"].isValid())

    def test_the_result_keeps_the_orientation_and_is_usable(self):
        face = cubic_face()
        rev = face.copy()
        rev.reverse()
        self.assertEqual(PE.edit_poles(rev, {(3, 3): V(0, 0, 2)})["face"].Orientation, rev.Orientation)
        r = PE.edit_poles(face, {(3, 3): V(0, 0, 2)}, {"U0": 0, "U1": 0, "V0": 0, "V1": 0})
        self.assertTrue(r["face"].isValid())
        pts = PE.editable_surface(face)
        for side_old, side_new in ((pts.uIso(pts.bounds()[0]), r["surface"].uIso(pts.bounds()[0])),):
            for k in range(11):
                p = side_old.FirstParameter + (side_old.LastParameter - side_old.FirstParameter) * k / 10.0
                self.assertLess((side_old.value(p) - side_new.value(p)).Length, 1e-9)


class TestPoleEditLocks(unittest.TestCase):
    def test_the_locked_pole_set_per_side_and_k(self):
        s = PE.editable_surface(cubic_face(6, 8))
        nu, nv = 6, 8
        for k in (0, 1, 2):
            self.assertEqual(PE.locked_poles(s, {"U0": k}), {(i, j) for i in range(1, k + 2) for j in range(1, nv + 1)})
            self.assertEqual(PE.locked_poles(s, {"U1": k}), {(i, j) for i in range(nu - k, nu + 1) for j in range(1, nv + 1)})
            self.assertEqual(PE.locked_poles(s, {"V0": k}), {(i, j) for j in range(1, k + 2) for i in range(1, nu + 1)})
            self.assertEqual(PE.locked_poles(s, {"V1": k}), {(i, j) for j in range(nv - k, nv + 1) for i in range(1, nu + 1)})
        self.assertEqual(PE.locked_poles(s, {}), set())
        self.assertEqual(PE.locked_poles(s, {"U0": None, "V1": None}), set())

    def test_moving_a_locked_pole_raises_with_its_index(self):
        face = cubic_face(6, 8)
        s = PE.editable_surface(face)
        for side in PE.SIDES:
            for k in (0, 1, 2):
                locks = {side: k}
                locked = PE.locked_poles(s, locks)
                for i in range(1, 7):
                    for j in range(1, 9):
                        if (i, j) in locked:
                            with self.assertRaises(ValueError) as cm:
                                PE.edit_poles(face, {(i, j): V(0, 0, 1)}, locks)
                            self.assertTrue(str(cm.exception).startswith("LockedPole"))
                            self.assertIn("(%d, %d)" % (i, j), str(cm.exception))
                        else:
                            PE.edit_poles(face, {(i, j): V(0, 0, 1)}, locks)
        PE.edit_poles(face, {(1, 1): V()}, {"U0": 2})  # a zero offset moves nothing, so a locked pole accepts it

    def test_a_lock_keeps_its_continuity_and_the_next_row_breaks_the_next_order(self):
        def derivs(surface, side, order, t):
            u0, u1, v0, v1 = surface.bounds()
            if side[0] == "U":
                u, v = (u0 if side == "U0" else u1), v0 + (v1 - v0) * t
                return surface.value(u, v) if order == 0 else surface.getDN(u, v, order, 0)
            u, v = u0 + (u1 - u0) * t, (v0 if side == "V0" else v1)
            return surface.value(u, v) if order == 0 else surface.getDN(u, v, 0, order)

        for face in (cubic_face(6, 8), mixed_degree_face()):
            src = PE.editable_surface(face)
            nu, nv = src.NbUPoles, src.NbVPoles
            for side in PE.SIDES:
                for k in (0, 1, 2):
                    locks = {side: k}
                    locked = PE.locked_poles(src, locks)
                    deg = src.UDegree if side[0] == "U" else src.VDegree
                    if k > deg - 1 and k > 0:
                        continue
                    moves = {(i, j): V(0.1 * ((i * j) % 3 - 1), 0.07 * (i - j), 0.2 * (j % 2 + 1)) for i in range(1, nu + 1) for j in range(1, nv + 1) if (i, j) not in locked}
                    new = PE.edit_poles(face, moves, locks)["surface"]
                    for t in (0.1, 0.35, 0.6, 0.93):
                        for order in range(k + 1):
                            self.assertLess((derivs(new, side, order, t) - derivs(src, side, order, t)).Length, 1e-9, (side, k, order))
                    if k + 1 <= 2 and k + 1 <= deg:
                        # the first unlocked row moves the next order
                        first = {"U0": (k + 2, 1), "U1": (nu - k - 1, 1), "V0": (1, k + 2), "V1": (1, nv - k - 1)}[side]
                        rows = {(i, j): V(0, 0, 1.0) for (i, j) in [(first[0], j) for j in range(1, nv + 1)]} if side[0] == "U" else {(i, first[1]): V(0, 0, 1.0) for i in range(1, nu + 1)}
                        new = PE.edit_poles(face, rows, locks)["surface"]
                        gap = max((derivs(new, side, k + 1, t) - derivs(src, side, k + 1, t)).Length for t in (0.2, 0.5, 0.8))
                        self.assertGreater(gap, 1e-3, (side, k))


def source_object(doc, shape=None, name="Src"):
    o = doc.addObject("Part::Feature", name)
    o.Shape = shape if shape is not None else cubic_face(6, 8)
    return o


def edited(face, moves, locks=None):
    return PE.edit_poles(face, moves, locks)["surface"]


class TestPoleEditFeature(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("PoleEditFeature")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def make(self, src, moves=None, locks=None, sub="Face1"):
        moves = moves if moves is not None else {(3, 4): V(0.2, -0.3, 0.8), (5, 7): V(0, 0.5, -0.4)}
        feat = PE.makePoleEditFeature(self.doc, (src, sub), moves, locks)
        self.doc.recompute()
        return feat

    def test_properties_and_equality_with_edit_poles(self):
        src = source_object(self.doc)
        moves = {(3, 4): V(0.2, -0.3, 0.8), (5, 7): V(0, 0.5, -0.4)}
        feat = self.make(src, moves, {"U0": 1, "V1": 0})
        self.assertTrue(feat.isValid(), feat.getStatusString())
        for name in ("Source", "PoleIndices", "PoleOffsets", "LockU0", "LockU1", "LockV0", "LockV1", "GridSignature", "MovedPoles", "MaxDeviation"):
            self.assertIn(name, feat.PropertiesList)
        self.assertEqual(list(feat.PoleIndices), [3, 4, 5, 7])
        self.assertEqual((feat.LockU0, feat.LockU1, feat.LockV0, feat.LockV1), ("G1", "Free", "Free", "G0"))
        self.assertEqual(feat.MovedPoles, 2)
        self.assertEqual(len(feat.Shape.Faces), 1)
        ref = PE.edit_poles(src.Shape.Faces[0], moves, {"U0": 1, "V1": 0})
        self.assertTrue(same_surface(feat.Shape.Faces[0].Surface, ref["surface"]))
        self.assertAlmostEqual(feat.MaxDeviation, ref["max_deviation"], places=9)
        self.assertIn(src, feat.OutList)
        self.assertIn("ReadOnly", feat.getPropertyStatus("MovedPoles"))
        self.assertIn("Hidden", feat.getPropertyStatus("GridSignature"))
        self.assertIn("ReadOnly", feat.getPropertyStatus("GridSignature"))

    def test_the_feature_follows_an_edit_of_the_source(self):
        src = source_object(self.doc)
        feat = self.make(src)
        first = feat.Shape.Faces[0].Surface
        s2 = PE.editable_surface(src.Shape.Faces[0])
        s2.setPole(4, 4, s2.getPole(4, 4) + V(0, 0, 3))
        src.Shape = s2.toShape()
        self.doc.recompute()
        self.assertTrue(feat.isValid(), feat.getStatusString())
        self.assertFalse(same_surface(first, feat.Shape.Faces[0].Surface))
        ref = edited(src.Shape.Faces[0], {(3, 4): V(0.2, -0.3, 0.8), (5, 7): V(0, 0.5, -0.4)})
        self.assertTrue(same_surface(feat.Shape.Faces[0].Surface, ref))

    def test_offsets_are_in_the_sources_own_frame(self):
        src = source_object(self.doc)
        feat = self.make(src)
        base = feat.Shape.Faces[0].Surface
        rot = FreeCAD.Placement(V(5, -2, 1), FreeCAD.Rotation(V(1, 2, 3), 40))
        src.Placement = rot
        self.doc.recompute()
        self.assertTrue(feat.isValid(), feat.getStatusString())
        turned = feat.Shape.Faces[0].Surface
        u0, u1, v0, v1 = base.bounds()
        for i in range(9):
            for j in range(9):
                u, v = u0 + (u1 - u0) * i / 8.0, v0 + (v1 - v0) * j / 8.0
                self.assertLess((rot.multVec(base.value(u, v)) - turned.value(u, v)).Length, 1e-9)

    def test_a_source_inside_a_placed_part(self):
        part = self.doc.addObject("App::Part", "Part")
        part.Placement = FreeCAD.Placement(V(3, 4, 5), FreeCAD.Rotation(V(0, 0, 1), 30))
        src = source_object(self.doc)
        part.addObject(src)
        self.doc.recompute()
        feat = PE.makePoleEditFeature(self.doc, (part, "Src.Face1"), {(3, 3): V(0.5, 0, 1)})
        self.doc.recompute()
        self.assertTrue(feat.isValid(), feat.getStatusString())
        placed = Part.getShape(part, "Src.Face1", needSubElement=True)
        local = PE.edit_poles(src.Shape.Faces[0], {(3, 3): V(0.5, 0, 1)})["surface"]
        s = feat.Shape.Faces[0].Surface
        u0, u1, v0, v1 = local.bounds()
        for i in range(7):
            for j in range(7):
                u, v = u0 + (u1 - u0) * i / 6.0, v0 + (v1 - v0) * j / 6.0
                want = part.Placement.multVec(local.value(u, v))
                self.assertLess((s.value(u, v) - want).Length, 1e-9)
        self.assertAlmostEqual(placed.Area, src.Shape.Area, places=9)

    def test_a_lock_that_locks_a_moved_pole_marks_it_invalid_and_keeps_the_shape(self):
        src = source_object(self.doc)
        feat = self.make(src)
        shape = feat.Shape.copy()
        feat.LockU0 = "G2"  # locks rows 1 to 3; the pole (3, 4) moved
        self.doc.recompute()
        self.assertFalse(feat.isValid())
        self.assertIn("LockedPole", feat.getStatusString())
        self.assertTrue(same_surface(feat.Shape.Faces[0].Surface, shape.Faces[0].Surface))
        feat.LockU0 = "Free"
        self.doc.recompute()
        self.assertTrue(feat.isValid(), feat.getStatusString())

    def test_a_changed_pole_grid_is_never_reapplied(self):
        src = source_object(self.doc)
        feat = self.make(src)
        shape = feat.Shape.copy()
        good = src.Shape
        src.Shape = cubic_face(7, 8)
        for _ in range(2):  # the second recompute must still refuse
            feat.touch()
            self.doc.recompute()
            self.assertFalse(feat.isValid())
            self.assertIn("PoleGridChanged", feat.getStatusString())
            self.assertTrue(same_surface(feat.Shape.Faces[0].Surface, shape.Faces[0].Surface))
        src.Shape = good
        self.doc.recompute()
        self.assertTrue(feat.isValid(), feat.getStatusString())
        self.assertTrue(same_surface(feat.Shape.Faces[0].Surface, shape.Faces[0].Surface))

    def test_the_edit_follows_a_blend_and_a_gordon_surface(self):
        a = source_object(self.doc, Part.makePlane(10, 10, V(-5, 0, 0)), "A")
        b = source_object(self.doc, Part.makePlane(10, 10, V(5, 0, 3)), "B")
        ea = [f"Edge{i + 1}" for i, e in enumerate(a.Shape.Edges) if abs(e.BoundBox.XMin - 5) < 1e-9 and abs(e.BoundBox.XMax - 5) < 1e-9][0]
        eb = [f"Edge{i + 1}" for i, e in enumerate(b.Shape.Edges) if abs(e.BoundBox.XMin - 5) < 1e-9 and abs(e.BoundBox.XMax - 5) < 1e-9][0]
        blend = SBS.makeBlendSurfaceFeature(self.doc, (a, ea), (b, eb))
        self.doc.recompute()
        self.assertTrue(blend.isValid(), blend.getStatusString())
        surf = PE.editable_surface(blend.Shape.Faces[0])
        move = {(3, 3): V(0, 0, 0.5)}
        feat = PE.makePoleEditFeature(self.doc, (blend, "Face1"), move)
        self.doc.recompute()
        self.assertTrue(feat.isValid(), feat.getStatusString())
        self.assertTrue(same_surface(feat.Shape.Faces[0].Surface, edited(blend.Shape.Faces[0], move)))
        self.assertGreater(surf.NbUPoles, 2)
        blend.Size1 = 1.4  # same grid (stations do not change), different pole positions
        self.doc.recompute()
        self.assertTrue(feat.isValid(), feat.getStatusString())
        self.assertTrue(same_surface(feat.Shape.Faces[0].Surface, edited(blend.Shape.Faces[0], move)))

    def test_save_and_reopen(self):
        src = source_object(self.doc)
        feat = self.make(src, locks={"V0": 2})
        shape = feat.Shape.copy()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.FCStd")
            self.doc.saveAs(path)
            other = FreeCAD.openDocument(path)
            try:
                back = other.getObject(feat.Name)
                self.assertTrue(same_surface(back.Shape.Faces[0].Surface, shape.Faces[0].Surface))
                other.recompute()
                self.assertTrue(back.isValid(), back.getStatusString())
                self.assertTrue(same_surface(back.Shape.Faces[0].Surface, shape.Faces[0].Surface))
                self.assertEqual(back.LockV0, "G2")
                self.assertEqual(list(back.PoleIndices), [3, 4, 5, 7])
            finally:
                FreeCAD.closeDocument(other.Name)
            self.doc = FreeCAD.newDocument("PoleEditFeature")

    def test_a_deleted_source_fails_with_its_name_and_a_copy_recomputes(self):
        src = source_object(self.doc)
        feat = self.make(src)
        dst = FreeCAD.newDocument("PoleEditCopy")
        try:
            clash = dst.addObject("Part::Feature", src.Name)
            clash.Shape = Part.makeBox(1, 1, 1)
            dst.copyObject(feat, True)
            dst.recompute()
            copy = [o for o in dst.Objects if o.TypeId == feat.TypeId][-1]
            self.assertTrue(copy.isValid(), copy.getStatusString())
            self.assertTrue(same_surface(copy.Shape.Faces[0].Surface, feat.Shape.Faces[0].Surface))
            victim = [o for o in copy.OutList if o.TypeId == "Part::Feature"][0]
            label = victim.Label
            dst.removeObject(victim.Name)
            dst.recompute()
            self.assertFalse(copy.isValid())
            self.assertIn("InputDeleted: %s " % label, copy.getStatusString())
        finally:
            FreeCAD.closeDocument(dst.Name)
        label = src.Label
        self.doc.removeObject(src.Name)
        self.doc.recompute()
        self.assertFalse(feat.isValid())
        self.assertIn("InputDeleted: %s " % label, feat.getStatusString())

    def test_a_trimmed_source_marks_the_feature_invalid(self):
        box = Part.makeBox(10, 10, 10).cut(Part.makeCylinder(2, 20, V(5, 5, -5)))
        src = source_object(self.doc, box)
        holed = [i + 1 for i, f in enumerate(box.Faces) if len(f.Wires) == 2][0]
        feat = PE.makePoleEditFeature(self.doc, (src, "Face%d" % holed), {})
        self.doc.recompute()
        self.assertFalse(feat.isValid())
        self.assertIn("TrimmedFace", feat.getStatusString())

    def test_bad_moves_are_refused_when_the_feature_is_made(self):
        src = source_object(self.doc)
        count = len(self.doc.Objects)
        with self.assertRaises(ValueError):
            PE.makePoleEditFeature(self.doc, (src, "Face1"), {(9, 9): V(1, 0, 0)})
        with self.assertRaises(ValueError):
            PE.makePoleEditFeature(self.doc, (src, "Face1"), {(1, 1): V(1, 0, 0)}, {"U0": 0})
        self.assertEqual(len(self.doc.Objects), count)
