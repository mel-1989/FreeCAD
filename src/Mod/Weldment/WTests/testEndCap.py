# SPDX-License-Identifier: LGPL-2.1-or-later
"""Acceptance check 2 (slice 5b): end caps.

Independent oracle: the cap's volume is the analytic outline area x thickness / |cos a| (a the angle
between the end plane and the square cut), with the outline areas written out here from the section
dimensions (rounded rectangle b h - (4 - pi) r^2, chamfered b h - 2 c^2, circle pi d^2 / 4). The
outline test maps every point of the cap's faces square to the end plane back along the axis onto
the section plane and measures it against the analytic signed distance of a rounded rectangle or a
circle; nothing here calls WeldEndCap's geometry.
"""

import math
import unittest

import FreeCAD
import Part

import WeldEndCap
from WTests import frames, oracle

V = FreeCAD.Vector
worst = {"volume": 0.0, "common": 0.0, "match": 0.0, "within": 0.0}


def sd_rounded_rect(x, y, a, b, r):
    """Signed distance from (x, y) to the rounded rectangle of half sizes a, b and corner radius r."""
    qx, qy = abs(x) - (a - r), abs(y) - (b - r)
    return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r


def sd_outer(prof, x, y):
    d = prof.dims
    if prof.kind == "CHS":
        return math.hypot(x, y) - d["d"] / 2.0
    return sd_rounded_rect(x, y, d["b"] / 2.0, d["h"] / 2.0, d["ro"])


def sd_bore(prof, x, y):
    d = prof.dims
    if prof.kind == "CHS":
        return math.hypot(x, y) - (d["d"] / 2.0 - d["t"])
    return sd_rounded_rect(x, y, d["b"] / 2.0 - d["t"], d["h"] / 2.0 - d["t"], d["ri"])


def analytic_area(prof, mode, c):
    d = prof.dims
    if prof.kind == "CHS":
        dia = d["d"] if mode == "Outset" else d["d"] - 2 * d["t"]
        return math.pi * dia * dia / 4.0
    if mode == "Outset":
        w, h, r = d["b"], d["h"], d["ro"]
    else:
        w, h, r = d["b"] - 2 * d["t"], d["h"] - 2 * d["t"], d["ri"]
    return w * h - 2 * c * c if c else w * h - (4 - math.pi) * r * r


def _mitre_frame(doc, prof, theta_deg):
    """Member A ends at a mitre against B; returns (frame, outward normal of A's end, cos a)."""
    fr = frames.Frame(doc)
    th = math.radians(theta_deg)
    fr.add("A", prof.family, prof.size, [((0, 0, 0), (1000, 0, 0))])
    fr.add("B", prof.family, prof.size, [((1000, 0, 0), (1000 + 1000 * math.cos(th), 0, 1000 * math.sin(th)))])
    fr.mitre("A", 0, "End", "B", 0, "Start")
    doc.recompute()
    ua = V(-1, 0, 0)
    ub = oracle.unit(V(math.cos(th), 0, math.sin(th)))
    n = oracle.unit(ua - ub)
    if n.x < 0:
        n = n * -1.0   # outward from A's end (A runs along +x)
    return fr, n, abs(n.x)


class TestEndCap(unittest.TestCase):
    def tearDown(self):
        for n in list(FreeCAD.listDocuments()):
            FreeCAD.closeDocument(n)

    def _check(self, name, doc, fr, prof, which, mode, t, recess, chamfer, n_out, cosa, u):
        member = fr.heads["A"]
        cap = WeldEndCap.make_end_cap(doc, member, 0, which, t, mode, recess, chamfer)
        doc.recompute()
        self.assertEqual(cap.State.count("Invalid"), 0, "%s: %s" % (name, cap.getStatusString()))
        plate = cap.Shape
        self.assertTrue(plate.isValid(), name)
        self.assertEqual(len(plate.Solids), 1, name)
        expect = analytic_area(prof, mode, chamfer) * t / cosa
        rel = abs(plate.Volume - expect) / expect
        worst["volume"] = max(worst["volume"], rel)
        self.assertLess(rel, 1e-9, "%s: volume %.9g vs %.9g" % (name, plate.Volume, expect))
        sol = member.Shape.Solids[0]
        com = plate.common(sol).Volume / sol.Volume
        worst["common"] = max(worst["common"], com)
        self.assertLess(com, 1e-9, name)
        # outline: points of the faces square to n_out, carried along the axis to the section plane
        xv, yv = oracle.axes(u, 0.0)
        c0 = V(0, 0, 0)
        flat = [f for f in plate.Faces if f.Surface.TypeId == "Part::GeomPlane"
                and abs(abs(f.normalAt(*[(a + b) / 2.0 for a, b in zip(f.ParameterRange[0::2], f.ParameterRange[1::2])]).dot(n_out)) - 1.0) < 1e-9]
        self.assertEqual(len(flat), 2, name)
        for f in flat:
            for e in f.Edges:
                for p in e.discretize(Number=60):
                    s = (p - c0).dot(u)
                    q = p - u * s
                    x, y = q.dot(xv), q.dot(yv)
                    outer = sd_outer(prof, x, y)
                    worst["within"] = max(worst["within"], outer)
                    self.assertLessEqual(outer, 1e-9, "%s: outline leaves the outer outline" % name)
                    if mode == "Outset" and not chamfer:
                        worst["match"] = max(worst["match"], abs(outer))
                        self.assertLess(abs(outer), 1e-9, "%s: outline does not match the outer outline" % name)
                    if mode == "Inset" and not chamfer:
                        bore = sd_bore(prof, x, y)
                        worst["match"] = max(worst["match"], abs(bore))
                        self.assertLess(abs(bore), 1e-9, "%s: inset outline does not fit the bore" % name)
                    if mode == "Inset":
                        self.assertLessEqual(sd_bore(prof, x, y), 1e-9, "%s: cap outside the bore" % name)
        # position: the outer face sits on the end plane (outset) or recess behind it (inset)
        return cap

    def test_caps(self):
        cases = []
        for kind, frac in (("RHS", 0.3), ("RHS", 0.9), ("CHS", 0.4), ("CHS", 0.9)):
            for mode in ("Outset", "Inset"):
                cases.append((kind, frac, mode))
        n = 0
        for kind, frac, mode in cases:
            prof = oracle.kind_profile(kind, frac)
            # square cut, End
            doc = FreeCAD.newDocument("C")
            fr = frames.Frame(doc)
            fr.add("A", prof.family, prof.size, [((0, 0, 0), (1000, 0, 0))])
            doc.recompute()
            self._check("%s %s square" % (prof.size, mode), doc, fr, prof, "End", mode, 8.0, 0.0 if mode == "Outset" else 5.0,
                        0.0, V(1, 0, 0), 1.0, V(1, 0, 0))
            n += 1
            # square cut, Start end
            self._check("%s %s start" % (prof.size, mode), doc, fr, prof, "Start", mode, 6.5, 0.0, 0.0, V(-1, 0, 0), 1.0, V(1, 0, 0))
            n += 1
            FreeCAD.closeDocument(doc.Name)
            # mitred end (60 and 135 degree joints)
            for theta in (60, 135):
                doc = FreeCAD.newDocument("C")
                fr, nout, cosa = _mitre_frame(doc, prof, theta)
                self._check("%s %s mitre %d" % (prof.size, mode, theta), doc, fr, prof, "End", mode, 10.0,
                            0.0 if mode == "Outset" else 3.0, 0.0, nout, cosa, V(1, 0, 0))
                n += 1
                FreeCAD.closeDocument(doc.Name)
        # chamfered rectangular caps, square and mitred
        for mode in ("Outset", "Inset"):
            prof = oracle.kind_profile("RHS", 0.7)
            r = prof.dims["ro"] if mode == "Outset" else prof.dims["ri"]
            c = r + 4.0
            doc = FreeCAD.newDocument("C")
            fr = frames.Frame(doc)
            fr.add("A", prof.family, prof.size, [((0, 0, 0), (1000, 0, 0))])
            doc.recompute()
            self._check("%s chamfer square" % mode, doc, fr, prof, "End", mode, 8.0, 0.0, c, V(1, 0, 0), 1.0, V(1, 0, 0))
            FreeCAD.closeDocument(doc.Name)
            doc = FreeCAD.newDocument("C")
            fr, nout, cosa = _mitre_frame(doc, prof, 120)
            self._check("%s chamfer mitre" % mode, doc, fr, prof, "End", mode, 8.0, 2.0, c, nout, cosa, V(1, 0, 0))
            FreeCAD.closeDocument(doc.Name)
            n += 2
        self.assertGreaterEqual(n, 20)
        FreeCAD.Console.PrintMessage(
            "end cap worst: volume %.3g, common %.3g, match %.3g mm, within %.3g mm over %d caps\n"
            % (worst["volume"], worst["common"], worst["match"], worst["within"], n)
        )

    def test_square_section_and_positions(self):
        """A square tube (EN SHS) cap sits exactly where the end face is."""
        prof = oracle.kind_profile("RHS", 0.5)
        d = prof.dims
        doc = FreeCAD.newDocument("C")
        fr = frames.Frame(doc)
        fr.add("A", prof.family, prof.size, [((0, 0, 0), (1000, 0, 0))])
        doc.recompute()
        out = WeldEndCap.make_end_cap(doc, fr.members["A"], 0, "End", 12.0, "Outset")
        inn = WeldEndCap.make_end_cap(doc, fr.members["A"], 0, "End", 12.0, "Inset", recess=20.0, name="Inner")
        doc.recompute()
        bb = out.Shape.BoundBox
        self.assertAlmostEqual(bb.XMin, 1000.0, delta=1e-9)
        self.assertAlmostEqual(bb.XMax, 1012.0, delta=1e-9)
        bb = inn.Shape.BoundBox
        self.assertAlmostEqual(bb.XMin, 1000.0 - 32.0, delta=1e-9)
        self.assertAlmostEqual(bb.XMax, 1000.0 - 20.0, delta=1e-9)
        self.assertAlmostEqual(bb.YLength, d["b"] - 2 * d["t"], delta=1e-9)


class TestEndCapRefusals(unittest.TestCase):
    def tearDown(self):
        for n in list(FreeCAD.listDocuments()):
            FreeCAD.closeDocument(n)

    def _cap(self, kind, frac, **kw):
        prof = oracle.kind_profile(kind, frac)
        doc = FreeCAD.newDocument("R")
        fr = frames.Frame(doc)
        fr.add("A", prof.family, prof.size, [((0, 0, 0), (1000, 0, 0))])
        doc.recompute()
        t = kw.pop("t", 8.0)
        cap = WeldEndCap.make_end_cap(doc, fr.members["A"], 0, "End", t, **kw)
        doc.recompute()
        return cap, prof, doc, fr

    def _bad(self, cap, words):
        self.assertIn("Invalid", cap.State)
        self.assertIn(words, cap.getStatusString())

    def test_open_sections(self):
        cap, *_ = self._cap("I", 0.5)
        self._bad(cap, "open section")
        cap, *_ = self._cap("L", 0.5)
        self._bad(cap, "open section")

    def test_chamfer_rules(self):
        cap, prof, *_ = self._cap("CHS", 0.5, chamfer=5.0)
        self._bad(cap, "no corners")
        cap, prof, *_ = self._cap("RHS", 0.5, chamfer=0.5)
        self._bad(cap, "smaller than the section's corner radius")
        cap, prof, *_ = self._cap("RHS", 0.5, chamfer=1.0e4)
        self._bad(cap, "half the shorter side")

    def test_bad_numbers(self):
        cap, *_ = self._cap("RHS", 0.5, t=0.0)
        self._bad(cap, "Thickness must be positive")
        # a Length property refuses negative values itself; the feature also guards its own inputs
        with self.assertRaises(WeldEndCap.WeldmentError):
            prof = oracle.kind_profile("RHS", 0.5)
            doc = FreeCAD.newDocument("N")
            fr = frames.Frame(doc)
            fr.add("A", prof.family, prof.size, [((0, 0, 0), (1000, 0, 0))])
            doc.recompute()
            spec = WeldEndCap.segment_specs(fr.members["A"])[0]
            WeldEndCap.build_cap(spec, fr.members["A"].Shape.Solids[0], "End", "Inset", 5.0, -1.0, 0.0)

    def test_recess_past_the_member(self):
        """An inset cap whose recess + thickness reaches beyond the other end of the member is refused."""
        for rec, t, ok in ((500.0, 10.0, True), (1180.0, 10.0, True), (1195.0, 10.0, False), (1250.0, 10.0, False),
                           (100.0, 1300.0, False)):
            prof = oracle.kind_profile("RHS", 0.5)
            doc = FreeCAD.newDocument("Rc")
            fr = frames.Frame(doc)
            fr.add("M", prof.family, prof.size, [((0, 0, 0), (0, 0, 1200))])
            doc.recompute()
            cap = WeldEndCap.make_end_cap(doc, fr.members["M"], 0, "End", t, "Inset", rec, 0.0)
            doc.recompute()
            if ok:
                self.assertNotIn("Invalid", cap.State, cap.getStatusString())
                bb = cap.Shape.BoundBox
                self.assertGreaterEqual(bb.ZMin, -1e-9)
                self.assertLessEqual(bb.ZMax, 1200.0 + 1e-9)
            else:
                self._bad(cap, "reaches past the other end")
            FreeCAD.closeDocument(doc.Name)

    def test_saddle_end(self):
        pa, pb = oracle.kind_profile("CHS", 0.2), oracle.kind_profile("CHS", 0.8)
        doc = FreeCAD.newDocument("S")
        fr = frames.Frame(doc)
        fr.add("B", pb.family, pb.size, [((-800, 0, 1000), (800, 0, 1000))])
        fr.add("A", pa.family, pa.size, [((0, 0, 0), (0, 0, 1000))])
        fr.butt("A", 0, "End", "B", 0)
        doc.recompute()
        cap = WeldEndCap.make_end_cap(doc, fr.heads["A"], 0, "End", 8.0)
        doc.recompute()
        self._bad(cap, "not a planar face")
