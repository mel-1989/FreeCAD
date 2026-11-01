# SPDX-License-Identifier: LGPL-2.1-or-later
"""Acceptance check 1 (slice 5b): gussets.

Independent oracle: the test re-derives the plate by hand. Two members start at the origin, A along
+x and B at an angle in the x-z plane; each contributes the side face of its section (a plane at
half the section height from its axis, on the side facing the other member). The apex K is the
2x2 intersection of those lines, the plate is the triangle K, K + d1 ua, K + d2 ub (apex clipped by
the chamfer c), of area (d1 d2 - c^2) sin(a) / 2, lifted across the plane as the placement says. The
feature code (WeldGusset) is never called for an expected value.
"""

import math
import unittest

import FreeCAD
import Part

import WeldGusset
from WTests import frames, oracle

V = FreeCAD.Vector
worst = {"face": 0.0, "common": 0.0, "volume": 0.0, "vertex": 0.0}


def _half(profile):
    x0, x1, y0, y1 = profile.bbox()
    return (y1 - y0) / 2.0


def _wall(profile):
    d = profile.dims
    return d["tw"] if profile.kind == "I" else d["t"]


def _solve2(r1, r2, c1, c2):
    det = r1[0] * r2[1] - r1[1] * r2[0]
    return ((c1 * r2[1] - r1[1] * c2) / det, (r1[0] * c2 - c1 * r2[0]) / det)


# name, profile kind, angle deg, rotation A, rotation B, d1, d2, chamfer, thickness, placement
JOINTS = [
    ("RHS 90 centred", "RHS", 90, 0, 0, 200, 200, 0, 10, "Centred"),
    ("RHS 60 chamfer", "RHS", 60, 0, 0, 250, 180, 15, 8, "Centred"),
    ("RHS 120 face+", "RHS", 120, 0, 0, 220, 260, 0, 6, "Face +"),
    ("RHS 45 inside", "RHS", 45, 0, 0, 300, 300, 20, 5, "Inside"),
    ("I 90", "I", 90, 0, 0, 250, 250, 0, 12, "Centred"),
    ("I 135 face-", "I", 135, 0, 0, 200, 240, 10, 10, "Face -"),
    ("I 70 chamfer", "I", 70, 0, 0, 300, 220, 25, 8, "Centred"),
    ("L 90", "L", 90, 180, 180, 150, 150, 0, 8, "Centred"),
    ("L 105 chamfer", "L", 105, 180, 180, 180, 200, 12, 6, "Centred"),
]


#: member A leaves the joint at PHI0 degrees above the x axis, B at PHI0 + theta (no member is vertical,
#: so every section has its height axis in the x-z plane and its width axis square to it)
PHI0 = 15.0


def _dirs(theta):
    a, b = math.radians(PHI0), math.radians(PHI0 + theta)
    return V(math.cos(a), 0, math.sin(a)), V(math.cos(b), 0, math.sin(b))


def _build(doc, kind, theta, rot_a, rot_b, frac=0.5):
    prof = oracle.kind_profile(kind, frac)
    fr = frames.Frame(doc)
    ua, ub = _dirs(theta)
    fr.add("A", prof.family, prof.size, [((0, 0, 0), tuple(ua * 1000))], rotation=rot_a)
    fr.add("B", prof.family, prof.size, [((0, 0, 0), tuple(ub * 1000))], rotation=rot_b)
    doc.recompute()
    return fr, prof, math.radians(theta)


class TestGusset(unittest.TestCase):
    def tearDown(self):
        for n in list(FreeCAD.listDocuments()):
            FreeCAD.closeDocument(n)

    def _expected(self, prof, th, rot_a, rot_b, d1, d2, c, t, placement):
        ua, ub = _dirs(math.degrees(th))
        ya = oracle.axes(ua, rot_a)[1]
        yb = oracle.axes(ub, rot_b)[1]
        half = _half(prof)
        sa = 1.0 if ya.dot(ub) > 0 else -1.0
        sb = 1.0 if yb.dot(ua) > 0 else -1.0
        kx, kz = _solve2((ya.x, ya.z), (yb.x, yb.z), sa * half, sb * half)
        k = V(kx, 0, kz)
        n = oracle.unit(ua.cross(ub))
        wall = _wall(prof)
        lo = {"Centred": -t / 2.0, "Inside": -wall / 2.0, "Face +": wall / 2.0, "Face -": -wall / 2.0 - t}[placement]
        base = [k + ua * c, k + ua * d1, k + ub * d2, k + ub * c] if c else [k, k + ua * d1, k + ub * d2]
        verts = [p + n * (lo + lvl) for p in base for lvl in (0.0, t)]
        on_a = [p + n * (lo + lvl) for p in (k + ua * c, k + ua * d1) for lvl in (0.0, t)]
        on_b = [p + n * (lo + lvl) for p in (k + ub * c, k + ub * d2) for lvl in (0.0, t)]
        area = 0.5 * (d1 * d2 - c * c) * math.sin(th)
        return verts, on_a, on_b, (ya, sa * half), (yb, sb * half), area

    def test_joints(self):
        self.assertGreaterEqual(len(JOINTS), 6)
        kinds = {j[1] for j in JOINTS}
        self.assertEqual(kinds, {"RHS", "I", "L"})
        for name, kind, theta, rot_a, rot_b, d1, d2, c, t, placement in JOINTS:
            doc = FreeCAD.newDocument("G")
            fr, prof, th = _build(doc, kind, theta, rot_a, rot_b)
            g = WeldGusset.make_gusset(doc, fr.members["A"], 0, "Start", fr.members["B"], 0, "Start", d1, d2, t, c, placement)
            doc.recompute()
            self.assertEqual(g.State.count("Invalid"), 0, "%s: %s" % (name, g.getStatusString()))
            verts, on_a, on_b, (ya, ca), (yb, cb), area = self._expected(prof, th, rot_a, rot_b, d1, d2, c, t, placement)
            plate = g.Shape
            self.assertEqual(len(plate.Solids), 1, name)
            self.assertTrue(plate.isValid(), name)
            # the plate has exactly the expected corners
            have = [v.Point for v in plate.Vertexes]
            self.assertEqual(len(have), len(verts), name)
            for p in verts:
                d = min((p - q).Length for q in have)
                worst["vertex"] = max(worst["vertex"], d)
                self.assertLess(d, 1e-9, "%s: expected corner %s" % (name, p))
            # leg corners lie on the members' side faces (hand-computed planes)
            for pts, (axis, off) in ((on_a, (ya, ca)), (on_b, (yb, cb))):
                for p in pts:
                    q = min(have, key=lambda h: (h - p).Length)
                    gap = abs(q.dot(axis) - off)
                    worst["face"] = max(worst["face"], gap)
                    self.assertLess(gap, 1e-9, "%s: leg corner off the face" % name)
            # distance of the leg edges to the real member solids
            for mname, pts in (("A", on_a), ("B", on_b)):
                sol = fr.members[mname].Shape.Solids[0]
                for p in pts:
                    gap = Part.Vertex(p).distToShape(sol)[0]
                    worst["face"] = max(worst["face"], gap)
                    self.assertLess(gap, 1e-9, "%s: leg corner off member %s" % (name, mname))
            # volume = area x thickness
            expect = area * t
            rel = abs(plate.Volume - expect) / expect
            worst["volume"] = max(worst["volume"], rel)
            self.assertLess(rel, 1e-9, name)
            # no common volume with either member
            for mname in ("A", "B"):
                sol = fr.members[mname].Shape.Solids[0]
                com = plate.common(sol).Volume / sol.Volume
                worst["common"] = max(worst["common"], com)
                self.assertLess(com, 1e-9, "%s: overlaps %s" % (name, mname))
            # reported plate data
            self.assertLess(abs(g.AreaMm2 - area) / area, 1e-9, name)
            self.assertGreaterEqual(g.OutlineLengthMm, g.OutlineWidthMm, name)
            FreeCAD.closeDocument(doc.Name)
        FreeCAD.Console.PrintMessage(
            "gusset worst: vertex %.3g mm, face %.3g mm, common %.3g, volume %.3g\n"
            % (worst["vertex"], worst["face"], worst["common"], worst["volume"])
        )

    def test_mitred_members(self):
        """Members trimmed to a mitre: the gusset still lies on the trimmed solids' faces."""
        doc = FreeCAD.newDocument("G")
        fr, prof, th = _build(doc, "RHS", 90, 0, 0)
        fr.mitre("A", 0, "Start", "B", 0, "Start")
        doc.recompute()
        g = WeldGusset.make_gusset(doc, fr.heads["A"], 0, "Start", fr.heads["B"], 0, "Start", 200, 200, 10)
        doc.recompute()
        self.assertEqual(g.State.count("Invalid"), 0, g.getStatusString())
        half = _half(prof)
        expect = 0.5 * 200 * 200 * 10
        self.assertLess(abs(g.Shape.Volume - expect) / expect, 1e-9)
        for h in ("A", "B"):
            sol = fr.heads[h].Shape.Solids[0]
            self.assertLess(g.Shape.common(sol).Volume / sol.Volume, 1e-9)
        self.assertGreater(half, 0)


class TestGussetRefusals(unittest.TestCase):
    def tearDown(self):
        for n in list(FreeCAD.listDocuments()):
            FreeCAD.closeDocument(n)

    def _bad(self, g, words):
        g.Document.recompute()
        self.assertIn("Invalid", g.State)
        self.assertIn(words, g.getStatusString())

    def _joint(self, kind="RHS", theta=90, **kw):
        doc = FreeCAD.newDocument("R")
        fr, prof, th = _build(doc, kind, theta, 0, 0)
        return doc, fr

    def test_leg_past_member_face(self):
        doc, fr = self._joint()
        g = WeldGusset.make_gusset(doc, fr.members["A"], 0, "Start", fr.members["B"], 0, "Start", 1400, 200, 10)
        self._bad(g, "runs past the face")
        doc2, fr2 = self._joint()
        g2 = WeldGusset.make_gusset(doc2, fr2.members["A"], 0, "Start", fr2.members["B"], 0, "Start", 200, 1400, 10)
        self._bad(g2, "runs past the face")

    def test_leg_past_trimmed_face(self):
        """A leg reaching past the end of a short member is caught on the real solid."""
        doc, fr = self._joint()
        # shorten B to 150 mm: a 200 mm leg 2 runs past its end
        fr.paths["B"].Shape = Part.Compound([Part.makeLine(V(0, 0, 0), V(0, 0, 150))])
        doc.recompute()
        g = WeldGusset.make_gusset(doc, fr.members["A"], 0, "Start", fr.members["B"], 0, "Start", 100, 200, 10)
        self._bad(g, "runs past the face")

    def test_not_sharing_a_plane(self):
        doc = FreeCAD.newDocument("R")
        prof = oracle.kind_profile("RHS", 0.5)
        fr = frames.Frame(doc)
        fr.add("A", prof.family, prof.size, [((0, 0, 0), (1000, 0, 0))])
        fr.add("B", prof.family, prof.size, [((0, 0, 0), (0, 0, 1000))], anchor="Top")
        doc.recompute()
        g = WeldGusset.make_gusset(doc, fr.members["A"], 0, "Start", fr.members["B"], 0, "Start", 100, 100, 8)
        self._bad(g, "do not share a plane")

    def test_skew_members(self):
        doc = FreeCAD.newDocument("R")
        prof = oracle.kind_profile("RHS", 0.5)
        fr = frames.Frame(doc)
        fr.add("A", prof.family, prof.size, [((0, 0, 0), (1000, 0, 0))])
        fr.add("B", prof.family, prof.size, [((0, 60, 0), (0, 60, 1000))])
        doc.recompute()
        g = WeldGusset.make_gusset(doc, fr.members["A"], 0, "Start", fr.members["B"], 0, "Start", 100, 100, 8)
        self._bad(g, "do not share a plane")

    def test_section_turned_out_of_plane(self):
        doc = FreeCAD.newDocument("R")
        prof = oracle.kind_profile("RHS", 0.5)
        fr = frames.Frame(doc)
        fr.add("A", prof.family, prof.size, [((0, 0, 0), (1000, 0, 0))], rotation=30.0)
        fr.add("B", prof.family, prof.size, [((0, 0, 0), (0, 0, 1000))])
        doc.recompute()
        g = WeldGusset.make_gusset(doc, fr.members["A"], 0, "Start", fr.members["B"], 0, "Start", 100, 100, 8)
        self._bad(g, "turned out of the joint plane")

    def test_other_refusals(self):
        doc, fr = self._joint()
        g = WeldGusset.make_gusset(doc, fr.members["A"], 0, "Start", fr.members["B"], 0, "Start", 100, 100, 8, chamfer=100)
        self._bad(g, "chamfer")
        doc2 = FreeCAD.newDocument("R2")
        prof = oracle.kind_profile("RHS", 0.5)
        fr2 = frames.Frame(doc2)
        fr2.add("A", prof.family, prof.size, [((0, 0, 0), (1000, 0, 0))])
        fr2.add("B", prof.family, prof.size, [((0, 0, 0), (1000, 0, 5))])
        doc2.recompute()
        g2 = WeldGusset.make_gusset(doc2, fr2.members["A"], 0, "Start", fr2.members["B"], 0, "Start", 100, 100, 8)
        self._bad(g2, "parallel")
        # a round tube has no flat face
        doc3 = FreeCAD.newDocument("R3")
        cp = oracle.kind_profile("CHS", 0.5)
        fam, size = cp.family, cp.size
        fr3 = frames.Frame(doc3)
        fr3.add("A", fam, size, [((0, 0, 0), (1000, 0, 0))])
        fr3.add("B", fam, size, [((0, 0, 0), (0, 0, 1000))])
        doc3.recompute()
        g3 = WeldGusset.make_gusset(doc3, fr3.members["A"], 0, "Start", fr3.members["B"], 0, "Start", 100, 100, 8)
        self._bad(g3, "circular hollow section")
