# SPDX-License-Identifier: LGPL-2.1-or-later
"""Acceptance check 3 (slice 5a): the parametric structural member."""

import unittest

import FreeCAD
import Part

import Profiles
import WeldMember
from WeldGeom import ANCHORS
from WTests import frames, oracle

V = FreeCAD.Vector
REL = 1e-9
def zc(shape):
    return (shape.BoundBox.ZMin + shape.BoundBox.ZMax) / 2.0


worst = {"volume": 0.0}


def _doc(name="WM"):
    return FreeCAD.newDocument(name)


def _line(doc, a, b, name="Path"):
    return frames.lines(doc, name, [(a, b)])


class TestMember(unittest.TestCase):
    def tearDown(self):
        for n in list(FreeCAD.listDocuments()):
            FreeCAD.closeDocument(n)

    def test_straight_volume_every_kind(self):
        """Volume = section area x length within 1e-9 for every section kind, at several positions."""
        doc = _doc()
        seen = set()
        for fam in Profiles.families():
            sizes = Profiles.sizes(fam)
            for k in (0, len(sizes) // 2, len(sizes) - 1):
                p = Profiles.get(fam, sizes[k])
                seen.add(p.kind)
                path = _line(doc, (10, -20, 5), (10 + 600.0, -20 + 300.0, 5 + 1234.5), "P%d" % len(doc.Objects))
                m = WeldMember.make_member(doc, fam, sizes[k], [path], "M")
                self.assertTrue(doc.recompute() >= 1)
                self.assertTrue(m.Shape.isValid(), (fam, sizes[k]))
                length = (V(600, 300, 1234.5)).Length
                err = abs(m.Shape.Volume - p.area() * length) / (p.area() * length)
                worst["volume"] = max(worst["volume"], err)
                self.assertLess(err, REL, "%s %s" % (fam, sizes[k]))
                doc.removeObject(m.Name)
                doc.removeObject(path.Name)
        self.assertTrue({"I", "RHS", "CHS", "L"} <= seen, seen)

    def test_nine_anchors_land_on_path(self):
        """For each of the nine anchors, the anchor point of the section lies on the path."""
        doc = _doc()
        p = oracle.kind_profile("RHS")
        path = _line(doc, (0, 0, 0), (1000, 0, 0))
        x0, x1, y0, y1 = p.bbox()
        for mirror in (False, True):
            xs = (-x1, -x0) if mirror else (x0, x1)
            for anchor in ANCHORS:
                m = WeldMember.make_member(doc, p.family, p.size, [path], "M", anchor=anchor, mirror=mirror)
                doc.recompute()
                ax, ay = oracle.anchor_xy(p, anchor, mirror)
                # path along +X: profile x -> global Y, profile y -> global Z
                bb = m.Shape.BoundBox
                self.assertAlmostEqual(bb.YMin, xs[0] - ax, 7, (anchor, mirror))
                self.assertAlmostEqual(bb.YMax, xs[1] - ax, 7, (anchor, mirror))
                self.assertAlmostEqual(bb.ZMin, y0 - ay, 7, (anchor, mirror))
                self.assertAlmostEqual(bb.ZMax, y1 - ay, 7, (anchor, mirror))
                doc.removeObject(m.Name)

    def test_rotation_and_mirror(self):
        doc = _doc()
        p = oracle.kind_profile("RHS")
        x0, x1, y0, y1 = p.bbox()
        path = _line(doc, (0, 0, 0), (1000, 0, 0))
        m = WeldMember.make_member(doc, p.family, p.size, [path], "M", rotation=90.0)
        doc.recompute()
        bb = m.Shape.BoundBox
        self.assertAlmostEqual(bb.YLength, y1 - y0, 7)
        self.assertAlmostEqual(bb.ZLength, x1 - x0, 7)
        # an asymmetric section (angle) mirrored: same volume, bounding box mirrored in Y
        q = oracle.kind_profile("L")
        a = WeldMember.make_member(doc, q.family, q.size, [path], "A", anchor="Centre")
        b = WeldMember.make_member(doc, q.family, q.size, [path], "B", anchor="Centre", mirror=True)
        doc.recompute()
        self.assertAlmostEqual(a.Shape.Volume, b.Shape.Volume, 4)
        self.assertAlmostEqual(a.Shape.BoundBox.YMin, -b.Shape.BoundBox.YMax, 7)
        self.assertAlmostEqual(a.Shape.BoundBox.YMax, -b.Shape.BoundBox.YMin, 7)

    def test_one_solid_per_segment(self):
        doc = _doc()
        fam = "EN SHS" if "EN SHS" in Profiles.families() else Profiles.families()[0]
        path = frames.lines(doc, "Poly", [((0, 0, 0), (1000, 0, 0)), ((1000, 0, 0), (1000, 800, 0)), ((1000, 800, 300), (0, 800, 300))])
        m = WeldMember.make_member(doc, fam, Profiles.sizes(fam)[1], [path], "M")
        doc.recompute()
        self.assertEqual(len(m.Shape.Solids), 3)
        self.assertEqual([round(x, 6) for x in m.SegmentLengths], [1000.0, 800.0, 1000.0])
        # single named edges also work
        m2 = WeldMember.make_member(doc, fam, Profiles.sizes(fam)[1], [(path, ["Edge2"])], "M2")
        doc.recompute()
        self.assertEqual(len(m2.Shape.Solids), 1)

    def _check_frame(self, build):
        doc = _doc()
        fr = build(doc)
        self.assertGreaterEqual(doc.recompute(), 1)
        total_segments = 0
        for name, m in fr.members.items():
            self.assertNotIn("Invalid", m.State, (name, m.getStatusString()))
            specs = WeldMember.segment_specs(m)
            total_segments += len(specs)
            self.assertEqual(len(m.Shape.Solids), len(specs))
            p = Profiles.get(m.Family, m.Size)
            for sol, sp in zip(m.Shape.Solids, specs):
                self.assertTrue(sol.isValid())
                self.assertLess(abs(sol.Volume - p.area() * sp.length) / (p.area() * sp.length), REL)
        for h in fr.final():
            self.assertNotIn("Invalid", h.State, (h.Name, h.getStatusString()))
            self.assertTrue(h.Shape.isValid())
        return total_segments

    def test_box_frame(self):
        self.assertEqual(self._check_frame(frames.box_frame), 12)

    def test_portal_frame(self):
        self.assertGreaterEqual(self._check_frame(frames.portal_frame), 5)

    def test_pitched_truss(self):
        self.assertGreaterEqual(self._check_frame(frames.pitched_truss), 7)

    def test_recompute_when_path_changes(self):
        doc = _doc()
        fr = frames.portal_frame(doc)
        doc.recompute()
        tie = fr.members["Tie"]
        head = fr.heads["Tie"]
        p = Profiles.get(tie.Family, tie.Size)
        self.assertAlmostEqual(tie.Shape.BoundBox.ZMax - tie.Shape.BoundBox.ZMin, p.bbox()[3] - p.bbox()[2], 6)
        z0 = zc(tie.Shape)
        v0 = head.Shape.Volume
        fr.paths["Tie"].Shape = Part.Compound([Part.makeLine(V(0, 0, 2500), V(8000, 0, 2500))])
        doc.recompute()
        self.assertAlmostEqual(zc(tie.Shape), 2500.0, 6)
        self.assertNotAlmostEqual(zc(tie.Shape), z0, 3)
        self.assertNotIn("Invalid", head.State)
        self.assertAlmostEqual(zc(head.Shape), 2500.0, 6)
        self.assertAlmostEqual(head.Shape.Volume, v0, 3)  # butted to the columns at the new height, same length
        # lengthening a column changes its solid
        col = fr.members["Columns"]
        self.assertAlmostEqual(col.Shape.Solids[0].Volume / Profiles.get(col.Family, col.Size).area(), 4000.0, 6)

    def test_readable_refusals(self):
        doc = _doc()
        fam = Profiles.families()[0]
        size = Profiles.sizes(fam)[0]
        arc = doc.addObject("Part::Feature", "Arc")
        arc.Shape = Part.Compound([Part.makeCircle(500, V(0, 0, 0), V(0, 0, 1), 0, 90)])
        m = WeldMember.make_member(doc, fam, size, [arc], "OnArc")
        bad = WeldMember.make_member(doc, fam, "no-such-size", [_line(doc, (0, 0, 0), (1, 0, 0))], "BadSize")
        empty = WeldMember.make_member(doc, fam, size, [], "Empty")
        doc.recompute()
        for obj, words in ((m, "curved"), (bad, "unknown profile"), (empty, "empty path")):
            self.assertIn("Invalid", obj.State, obj.Name)
            self.assertIn(words, obj.getStatusString(), obj.Name)


if __name__ == "__main__":
    unittest.main()
