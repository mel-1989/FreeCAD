# SPDX-License-Identifier: LGPL-2.1-or-later

"""Surface workbench commands for the analysis overlays: curvature, draft and zebra stripes.

Four commands, registered with ``FreeCADGui.addCommand`` when this module is imported (the
workbench imports it when it initialises):

``Surface_CurvatureAnalysis``, ``Surface_DraftAnalysis``, ``Surface_ZebraAnalysis``
    open one task panel on the selection, with the analysis chosen by the command.
``Surface_ClearAnalysis``
    takes every analysis overlay and legend out of the active 3D view.

The panel previews live: every parameter change replaces the panel's one overlay with a new one
built by :mod:`SurfaceAnalysisGui` from the same parameters. OK keeps the overlay and its legend,
Cancel removes them. The panel never opens an undo transaction, never touches the document and
never changes a view provider property.

Sources (clean room): FreeCAD's own Python task panels and commands (``Gui.Control.showDialog``,
the ``GetResources`` / ``IsActive`` / ``Activated`` protocol) and the Qt documentation.
"""

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import SurfaceAnalysisGui as SG

__all__ = ["AnalysisPanel", "selected_targets", "selected_subshapes"]

CURVATURE, DRAFT, ZEBRA = "curvature", "draft", "zebra"
_TYPES = ((CURVATURE, "Curvature"), (DRAFT, "Draft"), (ZEBRA, "Zebra stripes"))
_CURVATURE_KINDS = (
    ("gauss", "Gaussian"),
    ("mean", "Mean"),
    ("kmax", "Max principal"),
    ("kmin", "Min principal"),
)
_PULLS = (
    ("+X", (1, 0, 0)),
    ("-X", (-1, 0, 0)),
    ("+Y", (0, 1, 0)),
    ("-Y", (0, -1, 0)),
    ("+Z", (0, 0, 1)),
    ("-Z", (0, 0, -1)),
)
_ZEBRA_DIRS = (("X", (1, 0, 0)), ("Y", (0, 1, 0)), ("Z", (0, 0, 1)))
_BANDS = (4, 8, 16, 32, 64)
DEFAULT_MIN_DRAFT = 1.0


def _tr(text):
    return QtCore.QCoreApplication.translate("Surface_Analysis", text)


# ------------------------------------------------------------------ selection


def _object_shape(obj):
    shape = getattr(obj, "Shape", None)
    if isinstance(shape, Part.Shape) and not shape.isNull() and shape.Faces:
        return shape
    return None


def _sub_type(subname):
    """``"object"`` for a path that ends in ``.``, else the element type from the last name: ``"Face"``,
    ``"Edge"``, ``"Vertex"`` or ``""``."""
    last = subname.rsplit(".", 1)[-1]
    if not last:
        return "object"
    for kind in ("Face", "Edge", "Vertex"):
        if last.startswith(kind):
            return kind
    return ""


def _owner_names(top, subname):
    """The (document, object) names along a selection path: the top object, then each container down
    to the leaf, so the panel can notice when any of them is deleted."""
    names = [(top.Document.Name, top.Name)]
    try:
        for owner in top.getSubObjectList(subname):
            names.append((owner.Document.Name, owner.Name))
    except Exception:
        pass
    return names


def selected_subshapes(types=("Face", "object")):
    """The selection resolved to the shapes the 3D view shows, as a list of dicts with keys ``type``
    (``"Face"``, ``"Edge"``, ``"Vertex"`` or ``"object"``), ``object`` (the top selected object),
    ``subname`` (the dotted path, ``""`` for a whole-object entry), ``shape`` (a ``Part.Shape`` with every
    placement on the path applied, or the object itself for a whole-object entry) and ``owners`` (the
    ``(document, object)`` names along the path).

    Each selection entry is read unresolved (the top object and its dotted sub-names) and each sub-name
    goes through ``Part.getShape(top, subname, needSubElement=True)``, which applies the placement of
    every container on the path. ``types`` filters the result. An entry without sub-names is the top
    object (``type "object"``, ``shape`` ``None``: the caller gets the object itself)."""
    out = []
    try:
        selection = Gui.Selection.getSelectionEx("", 0)
    except Exception:
        return out
    for sel in selection:
        top = sel.Object
        names = [n for n in sel.SubElementNames if n]
        if not names:
            if "object" in types and _object_shape(top) is not None:
                out.append({"type": "object", "object": top, "subname": "", "shape": None,
                            "owners": [(top.Document.Name, top.Name)]})
            continue
        for name in names:
            kind = _sub_type(name)
            if kind not in types:
                continue
            try:
                if kind == "object":
                    shape = Part.getShape(top, name, needSubElement=False, transform=True)
                else:
                    shape = Part.getShape(top, name, needSubElement=True)
            except Exception:
                continue
            if shape is None or shape.isNull():
                continue
            if kind == "object" and not shape.Faces:
                continue
            if kind != "object" and shape.ShapeType != kind:
                continue
            out.append({"type": kind, "object": top, "subname": name, "shape": shape,
                        "owners": _owner_names(top, name)})
    return out


def selected_targets():
    """The analysis targets of the selection: a document object (an entry without sub-names), a
    ``Part.Face`` (a picked face) or a ``Part.Shape`` (a named sub-object), in selection order, every
    shape with the placements of its containers applied. Edges and vertices are ignored."""
    return [t for t, _owners in _selected_entries()]


def _selected_entries():
    out = []
    for item in selected_subshapes():
        target = item["object"] if item["shape"] is None else item["shape"]
        out.append((target, item["owners"]))
    return out


def _has_analysable_selection():
    try:
        for sel in Gui.Selection.getSelectionEx("", 0):
            names = [n for n in sel.SubElementNames if n]
            if not names:
                if _object_shape(sel.Object) is not None:
                    return True
                continue
            for name in names:
                if _sub_type(name) in ("Face", "object"):
                    return True
    except Exception:
        pass
    return False


def _one_target(targets):
    """One argument for the 2a API: the target itself, or a compound of several."""
    if len(targets) == 1:
        return targets[0]
    shapes = [t if isinstance(t, Part.Shape) else SG.placed_shape(t) for t in targets]
    return Part.makeCompound(shapes)


def _active_3d_view():
    try:
        doc = Gui.ActiveDocument
        view = doc.ActiveView if doc is not None else None
        if view is not None and hasattr(view, "getSceneGraph"):
            return view
    except Exception:
        pass
    return None


# ----------------------------------------------------------------------- panel


def _quantity_spin_box():
    """A ``Gui::QuantitySpinBox`` made by the UI loader (a widget class the Python bindings do not wrap)."""
    return Gui.UiLoader().createWidget("Gui::QuantitySpinBox")


class _Relay(QtCore.QObject):
    """Receiver of every widget signal of the panel. A real ``QObject`` slot keeps PySide from
    creating dynamic slots on the sender (a flaky crash in ``addMetaMethod`` on a spin box)."""

    def __init__(self, callback, parent):
        super().__init__(parent)
        self._callback = callback

    @QtCore.Slot()
    def changed(self):
        self._callback()

    @QtCore.Slot(bool)
    def toggled(self, on):
        self._callback(on)

    @QtCore.Slot()
    def finished(self):
        self._callback()


class AnalysisPanel:
    """Task panel with live preview of one analysis overlay.

    ``analysis`` is ``"curvature"``, ``"draft"`` or ``"zebra"``; ``targets`` defaults to the
    selection at the time the panel is made. ``self.handle`` is the overlay on show (or ``None``).
    """

    def __init__(self, analysis=CURVATURE, targets=None, view=None):
        if targets is not None:
            self.targets = list(targets)
            self._owners = [n for t in self.targets if not isinstance(t, Part.Shape) for n in [(t.Document.Name, t.Name)]]
        else:
            entries = _selected_entries()
            self.targets = [t for t, _o in entries]
            self._owners = [n for _t, owners in entries for n in owners]
        self.view = view
        self.handle = None
        self._gone = False
        self._observer = None
        self.error = ""
        self._busy = True

        form = QtWidgets.QWidget()
        form.setObjectName("G09_AnalysisPanel")
        form.setWindowTitle(_tr("Surface analysis"))
        self.form = form
        layout = QtWidgets.QVBoxLayout(form)
        top = QtWidgets.QFormLayout()
        layout.addLayout(top)

        self.type_box = self._combo("G09_AnalysisType", [(label, key) for key, label in _TYPES])
        self._row(top, "Analysis:", self.type_box)

        self.stack = QtWidgets.QStackedWidget(form)
        self.stack.setObjectName("G09_AnalysisPages")
        layout.addWidget(self.stack)

        # curvature page
        page = QtWidgets.QWidget(self.stack)
        page.setObjectName("G09_CurvaturePage")
        pf = QtWidgets.QFormLayout(page)
        self.kind_box = self._combo("G09_CurvatureKind", [(label, key) for key, label in _CURVATURE_KINDS])
        self.kind_box.setCurrentIndex(1)  # mean, as show_curvature's default
        self._row(pf, "Curvature:", self.kind_box)
        self.auto_box = QtWidgets.QCheckBox(_tr("Automatic range"), form)
        self.auto_box.setObjectName("G09_AutoRange")
        self.auto_box.setChecked(True)
        self.auto_box.setToolTip(_tr("Use the symmetric range of the 95th percentile of the absolute values"))
        pf.addRow(self.auto_box)
        self.low_spin = self._double("G09_RangeLow", -1.0)
        self._row(pf, "Low:", self.low_spin)
        self.high_spin = self._double("G09_RangeHigh", 1.0)
        self._row(pf, "High:", self.high_spin)
        self.low_spin.setEnabled(False)
        self.high_spin.setEnabled(False)
        self.stack.addWidget(page)

        # draft page
        page = QtWidgets.QWidget(self.stack)
        page.setObjectName("G09_DraftPage")
        pf = QtWidgets.QFormLayout(page)
        self.pull_box = self._combo("G09_DraftPull", [(label, vec) for label, vec in _PULLS])
        self.pull_box.setCurrentIndex(4)  # +Z
        self._row(pf, "Pull direction:", self.pull_box)
        self.draft_spin = _quantity_spin_box()
        self.draft_spin.setParent(form)
        self.draft_spin.setObjectName("G09_DraftMinAngle")
        self.draft_spin.setProperty("unit", "deg")
        self.draft_spin.setProperty("minimum", 0.0)
        self.draft_spin.setProperty("maximum", 89.0)
        self.draft_spin.setProperty("rawValue", DEFAULT_MIN_DRAFT)
        self.draft_spin.setToolTip(_tr("Faces with less draft than this are shown as insufficient"))
        self._row(pf, "Minimum draft angle:", self.draft_spin)
        self.stack.addWidget(page)

        # zebra page
        page = QtWidgets.QWidget(self.stack)
        page.setObjectName("G09_ZebraPage")
        pf = QtWidgets.QFormLayout(page)
        self.zebra_dir_box = self._combo("G09_ZebraDirection", [(label, vec) for label, vec in _ZEBRA_DIRS])
        self._row(pf, "Direction:", self.zebra_dir_box)
        self.bands_box = self._combo("G09_ZebraBands", [(str(n), n) for n in _BANDS])
        self.bands_box.setCurrentIndex(_BANDS.index(16))
        self._row(pf, "Bands:", self.bands_box)
        self.stack.addWidget(page)

        self.status = QtWidgets.QLabel(form)
        self.status.setObjectName("G09_AnalysisStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()

        self.set_analysis(analysis)

        self._relay_type = _Relay(self._type_changed, form)
        self._relay_auto = _Relay(self._auto_toggled, form)
        self._relay = _Relay(self._changed, form)
        # the spin box is made by the UI loader, so Shiboken does not see it die with the form; drop its
        # wrapper at once, or a later widget made at the same address comes back as this stale wrapper
        # (a "Signal source has been deleted" error on the second panel)
        self._relay_done = _Relay(self._release_wrappers, form)
        QtCore.QObject.connect(form, QtCore.SIGNAL("destroyed()"), self._relay_done, QtCore.SLOT("finished()"))
        self.type_box.currentIndexChanged.connect(self._relay_type.changed)
        self.auto_box.toggled.connect(self._relay_auto.toggled)
        for widget in (self.kind_box, self.pull_box, self.zebra_dir_box, self.bands_box):
            widget.currentIndexChanged.connect(self._relay.changed)
        for widget in (self.low_spin, self.high_spin, self.draft_spin):
            # by signature, not through the Python attribute (see above)
            QtCore.QObject.connect(widget, QtCore.SIGNAL("valueChanged(double)"), self._relay, QtCore.SLOT("changed()"))

        self._busy = False
        self._watch_owners()
        self.refresh()

    # ---- widget helpers
    def _combo(self, name, items):
        box = QtWidgets.QComboBox(self.form)
        box.setObjectName(name)
        for label, data in items:
            box.addItem(label, data)
        return box

    def _double(self, name, value):
        spin = QtWidgets.QDoubleSpinBox(self.form)
        spin.setObjectName(name)
        spin.setDecimals(9)
        spin.setRange(-1e9, 1e9)
        spin.setSingleStep(0.01)
        spin.setValue(value)
        return spin

    def _row(self, layout, text, widget):
        label = QtWidgets.QLabel(_tr(text), self.form)
        label.setObjectName(widget.objectName() + "_Label")
        label.setBuddy(widget)
        layout.addRow(label, widget)

    # ---- parameters
    def analysis(self):
        return self.type_box.currentData()

    def set_analysis(self, analysis):
        keys = [k for k, _ in _TYPES]
        self.type_box.setCurrentIndex(keys.index(analysis))
        self.stack.setCurrentIndex(keys.index(analysis))

    def parameters(self):
        """The arguments of the 2a call for the current widgets: ``(function name, args, kwargs)``."""
        a = self.analysis()
        if a == CURVATURE:
            rng = None if self.auto_box.isChecked() else (self.low_spin.value(), self.high_spin.value())
            return "show_curvature", (), {"kind": self.kind_box.currentData(), "range": rng}
        if a == DRAFT:
            return (
                "show_draft",
                (),
                {
                    "pull": self.pull_box.currentData(),
                    "min_draft": float(self.draft_spin.property("rawValue")),
                },
            )
        return (
            "show_zebra",
            (),
            {"direction": self.zebra_dir_box.currentData(), "bands": int(self.bands_box.currentData())},
        )

    # ---- preview
    def _type_changed(self, *_a):
        self.stack.setCurrentIndex(self.type_box.currentIndex())
        self._changed()

    def _auto_toggled(self, on=True):
        self.low_spin.setEnabled(not on)
        self.high_spin.setEnabled(not on)
        if not on and self.handle is not None and self.handle.range is not None:
            lo, hi = self.handle.range  # start from the automatic range the user was looking at
            self._busy = True
            try:
                self.low_spin.setValue(lo)
                self.high_spin.setValue(hi)
            finally:
                self._busy = False
        self._changed()

    def _changed(self, *_a):
        if not self._busy:
            self.refresh()

    def _release_wrappers(self):
        self._unwatch_owners()
        import shiboken6

        try:
            shiboken6.invalidate(self.draft_spin)
        except Exception:
            pass

    def _drop(self):
        if self.handle is not None:
            SG.remove(self.handle)
            self.handle = None

    # ---- deleted targets
    def _watch_owners(self):
        if not self._owners:
            return
        panel = self

        class _Observer:
            def slotDeletedObject(self, obj):
                try:
                    key = (obj.Document.Name, obj.Name)
                except Exception:
                    return
                if key in panel._owners:
                    panel._target_gone()

            def slotDeletedDocument(self, doc):
                try:
                    name = doc.Name
                except Exception:
                    return
                if any(d == name for d, _o in panel._owners):
                    panel._target_gone()

        self._observer = _Observer()
        App.addDocumentObserver(self._observer)

    def _unwatch_owners(self):
        if self._observer is not None:
            try:
                App.removeDocumentObserver(self._observer)
            except Exception:
                pass
            self._observer = None

    def _owners_alive(self):
        for doc_name, obj_name in self._owners:
            doc = App.getDocument(doc_name) if doc_name in App.listDocuments() else None
            if doc is None or doc.getObject(obj_name) is None:
                return False
        return True

    def _target_gone(self):
        """A target object was deleted: drop the overlay, forget the targets and say so."""
        self._gone = True
        self._drop()
        self.error = ""
        self._say(_tr("The target was deleted; there is nothing left to analyse."))

    def refresh(self):
        """Replace the panel's overlay with one built from the current widgets."""
        self._drop()
        self.error = ""
        if self._gone or not self._owners_alive():
            self._target_gone()
            return
        if not self.targets:
            self._say(_tr("Select a shape or faces first."))
            return
        name, args, kwargs = self.parameters()
        try:
            view = self.view if self.view is not None else SG._active_view(None)
            target = _one_target(self.targets)
            if name == "show_zebra":
                self.handle = SG.show_zebra(target, view=view, **kwargs)
            else:
                self.handle = getattr(SG, name)(target, view=view, legend=True, **kwargs)
        except (ValueError, RuntimeError) as e:
            self.error = str(e)
            self._say(self.error)
            return
        self._say(self._summary())

    def _summary(self):
        n = len(self.targets)
        return _tr("%d target(s), %d vertices") % (n, len(self.handle.vertices))

    def _say(self, text):
        self.status.setText(text)

    # ---- task dialog protocol
    def getStandardButtons(self):
        return QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel

    def isAllowedAlterDocument(self):
        return True

    def accept(self):
        self._unwatch_owners()
        self.handle = None  # the overlay and its legend stay in the view
        return True

    def reject(self):
        self._unwatch_owners()
        self._drop()
        return True


# -------------------------------------------------------------------- commands


class _AnalysisCommand:
    """Opens the analysis panel on the selection."""

    analysis = CURVATURE
    pixmap = ""
    menu = ""
    tip = ""
    status = ""

    def GetResources(self):
        return {
            "Pixmap": self.pixmap,
            "MenuText": QtCore.QT_TRANSLATE_NOOP(self.pixmap, self.menu),
            "ToolTip": QtCore.QT_TRANSLATE_NOOP(self.pixmap, self.tip),
            "StatusTip": QtCore.QT_TRANSLATE_NOOP(self.pixmap, self.status),
        }

    def IsActive(self):
        return (
            Gui.ActiveDocument is not None
            and not Gui.Control.activeDialog()
            and _has_analysable_selection()
        )

    def Activated(self):
        panel = AnalysisPanel(self.analysis)
        try:
            Gui.Control.showDialog(panel)
        except Exception:
            panel.reject()  # no overlay, legend or observer outlives a dialog that never opened
            raise


class _CurvatureCommand(_AnalysisCommand):
    analysis = CURVATURE
    pixmap = "Surface_CurvatureAnalysis"
    menu = "Curvature analysis"
    tip = "Colour the selected shapes or faces by Gaussian, mean or principal curvature, with a colour legend"
    status = "Show a curvature colour map of the selection in the 3D view"


class _DraftCommand(_AnalysisCommand):
    analysis = DRAFT
    pixmap = "Surface_DraftAnalysis"
    menu = "Draft analysis"
    tip = "Colour the selected shapes or faces by draft angle about a pull direction, with a legend"
    status = "Show a draft map of the selection in the 3D view"


class _ZebraCommand(_AnalysisCommand):
    analysis = ZEBRA
    pixmap = "Surface_ZebraAnalysis"
    menu = "Zebra stripes"
    tip = "Draw zebra stripes on the selected shapes or faces to judge how smoothly surfaces join"
    status = "Show zebra stripes on the selection in the 3D view"


class _ClearCommand:
    def GetResources(self):
        return {
            "Pixmap": "Surface_ClearAnalysis",
            # ForEdit: FreeCAD disables every command without it while a task dialog is open, and the
            # frozen rule keeps Clear active whenever a 3D view is (an analysis panel may be open)
            "CmdType": "ForEdit",
            "MenuText": QtCore.QT_TRANSLATE_NOOP("Surface_ClearAnalysis", "Clear analysis"),
            "ToolTip": QtCore.QT_TRANSLATE_NOOP(
                "Surface_ClearAnalysis", "Remove every analysis overlay and legend from the active 3D view"
            ),
            "StatusTip": QtCore.QT_TRANSLATE_NOOP(
                "Surface_ClearAnalysis", "Remove the analysis overlays from the 3D view"
            ),
        }

    def IsActive(self):
        return _active_3d_view() is not None

    def Activated(self):
        view = _active_3d_view()
        if view is not None:
            SG.clear(view)


COMMANDS = (
    ("Surface_CurvatureAnalysis", _CurvatureCommand),
    ("Surface_DraftAnalysis", _DraftCommand),
    ("Surface_ZebraAnalysis", _ZebraCommand),
    ("Surface_ClearAnalysis", _ClearCommand),
)


def register():
    for name, cls in COMMANDS:
        Gui.addCommand(name, cls())


register()
