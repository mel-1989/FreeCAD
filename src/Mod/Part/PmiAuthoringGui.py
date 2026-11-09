# SPDX-License-Identifier: LGPL-2.1-or-later

# G12 slice 3b-i: the PMI authoring task panel, PMI views and their commands (checks 40 to 49 of briefs/phase1/G12-pmi.md).
#
# The panel gathers faces from the 3D selection and values from its fields, shows the standards rules a new item would
# break before it is made, and on Accept calls PmiAuthoring once (one undo transaction). It never repeats the logic of
# PmiAuthoring: derivation, bounds, datum uses and the rules table all live there.
#
# Saved views follow the CAx-IF recommended practice for PMI (a saved view is a camera plus the annotations it shows,
# CAMERA_MODEL_D3) and ASME Y14.41 (model views): a Part::PmiView holds an Inventor camera string and a list of items;
# activating it sets the camera and shows exactly those items.

import types

import FreeCAD as App
import FreeCADGui as Gui

import PmiAuthoring as PA

from PySide import QtCore, QtGui, QtWidgets

translate = App.Qt.translate

KINDS = ("size", "location", "angular", "datum", "tolerance")
KIND_TEXT = {
    "size": "Size",
    "location": "Location",
    "angular": "Angular location",
    "datum": "Datum",
    "tolerance": "Geometric tolerance",
}
SIZE_TYPE_TEXT = (
    ("", "From the faces"),
    ("Size_Diameter", "Diameter"),
    ("Size_Radius", "Radius"),
    ("Size_Thickness", "Thickness"),
)
BOUNDS_TEXT = (("", "None"), ("plus_minus", "Plus and minus"), ("range", "Upper and lower limit"), ("class", "Fit class"))
ZONE_SHAPE_TEXT = (("", "Plain"), ("Diameter", "Diameter zone"), ("SphericalDiameter", "Spherical zone"))
ZONE_MOD_TEXT = (("", "None"), ("Projected", "Projected"), ("Runout", "Runout"), ("NonUniform", "Non-uniform"))
MATERIAL_TEXT = (("", "None"), ("MAXIMUM_MATERIAL_REQUIREMENT", "Maximum material"), ("LEAST_MATERIAL_REQUIREMENT", "Least material"))


# ------------------------------------------------------------------------------------------------
# picks: Gui.Selection -> (obj, ["FaceN"])
# ------------------------------------------------------------------------------------------------


def _same_face(a, b, rel=1e-6):
    """Ruling 6 of slice 3a: surface type, area and centre of mass (within rel of the model diagonal)."""
    try:
        if a.Surface.TypeId != b.Surface.TypeId:
            return False
        if abs(a.Area - b.Area) > rel * max(a.Area, b.Area, 1e-12):
            return False
        diag = max(a.BoundBox.DiagonalLength, b.BoundBox.DiagonalLength, 1e-12)
        return (a.CenterOfMass - b.CenterOfMass).Length <= rel * max(diag, 1.0) * 1.0 + 1e-9
    except Exception:
        return False


def _body_of(obj):
    """The PartDesign body that owns a feature, else None."""
    try:
        if obj.TypeId == "PartDesign::Body":
            return obj
        grp = obj.getParentGeoFeatureGroup()
        if grp is not None and grp.TypeId == "PartDesign::Body":
            return grp
    except Exception:
        pass
    return None


def resolvePick(top, sub):
    """Turn one selection entry (a top-level object and a sub-element path such as "Face3" or "Pocket.Face7") into the
    (object, "FaceN") pair that PmiAuthoring reads, or None when it is not a face of a shape.

    On a PartDesign body the click names a feature of the body (its tip) in the path; the shape PmiAuthoring reads is the
    body's own, so the pair is made on the body and the face is the one of the body's shape that has the picked face's
    geometry (surface type, area, centre of mass), exactly one of them."""
    if not sub:
        return None
    path, _, element = sub.rpartition(".")
    if not (element.startswith("Face") and element[4:].isdigit()):
        return None
    leaf = top
    if path:
        try:
            leaf = top.getSubObject(path + ".", 1)
        except Exception:
            leaf = None
        if leaf is None:
            return None
    shape = getattr(leaf, "Shape", None)
    if shape is None or shape.isNull():
        return None
    n = int(element[4:])
    if not 1 <= n <= len(shape.Faces):
        return None
    body = _body_of(leaf) or _body_of(top)
    if body is not None:
        face = shape.Faces[n - 1]
        bshape = body.Shape
        if bshape.isNull():
            return None
        hits = [i for i, f in enumerate(bshape.Faces, 1) if _same_face(f, face)]
        if len(hits) != 1:
            return None
        return (body, "Face%d" % hits[0])
    return (leaf, element)


def selectedFaces():
    """[(obj, "FaceN")] of the faces in the current selection, in selection order, duplicates removed."""
    out = []
    for sel in Gui.Selection.getSelectionEx("", 0):
        for sub in sel.SubElementNames:
            pr = resolvePick(sel.Object, sub)
            if pr is not None and pr not in out:
                out.append(pr)
    return out


# ------------------------------------------------------------------------------------------------
# live rule check: the rules table runs on a draft of the item, nothing is created
# ------------------------------------------------------------------------------------------------


class _Draft(types.SimpleNamespace):
    """A stand-in with the properties of a PMI item, enough for the rules of PmiAuthoring."""

    def __init__(self, doc, type_id, **kw):
        defaults = dict(
            Document=doc,
            TypeId=type_id,
            Name="Draft",
            Label="Draft",
            PmiType="",
            SemanticName="",
            Value=0.0,
            HasValue=True,
            HasBounds=False,
            BoundsKind="",
            UpperBound=0.0,
            LowerBound=0.0,
            Qualifier="",
            Modifiers=[],
            DatumReferences=[],
            References=[],
            SecondSide=[],
            RuleWarnings=[],
            Unattached=False,
            ToleranceClass="",
            ZoneShape="",
            ZoneModifier="",
            ZoneModifierValue=0.0,
            Datums=[],
            Letter="",
            Position=0,
            IsTarget=False,
        )
        defaults.update(kw)
        super().__init__(**defaults)


def _pairs(faces):
    return PA._merge(PA._as_pairs([(o, [s]) for o, s in faces]))


def _num(text, what, required=True):
    t = (text or "").strip()
    if not t:
        if required:
            raise ValueError("%s: enter a number" % what)
        return None
    try:
        return float(t.replace(",", "."))
    except ValueError:
        raise ValueError("%s: %r is not a number" % (what, text))


# ------------------------------------------------------------------------------------------------
# the task panel
# ------------------------------------------------------------------------------------------------


class _FaceSet(QtWidgets.QGroupBox):
    """A list of picked faces with a button that makes it the target of the next picks in the view."""

    def __init__(self, title, panel, index):
        super().__init__(title)
        self.faces = []
        self.panel = panel
        self.index = index
        lay = QtWidgets.QVBoxLayout(self)
        self.list = QtWidgets.QListWidget()
        self.list.setObjectName("faceList%d" % index)
        self.list.setAccessibleName(title)
        self.list.setMaximumHeight(80)
        lay.addWidget(self.list)
        row = QtWidgets.QHBoxLayout()
        self.pick = QtWidgets.QPushButton(translate("Part_PmiAuthor", "Pick in view"))
        self.pick.setCheckable(True)
        self.pick.setAutoDefault(False)
        self.pick.setToolTip(translate("Part_PmiAuthor", "Faces you click in the 3D view go into this list"))
        self.pick.clicked.connect(lambda: panel.setActiveSet(index))
        self.take = QtWidgets.QPushButton(translate("Part_PmiAuthor", "Use selection"))
        self.take.setAutoDefault(False)
        self.take.clicked.connect(lambda: panel.addFaces(index, selectedFaces()))
        self.clear = QtWidgets.QPushButton(translate("Part_PmiAuthor", "Clear"))
        self.clear.setAutoDefault(False)
        self.clear.clicked.connect(lambda: panel.clearFaces(index))
        for b in (self.pick, self.take, self.clear):
            row.addWidget(b)
        lay.addLayout(row)

    def refill(self):
        self.list.clear()
        for o, s in self.faces:
            self.list.addItem("%s.%s" % (o.Label, s))


class _Observer:
    def __init__(self, panel):
        self.panel = panel

    def addSelection(self, doc, obj, sub, pnt):
        self.panel.onPick(doc, obj, sub)


class PmiAuthorPanel:
    """Task panel of Part_PmiAuthor: kind chooser, fields, face lists and the live rule warnings."""

    def __init__(self, doc=None):
        self.doc = doc or App.ActiveDocument
        self.active = 0
        self.alive = True
        self._note = ""  # said by the next refresh, ahead of what the fields still lack
        self.faces = [[], []]
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle(translate("Part_PmiAuthor", "Author PMI"))
        self.form.setObjectName("PmiAuthorPanel")
        self._build()
        self.observer = _Observer(self)
        Gui.Selection.addObserver(self.observer)
        self.form.destroyed.connect(self._release)
        self.setKind("size")

    # ---- construction -------------------------------------------------------------------------
    def _line(self, name, label, placeholder=""):
        w = QtWidgets.QLineEdit()
        w.setObjectName(name)
        w.setAccessibleName(label)
        w.setPlaceholderText(placeholder)
        w.textChanged.connect(self.refresh)
        return w

    def _combo(self, name, label, table):
        w = QtWidgets.QComboBox()
        w.setObjectName(name)
        w.setAccessibleName(label)
        for key, text in table:
            w.addItem(translate("Part_PmiAuthor", text), key)
        w.currentIndexChanged.connect(self.refresh)
        return w

    def _build(self):
        lay = QtWidgets.QVBoxLayout(self.form)
        top = QtWidgets.QFormLayout()
        self.kind = QtWidgets.QComboBox()
        self.kind.setObjectName("kind")
        for k in KINDS:
            self.kind.addItem(translate("Part_PmiAuthor", KIND_TEXT[k]), k)
        lbl = QtWidgets.QLabel(translate("Part_PmiAuthor", "Kind"))
        lbl.setBuddy(self.kind)
        top.addRow(lbl, self.kind)
        self.kind.currentIndexChanged.connect(lambda _i: self.setKind(self.kind.currentData()))
        lay.addLayout(top)

        self.set1 = _FaceSet(translate("Part_PmiAuthor", "Faces"), self, 0)
        self.set2 = _FaceSet(translate("Part_PmiAuthor", "Second faces"), self, 1)
        lay.addWidget(self.set1)
        lay.addWidget(self.set2)

        # dimension fields
        self.dim_box = QtWidgets.QGroupBox(translate("Part_PmiAuthor", "Dimension"))
        f = QtWidgets.QFormLayout(self.dim_box)
        self.size_type = self._combo("sizeType", "Size type", SIZE_TYPE_TEXT)
        self.size_type_label = QtWidgets.QLabel(translate("Part_PmiAuthor", "Size type"))
        self.size_type_label.setBuddy(self.size_type)
        f.addRow(self.size_type_label, self.size_type)
        self.nominal = self._line("nominal", "Nominal (mm or degrees)", translate("Part_PmiAuthor", "empty: from the faces"))
        self.nominal_label = QtWidgets.QLabel(translate("Part_PmiAuthor", "Nominal (mm or degrees)"))
        self.nominal_label.setBuddy(self.nominal)
        f.addRow(self.nominal_label, self.nominal)
        self.no_value = QtWidgets.QCheckBox(translate("Part_PmiAuthor", "State no value"))
        self.no_value.setObjectName("noValue")
        self.no_value.setToolTip(translate("Part_PmiAuthor", "Author a dimension whose value is left off (a basic dimension)"))
        self.no_value.toggled.connect(self.refresh)
        f.addRow(self.no_value)
        self.bounds = self._combo("bounds", "Bounds", BOUNDS_TEXT)
        bl = QtWidgets.QLabel(translate("Part_PmiAuthor", "Bounds"))
        bl.setBuddy(self.bounds)
        f.addRow(bl, self.bounds)
        self.upper = self._line("upper", "Upper")
        ul = QtWidgets.QLabel(translate("Part_PmiAuthor", "Upper"))
        ul.setBuddy(self.upper)
        f.addRow(ul, self.upper)
        self.lower = self._line("lower", "Lower")
        ll = QtWidgets.QLabel(translate("Part_PmiAuthor", "Lower"))
        ll.setBuddy(self.lower)
        f.addRow(ll, self.lower)
        self.fit = self._line("fitClass", "Fit class", "H7")
        fl = QtWidgets.QLabel(translate("Part_PmiAuthor", "Fit class"))
        fl.setBuddy(self.fit)
        f.addRow(fl, self.fit)
        self.bounds.currentIndexChanged.connect(self._bounds_visible)
        lay.addWidget(self.dim_box)

        # datum fields
        self.datum_box = QtWidgets.QGroupBox(translate("Part_PmiAuthor", "Datum"))
        f = QtWidgets.QFormLayout(self.datum_box)
        self.datum_letter = self._line("datumLetter", "Datum letter", "A")
        self.datum_letter.setMaxLength(1)
        dl = QtWidgets.QLabel(translate("Part_PmiAuthor", "Datum letter"))
        dl.setBuddy(self.datum_letter)
        f.addRow(dl, self.datum_letter)
        lay.addWidget(self.datum_box)

        # tolerance fields
        self.tol_box = QtWidgets.QGroupBox(translate("Part_PmiAuthor", "Tolerance"))
        f = QtWidgets.QFormLayout(self.tol_box)
        self.tol_type = QtWidgets.QComboBox()
        self.tol_type.setObjectName("tolType")
        for t in PA.TOL_TYPES:
            self.tol_type.addItem(t, t)
        self.tol_type.currentIndexChanged.connect(self.refresh)
        tl = QtWidgets.QLabel(translate("Part_PmiAuthor", "Tolerance type"))
        tl.setBuddy(self.tol_type)
        f.addRow(tl, self.tol_type)
        self.tol_value = self._line("tolValue", "Tolerance value (mm)")
        vl = QtWidgets.QLabel(translate("Part_PmiAuthor", "Tolerance value (mm)"))
        vl.setBuddy(self.tol_value)
        f.addRow(vl, self.tol_value)
        self.zone_shape = self._combo("zoneShape", "Zone", ZONE_SHAPE_TEXT)
        zl = QtWidgets.QLabel(translate("Part_PmiAuthor", "Zone"))
        zl.setBuddy(self.zone_shape)
        f.addRow(zl, self.zone_shape)
        self.zone_mod = self._combo("zoneModifier", "Zone modifier", ZONE_MOD_TEXT)
        zml = QtWidgets.QLabel(translate("Part_PmiAuthor", "Zone modifier"))
        zml.setBuddy(self.zone_mod)
        f.addRow(zml, self.zone_mod)
        self.zone_mod_value = self._line("zoneModValue", "Zone modifier height (mm)")
        zvl = QtWidgets.QLabel(translate("Part_PmiAuthor", "Zone modifier height (mm)"))
        zvl.setBuddy(self.zone_mod_value)
        f.addRow(zvl, self.zone_mod_value)
        self.material = self._combo("material", "Material condition", MATERIAL_TEXT)
        ml = QtWidgets.QLabel(translate("Part_PmiAuthor", "Material condition"))
        ml.setBuddy(self.material)
        f.addRow(ml, self.material)
        self.all_around = QtWidgets.QCheckBox(translate("Part_PmiAuthor", "All around"))
        self.all_around.setObjectName("allAround")
        self.all_around.toggled.connect(self.refresh)
        f.addRow(self.all_around)
        self.comp_letter, self.comp_mat = [], []
        for i in range(PA.MAX_COMPARTMENTS):
            row = QtWidgets.QHBoxLayout()
            le = self._line("compLetter%d" % (i + 1), "Datum %d letter" % (i + 1))
            le.setMaxLength(1)
            le.setMaximumWidth(40)
            mc = self._combo("compMaterial%d" % (i + 1), "Datum %d material condition" % (i + 1), MATERIAL_TEXT)
            row.addWidget(le)
            row.addWidget(mc)
            cl = QtWidgets.QLabel("Datum %d" % (i + 1))
            cl.setBuddy(le)
            f.addRow(cl, row)
            self.comp_letter.append(le)
            self.comp_mat.append(mc)
        lay.addWidget(self.tol_box)

        self.warn_label = QtWidgets.QLabel()
        self.warn_label.setObjectName("warnings")
        self.warn_label.setWordWrap(True)
        self.warn_label.setAccessibleName("Rule warnings")
        self.status = QtWidgets.QLabel()
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        lay.addWidget(self.warn_label)
        lay.addWidget(self.status)
        lay.addStretch(1)
        self._bounds_visible()

    # ---- state ----------------------------------------------------------------------------------
    def currentKind(self):
        return self.kind.currentData()

    def setKind(self, kind):
        i = KINDS.index(kind)
        if self.kind.currentIndex() != i:
            self.kind.setCurrentIndex(i)
            return  # the signal comes back here
        two = kind in ("location", "angular")
        self.set2.setVisible(two)
        self.dim_box.setVisible(kind in ("size", "location", "angular"))
        self.size_type.setVisible(kind == "size")
        self.size_type_label.setVisible(kind == "size")
        self.datum_box.setVisible(kind == "datum")
        self.tol_box.setVisible(kind == "tolerance")
        self.set1.setTitle(translate("Part_PmiAuthor", "First faces") if two else translate("Part_PmiAuthor", "Faces"))
        if not two and self.active == 1:
            self.setActiveSet(0)
        self.setActiveSet(self.active)
        self.refresh()

    def _bounds_visible(self, *_a):
        key = self.bounds.currentData()
        for w in (self.upper, self.lower):
            w.setEnabled(key in ("plus_minus", "range"))
        self.fit.setEnabled(key == "class")
        self.refresh()

    def setActiveSet(self, index):
        self.active = index
        self.set1.pick.setChecked(index == 0)
        self.set2.pick.setChecked(index == 1)

    def _set(self, index):
        return (self.set1, self.set2)[index]

    def _stale(self, pr):
        """True when the object of a picked face has been deleted from its document since it was picked."""
        try:
            obj = pr[0]
            return obj.Document is None or obj.Document.getObject(obj.Name) is None
        except Exception:  # a deleted object raises ReferenceError on any access
            return True

    def _dropStale(self):
        """Forget the faces whose object was deleted while the panel was open (say so); True when any went."""
        dropped = 0
        for i in (0, 1):
            keep = [pr for pr in self.faces[i] if not self._stale(pr)]
            dropped += len(self.faces[i]) - len(keep)
            self.faces[i][:] = keep
            self._set(i).faces = self.faces[i]
            self._set(i).refill()
        if dropped:
            self._note = translate("Part_PmiAuthor", "A picked object was deleted: its face was dropped from the list.")
        return bool(dropped)

    def addFaces(self, index, faces):
        for pr in faces:
            if self._stale(pr):
                continue
            if pr[0].Document != self.doc:
                self._note = translate("Part_PmiAuthor", "That face is in another document ('%s'); PMI is made in '%s'.") % (pr[0].Document.Name, self.doc.Name)
                continue
            if pr not in self.faces[index]:
                self.faces[index].append(pr)
        self._set(index).faces = self.faces[index]
        self._set(index).refill()
        self.refresh()

    def clearFaces(self, index):
        self.faces[index] = []
        self._set(index).faces = self.faces[index]
        self._set(index).refill()
        self.refresh()

    def onPick(self, docname, objname, sub):
        """Selection observer: a face clicked in the view goes into the active face list."""
        if not self.alive:
            return
        doc = App.getDocument(docname)
        obj = doc.getObject(objname) if doc else None
        if obj is None:
            return
        if doc != self.doc:
            self._say(translate("Part_PmiAuthor", "That face is in another document ('%s'); PMI is made in '%s'.") % (doc.Name, self.doc.Name))
            return
        pr = resolvePick(obj, sub)
        if pr is None:
            self._say(translate("Part_PmiAuthor", "Only faces can be picked, and a PartDesign face must match exactly one face of its body."))
            return
        index = self.active if self.currentKind() in ("location", "angular") else 0
        self.addFaces(index, [pr])

    def _say(self, text):
        self.status.setText(text)

    # ---- collecting ---------------------------------------------------------------------------
    def _material_mods(self, combo, all_around=False):
        mods = []
        if combo.currentData():
            mods.append(combo.currentData())
        if all_around:
            mods.append("ALL_AROUND")
        return mods

    def _bounds_args(self):
        key = self.bounds.currentData()
        if key == "plus_minus":
            return dict(plus_minus=(_num(self.upper.text(), "Upper"), _num(self.lower.text(), "Lower")))
        if key == "range":
            return dict(limits=(_num(self.upper.text(), "Upper"), _num(self.lower.text(), "Lower")))
        if key == "class":
            return dict(fit_class=self.fit.text().strip())
        return {}

    def collect(self):
        """(function name, positional args, keyword args) of the PmiAuthoring call this panel would make."""
        kind = self.currentKind()
        f1, f2 = self.faces
        if kind == "size":
            kw = self._bounds_args()
            val = _num(self.nominal.text(), "Nominal", required=False)
            if self.no_value.isChecked():
                kw["derive"] = False
                val = None
            if self.size_type.currentData():
                kw["pmi_type"] = self.size_type.currentData()
            return "addSizeDimension", ([(o, [s]) for o, s in f1], val), kw
        if kind == "location":
            kw = self._bounds_args()
            val = _num(self.nominal.text(), "Nominal", required=False)
            if self.no_value.isChecked():
                kw["derive"] = False
                val = None
            return "addLocationDimension", ([(o, [s]) for o, s in f1], [(o, [s]) for o, s in f2], val), kw
        if kind == "angular":
            kw = self._bounds_args()
            kw.pop("fit_class", None)
            val = _num(self.nominal.text(), "Nominal", required=False)
            if self.no_value.isChecked():
                kw["derive"] = False
                val = None
            return "addAngularDimension", ([(o, [s]) for o, s in f1], [(o, [s]) for o, s in f2], val), kw
        if kind == "datum":
            return "addDatum", (self.datum_letter.text().strip(), [(o, [s]) for o, s in f1]), {}
        datums = []
        for le, mc in zip(self.comp_letter, self.comp_mat):
            t = le.text().strip()
            if t:
                datums.append((t, [mc.currentData()]) if mc.currentData() else t)
        kw = dict(
            datums=datums,
            zone_shape=self.zone_shape.currentData(),
            zone_modifier=self.zone_mod.currentData(),
            modifiers=self._material_mods(self.material, self.all_around.isChecked()),
        )
        zv = _num(self.zone_mod_value.text(), "Zone modifier height", required=False)
        if zv is not None:
            kw["zone_modifier_value"] = zv
        return "addTolerance", (self.tol_type.currentData(), _num(self.tol_value.text(), "Tolerance value"), [(o, [s]) for o, s in f1]), kw

    # ---- live rule check ------------------------------------------------------------------------
    def draft(self):
        """The item the current fields would make, as a _Draft; raises ValueError when the call would refuse."""
        fn, args, kw = self.collect()
        doc = self.doc
        kind = self.currentKind()
        if kind in ("size", "location", "angular"):
            if kind == "size":
                pairs = PA._as_pairs(args[0])
                value = args[1]
                pmi_type = kw.get("pmi_type")
                derive = True
                if value is None and not kw.get("derive", True):
                    v = None
                    if pmi_type is None:
                        d = PA.deriveSize(pairs)
                        pmi_type = d[0] if d is not None else "Size_Diameter"
                elif value is None:
                    d = PA.deriveSize(pairs, pmi_type)
                    if d is None:
                        raise ValueError("the nominal size cannot be derived from these faces: enter a nominal")
                    pmi_type, v = d
                else:
                    v = PA._angle(value) if pmi_type == "Size_Angular" else PA._length(value)
                    if pmi_type is None:
                        d = PA.deriveSize(pairs)
                        pmi_type = d[0] if d is not None else "Size_Diameter"
                conv = PA._angle if pmi_type == "Size_Angular" else PA._length
                bnds = PA._bounds(kw.get("plus_minus"), kw.get("limits"), kw.get("fit_class"), conv)
                it = _Draft(doc, "Part::PmiDimension")
                PA._dimension_common(it, pmi_type, v, bnds, "", (), None)
                it.References = PA._merge(pairs)
                return it
            p1, p2 = PA._as_pairs(args[0]), PA._as_pairs(args[1])
            value = args[2]
            nov = value is None and not kw.get("derive", True)
            if kind == "location":
                v = None if nov else (PA.deriveDistance(p1, p2) if value is None else PA._length(value))
                pmi_type, conv = "Location_LinearDistance", PA._length
            else:
                v = None if nov else (PA.deriveAngle(p1, p2) if value is None else PA._angle(value))
                pmi_type, conv = "Location_Angular", PA._angle
            if v is None and not nov:
                raise ValueError("the value cannot be derived from these faces: enter a nominal")
            bnds = PA._bounds(kw.get("plus_minus"), kw.get("limits"), kw.get("fit_class"), conv)
            it = _Draft(doc, "Part::PmiDimension")
            PA._dimension_common(it, pmi_type, v, bnds, "", (), None)
            it.References = PA._merge(p1 + p2)
            it.SecondSide = PA._names(p2)
            return it
        if kind == "datum":
            letter = args[0]
            if len(letter) != 1 or not letter.isalpha() or not letter.isupper():
                raise ValueError("a datum letter is one capital letter")
            pairs = PA._as_pairs(args[1])
            return _Draft(doc, "Part::PmiDatum", PmiType="Datum", Letter=letter, References=PA._merge(pairs))
        tol_type, value, picks = args
        pairs = PA._as_pairs(picks)
        v = PA._length(value, "tolerance value")
        specs = [PA._datum_spec(d) for d in kw["datums"]]
        it = _Draft(
            doc,
            "Part::PmiTolerance",
            PmiType=tol_type,
            Value=v,
            ZoneShape=kw["zone_shape"],
            ZoneModifier=kw["zone_modifier"],
            ZoneModifierValue=PA._length(kw.get("zone_modifier_value", 0.0)),
            Modifiers=list(kw["modifiers"]),
            References=PA._merge(pairs),
        )
        uses, refs = [], []
        for pos, (letter, mods) in enumerate(specs, start=1):
            uses.append(_Draft(doc, "Part::PmiDatum", PmiType="Datum", Letter=letter, Position=pos, Modifiers=list(mods)))
            refs.append(letter + ("(" + ",".join(mods) + ")" if mods else ""))
        it.Datums = uses
        it.DatumReferences = refs
        return it

    def warnings(self):
        """[(rule id, message)] the current fields would raise; [] when the fields cannot make an item yet."""
        try:
            return PA.ruleHits(self.draft())
        except ValueError:
            return []

    def refresh(self, *_a):
        if not self.alive:
            return
        self._dropStale()
        msg = ""
        try:
            hits = PA.ruleHits(self.draft())
        except ValueError as e:
            hits = []
            msg = str(e)
        note, self._note = self._note, ""
        self._say((note + " " + msg).strip())
        self.warn_label.setText("\n".join("%s: %s" % h for h in hits))
        self.warn_label.setVisible(bool(hits))

    # ---- task dialog protocol -------------------------------------------------------------------
    def getStandardButtons(self):
        return QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel

    def isAllowedAlterDocument(self):
        return True

    def accept(self):
        if self._dropStale():
            return False  # the face list changed under the fields: look at it again before making anything
        try:
            fn, args, kw = self.collect()
            item = getattr(PA, fn)(*args, **kw)
        except ValueError as e:
            self._say(str(e))
            return False
        self.created = item
        self._release()
        Gui.Selection.clearSelection()
        return True

    def reject(self):
        self._release()
        return True

    def _release(self, *_a):
        if self.alive:
            self.alive = False
            try:
                Gui.Selection.removeObserver(self.observer)
            except Exception:
                pass


class _CommandAuthor:
    def GetResources(self):
        return {
            "Pixmap": "Part_PmiAuthor",
            "MenuText": QtCore.QT_TRANSLATE_NOOP("Part_PmiAuthor", "Author PMI..."),
            "ToolTip": QtCore.QT_TRANSLATE_NOOP(
                "Part_PmiAuthor",
                "Add a dimension, datum or geometric tolerance to the faces you pick, and check it against the standards rules",
            ),
            "StatusTip": QtCore.QT_TRANSLATE_NOOP("Part_PmiAuthor", "Add a dimension, datum or geometric tolerance to picked faces"),
        }

    def IsActive(self):
        return App.ActiveDocument is not None and Gui.Control.activeDialog() is False

    def Activated(self):
        panel = PmiAuthorPanel(App.ActiveDocument)
        Gui.Control.showDialog(panel)
        panel.addFaces(0, selectedFaces())


# ------------------------------------------------------------------------------------------------
# PMI views
# ------------------------------------------------------------------------------------------------


def _pmi_items(doc):
    return [o for o in doc.Objects if o.isDerivedFrom("Part::PmiItem")]


def _item_visible(o):
    vo = getattr(o, "ViewObject", None)
    return bool(vo is not None and vo.Visibility)


def _datum_key(o):
    body = o.References[0][0].Name if len(o.References) else None
    return (body, o.Letter)


def saveView(doc=None, name=None):
    """Save the 3D view's camera and the PMI items that are shown now as a Part::PmiView in the PMI group. One undo
    transaction. Returns the new object."""
    doc = doc or App.ActiveDocument
    gdoc = Gui.getDocument(doc.Name)
    view = gdoc.ActiveView
    if view is None:
        raise RuntimeError("there is no 3D view to take a camera from")
    camera = view.getCamera()
    items = [o for o in _pmi_items(doc) if _item_visible(o)]
    if doc.UndoMode == 0:
        doc.UndoMode = 1
    doc.openTransaction("Save PMI view")
    try:
        grp = PA._group(doc)  # made inside the transaction when the document has no PMI yet: undo removes it with the view
        gvo = getattr(grp, "ViewObject", None)
        if gvo is not None and not gvo.Visibility:
            items = []  # the whole group is hidden: nothing is shown
        obj = doc.addObject("Part::PmiView", "PmiView")
        n = len([o for o in doc.Objects if o.TypeId == "Part::PmiView"])
        obj.Label = name or "View %d" % n
        obj.Camera = camera
        obj.Items = items
        grp.addObject(obj)
    except Exception:
        doc.abortTransaction()
        raise
    doc.commitTransaction()
    return obj


def activateView(obj):
    """Set the camera of a Part::PmiView and show exactly its items: every other PMI item is hidden. Of the uses of one
    datum letter on one body only one draws, so the uses of a letter are shown together when the view has any of them."""
    doc = obj.Document
    gdoc = Gui.getDocument(doc.Name)
    view = gdoc.ActiveView
    wanted = {o.Name for o in obj.Items if o is not None}
    datum_keys = {_datum_key(o) for o in obj.Items if o is not None and o.TypeId == "Part::PmiDatum"}
    grp = PA._group(doc) if any(o.TypeId == "Part::PmiGroup" for o in doc.Objects) else None
    if grp is not None and grp.ViewObject is not None and not grp.ViewObject.Visibility:
        grp.ViewObject.Visibility = True
    for o in _pmi_items(doc):
        show = o.Name in wanted or (o.TypeId == "Part::PmiDatum" and _datum_key(o) in datum_keys)
        vo = o.ViewObject
        if vo is not None and vo.Visibility != show:
            vo.Visibility = show
    if obj.Camera:
        view.setCamera(obj.Camera)
    Gui.updateGui()


class _CommandSaveView:
    def GetResources(self):
        return {
            "Pixmap": "Part_PmiSaveView",
            "MenuText": QtCore.QT_TRANSLATE_NOOP("Part_PmiSaveView", "Save PMI view"),
            "ToolTip": QtCore.QT_TRANSLATE_NOOP(
                "Part_PmiSaveView", "Save the camera and the PMI items shown now as a named view in the PMI group"
            ),
            "StatusTip": QtCore.QT_TRANSLATE_NOOP("Part_PmiSaveView", "Save the camera and the shown PMI items as a view"),
        }

    def IsActive(self):
        return App.ActiveDocument is not None and Gui.ActiveDocument is not None and Gui.ActiveDocument.ActiveView is not None

    def Activated(self):
        saveView(App.ActiveDocument)


class _CommandActivateView:
    def GetResources(self):
        return {
            "Pixmap": "Part_PmiActivateView",
            "MenuText": QtCore.QT_TRANSLATE_NOOP("Part_PmiActivateView", "Activate PMI view"),
            "ToolTip": QtCore.QT_TRANSLATE_NOOP(
                "Part_PmiActivateView", "Restore the camera of the selected PMI view and show only its PMI items"
            ),
            "StatusTip": QtCore.QT_TRANSLATE_NOOP("Part_PmiActivateView", "Restore a saved PMI view"),
        }

    def _selected(self):
        return [o for o in Gui.Selection.getSelection() if o.TypeId == "Part::PmiView"]

    def IsActive(self):
        return App.ActiveDocument is not None and len(self._selected()) == 1

    def Activated(self):
        sel = self._selected()
        if len(sel) == 1:
            activateView(sel[0])


if App.GuiUp:
    Gui.addCommand("Part_PmiAuthor", _CommandAuthor())
    Gui.addCommand("Part_PmiSaveView", _CommandSaveView())
    Gui.addCommand("Part_PmiActivateView", _CommandActivateView())


# ------------------------------------------------------------------------------------------------
# fixtures for the GUI lint, theme check and scene renders (harness/gui): a throwaway document and a panel ready to apply
# ------------------------------------------------------------------------------------------------


def scene_doc(name="pmi_panel_scene"):
    """A throwaway document with a box, so the panel has faces to name"""
    import Part

    doc = App.newDocument(name)
    box = doc.addObject("Part::Feature", "Box")
    box.Shape = Part.makeBox(20, 30, 40)
    doc.recompute()
    return doc


def scene_panel(doc):
    """The authoring panel on a datum with one face and the letter A: Return applies it, Escape cancels it"""
    panel = PmiAuthorPanel(doc)
    panel.setKind("datum")
    panel.addFaces(0, [(doc.getObject("Box"), "Face1")])
    panel.datum_letter.setText("A")
    return panel
