# SPDX-License-Identifier: LGPL-2.1-or-later

"""Surface workbench command ``Surface_EditPoles``: edit the control points of a B-spline face.

The task panel draws the control net of the selected face in the 3D view (one node named
``G09_POLES``: a marker per pole, the net lines, the chosen pole highlighted, locked poles drawn in
another colour) and hides the source. A pole is chosen with the U and V index spin boxes or by clicking
its marker (the nearest within 8 px). Three length fields set the chosen pole's offset in global axes
(absolute, not incremental); "Reset pole" clears it. Each side (U0, U1, V0, V1) has a lock choice
(Free, G0, G1, G2) and an analysis choice (None, Mean curvature, Gaussian curvature, Zebra,
Isophotes) is drawn with the functions of ``SurfaceAnalysisGui`` on the edited face. Every change
redraws the preview face, the net and the chosen analysis; nothing recomputes the document.

A left press within 8 px of a marker is claimed by the panel under every navigation style (a Qt event filter
on the 3D view's typed ``QOpenGLWidget``, which runs before any navigation style): it chooses that pole and
starts a drag, in the view plane or along a global axis (the "Drag in" choice), with the ray taken from the
view's own camera. Release ends the drag; Escape or a right press during it puts the pole back. A document
observer follows the source while the panel is open (same pole grid: reload and keep the moves; another
grid: ``PoleGridChanged``; deleted source or closed document: the panel closes).

OK on a selected face creates one ``PoleEdit`` feature, on a re-edit it updates the feature's
properties, in one undo transaction named "Edit control points". OK with a refused state (the status
line shows its name) leaves the panel open and changes nothing. Cancel changes nothing, opens no
transaction, restores the source's visibility and leaves no ``G09_`` node in the view.

Sources (clean room): FreeCAD's own Python task panels and commands, the Qt documentation, the public
Coin3D (Open Inventor) scene-graph API, and L. Piegl and W. Tiller, *The NURBS Book*, 2nd ed., 1997.
"""

import contextlib
import math

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import SurfaceAnalysisGui as SAG
import SurfacePoleEdit as SPE
from SurfaceAnalysisCommands import _Relay, _quantity_spin_box, selected_subshapes

__all__ = ["PoleEditPanel", "ViewProviderPoleEdit", "PICK_RADIUS", "active_panel"]

_ACTIVE = []  # the open panel, for tests and scripts

TRANSACTION = "Edit control points"
PICK_RADIUS = 8.0
_ANALYSES = (
    ("None", "none"),
    ("Mean curvature", "mean"),
    ("Gaussian curvature", "gauss"),
    ("Zebra", "zebra"),
    ("Isophotes", "isophotes"),
)
_LOCKS = (("Free", None), ("G0", 0), ("G1", 1), ("G2", 2))
_NODE_NAMES = ("G09_POLES", "G09_PREVIEW")


_DRAG_MODES = (("View plane", "view"), ("X", "X"), ("Y", "Y"), ("Z", "Z"))
_AXES = {"X": App.Vector(1, 0, 0), "Y": App.Vector(0, 1, 0), "Z": App.Vector(0, 0, 1)}


class _DragFilter(QtCore.QObject):
    """Qt event filter. On the view's ``QOpenGLWidget`` it claims marker presses, drag moves and the
    release; at application level (installed only while a drag runs) it keeps Escape from reaching the
    task dialog, whose Escape is Cancel."""

    def __init__(self, panel, app_level=False):
        super().__init__()
        self._panel = panel
        self._app_level = app_level

    def eventFilter(self, obj, event):
        try:
            if self._app_level:
                return self._panel._app_event(event)
            return self._panel._view_event(event)
        except Exception as e:  # a failing handler must never break the view's own event handling
            App.Console.PrintWarning("Surface_EditPoles: %s\n" % e)
            return False


class _SourceObserver:
    """Document observer: reports changes of the objects the panel's source depends on."""

    def __init__(self, panel):
        self._panel = panel

    def slotChangedObject(self, obj, prop):
        self._panel._source_changed(obj, prop)

    def slotDeletedObject(self, obj):
        self._panel._object_deleted(obj)

    def slotDeletedDocument(self, doc):
        self._panel._document_deleted(doc)

    def slotUndoDocument(self, doc):
        self._panel._source_changed(None, "Undo")

    def slotRedoDocument(self, doc):
        self._panel._source_changed(None, "Redo")


def active_panel():
    """The open pole edit panel, or ``None``."""
    return _ACTIVE[-1] if _ACTIVE else None


def _tr(text):
    return QtCore.QCoreApplication.translate("Surface_EditPoles", text)


class ViewProviderPoleEdit:
    """View provider proxy of the pole edit feature; a double click opens the panel on its edit."""

    def __init__(self, vobj):
        vobj.Proxy = self
        self.Object = vobj.Object

    def attach(self, vobj):
        self.Object = vobj.Object

    def getIcon(self):
        return ":/icons/Surface_EditPoles.svg"

    def updateData(self, obj, prop):
        return

    def onChanged(self, vobj, prop):
        return

    def _open(self):
        if Gui.Control.activeDialog():
            return False
        Gui.Control.showDialog(PoleEditPanel(feature=self.Object))
        return True

    def doubleClicked(self, vobj):
        return self._open()

    def setEdit(self, vobj, mode=0):
        if mode != 0:
            return None
        return self._open()

    def unsetEdit(self, vobj, mode=0):
        return True

    def dumps(self):
        return None

    def loads(self, state):
        return None


def selected_face_source():
    """``(object, [subname])`` of the one selected face (or of one selected object that is one face);
    ``None`` for any other selection."""
    items = selected_subshapes(("Face", "Edge", "Vertex", "object"))
    if len(items) != 1:
        return None
    item = items[0]
    if item["type"] == "Face":
        return item["object"], [item["subname"]]
    if item["type"] == "object":
        shape = SAG.placed_shape(item["object"])
        if isinstance(shape, Part.Shape) and len(shape.Faces) == 1:
            return item["object"], [""]
    return None


def _leaf_object(source):
    """The document object that owns the face a source link names (the leaf of a nested path)."""
    obj, subs = source
    sub = subs[0] if subs else ""
    owner = sub.rpartition(".")[0]
    if owner:
        try:
            leaf = obj.getSubObject(owner + ".", 1)
            if leaf is not None:
                return leaf
        except Exception:
            pass
    return obj


class PoleEditPanel:
    """Task panel editing the poles of one face numerically or by clicking a marker."""

    def __init__(self, document=None, feature=None, source=None, view=None):
        self.document = document
        self.feature = feature
        self.view = view
        self.created = None
        self.error = ""
        self.face = None
        self.surface = None  # the editable surface of the source (global frame)
        self.edited = None  # the last good edited surface
        self.handle = None  # the analysis overlay
        self.moves = {}  # (i, j) -> global offset
        self.locks = {}  # side -> k
        self.chosen = (1, 1)
        self._busy = True
        self._cb = None
        self._cb_view = None
        self._hidden = []  # (object, visibility before the panel opened)
        self._torn_down = False
        self._drag = None  # state of a drag: dict, or None
        self._swallow = None  # a claimed press that started no drag: swallow its moves and release
        self._gl = None
        self._view_filter = None
        self._app_filter = None
        self._observer = None
        self._reload_pending = False
        self._busy_accept = False
        self._regridded = False  # the source's pole grid changed while the panel was open
        self._grid_ref = None
        self.source = None
        self.grid_changed = False
        if feature is not None:
            self.source = (feature.Source[0], list(feature.Source[1]))
            if self.document is None:
                self.document = feature.Document
        elif source is not None:
            self.source = SPE.SG._split(source)
        else:
            self.source = selected_face_source()
        self._relays = []
        self._build_form()
        self._load_source()
        self._busy = False
        self._sync_pole_fields()
        self._hide_inputs()
        self._connect_view()
        self._connect_observer()
        _ACTIVE.append(self)
        self.refresh()

    # ---- widgets
    def _combo(self, name, items):
        box = QtWidgets.QComboBox(self.form)
        box.setObjectName(name)
        for label, data in items:
            box.addItem(_tr(label), data)
        return box

    def _row(self, layout, text, widget):
        label = QtWidgets.QLabel(_tr(text), self.form)
        label.setObjectName(widget.objectName() + "_Label")
        label.setBuddy(widget)
        layout.addRow(label, widget)

    def _build_form(self):
        form = QtWidgets.QWidget()
        form.setObjectName("G09_PoleEditPanel")
        form.setWindowTitle(_tr("Edit control points"))
        self.form = form
        layout = QtWidgets.QVBoxLayout(form)
        pole = QtWidgets.QFormLayout()
        layout.addLayout(pole)
        self.u_spin = QtWidgets.QSpinBox(form)
        self.u_spin.setObjectName("G09_PoleEdit_U")
        self.u_spin.setRange(1, 1)
        self._row(pole, "U index:", self.u_spin)
        self.v_spin = QtWidgets.QSpinBox(form)
        self.v_spin.setObjectName("G09_PoleEdit_V")
        self.v_spin.setRange(1, 1)
        self._row(pole, "V index:", self.v_spin)
        self.offset = {}
        for axis in "XYZ":
            spin = _quantity_spin_box()
            spin.setParent(form)
            spin.setObjectName("G09_PoleEdit_Offset%s" % axis)
            spin.setProperty("unit", "mm")
            spin.setProperty("minimum", -1.0e9)
            spin.setProperty("maximum", 1.0e9)
            spin.setProperty("rawValue", 0.0)
            spin.setToolTip(_tr("Offset of the chosen pole along global %s, from its source position") % axis)
            self._row(pole, "Offset %s:" % axis, spin)
            self.offset[axis] = spin
        self.reset_button = QtWidgets.QPushButton(_tr("Reset pole"), form)
        self.reset_button.setObjectName("G09_PoleEdit_Reset")
        layout.addWidget(self.reset_button)
        lock = QtWidgets.QFormLayout()
        layout.addLayout(lock)
        self.lock_boxes = {}
        for side in SPE.SIDES:
            box = self._combo("G09_PoleEdit_Lock%s" % side, _LOCKS)
            self._row(lock, "Lock %s:" % side, box)
            self.lock_boxes[side] = box
        self.analysis_box = self._combo("G09_PoleEdit_Analysis", _ANALYSES)
        self._row(lock, "Analysis:", self.analysis_box)
        self.drag_box = self._combo("G09_PoleEdit_DragMode", _DRAG_MODES)
        self.drag_box.setToolTip(_tr("Drag a pole marker in the view plane or along a global axis"))
        self._row(lock, "Drag in:", self.drag_box)
        self.status = QtWidgets.QLabel(form)
        self.status.setObjectName("G09_PoleEditStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()

        self._r_pole = _Relay(self._pole_changed, form)
        self._r_offset = _Relay(self._offset_changed, form)
        self._r_lock = _Relay(self._lock_changed, form)
        self._r_analysis = _Relay(self._analysis_changed, form)
        self._r_reset = _Relay(self._reset_clicked, form)
        self._r_done = _Relay(self._release_wrappers, form)
        QtCore.QObject.connect(form, QtCore.SIGNAL("destroyed()"), self._r_done, QtCore.SLOT("finished()"))
        self.u_spin.valueChanged.connect(self._r_pole.changed)
        self.v_spin.valueChanged.connect(self._r_pole.changed)
        for spin in self.offset.values():
            QtCore.QObject.connect(spin, QtCore.SIGNAL("valueChanged(double)"), self._r_offset, QtCore.SLOT("changed()"))
        for box in self.lock_boxes.values():
            box.currentIndexChanged.connect(self._r_lock.changed)
        self.analysis_box.currentIndexChanged.connect(self._r_analysis.changed)
        self.reset_button.clicked.connect(self._r_reset.changed)

    def _release_wrappers(self):
        # the offset spin boxes are made by the UI loader, so Shiboken does not see them die with the form:
        # drop their wrappers at once, or a later widget made at the same address comes back as a stale one
        # (a spin box of the next panel found as a QAbstractSpinBox)
        import shiboken6

        for spin in list(self.offset.values()):
            try:
                shiboken6.invalidate(spin)
            except Exception:
                pass
        self.offset = {}

    def say(self, text):
        self.status.setText(text)

    # ---- source
    def _load_source(self):
        if self.source is None:
            self.error = _tr("InvalidInput: select exactly one face")
            return
        try:
            self.face = SPE.resolve_face(self.source)
            self.surface = SPE.editable_surface(self.face)
        except Exception as e:
            self.error = str(e) or e.__class__.__name__
            return
        nu, nv = self.surface.NbUPoles, self.surface.NbVPoles
        self._grid_ref = SPE.grid_signature(self.surface)
        self.u_spin.setRange(1, nu)
        self.v_spin.setRange(1, nv)
        if self.document is None:
            self.document = self.source[0].Document
        if self.feature is not None:
            rot = self.face.Placement.Rotation
            stored = self.feature.GridSignature
            self.grid_changed = bool(stored) and stored != SPE.grid_signature(self.surface)
            if not self.grid_changed:  # never apply stored indices to another grid
                for index, vec in SPE.stored_moves(self.feature):
                    self.moves[index] = rot.multVec(vec)
            self.locks = {s: k for s, k in SPE.stored_locks(self.feature).items() if k is not None}
            for side, box in self.lock_boxes.items():
                box.setCurrentIndex(_index_of(box, self.locks.get(side)))

    @property
    def rotation(self):
        return self.face.Placement.Rotation

    # ---- visibility
    def _hide_inputs(self):
        if self.feature is not None:
            # a double click on the tree leaves an active (still empty) "Edit" transaction, which the first
            # change of a document object, here its Visibility, would turn into an undo step of its own
            # and make the later OK a second one: it is closed first
            try:
                active = App.getActiveTransaction()
                if active and active[0] == "Edit":
                    App.closeActiveTransaction()
            except Exception:
                pass
            self._hide(self.feature, view_only=True)
        elif self.source is not None:
            self._hide(_leaf_object(self.source))

    def _hide(self, obj, view_only=False):
        try:
            if obj.Visibility:
                self._hidden.append((obj, True))
                if view_only and obj.ViewObject is not None:
                    obj.ViewObject.Visibility = False
                else:
                    obj.Visibility = False
        except Exception:
            pass

    def _restore_inputs(self, only=None):
        keep = []
        for obj, vis in self._hidden:
            if only is not None and obj not in only:
                keep.append((obj, vis))
                continue
            try:
                if obj is self.feature and obj.ViewObject is not None:
                    obj.ViewObject.Visibility = vis
                else:
                    obj.Visibility = vis
            except Exception:
                pass
        self._hidden = keep

    # ---- the 3D view
    def _view(self):
        try:
            return SAG._active_view(self.view)
        except Exception:
            return None

    def _ufs(self):
        v = self._cb_view or self._view()
        return None if v is None else SAG._find_selection_node(v)

    def _gl_widget(self):
        """The 3D view's ``QOpenGLWidget``, found as that type: a wrapper made any other way (a bare
        ``QWidget``) crashes the process at exit while Shiboken tears it down."""
        from PySide6 import QtOpenGLWidgets

        mw = Gui.getMainWindow()
        found = [
            g
            for g in mw.findChildren(QtOpenGLWidgets.QOpenGLWidget)
            if g.isVisible() and g.parentWidget() is not None and g.parentWidget().metaObject().className() == "Gui::View3DInventorViewer"
        ]
        if len(found) > 1:  # several views: the one inside the active sub window
            try:
                from PySide6 import QtWidgets as QW6

                area = mw.findChild(QW6.QMdiArea)
                sub = area.activeSubWindow() if area is not None else None
                if sub is not None:
                    mine = [g for g in found if sub.isAncestorOf(g)]
                    found = mine or found
            except Exception:
                pass
        return found[0] if found else None

    def _connect_view(self):
        v = self._view()
        if v is None or self.surface is None:
            return
        self._cb_view = v
        self._gl = self._gl_widget()
        if self._gl is not None:
            self._view_filter = _DragFilter(self)
            self._gl.installEventFilter(self._view_filter)

    def _disconnect_view(self):
        if self._gl is not None and self._view_filter is not None:
            try:
                self._gl.removeEventFilter(self._view_filter)
            except Exception:
                pass
        self._gl = self._view_filter = None
        self._set_app_filter(False)
        self._cb = self._cb_view = None

    def _set_app_filter(self, on):
        app = QtWidgets.QApplication.instance()
        if on and self._app_filter is None:
            self._app_filter = _DragFilter(self, app_level=True)
            app.installEventFilter(self._app_filter)
        elif not on and self._app_filter is not None:
            with contextlib.suppress(Exception):
                app.removeEventFilter(self._app_filter)
            self._app_filter = None

    def _remove_nodes(self):
        SAG.remove(self.handle)
        self.handle = None
        try:
            ufs = self._ufs()
        except Exception:
            ufs = None
        if ufs is None:
            return
        for i in reversed(range(ufs.getNumChildren())):
            if ufs.getChild(i).getName().getString() in _NODE_NAMES:
                ufs.removeChild(i)

    def _pole_grid(self, surface):
        return surface.getPoles()

    def _preview_node(self, verts, tris):
        coin = SAG._coin()
        sep = coin.SoSeparator()
        sep.setName("G09_PREVIEW")
        ps = coin.SoPickStyle()
        ps.style = coin.SoPickStyle.UNPICKABLE
        sep.addChild(ps)
        mat = coin.SoMaterial()
        mat.diffuseColor = (0.78, 0.82, 0.90)
        sep.addChild(mat)
        hints = coin.SoShapeHints()
        hints.vertexOrdering = coin.SoShapeHints.UNKNOWN_ORDERING
        hints.shapeType = coin.SoShapeHints.UNKNOWN_SHAPE_TYPE
        sep.addChild(hints)
        cd = coin.SoCoordinate3()
        cd.point.setValues(0, len(verts), [(p.x, p.y, p.z) for p in verts])
        sep.addChild(cd)
        fs = coin.SoIndexedFaceSet()
        idx = []
        for a, b, c in tris:
            idx.extend((a, b, c, -1))
        fs.coordIndex.setValues(0, len(idx), idx)
        sep.addChild(fs)
        return sep

    def _points_node(self, name, points, color, size):
        coin = SAG._coin()
        sep = coin.SoSeparator()
        sep.setName(name)
        mat = coin.SoMaterial()
        mat.diffuseColor = color
        sep.addChild(mat)
        style = coin.SoDrawStyle()
        style.pointSize = size
        sep.addChild(style)
        cd = coin.SoCoordinate3()
        cd.point.setNum(len(points))
        if points:
            cd.point.setValues(0, len(points), [(p.x, p.y, p.z) for p in points])
        sep.addChild(cd)
        ps = coin.SoPointSet()
        ps.numPoints = len(points)
        sep.addChild(ps)
        return sep

    def _net_node(self, surface):
        """``G09_POLES``: the net lines, a marker per pole (free, locked, chosen)."""
        coin = SAG._coin()
        poles = surface.getPoles()
        nu, nv = len(poles), len(poles[0])
        sep = coin.SoSeparator()
        sep.setName("G09_POLES")
        ps = coin.SoPickStyle()
        ps.style = coin.SoPickStyle.UNPICKABLE
        sep.addChild(ps)
        lm = coin.SoLightModel()
        lm.model = coin.SoLightModel.BASE_COLOR
        sep.addChild(lm)
        try:
            depth = coin.SoDepthBuffer()
            depth.function = coin.SoDepthBuffer.ALWAYS
            sep.addChild(depth)
        except Exception:
            pass
        locked = SPE.locked_poles(surface, self.locks)
        net = coin.SoSeparator()
        net.setName("G09_POLES_NET")
        mat = coin.SoMaterial()
        mat.diffuseColor = (0.45, 0.45, 0.45)
        net.addChild(mat)
        cd = coin.SoCoordinate3()
        flat = [poles[i][j] for i in range(nu) for j in range(nv)]
        cd.point.setValues(0, len(flat), [(p.x, p.y, p.z) for p in flat])
        net.addChild(cd)
        ls = coin.SoIndexedLineSet()
        idx = []
        for i in range(nu):
            idx.extend([i * nv + j for j in range(nv)] + [-1])
        for j in range(nv):
            idx.extend([i * nv + j for i in range(nu)] + [-1])
        ls.coordIndex.setValues(0, len(idx), idx)
        net.addChild(ls)
        sep.addChild(net)
        free = [poles[i - 1][j - 1] for i in range(1, nu + 1) for j in range(1, nv + 1) if (i, j) not in locked and (i, j) != self.chosen]
        lock = [poles[i - 1][j - 1] for i in range(1, nu + 1) for j in range(1, nv + 1) if (i, j) in locked and (i, j) != self.chosen]
        sep.addChild(self._points_node("G09_POLES_FREE", free, (0.1, 0.35, 0.9), 6))
        sep.addChild(self._points_node("G09_POLES_LOCKED", lock, (0.85, 0.15, 0.15), 8))
        ci, cj = self.chosen
        chosen = [poles[ci - 1][cj - 1]] if 1 <= ci <= nu and 1 <= cj <= nv else []
        sep.addChild(self._points_node("G09_POLES_CHOSEN", chosen, (1.0, 0.8, 0.0), 12))
        return sep

    def _analysis_overlay(self, face, view, tess):
        kind = self.analysis_box.currentData()
        if kind == "none":
            return None
        # the overlay reuses the tessellation of the preview (one tessellate call per redraw, not two)
        original = SAG._tessellate
        SAG._tessellate = lambda shape, deflection: [(1, tess[0], tess[1], tess[2])]
        try:
            if kind in ("mean", "gauss"):
                return SAG.show_curvature(face, kind=kind, view=view)
            if kind == "zebra":
                return SAG.show_zebra(face, view=view)
            return SAG.show_isophotes(face, view=view)
        finally:
            SAG._tessellate = original

    def _redraw(self):
        """Replace the preview face, the net and the analysis overlay by ones of the current state."""
        self._remove_nodes()
        view = self._view()
        if view is None or self.edited is None:
            return
        ufs = SAG._find_selection_node(view)
        face = SPE._face_of(self.edited, self.face)
        copy = face.copy()  # the triangulation stays off the shared shape
        verts, tris = copy.tessellate(SAG._effective_deflection(copy, None))
        ufs.addChild(self._preview_node(verts, tris))
        ufs.addChild(self._net_node(self.edited))
        self.handle = self._analysis_overlay(face, view, (copy, verts, tris))

    # ---- state
    def _locks_dict(self):
        return {s: k for s, k in self.locks.items() if k is not None}

    def refresh(self):
        """Evaluate the pending moves and locks; on success redraw, else show the refusal and keep the
        last good preview."""
        if self.surface is None:
            self.say(self.error)
            return False
        try:
            edited, moved = SPE._apply(self.surface, list(self.moves.items()), self._locks_dict())
        except ValueError as e:
            self.error = str(e)
            self.say(self.error)
            return False
        self.error = ""
        self.edited = edited
        self._moved = moved
        self._deviation = SPE._max_deviation(self.surface, edited)
        text = _tr("%d pole(s) moved, maximum deviation %.6g mm") % (moved, self._deviation)
        if self.grid_changed:
            text = _tr("PoleGridChanged: the source's pole grid is no longer the one this edit was made on; the stored moves are not loaded and OK replaces the stored edit. ") + text
        elif self._regridded:
            text = _tr("PoleGridChanged: the source's pole grid changed while the panel was open; the moves made on the old grid were dropped and OK records the moves made on the new grid. ") + text
        self.say(text)
        self._redraw()
        return True

    def choose(self, i, j):
        """Choose pole ``(i, j)`` (1-based) as the one the offset fields edit."""
        self.u_spin.setValue(i)
        self.v_spin.setValue(j)
        self._pole_changed()

    def set_offset(self, vector):
        """Set the chosen pole's absolute offset (global axes)."""
        self._busy = True
        for axis, value in zip("XYZ", (vector.x, vector.y, vector.z)):
            self.offset[axis].setProperty("rawValue", float(value))
        self._busy = False
        self._offset_changed()

    def _sync_pole_fields(self):
        v = self.moves.get(self.chosen, App.Vector())
        self._busy = True
        for axis, value in zip("XYZ", (v.x, v.y, v.z)):
            if axis in self.offset:
                self.offset[axis].setProperty("rawValue", float(value))
        self._busy = False

    def _pole_changed(self):
        if self._busy:
            return
        self.chosen = (self.u_spin.value(), self.v_spin.value())
        self._sync_pole_fields()
        if self.edited is not None:
            self._redraw()

    def _offset_changed(self):
        if self._busy or self.surface is None:
            return
        vec = App.Vector(*(float(self.offset[a].property("rawValue")) for a in "XYZ"))
        if vec.Length > 0.0:
            self.moves[self.chosen] = vec
        else:
            self.moves.pop(self.chosen, None)
        self.refresh()

    def _reset_clicked(self):
        if self.surface is None:
            return
        self._busy = True
        self.moves.pop(self.chosen, None)
        self._busy = False
        self._sync_pole_fields()
        self.refresh()

    def _lock_changed(self):
        if self._busy:
            return
        self.locks = {s: b.currentData() for s, b in self.lock_boxes.items()}
        if self.surface is not None:
            self.refresh()

    def _analysis_changed(self):
        if self._busy or self.edited is None:
            return
        self._redraw()

    # ---- clicking a marker
    def nearest_pole(self, x, y, view=None):
        """``((i, j), distance in px)`` of the pole whose projection is nearest to the pixel
        ``(x, y)`` (origin bottom left); ``None`` when there are no poles."""
        view = view or self._cb_view or self._view()
        poles = self.edited.getPoles() if self.edited is not None else None
        if view is None or not poles:
            return None
        best = None
        for i, row in enumerate(poles):
            for j, p in enumerate(row):
                px, py = view.getPointOnScreen(p.x, p.y, p.z)
                d = ((px - x) ** 2 + (py - y) ** 2) ** 0.5
                if best is None or d < best[1]:
                    best = ((i + 1, j + 1), d)
        return best

    # ---- the dragger (Qt event filter on the view's QOpenGLWidget)
    @staticmethod
    def _mods(event):
        m = QtCore.Qt
        return bool(event.modifiers() & (m.ShiftModifier | m.ControlModifier | m.AltModifier | m.MetaModifier))

    def _pixel(self, event):
        """``(coin_x, coin_y, dpr)`` of a mouse event: device pixels, origin bottom left."""
        gl = self._gl
        dpr = float(gl.devicePixelRatioF())
        pos = event.position()
        return pos.x() * dpr, (gl.height() - pos.y()) * dpr, dpr

    def _marker_under(self, event):
        """The pole whose marker lies within ``PICK_RADIUS`` px of the event, else ``None``."""
        if self.edited is None:
            return None
        x, y, dpr = self._pixel(event)
        hit = self.nearest_pole(x, y, self._cb_view)
        if hit is not None and hit[1] / dpr <= PICK_RADIUS:
            return hit[0]
        return None

    def _view_event(self, event):
        et = event.type()
        E = QtCore.QEvent
        if et in (E.MouseButtonPress, E.MouseButtonDblClick):
            button = event.button()
            if self._drag is not None:
                if button == QtCore.Qt.RightButton:  # a right press during a drag: back to the press
                    self.cancel_drag()
                    self._swallow = "right"
                    return True
                return True  # no other press reaches the navigation style mid-drag
            if button != QtCore.Qt.LeftButton or self._mods(event):
                return False
            hit = self._marker_under(event)
            if hit is None:
                return False
            self._press(hit, event)
            return True
        if et == E.MouseMove:
            if self._drag is not None:
                self._move(event)
                return True
            return self._swallow is not None
        if et == E.MouseButtonRelease:
            button = event.button()
            if self._drag is not None and button == QtCore.Qt.LeftButton:
                self.end_drag()
                return True
            if self._swallow is not None and button == (QtCore.Qt.RightButton if self._swallow == "right" else QtCore.Qt.LeftButton):
                self._swallow = None
                return True
            return self._drag is not None
        return False

    def _app_event(self, event):
        et = event.type()
        if self._drag is None:
            return False
        if et in (QtCore.QEvent.ShortcutOverride, QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease):
            if event.key() == QtCore.Qt.Key_Escape:
                if et == QtCore.QEvent.KeyPress:
                    self.cancel_drag()
                    self._swallow = "left"
                event.accept()
                return et != QtCore.QEvent.ShortcutOverride
        return False

    def _press(self, index, event):
        self.choose(*index)
        if index in SPE.locked_poles(self.surface, self._locks_dict()):
            self._swallow = "left"
            self.say(_tr("LockedPole: pole (%d, %d) is locked by the side locks and cannot be dragged") % index)
            return
        self._swallow = None
        base = self.edited.getPole(*index)
        self._drag = {
            "index": index,
            "start": self.moves.get(index, App.Vector()),
            "pole": App.Vector(base),
            "mode": self.drag_box.currentData(),
        }
        self._set_app_filter(True)

    def _ray(self, event):
        """The view ray ``(origin, unit direction)`` through the event's position, from the view's own
        camera (position, orientation and view volume) and the viewport size."""
        cam = self._cb_view.getCameraNode()
        pos = App.Vector(*cam.position.getValue().getValue())
        rot = App.Rotation(*cam.orientation.getValue().getValue())
        fwd, right, up = rot.multVec(App.Vector(0, 0, -1)), rot.multVec(App.Vector(1, 0, 0)), rot.multVec(App.Vector(0, 1, 0))
        w, h = float(self._gl.width()), float(self._gl.height())
        dpr = float(self._gl.devicePixelRatioF())
        p = event.position()
        # the Coin pixel the view's own events carry (Quarter's InputDevice::toDevicePixelPosition:
        # rounded, y flipped about h - 1), normalised by the viewport size in device pixels
        px = math.floor(p.x() * dpr + 0.5)
        py = math.floor((h - p.y() - 1.0) * dpr + 0.5)
        wd, hd = w * dpr, h * dpr
        nx, ny = 2.0 * px / wd - 1.0, 2.0 * py / hd - 1.0
        aspect = w / h
        if not hasattr(cam, "heightAngle"):  # an orthographic camera has a height, not an angle
            half_h = 0.5 * float(cam.height.getValue())
            half_w = half_h * aspect
            origin = pos + right * (nx * half_w) + up * (ny * half_h)
            return origin, fwd
        half_h = math.tan(0.5 * float(cam.heightAngle.getValue()))
        half_w = half_h * aspect
        if aspect < 1.0:  # Coin applies the height angle to the shorter side
            half_w, half_h = half_h, half_h / aspect
        d = fwd + right * (nx * half_w) + up * (ny * half_h)
        d.normalize()
        return pos, d

    def _target(self, event):
        """The dragged pole's new position for the event, or ``None`` when the ray gives none."""
        drag = self._drag
        origin, direction = self._ray(event)
        p0 = drag["pole"]
        mode = drag["mode"]
        if mode == "view":
            fwd = self._cb_view.getCameraNode().orientation.getValue()
            normal = App.Rotation(*fwd.getValue()).multVec(App.Vector(0, 0, -1))
            denom = direction.dot(normal)
            if abs(denom) < 1e-12:
                return None
            t = (p0 - origin).dot(normal) / denom
            return origin + direction * t
        axis = _AXES[mode]
        w0 = p0 - origin
        b = axis.dot(direction)
        denom = 1.0 - b * b
        if denom < 1e-12:
            return None
        s = (b * direction.dot(w0) - axis.dot(w0)) / denom
        return p0 + axis * s

    def _move(self, event):
        drag = self._drag
        drag["last"] = event.position()
        if drag.get("busy"):  # a move that arrives while one is processed: only the newest is kept
            drag["pending"] = event
            return
        drag["busy"] = True
        try:
            while event is not None and self._drag is drag:
                self._apply_move(event)
                event, drag["pending"] = drag.get("pending"), None
        finally:
            drag["busy"] = False

    def _apply_move(self, event):
        drag = self._drag
        target = self._target(event)
        if target is None:
            return
        offset = drag["start"] + (target - drag["pole"])
        self._set_chosen_offset(drag["index"], offset)

    def _set_chosen_offset(self, index, offset):
        if offset.Length > 0.0:
            self.moves[index] = offset
        else:
            self.moves.pop(index, None)
        self._sync_pole_fields()
        self.refresh()

    def end_drag(self):
        """The release: the drag is one panel change; the state is the one a typed offset gives."""
        drag, self._drag = self._drag, None
        self._set_app_filter(False)
        if drag is None:
            return
        pending = drag.get("pending")
        if pending is not None:  # the newest move before the release is always processed
            self._drag = drag
            self._apply_move(pending)
            self._drag = None
        self._swallow = None

    def cancel_drag(self):
        """Escape or a right press: the pole goes back to its offset at the press, the drag ends."""
        drag, self._drag = self._drag, None
        self._set_app_filter(False)
        if drag is None:
            return
        self._set_chosen_offset(drag["index"], drag["start"])

    # ---- update hooks
    def _connect_observer(self):
        if self.surface is None:
            return
        self._observer = _SourceObserver(self)
        App.addDocumentObserver(self._observer)

    def _disconnect_observer(self):
        if self._observer is not None:
            with contextlib.suppress(Exception):
                App.removeDocumentObserver(self._observer)
        self._observer = None

    def _watched(self, obj):
        try:
            if obj is None or self.feature is not None and obj == self.feature:
                return False
            return obj.Document == self.document
        except Exception:
            return False

    def _source_changed(self, obj, prop):
        if self._torn_down or self._busy_accept or self._reload_pending:
            return
        if obj is not None and (prop not in ("Shape", "Placement") or not self._watched(obj)):
            return
        self._reload_pending = True
        QtCore.QTimer.singleShot(0, self._reload_source)

    def _reload_source(self):
        self._reload_pending = False
        if self._torn_down or self._busy_accept:
            return
        try:
            face = SPE.resolve_face(self.source)
            surface = SPE.editable_surface(face)
        except Exception:
            self._close_panel()  # the source is gone or no longer one face
            return
        if self._drag is not None:
            self.cancel_drag()
        if SPE.grid_signature(surface) != self._grid_ref:
            # as 4a's re-edit on a changed grid: the pending moves' indices do not carry over to the new
            # grid, so they are dropped; the panel shows the new grid and OK records moves made on it
            self._grid_ref = SPE.grid_signature(surface)
            self.moves.clear()
            self._regridded = True
            self._busy = True
            self.u_spin.setRange(1, surface.NbUPoles)
            self.v_spin.setRange(1, surface.NbVPoles)
            self._busy = False
            self.chosen = (self.u_spin.value(), self.v_spin.value())
            self.face, self.surface = face, surface
            self._sync_pole_fields()
            self.error = ""
            self.refresh()
            return
        self.face, self.surface = face, surface
        self.error = ""
        self.refresh()

    def _object_deleted(self, obj):
        if self._torn_down:
            return
        try:
            chain = {self.source[0], _leaf_object(self.source)}
            hit = obj in chain or (self.feature is not None and obj == self.feature)
        except Exception:
            hit = True
        if hit:
            self._close_panel()

    def _document_deleted(self, doc):
        try:
            mine = doc == self.document
        except Exception:
            mine = True
        if mine:
            self._close_panel()

    def _close_panel(self):
        """Close the panel without touching the document: the nodes and event filters go at once; the
        observer (which is calling us) and the dialog go from the event loop."""
        if self._torn_down:
            return
        self._drag = None
        self._swallow = None
        self._torn_down = True
        self._forget()
        with contextlib.suppress(Exception):
            self._remove_nodes()
        self._disconnect_view()
        QtCore.QTimer.singleShot(0, self._close_dialog)

    def _close_dialog(self):
        self._disconnect_observer()
        try:
            if Gui.Control.activeDialog():
                Gui.Control.closeDialog()
        except Exception:
            pass

    # ---- task dialog protocol
    def getStandardButtons(self):
        return QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel

    def isAllowedAlterDocument(self):
        return True

    def _local_moves(self):
        inv = self.rotation.inverted()
        return [(index, inv.multVec(v)) for index, v in self.moves.items()]

    def _teardown(self):
        if self._torn_down:
            return
        self._torn_down = True
        self._forget()
        self._drag = None
        self._swallow = None
        self._disconnect_observer()
        self._remove_nodes()
        self._disconnect_view()
        self._leave_edit_mode()

    def _forget(self):
        if self in _ACTIVE:
            _ACTIVE.remove(self)

    def _leave_edit_mode(self):
        """The panel may have been opened by ``setEdit``; leave the edit mode with it."""
        try:
            gdoc = Gui.ActiveDocument
            vobj = gdoc.getInEdit() if gdoc is not None else None
            if vobj is not None and self.feature is not None and vobj.Object == self.feature:
                gdoc.resetEdit()
        except Exception:
            pass

    def accept(self):
        self.end_drag()
        if self.surface is None:
            self.say(self.error)
            return False
        if not self.refresh():
            return False  # the status line shows the refusal; nothing was changed
        doc = self.document or (Gui.ActiveDocument.Document if Gui.ActiveDocument is not None else App.ActiveDocument)
        if doc is None:
            self.say(_tr("InvalidInput: no document is open"))
            return False
        local = self._local_moves()
        locks = self._locks_dict()
        # visibility is restored first so that the undo of the new step brings back what the user had
        source_leaf = None if self.feature is not None else _leaf_object(self.source)
        shown = [o for o, _v in self._hidden]
        self._restore_inputs()
        self._busy_accept = True
        doc.openTransaction(TRANSACTION)
        try:
            if self.feature is None:
                feature = SPE.makePoleEditFeature(doc, self.source, local, locks)
                if source_leaf is not None and source_leaf in shown:
                    source_leaf.Visibility = False
            else:
                feature = self.feature
                SPE.store_edit(feature, local, locks)
                feature.GridSignature = SPE.source_signature(feature.Source)
            doc.recompute()
            if not feature.isValid():
                raise ValueError(feature.getStatusString() or "ConstructionFailed: the feature did not recompute")
        except Exception as e:
            doc.abortTransaction()
            for o in shown:
                try:
                    o.Visibility = False
                except Exception:
                    pass
            self._hidden = [(o, True) for o in shown]
            self._busy_accept = False
            self.say(str(e) or e.__class__.__name__)
            return False
        doc.commitTransaction()
        self.created = feature
        self._teardown()
        return True

    def reject(self):
        self._drag = None  # a drag in progress ends first; nothing was written
        self._teardown()
        self._restore_inputs()
        return True

    def closed(self):
        self._teardown()


def _index_of(box, data):
    for i in range(box.count()):
        if box.itemData(i) == data:
            return i
    return 0


class _EditPolesCommand:
    def GetResources(self):
        return {
            "Pixmap": "Surface_EditPoles",
            "MenuText": QtCore.QT_TRANSLATE_NOOP("Surface_EditPoles", "Edit control points"),
            "ToolTip": QtCore.QT_TRANSLATE_NOOP(
                "Surface_EditPoles",
                "Move the control points of the selected face by numeric offsets or by clicking a pole marker, keep the continuity of chosen sides, and watch curvature, zebra or isophotes update",
            ),
            "StatusTip": QtCore.QT_TRANSLATE_NOOP("Surface_EditPoles", "Edit the control points of the selected face"),
        }

    def IsActive(self):
        return Gui.ActiveDocument is not None and not Gui.Control.activeDialog() and selected_face_source() is not None

    def Activated(self):
        panel = PoleEditPanel()
        try:
            Gui.Control.showDialog(panel)
        except Exception:
            panel.reject()
            raise


COMMANDS = (("Surface_EditPoles", _EditPolesCommand),)


def register():
    for name, cls in COMMANDS:
        Gui.addCommand(name, cls())


register()
