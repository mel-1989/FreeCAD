# SPDX-License-Identifier: LGPL-2.1-or-later

# **************************************************************************
#   Chain, baseline and ordinate dimension sets (G04 slice 5a): a Python     *
#   driver that lays stock DistanceX / DistanceY dimensions in a chain or a  *
#   baseline stack outside a view, or draws an ordinate set as one symbol,   *
#   and keeps them laid out when the model changes.                          *
#                                                                            *
#   Public sources: TechDraw's own CommandExtensionDims.cpp (chain and       *
#   coordinate dimension commands) and QGIViewDimension.cpp (how a           *
#   dimension is drawn) for the layout vocabulary; DimensionFormatter.cpp   *
#   for the value format; secondary descriptions of ordinate dimensioning    *
#   (values on extension lines, no dimension lines or arrowheads, the base   *
#   marked 0).  The details are house rulings; no compliance is claimed.     *
# **************************************************************************

"""Dimension sets that follow the model.

    import DimChain
    d = DimChain.make(page, view, ["Vertex0", "Vertex6", "Vertex9"], "baseline")          # horizontal, below the view
    d = DimChain.make(page, view, ["Vertex6", "Vertex9", "Vertex12"], "chain", side="above")
    d = DimChain.make(page, view, ["Vertex0", "Vertex6", "Vertex9"], "ordinate", direction="vertical")
    DimChain.add(d, "Vertex12")
    DimChain.remove(d, "Vertex9")

``vertices`` are the view's 0-based ``VertexN`` names; the first is the datum of a baseline or ordinate set.  Chain and baseline
members are stock ``DistanceX`` (horizontal) or ``DistanceY`` (vertical) dimensions; an ordinate set is one ``DrawViewSymbol``.
A ``DimChainDriver`` feature owns them: a plain ``doc.recompute()`` lays them out again against the view's outline.
"""

import math
import re

import FreeCAD as App
from FreeCAD import Vector

import HoleTable as HT

KINDS = ("chain", "baseline", "ordinate")
DIRECTIONS = ("horizontal", "vertical")
SIDES = {"horizontal": ("below", "above"), "vertical": ("left", "right")}

FIRST_MM = 10.0  # the first row or stack line, or the ends of the ordinate extension lines, this far outside the outline
CASCADE_MM = 7.0  # baseline line spacing when the TechDraw preference "CascadeSpacing" is not set (TaskSelectLineAttributes.cpp default)
ORD_GAP_MM = 1.5  # an ordinate extension line starts this far off the feature
ORD_CIRCLE_D = 3.0  # datum mark
ORD_STROKE = 0.25
ORD_JOG_STUB = 2.0  # straight run between the jog and the text
ORD_TEXT_GAP = 0.5  # least gap between two ordinate text boxes
ORD_LINE_H = 1.25  # text box thickness in font sizes
ORD_CHAR_W = 0.62  # text box length per character in font sizes (an upper bound for osifont digits)
# The ISO-oriented stock dimension draws its line (label height / 2 + spacing) from the label position (X, Y): 0.6333 * (font size + 1)
# mm, below the label for a horizontal dimension and to the right of it for a vertical one.  Measured in the GUI SVG at font sizes 5
# and 3.5 mm (run 097); QGIViewDimension.cpp l.1242-1251 is the code that does it.
LINE_OFFSET_SLOPE = 0.63333
STOCK_FONT_MM = 5.0  # TechDraw's own default when the preference is not set (Preferences::DefaultFontSizeInMM)
MIN_SPAN_MM = 1e-4  # chain and baseline make no member for a span shorter than this in model units (ruling 109)
TAG = "DimChainDriver"
PARKED = "DimChainParked"  # a parked member carries this instead of TAG, so nothing else counts it as part of the set
PARAMS = "User parameter:BaseApp/Preferences/Mod/TechDraw/Dimensions"


# ------------------------------------------------------------------------------------------------
# value format (DimensionFormatter::formatValue, FORMATTED, no tolerance)
# ------------------------------------------------------------------------------------------------


def format_value(value, show_units=None, spec=None):
    """The text a stock ``DistanceX``/``DistanceY`` dimension shows for ``value`` (model mm) with default tolerance settings."""
    grp = App.ParamGet(PARAMS)
    if spec is None:
        spec = grp.GetString("formatSpec", "%.2w")
        if not spec:
            if grp.GetBool("UseGlobalDecimals", True):
                dec = App.ParamGet("User parameter:BaseApp/Preferences/Units").GetInt("Decimals", 2)
            else:
                dec = grp.GetInt("AltDecimals", 2)
            spec = "%%.%df" % dec
    if show_units is None:
        show_units = grp.GetBool("ShowUnits", False)
    q = App.Units.Quantity(float(value), "mm")
    basic, factor, unit = q.getUserPreferred()
    m = re.search(r"%[+-]?[0-9]*\.*[0-9]*[aefgrwAEFGRW]", spec)
    if m is None:
        return spec
    prefix, suffix, conv = spec[: m.start()], spec[m.end() :], m.group(0)
    userval = float(value) / (factor or 1.0)
    kind = conv[-1].lower()
    if kind == "w":
        text = ("%" + conv[1:-1] + "f") % userval
        if "." in text:
            text = text.rstrip("0")
            if text.endswith("."):
                text = text[:-1]
    elif kind == "r":
        rounder = float(conv[1:-1] or 0)
        rv = round(userval / rounder) * rounder if rounder else userval
        dec = conv[1:-1]
        nd = len(dec.rstrip("0")) - dec.index(".") - 1 if "." in dec else 0
        text = "%.*f" % (nd, rv)
    else:
        if kind == "g" and grp.GetBool("UseGlobalDecimals", True):
            conv = "%%.%df" % App.ParamGet("User parameter:BaseApp/Preferences/Units").GetInt("Decimals", 2)
        text = conv % userval
    unit_text = (" " + (unit or "mm")) if show_units else ""
    return prefix + text + unit_text + suffix


def font_size():
    return App.ParamGet(PARAMS).GetFloat("FontSize", 3.5) or 3.5


def line_offset():
    """How far a stock dimension's line lies from the label position it is given (mm)."""
    return LINE_OFFSET_SLOPE * (App.ParamGet(PARAMS).GetFloat("FontSize", STOCK_FONT_MM) + 1.0)


def cascade_spacing():
    return App.ParamGet(PARAMS).GetFloat("CascadeSpacing", CASCADE_MM) or CASCADE_MM


# ------------------------------------------------------------------------------------------------
# keeping objects settled, and deleting a driver's objects with it
# ------------------------------------------------------------------------------------------------


def reattach(obj):
    """Give a driver feature whose Proxy was lost (a stable FreeCAD saved it without the module) its driver back."""
    if getattr(obj, "Proxy", None) is None:
        obj.Proxy = DimChainDriver.__new__(DimChainDriver)
        SETTLE.arm(obj.Document)


def _is_driver(obj):
    return obj.TypeId == "App::FeaturePython" and isinstance(getattr(obj, "Proxy", None), DimChainDriver)


class _Settle:
    """Document observer: a recompute ends with the driver's dimensions recomputed and untouched; a deleted driver takes its objects."""

    def __init__(self):
        self.docs = set()
        self.timers = {}
        self.ticks = {}
        self.removing = False
        self.pages = set()  # pages whose views changed in a sync and need a recompute of their own
        self.sync = set()  # drivers whose members need a look after the recompute (a vanished vertex, a zero span, a vertex without member)

    def arm(self, doc):
        self.docs.add(doc.Uid)

    def slotDeletedObject(self, obj):
        # called while the object is leaving the document, inside the caller's transaction: its members go in the same undo step
        try:
            doc = obj.Document
            if doc.Transacting:  # an undo or redo puts its own objects back or takes them away
                return
            owner = getattr(obj, TAG, "")
            if owner and not self.removing:  # a member or symbol the user deleted: the set lays itself out again at the next recompute
                drv = doc.getObject(owner)
                if drv is not None and _is_driver(drv):
                    self._drop_uncovered(drv, obj)
                    drv.touch()
                return
            if obj.TypeId != "App::FeaturePython" or not isinstance(getattr(obj, "Proxy", None), DimChainDriver):
                return
            name = obj.Name
        except Exception:
            return
        if self.removing:
            return
        self.removing = True
        try:
            for o in list(doc.Objects):
                if name in (getattr(o, TAG, None), getattr(o, PARKED, None)):
                    try:
                        doc.removeObject(o.Name)
                    except Exception:
                        pass
        finally:
            self.removing = False

    @staticmethod
    def _drop_uncovered(drv, gone):
        """A deleted member takes with it each vertex of its pair that no other member uses (the set no longer holds it)."""
        try:
            if drv.Kind == "ordinate" or gone.TypeId != "TechDraw::DrawViewDimension":
                return
            pr = DimChainDriver.pair(gone)
            if not pr:
                return
            covered = set()
            for m in DimChainDriver.members(drv):
                if m.Name != gone.Name:
                    covered.update(DimChainDriver.pair(m) or ())
            cur = list(drv.Vertices)
            datum = cur[0] if drv.Kind == "baseline" and cur else None
            new = [v for v in cur if not (v in pr and v not in covered and v != datum)]
            if new != cur:
                drv.Vertices = new
        except Exception:
            pass

    def slotRecomputedDocument(self, doc):
        if doc.Uid not in self.docs:
            return
        for drv in [o for o in doc.Objects if _is_driver(o)]:
            if drv.Name in self.sync:
                self.sync.discard(drv.Name)
                try:
                    self.removing = True
                    try:
                        changed = _sync(drv)
                    finally:
                        self.removing = False
                    if changed:
                        drv.touch()
                        drv.recompute()
                        if "Touched" in drv.State:
                            drv.purgeTouched()
                        self.pages.add(drv.PageName)
                except Exception as e:
                    App.Console.PrintWarning("Dimension set %s: could not tidy its dimensions (%s)\n" % (drv.Label, e))
        for drv in [o for o in doc.Objects if _is_driver(o)]:
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
        for name in list(self.pages):
            self.pages.discard(name)
            pg = doc.getObject(name)
            if pg is not None and "Touched" in pg.State:
                try:
                    pg.recompute()
                except Exception:
                    pass
                if "Touched" in pg.State:
                    try:
                        pg.purgeTouched()
                    except Exception:
                        pass

    def poll(self, doc):
        """GUI only: a view's edges arrive from a worker thread after the recompute; lay the sets out again once they differ."""
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
                if _is_driver(o) and o.Proxy.stale(o):
                    o.touch()
                    again = True
            if again:
                d.recompute()

        t.timeout.connect(tick)
        t.start()


SETTLE = _Settle()
App.addDocumentObserver(SETTLE)


# ------------------------------------------------------------------------------------------------
# geometry
# ------------------------------------------------------------------------------------------------


def _idx(name):
    m = re.fullmatch(r"Vertex(\d+)", str(name))
    if not m:
        raise ValueError("%r is not a vertex name (VertexN, 0-based)" % (name,))
    return int(m.group(1))


def vertex_point(view, name):
    """-> (x, y) of the vertex in the view's frame (unscaled model mm, y up); ValueError when the view has no such vertex."""
    i = _idx(name)
    try:
        v = view.getVertexByIndex(i)
    except Exception:
        raise ValueError("%s is not a vertex of %s" % (name, view.Name))
    return (v.X, v.Y)


def _axis(direction):
    return 0 if direction == "horizontal" else 1


class Frame:
    """Everything a layout reads from the view: scale, position, outline on the page, and each vertex."""

    def __init__(self, view, names):
        self.S = float(view.Scale)
        self.vx, self.vy = view.X.Value, view.Y.Value
        edges = HT.Edges(view)
        self.ok = edges.box is not None
        b = edges.box or [0, 0, 0, 0]
        self.outline = (self.vx + b[0] * self.S, self.vy + b[1] * self.S, self.vx + b[2] * self.S, self.vy + b[3] * self.S)
        self.sig = edges.signature()
        self.pts = {}
        for n in names:
            try:
                self.pts[n] = vertex_point(view, n)
            except ValueError:
                pass

    def page(self, n):
        x, y = self.pts[n]
        return (self.vx + x * self.S, self.vy + y * self.S)


def _outside(frame, direction, side):
    """-> (coordinate of the outline edge on the outside, sign pointing away from the part) for the set's side."""
    x0, y0, x1, y1 = frame.outline
    if direction == "horizontal":
        return (y0, -1.0) if side == "below" else (y1, 1.0)
    return (x0, -1.0) if side == "left" else (x1, 1.0)


# ------------------------------------------------------------------------------------------------
# the driver
# ------------------------------------------------------------------------------------------------


class DimChainDriver:
    def __init__(self, obj):
        obj.Proxy = self
        add = obj.addProperty
        add("App::PropertyLink", "View", "DimChain", "The view whose vertices are dimensioned")
        add("App::PropertyString", "PageName", "DimChain", "Name of the page")
        add("App::PropertyString", "Kind", "DimChain", "chain, baseline or ordinate")
        add("App::PropertyString", "Direction", "DimChain", "horizontal or vertical")
        add("App::PropertyString", "Side", "DimChain", "below or above (horizontal), left or right (vertical)")
        add("App::PropertyStringList", "Vertices", "DimChain", "Vertex names; the first is the datum of a baseline or ordinate set")
        add("App::PropertyString", "Signature", "DimChain", "Edges the set was last laid out against")
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
    def members(obj, parked=False):
        """The member dimensions on the page; ``parked=True``: the ones taken off the page (a vertex gone or a zero span) that wait to return.

        A parked member is never deleted: deleting an object the undo stack knows opens an undo step of its own ("Delete") outside the
        model edit's, so one undo of the edit would no longer restore the set (check 125, GUI).
        """
        key = PARKED if parked else TAG
        return sorted(
            (o for o in obj.Document.Objects if o.TypeId == "TechDraw::DrawViewDimension" and getattr(o, key, "") == obj.Name),
            key=lambda o: o.Name,
        )

    @staticmethod
    def symbols(obj):
        return sorted((o for o in obj.Document.Objects if o.TypeId == "TechDraw::DrawViewSymbol" and getattr(o, TAG, "") == obj.Name), key=lambda o: o.Name)

    def owned(self, obj):
        return self.members(obj) + self.members(obj, parked=True) + self.symbols(obj)  # a parked member left the tag, not the set: tidy it too

    @staticmethod
    def _dropped(obj):
        if "Dropped" not in obj.PropertiesList:
            return []
        out = []
        for e in obj.Dropped:
            n, _, i = e.partition("@")
            out.append((n, int(i) if i.isdigit() else 0))
        return out

    @staticmethod
    def _set_dropped(obj, entries):
        if "Dropped" not in obj.PropertiesList:
            if not entries:
                return
            obj.addProperty("App::PropertyStringList", "Dropped", "DimChain", "Vertices that left the view, with their place, so an undo of the model edit brings them back")
            obj.setEditorMode("Dropped", 2)
        new = ["%s@%d" % e for e in entries]
        if list(obj.Dropped) != new:
            obj.Dropped = new

    def stale(self, obj):
        v = obj.View
        if v is None:
            return False
        if "Touched" in v.State:
            return True
        return HT.Edges(v).signature() != obj.Signature

    # -- the work ---------------------------------------------------------------------------------

    @staticmethod
    def pair(dim):
        subs = []
        for _, s in dim.References2D:
            subs += [s] if isinstance(s, str) else list(s)
        return tuple(subs[:2]) if len(subs) >= 2 else None

    def execute(self, obj):
        view = obj.View
        doc = obj.Document
        page = doc.getObject(obj.PageName) if obj.PageName else None
        if view is None or page is None:
            return
        SETTLE.arm(doc)
        SETTLE.poll(doc)
        direction, side, kind = obj.Direction, obj.Side, obj.Kind
        mem = self.members(obj)
        names = set(obj.Vertices)
        dropped = self._dropped(obj)
        names.update(n for n, _ in dropped)
        for m in mem:
            p = self.pair(m)
            if p:
                names.update(p)
        frame = Frame(view, names)
        if not frame.ok:
            return
        # R14 (ruling 108): a vertex that is no longer in the view
        cur = list(obj.Vertices)
        # a vertex dropped earlier that is in the view again (an undo of the model edit, or an edit that brings the feature back)
        # returns at its old place; the Dropped list is what makes the drop undoable even when it happened outside the edit's transaction
        back = [(n, i) for n, i in dropped if n in frame.pts and n not in cur]
        for n, i in sorted(back, key=lambda ni: ni[1]):
            cur.insert(min(i, len(cur)), n)
        if kind != "chain" and cur and cur[0] not in frame.pts:
            raise RuntimeError("datum %s of %s is no longer in %s; pick a new datum or delete the set" % (cur[0], obj.Label, view.Label))
        lost = [n for n in cur if n not in frame.pts]
        for n in lost:
            App.Console.PrintWarning("Dimension set %s: vertex %s is no longer in %s and was dropped from the set\n" % (obj.Label, n, view.Label))
        keep = [(n, i) for n, i in dropped if n not in frame.pts]
        keep += [(n, cur.index(n)) for n in lost]
        self._set_dropped(obj, keep)
        base = [n for n in cur if n in frame.pts]
        obj.Signature = frame.sig
        if kind == "ordinate":
            if base != list(obj.Vertices):
                obj.Vertices = base
            self._ordinate(obj, view, page, frame)
            return
        a = _axis(direction)
        edge, sign = _outside(frame, direction, side)
        # a member the user deleted has left the set; a member whose vertex is gone or whose span is under 1e-4 mm is not drawn (the
        # observer removes it after the recompute); a vertex without member stays in the set, so the member returns when it moves apart
        allm = [(m, self.pair(m)) for m in mem]
        live = [(m, p) for m, p in allm if p and p[0] in frame.pts and p[1] in frame.pts]
        live = [(m, p) for m, p in live if abs(frame.pts[p[1]][a] - frame.pts[p[0]][a]) >= MIN_SPAN_MM]
        covered = set()
        for _, p in live:
            covered.update(p)
        todo = len(live) != len(allm) or any(n not in covered for n in base) or bool(self.members(obj, parked=True))
        if kind == "chain":  # a member that spans a vertex of the set (a bridge made while that vertex was away) is replaced
            order = sorted(base, key=lambda n: (frame.pts[n][a], _idx(n)))
            adj = {frozenset(x) for x in zip(order[:-1], order[1:])}
            todo = todo or any(frozenset(p) not in adj for _, p in live)
        if kind == "baseline":
            datum = base[0] if base else None
            span = lambda n: abs(frame.pts[n][a] - frame.pts[datum][a])  # noqa: E731
            order = sorted(live, key=lambda mp: (abs(frame.pts[mp[1][1]][a] - frame.pts[mp[1][0]][a]), mp[1][1]))
            sp = cascade_spacing()
            for k, (m, p) in enumerate(order):
                line = edge + sign * (FIRST_MM + k * sp)
                self._place(m, frame, a, line, 0.5 * (frame.page(p[0])[a] + frame.page(p[1])[a]))
            rest = sorted(set(base[1:]) | {p[1] for _, p in order}, key=lambda n: (span(n), n))
            new = ([datum] if datum else []) + rest
        else:
            line = edge + sign * FIRST_MM
            for m, p in live:
                self._place(m, frame, a, line, 0.5 * (frame.page(p[0])[a] + frame.page(p[1])[a]))
            new = sorted(set(base) | covered, key=lambda n: (frame.pts[n][a], _idx(n)))
        if new and new != list(obj.Vertices):
            obj.Vertices = new
        if todo:
            SETTLE.sync.add(obj.Name)

    def _place(self, dim, frame, a, line, along):
        """Put the label of ``dim`` on the line ``line`` (page coordinate on the other axis) at ``along`` (page, on the measuring axis)."""
        off = line_offset()
        if a == 0:
            x, y = along - frame.vx, line - frame.vy + off
        else:
            x, y = line - frame.vx - off, along - frame.vy
        HT.set_if(dim, "X", float(x), 1e-6)
        HT.set_if(dim, "Y", float(y), 1e-6)

    # -- ordinate ---------------------------------------------------------------------------------

    def _ordinate(self, obj, view, page, frame):
        syms = self.symbols(obj)
        if syms:
            sym = syms[0]
        else:
            sym = new_symbol(obj, page)
        svg, cx, cy = ordinate_svg(frame, list(obj.Vertices), obj.Direction, obj.Side)
        if sym.Symbol != svg:
            sym.Symbol = svg
        HT.set_if(sym, "X", float(cx), 1e-6)
        HT.set_if(sym, "Y", float(cy), 1e-6)


def _has_member(drv, a, b):
    return any(set(DimChainDriver.pair(m) or ()) == {a, b} for m in DimChainDriver.members(drv))


def _park(m, drv, page):
    page.removeView(m)
    if PARKED not in m.PropertiesList:
        m.addProperty("App::PropertyString", PARKED, "DimChain", "Driver that will take this dimension back when its vertices return")
        m.setEditorMode(PARKED, 1)
    setattr(m, PARKED, drv.Name)
    setattr(m, TAG, "")


def _ensure_member(drv, page, view, p, q):
    """Put the member for (p, q) on the page: a parked one when there is one, else a new one."""
    for m in DimChainDriver.members(drv, parked=True):
        if set(DimChainDriver.pair(m) or ()) == {p, q}:
            page.addView(m)
            setattr(m, TAG, drv.Name)
            setattr(m, PARKED, "")
            m.touch()
            return m
    return new_member(drv, page, view, p, q, drv.Direction)


def _sync(drv):
    """Bring a chain or baseline's members in line with its vertices after a recompute (R14, R15); -> True when it changed any.

    Takes a member off the page ("parks" it) when its vertex vanished from the view or its span is under MIN_SPAN_MM, or when it spans
    another vertex of a chain; puts a parked member back, or makes one, when a vertex without member is owed one.  Nothing is deleted.
    """
    view, doc = drv.View, drv.Document
    page = doc.getObject(drv.PageName)
    if view is None or page is None or drv.Kind == "ordinate":
        return False
    a = _axis(drv.Direction)
    names = set(drv.Vertices)
    for m in DimChainDriver.members(drv) + DimChainDriver.members(drv, parked=True):
        names.update(DimChainDriver.pair(m) or ())
    frame = Frame(view, names)
    if not frame.ok:
        return False
    pts = frame.pts

    def span(p, q):
        return abs(pts[q][a] - pts[p][a])

    def ok(p):
        return p and p[0] in pts and p[1] in pts and span(*p) >= MIN_SPAN_MM

    base = [n for n in drv.Vertices if n in pts]
    order = sorted(base, key=lambda n: (pts[n][a], _idx(n)))
    adj = {frozenset(x) for x in zip(order[:-1], order[1:])}
    changed, gone = False, set()
    for m in DimChainDriver.members(drv):
        p = DimChainDriver.pair(m)
        if p and (not ok(p) or (drv.Kind == "chain" and frozenset(p) not in adj)):
            gone.update(p)
            _park(m, drv, page)
            changed = True
    have = set()
    for m in DimChainDriver.members(drv):
        have.update(DimChainDriver.pair(m) or ())
    if drv.Kind == "baseline":
        for n in base[1:]:
            if not _has_member(drv, base[0], n) and span(base[0], n) >= MIN_SPAN_MM:
                _ensure_member(drv, page, view, base[0], n)
                changed = True
    else:
        touched = gone | {n for n in base if n not in have}
        for p, q in zip(order[:-1], order[1:]):
            if (p in touched or q in touched) and span(p, q) >= MIN_SPAN_MM and not _has_member(drv, p, q):
                _ensure_member(drv, page, view, p, q)
                changed = True
    return changed


def new_symbol(drv, page):
    doc = drv.Document
    s = doc.addObject("TechDraw::DrawViewSymbol", "OrdinateSet")
    page.addView(s)
    s.addProperty("App::PropertyString", TAG, "DimChain", "Name of the driver that keeps this symbol")
    setattr(s, TAG, drv.Name)
    s.setEditorMode(TAG, 1)
    s.ScaleType = "Custom"
    s.Scale = 1.0
    return s


def new_member(drv, page, view, a, b, direction):
    doc = drv.Document
    d = doc.addObject("TechDraw::DrawViewDimension", "ChainDim" if drv.Kind == "chain" else "BaseDim")
    d.Type = "DistanceX" if direction == "horizontal" else "DistanceY"
    d.References2D = [(view, a), (view, b)]
    page.addView(d)
    d.addProperty("App::PropertyString", TAG, "DimChain", "Name of the driver that keeps this dimension")
    setattr(d, TAG, drv.Name)
    d.setEditorMode(TAG, 1)
    return d


# ------------------------------------------------------------------------------------------------
# the ordinate symbol
# ------------------------------------------------------------------------------------------------


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def ordinate_entries(frame, names, direction, side):
    """-> the set's entries in axis order: dicts with the vertex, page point, value text and the text anchor with its jog."""
    a = _axis(direction)
    fs = font_size()
    edge, sign = _outside(frame, direction, side)
    datum = names[0]
    d0 = frame.pts[datum][a]
    rows = []
    for k, n in enumerate(names):
        if n not in frame.pts:
            continue
        v = abs(frame.pts[n][a] - d0)
        rows.append({"name": n, "k": k, "p": frame.page(n), "val": v, "text": "0" if n == datum else format_value(v)})
    rows.sort(key=lambda r: (r["p"][a], r["k"]))
    pitch = ORD_LINE_H * fs + ORD_TEXT_GAP
    prev = None
    end = edge + sign * FIRST_MM
    for r in rows:
        pos = r["p"][a]
        t = pos if prev is None else max(pos, prev + pitch)
        r["jog"] = t - pos
        r["t"] = t
        r["end"] = end
        prev = t
    return rows, fs, sign


def ordinate_dxf_entries(driver):
    """-> (axis, [entry]) for the DXF writer: each entry has the feature point, the datum point, the leader end and the text (page mm).

    The leader end lies at the entry's text position (the extension line's end, shifted along the axis by the jog).
    """
    view = driver.View
    names = list(driver.Vertices)
    frame = Frame(view, names)
    if not frame.ok or not names or names[0] not in frame.pts:
        return 0, []
    rows, fs, sign = ordinate_entries(frame, names, driver.Direction, driver.Side)
    a = _axis(driver.Direction)
    out = []
    for r in rows:
        px, py = r["p"]
        end = (r["t"], r["end"]) if a == 0 else (r["end"], r["t"])
        out.append({"name": r["name"], "feature": (px, py), "datum": frame.page(names[0]), "end": end, "text": r["text"], "is_datum": r["name"] == names[0]})
    return a, out


def ordinate_svg(frame, names, direction, side):
    """-> (svg, centre x, centre y): the set drawn in page mm, its centre on the page (the symbol's X and Y)."""
    rows, fs, sign = ordinate_entries(frame, names, direction, side)
    a = _axis(direction)
    ink = []  # (x0, y0, x1, y1) in page mm of everything drawn
    parts = []  # shapes with page coordinates, converted below

    for r in rows:
        px, py = r["p"]
        e, t, j = r["end"], r["t"], r["jog"]
        datum = r["name"] == names[0]
        if a == 0:
            # extension line vertical, texts rotated
            y_start = py + sign * ORD_GAP_MM
            if j > 1e-9:
                y_j = e - sign * (ORD_JOG_STUB + j)
                pts = [(px, y_start), (px, y_j), (px + j, e - sign * ORD_JOG_STUB), (px + j, e)]
            else:
                pts = [(px, y_start), (px, e)]
            parts.append(("poly", pts))
            if datum:  # ruling 99: the open circle is centred on the line's end, the 0 stands beyond it
                parts.append(("circle", t, e, 0.5 * ORD_CIRCLE_D))
            parts.append(("text", t, e + (sign * 0.5 * ORD_CIRCLE_D if datum else 0.0), r["text"], sign))
        else:
            x_start = px + sign * ORD_GAP_MM
            if j > 1e-9:
                x_j = e - sign * (ORD_JOG_STUB + j)
                pts = [(x_start, py), (x_j, py), (e - sign * ORD_JOG_STUB, py + j), (e, py + j)]
            else:
                pts = [(x_start, py), (e, py)]
            parts.append(("poly", pts))
            if datum:
                parts.append(("circle", e, t, 0.5 * ORD_CIRCLE_D))
            parts.append(("text", e + (sign * 0.5 * ORD_CIRCLE_D if datum else 0.0), t, r["text"], sign))
    # extent
    xs, ys = [], []
    for p in parts:
        if p[0] == "poly":
            for x, y in p[1]:
                xs.append(x)
                ys.append(y)
        elif p[0] == "circle":
            xs += [p[1] - p[3], p[1] + p[3]]
            ys += [p[2] - p[3], p[2] + p[3]]
        else:
            _, x, y, text, sg = p
            ln = ORD_CHAR_W * fs * len(text)
            if a == 0:
                xs += [x - ORD_LINE_H * fs, x + ORD_LINE_H * fs]
                ys += [y, y + sg * ln]
            else:
                xs += [x, x + sg * ln]
                ys += [y - ORD_LINE_H * fs, y + ORD_LINE_H * fs]
    m = 1.0
    left, right, bottom, top = min(xs) - m, max(xs) + m, min(ys) - m, max(ys) + m
    # QGraphicsSvgItem fits the drawing into the renderer's integer default size: a whole number of svg units keeps the scale at 1
    W = math.ceil((right - left) * 10.0 - 1e-9) / 10.0
    H = math.ceil((top - bottom) * 10.0 - 1e-9) / 10.0
    right, bottom = left + W, top - H

    K = 10.0  # one svg unit is 0.1 mm (QGIViewSymbol draws a unit-less svg with scene units, which are 0.1 mm, as pixels)

    def u(x):
        return round((x - left) * K, 3)

    def v(y):
        return round((top - y) * K, 3)

    sw = round(ORD_STROKE * K, 3)
    fsu = round(fs * K, 3)
    shift = 0.35 * fs  # Qt's svg renderer ignores dy: put the baseline this far off the line so the glyphs are centred on it
    out = []
    for p in parts:
        if p[0] == "poly":
            d = " ".join("%s%s,%s" % ("M" if i == 0 else "L", u(x), v(y)) for i, (x, y) in enumerate(p[1]))
            out.append('<path d="%s" fill="none" stroke="black" stroke-width="%s"/>' % (d, sw))
        elif p[0] == "circle":
            out.append(
                '<circle cx="%s" cy="%s" r="%s" fill="none" stroke="black" stroke-width="%s"/>' % (u(p[1]), v(p[2]), round(p[3] * K, 3), sw)
            )
        else:
            _, x, y, text, sg = p
            anchor = "end" if sg < 0 else "start"
            if a == 0:
                # written along the line, reading from the right side of the sheet (bottom to top): rotate -90 about the anchor;
                # the value ends at the line when the set is below the part, starts at it when above; dy centres the glyphs on the line
                out.append(
                    '<text x="%s" y="%s" transform="rotate(-90 %s %s)" text-anchor="%s" font-family="osifont" font-size="%s" fill="black">%s</text>'
                    % (u(x) + round(shift * K, 3), v(y), u(x) + round(shift * K, 3), v(y), anchor, fsu, _esc(text))
                )
            else:
                out.append(
                    '<text x="%s" y="%s" text-anchor="%s" font-family="osifont" font-size="%s" fill="black">%s</text>'
                    % (u(x), v(y) + round(shift * K, 3), anchor, fsu, _esc(text))
                )
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="%s" height="%s" viewBox="0 0 %s %s">%s</svg>' % (
        round(W * K, 3),
        round(H * K, 3),
        round(W * K, 3),
        round(H * K, 3),
        "".join(out),
    )
    return svg, 0.5 * (left + right), 0.5 * (bottom + top)


# ------------------------------------------------------------------------------------------------
# the API
# ------------------------------------------------------------------------------------------------


def _check(view, vertices, kind, direction, side):
    if kind not in KINDS:
        raise ValueError("unknown kind %r (use one of %s)" % (kind, ", ".join(KINDS)))
    if direction not in DIRECTIONS:
        raise ValueError("unknown direction %r (use horizontal or vertical)" % (direction,))
    if side is not None and side not in SIDES[direction]:
        raise ValueError("unknown side %r for a %s set (use %s)" % (side, direction, " or ".join(SIDES[direction])))
    if view is None or not hasattr(view, "getVertexByIndex"):
        raise ValueError("view %r is not a view with vertices" % (view,))
    rot = view.Rotation
    if abs(float(getattr(rot, "Value", rot))) > 1e-9:
        raise ValueError("view %s is rotated (Rotation %s); only unrotated views are supported" % (view.Name, rot))
    names = [str(v) for v in vertices]
    if len(names) < 2:
        raise ValueError("a set needs at least 2 vertices, got %d" % len(names))
    if len(set(names)) != len(names):
        dup = next(n for n in names if names.count(n) > 1)
        raise ValueError("duplicate vertex %s" % dup)
    for n in names:
        vertex_point(view, n)
    return names


def make(page, view, vertices, kind, direction="horizontal", side=None):
    """Create a dimension set on ``view`` and lay it out; -> the driver.  One undo step.  ValueError (nothing created) on bad input."""
    names = _check(view, vertices, kind, direction, side)
    if side is None:
        side = SIDES[direction][0]
    if view not in page.Views:
        raise ValueError("view %s is not on page %s" % (view.Name, page.Name))
    doc = page.Document
    HT.wait_for_geometry(view)
    own = HT.begin(doc, "Dimension set")
    try:
        drv = doc.addObject("App::FeaturePython", "DimChainDriver")
        DimChainDriver(drv)
        drv.Label = "%s dimensions" % kind.capitalize()
        drv.View = view
        drv.PageName = page.Name
        drv.Kind, drv.Direction, drv.Side = kind, direction, side
        drv.Vertices = names
        if kind == "baseline":
            a = _axis(direction)
            for n in names[1:]:
                if _long_enough(view, names[0], n, a):
                    new_member(drv, page, view, names[0], n, direction)
        elif kind == "chain":
            a = _axis(direction)
            for p, q in _chain_pairs(view, names, a):
                if _long_enough(view, p, q, a):
                    new_member(drv, page, view, p, q, direction)
        else:
            new_symbol(drv, page)
        SETTLE.arm(doc)
        drv.touch()
        doc.recompute()
        if any("Touched" in o.State for o in drv.Proxy.owned(drv)):
            doc.recompute()
    except Exception:
        HT.fail(doc, own)
        raise
    HT.end(doc, own)
    return drv


def _long_enough(view, p, q, a):
    """Ruling 109: a span under MIN_SPAN_MM gets no member."""
    return abs(vertex_point(view, q)[a] - vertex_point(view, p)[a]) >= MIN_SPAN_MM


def _chain_pairs(view, names, a):
    order = sorted(names, key=lambda n: (vertex_point(view, n)[a], _idx(n)))
    return list(zip(order[:-1], order[1:]))


def _members_by_pair(drv):
    return {frozenset(DimChainDriver.pair(m)): m for m in DimChainDriver.members(drv) if DimChainDriver.pair(m)}


def add(driver, vertex):
    """Add a vertex to the set (a baseline or ordinate entry, or a chain split at it); ValueError on a duplicate or unknown vertex."""
    view, doc = driver.View, driver.Document
    page = doc.getObject(driver.PageName)
    vertex = str(vertex)
    vertex_point(view, vertex)
    cur = list(driver.Vertices)
    kind, direction = driver.Kind, driver.Direction
    a = _axis(direction)
    if kind == "chain":
        have = set(cur)
        for m in DimChainDriver.members(driver):
            have.update(DimChainDriver.pair(m) or ())
        cur = sorted(have, key=lambda n: (vertex_point(view, n)[a], _idx(n)))
    if vertex in cur:
        raise ValueError("vertex %s is already in the set" % vertex)
    own = HT.begin(doc, "Add to dimension set")
    try:
        if kind == "baseline":
            if _long_enough(view, cur[0], vertex, a):
                new_member(driver, page, view, cur[0], vertex, direction)
            driver.Vertices = cur + [vertex]
        elif kind == "ordinate":
            driver.Vertices = cur + [vertex]
        else:
            key = lambda n: (vertex_point(view, n)[a], _idx(n))  # noqa: E731
            order = sorted(cur + [vertex], key=key)
            i = order.index(vertex)
            by = _members_by_pair(driver)
            lo = order[i - 1] if i > 0 else None
            hi = order[i + 1] if i + 1 < len(order) else None
            if lo is not None and hi is not None and frozenset((lo, hi)) in by:
                doc.removeObject(by[frozenset((lo, hi))].Name)
                for p, q in ((lo, vertex), (vertex, hi)):
                    if _long_enough(view, p, q, a):
                        new_member(driver, page, view, p, q, direction)
            elif lo is not None:
                if _long_enough(view, lo, vertex, a):
                    new_member(driver, page, view, lo, vertex, direction)
            elif hi is not None:
                if _long_enough(view, vertex, hi, a):
                    new_member(driver, page, view, vertex, hi, direction)
            driver.Vertices = order
        driver.touch()
        doc.recompute()
        if any("Touched" in o.State for o in driver.Proxy.owned(driver)):
            doc.recompute()
    except Exception:
        HT.fail(doc, own)
        raise
    HT.end(doc, own)
    return driver


def remove(driver, vertex):
    """Take a vertex out of the set (a chain closes the gap); ValueError when it is not in the set or is the datum."""
    view, doc = driver.View, driver.Document
    page = doc.getObject(driver.PageName)
    vertex = str(vertex)
    kind, direction = driver.Kind, driver.Direction
    a = _axis(direction)
    cur = list(driver.Vertices)
    if kind == "chain":
        have = set(cur)
        for m in DimChainDriver.members(driver):
            have.update(DimChainDriver.pair(m) or ())
        cur = sorted(have, key=lambda n: (vertex_point(view, n)[a], _idx(n)))
    if vertex not in cur:
        raise ValueError("vertex %s is not in the set" % vertex)
    if kind in ("baseline", "ordinate") and vertex == cur[0]:
        raise ValueError("vertex %s is the datum and cannot be removed" % vertex)
    if len(cur) <= 2:
        raise ValueError("a set keeps at least 2 vertices")
    own = HT.begin(doc, "Remove from dimension set")
    try:
        if kind == "ordinate":
            driver.Vertices = [n for n in cur if n != vertex]
        elif kind == "baseline":
            for m in DimChainDriver.members(driver):
                if vertex in (DimChainDriver.pair(m) or ()):
                    doc.removeObject(m.Name)
            driver.Vertices = [n for n in cur if n != vertex]
        else:
            by = _members_by_pair(driver)
            i = cur.index(vertex)
            lo = cur[i - 1] if i > 0 else None
            hi = cur[i + 1] if i + 1 < len(cur) else None
            both = lo is not None and hi is not None and frozenset((lo, vertex)) in by and frozenset((vertex, hi)) in by
            for k, m in list(by.items()):
                if vertex in k:
                    doc.removeObject(m.Name)
            if both and _long_enough(view, lo, hi, a):
                new_member(driver, page, view, lo, hi, direction)
            driver.Vertices = [n for n in cur if n != vertex]
        driver.touch()
        doc.recompute()
        if any("Touched" in o.State for o in driver.Proxy.owned(driver)):
            doc.recompute()
    except Exception:
        HT.fail(doc, own)
        raise
    HT.end(doc, own)
    return driver


def set_side(driver, side):
    """Move the set to the other side of the view (below or above, left or right) and lay it out again; one undo step.

    ValueError when ``side`` is not a side of the set's direction.
    """
    doc = driver.Document
    if side not in SIDES[driver.Direction]:
        raise ValueError("unknown side %r for a %s set (use %s)" % (side, driver.Direction, " or ".join(SIDES[driver.Direction])))
    if driver.Side == side:
        return driver
    own = HT.begin(doc, "Dimension set side")
    try:
        driver.Side = side
        driver.touch()
        doc.recompute()
        if any("Touched" in o.State for o in driver.Proxy.owned(driver)):
            doc.recompute()
    except Exception:
        HT.fail(doc, own)
        raise
    HT.end(doc, own)
    return driver


def set_datum(driver, vertex):
    """Make ``vertex`` (a ``"VertexN"`` string of the set's view) the datum of an ordinate or baseline set; one undo step; -> the driver.

    The old datum may be live (the origin moves; the old datum stays in the set as an ordinary vertex) or dead (a model edit removed
    it, see the R14 sentence; it leaves the set).  The result equals ``make`` of the same kind on the same view with ``vertex`` first.
    ``ValueError``, nothing changed and no undo step: a chain has no datum, ``vertex`` is not a vertex of the view, or fewer than 2
    vertices would remain.  ``vertex`` already the datum is a no-op.
    """
    doc, view = driver.Document, driver.View
    vertex = str(vertex)
    if driver.Kind == "chain":
        raise ValueError("a chain has no datum")
    vertex_point(view, vertex)
    cur = list(driver.Vertices)
    old = cur[0] if cur else None
    if vertex == old:
        return driver
    alive = old is not None and old in Frame(view, cur + [vertex]).pts
    rest = [n for n in cur[1:] if n != vertex]
    new = [vertex] + ([old] if alive else []) + rest
    if len(new) < 2:
        raise ValueError("a set keeps at least 2 vertices")
    own = HT.begin(doc, "Set datum")
    try:
        if driver.Kind == "baseline":
            page = doc.getObject(driver.PageName)
            for m in DimChainDriver.members(driver):  # the members measure from the datum: point each one at the new datum, in place
                pair = DimChainDriver.pair(m)
                if not pair or pair[0] != old:
                    continue
                if pair[1] != vertex:
                    m.References2D = [(view, vertex), (view, pair[1])]
                elif alive:  # the member that joined the old and the new datum now runs from the new datum to the old one
                    m.References2D = [(view, vertex), (view, old)]
                else:  # its far end was the dead datum: nothing is left to measure
                    _park(m, driver, page)
        driver.Vertices = new
        driver.touch()
        doc.recompute()
        if any("Touched" in o.State for o in driver.Proxy.owned(driver)):
            doc.recompute()
    except Exception:
        HT.fail(doc, own)
        raise
    HT.end(doc, own)
    return driver


def driver_of(obj):
    """-> the dimension set driver that ``obj`` is, or that owns ``obj`` (a member dimension or the ordinate symbol); None otherwise."""
    if obj is None:
        return None
    if _is_driver(obj):
        return obj
    owner = getattr(obj, TAG, "") if hasattr(obj, "PropertiesList") else ""
    if owner:
        drv = obj.Document.getObject(owner)
        if drv is not None and _is_driver(drv):
            return drv
    return None


def drivers_on(page):
    return [o for o in page.Document.Objects if _is_driver(o) and o.PageName == page.Name]


def members_of(driver):
    return DimChainDriver.members(driver)


def symbol_of(driver):
    s = DimChainDriver.symbols(driver)
    return s[0] if s else None
