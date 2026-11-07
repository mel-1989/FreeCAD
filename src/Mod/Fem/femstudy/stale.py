# SPDX-License-Identifier: LGPL-2.1-or-later
"""Input hash and the stale flag. The flag is recomputed on every call and never cached, so it cannot go missing after an edit."""

import hashlib
import json

from . import model
from .spec import parse_quantity, resolve_material, Refusal


HASH_FORM = 2  # 1 hashed volume, bounding box and referenced elements only; 2 adds every face of the body. A stored hash of form 1 can never match.
ROUND = 4  # decimals (mm, mm^2): enough to see a 0.1 micron move, coarse enough to survive a save and a reopen


def _r(x):
    return round(x, ROUND) + 0.0  # + 0.0 turns -0.0 into 0.0


def _sig(doc, objname, sub):
    try:
        sh = model.element(doc, objname, sub)
    except LookupError:
        return ["lost", objname, sub]
    t = sh.ShapeType
    if t == "Face":
        meas, c = sh.Area, sh.CenterOfMass
    elif t == "Edge":
        meas, c = sh.Length, sh.CenterOfMass
    elif t == "Vertex":
        meas, c = 0.0, sh.Point
    else:
        return ["lost", objname, sub]
    return [t, _r(meas), [_r(c.x), _r(c.y), _r(c.z)]]


def _face_sig(f):
    """Surface type, area, centroid and the exact (triangulation-free) bounding box of one face."""
    try:
        bb = f.optimalBoundingBox(False, False)
    except Exception:  # noqa: BLE001
        bb = f.BoundBox
    c = f.CenterOfMass
    return [type(f.Surface).__name__, _r(f.Area), [_r(c.x), _r(c.y), _r(c.z)], [_r(v) for v in (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)]]


def body_signature(shape):
    """Volume, bounding box and the sorted signatures of every face of the body: a hole that moves inside a box changes the face list while
    volume and bounding box stay put. The BREP text is not hashed (it carries a flag that changes when the shape is queried)."""
    bb = shape.BoundBox
    faces = sorted(_face_sig(f) for f in shape.Faces)
    return [_r(shape.Volume)] + [_r(v) for v in (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)] + [faces]


def _material(spec):
    try:
        return {k: (round(v, 9) if isinstance(v, float) else v) for k, v in resolve_material(spec["material"]).items()}
    except Refusal:
        return {"raw": json.dumps(spec["material"], sort_keys=True)}


def input_hash(doc, spec, live=None):
    """sha256 of: the hash form, the canonical spec, the body's volume, bounding box and every face (type, area, centroid, box), the signature of every referenced element as it resolves now, the
    resolved material values and the options (the options are part of the spec)."""
    live = live or {}
    body = doc.getObject(spec["body"])
    if body is not None and hasattr(body, "Shape") and not body.Shape.isNull():
        bd = body_signature(body.Shape)
    else:
        bd = None
    refs = []
    for role, items in (("S", spec["supports"]), ("L", spec["loads"])):
        for k, it in enumerate(items):
            for o, s in (live.get((role, k)) or it.get("refs", [])):
                refs.append([role, k, o, s, _sig(doc, o, s)])
    payload = {"form": HASH_FORM, "spec": spec, "body": bd, "refs": refs, "material": _material(spec)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def is_current(h, stored):
    """True when the stored hash is the hash of the model as it is now."""
    return h == stored


def stale_flag(h, stored):
    return h != stored
