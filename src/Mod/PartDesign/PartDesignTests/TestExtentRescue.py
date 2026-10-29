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
