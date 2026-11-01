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
