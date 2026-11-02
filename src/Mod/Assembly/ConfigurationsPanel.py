# SPDX-License-Identifier: LGPL-2.1-or-later
"""Task panel for assembly configurations (cad-fork G06 slice 5b). Imported only with the GUI up.

The panel lists the configurations of one assembly (the assembly's own values come first, as "Default"), marks
the active one, and offers the slice 5a operations: Activate, Add, Rename, Copy, Delete, Capture and Discard.
For the selected configuration it shows what the configuration overrides (suppressed components, alternative
parts, joint values, suppressed features) and, for the active one, the edits made since it was activated
(drift). Every action goes through ``AssemblyConfiguration``; nothing is computed here.

Transaction model. Each action is committed as its own named undo step at the moment it happens (the 5a calls
open and commit their own transaction; Rename is wrapped here). No transaction stays open while the panel is
open, so a save writes the real state and the user's other edits, each its own step, are never swept in. The
panel records the undo steps it made. OK keeps them. Cancel takes back exactly what the panel did: while the top of the
undo stack is a step the panel made (same name, same count) it is undone, which restores even the edits that
Capture or Discard consumed. If the user's own edit sits on top of the stack that edit must survive, so the rest is
reverted through the API instead: restore the stored overrides, bring back deleted configurations, take away added
ones, restore names and re-activate the configuration that was active when the panel opened. Edits that Capture or
Discard consumed below such an interleaved edit cannot be restored (drift is not reconstructible by the API).

Switching away from a configuration with drift is never silent: the drifted properties are listed and the user
chooses Capture, Discard or Cancel. The questions the panel asks (a name, the drift choice, a confirmation) are
module-level functions (``ask_name``, ``ask_new``, ``ask_drift``) that tests replace.

Behaviour sources: the slice 5a API; Qt widget documentation; FreeCAD's task dialog and document observer
interfaces. Wording and layout are this project's own.
"""

import json

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import AssemblyConfiguration as AC

translate = App.Qt.translate

DEFAULT_LABEL = "Default"
CAPTURABLE = ("joint_value", "feature", "alternative")


# ----------------------------------------------------------------- questions

def ask_name(parent, title, text, default=""):
    """Ask for a name; None when cancelled. Replaceable for tests."""
    value, ok = QtWidgets.QInputDialog.getText(parent, title, text, QtWidgets.QLineEdit.Normal, default)
    value = value.strip()
    return value if ok and value else None


def ask_new(parent, assembly, default):
    """Ask for the name and the components to suppress of a new configuration; None when cancelled."""
    dlg = QtWidgets.QDialog(parent)
    dlg.setWindowTitle(translate("Assembly", "New configuration"))
    lay = QtWidgets.QVBoxLayout(dlg)
    lay.addWidget(QtWidgets.QLabel(translate("Assembly", "Name")))
    name = QtWidgets.QLineEdit(default)
    lay.addWidget(name)
    lay.addWidget(QtWidgets.QLabel(translate("Assembly", "Components to suppress")))
    lst = QtWidgets.QListWidget()
    for p in AC._paths(assembly):
        item = QtWidgets.QListWidgetItem(p)
        item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
        item.setCheckState(QtCore.Qt.Unchecked)
        lst.addItem(item)
    lay.addWidget(lst)
    box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    box.accepted.connect(dlg.accept)
    box.rejected.connect(dlg.reject)
    lay.addWidget(box)
    if not dlg.exec_() or not name.text().strip():
        return None
    return name.text().strip(), [lst.item(i).text() for i in range(lst.count())
                                 if lst.item(i).checkState() == QtCore.Qt.Checked]


def ask_drift(parent, drift, target):
    """The active configuration has edits that a switch would lose: "capture", "discard" or "cancel".
    Replaceable for tests."""
    capturable = all(d["kind"] in CAPTURABLE for d in drift)
    box = QtWidgets.QMessageBox(parent)
    box.setIcon(QtWidgets.QMessageBox.Warning)
    box.setWindowTitle(translate("Assembly", "Edits not captured"))
    box.setText(translate("Assembly", "The active configuration has edits that are not captured."))
    box.setInformativeText(translate("Assembly", "%s\n\nSwitching to %s needs a decision.") % (
        drift_text(drift), target or DEFAULT_LABEL))
    cap = box.addButton(translate("Assembly", "Capture"), QtWidgets.QMessageBox.AcceptRole)
    cap.setEnabled(capturable)
    dis = box.addButton(translate("Assembly", "Discard"), QtWidgets.QMessageBox.DestructiveRole)
    box.addButton(QtWidgets.QMessageBox.Cancel)
    box.setDefaultButton(QtWidgets.QMessageBox.Cancel)
    box.exec_()
    return "capture" if box.clickedButton() is cap else "discard" if box.clickedButton() is dis else "cancel"


def drift_text(drift):
    return "\n".join("%s.%s: %s -> %s" % (d["target"], d["property"], d["expected"], d["found"]) for d in drift)


# ------------------------------------------------------------------ observer

class _Observer:
    """Tells the panel that something in its document changed, so the list can be refreshed (deferred)."""

    def __init__(self, panel):
        self.panel = panel

    def _hit(self, doc_or_obj):
        doc = getattr(doc_or_obj, "Document", doc_or_obj)
        if getattr(doc, "Name", None) == self.panel.document_name:
            self.panel.schedule_refresh()

    def slotCreatedObject(self, obj):
        self._hit(obj)

    def slotDeletedObject(self, obj):
        self._hit(obj)

    def slotChangedObject(self, obj, prop):
        if prop in ("Label", "Overrides", "Active", "Applied", "Group", "Suppressed", "Distance", "Angle"):
            self._hit(obj)

    def slotUndoDocument(self, doc):
        self._hit(doc)

    def slotRedoDocument(self, doc):
        self._hit(doc)


# --------------------------------------------------------------------- panel

class TaskConfigurations:
    """The Configurations panel of ``assembly``."""

    def __init__(self, assembly):
        if Gui.Control.activeDialog():   # before anything touches the document
            raise RuntimeError("another task dialog is already open")
        self.assembly = assembly
        self.doc = assembly.Document
        self.document_name = self.doc.Name
        self.done = False
        self.steps = []             # (undo name, undo count after it) of every step the panel made, oldest first
        self.snapshot = self._take_snapshot()
        self._pending = False
        self._busy = False
        self.parent = None

        self.form = QtWidgets.QWidget()
        self.form.setObjectName("Assembly_TaskConfigurations")
        self.form.setWindowTitle(translate("Assembly", "Configurations"))
        lay = QtWidgets.QVBoxLayout(self.form)
        self.list = QtWidgets.QListWidget()
        self.list.setObjectName("configurations")
        self.list.setMinimumHeight(90)
        self.list.setMaximumHeight(150)
        self.list.setAccessibleName(translate("Assembly", "Configurations"))
        lay.addWidget(self.list)

        row1 = QtWidgets.QHBoxLayout()
        row2 = QtWidgets.QHBoxLayout()
        self.buttons = {}
        for lay_row, specs in ((row1, (("activate", "Activate"), ("add", "Add..."), ("rename", "Rename..."),
                                       ("copy", "Copy..."))),
                               (row2, (("delete", "Delete"), ("capture", "Capture"), ("discard", "Discard")))):
            for key, text in specs:
                b = QtWidgets.QPushButton(translate("Assembly", text))
                b.setObjectName("btn_" + key)
                self.buttons[key] = b
                lay_row.addWidget(b)
        lay.addLayout(row1)
        lay.addLayout(row2)

        self.buttons["activate"].clicked.connect(lambda *_: self.activate())
        self.buttons["add"].clicked.connect(lambda *_: self.add())
        self.buttons["rename"].clicked.connect(lambda *_: self.rename())
        self.buttons["copy"].clicked.connect(lambda *_: self.copy())
        self.buttons["delete"].clicked.connect(lambda *_: self.delete())
        self.buttons["capture"].clicked.connect(lambda *_: self.capture())
        self.buttons["discard"].clicked.connect(lambda *_: self.discard())
        self.buttons["activate"].setToolTip(translate("Assembly", "Make the selected configuration the active one"))
        self.buttons["add"].setToolTip(translate("Assembly", "Add a configuration"))
        self.buttons["rename"].setToolTip(translate("Assembly", "Rename the selected configuration"))
        self.buttons["copy"].setToolTip(translate("Assembly", "Copy the selected configuration"))
        self.buttons["delete"].setToolTip(translate("Assembly", "Delete the selected configuration"))
        self.buttons["capture"].setToolTip(translate(
            "Assembly", "Take the edits made since activation into the active configuration"))
        self.buttons["discard"].setToolTip(translate(
            "Assembly", "Put the active configuration's values back over the edits made since activation"))

        self.summary = QtWidgets.QLabel()
        self.summary.setObjectName("summary")
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        self.error = QtWidgets.QLabel()
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        self.error.setStyleSheet("background-color: #b00020; color: white; padding: 2px 6px;")
        self.error.hide()
        lay.addWidget(self.error)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setObjectName("overrides")
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels([translate("Assembly", "Overrides"), translate("Assembly", "Value")])
        self.tree.setRootIsDecorated(True)
        self.tree.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.tree.setMinimumHeight(110)
        self.tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.tree.header().setStretchLastSection(True)
        self.tree.setAccessibleName(translate("Assembly", "Overrides of the selected configuration"))
        lay.addWidget(self.tree, 1)

        self.list.currentItemChanged.connect(lambda *_: self._selection_changed())
        self.list.itemDoubleClicked.connect(lambda *_: self.activate())
        self.observer = _Observer(self)
        App.addDocumentObserver(self.observer)
        self._observing = True
        self.form.destroyed.connect(self._form_gone)
        self.refresh()

    # ---- state read

    def _alive(self):
        return self.document_name in App.listDocuments() and self.assembly.Document is not None

    def _status(self):
        return AC.status(self.assembly)

    def selected(self):
        """Label of the selected configuration, "" for the default row, None when nothing is selected."""
        item = self.list.currentItem()
        return None if item is None else item.data(QtCore.Qt.UserRole)

    def labels(self):
        return [c.Label for c in AC.configurations(self.assembly)]

    def rows(self):
        """(label, is active) of every list row, the default row first (label "")."""
        return [(self.list.item(i).data(QtCore.Qt.UserRole), bool(self.list.item(i).data(QtCore.Qt.UserRole + 1)))
                for i in range(self.list.count())]

    def select(self, label):
        """Select the row of ``label`` (None or "" for the default row)."""
        want = label or ""
        for i in range(self.list.count()):
            if self.list.item(i).data(QtCore.Qt.UserRole) == want:
                self.list.setCurrentRow(i)
                return True
        return False

    # ---- refresh

    def schedule_refresh(self):
        if self._pending or self._busy or self.done:
            return
        self._pending = True
        QtCore.QTimer.singleShot(0, self._deferred_refresh)

    def _deferred_refresh(self):
        self._pending = False
        if not self.done and self._alive():
            try:
                self.refresh()
            except Exception:  # noqa: BLE001  (the document is going away)
                pass

    def refresh(self):
        """Rebuild the list, the buttons and the overrides view from the document."""
        st = self._status()
        keep = self.selected()
        self.list.blockSignals(True)
        self.list.clear()
        rows = [("", st["active"] is None)] + [(c.Label, c.Label == st["active"]) for c in AC.configurations(self.assembly)]
        for label, is_active in rows:
            text = (translate("Assembly", DEFAULT_LABEL) if label == "" else label) + (
                "  " + translate("Assembly", "(active)") if is_active else "")
            item = QtWidgets.QListWidgetItem(text)
            item.setData(QtCore.Qt.UserRole, label)
            item.setData(QtCore.Qt.UserRole + 1, is_active)
            if is_active:
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            self.list.addItem(item)
        self.list.blockSignals(False)
        if keep is None or not self.select(keep):
            self.select(st["active"] or "")
        self._selection_changed()

    def _selection_changed(self):
        if not self._alive():
            return
        st = self._status()
        sel = self.selected()
        real = bool(sel)
        is_active = real and sel == st["active"]
        drift = st["drift"]
        b = self.buttons
        b["activate"].setEnabled(sel is not None and (sel or None) != st["active"])
        b["add"].setEnabled(True)
        b["rename"].setEnabled(real)
        b["copy"].setEnabled(real)
        b["delete"].setEnabled(real)
        b["capture"].setEnabled(bool(drift) and all(d["kind"] in CAPTURABLE for d in drift))
        b["discard"].setEnabled(bool(drift))
        if drift and not b["capture"].isEnabled():
            b["capture"].setToolTip(translate("Assembly", "Placement and expression edits cannot be captured; discard them"))
        self._fill_overrides(sel, st, is_active)
        self.summary.setText(self._summary(st))

    def _summary(self, st):
        if st["active"] is None:
            text = translate("Assembly", "No configuration is active: the assembly has its own values.")
        else:
            text = translate("Assembly", "Active: %s.") % st["active"]
        if st["drift"]:
            text += " " + translate("Assembly", "%d edit(s) since activation are not captured.") % len(st["drift"])
        return text

    def _fill_overrides(self, sel, st, is_active):
        self.tree.clear()
        if not sel:
            top = QtWidgets.QTreeWidgetItem([translate("Assembly", "The assembly's own values"), ""])
            self.tree.addTopLevelItem(top)
            return
        cfg = AC._find(self.assembly, sel)
        ov = json.loads(cfg.Overrides)

        def section(title, pairs):
            if not pairs:
                return
            top = QtWidgets.QTreeWidgetItem([title, str(len(pairs))])
            self.tree.addTopLevelItem(top)
            for k, v in pairs:
                top.addChild(QtWidgets.QTreeWidgetItem([k, v]))
            top.setExpanded(True)

        section(translate("Assembly", "Suppressed components"), [(p, translate("Assembly", "suppressed")) for p in ov["suppress"]])
        section(translate("Assembly", "Alternative parts"), [(p, v) for p, v in sorted(ov["alternatives"].items())])
        section(translate("Assembly", "Joint values"), [("%s.%s" % (j, p), _val(v))
                                                         for j, vals in sorted(ov["joints"].items()) for p, v in sorted(vals.items())])
        section(translate("Assembly", "Features"), [(f, translate("Assembly", "suppressed") if v else translate("Assembly", "active"))
                                                     for f, v in sorted(ov["features"].items())])
        if not self.tree.topLevelItemCount():
            self.tree.addTopLevelItem(QtWidgets.QTreeWidgetItem([translate("Assembly", "Overrides nothing"), ""]))
        if is_active and st["drift"]:
            section(translate("Assembly", "Edited since activation"),
                    [("%s.%s" % (d["target"], d["property"]), "%s -> %s" % (_val(d["expected"]), _val(d["found"])))
                     for d in st["drift"]])

    def _message(self, text):
        self.error.setText(text)
        self.error.setVisible(bool(text))

    # ---- one action = one undo step

    def _act(self, fn):
        """Run ``fn`` (which commits its own transaction) and note the undo steps it added."""
        before = self.doc.UndoCount
        self._busy = True
        try:
            result = fn()
        finally:
            self._busy = False
        added = self.doc.UndoCount - before
        if added > 0:
            names = list(self.doc.UndoNames)
            for k in range(added - 1, -1, -1):          # oldest of the new steps first
                self.steps.append((names[k], before + (added - k)))
        self._message("")
        self.refresh()
        return result

    def _try(self, fn):
        """Run an action; show an API refusal in the panel instead of raising. Returns (ok, result)."""
        try:
            return True, self._act(fn)
        except AC.ConfigurationError as exc:
            self._message(str(exc))
            self.refresh()
            return False, None

    def _target(self, name):
        return self.selected() if name is None else name

    def activate(self, name=None):
        """Activate ``name`` (default: the selected row; "" or "default" is the assembly's own values).

        Edits to overridden properties (drift) in the configuration being left are shown first: Capture them
        into it, Discard them, or Cancel the switch. Returns True when the switch happened."""
        name = self._target(name)
        if name is None:
            return False
        target = None if name in ("", "default") else name
        if target is not None and target == self._status()["active"]:
            return False
        drift = self._status()["drift"]
        if drift:
            choice = ask_drift(self.form, drift, target)
            if choice == "cancel":
                self._message(translate("Assembly", "Switch cancelled: the edits are still there."))
                return False
            ok, _ = self._try(lambda: AC.capture(self.assembly) if choice == "capture" else AC.discard(self.assembly))
            if not ok:
                return False
        ok, _ = self._try(lambda: AC.activate(self.assembly, target))
        return ok

    def add(self, name=None, suppress=None):
        """Add a configuration. With no ``name`` the user is asked for it and the components to suppress."""
        if name is None:
            got = ask_new(self.form, self.assembly, self._fresh_name("Configuration"))
            if got is None:
                return None
            name, suppress = got
        ok, res = self._try(lambda: AC.create(self.assembly, name, suppress=suppress or ()))
        if ok:
            self.select(name)
        return res if ok else None

    def _fresh_name(self, base):
        names = set(self.labels())
        n = 1
        while "%s %d" % (base, n) in names:
            n += 1
        return "%s %d" % (base, n)

    def rename(self, new_name=None, name=None):
        name = self._target(name)
        if not name:
            return False
        if new_name is None:
            new_name = ask_name(self.form, translate("Assembly", "Rename configuration"),
                                translate("Assembly", "New name"), name)
            if new_name is None:
                return False

        def go():
            self.doc.openTransaction("Rename configuration")
            try:
                AC.rename(self.assembly, name, new_name)
            except Exception:
                self.doc.abortTransaction()
                raise
            self.doc.commitTransaction()

        ok, _ = self._try(go)
        if ok:
            self.select(new_name)
        return ok

    def copy(self, new_name=None, name=None):
        name = self._target(name)
        if not name:
            return None
        if new_name is None:
            new_name = ask_name(self.form, translate("Assembly", "Copy configuration"),
                                translate("Assembly", "Name of the copy"), self._fresh_name(name))
            if new_name is None:
                return None
        ok, res = self._try(lambda: AC.copy(self.assembly, name, new_name))
        if ok:
            self.select(new_name)
        return res if ok else None

    def delete(self, name=None):
        """Delete a configuration. The active one is deactivated first (the assembly returns to its own values);
        if it has edits that were not captured the user must agree to throw them away, else nothing happens."""
        name = self._target(name)
        if not name:
            return False
        st = self._status()
        if name == st["active"]:
            if st["drift"]:
                import ViewProviderAssemblyConfiguration as VPC

                cfg = AC._find(self.assembly, name)
                if not VPC.ask_discard_and_delete(cfg, st["drift"]):
                    self._message(translate("Assembly", "Not deleted: the active configuration has edits that were not captured."))
                    return False
                ok, _ = self._try(lambda: AC.discard(self.assembly))
                if not ok:
                    return False
            ok, _ = self._try(lambda: AC.activate(self.assembly, None))
            if not ok:
                return False
        ok, _ = self._try(lambda: AC.delete(self.assembly, name))
        return ok

    def capture(self):
        """Take the edits made since activation into the active configuration."""
        if not self._status()["drift"]:
            return False
        ok, _ = self._try(lambda: AC.capture(self.assembly))
        return ok

    def discard(self):
        """Put the active configuration's values back over the edits made since activation."""
        if not self._status()["drift"]:
            return False
        ok, _ = self._try(lambda: AC.discard(self.assembly))
        return ok

    # ---- Cancel

    def _take_snapshot(self):
        st = AC.status(self.assembly)
        return {"active": st["active"],
                "configs": {c.Name: {"label": c.Label, "overrides": c.Overrides, "defaults": c.Defaults,
                                     "applied": c.Applied, "active": bool(c.Active)}
                            for c in AC.configurations(self.assembly)}}

    def _undo_own_steps(self):
        """Undo the panel's own steps while they are on top of the undo stack. Returns the steps left."""
        while self.steps:
            name, count = self.steps[-1]
            names = list(self.doc.UndoNames)
            if self.doc.UndoCount != count or not names or names[0] != name:
                break
            self.doc.undo()
            self.steps.pop()
        return len(self.steps)

    def _converge(self):
        """Revert what is left through the API (an unrelated edit sits above the panel's steps)."""
        snap, asm, doc = self.snapshot, self.assembly, self.doc
        problems = []
        by_name = {c.Name: c for c in AC.configurations(asm)}
        doc.openTransaction("Cancel configurations")
        try:
            for nm, rec in snap["configs"].items():
                c = by_name.get(nm)
                if c is not None:
                    if c.Overrides != rec["overrides"]:
                        c.Overrides = rec["overrides"]
                    if c.Label != rec["label"]:
                        c.Label = rec["label"]
            for nm, rec in snap["configs"].items():           # bring back what was deleted
                if nm not in by_name and not any(c.Label == rec["label"] for c in by_name.values()):
                    ov = json.loads(rec["overrides"])
                    AC.create(asm, rec["label"], ov["suppress"], ov["alternatives"], ov["joints"], ov["features"])
            by_label = {c.Label: c for c in AC.configurations(asm)}
            want = snap["active"]
            cur = AC.status(asm)["active"]
            if cur != want:
                try:
                    AC.activate(asm, want)
                except AC.ConfigurationError as exc:
                    problems.append(str(exc))
            elif want is not None:
                c = by_label.get(want)
                rec = next((r for r in snap["configs"].values() if r["label"] == want), None)
                if c is not None and rec is not None and not AC.status(asm)["drift"]:
                    for prop, key in (("Defaults", "defaults"), ("Applied", "applied")):
                        if getattr(c, prop) != rec[key]:
                            setattr(c, prop, rec[key])
            keep = {r["label"] for r in snap["configs"].values()}
            for c in list(AC.configurations(asm)):
                if c.Label not in keep and not c.Active:
                    doc.removeObject(c.Name)
        except Exception:
            doc.abortTransaction()
            raise
        doc.commitTransaction()
        return problems

    def cancel_changes(self):
        """Take back everything the panel did. Returns a list of problems (empty when all is back)."""
        if not self.steps:
            return []
        problems = []
        left = self._undo_own_steps()
        if left:
            problems = self._converge()
            self.steps = []
        return problems

    # ---- leaving the session

    def _stop_observing(self):
        if getattr(self, "_observing", False):
            self._observing = False
            try:
                App.removeDocumentObserver(self.observer)
            except Exception:  # noqa: BLE001
                pass

    def _form_gone(self, *_):
        """Gui.Control.closeDialog() calls neither accept nor reject, nor does closing the document: tidy up."""
        self._stop_observing()
        if not self.done:
            self.done = True
            if self._alive():
                try:
                    self.cancel_changes()
                except Exception:  # noqa: BLE001  (the document is going away)
                    pass

    # ---- task dialog protocol

    def getStandardButtons(self):
        std = QtWidgets.QDialogButtonBox.StandardButton
        ok, cancel = std.Ok, std.Cancel
        return int(getattr(ok, "value", ok)) | int(getattr(cancel, "value", cancel))

    def accept(self):
        self._stop_observing()
        self.done = True
        return True

    def reject(self):
        self._stop_observing()
        if not self.done and self._alive():
            try:
                self.cancel_changes()
            finally:
                self.done = True
        self.done = True
        return True

    def clicked(self, button):
        return None

    def isAllowedAlterDocument(self):
        return True

    def isAllowedAlterView(self):
        return True

    def isAllowedAlterSelection(self):
        return True


def _val(v):
    if isinstance(v, dict) and "base" in v:
        return "placement %s" % json.dumps(v.get("base"))
    if isinstance(v, float):
        return ("%.6g" % v)
    return str(v)
