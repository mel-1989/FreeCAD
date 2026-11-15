# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Task panel for chain, baseline and ordinate dimension sets (G04 5b).  *
# *                                                                         *
# *   Public sources: FreeCAD's own TechDrawTools/TaskFillTemplateFields.py *
# *   and CommandFillTemplateFields.py (a Python task panel with a form,    *
# *   accept and reject, opened through Gui.Control.showDialog) and the     *
# *   Gui.Selection Python API (getCompleteSelection keeps selection        *
# *   order); the panel's contents are house rulings 104 to 107 of the G04  *
# *   brief.                                                                *
# ***************************************************************************
"""Provides the task panel that creates or edits a DimChain dimension set."""

import re

from PySide import QtCore, QtGui
from PySide.QtCore import QT_TRANSLATE_NOOP

import FreeCAD as App
import FreeCADGui as Gui

import DimChain

_VERTEX = re.compile(r"Vertex\d+")


def _is_view(obj):
    return obj is not None and hasattr(obj, "getVertexByIndex") and obj.TypeId.startswith("TechDraw::")


def read_selection(view=None):
    """-> (view, [VertexN in selection order], dropped count) from the complete selection.

    Without ``view`` the view of the first selected vertex is used.  Items that are not a vertex of that view (an edge, the whole
    view, another view or object) are dropped and counted.
    """
    names, dropped = [], 0
    for sel in Gui.Selection.getCompleteSelection():
        subs = list(sel.SubElementNames) or [""]
        for sub in subs:
            if view is None and _is_view(sel.Object) and _VERTEX.fullmatch(sub or ""):
                view = sel.Object
            if sel.Object is not None and view is not None and sel.Object.Name == view.Name and sel.Object.Document.Name == view.Document.Name and _VERTEX.fullmatch(sub or ""):
                if sub not in names:
                    names.append(sub)
            else:
                dropped += 1
    return view, names, dropped


def page_of(view):
    for o in view.Document.findObjects(Type="TechDraw::DrawPage"):
        if view in o.Views:
            return o
    return None


def _dropped_text(n, view):
    if n <= 0:
        return ""
    if n == 1:
        return "One selected item was not a vertex of %s and was left out." % view.Label
    return "%d selected items were not vertices of %s and were left out." % (n, view.Label)


class TaskDimSet:
    """Create a dimension set (``driver`` None, ``kind`` given) or edit one (``driver`` given)."""

    def __init__(self, kind=None, driver=None):
        self.driver = driver
        self.edit = driver is not None
        self.kind = driver.Kind if self.edit else kind
        self.view = driver.View if self.edit else None
        self.done = False
        self._build()
        if self.edit:
            self.direction.setCurrentIndex(self.direction.findText(driver.Direction))
            self._fill_sides(driver.Side)
            for n in driver.Vertices:
                self.vertices.addItem(n)
            self.original = list(driver.Vertices)
            self.make_datum.setEnabled(False)
        else:
            self.original = []
            self._fill_sides(None)
            view, names, dropped = read_selection()
            if view is None:
                self.set_message("Select at least two vertices of a view first, then open the panel; no view was found in the selection.")
                self.add_selected.setEnabled(True)  # it can still pick the view from a later selection
            else:
                self.view = view
                for n in names:
                    self.vertices.addItem(n)
                self.set_message(_dropped_text(dropped, view))

    # -- widgets ------------------------------------------------------------------------------

    def _build(self):
        f = QtGui.QWidget()
        f.setObjectName("DimSetPanel")
        f.setWindowTitle(QT_TRANSLATE_NOOP("TechDraw_DimSet", "Dimension set"))
        self.form = f
        lay = QtGui.QVBoxLayout(f)
        grid = QtGui.QGridLayout()
        lay.addLayout(grid)
        self.kind_label = QtGui.QLabel(self.kind or "")
        self.kind_label.setObjectName("dimsetKind")
        self.direction = QtGui.QComboBox()
        self.direction.setObjectName("dimsetDirection")
        self.direction.addItems(list(DimChain.DIRECTIONS))
        self.side = QtGui.QComboBox()
        self.side.setObjectName("dimsetSide")
        grid.addWidget(QtGui.QLabel(QT_TRANSLATE_NOOP("TechDraw_DimSet", "Kind")), 0, 0)
        grid.addWidget(self.kind_label, 0, 1)
        grid.addWidget(QtGui.QLabel(QT_TRANSLATE_NOOP("TechDraw_DimSet", "Direction")), 1, 0)
        grid.addWidget(self.direction, 1, 1)
        grid.addWidget(QtGui.QLabel(QT_TRANSLATE_NOOP("TechDraw_DimSet", "Side")), 2, 0)
        grid.addWidget(self.side, 2, 1)
        self.vertices = QtGui.QListWidget()
        self.vertices.setObjectName("dimsetVertices")
        lay.addWidget(QtGui.QLabel(QT_TRANSLATE_NOOP("TechDraw_DimSet", "Vertices (the first is the datum)")))
        lay.addWidget(self.vertices)
        row = QtGui.QHBoxLayout()
        self.add_selected = QtGui.QPushButton(QT_TRANSLATE_NOOP("TechDraw_DimSet", "Add selected"))
        self.add_selected.setObjectName("dimsetAddSelected")
        self.remove = QtGui.QPushButton(QT_TRANSLATE_NOOP("TechDraw_DimSet", "Remove"))
        self.remove.setObjectName("dimsetRemove")
        self.make_datum = QtGui.QPushButton(QT_TRANSLATE_NOOP("TechDraw_DimSet", "Make datum"))
        self.make_datum.setObjectName("dimsetMakeDatum")
        for b in (self.add_selected, self.remove, self.make_datum):
            row.addWidget(b)
        lay.addLayout(row)
        self.message = QtGui.QLabel("")
        self.message.setObjectName("dimsetMessage")
        self.message.setWordWrap(True)
        lay.addWidget(self.message)
        if self.edit:
            self.direction.setEnabled(False)
        if self.kind == "chain":
            self.make_datum.setEnabled(False)  # a chain has no datum
        self.direction.currentIndexChanged.connect(lambda *_: self._fill_sides(None))
        self.add_selected.clicked.connect(self.on_add_selected)
        self.remove.clicked.connect(self.on_remove)
        self.make_datum.clicked.connect(self.on_make_datum)

    def _fill_sides(self, current):
        d = self.direction.currentText()
        self.side.blockSignals(True)
        self.side.clear()
        self.side.addItems(list(DimChain.SIDES[d]))
        if current in DimChain.SIDES[d]:
            self.side.setCurrentIndex(self.side.findText(current))
        self.side.blockSignals(False)

    def set_message(self, text):
        self.message.setText(text or "")

    def items(self):
        return [self.vertices.item(i).text() for i in range(self.vertices.count())]

    # -- buttons ------------------------------------------------------------------------------

    def on_add_selected(self):
        try:
            view, names, dropped = read_selection(self.view)
            if view is None:
                self.set_message("Select vertices of a view first; no view was found in the selection.")
                return
            self.view = view
            have = set(self.items())
            for n in names:
                if n not in have:
                    self.vertices.addItem(n)
                    have.add(n)
            self.set_message(_dropped_text(dropped, view))
        except Exception as e:  # never a traceback in the report view
            self.set_message("Could not read the selection (%s)." % e)

    def on_remove(self):
        r = self.vertices.currentRow()
        if r >= 0:
            self.vertices.takeItem(r)
            self.set_message("")

    def on_make_datum(self):
        r = self.vertices.currentRow()
        if r > 0:
            it = self.vertices.takeItem(r)
            self.vertices.insertItem(0, it)
            self.vertices.setCurrentRow(0)
            self.set_message("")

    # -- task dialog protocol -------------------------------------------------------------------

    def getStandardButtons(self):
        flags = QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel
        return int(getattr(flags, "value", flags))  # PySide6 wraps the flags in an enum object

    def accept(self):
        try:
            return self._apply()
        except Exception as e:  # never a traceback in the report view
            self.set_message(self._sentence(e))
            return False

    def reject(self):
        self.done = True
        return True

    @staticmethod
    def _sentence(e):
        text = " ".join(str(e).split()) or e.__class__.__name__
        return text if text.endswith(".") else text + "."

    def _apply(self):
        if self.view is None:
            self.set_message("Select at least two vertices of a view first; there is no view to dimension yet.")
            return False
        doc = self.view.Document if not self.edit else self.driver.Document
        names, side, direction = self.items(), self.side.currentText(), self.direction.currentText()
        if self.edit:
            cur = list(self.driver.Vertices)
            gone = [n for n in cur if n not in names]
            new = [n for n in names if n not in cur]
            if not gone and not new and side == self.driver.Side:
                self.set_message("")
                self.done = True
                return True
        else:
            page = page_of(self.view)
            if page is None:
                self.set_message("The view %s is not on a TechDraw page." % self.view.Label)
                return False
        doc.openTransaction("Dimension set" if not self.edit else "Edit dimension set")
        try:
            if self.edit:
                for n in gone:
                    DimChain.remove(self.driver, n)
                for n in new:
                    DimChain.add(self.driver, n)
                if side != self.driver.Side:
                    DimChain.set_side(self.driver, side)
            else:
                DimChain.make(page, self.view, names, self.kind, direction, side)
        except Exception as e:
            doc.abortTransaction()
            self.set_message(self._sentence(e))
            return False
        doc.commitTransaction()
        self.set_message("")
        self.done = True
        return True


def show(kind=None, driver=None):
    """Open the panel in the task view; -> the panel."""
    panel = TaskDimSet(kind, driver)
    Gui.Control.showDialog(panel)
    return panel
