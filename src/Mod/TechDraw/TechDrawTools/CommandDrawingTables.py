# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Commands for the parts list, hole, bend and revision tables and for   *
# *   sheet sets (G04 slice 9).                                             *
# *                                                                         *
# *   Public sources: FreeCAD's own TechDrawTools/CommandFillTemplateFields *
# *   .py (how a Python TechDraw command is written and registered), the    *
# *   Gui.Selection Python API and run 098's CommandDimSet.py; the          *
# *   behaviour is house rulings 111 to 115 of the G04 brief and the        *
# *   public APIs of PartsList, HoleTable, BendTable, RevisionTable and     *
# *   SheetSet in this tree.                                                *
# ***************************************************************************
"""Provides the TechDraw_PartsListInsert, _HoleTableInsert, _BendTableInsert, _RevisionAdd, _SheetSetMake and _SheetSetAddSheet GuiCommands.

Each command is select-and-press.  A command that creates nothing writes exactly one line to the report view
(``<command name>: <one sentence>``, a warning, never a traceback) and leaves the document and its undo stack as they were.
"""

from PySide.QtCore import QT_TRANSLATE_NOOP

import FreeCAD as App
import FreeCADGui as Gui

_busy = False  # HoleTable.make and BendTable.make pump the event loop while they wait for geometry: a second press must not re-enter


class _Refuse(Exception):
    """A selection or state a command cannot work with; the text is the one sentence the user sees."""


def _sentence(text):
    text = " ".join(str(text).split())
    return text if text.endswith((".", "!", "?")) else text + "."


def _warn(name, text):
    App.Console.PrintWarning("%s: %s\n" % (name, _sentence(text)))


def _doc():
    doc = App.ActiveDocument
    if doc is None:
        raise _Refuse("no document is open")
    return doc


def _pages(doc):
    return list(doc.findObjects(Type="TechDraw::DrawPage"))


def _has_page():
    doc = App.ActiveDocument
    return doc is not None and bool(_pages(doc))


def _has_set():
    doc = App.ActiveDocument
    if doc is None:
        return False
    import SheetSet

    return bool(SheetSet.drivers(doc))


def _selected(doc):
    """-> the selected objects of ``doc`` in selection order, each once."""
    out = []
    for s in Gui.Selection.getCompleteSelection():
        o = s.Object
        if o is not None and o.Document.Name == doc.Name and all(o.Name != p.Name for p in out):
            out.append(o)
    return out


def _is(o, type_id):
    try:
        return bool(o.isDerivedFrom(type_id))
    except Exception:
        return False


def _split(objs):
    """-> (pages, views, assemblies, others) of the selected objects."""
    pages, views, asms, others = [], [], [], []
    for o in objs:
        if _is(o, "TechDraw::DrawPage"):
            pages.append(o)
        elif _is(o, "Assembly::AssemblyObject"):
            asms.append(o)
        elif _is(o, "TechDraw::DrawViewPart"):
            views.append(o)
        else:
            others.append(o)
    return pages, views, asms, others


def _page_of(doc, view):
    """-> the page that shows ``view``, or None.  Scans the pages: ``view.findParentPage()`` crashes FreeCAD for a view that is on no page."""
    def shows(views, depth=0):
        for v in views:
            if v is None:
                continue
            if v.Name == view.Name:
                return True
            # a projection group lists its items in its own Views, not in the page's
            if depth < 4 and v.isDerivedFrom("TechDraw::DrawProjGroup") and shows(getattr(v, "Views", []) or [], depth + 1):
                return True
        return False

    return next((p for p in _pages(doc) if shows(p.Views)), None)


def _active_page(doc):
    try:
        v = Gui.activeView()
        pg = v.getPage() if v is not None and hasattr(v, "getPage") else None
        if pg is not None and pg.Document.Name == doc.Name:
            return pg
    except Exception:
        pass
    return None


def _target_page(doc, pages, view=None):
    """Ruling 112: the selected page, else the parent page of the first selected view, else the active GUI page, else the only page."""
    if pages:
        return pages[0]
    if view is not None:
        pg = _page_of(doc, view)
        if pg is not None:
            return pg
    pg = _active_page(doc)
    if pg is not None:
        return pg
    every = _pages(doc)
    if len(every) == 1:
        return every[0]
    if not every:
        raise _Refuse("the document has no TechDraw page")
    raise _Refuse("select a page first; the document has %d pages and none is open" % len(every))


def _check_page(page):
    if page.Template is None:
        raise _Refuse("page %s has no template; give it one first" % page.Label)


def _select(doc, *objs):
    Gui.Selection.clearSelection()
    for o in objs:
        Gui.Selection.addSelection(doc.Name, o.Name)


def _label(o):
    return o.Label if o is not None else "?"


class _Base:
    name = ""

    def IsActive(self):
        return _has_page() and not _busy

    def Activated(self):
        global _busy
        if _busy:
            return
        _busy = True
        try:
            self.run(_doc())
        except _Refuse as e:
            _warn(self.name, str(e))
        except Exception as e:  # one sentence, never a traceback
            _warn(self.name, "%s" % (" ".join(str(e).split()) or e.__class__.__name__))
        finally:
            _busy = False

    def one_view(self, doc, what):
        """The single selected view of a hole or bend table command, and the page it stands on."""
        pages, views, asms, others = _split(_selected(doc))
        if not (pages or views or asms or others):
            raise _Refuse("select a view first")
        if len(views) != 1 or asms or others or len(pages) > 1:
            if len(views) > 1:
                raise _Refuse("select one view, not %d" % len(views))
            raise _Refuse("select one view of a part %s" % what)
        view = views[0]
        page = _page_of(doc, view)
        if page is None:
            raise _Refuse("the view %s is on no page" % view.Label)
        if pages and pages[0].Name != page.Name:
            raise _Refuse("the view %s is not on page %s" % (view.Label, pages[0].Label))
        _check_page(page)
        return view, page


# ---------------------------------------------------------------------------------------------
# parts list
# ---------------------------------------------------------------------------------------------


class CommandPartsListInsert(_Base):
    name = "TechDraw_PartsListInsert"

    def GetResources(self):
        return {
            "Pixmap": "actions/TechDraw_PartsListInsert.svg",
            "MenuText": QT_TRANSLATE_NOOP("TechDraw_PartsListInsert", "Insert parts list"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "TechDraw_PartsListInsert",
                "Draws the parts of the selected assembly as a table that follows the assembly",
            ),
            "StatusTip": QT_TRANSLATE_NOOP("TechDraw_PartsListInsert", "Select an assembly, or a view of one, then insert its parts table"),
        }

    def run(self, doc):
        import PartsList

        pages, views, asms, others = _split(_selected(doc))
        if not (pages or views or asms or others):
            raise _Refuse("select an assembly or a view of one first")
        if others or len(pages) > 1 or len(views) + len(asms) != 1:
            if len(views) + len(asms) > 1:
                raise _Refuse("select one assembly or one view of an assembly, not %d" % (len(views) + len(asms)))
            raise _Refuse("select an assembly or a view of one")
        view = views[0] if views else None
        if view is not None:
            asm = next((o for o in view.Source if _is(o, "Assembly::AssemblyObject")), None)
            if asm is None:
                raise _Refuse("the view %s does not show an assembly" % view.Label)
        else:
            asm = asms[0]
        page = _target_page(doc, pages, view)
        _check_page(page)
        have = next((v for v in PartsList.views_on(page) if getattr(v, "PartsListAssembly", None) == asm.Name and PartsList.fit_of(v) is not None), None)
        if have is not None:
            _select(doc, have)
            raise _Refuse("page %s already has a parts list for %s; it is selected" % (page.Label, asm.Label))
        made = PartsList.make(page, asm)
        _select(doc, made)


# ---------------------------------------------------------------------------------------------
# hole table
# ---------------------------------------------------------------------------------------------


class CommandHoleTableInsert(_Base):
    name = "TechDraw_HoleTableInsert"

    def GetResources(self):
        return {
            "Pixmap": "actions/TechDraw_HoleTableInsert.svg",
            "MenuText": QT_TRANSLATE_NOOP("TechDraw_HoleTableInsert", "Insert hole table"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "TechDraw_HoleTableInsert",
                "Tags the round openings seen end-on in the selected view and lists their position and size in a table",
            ),
            "StatusTip": QT_TRANSLATE_NOOP("TechDraw_HoleTableInsert", "Select a view of a part, then insert its hole table"),
        }

    def run(self, doc):
        import HoleTable

        view, page = self.one_view(doc, "to list its holes")
        have = next((d for d in HoleTable.drivers_on(page) if d.View is not None and d.View.Name == view.Name), None)
        blocks = HoleTable.HoleTableDriver.blocks(have) if have is not None else []
        if blocks:
            _select(doc, blocks[0])
            raise _Refuse("%s already has a hole table; it is selected" % view.Label)
        made = HoleTable.make(page, view)
        if made is None:
            raise _Refuse("no hole table could be made for %s" % view.Label)
        _select(doc, made)


# ---------------------------------------------------------------------------------------------
# bend table
# ---------------------------------------------------------------------------------------------


def has_bend_data(view):
    return any(hasattr(o, "BendData") for o in (view.Source or []))


class CommandBendTableInsert(_Base):
    name = "TechDraw_BendTableInsert"

    def GetResources(self):
        return {
            "Pixmap": "actions/TechDraw_BendTableInsert.svg",
            "MenuText": QT_TRANSLATE_NOOP("TechDraw_BendTableInsert", "Insert bend table"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "TechDraw_BendTableInsert",
                "Marks the bends of the selected unfolded view and lists their direction, angle and radius in a table",
            ),
            "StatusTip": QT_TRANSLATE_NOOP("TechDraw_BendTableInsert", "Select the view of an unfolded part, then insert its bend table"),
        }

    def run(self, doc):
        import BendTable

        view, page = self.one_view(doc, "unfolded to a flat shape")
        if not has_bend_data(view):
            raise _Refuse("the view %s does not show an unfolded part with bend data" % view.Label)
        have = next((d for d in BendTable.drivers_on(page) if d.View is not None and d.View.Name == view.Name), None)
        blocks = BendTable.BendTableDriver.blocks(have) if have is not None else []
        if blocks:
            _select(doc, blocks[0])
            raise _Refuse("%s already has a bend table; it is selected" % view.Label)
        made = BendTable.make(page, view)
        if made is None:
            raise _Refuse("no bend table could be made for %s" % view.Label)
        _select(doc, made)


# ---------------------------------------------------------------------------------------------
# revision
# ---------------------------------------------------------------------------------------------


class CommandRevisionAdd(_Base):
    name = "TechDraw_RevisionAdd"

    def GetResources(self):
        return {
            "Pixmap": "actions/TechDraw_RevisionAdd.svg",
            "MenuText": QT_TRANSLATE_NOOP("TechDraw_RevisionAdd", "Add revision"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "TechDraw_RevisionAdd",
                "Opens a form for a new revision row; the page's revision table and the title block's revision fields follow it",
            ),
            "StatusTip": QT_TRANSLATE_NOOP("TechDraw_RevisionAdd", "Select a page, or work on the open one, then enter the revision"),
        }

    def run(self, doc):
        pages, views, asms, others = _split(_selected(doc))
        if others or asms or len(pages) > 1 or len(views) > 1:
            raise _Refuse("select one page, or one view on it, or nothing when a page is open")
        page = _target_page(doc, pages, views[0] if views else None)
        _check_page(page)
        if Gui.Control.activeDialog():
            raise _Refuse("close the open task panel first")
        import TechDrawTools.TaskRevisionAdd as T

        T.show(page)


# ---------------------------------------------------------------------------------------------
# sheet sets
# ---------------------------------------------------------------------------------------------


def _set_of(doc, page):
    import SheetSet

    return next((d for d in SheetSet.drivers(doc) if any(p is not None and p.Name == page.Name for p in d.Pages)), None)


class CommandSheetSetMake(_Base):
    name = "TechDraw_SheetSetMake"

    def GetResources(self):
        return {
            "Pixmap": "actions/TechDraw_SheetSetMake.svg",
            "MenuText": QT_TRANSLATE_NOOP("TechDraw_SheetSetMake", "Make sheet set"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "TechDraw_SheetSetMake",
                "Groups the selected pages, or every page when none is selected, so sheet numbers and shared title block fields follow the set",
            ),
            "StatusTip": QT_TRANSLATE_NOOP("TechDraw_SheetSetMake", "Select the pages of the set in order, or none for all pages"),
        }

    def run(self, doc):
        import SheetSet

        pages, views, asms, others = _split(_selected(doc))
        if views or asms or others:
            raise _Refuse("select pages only, or nothing to take every page")
        chosen = pages if pages else _pages(doc)
        if not chosen:
            raise _Refuse("the document has no TechDraw page")
        sets = {}
        for p in chosen:
            d = _set_of(doc, p)
            if d is not None:
                sets.setdefault(d.Name, (d, []))[1].append(p)
        if sets:
            if len(sets) == 1:
                d, taken = next(iter(sets.values()))
                if len(taken) == len(chosen):
                    _select(doc, d)
                    raise _Refuse("%s already has %s; it is selected" % (d.Label, "this page" if len(chosen) == 1 else "these pages"))
            d, taken = next(iter(sets.values()))
            names = ", ".join(p.Label for p in taken)
            raise _Refuse("%s already %s to the sheet set %s; a page is in one set only" % (names, "belongs" if len(taken) == 1 else "belong", d.Label))
        for p in chosen:
            _check_page(p)
        made = SheetSet.make(doc, pages=chosen)
        _select(doc, made)


class CommandSheetSetAddSheet(_Base):
    name = "TechDraw_SheetSetAddSheet"

    def GetResources(self):
        return {
            "Pixmap": "actions/TechDraw_SheetSetAddSheet.svg",
            "MenuText": QT_TRANSLATE_NOOP("TechDraw_SheetSetAddSheet", "Add sheet to set"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "TechDraw_SheetSetAddSheet",
                "Adds a new page at the end of the set, drawn on the same layout as the set's last page",
            ),
            "StatusTip": QT_TRANSLATE_NOOP("TechDraw_SheetSetAddSheet", "Select a page of a sheet set, or the set, then add a sheet"),
        }

    def IsActive(self):
        return _has_set() and not _busy

    def run(self, doc):
        import SheetSet

        objs = _selected(doc)
        if not objs:
            raise _Refuse("select a page of a sheet set, or the set, first")
        if len(objs) != 1:
            raise _Refuse("select one page of a sheet set, or the set, not %d objects" % len(objs))
        o = objs[0]
        sset = None
        if any(d.Name == o.Name for d in SheetSet.drivers(doc)):
            sset = o
        elif _is(o, "TechDraw::DrawPage"):
            sset = _set_of(doc, o)
            if sset is None:
                raise _Refuse("page %s is in no sheet set; make a set first" % o.Label)
        else:
            raise _Refuse("select a page of a sheet set, or the set")
        if not [p for p in sset.Pages if p is not None and p.Template is not None]:
            raise _Refuse("the sheet set %s has no page with a template to copy" % sset.Label)
        made = SheetSet.add_sheet(sset)
        _select(doc, made)


Gui.addCommand("TechDraw_PartsListInsert", CommandPartsListInsert())
Gui.addCommand("TechDraw_HoleTableInsert", CommandHoleTableInsert())
Gui.addCommand("TechDraw_BendTableInsert", CommandBendTableInsert())
Gui.addCommand("TechDraw_RevisionAdd", CommandRevisionAdd())
Gui.addCommand("TechDraw_SheetSetMake", CommandSheetSetMake())
Gui.addCommand("TechDraw_SheetSetAddSheet", CommandSheetSetAddSheet())
