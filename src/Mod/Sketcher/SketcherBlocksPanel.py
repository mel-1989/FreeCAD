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

Rows are keyed by name, never by position.  The panel reads the sketch again, deferred by one event-loop turn, after a
change to ``BlockData`` or to the number of geometries, and after undo and redo; a drag moves geometry without
changing either, so it costs the panel nothing.  An action on a row that has gone is refused by the API with a
sentence and changes nothing.

Behaviour follows public descriptions of sketch blocks (a definition placed many times, edited once, exploded back to
plain geometry); the layout is the plain list-and-buttons form of FreeCAD's task panels, and no layout, icon, text or
file format comes from another product."""

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

_ROLE = QtCore.Qt.UserRole
_panel = None
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
    import math

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


def point_in_sketch(sketch, view, gl, pos):
    """The sketch-frame point under a Qt position of the viewport: the pixel's ray meets the sketch plane taken
    through the sketch's global placement (inside a Body that includes the Body's own placement)."""
    w, h = view.getSize()
    dpr = gl.devicePixelRatioF()
    x = int(round(pos.x() * dpr))
    y = int(round(h - 1 - pos.y() * dpr))
    p0, p1 = view.projectPointToLine(x, y)
    gp = sketch.getGlobalPlacement()
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
            self.panel.on_undo_redo()

    def slotRedoDocument(self, doc):
        if doc.Name == self.panel.doc_name:
            self.panel.on_undo_redo()

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
        self._placing = None  # the definition being placed by a click
        self._pressed = False
        self._pointer_busy = False
        self._viewport = None
        self._viewer = None
        self._view_filter = None
        self.session = None  # the edit session, see the end of the class
        self._build()
        self._dw = _DocWatch(self)
        self._gw = DP._GuiWatch(self)
        self._sw = _SelWatch(self)
        App.addDocumentObserver(self._dw)
        Gui.addDocumentObserver(self._gw)
        Gui.Selection.addObserver(self._sw)
        self.refresh()

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
        b.clicked.connect(lambda *_: slot())
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
        if self.session is None and not self._placing:
            nd, ni = len(info["definitions"]), len(info["instances"])
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
        ok = self._alive() and self.broken is None
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
        self.finish_button.setVisible(busy)
        self.cancel_button.setVisible(busy)
        self.session_label.setVisible(busy)
        self.finish_button.setEnabled(busy)
        self.cancel_button.setEnabled(busy)

    # -- running an action -----------------------------------------------------------
    def _editing_ok(self):
        if not self._alive():
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
                    raise B.SketchBlockError(tr("%s cannot be part of a block; select points, lines, circles or arcs.") % sub)
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
            return B.define(self.sketch, name, ids), len(ids)

        res = self._run(go)
        if res and res is not True:
            self._choose_row("def", res[0])
            self._update_buttons()
            self.status.setText(tr("Defined '%s' from %d member(s). The geometry is still plain geometry; Insert places copies.") % res)
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

    # -- selection both ways --------------------------------------------------------------
    def _on_choice(self):
        if self._tree_sync:
            return
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
        self.schedule()

    def on_undo_redo(self):
        if self._alive():
            self.schedule()

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
        self.status.setText(reason)
        if close:
            self._closing = True
            QtCore.QTimer.singleShot(0, self.close)

    # -- the edit session (phase 2) --------------------------------------------------------------
    def start_session(self):
        self.status.setText(tr("Edit in place is not available yet."))

    def finish_session(self):
        pass

    def cancel_session(self):
        pass

    # -- keys -------------------------------------------------------------------------------------
    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape and self._placing is not None:
            self.cancel_place()
            event.accept()
            return
        super().keyPressEvent(event)

    # -- closing -------------------------------------------------------------------------------------
    def closeEvent(self, event):
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
