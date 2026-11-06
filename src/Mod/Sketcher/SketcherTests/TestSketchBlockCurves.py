# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2026 The FreeCAD project association AISBL              *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful, but        *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU     *
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *

"""Tests for ellipses, arcs of ellipse and B-splines as sketch-block members (G10 slice 5b2, App level, no GUI).

Check numbers refer to the slice 5b2 acceptance checks in briefs/phase1/G10-sketcher.md.  Measured figures are printed
with the prefix ``5b2:`` so a run's log carries them."""

import math
import random
import time
import unittest

import FreeCAD
import Part
import Sketcher

import SketcherBlocks as B
from SketcherTests import SketchBlockCurvesCorpus as C

V = FreeCAD.Vector
MM = 1e-7
WEIGHT = 1e-9
CURVES = ("Part::GeomEllipse", "Part::GeomArcOfEllipse", "Part::GeomBSplineCurve")


def report(text):
    print("5b2: " + text, flush=True)


def new_doc(name):
    doc = FreeCAD.newDocument(name, name, True, True)
    doc.UndoMode = 1
    return doc


def build(doc, spec, placements, name="blk"):
    """A sketch with one instance per placement (x, y, angle); the source geometry is deleted after `define`."""
    sk = doc.addObject("Sketcher::SketchObject", "S_" + spec.name)
    sk.addGeometry([g.copy() for g in spec.geos], False)
    B.define(sk, name, list(range(len(spec.geos))))
    sk.delGeometries(list(range(len(spec.geos))))
    names = [B.insert(sk, name, *p) for p in placements]
    return sk, names


def member_geos(sk, inst):
    ids = B.blocks(sk)["instances"][inst]["geo_ids"]
    geo = sk.Geometry
    return [geo[i] for i in ids], ids


def area(p, q, r):
    return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])


class CurvesCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.specs = C.corpus()

    def setUp(self):
        self.doc = new_doc("Curves_" + type(self).__name__)

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def assertClean(self, sk, msg=""):
        self.assertEqual(list(sk.ConflictingConstraints), [], msg)
        self.assertEqual(list(sk.RedundantConstraints), [], msg)
        self.assertEqual(list(sk.PartiallyRedundantConstraints), [], msg)
        self.assertEqual(list(sk.MalformedConstraints), [], msg)

    def check_rigid(self, sk, spec, name, label):
        geos, _ = member_geos(sk, name)
        X, Y, th = B.instance_placement(sk, name)
        mm, dw = C.deviation(spec, X, Y, th, geos)
        self.assertLess(mm, MM, label)
        self.assertLess(dw, WEIGHT, label)
        origin = C.origin_of(spec.geos[0])
        for src, got in zip(spec.geos, geos):
            s_pts, g_pts = C.samples(src), C.samples(got)
            if len(s_pts) < 3 or src.TypeId == "Part::GeomCircle":
                continue
            i, j, k = 0, len(s_pts) // 3, 2 * len(s_pts) // 3
            a_s = area(s_pts[i], s_pts[j], s_pts[k])
            a_g = area(g_pts[i], g_pts[j], g_pts[k])
            if abs(a_s) > 1e-3:
                self.assertGreater(a_s * a_g, 0.0, "mirrored " + label)
        return mm, dw


class TestCurveDOF(CurvesCase):
    """Check 2: each instance adds exactly 3 DOF, before and after exposeInternalGeometry."""

    def test_corpus_size(self):
        self.assertGreaterEqual(len(self.specs), 40)
        kinds = {g.TypeId for s in self.specs for g in s.geos}
        for t in ("Part::GeomEllipse", "Part::GeomArcOfEllipse", "Part::GeomBSplineCurve", "Part::GeomLineSegment", "Part::GeomCircle", "Part::GeomArcOfCircle"):
            self.assertIn(t, kinds)
        degrees = {g.Degree for s in self.specs for g in s.geos if g.TypeId == "Part::GeomBSplineCurve"}
        self.assertTrue({2, 3, 4, 5} <= degrees, degrees)
        self.assertTrue(any(g.isPeriodic() for s in self.specs for g in s.geos if g.TypeId == "Part::GeomBSplineCurve"))
        self.assertTrue(any(max(g.getMultiplicities()[1:-1] or [1]) > 1 for s in self.specs for g in s.geos if g.TypeId == "Part::GeomBSplineCurve" and not g.isPeriodic()))

    def test_three_dof_per_instance(self):
        rng = random.Random(21)
        redundant_after = {}
        for spec in self.specs:
            n = 1 + rng.randrange(3)
            places = [(rng.uniform(-30, 30), rng.uniform(-30, 30), rng.uniform(-6.5, 6.5)) for _ in range(n)]
            sk, names = build(self.doc, spec, places)
            sk.solve()
            self.assertEqual(sk.DoF, 3 * n, spec.name)
            self.assertClean(sk, spec.name)
            self.assertEqual(sk.getBlockInstanceCount(), n, spec.name)
            dof = sk.DoF
            before = len(sk.Geometry)
            for name in names:
                _, ids = member_geos(sk, name)
                for g in ids:
                    if sk.Geometry[g].TypeId in CURVES:
                        sk.exposeInternalGeometry(g)
            sk.solve()
            self.assertGreater(len(sk.Geometry), before - 1)
            self.assertEqual(sk.DoF, dof, spec.name)
            self.assertEqual(list(sk.ConflictingConstraints), [], spec.name)
            self.assertEqual(sk.getBlockInstanceCount(), n, spec.name)
            has_spline = any(g.TypeId == "Part::GeomBSplineCurve" for g in spec.geos)
            red = list(sk.RedundantConstraints)
            if not has_spline:
                self.assertEqual(red, [], spec.name)
            else:
                # the constraints the stock tool adds to a spline's pole circles (their radii equal the weights, which
                # the block already fixes) are the only ones that may be redundant
                for i in red:
                    self.assertIn(sk.Constraints[i - 1].Type, ("Weight", "Equal"), spec.name)
                redundant_after[spec.name] = len(red)
            self.doc.removeObject(sk.Name)
        report("DOF: %d blocks, 3 per instance before and after exposeInternalGeometry; redundant (stock Weight/Equal on pole circles) after exposing: %s" % (len(self.specs), redundant_after))

    def test_a_lone_nearly_circular_ellipse_is_refused(self):
        sk = self.doc.addObject("Sketcher::SketchObject", "L")
        sk.addGeometry(Part.Ellipse(V(1, 2, 0), 3.0, 3.0), False)
        with self.assertRaises(B.SketchBlockError):
            B.define(sk, "x", [0])
        self.assertEqual(sk.BlockData, "")
        sk.addGeometry(Part.LineSegment(V(0, 0, 0), V(5, 0, 0)), False)
        B.define(sk, "y", [0, 1])  # next to something that tells the rotation it is fine


class TestCurveRigidity(CurvesCase):
    """Check 3: seeded drags of every draggable element, exposed internal geometry included, and datum changes keep
    every curve a rigid copy of the definition."""

    def draggables(self, sk, ids, extra_from):
        out = []
        geo = sk.Geometry
        for g in ids:
            t = geo[g].TypeId
            if t == "Part::GeomEllipse":
                out += [(g, 3), (g, 0)]
            elif t == "Part::GeomArcOfEllipse":
                out += [(g, 3), (g, 1), (g, 2), (g, 0)]
            elif t == "Part::GeomBSplineCurve":
                out += [(g, 1), (g, 2), (g, 0)]
            elif t == "Part::GeomLineSegment":
                out += [(g, 1), (g, 2)]
            elif t == "Part::GeomArcOfCircle":
                out += [(g, 1), (g, 2), (g, 3)]
            elif t == "Part::GeomCircle":
                out += [(g, 3)]
        for g in range(extra_from, len(geo)):
            t = geo[g].TypeId
            if t == "Part::GeomPoint":
                out.append((g, 1))
            elif t == "Part::GeomLineSegment":
                out += [(g, 1), (g, 2)]
            elif t == "Part::GeomCircle":
                out += [(g, 3)]
        return out

    def position(self, sk, g, pos):
        geo = sk.Geometry[g]
        t = geo.TypeId
        if pos == 0:
            s = C.samples(geo)
            return s[len(s) // 2]
        if t == "Part::GeomPoint":
            return geo.X, geo.Y
        if pos == 3:
            return geo.Center.x, geo.Center.y
        p = geo.StartPoint if pos == 1 else geo.EndPoint
        return p.x, p.y

    def test_drags_and_datum_changes(self):
        rng = random.Random(33)
        solves = drags = datums = failed = exploded = 0
        t0 = time.time()
        worst_mm = worst_w = 0.0
        fails = {}
        for spec in self.specs:
            n = 2
            places = [(rng.uniform(-30, 30), rng.uniform(-30, 30), rng.uniform(-6.5, 6.5)) for _ in range(n)]
            sk, names = build(self.doc, spec, places)
            sk.solve()
            for phase in (0,):
                extra_from = len(sk.Geometry)
                if phase == 1:
                    for name in names:
                        for g in member_geos(sk, name)[1]:
                            if sk.Geometry[g].TypeId in CURVES:
                                sk.exposeInternalGeometry(g)
                    sk.solve()
                for _ in range(20):
                    name = rng.choice(names)
                    ids = member_geos(sk, name)[1]
                    el = self.draggables(sk, ids, extra_from if phase else len(sk.Geometry))
                    g, pos = rng.choice(el)
                    cx, cy = self.position(sk, g, pos)
                    target = V(cx + rng.uniform(-8, 8), cy + rng.uniform(-8, 8), 0)
                    try:
                        rc = sk.moveGeometry(g, pos, target, False)
                    except (ValueError, RuntimeError):
                        rc = -1
                    solves += 1
                    drags += 1
                    failed += rc not in (0, None)
                    if rc not in (0, None):
                        key = (sk.Geometry[g].TypeId[10:], pos, phase, rc)
                        fails[key] = fails.get(key, 0) + 1
                    for nm in names:
                        mm, dw = self.check_rigid(sk, spec, nm, "%s phase %d drag" % (spec.name, phase))
                        worst_mm, worst_w = max(worst_mm, mm), max(worst_w, dw)
                    exploded += len(B.blocks(sk)["instances"]) != n
            # datum changes on the first instance's first member
            g0 = member_geos(sk, names[0])[1][0]
            pos = 3 if sk.Geometry[g0].TypeId in ("Part::GeomEllipse", "Part::GeomArcOfEllipse") else 1
            cx = sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, g0, pos, 1.0))
            cy = sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, g0, pos, 1.0))
            self.assertLess(cy, len(sk.Constraints), "%s: %d %d %d" % (spec.name, cx, cy, len(sk.Constraints)))
            for it in range(8):
                try:
                    sk.setDatum(cx, FreeCAD.Units.Quantity("%f mm" % rng.uniform(-40, 40)))
                    sk.setDatum(cy, FreeCAD.Units.Quantity("%f mm" % rng.uniform(-40, 40)))
                except ValueError as exc:
                    raise AssertionError("%s it %d: %s; %d constraints, cx=%d cy=%d" % (spec.name, it, exc, len(sk.Constraints), cx, cy))
                rc = sk.solve()
                solves += 1
                datums += 1
                failed += rc != 0
                for nm in names:
                    mm, dw = self.check_rigid(sk, spec, nm, "%s datum" % spec.name)
                    worst_mm, worst_w = max(worst_mm, mm), max(worst_w, dw)
                exploded += len(B.blocks(sk)["instances"]) != n
            sk.delConstraint(cy)
            sk.delConstraint(cx)
            sk.solve()
            for phase in (1,):
                extra_from = len(sk.Geometry)
                if phase == 1:
                    for name in names:
                        for g in member_geos(sk, name)[1]:
                            if sk.Geometry[g].TypeId in CURVES:
                                sk.exposeInternalGeometry(g)
                    sk.solve()
                for _ in range(20):
                    name = rng.choice(names)
                    ids = member_geos(sk, name)[1]
                    el = self.draggables(sk, ids, extra_from if phase else len(sk.Geometry))
                    g, pos = rng.choice(el)
                    cx, cy = self.position(sk, g, pos)
                    target = V(cx + rng.uniform(-8, 8), cy + rng.uniform(-8, 8), 0)
                    try:
                        rc = sk.moveGeometry(g, pos, target, False)
                    except (ValueError, RuntimeError):
                        rc = -1
                    solves += 1
                    drags += 1
                    failed += rc not in (0, None)
                    if rc not in (0, None):
                        key = (sk.Geometry[g].TypeId[10:], pos, phase, rc)
                        fails[key] = fails.get(key, 0) + 1
                    for nm in names:
                        mm, dw = self.check_rigid(sk, spec, nm, "%s phase %d drag" % (spec.name, phase))
                        worst_mm, worst_w = max(worst_mm, mm), max(worst_w, dw)
                    exploded += len(B.blocks(sk)["instances"]) != n
            self.doc.removeObject(sk.Name)
        report(
            "rigidity: %d solves (%d drags, %d datum changes) in %.0f s; worst curve deviation %.2e mm, worst weight %.2e; %d solves reported a failure %s; %d instances exploded"
            % (solves, drags, datums, time.time() - t0, worst_mm, worst_w, failed, sorted(fails.items()), exploded)
        )
        self.assertGreaterEqual(solves, 2000)
        self.assertEqual(exploded, 0)


class TestCurvePlacement(CurvesCase):
    """Check 4: instances pinned by constraints at known positions and angles land on the analytic poles, foci and
    sampled points, past 180 degrees and past a whole turn."""

    def pin_points(self, sk, spec, ids, X, Y, th):
        """Two distinct points of the instance, each as (GeoId, PointPos, analytic world point): ends of lines and
        splines, centres, and the first focus of an ellipse (exposed for it) when nothing else tells the rotation."""
        origin = C.origin_of(spec.geos[0])
        kp = []
        for i, g in enumerate(spec.geos):
            t = g.TypeId
            if t in ("Part::GeomEllipse", "Part::GeomArcOfEllipse", "Part::GeomArcOfCircle", "Part::GeomCircle"):
                kp.append((ids[i], 3, g.Center.x, g.Center.y))
            elif t == "Part::GeomBSplineCurve":
                p, q = g.StartPoint, g.EndPoint
                kp += [(ids[i], 1, p.x, p.y), (ids[i], 2, q.x, q.y)]
            elif t == "Part::GeomLineSegment":
                kp += [(ids[i], 1, g.StartPoint.x, g.StartPoint.y), (ids[i], 2, g.EndPoint.x, g.EndPoint.y)]
        world = lambda x, y: C.place((x, y), origin, X, Y, th)
        g0, p0, lx0, ly0 = kp[0]
        for g, p, lx, ly in kp[1:]:
            if math.hypot(lx - lx0, ly - ly0) > 3.0:
                return (g0, p0, world(lx0, ly0)), (g, p, world(lx, ly))
        # a lone ellipse or arc of ellipse: its first focus, from the exposed internal geometry
        for i, src in enumerate(spec.geos):
            if src.TypeId in ("Part::GeomEllipse", "Part::GeomArcOfEllipse"):
                f = math.sqrt(max(0.0, src.MajorRadius ** 2 - src.MinorRadius ** 2))
                fx, fy = src.Center.x + f * src.XAxis.x, src.Center.y + f * src.XAxis.y
                want = world(fx, fy)
                sk.exposeInternalGeometry(ids[i])
                pts = [(j, g) for j, g in enumerate(sk.Geometry) if g.TypeId == "Part::GeomPoint" and j >= len(sk.Geometry) - 4]
                j = min(pts, key=lambda jg: math.hypot(jg[1].X - want[0], jg[1].Y - want[1]) + 0.0)[0]
                self.focus_distance = math.hypot(sk.Geometry[j].X - want[0], sk.Geometry[j].Y - want[1])
                return (g0, p0, world(lx0, ly0)), (j, 1, want)
        raise AssertionError("no second point in " + spec.name)

    def test_instances_land_on_analytic_points(self):
        rng = random.Random(41)
        worst = 0.0
        placed = 0
        angles = [3.5, 3.5 + 2 * math.pi, 3.5 + 4 * math.pi, -3.5, -9.9, 6.0, 0.4]
        for spec in self.specs:
            th = angles[placed % len(angles)]
            X, Y = rng.uniform(-30, 30), rng.uniform(-30, 30)
            if len(spec.geos) == 1 and spec.geos[0].TypeId == "Part::GeomBSplineCurve":
                # a spline may start and end at (nearly) one point: a line beside it tells the rotation
                spec = C.Spec(spec.name + "+line", spec.geos + [Part.LineSegment(V(-3, 4, 0), V(6, 9, 0))])
            sk, names = build(self.doc, spec, [(X, Y, th)])
            ids = member_geos(sk, names[0])[1]
            (i0, p0, w0), (i1, p1, w1) = self.pin_points(sk, spec, ids, X, Y, th)
            sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, i0, p0, w0[0]))
            sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, i0, p0, w0[1]))
            if abs(w1[1] - w0[1]) >= abs(w1[0] - w0[0]):
                sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, i1, p1, w1[0]))
            else:
                sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, i1, p1, w1[1]))
            self.assertEqual(sk.solve(), 0, spec.name)
            self.assertEqual(sk.DoF, 0, spec.name)
            self.assertEqual(list(sk.ConflictingConstraints), [], spec.name)
            geos = member_geos(sk, names[0])[0]
            mm, dw = C.deviation(spec, X, Y, th, geos)
            self.assertLess(mm, MM, spec.name)
            self.assertLess(dw, WEIGHT, spec.name)
            # an arc of ellipse's parameters are the definition's whatever the angle
            for src, got in zip(spec.geos, geos):
                if src.TypeId == "Part::GeomArcOfEllipse":
                    for a, b in ((src.FirstParameter, got.FirstParameter), (src.LastParameter, got.LastParameter)):
                        self.assertLess(abs(math.remainder(a - b, 2 * math.pi)), 1e-9, spec.name)
            worst = max(worst, mm)
            placed += 1
            self.doc.removeObject(sk.Name)
        report("placement: %d blocks pinned at angles %s, worst sampled-point deviation %.2e mm" % (placed, [round(a, 2) for a in angles], worst))
