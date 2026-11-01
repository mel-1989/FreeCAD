# SPDX-License-Identifier: LGPL-2.1-or-later
"""Acceptance check 4, plate half (slice 5b): gussets and end caps as cut-list plate items.

Oracles written here: plate mass = (analytic area x thickness) x density / 1e9; identical plates group
with a quantity; the order is independent of the order the objects are passed; a frame without plates
produces exactly the slice 5a CSV.
"""

import csv
import io
import math
import random
import unittest

import FreeCAD

import WeldBead
import WeldCutList as CL
import WeldEndCap
import WeldGusset
from WTests import frames, oracle

V = FreeCAD.Vector


def _portal(doc):
    fr = frames.portal_frame(doc)
    doc.recompute()
    return fr


def _eave_gussets(doc, fr, d1, d2, t, c, density=None):
    out = []
    for i in (0, 1):
        g = WeldGusset.make_gusset(doc, fr.heads["Columns"], i, "End", fr.heads["Rafters"], i, "Start", d1, d2, t, c,
                                   name="Gusset%d" % i)
        if density is not None:
            g.Density = density
        out.append(g)
    doc.recompute()
    return out


def _eave_angle(span=8000.0, rise=1500.0):
    ua = V(0, 0, -1)
    ub = oracle.unit(V(span / 2.0, 0, rise))
    return math.asin(ua.cross(ub).Length)


class TestPlateCutList(unittest.TestCase):
    def tearDown(self):
        for n in list(FreeCAD.listDocuments()):
            FreeCAD.closeDocument(n)

    def _frame_with_plates(self):
        doc = FreeCAD.newDocument("P")
        fr = _portal(doc)
        gs = _eave_gussets(doc, fr, 300.0, 250.0, 10.0, 20.0)
        prof = oracle.kind_profile("RHS", 0.75)
        caps = []
        for i, seg_end in enumerate(("Start", "Start")):
            caps.append(WeldEndCap.make_end_cap(doc, fr.heads["Columns"], i, seg_end, 8.0, name="Base%d" % i))
        caps.append(WeldEndCap.make_end_cap(doc, fr.heads["Tie"], 0, "Start", 5.0, "Inset", 2.0, name="TieCap"))
        doc.recompute()
        for o in gs + caps:
            self.assertEqual(o.State.count("Invalid"), 0, "%s: %s" % (o.Name, o.getStatusString()))
        return doc, fr, gs, caps, prof

    def test_frame_without_plates_unchanged(self):
        doc = FreeCAD.newDocument("P")
        fr = _portal(doc)
        text = CL.to_csv(CL.cut_list(fr.final()))
        self.assertNotIn("thickness_mm", text)
        self.assertEqual(text.count("\n\n"), 0)
        rows = CL.cut_list(fr.final())
        self.assertTrue(all("type" not in r for r in rows))

    def test_plate_items_and_mass(self):
        doc, fr, gs, caps, prof = self._frame_with_plates()
        objs = fr.final() + gs + caps
        rows = CL.cut_list(objs)
        members = [r for r in rows if "type" not in r]
        plates = [r for r in rows if "type" in r]
        self.assertEqual(len(members), len(CL.cut_list(fr.final())))
        self.assertEqual(sum(r["quantity"] for r in plates), 5)
        by = {(r["type"], round(r["thickness_mm"], 6)): r for r in plates}
        self.assertEqual(len(plates), 3)
        # two identical eave gussets
        g = by[("Gusset", 10.0)]
        self.assertEqual(g["quantity"], 2)
        a = _eave_angle()
        area = 0.5 * (300.0 * 250.0 - 20.0 ** 2) * math.sin(a)
        self.assertLess(abs(g["area_mm2"] - area) / area, 1e-9)
        each = area * 10.0 * 7850.0 / 1e9
        self.assertLess(abs(g["mass_each_kg"] - each) / each, 1e-9)
        self.assertLess(abs(g["mass_total_kg"] - 2 * each) / each, 1e-9)
        self.assertEqual(g["density_kg_m3"], 7850.0)
        # two identical base caps (outset, square-cut): outer area x t
        d = prof.dims
        cap = by[("End cap", 8.0)]
        self.assertEqual(cap["quantity"], 2)
        oa = d["b"] * d["h"] - (4 - math.pi) * d["ro"] ** 2
        self.assertLess(abs(cap["mass_each_kg"] - oa * 8.0 * 7850.0 / 1e9) / cap["mass_each_kg"], 1e-9)
        self.assertLess(abs(cap["length_mm"] - max(d["b"], d["h"])) , 1e-6)
        self.assertLess(abs(cap["width_mm"] - min(d["b"], d["h"])), 1e-6)
        tie = by[("End cap", 5.0)]
        self.assertEqual(tie["quantity"], 1)
        # item numbers continue after the members and the plate rows have marks
        self.assertEqual([r["item"] for r in rows], list(range(1, len(rows) + 1)))
        self.assertEqual(sorted(sum((r["marks"] for r in plates), [])), sorted(o.Name for o in gs + caps))

    def test_density_and_deterministic_order(self):
        doc, fr, gs, caps, prof = self._frame_with_plates()
        gs[0].Density = 2700.0
        doc.recompute()
        objs = fr.final() + gs + caps
        rows = CL.cut_list(objs)
        plates = [r for r in rows if "type" in r]
        self.assertEqual(len([r for r in plates if r["type"] == "Gusset"]), 2)   # different density: separate items
        a = _eave_angle()
        each = 0.5 * (300.0 * 250.0 - 400.0) * math.sin(a) * 10.0
        masses = sorted(r["mass_each_kg"] for r in plates if r["type"] == "Gusset")
        self.assertLess(abs(masses[0] - each * 2700.0 / 1e9) / masses[0], 1e-9)
        self.assertLess(abs(masses[1] - each * 7850.0 / 1e9) / masses[1], 1e-9)
        text = CL.to_csv(rows)
        rnd = random.Random(7)
        for _ in range(5):
            shuffled = list(objs)
            rnd.shuffle(shuffled)
            self.assertEqual(CL.to_csv(CL.cut_list(shuffled)), text)

    def test_csv_sections(self):
        doc, fr, gs, caps, prof = self._frame_with_plates()
        base = CL.to_csv(CL.cut_list(fr.final()))
        text = CL.to_csv(CL.cut_list(fr.final() + gs + caps))
        self.assertTrue(text.startswith(base), "member section must be byte-identical")
        rest = text[len(base):]
        self.assertTrue(rest.startswith("\n" + ",".join(CL.PLATE_COLUMNS) + "\n"))
        body = list(csv.reader(io.StringIO(rest.strip("\n"))))
        self.assertEqual(body[0], CL.PLATE_COLUMNS)
        self.assertEqual(len(body) - 1, 3)
        for line in body[1:]:
            self.assertEqual(len(line), len(CL.PLATE_COLUMNS))
        self.assertTrue(text.endswith("\n") and not text.endswith("\n\n"))


def _joint(fr, name, x0, theta):
    th = math.radians(theta)
    fr.add(name + "A", "EN SHS", frames.pick("EN SHS", 0.5), [((x0, 0, 0), (x0 + 1500.0, 0, 0))])
    fr.add(name + "B", "EN SHS", frames.pick("EN SHS", 0.5),
           [((x0, 0, 0), (x0 + 1500.0 * math.cos(th), 0, 1500.0 * math.sin(th)))])


class TestPlateCongruence(unittest.TestCase):
    """Plates group by congruence of the polygon (angles and sides up to rotation and reflection), and
    report the minimum-area bounding rectangle."""

    def tearDown(self):
        for n in list(FreeCAD.listDocuments()):
            FreeCAD.closeDocument(n)

    def test_same_bounding_box_different_plates_do_not_merge(self):
        doc = FreeCAD.newDocument("K")
        fr = frames.Frame(doc)
        _joint(fr, "P", 0.0, 90)
        _joint(fr, "Q", 5000.0, 60)
        doc.recompute()
        # right triangle with legs 200 x 100, and a 60 degree triangle with legs 200 and 100 / sin 60:
        # the same area (10000) and the same height 100 over the 200 side (the old bounding-box
        # size was 200 x 100 for both), but not congruent
        g1 = WeldGusset.make_gusset(doc, fr.members["PA"], 0, "Start", fr.members["PB"], 0, "Start", 200.0, 100.0, 10.0, name="G1")
        g2 = WeldGusset.make_gusset(doc, fr.members["QA"], 0, "Start", fr.members["QB"], 0, "Start", 200.0,
                                    100.0 / math.sin(math.radians(60)), 10.0, name="G2")
        doc.recompute()
        # minimum-area rectangles (equal areas on every side of a triangle: the smaller long side wins):
        # 200 x 100 for the right triangle; for the other one the side opposite the 60 degree corner,
        # sqrt(200^2 + 115.47^2 - 200 x 115.47) = 173.205, with the height 20000 / 173.205 = 115.470 on it
        want = {"G1": (200.0, 100.0), "G2": (100.0 * math.sqrt(3.0), 200.0 / math.sqrt(3.0))}
        for g in (g1, g2):
            self.assertEqual(g.State.count("Invalid"), 0, g.getStatusString())
            self.assertAlmostEqual(g.AreaMm2, 10000.0, places=6)
            self.assertAlmostEqual(g.OutlineLengthMm, want[g.Name][0], places=6)
            self.assertAlmostEqual(g.OutlineWidthMm, want[g.Name][1], places=6)
        rows = CL.plate_list([g1, g2])
        self.assertEqual([r["quantity"] for r in rows], [1, 1])

    def test_swapped_legs_are_the_same_plate(self):
        doc = FreeCAD.newDocument("K")
        fr = frames.Frame(doc)
        _joint(fr, "P", 0.0, 60)
        _joint(fr, "Q", 5000.0, 60)
        doc.recompute()
        g1 = WeldGusset.make_gusset(doc, fr.members["PA"], 0, "Start", fr.members["PB"], 0, "Start", 200.0, 150.0, 10.0, name="G1")
        # the same plate described from the other member: legs and members swapped
        g2 = WeldGusset.make_gusset(doc, fr.members["QB"], 0, "Start", fr.members["QA"], 0, "Start", 150.0, 200.0, 10.0, name="G2")
        doc.recompute()
        for g in (g1, g2):
            self.assertEqual(g.State.count("Invalid"), 0, g.getStatusString())
        self.assertLess(abs(g1.Shape.Volume - g2.Shape.Volume), 1e-6)
        self.assertAlmostEqual(g1.OutlineLengthMm, g2.OutlineLengthMm, places=6)
        self.assertAlmostEqual(g1.OutlineWidthMm, g2.OutlineWidthMm, places=6)
        rows = CL.plate_list([g1, g2])
        self.assertEqual([r["quantity"] for r in rows], [2])
        self.assertEqual(CL.plate_list([g2, g1]), rows)

    def test_min_area_rectangle_of_known_triangle(self):
        # triangle with vertices (0,0), (200,0), (0,100) in the x-z plane
        pts = [V(0, 0, 0), V(200, 0, 0), V(0, 0, 100)]
        lng, wid = WeldGusset.min_area_rect(pts, V(0, 1, 0), V(1, 0, 0))
        self.assertAlmostEqual(lng, 200.0, places=9)
        self.assertAlmostEqual(wid, 100.0, places=9)
        # turned by 30 degrees about y: same answer
        rot = FreeCAD.Rotation(V(0, 1, 0), 30)
        lng2, wid2 = WeldGusset.min_area_rect([rot.multVec(p) for p in pts], V(0, 1, 0), rot.multVec(V(1, 0, 0)))
        self.assertAlmostEqual(lng2, 200.0, places=9)
        self.assertAlmostEqual(wid2, 100.0, places=9)


class TestInvalidFeaturesRefused(unittest.TestCase):
    def tearDown(self):
        for n in list(FreeCAD.listDocuments()):
            FreeCAD.closeDocument(n)

    def test_invalid_features_refuse_lists(self):
        import WeldBOM
        from WeldGeom import WeldmentError
        from WTests.testFullCutList import plane_edges

        doc = FreeCAD.newDocument("I")
        fr = frames.Frame(doc)
        _joint(fr, "P", 0.0, 90)
        doc.recompute()
        good = WeldGusset.make_gusset(doc, fr.members["PA"], 0, "Start", fr.members["PB"], 0, "Start", 200.0, 200.0, 10.0, name="Good")
        doc.recompute()
        bad = WeldGusset.make_gusset(doc, fr.members["PA"], 0, "Start", fr.members["PB"], 0, "Start", 5000.0, 200.0, 10.0, name="Bad")
        doc.recompute()
        self.assertIn("Invalid", bad.State)
        members = [fr.members["PA"], fr.members["PB"]]
        for call in (lambda: CL.cut_list(members + [good, bad]), lambda: CL.plate_list([good, bad]),
                     lambda: WeldBOM.bom_rows(doc)):
            with self.assertRaises(WeldmentError) as cm:
                call()
            self.assertIn("Bad", str(cm.exception))
            self.assertNotIn("Good", str(cm.exception))
        # an invalid bead
        names = plane_edges(fr.members["PA"], 1, -50.0)   # none: no edge lies there; use any edge instead
        edge = "Edge1"
        bead = WeldBead.make_bead(doc, fr.members["PA"], fr.members["PB"], [(fr.members["PB"], edge)], 5.0, name="BadBead")
        doc.recompute()
        self.assertIn("Invalid", bead.State)
        with self.assertRaises(WeldmentError) as cm:
            WeldBead.weld_summary(doc)
        self.assertIn("BadBead", str(cm.exception))
        with self.assertRaises(WeldmentError):
            CL.cut_list(members + [bead])
        # repaired: the lists work again
        bad.Leg1 = 200.0
        doc.recompute()
        doc.removeObject("BadBead")
        self.assertEqual(sum(r["quantity"] for r in CL.plate_list([good, bad])), 2)
        self.assertTrue(WeldBOM.bom_rows(doc))


class TestInvalidDependencies(unittest.TestCase):
    """A bead or plate that is itself up to date but stands on an invalid body is refused too."""

    def tearDown(self):
        for n in list(FreeCAD.listDocuments()):
            FreeCAD.closeDocument(n)

    def test_bead_on_invalid_gusset_and_cap_on_invalid_member(self):
        import WeldBOM
        from WeldGeom import WeldmentError

        doc = FreeCAD.newDocument("D")
        fr = frames.Frame(doc)
        _joint(fr, "P", 0.0, 90)
        fr.add("M", "EN SHS", frames.pick("EN SHS", 0.5), [((3000, 0, 0), (3000, 0, 1200))])
        doc.recompute()
        g = WeldGusset.make_gusset(doc, fr.members["PA"], 0, "Start", fr.members["PB"], 0, "Start", 200.0, 200.0, 10.0, name="Gus")
        doc.recompute()
        edge = next("Edge%d" % (i + 1) for i, e in enumerate(g.Shape.Edges) if abs(e.Length - 200.0) < 1e-6)
        bead = WeldBead.make_bead(doc, fr.members["PA"], g, [(g, edge)], 5.0, name="Bd")
        cap = WeldEndCap.make_end_cap(doc, fr.members["M"], 0, "End", 8.0, name="Cap")
        doc.recompute()
        for o in (g, bead, cap):
            self.assertNotIn("Invalid", o.State, o.getStatusString())
        self.assertTrue(WeldBead.weld_summary(doc) and WeldBOM.bom_rows(doc))
        # the gusset fails (leg too long); the bead is not recomputed with an error of its own
        g.Leg1 = 5000.0
        doc.recompute()
        self.assertIn("Invalid", g.State)
        self.assertNotIn("Invalid", bead.State)
        for call in (lambda: WeldBead.weld_summary(doc), lambda: WeldBOM.bom_rows(doc),
                     lambda: CL.cut_list([bead]), lambda: CL.plate_list([g])):
            with self.assertRaises(WeldmentError) as cm:
                call()
            self.assertIn("Gus", str(cm.exception))
        # a cap on a member that fails
        g.Leg1 = 200.0
        fr.members["M"].Size = "no such size"
        doc.recompute()
        self.assertIn("Invalid", fr.members["M"].State)
        with self.assertRaises(WeldmentError) as cm:
            CL.plate_list([cap])
        self.assertIn("M", str(cm.exception))
        # touched-only features are read, not refused
        fr.members["M"].Size = frames.pick("EN SHS", 0.5)
        doc.recompute()
        g.Thickness = 12.0   # touched, not recomputed
        self.assertIn("Touched", g.State)
        self.assertTrue(WeldBOM.bom_rows(doc))
