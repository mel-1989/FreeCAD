# SPDX-License-Identifier: LGPL-2.1-or-later
"""Assembly-level features (G06 slice 4a): cuts and holes that belong to the assembly.

An assembly feature is an ``App::FeaturePython`` object that lives inside an Assembly. It holds
a tool (an extruded cut from a sketch or profile, a box or cylinder, or a solid tool) or a hole
(position, axis, diameter, depth or through all, optional counterbore), a scope (every
component the feature reaches, or an explicit list of selectors) and ``Suppressed``. The tool
is given in the assembly's own frame (the frame the placements of its children are written in).

The part objects and documents are never modified. The cut shapes exist only in the data of the
feature: for every component a cut reaches, the feature keeps the cut shape in the component's
local frame (``Proxy.results``), so it follows the component when it moves. ``effective_shape``
is what consumers read (the interference check does, through ``Interference.resolve``): the
component's shape in assembly placement with every active feature applied in assembly order.
A suppressed or deleted feature leaves every component's shape exactly the original.

Components are the solid leaves ``Interference`` walks (a leaf reached through ``App::Link``,
a link array element, a sub-assembly or an ``App::Part``), each cut where it sits: two links to
one source are cut independently, and a link array element is cut by where it sits. Hidden
components are cut like visible ones (visibility is display state, not geometry). A tool that
touches a component on a face only removes no volume and leaves it unchanged; the report never
lists a component whose removed volume is not above the volume tolerance as cut.

Behaviour sources, all public: OCCT Boolean operations (``BRepAlgoAPI_Cut`` and ``Common``,
through ``Part.Shape.cut`` and ``common``); FreeCAD's ``App::FeaturePython`` and ``App::Link``
documentation; hole and counterbore terminology as in the ISO 273 and ISO 15065 titles only.

Limits (slice 4a): features sit in the assembly's own group, not in nested sub-assemblies; a
profile for an extruded cut is a planar closed wire set (a sketch or a ``Part::Feature``); a
through-all tool is as long as the farthest bounding-box corner of the components needs;
mass properties and export are not wired to ``effective_shape`` yet (they read parts directly).
"""

import json
import math

import FreeCAD as App
import Part

import Interference as _itf
from AgentCore.verify.tolerances import resolve as _resolve_tol

SCHEMA = "cadfork.assembly.feature/1"
PROP_GROUP = "AssemblyFeature"
_INCLUDE_HIDDEN = True
_UNSET = object()


# ---------------------------------------------------------------- tool specs

def _pl_spec(pos=(0, 0, 0), axis=(0, 0, 1), angle=0.0):
    return {"pos": [float(x) for x in pos], "axis": [float(x) for x in axis], "angle": float(angle)}


def _pl(spec):
    return App.Placement(App.Vector(*spec["pos"]), App.Rotation(App.Vector(*spec["axis"]), spec["angle"]))


def box_tool(size, pos=(0, 0, 0), axis=(0, 0, 1), angle=0.0):
    """A box tool: ``size`` along the local x, y, z of the placement, corner at ``pos``."""
    return {"kind": "box", "size": [float(x) for x in size], "placement": _pl_spec(pos, axis, angle)}


def cylinder_tool(radius, height, pos=(0, 0, 0), direction=(0, 0, 1)):
    """A cylinder tool: base centre ``pos``, axis ``direction``."""
    return {"kind": "cylinder", "radius": float(radius), "height": float(height),
            "pos": [float(x) for x in pos], "direction": [float(x) for x in direction]}


def extrude_tool(direction, length=None, through=False, symmetric=False):
    """Extrude the profile (the feature's ``ToolObject``) along ``direction``.

    ``length`` is a distance in mm; ``through=True`` extrudes beyond every component instead;
    ``symmetric=True`` extrudes to both sides of the profile plane (``length`` is then the total).
    """
    if not through and not length:
        raise ValueError("an extruded cut needs a length or through=True")
    return {"kind": "extrude", "direction": [float(x) for x in direction],
            "length": None if length is None else float(length), "through": bool(through),
            "symmetric": bool(symmetric)}


def solid_tool():
    """The feature's ``ToolObject`` solid, in the assembly frame, is the tool."""
    return {"kind": "solid"}


# ----------------------------------------------------------- feature object

def _is_feature(obj):
    return isinstance(getattr(obj, "Proxy", None), AssemblyFeature)


class AssemblyFeature:
    """Proxy of the feature object. ``results`` is the in-memory cut data and never persisted."""

    def __init__(self, obj):
        obj.addProperty("App::PropertyString", "Kind", PROP_GROUP, "cut or hole")
        obj.addProperty("App::PropertyString", "Spec", PROP_GROUP, "Tool or hole parameters (JSON)")
        obj.addProperty("App::PropertyLinkGlobal", "ToolObject", PROP_GROUP, "Sketch, profile or solid tool")
        obj.addProperty("App::PropertyStringList", "Scope", PROP_GROUP,
                        "Component selectors the feature reaches; empty means every component")
        obj.addProperty("App::PropertyBool", "Suppressed", PROP_GROUP, "Switch the feature off")
        obj.addProperty("App::PropertyLinkList", "Dependencies", PROP_GROUP,
                        "Components whose placement a re-cut depends on")
        obj.setPropertyStatus("Dependencies", "Hidden")
        obj.Proxy = self
        self.results = None

    # recompute: drop the cache; the next read re-cuts from the current placements
    def execute(self, obj):
        self.results = None
        self.executions = getattr(self, "executions", 0) + 1

    def onDocumentRestored(self, obj):
        self.results = None

    def dumps(self):
        return None

    def loads(self, state):
        self.results = None
        return None

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        self.results = None


def _spec(feature):
    return json.loads(feature.Spec) if feature.Spec else {}


def _dependency_objects(assembly, upto=None):
    skip = ("Assembly::JointGroup", "Assembly::ViewGroup", "Assembly::BomGroup",
            "Assembly::SimulationGroup", "Assembly::SnapshotGroup", "App::Origin")
    out = []
    for o in assembly.Group:
        if o is upto:
            break
        if o.TypeId in skip:
            continue
        out.append(o)
    return out


def refresh_dependencies(feature):
    """Point ``Dependencies`` at the components in front of the feature so a placement edit re-cuts."""
    asm = feature.getParentGeoFeatureGroup()
    feature.Dependencies = _dependency_objects(asm, upto=feature) if asm is not None else []


def _make(assembly, name, kind, spec, tool_object, scope):
    feat = assembly.newObject("App::FeaturePython", name)
    AssemblyFeature(feat)
    feat.Kind = kind
    feat.Spec = json.dumps(spec, sort_keys=True)
    if tool_object is not None:
        feat.ToolObject = tool_object
    feat.Scope = [str(s) for s in (scope or [])]
    feat.Suppressed = False
    refresh_dependencies(feat)
    return feat


def make_cut(assembly, tool, name="AssemblyCut", scope=None, tool_object=None):
    """Add a cut feature (``tool`` from ``box_tool``, ``cylinder_tool``, ``extrude_tool``, ``solid_tool``)."""
    if tool["kind"] in ("extrude", "solid") and tool_object is None:
        raise ValueError("%s tool needs tool_object" % tool["kind"])
    return _make(assembly, name, "cut", {"tool": tool}, tool_object, scope)


def make_hole(assembly, position, axis, diameter, depth=None, through=False, cbore_diameter=None,
              cbore_depth=None, name="AssemblyHole", scope=None):
    """Add a hole: entry point ``position`` and drilling ``axis`` in the assembly frame.

    The hole is ``diameter`` wide and ``depth`` deep along the axis from the entry point, or
    through all components beyond it. An optional counterbore ``cbore_diameter`` x ``cbore_depth``
    (both from the entry point, diameter larger than the hole's) widens its mouth.
    """
    if not through and not depth:
        raise ValueError("a hole needs a depth or through=True")
    if bool(cbore_diameter) != bool(cbore_depth):
        raise ValueError("a counterbore needs both cbore_diameter and cbore_depth")
    if cbore_diameter and not cbore_diameter > diameter:
        raise ValueError("cbore_diameter must exceed the hole diameter")
    spec = {"position": [float(x) for x in position], "axis": [float(x) for x in axis],
            "diameter": float(diameter), "depth": None if depth is None else float(depth),
            "through": bool(through), "cbore_diameter": None if not cbore_diameter else float(cbore_diameter),
            "cbore_depth": None if not cbore_depth else float(cbore_depth)}
    return _make(assembly, name, "hole", spec, None, scope)


def set_suppressed(feature, value=True):
    feature.Suppressed = bool(value)
    feature.Proxy.results = None


def set_scope(feature, selectors):
    feature.Scope = [str(s) for s in selectors or []]
    feature.Proxy.results = None


def features(assembly, active_only=False):
    """The assembly's feature objects in assembly order (the order cuts are applied in)."""
    out = [o for o in assembly.Group if _is_feature(o)]
    return [o for o in out if not o.Suppressed] if active_only else out


# ------------------------------------------------------------------- tools

def _through_length(start, boxes):
    reach = 0.0
    for b in boxes:
        for x in (b.XMin, b.XMax):
            for y in (b.YMin, b.YMax):
                for z in (b.ZMin, b.ZMax):
                    reach = max(reach, (App.Vector(x, y, z) - start).Length)
    return reach + 1.0


def _profile_shape(assembly, obj):
    """The tool object's shape in the global frame (assembly placement applied)."""
    if obj in assembly.Group:
        shp = Part.getShape(assembly, obj.Name + ".", needSubElement=False, transform=True)
    else:
        shp = obj.Shape
    shp = shp.copy()  # never hand an operation geometry shared with the tool object
    if obj not in assembly.Group:
        shp.transformShape(assembly.Placement.toMatrix())
    return shp


def _hole_tool(spec, length):
    axis = App.Vector(*spec["axis"])
    pos = App.Vector(*spec["position"])
    depth = length if spec["through"] else spec["depth"]
    shaft = Part.makeCylinder(spec["diameter"] / 2.0, depth, pos, axis)
    if spec["cbore_diameter"]:
        bore = Part.makeCylinder(spec["cbore_diameter"] / 2.0, spec["cbore_depth"], pos, axis)
        shaft = shaft.fuse(bore).removeSplitter()
    return shaft


def _extrude(profile, spec, boxes):
    d = App.Vector(*spec["direction"])
    if d.Length == 0:
        raise ValueError("extrude direction is zero")
    d.normalize()
    centre = profile.BoundBox.Center
    length = _through_length(centre, boxes) if spec["through"] else spec["length"]
    faces = Part.makeFace(profile.Wires, "Part::FaceMakerBullseye") if profile.Wires else profile
    if spec["symmetric"]:
        if spec["through"]:
            return faces.extrude(d * length).fuse(faces.extrude(d * -length))
        faces = faces.copy()
        faces.translate(d * (-length / 2.0))
    return faces.extrude(d * length)


def _build_tool(assembly, feat, boxes):
    """The feature's tool as one shape in the global frame."""
    spec = _spec(feat)
    gl = assembly.Placement
    if feat.Kind == "hole":
        start = App.Vector(*spec["position"])
        length = _through_length(gl.multVec(start), boxes) if spec["through"] else None
        shp = _hole_tool(spec, length)
    else:
        t = spec["tool"]
        if t["kind"] == "box":
            shp = Part.makeBox(*t["size"])
            shp.Placement = _pl(t["placement"])
        elif t["kind"] == "cylinder":
            shp = Part.makeCylinder(t["radius"], t["height"], App.Vector(*t["pos"]), App.Vector(*t["direction"]))
        elif t["kind"] == "extrude":
            prof = _profile_shape(assembly, feat.ToolObject)
            return _extrude(prof, t, boxes)  # already global
        elif t["kind"] == "solid":
            return _profile_shape(assembly, feat.ToolObject)
        else:
            raise ValueError("unknown tool kind %r" % t["kind"])
    shp = shp.copy()
    shp.transformShape(_asm_matrix(assembly))
    return shp


# ------------------------------------------------------------------- scope

def _in_scope(feat, inst):
    sel = list(feat.Scope)
    return not sel or any(_itf._matches(inst, s) for s in sel)


def _active_features(assembly):
    return features(assembly, active_only=True)


def _hidden_ok(inst):
    return True


# --------------------------------------------------------------- signature

def _shape_desc(shp):
    bb = shp.BoundBox
    return (tuple(shp.Placement.Matrix.A), round(shp.Volume, 9), round(shp.Area, 9),
            round(bb.XMin, 9), round(bb.XMax, 9), round(bb.YMin, 9), round(bb.YMax, 9),
            round(bb.ZMin, 9), round(bb.ZMax, 9))


def _signature(assembly, feats, insts):
    fd = []
    for f in feats:
        tool = None
        if f.ToolObject is not None:
            try:
                tool = (f.ToolObject.Name, _shape_desc(_profile_shape(assembly, f.ToolObject)))
            except Exception:
                tool = (f.ToolObject.Name, None)
        fd.append((f.Name, f.Kind, f.Spec, tuple(f.Scope), bool(f.Suppressed), tool,
                   tuple(assembly.Placement.Matrix.A)))
    idesc = tuple((i.key, _shape_desc(i.shape)) for i in insts)
    return (tuple(fd), idesc)


# ------------------------------------------------------------------- cutting

def _removed_volume(inst, cur, tool, tol):
    """Volume of ``cur`` inside ``tool`` (exact Boolean common)."""
    if not cur.BoundBox.intersect(tool.BoundBox):
        return 0.0
    com = cur.common(tool)
    return float(com.Volume) if not com.isNull() else 0.0


def _cut_one(inst, cur, tool):
    return cur.cut(tool)


def _input_shape(cur, base, key):
    """What the next feature cuts: the result of the features before it."""
    return cur[key]


def _asm_matrix(assembly):
    return assembly.Placement.toMatrix()


def _to_local(shape, matrix):
    return shape.transformed(matrix.inverse())


def _from_local(local, matrix):
    return local.transformed(matrix)


def _publish(inst, shape):
    """Hook point: the cut shape stays in the feature's data. Nothing is written to a part."""
    return None


def _compute(assembly, insts):
    """Run every active feature over ``insts`` (raw global shapes); store results on the proxies."""
    feats = _active_features(assembly)
    for f in features(assembly):
        f.Proxy.results = None
    if not feats:
        return None
    tol = _resolve_tol(None)["interference_volume_mm3"]
    sig = _signature(assembly, feats, insts)
    boxes = [i.box for i in insts]
    # Boolean operations may raise the tolerance of their arguments in place; the raw shapes share
    # their geometry with the part objects, so the cuts work on deep copies
    cur = {i.key: i.shape.copy() for i in insts}
    base = {i.key: i.shape for i in insts}
    mats = {i.key: App.Matrix(i.shape.Placement.Matrix) for i in insts}
    for f in feats:
        per, errors = {}, []
        try:
            tool = _build_tool(assembly, f, boxes)
        except Exception as exc:
            tool = None
            errors.append({"kind": "tool", "error": str(exc)})
        for inst in insts:
            ent = {"removed": 0.0, "cut": False, "local": None, "in_scope": _in_scope(f, inst)}
            per[inst.key] = ent
            if tool is None or not ent["in_scope"] or not _hidden_ok(inst):
                continue
            try:
                removed = _removed_volume(inst, _input_shape(cur, base, inst.key), tool, tol)
                if removed > tol:
                    new = _cut_one(inst, _input_shape(cur, base, inst.key), tool)
                    if not new.isValid():
                        raise ValueError("cut result is invalid")
                    if not new.Solids:
                        new = Part.Compound([])  # the tool swallowed the component
                    cur[inst.key] = new
                    ent["removed"] = removed
                    ent["cut"] = True
                    ent["local"] = new if not new.Solids else _to_local(new, mats[inst.key])
                    _publish(inst, new)
            except Exception as exc:
                errors.append({"kind": "cut", "path": inst.key, "error": str(exc)})
        f.Proxy.results = {"sig": sig, "per": per, "errors": errors, "mats": mats, "base": base,
                           "final": dict(cur)}
    return feats


def _ensure(assembly, insts):
    """Make the feature results current for ``insts``; returns the active features."""
    feats = _active_features(assembly)
    if not feats:
        for f in features(assembly):
            f.Proxy.results = None
        return feats
    res = [getattr(f.Proxy, "results", None) for f in feats]
    if all(r is not None for r in res) and res[-1]["sig"] == _signature(assembly, feats, insts):
        return feats
    _compute(assembly, insts)
    return feats


def apply_to_instances(assembly, insts):
    """Used by ``Interference.resolve``: the instances with the active features applied, or None.

    Returns a dict key -> shape (the effective shape) for the components that some feature cut.
    """
    feats = _ensure(assembly, insts)
    if not feats:
        return {}
    out = {}
    for inst in insts:
        if any(f.Proxy.results["per"][inst.key]["cut"] for f in feats):
            out[inst.key] = _effective(feats, inst)
    return out


def _effective(feats, inst):
    last = None
    for f in feats:
        ent = f.Proxy.results["per"][inst.key]
        if ent["cut"]:
            last = ent["local"]
    if last is None:
        return inst.shape
    if not last.Solids:
        return last
    return _from_local(last, App.Matrix(inst.shape.Placement.Matrix))


def _instances(assembly):
    return _itf.resolve(assembly, include_hidden=_INCLUDE_HIDDEN, apply_features=False).insts


def _find(insts, component):
    if isinstance(component, _itf.Instance):
        component = component.key
    if isinstance(component, str):
        key = component.rstrip(".")
        hit = [i for i in insts if i.key == key] or [i for i in insts if i.label == key]
        if len(hit) > 1:
            raise ValueError("%r names %d components; use the instance path" % (component, len(hit)))
        return hit[0] if hit else None
    name = getattr(component, "Name", None)
    hit = [i for i in insts if i.objs[-1].Name == name or i.key == name]
    return hit[0] if len(hit) == 1 else None


def effective_shape(assembly, component):
    """The component's shape in assembly placement with every active feature applied.

    ``component`` is an instance path, a label, an Instance or a part object. Returns a copy
    (a shape the caller may keep), or None for an unknown component. A component no active
    feature cuts comes back as its original shape.
    """
    insts = _instances(assembly)
    inst = _find(insts, component)
    if inst is None:
        return None
    feats = _ensure(assembly, insts)
    if not feats:
        return inst.shape.copy()
    return _effective(feats, inst).copy()


def _desc(assembly, inst):
    return inst.describe(assembly)


def affected(feature):
    """What one feature does, JSON-serialisable: each component it cuts with link path and removed volume."""
    asm = feature.getParentGeoFeatureGroup()
    out = {"schema": SCHEMA, "feature": {"name": feature.Name, "label": feature.Label, "kind": feature.Kind},
           "suppressed": bool(feature.Suppressed), "scope": list(feature.Scope),
           "components": [], "not_cut": [], "errors": []}
    if feature.Suppressed:
        return out
    insts = _instances(asm)
    feats = _ensure(asm, insts)
    res = getattr(feature.Proxy, "results", None)
    if res is None:
        return out
    out["errors"] = list(res["errors"])
    for inst in insts:
        ent = res["per"][inst.key]
        if ent["cut"]:
            rec = _desc(asm, inst)
            rec.update({"removed_mm3": ent["removed"], "hidden": bool(inst.hidden)})
            out["components"].append(rec)
        else:
            out["not_cut"].append(inst.key)
    return out


def report(assembly):
    """Every active feature's effect and each component's total, JSON-serialisable."""
    insts = _instances(assembly)
    feats = _ensure(assembly, insts)
    out = {"schema": SCHEMA, "assembly": {"name": assembly.Name, "label": assembly.Label},
           "features": [], "components": [], "errors": []}
    for f in features(assembly):
        out["features"].append(affected(f))
    for f in feats:
        out["errors"].extend(f.Proxy.results["errors"])
    for inst in insts:
        total, names = 0.0, []
        for f in feats:
            ent = f.Proxy.results["per"][inst.key]
            if ent["cut"]:
                total += ent["removed"]
                names.append(f.Name)
        rec = _desc(assembly, inst)
        rec.update({"removed_mm3": total, "cut": bool(names), "features": names, "hidden": bool(inst.hidden),
                    "volume_mm3": float(_effective(feats, inst).Volume) if feats else float(inst.shape.Volume)})
        out["components"].append(rec)
    return out
