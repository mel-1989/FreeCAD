# SPDX-License-Identifier: LGPL-2.1-or-later
"""Stable entity IDs and semantic entity descriptions.

IDs are built on FreeCAD's element map (``getElementMappedName``), never on a
second naming layer. An ID is ``"<Object>:<element>"`` where ``<element>`` is
the mapped name with its ``;`` prefix (``"Pad:;#14:1;:G0;XTR;:Hf99:8,F"``)
when the owning shape has an element map, and the short name
(``"Box:Face3"``) otherwise. ``resolve_entity`` turns an ID back into the
current short name.
"""

import re

import FreeCAD as App

from .values import fnum, vec

ELEMENT_RE = re.compile(r"(Face|Edge|Vertex)(\d+)$")
TAG_RE = re.compile(r";:H(-?[0-9a-fA-F]+)")
OP_RE = re.compile(r";([A-Z]{3})(?=;|$)")
OP_NAMES = {
    "XTR": "extrude", "CUT": "cut", "FUS": "fuse", "CMN": "common", "FLT": "fillet",
    "CHF": "chamfer", "RVL": "revolve", "LFT": "loft", "SWP": "sweep", "THK": "thickness",
    "MIR": "mirror", "PAT": "pattern", "SKT": "sketch", "REF": "refine", "SLD": "solid",
}
GEOM_NAMES = {
    "Part::GeomPlane": "planar", "Part::GeomCylinder": "cylindrical", "Part::GeomCone": "conical",
    "Part::GeomSphere": "spherical", "Part::GeomToroid": "toroidal", "Part::GeomBSplineSurface": "B-spline",
    "Part::GeomBezierSurface": "Bezier", "Part::GeomSurfaceOfExtrusion": "extruded",
    "Part::GeomSurfaceOfRevolution": "revolved", "Part::GeomOffsetSurface": "offset",
    "Part::GeomLine": "straight", "Part::GeomLineSegment": "straight", "Part::GeomCircle": "circular",
    "Part::GeomArcOfCircle": "circular", "Part::GeomEllipse": "elliptical", "Part::GeomBSplineCurve": "B-spline",
    "Part::GeomBezierCurve": "Bezier",
}


def split_subname(subname):
    """Split ``'Body.Pad.Face3'`` or ``';mapped.Face3'`` into (path, element)."""
    if not subname:
        return "", ""
    if ";" in subname:
        # mapped names may contain dots; the short name follows the last '.'
        head, _, tail = subname.rpartition(".")
        path = head.split(";", 1)[0]
        return path, tail
    head, _, tail = subname.rpartition(".")
    if ELEMENT_RE.match(tail.lstrip("?")):
        return (head + "." if head else ""), tail
    return subname, ""


def _resolve(obj, subname):
    """Return (owner, mapped_without_prefix_or_'', short)."""
    try:
        owner, new, old = obj.resolveSubElement(subname)
    except Exception:
        owner, new, old = None, "", ""
    if owner is None:
        owner = obj
    short = (old or split_subname(subname)[1]).lstrip("?")
    mapped = ""
    if new and new.startswith(";"):
        mapped = new[1:]
        if mapped.endswith("." + short):
            mapped = mapped[: -len(short) - 1]
    if not mapped and short:
        try:
            m = owner.Shape.getElementMappedName(short)
            mapped = m or ""
        except Exception:
            mapped = ""
    return owner, mapped, short


def entity_id(obj, subname=""):
    """Stable ID for an object or one of its sub-elements."""
    if not subname:
        return obj.Name
    owner, mapped, short = _resolve(obj, subname)
    if mapped:
        return f"{owner.Name}:;{mapped}"
    return f"{owner.Name}:{short}"


def resolve_entity(doc, eid):
    """Return (object, short_name or '') for an ID, or (None, '') if gone."""
    name, _, element = eid.partition(":")
    obj = doc.getObject(name)
    if obj is None or not element:
        return obj, ""
    if element.startswith(";"):
        try:
            short = obj.Shape.getElementName(element[1:])
        except Exception:
            short = ""
        if not short or short == element[1:] or not ELEMENT_RE.match(short):
            return obj, ""
        return obj, short
    return obj, element


def _axis_label(v):
    comps = (("X", v.x), ("Y", v.y), ("Z", v.z))
    name, val = max(comps, key=lambda c: abs(c[1]))
    if abs(val) < 0.999:
        return "[%s]" % ", ".join("%.3g" % c for c in (v.x, v.y, v.z))
    return ("+" if val > 0 else "-") + name


def _num(x):
    return ("%.4f" % x).rstrip("0").rstrip(".")


def _object_by_tag(doc, tag):
    for o in doc.Objects:
        try:
            if o.ID == tag:
                return o
        except Exception:
            pass
    return None


def _strip_sketch(obj, src):
    """A sketch as source means "made from this profile": report the feature
    in obj's history that consumes it."""
    if src is None or not (src.isDerivedFrom("Sketcher::SketchObject") or src.isDerivedFrom("Part::Part2DObject")):
        return src
    for f in [obj] + list(obj.OutListRecursive):
        prof = getattr(f, "Profile", None) if "Profile" in f.PropertiesList else None
        pobj = prof[0] if isinstance(prof, tuple) and prof else prof
        if pobj is src:
            return f
    return src


def creator_of(obj, mapped, _depth=0):
    """(feature, operation, how) that last generated or modified the element.

    A mapped name that carries an operation code (``XTR``, ``CUT``, ``FLT``
    ...) was made by the shape's owner; ``;:M;`` marks a modification and
    ``;:G;`` a generated element. A name without an operation code was passed
    through from the feature named by its ``;:H<tag>``; follow it with
    ``getElementHistory`` into that feature's shape.
    """
    if not mapped:
        return None, None, None
    ops = OP_RE.findall(mapped)
    if ops:
        how = "modified" if ";:M" in mapped else "created"
        return obj, OP_NAMES.get(ops[-1], ops[-1].lower()), how
    if _depth < 32:
        try:
            h = obj.Shape.getElementHistory(mapped)
        except Exception:
            h = None
        if h:
            src = _object_by_tag(obj.Document, abs(int(h[0])))
            src = _strip_sketch(obj, src)
            if src is not None and src is not obj and h[1]:
                try:
                    has = bool(src.Shape.getElementName(str(h[1])))
                except Exception:
                    has = False
                if has:
                    return creator_of(src, str(h[1]), _depth + 1)
                return src, None, "created"
    return obj, None, "created"


def _history(shape, mapped):
    try:
        h = shape.getElementHistory(mapped)
    except Exception:
        return None
    if not h:
        return None
    return {"source_tag": int(h[0]), "source_name": str(h[1]), "intermediates": [str(x) for x in h[2]]}


def describe_entity(obj, subname):
    """Type, key dimensions, axis, creator feature and a human-readable label."""
    owner, mapped, short = _resolve(obj, subname)
    out = {"id": entity_id(obj, subname), "object": owner.Name, "element": short, "mapped": mapped or None}
    m = ELEMENT_RE.match(short or "")
    if not m:
        out.update({"element_type": "object", "label": f"{owner.TypeId.split('::')[-1]} {owner.Label}"})
        return out
    etype = m.group(1)
    out["element_type"] = etype.lower()
    try:
        sub = owner.Shape.getElement(short)
    except Exception as e:  # noqa: BLE001
        out.update({"error": str(e)[:200], "label": f"missing {etype.lower()} {short} of {owner.Label}"})
        return out
    dims, words = {}, []
    if etype == "Face":
        geo = sub.Surface.TypeId
        out["geometry"] = geo
        dims["area"] = fnum(sub.Area)
        words.append(f"{GEOM_NAMES.get(geo, geo.split('::')[-1])} face")
        if geo in ("Part::GeomCylinder", "Part::GeomSphere", "Part::GeomCone"):
            r = sub.Surface.Radius
            dims["radius"] = fnum(r)
            dims["diameter"] = fnum(2 * r)
            if geo != "Part::GeomCone":
                words.append(f"diameter {_num(2 * r)} mm")
        if geo in ("Part::GeomCylinder", "Part::GeomCone"):
            ax = sub.Surface.Axis
            out["axis"] = vec(ax)
            words.append(f"axis {_axis_label(ax)}")
        if geo == "Part::GeomPlane":
            try:
                u0, u1, v0, v1 = sub.ParameterRange
                n = sub.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
            except Exception:
                n = sub.Surface.Axis
            out["normal"] = vec(n)
            words.append(f"normal {_axis_label(n)}")
            words.append(f"area {_num(sub.Area)} mm^2")
    elif etype == "Edge":
        geo = sub.Curve.TypeId
        out["geometry"] = geo
        dims["length"] = fnum(sub.Length)
        words.append(f"{GEOM_NAMES.get(geo, geo.split('::')[-1])} edge")
        if geo in ("Part::GeomCircle", "Part::GeomArcOfCircle"):
            r = sub.Curve.Radius
            dims["radius"] = fnum(r)
            dims["diameter"] = fnum(2 * r)
            out["axis"] = vec(sub.Curve.Axis)
            words.append(f"diameter {_num(2 * r)} mm")
            words.append(f"axis {_axis_label(sub.Curve.Axis)}")
        elif geo in ("Part::GeomLine", "Part::GeomLineSegment"):
            d = sub.Vertexes[-1].Point - sub.Vertexes[0].Point
            if d.Length > 1e-9:
                d.normalize()
                out["direction"] = vec(d)
                words.append(f"along {_axis_label(d)}")
            words.append(f"length {_num(sub.Length)} mm")
        else:
            words.append(f"length {_num(sub.Length)} mm")
    else:
        out["geometry"] = "Point"
        words.append("vertex")
        words.append("at (%s)" % ", ".join(_num(c) for c in (sub.Point.x, sub.Point.y, sub.Point.z)))
    try:
        out["center"] = vec(sub.CenterOfMass)
    except Exception:
        out["center"] = vec(sub.Point) if etype == "Vertex" else None
    out["dimensions"] = dims
    creator, op, how = creator_of(owner, mapped)
    if creator is None:
        creator, how = owner, "created"
    out["creator"] = creator.Name if creator is not None else None
    out["creator_label"] = creator.Label if creator is not None else None
    out["operation"] = op
    out["creation"] = how
    out["history"] = _history(owner.Shape, mapped) if mapped else None
    if creator is not None:
        words.append(f"{how or 'created'} by {creator.Label}")
    out["label"] = ", ".join(words)
    return out
