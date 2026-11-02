# SPDX-License-Identifier: LGPL-2.1-or-later
"""Task panel of the Assembly_ReplaceComponent command (cad-fork G06 slice 2b). Imported only with the GUI up.

The panel picks the component to replace and the new part (an object of the document, a file, or a revision from the
PDM vault), shows what ``ReplaceComponent.plan`` says for every joint reference, lets the user settle an ambiguous
alignment and the ambiguous references, and calls ``ReplaceComponent.apply`` on OK. Until OK nothing in the assembly's
document changes: planning is read-only, a file or a vault revision is opened as a hidden temporary document that is
closed again, and Cancel (or closing the panel any other way) removes what the panel opened.

The remap table has one row per joint reference (a face and an edge of one part make one row): the joint, the reference,
the old elements, the status, the new elements (a combo for each ambiguous one) and what happens to the joint.

Behaviour sources: the API contract in the ``ReplaceComponent`` docstring; FreeCAD's task dialog protocol. Wording and
layout are this project's own.
"""

import os
import threading
import time

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import ReplaceComponent as RC

translate = App.Qt.translate

#: seconds the vault may take before the panel gives up and says so
PDM_TIMEOUT = 6.0

TABLE_HEADERS = ["Joint", "Reference", "Old element", "Status", "New element or reason", "Joint afterwards"]
STATUS_TEXT = {"matched": "Matched", "ambiguous": "Ambiguous", "unmatched": "Unmatched"}
KIND_OBJECT, KIND_FILE, KIND_PDM = 0, 1, 2
SOURCE_TEXT = ["An object of this document", "A file", "A revision from the PDM vault"]
CHOOSE = "(choose)"
SHAPELESS_PREFIXES = ("Assembly::",)


def run_bounded(fn, seconds=None):
    """Run ``fn`` on a helper thread and wait at most ``seconds`` (keeping the interface painting, not clickable).

    Returns its value, re-raises its exception, or raises TimeoutError; the helper is a daemon thread and is left
    to finish on its own."""
    seconds = PDM_TIMEOUT if seconds is None else seconds
    box = {}

    def work():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001  (handed to the caller)
            box["error"] = exc

    th = threading.Thread(target=work, daemon=True)
    th.start()
    deadline = time.monotonic() + seconds
    while th.is_alive() and time.monotonic() < deadline:
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)
        th.join(0.02)
    if th.is_alive():
        raise TimeoutError(translate("Assembly", "no answer within %.0f s") % seconds)
    if "error" in box:
        raise box["error"]
    return box.get("value")


def solid_shape(obj):
    """The shape of ``obj`` (following links) when it holds a solid, else None."""
    try:
        import Part

        shape = Part.getShape(obj)
        return shape if shape is not None and not shape.isNull() and shape.Solids else None
    except Exception:  # noqa: BLE001  (an object without a shape is simply not a part)
        return None


def part_candidates(doc, exclude=()):
    """Objects of ``doc`` that can take a component's place: they hold a solid and are not part of another object
    (a feature of a body, an operand of a boolean), not assembly objects."""
    out = []
    for o in doc.Objects:
        if o.TypeId.startswith(SHAPELESS_PREFIXES) or o in exclude:
            continue
        parent = o.getParentGeoFeatureGroup()
        if parent is not None and parent.TypeId == "PartDesign::Body":
            continue
        if o.InList and not all(u.isDerivedFrom("Assembly::AssemblyObject") or u.TypeId == "App::Link" for u in o.InList):
            continue
        if solid_shape(o) is not None:
            out.append(o)
    return out


def components_of(assembly):
    """Children of the assembly that ``ReplaceComponent`` accepts as the old part."""
    out = []
    for o in assembly.Group:
        if RC._is_joint(o) or o.TypeId in ("Assembly::JointGroup", "Assembly::ViewGroup", "Assembly::BomGroup"):
            continue
        try:
            RC._find_hosts(assembly, o)
        except Exception:  # noqa: BLE001  (not something that can be replaced)
            continue
        out.append(o)
    return out


class _Keys(QtCore.QObject):
    """Return in a text field loads what it names (``on_return``); Return in a cell combo or the checkbox does nothing,
    so a stray key never replaces a component. Return anywhere else is the task view's: it presses OK."""

    def __init__(self, parent, on_return=None):
        super().__init__(parent)
        self.on_return = on_return or {}

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.KeyPress and event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            fn = self.on_return.get(obj)
            if fn is not None:
                QtCore.QTimer.singleShot(0, fn)
            return True
        return False


class TaskReplaceComponent:
    """Preview and apply the replacement of one component of ``assembly``."""

    title = "Replace component"

    def __init__(self, assembly, old=None, new=None):
        self.assembly = assembly
        self.doc = assembly.Document
        self.document_name = self.doc.Name
        self.done = False
        self.applied = None            # the report of apply, after OK
        self.report = None             # the last plan
        self.new_obj = None            # the part that will take the place
        self.alignment_plc = None      # App.Placement chosen in the alignment combo
        self.align_cands = []          # candidates of the ambiguous alignment (kept so the choice can be changed)
        self.choices = {}              # "<Joint>.<Reference>" -> {old element: new element}
        self.cands_seen = {}           # (key, old element) -> candidate names, to keep a user's pick changeable
        self.temp = []                 # [(document name, export folder or None)] opened by this panel
        self.file_objects = []
        self.pdm_docs = {}
        self._vault_obj = None
        self._was_active = False
        self._timer = None
        self.pdm_objects = []

        self.form = QtWidgets.QWidget()
        self.form.setObjectName("Assembly_TaskReplaceComponent")
        self.form.setWindowTitle(translate("Assembly", self.title))
        lay = QtWidgets.QVBoxLayout(self.form)
        self._keys = _Keys(self.form)

        fields = QtWidgets.QFormLayout()
        self.old_combo = QtWidgets.QComboBox()
        self.old_combo.setObjectName("old_component")
        self.old_combo.setAccessibleName(translate("Assembly", "Component to replace"))
        self.old_combo.setToolTip(translate("Assembly", "The component of the assembly that the new part takes the place of"))
        fields.addRow(translate("Assembly", "Component to replace"), self.old_combo)

        self.source_combo = QtWidgets.QComboBox()
        self.source_combo.setObjectName("source_kind")
        self.source_combo.setAccessibleName(translate("Assembly", "New part from"))
        for t in SOURCE_TEXT:
            self.source_combo.addItem(translate("Assembly", t))
        fields.addRow(translate("Assembly", "New part from"), self.source_combo)
        lay.addLayout(fields)

        self.stack = QtWidgets.QStackedWidget()
        self.stack.setObjectName("source_stack")
        self.stack.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
        lay.addWidget(self.stack)
        self._build_object_page()
        self._build_file_page()
        self._build_pdm_page()
        self._fit_stack(0)

        self.align_row = QtWidgets.QWidget()
        arow = QtWidgets.QFormLayout(self.align_row)
        arow.setContentsMargins(0, 0, 0, 0)
        self.align_combo = QtWidgets.QComboBox()
        self.align_combo.setObjectName("alignment")
        self.align_combo.setAccessibleName(translate("Assembly", "Position of the new part"))
        self.align_combo.setToolTip(translate("Assembly", "How the new part sits relative to the old one; the counts are what each reading gives"))
        self.align_label = QtWidgets.QLabel(translate("Assembly", "Position of the new part"))
        self.align_label.setBuddy(self.align_combo)
        arow.addRow(self.align_label, self.align_combo)
        lay.addWidget(self.align_row)
        self.align_row.hide()

        self.summary = QtWidgets.QLabel()
        self.summary.setObjectName("summary")
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)

        self.error = QtWidgets.QLabel()
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        self.error.setStyleSheet("background-color: #b00020; color: white; padding: 2px 6px;")   # own background: readable on any theme
        self.error.hide()
        lay.addWidget(self.error)

        self.notice = QtWidgets.QLabel()
        self.notice.setObjectName("notice")
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet("background-color: #fff1b8; color: #3a2f00; padding: 2px 6px;")   # informational, own colours: readable on any theme
        self.notice.hide()
        lay.addWidget(self.notice)

        self.table = QtWidgets.QTableWidget(0, len(TABLE_HEADERS))
        self.table.setObjectName("remap")
        self.table.setHorizontalHeaderLabels([translate("Assembly", h) for h in TABLE_HEADERS])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        head = self.table.horizontalHeader()
        head.setStretchLastSection(False)
        self.table.setMinimumHeight(120)
        self.table.setAccessibleName(translate("Assembly", "Joint references and where they go"))
        lay.addWidget(self.table, 1)

        self.delete_old = QtWidgets.QCheckBox(translate("Assembly", "Delete the old part afterwards"))
        self.delete_old.setObjectName("delete_old")
        self.delete_old.setChecked(False)
        self.delete_old.setToolTip(translate("Assembly", "Removes the old part from the document unless something else still uses it"))
        lay.addWidget(self.delete_old)
        self._keys_widgets = [self.delete_old, self.table]
        for w in self._keys_widgets:
            w.installEventFilter(self._keys)

        self.old_combo.activated.connect(lambda *_: self._old_changed())
        self.source_combo.activated.connect(lambda *_: self._source_kind_changed())
        self.align_combo.activated.connect(lambda *_: self._alignment_picked())
        self.form.destroyed.connect(self._close_temp)

        self._fill_old(old)
        self._fill_objects(new)
        if new is not None:
            self.set_source_object(new)
        self._refresh_notice()

    # ------------------------------------------------------------------ source pages

    def _fit_stack(self, index):
        """Show page ``index`` and let the stack be only as tall as that page (a QStackedWidget is as tall as its tallest)."""
        self.stack.setCurrentIndex(index)
        for i in range(self.stack.count()):
            page = self.stack.widget(i)
            pol = page.sizePolicy()
            pol.setVerticalPolicy(QtWidgets.QSizePolicy.Preferred if i == index else QtWidgets.QSizePolicy.Ignored)
            page.setSizePolicy(pol)
        self.stack.updateGeometry()

    def _build_object_page(self):
        page = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(page)
        f.setContentsMargins(0, 0, 0, 0)
        self.object_combo = QtWidgets.QComboBox()
        self.object_combo.setObjectName("new_object")
        self.object_combo.setAccessibleName(translate("Assembly", "New part"))
        f.addRow(translate("Assembly", "New part"), self.object_combo)
        self.object_combo.activated.connect(lambda *_: self._object_picked())
        self.stack.addWidget(page)

    def _build_file_page(self):
        page = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(page)
        f.setContentsMargins(0, 0, 0, 0)
        row = QtWidgets.QHBoxLayout()
        self.file_edit = QtWidgets.QLineEdit()
        self.file_edit.setObjectName("file_path")
        self.file_edit.setAccessibleName(translate("Assembly", "File"))
        self.file_edit.setPlaceholderText(translate("Assembly", "Path of a .FCStd file"))
        self.file_browse = QtWidgets.QPushButton(translate("Assembly", "Browse..."))
        self.file_browse.setObjectName("file_browse")
        self.file_browse.setAutoDefault(False)
        row.addWidget(self.file_edit, 1)
        row.addWidget(self.file_browse)
        f.addRow(translate("Assembly", "File"), row)
        self.file_object = QtWidgets.QComboBox()
        self.file_object.setObjectName("file_object")
        self.file_object.setAccessibleName(translate("Assembly", "Part in the file"))
        f.addRow(translate("Assembly", "Part in the file"), self.file_object)
        self.stack.addWidget(page)
        self.file_browse.clicked.connect(self._browse)
        self.file_object.activated.connect(lambda *_: self._file_object_picked())
        self.file_edit.installEventFilter(self._keys)
        self._keys.on_return[self.file_edit] = lambda: self.set_source_file(self.file_edit.text())

    def _build_pdm_page(self):
        page = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(page)
        f.setContentsMargins(0, 0, 0, 0)
        self.pdm_doc = QtWidgets.QComboBox()
        self.pdm_doc.setObjectName("pdm_document")
        self.pdm_doc.setAccessibleName(translate("Assembly", "Vault document"))
        f.addRow(translate("Assembly", "Vault document"), self.pdm_doc)
        self.pdm_rev = QtWidgets.QComboBox()
        self.pdm_rev.setObjectName("pdm_revision")
        self.pdm_rev.setAccessibleName(translate("Assembly", "Revision"))
        f.addRow(translate("Assembly", "Revision"), self.pdm_rev)
        self.pdm_object = QtWidgets.QComboBox()
        self.pdm_object.setObjectName("pdm_object")
        self.pdm_object.setAccessibleName(translate("Assembly", "Part in the revision"))
        f.addRow(translate("Assembly", "Part in the revision"), self.pdm_object)
        self.pdm_load = QtWidgets.QPushButton(translate("Assembly", "Reload the vault"))
        self.pdm_load.setObjectName("pdm_reload")
        self.pdm_load.setAutoDefault(False)
        f.addRow("", self.pdm_load)
        self.pdm_note = QtWidgets.QLabel()
        self.pdm_note.setObjectName("pdm_note")
        self.pdm_note.setWordWrap(True)
        f.addRow("", self.pdm_note)
        self.stack.addWidget(page)
        self.pdm_load.clicked.connect(lambda *_: self.load_pdm())
        self.pdm_doc.activated.connect(lambda *_: self._pdm_doc_picked())
        self.pdm_rev.activated.connect(lambda *_: self._pdm_rev_picked())
        self.pdm_object.activated.connect(lambda *_: self._pdm_object_picked())

    # ------------------------------------------------------------------ messages

    def show_error(self, text):
        self.error.setText(text)
        self.error.setVisible(bool(text))

    def _refresh_notice(self):
        txt = ""
        r = self.report
        if r is not None and r["references"]["ambiguous"] and r["alignment"]["method"] != "ambiguous":
            txt = translate("Assembly", "Ambiguous references you do not settle keep their joint suppressed and marked.")
        self.notice.setText(txt)
        self.notice.setVisible(bool(txt))

    # ------------------------------------------------------------------ the old component

    def _fill_old(self, old):
        self.components = components_of(self.assembly)
        self.old_combo.blockSignals(True)
        self.old_combo.clear()
        for o in self.components:
            self.old_combo.addItem(o.Label)
        self.old_combo.setCurrentIndex(self.components.index(old) if old in self.components else (0 if len(self.components) == 1 and old is None else -1))
        self.old_combo.blockSignals(False)

    @property
    def old(self):
        i = self.old_combo.currentIndex()
        return self.components[i] if 0 <= i < len(self.components) else None

    def set_old(self, obj):
        self.old_combo.setCurrentIndex(self.components.index(obj))
        self._old_changed()

    def _old_changed(self):
        self._reset_choices()
        self._fill_objects(self.new_obj if self.source_combo.currentIndex() == KIND_OBJECT else None)
        self.replan()

    def _reset_choices(self, alignment=True):
        self.choices = {}
        self.cands_seen = {}
        if alignment:
            self.alignment_plc = None
            self.align_cands = []

    # ------------------------------------------------------------------ the new part

    def _fill_objects(self, new):
        old = self.old
        self.objects = part_candidates(self.doc, exclude=(old,) if old is not None else ())
        self.object_combo.blockSignals(True)
        self.object_combo.clear()
        self.object_combo.addItem("")
        for o in self.objects:
            self.object_combo.addItem(o.Label)
        self.object_combo.setCurrentIndex(self.objects.index(new) + 1 if new in self.objects else 0)
        self.object_combo.blockSignals(False)

    def _source_kind_changed(self):
        kind = self.source_combo.currentIndex()
        self._fit_stack(kind)
        self._set_new(None)
        if kind == KIND_PDM and not self.pdm_docs:
            self.load_pdm()
        self.replan()

    def set_source_kind(self, kind):
        self.source_combo.setCurrentIndex(kind)
        self._source_kind_changed()

    def _set_new(self, obj):
        if obj is not self.new_obj:
            self._reset_choices()
        self.new_obj = obj

    def _object_picked(self):
        i = self.object_combo.currentIndex()
        self._set_new(self.objects[i - 1] if i > 0 else None)
        self.replan()

    def set_source_object(self, obj):
        self.source_combo.setCurrentIndex(KIND_OBJECT)
        self._fit_stack(KIND_OBJECT)
        if obj not in self.objects:
            self._fill_objects(obj)
        self.object_combo.setCurrentIndex(self.objects.index(obj) + 1 if obj in self.objects else 0)
        self._set_new(obj)
        self.replan()

    # ---- a file

    def _browse(self):
        path, _flt = QtWidgets.QFileDialog.getOpenFileName(
            self.form, translate("Assembly", "Choose the file with the new part"), "", "FreeCAD files (*.FCStd)")
        if path:
            self.file_edit.setText(path)
            self.set_source_file(path)

    def _open_hidden(self, path, export=None):
        """Open ``path`` as a hidden temporary document; remembered so closing the panel closes it. Opening a document
        makes it the active one, which would hide this panel and change what the user is working in, so the
        assembly's document is made active again at once."""
        before = set(App.listDocuments())
        try:
            doc = App.openDocument(path, True, True)
        finally:
            self._restore_active()
        if doc.Name not in before:
            self.temp.append((doc.Name, export))
            self._watch()
        return doc

    def _restore_active(self):
        if self.document_name in App.listDocuments():
            if App.ActiveDocument is None or App.ActiveDocument.Name != self.document_name:
                App.setActiveDocument(self.document_name)
            gdoc = Gui.getDocument(self.document_name)
            if Gui.ActiveDocument is None or Gui.ActiveDocument.Document.Name != self.document_name:
                Gui.ActiveDocument = gdoc

    def _watch(self):
        """Closing a task dialog with Gui.Control.closeDialog() calls neither accept nor reject, and the form is deleted
        late: a timer closes the temporary documents once the dialog is no longer the active one."""
        if getattr(self, "_timer", None) is None:
            self._timer = QtCore.QTimer(self.form)
            self._timer.setInterval(300)
            self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self):
        if Gui.Control.activeDialog():
            self._was_active = True
        elif self._was_active and self.temp:
            self._close_temp()
        if not self.temp:
            self._timer.stop()

    def set_source_file(self, path, object_name=None):
        """Load the part of a file; returns True when a part was found."""
        self.source_combo.setCurrentIndex(KIND_FILE)
        self._fit_stack(KIND_FILE)
        self.file_edit.setText(path)
        self._set_new(None)
        self.file_objects = []
        self.file_object.clear()
        self.show_error("")
        path = (path or "").strip()
        if not path:
            self.replan()
            return False
        if not os.path.isfile(path):
            self.show_error(translate("Assembly", "No such file: %s") % path)
            self.replan()
            return False
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            doc = self._open_hidden(path)
        except Exception as exc:  # noqa: BLE001  (a panel never raises into the Qt event loop)
            self.show_error(translate("Assembly", "Could not open %s: %s") % (path, exc))
            self.replan()
            return False
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        return self._offer(doc, self.file_object, self.file_objects, object_name, translate("Assembly", "The file holds no solid part."))

    def _offer(self, doc, combo, store, object_name, empty_text):
        store[:] = [o for o in part_candidates(doc)]
        combo.blockSignals(True)
        combo.clear()
        for o in store:
            combo.addItem(o.Label)
        combo.blockSignals(False)
        if not store:
            self.show_error(empty_text)
            self.replan()
            return False
        pick = next((i for i, o in enumerate(store) if o.Name == object_name or o.Label == object_name), 0)
        combo.setCurrentIndex(pick)
        self._set_new(store[pick])
        self.replan()
        return True

    def _file_object_picked(self):
        i = self.file_object.currentIndex()
        self._set_new(self.file_objects[i] if 0 <= i < len(self.file_objects) else None)
        self.replan()

    # ---- the PDM vault

    def _vault(self):
        from PDMGui import state

        return state.current_vault()

    def load_pdm(self):
        """List the documents of the connected vault; says so within PDM_TIMEOUT seconds when there is none."""
        self.pdm_docs = {}
        self.pdm_doc.blockSignals(True)
        self.pdm_doc.clear()
        self.pdm_doc.blockSignals(False)
        self.pdm_rev.clear()
        self.pdm_object.clear()
        self.pdm_note.setText("")
        self.form.setEnabled(False)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            try:
                vault = run_bounded(self._vault)
            except ImportError:
                self.pdm_note.setText(translate("Assembly", "The PDM workbench is not available."))
                return False
            if vault is None:
                self.pdm_note.setText(translate("Assembly", "No PDM vault is connected. Connect one with the PDM workbench first."))
                return False
            self._vault_obj = vault
            docs = run_bounded(vault.documents)
        except TimeoutError as exc:
            self.pdm_note.setText(translate("Assembly", "The PDM vault did not answer (%s).") % exc)
            return False
        except Exception as exc:  # noqa: BLE001
            self.pdm_note.setText(translate("Assembly", "The PDM vault could not be read: %s") % exc)
            return False
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
            self.form.setEnabled(True)
        self.pdm_docs = docs
        self.pdm_doc.blockSignals(True)
        self.pdm_doc.addItem("")
        for name in sorted(docs):
            self.pdm_doc.addItem(name)
        self.pdm_doc.blockSignals(False)
        if not docs:
            self.pdm_note.setText(translate("Assembly", "The vault holds no documents."))
        return bool(docs)

    def _pdm_doc_picked(self):
        name = self.pdm_doc.currentText()
        self.pdm_rev.clear()
        self.pdm_object.clear()
        self._set_new(None)
        self.pdm_note.setText("")
        if name:
            self.set_pdm_document(name)
        else:
            self.replan()

    def set_pdm_document(self, name):
        """List the revisions of a vault document (newest first)."""
        self.pdm_rev.clear()
        if self._vault_obj is None:
            self.pdm_note.setText(translate("Assembly", "Load the vault first."))
            return False
        self.form.setEnabled(False)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            from PDM import vault as V

            recs = run_bounded(lambda: self._vault_obj.history(name))
        except Exception as exc:  # noqa: BLE001
            self.pdm_note.setText(translate("Assembly", "The revisions could not be read: %s") % exc)
            return False
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
            self.form.setEnabled(True)
        seen = []
        for r in recs:
            if r["event"] in V.CONTENT_EVENTS and r["revision"] not in seen:
                seen.append(r["revision"])
        self.pdm_rev.blockSignals(True)
        self.pdm_rev.addItem("")
        for rev in seen:
            self.pdm_rev.addItem(rev)
        self.pdm_rev.blockSignals(False)
        self.pdm_doc_name = name
        return bool(seen)

    def _pdm_rev_picked(self):
        rev = self.pdm_rev.currentText()
        self._set_new(None)
        if rev:
            self.set_pdm_revision(self.pdm_doc.currentText(), rev)
        else:
            self.replan()

    def set_pdm_revision(self, name, revision, object_name=None):
        """Export one revision, open it hidden and offer its parts; True when a part was found."""
        self.source_combo.setCurrentIndex(KIND_PDM)
        self._fit_stack(KIND_PDM)
        self.pdm_note.setText("")
        self.pdm_object.clear()
        self._set_new(None)
        if self._vault_obj is None:
            self.pdm_note.setText(translate("Assembly", "Load the vault first."))
            self.replan()
            return False
        self.form.setEnabled(False)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            folder = run_bounded(lambda: self._vault_obj.export_revision(name, revision))
        except Exception as exc:  # noqa: BLE001
            self.pdm_note.setText(translate("Assembly", "The revision could not be exported: %s") % exc)
            self.replan()
            return False
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
            self.form.setEnabled(True)
        try:
            doc = self._open_hidden(folder, export=folder)
        except Exception as exc:  # noqa: BLE001
            self._cleanup_export(folder)
            self.pdm_note.setText(translate("Assembly", "Could not open the revision: %s") % exc)
            self.replan()
            return False
        self.pdm_objects = []
        return self._offer(doc, self.pdm_object, self.pdm_objects, object_name, translate("Assembly", "The revision holds no solid part."))

    def _pdm_object_picked(self):
        i = self.pdm_object.currentIndex()
        self._set_new(self.pdm_objects[i] if 0 <= i < len(self.pdm_objects) else None)
        self.replan()

    @staticmethod
    def _cleanup_export(folder):
        try:
            from PDM import vault as V

            V.cleanup_export(folder)
        except Exception:  # noqa: BLE001  (a leftover temp folder is not worth an error)
            pass

    def _close_temp(self, *_args):
        """Close the documents this panel opened and remove their exported folders."""
        temp, self.temp = self.temp, []
        for name, export in temp:
            try:
                if name in App.listDocuments():
                    App.closeDocument(name)
            except Exception:  # noqa: BLE001
                pass
            if export:
                self._cleanup_export(export)
        try:
            self._restore_active()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ planning

    def replan(self):
        """Plan again with the current old part, new part, alignment and choices; refills the table."""
        self.show_error("")
        self.report = None
        old, new = self.old, self.new_obj
        if old is not None and new is not None:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            try:
                self.report = RC.plan(self.assembly, old, new, choices=self.choices or None, alignment=self.alignment_plc)
            except Exception as exc:  # noqa: BLE001  (a panel never raises into the Qt event loop)
                self.show_error("%s: %s" % (type(exc).__name__, exc))
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
        self._fill_alignment()
        self._fill_table()
        self.summary.setText(self.summary_text())
        self._refresh_notice()

    @property
    def ready(self):
        return self.report is not None and self.report["alignment"]["method"] != "ambiguous"

    def summary_text(self):
        r = self.report
        if r is None:
            if self.old is None:
                return translate("Assembly", "Choose the component to replace.")
            if self.new_obj is None:
                return translate("Assembly", "Choose the new part.")
            return translate("Assembly", "The replacement could not be planned.")
        al = r["alignment"]
        if al["method"] == "ambiguous":
            return translate("Assembly", "The position of the new part is ambiguous: choose how it sits. %s") % al.get("note", "")
        c = r["references"]
        broken = [j for j in r["joints"] if j["outcome"] == "broken"]
        text = translate("Assembly", "%d joint references: %d matched, %d ambiguous, %d unmatched.") % (
            sum(c.values()), c["matched"], c["ambiguous"], c["unmatched"])
        if broken:
            text += " " + translate("Assembly", "%d joints will be kept suppressed and marked.") % len(broken)
        if al["method"] == "mirrored":
            text += " " + al.get("note", "")
        return text

    # ---- alignment

    def _fill_alignment(self):
        r = self.report
        cands = r["alignment"]["candidates"] if r is not None else []
        if cands:
            self.align_cands = cands
        elif r is None or r["alignment"]["method"] != "given":
            self.align_cands = []
        self.align_combo.blockSignals(True)
        self.align_combo.clear()
        if self.align_cands:
            self.align_combo.addItem(translate("Assembly", "(choose how the new part sits)"))
            for c in self.align_cands:
                if c["counts"] is None:
                    self.align_combo.addItem(translate("Assembly", "%s: cannot be used (%s)") % (c["label"], c["error"]))
                    item = self.align_combo.model().item(self.align_combo.count() - 1)
                    item.setEnabled(False)
                else:
                    n = c["counts"]
                    self.align_combo.addItem(translate("Assembly", "%s: %d matched, %d ambiguous, %d unmatched") % (
                        c["label"], n["matched"], n["ambiguous"], n["unmatched"]))
            idx = 0
            if self.alignment_plc is not None:
                for i, c in enumerate(self.align_cands):
                    if RC._plc_from_json(c["placement"]).isSame(self.alignment_plc, 1e-9):
                        idx = i + 1
            self.align_combo.setCurrentIndex(idx)
        self.align_combo.blockSignals(False)
        self.align_row.setVisible(bool(self.align_cands))

    def _alignment_picked(self):
        i = self.align_combo.currentIndex()
        self.choose_alignment(i - 1)

    def choose_alignment(self, index):
        """Pick candidate ``index`` of the alignment chooser (-1 for none); the choices for single references are dropped
        because their candidates belonged to the other reading."""
        self.choices = {}
        self.cands_seen = {}
        if 0 <= index < len(self.align_cands) and self.align_cands[index]["counts"] is not None:
            self.alignment_plc = RC._plc_from_json(self.align_cands[index]["placement"])
        else:
            self.alignment_plc = None
        self.replan()

    # ---- the table

    def _fill_table(self):
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.setRowCount(0)
        self.rows = []
        r = self.report
        if r is not None:
            for j in r["joints"]:
                for ref in j["references"]:
                    self.rows.append((j, ref))
        self.table.setRowCount(len(self.rows))
        for i, (j, ref) in enumerate(self.rows):
            els = ref["elements"]
            key = "%s.%s" % (j["joint"], ref["reference"])
            after = (translate("Assembly", "Remapped") if j["outcome"] == "remapped"
                     else translate("Assembly", "Broken: suppressed, marked"))
            plain = [j["label"], ref["reference"].replace("Reference", "Reference "),
                     " + ".join(dict.fromkeys(e["old_element"] or "-" for e in els)), STATUS_TEXT[ref["status"]], None, after]
            for col, text in enumerate(plain):
                if text is not None:
                    item = QtWidgets.QTableWidgetItem(text)
                    item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
                    self.table.setItem(i, col, item)
            self._new_cell(i, key, els)
        head = self.table.horizontalHeader()
        for col in range(len(TABLE_HEADERS)):
            head.setSectionResizeMode(col, QtWidgets.QHeaderView.Stretch if col == 4 else QtWidgets.QHeaderView.ResizeToContents)
        self.table.setTextElideMode(QtCore.Qt.ElideRight)
        self.table.blockSignals(False)

    def _new_cell(self, row, key, els):
        """Text for the matched and unmatched elements, a combo for each ambiguous one (or one the user settled)."""
        needs_widget = any(e["status"] == "ambiguous" or e["basis"] == "user" for e in els)
        parts = []
        for e in els:
            if e["status"] == "matched" and e["basis"] != "user":
                parts.append(("text", e["new_element"]))
            elif e["status"] == "unmatched":
                parts.append(("text", e["reason"] or translate("Assembly", "no match")))
            else:
                parts.append(("combo", e))
        if not needs_widget:
            item = QtWidgets.QTableWidgetItem(" + ".join(dict.fromkeys(p[1] for p in parts)))
            item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            item.setToolTip(item.text())
            self.table.setItem(row, 4, item)
            return
        w = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(2, 0, 2, 0)
        for n, (kind, val) in enumerate(parts):
            if kind == "text":
                lab = QtWidgets.QLabel(val)
                lab.setToolTip(val)
                h.addWidget(lab)
                continue
            e = val
            cands = [c["element"] for c in e["candidates"]] or self.cands_seen.get((key, e["old_element"]), [])
            if e["candidates"]:
                self.cands_seen[(key, e["old_element"])] = cands
            if not cands:
                h.addWidget(QtWidgets.QLabel(e["reason"] or translate("Assembly", "choose the alignment first")))
                continue
            combo = QtWidgets.QComboBox()
            combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(9)
            combo.setObjectName("candidates_%d_%d" % (row, n))
            combo.setAccessibleName(translate("Assembly", "New element for %s") % e["old_element"])
            combo.addItem(translate("Assembly", CHOOSE))
            costs = {c["element"]: c["cost"] for c in e["candidates"]}
            for name in cands:
                combo.addItem(name if name not in costs else "%s (cost %.3f)" % (name, costs[name]), name)
            chosen = e["new_element"] if e["basis"] == "user" else None
            if chosen and chosen not in cands:
                combo.addItem(chosen, chosen)
                cands = cands + [chosen]
            combo.setCurrentIndex(1 + cands.index(chosen) if chosen else 0)
            combo.installEventFilter(self._keys)
            combo.activated.connect(lambda idx, k=key, oe=e["old_element"], c=combo: self._candidate_picked(k, oe, c))
            h.addWidget(combo)
        self.table.setCellWidget(row, 4, w)

    def _candidate_picked(self, key, old_element, combo):
        self.choose_candidate(key, old_element, combo.currentData() if combo.currentIndex() > 0 else None)

    def choose_candidate(self, key, old_element, new_element):
        """Settle one ambiguous element (``key`` is ``"<Joint>.<Reference>"``); ``None`` takes the choice back."""
        picks = dict(self.choices.get(key, {}))
        if new_element:
            picks[old_element] = new_element
        else:
            picks.pop(old_element, None)
        if picks:
            self.choices[key] = picks
        else:
            self.choices.pop(key, None)
        self.replan()

    def candidate_combos(self, row):
        w = self.table.cellWidget(row, 4)
        return w.findChildren(QtWidgets.QComboBox) if w is not None else []

    def cell_text(self, row, col):
        item = self.table.item(row, col)
        if item is not None:
            return item.text()
        w = self.table.cellWidget(row, col)
        if w is None:
            return ""
        return " + ".join(x.text() if isinstance(x, QtWidgets.QLabel) else x.currentText() for x in w.children()
                          if isinstance(x, (QtWidgets.QLabel, QtWidgets.QComboBox)))

    # ------------------------------------------------------------------ task dialog protocol

    def getStandardButtons(self):
        box = QtWidgets.QDialogButtonBox
        ok, cancel = box.StandardButton.Ok, box.StandardButton.Cancel
        return int(getattr(ok, "value", ok)) | int(getattr(cancel, "value", cancel))

    def accept(self):
        """Apply the replacement (one undo step); stays open with a message when it cannot be applied."""
        if not self.ready:
            self.show_error(translate("Assembly", "Choose the component, the new part and, if asked, how it sits, before pressing OK."))
            return False
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self.applied = RC.apply(self.assembly, self.old, self.new_obj, choices=self.choices or None,
                                    delete_old=self.delete_old.isChecked(), alignment=self.alignment_plc,
                                    import_foreign=True)
        except Exception as exc:  # noqa: BLE001  (apply aborts its transaction; the panel stays open)
            self.show_error("%s: %s" % (type(exc).__name__, exc))
            return False
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.done = True
        self._close_temp()
        return True

    def reject(self):
        self.done = True
        self._close_temp()
        return True

    def clicked(self, button):
        return None

    def isAllowedAlterDocument(self):
        return False

    def isAllowedAlterView(self):
        return True

    def isAllowedAlterSelection(self):
        return True


# --------------------------------------------------------------------------- repair of one broken joint reference

def broken_joints(assembly):
    """Joints of ``assembly`` that a replace left with a broken reference."""
    return [o for o in assembly.Document.Objects if RC._is_joint(o) and RC.is_broken(o)]


def broken_references(joint):
    """The reference names of ``joint`` that still carry the broken-link marker."""
    out = []
    for prop in RC._JOINT_PROPS:
        comp, subs = getattr(joint, prop)
        if comp is not None and any("?" in s for s in subs):
            out.append(prop)
    return out


class TaskRepairJoint:
    """Pick the new element(s) of a joint reference that a replace left broken, and call ``ReplaceComponent.repair``."""

    title = "Repair joint reference"

    def __init__(self, assembly, joint=None):
        self.assembly = assembly
        self.done = False
        self.repaired = None
        self.form = QtWidgets.QWidget()
        self.form.setObjectName("Assembly_TaskRepairJoint")
        self.form.setWindowTitle(translate("Assembly", self.title))
        lay = QtWidgets.QVBoxLayout(self.form)
        fields = QtWidgets.QFormLayout()
        self.joints = broken_joints(assembly)
        self.joint_combo = QtWidgets.QComboBox()
        self.joint_combo.setObjectName("broken_joint")
        self.joint_combo.setAccessibleName(translate("Assembly", "Broken joint"))
        for j in self.joints:
            self.joint_combo.addItem(j.Label)
        fields.addRow(translate("Assembly", "Broken joint"), self.joint_combo)
        self.ref_combo = QtWidgets.QComboBox()
        self.ref_combo.setObjectName("broken_reference")
        self.ref_combo.setAccessibleName(translate("Assembly", "Reference"))
        fields.addRow(translate("Assembly", "Reference"), self.ref_combo)
        lay.addLayout(fields)
        self.why = QtWidgets.QLabel()
        self.why.setObjectName("why")
        self.why.setWordWrap(True)
        lay.addWidget(self.why)
        self.picks = QtWidgets.QFormLayout()
        lay.addLayout(self.picks)
        self.element_combos = []
        self.error = QtWidgets.QLabel()
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        self.error.setStyleSheet("background-color: #b00020; color: white; padding: 2px 6px;")
        self.error.hide()
        lay.addWidget(self.error)
        lay.addStretch(1)
        self.joint_combo.activated.connect(lambda *_: self._joint_changed())
        self.ref_combo.activated.connect(lambda *_: self._ref_changed())
        if joint in self.joints:
            self.joint_combo.setCurrentIndex(self.joints.index(joint))
        self._joint_changed()

    def show_error(self, text):
        self.error.setText(text)
        self.error.setVisible(bool(text))

    @property
    def joint(self):
        i = self.joint_combo.currentIndex()
        return self.joints[i] if 0 <= i < len(self.joints) else None

    @property
    def reference(self):
        return self.ref_combo.currentText()

    def _joint_changed(self):
        self.ref_combo.clear()
        j = self.joint
        if j is not None:
            self.ref_combo.addItems(broken_references(j))
            self.why.setText(getattr(j, RC.BROKEN_PROP, ""))
        else:
            self.why.setText(translate("Assembly", "No joint of this assembly is broken."))
        self._ref_changed()

    def _ref_changed(self):
        while self.picks.rowCount():
            self.picks.removeRow(0)
        self.element_combos = []
        self.show_error("")
        j, ref = self.joint, self.reference
        if j is None or not ref:
            return
        comp, subs = getattr(j, ref)
        for i, sub in enumerate(subs):
            old = RC._split_sub(sub.replace("?", ""))[1]
            if "?" not in sub:
                lab = QtWidgets.QLabel(old)
                self.picks.addRow(translate("Assembly", "Kept"), lab)
                self.element_combos.append(None)
                continue
            combo = QtWidgets.QComboBox()
            combo.setObjectName("new_element_%d" % i)
            combo.setAccessibleName(translate("Assembly", "New element for %s") % old)
            combo.addItem("")
            self._fill(combo, comp, subs, i)
            self.picks.addRow(translate("Assembly", "New element for %s") % old, combo)
            self.element_combos.append(combo)

    def _fill(self, combo, comp, subs, i):
        import Part

        path, old = RC._split_sub(subs[i].replace("?", ""))
        kind = old.rstrip("0123456789")
        try:
            if i == 1 and kind == "Edge" and RC._kind_of(RC._split_sub(subs[0].replace("?", ""))[1]) == "Face":
                n = len(Part.getShape(comp, path + RC._split_sub(subs[0].replace("?", ""))[1], needSubElement=True).Edges)
            else:
                shape = Part.getShape(comp, path, needSubElement=False)
                n = len(getattr(shape, kind + "s"))
        except Exception:  # noqa: BLE001  (no listing: the combo stays empty and the error says why on OK)
            n = 0
        for k in range(1, n + 1):
            combo.addItem("%s%d" % (kind, k))

    def set_element(self, index, name):
        """Pick ``name`` in the combo of sub-name ``index`` (what a click does)."""
        combo = self.element_combos[index]
        combo.setCurrentIndex(combo.findText(name))

    def getStandardButtons(self):
        box = QtWidgets.QDialogButtonBox
        ok, cancel = box.StandardButton.Ok, box.StandardButton.Cancel
        return int(getattr(ok, "value", ok)) | int(getattr(cancel, "value", cancel))

    def accept(self):
        j, ref = self.joint, self.reference
        if j is None or not ref:
            self.show_error(translate("Assembly", "There is no broken reference to repair."))
            return False
        comp, subs = getattr(j, ref)
        names = []
        for combo, sub in zip(self.element_combos, subs):
            if combo is None:
                names.append(RC._split_sub(sub)[1])
            elif not combo.currentText():
                self.show_error(translate("Assembly", "Choose the new element for every marked sub-name."))
                return False
            else:
                names.append(combo.currentText())
        try:
            self.repaired = RC.repair(j, ref, names)
        except Exception as exc:  # noqa: BLE001
            self.show_error("%s: %s" % (type(exc).__name__, exc))
            return False
        self.done = True
        return True

    def reject(self):
        self.done = True
        return True

    def clicked(self, button):
        return None

    def isAllowedAlterDocument(self):
        return False

    def isAllowedAlterView(self):
        return True

    def isAllowedAlterSelection(self):
        return True
