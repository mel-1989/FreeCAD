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
import zipfile
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

    # -------------------------------------------------------------- round 7 repairs (G01, repair round 1)
    def _draft_face(self, base, normal, name, angle, after):
        sh = base.Shape

        def n_of(f):
            u0, u1, v0, v1 = f.ParameterRange
            return f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)

        side = [
            "Face%d" % (i + 1)
            for i, f in enumerate(sh.Faces)
            if (n_of(f) - normal).Length < 1e-6 and isinstance(f.Surface, Part.Plane)
        ][0]
        bottom = [
            "Face%d" % (i + 1)
            for i, f in enumerate(sh.Faces)
            if abs(f.CenterOfMass.z) < 1e-6 and isinstance(f.Surface, Part.Plane)
        ][0]
        d = _add(self.doc, self.body, "PartDesign::Draft", name, after)
        d.Base = (base, [side])
        d.NeutralPlane = (base, [bottom])
        d.Angle = angle
        self.doc.recompute()
        return d

    def test_undo_of_a_move_does_not_retarget_to_a_stale_index(self):
        """Fails on 3992f8bd76: a fillet inserted in front of a chamfer (no recompute), moved
        behind it and the move undone. The element cache of the fillet was filled from its shape
        in the other position, where "Edge10" was an unrelated vertical edge, and FreeCAD's own
        geometric search carried the chamfer's top edge over to it, silently. A retargeted link
        may only take over an element that is provably the same as the previous target's."""
        self.doc.UndoMode = 1
        pad = self._pad()
        cons = self._dressup("PartDesign::Chamfer", pad, ["Edge12", "Edge10"], 0.5)
        self.doc.recompute()
        self._valid(cons)
        sigs = [_edge_sig(pad.Shape, e) for e in cons.Base[1]]
        self.doc.openTransaction("insert")
        fil = _add(self.doc, self.body, "PartDesign::Fillet", "InsFillet", pad)
        fil.Base = (pad, ["Edge2"])
        fil.Radius = 0.8
        self.doc.commitTransaction()
        self.doc.openTransaction("move")
        self.body.removeObject(fil)
        self.body.insertObject(fil, cons, True)
        self.doc.commitTransaction()
        self.doc.recompute()
        self.doc.undo()
        self.doc.recompute()
        base, subs = cons.Base
        for sub, expected in zip(subs, sigs):
            if sub.startswith("?"):
                continue
            self.assertEqual(
                _edge_sig(base.Shape, sub),
                expected,
                "%s on %s is a different element than the one the chamfer referenced" % (sub, base.Name),
            )

    def _undoable_insert(self):
        self.doc.UndoMode = 1
        pad, b0, before = self._d5_dressed_pad()
        self.doc.openTransaction("insert")
        ins = self._d5_insert(pad, "draft")
        self.doc.commitTransaction()
        self._d5_rescued(b0, ins)
        return pad, b0, before

    def _delete_in_transaction(self):
        ins = self.doc.getObject("INS")
        self.doc.openTransaction("delete")
        self.body.removeObject(ins)
        self.doc.removeObject("INS")
        self.doc.commitTransaction()
        self.doc.recompute()

    def test_undo_redo_of_an_insert_keeps_the_rescue_records(self):
        """Fails on 3992f8bd76: the undo/redo Paste brought the rescued name back without its
        rescue record, so deleting the insert afterwards left the fillet edge missing."""
        pad, b0, before = self._undoable_insert()
        self.doc.undo()
        self.doc.recompute()
        self.doc.redo()
        self.doc.recompute()
        self._d5_rescued(b0, self.doc.getObject("INS"))
        self._delete_in_transaction()
        self._d5_restored(pad, b0, before)

    def test_undo_of_a_delete_keeps_the_rescue_records(self):
        """Fails on 3992f8bd76: restoring on the delete erased the record the undo of the delete
        needs, so the second delete could not give the original edge back."""
        pad, b0, before = self._undoable_insert()
        self._delete_in_transaction()
        self._d5_restored(pad, b0, before)
        self.doc.undo()
        self.doc.recompute()
        self._d5_rescued(b0, self.doc.getObject("INS"))
        self._delete_in_transaction()
        self._d5_restored(pad, b0, before)

    def test_middle_delete_of_a_chain_keeps_the_original_reachable(self):
        """Fails on 3992f8bd76: two inserts rescued in a row, the first deleted. The second's
        element is re-tracked by FreeCAD's mapping to a new mapped name no record was keyed on,
        so deleting the second left the fillet edge missing."""
        pad, b0, before = self._d5_dressed_pad()
        ins1 = self._d5_insert(pad, "draft")
        self._d5_rescued(b0, ins1)
        ins2 = self._draft_face(ins1, V(0, 1, 0), "INS2", 3.0, ins1)
        self.assertIs(b0.Base[0], ins2)
        self._d5_delete(ins1)
        self._d5_delete(ins2)
        self._d5_restored(pad, b0, before)

    def test_malformed_rescue_record_attribute_keeps_the_link(self):
        """Fails on 3992f8bd76: a non-numeric HistRescue threw while reading the link and the
        whole link was dropped. A malformed record is no record; the link is read as usual."""
        import zipfile

        pad, b0, before = self._d5_dressed_pad()
        ins = self._d5_insert(pad, "draft")
        self._d5_rescued(b0, ins)
        with tempfile.TemporaryDirectory() as tmp:
            good = os.path.join(tmp, "good.FCStd")
            bad = os.path.join(tmp, "bad.FCStd")
            self.doc.saveAs(good)
            changed = 0
            with zipfile.ZipFile(good) as zin, zipfile.ZipFile(bad, "w", zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    data = zin.read(item.filename)
                    if item.filename == "Document.xml":
                        text = data.decode("utf-8")
                        changed = text.count('HistRescue="1"')
                        data = text.replace('HistRescue="1"', 'HistRescue="abc"').encode("utf-8")
                    zout.writestr(item, data)
            self.assertGreater(changed, 0, "the saved file carries no rescue record")
            App.closeDocument(self.doc.Name)
            self.doc = App.openDocument(bad)
            b0 = self.doc.getObject("Fillet")
            self.assertIsNotNone(b0.Base[0], "the link was dropped")
            self.doc.recompute()
            self.assertIs(b0.Base[0], self.doc.getObject("INS"))
            self.assertEqual(len(b0.Base[1]), 2)
            self.assertFalse(b0.Base[1][0].startswith("?"), "the rescued edge went missing")

    def test_relink_by_geometry_keeps_the_rescue_records(self):
        """Fails before the setValueSameElements repair: a hole and a linear pattern inserted in
        front of a dress-up (both rescue its edges), the pair moved away and back in front of it, then
        behind the dress-up, that move undone without a recompute, then the pair deleted. Each removal relinks the dress-up to the same edge of the
        previous feature by geometry, under a new mapped name; the rescue records were keyed on
        the old name and the original edges did not come back."""
        self.doc.UndoMode = 1
        pad, b0, before = self._d5_dressed_pad()
        self.doc.openTransaction("insert")
        hs = _sketch(self.doc, self.body, "HS", z=L, after=pad)
        _circle(hs, -10.0, 0.0, 2.0)
        hp = _add(self.doc, self.body, "PartDesign::Pocket", "HP", hs)
        hp.Profile = hs
        hp.Length = 2 * L
        lp = _add(self.doc, self.body, "PartDesign::LinearPattern", "LP", hp)
        lp.Originals = [hp]
        lp.Direction = (hs, ["H_Axis"])
        lp.Length = 8.0
        lp.Occurrences = 2
        self.doc.commitTransaction()
        self.doc.recompute()
        self.assertIs(b0.Base[0], lp)
        self.assertFalse(b0.Base[1][0].startswith("?"), "the insert did not rescue Edge1")
        for target in (pad, b0):
            self.doc.openTransaction("move")
            for obj in (hs, hp, lp):
                self.body.removeObject(obj)
            after = target
            for obj in (hs, hp, lp):
                self.body.insertObject(obj, after, True)
                after = obj
            self.doc.commitTransaction()
            self.doc.recompute()
        self.doc.undo()
        self.doc.openTransaction("delete")
        for obj in (lp, hp, hs):
            self.body.removeObject(obj)
            self.doc.removeObject(obj.Name)
        self.doc.commitTransaction()
        self.doc.recompute()
        self._d5_restored(pad, b0, before)

    def test_undo_on_the_same_target_does_not_retarget_to_a_stale_index(self):
        """Fails before the repair of Paste: the dress-up's names belong to the Pad while it is
        linked to a hole inserted in front of it (no recompute yet). Moving the hole behind and
        away from the Pad and undoing leaves the link on the hole with those Pad names; the
        hole's element cache is filled by plain index from its previous shape and the geometric
        search carried the top edge over to an unrelated one of the same length."""
        self.doc.UndoMode = 1
        pad = self._pad()
        sh = pad.Shape
        top = [
            "Edge%d" % (i + 1)
            for i, e in enumerate(sh.Edges)
            if abs(e.CenterOfMass.z - L) < 1e-6 and abs(e.Length - W) < 1e-6
        ]
        edges = top[:1] + _line_edges(sh, V(0, 0, 1), (W / 2,), (H / 2,))
        cons = self._dressup("PartDesign::Fillet", pad, edges, 0.5)
        self.doc.recompute()
        self._valid(cons)
        sigs = [_edge_sig(pad.Shape, e) for e in cons.Base[1]]
        self.doc.openTransaction("insert")
        hs = _sketch(self.doc, self.body, "HS", z=L, after=pad)
        _circle(hs, 0.0, -3.0, 2.0)
        hp = _add(self.doc, self.body, "PartDesign::Pocket", "HP", hs)
        hp.Profile = hs
        hp.Length = 2 * L
        self.doc.commitTransaction()
        for target in (pad, cons):
            self.doc.openTransaction("move")
            for obj in (hs, hp):
                self.body.removeObject(obj)
            after = target
            for obj in (hs, hp):
                self.body.insertObject(obj, after, True)
                after = obj
            self.doc.commitTransaction()
            self.doc.recompute()
            if target is pad:
                self.doc.undo()
                self.doc.recompute()
        self.doc.recompute()
        base, subs = cons.Base
        for sub, expected in zip(subs, sigs):
            if sub.startswith("?"):
                continue
            self.assertEqual(
                _edge_sig(base.Shape, sub),
                expected,
                "%s on %s is a different element than the one the fillet referenced" % (sub, base.Name),
            )

    # -------------------------------------------------------------- round 8 repairs (G01, repair round 2)
    def _boss_at(self, cx, after, z=L, name="BO"):
        bs = _sketch(self.doc, self.body, name + "Sk", z=z, after=after)
        _rect(bs, cx, 0.0, 4.0, 4.0)
        boss = _add(self.doc, self.body, "PartDesign::Pad", name, bs)
        boss.Profile = bs
        boss.Length = 3.0
        return boss

    def test_redo_does_not_take_an_unrelated_element_at_the_same_index(self):
        """Fails on 35b073aff5: a draft on a pentagon prism, a second draft inserted in front of
        it (the consumer's face is rescued and carries a record), a boss inserted in front of that
        and both moved behind the consumer, then undo and redo with a recompute in between. The
        undo's recompute re-tracks the face outside any transaction; redo restores the draft's
        shape but not the link, the name is missing, and upstream's search by the plain index of
        the old shape was accepted without the gate that only runs for a pending retarget: the
        consumer stayed Up-to-date on a face of a different area."""
        self.doc.UndoMode = 1
        pts = [(-20, -10), (20, -10), (26, 6), (0, 14), (-24, 4)]
        sk = _sketch(self.doc, self.body, "Sketch")
        for i, a in enumerate(pts):
            b = pts[(i + 1) % len(pts)]
            sk.addGeometry(Part.LineSegment(V(a[0], a[1], 0), V(b[0], b[1], 0)), False)
        for i in range(len(pts)):
            sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % len(pts), 1))
        pad = self.body.newObject("PartDesign::Pad", "Pad")
        pad.Profile = sk
        pad.Length = L
        self.doc.recompute()
        sh = pad.Shape

        def flat_normal(f):
            u0, u1, v0, v1 = f.ParameterRange
            return f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)

        sides = [
            "Face%d" % (i + 1)
            for i, f in enumerate(sh.Faces)
            if isinstance(f.Surface, Part.Plane) and abs(flat_normal(f).z) < 1e-6
        ]
        bottom = [
            "Face%d" % (i + 1)
            for i, f in enumerate(sh.Faces)
            if isinstance(f.Surface, Part.Plane) and abs(f.CenterOfMass.z) < 1e-6
        ][0]
        cons = _add(self.doc, self.body, "PartDesign::Draft", "Cons")
        cons.Base = (pad, [sides[4]])
        cons.NeutralPlane = (pad, [bottom])
        cons.Angle = 2.0
        self.doc.recompute()
        self._valid(cons)
        sig = _face_sig(pad.Shape, cons.Base[1][0])

        def ok_or_missing(tag):
            base, subs = cons.Base
            if subs[0].startswith("?"):
                return
            self.assertEqual(
                _face_sig(base.Shape, subs[0]), sig, "%s: %s is another face" % (tag, subs[0])
            )

        self.doc.openTransaction("insert")
        d1 = _add(self.doc, self.body, "PartDesign::Draft", "ID1", pad)
        d1.Base = (pad, [sides[1]])
        d1.NeutralPlane = (pad, [bottom])
        d1.Angle = 3.0
        self.doc.recompute()
        self.doc.commitTransaction()
        self.assertIs(cons.Base[0], d1)
        self.assertFalse(cons.Base[1][0].startswith("?"), "the insert did not rescue the face")
        self.doc.openTransaction("insert boss")
        bo = self._boss_at(-12.0, pad)
        self.doc.commitTransaction()
        ok_or_missing("insert")
        self.doc.openTransaction("move")
        for obj in (bo.Profile[0], bo):
            self.body.removeObject(obj)
        after = cons
        for obj in (bo.Profile[0], bo):
            self.body.insertObject(obj, after, True)
            after = obj
        self.doc.recompute()
        self.doc.commitTransaction()
        ok_or_missing("move")
        self.doc.undo()
        self.doc.recompute()
        ok_or_missing("undo")
        self.doc.redo()
        self.doc.recompute()
        ok_or_missing("redo")

    def test_relink_by_geometry_after_a_double_insert_keeps_the_original_reachable(self):
        """Fails on 35b073aff5: a draft and a boss inserted in front of a fillet (both rescue its
        first edge, a chain of two records), the fillet moved behind the draft and then behind
        the boss, the draft deleted, the boss deleted. The delete re-tracks the rescued name
        onto the very name the newest record would give back; the move of that record onto its
        own original name erased the older record of the chain and left a record that pointed at
        itself, so the first edge was not given back (the other edge, never rescued, was)."""
        self.doc.UndoMode = 1
        pad, b0, before = self._d5_dressed_pad()

        def step(f):
            self.doc.openTransaction("step")
            f()
            self.doc.recompute()
            self.doc.commitTransaction()

        step(lambda: self._d5_insert(pad, "draft"))
        ins = self.doc.getObject("INS")
        self._d5_rescued(b0, ins)
        step(lambda: self._boss_at(-10.0, ins))
        bo = self.doc.getObject("BO")
        self.assertIs(b0.Base[0], bo)
        for target in (ins, bo):

            def move(target=target):
                self.body.removeObject(b0)
                self.body.insertObject(b0, target, True)

            step(move)
        for obj in (ins, bo):

            def delete(obj=obj):
                self.body.removeObject(obj)
                self.doc.removeObject(obj.Name)

            step(delete)
        self._d5_restored(pad, b0, before)

    # -------------------------------------------------------------- slice 2: anchors and supports
    def _draft_with_fillet_on_edge(self, k):
        self.doc.UndoMode = 1
        pad = self._pad()
        sh = pad.Shape
        side = [
            "Face%d" % (i + 1)
            for i, f in enumerate(sh.Faces)
            if isinstance(f.Surface, Part.Plane) and abs(f.normalAt(0, 0).z) < 1e-6
        ][0]
        bottom = ["Face%d" % (i + 1) for i, f in enumerate(sh.Faces) if abs(f.CenterOfMass.z) < 1e-6][0]
        draft = _add(self.doc, self.body, "PartDesign::Draft", "Draft")
        draft.Base = (pad, [side])
        draft.NeutralPlane = (pad, [bottom])
        draft.Angle = 3.0
        self.doc.recompute()
        cons = _add(self.doc, self.body, "PartDesign::Fillet", "Cons")
        cons.Base = (draft, ["Edge%d" % (k + 1)])
        cons.Radius = 0.3
        self.doc.recompute()
        self._valid(cons)
        return pad, draft, cons

    def _boss_group(self, pad):
        bs = _sketch(self.doc, self.body, "BS", z=L, after=pad)
        _rect(bs, -12.0, 0.0, 4.0, 4.0)
        bp = _add(self.doc, self.body, "PartDesign::Pad", "BP", bs)
        bp.Profile = bs
        bp.Length = 3.0
        return bs, bp

    def test_redo_after_insert_and_move_does_not_take_the_mirror_twin(self):
        """Fails on 4646cdee17 and on fork/main: a Fillet on an edge of a Draft, a boss inserted in
        front of the Draft (committed, not recomputed), moved behind the Fillet with a recompute
        inside the transaction, the move undone and redone. FreeCAD's geometric search ran from
        the element cache of the Draft, which held the shape of the other configuration, and
        carried the reference over to the edge at the mirrored position (x = -20 instead of 20),
        silently. The search now runs from the edge the link designated when it last resolved."""
        pad, draft, cons = self._draft_with_fillet_on_edge(4)
        sig = _edge_sig(draft.Shape, cons.Base[1][0])
        self.doc.openTransaction("insert")
        bs, bp = self._boss_group(pad)
        self.doc.commitTransaction()
        self.doc.openTransaction("move")
        after = cons
        for obj in (bs, bp):
            self.body.removeObject(obj)
            self.body.insertObject(obj, after, True)
            after = obj
        self.doc.recompute()
        self.doc.commitTransaction()
        self._refs_same(cons, [sig])
        self.doc.undo()
        self.doc.recompute()
        self.doc.redo()
        self.doc.recompute()
        base, subs = cons.Base
        for sub in subs:
            if sub.startswith("?"):
                continue
            self.assertEqual(
                _edge_sig(base.Shape, sub),
                sig,
                "%s on %s is not the edge the Fillet referenced" % (sub, base.Name),
            )

    def _attached_pad(self):
        """A pad with a sketch attached to one side face; returns (pad, sketch, face names)."""
        pad = self._pad()

        def side(x, y):
            for i, f in enumerate(pad.Shape.Faces):
                c = f.CenterOfMass
                if abs(c.x - x) < 1e-6 and abs(c.y - y) < 1e-6 and abs(c.z - L / 2) < 1e-6:
                    return "Face%d" % (i + 1)
            raise AssertionError("no side face")

        fa, fb = side(0, -H / 2), side(-W / 2, 0)
        cs = _add(self.doc, self.body, "Sketcher::SketchObject", "AttachedSketch")
        cs.AttachmentSupport = (pad, [fa])
        cs.MapMode = "FlatFace"
        self.doc.recompute()
        self.assertEqual(list(cs.AttachmentSupport[0][1]), [fa])
        return pad, cs, fa, fb

    # property status bit 31 is User3, which AttachExtension sets around the write that the
    # attachment engine makes when it normalises the support's sub names
    def _engine_rewrites_support(self, pad, cs, face):
        cs.setPropertyStatus("AttachmentSupport", 31)
        try:
            engine = Part.AttachEngine("Attacher::AttachEnginePlane")
            engine.References = [(pad, face)]
            engine.Mode = "FlatFace"
            engine.writeParametersToFeature(cs)
        finally:
            cs.setPropertyStatus("AttachmentSupport", -31)
        self.doc.recompute()
        self.assertEqual(list(cs.AttachmentSupport[0][1]), [face])

    def _cut_the_left_face(self, pad):
        """Replace the rectangle's left edge by two slanted ones: the face made from it is gone."""
        sk = pad.Profile[0]
        sk.delGeometry(3)
        a = sk.addGeometry(Part.LineSegment(V(-W / 2, H / 2, 0), V(-W / 2 - 5, 0, 0)), False)
        b = sk.addGeometry(Part.LineSegment(V(-W / 2 - 5, 0, 0), V(-W / 2, -H / 2, 0)), False)
        sk.addConstraint(Sketcher.Constraint("Coincident", 2, 2, a, 1))
        sk.addConstraint(Sketcher.Constraint("Coincident", a, 2, b, 1))
        sk.addConstraint(Sketcher.Constraint("Coincident", b, 2, 0, 1))
        self.doc.recompute()

    def _support_is(self, cs, pad, face_sig):
        obj, subs = cs.AttachmentSupport[0]
        self.assertIs(obj, pad)
        self.assertFalse(subs[0].startswith("?"), "the support went missing: %r" % (subs,))
        self.assertEqual(_face_sig(pad.Shape, subs[0]), face_sig)
        self.assertNotIn("Invalid", cs.State)

    def test_support_rewritten_by_the_attacher_is_given_back_when_its_face_is_gone(self):
        """PropertyLinkSubList carries rescue records like PropertyLinkSub: the attachment engine
        rewrote the support from face A to face B; when B is gone, A comes back by name. On
        fork/main the sketch goes Invalid with the support '?Face4'."""
        pad, cs, fa, fb = self._attached_pad()
        sig_a = _face_sig(pad.Shape, fa)
        self._engine_rewrites_support(pad, cs, fb)
        self._cut_the_left_face(pad)
        self._support_is(cs, pad, sig_a)

    def test_support_rescue_record_is_written_to_the_file_and_survives_reload(self):
        pad, cs, fa, fb = self._attached_pad()
        sig_a = _face_sig(pad.Shape, fa)
        self._engine_rewrites_support(pad, cs, fb)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "support.FCStd")
            self.doc.saveAs(path)
            with zipfile.ZipFile(path) as z:
                xml = z.read("Document.xml").decode()
            self.assertRegex(xml, r"<Link obj=\"Pad\"[^>]*HistRescue=\"1\"[^>]*HistSub0=\"%s\"" % fa)
            name = self.doc.Name
            App.closeDocument(name)
            self.doc = App.openDocument(path)
        self.body = self.doc.getObject("Body")
        pad = self.doc.getObject("Pad")
        cs = self.doc.getObject("AttachedSketch")
        self.assertEqual(list(cs.AttachmentSupport[0][1]), [fb])
        self._cut_the_left_face(pad)
        self._support_is(cs, pad, sig_a)

    def test_support_rescue_record_follows_an_undo_and_redo_of_the_rewrite(self):
        self.doc.UndoMode = 1
        pad, cs, fa, fb = self._attached_pad()
        sig_a = _face_sig(pad.Shape, fa)
        self.doc.openTransaction("rewrite")
        self._engine_rewrites_support(pad, cs, fb)
        self.doc.commitTransaction()
        self.doc.undo()
        self.doc.recompute()
        self.assertEqual(list(cs.AttachmentSupport[0][1]), [fa])
        self.doc.redo()
        self.doc.recompute()
        self.assertEqual(list(cs.AttachmentSupport[0][1]), [fb])
        self._cut_the_left_face(pad)
        self._support_is(cs, pad, sig_a)
