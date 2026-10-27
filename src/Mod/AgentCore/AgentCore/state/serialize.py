# SPDX-License-Identifier: LGPL-2.1-or-later
"""Document DAG serialiser with paging (schema agentcore.state/1)."""

import math

import FreeCAD as App

from . import sections
from .values import SKIP_PROPERTIES, VOLATILE_TYPES, property_value, shape_summary

SCHEMA_VERSION = "agentcore.state/1"
DEFAULT_PAGE_SIZE = 50

# Values covered by a detail section; listed with type but not repeated.
DETAIL_PROPERTIES = {
    "sketch": {"Geometry", "Constraints", "ExternalGeo", "ExternalGeometry"},
    "spreadsheet": {"cells"},
}
SHAPE_TYPES = {"Part::PropertyPartShape"}
DOC_META = ("Comment", "Company", "CreatedBy", "License", "LicenseURL", "Meta", "UnitSystem")


def _names(objs):
    out = set()
    for o in objs or []:
        try:
            out.add(o.Name)
        except Exception:
            pass
    return sorted(out)


def serialize_object(obj):
    kind = sections.kind_of(obj)
    # Detail first: Sketcher solve() refreshes FullyConstrained and friends.
    det = sections.detail(obj, kind)
    skip_detail = DETAIL_PROPERTIES.get(kind, set())
    props = []
    shape = None
    for name in sorted(obj.PropertiesList):
        if name in SKIP_PROPERTIES:
            continue
        try:
            ptype = obj.getTypeIdOfProperty(name)
        except Exception:
            ptype = ""
        if ptype in VOLATILE_TYPES:
            continue
        if ptype in SHAPE_TYPES and name == "Shape":
            try:
                shape = shape_summary(obj.Shape)
            except Exception:
                shape = None
            continue
        entry = {"name": name, "type": ptype, "group": obj.getGroupOfProperty(name) or ""}
        if name in skip_detail:
            entry["value"] = {"see": "detail"}
        else:
            entry["value"] = property_value(obj, name, ptype)
        try:
            if obj.getEnumerationsOfProperty(name):
                entry["enum"] = list(obj.getEnumerationsOfProperty(name))
        except Exception:
            pass
        props.append(entry)
    try:
        exprs = [{"path": str(p), "expression": str(e)} for p, e in obj.ExpressionEngine]
    except Exception:
        exprs = []
    proxy = None
    if "Proxy" in obj.PropertiesList:
        try:
            proxy = type(obj.Proxy).__name__ if obj.Proxy is not None else None
        except Exception:
            proxy = None
    return {
        "name": obj.Name,
        "label": obj.Label,
        "type": obj.TypeId,
        "kind": kind,
        "proxy": proxy,
        "state": sorted(str(s) for s in obj.State),
        "in_list": _names(obj.InList),
        "out_list": _names(obj.OutList),
        "properties": props,
        "expressions": sorted(exprs, key=lambda e: e["path"]),
        "shape": shape,
        "detail": det,
    }


def _doc_header(doc):
    meta = {}
    for key in DOC_META:
        try:
            v = getattr(doc, key)
        except Exception:
            continue
        if isinstance(v, (str, int, float)):
            meta[key] = v
    objs = doc.Objects
    return {
        "name": doc.Name,
        "label": doc.Label,
        "object_count": len(objs),
        "roots": _names(o for o in objs if not o.InList),
        "meta": meta,
    }


def dump(doc, page=None, page_size=None):
    """Versioned JSON state of ``doc``.

    ``page`` is 0-based and pages slice ``objects`` in document order; every
    other key is identical on every page, so concatenating ``objects`` across
    pages gives the unpaged read.
    """
    if doc is None:
        doc = App.ActiveDocument
    objs = list(doc.Objects)
    total = len(objs)
    if page is None and page_size is None:
        sel, page_no, size = objs, 0, max(total, 1)
    else:
        size = int(DEFAULT_PAGE_SIZE if page_size is None else page_size)
        if size < 1:
            raise ValueError("page_size must be >= 1")
        page_no = int(page or 0)
        if page_no < 0:
            raise ValueError("page must be >= 0")
        sel = objs[page_no * size:(page_no + 1) * size]
    v = App.Version()
    return {
        "schema": SCHEMA_VERSION,
        "generator": {"freecad": ".".join(v[:3]), "agentcore_state": SCHEMA_VERSION},
        "document": _doc_header(doc),
        "paging": {"page": page_no, "page_size": size, "total": total, "pages": max(1, math.ceil(total / size))},
        "objects": [serialize_object(o) for o in sel],
    }


def dump_all_pages(doc, page_size):
    """Iterate pages and return them as a list (convenience for tests and F4)."""
    first = dump(doc, 0, page_size)
    pages = [first]
    for p in range(1, first["paging"]["pages"]):
        pages.append(dump(doc, p, page_size))
    return pages
