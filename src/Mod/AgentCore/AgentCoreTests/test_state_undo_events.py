# SPDX-License-Identifier: LGPL-2.1-or-later
"""F1: undo, redo and rollback journal every element reference they change.

For every toponaming scenario (fixtures/state/topo_scenarios.py) the document goes through
edit + recompute, undo, recompute, redo, recompute (one pass, edit committed) and edit + recompute
+ abort (second pass). Before each step the test holds a model of every link-family property
(flattened to (target, sub) per position); after the step it replays the journal events of that
step onto the model and demands that the result equals the real document. The edit itself is
the one silent step (a user assignment is not journaled), so after it the model is resynced.

Replay rules, as documented in Document.pyi: remapped, lost and restored set the reference at
``subIndex``; added appends at ``subIndex``; removed truncates the property to ``subIndex``.
"""

import os
import sys
import unittest

import FreeCAD as App

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "state")
if FIX not in sys.path:
    sys.path.insert(0, FIX)

import topo_scenarios  # noqa: E402
from AgentCore import state  # noqa: E402
from AgentCore.state.refs import LINK_SUB_TYPES  # noqa: E402

HISTORY = ("undo", "redo", "rollback")


def flat(value):
    """Flatten a link property value into [(object name, sub), ...] as the C++ journal counts."""
    out = []
    if value is None:
        return out
    if hasattr(value, "Name") and hasattr(value, "Document") and not isinstance(value, (list, tuple)):
        return [(value.Name, "")]
    if isinstance(value, tuple) and len(value) == 2 and hasattr(value[0], "Name"):
        subs = value[1]
        if isinstance(subs, str):
            subs = [subs]
        for s in subs or [""]:
            out.append((value[0].Name, str(s)))
        return out
    if isinstance(value, (list, tuple)):
        for v in value:
            out.extend(flat(v))
    return out


def model_of(doc):
    model = {}
    for obj in doc.Objects:
        for prop in obj.PropertiesList:
            try:
                if obj.getTypeIdOfProperty(prop) not in LINK_SUB_TYPES:
                    continue
                refs = flat(obj.getPropertyByName(prop))
            except Exception:  # noqa: BLE001
                continue
            if refs:
                model[(obj.Name, prop)] = refs
    return model


def replay(model, events):
    model = {k: list(v) for k, v in model.items()}
    for ev in sorted(events, key=lambda e: e["seq"]):
        key = (ev["owner"], ev["property"])
        refs = model.setdefault(key, [])
        i = ev["subIndex"]
        if ev["kind"] == "removed":
            del refs[i:]
        elif ev["kind"] == "added":
            assert i == len(refs), "added must append: %s" % (ev,)
            refs.append((ev["target"], ev["newSub"]))
        else:
            while len(refs) <= i:
                refs.append(("", ""))
            refs[i] = (ev["target"], ev["newSub"])
        if not refs:
            del model[key]
    return model


def diff(a, b):
    return {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)}


def build_binder():
    doc = App.newDocument("undo_binder")
    body, sk, pad = topo_scenarios.base_pad(doc, 24, 14, 5)
    binder = body.newObject("PartDesign::SubShapeBinder", "Binder")
    binder.Support = [(pad, ["Face1"])]
    doc.recompute()
    return doc, ("Binder", "Support", 0)


def edit_binder_support(doc):
    pad = doc.getObject("Pad")
    doc.getObject("Binder").Support = [(pad, ["Face2", "Face3", "Face4"])]


def edit_binder_shrink(doc):
    pad = doc.getObject("Pad")
    doc.getObject("Binder").Support = [(pad, ["Face1"]), (doc.getObject("Sketch"), [""])]
    doc.getObject("Binder").Support = [(pad, [])]


def edit_attach_other_face(doc):
    # a direct reassignment of a LinkSub: nothing journals it, undo must
    hs = doc.getObject("HoleSketch")
    hs.AttachmentSupport = (doc.getObject("Pad"), ["Face2"])



EXTRA = [
    ("binder_support_grows", build_binder, edit_binder_support),
    ("binder_support_shrinks", build_binder, edit_binder_shrink),
    ("attach_other_face", topo_scenarios.build_attach, edit_attach_other_face),
]


class Tally:
    def __init__(self):
        self.steps = {}
        self.moved = {}  # step -> (agree, total) over steps where the step changed some reference
        self.bad = []
        self.events = {}

    def add(self, scenario, step, model, doc, seq0):
        events = list(doc.getElementReferenceEvents(seq0))
        predicted = replay(model, events)
        actual = model_of(doc)
        d = diff(predicted, actual)
        ok, total = self.steps.get(step, (0, 0))
        self.steps[step] = (ok + (0 if d else 1), total + 1)
        if model != actual:
            ok, total = self.moved.get(step, (0, 0))
            self.moved[step] = (ok + (0 if d else 1), total + 1)
        for e in events:
            self.events[e["basis"]] = self.events.get(e["basis"], 0) + 1
        if d:
            self.bad.append((scenario, step, d))
        return actual


def commit_pass(name, build, edit, tally):
    doc, _target = build()
    try:
        doc.clearUndos()
        model = model_of(doc)
        seq = doc.lastElementReferenceEventSeq()
        doc.openTransaction("edit")
        edit(doc)
        doc.recompute()
        doc.commitTransaction()
        model = model_of(doc)  # the edit is silent: resync
        seq = doc.lastElementReferenceEventSeq()
        for step, action in (("undo", doc.undo), ("undo_recompute", doc.recompute), ("redo", doc.redo),
                             ("redo_recompute", doc.recompute)):
            if step == "undo":
                assert doc.UndoCount >= 1, "%s: the edit left no undo entry" % name
            action()
            model = tally.add(name, step, model, doc, seq)
            seq = doc.lastElementReferenceEventSeq()
    finally:
        App.closeDocument(doc.Name)


def abort_pass(name, build, edit, tally):
    doc, _target = build()
    try:
        doc.clearUndos()
        doc.openTransaction("edit")
        edit(doc)
        doc.recompute()
        model = model_of(doc)
        seq = doc.lastElementReferenceEventSeq()
        doc.abortTransaction()
        tally.add(name, "rollback", model, doc, seq)
    finally:
        App.closeDocument(doc.Name)


class TestStateUndoEvents(unittest.TestCase):
    def test_journal_replay_agrees_with_the_document(self):
        tally = Tally()
        names = []
        for sc in list(topo_scenarios.SCENARIOS) + EXTRA:
            name, build, edit = sc[0], sc[1], sc[2]
            names.append(name)
            try:
                commit_pass(name, build, edit, tally)
            except Exception as e:  # noqa: BLE001
                tally.bad.append((name, "commit_pass raised", repr(e)))
            try:
                abort_pass(name, build, edit, tally)
            except Exception as e:  # noqa: BLE001
                tally.bad.append((name, "abort_pass raised", repr(e)))
        total_ok = sum(o for o, _ in tally.steps.values())
        total = sum(t for _, t in tally.steps.values())
        print("\nundo-event replay: %d scenarios, %d of %d steps agree; per step %s; steps that changed a reference %s; events by basis %s" %
              (len(names), total_ok, total, tally.steps, tally.moved, tally.events))
        for b in tally.bad:
            print("  DISAGREE", b)
        self.assertEqual(tally.bad, [], "\n".join(map(str, tally.bad)))
        self.assertGreater(tally.events.get("undo", 0), 0)
        self.assertGreater(tally.events.get("redo", 0), 0)
        self.assertGreater(tally.events.get("rollback", 0), 0)


if __name__ == "__main__":
    unittest.main()
