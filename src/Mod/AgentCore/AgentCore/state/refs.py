# SPDX-License-Identifier: LGPL-2.1-or-later
"""Reference remap and lost-reference reporting across a recompute.

Python stand-in for a C++ hook in ``App/PropertyLinks``: before a recompute
the tracker snapshots every element reference held by a ``PropertyLinkSub``
style property (owner, property, linked object, short name, mapped name and a
geometric signature of the element). After the recompute ``classify()`` gives
one event per reference:

- ``kept``: the reference still names the same element (same mapped name, or
  for shapes without an element map the same short name with a matching
  signature). ``geometry_changed`` says whether its size or position moved.
- ``remapped``: the element survives under a different short name
  (``old -> new``). ``property_updated`` says whether FreeCAD already rewrote
  the property; if not, the property now points somewhere else and
  ``stale`` is true.
- ``lost``: the element no longer exists. ``now_points_to`` reports what the
  property resolves to now, so a silent retarget is visible.
"""

import FreeCAD as App

from .entity import ELEMENT_RE, split_subname
from .values import fnum

LINK_SUB_TYPES = {
    "App::PropertyLinkSub", "App::PropertyLinkSubChild", "App::PropertyLinkSubGlobal", "App::PropertyLinkSubHidden",
    "App::PropertyLinkSubList", "App::PropertyLinkSubListChild", "App::PropertyLinkSubListGlobal",
    "App::PropertyLinkSubListHidden", "App::PropertyXLinkSub", "App::PropertyXLinkSubList",
    "App::PropertyXLinkSubHidden", "App::PropertyXLink", "App::PropertyXLinkList",
}
POS_TOL = 1e-4
SIZE_TOL = 1e-4


def _pairs(value):
    """Flatten a link-sub value into [(object, sub), ...]."""
    out = []
    if value is None:
        return out
    if isinstance(value, tuple) and len(value) == 2 and hasattr(value[0], "Name"):
        subs = value[1]
        if isinstance(subs, str):
            subs = [subs]
        for s in subs or [""]:
            out.append((value[0], str(s)))
        return out
    if isinstance(value, (list, tuple)):
        for v in value:
            out.extend(_pairs(v))
    return out


def signature(shape, short):
    """Loose and strict geometric signature of one sub-element, or None."""
    try:
        e = shape.getElement(short)
    except Exception:
        return None
    m = ELEMENT_RE.match(short)
    etype = m.group(1)
    loose = [etype]
    strict = {}
    try:
        if etype == "Face":
            s = e.Surface
            loose.append(s.TypeId)
            if s.TypeId == "Part::GeomPlane":
                u0, u1, v0, v1 = e.ParameterRange
                n = e.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
                loose.append(tuple(round(c, 4) for c in n))
            elif hasattr(s, "Axis"):
                a = s.Axis
                loose.append(tuple(round(abs(c), 4) for c in a))
            strict["size"] = e.Area
            strict["center"] = e.CenterOfMass
        elif etype == "Edge":
            c = e.Curve
            loose.append(c.TypeId)
            if hasattr(c, "Direction"):
                d = c.Direction
                loose.append(tuple(round(abs(x), 4) for x in d))
            elif hasattr(c, "Axis"):
                loose.append(tuple(round(abs(x), 4) for x in c.Axis))
            strict["size"] = e.Length
            strict["center"] = e.CenterOfMass
        else:
            strict["size"] = 0.0
            strict["center"] = e.Point
    except Exception:
        return {"loose": tuple(loose), "size": None, "center": None}
    return {"loose": tuple(loose), "size": strict.get("size"), "center": strict.get("center")}


def same_strict(a, b):
    if a is None or b is None or a["loose"] != b["loose"]:
        return False
    if a["size"] is None or b["size"] is None:
        return False
    if abs(a["size"] - b["size"]) > SIZE_TOL * max(1.0, abs(a["size"])):
        return False
    return (a["center"] - b["center"]).Length <= POS_TOL * max(1.0, a["center"].Length)


def _sig_json(sig):
    if sig is None:
        return None
    c = sig["center"]
    return {"kind": list(map(str, sig["loose"][:2])), "size": fnum(sig["size"]) if sig["size"] is not None else None,
            "center": [fnum(c.x), fnum(c.y), fnum(c.z)] if c is not None else None}


def _element_owner(linked, sub):
    """Return (owner object, short element name, sub path) for one reference."""
    path, element = split_subname(sub)
    element = element.lstrip("?")
    owner = linked
    if path:
        try:
            owner = linked.getSubObject(path, retType=1) or linked
        except Exception:
            owner = linked
    return owner, element, path


def _shape(obj):
    try:
        s = obj.Shape
        return None if s.isNull() else s
    except Exception:
        return None


def _mapped(shape, short):
    try:
        m = shape.getElementMappedName(short)
        return m or ""
    except Exception:
        return ""


def _short_for(shape, mapped):
    try:
        s = shape.getElementName(mapped)
    except Exception:
        return ""
    if not s or s == mapped or not ELEMENT_RE.match(s):
        return ""
    return s


class ReferenceTracker:
    """Snapshot element references now; classify them after a recompute."""

    def __init__(self, doc):
        self.doc = doc
        self.records = []
        self.events = []
        self.snapshot()

    # -- snapshot ---------------------------------------------------------
    def snapshot(self):
        self.records = []
        for obj in self.doc.Objects:
            for prop in obj.PropertiesList:
                try:
                    ptype = obj.getTypeIdOfProperty(prop)
                except Exception:
                    continue
                if ptype not in LINK_SUB_TYPES:
                    continue
                try:
                    pairs = _pairs(obj.getPropertyByName(prop))
                except Exception:
                    continue
                for index, (linked, sub) in enumerate(pairs):
                    owner, element, path = _element_owner(linked, sub)
                    if not ELEMENT_RE.match(element):
                        continue
                    shape = _shape(owner)
                    mapped = _mapped(shape, element) if shape is not None else ""
                    self.records.append({
                        "owner": obj.Name, "property": prop, "index": index,
                        "linked": linked.Name, "path": path, "element_owner": owner.Name,
                        "element": element, "mapped": mapped,
                        "sig": signature(shape, element) if shape is not None else None,
                        "valid_before": not sub.startswith("?") and shape is not None and signature(shape, element) is not None,
                    })
        return self

    # -- classify ---------------------------------------------------------
    def _current(self, rec):
        obj = self.doc.getObject(rec["owner"])
        if obj is None or rec["property"] not in obj.PropertiesList:
            return None, "owner or property removed"
        try:
            pairs = _pairs(obj.getPropertyByName(rec["property"]))
        except Exception as e:  # noqa: BLE001
            return None, str(e)[:200]
        if rec["index"] < len(pairs):
            return pairs[rec["index"]], None
        return None, "reference removed from property"

    def _classify(self, rec):
        ev = {k: rec[k] for k in ("owner", "property", "index", "linked", "element_owner", "element")}
        ev["mapped"] = rec["mapped"] or None
        ev["before"] = _sig_json(rec["sig"])
        cur, why = self._current(rec)
        owner = self.doc.getObject(rec["element_owner"])
        shape = _shape(owner) if owner is not None else None
        now_sub = None
        now_sig = None
        if cur is not None:
            linked, sub = cur
            now_owner, now_el, _ = _element_owner(linked, sub)
            now_sub = sub.rpartition(".")[2] if "." in sub else sub
            ev["property_value"] = sub
            ns = _shape(now_owner)
            if ns is not None and not sub.rpartition(".")[2].startswith("?") and ELEMENT_RE.match(now_el):
                now_sig = signature(ns, now_el)
            now_sub = now_el if now_sig is not None else None
        ev["now_points_to"] = now_sub
        ev["after"] = _sig_json(now_sig)
        if shape is None:
            ev.update(outcome="lost", reason=why or "referenced shape missing or null")
            return ev
        if rec["mapped"] and shape.ElementMapSize > 0:
            short = _short_for(shape, rec["mapped"])
            if short:
                new_sig = signature(shape, short)
                if short == rec["element"]:
                    ev.update(outcome="kept", new_element=short, geometry_changed=not same_strict(rec["sig"], new_sig))
                else:
                    ev.update(outcome="remapped", new_element=short, property_updated=(now_sub == short),
                              stale=(now_sub != short), geometry_changed=not same_strict(rec["sig"], new_sig))
                if now_sub != short and ev["outcome"] == "kept":
                    ev.update(outcome="remapped", property_updated=False, stale=True)
                return ev
            ev.update(outcome="lost", reason="mapped element name no longer in the element map",
                      retargeted=now_sub is not None)
            return ev
        # No element map: fall back to geometry.
        cur_sig = signature(shape, rec["element"]) if ELEMENT_RE.match(rec["element"]) else None
        if cur_sig is not None and rec["sig"] is not None and cur_sig["loose"] == rec["sig"]["loose"]:
            exact = same_strict(rec["sig"], cur_sig)
            ambiguous = False
            if not exact:
                # Same kind of element but moved: accept only if no other
                # element is a strict match for the old one.
                ambiguous = self._find_strict(shape, rec) is not None
            if not ambiguous:
                ev.update(outcome="kept", new_element=rec["element"], geometry_changed=not exact, basis="signature")
                if now_sub != rec["element"]:
                    ev.update(outcome="remapped", property_updated=False, stale=True)
                return ev
        found = self._find_strict(shape, rec) or self._find_unique_loose(shape, rec)
        if found:
            ev.update(outcome="remapped", new_element=found, basis="signature", property_updated=(now_sub == found),
                      stale=(now_sub != found), geometry_changed=not same_strict(rec["sig"], signature(shape, found)))
            return ev
        ev.update(outcome="lost", reason=why or "no element with a matching signature", basis="signature",
                  retargeted=now_sub is not None)
        return ev

    @staticmethod
    def _candidates(shape, rec):
        etype = ELEMENT_RE.match(rec["element"]).group(1)
        n = {"Face": len(shape.Faces), "Edge": len(shape.Edges), "Vertex": len(shape.Vertexes)}[etype]
        return [f"{etype}{i}" for i in range(1, n + 1)]

    def _find_strict(self, shape, rec):
        if rec["sig"] is None:
            return None
        for c in self._candidates(shape, rec):
            if c != rec["element"] and same_strict(rec["sig"], signature(shape, c)):
                return c
        return None

    def _find_unique_loose(self, shape, rec):
        if rec["sig"] is None:
            return None
        hits = [c for c in self._candidates(shape, rec) if (signature(shape, c) or {}).get("loose") == rec["sig"]["loose"]]
        return hits[0] if len(hits) == 1 else None

    def classify(self):
        self.events = [self._classify(r) for r in self.records]
        return self.events

    def report(self):
        if not self.events and self.records:
            self.classify()
        counts = {"kept": 0, "remapped": 0, "lost": 0}
        for e in self.events:
            counts[e["outcome"]] += 1
        return {"references": len(self.records), "counts": counts, "events": self.events}

    @property
    def remapped(self):
        return [e for e in self.events if e["outcome"] == "remapped"]

    @property
    def lost(self):
        return [e for e in self.events if e["outcome"] == "lost"]

    @property
    def kept(self):
        return [e for e in self.events if e["outcome"] == "kept"]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if exc[0] is None:
            self.classify()
        return False


def track_references(doc):
    """Snapshot every element reference in ``doc``; call ``classify()`` (or
    leave the ``with`` block) after the edit and recompute."""
    return ReferenceTracker(doc if doc is not None else App.ActiveDocument)
