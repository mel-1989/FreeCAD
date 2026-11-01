# SPDX-License-Identifier: LGPL-2.1-or-later
"""Scenes and helpers shared by the Weldment GUI tests and the screenshot scenes."""

import contextlib
import os
import tempfile

import FreeCAD
import FreeCADGui as Gui
import Part

import Profiles
from WTests import oracle

V = FreeCAD.Vector


def rhs(frac=0.5):
    p = oracle.kind_profile("RHS", frac)
    return p.family, p.size


def rhs_sizes(frac_a, frac_b):
    return rhs(frac_a), rhs(frac_b)


def new_doc(name):
    doc = FreeCAD.newDocument(name)
    doc.UndoMode = 1
    return doc


def path_object(doc, name, segments):
    o = doc.addObject("Part::Feature", name)
    o.Shape = Part.Compound([Part.makeLine(V(*a), V(*b)) for a, b in segments])
    return o


def joint(doc, frac=0.5, la=1000.0, lb=1000.0, frac_b=None, a_span=False, b_span=False):
    """Two RHS members meeting at the origin: A along +x, B along +z. Returns (A, B).

    ``a_span`` or ``b_span`` extends that member through the origin to -500 so the other butts against its side
    (a T joint); ``frac_b`` gives B another section."""
    import WeldMember

    fam, size = rhs(frac)
    famb, sizeb = rhs(frac if frac_b is None else frac_b)
    pa = path_object(doc, "PathA", [((-500.0 if a_span else 0.0, 0, 0), (la, 0, 0))])
    pb = path_object(doc, "PathB", [((0, 0, -500.0 if b_span else 0.0), (0, 0, lb))])
    a = WeldMember.make_member(doc, fam, size, [(pa, [""])], "A")
    b = WeldMember.make_member(doc, famb, sizeb, [(pb, [""])], "B")
    doc.recompute()
    return a, b


def bead_world(doc):
    """A base plate and an upright wall on it: (plate, wall, [wall edge names on the plate])."""
    plate = doc.addObject("Part::Feature", "Plate")
    plate.Shape = Part.makeBox(400, 400, 10, V(-200, -200, -10))
    wall = doc.addObject("Part::Feature", "Wall")
    wall.Shape = Part.makeBox(10, 200, 100, V(-5, -100, 0))
    doc.recompute()
    on_plate = []
    for i, e in enumerate(wall.Shape.Edges):
        a, b = e.valueAt(e.FirstParameter), e.valueAt(e.LastParameter)
        if abs(a.z) < 1e-9 and abs(b.z) < 1e-9 and abs(e.Length - 200) < 1e-9:
            on_plate.append("Edge%d" % (i + 1))
    assert len(on_plate) == 2, on_plate
    return plate, wall, on_plate


def face_with_normal(obj, axis, sign):
    """Name of the face of ``obj`` whose normal is ``sign`` along ``axis`` (0 x, 1 y, 2 z)."""
    for i, f in enumerate(obj.Shape.Faces):
        n = f.normalAt(0, 0)
        if abs(n[axis] - sign) < 1e-9:
            return "Face%d" % (i + 1)
    raise AssertionError("no face")


def props(obj, names):
    out = {}
    for n in names:
        v = getattr(obj, n)
        out[n] = v.Value if hasattr(v, "Value") else (v.Name if hasattr(v, "Name") and hasattr(v, "Shape") else v)
    return out


def snapshot(doc):
    """What a cancelled or refused panel must leave alone: object names, undo depth, every shape volume."""
    return (sorted(o.Name for o in doc.Objects), doc.UndoCount, doc.RedoCount,
            {o.Name: round(o.Shape.Volume, 9) for o in doc.Objects if hasattr(o, "Shape") and not o.Shape.isNull()})


@contextlib.contextmanager
def capture_output():
    """Everything written to file descriptors 1 and 2 (the C++ console writes there) while the block runs."""
    import sys

    sys.stdout.flush()
    sys.stderr.flush()
    tmp = tempfile.TemporaryFile(mode="w+b")
    saved = (os.dup(1), os.dup(2))
    os.dup2(tmp.fileno(), 1)
    os.dup2(tmp.fileno(), 2)
    cap = type("Cap", (), {"text": ""})()
    try:
        yield cap
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(saved[0], 1)
        os.dup2(saved[1], 2)
        os.close(saved[0])
        os.close(saved[1])
        tmp.seek(0)
        cap.text = tmp.read().decode("utf-8", "replace")
        tmp.close()


def report_view_text():
    """Text of the report view dock when it exists, else None."""
    from PySide import QtWidgets

    w = Gui.getMainWindow().findChild(QtWidgets.QTextEdit, "Report view")
    return None if w is None else w.toPlainText()


def close_everything():
    if Gui.Control.activeDialog():
        Gui.Control.closeDialog()
    for n in list(FreeCAD.listDocuments()):
        FreeCAD.closeDocument(n)


# ---------------------------------------------------------------- screenshot scenes (fixtures/gui/scenes.json)

#: the portal frame of the 3D scene and the panel scenes; every key can be overridden by a scene's params
FRAME = {"family": "EN SHS", "section": "60x60x4", "span": 1200.0, "eave": 800.0, "leg1": 150.0, "leg2": 150.0,
         "thickness": 8.0, "cap": 8.0, "bead": 5.0}


def scene_frame(doc, params=None):
    """A small portal frame: two posts and a beam (corners mitred), a gusset in the left knee, an end cap on the
    right foot and a fillet weld bead where the left post stands on its base plate. Returns the objects by role."""
    import WeldBead
    import WeldEndCap
    import WeldGusset
    import WeldMember
    import WeldTrim

    q = dict(FRAME)
    q.update(params or {})
    fam, size = q["family"], q["section"]
    span, eave = float(q["span"]), float(q["eave"])
    pl = path_object(doc, "PathPostL", [((0, 0, 0), (0, 0, eave))])
    pr = path_object(doc, "PathPostR", [((span, 0, 0), (span, 0, eave))])
    pb = path_object(doc, "PathBeam", [((0, 0, eave), (span, 0, eave))])
    post_l = WeldMember.make_member(doc, fam, size, [(pl, [""])], "PostL")
    post_r = WeldMember.make_member(doc, fam, size, [(pr, [""])], "PostR")
    beam = WeldMember.make_member(doc, fam, size, [(pb, [""])], "Beam")
    doc.recompute()
    head_l, head_b = WeldTrim.make_corner(doc, post_l, 0, "End", beam, 0, "Start", "Mitre")
    head_r, head_b = WeldTrim.make_corner(doc, post_r, 0, "End", head_b, 0, "End", "Mitre")
    gusset = WeldGusset.make_gusset(doc, head_l, 0, "End", head_b, 0, "Start", float(q["leg1"]), float(q["leg2"]),
                                    float(q["thickness"]))
    cap = WeldEndCap.make_end_cap(doc, post_r, 0, "Start", float(q["cap"]))
    plate = doc.addObject("Part::Feature", "BasePlate")
    plate.Shape = Part.makeBox(300, 300, 10, V(-150, -150, -10))
    doc.recompute()
    half = max(Profiles.get(fam, size).bbox()[1] - Profiles.get(fam, size).bbox()[0],
               Profiles.get(fam, size).bbox()[3] - Profiles.get(fam, size).bbox()[2]) / 2.0
    edges = []
    for i, e in enumerate(post_l.Shape.Edges):
        a, b = e.valueAt(e.FirstParameter), e.valueAt(e.LastParameter)
        if e.Curve.TypeId == "Part::GeomLine" and abs(a.z) < 1e-9 and abs(b.z) < 1e-9 and (
                (abs(abs(a.x) - half) < 1e-9 and abs(abs(b.x) - half) < 1e-9)
                or (abs(abs(a.y) - half) < 1e-9 and abs(abs(b.y) - half) < 1e-9)):
            edges.append("Edge%d" % (i + 1))
    bead = WeldBead.make_bead(doc, plate, post_l, [(post_l, e) for e in edges], float(q["bead"]))
    doc.recompute()
    if FreeCAD.GuiUp:    # distinct colours so the plate, cap, gusset and bead read in the screenshots
        for o, rgb in ((gusset, (0.90, 0.50, 0.15)), (cap, (0.20, 0.50, 0.80)), (bead, (0.80, 0.10, 0.10)),
                       (plate, (0.55, 0.60, 0.70))):
            o.ViewObject.ShapeColor = rgb
    return {"post_l": post_l, "post_r": post_r, "beam": beam, "head_l": head_l, "head_r": head_r, "head_b": head_b,
            "gusset": gusset, "cap": cap, "plate": plate, "bead": bead, "bead_edges": edges, "paths": (pl, pr, pb)}


def _pick(combo, data):
    i = combo.findData(data)
    if i >= 0:
        combo.setCurrentIndex(i)


def scene_panel(doc, objs, params):
    """The panel a ``weld_panel`` scene shows: built on the scene frame, filled from ``params`` (a fixed state,
    nothing applied)."""
    import WeldPanels as P

    p = dict(FRAME)
    p.update(params)
    kind = p["panel"]
    if kind == "member":
        panel = P.MemberPanel(doc, path=[(objs["paths"][2], [""])], family=p["family"])
        panel.set_size(p["family"], p["section"])
        _pick(panel.anchor, p.get("anchor", "Centre"))
        panel.rotation.setValue(float(p.get("rotation", 0.0)))
        panel.mirror.setChecked(bool(p.get("mirror", False)))
    elif kind == "corner":
        panel = P.CornerPanel(doc, a=objs["post_l"], b=objs["beam"], kind=p.get("kind", "Mitre"))
        panel.end_a.setCurrentIndex(panel.end_a.findData("End"))
    elif kind == "gusset":
        panel = P.GussetPanel(doc, a=objs["head_l"], b=objs["head_b"])
        panel.end_a.setCurrentIndex(panel.end_a.findData("End"))
        _pick(panel.shape, p.get("shape", "Triangular"))
        panel.leg1.setValue(float(p["leg1"]))
        panel.leg2.setValue(float(p["leg2"]))
        panel.thickness.setValue(float(p["thickness"]))
        panel.chamfer.setValue(float(p.get("chamfer", 15.0)))
        _pick(panel.placement, p.get("placement", "Centred"))
    elif kind == "endcap":
        panel = P.EndCapPanel(doc, base=objs["post_r"], end="Start")
        _pick(panel.mode, p.get("mode", "Outset"))
        panel.thickness.setValue(float(p["cap"]))
        panel.recess.setValue(float(p.get("recess", 0.0)))
        panel.chamfer.setValue(float(p.get("chamfer", 0.0)))
    elif kind == "bead":
        panel = P.BeadPanel(doc, bodies=[objs["plate"], objs["post_l"]], edges=[(objs["post_l"], e) for e in objs["bead_edges"]])
        _pick(panel.mode, p.get("mode", "Leg z"))
        panel.size.setValue(float(p["bead"]))
    elif kind == "cutlist":
        panel = P.CutListPanel(doc, objects=[objs["head_l"], objs["head_r"], objs["head_b"], objs["gusset"], objs["cap"], objs["bead"]])
    else:
        raise ValueError("unknown panel " + kind)
    return panel


def grab_png(panel, path, size):
    """Save the panel's form as a PNG at a fixed size and style, with no focus or selection state."""
    from PySide import QtWidgets

    w = panel.form
    w.setStyle(QtWidgets.QStyleFactory.create("Fusion"))
    for t in w.findChildren(QtWidgets.QTableWidget):
        t.clearSelection()
        t.setCurrentItem(None)
    w.setFixedSize(*size)
    w.ensurePolished()
    w.layout().activate()
    w.show()
    for _ in range(4):
        QtWidgets.QApplication.processEvents()
    img = w.grab().toImage()
    img.save(path)
    w.hide()
    return img.width(), img.height()
