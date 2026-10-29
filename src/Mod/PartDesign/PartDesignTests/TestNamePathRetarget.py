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
