# SPDX-License-Identifier: LGPL-2.1-or-later
"""Canonical F1 dumps and their structural comparison.

Reject equality is judged on ``AgentCore.state.dump`` (F1): every object,
property value, expression, link list, state flag and shape summary. Three
things are left out, all deliberately:

- ``generator`` (versions of the tools that wrote the dump);
- ``TRANSIENT_CACHES``: hidden output properties with ``Prop_Transient``
  status that FreeCAD fills lazily and never writes to the FCStd.
  ``GeoFeature::_ElementMapVersion`` is empty on a freshly
  opened file and set the first time a shape changes outside a restore or a
  transaction, and undo does not clear it again (it is not part of the
  persistent document state at all); ``Part::PropertyShapeCache``
  ``_Part_ShapeCache`` (App::Link's cache of resolved sub-shapes, filled on
  demand and emptied whenever the link's inputs are rewritten);
- the ``volume`` of a shape with no solids. OCCT's volume of a face or a
  datum plane is numerical noise (values like 1e167 that change on every
  rebuild); F1 keeps it, comparison does not.
"""

import json
import math

from .. import state

TRANSIENT_CACHES = frozenset({"_ElementMapVersion", "_Part_ShapeCache"})
IGNORE_TOP = ("generator",)


def canonical(x):
    """Sort records keyed by ``name`` so order never decides equality."""
    if isinstance(x, dict):
        return {k: canonical(v) for k, v in sorted(x.items())}
    if isinstance(x, list):
        items = [canonical(v) for v in x]
        if items and all(isinstance(v, dict) and "name" in v for v in items):
            return sorted(items, key=lambda v: str(v["name"]))
        return items
    return x


def _strip(d):
    d = {k: v for k, v in d.items() if k not in IGNORE_TOP}
    objs = []
    for o in d.get("objects", []):
        o = dict(o)
        o["properties"] = [p for p in o.get("properties", []) if p.get("name") not in TRANSIENT_CACHES]
        sh = o.get("shape")
        if isinstance(sh, dict) and not sh.get("solids") and "volume" in sh:
            o["shape"] = {k: v for k, v in sh.items() if k != "volume"}
        objs.append(o)
    d["objects"] = objs
    return d


def snapshot_dump(doc):
    """Canonical, comparison-ready F1 dump of ``doc``."""
    return canonical(_strip(state.dump(doc)))


def differences(a, b, path="", out=None, limit=50, rel_tol=1e-9, abs_tol=1e-9):
    """List of human-readable differences between two canonical dumps.

    Numbers compare with ``math.isclose`` at ``rel_tol``/``abs_tol`` (1e-9 by
    default, F1's tolerance); pass 0 for both to compare exactly.
    """
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    num = (int, float)
    if isinstance(a, num) and isinstance(b, num) and not isinstance(a, bool) and not isinstance(b, bool):
        if not math.isclose(a, b, rel_tol=rel_tol, abs_tol=abs_tol):
            out.append(f"{path}: {a!r} != {b!r}")
        return out
    if type(a) is not type(b):
        out.append(f"{path}: {a!r} != {b!r}")
        return out
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a or k not in b:
                out.append(f"{path}/{k}: only in {'after' if k in b else 'before'}")
            else:
                differences(a[k], b[k], f"{path}/{k}", out, limit, rel_tol, abs_tol)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} != {len(b)}")
        else:
            for i, (u, w) in enumerate(zip(a, b)):
                differences(u, w, f"{path}/{i}", out, limit, rel_tol, abs_tol)
    elif a != b:
        out.append(f"{path}: {a!r} != {b!r}")
    return out


def _unzip(raw):
    """Members of a ``dumpPropertyContent`` zip, without the zip header.

    The header carries the time the content was dumped, so two dumps of the
    same value differ in bytes 10 to 13; the members themselves are compared.
    """
    import io
    import zipfile
    try:
        z = zipfile.ZipFile(io.BytesIO(bytes(raw)))
        return tuple((n, z.read(n)) for n in sorted(z.namelist()))
    except Exception:
        return bytes(raw)


def raw_state(doc):
    """``{object: {property: serialised content}}`` plus expressions.

    Byte-level twin of ``snapshot_dump`` for exactness checks: every persistent
    property as FreeCAD serialises it (``transplant.capture``). Shape
    properties are left out: their serialisation embeds element-map hasher
    indices that differ between two recomputes of the same model, so shapes
    are judged by F1's shape summary in ``snapshot_dump`` instead.
    """
    from . import transplant
    return raw_state_of_capture(transplant.capture(doc))


def raw_state_of_capture(cap):
    """``raw_state`` of an existing ``transplant.capture`` result."""
    out = {}
    for o in cap:
        props = {p: _unzip(v["raw"]) for p, v in o["props"].items()
                 if p not in TRANSIENT_CACHES and "PropertyPartShape" not in v.get("type", "")}
        props["@expressions"] = tuple(sorted(o["expressions"].items()))
        out[o["name"]] = props
    return out


def raw_differences(a, b, limit=50):
    """``object.property`` names whose serialised content differs."""
    out = []
    for n in sorted(set(a) | set(b)):
        if n not in a or n not in b:
            out.append(f"{n}: only in {'after' if n in b else 'before'}")
            continue
        for p in sorted(set(a[n]) | set(b[n])):
            if a[n].get(p) != b[n].get(p):
                out.append(f"{n}.{p}")
        if len(out) >= limit:
            break
    return out[:limit]


def digest(obj):
    """Stable sha256 of a JSON-able object."""
    import hashlib
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()
