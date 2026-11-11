# SPDX-License-Identifier: LGPL-2.1-or-later

# **************************************************************************
#   Bend table (G04 slice 3b): dashed bend lines on the view of a sheet     *
#   metal flat pattern, a tag on each, and a stock spreadsheet view listing  *
#   each tag with direction, angle, inside radius, K and allowance, kept up  *
#   to date by one recompute.                                                *
#                                                                            *
#   Public sources of the conventions: vendor help pages of CAD tools on     *
#   sheet metal bend tables on drawings (tag, UP or DOWN direction, bend     *
#   angle, inside radius, sometimes K-factor or allowance; the tag at the    *
#   bend line; behaviour only, nothing copied).  No standard is cited and no *
#   compliance is claimed.                                                   *
# **************************************************************************

"""A bend table on a drawing page that follows the model.

    import BendTable
    view = BendTable.flat_view(page, unfold)     # DrawViewPart of the flat pattern, along its normal, in the DXF frame
    table = BendTable.make(page, view)           # DrawViewSpreadsheet in free space; bend lines and tags on the view
    BendTable.tags_of(table)                     # the DrawViewBalloon tags (B1, B2, ...)
    BendTable.warning_of(table)                  # "" or what the table could not avoid (no bend data, a problem, a long table, an overlap)

The bends come from the SheetMetal ``Unfold`` feature of the view's source (``SheetMetalUnfoldCmd.get_bend_lines``).  Each bend is a
cosmetic line on the view at its projected segments (``lineNumber`` 10, a long-dash pattern, for UP and 2, a short dash, for DOWN)
and a tag (a stock ``TechDraw::DrawViewBalloon``) whose arrow tip is the midpoint of the bend's longest segment.  Rows and tags run
by that midpoint in the view's axes, increasing X then Y.  A Python feature (``BendTableDriver``, a ``HoleTable.HoleTableDriver``
whose items are bends) keeps the sheet, tags and lines, so a plain ``doc.recompute()`` follows an edit of the model.  The table,
placement, tag placer and geometry wait are ``HoleTable``'s.  SheetMetal is imported when needed, so TechDraw loads without it.
"""

import json
import math

import FreeCAD as App
from FreeCAD import Vector

import HoleTable as HT
import PartsList as PL

HEADER = ("TAG", "DIRECTION", "ANGLE", "RADIUS", "K", "ALLOWANCE")
LINE_NUMBER = {"UP": 10, "DOWN": 2}  # ISO 128 line numbers of the cosmetic lines; the GUI scene draws them as different dash arrays
LINE_NUMBERS = tuple(LINE_NUMBER.values())
LONG_TABLE = 30  # more bends than this: all are listed and the table says it is long
ORDER_MM = 0.1  # rows and tags are ordered on midpoints rounded to this
LINE_TOL = 1e-6  # a cosmetic line is the one drawn earlier when its ends lie this close
LINE_WEIGHT = 0.35
LINE_COLOR = (0.0, 0.0, 0.0, 1.0)
DEG = "°"


# ------------------------------------------------------------------------------------------------
# numbers and text
# ------------------------------------------------------------------------------------------------


def angle_text(a):
    """Whole degrees with the degree sign (``90°``), else two decimals (``37.50°``)."""
    r = round(float(a), 2)
    return ("%d" % round(r) if r == round(r) else "%.2f" % r) + DEG


def radius_text(r):
    return "R" + HT.fmt(r)


def k_text(k):
    return "%.2f" % round(float(k), 6)


def row_of(tag, b):
    return [tag, b["dir"], angle_text(b["angle"]), radius_text(b["radius"]), k_text(b["k"]), HT.fmt(b["ba"])]


def _longest(segs):
    return max(segs, key=lambda s: (s[1] - s[0]).Length)


def bends_of(view):
    """-> (bends, problem, has_data): the view's bends in view coordinates, ordered as the table lists them.

    Each bend is a dict with ``dir``, ``angle``, ``radius``, ``k``, ``ba``, ``lines`` ([(p1, p2)] in the view's unscaled coordinates,
    ``projectPoint(P - getGeometricCenter())``) and ``p`` (the midpoint of the longest line).  The order is the midpoint's X then Y
    in the view's axes, both rounded to 0.1 mm.
    """
    import SheetMetalFlatPattern as FP
    import SheetMetalUnfoldCmd as UC

    unfold = next((o for o in view.Source if hasattr(o, "BendData")), None)
    if unfold is None:
        return [], "", False
    text = getattr(unfold, "BendData", "")
    flat, normal, problem = FP.bends_and_problem_from_json(text)
    if normal is None:
        return [], "", False
    gc = view.getGeometricCenter()

    def pt(P):
        q = view.projectPoint(Vector(P) - gc)
        return (float(q.x), float(q.y))

    out = []
    for b in UC.get_bend_lines(unfold):
        lines = [(pt(a), pt(e)) for a, e in b.segments]
        if not lines:
            continue
        a, e = _longest(b.segments)
        m = pt((a + e) * 0.5)
        out.append({"dir": b.direction, "angle": float(b.angle), "radius": float(b.inside_radius), "k": float(b.k_factor), "ba": float(b.allowance), "lines": lines, "p": m})
    out.sort(key=lambda b: (round(b["p"][0] / ORDER_MM), round(b["p"][1] / ORDER_MM), b["p"]))
    return out, problem or "", True


# ------------------------------------------------------------------------------------------------
# cosmetic bend lines
# ------------------------------------------------------------------------------------------------


def _same(l1, l2):
    return all(abs(a - b) < LINE_TOL for a, b in zip(l1[:4], l2[:4])) and l1[4] == l2[4]


def _line_number(ce):
    try:
        return int(ce.Format.get("lineNumber", 1))
    except Exception:
        return 1


def _lines_wanted(bends):
    return [(a[0], a[1], e[0], e[1], LINE_NUMBER[b["dir"]]) for b in bends for a, e in b["lines"]]


def _own_edges(view, drawn):
    """The cosmetic edges of ``view`` that are the bend lines drawn earlier: found by geometry (and line number), in list order.

    ``drawn`` are the lines (x1, y1, x2, y2, lineNumber) the driver remembers.  Tags change on every reopen, so nothing is
    identified by tag.
    """
    left = list(drawn)
    out = []
    for ce in view.CosmeticEdges:
        cur = (ce.Start.x, ce.Start.y, ce.End.x, ce.End.y, _line_number(ce))
        for i, d in enumerate(left):
            if _same(cur, d):
                out.append(ce)
                del left[i]
                break
    return out


def update_lines(view, drawn, wanted):
    """Make the view's bend lines the ``wanted`` ones: leave them when they are, else remove the old and draw the new.  -> True if changed."""
    own = _own_edges(view, drawn)
    now = [(ce.Start.x, ce.Start.y, ce.End.x, ce.End.y, _line_number(ce)) for ce in own]
    if len(now) == len(wanted) == len(drawn) and all(_same(a, b) for a, b in zip(now, wanted)):
        return False
    for ce in own:
        view.removeCosmeticEdge(ce.Tag)
    for x1, y1, x2, y2, ln in wanted:
        view.makeCosmeticLine(Vector(x1, y1, 0), Vector(x2, y2, 0), 1, LINE_WEIGHT, LINE_COLOR, ln)
    return True


# ------------------------------------------------------------------------------------------------
# the driver
# ------------------------------------------------------------------------------------------------


class BendTableDriver(HT.HoleTableDriver):
    """Python feature between a flat pattern's view and its bend table: lists the bends, draws their lines, keeps tags and table."""

    NAME = "BendTable"
    TITLE = "Bend table"
    BLOCK_NAME = "BendTable"
    TAG_NAME = "BendTag"
    COLUMNS = HEADER

    def __init__(self, obj):
        super().__init__(obj)
        obj.addProperty("App::PropertyString", "Lines", "BendTable", "Bend lines drawn on the view: [x1, y1, x2, y2, lineNumber] each")
        obj.addProperty("App::PropertyInteger", "BendCount", "BendTable", "Bends listed")
        obj.setEditorMode("Lines", 2)

    def execute(self, obj):
        view, sheet = obj.View, obj.Sheet
        doc = obj.Document
        page = doc.getObject(obj.PageName) if obj.PageName else None
        if view is None or sheet is None or page is None:
            return
        HT.SETTLE.arm(doc)
        edges = HT.Edges(view)
        HT.SETTLE.poll(doc)
        if not edges.edges or edges.box is None:
            return
        obj.Signature = edges.signature()
        bends, problem, has_data = bends_of(view)
        for i, b in enumerate(bends, start=1):
            b["tag"] = "B%d" % i
            b["size"] = "%s %.2f" % (b["dir"], round(b["angle"], 2))  # tag identity: direction and angle, with the midpoint
        rows = [row_of(b["tag"], b) for b in bends]
        mine = self._match_tags(obj, bends, view, page)
        boxes = self._tables(obj, rows, page, edges)
        self._layout_tags(obj, bends, mine, view, page, edges, boxes)
        notes = []
        if not has_data or (not bends and not problem):
            notes.append("BendTable: no bend data")
        if problem:
            notes.append("BendTable: " + problem)
        if len(bends) > LONG_TABLE:
            notes.append("BendTable: %d bends listed, the table is long" % len(bends))
        if notes:
            for n in notes:
                App.Console.PrintWarning(n + "\n")
            obj.Warning = " | ".join(([obj.Warning] if obj.Warning else []) + notes)
        obj.BendCount = len(bends)
        wanted = _lines_wanted(bends)
        drawn = json.loads(obj.Lines) if obj.Lines else []
        if update_lines(view, drawn, wanted):
            obj.Lines = json.dumps([list(w) for w in wanted])
        elif not obj.Lines and wanted:
            obj.Lines = json.dumps([list(w) for w in wanted])

    def _layout_tags(self, obj, bends, mine, view, page, edges, blocks):
        """Place the tags: each tag's arrow tip is its bend's midpoint, the bubble within 20 mm of it (ruling 58)."""
        S = float(view.Scale)
        vx, vy = HT.r3(view.X.Value), HT.r3(view.Y.Value)
        area, title = PL.page_areas(page)
        names = {t.Name for t in self.tags(obj)} | {b.Name for b in self.blocks(obj)} | {view.Name}
        obs = [(n, tuple(HT.r3(v) for v in b)) for n, b in HT.obstacles(page, skip=names)] + [("bend table", tuple(HT.r3(v) for v in b)) for b in blocks]
        obs += HT.foreign_tags(page, view, names)
        area = tuple(HT.r3(v) for v in area)
        vb = edges.drawn_box
        vcx, vcy = HT.r3(vx + 0.5 * (vb[0] + vb[2]) * S), HT.r3(vy + 0.5 * (vb[1] + vb[3]) * S)
        items = []
        for b in bends:
            t = b["tagobj"]
            c = (HT.r3(vx + b["p"][0] * S), HT.r3(vy + b["p"][1] * S))
            it = {"tag": b["tag"], "c": c, "R": 0.0, "tips": [c]}
            if t.HoleSize and self._moved(t):  # a tag the user moved stays where it is
                it["fixed"] = (HT.r3(vx + HT._num(t.X) * S), HT.r3(vy + HT._num(t.Y) * S - HT.TAG_ANCHOR_MM))
            items.append(it)
        soft = {v.Label for v in page.Views if hasattr(v, "getVisibleEdges")}
        plan, fallbacks, conflicts = HT.plan_tags(items, area, obs, (vcx, vcy), HT.MAX_TAG_DIST, soft, ())
        obj.Violations = fallbacks
        note = ""
        if conflicts:
            note = "BendTable: no clean tag layout found; left over: " + "; ".join(conflicts)
            App.Console.PrintWarning(note + "\n")
        obj.Warning = (obj.Warning + " | " if obj.Warning and note else obj.Warning) + note
        for b, it, (centre, tip) in zip(bends, items, plan):
            t = b["tagobj"]
            tx, ty = b["p"]  # the tip is the bend's midpoint, exactly
            HT.set_if(t, "Text", b["tag"])
            HT.set_if(t, "OriginX", float(tx))
            HT.set_if(t, "OriginY", float(ty))
            t.HoleSize = b["size"]
            t.HoleX, t.HoleY = float(b["p"][0]), float(b["p"][1])
            if "fixed" not in it:
                px, py = round((centre[0] - vx) / S, 4), round((centre[1] + HT.TAG_ANCHOR_MM - vy) / S, 4)
                HT.set_if(t, "X", float(px))
                HT.set_if(t, "Y", float(py))
                HT.set_if(t, HT.PLACED_X, float(px))
                HT.set_if(t, HT.PLACED_Y, float(py))


# ------------------------------------------------------------------------------------------------
# public API
# ------------------------------------------------------------------------------------------------


def drivers_on(page):
    return [
        o
        for o in page.Document.Objects
        if o.TypeId == "App::FeaturePython" and isinstance(getattr(o, "Proxy", None), BendTableDriver) and o.PageName == page.Name
    ]


def driver_of(table):
    return HT.driver_of(table)


def tags_of(table):
    """The bend tags (DrawViewBalloon) of a bend table block, in tag order."""
    d = driver_of(table)
    return sorted(HT.HoleTableDriver.tags(d), key=lambda t: int(t.Text[1:]) if t.Text[1:].isdigit() else 0) if d is not None else []


def blocks_of(table):
    return HT.blocks_of(table)


def warning_of(table):
    return HT.warning_of(table)


def scale_for(box_w, box_h, area):
    """Ruling 32's scale: the largest of the standard scales at which a box fits 60 % of the area's width and 70 % of its height."""
    aw, ah = area[2] - area[0], area[3] - area[1]
    best = None
    for s in (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0):
        if box_w * s <= 0.6 * aw and box_h * s <= 0.7 * ah:
            best = s
    return best or 0.01


def flat_view(page, unfold):
    """Make the view of a flat pattern a bend table reads: along the bend data's normal, x axis the DXF frame's, ruling 32's scale and place.

    -> the ``DrawViewPart`` (``Source = [unfold]``, ``Direction`` the normal, ``XDirection`` ``Frame2D(normal).ex``).
    """
    import SheetMetalFlatPattern as FP

    doc = page.Document
    flat, normal, problem = FP.bends_and_problem_from_json(getattr(unfold, "BendData", ""))
    if normal is None:
        raise ValueError("%s has no V1 bend data" % unfold.Label)
    own = HT.begin(doc, "Flat pattern view")
    try:
        v = doc.addObject("TechDraw::DrawViewPart", "FlatView")
        page.addView(v)
        v.Source = [unfold]
        v.Direction = Vector(normal)
        v.XDirection = FP.Frame2D(normal).ex
        v.ScaleType = "Custom"
        v.Scale = 1.0
        doc.recompute()
        HT.wait_for_geometry(v)
        e = HT.Edges(v)
        area, title = PL.page_areas(page)
        if e.box is not None:
            w, h = e.box[2] - e.box[0], e.box[3] - e.box[1]
            s = scale_for(w, h, area)
            # centred in the left 60 % of the drawing area; a flat that would run into the title block moves left, then down a scale
            while True:
                v.Scale = s
                y = 0.5 * (area[1] + area[3])
                for x in (area[0] + 0.3 * (area[2] - area[0]), area[0] + PL.MARGIN_MM + 0.5 * w * s):
                    box = (x - 0.5 * w * s, y - 0.5 * h * s, x + 0.5 * w * s, y + 0.5 * h * s)
                    if not title or not HT.overlap(box, tuple(title)):
                        break
                if not title or not HT.overlap(box, tuple(title)) or s <= 0.01:
                    break
                s = max(c for c in (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0) if c < s) if s > 0.01 else 0.01
            v.X, v.Y = x, y
        doc.recompute()
        HT.wait_for_geometry(v)
    except Exception:
        HT.fail(doc, own)
        raise
    HT.end(doc, own)
    return v


def make(page, view):
    """Draw the bend lines of the flat pattern ``view`` shows, tag them and put their bend table on ``page``; -> the first table view.

    One undo transaction.  Calling it again for the same view returns the table that is there.  Afterwards a plain
    ``doc.recompute()`` follows edits of the model.
    """
    doc = page.Document
    HT.wait_for_geometry(view)
    own = HT.begin(doc, "Bend table")
    try:
        drv = next((d for d in drivers_on(page) if d.View == view), None)
        if drv is None:
            sheet = doc.addObject("Spreadsheet::Sheet", "BendTableSheet")
            sheet.Label = "Bend table data"
            drv = doc.addObject("App::FeaturePython", "BendTableDriver")
            BendTableDriver(drv)
            drv.Label = "Bend table driver"
            drv.View = view
            drv.Sheet = sheet
            drv.PageName = page.Name
        HT.SETTLE.arm(doc)
        drv.touch()
        doc.recompute()
        if any("Touched" in o.State for o in drv.Proxy.owned(drv)):
            doc.recompute()
        for _ in range(3):  # GUI: the hidden-line result of the redrawn view arrives later; settle once it has
            if not App.GuiUp:
                break
            HT.wait_for_geometry(view)
            if not drv.Proxy.stale(drv):
                break
            drv.touch()
            doc.recompute()
    except Exception:
        HT.fail(doc, own)
        raise
    HT.end(doc, own)
    blocks = BendTableDriver.blocks(drv)
    return blocks[0] if blocks else None
