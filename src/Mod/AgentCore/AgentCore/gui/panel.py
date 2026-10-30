# SPDX-License-Identifier: LGPL-2.1-or-later
"""Change-set task panel: what the agent changed, with Accept and Reject for the human.

The form lists the change set in groups (features added, removed and modified, properties,
expressions, geometry per object, verification, transactions). Selecting a row selects that object in
the document, for inspection. While the panel is open, the 3D view shows added material in green and
removed material in red (``overlay.py``). Accept and Reject are the only paths to a decision: they call
the backend, which mints the token (``backends.py``). Closing the panel decides nothing; the turn stays
``proposed`` and the overlay goes away. The panel holds no token and no authority.

An undecided turn is never lost: ``pending()`` is a per-session registry of the turns still ``proposed``,
``review_pending()`` (the ``AgentCore_ReviewPending`` command) reopens the newest, and a console warning
names any undecided turn when the panel is closed without a decision, or its document is saved or closed.
"""

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

from ..diff.authority import TokenRefused
from ..diff.turn import TurnError
from . import model, overlay
from .backends import LocalTurnBackend, RemoteTurnBackend, StaleChangeSet

_CURRENT = None
_SHOWN = None     # the panel whose task dialog is open (ours), until it closes
_CLOSING = False  # our deferred closeDialog is queued
_GEN = 0          # bumped on every show, so a queued close never hits a newer dialog
_PENDING = []     # backends of reviewed turns that have no decision yet, oldest first
_WATCH = []
REFUSALS = (StaleChangeSet, TurnError, TokenRefused)
PANEL_SIZE = (420, 600)


def current():
    return _CURRENT


def _warn(text):
    App.Console.PrintWarning("AgentCore: " + text + "\n")


def _alive(backend):
    if backend.status() != "proposed":
        return False
    return backend.kind != "local" or backend.document in App.listDocuments()


def pending():
    """Backends of turns still waiting for a human decision, oldest first."""
    _PENDING[:] = [b for b in _PENDING if _alive(b)]
    return list(_PENDING)


def _register(backend):
    if all(b.turn_id != backend.turn_id for b in _PENDING):
        _PENDING.append(backend)
    if not _WATCH:
        _WATCH.append(_DocWatch())
        App.addDocumentObserver(_WATCH[0])


def _forget(backend):
    _PENDING[:] = [b for b in _PENDING if b is not backend]


def _reset():
    """Test hook: empty the registry and the dialog bookkeeping."""
    global _CURRENT, _SHOWN, _CLOSING
    _PENDING.clear()
    _CURRENT = _SHOWN = None
    _CLOSING = False


def _undecided_text(backends):
    return ", ".join(f"'{b.label}' on '{b.document}'" for b in backends) + \
        " (reopen with Gui.runCommand('AgentCore_ReviewPending'))"


class _DocWatch:
    """Warn once when a document with an undecided turn is saved or closed."""

    def slotStartSaveDocument(self, doc, _filename=None):
        mine = [b for b in pending() if b.document == doc.Name]
        if mine:
            _warn("saving a document with agent changes nobody has accepted or rejected: " + _undecided_text(mine))

    def slotDeletedDocument(self, doc=None, *_):
        try:
            name = doc.Name
        except Exception:
            name = None
        gone = [b for b in _PENDING if b.kind == "local" and b.status() == "proposed"
                and (b.document == name or b.document not in App.listDocuments())]
        if gone:
            _warn("document closed with undecided agent changes: " + _undecided_text(gone))
            _PENDING[:] = [b for b in _PENDING if b not in gone]


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
        self._warned = False
        self.message = ""
        self._rows = model.build_rows(backend.change_set())
        self.overlay = overlay.Overlay(backend.overlay_pieces()) if with_overlay else None
        self._build_form()
        _register(backend)
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
        except REFUSALS as exc:   # nothing was rejected: the turn stays proposed and reviewable
            self._say(f"Reject refused: {exc}", ok=False)
            return
        except Exception as exc:
            self._say(f"Reject failed: {type(exc).__name__}: {exc}", ok=False)
            raise
        self._decided("rejected", res)

    def _decided(self, what, result):
        self.decision, self.result = what, result
        _forget(self.backend)
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
        self._warn_undecided()
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
        global _CURRENT, _SHOWN
        if _CURRENT is self:
            _CURRENT = None
        if _SHOWN is self:
            _SHOWN = None

    def _close(self):
        self._detach()
        mine = _CURRENT is self
        self._close_dialog_state()
        if mine:   # not inside the button's own click handler: the form is deleted with the dialog
            global _CLOSING
            _CLOSING = True
            gen = _GEN

            def later():
                global _CLOSING
                _CLOSING = False
                if gen == _GEN and Gui.Control.activeDialog():   # only our own dialog, never a newer one
                    Gui.Control.closeDialog()
            QtCore.QTimer.singleShot(0, later)

    def _warn_undecided(self):
        if self.decision is None and not self._warned and _alive(self.backend):
            self._warned = True
            _warn("review closed without a decision, the agent's changes are still live: "
                  + _undecided_text([self.backend]))

    def _on_destroyed(self, *_):
        self._detach()
        self._warn_undecided()
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


def _show(backend, make):
    """Open ``make()``'s panel unless a foreign task dialog is open (then the review stays pending)."""
    global _CURRENT, _SHOWN, _GEN
    _register(backend)
    if Gui.Control.activeDialog():
        if _SHOWN is None and not _CLOSING:
            _warn(f"another task dialog is open, so the review of '{backend.label}' was not opened; it stays "
                  "pending: finish that dialog, then Gui.runCommand('AgentCore_ReviewPending')")
            return None
        Gui.Control.closeDialog()   # ours: its turn is already in the registry, nothing is lost
    panel = make()
    _GEN += 1
    _CURRENT = _SHOWN = panel
    Gui.Control.showDialog(panel)
    panel.attach_overlay()
    return panel


def review(turn, with_overlay=True):
    """Open the panel for a proposed in-process turn. The turn's document must have a 3D view.
    Returns None, with the turn left pending, when another task dialog is open."""
    backend = LocalTurnBackend(turn)
    return _show(backend, lambda: ChangeSetPanel(backend, with_overlay))


def review_remote(turn_id, fetch_change_set, label=None):
    """Open the panel for a turn running in an F4 worker. ``fetch_change_set()`` returns its
    current change set; Accept and Reject go to the GUI's approver, which the worker polls."""
    backend = RemoteTurnBackend(turn_id, fetch_change_set, label)
    return _show(backend, lambda: ChangeSetPanel(backend, with_overlay=False))


def review_pending(with_overlay=True):
    """Reopen the panel for the newest undecided turn (the AgentCore_ReviewPending command)."""
    todo = pending()
    if not todo:
        _warn("no agent change set is waiting for a decision")
        return None
    backend = todo[-1]
    if _CURRENT is not None and _CURRENT.backend is backend:
        return _CURRENT
    return _show(backend, lambda: ChangeSetPanel(backend, with_overlay and backend.kind == "local"))
