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

"""History-based reference rescue (cad-fork G01, slice 1).

A feature inserted between a referenced feature and its consumer used to turn the consumer's
element reference into '?EdgeN' (the consumer went Invalid). The element map records which
element each new element derives from; the reference is now rescued when exactly one element
of the requested type derives from the stored name. Every test checks that the consumer stays
valid and that the reference points at the geometrically same element as before the insertion
(never by name); the negative test checks that an ambiguous case stays reported as missing.
"""

import os
import tempfile
import unittest

import FreeCAD as App
import Part
import Sketcher
from FreeCAD import Vector as V

W, H, L = 40.0, 20.0, 10.0


# ------------------------------------------------------------------ helpers
def _plane(body, role):
    for f in body.Origin.OriginFeatures:
        if f.Role == role:
            return f
    raise KeyError(role)


def _add(doc, body, typ, name, after=None):
    if after is None:
        return body.newObject(typ, name)
    obj = doc.addObject(typ, name)
    body.insertObject(obj, after, True)
    return obj


def _sketch(doc, body, name, plane="XY_Plane", z=0.0, after=None):
    sk = _add(doc, body, "Sketcher::SketchObject", name, after)
    sk.AttachmentSupport = (_plane(body, plane), [""])
    sk.MapMode = "FlatFace"
    if z:
        sk.AttachmentOffset = App.Placement(V(0, 0, z), App.Rotation())
    return sk


def _rect(sk, cx, cy, w, h):
    x0, x1, y0, y1 = cx - w / 2, cx + w / 2, cy - h / 2, cy + h / 2
    p = [V(x0, y0, 0), V(x1, y0, 0), V(x1, y1, 0), V(x0, y1, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(p[i], p[(i + 1) % 4]), False)
    for i in range(4):
        sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))


def _circle(sk, cx, cy, r):
    sk.addGeometry(Part.Circle(V(cx, cy, 0), V(0, 0, 1), r), False)


def _line_edges(shape, direction, xs, ys):
    """Names of the straight edges parallel to `direction` at the corner positions xs x ys."""
    names = []
    for sx in xs:
        for sy in ys:
            for i, e in enumerate(shape.Edges):
                if not isinstance(e.Curve, Part.Line):
                    continue
                if abs(e.Curve.Direction.dot(direction)) < 1 - 1e-9:
                    continue
                m = e.CenterOfMass
                if abs(m.x - sx) < 1e-6 and abs(m.y - sy) < 1e-6:
                    names.append("Edge%d" % (i + 1))
                    break
    return names


def _edge_sig(shape, name):
    e = shape.getElement(name)
    c = e.CenterOfMass
    return (round(c.x, 5), round(c.y, 5), round(c.z, 5), round(e.Length, 5), type(e.Curve).__name__)


def _face_sig(shape, name):
    f = shape.getElement(name)
    u0, u1, v0, v1 = f.ParameterRange
    n = f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
    c = f.CenterOfMass
    return (
        round(n.x, 5),
        round(n.y, 5),
        round(n.z, 5),
        round(c.x, 5),
        round(c.y, 5),
        round(c.z, 5),
        round(f.Area, 5),
        type(f.Surface).__name__,
    )


class TestReferenceRescue(unittest.TestCase):
    """Element references survive a feature inserted upstream of the consumer."""

    def setUp(self):
        self.doc = App.newDocument("RefRescue." + self._testMethodName)
        self.body = self.doc.addObject("PartDesign::Body", "Body")

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    # -------------------------------------------------------------- builders
    def _pad(self, cylinder=False):
        sk = _sketch(self.doc, self.body, "Sketch")
        if cylinder:
            _circle(sk, 0, 0, 10.0)
        else:
            _rect(sk, 0, 0, W, H)
        pad = self.body.newObject("PartDesign::Pad", "Pad")
        pad.Profile = sk
        pad.Length = L
        self.doc.recompute()
        return pad

    def _dressup(self, typ, base, edges, size, after=None):
        d = _add(self.doc, self.body, typ, typ.split("::")[1], after)
        d.Base = (base, edges)
        if typ.endswith("Chamfer"):
            d.Size = size
        else:
            d.Radius = size
        return d

    def _vertical_edges(self, pad):
        return _line_edges(pad.Shape, V(0, 0, 1), (-W / 2, W / 2), (-H / 2, H / 2))

    def _insert_hole(self, after, x=-10.0, y=0.0, name="Hole"):
        hs = _sketch(self.doc, self.body, name + "Sketch", z=L, after=after)
        _circle(hs, x, y, 2.0)
        hp = _add(self.doc, self.body, "PartDesign::Pocket", name + "Pocket", hs)
        hp.Profile = hs
        hp.Length = 2 * L
        return hp

    def _insert_boss(self, after):
        bs = _sketch(self.doc, self.body, "BossSketch", z=L, after=after)
        _rect(bs, 10.0, 0.0, 6.0, 6.0)
        boss = _add(self.doc, self.body, "PartDesign::Pad", "Boss", bs)
        boss.Profile = bs
        boss.Length = 4.0
        return boss

    # -------------------------------------------------------------- assertions
    def _valid(self, *objs):
        for o in objs:
            self.assertNotIn("Invalid", o.State, o.Name + " is invalid")
            self.assertNotIn("Error", o.State, o.Name + " has an error")
            self.assertTrue(o.Shape.isValid() and not o.Shape.isNull(), o.Name + " has no valid shape")

    def _refs_same(self, feature, before_sigs, sig=_edge_sig):
        base, subs = feature.Base[0], feature.Base[1]
        self.assertEqual(len(subs), len(before_sigs))
        for sub, expected in zip(subs, before_sigs):
            self.assertFalse(sub.startswith("?"), "reference went missing: %r" % (subs,))
            self.assertEqual(sig(base.Shape, sub), expected, "%s points at a different element" % sub)

    # -------------------------------------------------------------- positive cases
    def test_through_hole_before_fillet(self):
        pad = self._pad()
        edges = self._vertical_edges(pad)
        self.assertEqual(len(edges), 4)
        sigs = [_edge_sig(pad.Shape, e) for e in edges]
        fil = self._dressup("PartDesign::Fillet", pad, edges, 2.0)
        self.doc.recompute()
        self._valid(fil)
        self._insert_hole(pad)
        self.doc.recompute()
        self._valid(fil)
        self.assertIs(fil.Base[0], self.doc.getObject("HolePocket"))
        self._refs_same(fil, sigs)

    def test_boss_before_fillet(self):
        pad = self._pad()
        edges = self._vertical_edges(pad)
        sigs = [_edge_sig(pad.Shape, e) for e in edges]
        fil = self._dressup("PartDesign::Fillet", pad, edges, 2.0)
        self.doc.recompute()
        self._insert_boss(pad)
        self.doc.recompute()
        self._valid(fil)
        self.assertIs(fil.Base[0], self.doc.getObject("Boss"))
        self._refs_same(fil, sigs)

    def test_hole_before_chamfer(self):
        pad = self._pad()
        edges = self._vertical_edges(pad)[:2]
        sigs = [_edge_sig(pad.Shape, e) for e in edges]
        cham = self._dressup("PartDesign::Chamfer", pad, edges, 1.5)
        self.doc.recompute()
        self._valid(cham)
        self._insert_hole(pad)
        self.doc.recompute()
        self._valid(cham)
        self._refs_same(cham, sigs)

    def test_sketch_support_after_insertion(self):
        pad = self._pad()
        edges = self._vertical_edges(pad)
        fil = self._dressup("PartDesign::Fillet", pad, edges, 2.0)
        self.doc.recompute()
        top = [
            "Face%d" % (i + 1)
            for i, f in enumerate(fil.Shape.Faces)
            if isinstance(f.Surface, Part.Plane) and abs(f.CenterOfMass.z - L) < 1e-6
        ]
        self.assertEqual(len(top), 1)
        sk = self.body.newObject("Sketcher::SketchObject", "TopSketch")
        sk.AttachmentSupport = [(fil, top[0])]
        sk.MapMode = "FlatFace"
        _circle(sk, 0, 0, 3.0)
        self.doc.recompute()
        sig = _face_sig(fil.Shape, top[0])
        self.assertEqual(sig[:3], (0.0, 0.0, 1.0))
        rot_before = sk.Placement.Rotation
        z_before = sk.Placement.Base.z
        self._insert_hole(pad)
        self.doc.recompute()
        self._valid(fil, sk)
        support, subs = sk.AttachmentSupport[0]
        self.assertIs(support, fil)
        self.assertFalse(subs[0].startswith("?"))
        # the hole changes the top face's area and centroid; it is still the planar face at height L facing +Z
        after = _face_sig(fil.Shape, subs[0])
        self.assertEqual((after[:3], after[5], after[7]), (sig[:3], L, "Plane"))
        self.assertAlmostEqual(sk.Placement.Base.z, z_before, places=6)
        self.assertTrue(sk.Placement.Rotation.isSame(rot_before, 1e-9))

    def test_two_features_inserted_in_a_row(self):
        pad = self._pad()
        edges = self._vertical_edges(pad)
        sigs = [_edge_sig(pad.Shape, e) for e in edges]
        fil = self._dressup("PartDesign::Fillet", pad, edges, 2.0)
        self.doc.recompute()
        h1 = self._insert_hole(pad, x=-10.0, name="HoleA")
        self.doc.recompute()
        self._valid(fil)
        self._refs_same(fil, sigs)
        h2 = self._insert_hole(h1, x=10.0, name="HoleB")
        self.doc.recompute()
        self._valid(fil)
        self.assertIs(fil.Base[0], h2)
        self._refs_same(fil, sigs)

    def test_two_features_inserted_without_recompute(self):
        pad = self._pad()
        edges = self._vertical_edges(pad)
        sigs = [_edge_sig(pad.Shape, e) for e in edges]
        fil = self._dressup("PartDesign::Fillet", pad, edges, 2.0)
        self.doc.recompute()
        h1 = self._insert_hole(pad, x=-10.0, name="HoleA")
        h2 = self._insert_hole(h1, x=10.0, name="HoleB")
        self.doc.recompute()
        self._valid(fil)
        self.assertIs(fil.Base[0], h2)
        self._refs_same(fil, sigs)

    def test_cylinder_circle_edge(self):
        pad = self._pad(cylinder=True)
        top = [
            "Edge%d" % (i + 1)
            for i, e in enumerate(pad.Shape.Edges)
            if isinstance(e.Curve, Part.Circle) and abs(e.Curve.Center.z - L) < 1e-6
        ]
        self.assertEqual(len(top), 1)
        sigs = [_edge_sig(pad.Shape, top[0])]
        fil = self._dressup("PartDesign::Fillet", pad, top, 1.0)
        self.doc.recompute()
        self._valid(fil)
        self._insert_hole(pad, x=5.0, y=0.0)
        self.doc.recompute()
        self._valid(fil)
        self._refs_same(fil, sigs)

    # -------------------------------------------------------------- negative case
    def test_split_edge_stays_missing(self):
        """A notch cut through the middle of the +X vertical edges splits each into two edges: the
        reference to either is ambiguous and must be reported, never retargeted silently."""
        pad = self._pad()
        edges = self._vertical_edges(pad)
        sigs = [_edge_sig(pad.Shape, e) for e in edges]
        fil = self._dressup("PartDesign::Fillet", pad, edges, 2.0)
        self.doc.recompute()
        self._valid(fil)
        sk = _sketch(self.doc, self.body, "NotchSketch", plane="XZ_Plane", after=pad)
        _rect(sk, W / 2, L / 2, 6.0, 4.0)
        notch = _add(self.doc, self.body, "PartDesign::Pocket", "Notch", sk)
        notch.Profile = sk
        notch.Type = 1  # ThroughAll
        notch.Midplane = True
        self.doc.recompute()
        self.assertEqual(len(notch.Shape.Solids), 1)
        self.assertGreater(len(notch.Shape.Edges), len(pad.Shape.Edges))
        subs = fil.Base[1]
        self.assertTrue(any(s.startswith("?") for s in subs), "ambiguous reference was not reported: %r" % (subs,))
        self.assertIn("Invalid", fil.State)
        # whatever did not go missing must still be the very same geometry
        for sub, sig in zip(subs, sigs):
            if not sub.startswith("?"):
                self.assertEqual(_edge_sig(fil.Base[0].Shape, sub), sig)

    # -------------------------------------------------------------- never a silent retarget
    def _assert_never_retargeted(self, feature, before_sigs):
        """Every reference is either reported missing or geometrically identical to before."""
        base, subs = feature.Base[0], feature.Base[1]
        self.assertEqual(len(subs), len(before_sigs))
        for sub, expected in zip(subs, before_sigs):
            if sub.startswith("?"):
                continue
            self.assertEqual(
                _edge_sig(base.Shape, sub), expected, "%s was silently retargeted" % sub
            )

    def _edge_at(self, shape, x, y, z):
        for i, e in enumerate(shape.Edges):
            c = e.CenterOfMass
            if abs(c.x - x) < 1e-6 and abs(c.y - y) < 1e-6 and abs(c.z - z) < 1e-6:
                return "Edge%d" % (i + 1)
        self.fail("no edge at %s" % ((x, y, z),))

    def _inserted_dressup_case(self, ref_type, ins_type, point):
        pad = self._pad()
        edge = self._edge_at(pad.Shape, *point)
        sigs = [_edge_sig(pad.Shape, edge)]
        down = self._dressup(ref_type, pad, [edge], 1.0)
        self.doc.recompute()
        self._valid(down)
        self._dressup(ins_type, pad, [edge], 3.0, after=pad)
        self.doc.recompute()
        self._assert_never_retargeted(down, sigs)
        # the edge no longer exists in the same form: the reference must be reported
        self.assertTrue(
            any(s.startswith("?") for s in down.Base[1]) and "Invalid" in down.State,
            "reference was not reported missing: %r %r" % (down.Base[1], down.State),
        )

    def test_inserted_chamfer_on_same_vertical_edge(self):
        self._inserted_dressup_case(
            "PartDesign::Fillet", "PartDesign::Chamfer", (W / 2, H / 2, L / 2)
        )

    def test_inserted_fillet_on_same_vertical_edge(self):
        self._inserted_dressup_case(
            "PartDesign::Chamfer", "PartDesign::Fillet", (W / 2, H / 2, L / 2)
        )

    def test_inserted_chamfer_on_top_edge(self):
        self._inserted_dressup_case("PartDesign::Fillet", "PartDesign::Chamfer", (0, H / 2, L))

    def test_inserted_fillet_on_top_edge(self):
        self._inserted_dressup_case("PartDesign::Chamfer", "PartDesign::Fillet", (0, H / 2, L))

    def test_inserted_draft_moving_the_bottom_edge(self):
        """A Draft of the +X face (neutral plane: the top face) renames the bottom +Y edge, and the top edge, generated from
        the same sketch edge, becomes the only history match: it is a different edge and must not be taken."""
        pad = self._pad()
        edge = self._edge_at(pad.Shape, 0, H / 2, 0)
        sigs = [_edge_sig(pad.Shape, edge)]
        fil = self._dressup("PartDesign::Fillet", pad, [edge], 0.5)
        self.doc.recompute()
        self._valid(fil)
        side = [
            "Face%d" % (i + 1)
            for i, f in enumerate(pad.Shape.Faces)
            if abs(f.CenterOfMass.x - W / 2) < 1e-6
        ][0]
        top = [
            "Face%d" % (i + 1)
            for i, f in enumerate(pad.Shape.Faces)
            if abs(f.CenterOfMass.z - L) < 1e-6
        ][0]
        draft = _add(self.doc, self.body, "PartDesign::Draft", "Draft", pad)
        draft.Base = (pad, [side])
        draft.NeutralPlane = (pad, [top])
        draft.Angle = 5.0
        self.doc.recompute()
        self._assert_never_retargeted(fil, sigs)

    def _draft_before_same_face_draft(self, kind):
        """B0 drafts the +X face (4 degrees) and a consumer references one of its elements; a Draft of the same
        face (2 degrees) is inserted after the Pad. The inserted Draft produces the same mapped names on different
        geometry, so a rescue compared with it would move the consumer silently. B0's own link target is
        unchanged, so its missing name must stay missing."""

        def face_x(sh):
            for i, f in enumerate(sh.Faces):
                u0, u1, v0, v1 = f.ParameterRange
                if (f.normalAt((u0 + u1) / 2, (v0 + v1) / 2) - V(1, 0, 0)).Length < 1e-6:
                    return "Face%d" % (i + 1)

        def face_bottom(sh):
            for i, f in enumerate(sh.Faces):
                if abs(f.CenterOfMass.z) < 1e-6:
                    return "Face%d" % (i + 1)

        def draft(name, angle, after=None):
            d = _add(self.doc, self.body, "PartDesign::Draft", name, after)
            d.Base = (pad, [face_x(pad.Shape)])
            d.NeutralPlane = (pad, [face_bottom(pad.Shape)])
            d.Angle = angle
            return d

        pad = self._pad()
        b0 = draft("B0", 4.0)
        self.doc.recompute()
        sh = b0.Shape
        if kind == "edge":
            ref = None
            for i, e in enumerate(sh.Edges):
                c = e.CenterOfMass
                if abs(c.z - L) < 1e-6 and c.x > 1.0 and abs(c.y) < 1e-6:
                    ref = "Edge%d" % (i + 1)
            b1 = self._dressup("PartDesign::Fillet", b0, [ref], 1.0)
            sig = _edge_sig
        else:
            ref = None
            for i, f in enumerate(sh.Faces):
                c = f.CenterOfMass
                if c.x > 1.0 and abs(c.y) < 1e-6 and abs(c.z - L / 2) < 1e-6:
                    ref = "Face%d" % (i + 1)
            b1 = _add(self.doc, self.body, "PartDesign::Thickness", "B1")
            b1.Base = (b0, [ref])
            b1.Value = 1.0
            sig = _face_sig
        self.doc.recompute()
        self._valid(b1)
        before = sig(sh, ref)
        draft("INS", 2.0, after=pad)
        self.doc.recompute()
        base, subs = b1.Base
        self.assertTrue(
            subs[0].startswith("?") or sig(base.Shape, subs[0]) == before,
            "%s was silently retargeted: %r" % (kind, subs),
        )

    def test_draft_before_same_face_draft_edge_stays_missing(self):
        self._draft_before_same_face_draft("edge")

    def test_draft_before_same_face_draft_face_stays_missing(self):
        self._draft_before_same_face_draft("face")

    def test_pocket_shortening_the_edge(self):
        pad = self._pad()
        edge = self._edge_at(pad.Shape, 0, H / 2, L)
        sigs = [_edge_sig(pad.Shape, edge)]
        fil = self._dressup("PartDesign::Fillet", pad, [edge], 1.0)
        self.doc.recompute()
        sk = _sketch(self.doc, self.body, "CutSk", z=L, after=pad)
        _rect(sk, -10.0, 0.0, W / 2 + 10.0, H + 10.0)
        pk = _add(self.doc, self.body, "PartDesign::Pocket", "CutPocket", sk)
        pk.Profile = sk
        pk.Length = 3.0
        self.doc.recompute()
        self._assert_never_retargeted(fil, sigs)
        self.assertTrue(any(s.startswith("?") for s in fil.Base[1]))

    def test_fragment_of_split_edge_stays_missing(self):
        """F2 is a boss that splits the inner top edge of an L-shaped Pad; a Fillet references the
        8 mm fragment. A full-footprint Pad inserted between Pad and F2 holds the same mapped
        name on the unsplit 20 mm edge; the reference must not move to that whole edge."""
        sk = _sketch(self.doc, self.body, "Sketch")
        pts = [V(-20, -10, 0), V(20, -10, 0), V(20, 0, 0), V(0, 0, 0), V(0, 10, 0), V(-20, 10, 0)]
        for i in range(len(pts)):
            sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % len(pts)]), False)
        pad = self.body.newObject("PartDesign::Pad", "Pad")
        pad.Profile = sk
        pad.Length = L
        self.doc.recompute()
        bs = _sketch(self.doc, self.body, "F2Sketch", z=L)
        _rect(bs, 8, 0, 8, 8)
        f2 = self.body.newObject("PartDesign::Pad", "F2")
        f2.Profile = bs
        f2.Length = 4.0
        self.doc.recompute()
        frag = None
        for i, e in enumerate(f2.Shape.Edges):
            c = e.CenterOfMass
            if abs(c.x - 16) < 1e-6 and abs(c.y) < 1e-6 and abs(c.z - L) < 1e-6:
                frag = "Edge%d" % (i + 1)
        self.assertIsNotNone(frag)
        sigs = [_edge_sig(f2.Shape, frag)]
        self.assertAlmostEqual(f2.Shape.getElement(frag).Length, 8.0, 6)
        fil = self._dressup("PartDesign::Fillet", f2, [frag], 0.3)
        self.doc.recompute()
        self._valid(fil)
        ts = _sketch(self.doc, self.body, "TopSketch", z=L, after=pad)
        _rect(ts, 0, 0, W, H)
        top = _add(self.doc, self.body, "PartDesign::Pad", "Top", ts)
        top.Profile = ts
        top.Length = 5.0
        self.doc.recompute()
        self._assert_never_retargeted(fil, sigs)
        self.assertTrue(any(s.startswith("?") for s in fil.Base[1]), "fragment reference was retargeted: %r" % (fil.Base[1],))

    def _shortened_plate_case(self, scale, d):
        """A pocket removes the last d mm of the referenced top edge of a plate `scale` times
        larger than the default; the edge is modified, so the reference must be reported."""
        Wl, Hl, Ll = W * scale, H * scale, L * scale
        sk = _sketch(self.doc, self.body, "Sketch")
        _rect(sk, 0, 0, Wl, Hl)
        pad = self.body.newObject("PartDesign::Pad", "Pad")
        pad.Profile = sk
        pad.Length = Ll
        self.doc.recompute()
        edge = self._edge_at(pad.Shape, 0, -Hl / 2, Ll)
        sigs = [_edge_sig(pad.Shape, edge)]
        fil = self._dressup("PartDesign::Fillet", pad, [edge], 0.5)
        self.doc.recompute()
        self._valid(fil)
        cs = _sketch(self.doc, self.body, "CutSk", z=Ll, after=pad)
        _rect(cs, Wl / 2 - d + 5.0, -Hl / 2, 10.0, 20.0)
        pk = _add(self.doc, self.body, "PartDesign::Pocket", "Cut", cs)
        pk.Profile = cs
        pk.Length = 2 * Ll
        self.doc.recompute()
        self._assert_never_retargeted(fil, sigs)
        self.assertTrue(
            any(s.startswith("?") for s in fil.Base[1]),
            "edge shortened by %g mm on a %g mm plate was rescued: %r" % (d, Wl, fil.Base[1]),
        )

    def test_shortening_on_large_part_stays_missing(self):
        self._shortened_plate_case(25.0, 0.0003)

    def test_shortening_on_very_large_part_stays_missing(self):
        self._shortened_plate_case(250.0, 0.008)

    def _drafted_step_case(self, size, angle):
        """An L-plate `size` mm across with a 20 mm inner step; a Thickness references the top face. A Draft of the
        step face inserted upstream moves the top face's inner outline edges by a few micrometres: the face is
        modified, so the reference must be reported (an area tolerance that grew with the part accepted it)."""
        sk = _sketch(self.doc, self.body, "Sketch")
        pts = [V(0, 0, 0), V(size, 0, 0), V(size, 20, 0), V(size - 20, 20, 0), V(size - 20, size, 0), V(0, size, 0)]
        for i in range(6):
            sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 6]), False)
        pad = self.body.newObject("PartDesign::Pad", "Pad")
        pad.Profile = sk
        pad.Length = L
        self.doc.recompute()
        faces = pad.Shape.Faces
        top = [
            "Face%d" % (i + 1)
            for i, f in enumerate(faces)
            if abs(f.CenterOfMass.z - L) < 1e-6 and f.normalAt(*f.ParameterRange[::2]).z > 0.5
        ][0]
        bottom = [
            "Face%d" % (i + 1) for i, f in enumerate(faces) if abs(f.CenterOfMass.z) < 1e-6
        ][0]
        step = [
            "Face%d" % (i + 1)
            for i, f in enumerate(faces)
            if abs(f.CenterOfMass.y - 20) < 1e-6 and abs(f.CenterOfMass.z - L / 2) < 1e-6
        ][0]
        sig = _face_sig(pad.Shape, top)
        thick = _add(self.doc, self.body, "PartDesign::Thickness", "Thick")
        thick.Base = (pad, [top])
        thick.Value = 1.0
        thick.Reversed = False
        self.doc.recompute()
        self.assertNotIn("Invalid", thick.State)
        draft = _add(self.doc, self.body, "PartDesign::Draft", "Draft", pad)
        draft.Base = (pad, [step])
        draft.NeutralPlane = (pad, [bottom])
        draft.Angle = angle
        self.doc.recompute()
        self.assertNotIn("Invalid", draft.State)
        base, subs = thick.Base[0], thick.Base[1]
        for sub in subs:
            if not sub.startswith("?"):
                self.assertEqual(_face_sig(base.Shape, sub), sig, "%s was silently retargeted" % sub)
        self.assertTrue(
            any(s.startswith("?") for s in subs),
            "face modified by a %g degree draft on a %g mm plate was rescued: %r" % (angle, size, subs),
        )

    def test_drafted_face_on_10m_part_stays_missing(self):
        self._drafted_step_case(10000.0, 0.01)

    def test_drafted_face_on_10m_part_stays_missing_larger_angle(self):
        self._drafted_step_case(10000.0, 0.02)

    # -------------------------------------------------------------- persistence
    def test_save_reload_keeps_rescued_references(self):
        pad = self._pad()
        edges = self._vertical_edges(pad)
        sigs = [_edge_sig(pad.Shape, e) for e in edges]
        fil = self._dressup("PartDesign::Fillet", pad, edges, 2.0)
        self.doc.recompute()
        self._insert_hole(pad)
        self.doc.recompute()
        self._valid(fil)
        self._refs_same(fil, sigs)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rescued.FCStd")
            self.doc.saveAs(path)
            name = self.doc.Name
            App.closeDocument(name)
            self.doc = App.openDocument(path)
            fil2 = self.doc.getObject("Fillet")
            self.doc.recompute()
            self._valid(fil2)
            self.assertIs(fil2.Base[0], self.doc.getObject("HolePocket"))
            self._refs_same(fil2, sigs)

    # -------------------------------------------------------------- D5: give the original back
    # A rescue rewrites the reference to the inserted feature's element. When the inserted
    # feature goes away again (deleted, or moved behind the consumer), the original name must
    # resolve again exactly as it would have without the rescue.
    def _d5_dressed_pad(self):
        pad = self._pad()
        b0 = self._dressup("PartDesign::Fillet", pad, ["Edge1", "Edge7"], 0.3)
        self.doc.recompute()
        self._valid(b0)
        return pad, b0, (b0.Shape.Volume, list(b0.Base[1]))

    def _d5_insert(self, pad, kind, after=None):
        after = after or pad
        if kind == "chamfer":
            ins = self._dressup("PartDesign::Chamfer", pad, ["Edge2"], 0.5, after=after)
        else:
            sh = pad.Shape
            side = [
                "Face%d" % (i + 1)
                for i, f in enumerate(sh.Faces)
                if abs(f.CenterOfMass.x - W / 2) < 1e-6
            ][0]
            bottom = [
                "Face%d" % (i + 1) for i, f in enumerate(sh.Faces) if abs(f.CenterOfMass.z) < 1e-6
            ][0]
            ins = _add(self.doc, self.body, "PartDesign::Draft", "INS", after)
            ins.Base = (pad, [side])
            ins.NeutralPlane = (pad, [bottom])
            ins.Angle = 2.0
        self.doc.recompute()
        return ins

    def _d5_delete(self, obj):
        self.body.removeObject(obj)
        self.doc.removeObject(obj.Name)
        self.doc.recompute()

    def _d5_restored(self, pad, b0, before):
        volume, subs = before
        self._valid(b0)
        self.assertIs(b0.Base[0], pad)
        self.assertEqual(list(b0.Base[1]), subs)
        self.assertAlmostEqual(b0.Shape.Volume, volume, places=5)

    def _d5_rescued(self, b0, ins):
        # the insert must really have exercised the rescue: Edge1 follows into the new feature
        self.assertIs(b0.Base[0], ins)
        self.assertFalse(b0.Base[1][0].startswith("?"), "the insert did not rescue Edge1")

    def _d5_insert_delete(self, kind):
        pad, b0, before = self._d5_dressed_pad()
        ins = self._d5_insert(pad, kind)
        self._d5_rescued(b0, ins)
        self._d5_delete(ins)
        self._d5_restored(pad, b0, before)

    def test_insert_then_delete_draft_restores_references(self):
        self._d5_insert_delete("draft")

    def test_insert_then_delete_chamfer_restores_references(self):
        self._d5_insert_delete("chamfer")

    def test_insert_then_move_after_consumer_restores_references(self):
        pad, b0, before = self._d5_dressed_pad()
        ins = self._d5_insert(pad, "draft")
        self._d5_rescued(b0, ins)
        self.body.removeObject(ins)
        self.body.insertObject(ins, b0, True)
        self.doc.recompute()
        self._d5_restored(pad, b0, before)

    def test_insert_save_reload_delete_restores_references(self):
        pad, b0, before = self._d5_dressed_pad()
        ins = self._d5_insert(pad, "draft")
        self._d5_rescued(b0, ins)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "d5.FCStd")
            self.doc.saveAs(path)
            App.closeDocument(self.doc.Name)
            self.doc = App.openDocument(path)
            self.body = self.doc.getObject("Body")
            self.doc.recompute()
            pad = self.doc.getObject("Pad")
            b0 = self.doc.getObject("Fillet")
            ins = self.doc.getObject("INS")
            self._d5_rescued(b0, ins)
            self._d5_delete(ins)
            self._d5_restored(pad, b0, before)

    def test_double_insert_then_delete_both_restores_references(self):
        for order in ("newest_first", "oldest_first"):
            with self.subTest(order=order):
                App.closeDocument(self.doc.Name)
                self.setUp()
                pad, b0, before = self._d5_dressed_pad()
                ins1 = self._d5_insert(pad, "draft")
                self._d5_rescued(b0, ins1)
                ins2 = self._dressup("PartDesign::Chamfer", ins1, ["Edge5"], 0.5, after=ins1)
                self.doc.recompute()
                self.assertIs(b0.Base[0], ins2)
                first, second = (ins2, ins1) if order == "newest_first" else (ins1, ins2)
                self._d5_delete(first)
                self._d5_delete(second)
                self._d5_restored(pad, b0, before)

    def test_insert_then_edit_pad_then_delete_stays_missing(self):
        """The original edge no longer exists in the Pad: restoring by name finds nothing, and the
        reference is reported missing instead of landing on another edge."""
        pad, b0, before = self._d5_dressed_pad()
        ins = self._d5_insert(pad, "draft")
        self._d5_rescued(b0, ins)
        csk = _sketch(self.doc, self.body, "CircleSketch", after=self.doc.getObject("Sketch"))
        _circle(csk, 0, 0, 10.0)
        pad.Profile = csk
        self.doc.recompute()
        self._d5_delete(ins)
        self.assertIs(b0.Base[0], pad)
        self.assertTrue(
            all(s.startswith("?") for s in b0.Base[1]),
            "a reference resolved after the Pad lost its edges: %r" % (b0.Base[1],),
        )
        self.assertIn("Invalid", b0.State)

    def test_manual_retarget_after_rescue_is_kept(self):
        pad, b0, before = self._d5_dressed_pad()
        ins = self._d5_insert(pad, "draft")
        self._d5_rescued(b0, ins)
        # the user picks another edge of the inserted feature, by hand
        b0.Base = (ins, ["Edge2", "Edge3"])
        self.doc.recompute()
        chosen = [_edge_sig(ins.Shape, sub) for sub in b0.Base[1]]
        self.assertNotEqual(chosen, [_edge_sig(pad.Shape, s) for s in before[1]])
        self._d5_delete(ins)
        self.assertNotEqual(list(b0.Base[1]), before[1])

    def test_rejected_second_rescue_then_move_both_after_restores_references(self):
        """A Draft consumer's face is rescued into a boss, then a polar pattern of the boss is
        inserted behind it and the identity gate rejects the second rescue, so the link already
        carries the missing mark for the boss's name. Moving boss and pattern behind the consumer
        must still give the original face back."""
        pad = self._pad()
        sh = pad.Shape

        def face(pred):
            return [
                "Face%d" % (i + 1) for i, f in enumerate(sh.Faces) if pred(f)
            ][0]

        def normal(f):
            u0, u1, v0, v1 = f.ParameterRange
            return f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)

        b0 = _add(self.doc, self.body, "PartDesign::Draft", "B0")
        b0.Base = (pad, [face(lambda f: (normal(f) - V(0, 1, 0)).Length < 1e-6)])
        b0.NeutralPlane = (pad, [face(lambda f: abs(f.CenterOfMass.z) < 1e-6)])
        b0.Angle = 2.0
        self.doc.recompute()
        self._valid(b0)
        before = (b0.Shape.Volume, list(b0.Base[1]))
        bs = _sketch(self.doc, self.body, "BossSk", z=L, after=pad)
        _rect(bs, 12.0, 4.0, 4.0, 4.0)
        boss = _add(self.doc, self.body, "PartDesign::Pad", "INSBo", bs)
        boss.Profile = bs
        boss.Length = 3.0
        pat = _add(self.doc, self.body, "PartDesign::PolarPattern", "INS", boss)
        pat.Originals = [boss]
        pat.Axis = (_plane(self.body, "Z_Axis"), [""])
        pat.Angle = 360
        pat.Occurrences = 3
        self.doc.recompute()
        self.assertIs(b0.Base[0], pat)
        for obj in (boss, pat):
            self.body.removeObject(obj)
        self.body.insertObject(boss, b0, True)
        self.body.insertObject(pat, boss, True)
        self.doc.recompute()
        self._d5_restored(pad, b0, before)
