# SPDX-License-Identifier: LGPL-2.1-or-later
"""Task panel for assembly cuts and holes (cad-fork G06 slice 4b). Imported only with the GUI up.

The panel creates or edits one assembly feature through the slice 4a API (``AssemblyFeature``). The
feature exists while the panel is open so that the preview can ask it what it cuts
(``AssemblyFeature.affected``), but no document transaction is open during the session, so the
working feature is never in the undo history and the user's other edits (made while the panel is open,
each in its own transaction) are never swept up with it. Cancel deletes a new feature or writes the
three properties the panel touches (Spec, ToolObject, Scope) back on an edited one, nothing else. OK
puts the feature back to its starting state and then makes the final change in one short transaction,
so the feature is its own single undo step. The preview lists the components the feature will cut with the volume each loses and
draws the removed material in the 3D view as temporary Coin geometry under the selection root (the
node a picture capture renders). That geometry is not a document object and changes no view
provider.

The part documents are never touched. Scope: either every component the tool reaches (an empty
``Scope``) or a checked list of components, labelled as the user sees them with their link path.

Behaviour sources: the slice 4a API; OCCT Boolean operations through it; Coin3D scene-graph nodes
from the public Coin documentation; Qt widget documentation. Wording and layout are this project's own.
"""

import json

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets
from pivy import coin

import AssemblyFeature as AF
import Interference as I
from InterferencePanel import Highlight

translate = App.Qt.translate

NODE_NAME = "AssemblyFeaturePreview"
NODE_REMOVED = NODE_NAME + "Removed"
NODE_OUTLINE = NODE_NAME + "Outline"
COL_REMOVED = (0.90, 0.05, 0.05)
COL_REMOVED_EDGE = (0.45, 0.00, 0.00)
COL_CUT = (0.95, 0.50, 0.00)

TOOL_EXTRUDE = "extrude"
TOOL_SOLID = "solid"
TOOL_FIXED = "fixed"      # a box or cylinder tool made by script: shown, kept, not edited here
TITLES = {"cut": "Assembly cut", "hole": "Assembly hole"}


def part_text(desc):
    path = desc.get("link_path") or [desc.get("label", "")]
    return " / ".join(path)


def _is_face(sub):
    """True for a sub-element path that ends in a face: 'Face3', 'Plate.Face3.' or 'Sub.Link.Box.Face3'."""
    parts = [p for p in sub.split(".") if p]
    return bool(parts) and parts[-1].startswith("Face")


def has_wires(obj):
    shp = getattr(obj, "Shape", None)
    return shp is not None and not shp.isNull() and len(shp.Wires) > 0


def is_profile(obj):
    """A sketch or planar wire/face shape: wires and neither solids nor shells (a solid is a tool, not a profile)."""
    shp = getattr(obj, "Shape", None)
    return has_wires(obj) and not shp.Solids and not shp.Shells


def has_solid(obj):
    shp = getattr(obj, "Shape", None)
    return shp is not None and not shp.isNull() and len(shp.Solids) > 0


# --------------------------------------------------------------------------- preview

class Preview(Highlight):
    """Temporary scene-graph geometry: the material removed (red) and the outline of each cut component."""

    def show_cuts(self, items):
        """``items``: list of (cut component shape, removed shape or None)."""
        self.clear()
        sg = self._scene_graph()
        if sg is None:
            return False
        shapes = [s for s, r in items if s is not None and not s.isNull()]
        shapes += [r for s, r in items if r is not None and not r.isNull()]
        if not shapes:
            return True
        defl = self._deflection(*shapes)
        root = coin.SoSeparator()
        root.setName(NODE_NAME)
        pick = coin.SoPickStyle()
        pick.style = coin.SoPickStyle.UNPICKABLE
        root.addChild(pick)
        depth = coin.SoDepthBuffer()
        depth.function = coin.SoDepthBuffer.ALWAYS
        root.addChild(depth)
        for cut, removed in items:
            if removed is not None and not removed.isNull() and removed.Solids:
                root.addChild(self._solid_node(removed, COL_REMOVED, 0.15, defl, NODE_REMOVED))
                root.addChild(self._line_node(removed, COL_REMOVED_EDGE, 2, defl))
            if cut is not None and not cut.isNull() and cut.Edges:
                root.addChild(self._line_node(cut, COL_CUT, 2, defl, NODE_OUTLINE))
        sg.addChild(root)
        self.sg, self.node = sg, root
        return True


# --------------------------------------------------------------------------- panel

def _spin(low, high, value, decimals=3, suffix=" mm", name=""):
    s = QtWidgets.QDoubleSpinBox()
    s.setObjectName(name)
    s.setDecimals(decimals)
    s.setRange(low, high)
    s.setValue(value)
    if suffix:
        s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    return s


class TaskAssemblyFeature:
    """Create (``feature`` None) or edit (``feature`` given) a cut or hole of ``assembly``."""

    def __init__(self, assembly, kind="cut", feature=None, selection=None):
        if Gui.Control.activeDialog():   # before anything touches the document
            raise RuntimeError("another task dialog is already open")
        if feature is not None:
            kind = feature.Kind
        self.assembly = assembly
        self.kind = kind
        self.feature = feature
        self.editing = feature is not None
        self.doc = assembly.Document
        self.document_name = self.doc.Name
        self.done = False
        self.problem = None
        self.rows = []              # (path, removed_mm3) of what the feature cuts now
        self.errors = []
        self.reached = None         # keys the tool reaches with an empty scope (set while every component is selected)
        self.tool_obj = feature.ToolObject if feature is not None else None
        self.fixed_tool = None
        self.preview = Preview(self.document_name)
        self.insts = I.resolve(assembly, include_hidden=True, apply_features=False).insts
        self.labels = {i.key: part_text(i.describe(assembly)) for i in self.insts}
        sel = list(selection) if selection is not None else list(Gui.Selection.getSelection())
        self._selection = [o for o in sel if o is not assembly]
        self._active = False
        self._cur = None            # (spec, tool object, scope) of the last valid input
        self._scope_dirty = False   # the user touched the scope controls
        self._orig = None
        if feature is not None:
            self._orig = {"Spec": feature.Spec, "ToolObject": feature.ToolObject, "Scope": list(feature.Scope)}

        self.form = QtWidgets.QWidget()
        self.form.setObjectName("Assembly_TaskAssemblyFeature")
        self.form.setWindowTitle(translate("Assembly", TITLES[kind]))
        lay = QtWidgets.QVBoxLayout(self.form)
        self.fields = QtWidgets.QFormLayout()
        lay.addLayout(self.fields)
        self._build_tool_fields()
        self._build_scope(lay)
        self._build_preview(lay)

        if feature is not None:
            self._load(feature)
        else:
            self._defaults()
        self._active = True
        self.form.destroyed.connect(self._form_gone)
        self.refresh()

    # ---- leaving the session

    def _doc_alive(self):
        return self.document_name in App.listDocuments()

    def _restore_edited(self):
        """Put an edited feature's Spec, ToolObject and Scope back (no transaction is open: nothing is recorded)."""
        feat = self.feature
        o = self._orig
        if feat.Spec != o["Spec"]:
            feat.Spec = o["Spec"]
        if feat.ToolObject is not o["ToolObject"]:
            feat.ToolObject = o["ToolObject"]
        if list(feat.Scope) != o["Scope"]:
            feat.Scope = list(o["Scope"])
        feat.Proxy.results = None

    def _discard(self):
        """Undo the session's effect on the document: delete a new feature, restore an edited one."""
        if self.discarded or not self._doc_alive():
            self.discarded = True
            return
        self.discarded = True
        feat = self.feature
        if feat is None:
            return
        if self.editing:
            self._restore_edited()
        else:
            self.doc.removeObject(feat.Name)
            self.feature = None

    discarded = False

    def _form_gone(self, *_):
        """Gui.Control.closeDialog() calls neither accept nor reject, nor does closing the document: tidy up."""
        self.preview.clear()
        if not self.done:
            try:
                self._discard()
            except Exception:  # noqa: BLE001  (the document is going away)
                pass

    # ---- tool fields

    def _build_tool_fields(self):
        f = self.fields
        if self.kind == "cut":
            self.tool_kind = QtWidgets.QComboBox()
            self.tool_kind.setObjectName("tool_kind")
            self.tool_kind.addItem(translate("Assembly", "Extrude a sketch or profile"), TOOL_EXTRUDE)
            self.tool_kind.addItem(translate("Assembly", "Use a solid as the tool"), TOOL_SOLID)
            f.addRow(translate("Assembly", "Tool"), self.tool_kind)
            self.tool_label = QtWidgets.QLabel()
            self.tool_label.setObjectName("tool_object")
            self.tool_label.setWordWrap(True)
            self.pick_button = QtWidgets.QPushButton(translate("Assembly", "Use selected object"))
            self.pick_button.setObjectName("use_selected")
            row = QtWidgets.QVBoxLayout()
            row.addWidget(self.tool_label)
            row.addWidget(self.pick_button)
            f.addRow(translate("Assembly", "Object"), row)
            self.dir_x = _spin(-1e6, 1e6, 0.0, 4, "", "dir_x")
            self.dir_y = _spin(-1e6, 1e6, 0.0, 4, "", "dir_y")
            self.dir_z = _spin(-1e6, 1e6, -1.0, 4, "", "dir_z")
            f.addRow(translate("Assembly", "Direction x"), self.dir_x)
            f.addRow(translate("Assembly", "Direction y"), self.dir_y)
            f.addRow(translate("Assembly", "Direction z"), self.dir_z)
            self.reverse_button = QtWidgets.QPushButton(translate("Assembly", "Reverse direction"))
            self.reverse_button.setObjectName("reverse")
            f.addRow("", self.reverse_button)
            self.length = _spin(0.0, 1e6, 10.0, 3, " mm", "length")
            f.addRow(translate("Assembly", "Length"), self.length)
            self.through = QtWidgets.QCheckBox(translate("Assembly", "Through all"))
            self.through.setObjectName("through")
            f.addRow("", self.through)
            self.symmetric = QtWidgets.QCheckBox(translate("Assembly", "Symmetric about the profile"))
            self.symmetric.setObjectName("symmetric")
            f.addRow("", self.symmetric)
            self._extrude_widgets = [self.dir_x, self.dir_y, self.dir_z, self.reverse_button, self.length,
                                     self.through, self.symmetric]
            self.tool_kind.currentIndexChanged.connect(self._tool_kind_changed)
            self.pick_button.clicked.connect(self.use_selected_object)
            self.reverse_button.clicked.connect(self.reverse_direction)
            for w in (self.dir_x, self.dir_y, self.dir_z, self.length):
                w.valueChanged.connect(self._changed)
            self.through.toggled.connect(self._through_toggled)
            self.symmetric.toggled.connect(self._changed)
        else:
            self.pos = [_spin(-1e6, 1e6, 0.0, 3, " mm", "pos_%s" % a) for a in "xyz"]
            for a, w in zip("xyz", self.pos):
                f.addRow(translate("Assembly", "Position %s") % a, w)
            self.axis = [_spin(-1e6, 1e6, v, 4, "", "axis_%s" % a) for a, v in zip("xyz", (0.0, 0.0, -1.0))]
            for a, w in zip("xyz", self.axis):
                f.addRow(translate("Assembly", "Axis %s") % a, w)
            self.face_button = QtWidgets.QPushButton(translate("Assembly", "Use selected face"))
            self.face_button.setObjectName("use_face")
            f.addRow("", self.face_button)
            self.diameter = _spin(0.001, 1e6, 6.0, 3, " mm", "diameter")
            f.addRow(translate("Assembly", "Diameter"), self.diameter)
            self.depth = _spin(0.001, 1e6, 10.0, 3, " mm", "depth")
            f.addRow(translate("Assembly", "Depth"), self.depth)
            self.through = QtWidgets.QCheckBox(translate("Assembly", "Through all"))
            self.through.setObjectName("through")
            f.addRow("", self.through)
            self.cbore = QtWidgets.QCheckBox(translate("Assembly", "Counterbore"))
            self.cbore.setObjectName("counterbore")
            f.addRow("", self.cbore)
            self.cbore_diameter = _spin(0.001, 1e6, 12.0, 3, " mm", "cbore_diameter")
            f.addRow(translate("Assembly", "Counterbore diameter"), self.cbore_diameter)
            self.cbore_depth = _spin(0.001, 1e6, 3.0, 3, " mm", "cbore_depth")
            f.addRow(translate("Assembly", "Counterbore depth"), self.cbore_depth)
            for w in self.pos + self.axis + [self.diameter, self.depth, self.cbore_diameter, self.cbore_depth]:
                w.valueChanged.connect(self._changed)
            self.through.toggled.connect(self._through_toggled)
            self.cbore.toggled.connect(self._cbore_toggled)
            self.face_button.clicked.connect(self.use_selected_face)

    # ---- scope

    def _build_scope(self, lay):
        box = QtWidgets.QGroupBox(translate("Assembly", "Components to cut"))
        box.setObjectName("scope_group")
        v = QtWidgets.QVBoxLayout(box)
        self.scope_all = QtWidgets.QRadioButton(translate("Assembly", "Every component the feature reaches"))
        self.scope_all.setObjectName("scope_all")
        self.scope_some = QtWidgets.QRadioButton(translate("Assembly", "Only the components checked below"))
        self.scope_some.setObjectName("scope_some")
        self.scope_all.setChecked(True)
        v.addWidget(self.scope_all)
        v.addWidget(self.scope_some)
        self.components = QtWidgets.QListWidget()
        self.components.setObjectName("components")
        self.components.setMinimumHeight(70)
        self.components.setMaximumHeight(120)
        for inst in self.insts:
            text = self.labels[inst.key] + (translate("Assembly", " (hidden)") if inst.hidden else "")
            it = QtWidgets.QListWidgetItem(text)
            it.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.Unchecked)
            it.setData(QtCore.Qt.UserRole, inst.key)
            self.components.addItem(it)
        self.components.setEnabled(False)
        v.addWidget(self.components)
        lay.addWidget(box)
        self.scope_some.toggled.connect(self._scope_mode_changed)
        self.components.itemChanged.connect(self._item_changed)

    def _scope_mode_changed(self, explicit):
        if self._active:
            self._scope_dirty = True
        if explicit:
            self.components.setEnabled(True)
            if not self._explicit_touched and self.reached is not None:
                self._check_keys(self.reached)
        else:
            self.components.setEnabled(False)
        self._changed()

    _explicit_touched = False

    def _check_keys(self, keys):
        self.components.blockSignals(True)
        for i in range(self.components.count()):
            it = self.components.item(i)
            it.setCheckState(QtCore.Qt.Checked if it.data(QtCore.Qt.UserRole) in keys else QtCore.Qt.Unchecked)
        self.components.blockSignals(False)

    def checked_keys(self):
        return [self.components.item(i).data(QtCore.Qt.UserRole) for i in range(self.components.count())
                if self.components.item(i).checkState() == QtCore.Qt.Checked]

    def set_explicit_scope(self, keys):
        """Switch to the checked list and check exactly ``keys`` (what a user does with the radio and the boxes)."""
        self._explicit_touched = True
        self._scope_dirty = True
        self.scope_some.blockSignals(True)
        self.scope_some.setChecked(True)
        self.scope_some.blockSignals(False)
        self.components.setEnabled(True)
        self._check_keys(set(keys))
        self._changed()

    def set_scope_all(self):
        self.scope_all.setChecked(True)

    def _scope_value(self):
        """The ``Scope`` to write, or None when the checked list is empty (nothing to cut).

        An edit whose scope controls were not touched keeps the stored selectors as they are (prefixes and
        selectors that match nothing included)."""
        if self.editing and not self._scope_dirty:
            return list(self._orig["Scope"])
        if self.scope_all.isChecked():
            return []
        keys = self.checked_keys()
        return keys or None

    # ---- preview widgets

    def _build_preview(self, lay):
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
        self.table = QtWidgets.QTableWidget(0, 2)
        self.table.setObjectName("preview")
        self.table.setHorizontalHeaderLabels([translate("Assembly", "Component"), translate("Assembly", "Removed volume (mm³)")])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(80)
        lay.addWidget(self.table)

    # ---- loading and defaults

    def _set(self, widget, value):
        widget.blockSignals(True)
        if isinstance(widget, QtWidgets.QAbstractButton):
            widget.setChecked(bool(value))
        else:
            widget.setValue(float(value))
        widget.blockSignals(False)

    def _defaults(self):
        if self.kind == "cut":
            mode = 0
            for o in self._selection:
                if is_profile(o):
                    self.tool_obj = o
                    break
                if has_solid(o):   # a selected solid is a solid tool, never a profile
                    self.tool_obj = o
                    mode = 1
                    break
            self.tool_kind.blockSignals(True)
            self.tool_kind.setCurrentIndex(mode)
            self.tool_kind.blockSignals(False)
            if mode == 0:
                self._default_direction()
        else:
            for sel in Gui.Selection.getSelectionEx("*", 0):
                if sel.SubElementNames and _is_face(sel.SubElementNames[0]):
                    self._face_to_fields(sel)
                    break
            self.cbore_diameter.setEnabled(False)
            self.cbore_depth.setEnabled(False)
            self.depth.setEnabled(True)
        if self.kind == "cut":
            self._sync_tool_widgets()

    def _load(self, feat):
        spec = json.loads(feat.Spec) if feat.Spec else {}
        if self.kind == "cut":
            t = spec["tool"]
            if t["kind"] == "extrude":
                self.tool_kind.setCurrentIndex(0)
                for w, v in zip((self.dir_x, self.dir_y, self.dir_z), t["direction"]):
                    self._set(w, v)
                self._set(self.length, t["length"] if t["length"] else 10.0)
                self._set(self.through, t["through"])
                self._set(self.symmetric, t["symmetric"])
            elif t["kind"] == "solid":
                self.tool_kind.setCurrentIndex(1)
            else:
                self.fixed_tool = t
                self.tool_kind.addItem(translate("Assembly", "Box or cylinder (set by script)"), TOOL_FIXED)
                self.tool_kind.setCurrentIndex(2)
            self._sync_tool_widgets()
        else:
            for w, v in zip(self.pos, spec["position"]):
                self._set(w, v)
            for w, v in zip(self.axis, spec["axis"]):
                self._set(w, v)
            self._set(self.diameter, spec["diameter"])
            self._set(self.depth, spec["depth"] if spec["depth"] else 10.0)
            self._set(self.through, spec["through"])
            self.depth.setEnabled(not spec["through"])
            has_cb = bool(spec["cbore_diameter"])
            self._set(self.cbore, has_cb)
            if has_cb:
                self._set(self.cbore_diameter, spec["cbore_diameter"])
                self._set(self.cbore_depth, spec["cbore_depth"])
            self.cbore_diameter.setEnabled(has_cb)
            self.cbore_depth.setEnabled(has_cb)
        scope = list(feat.Scope)
        if scope:
            keys = [i.key for i in self.insts if any(I._matches(i, s) for s in scope)]
            self._explicit_touched = True
            self.scope_some.blockSignals(True)
            self.scope_some.setChecked(True)
            self.scope_some.blockSignals(False)
            self.components.setEnabled(True)
            self._check_keys(keys)

    def _default_direction(self):
        """Into the part from the profile: against the profile's plane normal."""
        if self.tool_obj is None:
            return
        try:
            prof = AF._profile_shape(self.assembly, self.tool_obj)
            face = Part.makeFace(prof.Wires, "Part::FaceMakerBullseye") if prof.Wires else prof.Faces[0]
            n = face.normalAt(0, 0)
        except Exception:  # noqa: BLE001  (no plane: the user types the direction)
            return
        for w, v in zip((self.dir_x, self.dir_y, self.dir_z), (-n.x, -n.y, -n.z)):
            self._set(w, round(v, 6))

    def _face_to_fields(self, sel):
        """Position and axis of a hole from a selected face: the picked point (else the face centre), drilling into the face."""
        try:
            name = sel.SubElementNames[0]   # resolve mode 0: the path from the top object, so links and sub-assemblies place it
            face = Part.getShape(sel.Object, name, needSubElement=True, transform=True)
            pts = list(sel.PickedPoints) if sel.PickedPoints else []
            point = pts[0] if pts else face.CenterOfMass
            u, v = face.Surface.parameter(point) if hasattr(face.Surface, "parameter") else (0, 0)
            normal = face.normalAt(u, v)
        except Exception:  # noqa: BLE001
            return False
        inv = self.assembly.Placement.inverse()
        p = inv.multVec(point)
        n = inv.Rotation.multVec(normal)
        for w, val in zip(self.pos, (p.x, p.y, p.z)):
            self._set(w, round(val, 6))
        for w, val in zip(self.axis, (-n.x, -n.y, -n.z)):
            self._set(w, round(val, 6))
        return True

    def use_selected_face(self):
        for sel in Gui.Selection.getSelectionEx("*", 0):
            if sel.SubElementNames and _is_face(sel.SubElementNames[0]) and self._face_to_fields(sel):
                self._changed()
                return
        self._message(translate("Assembly", "Select a face of a part in the 3D view first."))

    # ---- tool object handling

    def _tool_kind_changed(self, *_):
        self._sync_tool_widgets()
        self._changed()

    def _tool_mode(self):
        return self.tool_kind.currentData()

    def _sync_tool_widgets(self):
        mode = self._tool_mode()
        extrude = mode == TOOL_EXTRUDE
        for w in self._extrude_widgets:
            w.setEnabled(extrude)
        self.length.setEnabled(extrude and not self.through.isChecked())
        self.pick_button.setEnabled(mode != TOOL_FIXED)
        if mode == TOOL_FIXED:
            self.tool_label.setText(translate("Assembly", "A box or cylinder tool made by script; only the components can be changed here."))
        elif self.tool_obj is None:
            self.tool_label.setText(translate("Assembly", "Nothing picked yet. Select a %s, then press the button.") % (
                translate("Assembly", "sketch or profile") if extrude else translate("Assembly", "solid")))
        else:
            self.tool_label.setText("%s (%s)" % (self.tool_obj.Label, self.tool_obj.TypeId))

    def use_selected_object(self):
        """Take the selected object as the profile or solid tool."""
        mode = self._tool_mode()
        ok = is_profile if mode == TOOL_EXTRUDE else has_solid
        for o in Gui.Selection.getSelection():
            if o is not self.assembly and o is not self.feature and ok(o):
                self.set_tool_object(o)
                return True
        self._message(translate("Assembly", "Select a sketch or profile first.") if mode == TOOL_EXTRUDE
                      else translate("Assembly", "Select a solid first."))
        return False

    def set_tool_object(self, obj):
        first = self.tool_obj is None
        self.tool_obj = obj
        if first and self._tool_mode() == TOOL_EXTRUDE:
            self._default_direction()
        self._sync_tool_widgets()
        self._changed()

    def reverse_direction(self):
        for w in (self.dir_x, self.dir_y, self.dir_z):
            self._set(w, -w.value() if w.value() else 0.0)
        self._changed()

    def _through_toggled(self, on):
        if self.kind == "cut":
            self.length.setEnabled(not on and self._tool_mode() == TOOL_EXTRUDE)
        else:
            self.depth.setEnabled(not on)
        self._changed()

    def _cbore_toggled(self, on):
        self.cbore_diameter.setEnabled(on)
        self.cbore_depth.setEnabled(on)
        self._changed()

    def _message(self, text):
        self.error.setText(text)
        self.error.setVisible(bool(text))

    # ---- reading the widgets

    def read_spec(self):
        """(spec dict, tool object, problem) from the widgets; spec is None when there is a problem."""
        try:
            if self.kind == "hole":
                return self._read_hole()
            return self._read_cut()
        except ValueError as exc:
            return None, None, str(exc)

    def _read_cut(self):
        mode = self._tool_mode()
        if mode == TOOL_FIXED:
            return {"tool": self.fixed_tool}, self.feature.ToolObject, None
        if self.tool_obj is None:
            what = translate("Assembly", "a sketch or profile") if mode == TOOL_EXTRUDE else translate("Assembly", "a solid")
            return None, None, translate("Assembly", "Pick %s for the tool.") % what
        if mode == TOOL_SOLID:
            if not has_solid(self.tool_obj):
                return None, None, translate("Assembly", "%s holds no solid.") % self.tool_obj.Label
            return {"tool": AF.solid_tool()}, self.tool_obj, None
        if not has_wires(self.tool_obj):
            return None, None, translate("Assembly", "%s holds no closed profile.") % self.tool_obj.Label
        d = (self.dir_x.value(), self.dir_y.value(), self.dir_z.value())
        if not any(d):
            return None, None, translate("Assembly", "The direction is zero.")
        through = self.through.isChecked()
        if not through and not self.length.value() > 0:
            return None, None, translate("Assembly", "Give a length or choose through all.")
        tool = AF.extrude_tool(d, None if through else self.length.value(), through, self.symmetric.isChecked())
        return {"tool": tool}, self.tool_obj, None

    def _read_hole(self):
        axis = [w.value() for w in self.axis]
        if not any(axis):
            return None, None, translate("Assembly", "The axis is zero.")
        through = self.through.isChecked()
        if not self.diameter.value() > 0:
            return None, None, translate("Assembly", "The diameter must be above zero.")
        if not through and not self.depth.value() > 0:
            return None, None, translate("Assembly", "Give a depth or choose through all.")
        cb = self.cbore.isChecked()
        if cb and not self.cbore_diameter.value() > self.diameter.value():
            return None, None, translate("Assembly", "The counterbore must be wider than the hole.")
        if cb and not self.cbore_depth.value() > 0:
            return None, None, translate("Assembly", "Give the counterbore a depth.")
        spec = {"position": [w.value() for w in self.pos], "axis": axis, "diameter": self.diameter.value(),
                "depth": None if through else self.depth.value(), "through": through,
                "cbore_diameter": self.cbore_diameter.value() if cb else None,
                "cbore_depth": self.cbore_depth.value() if cb else None}
        return spec, None, None

    # ---- applying to the feature and previewing

    def _item_changed(self, *_):
        if self._active:
            self._explicit_touched = True
            self._scope_dirty = True
        self._changed()

    def _changed(self, *_):
        if self._active:
            self.refresh()

    def _write(self, spec, tool_obj, scope):
        """Create the feature on first valid input, else update the properties that differ."""
        feat = self.feature
        if feat is None:
            if self.kind == "cut":
                feat = AF.make_cut(self.assembly, spec["tool"], scope=scope, tool_object=tool_obj)
            else:
                feat = AF.make_hole(self.assembly, spec["position"], spec["axis"], spec["diameter"],
                                    depth=spec["depth"], through=spec["through"],
                                    cbore_diameter=spec["cbore_diameter"], cbore_depth=spec["cbore_depth"],
                                    scope=scope)
            self.feature = feat
            return feat
        text = json.dumps(spec, sort_keys=True)
        if feat.Spec != text:
            feat.Spec = text
        if feat.ToolObject is not tool_obj and tool_obj is not None:
            feat.ToolObject = tool_obj
        if list(feat.Scope) != list(scope):
            feat.Scope = list(scope)
        feat.Proxy.results = None
        return feat

    def refresh(self):
        """Read the widgets, update the feature, recompute what it cuts and redraw the preview."""
        self._message("")
        spec, tool_obj, problem = self.read_spec()
        scope = self._scope_value()
        if problem is None and scope is None:
            problem = translate("Assembly", "No component is checked, so the feature would cut nothing.")
        self.problem = problem
        self._cur = (spec, tool_obj, scope) if problem is None else None
        self.rows, self.errors = [], []
        # components may have moved since the panel opened: look at them again on every refresh
        self.insts = I.resolve(self.assembly, include_hidden=True, apply_features=False).insts
        self.labels = {i.key: part_text(i.describe(self.assembly)) for i in self.insts}
        items = []
        if problem is None:
            try:
                self._write(spec, tool_obj, scope)
                self.feature.Proxy.results = None
                aff = AF.affected(self.feature)
                self.errors = [e.get("error", str(e)) for e in aff["errors"]]
                self.rows = [(c["path"], c["removed_mm3"]) for c in aff["components"]]
                if self.scope_all.isChecked():
                    self.reached = [c["path"] for c in aff["components"]]
                    self._check_keys(self.reached)
                items = self._preview_items(aff)
            except Exception as exc:  # noqa: BLE001  (a panel never raises into the Qt event loop)
                self.problem = "%s: %s" % (type(exc).__name__, exc)
        self._fill()
        try:
            self.preview.show_cuts(items)
        except Exception as exc:  # noqa: BLE001
            self.preview.clear()
            self.errors.append(translate("Assembly", "Could not draw the preview: %s") % exc)
            self._fill()

    def _preview_items(self, aff):
        tool = None
        try:
            tool = AF._build_tool(self.assembly, self.feature, [i.box for i in self.insts])
        except Exception:  # noqa: BLE001  (the cut report already carries the tool error)
            pass
        by_key = {i.key: i for i in self.insts}
        items = []
        for c in aff["components"]:
            inst = by_key.get(c["path"])
            cut = AF.effective_shape(self.assembly, c["path"])
            removed = None
            if tool is not None and inst is not None:
                try:
                    removed = inst.shape.common(tool)
                except Exception:  # noqa: BLE001
                    removed = None
            items.append((cut, removed))
        return items

    def _fill(self):
        t = self.table
        t.setRowCount(len(self.rows))
        for i, (path, vol) in enumerate(self.rows):
            a = QtWidgets.QTableWidgetItem(self.labels.get(path, path))
            b = QtWidgets.QTableWidgetItem("%.3f" % vol)
            b.setTextAlignment(int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter))
            for it in (a, b):
                it.setFlags(QtCore.Qt.ItemIsEnabled)
            t.setItem(i, 0, a)
            t.setItem(i, 1, b)
        t.resizeColumnsToContents()
        if self.problem:
            self.summary.setText(self.problem)
        elif self.feature is not None and self.feature.Suppressed:
            self.summary.setText(translate("Assembly", "This feature is switched off, so it cuts nothing."))
        elif not self.rows:
            self.summary.setText(translate("Assembly", "The feature reaches no component."))
        else:
            total = sum(v for _, v in self.rows)
            self.summary.setText(translate("Assembly", "Cuts %d components, %.3f mm³ in all.") % (len(self.rows), total))
        self._message("; ".join(self.errors))

    # ---- task dialog protocol

    def getStandardButtons(self):
        std = QtWidgets.QDialogButtonBox.StandardButton
        ok, cancel = std.Ok, std.Cancel
        return int(getattr(ok, "value", ok)) | int(getattr(cancel, "value", cancel))

    def accept(self):
        if self.problem or self.feature is None or self._cur is None:
            self._message(self.problem or translate("Assembly", "Nothing to create yet."))
            return False
        spec, tool_obj, scope = self._cur
        self.preview.clear()
        # back to the starting state outside any transaction, then the real change as one undo step
        self._discard()
        self.discarded = False
        title = translate("Assembly", "Edit %s") % self.feature.Label if self.editing else translate(
            "Assembly", "Create %s") % translate("Assembly", TITLES[self.kind]).lower()
        self.doc.openTransaction(title)
        try:
            feat = self._write(spec, tool_obj, scope)
            try:
                self.doc.recompute()
            except Exception:  # noqa: BLE001
                pass
            if not self.editing and App.GuiUp and getattr(feat, "ViewObject", None) is not None:
                try:
                    import ViewProviderAssemblyFeature as VP
                    VP.attach(feat)
                except Exception:  # noqa: BLE001  (the tree icon is cosmetic)
                    pass
        except Exception:
            self.doc.abortTransaction()
            raise
        self.doc.commitTransaction()
        self.discarded = True
        self.done = True
        return True

    def reject(self):
        self.preview.clear()
        self._discard()
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
