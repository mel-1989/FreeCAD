# SPDX-License-Identifier: LGPL-2.1-or-later
"""Acceptance check 5 (slice 5a): the cut list.

Cut length is the longest line on the member parallel to its axis (long point to long point); the
oracle here intersects that line with the end planes by hand for every point of the section outline.
"""

import csv
import io
import math
import os
import unittest

import FreeCAD
import Part

import Profiles
import WeldCutList as CL
from WTests import frames, oracle

V = FreeCAD.Vector
worst = {"length": 0.0, "angle": 0.0}


def _segments(fr, name):
    out = []
    for e in fr.paths[name].Shape.Edges:
        out.append((e.valueAt(e.FirstParameter), e.valueAt(e.LastParameter)))
    return out


def _away(seg, end):
    u = oracle.unit(seg[1] - seg[0])
    return u if end == "Start" else u * -1.0


def _mitre_planes(fr, joints):
    """{(member, segment, end): (plane (q, n), joint angle)} for the listed mitre joints."""
    planes = {}
    for (na, sa, ea), (nb, sb, eb) in joints:
        a, b = _segments(fr, na)[sa], _segments(fr, nb)[sb]
        ua, ub = _away(a, ea), _away(b, eb)
        q = a[0] if ea == "Start" else a[1]
        n = oracle.unit(ua - ub)
        joint = math.acos(ua.dot(ub))
        planes[(na, sa, ea)] = ((q, n), joint)
        planes[(nb, sb, eb)] = ((q, n), joint)
    return planes


def _check_pieces(test, fr, planes, names):
    """Cut lengths and end angles of every mitred piece of the named members against the oracle."""
    n = 0
    for name in names:
        head = fr.heads[name]
        member = fr.members[name]
        prof = Profiles.get(member.Family, member.Size)
        pieces = CL.segment_pieces(head)
        for i, (p0, p1) in enumerate(_segments(fr, name)):
            ps, pe = planes.get((name, i, "Start")), planes.get((name, i, "End"))
            want = oracle.long_point_length(prof, p0, p1, member.Anchor, member.Rotation.getValueAs("deg").Value, False, ps and ps[0], pe and pe[0])
            got = pieces[i]["cut_length"]
            worst["length"] = max(worst["length"], abs(got - want))
            test.assertLess(abs(got - want), 1e-6, "%s.%d length" % (name, i))
            for end, pl, key in (("Start", ps, "start_angle"), ("End", pe, "end_angle")):
                want_a = 0.0 if pl is None else 90.0 - math.degrees(pl[1]) / 2.0
                got_a = pieces[i][key]
                worst["angle"] = max(worst["angle"], abs(got_a - want_a))
                test.assertLess(abs(got_a - want_a), 1e-6, "%s.%d %s angle" % (name, i, end))
            n += 1
    return n


PORTAL_JOINTS = [
    (("Columns", 0, "End"), ("Rafters", 0, "Start")),
    (("Columns", 1, "End"), ("Rafters", 1, "Start")),
    (("Rafters", 0, "End"), ("Rafters", 1, "End")),
]
TRUSS_JOINTS = [
    (("Chords", 0, "Start"), ("Chords", 1, "Start")),
    (("Chords", 0, "End"), ("Chords", 2, "Start")),
    (("Chords", 1, "End"), ("Chords", 2, "End")),
]


def _golden_check(test, name, text):
    text = frames.GOLDEN_HEADER + text
    path = frames.golden_path(name)
    if os.environ.get("WELDMENT_REGEN_GOLDEN") == "1":
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return
    with open(path, encoding="utf-8", newline="") as fh:
        test.assertEqual(text, fh.read(), "%s differs from the golden; if the tables changed, regenerate (see WTests/frames.py)" % name)


class TestCutList(unittest.TestCase):
    def tearDown(self):
        for n in list(FreeCAD.listDocuments()):
            FreeCAD.closeDocument(n)

    def test_portal_cut_lengths_and_angles(self):
        doc = FreeCAD.newDocument("P")
        fr = frames.portal_frame(doc)
        doc.recompute()
        planes = _mitre_planes(fr, PORTAL_JOINTS)
        self.assertEqual(_check_pieces(self, fr, planes, ["Columns", "Rafters"]), 4)
        # the tie is butted at both ends: square to its axis, shorter than the clear span by the column widths
        tie = CL.segment_pieces(fr.heads["Tie"])[0]
        col = fr.members["Columns"]
        w = Profiles.get(col.Family, col.Size).bbox()
        self.assertAlmostEqual(tie["cut_length"], 8000.0 - (w[1] - w[0]), 6)
        self.assertEqual((tie["start_end"], tie["end_end"]), ("butt", "butt"))

    def test_truss_chords(self):
        doc = FreeCAD.newDocument("T")
        fr = frames.pitched_truss(doc)
        doc.recompute()
        planes = _mitre_planes(fr, TRUSS_JOINTS)
        self.assertEqual(_check_pieces(self, fr, planes, ["Chords"]), 3)

    def test_sections_mitred_at_both_ends(self):
        """I, L, CHS and RHS members mitred at both ends, with off-centre anchors and a turn about the axis."""
        for kind, anchor, rot in (("RHS", "Centre", 0.0), ("I", "Top Left", 25.0), ("L", "Bottom", 0.0), ("CHS", "Centre", 0.0), ("L", "Top Right", 90.0)):
            with self.subTest(kind=kind, anchor=anchor):
                doc = FreeCAD.newDocument("S")
                prof = oracle.kind_profile(kind)
                fr = frames.Frame(doc)
                a0, a1 = V(0, 0, 0), V(2000, 100, 300)
                fr.add("A", prof.family, prof.size, [(tuple(a0), tuple(a1))], anchor, rot)
                fr.add("B", prof.family, prof.size, [((-300, 500, 100), tuple(a0))], anchor, rot)
                fr.add("C", prof.family, prof.size, [(tuple(a1), (2400, -600, 900))], anchor, rot)
                fr.mitre("B", 0, "End", "A", 0, "Start")
                fr.mitre("A", 0, "End", "C", 0, "Start")
                doc.recompute()
                joints = [(("B", 0, "End"), ("A", 0, "Start")), (("A", 0, "End"), ("C", 0, "Start"))]
                planes = _mitre_planes(fr, joints)
                self.assertEqual(_check_pieces(self, fr, planes, ["A", "B", "C"]), 3)
                FreeCAD.closeDocument(doc.Name)

    def test_grouping_quantities_and_mass(self):
        doc = FreeCAD.newDocument("G")
        fr = frames.box_frame(doc)
        doc.recompute()
        rows = CL.cut_list(fr.final())
        self.assertEqual(sum(r["quantity"] for r in rows), 12)
        self.assertEqual(sorted(r["quantity"] for r in rows), [4, 4, 4])
        for r in rows:
            p = Profiles.get(r["family"], r["size"])
            self.assertAlmostEqual(r["mass_each_kg"], p.mass_per_m * r["cut_length_mm"] / 1000.0, 9)
            self.assertAlmostEqual(r["mass_total_kg"], r["mass_each_kg"] * r["quantity"], 9)
            self.assertEqual(len(r["marks"]), r["quantity"])
        # portal: both columns are the same piece, so they share a row
        doc2 = FreeCAD.newDocument("G2")
        fr2 = frames.portal_frame(doc2)
        doc2.recompute()
        rows2 = CL.cut_list(fr2.final())
        col = [r for r in rows2 if r["start_end"] == "square" and r["end_end"] == "mitre"]
        self.assertEqual(len(col), 1)
        self.assertEqual(col[0]["quantity"], 2)
        self.assertEqual(sum(r["quantity"] for r in rows2), 5)

    def test_deterministic_and_csv(self):
        texts = []
        for _ in range(2):
            doc = FreeCAD.newDocument("D")
            fr = frames.pitched_truss(doc)
            doc.recompute()
            texts.append(CL.to_csv(CL.cut_list(fr.final())))
            FreeCAD.closeDocument(doc.Name)
        self.assertEqual(texts[0], texts[1])
        rows = list(csv.DictReader(io.StringIO(texts[0])))
        self.assertEqual(list(rows[0].keys()), CL.COLUMNS)
        self.assertEqual([int(r["item"]) for r in rows], list(range(1, len(rows) + 1)))
        lengths = [(r["family"], r["size"], -float(r["cut_length_mm"])) for r in rows]
        self.assertEqual(lengths, sorted(lengths))
        self.assertNotIn("\r", texts[0])

    def test_export_csv_file(self):
        import tempfile

        doc = FreeCAD.newDocument("E")
        fr = frames.portal_frame(doc)
        doc.recompute()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "cl.csv")
            text = CL.export_csv(fr.final(), path)
            with open(path, encoding="utf-8", newline="") as fh:
                self.assertEqual(fh.read(), text)

    def test_golden_portal(self):
        doc = FreeCAD.newDocument("GP")
        fr = frames.portal_frame(doc)
        doc.recompute()
        _golden_check(self, "portal_cutlist.csv", CL.to_csv(CL.cut_list(fr.final())))

    def test_golden_truss(self):
        doc = FreeCAD.newDocument("GT")
        fr = frames.pitched_truss(doc)
        doc.recompute()
        _golden_check(self, "truss_cutlist.csv", CL.to_csv(CL.cut_list(fr.final())))


if __name__ == "__main__":
    unittest.main()
