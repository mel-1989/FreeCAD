# SPDX-License-Identifier: LGPL-2.1-or-later
"""Scenes and helpers shared by the lane B SheetMetal GUI tests (corner relief, lightening cutout, lofted bend,
bend tables in the Unfold panel, DXF export) and by the screenshot scenes.

The constructions are the ones the feature tests in ``SMTests`` use: a tray (a plate with edge flanges on two
adjacent edges) for the corner relief, a plate and an L part with a closed sketch for the cutout, and the open
rectangle and round profiles of the lofted bend cases.
"""

import contextlib
import os
import tempfile

import FreeCAD
import FreeCADGui as Gui
import Part

import SheetMetalEdgeFlange as EF
import SheetMetalLoftedBend as LB
from SMTests import testCornerRelief as TCR
from SMTests import testLighteningCutout as TLC
from SMTests import testLoftedBend as TLB
from SMTests.testOracle import Case, build_base, unfold_target_face
from SMTests.testUnfoldFeature import feature_unfold

V = FreeCAD.Vector


def new_doc(name):
    doc = FreeCAD.newDocument(name)
    doc.UndoMode = 1
    return doc


# ---------------------------------------------------------------- corner relief

def tray(doc, T=2.0, r_t=1.0, angle=90.0, length=15.0, k=0.42):
    """A 40 x 30 plate with edge flanges on +x and +y, so two bends meet at the corner edge. Returns (tray, corner edge)."""
    box = TCR.make_box(doc, T)
    t = EF.make_edge_flange(doc, box, [TCR.find_edge(box, "+x"), TCR.find_edge(box, "+y")], length=length,
                            angle=angle, radius=r_t * T, kfactor=k)
    doc.recompute()
    return t, TCR.thickness_corner_edge(t.Shape, T)


def other_edge(shape, not_names):
    """The name of a straight edge of ``shape`` that is not one of ``not_names`` (a plain sheet edge, no corner)."""
    for i, e in enumerate(shape.Edges):
        name = "Edge%d" % (i + 1)
        if name not in not_names and e.Curve.TypeId == "Part::GeomLine":
            return name
    raise AssertionError("no edge")


# ---------------------------------------------------------------- lightening cutout

def plate(doc, T=2.0):
    obj = doc.addObject("Part::Feature", "Plate")
    obj.Shape = Part.makeBox(90.0, 60.0, T, V(0, 0, 0))
    doc.recompute()
    face = [f for f in obj.Shape.Faces if f.normalAt(0, 0).z > 0.9][0]
    return obj, TLC.face_name(obj.Shape, face), face


def circle_sketch(doc, face, r=5.0, du=0.0, dv=0.0, name="Profile"):
    sk = TLC.make_sketch(doc, face, TLC.circle_geo(0, 0, r)[0], du=du, dv=dv)
    sk.Label = name
    return sk


def bent_part(doc):
    """An L part (60 + 30 mm legs, T = 2, R = 2) with its big planar face: (base, face name, face)."""
    base = build_base(doc, Case("Lx", 2.0, 1.0, 0.42, [60.0, 30.0], [90.0]))
    face = TLC.planar_big(base.Shape)
    return base, TLC.face_name(base.Shape, face), face


def sketch_near_bend(doc, base, face, gap, r=5.0):
    """A circle of radius ``r`` whose edge is ``gap`` mm from the nearest bend tangent line (negative: across it)."""
    origin, u, w, n = TLC.face_frame(face)
    e = TLC.tangent_edges(base.Shape, face)[0]
    d0 = TLC.line_dist(origin, e.Vertexes[0].Point, e.Vertexes[-1].Point)
    sign = 1.0 if (e.CenterOfMass - origin).dot(u) > 0 else -1.0
    return circle_sketch(doc, face, r, du=sign * (d0 - (r + gap)))


# ---------------------------------------------------------------- lofted bend

def profiles(doc, index=0, dz=None):
    """Profile objects A and B of lofted case ``index`` (see ``SMTests.testLoftedBend.CASES``)."""
    return TLB.make_profiles(doc, TLB.CASES[index])


def tilted_profile(doc, angle=10.0):
    """A copy of the case 0 profile B tilted, so the planes of A and B are not parallel."""
    a, b = profiles(doc, 0)
    b.Shape = b.Shape.copy().rotate(V(0, 0, 40), V(1, 0, 0), angle)
    return a, b


# ---------------------------------------------------------------- unfold and bend tables

def unfold_of(doc, case=None, material="_manual", k=0.4, old=None):
    """An Unfold feature (V1 unfolder by default, so it carries bend data) on ``case`` (a testOracle Case)."""
    case = case or Case("U1", 2.0, 2.0, 0.4, [40.0, 25.0], [90.0])
    base = build_base(doc, case)
    return base, feature_unfold(doc, base, unfold_target_face(base.Shape), material=material, k=k, old=old)


# ---------------------------------------------------------------- generic helpers

def props(obj, names):
    out = {}
    for n in names:
        v = getattr(obj, n)
        if hasattr(v, "Value"):
            v = v.Value
        elif hasattr(v, "Name") and hasattr(v, "Shape"):
            v = v.Name
        elif isinstance(v, FreeCAD.Vector):
            v = (v.x, v.y, v.z)
        elif isinstance(v, (list, tuple)):
            v = list(v)
        out[n] = v
    return out


def snapshot(doc):
    """What a cancelled or refused panel must leave alone: object names, undo depth, every shape volume."""
    return (sorted(o.Name for o in doc.Objects), doc.UndoCount, doc.RedoCount,
            {o.Name: round(o.Shape.Volume, 9) for o in doc.Objects if hasattr(o, "Shape") and not o.Shape.isNull()
             and o.Shape.Solids})


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
    Gui.Selection.clearSelection()
    for n in list(FreeCAD.listDocuments()):
        FreeCAD.closeDocument(n)


# ---------------------------------------------------------------- screenshot scenes (fixtures/gui/scenes.json)

#: parameters of the demo part and of the panel scenes; every key can be overridden by a scene's params
SCENE = {"T": 2.0, "relief": "Round", "relief_size": 4.0, "hole": 5.0, "pitch": 14.0, "loft": 0, "loft_T": 2.0}


def scene_part(doc, params=None):
    """A demo part: a tray with a round corner relief, a plate with a 2 x 2 grid of round cutouts, and a lofted
    bend between two profiles. Deterministic; returns the objects by role."""
    import SheetMetalBendCornerRelief as CR
    import SheetMetalLighteningCutout as LC
    import SheetMetalLoftedBend as LBend

    q = dict(SCENE)
    q.update(params or {})
    t, edge = tray(doc, float(q["T"]))
    relief = CR.make_corner_relief(doc, t, edge, reliefType=q["relief"], size=float(q["relief_size"]), kfactor=0.42)
    pl, fname, face = plate(doc, float(q["T"]))
    pl.Placement = FreeCAD.Placement(V(80, 0, 0), FreeCAD.Rotation())
    doc.recompute()
    face = pl.Shape.getElement(fname)
    sk = circle_sketch(doc, face, float(q["hole"]), du=-15.0, dv=-10.0)
    cutout = LC.make_lightening_cutout(doc, pl, fname, sk, Pattern="Grid", CountX=2, CountY=2,
                                       PitchX=float(q["pitch"]), PitchY=float(q["pitch"]))
    a, b = profiles(doc, int(q["loft"]))
    for o in (a, b):
        o.Placement = FreeCAD.Placement(V(0, 150, 0), FreeCAD.Rotation())
    doc.recompute()
    loft = LBend.make_lofted_bend(doc, a, b, thickness=float(q["loft_T"]))
    if FreeCAD.GuiUp:
        for o, rgb in ((relief, (0.45, 0.62, 0.82)), (cutout, (0.55, 0.60, 0.70)), (loft, (0.90, 0.50, 0.15))):
            o.ViewObject.ShapeColor = rgb
        for o in (t, pl, a, b):
            o.ViewObject.Visibility = False
    return {"tray": t, "edge": edge, "relief": relief, "plate": pl, "face": fname, "sketch": sk, "cutout": cutout,
            "profile_a": a, "profile_b": b, "loft": loft}


def _pick(combo, data):
    i = combo.findData(data)
    if i >= 0:
        combo.setCurrentIndex(i)


def scene_panel(doc, objs, params):
    """The panel a ``sm_panel`` scene shows: built on the scene part, filled from ``params`` (a fixed state, nothing
    applied)."""
    import SMShapePanels as P

    p = dict(SCENE)
    p.update(params)
    kind = p["panel"]
    if kind == "relief":
        panel = P.CornerReliefPanel(doc, base=objs["tray"], edge=objs["edge"])
        _pick(panel.kind, p.get("relief", "Round"))
        panel.size.setValue(float(p.get("relief_size", 4.0)))
        _pick(panel.size_mode, p.get("size_mode", "Absolute"))
        panel.ratio.setValue(float(p.get("ratio", 2.0)))
        panel.rotation.setValue(float(p.get("rotation", 0.0)))
    elif kind == "cutout":
        panel = P.LighteningCutoutPanel(doc, base=objs["plate"], face=objs["face"], sketch=objs["sketch"])
        _pick(panel.pattern, p.get("pattern", "Grid"))
        panel.count_x.setValue(int(p.get("count_x", 2)))
        panel.count_y.setValue(int(p.get("count_y", 2)))
        panel.pitch_x.setValue(float(p.get("pitch", 14.0)))
        panel.pitch_y.setValue(float(p.get("pitch", 14.0)))
    elif kind == "lofted":
        panel = P.LoftedBendPanel(doc, profile_a=objs["profile_a"], profile_b=objs["profile_b"])
        panel.thickness.setValue(float(p.get("loft_T", 2.0)))
        _pick(panel.side, p.get("side", "mid"))
        panel.facets.setValue(int(p.get("facets", 4)))
    elif kind == "dxf":
        _base, unfold = unfold_of(doc)
        panel = P.ExportDxfPanel(doc, obj=unfold, path="/tmp/flat.dxf")
    else:
        raise ValueError("unknown panel " + kind)
    return panel


def grab_png(panel, path, size):
    """Save the panel's form as a PNG at a fixed size and style, with no focus or selection state."""
    from PySide import QtWidgets

    w = panel.form
    w.setStyle(QtWidgets.QStyleFactory.create("Fusion"))
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
