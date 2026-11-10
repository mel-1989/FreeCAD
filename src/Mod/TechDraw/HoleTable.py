# SPDX-License-Identifier: LGPL-2.1-or-later

# **************************************************************************
#   Hole table (G04 slice 3a): a tag on every hole a view sees end-on and a  *
#   stock spreadsheet view listing each tag with its X and Y from an origin  *
#   and its size, kept up to date by one recompute.                          *
#                                                                            *
#   Public sources of the conventions: vendor help pages of open and         *
#   commercial CAD tools on hole tables (tag, X and Y offset from a chosen   *
#   origin, size; behaviour only, nothing copied).  No standard is cited and *
#   no compliance is claimed.                                                *
# **************************************************************************

"""A hole table on a drawing page that follows the model.

    import HoleTable
    table = HoleTable.make(page, view)                  # DrawViewSpreadsheet in free space; tags on the view's holes
    table = HoleTable.make(page, view, origin=(0, 0, 0))  # X and Y from a model point instead of the view's lower left corner
    HoleTable.tags_of(table)                            # the DrawViewBalloon tags
    HoleTable.warning_of(table)                         # "" or what the table could not avoid overlapping

Holes are found from geometry of the view's source shapes (a STEP part works): inner cylinders and cones grouped by axis.  A hole
is listed when its axis is parallel to the view direction and a circle of one of its radii, centred on its projected axis, shows
among the view's visible edges.  A Python feature (``HoleTableDriver``) between the view and the table keeps a
``Spreadsheet::Sheet`` and one tag (a stock ``TechDraw::DrawViewBalloon``) per hole, so a plain ``doc.recompute()`` follows an
edit of the model.  A tag or table moved by the user keeps its position.
"""

import math

import FreeCAD as App
import Part
from FreeCAD import Vector

import PartsList as PL

HEADER = ("TAG", "X", "Y", "SIZE")
TAG_H_MM = 13.0  # drawn height of a tag: the text's scene box plus 0.5 mm each side, measured in the GUI (105 tags, run 089)
TAG_ANCHOR_MM = 1.15  # the tag's anchor (X, Y) lies this far above the centre of its box
TAG_LEAD_MM = 1.5  # the leader starts this far beyond the side of the box that faces the tip
TAG_PLAN_W_MM = 0.6  # extra width and height the placer keeps free around a tag beyond its measured size
TAG_PLAN_H_MM = 0.8
TAG_CLEAR_MM = 0.3  # clear space the placer keeps to every neighbour
TIP_STEPS = 24  # rim points tried for an arrow tip
TIP_TOL_MM = 0.02  # a rim point counts as on a visible arc within this
TAG_ROOM_MM = 14.0  # room kept around the view for its tags when a table is placed beside it
GAP_MM = 2.0  # clear space kept between a table and its neighbours
NEAR_MM = 8.0  # holes closer than this on the page get the larger tag distance
MAX_TAG_DIST = 20.0  # ruling 27: the tag's box centre within this of its arrow tip (the audit takes it exactly, the first fit keeps clear of it)
MAX_TAG_DIST_DENSE = 35.0  # the same where the closest holes are under NEAR_MM apart
LEAD_FRACTIONS = (1.0, 0.72)  # the drawn text is 78 to 100 % of the width tag_size plans (105 tags); the leader may start anywhere between these
REPAIR_PASSES = 100  # the repair after the first fit: at most this many passes per start ...
REPAIR_EVALS = 6000000  # ... and this many tag-pair tests
REPAIR_RESTARTS = 24  # fresh starts from the best plan, each shaken a little ...
REPAIR_SHAKE = 35  # ... this percent of the free tags put elsewhere
REPAIR_KICK = 7  # every this many passes one tag makes a move that is not the best one, to leave a dead end
REPAIR_TABU = 12  # a position a tag has left is not taken again for this many passes
REPAIR_RING = 0.75  # rings and directions of the positions the repair tries
REPAIR_DIRS = 32
RING_MM = 1.5  # step of the rings the placer tries tag centres on
RING_DIRS = 16
ROUND_MM = 0.001  # inputs of the layout are rounded to this, so a GUI run and a headless run choose the same slots
IDENTITY_MM = 0.5  # a tag stays tied to a hole that moved less than this
MAX_BLOCKS = 10
PLACED_X, PLACED_Y = "PlacedX", "PlacedY"


# ------------------------------------------------------------------------------------------------
# numbers
# ------------------------------------------------------------------------------------------------


def fmt(x):
    """round(x, 6), then at most two decimals, trailing zeros and point stripped, -0 written 0."""
    s = "%.2f" % round(x, 6)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def tag_letters(i):
    """0 -> A, 25 -> Z, 26 -> AA ..."""
    s = ""
    i += 1
    while i > 0:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


# ------------------------------------------------------------------------------------------------
# holes from geometry
# ------------------------------------------------------------------------------------------------


def _faces(solid):
    for f in solid.Faces:
        s = f.Surface
        tid = s.TypeId
        if tid not in ("Part::GeomCylinder", "Part::GeomCone"):
            continue
        try:
            u0, u1, v0, v1 = f.ParameterRange
            u, v = 0.5 * (u0 + u1), 0.5 * (v0 + v1)
            n, pt = f.normalAt(u, v), f.valueAt(u, v)
        except Exception:
            continue
        ax = Vector(s.Axis)
        ax.normalize()
        c = Vector(s.Center)
        rad = pt - c
        rad = rad - ax * rad.dot(ax)
        if n.dot(rad) >= 0:  # faces outward: a boss or shaft
            continue
        yield f, s, tid, ax, c


def find_holes(solid, d):
    """Holes of one solid whose axis is oriented toward ``d`` (the viewer): a list of dicts.

    Keys: ``c0`` (point on the axis), ``a`` (unit axis, a . d >= 0), ``T0``/``T1`` (extent along a; the mouth is T1),
    ``segs`` (cylinders and cones, each ``kind, t0, t1, r`` / ``ra, rb``); only holes parallel to ``d`` are returned.  ``open_ends``
    adds ``top_open`` and ``through``.
    """
    raw = []
    for f, s, tid, ax, c in _faces(solid):
        raw.append((f, tid, ax, c, s))
    groups = []
    for item in raw:
        ax, c = item[2], item[3]
        for g in groups:
            a0, c0 = g[0][2], g[0][3]
            dd = c - c0
            dd = dd - a0 * dd.dot(a0)
            if abs(abs(a0.dot(ax)) - 1) < 1e-6 and dd.Length < 1e-4:
                g.append(item)
                break
        else:
            groups.append([item])
    holes = []
    for g in groups:
        a, c0 = Vector(g[0][2]), Vector(g[0][3])
        if a.dot(d) < 0:
            a = -a
        merged = {}
        for f, tid, ax, c, s in g:
            pts = []
            for v in f.Vertexes:
                dv = v.Point - c0
                t = dv.dot(a)
                pts.append((t, (dv - a * t).Length))
            if not pts:
                continue
            t0, t1 = min(p[0] for p in pts), max(p[0] for p in pts)
            if tid == "Part::GeomCylinder":
                r = float(s.Radius)
                h = t1 - t0
                seg = dict(kind="cyl", r=r, t0=t0, t1=t1, cover=(f.Area / (2 * math.pi * r * h)) if h > 1e-9 else 0.0)
                key = ("c", round(r, 4), round(t0, 3), round(t1, 3))
            else:
                ra = min(pts, key=lambda p: p[0])[1]
                rb = max(pts, key=lambda p: p[0])[1]
                slant = math.hypot(ra - rb, t1 - t0)
                full = math.pi * (ra + rb) * slant
                seg = dict(kind="cone", ra=ra, rb=rb, t0=t0, t1=t1, cover=(f.Area / full) if full > 1e-9 else 0.0)
                key = ("k", round(ra, 4), round(rb, 4), round(t0, 3), round(t1, 3))
            if key in merged:
                merged[key]["cover"] += seg["cover"]
            else:
                merged[key] = seg
        segs = sorted((s for s in merged.values() if s["cover"] > 0.95), key=lambda s: (s["t0"], s["t1"]))
        run = []

        def flush(run):
            if not any(x["kind"] == "cyl" for x in run):
                return
            T0, T1 = min(x["t0"] for x in run), max(x["t1"] for x in run)
            if a.cross(d).Length > 1e-6:  # not parallel to the view direction: never listed
                return
            holes.append(dict(c0=c0, a=a, T0=T0, T1=T1, segs=list(run), solid=solid))

        reach = None
        for s in segs:
            if run and s["t0"] - reach > 1e-3:
                flush(run)
                run, reach = [], None
            run.append(s)
            reach = s["t1"] if reach is None else max(reach, s["t1"])
        if run:
            flush(run)
    return holes


def open_ends(h):
    """Fill in ``top_open`` (the mouth faces the viewer) and ``through`` by probing the solid just beyond each end of the axis."""
    ends = []
    for T, sg in ((h["T1"], 1), (h["T0"], -1)):
        try:
            inside = h["solid"].isInside(h["c0"] + h["a"] * (T + sg * 0.01), 1e-6, True)
        except Exception:
            inside = True
        ends.append(not inside)
    h["top_open"], h["through"] = ends[0], ends[0] and ends[1]


def size_text(h, thread=None):
    """The size words of a hole (ruling 25): base clause, then one clause per other segment from the mouth inward."""
    segs = h["segs"]
    cyls = [s for s in segs if s["kind"] == "cyl"]
    rmin = min(round(s["r"], 6) for s in cyls)
    bases = [s for s in cyls if abs(s["r"] - rmin) < 1e-6]
    base = min(bases, key=lambda s: s["t0"])
    T1 = h["T1"]
    first = thread if thread else "Ø" + fmt(2 * base["r"])
    out = [first + (" THRU" if h["through"] else " DEPTH " + fmt(T1 - base["t0"]))]
    for s in sorted(segs, key=lambda s: (-s["t1"], -s["t0"])):
        if s is base:
            continue
        if s["kind"] == "cyl":
            if abs(s["r"] - rmin) < 1e-6:
                continue
            if s["t0"] >= base["t1"] - 1e-6:
                out.append("CBORE Ø%s DEPTH %s" % (fmt(2 * s["r"]), fmt(T1 - s["t0"])))
            elif s["t1"] <= base["t0"] + 1e-6:
                out.append("BACK Ø%s DEPTH %s" % (fmt(2 * s["r"]), fmt(s["t1"] - s["t0"])))
        elif abs(s["t1"] - T1) < 1e-3 and s["rb"] > s["ra"]:
            ang = 2.0 * math.degrees(math.atan2(s["rb"] - s["ra"], s["t1"] - s["t0"]))
            out.append("CSK Ø%s X %d°" % (fmt(2 * max(s["ra"], s["rb"])), int(math.floor(ang + 0.5))))
    return ", ".join(out)


def mouth_radius(h):
    r = 0.0
    for s in h["segs"]:
        if abs(s["t1"] - h["T1"]) < 1e-3:
            r = max(r, s["r"] if s["kind"] == "cyl" else max(s["ra"], s["rb"]))
    return r


def _radii(h):
    out = set()
    for s in h["segs"]:
        if s["kind"] == "cyl":
            out.add(round(s["r"], 6))
        else:
            out.update(round(x, 6) for x in (s["ra"], s["rb"]) if x > 1e-6)
    return sorted(out)


# ------------------------------------------------------------------------------------------------
# the view's edges
# ------------------------------------------------------------------------------------------------


_GC = {}


def _plain_centre(view):
    """The centre TechDraw centred the shape on when it projected it: the middle of the source shapes' bounding box in the view's frame.

    ``view.getGeometricCenter()`` measures the shape again, and once the 3D view has meshed it the box includes the mesh deflection
    (7 micrometres off on a plate with a tilted hole in the GUI), so a hole centre projected with it misses its rim circle.  The same
    box is taken here on a copy that carries no mesh, whether the GUI is up or not.
    """
    z = Vector(view.Direction)
    z.normalize()
    x = Vector(view.XDirection)
    x = x - z * x.dot(z)
    x.normalize()
    y = z.cross(x)
    m = App.Matrix()
    m.A11, m.A12, m.A13 = x.x, x.y, x.z
    m.A21, m.A22, m.A23 = y.x, y.y, y.z
    m.A31, m.A32, m.A33 = z.x, z.y, z.z
    lo = hi = None
    for sh in source_shapes(view):
        c = sh.copy()
        c.transformShape(m)
        bb = c.optimalBoundingBox(True, False)
        a, b = (bb.XMin, bb.YMin, bb.ZMin), (bb.XMax, bb.YMax, bb.ZMax)
        lo = a if lo is None else tuple(min(i, j) for i, j in zip(lo, a))
        hi = b if hi is None else tuple(max(i, j) for i, j in zip(hi, b))
    if lo is None:
        return view.getGeometricCenter()
    cx, cy, cz = [0.5 * (i + j) for i, j in zip(lo, hi)]
    return x * cx + y * cy + z * cz


def gc_of(view):
    """The centre the view was centred on (see ``_plain_centre``), a quarter of a second on a big part, cached on what it depends on."""
    try:
        key = (view.Document.Uid, view.Name, tuple(view.Direction), tuple(view.XDirection), tuple((o.Shape.hashCode(), str(o.Placement)) for o in view.Source))
    except Exception:
        return view.getGeometricCenter()
    hit = _GC.get(key)
    if hit is None:
        if len(_GC) > 64:
            _GC.clear()
        try:
            hit = _plain_centre(view)
        except Exception:
            hit = view.getGeometricCenter()
        _GC[key] = hit
    return hit


def _edge_factor(view, box):
    """How much ``getVisibleEdges(True)`` is multiplied by: 1 or the view's Scale.

    With ScaleType Page the edges come back unscaled, with ScaleType Custom they come back times Scale while ``projectPoint`` stays
    unscaled (run 088).  Compare the edges' extent with the projected extent of the sources' bounding boxes.
    """
    S = float(view.Scale)
    if abs(S - 1.0) < 1e-9 or box is None:
        return 1.0
    try:
        gc = gc_of(view)
        pts = []
        for sh in source_shapes(view):
            bb = sh.BoundBox
            for x in (bb.XMin, bb.XMax):
                for y in (bb.YMin, bb.YMax):
                    for z in (bb.ZMin, bb.ZMax):
                        q = view.projectPoint(Vector(x, y, z) - gc)
                        pts.append((q.x, q.y))
        base = math.hypot(max(p[0] for p in pts) - min(p[0] for p in pts), max(p[1] for p in pts) - min(p[1] for p in pts))
        ext = math.hypot(box[2] - box[0], box[3] - box[1])
        r = ext / base
        return S if abs(math.log(r / S)) < abs(math.log(r)) else 1.0
    except Exception:
        return 1.0


class Edges:
    """The visible edges of a view in its own frame (y up, unscaled model mm, whatever the view's ScaleType), circles indexed.

    ``k`` is the factor the view applied to the edges; ``box``, ``circles`` and ``rim`` are in unscaled units, ``e`` stays raw.
    """

    def __init__(self, view):
        try:
            self.edges = list(view.getVisibleEdges(True))
        except Exception:
            self.edges = []
        self.box = None
        raw = None
        for e in self.edges:
            bb = e.BoundBox
            if raw is None:
                raw = [bb.XMin, bb.YMin, bb.XMax, bb.YMax]
            else:
                raw = [min(raw[0], bb.XMin), min(raw[1], bb.YMin), max(raw[2], bb.XMax), max(raw[3], bb.YMax)]
        self.k = _edge_factor(view, raw)
        k = self.k
        self.box = [v / k for v in raw] if raw else None
        self.drawn_box = self.box
        self.box0 = self.box
        rot = _rotation(view)
        if raw and rot:
            # the lower left corner X and Y are measured from is the view's unrotated one (ruling 34): turn the edges back
            lo = hi = None
            for e in self.edges:
                c = e.copy()
                c.rotate(Vector(0, 0, 0), Vector(0, 0, 1), -rot)
                bb = c.BoundBox
                lo = [bb.XMin, bb.YMin] if lo is None else [min(lo[0], bb.XMin), min(lo[1], bb.YMin)]
                hi = [bb.XMax, bb.YMax] if hi is None else [max(hi[0], bb.XMax), max(hi[1], bb.YMax)]
            self.box0 = [lo[0] / k, lo[1] / k, hi[0] / k, hi[1] / k]
        self.circles = {}
        for e in self.edges:
            try:
                c = e.Curve
                r = None
                if c.TypeId == "Part::GeomCircle":
                    r = c.Radius
                elif c.TypeId == "Part::GeomEllipse" and c.MajorRadius - c.MinorRadius < 1e-4 * c.MajorRadius:
                    # a hole a hair off the view direction (within ruling 23's 1e-6) comes back as an ellipse that is a circle to the eye
                    r = 0.5 * (c.MajorRadius + c.MinorRadius)
                if r is not None:
                    r /= k
                    self.circles.setdefault(round(r, 2), []).append((c.Center.x / k, c.Center.y / k, r, e.Length / k, e))
            except Exception:
                pass

    def signature(self):
        """What the edges are, down to where each one lies: a hole that moved changes it though the edge count and lengths do not."""
        tot = 0
        for e in self.edges:
            bb = e.BoundBox
            tot += hash((round(bb.XMin, 3), round(bb.YMin, 3), round(bb.XMax, 3), round(bb.YMax, 3), round(e.Length, 3)))
        return "%d:%d" % (len(self.edges), tot % (1 << 61))

    def rim(self, cx, cy, radius):
        """-> (cover fraction of the circumference, [edges]) of visible arcs of that circle."""
        tot, es = 0.0, []
        for k in (round(radius, 2) - 0.01, round(radius, 2), round(radius, 2) + 0.01):
            for x, y, r, ln, e in self.circles.get(round(k, 2), ()):
                if abs(x - cx) < 1e-3 and abs(y - cy) < 1e-3 and abs(r - radius) < 1e-3:
                    tot += ln
                    es.append(e)
        return tot / (2 * math.pi * radius), es


def _on_arc(e, pt, tol_mm, k=1.0):
    """True when the point (unscaled, on the circle) lies on the arc ``e`` (whose coordinates are ``k`` times larger)."""
    c = e.Curve
    r = c.Radius if c.TypeId == "Part::GeomCircle" else c.MajorRadius
    pt = (pt[0] * k, pt[1] * k)
    tol_mm = tol_mm * k
    try:
        u = c.parameter(Vector(pt[0], pt[1], 0))
    except Exception:
        return False
    lo, hi = e.FirstParameter, e.LastParameter
    tol = tol_mm / r
    for k in (-2 * math.pi, 0.0, 2 * math.pi):
        if lo - tol <= u + k <= hi + tol:
            return True
    return False


# ------------------------------------------------------------------------------------------------
# thread notes from PartDesign::Hole features
# ------------------------------------------------------------------------------------------------


def _threads(doc):
    """-> [(removed solid, ThreadSize)] for threaded Hole features and the patterns of them."""
    out = []
    for o in doc.Objects:
        try:
            if o.TypeId == "PartDesign::Hole" and o.Threaded and o.BaseFeature is not None:
                out.append((o.BaseFeature.Shape.cut(o.Shape), str(o.ThreadSize)))
        except Exception:
            continue
    for o in doc.Objects:
        try:
            if o.TypeId.startswith("PartDesign::") and getattr(o, "Originals", None) and o.BaseFeature is not None:
                for orig in o.Originals:
                    if orig.TypeId == "PartDesign::Hole" and orig.Threaded:
                        out.append((o.BaseFeature.Shape.cut(o.Shape), str(orig.ThreadSize)))
                        break
        except Exception:
            continue
    return out


def _thread_of(h, threads):
    base = min((s for s in h["segs"] if s["kind"] == "cyl"), key=lambda s: s["r"])
    P = h["c0"] + h["a"] * (0.5 * (base["t0"] + base["t1"]))
    for removed, size in threads:
        try:
            if removed.isInside(P, 1e-6, True):
                return size
        except Exception:
            continue
    return None


# ------------------------------------------------------------------------------------------------
# geometry of a view on the page
# ------------------------------------------------------------------------------------------------


def _rotation(view):
    rot = view.Rotation
    return float(rot.Value) if hasattr(rot, "Value") else float(rot)


def _rot(p, deg):
    if not deg:
        return p
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return (p[0] * c - p[1] * s, p[0] * s + p[1] * c)


def source_shapes(view):
    out = []
    for o in list(view.Source) + list(getattr(view, "XSource", []) or []):
        try:
            s = Part.getShape(o)
        except Exception:
            s = getattr(o, "Shape", None)
        if s is not None and not s.isNull():
            out.append(s)
    return out


def listed_holes(view, edges, origin=None):
    """-> list of dicts (pos, size, bore, tip candidates ...) for the holes ``view`` shows end-on, unsorted and untagged."""
    d = Vector(view.Direction)
    d.normalize()
    gc = gc_of(view)
    rot = _rotation(view)
    threads = _threads(view.Document)
    found = []
    for shape in source_shapes(view):
        for solid in shape.Solids:
            for h in find_holes(solid, d):
                pc = view.projectPoint(h["c0"] - gc)
                pcr = _rot((pc.x, pc.y), rot)
                seen = False
                for r in _radii(h):
                    cover, _ = edges.rim(pcr[0], pcr[1], r)
                    if cover >= 0.5 - 1e-9:
                        seen = True
                        break
                if not seen:
                    continue
                open_ends(h)
                if not h["top_open"]:
                    continue
                h["gc"] = gc
                h["p"] = (pc.x, pc.y)
                h["pr"] = pcr
                h["thread"] = _thread_of(h, threads) if threads else None
                h["size"] = size_text(h, h["thread"])
                h["bore"] = 2 * min(s["r"] for s in h["segs"] if s["kind"] == "cyl")
                found.append(h)
    return found


def tip_options(view, h, edges):
    """-> [(x, y)] points of the hole's outermost rim at the mouth, in the view's frame, that lie on a visible arc."""
    gc = h["gc"]
    rot = _rotation(view)
    R = mouth_radius(h)
    a = h["a"]
    u = a.cross(Vector(1, 0, 0))
    if u.Length < 0.1:
        u = a.cross(Vector(0, 1, 0))
    u.normalize()
    w = a.cross(u)
    centre = h["c0"] + a * h["T1"]
    pcr = h["pr"]
    _, arcs = edges.rim(pcr[0], pcr[1], R)
    pts, valid = [], []
    for i in range(TIP_STEPS):
        th = 2 * math.pi * i / TIP_STEPS
        P = centre + u * (R * math.cos(th)) + w * (R * math.sin(th))
        q = view.projectPoint(P - gc)
        q = _rot((q.x, q.y), rot)
        pts.append(q)
        if any(_on_arc(e, q, TIP_TOL_MM, edges.k) for e in arcs):
            valid.append(q)
    return valid or pts, R


# ------------------------------------------------------------------------------------------------
# rectangles on the page
# ------------------------------------------------------------------------------------------------


def overlap(a, b, gap=0.0):
    return a[0] < b[2] + gap and b[0] < a[2] + gap and a[1] < b[3] + gap and b[1] < a[3] + gap


def _seg_cross(p1, p2, p3, p4):
    def o(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    d1, d2, d3, d4 = o(p3, p4, p1), o(p3, p4, p2), o(p1, p2, p3), o(p1, p2, p4)
    return d1 * d2 < 0 and d3 * d4 < 0


def _seg_hits_box(p, q, b):
    """True when the open segment p-q passes through the box interior (Liang-Barsky)."""
    t0, t1 = 0.0, 1.0
    dx, dy = q[0] - p[0], q[1] - p[1]
    for pp, qq in ((-dx, p[0] - b[0]), (dx, b[2] - p[0]), (-dy, p[1] - b[1]), (dy, b[3] - p[1])):
        if pp == 0:
            if qq < 0:
                return False
        else:
            t = qq / pp
            if pp < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
    return t0 + 1e-9 < t1 - 1e-9


def r3(x):
    """Round a layout input to 0.001 mm (the one place both the GUI and the headless run take their numbers through)."""
    return round(float(x) / ROUND_MM) * ROUND_MM


def tag_size(text):
    """-> (width, height) in page mm of the box a tag with this text draws, from the text alone.

    Measured in the GUI (runs 088 and 089, 105 tags): the box is the text plus 1.0 mm and 13.0 mm tall; a digit is up to 3.4 mm wide
    and a capital up to 5.0 mm (6.4 mm for M and W).  These are upper bounds, so a tag is never larger than planned.  No font is read.
    """
    return 1.0 + sum(3.4 if not c.isalpha() else 6.4 if c in "MW" else 5.0 for c in text), TAG_H_MM


def tag_box(sv, t):
    """Box (x0, y0, x1, y1) of the tag object ``t`` of view ``sv`` on the page, from its anchor and text."""
    w, h = tag_size(t.Text)
    S = float(sv.Scale)
    cx = sv.X.Value + _num(t.X) * S
    cy = sv.Y.Value + _num(t.Y) * S - TAG_ANCHOR_MM
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def view_box(view, edges=None):
    """Visible-edge box of a view on the page: (x0, y0, x1, y1) or None."""
    edges = edges or Edges(view)
    if edges.box is None:
        return None
    S = float(view.Scale)
    vx, vy = view.X.Value, view.Y.Value
    return (vx + edges.box[0] * S, vy + edges.box[1] * S, vx + edges.box[2] * S, vy + edges.box[3] * S)


def sheet_view_box(v):
    """Drawn box of a DrawViewSpreadsheet from its symbol (page mm), or None."""
    import re

    sym = v.Symbol
    if not sym:
        return None
    try:
        w = max(float(x) + float(wd) for x, wd in re.findall(r'<rect x="([-\d.]+)" y="[-\d.]+" width="([-\d.]+)"', sym))
        h = max(float(y) + float(ht) for y, ht in re.findall(r'<rect x="[-\d.]+" y="([-\d.]+)" width="[-\d.]+" height="([-\d.]+)"', sym))
    except ValueError:
        return None
    w, h = w * PL.PX_MM, h * PL.PX_MM
    x, y = v.X.Value, v.Y.Value
    return (x - w / 2, y - h / 2, x + w / 2, y + h / 2)


def obstacles(page, skip=()):
    """-> [(name, box)] of everything a new table or tag must stay clear of.  ``skip`` are object names left out."""
    area, title = PL.page_areas(page)
    out = []
    if title:
        out.append(("the title block", tuple(title)))
    tags_by_view = {}
    for v in page.Views:
        if v.Name in skip:
            continue
        if v.TypeId == "TechDraw::DrawViewBalloon":
            sv = v.SourceView
            if sv is not None:
                tags_by_view.setdefault(sv.Name, []).append(tag_box(sv, v))
    for v in page.Views:
        if v.Name in skip:
            continue
        if v.TypeId == "TechDraw::DrawViewSpreadsheet":
            b = sheet_view_box(v)
            if b:
                out.append((v.Label, b))
        elif hasattr(v, "getVisibleEdges"):
            b = view_box(v)
            if b:
                for t in tags_by_view.get(v.Name, ()):
                    b = (min(b[0], t[0]), min(b[1], t[1]), max(b[2], t[2]), max(b[3], t[3]))
                out.append((v.Label, b))
    return out


def _angle_gap(a, b):
    return abs(((a - b + math.pi) % (2 * math.pi)) - math.pi)


# attempts of the placer, widest margins first: (extra width, extra height, clear space, ring step, directions)
ATTEMPTS = (
    (TAG_PLAN_W_MM, TAG_PLAN_H_MM, TAG_CLEAR_MM, RING_MM, RING_DIRS),
    (0.2, 0.3, 0.1, 1.0, 24),
    (0.0, 0.0, 0.02, 0.75, 32),
)


def tag_tails(text, centre, tip):
    """-> the points a tag's leader may start from, for a tag whose box is centred at ``centre`` with its arrow at ``tip``.

    TechDraw draws the leader of a ``DrawViewBalloon`` with ``BubbleShape`` None and ``KinkLength`` 0 as one straight segment from
    ``(anchor x +- (text width / 2 + 2 mm), anchor y)`` to the arrow tip (``QGIViewBalloon::drawBalloon``: no bubble gives
    ``offsetLR = textWidth / 2 + 2`` and no kink gives ``kinkPoint = dLineStart``), the side being the one that faces the tip, and the
    anchor lying ``TAG_ANCHOR_MM`` above the box centre.  The text width is not known without a font, only bounded: ``tag_size`` is
    its upper bound and the drawn width was 78 to 100 % of that, so the start is given at the two ends of that range and the
    leader is a fan, the triangle of the tip and those two starts.  Two leaders count as crossing when their fans overlap, because then
    some drawn width puts one segment across the other (three sample widths missed a crossing that was 0.01 mm deep).
    """
    w = tag_size(text)[0]
    side = 1.0 if tip[0] > centre[0] else -1.0
    return [(centre[0] + side * (w * f / 2 + TAG_LEAD_MM), centre[1] + TAG_ANCHOR_MM) for f in LEAD_FRACTIONS]


def _bb(tails, tip):
    xs = [t[0] for t in tails] + [tip[0]]
    ys = [t[1] for t in tails] + [tip[1]]
    return (min(xs), min(ys), max(xs), max(ys))


def _tri_overlap(a, b):
    """True when two triangles share area (touching at a point or an edge does not count): separating-axis test."""
    for poly in (a, b):
        for k in range(3):
            p, q = poly[k], poly[(k + 1) % 3]
            nx, ny = q[1] - p[1], p[0] - q[0]
            ln = math.hypot(nx, ny)
            if ln < 1e-12:
                continue
            nx, ny = nx / ln, ny / ln
            pa = [nx * v[0] + ny * v[1] for v in a]
            pb = [nx * v[0] + ny * v[1] for v in b]
            if max(pa) <= min(pb) + 0.002 or max(pb) <= min(pa) + 0.002:
                return False
    return True


def _legs_cross(ta, tipa, tb, tipb, bba=None, bbb=None):
    """True when the leaders, each a fan from the tip to the possible starts ``ta`` / ``tb``, can cross at some drawn text width."""
    if bba is not None and (bba[0] > bbb[2] or bbb[0] > bba[2] or bba[1] > bbb[3] or bbb[1] > bba[3]):
        return False
    return _tri_overlap((ta[0], ta[-1], tipa), (tb[0], tb[-1], tipb))


def _pen(a, b):
    return min(a[2], b[2]) - max(a[0], b[0]) > 0.05 and min(a[3], b[3]) - max(a[1], b[1]) > 0.05


class _Plan:
    """A tag plan with the geometry that the audit and the repair share: a box and the leader starts of every tag.

    The faults are named with the phrases the driver's warning uses: ``A1 outside the drawing area``, ``A1 over <obstacle or tag>``,
    ``A1 covers a hole centre``, ``A1 more than 20 mm from its hole`` (the bound exact: box centre to arrow tip) and ``leaders of A1 and
    A2 cross``.
    """

    def __init__(self, items, plan, area, obs, limit):
        self.items, self.area, self.obs, self.limit = items, area, obs, limit
        self.cs = [it["c"] for it in items]
        self.plan = [None] * len(items)
        self.rects = [None] * len(items)
        self.tails = [None] * len(items)
        self.bbs = [None] * len(items)
        for i, p in enumerate(plan):
            self.put(i, p[0], p[1])

    def rect(self, i, centre):
        w, h = tag_size(self.items[i]["tag"])
        return (centre[0] - w / 2, centre[1] - h / 2, centre[0] + w / 2, centre[1] + h / 2)

    def put(self, i, centre, tip):
        self.plan[i] = (centre, tip)
        self.rects[i] = self.rect(i, centre)
        self.tails[i] = tag_tails(self.items[i]["tag"], centre, tip)
        self.bbs[i] = _bb(self.tails[i], tip)

    def solo(self, i, centre=None, tip=None):
        """Words for the faults tag ``i`` has by itself (area, obstacles, hole centres, distance)."""
        t = self.items[i]["tag"]
        centre, tip = (centre, tip) if centre is not None else self.plan[i]
        r = self.rect(i, centre)
        a = self.area
        out = []
        if r[0] < a[0] - 0.01 or r[1] < a[1] - 0.01 or r[2] > a[2] + 0.01 or r[3] > a[3] + 0.01:
            out.append("%s outside the drawing area" % t)
        for n, b in self.obs:
            if _pen(r, b):
                out.append("%s over %s" % (t, n))
        if any(r[0] < c[0] < r[2] and r[1] < c[1] < r[3] for c in self.cs):
            out.append("%s covers a hole centre" % t)
        if math.hypot(centre[0] - tip[0], centre[1] - tip[1]) > self.limit - 0.001:
            out.append("%s more than %g mm from its hole" % (t, self.limit))
        return out

    def pair(self, i, j):
        """Words for the faults between tags ``i`` and ``j``."""
        out = []
        if _pen(self.rects[i], self.rects[j]):
            out.append("%s over %s" % (self.items[i]["tag"], self.items[j]["tag"]))
        if _legs_cross(self.tails[i], self.plan[i][1], self.tails[j], self.plan[j][1], self.bbs[i], self.bbs[j]):
            out.append("leaders of %s and %s cross" % (self.items[i]["tag"], self.items[j]["tag"]))
        return out

    def audit(self):
        """-> (faults as words, indices of the tags they involve), each pair once, in tag order."""
        words, bad = [], set()
        n = len(self.items)
        for i in range(n):
            s = self.solo(i)
            if s:
                bad.add(i)
            words.extend(s)
            for j in range(i + 1, n):
                p = self.pair(i, j)
                if p:
                    bad.update((i, j))
                words.extend(p)
        return words, bad

    def count(self, i, centre, tip, cap=1 << 30, own=True):
        """How many faults tag ``i`` would have at ``centre`` / ``tip`` (alone and against the other tags); stops counting at ``cap``.

        ``own=False`` leaves out the faults of the tag by itself (area, distance, hole centres, obstacles) for a position already known clear of them.
        """
        r = self.rect(i, centre)
        n = 0
        if own:
            a = self.area
            if r[0] < a[0] - 0.01 or r[1] < a[1] - 0.01 or r[2] > a[2] + 0.01 or r[3] > a[3] + 0.01:
                n += 1
            if math.hypot(centre[0] - tip[0], centre[1] - tip[1]) > self.limit - 0.001:
                n += 1
            if any(r[0] < c[0] < r[2] and r[1] < c[1] < r[3] for c in self.cs):
                n += 1
            for _, b in self.obs:
                if _pen(r, b):
                    n += 1
            if n >= cap:
                return n
        tl = tag_tails(self.items[i]["tag"], centre, tip)
        bb = _bb(tl, tip)
        r0, r1, r2, r3_ = r[0] + 0.05, r[1] + 0.05, r[2] - 0.05, r[3] - 0.05
        rects, plan, tails, bbs = self.rects, self.plan, self.tails, self.bbs
        for j in range(len(rects)):
            if j == i:
                continue
            q = rects[j]
            if q[0] < r2 and r0 < q[2] and q[1] < r3_ and r1 < q[3]:
                n += 1
            b2 = bbs[j]
            if not (bb[0] > b2[2] or b2[0] > bb[2] or bb[1] > b2[3] or b2[1] > bb[3]) and _tri_overlap((tl[0], tl[-1], tip), (tails[j][0], tails[j][-1], plan[j][1])):
                n += 1
            if n >= cap:
                return n
        return n

    def count_of(self, i):
        return self.count(i, self.plan[i][0], self.plan[i][1])


def _best_tip(it, centre):
    hc = it["c"]
    want = math.atan2(centre[1] - hc[1], centre[0] - hc[0])
    return min(it["tips"], key=lambda q: (_angle_gap(math.atan2(q[1] - hc[1], q[0] - hc[0]), want), q))


def _plan_once(items, area, obs, view_centre, limit, prm, front, soft=None):
    """One pass of the first fit.  -> (plan, tiers): per hole its (box centre, tip) and the tier it was placed at (0 = clean)."""
    dw, dh, mg, ring, ndir = prm
    n = len(items)
    cs = [it["c"] for it in items]
    plan, tiers = [None] * n, [0] * n
    boxes, legs = [], []
    hard = [(nm, b) for nm, b in obs if soft is not None and nm not in soft]  # tables and the title block: no tier overlaps these
    softobs = [(nm, b) for nm, b in obs if not (soft is not None and nm not in soft)]

    def geom(it, centre, tip):
        w, h = tag_size(it["tag"])
        cx, cy = centre
        return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), tag_tails(it["tag"], centre, tip)

    free = []
    for i, it in enumerate(items):
        if "fixed" in it:
            tip = _best_tip(it, it["fixed"])
            box, tails = geom(it, it["fixed"], tip)
            boxes.append(box)
            legs.append((tails, tip, _bb(tails, tip)))
            plan[i] = (it["fixed"], tip)
        else:
            free.append(i)
    crowd = {i: sum(1 for k, c in enumerate(cs) if k != i and math.hypot(cs[i][0] - c[0], cs[i][1] - c[1]) < 12.0) for i in free}
    free.sort(key=lambda i: (0 if i in front else 1, -crowd[i], i))
    vcx, vcy = view_centre
    for i in free:
        it = items[i]
        hc, R = it["c"], it["R"]
        w, h = tag_size(it["tag"])
        pw, ph = w + dw, h + dh
        outward = math.atan2(hc[1] - vcy, hc[0] - vcx)
        cands = []
        for k in range(int((limit + R + 12.0) / ring) + 2):
            rho = 2.0 + k * ring
            for j in range(ndir):
                ang = outward + ((j + 1) // 2) * (2 * math.pi / ndir) * (1 if j % 2 else -1)
                cands.append((rho + 1.5 * _angle_gap(ang, outward), rho, ang))
        cands.sort()
        done, tier_done = None, 0
        for tier in (0, 1, 2):
            for _, rho, ang in cands:
                cx, cy = r3(hc[0] + rho * math.cos(ang)), r3(hc[1] + rho * math.sin(ang))
                box = (cx - pw / 2, cy - ph / 2, cx + pw / 2, cy + ph / 2)
                if box[0] < area[0] + 0.05 or box[1] < area[1] + 0.05 or box[2] > area[2] - 0.05 or box[3] > area[3] - 0.05:
                    continue  # never outside the drawing area, at any tier
                m = mg if tier == 0 else 0.0
                if any(box[0] - m < c[0] < box[2] + m and box[1] - m < c[1] < box[3] + m for c in cs):
                    continue
                if any(overlap(box, b, m) for b in boxes):
                    continue
                if any(overlap(box, b, m) for _, b in hard):
                    continue
                if tier < 2 and any(overlap(box, b, m) for _, b in softobs):
                    continue
                tip = _best_tip(it, (cx, cy))
                if tier == 0 and math.hypot(cx - tip[0], cy - tip[1]) > limit - mg:
                    continue
                if tier == 1 and math.hypot(cx - tip[0], cy - tip[1]) > limit + 12.0:
                    continue
                tails = tag_tails(it["tag"], (cx, cy), tip)
                if tier == 0:
                    bb = _bb(tails, tip)
                    if any(_legs_cross(tails, tip, lt, lp, bb, lb) for lt, lp, lb in legs):
                        continue
                    if any(_seg_hits_box(tails[0], tip, (b[0] - 0.2, b[1] - 0.2, b[2] + 0.2, b[3] + 0.2)) for b in boxes):
                        continue
                    if any(_seg_hits_box(lt[0], lp, (box[0] - 0.2, box[1] - 0.2, box[2] + 0.2, box[3] + 0.2)) for lt, lp, _ in legs):
                        continue
                done, tier_done = ((cx, cy), tip, box, tails), tier
                break
            if done:
                break
        if done is None:  # not even a free spot inside the area: the first place in the area that covers no hole centre and no table
            tier_done = 3
            for strict in (True, False):
                for _, rho, ang in cands:
                    cx, cy = r3(hc[0] + rho * math.cos(ang)), r3(hc[1] + rho * math.sin(ang))
                    box = (cx - pw / 2, cy - ph / 2, cx + pw / 2, cy + ph / 2)
                    if box[0] >= area[0] + 0.05 and box[1] >= area[1] + 0.05 and box[2] <= area[2] - 0.05 and box[3] <= area[3] - 0.05:
                        if strict and any(overlap(box, b) for _, b in hard):
                            continue
                        tip = _best_tip(it, (cx, cy))
                        done = ((cx, cy), tip, box, tag_tails(it["tag"], (cx, cy), tip))
                        break
                if done:
                    break
        if done is None:  # the area cannot hold the tag at all (a tiny page)
            centre = (min(max(hc[0], area[0] + pw / 2), area[2] - pw / 2), min(max(hc[1], area[1] + ph / 2), area[3] - ph / 2))
            tip = _best_tip(it, centre)
            done = (centre, tip, (centre[0] - pw / 2, centre[1] - ph / 2, centre[0] + pw / 2, centre[1] + ph / 2), tag_tails(it["tag"], centre, tip))
        centre, tip, box, tails = done
        boxes.append(box)
        legs.append((tails, tip, _bb(tails, tip)))
        plan[i] = (centre, tip)
        tiers[i] = tier_done
    return plan, tiers


def tag_conflicts(items, plan, area, obs, bound):
    """-> list of words naming what a finished tag plan still gets wrong (the checks of ruling 27 and 28), empty when clean.

    The audit works on the drawn geometry (see ``tag_tails``) and on ruling 27's bound as it stands, 20 or 35 mm, with no slack.
    """
    return _Plan(items, plan, area, obs, bound).audit()[0]


def _candidates(it, limit, ring, ndir, outward):
    """Box centres to try for one tag in rings round its hole, nearest the outward direction first: -> [(centre, tip)]."""
    hc, R = it["c"], it["R"]
    cands = []
    for k in range(int((limit + R) / ring) + 1):
        rho = 2.0 + k * ring
        for j in range(ndir):
            ang = outward + ((j + 1) // 2) * (2 * math.pi / ndir) * (1 if j % 2 else -1)
            cands.append((rho + 1.5 * _angle_gap(ang, outward), k, j, rho, ang))
    cands.sort()
    out = []
    for _, _, _, rho, ang in cands:
        c = (r3(hc[0] + rho * math.cos(ang)), r3(hc[1] + rho * math.sin(ang)))
        out.append((c, _best_tip(it, c)))
    return out


def _mix(a, b, c):
    """A fixed integer hash of three small integers: the tie key of the repair (no random state, the same on every machine)."""
    return ((a * 73856093) ^ (b * 19349663) ^ (c * 83492791)) & 0xFFFFFFFF


def repair(items, plan, area, obs, view_centre, limit):
    """A deterministic local search on a finished plan: -> the plan with the fewest faults it found (never more than it was given).

    Tabu min-conflicts.  Each pass takes one of the tags that have a fault (most faults first, then tag order, the choice rotating
    with the pass number), first tries to uncross it by exchanging boxes with a tag whose leader crosses its own (uncrossing never
    lengthens two leaders), then moves it to the position in rings round its hole that has the fewest faults against the other tags,
    among the positions clear of the area edge, the obstacles, every hole centre and the distance bound; a position a tag has left
    in the last ``REPAIR_TABU`` passes is not taken again, so a move may keep or add a fault to get out of a dead end.  The best plan
    seen is returned.  Tags the user moved are never moved.  There is no randomness; the inputs are the caller's numbers rounded to
    0.001 mm; the work is capped by ``REPAIR_PASSES`` and ``REPAIR_EVALS`` (tag-pair tests), so the same inputs give the same plan,
    in the GUI and headless.
    """
    pl = _Plan(items, plan, area, obs, limit)
    n = len(items)
    free = [i for i in range(n) if "fixed" not in items[i]]
    vcx, vcy = view_centre
    pool = {}

    def pool_of(i):  # the positions a tag may move to: no fault of its own there
        if i not in pool:
            it = items[i]
            outward = math.atan2(it["c"][1] - vcy, it["c"][0] - vcx)
            pool[i] = [(c, t) for c, t in _candidates(it, limit, REPAIR_RING, REPAIR_DIRS, outward) if not pl.solo(i, c, t)]
        return pool[i]

    def total():
        return sum(pl.count_of(i) for i in range(n))

    best = total()
    best_plan = list(pl.plan)
    work = n * n
    for restart in range(REPAIR_RESTARTS):
        if restart:  # a fresh start from the best plan with about a third of the free tags put elsewhere, chosen by the fixed key
            for i in free:
                if _mix(i, restart, 7) % 100 < REPAIR_SHAKE and pool_of(i):
                    pl.put(i, *pool_of(i)[_mix(i, restart, 13) % len(pool_of(i))])
            work += n * n
        tabu = {}
        for rnd_no in range(REPAIR_PASSES):
            if best == 0 or work > REPAIR_EVALS:
                break
            counts = {i: pl.count_of(i) for i in free}
            work += n * n
            bad = sorted((i for i in free if counts[i]), key=lambda i: (-counts[i], i))
            if not bad:
                break
            i = bad[rnd_no % len(bad)]
            swapped = False
            for j in free:  # 1. uncross by exchanging boxes
                if j == i or not _legs_cross(pl.tails[i], pl.plan[i][1], pl.tails[j], pl.plan[j][1], pl.bbs[i], pl.bbs[j]):
                    continue
                before = pl.count_of(i) + pl.count_of(j)
                (ci, ti), (cj, tj) = pl.plan[i], pl.plan[j]
                pl.put(i, cj, _best_tip(items[i], cj))
                pl.put(j, ci, _best_tip(items[j], ci))
                work += 2 * n
                if pl.count_of(i) + pl.count_of(j) < before:
                    swapped = True
                    break
                pl.put(i, ci, ti)
                pl.put(j, cj, tj)
            if not swapped:  # 2. move to the best position not left in the last passes
                here = pl.plan[i][0]
                pick, pick_key = None, None
                kick = rnd_no % REPAIR_KICK == REPAIR_KICK - 1  # now and then a move that is not the best, to leave a dead end
                for idx, (centre, tip) in enumerate(pool_of(i)):
                    if centre == here or tabu.get((i, centre), -1) >= rnd_no:
                        continue
                    work += n
                    k = 0 if kick else pl.count(i, centre, tip, (pick_key[0] + 1) if pick_key is not None else 1 << 30, False)
                    key = (k, _mix(i, idx, rnd_no + 1000 * restart))  # ties on a fixed key: the tag, the position, the pass
                    if pick_key is None or key < pick_key:
                        pick, pick_key = (centre, tip), key
                if pick is None:
                    continue
                tabu[(i, here)] = rnd_no + REPAIR_TABU
                pl.put(i, *pick)
            cur = total()
            work += n * n
            if cur < best:
                best, best_plan = cur, list(pl.plan)
        if best == 0 or work > REPAIR_EVALS:
            break
        for i in range(n):  # back to the best plan before the next shake
            pl.put(i, *best_plan[i])
    return [tuple(p) for p in best_plan]


_PLAN_MEMO = {}


def plan_tags(items, area, obs, view_centre, limit, soft=None):
    """-> (plan, number of tags with a fault left, faults left as words); see ``_plan_tags``.  The search is a pure function of its
    inputs, and one recompute often asks for the same plan twice (the driver runs again when the hidden-line result arrives), so the
    last few answers are kept."""
    key = repr((items, area, obs, view_centre, limit, sorted(soft) if soft is not None else None))
    hit = _PLAN_MEMO.get(key)
    if hit is None:
        plan, nbad, words = _plan_tags(items, area, obs, view_centre, limit, soft)
        if len(_PLAN_MEMO) >= 8:
            _PLAN_MEMO.pop(next(iter(_PLAN_MEMO)))
        hit = _PLAN_MEMO[key] = ([tuple(p) for p in plan], nbad, list(words))
    return [tuple(p) for p in hit[0]], hit[1], list(hit[2])


def _plan_tags(items, area, obs, view_centre, limit, soft=None):
    """Choose a tag position for every hole: -> (plan, number of tags with a fault left, faults left as words).

    ``items`` are dicts with ``tag``, ``c`` (hole centre), ``R`` (rim radius), ``tips`` (rim points that lie on a visible arc) and,
    for a tag the user moved, ``fixed`` (its box centre); all in page mm, rounded by the caller.  ``obs`` are the (name, box) the
    tags must stay clear of, ``area`` the drawing area; ``limit`` is ruling 27's distance bound, 20 or 35 mm.  ``soft`` names the
    obstacles (the other views) that the last tiers may overlap; the rest (tables, title block) they may not while the area holds
    a place free of them.

    1. A first fit in rings around each hole, most crowded hole first: the nearest free place where the tag lies in the area, clear
       of every obstacle and tag, covers no hole centre, is within the bound of its arrow tip, and whose leader crosses no other
       leader and runs through no other tag.  A tag may lie over its own view.  When a pass leaves a tag unplaced the search runs
       again with the failing tags first and then with smaller margins; the pass with fewest faults wins.
    2. Whatever the first fit leaves is repaired by ``repair`` (leader swaps, then moving the tags with faults).
    3. The final plan is audited on the drawn geometry (``tag_tails``), clean or not, and every fault left is named (ruling 28: never
       silently).
    """
    for it in items:  # the tips a tag may point at: nearer their own hole's centre than any other's
        others = [it2["c"] for it2 in items if it2 is not it]
        good = [q for q in it["tips"] if all(math.hypot(q[0] - c[0], q[1] - c[1]) > it["R"] + 0.02 for c in others)]
        it["tips"] = good or it["tips"]
    best = None
    front = set()
    for prm in ATTEMPTS:
        for _ in range(2):
            plan, tiers = _plan_once(items, area, obs, view_centre, limit, prm, front, soft)
            words, bad = _Plan(items, plan, area, obs, limit).audit()
            key = (len(words), len(bad))
            if best is None or key < best[0]:
                best = (key, plan, words, bad)
            if not words:
                return plan, 0, []
            bad_t = {i for i, t in enumerate(tiers) if t} | bad
            if bad_t <= front:
                break
            front = front | bad_t
    plan = repair(items, best[1], area, obs, view_centre, limit)
    words, bad = _Plan(items, plan, area, obs, limit).audit()
    if len(words) > len(best[2]):  # never worse than the first fit
        plan, words, bad = best[1], best[2], best[3]
    return plan, len(bad), words


def find_slot(w, h, area, obs, pref, gap=GAP_MM, accept=None):
    """-> box (x0, y0, x1, y1) of the best free place of size w x h inside ``area`` clear of ``obs`` by ``gap``, or None.

    ``pref`` maps a candidate box to a number, lower is better.
    """
    m = PL.MARGIN_MM
    xs = {area[0] + m, area[2] - m - w}
    ys = {area[1] + m, area[3] - m - h}
    for _, b in obs:
        xs.update((b[2] + gap, b[0] - gap - w))
        ys.update((b[3] + gap, b[1] - gap - h))
    best, best_key = None, None
    for x in sorted(xs):
        if x < area[0] + m - 1e-6 or x + w > area[2] - m + 1e-6:
            continue
        for y in sorted(ys):
            if y < area[1] + m - 1e-6 or y + h > area[3] - m + 1e-6:
                continue
            box = (x, y, x + w, y + h)
            if any(overlap(box, b, gap - 1e-6) for _, b in obs) or (accept and not accept(box)):
                continue
            key = (round(pref(box), 6), y, x)
            if best_key is None or key < best_key:
                best, best_key = box, key
    return best


def default_corner(page, w, h):
    area, title = PL.page_areas(page)
    right, bottom = PL.anchor(area, title, w)
    return (right - w, bottom, right, bottom + h)


# ------------------------------------------------------------------------------------------------
# sheet helpers shared with RevisionTable
# ------------------------------------------------------------------------------------------------


def col(i):
    return chr(ord("A") + i)


def write_cells(sheet, rows, old_rows):
    """Write ``rows`` (lists of text) into the sheet as quoted text; clear what an earlier, longer table left.  -> True if changed."""
    changed = False
    for r, row in enumerate(rows, start=1):
        for c, txt in enumerate(row):
            want = "'" + txt if txt != "" else ""
            addr = "%s%d" % (col(c), r)
            if sheet.getContents(addr) != want:
                if want:
                    sheet.set(addr, want)
                else:
                    sheet.clear(addr)
                changed = True
    ncols = len(rows[0]) if rows else 0
    for r in range(len(rows) + 1, old_rows + 1):
        for c in range(ncols):
            addr = "%s%d" % (col(c), r)
            if sheet.getContents(addr):
                sheet.clear(addr)
                changed = True
    return changed


def fit_widths(sheet, rows):
    """Fit the sheet's column widths to the widest text and set the row heights; -> (width mm per column list)."""
    ncols = len(rows[0]) if rows else 0
    widths = []
    for c in range(ncols):
        w = max(PL.text_width_px(r[c]) for r in rows) if rows else 0.0
        widths.append(max(PL.MIN_COL_PX, int(math.ceil((w * PL.FIT_SLACK) + PL.PAD_LEFT_PX + PL.PAD_RIGHT_PX))))
    PL.PartsListFit._apply(sheet, widths, len(rows))
    return widths


def set_if(obj, prop, value, eps=1e-9):
    """Assign only when the value differs, so an unchanged object is not touched.  -> True if it changed."""
    cur = getattr(obj, prop)
    cur = getattr(cur, "Value", cur)
    if isinstance(value, float):
        if abs(float(cur) - value) <= eps:
            return False
    elif cur == value:
        return False
    setattr(obj, prop, value)
    return True


def _num(p):
    return float(getattr(p, "Value", p))


# ------------------------------------------------------------------------------------------------
# keeping objects settled after a recompute
# ------------------------------------------------------------------------------------------------


class _Settle:
    """Document observer: when a recompute ends, finish what a driver left half done.

    Objects added or changed in the middle of a recompute stay Touched, and removal inside one is deferred; bring the owned objects
    of every driver up to date and give back the touch, so one ``doc.recompute()`` leaves nothing Touched.
    """

    def __init__(self):
        self.docs = set()
        self.doomed = {}
        self.timers = {}
        self.ticks = {}

    def arm(self, doc):
        self.docs.add(doc.Uid)

    def doom(self, doc, name):
        self.doomed.setdefault(doc.Uid, set()).add(name)

    def slotRecomputedDocument(self, doc):
        if doc.Uid not in self.docs:
            return
        names = self.doomed.pop(doc.Uid, set())
        keep = {}
        # removing outside any transaction opens an implicit one that stays pending and swallows what the caller does next
        # (a page made afterwards would be undone with it): give the removal a transaction of its own
        own = bool(names) and not doc.HasPendingTransaction
        if own:
            doc.openTransaction("Update tables")
        for n in names:
            o = doc.getObject(n)
            if o is None:
                continue
            for dep in list(o.InList) + list(o.OutList):
                if dep.Name not in keep and dep.Name not in names:
                    keep[dep.Name] = "Touched" in dep.State
            doc.removeObject(n)
        for n, was in keep.items():
            dep = doc.getObject(n)
            if dep is not None and not was and "Touched" in dep.State:
                dep.purgeTouched()
        if own:
            doc.commitTransaction()
        for drv in [o for o in doc.Objects if o.TypeId == "App::FeaturePython" and hasattr(getattr(o, "Proxy", None), "owned")]:
            for o in drv.Proxy.owned(drv):
                if "Touched" in o.State:
                    try:
                        o.recompute()
                    except Exception:
                        pass
                    if "Touched" in o.State:
                        try:
                            o.purgeTouched()
                        except Exception:
                            pass

    def poll(self, doc):
        """GUI only: a view's edges arrive from a worker thread after the recompute; run the drivers again once they differ."""
        if not App.GuiUp:
            return
        from PySide import QtCore

        name = doc.Name
        if name in self.timers:
            self.ticks[name] = 0
            return
        t = QtCore.QTimer()
        t.setInterval(150)
        self.timers[name] = t
        self.ticks[name] = 0

        def tick():
            self.ticks[name] = self.ticks.get(name, 0) + 1
            if self.ticks[name] > 100 or name not in App.listDocuments():
                t.stop()
                self.timers.pop(name, None)
                return
            d = App.getDocument(name)
            again = False
            for o in d.Objects:
                p = getattr(o, "Proxy", None)
                if o.TypeId == "App::FeaturePython" and isinstance(p, HoleTableDriver) and (p.stale(o) or p.unsettled(o)):
                    o.touch()
                    again = True
            if again:
                d.recompute()

        t.timeout.connect(tick)
        t.start()


SETTLE = _Settle()
App.addDocumentObserver(SETTLE)


# ------------------------------------------------------------------------------------------------
# the driver
# ------------------------------------------------------------------------------------------------


class HoleTableDriver:
    """Python feature between a view and its hole table: lists the holes, writes the sheet, keeps tags and table blocks."""

    def __init__(self, obj):
        obj.Proxy = self
        add = obj.addProperty
        add("App::PropertyLink", "View", "HoleTable", "The view whose holes are tabulated")
        add("App::PropertyLink", "Sheet", "HoleTable", "The sheet the table views show")
        add("App::PropertyString", "PageName", "HoleTable", "Name of the page")
        add("App::PropertyBool", "HasOrigin", "HoleTable", "True when the origin is the model point below")
        add("App::PropertyVector", "Origin", "HoleTable", "Model point X and Y are measured from")
        add("App::PropertyString", "Signature", "HoleTable", "Visible edges the table was last made against")
        add("App::PropertyString", "Warning", "HoleTable", "What the table could not avoid overlapping, or empty")
        add("App::PropertyInteger", "Rows", "HoleTable", "Rows of the sheet, headers included")
        add("App::PropertyInteger", "HoleCount", "HoleTable", "Holes listed")
        add("App::PropertyInteger", "Violations", "HoleTable", "Tags that had to stand beyond the distance limit or touch a neighbour")
        obj.setEditorMode("Signature", 2)

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def onDocumentRestored(self, obj):
        obj.Proxy = self
        SETTLE.arm(obj.Document)

    # -- objects the driver keeps ---------------------------------------------------------------

    @staticmethod
    def tags(obj):
        return sorted((o for o in obj.Document.Objects if o.TypeId == "TechDraw::DrawViewBalloon" and getattr(o, "HoleDriver", "") == obj.Name), key=lambda o: o.Name)

    @staticmethod
    def blocks(obj):
        return sorted((o for o in obj.Document.Objects if o.TypeId == "TechDraw::DrawViewSpreadsheet" and getattr(o, "HoleDriver", "") == obj.Name), key=lambda o: o.Name)

    def owned(self, obj):
        out = [obj.Sheet] if obj.Sheet is not None else []
        return out + self.blocks(obj) + self.tags(obj)

    def unsettled(self, obj):
        """The view or its page was left Touched by the recompute (the hidden-line run was still going): another pass settles it."""
        v = obj.View
        return v is not None and "Touched" in v.State

    def stale(self, obj):
        v = obj.View
        return v is not None and Edges(v).signature() != obj.Signature

    # -- the work ---------------------------------------------------------------------------------

    def execute(self, obj):
        view, sheet = obj.View, obj.Sheet
        doc = obj.Document
        page = doc.getObject(obj.PageName) if obj.PageName else None
        if view is None or sheet is None or page is None:
            return
        SETTLE.arm(doc)
        edges = Edges(view)
        SETTLE.poll(doc)
        if not edges.edges or edges.box is None:
            return
        obj.Signature = edges.signature()
        holes = listed_holes(view, edges)
        S = float(view.Scale)
        if obj.HasOrigin:
            o = view.projectPoint(Vector(obj.Origin) - gc_of(view))
            org = (o.x, o.y)
        else:
            org = (edges.box0[0], edges.box0[1])
        for h in holes:
            h["X"] = h["p"][0] - org[0]
            h["Y"] = h["p"][1] - org[1]
        # tag order (ruling 26): letters by bore then size text, numbers by X then Y, all on values rounded to 0.01
        sizes = sorted({(round(h["bore"], 2), h["size"]) for h in holes})
        letter = {s: tag_letters(i) for i, s in enumerate(sizes)}
        by_letter = {}
        for h in holes:
            by_letter.setdefault(letter[(round(h["bore"], 2), h["size"])], []).append(h)
        ordered = []
        for L in sorted(by_letter, key=lambda k: (len(k), k)):
            for n, h in enumerate(sorted(by_letter[L], key=lambda h: (round(h["X"], 2), round(h["Y"], 2), h["X"], h["Y"])), start=1):
                h["tag"] = "%s%d" % (L, n)
                ordered.append(h)
        rows = [[h["tag"], fmt(h["X"]), fmt(h["Y"]), h["size"]] for h in ordered]
        # the tables first, in free space, then the tags around the holes with the tables as obstacles (ruling 40)
        mine = self._match_tags(obj, ordered, view, page)
        boxes = self._tables(obj, rows, page, edges)
        self._layout_tags(obj, ordered, mine, view, page, edges, boxes)
        obj.HoleCount = len(ordered)

    # -- tags -------------------------------------------------------------------------------------

    def _match_tags(self, obj, holes, view, page):
        """Tie each hole to the tag of the hole it was (ruling 33); create tags for new holes, doom the rest."""
        doc = obj.Document
        old = self.tags(obj)
        used = set()
        for h in holes:
            best, bd = None, IDENTITY_MM
            for t in old:
                if t.Name in used or t.HoleSize != h["size"]:
                    continue
                dd = math.hypot(t.HoleX - h["p"][0], t.HoleY - h["p"][1])
                if dd < bd:
                    best, bd = t, dd
            if best is not None:
                used.add(best.Name)
                h["tagobj"] = best
        for t in old:
            if t.Name not in used:
                SETTLE.doom(doc, t.Name)
        for h in holes:
            if "tagobj" not in h:
                h["tagobj"] = self._new_tag(obj, page, view)
        return holes

    def _new_tag(self, drv, page, view):
        doc = drv.Document
        b = doc.addObject("TechDraw::DrawViewBalloon", "HoleTag")
        b.SourceView = view
        page.addView(b)
        add = b.addProperty
        add("App::PropertyString", "HoleDriver", "HoleTable", "Name of the driver that keeps this tag")
        add("App::PropertyString", "HoleSize", "HoleTable", "Size text of the hole")
        add("App::PropertyFloat", "HoleX", "HoleTable", "Hole centre in the view's frame")
        add("App::PropertyFloat", "HoleY", "HoleTable", "Hole centre in the view's frame")
        add("App::PropertyFloat", PLACED_X, "HoleTable", "X written by the driver; a different X means the user moved it")
        add("App::PropertyFloat", PLACED_Y, "HoleTable", "Y written by the driver")
        b.HoleDriver = drv.Name
        for p in ("HoleDriver", "HoleSize", "HoleX", "HoleY", PLACED_X, PLACED_Y):
            b.setEditorMode(p, 1)
        b.BubbleShape = "None"
        b.KinkLength = 0.0
        return b

    @staticmethod
    def _moved(t):
        return abs(_num(t.X) - t.PlacedX) > 1e-6 or abs(_num(t.Y) - t.PlacedY) > 1e-6

    def _layout_tags(self, obj, holes, mine, view, page, edges, blocks):
        """Place the tags (the tables are already in place and are obstacles, with the title block and the other views)."""
        S = float(view.Scale)
        vx, vy = r3(view.X.Value), r3(view.Y.Value)
        area, title = PL.page_areas(page)
        names = {t.Name for t in self.tags(obj)} | {b.Name for b in self.blocks(obj)} | {view.Name}
        obs = [(n, tuple(r3(v) for v in b)) for n, b in obstacles(page, skip=names)] + [("hole table", tuple(r3(v) for v in b)) for b in blocks]
        area = tuple(r3(v) for v in area)
        vb = edges.drawn_box
        vcx, vcy = r3(vx + 0.5 * (vb[0] + vb[2]) * S), r3(vy + 0.5 * (vb[1] + vb[3]) * S)
        items = []
        for h in holes:
            tips, R = tip_options(view, h, edges)
            h["tips"], h["R"] = tips, R
            t = h["tagobj"]
            hx, hy = h["pr"]
            it = {
                "tag": h["tag"],
                "c": (r3(vx + hx * S), r3(vy + hy * S)),
                "R": r3(R * S),
                "tips": [(r3(vx + q[0] * S), r3(vy + q[1] * S)) for q in tips],
            }
            if t.HoleSize and self._moved(t):  # a tag the user moved stays where it is
                cx, cy = r3(vx + _num(t.X) * S), r3(vy + _num(t.Y) * S - TAG_ANCHOR_MM)
                it["fixed"] = (cx, cy)
            items.append(it)
        cs = [it["c"] for it in items]
        dense = any(math.hypot(a[0] - b[0], a[1] - b[1]) < NEAR_MM - 0.01 for i, a in enumerate(cs) for b in cs[i + 1 :])
        soft = {v.Label for v in page.Views if hasattr(v, "getVisibleEdges")}  # the other views: a last-resort tag may lie over them, never over a table
        plan, fallbacks, conflicts = plan_tags(items, area, obs, (vcx, vcy), MAX_TAG_DIST_DENSE if dense else MAX_TAG_DIST, soft)
        obj.Violations = fallbacks
        note = ""
        if conflicts:
            note = "HoleTable: no clean tag layout found; left over: " + "; ".join(conflicts)
            App.Console.PrintWarning(note + "\n")
        obj.Warning = (obj.Warning + " | " if obj.Warning and note else obj.Warning) + note
        for h, it, (centre, tip) in zip(holes, items, plan):
            t = h["tagobj"]
            # the tip back into the view's frame (unscaled, relative to the view)
            tx, ty = round((tip[0] - vx) / S, 4), round((tip[1] - vy) / S, 4)
            set_if(t, "Text", h["tag"])
            set_if(t, "OriginX", float(tx))
            set_if(t, "OriginY", float(ty))
            t.HoleSize = h["size"]
            t.HoleX, t.HoleY = float(h["p"][0]), float(h["p"][1])
            if "fixed" not in it:
                px, py = round((centre[0] - vx) / S, 4), round((centre[1] + TAG_ANCHOR_MM - vy) / S, 4)
                set_if(t, "X", float(px))
                set_if(t, "Y", float(py))
                set_if(t, PLACED_X, float(px))
                set_if(t, PLACED_Y, float(py))

    # -- the table --------------------------------------------------------------------------------

    def _tables(self, obj, rows, page, edges):
        """Write the sheet and place the table blocks; -> the boxes of the blocks (page mm)."""
        doc = obj.Document
        sheet = obj.Sheet
        old_blocks = self.blocks(obj)
        view = obj.View
        skip = {b.Name for b in old_blocks} | {t.Name for t in self.tags(obj)} | {view.Name}
        body = rows
        area, title = PL.page_areas(page)
        area = tuple(r3(v) for v in area)
        obs = [(n, tuple(r3(v) for v in b)) for n, b in obstacles(page, skip=skip)]
        own = view_box(view, edges)
        header = list(HEADER)
        # the own view's box, with and without the room its tags will want around it
        near = [(view.Label, tuple(r3(v) for v in own))] if own else []
        room = [(view.Label, tuple(r3(v) for v in (own[0] - TAG_ROOM_MM, own[1] - TAG_ROOM_MM, own[2] + TAG_ROOM_MM, own[3] + TAG_ROOM_MM)))] if own else []
        placed, warning = None, ""
        user = {b.Name: b for b in old_blocks if getattr(b, PLACED_X, None) is not None and self._block_moved(b)}
        nrows = len(body)
        pref = self._pref_point(obj, page, area, own)
        k = 1
        strict = True  # blocks side by side in one row; failing that, rows of blocks, each row below the last
        while True:
            per = max(1, int(math.ceil(nrows / float(k)))) if nrows else 1
            chunks = [body[i : i + per] for i in range(0, nrows, per)] or [[]]
            sheet_rows = []
            for ch in chunks:
                sheet_rows.append(header)
                sheet_rows.extend(ch)
            widths = [max(PL.MIN_COL_PX, int(math.ceil(max(PL.text_width_px(r[c]) for r in sheet_rows) * PL.FIT_SLACK + PL.PAD_LEFT_PX + PL.PAD_RIGHT_PX))) for c in range(4)]
            wmm = sum(widths) * PL.PX_MM
            sizes = [(wmm, (len(ch) + 1) * PL.ROW_PX * PL.PX_MM) for ch in chunks]
            # a block the user moved stays where it is and is an obstacle to the others
            fixed = {}
            for ci in range(min(len(chunks), len(old_blocks))):
                v = old_blocks[ci]
                if v.Name in user:
                    fixed[ci] = (v.X.Value - sizes[ci][0] / 2, v.Y.Value - sizes[ci][1] / 2, v.X.Value + sizes[ci][0] / 2, v.Y.Value + sizes[ci][1] / 2)
            for extra in (room, near):
                taken = list(obs) + list(extra) + [("hole table", b) for b in fixed.values()]
                boxes = [fixed.get(ci) for ci in range(len(chunks))]
                ok = True
                for ci, (bw, bh) in enumerate(sizes):
                    if ci in fixed:
                        continue
                    # side by side in reading order (ruling 37): each block to the right of the one before it
                    left = boxes[ci - 1] if ci and boxes[ci - 1] else None
                    box = find_slot(
                        bw, bh, area, taken, lambda b, p=pref: math.hypot(0.5 * (b[0] + b[2]) - p[0], 0.5 * (b[1] + b[3]) - p[1]),
                        accept=(lambda b, l=left: b[0] >= l[2] + GAP_MM - 1e-6 or (not strict and b[3] <= l[1] - GAP_MM + 1e-6)) if left else None,
                    )
                    if box is None:
                        ok = False
                        break
                    boxes[ci] = tuple(r3(v) for v in box)
                    taken.append(("hole table", boxes[ci]))
                if ok:
                    placed = boxes
                    break
            if placed:
                break
            k += 1
            if strict and (k > MAX_BLOCKS or k > max(nrows, 1)):
                strict, k = False, 1
                continue
            if k > MAX_BLOCKS or k > max(nrows, 1):
                # no free space holds it: the default corner, and say what it overlaps
                k = 1
                chunks = [body] if body else [[]]
                sheet_rows = [header] + body
                widths = [max(PL.MIN_COL_PX, int(math.ceil(max(PL.text_width_px(r[c]) for r in sheet_rows) * PL.FIT_SLACK + PL.PAD_LEFT_PX + PL.PAD_RIGHT_PX))) for c in range(4)]
                wmm = sum(widths) * PL.PX_MM
                hmm = len(sheet_rows) * PL.ROW_PX * PL.PX_MM
                box = default_corner(page, wmm, hmm)
                obs = obs + near
                hit = [n for n, b in obs if overlap(box, b)]
                out = box[3] > area[3] or box[0] < area[0]
                warning = "HoleTable: no free space on the page holds the table (%d rows); placed in the corner, it overlaps %s%s" % (
                    len(body), ", ".join(hit) if hit else "nothing", " and runs outside the drawing area" if out else ""
                )
                placed = [box]
                break
        obj.Warning = warning
        if warning:
            App.Console.PrintWarning(warning + "\n")
        old_total = int(obj.Rows)
        changed = write_cells(sheet, sheet_rows, old_total)
        obj.Rows = len(sheet_rows)
        PL.PartsListFit._apply(sheet, widths, len(sheet_rows))
        if changed:
            sheet.recompute()
        # views: one per block
        views = list(old_blocks)
        while len(views) < len(chunks):
            views.append(self._new_block(obj, page, sheet))
        for extra in views[len(chunks) :]:
            SETTLE.doom(doc, extra.Name)
        r0 = 1
        for ci, ch in enumerate(chunks):
            v = views[ci]
            r1 = r0 + len(ch)
            set_if(v, "CellStart", "A%d" % r0)
            set_if(v, "CellEnd", "D%d" % r1)
            r0 = r1 + 1
            box = placed[ci]
            cx, cy = 0.5 * (box[0] + box[2]), 0.5 * (box[1] + box[3])
            if v.Name in user:
                continue
            set_if(v, "X", float(cx))
            set_if(v, "Y", float(cy))
            set_if(v, PLACED_X, float(cx))
            set_if(v, PLACED_Y, float(cy))
        return placed

    @staticmethod
    def _block_moved(b):
        return abs(b.X.Value - b.PlacedX) > 1e-6 or abs(b.Y.Value - b.PlacedY) > 1e-6

    def _pref_point(self, obj, page, area, b):
        return (0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])) if b else (area[2], area[1])

    def _new_block(self, drv, page, sheet):
        doc = drv.Document
        v = doc.addObject("TechDraw::DrawViewSpreadsheet", "HoleTable")
        v.Label = "Hole table"
        page.addView(v)
        v.addProperty("App::PropertyString", "HoleDriver", "HoleTable", "Name of the driver that keeps this table")
        v.addProperty("App::PropertyFloat", PLACED_X, "HoleTable", "X written by the driver; a different X means the user moved it")
        v.addProperty("App::PropertyFloat", PLACED_Y, "HoleTable", "Y written by the driver")
        v.HoleDriver = drv.Name
        v.PlacedX, v.PlacedY = v.X.Value, v.Y.Value
        v.Source = sheet
        v.TextSize = PL.TEXT_PX
        v.CellStart = "A1"
        v.CellEnd = "D2"
        return v


# ------------------------------------------------------------------------------------------------
# public API
# ------------------------------------------------------------------------------------------------


def drivers_on(page):
    return [
        o
        for o in page.Document.Objects
        if o.TypeId == "App::FeaturePython" and isinstance(getattr(o, "Proxy", None), HoleTableDriver) and o.PageName == page.Name
    ]


def driver_of(table):
    """The driver of a hole table block (or of a tag)."""
    name = getattr(table, "HoleDriver", "")
    return table.Document.getObject(name) if name else None


def tags_of(table):
    d = driver_of(table)
    return HoleTableDriver.tags(d) if d is not None else []


def blocks_of(table):
    d = driver_of(table)
    return HoleTableDriver.blocks(d) if d is not None else []


def warning_of(table):
    d = driver_of(table)
    return d.Warning if d is not None else ""


def begin(doc, name):
    """Open the undo transaction ``name`` unless the caller has one open; -> True when this call opened it.

    A caller's open transaction is joined, not committed or aborted: its macro (the page and view made in it, say) stays one undo
    step, and whatever we make is part of it.  Only a transaction we opened is committed (``end``) or aborted (``fail``) by us.
    """
    if App.getActiveTransaction() is not None:
        return False
    for _ in range(16):  # a stray implicit transaction (a removal made outside any) would swallow ours: close it first
        if not getattr(doc, "HasPendingTransaction", False):
            break
        doc.commitTransaction()
    doc.openTransaction(name)
    return True


def end(doc, own):
    if own:
        doc.commitTransaction()


def fail(doc, own):
    if own:
        doc.abortTransaction()


def wait_for_geometry(view, timeout=20.0):
    """In the GUI a view's hidden line removal runs in a worker thread and its edges arrive with the event loop: let it finish."""
    if not App.GuiUp:
        return
    import time

    from PySide import QtWidgets

    t0, last, changed = time.time(), None, time.time()
    while time.time() - t0 < timeout:
        QtWidgets.QApplication.processEvents()
        now = Edges(view).signature()
        if now != last:
            last, changed = now, time.time()
        elif not now.startswith("0:") and time.time() - changed > 0.4:
            return
        time.sleep(0.01)


def make(page, view, origin=None):
    """Tag every hole ``view`` shows end-on and put their hole table on ``page``; -> the first table view.

    ``origin`` is a model point (Vector or tuple) X and Y are measured from; None means the lower left corner of the box of the
    view's visible edges.  One undo transaction.  Calling it again for the same view returns the table that is there (a new
    ``origin`` is applied).  Afterwards a plain ``doc.recompute()`` follows edits of the model.
    """
    doc = page.Document
    wait_for_geometry(view)
    own = begin(doc, "Hole table")
    try:
        drv = next((d for d in drivers_on(page) if d.View == view), None)
        if drv is None:
            sheet = doc.addObject("Spreadsheet::Sheet", "HoleTableSheet")
            sheet.Label = "Hole table data"
            drv = doc.addObject("App::FeaturePython", "HoleTableDriver")
            HoleTableDriver(drv)
            drv.Label = "Hole table driver"
            drv.View = view
            drv.Sheet = sheet
            drv.PageName = page.Name
        if origin is not None:
            drv.HasOrigin = True
            drv.Origin = Vector(*origin) if not isinstance(origin, Vector) else origin
        else:
            drv.HasOrigin = False
        SETTLE.arm(doc)
        drv.touch()
        doc.recompute()
        if any("Touched" in o.State for o in drv.Proxy.owned(drv)):
            doc.recompute()
    except Exception:
        fail(doc, own)
        raise
    end(doc, own)
    blocks = HoleTableDriver.blocks(drv)
    return blocks[0] if blocks else None
