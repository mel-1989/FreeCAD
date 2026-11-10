# SPDX-License-Identifier: LGPL-2.1-or-later

# **************************************************************************
#   PMI onto TechDraw views (G12 slice 4): turn the semantic PMI of a model *
#   (dimensions, geometric tolerances, datums; imported from AP242 or      *
#   authored with Part.PmiAuthoring) into stock TechDraw objects on a      *
#   page: measured dimensions, feature control frames, datum feature       *
#   symbols and their leaders.                                             *
#                                                                          *
#   Public sources of the vocabulary: ASME Y14.5-2018 (feature control     *
#   frame, datum feature symbol, basic dimension), ISO 1101:2017 and       *
#   ISO 5459:2011 (the same frame and datum symbols), ISO 286-1:2010       *
#   (fit classes), the CAx-IF recommended practice for PMI (AP242).        *
# **************************************************************************

"""Project PMI onto TechDraw views.

    import PmiDrawing
    report = PmiDrawing.project(page, body)         # a projection group (Front, Top, Right, more on demand)
    PmiDrawing.refresh(page)                        # follow edits of the PMI items
    PmiDrawing.itemsOn(page)                        # [(drawing object, PMI item), ...]

Nothing is typed into a dimension: TechDraw measures ``References3D`` and a dimension whose measurement differs from the
item's nominal by more than 1e-6 (relative) is not projected.  Every object made carries ``PmiSource`` (a link to its item)
and ``PmiRole`` (``dimension``, ``frame``, ``datum`` or ``leader``).  Annotations are stacked outside the frame of the view on
the side nearest their anchor; layout proper belongs to G04.
"""

import html as _html
import math
import os
import re

import FreeCAD as App
import Part

import PmiAuthoring as PA

SEMANTIC = ("Part::PmiDimension", "Part::PmiTolerance", "Part::PmiDatum")
VIEW_ORDER = ("Front", "Top", "Right", "Left", "Rear", "Bottom")
NOT_PROJECTED = "not projected"
REASONS = ("unattached", "geometry", "no view")
NOMINAL_REL = 1e-6
ANCHOR_TOL = 1e-3  # a point lies on a visible edge of a view when it is this near one (view space, page millimetres)
FONT_FAMILY = "Y14.5-FreeCAD"
PITCH = 9.0  # page millimetres between stacked annotations
MARGIN = 10.0  # page millimetres between a view's frame and its annotations


# ------------------------------------------------------------------------------------------------
# frame text: ASME Y14.5-2018 clause 10 (feature control frame): compartments left to right are the characteristic symbol,
# the tolerance zone (diameter sign, value, zone and material modifiers), then one compartment per datum reference in
# order of precedence (ISO 1101:2017 clause 8, ISO 5459:2011 clause 6).  Glyph codes are those of TechDraw's bundled
# Y14.5-FreeCAD.ttf, read off the font by rendering (slice 2, ruling 5); the word is the fallback when the font is absent.
# ------------------------------------------------------------------------------------------------

_TOL_GLYPH = {
    "Straightness": (0xB6, "straightness"),
    "Flatness": (0xB8, "flatness"),
    "CircularityOrRoundness": (0xB4, "circularity"),
    "Cylindricity": (0xB3, "cylindricity"),
    "ProfileOfLine": (0xB9, "profile of a line"),
    "ProfileOfSurface": (0xBA, "profile of a surface"),
    "Angularity": (0xB2, "angularity"),
    "Perpendicularity": (0xBC, "perpendicularity"),
    "Parallelism": (0xB7, "parallelism"),
    "Symmetry": (0xBB, "symmetry"),
    "Position": (0xBF, "position"),
    "Concentricity": (0xB5, "concentricity"),
    "Coaxiality": (0xB5, "coaxiality"),
    "CircularRunout": (0xBD, "circular runout"),
    "TotalRunout": (0xBE, "total runout"),
}
_MOD_GLYPH = {
    "MAXIMUM_MATERIAL_REQUIREMENT": (0xCC, "MMC"),
    "LEAST_MATERIAL_REQUIREMENT": (0xCB, "LMC"),
    "FREE_STATE": (0xC5, "FREE STATE"),
    "TANGENT_PLANE": (0xD2, "TANGENT PLANE"),
    "STATISTICAL_TOLERANCE": (0xC6, "ST"),
}
_ZONE_GLYPH = {"Diameter": (0xD8, "DIA"), "SphericalDiameter": (0xDB, "SDIA")}
_PROJECTED = (0xCE, "PROJECTED")
_ZONE_WORD = {"Runout": "RUNOUT", "NonUniform": "NONUNIFORM"}


def fontAvailable():
    return os.path.exists(os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Resources", "fonts", "Y14.5-FreeCAD.ttf"))


def _tok(glyph, plain=None):
    """One token: (code, word) glyph pair or a plain text string, as an HTML span."""
    if isinstance(glyph, tuple):
        code, word = glyph
        if fontAvailable():
            return "<span style=\"font-family:'%s';\">&#%d;</span>" % (FONT_FAMILY, code)
        return "<span>%s</span>" % _html.escape(word)
    return "<span>%s</span>" % _html.escape(glyph)


def _num(x):
    return "%.9g" % x


def _mod_tok(m):
    if m in _MOD_GLYPH:
        return _tok(_MOD_GLYPH[m])
    return _tok(m.replace("_", " "))


def frameHtml(item):
    """AnnoText of a feature control frame: one table row, a cell per compartment."""
    cells = []
    t = item.PmiType
    cells.append((_tok(_TOL_GLYPH[t]) if t in _TOL_GLYPH else _tok(t)) + (_tok("COAXIALITY") if t == "Coaxiality" else ""))
    zone = []
    if item.ZoneShape in _ZONE_GLYPH:
        zone.append(_tok(_ZONE_GLYPH[item.ZoneShape]))
    zone.append(_tok(_num(item.Value)))
    if item.ZoneModifier == "Projected":
        zone.append(_tok(_PROJECTED))
        if item.ZoneModifierValue > 0:
            zone.append(_tok(_num(item.ZoneModifierValue)))
    elif item.ZoneModifier in _ZONE_WORD:
        zone.append(_tok(_ZONE_WORD[item.ZoneModifier]))
    for m in item.Modifiers:
        zone.append(_mod_tok(m))
    cells.append("".join(zone))
    for ref in item.DatumReferences:
        letter, mods = _split_ref(ref)
        cells.append(_tok(letter) + "".join(_mod_tok(m) for m in mods))
    row = "".join("<td>%s</td>" % c for c in cells)
    return "<table border=\"1\" cellspacing=\"0\" cellpadding=\"3\"><tr>%s</tr></table>" % row


def _split_ref(ref):
    if "(" in ref and ref.endswith(")"):
        letter, rest = ref[:-1].split("(", 1)
        return letter, [m for m in rest.split(",") if m]
    return ref, []


_CODE_TOL = {}
for _k, (_c, _w) in _TOL_GLYPH.items():
    _CODE_TOL.setdefault(_c, _k)  # Concentricity and Coaxiality share a glyph; the word tells them apart
_WORD_TOL = {w: k for k, (c, w) in _TOL_GLYPH.items()}
_WORD_TOL["coaxiality"] = "Concentricity"  # the word of the shared glyph; the COAXIALITY token tells them apart
_CODE_MOD = {c: k for k, (c, w) in _MOD_GLYPH.items()}
_WORD_MOD = {w: k for k, (c, w) in _MOD_GLYPH.items()}
_CODE_ZONE = {c: k for k, (c, w) in _ZONE_GLYPH.items()}
_WORD_ZONE = {w: k for k, (c, w) in _ZONE_GLYPH.items()}
_WORD_ZMOD = {v: k for k, v in _ZONE_WORD.items()}
_SPAN = re.compile(r"<span([^>]*)>(.*?)</span>", re.S)


def _cells(text):
    out = []
    for cell in re.findall(r"<td[^>]*>(.*?)</td>", text, re.S):
        toks = []
        for attrs, body in _SPAN.findall(cell):
            s = _html.unescape(body)
            toks.append(("g", ord(s)) if "font-family" in attrs and len(s) == 1 else ("t", s))
        out.append(toks)
    return out


def parseFrame(text):
    """Read a frame back: (tolerance type, zone shape, value, zone modifier, zone modifier value, modifiers, datum compartments).

    The datum compartments are [(letter, [modifiers])] in order."""
    cells = _cells(text)
    sym = cells[0][0]
    if sym[0] == "g":
        ttype = _CODE_TOL.get(sym[1])
    else:
        ttype = _WORD_TOL.get(sym[1], sym[1])
    if len(cells[0]) > 1 and cells[0][1] == ("t", "COAXIALITY"):
        ttype = "Coaxiality"
    shape, value, zmod, zval, mods = "", None, "", 0.0, []
    toks = list(cells[1])
    i = 0
    if toks and ((toks[0][0] == "g" and toks[0][1] in _CODE_ZONE) or (toks[0][0] == "t" and toks[0][1] in _WORD_ZONE)):
        shape = _CODE_ZONE[toks[0][1]] if toks[0][0] == "g" else _WORD_ZONE[toks[0][1]]
        i = 1
    value = float(toks[i][1])
    i += 1
    while i < len(toks):
        kind, v = toks[i]
        if (kind == "g" and v == _PROJECTED[0]) or (kind == "t" and v == _PROJECTED[1]):
            zmod = "Projected"
            if i + 1 < len(toks) and _is_number(toks[i + 1]):
                zval = float(toks[i + 1][1])
                i += 1
        elif kind == "t" and v in _WORD_ZMOD:
            zmod = _WORD_ZMOD[v]
        else:
            mods.append(_mod_name(kind, v))
        i += 1
    datums = []
    for cell in cells[2:]:
        letter = cell[0][1]
        datums.append((letter, [_mod_name(k, v) for k, v in cell[1:]]))
    return ttype, shape, value, zmod, zval, mods, datums


def _is_number(tok):
    try:
        float(tok[1])
        return tok[0] == "t"
    except ValueError:
        return False


def _mod_name(kind, v):
    if kind == "g":
        return _CODE_MOD.get(v, "?%d" % v)
    return _WORD_MOD.get(v, v.replace(" ", "_"))


def datumHtml(letter):
    return "<p>%s</p>" % _html.escape(letter)


def parseDatum(text):
    return _html.unescape(re.sub(r"<[^>]*>", "", re.sub(r"<head>.*?</head>", "", text, flags=re.S))).strip()


# ------------------------------------------------------------------------------------------------
# tolerances of a dimension (ruling 5)
# ------------------------------------------------------------------------------------------------


def nominalOf(item):
    """The stated nominal of a dimension, or None when it states none: HasValue is false, or the STEP reader lost the value
    (KernelLoss says so and Value 0.0 is not a measurement)."""
    if not item.HasValue or any("no value is stated" in k for k in item.KernelLoss):
        return None
    return item.Value


def expectedTolerances(item):
    """What ruling 5 derives from an item: dict with exact, equal, arbitrary, over, under, spec."""
    e = {"exact": False, "equal": True, "arbitrary": False, "over": 0.0, "under": 0.0, "spec": ""}
    # TheoreticalExact (a basic dimension) is never set from the PMI model: it carries no basic flag, and what looked like a
    # basic dimension (a dimension no frame refers to) can be a reference dimension (the STEP "dimensional note" text that
    # says so is lost by OCCT's reader).  The mapping below stays for a future explicit item property.
    kind = item.BoundsKind
    if item.HasBounds and kind == "plus_minus":
        up, lo = item.UpperBound, item.LowerBound
        e["over"], e["under"] = up, lo
        e["equal"] = up >= 0 and abs(up + lo) <= 1e-9 * max(1.0, abs(up))
        if e["equal"]:
            e["under"] = -up
    elif item.HasBounds and kind == "range" and nominalOf(item) is not None:
        e["over"], e["under"] = item.UpperBound - item.Value, item.LowerBound - item.Value
        e["equal"] = e["over"] >= 0 and abs(e["over"] + e["under"]) <= 1e-9 * max(1.0, abs(e["over"]))
        if e["equal"]:
            e["under"] = -e["over"]
    if item.TypeId == "Part::PmiDimension" and item.ToleranceClass:
        e["arbitrary"] = True
        e["spec"] = item.ToleranceClass
    return e


def _tolerances_of(dim):
    return {
        "exact": bool(dim.TheoreticalExact),
        "equal": bool(dim.EqualTolerance),
        "arbitrary": bool(dim.ArbitraryTolerances),
        "over": dim.OverTolerance.Value,
        "under": dim.UnderTolerance.Value,
        "spec": dim.FormatSpecOverTolerance if dim.ArbitraryTolerances else "",
    }


def _apply_tolerances(dim, e):
    """Set only what differs, so a refresh that finds nothing new touches nothing."""
    cur = _tolerances_of(dim)
    if cur == _expected_view(e):
        return False
    if e["exact"]:
        dim.TheoreticalExact = True
        return True
    if dim.TheoreticalExact:
        dim.TheoreticalExact = False
    if bool(dim.EqualTolerance) != e["equal"]:
        dim.EqualTolerance = e["equal"]
    if abs(dim.OverTolerance.Value - e["over"]) > 1e-15:
        dim.OverTolerance = e["over"]
    if not e["equal"] and abs(dim.UnderTolerance.Value - e["under"]) > 1e-15:
        dim.UnderTolerance = e["under"]
    if bool(dim.ArbitraryTolerances) != e["arbitrary"]:
        dim.ArbitraryTolerances = e["arbitrary"]
    if e["arbitrary"] and dim.FormatSpecOverTolerance != e["spec"]:
        dim.FormatSpecOverTolerance = e["spec"]
    return True


def _expected_view(e):
    """The tolerance dict a dimension reads back as when it carries ``e`` (exact zeroes everything)."""
    if e["exact"]:
        return {"exact": True, "equal": True, "arbitrary": False, "over": 0.0, "under": 0.0, "spec": ""}
    return dict(e)


def dimensionTolerances(dim):
    """The tolerance properties of a drawing dimension, for comparison with expectedTolerances."""
    return _tolerances_of(dim)


def sameTolerances(have, want):
    w = _expected_view(want)
    return (
        have["exact"] == w["exact"]
        and have["equal"] == w["equal"]
        and have["arbitrary"] == w["arbitrary"]
        and abs(have["over"] - w["over"]) <= 1e-9
        and abs(have["under"] - w["under"]) <= 1e-9
        and have["spec"] == w["spec"]
    )


# ------------------------------------------------------------------------------------------------
# geometry
# ------------------------------------------------------------------------------------------------


def _unit(v):
    return v * (1.0 / v.Length)


def _parallel(a, b, tol=1e-9):
    return a.cross(b).Length < tol


def _global_shape(obj):
    s = obj.Shape.copy()
    try:
        gp = obj.getGlobalPlacement()
        if not gp.isSame(obj.Placement, 1e-12):
            s.Placement = gp
    except Exception:
        pass
    return s


class _Geo:
    """Global-coordinate shapes of the objects an item refers to, cached for one projection."""

    def __init__(self):
        self.shapes = {}

    def shape(self, obj):
        s = self.shapes.get(obj.Name)
        if s is None:
            s = self.shapes[obj.Name] = _global_shape(obj)
        return s

    def element(self, obj, sub):
        try:
            return self.shape(obj).getElement(sub)
        except Exception:
            return None

    def refs(self, pairs):
        """[(obj, sub, shape)] for [(obj, [subs])] or None when something does not resolve."""
        out = []
        for o, subs in pairs:
            if o is None or not hasattr(o, "Shape") or o.Shape.isNull():
                return None
            for s in subs:
                e = self.element(o, s)
                if e is None:
                    return None
                out.append((o, s, e))
        return out

    def name_of(self, obj, sub_shape):
        r = self.shape(obj).findSubShape(sub_shape)
        return None if r is None else "%s%d" % r


def _sides(item):
    """((first pairs), (second pairs)) of a location or angular dimension, [(obj, [sub])] each; None when it has no two sides."""
    doc = item.Document
    sec = set(item.SecondSide)
    if sec:
        first, second = [], []
        for o, subs in item.References:
            for s in subs:
                (second if ("%s.%s" % (o.Name, s)) in sec else first).append((o, [s]))
        return (first, second) if first and second else None
    raw = dict(item.RawAttributes)
    f, s = raw.get("link.first", ""), raw.get("link.second", "")
    sides = []
    for text in (f, s):
        pairs = []
        for n in [x for x in text.split(";") if x]:
            on, _, sub = n.partition(".")
            o = doc.getObject(on)
            if o is not None and sub:
                pairs.append((o, [sub]))
        sides.append(pairs)
    return (sides[0], sides[1]) if sides[0] and sides[1] else None


def _desc_of(shape):
    return PA._desc(shape)


def _plane_sides(geo, pairs):
    """(normal, point, [(obj, sub, face)]) when every element of the side is a face of one plane, else None."""
    refs = geo.refs([(o, s) for o, s in pairs])
    if not refs:
        return None
    n0 = p0 = None
    for o, s, f in refs:
        d = _desc_of(f)
        if d[0] != "plane":
            return None
        if n0 is None:
            n0, p0 = d[2], d[1]
        elif not PA._parallel(n0, d[2]) or abs((d[1] - p0).dot(n0)) > 1e-7 * max(1.0, p0.Length, d[1].Length):
            return None
    return n0, p0, refs


def groupOf(view):
    """The projection group a view belongs to, or None."""
    for o in view.InList:
        if o.TypeId == "TechDraw::DrawProjGroup":
            return o
    return None


SETTLE_TIMEOUT = 120.0  # seconds a GUI session waits for the views' hidden-line removal before giving up (raises)


def _page_views(page):
    out = []
    for o in page.Views:
        if o.TypeId == "TechDraw::DrawProjGroup":
            out.extend(o.Views)
        elif o.isDerivedFrom("TechDraw::DrawViewPart"):
            out.append(o)
    return out


def settle(page, timeout=None):
    """With the GUI up TechDraw runs hidden-line removal in a thread and the views have no geometry until it has finished and
    its result has been taken over; nothing in the Python API says when.  Pump the event loop until every view of the page
    has edges and their count has not changed for a while; raise when that takes longer than ``timeout`` seconds.  Headless
    (FreeCADCmd) the removal is synchronous and this returns at once."""
    if not App.GuiUp:
        return
    import time

    from PySide import QtCore

    views = _page_views(page)
    t0 = time.time()
    limit = SETTLE_TIMEOUT if timeout is None else timeout
    last, stable = None, 0
    while True:
        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(40, loop.quit)
        loop.exec_()
        sig = tuple((len(v.getVisibleEdges(True)), len(v.getHiddenEdges(True))) for v in views)
        full = all(vis > 0 for vis, _ in sig)
        stable = stable + 1 if (sig == last and full) else 0
        last = sig
        if stable >= 8:  # 8 polls of 40 ms with the same edges
            return
        if time.time() - t0 > limit:
            raise RuntimeError("PmiDrawing: the views of %s did not finish their hidden-line removal in %.0f s" % (page.Name, limit))


def page_of(obj):
    for o in obj.InList:
        if o.TypeId == "TechDraw::DrawPage":
            return o
    return None


def _occluder_objects(objs):
    """The objects with a shape among objs and their leaves (a container is looked through)."""
    out, seen = [], set()

    def add(o):
        if o.Name in seen:
            return
        seen.add(o.Name)
        sh = getattr(o, "Shape", None)
        if sh is not None and not sh.isNull() and sh.Faces:
            out.append(o)
        else:
            for c in o.OutList:
                add(c)

    for o in objs:
        add(o)
    return out


def _occluders(objs):
    """The solids (global) that can stand between a point and the viewer: those of the objects, or of their leaves."""
    out, seen = [], set()

    def add(o):
        if o.Name in seen:
            return
        seen.add(o.Name)
        sh = getattr(o, "Shape", None)
        if sh is not None and not sh.isNull() and sh.Faces:
            out.extend(_global_shape(o).Solids)
        else:
            for c in o.OutList:
                add(c)

    for o in objs:
        add(o)
    return out


def _behind_material(solids, p, d):
    """Does the ray from p towards the viewer (direction d) run through the inside of a solid?  The part of the ray the solid
    shares with it is taken, and judged by its middle."""
    for sol in solids:
        bb = sol.BoundBox
        if not bb.intersect(p, d):
            continue
        seg = Part.makeLine(p + d * 1e-8, p + d * (2.0 * bb.DiagonalLength + 1.0))
        try:
            common = sol.common(seg)
        except Exception:
            continue
        for e in common.Edges:
            if e.Length > 1e-5 and sol.isInside(e.valueAt((e.FirstParameter + e.LastParameter) / 2), 1e-7, False):
                return True
    return False


def _inside_point(face, edge, p, delta):
    """A point a little inside the face from p, a point of the face's edge, and a hair off its outward side: the visibility
    ray starts there, so a point on a silhouette or on the junction of two faces is judged by the face it belongs to."""
    try:
        u, v = face.Surface.parameter(p)
        n = face.normalAt(u, v)
        t = edge.tangentAt(edge.Curve.parameter(p))
    except Exception:
        return p
    inward = n.cross(t)
    if inward.Length < 1e-12:
        return p
    inward.normalize()
    for sign in (1.0, -1.0):
        q = p + inward * (sign * delta)
        try:
            if face.isInside(q, 1e-9, True):
                return q + n * (0.1 * delta)
        except Exception:
            return p
    return p


class _View:
    """One TechDraw view with the numbers the projection needs."""

    def __init__(self, view, label, scale_source):
        self.view = view
        self.label = label
        self.scale_source = scale_source
        self.d = _unit(App.Vector(view.Direction))
        self.x = _unit(App.Vector(view.XDirection))
        self.y = _unit(self.d.cross(self.x))
        self._vis = None
        self._center = None
        self._bbox = None

    @property
    def scale(self):
        return float(self.scale_source.Scale)

    def center(self):
        if self._center is None:
            c3 = self.view.getGeometricCenter()
            if App.GuiUp:
                c3 = self._saved_centroid() or c3
            self._center = self.view.projectPoint(c3)
        return self._center

    def _saved_centroid(self):
        """The centre TechDraw centred the edges on.  getGeometricCenter() measures the source shape, and in the GUI that shape
        carries the 3D view's triangulation, which makes its box a little smaller than the geometry-only copy the views are
        centred on.  Rebuilt here from a geometry-only copy of the sources, boxed the way TechDraw boxes it (in the view's frame,
        optimal box)."""
        try:
            g = groupOf(self.view)
            srcs = list((g or self.view).Source)
            shapes = []
            for o in _occluder_objects(srcs):
                sh = _global_shape(o).copy()
                if not sh.isNull():
                    shapes.append(sh)
            if not shapes:
                return None
            comp = Part.makeCompound(shapes)
            m = App.Placement(App.Vector(), App.Rotation(self.x, self.y, self.d, "ZXY"))
            local = comp.copy()
            local.transformShape(m.inverse().toMatrix())
            bb = local.optimalBoundingBox(True, False)
            c = App.Vector((bb.XMin + bb.XMax) / 2, (bb.YMin + bb.YMax) / 2, (bb.ZMin + bb.ZMax) / 2)
            return m.multVec(c)
        except Exception:
            return None

    def u(self, p):
        """Unscaled view coordinates of a model point (the view centre is the origin)."""
        q = self.view.projectPoint(p) - self.center()
        return App.Vector(q.x, q.y, 0)

    def vis(self):
        if self._vis is None:
            self._vis = []
            for e in self.view.getVisibleEdges(True):
                bb = e.BoundBox
                self._vis.append(((bb.XMin, bb.XMax, bb.YMin, bb.YMax), e))
        return self._vis

    def frame(self):
        """(xmin, xmax, ymin, ymax) of the visible edges in unscaled coordinates."""
        if self._bbox is None:
            vis = self.vis()
            if not vis:
                self._bbox = (-1.0, 1.0, -1.0, 1.0)
            else:
                s = self.scale
                self._bbox = (
                    min(b[0][0] for b in vis) / s,
                    max(b[0][1] for b in vis) / s,
                    min(b[0][2] for b in vis) / s,
                    max(b[0][3] for b in vis) / s,
                )
        return self._bbox

    def seen(self, p, occ, face=None, edge=None):
        """Is model point p on a visible edge of the view and in front of all material?  The ray towards the viewer starts a
        little inside the face the edge belongs to."""
        if not self.onVisibleEdge(p):
            return False
        if not occ:
            return True
        delta = 1e-5 * max(sol.BoundBox.DiagonalLength for sol in occ)
        q = _inside_point(face, edge, p, delta) if face is not None else p
        return not _behind_material(occ, q, self.d)

    def onVisibleEdge(self, p, tol=None):
        """Is the projection of model point p on a visible edge of the view? With the GUI up TechDraw edges are centred on the
        centroid saved when the views were computed (see center())."""
        if tol is None:
            tol = ANCHOR_TOL
        s = self.scale
        u = self.u(p)
        px, py = u.x * s, u.y * s
        vert = None
        for bb, e in self.vis():
            if bb[0] - tol <= px <= bb[1] + tol and bb[2] - tol <= py <= bb[3] + tol:
                if vert is None:
                    vert = Part.Vertex(App.Vector(px, py, 0))
                if vert.distToShape(e)[0] <= tol:
                    return True
        return False


def anchorEnd(leader):
    """The end of a leader in unscaled view coordinates: (X, Y) plus the last waypoint delta over the scale (way points are
    page millimetres with y pointing down, as TechDraw draws them; the leader's X, Y are unscaled with y up)."""
    wp = leader.WayPoints
    s = _leader_scale(leader)
    last = wp[-1]
    return App.Vector(leader.X.Value + last.x / s, leader.Y.Value - last.y / s, 0)  # way points are page mm, y down; the position is y up


def _leader_scale(leader):
    v = leader.LeaderParent
    g = groupOf(v)
    for cand in ([g] if g is not None else []) + [v]:
        if cand is not None and hasattr(cand, "Scale"):
            return float(cand.Scale)
    return 1.0


# ------------------------------------------------------------------------------------------------
# the projector
# ------------------------------------------------------------------------------------------------


def semanticItems(doc):
    return [o for o in doc.Objects if o.TypeId in SEMANTIC]


def _dyn(obj, typ, name, doc=""):
    if name not in obj.PropertiesList:
        obj.addProperty(typ, name, "Pmi", doc)


def _tag(obj, item, role):
    _dyn(obj, "App::PropertyLink", "PmiSource", "The PMI item this object was made from")
    _dyn(obj, "App::PropertyString", "PmiRole", "dimension, frame, datum or leader")
    obj.PmiSource = item
    obj.PmiRole = role


def _scope(sources):
    names = set()
    for s in sources:
        names.add(s.Name)
        for o in s.OutListRecursive:
            names.add(o.Name)
    return names


def _edges_of(refs):
    for o, sub, sh in refs:
        if sh.ShapeType == "Face":
            for e in sh.Edges:
                yield o, e, sh
        elif sh.ShapeType == "Edge":
            yield o, sh, None


COARSE = (0.5, 0.25, 0.75)
FINE = tuple(k / 20.0 for k in range(1, 20))  # tried only for an item the coarse samples found nowhere


def _find_anchor(geo, vw, refs, params=COARSE, occ=None):
    """First (obj, edge name, point) on an edge of a governed element whose projection lies on a visible edge of vw."""
    for o, e, f in _edges_of(refs):
        if e.Length < 1e-9:
            continue
        a, b = e.FirstParameter, e.LastParameter
        for t in params:
            try:
                p = e.valueAt(a + t * (b - a))
            except Exception:
                continue
            if vw.seen(p, occ, f, e):
                name = geo.name_of(o, e)
                if name:
                    return o, name, p
    return None


class _Projector:
    def __init__(self, page, sources, view, prescale=False):
        self.prescale = prescale
        self.page = page
        self.doc = page.Document
        self.sources = sources
        self.scope = _scope(sources)
        self.geo = _Geo()
        self.group = None
        self.fixed_view = view
        self.views = []  # [_View] in VIEW_ORDER
        self.all_added = False
        self.stack = {}
        self.datums = {}  # (body name, letter) -> (names, view label) or reason
        self.occ = _occluders(sources)
        self.existing = {}
        for obj, item in itemsOn(page):
            self.existing.setdefault(item.Name, []).append(obj)
            if obj.PmiRole == "datum" and "PmiKey" in obj.PropertiesList:
                body, _, letter = obj.PmiKey.partition("|")
                self.datums[(body, letter)] = ([obj.Name, obj.AnnoParent.Name], self._view_of(obj))
        self._init_views()

    # ---- views
    def _init_views(self):
        if self.fixed_view is not None:
            v = self.fixed_view
            src = groupOf(v) or v
            self.views = [_View(v, v.Type if hasattr(v, "Type") and v.Type in VIEW_ORDER else v.Label, src)]
            self.all_added = True
            return
        for o in self.page.Views:
            if o.TypeId == "TechDraw::DrawProjGroup" and "PmiRole" in o.PropertiesList and o.PmiRole == "view":
                if {s.Name for s in self.sources} <= {s.Name for s in o.Source}:
                    self.group = o
                    break
        if self.group is not None:
            settle(self.page)
            self._collect_views()

    def _ensure_group(self):
        """Make the projection group when the first item needs a view (nothing is made for items already on the page)."""
        if self.group is None and self.fixed_view is None:
            g = self.doc.addObject("TechDraw::DrawProjGroup", "PmiProjGroup")
            self.page.addView(g)
            g.Source = list(self.sources)
            if self.prescale:
                # the group starts at the scale the layout will most likely keep: the hidden-line removal then runs once
                import DrawingLayout

                est = DrawingLayout.estimateScale(self.page, self.sources)
                if est:
                    g.ScaleType = "Custom"
                    g.Scale = est
            g.ProjectionType = "Third angle"
            _dyn(g, "App::PropertyString", "PmiRole", "view")
            g.PmiRole = "view"
            self.group = g
            rot = self.sources[0].getGlobalPlacement().Rotation if hasattr(self.sources[0], "getGlobalPlacement") else App.Rotation()
            for n in VIEW_ORDER[:3]:
                it = g.addProjection(n)
                if n == "Front" and not rot.isSame(App.Rotation(), 1e-12):
                    # the views follow the body's own axes (the other projections are derived from the Front one)
                    it.Direction = rot.multVec(App.Vector(0, -1, 0))
                    it.XDirection = rot.multVec(App.Vector(1, 0, 0))
                self.doc.recompute()
                settle(self.page)
            self._collect_views()

    def _collect_views(self):
        have = {}
        for it in self.group.Views:
            have[it.Type] = it  # the Label gets a numeric suffix when another group in the document has the same view
        self.views = [_View(have[n], n, self.group) for n in VIEW_ORDER if n in have]
        self.all_added = all(n in have for n in VIEW_ORDER)

    def add_remaining_views(self):
        if self.all_added or self.group is None:
            return False
        have = {v.label for v in self.views}
        for n in VIEW_ORDER:
            if n not in have:
                self.group.addProjection(n)
                self.doc.recompute()  # a projection is added to a group that has been recomputed
                settle(self.page)
        self.doc.recompute()
        settle(self.page)
        self._collect_views()
        return True

    # ---- driver
    def run(self, items):
        settle(self.page)
        report = {}
        done = set()
        for item in items:
            if item.Name in report:
                continue
            if item.Name in self.existing:
                report[item.Name] = self._existing_entry(item)
                continue
            if item.TypeId == "Part::PmiDimension":
                report[item.Name] = self._dimension(item)
            elif item.TypeId == "Part::PmiTolerance":
                report[item.Name] = self._tolerance(item)
            elif item.TypeId == "Part::PmiDatum":
                report[item.Name] = self._datum(item, items, report)
            else:
                report[item.Name] = (NOT_PROJECTED, "geometry")
        self.doc.recompute()
        return report

    def _existing_entry(self, item):
        objs = sorted(self.existing[item.Name], key=lambda o: o.PmiRole == "leader")
        return ([o.Name for o in objs], self._view_of(objs[0]))

    def _view_of(self, obj):
        if obj.PmiRole == "dimension":
            v = obj.References2D[0][0]
        elif obj.PmiRole == "leader":
            v = obj.LeaderParent
        else:
            v = obj.AnnoParent.LeaderParent
        return v.Label if v is not None else ""

    # ---- common
    def _governed(self, item):
        """Resolved governed elements [(obj, sub, shape)] or a reason string."""
        if item.Unattached or not len(item.References):
            return "unattached"
        refs = self.geo.refs([(o, list(s)) for o, s in item.References])
        if refs is None:
            return "unattached"
        if any(o.Name not in self.scope for o, _, _ in refs):
            return "no view"
        return refs

    def _anchor_any_view(self, refs):
        """(view, anchor) for the first view that shows an edge of the governed elements; the finer samples are tried only when
        the coarse ones found nothing in any of the six views."""
        for params in (COARSE, FINE):
            for vw in self._views_lazily():
                a = _find_anchor(self.geo, vw, refs, params, self.occ)
                if a:
                    return vw, a
        return None, None

    def _place(self, vw, anchor_u):
        """Where the annotation goes: outside the frame of the view on the side nearest the anchor (ruling 7)."""
        x0, x1, y0, y1 = vw.frame()
        s = vw.scale
        margin, pitch = MARGIN / s, PITCH / s
        dist = {"left": anchor_u.x - x0, "right": x1 - anchor_u.x, "bottom": anchor_u.y - y0, "top": y1 - anchor_u.y}
        side = min(("left", "right", "bottom", "top"), key=lambda k: (dist[k], k))
        n = self.stack.get((vw.label, side), 0)
        self.stack[(vw.label, side)] = n + 1
        if side in ("left", "right"):
            x = x0 - margin if side == "left" else x1 + margin
            y = anchor_u.y + n * pitch
        else:
            y = y0 - margin if side == "bottom" else y1 + margin
            x = anchor_u.x + n * pitch
        return x, y

    def _make_leader(self, item, vw, anchor, label):
        o, edge_name, p = anchor
        u = vw.u(p)
        x, y = self._place(vw, u)
        s = vw.scale
        ld = self.doc.addObject("TechDraw::DrawLeaderLine", "PmiLeader")
        ld.LeaderParent = vw.view  # the parent before the page: with the page open the GUI looks the parent up when the item is made
        self.page.addView(ld)
        ld.Scalable = False
        ld.AutoHorizontal = False
        ld.RotatesWithParent = False
        ld.X = x
        ld.Y = y
        ld.WayPoints = [App.Vector(0, 0, 0), App.Vector((u.x - x) * s, -(u.y - y) * s, 0)]  # way points are page mm, y down
        ld.EndSymbol = "Filled triangle"
        ld.Label = "Leader " + label
        _dyn(ld, "App::PropertyVector", "PmiOffset", "Annotation position minus anchor, unscaled view coordinates")
        ld.PmiOffset = App.Vector(x - u.x, y - u.y, 0)
        _tag(ld, item, "leader")
        _dyn(ld, "App::PropertyVector", "PmiAnchor", "The 3D point the leader ends on")
        _dyn(ld, "App::PropertyString", "PmiAnchorEdge", "The edge of a governed face that point lies on, as Object.EdgeN")
        ld.PmiAnchor = p
        ld.PmiAnchorEdge = "%s.%s" % (o.Name, edge_name)
        return ld

    def _make_anno(self, item, ld, text, role, label, frame):
        an = self.doc.addObject("TechDraw::DrawRichAnno", "PmiAnno")
        an.AnnoParent = ld  # before the page: with the page open the GUI looks the parent up when the item is made
        self.page.addView(an)
        an.AnnoText = text
        an.ShowFrame = frame
        an.Label = label
        _tag(an, item, role)
        return an

    # ---- tolerances and datums
    def _tolerance(self, item):
        refs = self._governed(item)
        if isinstance(refs, str):
            return (NOT_PROJECTED, refs)
        vw, a = self._anchor_any_view(refs)
        if a:
            ld = self._make_leader(item, vw, a, item.Label)
            an = self._make_anno(item, ld, frameHtml(item), "frame", "Frame " + item.Label, False)
            return ([an.Name, ld.Name], vw.label)
        return (NOT_PROJECTED, "no view")

    def _views_lazily(self):
        self._ensure_group()
        first = list(self.views[:3]) if self.group is not None else list(self.views)
        for vw in first:
            yield vw
        if self.group is not None:
            self.add_remaining_views()
            done = {v.label for v in first}
            for vw in list(self.views):
                if vw.label not in done:
                    yield vw

    def _datum(self, item, items, report):
        refs = self._governed(item)
        if isinstance(refs, str):
            return (NOT_PROJECTED, refs)
        key = (refs[0][0].Name, item.Letter)
        if key in self.datums:
            return self.datums[key]
        vw, a = self._anchor_any_view(refs)
        if a:
            ld = self._make_leader(item, vw, a, "Datum " + item.Letter)
            an = self._make_anno(item, ld, datumHtml(item.Letter), "datum", "Datum " + item.Letter, True)
            _dyn(an, "App::PropertyString", "PmiKey", "Body and letter of this datum feature symbol")
            an.PmiKey = "%s|%s" % key
            _dyn(an, "App::PropertyLinkList", "PmiUses", "Every use of this datum letter on this body")
            res = ([an.Name, ld.Name], vw.label)
            self.datums[key] = res
            return res
        return (NOT_PROJECTED, "no view")

    # ---- dimensions
    def _dimension(self, item):
        refs = self._governed(item)
        if isinstance(refs, str):
            return (NOT_PROJECTED, refs)
        plan = self._plan(item)
        if plan is None:
            return (NOT_PROJECTED, "geometry")
        for vw in self._views_lazily():
            for cand in plan(vw):
                typ, ref3d = cand[0], cand[1]
                d = self._try_dimension(item, vw, typ, ref3d, cand[2] if len(cand) > 2 else None)
                if d is not None:
                    return ([d.Name], vw.label)
        return (NOT_PROJECTED, "geometry")

    def _try_dimension(self, item, vw, typ, ref3d, prefix=None):
        d = self.doc.addObject("TechDraw::DrawViewDimension", "PmiDimension")
        try:
            d.Type = typ
            d.References2D = [(vw.view, "")]
            d.References3D = ref3d
            self.page.addView(d)
            d.recompute()
            ok = self._measured_ok(item, d)
        except Exception:
            ok = False
        if not ok:
            self.doc.removeObject(d.Name)
            return None
        if prefix:
            d.FormatSpec = prefix + d.FormatSpec
        _apply_tolerances(d, expectedTolerances(item))
        d.Label = "Dim " + item.Label
        _tag(d, item, "dimension")
        d.recompute()
        return d

    def _measured_ok(self, item, d):
        if "Invalid" in d.State:
            return False
        raw = abs(float(d.getRawValue()))
        if nominalOf(item) is None:
            return raw > 0
        return _close(raw, item.Value)

    def _plan(self, item):
        """A function view -> iterator of (TechDraw type, References3D), or None when the geometry is not covered."""
        t = item.PmiType
        geo = self.geo
        if t in ("Size_Diameter", "Size_Radius"):
            refs = geo.refs([(o, list(s)) for o, s in item.References])
            descs = [_desc_of(f) for _, _, f in refs]
            if any(sh.ShapeType != "Face" for _, _, sh in refs):
                return None
            cyl = PA._cylinder_of(descs)
            if cyl is None:
                return None
            r, _, axis = cyl
            want = 2.0 * r if t == "Size_Diameter" else r
            if nominalOf(item) is not None and not _close(want, item.Value):
                return None
            typ = "Diameter" if t == "Size_Diameter" else "Radius"
            circles, others = [], []
            for o, _, f in refs:
                for e in f.Edges:
                    try:
                        c = e.Curve
                    except Exception:
                        continue
                    if c.TypeId == "Part::GeomLine" or e.Length < 1e-9:
                        continue
                    n = geo.name_of(o, e)
                    if not n:
                        continue
                    if c.TypeId == "Part::GeomCircle":
                        if abs(c.Radius - r) <= 1e-7 * r and _parallel(_unit(c.Axis), axis, 1e-7):
                            circles.append((o, n, e))
                    elif _round_section(e, cyl):
                        others.append((o, n, e))  # the STEP reader hands some circles over as B-spline curves
            circles += others
            # TechDraw's Diameter needs a true circle.  A circle the STEP reader hands over as a B-spline cannot be measured that
            # way; two diametrically opposite vertices of the cylinder's faces (the seams of two half faces) can: a distance
            # in the view along the axis, shown with the diameter sign.
            pairs = _opposite_vertices(geo, refs, cyl) if t == "Size_Diameter" else []
            if not circles and not pairs:
                return None

            def plan(vw):
                if not _parallel(vw.d, axis, 1e-7):
                    return
                ordered = sorted(circles, key=lambda c: 0 if vw.onVisibleEdge(c[2].valueAt(c[2].FirstParameter + 0.5 * (c[2].LastParameter - c[2].FirstParameter))) else 1)
                for o, n, _ in ordered[:3]:
                    yield typ, [(o, n)]
                for o, n1, n2 in pairs[:3]:
                    yield "Distance", [(o, n1), (o, n2)], "\u2300"

            return plan
        if t in ("Size_Thickness", "Location_LinearDistance", "Location_None", "Location_CurvedDistance"):
            if t == "Size_Thickness":
                refs = geo.refs([(o, list(s)) for o, s in item.References])
                if not refs:
                    return None
                groups = []
                for o, s, f in refs:
                    d = _desc_of(f)
                    if d[0] != "plane":
                        return None
                    for g in groups:
                        if PA._coplanar(g[0], d):
                            g[1].append((o, s, f))
                            break
                    else:
                        groups.append((d, [(o, s, f)]))
                if len(groups) != 2 or not PA._parallel(groups[0][0][2], groups[1][0][2]):
                    return None
                a, b = groups[0][0], groups[1][0]
                s1, s2 = groups[0][1], groups[1][1]
            else:
                sides = _sides(item)
                if sides is None:
                    return None
                p1, p2 = _plane_sides(geo, sides[0]), _plane_sides(geo, sides[1])
                if p1 is None or p2 is None or not PA._parallel(p1[0], p2[0]):
                    return None
                a, b = ("plane", p1[1], p1[0]), ("plane", p2[1], p2[0])
                s1, s2 = p1[2], p2[2]
            n = a[2]
            dist = abs((b[1] - a[1]).dot(n))
            if nominalOf(item) is not None and not _close(dist, item.Value):
                return None
            return self._distance_plan(n, s1, s2)
        if t in ("Location_Angular", "Size_Angular"):
            sides = _sides(item)
            if sides is None:
                return None
            p1, p2 = _plane_sides(geo, sides[0]), _plane_sides(geo, sides[1])
            if p1 is None or p2 is None or PA._parallel(p1[0], p2[0]):
                return None
            inter = _unit(p1[0].cross(p2[0]))
            ang = PA._angle_faces([f for _, _, f in p1[2]], [f for _, _, f in p2[2]])
            if nominalOf(item) is not None and not (_close(ang, item.Value) or _close(180.0 - ang, item.Value)):
                return None
            return self._angle_plan(inter, p1[2], p2[2])
        return None

    def _distance_plan(self, n, s1, s2):
        geo = self.geo

        def verts(side):
            out = []
            for o, _, f in side:
                for v in f.Vertexes:
                    name = geo.name_of(o, v)
                    if name:
                        out.append((o, name, v.Point))
                    if len(out) >= 60:
                        return out
            return out

        v1, v2 = verts(s1), verts(s2)

        def plan(vw):
            if abs(n.dot(vw.d)) > 1e-9:
                return
            if abs(abs(n.dot(vw.x)) - 1) < 1e-9:
                typ, lat = "DistanceX", vw.y
            elif abs(abs(n.dot(vw.y)) - 1) < 1e-9:
                typ, lat = "DistanceY", vw.x
            else:
                return
            best = sorted(
                ((abs((q[2] - p[2]).dot(lat)), i, j) for i, p in enumerate(v1) for j, q in enumerate(v2)),
            )[:3]
            for _, i, j in best:
                yield typ, [(v1[i][0], v1[i][1]), (v2[j][0], v2[j][1])]

        return plan

    def _angle_plan(self, inter, s1, s2):
        geo = self.geo

        def lines(side):
            out = []
            for o, _, f in side:
                for e in f.Edges:
                    try:
                        if e.Curve.TypeId != "Part::GeomLine":
                            continue
                    except Exception:
                        continue
                    dvec = e.valueAt(e.LastParameter) - e.valueAt(e.FirstParameter)
                    if dvec.Length < 1e-9 or abs(_unit(dvec).dot(inter)) > 1e-7:
                        continue
                    n = geo.name_of(o, e)
                    if n:
                        out.append((o, n))
                    if len(out) >= 4:
                        return out
            return out

        e1, e2 = lines(s1), lines(s2)

        def plan(vw):
            if not _parallel(vw.d, inter, 1e-7):
                return
            for a in e1:
                for b in e2:
                    yield "Angle", [a, b]
                    yield "Angle", [b, a]

        return plan


def _round_section(edge, cyl):
    """Does the edge run round the cylinder in a plane square to its axis?  (Sampled: all points at the radius from the axis
    and at one height.)"""
    r, c0, axis = cyl
    a, b = edge.FirstParameter, edge.LastParameter
    h = None
    for k in range(0, 9):
        p = edge.valueAt(a + k / 8.0 * (b - a))
        v = p - c0
        hh = v.dot(axis)
        rad = (v - axis * hh).Length
        if abs(rad - r) > 1e-6 * max(r, 1.0):
            return False
        if h is None:
            h = hh
        elif abs(hh - h) > 1e-6 * max(r, 1.0):
            return False
    return True


def _opposite_vertices(geo, refs, cyl):
    """[(obj, vertex name, vertex name)] for pairs of vertices of the faces that lie opposite each other on one section of the cylinder."""
    r, c0, axis = cyl
    verts = []
    for o, _, f in refs:
        for v in f.Vertexes:
            n = geo.name_of(o, v)
            if n:
                verts.append((o, n, v.Point))
    out = []
    for i, (o1, n1, p1) in enumerate(verts):
        for o2, n2, p2 in verts[i + 1:]:
            if o1 is not o2 or n1 == n2:
                continue
            mid = (p1 + p2) * 0.5
            off = mid - c0
            if (off - axis * off.dot(axis)).Length > 1e-6 * max(r, 1.0) or abs((p2 - p1).dot(axis)) > 1e-6 * max(r, 1.0):
                continue
            if abs((p2 - p1).Length - 2.0 * r) <= 1e-6 * r:
                out.append((o1, n1, n2))
    return out


def _close(a, b, rel=NOMINAL_REL):
    return abs(a - b) <= max(rel * max(abs(a), abs(b)), 1e-12)


# ------------------------------------------------------------------------------------------------
# public API
# ------------------------------------------------------------------------------------------------


def project(page, source, items=None, view=None, layout=True):
    """Put PMI on views of ``page``.

    source: the body (or a list of bodies) the views show.  items: PMI items to project, default every dimension, tolerance
    and datum of the document.  view: a DrawViewPart to put everything on; default is a projection group (Front, Top, Right,
    Left, Rear and Bottom only when an item needs them).  layout: lay the page out afterwards (DrawingLayout.layout: scale,
    view positions, annotation positions), in the same undo transaction; False leaves every annotation stacked beside its view.
    Returns {item name: ([drawing object names], view label)} or {item name: ("not projected", reason)} with reason one of
    ``unattached``, ``geometry``, ``no view``."""
    sources = list(source) if isinstance(source, (list, tuple)) else [source]
    doc = page.Document
    if items is None:
        items = semanticItems(doc)
    doc.openTransaction("PMI onto TechDraw views")
    try:
        _remove_orphans(page)
        report = _Projector(page, sources, view, prescale=layout).run(list(items))
        if layout:
            import DrawingLayout

            DrawingLayout.layout(page, _in_transaction=True)
    except Exception:
        doc.abortTransaction()
        raise
    doc.commitTransaction()
    _fill_datum_uses(page, items, report)
    return report


def clear(page, view=None):
    """Remove the PMI drawing objects (dimensions, frames, datum symbols and their leaders) of ``page``, or only those on
    ``view`` (a view or a projection group item; a projection group stands for its members), in one undo transaction.  The
    views stay; afterwards TechDraw's own delete of a view works, because nothing hangs from it.  Returns the names removed."""
    doc = page.Document
    objs = _all_pmi_objects(page)
    if view is not None:
        views = set()
        members = list(view.Views) if view.TypeId == "TechDraw::DrawProjGroup" else []
        views = {view.Name} | {m.Name for m in members}

        def parent(o):
            if o.PmiRole == "dimension":
                r2 = o.References2D
                return r2[0][0] if len(r2) and r2[0][0] is not None else None
            if o.PmiRole == "leader":
                return o.LeaderParent
            return o.AnnoParent.LeaderParent if o.AnnoParent is not None else None

        objs = [o for o in objs if parent(o) is not None and parent(o).Name in views]
    names = [o.Name for o in objs]
    for o in objs:  # a frame or datum symbol takes its leader with it, and a leader its annotation
        if o.PmiRole in ("frame", "datum") and o.AnnoParent is not None and o.AnnoParent.Name not in names:
            names.append(o.AnnoParent.Name)
        if o.PmiRole == "leader":
            for x in o.InList:
                if x.TypeId == "TechDraw::DrawRichAnno" and x.Name not in names:
                    names.append(x.Name)
    leaders = [n for n in names if doc.getObject(n) is not None and doc.getObject(n).PmiRole == "leader"]
    names = [n for n in names if n not in leaders] + leaders  # annotations first, then the leaders they hung from
    doc.openTransaction("PMI clear")
    try:
        for n in names:
            if doc.getObject(n) is not None:
                doc.removeObject(n)
    except Exception:
        doc.abortTransaction()
        raise
    doc.commitTransaction()
    return names


def _fill_datum_uses(page, items, report):
    """Datum symbols are one per letter per body: record every use of the letter on the symbol (PmiUses)."""
    by_anno = {}
    for item in items:
        if item.TypeId != "Part::PmiDatum":
            continue
        res = report.get(item.Name)
        if res and res[0] != NOT_PROJECTED:
            by_anno.setdefault(res[0][0], []).append(item)
    for name, uses in by_anno.items():
        an = page.Document.getObject(name)
        if an is not None and "PmiUses" in an.PropertiesList:
            have = list(an.PmiUses)
            new = [u for u in uses if u not in have]
            if new:
                an.PmiUses = have + new


def itemsOn(page):
    """[(drawing object, PMI item)] for every object on the page made from an item."""
    out = []
    for o in page.Views:
        out.extend(_walk(o))
    return out


def _walk(o):
    res = []
    if "PmiSource" in o.PropertiesList and o.PmiSource is not None:
        res.append((o, o.PmiSource))
    return res


def _all_pmi_objects(page):
    doc = page.Document
    return [o for o in doc.Objects if "PmiSource" in o.PropertiesList and "PmiRole" in o.PropertiesList and o.PmiRole in ("dimension", "frame", "datum", "leader")]


def _orphans(page, objs):
    """Drawing objects whose view is gone: the view was deleted (the links are then empty) or taken off the page.  An
    annotation whose leader is an orphan is one too."""
    alive = {v.Name for v in _page_views(page)}
    out = []
    for o in objs:
        if o.PmiRole == "dimension":
            r2 = o.References2D
            if not len(r2) or r2[0][0] is None or r2[0][0].Name not in alive:
                out.append(o)
        elif o.PmiRole == "leader":
            if o.LeaderParent is None or o.LeaderParent.Name not in alive:
                out.append(o)
        elif o.AnnoParent is None or o.AnnoParent.LeaderParent is None or o.AnnoParent.LeaderParent.Name not in alive:
            out.append(o)
    return out


def _remove_orphans(page):
    doc = page.Document
    objs = _all_pmi_objects(page)
    names = [o.Name for o in _orphans(page, objs)]
    # the leader of an orphaned annotation and the annotation of an orphaned leader go with it
    for o in objs:
        if o.PmiRole in ("frame", "datum") and o.AnnoParent is not None and o.AnnoParent.Name in names and o.Name not in names:
            names.append(o.Name)
    for n in names:
        if doc.getObject(n) is not None:
            doc.removeObject(n)
    return names


def clear(page, view=None):
    """Remove the PMI drawing objects (dimensions, frames, datum symbols and their leaders) of ``page``, or only those on
    ``view`` (a view or a projection group item; a projection group stands for its members), in one undo transaction.  The
    views stay; afterwards TechDraw's own delete of a view works, because nothing hangs from it.  Returns the names removed."""
    doc = page.Document
    objs = _all_pmi_objects(page)
    if view is not None:
        views = set()
        members = list(view.Views) if view.TypeId == "TechDraw::DrawProjGroup" else []
        views = {view.Name} | {m.Name for m in members}

        def parent(o):
            if o.PmiRole == "dimension":
                r2 = o.References2D
                return r2[0][0] if len(r2) and r2[0][0] is not None else None
            if o.PmiRole == "leader":
                return o.LeaderParent
            return o.AnnoParent.LeaderParent if o.AnnoParent is not None else None

        objs = [o for o in objs if parent(o) is not None and parent(o).Name in views]
    names = [o.Name for o in objs]
    for o in objs:  # a frame or datum symbol takes its leader with it, and a leader its annotation
        if o.PmiRole in ("frame", "datum") and o.AnnoParent is not None and o.AnnoParent.Name not in names:
            names.append(o.AnnoParent.Name)
        if o.PmiRole == "leader":
            for x in o.InList:
                if x.TypeId == "TechDraw::DrawRichAnno" and x.Name not in names:
                    names.append(x.Name)
    leaders = [n for n in names if doc.getObject(n) is not None and doc.getObject(n).PmiRole == "leader"]
    names = [n for n in names if n not in leaders] + leaders  # annotations first, then the leaders they hung from
    doc.openTransaction("PMI clear")
    try:
        for n in names:
            if doc.getObject(n) is not None:
                doc.removeObject(n)
    except Exception:
        doc.abortTransaction()
        raise
    doc.commitTransaction()
    return names


def refresh(page):
    """Bring the drawing objects up to date with their items: tolerances of dimensions, frame texts, datum letters; remove
    the objects whose item is gone.  One undo transaction; only properties that differ are written."""
    doc = page.Document
    objs = _all_pmi_objects(page)
    doc.openTransaction("PMI refresh")
    try:
        changed = bool(_remove_orphans(page))
        objs = _all_pmi_objects(page)
        dead = []
        for o in objs:
            if o.PmiSource is None:
                uses = [u for u in o.PmiUses if u is not None] if "PmiUses" in o.PropertiesList else []
                if o.PmiRole == "datum" and uses:
                    o.PmiSource = uses[0]  # the first use is gone, others remain: the symbol stays
                    if o.AnnoParent is not None:
                        o.AnnoParent.PmiSource = uses[0]
                    changed = True
                elif o.PmiRole != "leader":
                    dead.append(o)
        geo = _Geo()
        for o in objs:
            if o in dead or o.PmiRole == "leader" and o.PmiSource is None:
                continue
            item = o.PmiSource
            if o.PmiRole == "leader":
                changed |= _refresh_leader(o, item, geo)
            elif o.PmiRole == "dimension":
                ch, keep = _refresh_dimension(o, item)
                changed |= ch
                if not keep:
                    dead.append(o)
            elif o.PmiRole == "frame":
                text = frameHtml(item)
                if o.AnnoText != text:
                    o.AnnoText = text
                    changed = True
            elif o.PmiRole == "datum":
                text = datumHtml(item.Letter)
                if o.AnnoText != text:
                    o.AnnoText = text
                    changed = True
        # the leader of a dead frame or datum symbol goes with it; so does a leader whose item is gone
        leaders = {o.Name for o in dead if o.PmiRole in ("frame", "datum") and o.AnnoParent is not None}
        for o in dead:
            if o.PmiRole in ("frame", "datum") and o.AnnoParent is not None:
                leaders.add(o.AnnoParent.Name)
        names = [o.Name for o in dead] + [n for n in leaders if n not in {o.Name for o in dead}]
        removed = False
        for o in objs:
            if o.PmiRole == "leader" and o.PmiSource is None and o.Name not in names:
                names.append(o.Name)
        for n in names:
            if doc.getObject(n) is not None:
                doc.removeObject(n)
                removed = True
        if changed or removed:
            doc.recompute()
            settle(page)
    except Exception:
        doc.abortTransaction()
        raise
    doc.commitTransaction()


def _refresh_leader(ld, item, geo):
    """Keep a leader on its anchor: the view centre and scale may have moved, an edge may have been renumbered, and an edit may
    have hidden the anchor, in which case it moves to another point or another view of the group."""
    v = ld.LeaderParent
    if v is None:
        return False
    g = groupOf(v)
    vw = _View(v, v.Type if g is not None else v.Label, g if g is not None else v)
    changed = False
    p = ld.PmiAnchor
    refs = geo.refs([(o, list(s)) for o, s in item.References]) if len(item.References) else None
    if refs:
        src = g.Source if g is not None else v.Source
        occ = _occluders(src)
        vert = Part.Vertex(p)
        found = None
        for o, e, f in _edges_of(refs):
            if e.Length > 1e-9 and vert.distToShape(e)[0] <= 1e-7:
                if vw.seen(p, occ, f, e):
                    found = (o, geo.name_of(o, e))
                break
        if found is None:
            a = None
            for params in (COARSE, FINE):
                a = _find_anchor(geo, vw, refs, params, occ)
                if a:
                    break
            if a is None and g is not None:
                for n in VIEW_ORDER:  # another view of the group, adding the ones the group lacks
                    have = {i.Type: i for i in g.Views}
                    if n not in have:
                        g.addProjection(n)
                        g.Document.recompute()
                        settle(page_of(g))
                        have = {i.Type: i for i in g.Views}
                    if have[n] is v:
                        continue
                    other = _View(have[n], n, g)
                    for params in (COARSE, FINE):
                        a = _find_anchor(geo, other, refs, params, occ)
                        if a:
                            break
                    if a:
                        ld.LeaderParent = have[n]
                        vw = other
                        changed = True
                        break
            if a is not None:
                found = (a[0], a[1])
                p = a[2]
                ld.PmiAnchor = p
                changed = True
        if found is not None and found[1]:
            name = "%s.%s" % (found[0].Name, found[1])
            if ld.PmiAnchorEdge != name:
                ld.PmiAnchorEdge = name
                changed = True
    u = vw.u(p)
    off = ld.PmiOffset
    s = vw.scale
    if "PmiLayout" in ld.PropertiesList:
        st = ld.PmiLayout
        if abs(ld.X.Value - st.x) > 1e-4 or abs(ld.Y.Value - st.y) > 1e-4:
            # the user moved this leader after the last layout (a pin): keep it where it is, whatever the anchor did
            off = App.Vector(ld.X.Value - u.x, ld.Y.Value - u.y, 0)
            if (ld.PmiOffset - off).Length > 1e-9:
                ld.PmiOffset = off
                changed = True
    x, y = u.x + off.x, u.y + off.y
    wp = [App.Vector(0, 0, 0), App.Vector(-off.x * s, off.y * s, 0)]  # page mm, y down
    if abs(ld.X.Value - x) > 1e-9 or abs(ld.Y.Value - y) > 1e-9:
        ld.X, ld.Y = x, y
        if "PmiLayout" in ld.PropertiesList:
            ld.PmiLayout = App.Vector(ld.X.Value, ld.Y.Value, 0)
        changed = True
    cur = ld.WayPoints
    if len(cur) != 2 or (cur[1] - wp[1]).Length > 1e-9:
        ld.WayPoints = wp
        changed = True
    return changed


def _refresh_dimension(dim, item):
    """(changed, keep).  A dimension is measured by TechDraw from the model, so it follows an edit of the geometry by itself
    (and may then differ from a stale nominal of its item, which the authoring rule 'nominal-agrees' reports); refresh only
    brings its tolerance properties up to date."""
    changed = _apply_tolerances(dim, expectedTolerances(item))
    if changed:
        dim.recompute()
    return changed, True
