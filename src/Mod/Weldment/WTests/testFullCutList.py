# SPDX-License-Identifier: LGPL-2.1-or-later
"""Acceptance check 4 (slice 5b): the cut list with gussets, end caps and weld beads, against goldens.

The portal and the pitched truss of ``WTests/frames.py`` are extended here with gussets, end caps and
fillet welds; their cut lists (members, plates, welds) are compared byte for byte with
``golden/portal_full_cutlist.csv`` and ``golden/truss_full_cutlist.csv`` and must not depend on the
order the objects are passed in. A few numbers are re-derived by hand (weld totals from the chosen
edges, cap mass from the analytic area), independently of the feature code. Regenerate the goldens
with ``WELDMENT_REGEN_GOLDEN=1`` (see ``WTests/frames.py``) and review the diff.
"""

import math
import random
import unittest

import FreeCAD

import WeldBead
import WeldCutList as CL
import WeldEndCap
import WeldGusset
from WTests import frames, oracle
from WTests.testCutList import _golden_check

V = FreeCAD.Vector


def plane_edges(obj, axis, value, near=None, radius=400.0):
    """Names of the straight edges of obj whose two ends lie in the plane axis = value (axis 0, 1, 2),
    optionally within radius of the point near."""
    out = []
    for i, e in enumerate(obj.Shape.Edges):
        a, b = e.valueAt(e.FirstParameter), e.valueAt(e.LastParameter)
        if abs(e.Length - (b - a).Length) > 1e-9:
            continue
        if abs(a[axis] - value) > 1e-6 or abs(b[axis] - value) > 1e-6:
            continue
        if near is not None and (e.CenterOfMass - near).Length > radius:
            continue
        out.append((i, e))
    if not out:
        return []
    # the outer outline only: the edges farthest from the centre of the end face (the bore edges sit inside)
    cen = V(sum(e.CenterOfMass.x for _, e in out) / len(out), sum(e.CenterOfMass.y for _, e in out) / len(out),
            sum(e.CenterOfMass.z for _, e in out) / len(out))
    far = max((e.CenterOfMass - cen).Length for _, e in out)
    return ["Edge%d" % (i + 1) for i, e in out if (e.CenterOfMass - cen).Length > far - 1e-6]


def portal_full(doc):
    fr = frames.portal_frame(doc)
    doc.recompute()
    cols, rafters, tie = fr.heads["Columns"], fr.heads["Rafters"], fr.heads["Tie"]
    objs = list(fr.final())
    eaves = [WeldGusset.make_gusset(doc, cols, i, "End", rafters, i, "Start", 300.0, 250.0, 10.0, 20.0, name="Eave%d" % i)
             for i in (0, 1)]
    caps = [WeldEndCap.make_end_cap(doc, cols, i, "Start", 8.0, name="Base%d" % i) for i in (0, 1)]
    caps.append(WeldEndCap.make_end_cap(doc, fr.heads["Tie"], 0, "Start", 5.0, "Inset", 2.0, 0.0, name="TieCap"))
    doc.recompute()
    xmin, xmax = tie.Shape.BoundBox.XMin, tie.Shape.BoundBox.XMax
    welds = []
    for name, x, z in (("TieL", xmin, 5.0), ("TieR", xmax, 6.0)):
        names = plane_edges(tie, 0, x)
        welds.append((make_bead(doc, cols, tie, names, z, name), names, z))
    doc.recompute()
    return fr, objs + eaves + caps + [w[0] for w in welds], eaves, caps, welds


def make_bead(doc, body1, body2, names, z, name):
    return WeldBead.make_bead(doc, body1, body2, [(body2, n) for n in names], z, name=name)


def truss_full(doc):
    fr = frames.pitched_truss(doc)
    half, rise = 4000.0, 1600.0
    fam = "EN SHS"
    fr.add("Mast", fam, frames.pick(fam, 0.2), [((half, 0, rise), (half, 0, rise + 600.0))])
    doc.recompute()
    chords, web, mast = fr.heads["Chords"], fr.heads["Web"], fr.heads["Mast"]
    objs = list(fr.final())
    gus = [
        WeldGusset.make_gusset(doc, chords, 0, "Start", chords, 1, "Start", 250.0, 250.0, 8.0, 0.0, name="EaveL"),
        WeldGusset.make_gusset(doc, chords, 0, "End", chords, 2, "Start", 250.0, 250.0, 8.0, 0.0, name="EaveR"),
    ]
    prof = oracle.kind_profile("RHS", 0.2)
    caps = [WeldEndCap.make_end_cap(doc, mast, 0, "End", 6.0, "Outset", 0.0, prof.dims["ro"] + 2.0, name="MastCap")]
    doc.recompute()
    welds = []
    for name, seg, z in (("Web0", 0, 2.5), ("Web1", 1, 2.0)):
        solid = web.Shape.Solids[seg]
        plane = min(v.Point.z for v in solid.Vertexes)
        near = next(v.Point for v in solid.Vertexes if abs(v.Point.z - plane) < 1e-6)
        names = plane_edges(web, 2, plane, near, 150.0)
        welds.append((make_bead(doc, chords, web, names, z, name), names, z))
    doc.recompute()
    return fr, objs + gus + caps + [w[0] for w in welds], gus, caps, welds


def edge_total(obj, names):
    return sum(obj.Shape.getElement(n).Length for n in names)


class TestFullCutList(unittest.TestCase):
    def tearDown(self):
        for n in list(FreeCAD.listDocuments()):
            FreeCAD.closeDocument(n)

    def _checked(self, build):
        doc = FreeCAD.newDocument("Full")
        fr, objs, plates, caps, welds = build(doc)
        for o in objs:
            self.assertNotIn("Invalid", o.State, "%s: %s" % (o.Name, o.getStatusString()))
        return doc, fr, objs, plates, caps, welds

    def _order(self, objs, text):
        rnd = random.Random(11)
        for _ in range(4):
            shuffled = list(objs)
            rnd.shuffle(shuffled)
            self.assertEqual(CL.to_csv(CL.cut_list(shuffled)), text)

    def _sections(self, text):
        out, cur = [], None
        for line in text.split("\n")[:-1]:
            if not line:
                cur = None
            elif cur is None:
                cur = [line]
                out.append(cur)
            else:
                cur.append(line)
        return out

    def test_portal_full(self):
        doc, fr, objs, gus, caps, welds = self._checked(portal_full)
        text = CL.to_csv(CL.cut_list(objs))
        _golden_check(self, "portal_full_cutlist.csv", text)
        self._order(objs, text)
        sec = self._sections(text)
        self.assertEqual([len(s) - 1 for s in sec], [3, 3, 2])   # member items, plate items, weld sizes
        self.assertEqual(sec[2][0], ",".join(CL.WELD_COLUMNS))
        self._hand_numbers(objs, caps, welds)

    def test_truss_full(self):
        doc, fr, objs, gus, caps, welds = self._checked(truss_full)
        text = CL.to_csv(CL.cut_list(objs))
        _golden_check(self, "truss_full_cutlist.csv", text)
        self._order(objs, text)
        sec = self._sections(text)
        self.assertEqual(len(sec), 3)
        self._hand_numbers(objs, caps, welds)

    def _hand_numbers(self, objs, caps, welds):
        rows = CL.cut_list(objs)
        wrows = [r for r in rows if r.get("kind") == "weld"]
        prows = [r for r in rows if r.get("kind") == "plate"]
        # welds: one row per leg size; totals are the sums of the chosen edges; mass z^2/2 L at 7850 kg/m^3
        sizes = sorted({w[2] for w in welds})
        self.assertEqual([r["leg_z_mm"] for r in wrows], sizes)
        for r, z in zip(wrows, sizes):
            chosen = [w for w in welds if w[2] == z]
            total = sum(edge_total(w[0].Body2, w[1]) for w in chosen)
            self.assertLess(abs(r["total_length_mm"] - total), 1e-6)
            self.assertEqual(r["bead_count"], len(chosen))
            self.assertAlmostEqual(r["throat_a_mm"], z / math.sqrt(2.0), places=9)
            self.assertLess(abs(r["mass_kg"] - z * z / 2.0 * total * 7850e-9), 1e-9)
        # caps: mass = analytic outline area x thickness x density
        for c in caps:
            row = next(r for r in prows if c.Name in r["marks"])
            prof = WeldEndCap.segment_specs(WeldEndCap.root_member(c.Base))[0].profile
            d = prof.dims
            if c.Mode == "Outset":
                w, h, r_ = d["b"], d["h"], d["ro"]
            else:
                w, h, r_ = d["b"] - 2 * d["t"], d["h"] - 2 * d["t"], d["ri"]
            k = c.Chamfer.Value
            area = w * h - 2 * k * k if k else w * h - (4 - math.pi) * r_ * r_
            each = area * c.Thickness.Value * 7850e-9
            self.assertLess(abs(row["mass_each_kg"] - each) / each, 1e-9)
