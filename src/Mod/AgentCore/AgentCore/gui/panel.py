# SPDX-License-Identifier: LGPL-2.1-or-later
"""Change-set task panel: what the agent changed, with Accept and Reject for the human.

The form lists the change set in groups (features added, removed and modified, properties,
expressions, geometry per object, verification, transactions). Selecting a row selects that object in
the document, for inspection. While the panel is open, the 3D view shows added material in green and
removed material in red (``overlay.py``). Accept and Reject are the only paths to a decision: they call
the backend, which mints the token (``backends.py``). Closing the panel decides nothing; the turn stays
``proposed`` and the overlay goes away. The panel holds no token and no authority.
"""

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

from ..diff.authority import TokenRefused
from ..diff.turn import TurnError
from . import model, overlay
from .backends import LocalTurnBackend, RemoteTurnBackend, StaleChangeSet

_CURRENT = None
REFUSALS = (StaleChangeSet, TurnError, TokenRefused)
PANEL_SIZE = (420, 600)


def current():
    return _CURRENT


def _legend(text, color):
    lab = QtWidgets.QLabel(text)
    r, g, b = (int(c * 255) for c in color)
    lab.setStyleSheet(f"background-color: rgb({r},{g},{b}); color: white; padding: 2px 6px;")
    return lab


class ChangeSetPanel:
    """A FreeCAD task dialog (duck-typed) around a backend."""

    def __init__(self, backend, with_overlay=True):
        self.backend = backend
        self.decision = None
        self.message = ""
        self._rows = model.build_rows(backend.change_set())
        self.overlay = overlay.Overlay(backend.overlay_pieces()) if with_overlay else None
        self._build_form()
        self.form.destroyed.connect(self._on_destroyed)

    # ------------------------------------------------------------ form
    def _build_form(self):
        cs = self.backend.change_set()
        form = QtWidgets.QWidget()
        form.setObjectName("AgentCoreChangeSetPanel")
        form.setWindowTitle("Agent change set")
        lay = QtWidgets.QVBoxLayout(form)
        self.title = QtWidgets.QLabel(self.backend.label)   # the turn id is random: tooltip only
        self.title.setToolTip(self.backend.turn_id)
        self.title.setWordWrap(True)
        lay.addWidget(self.title)
        legend = QtWidgets.QHBoxLayout()
        legend.addWidget(_legend("added material", overlay.COLORS["added"]))
        legend.addWidget(_legend("removed material", overlay.COLORS["removed"]))
        legend.addStretch(1)
        lay.addLayout(legend)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setFocusPolicy(QtCore.Qt.NoFocus)
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(14)
        self.groups = {}
        for key in model.GROUPS:
            rows = self._rows[key]
            top = QtWidgets.QTreeWidgetItem([f"{model.TITLES[key]} ({len(rows)})"])
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)
            for r in rows:
                child = QtWidgets.QTreeWidgetItem([r["text"]])
                child.setData(0, QtCore.Qt.UserRole, r["object"])
                child.setToolTip(0, r["text"])
                top.addChild(child)
            self.tree.addTopLevelItem(top)
            self.groups[key] = top
            top.setExpanded(bool(rows) and key != "transactions")
        self.tree.itemSelectionChanged.connect(self._on_row_selected)
        lay.addWidget(self.tree, 1)
        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        buttons = QtWidgets.QHBoxLayout()
        self.accept_button = QtWidgets.QPushButton("Accept")
        self.reject_button = QtWidgets.QPushButton("Reject")
        for b in (self.accept_button, self.reject_button):
            b.setFocusPolicy(QtCore.Qt.NoFocus)
            buttons.addWidget(b)
        self.accept_button.setToolTip("Keep these changes as one undo step")
        self.reject_button.setToolTip("Restore the document to its state before the turn")
        self.accept_button.clicked.connect(self._on_accept)
        self.reject_button.clicked.connect(self._on_reject)
        lay.addLayout(buttons)
        self.form = form

    def row_counts(self):
        return {k: len(v) for k, v in self._rows.items()}

    def _item(self, group, index):
        return self.groups[group].child(index)

    # ------------------------------------------------------------ selection
    def _on_row_selected(self):
        items = self.tree.selectedItems()
        name = items[0].data(0, QtCore.Qt.UserRole) if items else None
        doc = App.getDocument(self.backend.document) if self.backend.document in App.listDocuments() else None
        Gui.Selection.clearSelection()
        if name and doc is not None and doc.getObject(name) is not None:
            Gui.Selection.addSelection(doc.Name, name)

    # ------------------------------------------------------------ decisions
    def _say(self, text, ok=True):
        self.message = text
        self.status.setStyleSheet("color: " + ("rgb(20,110,30)" if ok else "rgb(170,20,20)"))
        self.status.setText(text)

    def _on_accept(self):
        if self.decision is not None:
            return
        try:
            res = self.backend.accept()
        except REFUSALS as exc:
            self._say(f"Accept refused: {exc}", ok=False)
            return
        self._decided("accepted", res)

    def _on_reject(self):
        if self.decision is not None:
            return
        try:
            res = self.backend.reject()
        except Exception as exc:
            self._say(f"Reject failed: {type(exc).__name__}: {exc}", ok=False)
            raise
        self._decided("rejected", res)

    def _decided(self, what, result):
        self.decision, self.result = what, result
        self.form.setEnabled(False)
        self._say(what.capitalize() + ".")
        self._close()

    # ------------------------------------------------------------ task dialog protocol
    def getStandardButtons(self):
        close = QtWidgets.QDialogButtonBox.StandardButton.Close
        return int(getattr(close, "value", close))

    def reject(self):   # the Close button: decides nothing
        self._detach()
        self._close_dialog_state()
        return True

    def accept(self):
        return self.reject()

    def isAllowedAlterDocument(self):
        return True

    def isAllowedAlterView(self):
        return True

    def isAllowedAlterSelection(self):
        return True

    # ------------------------------------------------------------ overlay and lifetime
    def attach_overlay(self):
        if self.overlay is None:
            return False
        view = Gui.getDocument(self.backend.document).ActiveView if self.backend.document in App.listDocuments() \
            and Gui.getDocument(self.backend.document) is not None else None
        return bool(view is not None and self.overlay.attach(view))

    def _detach(self):
        if self.overlay is not None:
            self.overlay.detach()

    def _close_dialog_state(self):
        global _CURRENT
        if _CURRENT is self:
            _CURRENT = None

    def _close(self):
        self._detach()
        mine = _CURRENT is self
        self._close_dialog_state()
        if mine:   # not inside the button's own click handler: the form is deleted with the dialog
            QtCore.QTimer.singleShot(0, lambda: Gui.Control.activeDialog() and Gui.Control.closeDialog())

    def _on_destroyed(self, *_):
        self._detach()
        self._close_dialog_state()

    # ------------------------------------------------------------ capture (baselines)
    def grab(self, path, size=PANEL_SIZE):
        """Save the form as a PNG at a fixed size, with no focus-dependent state."""
        form = self.form
        self.tree.clearSelection()
        self.tree.setCurrentItem(None)
        form.setStyle(QtWidgets.QStyleFactory.create("Fusion"))
        form.setFixedSize(*size)
        form.ensurePolished()
        form.layout().activate()
        QtWidgets.QApplication.processEvents()
        img = form.grab().toImage()
        img.save(path)
        return img.width(), img.height()


def _show(panel):
    global _CURRENT
    if Gui.Control.activeDialog():
        Gui.Control.closeDialog()
    _CURRENT = panel
    Gui.Control.showDialog(panel)
    panel.attach_overlay()
    return panel


def review(turn, with_overlay=True):
    """Open the panel for a proposed in-process turn. The turn's document must have a 3D view."""
    return _show(ChangeSetPanel(LocalTurnBackend(turn), with_overlay))


def review_remote(turn_id, fetch_change_set, label=None):
    """Open the panel for a turn running in an F4 worker. ``fetch_change_set()`` returns its
    current change set; Accept and Reject go to the GUI's approver, which the worker polls."""
    return _show(ChangeSetPanel(RemoteTurnBackend(turn_id, fetch_change_set, label), with_overlay=False))
