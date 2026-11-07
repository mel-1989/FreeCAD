# SPDX-License-Identifier: LGPL-2.1-or-later
"""Input hash and the stale flag. The flag is recomputed on every call and never cached, so it cannot go missing after an edit."""

import hashlib
import json

from . import model
from .spec import parse_quantity, resolve_material, Refusal


def _sig(doc, objname, sub):
    try:
        sh = model.element(doc, objname, sub)
    except LookupError:
        return ["lost", objname, sub]
    t = sh.ShapeType
    meas = sh.Area if t == "Face" else (sh.Length if t == "Edge" else 0.0)
    c = sh.CenterOfMass if t in ("Face", "Edge") else sh.Point
    return [t, round(meas, 6), [round(c.x, 6), round(c.y, 6), round(c.z, 6)]]


def _material(spec):
    try:
        return {k: (round(v, 9) if isinstance(v, float) else v) for k, v in resolve_material(spec["material"]).items()}
    except Refusal:
        return {"raw": json.dumps(spec["material"], sort_keys=True)}


def input_hash(doc, spec, live=None):
    """sha256 of: the canonical spec, the body's volume and bounding box, the signature of every referenced element as it resolves now, the
    resolved material values and the options (the options are part of the spec)."""
    live = live or {}
    body = doc.getObject(spec["body"])
    if body is not None and hasattr(body, "Shape") and not body.Shape.isNull():
        bb = body.Shape.BoundBox
        bd = [round(body.Shape.Volume, 6)] + [round(v, 6) for v in (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)]
    else:
        bd = None
    refs = []
    for role, items in (("S", spec["supports"]), ("L", spec["loads"])):
        for k, it in enumerate(items):
            for o, s in (live.get((role, k)) or it.get("refs", [])):
                refs.append([role, k, o, s, _sig(doc, o, s)])
    payload = {"spec": spec, "body": bd, "refs": refs, "material": _material(spec)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def is_current(h, stored):
    """True when the stored hash is the hash of the model as it is now."""
    return h == stored


def stale_flag(h, stored):
    return h != stored
