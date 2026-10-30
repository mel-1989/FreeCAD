# SPDX-License-Identifier: LGPL-2.1-or-later
"""Rows of the change-set panel, from a change set (plain dicts; no Qt, no FreeCAD)."""

GROUPS = ("added", "removed", "modified", "properties", "expressions", "geometry", "verify", "transactions")
TITLES = {
    "added": "Features added",
    "removed": "Features removed",
    "modified": "Features modified",
    "properties": "Properties",
    "expressions": "Expressions",
    "geometry": "Geometry",
    "verify": "Verification",
    "transactions": "Transactions",
}


def _fmt(v, limit=40):
    if v is None:
        return "-"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        return f"{v:.6g}"
    if isinstance(v, dict) and "value" in v and len(v) <= 2:
        return _fmt(v["value"], limit)
    if isinstance(v, dict) and "base" in v and "rotation" in v:   # a Placement
        text = "at (" + ", ".join(f"{c:.6g}" for c in v["base"]) + ")"
        if list(v["rotation"]) != [0.0, 0.0, 0.0, 1.0]:
            text += " rotated"
        return text
    if isinstance(v, (list, tuple)) and v and all(isinstance(c, (int, float)) for c in v):
        return "(" + ", ".join(f"{c:.6g}" for c in v) + ")"
    s = str(v)
    return s if len(s) <= limit else s[: limit - 1] + "..."


def _geometry_text(g):
    st = g.get("status")
    if st in ("added", "removed"):
        return f"{st}, {g.get('faces', 0)} faces, volume {_fmt(g.get('volume', 0.0))}"
    if st == "unchanged":
        return "unchanged"
    if st == "error":
        return "error: " + _fmt(g.get("error"), 60)
    text = (f"faces {g['matched']} matched ({g['unchanged_faces']} same, {len(g['modified_faces'])} modified), "
            f"{len(g['added_faces'])} added, {len(g['removed_faces'])} removed, "
            f"max deviation {_fmt(g['max_deviation'])}")
    r = g.get("region")
    if r and "added_volume" in r:
        text += f", +{_fmt(r['added_volume'])} / -{_fmt(r['removed_volume'])} mm3"
    return text


def build_rows(cs):
    """``{group: [row]}`` with a row ``{"text", "object"}`` (``object`` is the document object to select)."""
    f = cs["features"]
    rows = {
        "added": [{"text": f"{x['name']}  ({x['type']})", "object": x["name"]} for x in f["added"]],
        "removed": [{"text": f"{x['name']}  ({x['type']})", "object": x["name"]} for x in f["removed"]],
        "modified": [{"text": n, "object": n} for n in f["modified"]],
        "properties": [{"text": f"{p['object']}.{p['property']}: {_fmt(p['before'])} → {_fmt(p['after'])}",
                        "object": p["object"]} for p in cs["properties"]],
        "expressions": [{"text": f"{e['object']}.{e['path']}: {_fmt(e['before'])} → {_fmt(e['after'])}",
                         "object": e["object"]} for e in cs["expressions"]],
        "geometry": [{"text": f"{n}: {_geometry_text(g)}", "object": n}
                     for n, g in sorted(cs["geometry"].items()) if g.get("status") not in (None, "none")],
    }
    v = cs.get("verify") or {}
    rows["verify"] = ([{"text": f"regression: {c}", "object": None} for c in v.get("regressions", [])]
                      + [{"text": f"fixed: {c}", "object": None} for c in v.get("fixes", [])])
    t = cs.get("transactions") or {}
    rows["transactions"] = [{"text": f"{t.get('segments', 0)} undo segment(s), "
                                     f"{len(t.get('nested_opened', []))} nested transaction(s), "
                                     f"{t.get('nested_aborts', 0)} nested abort(s)", "object": None}]
    return rows


def counts(cs):
    return {k: len(v) for k, v in build_rows(cs).items()}
