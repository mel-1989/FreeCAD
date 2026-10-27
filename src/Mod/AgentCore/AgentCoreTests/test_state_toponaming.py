# SPDX-License-Identifier: LGPL-2.1-or-later
"""F1: toponaming edit scripts end in the correct outcome, never a silent
retarget. One generated test per scenario in fixtures/state/topo_scenarios.py.

The oracle is independent of the tracker. After the edit and recompute it
derives the correct verdict for the target reference from geometry alone:

- if the element's owner, or anything it depends on, failed or was not
  recomputed (``State`` or ``isValid()``), the geometry is stale and nothing
  can be confirmed: the correct verdict is ``lost``;
- otherwise it counts the elements of the owner's shape that satisfy the
  scenario's predicate ``pred``. None means ``lost``; exactly one means
  ``kept`` when it has the original short name and the property still names
  it, else ``remapped`` onto that element; more than one means the scenario is
  ill-posed and the test fails.

The tracker must report exactly that verdict (and, for a remap, that
element). The scenario's documented ``expect`` must equal it as well, so a
change in FreeCAD's behaviour shows up as a failure, not a silent pass.

One documented exception, ``expect="lost*"`` (a conservative loss): the
oracle finds the intended element but the tracker has no information that
singles it out (FreeCAD dropped the reference and two same-kind candidates
remain), so it must report ``lost`` rather than guess. The test asserts both
halves, so a tracker that learns to resolve the case fails it and the entry
must be updated. At most CONSERVATIVE_MAX scenarios may use it.
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
from AgentCore.state.entity import ELEMENT_RE  # noqa: E402
from AgentCore.state.refs import _pairs  # noqa: E402

ALLOWED = {"kept", "remapped", "lost"}
CONSERVATIVE = "lost*"
CONSERVATIVE_MAX = 3


def oracle_stale(doc, owner_name):
    """Independent staleness check (does not call the tracker)."""
    obj = doc.getObject(owner_name)
    if obj is None:
        return True
    for o in [obj] + list(obj.OutListRecursive):
        st = set(o.State)
        if st & {"Invalid", "Error", "Touched"}:
            return True
        try:
            if not o.isValid():
                return True
        except Exception:
            pass
    return False


def oracle_candidates(doc, owner_name, etype, pred):
    obj = doc.getObject(owner_name)
    try:
        shape = obj.Shape
    except Exception:
        return []
    if shape.isNull():
        return []
    elems = {"Face": shape.Faces, "Edge": shape.Edges, "Vertex": shape.Vertexes}[etype]
    return ["%s%d" % (etype, i + 1) for i, e in enumerate(elems) if pred(doc, e)]


def oracle_verdict(doc, owner, prop, index, element_owner, element, pred):
    """(outcome, element or None, why) from geometry alone."""
    if oracle_stale(doc, element_owner):
        return "lost", None, "stale"
    etype = ELEMENT_RE.match(element).group(1)
    cands = oracle_candidates(doc, element_owner, etype, pred)
    if not cands:
        return "lost", None, "no element satisfies the predicate"
    if len(cands) > 1:
        return "ambiguous", cands, "several elements satisfy the predicate"
    pairs = _pairs(doc.getObject(owner).getPropertyByName(prop))
    cur = pairs[index][1].rpartition(".")[2] if index < len(pairs) else ""
    if cands[0] == element and cur == element:
        return "kept", cands[0], "same element, property unchanged"
    return "remapped", cands[0], "intended element is %s" % cands[0]


def run_scenario(case, name, build, edit, pred, expect):
    doc, (owner, prop, index) = build()
    try:
        tracker = state.track_references(doc)
        targets = [r for r in tracker.records if (r["owner"], r["property"], r["index"]) == (owner, prop, index)]
        case.assertEqual(len(targets), 1, "target reference not snapshotted")
        rec = targets[0]
        before = oracle_candidates(doc, rec["element_owner"], ELEMENT_RE.match(rec["element"]).group(1), pred)
        case.assertEqual(before, [rec["element"]], "oracle must single out the target before the edit")
        edit(doc)
        doc.recompute()
        events = tracker.classify()
        case.assertEqual(len(events), len(tracker.records))
        for ev in events:
            case.assertIn(ev["outcome"], ALLOWED)
            case.assertIsInstance(ev["stale_geometry"], bool)
            case.assertEqual(ev["stale_geometry"], oracle_stale(doc, ev["element_owner"]), ev)
            if ev["stale_geometry"]:
                case.assertEqual(ev["outcome"], "lost", "stale geometry must never be reported kept or remapped: %s" % ev)
            if ev["outcome"] == "kept":
                case.assertEqual(ev["now_points_to"], ev["new_element"], ev)
            if ev["outcome"] == "remapped":
                case.assertTrue(ev["new_element"] != ev["element"] or ev.get("stale"), ev)
            if ev["outcome"] == "lost":
                case.assertTrue(ev.get("reason"), ev)
        target = next(e for e in events if (e["owner"], e["property"], e["index"]) == (owner, prop, index))
        verdict, element, why = oracle_verdict(doc, owner, prop, index, rec["element_owner"], rec["element"], pred)
        case.assertNotEqual(verdict, "ambiguous", "ill-posed scenario %s: %s" % (name, element))
        if expect == CONSERVATIVE:
            case.assertIn(verdict, ("kept", "remapped"), "conservative loss documented but the oracle finds no element")
            case.assertEqual(target["outcome"], "lost", "documented conservative loss now resolves: %s" % target)
            case.assertIsNone(target.get("new_element"), target)
            report = tracker.report()
            return target, report, why
        case.assertEqual(target["outcome"], verdict, "oracle says %s (%s); tracker: %s" % (verdict, why, target))
        if verdict in ("kept", "remapped"):
            case.assertEqual(target["new_element"], element, target)
        if verdict == "kept":
            cur = _pairs(doc.getObject(owner).getPropertyByName(prop))[index][1]
            el = doc.getObject(target["element_owner"]).Shape.getElement(cur.rpartition(".")[2])
            case.assertTrue(pred(doc, el), "kept reported but the property now names another element: %s" % target)
        case.assertEqual(expect, verdict, "scenario documents %s but the oracle derives %s" % (expect, verdict))
        report = tracker.report()
        case.assertEqual(sum(report["counts"].values()), report["references"])
        return target, report, why
    finally:
        App.closeDocument(doc.Name)


class TestStateToponaming(unittest.TestCase):
    outcomes = {}

    def test_scenario_count_and_distinct_models(self):
        sc = topo_scenarios.SCENARIOS
        self.assertGreaterEqual(len(sc), 60)
        self.assertEqual(len({s[0] for s in sc}), len(sc), "scenario names must be unique")
        keys = [topo_scenarios.model_key(s) for s in sc]
        self.assertEqual(len(set(keys)), len(keys), "two scenarios share a builder and parameter set")
        self.assertGreaterEqual(len({k[0] for k in keys}), 15, "need at least 15 model families")

    def test_no_wildcard_expectations(self):
        for s in topo_scenarios.SCENARIOS:
            self.assertIsInstance(s[4], str, s[0])
            self.assertIn(s[4], ALLOWED | {CONSERVATIVE}, s[0])
        self.assertLessEqual(sum(1 for s in topo_scenarios.SCENARIOS if s[4] == CONSERVATIVE), CONSERVATIVE_MAX)

    def test_context_manager(self):
        doc, _ = topo_scenarios.build_box()
        try:
            with state.track_references(doc) as t:
                doc.getObject("Box").Height = 7
                doc.recompute()
            self.assertEqual([e["outcome"] for e in t.events], ["kept"])
            self.assertTrue(t.events[0]["geometry_changed"])
        finally:
            App.closeDocument(doc.Name)


def _make(sc):
    name, build, edit, pred, expect = sc

    def test(self):
        target, _, _ = run_scenario(self, name, build, edit, pred, expect)
        TestStateToponaming.outcomes[name] = target["outcome"]
    test.__doc__ = "toponaming: " + name
    return test


for _sc in topo_scenarios.SCENARIOS:
    setattr(TestStateToponaming, "test_topo_" + _sc[0], _make(_sc))
