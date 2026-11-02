# SPDX-License-Identifier: LGPL-2.1-or-later
"""Assembly configurations (G06 slice 5a): named variants of an assembly, a JSON-returning Python API.

A configuration is an ``App::FeaturePython`` object inside the assembly. It holds named overrides of four kinds
and nothing else; anything it does not name keeps the assembly's default value.

- ``suppress``: component paths ("Link", "Sub.Link", a link array element). A suppressed component is taken
  out of its container's ``Group`` (the object stays in the document, the group index is remembered) or, for a
  link array element, gets FreeCAD's own ``Suppressed`` flag. Everything that walks the assembly through its
  group or its sub-objects then leaves it out: the solve, the BOM, ``Interference.check``, the assembly
  features and a STEP export. Every joint that reaches a suppressed component is suppressed (``Suppressed``),
  never deleted; a grounded joint, which has no such flag, is taken out of its joint group the same way.
- ``alternatives``: ``{component path: part name}``. The link of the component is repointed to another source
  and the joint references that reach it are remapped by slice 2's matcher (``ReplaceComponent``) on the way in.
  A joint with a reference the matcher could not match or found ambiguous keeps every reference as it was and is
  suppressed; nothing is guessed and nothing is deleted.
- ``joints``: ``{joint name: {property: value}}`` for ``Distance``, ``Distance2``, ``Angle`` (degrees),
  ``Offset1`` and ``Offset2`` (placements as ``{"base": [x, y, z], "rotation": [qx, qy, qz, qw]}``). Activation
  solves the assembly, so the parts the joint drives move.
- ``features``: ``{assembly feature name: suppressed}`` (slice 4a features).

One configuration is active at a time. Activating a configuration first restores the stored default of
everything the previous one overrode, verbatim (never by running the matcher backwards), then captures the
defaults of what the new one overrides and writes its values, and solves; the whole switch is one undo
transaction, so the state after activating X depends on X alone and A, B, A returns to A exactly. The defaults
live in the active configuration's ``Defaults`` string (JSON, floats written exactly), the values it wrote in
``Applied``; both are cleared when the configuration is left, so an inactive configuration holds only its
``Overrides``.

Drift. An edit to a property a configuration overrides, made while it is active, is reported by ``status``;
``activate`` refuses to leave while drift exists until ``capture`` (the edit becomes the configuration's value)
or ``discard`` (the configuration's value is written back). Edits to properties no configuration overrides
survive every switch, including the placement of a component that the solver moved and the user then moved
again. A property driven by an expression is never overridden: ``create`` refuses it and names the expression.

Limits (slice 5a): ``capture`` handles joint values, feature suppression and the part name of an alternative
(the references are not re-matched); a suppressed component is identified by its object name path, not by a
label; alternatives work on ``App::Link`` components and link arrays, not on plain geometry components; a
sub-assembly's own configurations and parent-chooses-child configurations are out of slice 5.

Behaviour sources, all public: FreeCAD's ``App::Link`` and Assembly documentation (wiki.freecad.org),
``App::FeaturePython`` and the document transaction API; slice 2's matcher in ``ReplaceComponent``.
"""

import json
import re

import FreeCAD as App

import ReplaceComponent as _rc

SCHEMA = "cadfork.assembly.configuration/1"
PROP_GROUP = "Configuration"
JOINT_VALUE_PROPS = ("Distance", "Distance2", "Angle", "Offset1", "Offset2")
_JOINT_SNAPSHOT_PROPS = ("Reference1", "Reference2", "Placement1", "Placement2", "Offset1", "Offset2",
                         "Distance", "Distance2", "Angle", "Suppressed")
_SKIP_TYPES = ("Assembly::JointGroup", "Assembly::ViewGroup", "Assembly::BomGroup", "Assembly::SimulationGroup",
               "Assembly::SnapshotGroup", "App::Origin")
_ROUND = 12
_TOL = 1e-9


class ConfigurationError(Exception):
    """A configuration cannot be made or changed (unknown target, expression-driven property, ...)."""


class ConfigurationDriftError(ConfigurationError):
    """Leaving the active configuration was refused because an overridden property was edited."""

    def __init__(self, drift):
        self.drift = drift
        super().__init__("overridden properties were edited while the configuration is active (capture or "
                         "discard first): " + "; ".join("%s.%s" % (d["target"], d["property"]) for d in drift))


# ------------------------------------------------------------------ the object

class AssemblyConfiguration:
    """Proxy of a configuration object; it has no behaviour of its own."""

    def __init__(self, obj):
        obj.addProperty("App::PropertyString", "Overrides", PROP_GROUP, "Overrides (JSON)")
        obj.addProperty("App::PropertyString", "Defaults", PROP_GROUP,
                        "Defaults of what this configuration overrides while it is active (JSON)")
        obj.addProperty("App::PropertyString", "Applied", PROP_GROUP,
                        "What activation wrote, for drift detection (JSON)")
        obj.addProperty("App::PropertyBool", "Active", PROP_GROUP, "The active configuration of the assembly")
        obj.Proxy = self

    def execute(self, obj):
        pass


def _is_config(obj):
    return isinstance(getattr(obj, "Proxy", None), AssemblyConfiguration)


def configurations(assembly):
    """The configuration objects of the assembly, in creation order."""
    return [o for o in assembly.Group if _is_config(o)]


def active(assembly):
    """The active configuration object, or None (the default)."""
    for c in configurations(assembly):
        if c.Active:
            return c
    return None


def _find(assembly, name):
    hit = [c for c in configurations(assembly) if name in (c.Label, c.Name)]
    if not hit:
        raise ConfigurationError("no configuration named %r" % name)
    if len(hit) > 1:
        raise ConfigurationError("%d configurations are named %r" % (len(hit), name))
    return hit[0]


# ------------------------------------------------------------------ small helpers

def _entity(obj, sub=""):
    return _rc._entity(obj, sub)


def _r(x):
    return round(float(x), _ROUND) + 0.0


def _pl_json(p, exact=True):
    f = (lambda x: float(x)) if exact else _r
    return {"base": [f(x) for x in p.Base], "rotation": [f(x) for x in p.Rotation.Q]}


def _pl_from(d):
    return App.Placement(App.Vector(*d["base"]), App.Rotation(*d["rotation"]))


def _same_pl(a, b, tol=_TOL):
    return a.isSame(b, tol)


def _num(v):
    return float(getattr(v, "Value", v))


def _value_json(v):
    if isinstance(v, App.Placement):
        return _pl_json(v)
    return _num(v)


def _value_from(prop, d):
    return _pl_from(d) if prop.startswith("Offset") else float(d)


def _value_same(a, b):
    if isinstance(a, dict):
        return _same_pl(_pl_from(a), _pl_from(b))
    return abs(a - b) <= _TOL


def _containers(assembly):
    """The assembly and every sub-assembly below it."""
    out, todo = [], [assembly]
    while todo:
        c = todo.pop(0)
        out.append(c)
        todo.extend(o for o in c.Group if o.TypeId == "Assembly::AssemblyObject")
    return out


def _owner_path(container, assembly):
    names, cur = [], container
    while cur is not None and cur != assembly:
        names.insert(0, cur.Name)
        cur = cur.getParentGeoFeatureGroup()
    return names


def _joints(assembly):
    """(joint object, owner path tokens, joint group, is_grounded) for every joint below the assembly."""
    out = []
    for c in _containers(assembly):
        owner = _owner_path(c, assembly)
        for g in c.Group:
            if g.TypeId != "Assembly::JointGroup":
                continue
            for j in g.Group:
                if hasattr(j, "JointType") and hasattr(j, "Reference1"):
                    out.append((j, owner, g, False))
                elif hasattr(j, "ObjectToGround"):
                    out.append((j, owner, g, True))
    return out


def _ref_tokens(obj, subs):
    """Object-name tokens a joint reference passes through: the component, then each object in the sub-name."""
    toks = [obj.Name]
    if subs:
        parts = [p for p in subs[0].split(".") if p]
        if parts and _rc._ELEMENT_RE.match(parts[-1]):
            parts = parts[:-1]
        toks += parts
    return toks


def _joint_reach(j, owner, grounded):
    """List of token paths the joint reaches (owner prefix included)."""
    if grounded:
        return [owner + [j.ObjectToGround.Name]] if j.ObjectToGround is not None else []
    out = []
    for prop in ("Reference1", "Reference2"):
        ref = getattr(j, prop, None)
        if ref is None or ref[0] is None:
            continue
        out.append(owner + _ref_tokens(ref[0], ref[1]))
    return out


def _touches(reach, comp_tokens):
    n = len(comp_tokens)
    return any(r[:n] == comp_tokens for r in reach)


def _expr_for(obj, prop):
    for path, expr in getattr(obj, "ExpressionEngine", []) or []:
        base = path.lstrip(".")
        if base == prop or base.startswith(prop + ".") or base.startswith(prop + "["):
            return path, expr
    return None


def _refuse_expression(obj, prop):
    hit = _expr_for(obj, prop)
    if hit:
        raise ConfigurationError("%s.%s is driven by the expression %r (%s = %s); a configuration never "
                                 "overrides an expression-driven property" % (obj.Name, prop, hit[1], hit[0], hit[1]))


# ------------------------------------------------------------------ resolving targets

class _Comp:
    def __init__(self, path, obj, container, kind):
        self.path, self.obj, self.container, self.kind = path, obj, container, kind
        self.tokens = path.split(".")


def _resolve_component(assembly, path):
    doc = assembly.Document
    toks = path.split(".")
    container = assembly
    for tok in toks[:-1]:
        o = doc.getObject(tok)
        if o is None or o not in container.Group:
            raise ConfigurationError("%r: %s is not inside %s" % (path, tok, container.Name))
        container = o
    obj = doc.getObject(toks[-1])
    if obj is None:
        raise ConfigurationError("%r names no object" % path)
    if obj in container.Group and obj.TypeId not in _SKIP_TYPES and not _is_config(obj) \
            and not _rc._is_joint(obj):
        return _Comp(path, obj, container, "group")
    if obj.TypeId == "App::LinkElement":
        return _Comp(path, obj, container, "element")
    raise ConfigurationError("%r is not a component of %s" % (path, assembly.Label))


def _find_link(assembly, path):
    comp = _resolve_component(assembly, path)
    if comp.obj.TypeId != "App::Link":
        raise ConfigurationError("%r is %s: an alternative part needs an App::Link (or a link array)"
                                 % (path, comp.obj.TypeId))
    return comp


def _joint_by_name(assembly, name):
    for j, owner, grp, grounded in _joints(assembly):
        if j.Name == name and not grounded:
            return j
    raise ConfigurationError("no joint named %r in %s" % (name, assembly.Label))


def _feature_by_name(assembly, name):
    import AssemblyFeature
    for f in AssemblyFeature.features(assembly):
        if f.Name == name:
            return f
    raise ConfigurationError("no assembly feature named %r in %s" % (name, assembly.Label))


# ------------------------------------------------------------------ creating

def create(assembly, name, suppress=(), alternatives=None, joints=None, features=None):
    """Add a configuration. Validates every target; refuses an expression-driven property.

    ``joints`` maps a joint name to ``{property: value}`` (``Distance``, ``Distance2``, ``Angle``, ``Offset1``,
    ``Offset2``; a placement is ``{"base": [...], "rotation": [qx, qy, qz, qw]}`` or an ``App.Placement``).
    ``features`` maps an assembly feature name to its ``Suppressed`` value. One undo transaction.
    """
    if any(c.Label == name for c in configurations(assembly)):
        raise ConfigurationError("a configuration named %r exists" % name)
    ov = {"suppress": [str(p) for p in suppress], "alternatives": {}, "joints": {}, "features": {}}
    for p in ov["suppress"]:
        _resolve_component(assembly, p)
    for path, part in (alternatives or {}).items():
        comp = _find_link(assembly, path)
        if path in ov["suppress"]:
            raise ConfigurationError("%r is both suppressed and given an alternative part" % path)
        if assembly.Document.getObject(str(part)) is None:
            raise ConfigurationError("no part named %r" % part)
        _refuse_expression(comp.obj, "LinkedObject")
        ov["alternatives"][path] = str(part)
    for jname, vals in (joints or {}).items():
        j = _joint_by_name(assembly, jname)
        rec = {}
        for prop, v in vals.items():
            if prop not in JOINT_VALUE_PROPS or not hasattr(j, prop):
                raise ConfigurationError("%s has no overridable value %r" % (jname, prop))
            _refuse_expression(j, prop)
            rec[prop] = _value_json(v) if isinstance(v, App.Placement) else (
                v if isinstance(v, dict) else float(v))
        ov["joints"][jname] = rec
    for fname, val in (features or {}).items():
        f = _feature_by_name(assembly, fname)
        _refuse_expression(f, "Suppressed")
        ov["features"][fname] = bool(val)
    doc = assembly.Document
    doc.openTransaction("Create configuration")
    try:
        obj = assembly.newObject("App::FeaturePython", "Configuration")
        AssemblyConfiguration(obj)
        obj.Label = name
        obj.Overrides = json.dumps(ov, sort_keys=True)
        obj.Active = False
    except Exception:
        doc.abortTransaction()
        raise
    doc.commitTransaction()
    return describe(obj)


def delete(assembly, name):
    """Remove an inactive configuration."""
    c = _find(assembly, name)
    if c.Active:
        raise ConfigurationError("%r is active; activate another configuration first" % name)
    doc = assembly.Document
    doc.openTransaction("Delete configuration")
    doc.removeObject(c.Name)
    doc.commitTransaction()


def rename(assembly, name, new_name):
    c = _find(assembly, name)
    if any(o.Label == new_name for o in configurations(assembly)):
        raise ConfigurationError("a configuration named %r exists" % new_name)
    c.Label = new_name


def copy(assembly, name, new_name):
    c = _find(assembly, name)
    ov = json.loads(c.Overrides)
    return create(assembly, new_name, ov["suppress"], ov["alternatives"], ov["joints"], ov["features"])


def describe(cfg):
    ov = json.loads(cfg.Overrides)
    return {"schema": SCHEMA, "name": cfg.Label, "object": cfg.Name, "entity": _entity(cfg),
            "active": bool(cfg.Active), "overrides": ov}


# ------------------------------------------------------------------ state: components

def _components(assembly):
    """Objects whose placement the solver may write: components of every container, link array elements."""
    out = []
    for c in _containers(assembly):
        for o in c.Group:
            if o.TypeId in _SKIP_TYPES or _is_config(o) or not hasattr(o, "Placement"):
                continue
            if o.TypeId == "Assembly::AssemblyObject" or getattr(o, "Proxy", None).__class__.__name__ == "AssemblyFeature":
                continue
            out.append(o)
            out.extend(getattr(o, "ElementList", []) or [])
    return out


def _paths(assembly):
    return ["%s" % o.Name for o in _components(assembly)]


class _State:
    """Everything captured before an activation changes it, and what the activation wrote."""

    def __init__(self):
        self.defaults = {"components": {}, "joints": {}, "links": {}, "features": {}, "placements": {}}
        self.applied = {"components": [], "alternatives": {}, "joint_values": {}, "joints_suppressed": [],
                        "features": {}, "placements": {}}
        self.report = {"suppressed": [], "alternatives": [], "suppressed_joints": [], "joint_values": [],
                       "features": []}


def _snapshot_joint(state, j, grp, grounded):
    if j.Name in state.defaults["joints"]:
        return
    rec = {"grounded": grounded, "group": grp.Name, "index": list(grp.Group).index(j)}
    if not grounded:
        for prop in _JOINT_SNAPSHOT_PROPS:
            if not hasattr(j, prop):
                continue
            v = getattr(j, prop)
            if prop.startswith("Reference"):
                rec[prop] = None if v is None or v[0] is None else [v[0].Name, list(v[1])]
            elif isinstance(v, App.Placement):
                rec[prop] = _pl_json(v)
            elif isinstance(v, bool):
                rec[prop] = v
            else:
                rec[prop] = _num(v)
    state.defaults["joints"][j.Name] = rec


def _restore_joint(assembly, name, rec):
    doc = assembly.Document
    j = doc.getObject(name)
    if j is None:
        return
    if rec["grounded"]:
        grp = doc.getObject(rec["group"])
        if j not in grp.Group:
            lst = list(grp.Group)
            lst.insert(min(rec["index"], len(lst)), j)
            grp.Group = lst
        return
    for prop in _JOINT_SNAPSHOT_PROPS:
        if prop not in rec or prop == "Suppressed" or not hasattr(j, prop):
            continue
        v = rec[prop]
        if prop.startswith("Reference"):
            cur = getattr(j, prop)
            want = None if v is None else (doc.getObject(v[0]), list(v[1]))
            if (cur is None) != (want is None) or (cur is not None and (cur[0] != want[0] or list(cur[1]) != want[1])):
                setattr(j, prop, want)
        elif isinstance(v, dict):
            if not _same_pl(getattr(j, prop), _pl_from(v), 0.0):
                setattr(j, prop, _pl_from(v))
        elif getattr(j, prop) != v:
            setattr(j, prop, v)
    if "Suppressed" in rec and bool(j.Suppressed) != rec["Suppressed"]:
        j.Suppressed = rec["Suppressed"]


# ------------------------------------------------------------------ applying

def _remove_from_group(state, comp):
    cont = comp.container
    lst = list(cont.Group)
    idx = lst.index(comp.obj)
    state.defaults["components"][comp.path] = {"kind": "group", "container": cont.Name, "name": comp.obj.Name,
                                               "index": idx}
    cont.removeObject(comp.obj)


def _suppress_component(assembly, state, comp):
    if comp.kind == "element":
        state.defaults["components"][comp.path] = {"kind": "element", "name": comp.obj.Name,
                                                   "suppressed": bool(comp.obj.Suppressed)}
        comp.obj.Suppressed = True
    else:
        _remove_from_group(state, comp)
    state.applied["components"].append(comp.path)
    state.report["suppressed"].append({"path": comp.path, "label": comp.obj.Label, "entity": _entity(comp.obj)})


def _suppress_joint(state, j, reason):
    if j.Name not in state.applied["joints_suppressed"]:
        j.Suppressed = True
        state.applied["joints_suppressed"].append(j.Name)
        state.report["suppressed_joints"].append({"joint": j.Name, "label": j.Label, "entity": _entity(j),
                                                  "reason": reason})


def _apply_alternative(assembly, state, path, part_name):
    doc = assembly.Document
    comp = _find_link(assembly, path)
    link, part = comp.obj, doc.getObject(part_name)
    try:
        report, ctx = _rc._plan(assembly, link, part)
    except _rc.ReplaceError as exc:
        raise ConfigurationError("alternative part %s for %s: %s" % (part_name, path, exc))
    refs, plc = ctx["refs"], ctx["plc"]
    by_joint = {}
    for rf in refs:
        by_joint.setdefault(rf.joint.Name, []).append(rf)
    for jname in by_joint:
        j = doc.getObject(jname)
        for jj, owner, grp, grounded in _joints(assembly):
            if jj == j:
                _snapshot_joint(state, j, grp, grounded)
    state.defaults["links"][link.Name] = {"linked": link.LinkedObject.Name if link.LinkedObject else None,
                                          "placement": _pl_json(link.Placement)}
    if not plc.isSame(App.Placement(), 1e-9):
        link.Placement = link.Placement * plc
    link.LinkedObject = ctx["new_top"]
    jrec = []
    for jname, rfs in sorted(by_joint.items()):
        j = doc.getObject(jname)
        if all(rf.status == "matched" for rf in rfs):
            for rf in rfs:
                _rc._apply_ref(rf, {}, ctx["new_tokens"])
            jrec.append({"joint": jname, "entity": _entity(j), "outcome": "remapped"})
        else:
            _suppress_joint(state, j, "alternative part %s: %s" % (part_name, "; ".join(
                "%s %s" % (rf.prop, rf.status) for rf in rfs if rf.status != "matched")))
            jrec.append({"joint": jname, "entity": _entity(j), "outcome": "suppressed",
                         "references": [{"reference": rf.prop, "status": rf.status,
                                         "elements": [{"old": s["old_element"], "new": s["new_element"],
                                                       "status": s["status"], "reason": s["reason"]}
                                                      for s in rf.result]} for rf in rfs]})
    state.applied["alternatives"][path] = part_name
    state.report["alternatives"].append({"component": path, "entity": _entity(link), "part": part_name,
                                         "part_entity": _entity(part), "joints": jrec})


def _write_joint_value(state, j, prop, d):
    cur = getattr(j, prop)
    want = _value_from(prop, d)
    setattr(j, prop, want)
    state.applied["joint_values"].setdefault(j.Name, {})[prop] = d
    state.report["joint_values"].append({"joint": j.Name, "entity": _entity(j), "property": prop,
                                         "default": _value_json(cur), "value": d})


def _solve(assembly):
    try:
        return assembly.solve()
    except Exception as exc:
        return "error: %s" % exc


def _refresh_consumers(assembly):
    """Touch what caches the component list: the BOM sheets and the assembly features."""
    for c in _containers(assembly):
        for g in c.Group:
            if g.TypeId == "Assembly::BomGroup":
                for b in g.Group:
                    b.touch()
    import AssemblyFeature
    for f in AssemblyFeature.features(assembly):
        AssemblyFeature.refresh_dependencies(f)
        f.Proxy.results = None


def _autosolve_off():
    import Preferences
    pref = Preferences.preferences()
    old = pref.GetBool("SolveInJointCreation", True)
    pref.SetBool("SolveInJointCreation", False)
    return pref, old


def _apply(assembly, cfg):
    """Capture defaults, write the overrides of ``cfg``. Called inside the activation transaction."""
    ov = json.loads(cfg.Overrides)
    st = _State()
    comps = [_resolve_component(assembly, p) for p in ov["suppress"]]
    joints = _joints(assembly)
    for comp in _components(assembly):
        st.defaults["placements"][comp.Name] = _pl_json(comp.Placement)
    for f in ov["features"]:
        feat = _feature_by_name(assembly, f)
        st.defaults["features"][f] = bool(feat.Suppressed)
    # joints that reach a suppressed component, and joints with values
    touched = []
    for j, owner, grp, grounded in joints:
        reach = _joint_reach(j, owner, grounded)
        if any(_touches(reach, c.tokens) for c in comps):
            touched.append((j, grounded, grp))
        if j.Name in ov["joints"]:
            _snapshot_joint(st, j, grp, grounded)
        elif any(_touches(reach, c.tokens) for c in comps):
            _snapshot_joint(st, j, grp, grounded)
    for path in sorted(ov["alternatives"]):
        _apply_alternative(assembly, st, path, ov["alternatives"][path])
    # components in descending group index so the stored indexes stay valid
    for comp in sorted(comps, key=lambda c: -list(c.container.Group).index(c.obj) if c.kind == "group" else 0):
        _suppress_component(assembly, st, comp)
    for j, grounded, grp in touched:
        if grounded:
            grp.removeObject(j)
            st.applied["joints_suppressed"].append(j.Name)
            st.report["suppressed_joints"].append({"joint": j.Name, "label": j.Label, "entity": _entity(j),
                                                   "reason": "grounds a suppressed component"})
        else:
            _suppress_joint(st, j, "reaches a suppressed component")
    for jname in sorted(ov["joints"]):
        j = _joint_by_name(assembly, jname)
        for prop, d in sorted(ov["joints"][jname].items()):
            _write_joint_value(st, j, prop, d)
    import AssemblyFeature
    for fname, val in sorted(ov["features"].items()):
        feat = _feature_by_name(assembly, fname)
        AssemblyFeature.set_suppressed(feat, val)
        st.applied["features"][fname] = val
        st.report["features"].append({"feature": fname, "entity": _entity(feat), "suppressed": val})
    return st


def _restore(assembly, cfg):
    """Write back, verbatim, the defaults ``cfg`` stored; leave the configuration inactive."""
    doc = assembly.Document
    d = json.loads(cfg.Defaults) if cfg.Defaults else None
    if d is None:
        cfg.Active = False
        return
    applied = json.loads(cfg.Applied)
    for rec in sorted((r for r in d["components"].values() if isinstance(r, dict) and r["kind"] == "group"),
                      key=lambda r: r["index"]):
        cont, obj = doc.getObject(rec["container"]), doc.getObject(rec["name"])
        if obj not in cont.Group:
            lst = list(cont.Group)
            lst.insert(min(rec["index"], len(lst)), obj)
            cont.Group = lst
    for rec in d["components"].values():
        if isinstance(rec, dict) and rec["kind"] == "element":
            el = doc.getObject(rec["name"])
            if bool(el.Suppressed) != rec["suppressed"]:
                el.Suppressed = rec["suppressed"]
    for name, rec in d["links"].items():
        link = doc.getObject(name)
        want = doc.getObject(rec["linked"]) if rec["linked"] else None
        if link.LinkedObject != want:
            link.LinkedObject = want
        if not _same_pl(link.Placement, _pl_from(rec["placement"]), 0.0):
            link.Placement = _pl_from(rec["placement"])
    for jname, rec in sorted(d["joints"].items(), key=lambda kv: kv[1]["index"]):
        _restore_joint(assembly, jname, rec)
    import AssemblyFeature
    for fname, val in d["features"].items():
        AssemblyFeature.set_suppressed(doc.getObject(fname), val)
    # placements: restore what the solve moved; keep a placement the user moved again afterwards
    for name, plc in d["placements"].items():
        o = doc.getObject(name)
        if o is None:
            continue
        after = applied["placements"].get(name)
        if after is not None and not _same_pl(o.Placement, _pl_from(after), 1e-7):
            continue
        if not _same_pl(o.Placement, _pl_from(plc), 0.0):
            o.Placement = _pl_from(plc)
    cfg.Defaults = ""
    cfg.Applied = ""
    cfg.Active = False


# ------------------------------------------------------------------ drift and status

def _drift(assembly, cfg):
    """Overridden properties whose current value differs from what activation wrote."""
    if not cfg.Active or not cfg.Applied:
        return []
    doc = assembly.Document
    ap = json.loads(cfg.Applied)
    out = []

    def add(kind, obj, prop, expected, found):
        out.append({"kind": kind, "target": obj.Name, "entity": _entity(obj), "property": prop,
                    "expected": expected, "found": found})
    for path in ap["components"]:
        comp = _resolve_component_loose(assembly, path)
        if comp is None:
            continue
        if comp.kind == "element":
            if not comp.obj.Suppressed:
                add("component", comp.obj, "Suppressed", True, False)
        elif comp.obj in comp.container.Group:
            add("component", comp.obj, "Group", "suppressed", "member of %s" % comp.container.Name)
    for path, part in ap["alternatives"].items():
        link = doc.getObject(path.split(".")[-1])
        cur = link.LinkedObject.Name if link.LinkedObject else None
        if cur != part:
            add("alternative", link, "LinkedObject", part, cur)
    for jname, vals in ap["joint_values"].items():
        j = doc.getObject(jname)
        for prop, want in vals.items():
            now = _value_json(getattr(j, prop))
            if not _value_same(now, want):
                add("joint_value", j, prop, want, now)
    for jname in ap["joints_suppressed"]:
        j = doc.getObject(jname)
        if j is not None and hasattr(j, "Suppressed") and not j.Suppressed:
            add("joint_suppressed", j, "Suppressed", True, False)
    for fname, val in ap["features"].items():
        f = doc.getObject(fname)
        if bool(f.Suppressed) != val:
            add("feature", f, "Suppressed", val, bool(f.Suppressed))
    return out


def _resolve_component_loose(assembly, path):
    """Like ``_resolve_component`` for an object that is no longer in its group (a suppressed one)."""
    doc = assembly.Document
    toks = path.split(".")
    container = assembly
    for tok in toks[:-1]:
        container = doc.getObject(tok)
    obj = doc.getObject(toks[-1])
    if obj is None:
        return None
    return _Comp(path, obj, container, "element" if obj.TypeId == "App::LinkElement" else "group")


def status(assembly):
    """JSON-serialisable state: the configurations, the active one and any drift."""
    cfg = active(assembly)
    return {"schema": SCHEMA, "assembly": {"name": assembly.Name, "label": assembly.Label,
                                           "entity": _entity(assembly)},
            "configurations": [c.Label for c in configurations(assembly)],
            "active": cfg.Label if cfg else None,
            "drift": _drift(assembly, cfg) if cfg else [],
            "suppressed": suppressed(assembly)}


def suppressed(assembly):
    """Component paths the active configuration suppresses."""
    cfg = active(assembly)
    return list(json.loads(cfg.Applied)["components"]) if cfg and cfg.Applied else []


# ------------------------------------------------------------------ activation

def activate(assembly, name=None):
    """Make ``name`` the active configuration (``None`` or ``"default"`` restores the assembly's own values).

    One undo transaction: the active configuration's defaults are written back, the new one's overrides are
    written, the assembly is solved. Refused with ``ConfigurationDriftError`` while the active configuration
    has drift (``capture`` or ``discard`` first). Returns the JSON report.
    """
    doc = assembly.Document
    target = None if name in (None, "default") else _find(assembly, name)
    cur = active(assembly)
    if target is not None and cur is not None and target == cur:
        return dict(status(assembly), changed=False)
    drift = _drift(assembly, cur) if cur else []
    if drift:
        raise ConfigurationDriftError(drift)
    if target is None and cur is None:
        return dict(status(assembly), changed=False)
    pref, old_pref = _autosolve_off()
    doc.openTransaction("Activate configuration")
    try:
        if cur is not None:
            _restore(assembly, cur)
        rep = _empty_report()
        code = 0
        if target is not None:
            st = _apply(assembly, target)
            target.Defaults = json.dumps(st.defaults, sort_keys=True)
            target.Active = True
            rep = st.report
        _refresh_consumers(assembly)
        doc.recompute()
        if target is not None:
            code = _solve(assembly)
            doc.recompute()
            st.applied["placements"] = {o.Name: _pl_json(o.Placement, exact=False) for o in _components(assembly)}
            target.Applied = json.dumps(st.applied, sort_keys=True)
    except Exception:
        doc.abortTransaction()
        raise
    finally:
        pref.SetBool("SolveInJointCreation", old_pref)
    doc.commitTransaction()
    out = status(assembly)
    out.update(changed=True, previous=cur.Label if cur else None, solve={"code": code}, **rep)
    out["placements"] = {o.Name: _pl_json(o.Placement) for o in _components(assembly)}
    return out


def _empty_report():
    return {"suppressed": [], "alternatives": [], "suppressed_joints": [], "joint_values": [], "features": []}


def default(assembly):
    """Restore the assembly's own values (no configuration active)."""
    return activate(assembly, None)


def capture(assembly):
    """Take the edits of the overridden properties into the active configuration (the drift is resolved).

    Joint values, feature suppression and the part name of an alternative are captured; any other drift is
    refused (``discard`` it). One undo transaction."""
    cfg = active(assembly)
    if cfg is None:
        raise ConfigurationError("no configuration is active")
    drift = _drift(assembly, cfg)
    bad = [d for d in drift if d["kind"] not in ("joint_value", "feature", "alternative")]
    if bad:
        raise ConfigurationError("cannot capture %s; discard it instead" % ", ".join(
            "%s.%s" % (d["target"], d["property"]) for d in bad))
    ov, ap = json.loads(cfg.Overrides), json.loads(cfg.Applied)
    doc = assembly.Document
    doc.openTransaction("Capture into configuration")
    for d in drift:
        if d["kind"] == "joint_value":
            ov["joints"][d["target"]][d["property"]] = d["found"]
            ap["joint_values"][d["target"]][d["property"]] = d["found"]
        elif d["kind"] == "feature":
            ov["features"][d["target"]] = d["found"]
            ap["features"][d["target"]] = d["found"]
        else:
            for path in ov["alternatives"]:
                if path.split(".")[-1] == d["target"]:
                    ov["alternatives"][path] = d["found"]
                    ap["alternatives"][path] = d["found"]
    cfg.Overrides = json.dumps(ov, sort_keys=True)
    if any(d["kind"] == "joint_value" for d in drift):
        pref, old_pref = _autosolve_off()
        try:
            _solve(assembly)
            doc.recompute()
        finally:
            pref.SetBool("SolveInJointCreation", old_pref)
    ap["placements"] = {o.Name: _pl_json(o.Placement, exact=False) for o in _components(assembly)}
    cfg.Applied = json.dumps(ap, sort_keys=True)
    doc.commitTransaction()
    return status(assembly)


def discard(assembly):
    """Write the active configuration's values back over the edits (the drift is resolved)."""
    cfg = active(assembly)
    if cfg is None:
        raise ConfigurationError("no configuration is active")
    drift = _drift(assembly, cfg)
    if not drift:
        return status(assembly)
    doc = assembly.Document
    ap = json.loads(cfg.Applied)
    pref, old_pref = _autosolve_off()
    doc.openTransaction("Discard configuration drift")
    try:
        for d in drift:
            obj = doc.getObject(d["target"])
            if d["kind"] == "joint_value":
                setattr(obj, d["property"], _value_from(d["property"], d["expected"]))
            elif d["kind"] == "feature":
                import AssemblyFeature
                AssemblyFeature.set_suppressed(obj, d["expected"])
            elif d["kind"] == "alternative":
                obj.LinkedObject = doc.getObject(d["expected"])
            elif d["kind"] == "joint_suppressed":
                obj.Suppressed = True
            elif d["kind"] == "component":
                comp = _resolve_component_loose(assembly, [p for p in ap["components"]
                                                           if p.split(".")[-1] == d["target"]][0])
                if comp.kind == "element":
                    obj.Suppressed = True
                else:
                    comp.container.removeObject(obj)
        _refresh_consumers(assembly)
        doc.recompute()
        if any(d["kind"] == "joint_value" for d in drift):
            _solve(assembly)
            doc.recompute()
            ap["placements"] = {o.Name: _pl_json(o.Placement, exact=False) for o in _components(assembly)}
            cfg.Applied = json.dumps(ap, sort_keys=True)
    except Exception:
        doc.abortTransaction()
        raise
    finally:
        pref.SetBool("SolveInJointCreation", old_pref)
    doc.commitTransaction()
    return status(assembly)


# ------------------------------------------------------------------ signature

_CELL = re.compile(r"^[A-Z]+[0-9]+$")


def _round_json(v):
    if isinstance(v, float):
        return round(v, 9) + 0.0
    if isinstance(v, list):
        return [_round_json(x) for x in v]
    if isinstance(v, dict):
        return {k: _round_json(x) for k, x in v.items()}
    return v


def _norm_prop(rec, p, v):
    """Stored JSON of a configuration compares at the signature's own precision (9 decimals)."""
    if rec["label_is_config"] and p in ("Overrides", "Defaults", "Applied") and isinstance(v, str) and v:
        return json.dumps(_round_json(json.loads(v)), sort_keys=True)
    return v


def _clean(sig):
    """Drop FreeCAD's own transient caches (property names starting with an underscore) and the generated
    cells of a BOM sheet (the sheet rebuilds them from the assembly on every recompute; undo does not restore
    them cell by cell)."""
    return {n: dict(rec, props={p: _norm_prop(dict(rec, label_is_config="Defaults" in rec["props"]
                                                   and "Applied" in rec["props"]), p, v)
                                for p, v in rec["props"].items() if not p.startswith("_")
                                and not (rec["type"] == "Assembly::BomObject" and _CELL.match(p))})
            for n, rec in sig.items()}


def signature(doc, assembly=None):
    """Object-and-property signature of a document (``ReplaceComponent.signature``) without the
    configuration objects and the references to them, so a state with configurations made can be compared
    with the state before any was made."""
    sig = _clean(_rc.signature(doc))
    cfg_names = {o.Name for o in doc.Objects if _is_config(o)}
    out = {}
    for name, rec in sig.items():
        if name in cfg_names:
            continue
        props = {}
        for p, v in rec["props"].items():
            if isinstance(v, list):
                v = [x for x in v if not (isinstance(x, str) and x.startswith("@") and x[1:] in cfg_names)]
            props[p] = v
        out[name] = dict(rec, props=props)
    return out


def full_signature(doc):
    """The same with the configuration objects included (for A, B, A comparisons)."""
    return _clean(_rc.signature(doc))
