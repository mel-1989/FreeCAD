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
MEDIAN_GOAL = 24.5  # mm, a scale whose median reach is above this loses to a smaller one
PIN_TOL = 1e-6
# global slot assignment (run 085): costs of a conflict, candidate budget and the fixed annealing schedule
W_OVERLAP, W_LEADER_BOX, W_CROSS, W_EDGE = 100.0, 60.0, 30.0, 30.0
KEEP_NEAR, KEEP_SPREAD = 300, 300  # candidates kept per annotation: the cheapest ones plus an even spread of the rest
SWEEPS = 24  # heat-bath sweeps; the move count is capped by iterations, never by the clock (ruling 3)
SWAPS = 300  # exchange trials after the sweeps
SEED = 20261110
FAR = -1.0e6  # a leader that does not exist sits here, away from everything
# TechDraw draws a RichAnno's frame with its top left this far from the leader's origin (page mm, y up), measured in run 084
ANNO_OFFSET = {"frame": (1.38, -1.38), "datum": (-0.2, 0.0)}
FRAME_H, DATUM_H = 9.4, 8.6
FRAME_SLACK, DATUM_SLACK = 1.0, 1.3  # how much narrower than the estimate the drawn box may be
DATUM_W = 6.4
DIM_LINE_OFFSET = 3.75  # the dimension line of a distance sits this far below (X) or beside (Y) the text centre
TEXT_K, TEXT_PAD = 6.4, 0.6  # dimension text width per em of osifont advance, and padding
TOL_K = 3.4
DIA_TOL_H = 11.6  # mm, text height of a diameter or radius dimension with an equal tolerance (run 085 measurement)

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
            if dim.Type in ("Diameter", "Radius"):
                h = max(h, DIA_TOL_H)  # measured in run 085: these draw 10 to 11.6 mm tall when level, not one line
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
        self.raw = {k: np.zeros((self.ny, self.nx), dtype=np.int32) for k in ("E", "V")}
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
        P.leaders, P.boxes = leaders, placed_boxes
        # pinned annotations: obstacles at their current place
        free = []
        for a in self.anns:
            if a.pinned:
                self.markPinned(P, a, grid)
            else:
                free.append(a)
        failed = self.assign(P, free, grid)
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
        """A pinned annotation stays where it is and is a fixed obstacle of the assignment: its box and, for a frame or datum, its leader."""
        px, py = P.pos[a.view.view.Name]
        sc = P.s if a.view in self.managed else a.view.s0
        if a.kind == "dim":
            tx, ty = px + _mm(a.obj.X), py + _mm(a.obj.Y)
            hull, tb, _ = self.dimBox(P, a, np.array([tx]), np.array([ty]), sc, (px, py))
            b = tuple(float(h[0]) for h in hull)
            P.boxes.append((b, a.obj.Name))
        else:
            ox, oy = px + _mm(a.leader.X) * sc, py + _mm(a.leader.Y) * sc
            dx, dy = ANNO_OFFSET[a.kind]
            tlx, tly = ox + dx + _mm(a.obj.X), oy + dy + _mm(a.obj.Y)
            w, h = a.size
            b = (tlx, tly - h, tlx + w, tly)
            P.boxes.append((b, a.obj.Name))
            A = self.anchorPage(P, a)
            P.leaders.append(((ox, oy), A, a.obj.Name))

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

    # ---- global slot assignment (run 085)
    def frameCands(self, P, a, grid):
        self.cur_inner = P.inner
        A = self.anchorPage(P, a)
        w, h = a.size
        X, Y = self.candidates(A[0], A[1], w / 2, h / 2, REACH)
        if not len(X):
            return None
        x0, y0, x1, y1 = X - w / 2, Y - h / 2, X + w / 2, Y + h / 2
        d = _box_dist(A[0], A[1], x0, y0, x1, y1)
        cE = grid.hit("E", x0 - EDGE_CLEAR, y0 - EDGE_CLEAR, x1 + EDGE_CLEAR, y1 + EDGE_CLEAR)
        cV = grid.hit("V", x0, y0, x1, y1)
        ok = (d >= 1.5) & (d <= REACH)
        if (ok & ~cE).sum() >= 20:
            ok &= ~cE
        idx = np.nonzero(ok)[0]
        if not len(idx):
            return None
        static = (d + W_EDGE * cE + 6.0 * cV)[idx]
        keep = idx[_thin(static)]
        sel = {int(k): n for n, k in enumerate(keep)}
        x0, y0, x1, y1, d = x0[keep], y0[keep], x1[keep], y1[keep], d[keep]
        static = (d + W_EDGE * cE[keep] + 6.0 * cV[keep])
        slack = FRAME_SLACK if a.kind == "frame" else DATUM_SLACK
        inset = slack + 0.7 if a.kind == "frame" else 1.2
        cy = np.clip(A[1], y0 + 1.0, y1 - 1.0)
        left, right = A[0] < x0, A[0] > x1
        xr = np.clip(A[0], x0 + 2.0, x1 - slack - 1.0)
        Ox = np.where(left, x0 + 0.3, np.where(right, x1 - inset, xr))
        Oy = np.where(left | right, cy, np.where(A[1] < y0, y0 + 0.1, y1 - 0.1))
        box = np.stack([x0, y0, x1, y1], axis=1)
        return _Cand(a, box, np.stack([Ox, Oy], axis=1), np.array(A, dtype=float), static, {"d": d, "A": A})

    def dimCands(self, P, a, grid):
        self.cur_inner = P.inner
        px, py = P.pos[a.view.view.Name]
        sc = P.s if a.view in self.managed else a.view.s0
        g = a.geo
        typ = g["type"]
        tw, th = a.size
        ax0, ay0, ax1, ay1 = P.inner
        along = None
        if typ in ("DistanceX", "DistanceY"):
            (x1_, y1_), (x2_, y2_) = [(px + p[0] * sc, py + p[1] * sc) for p in g["pts"]]
            mid = ((x1_ + x2_) / 2, (y1_ + y2_) / 2)
            hw, hh = (tw / 2, th / 2) if typ == "DistanceX" else (th / 2, tw / 2)
            offs = np.array([0, -4, 4, -8, 8, -12, 12, -20, 20, -30, 30], dtype=float)
            if typ == "DistanceX":
                xs = np.round(mid[0] + offs)
                ys = np.arange(math.ceil(max(ay0 + hh, mid[1] - REACH - 20) / LATTICE), math.floor(min(ay1 - hh, mid[1] + REACH + 20) / LATTICE) + 1) * LATTICE
                xs = xs[(xs >= ax0 + hw) & (xs <= ax1 - hw)]
            else:
                ys = np.round(mid[1] + offs)
                xs = np.arange(math.ceil(max(ax0 + hw, mid[0] - REACH - 20) / LATTICE), math.floor(min(ax1 - hw, mid[0] + REACH + 20) / LATTICE) + 1) * LATTICE
                ys = ys[(ys >= ay0 + hh) & (ys <= ay1 - hh)]
            if not len(xs) or not len(ys):
                return None
            X, Y = np.meshgrid(xs, ys)
            X, Y = X.ravel(), Y.ravel()
            along = np.abs(X - mid[0]) if typ == "DistanceX" else np.abs(Y - mid[1])
        elif typ in ("Diameter", "Radius"):
            cx, cy = px + g["c"][0] * sc, py + g["c"][1] * sc
            X, Y = self.candidates(cx, cy, tw / 2, 3.0, REACH + g["r"] * sc)
            # the text exactly level with or above the centre: the dimension line is then horizontal or vertical and its box is a thin strip
            R = g["r"] * sc + tw / 2 + 2.0
            ss = np.arange(-REACH, REACH + 0.1, 1.0)
            ss = ss[np.abs(ss) >= R]
            ex = np.concatenate([cx + ss, np.full(len(ss), cx)])
            ey = np.concatenate([np.full(len(ss), cy), cy + ss])
            okc = (ex >= ax0 + tw / 2) & (ex <= ax1 - tw / 2) & (ey >= ay0 + 3.0) & (ey <= ay1 - 3.0)
            X, Y = np.concatenate([X, ex[okc]]), np.concatenate([Y, ey[okc]])
        else:
            pts = [(px + p[0] * sc, py + p[1] * sc) for p in g["pts"]]
            mid = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
            hw = hh = math.hypot(tw, th) / 2 + 0.5
            X, Y = self.candidates(mid[0], mid[1], hw, hh, REACH + 40)
        if not len(X):
            return None
        hull, tb, d = self.dimBox(P, a, X, Y, sc, (px, py))
        inside = (hull[0] >= ax0 - 1e-9) & (hull[1] >= ay0 - 1e-9) & (hull[2] <= ax1 + 1e-9) & (hull[3] <= ay1 + 1e-9)
        cE = grid.hit("E", tb[0] - EDGE_CLEAR, tb[1] - EDGE_CLEAR, tb[2] + EDGE_CLEAR, tb[3] + EDGE_CLEAR)
        cV = grid.hit("V", tb[0], tb[1], tb[2], tb[3]) if typ not in ("Diameter", "Radius") else np.zeros(len(X), dtype=bool)
        area = (hull[2] - hull[0]) * (hull[3] - hull[1])
        static = d + 0.012 * area + 120.0 * cE + 8.0 * cV
        if along is not None:
            static = static + 0.15 * along
        ok = inside & (d <= REACH)
        if (ok & ~cE).sum() >= 10:
            ok &= ~cE
        idx = np.nonzero(ok)[0]
        if not len(idx):
            return None
        keep = idx[_thin(static[idx])]
        box = np.stack([np.asarray(hull[i])[keep] if np.ndim(hull[i]) else np.full(len(keep), float(hull[i])) for i in range(4)], axis=1)
        tbk = np.stack([np.asarray(tb[i])[keep] if np.ndim(tb[i]) else np.full(len(keep), float(tb[i])) for i in range(4)], axis=1)
        far = np.full((len(keep), 2), FAR)
        return _Cand(a, box, far, np.array([FAR, FAR]), static[keep], {"d": d[keep], "X": X[keep], "Y": Y[keep], "tb": tbk, "vpos": (px, py), "cE": cE[keep]})

    def assign(self, P, free, grid):
        """Place every free annotation at once: one candidate each, chosen by a greedy seed (largest first) and a fixed number of
        heat-bath annealing sweeps over the cost of overlaps, leaders over foreign boxes, leader crossings and distance.  The random
        generator is seeded, and the work is capped by sweeps, so the result is the same on every machine and in the GUI."""
        failed = []
        cands = []
        for a in sorted(free, key=lambda a: a.key()):
            c = self.dimCands(P, a, grid) if a.kind == "dim" else self.frameCands(P, a, grid)
            if c is None:
                failed.append(a.obj.Name)
            else:
                cands.append(c)
        lead = {n: (np.array(o, float), np.array(an, float)) for o, an, n in P.leaders}
        fixed_boxes = [b for b, _ in P.boxes]
        fixed_segs = [lead.get(n) for _, n in P.boxes]
        n = len(cands)
        P.resid = {"overlaps": 0, "leader_over_box": 0, "crossings": 0, "dims_over_edge": 0}
        if not n:
            return failed
        F = _Field(fixed_boxes, fixed_segs, n)
        nf = F.nf
        # A leader whose anchor sits on a circle that a diameter or radius dimension measures ends inside that dimension's box wherever
        # the dimension goes (its arrows stand on both sides of the circle, so the box spans the circle): that pair is not charged.
        for i, c in enumerate(cands):
            if c.ann.kind == "dim" or c.A[0] == FAR:
                continue
            for j, d_ in enumerate(cands):
                g = d_.ann.geo if d_.ann.kind == "dim" else None
                if g is not None and g["type"] in ("Diameter", "Radius") and d_.ann.view is c.ann.view:
                    qx, qy = P.pos[d_.ann.view.view.Name]
                    scj = P.s if d_.ann.view in self.managed else d_.ann.view.s0
                    if math.hypot(c.A[0] - (qx + g["c"][0] * scj), c.A[1] - (qy + g["c"][1] * scj)) <= g["r"] * scj + 3.5:
                        F.FORCE[nf + i, nf + j] = True
        # Two diameter or radius dimensions of concentric circles cannot be kept apart: each box spans its circle's centre.
        for i, c in enumerate(cands):
            gi = c.ann.geo if c.ann.kind == "dim" else None
            if gi is None or gi["type"] not in ("Diameter", "Radius"):
                continue
            for j in range(i + 1, n):
                gj = cands[j].ann.geo if cands[j].ann.kind == "dim" else None
                if gj is None or gj["type"] not in ("Diameter", "Radius") or cands[j].ann.view is not c.ann.view:
                    continue
                scj = P.s if c.ann.view in self.managed else c.ann.view.s0
                if math.hypot(gi["c"][0] - gj["c"][0], gi["c"][1] - gj["c"][1]) * scj <= 6.0:
                    F.FORCE_OV[nf + i, nf + j] = F.FORCE_OV[nf + j, nf + i] = True
        rng = np.random.RandomState(SEED)
        sel = [-1] * n

        def others(i):
            idx = np.nonzero(F.ACT)[0]
            return idx[idx != nf + i]

        def choose(i, T):
            c = cands[i]
            E = c.static + F.energy(c.box, c.O, np.broadcast_to(c.A, c.O.shape) if c.A[0] != FAR else c.O, others(i), nf + i)
            if T <= 0:
                k = int(np.argmin(E))
            else:
                w = np.exp(-(E - E.min()) / T)
                cum = np.cumsum(w)
                k = int(min(np.searchsorted(cum, rng.random_sample() * cum[-1]), len(E) - 1))
            return k, E

        def put(i, k):
            c = cands[i]
            sel[i] = k
            F.put(i, c.box[k], c.O[k], c.A)

        order = sorted(range(n), key=lambda i: (-cands[i].area, cands[i].name))
        for i in order:
            k, _ = choose(i, 0.0)
            put(i, k)
        rows = np.arange(nf, nf + n)
        best = (F.state(rows)[0].sum(), list(sel))
        T0, T1 = 30.0, 3.0
        for sweep in range(SWEEPS):
            per, _, _, _ = F.state(rows)
            if per.sum() == 0 and sweep >= 2:
                break
            T = T0 * (T1 / T0) ** (sweep / max(1, SWEEPS - 1))
            if sweep < 2:
                todo = rng.permutation(n)
            else:
                todo = [i for i in rng.permutation(n) if per[i] > 0 or rng.random_sample() < 0.12]
            for i in todo:
                k, _ = choose(int(i), T)
                put(int(i), k)
            tot = F.state(rows)[0].sum()
            if tot < best[0]:
                best = (tot, list(sel))
        if F.state(rows)[0].sum() > best[0]:
            for i in range(n):
                put(i, best[1][i])
        # exchanges: two frames or datums whose leaders cross or whose boxes overlap trade places (the box of one goes where the box of the
        # other stands), which sorts the fans of leaders that single moves cannot untangle
        ctr = [0.5 * (c.box[:, 0] + c.box[:, 2]) for c in cands]
        ctr2 = [0.5 * (c.box[:, 1] + c.box[:, 3]) for c in cands]
        for it in range(SWAPS):
            per, nov_, nlb_, ncr_ = F.state(rows)
            if per.sum() == 0:
                break
            idx = np.nonzero(F.ACT)[0]
            ov, lb, lb2, cr = F.parts(F.BOX[rows], F.SO[rows], F.SA[rows], idx, rows)
            bad = [(a_, int(idx[b_]) - nf) for a_, b_ in zip(*np.nonzero(ov | cr | lb))]
            bad = [(a_, b_) for a_, b_ in bad if b_ >= 0 and a_ != b_ and cands[a_].A[0] != FAR and cands[b_].A[0] != FAR]
            if not bad:
                break
            a_, b_ = bad[int(rng.randint(len(bad)))]
            cxa, cya = 0.5 * (F.BOX[nf + a_, 0] + F.BOX[nf + a_, 2]), 0.5 * (F.BOX[nf + a_, 1] + F.BOX[nf + a_, 3])
            cxb, cyb = 0.5 * (F.BOX[nf + b_, 0] + F.BOX[nf + b_, 2]), 0.5 * (F.BOX[nf + b_, 1] + F.BOX[nf + b_, 3])
            ka = int(np.argmin((ctr[a_] - cxb) ** 2 + (ctr2[a_] - cyb) ** 2))
            kb = int(np.argmin((ctr[b_] - cxa) ** 2 + (ctr2[b_] - cya) ** 2))
            ra, rb = nf + a_, nf + b_
            before = F.state([ra, rb])[0].sum() + cands[a_].static[sel[a_]] + cands[b_].static[sel[b_]]
            oa, ob = sel[a_], sel[b_]
            put(a_, ka)
            put(b_, kb)
            after = F.state([ra, rb])[0].sum() + cands[a_].static[ka] + cands[b_].static[kb]
            if after < before - 1e-9:
                continue
            put(a_, oa)
            put(b_, ob)
        # a last greedy sweep that keeps a candidate unless another is strictly cheaper
        for i in range(n):
            c = cands[i]
            k, E = choose(i, 0.0)
            if E[k] < E[sel[i]] - 1e-9:
                put(i, k)
        per, nov, nlb, ncr = F.state(rows)
        P.resid.update({"overlaps": nov // 2, "leader_over_box": nlb, "crossings": ncr // 2})
        P.viol += nov // 2 + nlb + ncr // 2
        for i, c in enumerate(cands):
            k = sel[i]
            a = c.ann
            px, py = P.pos[a.view.view.Name]
            sc = P.s if a.view in self.managed else a.view.s0
            b = tuple(float(v) for v in c.box[k])
            d = float(c.extra["d"][k])
            if a.kind == "dim":
                if c.extra["cE"][k]:
                    P.resid["dims_over_edge"] += 1
                P.viol += 0
                X, Y = float(c.extra["X"][k]), float(c.extra["Y"][k])
                r = {"kind": "dim", "box": b, "text": tuple(float(v) for v in c.extra["tb"][k]), "dist": d, "T": (X, Y), "dimXY": (X - px, Y - py)}
            else:
                O = (float(c.O[k, 0]), float(c.O[k, 1]))
                Aa = (float(c.A[0]), float(c.A[1]))
                dx, dy = ANNO_OFFSET[a.kind]
                r = {"kind": a.kind, "origin": O, "anchor": Aa, "box": b, "dist": d, "annoXY": (b[0] - O[0] - dx, b[3] - O[1] - dy), "leaderXY": ((O[0] - px) / sc, (O[1] - py) / sc)}
                P.leaders.append((O, Aa, a.obj.Name))
            P.boxes.append((b, a.obj.Name))
            P.placed[a.obj.Name] = r
            P.dists.append(d)
        return failed


def _seg_box_vec(px, py, qx, qy, x0, y0, x1, y1):
    """Does the segment p-q meet the box (broadcasting arrays; Liang-Barsky, as _seg_hits_box)."""
    dx, dy = qx - px, qy - py
    shape = np.broadcast(dx, dy, x0, y0, x1, y1, px, py).shape
    t0 = np.zeros(shape)
    t1 = np.ones(shape)
    ok = np.ones(shape, dtype=bool)
    for pp, qq in ((-dx, px - x0), (dx, x1 - px), (-dy, py - y0), (dy, y1 - py)):
        par = pp == 0
        ok = ok & ~(par & (qq < 0))
        t = qq / np.where(par, 1.0, pp)
        t0 = np.where((pp < 0), np.maximum(t0, t), t0)
        t1 = np.where((pp > 0), np.minimum(t1, t), t1)
    return ok & (t0 <= t1)


def _cross_vec(p1x, p1y, p2x, p2y, q1x, q1y, q2x, q2y):
    """Proper crossing of the segments p1-p2 and q1-q2 (broadcasting arrays)."""

    def o(ax, ay, bx, by, cx, cy):
        return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)

    d1, d2 = o(q1x, q1y, q2x, q2y, p1x, p1y), o(q1x, q1y, q2x, q2y, p2x, p2y)
    d3, d4 = o(p1x, p1y, p2x, p2y, q1x, q1y), o(p1x, p1y, p2x, p2y, q2x, q2y)
    return (d1 * d2 < 0) & (d3 * d4 < 0)


class _Cand:
    """The candidate places of one annotation: collision boxes, leader starts (frames and datums) and a static cost."""

    __slots__ = ("ann", "box", "O", "A", "static", "area", "extra", "name")

    def __init__(self, ann, box, O, A, static, extra):
        self.ann = ann
        self.box = box  # (K, 4)
        self.O = O  # (K, 2) leader starts, FAR for a dimension
        self.A = A  # (2,) the anchor, FAR for a dimension
        self.static = static  # (K,)
        self.extra = extra  # per-kind data for the record
        self.area = float(np.mean((box[:, 2] - box[:, 0]) * (box[:, 3] - box[:, 1])))
        self.name = ann.obj.Name


def _thin(static, keep_near=KEEP_NEAR, keep_spread=KEEP_SPREAD):
    """Indices of the candidates worth keeping: the cheapest ones and an even spread of the rest (deterministic)."""
    order = np.argsort(static, kind="stable")
    if len(order) <= keep_near + keep_spread:
        return order
    rest = order[keep_near:]
    pick = rest[np.linspace(0, len(rest) - 1, keep_spread).astype(int)]
    return np.concatenate([order[:keep_near], pick])


class _Field:
    """The annotations of one plan as arrays (fixed obstacles first, then the movable ones), for the cost of a candidate against the rest."""

    def __init__(self, fixed_boxes, fixed_segs, n):
        nf = len(fixed_boxes)
        self.nf = nf
        self.BOX = np.zeros((nf + n, 4))
        self.SO = np.full((nf + n, 2), FAR)
        self.SA = np.full((nf + n, 2), FAR)
        self.ACT = np.zeros(nf + n, dtype=bool)
        self.FORCE_OV = np.zeros((nf + n, nf + n), dtype=bool)  # two dimensions of one circle: their boxes meet at its centre
        self.FORCE = np.zeros((nf + n, nf + n), dtype=bool)  # FORCE[k, j]: the leader of row k must end inside the box of row j (see assign)
        for i, (b, sg) in enumerate(zip(fixed_boxes, fixed_segs)):
            self.BOX[i] = b
            if sg is not None:
                self.SO[i], self.SA[i] = sg
            self.ACT[i] = True

    def put(self, i, box, O, A):
        r = self.nf + i
        self.BOX[r] = box
        self.SO[r] = O
        self.SA[r] = A
        self.ACT[r] = True

    def parts(self, box, O, A, idx, rows):
        """(overlap, leader-over-box, crossing) boolean matrices (K, M) of K candidate boxes (the rows ``rows`` of the arrays) against the rows ``idx``."""
        B, bO, bA = self.BOX[idx], self.SO[idx], self.SA[idx]
        ov = (box[:, 0:1] < B[None, :, 2] + CLEAR) & (B[None, :, 0] < box[:, 2:3] + CLEAR) & (box[:, 1:2] < B[None, :, 3] + CLEAR) & (B[None, :, 1] < box[:, 3:4] + CLEAR)
        Ax, Ay = (A[:, 0:1], A[:, 1:2]) if A.ndim == 2 else (A[0], A[1])
        lb = _seg_box_vec(O[:, 0:1], O[:, 1:2], Ax, Ay, B[None, :, 0] - 0.3, B[None, :, 1] - 0.3, B[None, :, 2] + 0.3, B[None, :, 3] + 0.3)
        lb2 = _seg_box_vec(bO[None, :, 0], bO[None, :, 1], bA[None, :, 0], bA[None, :, 1], box[:, 0:1] - 0.3, box[:, 1:2] - 0.3, box[:, 2:3] + 0.3, box[:, 3:4] + 0.3)
        cr = _cross_vec(O[:, 0:1], O[:, 1:2], Ax, Ay, bO[None, :, 0], bO[None, :, 1], bA[None, :, 0], bA[None, :, 1])
        rows = np.broadcast_to(np.asarray(rows), (len(box),))
        ov = ov & ~self.FORCE_OV[rows[:, None], idx[None, :]]
        lb = lb & ~self.FORCE[rows[:, None], idx[None, :]]
        lb2 = lb2 & ~self.FORCE[idx[None, :], rows[:, None]]
        return ov, lb, lb2, cr

    def energy(self, box, O, A, idx, row):
        ov, lb, lb2, cr = self.parts(box, O, A, idx, row)
        return W_OVERLAP * ov.sum(axis=1) + W_LEADER_BOX * (lb.sum(axis=1) + lb2.sum(axis=1)) + W_CROSS * cr.sum(axis=1)

    def state(self, rows):
        """Conflicts of the chosen candidates in ``rows`` against every active row: (per-row weighted conflicts, overlaps, leader-over-box, crossings)."""
        idx = np.nonzero(self.ACT)[0]
        box, O, A = self.BOX[rows], self.SO[rows], self.SA[rows]
        ov, lb, lb2, cr = self.parts(box, O, A, idx, rows)
        same = idx[None, :] == np.asarray(rows)[:, None]
        ov, lb, lb2, cr = ov & ~same, lb & ~same, lb2 & ~same, cr & ~same
        per = W_OVERLAP * ov.sum(axis=1) + W_LEADER_BOX * (lb.sum(axis=1) + lb2.sum(axis=1)) + W_CROSS * cr.sum(axis=1)
        return per, int(ov.sum()), int(lb.sum()), int(cr.sum())


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
        if chosen is not None or spare <= 0 or any(t.ok for t in tried):
            break
    if chosen is None:
        good = [t for t in tried if t.ok]
        if good:
            chosen = min(good, key=lambda t: (t.viol, -t.s, t.gap))
    if chosen is None:
        placed = [t for t in tried if t.reason != "views do not fit" and not t.failed and t.dists]
        if placed:  # everything is placed but the reach goal is missed at every scale: the plan with the lowest median reach, the larger scale on a tie
            chosen = min(placed, key=lambda t: (round(float(np.median(t.dists)), 1), -t.s, t.gap))
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
        "resid": getattr(chosen, "resid", None),
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
