# SPDX-License-Identifier: LGPL-2.1-or-later
"""Diagnosis panel of the sketch editor (cad-fork G10 slice 4a).  Imported only with the GUI up.

The panel shows what ``SketcherDiagnosis.diagnose`` says about the sketch that is open in the editor: the minimal
conflict sets with their members and their ranked, solver-tested fixes (each with the degrees of freedom left after
it), and the redundancy groups with the constraint suggested for removal.  Choosing a set, a fix or a constraint
selects the constraints in the editor, so they highlight in the view and in the constraint list.  Apply runs one fix
of the API in one named transaction.

The sketch editor already shows its own task dialog, so the panel is a non-modal dock widget of the main window and
needs no second ``Gui.Control`` dialog.  Behaviour is based on slice 1's API (``SketcherDiagnosis``) and on how
FreeCAD's own sketch commands change a sketch in the editor (open a command, change the sketch, commit); the layout
is the plain tree-and-buttons form of FreeCAD's other task panels.

Diagnosis runs on the main thread, because document operations are not thread-safe.  It hands control back to the
event loop through ``diagnose``'s progress callback, which is also where Cancel is noticed.  The panel never applies a
fix that was computed for an earlier state of the sketch: document and editor events invalidate the result at once,
and Apply compares a fingerprint of the sketch as a second guard.
"""

import io
import time
import zipfile

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

import SketcherDiagnosis as SD

translate = App.Qt.translate
CONTEXT = "Sketcher"

# object names the tests (and users of the widgets from Python) look for
DOCK_NAME = "SketcherDiagnosisPanel"
TREE_NAME = "diagnosisTree"
STATUS_NAME = "diagnosisStatus"
APPLY_NAME = "diagnosisApply"
REFRESH_NAME = "diagnosisRefresh"
CANCEL_NAME = "diagnosisCancel"
CLOSE_NAME = "diagnosisClose"
PROGRESS_NAME = "diagnosisProgress"

# properties of the sketch whose change makes a diagnosis out of date
_WATCHED = {"Constraints", "Geometry", "ExternalGeo", "ExpressionEngine", "Placement", "Support", "AttachmentOffset"}
_PUMP_INTERVAL = 0.03
_MAX_RESTARTS = 3  # consecutive automatic diagnoses that were overtaken by a change before the panel gives up

_ROLE = QtCore.Qt.UserRole
_panel = None


def tr(text):
    return translate(CONTEXT, text)


def constraint_name(index):
    """The editor's selection name of a constraint: 1-based, where the API's indices are 0-based."""
    return "Constraint%d" % (index + 1)


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


def constraint_text(sketch, index):
    """One line for a constraint: its editor number, type, name and datum."""
    try:
        c = sketch.Constraints[index]
    except Exception:
        return tr("Constraint %d") % (index + 1)
    text = "%d: %s" % (index + 1, c.Type)
    if c.Name:
        text += " '%s'" % c.Name
    if c.Type in SD._DATUM_TYPES:
        unit = "deg" if c.Type == "Angle" else "mm"
        text += " = %.6g %s" % (SD._value_in_unit(c.Value, c.Type), unit)
    return text


def fix_text(sketch, fix):
    """What a fix does, with the constraints named as the editor numbers them."""
    parts = []
    for op in fix["ops"]:
        i = op["index"]
        try:
            c = sketch.Constraints[i]
            label = c.Type + (" '%s'" % c.Name if c.Name else "")
        except Exception:
            label = tr("Constraint")
        label = "%s [%d]" % (label, i + 1)
        if op["op"] == "delete":
            parts.append(tr("Delete %s") % label)
        elif op["op"] == "set_driving":
            parts.append(tr("Make %s non-driving (reference)") % label)
        else:
            parts.append(tr("Change %s to %.6g %s") % (label, op["value"], op.get("unit", "")))
    return "; ".join(parts)


def set_text(position, members):
    return tr("Conflict %d: constraints %s") % (position + 1, ", ".join(str(m + 1) for m in members))


def group_text(position, members):
    return tr("Redundant group %d: constraints %s") % (position + 1, ", ".join(str(m + 1) for m in members))


def fix_row_text(sketch, fix):
    return tr("%s (degrees of freedom after: %d)") % (fix_text(sketch, fix), fix["dof_after"])


# ---------------------------------------------------------------------------
# Watching the document and the editor
# ---------------------------------------------------------------------------


class _DocWatch:
    """Document events that make the shown diagnosis out of date."""

    def __init__(self, panel):
        self.panel = panel

    def slotChangedObject(self, obj, prop):
        p = self.panel
        if p._applying or obj is None:
            return
        try:
            if obj.Document.Name == p.doc_name and obj.Name == p.sketch_name and prop in _WATCHED:
                p.invalidate(tr("The sketch changed."))
        except Exception:
            pass

    def slotUndoDocument(self, doc):
        if doc.Name == self.panel.doc_name and not self.panel._applying:
            self.panel.invalidate(tr("Undo changed the document."))

    def slotRedoDocument(self, doc):
        if doc.Name == self.panel.doc_name and not self.panel._applying:
            self.panel.invalidate(tr("Redo changed the document."))

    def slotDeletedObject(self, obj):
        try:
            if obj.Document.Name == self.panel.doc_name and obj.Name == self.panel.sketch_name:
                self.panel.invalidate(tr("The sketch was deleted."), close=True)
        except Exception:
            pass

    def slotDeletedDocument(self, doc):
        if doc.Name == self.panel.doc_name:
            self.panel.invalidate(tr("The document was closed."), close=True)


class _GuiWatch:
    """Editor events: the sketch leaving edit mode."""

    def __init__(self, panel):
        self.panel = panel

    def slotResetEdit(self, vobj):
        try:
            if vobj.Object.Name == self.panel.sketch_name and vobj.Object.Document.Name == self.panel.doc_name:
                self.panel.invalidate(tr("The sketch is no longer being edited."), close=True)
        except Exception:
            self.panel.invalidate(tr("The sketch is no longer being edited."), close=True)

    def slotDeletedDocument(self, gdoc):
        try:
            if gdoc.Document.Name == self.panel.doc_name:
                self.panel.invalidate(tr("The document was closed."), close=True)
        except Exception:
            self.panel.invalidate(tr("The document was closed."), close=True)


def sketch_in_edit(sketch):
    """True when `sketch` is the object the editor has open now."""
    try:
        gdoc = Gui.getDocument(sketch.Document.Name)
        vp = gdoc.getInEdit()
        return vp is not None and vp.Object.Name == sketch.Name
    except Exception:
        return False


# ---------------------------------------------------------------------------
# The panel
# ---------------------------------------------------------------------------


class DiagnosisPanel(QtWidgets.QDockWidget):
    def __init__(self, sketch, parent=None, auto_run=True):
        super().__init__(tr("Diagnose sketch"), parent or Gui.getMainWindow())
        self.setObjectName(DOCK_NAME)
        self.sketch = sketch
        self.doc_name = sketch.Document.Name
        self.sketch_name = sketch.Name
        self.state = "idle"  # idle, running, ready, stale, cancelled, failed, closed
        self.result = None
        self.fingerprint = None
        self._generation = 0
        self._cancel = False
        self._rerun = False
        self._restarts = 0
        self._applying = False
        self._rediagnose_pending = False
        self._owned = []  # selection names the panel put in the editor
        self._last_pump = 0.0
        self._pump_end = 0.0
        self.max_gap = 0.0
        self.last_error = None
        self._closing = False
        self._build()
        self._watch()
        if auto_run:
            QtCore.QTimer.singleShot(0, self.diagnose_now)

    # -- widgets -----------------------------------------------------------
    def _build(self):
        body = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(body)
        self.status = QtWidgets.QLabel(tr("Not diagnosed yet."))
        self.status.setObjectName(STATUS_NAME)
        self.status.setWordWrap(True)
        self.status.setAccessibleName(tr("Diagnosis status"))
        lay.addWidget(self.status)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setObjectName(PROGRESS_NAME)
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName(tr("Diagnosis progress"))
        self.progress.hide()
        lay.addWidget(self.progress)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setObjectName(TREE_NAME)
        self.tree.setHeaderHidden(True)
        self.tree.setWordWrap(True)
        self.tree.setTextElideMode(QtCore.Qt.ElideNone)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.tree.setAccessibleName(tr("Conflicts, redundancies and fixes"))
        self.tree.setToolTip(
            tr("Choose a conflict set, a fix or a constraint to select its constraints in the sketch")
        )
        self.tree.setMinimumHeight(220)
        self.tree.itemSelectionChanged.connect(self._on_choice)
        lay.addWidget(self.tree)
        row = QtWidgets.QHBoxLayout()
        self.apply_button = QtWidgets.QPushButton(tr("Apply fix"))
        self.apply_button.setObjectName(APPLY_NAME)
        self.apply_button.setToolTip(tr("Applies the chosen fix to the sketch as one undoable step"))
        self.apply_button.setAccessibleName(tr("Apply fix"))
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.apply_chosen)
        self.refresh_button = QtWidgets.QPushButton(tr("Diagnose again"))
        self.refresh_button.setObjectName(REFRESH_NAME)
        self.refresh_button.setToolTip(tr("Runs the diagnosis again on the sketch as it is now"))
        self.refresh_button.setAccessibleName(tr("Diagnose again"))
        self.refresh_button.clicked.connect(self.diagnose_now)
        self.cancel_button = QtWidgets.QPushButton(tr("Cancel"))
        self.cancel_button.setObjectName(CANCEL_NAME)
        self.cancel_button.setToolTip(tr("Stops the diagnosis that is running"))
        self.cancel_button.setAccessibleName(tr("Cancel diagnosis"))
        self.cancel_button.clicked.connect(self.request_cancel)
        self.cancel_button.hide()
        self.close_button = QtWidgets.QPushButton(tr("Close"))
        self.close_button.setObjectName(CLOSE_NAME)
        self.close_button.setToolTip(tr("Closes the panel and leaves the sketch as it is"))
        self.close_button.setAccessibleName(tr("Close panel"))
        self.close_button.clicked.connect(self.close)
        for b in (self.apply_button, self.refresh_button, self.cancel_button, self.close_button):
            row.addWidget(b)
        lay.addLayout(row)
        self.setWidget(body)
        self.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea)
        self.setFeatures(QtWidgets.QDockWidget.DockWidgetClosable | QtWidgets.QDockWidget.DockWidgetMovable)

    def _watch(self):
        self._dw = _DocWatch(self)
        self._gw = _GuiWatch(self)
        App.addDocumentObserver(self._dw)
        Gui.addDocumentObserver(self._gw)

    def _unwatch(self):
        for fn, obj in ((App.removeDocumentObserver, "_dw"), (Gui.removeDocumentObserver, "_gw")):
            try:
                fn(getattr(self, obj))
            except Exception:
                pass

    # -- running the diagnosis ---------------------------------------------
    def _fingerprint(self):
        """What the diagnosis depends on, as stable data.  The dumps are zip archives that carry timestamps, so the
        members are compared, not the bytes."""
        sk = self.sketch
        parts = []
        for prop in ("Constraints", "Geometry", "ExternalGeo"):
            z = zipfile.ZipFile(io.BytesIO(bytes(sk.dumpPropertyContent(prop))))
            parts.append(tuple((n, z.read(n)) for n in sorted(z.namelist())))
        parts.append(tuple((str(p), str(e)) for p, e in sk.ExpressionEngine))
        parts.append(str(sk.Placement))
        return tuple(parts)

    def _alive(self):
        try:
            return (
                self.doc_name in App.listDocuments()
                and App.getDocument(self.doc_name).getObject(self.sketch_name) is not None
                and sketch_in_edit(self.sketch)
            )
        except Exception:
            return False

    def _on_progress(self, info):
        """Called by ``diagnose`` before every solver call; keeps the interface alive and notices Cancel."""
        now = time.time()
        if self._pump_end:
            self.max_gap = max(self.max_gap, now - self._pump_end)
        if now - self._last_pump >= _PUMP_INTERVAL:
            QtWidgets.QApplication.processEvents()
            self._last_pump = time.time()
        self._pump_end = time.time()
        return self._cancel

    def request_cancel(self):
        self._cancel = True

    def _set_running(self, running):
        self.progress.setVisible(running)
        self.cancel_button.setVisible(running)
        self.refresh_button.setEnabled(not running)
        self.apply_button.setEnabled(False)
        self.tree.setEnabled(not running)
        self.close_button.setEnabled(True)

    def diagnose_now(self, *_):
        """Diagnose the sketch as it is now and show the result.  Safe to call while a diagnosis runs (it asks that
        one to stop and starts again)."""
        if self.state == "closed":
            return
        if self.state == "running":
            self._cancel = True
            self._rerun = True
            return
        if not self._alive():
            self.invalidate(tr("The sketch is no longer being edited."), close=True)
            return
        self._clear_tree()
        self._generation += 1
        gen = self._generation
        self._cancel = False
        self._rerun = False
        self.state = "running"
        self.status.setText(tr("Diagnosing..."))
        self._set_running(True)
        self.max_gap = 0.0
        self._last_pump = 0.0
        self._pump_end = 0.0
        result = None
        fp = None
        error = None
        try:
            fp = self._fingerprint()
            result = SD.diagnose(self.sketch, progress=self._on_progress)
        except SD.DiagnosisCancelled:
            error = "cancelled"
        except Exception as e:  # shown, not raised: a failed diagnosis must not take the editor down
            error = "failed"
            self.last_error = "%s: %s" % (type(e).__name__, e)
        if self.state == "closed":
            return
        self._set_running(False)
        if self._cancel and error is None:
            error = "cancelled"  # cancelled after the last tick: the result is still complete but was asked away
            result = None
        stale = gen != self._generation or self.state == "stale"
        if error == "cancelled" or stale:
            self.state = "cancelled" if not stale else "stale"
            self.status.setText(tr("Diagnosis cancelled.") if not stale else tr("The sketch changed; diagnosing again."))
            if self._rerun or stale:
                self._schedule_rediagnose()
            return
        if error == "failed":
            self.state = "failed"
            self.status.setText(tr("The diagnosis failed: %s") % self.last_error)
            return
        self.result = result
        self.fingerprint = fp
        self.state = "ready"
        self._restarts = 0
        self._show(result)

    def _schedule_rediagnose(self):
        if self._rediagnose_pending or self.state == "closed":
            return
        self._rediagnose_pending = True

        def go():
            self._rediagnose_pending = False
            if self.state in ("stale", "cancelled") and self._rerun_wanted():
                self._restarts += 1
                if self._restarts > _MAX_RESTARTS:
                    # something keeps changing the sketch while it is diagnosed: stop instead of looping
                    self.state = "failed"
                    self.status.setText(tr("The sketch keeps changing while it is diagnosed. Use Diagnose again."))
                    self.tree.setEnabled(True)
                    return
                self.state = "idle"
                self.diagnose_now()

        QtCore.QTimer.singleShot(0, go)

    def _rerun_wanted(self):
        return self._alive()

    # -- showing the result ---------------------------------------------------
    def _clear_tree(self):
        self._deselect_owned()
        self.tree.blockSignals(True)
        self.tree.clear()
        self.tree.blockSignals(False)
        self.apply_button.setEnabled(False)

    def _item(self, parent, text, data=None):
        item = QtWidgets.QTreeWidgetItem(parent, [text])
        if data is not None:
            item.setData(0, _ROLE, data)
        return item

    def _show(self, r):
        sk = self.sketch
        self.tree.blockSignals(True)
        self.tree.clear()
        sets = r["conflict_sets"]
        groups = r["redundancy_groups"]
        for k, cs in enumerate(sets):
            top = self._item(self.tree, set_text(k, cs["members"]), {"kind": "set", "set": k, "select": cs["members"]})
            for m in cs["members"]:
                self._item(top, constraint_text(sk, m), {"kind": "member", "select": [m]})
            for j, fix in enumerate(cs["fixes"]):
                self._item(
                    top,
                    fix_row_text(sk, fix),
                    {"kind": "fix", "set": k, "fix": j, "select": [o["index"] for o in fix["ops"]]},
                )
            top.setExpanded(True)
        for k, g in enumerate(groups):
            top = self._item(self.tree, group_text(k, g["members"]), {"kind": "group", "group": k, "select": g["members"]})
            for m in g["members"]:
                self._item(top, constraint_text(sk, m), {"kind": "member", "select": [m]})
            rem = g["suggested_removal"]
            self._item(
                top,
                tr("Suggested removal: constraint %s") % constraint_text(sk, rem),
                {"kind": "removal", "select": [rem]},
            )
            top.setExpanded(True)
        self.tree.blockSignals(False)
        if sets or groups:
            parts = []
            if sets:
                parts.append(tr("%d conflict set(s)") % len(sets))
            if groups:
                parts.append(tr("%d redundancy group(s)") % len(groups))
            text = tr("Found %s; degrees of freedom: %d.") % (", ".join(parts), r["dof"])
            if not r.get("complete", True):
                text += " " + tr("The list may be incomplete.")
        elif r["status"] in ("solver_failed", "malformed"):
            text = tr("The solver reports %s, without a conflict or redundancy that can be located.") % r["status"]
        else:
            text = tr("Nothing is wrong: no conflicting or redundant constraints (degrees of freedom: %d).") % r["dof"]
        self.status.setText(text)
        self.apply_button.setEnabled(False)

    # -- choosing rows selects constraints ------------------------------------
    def _chosen(self):
        items = self.tree.selectedItems()
        return items[0] if items else None

    def _on_choice(self):
        item = self._chosen()
        if item is None:
            self._deselect_owned()
            self.apply_button.setEnabled(False)
            return
        data = item.data(0, _ROLE) or {}
        self.select_constraints(data.get("select", []))
        self.apply_button.setEnabled(data.get("kind") == "fix" and self.state == "ready")

    def select_constraints(self, indices):
        """Select exactly these constraints (0-based API indices) in the editor."""
        Gui.Selection.clearSelection()
        self._owned = []
        for i in indices:
            name = constraint_name(i)
            Gui.Selection.addSelection(self.doc_name, self.sketch_name, name)
            self._owned.append(name)

    def _deselect_owned(self):
        """Remove what the panel selected, and nothing else."""
        names, self._owned = self._owned, []
        for name in names:
            try:
                Gui.Selection.removeSelection(self.doc_name, self.sketch_name, name)
            except Exception:
                pass

    # -- applying -----------------------------------------------------------------
    def chosen_fix(self):
        item = self._chosen()
        if item is None or self.result is None:
            return None
        data = item.data(0, _ROLE) or {}
        if data.get("kind") != "fix":
            return None
        return self.result["conflict_sets"][data["set"]]["fixes"][data["fix"]]

    def apply_chosen(self, *_):
        """Apply the chosen fix in one named transaction, if the sketch is still the one it was computed for."""
        fix = self.chosen_fix()
        if fix is None or self.state != "ready":
            return False
        if not self._alive() or self._fingerprint() != self.fingerprint:
            self.invalidate(tr("The sketch changed."))
            return False
        doc = self.sketch.Document
        self._applying = True
        name = tr("Apply sketch fix")
        try:
            doc.openTransaction(name)
            try:
                SD.apply_fix(self.sketch, fix)
            except Exception:
                doc.abortTransaction()
                raise
            doc.commitTransaction()
        finally:
            self._applying = False
        self._after_change()
        return True

    def _after_change(self):
        """The sketch changed under the panel's own hand: show the new diagnosis at once."""
        self._deselect_owned()
        self.state = "idle"
        self.diagnose_now()

    # -- staleness -----------------------------------------------------------------
    def invalidate(self, reason, close=False):
        """The sketch (or the editor) is no longer what the result was computed for."""
        if self.state == "closed" or self._closing:
            return
        self._generation += 1
        self.result = None
        self.fingerprint = None
        self.apply_button.setEnabled(False)
        if self.state == "running":
            self._cancel = True
            self._rerun = not close
            self.state = "stale"
        else:
            self.state = "stale"
        if close:
            self.status.setText(reason)
            self._closing = True
            QtCore.QTimer.singleShot(0, self.close)
            return
        self.tree.blockSignals(True)
        self.tree.clear()
        self.tree.blockSignals(False)
        self._owned = []
        self.status.setText(reason + " " + tr("Diagnosing again."))
        if self._cancel:
            return  # the running diagnosis stops at its next tick and starts again
        self._schedule_rediagnose()

    # -- closing ---------------------------------------------------------------------
    def closeEvent(self, event):
        global _panel
        if self.state == "running":
            self._cancel = True
        self.state = "closed"
        self._unwatch()
        try:
            self._deselect_owned()
        except Exception:
            pass
        if _panel is self:
            _panel = None
        super().closeEvent(event)
        self.deleteLater()


def current_panel():
    return _panel


def open_panel(sketch=None, auto_run=True):
    """Open the panel for the sketch in the editor (or `sketch`).  A panel that is open is reused."""
    global _panel
    if sketch is None:
        sketch = editing_sketch()
    if sketch is None:
        return None
    if _panel is not None and _panel.state != "closed":
        if _panel.sketch_name == sketch.Name and _panel.doc_name == sketch.Document.Name:
            _panel.show()
            _panel.raise_()
            return _panel
        _panel.close()
    mw = Gui.getMainWindow()
    panel = DiagnosisPanel(sketch, mw, auto_run=auto_run)
    mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, panel)
    panel.show()
    _panel = panel
    return panel


def editing_sketch():
    """The sketch that is open in the editor now, or None."""
    gdoc = Gui.ActiveDocument
    if gdoc is None:
        return None
    vp = gdoc.getInEdit()
    obj = getattr(vp, "Object", None) if vp is not None else None
    if obj is not None and obj.TypeId == "Sketcher::SketchObject":
        return obj
    return None
