# SPDX-License-Identifier: LGPL-2.1-or-later
"""F1: how the tracker attributes C++ journal events to references.

Covers the cases the 76 toponaming scenarios do not: several subs of one property renumbering
in a chain (A: EdgeX to EdgeY while B: EdgeY to EdgeZ), a lost event next to a remap on the next
sub, and G01 insertions that retarget the link to the inserted feature.
"""

import unittest

import FreeCAD as App
import Part
from FreeCAD import Vector as V

from AgentCore import state
from AgentCore.state.refs import _sub_element
from PartDesignTests.TestReferenceRescue import H, L, W, _add, _circle, _rect, _sketch


def _pad_doc(name):
    doc = App.newDocument(name)
    body = doc.addObject("PartDesign::Body", "Body")
    sk = _sketch(doc, body, "Sketch")
    _rect(sk, 0, 0, W, H)
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = L
    doc.recompute()
    return doc, body, sk, pad


def _split_bottom(sk):
    i = next(i for i, g in enumerate(sk.Geometry) if abs(g.StartPoint.y + H / 2) < 1e-6 and abs(g.EndPoint.y + H / 2) < 1e-6)
    a, b = sk.Geometry[i].StartPoint, sk.Geometry[i].EndPoint
    m = (a + b) * 0.5
    sk.delGeometry(i)
    sk.addGeometry(Part.LineSegment(a, m))
    sk.addGeometry(Part.LineSegment(m, b))


class TestJournalMatching(unittest.TestCase):
    def test_chain_of_renumbering_subs_is_attributed_one_to_one(self):
        doc, body, sk, pad = _pad_doc("JM.chain")
        try:
            hold = doc.addObject("App::FeaturePython", "Hold")
            hold.addProperty("App::PropertyLinkSub", "Ref")
            hold.Ref = (pad, ["Edge%d" % i for i in range(1, 13)])
            doc.recompute()
            before = list(hold.Ref[1])
            tr = state.track_references(doc)
            _split_bottom(sk)
            doc.recompute()
            after = list(hold.Ref[1])
            truth = dict(zip(before, after))
            self.assertGreaterEqual(sum(1 for a, b in truth.items() if a != b), 3, "needs 3+ renumbered subs")
            evs = tr.classify()
            for ev, rec in zip(evs, tr.records):
                fe = ev.get("freecad_event")
                if truth[rec["element"]] != rec["element"]:
                    self.assertIsNotNone(fe, rec["element"])
                    self.assertEqual(_sub_element(fe["oldSub"]), rec["element"])
                    self.assertEqual(_sub_element(fe["newSub"]), truth[rec["element"]], rec["element"])
                    self.assertNotIn("freecad_events", ev)
                else:
                    self.assertIsNone(fe, rec["element"])
            self.assertEqual(tr.untracked_events, [])
        finally:
            App.closeDocument(doc.Name)

    def test_lost_event_is_not_stolen_by_the_previous_sub(self):
        doc, body, sk, pad = _pad_doc("JM.steal")
        try:
            hold = doc.addObject("App::FeaturePython", "Hold")
            hold.addProperty("App::PropertyLinkSub", "Ref")
            hold.Ref = (pad, ["Edge1", "Edge8"])
            doc.recompute()
            tr = state.track_references(doc)

            def mk(seq, o, n, k):
                return {"seq": seq, "document": doc.Name, "owner": "Hold", "property": "Ref", "target": "Pad",
                        "targetDocument": doc.Name, "oldSub": o, "newSub": n, "oldMapped": "", "newMapped": "",
                        "kind": k, "basis": "element_map"}

            fake = [mk(10**9 + 1, "Edge1", "Edge8", "remapped"), mk(10**9 + 2, "Edge8", "?Edge8", "lost")]
            tr._read_journal = lambda: fake
            evs = tr.classify()
            a, b = evs
            self.assertEqual(a["freecad_event"]["kind"], "remapped")
            self.assertNotIn("property_marked_missing", a)
            self.assertEqual(b["freecad_event"]["kind"], "lost")
            self.assertTrue(b["property_marked_missing"])
            self.assertEqual(tr.untracked_events, [])
        finally:
            App.closeDocument(doc.Name)

    def test_event_that_ends_elsewhere_does_not_clear_stale(self):
        doc, body, sk, pad = _pad_doc("JM.stale")
        try:
            hold = doc.addObject("App::FeaturePython", "Hold")
            hold.addProperty("App::PropertyLinkSub", "Ref")
            hold.Ref = (pad, ["Edge1"])
            doc.recompute()
            tr = state.track_references(doc)
            ev = {"outcome": "remapped", "new_element": "Edge5", "now_points_to": "Edge1", "stale": True,
                  "property_updated": False}
            rec = tr.records[0]
            rec["cpp_chain"] = [{"seq": 1, "kind": "remapped", "oldSub": "Edge1", "newSub": "Edge8"}]
            tr._apply_journal(ev, rec)
            self.assertTrue(ev["stale"])
            self.assertFalse(ev["property_updated"])
            self.assertIn("freecad_event", ev)
        finally:
            App.closeDocument(doc.Name)

    def _fillet_case(self, edges, edit):
        doc, body, sk, pad = _pad_doc("JM.g01")
        fil = _add(doc, body, "PartDesign::Fillet", "Fillet")
        fil.Base = (pad, edges)
        fil.Radius = 1.0
        doc.recompute()
        tr = state.track_references(doc)
        edit(doc, body, pad)
        doc.recompute()
        return doc, tr

    def test_insertion_that_retargets_the_link_is_matched(self):
        def hole(doc, body, pad):
            hs = _sketch(doc, body, "HoleSketch", z=L, after=pad)
            _circle(hs, -10.0, 0.0, 2.0)
            hp = _add(doc, body, "PartDesign::Pocket", "HolePocket", hs)
            hp.Profile = hs
            hp.Length = 2 * L

        doc, tr = self._fillet_case(["Edge1", "Edge8", "Edge2", "Edge5"], hole)
        try:
            self.assertIs(doc.getObject("Fillet").Base[0], doc.getObject("HolePocket"))
            tr.classify()
            self.assertEqual(tr.untracked_events, [])
            matched = [r for r in tr.records if r["cpp_chain"]]
            self.assertTrue(matched)
            for r in matched:
                self.assertEqual(r["cpp_chain"][0]["target"], "HolePocket")
        finally:
            App.closeDocument(doc.Name)

    def test_insertion_that_consumes_one_edge_reports_the_loss(self):
        def chamfer(doc, body, pad):
            ch = _add(doc, body, "PartDesign::Chamfer", "Chamfer", pad)
            ch.Base = (pad, ["Edge1"])
            ch.Size = 3.0

        doc, tr = self._fillet_case(["Edge1", "Edge7"], chamfer)
        try:
            evs = tr.classify()
            self.assertEqual(tr.untracked_events, [])
            lost = [e for e in evs if e.get("freecad_event", {}).get("kind") == "lost"]
            self.assertTrue(lost, [e.get("freecad_event") for e in evs])
            for e in lost:
                self.assertTrue(e["property_marked_missing"])
        finally:
            App.closeDocument(doc.Name)
