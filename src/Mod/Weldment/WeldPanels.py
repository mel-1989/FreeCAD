# SPDX-License-Identifier: LGPL-2.1-or-later
"""Task panels of the Weldment workbench (cad-fork G07 slice 6a). Imported only with the GUI up.

One panel per command: structural member, corner treatment, gusset, end cap, weld bead and cut list.
A panel only collects parameters and calls the same API functions the tests and scripts call
(``WeldMember.make_member``, ``WeldTrim.make_corner`` and so on); it holds no geometry logic.

* OK runs the API call in one transaction (``transact``). When the API raises, or a recompute leaves the
  new or edited feature (or any feature that was fine before) invalid, the transaction is aborted so the
  document and its undo stack are as they were, the reason is shown as text in the panel, and the panel
  stays open. Nothing is printed as a Python traceback.
* Cancel changes nothing. An edit panel (opened from a feature's view provider) is prefilled from the
  object and writes its properties only on OK, so Cancel leaves every property and the shape untouched.
* ``values()`` reads the widgets, ``create()`` and ``edit()`` apply them; tests drive a panel by setting
  widgets and calling ``accept()`` or ``reject()`` exactly as the dialog buttons do.

Clean room: written from the FreeCAD Python task-panel conventions; no weldment workbench source was read.
"""

import re

import FreeCAD
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import Profiles
import WeldBead
import WeldCutList
import WeldEndCap
import WeldGusset
import WeldMember
import WeldTrim
from WeldGeom import ANCHORS, WeldmentError

#: panel the user has open (set by ``show``)
_CURRENT = []


def current():
    """The open Weldment panel, or None when its dialog has been closed."""
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


def is_member(obj):
    return type_name(obj) == "Member"


def is_chain(obj):
    """A Member or an EndTrim: what a corner treatment, gusset or end cap can be built on."""
    return is_member(obj) or WeldTrim.is_trim(obj)


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


_FEATURES = ("Member", "EndTrim", "Gusset", "EndCap", "WeldBead")


def _dry_run(doc, objs):
    """Execute, in Python and in dependency order, every Weldment feature that is one of ``objs`` or depends on
    one. A refusal is a WeldmentError caught here; letting the recompute raise it instead would make FreeCAD
    print a Python traceback to the report view. Returns the list of problem lines."""
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
        if o.Name not in targets or type_name(o) not in _FEATURES:
            continue
        if any(p.Name in failed for p in o.OutList):
            failed.add(o.Name)    # its input already failed; one reason is enough
            continue
        try:
            o.Proxy.execute(o)
        except Exception as e:    # noqa: BLE001
            failed.add(o.Name)
            text = str(e.args[0] if e.args else e) or type(e).__name__
            problems.append(text if o in objs else "%s: %s (broken by this change)" % (o.Label, text))
    return problems


def transact(doc, label, fn):
    """Run ``fn`` (returns the objects it created or changed) in one transaction and recompute.

    Returns ``(True, objects)`` when everything is valid. Otherwise the transaction is aborted and the
    result is ``(False, message)``: nothing of the attempt remains, not even on the undo stack."""
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
    except (WeldmentError, ValueError, TypeError, KeyError, RuntimeError, FreeCAD.Base.FreeCADError) as e:
        message = str(e.args[0] if e.args else e) or type(e).__name__
    except Exception as e:    # noqa: BLE001  (a panel never raises into the Qt event loop)
        message = "%s: %s" % (type(e).__name__, e)
    doc.abortTransaction()
    return False, message


def selection_path():
    """Member path from the selection: [(object, [sub names])], whole objects taking all their edges."""
    return [(s.Object, list(s.SubElementNames) or [""]) for s in Gui.Selection.getSelectionEx()]


def selection_chains():
    return [o for o in Gui.Selection.getSelection() if is_chain(o)]


def selection_face():
    """(object, 'FaceN') of the first picked face, else None."""
    for s in Gui.Selection.getSelectionEx():
        for n in s.SubElementNames:
            if n.startswith("Face"):
                return s.Object, n
    return None


def selection_bead():
    """(body1, body2, [(object, 'EdgeN')]) from the selection: the bodies are the selected objects."""
    sel = Gui.Selection.getSelectionEx()
    edges = [(s.Object, n) for s in sel for n in s.SubElementNames if n.startswith("Edge")]
    bodies = []
    for s in sel:
        if s.Object not in bodies:
            bodies.append(s.Object)
    return bodies, edges


def chain_label(obj):
    """Member name for a panel: the member's label, and the chain head's too when a trim stands in for it."""
    if obj is None:
        return ""
    if WeldTrim.is_trim(obj):
        try:
            return "%s (%s)" % (WeldTrim.root_member(obj).Label, obj.Label)
        except Exception:    # noqa: BLE001
            return obj.Label
    return obj.Label


def sorted_sizes(family):
    """The sizes of a family in natural order (50x50x2 before 50x50x10 before 125x125x4), as the panel lists them."""
    def key(size):
        return ([float(n) for n in re.findall(r"\d+\.?\d*", size)], size)

    return sorted(Profiles.sizes(family), key=key)


def path_text(path):
    if not path:
        return "(nothing selected: select straight edges or a sketch, then press Use selection)"
    parts = []
    for entry in path:
        subs = [s for s in entry[1] if s] if not isinstance(entry[1], str) else [entry[1]]
        parts.append("%s: %s" % (entry[0].Label, ", ".join(subs) if subs else "all edges"))
    return "; ".join(parts)


def joint_ends(a, b):
    """('Start'|'End' of a, same of b): the pair of ends of the two members' first segments that lie closest."""
    pa = WeldMember.segment_specs(WeldTrim.root_member(a))[0]
    pb = WeldMember.segment_specs(WeldTrim.root_member(b))[0]
    best = min(
        ((x, y, (px - py).Length) for x, px in (("Start", pa.p0), ("End", pa.p1)) for y, py in (("Start", pb.p0), ("End", pb.p1))),
        key=lambda t: t[2],
    )
    return best[0], best[1]


def end_near(member, point):
    spec = WeldMember.segment_specs(WeldTrim.root_member(member))[0]
    return "Start" if (spec.p0 - point).Length <= (spec.p1 - point).Length else "End"


def end_facing(member, face_obj, sub):
    """The end of ``member``'s first segment closest to the centre of the named face."""
    return end_near(member, face_obj.Shape.getElement(sub).CenterOfMass)


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


# ---------------------------------------------------------------- base panel

class FeaturePanel:
    """A task dialog that creates one feature (``obj`` None) or edits one."""

    title = ""
    transaction = ""

    def __init__(self, doc=None, obj=None):
        self.obj = obj
        self.doc = doc if doc is not None else (obj.Document if obj is not None else FreeCAD.ActiveDocument)
        if self.doc is None:
            raise WeldmentError("open a document first")
        self.done = False
        self.form = QtWidgets.QWidget()
        self.form.setObjectName("Weldment_" + type(self).__name__)
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

    # subclass hooks
    def build(self):
        raise NotImplementedError

    def load(self, obj):
        raise NotImplementedError

    def values(self):
        raise NotImplementedError

    def create(self, doc, v):
        raise NotImplementedError

    def edit(self, obj, v):
        raise NotImplementedError

    # messages
    def show_error(self, text):
        self.error.setText(text)
        self.error.setVisible(bool(text))

    def error_text(self):
        return self.error.text()

    # dialog buttons
    def accept(self):
        self.show_error("")
        try:
            v = self.values()
        except (WeldmentError, ValueError) as e:
            self.show_error(str(e))
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


# ---------------------------------------------------------------- member

class MemberPanel(FeaturePanel):
    title = "Structural member"
    transaction = "Structural member"

    def __init__(self, doc=None, obj=None, path=None, family=None):
        self._path = list(path or [])
        self._path_changed = False
        self._family0 = family
        super().__init__(doc, obj)

    def build(self):
        fams = Profiles.families()
        first = self._family0 or ("EN SHS" if "EN SHS" in fams else fams[0])
        self.family = _combo(fams, first)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("filter sizes, e.g. 100x100")
        self.sizes = QtWidgets.QListWidget()
        self.sizes.setMinimumHeight(120)
        self.anchor = _combo(ANCHORS, "Centre")
        self.rotation = _spin(-360.0, 360.0, 0.0, 3, " deg")
        self.mirror = QtWidgets.QCheckBox("Mirror the profile about its height axis")
        self.path_label = _label(path_text(self._path))
        self.path_label.setObjectName("path")
        self.use_sel = QtWidgets.QPushButton("Use selection")
        self.use_sel.setObjectName("use_selection")
        self.fields.addRow("Profile family", self.family)
        self.fields.addRow("Find size", self.search)
        self.fields.addRow("Size", self.sizes)
        self.fields.addRow("Anchor point", self.anchor)
        self.fields.addRow("Rotation", self.rotation)
        self.fields.addRow("", self.mirror)
        self.fields.addRow("Path", self.path_label)
        self.fields.addRow("", self.use_sel)
        self.family.currentIndexChanged.connect(lambda _i: self.fill_sizes())
        self.search.textChanged.connect(lambda _t: self.fill_sizes(keep=True))
        self.use_sel.clicked.connect(self.take_selection)
        self.fill_sizes()

    def fill_sizes(self, keep=False, select=None):
        want = select or (self.size_value() if keep else None)
        needle = self.search.text().strip().lower().replace(" ", "")
        self.sizes.clear()
        for s in sorted_sizes(self.family.currentData()):
            if needle in s.lower().replace(" ", ""):
                self.sizes.addItem(s)
        rows = [self.sizes.item(i).text() for i in range(self.sizes.count())]
        if want in rows:
            self.sizes.setCurrentRow(rows.index(want))
        elif rows:
            self.sizes.setCurrentRow(0)

    def size_value(self):
        item = self.sizes.currentItem()
        return item.text() if item is not None else None

    def set_size(self, family, size):
        self.search.setText("")
        _set_combo(self.family, family)
        self.fill_sizes(select=size)

    def take_selection(self):
        self._path = selection_path()
        self._path_changed = True
        self.path_label.setText(path_text(self._path))

    def set_path(self, path):
        self._path = list(path)
        self._path_changed = True
        self.path_label.setText(path_text(self._path))

    def load(self, obj):
        self._path = [(o, list(s)) for o, s in obj.Path]
        self.path_label.setText(path_text(self._path))
        self.set_size(obj.Family, obj.Size)
        _set_combo(self.anchor, obj.Anchor)
        self.rotation.setValue(obj.Rotation.Value)
        self.mirror.setChecked(bool(obj.Mirror))

    def values(self):
        size = self.size_value()
        if size is None:
            raise WeldmentError("choose a size from the list (the filter shows no match)")
        return {
            "family": self.family.currentData(), "size": size, "path": list(self._path),
            "anchor": self.anchor.currentData(), "rotation": self.rotation.value(), "mirror": self.mirror.isChecked(),
        }

    def create(self, doc, v):
        return [WeldMember.make_member(doc, v["family"], v["size"], v["path"], anchor=v["anchor"],
                                       rotation=v["rotation"], mirror=v["mirror"])]

    def edit(self, obj, v):
        obj.Family, obj.Size = v["family"], v["size"]
        obj.Anchor, obj.Rotation, obj.Mirror = v["anchor"], v["rotation"], v["mirror"]
        if self._path_changed:
            obj.Path = [(p[0], list(p[1]) or [""]) for p in v["path"]]
        return [obj]


# ---------------------------------------------------------------- corner treatment

#: (key, label, WeldTrim kind)
CORNER_KINDS = [
    ("Mitre", "Mitre (both ends cut on the bisector)", "Mitre"),
    ("ButtA", "Butt, A trimmed to B", "Butt"),
    ("ButtB", "Butt, B trimmed to A", "ButtB"),
    ("Face", "Trim to a planar face", "Face"),
]
#: kinds an existing EndTrim can be switched between (one feature treats one end)
_EDIT_KINDS = [("Mitre", "Mitre"), ("ButtA", "Butt, trimmed to the other member"), ("Face", "Trim to a planar face"),
               ("Body", "Trim to a body")]


class CornerPanel(FeaturePanel):
    title = "Corner treatment"
    transaction = "Corner treatment"

    def __init__(self, doc=None, obj=None, a=None, b=None, face=None, kind=None):
        self.a, self.b, self.face = a, b, face    # members (chain heads); face = (object, "FaceN")
        self._kind0 = kind
        super().__init__(doc, obj)

    def build(self):
        self.kind = _combo([(k, label) for k, label, _ in CORNER_KINDS] if self.obj is None
                           else _EDIT_KINDS, self._kind0 or "Mitre")
        self.a_label = _label()
        self.a_label.setObjectName("member_a")
        self.seg_a = QtWidgets.QSpinBox()
        self.end_a = _combo(["Start", "End"], "End")
        self.b_label = _label()
        self.b_label.setObjectName("member_b")
        self.seg_b = QtWidgets.QSpinBox()
        self.end_b = _combo(["Start", "End"], "Start")
        self.fields.addRow("Treatment", self.kind)
        self.fields.addRow("Member A", self.a_label)
        self.fields.addRow("Segment of A", self.seg_a)
        self.fields.addRow("End of A", self.end_a)
        self.b_row_label = QtWidgets.QLabel("Member B")
        self.fields.addRow(self.b_row_label, self.b_label)
        self.fields.addRow("Segment of B", self.seg_b)
        self.fields.addRow("End of B", self.end_b)
        self.kind.currentIndexChanged.connect(lambda _i: self.refresh())
        if self.obj is None:
            self.guess_ends()
            self.refresh()

    def guess_ends(self):
        try:
            if self.a is not None and self.b is not None:
                ea, eb = joint_ends(self.a, self.b)
                _set_combo(self.end_a, ea)
                _set_combo(self.end_b, eb)
            elif self.a is not None and self.face is not None:
                _set_combo(self.end_a, end_facing(self.a, *self.face))
        except Exception:    # noqa: BLE001  (the user can still pick the ends; OK reports the real reason)
            pass

    def set_members(self, a, b=None, face=None):
        self.a, self.b, self.face = a, b, face
        self.guess_ends()
        self.refresh()

    def refresh(self):
        key = self.kind.currentData()
        face = key in ("Face",)
        self.a_label.setText(chain_label(self.a) if self.a is not None else "(select a member first)")
        if face:
            self.b_row_label.setText("Face")
            self.b_label.setText("%s.%s" % (self.face[0].Label, self.face[1]) if self.face else "(select a planar face)")
        else:
            self.b_row_label.setText("Member B")
            self.b_label.setText(chain_label(self.b) if self.b is not None else "(select the second member)")
        self.seg_b.setEnabled(not face)
        self.end_b.setEnabled(not face)

    def load(self, obj):
        self.a, self.b = obj.Base, obj.Other
        self.seg_a.setValue(obj.Segment)
        _set_combo(self.end_a, obj.End)
        self.seg_b.setValue(max(0, obj.OtherSegment))
        _set_combo(self.end_b, obj.OtherEnd)
        _set_combo(self.kind, {"Butt": "ButtA"}.get(obj.Kind, obj.Kind))
        if obj.Kind == "Face":
            self.face = (obj.Other, obj.OtherSub)
        self.a_label.setText(chain_label(obj.Base))
        self.refresh()
        if obj.Kind == "Body":
            self.b_row_label.setText("Body")
            self.b_label.setText(obj.Other.Label if obj.Other is not None else "")

    def values(self):
        key = self.kind.currentData()
        if self.a is None:
            raise WeldmentError("select the member to treat first")
        if key == "Face":
            if not self.face:
                raise WeldmentError("pick a planar face to trim to")
        elif self.b is None and self.obj is None:
            raise WeldmentError("select two members that meet at a point")
        return {"kind": key, "seg_a": self.seg_a.value(), "end_a": self.end_a.currentData(),
                "seg_b": self.seg_b.value(), "end_b": self.end_b.currentData()}

    def create(self, doc, v):
        a, b = self.a, self.b
        if v["kind"] == "Face":
            fo, fs = self.face
            return [WeldTrim.make_trim(doc, a, v["seg_a"], v["end_a"], "Face", fo, fs, name="FaceTrim")]
        api = {"Mitre": "Mitre", "ButtA": "Butt", "ButtB": "ButtB"}[v["kind"]]
        made = WeldTrim.make_corner(doc, a, v["seg_a"], v["end_a"], b, v["seg_b"], v["end_b"], api)
        return [o for o in made if o is not a and o is not b]

    def edit(self, obj, v):
        obj.Kind = {"ButtA": "Butt"}.get(v["kind"], v["kind"])
        obj.Segment, obj.End = v["seg_a"], v["end_a"]
        if v["kind"] == "Face":
            obj.Other, obj.OtherSub = self.face
        elif v["kind"] in ("Mitre", "ButtA"):
            obj.OtherSegment, obj.OtherEnd = v["seg_b"], v["end_b"]
        return [obj]


# ---------------------------------------------------------------- gusset

class GussetPanel(FeaturePanel):
    title = "Gusset"
    transaction = "Gusset"

    def __init__(self, doc=None, obj=None, a=None, b=None):
        self.a, self.b = a, b
        super().__init__(doc, obj)

    def build(self):
        self.a_label = _label(chain_label(self.a) if self.a is not None else "(select two members)")
        self.a_label.setObjectName("member_a")
        self.b_label = _label(chain_label(self.b))
        self.b_label.setObjectName("member_b")
        self.seg_a = QtWidgets.QSpinBox()
        self.end_a = _combo(["Start", "End"], "Start")
        self.seg_b = QtWidgets.QSpinBox()
        self.end_b = _combo(["Start", "End"], "Start")
        self.shape = _combo([("Triangular", "Triangular (sharp apex)"), ("Chamfered", "Chamfered apex")], "Triangular")
        self.leg1 = _spin(0.0, 100000.0, 100.0)
        self.leg2 = _spin(0.0, 100000.0, 100.0)
        self.chamfer = _spin(0.0, 100000.0, 15.0)
        self.thickness = _spin(0.0, 1000.0, 8.0)
        self.placement = _combo(WeldGusset.PLACEMENTS, "Centred")
        for label, w in (("Member A", self.a_label), ("Segment of A", self.seg_a), ("End of A", self.end_a),
                         ("Member B", self.b_label), ("Segment of B", self.seg_b), ("End of B", self.end_b),
                         ("Plate shape", self.shape), ("Leg d1 along A", self.leg1), ("Leg d2 along B", self.leg2),
                         ("Chamfer", self.chamfer), ("Thickness", self.thickness), ("Placement", self.placement)):
            self.fields.addRow(label, w)
        self.shape.currentIndexChanged.connect(lambda _i: self.refresh())
        if self.obj is None and self.a is not None and self.b is not None:
            try:
                ea, eb = joint_ends(self.a, self.b)
                _set_combo(self.end_a, ea)
                _set_combo(self.end_b, eb)
            except Exception:    # noqa: BLE001
                pass
        self.refresh()

    def refresh(self):
        self.chamfer.setEnabled(self.shape.currentData() == "Chamfered")

    def load(self, obj):
        self.a, self.b = obj.MemberA, obj.MemberB
        self.a_label.setText(chain_label(obj.MemberA))
        self.b_label.setText(chain_label(obj.MemberB))
        self.seg_a.setValue(obj.SegmentA)
        self.seg_b.setValue(obj.SegmentB)
        _set_combo(self.end_a, obj.EndA)
        _set_combo(self.end_b, obj.EndB)
        self.leg1.setValue(obj.Leg1.Value)
        self.leg2.setValue(obj.Leg2.Value)
        self.thickness.setValue(obj.Thickness.Value)
        self.chamfer.setValue(obj.Chamfer.Value or self.chamfer.value())
        _set_combo(self.shape, "Chamfered" if obj.Chamfer.Value > 0 else "Triangular")
        _set_combo(self.placement, obj.PlatePlacement)
        self.refresh()

    def values(self):
        if self.a is None or self.b is None:
            raise WeldmentError("select the two members that meet at the joint first")
        chamfered = self.shape.currentData() == "Chamfered"
        return {"seg_a": self.seg_a.value(), "end_a": self.end_a.currentData(), "seg_b": self.seg_b.value(),
                "end_b": self.end_b.currentData(), "leg1": self.leg1.value(), "leg2": self.leg2.value(),
                "thickness": self.thickness.value(), "chamfer": self.chamfer.value() if chamfered else 0.0,
                "placement": self.placement.currentData()}

    def create(self, doc, v):
        return [WeldGusset.make_gusset(doc, self.a, v["seg_a"], v["end_a"], self.b, v["seg_b"], v["end_b"],
                                       v["leg1"], v["leg2"], v["thickness"], v["chamfer"], v["placement"])]

    def edit(self, obj, v):
        obj.SegmentA, obj.EndA, obj.SegmentB, obj.EndB = v["seg_a"], v["end_a"], v["seg_b"], v["end_b"]
        obj.Leg1, obj.Leg2, obj.Thickness, obj.Chamfer = v["leg1"], v["leg2"], v["thickness"], v["chamfer"]
        obj.PlatePlacement = v["placement"]
        return [obj]


# ---------------------------------------------------------------- end cap

class EndCapPanel(FeaturePanel):
    title = "End cap"
    transaction = "End cap"

    def __init__(self, doc=None, obj=None, base=None, end="End"):
        self.base, self._end0 = base, end
        super().__init__(doc, obj)

    def build(self):
        self.base_label = _label(chain_label(self.base) if self.base is not None else "(select a member)")
        self.base_label.setObjectName("member")
        self.segment = QtWidgets.QSpinBox()
        self.end = _combo(["Start", "End"], self._end0)
        self.mode = _combo([("Outset", "Outset (on the end face)"), ("Inset", "Inset (inside the bore)")], "Outset")
        self.thickness = _spin(0.0, 1000.0, 8.0)
        self.recess = _spin(0.0, 1000.0, 0.0)
        self.chamfer = _spin(0.0, 1000.0, 0.0)
        for label, w in (("Member", self.base_label), ("Segment", self.segment), ("End", self.end), ("Mode", self.mode),
                         ("Thickness", self.thickness), ("Recess", self.recess), ("Corner chamfer", self.chamfer)):
            self.fields.addRow(label, w)
        self.mode.currentIndexChanged.connect(lambda _i: self.refresh())
        self.refresh()

    def refresh(self):
        self.recess.setEnabled(self.mode.currentData() == "Inset")

    def load(self, obj):
        self.base = obj.Base
        self.base_label.setText(chain_label(obj.Base))
        self.segment.setValue(obj.Segment)
        _set_combo(self.end, obj.End)
        _set_combo(self.mode, obj.Mode)
        self.thickness.setValue(obj.Thickness.Value)
        self.recess.setValue(obj.Recess.Value)
        self.chamfer.setValue(obj.Chamfer.Value)
        self.refresh()

    def values(self):
        if self.base is None:
            raise WeldmentError("select the member to cap first")
        inset = self.mode.currentData() == "Inset"
        return {"segment": self.segment.value(), "end": self.end.currentData(), "mode": self.mode.currentData(),
                "thickness": self.thickness.value(), "recess": self.recess.value() if inset else 0.0,
                "chamfer": self.chamfer.value()}

    def create(self, doc, v):
        return [WeldEndCap.make_end_cap(doc, self.base, v["segment"], v["end"], v["thickness"], v["mode"],
                                        v["recess"], v["chamfer"])]

    def edit(self, obj, v):
        obj.Segment, obj.End, obj.Mode = v["segment"], v["end"], v["mode"]
        obj.Thickness, obj.Recess, obj.Chamfer = v["thickness"], v["recess"], v["chamfer"]
        return [obj]


# ---------------------------------------------------------------- weld bead

class BeadPanel(FeaturePanel):
    title = "Weld bead"
    transaction = "Weld bead"

    def __init__(self, doc=None, obj=None, bodies=None, edges=None):
        bodies = list(bodies or [])
        self.body1 = bodies[0] if len(bodies) > 0 else None
        self.body2 = bodies[1] if len(bodies) > 1 else None
        self._edges = list(edges or [])
        self._edges_changed = False
        super().__init__(doc, obj)

    def build(self):
        self.b1_label = _label()
        self.b1_label.setObjectName("body1")
        self.b2_label = _label()
        self.b2_label.setObjectName("body2")
        self.edge_list = QtWidgets.QListWidget()
        self.edge_list.setObjectName("edges")
        self.edge_list.setMaximumHeight(110)
        self.mode = _combo(WeldBead.MODES, "Leg z")
        self.size = _spin(0.0, 1000.0, 5.0, 3)
        self.hint = _label()
        self.use_sel = QtWidgets.QPushButton("Use selection")
        self.use_sel.setObjectName("use_selection")
        for label, w in (("Body 1", self.b1_label), ("Body 2", self.b2_label), ("Edges", self.edge_list),
                         ("Size is", self.mode), ("Size", self.size), ("", self.hint), ("", self.use_sel)):
            self.fields.addRow(label, w)
        self.mode.currentIndexChanged.connect(lambda _i: self.refresh())
        self.use_sel.clicked.connect(self.take_selection)
        self.size.valueChanged.connect(lambda _v: self.refresh())
        self.refresh()

    def refresh(self):
        self.b1_label.setText(self.body1.Label if self.body1 is not None else "(select two bodies)")
        self.b2_label.setText(self.body2.Label if self.body2 is not None else "")
        self.edge_list.clear()
        for o, n in self._edges:
            self.edge_list.addItem("%s.%s" % (o.Label, n))
        size = self.size.value()
        if self.mode.currentData() == "Leg z":
            self.hint.setText("Throat a = %.3f mm" % WeldBead.throat_from_leg(size))
        else:
            self.hint.setText("Leg z = %.3f mm" % WeldBead.leg_from_throat(size))

    def take_selection(self):
        bodies, edges = selection_bead()
        self.set_selection(bodies, edges)

    def set_selection(self, bodies, edges):
        self.body1 = bodies[0] if len(bodies) > 0 else None
        self.body2 = bodies[1] if len(bodies) > 1 else None
        self._edges = list(edges)
        self._edges_changed = True
        self.refresh()

    def load(self, obj):
        self.body1, self.body2 = obj.Body1, obj.Body2
        self._edges = [(o, n) for o, subs in obj.Edges for n in subs]
        _set_combo(self.mode, obj.SizeMode)
        self.size.setValue(obj.Size.Value)
        self.refresh()

    def values(self):
        if self.body1 is None or self.body2 is None:
            raise WeldmentError("select the two bodies the weld joins")
        if not self._edges:
            raise WeldmentError("select the edges where the bodies meet (Ctrl-click them), then press Use selection")
        return {"size": self.size.value(), "mode": self.mode.currentData()}

    def create(self, doc, v):
        return [WeldBead.make_bead(doc, self.body1, self.body2, self._edges, v["size"], v["mode"])]

    def edit(self, obj, v):
        obj.SizeMode, obj.Size = v["mode"], v["size"]
        if self._edges_changed:
            obj.Body1, obj.Body2 = self.body1, self.body2
            obj.Edges = [(o, [n]) for o, n in self._edges]
        return [obj]


# ---------------------------------------------------------------- cut list

MEMBER_HEADERS = {
    "item": "Item", "quantity": "Qty", "family": "Family", "size": "Size", "material": "Material",
    "cut_length_mm": "Length mm", "start_angle_deg": "Start deg", "end_angle_deg": "End deg",
    "start_end": "Start end", "end_end": "End end", "mass_per_m_kg": "kg/m", "mass_each_kg": "kg each",
    "mass_total_kg": "kg total", "marks": "Marks",
}
PLATE_HEADERS = {
    "item": "Item", "quantity": "Qty", "type": "Plate", "thickness_mm": "Thick mm", "length_mm": "Length mm",
    "width_mm": "Width mm", "area_mm2": "Area mm2", "material": "Material", "density_kg_m3": "kg/m3",
    "mass_each_kg": "kg each", "mass_total_kg": "kg total",
}
WELD_HEADERS = {"leg_z_mm": "Leg z mm", "throat_a_mm": "Throat a mm", "bead_count": "Beads",
                "total_length_mm": "Length mm", "mass_kg": "Weld metal kg"}


#: decimals shown in the tables (the CSV keeps the full precision of ``WeldCutList.to_csv``)
_SHOWN = {"cut_length_mm": 1, "start_angle_deg": 1, "end_angle_deg": 1, "mass_per_m_kg": 2, "mass_each_kg": 2,
          "mass_total_kg": 2, "thickness_mm": 1, "length_mm": 1, "width_mm": 1, "area_mm2": 0, "density_kg_m3": 0,
          "leg_z_mm": 1, "throat_a_mm": 2, "total_length_mm": 1, "mass_kg": 3}


def _shown(col, v):
    if v is None:
        return ""
    if col in _SHOWN:
        return "%.*f" % (_SHOWN[col], v + 0.0)
    return str(v)


def default_cut_objects(doc):
    """Every member chain head, plate and weld bead of a document (what an empty selection lists)."""
    out = []
    for o in doc.Objects:
        t = type_name(o)
        if t in ("Member", "EndTrim"):
            if not any(WeldTrim.is_trim(p) and p.Base is o for p in o.InList):
                out.append(o)
        elif t in ("Gusset", "EndCap", "WeldBead"):
            out.append(o)
    return out


class CutListPanel:
    """The cut list as three tables (members, plates, welds) with a CSV export; read-only, no document change."""

    title = "Cut list"

    def __init__(self, doc=None, objects=None):
        self.doc = doc if doc is not None else FreeCAD.ActiveDocument
        if self.doc is None:
            raise WeldmentError("open a document first")
        self.objects = list(objects) if objects else default_cut_objects(self.doc)
        self.rows = []
        self.done = False
        self.ask_path = self._ask_path
        self.form = QtWidgets.QWidget()
        self.form.setObjectName("Weldment_CutListPanel")
        self.form.setWindowTitle(self.title)
        lay = QtWidgets.QVBoxLayout(self.form)
        self.error = _label()
        self.error.setObjectName("error")
        self.error.setStyleSheet("color: #b00020;")
        self.error.hide()
        lay.addWidget(self.error)
        self.sections = {}
        for key, heading in (("members", "Members"), ("plates", "Plates"), ("welds", "Welds")):
            head = QtWidgets.QLabel(heading)
            f = head.font()
            f.setBold(True)
            head.setFont(f)
            table = QtWidgets.QTableWidget(0, 0)
            table.setObjectName("table_" + key)
            table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            table.verticalHeader().setVisible(False)
            table.horizontalHeader().setStretchLastSection(True)
            lay.addWidget(head)
            lay.addWidget(table)
            self.sections[key] = (head, table)
        self.export_button = QtWidgets.QPushButton("Export CSV...")
        self.export_button.setObjectName("export")
        self.export_button.clicked.connect(self.export_clicked)
        lay.addWidget(self.export_button)
        self.refresh()

    def refresh(self):
        self.show_error("")
        try:
            self.rows = WeldCutList.cut_list(self.objects)
        except (WeldmentError, ValueError, RuntimeError) as e:
            self.rows = []
            self.show_error(str(e.args[0] if e.args else e))
        members = [r for r in self.rows if "type" not in r and r.get("kind") != "weld"]
        plates = [r for r in self.rows if "type" in r]
        welds = [r for r in self.rows if r.get("kind") == "weld"]
        self._fill("members", WeldCutList.COLUMNS + ["marks"], MEMBER_HEADERS, members)
        self._fill("plates", WeldCutList.PLATE_COLUMNS, PLATE_HEADERS, plates)
        self._fill("welds", WeldCutList.WELD_COLUMNS, WELD_HEADERS, welds)
        self.export_button.setEnabled(bool(self.rows))

    def _fill(self, key, cols, headers, rows):
        head, table = self.sections[key]
        table.clear()
        table.setColumnCount(len(cols))
        table.setHorizontalHeaderLabels([headers[c] for c in cols])
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, col in enumerate(cols):
                v = row.get(col)
                text = ", ".join(v) if col == "marks" else _shown(col, v)
                item = QtWidgets.QTableWidgetItem(text)
                item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
                if isinstance(v, (int, float)):
                    item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                table.setItem(r, c, item)
        table.resizeColumnsToContents()
        head.setVisible(bool(rows))
        table.setVisible(bool(rows))

    def show_error(self, text):
        self.error.setText(text)
        self.error.setVisible(bool(text))

    def error_text(self):
        return self.error.text()

    def csv_text(self):
        return WeldCutList.to_csv(self.rows)

    def export_csv(self, path):
        """Write exactly ``WeldCutList.to_csv`` of the listed rows to ``path``; returns the text."""
        text = self.csv_text()
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return text

    def _ask_path(self):
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(self.form, "Export cut list", "cutlist.csv", "CSV (*.csv)")
        return path

    def export_clicked(self):
        path = self.ask_path()
        if not path:
            return
        try:
            self.export_csv(path)
        except OSError as e:
            self.show_error("could not write %s: %s" % (path, e.strerror or e))

    def getStandardButtons(self):
        close = QtWidgets.QDialogButtonBox.StandardButton.Close
        return int(getattr(close, "value", close))

    def accept(self):
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


PANELS = {"member": MemberPanel, "corner": CornerPanel, "gusset": GussetPanel, "endcap": EndCapPanel,
          "bead": BeadPanel, "cutlist": CutListPanel}
