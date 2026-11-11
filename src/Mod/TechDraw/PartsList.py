# SPDX-License-Identifier: LGPL-2.1-or-later

# **************************************************************************
#   Associative parts list (G04 slice 2a): the assembly's stock bill of      *
#   materials, drawn on a TechDraw page as a stock spreadsheet view.         *
#                                                                            *
#   Public sources of the conventions: ISO 7573:2008 (parts lists: item,     *
#   description, quantity), ISO 6433:2012 (part references).  Only the       *
#   column vocabulary is used; no text of either standard is copied.         *
# **************************************************************************

"""A parts list on a drawing page that follows the assembly.

    import PartsList
    view = PartsList.make(page, assembly)           # DrawViewSpreadsheet in the corner above the title block
    PartsList.refresh(page)                         # force a regeneration (never needed after a plain doc.recompute())

Rows come from the assembly's ``Assembly::BomObject`` (found, or created with the columns ``Index``, ``Name``,
``Quantity`` plus any ``.Property`` column asked for); the table is a stock ``TechDraw::DrawViewSpreadsheet`` of that sheet.
One helper object, ``PartsListFit`` (a Python feature), sits between them in the dependency graph.  After the BOM has been
regenerated it fits the column widths to the widest text of each column (font metrics of the bundled osifont), counts the
rows for the view's ``CellEnd`` and computes the view's position so the table keeps its bottom right corner above the title
block, stacked above any parts list made earlier on the same page; the view reads all three through expressions.  A plain ``doc.recompute()`` is therefore enough after a component is
added, removed or relabelled or a configuration is activated.  To move the table, clear the expressions on ``X`` and ``Y``.
"""

import math
import os
import re

import FreeCAD as App

DEFAULT_COLUMNS = ("Index", "Name", "Quantity")
PX_MM = 25.4 / 96.0  # one unit of a DrawViewSpreadsheet symbol in page millimetres (96 dpi SVG units)
TEXT_PX = 14.0  # TextSize of the view, in symbol units (3.7 mm; a digit's ink is then 2.8 mm, above the 2.5 mm floor)
PAD_LEFT_PX = 6.0  # the view starts every cell's text this far from the cell's left edge
PAD_RIGHT_PX = 6.0
ROW_PX = 22  # row height in symbol units (5.8 mm); the view puts the baseline 7.5 units above the cell bottom
MIN_COL_PX = 24
MARGIN_MM = 5.0  # between the table and the frame, and between the table and the title block
NO_FRAME_INSET_MM = 10.0  # drawing area of a template whose frame cannot be found: the page less this on every side
GAP_MM = 3.0  # between two parts lists stacked on one page
FIT_SLACK = 1.04  # fontTools advances against the renderer's shaping
FALLBACK_ADVANCE = 0.55  # em, for a glyph the font lacks

_FONTS = {}


# ------------------------------------------------------------------------------------------------
# font metrics
# ------------------------------------------------------------------------------------------------


def _font():
    f = _FONTS.get("osifont")
    if f is None:
        try:
            from fontTools.ttLib import TTFont

            tt = TTFont(os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Resources", "fonts", "osifont-lgpl3fe.ttf"))
            f = (tt.getBestCmap(), tt["hmtx"], float(tt["head"].unitsPerEm))
        except Exception:
            f = False
        _FONTS["osifont"] = f
    return f or None


def text_width_px(text, size_px=TEXT_PX):
    """Width of ``text`` in osifont at ``size_px``, in symbol units."""
    f = _font()
    total = 0.0
    for ch in text:
        g = f[0].get(ord(ch)) if f else None
        total += (f[1][g][0] / f[2]) if g is not None else FALLBACK_ADVANCE
    return total * size_px


# ------------------------------------------------------------------------------------------------
# the page's drawing area
# ------------------------------------------------------------------------------------------------


def _rects(svg):
    out = []
    for tag in re.findall(r"<rect\b[^>]*>", svg):
        attrs = dict(re.findall(r'([\w:-]+)\s*=\s*"([^"]*)"', tag))
        try:
            out.append(
                (
                    attrs.get("id", "") + " " + attrs.get("class", "") + " " + attrs.get("inkscape:label", ""),
                    float(attrs.get("x", 0)),
                    float(attrs.get("y", 0)),
                    float(attrs["width"]),
                    float(attrs["height"]),
                )
            )
        except (KeyError, ValueError):
            continue
    return out


# -- the title block's text, read from the template ---------------------------------------------

TITLE_PAD_MM = 2.0  # text boxes closer than this belong to one title-block cluster
MARKER_BAND_MM = 8.0  # template text this close to the drawing area's right edge (zone letters between a double frame) pulls the edge in
NOMINAL_EDIT_CHARS = 6  # an editable field is empty in the template: give it the width of this many characters
ASCENT, DESCENT = 1.0, 0.25  # of the font size, above and below a text's baseline


def _mat(t):
    """The 2D affine matrix (a, b, c, d, e, f) of an SVG ``transform`` attribute (translate, scale, rotate, matrix)."""
    m = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", t or ""):
        v = [float(x) for x in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", args)]
        if name == "matrix" and len(v) == 6:
            n = tuple(v)
        elif name == "translate" and v:
            n = (1.0, 0.0, 0.0, 1.0, v[0], v[1] if len(v) > 1 else 0.0)
        elif name == "scale" and v:
            n = (v[0], 0.0, 0.0, v[1] if len(v) > 1 else v[0], 0.0, 0.0)
        elif name == "rotate" and v:
            c, s_ = math.cos(math.radians(v[0])), math.sin(math.radians(v[0]))
            n = (c, s_, -s_, c, 0.0, 0.0)
            if len(v) == 3:
                n = _mul((1.0, 0.0, 0.0, 1.0, v[1], v[2]), _mul(n, (1.0, 0.0, 0.0, 1.0, -v[1], -v[2])))
        else:
            continue
        m = _mul(m, n)
    return m


def _mul(m, n):
    return (
        m[0] * n[0] + m[2] * n[1],
        m[1] * n[0] + m[3] * n[1],
        m[0] * n[2] + m[2] * n[3],
        m[1] * n[2] + m[3] * n[3],
        m[0] * n[4] + m[2] * n[5] + m[4],
        m[1] * n[4] + m[3] * n[5] + m[5],
    )


def _first_num(v):
    m = re.search(r"[-+]?(?:\d+\.?\d*|\.\d+)", v or "")
    return float(m.group()) if m else None


def _style(el):
    d = dict(re.findall(r"([\w-]+)\s*:\s*([^;]+)", el.get("style", "")))
    d.update({k: el.get(k) for k in ("font-size", "text-anchor") if el.get(k) is not None})
    return d


def template_texts(svg):
    """-> [(box, editable name or None)]: every ``<text>`` of the template as a box (x0, y0, x1, y1) in SVG user units, y down.

    Transforms of the element and its ancestors are applied, the font size and anchor are inherited, and the width is the text's
    width in osifont (an empty editable field counts ``NOMINAL_EDIT_CHARS`` characters).
    """
    import xml.etree.ElementTree as ET

    out = []
    try:
        root = ET.fromstring(svg.encode("utf-8"))
    except ET.ParseError:
        return out

    def local(tag):
        return tag.rsplit("}", 1)[-1]

    def walk(el, m, inherited):
        st = dict(inherited)
        st.update(_style(el))
        m = _mul(m, _mat(el.get("transform")))
        if local(el.tag) == "text":
            tspans = [c for c in el.iter() if local(c.tag) == "tspan"]
            for c in tspans:  # a size on the tspan (Inkscape puts it there)
                if "font-size" in _style(c):
                    st["font-size"] = _style(c)["font-size"]
                    break
            size = _first_num(st.get("font-size")) or 3.5
            text = "".join(el.itertext()).strip()
            name = next((v for k, v in el.attrib.items() if local(k) == "editable"), None)
            x = _first_num(el.get("x"))
            y = _first_num(el.get("y"))
            for c in tspans:
                x = _first_num(c.get("x")) if x is None else x
                y = _first_num(c.get("y")) if y is None else y
            if x is None or y is None:
                return
            w = text_width_px(text, size) if text else NOMINAL_EDIT_CHARS * 0.55 * size
            if name is not None and text:
                w = max(w, NOMINAL_EDIT_CHARS * 0.55 * size)
            anchor = (st.get("text-anchor") or "start").strip()
            x0 = x - (w / 2 if anchor == "middle" else w if anchor == "end" else 0.0)
            pts = [(x0, y - ASCENT * size), (x0 + w, y - ASCENT * size), (x0, y + DESCENT * size), (x0 + w, y + DESCENT * size)]
            pts = [(m[0] * px + m[2] * py + m[4], m[1] * px + m[3] * py + m[5]) for px, py in pts]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            out.append(((min(xs), min(ys), max(xs), max(ys)), name))
            return
        for c in el:
            walk(c, m, st)

    walk(root, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), {})
    return out


def _near(a, b, pad):
    return a[0] - pad < b[2] and b[0] - pad < a[2] and a[1] - pad < b[3] and b[1] - pad < a[3]


def clip_box(t, area):
    """``t`` = (box, name) with the box cut to ``area`` (an empty editable field has only a nominal width)."""
    b = t[0]
    return ((max(b[0], area[0]), max(b[1], area[1]), min(b[2], area[2]), min(b[3], area[3])), t[1])


def inset_for_markers(texts, area, title):
    """``area`` with its right edge pulled in past template text (zone letters between a double frame) that sits within ``MARKER_BAND_MM``
    of it and outside the title block.  Only the right edge: tables are anchored to it, and the other edges stay what the frame says."""
    x1 = area[2]
    for b, _ in (clip_box(t, area) for t in texts):
        if (title and _near(title, b, 0.0)) or b[2] <= b[0] or b[3] <= b[1]:
            continue
        if b[2] > area[2] - MARKER_BAND_MM and b[0] > area[2] - MARKER_BAND_MM:
            x1 = min(x1, b[0] - 1.0)
    return (area[0], area[1], x1, area[3])


def title_cluster(texts, area, seed, W, H):
    """The box (page mm, y up) around the title block's text: the editable fields in the page's bottom right quarter, grown over every
    text box inside ``area`` closer than ``TITLE_PAD_MM`` to the cluster; ``seed`` is a box the template names as its title block.
    ``texts`` are boxes in page mm, y up, with the editable name or None."""
    inside = [clip_box(t, area) for t in texts if area[0] - 1 <= (t[0][0] + t[0][2]) / 2 <= area[2] + 1 and area[1] - 1 <= (t[0][1] + t[0][3]) / 2 <= area[3] + 1]
    box = tuple(seed) if seed else None
    for b, name in inside:
        if name is not None and (b[0] + b[2]) / 2 > 0.4 * W and (b[1] + b[3]) / 2 < 0.4 * H:
            box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
    if box is None:
        return None
    changed = True
    while changed:
        changed = False
        for b, _ in inside:
            if _near(box, b, TITLE_PAD_MM) and not (b[0] >= box[0] and b[1] >= box[1] and b[2] <= box[2] and b[3] <= box[3]):
                box = (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
                changed = True
    return box


def page_areas(page):
    """-> (drawing area, title block or None), boxes (x0, y0, x1, y1) in page millimetres, y up.

    Read from the template's rectangles: the drawing space frame, and the title block frame, grown to cover every text element and
    editable field of the title block (``title_cluster``).  A template that names none
    (the blank default, ASME ANSIA, ``A4_Landscape_TD``) has the page inset by ``NO_FRAME_INSET_MM`` on every side as its drawing
    area and no title block.
    """
    t = page.Template
    W, H = float(t.Width), float(t.Height)
    inset = NO_FRAME_INSET_MM
    area, title = (inset, inset, W - inset, H - inset), None
    try:
        with open(t.Template, encoding="utf-8", errors="replace") as fh:
            svg = fh.read()
        m = re.search(r'viewBox="\s*([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)', svg)
        vb = [float(x) for x in m.groups()] if m else [0.0, 0.0, W, H]
        sx, sy = W / vb[2], H / vb[3]

        def box(r):
            _, x, y, w, h = r
            return ((x - vb[0]) * sx, H - (y - vb[1] + h) * sy, (x - vb[0] + w) * sx, H - (y - vb[1]) * sy)

        rects = _rects(svg)
        for pat in (r"drawing[_ -]?(space|area)", r"\bframe\b", r"frame|border"):
            hit = [r for r in rects if re.search(pat, r[0], re.I) and not re.search(r"title|grid|legal", r[0], re.I)]
            if hit:
                area = box(max(hit, key=lambda r: r[3] * r[4]))
                break
        hit = [r for r in rects if re.search(r"title[_ -]?block[_ -]?(frame|border)|title[_ -]?block", r[0], re.I)]
        if hit:
            title = box(max(hit, key=lambda r: r[3] * r[4]))
        # the true title block is whatever the template writes there: its rectangle, every text element and editable field beside it
        # (the ISO minimal template keeps three fields above its rectangle; ANSI names no rectangle at all)
        def page_box(b):
            return (
                (b[0] - vb[0]) * sx,
                H - (b[3] - vb[1]) * sy,
                (b[2] - vb[0]) * sx,
                H - (b[1] - vb[1]) * sy,
            )

        texts = [(page_box(b), n) for b, n in template_texts(svg)]
        grown = title_cluster(texts, area, title, W, H)
        if grown is not None:
            # the cells' own rectangles that the text cluster touches (ANSI draws its title block as unnamed rectangles)
            cells = [box(r) for r in rects if r[3] * r[4] * sx * sy < 0.25 * (area[2] - area[0]) * (area[3] - area[1])]
            cells = [c for c in cells if c[0] >= area[0] - 1 and c[2] <= area[2] + 1 and c[1] >= area[1] - 1 and c[3] <= area[3] + 1]
            for c in cells:
                if _near(grown, c, 0.0):
                    grown = (min(grown[0], c[0]), min(grown[1], c[1]), max(grown[2], c[2]), max(grown[3], c[3]))
            title = grown
        area = inset_for_markers(texts, area, title)
    except Exception:
        pass
    return area, title


def anchor(area, title, width_mm):
    """-> (right, bottom) of the table: its bottom right corner in the drawing area, clear of the title block."""
    right = area[2] - MARGIN_MM
    bottom = area[1] + MARGIN_MM
    if title:
        left = right - width_mm
        if title[0] < right and title[2] > left:  # the table's columns reach over the title block
            bottom = max(bottom, title[3] + MARGIN_MM)
    return right, bottom


# ------------------------------------------------------------------------------------------------
# the BOM
# ------------------------------------------------------------------------------------------------


def _wanted_columns(columns):
    cols = list(DEFAULT_COLUMNS)
    for c in columns or ():
        if c not in cols:
            cols.append(c)
    return cols


def find_bom(assembly):
    for g in assembly.Group:
        if g.TypeId == "Assembly::BomGroup":
            for o in g.Group:
                if o.TypeId == "Assembly::BomObject":
                    return o
    return None


def _bom(assembly, columns):
    """The assembly's BomObject, created when there is none, with at least the wanted columns."""
    bom = find_bom(assembly)
    wanted = _wanted_columns(columns)
    if bom is None:
        group = None
        for g in assembly.Group:
            if g.TypeId == "Assembly::BomGroup":
                group = g
        if group is None:
            group = assembly.newObject("Assembly::BomGroup", "Bom")
        bom = group.newObject("Assembly::BomObject", "BOM")
        bom.Label = "Parts list"
        bom.columnsNames = wanted
    else:
        have = list(bom.columnsNames)
        if any(c not in have for c in wanted):
            bom.columnsNames = wanted + [c for c in have if c not in wanted]
    return bom


def _letter(i):
    return chr(ord("A") + i)


def _cell_text(sheet, address):
    try:
        v = sheet.get(address)
    except Exception:
        return ""
    return v if isinstance(v, str) else (("%g" % v) if isinstance(v, float) else str(v))


def read_table(sheet):
    """-> list of rows of cell texts, header first: up to the first row whose first cell is empty, over the header's columns."""
    ncols = 0
    while ncols < 26 and sheet.getContents("%s1" % _letter(ncols)):
        ncols += 1
    rows, r = [], 1
    while ncols and r < 2000 and sheet.getContents("A%d" % r):
        rows.append([_cell_text(sheet, "%s%d" % (_letter(c), r)) for c in range(ncols)])
        r += 1
    return rows


# ------------------------------------------------------------------------------------------------
# the fit helper
# ------------------------------------------------------------------------------------------------


class PartsListFit:
    """Python feature between the BOM and the table view: fits widths, counts rows, places the table."""

    def __init__(self, obj):
        obj.Proxy = self
        add = obj.addProperty
        add("App::PropertyLinkGlobal", "Source", "PartsList", "The bill of materials sheet")
        add("App::PropertyString", "CellEnd", "PartsList", "Last cell of the table (read by the view)")
        add("App::PropertyInteger", "Rows", "PartsList", "Rows of the table, header included")
        add("App::PropertyInteger", "Columns", "PartsList", "Columns of the table")
        add("App::PropertyLength", "Width", "PartsList", "Width of the drawn table")
        add("App::PropertyLength", "Height", "PartsList", "Height of the drawn table")
        add("App::PropertyLength", "Right", "PartsList", "Right edge of the table on the page")
        add("App::PropertyLength", "Bottom", "PartsList", "Bottom edge of the table on the page")
        add("App::PropertyFloatList", "Area", "PartsList", "Drawing area of the page: x0, y0, x1, y1 in mm")
        add("App::PropertyFloatList", "TitleBlock", "PartsList", "Title block: x0, y0, x1, y1 in mm, empty when none")
        add("App::PropertyLength", "CenterX", "PartsList", "Centre of the drawn table, page x")
        add("App::PropertyLength", "CenterY", "PartsList", "Centre of the drawn table, page y")
        add("App::PropertyLinkList", "Below", "PartsList", "Parts lists made earlier on the same page: this one stacks above them")
        add("App::PropertyString", "PageName", "PartsList", "Name of the page; its template is read again on every run")
        add("App::PropertyString", "ViewName", "PartsList", "Name of the table view this helper feeds")
        add("App::PropertyString", "Signature", "PartsList", "Template and stack this helper last ran against")
        add("App::PropertyLinkGlobal", "Template", "PartsList", "Template object of the page (an edit of it recomputes the table)")
        obj.setEditorMode("Source", 1)

    # FreeCAD restores the proxy by class, it needs no state
    def dumps(self):
        return None

    def loads(self, state):
        return None

    def onDocumentRestored(self, obj):
        obj.Proxy = self

    @staticmethod
    def _live_below(obj):
        """The earlier parts lists whose table is still on the page: a deleted table leaves no gap in the stack."""
        doc = obj.Document
        page = doc.getObject(obj.PageName) if obj.PageName else None
        out = []
        for prev in obj.Below:
            if prev is None or prev is obj:
                continue
            v = doc.getObject(prev.ViewName) if prev.ViewName else None
            if v is not None and (page is None or v in page.Views):
                out.append(prev)
        return out

    def _signature(self, obj):
        page = obj.Document.getObject(obj.PageName) if obj.PageName else None
        tpl = page.Template.Name if page is not None and page.Template is not None else ""
        return tpl + "|" + ",".join(p.Name for p in self._live_below(obj))

    def mustExecute(self, obj):
        # a new page template (another object) or a table taken off the page does not touch this helper by itself
        return 1 if obj.Source is not None and self._signature(obj) != obj.Signature else 0

    def execute(self, obj):
        sheet = obj.Source
        if sheet is None:
            return
        self._read_template(obj)
        obj.Signature = self._signature(obj)
        table = read_table(sheet)
        ncols = len(table[0]) if table else 0
        widths = []
        for c in range(ncols):
            w = max(text_width_px(row[c]) for row in table) if table else 0.0
            widths.append(max(MIN_COL_PX, int(math.ceil((w * FIT_SLACK) + PAD_LEFT_PX + PAD_RIGHT_PX))))
        self._apply(sheet, widths, len(table))
        width_mm = sum(widths) * PX_MM
        height_mm = len(table) * ROW_PX * PX_MM
        obj.Columns, obj.Rows = ncols, len(table)
        obj.CellEnd = "%s%d" % (_letter(max(ncols, 1) - 1), max(len(table), 1))
        obj.Width, obj.Height = width_mm, height_mm
        # the corner depends on the width of the columns, which decides whether they reach over the title block
        area = list(obj.Area)
        right, bottom = anchor(area, list(obj.TitleBlock), width_mm)
        # a second table stands above the earlier ones, so two assemblies' lists on one page never overlap
        for prev in self._live_below(obj):
            bottom = max(bottom, float(prev.Bottom) + float(prev.Height) + GAP_MM)
        obj.Right, obj.Bottom = right, bottom
        obj.CenterX = right - width_mm / 2.0
        obj.CenterY = bottom + height_mm / 2.0
        top, room = bottom + height_mm, area[3] - MARGIN_MM
        if top > room + 1e-6 and getattr(self, "_warned", None) != (len(table), round(top, 1)):
            self._warned = (len(table), round(top, 1))
            App.Console.PrintWarning(
                "PartsList: %s (%d rows, %.0f mm) does not fit the drawing area: it runs %.0f mm past the top of the sheet area; "
                "use a larger sheet, fewer rows, or clear the X and Y expressions of the view and place it by hand\n"
                % (obj.Label, len(table), height_mm, top - room)
            )

    @staticmethod
    def _read_template(obj):
        """Read the drawing area and title block of the page again: its template may have changed since the last run."""
        page = obj.Document.getObject(obj.PageName) if obj.PageName else None
        if page is None:
            return
        if obj.Template != page.Template:
            obj.Template = page.Template
        area, title = page_areas(page)
        area, title = list(area), (list(title) if title else [])
        if list(obj.Area) != area:
            obj.Area = area
        if list(obj.TitleBlock) != title:
            obj.TitleBlock = title

    @staticmethod
    def _apply(sheet, widths, nrows):
        changed = False
        for c, w in enumerate(widths):
            if sheet.getColumnWidth(_letter(c)) != w:
                sheet.setColumnWidth(_letter(c), w)
                changed = True
        for r in range(1, nrows + 1):
            if sheet.getRowHeight(str(r)) != ROW_PX:
                sheet.setRowHeight(str(r), ROW_PX)
                changed = True
        if changed:
            # a width is layout, not content: the sheet stays up to date, so the next recompute does not run it again
            try:
                sheet.purgeTouched()
            except Exception:
                pass


# ------------------------------------------------------------------------------------------------
# public API
# ------------------------------------------------------------------------------------------------


def _is_fit(o):
    return o.TypeId == "App::FeaturePython" and isinstance(getattr(o, "Proxy", None), PartsListFit)


def fit_of(view):
    """The PartsListFit object that feeds a parts-list view, or None.

    Found by the view's own expressions (``CellEnd``, ``X``, ``Y`` name the helper), then by the helper's ``ViewName``, then by
    ``OutList``: after a delete and an undo the view's ``OutList`` no longer lists the helper though the expressions still name it.
    """
    doc = view.Document
    named = set()
    for prop, expr in getattr(view, "ExpressionEngine", None) or ():
        m = re.match(r"\s*(\w+)\.", expr)
        if m:
            named.add(m.group(1))
    for n in sorted(named):
        o = doc.getObject(n)
        if o is not None and _is_fit(o):
            return o
    for o in doc.Objects:
        if _is_fit(o) and o.ViewName == view.Name:
            return o
    for o in view.OutList:
        if _is_fit(o):
            return o
    return None


def _drop_orphans(page):
    """Remove helpers of this page that no parts-list view uses (left by a delete and an undo of the table)."""
    doc = page.Document
    used = {fit_of(v).Name for v in page.Views if v.TypeId == "TechDraw::DrawViewSpreadsheet" and fit_of(v) is not None}
    for o in list(doc.Objects):
        if _is_fit(o) and o.PageName == page.Name and o.Name not in used:
            doc.removeObject(o.Name)


def is_parts_view(v):
    return v.TypeId == "TechDraw::DrawViewSpreadsheet" and (hasattr(v, "PartsListAssembly") or fit_of(v) is not None)


def views_on(page):
    """The parts-list tables on ``page``, including a damaged one (its BOM or helper deleted)."""
    return [v for v in page.Views if is_parts_view(v)]


def assembly_of(view):
    """The assembly a parts-list table was made for, or None when it is gone."""
    name = getattr(view, "PartsListAssembly", "")
    asm = view.Document.getObject(name) if name else None
    if asm is None:
        fit = fit_of(view)
        src = fit.Source if fit is not None else None
        asm = next((o for o in (src.InList if src is not None else []) if o.TypeId == "Assembly::AssemblyObject"), None)
    return asm


def _attach(doc, page, assembly, bom, view=None):
    """Create the helper and the table view for ``bom``, or repair a damaged pair (a deleted BOM or helper) in place."""
    fit = fit_of(view) if view is not None else None
    if fit is None:
        fit = doc.addObject("App::FeaturePython", "PartsListFit")
        PartsListFit(fit)
        fit.Label = "Parts list fit"
    fit.Source = bom
    area, title = page_areas(page)
    fit.Area = list(area)
    fit.TitleBlock = list(title) if title else []
    fit.PageName = page.Name
    fit.Template = page.Template
    others = []
    for v in views_on(page):
        if v is view:
            break
        f = fit_of(v)
        if f is not None:
            others.append(f)
    fit.Below = others
    if view is None:
        view = doc.addObject("TechDraw::DrawViewSpreadsheet", "PartsList")
        view.Label = "Parts list"
        page.addView(view)
        view.addProperty("App::PropertyString", "PartsListAssembly", "PartsList", "Name of the assembly this table lists")
        view.CellStart = "A1"
        view.CellEnd = "C2"
    view.PartsListAssembly = assembly.Name
    fit.ViewName = view.Name
    fit.Signature = ""
    view.Source = bom
    view.TextSize = TEXT_PX
    for prop in ("CellEnd", "X", "Y"):
        view.setExpression(prop, None)
    view.setExpression("CellEnd", "%s.CellEnd" % fit.Name)
    view.setExpression("X", "%s.CenterX" % fit.Name)
    view.setExpression("Y", "%s.CenterY" % fit.Name)
    bom.touch()
    return view


def make(page, assembly, columns=None):
    """Put the assembly's parts list on ``page`` and return the ``TechDraw::DrawViewSpreadsheet``.

    One undo transaction.  Calling it again for the same assembly on the same page returns the view that is there; when its
    BOM or helper has been deleted, the same view is rebuilt in place (no second table).
    """
    doc = page.Document
    doc.openTransaction("Parts list")
    try:
        bom = _bom(assembly, columns)
        mine = [v for v in views_on(page) if getattr(v, "PartsListAssembly", None) == assembly.Name or assembly_of(v) is assembly]
        view = None
        for v in mine:
            if v.Source == bom and fit_of(v) is not None and fit_of(v).Source == bom:
                doc.commitTransaction()
                return v
            if view is None:
                view = v
        view = _attach(doc, page, assembly, bom, view)
        doc.recompute([view])
    except Exception:
        doc.abortTransaction()
        raise
    doc.commitTransaction()
    return view


def refresh(page):
    """Regenerate every parts list on ``page`` now, rebuilding a table whose BOM or helper was deleted.

    A plain ``doc.recompute()`` already follows an assembly edit; this is for forcing a regeneration or repairing damage.
    """
    doc = page.Document
    _drop_orphans(page)
    for v in views_on(page):
        asm = assembly_of(v)
        if asm is not None:
            make(page, asm)
    for v in views_on(page):
        if v.Source is not None:
            v.Source.touch()
    doc.recompute()
