# SPDX-License-Identifier: LGPL-2.1-or-later
"""F1: toponaming edit scripts end in an allowed outcome, never a silent
retarget. One generated test per scenario in fixtures/state/topo_scenarios.py."""

import os
import sys
import unittest

import FreeCAD as App

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "state")
if FIX not in sys.path:
    sys.path.insert(0, FIX)

import topo_scenarios  # noqa: E402
from AgentCore import state  # noqa: E402
from AgentCore.state.refs import _pairs  # noqa: E402

ALLOWED = {"kept", "remapped", "lost"}


def _element(doc, ev, name):
    owner = doc.getObject(ev["element_owner"])
    return owner.Shape.getElement(name)


def run_scenario(case, name, build, edit, pred, expect):
    doc, (owner, prop, index) = build()
    try:
        tracker = state.track_references(doc)
        self_targets = [r for r in tracker.records if (r["owner"], r["property"], r["index"]) == (owner, prop, index)]
        case.assertEqual(len(self_targets), 1, "target reference not snapshotted")
        before = _element(doc, self_targets[0], self_targets[0]["element"])
        case.assertTrue(pred(doc, before), "oracle rejects the target before the edit")
        edit(doc)
        doc.recompute()
        events = tracker.classify()
        case.assertEqual(len(events), len(tracker.records))
        for ev in events:
            case.assertIn(ev["outcome"], ALLOWED)
            case.assertIsInstance(ev["stale_geometry"], bool)
            if ev["outcome"] == "kept":
                # kept means the property still names the element it named
                case.assertEqual(ev["now_points_to"], ev["new_element"], ev)
            if ev["outcome"] == "remapped":
                case.assertTrue(ev["new_element"] != ev["element"] or ev.get("stale"), ev)
        target = next(e for e in events if (e["owner"], e["property"], e["index"]) == (owner, prop, index))
        case.assertIn(target["outcome"], expect, target)
        if target["outcome"] == "kept":
            cur = _pairs(doc.getObject(owner).getPropertyByName(prop))[index][1]
            el = _element(doc, target, cur.rpartition(".")[2])
            case.assertTrue(pred(doc, el), "kept reported but the property now names another element: %s" % target)
        elif target["outcome"] == "remapped":
            el = _element(doc, target, target["new_element"])
            case.assertTrue(pred(doc, el), "remap target is not the referenced element: %s" % target)
        else:
            case.assertIn("reason", target)
        report = tracker.report()
        case.assertEqual(sum(report["counts"].values()), report["references"])
        return target
    finally:
        App.closeDocument(doc.Name)


class TestStateToponaming(unittest.TestCase):
    outcomes = {}

    def test_at_least_twenty_scenarios(self):
        self.assertGreaterEqual(len(topo_scenarios.SCENARIOS), 20)

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
        target = run_scenario(self, name, build, edit, pred, expect)
        TestStateToponaming.outcomes[name] = target["outcome"]
    test.__doc__ = "toponaming: " + name
    return test


for _sc in topo_scenarios.SCENARIOS:
    setattr(TestStateToponaming, "test_topo_" + _sc[0], _make(_sc))
