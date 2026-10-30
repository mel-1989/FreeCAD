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


_KIND = {"edge": ("Edge", "Edges"), "face": ("Face", "Faces"), "vertex": ("Vertex", "Vertexes")}


def _is_target(shape, kind, i):
    """The top +Y edge, the top face, or the (40, 20, 10) corner of the plate."""
    if kind == "edge":
        e = shape.Edges[i]
        return _on_carrier(e) and abs(e.Length - W) < 1e-6
    if kind == "face":
        f = shape.Faces[i]
        return abs(f.CenterOfMass.z - L) < 1e-6 and abs(f.Area - W * H) < 1e-6 and abs(f.CenterOfMass.x - W / 2) < 1e-6
    return (shape.Vertexes[i].Point - V(W, H, L)).Length < 1e-6


def _targets_in(shape, kind, solid):
    """Names of the target elements that lie in solid index `solid` of the compound."""
    prefix, attr = _KIND[kind]
    out = []
    for i in range(len(getattr(shape, attr))):
        if _is_target(shape, kind, i):
            el = shape.getElement("%s%d" % (prefix, i + 1))
            if any(el.isSame(x) for x in getattr(shape.Solids[solid], attr)):
                out.append("%s%d" % (prefix, i + 1))
    return out


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

    # ------------------------------------------- the owner is the child, not the position
    def _plate_x(self):
        d = self.doc
        e, t, x = self._plate()
        far = d.addObject("Part::Box", "FarT")
        far.Placement.Base = V(-300, 300, 300)
        q = d.addObject("Part::Cut", "Q")
        q.Base = x
        q.Tool = far
        c = d.addObject("Part::Compound", "C")
        return d, e, x, q, c

    def _box(self, name, y):
        z = self.doc.addObject("Part::Box", name)
        z.Placement.Base = V(0, y, 0)
        return z

    def _ref_on(self, c, kind, solid):
        d = self.doc
        d.recompute()
        names = _targets_in(c.Shape, kind, solid)
        self.assertEqual(len(names), 1)
        return self._consumer(c, names[0])

    def test_dropped_child_every_element_kind(self):
        for kind in ("edge", "face", "vertex"):
            with self.subTest(kind=kind):
                d, e, x, q, c = self._plate_x()
                c.Links = [q, x]
                cons = self._ref_on(c, kind, 1)
                c.Links = [q]
                d.recompute()
                self._assert_reported(cons)
                App.closeDocument(d.Name)
                self.doc = App.newDocument("NamePathRetarget")

    def test_dropped_referenced_q_with_x_shifting_into_its_index(self):
        d, e, x, q, c = self._plate_x()
        c.Links = [q, x]
        cons = self._ref_on(c, "edge", 0)  # on Q
        c.Links = [x]  # X's twin takes Q's index
        d.recompute()
        self._assert_reported(cons)

    def test_children_reversed_twin_at_the_old_index_is_not_taken(self):
        d, e, x, q, c = self._plate_x()
        c.Links = [x, q]
        cons = self._ref_on(c, "edge", 0)  # on X
        c.Links = [q]  # Q's twin is at index 0 now
        d.recompute()
        self._assert_reported(cons)

    def test_children_reversed_mirror_twin_is_not_taken(self):
        d, e, x, q, c = self._plate_x()
        m = d.addObject("Part::Mirroring", "M")
        m.Source = x
        m.Normal = V(1, 0, 0)
        m.Base = V(W / 2, 0, 0)
        c.Links = [x, m]
        cons = self._ref_on(c, "edge", 0)
        c.Links = [m]
        d.recompute()
        self._assert_reported(cons)

    def test_sibling_prepended_twin_at_the_old_index_is_not_taken(self):
        d, e, x, q, c = self._plate_x()
        c.Links = [q, x]
        cons = self._ref_on(c, "edge", 1)  # on X, index 1
        c.Links = [self._box("Z", -200), q]  # Q's twin is at index 1 now
        d.recompute()
        self._assert_reported(cons)

    def test_non_solid_sibling_twin_is_not_taken(self):
        for kind in ("edge", "vertex"):
            with self.subTest(kind=kind):
                d, e, x, q, c = self._plate_x()
                pl = d.addObject("Part::Plane", "SR")
                pl.Length, pl.Width = W, H
                s = d.addObject("Part::Extrusion", "S")
                s.Base = pl
                s.Dir = V(0, 0, L)
                s.Solid = False
                s.LengthFwd = L
                c.Links = [x, s]
                d.recompute()
                names = _targets_in(c.Shape, kind, 0)
                self.assertEqual(len(names), 1)
                cons = self._consumer(c, names[0])
                c.Links = [s]
                d.recompute()
                self._assert_reported(cons)
                App.closeDocument(d.Name)
                self.doc = App.newDocument("NamePathRetarget")

    def _rebased(self, links_after):
        """C = [X]; the reference is on X; X.Base is swapped to an identical extrusion (the name
        chain breaks, the geometry does not) while C's children change."""
        d = self.doc
        e, t, x = self._plate()
        c = d.addObject("Part::Compound", "C")
        w = self._box("W", 200)
        c.Links = [w, x] if links_after == "removed" else [x]
        cons = self._ref_on(c, "edge", 1 if links_after == "removed" else 0)
        pl = d.addObject("Part::Plane", "R2")
        pl.Length, pl.Width = W, H
        e2 = d.addObject("Part::Extrusion", "E2")
        e2.Base = pl
        e2.Dir = V(0, 0, L)
        e2.Solid = True
        e2.LengthFwd = L
        x.Base = e2
        c.Links = {"prepended": [self._box("Z", -200), x], "removed": [x], "same": [x]}[links_after]
        d.recompute()
        return c, cons

    def test_same_child_rescue_survives_a_prepended_sibling(self):
        c, cons = self._rebased("prepended")
        sub = cons.Ref[1][0]
        self.assertFalse(sub.startswith("?"))
        self.assertEqual(_solids_of(c.Shape, sub), [1])

    def test_same_child_rescue_survives_a_removed_sibling(self):
        c, cons = self._rebased("removed")
        sub = cons.Ref[1][0]
        self.assertFalse(sub.startswith("?"))
        self.assertEqual(_solids_of(c.Shape, sub), [0])

    def test_same_child_rescue_without_sibling_change(self):
        c, cons = self._rebased("same")
        self.assertFalse(cons.Ref[1][0].startswith("?"))


    # ------------------------------------------- the same child, another nesting level
    def _twin(self, x, kind):
        """A twin of X 500 mm away in y (a mirror, or a cut that changes nothing near the carrier).
        Coincident twins make stock FreeCAD itself report, so they would not be a control."""
        d = self.doc
        if kind == "mirror":
            t = d.addObject("Part::Mirroring", "T")
            t.Source = x
            t.Normal = V(1, 0, 0)
            t.Base = V(W / 2, 0, 0)
        else:
            tt = d.addObject("Part::Box", "TT")
            tt.Placement.Base = V(-300, 300, 300)
            t = d.addObject("Part::Cut", "T")
            t.Base = x
            t.Tool = tt
        t.Placement.Base = V(0, 500, 0)
        return t

    def _nest(self, x, levels):
        """X wrapped in `levels` sub-compounds, innermost first."""
        node = x
        for i in range(levels):
            c = self.doc.addObject("Part::Compound", "Wrap%d" % i)
            c.Links = [node]
            node = c
        return node

    def _regroup(self, levels_before, levels_after, twin):
        """C = [wrap^before(X), T?] with a reference on X's top edge; then C = [wrap^after(X), T?]
        with nothing else changed. The owner of the element is X throughout, so the reference must
        resolve exactly as it does without the owner check."""
        d = self.doc
        e, t, x = self._plate()
        tw = self._twin(x, twin) if twin else None
        c = d.addObject("Part::Compound", "C")
        links = [self._nest(x, levels_before)] + ([tw] if tw else [])
        c.Links = links
        d.recompute()
        names = [n for n in _carrier_edges(c.Shape) if 0 in _solids_of(c.Shape, n)]
        self.assertGreaterEqual(len(names), 1)
        cons = self._consumer(c, names[0])
        after = self._nest(x, levels_after)
        c.Links = [after] + ([tw] if tw else [])
        d.recompute()
        sub = cons.Ref[1][0]
        self.assertFalse(sub.startswith("?"), "reference lost on regrouping: %s" % sub)
        self.assertAlmostEqual(c.Shape.getElement(sub).Length, W, places=6)
        self.assertEqual(_solids_of(c.Shape, sub), [0])
        return c, cons

    def test_unwrapped_from_a_sub_compound_keeps_the_reference(self):
        self._regroup(1, 0, None)

    def test_wrapped_into_a_sub_compound_keeps_the_reference(self):
        self._regroup(0, 1, None)

    # App::Part and App::LinkGroup only group children, so wrapping X in one is not a new owner
    def _box_edge_in(self, shape, solid):
        """The edge of a 10 mm box at (5, 10, 10) (its top +Y edge), in solid index `solid`."""
        for i, e in enumerate(shape.Edges):
            m = e.CenterOfMass
            if abs(m.x - 5) < 1e-6 and abs(m.z - 10) < 1e-6 and abs(m.y % 100 - 10) < 1e-6:
                if any(e.isSame(x) for x in shape.Solids[solid].Edges):
                    return "Edge%d" % (i + 1)
        self.fail("no top edge in solid %d" % solid)

    def _group(self, kind, x):
        d = self.doc
        if kind == "part":
            w = d.addObject("App::Part", "W")
            w.addObject(x)
        else:
            w = d.addObject("App::LinkGroup", "W")
            w.ElementList = [x]
        return w

    def _grouped(self, kind, unwrap):
        """C = [W[X], Y] (unwrap) or [X, Y] with a reference on X's top edge, then the other."""
        d = self.doc
        x = d.addObject("Part::Box", "X")
        y = self._box("Y", 100)
        w = self._group(kind, x)
        c = d.addObject("Part::Compound", "C")
        c.Links = [w, y] if unwrap else [x, y]
        d.recompute()
        cons = self._consumer(c, self._box_edge_in(c.Shape, 0))
        c.Links = [x, y] if unwrap else [w, y]
        d.recompute()
        sub = cons.Ref[1][0]
        self.assertFalse(sub.startswith("?"), "reference lost on regrouping: %s" % sub)
        self.assertEqual(sub, self._box_edge_in(c.Shape, 0))

    def test_unwrapped_from_an_app_part_keeps_the_reference(self):
        self._grouped("part", True)

    def test_wrapped_into_an_app_part_keeps_the_reference(self):
        self._grouped("part", False)

    def test_unwrapped_from_a_link_group_keeps_the_reference(self):
        self._grouped("linkgroup", True)

    def test_wrapped_into_a_link_group_keeps_the_reference(self):
        self._grouped("linkgroup", False)

    def test_dropped_child_twin_in_an_app_part_is_not_taken(self):
        """C = [P[Q], X] with Q a coincident copy of X in an App::Part sibling; the reference is
        on X's edge and X is then dropped: the only match is Q's edge, in the other container."""
        d = self.doc
        x = d.addObject("Part::Box", "X")
        q = d.addObject("Part::Box", "Q")
        p = d.addObject("App::Part", "P")
        p.addObject(q)
        c = d.addObject("Part::Compound", "C")
        c.Links = [p, x]
        d.recompute()
        cons = self._consumer(c, self._box_edge_in(c.Shape, 1))
        c.Links = [p]
        d.recompute()
        self.assertEqual(len(c.Shape.Solids), 1)
        self._assert_reported(cons)

    def test_unwrapped_with_a_cut_twin_keeps_the_reference(self):
        self._regroup(1, 0, "cut")

    def test_wrapped_with_a_cut_twin_keeps_the_reference(self):
        self._regroup(0, 1, "cut")

    def test_unwrapped_with_a_mirror_twin_keeps_the_reference(self):
        self._regroup(1, 0, "mirror")

    def test_wrapped_with_a_mirror_twin_keeps_the_reference(self):
        self._regroup(0, 1, "mirror")

    def test_unwrapped_from_two_sub_compounds_keeps_the_reference(self):
        self._regroup(2, 0, None)

    def test_unwrapped_from_two_sub_compounds_with_a_mirror_twin(self):
        self._regroup(2, 0, "mirror")

    # ------------------------------ history is not ownership: X made from Q, twin Q is not X
    def _derived(self, how):
        d = self.doc
        e, t, q = self._plate()  # the Cut named X by the fixture plays Q here
        if how == "refine":
            x = d.addObject("Part::Refine", "XR")
            x.Source = q
        else:
            tt = d.addObject("Part::Box", "Tool2")
            tt.Placement.Base = V(-300, 300, 300)
            x = d.addObject("Part::Cut", "XC")
            x.Base = q
            x.Tool = tt
        c = d.addObject("Part::Compound", "C")
        c.Links = [x, q]
        d.recompute()
        names = [n for n in _carrier_edges(c.Shape) if 0 in _solids_of(c.Shape, n)]
        self.assertEqual(len(names), 1)
        cons = self._consumer(c, names[0])
        c.Links = [q]  # X leaves; Q has the same edge and its tag is in X's history
        d.recompute()
        return c, cons

    def test_dropped_refine_of_q_does_not_retarget_to_q(self):
        c, cons = self._derived("refine")
        self._assert_reported(cons)

    def test_dropped_cut_of_q_does_not_retarget_to_q(self):
        c, cons = self._derived("cut")
        self._assert_reported(cons)

    # ------------- a child with few elements, a deleted container: the owner is unknown, keep
    def _small_body(self, n, y):
        """A PartDesign Body padded from a circle (3 faces, 3 edges, 2 vertices: no child map)."""
        d = self.doc
        b = d.addObject("PartDesign::Body", n)
        sk = b.newObject("Sketcher::SketchObject", n + "S")
        sk.addGeometry(Part.Circle(V(20, 20 + y, 0), V(0, 0, 1), 5))
        pad = b.newObject("PartDesign::Pad", n + "P")
        pad.Profile = sk
        pad.Length = 10
        d.recompute()
        return b

    def _top_circles(self, shape):
        return [
            "Edge%d" % (i + 1)
            for i, e in enumerate(shape.Edges)
            if isinstance(e.Curve, Part.Circle)
            and abs(e.Curve.Radius - 5) < 1e-6
            and abs(e.CenterOfMass.z - 10) < 1e-6
        ]

    def _assert_small_kept(self, comp, cons, index):
        sub = cons.Ref[1][0]
        self.assertFalse(sub.startswith("?"), "reference lost: %s" % sub)
        ed = comp.Shape.getElement(sub)
        self.assertAlmostEqual(ed.Curve.Radius, 5.0, places=6)
        self.assertEqual(_solids_of(comp.Shape, sub), [index])

    def _small_regroup(self, moved):
        d = self.doc
        x = self._small_body("SX", 0)
        y = self._small_body("SY", 300)
        z = self._small_body("SZ", 600)
        w = self._small_body("SW", 900)
        c = d.addObject("Part::Compound", "C")
        if moved:
            c1 = d.addObject("Part::Compound", "C1")
            c2 = d.addObject("Part::Compound", "C2")
            c1.Links = [x, y]
            c2.Links = [z, w]
            c.Links = [c1, c2]
        else:
            c.Links = [x, y]
        d.recompute()
        names = [n for n in self._top_circles(c.Shape) if 0 in _solids_of(c.Shape, n)]
        self.assertEqual(len(names), 1)
        cons = self._consumer(c, names[0])
        if moved:
            c1.Links = [y]
            c2.Links = [z, w, x]
            d.recompute()
            self._assert_small_kept(c, cons, 3)
        else:
            c1 = d.addObject("Part::Compound", "C1")
            c1.Links = [x, y]
            c.Links = [c1]
            d.recompute()
            self._assert_small_kept(c, cons, 0)

    def test_small_body_wrapped_into_a_sub_compound_keeps_the_reference(self):
        self._small_regroup(False)

    def test_small_body_moved_between_sub_compounds_keeps_the_reference(self):
        self._small_regroup(True)

    def _deleted_container(self, mode):
        d = self.doc
        e, t, x = self._plate()
        e2, t2, y = self._plate()
        y.Placement.Base = V(0, 300, 0)
        c1 = d.addObject("Part::Compound", "C1")
        c1.Links = [x, y]
        c = d.addObject("Part::Compound", "C")
        c.Links = [c1]
        d.recompute()
        names = [n for n in _carrier_edges(c.Shape) if 0 in _solids_of(c.Shape, n)]
        self.assertEqual(len(names), 1)
        cons = self._consumer(c, names[0])
        if mode == "undo":
            d.UndoMode = 1
            d.openTransaction("unwrap")
        c.Links = [x, y]
        d.removeObject("C1")
        if mode == "undo":
            d.commitTransaction()
        d.recompute()
        sub = cons.Ref[1][0]
        self.assertFalse(sub.startswith("?"), "reference lost: %s" % sub)
        self.assertEqual(_solids_of(c.Shape, sub), [0])
        if mode == "undo":
            d.undo()
            d.recompute()
            sub = cons.Ref[1][0]
            self.assertFalse(sub.startswith("?"), "reference lost on undo: %s" % sub)
            self.assertEqual(_solids_of(c.Shape, sub), [0])
        return d, c, cons

    def test_container_deleted_after_unwrapping_keeps_the_reference(self):
        self._deleted_container("plain")

    def test_container_deleted_after_unwrapping_then_undo(self):
        self._deleted_container("undo")

    def test_container_deleted_after_unwrapping_save_and_reopen(self):
        import os
        import tempfile

        d, c, cons = self._deleted_container("plain")
        fn = os.path.join(tempfile.mkdtemp(), "k.FCStd")
        d.saveAs(fn)
        App.closeDocument(d.Name)
        d2 = App.openDocument(fn)
        self.doc = d2
        d2.recompute()
        c2 = d2.getObject("C")
        sub = d2.getObject("Consumer").Ref[1][0]
        self.assertFalse(sub.startswith("?"), "reference lost on reopen: %s" % sub)
        self.assertEqual(_solids_of(c2.Shape, sub), [0])

    # ------------------------------------------------ round 6: one case per kind of container
    # The classification (isTransparentContainer in src/App/PropertyLinks.cpp), probed with
    # the element names FreeCAD's own element map gives each kind, is: a Part::Compound (and
    # Compound2), App::Part (and Assembly), App::LinkGroup and a plain App::Link are transparent
    # (swapping one for its content keeps the reference); everything else that has a shape of its
    # own is an owner (a PartDesign::Body or Boolean, a link array, a wrapper that computes a
    # shape), so swapping it for its content is another element and is reported (D6).
    def _top_edge(self, shape):
        """The top +Y edge (5, 10, 10) of the 10 mm box X, wherever its solid is in `shape`."""
        for i, e in enumerate(shape.Edges):
            m = e.CenterOfMass
            if abs(m.x - 5) < 1e-6 and abs(m.y - 10) < 1e-6 and abs(m.z - 10) < 1e-6:
                return "Edge%d" % (i + 1)
        self.fail("no top edge of X")

    def _swap(self, make, kept, unwrap):
        """C = [W(X), Y] (unwrap) or [X, Y], a reference on X's top edge, then the other way."""
        d = self.doc
        x = d.addObject("Part::Box", "X")
        y = self._box("Y", 100)
        w = make(x)
        c = d.addObject("Part::Compound", "C")
        c.Links = [w, y] if unwrap else [x, y]
        d.recompute()
        cons = self._consumer(c, self._top_edge(c.Shape))
        c.Links = [x, y] if unwrap else [w, y]
        d.recompute()
        sub = cons.Ref[1][0]
        if kept:
            self.assertFalse(sub.startswith("?"), "reference lost on the swap: %s" % sub)
            self.assertEqual(sub, self._top_edge(c.Shape))
        else:
            self._assert_reported(cons)

    def _link_to(self, target, count=0):
        lk = self.doc.addObject("App::Link", "L")
        lk.LinkedObject = target
        if count:
            lk.ElementCount = count
        return lk

    def _part_of(self, x):
        w = self.doc.addObject("App::Part", "W")
        w.addObject(x)
        return w

    def _assembly_of(self, x):
        try:
            import UtilsAssembly  # noqa: F401
            w = self.doc.addObject("Assembly::AssemblyObject", "W")
        except Exception as ex:
            self.skipTest("no Assembly module: %s" % ex)
        w.addObject(x)
        return w

    def _link_group_of(self, x):
        w = self.doc.addObject("App::LinkGroup", "W")
        w.ElementList = [x]
        return w

    # a Link to a group adds its own tag and a window; looking through it keeps X
    def test_link_to_an_app_part_swapped_for_its_content_keeps_the_reference(self):
        self._swap(lambda x: self._link_to(self._part_of(x)), True, True)

    def test_content_swapped_for_a_link_to_an_app_part_keeps_the_reference(self):
        self._swap(lambda x: self._link_to(self._part_of(x)), True, False)

    def test_link_to_a_link_group_swapped_for_its_content_keeps_the_reference(self):
        self._swap(lambda x: self._link_to(self._link_group_of(x)), True, True)

    def test_link_to_an_assembly_swapped_for_its_content_keeps_the_reference(self):
        self._swap(lambda x: self._link_to(self._assembly_of(x)), True, True)

    def test_link_to_a_link_to_an_app_part_keeps_the_reference(self):
        self._swap(lambda x: self._link_to(self._link_to(self._part_of(x))), True, True)

    # a plain group (App::DocumentObjectGroup) has no shape of its own: it adds its tag and a window
    def _group_of(self, x):
        g = self.doc.addObject("App::DocumentObjectGroup", "Grp")
        g.addObject(x)
        return g

    def test_document_object_group_swapped_for_its_content_keeps_the_reference(self):
        self._swap(self._group_of, True, True)

    def test_content_swapped_for_a_document_object_group_keeps_the_reference(self):
        self._swap(self._group_of, True, False)

    def test_link_to_a_document_object_group_swapped_for_its_content_keeps_the_reference(self):
        self._swap(lambda x: self._link_to(self._group_of(x)), True, True)

    # a Link to a solid or compound passes the names through (no segment of its own)
    def test_link_to_a_box_swapped_for_the_box_keeps_the_reference(self):
        self._swap(lambda x: self._link_to(x), True, True)

    def test_link_to_a_compound_swapped_for_the_box_keeps_the_reference(self):
        self._swap(lambda x: self._link_to(self._nest(x, 1)), True, True)

    def test_assembly_swapped_for_its_content_keeps_the_reference(self):
        self._swap(self._assembly_of, True, True)

    def test_assembly_link_swapped_for_its_content_keeps_the_reference(self):
        def make(x):
            try:
                w = self.doc.addObject("Assembly::AssemblyLink", "W")
            except Exception as ex:
                self.skipTest("no AssemblyLink: %s" % ex)
            w.addObject(x)
            return w

        self._swap(make, True, True)

    def test_compound2_swapped_for_its_content_keeps_the_reference(self):
        def make(x):
            w = self.doc.addObject("Part::Compound2", "W")
            w.Links = [x]
            return w

        self._swap(make, True, True)

    # a link array makes every copy a different element (D6, run 008)
    def test_link_array_swapped_for_its_content_is_reported(self):
        self._swap(lambda x: self._link_to(x, 1), False, True)

    def test_content_swapped_for_a_link_array_is_reported(self):
        self._swap(lambda x: self._link_to(x, 1), False, False)

    def test_link_array_of_an_app_part_swapped_for_its_content_is_reported(self):
        self._swap(lambda x: self._link_to(self._part_of(x), 2), False, True)

    # a wrapper that computes a shape is a new object (D6: opaque)
    def test_refine_swapped_for_its_source_is_reported(self):
        def make(x):
            r = self.doc.addObject("Part::Refine", "R")
            r.Source = x
            return r

        self._swap(make, False, True)

    def test_python_compound_swapped_for_its_source_is_reported(self):
        class Proxy:
            def __init__(self, obj):
                obj.Proxy = self

            def execute(self, obj):
                obj.Shape = Part.makeCompound([obj.Src.Shape])

        def make(x):
            f = self.doc.addObject("Part::FeaturePython", "FP")
            f.addProperty("App::PropertyLink", "Src")
            Proxy(f)
            f.Src = x
            return f

        self._swap(make, False, True)

    def test_shape_copy_swapped_for_its_source_is_reported(self):
        def make(x):
            self.doc.recompute()
            s = self.doc.addObject("Part::Feature", "Copy")
            s.Shape = x.Shape
            return s

        self._swap(make, False, True)

    def test_multi_fuse_swapped_for_its_source_is_reported(self):
        def make(x):
            q = self.doc.addObject("Part::Box", "Q")
            q.Placement.Base = V(500, 0, 0)
            m = self.doc.addObject("Part::MultiFuse", "MF")
            m.Shapes = [x, q]
            return m

        self._swap(make, False, True)

    def test_mirror_swapped_for_its_source_is_reported(self):
        def make(x):
            m = self.doc.addObject("Part::Mirroring", "M")
            m.Source = x
            m.Normal = V(1, 0, 0)
            m.Base = V(5, 0, 0)  # a copy of X exactly on X
            return m

        self._swap(make, False, True)

    def test_compound_filter_swapped_for_its_source_is_reported(self):
        try:
            import CompoundTools.CompoundFilter as CF
        except ImportError as ex:
            self.skipTest("no CompoundFilter: %s" % ex)

        def make(x):
            k = self._nest(x, 1)
            q = self.doc.addObject("Part::Box", "Q")
            q.Placement.Base = V(500, 0, 0)
            k.Links = [x, q]
            f = CF.makeCompoundFilter("CF")
            f.Base = k
            f.FilterType = "bypass"
            return f

        self._swap(make, False, True)

    def test_cut_swapped_for_its_base_is_reported(self):
        def make(x):
            q = self.doc.addObject("Part::Box", "Q")
            q.Placement.Base = V(500, 0, 0)
            c = self.doc.addObject("Part::Cut", "Cut")
            c.Base = x
            c.Tool = q
            return c

        self._swap(make, False, True)

    def test_sub_shape_binder_swapped_for_its_source_is_reported(self):
        def make(x):
            b = self.doc.addObject("PartDesign::SubShapeBinder", "Binder")
            b.Support = [(x, "")]
            return b

        self._swap(make, False, True)

    def test_boolean_fragments_swapped_for_its_source_is_reported(self):
        try:
            import BOPTools.SplitFeatures as SF
        except ImportError as ex:
            self.skipTest("no BOPTools: %s" % ex)

        def make(x):
            q = self.doc.addObject("Part::Box", "Q")
            q.Placement.Base = V(500, 0, 0)
            f = SF.makeBooleanFragments(name="BF")
            f.Objects = [x, q]
            return f

        self._swap(make, False, True)

    def test_draft_array_of_links_swapped_for_its_source_is_reported(self):
        try:
            import Draft
        except ImportError as ex:
            self.skipTest("no Draft: %s" % ex)

        def make(x):
            return Draft.make_ortho_array(x, V(0, 0, 0), V(0, 100, 0), V(0, 0, 100), 1, 1, 1, use_link=True)

        self._swap(make, False, True)

    def test_draft_array_swapped_for_its_source_is_reported(self):
        try:
            import Draft
        except ImportError as ex:
            self.skipTest("no Draft: %s" % ex)

        def make(x):
            return Draft.make_ortho_array(x, V(0, 0, 0), V(0, 100, 0), V(0, 0, 100), 1, 1, 1, use_link=False)

        self._swap(make, False, True)

    def test_draft_clone_swapped_for_its_source_is_reported(self):
        try:
            import Draft
        except ImportError as ex:
            self.skipTest("no Draft: %s" % ex)
        self._swap(lambda x: Draft.make_clone(x), False, True)

    # ---------------------------------------- PartDesign bodies and booleans as compound children
    def _pd_rect(self, body, name, y):
        sk = body.newObject("Sketcher::SketchObject", name)
        pts = [V(0, y, 0), V(40, y, 0), V(40, y + 20, 0), V(0, y + 20, 0)]
        for i in range(4):
            sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
        return sk

    def _pd_body(self, name, y, pad_name):
        b = self.doc.addObject("PartDesign::Body", name)
        pad = b.newObject("PartDesign::Pad", pad_name)
        pad.Profile = self._pd_rect(b, name + "S", y)
        pad.Length = 10
        return b

    def _pd_pocket(self, body, name):
        sk = body.newObject("Sketcher::SketchObject", name + "S")
        sk.AttachmentSupport = [(body.Origin.OriginFeatures[3], "")]
        sk.MapMode = "FlatFace"
        sk.addGeometry(Part.Circle(V(30, 210, 0), V(0, 0, 1), 3))
        pk = body.newObject("PartDesign::Pocket", name)
        pk.Profile = sk
        pk.Reversed = True
        pk.Type = 1
        return pk

    def _pd_edge(self, shape):
        """The top +Y line of the 40 mm pad at y = 200 (the edge at y = 220, z = 10)."""
        for i, e in enumerate(shape.Edges):
            if (
                isinstance(e.Curve, Part.Line)
                and abs(e.Length - 40) < 1e-6
                and abs(e.CenterOfMass.y - 220) < 1e-6
                and abs(e.CenterOfMass.z - 10) < 1e-6
            ):
                return "Edge%d" % (i + 1)
        self.fail("no pad edge")

    def _pd_boolean_child(self, change):
        """C = [Boolean, Y] with the Boolean (Fuse of body B2) at the tip of body B1, a reference
        on the pad edge of B1; then `change` edits the features beneath the Boolean."""
        d = self.doc
        b1 = self._pd_body("B1", 200, "Pad1")
        y = self._box("Y", 100)
        b2 = self._pd_body("B2", 300, "Pad2")
        d.recompute()
        bo = b1.newObject("PartDesign::Boolean", "Bool")
        bo.addObjects([b2])
        bo.Type = "Fuse"
        d.recompute()
        b1.Tip = bo
        c = d.addObject("Part::Compound", "C")
        c.Links = [bo, y]
        d.recompute()
        cons = self._consumer(c, self._pd_edge(c.Shape))
        change(b1, b2, bo, d)
        d.recompute()
        return c, cons

    def test_pocket_inserted_before_a_boolean_child_keeps_the_reference(self):
        """The Boolean's window holds the tag of the feature beneath it, so it is an owner."""

        def change(b1, b2, bo, d):
            b1.Tip = d.getObject("Pad1")
            pk = self._pd_pocket(b1, "Pk")
            bo.BaseFeature = pk
            b1.Tip = bo

        c, cons = self._pd_boolean_child(change)
        sub = cons.Ref[1][0]
        self.assertFalse(sub.startswith("?"), "reference lost on insert: %s" % sub)
        self.assertEqual(sub, self._pd_edge(c.Shape))

    def test_pocket_in_the_tool_body_of_a_boolean_child_keeps_the_reference(self):
        def change(b1, b2, bo, d):
            self._pd_pocket(b2, "Pk2")

        c, cons = self._pd_boolean_child(change)
        self.assertFalse(cons.Ref[1][0].startswith("?"))

    def test_pocket_added_to_a_body_child_keeps_the_reference(self):
        d = self.doc
        b1 = self._pd_body("B1", 200, "Pad1")
        y = self._box("Y", 100)
        c = d.addObject("Part::Compound", "C")
        c.Links = [b1, y]
        d.recompute()
        cons = self._consumer(c, self._pd_edge(c.Shape))
        self._pd_pocket(b1, "Pk")
        d.recompute()
        sub = cons.Ref[1][0]
        self.assertFalse(sub.startswith("?"), "reference lost: %s" % sub)
        self.assertEqual(sub, self._pd_edge(c.Shape))

    def test_boolean_child_wrapped_in_a_sub_compound_keeps_the_reference(self):
        def change(b1, b2, bo, d):
            pass

        d = self.doc
        c, cons = self._pd_boolean_child(change)
        k = d.addObject("Part::Compound", "K")
        k.Links = [d.getObject("Bool")]
        c.Links = [k, d.getObject("Y")]
        d.recompute()
        sub = cons.Ref[1][0]
        self.assertFalse(sub.startswith("?"), "reference lost: %s" % sub)

    # a twin that sits inside a Link to a group: the unwrapped reference must not move to it
    def test_dropped_child_twin_behind_a_link_to_an_app_part_is_not_taken(self):
        d = self.doc
        x = d.addObject("Part::Box", "X")
        q = d.addObject("Part::Box", "Q")
        p = self._part_of(q)
        lk = self._link_to(p)
        c = d.addObject("Part::Compound", "C")
        c.Links = [lk, x]
        d.recompute()
        cons = self._consumer(c, self._top_edge_of_solid(c.Shape, 1))
        c.Links = [lk]
        d.recompute()
        self.assertEqual(len(c.Shape.Solids), 1)
        self._assert_reported(cons)

    def _top_edge_of_solid(self, shape, solid):
        for i, e in enumerate(shape.Edges):
            m = e.CenterOfMass
            if abs(m.x - 5) < 1e-6 and abs(m.y - 10) < 1e-6 and abs(m.z - 10) < 1e-6:
                if any(e.isSame(s) for s in shape.Solids[solid].Edges):
                    return "Edge%d" % (i + 1)
        self.fail("no top edge")


class TestNamePathBodyControl(unittest.TestCase):
    """A PartDesign body split by a pocket and rejoined by a pad: an unchanged edge keeps its
    reference (the child maps of a body carry the tags of later features of one chain; only a
    Part::Compound's children are separate objects)."""

    def test_body_split_and_rejoined_keeps_unchanged_edge(self):
        import Sketcher

        doc = App.newDocument("NamePathBody")
        try:
            body = doc.addObject("PartDesign::Body", "Body")

            def sketch(name, pts, z=0.0):
                sk = body.newObject("Sketcher::SketchObject", name)
                sk.AttachmentSupport = (body.Origin.OriginFeatures[3], [""])
                sk.MapMode = "FlatFace"
                sk.AttachmentOffset = App.Placement(V(0, 0, z), App.Rotation())
                n = len(pts)
                for i in range(n):
                    sk.addGeometry(
                        Part.LineSegment(V(pts[i][0], pts[i][1], 0), V(pts[(i + 1) % n][0], pts[(i + 1) % n][1], 0))
                    )
                for i in range(n):
                    sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % n, 1))
                return sk

            doc.recompute()
            pad = body.newObject("PartDesign::Pad", "Pad")
            pad.Profile = sketch("S1", [(0, 0), (60, 0), (60, 20), (0, 20)])
            pad.Length = 10
            doc.recompute()

            def find(shape, mid, length):
                for i, ed in enumerate(shape.Edges):
                    if abs(ed.Length - length) < 1e-6 and (ed.CenterOfMass - V(*mid)).Length < 1e-6:
                        return "Edge%d" % (i + 1)

            cons = doc.addObject("App::FeaturePython", "Consumer")
            cons.addProperty("App::PropertyLinkSub", "R1")
            cons.R1 = (body, [find(body.Shape, (60, 20, 5), 10)])
            doc.recompute()
            pocket = body.newObject("PartDesign::Pocket", "Pocket")
            pocket.Profile = sketch("S2", [(25, -5), (35, -5), (35, 25), (25, 25)], z=10)
            pocket.Type = 1
            pocket.Length = 20
            doc.recompute()
            self.assertEqual(len(body.Shape.Solids), 2)
            pad2 = body.newObject("PartDesign::Pad", "Pad2")
            pad2.Profile = sketch("S3", [(25, 0), (35, 0), (35, 20), (25, 20)], z=0)
            pad2.Length = 5
            doc.recompute()
            self.assertEqual(len(body.Shape.Solids), 1)
            sub = cons.R1[1][0]
            self.assertFalse(sub.startswith("?"))
            ed = body.Shape.getElement(sub)
            self.assertAlmostEqual(ed.Length, 10.0, places=6)
            self.assertLess((ed.CenterOfMass - V(60, 20, 5)).Length, 1e-6)
        finally:
            App.closeDocument(doc.Name)
