# SPDX-License-Identifier: LGPL-2.1-or-later
"""Small PartDesign documents for the F6 refactor tests (not collected: name does not start with test_)."""

import FreeCAD
import Part
import Sketcher
from FreeCAD import Vector as V


def new_body(doc, name="Body"):
    return doc.addObject("PartDesign::Body", name)


def _plane(body, role):
    return next(f for f in body.Origin.OriginFeatures if f.Role == role)


def rect_sketch(body, w, h, name="Sketch", offset=0.0):
    """Centred rectangle on XY with named constraints Width and Height."""
    sk = body.newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = (_plane(body, "XY_Plane"), [""])
    sk.MapMode = "FlatFace"
    if offset:
        sk.AttachmentOffset = FreeCAD.Placement(V(0, 0, offset), FreeCAD.Rotation())
    p = [V(-w / 2, -h / 2, 0), V(w / 2, -h / 2, 0), V(w / 2, h / 2, 0), V(-w / 2, h / 2, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(p[i], p[(i + 1) % 4]), False)
    for i in range(4):
        sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
    sk.addConstraint(Sketcher.Constraint("Horizontal", 0))
    sk.addConstraint(Sketcher.Constraint("Horizontal", 2))
    sk.addConstraint(Sketcher.Constraint("Vertical", 1))
    sk.addConstraint(Sketcher.Constraint("Vertical", 3))
    sk.renameConstraint(sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, w)), "Width")
    sk.renameConstraint(sk.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, h)), "Height")
    sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, 0, 1, -w / 2))
    sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, 0, 1, -h / 2))
    return sk


def pad(body, sk, length, name="Pad"):
    f = body.newObject("PartDesign::Pad", name)
    f.Profile = sk
    f.Length = length
    return f


def pocket(body, sk, length, name="Pocket"):
    f = body.newObject("PartDesign::Pocket", name)
    f.Profile = sk
    f.Length = length
    return f


def plate_with_slot(doc, name="Body", w=40.0, h=20.0, t=10.0, slot=6.0, slot_depth=3.0):
    """Pad, then a square pocket in the top face. Returns (body, sketch, pad, slot sketch, pocket)."""
    body = new_body(doc, name)
    sk = rect_sketch(body, w, h, "Base")
    p = pad(body, sk, t, "Pad")
    sk2 = rect_sketch(body, slot, slot, "SlotSketch", offset=t)
    pk = pocket(body, sk2, slot_depth, "Pocket")
    doc.recompute()
    return body, sk, p, sk2, pk


def add_noop_pattern(body, original, sketch, name="NoopPattern"):
    """A LinearPattern with one occurrence: its shape equals its base feature's shape."""
    lp = body.newObject("PartDesign::LinearPattern", name)
    lp.Originals = [original]
    lp.Direction = (sketch, ["H_Axis"])
    lp.Length = 10.0
    lp.Occurrences = 1
    body.Tip = lp
    body.Document.recompute()
    return lp


def add_duplicate_pocket(body, like_sketch_size=6.0, offset=10.0, depth=3.0, name="PocketAgain"):
    """A second pocket identical to the first slot: it removes nothing more."""
    sk = rect_sketch(body, like_sketch_size, like_sketch_size, name + "Sketch", offset=offset)
    pk = pocket(body, sk, depth, name)
    body.Document.recompute()
    return pk


def two_bodies(doc):
    a = plate_with_slot(doc, "BodyA")[0]
    b = new_body(doc, "BodyB")
    sk = rect_sketch(b, 12.0, 12.0, "BSketch")
    pad(b, sk, 7.0, "BPad")
    doc.recompute()
    return a, b
