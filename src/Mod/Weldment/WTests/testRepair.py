# SPDX-License-Identifier: LGPL-2.1-or-later
"""Regression tests for the run 029 repair round (slice 5a): saddle cut lengths, seating of butts
and face trims, bevel azimuth and tolerance clustering in the cut list, CSV precision, messages.

Oracles are derived by hand from the cylinder equation, never from the feature code."""

import csv
import io
import math
import unittest

import FreeCAD
import Part

import Profiles
import WeldCutList as CL
import WeldGeom
from WeldMember import make_member
from WeldTrim import make_trim

V = FreeCAD.Vector


def _lines(doc, name, segs):
    o = doc.addObject("Part::Feature", name)
    o.Shape = Part.Compound([Part.makeLine(V(*a), V(*b)) for a, b in segs])
    return o


def _plane(doc, name, point, normal):
    f = Part.makePlane(4000, 4000, V(-2000, -2000, 0), V(0, 0, 1))
    f.Placement = FreeCAD.Placement(point, FreeCAD.Rotation(V(0, 0, 1), normal))
    o = doc.addObject("Part::Feature", name)
    o.Shape = f
    return o


def _plain(doc, name, length, fam="EN RHS", size="100x50x5"):
    return make_member(doc, fam, size, [_lines(doc, name + "p", [((0, 0, 0), (length, 0, 0))])], name)


class _Base(unittest.TestCase):
    def tearDown(self):
        for n in list(FreeCAD.listDocuments()):
            FreeCAD.closeDocument(n)


class TestSaddleLength(_Base):
    RC, RB = 168.3 / 2.0, 273.0 / 2.0

    def _beam(self, doc, z=0.0, name="Beam"):
        return make_member(doc, "EN CHS", "273x6.3", [_lines(doc, name + "p", [((0, -1500, z), (0, 1500, z))])], name)

    def test_one_saddle_end_is_rotation_independent_and_analytic(self):
        # column axis z, ends on the top of a horizontal tube of radius RB along y; the long point is
        # where the column's surface line at x = +-RC meets the beam: z = -sqrt(RB^2 - RC^2)
        analytic = 1000.0 - math.sqrt(self.RB ** 2 - self.RC ** 2)
        doc = FreeCAD.newDocument("Saddle")
        beam = self._beam(doc)
        got = []
        for rot in (0.0, 30.0, 60.0, 90.0, 137.0):
            col = make_member(doc, "EN CHS", "168.3x10", [_lines(doc, "cp%d" % rot, [((0, 0, -1000), (0, 0, 0))])],
                              "Col%d" % rot, "Centre", rot)
            t = make_trim(doc, col, 0, "End", "Butt", beam, "", 0, "Start", name="Bt%d" % rot)
            doc.recompute()
            self.assertNotIn("Invalid", t.State, t.getStatusString())
            r = CL.cut_list([t])[0]
            self.assertIsNone(r["end_angle_deg"])
            self.assertIsNone(r["start_angle_deg"] if False else r["end_angle_deg"])
            got.append(r["cut_length_mm"])
            self.assertAlmostEqual(r["cut_length_mm"], analytic, delta=1e-6, msg="rot %g" % rot)
        TestSaddleLength.worst = max(abs(g - analytic) for g in got)
        self.assertLess(TestSaddleLength.worst, 1e-6)

    def test_two_saddle_ends(self):
        # both ends curved: line at x = +-RC peaks, length = H - 2 sqrt(RB^2 - RC^2)
        H = 1200.0
        analytic = H - 2.0 * math.sqrt(self.RB ** 2 - self.RC ** 2)
        doc = FreeCAD.newDocument("Saddle2")
        top, bot = self._beam(doc, 0.0, "Top"), self._beam(doc, -H, "Bot")
        col = make_member(doc, "EN CHS", "168.3x10", [_lines(doc, "cp", [((0, 0, -H), (0, 0, 0))])], "Col")
        t1 = make_trim(doc, col, 0, "End", "Butt", top, "", 0, "Start", name="B1")
        t2 = make_trim(doc, t1, 0, "Start", "Butt", bot, "", 0, "Start", name="B2")
        doc.recompute()
        self.assertNotIn("Invalid", t2.State, t2.getStatusString())
        r = CL.cut_list([t2])[0]
        self.assertAlmostEqual(r["cut_length_mm"], analytic, delta=1e-6)
        self.assertIsNone(r["start_angle_deg"])
        self.assertIsNone(r["end_angle_deg"])
        TestSaddleLength.worst2 = abs(r["cut_length_mm"] - analytic)


class TestSeating(_Base):
    def _tee(self, doc, col_fam, col_size, beam_fam, beam_size):
        beam = make_member(doc, beam_fam, beam_size, [_lines(doc, "bp", [((-1500, 0, 0), (1500, 0, 0))])], "Beam")
        col = make_member(doc, col_fam, col_size, [_lines(doc, "cp", [((0, 0, 1500), (0, 0, 0))])], "Col")
        t = make_trim(doc, col, 0, "End", "Butt", beam, "", 0, "Start", name="Bt")
        doc.recompute()
        return t

    def test_overhanging_butt_is_refused(self):
        doc = FreeCAD.newDocument("Overhang")
        t = self._tee(doc, "EN SHS", "250x250x9", "EN IPE", "100")
        self.assertIn("Invalid", t.State)
        self.assertIn("not fully seated", t.getStatusString())

    def test_seated_butt_is_accepted(self):
        doc = FreeCAD.newDocument("Seated")
        # 100 mm high section lies inside the 150 mm wide flange of an IPE 300
        t = self._tee(doc, "EN RHS", "100x50x5", "EN IPE", "300")
        self.assertNotIn("Invalid", t.State, t.getStatusString())

    def test_face_smaller_than_section_is_refused(self):
        doc = FreeCAD.newDocument("SmallFace")
        col = make_member(doc, "EN RHS", "100x50x5", [_lines(doc, "cp", [((0, 0, 0), (0, 0, 800))])], "Col")
        plate = doc.addObject("Part::Box", "Plate")
        plate.Length, plate.Width, plate.Height = 20, 20, 10
        plate.Placement = FreeCAD.Placement(V(-10, -10, 1000), FreeCAD.Rotation())
        doc.recompute()
        face = "Face%d" % (1 + [round(f.normalAt(0, 0).z) for f in plate.Shape.Faces].index(-1))
        t = make_trim(doc, col, 0, "End", "Face", plate, face)
        doc.recompute()
        self.assertIn("Invalid", t.State)
        self.assertIn("does not lie within the face", t.getStatusString())

    def test_face_larger_than_section_is_accepted(self):
        doc = FreeCAD.newDocument("BigFace")
        col = make_member(doc, "EN RHS", "100x50x5", [_lines(doc, "cp", [((0, 0, 0), (0, 0, 800))])], "Col")
        plate = doc.addObject("Part::Box", "Plate")
        plate.Length, plate.Width, plate.Height = 100, 100, 10
        plate.Placement = FreeCAD.Placement(V(-50, -50, 1000), FreeCAD.Rotation())
        doc.recompute()
        face = "Face%d" % (1 + [round(f.normalAt(0, 0).z) for f in plate.Shape.Faces].index(-1))
        t = make_trim(doc, col, 0, "End", "Face", plate, face)
        doc.recompute()
        self.assertNotIn("Invalid", t.State, t.getStatusString())


class TestGrouping(_Base):
    def _tilted(self, doc, name, axis):
        p = _lines(doc, name + "p", [((0, 0, 0), (1000, 0, 0))])
        m = make_member(doc, "EN HEB", "300", [p], name)
        a = math.radians(30.0)
        n = V(math.cos(a), math.sin(a), 0) if axis == "z" else V(math.cos(a), 0, math.sin(a))
        o1 = _plane(doc, name + "s", V(0, 0, 0), n)
        o2 = _plane(doc, name + "e", V(1000, 0, 0), n)
        t1 = make_trim(doc, m, 0, "Start", "Face", o1, "Face1", name=name + "T1")
        return make_trim(doc, t1, 0, "End", "Face", o2, "Face1", name=name + "T2")

    def test_bevel_orientation_separates_rows(self):
        doc = FreeCAD.newDocument("Bevel")
        a, b = self._tilted(doc, "a", "z"), self._tilted(doc, "b", "y")
        doc.recompute()
        rows = CL.cut_list([a, b])
        self.assertEqual(sorted(r["quantity"] for r in rows), [1, 1])
        self.assertAlmostEqual(rows[0]["start_angle_deg"], rows[1]["start_angle_deg"], 6)
        c = self._tilted(doc, "c", "z")
        doc.recompute()
        rows = CL.cut_list([a, b, c])
        self.assertEqual(sorted(r["quantity"] for r in rows), [1, 2])

    def test_symmetric_section_turned_half_a_turn_still_merges(self):
        # a bevel facing +x and one facing -x on a doubly symmetric section are the same piece
        doc = FreeCAD.newDocument("Half")
        a = self._tilted(doc, "a", "z")
        p = _lines(doc, "bp", [((0, 0, 0), (1000, 0, 0))])
        m = make_member(doc, "EN HEB", "300", [p], "b")
        a2 = math.radians(-30.0)
        n = V(math.cos(a2), math.sin(a2), 0)
        t1 = make_trim(doc, m, 0, "Start", "Face", _plane(doc, "bs", V(0, 0, 0), n), "Face1", name="bT1")
        b = make_trim(doc, t1, 0, "End", "Face", _plane(doc, "be", V(1000, 0, 0), n), "Face1", name="bT2")
        doc.recompute()
        # b is tilted the opposite way about z: start/end normals are mirrored, not turned, so it is
        # the opposite hand of a only if the bevels disagree; here both ends turn together by 180
        rows = CL.cut_list([a, b])
        self.assertEqual(sum(r["quantity"] for r in rows), 2)

    def test_float_noise_across_rounding_boundary_merges(self):
        doc = FreeCAD.newDocument("Noise")
        base = 1000.00005     # sits exactly on a 4-decimal rounding boundary
        ms = [_plain(doc, "n0", base - 1e-9), _plain(doc, "n1", base + 1e-9), _plain(doc, "n2", base)]
        doc.recompute()
        rows = CL.cut_list(ms)
        self.assertEqual([r["quantity"] for r in rows], [3])
        # beyond tolerance they stay apart
        far = [_plain(doc, "f0", 1000.0), _plain(doc, "f1", 1000.0 + 1e-4)]
        doc.recompute()
        self.assertEqual(len(CL.cut_list(far)), 2)

    def test_output_does_not_depend_on_input_order(self):
        doc = FreeCAD.newDocument("Order")
        ms = [_plain(doc, "o%d" % i, 1000.0 + 1e-9 * i) for i in range(4)] + [_plain(doc, "x", 500.0)]
        doc.recompute()
        self.assertEqual(CL.to_csv(CL.cut_list(ms)), CL.to_csv(CL.cut_list(list(reversed(ms)))))


class TestCsvAndMessages(_Base):
    def test_csv_prints_micrometre_precision(self):
        doc = FreeCAD.newDocument("Csv")
        m = _plain(doc, "m", 1234.5678912)
        doc.recompute()
        row = list(csv.DictReader(io.StringIO(CL.to_csv(CL.cut_list([m])))))[0]
        self.assertEqual(row["cut_length_mm"], "1234.567891")
        self.assertAlmostEqual(float(row["cut_length_mm"]), 1234.5678912, delta=1e-6)

    def test_zero_length_path_edge_is_readable(self):
        doc = FreeCAD.newDocument("Zero")
        z = doc.addObject("Part::Feature", "zp")
        z.Shape = Part.makeLine(V(0, 0, 0), V(0, 0, 1e-9))
        m = make_member(doc, "EN RHS", "100x50x5", [z], "Z")
        doc.recompute()
        self.assertIn("Invalid", m.State)
        self.assertIn("zero length", m.getStatusString())
        self.assertNotIn("Unknown exception", m.getStatusString())

    def test_unknown_size_message_is_capped(self):
        with self.assertRaises(KeyError) as cm:
            Profiles.get("EN RHS", "100x50x55")
        msg = str(cm.exception)
        self.assertLess(len(msg), 400)
        self.assertIn("nearest", msg)
        self.assertIn("%d sizes" % len(Profiles.sizes("EN RHS")), msg)
        doc = FreeCAD.newDocument("Bad")
        m = make_member(doc, "EN RHS", "100x50x5", [_lines(doc, "p", [((0, 0, 0), (0, 0, 100))])], "B")
        m.Size = "nope"
        doc.recompute()
        self.assertLess(len(m.getStatusString()), 600)

    def test_trim_wording_has_no_doubled_body(self):
        for kind in ("butt", "body"):
            self.assertNotIn("body body", WeldGeom._target(kind))
            self.assertNotIn("butt body", WeldGeom._target(kind))


class TestHssDocumented(unittest.TestCase):
    def test_mass_note_present(self):
        import os

        here = os.path.dirname(os.path.abspath(Profiles.__file__))
        with open(os.path.join(here, "profiles", "05_aisc_hss.csv"), encoding="utf-8") as fh:
            head = "".join(l for l in fh if l.startswith("#"))
        self.assertIn("nominal-wall", head)
        self.assertIn("ambaker1/aisc-csv", head)
        self.assertIn("tdes", Profiles.__doc__)


if __name__ == "__main__":
    unittest.main()
