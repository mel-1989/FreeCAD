# SPDX-License-Identifier: LGPL-2.1-or-later
"""G06 slice 3a: skeleton publish and subscribe, follow / freeze / break / unload, round trip, undo, assembly, mutants.

Every case builds fresh documents (``skeleton_corpus.World``), walks edits of the skeleton and compares what the
subscribers hold with a closed-form truth. A case function returns the list of problems it found (empty: pass);
``CASES`` registers them, the test class runs each as a subtest, and the mutant test plants defects in
``AssemblySkeleton`` and requires each to make at least one case fail.
"""

import contextlib
import json
import math
import os
import re
import shutil
import tempfile
import unittest
import zipfile

import FreeCAD as App
import Part
import Sketcher

import AssemblyConfiguration as AC
import AssemblySkeleton as AS
import Interference
import JointObject
from AssemblyTests import skeleton_corpus as K

V = App.Vector
CASES = {}


def case(fn):
    CASES[fn.__name__] = fn
    return fn


def chk(P, cond, msg):
    if not cond:
        P.append(msg)


def state(b):
    return AS.describe_subscription(b)["state"]


def sig(b, key):
    return K.observe_shape(b.Shape, key)


def digest(b):
    return AS.shape_digest(b.Shape)


def follows(P, world_, key, tag="", **geom):
    b = world_.binders[key]
    try:
        got = sig(b, key)
    except Exception as exc:
        P.append("%s %s: no geometry (%s)" % (tag, key, exc))
        return
    want = K.truth(key, **geom)
    chk(P, K.same(got, want), "%s %s: holds %s, truth %s" % (tag, key, got, want))
    chk(P, state(b) == "following", "%s %s: state %s" % (tag, key, state(b)))


def invalid_upstream(obj, seen=None):
    seen = seen if seen is not None else set()
    out = []
    for o in obj.OutList:
        if o.Name in seen:
            continue
        seen.add(o.Name)
        if "Invalid" in o.State or "Error" in o.State:
            out.append(o.Name)
        out += invalid_upstream(o, seen)
    return out


@contextlib.contextmanager
def world(*a, **kw):
    w = K.World(*a, **kw)
    try:
        yield w
    finally:
        w.close()


# ------------------------------------------------------------------ item kinds (same document)

def _kind_case(key, xdoc):
    def fn():
        P = []
        with world(xdoc) as w:
            w.both(key)
            b = w.binders[key]
            sup = b.Support
            chk(P, len(sup) == 1 and AS.is_item(sup[0][0]) and sup[0][1] in (("",), [""]),
                "the subscriber does not name the published item: %s" % (sup,))
            follows(P, w, key, "L=10")
            w.set_length(25.0)
            follows(P, w, key, "L=25", L=25.0)
            w.set_length(7.5)
            follows(P, w, key, "L=7.5", L=7.5)
        return P
    fn.__name__ = "kind_%s_%s" % (key, "xdoc" if xdoc else "same")
    return fn


for _k in K.KEYS:
    case(_kind_case(_k, False))
for _k in ("face", "edge", "sketch", "plane"):
    case(_kind_case(_k, True))


# ------------------------------------------------------------------ reached through App::Link and a sub-assembly

@case
def subscriber_through_link():
    P = []
    with world(False) as w:
        w.publish("face")
        part = w.doc.addObject("App::Part", "PartA")
        body = part.newObject("PartDesign::Body", "SubBody")
        b = w.subscribe("face", holder=body)
        lnk = w.doc.addObject("App::Link", "Lnk")
        lnk.LinkedObject = part
        lnk.Placement = App.Placement(V(100, 0, 0), App.Rotation())
        w.doc.recompute()
        for L in (10.0, 25.0):
            if L != 10.0:
                w.set_length(L)
            sh = Part.getShape(lnk, "SubBody.%s." % b.Name, needSubElement=False)
            got = K.observe_shape(sh, "face")
            want = K.truth("face", L=L)
            want = (want[0], want[1], want[2] + 100, want[3], want[4])
            chk(P, K.same(got, want), "through the link at L=%g: %s, truth %s" % (L, got, want))
    return P


@case
def subscriber_in_subassembly():
    P = []
    with world(False) as w:
        w.publish("edge")
        asm = w.doc.addObject("Assembly::AssemblyObject", "Assembly")
        subasm = asm.newObject("Assembly::AssemblyObject", "SubAsm")
        subasm.Placement = App.Placement(V(0, 50, 0), App.Rotation())
        part = subasm.newObject("App::Part", "PA")
        body = part.newObject("PartDesign::Body", "SubBody")
        b = w.subscribe("edge", holder=body)
        w.doc.recompute()
        for L in (10.0, 25.0):
            if L != 10.0:
                w.set_length(L)
            sh = Part.getShape(asm, "SubAsm.PA.SubBody.%s." % b.Name, needSubElement=False)
            got = K.observe_shape(sh, "edge")
            want = K.truth("edge", L=L)       # a binder keeps the skeleton's own position whatever the sub-assembly's placement
            chk(P, K.same(got, want), "through the sub-assembly at L=%g: %s, truth %s" % (L, got, want))
            local = K.observe_shape(b.Shape, "edge")
            chk(P, abs(local[3] - (want[3] - 50.0)) < 1e-9, "the binder did not compensate the sub-assembly offset: %s" % (local,))
            chk(P, state(b) == "following", "state %s" % state(b))
    return P


# ------------------------------------------------------------------ features built on a subscription

def _slab_sketch(body, support, name="S2"):
    s = body.newObject("Sketcher::SketchObject", name)
    s.AttachmentSupport = support
    s.MapMode = "FlatFace"
    K.fill_rect(s, 10.0, 10.0)
    return s


@case
def pad_up_to_subscribed_face():
    P = []
    with world(False) as w:
        w.both("face")
        b = w.binders["face"]
        s = _slab_sketch(w.holder, (w.holder.Origin.OriginFeatures[3], [""]), "UpSketch")
        pad = w.holder.newObject("PartDesign::Pad", "UpPad")
        pad.Profile = s
        pad.Type = "UpToFace"
        pad.UpToFace = (b, ["Face1"])
        w.recompute()
        for L in (10.0, 25.0):
            if L != 10.0:
                w.set_length(L)
            chk(P, abs(pad.Shape.Volume - 100.0 * L) < 1e-6, "pad up to the subscribed face at L=%g: volume %s, truth %s"
                % (L, pad.Shape.Volume, 100.0 * L))
    return P


@case
def sketch_on_subscribed_plane():
    P = []
    with world(False) as w:
        w.both("plane")
        b = w.binders["plane"]
        s = _slab_sketch(w.holder, [(b, [""])])
        w.recompute()
        for width in (40.0, 60.0):
            if width != 40.0:
                K.set_size(w.sdoc, w=width)
                w.recompute()
            n = s.Placement.Rotation.multVec(V(0, 0, 1))
            chk(P, abs(abs(n.x) - 1) < 1e-9 and abs(s.Placement.Base.x - width) < 1e-9,
                "sketch on the subscribed plane at width %g: plane x=%s normal %s" % (width, s.Placement.Base.x, n))
            chk(P, state(b) == "following", "state %s" % state(b))
    return P


@case
def feature_on_broken_subscription_reports():
    P = []
    with world(False) as w:
        w.both("face")
        b = w.binders["face"]
        s = _slab_sketch(w.holder, (w.holder.Origin.OriginFeatures[3], [""]), "UpSketch")
        pad = w.holder.newObject("PartDesign::Pad", "UpPad")
        pad.Profile = s
        pad.Type = "UpToFace"
        pad.UpToFace = (b, ["Face1"])
        w.recompute()
        v0 = pad.Shape.Volume
        h0 = digest(b)
        K.insert_vertical_fillet(w.sdoc)
        w.recompute()
        chk(P, state(b) == "broken", "state %s" % state(b))
        chk(P, digest(b) == h0, "a broken subscriber lost its last shape")
        chk(P, abs(pad.Shape.Volume - v0) < 1e-9, "the pad recomputed on other geometry: %s vs %s" % (pad.Shape.Volume, v0))
        chk(P, bool(invalid_upstream(pad)), "the pad does not report the broken subscription (no upstream error)")
        d = AS.describe_subscription(b)
        chk(P, "face" in (d["reason"] or "") and "no longer exists" in (d["reason"] or ""), "reason: %s" % d["reason"])
    return P


# ------------------------------------------------------------------ skeleton edits

@case
def edit_insert_hole_earlier():
    P = []
    with world(False) as w:
        w.both("face", "edge", "vertex")
        K.insert_hole(w.sdoc, 10.0)
        w.recompute()
        follows(P, w, "face", "hole", hole=True)
        follows(P, w, "edge", "hole")
        follows(P, w, "vertex", "hole")
    return P


@case
def edit_insert_hole_earlier_xdoc():
    P = []
    with world(True) as w:
        w.both("face", "sketch", "plane")
        K.insert_hole(w.sdoc, 10.0)
        w.recompute()
        follows(P, w, "face", "hole", hole=True)
        follows(P, w, "sketch", "hole")
        follows(P, w, "plane", "hole")
    return P


def _never_wrong(P, w, key, allowed, tag):
    """The subscriber either follows one of the closed-form truths ``allowed`` or is broken with its last shape."""
    b = w.binders[key]
    st = state(b)
    if st == "following":
        got = sig(b, key)
        chk(P, any(K.same(got, t) for t in allowed), "%s %s: following but holds %s, truth %s" % (tag, key, got, allowed))
    else:
        chk(P, st == "broken", "%s %s: state %s" % (tag, key, st))
    return st


@case
def edit_insert_fillet_earlier():
    P = []
    with world(False) as w:
        w.both("face", "edge", "vertex", "sketch")
        last = {k: sig(w.binders[k], k) for k in ("face", "edge", "vertex")}
        K.insert_vertical_fillet(w.sdoc)
        w.recompute()
        for key in ("face", "edge"):
            st = _never_wrong(P, w, key, [K.truth(key, fillet=3.0)], "fillet")
            if st == "broken":
                chk(P, K.same(sig(w.binders[key], key), last[key]), "fillet %s: broken but lost its last shape" % key)
        chk(P, state(w.binders["vertex"]) == "broken", "the consumed corner vertex is not reported broken")
        chk(P, K.same(sig(w.binders["vertex"], "vertex"), last["vertex"]), "the broken vertex lost its last shape")
        follows(P, w, "sketch", "fillet")
    return P


@case
def edit_redraw_sketch():
    P = []
    for xdoc in (False, True):
        with world(xdoc) as w:
            w.both(*K.KEYS)
            K.redraw_profile(w.sdoc, order=1)
            w.recompute()
            for k in K.KEYS:
                if k in ("plane", "line", "point"):      # datums attached to a face or vertex of the pad: the core may lose their attachment
                    _never_wrong(P, w, k, [K.truth(k)], "redraw xdoc=%s" % xdoc)
                else:
                    follows(P, w, k, "redraw xdoc=%s" % xdoc)
            K.redraw_profile(w.sdoc, order=0)
            w.recompute()
            for k in K.KEYS:
                if k in ("plane", "line", "point"):
                    _never_wrong(P, w, k, [K.truth(k)], "redraw back xdoc=%s" % xdoc)
                else:
                    follows(P, w, k, "redraw back xdoc=%s" % xdoc)
    return P


@case
def edit_resize_profile():
    P = []
    with world(True) as w:
        w.both("face", "edge", "vertex", "sketch", "plane", "line", "point")
        K.set_size(w.sdoc, w=60.0, h=30.0)
        w.recompute()
        for k in K.KEYS:
            follows(P, w, k, "60x30", w=60.0, h=30.0)
    return P


def _side_face(w):
    return w.body, K.pick_face(w.body.Shape, (1, 0, 0), (K.W0, K.H0 / 2, K.L0 / 2))


@case
def edit_delete_published_face():
    P = []
    for xdoc in (False, True):
        with world(xdoc) as w:
            tgt, sub = _side_face(w)
            AS.publish(w.body, "side", tgt, sub)
            b = w.subscribe("side")
            last = K.observe_shape(b.Shape, "face")
            chk(P, K.same(last, ("face", K.H0 * K.L0, K.W0, K.H0 / 2, K.L0 / 2)), "side face before: %s" % (last,))
            h0 = digest(b)
            K.cut_off_plus_x(w.sdoc)
            w.recompute()
            d = AS.describe_subscription(b)
            chk(P, d["state"] == "broken", "xdoc=%s: state %s" % (xdoc, d["state"]))
            chk(P, "side" in (d["reason"] or ""), "the reason does not name the item: %s" % d["reason"])
            chk(P, digest(b) == h0, "the broken subscriber lost its last shape")
            AS.refresh(w.doc)
            chk(P, "Up-to-date" not in b.State and digest(b) == h0, "xdoc=%s: refresh left the broken binder up to date (%s)" % (xdoc, b.State))
            now = [K.observe_shape(f, "face") for f in w.body.Shape.Faces]
            chk(P, not any(K.same(K.observe_shape(b.Shape, "face"), n, 1e-6) for n in now),
                "the broken subscriber holds a face of the edited skeleton (retargeted)")
            item = AS.find_item(w.body, "side", include_withdrawn=True)
            chk(P, item.Status != "ok", "the item does not report the break")
    return P


@case
def edit_delete_published_vertex_and_edge():
    P = []
    with world(False) as w:
        w.both("vertex")
        AS.publish(w.body, "vedge", w.body, K.pick_edge(w.body.Shape, (0, 0, 1), (K.W0, K.H0, K.L0 / 2)))
        eb = w.subscribe("vedge")
        last_e = sig(eb, "edge")
        last_v = sig(w.binders["vertex"], "vertex")
        K.insert_vertical_fillet(w.sdoc)
        w.recompute()
        for k, b, last in (("vertex", w.binders["vertex"], last_v), ("edge", eb, last_e)):
            chk(P, state(b) == "broken", "%s state %s" % (k, state(b)))
            chk(P, K.same(sig(b, k), last), "%s: broken subscriber lost its last shape" % k)
        cur = [K.observe_shape(v, "vertex") for v in w.body.Shape.Vertexes]
        chk(P, not any(K.same(sig(w.binders["vertex"], "vertex"), c, 1e-6) for c in cur), "vertex retargeted")
    return P


@case
def edit_rename_item():
    P = []
    for xdoc in (False, True):
        with world(xdoc) as w:
            w.both("face", "plane")
            item = AS.find_item(w.body, "face")
            b = w.binders["face"]
            rec = AS.rename(w.body, "face", "deck")
            chk(P, rec["item"] == "deck", "rename record %s" % rec["item"])
            w.recompute()
            chk(P, b.Support[0][0] == item, "the subscriber no longer names the item object")
            follows(P, w, "face", "renamed")
            w.set_length(25.0)
            follows(P, w, "face", "renamed L=25", L=25.0)
            d = AS.describe_subscription(b)
            chk(P, d["item"] == "deck" and d["state"] == "following", "status after rename: %s %s" % (d["item"], d["state"]))
            AS.publish(w.body, "face", *K.target_of(w.body, "face", L=25.0))     # the old name is free again
            chk(P, state(b) == "following", "publishing a new item under the old name changed the subscriber")
            try:
                AS.rename(w.body, "deck", "plane")
                P.append("rename onto an existing name was accepted")
            except AS.SkeletonError:
                pass
    return P


@case
def edit_unpublish_item():
    P = []
    for xdoc in (False, True):
        with world(xdoc) as w:
            w.both("face", "edge")
            h0 = digest(w.binders["face"])
            AS.unpublish(w.body, "face")
            w.recompute()
            b = w.binders["face"]
            d = AS.describe_subscription(b)
            chk(P, d["state"] == "broken" and "withdrawn" in (d["reason"] or ""), "xdoc=%s: %s %s" % (xdoc, d["state"], d["reason"]))
            AS.refresh(w.doc)
            chk(P, "Up-to-date" not in b.State and digest(b) == h0, "refresh: the broken binder is still up to date (%s) or lost its shape" % (b.State,))
            w.set_length(25.0)
            chk(P, digest(b) == h0, "a withdrawn item's subscriber moved with the skeleton")
            chk(P, state(b) == "broken", "state %s after an edit" % state(b))
            follows(P, w, "edge", "other item", L=25.0)
            names = [i["item"] for i in AS.status(w.body)["items"] if i["published"]]
            chk(P, names == ["edge"], "publication lists %s" % names)
            AS.publish(w.body, "face", *K.target_of(w.body, "face", L=25.0))     # same name again
            w.recompute()
            chk(P, state(b) == "broken", "the old subscriber re-attached to a new item of the same name")
    return P


@case
def edit_delete_item_object():
    P = []
    with world(False) as w:
        w.both("face")
        b = w.binders["face"]
        h0 = digest(b)
        w.sdoc.removeObject(AS.find_item(w.body, "face").Name)
        w.recompute()
        d = AS.describe_subscription(b)
        chk(P, d["state"] == "broken", "state %s" % d["state"])
        chk(P, digest(b) == h0, "shape lost")
        AS.refresh(w.doc)
        chk(P, "Invalid" in b.State and digest(b) == h0, "the binder of a deleted item is not in error (%s) or lost its shape" % (b.State,))
    return P


# ------------------------------------------------------------------ freeze

def _freeze_case(xdoc):
    def fn():
        P = []
        with world(xdoc) as w:
            w.both("face", "sketch")
            b = w.binders["face"]
            h0 = digest(b)
            rec = AS.freeze(b)
            chk(P, rec["state"] == "frozen" and rec["skeleton_changed"] is False, "frozen: %s" % rec)
            for L in (25.0, 40.0):
                w.set_length(L)
                chk(P, digest(b) == h0, "frozen shape changed at L=%g" % L)
                d = AS.describe_subscription(b)
                chk(P, d["state"] == "frozen" and d["skeleton_changed"] is True, "frozen status at L=%g: %s" % (L, d))
            follows(P, w, "sketch", "other subscriber follows", L=40.0)
            rec = AS.unfreeze(b)
            chk(P, rec["state"] == "following", "unfrozen: %s" % rec["state"])
            follows(P, w, "face", "unfrozen", L=40.0)
            chk(P, digest(b) != h0, "unfreeze left the old shape")
        return P
    fn.__name__ = "freeze_edit_unfreeze_%s" % ("xdoc" if xdoc else "same")
    return fn


case(_freeze_case(False))
case(_freeze_case(True))


@case
def freeze_survives_save_reopen():
    P = []
    with world(True) as w:
        w.both("face")
        b = w.binders["face"]
        AS.freeze(b)
        h0 = digest(b)
        w.save()
        d = w.dir
        for n in (w.doc.Name, w.sdoc.Name):
            App.closeDocument(n)
        doc = App.openDocument(os.path.join(d, "subscriber.FCStd"))
        b2 = doc.getObject("S_face")
        chk(P, b2 is not None and digest(b2) == h0 and K.same(K.observe_shape(b2.Shape, "face"), K.truth("face")),
            "frozen shape changed across save and reopen")
        rec = AS.describe_subscription(b2)
        chk(P, rec["state"] == "frozen" and rec["skeleton_changed"] is False, "reopened status %s" % rec)
        sk = App.getDocument("skeleton")
        sk.getObject("Pad").Length = 30.0
        sk.recompute()
        doc.recompute()
        chk(P, digest(b2) == h0 and AS.describe_subscription(b2)["skeleton_changed"] is True,
            "frozen after reopen: %s" % AS.describe_subscription(b2))
        AS.unfreeze(b2)
        chk(P, K.same(K.observe_shape(b2.Shape, "face"), K.truth("face", L=30.0)), "unfreeze after reopen")
        for n in ("subscriber", "skeleton"):
            if n in App.listDocuments():
                App.closeDocument(n)
    return P


# ------------------------------------------------------------------ documents

@case
def skeleton_closed_and_reopened():
    P = []
    with world(True) as w:
        w.both("face", "sketch", "plane")
        w.save()
        path = w.sdoc.FileName
        h0 = {k: digest(b) for k, b in w.binders.items()}
        App.closeDocument(w.sdoc.Name)
        for k, b in w.binders.items():
            d = AS.describe_subscription(b)
            chk(P, d["state"] == "unloaded", "%s: state %s with the skeleton closed" % (k, d["state"]))
            chk(P, digest(b) == h0[k], "%s: shape changed when the skeleton was closed" % k)
        w.doc.recompute()
        for k, b in w.binders.items():
            chk(P, digest(b) == h0[k] and state(b) == "unloaded", "%s: dropped its geometry on a recompute (%s)" % (k, state(b)))
        sd = App.openDocument(path)
        w.sdoc = sd
        w.body = sd.getObject("Skel")
        sd.recompute()
        w.doc.recompute()
        for k in ("face", "sketch", "plane"):
            follows(P, w, k, "reopened")
        sd.getObject("Pad").Length = 25.0
        sd.recompute()
        w.doc.recompute()
        for k in ("face", "sketch", "plane"):
            follows(P, w, k, "reopened, edited", L=25.0)
    return P


@case
def save_and_reopen_keeps_publication_and_states():
    P = []
    with world(True) as w:
        w.both(*K.KEYS)
        AS.freeze(w.binders["edge"])
        AS.unpublish(w.body, "point")
        w.recompute()
        w.save()
        before = _states(w.doc)
        pub_before = [(i["item"], i["published"]) for i in AS.status(w.body)["items"]]
        d = w.dir
        for n in (w.doc.Name, w.sdoc.Name):
            App.closeDocument(n)
        doc = App.openDocument(os.path.join(d, "subscriber.FCStd"))
        after = _states(doc)
        ln = doc.getObject("S_line")
        chk(P, not AS._empty(ln.Shape) or after["S_line"][0] != "following", "a datum line with no geometry is reported following")
        chk(P, {k: v for k, v in before.items() if k != "S_line"} == {k: v for k, v in after.items() if k != "S_line"},
            "states changed across save and reopen: %s vs %s" % (before, after))
        sk = App.getDocument("skeleton")
        sk.recompute()                       # the core restores a datum line with a null shape; a plain recompute must rebuild it
        doc.recompute()
        chk(P, K.same(K.observe_shape(ln.Shape, "line"), K.truth("line")) and AS.describe_subscription(ln)["state"] == "following",
            "the published datum line after a plain recompute: %s" % AS.describe_subscription(ln)["state"])
        chk(P, _states(doc) == before, "states after reopen and a plain recompute: %s vs %s" % (_states(doc), before))
        pub_after = [(i["item"], i["published"]) for i in AS.status(sk.getObject("Skel"))["items"]]
        chk(P, pub_before == pub_after, "publication changed: %s vs %s" % (pub_before, pub_after))
        sk.getObject("Pad").Length = 25.0
        sk.recompute()
        doc.recompute()
        for k in ("face", "vertex", "sketch", "plane"):
            b = doc.getObject("S_" + k)
            chk(P, K.same(K.observe_shape(b.Shape, k), K.truth(k, L=25.0)), "%s after reopen and edit" % k)
        chk(P, AS.describe_subscription(doc.getObject("S_edge"))["state"] == "frozen", "frozen lost")
        chk(P, AS.describe_subscription(doc.getObject("S_point"))["state"] == "broken", "withdrawn item resolves again")
        for n in ("subscriber", "skeleton"):
            if n in App.listDocuments():
                App.closeDocument(n)
    return P


def _states(doc):
    return {r["object"]: (r["state"], r["item"], r["frozen"]) for r in AS.status(doc)["subscriptions"]}


def _xml(path):
    with zipfile.ZipFile(path) as z:
        return z.read("Document.xml").decode("utf-8", "replace")


@case
def links_are_relative_and_survive_a_move():
    P = []
    with world(True) as w:
        w.both("face", "sketch")
        w.save()
        old = w.dir
        for f in ("subscriber.FCStd", "skeleton.FCStd"):
            x = _xml(os.path.join(old, f))
            for needle in (old, "/private", "/Users", "/tmp", "/var/"):
                chk(P, needle not in x, "%s stores a machine path (%s)" % (f, needle))
        x = _xml(os.path.join(old, "subscriber.FCStd"))
        chk(P, 'file="skeleton.FCStd"' in x, "the cross-document link is not stored relative")
        new = os.path.join(K.tmpdir(), "moved")
        for n in (w.doc.Name, w.sdoc.Name):
            App.closeDocument(n)
        shutil.copytree(old, new)
        shutil.rmtree(old)
        doc = App.openDocument(os.path.join(new, "subscriber.FCStd"))
        b = doc.getObject("S_face")
        rec = AS.describe_subscription(b)
        chk(P, rec["state"] == "following", "state after moving the pair: %s" % rec["state"])
        sk = App.getDocument("skeleton")
        sk.getObject("Pad").Length = 25.0
        sk.recompute()
        doc.recompute()
        chk(P, K.same(K.observe_shape(b.Shape, "face"), K.truth("face", L=25.0)), "does not follow after the move")
        for n in ("subscriber", "skeleton"):
            if n in App.listDocuments():
                App.closeDocument(n)
        shutil.rmtree(os.path.dirname(new), ignore_errors=True)
    return P


_VOLATILE = re.compile(r'(LastModifiedDate|CreationDate|Uid|Id|Date|LastModifiedBy|CreatedBy)="[^"]*"')


_BREP_FLAGS = re.compile(rb"^[01]{7}$", re.M)


def saved_fingerprint(doc):
    """Content of a saved copy with the time stamps and uids left out and, in the BRep files, OpenCASCADE's per-shape
    cache flags (free, modified, checked, ...) left out: reading a shape sets them, they say nothing about geometry."""
    d = tempfile.mkdtemp(prefix="skelcopy_")
    try:
        p = os.path.join(d, "copy.FCStd")
        doc.saveCopy(p)
        with zipfile.ZipFile(p) as z:
            out = {}
            for n in sorted(z.namelist()):
                raw = z.read(n)
                if n.endswith(".xml"):
                    out[n] = _VOLATILE.sub("", raw.decode("utf-8", "replace"))
                elif n.endswith(".brp"):
                    out[n] = _BREP_FLAGS.sub(b"", raw)
                else:
                    out[n] = raw
            return out
    finally:
        shutil.rmtree(d, ignore_errors=True)


@case
def subscribing_leaves_the_skeleton_alone():
    P = []
    with world(True) as w:
        w.publish("face")
        w.publish("sketch")
        w.sdoc.recompute()
        sig0, fp0, h0 = AS.signature(w.sdoc), saved_fingerprint(w.sdoc), AS.shape_digest(w.body.Shape)
        sig_other = AS.signature(w.sdoc)
        chk(P, sig0 == sig_other, "the signature is not stable")
        w.subscribe("face")
        w.subscribe("sketch")
        w.recompute()
        AS.freeze(w.binders["face"])
        AS.unfreeze(w.binders["face"])
        sig1, fp1 = AS.signature(w.sdoc), saved_fingerprint(w.sdoc)
        chk(P, sig1 == sig0, "subscribing changed the skeleton document's objects or properties: %s"
            % sorted(k for k in set(sig0) | set(sig1) if sig0.get(k) != sig1.get(k)))
        chk(P, fp0 == fp1, "subscribing changed the saved skeleton: %s" % sorted(k for k in fp0 if fp0.get(k) != fp1.get(k)))
        chk(P, AS.shape_digest(w.body.Shape) == h0, "the skeleton's shape changed")
        # publishing modifies only the publication: existing objects are unchanged, the new ones are items
        before = AS.signature(w.sdoc)
        w.publish("edge")
        after = AS.signature(w.sdoc)
        new = sorted(set(after) - set(before))
        changed = sorted(k for k in before if before[k] != after.get(k))
        chk(P, len(new) == 1 and after[new[0]]["type"] == "Part::FeaturePython", "publish added %s" % new)
        chk(P, changed == [], "publish changed existing objects %s" % changed)
        base = AS.shape_digest(w.body.Shape)
        chk(P, base == h0, "publish changed the skeleton shape")
    return P


@case
def mutating_calls_are_one_undo_transaction():
    P = []
    for xdoc in (False, True):
        with world(xdoc) as w:
            tgt, sub = K.target_of(w.body, "face")
            s0 = AS.signature(w.sdoc)
            AS.publish(w.body, "face", tgt, sub)
            s1 = AS.signature(w.sdoc)
            chk(P, s1 != s0, "publish changed nothing")
            w.sdoc.undo()
            chk(P, AS.signature(w.sdoc) == s0, "undo of publish did not restore the signature (xdoc=%s)" % xdoc)
            w.sdoc.redo()
            chk(P, AS.signature(w.sdoc) == s1, "redo of publish (xdoc=%s)" % xdoc)
            d0 = AS.signature(w.doc)
            b = w.subscribe("face")
            d1 = AS.signature(w.doc)
            w.doc.undo()
            chk(P, AS.signature(w.doc) == d0, "undo of subscribe (xdoc=%s)" % xdoc)
            w.doc.redo()
            b = w.doc.getObject("S_face")
            chk(P, AS.signature(w.doc) == d1, "redo of subscribe (xdoc=%s)" % xdoc)
            for call, label in ((AS.freeze, "freeze"), (AS.unfreeze, "unfreeze")):
                before = AS.signature(w.doc)
                call(b)
                after = AS.signature(w.doc)
                chk(P, before != after, "%s changed nothing" % label)
                w.doc.undo()
                chk(P, AS.signature(w.doc) == before, "undo of %s (xdoc=%s)" % (label, xdoc))
                w.doc.redo()
            before = AS.signature(w.sdoc)
            AS.rename(w.body, "face", "deck")
            w.sdoc.undo()
            chk(P, AS.signature(w.sdoc) == before, "undo of rename (xdoc=%s)" % xdoc)
            AS.unpublish(w.body, "face")
            w.sdoc.undo()
            chk(P, AS.signature(w.sdoc) == before, "undo of unpublish (xdoc=%s)" % xdoc)
    return P


@case
def refusals_and_messages():
    P = []
    with world(True) as w:
        tgt, sub = K.target_of(w.body, "face")
        AS.publish(w.body, "face", tgt, sub)
        for what, fn in (
                ("duplicate name", lambda: AS.publish(w.body, "face", tgt, sub)),
                ("empty name", lambda: AS.publish(w.body, " ", tgt, sub)),
                ("bad element name", lambda: AS.publish(w.body, "x", tgt, "Foo1")),
                ("unknown item", lambda: AS.find_item(w.body, "nope")),
                ("subscribing to a body", lambda: AS.subscribe(w.holder, w.body)),
                ("freeze of a non-subscription", lambda: AS.freeze(w.holder)),
                ("publish on a sketch", lambda: AS.publish(w.holder.Origin, "o", tgt, sub))):
            try:
                fn()
                P.append("%s was accepted" % what)
            except AS.SkeletonError:
                pass
        n0 = len(w.sdoc.Objects)
        try:
            AS.publish(w.body, "bad", tgt, "Face99")
            P.append("a face that does not exist was published")
        except AS.SkeletonError:
            pass
        chk(P, len(w.sdoc.Objects) == n0, "a refused publish left objects behind")
        sub_doc = App.newDocument("Unsaved")
        try:
            h = sub_doc.addObject("PartDesign::Body", "H")
            try:
                AS.subscribe(h, AS.find_item(w.body, "face"))
                P.append("subscribing from an unsaved document was accepted")
            except AS.SkeletonError as exc:
                chk(P, "save" in str(exc), "the message does not say to save: %s" % exc)
            chk(P, len(sub_doc.Objects) == 1 + len([o for o in sub_doc.Objects if o.TypeId == "App::Origin"]) + 0
                or all(o.TypeId != "PartDesign::SubShapeBinderPython" for o in sub_doc.Objects), "refused subscribe left a binder")
        finally:
            App.closeDocument(sub_doc.Name)
    return P


@case
def json_status_names_item_subscriber_reason_and_entities():
    P = []
    with world(True) as w:
        w.both("face", "vertex", "sketch")
        AS.freeze(w.binders["sketch"])
        K.insert_vertical_fillet(w.sdoc)
        w.recompute()
        st = AS.status()
        txt = json.dumps(st)
        back = json.loads(txt)
        chk(P, back["schema"] == AS.SCHEMA, "schema")
        subs = {s["object"]: s for s in back["subscriptions"]}
        chk(P, {"S_face", "S_vertex", "S_sketch"} <= set(subs), "subscriptions listed: %s" % sorted(subs))
        v = subs["S_vertex"]
        chk(P, v["state"] == "broken" and v["item"] == "vertex" and v["subscriber"] and "vertex" in (v["reason"] or "")
            and "no longer exists" in v["reason"], "broken record: %s" % v)
        chk(P, subs["S_sketch"]["state"] == "frozen", "frozen record: %s" % subs["S_sketch"])
        for s in subs.values():
            chk(P, bool(s["entity"]) and bool(s["item_entity"]), "missing F1 entity ids: %s" % s)
        from ReplaceComponent import _entity
        chk(P, subs["S_vertex"]["entity"] == _entity(w.binders["vertex"]), "subscriber entity is not the F1 id")
        pub = [p for p in back["publications"] if p["skeleton"] == "Skel"][0]
        chk(P, pub["entity"] == _entity(w.body), "skeleton entity")
        items = {i["item"]: i for i in pub["items"]}
        chk(P, items["vertex"]["status"] != "ok" and items["face"]["subscribers"], "item records: %s" % items["vertex"])
        chk(P, items["vertex"]["subscribers"][0]["subscriber"] == v["subscriber"], "item lists its subscribers")
        one = AS.status(w.body)
        chk(P, [i["item"] for i in one["items"]] == [i["item"] for i in pub["items"]], "status of a skeleton")
    return P


# ------------------------------------------------------------------ assembly

def _asm_world(parts):
    """Skeleton "Skel" and an assembly in one document; parts = {name: [item keys]} each a Body in the assembly."""
    w = K.World(False)
    asm = w.doc.addObject("Assembly::AssemblyObject", "Assembly")
    asm.newObject("Assembly::JointGroup", "Joints")
    keys = sorted({k for ks in parts.values() for k in ks})
    for k in keys:
        w.publish(k)
    w.asm = asm
    w.parts = {}
    for name, ks in parts.items():
        body = asm.newObject("PartDesign::Body", name)
        w.parts[name] = body
        for k in ks:
            item = AS.find_item(w.body, k)
            rec = AS.subscribe(body, item, object_name="%s_%s" % (name, k))
            w.binders["%s_%s" % (name, k)] = w.doc.getObject(rec["object"])
    w.doc.recompute()
    return w


@case
def three_items_feed_three_parts_in_an_assembly():
    P = []
    plan = {"PartA": ["face", "plane"], "PartB": ["sketch", "line"], "PartC": ["edge", "point", "face"]}
    w = _asm_world(plan)
    try:
        for L in (10.0, 25.0):
            if L != 10.0:
                w.set_length(L)
            w.asm.solve()
            w.doc.recompute()
            for part, ks in plan.items():
                for k in ks:
                    follows(P, _Alias(w, part + "_" + k), k, "%s L=%g" % (part, L), L=L)
        chk(P, len(AS.subscribers_of(AS.find_item(w.body, "face"))) == 2, "the face item has two subscribers")
    finally:
        w.close()
    return P


class _Alias:
    def __init__(self, w, name):
        self.binders = {}
        self._w = w
        self.binders[name.split("_", 1)[1]] = w.binders[name]


def _joint_world():
    w = K.World(False)
    asm = w.doc.addObject("Assembly::AssemblyObject", "Assembly")
    jg = asm.newObject("Assembly::JointGroup", "Joints")
    comp = asm.newObject("PartDesign::Body", "Comp")
    mover = asm.newObject("Part::Box", "Mover")
    mover.Length, mover.Width, mover.Height = 10.0, 10.0, 4.0
    w.publish("face")
    b = w.subscribe("face", holder=comp)
    w.doc.recompute()
    s = comp.newObject("Sketcher::SketchObject", "S2")
    s.AttachmentSupport = [(b, ["Face1"])]
    s.MapMode = "FlatFace"
    K.fill_rect(s, 10.0, 10.0)
    pad = comp.newObject("PartDesign::Pad", "P2")
    pad.Profile = s
    pad.Length = 6.0
    w.doc.recompute()
    gj = jg.newObject("App::FeaturePython", "Gr")
    JointObject.GroundedJoint(gj, comp)
    j = jg.newObject("App::FeaturePython", "Fx")
    JointObject.Joint(j, JointObject.JointTypes.index("Fixed"))
    sh = pad.Shape
    top = [i + 1 for i, f in enumerate(sh.Faces) if abs(f.CenterOfMass.z - (K.L0 + 6.0)) < 1e-9 and abs(f.normalAt(0, 0).z) > 0.99][0]
    vt = K.pick_vertex(sh, (0, 0, K.L0 + 6.0))
    j.Reference1 = (comp, ["P2.Face%d" % top, "P2.%s" % vt])
    msh = mover.Shape
    bottom = [i + 1 for i, f in enumerate(msh.Faces) if abs(f.CenterOfMass.z) < 1e-9 and abs(f.normalAt(0, 0).z) > 0.99][0]
    j.Reference2 = (mover, ["Face%d" % bottom, K.pick_vertex(msh, (0, 0, 0))])
    j.Proxy.updateJCSPlacements(j)
    mover.Placement = App.Placement(V(100, 100, 100), App.Rotation())
    w.asm, w.comp, w.mover, w.joint = asm, comp, mover, j
    return w


@case
def joint_on_subscribed_geometry_follows_the_skeleton():
    P = []
    w = _joint_world()
    try:
        for L in (10.0, 25.0, 3.0):
            w.set_length(L)
            w.doc.recompute()
            w.asm.solve()
            w.doc.recompute()
            bb = w.mover.Shape.BoundBox
            pl = w.mover.Placement
            chk(P, abs(pl.Base.z - (L + 6.0)) < 1e-6 and abs(pl.Base.x) < 1e-6 and abs(pl.Base.y) < 1e-6,
                "mover at L=%g: %s, truth (0, 0, %g)" % (L, pl.Base, L + 6.0))
            chk(P, pl.Rotation.Angle < 1e-9, "mover rotated by %g rad" % pl.Rotation.Angle)
            chk(P, abs(bb.ZMin - (L + 6.0)) < 1e-6 and abs(bb.ZMax - (L + 10.0)) < 1e-6, "mover z range %s..%s" % (bb.ZMin, bb.ZMax))
            chk(P, abs(w.joint.Placement1.Base.z - (L + 6.0)) < 1e-6, "joint frame z %s, truth %g" % (w.joint.Placement1.Base.z, L + 6.0))
            chk(P, state(w.binders["face"]) == "following", "state %s" % state(w.binders["face"]))
    finally:
        w.close()
    return P


@case
def interference_on_subscribed_geometry():
    P = []
    w = _joint_world()
    try:
        blocker = w.asm.newObject("Part::Box", "Blocker")
        blocker.Length, blocker.Width, blocker.Height = 10.0, 10.0, 10.0
        blocker.Placement = App.Placement(V(0, 0, 20), App.Rotation())
        w.doc.getObject("Mover").Visibility = False
        w.doc.getObject("Fx").Suppressed = True
        w.doc.recompute()

        def overlaps(L):
            w.set_length(L)
            w.doc.recompute()
            rep = Interference.check(w.asm)
            return [p for p in rep["pairs"] if p["class"] == "interfering" and {p["a"]["label"], p["b"]["label"]} == {"Comp", "Blocker"}]
        chk(P, overlaps(10.0) == [], "the pad on the subscribed face at L=10 (z 10..16) is clear of the blocker (z 20..30)")
        hits = overlaps(18.0)
        chk(P, len(hits) == 1, "L=18: pad z 18..24 overlaps the blocker z 20..24: %s" % hits)
        if hits:
            vol = hits[0]["volume_mm3"]
            chk(P, vol is not None and abs(vol - 400.0) < 1e-6, "common volume %s, truth 400" % vol)
        chk(P, overlaps(10.0) == [], "back at L=10 the interference is gone")
    finally:
        w.close()
    return P


@case
def configuration_suppresses_a_subscriber():
    P = []
    w = _joint_world()
    try:
        w.doc.getObject("Fx").Suppressed = True
        AC.create(w.asm, "NoComp", suppress=["Comp"])
        w.doc.recompute()
        sig0 = AC.signature(w.doc, w.asm)
        AC.activate(w.asm, "NoComp")
        chk(P, w.comp not in w.asm.Group, "the suppressed subscriber part is still in the assembly")
        chk(P, state(w.binders["face"]) == "following", "state of the suppressed subscriber: %s" % state(w.binders["face"]))
        w.set_length(25.0)
        chk(P, K.same(sig(w.binders["face"], "face"), K.truth("face", L=25.0)), "a suppressed subscriber stopped following")
        rep = Interference.check(w.asm)
        chk(P, "Comp" not in json.dumps(rep["pairs"]), "the suppressed subscriber is checked for interference")
        AC.activate(w.asm, None)
        chk(P, w.comp in w.asm.Group, "restore did not bring the subscriber part back")
        chk(P, state(w.binders["face"]) == "following" and K.same(sig(w.binders["face"], "face"), K.truth("face", L=25.0)),
            "the subscriber after restore")
        w.set_length(10.0)
        chk(P, K.same(sig(w.binders["face"], "face"), K.truth("face")), "does not follow after restore")
        del sig0
    finally:
        w.close()
    return P


# ------------------------------------------------------------------ repair 1: stale, aliasing, lost proxies, same-name documents

@case
def item_follows_changes_a_fingerprint_would_miss():
    P = []
    with world(False) as w:
        w.both("face", "vertex", "edge")
        for L in (10.0000004, 10.0000009, 10.4):
            w.set_length(L)
            for k in ("face", "vertex", "edge"):
                follows(P, w, k, "L=%r" % L, L=L)
        # a centred 40 x 20 plate turned into 20 x 40: same area, same centroid, different geometry
        doc = App.newDocument("Turned")
        body = doc.addObject("PartDesign::Body", "Skel")
        sk = body.newObject("Sketcher::SketchObject", "Sketch")
        sk.AttachmentSupport = (body.Origin.OriginFeatures[3], [""])
        sk.MapMode = "FlatFace"
        pts = [V(-20, -10, 0), V(20, -10, 0), V(20, 10, 0), V(-20, 10, 0)]
        for i in range(4):
            sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
        for i in range(4):
            sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
        for g in (0, 2):
            sk.addConstraint(Sketcher.Constraint("Horizontal", g))
        for g in (1, 3):
            sk.addConstraint(Sketcher.Constraint("Vertical", g))
        sk.addConstraint(Sketcher.Constraint("Symmetric", 0, 1, 1, 2, -1, 1))
        sk.renameConstraint(sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 40.0)), "Width")
        sk.renameConstraint(sk.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, 20.0)), "Height")
        pad = body.newObject("PartDesign::Pad", "Pad")
        pad.Profile = sk
        pad.Length = 10
        doc.recompute()
        AS.publish(body, "top", body, K.pick_face(body.Shape, (0, 0, 1), (0, 0, 10)))
        hold = doc.addObject("PartDesign::Body", "Sub")
        b = doc.getObject(AS.subscribe(hold, AS.find_item(body, "top"))["object"])
        sk.setDatum("Width", App.Units.Quantity("20 mm"))
        sk.setDatum("Height", App.Units.Quantity("40 mm"))
        doc.recompute()
        bb = b.Shape.BoundBox
        chk(P, abs(bb.XLength - 20) < 1e-9 and abs(bb.YLength - 40) < 1e-9, "a turned plate: the subscriber holds %s" % bb)
        App.closeDocument("Turned")
    return P


@case
def subscriber_is_not_following_while_upstream_is_stale_or_broken():
    P = []
    for xdoc in (False, True):
        with world(xdoc) as w:
            w.both("face", "sketch")
            b, sb = w.binders["face"], w.binders["sketch"]
            h0 = digest(b)
            w.sdoc.getObject("Sketch").touch()              # edited but not recomputed: upstream of both items
            for k in ("face", "sketch"):
                chk(P, state(w.binders[k]) == "stale", "xdoc=%s %s: %s while its source is Touched" % (xdoc, k, state(w.binders[k])))
            w.recompute()
            follows(P, w, "face", "recomputed xdoc=%s" % xdoc)
            w.sdoc.getObject("Pad").Length = 0.0            # the feature the face belongs to fails
            w.recompute()
            chk(P, state(b) == "broken", "xdoc=%s: face with a failed Pad: %s" % (xdoc, state(b)))
            chk(P, digest(b) == h0, "the face lost its last shape")
            chk(P, "in error" in (AS.describe_subscription(b)["reason"] or ""), "reason: %s" % AS.describe_subscription(b)["reason"])
    return P


@case
def lost_proxies_are_reattached_before_a_recompute():
    P = []
    for xdoc in (False, True):
        with world(xdoc) as w:
            w.both("face", "sketch")
            for d in {w.sdoc.Name: w.sdoc, w.doc.Name: w.doc}.values():
                for o in d.Objects:
                    if AS.is_item(o) or AS.is_subscription(o):
                        o.Proxy = None                          # what a re-save by stable FreeCAD leaves behind
            w.sdoc.getObject("Pad").Length = 25.0
            w.recompute()
            follows(P, w, "face", "no proxies xdoc=%s" % xdoc, L=25.0)
            chk(P, isinstance(AS.find_item(w.body, "face").Proxy, AS.PublishedItem), "the item proxy was not re-attached")
    return P


@case
def break_reaches_features_on_a_plain_recompute():
    P = []
    for xdoc in (False, True):
        with world(xdoc) as w:
            w.both("face")
            b = w.binders["face"]
            s = _slab_sketch(w.holder, (w.holder.Origin.OriginFeatures[3], [""]), "UpSketch")
            pad = w.holder.newObject("PartDesign::Pad", "UpPad")
            pad.Profile = s
            pad.Type = "UpToFace"
            pad.UpToFace = (b, ["Face1"])
            w.recompute()
            K.insert_vertical_fillet(w.sdoc)
            w.recompute()                                       # no refresh()
            chk(P, "Up-to-date" not in b.State, "xdoc=%s: the broken subscriber is Up-to-date after a plain recompute" % xdoc)
            chk(P, "Up-to-date" not in pad.State, "xdoc=%s: the feature built on it is Up-to-date (%s)" % (xdoc, pad.State))
    return P


@case
def a_subscriber_without_geometry_is_not_following():
    P = []
    with world(False) as w:
        w.both("face")
        b = w.binders["face"]
        b.Shape = Part.Shape()                     # what a failed restore of a stored shape leaves
        chk(P, state(b) == "stale", "an empty subscriber is %s" % state(b))
        w.recompute()
        follows(P, w, "face", "after a recompute")
    return P


@case
def unrelated_document_with_the_same_name_does_not_hide_unloaded():
    P = []
    with world(True) as w:
        w.both("face")
        w.save()
        App.closeDocument(w.sdoc.Name)
        other = os.path.join(K.tmpdir(), "elsewhere")
        os.makedirs(other)
        d2 = App.newDocument("skeleton")
        d2.saveAs(os.path.join(other, "skeleton.FCStd"))
        b = w.binders["face"]
        chk(P, state(b) == "unloaded", "an unrelated document called skeleton changes the state to %s" % state(b))
        App.closeDocument(d2.Name)
        shutil.rmtree(os.path.dirname(other), ignore_errors=True)
    return P


@case
def reopened_skeleton_is_stale_until_the_subscriber_recomputes():
    P = []
    with world(True) as w:
        w.both("face", "sketch")
        w.save()
        path = w.sdoc.FileName
        App.closeDocument(w.sdoc.Name)
        sd = App.openDocument(path)
        sd.getObject("Pad").Length = 22.0
        sd.recompute()
        sd.save()
        w.sdoc, w.body = sd, sd.getObject("Skel")
        chk(P, state(w.binders["face"]) == "stale", "face after the skeleton changed on reopen, before a recompute: %s" % state(w.binders["face"]))
        w.doc.recompute()
        follows(P, w, "face", "after the subscriber recomputed", L=22.0)
    return P


# ------------------------------------------------------------------ repair 2: identity, Save As, restore, cycles, cost, containers

class _Imposter:
    """An unrelated feature class whose object happens to share the property names of a published item."""
    def __init__(self, obj):
        obj.Proxy = self

    def execute(self, obj):
        obj.Status = "mine"


@case
def an_unrelated_feature_with_the_same_property_names_is_left_alone():
    P = []
    with world(False) as w:
        w.both("face")
        for cls in ("own proxy", "no proxy"):
            o = w.sdoc.addObject("Part::FeaturePython", "Imposter_" + cls[0])
            o.addProperty("App::PropertyXLinkSub", "Source", "X")
            for n in ("ItemName", "Publication", "Kind", "Status", "SkeletonName"):
                o.addProperty("App::PropertyString", n, "X")
            o.addProperty("App::PropertyBool", "Published", "X")
            o.addProperty("App::PropertyInteger", "SkeletonId", "X")
            o.Published = True
            o.Status = "mine"
            if cls == "own proxy":
                _Imposter(o)
        w.sdoc.getObject("Pad").touch()
        w.recompute()
        a, b = w.sdoc.getObject("Imposter_o"), w.sdoc.getObject("Imposter_n")
        chk(P, isinstance(a.Proxy, _Imposter), "the proxy of an unrelated feature was replaced by %s" % type(a.Proxy).__name__)
        chk(P, "Invalid" not in a.State and a.Status == "mine", "the unrelated feature went %s / %s" % (a.State, a.Status))
        chk(P, getattr(b, "Proxy", None) is None, "an unrelated feature without a proxy was given one")
        chk(P, not AS.is_item(a) and not AS.is_item(b), "an unrelated feature is taken for an item")
        chk(P, [i["item"] for i in AS.status(w.body)["items"]] == ["face"], "the publication lists unrelated objects")
    return P


@case
def save_as_does_not_turn_a_deleted_item_into_unloaded():
    P = []
    for which in ("subscriber", "skeleton"):
        with world(True) as w:
            w.both("face")
            w.save()
            b = w.binders["face"]
            d2 = K.tmpdir()
            if which == "subscriber":
                w.doc.saveAs(os.path.join(d2, "moved_sub.FCStd"))
            else:
                w.sdoc.saveAs(os.path.join(d2, "renamed_skel.FCStd"))
            w.recompute()
            chk(P, state(b) == "following", "%s saved as: %s" % (which, state(b)))
            w.sdoc.removeObject(AS.find_item(w.body, "face").Name)
            w.recompute()
            chk(P, state(b) == "broken", "%s saved as, item deleted, skeleton open: %s" % (which, state(b)))
            shutil.rmtree(d2, ignore_errors=True)
    return P


@case
def skeleton_edited_while_the_subscriber_was_closed_follows_on_a_plain_recompute():
    P = []
    with world(True) as w:
        w.both("face", "sketch", "plane")
        w.save()
        d = w.dir
        for n in (w.doc.Name, w.sdoc.Name):
            App.closeDocument(n)
        sk = App.openDocument(os.path.join(d, "skeleton.FCStd"))
        sk.getObject("Pad").Length = 22.0
        sk.recompute()
        sk.save()
        App.closeDocument(sk.Name)
        doc = App.openDocument(os.path.join(d, "subscriber.FCStd"))
        doc.recompute()                                           # a plain recompute, no refresh()
        for k in ("face", "sketch", "plane"):
            b = doc.getObject("S_" + k)
            chk(P, AS.describe_subscription(b)["state"] == "following", "%s after a plain recompute: %s" % (k, AS.describe_subscription(b)))
            chk(P, K.same(K.observe_shape(b.Shape, k), K.truth(k, L=22.0)), "%s holds %s" % (k, K.observe_shape(b.Shape, k) if not AS._empty(b.Shape) else None))
        for n in list(App.listDocuments()):
            App.closeDocument(n)
    return P


@case
def subscribing_from_an_upstream_holder_is_refused():
    P = []
    with world(False) as w:
        w.publish("face")
        try:
            AS.subscribe(w.body, AS.find_item(w.body, "face"))
            P.append("a skeleton subscribed to its own item")
        except AS.SkeletonError as exc:
            chk(P, "cycle" in str(exc), "message: %s" % exc)
        b2 = K.build_skeleton(w.sdoc, "SkelB")
        AS.publish(b2, "face", *K.target_of(b2, "face"))
        AS.subscribe(b2, AS.find_item(w.body, "face"), object_name="B_from_A")
        w.sdoc.recompute()
        try:
            AS.subscribe(w.body, AS.find_item(b2, "face"), object_name="A_from_B")
            P.append("a mutual subscription (a cycle) was accepted")
        except AS.SkeletonError:
            pass
        chk(P, w.sdoc.getObject("A_from_B") is None, "a refused subscribe left a binder")
    return P


@case
def documents_without_skeleton_objects_cost_the_observer_nothing():
    P = []
    calls = []
    real = AS.reattach
    AS.reattach = lambda doc: calls.append(doc.Name) or real(doc)
    plain = App.newDocument("PlainDoc")
    try:
        plain.addObject("Part::Box", "Box")
        for _ in range(3):
            plain.recompute()
        chk(P, calls == [], "the observer prepared a document with no skeleton objects: %s" % calls)
        chk(P, AS._observer.count.get("PlainDoc", 0) == 0, "candidate count %s" % AS._observer.count.get("PlainDoc"))
        with world(False) as w:
            w.both("face")
            del calls[:]
            w.recompute()
            chk(P, calls != [], "the observer skipped a document that holds items")
    finally:
        AS.reattach = real
        App.closeDocument("PlainDoc")
    return P


@case
def skeleton_identity_label_and_containers():
    P = []
    with world(False) as w:
        w.both("face")
        item, b = AS.find_item(w.body, "face"), w.binders["face"]
        w.body.Label = "Frame"
        d = AS.describe_subscription(b)
        chk(P, d["skeleton"] == "Frame", "the status shows the skeleton as %r after a relabel" % d["skeleton"])
        # break the item; the container that merely holds the subscriber must not be left Touched
        folder = w.sdoc.addObject("App::DocumentObjectGroup", "Folder")
        folder.addObject(w.holder)
        lnk = w.sdoc.addObject("App::Link", "Lnk")
        lnk.LinkedObject = w.holder
        other = w.holder.newObject("Sketcher::SketchObject", "Other")
        w.recompute()
        K.insert_vertical_fillet(w.sdoc)
        w.recompute()
        chk(P, state(b) == "broken", "state %s" % state(b))
        for o in (w.holder, folder, lnk, other):
            chk(P, "Touched" not in o.State, "%s was left Touched by a broken subscriber: %s" % (o.Name, o.State))
        # the skeleton is deleted and a new body takes its name: the orphan item does not belong to it
        name = w.body.Name
        w.sdoc.removeObject(name)
        nb = w.sdoc.addObject("PartDesign::Body", name)
        chk(P, nb.Name == name and AS.items(nb, include_withdrawn=True) == [], "a new body that took the name inherited %s" % AS.items(nb, include_withdrawn=True))
        chk(P, AS.skeleton_of(item) is None, "the orphan item is bound to the new body")
    return P


@case
def unloaded_datum_subscribers_keep_their_geometry():
    P = []
    with world(True) as w:
        w.both("line", "plane", "point", "face")
        w.save()
        d = w.dir
        for n in (w.doc.Name, w.sdoc.Name):
            App.closeDocument(n)
        doc = App.openDocument(os.path.join(d, "subscriber.FCStd"))
        App.closeDocument("skeleton")
        for k in ("line", "plane", "point", "face"):
            b = doc.getObject("S_" + k)
            rec = AS.describe_subscription(b)
            chk(P, rec["state"] == "unloaded", "%s: %s" % (k, rec["state"]))
            chk(P, not AS._empty(b.Shape) and K.same(K.observe_shape(b.Shape, k), K.truth(k)),
                "%s unloaded holds %s" % (k, None if AS._empty(b.Shape) else K.observe_shape(b.Shape, k)))
        for n in list(App.listDocuments()):
            App.closeDocument(n)
    return P


# ------------------------------------------------------------------ mutants

@contextlib.contextmanager
def patched(**fns):
    saved = {k: getattr(AS, k) for k in fns}
    try:
        for k, v in fns.items():
            setattr(AS, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(AS, k, v)


def _m_raw_element():
    """A subscriber that stores the raw element of the skeleton (the stock way) instead of the item."""
    def subscribe(holder, item, label=None, object_name="Subscription"):
        doc = holder.Document
        src = item.Source
        b = holder.newObject("PartDesign::SubShapeBinderPython", object_name)
        AS.Subscription(b)
        b.Subscription = json.dumps(AS._record_of(item), sort_keys=True)
        b.Support = [(src[0], (src[1][0] if src[1] else "",))]
        doc.recompute([b])
        return AS.describe_subscription(b)
    return dict(subscribe=subscribe)


def _m_retarget_first_of_type():
    real = AS.resolve_item

    def resolve(obj):
        try:
            return real(obj)
        except AS.SkeletonError:
            src = obj.Source
            sh = src[0].Shape
            el = {"face": sh.Faces, "edge": sh.Edges, "vertex": sh.Vertexes}.get(obj.Kind)
            if not el:
                raise
            obj.Status = "ok"
            return el[0].copy()
    return dict(resolve_item=resolve)


def _m_freeze_noop():
    return dict(freeze=lambda binder: AS.describe_subscription(binder))


def _m_unfreeze_noop():
    return dict(unfreeze=lambda binder: AS.describe_subscription(binder))


def _m_unloaded_is_broken():
    real = AS.classify

    def classify(binder):
        r = real(binder)
        if r["state"] == AS.UNLOADED:
            r["state"] = AS.BROKEN
            r["reason"] = "the item no longer exists"
        return r
    return dict(classify=classify)


def _m_unloaded_drops_geometry():
    real = AS.Subscription.execute

    def execute(self, obj):
        info = AS.classify(obj)
        if info["state"] == AS.UNLOADED:
            import Part as P_
            obj.Shape = P_.Shape()
            return True
        return real(self, obj)
    return dict(_proxy_execute=(AS.Subscription, "execute", execute))


def _m_rename_breaks():
    def rename(skeleton, name, new_name):
        item = AS.find_item(skeleton, name)
        new_name = AS._check_name(skeleton, new_name, own=item)
        doc = skeleton.Document
        with AS._transaction(doc, "Rename item " + name):
            src = item.Source
            doc.removeObject(item.Name)
            tgt, sub = src[0], (src[1][0] if src[1] else "")
            AS.publish(skeleton, new_name, tgt, sub)
        return {}
    return dict(rename=rename)


def _m_removed_item_resolves():
    real = AS.resolve_item
    real_problem = AS._item_problem

    def resolve(obj):
        if not obj.Published:
            obj.Published = True
            try:
                return real(obj)
            finally:
                obj.Published = False
        return real(obj)

    def problem(item):
        if not item.Published:
            item.Published = True
            try:
                return real_problem(item)
            finally:
                item.Published = False
        return real_problem(item)
    return dict(resolve_item=resolve, _item_problem=problem)


def _m_absolute_path():
    def record(item, holder_doc=None):
        d = item.Document
        return {"item": item.ItemName, "publication": item.Publication, "skeleton": AS.skeleton_of(item).Label, "doc": d.Name,
                "file": d.FileName, "same": holder_doc is None or holder_doc == d}
    return dict(_record_of=record)


def _m_empty_sub_binds_whole():
    real = AS.resolve_item

    def resolve(obj):
        try:
            return real(obj)
        except AS.SkeletonError:
            src = obj.Source
            if src and src[0] is not None and obj.Kind != "object":
                obj.Status = "ok"
                return src[0].Shape.copy()
            raise
    return dict(resolve_item=resolve)


def _m_swallow_error():
    real = AS.resolve_item

    def resolve(obj):
        try:
            return real(obj)
        except AS.SkeletonError:
            obj.Status = "ok"
            return obj.Shape.copy()
    return dict(resolve_item=resolve)


def _m_subscribe_touches_skeleton():
    real = AS.subscribe

    def subscribe(holder, item, label=None, object_name="Subscription"):
        rec = real(holder, item, label, object_name)
        AS.skeleton_of(item).Label2 = "subscribed"
        return rec
    return dict(subscribe=subscribe)


def _m_no_transactions():
    @contextlib.contextmanager
    def tx(doc, title):
        yield
    return dict(_transaction=tx)


def _m_frozen_not_reported():
    real = AS.classify

    def classify(binder):
        r = real(binder)
        if r["state"] == AS.FROZEN:
            r["skeleton_changed"] = False
        return r
    return dict(classify=classify)


def _m_broken_does_not_raise():
    real = AS.Subscription.execute

    def execute(self, obj):
        if AS.classify(obj)["state"] == AS.BROKEN:
            return False
        return real(self, obj)
    return dict(_proxy_execute=(AS.Subscription, "execute", execute))


def _m_publish_unchecked_name():
    real = AS._check_name
    return dict(_check_name=lambda skeleton, name, own=None: str(name))


def _m_status_hides_reason():
    real = AS.describe_subscription

    def describe(binder):
        r = real(binder)
        r["reason"] = None
        return r
    return dict(describe_subscription=describe)


def _m_following_ignores_frozen():
    real = AS.classify

    def classify(binder):
        r = real(binder)
        if r["state"] == AS.FROZEN:
            r["state"] = AS.FOLLOWING
        return r
    return dict(classify=classify)


def _m_weak_digest():
    def digest(shape):
        if shape is None or shape.isNull():
            return None
        import hashlib as h
        return h.sha1(repr((round(shape.Area, 6), tuple(round(c, 6) for c in shape.CenterOfMass))).encode()).hexdigest() if shape.Faces else "x"

    def execute(self, obj):
        shape = AS.resolve_item(obj)
        if digest(shape) != digest(obj.Shape):
            obj.Placement = shape.Placement
            obj.Shape = shape
        if obj.Status != "ok":
            obj.Status = "ok"
    return dict(shape_digest=digest, _proxy_item=(AS.PublishedItem, "execute", execute))


def _m_upstream_ignored():
    return dict(_upstream=lambda item: [])


def _m_no_reattach():
    return dict(reattach=lambda doc: None)


def _m_dependents_not_touched():
    return dict(_touch_dependents=lambda obj: None)


def _m_document_open_by_basename():
    def open_(binder, rec):
        return any(os.path.basename(d.FileName or "") == os.path.basename(rec.get("file", "") or "?")
                   for d in App.listDocuments().values()) or bool(rec.get("same"))
    return dict(_document_open=open_)


def _m_empty_is_following():
    return dict(_empty=lambda shape: False)


def _m_never_stale():
    real = AS.classify

    def classify(binder):
        r = real(binder)
        if r["state"] == AS.STALE:
            r["state"], r["reason"] = AS.FOLLOWING, None
        return r
    return dict(classify=classify)


def _m_is_item_by_names():
    def is_item(obj):
        return obj.TypeId == "Part::FeaturePython" and all(p in obj.PropertiesList for p in ("Source", "ItemName", "Publication", "Published", "Status"))

    def ensure(obj, cls):
        cls.attach(obj)
        return True
    return dict(is_item=is_item, _ensure_proxy=ensure)


def _m_document_by_path():
    def open_(binder, rec):
        if rec.get("same"):
            return True
        rel, here = rec.get("file"), binder.Document.FileName
        want = AS._real(os.path.join(os.path.dirname(here), rel)) if rel and here else ""
        return any(AS._real(d.FileName) == want for d in App.listDocuments().values() if d.FileName)
    return dict(_document_open=open_)


def _m_datum_not_rebuilt():
    return dict(_datum_shape=lambda target: None)


def _m_restore_touch_dropped():
    real = AS.reattach

    def reattach(doc):
        for o in doc.Objects:
            if AS.is_item(o):
                AS._ensure_proxy(o, AS.PublishedItem)
            elif AS.is_subscription(o):
                AS._ensure_proxy(o, AS.Subscription)
    return dict(reattach=reattach)


def _m_cycle_allowed():
    return dict(_refuse_cycle=lambda holder, item: None)


def _m_observer_ungated():
    return dict(_prepare=lambda doc: AS.reattach(doc))


def _m_bound_by_name():
    return dict(skeleton_of=lambda item: item.Document.getObject(item.SkeletonName))


def _m_containers_touched():
    return dict(_is_container=lambda o: False)


def _m_datum_not_recorded():
    return dict(_datum_record=lambda item: None)


MUTANTS = {
    "subscriber_stores_raw_element": _m_raw_element,
    "broken_item_retargeted_to_first_of_type": _m_retarget_first_of_type,
    "empty_sub_binds_whole_skeleton": _m_empty_sub_binds_whole,
    "item_swallows_resolve_error": _m_swallow_error,
    "freeze_still_updates": _m_freeze_noop,
    "unfreeze_does_not_update": _m_unfreeze_noop,
    "frozen_not_reported_as_moved_on": _m_frozen_not_reported,
    "frozen_reported_as_following": _m_following_ignores_frozen,
    "unloaded_treated_as_broken": _m_unloaded_is_broken,
    "unloaded_drops_geometry": _m_unloaded_drops_geometry,
    "rename_breaks_subscribers": _m_rename_breaks,
    "removed_item_keeps_resolving": _m_removed_item_resolves,
    "absolute_path_written": _m_absolute_path,
    "subscribe_modifies_skeleton": _m_subscribe_touches_skeleton,
    "mutating_calls_not_transactions": _m_no_transactions,
    "broken_subscriber_does_not_raise": _m_broken_does_not_raise,
    "duplicate_names_accepted": _m_publish_unchecked_name,
    "status_hides_reason": _m_status_hides_reason,
    "fingerprint_decides_whether_item_changed": _m_weak_digest,
    "upstream_state_ignored": _m_upstream_ignored,
    "lost_proxies_not_reattached": _m_no_reattach,
    "break_does_not_touch_dependents": _m_dependents_not_touched,
    "document_open_matched_by_file_name": _m_document_open_by_basename,
    "empty_geometry_reported_following": _m_empty_is_following,
    "stale_reported_as_following": _m_never_stale,
    "item_recognised_by_property_names": _m_is_item_by_names,
    "skeleton_document_identified_by_path": _m_document_by_path,
    "datum_without_shape_not_rebuilt": _m_datum_not_rebuilt,
    "restore_does_not_touch_changed_subscribers": _m_restore_touch_dropped,
    "subscribe_cycle_allowed": _m_cycle_allowed,
    "observer_prepares_every_document": _m_observer_ungated,
    "skeleton_bound_by_name_only": _m_bound_by_name,
    "break_touches_containers": _m_containers_touched,
    "datum_geometry_not_recorded": _m_datum_not_recorded,
}


@contextlib.contextmanager
def mutated(name):
    spec = MUTANTS[name]()
    proxy = spec.pop("_proxy_execute", None) or spec.pop("_proxy_item", None)
    saved = (AS.Subscription.execute, AS.PublishedItem.execute)
    try:
        if proxy is not None:
            setattr(proxy[0], proxy[1], proxy[2])
        with patched(**spec):
            yield
    finally:
        AS.Subscription.execute, AS.PublishedItem.execute = saved


def run_case(name):
    try:
        return CASES[name]()
    except Exception as exc:
        import traceback
        return ["raised %r: %s" % (exc, traceback.format_exc().splitlines()[-3:])]


class TestSkeleton(unittest.TestCase):
    def tearDown(self):
        for n in list(App.listDocuments()):
            App.closeDocument(n)

    def test_corpus_size(self):
        self.assertGreaterEqual(len(CASES), 30)

    def test_cases(self):
        for name in CASES:
            with self.subTest(case=name):
                problems = run_case(name)
                self.assertEqual(problems, [], name)
                for n in list(App.listDocuments()):
                    App.closeDocument(n)

    def test_mutants_are_caught(self):
        self.assertGreaterEqual(len(MUTANTS), 12)
        table = []
        for mname in MUTANTS:
            failing = []
            with mutated(mname):
                for cname in CASES:
                    problems = run_case(cname)
                    for n in list(App.listDocuments()):
                        App.closeDocument(n)
                    if problems:
                        failing.append(cname)
            table.append((mname, failing))
        print("\n  mutants: %d planted, %d caught" % (len(table), sum(1 for _m, f in table if f)))
        for mname, failing in table:
            print("    %-42s %3d  %s" % (mname, len(failing), ", ".join(failing[:3])))
        for mname, failing in table:
            with self.subTest(mutant=mname):
                self.assertTrue(failing, "mutant %s survived" % mname)
