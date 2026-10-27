# SPDX-License-Identifier: LGPL-2.1-or-later
"""Property, expression and feature layer of a change set, from two F1 dumps."""

from .compare import TRANSIENT_CACHES

# Keys of an F1 object record compared as "structure" (not properties).
_STRUCT_KEYS = ("type", "kind", "proxy", "in_list", "out_list")


def _by_name(d):
    return {o["name"]: o for o in d.get("objects", [])}


def _props(o):
    return {p["name"]: p for p in o.get("properties", []) if p["name"] not in TRANSIENT_CACHES}


def _exprs(o):
    return {e["path"]: e["expression"] for e in o.get("expressions", [])}


def object_changes(before, after):
    """Diff two canonical dumps.

    Returns ``{"features": {added, removed, modified}, "properties": [...],
    "expressions": [...], "structure": [...], "shape_changed": [names]}``.
    """
    b, a = _by_name(before), _by_name(after)
    added = sorted(set(a) - set(b))
    removed = sorted(set(b) - set(a))
    modified, props, exprs, struct, shape_changed = [], [], [], [], []
    for name in sorted(set(a) & set(b)):
        ob, oa = b[name], a[name]
        touched = False
        pb, pa = _props(ob), _props(oa)
        for pn in sorted(set(pb) | set(pa)):
            vb = pb.get(pn, {}).get("value") if pn in pb else None
            va = pa.get(pn, {}).get("value") if pn in pa else None
            if pn not in pb or pn not in pa or vb != va:
                change = "added" if pn not in pb else "removed" if pn not in pa else "changed"
                props.append({"object": name, "property": pn, "change": change, "before": vb, "after": va,
                              "type": (pa.get(pn) or pb.get(pn))["type"]})
                touched = True
        eb, ea = _exprs(ob), _exprs(oa)
        for path in sorted(set(eb) | set(ea)):
            if eb.get(path) != ea.get(path):
                exprs.append({"object": name, "path": path, "before": eb.get(path), "after": ea.get(path)})
                touched = True
        for key in _STRUCT_KEYS:
            if ob.get(key) != oa.get(key):
                struct.append({"object": name, "key": key, "before": ob.get(key), "after": oa.get(key)})
                touched = True
        if ob.get("label") != oa.get("label"):
            touched = True
        if ob.get("shape") != oa.get("shape"):
            shape_changed.append(name)
            touched = True
        if ob.get("detail") != oa.get("detail"):
            struct.append({"object": name, "key": "detail", "before": ob.get("detail"), "after": oa.get("detail")})
            touched = True
        if touched:
            modified.append(name)
    for name in added:
        for e in a[name].get("expressions", []):
            exprs.append({"object": name, "path": e["path"], "before": None, "after": e["expression"]})
    for name in removed:
        for e in b[name].get("expressions", []):
            exprs.append({"object": name, "path": e["path"], "before": e["expression"], "after": None})
    return {
        "features": {
            "added": [{"name": n, "type": a[n]["type"], "label": a[n]["label"]} for n in added],
            "removed": [{"name": n, "type": b[n]["type"], "label": b[n]["label"]} for n in removed],
            "modified": modified,
        },
        "properties": props,
        "expressions": exprs,
        "structure": struct,
        "shape_changed": shape_changed,
    }


def verify_delta(before, after):
    """Checks that went from ok to failing (regressions) and back (fixes)."""
    if not before or not after:
        return {"regressions": [], "fixes": []}
    rb = {r["check"]: r for r in before.get("results", [])}
    ra = {r["check"]: r for r in after.get("results", [])}
    reg = sorted(c for c in ra if c in rb and rb[c]["ok"] and not ra[c]["ok"])
    fix = sorted(c for c in ra if c in rb and not rb[c]["ok"] and ra[c]["ok"])
    return {"regressions": reg, "fixes": fix}
