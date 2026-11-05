# SPDX-License-Identifier: LGPL-2.1-or-later
"""Rendering benchmark of the sketch editor (G10 slice 4c, check 1).

Runs inside the FreeCAD GUI, one launch per process, under the GUI harness (`harness/gui/render_bench.py` in the
workshop folder is the launcher; it passes `PARAMS` and writes nothing itself).  For each size it builds a decoupled
corpus sketch of exactly that many constraints (`SketchDiagnosisCorpus.build_scale`, the generator the solver
benchmarks use) and times four steps in the real editor:

  enter  `ActiveDocument.setEdit(sketch)`
  drag   one mouse move of a drag in progress: Qt press on a vertex, a first move past the drag threshold (not timed),
         then one timed move, then the release (not timed).  These are the events the editor receives from the user, so the
         path is `ViewProviderSketch::mouseMove -> doDragStep -> moveGeometriesTemporary -> draw`
  add    `addConstraint` of one DistanceX, `setDriving(False)` (a reference dimension, so no redundancy), `recompute`
  zoom   one wheel notch sent to the 3D viewport

Every step is timed to the point the scene is rendered, not to the return of the call.  The step runs, then the event
loop is pumped until the 3D view has been quiet for 100 ms.  Per step the record holds:

  call_ms      the call or event send, from before it to its return
  frame_ms     from before it to the last `frameSwapped` of the 3D viewport: the picture is in the framebuffer
  render_ms    the sum of the Coin render traversals (`SoGLRenderAction` from the first to the last node of the scene
               graph) inside that step, so it includes the render traversal and the GL command submission and excludes
               the GPU executing them; with several renders in a step they are summed
  renders      how many traversals the step caused
  timers       when FREECAD_SKETCHER_RENDER_TIMING names a file, the instrumented C++ timers of this step:
               name -> {n, total_ms, max_ms} (processConstraints, rebuildConstraintNodes, updateVirtualSpace,
               drawConstraintIcons, combineConstraintIcons, draw, dragSolve)

Solve time: `dragSolve` is the solver inside the drag move.  For enter and add, the solve runs in the App layer; the
record carries `solve_s`, the median of three `sketch.solve()` of the unchanged sketch, which is what one solve costs here
(enter and add each include about one solve, drag includes dragSolve).  Nothing here changes what is drawn.

The viewport is fixed at 1280x800 logical pixels (the main window is resized); the output records it with the device
pixel ratio, because the render depends on it.
"""

import json
import os
import statistics
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Sketcher
from PySide6 import QtCore, QtGui, QtOpenGLWidgets, QtWidgets

from SketcherTests import SketchDiagnosisCorpus as Corpus

P = PARAMS  # noqa: F821  (injected by harness/gui/gui_wrap.py)
from pivy import coin  # noqa: E402

W, H = int(P.get("width", 1280)), int(P.get("height", 800))
SIZES = [int(s) for s in str(P.get("sizes", "100")).split(",")]
QUIET_NS = int(float(P.get("quiet_ms", 100)) * 1e6)
TIMING_FILE = os.environ.get("FREECAD_SKETCHER_RENDER_TIMING") or None
ns = time.perf_counter_ns
QApp = QtWidgets.QApplication


class Frame:
    swaps = []
    spans = []
    start = None
    unpaired = 0


def pump():
    QApp.processEvents()


def find_gl(mw):
    for g in mw.findChildren(QtOpenGLWidgets.QOpenGLWidget):
        if g.parentWidget() is not None and g.parentWidget().metaObject().className() == "Gui::View3DInventorViewer" and g.isVisible():
            return g
    raise RuntimeError("no visible 3D view OpenGL widget")


def fix_viewport(mw, gl):
    mw.showNormal()
    for _ in range(4):
        pump()
        dx, dy = mw.width() - gl.width(), mw.height() - gl.height()
        if (gl.width(), gl.height()) == (W, H):
            break
        mw.move(0, 25)
        mw.resize(W + dx, H + dy)
        for _ in range(10):
            pump()
            time.sleep(0.02)
    return gl.width(), gl.height()


def quiet(min_ns=0, limit_s=60):
    """Pump until no frame was swapped for QUIET_NS and at least min_ns have passed."""
    last = ns()
    n = len(Frame.swaps)
    start = last
    while True:
        pump()
        t = ns()
        if len(Frame.swaps) != n:
            n, last = len(Frame.swaps), t
        if t - last >= QUIET_NS and t - start >= min_ns:
            return
        if t - start > limit_s * 1e9:
            raise RuntimeError("view never settled")


def timer_lines():
    if not TIMING_FILE or not os.path.exists(TIMING_FILE):
        return []
    with open(TIMING_FILE) as f:
        return f.read().splitlines()


def parse_timers(lines):
    out = {}
    for ln in lines:
        name, sec = ln.split("\t")
        ms = float(sec) * 1000.0
        r = out.setdefault(name, {"n": 0, "total_ms": 0.0, "max_ms": 0.0})
        r["n"] += 1
        r["total_ms"] += ms
        r["max_ms"] = max(r["max_ms"], ms)
    return out


def step(send, wait_ms=0):
    """Run send(); return the step record (see the module docstring)."""
    quiet()
    Frame.swaps, Frame.spans, Frame.start, Frame.unpaired = [], [], None, 0
    before = len(timer_lines())
    t0 = ns()
    send()
    t1 = ns()
    quiet(min_ns=int(wait_ms * 1e6))
    swaps = [s for s in Frame.swaps if s >= t0]
    rec = {
        "call_ms": (t1 - t0) / 1e6,
        "frame_ms": (max(swaps) - t0) / 1e6 if swaps else None,
        "render_ms": sum(Frame.spans) / 1e6 if Frame.spans else None,
        "renders": len(Frame.spans),
        "renders_unpaired": Frame.unpaired,   # traversals that began and never reached the last node: not counted
        "swaps": len(swaps),
    }
    if TIMING_FILE:
        rec["timers"] = parse_timers(timer_lines()[before:])
    return rec


def mouse(gl, typ, x, y, button, buttons):
    lp = QtCore.QPointF(x, y)
    gp = QtCore.QPointF(gl.mapToGlobal(QtCore.QPoint(int(x), int(y))))
    return QtGui.QMouseEvent(typ, lp, lp, gp, button, buttons, QtCore.Qt.NoModifier)


def wheel(gl, x, y, delta):
    lp = QtCore.QPointF(x, y)
    gp = QtCore.QPointF(gl.mapToGlobal(QtCore.QPoint(int(x), int(y))))
    return QtGui.QWheelEvent(lp, gp, QtCore.QPoint(0, 0), QtCore.QPoint(0, delta), QtCore.Qt.NoButton, QtCore.Qt.NoModifier, QtCore.Qt.NoScrollPhase, False)


def fit(view):
    Gui.SendMsgToActiveView("ViewFit")
    quiet(min_ns=int(0.2e9))


def drag_target(sk, view):
    """Viewport pixel of the first rectangle's start point (geometry 0, start)."""
    p = sk.Placement.multVec(sk.getPoint(0, 1))
    x, y = view.getPointOnViewport(p)
    return p, x, y


def centre_on(view, p, height):
    cam = view.getCameraNode()
    cam.position.setValue(p.x, p.y, cam.position.getValue()[2])
    cam.height.setValue(height)
    quiet(min_ns=int(0.2e9))


def drag_step(gl, view, sk, p):
    """Press on the start point of geometry 0, one move past the drag threshold (untimed), the timed move, the release."""
    L, N = QtCore.Qt.LeftButton, QtCore.Qt.NoButton
    start = sk.getPoint(0, 1)
    x, y = [int(round(v)) for v in view.getPointOnViewport(p)]
    QApp.sendEvent(gl, mouse(gl, QtCore.QEvent.MouseMove, x, y, N, N))
    quiet()
    pre = Gui.Selection.getPreselection()
    pre_names = list(pre.SubElementNames) if pre is not None and pre.ObjectName else []
    QApp.sendEvent(gl, mouse(gl, QtCore.QEvent.MouseButtonPress, x, y, L, L))
    quiet()
    QApp.sendEvent(gl, mouse(gl, QtCore.QEvent.MouseMove, x + 8, y + 3, N, L))
    quiet()
    rec = step(lambda: QApp.sendEvent(gl, mouse(gl, QtCore.QEvent.MouseMove, x + 14, y + 6, N, L)))
    rec["preselection"] = pre_names
    QApp.sendEvent(gl, mouse(gl, QtCore.QEvent.MouseButtonRelease, x + 14, y + 6, L, N))
    quiet(min_ns=int(0.3e9))
    rec["moved_mm"] = sk.getPoint(0, 1).distanceToPoint(start)
    return rec


def setup_view(mw, doc):
    """Make `doc` the active document, size its viewer to W x H and bracket its scene graph with the render callbacks.
    The viewer belongs to the document, so this runs for every size."""
    App.setActiveDocument(doc.Name)
    Gui.ActiveDocument = Gui.getDocument(doc.Name)
    Gui.SendMsgToActiveView("ViewFit")
    for _ in range(20):
        pump()
        time.sleep(0.02)
    gl = find_gl(mw)
    size = fix_viewport(mw, gl)
    if size != (W, H):
        raise RuntimeError("viewport is %s, not %s" % (size, (W, H)))
    root = Gui.ActiveDocument.ActiveView.getSceneGraph()
    first, last = coin.SoCallback(), coin.SoCallback()

    def cb_start(_d, action):
        if action.isOfType(coin.SoGLRenderAction.getClassTypeId()):
            Frame.unpaired += Frame.start is not None
            Frame.start = ns()

    def cb_end(_d, action):
        if action.isOfType(coin.SoGLRenderAction.getClassTypeId()):
            if Frame.start is not None:
                Frame.spans.append(ns() - Frame.start)
                Frame.start = None

    first.setCallback(cb_start)
    last.setCallback(cb_end)
    root.insertChild(first, 0)
    root.addChild(last)
    gl.frameSwapped.connect(lambda: Frame.swaps.append(ns()))
    quiet(min_ns=int(0.3e9))
    return gl


def run_size(n, mw, warm=False):
    doc = App.newDocument("RenderBench%d" % n)
    sk, _ = Corpus.build_scale(doc, "S", n + 2, chained=False)
    # The corpus sketch has DOF 0, where a drag cannot move anything. Free the first rectangle from the origin: exactly n constraints, DOF 2.
    free = [i for i, c in enumerate(sk.Constraints) if c.First == -1 and c.Second == 0]
    if len(free) != 2:
        raise RuntimeError("expected two origin datums on the first rectangle, got %s" % free)
    sk.delConstraints(free, True)
    doc.recompute()
    cons = sk.ConstraintCount
    solves = []
    for _ in range(3):
        t0 = time.perf_counter()
        sk.solve()
        solves.append(time.perf_counter() - t0)
    out = {"constraints": cons, "geometry": sk.GeometryCount, "solve_s": statistics.median(solves), "steps": {}}
    gl = setup_view(mw, doc)
    out["gl_size"] = [gl.width(), gl.height()]
    out["dpr"] = gl.devicePixelRatioF()
    out["window"] = [mw.width(), mw.height()]
    view = Gui.ActiveDocument.ActiveView
    if os.environ.get("FREECAD_RENDER_BENCH_NO_CAMERA_ANIMATION"):
        view.setAnimationEnabled(False)   # a second figure for enter, without the 10-step camera fit; off in the baseline
    out["steps"]["enter"] = step(lambda: Gui.ActiveDocument.setEdit(sk.Name))
    out["gl_size_in_edit"] = [gl.width(), gl.height()]   # the task panel of edit mode may change the viewport
    cx, cy = W // 2, H // 2
    QApp.sendEvent(gl, mouse(gl, QtCore.QEvent.MouseMove, cx, cy, QtCore.Qt.NoButton, QtCore.Qt.NoButton))
    quiet(min_ns=int(0.2e9))
    cam = view.getCameraNode()
    h0 = cam.height.getValue()
    out["steps"]["zoom"] = step(lambda: QApp.sendEvent(gl.parentWidget(), wheel(gl, cx, cy, 120)), wait_ms=300)   # the wheel goes to the viewer widget, which is where Quarter reads it
    out["steps"]["zoom"]["camera_height"] = [h0, cam.height.getValue()]
    fit(view)
    p, _, _ = drag_target(sk, view)
    centre_on(view, p, 30.0)   # zoomed on the first rectangle so the press certainly lands on its vertex
    out["steps"]["drag"] = drag_step(gl, view, sk, p)
    fit(view)   # again with the whole sketch in view: the rendered scene is the large one, the pick less certain
    p, _, _ = drag_target(sk, view)
    out["steps"]["drag_fit"] = drag_step(gl, view, sk, p)
    fit(view)

    def add():
        c = Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 10.0)
        idx = sk.addConstraint(c)
        sk.setDriving(idx, False)
        doc.recompute()

    out["steps"]["add"] = step(add)
    out["constraints_after_add"] = sk.ConstraintCount
    Gui.ActiveDocument.resetEdit()
    quiet(min_ns=int(0.2e9))
    App.closeDocument(doc.Name)
    for _ in range(10):
        pump()
        time.sleep(0.02)
    return out


result = {"viewport": [W, H], "sizes": {}, "error": None, "timing_enabled": bool(TIMING_FILE)}
try:
    mw = Gui.getMainWindow()
    run_size(24, mw, warm=True)   # a warm-up for the viewer and the icon caches, not measured
    if TIMING_FILE:
        open(TIMING_FILE, "w").close()   # drop the warm-up lines
    for n in SIZES:
        result["sizes"][str(n)] = run_size(n, mw)
        print("size", n, json.dumps({k: v["frame_ms"] for k, v in result["sizes"][str(n)]["steps"].items()}), flush=True)
    first_size = result["sizes"][str(SIZES[0])]
    result["gl_size"], result["dpr"], result["window"] = first_size["gl_size"], first_size["dpr"], first_size["window"]
except BaseException:
    result["error"] = traceback.format_exc()
    print(result["error"])
if P.get("result"):
    with open(P["result"], "w") as f:
        json.dump(result, f, indent=1)
if result.get("error"):
    raise RuntimeError("SketchRenderBenchmark failed")
