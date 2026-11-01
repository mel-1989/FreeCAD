# SPDX-License-Identifier: LGPL-2.1-or-later
"""Scenes and helpers shared by the lane 6b-A GUI tests (edge flange, hem, convert-from-solid, jog) and their screenshot scenes."""

import contextlib
import os
import tempfile

import FreeCAD
import FreeCADGui as Gui
import Part

V = FreeCAD.Vector

PL, W = 60.0, 30.0    # plate length (x) and width (y)


def new_doc(name):
    doc = FreeCAD.newDocument(name)
    doc.UndoMode = 1
    return doc


def plate(doc, T=2.0, name="Plate"):
    box = doc.addObject("Part::Box", name)
    box.Length, box.Width, box.Height = PL, W, T
    doc.recompute()
    return box


def end_edge(obj, which, z=None):
    """Name of the straight edge on the ``which`` ('+x', '-x', '+y') end of a plate, at height z (default: the top)."""
    sh = obj.Shape
    z = sh.BoundBox.ZMax if z is None else z
    for i, e in enumerate(sh.Edges):
        a, b = e.Vertexes[0].Point, e.Vertexes[-1].Point
        if abs(a.z - z) > 1e-9 or abs(b.z - z) > 1e-9 or not isinstance(e.Curve, Part.Line):
            continue
        if which == "+x" and abs(a.x - PL) < 1e-9 and abs(b.x - PL) < 1e-9:
            return "Edge%d" % (i + 1)
        if which == "-x" and abs(a.x) < 1e-9 and abs(b.x) < 1e-9:
            return "Edge%d" % (i + 1)
        if which == "+y" and abs(a.y - W) < 1e-9 and abs(b.y - W) < 1e-9:
            return "Edge%d" % (i + 1)
    raise AssertionError("edge %s not found" % which)


def tip_edge(obj):
    """Name of the straight edge across the end of an up-standing flange wall on the inner side (lowest x, highest z)."""
    best = None
    for i, e in enumerate(obj.Shape.Edges):
        if not isinstance(e.Curve, Part.Line) or abs(e.Length - W) > 1e-9:
            continue
        a = e.Vertexes[0].Point
        key = (round(a.z, 6), -round(a.x, 6))
        if best is None or key > best[0]:
            best = (key, "Edge%d" % (i + 1))
    return best[1]


def face_with_normal(obj, axis, sign):
    """Name of the face whose normal is ``sign`` along ``axis`` (0 x, 1 y, 2 z); the largest when several."""
    best = None
    for i, f in enumerate(obj.Shape.Faces):
        if not isinstance(f.Surface, Part.Plane):
            continue
        n = f.normalAt(0, 0)
        if abs(n[axis] - sign) < 1e-9 and (best is None or f.Area > best[1]):
            best = ("Face%d" % (i + 1), f.Area)
    if best is None:
        raise AssertionError("no face")
    return best[0]


def line_sketch(doc, xc, z, name="JogLine"):
    """A sketch holding one straight line across the plate at x = xc, on the plane z."""
    sk = doc.addObject("Sketcher::SketchObject", name)
    sk.Placement = FreeCAD.Placement(V(0, 0, z), FreeCAD.Rotation())
    sk.addGeometry(Part.LineSegment(V(xc, 0, 0), V(xc, W, 0)), False)
    doc.recompute()
    return sk


def cylinder(doc, name="Cyl"):
    cyl = doc.addObject("Part::Cylinder", name)
    cyl.Radius, cyl.Height = 15.0, 2.0
    doc.recompute()
    return cyl


def circle_edge(obj):
    return next("Edge%d" % (i + 1) for i, e in enumerate(obj.Shape.Edges) if isinstance(e.Curve, Part.Circle))


def curved_face(obj):
    return next("Face%d" % (i + 1) for i, f in enumerate(obj.Shape.Faces) if not isinstance(f.Surface, Part.Plane))


def box_source(doc, dims=(40.0, 30.0, 20.0), name="Src", at=(0.0, 0.0, 0.0)):
    box = doc.addObject("Part::Box", name)
    box.Length, box.Width, box.Height = dims
    box.Placement = FreeCAD.Placement(V(*at), FreeCAD.Rotation())
    doc.recompute()
    return box


def edge_between(obj, f1, f2):
    a, b = obj.Shape.getElement(f1), obj.Shape.getElement(f2)
    hits = ["Edge%d" % (i + 1) for i, e in enumerate(obj.Shape.Edges)
            if any(e.isSame(x) for x in a.Edges) and any(e.isSame(x) for x in b.Edges)]
    assert len(hits) == 1, hits
    return hits[0]


def tray(obj):
    """(fixed face, bend edges, open faces) of a box turned into an open tray: the bottom stays, the four sides bend,
    the top is open."""
    bottom = face_with_normal(obj, 2, -1)
    sides = [face_with_normal(obj, ax, s) for ax, s in ((0, -1), (0, 1), (1, -1), (1, 1))]
    return bottom, [edge_between(obj, bottom, s) for s in sides], [face_with_normal(obj, 2, 1)]


def u_channel(obj):
    """(fixed face, bend edges, open faces) of a box turned into a U channel: bottom, left and right walls."""
    bottom = face_with_normal(obj, 2, -1)
    walls = [face_with_normal(obj, 0, -1), face_with_normal(obj, 0, 1)]
    opens = [face_with_normal(obj, 2, 1), face_with_normal(obj, 1, -1), face_with_normal(obj, 1, 1)]
    return bottom, [edge_between(obj, bottom, w) for w in walls], opens


def props(obj, names):
    out = {}
    for n in names:
        v = getattr(obj, n)
        if hasattr(v, "Value"):
            out[n] = v.Value
        elif isinstance(v, tuple):
            out[n] = (v[0].Name, tuple(v[1]))
        elif hasattr(v, "Name") and hasattr(v, "Shape"):
            out[n] = v.Name
        else:
            out[n] = v
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


def select(obj, *subs):
    Gui.Selection.clearSelection()
    if not subs:
        Gui.Selection.addSelection(obj.Document.Name, obj.Name)
    for s in subs:
        Gui.Selection.addSelection(obj.Document.Name, obj.Name, s)


# ---------------------------------------------------------------- screenshot scenes (a later integration step wires them in)

#: parameters of the demo part and the panel scenes; every key can be overridden by a scene's params
PART = {"thickness": 2.0, "flange_length": 14.0, "flange_angle": 90.0, "flange_radius": 2.0, "hem_length": 8.0,
        "jog_offset": 8.0, "jog_x": 36.0}


def scene_part(doc, params=None):
    """A sheet-metal plate with an edge flange on its +x end, a closed hem on its -x end and a jog across the middle,
    plus a box converted to an open tray beside it. Returns the objects by role."""
    import SheetMetalConvertSolid as CS
    import SheetMetalEdgeFlange as EF
    import SheetMetalHemFeature as HM
    import SheetMetalJog as JG

    q = dict(PART)
    q.update(params or {})
    T = float(q["thickness"])
    base = plate(doc, T)
    flange = EF.make_edge_flange(doc, base, [end_edge(base, "+x")], length=float(q["flange_length"]),
                                 angle=float(q["flange_angle"]), radius=float(q["flange_radius"]))
    hem = HM.make_hem(doc, flange, [end_edge(flange, "-x", T)], hemType="Closed", length=float(q["hem_length"]))
    line = line_sketch(doc, float(q["jog_x"]), T)
    jog = JG.make_jog(doc, hem, face_with_normal(hem, 2, 1), line, offset=float(q["jog_offset"]), offsetType="outside",
                      angle=90.0, radius=float(q["flange_radius"]))
    src = box_source(doc, (40.0, 30.0, 20.0), "TraySource", (0.0, 50.0, 0.0))
    fixed, bends, opens = tray(src)
    conv = CS.make_convert_solid(doc, src, fixed, bends, opens, thickness=T, radius=float(q["flange_radius"]), ripGap=1.0)
    doc.recompute()
    if FreeCAD.GuiUp:    # distinct colours so each feature reads in the screenshots
        base.ViewObject.Visibility = False
        flange.ViewObject.Visibility = False
        hem.ViewObject.Visibility = False
        src.ViewObject.Visibility = False
        jog.ViewObject.ShapeColor = (0.90, 0.50, 0.15)
        conv.ViewObject.ShapeColor = (0.20, 0.50, 0.80)
    return {"base": base, "flange": flange, "hem": hem, "jog": jog, "line": line, "src": src, "conv": conv,
            "tray": (fixed, bends, opens)}


def scene_panel(doc, objs, params):
    """The panel a widget-grab scene shows: built on the scene part, filled from ``params`` (a fixed state, nothing applied)."""
    import SMEdgePanels as P

    p = dict(PART)
    p.update(params)
    kind = p["panel"]
    if kind == "edgeflange":
        panel = P.EdgeFlangePanel(doc, base=objs["base"], edges=[end_edge(objs["base"], "+x")])
        panel.length.setValue(float(p["flange_length"]))
        panel.angle.setValue(float(p["flange_angle"]))
        panel.radius.setValue(float(p["flange_radius"]))
    elif kind == "hem":
        panel = P.HemPanel(doc, base=objs["flange"], edges=[end_edge(objs["flange"], "-x", float(p["thickness"]))])
        panel.length.setValue(float(p["hem_length"]))
    elif kind == "jog":
        panel = P.JogPanel(doc, base=objs["hem"], face=face_with_normal(objs["hem"], 2, 1), sketch=objs["line"])
        panel.offset.setValue(float(p["jog_offset"]))
        panel.radius.setValue(float(p["flange_radius"]))
    elif kind == "convert":
        fixed, bends, opens = objs["tray"]
        panel = P.ConvertSolidPanel(doc, source=objs["src"], fixed=fixed, bends=bends, opens=opens)
        panel.thickness.setValue(float(p["thickness"]))
        panel.radius.setValue(float(p["flange_radius"]))
    else:
        raise ValueError("unknown panel " + kind)
    return panel


def grab_png(panel, path, size):
    """Save the panel's form as a PNG at a fixed size and style, with no focus or selection state."""
    from PySide import QtWidgets

    w = panel.form
    w.setStyle(QtWidgets.QStyleFactory.create("Fusion"))
    for t in w.findChildren(QtWidgets.QListWidget):
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
