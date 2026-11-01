# SPDX-License-Identifier: LGPL-2.1-or-later
"""Task panels of the corner relief, lightening cutout, lofted bend and flat DXF export (cad-fork G07 slice 6b).

Imported only with the GUI up. A panel only collects parameters and calls the same API functions the tests and
scripts call (``make_corner_relief``, ``make_lightening_cutout``, ``make_lofted_bend``, ``export_flat_dxf``); the
geometry builders (``build_corner_relief``, ``build_cutout``, ``analyse`` and ``make_all``) are called once before
the document is touched, as a dry run, so a refusal is caught with the document and its undo stack untouched.

* OK runs the API call in one transaction. When the dry run refuses, or the recompute leaves the new or edited
  feature (or any feature that was fine before) invalid, nothing of the attempt remains, the reason is shown as
  text in the panel and the panel stays open. Nothing is printed as a Python traceback.
* Cancel changes nothing. An edit panel (opened from a feature's view provider) is prefilled from the object and
  writes its properties only on OK. Closing the dialog from outside leaves edit mode too.
* ``values()`` reads the widgets, ``create()`` and ``edit()`` apply them; tests drive a panel by setting widgets
  and calling ``accept()`` or ``reject()`` exactly as the dialog buttons do.

Clean room: written from the FreeCAD Python task-panel conventions and the Weldment panels of this fork; the
implementer never opened the add-on's GPL files or UI files.
"""

import os

import FreeCAD
import FreeCADGui as Gui
from FreeCAD import Vector as V
from PySide import QtWidgets

import SheetMetalBendCornerRelief as CR
import SheetMetalLighteningCutout as LC
import SheetMetalLoftedBend as LB

#: panel the user has open (set by ``show``)
_CURRENT = []

#: refusals the builders raise; their text is what the panel shows
_REFUSALS = (CR.ReliefError, LC.CutoutError, LB.LoftedBendError)


class PanelError(Exception):
    """A refusal raised by the panel itself (nothing selected, wrong object kind, bad file)."""


def current():
    """The open SheetMetal shape panel, or None when its dialog has been closed."""
    if _CURRENT and Gui.Control.activeDialog():
        return _CURRENT[-1]
    return None


def show(panel):
    _CURRENT[:] = [panel]
    Gui.Control.showDialog(panel)
    return panel


# ---------------------------------------------------------------- document and selection helpers

def type_name(obj):
    return type(getattr(obj, "Proxy", None)).__name__


def is_unfold(obj):
    return type_name(obj) == "SMUnfold"


def bad_objects(doc):
    out = {}
    for o in doc.Objects:
        state = list(getattr(o, "State", []) or [])
        if "Invalid" in state or "Error" in state:
            out[o.Name] = o
    return out


def _status(o):
    try:
        text = o.getStatusString()
    except Exception:    # noqa: BLE001
        text = ""
    return text if text and text != "Valid" else "invalid after recompute"


def message_of(e):
    return str(e.args[0] if e.args else e) or type(e).__name__


def transact(doc, label, fn):
    """Run ``fn`` (returns the objects it created or changed) in one transaction and recompute.

    Returns ``(True, objects)`` when everything is valid. Otherwise the transaction is aborted and the result is
    ``(False, message)``: nothing of the attempt remains, not even on the undo stack."""
    before = set(bad_objects(doc))
    doc.openTransaction(label)
    try:
        objs = list(fn())
        doc.recompute()
        bad = bad_objects(doc)
        problems = ["%s: %s" % (o.Label, _status(o)) for o in objs if o.Name in bad]
        problems += ["%s: %s (broken by this change)" % (o.Label, _status(o)) for n, o in bad.items()
                     if n not in before and o not in objs]
        if not problems:
            doc.commitTransaction()
            return True, objs
        message = "\n".join(problems)
    except _REFUSALS + (PanelError, ValueError, TypeError, KeyError, RuntimeError, FreeCAD.Base.FreeCADError) as e:
        message = message_of(e)
    except Exception as e:    # noqa: BLE001  (a panel never raises into the Qt event loop)
        message = "%s: %s" % (type(e).__name__, e)
    doc.abortTransaction()
    return False, message


def selection_edge():
    """(object, 'EdgeN') of the first picked edge, else None."""
    for s in Gui.Selection.getSelectionEx():
        for n in s.SubElementNames:
            if n.startswith("Edge"):
                return s.Object, n
    return None


def selection_face():
    """(object, 'FaceN') of the first picked face, else None."""
    for s in Gui.Selection.getSelectionEx():
        for n in s.SubElementNames:
            if n.startswith("Face"):
                return s.Object, n
    return None


def is_profile(obj):
    """An object that can serve as a profile: it has a shape with edges and no solid."""
    shape = getattr(obj, "Shape", None)
    return shape is not None and not shape.isNull() and bool(shape.Edges) and not shape.Solids


def selection_profiles(exclude=None):
    """Whole selected objects that can serve as profiles, in selection order."""
    out = []
    for s in Gui.Selection.getSelectionEx():
        if s.Object is not exclude and is_profile(s.Object) and s.Object not in out:
            out.append(s.Object)
    return out


def depends_on(obj, other):
    """True when ``obj`` is ``other`` or depends on it (linking it would close a cycle)."""
    return obj is other or other in obj.OutListRecursive


def label_of(obj):
    return obj.Label if obj is not None else ""


def sub_text(obj, sub):
    return "%s: %s" % (obj.Label, sub) if obj is not None and sub else label_of(obj)


# ---------------------------------------------------------------- widgets

def _spin(lo, hi, value, decimals=4, suffix=""):
    w = QtWidgets.QDoubleSpinBox()
    w.setDecimals(decimals)
    w.setRange(lo, hi)
    w.setValue(value)
    if suffix:
        w.setSuffix(suffix)
    w.setKeyboardTracking(False)
    return w


def _ispin(lo, hi, value):
    w = QtWidgets.QSpinBox()
    w.setRange(lo, hi)
    w.setValue(value)
    w.setKeyboardTracking(False)
    return w


def _combo(items, current=None):
    w = QtWidgets.QComboBox()
    for it in items:
        if isinstance(it, tuple):
            w.addItem(it[1], it[0])
        else:
            w.addItem(it, it)
    if current is not None:
        w.setCurrentIndex(max(0, w.findData(current)))
    return w


def _label(text=""):
    w = QtWidgets.QLabel(text)
    w.setWordWrap(True)
    return w


def _set_combo(w, data):
    i = w.findData(data)
    if i >= 0:
        w.setCurrentIndex(i)


# ---------------------------------------------------------------- base panel

class FeaturePanel:
    """A task dialog that creates one feature (``obj`` None) or edits one."""

    title = ""
    transaction = ""
    vp_attach = True

    def __init__(self, doc=None, obj=None):
        self.obj = obj
        self.doc = doc if doc is not None else (obj.Document if obj is not None else FreeCAD.ActiveDocument)
        if self.doc is None:
            raise PanelError("open a document first")
        self.done = False
        self.form = QtWidgets.QWidget()
        self.form.setObjectName("SheetMetal_" + type(self).__name__)
        self.form.setWindowTitle(self.title + (" (edit)" if obj is not None else ""))
        outer = QtWidgets.QVBoxLayout(self.form)
        self.fields = QtWidgets.QFormLayout()
        self.fields.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        outer.addLayout(self.fields)
        self.error = _label()
        self.error.setObjectName("error")
        self.error.setStyleSheet("color: #b00020;")
        self.error.hide()
        outer.addWidget(self.error)
        outer.addStretch(1)
        self.build()
        if obj is not None:
            self.load(obj)
        # closing the dialog with Gui.Control.closeDialog() calls neither accept nor reject: leave edit mode
        # when the form is destroyed without either having run
        self.form.destroyed.connect(self._form_destroyed)

    # subclass hooks
    def build(self):
        raise NotImplementedError

    def load(self, obj):
        raise NotImplementedError

    def values(self):
        raise NotImplementedError

    def preflight(self, v):
        """Dry run of the geometry before the document is touched; raises a refusal."""

    def create(self, doc, v):
        raise NotImplementedError

    def edit(self, obj, v):
        raise NotImplementedError

    # messages
    def show_error(self, text):
        try:
            self.error.setText(text)
            self.error.setVisible(bool(text))
        except RuntimeError:    # the dialog is already closed: nothing to show it in
            pass

    def error_text(self):
        try:
            return self.error.text()
        except RuntimeError:
            return ""

    # dialog buttons
    def alive(self):
        """False (with a message in the panel) when the edited feature or the document is gone."""
        try:
            self.doc.Name
            if self.obj is not None:
                self.obj.Name
                if self.obj.Document is None:
                    raise ReferenceError
        except (ReferenceError, RuntimeError, AttributeError):
            self.show_error("The feature or its document no longer exists: close this panel.")
            return False
        return True

    def accept(self):
        self.show_error("")
        if not self.alive():
            return False
        try:
            v = self.values()
            self.preflight(v)
        except _REFUSALS + (PanelError, ValueError, TypeError, KeyError, RuntimeError, FreeCAD.Base.FreeCADError) as e:
            self.show_error(message_of(e))
            return False
        except Exception as e:    # noqa: BLE001
            self.show_error("%s: %s" % (type(e).__name__, e))
            return False
        if self.obj is None:
            ok, res = transact(self.doc, self.transaction, lambda: self.create(self.doc, v))
        else:
            ok, res = transact(self.doc, "Edit " + self.transaction.lower(), lambda: self.edit(self.obj, v))
        if not ok:
            self.show_error(res)
            return False
        self.done = True
        self._leave_edit()
        return True

    def reject(self):
        self.done = True
        self._leave_edit()
        return True

    def cancelled(self):
        """The dialog was closed from outside (edit mode left): nothing was applied, nothing to undo."""
        self.done = True

    def _form_destroyed(self, *_args):
        if not self.done:
            self.done = True
            self._leave_edit()

    def _leave_edit(self):
        if self.obj is None:
            return
        try:
            gd = Gui.getDocument(self.doc.Name)
            if gd.getInEdit() is not None:
                gd.resetEdit()
        except Exception:    # noqa: BLE001
            pass

    def clicked(self, button):
        return None

    def isAllowedAlterDocument(self):
        return True

    def isAllowedAlterView(self):
        return True

    def isAllowedAlterSelection(self):
        return True

    def attach_view_provider(self, obj):
        import SMShapeViewProviders

        SMShapeViewProviders.attach(obj)


# ---------------------------------------------------------------- corner relief

class CornerReliefPanel(FeaturePanel):
    title = "Corner relief"
    transaction = "Corner relief"

    def __init__(self, doc=None, obj=None, base=None, edge=None):
        self.base, self.edge = base, edge
        self._target_changed = False
        super().__init__(doc, obj)

    def build(self):
        self.target = _label()
        self.target.setObjectName("target")
        self.use_sel = QtWidgets.QPushButton("Use selection")
        self.use_sel.setObjectName("use_selection")
        self.kind = _combo(CR.RELIEF_TYPES, "Round")
        self.size = _spin(0.0, 10000.0, 3.0)
        self.size_mode = _combo(CR.SIZE_MODES, "Absolute")
        self.ratio = _spin(0.0, 1000.0, 2.0)
        self.rotation = _spin(-360.0, 360.0, 0.0, 3, " deg")
        self.from_part = QtWidgets.QCheckBox("Take the K-factor from the bends of the part")
        self.from_part.setChecked(True)
        self.kfactor = _spin(0.0, 1.0, 0.4)
        self.fields.addRow("Sheet and corner edge", self.target)
        self.fields.addRow("", self.use_sel)
        self.fields.addRow("Shape", self.kind)
        self.fields.addRow("Size (mm, or multiples of T)", self.size)
        self.fields.addRow("Size is", self.size_mode)
        self.fields.addRow("Obround length / width", self.ratio)
        self.fields.addRow("Rotation in the flat pattern", self.rotation)
        self.fields.addRow("", self.from_part)
        self.fields.addRow("K-factor (ANSI)", self.kfactor)
        self.kind.currentIndexChanged.connect(lambda _i: self.refresh())
        self.from_part.toggled.connect(lambda _b: self.refresh())
        self.use_sel.clicked.connect(self.take_selection)
        self.refresh()

    def refresh(self):
        self.ratio.setEnabled(self.kind.currentData() == "Obround")
        self.kfactor.setEnabled(not self.from_part.isChecked())
        if self.base is None:
            self.target.setText("(nothing selected: pick the sheet-metal solid and a corner edge, then press Use selection)")
        else:
            self.target.setText(sub_text(self.base, self.edge))

    def take_selection(self):
        picked = selection_edge()
        if picked is None:
            self.show_error("Select the corner edge on the sheet-metal solid first.")
            return
        self.set_target(*picked)

    def set_target(self, base, edge):
        self.base, self.edge = base, edge
        self._target_changed = True
        self.show_error("")
        self.refresh()

    def load(self, obj):
        self.base, self.edge = obj.baseObject[0], (obj.baseObject[1] or [""])[0]
        _set_combo(self.kind, obj.reliefType)
        self.size.setValue(obj.size)
        _set_combo(self.size_mode, obj.sizeMode)
        self.ratio.setValue(obj.ratio)
        self.rotation.setValue(obj.rotation.Value)
        self.from_part.setChecked(bool(obj.kfromPart))
        self.kfactor.setValue(obj.kfactor)
        self.refresh()

    def values(self):
        if self.base is None or not self.edge:
            raise PanelError("select the sheet-metal solid and its corner edge, then press Use selection")
        if self.obj is not None and depends_on(self.base, self.obj):
            raise PanelError("the corner relief cannot be built on itself or on a feature that follows it")
        return {"base": self.base, "edge": self.edge, "reliefType": self.kind.currentData(), "size": self.size.value(),
                "sizeMode": self.size_mode.currentData(), "ratio": self.ratio.value(),
                "rotation": self.rotation.value(), "kfromPart": self.from_part.isChecked(),
                "kfactor": self.kfactor.value()}

    def preflight(self, v):
        k = CR.part_kfactor(v["base"]) if v["kfromPart"] else None
        if k is None:
            k = v["kfactor"]
        CR.build_corner_relief(v["base"].Shape.copy(), v["edge"], v["reliefType"], v["size"], v["sizeMode"], k,
                               v["ratio"], v["rotation"], {})

    def props(self, v):
        return {"reliefType": v["reliefType"], "size": v["size"], "sizeMode": v["sizeMode"], "ratio": v["ratio"],
                "rotation": v["rotation"], "kfactor": v["kfactor"], "kfromPart": v["kfromPart"]}

    def create(self, doc, v):
        obj = CR.make_corner_relief(doc, v["base"], v["edge"], **self.props(v))
        self.attach_view_provider(obj)
        if obj.ViewObject is not None and v["base"].ViewObject is not None:
            v["base"].ViewObject.Visibility = False
        return [obj]

    def edit(self, obj, v):
        for key, value in self.props(v).items():
            setattr(obj, key, value)
        if self._target_changed:
            obj.baseObject = (v["base"], [v["edge"]])
        return [obj]


# ---------------------------------------------------------------- lightening cutout

class LighteningCutoutPanel(FeaturePanel):
    title = "Lightening cutout"
    transaction = "Lightening cutout"

    def __init__(self, doc=None, obj=None, base=None, face=None, sketch=None):
        self.base, self.face, self.sketch = base, face, sketch
        self._target_changed = False
        super().__init__(doc, obj)

    def build(self):
        self.target = _label()
        self.target.setObjectName("target")
        self.profile = _label()
        self.profile.setObjectName("profile")
        self.use_sel = QtWidgets.QPushButton("Use selection")
        self.use_sel.setObjectName("use_selection")
        self.pattern = _combo(["None", "Linear", "Grid"], "None")
        self.count_x = _ispin(1, 1000, 1)
        self.count_y = _ispin(1, 1000, 1)
        self.pitch_x = _spin(0.0, 100000.0, 10.0, 4, " mm")
        self.pitch_y = _spin(0.0, 100000.0, 10.0, 4, " mm")
        self.dir_x = _spin(-1000.0, 1000.0, 1.0)
        self.dir_y = _spin(-1000.0, 1000.0, 0.0)
        self.dir_z = _spin(-1000.0, 1000.0, 0.0)
        self.min_edge = _spin(0.0, 100000.0, 0.0, 4, " mm")
        self.fields.addRow("Sheet face", self.target)
        self.fields.addRow("Profile sketch", self.profile)
        self.fields.addRow("", self.use_sel)
        self.fields.addRow("Pattern", self.pattern)
        self.fields.addRow("Copies along", self.count_x)
        self.fields.addRow("Copies across (grid)", self.count_y)
        self.fields.addRow("Pitch along", self.pitch_x)
        self.fields.addRow("Pitch across (grid)", self.pitch_y)
        self.fields.addRow("Direction x", self.dir_x)
        self.fields.addRow("Direction y", self.dir_y)
        self.fields.addRow("Direction z", self.dir_z)
        self.fields.addRow("Least distance from the face boundary", self.min_edge)
        self.pattern.currentIndexChanged.connect(lambda _i: self.refresh())
        self.use_sel.clicked.connect(self.take_selection)
        self.refresh()

    def refresh(self):
        p = self.pattern.currentData()
        for w in (self.count_x, self.pitch_x, self.dir_x, self.dir_y, self.dir_z):
            w.setEnabled(p != "None")
        for w in (self.count_y, self.pitch_y):
            w.setEnabled(p == "Grid")
        self.target.setText(sub_text(self.base, self.face) if self.base is not None else
                            "(nothing selected: pick a planar face of the sheet, then press Use selection)")
        self.profile.setText(label_of(self.sketch) if self.sketch is not None else
                             "(nothing selected: pick the closed profile sketch, then press Use selection)")

    def take_selection(self):
        face = selection_face()
        profiles = selection_profiles(exclude=face[0] if face else None)
        if face is None and not profiles:
            self.show_error("Select a planar face of the sheet and the profile sketch first.")
            return
        self.set_targets(face, profiles[0] if profiles else None)

    def set_targets(self, face=None, sketch=None):
        if face is not None:
            self.base, self.face = face
        if sketch is not None:
            self.sketch = sketch
        self._target_changed = True
        self.show_error("")
        self.refresh()

    def load(self, obj):
        self.base, self.face = obj.baseObject[0], (obj.baseObject[1] or [""])[0]
        self.sketch = obj.Sketch
        _set_combo(self.pattern, obj.Pattern)
        self.count_x.setValue(obj.CountX)
        self.count_y.setValue(obj.CountY)
        self.pitch_x.setValue(obj.PitchX.Value)
        self.pitch_y.setValue(obj.PitchY.Value)
        d = obj.Direction
        self.dir_x.setValue(d.x)
        self.dir_y.setValue(d.y)
        self.dir_z.setValue(d.z)
        self.min_edge.setValue(obj.MinEdgeDistance.Value)
        self.refresh()

    def values(self):
        if self.base is None or not self.face:
            raise PanelError("select a planar face of the sheet, then press Use selection")
        if self.sketch is None:
            raise PanelError("select the closed profile sketch, then press Use selection")
        if self.obj is not None and (depends_on(self.base, self.obj) or depends_on(self.sketch, self.obj)):
            raise PanelError("the cutout cannot be built on itself or on a feature that follows it")
        return {"base": self.base, "face": self.face, "sketch": self.sketch, "Pattern": self.pattern.currentData(),
                "CountX": self.count_x.value(), "CountY": self.count_y.value(), "PitchX": self.pitch_x.value(),
                "PitchY": self.pitch_y.value(),
                "Direction": V(self.dir_x.value(), self.dir_y.value(), self.dir_z.value()),
                "MinEdgeDistance": self.min_edge.value()}

    def preflight(self, v):
        LC.build_cutout(v["base"].Shape.copy(), v["face"], v["sketch"].Shape, v["Pattern"], v["CountX"], v["CountY"],
                        v["PitchX"], v["PitchY"], V(v["Direction"]), v["MinEdgeDistance"])

    PROPS = ("Pattern", "CountX", "CountY", "PitchX", "PitchY", "Direction", "MinEdgeDistance")

    def create(self, doc, v):
        obj = LC.make_lightening_cutout(doc, v["base"], v["face"], v["sketch"], **{k: v[k] for k in self.PROPS})
        self.attach_view_provider(obj)
        return [obj]

    def edit(self, obj, v):
        for k in self.PROPS:
            setattr(obj, k, v[k])
        if self._target_changed:
            obj.baseObject = (v["base"], [v["face"]])
            obj.Sketch = v["sketch"]
        return [obj]


# ---------------------------------------------------------------- lofted bend

class LoftedBendPanel(FeaturePanel):
    title = "Lofted bend"
    transaction = "Lofted bend"

    def __init__(self, doc=None, obj=None, profile_a=None, profile_b=None):
        self.profile_a, self.profile_b = profile_a, profile_b
        self._target_changed = False
        super().__init__(doc, obj)

    def build(self):
        self.a_label = _label()
        self.a_label.setObjectName("profile_a")
        self.b_label = _label()
        self.b_label.setObjectName("profile_b")
        self.use_sel = QtWidgets.QPushButton("Use selection (A then B)")
        self.use_sel.setObjectName("use_selection")
        self.thickness = _spin(0.0, 1000.0, 2.0, 4, " mm")
        self.side = _combo(LB.SIDES, "mid")
        self.facets = _ispin(0, 256, 4)
        self.alignment = _combo(LB.ALIGNMENTS, "auto")
        self.method = _combo(LB.METHODS, "faceted")
        self.invert = QtWidgets.QCheckBox("Swap inside and outside")
        self.fields.addRow("Profile A", self.a_label)
        self.fields.addRow("Profile B", self.b_label)
        self.fields.addRow("", self.use_sel)
        self.fields.addRow("Thickness", self.thickness)
        self.fields.addRow("Thickness goes", self.side)
        self.fields.addRow("Facets per curved segment", self.facets)
        self.fields.addRow("Profile B runs", self.alignment)
        self.fields.addRow("Method", self.method)
        self.fields.addRow("", self.invert)
        self.use_sel.clicked.connect(self.take_selection)
        self.refresh()

    def refresh(self):
        none = "(nothing selected: pick two open profiles on parallel planes, then press Use selection)"
        self.a_label.setText(label_of(self.profile_a) if self.profile_a is not None else none)
        self.b_label.setText(label_of(self.profile_b) if self.profile_b is not None else none)

    def take_selection(self):
        profiles = selection_profiles()
        if len(profiles) < 2:
            self.show_error("Select the two profiles (sketches or wires) first.")
            return
        self.set_profiles(profiles[0], profiles[1])

    def set_profiles(self, a, b):
        self.profile_a, self.profile_b = a, b
        self._target_changed = True
        self.show_error("")
        self.refresh()

    def load(self, obj):
        self.profile_a, self.profile_b = obj.profileA, obj.profileB
        self.thickness.setValue(obj.thickness.Value)
        _set_combo(self.side, obj.side)
        self.facets.setValue(obj.facets)
        _set_combo(self.alignment, obj.alignment)
        _set_combo(self.method, obj.method)
        self.invert.setChecked(bool(obj.invertSides))
        self.refresh()

    def values(self):
        if self.profile_a is None or self.profile_b is None:
            raise PanelError("select the two profiles, then press Use selection")
        if self.profile_a is self.profile_b:
            raise PanelError("profile A and profile B must be two different objects")
        if self.obj is not None and (depends_on(self.profile_a, self.obj) or depends_on(self.profile_b, self.obj)):
            raise PanelError("the lofted bend cannot be built on itself or on a feature that follows it")
        return {"a": self.profile_a, "b": self.profile_b, "thickness": self.thickness.value(),
                "side": self.side.currentData(), "facets": self.facets.value(),
                "alignment": self.alignment.currentData(), "method": self.method.currentData(),
                "invertSides": self.invert.isChecked()}

    def preflight(self, v):
        mesh = LB.analyse(v["a"].Shape, v["b"].Shape, v["thickness"], v["side"], v["facets"], v["alignment"],
                          v["invertSides"], v["method"], LB._sketch_normal(v["a"]), LB._sketch_normal(v["b"]))
        LB.make_all(mesh)

    PROPS = ("thickness", "side", "facets", "alignment", "method", "invertSides")

    def create(self, doc, v):
        obj = LB.make_lofted_bend(doc, v["a"], v["b"], **{k: v[k] for k in self.PROPS})
        self.attach_view_provider(obj)
        for p in (v["a"], v["b"]):
            if p.ViewObject is not None:
                p.ViewObject.Visibility = False
        return [obj]

    def edit(self, obj, v):
        for k in self.PROPS:
            setattr(obj, k, v[k])
        if self._target_changed:
            obj.profileA, obj.profileB = v["a"], v["b"]
        return [obj]


# ---------------------------------------------------------------- flat DXF export

class ExportDxfPanel:
    """Export the flat pattern of an Unfold feature to DXF through ``SheetMetalUnfoldCmd.export_flat_dxf``.

    OK writes the file. A refusal (not an Unfold feature, no V1 bend data, a pattern that cannot be exported, an
    unwritable path, a bad text height) is shown as text and the panel stays open. The file dialog of the Browse
    button is ``ask_path``; a test sets the path field instead, or overrides ``ask_path``."""

    title = "Export flat pattern to DXF"

    def __init__(self, doc=None, obj=None, path=None):
        self.obj = obj
        self.doc = doc if doc is not None else (obj.Document if obj is not None else FreeCAD.ActiveDocument)
        if self.doc is None:
            raise PanelError("open a document first")
        self.done = False
        self.written = None
        self.form = QtWidgets.QWidget()
        self.form.setObjectName("SheetMetal_ExportDxfPanel")
        self.form.setWindowTitle(self.title)
        outer = QtWidgets.QVBoxLayout(self.form)
        fields = QtWidgets.QFormLayout()
        fields.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        outer.addLayout(fields)
        self.target = _label(obj.Label if obj is not None else "(select an Unfold feature)")
        self.target.setObjectName("target")
        self.notes = QtWidgets.QCheckBox("Write a note with the direction, angle, radius and allowance beside each bend line")
        self.notes.setChecked(True)
        self.text_height = _spin(0.0, 1000.0, 2.5, 3, " mm")
        self.path = QtWidgets.QLineEdit(path if path is not None else self.default_path())
        self.path.setObjectName("path")
        self.browse = QtWidgets.QPushButton("Browse...")
        self.browse.setObjectName("browse")
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.path, 1)
        row.addWidget(self.browse)
        fields.addRow("Unfold feature", self.target)
        fields.addRow("", self.notes)
        fields.addRow("Note text height", self.text_height)
        fields.addRow("File", row)
        self.error = _label()
        self.error.setObjectName("error")
        self.error.setStyleSheet("color: #b00020;")
        self.error.hide()
        outer.addWidget(self.error)
        outer.addStretch(1)
        self.browse.clicked.connect(self.browse_clicked)

    def default_path(self):
        if self.obj is None:
            return ""
        folder = os.path.dirname(self.doc.FileName) if getattr(self.doc, "FileName", "") else os.path.expanduser("~")
        name = "".join(c if c.isalnum() or c in "-_" else "_" for c in self.obj.Label) or "flat"
        return os.path.join(folder, name + ".dxf")

    def ask_path(self):
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(self.form, "Export flat pattern", self.path.text(),
                                                              "DXF (*.dxf)")
        return path

    def browse_clicked(self):
        path = self.ask_path()
        if path:
            self.path.setText(path)

    def show_error(self, text):
        self.error.setText(text)
        self.error.setVisible(bool(text))

    def error_text(self):
        return self.error.text()

    def accept(self):
        import SheetMetalUnfoldCmd

        self.show_error("")
        try:
            obj = self.obj
            if obj is None or not is_unfold(obj):
                raise PanelError("%s is not an Unfold feature: select the Unfold feature to export"
                                 % (obj.Label if obj is not None else "nothing"))
            path = self.path.text().strip()
            if not path:
                raise PanelError("choose a file name for the DXF")
            SheetMetalUnfoldCmd.export_flat_dxf(obj, path, text_height=self.text_height.value(),
                                                notes=self.notes.isChecked())
            self.written = path
        except OSError as e:
            self.show_error("could not write %s: %s" % (self.path.text(), e.strerror or e))
            return False
        except (PanelError, ValueError, TypeError, KeyError, RuntimeError, ReferenceError,
                FreeCAD.Base.FreeCADError) as e:
            self.show_error(message_of(e))
            return False
        except Exception as e:    # noqa: BLE001
            self.show_error("%s: %s" % (type(e).__name__, e))
            return False
        self.done = True
        return True

    def reject(self):
        self.done = True
        return True

    def cancelled(self):
        self.done = True

    def clicked(self, button):
        return None

    def isAllowedAlterDocument(self):
        return True

    def isAllowedAlterView(self):
        return True

    def isAllowedAlterSelection(self):
        return True


PANELS = {"relief": CornerReliefPanel, "cutout": LighteningCutoutPanel, "lofted": LoftedBendPanel,
          "dxf": ExportDxfPanel}
