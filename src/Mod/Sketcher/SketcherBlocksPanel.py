# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2026 The FreeCAD project association AISBL              *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful, but        *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU     *
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************

"""Sketch blocks panel (G10 slice 5c): a dock over :mod:`SketcherBlocks` for the sketch in the editor.

It lists the definitions and their instances, defines a block from the selected geometry (the geometry stays plain
geometry), inserts an instance from a form or by one click in the 3D view, selects an instance's members, explodes and
deletes instances and definitions, and edits a definition in place through an edit session.  The dock is non-modal, so
the sketch's task dialog stays active.

The Library section (slice 5b1) lists the block files in the library folder of :mod:`SketcherBlocks`, with the folder in use,
and saves the chosen definition to it, loads a library block into the sketch (alone, or followed by one insert at the
form's X, Y and angle) and deletes a file after a second press.  A file that cannot be read is listed with its reason and
offers no action.  A load during an edit session is refused, since the session's rollback is a document undo.  The list is
read when the panel opens and after each library action; the API refuses a row whose file has gone.

Rows are keyed by name, never by position.  The panel reads the sketch again, deferred by one event-loop turn, after a
change to ``BlockData`` or to the number of geometries, and after undo and redo; a drag moves geometry without
changing either, so it costs the panel nothing.  An action on a row that has gone is refused by the API with a
sentence and changes nothing.

Behaviour follows public descriptions of sketch blocks (a definition placed many times, edited once, exploded back to
plain geometry); the layout is the plain list-and-buttons form of FreeCAD's task panels, and no layout, icon, text or
file format comes from another product."""

import math

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

import SketcherBlocks as B
import SketcherDiagnosisPanel as DP
import SketcherRelationsPanel as RP
from SketcherDiagnosisPanel import tr

try:
    from PySide6 import QtOpenGLWidgets
except Exception:  # pragma: no cover - the GUI build always has it
    QtOpenGLWidgets = None

DOCK_NAME = "SketcherBlocksPanel"
TREE_NAME = "blocksTree"
STATUS_NAME = "blocksStatus"
NAME_NAME = "blocksName"
DEFINE_NAME = "blocksDefine"
NOTE_NAME = "blocksDefineNote"
X_NAME = "blocksX"
Y_NAME = "blocksY"
ANGLE_NAME = "blocksAngle"
INSERT_NAME = "blocksInsert"
PLACE_NAME = "blocksPlace"
EXPLODE_NAME = "blocksExplode"
DELETE_NAME = "blocksDelete"
EDIT_NAME = "blocksEdit"
FINISH_NAME = "blocksFinish"
CANCEL_NAME = "blocksCancel"
SESSION_NAME = "blocksSession"
CLOSE_NAME = "blocksClose"
LIB_FOLDER_NAME = "blocksLibFolder"
LIB_LIST_NAME = "blocksLibList"
LIB_SAVE_NAME = "blocksLibSave"
LIB_LOAD_NAME = "blocksLibLoad"
LIB_INSERT_NAME = "blocksLibInsert"
LIB_DELETE_NAME = "blocksLibDelete"

_ROLE = QtCore.Qt.UserRole
_panel = None
messages = []  # the one-line messages the panel has given about an edit that ended on its own
_filters = []  # the view event filters that are installed, for the count


def active_filter_count():
    """How many view event filters this module has installed on a viewport right now."""
    return len(_filters)


# ---------------------------------------------------------------------------
# Numbers, geometry and the view
# ---------------------------------------------------------------------------


def parse_length(text):
    """A length in mm from text a FreeCAD quantity field accepts; a bare number is mm."""
    q = _parse(text, tr("a length"))
    if q.Unit == App.Units.Unit():
        return float(q.Value)
    if q.Unit == App.Units.Length:
        return float(q.getValueAs("mm"))
    raise B.SketchBlockError(tr("'%s' is not a length.") % text.strip())


def parse_angle(text):
    """An angle in radians from text a FreeCAD quantity field accepts; a bare number is degrees."""
    q = _parse(text, tr("an angle"))
    if q.Unit == App.Units.Unit():
        return math.radians(float(q.Value))
    if q.Unit == App.Units.Angle:
        return math.radians(float(q.getValueAs("deg")))
    raise B.SketchBlockError(tr("'%s' is not an angle.") % text.strip())


def _parse(text, what):
    text = (text or "").strip()
    if not text:
        raise B.SketchBlockError(tr("Enter %s.") % what)
    try:
        return App.Units.parseQuantity(text)
    except Exception:
        raise B.SketchBlockError(tr("'%s' is not a number or a quantity.") % text)


def vertex_index(sketch):
    """{(geo, pos): number} of the editor's ``VertexN`` names (N is number + 1)."""
    m = {}
    i = 0
    while True:
        try:
            geo, pos = sketch.getGeoVertexIndex(i)
        except Exception:
            break
        if geo == RP.GEO_UNDEF and pos == 0:
            break
        m[(geo, pos)] = i
        i += 1
    return m


def member_names(sketch, geo_ids):
    """The editor's selection names of the members, for the sketch as it is now."""
    vix = None
    names = []
    geos = sketch.Geometry
    for g in geo_ids:
        if g < 0 or g >= len(geos):
            continue
        if geos[g].TypeId == "Part::GeomPoint":
            if vix is None:
                vix = vertex_index(sketch)
            n = RP.selection_names_of_element(sketch, g, 1, vix)
        else:
            n = "Edge%d" % (g + 1)
        if n is not None:
            names.append(n)
    return names


def find_viewport():
    """The OpenGL widget of the active 3D view, or None."""
    if QtOpenGLWidgets is None:
        return None
    mw = Gui.getMainWindow()
    area = mw.findChild(QtWidgets.QMdiArea)
    scopes = []
    if area is not None and area.activeSubWindow() is not None:
        scopes.append(area.activeSubWindow())
    scopes.append(mw)
    for scope in scopes:
        for g in scope.findChildren(QtOpenGLWidgets.QOpenGLWidget):
            p = g.parentWidget()
            if p is not None and p.metaObject().className() == "Gui::View3DInventorViewer" and g.isVisible():
                return g
    return None


def _editing_placement(sketch):
    """The placement of the sketch plane in the frame the editor's view works in: the document's editing transform, which
    is what the stock tools use.  For a sketch in a Body it is the sketch's placement within the Body (the editor shows the
    Body in its own frame), not the global placement."""
    try:
        m = Gui.getDocument(sketch.Document.Name).EditingTransform
        return App.Placement(m)
    except Exception:
        return sketch.getGlobalPlacement()


def point_in_sketch(sketch, view, gl, pos):
    """The sketch-frame point under a Qt position of the viewport: the pixel's ray meets the sketch plane taken
    through the editor's own placement of the sketch, which is what the stock drawing tools do."""
    dpr = gl.devicePixelRatioF()
    # the viewer's own conversion (Quarter's InputDevice::toDevicePixelPosition): the flip is done in logical pixels,
    # on the widget's logical height, and the result is scaled to device pixels and rounded half away from zero; flipping in
    # device pixels is one device pixel off at a pixel ratio of 2
    x = int(math.floor(pos.x() * dpr + 0.5))
    y = int(math.floor((gl.height() - pos.y() - 1.0) * dpr + 0.5))
    p0, p1 = view.projectPointToLine(x, y)
    gp = _editing_placement(sketch)
    normal = gp.Rotation.multVec(App.Vector(0, 0, 1))
    d = p1 - p0
    denom = normal.dot(d)
    if abs(denom) < 1e-12 * max(d.Length, 1.0):
        raise B.SketchBlockError(tr("The view looks along the sketch plane, so a click does not name a point on it."))
    t = normal.dot(gp.Base - p0) / denom
    local = gp.inverse().multVec(p0 + d * t)
    return float(local.x), float(local.y)


# ---------------------------------------------------------------------------
# Watching the document, the selection and the viewport
# ---------------------------------------------------------------------------


class _DocWatch:
    def __init__(self, panel):
        self.panel = panel

    def _mine(self, obj):
        try:
            return obj is not None and obj.Document.Name == self.panel.doc_name and obj.Name == self.panel.sketch_name
        except Exception:
            return False

    def slotChangedObject(self, obj, prop):
        if self._mine(obj):
            self.panel.on_property(prop)

    def slotUndoDocument(self, doc):
        if doc.Name == self.panel.doc_name:
            self.panel.on_undo_redo(-1)

    def slotRedoDocument(self, doc):
        if doc.Name == self.panel.doc_name:
            self.panel.on_undo_redo(+1)

    def slotCommitTransaction(self, doc):
        if doc.Name == self.panel.doc_name:
            self.panel.on_commit()

    def slotDeletedObject(self, obj):
        if self._mine(obj):
            self.panel.invalidate(tr("The sketch was deleted."), close=True)

    def slotDeletedDocument(self, doc):
        if doc.Name == self.panel.doc_name:
            self.panel.invalidate(tr("The document was closed."), close=True)


class _SelWatch:
    def __init__(self, panel):
        self.panel = panel

    def _event(self, *_):
        self.panel.on_selection()

    addSelection = removeSelection = setSelection = clearSelection = _event


class _ViewFilter(QtCore.QObject):
    """Sees the viewport's events before the view does; while a placement is waiting for its click it takes the left and
    right buttons and Escape, and nothing else."""

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel

    def eventFilter(self, obj, ev):
        try:
            if ev.type() == QtCore.QEvent.CursorChange:
                self.panel.on_cursor_change(obj)
                return False
            return bool(self.panel.on_view_event(ev))
        except Exception as exc:  # an exception in a filter must never reach Qt
            App.Console.PrintError("Sketch blocks: %s\n" % exc)
            return False


# ---------------------------------------------------------------------------
# The panel
# ---------------------------------------------------------------------------


class BlocksPanel(QtWidgets.QDockWidget):
    def __init__(self, sketch, parent=None):
        super().__init__(tr("Sketch blocks"), parent or Gui.getMainWindow())
        self.setObjectName(DOCK_NAME)
        self.sketch = sketch
        self.doc_name = sketch.Document.Name
        self.sketch_name = sketch.Name
        self.state = "ready"  # ready, closed
        self.info = None  # B.blocks(sketch) as last read, None when unreadable
        self.member_of = {}  # GeoId -> instance name, for the selection
        self.broken = None  # the sentence when BlockData cannot be read
        self._geo_count = sketch.GeometryCount
        self._pending = False
        self._own_change = False
        self._tree_sync = False
        self._closing = False
        self._sticky = None  # the message that ended an edit; refresh leaves it on screen until the next user action
        self._placing = None  # the definition being placed by a click
        self._pressed = False
        self._pointer_busy = False
        self._dormant = False  # the sketch is still in edit, but its document is not the one in view
        self._status_when_active = ""
        self._viewport = None
        self._viewer = None
        self._view_filter = None
        self.session = None  # the edit session, see the end of the class
        self._lib_armed = None  # ("delete" | "save", name): the first press of a two-press action
        self._lib_rows = []  # B.library_list() as last read
        self._lib_shown = None  # the folder the list was read from; a row acts on that folder or on none
        self._build()
        self._dw = _DocWatch(self)
        self._gw = DP._GuiWatch(self)
        self._sw = _SelWatch(self)
        App.addDocumentObserver(self._dw)
        Gui.addDocumentObserver(self._gw)
        Gui.Selection.addObserver(self._sw)
        self.refresh()
        self.refresh_library()

    # -- widgets -----------------------------------------------------------
    def _build(self):
        body = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(body)
        self.status = QtWidgets.QLabel(tr("Select geometry, name it and choose Define to make a block."))
        self.status.setObjectName(STATUS_NAME)
        self.status.setWordWrap(True)
        self.status.setAccessibleName(tr("Blocks status"))
        lay.addWidget(self.status)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setObjectName(TREE_NAME)
        self.tree.setHeaderHidden(True)
        self.tree.setWordWrap(True)
        self.tree.setTextElideMode(QtCore.Qt.ElideNone)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.tree.setAccessibleName(tr("Block definitions and their instances"))
        self.tree.setToolTip(tr("Choose a definition to place it, or an instance to select its members in the view"))
        self.tree.setMinimumHeight(140)
        self.tree.setStyleSheet(
            "QTreeWidget::item:selected, QTreeWidget::item:selected:!active "
            "{ background: palette(highlight); color: palette(highlighted-text); }"
        )
        self.tree.itemSelectionChanged.connect(self._on_choice)
        lay.addWidget(self.tree)

        row = QtWidgets.QHBoxLayout()
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setObjectName(NAME_NAME)
        self.name_edit.setPlaceholderText(tr("Name of the new block"))
        self.name_edit.setAccessibleName(tr("Name of the new block"))
        self.define_button = QtWidgets.QPushButton(tr("Define"))
        self.define_button.setObjectName(DEFINE_NAME)
        self.define_button.setToolTip(tr("Makes a block from the selected geometry. The geometry stays plain geometry"))
        self.define_button.setAccessibleName(tr("Define block from selection"))
        self.define_button.clicked.connect(self.define_from_selection)
        row.addWidget(self.name_edit, 1)
        row.addWidget(self.define_button)
        lay.addLayout(row)
        note = QtWidgets.QLabel(tr("Defining leaves the selected geometry as it is. Insert places copies that move as one."))
        note.setObjectName(NOTE_NAME)
        note.setWordWrap(True)
        lay.addWidget(note)

        form = QtWidgets.QFormLayout()
        self.x_edit = QtWidgets.QLineEdit("0 mm")
        self.x_edit.setObjectName(X_NAME)
        self.y_edit = QtWidgets.QLineEdit("0 mm")
        self.y_edit.setObjectName(Y_NAME)
        self.angle_edit = QtWidgets.QLineEdit("0 deg")
        self.angle_edit.setObjectName(ANGLE_NAME)
        for edit, text in ((self.x_edit, tr("X")), (self.y_edit, tr("Y")), (self.angle_edit, tr("Angle"))):
            edit.setAccessibleName(text)
            edit.setToolTip(tr("A number or a quantity such as 25.4 mm, 1 in or 90 deg"))
            form.addRow(text, edit)
        lay.addLayout(form)
        prow = QtWidgets.QHBoxLayout()
        self.insert_button = self._button(tr("Insert"), INSERT_NAME, tr("Places the chosen block at X, Y and the angle"), self.insert_by_number)
        self.place_button = self._button(tr("Place by click"), PLACE_NAME, tr("Click in the view to place the chosen block there. Escape or a right click cancels"), self.start_place)
        prow.addWidget(self.insert_button)
        prow.addWidget(self.place_button)
        lay.addLayout(prow)

        arow = QtWidgets.QHBoxLayout()
        self.explode_button = self._button(tr("Explode"), EXPLODE_NAME, tr("Turns the chosen instance into plain geometry"), self.explode_chosen)
        self.delete_button = self._button(tr("Delete"), DELETE_NAME, tr("Deletes the chosen instance with its members, or the chosen definition if it has no instance"), self.delete_chosen)
        self.edit_button = self._button(tr("Edit in place"), EDIT_NAME, tr("Opens the chosen instance as ordinary geometry; Finish applies the result to the definition and every instance"), self.start_session)
        arow.addWidget(self.explode_button)
        arow.addWidget(self.delete_button)
        arow.addWidget(self.edit_button)
        lay.addLayout(arow)

        head = QtWidgets.QLabel(tr("Library"))
        head.setObjectName("blocksLibHead")
        lay.addWidget(head)
        self.lib_folder = QtWidgets.QLabel("")
        self.lib_folder.setObjectName(LIB_FOLDER_NAME)
        self.lib_folder.setWordWrap(True)
        self.lib_folder.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lay.addWidget(self.lib_folder)
        self.lib_list = QtWidgets.QListWidget()
        self.lib_list.setObjectName(LIB_LIST_NAME)
        self.lib_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.lib_list.setWordWrap(True)
        self.lib_list.setTextElideMode(QtCore.Qt.ElideNone)
        self.lib_list.setMinimumHeight(70)
        self.lib_list.setAccessibleName(tr("Blocks in the library folder"))
        self.lib_list.setToolTip(tr("Blocks saved as files. Loading copies one into this sketch; the sketch keeps no link to the file"))
        self.lib_list.setStyleSheet(
            "QListWidget::item:selected, QListWidget::item:selected:!active "
            "{ background: palette(highlight); color: palette(highlighted-text); }"
        )
        self.lib_list.itemSelectionChanged.connect(self._on_lib_choice)
        lay.addWidget(self.lib_list)
        self.lib_save_button = self._button(tr("Save"), LIB_SAVE_NAME, tr("Saves the chosen definition to the library folder"), self.library_save_chosen)
        self.lib_load_button = self._button(tr("Load"), LIB_LOAD_NAME, tr("Copies the chosen library block into this sketch as a definition"), self.library_load_chosen)
        self.lib_insert_button = self._button(tr("Load and insert"), LIB_INSERT_NAME, tr("Copies the chosen library block into this sketch and places it at X, Y and the angle"), self.library_load_and_insert)
        self.lib_delete_button = self._button(tr("Delete file"), LIB_DELETE_NAME, tr("Deletes the chosen block's file from the library folder. Press twice. Sketches that loaded it are not changed"), self.library_delete_chosen)
        lrow = QtWidgets.QGridLayout()
        for i, b in enumerate((self.lib_save_button, self.lib_load_button, self.lib_insert_button, self.lib_delete_button)):
            lrow.addWidget(b, i // 2, i % 2)  # two by two: four buttons in a row overlap in a narrow dock
        lay.addLayout(lrow)

        self.session_label = QtWidgets.QLabel("")
        self.session_label.setObjectName(SESSION_NAME)
        self.session_label.setWordWrap(True)
        self.session_label.hide()
        lay.addWidget(self.session_label)
        srow = QtWidgets.QHBoxLayout()
        self.finish_button = self._button(tr("Finish"), FINISH_NAME, tr("Applies the edit to the definition and every instance, as one undo step"), self.finish_session)
        self.cancel_button = self._button(tr("Cancel edit"), CANCEL_NAME, tr("Puts the sketch back as it was before the edit"), self.cancel_session)
        srow.addWidget(self.finish_button)
        srow.addWidget(self.cancel_button)
        self.finish_button.hide()
        self.cancel_button.hide()
        lay.addLayout(srow)

        self.close_button = self._button(tr("Close"), CLOSE_NAME, tr("Closes the panel and leaves the sketch as it is"), self.close)
        lay.addWidget(self.close_button)
        self.setWidget(body)
        self.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea)
        self.setFeatures(QtWidgets.QDockWidget.DockWidgetClosable | QtWidgets.QDockWidget.DockWidgetMovable)
        self._update_buttons()

    def _button(self, text, name, tip, slot):
        b = QtWidgets.QPushButton(text)
        b.setObjectName(name)
        b.setToolTip(tip)
        b.setAccessibleName(text)
        b.clicked.connect(lambda *_: (self._user_action(), slot()))
        return b

    # -- reading the sketch -------------------------------------------------------
    def _alive(self):
        return self.state != "closed" and not self._closing

    def refresh(self):
        """Read the sketch again and rebuild the list; the chosen row stays chosen if it still exists."""
        self._pending = False
        if not self._alive():
            return
        self._geo_count = self.sketch.GeometryCount
        chosen = self.chosen()
        try:
            info = B.blocks(self.sketch)
        except B.SketchBlockError as exc:
            self.info = None
            self.member_of = {}
            self.broken = _sentence(str(exc))
            self._fill(None, None)
            self.status.setText(self.broken)
            self._update_buttons()
            return
        self.broken = None
        self.info = info
        self.member_of = {g: n for n, i in info["instances"].items() for g in i["geo_ids"] if g >= 0}
        self._fill(info, chosen)
        if self.session is not None:
            self._update_session_label()
        if self.session is None and not self._placing:
            nd, ni = len(info["definitions"]), len(info["instances"])
            if self._sticky is None:  # a message that ended an edit stays until the user next does something in the panel
                self.status.setText(tr("%d definition(s), %d instance(s).") % (nd, ni) if nd else tr("Select geometry, name it and choose Define to make a block."))
        self._update_buttons()

    def _fill(self, info, chosen):
        self._tree_sync = True
        self.tree.blockSignals(True)
        try:
            self.tree.setCurrentItem(None)
            self.tree.clear()
            if info is None:
                return
            by_def = {}
            for iname, inst in info["instances"].items():
                by_def.setdefault(inst["definition"], []).append(iname)
            for dname, members in info["definitions"].items():
                insts = by_def.get(dname, [])
                top = QtWidgets.QTreeWidgetItem(self.tree, [tr("%s: %d member(s), %d instance(s)") % (dname, len(members), len(insts))])
                top.setData(0, _ROLE, {"kind": "def", "name": dname})
                for iname in insts:
                    inst = info["instances"][iname]
                    child = QtWidgets.QTreeWidgetItem(top, [tr("%s: %d member(s)") % (iname, len(inst["geo_ids"]))])
                    child.setData(0, _ROLE, {"kind": "inst", "name": iname, "def": dname})
                top.setExpanded(True)
            if chosen is not None:
                self._choose_row(chosen[0], chosen[1])
        finally:
            self.tree.blockSignals(False)
            self._tree_sync = False

    def _choose_row(self, kind, name):
        it = self.tree.invisibleRootItem()
        stack = [it.child(i) for i in range(it.childCount())]
        while stack:
            item = stack.pop()
            d = item.data(0, _ROLE) or {}
            if d.get("kind") == kind and d.get("name") == name:
                self.tree.setCurrentItem(item)
                item.setSelected(True)
                return True
            stack += [item.child(i) for i in range(item.childCount())]
        return False

    def chosen(self):
        """(kind, name) of the chosen row, or None."""
        items = self.tree.selectedItems()
        if not items:
            return None
        d = items[0].data(0, _ROLE) or {}
        return (d["kind"], d["name"]) if "kind" in d else None

    def _chosen_def(self):
        c = self.chosen()
        if c is None:
            return None
        if c[0] == "def":
            return c[1]
        return (self.tree.selectedItems()[0].data(0, _ROLE) or {}).get("def")

    def _update_buttons(self):
        ok = self._alive() and self.broken is None and not self._dormant
        busy = self.session is not None
        c = self.chosen()
        d = self._chosen_def()
        self.define_button.setEnabled(ok and not busy)
        self.name_edit.setEnabled(ok and not busy)
        self.insert_button.setEnabled(ok and not busy and d is not None)
        self.place_button.setEnabled(ok and not busy and d is not None)
        self.explode_button.setEnabled(ok and not busy and c is not None and c[0] == "inst")
        self.delete_button.setEnabled(ok and not busy and c is not None)
        self.edit_button.setEnabled(ok and not busy and c is not None and c[0] == "inst")
        self.tree.setEnabled(ok and not busy)
        lib = self.chosen_library()
        self.lib_save_button.setEnabled(ok and d is not None)
        self.lib_load_button.setEnabled(ok and not busy and lib is not None)
        self.lib_insert_button.setEnabled(ok and not busy and lib is not None)
        self.lib_delete_button.setEnabled(self._alive() and lib is not None)
        self.finish_button.setVisible(busy)
        self.cancel_button.setVisible(busy)
        self.session_label.setVisible(busy)
        self.finish_button.setEnabled(busy and not self._dormant)
        self.cancel_button.setEnabled(busy and not self._dormant)

    # -- running an action -----------------------------------------------------------
    def _edit_now(self, active=None):
        try:
            if self.doc_name not in App.listDocuments() or App.getDocument(self.doc_name).getObject(self.sketch_name) is None:
                return "gone"
        except Exception:
            return "gone"
        return DP.edit_state(self.sketch, active)

    def sync_activation(self, active=None):
        """Bring the panel in line with where the editor is, as the other sketch docks do: go quiet while the sketch's
        document is out of view and pick up again, with the status it had, when the user comes back; close if the sketch
        is gone from the editor.  (The shared document watch calls this on every document activation.)"""
        if not self._alive():
            return
        state = self._edit_now(active)
        if state == "inactive":
            if not self._dormant:
                self._dormant = True
                self._status_when_active = self.status.text()
                self.cancel_place()
                self.status.setText(tr("The sketch is in another document's tab. Come back to it to go on."))
                self._update_buttons()
        elif state == "gone":
            self.invalidate(tr("The sketch is no longer being edited."), close=True)
        elif self._dormant:
            self._dormant = False
            self.status.setText(self._status_when_active)
            self._update_buttons()

    def _editing_ok(self):
        if not self._alive():
            return False
        self.sync_activation()
        if self._dormant or not self._alive():
            return False
        if DP.edit_state(self.sketch) != "editing":
            self.status.setText(tr("The sketch is not being edited."))
            return False
        return True

    def _run(self, fn, done=None):
        """Run an API call; a refusal shows its sentence and changes nothing.  Returns the result or None."""
        if not self._editing_ok():
            return None
        try:
            res = fn()
        except B.SketchBlockError as exc:
            self.refresh()
            self.status.setText(_sentence(str(exc)))
            return None
        except Exception as exc:
            self.refresh()
            self.status.setText(tr("That could not be done (%s).") % _sentence(str(exc)).rstrip("."))
            return None
        self.refresh()
        if done is not None:
            self.status.setText(done(res))
        return res if res is not None else True

    # -- define ----------------------------------------------------------------------
    def user_geo_ids(self):
        """The GeoIds of the user's selection in the editor, in selection order."""
        out = []
        for s in Gui.Selection.getSelectionEx(self.doc_name):
            if s.ObjectName != self.sketch_name:
                continue
            for sub in s.SubElementNames:
                if sub.startswith("Constraint"):
                    raise B.SketchBlockError(tr("A constraint cannot be part of a block; select geometry."))
                el = RP.element_of_selection_name(self.sketch, sub)
                if el is None:
                    raise B.SketchBlockError(tr("%s cannot be part of a block; select points, lines, circles, arcs, ellipses or B-splines.") % sub)
                geo, pos = el
                if geo in (-1, -2):
                    raise B.SketchBlockError(tr("The origin and the axes cannot be part of a block."))
                if pos and geo >= 0 and self.sketch.Geometry[geo].TypeId != "Part::GeomPoint":
                    raise B.SketchBlockError(tr("Select the whole line, circle or arc, not one of its points."))
                if geo not in out:
                    out.append(geo)
        return out

    def define_from_selection(self):
        name = self.name_edit.text()

        def go():
            ids = self.user_geo_ids()
            rep = B.define_report(self.sketch, name, ids)
            return rep["name"], len(ids) - len(rep["left_out"]), len(rep["left_out"])

        res = self._run(go)
        if res and res is not True:
            self._choose_row("def", res[0])
            self._update_buttons()
            text = tr("Defined '%s' from %d member(s). The geometry is still plain geometry; Insert places copies.") % res[:2]
            if res[2]:
                text += " " + tr("Left out %d axes, foci, pole circles or knot points that belong to a curve; a block holds the curve alone.") % res[2]
            self.status.setText(text)
            self.name_edit.clear()
        return res

    # -- insert ----------------------------------------------------------------------
    def insert_by_number(self):
        d = self._chosen_def()

        def go():
            if d is None:
                raise B.SketchBlockError(tr("Choose a definition first."))
            x, y = parse_length(self.x_edit.text()), parse_length(self.y_edit.text())
            return B.insert(self.sketch, d, x, y, parse_angle(self.angle_edit.text()))

        res = self._run(go)
        if res and res is not True:
            self.status.setText(tr("Inserted %s.") % res)
        return res

    def start_place(self):
        d = self._chosen_def()
        if not self._editing_ok():
            return False
        if d is None or d not in (self.info or {}).get("definitions", {}):
            self.status.setText(tr("Choose a definition first."))
            return False
        try:
            parse_angle(self.angle_edit.text())
        except B.SketchBlockError as exc:
            self.status.setText(_sentence(str(exc)))
            return False
        gl = find_viewport()
        if gl is None:
            self.status.setText(tr("There is no 3D view to click in."))
            return False
        self.end_place(None)
        self._viewport = gl
        self._view_filter = _ViewFilter(self)
        gl.installEventFilter(self._view_filter)
        self._viewer = gl.parentWidget()
        self._viewer.installEventFilter(self._view_filter)
        self._viewer.setCursor(QtCore.Qt.CrossCursor)
        _filters.append(self._view_filter)
        self._placing = d
        self._pressed = False
        self.status.setText(tr("Click in the view to place '%s'. Escape or a right click cancels.") % d)
        return True

    def end_place(self, message):
        """Leave the placement mode; the filter is removed whatever happened."""
        if self._view_filter is not None:
            try:
                if self._viewport is not None:
                    self._viewport.removeEventFilter(self._view_filter)
                if self._viewer is not None:
                    self._viewer.removeEventFilter(self._view_filter)
                    self._viewer.unsetCursor()
            except Exception:
                pass
            if self._view_filter in _filters:
                _filters.remove(self._view_filter)
            self._view_filter.deleteLater()
        self._view_filter = None
        self._viewport = None
        self._viewer = None
        was = self._placing
        self._placing = None
        self._pressed = False
        if message and was is not None and self._alive():
            self.status.setText(message)

    def _pointer_idle(self):
        self._pointer_busy = False

    def cancel_place(self, message=None):
        if self._placing is not None:
            self.end_place(message or tr("Placing was cancelled."))

    def on_cursor_change(self, obj):
        """A stock tool sets a pixmap cursor of its own when it starts, and that ends the placement.  Navigation puts the
        plain arrow back when it ends; the cross comes back with it."""
        if self._placing is None or obj is not self._viewer:
            return
        if self._pointer_busy:  # navigation sets its own cursors while the pointer is being dragged
            return
        shape = obj.cursor().shape()
        if shape == QtCore.Qt.BitmapCursor:
            self.cancel_place(tr("Placing was cancelled because a tool was started."))
        elif shape == QtCore.Qt.ArrowCursor:
            obj.setCursor(QtCore.Qt.CrossCursor)

    def on_view_event(self, ev):
        """True when the event is taken.  Only while a placement waits for its click."""
        if self._placing is None or not self._alive():
            return False
        t = ev.type()
        E = QtCore.QEvent
        if t in (E.MouseButtonPress, E.MouseButtonRelease, E.MouseMove, E.MouseButtonDblClick, E.Wheel):
            self._pointer_busy = True
            QtCore.QTimer.singleShot(0, self._pointer_idle)
        if t == E.KeyPress:
            if ev.key() == QtCore.Qt.Key_Escape:
                self.cancel_place()
                return True
            return False
        if t not in (E.MouseButtonPress, E.MouseButtonRelease, E.MouseButtonDblClick):
            return False
        if DP.edit_state(self.sketch) != "editing":
            self.cancel_place(tr("Placing was cancelled because the sketch is not being edited."))
            return False
        b = ev.button()
        if b == QtCore.Qt.RightButton:
            # both halves are taken, so the editor never sees half a right click (it would open its menu); it cancels on the release
            if t == E.MouseButtonRelease:
                self.cancel_place()
            return True
        if b != QtCore.Qt.LeftButton or ev.modifiers() != QtCore.Qt.NoModifier:
            return False
        if t == E.MouseButtonPress or t == E.MouseButtonDblClick:
            self._pressed = True
            return True
        if not self._pressed:
            return False
        self._pressed = False
        self.place_at(ev.position())
        return True

    def place_at(self, pos):
        d = self._placing
        gl = self._viewport
        self.end_place(None)

        def go():
            view = Gui.getDocument(self.doc_name).ActiveView
            x, y = point_in_sketch(self.sketch, view, gl, pos)
            return B.insert(self.sketch, d, x, y, parse_angle(self.angle_edit.text()))

        res = self._run(go)
        if res and res is not True:
            self.status.setText(tr("Inserted %s.") % res)

    # -- explode and delete --------------------------------------------------------------
    def explode_chosen(self):
        c = self.chosen()
        if c is None or c[0] != "inst":
            self.status.setText(tr("Choose an instance first."))
            return None
        name = c[1]
        return self._run(lambda: B.explode(self.sketch, name), lambda _: tr("Exploded %s into plain geometry.") % name)

    def delete_chosen(self):
        c = self.chosen()
        if c is None:
            self.status.setText(tr("Choose an instance or a definition first."))
            return None
        kind, name = c
        if kind == "inst":

            def done(names):
                if names:
                    return tr("Deleted %s with its members. Constraints removed: %s.") % (name, ", ".join(names))
                return tr("Deleted %s with its members. No constraint was removed.") % name

            return self._run(lambda: B.delete_instance(self.sketch, name) or [], done)
        return self._run(lambda: B.delete_definition(self.sketch, name), lambda _: tr("Deleted the definition %s.") % name)

    # -- library ---------------------------------------------------------------------------
    def refresh_library(self):
        """Read the library folder again and rebuild its list; the chosen row stays chosen if it is still there."""
        keep = self.chosen_library()
        self._lib_armed = None
        try:
            self._lib_shown = B.library_folder()
            self.lib_folder.setText(tr("Folder: %s") % self._lib_shown)
            self._lib_rows = B.library_list()
            note = ""
        except B.SketchBlockError as exc:
            self._lib_rows = []
            note = _sentence(str(exc))
        self.lib_list.blockSignals(True)
        try:
            self.lib_list.clear()
            for r in self._lib_rows:
                if r["error"] is None:
                    item = QtWidgets.QListWidgetItem(tr("%s: %d member(s)") % (r["name"], r["members"]))
                else:
                    item = QtWidgets.QListWidgetItem(tr("%s: cannot be used (%s)") % (r["name"], _sentence(r["error"]).rstrip(".")))
                    item.setToolTip(r["error"])
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsSelectable & ~QtCore.Qt.ItemIsEnabled)
                item.setData(_ROLE, {"name": r["name"], "ok": r["error"] is None})
                self.lib_list.addItem(item)
                if keep is not None and r["error"] is None and r["name"] == keep:
                    item.setSelected(True)
                    self.lib_list.setCurrentItem(item)
            if note:
                self.lib_list.addItem(QtWidgets.QListWidgetItem(note))
                self.lib_list.item(self.lib_list.count() - 1).setFlags(QtCore.Qt.NoItemFlags)
        finally:
            self.lib_list.blockSignals(False)
        self._update_buttons()

    def chosen_library(self):
        """The name of the chosen library row, or None (a row that cannot be used is never chosen)."""
        items = self.lib_list.selectedItems()
        if not items:
            return None
        d = items[0].data(_ROLE) or {}
        return d["name"] if d.get("ok") else None

    def _on_lib_choice(self):
        self._user_action()
        self._lib_armed = None
        self._update_buttons()

    def _lib_name(self):
        name = self.chosen_library()
        if name is None:
            raise B.SketchBlockError(tr("Choose a block in the library first."))
        return name

    def _lib_run(self, fn, done=None, touches_sketch=True):
        """Like _run for the library: a refusal shows the API's sentence, the list is read again."""
        self._lib_armed = None
        try:
            now = B.library_folder()
        except B.SketchBlockError:
            now = None
        shown = self._lib_shown
        if shown is not None and now != shown:
            self.refresh_library()
            self.status.setText(tr("The library folder changed from %s to %s since the list was shown. The list is read again; choose the block again.") % (shown, now))
            return None
        if touches_sketch and self.session is not None:
            self.status.setText(tr("Finish or cancel the edit first: a block cannot be loaded during one."))
            return None
        res = self._run(fn, done) if touches_sketch else self._run_plain(fn, done)
        self.refresh_library()
        return res

    def _run_plain(self, fn, done):
        if not self._alive():
            return None
        try:
            res = fn()
        except B.SketchBlockError as exc:
            self.status.setText(_sentence(str(exc)))
            return None
        except Exception as exc:
            self.status.setText(tr("That could not be done (%s).") % _sentence(str(exc)).rstrip("."))
            return None
        if done is not None:
            self.status.setText(done(res))
        return res if res is not None else True

    def library_save_chosen(self):
        d = self._chosen_def()
        if d is None:
            self.status.setText(tr("Choose a definition first."))
            return None
        again = self._lib_armed == ("save", d)
        exists = any(r["name"] == d for r in self._lib_rows)
        if exists and not again:
            self._lib_armed = ("save", d)
            self.status.setText(tr("The library already has a block called '%s'. Press Save again to replace it.") % d)
            return None
        return self._lib_run(lambda: B.library_save(self.sketch, d, overwrite=again), lambda p: tr("Saved '%s' to the library.") % d, touches_sketch=False)

    def library_load_chosen(self):
        def go():
            return B.library_load(self.sketch, self._lib_name())

        name = self.chosen_library()
        return self._lib_run(go, lambda n: tr("Loaded '%s' into the sketch. The sketch keeps its own copy; changing the file later changes nothing here.") % n) and self._pick_loaded(name)

    def _pick_loaded(self, name):
        if name is not None:
            self._choose_row("def", name)
            self._update_buttons()
        return True

    def library_load_and_insert(self):
        """Load the chosen block, then place one instance at the X, Y and angle of the form.  The form is read first, so
        a bad value refuses before anything is loaded; the two steps are the API's two calls, each its own undo entry,
        and the status says what stands when the second one fails."""
        def go():
            name = self._lib_name()
            x, y = parse_length(self.x_edit.text()), parse_length(self.y_edit.text())
            angle = parse_angle(self.angle_edit.text())
            had = name in B._load(self.sketch)["defs"]
            loaded = B.library_load(self.sketch, name)
            new = not had
            try:
                inst = B.insert(self.sketch, loaded, x, y, angle)
            except B.SketchBlockError as exc:
                if new:
                    raise B.SketchBlockError(
                        tr("Loaded '%s' into the sketch (one undo step) but it was not inserted: %s Undo once to remove the loaded block.") % (loaded, _sentence(str(exc)))
                    )
                raise
            return loaded, inst, new

        def done(r):
            if r[2]:
                return tr("Loaded '%s' and inserted %s. That took two undo steps: the load and the insert.") % (r[0], r[1])
            return tr("'%s' was already in the sketch; inserted %s (one undo step).") % (r[0], r[1])

        res = self._lib_run(go, done)
        if res and res is not True:
            self._pick_loaded(res[0])
        return res

    def library_delete_chosen(self):
        name = self.chosen_library()
        if name is None:
            self.status.setText(tr("Choose a block in the library first."))
            return None
        if self._lib_armed != ("delete", name):
            self._lib_armed = ("delete", name)
            self.status.setText(tr("Press Delete file again to remove '%s' from the library folder. Sketches that loaded it are not changed.") % name)
            return None
        return self._lib_run(lambda: B.library_delete(name), lambda _: tr("Deleted '%s' from the library.") % name, touches_sketch=False)

    # -- selection both ways --------------------------------------------------------------
    def _user_action(self):
        """The user did something in the panel: a message that ended an edit has been on screen long enough."""
        if self._sticky is not None:
            self._sticky = None
            self.schedule()

    def _on_choice(self):
        if self._tree_sync:
            return
        self._user_action()
        self._update_buttons()
        c = self.chosen()
        if c is None or c[0] != "inst" or not self._alive():
            return
        self.select_instance(c[1])

    def select_instance(self, name):
        """Select exactly the members of an instance in the editor.  Nothing is selected for one that has gone."""
        inst = (self.info or {}).get("instances", {}).get(name)
        if inst is None or not self._editing_ok():
            return False
        try:
            fresh = B.blocks(self.sketch)["instances"].get(name)
        except B.SketchBlockError:
            return False
        if fresh is None:
            return False
        names = member_names(self.sketch, fresh["geo_ids"])
        self._own_change = True
        try:
            Gui.Selection.clearSelection(self.doc_name)
            for n in names:
                Gui.Selection.addSelection(self.doc_name, self.sketch_name, n)
        finally:
            self._own_change = False
        return True

    def on_selection(self):
        if not self._alive() or self._own_change or self.session is not None or self.info is None:
            return
        found = None
        try:
            for s in Gui.Selection.getSelectionEx(self.doc_name):
                if s.ObjectName != self.sketch_name:
                    continue
                for sub in s.SubElementNames:
                    el = RP.element_of_selection_name(self.sketch, sub)
                    if el is not None and el[0] in self.member_of:
                        found = self.member_of[el[0]]
                        break
                if found:
                    break
        except Exception:
            found = None
        self._tree_sync = True
        try:
            if found is None:
                c = self.chosen()
                if c is not None and c[0] == "inst":  # a definition stays chosen: the user may be about to place it
                    self.tree.clearSelection()
                    self.tree.setCurrentItem(None)
            else:
                self._choose_row("inst", found)
        finally:
            self._tree_sync = False
        self._update_buttons()

    # -- the document ---------------------------------------------------------------------------
    def on_property(self, prop):
        """A property of the sketch changed.  Only the data and the number of geometries matter: a drag moves geometry
        and changes neither, so it never refreshes the list."""
        if not self._alive():
            return
        if prop == "BlockData":
            self.data_changed()
        elif prop == "Geometry":
            if self.sketch.GeometryCount != self._geo_count:
                self.schedule()
        elif prop == "Constraints" and self.session is not None:
            self.schedule()

    def data_changed(self):
        if self.session is not None and not self.session["rolling"]:
            self._session_check()
        self.schedule()

    def on_undo_redo(self, step=0):
        """An undo (step -1) or a redo (+1) of the document: the session's depth on the undo stack moves with it."""
        if self._alive():
            if self.session is not None and not self.session["rolling"]:
                self.session["depth"] += step
                self._session_check()
            self.schedule()

    def on_commit(self):
        """A transaction went onto the undo stack.  The session's depth counts them from its own start, by this count and
        never by a name, because the stack drops its oldest entries when it is full and names repeat."""
        if self._alive() and self.session is not None and not self.session["rolling"]:
            self.session["depth"] += 1
            self._update_session_label()

    def _stack_limit(self):
        """The number of undo steps a document keeps (the preference it reads when it is made)."""
        try:
            return int(App.ParamGet("User parameter:BaseApp/Preferences/Document").GetInt("MaxUndoSize", 20))
        except Exception:
            return 20

    def _session_check(self):
        """Something moved under an edit session: end it, with one message, if it can no longer be finished safely."""
        if self.session is None:
            return
        if self.sketch.BlockData != self.session["data"] or self._stack_depth_to_start() is None:
            self._session_broken_deferred()

    def _session_broken_deferred(self):
        if self.session is None or self.session.get("broken"):
            return
        self.session["broken"] = True
        QtCore.QTimer.singleShot(0, self._broken_now)

    def _broken_now(self):
        if self.session is None or not self._alive():
            return
        s = self.session
        if self._stack_depth_to_start() is None:
            self._session_broken(tr("the undo steps of the edit are gone (undone, or more steps than the undo stack keeps)"))
        elif self.sketch.BlockData != s["data"]:
            self._session_broken(tr("the block data changed during the edit"))
        else:
            s["broken"] = False

    def schedule(self):
        if not self._pending:
            self._pending = True
            QtCore.QTimer.singleShot(0, self._deferred)

    def _deferred(self):
        if not self._pending:
            return
        self._pending = False
        if self._alive():
            self.refresh()

    def invalidate(self, reason, close=False):
        if not self._alive():
            return
        self.cancel_place()
        if self.session is not None:
            name = self.session["instance"]
            self.session = None
            self._say(tr("The edit of %s ended: %s The instance stays exploded with the changes made so far.") % (name, reason))
        else:
            self.status.setText(reason)
        if close:
            self._closing = True
            QtCore.QTimer.singleShot(0, self.close)

    # -- the edit session -------------------------------------------------------------------------------------
    # Edit in place.  The chosen instance is exploded in an undo entry named for the session, so its members are plain
    # geometry that every stock tool can change while the siblings stay rigid.  Finish reads the result by persistent id
    # through the placement the instance had at the start, rolls the session's undo entries back, and gives the result to
    # update_definition, so the session is one undo entry and every instance follows.  Cancel rolls back alone.
    # A rollback is a number of undos found by the session entry's name on the undo stack, never a count kept here.

    def _session_entry(self):
        return self.session["entry"]

    def _stack_depth_to_start(self):
        """How many undo steps lie on the stack from the newest down to the session's first entry, counted from the
        session's start (one for its own explosion, one more for each transaction committed since, one less for each
        undo), or None when that cannot be proved: the stack must hold at least that many entries, the one at that depth
        must carry the session's name, and everything below it must be what lay below the session's start (the stack drops
        its oldest entries when it is full, so what lies below may be shorter, never different)."""
        s = self.session
        names = list(self.sketch.Document.UndoNames)  # newest first
        n = s["depth"]
        if n < 1 or n > len(names) or names[n - 1] != s["entry"]:
            return None
        if names[n:] != s["tail"][: len(names) - n]:
            return None
        return n

    def start_session(self):
        c = self.chosen()
        if self.session is not None:
            self.status.setText(tr("An edit is already going on; finish or cancel it first."))
            return False
        if c is None or c[0] != "inst":
            self.status.setText(tr("Choose an instance first."))
            return False
        if not self._editing_ok():
            return False
        name = c[1]
        doc = self.sketch.Document
        try:
            data = B._load(self.sketch)
            if name not in data["inst"]:
                raise B.SketchBlockError(tr("There is no block instance called '%s'.") % name)
            inst = data["inst"][name]
            placement = B.instance_placement(self.sketch, name)
            if getattr(doc, "HasPendingTransaction", False):
                raise B.SketchBlockError(tr("A tool or macro still has an operation open. Let it finish, then start the edit."))
            self.cancel_place()
            snapshot = {p: self.sketch.dumpPropertyContent(p) for p in ("Geometry", "Constraints", "BlockData")}
            start_data = self.sketch.BlockData
            known = {self.sketch.getGeometryId(i) for i in range(len(self.sketch.Geometry))}
            outside = B.session_outside_state(self.sketch, inst["m"].values())  # one pass over the document, here and at Finish and Cancel only
            entry = tr("Edit block %s") % name
            tail = list(doc.UndoNames)
            B.explode(self.sketch, name, label=entry)
            outside["cons"] = B.session_constraint_state(self.sketch)  # the Weight and Equal constraints the explode gave exposed pole circles are the start, not an addition
        except B.SketchBlockError as exc:
            self.refresh()
            self.status.setText(_sentence(str(exc)))
            return False
        except Exception as exc:
            self.refresh()
            self.status.setText(tr("The edit could not start (%s).") % _sentence(str(exc)).rstrip("."))
            return False
        self.session = {
            "instance": name,
            "definition": inst["def"],
            "placement": placement,
            "members": dict(inst["m"]),
            "known": known,
            "snapshot": snapshot,
            "outside": outside,
            "entry": entry,
            "tail": tail,
            "depth": 1,
            "data": self.sketch.BlockData,
            "start_data": start_data,
            "rolling": False,
            "warned": None,
        }
        if self._stack_depth_to_start() is None:
            # the undo history did not take the explosion (a stack limited to nothing): the edit could never be rolled back
            self.session = None
            self._restore(snapshot, "Restore block")
            self.refresh()
            self.status.setText(tr("Edit in place needs the document's undo history to put the sketch back, and it did not keep the first step of the edit, so the edit was not started and the sketch is as it was."))
            return False
        self.status.setText(
            tr("Editing %s of '%s'. Change its geometry with the usual tools, then choose Finish or Cancel edit. Finish keeps only the changes to the block's own members: if anything else in the sketch has moved, or a constraint has been deleted or changed, Finish says so first and undoes it.")
            % (name, inst["def"])
        )
        self._update_session_label()
        self.refresh()
        return True

    def _outside(self, full=False):
        """What differs from the start outside the instance's members (see SketcherBlocks.session_outside_changes).  The
        pass over the whole document (full) is made only when the session starts, at Finish and at Cancel."""
        s = self.session
        return B.session_outside_changes(self.sketch, s["outside"], s["members"].values(), full=full)

    def session_added_constraints(self):
        """How many constraints now in the sketch were not there when the edit began."""
        if self.session is None:
            return 0
        return self._outside()["added_constraints"]

    def _update_session_label(self):
        if self.session is None:
            self.session_label.setText("")
            return
        n = self.session_added_constraints()
        text = tr("Editing %s. Finish keeps the geometry of the block's members as it is now and every instance follows. New geometry becomes a member of every instance. Constraints added during this edit are not kept: %d added so far. Changes to anything else, such as plain geometry that moved or a constraint that was deleted or changed, are undone, and Finish says so first.") % (self.session["instance"], n)
        limit = self._stack_limit()
        if limit and self.session["depth"] >= limit - 2:
            text += " " + tr("This edit has used %d of the %d undo steps the document keeps. Past that, Finish and Cancel can no longer put the sketch back, so finish before then.") % (self.session["depth"], limit)
        self.session_label.setText(text)

    def _undone_text(self, out):
        """The items Finish undoes because they are not part of the block, in words: '1 moved element (Edge3) and 1 deleted constraint ('Ph')'."""
        parts = []

        def names(items):
            items = list(items)
            return ", ".join(items[:4]) + (tr(", and %d more") % (len(items) - 4) if len(items) > 4 else "")

        if out["moved"]:
            n = len(out["moved"])
            parts.append((tr("%d moved element (%s)") if n == 1 else tr("%d moved elements (%s)")) % (n, names(member_names(self.sketch, out["moved"]))))
        if out["deleted"]:
            n = out["deleted"]
            parts.append((tr("%d deleted element") if n == 1 else tr("%d deleted elements")) % n)
        if out["deleted_constraints"]:
            n = len(out["deleted_constraints"])
            parts.append((tr("%d deleted constraint (%s)") if n == 1 else tr("%d deleted constraints (%s)")) % (n, names("'%s'" % x for x in out["deleted_constraints"])))
        if out["changed_constraints"]:
            n = len(out["changed_constraints"])
            parts.append((tr("%d constraint with a changed value (%s)") if n == 1 else tr("%d constraints with a changed value (%s)")) % (n, names("'%s'" % x for x in out["changed_constraints"])))
        if out["construction"]:
            n = len(out["construction"])
            parts.append((tr("%d element with its construction setting changed (%s)") if n == 1 else tr("%d elements with their construction setting changed (%s)")) % (n, names(member_names(self.sketch, out["construction"]))))
        for key, one, many in (
            ("renamed_constraints", tr("%d renamed constraint (%s)"), tr("%d renamed constraints (%s)")),
            ("flag_constraints", tr("%d constraint with a changed active, driving or virtual-space setting (%s)"), tr("%d constraints with a changed active, driving or virtual-space setting (%s)")),
        ):
            if out[key]:
                n = len(out[key])
                parts.append((one if n == 1 else many) % (n, names("'%s'" % x for x in out[key])))
        if out["sketch_props"]:
            n = len(out["sketch_props"])
            parts.append((tr("%d changed sketch property (%s)") if n == 1 else tr("%d changed sketch properties (%s)")) % (n, names(out["sketch_props"])))
        for key, one, many in (
            ("objects_added", tr("%d added object (%s)"), tr("%d added objects (%s)")),
            ("objects_deleted", tr("%d deleted object (%s)"), tr("%d deleted objects (%s)")),
            ("objects_changed", tr("%d other object with a changed property (%s)"), tr("%d other objects with changed properties (%s)")),
        ):
            if out[key]:
                n = len(out[key])
                parts.append((one if n == 1 else many) % (n, names(out[key])))
        if not parts:
            return ""
        return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + tr(" and ") + parts[-1]

    def _signature(self, out):
        """The state a warning was given for: the sketch's geometry and constraints and what is listed in `out`.  A change
        that nets to zero between two presses gives the same signature and does not ask again; any other change does."""
        sk = self.sketch
        return (B.content_hash(sk, "Geometry"), B.content_hash(sk, "Constraints"), repr(sorted(out.items())))

    def _rollback(self):
        """Undo the session's entries, counted from its start, and clear what is left to redo.  Returns False when their
        place on the stack cannot be proved or the sketch is not what it was at the start afterwards."""
        s = self.session
        doc = self.sketch.Document
        n = self._stack_depth_to_start()
        if n is None:
            return False
        s["rolling"] = True
        try:
            for _ in range(n):
                doc.undo()
            # undo leaves the session's steps on the redo stack; a change inside a transaction that is then abandoned
            # empties it, and nothing else
            doc.openTransaction("Clear redo")
            self.sketch.Placement = self.sketch.Placement
            doc.abortTransaction()
        finally:
            s["rolling"] = False
        names = list(doc.UndoNames)
        return names == s["tail"][: len(names)] and self.sketch.BlockData == s["start_data"]

    def _end_session(self, message=None):
        self.session = None
        self.session_label.setText("")
        if message:
            self.status.setText(message)
        self._update_buttons()

    def _session_broken(self, why):
        """The session cannot go on and cannot be rolled back safely: it ends, the sketch stays as it is, one message."""
        if self.session is None:
            return
        name = self.session["instance"]
        self._end_session(None)
        self._say(tr("The edit of %s ended: %s. Nothing was changed by ending it.") % (name, why))
        self.refresh()

    def _say(self, text):
        messages.append(text)
        self._sticky = text
        self.status.setText(text)
        App.Console.PrintMessage("Sketch blocks: %s\n" % text)

    def _session_valid(self):
        """False, with the reason said, when something other than the user's own stock operations changed the session."""
        s = self.session
        if self._stack_depth_to_start() is None:
            self._session_broken(tr("the undo steps of the edit are gone (undone, or more steps than the undo stack keeps)"))
            return False
        if self.sketch.BlockData != s["data"]:
            self._session_broken(tr("the block data changed during the edit"))
            return False
        return True

    def _busy_elsewhere(self):
        """True, with the reason said, while a tool or a macro has a transaction open: an undo would close it as an entry of
        its own and the depth counted from the session's start would be one short."""
        if getattr(self.sketch.Document, "HasPendingTransaction", False):
            self.status.setText(tr("A tool or macro still has an operation open. Let it finish, then choose Finish or Cancel edit."))
            return True
        return False

    def finish_session(self):
        if self.session is None:
            self.status.setText(tr("No edit is going on."))
            return False
        if not self._editing_ok() or self._busy_elsewhere() or not self._session_valid():
            return False
        s = self.session
        sk = self.sketch
        doc = sk.Document
        try:
            res = B.session_changes(sk, s["definition"], s["placement"], s["members"], s["known"])
            out = self._outside(full=True)
        except B.SketchBlockError as exc:
            self.status.setText(_sentence(str(exc)))
            return False
        except Exception as exc:
            self.status.setText(tr("The result cannot be read (%s); the edit goes on.") % _sentence(str(exc)).rstrip("."))
            return False
        undone = self._undone_text(out)
        if undone and s["warned"] != self._signature(out):
            # Finish keeps only the block's change; the rest of the session is rolled back.  Say so, and wait for a second Finish
            # with nothing further changed.
            s["warned"] = self._signature(out)
            self.status.setText(tr("Finish will undo %s that are not part of the block. Press Finish again to accept, or Cancel.") % undone)
            return False
        dropped = out["added_constraints"]
        changes = res["changes"]
        if changes:
            ok, why = self._dry_run(s, changes)
            if not ok:
                self.status.setText(_sentence(why) + " " + tr("The edit goes on; nothing was lost."))
                return False
        final = {p: sk.dumpPropertyContent(p) for p in ("Geometry", "Constraints", "BlockData")}
        if not self._rollback():
            self._restore(final)
            self._session_broken(tr("the undo steps of the edit could not be rolled back"))
            return False
        removed_constraints = []
        if changes:
            try:
                applied = B.update_definition(sk, s["definition"], changes)
            except Exception as exc:  # the dry run said it would work: put the session's result back and say so
                self._restore(final)
                self._session_broken(tr("applying the result failed (%s)") % _sentence(str(exc)).rstrip("."))
                return False
            removed_constraints = applied["removed_constraints"]
            scaled = applied["scaled"]
        else:
            scaled = {}
        self._end_session(None)
        self.refresh()
        if changes:
            text = tr("Finished: %d changed, %d added, %d removed member(s); every instance follows.") % (len(res["moved"]), len(res["added"]), len(res["removed"]))
        elif not (undone or dropped or res["moved_together"]):
            text = tr("Finished: nothing had changed, the sketch is as it was.")
        else:
            text = tr("Finished: the block itself did not change.")
        if removed_constraints:
            text += " " + tr("Constraints removed with their members: %s.") % ", ".join(removed_constraints)
        if scaled:
            text += " " + tr("A B-spline had exactly one weight equal to 1.0, which the solver would rewrite when its pole circles are exposed, so all its weights were multiplied by a power of 2 (%s); the curve is the same and an exposed pole circle shows the scaled radius.") % ", ".join(
                "%s: %d" % (k, f) for k, f in sorted(scaled.items())
            )
        if res["moved_together"]:
            text += " " + tr("The members moved together, which moves the instance rather than changing the block, so the move was not kept.")
        if undone:
            text += " " + tr("Undone because they are not part of the block: %s.") % undone
        if dropped:
            text += " " + tr("%d constraint(s) added during the edit were not kept.") % dropped
        self.status.setText(text)
        return True

    def _dry_run(self, s, changes):
        """Would update_definition accept the result?  Tried on a hidden copy of the sketch as it was at the start."""
        import SketcherDiagnosis as SD

        scratch = SD._Scratch(self.sketch)
        try:
            cp = scratch.copy()
            for prop, content in s["snapshot"].items():
                cp.restorePropertyContent(prop, content)
            B.update_definition(cp, s["definition"], changes)
        except B.SketchBlockError as exc:
            return False, str(exc)
        except Exception as exc:
            return False, tr("the result cannot be applied (%s)") % exc
        finally:
            scratch.close()
        return True, ""

    def _restore(self, content, label=None):
        """Last resort: put the session's result back by hand, in one undo entry."""
        doc = self.sketch.Document
        doc.openTransaction(label or tr("Restore edit"))
        try:
            for prop, c in content.items():
                if self.sketch.dumpPropertyContent(prop) != c:
                    self.sketch.restorePropertyContent(prop, c)
            self.sketch.solve()
        finally:
            doc.commitTransaction()

    def cancel_session(self):
        if self.session is None:
            self.status.setText(tr("No edit is going on."))
            return False
        if not self._editing_ok() or self._busy_elsewhere() or not self._session_valid():
            return False
        s = self.session
        try:
            out = self._outside(full=True)
        except Exception as exc:
            self.status.setText(tr("The result cannot be read (%s); the edit goes on.") % _sentence(str(exc)).rstrip("."))
            return False
        undone = self._undone_text(out)
        if undone and s["warned"] != self._signature(out):
            # Cancel rolls the whole document back by undo, so what was changed outside the block goes too: say so first
            s["warned"] = self._signature(out)
            self.status.setText(tr("Cancel will undo %s that are not part of the block, as well as the edit. Press Cancel again to go on.") % undone)
            return False
        final = {p: self.sketch.dumpPropertyContent(p) for p in ("Geometry", "Constraints", "BlockData")}
        if not self._rollback():
            self._restore(final)
            self._session_broken(tr("the undo steps of the edit could not be rolled back"))
            return False
        text = tr("The edit was cancelled; the sketch is as it was before it.")
        if undone:
            text += " " + tr("Undone because they are not part of the block: %s.") % undone
        self._end_session(None)
        self.refresh()
        self.status.setText(text)
        return True

    # -- keys -------------------------------------------------------------------------------------
    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape and self._placing is not None:
            self.cancel_place()
            event.accept()
            return
        super().keyPressEvent(event)

    # -- closing -------------------------------------------------------------------------------------
    def closeEvent(self, event):
        if self.session is not None and self.state != "closed":
            name = self.session["instance"]
            self.session = None
            messages.append(tr("The edit of %s ended because the panel was closed. The instance stays exploded with the changes made so far.") % name)
            App.Console.PrintMessage("Sketch blocks: %s\n" % messages[-1])
        self.state = "closed"
        self.end_place(None)
        for fn, name in ((App.removeDocumentObserver, "_dw"), (Gui.removeDocumentObserver, "_gw"), (Gui.Selection.removeObserver, "_sw")):
            try:
                fn(getattr(self, name))
            except Exception:
                pass
        global _panel
        if _panel is self:
            _panel = None
        super().closeEvent(event)
        self.deleteLater()


def _sentence(text):
    text = (text or "").strip()
    if not text:
        return text
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


def current_panel():
    return _panel


def open_panel(sketch=None):
    """Open the panel for the sketch in the editor (or `sketch`).  A panel that is open is reused."""
    global _panel
    if sketch is None:
        sketch = DP.editing_sketch()
    if sketch is None:
        return None
    if _panel is not None and _panel.state != "closed":
        if _panel.sketch_name == sketch.Name and _panel.doc_name == sketch.Document.Name:
            _panel.show()
            _panel.raise_()
            return _panel
        _panel.close()
    mw = Gui.getMainWindow()
    panel = BlocksPanel(sketch, mw)
    mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, panel)
    panel.show()
    _panel = panel
    return panel
