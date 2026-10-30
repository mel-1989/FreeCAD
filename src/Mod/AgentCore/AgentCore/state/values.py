# SPDX-License-Identifier: LGPL-2.1-or-later
"""Convert FreeCAD property values to deterministic JSON values.

Floats are rounded to 1e-6 and -0.0 is normalised, so dumps are reproducible.
Unknown values become ``{"unserialised": "<python type>"}``; nothing here
ever emits a ``repr`` with a memory address.
"""

import functools
import hashlib
import math
import os

import FreeCAD as App

FLOAT_DIGITS = 6
MAX_STRING = 4096

# Properties whose value is volatile (random, path- or clock-dependent) or
# covered by a dedicated section of the dump.
VOLATILE_TYPES = {"App::PropertyUUID"}
SKIP_PROPERTIES = {"ExpressionEngine", "Proxy"}


def fnum(x):
    """Round a float for output; ints and bools pass through."""
    if isinstance(x, bool) or isinstance(x, int):
        return x
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return str(x)
    r = round(x, FLOAT_DIGITS)
    return 0.0 if r == 0 else r


def vec(v):
    return [fnum(v.x), fnum(v.y), fnum(v.z)]


def quantity(q):
    unit = ""
    try:
        unit = q.Unit.Type
    except Exception:
        pass
    return {"value": fnum(q.Value), "unit": unit}


def placement(p):
    q = p.Rotation.Q
    return {"base": vec(p.Base), "rotation": [fnum(c) for c in q]}


# Names of objects removed inside an agent turn, by id() of their Python object: a removed
# object has no Name any more, but old property values (transaction records) still link to it.
_REMOVED_NAMES = {}


def _obj_name(o):
    try:
        name = o.Name
    except Exception:
        name = None
    return name if name is not None else _REMOVED_NAMES.get(id(o))


def link_value(v):
    """Normalise any App link value to JSON."""
    if v is None:
        return None
    try:
        import FreeCAD

        if isinstance(v, FreeCAD.DocumentObject):
            return {"object": _obj_name(v)}
    except Exception:
        pass
    if isinstance(v, tuple) and len(v) == 2 and hasattr(v[0], "Name"):
        subs = v[1]
        if isinstance(subs, str):
            subs = [subs]
        return {"object": _obj_name(v[0]), "subs": [str(s) for s in subs]}
    if isinstance(v, (list, tuple)):
        return [link_value(x) for x in v]
    return to_json(v)


def shape_summary(shape):
    if shape is None:
        return None
    try:
        if shape.isNull():
            return {"null": True}
    except Exception:
        return None
    out = {"null": False, "shape_type": shape.ShapeType}
    for key, attr in (("solids", "Solids"), ("shells", "Shells"), ("faces", "Faces"), ("edges", "Edges"), ("vertexes", "Vertexes")):
        try:
            out[key] = len(getattr(shape, attr))
        except Exception:
            out[key] = 0
    for key, attr in (("volume", "Volume"), ("area", "Area")):
        try:
            out[key] = fnum(getattr(shape, attr))
        except Exception:
            out[key] = None
    try:
        bb = shape.BoundBox
        out["bbox"] = [fnum(c) for c in (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)] if bb.isValid() else None
    except Exception:
        out["bbox"] = None
    try:
        out["element_map_size"] = int(shape.ElementMapSize)
    except Exception:
        out["element_map_size"] = 0
    try:
        out["valid"] = bool(shape.isValid())
    except Exception:
        out["valid"] = None
    return out


def _path_roots():
    docdir = ""
    try:
        doc = App.ActiveDocument
        if doc is not None and doc.FileName:
            docdir = os.path.dirname(doc.FileName)
    except Exception:
        pass
    return _path_roots_for(docdir)


@functools.lru_cache(maxsize=32)
def _path_roots_for(docdir):
    """(prefix, token) pairs for machine-specific directories, longest first."""
    roots = []

    def add(path, token):
        if not path:
            return
        for p in {os.path.normpath(path), os.path.realpath(path)}:
            roots.append((p.rstrip("/") + "/", token))

    for getter, token in (("getResourceDir", "$RESOURCE"), ("getUserAppDataDir", "$USERAPPDATA"),
                          ("getHomePath", "$INSTALL"), ("getUserMacroDir", "$USERMACRO")):
        try:
            add(getattr(App, getter)(), token)
        except Exception:
            pass
    add(docdir, "$DOCDIR")
    add(os.path.expanduser("~"), "$HOME")
    roots.sort(key=lambda r: -len(r[0]))
    return tuple(roots)


def normalize_path(s):
    """Replace a machine-specific directory prefix by a stable token, so a
    dump (and a golden made from it) does not depend on the build or the
    user: ``<resource dir>/Mod/TechDraw/x.svg`` -> ``$RESOURCE/Mod/TechDraw/x.svg``."""
    if not isinstance(s, str) or not s.startswith("/") and not (len(s) > 2 and s[1] == ":"):
        return s
    cands = {s, os.path.normpath(s)}
    try:
        cands.add(os.path.realpath(s))
    except Exception:
        pass
    for prefix, token in _path_roots():
        for c in cands:
            if c.startswith(prefix):
                return token + "/" + c[len(prefix):]
    return s


def _string(s):
    s = normalize_path(s)
    tdir = ""
    try:
        tdir = App.ActiveDocument.TransientDir if App.ActiveDocument else ""
    except Exception:
        pass
    if tdir and tdir in s:
        s = s.replace(tdir, "<transient>")
    if len(s) > MAX_STRING:
        return {"truncated": True, "length": len(s), "sha1": hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()}
    return s


def to_json(v, depth=0):
    """Best-effort conversion of an arbitrary property value."""
    if depth > 6:
        return {"unserialised": "depth"}
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return fnum(v)
    if isinstance(v, str):
        return _string(v)
    if isinstance(v, bytes):
        return {"bytes": len(v)}
    if isinstance(v, App.Vector):
        return vec(v)
    if isinstance(v, App.Placement):
        return placement(v)
    if isinstance(v, App.Rotation):
        return [fnum(c) for c in v.Q]
    if isinstance(v, App.Matrix):
        return [fnum(c) for c in v.A]
    if isinstance(v, App.Units.Quantity):
        return quantity(v)
    if isinstance(v, App.DocumentObject):
        return {"object": _obj_name(v)}
    try:
        import Part

        if isinstance(v, Part.Shape):
            return shape_summary(v)
    except Exception:
        pass
    if isinstance(v, (list, tuple)):
        if len(v) == 2 and isinstance(v[0], App.DocumentObject):
            return link_value(v)
        if len(v) > 2000:
            return {"truncated": True, "length": len(v)}
        return [to_json(x, depth + 1) for x in v]
    if isinstance(v, dict):
        return {str(k): to_json(x, depth + 1) for k, x in sorted(v.items(), key=lambda kv: str(kv[0]))}
    if hasattr(v, "x") and hasattr(v, "y") and hasattr(v, "z"):
        try:
            return [fnum(v.x), fnum(v.y), fnum(v.z)]
        except Exception:
            pass
    return {"unserialised": type(v).__name__}


def value_json(v, ptype, removed_names=None):
    """JSON value of an already fetched property value, never raising.

    ``removed_names`` maps ``id()`` of the Python object of a removed document object to the name
    it had, so links to it still serialise by name.
    """
    global _REMOVED_NAMES
    saved, _REMOVED_NAMES = _REMOVED_NAMES, removed_names or {}
    try:
        return _value_json(v, ptype)
    finally:
        _REMOVED_NAMES = saved


def _value_json(v, ptype):
    try:
        if "PropertyLink" in ptype:
            return link_value(v)
        if ptype == "App::PropertyFileIncluded" and isinstance(v, str):
            return os.path.basename(v)
        if ptype in ("App::PropertyFile", "App::PropertyPath") and isinstance(v, str):
            return normalize_path(v)
        return to_json(v)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:200]}


def property_value(obj, name, ptype):
    """JSON value of one property, never raising."""
    try:
        v = obj.getPropertyByName(name)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:200]}
    return value_json(v, ptype)
