# SPDX-License-Identifier: LGPL-2.1-or-later
"""F1: the C++ journal of element-reference updates agrees with the Python tracker.

Runs every toponaming edit scenario (fixtures/state/topo_scenarios.py) and compares, per tracked
reference, the tracker's own verdict (computed with the journal switched off) against the events
FreeCAD's ``_updateElementReference`` journalled during the same edit and recompute:

- every tracker ``remapped`` with ``property_updated`` has a C++ ``remapped`` (or ``restored``)
  event whose new sub is the tracker's new element;
- every tracker ``lost`` whose property now carries the missing mark (``?``) has a C++ ``lost``
  event;
- no C++ event touches a reference the tracker reports ``kept`` with an unchanged sub.

Disagreements are collected per scenario; ``KNOWN`` documents the ones that are understood.
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
from AgentCore.state.refs import _pairs, _sub_element  # noqa: E402

# (scenario, owner, property) -> explanation, for disagreements that are understood
KNOWN = {}


def compare(doc, tracker, py_events, cpp_events):
    """Return (counts, disagreements) for one scenario."""
    counts = {"tracked": len(py_events), "py_remapped_updated": 0, "py_lost_marked": 0, "py_kept_unchanged": 0,
              "cpp_events": len(cpp_events), "cpp_on_tracked": 0, "untracked": 0, "matched_remap": 0,
              "matched_lost": 0}
    bad = []
    used = tracker._match_journal(cpp_events)
    counts["untracked"] = len([c for c in cpp_events if c["seq"] not in used])
    for rec, ev in zip(tracker.records, py_events):
        chain = rec.get("cpp_chain") or []
        counts["cpp_on_tracked"] += len(chain)
        key = "%s.%s[%d]" % (ev["owner"], ev["property"], ev["index"])
        last = chain[-1] if chain else None
        pairs = _pairs(doc.getObject(ev["owner"]).getPropertyByName(ev["property"]))
        cur = pairs[ev["index"]][1] if ev["index"] < len(pairs) else ""
        if ev["outcome"] == "remapped" and ev.get("property_updated"):
            counts["py_remapped_updated"] += 1
            if last is None or last["kind"] not in ("remapped", "restored"):
                bad.append(("missing_cpp_remap", key, ev.get("new_element"), last))
            elif _sub_element(last["newSub"]) != ev["new_element"]:
                bad.append(("remap_target_differs", key, ev.get("new_element"), last))
            else:
                counts["matched_remap"] += 1
        elif ev["outcome"] == "lost" and cur.rpartition(".")[2].startswith("?"):
            counts["py_lost_marked"] += 1
            if last is None or last["kind"] != "lost":
                bad.append(("missing_cpp_lost", key, None, last))
            else:
                counts["matched_lost"] += 1
        elif ev["outcome"] == "kept" and ev.get("now_points_to") == ev["element"]:
            counts["py_kept_unchanged"] += 1
            if chain:
                bad.append(("cpp_event_on_kept", key, None, chain))
    return counts, bad


def run(name, build, edit):
    doc, _target = build()
    try:
        tracker = state.track_references(doc)
        seq0 = doc.lastElementReferenceEventSeq()
        edit(doc)
        doc.recompute()
        tracker.journal_seq = None  # the tracker's own verdict, without the journal
        py_events = tracker.classify()
        cpp_events = list(doc.getElementReferenceEvents(seq0))
        return compare(doc, tracker, py_events, cpp_events)
    finally:
        App.closeDocument(doc.Name)


class TestStateCppAgreement(unittest.TestCase):
    results = {}

    def test_all_scenarios_agree(self):
        total = {}
        disagreements = []
        for sc in topo_scenarios.SCENARIOS:
            name, build, edit = sc[0], sc[1], sc[2]
            counts, bad = run(name, build, edit)
            TestStateCppAgreement.results[name] = (counts, bad)
            for k, v in counts.items():
                total[k] = total.get(k, 0) + v
            for b in bad:
                if (name, b[1].split("[")[0]) not in KNOWN:
                    disagreements.append((name,) + b)
        print("\nagreement totals: %s over %d scenarios" % (total, len(topo_scenarios.SCENARIOS)))
        self.assertEqual(disagreements, [], "\n".join(map(str, disagreements)))
        self.assertGreater(total["matched_remap"], 0)
        self.assertGreater(total["matched_lost"], 0)

    def test_journal_present(self):
        doc = App.newDocument("CppAgreementProbe")
        try:
            self.assertTrue(hasattr(doc, "getElementReferenceEvents"))
            self.assertEqual(doc.getElementReferenceEvents(), [])
        finally:
            App.closeDocument(doc.Name)
