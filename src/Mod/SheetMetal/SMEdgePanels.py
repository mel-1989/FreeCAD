# SPDX-License-Identifier: LGPL-2.1-or-later
"""Task panels of the SheetMetal edge flange, hem, convert-from-solid and jog commands (cad-fork G07 slice 6b, lane A).

Imported only with the GUI up. A panel collects parameters and calls the same API the tests and scripts call
(``make_edge_flange``, ``make_hem``, ``make_convert_solid``, ``make_jog`` and the ``build_*`` functions for
dry runs); it holds no geometry logic.

* OK first dry-runs the build with the panel's values, on a copy of the shapes, so a refusal (a curved edge, an
  open hem gap of 0, a curved face, a jog offset too small) is caught before the document is touched. The reason
  is shown as text in the panel, the panel stays open and the undo stack is as it was. Nothing reaches the report
  view as a Python traceback.
* A passing dry run is followed by one transaction (``transact``): create the feature or write the edited
  properties, recompute, and abort the whole transaction when the new feature, or any feature that was fine
  before, is invalid afterwards.
* Cancel changes nothing. An edit panel (opened from a feature's view provider) is prefilled from the object and
  writes its properties only on OK. Closing the dialog from outside leaves edit mode too.
* ``values()`` reads the widgets, ``create()`` and ``edit()`` apply them; tests drive a panel by setting widgets
  and calling ``accept()`` or ``reject()`` exactly as the dialog buttons do.

Clean room: written from the FreeCAD Python task-panel conventions and the Weldment panels of this fork; the
add-on's GPL sources and UI files were not read. UI text is original.
"""

import FreeCAD
import FreeCADGui as Gui
from PySide import QtWidgets

import SheetMetalConvertSolid as CS
import SheetMetalEdgeFlange as EF
import SheetMetalHemFeature as HM
import SheetMetalJog as JG
import SheetMetalTools
import SMPanelGuard


class PanelError(Exception):
    """The panel cannot build what the widgets ask for (shown as text in the panel)."""


#: panel the user has open (set by ``show``)
_CURRENT = []


def current():
    """The open SheetMetal panel, or None when its dialog has been closed."""
    if _CURRENT and Gui.Control.activeDialog():
        return _CURRENT[-1]
    return None


def show(panel):
    _CURRENT[:] = [panel]
    Gui.Control.showDialog(panel)
    return panel


# ---------------------------------------------------------------- document and selection helpers

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


def _text(exc):
    return str(exc.args[0] if exc.args else exc) or type(exc).__name__


_FEATURES = ("SMEdgeFlange", "SMHem", "SMConvertSolid", "SMJog")


def _dry_run(doc, objs):
    """Execute, in Python and in dependency order, every edge flange, hem, convert or jog feature that is one of
    ``objs`` or depends on one. A refusal is an exception caught here; letting the recompute raise it instead would
    make FreeCAD print a Python traceback to the report view. Returns the list of problem lines."""
    targets = {o.Name for o in objs}
    for o in objs:
        targets.update(d.Name for d in o.InListRecursive)
    order, seen = [], set()

    def visit(o):
        if o.Name in seen:
            return
        seen.add(o.Name)
        for p in o.OutList:
            visit(p)
        order.append(o)

    for o in doc.Objects:
        visit(o)
    problems, failed = [], set()
    for o in order:
        if o.Name not in targets or type(getattr(o, "Proxy", None)).__name__ not in _FEATURES:
            continue
        if any(p.Name in failed for p in o.OutList):
            failed.add(o.Name)    # its input already failed; one reason is enough
            continue
        try:
            o.Proxy.execute(o)
        except Exception as e:    # noqa: BLE001
            failed.add(o.Name)
            text = _text(e)
            problems.append(text if o in objs else "%s: %s (broken by this change)" % (o.Label, text))
    return problems


def transact(doc, label, fn):
    """Run ``fn`` (returns the objects it created or changed) in one transaction and recompute.

    Returns ``(True, objects)`` when everything is valid. Otherwise the transaction is aborted and the result is
    ``(False, message)``: nothing of the attempt remains, not even on the undo stack."""
    before = set(bad_objects(doc))
    doc.openTransaction(label)
    try:
        objs = list(fn())
        problems = _dry_run(doc, objs)
        if not problems:
            doc.recompute()
            bad = bad_objects(doc)
            problems = ["%s: %s" % (o.Label, _status(o)) for o in objs if o.Name in bad]
            problems += ["%s: %s (broken by this change)" % (o.Label, _status(o)) for n, o in bad.items()
                         if n not in before and o not in objs]
        if not problems:
            doc.commitTransaction()
            return True, objs
        message = "\n".join(problems)
    except (EF.EdgeFlangeError, HM.HemError, JG.JogError, CS.ConvertSolidError, PanelError, ValueError, TypeError,
            KeyError, RuntimeError, FreeCAD.Base.FreeCADError) as e:
        message = _text(e)
    except Exception as e:    # noqa: BLE001  (a panel never raises into the Qt event loop)
        message = "%s: %s" % (type(e).__name__, e)
    doc.abortTransaction()
    return False, message


def selected(kind):
    """(object, [sub names starting with kind]) pairs of the selection, in selection order."""
    out = []
    for s in Gui.Selection.getSelectionEx():
        subs = [n for n in s.SubElementNames if n.startswith(kind)]
        if subs:
            out.append((s.Object, subs))
    return out


def has_sub(kind):
    return bool(selected(kind))


def sel_label(obj, names):
    if obj is None:
        return "(nothing selected)"
    return "%s: %s" % (obj.Label, ", ".join(names)) if names else obj.Label


def alive(obj):
    """False when the document object behind ``obj`` has been deleted."""
    if obj is None:
        return False
    try:
        doc = obj.Document
        return doc is not None and doc.getObject(obj.Name) is not None
    except Exception:    # noqa: BLE001  (a deleted object may raise ReferenceError)
        return False


# ---------------------------------------------------------------- widgets

def _spin(lo, hi, value, decimals=3, suffix=" mm"):
    w = QtWidgets.QDoubleSpinBox()
    w.setDecimals(decimals)
    w.setRange(lo, hi)
    w.setValue(value)
    w.setSuffix(suffix)
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


def _leave_edit(docname):
    try:
        gd = Gui.getDocument(docname)
        if gd.getInEdit() is not None:
            gd.resetEdit()
    except Exception:    # noqa: BLE001
        pass


# ---------------------------------------------------------------- base panel

class FeaturePanel:
    """A task dialog that creates one feature (``obj`` None) or edits one."""

    title = ""
    transaction = ""

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
        self.error.setStyleSheet("background-color: #b00020; color: white; padding: 2px 6px;")   # on its own background: 7:1 on any theme
        self.error.hide()
        outer.addWidget(self.error)
        outer.addStretch(1)
        self.build()
        if obj is not None:
            self.load(obj)
            # Gui.Control.closeDialog() deletes the form without calling reject(): leave edit mode then too
            name = self.doc.Name
            self.form.destroyed.connect(lambda *_a: _leave_edit(name))

    # subclass hooks
    def build(self):
        raise NotImplementedError

    def load(self, obj):
        raise NotImplementedError

    def values(self):
        raise NotImplementedError

    def dry_run(self, v):
        """Build the geometry with the panel's values, touching nothing; raises the API's error on a refusal."""
        raise NotImplementedError

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

    def _check_alive(self):
        if self.obj is not None and not alive(self.obj):
            raise PanelError("the feature this panel edits has been deleted; close the panel")
        gone = SMPanelGuard.deleted_target_message(self)
        if gone:
            raise PanelError(gone)

    def doc_closed(self):
        try:
            self.doc.Name
        except (ReferenceError, RuntimeError, AttributeError):
            return True
        return False

    # dialog buttons
    def accept(self):
        if self.doc_closed():    # the document was closed under the open dialog: nothing to apply, close quietly
            self.done = True
            return True
        self.show_error("")
        try:
            self._check_alive()
            v = self.values()
            self.dry_run(v)
        except (EF.EdgeFlangeError, HM.HemError, JG.JogError, CS.ConvertSolidError, PanelError, ValueError,
                FreeCAD.Base.FreeCADError) as e:
            self.show_error(_text(e))
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

    def _leave_edit(self):
        if self.obj is not None:
            _leave_edit(self.doc.Name)

    def clicked(self, button):
        return None

    def isAllowedAlterDocument(self):
        return True

    def isAllowedAlterView(self):
        return True

    def isAllowedAlterSelection(self):
        return True

    def attach(self, objs):
        import SMEdgeViewProviders

        if FreeCAD.GuiUp:
            for o in objs:
                SMEdgeViewProviders.attach(o)


# ---------------------------------------------------------------- edge-based panels (edge flange, hem)

class _EdgePanel(FeaturePanel):
    """Common part of the edge flange and hem panels: the base object and its edges come from the selection."""

    def __init__(self, doc=None, obj=None, base=None, edges=()):
        self.base, self.edges = base, list(edges)
        super().__init__(doc, obj)

    def build_selection(self):
        self.sel_label = _label()
        self.sel_label.setObjectName("selection")
        self.use_sel = QtWidgets.QPushButton("Use selected edges")
        self.use_sel.setObjectName("use_selection")
        self.use_sel.clicked.connect(self.take_selection)
        self.fields.addRow("Edges", self.sel_label)
        self.fields.addRow("", self.use_sel)
        self.refresh_selection()

    def refresh_selection(self):
        self.sel_label.setText(sel_label(self.base, self.edges) if self.edges else "(select the straight sheet edges, then press the button)")
        if self.obj is not None:
            self.use_sel.setEnabled(False)    # an existing feature keeps its base edges

    def take_selection(self):
        picks = selected("Edge")
        if not picks:
            self.show_error("select one or more edges of a sheet-metal part first")
            return False
        self.show_error("")
        self.base = picks[0][0]
        self.edges = [n for o, names in picks if o is self.base for n in names]
        self.refresh_selection()
        return True

    def _need_edges(self):
        if self.obj is not None:
            return
        if not alive(self.base) or not self.edges:
            raise PanelError("select the base part and its edges first (select edges, then press 'Use selected edges')")

    def load_edges(self, obj):
        self.base, self.edges = obj.baseObject[0], list(obj.baseObject[1])
        self.refresh_selection()

    def base_and_edges(self):
        if self.obj is not None:
            return self.obj.baseObject[0], list(self.obj.baseObject[1])
        return self.base, list(self.edges)


class EdgeFlangePanel(_EdgePanel):
    title = "Edge flange"
    transaction = "Edge flange"

    def build(self):
        self.build_selection()
        self.length = _spin(0.001, 100000.0, 10.0)
        self.angle = _spin(0.001, 179.999, 90.0, suffix=" deg")
        self.radius = _spin(0.001, 100000.0, 1.0)
        self.kfactor = _spin(0.0, 1.0, 0.4, 3, "")
        self.gap = _spin(0.0, 100000.0, 0.0)
        self.flip = QtWidgets.QCheckBox("Fold towards the other face")
        self.measure = _combo([("outer", "Outer mould line"), ("inner", "Inner mould line")], "outer")
        for label, w in (("Flange length", self.length), ("Bend angle", self.angle), ("Inside bend radius", self.radius),
                         ("K-factor", self.kfactor), ("Setback from edge ends", self.gap), ("Flip", self.flip),
                         ("Length measured to", self.measure)):
            self.fields.addRow(label, w)

    def load(self, obj):
        self.load_edges(obj)
        self.length.setValue(obj.length.Value)
        self.angle.setValue(obj.angle.Value)
        self.radius.setValue(obj.radius.Value)
        self.kfactor.setValue(float(obj.kfactor))
        self.gap.setValue(obj.gap.Value)
        self.flip.setChecked(bool(obj.flip))
        _set_combo(self.measure, str(obj.measure))

    def values(self):
        self._need_edges()
        return {"length": self.length.value(), "angle": self.angle.value(), "radius": self.radius.value(),
                "kfactor": self.kfactor.value(), "gap": self.gap.value(), "flip": self.flip.isChecked(),
                "measure": self.measure.currentData()}

    def dry_run(self, v):
        base, edges = self.base_and_edges()
        EF.build_edge_flange(base.Shape.copy(), edges, v["length"], v["angle"], v["radius"], v["kfactor"], v["gap"],
                             v["flip"], v["measure"])

    def create(self, doc, v):
        obj = EF.make_edge_flange(doc, self.base, self.edges, **v)
        self.attach([obj])
        return [obj]

    def edit(self, obj, v):
        for k, val in v.items():
            setattr(obj, k, val)
        return [obj]


class HemPanel(_EdgePanel):
    title = "Hem"
    transaction = "Hem"

    def build(self):
        self.build_selection()
        self.hem_type = _combo([("Closed", "Closed (flat)"), ("Open", "Open")], "Closed")
        self.gap = _spin(0.0, 100000.0, 2.0)
        self.length = _spin(0.001, 100000.0, 10.0)
        self.kfactor = _spin(0.0, 1.0, 0.4, 3, "")
        self.flip = QtWidgets.QCheckBox("Fold towards the other face")
        self.end_gap = _spin(0.0, 100000.0, 0.0)
        for label, w in (("Hem type", self.hem_type), ("Gap of an open hem", self.gap), ("Return leg length", self.length),
                         ("K-factor", self.kfactor), ("Flip", self.flip), ("Setback from edge ends", self.end_gap)):
            self.fields.addRow(label, w)
        self.hem_type.currentIndexChanged.connect(lambda _i: self.refresh())
        self.refresh()

    def refresh(self):
        self.gap.setEnabled(self.hem_type.currentData() == "Open")

    def load(self, obj):
        self.load_edges(obj)
        _set_combo(self.hem_type, str(obj.hemType))
        self.gap.setValue(obj.gap.Value)
        self.length.setValue(obj.length.Value)
        self.kfactor.setValue(float(obj.kfactor))
        self.flip.setChecked(bool(obj.flip))
        self.end_gap.setValue(obj.endGap.Value)
        self.refresh()

    def values(self):
        self._need_edges()
        return {"hemType": self.hem_type.currentData(), "gap": self.gap.value(), "length": self.length.value(),
                "kfactor": self.kfactor.value(), "flip": self.flip.isChecked(), "endGap": self.end_gap.value()}

    def dry_run(self, v):
        base, edges = self.base_and_edges()
        HM.build_hem(base.Shape.copy(), edges, v["hemType"], v["gap"], v["length"], v["kfactor"], v["flip"], v["endGap"])

    def create(self, doc, v):
        obj = HM.make_hem(doc, self.base, self.edges, **v)
        self.attach([obj])
        return [obj]

    def edit(self, obj, v):
        for k, val in v.items():
            setattr(obj, k, val)
        return [obj]


# ---------------------------------------------------------------- jog

class JogPanel(FeaturePanel):
    title = "Jog"
    transaction = "Jog"

    def __init__(self, doc=None, obj=None, base=None, face=None, sketch=None):
        self.base, self.face, self.sketch = base, face, sketch
        super().__init__(doc, obj)

    def build(self):
        self.face_label = _label()
        self.face_label.setObjectName("face")
        self.sketch_label = _label()
        self.sketch_label.setObjectName("sketch")
        self.use_sel = QtWidgets.QPushButton("Use selection (a face and a sketch)")
        self.use_sel.setObjectName("use_selection")
        self.use_sel.clicked.connect(self.take_selection)
        self.fields.addRow("Face", self.face_label)
        self.fields.addRow("Line sketch", self.sketch_label)
        self.fields.addRow("", self.use_sel)
        self.offset = _spin(0.001, 100000.0, 10.0)
        self.offset_type = _combo([(t, t.capitalize()) for t in JG.OFFSET_TYPES], "outside")
        self.angle = _spin(0.001, 90.0, 90.0, suffix=" deg")
        self.radius = _spin(0.0, 100000.0, 0.0)
        self.kfactor = _spin(0.0, 1.0, 0.4, 3, "")
        self.flip = QtWidgets.QCheckBox("Move against the face normal")
        self.fixed_side = _combo(list(JG.FIXED_SIDES), "Left")
        for label, w in (("Jog offset", self.offset), ("Offset convention", self.offset_type), ("Jog angle", self.angle),
                         ("Inside bend radius (0 = 1 x thickness)", self.radius), ("K-factor", self.kfactor),
                         ("Flip", self.flip), ("Side that stays put", self.fixed_side)):
            self.fields.addRow(label, w)
        self.refresh_selection()

    def refresh_selection(self):
        self.face_label.setText(sel_label(self.base, [self.face] if self.face else []) if self.face else "(select a planar face)")
        self.sketch_label.setText(self.sketch.Label if alive(self.sketch) else "(select a sketch holding one straight line)")
        if self.obj is not None:
            self.use_sel.setEnabled(False)

    def take_selection(self):
        faces = selected("Face")
        face_obj = faces[0][0] if faces else None
        sketch = None
        for s in Gui.Selection.getSelectionEx():
            if s.Object is face_obj:
                continue
            if s.Object.TypeId.startswith("Sketcher::") or not s.SubElementNames:
                sketch = s.Object
                break
        if not faces or sketch is None:
            self.show_error("select a planar face of the sheet-metal part and, with Ctrl, the sketch that holds the jog line")
            return False
        self.show_error("")
        self.base, self.face, self.sketch = face_obj, faces[0][1][0], sketch
        self.refresh_selection()
        return True

    def load(self, obj):
        self.base = obj.baseObject[0]
        self.face = obj.baseObject[1][0] if obj.baseObject[1] else None
        self.sketch = obj.sketch
        self.offset.setValue(obj.offset.Value)
        _set_combo(self.offset_type, str(obj.offsetType))
        self.angle.setValue(obj.angle.Value)
        self.radius.setValue(obj.radius.Value)
        self.kfactor.setValue(float(obj.kfactor))
        self.flip.setChecked(bool(obj.flip))
        _set_combo(self.fixed_side, str(obj.fixedSide))
        self.refresh_selection()

    def inputs(self):
        if self.obj is not None:
            return self.obj.baseObject[0], self.obj.baseObject[1][0], self.obj.sketch
        return self.base, self.face, self.sketch

    def values(self):
        base, face, sketch = self.inputs()
        if not alive(base) or not face:
            raise PanelError("select a planar face of the sheet-metal part first")
        if not alive(sketch):
            raise PanelError("select the sketch that holds the jog line (one straight line on the face)")
        return {"offset": self.offset.value(), "offsetType": self.offset_type.currentData(), "angle": self.angle.value(),
                "radius": self.radius.value(), "kfactor": self.kfactor.value(), "flip": self.flip.isChecked(),
                "fixedSide": self.fixed_side.currentData()}

    def dry_run(self, v):
        base, face, sketch = self.inputs()
        p0, p1 = JG.line_from_sketch(sketch)
        JG.build_jog(base.Shape.copy(), face, p0, p1, v["offset"], v["offsetType"], v["angle"], v["radius"], v["flip"],
                     v["fixedSide"])

    def create(self, doc, v):
        obj = JG.make_jog(doc, self.base, self.face, self.sketch, **v)
        self.attach([obj])
        return [obj]

    def edit(self, obj, v):
        for k, val in v.items():
            setattr(obj, k, val)
        return [obj]


# ---------------------------------------------------------------- convert from solid

class ConvertSolidPanel(FeaturePanel):
    """Fixed face, bend edges and open faces come from the selection into three lists, each with its own
    buttons: 'Set' / 'Add' take the current selection, 'Remove' drops the highlighted rows."""

    title = "Convert to sheet metal"
    transaction = "Convert to sheet metal"

    def __init__(self, doc=None, obj=None, source=None, fixed=None, bends=(), opens=()):
        self.source = source
        self._fixed0, self._bends0, self._opens0 = fixed, list(bends), list(opens)
        super().__init__(doc, obj)

    def _list_row(self, label, name, kind, single, hint):
        lst = QtWidgets.QListWidget()
        lst.setObjectName(name)
        lst.setMaximumHeight(70)
        lst.setMinimumHeight(3 * lst.fontMetrics().lineSpacing() + 2 * lst.frameWidth() + 6)   # about three rows; three lists share the panel, so a short panel may squeeze them down to this
        lst.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        add = QtWidgets.QPushButton("Set from selection" if single else "Add selected")
        add.setObjectName(name + "_add")
        add.setToolTip(hint)
        rem = QtWidgets.QPushButton("Remove")
        rem.setObjectName(name + "_remove")
        box = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(lst)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(add)
        row.addWidget(rem)
        lay.addLayout(row)
        self.fields.addRow(label, box)
        add.clicked.connect(lambda _c=False: self.take(lst, kind, single))
        rem.clicked.connect(lambda _c=False: self.remove(lst))
        return lst

    def build(self):
        self.source_label = _label()
        self.source_label.setObjectName("source")
        self.fields.addRow("Solid", self.source_label)
        self.fixed = self._list_row("Fixed face", "fixed", "Face", True, "The face that stays where it is; everything else unfolds from it")
        self.bends = self._list_row("Bend edges", "bends", "Edge", False, "Edges between two kept faces that become bends")
        self.opens = self._list_row("Open faces", "opens", "Face", False, "Faces of the solid that get no wall")
        self.thickness = _spin(0.001, 100000.0, 1.0)
        self.radius = _spin(0.001, 100000.0, 1.0)
        self.kfactor = _spin(0.0, 1.0, 0.4, 3, "")
        self.rip_gap = _spin(0.0, 100000.0, 1.0)
        self.side = _combo([("Inward", "Inward (into the solid)"), ("Outward", "Outward")], "Inward")
        for label, w in (("Thickness", self.thickness), ("Inside bend radius", self.radius), ("K-factor", self.kfactor),
                         ("Gap at ripped corners", self.rip_gap), ("Thickness direction", self.side)):
            self.fields.addRow(label, w)
        if self.source is not None:
            self.source_label.setText(self.source.Label)
        else:
            self.source_label.setText("(select a solid, then use the buttons below)")
        if self._fixed0:
            self.fixed.addItem(self._fixed0)
        for n in self._bends0:
            self.bends.addItem(n)
        for n in self._opens0:
            self.opens.addItem(n)

    # list handling
    def _items(self, lst):
        return [lst.item(i).text() for i in range(lst.count())]

    def take(self, lst, kind, single):
        picks = selected(kind)
        if not picks:
            self.show_error("select %s of the solid first" % ("a face" if kind == "Face" else "one or more edges"))
            return False
        obj = picks[0][0]
        if self.obj is not None and obj is not self.source:
            self.show_error("pick %ss of %s" % (kind.lower(), self.source.Label))
            return False
        if self.source is not None and obj is not self.source:
            self.show_error("the selection belongs to %s, not to %s; clear the lists to change the solid"
                            % (obj.Label, self.source.Label))
            return False
        self.show_error("")
        self.source = obj
        self.source_label.setText(obj.Label)
        names = [n for o, ns in picks if o is obj for n in ns]
        if single:
            lst.clear()
            lst.addItem(names[0])
        else:
            have = set(self._items(lst))
            for n in names:
                if n not in have:
                    lst.addItem(n)
        return True

    def remove(self, lst):
        for it in lst.selectedItems():
            lst.takeItem(lst.row(it))

    def load(self, obj):
        self.source = obj.baseObject[0]
        self.source_label.setText(self.source.Label)
        for lst, names in ((self.fixed, obj.baseObject[1]), (self.bends, obj.bendEdges[1]), (self.opens, obj.openFaces[1])):
            lst.clear()
            for n in names:
                lst.addItem(n)
        self.thickness.setValue(obj.thickness.Value)
        self.radius.setValue(obj.radius.Value)
        self.kfactor.setValue(float(obj.kfactor))
        self.rip_gap.setValue(obj.ripGap.Value)
        _set_combo(self.side, str(obj.thicknessSide))

    def values(self):
        if not alive(self.source):
            raise PanelError("select the solid to convert first")
        fixed = self._items(self.fixed)
        if len(fixed) != 1:
            raise PanelError("set the fixed face (select one face, then press 'Set from selection')")
        return {"fixed": fixed[0], "bends": self._items(self.bends), "opens": self._items(self.opens),
                "thickness": self.thickness.value(), "radius": self.radius.value(), "kfactor": self.kfactor.value(),
                "ripGap": self.rip_gap.value(), "thicknessSide": self.side.currentData()}

    def dry_run(self, v):
        shape = self.source.Shape
        solid = CS.source_solid(shape)
        fixed = CS._to_solid_indices(shape, solid, [v["fixed"]], "Face")
        bends = CS._to_solid_indices(shape, solid, v["bends"], "Edge")
        opens = CS._to_solid_indices(shape, solid, v["opens"], "Face")
        CS.build_from_solid(solid, fixed[0], bends, opens, v["thickness"], v["radius"], v["ripGap"],
                            v["thicknessSide"] == "Inward")

    def create(self, doc, v):
        obj = CS.make_convert_solid(doc, self.source, v["fixed"], v["bends"], v["opens"], thickness=v["thickness"],
                                    radius=v["radius"], kfactor=v["kfactor"], ripGap=v["ripGap"],
                                    thicknessSide=v["thicknessSide"])
        self.attach([obj])
        return [obj]

    def edit(self, obj, v):
        src = obj.baseObject[0]
        obj.baseObject = (src, [v["fixed"]])
        obj.bendEdges = (src, list(v["bends"]))
        obj.openFaces = (src, list(v["opens"]))
        for k in ("thickness", "radius", "kfactor", "ripGap", "thicknessSide"):
            setattr(obj, k, v[k])
        return [obj]


PANELS = {"edgeflange": EdgeFlangePanel, "hem": HemPanel, "convert": ConvertSolidPanel, "jog": JogPanel}
