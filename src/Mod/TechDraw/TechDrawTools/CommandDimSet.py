# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Commands that create and edit chain, baseline and ordinate dimension  *
# *   sets (G04 slice 5b).                                                  *
# *                                                                         *
# *   Public source: FreeCAD's own TechDrawTools/CommandFillTemplateFields  *
# *   .py (how a Python TechDraw command is written and registered); the   *
# *   behaviour is house rulings 104 to 107 of the G04 brief.               *
# ***************************************************************************
"""Provides the TechDraw_DimSetChain, _DimSetBaseline, _DimSetOrdinate and _DimSetEdit GuiCommands."""

from PySide.QtCore import QT_TRANSLATE_NOOP

import FreeCAD as App
import FreeCADGui as Gui

import DimChain


def _has_page():
    doc = App.ActiveDocument
    return doc is not None and bool(doc.findObjects(Type="TechDraw::DrawPage"))


def selected_driver():
    """-> the dimension set driver of the selection (a driver, one of its member dimensions or its ordinate symbol), or None."""
    for o in Gui.Selection.getSelection():
        d = DimChain.driver_of(o)
        if d is not None:
            return d
    return None


def _report(text):
    App.Console.PrintError("TechDraw dimension set: %s\n" % text)


class _CommandCreate:
    kind = ""

    def Activated(self):
        try:
            import TechDrawTools.TaskDimSet as T

            T.show(kind=self.kind)
        except Exception as e:  # one sentence, never a traceback
            _report("could not open the panel (%s)." % " ".join(str(e).split()))

    def IsActive(self):
        return _has_page()


class CommandDimSetChain(_CommandCreate):
    kind = "chain"

    def GetResources(self):
        return {
            "Pixmap": "actions/TechDraw_DimSetChain.svg",
            "MenuText": QT_TRANSLATE_NOOP("TechDraw_DimSetChain", "Chain dimension set"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "TechDraw_DimSetChain",
                "Lays the selected vertices out as a chain of dimensions on one line outside the view; the set follows model edits",
            ),
            "StatusTip": QT_TRANSLATE_NOOP("TechDraw_DimSetChain", "Select two or more vertices of a view, then make a chain set"),
        }


class CommandDimSetBaseline(_CommandCreate):
    kind = "baseline"

    def GetResources(self):
        return {
            "Pixmap": "actions/TechDraw_DimSetBaseline.svg",
            "MenuText": QT_TRANSLATE_NOOP("TechDraw_DimSetBaseline", "Baseline dimension set"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "TechDraw_DimSetBaseline",
                "Dimensions the selected vertices from the first one in stacked lines outside the view; the set follows model edits",
            ),
            "StatusTip": QT_TRANSLATE_NOOP("TechDraw_DimSetBaseline", "Select two or more vertices of a view, the datum first, then make a baseline set"),
        }


class CommandDimSetOrdinate(_CommandCreate):
    kind = "ordinate"

    def GetResources(self):
        return {
            "Pixmap": "actions/TechDraw_DimSetOrdinate.svg",
            "MenuText": QT_TRANSLATE_NOOP("TechDraw_DimSetOrdinate", "Ordinate dimension set"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "TechDraw_DimSetOrdinate",
                "Marks the selected vertices with their distance from the first one on extension lines; the set follows model edits",
            ),
            "StatusTip": QT_TRANSLATE_NOOP("TechDraw_DimSetOrdinate", "Select two or more vertices of a view, the datum first, then make an ordinate set"),
        }


class CommandDimSetEdit:
    def GetResources(self):
        return {
            "Pixmap": "actions/TechDraw_DimSetEdit.svg",
            "MenuText": QT_TRANSLATE_NOOP("TechDraw_DimSetEdit", "Edit dimension set"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "TechDraw_DimSetEdit",
                "Changes the side of the selected set, or takes vertices in and out of it",
            ),
            "StatusTip": QT_TRANSLATE_NOOP("TechDraw_DimSetEdit", "Select a dimension set, one of its dimensions or its ordinate symbol, then edit it"),
        }

    def Activated(self):
        try:
            import TechDrawTools.TaskDimSet as T

            d = selected_driver()
            if d is None:
                _report("select a dimension set, one of its dimensions or its ordinate symbol first.")
                return
            T.show(driver=d)
        except Exception as e:  # one sentence, never a traceback
            _report("could not open the panel (%s)." % " ".join(str(e).split()))

    def IsActive(self):
        return App.ActiveDocument is not None and selected_driver() is not None


# ---------------------------------------------------------------------------------------------
# double click on a driver in the tree opens the edit panel.  A view-provider proxy would be written into GuiDocument.xml and stable
# FreeCAD would warn about it a second time, so the tree's own itemDoubleClicked signal is used instead and nothing is saved.
# ---------------------------------------------------------------------------------------------

_hooked = set()


def _open_edit_for_selection():
    d = selected_driver()
    sel = Gui.Selection.getSelection()
    if d is not None and len(sel) == 1 and sel[0].Name == d.Name:
        try:
            import TechDrawTools.TaskDimSet as T

            T.show(driver=d)
        except Exception as e:
            _report("could not open the panel (%s)." % " ".join(str(e).split()))


def on_tree_double_click(*_):
    from PySide import QtCore

    QtCore.QTimer.singleShot(0, _open_edit_for_selection)  # after the tree has selected the item


def install_tree_hook():
    """Connect every document tree widget's itemDoubleClicked to the edit panel; safe to call again."""
    if not App.GuiUp:
        return 0
    from PySide import QtWidgets

    mw = Gui.getMainWindow()
    n = 0
    for w in mw.findChildren(QtWidgets.QTreeWidget):
        if w.metaObject().className() == "Gui::TreeWidget" and id(w) not in _hooked:
            w.itemDoubleClicked.connect(on_tree_double_click)
            _hooked.add(id(w))
            n += 1
    return n


Gui.addCommand("TechDraw_DimSetChain", CommandDimSetChain())
Gui.addCommand("TechDraw_DimSetBaseline", CommandDimSetBaseline())
Gui.addCommand("TechDraw_DimSetOrdinate", CommandDimSetOrdinate())
Gui.addCommand("TechDraw_DimSetEdit", CommandDimSetEdit())

try:
    from PySide import QtCore as _QtCore

    _QtCore.QTimer.singleShot(0, install_tree_hook)
except Exception:
    pass
