# SPDX-License-Identifier: LGPL-2.1-or-later

# **************************************************************************
#   Revision table (G04 slice 3a): a revision block kept on the page as a    *
#   stock spreadsheet view, with the newest revision written into the        *
#   template's own revision fields.                                          *
#                                                                            *
#   Public sources of the conventions: ASME Y14.35 (catalogue pages) and a   *
#   public NDIA training deck on it, for the columns zone, revision,         *
#   description, date and approval; ISO 7200 for the revision index and date *
#   of issue fields the bundled templates already carry.  No text of either  *
#   is copied and no compliance is claimed.                                  *
# **************************************************************************

"""A revision table on a drawing page.

    import RevisionTable
    view = RevisionTable.make(page)
    RevisionTable.add(page, "B", "Hole moved", "2026-11-10", "MS", zone="B3")
    RevisionTable.from_pdm(page, "bracket", vault)      # released revisions of a PDM vault document, not twice

The table is a stock ``TechDraw::DrawViewSpreadsheet`` of a ``Spreadsheet::Sheet`` (columns ZONE, REV, DESCRIPTION, DATE,
APPROVED; newest row last), placed in free space at the top right of the drawing area.  On every recompute the template's
``revision_index`` (and ``date_of_issue`` or ``issue_date`` where the template has them) are set to the newest row's REV and DATE;
a template without those keys is left alone.  Edit the sheet to change a row.
"""

import os

import FreeCAD as App

import HoleTable as HT
import PartsList as PL

HEADER = ("ZONE", "REV", "DESCRIPTION", "DATE", "APPROVED")
REV_KEYS = ("revision_index",)
DATE_KEYS = ("date_of_issue", "issue_date")


def read_rows(sheet):
    """-> the sheet's rows as lists of text (quotes stripped), header first, up to the first empty row."""
    rows, r = [], 1
    while r < 2000:
        row = []
        for c in range(len(HEADER)):
            v = sheet.getContents("%s%d" % (HT.col(c), r))
            row.append(v[1:] if v.startswith("'") else v)
        if not any(row):
            break
        rows.append(row)
        r += 1
    return rows


class RevisionDriver:
    """Python feature between the revision sheet and its table view: fits, places, and syncs the template fields."""

    def __init__(self, obj):
        obj.Proxy = self
        add = obj.addProperty
        add("App::PropertyLink", "Sheet", "Revision", "The revision sheet")
        add("App::PropertyString", "PageName", "Revision", "Name of the page")
        add("App::PropertyString", "ViewName", "Revision", "Name of the table view")
        add("App::PropertyString", "Warning", "Revision", "What the table could not avoid overlapping, or empty")
        add("App::PropertyInteger", "Rows", "Revision", "Rows of the sheet, header included")

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def onDocumentRestored(self, obj):
        obj.Proxy = self
        HT.SETTLE.arm(obj.Document)

    def owned(self, obj):
        doc = obj.Document
        page = doc.getObject(obj.PageName) if obj.PageName else None
        out = [o for o in (obj.Sheet, doc.getObject(obj.ViewName) if obj.ViewName else None) if o is not None]
        if page is not None and page.Template is not None:
            out += [page.Template, page]
        return out

    def execute(self, obj):
        doc = obj.Document
        sheet = obj.Sheet
        page = doc.getObject(obj.PageName) if obj.PageName else None
        view = doc.getObject(obj.ViewName) if obj.ViewName else None
        if sheet is None or page is None or view is None:
            return
        HT.SETTLE.arm(doc)
        rows = read_rows(sheet)
        if not rows:
            return
        widths = HT.fit_widths(sheet, rows)
        w, h = sum(widths) * PL.PX_MM, len(rows) * PL.ROW_PX * PL.PX_MM
        area, title = PL.page_areas(page)
        obs = HT.obstacles(page, skip={view.Name})
        corner = (area[2], area[3])
        box = HT.find_slot(w, h, area, obs, lambda b: (b[2] - corner[0]) ** 2 + (b[3] - corner[1]) ** 2)
        warning = ""
        if box is None:
            box = (area[2] - PL.MARGIN_MM - w, area[3] - PL.MARGIN_MM - h, area[2] - PL.MARGIN_MM, area[3] - PL.MARGIN_MM)
            hit = [n for n, b in obs if HT.overlap(box, b)]
            warning = "RevisionTable: no free space on the page holds the table (%d rows); placed at the top right, it overlaps %s" % (
                len(rows), ", ".join(hit) if hit else "nothing"
            )
            App.Console.PrintWarning(warning + "\n")
        HT.set_if(obj, "Warning", warning)
        HT.set_if(view, "CellStart", "A1")
        HT.set_if(view, "CellEnd", "%s%d" % (HT.col(len(HEADER) - 1), len(rows)))
        moved = abs(view.X.Value - view.PlacedX) > 1e-6 or abs(view.Y.Value - view.PlacedY) > 1e-6
        if not moved:
            cx, cy = 0.5 * (box[0] + box[2]), 0.5 * (box[1] + box[3])
            HT.set_if(view, "X", float(cx))
            HT.set_if(view, "Y", float(cy))
            HT.set_if(view, "PlacedX", float(cx))
            HT.set_if(view, "PlacedY", float(cy))
        obj.Rows = len(rows)
        if len(rows) > 1:
            newest = rows[-1]
            sync_template(page, newest[1], newest[3])


def sync_template(page, rev, date):
    """Write the newest revision and date into the template's editable fields that exist."""
    tpl = page.Template
    if tpl is None or not hasattr(tpl, "EditableTexts"):
        return
    cur = dict(tpl.EditableTexts)
    new = dict(cur)
    for k in REV_KEYS:
        if k in new:
            new[k] = rev
    for k in DATE_KEYS:
        if k in new:
            new[k] = date
    if new != cur:
        tpl.EditableTexts = new


# ------------------------------------------------------------------------------------------------
# public API
# ------------------------------------------------------------------------------------------------


def drivers_on(page):
    return [
        o
        for o in page.Document.Objects
        if o.TypeId == "App::FeaturePython" and isinstance(getattr(o, "Proxy", None), RevisionDriver) and o.PageName == page.Name
    ]


def view_of(page):
    d = drivers_on(page)
    return page.Document.getObject(d[0].ViewName) if d else None


def warning_of(page):
    d = drivers_on(page)
    return d[0].Warning if d else ""


def _make(page):
    doc = page.Document
    drv = drivers_on(page)
    if drv:
        return drv[0]
    sheet = doc.addObject("Spreadsheet::Sheet", "RevisionSheet")
    sheet.Label = "Revision table data"
    HT.write_cells(sheet, [list(HEADER)], 0)
    drv = doc.addObject("App::FeaturePython", "RevisionDriver")
    RevisionDriver(drv)
    drv.Label = "Revision table driver"
    drv.Sheet = sheet
    drv.PageName = page.Name
    v = doc.addObject("TechDraw::DrawViewSpreadsheet", "RevisionTable")
    v.Label = "Revision table"
    page.addView(v)
    v.addProperty("App::PropertyFloat", "PlacedX", "Revision", "X written by the driver; a different X means the user moved it")
    v.addProperty("App::PropertyFloat", "PlacedY", "Revision", "Y written by the driver")
    v.PlacedX, v.PlacedY = v.X.Value, v.Y.Value  # where it stands now is where the driver left it: not moved by the user
    v.Source = sheet
    v.TextSize = PL.TEXT_PX
    v.CellStart = "A1"
    v.CellEnd = "E1"
    drv.ViewName = v.Name
    # the view reads the driver's work through the dependency graph: sheet -> driver; the view follows the sheet
    return drv


def _settle(doc, drv):
    HT.SETTLE.arm(doc)
    drv.touch()
    doc.recompute()
    if any("Touched" in o.State for o in drv.Proxy.owned(drv)):
        doc.recompute()


def make(page):
    """Put a revision table (header only) on ``page`` and return its view; one undo transaction; returns the existing one if there is."""
    doc = page.Document
    HT.begin(doc, "Revision table")
    try:
        drv = _make(page)
        _settle(doc, drv)
    except Exception:
        doc.abortTransaction()
        raise
    doc.commitTransaction()
    return doc.getObject(drv.ViewName)


def add(page, rev, description, date, approved, zone=""):
    """Append a revision row (newest last) and update the table and the template fields; one undo transaction; -> the view."""
    doc = page.Document
    HT.begin(doc, "Add revision")
    try:
        drv = _make(page)
        rows = read_rows(drv.Sheet)
        rows.append([zone, rev, description, date, approved])
        HT.write_cells(drv.Sheet, rows, len(rows))
        _settle(doc, drv)
    except Exception:
        doc.abortTransaction()
        raise
    doc.commitTransaction()
    return doc.getObject(drv.ViewName)


def default_vault():
    """The vault ``from_pdm`` uses when none is passed.

    G03 has no default vault of its own: the PDM workbench keeps the connection in the ``PDM`` parameter group
    (``PDMGui.state.current_vault()``, which also works headless once ``state.connect`` has saved it).  Resolution order:
    the environment (``PDM_WORKDIR`` = a working copy of the vault, ``PDM_USER`` = the user), then that saved connection.
    """
    wd, user = os.environ.get("PDM_WORKDIR"), os.environ.get("PDM_USER")
    if wd and user:
        from PDM import vault as V

        return V.Vault.open(wd, user)
    from PDMGui import state

    v = state.current_vault()
    if v is None:
        raise ValueError("from_pdm: no vault given, PDM_WORKDIR/PDM_USER are not set and the PDM workbench is not connected to one")
    return v


def from_pdm(page, name, vault=None):
    """Append the released revisions of the PDM document ``name`` that the table lacks, oldest first; -> the view.

    ``vault`` is a ``PDM.vault.Vault`` (default: the PDM workbench's connected vault; anything whose ``history(name)`` returns the newest-first records).  A revision label is
    taken once, from its newest released record; the description is the record's first message line, the date its day, APPROVED
    the author.
    """
    if vault is None:
        vault = default_vault()
    doc = page.Document
    HT.begin(doc, "Revisions from PDM")
    try:
        drv = _make(page)
        rows = read_rows(drv.Sheet)
        have = {r[1] for r in rows[1:]}
        new, seen = [], set()
        for rec in vault.history(name):
            lab = rec.get("revision")
            if not rec.get("released") or not lab or lab in seen:
                continue
            seen.add(lab)
            if lab in have:
                continue
            msg = (rec.get("message") or "").strip().splitlines()
            new.append(["", lab, msg[0] if msg else "", str(rec.get("time", ""))[:10], rec.get("author", "")])
        rows.extend(reversed(new))
        HT.write_cells(drv.Sheet, rows, len(rows))
        _settle(doc, drv)
    except Exception:
        doc.abortTransaction()
        raise
    doc.commitTransaction()
    return doc.getObject(drv.ViewName)
