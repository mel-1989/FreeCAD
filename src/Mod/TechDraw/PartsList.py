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


def page_areas(page):
    """-> (drawing area, title block or None), boxes (x0, y0, x1, y1) in page millimetres, y up.

    Read from the template's rectangles: the drawing space frame, and the title block frame.  A template that names none
    (the blank default) has the page itself as its drawing area and no title block.
    """
    t = page.Template
    W, H = float(t.Width), float(t.Height)
    area, title = (0.0, 0.0, W, H), None
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


def fit_of(view):
    """The PartsListFit object that feeds a parts-list view, or None."""
    for o in view.OutList:
        if o.TypeId == "App::FeaturePython" and isinstance(getattr(o, "Proxy", None), PartsListFit):
            return o
    return None


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
    for v in views_on(page):
        asm = assembly_of(v)
        if asm is not None:
            make(page, asm)
    for v in views_on(page):
        if v.Source is not None:
            v.Source.touch()
    doc.recompute()


def balloon(page, view, partslist=None):
    """Balloons tied to the rows of the page's parts list; see ``Balloons.balloon``."""
    import Balloons

    return Balloons.balloon(page, view, partslist)
