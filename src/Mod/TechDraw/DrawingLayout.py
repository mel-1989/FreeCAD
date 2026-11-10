# SPDX-License-Identifier: LGPL-2.1-or-later

# **************************************************************************
#   Drawing layout (G04 slice 1): choose the scale and place the views of a *
#   page, then put every PMI dimension, feature control frame and datum    *
#   symbol next to what it labels, clear of the part and of each other,    *
#   with leaders that start on their annotation and do not cross.          *
#                                                                          *
#   Public sources of the conventions: ISO 5455 (the preferred scale       *
#   series 1:100 ... 1:2, 1:1, 2:1 ... 10:1), ISO 128 and ISO 129-1 and    *
#   ASME Y14.5 as stated in public teaching material (dimensions sit       *
#   outside the outline, dimension lines and leaders do not cross), ISO    *
#   3098 (lettering at least 2.5 mm).  Nothing is copied from a standard   *
#   and no compliance is claimed.  Behaviour of TechDraw's own projection  *
#   group (AutoDistribute, spacing, ScaleType) was read from FreeCAD's     *
#   source, which this file's arrangement mirrors for third-angle views.   *
# **************************************************************************

"""Lay out a TechDraw page that holds PMI drawing objects.

    import DrawingLayout
    report = DrawingLayout.layout(page)

``layout`` is deterministic and does not look at the GUI: box sizes come from font metrics (fontTools on the bundled osifont
and Y14.5-FreeCAD), geometry from the model.  It chooses the largest ISO 5455 preferred scale for which the views, spaced
apart, fit the template's drawing area with an annotation margin and every annotation then finds a free place within reach of
its anchor.  An annotation the user moved after the last layout carries ``PmiPinned`` and is kept (and kept clear of) by every
later layout; so are the scale and the view positions while a pin exists.
"""

import math
import os
import re

import numpy as np

import FreeCAD as App
import Part

ISO_SCALES = (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0)  # ISO 5455 preferred series down to 1:100 and up to 10:1
CELL = 0.5  # mm, occupancy grid
LATTICE = 2.0  # mm, candidate positions (page coordinates, a multiple of this)
VIEW_GAP = 20.0  # mm between the outlines of views
FIT_MARGIN = 12.0  # mm kept free between the views and the sheet edge (the annotation margin)
SHEET_INSET = 3.0  # mm an annotation keeps from the sheet edge
CLEAR = 0.8  # mm clearance between annotations
EDGE_CLEAR = 0.6  # mm clearance between annotation text and visible edges
ARROW = 3.9  # mm an arrow head reaches past the end of its dimension line
REACH = 78.0  # mm, the farthest an annotation may sit from its anchor (M6 bar is 80)
MEDIAN_GOAL = 20.0  # mm, a scale whose median reach is above this loses to a smaller one
PIN_TOL = 1e-6
# TechDraw draws a RichAnno's frame with its top left this far from the leader's origin (page mm, y up), measured in run 084
ANNO_OFFSET = {"frame": (1.38, -1.38), "datum": (-0.2, 0.0)}
FRAME_H, DATUM_H = 9.4, 8.6
FRAME_SLACK, DATUM_SLACK = 1.0, 1.3  # how much narrower than the estimate the drawn box may be
DATUM_W = 6.4
DIM_LINE_OFFSET = 3.75  # the dimension line of a distance sits this far below (X) or beside (Y) the text centre
TEXT_K, TEXT_PAD = 6.4, 0.6  # dimension text width per em of osifont advance, and padding
TOL_K = 3.4

_FONTS = {}


def _font(name):
    f = _FONTS.get(name)
    if f is None:
        try:
            from fontTools.ttLib import TTFont

            f = TTFont(os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Resources", "fonts", name))
            f = (f, f.getBestCmap(), f["hmtx"], float(f["head"].unitsPerEm))
        except Exception:
            f = False
        _FONTS[name] = f
    return f or None


def _adv(name, ch, default):
    f = _font(name)
    if f is None:
        return default
    g = f[1].get(ord(ch))
    if g is None:
        return default
    return f[2][g][0] / f[3]


def _osi(text):
    return sum(_adv("osifont-lgpl3fe.ttf", c, 0.55) for c in text)


# ------------------------------------------------------------------------------------------------
# annotation sizes (page mm, from font metrics only)
# ------------------------------------------------------------------------------------------------


def frameSize(text):
    """(width, height) of a feature control frame's drawn box, with a margin: calibrated on 332 frames of the NIST corpus
    (drawn width = 1.33 + 3.16 per compartment + 4.64 per em of symbol glyphs + 5.25 per em of plain text, within -1.8 and
    +1.2 mm)."""
    import PmiDrawing as PD

    cells = PD._cells(text)
    sg = sp = 0.0
    for c in cells:
        for kind, v in c:
            if kind == "g":
                sg += _adv("Y14.5-FreeCAD.ttf", chr(v), 1.4)
            else:
                sp += _osi(v)
    w = 1.33 + 3.16 * len(cells) + 4.64 * sg + 5.25 * sp
    return w + FRAME_SLACK, FRAME_H


def _decimals(spec):
    m = re.search(r"%\.?(\d*)([a-zA-Z])", spec)
    if not m:
        return 2, "w"
    return (int(m.group(1)) if m.group(1) else 6), m.group(2)


def _fmt(spec, val):
    n, kind = _decimals(spec)
    s = "%.*f" % (min(n, 8), val)
    if kind == "w" and "." in s:
        s = s.rstrip("0").rstrip(".")
    return re.sub(r"%\.?\d*[a-zA-Z]", s, spec, count=1)


def dimTextSize(dim):
    """(width, height) of a dimension's text with its tolerance, for an unrotated reading."""
    try:
        raw = float(dim.getRawValue())
    except Exception:
        raw = 0.0
    spec = dim.FormatSpec or "%.2w"
    main = _fmt(spec, raw)
    if dim.Type.startswith("Angle"):
        main += "\u00b0"
    w = TEXT_K * _osi(main) + TEXT_PAD
    h = 5.6
    if dim.TheoreticalExact:
        w += 2.5
        h += 1.5
    elif dim.ArbitraryTolerances:
        tol = [dim.FormatSpecOverTolerance or "", dim.FormatSpecUnderTolerance or ""]
        w += 1.5 + TOL_K * max(_osi(t) for t in tol) + 1.0
        h = max(h, 10.0)
    elif dim.OverTolerance.Value != 0 or dim.UnderTolerance.Value != 0:
        n, _ = _decimals(spec)
        if dim.EqualTolerance:  # one line: the value, then plus-minus and the tolerance
            w = TEXT_K * _osi(main + " \u00b1" + "%.*f" % (min(n, 4), abs(dim.OverTolerance.Value))) + TEXT_PAD + 1.5
        else:  # the two limits stacked beside the value
            tol = ["+%.*f" % (min(n, 4), abs(dim.OverTolerance.Value)), "-%.*f" % (min(n, 4), abs(dim.UnderTolerance.Value))]
            w += 1.5 + TOL_K * max(_osi(t) for t in tol) + 1.0
            h = max(h, 10.0)
    return w, h


# ------------------------------------------------------------------------------------------------
# page geometry
# ------------------------------------------------------------------------------------------------


def _mm(q):
    return float(q.Value) if hasattr(q, "Value") else float(q)


def sheetOf(page):
    """(x0, y0, x1, y1) of the template's drawing area: the template's frame element when its SVG names one, else the page."""
    t = page.Template
    W, H = _mm(t.Width), _mm(t.Height)
    try:
        svg = open(t.Template, encoding="utf-8", errors="replace").read()
        m = re.search(r'viewBox="\s*([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)', svg)
        vb = [float(x) for x in m.groups()] if m else [0, 0, W, H]
        sx, sy = W / vb[2], H / vb[3]
        for tag in re.findall(r"<rect\b[^>]*>", svg):
            if re.search(r'(id|class|inkscape:label)="[^"]*(frame|border|drawing[-_ ]?area)', tag, re.I):
                g = {k: float(v) for k, v in re.findall(r'\b(x|y|width|height)="([-\d.]+)', tag)}
                if "width" in g and "height" in g:
                    x0, y0 = (g.get("x", 0) - vb[0]) * sx, (g.get("y", 0) - vb[1]) * sy
                    return (x0, H - y0 - g["height"] * sy, x0 + g["width"] * sx, H - y0), W, H
    except Exception:
        pass
    return (0.0, 0.0, W, H), W, H


def _group_of(v):
    import PmiDrawing as PD

    return PD.groupOf(v)


def _view_pos(v):
    g = _group_of(v)
    if g is not None:
        return _mm(g.X) + _mm(v.X), _mm(g.Y) + _mm(v.Y)
    return _mm(v.X), _mm(v.Y)


def _snap(x, q=0.5):
    return round(x / q) * q


class _V:
    """A view with its geometry in unscaled view coordinates (the view centre is the origin, y up)."""

    def __init__(self, view, label, source, cur_scale):
        import PmiDrawing as PD

        self.view = view
        self.label = label
        self.vw = PD._View(view, label, source)
        self.s0 = cur_scale
        # a symmetric frame from the geometry-only box of the sources in the view's frame: identical headless and in the GUI
        self.half = self._box()
        self.seg = None
        self.pos = _view_pos(view)

    def _box(self):
        import PmiDrawing as PD

        v = self.view
        try:
            g = _group_of(v)
            srcs = list((g or v).Source)
            shapes = []
            for o in PD._occluder_objects(srcs):
                sh = PD._global_shape(o).copy()
                if not sh.isNull():
                    shapes.append(sh)
            comp = Part.makeCompound(shapes)
            d = self.vw.d
            m = App.Placement(App.Vector(), App.Rotation(self.vw.x, self.vw.y, d, "ZXY"))
            local = comp.copy()
            local.transformShape(m.inverse().toMatrix())
            bb = local.optimalBoundingBox(True, False)
            return ((bb.XMax - bb.XMin) / 2 + 0.2, (bb.YMax - bb.YMin) / 2 + 0.2)
        except Exception:
            fr = self.vw.frame()
            return (max(abs(fr[0]), abs(fr[1])) + 0.2, max(abs(fr[2]), abs(fr[3])) + 0.2)

    def segments(self, deflection):
        """The visible edges as unscaled segments: (P0, P1) arrays of shape (N, 2) (cached)."""
        if self.seg is None:
            p0, p1 = [], []
            s = self.s0
            for e in self.view.getVisibleEdges(True):
                try:
                    pts = e.discretize(Deflection=deflection * s)
                except Exception:
                    continue
                if len(pts) < 2:
                    continue
                a = np.array([(p.x, p.y) for p in pts], dtype=float) / s
                p0.append(a[:-1])
                p1.append(a[1:])
            if p0:
                self.seg = (np.concatenate(p0), np.concatenate(p1))
            else:
                self.seg = (np.zeros((0, 2)), np.zeros((0, 2)))
        return self.seg


# ------------------------------------------------------------------------------------------------
# occupancy grid with integral images
# ------------------------------------------------------------------------------------------------


class _Grid:
    def __init__(self, W, H):
        self.nx, self.ny = int(math.ceil(W / CELL)) + 2, int(math.ceil(H / CELL)) + 2
        self.raw = {k: np.zeros((self.ny, self.nx), dtype=np.int32) for k in ("A", "E", "V", "L", "N")}
        self.integ = {}

    def _rng(self, x0, y0, x1, y1):
        return (
            np.clip(np.floor(np.asarray(x0) / CELL).astype(int), 0, self.nx),
            np.clip(np.ceil(np.asarray(x1) / CELL).astype(int), 0, self.nx),
            np.clip(np.floor(np.asarray(y0) / CELL).astype(int), 0, self.ny),
            np.clip(np.ceil(np.asarray(y1) / CELL).astype(int), 0, self.ny),
        )

    def mark(self, k, x0, y0, x1, y1, infl=0.0):
        i0, i1, j0, j1 = self._rng(x0 - infl, y0 - infl, x1 + infl, y1 + infl)
        self.raw[k][j0:j1, i0:i1] = 1
        self.integ.pop(k, None)

    def markPoints(self, k, xs, ys, cells=1):
        i = np.floor(xs / CELL).astype(int)
        j = np.floor(ys / CELL).astype(int)
        for di in range(-cells, cells + 1):
            for dj in range(-cells, cells + 1):
                ii, jj = i + di, j + dj
                ok = (ii >= 0) & (ii < self.nx) & (jj >= 0) & (jj < self.ny)
                self.raw[k][jj[ok], ii[ok]] = 1
        self.integ.pop(k, None)

    def _I(self, k):
        t = self.integ.get(k)
        if t is None:
            t = np.zeros((self.ny + 1, self.nx + 1), dtype=np.int32)
            t[1:, 1:] = np.cumsum(np.cumsum(self.raw[k] > 0, axis=0, dtype=np.int32), axis=1, dtype=np.int32)
            self.integ[k] = t
        return t

    def count(self, k, x0, y0, x1, y1):
        """How many marked cells of layer k the rectangle covers (anchors are marked one cell each, so this counts them)."""
        t = self.integ.get("#" + k)
        if t is None:
            t = np.zeros((self.ny + 1, self.nx + 1), dtype=np.int32)
            t[1:, 1:] = np.cumsum(np.cumsum(self.raw[k], axis=0, dtype=np.int32), axis=1, dtype=np.int32)
            self.integ["#" + k] = t
        i0, i1, j0, j1 = self._rng(x0, y0, x1, y1)
        return t[j1, i1] - t[j0, i1] - t[j1, i0] + t[j0, i0]

    def markAt(self, k, x, y):
        i, j = int(x / CELL), int(y / CELL)
        if 0 <= i < self.nx and 0 <= j < self.ny:
            self.raw[k][j, i] += 1
        self.integ.pop(k, None)
        self.integ.pop("#" + k, None)

    def hit(self, k, x0, y0, x1, y1):
        """Boolean array: does the rectangle meet a marked cell of layer k."""
        t = self._I(k)
        i0, i1, j0, j1 = self._rng(x0, y0, x1, y1)
        return (t[j1, i1] - t[j0, i1] - t[j1, i0] + t[j0, i0]) > 0


# ------------------------------------------------------------------------------------------------
# small geometry helpers
# ------------------------------------------------------------------------------------------------


def _seg_hits_box(p, q, r, eps=0.0):
    t0, t1 = 0.0, 1.0
    dx, dy = q[0] - p[0], q[1] - p[1]
    for pp, qq in ((-dx, p[0] - (r[0] - eps)), (dx, (r[2] + eps) - p[0]), (-dy, p[1] - (r[1] - eps)), (dy, (r[3] + eps) - p[1])):
        if pp == 0:
            if qq < 0:
                return False
        else:
            t = qq / pp
            if pp < 0:
                if t > t1:
                    return False
                t0 = max(t0, t)
            else:
                if t < t0:
                    return False
                t1 = min(t1, t)
    return True


def _orient(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _seg_cross(p1, p2, q1, q2, margin=0.0):
    """Do the segments cross or come closer than ``margin`` (touching at a shared anchor is the caller's business)."""
    d1, d2 = _orient(q1, q2, p1), _orient(q1, q2, p2)
    d3, d4 = _orient(p1, p2, q1), _orient(p1, p2, q2)
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return True
    if margin > 0:
        for a, b, c in ((p1, q1, q2), (p2, q1, q2), (q1, p1, p2), (q2, p1, p2)):
            if _pt_seg(a, b, c) < margin:
                return True
    return False


def _pt_seg(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def _box_dist(px, py, x0, y0, x1, y1):
    dx = np.maximum(np.maximum(x0 - px, 0.0), px - x1)
    dy = np.maximum(np.maximum(y0 - py, 0.0), py - y1)
    return np.hypot(dx, dy)


# ------------------------------------------------------------------------------------------------
# the annotations of a page
# ------------------------------------------------------------------------------------------------


class _Ann:
    def __init__(self, kind, obj, view):
        self.kind = kind  # 'dim', 'frame', 'datum'
        self.obj = obj  # the dimension or the RichAnno
        self.leader = None
        self.view = view  # _V
        self.pinned = False
        self.anchor = None  # unscaled view coordinates (leader anchor), or None
        self.geo = None  # dimension geometry, unscaled
        self.size = (0.0, 0.0)
        self.box = None  # placed hull (page mm)
        self.text = None  # placed text box (dimensions)
        self.origin = None
        self.dist = None
        self.T = None

    def key(self):
        return (self.kind != "dim", self.kind == "datum", self.view.label, self.obj.Name)


def _dyn(obj, typ, name, doc=""):
    if name not in obj.PropertiesList:
        obj.addProperty(typ, name, "PMI", doc)


def _get_pos(a):
    """The current X,Y of the annotation object(s), page mm."""
    if a.kind == "dim":
        return (_mm(a.obj.X), _mm(a.obj.Y))
    return (_mm(a.obj.X), _mm(a.obj.Y), _mm(a.leader.X), _mm(a.leader.Y))


def _stored(a):
    if "PmiLayout" not in a.obj.PropertiesList:
        return None
    v = a.obj.PmiLayout
    if a.kind == "dim":
        return (v.x, v.y)
    if a.leader is None or "PmiLayout" not in a.leader.PropertiesList:
        return None
    w = a.leader.PmiLayout
    return (v.x, v.y, w.x, w.y)


def _is_pinned(a):
    if "PmiPinned" in a.obj.PropertiesList and a.obj.PmiPinned:
        return True
    st = _stored(a)
    if st is None:
        return False
    cur = _get_pos(a)
    return any(abs(c - s) > 1e-4 for c, s in zip(cur, st))


def _collect(page, views_by_name):
    """Annotation records for every PMI drawing object on the page whose view is known."""
    import PmiDrawing as PD

    anns = []
    leaders = {}
    objs = PD._all_pmi_objects(page)
    for o in objs:
        if o.PmiRole == "leader":
            leaders[o.Name] = o
    for o in objs:
        if o.PmiRole == "dimension":
            r2 = o.References2D
            if not len(r2) or r2[0][0] is None or r2[0][0].Name not in views_by_name:
                continue
            anns.append(_Ann("dim", o, views_by_name[r2[0][0].Name]))
        elif o.PmiRole in ("frame", "datum"):
            ld = o.AnnoParent
            if ld is None or ld.Name not in leaders or ld.LeaderParent is None or ld.LeaderParent.Name not in views_by_name:
                continue
            a = _Ann(o.PmiRole, o, views_by_name[ld.LeaderParent.Name])
            a.leader = ld
            anns.append(a)
    return anns


# ------------------------------------------------------------------------------------------------
# the layout
# ------------------------------------------------------------------------------------------------


class _Plan:
    """One attempt at one scale: view positions and annotation positions, in page mm."""

    def __init__(self, ctx, scale):
        self.ctx = ctx
        self.s = scale
        self.ok = False
        self.reason = ""
        self.pos = {}  # view name -> (x, y)
        self.placed = {}  # annotation name -> dict
        self.dists = []
        self.diag = {}
        self.viol = 0
        self.levels = {}


def _arrangement(vs, s, gap):
    """Positions (relative to the Front view centre) of the views of a projection group, third angle, as TechDraw arranges
    them: Top above and Bottom below the Front, Left and Right beside it, Rear beyond the Right."""
    cell = {"Front": (0, 0), "Top": (0, 1), "Bottom": (0, -1), "Left": (-1, 0), "Right": (1, 0), "Rear": (2, 0)}
    cols, rows = {}, {}
    for v in vs:
        c, r = cell.get(v.label, (3, 0))
        cols[c] = max(cols.get(c, 0.0), 2 * v.half[0] * s)
        rows[r] = max(rows.get(r, 0.0), 2 * v.half[1] * s)
    cols.setdefault(0, 0.0)
    rows.setdefault(0, 0.0)
    xs, ys = {}, {}
    ck = sorted(cols)
    x = 0.0
    xs[0] = 0.0
    for c in ck:
        if c > 0:
            prev = c - 1 if (c - 1) in cols else ck[ck.index(c) - 1]
            xs[c] = xs[prev] + cols[prev] / 2 + gap + cols[c] / 2
    for c in reversed(ck):
        if c < 0:
            nxt = c + 1 if (c + 1) in cols else ck[ck.index(c) + 1]
            xs[c] = xs[nxt] - cols[nxt] / 2 - gap - cols[c] / 2
    rk = sorted(rows)
    ys[0] = 0.0
    for r in rk:
        if r > 0:
            prev = r - 1 if (r - 1) in rows else rk[rk.index(r) - 1]
            ys[r] = ys[prev] + rows[prev] / 2 + gap + rows[r] / 2
    for r in reversed(rk):
        if r < 0:
            nxt = r + 1 if (r + 1) in rows else rk[rk.index(r) + 1]
            ys[r] = ys[nxt] - rows[nxt] / 2 - gap - rows[r] / 2
    rel = {}
    for v in vs:
        c, r = cell.get(v.label, (3, 0))
        rel[v.label] = (xs.get(c, 0.0), ys.get(r, 0.0))
    left = min(rel[v.label][0] - cols[cell.get(v.label, (3, 0))[0]] / 2 for v in vs)
    right = max(rel[v.label][0] + cols[cell.get(v.label, (3, 0))[0]] / 2 for v in vs)
    bot = min(rel[v.label][1] - rows[cell.get(v.label, (3, 0))[1]] / 2 for v in vs)
    top = max(rel[v.label][1] + rows[cell.get(v.label, (3, 0))[1]] / 2 for v in vs)
    return rel, (left, bot, right, top)


class _Layout:
    def __init__(self, page, anns, views, managed, group, pins_exist):
        self.page = page
        self.anns = anns
        self.views = views  # all _V of the page
        self.managed = managed  # _V in the managed projection group
        self.group = group
        self.pins = pins_exist
        self.sheet, self.W, self.H = sheetOf(page)
        self.defl = 0.05

    # ---- views
    def viewPositions(self, s, gap=VIEW_GAP):
        """Page positions of every view at scale s, centred in the sheet; (positions, ok)."""
        pos = {v.view.Name: v.pos for v in self.views}
        sx0, sy0, sx1, sy1 = self.sheet
        if not self.managed or self.pins:
            return pos, True
        rel, (l, b, r, t) = _arrangement(self.managed, s, gap)
        w, h = r - l, t - b
        ok = w <= (sx1 - sx0) - 2 * FIT_MARGIN + 1e-9 and h <= (sy1 - sy0) - 2 * FIT_MARGIN + 1e-9
        cx = _snap((sx0 + sx1) / 2 - (l + r) / 2)
        cy = _snap((sy0 + sy1) / 2 - (b + t) / 2)
        for v in self.managed:
            x, y = rel[v.label]
            pos[v.view.Name] = (_snap(cx + x), _snap(cy + y))
        return pos, ok

    # ---- one attempt
    def plan(self, s, final=False, gap=VIEW_GAP):
        P = _Plan(self, s)
        P.gap = gap
        P.pos, fits = self.viewPositions(s, gap)
        if not fits and not final:
            P.reason = "views do not fit"
            return P
        sx0, sy0, sx1, sy1 = self.sheet
        grid = _Grid(self.W, self.H)
        P.grid = grid
        # views: frames and visible edges
        for v in self.views:
            px, py = P.pos[v.view.Name]
            sc = s if v in self.managed else v.s0
            hx, hy = v.half[0] * sc, v.half[1] * sc
            grid.mark("V", px - hx, py - hy, px + hx, py + hy, 1.0)
            q0, q1 = v.segments(self.defl)
            if len(q0):
                a0, a1 = q0 * sc, q1 * sc
                seg = np.hypot(a1[:, 0] - a0[:, 0], a1[:, 1] - a0[:, 1])
                n = np.maximum(1, np.ceil(seg / 0.25).astype(int))
                total = int(n.sum())
                idx = np.repeat(np.arange(len(seg)), n)
                start = np.cumsum(n) - n
                tt = (np.arange(total) - np.repeat(start, n)) / np.repeat(n, n)
                xs = a0[idx, 0] + (a1[idx, 0] - a0[idx, 0]) * tt + px
                ys = a0[idx, 1] + (a1[idx, 1] - a0[idx, 1]) * tt + py
                grid.markPoints("E", np.concatenate([xs, a1[:, 0] + px]), np.concatenate([ys, a1[:, 1] + py]), 1)
        ax0, ay0, ax1, ay1 = sx0 + SHEET_INSET, sy0 + SHEET_INSET, sx1 - SHEET_INSET, sy1 - SHEET_INSET
        P.inner = (ax0, ay0, ax1, ay1)
        placed_boxes = []  # (box, name)
        leaders = []  # (O, A, name)
        # anchors of every leader are obstacles for dimension hulls
        for a in self.anns:
            if a.leader is not None and a.anchor is not None:
                px, py = P.pos[a.view.view.Name]
                sc = s if a.view in self.managed else a.view.s0
                A = (px + a.anchor[0] * sc, py + a.anchor[1] * sc)
                grid.markAt("N", A[0], A[1])
        P.leaders, P.boxes = leaders, placed_boxes
        # pinned annotations: obstacles at their current place
        free = []
        for a in self.anns:
            if a.pinned:
                self.markPinned(P, a, grid)
            else:
                free.append(a)
        failed = []
        for a in sorted(free, key=lambda a: (self.difficulty(P, a, grid), a.key())):
            r = self.place(P, a, grid, final)
            if r is None:
                failed.append(a.obj.Name)
            else:
                P.placed[a.obj.Name] = r
                P.dists.append(r["dist"])
        P.failed = failed
        P.ok = not failed
        if P.ok and P.dists and not final:
            med = float(np.median(P.dists))
            if med > MEDIAN_GOAL or max(P.dists) > REACH:
                P.ok = False
                P.reason = "reach: median %.1f max %.1f" % (med, max(P.dists))
        elif failed:
            P.reason = "no room for %d annotations" % len(failed)
        return P

    # ---- geometry of annotations at scale s
    def anchorPage(self, P, a):
        px, py = P.pos[a.view.view.Name]
        sc = P.s if a.view in self.managed else a.view.s0
        return (px + a.anchor[0] * sc, py + a.anchor[1] * sc)

    def markPinned(self, P, a, grid):
        px, py = P.pos[a.view.view.Name]
        sc = P.s if a.view in self.managed else a.view.s0
        if a.kind == "dim":
            tx, ty = px + _mm(a.obj.X), py + _mm(a.obj.Y)
            hull, tb, _ = self.dimBox(P, a, np.array([tx]), np.array([ty]), sc, (px, py))
            b = tuple(float(h[0]) for h in hull)
            grid.mark("A", b[0], b[1], b[2], b[3], CLEAR)
            P.boxes.append((b, a.obj.Name))
        else:
            ox, oy = px + _mm(a.leader.X) * sc, py + _mm(a.leader.Y) * sc
            dx, dy = ANNO_OFFSET[a.kind]
            tlx, tly = ox + dx + _mm(a.obj.X), oy + dy + _mm(a.obj.Y)
            w, h = a.size
            b = (tlx, tly - h, tlx + w, tly)
            grid.mark("A", b[0], b[1], b[2], b[3], CLEAR)
            P.boxes.append((b, a.obj.Name))
            A = self.anchorPage(P, a)
            P.leaders.append(((ox, oy), A, a.obj.Name))
            self.markLeader(grid, (ox, oy), A)

    def markLeader(self, grid, O, A):
        n = max(2, int(math.hypot(A[0] - O[0], A[1] - O[1]) / 0.4))
        t = np.arange(n) / (n - 1.0)
        # the last 1.5 mm at the anchor stay unmarked so that two leaders may meet at one anchor
        L = math.hypot(A[0] - O[0], A[1] - O[1])
        t = t[t * L <= max(0.0, L - 1.5)]
        if len(t):
            grid.markPoints("L", O[0] + (A[0] - O[0]) * t, O[1] + (A[1] - O[1]) * t, 0)

    # ---- dimension hulls
    def dimBox(self, P, a, tx, ty, sc, vpos):
        """Hull and text box of a dimension whose text centre is at (tx, ty) (arrays, page mm), the view at vpos and scale sc.
        Returns ((x0,y0,x1,y1) hull, (x0,y0,x1,y1) text, feature distance)."""
        g = a.geo
        tw, th = a.size
        px, py = vpos
        typ = g["type"]
        if typ == "DistanceX":
            (x1, y1), (x2, y2) = [(px + p[0] * sc, py + p[1] * sc) for p in g["pts"]]
            w, h = tw, th
            tb = (tx - w / 2, ty - h / 2, tx + w / 2, ty + h / 2)
            lo, hi = min(x1, x2), max(x1, x2)
            hull = (np.minimum(lo - ARROW, tb[0]), np.minimum(ty - DIM_LINE_OFFSET - 0.9, tb[1]), np.maximum(hi + ARROW, tb[2]), tb[3])
            d = np.minimum(_box_dist(x1, y1, *tb), _box_dist(x2, y2, *tb))
        elif typ == "DistanceY":
            (x1, y1), (x2, y2) = [(px + p[0] * sc, py + p[1] * sc) for p in g["pts"]]
            w, h = th, tw
            tb = (tx - w / 2, ty - h / 2, tx + w / 2, ty + h / 2)
            lo, hi = min(y1, y2), max(y1, y2)
            hull = (tb[0], np.minimum(lo - ARROW, tb[1]), np.maximum(tx + DIM_LINE_OFFSET + 0.9, tb[2]), np.maximum(hi + ARROW, tb[3]))
            d = np.minimum(_box_dist(x1, y1, *tb), _box_dist(x2, y2, *tb))
        elif typ in ("Diameter", "Radius"):
            cx, cy = px + g["c"][0] * sc, py + g["c"][1] * sc
            r = g["r"] * sc
            th_ = np.arctan2(ty - cy, tx - cx)
            th_ = np.where(th_ > math.pi / 2, th_ - math.pi, np.where(th_ <= -math.pi / 2, th_ + math.pi, th_))  # the text stays upright
            ca, sa = np.abs(np.cos(th_)), np.abs(np.sin(th_))
            w = tw * ca + th * sa + 0.8
            h = tw * sa + th * ca + 0.8
            tb = (tx - w / 2, ty - h / 2, tx + w / 2, ty + h / 2)
            # an arrow on each end of the diameter through the centre towards the text, drawn outside the circle
            ux, uy = np.cos(th_), np.sin(th_)
            e = r + ARROW / 2
            ax_ = [cx + e * ux, cx - e * ux]
            ay_ = [cy + e * uy, cy - e * uy]
            ex = 1.75 * ca + 0.6 * sa + 0.25  # half sizes of an arrow's drawn box, turned by the angle of the dimension line
            ey = 1.75 * sa + 0.6 * ca + 0.25
            hx0 = np.minimum(np.minimum(ax_[0], ax_[1]) - ex, tb[0])
            hx1 = np.maximum(np.maximum(ax_[0], ax_[1]) + ex, tb[2])
            hy0 = np.minimum(np.minimum(ay_[0], ay_[1]) - ey, tb[1])
            hy1 = np.maximum(np.maximum(ay_[0], ay_[1]) + ey, tb[3])
            hull = (hx0, hy0, hx1, hy1)
            d = np.maximum(_box_dist(cx, cy, *tb) - r, 0.0)
        else:  # Distance, Angle and anything else: a rotated text, bounded by a circle
            pts = [(px + p[0] * sc, py + p[1] * sc) for p in g["pts"]]
            rr = math.hypot(tw, th) / 2 + 0.5
            tb = (tx - rr, ty - rr, tx + rr, ty + rr)
            if typ.startswith("Angle") and len(pts) >= 3:
                V = pts[2]
                R = np.hypot(tx - V[0], ty - V[1])
                bx0, by0, bx1, by1 = tb
                for q in pts[:2]:
                    L = math.hypot(q[0] - V[0], q[1] - V[1]) or 1.0
                    ex_, ey_ = V[0] + R * (q[0] - V[0]) / L, V[1] + R * (q[1] - V[1]) / L
                    bx0, by0 = np.minimum(bx0, ex_ - 2.0), np.minimum(by0, ey_ - 2.0)
                    bx1, by1 = np.maximum(bx1, ex_ + 2.0), np.maximum(by1, ey_ + 2.0)
                hull = (bx0, by0, bx1, by1)
            else:
                P1, P2 = pts[0], pts[1]
                L = math.hypot(P2[0] - P1[0], P2[1] - P1[1]) or 1.0
                ux_, uy_ = (P2[0] - P1[0]) / L, (P2[1] - P1[1]) / L
                nx_, ny_ = -uy_, ux_
                off = (tx - P1[0]) * nx_ + (ty - P1[1]) * ny_
                bx0, by0, bx1, by1 = tb
                for q, sg in ((P1, -1.0), (P2, 1.0)):
                    ex_ = q[0] + nx_ * off + sg * ux_ * ARROW / 2
                    ey_ = q[1] + ny_ * off + sg * uy_ * ARROW / 2
                    bx0, by0 = np.minimum(bx0, ex_ - 2.0), np.minimum(by0, ey_ - 2.0)
                    bx1, by1 = np.maximum(bx1, ex_ + 2.0), np.maximum(by1, ey_ + 2.0)
                hull = (bx0, by0, bx1, by1)
            d = np.full(np.shape(tx), 1e9)
            for p in pts:
                d = np.minimum(d, _box_dist(p[0], p[1], *tb))
        return hull, tb, d

    def difficulty(self, P, a, grid):
        """How many places the annotation could take before anything else is placed: the fewer, the earlier it goes."""
        px, py = P.pos[a.view.view.Name]
        sc = P.s if a.view in self.managed else a.view.s0
        self.cur_inner = P.inner
        if a.kind == "dim":
            g = a.geo
            if g["type"] in ("Diameter", "Radius"):
                cx, cy = px + g["c"][0] * sc, py + g["c"][1] * sc
                X, Y = self.candidates(cx, cy, a.size[0] / 2, 3.0, REACH + g["r"] * sc)
            else:
                pts = [(px + p[0] * sc, py + p[1] * sc) for p in g["pts"]]
                X, Y = self.candidates(sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts), a.size[0] / 2, a.size[1] / 2, REACH + 40)
            if not len(X):
                return 0
            hull, tb, d = self.dimBox(P, a, X, Y, sc, (px, py))
            ok = ~grid.hit("E", tb[0] - EDGE_CLEAR, tb[1] - EDGE_CLEAR, tb[2] + EDGE_CLEAR, tb[3] + EDGE_CLEAR) & (d <= REACH)
            return int(ok.sum())
        A = self.anchorPage(P, a)
        w, h = a.size
        X, Y = self.candidates(A[0], A[1], w / 2, h / 2, REACH)
        if not len(X):
            return 0
        x0, y0, x1, y1 = X - w / 2, Y - h / 2, X + w / 2, Y + h / 2
        d = _box_dist(A[0], A[1], x0, y0, x1, y1)
        ok = ~grid.hit("E", x0 - EDGE_CLEAR, y0 - EDGE_CLEAR, x1 + EDGE_CLEAR, y1 + EDGE_CLEAR) & (d >= 1.5) & (d <= REACH)
        return int(ok.sum())

    # ---- placing one annotation
    def place(self, P, a, grid, final):
        px, py = P.pos[a.view.view.Name]
        sc = P.s if a.view in self.managed else a.view.s0
        ax0, ay0, ax1, ay1 = P.inner
        if a.kind == "dim":
            return self.placeDim(P, a, grid, sc, (px, py), final)
        return self.placeFrame(P, a, grid, sc, (px, py), final)

    def candidates(self, cx, cy, hw, hh, R):
        """Lattice centres of a box of half size (hw, hh) near (cx, cy), inside the sheet inset."""
        ax0, ay0, ax1, ay1 = self.cur_inner
        x0 = max(ax0 + hw, cx - R)
        x1 = min(ax1 - hw, cx + R)
        y0 = max(ay0 + hh, cy - R)
        y1 = min(ay1 - hh, cy + R)
        if x1 < x0 or y1 < y0:
            return np.zeros(0), np.zeros(0)
        xs = np.arange(math.ceil(x0 / LATTICE), math.floor(x1 / LATTICE) + 1) * LATTICE
        ys = np.arange(math.ceil(y0 / LATTICE), math.floor(y1 / LATTICE) + 1) * LATTICE
        X, Y = np.meshgrid(xs, ys)
        return X.ravel(), Y.ravel()

    def placeFrame(self, P, a, grid, sc, vpos, final):
        self.cur_inner = P.inner
        A = self.anchorPage(P, a)
        w, h = a.size
        for level in (0, 1, 2, 3, 4):
            Rl = (0.6 * REACH, REACH, REACH + 25, REACH + 25, REACH + 25)[level]
            X, Y = self.candidates(A[0], A[1], w / 2, h / 2, Rl)
            if not len(X):
                continue
            x0, y0, x1, y1 = X - w / 2, Y - h / 2, X + w / 2, Y + h / 2
            cA = grid.hit("A", x0, y0, x1, y1)
            cE = grid.hit("E", x0 - EDGE_CLEAR, y0 - EDGE_CLEAR, x1 + EDGE_CLEAR, y1 + EDGE_CLEAR)
            cL = grid.hit("L", x0, y0, x1, y1)
            cV = grid.hit("V", x0, y0, x1, y1) if level == 0 else np.zeros(len(X), dtype=bool)
            d = _box_dist(A[0], A[1], x0, y0, x1, y1)
            P.diag[a.obj.Name] = {"n": len(X), "A": int(cA.sum()), "E": int(cE.sum()), "L": int(cL.sum()), "V": int(cV.sum()), "dmin": int((d < 1.5).sum()), "level": level, "anchor": A}
            ok = ~cA & ~cE & ~cV
            if level < 3:
                ok &= ~cL
            ok &= d >= 1.5
            ok &= d <= Rl
            if level == 4:
                ok = (d >= 1.5) & (d <= Rl)
                cost4 = 3.0 * grid.count("A", x0, y0, x1, y1) + 0.5 * grid.count("E", x0, y0, x1, y1) + d
                k = int(np.lexsort((X, Y, cost4 + np.where(ok, 0.0, 1e9)))[0])
                box = (float(x0[k]), float(y0[k]), float(x1[k]), float(y1[k]))
                O = self.attach(box, A, a)
                P.viol += 10
                P.levels[a.kind + "4"] = P.levels.get(a.kind + "4", 0) + 1
                return self.commitFrame(P, a, grid, box, O, A, float(d[k]), vpos, sc)
            if not ok.any():
                continue
            idx = np.nonzero(ok)[0]
            order = idx[np.lexsort((X[idx], Y[idx], d[idx]))]
            tried = 0
            for k in order:
                box = (float(x0[k]), float(y0[k]), float(x1[k]), float(y1[k]))
                O = self.attach(box, A, a)
                if self.leaderOK(P, O, A, box, a, level >= 2):
                    P.levels[a.kind + str(level)] = P.levels.get(a.kind + str(level), 0) + 1
                    if level >= 2:
                        P.viol += 1
                    return self.commitFrame(P, a, grid, box, O, A, float(d[k]), vpos, sc)
                tried += 1
                if tried > 400:
                    break
        return None

    def attach(self, box, A, a):
        """The point where the leader starts: on the box boundary nearest the anchor, kept inside the drawn box (the drawn box
        may be narrower than the estimate: its left edge and its top and bottom are exact)."""
        x0, y0, x1, y1 = box
        slack = FRAME_SLACK if a.kind == "frame" else DATUM_SLACK
        inset = slack + 0.7 if a.kind == "frame" else 1.2  # a leader starts this far inside the estimated right edge
        cx = min(max(A[0], x0), x1)
        cy = min(max(A[1], y0), y1)
        # nearest side
        dl, dr, db, dt = A[0] - x0, x1 - A[0], A[1] - y0, y1 - A[1]
        if A[0] < x0:
            return (x0 + 0.3, min(max(A[1], y0 + 1.0), y1 - 1.0))
        if A[0] > x1:
            return (x1 - inset - (0.0 if a.kind == "frame" else 0.0), min(max(A[1], y0 + 1.0), y1 - 1.0))
        xr = min(max(A[0], x0 + 2.0), x1 - slack - 1.0)
        return (xr, y0 + 0.1 if A[1] < y0 else y1 - 0.1)

    def leaderOK(self, P, O, A, box, a, relaxed):
        for b, name in P.boxes:
            if _seg_hits_box(O, A, b, 0.3):
                return False
        if not relaxed:
            for (o2, a2, name) in P.leaders:
                shared = math.hypot(A[0] - a2[0], A[1] - a2[1]) < 1.6
                if _seg_cross(O, A, o2, a2, 0.0 if shared else 0.8):
                    if not shared or _seg_cross(O, A, o2, a2, 0.0):
                        return False
        return True

    def commitFrame(self, P, a, grid, box, O, A, d, vpos, sc):
        w, h = a.size
        grid.mark("A", box[0], box[1], box[2], box[3], CLEAR)
        P.boxes.append((box, a.obj.Name))
        P.leaders.append((O, A, a.obj.Name))
        self.markLeader(grid, O, A)
        # TechDraw puts the drawn box's top left at the leader origin plus (ANNO_DX, ANNO_DY) plus the annotation's X, Y
        tlx, tly = box[0], box[3]
        dx, dy = ANNO_OFFSET[a.kind]
        return {"kind": a.kind, "origin": O, "anchor": A, "box": box, "dist": d, "annoXY": (tlx - O[0] - dx, tly - O[1] - dy), "leaderXY": ((O[0] - vpos[0]) / sc, (O[1] - vpos[1]) / sc)}

    def placeDim(self, P, a, grid, sc, vpos, final):
        self.cur_inner = P.inner
        g = a.geo
        px, py = vpos
        typ = g["type"]
        tw, th = a.size
        if typ in ("Diameter", "Radius"):
            cx, cy = px + g["c"][0] * sc, py + g["c"][1] * sc
            r = g["r"] * sc
            X, Y = self.candidates(cx, cy, tw / 2, 3.0, REACH + r)
            mid = (cx, cy)
        else:
            if typ == "DistanceX":
                (x1, y1), (x2, y2) = [(px + p[0] * sc, py + p[1] * sc) for p in g["pts"]]
                mid = ((x1 + x2) / 2, (y1 + y2) / 2)
                hw, hh = tw / 2, th / 2
            elif typ == "DistanceY":
                (x1, y1), (x2, y2) = [(px + p[0] * sc, py + p[1] * sc) for p in g["pts"]]
                mid = ((x1 + x2) / 2, (y1 + y2) / 2)
                hw, hh = th / 2, tw / 2
            else:
                pts = [(px + p[0] * sc, py + p[1] * sc) for p in g["pts"]]
                mid = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
                hw = hh = math.hypot(tw, th) / 2 + 0.5
            X, Y = self.candidates(mid[0], mid[1], hw, hh, REACH + 40)
        if not len(X):
            return None
        hull, tb, d = self.dimBox(P, a, X, Y, sc, vpos)
        ax0, ay0, ax1, ay1 = P.inner
        inside = (hull[0] >= ax0 - 1e-9) & (hull[1] >= ay0 - 1e-9) & (hull[2] <= ax1 + 1e-9) & (hull[3] <= ay1 + 1e-9)
        cA = grid.hit("A", hull[0], hull[1], hull[2], hull[3])
        nN = grid.count("N", hull[0], hull[1], hull[2], hull[3])
        cL = grid.hit("L", hull[0], hull[1], hull[2], hull[3])
        cE = grid.hit("E", tb[0] - EDGE_CLEAR, tb[1] - EDGE_CLEAR, tb[2] + EDGE_CLEAR, tb[3] + EDGE_CLEAR)
        cV = grid.hit("V", tb[0], tb[1], tb[2], tb[3]) if typ not in ("Diameter", "Radius") else np.zeros(len(X), dtype=bool)
        area = (hull[2] - hull[0]) * (hull[3] - hull[1])
        cost = d + 0.012 * area + 60.0 * nN
        if typ in ("DistanceX", "DistanceY"):
            cost = cost + 0.15 * (np.abs(X - mid[0]) if typ == "DistanceX" else np.abs(Y - mid[1]))
        P.diag[a.obj.Name] = {"n": len(X), "A": int(cA.sum()), "N": int((nN > 0).sum()), "L": int(cL.sum()), "E": int(cE.sum()), "V": int(cV.sum()), "inside": int(inside.sum())}
        # level 0: everything clear; 1: a leader anchor of another annotation may lie under the box; 2: and the text may stand in a view
        for level, mask in ((0, ~cA & ~cL & ~cE & ~cV & (nN == 0)), (1, ~cA & ~cL & ~cE & ~cV), (2, ~cA & ~cL & ~cE)):
            ok = mask & inside & (d <= REACH)
            if ok.any():
                idx = np.nonzero(ok)[0]
                k = idx[np.lexsort((X[idx], Y[idx], cost[idx]))[0]]
                P.levels["dim" + str(level)] = P.levels.get("dim" + str(level), 0) + 1
                return self.commitDim(P, grid, a, hull, tb, d, nN, X, Y, k, vpos, 0)
        # last resort: the least overlap
        ok = inside & (d <= REACH + 25)
        if not ok.any():
            return None
        cost4 = 3.0 * grid.count("A", hull[0], hull[1], hull[2], hull[3]) + 0.5 * grid.count("E", tb[0], tb[1], tb[2], tb[3]) + d + np.where(ok, 0.0, 1e9)
        k = int(np.lexsort((X, Y, cost4))[0])
        P.levels["dim3"] = P.levels.get("dim3", 0) + 1
        return self.commitDim(P, grid, a, hull, tb, d, nN, X, Y, k, vpos, 10)

    def commitDim(self, P, grid, a, hull, tb, d, nN, X, Y, k, vpos, extra):
        px, py = vpos
        b = tuple(float(h[k]) if np.ndim(h) else float(h) for h in hull)
        tbk = tuple(float(t[k]) if np.ndim(t) else float(t) for t in tb)
        grid.mark("A", b[0], b[1], b[2], b[3], CLEAR)
        P.boxes.append((b, a.obj.Name))
        P.viol += int(nN[k]) + extra
        return {"kind": "dim", "box": b, "text": tbk, "dist": float(d[k]), "T": (float(X[k]), float(Y[k])), "dimXY": (float(X[k]) - px, float(Y[k]) - py)}


def _dim_geometry(dim, s0):
    """Unscaled feature geometry of a dimension, y up in the view's frame (TechDraw's API returns y down)."""
    t = dim.Type
    if "Invalid" in dim.State:
        return None
    try:
        if t in ("Diameter", "Radius"):
            ap = dim.getArcPoints()
            c = ap[0]
            r = math.hypot(ap[1].x - c.x, ap[1].y - c.y)
            return {"type": t, "c": (c.x / s0, -c.y / s0), "r": r / s0}
        if t.startswith("Angle"):
            pts = dim.getAnglePoints()
        else:
            pts = dim.getLinearPoints()
        if len(pts) < 2:
            return None
        return {"type": t, "pts": [(p.x / s0, -p.y / s0) for p in pts]}
    except Exception:
        return None


def estimateScale(page, sources):
    """The largest preferred scale at which the Front, Top and Right views of ``sources`` (third angle, along the body's axes) fit
    the drawing area of ``page`` with the margin and spacing; None when none does.  PmiDrawing makes its projection group at this
    scale, so the hidden-line removal is not run twice; ``layout`` refines the choice with everything it has to place."""
    import PmiDrawing as PD

    try:
        shapes = [PD._global_shape(o).copy() for o in PD._occluder_objects(list(sources))]
        comp = Part.makeCompound([sh for sh in shapes if not sh.isNull()])
        rot = sources[0].getGlobalPlacement().Rotation if hasattr(sources[0], "getGlobalPlacement") else App.Rotation()
        local = comp.copy()
        local.transformShape(App.Placement(App.Vector(), rot).inverse().toMatrix())
        bb = local.optimalBoundingBox(True, False)
    except Exception:
        return None
    dx, dy, dz = bb.XMax - bb.XMin, bb.YMax - bb.YMin, bb.ZMax - bb.ZMin
    sheet, W, H = sheetOf(page)
    for s in reversed(ISO_SCALES):
        if (dx + dy) * s + VIEW_GAP <= (sheet[2] - sheet[0]) - 2 * FIT_MARGIN and (dz + dy) * s + VIEW_GAP <= (sheet[3] - sheet[1]) - 2 * FIT_MARGIN:
            return s
    return None


def _managed_group(page):
    for o in page.Views:
        if o.TypeId == "TechDraw::DrawProjGroup" and "PmiRole" in o.PropertiesList and o.PmiRole == "view":
            return o
    return None


def _wait_views(page, expect, timeout=120.0):
    """GUI only: hidden-line removal runs in a thread after a scale change; wait until every view's edges have the width the new
    scale gives them and have stopped changing."""
    import time

    if not App.GuiUp:
        return
    from PySide import QtCore

    t0 = time.time()
    stable, last = 0, None
    while True:
        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(40, loop.quit)
        loop.exec_()
        sig = []
        good = True
        for v, w in expect.items():
            es = v.getVisibleEdges(True)
            if not es:
                good = False
                sig.append(0)
                continue
            xmin = min(e.BoundBox.XMin for e in es)
            xmax = max(e.BoundBox.XMax for e in es)
            sig.append((round(xmax - xmin, 3), len(es)))
            if abs((xmax - xmin) - w) > max(0.5, 0.02 * w):
                good = False
        stable = stable + 1 if (good and sig == last) else 0
        last = sig
        if stable >= 6:
            return
        if time.time() - t0 > timeout:
            raise RuntimeError("DrawingLayout: the views of %s did not finish their hidden-line removal in %.0f s" % (page.Name, timeout))


def layout(page, scale=None, _in_transaction=False):
    """Lay out ``page``: scale, view positions, annotation positions, leaders.  One undo transaction.

    Returns {"scale": s, "fits": bool, "placed": n, "pinned": n, "failed": [...], "note": str}.  ``scale`` forces a scale."""
    import PmiDrawing as PD

    doc = page.Document
    if not _in_transaction:
        doc.openTransaction("PMI layout")
    try:
        rep = _layout(page, scale)
    except Exception:
        if not _in_transaction:
            doc.abortTransaction()
        raise
    if not _in_transaction:
        doc.commitTransaction()
    return rep


def _prepare(page):
    """The views and annotations of a page as the layout sees them: (layout object, managed group or None)."""
    import PmiDrawing as PD

    PD.settle(page)
    group = _managed_group(page)
    views = []
    managed = []
    if group is not None:
        s0 = float(group.Scale)
        for v in group.Views:
            lv = _V(v, v.Type, group, s0)
            views.append(lv)
            managed.append(lv)
    for o in PD._page_views(page):
        if group is not None and o in list(group.Views):
            continue
        sc = float(o.Scale) if hasattr(o, "Scale") else 1.0
        views.append(_V(o, o.Label, o, sc))
    by_name = {v.view.Name: v for v in views}
    anns = _collect(page, by_name)
    drop = []
    for a in anns:
        a.pinned = _is_pinned(a)
        if a.kind == "dim":
            a.geo = _dim_geometry(a.obj, a.view.s0)
            if a.geo is None:
                drop.append(a)
                continue
            a.size = dimTextSize(a.obj)
        else:
            a.anchor = _anchor_unscaled(a)
            if a.anchor is None:
                drop.append(a)
                continue
            a.size = frameSize(a.obj.AnnoText) if a.kind == "frame" else (DATUM_W, DATUM_H)
    anns = [a for a in anns if a not in drop]
    pins = any(a.pinned for a in anns)
    return _Layout(page, anns, views, managed, group, pins), group


def annotationBoxes(page):
    """{object name: (x0, y0, x1, y1)} page millimetres: the box the layout reckons each annotation takes where it stands now (a
    dimension's box is the hull of its text and arrows)."""
    lay, group = _prepare(page)
    P = _Plan(lay, float(group.Scale) if group is not None else 1.0)
    P.pos = {v.view.Name: v.pos for v in lay.views}
    out = {}
    for a in lay.anns:
        px, py = P.pos[a.view.view.Name]
        sc = P.s if a.view in lay.managed else a.view.s0
        if a.kind == "dim":
            hull, tb, _ = lay.dimBox(P, a, np.array([px + _mm(a.obj.X)]), np.array([py + _mm(a.obj.Y)]), sc, (px, py))
            out[a.obj.Name] = tuple(float(h[0]) for h in hull)
        else:
            ox, oy = px + _mm(a.leader.X) * sc, py + _mm(a.leader.Y) * sc
            dx, dy = ANNO_OFFSET[a.kind]
            tlx, tly = ox + dx + _mm(a.obj.X), oy + dy + _mm(a.obj.Y)
            out[a.obj.Name] = (tlx, tly - a.size[1], tlx + a.size[0], tly)
    return out


def _layout(page, forced_scale):
    import PmiDrawing as PD

    doc = page.Document
    lay, group = _prepare(page)
    anns, views, managed, pins = lay.anns, lay.views, lay.managed, lay.pins
    if not anns and not managed:
        return {"scale": None, "fits": True, "placed": 0, "pinned": 0, "failed": [], "note": "nothing to lay out"}
    # scale candidates
    cur = float(group.Scale) if group is not None else 1.0
    if group is None or pins:
        scales = [cur]
    elif forced_scale is not None:
        scales = [forced_scale]
    else:
        scales = list(reversed(ISO_SCALES))
    fitting = [x for x in scales if lay.viewPositions(x)[1]]
    # deflection of the edge polylines: 0.08 mm on the page at the largest scale that can be chosen
    lay.defl = 0.08 / (fitting[0] if fitting else cur)
    chosen = None
    tried = []
    spare = 3  # plans to try after the first that places everything, when that one still has a violation
    for i, x in enumerate(fitting):
        last = i == len(fitting) - 1 or forced_scale is not None or len(fitting) == 1
        for gap in ((VIEW_GAP, 1.6 * VIEW_GAP) if (managed and not pins) else (VIEW_GAP,)):
            P = lay.plan(x, final=last, gap=gap)
            tried.append(P)
            if P.reason == "views do not fit":
                continue
            if P.ok and P.viol == 0:
                chosen = P
                break
            if any(t.ok for t in tried):
                spare -= 1
        if chosen is not None or spare <= 0:
            break
    if chosen is None:
        good = [t for t in tried if t.ok]
        if good:
            chosen = min(good, key=lambda t: (t.viol, -t.s, t.gap))
    if chosen is None and tried:
        chosen = tried[-1]  # nothing placed cleanly down to the smallest scale that fits: the best effort at that one
    if chosen is None:
        # no preferred scale down to 1:100 fits the views: the page keeps its scale (reported)
        chosen = lay.plan(cur, final=True)
        chosen.reason = "views do not fit"
    _apply(page, lay, chosen, group)
    return {
        "scale": chosen.s,
        "fits": chosen.reason != "views do not fit",
        "placed": len(chosen.placed),
        "pinned": sum(1 for a in anns if a.pinned),
        "failed": list(chosen.failed),
        "note": chosen.reason,
        "median_reach": float(np.median(chosen.dists)) if chosen.dists else None,
        "max_reach": float(max(chosen.dists)) if chosen.dists else None,
        "tried": [(round(t.s, 4), t.gap, t.ok, t.reason, t.viol) for t in tried],
        "violations": chosen.viol,
        "levels": chosen.levels,
        "diag": {n: chosen.diag.get(n) for n in chosen.failed},
    }


def _anchor_unscaled(a):
    import PmiDrawing as PD

    ld = a.leader
    if "PmiAnchor" not in ld.PropertiesList:
        return None
    u = a.view.vw.u(ld.PmiAnchor)
    return (u.x, u.y)


def _apply(page, lay, P, group):
    import PmiDrawing as PD

    doc = page.Document
    s = P.s
    expect = {}
    rescaled = False
    moved = False
    if group is not None and not lay.pins:
        rescaled = abs(float(group.Scale) - s) > 1e-12 or group.ScaleType != "Custom"
        if rescaled:
            group.ScaleType = "Custom"
            group.Scale = s
        if group.AutoDistribute:
            group.AutoDistribute = False
        front = next((v for v in lay.managed if v.label == "Front"), lay.managed[0])
        fx, fy = P.pos[front.view.Name]
        if abs(_mm(group.X) - fx) > 1e-9 or abs(_mm(group.Y) - fy) > 1e-9:
            group.X, group.Y = fx, fy
        for v in lay.managed:
            if v is front:
                continue
            x, y = P.pos[v.view.Name]
            # moving a projection group item makes TechDraw run its hidden-line removal again: only when it really moves
            if abs(_mm(v.view.X) - (x - fx)) > 1e-9 or abs(_mm(v.view.Y) - (y - fy)) > 1e-9:
                v.view.X, v.view.Y = x - fx, y - fy
        for v in lay.managed:
            q0, q1 = v.segments(lay.defl)
            if len(q0):
                expect[v.view] = (max(q0[:, 0].max(), q1[:, 0].max()) - min(q0[:, 0].min(), q1[:, 0].min())) * s
    # annotations
    for a in lay.anns:
        r = P.placed.get(a.obj.Name)
        px, py = P.pos[a.view.view.Name]
        sc = s if a.view in lay.managed else a.view.s0
        if a.pinned:
            _refresh_pinned(a, P, lay, sc, (px, py))
            _dyn(a.obj, "App::PropertyBool", "PmiPinned", "The user moved this annotation after the last layout: layout keeps it")
            a.obj.PmiPinned = True
            continue
        if r is None:
            continue
        if a.kind == "dim":
            moved |= _set_xy(a.obj, round(r["dimXY"][0], 3), round(r["dimXY"][1], 3))
            _store(a.obj, (_mm(a.obj.X), _mm(a.obj.Y)))
        else:
            ld = a.leader
            moved |= _set_xy(ld, round(r["leaderXY"][0], 4), round(r["leaderXY"][1], 4))
            O, A = r["origin"], r["anchor"]
            ox, oy = px + _mm(ld.X) * sc, py + _mm(ld.Y) * sc
            wp = [App.Vector(0, 0, 0), App.Vector(A[0] - ox, -(A[1] - oy), 0)]
            cur = ld.WayPoints
            if len(cur) != 2 or (cur[1] - wp[1]).Length > 1e-9:
                ld.WayPoints = wp
            moved |= _set_xy(a.obj, round(r["annoXY"][0], 3), round(r["annoXY"][1], 3))
            u = a.view.vw.u(ld.PmiAnchor)
            off = App.Vector(_mm(ld.X) - u.x, _mm(ld.Y) - u.y, 0)
            if (ld.PmiOffset - off).Length > 1e-12:
                ld.PmiOffset = off
            _store(a.obj, (_mm(a.obj.X), _mm(a.obj.Y)))
            _store(ld, (_mm(ld.X), _mm(ld.Y)))
        _dyn(a.obj, "App::PropertyBool", "PmiPinned", "The user moved this annotation after the last layout: layout keeps it")
        if a.obj.PmiPinned:
            a.obj.PmiPinned = False
    doc.recompute()
    if group is not None and not lay.pins:
        _wait_views(page, expect)
        if App.GuiUp and rescaled:
            # with the page open while the scale changes, the Front view's drawn edges can stay at the old scale: touch the views
            # so the GUI redraws them (a page opened afterwards is built from the properties and needs nothing)
            for v in lay.managed:
                v.view.touch()
            group.touch()
            doc.recompute()
            _wait_views(page, expect)
    PD.settle(page)
    if App.GuiUp and (rescaled or moved):
        _rebuild_gui(page)


def _rebuild_gui(page):
    """A page whose window was open while the layout moved things can keep stale scene items (the views' drawn edges at the old
    scale, the group away from its position): hiding and showing the page builds its scene again from the properties.  Nothing
    happens when the page has no window, so a layout never opens one."""
    try:
        import FreeCADGui as Gui
        import TechDrawGui
        from PySide import QtWidgets

        if TechDrawGui.getSceneForPage(page) is None:
            return
        vp = Gui.getDocument(page.Document.Name).getObject(page.Name)
        vp.hide()
        for _ in range(30):
            QtWidgets.QApplication.processEvents()
        vp.show()
        for _ in range(30):
            QtWidgets.QApplication.processEvents()
        import PmiDrawing as PD

        PD.settle(page)
    except Exception:
        pass


def _set_xy(obj, x, y):
    """Write X, Y only when they differ: a write touches the object and TechDraw then recomputes it (a dimension takes 0.3 s)."""
    wrote = False
    if abs(_mm(obj.X) - x) > 1e-9:
        obj.X = x
        wrote = True
    if abs(_mm(obj.Y) - y) > 1e-9:
        obj.Y = y
        wrote = True
    return wrote


def _store(obj, xy):
    _dyn(obj, "App::PropertyVector", "PmiLayout", "Position written by the last layout (X, Y); a different position means the user moved it")
    v = App.Vector(xy[0], xy[1], 0)
    if (obj.PmiLayout - v).Length > 1e-12:
        obj.PmiLayout = v


def _refresh_pinned(a, P, lay, sc, vpos):
    """A pinned frame keeps its places; its leader must still end on the anchor, whatever the scale did."""
    if a.kind == "dim":
        return
    ld = a.leader
    u = a.view.vw.u(ld.PmiAnchor)
    A = (vpos[0] + u.x * sc, vpos[1] + u.y * sc)
    ox, oy = vpos[0] + _mm(ld.X) * sc, vpos[1] + _mm(ld.Y) * sc
    wp = [App.Vector(0, 0, 0), App.Vector(A[0] - ox, -(A[1] - oy), 0)]
    cur = ld.WayPoints
    if len(cur) != 2 or (cur[1] - wp[1]).Length > 1e-9:
        ld.WayPoints = wp
    ld.PmiOffset = App.Vector(_mm(ld.X) - u.x, _mm(ld.Y) - u.y, 0)
