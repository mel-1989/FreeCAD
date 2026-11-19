# SPDX-License-Identifier: LGPL-2.1-or-later

"""Surface workbench command ``Surface_MatchSurface``: match one side of a B-spline face to a target edge.

The task panel takes a source face with a side (U0, U1, V0, V1) and a target edge (with its face), a
continuity (G0, G1, G2), a maximum refinement level (0 to 6) and "Allow approximation". When the source
and the target are set, every change recomputes ``SurfaceMatch.matchSurface`` and draws the matched face
in the 3D view as one node named ``G09_MATCH`` (the face, and the matched side as a highlighted curve);
the source is hidden while the panel is open. The status line shows the level, the gap, the angle, the
curvature jump and every warning name, or the status name of a refusal. Nothing recomputes the document
until OK.

OK creates one ``MatchSurface`` feature, or on a re-edit (a double click on the feature) updates its
properties, in one undo transaction named "Match surface", and leaves the source hidden. A failing OK
leaves the panel open and changes nothing. Cancel changes nothing, opens no transaction, restores the
source's visibility and leaves no ``G09_`` node in the view.

Sources (clean room): FreeCAD's own Python task panels and commands, the Qt documentation, the public
Coin3D (Open Inventor) scene-graph API, and L. Piegl and W. Tiller, *The NURBS Book*, 2nd ed., 1997.
"""

import contextlib

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import SurfaceAnalysisGui as SAG
import SurfaceMatch as SM
import SurfacePoleEdit as PE
from SurfaceAnalysisCommands import _Relay, selected_subshapes
from SurfaceBlendSurfaceCommands import selected_edge_with_face

__all__ = ["MatchPanel", "ViewProviderMatch", "active_panel", "MAX_PANEL_LEVEL"]

_ACTIVE = []  # the open panel, for tests and scripts

TRANSACTION = "Match surface"
MAX_PANEL_LEVEL = 6
NODE_NAME = "G09_MATCH"
_NODE_NAMES = (NODE_NAME,)
_CONTINUITY = ("G0", "G1", "G2")


def active_panel():
    """The open match surface panel, or ``None``."""
    return _ACTIVE[-1] if _ACTIVE else None


def _tr(text):
    return QtCore.QCoreApplication.translate("Surface_MatchSurface", text)


def _alive(entry):
    try:
        return entry[0].Document is not None and entry[0].Document.getObject(entry[0].Name) is not None
    except Exception:  # ReferenceError: the object was deleted
        return False


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


def selected_face_and_edge():
    """``((object, [subname]), edge_shape_or_None)`` of the selection: exactly one face, and the one
    selected edge of the same object if there is one; ``None`` for any other selection."""
    faces = selected_subshapes(("Face",))
    if len(faces) != 1:
        return None
    face = faces[0]
    edges = [e for e in selected_subshapes(("Edge",)) if e["object"] is face["object"]]
    edge = edges[0]["shape"] if len(edges) == 1 else None
    return (face["object"], [face["subname"]]), edge


class ViewProviderMatch:
    """View provider proxy of the match surface feature; a double click opens the panel on its inputs."""

    def __init__(self, vobj):
        vobj.Proxy = self
        self.Object = vobj.Object

    def attach(self, vobj):
        self.Object = vobj.Object

    def getIcon(self):
        return ":/icons/Surface_MatchSurface.svg"

    def updateData(self, obj, prop):
        return

    def onChanged(self, vobj, prop):
        return

    def _open(self):
        if Gui.Control.activeDialog():
            return False
        Gui.Control.showDialog(MatchPanel(feature=self.Object))
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


class _DocumentCloseObserver:
    """Closes the panel when the document it was opened on is closed."""

    def __init__(self, panel):
        self.panel = panel

    def slotDeletedDocument(self, gdoc):
        panel = self.panel
        try:
            mine = gdoc.Document == panel.document
        except Exception:
            mine = False
        if mine:
            panel.document_closed()


class MatchPanel:
    """Task panel for one match surface: source face and side, target edge, continuity and level."""

    def __init__(self, document=None, feature=None, view=None):
        self.document = document
        self.feature = feature
        self.view = view
        self.created = None
        self.source = None  # (object, [subname])
        self.target = None  # (object, [subnames])
        self.tolerance = 1e-4
        self.result = None  # the last good matchSurface result
        self.error = ""
        self.handle = None
        self._busy = True
        self._torn_down = False
        self._observer = None
        self._node_ufs = None
        self._hidden = []  # (object, visibility before the panel opened)
        self._relays = []
        if feature is not None:
            self.source = (feature.Source[0], list(feature.Source[1]))
            self.target = (feature.Target[0], list(feature.Target[1]))
            self.tolerance = feature.Tolerance
            if self.document is None:
                self.document = feature.Document
        if self.document is None:  # the document the panel was opened on, whatever becomes active later
            self.document = Gui.ActiveDocument.Document if Gui.ActiveDocument is not None else App.ActiveDocument
        self._build_form()
        self._busy = False
        self._hide_inputs()
        _ACTIVE.append(self)
        self._observer = _DocumentCloseObserver(self)
        Gui.addDocumentObserver(self._observer)
        self.refresh()

    # ---- widgets
    def _build_form(self):
        form = QtWidgets.QWidget()
        form.setObjectName("G09_MatchPanel")
        form.setWindowTitle(_tr("Match surface"))
        self.form = form
        layout = QtWidgets.QVBoxLayout(form)
        self.fields = {}
        for key, title in (("Source", "Source face"), ("Target", "Target edge")):
            label = QtWidgets.QLabel(_tr(title + ":"), form)
            label.setObjectName("G09_Match_%s_Label" % key)
            box = QtWidgets.QLineEdit(form)
            box.setObjectName("G09_Match_%s" % key)
            box.setReadOnly(True)
            label.setBuddy(box)
            button = QtWidgets.QPushButton(_tr("Set from selection"), form)
            button.setObjectName("G09_Match_%s_Set" % key)
            relay = _Relay(lambda k=key: self.set_from_selection(k), form)
            self._relays.append(relay)
            button.clicked.connect(relay.changed)
            layout.addWidget(label)
            row = QtWidgets.QHBoxLayout()
            row.addWidget(box)
            row.addWidget(button)
            layout.addLayout(row)
            self.fields[key] = box
            if key == "Source":
                self.side = self._combo("G09_Match_Side", PE.SIDES, "V0")
                self._row(layout, "Side:", self.side)
        self.continuity = self._combo("G09_Match_Continuity", _CONTINUITY, "G2")
        self._row(layout, "Continuity:", self.continuity)
        self.level = QtWidgets.QSpinBox(form)
        self.level.setObjectName("G09_Match_MaxLevel")
        self.level.setRange(0, MAX_PANEL_LEVEL)
        self.level.setValue(5)
        self.level.setToolTip(_tr("Most knot refinement levels along the side"))
        self._row(layout, "Maximum level:", self.level)
        self.approximate = QtWidgets.QCheckBox(_tr("Allow approximation"), form)
        self.approximate.setObjectName("G09_Match_Approximate")
        layout.addWidget(self.approximate)
        if self.feature is not None:
            self.side.setCurrentText(self.feature.Side)
            self.continuity.setCurrentText(self.feature.Continuity)
            stored = int(self.feature.MaxLevel)
            self.level.setRange(0, max(MAX_PANEL_LEVEL, stored))  # a stored level above 6 is shown and kept
            self.level.setValue(stored)
            self.approximate.setChecked(bool(self.feature.AllowApproximation))
            self.fields["Source"].setText(self._text(self.source))
            self.fields["Target"].setText(self._text(self.target))
        self.status = QtWidgets.QLabel(form)
        self.status.setObjectName("G09_MatchStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()
        self._r_change = _Relay(self._changed, form)
        self.side.currentIndexChanged.connect(self._r_change.changed)
        self.continuity.currentIndexChanged.connect(self._r_change.changed)
        self.level.valueChanged.connect(self._r_change.changed)
        self.approximate.stateChanged.connect(self._r_change.changed)

    def _combo(self, name, items, current):
        box = QtWidgets.QComboBox(self.form)
        box.setObjectName(name)
        box.addItems(list(items))
        box.setCurrentText(current)
        return box

    def _row(self, layout, text, widget):
        row = QtWidgets.QHBoxLayout()
        label = QtWidgets.QLabel(_tr(text), self.form)
        label.setObjectName(widget.objectName() + "_Label")
        label.setBuddy(widget)
        row.addWidget(label)
        row.addWidget(widget)
        layout.addLayout(row)

    @staticmethod
    def _text(entry):
        obj, subs = entry
        return "%s.%s" % (obj.Label, subs[-1]) if subs else obj.Label

    def say(self, text):
        self.status.setText(text)

    # ---- inputs
    def set_source(self, entry, side=None):
        self.source = (entry[0], list(entry[1]))
        self.fields["Source"].setText(self._text(self.source))
        if side is not None:
            self._busy = True
            self.side.setCurrentText(side)
            self._busy = False
        self._rehide()
        self.refresh()

    def set_target(self, entry):
        self.target = (entry[0], list(entry[1]))
        self.fields["Target"].setText(self._text(self.target))
        self.refresh()

    def set_from_selection(self, key):
        if key == "Source":
            picked = selected_face_and_edge()
            if picked is None:
                self.say(_tr("InvalidInput: select exactly one face for the source"))
                return
            entry, edge = picked
            side = None
            if edge is not None:
                try:
                    face = PE.resolve_face(entry)
                    side = SM._resolve_side(edge, face, PE.editable_surface(face), self.tolerance)
                except Exception as e:
                    self.say(str(e) or e.__class__.__name__)
                    side = None
            self.set_source(entry, side)
        else:
            picked = selected_edge_with_face()
            if picked is None:
                self.say(_tr("InvalidInput: select exactly one edge for the target"))
                return
            self.set_target(picked)

    def _changed(self):
        if not self._busy:
            self.refresh()

    # ---- visibility
    def _hide_inputs(self):
        if self.feature is not None:
            # a double click on the tree leaves an active (still empty) "Edit" transaction, which the first
            # change of a document object, here its Visibility, would turn into an undo step of its own
            # and make the later OK a second one: it is closed first
            with contextlib.suppress(Exception):
                active = App.getActiveTransaction()
                if active and active[0] == "Edit":
                    App.closeActiveTransaction()
            self._hide(self.feature, view_only=True)
        if self.source is not None:
            self._hide(_leaf_object(self.source))

    def _rehide(self):
        """A new source is hidden too; the one it replaces keeps its visibility."""
        if self.source is None:
            return
        leaf = _leaf_object(self.source)
        if all(leaf is not o for o, _v in self._hidden):
            self._hide(leaf)

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

    def _restore_inputs(self):
        for obj, vis in self._hidden:
            try:
                if obj is self.feature and obj.ViewObject is not None:
                    obj.ViewObject.Visibility = vis
                else:
                    obj.Visibility = vis
            except Exception:
                pass
        self._hidden = []

    # ---- the 3D view
    def _panel_view(self):
        try:
            return SAG._active_view(self.view)
        except Exception:
            return None

    def _remove_nodes(self):
        held, self._node_ufs = self._node_ufs, None
        targets = [held]
        with contextlib.suppress(Exception):
            view = self._panel_view()
            targets.append(None if view is None else SAG._find_selection_node(view))
        done = []
        for ufs in targets:
            if ufs is None or any(ufs is d for d in done):
                continue
            done.append(ufs)
            with contextlib.suppress(Exception):
                for i in reversed(range(ufs.getNumChildren())):
                    if ufs.getChild(i).getName().getString() in _NODE_NAMES:
                        ufs.removeChild(i)
        if held is not None:  # the reference taken in _redraw keeps it valid after its view closed
            with contextlib.suppress(Exception):
                held.unref()

    def _match_node(self, verts, tris, curve):
        coin = SAG._coin()
        sep = coin.SoSeparator()
        sep.setName(NODE_NAME)
        ps = coin.SoPickStyle()
        ps.style = coin.SoPickStyle.UNPICKABLE
        sep.addChild(ps)
        face = coin.SoSeparator()
        face.setName("G09_MATCH_FACE")
        mat = coin.SoMaterial()
        mat.diffuseColor = (0.78, 0.86, 0.78)
        face.addChild(mat)
        hints = coin.SoShapeHints()
        hints.vertexOrdering = coin.SoShapeHints.UNKNOWN_ORDERING
        hints.shapeType = coin.SoShapeHints.UNKNOWN_SHAPE_TYPE
        face.addChild(hints)
        cd = coin.SoCoordinate3()
        cd.point.setValues(0, len(verts), [(p.x, p.y, p.z) for p in verts])
        face.addChild(cd)
        fs = coin.SoIndexedFaceSet()
        idx = []
        for a, b, c in tris:
            idx.extend((a, b, c, -1))
        fs.coordIndex.setValues(0, len(idx), idx)
        face.addChild(fs)
        sep.addChild(face)
        side = coin.SoSeparator()
        side.setName("G09_MATCH_SIDE")
        lm = coin.SoLightModel()
        lm.model = coin.SoLightModel.BASE_COLOR
        side.addChild(lm)
        with contextlib.suppress(Exception):
            depth = coin.SoDepthBuffer()
            depth.function = coin.SoDepthBuffer.ALWAYS
            side.addChild(depth)
        lmat = coin.SoMaterial()
        lmat.diffuseColor = (1.0, 0.55, 0.0)
        side.addChild(lmat)
        style = coin.SoDrawStyle()
        style.lineWidth = 4
        side.addChild(style)
        cc = coin.SoCoordinate3()
        cc.point.setValues(0, len(curve), [(p.x, p.y, p.z) for p in curve])
        side.addChild(cc)
        ls = coin.SoLineSet()
        ls.numVertices.setValue(len(curve))
        side.addChild(ls)
        sep.addChild(side)
        return sep

    def _redraw(self):
        """Replace the ``G09_MATCH`` node by one of the current result."""
        self._remove_nodes()
        if self._torn_down or self.result is None:
            return
        view = self._panel_view()
        if view is None:
            return
        ufs = SAG._find_selection_node(view)
        ufs.ref()  # released in _remove_nodes, so removing from it is safe even after its view closed
        self._node_ufs = ufs
        copy = self.result["face"].copy()  # the triangulation stays off the shared shape
        verts, tris = copy.tessellate(SAG._effective_deflection(copy, None))
        iso = SM._side_iso(self.result["surface"], self.side.currentText())
        curve = iso.discretize(Number=65)
        ufs.addChild(self._match_node(verts, tris, curve))

    # ---- state
    def _status_text(self, r):
        text = _tr("Level %d, gap %.6g mm, angle %.6g deg, curvature jump %.6g") % (
            r["level"], r["gap"], r["angle"], r["curvature_jump"]
        )
        if r["approximate"]:
            text += _tr(", approximate")
        if r["warnings"]:
            text += _tr(". Warnings: %s") % ", ".join(r["warnings"])
        return text

    def refresh(self):
        """Match with the current inputs; on success draw the preview and show the numbers, else show the
        refusal and keep the last good preview. ``True`` when the match is valid."""
        if self.source is None or self.target is None:
            self.error = _tr("InvalidInput: set the source face and the target edge")
            self.say(self.error)
            return False
        if not (_alive(self.source) and _alive(self.target) and (self.feature is None or _alive((self.feature,)))):
            self.error = _tr("InputDeleted: the source or the target was deleted")
            self.say(self.error)
            return False
        try:
            r = SM.build_match(
                self.source,
                self.side.currentText(),
                self.target,
                _CONTINUITY.index(self.continuity.currentText()),
                self.tolerance,
                self.level.value(),
                self.approximate.isChecked(),
                feature=self.feature,
            )
        except Exception as e:
            self.error = str(e) or e.__class__.__name__
            self.say(self.error)
            return False
        self.error = ""
        self.result = r
        self.say(self._status_text(r))
        self._redraw()
        return True

    # ---- task dialog protocol
    def getStandardButtons(self):
        return QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel

    def isAllowedAlterDocument(self):
        return True

    def _teardown(self):
        if self._torn_down:
            return
        self._torn_down = True
        if self in _ACTIVE:
            _ACTIVE.remove(self)
        observer, self._observer = self._observer, None
        if observer is not None:
            with contextlib.suppress(Exception):
                Gui.removeDocumentObserver(observer)
        self._remove_nodes()
        with contextlib.suppress(Exception):  # the panel may have been opened by setEdit
            gdoc = Gui.ActiveDocument
            vobj = gdoc.getInEdit() if gdoc is not None else None
            if vobj is not None and self.feature is not None and vobj.Object == self.feature:
                gdoc.resetEdit()

    def accept(self):
        if not self.refresh():
            return False  # the status line shows the refusal; nothing was changed
        doc = self.document or (Gui.ActiveDocument.Document if Gui.ActiveDocument is not None else App.ActiveDocument)
        if doc is None:
            self.say(_tr("InvalidInput: no document is open"))
            return False
        side = self.side.currentText()
        k = self.continuity.currentText()
        # visibility is restored first so that the undo of the new step brings back what the user had
        source_leaf = _leaf_object(self.source)
        shown = [o for o, _v in self._hidden]
        self._restore_inputs()
        doc.openTransaction(TRANSACTION)
        try:
            if self.feature is None:
                feature = SM.makeMatchSurfaceFeature(doc, self.source, side, self.target)
            else:
                feature = self.feature
                if (feature.Source[0], list(feature.Source[1])) != self.source:
                    feature.Source = (self.source[0], self.source[1])
                if (feature.Target[0], list(feature.Target[1])) != self.target:
                    feature.Target = (self.target[0], self.target[1])
                feature.Side = side
            feature.Continuity = k
            feature.MaxLevel = self.level.value()
            feature.AllowApproximation = self.approximate.isChecked()
            if source_leaf in shown:
                source_leaf.Visibility = False
            doc.recompute()
            if not feature.isValid():
                raise ValueError(feature.getStatusString() or "ConstructionFailed: the feature did not recompute")
        except Exception as e:
            doc.abortTransaction()
            for o in shown:
                with contextlib.suppress(Exception):
                    o.Visibility = False
            self._hidden = [(o, True) for o in shown]
            self.say(str(e) or e.__class__.__name__)
            return False
        doc.commitTransaction()
        self.created = feature
        self._teardown()
        return True

    def reject(self):
        self._teardown()
        self._restore_inputs()
        return True

    def closed(self):
        self._teardown()

    def document_closed(self):
        """The panel's document is closing: close the panel without a transaction or any change."""
        self._teardown()
        self._hidden = []  # the objects go with the document
        with contextlib.suppress(Exception):
            if Gui.Control.activeDialog():
                Gui.Control.closeDialog()


class _MatchSurfaceCommand:
    def GetResources(self):
        return {
            "Pixmap": "Surface_MatchSurface",
            "MenuText": QtCore.QT_TRANSLATE_NOOP("Surface_MatchSurface", "Match surface"),
            "ToolTip": QtCore.QT_TRANSLATE_NOOP(
                "Surface_MatchSurface",
                "Move one side of a B-spline face onto an edge of another face with G0, G1 or G2 continuity, with a live preview of the matched face",
            ),
            "StatusTip": QtCore.QT_TRANSLATE_NOOP("Surface_MatchSurface", "Match a side of a surface to a target edge"),
        }

    def IsActive(self):
        return Gui.ActiveDocument is not None and not Gui.Control.activeDialog()

    def Activated(self):
        panel = MatchPanel()
        try:
            Gui.Control.showDialog(panel)
        except Exception:
            panel.reject()
            raise


COMMANDS = (("Surface_MatchSurface", _MatchSurfaceCommand),)


def register():
    for name, cls in COMMANDS:
        Gui.addCommand(name, cls())


register()
