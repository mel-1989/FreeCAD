# SPDX-License-Identifier: LGPL-2.1-or-later
"""Scenes and helpers shared by the Skeleton panel GUI tests and the harness screenshot scene (G06 slice 3b).

The baseline document is one file's worth of scene, built from the 3a corpus kit: a skeleton Body ``Skel`` (a 40 x 20 x 10
plate with a datum plane, line and point) that publishes ``Top_Face``, ``Edge_Y``, ``Mid_Plane`` and ``Profile``, two subscriber
Bodies ``PartA`` and ``PartB``, and a fifth item ``Spare`` that was published, subscribed by ``PartB`` and then withdrawn.
The subscriptions: ``PartA`` follows ``Top_Face`` and holds ``Mid_Plane`` frozen; ``PartB`` follows ``Edge_Y`` and ``Profile`` and
holds ``Spare`` broken. Cross-document cases (unloaded, broken in another document, go-to across documents) are built by the
tests with ``skeleton_corpus.World``, not here.
"""

import ast
import json
import re

import FreeCAD as App

import AssemblySkeleton as AS
from AssemblyTests import feature_gui_fixture as FG
from AssemblyTests import skeleton_corpus as K

_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

ITEMS = (("Top_Face", "face"), ("Edge_Y", "edge"), ("Mid_Plane", "plane"), ("Profile", "sketch"))
SUBSCRIPTIONS = {"PartA": [("Top_Face", False), ("Mid_Plane", True)],
                 "PartB": [("Edge_Y", False), ("Profile", False), ("Spare", False)]}


class Rig:
    """The document and the names the tests use."""

    def __init__(self, doc):
        self.doc = doc
        self.skel = doc.getObject("Skel")
        self.parts = {n: doc.getObject(n) for n in ("PartA", "PartB")}

    def binder(self, part, item):
        """The subscriber object of ``part`` for item name ``item``."""
        for b in AS.subscriptions(self.doc):
            if b.getParentGeoFeatureGroup() == self.parts[part] and AS.describe_subscription(b)["item"] == item:
                return b
        raise LookupError("%s holds no subscription to %s" % (part, item))

    def close(self):
        if self.doc.Name in App.listDocuments():
            App.closeDocument(self.doc.Name)


def build(name="SkelGui"):
    doc = App.newDocument(name)
    skel = K.build_skeleton(doc, "Skel")
    for item, key in ITEMS + (("Spare", "point"),):
        tgt, sub = K.target_of(skel, key)
        AS.publish(skel, item, tgt, sub)
    for part in ("PartA", "PartB"):
        doc.addObject("PartDesign::Body", part)
    for part, subs in SUBSCRIPTIONS.items():
        for item, frozen in subs:
            b = AS.subscribe(doc.getObject(part), AS.find_item(skel, item), object_name="Sub_" + item)
            if frozen:
                AS.freeze(doc.getObject(b["object"]))
    AS.unpublish(skel, "Spare")
    AS.refresh(doc)
    return Rig(doc)


def scene_doc(name, case=None):
    return build("SkelScene")


def scene_panel(rig):
    """The panel a harness scene shows: opened on ``PartB`` (a part with a following, a frozen-to-be and a broken subscription), with one
    freeze staged so the Change column shows and the broken subscription selected so its reason shows. Nothing is shown yet."""
    from SkeletonPanel import TaskSkeleton

    panel = TaskSkeleton(rig.parts["PartB"])
    panel.freeze(rig.binder("PartB", "Profile").Name)
    panel.select_subscription(rig.binder("PartB", "Spare").Name)
    return panel


grab_png = FG.grab_png


def normalized(doc):
    """``AssemblySkeleton.signature`` of a document with what differs between two documents built the same way removed: the
    document Uid and name that a subscription records, the object ID an item records for its skeleton, and GUID-valued properties."""
    sig = json.loads(json.dumps(AS.signature(doc), default=str))
    for rec in sig.values():
        props = rec["props"]
        if props.get("Subscription"):
            try:
                d = json.loads(ast.literal_eval(props["Subscription"]))     # the signature holds the repr of the string
                d.pop("uid", None)
                d.pop("doc", None)
                props["Subscription"] = json.dumps(d, sort_keys=True)
            except (ValueError, SyntaxError):
                pass
        for k in [k for k, v in props.items() if k in ("Id", "Uid", "SkeletonId") or (isinstance(v, str) and _GUID.match(v))]:
            props.pop(k)
    return sig


def states(doc):
    """Label -> (state, reason, frozen) of every subscription in the document."""
    out = {}
    for b in AS.subscriptions(doc):
        d = AS.describe_subscription(b)
        out[b.Name] = (d["state"], d["reason"], d["frozen"], d["shape_digest"])
    return out


def world_snapshot(docs):
    """Everything Cancel must leave alone: the property signature (shape hashes included) and the states, per open document."""
    return {d.Name: {"signature": AS.signature(d), "states": states(d), "undo": d.UndoCount, "redo": d.RedoCount}
            for d in docs}
