# SPDX-License-Identifier: LGPL-2.1-or-later
"""Refactor operations (F6). Every operation is meant to run under ``invariant(doc, fn)``.

- ``relabel``: change ``Label`` (never ``Name``, which is immutable).
- ``lift_literal`` / ``lift_constraint``: replace a literal with an expression bound to a VarSet
  property or a Spreadsheet alias, through ``setExpression``; the value is read back and must
  be identical, or the lift is undone and raises.
- ``apply_across_bodies``: run one payload on every PartDesign Body.
- ``find_redundant`` / ``merge_redundant``: remove a feature only when SAFETY.md rules R1 to R7
  prove it is a no-op.

Operations return plain dicts. ``merge_redundant`` reports ``removed`` so ``invariant`` accepts
the deletion of exactly those objects.
"""

import re

import FreeCAD

from AgentCore.verify import checks as _vchecks

from . import deviation, geomhash

# ------------------------------------------------------------------ helpers

LIFTABLE = {
    "App::PropertyLength": "mm",
    "App::PropertyDistance": "mm",
    "App::PropertyAngle": "deg",
    "App::PropertyFloat": "",
}
# R-L1: only these families are lifted; Draft, Arch, BIM, FEM and CAM objects rebuild from
# Python proxies that read their own properties and are left alone.
LIFT_FAMILIES = ("PartDesign::", "Sketcher::", "Part::")
SKIP_STATUS = {"ReadOnly", "Hidden", "Output", "Transient"}
CONSTRAINT_TYPES = {"Distance", "DistanceX", "DistanceY", "Radius", "Diameter", "Angle"}


def _obj(doc, ref):
    return ref if hasattr(ref, "Name") else doc.getObject(ref)


def _ident(text):
    return re.sub(r"\W", "_", text).strip("_") or "x"


def _short_type(obj):
    return obj.TypeId.split("::")[-1]


def bodies(doc):
    """PartDesign Body objects, in document order."""
    return [o for o in doc.Objects if o.TypeId == "PartDesign::Body"]


def body_of(obj):
    """The PartDesign Body that owns ``obj``, or None."""
    for p in obj.InList:
        if p.TypeId == "PartDesign::Body" and obj in p.Group:
            return p
    return None


# ------------------------------------------------------------------ relabel

def relabel(doc, mapping):
    """Set ``Label`` for each ``{name_or_object: new_label}``. ``Name`` must not change.

    FreeCAD may append digits to keep labels unique; the label actually set is returned.
    """
    changed, skipped = {}, {}
    for key, new in mapping.items():
        obj = _obj(doc, key)
        if obj is None:
            skipped[str(key)] = "no such object"
            continue
        name, old = obj.Name, obj.Label
        if old == new:
            continue
        try:
            obj.Label = new
        except Exception as exc:
            skipped[name] = repr(exc)
            continue
        if obj.Name != name:
            raise RuntimeError(f"relabel changed Name {name} -> {obj.Name}")
        changed[name] = {"old": old, "new": obj.Label}
    return {"op": "relabel", "changed": changed, "skipped": skipped, "count": len(changed)}


def relabel_scheme(doc, only_families=("PartDesign::", "Sketcher::", "Part::", "App::")):
    """A deterministic bulk scheme: ``<Body>_<Type><n>`` inside bodies, ``<Type><n>`` elsewhere."""
    counters, mapping = {}, {}
    for o in doc.Objects:
        if not o.TypeId.startswith(tuple(only_families)):
            continue
        body = body_of(o)
        prefix = (_ident(body.Label) + "_") if body else ""
        key = prefix + _short_type(o)
        counters[key] = counters.get(key, 0) + 1
        mapping[o.Name] = f"{key}{counters[key]}"
    return mapping


def relabel_all(doc):
    return relabel(doc, relabel_scheme(doc))


# ------------------------------------------------------------------ lifting

def _is_lift_family(obj):
    return obj.TypeId.startswith(LIFT_FAMILIES) and not obj.TypeId.endswith("Python") \
        and obj.TypeId not in ("App::VarSet", "Spreadsheet::Sheet")


def lift_candidates(doc):
    """Literal properties and named driving sketch constraints that may be lifted (rule R-L1..L3)."""
    out = []
    for o in doc.Objects:
        if not _is_lift_family(o):
            continue
        bound = {p.lstrip(".") for p, _ in o.ExpressionEngine}
        for prop in o.PropertiesList:
            tid = o.getTypeIdOfProperty(prop)
            if tid not in LIFTABLE or prop in bound:
                continue
            if SKIP_STATUS & set(o.getPropertyStatus(prop)):
                continue
            out.append({"object": o.Name, "property": prop, "kind": "property"})
        if o.TypeId == "Sketcher::SketchObject":
            for c in o.Constraints:
                if c.Name and c.Type in CONSTRAINT_TYPES and c.Driving and f"Constraints.{c.Name}" not in bound:
                    out.append({"object": o.Name, "property": c.Name, "kind": "constraint"})
    return out


def _container(doc, target, name):
    name = name or "Parameters"
    if target == "VarSet":
        vs = doc.getObject(name)
        if vs is None:
            vs = doc.addObject("App::VarSet", name)
        if vs.TypeId != "App::VarSet":
            raise ValueError(f"{name} is not a VarSet")
        return vs
    if target == "Spreadsheet":
        sh = doc.getObject(name)
        if sh is None:
            sh = doc.addObject("Spreadsheet::Sheet", name)
        if sh.TypeId != "Spreadsheet::Sheet":
            raise ValueError(f"{name} is not a Spreadsheet")
        return sh
    raise ValueError("target must be 'VarSet' or 'Spreadsheet'")


def _unique_alias(container, base):
    alias = _ident(base)
    if not re.match(r"[A-Za-z_]", alias):
        alias = "p_" + alias
    taken = set(container.PropertiesList) if container.TypeId == "App::VarSet" else set(_sheet_aliases(container))
    cand, n = alias, 1
    while cand in taken or _looks_like_cell_or_unit(cand):
        n += 1
        cand = f"{alias}_{n}"
    return cand


def _sheet_aliases(sheet):
    return [a for a in (sheet.getAlias(c) for c in _used_cells(sheet)) if a]


def _used_cells(sheet):
    try:
        return list(sheet.getUsedCells())  # not exposed in every build
    except Exception:
        return [c for c in sheet.PropertiesList if re.fullmatch(r"[A-Z]+[0-9]+", c)]


def _looks_like_cell_or_unit(name):
    # Spreadsheet aliases may not look like cells (A1) or units (W, m, mm).
    if re.fullmatch(r"[A-Za-z]{1,2}[0-9]+", name):
        return True
    try:
        FreeCAD.Units.Quantity("1 " + name)
        return True
    except Exception:
        return False


def _next_cell(sheet):
    n = 1
    used = set(_used_cells(sheet))
    while f"A{n}" in used:
        n += 1
    return f"A{n}"


def _read(obj, prop, kind):
    if kind == "constraint":
        q = obj.getDatum(prop)
        c = next(c for c in obj.Constraints if c.Name == prop)
        return (q.Value, "deg" if c.Type == "Angle" else "mm", "App::PropertyAngle" if c.Type == "Angle" else "App::PropertyLength")
    tid = obj.getTypeIdOfProperty(prop)
    val = getattr(obj, prop)
    v = val.Value if hasattr(val, "Value") else float(val)
    return v, LIFTABLE[tid], tid


def lift_literal(doc, obj, prop, *, target="VarSet", container=None, alias=None, kind="property"):
    """Bind ``obj.prop`` (or the named sketch constraint ``prop``) to a VarSet property or Spreadsheet alias.

    The literal becomes the container's value, the property gets ``setExpression``, and the
    expression is evaluated and compared exactly to the original; a mismatch removes the
    expression again and raises. Returns a record of what was bound.
    """
    obj = _obj(doc, obj)
    value, unit, tid = _read(obj, prop, kind)
    cont = _container(doc, target, container)
    alias = alias or _unique_alias(cont, f"{obj.Name}_{prop}")
    path = f"Constraints.{prop}" if kind == "constraint" else prop
    if target == "VarSet":
        cont.addProperty(tid, alias, "Lifted", f"Lifted from {obj.Name}.{prop}")
        setattr(cont, alias, FreeCAD.Units.Quantity(f"{value!r} {unit}") if unit else value)
        cell = None
    else:
        cell = _next_cell(cont)
        cont.set(cell, f"{value!r} {unit}".strip())
        cont.setAlias(cell, alias)
        cont.recompute()
    expr = f"{cont.Name}.{alias}"
    obj.setExpression(path, expr)
    back = obj.evalExpression(expr)
    back = back.Value if hasattr(back, "Value") else float(back)
    if back != value:
        obj.setExpression(path, None)
        raise ValueError(f"lift of {obj.Name}.{prop} would change the value {value!r} -> {back!r}")
    return {"object": obj.Name, "property": prop, "target": target, "container": cont.Name,
            "alias": alias, "expression": expr, "value": value, "cell": cell}


def lift_all(doc, *, target="VarSet", container=None, limit=None, candidates=None):
    """Lift every candidate literal (up to ``limit``). Candidates that cannot be lifted are skipped and listed."""
    lifted, skipped = [], []
    for c in (candidates if candidates is not None else lift_candidates(doc)):
        if limit is not None and len(lifted) >= limit:
            break
        try:
            lifted.append(lift_literal(doc, c["object"], c["property"], target=target, container=container,
                                       kind=c["kind"]))
        except Exception as exc:
            skipped.append({**c, "error": repr(exc)})
    return {"op": "lift_literal", "target": target, "lifted": lifted, "skipped": skipped, "count": len(lifted)}


# ------------------------------------------------------------ across bodies

def apply_across_bodies(doc, fn, bodies_=None):
    """Run ``fn(body)`` on every PartDesign Body (or ``bodies_``); the payload decides what changes.

    Raises on the first failure so a surrounding ``invariant`` rolls the whole batch back.
    """
    targets = [(_obj(doc, b)) for b in bodies_] if bodies_ is not None else bodies(doc)
    results = {}
    for b in targets:
        results[b.Name] = fn(b)
    return {"op": "apply_across_bodies", "bodies": [b.Name for b in targets], "results": results,
            "count": len(targets)}


def _body_features(body):
    return [o for o in body.Group if o.TypeId.startswith(("PartDesign::", "Sketcher::"))]


def standardise_body(body, *, target="Spreadsheet", container="Parameters"):
    """A payload for ``apply_across_bodies``: prefix every feature label with the body and lift its literals."""
    doc = body.Document
    relabeled = relabel(doc, {f.Name: f"{_ident(body.Label)}_{f.Label}" for f in _body_features(body)
                              if not f.Label.startswith(_ident(body.Label) + "_")})
    members = {f.Name for f in _body_features(body)}
    cands = [c for c in lift_candidates(doc) if c["object"] in members and body_of(doc.getObject(c["object"])) is body]
    lifted = lift_all(doc, target=target, container=container, candidates=cands)
    return {"relabeled": relabeled["count"], "lifted": lifted["count"], "skipped": len(lifted["skipped"])}


def across_all_bodies(doc, *, target="Spreadsheet"):
    return apply_across_bodies(doc, lambda b: standardise_body(b, target=target))


# ------------------------------------------------------------ redundant merge

IDENTICAL_REL = 1e-9
IDENTICAL_DEV = 1e-9


def shapes_identical(a, b, budget=150):
    """True when two shapes agree on type, validity, topology counts exactly, on volume, area,
    bounding box and centre of mass to 1e-9 relative, and on sampled point-to-surface
    deviation both ways to 1e-9 mm."""
    ma, mb = geomhash.shape_metrics(a), geomhash.shape_metrics(b)
    if ma["null"] or mb["null"] or ma.get("empty") or mb.get("empty"):
        return False
    tight = {"rel_tol": IDENTICAL_REL}
    if geomhash.compare_metrics("", ma, mb, tight):
        return False
    if a.isSame(b):
        return True
    m = deviation.measure(a, b, budget)
    return max(m["max_cand_to_gold"], m["max_gold_to_cand"]) <= IDENTICAL_DEV


def _link_targets(obj, prop):
    try:
        val = getattr(obj, prop)
    except Exception:
        return []
    out = []

    def walk(v):
        if v is None:
            return
        if hasattr(v, "Name") and hasattr(v, "Document"):
            out.append(v)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
    walk(val)
    return out


def _consumers_ok(feat, body):
    """R3: the only consumers of ``feat`` are its Body and features whose BaseFeature it is,
    and those consumers use it through BaseFeature only."""
    nxt = []
    for c in feat.InList:
        if c is body:
            continue
        if getattr(c, "BaseFeature", None) is feat:
            for prop in c.PropertiesList:
                if prop == "BaseFeature":
                    continue
                if c.getTypeIdOfProperty(prop).startswith("App::PropertyLink") and feat in _link_targets(c, prop):
                    return None
            if any(feat.Name in e or f"<<{feat.Label}>>" in e for _, e in c.ExpressionEngine):
                return None
            nxt.append(c)
        else:
            return None
    return nxt


def find_redundant(doc, *, budget=150):
    """Features that safety rules R1 to R7 prove to be no-ops. Read-only; returns one record per feature."""
    out = []
    for body in bodies(doc):
        for feat in list(body.Group):
            if not feat.TypeId.startswith("PartDesign::") or not hasattr(feat, "BaseFeature"):
                continue                                    # R1: PartDesign solid features only
            base = feat.BaseFeature
            if base is None or base not in body.Group:
                continue                                    # R2: needs a previous solid in the same body
            if getattr(feat, "Suppressed", False):
                continue                                    # R4: suppressed means the user parked it
            if feat.ExpressionEngine:
                continue                                    # R5: parametric intent of its own
            nxt = _consumers_ok(feat, body)
            if nxt is None:
                continue                                    # R3
            if any(feat.Name in e or f"<<{feat.Label}>>" in e
                   for o in doc.Objects for _, e in o.ExpressionEngine):
                continue                                    # R6: no expression names it
            if (set(feat.State) | set(base.State)) & {"Invalid", "Error"}:
                continue
            sa, sb = _vchecks._shape(feat), _vchecks._shape(base)
            if sa is None or sb is None or not shapes_identical(sa, sb, budget):
                continue                                    # R7: result geometry equals the base
            out.append({"object": feat.Name, "body": body.Name, "base": base.Name,
                        "next": [c.Name for c in nxt], "tip": body.Tip is feat})
    return out


def merge_redundant(doc, *, dry_run=False, budget=150):
    """Remove the features ``find_redundant`` proves redundant, one at a time, re-checking after each.

    Rewires the next feature's BaseFeature to the removed feature's base and moves the Tip when
    the removed feature was the Tip. Returns ``removed`` for ``invariant``.
    """
    removed = []
    while True:
        cands = find_redundant(doc, budget=budget)
        if not cands or dry_run:
            return {"op": "merge_redundant", "candidates": cands if dry_run else [], "removed": removed,
                    "count": len(removed), "dry_run": dry_run}
        c = cands[0]
        feat, body, base = doc.getObject(c["object"]), doc.getObject(c["body"]), doc.getObject(c["base"])
        for nname in c["next"]:
            doc.getObject(nname).BaseFeature = base
        if c["tip"]:
            body.Tip = base
        body.removeObject(feat)
        doc.removeObject(feat.Name)
        removed.append(c["object"])
        doc.recompute()
