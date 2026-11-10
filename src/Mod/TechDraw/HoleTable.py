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
TEXT_MM = 7.0  # label size of a tag, page mm (used to estimate its box)
TAG_PAD_MM = 1.0
TAG_H_MM = 8.0
TIP_STEPS = 24  # rim points tried for an arrow tip
TIP_TOL_MM = 0.02  # a rim point counts as on a visible arc within this
GAP_MM = 2.0  # clear space kept between a table or tag and its neighbours
NEAR_MM = 8.0  # holes closer than this on the page get the larger tag distance
MAX_TAG_DIST = 18.0
MAX_TAG_DIST_DENSE = 33.0
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


def gc_of(view):
    """``view.getGeometricCenter()`` (a quarter of a second on a big part), cached on what it depends on."""
    try:
        key = (view.Document.Uid, view.Name, float(view.Scale), tuple(view.Direction), tuple((o.Shape.hashCode(), str(o.Placement)) for o in view.Source))
    except Exception:
        return view.getGeometricCenter()
    hit = _GC.get(key)
    if hit is None:
        if len(_GC) > 64:
            _GC.clear()
        hit = _GC[key] = view.getGeometricCenter()
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
        self.circles = {}
        for e in self.edges:
            try:
                c = e.Curve
                if c.TypeId == "Part::GeomCircle":
                    r = c.Radius / k
                    self.circles.setdefault(round(r, 2), []).append((c.Center.x / k, c.Center.y / k, r, e.Length / k, e))
            except Exception:
                pass

    def signature(self):
        return "%d:%.3f" % (len(self.edges), sum(e.Length for e in self.edges))

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
    r = c.Radius
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
    rot = float(view.Rotation.Value) if hasattr(view.Rotation, "Value") else float(view.Rotation)
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
    rot = float(view.Rotation.Value) if hasattr(view.Rotation, "Value") else float(view.Rotation)
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


def tag_size(text):
    return PL.text_width_px(text, TEXT_MM) + TAG_PAD_MM, TAG_H_MM


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
                w, h = tag_size(v.Text)
                S = float(sv.Scale)
                cx, cy = sv.X.Value + v.X.Value * S, sv.Y.Value + v.Y.Value * S
                tags_by_view.setdefault(sv.Name, []).append((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2))
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


def find_slot(w, h, area, obs, pref, gap=GAP_MM):
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
            if any(overlap(box, b, gap - 1e-6) for _, b in obs):
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
                if o.TypeId == "App::FeaturePython" and isinstance(p, HoleTableDriver) and p.stale(o):
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
            org = (edges.box[0], edges.box[1])
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
        # tags first (they hug the view), then the table blocks in free space
        mine = self._match_tags(obj, ordered, view, page)
        self._layout_tags(obj, ordered, mine, view, page, edges)
        self._tables(obj, rows, page)
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

    def _layout_tags(self, obj, holes, mine, view, page, edges):
        S = float(view.Scale)
        vx, vy = view.X.Value, view.Y.Value
        area, title = PL.page_areas(page)
        names = {t.Name for t in self.tags(obj)} | {b.Name for b in self.blocks(obj)} | {view.Name}
        obs = obstacles(page, skip=names)
        centres = [(vx + h["p"][0] * S, vy + h["p"][1] * S) for h in holes]
        dense = False
        for i, c in enumerate(centres):
            for j in range(i + 1, len(centres)):
                if math.hypot(c[0] - centres[j][0], c[1] - centres[j][1]) < NEAR_MM:
                    dense = True
                    break
            if dense:
                break
        limit = MAX_TAG_DIST_DENSE if dense else MAX_TAG_DIST
        vcx = vx + 0.5 * (edges.box[0] + edges.box[2]) * S
        vcy = vy + 0.5 * (edges.box[1] + edges.box[3]) * S
        placed_boxes, placed_legs = [], []
        plan = {}
        fallbacks = 0
        # user-moved tags stay where they are
        for h in holes:
            tips, R = tip_options(view, h, edges)
            h["tips"], h["R"] = tips, R
            t = h["tagobj"]
            w, hh = tag_size(h["tag"])
            h["w"], h["h"] = w, hh
            if t.HoleSize and self._moved(t):
                cx, cy = vx + _num(t.X) * S, vy + _num(t.Y) * S
                tip = self._best_tip(h, (cx, cy), vx, vy, S)
                plan[id(h)] = ((cx, cy), tip, True)
                placed_boxes.append((cx - w / 2, cy - hh / 2, cx + w / 2, cy + hh / 2))
                placed_legs.append(((vx + tip[0] * S, vy + tip[1] * S), (cx, cy)))
        for h in holes:
            if id(h) in plan:
                continue
            w, hh = h["w"], h["h"]
            hc = (vx + h["p"][0] * S, vy + h["p"][1] * S)
            Rp = h["R"] * S
            best = None
            rho0 = Rp + 0.5 * math.hypot(w, hh) + 0.5
            outward = math.atan2(hc[1] - vcy, hc[0] - vcx)
            cands = []
            for k in range(0, 40):
                rho = rho0 + k * 1.5
                for j in range(16):
                    ang = outward + (j // 2 + 1) * (math.pi / 8) * (1 if j % 2 == 0 else -1) if j else outward
                    cands.append((rho + 0.4 * (abs(((ang - outward + math.pi) % (2 * math.pi)) - math.pi)), rho, ang))
            cands.sort()
            for _, rho, ang in cands:
                cx, cy = hc[0] + rho * math.cos(ang), hc[1] + rho * math.sin(ang)
                box = (cx - w / 2, cy - hh / 2, cx + w / 2, cy + hh / 2)
                if box[0] < area[0] + 1 or box[1] < area[1] + 1 or box[2] > area[2] - 1 or box[3] > area[3] - 1:
                    continue
                tip = self._best_tip(h, (cx, cy), vx, vy, S)
                tp = (vx + tip[0] * S, vy + tip[1] * S)
                if math.hypot(tp[0] - cx, tp[1] - cy) > limit - 1.0:
                    continue
                if any(overlap(box, b, 0.5) for b in placed_boxes) or any(overlap(box, b, 0.5) for _, b in obs):
                    continue
                if any(box[0] - 0.5 < c[0] < box[2] + 0.5 and box[1] - 0.5 < c[1] < box[3] + 0.5 for c in centres):
                    continue
                if any(_seg_cross(tp, (cx, cy), a, b) for a, b in placed_legs):
                    continue
                if any(_seg_hits_box(tp, (cx, cy), b) for b in placed_boxes):
                    continue
                best = ((cx, cy), tip, False)
                placed_boxes.append(box)
                placed_legs.append((tp, (cx, cy)))
                break
            if best is None:
                # nothing clean within the limit: take the nearest spot that at least covers no hole centre and no other tag
                fallbacks += 1
                best = self._fallback(h, hc, centres, placed_boxes, vx, vy, S)
                placed_boxes.append((best[0][0] - w / 2, best[0][1] - hh / 2, best[0][0] + w / 2, best[0][1] + hh / 2))
                placed_legs.append(((vx + best[1][0] * S, vy + best[1][1] * S), best[0]))
            plan[id(h)] = best
        obj.Violations = fallbacks
        for h in holes:
            t = h["tagobj"]
            (cx, cy), tip, moved = plan[id(h)]
            set_if(t, "Text", h["tag"])
            set_if(t, "OriginX", float(tip[0]))
            set_if(t, "OriginY", float(tip[1]))
            t.HoleSize = h["size"]
            t.HoleX, t.HoleY = float(h["p"][0]), float(h["p"][1])
            if not moved:
                px, py = float((cx - vx) / S), float((cy - vy) / S)
                set_if(t, "X", px)
                set_if(t, "Y", py)
                set_if(t, PLACED_X, px)
                set_if(t, PLACED_Y, py)

    @staticmethod
    def _best_tip(h, target, vx, vy, S):
        """The rim point (view frame) that points most nearly at ``target`` (page mm)."""
        hx, hy = h["pr"]
        want = math.atan2(target[1] - (vy + hy * S), target[0] - (vx + hx * S))
        best, bd = None, 9.0
        for q in h["tips"]:
            a = math.atan2(q[1] - hy, q[0] - hx)
            dd = abs(((a - want + math.pi) % (2 * math.pi)) - math.pi)
            if dd < bd:
                best, bd = q, dd
        return best

    def _fallback(self, h, hc, centres, placed, vx, vy, S):
        w, hh = h["w"], h["h"]
        for k in range(1, 400):
            rho = h["R"] * S + 0.5 * math.hypot(w, hh) + 0.5 * k
            for j in range(24):
                ang = 2 * math.pi * j / 24
                cx, cy = hc[0] + rho * math.cos(ang), hc[1] + rho * math.sin(ang)
                box = (cx - w / 2, cy - hh / 2, cx + w / 2, cy + hh / 2)
                if any(overlap(box, b, 0.2) for b in placed):
                    continue
                if any(box[0] - 0.5 < c[0] < box[2] + 0.5 and box[1] - 0.5 < c[1] < box[3] + 0.5 for c in centres):
                    continue
                return ((cx, cy), self._best_tip(h, (cx, cy), vx, vy, S), False)
        return ((hc[0] + 30.0, hc[1]), self._best_tip(h, (hc[0] + 30.0, hc[1]), vx, vy, S), False)

    # -- the table --------------------------------------------------------------------------------

    def _tables(self, obj, rows, page):
        doc = obj.Document
        sheet = obj.Sheet
        names = {t.Name for t in self.tags(obj)}
        old_blocks = self.blocks(obj)
        skip = {b.Name for b in old_blocks}
        body = rows
        # decide the number of blocks and their places
        area, title = PL.page_areas(page)
        obs = obstacles(page, skip=skip)
        # the tags of this table belong to the obstacles through the view's cluster already
        header = list(HEADER)
        placed, warning = None, ""
        user = {b.Name: b for b in old_blocks if getattr(b, PLACED_X, None) is not None and self._block_moved(b)}
        nrows = len(body)
        k = 1
        while True:
            per = max(1, int(math.ceil(nrows / float(k)))) if nrows else 1
            chunks = [body[i : i + per] for i in range(0, nrows, per)] or [[]]
            sheet_rows = []
            for ch in chunks:
                sheet_rows.append(header)
                sheet_rows.extend(ch)
            widths = [max(PL.MIN_COL_PX, int(math.ceil(max(PL.text_width_px(r[c]) for r in sheet_rows) * PL.FIT_SLACK + PL.PAD_LEFT_PX + PL.PAD_RIGHT_PX))) for c in range(4)]
            wmm = sum(widths) * PL.PX_MM
            boxes, taken = [], list(obs)
            ok = True
            for ci, ch in enumerate(chunks):
                hmm = (len(ch) + 1) * PL.ROW_PX * PL.PX_MM
                vcx = self._pref_point(obj, page, area)
                box = find_slot(wmm, hmm, area, taken, lambda b, p=vcx: math.hypot(0.5 * (b[0] + b[2]) - p[0], 0.5 * (b[1] + b[3]) - p[1]))
                if box is None:
                    ok = False
                    break
                boxes.append(box)
                taken.append(("hole table", box))
            if ok:
                placed = boxes
                break
            k += 1
            if k > MAX_BLOCKS or k > max(nrows, 1):
                # no free space holds it: the default corner, and say what it overlaps
                k = 1
                chunks = [body] if body else [[]]
                sheet_rows = [header] + body
                widths = [max(PL.MIN_COL_PX, int(math.ceil(max(PL.text_width_px(r[c]) for r in sheet_rows) * PL.FIT_SLACK + PL.PAD_LEFT_PX + PL.PAD_RIGHT_PX))) for c in range(4)]
                wmm = sum(widths) * PL.PX_MM
                hmm = len(sheet_rows) * PL.ROW_PX * PL.PX_MM
                box = default_corner(page, wmm, hmm)
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

    @staticmethod
    def _block_moved(b):
        return abs(b.X.Value - b.PlacedX) > 1e-6 or abs(b.Y.Value - b.PlacedY) > 1e-6

    def _pref_point(self, obj, page, area):
        v = obj.View
        b = view_box(v)
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
    """Open the undo transaction ``name`` that holds only what the calling function does.

    A transaction a caller left open (the page and view were made inside it, say) would otherwise swallow ours, and one undo would
    then delete the caller's page; commit it first so ours is a step of its own.
    """
    for _ in range(16):  # openTransaction calls nest: close them all
        if not getattr(doc, "HasPendingTransaction", False):
            break
        doc.commitTransaction()
        if getattr(doc, "HasPendingTransaction", False):
            App.closeActiveTransaction()
    doc.openTransaction(name)


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
    begin(doc, "Hole table")
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
        doc.abortTransaction()
        raise
    doc.commitTransaction()
    blocks = HoleTableDriver.blocks(drv)
    return blocks[0] if blocks else None
