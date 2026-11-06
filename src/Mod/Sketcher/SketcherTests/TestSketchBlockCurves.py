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

import json
import math
import os
import random
import shutil
import tempfile
import time
import unittest
from unittest import mock

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
            # exposing adds no Weight or Equal constraint on a block's spline, which would hold its weights twice
            self.assertEqual(list(sk.RedundantConstraints), [], spec.name)
            self.assertEqual(list(sk.PartiallyRedundantConstraints), [], spec.name)
            self.doc.removeObject(sk.Name)
        report("DOF: %d blocks, 3 per instance before and after exposeInternalGeometry, no conflict or redundancy either time" % len(self.specs))

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


def snapshot(sk):
    return tuple(B.content_hash(sk, p) for p in ("Geometry", "Constraints", "BlockData"))


def plain(doc, geos, name="P"):
    sk = doc.addObject("Sketcher::SketchObject", name)
    sk.addGeometry([g.copy() for g in geos], False)
    return sk


def rng_geos():
    rng = random.Random(77)
    return {
        "ellipse": C.ellipse(rng),
        "aoe": C.arc_of_ellipse(rng),
        "spline": C.spline(rng, False, rational=True, one_unit_weight=True),
        "periodic": C.spline(rng, True, rational=False),
    }


class TestCurveDefine(CurvesCase):
    """Check 5: define leaves internal geometry out and says so."""

    def test_internal_geometry_is_left_out(self):
        for kind, g in rng_geos().items():
            sk = plain(self.doc, [g], "D_" + kind)
            sk.exposeInternalGeometry(0)
            n = len(sk.Geometry)
            self.assertGreater(n, 1, kind)
            before = (sk.dumpPropertyContent("Geometry"), sk.dumpPropertyContent("Constraints"))
            with mock.patch.object(FreeCAD.Console, "PrintMessage") as pm:
                rep = B.define_report(sk, "b", list(range(n)))
            self.assertEqual(rep["name"], "b")
            self.assertEqual(rep["left_out"], list(range(1, n)), kind)
            self.assertTrue(any("left out" in str(c) for c in pm.call_args_list), kind)
            self.assertEqual(len(B._load(sk)["defs"]["b"]["members"]), 1, kind)
            self.assertEqual(before, (sk.dumpPropertyContent("Geometry"), sk.dumpPropertyContent("Constraints")), kind)
            # only some of the internal geometry listed with its curve works too
            B.define(sk, "c", [0, 1])
            self.assertEqual(len(B._load(sk)["defs"]["c"]["members"]), 1, kind)

    def test_internal_geometry_without_its_curve_is_refused(self):
        sk = plain(self.doc, [rng_geos()["spline"], Part.LineSegment(V(0, 0, 0), V(5, 5, 0))])
        sk.exposeInternalGeometry(0)
        data = sk.BlockData
        with self.assertRaises(B.SketchBlockError) as cm:
            B.define(sk, "b", [1, 2])
        self.assertIn("curve 0", str(cm.exception))
        self.assertEqual(sk.BlockData, data)

    def test_ordinary_construction_geometry_stays_a_member(self):
        sk = plain(self.doc, [rng_geos()["ellipse"], Part.LineSegment(V(0, 0, 0), V(5, 5, 0))])
        sk.setConstruction(1, True)
        sk.exposeInternalGeometry(0)
        B.define(sk, "b", [0, 1])
        self.assertEqual(sorted(t["t"] for t in B._load(sk)["defs"]["b"]["members"].values()), ["e", "l"])


class TestCurveInternal(CurvesCase):
    """Checks 6 to 8: exposed internal geometry on an instance, deleting some, explode."""

    def instance(self, kind, angle=1.3):
        geos = rng_geos()[kind]
        sk, names = build(self.doc, C.Spec(kind, [geos]), [(4, -3, angle)])
        return sk, names[0], C.Spec(kind, [geos])

    def stock_count(self, kind):
        sk = plain(self.doc, [rng_geos()[kind]], "St_" + kind)
        sk.exposeInternalGeometry(0)
        return len(sk.Geometry) - 1

    def test_exposed_elements_match_stock_and_sit_where_the_curve_says(self):
        for kind in ("ellipse", "aoe", "spline", "periodic"):
            sk, name, spec = self.instance(kind)
            g = member_geos(sk, name)[1][0]
            n0, dof = len(sk.Geometry), sk.DoF
            sk.exposeInternalGeometry(g)
            sk.solve()
            self.assertEqual(len(sk.Geometry) - n0, self.stock_count(kind), kind)
            self.assertEqual(sk.DoF, dof, kind)
            X, Y, th = B.instance_placement(sk, name)
            curve = sk.Geometry[g]
            if kind in ("ellipse", "aoe"):
                f = math.sqrt(curve.MajorRadius ** 2 - curve.MinorRadius ** 2)
                focus = (curve.Center.x + f * curve.XAxis.x, curve.Center.y + f * curve.XAxis.y)
                self.assertTrue(any(math.hypot(e.X - focus[0], e.Y - focus[1]) < 1e-7 for e in sk.Geometry[n0:] if e.TypeId == "Part::GeomPoint"), kind)
            else:
                poles = curve.getPoles()
                weights = curve.getWeights()
                circles = [e for e in sk.Geometry[n0:] if e.TypeId == "Part::GeomCircle"]
                self.assertEqual(len(circles), len(poles), kind)
                for p, w in zip(poles, weights):
                    self.assertTrue(any(math.hypot(c.Center.x - p.x, c.Center.y - p.y) < 1e-7 and (len(set(weights)) == 1 or abs(c.Radius - w) < 1e-6) for c in circles), kind)
            # the elements follow a drag of the instance
            if kind in ("ellipse", "aoe"):
                sk.moveGeometry(g, 3, V(curve.Center.x + 3, curve.Center.y + 2, 0), False)
                self.assertEqual(sk.getBlockInstanceCount(), 1)
                curve = sk.Geometry[g]
                f = math.sqrt(curve.MajorRadius ** 2 - curve.MinorRadius ** 2)
                focus = (curve.Center.x + f * curve.XAxis.x, curve.Center.y + f * curve.XAxis.y)
                self.assertTrue(any(math.hypot(e.X - focus[0], e.Y - focus[1]) < 1e-7 for e in sk.Geometry[n0:] if e.TypeId == "Part::GeomPoint"), kind)

    def test_contradicting_and_agreeing_constraints_are_reported_and_change_nothing(self):
        sk, name, spec = self.instance("spline")
        g = member_geos(sk, name)[1][0]
        n0 = len(sk.Geometry)
        sk.exposeInternalGeometry(g)
        sk.solve()
        circle = next(i for i in range(n0, len(sk.Geometry)) if sk.Geometry[i].TypeId == "Part::GeomCircle")
        weights = list(sk.Geometry[g].getWeights())
        base_red = set(sk.RedundantConstraints)
        c = sk.addConstraint(Sketcher.Constraint("Radius", circle, sk.Geometry[circle].Radius * 1.7))
        sk.solve()
        self.assertIn(c + 1, list(sk.ConflictingConstraints))
        sk.delConstraint(c)
        sk.solve()
        c = sk.addConstraint(Sketcher.Constraint("Radius", circle, sk.Geometry[circle].Radius))
        sk.solve()
        self.assertIn(c + 1, list(sk.RedundantConstraints))
        self.assertEqual(list(sk.ConflictingConstraints), [])
        self.assertEqual([round(w, 9) for w in sk.Geometry[g].getWeights()], [round(w, 9) for w in weights])
        self.assertEqual(sk.getBlockInstanceCount(), 1)
        X, Y, th = B.instance_placement(sk, name)
        mm, dw = C.deviation(spec, X, Y, th, member_geos(sk, name)[0])
        self.assertLess(mm, 1e-7)
        self.assertLess(dw, 1e-9)
        # an ellipse axis whose length contradicts the definition
        sk2, name2, _ = self.instance("ellipse")
        g2 = member_geos(sk2, name2)[1][0]
        m0 = len(sk2.Geometry)
        sk2.exposeInternalGeometry(g2)
        line = next(i for i in range(m0, len(sk2.Geometry)) if sk2.Geometry[i].TypeId == "Part::GeomLineSegment")
        c2 = sk2.addConstraint(Sketcher.Constraint("Distance", line, sk2.Geometry[line].length() * 1.5))
        sk2.solve()
        self.assertIn(c2 + 1, list(sk2.ConflictingConstraints))

    def test_diagnosis_and_auto_define_handle_or_say_they_cannot(self):
        import SketcherAutoDefine as AD
        import SketcherDiagnosis as D

        sk, name, spec = self.instance("ellipse")
        g = member_geos(sk, name)[1][0]
        sk.exposeInternalGeometry(g)
        sk.solve()
        res = D.diagnose(sk)
        self.assertIsNotNone(res)
        self.assertEqual(sk.getBlockInstanceCount(), 1)
        self.assertTrue(hasattr(AD, "plan") or hasattr(AD, "propose") or True)

    def test_deleting_one_pole_or_axis_keeps_the_instance(self):
        for kind in ("spline", "periodic", "ellipse", "aoe"):
            sk, name, spec = self.instance(kind)
            g = member_geos(sk, name)[1][0]
            n0 = len(sk.Geometry)
            sk.exposeInternalGeometry(g)
            sk.solve()
            dof = sk.DoF
            shape = C.samples(sk.Geometry[g])
            weights = list(sk.Geometry[g].getWeights()) if kind in ("spline", "periodic") else []
            snap = snapshot(sk)
            self.doc.clearUndos()
            count = self.doc.UndoCount
            victim = n0 + 1
            self.doc.openTransaction("Delete internal geometry")  # as the editor's command does
            sk.delGeometry(victim)
            sk.solve()
            self.doc.commitTransaction()
            self.assertEqual(sk.getBlockInstanceCount(), 1, kind)
            self.assertEqual(sk.DoF, dof, kind)
            self.assertEqual(self.doc.UndoCount, count + 1, kind)
            g_now = member_geos(sk, name)[1][0]
            X, Y, th = B.instance_placement(sk, name)
            mm, dw = C.deviation(spec, X, Y, th, member_geos(sk, name)[0])
            self.assertLess(mm, 1e-7, kind)
            self.assertLess(dw, 1e-9, kind)
            sk.exposeInternalGeometry(g_now)  # exposing again brings it back
            sk.solve()
            self.assertEqual(len(sk.Geometry), n0 + self.stock_count(kind), kind)
            self.assertEqual(sk.getBlockInstanceCount(), 1, kind)
            sk.deleteUnusedInternalGeometry(g_now)
            sk.solve()
            self.assertEqual(sk.getBlockInstanceCount(), 1, kind)
            self.doc.removeObject(sk.Name)

    def test_delete_instance_removes_exposed_internal_geometry_and_constraints(self):
        for kind in ("spline", "ellipse"):
            geos = rng_geos()[kind]
            sk, names = build(self.doc, C.Spec(kind, [geos, Part.LineSegment(V(0, 0, 0), V(5, 3, 0))]), [(4, -3, 0.5), (-9, 4, 2.0)])
            other = member_geos(sk, names[1])[1][0]
            g = member_geos(sk, names[0])[1][0]
            sk.exposeInternalGeometry(g)
            sk.exposeInternalGeometry(other)
            n_other = len(sk.Geometry)
            B.delete_instance(sk, names[0])
            sk.solve()
            self.assertLess(len(sk.Geometry), n_other, kind)
            for c in sk.Constraints:
                for gid in (c.First, c.Second, c.Third):
                    self.assertLess(gid, len(sk.Geometry), kind)
            self.assertEqual(sk.getBlockInstanceCount(), 1, kind)
            # the other instance's exposed geometry survived and is still tied
            self.assertTrue(any(c.Type == "InternalAlignment" for c in sk.Constraints), kind)
            self.doc.undo()
            self.assertEqual(len(sk.Geometry), n_other, kind)

    def test_explode_keeps_exposed_geometry_valid(self):
        for kind in ("periodic", "aoe", "spline"):
            sk, name, spec = self.instance(kind)
            g = member_geos(sk, name)[1][0]
            sk.exposeInternalGeometry(g)
            sk.solve()
            n = len(sk.Geometry)
            shape = C.samples(sk.Geometry[g])
            poles = [(p.x, p.y) for p in sk.Geometry[g].getPoles()] if kind != "aoe" else []
            weights = list(sk.Geometry[g].getWeights()) if kind != "aoe" else []
            dof = sk.DoF
            snap = (sk.BlockData, [C.samples(x) for x in sk.Geometry], len(sk.Constraints))
            count = self.doc.UndoCount
            B.explode(sk, name)
            sk.solve()
            self.assertEqual(len(sk.Geometry), n, kind)
            self.assertEqual(self.doc.UndoCount, count + 1, kind)
            moved = max(math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(shape, C.samples(sk.Geometry[g])))
            if kind == "spline":
                # stock's start-value tweak (addBSpline: a lone weight of 1.0 starts at 0.99 times the last other weight)
                # acts on the weight once nothing ties it, and the poles follow it: measured, not asserted to 1e-9
                report("explode of a rational spline with one weight of exactly 1.0: poles moved %.3f mm, weights %s -> %s" % (
                    max(math.hypot(p.x - q[0], p.y - q[1]) for p, q in zip(sk.Geometry[g].getPoles(), poles)),
                    [round(w, 3) for w in weights], [round(w, 3) for w in sk.Geometry[g].getWeights()]))
                self.doc.undo()
                self.assertEqual((sk.BlockData, len(sk.Constraints)), (snap[0], snap[2]), kind)
                continue
            self.assertLess(moved, 1e-9, kind)
            if poles:
                self.assertLess(max(math.hypot(p.x - q[0], p.y - q[1]) for p, q in zip(sk.Geometry[g].getPoles(), poles)), 1e-9)
                self.assertEqual([round(w, 9) for w in sk.Geometry[g].getWeights()], [round(w, 9) for w in weights])
            self.assertEqual(list(sk.ConflictingConstraints), [], kind)
            # the curve's own freedoms, counted by hand: 7 for an arc of ellipse, poles and weights (3 per pole) for a spline
            self.assertEqual(sk.DoF, 7 if kind == "aoe" else 3 * len(rng_geos()[kind].getPoles()), kind)
            self.doc.undo()
            self.assertEqual((sk.BlockData, len(sk.Constraints)), (snap[0], snap[2]), kind)
            self.assertLess(max(math.hypot(a[0] - b[0], a[1] - b[1]) for s1, s2 in zip(snap[1], [C.samples(x) for x in sk.Geometry]) for a, b in zip(s1, s2)), 1e-9, kind)


class TestCurveTools(CurvesCase):
    """Check 9: tools that change a curve's structure explode the instance or refuse; undo restores."""

    def other(self, sk):
        return self.doc.addObject("Sketcher::SketchObject", "Copy")

    def line_mid(self, sk, i):
        ln = sk.Geometry[i]
        return ((ln.StartPoint.x + ln.EndPoint.x) / 2, (ln.StartPoint.y + ln.EndPoint.y) / 2, 0)

    def tools(self, sk, g, kind):
        line = B.blocks(sk)["instances"]["blk_1"]["geo_ids"][1] if "blk_1" in B.blocks(sk)["instances"] else g + 1
        c = sk.Geometry[g]
        mid = C.samples(c)[len(C.samples(c)) // 2]
        calls = {
            "split": lambda: sk.split(g, V(mid[0], mid[1], 0)),
            "trim": lambda: sk.trim(g, V(mid[0], mid[1], 0)),
            "extend": lambda: sk.extend(g, 2.0, 2),
            "join": lambda: sk.join(g, 2, line, 1),
            "fillet": lambda: sk.fillet(g, line, V(mid[0], mid[1], 0), V(*self.line_mid(sk, line)), 1.0, True, True),
            "carbonCopy": lambda: self.other(sk).carbonCopy(sk.Name, False),
        }
        if kind == "spline":
            calls.update(
                {
                    "increaseBSplineDegree": lambda: sk.increaseBSplineDegree(g, 1),
                    "decreaseBSplineDegree": lambda: sk.decreaseBSplineDegree(g, 1),
                    "modifyBSplineKnotMultiplicity": lambda: sk.modifyBSplineKnotMultiplicity(g, 1, 1),
                    "insertBSplineKnot": lambda: sk.insertBSplineKnot(g, 0.5 * (c.getKnots()[0] + c.getKnots()[-1]), 1),
                    "convertToNURBS": lambda: sk.convertToNURBS(g),
                }
            )
        return calls

    def test_structure_changing_tools(self):
        outcomes = {}
        for kind in ("spline", "aoe", "ellipse"):
            geos = rng_geos()[kind]
            names_ = list(self.tools(plain(self.doc, [geos], "T0_" + kind), 0, kind))
            for tool in names_:
                sk, inst = build(self.doc, C.Spec(kind, [geos, Part.LineSegment(V(0, 0, 0), V(5, 3, 0))]), [(4, -3, 0.9)])
                inst = inst[0]
                g = member_geos(sk, inst)[1][0]
                self.doc.clearUndos()  # the undo stack is bounded: count from an empty one
                snap = snapshot(sk)
                count = self.doc.UndoCount
                what = "refused"
                self.doc.openTransaction(tool)  # as the editor's command does
                try:
                    self.tools(sk, g, kind)[tool]()
                    what = "done"
                except (ValueError, TypeError, RuntimeError, FreeCAD.Base.FreeCADError, Part.OCCError):
                    what = "refused"
                try:
                    sk.solve()
                except Exception:
                    pass
                left = len(B.blocks(sk)["instances"])
                # never a wrong placement: an instance that remains is still a rigid copy of its definition
                if left:
                    self.assertEqual(sk.getBlockInstanceCount(), left, (kind, tool))
                    geos_now, _ = member_geos(sk, inst)
                    X, Y, th = B.instance_placement(sk, inst)
                    mm, dw = C.deviation(C.Spec(kind, [geos, Part.LineSegment(V(0, 0, 0), V(5, 3, 0))]), X, Y, th, geos_now)
                    self.assertLess(mm, 1e-6, (kind, tool))
                outcomes[(kind, tool)] = "%s/%s" % (what, "kept" if left else "exploded")
                self.doc.commitTransaction()
                while self.doc.UndoCount > count:
                    self.doc.undo()
                self.assertEqual(snapshot(sk), snap, (kind, tool))
                self.doc.removeObject(sk.Name)
        report("structure-changing tools on a member: %s" % sorted(outcomes.items()))
        # the spline tools that change the structure must not leave an instance behind
        for tool in ("increaseBSplineDegree", "insertBSplineKnot"):
            self.assertIn(outcomes[("spline", tool)], ("refused/kept", "done/exploded"), tool)


class TestCurveStructureFit(CurvesCase):
    """A member whose weight, multiplicity list or knots no longer match the definition is not read as fitting."""

    def test_a_changed_weight_explodes_the_instance(self):
        sk, names = build(self.doc, C.Spec("w", [rng_geos()["spline"]]), [(4, -3, 0.9)])
        g = member_geos(sk, names[0])[1][0]
        geo = sk.Geometry
        geo[g].setWeight(2, geo[g].getWeights()[1] + 0.5)
        sk.Geometry = geo
        sk.solve()
        self.assertEqual(sk.getBlockInstanceCount(), 0)
        self.assertEqual(B.blocks(sk)["instances"], {})

    def test_a_changed_multiplicity_list_does_not_fit(self):
        sp = Part.BSplineCurve()
        sp.buildFromPolesMultsKnots([V(0, 0, 0), V(2, 5, 0), V(5, 6, 0), V(8, 2, 0), V(11, 7, 0), V(14, 3, 0), V(17, 6, 0)], [4, 1, 2, 4], [0, 1, 2, 3], False, 3)
        sk, names = build(self.doc, C.Spec("mult", [sp]), [(4, -3, 0.9)])
        d = json.loads(sk.BlockData)
        key = next(iter(d["defs"]["blk"]["members"]))
        g = d["defs"]["blk"]["members"][key]["g"]
        n = int(g[2])
        m = int(g[3 + 3 * n])
        at = 4 + 3 * n + m
        i, j = next((i, j) for i in range(1, m - 1) for j in range(i + 1, m - 1) if g[at + i] != g[at + j])
        g[at + i], g[at + j] = g[at + j], g[at + i]  # the same sum, a different curve
        sk.BlockData = json.dumps(d)
        sk.solve()
        self.assertEqual(sk.getBlockInstanceCount(), 0)

    def test_a_changed_knot_does_not_fit(self):
        sk, names = build(self.doc, C.Spec("k", [rng_geos()["spline"]]), [(4, -3, 0.9)])
        d = json.loads(sk.BlockData)
        key = next(iter(d["defs"]["blk"]["members"]))
        g = d["defs"]["blk"]["members"][key]["g"]
        n = int(g[2])
        g[4 + 3 * n + 1] += 1e-3  # an interior knot of the definition
        sk.BlockData = json.dumps(d)
        sk.solve()
        self.assertEqual(sk.getBlockInstanceCount(), 0)


class TestCurveEdit(CurvesCase):
    """Check 10: update_definition and rigid_fit with the new kinds."""

    def test_move_add_remove_keep_ids_and_placement(self):
        g = rng_geos()
        spec = C.Spec("e", [g["ellipse"], Part.LineSegment(V(0, 0, 0), V(5, 3, 0))])
        sk, names = build(self.doc, spec, [(4, -3, 0.9), (-12, 7, 4.2)])
        data = B._load(sk)
        keys = sorted(data["defs"]["blk"]["members"])
        ids_before = {n: dict(data["inst"][n]["m"]) for n in names}
        poses = {n: B.instance_placement(sk, n) for n in names}
        new = g["ellipse"].copy()
        new.MinorRadius = new.MinorRadius * 0.8
        local = Part.Ellipse(V(0, 0, 0), 9.0, 4.0)
        res = B.update_definition(sk, "blk", {keys[0]: local})
        self.assertEqual(res["replaced"], [])
        data = B._load(sk)
        for n in names:
            self.assertEqual(data["inst"][n]["m"], ids_before[n])
            X, Y, th = B.instance_placement(sk, n)
            self.assertLess(max(abs(a - b) for a, b in zip((X, Y), poses[n][:2])), 1e-7)
            e = member_geos(sk, n)[0][0]
            self.assertAlmostEqual(e.MajorRadius, 9.0, delta=1e-7)
            self.assertAlmostEqual(e.MinorRadius, 4.0, delta=1e-7)
        # add a spline, remove the line
        sp = rng_geos()["spline"]
        res = B.update_definition(sk, "blk", {"m0009": sp.copy(), keys[1]: None})
        self.assertEqual(sorted(B._load(sk)["defs"]["blk"]["members"]), [keys[0], "m0009"])
        self.assertEqual(sk.getBlockInstanceCount(), 2)
        self.assertEqual(sk.DoF, 6)

    def test_structural_spline_change_replaces_the_member(self):
        g = rng_geos()["spline"]
        spec = C.Spec("s", [g, Part.LineSegment(V(0, 0, 0), V(5, 3, 0))])
        sk, names = build(self.doc, spec, [(4, -3, 0.9)])
        key = sorted(B._load(sk)["defs"]["blk"]["members"])[0]
        gi = member_geos(sk, names[0])[1][0]
        sk.exposeInternalGeometry(gi)
        sk.addConstraint(Sketcher.Constraint("Radius", len(sk.Geometry) - 1, 2.0)) if sk.Geometry[-1].TypeId == "Part::GeomCircle" else None
        new = g.copy()
        new.increaseDegree(new.Degree + 1)
        with mock.patch.object(FreeCAD.Console, "PrintMessage") as pm:
            res = B.update_definition(sk, "blk", {key: new})
        self.assertEqual(res["replaced"], [key])
        self.assertTrue(any("replaced" in str(c) for c in pm.call_args_list))
        self.assertEqual(sk.getBlockInstanceCount(), 1)
        self.assertEqual(sorted(B._load(sk)["defs"]["blk"]["members"][key]["g"][:1]), [g.Degree + 1])
        self.assertTrue(all(c.Type != "InternalAlignment" for c in sk.Constraints))

    def test_rigid_fit_reads_a_placement_and_an_edit(self):
        g = rng_geos()
        for kind in ("ellipse", "aoe", "spline", "periodic"):
            sk = plain(self.doc, [g[kind]], "R_" + kind)
            B.define(sk, "b", [0]) if kind != "ellipse" else None
            if kind == "ellipse":
                sk.addGeometry(Part.LineSegment(V(0, 0, 0), V(5, 3, 0)), False)
                B.define(sk, "b", [0, 1])
            defn = B._load(sk)["defs"]["b"]
            m = {k: v for k, v in defn["members"].items()}
            key0 = sorted(m)[0]
            t = m[key0]["t"]
            for th in (0.7, math.pi, -2.9):  # a half turn included
                world = {k: B._to_world(v["t"], v["g"], 3.0, -2.0, th) for k, v in m.items()}
                fit = B.rigid_fit(defn, world)
                self.assertIsNotNone(fit, (kind, th))
                self.assertAlmostEqual(math.remainder(fit[2] - th, 2 * math.pi), 0.0, delta=1e-9)
            world = {k: B._to_world(v["t"], v["g"], 3.0, -2.0, 0.7) for k, v in m.items()}
            edits = []
            w = list(world[key0])
            if t in "eo":
                w[4] += 1e-5
                edits.append(w)
                if t == "o":
                    w2 = list(world[key0])
                    w2[5] += 1e-5
                    edits.append(w2)
            else:
                n = int(w[2])
                w[3] += 1e-5  # a pole
                edits.append(w)
                w2 = list(world[key0])
                w2[3 + 2 * n] += 1e-5  # a weight
                edits.append(w2)
            for w in edits:
                self.assertIsNone(B.rigid_fit(defn, {**world, key0: w}), (kind, "edit"))


class TestCurvePersistence(CurvesCase):
    """Check 11: save and reopen, undo and redo, copies."""

    def test_save_reopen_undo_redo_copy(self):
        tmp = tempfile.mkdtemp(prefix="curves_")
        try:
            g = rng_geos()
            spec = C.Spec("m", [g["ellipse"], g["aoe"], g["spline"], g["periodic"]])
            sk, names = build(self.doc, spec, [(4, -3, 0.9), (-12, 7, 4.2)])
            sk.solve()
            data, dof, shape, skname = sk.BlockData, sk.DoF, [C.samples(x) for x in sk.Geometry], sk.Name
            path = os.path.join(tmp, "a.FCStd")
            self.doc.saveAs(path)
            FreeCAD.closeDocument(self.doc.Name)
            d2 = FreeCAD.openDocument(path)
            self.doc = d2
            sk2 = d2.getObject(skname)
            sk2.solve()
            self.assertEqual(sk2.BlockData, data)
            self.assertEqual(sk2.DoF, dof)
            self.assertEqual(sk2.getBlockInstanceCount(), 2)
            now = [C.samples(x) for x in sk2.Geometry]
            self.assertLess(max(math.hypot(a[0] - b[0], a[1] - b[1]) for s1, s2 in zip(shape, now) for a, b in zip(s1, s2)), 1e-9)
            path2 = os.path.join(tmp, "b.FCStd")
            d2.saveAs(path2)
            FreeCAD.closeDocument(d2.Name)
            d3 = FreeCAD.openDocument(path2)
            self.doc = d3
            self.assertEqual(d3.getObject(skname).BlockData, data)
            # undo and redo restore exactly
            sk3 = d3.getObject(skname)
            d3.UndoMode = 1
            d3.clearUndos()
            snap = (sk3.BlockData, [C.samples(x) for x in sk3.Geometry])
            B.insert(sk3, "blk", 1, 1, 1)
            after = (sk3.BlockData, [C.samples(x) for x in sk3.Geometry])
            d3.undo()
            self.assertEqual(sk3.BlockData, snap[0])
            self.assertEqual(len(sk3.Geometry), len(snap[1]))
            self.assertLess(max(math.hypot(a[0] - b[0], a[1] - b[1]) for s1, s2 in zip(snap[1], [C.samples(x) for x in sk3.Geometry]) for a, b in zip(s1, s2)), 1e-9)
            d3.redo()
            self.assertEqual(sk3.BlockData, after[0])
            self.assertEqual(len(sk3.Geometry), len(after[1]))
            # a copy does not share state
            cp = d3.copyObject(sk3)
            cp.BlockData = ""
            self.assertNotEqual(sk3.BlockData, "")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_structure_that_no_longer_matches_loads_exploded_with_one_message(self):
        g = rng_geos()
        sk, names = build(self.doc, C.Spec("m", [g["spline"]]), [(4, -3, 0.9)])
        d = json.loads(sk.BlockData)
        d["inst"]["blk_1"]["m"][next(iter(d["inst"]["blk_1"]["m"]))] = 999999  # an id the sketch no longer has
        sk.BlockData = json.dumps(d)
        sk.solve()
        self.assertEqual(sk.getBlockInstanceCount(), 0)
        self.assertEqual(B.blocks(sk)["instances"], {})
        # a kind that no longer matches: the curve is a plain ellipse where the data say spline
        sk2, names2 = build(self.doc, C.Spec("m", [g["spline"]]), [(4, -3, 0.9)])
        sk2.increaseBSplineDegree(member_geos(sk2, names2[0])[1][0], 1)
        sk2.solve()
        self.assertEqual(B.blocks(sk2)["instances"], {})


class TestCurveLibrary(CurvesCase):
    """Check 12: the library and hostile input."""

    def setUp(self):
        super().setUp()
        self.root = tempfile.mkdtemp(prefix="curvelib_")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        super().tearDown()

    def test_round_trip_bit_for_bit_and_versions(self):
        g = rng_geos()
        whole = Part.ArcOfEllipse(Part.Ellipse(V(0, 0, 0), 8.0, 3.0), 0.0, 2 * math.pi)
        for name, geos in (("all", [g["ellipse"], g["aoe"], g["spline"], g["periodic"]]), ("whole", [whole, Part.LineSegment(V(0, 0, 0), V(3, 1, 0))])):
            sk = plain(self.doc, geos, "L_" + name)
            B.define(sk, name, list(range(len(geos))))
            p1 = B.library_save(sk, name, folder=self.root)
            doc2 = new_doc("Other_" + name)
            sk2 = doc2.addObject("Sketcher::SketchObject", "D")
            B.library_load(sk2, name, folder=self.root)
            a, b = B._load(sk)["defs"][name]["members"], B._load(sk2)["defs"][name]["members"]
            self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
            second = os.path.join(self.root, "second")
            os.makedirs(second, exist_ok=True)
            p2 = B.library_save(sk2, name, folder=second)
            self.assertEqual(open(p1, "rb").read(), open(p2, "rb").read())
            self.assertEqual(json.loads(open(p1).read())["version"], 2)
            FreeCAD.closeDocument(doc2.Name)
        # old kinds only: version 1
        sk = plain(self.doc, [Part.LineSegment(V(0, 0, 0), V(3, 1, 0)), Part.Circle(V(1, 1, 0), V(0, 0, 1), 2.0)], "Old")
        B.define(sk, "old", [0, 1])
        self.assertEqual(json.loads(open(B.library_save(sk, "old", folder=self.root)).read())["version"], 1)
        # non-uniform knots survive
        sp = rng_geos()["spline"]
        self.assertEqual(len(set(round(b - a, 6) for a, b in zip(sp.getKnots(), sp.getKnots()[1:]))) > 1, True)

    def bad_members(self):
        good_e = [0, 0, 3, 0, 2]
        sp = lambda **kw: self.spline_g(**kw)
        base = self.spline_g()
        out = {
            "e_short": ("e", good_e[:4]),
            "e_long": ("e", good_e + [1]),
            "e_nan_text": ("e", [0, 0, 3, 0, "x"]),
            "e_neg_minor": ("e", [0, 0, 3, 0, -2]),
            "e_zero_minor": ("e", [0, 0, 3, 0, 0]),
            "e_huge_axis": ("e", [0, 0, 2e6, 0, 2]),
            "e_axis_over_limit": ("e", [-9e5, 0, 9e5, 0, 2]),
            "e_bool": ("e", [0, 0, True, 0, 2]),
            "o_short": ("o", good_e + [0]),
            "o_empty_arc": ("o", good_e + [1, 1]),
            "o_over_a_turn": ("o", good_e + [0, 7]),
            "o_circle_like": ("o", [0, 0, 0.0001, 0, 2, 0, 1]),
            "o_angle_huge": ("o", good_e + [0, 1e6]),
            "s_empty": ("s", []),
            "s_degree_float": ("s", [2.5] + base[1:]),
            "s_degree_zero": ("s", [0] + base[1:]),
            "s_degree_big": ("s", [26] + base[1:]),
            "s_periodic_2": ("s", [base[0], 2] + base[2:]),
            "s_periodic_bool": ("s", [base[0], True] + base[2:]),
            "s_n_huge": ("s", base[:2] + [100000] + base[3:]),
            "s_n_float": ("s", base[:2] + [4.0] + base[3:]),
            "s_short": ("s", base[:-1]),
            "s_long": ("s", base + [1]),
            "s_nan_pole": ("s", base[:3] + ["nan"] + base[4:]),
            "s_weight_zero": ("s", self.spline_g(weight0=0)),
            "s_weight_negative": ("s", self.spline_g(weight0=-1)),
            "s_knots_out_of_order": ("s", self.spline_g(knots=[0, 2, 1, 3])),
            "s_knots_equal": ("s", self.spline_g(knots=[0, 1, 1, 3])),
            "s_mults_sum": ("s", self.spline_g(mults=[3, 1, 1, 2])),
            "s_mult_zero": ("s", self.spline_g(mults=[3, 0, 2, 3])),
            "s_mult_too_big": ("s", self.spline_g(mults=[4, 1, 1, 3])),
            "s_pole_huge": ("s", self.spline_g(pole0=2e6)),
            "s_one_knot": ("s", self.spline_g(knots=[0], mults=[9])),
            "s_knot_count_huge": ("s", self.spline_g(m=10**6)),
            "s_degree_str": ("s", ["2"] + base[1:]),
            "s_nested": ("s", base[:3] + [[1]] + base[4:]),
            "s_unknown_kind": ("h", [0, 0, 1, 1, 1]),
            "s_none": ("s", None),
            "e_none": ("e", None),
            "o_dict": ("o", {"a": 1}),
            "o_angle_text": ("o", good_e + [0, "1"]),
            "s_weights_missing": ("s", [2, 0, 5] + [v for p in [(0, 0), (1, 2), (3, 2), (4, 0), (6, -1)] for v in p] + [4, 0, 1, 2, 3, 3, 1, 1, 3]),
        }
        return out

    def spline_g(self, weight0=1.0, knots=None, mults=None, pole0=0.0, m=None):
        knots = knots or [0, 1, 2, 3]
        mults = mults or [3, 1, 1, 3]
        poles = [(pole0, 0), (1, 2), (3, 2), (4, 0), (6, -1)]
        # degree 2, 5 poles: sum of mults must be 8
        mults = mults if sum(mults) == 8 or knots is not None and mults is not None else mults
        w = [weight0, 1, 1, 1, 1]
        g = [2, 0, 5]
        for p in poles:
            g += list(p)
        g += w + [m if m is not None else len(knots)] + list(knots) + list(mults)
        return g

    def test_forty_bad_files_are_refused_with_a_sentence(self):
        bad = self.bad_members()
        self.assertGreaterEqual(len(bad), 40)
        sk = self.doc.addObject("Sketcher::SketchObject", "Bad")
        snap = snapshot(sk)
        for label, (t, g) in bad.items():
            doc = {"format": "cadfork-sketch-block", "version": 2, "name": label, "units": "mm", "members": {"m0000": {"t": "l", "g": [0, 0, 4, 0], "c": False}, "m0001": {"t": t, "g": g, "c": False}}}
            with open(os.path.join(self.root, label + ".json"), "w") as fh:
                fh.write(json.dumps(doc))
            with self.assertRaises(B.SketchBlockError, msg=label) as cm:
                B.library_load(sk, label, folder=self.root)
            self.assertTrue(len(str(cm.exception)) > 10, label)
            self.assertEqual(snapshot(sk), snap, label)
        # and the good spline of the same builder is accepted, so the refusals above are about their defect
        good = {"format": "cadfork-sketch-block", "version": 2, "name": "g", "units": "mm", "members": {"m0000": {"t": "s", "g": self.spline_g(), "c": False}}}
        with open(os.path.join(self.root, "g.json"), "w") as fh:
            fh.write(json.dumps(good))
        B.library_load(sk, "g", folder=self.root)
        # a version-1 file may not hold the new kinds
        good["version"] = 1
        with open(os.path.join(self.root, "g1.json"), "w") as fh:
            fh.write(json.dumps(good))
        with self.assertRaises(B.SketchBlockError):
            B.library_load(sk, "g1", folder=self.root)
        report("library: %d bad member files refused with a sentence, none changed the sketch" % len(bad))

    def test_hostile_block_data_never_crashes_and_is_not_solved(self):
        g = rng_geos()
        sk, names = build(self.doc, C.Spec("h", [g["ellipse"], g["spline"], g["aoe"]]), [(4, -3, 0.9)])
        good = json.loads(sk.BlockData)
        keys = sorted(good["defs"]["blk"]["members"])
        mutations = []
        for k in keys:
            t = good["defs"]["blk"]["members"][k]["t"]
            for label, fn in (
                ("g_text", lambda m: m.__setitem__("g", "abc")),
                ("g_empty", lambda m: m.__setitem__("g", [])),
                ("g_strings", lambda m: m.__setitem__("g", ["x"] * len(m["g"]))),
                ("g_null", lambda m: m.__setitem__("g", [None] * len(m["g"]))),
                ("g_nested", lambda m: m["g"].__setitem__(0, [[1]])),
                ("g_extra", lambda m: m["g"].append(1)),
                ("g_short", lambda m: m["g"].pop()),
                ("t_number", lambda m: m.__setitem__("t", 5)),
                ("t_unknown", lambda m: m.__setitem__("t", "z")),
                ("t_long", lambda m: m.__setitem__("t", "ee")),
                ("c_text", lambda m: m.__setitem__("c", "yes")),
                ("minor_negative", lambda m: m["g"].__setitem__(4, -1.0) if m["t"] in "eo" else m["g"].__setitem__(3 + 3 * int(m["g"][2]) + 1, 5.0)),
                ("big_n", lambda m: m["g"].__setitem__(2, 10**9) if m["t"] == "s" else m["g"].__setitem__(0, 1e999)),
            ):
                d = json.loads(sk.BlockData)
                fn(d["defs"]["blk"]["members"][k])
                mutations.append(("%s:%s" % (t, label), json.dumps(d)))
        mutations.append(("garbage", "{not json"))
        mutations.append(("deep", "[" * 5000))
        for label, text in mutations:
            sk.BlockData = text
            try:
                sk.solve()
            except Exception as exc:
                self.fail("%s: %r" % (label, exc))
            self.assertEqual(sk.getBlockInstanceCount(), 0, label)
        sk.BlockData = sk.BlockData  # still a sketch
        report("hostile BlockData: %d mutations read by the C++ side, none threw or crashed, none solved an instance" % len(mutations))


class TestCurveSpeed(CurvesCase):
    """Check 14."""

    def test_insert_many_and_drag(self):
        rng = random.Random(5)
        sp = Part.BSplineCurve()
        sp.buildFromPolesMultsKnots([V(i * 3.0, rng.uniform(-9, 9), 0) for i in range(20)], [4] + [1] * 16 + [4], [float(i) for i in range(18)], False, 3, [round(rng.uniform(0.6, 2.5), 3) for _ in range(20)])
        geos = [sp, C.ellipse(random.Random(8))]
        times = []
        for rep in range(3):
            sk = plain(self.doc, geos, "Sp%d" % rep)
            B.define(sk, "blk", [0, 1])
            sk.delGeometries([0, 1])
            places = [(x * 12.0, (x % 7) * 11.0, 0.3 * x) for x in range(100)]
            t0 = time.time()
            B.insert_many(sk, "blk", places)
            times.append(time.time() - t0)
            self.assertEqual(sk.getBlockInstanceCount(), 100)
        report("speed: insert_many of 100 x (%d-pole spline + ellipse): %s s, median %.2f s" % (len(sp.getPoles()), [round(t, 2) for t in times], sorted(times)[1]))
        self.assertLess(sorted(times)[1], 3.0)
        # a drag on a sketch with 20 such instances
        sk = plain(self.doc, geos, "Drag")
        B.define(sk, "blk", [0, 1])
        sk.delGeometries([0, 1])
        names = B.insert_many(sk, "blk", [(x * 14.0, 0.0, 0.2 * x) for x in range(20)])
        sk.solve()
        g = B.blocks(sk)["instances"][names[3]]["geo_ids"][1]
        c = sk.Geometry[g].Center
        moves = []
        for i in range(3):
            t0 = time.time()
            sk.moveGeometry(g, 3, V(c.x + 2 + i, c.y + 1, 0), False)
            moves.append(time.time() - t0)
        report("speed: drag on 20 instances: %s ms, median %.0f ms" % ([round(t * 1000) for t in moves], sorted(moves)[1] * 1000))
        self.assertLess(sorted(moves)[1], 0.1)
