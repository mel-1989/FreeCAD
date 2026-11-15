# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Task panel that adds a revision row (G04 slice 9).                    *
# *                                                                         *
# *   Public sources: FreeCAD's own TechDrawTools/TaskFillTemplateFields.py *
# *   (a Python task panel with a form, accept and reject, opened through   *
# *   Gui.Control.showDialog), run 098's TaskDimSet.py and RevisionTable's  *
# *   public add(); the contents are ruling 115 of the G04 brief.           *
# ***************************************************************************
"""Provides the task panel behind TechDraw_RevisionAdd."""

import datetime

from PySide import QtGui
from PySide.QtCore import QT_TRANSLATE_NOOP

import FreeCAD as App
import FreeCADGui as Gui

import RevisionTable

_FORMULA_STARTS = ("=", "+", "-", "@")


class TaskRevisionAdd:
    """Collects one revision row for ``page`` and adds it through ``RevisionTable.add``."""

    def __init__(self, page):
        self.page = page
        self.done = False
        self._build()

    def _line(self, grid, row, label, name, text=""):
        grid.addWidget(QtGui.QLabel(QT_TRANSLATE_NOOP("TechDraw_RevisionAdd", label)), row, 0)
        edit = QtGui.QLineEdit(text)
        edit.setObjectName(name)
        grid.addWidget(edit, row, 1)
        return edit

    def _build(self):
        f = QtGui.QWidget()
        f.setObjectName("RevisionAddPanel")
        f.setWindowTitle(QT_TRANSLATE_NOOP("TechDraw_RevisionAdd", "Add revision"))
        self.form = f
        lay = QtGui.QVBoxLayout(f)
        grid = QtGui.QGridLayout()
        lay.addLayout(grid)
        self.rev = self._line(grid, 0, "Revision", "revRev")
        self.description = self._line(grid, 1, "Description", "revDescription")
        self.date = self._line(grid, 2, "Date", "revDate", datetime.date.today().isoformat())
        self.approved = self._line(grid, 3, "Approved by", "revApproved")
        self.zone = self._line(grid, 4, "Zone", "revZone")
        self.message = QtGui.QLabel("")
        self.message.setObjectName("revMessage")
        self.message.setWordWrap(True)
        lay.addWidget(self.message)

    def set_message(self, text):
        self.message.setText(text or "")

    def getStandardButtons(self):
        flags = QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel
        return int(getattr(flags, "value", flags))  # PySide6 wraps the flags in an enum object

    @staticmethod
    def _sentence(e):
        text = " ".join(str(e).split()) or e.__class__.__name__
        return text if text.endswith(".") else text + "."

    def accept(self):
        try:
            return self._apply()
        except Exception as e:  # never a traceback in the report view
            self.set_message(self._sentence(e))
            return False

    def reject(self):
        self.done = True
        return True

    def _apply(self):
        rev = self.rev.text().strip()
        desc = self.description.text().strip()
        if not rev:
            self.set_message("Enter the revision, for example A.")
            return False
        if not desc:
            self.set_message("Enter a description of the change.")
            return False
        if rev.startswith(_FORMULA_STARTS):
            self.set_message("The revision cannot start with =, +, - or @, because a table cell would read it as a formula.")
            return False
        try:
            gone = self.page.Document.getObject(self.page.Name) is None
        except Exception:  # a deleted object raises on attribute access
            gone = True
        if gone:
            self.set_message("The page was deleted while this panel was open; press Cancel.")
            return False
        doc = self.page.Document
        try:
            view = RevisionTable.add(
                self.page, rev, desc, self.date.text().strip(), self.approved.text().strip(), self.zone.text().strip()
            )
        except Exception as e:
            self.set_message(self._sentence(e))
            return False
        self.set_message("")
        self.done = True
        if view is not None:
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(doc.Name, view.Name)
        return True


def show(page):
    """Open the panel in the task view; -> the panel."""
    panel = TaskRevisionAdd(page)
    Gui.Control.showDialog(panel)
    return panel
