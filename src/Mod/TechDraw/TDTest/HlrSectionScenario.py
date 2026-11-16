# SPDX-License-Identifier: LGPL-2.1-or-later

# One scripted sequence of edits on a page with a section view, used by HlrSectionCacheTest: it is
# run in the test process (hidden-line cache on) and in a separate FreeCADCmd process with
# CADFORK_HLR_CACHE=0, and the two lists of snapshots must be equal.  Run as a script it writes the
# list as JSON to the file named in SCENARIO_OUT.
# Public sources: OCCT reference documentation for BRepAlgoAPI_Cut and BinTools; FreeCAD's TechDraw
# DrawViewSection.cpp.

import hashlib
import json
import os
import shutil
import tempfile

import FreeCAD
import Part
import TechDraw

V = FreeCAD.Vector

# the part of the debug dumps that a hit rewrites: the cut shape, the prepared shape and the final
# section faces (not the tool: the cut itself changes its tolerances after the miss dumped it)
DUMPS = ("DVSCutShape.brep", "DVSPreparedShape.brep", "DVSFinalSectionFaces.brep")


def edges_of(view):
    out = []
    for kind, edges in (("visible", view.getVisibleEdges()), ("hidden", view.getHiddenEdges())):
        for e in edges:
            out.append([kind, e.Curve.TypeId, list(e.firstVertex().Point), list(e.lastVertex().Point), e.Length])
    for kind, verts in (("visible", view.getVisibleVertexes()), ("hidden", view.getHiddenVertexes())):
        for v in verts:
            out.append([kind + "-vertex", list(v)])
    out.append(TechDraw.viewPartAsSvg(view))
    return out


def dumps_of(directory):
    out = {}
    for name in DUMPS:
        path = os.path.join(directory, name)
        if os.path.exists(path):
            data = open(path, "rb").read()
            s = Part.Shape()
            s.read(path)
            out[name] = [hashlib.sha256(data).hexdigest(), s.Volume if s.Solids else None, len(s.Faces)]
            os.remove(path)
    return out


def build_model(doc):
    base = Part.makeBox(30, 20, 10)
    base = base.makeFillet(2.0, [e for e in base.Edges if abs(e.Vertexes[0].Z - e.Vertexes[-1].Z) > 5])
    base = base.cut(Part.makeCylinder(3, 12, V(15, 10, -1)))
    blk = doc.addObject("Part::Feature", "Blk")
    blk.Shape = base
    link = doc.addObject("App::Link", "Lnk")
    link.LinkedObject = blk
    link.Placement = FreeCAD.Placement(V(50, 0, 0), FreeCAD.Rotation(V(0, 0, 1), 30))
    return blk, link


def build_page(doc, blk, link, template):
    page = doc.addObject("TechDraw::DrawPage", "Page")
    tpl = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
    tpl.Template = template
    page.Template = tpl
    page.Scale = 1.0
    base = doc.addObject("TechDraw::DrawViewPart", "Base")
    page.addView(base)
    base.Source = [blk, link]
    base.Direction = V(0, -1, 0)
    base.ScaleType = "Custom"
    base.Scale = 1.0
    sec = doc.addObject("TechDraw::DrawViewSection", "Section")
    page.addView(sec)
    sec.BaseView = base
    sec.Source = [blk, link]
    sec.SectionNormal = V(0, 1, 0)
    sec.SectionDirection = "Down"
    sec.SectionOrigin = V(15, 10, 5)
    sec.ScaleType = "Custom"
    sec.Scale = 1.0
    sec.X = 120
    det = doc.addObject("TechDraw::DrawViewDetail", "Detail")
    page.addView(det)
    det.BaseView = sec
    det.Source = [blk, link]
    det.AnchorPoint = V(0, 0, 0)
    det.Radius = 10.0
    det.ScaleType = "Custom"
    det.Scale = 2.0
    det.X = 200
    sos = doc.addObject("TechDraw::DrawViewSection", "SecOfSec")
    page.addView(sos)
    sos.BaseView = sec
    sos.Source = [blk, link]
    sos.SectionNormal = V(1, 0, 0)
    sos.SectionDirection = "Right"
    sos.SectionOrigin = V(15, 10, 5)
    sos.ScaleType = "Custom"
    sos.Scale = 1.0
    sos.X = 120
    sos.Y = 100
    return page, base, sec, det, sos


def run(template, workdir=None):
    """Run the sequence; returns [[label, snapshot], ...] and the cache counters per step."""
    own = workdir is None
    workdir = workdir or tempfile.mkdtemp(prefix="hlr8c_")
    old_cwd = os.getcwd()
    os.chdir(workdir)
    dbg = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/TechDraw/debug")
    old_dbg = dbg.GetBool("debugSection", False)
    dbg.SetBool("debugSection", True)
    try:
        return _run(template, workdir)
    finally:
        dbg.SetBool("debugSection", old_dbg)
        os.chdir(old_cwd)
        if own:
            shutil.rmtree(workdir, ignore_errors=True)


def _run(template, workdir):
    doc = FreeCAD.newDocument("HlrSectionScenario")
    doc.UndoMode = 1
    blk, link = build_model(doc)
    page, base, sec, det, sos = build_page(doc, blk, link, template)
    doc.recompute()
    steps = []

    def stats():
        s = TechDraw.hlrCacheStats()
        return [s["hits"], s["misses"]]

    def record(label, before):
        after = stats()
        steps.append([label, {
            "section": edges_of(sec), "detail": edges_of(det), "secOfSec": edges_of(sos),
            "base": edges_of(base), "dumps": dumps_of(workdir)}, [after[0] - before[0], after[1] - before[1]]])

    def act(label, fn, only=None):
        for n in DUMPS:
            if os.path.exists(os.path.join(workdir, n)):
                os.remove(os.path.join(workdir, n))
        before = stats()
        fn()
        if only is not None:
            only.touch()
        doc.recompute()
        record(label, before)

    act("build", lambda: sec.touch())
    # move, undo, redo (real transactions)
    def moved():
        doc.openTransaction("move")
        sec.X = sec.X.Value + 15
        doc.recompute()
        doc.commitTransaction()
    act("move", moved)
    act("undo", lambda: doc.undo())
    act("redo", lambda: doc.redo())
    act("scale 0.5", lambda: setattr(sec, "Scale", 0.5))
    act("scale back", lambda: setattr(sec, "Scale", 1.0))
    act("rotation 90", lambda: setattr(sec, "Rotation", 90.0))
    act("rotation back", lambda: setattr(sec, "Rotation", 0.0))
    for flag in ("SmoothVisible", "SeamVisible", "IsoVisible", "HardHidden", "SmoothHidden", "SeamHidden", "IsoHidden"):
        old = getattr(sec, flag)
        act("flag %s toggled" % flag, lambda f=flag, o=old: setattr(sec, f, not o))
        act("flag %s back" % flag, lambda f=flag, o=old: setattr(sec, f, o))
    act("origin +0.001", lambda: setattr(sec, "SectionOrigin", V(15.001, 10, 5)))
    act("origin back", lambda: setattr(sec, "SectionOrigin", V(15, 10, 5)))
    act("normal changed", lambda: setattr(sec, "SectionNormal", V(0.2, 1, 0)))
    act("normal back", lambda: setattr(sec, "SectionNormal", V(0, 1, 0)))
    act("fuse before cut", lambda: setattr(sec, "FuseBeforeCut", True))
    act("fuse back", lambda: setattr(sec, "FuseBeforeCut", False))
    act("trim after cut", lambda: setattr(sec, "TrimAfterCut", True))
    act("trim back", lambda: setattr(sec, "TrimAfterCut", False))
    act("hatch", lambda: setattr(sec, "HatchScale", 2.0), only=sec)
    act("cut surface display", lambda: setattr(sec, "CutSurfaceDisplay", "Hide"), only=sec)
    act("cut surface display back", lambda: setattr(sec, "CutSurfaceDisplay", "SvgHatch"), only=sec)
    act("base moved", lambda: setattr(base, "X", base.X.Value + 20))
    act("link touch", lambda: link.touch())
    act("same-value edit", lambda: blk.touch())
    act("link moved 0.001", lambda: setattr(link, "Placement", FreeCAD.Placement(V(50.001, 0, 0), FreeCAD.Rotation(V(0, 0, 1), 30))))
    act("link moved back", lambda: setattr(link, "Placement", FreeCAD.Placement(V(50, 0, 0), FreeCAD.Rotation(V(0, 0, 1), 30))))
    def real_edit():
        s = blk.Shape.cut(Part.makeCylinder(2, 12, V(6, 6, -1)))
        blk.Shape = s
    act("real edit", real_edit)
    act("cosmetic line on the section", lambda: sec.makeCosmeticLine(V(0, 0, 0), V(5, 5, 0)), only=sec)
    act("cosmetics cleared", lambda: sec.clearCosmeticEdges(), only=sec)
    # save and reopen
    path = os.path.join(workdir, "scenario.FCStd")
    doc.saveAs(path)
    FreeCAD.closeDocument(doc.Name)
    for n in DUMPS:
        if os.path.exists(os.path.join(workdir, n)):
            os.remove(os.path.join(workdir, n))
    before = stats()
    doc = FreeCAD.openDocument(path)
    doc.recompute()
    sec = doc.getObject("Section"); det = doc.getObject("Detail"); sos = doc.getObject("SecOfSec"); base = doc.getObject("Base")
    record("reopen", before)
    FreeCAD.closeDocument(doc.Name)
    return steps


if __name__ == "__main__" or "SCENARIO_OUT" in os.environ:
    _out = os.environ.get("SCENARIO_OUT")
    if _out:
        import sys
        _steps = run(os.environ["SCENARIO_TEMPLATE"])
        with open(_out, "w") as _f:
            json.dump(_steps, _f)
