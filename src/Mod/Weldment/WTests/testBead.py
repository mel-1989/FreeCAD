# SPDX-License-Identifier: LGPL-2.1-or-later
"""Acceptance check 3 (slice 5b): fillet weld beads, and the weld summary of check 4.

Bodies are built here from hand-computed outlines (no feature code), so the volume and length
oracles are independent: a bead's volume must be z**2 / 2 * (sum of edge lengths), the length is the
sum of the edge lengths of the outline, a = z / sqrt 2, and member volumes must not change.
"""

import math
import os
import shutil
import tempfile
import unittest

import FreeCAD
import Part

import WeldBead
from WeldBead import make_bead, weld_summary

V = FreeCAD.Vector
REL = 1e-6
worst = {"volume": 0.0, "length": 0.0}


def prism(doc, name, outline, z0, z1, holes=()):
    """Part::Feature: polygon outline (x, y) extruded from z0 to z1, minus polygon holes."""
    def face(pts):
        return Part.Face(Part.makePolygon([V(x, y, z0) for x, y in pts] + [V(pts[0][0], pts[0][1], z0)]))

    s = face(outline).extrude(V(0, 0, z1 - z0))
    for h in holes:
        s = s.cut(face(h).extrude(V(0, 0, z1 - z0)))
    o = doc.addObject("Part::Feature", name)
    o.Shape = s
    return o


def rect(cx, cy, w, h):
    return [(cx - w / 2, cy - h / 2), (cx + w / 2, cy - h / 2), (cx + w / 2, cy + h / 2), (cx - w / 2, cy + h / 2)]


def i_outline(bf, d, tf, tw):
    """I section outline, 12 vertices, centred on the origin (x flange width, y depth)."""
    x, y = bf / 2, d / 2
    w = tw / 2
    return [(-x, -y), (x, -y), (x, -y + tf), (w, -y + tf), (w, y - tf), (x, y - tf), (x, y), (-x, y),
            (-x, y - tf), (-w, y - tf), (-w, -y + tf), (-x, -y + tf)]


def l_outline(a, b, t):
    return [(0, 0), (a, 0), (a, t), (t, t), (t, b), (0, b)]


def perimeter(pts):
    return sum(math.dist(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts)))


def edges_at(obj, z, tol=1e-9, pred=None):
    """('EdgeN') names of straight edges lying in the plane height z of the object (optionally filtered)."""
    out = []
    for i, e in enumerate(obj.Shape.Edges):
        a, b = e.valueAt(e.FirstParameter), e.valueAt(e.LastParameter)
        if abs(a.z - z) < tol and abs(b.z - z) < tol and (pred is None or pred(a, b)):
            out.append("Edge%d" % (i + 1))
    return out


def edge_len(obj, name):
    return obj.Shape.getElement(name).Length


def link(obj, names):
    return [(obj, n) for n in names]


def check(test, bead, z, total_length):
    """Volume = z^2/2 L within 1e-6 relative, length reported, solids valid and unfused."""
    test.assertNotIn("Invalid", bead.State, bead.getStatusString())
    want = z * z / 2.0 * total_length
    rv = abs(bead.Shape.Volume - want) / want
    rl = abs(bead.Length.Value - total_length) / total_length
    worst["volume"] = max(worst["volume"], rv)
    worst["length"] = max(worst["length"], rl)
    test.assertLess(rv, REL, "volume")
    test.assertLess(rl, REL, "length")
    test.assertAlmostEqual(bead.LegZ.Value, z, places=9)
    test.assertAlmostEqual(bead.Throat.Value, z / math.sqrt(2.0), places=9)
    test.assertAlmostEqual(sum(bead.EdgeLengths), total_length, places=6)
    for s in bead.Shape.Solids:
        test.assertTrue(s.isValid())


class Base(unittest.TestCase):
    def tearDown(self):
        for n in list(FreeCAD.listDocuments()):
            FreeCAD.closeDocument(n)

    def tjoint(self):
        """Tube B (60 x 40, wall 4) standing on top of tube A (100 x 60, wall 5, along X)."""
        doc = FreeCAD.newDocument("T")
        # A: box section along X is a vertical extrusion of the plan; model it as plan (x, y) extruded in z
        # (a tube standing on end would do, but the weld needs a horizontal top: build A lying down)
        a = doc.addObject("Part::Feature", "A")
        outer = Part.makeBox(600, 100, 60, V(0, -50, -60))
        inner = Part.makeBox(600, 90, 50, V(0, -45, -55))
        a.Shape = outer.cut(inner)
        b = prism(doc, "B", rect(300, 0, 60, 40), 0, 300, [rect(300, 0, 52, 32)])
        doc.recompute()
        return doc, a, b

    def volumes(self, *objs):
        return [o.Shape.Volume for o in objs]


class TestTJoint(Base):
    def test_single_edge_leg(self):
        doc, a, b = self.tjoint()
        va = self.volumes(a, b)
        name = edges_at(b, 0, pred=lambda p, q: abs(p.y - q.y) < 1e-9 and abs(p.y) > 19)[0]
        bead = make_bead(doc, a, b, [(b, name)], 6.0)
        doc.recompute()
        check(self, bead, 6.0, 60.0)
        self.assertEqual(self.volumes(a, b), va)

    def test_throat_input(self):
        doc, a, b = self.tjoint()
        name = edges_at(b, 0, pred=lambda p, q: abs(p.x - q.x) < 1e-9 and abs(p.x - 300) > 29)[0]
        bead = make_bead(doc, a, b, [(b, name)], 4.0, "Throat a")
        doc.recompute()
        z = 4.0 * math.sqrt(2.0)
        check(self, bead, z, 40.0)
        self.assertAlmostEqual(bead.Throat.Value, 4.0, places=9)

    def test_outer_outline_multi_edge(self):
        doc, a, b = self.tjoint()
        names = edges_at(b, 0, pred=lambda p, q: max(abs(p.x - 300), abs(p.y) * 1.5) > 29 and
                         max(abs(q.x - 300), abs(q.y) * 1.5) > 29 and min(abs(p.x - 300), abs(p.y) * 1.5) > 0)
        # outer outline of B: the four edges on the 60 x 40 rectangle
        outer = [n for n in names if max(abs(b.Shape.getElement(n).CenterOfMass.x - 300),
                                         abs(b.Shape.getElement(n).CenterOfMass.y) * 1.5) > 29]
        self.assertEqual(len(outer), 4)
        va = self.volumes(a, b)
        bead = make_bead(doc, a, b, [(b, n) for n in outer], 5.0)
        doc.recompute()
        check(self, bead, 5.0, 200.0)
        self.assertEqual(len(bead.Shape.Solids), 4)
        self.assertEqual(self.volumes(a, b), va)
        # the bead is cosmetic: it touches the bodies and shares no volume with them
        for body in (a, b):
            self.assertLess(bead.Shape.common(body.Shape).Volume, 1e-9 * bead.Shape.Volume)

    def test_inner_outline_inside_tube(self):
        doc, a, b = self.tjoint()
        names = [n for n in edges_at(b, 0) if max(abs(b.Shape.getElement(n).CenterOfMass.x - 300),
                                                   abs(b.Shape.getElement(n).CenterOfMass.y) * 1.5) < 29]
        self.assertEqual(len(names), 4)
        bead = make_bead(doc, a, b, [(b, n) for n in names], 3.0)
        doc.recompute()
        check(self, bead, 3.0, 2 * 52 + 2 * 32)


class TestSections(Base):
    def test_i_section_on_plate(self):
        doc = FreeCAD.newDocument("I")
        plate = prism(doc, "Plate", rect(0, 0, 400, 300), -10, 0)
        outline = i_outline(100, 150, 10, 6)
        col = prism(doc, "Col", outline, 0, 400)
        doc.recompute()
        va = self.volumes(plate, col)
        names = edges_at(col, 0)
        self.assertEqual(len(names), 12)
        bead = make_bead(doc, plate, col, [(col, n) for n in names], 6.0)
        doc.recompute()
        check(self, bead, 6.0, perimeter(outline))
        self.assertEqual(self.volumes(plate, col), va)

    def test_i_section_flange_edges_throat(self):
        doc = FreeCAD.newDocument("I2")
        plate = prism(doc, "Plate", rect(0, 0, 400, 300), -10, 0)
        outline = i_outline(120, 200, 12, 7)
        col = prism(doc, "Col", outline, 0, 300)
        doc.recompute()
        # the two outer long flange edges only (y = +-100)
        names = edges_at(col, 0, pred=lambda p, q: abs(abs(p.y) - 100) < 1e-9 and abs(abs(q.y) - 100) < 1e-9)
        self.assertEqual(len(names), 2)
        bead = make_bead(doc, plate, col, [(col, n) for n in names], 5.0, "Throat a")
        doc.recompute()
        check(self, bead, 5.0 * math.sqrt(2.0), 240.0)

    def test_angle_on_plate(self):
        doc = FreeCAD.newDocument("L")
        plate = prism(doc, "Plate", rect(40, 40, 300, 300), -8, 0)
        outline = l_outline(80, 80, 8)
        ang = prism(doc, "Angle", outline, 0, 250)
        doc.recompute()
        va = self.volumes(plate, ang)
        names = edges_at(ang, 0)
        self.assertEqual(len(names), 6)
        bead = make_bead(doc, plate, ang, [(ang, n) for n in names], 4.0)
        doc.recompute()
        check(self, bead, 4.0, perimeter(outline))
        self.assertEqual(self.volumes(plate, ang), va)

    def test_angle_bead_on_either_body_order(self):
        doc = FreeCAD.newDocument("L2")
        plate = prism(doc, "Plate", rect(40, 40, 300, 300), -8, 0)
        ang = prism(doc, "Angle", l_outline(80, 80, 8), 0, 250)
        doc.recompute()
        name = edges_at(ang, 0, pred=lambda p, q: abs(p.y) < 1e-9 and abs(q.y) < 1e-9)[0]
        for b1, b2 in ((plate, ang), (ang, plate)):
            bead = make_bead(doc, b1, b2, [(ang, name)], 5.0)
            doc.recompute()
            check(self, bead, 5.0, 80.0)


class TestRefusals(Base):
    def _bad(self, bead, words):
        bead.Document.recompute()
        self.assertIn("Invalid", bead.State)
        self.assertIn(words, bead.getStatusString())

    def test_edge_not_on_both_bodies(self):
        doc, a, b = self.tjoint()
        top = edges_at(b, 300)[0]
        self._bad(make_bead(doc, a, b, [(b, top)], 5.0), "does not lie on")

    def test_edge_on_neither(self):
        doc, a, b = self.tjoint()
        far = doc.addObject("Part::Feature", "Far")
        far.Shape = Part.makeLine(V(5000, 5000, 5000), V(5100, 5000, 5000))
        self._bad(make_bead(doc, a, b, [(far, "Edge1")], 5.0), "both bodies")

    def test_oblique_corner_refused(self):
        doc = FreeCAD.newDocument("Obl")
        plate = prism(doc, "Plate", rect(0, 0, 400, 400), -10, 0)
        o = doc.addObject("Part::Feature", "Wedge")
        # parallelogram in the xz plane, extruded along y; slanted side leaves the plate at 53.13 degrees
        pts = [V(0, 0, 0), V(50, 0, 0), V(80, 0, 40), V(30, 0, 40)]
        o.Shape = Part.Face(Part.makePolygon(pts + [pts[0]])).extrude(V(0, 100, 0))
        doc.recompute()
        slant = [i for i, e in enumerate(o.Shape.Edges)
                 if abs(e.valueAt(e.FirstParameter).z) < 1e-9 and abs(e.valueAt(e.LastParameter).z) < 1e-9
                 and abs(e.valueAt(e.FirstParameter).x) < 1e-9 and abs(e.Length - 100) < 1e-9]
        self.assertEqual(len(slant), 1)
        self._bad(make_bead(doc, plate, o, [(o, "Edge%d" % (slant[0] + 1))], 5.0), "degrees")

    def test_no_free_corner_refused(self):
        doc = FreeCAD.newDocument("Flush")
        a = doc.addObject("Part::Feature", "A")
        a.Shape = Part.makeBox(10, 100, 20)
        b = doc.addObject("Part::Feature", "B")
        b.Shape = Part.makeBox(10, 100, 20, V(10, 0, 0))
        doc.recompute()
        # the edge x = 10, z = 20 lies on both boxes but the corner it forms is solid
        e = [i for i, ed in enumerate(a.Shape.Edges) if abs(ed.valueAt(ed.FirstParameter).x - 10) < 1e-9
             and abs(ed.valueAt(ed.FirstParameter).z - 20) < 1e-9 and abs(ed.Length - 100) < 1e-9][0]
        self._bad(make_bead(doc, a, b, [(a, "Edge%d" % (e + 1))], 4.0), "free corner")

    def test_leg_too_long_refused(self):
        doc, a, b = self.tjoint()
        name = edges_at(b, 0, pred=lambda p, q: abs(p.y - q.y) < 1e-9 and abs(p.y) > 19)[0]
        # B's wall is 4 mm thick but its side face is 300 mm high; A's top is 100 mm wide: 80 mm overruns A
        self._bad(make_bead(doc, a, b, [(b, name)], 80.0), "runs past")

    def test_bad_inputs(self):
        doc, a, b = self.tjoint()
        name = edges_at(b, 0)[0]
        self._bad(make_bead(doc, a, b, [(b, name)], 0.0), "positive")
        empty = make_bead(doc, a, b, [], 5.0)
        self._bad(empty, "no edges")

    def test_curved_edge_refused(self):
        doc = FreeCAD.newDocument("Cur")
        plate = prism(doc, "Plate", rect(0, 0, 300, 300), -10, 0)
        cyl = doc.addObject("Part::Feature", "Cyl")
        cyl.Shape = Part.makeCylinder(30, 100)
        doc.recompute()
        circ = [i for i, e in enumerate(cyl.Shape.Edges) if abs(e.valueAt(e.FirstParameter).z) < 1e-9
                and e.Length > 100][0]
        self._bad(make_bead(doc, plate, cyl, [(cyl, "Edge%d" % (circ + 1))], 4.0), "curved")


class TestSummaryAndReopen(Base):
    def test_summary_groups_by_size_sorted(self):
        doc, a, b = self.tjoint()
        names = edges_at(b, 0, pred=lambda p, q: max(abs(p.x - 300), abs(p.y) * 1.5) > 29 and
                         max(abs(q.x - 300), abs(q.y) * 1.5) > 29)
        outer = [n for n in names if max(abs(b.Shape.getElement(n).CenterOfMass.x - 300),
                                         abs(b.Shape.getElement(n).CenterOfMass.y) * 1.5) > 29]
        lens = {n: edge_len(b, n) for n in outer}
        n0, n1, n2, n3 = outer
        make_bead(doc, a, b, [(b, n0), (b, n1)], 8.0, name="W8")
        make_bead(doc, a, b, [(b, n2)], 5.0, name="W5a")
        make_bead(doc, a, b, [(b, n3)], 5.0, name="W5b")
        doc.recompute()
        rows = weld_summary(doc)
        self.assertEqual([r["leg_z_mm"] for r in rows], [5.0, 8.0])
        self.assertEqual([r["bead_count"] for r in rows], [2, 1])
        self.assertEqual([r["edge_count"] for r in rows], [2, 2])
        self.assertAlmostEqual(rows[0]["total_length_mm"], lens[n2] + lens[n3], places=6)
        self.assertAlmostEqual(rows[1]["total_length_mm"], lens[n0] + lens[n1], places=6)
        self.assertAlmostEqual(rows[0]["throat_a_mm"], 5.0 / math.sqrt(2.0), places=9)
        want_mass = 8.0 ** 2 / 2 * (lens[n0] + lens[n1]) * 7850.0 * 1e-9
        self.assertAlmostEqual(rows[1]["mass_kg"], want_mass, places=12)
        self.assertEqual(weld_summary(doc), rows)
        self.assertEqual(weld_summary(list(reversed(doc.Objects))), rows)
        self.assertEqual(weld_summary(FreeCAD.newDocument("Empty")), [])

    def test_save_reopen_keeps_length(self):
        doc, a, b = self.tjoint()
        names = edges_at(b, 0, pred=lambda p, q: max(abs(p.x - 300), abs(p.y) * 1.5) > 29 and
                         max(abs(q.x - 300), abs(q.y) * 1.5) > 29)
        outer = [n for n in names if max(abs(b.Shape.getElement(n).CenterOfMass.x - 300),
                                         abs(b.Shape.getElement(n).CenterOfMass.y) * 1.5) > 29]
        bead = make_bead(doc, a, b, [(b, n) for n in outer], 6.0, name="Bead")
        doc.recompute()
        before = (bead.Length.Value, list(bead.EdgeLengths), bead.Shape.Volume, weld_summary(doc))
        tmp = tempfile.mkdtemp(prefix="bead")
        try:
            path = os.path.join(tmp, "t.FCStd")
            doc.saveAs(path)
            FreeCAD.closeDocument(doc.Name)
            d2 = FreeCAD.openDocument(path)
            b2 = d2.getObject("Bead")
            self.assertEqual((b2.Length.Value, list(b2.EdgeLengths), b2.Shape.Volume), before[:3])
            self.assertEqual(weld_summary(d2), before[3])
            d2.recompute()
            b2.touch()
            d2.recompute()
            self.assertNotIn("Invalid", b2.State, b2.getStatusString())
            self.assertAlmostEqual(b2.Length.Value, before[0], places=9)
            self.assertLess(abs(b2.Shape.Volume - before[2]) / before[2], 1e-12)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_zz_report(self):
        print("\nWeldBead worst relative volume error %.3g, length error %.3g" % (worst["volume"], worst["length"]))


if __name__ == "__main__":
    unittest.main()
