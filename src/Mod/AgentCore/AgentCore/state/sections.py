# SPDX-License-Identifier: LGPL-2.1-or-later
"""Per-kind detail sections: Sketcher, Spreadsheet, VarSet, PartDesign Body,
Assembly and joints, TechDraw pages, views and dimensions."""

from .values import fnum, link_value, property_value, to_json, vec

VARSET_BASE = {"ExpressionEngine", "Label", "Label2", "Visibility", "ExposedVarSet"}


def kind_of(obj):
    t = obj.TypeId
    d = obj.isDerivedFrom
    if d("Sketcher::SketchObject"):
        return "sketch"
    if d("Spreadsheet::Sheet"):
        return "spreadsheet"
    if t == "App::VarSet" or d("App::VarSet"):
        return "varset"
    if d("PartDesign::Body"):
        return "body"
    if t.startswith("Assembly::AssemblyObject") or d("Assembly::AssemblyObject"):
        return "assembly"
    if hasattr(obj, "JointType") and hasattr(obj, "Reference1"):
        return "joint"
    if d("TechDraw::DrawPage"):
        return "techdraw_page"
    if d("TechDraw::DrawViewDimension"):
        return "techdraw_dimension"
    if d("TechDraw::DrawView"):
        return "techdraw_view"
    if d("PartDesign::Feature"):
        return "partdesign_feature"
    if d("App::Origin") or d("App::OriginFeature") or d("App::DatumElement"):
        return "origin"
    if d("Part::Feature"):
        return "part_feature"
    if d("App::DocumentObjectGroup") or d("App::Part"):
        return "group"
    return "other"


def _attr(obj, name, default=None):
    try:
        return getattr(obj, name)
    except Exception:
        return default


def _ints(v):
    try:
        return sorted(int(x) for x in v)
    except Exception:
        return []


def _geometry(g):
    t = g.TypeId
    out = {"type": t}
    for key in ("StartPoint", "EndPoint", "Center", "Location"):
        v = _attr(g, key)
        if v is not None and hasattr(v, "x"):
            out[key[0].lower() + key[1:]] = vec(v)
    for key in ("Radius", "MajorRadius", "MinorRadius", "FirstParameter", "LastParameter", "Degree"):
        v = _attr(g, key)
        if isinstance(v, (int, float)):
            out[key[0].lower() + key[1:]] = fnum(v)
    if "BSpline" in t:
        try:
            out["poles"] = [vec(p) for p in g.getPoles()]
        except Exception:
            pass
    if t == "Part::GeomPoint":
        out["point"] = [fnum(g.X), fnum(g.Y), fnum(g.Z)]
    return out


def sketch_detail(sk):
    was_touched = "Touched" in sk.State
    try:
        status = int(sk.solve())
    except Exception:
        status = None
    # solve() is a read here; do not leave the sketch touched by it.
    if not was_touched and "Touched" in sk.State:
        try:
            sk.purgeTouched()
        except Exception:
            pass
    geometry = []
    for i, g in enumerate(sk.Geometry):
        e = _geometry(g)
        e["index"] = i
        try:
            e["construction"] = bool(sk.getConstruction(i))
        except Exception:
            e["construction"] = False
        geometry.append(e)
    constraints = []
    for i, c in enumerate(sk.Constraints):
        constraints.append({
            "index": i,
            "type": c.Type,
            "name": c.Name,
            "first": c.First, "first_pos": c.FirstPos,
            "second": c.Second, "second_pos": c.SecondPos,
            "third": c.Third, "third_pos": c.ThirdPos,
            "value": fnum(c.Value),
            "driving": bool(c.Driving),
            "active": bool(c.IsActive),
        })
    try:
        dep = [[int(a), int(b)] for a, b in sk.getGeometryWithDependentParameters()]
    except Exception:
        dep = []
    ext = []
    try:
        for o, subs in sk.ExternalGeometry:
            for s in (subs if isinstance(subs, (list, tuple)) else [subs]):
                ext.append({"object": o.Name, "sub": str(s)})
    except Exception:
        pass
    return {
        "solver": {
            "status": status,
            "dof": _attr(sk, "DoF"),
            "fully_constrained": bool(_attr(sk, "FullyConstrained", False)),
            "conflicting": _ints(_attr(sk, "ConflictingConstraints", [])),
            "redundant": _ints(_attr(sk, "RedundantConstraints", [])),
            "partially_redundant": _ints(_attr(sk, "PartiallyRedundantConstraints", [])),
            "malformed": _ints(_attr(sk, "MalformedConstraints", [])),
        },
        "geometry": geometry,
        "constraints": constraints,
        "dependent_parameters": dep,
        "external_geometry": ext,
        "attachment": {
            "support": link_value(_attr(sk, "AttachmentSupport")),
            "map_mode": _attr(sk, "MapMode"),
        },
    }


def _cell_sort_key(addr):
    col = "".join(ch for ch in addr if ch.isalpha())
    row = "".join(ch for ch in addr if ch.isdigit())
    return (int(row or 0), len(col), col)


def sheet_detail(sh):
    cells = []
    for addr in sorted(sh.getUsedCells(), key=_cell_sort_key):
        try:
            content = sh.getContents(addr)
        except Exception:
            content = ""
        try:
            value = to_json(sh.get(addr))
        except Exception as e:  # noqa: BLE001
            value = {"error": str(e)[:200]}
        try:
            alias = sh.getAlias(addr) or None
        except Exception:
            alias = None
        cells.append({"address": addr, "content": content, "value": value, "alias": alias})
    return {"cells": cells}


def varset_detail(vs):
    exprs = dict(vs.ExpressionEngine or [])
    out = []
    for name in sorted(vs.PropertiesList):
        if name in VARSET_BASE:
            continue
        try:
            ptype = vs.getTypeIdOfProperty(name)
        except Exception:
            ptype = ""
        out.append({
            "name": name,
            "type": ptype,
            "group": vs.getGroupOfProperty(name),
            "value": property_value(vs, name, ptype),
            "expression": exprs.get(name),
        })
    return {"variables": out}


def body_detail(body):
    feats = [o for o in (body.Group or [])]
    tip = _attr(body, "Tip")
    base = _attr(body, "BaseFeature")
    return {
        "tip": tip.Name if tip else None,
        "base_feature": base.Name if base else None,
        "origin": body.Origin.Name if _attr(body, "Origin") else None,
        "features": [{
            "name": f.Name,
            "type": f.TypeId,
            "is_solid_feature": bool(f.isDerivedFrom("PartDesign::Feature")),
            "profile": (lambda p: p[0].Name if isinstance(p, tuple) and p else (p.Name if p is not None and hasattr(p, "Name") else None))(_attr(f, "Profile")),
        } for f in feats],
    }


def joint_detail(j):
    out = {"joint_type": str(_attr(j, "JointType"))}
    for key in ("Reference1", "Reference2"):
        out[key.lower()] = link_value(_attr(j, key))
    for key in ("Activated", "Distance", "Distance2", "Angle", "Angle2", "Offset1", "Offset2", "Placement1", "Placement2"):
        if key in j.PropertiesList:
            out[key.lower()] = to_json(_attr(j, key))
    return out


def assembly_detail(asm):
    joints, comps, grounded = [], [], []

    def walk(group):
        for o in group or []:
            if o.isDerivedFrom("Assembly::JointGroup"):
                for j in o.Group or []:
                    if hasattr(j, "ObjectToGround"):
                        g = _attr(j, "ObjectToGround")
                        grounded.append({"joint": j.Name, "object": g.Name if g else None})
                    elif hasattr(j, "JointType"):
                        joints.append(j.Name)
            elif o.isDerivedFrom("App::Origin"):
                continue
            else:
                comps.append(o.Name)
    walk(asm.Group)
    return {"components": comps, "joints": joints, "grounded": grounded}


def techdraw_page_detail(p):
    tmpl = _attr(p, "Template")
    views = []
    for v in _attr(p, "Views", []) or []:
        views.append({"name": v.Name, "type": v.TypeId})
    return {
        "template": tmpl.Name if tmpl else None,
        "template_file": (str(_attr(tmpl, "Template", "")).rsplit("/", 1)[-1] or None) if tmpl else None,
        "scale": fnum(_attr(p, "Scale", 1.0)),
        "views": views,
    }


def techdraw_view_detail(v):
    out = {"x": fnum(_attr(v, "X", 0)), "y": fnum(_attr(v, "Y", 0)), "scale": fnum(_attr(v, "Scale", 1.0))}
    src = _attr(v, "Source")
    if src is not None:
        out["sources"] = [o.Name for o in src]
    d = _attr(v, "Direction")
    if d is not None and hasattr(d, "x"):
        out["direction"] = vec(d)
    for key, fn in (("visible_edges", "getVisibleEdges"), ("hidden_edges", "getHiddenEdges")):
        try:
            out[key] = len(getattr(v, fn)())
        except Exception:
            pass
    return out


def techdraw_dimension_detail(d):
    out = {"dimension_type": str(_attr(d, "Type")), "references_2d": link_value(_attr(d, "References2D")), "references_3d": link_value(_attr(d, "References3D"))}
    try:
        out["text"] = d.getText()
    except Exception:
        out["text"] = None
    try:
        out["raw_value"] = fnum(d.getRawValue())
    except Exception:
        pass
    return out


DETAIL = {
    "sketch": sketch_detail,
    "spreadsheet": sheet_detail,
    "varset": varset_detail,
    "body": body_detail,
    "joint": joint_detail,
    "assembly": assembly_detail,
    "techdraw_page": techdraw_page_detail,
    "techdraw_view": techdraw_view_detail,
    "techdraw_dimension": techdraw_dimension_detail,
}


def detail(obj, kind):
    fn = DETAIL.get(kind)
    if fn is None:
        return None
    try:
        return fn(obj)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:200]}
