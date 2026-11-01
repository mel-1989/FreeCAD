# SPDX-License-Identifier: LGPL-2.1-or-later
"""The combined demo part of the ``sm_part`` screenshot scene (G07 slice 6b): one sheet-metal chain carrying a corner relief, a lightening
cutout, an edge flange, a hem and a jog. Built with the feature API ``make_*`` functions only; deterministic."""

import FreeCAD
import Part

from SMGuiTests import fixture_a as FA
from SMTests import testCornerRelief as TCR
from SMTests import testEdgeFlange as TEF
from SMTests import testLighteningCutout as TLC

V = FreeCAD.Vector

#: parameters of the demo part; every key can be overridden by a scene's params
PART = {"thickness": 2.0, "flange_length": 14.0, "flange_angle": 90.0, "flange_radius": 2.0, "hem_length": 8.0,
        "jog_offset": 8.0, "jog_x": 20.0, "relief_size": 4.0, "hole": 2.5}


def _edge_at(shape, axis, value, z):
    """Name of the straight edge at ``axis`` == value and height z (a plate end edge)."""
    for i, e in enumerate(shape.Edges):
        a, b = e.Vertexes[0].Point, e.Vertexes[-1].Point
        if abs(a.z - z) < 1e-9 and abs(b.z - z) < 1e-9 and e.Curve.TypeId == "Part::GeomLine" \
                and abs(getattr(a, axis) - value) < 1e-9 and abs(getattr(b, axis) - value) < 1e-9:
            return "Edge%d" % (i + 1)
    raise AssertionError("edge not found")


def attach_all(doc):
    """Give every sheet-metal feature of ``doc`` its view provider (the commands do this; bare ``make_*`` calls do not, and a
    feature without one is not drawn)."""
    import SMEdgeViewProviders
    import SMShapeViewProviders

    for o in list(doc.Objects):
        if getattr(o, "Proxy", None) is None:
            continue
        SMEdgeViewProviders.attach(o)
        SMShapeViewProviders.attach(o)


def scene_part(doc, params=None):
    import SheetMetalBendCornerRelief as CR
    import SheetMetalEdgeFlange as EF
    import SheetMetalHemFeature as HM
    import SheetMetalJog as JG
    import SheetMetalLighteningCutout as LC

    q = dict(PART)
    q.update(params or {})
    T = float(q["thickness"])
    R = float(q["flange_radius"])
    # chain 1: plate, edge flange on +x, closed hem on -x, jog across the middle (opposite ends, so each end face stays a plain rectangle)
    base = TEF.make_box(doc, T)
    edge_x = _edge_at(base.Shape, "x", TEF.PL, T)    # names first: they do not depend on the placement
    base.Placement = FreeCAD.Placement(V(0, -50, 0), FreeCAD.Rotation())
    doc.recompute()
    flange = EF.make_edge_flange(doc, base, [edge_x], length=float(q["flange_length"]),
                                 angle=float(q["flange_angle"]), radius=R)
    hem = HM.make_hem(doc, flange, [_edge_at(flange.Shape, "x", 0.0, T)], hemType="Closed", length=float(q["hem_length"]))
    line = FA.line_sketch(doc, float(q["jog_x"]), T)
    line.Placement = FreeCAD.Placement(V(0, -50, T), FreeCAD.Rotation())
    jog = JG.make_jog(doc, hem, FA.face_with_normal(hem, 2, 1), line, offset=float(q["jog_offset"]), offsetType="outside",
                      angle=90.0, radius=R)
    # chain 2, beside it: a tray (two bends meet at the corner), a round corner relief and a lightening cutout
    box = TEF.make_box(doc, T, "TrayPlate")
    tray = EF.make_edge_flange(doc, box, [TEF.find_edge(box, "+x"), TEF.find_edge(box, "+y")], length=12.0, angle=90.0, radius=R,
                               kfactor=0.42)
    doc.recompute()
    relief = CR.make_corner_relief(doc, tray, TCR.thickness_corner_edge(tray.Shape, T), reliefType="Round",
                                   size=float(q["relief_size"]), kfactor=0.42)
    doc.recompute()
    face = TLC.planar_big(relief.Shape)
    fname = TLC.face_name(relief.Shape, face)
    sk = TLC.make_sketch(doc, face, TLC.circle_geo(0, 0, float(q["hole"]))[0], du=-8.0, dv=-4.0)
    cutout = LC.make_lightening_cutout(doc, relief, fname, sk, Pattern="Grid", CountX=1, CountY=2, PitchX=10.0, PitchY=8.0)
    doc.recompute()
    if FreeCAD.GuiUp:
        attach_all(doc)
        # only the final feature shows
        for o in (base, flange, hem, box, tray, relief):
            o.ViewObject.Visibility = False
        jog.ViewObject.ShapeColor = (0.90, 0.50, 0.15)
        cutout.ViewObject.ShapeColor = (0.20, 0.50, 0.80)
    return {"base": base, "box": box, "tray": tray, "relief": relief, "cutout": cutout, "flange": flange, "hem": hem, "line": line, "jog": jog}
