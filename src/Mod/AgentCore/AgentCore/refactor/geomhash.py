# SPDX-License-Identifier: LGPL-2.1-or-later
"""Geometry snapshot and comparison for the refactor invariance check (F6, built on F2 and I3).

A snapshot records, for every shape-bearing object, the I3 golden metrics (type,
validity, volume, area, bounding box, centre of mass, topology counts) and keeps
a copy of each top-level body's shape so a sampled point-to-surface deviation
can be measured after the edit. The comparison uses I3's tolerance policy
(``tolerances.json``, vendored) and its deviation measure (``deviation.py``,
vendored), never a new measure.

``Snapshot.hash()`` is a short content hash of the metrics snapped to I3's grid
(2**-20 of the bounding-box diagonal). It is reported for humans and logs; the
verdict comes from ``compare`` with tolerances, because a value within noise of a
grid boundary can flip a snapped hash.
"""

import hashlib
import json
import math
import os
import platform

from AgentCore.verify import checks as _vchecks
from AgentCore.verify import run_checks as _run_checks

from . import deviation

_TOL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tolerances.json")
_GRID = 2.0**-20

_CLASS = {"darwin": "darwin-clang", "linux": "linux-gcc", "windows": "windows-msvc"}


def load_tolerances(platform_class=None, path=None):
    """The I3 tolerance block for this platform (``darwin-clang`` and so on)."""
    with open(path or _TOL_PATH, encoding="utf-8") as fh:
        raw = json.load(fh)
    cls = platform_class or _CLASS.get(platform.system().lower()) or raw["default_platform"]
    block = raw["platforms"].get(cls) or raw["platforms"][raw["default_platform"]]
    return {k: v for k, v in block.items() if k != "calibrated"}


def _snap(x, scale):
    g = _GRID * 2.0 ** math.ceil(math.log2(max(scale, 1e-12)))
    return float(f"{round(x / g) * g:.12g}")


def shape_metrics(shp):
    """I3 per-shape metrics, unsnapped floats (comparison applies tolerances)."""
    m = {"null": bool(shp.isNull())}
    if m["null"]:
        return m
    counts = {
        "solids": len(shp.Solids), "shells": len(shp.Shells), "faces": len(shp.Faces),
        "edges": len(shp.Edges), "vertexes": len(shp.Vertexes),
    }
    bb = shp.BoundBox
    if not bb.isValid():
        m.update({"empty": True, "type": shp.ShapeType, "valid": bool(shp.isValid()), "counts": counts})
        return m
    m.update({
        "type": shp.ShapeType,
        "valid": bool(shp.isValid()),
        "diag": max(bb.DiagonalLength, 1.0),
        "volume": float(shp.Volume),
        "area": float(shp.Area),
        "bbox": [bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax],
        "counts": counts,
    })
    try:
        c = shp.CenterOfGravity if hasattr(shp, "CenterOfGravity") else shp.CenterOfMass
        m["com"] = [c.x, c.y, c.z]
    except Exception:
        m["com"] = None
    return m


def _snapped(m):
    """The metrics snapped to I3's grid, for the hash."""
    if m.get("null") or m.get("empty") or "diag" not in m:
        return m
    d = m["diag"]
    out = {k: v for k, v in m.items() if k != "diag"}
    out["volume"] = _snap(m["volume"], d**3)
    out["area"] = _snap(m["area"], d**2)
    out["bbox"] = [_snap(v, d) for v in m["bbox"]]
    out["com"] = None if m["com"] is None else [_snap(v, d) for v in m["com"]]
    return out


class Snapshot:
    """Per-object geometry metrics plus copies of the top-level body shapes."""

    def __init__(self, objects, bodies, shapes, validity):
        self.objects = objects      # name -> {"type", "label", "state", "shape": metrics or None}
        self.bodies = bodies        # top-level solid object names
        self.shapes = shapes        # body name -> Part.Shape copy
        self.validity = validity    # F2 validity check result

    def hash(self, only=None):
        """Short sha256 over the grid-snapped metrics (all shape objects, or ``only``)."""
        payload = {
            n: _snapped(o["shape"])
            for n, o in sorted(self.objects.items())
            if o["shape"] is not None and (only is None or n in only)
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]

    def summary(self):
        return {
            "hash": self.hash(),
            "objects": len(self.objects),
            "shape_objects": sum(1 for o in self.objects.values() if o["shape"] is not None),
            "bodies": list(self.bodies),
        }


def snapshot(doc, *, max_deviation_bodies=40):
    """Measure ``doc`` as it stands (no recompute). Shapes are copied for the top-level bodies."""
    objects, shapes = {}, {}
    body_names = [o.Name for o in _vchecks.bodies(doc)]
    keep = set(body_names[:max_deviation_bodies])
    for o in doc.Objects:
        entry = {"type": o.TypeId, "label": o.Label, "state": list(o.State), "shape": None}
        shp = _vchecks._shape(o)
        if shp is not None:
            entry["shape"] = shape_metrics(shp)
            if o.Name in keep and not entry["shape"].get("empty"):
                shapes[o.Name] = shp.copy()
        objects[o.Name] = entry
    validity = _run_checks(doc, ["validity"])["results"][0]
    return Snapshot(objects, body_names, shapes, validity)


def _failed(entry):
    return "Invalid" in entry["state"] or "Error" in entry["state"]


def _close(a, b, tol):
    return abs(a - b) <= tol


def compare_metrics(name, a, b, tol):
    """Violations between two metric dicts of one object. Counts, type and validity are exact."""
    out = []
    if a["null"] != b["null"]:
        return [{"object": name, "field": "null", "before": a["null"], "after": b["null"]}]
    if a["null"]:
        return out
    for key in ("type", "valid", "empty", "counts"):
        if a.get(key) != b.get(key):
            out.append({"object": name, "field": key, "before": a.get(key), "after": b.get(key)})
    if "diag" in a and "diag" in b:
        d, rt = a["diag"], tol["rel_tol"]
        for key, scale in (("volume", d**3), ("area", d**2)):
            if not _close(a[key], b[key], rt * scale):
                out.append({"object": name, "field": key, "before": a[key], "after": b[key], "tolerance": rt * scale})
        for key in ("bbox", "com"):
            va, vb = a.get(key), b.get(key)
            if va is None or vb is None:
                if va != vb:
                    out.append({"object": name, "field": key, "before": va, "after": vb})
            elif not all(_close(x, y, rt * d) for x, y in zip(va, vb)):
                out.append({"object": name, "field": key, "before": va, "after": vb, "tolerance": rt * d})
    elif ("diag" in a) != ("diag" in b):
        out.append({"object": name, "field": "bbox", "before": a.get("bbox"), "after": b.get("bbox")})
    return out


def compare(before, after, tol=None, *, removed=(), added=(), budget=150):
    """All violations of geometry invariance between two snapshots (empty list: invariant).

    ``removed`` names objects the edit was allowed to delete (merged features);
    a top-level body may never vanish. ``added`` names shape-bearing objects the
    edit was allowed to create. Objects present on both sides are compared by
    Name, never by Label.
    """
    tol = tol or load_tolerances()
    removed, added = set(removed), set(added)
    v = []
    for name, ent in before.objects.items():
        if ent["shape"] is None:
            continue
        now = after.objects.get(name)
        if now is None or now["shape"] is None:
            if name in before.bodies:
                v.append({"object": name, "field": "body_missing"})
            elif name not in removed:
                v.append({"object": name, "field": "missing"})
            continue
        v += compare_metrics(name, ent["shape"], now["shape"], tol)
        if _failed(now) and not _failed(ent):
            v.append({"object": name, "field": "state", "before": ent["state"], "after": now["state"]})
    for name, ent in after.objects.items():
        if name not in before.objects and ent["shape"] is not None and name not in added:
            v.append({"object": name, "field": "added"})
    bad_before = set(before.validity["details"].get("invalid", {}))
    for name in after.validity["details"].get("invalid", {}):
        if name not in bad_before and name in before.objects:
            v.append({"object": name, "field": "newly_invalid"})
    for name in before.bodies:
        if name not in after.objects or after.objects[name]["shape"] is None:
            continue
        v += _body_deviation(name, before, after, tol, budget)
    return v


def _body_deviation(name, before, after, tol, budget):
    old = before.shapes.get(name)
    new = after.shapes.get(name)
    if old is None:
        return []
    if new is None:
        return [{"object": name, "field": "deviation", "error": "no shape after"}]
    meas = deviation.measure(new, old, budget)
    diag = before.objects[name]["shape"].get("diag", 1.0)
    lim_max = max(tol["dev_max_abs"], tol["dev_max_rel"] * diag)
    lim_rms = max(tol["dev_rms_abs"], tol["dev_rms_rel"] * diag)
    worst = max(meas["max_cand_to_gold"], meas["max_gold_to_cand"])
    rms = max(meas["rms_cand_to_gold"], meas["rms_gold_to_cand"])
    if worst > lim_max or rms > lim_rms:
        return [{"object": name, "field": "deviation", "max": worst, "rms": rms,
                 "limit_max": lim_max, "limit_rms": lim_rms, "points": meas["points"]}]
    return []


def max_deviation(before, after, budget=150):
    """The largest measured body deviation between two snapshots (for reporting)."""
    worst = 0.0
    for name, old in before.shapes.items():
        new = after.shapes.get(name)
        if new is not None:
            m = deviation.measure(new, old, budget)
            worst = max(worst, m["max_cand_to_gold"], m["max_gold_to_cand"])
    return worst
