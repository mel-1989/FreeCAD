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

"""Extent rescue of straight edges (cad-fork G01, slice 7a).

D6 extent ruling: a straight edge is the same element after an edit if it stays on the same
carrier line with the same midpoint (unchanged, or trimmed equally at both ends, as by a fillet
of both corners). An edge shortened at one end, a fragment merged into a longer edge, or any
piece of a split edge (even a centre piece that keeps the old midpoint) is a different element and
the reference must be reported, never retargeted. The link layer's rescue accepts a new edge only
if it is the one edge of the shape on that carrier overlapping the old extent.
"""

import math
import unittest

import FreeCAD as App
import Part
import Sketcher
from FreeCAD import Vector as V

from PartDesignTests.TestReferenceRescue import _add, _plane, _rect, _sketch

W, H, L = 40.0, 20.0, 10.0
TOP_Y = V(0, H / 2, L)  # centre of the +Y top edge of the pad (an X-direction edge of length W)


def _find_edge(shape, centre, length, tol=1e-6):
    for i, e in enumerate(shape.Edges):
        if isinstance(e.Curve, Part.Line) and abs(e.Length - length) < tol and (e.CenterOfMass - centre).Length < tol:
            return "Edge%d" % (i + 1)
    return None


class TestExtentRescue(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("ExtentRescue." + self._testMethodName)
        self.body = self.doc.addObject("PartDesign::Body", "Body")
        sk = _sketch(self.doc, self.body, "Sketch")
        _rect(sk, 0, 0, W, H)
        self.pad = self.body.newObject("PartDesign::Pad", "Pad")
        self.pad.Profile = sk
        self.pad.Length = L
        self.doc.recompute()
        name = _find_edge(self.pad.Shape, TOP_Y, W)
        self.assertIsNotNone(name)
        self.chamfer = self.body.newObject("PartDesign::Chamfer", "Chamfer")
        self.chamfer.Base = (self.pad, [name])
        self.chamfer.Size = 0.5
        self.doc.recompute()
        self._ok(self.chamfer)

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    # ------------------------------------------------------------ edits (all inserted after the pad)
    def _fillet_vertical(self, radius=3.0):
        edges = [
            "Edge%d" % (i + 1)
            for i, e in enumerate(self.pad.Shape.Edges)
            if isinstance(e.Curve, Part.Line) and abs(e.Curve.Direction.z) > 1 - 1e-9
        ]
        self.assertEqual(len(edges), 4)
        fil = _add(self.doc, self.body, "PartDesign::Fillet", "VFillet", self.pad)
        fil.Base = (self.pad, edges)
        fil.Radius = radius
        return fil

    def _notches(self, name, *x_ranges):
        """A through pocket of full-width notches at the given x ranges; each cuts the +Y top edge."""
        sk = _sketch(self.doc, self.body, name + "Sketch", z=L, after=self.pad)
        n = 0
        for x0, x1 in x_ranges:
            pts = [V(x0, -H, 0), V(x1, -H, 0), V(x1, H, 0), V(x0, H, 0)]
            for i in range(4):
                sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
            for i in range(4):
                sk.addConstraint(Sketcher.Constraint("Coincident", n + i, 2, n + (i + 1) % 4, 1))
            n += 4
        pk = _add(self.doc, self.body, "PartDesign::Pocket", name, sk)
        pk.Profile = sk
        pk.Length = 2 * L
        return pk

    # ------------------------------------------------------------ assertions
    def _ok(self, *objs):
        for o in objs:
            self.assertNotIn("Invalid", o.State, o.Name + " is invalid")
            self.assertNotIn("Error", o.State, o.Name + " has an error")

    def _reported(self):
        """The reference is reported: the consumer is invalid or its sub-name carries the missing mark."""
        sub = self.chamfer.Base[1][0]
        reported = sub.startswith("?") or "Invalid" in self.chamfer.State or "Error" in self.chamfer.State
        self.assertTrue(reported, "the reference silently resolves to %r" % sub)

    def _points_at_top_edge(self, length):
        base, subs = self.chamfer.Base
        self.assertEqual(len(subs), 1)
        self.assertFalse(subs[0].startswith("?"), "reference went missing: %r" % (subs,))
        e = base.Shape.getElement(subs[0])
        self.assertIsInstance(e.Curve, Part.Line)
        self.assertAlmostEqual(e.Length, length, places=5)
        self.assertLess((e.CenterOfMass - TOP_Y).Length, 1e-5, "not the same midpoint")

    # ------------------------------------------------------------ tests
    def test_trimmed_equally_at_both_ends_is_rescued(self):
        fil = self._fillet_vertical(3.0)
        self.doc.recompute()
        self._ok(fil, self.chamfer)
        self.assertIs(self.chamfer.Base[0], fil)
        self._points_at_top_edge(W - 6.0)

    def test_unchanged_edge_still_resolves(self):
        self._notches("Far", (30.0, 34.0))  # cuts nothing: outside the plate
        self.doc.recompute()
        self._ok(self.chamfer)
        self._points_at_top_edge(W)

    def test_shortened_at_one_end_is_reported(self):
        self._notches("EndNotch", (16.0, 25.0))
        self.doc.recompute()
        self._reported()

    def test_split_in_two_pieces_is_reported(self):
        self._notches("MidNotch", (-2.0, 2.0))
        self.doc.recompute()
        self._reported()

    def test_centre_piece_of_a_split_keeping_the_midpoint_is_reported(self):
        pk = self._notches("TwoNotches", (-14.0, -10.0), (10.0, 14.0))
        self.doc.recompute()
        # the attack is real: the centre piece [-10, 10] exists with the old midpoint
        self.assertIsNotNone(_find_edge(pk.Shape, TOP_Y, 20.0))
        pieces = [e for e in pk.Shape.Edges if isinstance(e.Curve, Part.Line)
                  and abs(e.CenterOfMass.y - H / 2) < 1e-6 and abs(e.CenterOfMass.z - L) < 1e-6
                  and abs(e.Curve.Direction.x) > 0.999]
        self.assertEqual(len(pieces), 3)
        self._reported()

    def test_centre_piece_alone_after_trimming_both_ends_is_the_same_element(self):
        # one notch pair that removes both ends entirely leaves the centre piece as the only
        # edge on the carrier: trimmed equally at both ends, so by the ruling the same element
        pk = self._notches("EndsGone", (-25.0, -10.0), (10.0, 25.0))
        self.doc.recompute()
        self._ok(pk, self.chamfer)
        self._points_at_top_edge(20.0)

    def test_split_after_a_rescue_is_still_reported(self):
        fil = self._fillet_vertical(3.0)
        self.doc.recompute()
        self._ok(self.chamfer)
        self._points_at_top_edge(W - 6.0)
        self._notches("LateNotches", (-12.0, -8.0), (8.0, 12.0))
        self.doc.recompute()
        self._reported()

    # ------------------------------------------------------------ repair round: verifier findings
    def _rebate(self):
        sk = _sketch(self.doc, self.body, "RebateSketch", z=L, after=self.pad)
        _rect(sk, 0, H / 2, W + 10, 4)
        pk = _add(self.doc, self.body, "PartDesign::Pocket", "Rebate", sk)
        pk.Profile = sk
        pk.Length = 2.0
        return pk

    def test_a_new_edge_between_other_faces_is_not_the_same_element(self):
        # a rebate removes the top +Y edge; a block behind it brings a centred edge on the same
        # carrier whose side face looks the other way
        rb = self._rebate()
        bs = _sketch(self.doc, self.body, "BlockSketch", z=0.001, after=rb)
        _rect(bs, 0, H / 2 + 2.5, 10, 5)
        blk = _add(self.doc, self.body, "PartDesign::Pad", "Block", bs)
        blk.Profile = bs
        blk.Length = L - 0.001
        self.doc.recompute()
        self._reported()

    def test_an_edge_that_already_existed_before_the_edit_is_not_taken(self):
        doc = App.newDocument("ExtentCompound")
        try:
            a = doc.addObject("Part::Box", "A")
            a.Length, a.Width, a.Height = 40, 20, 10
            t = doc.addObject("Part::Box", "Tool")
            t.Length, t.Width, t.Height = 50, 4, 4
            t.Placement.Base = V(-5, 100, 100)
            cut = doc.addObject("Part::Cut", "CutA")
            cut.Base, cut.Tool = a, t
            b = doc.addObject("Part::Box", "B")
            b.Length, b.Width, b.Height = 10, 5, 5
            b.Placement.Base = V(15, 20.0, 10.0)
            c = doc.addObject("Part::Compound", "Comp")
            c.Links = [cut, b]
            doc.recompute()

            def on(e):
                return (isinstance(e.Curve, Part.Line) and abs(e.CenterOfMass.y - 20) < 1e-6
                        and abs(e.CenterOfMass.z - 10) < 1e-6)

            nm = ["Edge%d" % (i + 1) for i, e in enumerate(c.Shape.Edges) if on(e) and abs(e.Length - 40) < 1e-6][0]
            f = doc.addObject("App::FeaturePython", "Consumer")
            f.addProperty("App::PropertyLinkSub", "Ref")
            f.Ref = (c, [nm])
            doc.recompute()
            t.Placement.Base = V(-5, 18, 8)  # a rebate removes A's edge; only B's is left on the carrier
            doc.recompute()
            sub = f.Ref[1][0] if f.Ref and f.Ref[1] else "?"
            self.assertTrue(sub.startswith("?"), "the reference silently resolves to %r" % sub)
        finally:
            App.closeDocument(doc.Name)

    def test_an_asymmetric_trim_below_the_old_tolerance_is_reported(self):
        # shortened at one end by 1.9e-5 mm: the midpoint moves by less than 1e-5
        sk = _sketch(self.doc, self.body, "SlipSketch", z=L, after=self.pad)
        _rect(sk, W / 2 + 5 - 1.9e-5, 0, 10, H + 10)
        pk = _add(self.doc, self.body, "PartDesign::Pocket", "Slip", sk)
        pk.Profile = sk
        pk.Length = 2 * L
        self.doc.recompute()
        self._reported()

    def _delete(self, feature):
        self.body.removeObject(feature)
        self.doc.removeObject(feature.Name)

    def _points_at_pad_top_edge(self):
        base, subs = self.chamfer.Base
        self.assertIs(base, self.pad)
        self.assertFalse(subs[0].startswith("?"), "reference went missing: %r" % (subs,))
        e = base.Shape.getElement(subs[0])
        self.assertAlmostEqual(e.Length, W, places=5)
        self.assertLess((e.CenterOfMass - TOP_Y).Length, 1e-5)

    def test_deleting_the_inserting_feature_gives_the_original_edge_back(self):
        fil = self._fillet_vertical(3.0)
        self.doc.recompute()
        self._points_at_top_edge(W - 6.0)
        self._delete(fil)
        self.doc.recompute()
        self._ok(self.chamfer)
        self._points_at_pad_top_edge()

    def test_a_rescue_survives_undo_redo_and_an_unrelated_undo_after_the_restore(self):
        self.doc.UndoMode = 1
        self.doc.openTransaction("insert")
        fil = self._fillet_vertical(3.0)
        self.doc.recompute()
        self.doc.commitTransaction()
        self._points_at_top_edge(W - 6.0)
        self.doc.undo()
        self.doc.recompute()
        self._ok(self.chamfer)
        self._points_at_pad_top_edge()
        self.doc.redo()
        self.doc.recompute()
        self._points_at_top_edge(W - 6.0)
        self._delete(self.doc.getObject("VFillet"))
        self.doc.recompute()
        self._points_at_pad_top_edge()
        # an unrelated edit in a transaction, then its undo
        self.doc.openTransaction("unrelated")
        self.pad.Length = L + 1.0
        self.doc.recompute()
        self.doc.commitTransaction()
        self.doc.undo()
        self.doc.recompute()
        self._ok(self.chamfer)
        self._points_at_pad_top_edge()

    # ------------------------------------------------------------ repair round 2
    def _round_corner(self, convex):
        """A body whose vertical edge through the origin lies between the plane x=0 and an R1 round;
        a pocket removes the corner and a pad rebuilds it with the round on the other side (convex)
        or the same side (concave). Returns the document, the consumer and the body."""
        doc = App.newDocument("ExtentRound")
        body = doc.addObject("PartDesign::Body", "Body")
        r = 1.0

        def arc(c, a0, a1):
            return Part.ArcOfCircle(Part.Circle(c, V(0, 0, 1), r), a0, a1)

        def seg(a, b):
            return Part.LineSegment(V(*a, 0), V(*b, 0))

        sk = _sketch(doc, body, "Sketch")
        for g in [seg((-10, -10), (0, -10)), seg((0, -10), (0, 0)), arc(V(r, 0, 0), math.pi / 2, math.pi),
                  seg((r, r), (r, 10)), seg((r, 10), (-10, 10)), seg((-10, 10), (-10, -10))]:
            sk.addGeometry(g, False)
        pad = body.newObject("PartDesign::Pad", "Pad")
        pad.Profile = sk
        pad.Length = L
        doc.recompute()
        nm = None
        for i, e in enumerate(body.Shape.Edges):
            if isinstance(e.Curve, Part.Line) and abs(e.CenterOfMass.x) < 1e-6 and abs(e.CenterOfMass.y) < 1e-6:
                nm = "Edge%d" % (i + 1)
        f = doc.addObject("App::FeaturePython", "Consumer")
        f.addProperty("App::PropertyLinkSub", "Ref")
        f.Ref = (body, [nm])
        doc.recompute()
        ps = _sketch(doc, body, "PocketSk", z=L, after=pad)
        for g in [seg((-3, -3), (3, -3)), seg((3, -3), (3, 3)), seg((3, 3), (-3, 3)), seg((-3, 3), (-3, -3))]:
            ps.addGeometry(g, False)
        pk = _add(doc, body, "PartDesign::Pocket", "Pocket", ps)
        pk.Profile = ps
        pk.Length = L + 5
        ns = _sketch(doc, body, "NewSk", z=1.0, after=pk)
        if convex:
            geo = [seg((-3, -3), (0, -3)), seg((0, -3), (0, 0)), arc(V(-r, 0, 0), 0, math.pi / 2),
                   seg((-r, r), (-3, r)), seg((-3, r), (-3, -3))]
        else:
            geo = [seg((-3, -3), (0, -3)), seg((0, -3), (0, 0)), arc(V(r, 0, 0), math.pi / 2, math.pi),
                   seg((r, r), (r, 3)), seg((r, 3), (-3, 3)), seg((-3, 3), (-3, -3))]
        for g in geo:
            ns.addGeometry(g, False)
        npad = _add(doc, body, "PartDesign::Pad", "NewPad", ns)
        npad.Profile = ns
        npad.Length = L - 2.0
        body.Tip = npad
        doc.recompute()
        return doc, f

    def test_a_round_on_the_other_side_of_the_edge_is_not_the_same_element(self):
        # a concave R1 round centred (1,0) and a convex R1 round centred (-1,0) once encoded alike
        doc, f = self._round_corner(convex=True)
        try:
            sub = f.Ref[1][0] if f.Ref and f.Ref[1] else "?"
            self.assertTrue(sub.startswith("?"), "the reference silently resolves to %r" % sub)
        finally:
            App.closeDocument(doc.Name)

    def _compound_with_moving_box(self, join):
        doc = App.newDocument("ExtentCompound2")
        try:
            a = doc.addObject("Part::Box", "A")
            a.Length, a.Width, a.Height = 40, 20, 10
            t = doc.addObject("Part::Box", "Tool")
            t.Length, t.Width, t.Height = 50, 4, 4
            t.Placement.Base = V(-5, 100, 100)
            cut = doc.addObject("Part::Cut", "CutA")
            cut.Base, cut.Tool = a, t
            b = doc.addObject("Part::Box", "B")
            b.Length, b.Width, b.Height = 10, 5, 5
            b.Placement.Base = V(15, 100, 0)
            c = doc.addObject("Part::Compound", "Comp")
            c.Links = [cut] if join else [cut, b]
            doc.recompute()

            def on(e):
                return (isinstance(e.Curve, Part.Line) and abs(e.CenterOfMass.y - 20) < 1e-6
                        and abs(e.CenterOfMass.z - 10) < 1e-6)

            nm = ["Edge%d" % (i + 1) for i, e in enumerate(c.Shape.Edges) if on(e) and abs(e.Length - 40) < 1e-6][0]
            f = doc.addObject("App::FeaturePython", "Consumer")
            f.addProperty("App::PropertyLinkSub", "Ref")
            f.Ref = (c, [nm])
            doc.recompute()
            # one recompute: a rebate removes A's edge and box B arrives with its top +Y edge on the
            # carrier, centred, between faces on the same planes with the material on the same sides
            t.Placement.Base = V(-5, 16, 6)
            b.Placement.Base = V(15, 15, 5)
            if join:
                c.Links = [cut, b]
            doc.recompute()
            sub = f.Ref[1][0] if f.Ref and f.Ref[1] else "?"
            self.assertTrue(sub.startswith("?"), "the reference silently resolves to %r" % sub)
        finally:
            App.closeDocument(doc.Name)

    def test_an_edge_of_another_solid_that_moves_onto_the_carrier_is_not_taken(self):
        self._compound_with_moving_box(join=False)

    def test_an_edge_of_a_solid_that_joins_in_the_same_recompute_is_not_taken(self):
        self._compound_with_moving_box(join=True)

    # ------------------------------------------------------------ repair round 3
    def _compound_with_copy(self, src, join):
        """X = Cut(E, T) and a copy Y of a symmetrically end-trimmed E in a compound; one recompute
        removes X's top +Y edge with a rebate while Y arrives on its carrier (or joins the compound)."""
        doc = App.newDocument("ExtentCopy")
        try:
            pl = doc.addObject("Part::Plane", "Rect")
            pl.Length, pl.Width = 40, 20
            e = doc.addObject("Part::Extrusion", "E")
            e.Base, e.Dir, e.Solid, e.LengthFwd = pl, V(0, 0, 10), True, 10
            t = doc.addObject("Part::Box", "Tool")
            t.Length, t.Width, t.Height = 50, 4, 4
            t.Placement.Base = V(-5, 300, 300)
            x = doc.addObject("Part::Cut", "X")
            x.Base, x.Tool = e, t
            t2 = doc.addObject("Part::Box", "EndTools")
            t2.Length, t2.Width, t2.Height = 50, 30, 30
            t2.Placement.Base = V(-45, -5, -5)
            t3 = doc.addObject("Part::Box", "EndTools2")
            t3.Length, t3.Width, t3.Height = 50, 30, 30
            t3.Placement.Base = V(35, -5, -5)
            tf = doc.addObject("Part::MultiFuse", "Ends")
            tf.Shapes = [t2, t3]
            base = doc.addObject("Part::Cut", "Etrim")
            base.Base, base.Tool = e, tf
            if src == "link":
                y = doc.addObject("App::Link", "Y")
                y.LinkedObject = base
                y.LinkPlacement.Base = V(0, 100, 0)
            elif src == "mirror1":
                y = doc.addObject("Part::Mirroring", "Y")
                y.Source, y.Normal, y.Base = base, V(1, 0, 0), V(100, 0, 0)
            else:
                m1 = doc.addObject("Part::Mirroring", "M1")
                m1.Source, m1.Normal, m1.Base = base, V(0, 1, 0), V(0, 0, 0)
                y = doc.addObject("Part::Mirroring", "Y")
                y.Source, y.Normal, y.Base = m1, V(0, 1, 0), V(0, 50, 0)
            c = doc.addObject("Part::Compound", "Comp")
            c.Links = [x] if join else [x, y]
            doc.recompute()

            def on(ed):
                return (isinstance(ed.Curve, Part.Line) and abs(ed.CenterOfMass.y - 20) < 1e-6
                        and abs(ed.CenterOfMass.z - 10) < 1e-6)

            nm = ["Edge%d" % (i + 1) for i, ed in enumerate(c.Shape.Edges) if on(ed) and abs(ed.Length - 40) < 1e-6][0]
            f = doc.addObject("App::FeaturePython", "Consumer")
            f.addProperty("App::PropertyLinkSub", "Ref")
            f.Ref = (c, [nm])
            doc.recompute()
            t.Placement.Base = V(-5, 16, 6)
            if src == "link":
                y.LinkPlacement.Base = V(0, 0, 0)
            elif src == "mirror1":
                y.Base = V(20, 0, 0)
            else:
                y.Base = V(0, 0, 0)
            if join:
                c.Links = [x, y]
            doc.recompute()
            sub = f.Ref[1][0] if f.Ref and f.Ref[1] else "?"
            self.assertTrue(sub.startswith("?"), "%s: the reference silently resolves to %r" % (src, sub))
        finally:
            App.closeDocument(doc.Name)

    def test_a_copy_of_the_same_solid_is_not_the_same_element(self):
        for src in ("link", "mirror1", "mirror2"):
            for join in (False, True):
                self._compound_with_copy(src, join)

    def test_a_trim_is_rescued_whatever_the_size_of_the_hashed_name_index(self):
        # the element map writes the index of a hashed name in hex, the history in decimal: from
        # 16 faces on, the same name read differently and the lineage of the rescue was refused
        for n in (4, 12, 16, 20):
            doc = App.newDocument("ExtentNGon%d" % n)
            try:
                body = doc.addObject("PartDesign::Body", "Body")
                sk = _sketch(doc, body, "Sketch")
                radius = 10.0 / math.sin(math.pi / n)
                pts = [V(radius * math.cos(2 * math.pi * k / n - math.pi / 2 - math.pi / n),
                         radius * math.sin(2 * math.pi * k / n - math.pi / 2 - math.pi / n), 0) for k in range(n)]
                for k in range(n):
                    sk.addGeometry(Part.LineSegment(pts[k], pts[(k + 1) % n]), False)
                pad = body.newObject("PartDesign::Pad", "Pad")
                pad.Profile = sk
                pad.Length = 10
                doc.recompute()
                mid = (pts[0] + pts[1]) * 0.5 + V(0, 0, 10)
                nm = [("Edge%d" % (i + 1)) for i, e in enumerate(pad.Shape.Edges)
                      if isinstance(e.Curve, Part.Line) and (e.CenterOfMass - mid).Length < 1e-6][0]
                c = doc.addObject("App::FeaturePython", "C")
                c.addProperty("App::PropertyLinkSub", "Ref")
                c.Ref = (body, [nm])
                doc.recompute()
                vs = [("Edge%d" % (i + 1)) for i, e in enumerate(pad.Shape.Edges)
                      if isinstance(e.Curve, Part.Line) and abs(e.Curve.Direction.z) > 0.999
                      and min((V(e.CenterOfMass.x, e.CenterOfMass.y, 0) - p).Length for p in (pts[0], pts[1])) < 1e-6]
                fil = body.newObject("PartDesign::Fillet", "F")
                fil.Base = (pad, vs)
                fil.Radius = 2
                doc.recompute()
                sub = c.Ref[1][0]
                self.assertFalse(sub.startswith("?"), "N=%d: the trimmed edge was lost" % n)
                ed = body.Shape.getElement(sub)
                self.assertLess(ed.Length, (pts[1] - pts[0]).Length - 1.0)
                self.assertLess((ed.CenterOfMass - mid).Length, 1e-5)
            finally:
                App.closeDocument(doc.Name)
