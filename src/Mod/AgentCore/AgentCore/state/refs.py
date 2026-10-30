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

``basis`` says how a verdict was reached (element map by default,
``"freecad"`` when FreeCAD's own reference update supplied the successor,
``"signature"`` for an exact geometric match, ``"element_map_structure"``
for the unique element made by the same operation in the same role, and
``"carrier"`` for the unique element on the same plane, axis and radius, or
line). A successor is accepted only when it is unique; otherwise the
reference is reported lost.

The element map can also keep a name on the wrong element. A kept name whose
geometry moved while an exact copy of the old geometry sits elsewhere is
reported as a remap onto that copy; a kept name whose element now faces
another way (more than a sign flip) is reported lost.

``stale_geometry`` is true when the element's owner or one of its
dependencies failed or was not recomputed. Such a reference is always
reported ``lost`` (the would-be verdict is kept under ``provisional``),
because nothing about stale geometry can be confirmed.

When the FreeCAD build has the C++ journal of element-reference updates
(``Document.getElementReferenceEvents``), the tracker also reads it. A tracked
reference with a matching C++ event gets ``freecad_event`` (the event dict). A
C++ ``remapped`` event makes ``property_updated`` authoritative (FreeCAD itself
rewrote the property), and a C++ ``lost`` event makes ``property_marked_missing``
true. The C++ side cannot see an element map that keeps a name on the wrong
element, so the geometry checks above stay the oracle for that case. C++ events
that match no tracked reference are listed under ``untracked_events`` in the
report. Without the journal everything works as before.

The journal is an incremental log of what FreeCAD's ``_updateElementReference`` rewrote, not a
replayable history. Undo, redo, Body relinking, deleting a feature and user assignments change
properties without an event, so the property is always read for its current value and the events
only say what FreeCAD did. A ``lost`` event means the reference's mapped name is gone; the visible
sub may still show the old index because FreeCAD writes the mapped name back over the "?" mark.
``restored`` also covers a recovery from the missing mark and may carry an unchanged short sub;
a reference can leave and return within one edit, and every step is logged. ``freecad_event`` is
trusted for ``property_updated`` only when it ends on the sub the property has now. The report's
``journal_truncated`` is true when the size cap discarded events newer than the snapshot.
"""

import re

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


_HEAD_RE = re.compile(r"^(#[0-9a-fA-F]+:)[0-9]+")


def mapped_tail(mapped):
    """The structural part of a mapped name: the name with the index of its
    leading string-hasher token wildcarded (``#14:1;:G0;XTR;:H27f:8,F`` ->
    ``#14:*;:G0;XTR;:H27f:8,F``). Two elements with the same structure were
    made by the same operation, from the same hashed source, in the same
    role; only the index of the source sub-element differs."""
    if not mapped:
        return ""
    m = _HEAD_RE.match(mapped)
    return m.group(1) + "*" + mapped[m.end():] if m else ""


def carrier(shape, short):
    """Geometry that survives a resize: the plane a face lies on, the
    cylinder (axis line and radius) or the line an edge lies on. Two elements
    with the same carrier occupy the same place in space."""
    try:
        e = shape.getElement(short)
    except Exception:
        return None
    try:
        if short.startswith("Face"):
            s = e.Surface
            if s.TypeId == "Part::GeomPlane":
                u0, u1, v0, v1 = e.ParameterRange
                n = e.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
                return ("plane", n, n.dot(e.CenterOfMass))
            if s.TypeId in ("Part::GeomCylinder", "Part::GeomCone", "Part::GeomSphere", "Part::GeomToroid"):
                ax = getattr(s, "Axis", App.Vector(0, 0, 1))
                c = s.Center
                # foot of the axis line nearest the origin, so any point on the axis compares equal
                foot = c - ax * ax.dot(c)
                return (s.TypeId, ax, foot, getattr(s, "Radius", 0.0))
            return None
        if short.startswith("Edge"):
            c = e.Curve
            if c.TypeId in ("Part::GeomLine", "Part::GeomLineSegment"):
                d = e.Vertexes[-1].Point - e.Vertexes[0].Point
                if d.Length == 0:
                    return None
                d = d * (1.0 / d.Length)
                p = e.Vertexes[0].Point
                return ("line", d, p - d * d.dot(p))
            if c.TypeId == "Part::GeomCircle":
                return ("circle", c.Axis, c.Center, c.Radius)
    except Exception:
        return None
    return None


def _vec_eq(a, b, tol, sign_free=False):
    if (a - b).Length <= tol:
        return True
    return sign_free and (a + b).Length <= tol


def same_carrier(a, b):
    if a is None or b is None or a[0] != b[0]:
        return False
    tol = POS_TOL * 10
    if a[0] == "plane":
        return _vec_eq(a[1], b[1], 1e-6) and abs(a[2] - b[2]) <= tol * max(1.0, abs(a[2]))
    if a[0] == "line":
        return _vec_eq(a[1], b[1], 1e-6, sign_free=True) and (a[2] - b[2]).Length <= tol * max(1.0, a[2].Length)
    # cylinder-like faces and circles: axis, location, radius
    return (_vec_eq(a[1], b[1], 1e-6, sign_free=True) and (a[2] - b[2]).Length <= tol * max(1.0, a[2].Length)
            and abs(a[3] - b[3]) <= tol * max(1.0, abs(a[3])))


def same_strict(a, b):
    if a is None or b is None or a["loose"] != b["loose"]:
        return False
    if a["size"] is None or b["size"] is None:
        return False
    if abs(a["size"] - b["size"]) > SIZE_TOL * max(1.0, abs(a["size"])):
        return False
    return (a["center"] - b["center"]).Length <= POS_TOL * max(1.0, a["center"].Length)


def _turned(old, new):
    """True when two loose signatures of the same kind differ in orientation
    by more than a sign flip (a +Z face that now faces -Y)."""
    if old[:2] != new[:2] or len(old) < 3 or len(new) < 3:
        return False
    a, b = old[2], new[2]
    if a == b:
        return False
    return any(abs(abs(x) - abs(y)) > 1e-4 for x, y in zip(a, b))


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


def _sub_element(sub):
    """Short element name of a sub-element string (``Pad.Face3``, ``?Face3`` -> ``Face3``)."""
    return split_subname(sub or "")[1].lstrip("?")


class ReferenceTracker:
    """Snapshot element references now; classify them after a recompute."""

    def __init__(self, doc):
        self.doc = doc
        self.records = []
        self.events = []
        self.cpp_events = []
        self.untracked_events = []
        self.journal_seq = None
        self.journal_truncated = False
        self.snapshot()

    # -- snapshot ---------------------------------------------------------
    def snapshot(self):
        self.records = []
        self.cpp_events = []
        self.untracked_events = []
        self.journal_seq = None
        if hasattr(self.doc, "lastElementReferenceEventSeq"):
            self.journal_seq = self.doc.lastElementReferenceEventSeq()
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
                        "element": element, "mapped": mapped, "sub": sub,
                        "sig": signature(shape, element) if shape is not None else None,
                        "carrier": carrier(shape, element) if shape is not None else None,
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

    def _stale(self, obj):
        """True when obj or anything it depends on failed or was not recomputed."""
        if obj is None:
            return False
        for o in [obj] + list(obj.OutListRecursive):
            st = o.State
            if "Invalid" in st or "Touched" in st or "Error" in st:
                return True
        return False

    def _stale_reason(self, obj):
        """Name the object that makes obj's geometry stale, or None."""
        if obj is None:
            return None
        for o in [obj] + list(obj.OutListRecursive):
            st = set(o.State)
            bad = st & {"Invalid", "Error", "Touched"}
            try:
                if not bad and not o.isValid():
                    bad = {"Invalid"}
            except Exception:
                pass
            if bad:
                return "%s is %s" % (o.Name, "/".join(sorted(bad)))
        return None

    def _stale(self, obj):
        return self._stale_reason(obj) is not None

    def _classify(self, rec):
        ev = self._classify_inner(rec)
        why = self._stale_reason(self.doc.getObject(rec["element_owner"]))
        ev["stale_geometry"] = why is not None
        if why is not None and ev["outcome"] != "lost":
            # The owner's shape no longer reflects the edit, so nothing about
            # the reference can be confirmed. Reporting it kept or remapped
            # would overstate stability; report a loss and keep the would-be
            # verdict for diagnosis.
            ev["provisional"] = {"outcome": ev["outcome"], "new_element": ev.get("new_element")}
            ev["outcome"] = "lost"
            ev["reason"] = "stale geometry: %s, so %s was not rebuilt" % (why, rec["element_owner"])
        elif why is not None:
            ev["reason"] = "%s; stale geometry: %s" % (ev.get("reason", "lost"), why)
        return ev

    def _classify_inner(self, rec):
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
                if rec["sig"] is not None and new_sig is not None and rec["sig"]["loose"][:2] != new_sig["loose"][:2]:
                    # Same topological name, different kind of geometry (a
                    # plane became a cylinder): the reference no longer means
                    # what it meant. Report it rather than keep it silently.
                    ev.update(outcome="lost", reason="element changed geometric type: %s -> %s" % (rec["sig"]["loose"][1], new_sig["loose"][1]),
                              new_element=short, retargeted=now_sub is not None)
                    return ev
                if not same_strict(rec["sig"], new_sig):
                    # The element map says "same element", but it moved and
                    # another element now sits exactly where it was. That is
                    # the classic toponaming flip (e.g. a pad's caps trading
                    # names); trust the geometry and report it.
                    twin = self._find_strict(shape, rec, exclude=short)
                    if twin:
                        ev.update(outcome="remapped", new_element=twin, basis="signature", property_updated=(now_sub == twin),
                                  stale=(now_sub != twin), geometry_changed=False,
                                  reason="element map kept the name on moved geometry; the original geometry is now %s" % twin)
                        return ev
                    if rec["sig"] is not None and new_sig is not None and _turned(rec["sig"]["loose"], new_sig["loose"]):
                        # The name survived on an element that now faces a
                        # different way (not merely flipped by a reversal or
                        # mirror): the name was reused by another element.
                        ev.update(outcome="lost", reason="element map name moved to an element with another orientation: %s -> %s"
                                  % (rec["sig"]["loose"][2:], new_sig["loose"][2:]), new_element=short, retargeted=now_sub is not None)
                        return ev
                if short == rec["element"]:
                    ev.update(outcome="kept", new_element=short, geometry_changed=not same_strict(rec["sig"], new_sig))
                else:
                    ev.update(outcome="remapped", new_element=short, property_updated=(now_sub == short),
                              stale=(now_sub != short), geometry_changed=not same_strict(rec["sig"], new_sig))
                if now_sub != short and ev["outcome"] == "kept":
                    ev.update(outcome="remapped", property_updated=False, stale=True)
                return ev
            # The mapped name is gone. FreeCAD's own _updateElementReference
            # may have rewritten the property onto a successor element; report
            # that as a remap (never silently), provided it is still the same
            # kind of element. Otherwise try an exact geometric match.
            if (now_sub and now_sub != rec["element"] and now_sig is not None and rec["sig"] is not None
                    and now_sig["loose"][:2] == rec["sig"]["loose"][:2]):
                ev.update(outcome="remapped", new_element=now_sub, basis="freecad", property_updated=True, stale=False,
                          geometry_changed=not same_strict(rec["sig"], now_sig))
                return ev
            found = self._find_strict(shape, rec)
            if found:
                ev.update(outcome="remapped", new_element=found, basis="signature", property_updated=(now_sub == found),
                          stale=(now_sub != found), geometry_changed=False)
                return ev
            # Same operation and role in the element map (only the source
            # index changed), or the same carrier geometry (plane, axis and
            # radius, line): accept only a unique candidate.
            for finder, basis in ((self._find_structural, "element_map_structure"), (self._find_carrier, "carrier")):
                found = finder(shape, rec)
                if found:
                    ev.update(outcome="remapped", new_element=found, basis=basis, property_updated=(now_sub == found),
                              stale=(now_sub != found), geometry_changed=not same_strict(rec["sig"], signature(shape, found)))
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
        found = self._find_strict(shape, rec) or self._find_carrier(shape, rec) or self._find_unique_loose(shape, rec)
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

    def _find_strict(self, shape, rec, exclude=None):
        if rec["sig"] is None:
            return None
        skip = rec["element"] if exclude is None else exclude
        for c in self._candidates(shape, rec):
            if c != skip and same_strict(rec["sig"], signature(shape, c)):
                return c
        return None

    def _find_structural(self, shape, rec):
        tail = mapped_tail(rec["mapped"])
        if not tail or rec["sig"] is None or shape.ElementMapSize == 0:
            return None
        hits = [c for c in self._candidates(shape, rec)
                if mapped_tail(_mapped(shape, c)) == tail and (signature(shape, c) or {}).get("loose") == rec["sig"]["loose"]]
        return hits[0] if len(hits) == 1 else None

    def _find_carrier(self, shape, rec):
        if rec.get("carrier") is None or rec["sig"] is None:
            return None
        hits = [c for c in self._candidates(shape, rec)
                if (signature(shape, c) or {}).get("loose") == rec["sig"]["loose"] and same_carrier(rec["carrier"], carrier(shape, c))]
        return hits[0] if len(hits) == 1 else None

    def _find_unique_loose(self, shape, rec):
        if rec["sig"] is None:
            return None
        hits = [c for c in self._candidates(shape, rec) if (signature(shape, c) or {}).get("loose") == rec["sig"]["loose"]]
        return hits[0] if len(hits) == 1 else None

    def _read_journal(self):
        """The C++ journal entries since the snapshot, oldest first ([] without a journal)."""
        if self.journal_seq is None or not hasattr(self.doc, "getElementReferenceEvents"):
            return []
        return list(self.doc.getElementReferenceEvents(self.journal_seq))

    def _match_journal(self, cpp_events):
        """Attach the C++ events to the records and return the set of event seqs used.

        The journal has no sub index, so a record is matched by identity of its snapshot sub:
        1. each record claims the earliest unclaimed event of its owner and property whose
           ``oldSub`` is exactly the record's snapshot sub and whose target is the record's
           linked object or the object the property links to now (a G01 insertion moves the
           link to the inserted feature). A second pass accepts the same short element name
           when no exact event exists;
        2. a record then extends its chain with events that no record claimed in step 1,
           each starting where the previous one ended (a reference that moves in several
           recomputes), stopping at a ``lost`` event.
        Because step 1 completes for every record before any chain grows, one sub's event is
        never taken as the next hop of another sub (A: Edge1 to Edge8 while B: Edge8 to Edge5)."""
        used = set()
        by_prop = {}
        for ce in cpp_events:
            by_prop.setdefault((ce["owner"], ce["property"]), []).append(ce)
        now_linked = {}
        for i, rec in enumerate(self.records):
            cur, _why = self._current(rec)
            now_linked[i] = {rec["linked"]} | ({cur[0].Name} if cur is not None and hasattr(cur[0], "Name") else set())
            rec["cpp_chain"] = []

        def candidates(rec, i):
            return [ce for ce in by_prop.get((rec["owner"], rec["property"]), []) if ce["seq"] not in used
                    and ce["target"] in now_linked[i]]

        for exact in (True, False):
            for i, rec in enumerate(self.records):
                if rec["cpp_chain"]:
                    continue
                for ce in candidates(rec, i):
                    hit = ce["oldSub"] == rec["sub"] if exact else _sub_element(ce["oldSub"]) == rec["element"]
                    if hit:
                        rec["cpp_chain"].append(ce)
                        used.add(ce["seq"])
                        break
        for i, rec in enumerate(self.records):
            chain = rec["cpp_chain"]
            while chain and chain[-1]["kind"] != "lost":
                cur = _sub_element(chain[-1]["newSub"])
                nxt = [ce for ce in candidates(rec, i) if ce["seq"] > chain[-1]["seq"] and _sub_element(ce["oldSub"]) == cur]
                if not nxt:
                    break
                chain.append(nxt[0])
                used.add(nxt[0]["seq"])
        return used

    def _apply_journal(self, ev, rec):
        chain = rec.get("cpp_chain") or []
        if not chain:
            return
        last = chain[-1]
        ev["freecad_event"] = dict(last)
        if len(chain) > 1:
            ev["freecad_events"] = [dict(c) for c in chain]
        if last["kind"] == "lost":
            ev["property_marked_missing"] = True
        elif ev["outcome"] == "remapped" and last["kind"] in ("remapped", "restored"):
            # FreeCAD rewrote the property itself, provided the event ends where the property
            # stands now; an event that ends elsewhere is attached but not trusted
            if _sub_element(last["newSub"]) == ev.get("now_points_to") == ev.get("new_element"):
                ev["property_updated"] = True
                ev["stale"] = False

    def classify(self):
        cpp_events = self._read_journal()
        self.journal_truncated = bool(
            self.journal_seq is not None and hasattr(self.doc, "lastDroppedElementReferenceEventSeq")
            and self.doc.lastDroppedElementReferenceEventSeq() > self.journal_seq)
        self.cpp_events = cpp_events
        used = self._match_journal(cpp_events)
        self.untracked_events = [dict(c) for c in cpp_events if c["seq"] not in used]
        self.events = []
        for r in self.records:
            ev = self._classify(r)
            self._apply_journal(ev, r)
            self.events.append(ev)
        return self.events

    def report(self):
        if not self.events and self.records:
            self.classify()
        counts = {"kept": 0, "remapped": 0, "lost": 0}
        for e in self.events:
            counts[e["outcome"]] += 1
        out = {"references": len(self.records), "counts": counts, "events": self.events}
        if self.journal_seq is not None:
            out["untracked_events"] = self.untracked_events
            out["journal_truncated"] = self.journal_truncated
        return out

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
