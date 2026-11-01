# SPDX-License-Identifier: LGPL-2.1-or-later
"""Revision diff of one vaulted document (G03 slice 6a, check 4).

``Vault.diff(name, rev_a, rev_b)`` delegates here. Both revisions are exported with the rest of the vault as it
was at their commits and opened; the object and property layer is F3's ``AgentCore.diff`` (``snapshot_dump`` and
``changeset.object_changes``, no second differ); changed shapes carry I3's golden metrics (``shape_metrics``
and the sampled point-to-surface ``deviation.measure`` of ``harness/golden``); the BOM delta compares the
indented BOMs of the two revisions (``PDM.bom``).

Result (a plain, JSON-safe dict, one per document, so an ECO can list the diff of each document it changes)::

    {"schema": "pdm.diff/1", "document": name,
     "from": {"revision", "sha", "document"}, "to": {...},
     "empty": bool,                       # nothing differs at all
     "objects": {"added": [{name, type, label}], "removed": [...], "modified": [names], "renamed": [{object, before, after}]},
     "properties": [{object, property, change, before, after, type}],   # inputs; Label is under "renamed"
     "outputs": [same shape],             # properties holding computed shapes (a Pad's AddSubShape)
     "expressions": [...], "structure": [...],
     "shapes": [{object, status: changed|added|removed, before, after, delta, deviation}],
     "bom": {"added": [...], "removed": [...], "changed": [...]}}

``before``/``after`` of a shape are I3's ``shape_metrics`` (volume, area, bbox, counts, centre of gravity);
``delta`` has ``volume`` and ``area``; ``deviation`` is ``deviation.measure`` (max and RMS point-to-surface
distance in both directions) for changed shapes. The golden harness is found through ``PDM_GOLDEN_DIR`` or
``$CADFORK_CTL/harness/golden``; without it ``shapes`` entries carry no metrics and ``golden_metrics`` is
``"unavailable"``.
"""

import os
import types

from PDM import bom as B
from PDM import vault as V

SCHEMA = "pdm.diff/1"
_GOLDEN = {}


def _golden():
    """``(shape_metrics, measure)`` from harness/golden, or None. probe.py runs its main() when imported, so
    its source up to ``def main():`` is executed (its own functions, not copies)."""
    if "fns" in _GOLDEN:
        return _GOLDEN["fns"]
    fns = None
    d = os.environ.get("PDM_GOLDEN_DIR") or os.path.join(os.environ.get("CADFORK_CTL", ""), "harness", "golden")
    path = os.path.join(d, "probe.py")
    if os.path.isfile(path):
        import sys
        with open(path, encoding="utf-8") as f:
            src = f.read().split("\ndef main():")[0]
        saved = {k: os.environ.get(k) for k in ("CADFORK_GOLDEN_ROOT", "CADFORK_GOLDEN_MANIFEST", "CADFORK_GOLDEN_OUT")}
        for k in saved:
            os.environ.setdefault(k, "")
        mod = types.ModuleType("pdm_golden_probe")
        mod.__file__ = path
        inserted = sys.path[:1]
        try:
            exec(compile(src, path, "exec"), mod.__dict__)
            fns = (mod.shape_metrics, mod.deviation.measure)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
            if sys.path[:1] != inserted and d in sys.path:
                sys.path.remove(d)
    _GOLDEN["fns"] = fns
    return fns


def _object_shape(doc, name):
    o = doc.getObject(name)
    shp = getattr(o, "Shape", None) if o is not None else None
    return None if shp is None or shp.isNull() else shp


def _is_output(p):
    return str(p.get("type", "")).endswith("PropertyPartShape") or p["property"] == "Shape"


def _shapes(doc_a, doc_b, names, status_of):
    golden = _golden()
    from AgentCore.diff.compare import differences
    out = []
    for n in sorted(names):
        sa, sb = _object_shape(doc_a, n) if status_of[n] != "added" else None, \
            _object_shape(doc_b, n) if status_of[n] != "removed" else None
        if sa is None and sb is None:
            continue
        entry = {"object": n, "status": status_of[n], "before": None, "after": None, "delta": None, "deviation": None}
        if golden is None:
            entry["golden_metrics"] = "unavailable"
            out.append(entry)
            continue
        metrics, measure = golden
        ma = metrics(sa) if sa is not None else None
        mb = metrics(sb) if sb is not None else None
        entry["before"], entry["after"] = ma, mb
        if ma is not None and mb is not None:
            entry["delta"] = {"volume": mb.get("volume", 0.0) - ma.get("volume", 0.0),
                              "area": mb.get("area", 0.0) - ma.get("area", 0.0)}
            if sa.Solids or sb.Solids or sa.Faces:
                entry["deviation"] = measure(sb, sa)
            same = not differences(ma, mb) and (entry["deviation"] is None or entry["deviation"]["max_cand_to_gold"] < 1e-7
                                                and entry["deviation"]["max_gold_to_cand"] < 1e-7)
            if same:
                continue  # the dump saw a change, the geometry did not move (summary noise)
        out.append(entry)
    return out


def diff(vault, name, rev_a, rev_b):
    V._check_name(name)
    vault._fetch()
    ra, rb = vault.find_revision(name, rev_a), vault.find_revision(name, rev_b)
    na, nb = ra.get("document") or name, rb.get("document") or name
    res = {"schema": SCHEMA, "document": name,
           "from": {"revision": rev_a, "sha": ra["sha"], "document": na},
           "to": {"revision": rev_b, "sha": rb["sha"], "document": nb}}
    from AgentCore.diff import snapshot_dump
    from AgentCore.diff.changeset import object_changes
    with B.Tree(vault, ra["sha"]) as ta, B.Tree(vault, rb["sha"]) as tb:
        da, db = ta.open(na), tb.open(nb)
        dump_a, dump_b = snapshot_dump(da), snapshot_dump(db)
        ch = object_changes(dump_a, dump_b)
        renamed, props, outputs = [], [], []
        for p in ch["properties"]:
            if p["property"] == "Label" and p["change"] == "changed":
                renamed.append({"object": p["object"], "before": p["before"], "after": p["after"]})
            elif _is_output(p):
                outputs.append(p)
            else:
                props.append(p)
        content = {p["object"] for p in props + outputs} | {e["object"] for e in ch["expressions"]} \
            | {s["object"] for s in ch["structure"]} | set(ch["shape_changed"])
        modified = sorted(n for n in ch["features"]["modified"] if n in content)
        status = {n: "changed" for n in ch["shape_changed"]}
        status.update({f["name"]: "added" for f in ch["features"]["added"]})
        status.update({f["name"]: "removed" for f in ch["features"]["removed"]})
        shapes = _shapes(da, db, set(status), status)
        bom_delta = B.delta(B.bom_at(ta, na), B.bom_at(tb, nb))
    res.update({
        "objects": {"added": ch["features"]["added"], "removed": ch["features"]["removed"], "modified": modified,
                    "renamed": sorted(renamed, key=lambda r: r["object"])},
        "properties": props, "outputs": outputs, "expressions": ch["expressions"], "structure": ch["structure"],
        "shapes": shapes, "bom": bom_delta})
    res["empty"] = not (ch["features"]["added"] or ch["features"]["removed"] or modified or renamed or props
                        or outputs or ch["expressions"] or ch["structure"] or shapes
                        or bom_delta["added"] or bom_delta["removed"] or bom_delta["changed"])
    return res
