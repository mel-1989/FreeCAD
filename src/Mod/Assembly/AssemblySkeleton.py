# SPDX-License-Identifier: LGPL-2.1-or-later
"""Skeleton publish and subscribe (G06 slice 3a): a JSON-returning Python API.

A *skeleton* is a part (a PartDesign Body or an ``App::Part``, in the assembly's document or in a document of its
own) that carries a *publication*: a named list of *published items*, each one piece of its geometry (a sketch, a
datum plane, line or point, or a face, edge or vertex of a feature). A *subscriber* is a binder inside another part
that names a published item and keeps a copy of its geometry for that part's features to use.

Published item. An ``Part::FeaturePython`` object in the skeleton's document with a ``PropertyXLinkSub`` ``Source``
(the skeleton element or the whole sketch or datum), the item's name and publication, and a ``Status``. Its
``execute`` resolves the element through the link, so the element-map rescue of G01 (shadow names) applies, and it
refuses to produce a shape when the element cannot be resolved (the sub-name came back empty, no longer names an
element of the kind published, or does not resolve), when the source object is gone, or when the item was withdrawn
from the publication: the object goes into error, keeps its last shape and ``Status`` says why. It never rebinds
to a different element. Renaming an item changes only its ``ItemName`` and label.

Staleness. A subscriber is ``following`` only when the item, its source and everything upstream of the source (a Body is
judged by its Tip chain) are recomputed and not in error and the item's geometry is the geometry the subscriber last
followed; if only the recompute is missing it is ``stale``, and a failed item touches everything downstream of it so
that features built on a broken subscriber do not stay Up-to-date. A document observer re-attaches proxies that a
re-save by stable FreeCAD dropped, before any recompute.

Subscription. A ``PartDesign::SubShapeBinderPython`` whose ``Support`` is the whole item object (never a raw element
of the skeleton, never a name string) and whose Python proxy decides, at every recompute, what the stock binder
may do. Five states (following, stale, frozen, broken, unloaded), always computed from the live objects (``classify``) and never stored:

- following: the item resolves; the stock binder (BindMode Synchronized) copies the item's shape at every recompute.
- frozen: stock BindMode Frozen; the binder keeps the shape it last had and ``status`` says whether the item has
  moved on since (``skeleton_changed``, by a digest of the item's shape recorded at the last following recompute).
- broken: the item was withdrawn, deleted or can no longer be resolved. The proxy raises, so the binder goes into
  error, keeps its last shape and every feature built on it is not recomputed on other geometry; the reason names
  the item and the cause.
- unloaded: the item's document is not open. The proxy leaves the binder alone (its last shape stays); the state
  becomes ``following`` again when the document is opened and the link re-attaches.

Every mutating call is one undo transaction. Cross-document links are the stock external links (stored relative to
the subscriber's file); the subscriber's document must have been saved before it can hold one.

Frames. The geometry of an item is expressed as the stock binder expresses it: an item is a copy of the element in the
frame of the shape it was taken from (a Body's Shape includes the Body's Placement), and a subscriber's binder keeps that
geometry at the skeleton's position in space whatever the placement of the part that holds it. For a skeleton that is an
``App::Part`` with a Placement, the items are in the frame of the Body inside it, not in the Part's.

Save As. A cross-document link is stored by FreeCAD relative to the subscriber's file. Saving either document under a
different directory (or the subscriber under a new name) leaves the stored link pointing at the old place; this is the core's
external-link behaviour and is not repaired here. The skeleton's document is recognised by its Uid, which Save As keeps, so
a subscriber is still reported as ``unloaded`` or ``broken`` for the right reason, but it re-attaches only when the pair is
opened from the files the link names.

Limits (slice 3a): items live at the top of the skeleton's document, not inside a Body; a withdrawn item stays in
the document, hidden from the publication, so that subscribers can name it and so that undo is exact; the
element-level rescue is the one the core gives the link (no extra geometric matching is added here); a subscriber
reached through ``App::Link`` or a sub-assembly sees whatever the stock binder gives it there.

Behaviour sources, all public: FreeCAD's documentation of ``PartDesign::SubShapeBinder``, external links
(wiki.freecad.org), the ``App::FeaturePython`` and document transaction API.
"""

import contextlib
import hashlib
import json
import os
import re

import FreeCAD as App

SCHEMA = "cadfork.assembly.skeleton/1"
PROP_GROUP = "Skeleton"
DEFAULT_PUBLICATION = "Default"

FOLLOWING, STALE, FROZEN, BROKEN, UNLOADED = "following", "stale", "frozen", "broken", "unloaded"
_KINDS = {"Face": "face", "Edge": "edge", "Vertex": "vertex"}


class SkeletonError(Exception):
    """A publish or subscribe call cannot be made (unknown target, duplicate name, unsaved document, ...)."""


@contextlib.contextmanager
def _transaction(doc, title):
    """One undo transaction around a mutating call; aborted if the call fails."""
    doc.openTransaction(title)
    try:
        yield
    except Exception:
        doc.abortTransaction()
        raise
    doc.commitTransaction()


def _entity(obj, sub=""):
    import ReplaceComponent as _rc
    return _rc._entity(obj, sub)


# ------------------------------------------------------------------ shape helpers

def _r(x):
    return round(x, 9) + 0.0


def _vec(v):
    return (_r(v.x), _r(v.y), _r(v.z))


def _face_key(f):
    u0, u1, v0, v1 = f.ParameterRange
    try:
        n = _vec(f.normalAt((u0 + u1) / 2, (v0 + v1) / 2))
    except Exception:
        n = ()
    return (_r(f.Area), _vec(f.CenterOfMass), n, len(f.Edges))


def shape_digest(shape):
    """sha1 of a strict geometric key of a shape: type, counts, bounding box, every vertex, and for every face its area,
    centre, a normal and edge count (else every edge's length and centre), all rounded to 1e-9 mm; an unbounded datum by
    its plane or line. It cannot alias a turned plate (same area and centroid) or a 4e-7 length change; a reload from a
    file or a recompute that rebuilds the same geometry gives the same key (the BRep text does not). None for a null shape."""
    if shape is None or shape.isNull():
        return None
    key = [shape.ShapeType, len(shape.Faces), len(shape.Edges), len(shape.Vertexes)]
    try:
        bb = shape.BoundBox
        if bb.DiagonalLength > 1e50:
            if shape.Faces:
                s = shape.Faces[0].Surface
                key += [_vec(s.Axis), _r(s.Axis.dot(s.Position))]
            elif shape.Edges:
                c = shape.Edges[0].Curve
                key += [_vec(c.Direction), _r(c.Location.dot(c.Direction.cross(App.Vector(1, 0, 0)))),
                        _r(c.Location.dot(c.Direction.cross(App.Vector(0, 1, 0))))]
        else:
            key += [_r(bb.XMin), _r(bb.YMin), _r(bb.ZMin), _r(bb.XMax), _r(bb.YMax), _r(bb.ZMax)]
            key += sorted((_r(v.X), _r(v.Y), _r(v.Z)) for v in shape.Vertexes)
            if shape.Faces:
                key += sorted(_face_key(f) for f in shape.Faces)
            elif shape.Edges:
                key += sorted((_r(e.Length), _vec(e.CenterOfMass)) for e in shape.Edges)
    except Exception:
        key.append(repr(shape.BoundBox))
    return hashlib.sha1(repr(key).encode("utf-8")).hexdigest()


def _bad(obj):
    return "Invalid" in obj.State or "Error" in obj.State


# ------------------------------------------------------------------ the published item

class PublishedItem:
    """Proxy of a published item (``Part::FeaturePython``)."""

    @classmethod
    def attach(cls, obj):
        obj.Proxy = cls.__new__(cls)

    def __init__(self, obj):
        obj.addProperty("App::PropertyXLinkSub", "Source", PROP_GROUP, "The skeleton geometry that is published")
        obj.addProperty("App::PropertyString", "ItemName", PROP_GROUP, "Name of the item in its publication")
        obj.addProperty("App::PropertyString", "Publication", PROP_GROUP, "Publication the item belongs to")
        obj.addProperty("App::PropertyString", "Kind", PROP_GROUP,
                        "What is published: object, face, edge or vertex")
        obj.addProperty("App::PropertyString", "SkeletonSchema", PROP_GROUP, "Marks this object as a published item")
        obj.addProperty("App::PropertyInteger", "SkeletonId", PROP_GROUP, "ID of the skeleton part (a name can be reused)")
        obj.addProperty("App::PropertyString", "SkeletonName", PROP_GROUP,
                        "Name of the skeleton part that carries the item (a name, not a link: a link would make the item wait for the whole skeleton)")
        obj.addProperty("App::PropertyBool", "Published", PROP_GROUP,
                        "False once the item was withdrawn from the publication")
        obj.addProperty("App::PropertyString", "Status", PROP_GROUP,
                        "ok, or why the item cannot be resolved (set by the last recompute)")
        obj.Published = True
        obj.Status = "ok"
        obj.SkeletonSchema = SCHEMA
        obj.Proxy = self

    def execute(self, obj):
        shape = resolve_item(obj)           # raises SkeletonError with the reason
        obj.Placement = shape.Placement         # a feature's Placement and its shape's location are one thing
        obj.Shape = shape                       # always: no fingerprint decides whether the geometry changed
        if obj.Status != "ok":
            obj.Status = "ok"

    def onChanged(self, obj, prop):
        pass


def _is_container(o):
    """Groups, bodies, parts, assemblies and links hold or show other objects; they depend on a binder only by containing
    it, so a break is not propagated into or through them."""
    if o.TypeId in ("App::Link", "App::LinkElement", "PartDesign::Body", "App::Part"):
        return True
    try:
        return o.hasExtension("App::GroupExtension") or o.hasExtension("App::GeoFeatureGroupExtension")
    except Exception:
        return False


def _touch_dependents(obj):
    """Mark the features downstream of ``obj`` (any document) as not up to date, stopping at containers."""
    try:
        todo, seen = list(obj.InList), set()
        while todo:
            o = todo.pop()
            if (o.Document.Name, o.Name) in seen or _is_container(o):
                continue
            seen.add((o.Document.Name, o.Name))
            o.touch()
            todo += list(o.InList)
    except Exception:
        pass


def _fail(obj, reason):
    """Record why the item cannot be resolved, mark everything that depends on it as not up to date (FreeCAD does not
    recompute the dependents of a failed object and would leave them Up-to-date, with the old geometry) and raise."""
    if obj.Status != reason:
        obj.Status = reason
    _touch_dependents(obj)
    raise SkeletonError(reason)


def resolve_item(obj):
    """The shape an item publishes now, or SkeletonError (after recording the reason in ``Status``)."""
    if not obj.Published:
        _fail(obj, "withdrawn from the publication")
    src = obj.Source
    if not src:
        _fail(obj, "the source of the item is gone")
    target, subs = src[0], list(src[1])
    if target is None:
        _fail(obj, "the source of the item is gone")
    sub = subs[0] if subs else ""
    if obj.Kind == "object":
        if sub:
            _fail(obj, "the item publishes a whole object but its link names %r" % sub)
        shape = getattr(target, "Shape", None)
        if shape is None or shape.isNull():
            shape = _datum_shape(target)        # the core restores a stored datum line with a null shape
        if shape is None or shape.isNull():
            _fail(obj, "the published object %s has no shape" % target.Label)
        return shape.copy()
    kind = _KINDS_BY_VALUE.get(obj.Kind)
    if not sub or sub.startswith("?"):
        _fail(obj, "the published %s no longer exists" % obj.Kind)
    if not sub.split(".")[-1].startswith(kind):
        _fail(obj, "the published %s no longer exists (the link names %r)" % (obj.Kind, sub))
    try:
        shape = target.Shape.getElement(sub.split(".")[-1]) if "." not in sub else _sub_shape(target, sub)
    except Exception:
        shape = None
    if shape is None or shape.isNull():
        _fail(obj, "the published %s no longer exists" % obj.Kind)
    return shape.copy()


_KINDS_BY_VALUE = {"face": "Face", "edge": "Edge", "vertex": "Vertex"}


def _sub_shape(target, sub):
    import Part
    return Part.getShape(target, sub, needSubElement=True)


_ITEM_PROPS = {"Source": "App::PropertyXLinkSub", "ItemName": "App::PropertyString", "Publication": "App::PropertyString",
               "Kind": "App::PropertyString", "Published": "App::PropertyBool", "Status": "App::PropertyString",
               "SkeletonName": "App::PropertyString", "SkeletonId": "App::PropertyInteger",
               "SkeletonSchema": "App::PropertyString"}


def _datum_shape(target):
    """The unbounded plane, line or point of a PartDesign datum, built from its Placement, for a datum that has no shape.
    A datum line, plane or point gets its infinite shape once, in its constructor, and the core cannot store an infinite
    BRep: a datum restored from a file has a null shape that no recompute rebuilds. Same geometry as the stock shape."""
    import Part
    kind = {"PartDesign::Line": Part.Line, "PartDesign::Plane": Part.Plane}.get(target.TypeId)
    if target.TypeId == "PartDesign::Point":
        sh = Part.Vertex(0, 0, 0)
    elif kind is not None:
        sh = kind().toShape()
    else:
        return None
    sh.Placement = target.Placement
    return sh


def is_item(obj):
    """A published item is recognised by its type, the types of its properties and the schema marker it was given,
    never by property names alone (an unrelated feature that happens to share the names is not ours). The proxy is
    not consulted: a file re-saved by stable FreeCAD keeps the properties and drops the proxy."""
    if obj.TypeId != "Part::FeaturePython":
        return False
    props = obj.PropertiesList
    try:
        return all(p in props and obj.getTypeIdOfProperty(p) == t for p, t in _ITEM_PROPS.items()) \
            and obj.SkeletonSchema == SCHEMA
    except Exception:
        return False


def _ensure_proxy(obj, cls):
    """Attach ``cls`` only to an object that has no proxy at all; a foreign proxy is never replaced."""
    p = getattr(obj, "Proxy", None)
    if p is None:
        cls.attach(obj)
        return True
    return isinstance(p, cls)


def skeleton_of(item):
    """The skeleton part of an item, bound by name and object ID: a new part that took a deleted skeleton's name is not it."""
    o = item.Document.getObject(item.SkeletonName)
    return o if o is not None and o.ID == item.SkeletonId else None


def items(skeleton, publication=None, include_withdrawn=False):
    """The published items of a skeleton, in creation order."""
    out = []
    for o in skeleton.Document.Objects:
        if not is_item(o) or o.SkeletonName != skeleton.Name or o.SkeletonId != skeleton.ID:
            continue
        if not include_withdrawn and not o.Published:
            continue
        if publication is not None and o.Publication != publication:
            continue
        _ensure_proxy(o, PublishedItem)
        out.append(o)
    return out


def find_item(skeleton, name, publication=None, include_withdrawn=False):
    for o in items(skeleton, publication, include_withdrawn):
        if o.ItemName == name:
            return o
    raise SkeletonError("%s publishes no item named %r" % (skeleton.Label, name))


# ------------------------------------------------------------------ the subscription

class Subscription:
    """Proxy of a subscriber binder (``PartDesign::SubShapeBinderPython``)."""

    @classmethod
    def attach(cls, obj):
        obj.Proxy = cls.__new__(cls)

    def __init__(self, obj):
        obj.addProperty("App::PropertyString", "Subscription", PROP_GROUP,
                        "What the binder subscribes to (JSON: item, publication, skeleton document)")
        obj.addProperty("App::PropertyString", "Basis", PROP_GROUP,
                        "Digest of the item's shape at the last following recompute")
        obj.Proxy = self

    def execute(self, obj):
        info = classify(obj)
        if info["state"] == UNLOADED:
            return True                     # keep the last shape: the stock execute would see no Support
        if info["state"] == BROKEN:
            _touch_dependents(obj)          # the features built on this subscriber must not stay Up-to-date
            raise SkeletonError(info["reason"])
        item = info["_item"]
        if item is not None and obj.BindMode != "Frozen" and _item_problem(item)[0] is None:
            digest = shape_digest(item.Shape) or ""
            if obj.Basis != digest:
                obj.Basis = digest
        return False                        # let the stock binder do the (un)update

    def onChanged(self, obj, prop):
        pass


def is_subscription(obj):
    """A subscriber binder: right type, a Subscription record that carries our schema (never by the name alone)."""
    if obj.TypeId != "PartDesign::SubShapeBinderPython" or "Subscription" not in obj.PropertiesList \
            or "Basis" not in obj.PropertiesList:
        return False
    try:
        return json.loads(obj.Subscription).get("schema") == SCHEMA
    except (ValueError, AttributeError):
        return False


def _info(binder):
    try:
        return json.loads(binder.Subscription) if binder.Subscription else {}
    except ValueError:
        return {}


def _support_item(binder):
    sup = binder.Support
    if not sup:
        return None
    return sup[0][0]


def _real(path):
    return os.path.realpath(path) if path else ""


def _document_open(binder, rec):
    """Is the skeleton's document open? Identified by its Uid, which survives Save As and a reload (never by a name, which
    an unrelated document can share); records without a Uid fall back to the resolved path of the link's target."""
    if rec.get("same"):
        return True
    docs = [d for d in App.listDocuments().values()]
    if rec.get("uid"):
        return any(d.Uid == rec["uid"] for d in docs)
    rel, here = rec.get("file"), binder.Document.FileName
    if not rel or not here:
        return False
    want = _real(os.path.join(os.path.dirname(here), rel))
    return any(_real(d.FileName) == want for d in docs if d.FileName)


def classify(binder):
    """State of a subscriber from the live objects. ``_item`` (the item object or None) is for internal use.

    following needs an item whose own recompute succeeded, whose source and everything upstream of the source are
    recomputed (not Touched, not in error), whose geometry is the geometry the binder last followed, and a binder
    that holds geometry. A subscriber that fails only the recompute test is ``stale`` (recompute to refresh it); one
    whose item or upstream is in error is ``broken``."""
    rec = _info(binder)
    item = _support_item(binder)
    out = {"state": FOLLOWING, "reason": None, "_item": item, "skeleton_changed": None}
    frozen = binder.BindMode == "Frozen"
    if item is not None:
        if is_item(item):
            _ensure_proxy(item, PublishedItem)
        digest = shape_digest(item.Shape)
        if frozen:
            out["state"] = FROZEN
            out["skeleton_changed"] = bool(binder.Basis) and (digest or "") != binder.Basis
            kind, reason = _item_problem(item)
            if reason:
                out["item_problem"] = reason
            return out
        kind, reason = _item_problem(item)
        if kind:
            out.update(state=kind, reason="%s: %s" % (_item_label(item, rec), reason))
        elif (digest or "") != binder.Basis:
            out.update(state=STALE, reason="%s: the item changed since this subscriber last followed it (recompute this document)"
                       % _item_label(item, rec))
        elif _empty(binder.Shape):
            out.update(state=STALE, reason="%s: the subscriber holds no geometry (recompute)" % _item_label(item, rec))
        return out
    if frozen:
        out["state"] = FROZEN
        out["skeleton_changed"] = None if not _document_open(binder, rec) else True
        return out
    if rec and not _document_open(binder, rec):
        out.update(state=UNLOADED, reason="the skeleton document %r is not open" % (rec.get("file") or rec.get("doc")))
        return out
    out.update(state=BROKEN, reason="%s: the item no longer exists" % (rec.get("item") or "item"))
    return out


def _empty(shape):
    return shape is None or shape.isNull() or not (shape.Faces or shape.Edges or shape.Vertexes)


def _item_label(item, rec):
    return item.ItemName if item.ItemName else (rec.get("item") or item.Label)


def _upstream(item):
    """The objects whose recompute decides the item: the source and what is upstream of it (for a Body, its own state
    and the chain of its Tip, not unrelated datums), across documents."""
    src = item.Source
    if not src or src[0] is None:
        return []
    out, seen, todo = [], set(), [src[0]]
    while todo:
        o = todo.pop()
        key = (o.Document.Name, o.Name)
        if key in seen:
            continue
        seen.add(key)
        if o.TypeId == "PartDesign::Body":
            tip = getattr(o, "Tip", None)       # a Body is judged by its Tip chain: an unrelated failed datum does not count
            if tip is not None:
                todo.append(tip)
        else:
            out.append(o)
            todo += list(o.OutList)
    return out


def _item_problem(item):
    """(kind, reason): kind is BROKEN or STALE, or None with no reason when the item is sound."""
    if not is_item(item):
        return BROKEN, "the Support is not a published item"
    if not item.Published:
        return BROKEN, "withdrawn from the publication"
    if item.Status != "ok":
        return BROKEN, item.Status
    if _bad(item):
        return BROKEN, "the item is in error"
    if _empty(item.Shape):
        return STALE, "the item holds no geometry (recompute the skeleton)"
    ups = _upstream(item)
    for o in ups:
        if _bad(o):
            return BROKEN, "%s is in error" % o.Label
    for o in [item] + ups:
        if "Touched" in o.State or "Recompute" in o.State:
            return STALE, "%s has not been recomputed (recompute the skeleton)" % o.Label
    return None, None


# ------------------------------------------------------------------ publish API

def _check_skeleton(skeleton):
    if skeleton.TypeId not in ("PartDesign::Body", "App::Part") and not skeleton.isDerivedFrom("App::Part"):
        raise SkeletonError("%s is neither a Body nor a Part and cannot carry a publication" % skeleton.Label)


def _kind_of(target, sub):
    if not sub:
        if not hasattr(target, "Shape"):
            raise SkeletonError("%s has no shape to publish" % target.Label)
        return "object"
    name = sub.split(".")[-1]
    for prefix, kind in _KINDS.items():
        if name.startswith(prefix) and name[len(prefix):].isdigit():
            return kind
    raise SkeletonError("%r is not a face, edge or vertex name" % sub)


def _check_name(skeleton, name, own=None):
    name = str(name).strip()
    if not name:
        raise SkeletonError("an item needs a name")
    for o in items(skeleton):
        if o is not own and o.ItemName == name:
            raise SkeletonError("%s already publishes an item named %r" % (skeleton.Label, name))
    return name


def describe_item(item):
    sk = skeleton_of(item)
    return {"schema": SCHEMA, "item": item.ItemName, "object": item.Name, "entity": _entity(item),
            "publication": item.Publication, "kind": item.Kind, "published": bool(item.Published),
            "status": item.Status, "skeleton": sk.Label if sk else None,
            "skeleton_entity": _entity(sk) if sk else None,
            "source": _source_record(item), "digest": shape_digest(item.Shape)}


def _source_record(item):
    src = item.Source
    if not src or src[0] is None:
        return None
    sub = src[1][0] if src[1] else ""
    return {"object": src[0].Name, "sub": sub, "entity": _entity(src[0], sub)}


def publish(skeleton, name, target, sub="", publication=DEFAULT_PUBLICATION):
    """Publish ``target`` (a sketch or datum, or with ``sub`` a face, edge or vertex of a feature or of the body)
    of the skeleton under ``name``. One undo transaction. Returns the item's record."""
    _check_skeleton(skeleton)
    doc = skeleton.Document
    if target.Document != doc:
        raise SkeletonError("%s is not in the document of the skeleton" % target.Label)
    kind = _kind_of(target, sub)
    name = _check_name(skeleton, name)
    with _transaction(doc, "Publish " + name):
        item = doc.addObject("Part::FeaturePython", "Published")
        PublishedItem(item)
        item.ItemName = name
        item.Publication = str(publication)
        item.Kind = kind
        item.SkeletonName = skeleton.Name
        item.SkeletonId = skeleton.ID
        item.Source = (target, [sub] if sub else [])
        item.Label = "%s.%s" % (skeleton.Label, name)
        doc.recompute([item])
        if item.Status != "ok" or _bad(item):
            raise SkeletonError("cannot publish %s: %s" % (name, item.Status))
    return describe_item(item)


def unpublish(skeleton, name):
    """Withdraw an item from the publication. Its subscribers become broken (never retargeted). One transaction."""
    item = find_item(skeleton, name)
    doc = skeleton.Document
    with _transaction(doc, "Unpublish " + name):
        item.Published = False
        doc.recompute([item])
    return describe_item(item)


def rename(skeleton, name, new_name):
    """Rename an item. Subscribers reference the item object and are not affected. One transaction."""
    item = find_item(skeleton, name)
    new_name = _check_name(skeleton, new_name, own=item)
    doc = skeleton.Document
    with _transaction(doc, "Rename item " + name):
        item.ItemName = new_name
        item.Label = "%s.%s" % (skeleton.Label, new_name)
    return describe_item(item)


# ------------------------------------------------------------------ subscribe API

def subscribe(holder, item, label=None, object_name="Subscription"):
    """Create a subscriber binder in ``holder`` (a Body or Part) for a published item (an item object, possibly in
    another document). One undo transaction. Returns the subscription's record."""
    if not is_item(item):
        raise SkeletonError("%s is not a published item" % item.Label)
    if not item.Published:
        raise SkeletonError("%s was withdrawn from its publication" % item.ItemName)
    doc = holder.Document
    _refuse_cycle(holder, item)
    if item.Document != doc:
        if not doc.FileName:
            raise SkeletonError("save the subscriber's document before subscribing to an item of another document")
        if not item.Document.FileName:
            raise SkeletonError("save the skeleton's document before subscribing to one of its items")
    with _transaction(doc, "Subscribe to " + item.ItemName):
        b = holder.newObject("PartDesign::SubShapeBinderPython", object_name)
        Subscription(b)
        b.Subscription = json.dumps(_record_of(item, doc), sort_keys=True)
        b.Label = label or item.ItemName
        b.Support = [(item, ("",))]
        doc.recompute([b])
    return describe_subscription(b)


def _closure(obj):
    seen, todo = set(), [obj]
    while todo:
        o = todo.pop()
        key = (o.Document.Name, o.Name)
        if key not in seen:
            seen.add(key)
            todo += list(o.OutList)
    return seen


def _refuse_cycle(holder, item):
    """A subscriber whose holder is upstream of the item would make the two wait for each other for ever."""
    up = _closure(item)
    todo = [holder]
    while todo:
        o = todo.pop()
        if (o.Document.Name, o.Name) in up:
            raise SkeletonError("%s is upstream of %s: subscribing would make a dependency cycle" % (o.Label, item.ItemName))
        todo += list(getattr(o, "Group", []) or [])


def _record_of(item, holder_doc=None):
    """What a subscriber remembers about its item. The file is a path relative to the subscriber's own file (no machine path)."""
    d = item.Document
    same = holder_doc is None or d == holder_doc
    rel = ""
    if not same and d.FileName and holder_doc.FileName:
        rel = os.path.relpath(_real(d.FileName), os.path.dirname(_real(holder_doc.FileName)))
    return {"schema": SCHEMA, "item": item.ItemName, "publication": item.Publication, "skeleton": skeleton_of(item).Label,
            "doc": d.Name, "uid": d.Uid, "file": rel, "same": same}


def unsubscribe(binder):
    doc = binder.Document
    with _transaction(doc, "Unsubscribe"):
        doc.removeObject(binder.Name)


def freeze(binder):
    """Keep the geometry the subscriber has now; the link stays. One undo transaction."""
    return _set_mode(binder, "Frozen", "Freeze")


def unfreeze(binder):
    """Follow the item again; the subscriber takes the item's current geometry. One undo transaction."""
    return _set_mode(binder, "Synchronized", "Unfreeze")


def _set_mode(binder, mode, title):
    _need_subscription(binder)
    doc = binder.Document
    with _transaction(doc, "%s %s" % (title, binder.Label)):
        binder.BindMode = mode
        if mode == "Synchronized":
            binder.touch()
        doc.recompute([binder])
    return describe_subscription(binder)


def _need_subscription(binder):
    if not is_subscription(binder):
        raise SkeletonError("%s is not a skeleton subscription" % binder.Label)
    _ensure_proxy(binder, Subscription)


def describe_subscription(binder):
    if is_subscription(binder):
        _ensure_proxy(binder, Subscription)
    info = classify(binder)
    item = info["_item"]
    rec = _info(binder)
    out = {"schema": SCHEMA, "subscriber": binder.Label, "object": binder.Name, "entity": _entity(binder),
           "document": binder.Document.Name, "state": info["state"], "reason": info["reason"],
           "item": item.ItemName if item is not None else rec.get("item"),
           "publication": item.Publication if item is not None else rec.get("publication"),
           "skeleton": (skeleton_of(item).Label if item is not None and skeleton_of(item) is not None else rec.get("skeleton")),
           "skeleton_document": rec.get("doc"),
           "item_entity": _entity(item) if item is not None else None,
           "frozen": binder.BindMode == "Frozen", "skeleton_changed": info["skeleton_changed"],
           "shape_digest": shape_digest(binder.Shape)}
    if info.get("item_problem"):
        out["item_problem"] = info["item_problem"]
    return out


def subscriptions(scope=None):
    """Every subscriber binder of a document (or of every open document), as records."""
    docs = [scope] if scope is not None and hasattr(scope, "Objects") else list(App.listDocuments().values())
    out = []
    for d in docs:
        for o in d.Objects:
            if is_subscription(o):
                out.append(o)
    return out


def refresh(scope=None):
    """Touch and recompute the subscribers of a document (or of every open document), so that a broken one goes
    into error instead of waiting for its published item to change. Not an edit: no transaction. Returns ``status``."""
    docs = [scope] if scope is not None else list(App.listDocuments().values())
    for d in docs:
        subs = subscriptions(d)
        for b in subs:
            if b.BindMode != "Frozen":
                b.touch()
        if subs:
            d.recompute()
    return status(scope)


def subscribers_of(item):
    return [b for b in subscriptions() if _support_item(b) == item]


def status(target=None):
    """JSON-ready status. ``target`` is a skeleton (publication with its subscribers), a document (its publications
    and subscriptions) or None (every open document)."""
    if target is not None and hasattr(target, "TypeId") and not hasattr(target, "Objects"):
        return _publication_status(target)
    docs = [target] if target is not None else list(App.listDocuments().values())
    pubs, subs = [], []
    for d in docs:
        skels = {o.SkeletonName: skeleton_of(o) for o in d.Objects if is_item(o) and skeleton_of(o) is not None}
        for sk in skels.values():
            pubs.append(_publication_status(sk))
        subs += [describe_subscription(b) for b in subscriptions(d)]
    return {"schema": SCHEMA, "publications": pubs, "subscriptions": subs}


def _publication_status(skeleton):
    recs = []
    for it in items(skeleton, include_withdrawn=True):
        r = describe_item(it)
        r["subscribers"] = [describe_subscription(b) for b in subscribers_of(it)]
        recs.append(r)
    return {"schema": SCHEMA, "skeleton": skeleton.Label, "entity": _entity(skeleton), "items": recs}


def to_json(obj):
    return json.dumps(obj, sort_keys=True, indent=1)


def signature(doc):
    """Object-and-property signature of a document for the round-trip and undo checks."""
    out = {}
    for o in doc.Objects:
        props = {}
        for p in o.PropertiesList:
            if p in ("Shape", "Proxy", "ExpressionEngine", "Visibility", "Label2"):
                continue
            try:
                props[p] = re.sub(r" at 0x[0-9a-fA-F]+", "", repr(o.getPropertyByName(p)))
            except Exception:
                props[p] = "?"
        out[o.Name] = {"type": o.TypeId, "label": o.Label, "props": props,
                       "shape": shape_digest(o.Shape) if hasattr(o, "Shape") else None}
    return out


# ------------------------------------------------------------------ proxies lost in a re-save by stable FreeCAD

def reattach(doc):
    """Make a document ready for a recompute: give its items and subscribers their proxy back (a file re-saved by a
    FreeCAD without this module keeps every property but drops the Python proxies), touch a datum or item that was
    restored without geometry, and touch a subscriber whose item changed since it last followed it (the skeleton was
    edited while the subscriber's document was closed), so that this very recompute brings them up to date."""
    for o in doc.Objects:
        if is_item(o):
            _ensure_proxy(o, PublishedItem)
            if _empty(o.Shape):
                o.touch()                       # restored without geometry (a datum): rebuild it in this recompute
        elif is_subscription(o):
            _ensure_proxy(o, Subscription)
            it = _support_item(o)
            if it is not None and is_item(it):
                _ensure_proxy(it, PublishedItem)
                if o.BindMode != "Frozen" and _item_problem(it)[0] is None \
                        and (shape_digest(it.Shape) or "") != o.Basis:
                    o.touch()


_CANDIDATES = ("Part::FeaturePython", "PartDesign::SubShapeBinderPython")


class _Observer:
    """Counts the objects that could be items or subscribers per document, so that a recompute of any other document costs
    one dictionary lookup."""

    def __init__(self):
        self.count = {}

    def scan(self):
        for d in App.listDocuments().values():
            self.count[d.Name] = sum(1 for o in d.Objects if o.TypeId in _CANDIDATES)

    def slotCreatedObject(self, obj):
        if obj.TypeId in _CANDIDATES:
            self.count[obj.Document.Name] = self.count.get(obj.Document.Name, 0) + 1

    def slotDeletedDocument(self, doc):
        self.count.pop(doc.Name, None)

    def slotBeforeRecomputeDocument(self, doc):
        _prepare(doc)


def _prepare(doc):
    if not _observer.count.get(doc.Name):
        return
    try:
        reattach(doc)
    except Exception as exc:  # never let this break a recompute
        App.Console.PrintWarning("AssemblySkeleton: preparing %s for a recompute failed: %s\n" % (doc.Name, exc))


_observer = None


def install_observer():
    """Idempotent; Init.py calls it at start-up, so every recompute of a document that holds items or subscribers
    prepares it first."""
    global _observer
    if _observer is None:
        _observer = _Observer()
        _observer.scan()
        App.addDocumentObserver(_observer)


install_observer()
