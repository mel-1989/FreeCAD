# SPDX-License-Identifier: LGPL-2.1-or-later
"""The verify check library. Each check: ``fn(doc, params) -> (ok, entities, details)``.

Checks never mutate geometry (they recompute only in ``recompute``). Tolerances
come from tolerances.json, never from constants here.
"""

import itertools

from . import thickness as _thickness
from .entities import entity
from .tolerances import resolve


# ---------------------------------------------------------------- selection

def _shape(obj):
    s = getattr(obj, "Shape", None)
    if s is None:
        return None
    try:
        return None if s.isNull() else s
    except Exception:
        return None


def shape_objects(doc, names=None):
    out = []
    for o in doc.Objects:
        if names is not None and o.Name not in names:
            continue
        if _shape(o) is not None:
            out.append(o)
    return out


def bodies(doc, names=None):
    """Top-level solid objects: shape has solids and no shape-bearing object consumes it."""
    if names is not None:
        return [o for o in shape_objects(doc, names) if _shape(o).Solids]
    res = []
    for o in shape_objects(doc):
        if not _shape(o).Solids:
            continue
        if any(_shape(p) is not None for p in o.InList):
            continue
        res.append(o)
    return res


def _vec(v):
    return [float(v.x), float(v.y), float(v.z)]


# ------------------------------------------------------------------- checks

def check_recompute(doc, params):
    doc.recompute()
    bad = {}
    for o in doc.Objects:
        st = list(o.State)
        if "Invalid" in st or "Error" in st or not o.isValid():
            bad[o.Name] = {"state": st, "status": o.getStatusString()}
    return (not bad), [entity(doc.getObject(n)) for n in bad], {"objects": len(doc.Objects), "failed": bad}


def check_validity(doc, params):
    bad = {}
    n = 0
    for o in shape_objects(doc, params.get("objects")):
        n += 1
        s = _shape(o)
        msg = None
        if not s.isValid():
            msg = "isValid() is False"
            try:
                s.check(True)
            except Exception as exc:  # BRepCheck report is raised as an exception
                msg = f"BRepCheck: {exc}"
        if msg:
            bad[o.Name] = msg
    return (not bad), [entity(doc.getObject(k)) for k in bad], {"checked": n, "invalid": bad}


def _close(a, b, tol):
    return abs(a - b) <= tol


def check_bbox(doc, params):
    tol = resolve(params)["linear_mm"]
    expect = params.get("expect") or {}
    info, bad = {}, {}
    for o in shape_objects(doc, params.get("objects") or (list(expect) if expect and "*" not in expect else None)):
        bb = _shape(o).BoundBox
        rec = {"min": [bb.XMin, bb.YMin, bb.ZMin],
               "max": [bb.XMax, bb.YMax, bb.ZMax], "size": [bb.XLength, bb.YLength, bb.ZLength]}
        info[o.Name] = rec
        exp = expect.get(o.Name)
        if exp:
            for key, got in rec.items():
                if key in exp and not all(_close(g, e, tol) for g, e in zip(got, exp[key])):
                    bad.setdefault(o.Name, {})[key] = {"expected": exp[key], "got": got}
    missing = [n for n in expect if n != "*" and n not in info]
    for n in missing:
        bad[n] = {"error": "object missing or has no shape"}
    if "*" in expect and info:
        lo = [min(r["min"][i] for r in info.values()) for i in range(3)]
        hi = [max(r["max"][i] for r in info.values()) for i in range(3)]
        tot = {"min": lo, "max": hi, "size": [h - l for h, l in zip(hi, lo)]}
        info["*"] = tot
        for key, exp in expect["*"].items():
            if not all(_close(g, e, tol) for g, e in zip(tot[key], exp)):
                bad["*"] = {key: {"expected": exp, "got": tot[key]}}
    return (not bad), [entity(doc.getObject(n)) for n in bad if doc.getObject(n)], {"bbox": info, "mismatch": bad}


def _pairs(doc, params):
    objs = bodies(doc, params.get("objects"))
    skip = {frozenset(p) for p in params.get("allow", [])}
    for a, b in itertools.combinations(objs, 2):
        if frozenset((a.Name, b.Name)) not in skip:
            yield a, b


def check_interference(doc, params):
    tol = resolve(params)["interference_volume_mm3"]
    hits, n = [], 0
    for a, b in _pairs(doc, params):
        n += 1
        sa, sb = _shape(a), _shape(b)
        if not sa.BoundBox.intersect(sb.BoundBox):
            continue
        vol = sa.common(sb).Volume
        if vol > tol:
            hits.append({"a": a.Name, "b": b.Name, "volume_mm3": vol})
    ents = sorted({entity(doc.getObject(x)) for h in hits for x in (h["a"], h["b"])})
    return (not hits), ents, {"pairs_checked": n, "interferences": hits}


def _sub(kind, idx):
    return f"{kind}{idx + 1}"


def check_clearance(doc, params):
    tols = resolve(params)
    need = float(params.get("min", 0.0))
    slack = tols["clearance_abs_mm"]
    pairs, bad, ents = [], [], set()
    for a, b in _pairs(doc, params):
        dist, pts, info = _shape(a).distToShape(_shape(b))
        rec = {"a": a.Name, "b": b.Name, "distance_mm": dist}
        if info and pts:
            # info is one (typeA, idxA, paramA, typeB, idxB, paramB) tuple per solution
            sol = info[0]
            ea, eb = entity(a, _sub(sol[0], sol[1])), entity(b, _sub(sol[3], sol[4]))
            rec["entities"] = [ea, eb]
            rec["points"] = [_vec(pts[0][0]), _vec(pts[0][1])]
        pairs.append(rec)
        if dist < need - slack:
            bad.append(rec)
            ents.update(rec.get("entities", [entity(a), entity(b)]))
    return (not bad), sorted(ents), {"min_required_mm": need, "pairs": pairs, "violations": bad}


def _density(obj, params):
    """kg/mm^3 from explicit params, then the Material module, else None."""
    over = (params.get("densities") or {}).get(obj.Name)
    if over is not None:
        return over * 1e-9, "param"  # given in kg/m^3
    for holder in [obj] + list(obj.InList):
        mat = getattr(holder, "ShapeMaterial", None)
        if mat is not None:
            try:
                if mat.Name != "Default" and mat.hasPhysicalProperty("Density"):
                    return float(mat.getPhysicalValue("Density").Value), f"material:{mat.Name}"
            except Exception:
                pass
    if params.get("default_density") is not None:
        return params["default_density"] * 1e-9, "default"
    return None, None


def _com(shape):
    """Volume-weighted centre of mass over the solids (compounds have no CenterOfMass)."""
    sols = shape.Solids
    vol = sum(x.Volume for x in sols)
    return [sum(x.CenterOfMass[i] * x.Volume for x in sols) / vol for i in range(3)] if vol else [0.0, 0.0, 0.0]


def check_mass(doc, params):
    tol = resolve(params)
    info, bad = {}, {}
    tv = tm = 0.0
    com_num = [0.0, 0.0, 0.0]
    for o in bodies(doc, params.get("objects")):
        s = _shape(o)
        dens, src = _density(o, params)
        vol = s.Volume
        rec = {"volume_mm3": vol, "area_mm2": s.Area, "com": _com(s),
               "density_kg_mm3": dens, "density_source": src,
               "mass_kg": None if dens is None else vol * dens}
        info[o.Name] = rec
        tv += vol
        if dens is not None:
            tm += vol * dens
            com_num = [c + m * vol * dens for c, m in zip(com_num, rec["com"])]
        exp = (params.get("expect") or {}).get(o.Name)
        if exp:
            for key, rel in (("volume_mm3", tol["volume_rel"]), ("mass_kg", tol["mass_rel"]), ("area_mm2", tol["area_rel"])):
                if key in exp:
                    got = rec[key]
                    if got is None or abs(got - exp[key]) > rel * max(abs(exp[key]), 1e-30):
                        bad.setdefault(o.Name, {})[key] = {"expected": exp[key], "got": got}
            if "com" in exp and not all(_close(g, e, tol["com_mm"]) for g, e in zip(rec["com"], exp["com"])):
                bad.setdefault(o.Name, {})["com"] = {"expected": exp["com"], "got": rec["com"]}
    for n in (params.get("expect") or {}):
        if n not in info:
            bad[n] = {"error": "object missing or not a solid body"}
    total = {"volume_mm3": tv, "mass_kg": tm if tm else None,
             "com": [c / tm for c in com_num] if tm else None}
    return (not bad), [entity(doc.getObject(n)) for n in bad if doc.getObject(n)], {"bodies": info, "total": total, "mismatch": bad}


def check_thickness(doc, params):
    tols = resolve(params)
    need = params.get("min_required")
    samples = int(params.get("samples", 5))
    budget = params.get("time_budget_s")
    per, bad, ents = {}, {}, set()
    overall = None
    complete = True
    for o in bodies(doc, params.get("objects")):
        r = _thickness.min_wall_thickness(_shape(o), samples=samples, time_budget_s=budget)
        per[o.Name] = r
        complete = complete and r.get("complete", True)
        if r["min"] is not None and (overall is None or r["min"] < overall):
            overall = r["min"]
        if need is not None and r["min"] is not None and r["min"] < need - max(tols["thickness_abs_mm"], tols["thickness_rel"] * need):
            bad[o.Name] = r["min"]
            ents.add(entity(o, f"Face{r['face']}" if r.get("face") else ""))
    return (not bad), sorted(ents), {"min_thickness_mm": overall, "min_required_mm": need,
                                    "complete": complete, "bodies": per}


def _selftest_abort(doc, params):
    import os
    os.abort()


def _selftest_kill(doc, params):
    import os
    import signal
    os.kill(os.getpid(), signal.SIGKILL)


def _selftest_sleep(doc, params):
    import time
    time.sleep(float(params.get("seconds", 60)))
    return True, [], {}


def _selftest_raise(doc, params):
    raise RuntimeError("selftest")


CHECKS = {
    "recompute": check_recompute,
    "validity": check_validity,
    "bbox": check_bbox,
    "interference": check_interference,
    "clearance": check_clearance,
    "mass": check_mass,
    "thickness": check_thickness,
    "_selftest_abort": _selftest_abort,
    "_selftest_kill": _selftest_kill,
    "_selftest_sleep": _selftest_sleep,
    "_selftest_raise": _selftest_raise,
}

DEFAULT_CHECKS = ["recompute", "validity", "bbox", "interference", "mass"]


def normalize(spec):
    """A check spec is a name or {"check": name, **params}."""
    if isinstance(spec, str):
        return spec, {}
    spec = dict(spec)
    return spec.pop("check"), spec


def run_one(doc, spec):
    import traceback

    name, params = normalize(spec)
    fn = CHECKS.get(name)
    if fn is None:
        return {"check": name, "ok": False, "status": "error", "entities": [], "details": {"error": "unknown check"}}
    try:
        out = fn(doc, params)
        ok, ents, details = out if isinstance(out, tuple) and len(out) == 3 else (out[0], out[1], out[2])
        return {"check": name, "ok": bool(ok), "status": "ok", "entities": list(ents), "details": details}
    except Exception:
        return {"check": name, "ok": False, "status": "error", "entities": [],
                "details": {"error": traceback.format_exc(limit=6)}}
