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

Subscription. A ``PartDesign::SubShapeBinderPython`` whose ``Support`` is the whole item object (never a raw element
of the skeleton, never a name string) and whose Python proxy decides, at every recompute, what the stock binder
may do. Four states, always computed from the live objects (``classify``) and never stored:

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

import ReplaceComponent as _rc

SCHEMA = "cadfork.assembly.skeleton/1"
PROP_GROUP = "Skeleton"
DEFAULT_PUBLICATION = "Default"

FOLLOWING, FROZEN, BROKEN, UNLOADED = "following", "frozen", "broken", "unloaded"
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
    return _rc._entity(obj, sub)


# ------------------------------------------------------------------ shape helpers

def _r(x):
    return round(x, 6) + 0.0


def shape_digest(shape):
    """sha1 of a geometric key of a shape (type, counts, and the rounded measure and centre of every face, else edge, else
    vertex; an unbounded datum by its plane or line); None for a null shape. A reload from a file or a recompute that
    rebuilds the same geometry gives the same key; the BRep text is not stable enough for that."""
    if shape is None or shape.isNull():
        return None
    key = [shape.ShapeType, len(shape.Faces), len(shape.Edges), len(shape.Vertexes)]
    try:
        if shape.BoundBox.DiagonalLength > 1e50:
            if shape.Faces:
                s = shape.Faces[0].Surface
                key += [_r(s.Axis.x), _r(s.Axis.y), _r(s.Axis.z), _r(s.Axis.dot(s.Position))]
            elif shape.Edges:
                c = shape.Edges[0].Curve
                key += [_r(c.Direction.x), _r(c.Direction.y), _r(c.Direction.z),
                        _r(c.Location.dot(c.Direction.cross(App.Vector(1, 0, 0)))),
                        _r(c.Location.dot(c.Direction.cross(App.Vector(0, 1, 0))))]
        elif shape.Faces:
            key += sorted((_r(f.Area), _r(f.CenterOfMass.x), _r(f.CenterOfMass.y), _r(f.CenterOfMass.z)) for f in shape.Faces)
        elif shape.Edges:
            key += sorted((_r(e.Length), _r(e.CenterOfMass.x), _r(e.CenterOfMass.y), _r(e.CenterOfMass.z)) for e in shape.Edges)
        else:
            key += sorted((_r(v.X), _r(v.Y), _r(v.Z)) for v in shape.Vertexes)
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
        obj.addProperty("App::PropertyLink", "Skeleton", PROP_GROUP, "The skeleton part that carries the item")
        obj.addProperty("App::PropertyBool", "Published", PROP_GROUP,
                        "False once the item was withdrawn from the publication")
        obj.addProperty("App::PropertyString", "Status", PROP_GROUP,
                        "ok, or why the item cannot be resolved (set by the last recompute)")
        obj.Published = True
        obj.Status = "ok"
        obj.Proxy = self

    def execute(self, obj):
        shape = resolve_item(obj)           # raises SkeletonError with the reason
        if shape_digest(shape) != shape_digest(obj.Shape):
            obj.Placement = shape.Placement     # a feature's Placement and its shape's location are one thing
            obj.Shape = shape
        if obj.Status != "ok":
            obj.Status = "ok"

    def onChanged(self, obj, prop):
        pass


def _fail(obj, reason):
    if obj.Status != reason:
        obj.Status = reason
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


def is_item(obj):
    """By proxy, or by properties alone (the proxy is lost when stable FreeCAD re-saves a file)."""
    if isinstance(getattr(obj, "Proxy", None), PublishedItem):
        return True
    return obj.TypeId == "Part::FeaturePython" and all(
        p in obj.PropertiesList for p in ("Source", "ItemName", "Publication", "Published", "Status"))


def items(skeleton, publication=None, include_withdrawn=False):
    """The published items of a skeleton, in creation order."""
    out = []
    for o in skeleton.Document.Objects:
        if not is_item(o) or o.Skeleton != skeleton:
            continue
        if not include_withdrawn and not o.Published:
            continue
        if publication is not None and o.Publication != publication:
            continue
        if not isinstance(getattr(o, "Proxy", None), PublishedItem):
            PublishedItem.attach(o)
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
            raise SkeletonError(info["reason"])
        if info["state"] == FOLLOWING and info["_item"] is not None:
            digest = shape_digest(info["_item"].Shape)
            if obj.Basis != (digest or ""):
                obj.Basis = digest or ""
        return False                        # let the stock binder do the (un)update

    def onChanged(self, obj, prop):
        pass


def is_subscription(obj):
    if isinstance(getattr(obj, "Proxy", None), Subscription):
        return True
    return obj.TypeId == "PartDesign::SubShapeBinderPython" and "Subscription" in obj.PropertiesList \
        and bool(obj.Subscription)


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


def _document_open(rec):
    for d in App.listDocuments().values():
        if rec.get("doc") and d.Name == rec["doc"]:
            return True
        if rec.get("file") and d.FileName and os.path.basename(d.FileName) == rec["file"]:
            return True
    return False


def classify(binder):
    """State of a subscriber from the live objects. ``_item`` (the item object or None) is for internal use."""
    rec = _info(binder)
    item = _support_item(binder)
    out = {"state": FOLLOWING, "reason": None, "_item": item, "skeleton_changed": None}
    frozen = binder.BindMode == "Frozen"
    if item is not None:
        digest = shape_digest(item.Shape)
        if frozen:
            out["state"] = FROZEN
            out["skeleton_changed"] = bool(binder.Basis) and (digest or "") != binder.Basis
            reason = _item_problem(item)
            if reason:
                out["item_problem"] = reason
            return out
        reason = _item_problem(item)
        if reason:
            out.update(state=BROKEN, reason="%s: %s" % (_item_label(item, rec), reason))
        return out
    if frozen:
        out["state"] = FROZEN
        out["skeleton_changed"] = None if not _document_open(rec) else True
        return out
    if rec and not _document_open(rec):
        out.update(state=UNLOADED, reason="the skeleton document %r is not open" % rec.get("doc"))
        return out
    out.update(state=BROKEN, reason="%s: the item no longer exists" % (rec.get("item") or "item"))
    return out


def _item_label(item, rec):
    return item.ItemName if item.ItemName else (rec.get("item") or item.Label)


def _item_problem(item):
    if not is_item(item):
        return "the Support is not a published item"
    if not item.Published:
        return "withdrawn from the publication"
    if item.Status != "ok":
        return item.Status
    if _bad(item):
        return "the item is in error"
    src = item.Source
    if src and src[0] is not None and _bad(src[0]):
        return "the published %s is in error" % src[0].Label
    return None


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
    sk = item.Skeleton
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
        item.Skeleton = skeleton
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
    if item.Document != doc:
        if not doc.FileName:
            raise SkeletonError("save the subscriber's document before subscribing to an item of another document")
        if not item.Document.FileName:
            raise SkeletonError("save the skeleton's document before subscribing to one of its items")
    with _transaction(doc, "Subscribe to " + item.ItemName):
        b = holder.newObject("PartDesign::SubShapeBinderPython", object_name)
        Subscription(b)
        b.Subscription = json.dumps(_record_of(item), sort_keys=True)
        b.Label = label or item.ItemName
        b.Support = [(item, ("",))]
        doc.recompute([b])
    return describe_subscription(b)


def _record_of(item):
    d = item.Document
    return {"item": item.ItemName, "publication": item.Publication, "skeleton": item.Skeleton.Label,
            "doc": d.Name, "file": os.path.basename(d.FileName) if d.FileName else ""}


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
    if not isinstance(getattr(binder, "Proxy", None), Subscription):
        Subscription.attach(binder)


def describe_subscription(binder):
    if not isinstance(getattr(binder, "Proxy", None), Subscription) and is_subscription(binder):
        Subscription.attach(binder)
    info = classify(binder)
    item = info["_item"]
    rec = _info(binder)
    out = {"schema": SCHEMA, "subscriber": binder.Label, "object": binder.Name, "entity": _entity(binder),
           "document": binder.Document.Name, "state": info["state"], "reason": info["reason"],
           "item": item.ItemName if item is not None else rec.get("item"),
           "publication": item.Publication if item is not None else rec.get("publication"),
           "skeleton": rec.get("skeleton"), "skeleton_document": rec.get("doc"),
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
        skels = {o.Skeleton.Name: o.Skeleton for o in d.Objects if is_item(o) and o.Skeleton is not None}
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
