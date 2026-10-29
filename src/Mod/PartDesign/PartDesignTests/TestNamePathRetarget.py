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

"""Name-path retargets across solids (cad-fork G01, slice 9).

A link to an element of a compound whose stored name stops resolving (the child that carried it
is dropped, or a cut removes the element) used to fall back to a geometric search, which accepts
a coincident twin edge (same vertices, same curve, same midpoint) of another solid: a Mirror or
Link copy of the same solid, or the sibling child itself. D6: an element of another solid is a
different element, so the reference must be reported ('?EdgeN'), never moved. The positive
controls check that a reference whose name still resolves, or whose solid is unchanged, stays.
"""

import unittest

import FreeCAD as App
import Part
from FreeCAD import Vector as V

W, H, L = 40.0, 20.0, 10.0


def _on_carrier(edge):
    """The top +Y line of the 40 x 20 x 10 plate: y = 20, z = 10."""
    return (
        isinstance(edge.Curve, Part.Line)
        and abs(edge.CenterOfMass.y - H) < 1e-6
        and abs(edge.CenterOfMass.z - L) < 1e-6
    )


def _carrier_edges(shape, length=W):
    return [
        "Edge%d" % (i + 1)
        for i, e in enumerate(shape.Edges)
        if _on_carrier(e) and abs(e.Length - length) < 1e-6
    ]


def _solids_of(shape, name):
    edge = shape.getElement(name)
    return [i for i, s in enumerate(shape.Solids) if any(edge.isSame(x) for x in s.Edges)]


class TestNamePathRetarget(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("NamePathRetarget")

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    # ------------------------------------------------------------- fixtures
    def _plate(self, cut_by_tool=False):
        d = self.doc
        pl = d.addObject("Part::Plane", "Rect")
        pl.Length, pl.Width = W, H
        e = d.addObject("Part::Extrusion", "E")
        e.Base = pl
        e.Dir = V(0, 0, L)
        e.Solid = True
        e.LengthFwd = L
        # a rebate tool, far away until moved onto the carrier
        t = d.addObject("Part::Box", "Tool")
        t.Length, t.Width, t.Height = 50, 4, 4
        t.Placement.Base = V(-300, 300, 300)
        t0 = d.addObject("Part::Box", "Tool0")
        t0.Placement.Base = V(-400, 400, 400)
        x = d.addObject("Part::Cut", "X")
        x.Base = e
        x.Tool = t if cut_by_tool else t0
        d.recompute()
        return e, t, x

    def _consumer(self, comp, name):
        c = self.doc.addObject("App::FeaturePython", "Consumer")
        c.addProperty("App::PropertyLinkSub", "Ref")
        c.Ref = (comp, [name])
        self.doc.recompute()
        return c

    def _assert_reported(self, consumer):
        sub = consumer.Ref[1][0]
        self.assertTrue(sub.startswith("?"), "reference was silently moved to %s" % sub)

    # ------------------------------------------------------------- drops
    def _drop(self, kind, keep_ref_child):
        """Compound [Q, X]; the reference is on X's edge; the compound then drops X (or Q)."""
        d = self.doc
        e, t, x = self._plate()
        if kind == "mirror":
            q = d.addObject("Part::Mirroring", "Q")
            q.Source = x
            q.Normal = V(1, 0, 0)
            q.Base = V(W / 2, 0, 0)  # a copy of X exactly on X
        else:
            q = d.addObject("Part::Cut", "Q")
            q.Base = x
            q.Tool = t
        c = d.addObject("Part::Compound", "C")
        c.Links = [q, x]
        d.recompute()
        names = [n for n in _carrier_edges(c.Shape) if 1 in _solids_of(c.Shape, n)]
        self.assertEqual(len(names), 1)
        cons = self._consumer(c, names[0])
        c.Links = [x] if keep_ref_child else [q]
        d.recompute()
        return c, cons

    def test_dropped_child_mirror_twin_is_not_taken(self):
        c, cons = self._drop("mirror", keep_ref_child=False)
        self.assertEqual(len(c.Shape.Solids), 1)
        self._assert_reported(cons)

    def test_dropped_child_sibling_twin_is_not_taken(self):
        c, cons = self._drop("cut", keep_ref_child=False)
        self._assert_reported(cons)

    def test_dropped_other_child_keeps_the_reference(self):
        c, cons = self._drop("mirror", keep_ref_child=True)
        sub = cons.Ref[1][0]
        self.assertFalse(sub.startswith("?"))
        self.assertAlmostEqual(c.Shape.getElement(sub).Length, W, places=6)
        self.assertEqual(len(c.Shape.Solids), 1)

    def test_mirror_twin_that_stays_keeps_the_reference(self):
        d = self.doc
        e, t, x = self._plate()
        q = d.addObject("Part::Mirroring", "Q")
        q.Source = x
        q.Normal = V(1, 0, 0)
        q.Base = V(W / 2, 0, 0)
        c = d.addObject("Part::Compound", "C")
        c.Links = [q, x]
        d.recompute()
        name = [n for n in _carrier_edges(c.Shape) if 1 in _solids_of(c.Shape, n)][0]
        cons = self._consumer(c, name)
        t.Placement.Base = V(-300, 310, 310)  # recompute with nothing changed in the shape
        d.recompute()
        self.assertEqual(cons.Ref[1][0], name)
        self.assertEqual(_solids_of(c.Shape, name), [1])

    # ------------------------------------------------- cut removes the edge
    def test_cut_removes_edge_sibling_twin_is_not_taken(self):
        d = self.doc
        e, t, x = self._plate()
        q = d.addObject("Part::Cut", "Q")
        q.Base = x
        q.Tool = t
        c = d.addObject("Part::Compound", "C")
        c.Links = [q, x]
        d.recompute()
        name = [n for n in _carrier_edges(c.Shape) if 0 in _solids_of(c.Shape, n)][0]
        cons = self._consumer(c, name)
        t.Placement.Base = V(-5, 16, 6)  # Q's rebate removes Q's edge; X's twin stays
        d.recompute()
        self.assertEqual(len(c.Shape.Solids), 2)
        self._assert_reported(cons)

    def test_cut_removes_edge_nested_compound_twin_is_not_taken(self):
        d = self.doc
        e, t, x = self._plate()
        q = d.addObject("Part::Cut", "Q")
        q.Base = x
        q.Tool = t
        c1 = d.addObject("Part::Compound", "C1")
        c1.Links = [q]
        c2 = d.addObject("Part::Compound", "C2")
        c2.Links = [x]
        c = d.addObject("Part::Compound", "C")
        c.Links = [c1, c2]
        d.recompute()
        name = [n for n in _carrier_edges(c.Shape) if 0 in _solids_of(c.Shape, n)][0]
        cons = self._consumer(c, name)
        t.Placement.Base = V(-5, 16, 6)
        d.recompute()
        self._assert_reported(cons)

    # ------------------------------------------- a copy arrives on the carrier
    def _link_copy(self, joins_late):
        d = self.doc
        e, t, x = self._plate(cut_by_tool=True)
        y = d.addObject("App::Link", "Y")
        y.LinkedObject = e
        y.LinkPlacement.Base = V(0, 100, 0)
        c = d.addObject("Part::Compound", "Comp")
        c.Links = [x] if joins_late else [x, y]
        d.recompute()
        name = _carrier_edges(c.Shape)[0]
        cons = self._consumer(c, name)
        t.Placement.Base = V(-5, 16, 6)  # removes X's edge on the carrier
        y.LinkPlacement.Base = V(0, 0, 0)  # the copy's own edge lies on the carrier now
        if joins_late:
            c.Links = [x, y]
        d.recompute()
        return c, cons

    def test_moved_link_copy_edge_is_not_taken(self):
        c, cons = self._link_copy(joins_late=False)
        self.assertEqual(len(c.Shape.Solids), 2)
        self._assert_reported(cons)

    def test_joined_link_copy_edge_is_not_taken(self):
        c, cons = self._link_copy(joins_late=True)
        self.assertEqual(len(c.Shape.Solids), 2)
        self._assert_reported(cons)

    def test_frozen_copy_edge_is_not_taken(self):
        d = self.doc
        e, t, x = self._plate(cut_by_tool=True)
        y = d.addObject("Part::Feature", "Frozen")
        y.Shape = e.Shape.copy()
        y.Placement.Base = V(0, 100, 0)
        c = d.addObject("Part::Compound", "Comp")
        c.Links = [x, y]
        d.recompute()
        cons = self._consumer(c, _carrier_edges(c.Shape)[0])
        t.Placement.Base = V(-5, 16, 6)
        y.Placement.Base = V(0, 0, 0)
        d.recompute()
        self._assert_reported(cons)

    def test_copy_that_stays_away_keeps_the_reference(self):
        d = self.doc
        e, t, x = self._plate(cut_by_tool=True)
        y = d.addObject("App::Link", "Y")
        y.LinkedObject = e
        y.LinkPlacement.Base = V(0, 100, 0)
        c = d.addObject("Part::Compound", "Comp")
        c.Links = [x, y]
        d.recompute()
        name = _carrier_edges(c.Shape)[0]
        cons = self._consumer(c, name)
        t.Placement.Base = V(-5, 300, 300)  # nothing changes on the carrier
        d.recompute()
        self.assertEqual(cons.Ref[1][0], name)
        self.assertEqual(_solids_of(c.Shape, name), [0])
