# SPDX-License-Identifier: LGPL-2.1-or-later

"""Drag scenarios for hunting solver branch flips (cad-fork run 053).

Every builder has the shape of SketchBenchmark.py's ``_scenario_*``:
``builder(doc) -> (sketch, (geo, pos), waypoints, spec)``.  Differences:
``waypoints`` is a coarse polyline of ABSOLUTE drag targets, outward leg only;
the runner resamples it to a step length (0.1, 1, 10, 50 mm) and appends the
reverse leg (out and back, for hysteresis).  The first waypoint is replaced by
the dragged point's actual start position.  Units are mm.  ``spec`` keys:
arcs, tangents, loops (as in SketchBenchmark) plus curves (geometry ids sampled
along their parameter) and ellipses (geometry ids whose AngleXU is tracked).
"""
import math

import FreeCAD
import Part
import Sketcher

V = FreeCAD.Vector
D = math.radians


class Sk:
    def __init__(self, doc, name):
        self.o = doc.addObject("Sketcher::SketchObject", name)

    def line(self, x1, y1, x2, y2):
        return self.o.addGeometry(Part.LineSegment(V(x1, y1, 0), V(x2, y2, 0)), False)

    def circle(self, x, y, r):
        return self.o.addGeometry(Part.Circle(V(x, y, 0), V(0, 0, 1), r), False)

    def arc(self, x, y, r, a0, a1):
        return self.o.addGeometry(Part.ArcOfCircle(Part.Circle(V(x, y, 0), V(0, 0, 1), r), a0, a1), False)

    def c(self, *args):
        return self.o.addConstraint(Sketcher.Constraint(*args))

    def fix(self, g, p, x, y):
        self.c("DistanceX", -1, 1, g, p, x)
        self.c("DistanceY", -1, 1, g, p, y)


def circ(cx, cy, r, a0, a1, n=None):
    n = n or max(2, int(abs(a1 - a0) / 5) + 1)
    return [(cx + r * math.cos(D(a0 + (a1 - a0) * i / (n - 1))), cy + r * math.sin(D(a0 + (a1 - a0) * i / (n - 1)))) for i in range(n)]


def S01_arc_tangent_to_line_straightens(doc):
    """Fixed line, arc tangent at its end (centre above, curving up).  Drag the
    arc's end down through the line's extension: the radius goes to infinity and
    the centre must reappear below, which a CCW-only arc cannot represent."""
    s = Sk(doc, "S01")
    ln = s.line(-30, 0, 0, 0)
    s.fix(ln, 1, -30, 0)
    s.fix(ln, 2, 0, 0)
    a = s.arc(0, 10, 10, -math.pi / 2, 0)
    s.c("Tangent", ln, 2, a, 1)
    path = [(10, 10), (10, 3), (10, 0.5), (10, -0.5), (10, -3), (10, -10)]
    return s.o, (a, 2), path, {"arcs": [a], "tangents": []}


def S02_arc_end_dragged_past_start(doc):
    """Arc on a fixed circle (centre fixed, radius 20); start kept level with
    the centre.  The end is dragged round the circle past the start."""
    s = Sk(doc, "S02")
    a = s.arc(0, 0, 20, 0.0, math.pi / 2)
    s.fix(a, 3, 0, 0)
    s.c("Radius", a, 20.0)
    s.c("Horizontal", a, 3, a, 1)
    path = circ(0, 0, 20, 90, 400, 60)
    return s.o, (a, 2), path, {"arcs": [a]}


def _slot(doc, name, horizontal):
    """Right centre starts at x=50.03 so a 0.1/1/10 mm grid never lands on the exactly coincident centres (moveGeometry throws there)."""
    s = Sk(doc, name)
    al = s.arc(0.0, 0.0, 10.0, math.pi / 2, 3 * math.pi / 2)
    ar = s.arc(50.03, 0.0, 10.0, -math.pi / 2, math.pi / 2)
    lt = s.line(0.0, 10.0, 50.03, 10.0)
    lb = s.line(50.03, -10.0, 0.0, -10.0)
    s.c("Tangent", al, 2, lb, 2)
    s.c("Tangent", lb, 1, ar, 1)
    s.c("Tangent", ar, 2, lt, 2)
    s.c("Tangent", lt, 1, al, 1)
    s.fix(al, 3, 0, 0)
    s.c("Radius", al, 10.0)
    s.c("Equal", al, ar)
    if horizontal:
        s.c("Horizontal", al, 3, ar, 3)
    spec = {"arcs": [al, ar], "loops": [[(lt, 1), (lt, 2), (lb, 1), (lb, 2)]]}
    return s, ar, spec


def S03_slot_end_across_axis(doc):
    s, ar, spec = _slot(doc, "S03", True)
    return s.o, (ar, 3), [(50, 0), (-50, 0)], spec


def S04_slot_end_across_diagonal(doc):
    """Free slot (no horizontal): the right centre passes 8 mm from the left
    centre, so the slot has to swing round."""
    s, ar, spec = _slot(doc, "S04", False)
    return s.o, (ar, 3), [(50, 0), (30, 5), (8, 8), (-30, 6), (-50, 0)], spec


def S05_fourbar_crank_full_turn(doc):
    s = Sk(doc, "S05")
    g = s.line(0, 0, 60, 0)
    cr = s.line(0, 0, 10, 17)
    cp = s.line(10, 17, 55, 35)
    rk = s.line(55, 35, 60, 0)
    s.c("Coincident", g, 1, cr, 1)
    s.c("Coincident", cr, 2, cp, 1)
    s.c("Coincident", cp, 2, rk, 1)
    s.c("Coincident", rk, 2, g, 2)
    s.fix(g, 1, 0, 0)
    s.c("Horizontal", g)
    s.c("Distance", g, 60.0)
    s.c("Distance", cr, 20.0)
    s.c("Distance", cp, 50.0)
    s.c("Distance", rk, 45.0)
    # crank 20, ground 60, coupler 50, rocker 45: Grashof crank, full rotation
    path = circ(0, 0, 20, 60, 60 + 400, 100)
    return s.o, (cr, 2), path, {"loops": [[(g, 1), (g, 2), (cp, 2), (cp, 1)]]}


def S06_fourbar_rocker_dead_point(doc):
    s = Sk(doc, "S06")
    g = s.line(0, 0, 60, 0)
    cr = s.line(0, 0, 15, 26)
    cp = s.line(15, 26, 45, 29)
    rk = s.line(45, 29, 60, 0)
    s.c("Coincident", g, 1, cr, 1)
    s.c("Coincident", cr, 2, cp, 1)
    s.c("Coincident", cp, 2, rk, 1)
    s.c("Coincident", rk, 2, g, 2)
    s.fix(g, 1, 0, 0)
    s.c("Horizontal", g)
    s.c("Distance", g, 60.0)
    s.c("Distance", cr, 30.0)
    s.c("Distance", cp, 40.0)
    s.c("Distance", rk, 40.0)
    # non-Grashof (60+30 > 40+40): crank stops at a dead point; drag well past
    path = circ(0, 0, 30, 60, -180, 40)
    return s.o, (cr, 2), path, {"loops": [[(g, 1), (g, 2), (cp, 2), (cp, 1)]]}


def _crank_slider(doc, name):
    s = Sk(doc, name)
    cr = s.line(0, 0, 14, 14)
    rd = s.line(14, 14, 60, 0)
    s.c("Coincident", cr, 2, rd, 1)
    s.fix(cr, 1, 0, 0)
    s.c("Distance", cr, 20.0)
    s.c("Distance", rd, 50.0)
    s.c("PointOnObject", rd, 2, -1)
    return s, cr, rd


def S07_crankslider_crank_turn(doc):
    s, cr, rd = _crank_slider(doc, "S07")
    path = circ(0, 0, 20, 45, 45 + 400, 100)
    return s.o, (cr, 2), path, {"loops": [[(cr, 1), (cr, 2), (rd, 2)]]}


def S08_crankslider_slider_beyond_reach(doc):
    """Slider dragged along its rail from x=60 past the stroke end (70), on to
    -60 (past the other end, 30) and back."""
    s, cr, rd = _crank_slider(doc, "S08")
    return s.o, (rd, 2), [(60, 0), (75, 0), (60, 0), (20, 0), (-60, 0)], {"loops": [[(cr, 1), (cr, 2), (rd, 2)]]}


def S09_rounded_rect_side_dragged_in(doc):
    """Fully tangent rounded rectangle 100x60, r=10.  The right side's lower end
    is dragged left across the whole shape (width < 2r, then negative)."""
    s = Sk(doc, "S09")
    r = 10.0
    b = s.line(10, 0, 90, 0)
    br = s.arc(90, 10, r, -math.pi / 2, 0)
    rt = s.line(100, 10, 100, 50)
    tr = s.arc(90, 50, r, 0, math.pi / 2)
    tp = s.line(90, 60, 10, 60)
    tl = s.arc(10, 50, r, math.pi / 2, math.pi)
    lf = s.line(0, 50, 0, 10)
    bl = s.arc(10, 10, r, math.pi, 3 * math.pi / 2)
    s.c("Tangent", b, 2, br, 1)
    s.c("Tangent", br, 2, rt, 1)
    s.c("Tangent", rt, 2, tr, 1)
    s.c("Tangent", tr, 2, tp, 1)
    s.c("Tangent", tp, 2, tl, 1)
    s.c("Tangent", tl, 2, lf, 1)
    s.c("Tangent", lf, 2, bl, 1)
    s.c("Tangent", bl, 2, b, 1)
    s.c("Horizontal", b)
    s.c("Horizontal", tp)
    s.c("Vertical", rt)
    s.c("Vertical", lf)
    s.c("Radius", br, r)
    for x in (tr, tl, bl):
        s.c("Equal", br, x)
    s.fix(b, 1, 10, 0)
    path = [(100, 10), (60, 10), (20, 10), (12, 10), (0, 10), (-30, 10)]
    spec = {"arcs": [br, tr, tl, bl], "tangents": [], "loops": [[(b, 1), (rt, 1), (tp, 1), (lf, 1)]]}
    return s.o, (rt, 1), path, spec


def S10_two_circle_tangent_line_crossing(doc):
    """Line tangent to two fixed circles (outer tangent).  Its end is dragged
    straight down across the gap between the circles, so it has to hop to the
    crossing tangent or the lower outer one; the continuity-forced answer is
    'no continuous path', so jumps are expected."""
    s = Sk(doc, "S10")
    c1 = s.circle(0, 0, 20)
    c2 = s.circle(80, 0, 10)
    s.fix(c1, 3, 0, 0)
    s.fix(c2, 3, 80, 0)
    s.c("Radius", c1, 20.0)
    s.c("Radius", c2, 10.0)
    # upper outer tangent: sin a = 10/80
    a = math.asin(10.0 / 80.0)
    t1 = (20 * math.sin(a), 20 * math.cos(a))
    t2 = (80 + 10 * math.sin(a), 10 * math.cos(a))
    ln = s.line(t1[0], t1[1], t2[0], t2[1])
    s.c("Tangent", ln, c1)
    s.c("Tangent", ln, c2)
    path = [t2, (t2[0], 5), (t2[0], -5), (t2[0], -t2[1])]
    # the loop invariant for a line is moot; track tangent sides
    return s.o, (ln, 2), path, {"tangents": [(ln, c1), (ln, c2)]}


def S11_symmetric_profile_across_axis(doc):
    s = Sk(doc, "S11")
    bt = s.line(-20, 0, 20, 0)
    r1 = s.line(20, 0, 30, 30)
    r2 = s.line(30, 30, 0, 50)
    l2 = s.line(0, 50, -30, 30)
    l1 = s.line(-30, 30, -20, 0)
    s.c("Coincident", bt, 2, r1, 1)
    s.c("Coincident", r1, 2, r2, 1)
    s.c("Coincident", r2, 2, l2, 1)
    s.c("Coincident", l2, 2, l1, 1)
    s.c("Coincident", l1, 2, bt, 1)
    s.c("PointOnObject", r2, 2, -2)
    s.c("Horizontal", bt)
    s.c("Symmetric", bt, 1, bt, 2, -2)
    s.c("Symmetric", r1, 2, l1, 1, -2)
    s.fix(bt, 1, -20, 0)
    path = [(30, 30), (5, 30), (-5, 40), (-40, 30), (-60, 20)]
    return s.o, (r1, 2), path, {"loops": [[(bt, 1), (bt, 2), (r1, 2), (r2, 2), (l2, 2), (l1, 1)]]}


def _fixed_radius_arc(doc, name):
    s = Sk(doc, name)
    a = s.arc(30, 0, 30, D(90), D(180))
    s.fix(a, 2, 0, 0)  # fixed end at origin (angle 180 from centre), free start
    s.c("Radius", a, 30.0)
    return s, a


def S12_arc_radius_end_beyond_reach_and_back(doc):
    """Fixed radius 30 arc pinned at the origin.  The other end runs straight out
    to chord 85 (> 2R=60) and back; does it return to the same branch?"""
    s, a = _fixed_radius_arc(doc, "S12")
    path = [(30, 30), (45, 30), (60, 30), (85, 30)]
    return s.o, (a, 1), path, {"arcs": [a]}


def S13_arc_radius_end_rotates_round_pin(doc):
    """Same arc; the free end orbits the pinned end at distance 45 (through
    more than a full turn, so the arc must swing round every side)."""
    s, a = _fixed_radius_arc(doc, "S13")
    path = circ(0, 0, 42.4, 45, 45 + 400, 100)
    return s.o, (a, 1), path, {"arcs": [a]}


def S14_bspline_pole_dragged_across(doc):
    s = Sk(doc, "S14")
    poles = [V(0, 0, 0), V(25, 30, 0), V(50, -20, 0), V(75, 30, 0), V(100, 0, 0)]
    bs = Part.BSplineCurve()
    bs.buildFromPoles(poles, False, 3)
    g = s.o.addGeometry(bs, False)
    s.o.exposeInternalGeometry(g)
    s.fix(g, 1, 0, 0)
    s.fix(g, 2, 100, 0)
    # pole circles are geometries g+1 .. g+5 after exposure
    mid = g + 3
    path = [(50, -20), (50, 20), (50, 60), (50, 100), (20, 100), (-40, 60)]
    return s.o, (mid, 3), path, {"curves": [g]}


def S15_ellipse_arc_end_around(doc):
    s = Sk(doc, "S15")
    el = Part.Ellipse(V(0, 0, 0), 40, 20)
    ae = Part.ArcOfEllipse(el, 0.0, math.pi / 2)
    g = s.o.addGeometry(ae, False)
    s.o.exposeInternalGeometry(g)
    s.fix(g, 3, 0, 0)
    s.c("Horizontal", g, 3, g, 1) if False else None
    path = [(0, 20)] + [(40 * math.cos(D(a)), 20 * math.sin(D(a))) for a in range(90, 400, 6)]
    return s.o, (g, 2), path, {"arcs": [g], "ellipses": [g]}


def S16_ellipse_minor_exceeds_major(doc):
    """Drag the minor-axis end point of an ellipse outwards until it is longer
    than the major axis: major and minor have to swap."""
    s = Sk(doc, "S16")
    el = Part.Ellipse(V(0, 0, 0), 40, 20)
    g = s.o.addGeometry(el, False)
    s.o.exposeInternalGeometry(g)
    s.fix(g, 3, 0, 0)
    # internal geometry order: major axis line g+1, minor axis line g+2, then foci
    minor = g + 2
    path = [(0, 20), (0, 38), (0, 45), (0, 60)]
    return s.o, (minor, 2), path, {"ellipses": [g]}


def S17_rhombus_through_flat(doc):
    """Rhombus of side 40 on a fixed horizontal base; vertex C is dragged on its
    circle about B through the flat (area 0) state to the mirror side."""
    s = Sk(doc, "S17")
    ab = s.line(0, 0, 40, 0)
    bc = s.line(40, 0, 60, 34.64)
    cd = s.line(60, 34.64, 20, 34.64)
    da = s.line(20, 34.64, 0, 0)
    s.c("Coincident", ab, 2, bc, 1)
    s.c("Coincident", bc, 2, cd, 1)
    s.c("Coincident", cd, 2, da, 1)
    s.c("Coincident", da, 2, ab, 1)
    s.fix(ab, 1, 0, 0)
    s.c("Horizontal", ab)
    s.c("Distance", ab, 40.0)
    s.c("Equal", ab, bc)
    s.c("Equal", ab, cd)
    s.c("Equal", ab, da)
    path = circ(40, 0, 40, 60, -90, 60)
    return s.o, (bc, 2), path, {"loops": [[(ab, 1), (bc, 1), (cd, 1), (da, 1)]]}


def S18_fillet_corner_angle_swept(doc):
    """Two lines with a tangent fillet (r=10).  The second line's far end swings
    from 90 degrees up towards 175 (fillet fades to straight) and down to 10."""
    s = Sk(doc, "S18")
    l1 = s.line(60, 0, 10, 0)
    ar = s.arc(10, 10, 10, math.pi, 3 * math.pi / 2)
    l2 = s.line(0, 10, 0, 60)
    s.c("Tangent", l1, 2, ar, 2)
    s.c("Tangent", ar, 1, l2, 1)
    s.c("Horizontal", l1)
    s.c("Radius", ar, 10.0)
    s.fix(l1, 1, 60, 0)
    s.c("Distance", l2, 50.0)
    path = circ(0, 0, 60, 90, 177, 30) + circ(0, 0, 60, 177, 10, 40)[1:]
    return s.o, (l2, 2), path, {"arcs": [ar], "tangents": [(l1, ar)]}


def S19_tangent_circles_chain(doc):
    """Benchmark's tangent chain x10 (radius 20, 10, 5), small circle dragged
    round the middle one, then through the large one."""
    s = Sk(doc, "S19")
    c1 = s.circle(0, 0, 20)
    c2 = s.circle(30, 0, 10)
    c3 = s.circle(45, 0, 5)
    s.fix(c1, 3, 0, 0)
    s.c("Radius", c1, 20.0)
    s.c("Radius", c2, 10.0)
    s.c("Radius", c3, 5.0)
    s.c("Tangent", c1, c2)
    s.c("Tangent", c2, c3)
    path = circ(30, 0, 15, 0, 360, 25) + [(45 - 5 * i, 0.0) for i in range(0, 14)]
    return s.o, (c3, 3), path, {"tangents": [(c1, c2), (c2, c3)]}


def S20_equal_circle_orbit(doc):
    """Two equal circles, tangent; the free circle is dragged round the fixed one
    and through it (internal tangency of equal radii is a coincidence)."""
    s = Sk(doc, "S20")
    c1 = s.circle(0, 0, 15)
    c2 = s.circle(30, 0, 15)
    s.fix(c1, 3, 0, 0)
    s.c("Radius", c1, 15.0)
    s.c("Equal", c1, c2)
    s.c("Tangent", c1, c2)
    path = circ(0, 0, 30, 0, 360, 30) + [(30 - 3 * i, 0.0) for i in range(0, 12)]
    return s.o, (c2, 3), path, {"tangents": [(c1, c2)]}


SCENARIOS = [(n[:n.index("_")] + n[n.index("_"):], f) for n, f in sorted(
    (k, v) for k, v in globals().items() if k.startswith("S") and k[1:3].isdigit() and callable(v))]
