# SPDX-License-Identifier: LGPL-2.1-or-later
"""The PDM panels: connect, vault documents, check-in, revision browser, where-used and lifecycle.

Each panel is a plain QWidget that can be docked (``show_panel``) or grabbed to a PNG (``grab``). A panel
reads the vault through ``PDM.vault`` and acts through ``state``; errors go to ``dialogs`` (non-modal)."""

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

from PDM import vault as V

from . import dialogs, state

PANEL_SIZE = (560, 340)
_LIVE = {}   # kind -> (dock, widget)


def _table(headers, stretch_last=True):
    t = QtWidgets.QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
    t.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
    t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setStretchLastSection(stretch_last)
    t.setAlternatingRowColors(True)
    return t


def _fill(table, rows, widths=None):
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            item = QtWidgets.QTableWidgetItem(str(val))
            item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            table.setItem(r, c, item)
    for c, w in enumerate(widths or []):
        table.setColumnWidth(c, w)


def lock_text(name, refresh=False):
    st = state.lock_state(name, refresh=refresh)
    if st["info"] is None:
        return "not checked out"
    return "checked out by you" if st["mine"] else f"checked out by {st['holder']}"


class Panel(QtWidgets.QWidget):
    """Base: a title line, a status line, the body, and ``refresh``."""
    kind = "panel"
    title = "PDM"

    def __init__(self, name=None):
        super().__init__()
        self.name = name
        self.setObjectName("PDM_" + self.kind)
        self.layout_ = QtWidgets.QVBoxLayout(self)
        self.head = QtWidgets.QLabel()
        self.head.setObjectName("head")
        f = self.head.font()
        f.setBold(True)
        self.head.setFont(f)
        self.status = QtWidgets.QLabel()
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        self.layout_.addWidget(self.head)
        self.layout_.addWidget(self.status)

    def guarded(self, fn, title):
        """Run ``fn``; a vault error becomes a non-modal dialog and returns None."""
        try:
            return fn()
        except V.PDMError as e:
            dialogs.report(title, e)
        except Exception as e:  # noqa: BLE001  (a GUI action never raises into the Qt event loop)
            dialogs.error(title, f"{type(e).__name__}: {e}")
        return None

    def refresh(self):
        pass

    def changed(self):
        """An action changed the vault: refresh every open panel."""
        for _kind, (_dock, w) in list(_LIVE.items()):
            w.refresh()
        if self.kind not in _LIVE:
            self.refresh()

    def grab_png(self, path, size=PANEL_SIZE):
        """Save the panel as a PNG at a fixed size with a fixed style and no selection or focus state."""
        self.setStyle(QtWidgets.QStyleFactory.create("Fusion"))
        for t in self.findChildren(QtWidgets.QTableWidget):
            t.clearSelection()
            t.setCurrentItem(None)
        self.setFixedSize(*size)
        self.ensurePolished()
        self.layout().activate()
        self.show()
        for _ in range(4):
            QtWidgets.QApplication.processEvents()
        img = self.grab().toImage()
        img.save(path)
        self.hide()
        return img.width(), img.height()


class ConnectPanel(Panel):
    kind = "connect"
    title = "PDM: connect"

    def __init__(self, name=None):
        super().__init__()
        s = state.settings()
        self.head.setText("Connect to a vault")
        form = QtWidgets.QFormLayout()
        self.vault_edit = QtWidgets.QLineEdit(s["vault"])
        self.vault_edit.setObjectName("vault")
        self.wc_edit = QtWidgets.QLineEdit(s["workdir"])
        self.wc_edit.setObjectName("workdir")
        self.user_edit = QtWidgets.QLineEdit(s["user"])
        self.user_edit.setObjectName("user")
        form.addRow("Vault (bare repository)", self.vault_edit)
        form.addRow("Working copy folder", self.wc_edit)
        form.addRow("User name", self.user_edit)
        self.layout_.addLayout(form)
        row = QtWidgets.QHBoxLayout()
        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.connect_btn.setObjectName("connect")
        self.connect_btn.clicked.connect(self.do_connect)
        row.addStretch(1)
        row.addWidget(self.connect_btn)
        self.layout_.addLayout(row)
        self.layout_.addStretch(1)
        self.refresh()

    def do_connect(self):
        def go():
            v = state.connect(self.vault_edit.text().strip(), self.wc_edit.text().strip(), self.user_edit.text().strip())
            self.changed()
            return v
        self.guarded(go, "Connect to vault")

    def refresh(self):
        v = state.current_vault()
        self.status.setText(f"Connected as {v.user}; working copy {v.workdir}" if v else "Not connected.")


class DocumentsPanel(Panel):
    kind = "documents"
    title = "PDM: vault documents"

    def __init__(self, name=None):
        super().__init__()
        self.head.setText("Documents in the vault")
        self.table = _table(["Document", "Revision", "State", "Checked out by"])
        self.table.itemDoubleClicked.connect(lambda *_: self.do_open())
        self.layout_.addWidget(self.table, 1)
        self.next_rev = QtWidgets.QCheckBox("Start the next revision (needed for a Released document)")
        self.next_rev.setObjectName("next_revision")
        self.layout_.addWidget(self.next_rev)
        row = QtWidgets.QHBoxLayout()
        self.btn_refresh = QtWidgets.QPushButton("Refresh")
        self.btn_open = QtWidgets.QPushButton("Open read-only (no lock)")
        self.btn_open.setObjectName("open")
        self.btn_checkout = QtWidgets.QPushButton("Check out")
        self.btn_checkout.setObjectName("checkout")
        self.btn_discard = QtWidgets.QPushButton("Discard local edits and check out")
        self.btn_discard.setObjectName("discard")
        self.btn_discard.clicked.connect(self.do_discard)
        for b in (self.btn_refresh, self.btn_open, self.btn_checkout, self.btn_discard):
            row.addWidget(b)
        row.addStretch(1)
        self.layout_.addLayout(row)
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_open.clicked.connect(self.do_open)
        self.btn_checkout.clicked.connect(self.do_checkout)
        self.refresh()

    def selected(self):
        r = self.table.currentRow()
        return self.table.item(r, 0).text() if r >= 0 and self.table.item(r, 0) else None

    def refresh(self):
        v = state.current_vault()
        if v is None:
            self.status.setText("Not connected.")
            _fill(self.table, [])
            return
        try:
            docs, locks = v.documents(), v.locks()
        except V.PDMError as e:
            self.status.setText(str(e))
            return
        state.forget_locks()
        rows = []
        for n in sorted(docs):
            lk = locks.get(n)
            who = "" if lk is None else (lk["holder"] + (" (you)" if state.lock_state(n)["mine"] else ""))
            rows.append((n, docs[n]["revision"], docs[n]["state"], who))
        _fill(self.table, rows, [180, 80, 90])
        self.status.setText(f"{len(rows)} document(s)")

    def do_open(self):
        n = self.selected()
        if n:
            self.guarded(lambda: state.open_head(n), "Open document")

    def do_checkout(self):
        n = self.selected()
        if n:
            run_checkout(n, self.next_rev.isChecked(), self)

    def do_discard(self):
        n = self.selected()
        if n:
            ask_discard_checkout(n, self.next_rev.isChecked(), self)


def run_checkout(name, new_revision, panel, discard_local=False):
    """Check out through the library; show the library's refusal (naming the holder) and leave nothing open."""
    doc = panel.guarded(lambda: state.checkout(name, new_revision=new_revision, discard_local=discard_local),
                        "Check out")
    panel.changed()
    return doc


def ask_discard_checkout(name, new_revision, panel):
    """Confirm, then check out with the library's ``discard_local``: local edits in the working copy are lost."""
    dialogs.confirm("Discard local edits", f"Replace the local edits to {name} in your working copy with the "
                    "current vault revision and check it out? The local edits cannot be recovered.",
                    lambda: run_checkout(name, new_revision, panel, discard_local=True))


class CheckInPanel(Panel):
    kind = "checkin"
    title = "PDM: check in"

    def __init__(self, name):
        super().__init__(name)
        self.head.setText(f"Check in {name}")
        self.msg = QtWidgets.QPlainTextEdit()
        self.msg.setObjectName("message")
        self.msg.setPlaceholderText("What changed in this revision?")
        self.layout_.addWidget(QtWidgets.QLabel("Message"))
        self.layout_.addWidget(self.msg, 1)
        self.keep = QtWidgets.QCheckBox("Keep the document checked out")
        self.keep.setObjectName("keep_lock")
        self.layout_.addWidget(self.keep)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        self.btn = QtWidgets.QPushButton("Check in")
        self.btn.setObjectName("checkin")
        self.btn.clicked.connect(self.do_checkin)
        row.addWidget(self.btn)
        self.layout_.addLayout(row)
        self.refresh()

    def refresh(self):
        v = state.current_vault()
        if v is None:
            self.status.setText("Not connected.")
            return
        try:
            st = v.status(self.name)
            changed = "edited since check-out" if v.has_changes(self.name) else "no saved changes yet"
            self.status.setText(f"Revision {st['revision']} ({st['state']}); {lock_text(self.name)}; {changed}")
        except V.PDMError as e:
            self.status.setText(str(e))

    def do_checkin(self):
        text = self.msg.toPlainText().strip()
        if not text:
            dialogs.error("Check in", "Write a message describing this revision first.")
            return None
        rec = self.guarded(lambda: state.checkin(self.name, text, keep_lock=self.keep.isChecked()), "Check in")
        if rec is not None:
            self.msg.clear()
            note = " The open document was saved first." if rec.get("saved") else ""
            dialogs.info("Check in", f"{self.name} is now revision {rec['revision']}.{note}")
            self.changed()
        return rec


class RevisionsPanel(Panel):
    kind = "revisions"
    title = "PDM: revisions"

    def __init__(self, name):
        super().__init__(name)
        self.head.setText(f"Revisions of {name}")
        self.table = _table(["Revision", "State", "Author", "Time", "Message"])
        self.table.itemDoubleClicked.connect(lambda *_: self.do_open())
        self.layout_.addWidget(self.table, 1)
        self.next_rev = QtWidgets.QCheckBox("Start the next revision (needed for a Released document)")
        self.next_rev.setObjectName("next_revision")
        self.layout_.addWidget(self.next_rev)
        row = QtWidgets.QHBoxLayout()
        self.btn_open = QtWidgets.QPushButton("Open read-only")
        self.btn_open.setObjectName("open")
        self.btn_checkout = QtWidgets.QPushButton("Check out latest")
        self.btn_checkout.setObjectName("checkout")
        self.btn_checkout.setToolTip("Always checks out the newest revision, whichever row is selected")
        self.btn_discard = QtWidgets.QPushButton("Discard local edits and check out")
        self.btn_discard.setObjectName("discard")
        self.btn_discard.clicked.connect(lambda: ask_discard_checkout(self.name, self.next_rev.isChecked(), self))
        row.addWidget(self.btn_open)
        row.addWidget(self.btn_checkout)
        row.addWidget(self.btn_discard)
        row.addStretch(1)
        self.layout_.addLayout(row)
        self.btn_open.clicked.connect(self.do_open)
        self.btn_checkout.clicked.connect(self.do_checkout)
        self.refresh()

    def refresh(self):
        v = state.current_vault()
        if v is None:
            self.status.setText("Not connected.")
            return
        try:
            hist = v.history(self.name)
        except V.PDMError as e:
            self.status.setText(str(e))
            return
        self.records = hist
        _fill(self.table, [(r["revision"], r["state"], r["author"], r["time"].replace("T", " "), r["message"])
                           for r in hist], [70, 80, 80, 150])
        self.status.setText(f"{len(hist)} entries; {lock_text(self.name, refresh=True)}")

    def do_open(self):
        r = self.table.currentRow()
        if r < 0:
            return None
        rev = self.records[r]["revision"]
        return self.guarded(lambda: state.open_revision(self.name, rev), "Open revision")

    def do_checkout(self):
        return run_checkout(self.name, self.next_rev.isChecked(), self)


class WhereUsedPanel(Panel):
    kind = "whereused"
    title = "PDM: where used"

    def __init__(self, name):
        super().__init__(name)
        self.head.setText(f"Where {name} is used")
        self.table = _table(["Document", "Object", "Property"])
        self.table.itemDoubleClicked.connect(lambda *_: self.do_open())
        self.layout_.addWidget(self.table, 1)
        self.refresh()

    def refresh(self):
        v = state.current_vault()
        if v is None:
            self.status.setText("Not connected.")
            return
        try:
            used = v.where_used(self.name)
        except V.PDMError as e:
            self.status.setText(str(e))
            return
        self.used = list(used)
        _fill(self.table, [(u["document"], u["object"], u["property"]) for u in self.used], [200, 140])
        skipped = f"; {len(used.unreadable)} unreadable document(s) skipped" if used.unreadable else ""
        self.status.setText((f"{len(self.used)} link(s) from {len({u['document'] for u in self.used})} document(s)"
                             if self.used else "No document links to this one.") + skipped)

    def do_open(self):
        r = self.table.currentRow()
        if r < 0:
            return None
        doc_name = self.used[r]["document"]
        return self.guarded(lambda: state.open_head(doc_name), "Open document")


class LifecyclePanel(Panel):
    kind = "lifecycle"
    title = "PDM: lifecycle"

    def __init__(self, name):
        super().__init__(name)
        self.head.setText(f"Lifecycle of {name}")
        self.current = QtWidgets.QLabel()
        self.current.setObjectName("current")
        self.layout_.addWidget(self.current)
        self.msg = QtWidgets.QLineEdit()
        self.msg.setObjectName("message")
        self.msg.setPlaceholderText("Optional note for the history")
        self.layout_.addWidget(self.msg)
        self.buttons_box = QtWidgets.QHBoxLayout()
        self.layout_.addLayout(self.buttons_box)
        self.layout_.addStretch(1)
        self.buttons = {}
        self.refresh()

    def refresh(self):
        while self.buttons_box.count():   # buttons and the stretch: nothing from the last refresh survives
            item = self.buttons_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.buttons = {}
        v = state.current_vault()
        if v is None:
            self.status.setText("Not connected.")
            return
        try:
            st = v.status(self.name)
        except V.PDMError as e:
            self.status.setText(str(e))
            return
        self.current.setText(f"Revision {st['revision']}, state {st['state']}")
        allowed = V.TRANSITIONS[st["state"]]
        for target in allowed:
            b = QtWidgets.QPushButton(f"Move to {target}")
            b.setObjectName("to_" + target)
            b.clicked.connect(lambda _=False, t=target: self.do_transition(t))
            self.buttons[target] = b
            self.buttons_box.addWidget(b)
        self.buttons_box.addStretch(1)
        self.status.setText(f"{lock_text(self.name)}; " + ("allowed: " + ", ".join(allowed) if allowed
                                                         else "no further transitions"))

    def do_transition(self, target):
        rec = self.guarded(lambda: state.transition(self.name, target, self.msg.text().strip() or None),
                           "Lifecycle")
        if rec is not None:
            self.msg.clear()
            self.changed()
        return rec


KINDS = {c.kind: c for c in (ConnectPanel, DocumentsPanel, CheckInPanel, RevisionsPanel, WhereUsedPanel,
                             LifecyclePanel)}


def show_panel(kind, name=None):
    """Dock the panel ``kind`` (for document ``name``) at the right of the main window, replacing an older
    one of the same kind. Returns the widget."""
    old = _LIVE.pop(kind, None)
    if old is not None:
        old[0].close()
        old[0].deleteLater()
    cls = KINDS[kind]
    w = cls(name)
    mw = Gui.getMainWindow()
    dock = QtWidgets.QDockWidget(cls.title if name is None else f"{cls.title} ({name})", mw)
    dock.setObjectName("PDMDock_" + kind)
    dock.setWidget(w)
    mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
    dock.show()
    dock.raise_()
    _LIVE[kind] = (dock, w)
    dock.destroyed.connect(lambda *_: _LIVE.pop(kind, None) if _LIVE.get(kind, (None,))[0] is dock else None)
    return w


def current(kind):
    e = _LIVE.get(kind)
    return e[1] if e else None


def close_all():
    for kind in list(_LIVE):
        dock, _w = _LIVE.pop(kind)
        dock.close()
        dock.deleteLater()
