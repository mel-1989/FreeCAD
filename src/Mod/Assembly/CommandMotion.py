# SPDX-License-Identifier: LGPL-2.1-or-later
"""Dynamic-analysis commands (cad-fork G06 slice 6b).

Assembly_CreateMotionStudy makes a dynamic analysis (a ``MotionStudy``, see ``AssemblyMotion``) in the active or selected
assembly and opens its panel; Assembly_EditMotionStudy opens the panel on the selected analysis; Assembly_AddMotionGravity,
Assembly_AddMotionForce, Assembly_AddMotionTorque, Assembly_AddMotionSpring and Assembly_AddMotionDamper add a load to it.

Wording. The study object is a "dynamic analysis" in every label, and the commands read "Create dynamic analysis", "Add
gravity", "Add force" and so on. "Motion Study" is the name of a feature in a commercial CAD product, so the labels use this
project's own words; the document objects keep the names of the API (``MotionStudy``, ``MotionLoad``).

Picking. A point load is placed by selecting geometry in the 3D view: a vertex is used as it is, a face or an edge by the
point that was clicked (or, when there is none, its centre). The selection is read with the sub-names unresolved and the
component found by FreeCAD's own path resolution, so a part reached through an ``App::Link`` or a sub-assembly path works.
A force or torque needs one part, a spring or damper two different parts. Each add is one undo transaction (the API's). The
values (a force vector, a stiffness) are asked in a small dialog whose function ``ask_values`` tests replace.

Icons are SVGs next to the module's other command icons, referenced by path (the compiled icon resource would need a rebuild
of the GUI library).
"""

import os

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtWidgets
from PySide.QtCore import QT_TRANSLATE_NOOP

import AssemblyMotion as MO

translate = App.Qt.translate
ICONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")


# ----------------------------------------------------------------- questions

def ask_values(parent, title, fields):
    """Ask for numbers. ``fields`` is a list of ``(key, label, default, unit)``; returns ``{key: value}`` or None when cancelled.
    Tests replace this."""
    dlg = QtWidgets.QDialog(parent)
    dlg.setWindowTitle(title)
    form = QtWidgets.QFormLayout(dlg)
    boxes = {}
    for key, label, default, unit in fields:
        box = QtWidgets.QDoubleSpinBox()
        box.setDecimals(6)
        box.setRange(-1e12, 1e12)
        box.setValue(default)
        box.setSuffix(" " + unit if unit else "")
        box.setAccessibleName(label)
        boxes[key] = box
        form.addRow(label, box)
    bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    form.addRow(bb)
    if not dlg.exec_():
        return None
    return {k: b.value() for k, b in boxes.items()}


def report(parent, text):
    """Tell the user why a command did nothing. Tests replace this."""
    QtWidgets.QMessageBox.warning(parent, translate("Assembly", "Dynamic analysis"), text)


# ----------------------------------------------------------------- context

def _assembly_of_object(obj):
    if obj is None:
        return None
    if obj.isDerivedFrom("Assembly::AssemblyObject"):
        return obj
    if MO.is_study(obj) or MO.is_load(obj) or MO.is_mass(obj):
        study = MO.study_of(obj)
        try:
            return MO.assembly_of(study) if study is not None else None
        except MO.MotionError:
            return None
    for o in obj.InList:
        if o.isDerivedFrom("Assembly::AssemblyObject"):
            return o
    return None


def selected_study():
    """The analysis named by the selection (the study itself or one of its loads or masses), else None."""
    for sel in Gui.Selection.getSelectionEx("", 0):
        for obj in [sel.Object] + _resolved(sel):
            if MO.is_study(obj):
                return obj
            if MO.is_load(obj) or MO.is_mass(obj):
                study = MO.study_of(obj)
                if study is not None:
                    return study
    return None


def _resolved(sel):
    out = []
    for sub in list(sel.SubElementNames) or [""]:
        try:
            o = sel.Object.resolve(sub)[0]
        except Exception:                       # noqa: BLE001
            continue
        if o is not None:
            out.append(o)
    return out


def assembly_context():
    """The assembly the commands act in: the selected assembly or the one that holds the selected objects, else the assembly
    being edited."""
    study = selected_study()
    if study is not None:
        try:
            return MO.assembly_of(study)
        except MO.MotionError:
            return None
    for sel in Gui.Selection.getSelectionEx("", 0):
        for obj in [sel.Object] + _resolved(sel):
            asm = _assembly_of_object(obj)
            if asm is not None:
                return asm
    import UtilsAssembly
    return UtilsAssembly.activeAssembly()


def target_study(asm):
    """The analysis a load is added to: the selected one, else the only one of the assembly."""
    study = selected_study()
    if study is not None:
        return study
    found = MO.studies(asm) if asm is not None else []
    return found[0] if len(found) == 1 else None


class Pick:
    """A component of the assembly and a point on it."""

    def __init__(self, comp, local, element):
        self.comp, self.local, self.element = comp, local, element

    def __repr__(self):
        return "Pick(%s, %s, %r)" % (self.comp.Name, tuple(self.local), self.element)


def _component_of(asm, obj, sub):
    """The component of ``asm`` that the selected (object, sub-name) goes through, or None. The sub-name is split at dots
    and matched against the assembly's components (the objects its solver may move, also those inside a sub-assembly), so
    a path through a link or a sub-assembly resolves to the component the assembly's study can name."""
    comps = {o.Name: o for o in MO.live_components(asm)}
    tokens = [t for t in (sub or "").split(".")]
    if obj is asm:
        start = 0
    elif asm.Name in tokens:
        start = tokens.index(asm.Name) + 1
    elif obj.Name in comps:
        return comps[obj.Name]
    else:
        return None
    for t in tokens[start:]:
        if t in comps:
            return comps[t]
    return None


def picked_components(asm):
    """(component, object, sub-name, picked point or None) of every selected thing that lies on a component of ``asm``."""
    out = []
    for sel in Gui.Selection.getSelectionEx("", 0):
        pts = list(getattr(sel, "PickedPoints", []) or [])
        for i, sub in enumerate(list(sel.SubElementNames) or [""]):
            comp = _component_of(asm, sel.Object, sub)
            if comp is not None:
                out.append((comp, sel.Object, sub, pts[i] if i < len(pts) else None))
    return out


def _chain(obj, sub, comp):
    """Placement of ``comp`` in the frame of the selected root object (the root's own placement left out)."""
    if obj is comp:
        return App.Placement()
    tokens = (sub or "").split(".")
    prefix = ".".join(tokens[:tokens.index(comp.Name) + 1]) + "."
    return obj.getSubObject(prefix, retType=3, transform=False)


def _local_point(comp, obj, sub, picked):
    """``(point in the component's frame, element name)`` of the selected element: a vertex as it is, a face or an edge by the
    click (when there is one and it lies on the element; a selection made from a script carries (0, 0, 0)) or by its centre;
    ``(None, "")`` for a selection without an element (the whole component)."""
    element = (sub or "").rsplit(".", 1)[-1]
    if not (element and element[0] in "FEV" and element[-1].isdigit()):
        return None, ""
    shape = Part.getShape(obj, sub, needSubElement=True, transform=False, retType=0)     # in the selected object's frame
    to_comp = App.Placement() if obj is comp else _chain(obj, sub, comp).inverse()
    if shape.ShapeType == "Vertex":
        return to_comp.multVec(shape.Point), element
    if picked is not None:
        click = comp.Placement.inverse().multVec(App.Vector(picked)) if obj is comp else App.Vector(picked)
        if shape.distToShape(Part.Vertex(click))[0] < 1e-3:
            return to_comp.multVec(click), element
    if shape.ShapeType == "Face":
        c = shape.CenterOfMass
    else:
        c = shape.valueAt((shape.FirstParameter + shape.LastParameter) / 2)
    return to_comp.multVec(c), element


def points(asm):
    """``[Pick]`` for the selection: the component and the point in the component's own frame, mm."""
    out = []
    for comp, obj, sub, picked in picked_components(asm):
        local, element = _local_point(comp, obj, sub, picked)
        out.append(Pick(comp, App.Vector(0, 0, 0) if local is None else local, element))
    return out


# ----------------------------------------------------------------- commands

def open_panel(study):
    """Open the panel on ``study`` unless another task dialog is open. Returns the panel or None."""
    from MotionStudyPanel import TaskMotionStudy, panel_open

    if Gui.Control.activeDialog() or panel_open():
        return None
    panel = TaskMotionStudy(study)
    dialog = Gui.Control.showDialog(panel)
    if dialog is not None:
        dialog.setAutoCloseOnDeletedDocument(True)
        dialog.setDocumentName(study.Document.Name)
    return panel


class _Command:
    name = ""
    icon = ""
    menu = ""
    tip = ""
    status = ""

    def GetResources(self):
        return {
            "Pixmap": os.path.join(ICONS, self.icon),
            "MenuText": self.menu,
            "ToolTip": self.tip,
            "StatusTip": self.status,
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        from MotionStudyPanel import panel_open

        if App.ActiveDocument is None or Gui.Control.activeDialog() or panel_open():
            return False
        return bool(self.can_act())

    def can_act(self):
        return False


class CommandCreateMotionStudy(_Command):
    icon = "Assembly_CreateMotionStudy.svg"
    menu = QT_TRANSLATE_NOOP("Assembly_CreateMotionStudy", "Create dynamic analysis")
    tip = QT_TRANSLATE_NOOP(
        "Assembly_CreateMotionStudy",
        "<p>Creates a dynamic analysis in the assembly: the loads (gravity, forces, springs, dampers) and masses of its parts, "
        "integrated over a time span with the assembly's joints as constraints.</p>"
        "<p>The panel sets the time span and step, runs the analysis, plays the result in the 3D view and plots a quantity.</p>")
    status = QT_TRANSLATE_NOOP("Assembly_CreateMotionStudy", "Create a dynamic analysis of the assembly's motion under loads")

    def can_act(self):
        return assembly_context() is not None

    def Activated(self):
        asm = assembly_context()
        if asm is None:
            return
        study = MO.create_study(asm, name="DynamicAnalysis")
        open_panel(study)


class CommandEditMotionStudy(_Command):
    icon = "Assembly_EditMotionStudy.svg"
    menu = QT_TRANSLATE_NOOP("Assembly_EditMotionStudy", "Edit dynamic analysis")
    tip = QT_TRANSLATE_NOOP(
        "Assembly_EditMotionStudy",
        "<p>Opens the panel of the selected dynamic analysis, or of the assembly's only one: time span and step, the loads, "
        "running, playback and the plot.</p>")
    status = QT_TRANSLATE_NOOP("Assembly_EditMotionStudy", "Open the panel of the selected dynamic analysis")

    def can_act(self):
        return target_study(assembly_context()) is not None

    def Activated(self):
        study = target_study(assembly_context())
        if study is not None:
            open_panel(study)


class _AddLoad(_Command):
    need = 0            # parts to pick: 0 none, 1 one, 2 two different ones

    def picks_ok(self, asm):
        if self.need == 0:
            return True
        comps = [c.Name for c, *_ in picked_components(asm)]
        if self.need == 1:
            return len(comps) == 1 or (len(comps) > 1 and len(set(comps)) == 1)
        return len(comps) == 2 and comps[0] != comps[1]

    def can_act(self):
        asm = assembly_context()
        study = target_study(asm)
        return study is not None and self.picks_ok(asm) and self.free(study)

    def free(self, study):
        return True

    def Activated(self):
        asm = assembly_context()
        study = target_study(asm)
        if study is None or not self.picks_ok(asm):
            return
        inner = [c.Name for c, *_ in picked_components(asm) if not MO.is_direct(asm, c)]
        if inner:
            report(Gui.getMainWindow(), translate(
                "Assembly", "%s is inside a sub-assembly. A dynamic analysis takes loads only on the parts of the assembly "
                "itself; pick a part of this assembly instead.") % ", ".join(sorted(set(inner))))
            return
        self.add(study, points(asm))


class CommandAddMotionGravity(_AddLoad):
    icon = "Assembly_AddMotionGravity.svg"
    menu = QT_TRANSLATE_NOOP("Assembly_AddMotionGravity", "Add gravity")
    tip = QT_TRANSLATE_NOOP(
        "Assembly_AddMotionGravity",
        "<p>Adds gravity (9.80665 m/s² towards -Z) to the dynamic analysis. Edit the load's Value to change the direction.</p>")
    status = QT_TRANSLATE_NOOP("Assembly_AddMotionGravity", "Add gravity to the dynamic analysis")

    def free(self, study):
        return not any(ld.Kind == "Gravity" for ld in MO.loads(study))

    def add(self, study, picks):
        MO.add_gravity(study)


class CommandAddMotionForce(_AddLoad):
    need = 1
    icon = "Assembly_AddMotionForce.svg"
    menu = QT_TRANSLATE_NOOP("Assembly_AddMotionForce", "Add force")
    tip = QT_TRANSLATE_NOOP(
        "Assembly_AddMotionForce",
        "<p>Adds a constant force to the dynamic analysis, applied at the point you select on a part (a vertex, or a point "
        "on a face or edge). The force is asked in newtons, in the world frame.</p>")
    status = QT_TRANSLATE_NOOP("Assembly_AddMotionForce", "Add a force at a point on a part")

    def add(self, study, picks):
        pick = picks[0]
        v = ask_values(Gui.getMainWindow(), translate("Assembly", "Add force"),
                       [("x", translate("Assembly", "Force X"), 0.0, "N"), ("y", translate("Assembly", "Force Y"), 0.0, "N"),
                        ("z", translate("Assembly", "Force Z"), -10.0, "N")])
        if v is not None:
            MO.add_force(study, pick.comp, tuple(pick.local), (v["x"], v["y"], v["z"]))


class CommandAddMotionTorque(_AddLoad):
    need = 1
    icon = "Assembly_AddMotionTorque.svg"
    menu = QT_TRANSLATE_NOOP("Assembly_AddMotionTorque", "Add torque")
    tip = QT_TRANSLATE_NOOP(
        "Assembly_AddMotionTorque",
        "<p>Adds a constant torque on the selected part to the dynamic analysis. The torque is asked in newton metres, in the "
        "world frame.</p>")
    status = QT_TRANSLATE_NOOP("Assembly_AddMotionTorque", "Add a torque on a part")

    def add(self, study, picks):
        v = ask_values(Gui.getMainWindow(), translate("Assembly", "Add torque"),
                       [("x", translate("Assembly", "Torque X"), 0.0, "N·m"),
                        ("y", translate("Assembly", "Torque Y"), 0.0, "N·m"),
                        ("z", translate("Assembly", "Torque Z"), 1.0, "N·m")])
        if v is not None:
            MO.add_torque(study, picks[0].comp, (v["x"], v["y"], v["z"]))


class CommandAddMotionSpring(_AddLoad):
    need = 2
    icon = "Assembly_AddMotionSpring.svg"
    menu = QT_TRANSLATE_NOOP("Assembly_AddMotionSpring", "Add spring")
    tip = QT_TRANSLATE_NOOP(
        "Assembly_AddMotionSpring",
        "<p>Adds a linear spring between a point on each of two parts: select a vertex, or a point on a face or edge, on each. "
        "The stiffness is asked in newtons per metre and the free length in millimetres (the current distance is proposed).</p>")
    status = QT_TRANSLATE_NOOP("Assembly_AddMotionSpring", "Add a spring between points on two parts")

    def add(self, study, picks):
        a, b = picks
        gap = (a.comp.Placement.multVec(a.local) - b.comp.Placement.multVec(b.local)).Length
        v = ask_values(Gui.getMainWindow(), translate("Assembly", "Add spring"),
                       [("k", translate("Assembly", "Stiffness"), 1000.0, "N/m"),
                        ("l0", translate("Assembly", "Free length"), round(gap, 6), "mm")])
        if v is not None:
            MO.add_spring(study, a.comp, tuple(a.local), b.comp, tuple(b.local), v["k"], v["l0"])


class CommandAddMotionDamper(_AddLoad):
    need = 2
    icon = "Assembly_AddMotionDamper.svg"
    menu = QT_TRANSLATE_NOOP("Assembly_AddMotionDamper", "Add damper")
    tip = QT_TRANSLATE_NOOP(
        "Assembly_AddMotionDamper",
        "<p>Adds a linear damper between a point on each of two parts: select a vertex, or a point on a face or edge, on each. "
        "The damping is asked in newton seconds per metre.</p>")
    status = QT_TRANSLATE_NOOP("Assembly_AddMotionDamper", "Add a damper between points on two parts")

    def add(self, study, picks):
        a, b = picks
        v = ask_values(Gui.getMainWindow(), translate("Assembly", "Add damper"),
                       [("c", translate("Assembly", "Damping"), 10.0, "N·s/m")])
        if v is not None:
            MO.add_damper(study, a.comp, tuple(a.local), b.comp, tuple(b.local), v["c"])


COMMANDS = {
    "Assembly_CreateMotionStudy": CommandCreateMotionStudy,
    "Assembly_EditMotionStudy": CommandEditMotionStudy,
    "Assembly_AddMotionGravity": CommandAddMotionGravity,
    "Assembly_AddMotionForce": CommandAddMotionForce,
    "Assembly_AddMotionTorque": CommandAddMotionTorque,
    "Assembly_AddMotionSpring": CommandAddMotionSpring,
    "Assembly_AddMotionDamper": CommandAddMotionDamper,
}
for _name, _cls in COMMANDS.items():
    Gui.addCommand(_name, _cls())
