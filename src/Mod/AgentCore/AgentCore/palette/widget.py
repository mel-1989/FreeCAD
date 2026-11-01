# SPDX-License-Identifier: LGPL-2.1-or-later
"""The command palette popup (Std_CommandPalette). Qt side only; index.py and rank.py hold the data and the ranking.

Typing filters and ranks every command of every workbench; Up and Down move, Enter runs the selected command (activating its
workbench first when it is not the active one), Escape closes without running anything. A command that is not active in the
current context is drawn grey; Enter on it runs nothing and says why in the status bar. With an empty query the commands run
from the palette come first, newest first."""

import time

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

from . import index as pidx
from . import rank as prank

MAX_ROWS = 12
RECENT_MAX = 20
PARAM = "User parameter:BaseApp/Preferences/CommandPalette"

_dialog = None
last_open = {}       # timings of the latest open (perf_counter seconds): "start", "first_paint"; read by the tests and the eval


class Model:
    """Shipped index merged with the commands registered right now; the ranker is rebuilt only when the command set changes."""

    def __init__(self):
        self.shipped, self.wb_labels = pidx.load_shipped()
        self.known = {e["name"] for e in self.shipped}
        self.entries = list(self.shipped)
        self.ranker = prank.Ranker(self.entries, self.wb_labels)
        self.by_name = {e["name"]: i for i, e in enumerate(self.entries)}
        self.live = set()

    def refresh(self):
        live = set(Gui.listCommands())
        if live == self.live:
            return
        self.live = live
        new, _ = pidx.live_entries(self.known)
        if new:
            self.known.update(e["name"] for e in new)
            self.entries.extend(new)
            self.by_name = {e["name"]: i for i, e in enumerate(self.entries)}
            self.ranker = prank.Ranker(self.entries, self.wb_labels)

    def wb_label(self, e):
        return self.wb_labels.get(e["wb"], e["wb"]) if e["wb"] else "Core"

    def recents(self):
        raw = App.ParamGet(PARAM).GetString("Recent", "")
        return [n for n in raw.split(",") if n in self.by_name]

    def remember(self, name):
        rec = [name] + [n for n in self.recents() if n != name]
        App.ParamGet(PARAM).SetString("Recent", ",".join(rec[:RECENT_MAX]))

    def results(self, query):
        query = query.strip()
        if not query:
            rec = self.recents()
            rest = [e["name"] for e in sorted(self.entries, key=lambda e: e["menu"].lower()) if e["name"] not in rec]
            return [self.entries[self.by_name[n]] for n in (rec + rest)[:MAX_ROWS * 4]]
        return [self.entries[i] for i, _ in self.ranker.search(query, MAX_ROWS)]


def current_workbench():
    try:
        return Gui.activeWorkbench().name()
    except Exception:
        return ""


def inactive_reason(entry):
    """None when the command can run now, else the sentence for the status bar. A command of a workbench that is not loaded
    yet is not registered and counts as runnable: activating the workbench registers it."""
    if entry["name"] not in Gui.listCommands():
        return None
    cmd = Gui.Command.get(entry["name"])
    if cmd is None or cmd.isActive():
        return None
    return "'%s' is not available right now (it may need an open document, a selection or another mode)" % entry["menu"]


def run_entry(entry, model=None):
    """Activate the entry's workbench when needed, then run it. Returns True when the command was started."""
    mw = Gui.getMainWindow()
    bar = mw.statusBar() if mw else None
    why = inactive_reason(entry)
    if why:
        if bar:
            bar.showMessage(why, 8000)
        return False
    wb = entry["wb"]
    previous = current_workbench()
    switch = bool(wb) and wb != previous and wb in Gui.listWorkbenches()
    if switch and Gui.Control.activeDialog():
        if bar:
            bar.showMessage("Close the open task panel first.", 8000)
        return False
    if switch:
        Gui.activateWorkbench(wb)
    if entry["name"] not in Gui.listCommands():
        if bar:
            bar.showMessage("'%s' could not be loaded" % entry["menu"], 8000)
        return False
    why = inactive_reason(entry)    # the workbench switch can change the context
    if why:
        if switch:
            Gui.activateWorkbench(previous)
        if bar:
            bar.showMessage(why, 8000)
        return False
    if model is not None:
        model.remember(entry["name"])
    Gui.runCommand(entry["name"])
    return True


class PaletteDialog(QtWidgets.QDialog):
    def __init__(self, parent):
        super().__init__(parent, QtCore.Qt.Dialog | QtCore.Qt.FramelessWindowHint)
        self.setObjectName("CommandPalette")
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, False)
        self.model = Model()
        self.shown = []
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.edit = QtWidgets.QLineEdit(self)
        self.edit.setPlaceholderText("Search commands in every workbench...")
        self.edit.setClearButtonEnabled(False)
        self.list = QtWidgets.QListWidget(self)
        self.list.setObjectName("CommandPaletteList")
        self.list.setUniformItemSizes(True)
        self.list.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.list.setFocusPolicy(QtCore.Qt.NoFocus)
        lay.addWidget(self.edit)
        lay.addWidget(self.list)
        self.resize(640, 44 + MAX_ROWS * 26 + 24)
        self.edit.textChanged.connect(self.refill)
        self.edit.installEventFilter(self)
        self.list.viewport().installEventFilter(self)
        self.list.itemDoubleClicked.connect(lambda _item: self.accept_current())
        self._want_paint = False

    # -- content
    def refill(self, text=None):
        text = self.edit.text() if text is None else text
        self.shown = self.model.results(text)
        self.list.setUpdatesEnabled(False)
        self.list.clear()
        text, base = self.palette().color(QtGui.QPalette.Text), self.palette().color(QtGui.QPalette.Base)
        grey = QtGui.QColor((text.red() + base.red()) // 2, (text.green() + base.green()) // 2, (text.blue() + base.blue()) // 2)
        for e in self.shown:
            item = QtWidgets.QListWidgetItem("%s    -    %s" % (e["menu"], self.model.wb_label(e)))
            item.setToolTip("%s\n%s" % (e["name"], e["tip"]))
            if inactive_reason(e):
                item.setForeground(grey)
                item.setData(QtCore.Qt.UserRole + 1, True)
            self.list.addItem(item)
        if self.shown:
            self.list.setCurrentRow(0)
        self.list.setUpdatesEnabled(True)

    def selected(self):
        row = self.list.currentRow()
        return self.shown[row] if 0 <= row < len(self.shown) else None

    def accept_current(self):
        entry = self.selected()
        if entry is None:
            return
        self.hide()
        run_entry(entry, self.model)

    # -- events
    def eventFilter(self, obj, ev):
        t = ev.type()
        if obj is self.edit and t == QtCore.QEvent.KeyPress:
            k = ev.key()
            if k in (QtCore.Qt.Key_Down, QtCore.Qt.Key_Up):
                n = self.list.count()
                if n:
                    row = self.list.currentRow() + (1 if k == QtCore.Qt.Key_Down else -1)
                    self.list.setCurrentRow(max(0, min(n - 1, row)))
                return True
            if k in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                self.accept_current()
                return True
            if k == QtCore.Qt.Key_Escape:
                self.hide()
                return True
        elif obj is self.list.viewport() and t == QtCore.QEvent.Paint and self._want_paint:
            self._want_paint = False
            last_open["first_paint"] = time.perf_counter()
        return super().eventFilter(obj, ev)

    def keyPressEvent(self, ev):
        if ev.key() == QtCore.Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(ev)

    def open(self, query=""):
        self._want_paint = True
        self.model.refresh()
        self.edit.blockSignals(True)
        self.edit.setText(query)
        self.edit.blockSignals(False)
        self.refill(query)
        mw = self.parent()
        if mw is not None:
            g = mw.geometry()
            self.move(g.x() + (g.width() - self.width()) // 2, g.y() + 80)
        self.show()
        self.raise_()
        self.activateWindow()
        self.edit.setFocus()
        self.edit.selectAll()


def open_palette(query=""):
    """Open (or reopen) the palette; returns the dialog. The first call builds the model, later calls reuse it."""
    global _dialog
    last_open.clear()
    last_open["start"] = time.perf_counter()
    if _dialog is None:
        _dialog = PaletteDialog(Gui.getMainWindow())
    _dialog.open(query)
    return _dialog
