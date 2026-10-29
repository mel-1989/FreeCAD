# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *                                                                         *
# *   This file is part of the FreeCAD CAx development system.              *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# ***************************************************************************

"""Straight edges trimmed by an inserted fillet or chamfer (cad-fork G01, slice 8).

A fillet or chamfer inserted upstream of a consumer trims the straight edges next to the rounded
or cut edges equally at both ends. By the D6 extent ruling the trimmed edge is the same element,
and the element map records it: the trimmed edge is the one Modified image (op code FLT or CHF)
of the stored name. These tests check that such a reference follows the edge, and that every
other change of the edge (a one-end trim, a split, the centre piece of a split, a merge into a
longer edge, an edge of a copy of the solid) is reported and never retargeted.
"""

import os
import tempfile
import unittest

import FreeCAD as App
import Part
from FreeCAD import Vector as V

from PartDesignTests.TestReferenceRescue import W, H, L, _add, _edge_sig, _rect, _sketch

R = 3.0  # fillet radius or chamfer size of the inserted feature: the trim at each end


def _edge_info(shape, name):
    e = shape.getElement(name)
    c = e.CenterOfMass
    return (round(c.x, 5), round(c.y, 5), round(c.z, 5)), round(e.Length, 5), e.Curve


class TestTrimmedEdgeRescue(unittest.TestCase):
    """A reference to a straight edge follows the edge through an equal two-end trim."""

    def setUp(self):
        self.doc = App.newDocument("TrimRescue." + self._testMethodName)
        self.body = self.doc.addObject("PartDesign::Body", "Body")

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    # -------------------------------------------------------------- builders
    def _pad(self):
        sk = _sketch(self.doc, self.body, "Sketch")
        _rect(sk, 0, 0, W, H)
        pad = self.body.newObject("PartDesign::Pad", "Pad")
        pad.Profile = sk
        pad.Length = L
        self.doc.recompute()
        return pad

    def _vertical_edges(self, shape, only=None):
        names = []
        for i, e in enumerate(shape.Edges):
            if not isinstance(e.Curve, Part.Line) or abs(abs(e.Curve.Direction.z) - 1) > 1e-9:
                continue
            c = e.CenterOfMass
            if only is None or (abs(c.x - only[0]) < 1e-6 and abs(c.y - only[1]) < 1e-6):
                names.append("Edge%d" % (i + 1))
        return names

    def _edge_at(self, shape, x, y, z):
        for i, e in enumerate(shape.Edges):
            c = e.CenterOfMass
            if abs(c.x - x) < 1e-6 and abs(c.y - y) < 1e-6 and abs(c.z - z) < 1e-6:
                return "Edge%d" % (i + 1)
        self.fail("no edge at %s" % ((x, y, z),))

    def _dressup(self, kind, base, edges, size, after=None):
        d = _add(self.doc, self.body, "PartDesign::" + kind, kind, after)
        d.Base = (base, edges)
        if kind == "Chamfer":
            d.Size = size
        else:
            d.Radius = size
        return d

    def _hole(self, after):
        """A through hole, away from the referenced edges: the consumer sits behind it."""
        hs = _sketch(self.doc, self.body, "HoleSketch", z=L, after=after)
        hs.addGeometry(Part.Circle(V(-10, 0, 0), V(0, 0, 1), 2.0), False)
        hp = _add(self.doc, self.body, "PartDesign::Pocket", "HolePocket", hs)
        hp.Profile = hs
        hp.Length = 2 * L
        self.body.Tip = hp  # an object inserted after the Pad leaves the Tip on the Pad
        return hp

    def _consumer(self, base, edge, size=1.0):
        down = self._dressup("Chamfer", base, [edge], size)
        self.doc.recompute()
        self.assertNotIn("Invalid", down.State)
        return down

    def _valid(self, *objs):
        for o in objs:
            self.assertNotIn("Invalid", o.State, o.Name + " is invalid")
            self.assertTrue(o.Shape.isValid() and not o.Shape.isNull(), o.Name + " has no valid shape")

    def _assert_trimmed(self, down, before_sig, trim=R):
        """The reference resolves to the same straight line, shortened by `trim` at both ends."""
        base, subs = down.Base
        self.assertFalse(subs[0].startswith("?"), "the reference went missing: %r" % (subs,))
        self.assertNotIn("Invalid", down.State)
        centre, length, curve = _edge_info(base.Shape, subs[0])
        self.assertIsInstance(curve, Part.Line)
        self.assertEqual(centre, before_sig[:3])
        self.assertAlmostEqual(length, before_sig[3] - 2 * trim, 5)

    def _assert_never_retargeted(self, down, before_sig):
        """The reference is reported missing, or still the very same edge."""
        base, subs = down.Base
        if subs[0].startswith("?"):
            self.assertIn("Invalid", down.State)
            return
        self.assertEqual(_edge_sig(base.Shape, subs[0]), before_sig, "%s was silently retargeted" % subs[0])

    def _assert_reported(self, down, before_sig):
        self.assertTrue(
            down.Base[1][0].startswith("?") and "Invalid" in down.State,
            "the reference was not reported missing: %r %r" % (down.Base[1], down.State),
        )

    # -------------------------------------------------------------- rescued: equal two-end trim
    def _trimmed_case(self, kind, point, behind_hole=False):
        pad = self._pad()
        edge = self._edge_at(pad.Shape, *point)
        before = _edge_sig(pad.Shape, edge)
        base = pad
        if behind_hole:
            base = self._hole(pad)
            self.doc.recompute()
            edge = self._edge_at(base.Shape, *point)
            before = _edge_sig(base.Shape, edge)
        down = self._consumer(base, edge)
        self._dressup(kind, pad, self._vertical_edges(pad.Shape), R, after=pad)
        self.doc.recompute()
        self._valid(down)
        self._assert_trimmed(down, before)

    def test_fillet_trims_top_edge(self):
        self._trimmed_case("Fillet", (0, H / 2, L))

    def test_chamfer_trims_top_edge(self):
        self._trimmed_case("Chamfer", (0, H / 2, L))

    def test_fillet_trims_bottom_edge(self):
        """The bottom edge is the sketch edge itself: a face generated from it also derives from its name."""
        self._trimmed_case("Fillet", (0, H / 2, 0))

    def test_chamfer_trims_bottom_edge(self):
        self._trimmed_case("Chamfer", (0, H / 2, 0))

    def test_fillet_trims_edge_of_a_feature_behind_the_inserted_one(self):
        """The consumer is not retargeted: its base keeps its place, the names under it change."""
        self._trimmed_case("Fillet", (0, H / 2, L), behind_hole=True)

    def test_chamfer_trims_edge_of_a_feature_behind_the_inserted_one(self):
        self._trimmed_case("Chamfer", (0, -H / 2, 0), behind_hole=True)

    # -------------------------------------------------------------- reported: every other change
    def test_one_end_trim_is_reported(self):
        pad = self._pad()
        edge = self._edge_at(pad.Shape, 0, H / 2, L)
        before = _edge_sig(pad.Shape, edge)
        down = self._consumer(pad, edge)
        one = self._vertical_edges(pad.Shape, only=(W / 2, H / 2))
        self.assertEqual(len(one), 1)
        self._dressup("Fillet", pad, one, R, after=pad)
        self.doc.recompute()
        self._assert_never_retargeted(down, before)
        self._assert_reported(down, before)

    def test_unequal_trim_is_reported(self):
        """Two vertical edges at the ends of the top edge, rounded with different radii."""
        pad = self._pad()
        edge = self._edge_at(pad.Shape, 0, H / 2, L)
        before = _edge_sig(pad.Shape, edge)
        down = self._consumer(pad, edge)
        a = self._vertical_edges(pad.Shape, only=(W / 2, H / 2))
        b = self._vertical_edges(pad.Shape, only=(-W / 2, H / 2))
        self._dressup("Fillet", pad, a, 3.0, after=pad)
        self._dressup("Fillet", self.doc.getObject("Fillet"), b, 5.0, after=self.doc.getObject("Fillet"))
        self.doc.recompute()
        self._assert_never_retargeted(down, before)

    def _slot(self, after, xs, name):
        """A through slot across the top +Y edge at x in [xs[0], xs[1]]."""
        sk = _sketch(self.doc, self.body, name + "Sk", z=L, after=after)
        _rect(sk, (xs[0] + xs[1]) / 2, H / 2, xs[1] - xs[0], 6.0)
        pk = _add(self.doc, self.body, "PartDesign::Pocket", name, sk)
        pk.Profile = sk
        pk.Length = 2 * L
        return pk

    def test_split_is_reported(self):
        pad = self._pad()
        edge = self._edge_at(pad.Shape, 0, H / 2, L)
        before = _edge_sig(pad.Shape, edge)
        down = self._consumer(pad, edge)
        self._dressup("Fillet", pad, self._vertical_edges(pad.Shape), R, after=pad)
        self.doc.recompute()
        self._assert_trimmed(down, before)
        self._slot(self.doc.getObject("Fillet"), (5.0, 9.0), "Slot")
        self.doc.recompute()
        self._assert_never_retargeted(down, _edge_sig(down.Base[0].Shape, down.Base[1][0]) if False else _trimmed_sig(before))

    def test_centre_piece_of_a_split_is_reported(self):
        """Two slots leave a centre piece with the old midpoint: it is a split, not the same edge."""
        pad = self._pad()
        edge = self._edge_at(pad.Shape, 0, H / 2, L)
        before = _edge_sig(pad.Shape, edge)
        down = self._consumer(pad, edge)
        s1 = self._slot(pad, (-14.0, -10.0), "SlotA")
        self._slot(s1, (10.0, 14.0), "SlotB")
        self.doc.recompute()
        self._assert_never_retargeted(down, before)
        self._assert_reported(down, before)

    def test_merge_into_a_longer_edge_is_reported(self):
        """A block fused on the end of the plate continues the top edge: it becomes one longer edge."""
        pad = self._pad()
        edge = self._edge_at(pad.Shape, 0, H / 2, L)
        before = _edge_sig(pad.Shape, edge)
        down = self._consumer(pad, edge)
        bs = _sketch(self.doc, self.body, "BlockSk", after=pad)
        _rect(bs, W / 2 + 5.0, 0, 10.0, H)
        bp = _add(self.doc, self.body, "PartDesign::Pad", "Block", bs)
        bp.Profile = bs
        bp.Length = L
        self.doc.recompute()
        self._assert_never_retargeted(down, before)

    def test_fillet_then_merge_is_reported(self):
        """The trim by the fillet is followed by a merge into a longer edge: still not the same element."""
        pad = self._pad()
        edge = self._edge_at(pad.Shape, 0, H / 2, L)
        before = _edge_sig(pad.Shape, edge)
        down = self._consumer(pad, edge)
        fil = self._dressup("Fillet", pad, self._vertical_edges(pad.Shape), R, after=pad)
        self.doc.recompute()
        self._assert_trimmed(down, before)
        bs = _sketch(self.doc, self.body, "BlockSk", after=fil)
        _rect(bs, W / 2 + 5.0, 0, 10.0, H)
        bp = _add(self.doc, self.body, "PartDesign::Pad", "Block", bs)
        bp.Profile = bs
        bp.Length = L
        self.doc.recompute()
        self._assert_never_retargeted(down, _trimmed_sig(before))

    # -------------------------------------------------------------- copies are other elements
    def _copies_case(self, kind):
        """A Compound of the solid and a copy of it lying on the same place: the consumer's edge
        belongs to the first child, and must never end up on the second one's edge."""
        pad = self._pad()
        box = self.doc.addObject("Part::Feature", "Src")
        box.Shape = pad.Shape.copy()
        doc = self.doc
        a = doc.addObject("App::Link", "A")
        a.LinkedObject = self.body
        if kind == "link":
            b = doc.addObject("App::Link", "B")
            b.LinkedObject = self.body
        elif kind == "mirror":
            b = doc.addObject("Part::Mirroring", "B")
            b.Source = self.body
            b.Normal = V(1, 0, 0)  # the plate is symmetric about x = 0: its mirror lies on it
            b.Base = V(0, 0, 0)
        else:
            b = doc.addObject("Part::Feature", "B")
            b.Shape = self.body.Shape.copy()
        comp = doc.addObject("Part::Compound", "Comp")
        comp.Links = [a, b]
        doc.recompute()
        solids = comp.Shape.Solids
        self.assertEqual(len(solids), 2)
        name = None
        for i, e in enumerate(comp.Shape.Edges):
            c = e.CenterOfMass
            if abs(c.x) < 1e-6 and abs(c.y - H / 2) < 1e-6 and abs(c.z - L) < 1e-6 and any(e.isSame(x) for x in solids[0].Edges):
                name = "Edge%d" % (i + 1)
                break
        self.assertIsNotNone(name)
        cons = doc.addObject("App::FeaturePython", "Consumer")
        cons.addProperty("App::PropertyLinkSub", "Ref")
        cons.Ref = (comp, [name])
        doc.recompute()
        self._dressup("Fillet", pad, self._vertical_edges(pad.Shape), R, after=pad)
        doc.recompute()
        sub = cons.Ref[1][0] if cons.Ref and cons.Ref[1] else None
        if sub and not sub.startswith("?"):
            ed = comp.Shape.getElement(sub)
            self.assertTrue(
                any(ed.isSame(x) for x in comp.Shape.Solids[0].Edges),
                "the reference moved to an edge of the copy (%s)" % sub,
            )
        return cons, comp

    def test_link_copy_is_never_taken(self):
        self._copies_case("link")

    def test_mirror_copy_is_never_taken(self):
        self._copies_case("mirror")

    def test_compound_of_two_identical_solids_never_takes_the_other(self):
        self._copies_case("frozen")

    # -------------------------------------------------------------- undo, redo, delete, reload
    def _inserted(self, behind_hole=False):
        pad = self._pad()
        base = pad
        if behind_hole:
            base = self._hole(pad)
            self.doc.recompute()
        edge = self._edge_at(base.Shape, 0, H / 2, L)
        before = _edge_sig(base.Shape, edge)
        down = self._consumer(base, edge)
        state = (down.Shape.Volume, list(down.Base[1]), down.Base[0])
        return pad, base, down, before, state

    def _insert_fillet(self, pad):
        fil = self._dressup("Fillet", pad, self._vertical_edges(pad.Shape), R, after=pad)
        self.doc.recompute()
        return fil

    def test_undo_and_redo_of_the_inserted_fillet(self):
        for behind in (False, True):
            with self.subTest(behind_hole=behind):
                App.closeDocument(self.doc.Name)
                self.setUp()
                pad, base, down, before, (volume, subs, tgt) = self._inserted(behind)
                self.doc.openTransaction("insert fillet")
                fil = self._insert_fillet(pad)
                self.doc.commitTransaction()
                self._assert_trimmed(down, before)
                self.doc.undo()
                self.doc.recompute()
                self.assertIsNone(self.doc.getObject("Fillet"))
                self.assertNotIn("Invalid", down.State)
                self.assertEqual(list(down.Base[1]), subs)
                self.assertAlmostEqual(down.Shape.Volume, volume, 5)
                self.doc.redo()
                self.doc.recompute()
                self._assert_trimmed(down, before)

    def test_deleting_the_inserted_fillet_restores_the_reference(self):
        for behind in (False, True):
            with self.subTest(behind_hole=behind):
                App.closeDocument(self.doc.Name)
                self.setUp()
                pad, base, down, before, (volume, subs, tgt) = self._inserted(behind)
                fil = self._insert_fillet(pad)
                self._assert_trimmed(down, before)
                self.body.removeObject(fil)
                self.doc.removeObject(fil.Name)
                self.doc.recompute()
                self._valid(down)
                self.assertIs(down.Base[0], tgt)
                self.assertEqual(list(down.Base[1]), subs)
                self.assertAlmostEqual(down.Shape.Volume, volume, 5)

    def test_save_and_reload_keeps_the_rescued_reference(self):
        pad, base, down, before, (volume, subs, tgt) = self._inserted()
        self._insert_fillet(pad)
        self._assert_trimmed(down, before)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "trim.FCStd")
            self.doc.saveAs(path)
            App.closeDocument(self.doc.Name)
            self.doc = App.openDocument(path)
            down2 = self.doc.getObject("Chamfer")
            self.doc.recompute()
            self._valid(down2)
            self._assert_trimmed(down2, before)
            # and the inserted feature can still be taken out again
            fil = self.doc.getObject("Fillet")
            self.doc.getObject("Body").removeObject(fil)
            self.doc.removeObject(fil.Name)
            self.doc.recompute()
            self._valid(down2)
            self.assertEqual(list(down2.Base[1]), subs)
            self.assertAlmostEqual(down2.Shape.Volume, volume, 5)


def _trimmed_sig(before):
    """The signature the reference had after the fillet: the same line shortened by R at both ends."""
    return (before[0], before[1], before[2], round(before[3] - 2 * R, 5), before[4])
