# SPDX-License-Identifier: LGPL-2.1-or-later
"""Journal of element-reference updates (cad-fork F1, C++ half).

``PropertyLinkBase::_updateElementReference`` records an event whenever it moves a reference to
another element (``remapped``), marks one missing (``lost``) or gives an earlier one back
(``restored``). The journal is observational: these tests read it through
``Document.getElementReferenceEvents`` and never depend on it for behaviour.
"""

import os
import tempfile
import unittest

import FreeCAD as App
import Part
from FreeCAD import Vector as V

from PartDesignTests.TestReferenceRescue import (
    H,
    L,
    W,
    _add,
    _circle,
    _line_edges,
    _rect,
    _sketch,
)

KEYS = {
    "seq", "document", "owner", "property", "target", "oldSub", "newSub", "oldMapped", "newMapped",
    "kind", "basis",
}


class TestElementReferenceEvents(unittest.TestCase):
    def setUp(self):
        self.docname = "RefEvents." + self._testMethodName
        self.doc = App.newDocument(self.docname)
        self.body = self.doc.addObject("PartDesign::Body", "Body")

    def tearDown(self):
        if self.docname in App.listDocuments():
            App.closeDocument(self.docname)

    # -------------------------------------------------------------- builders
    def _pad(self):
        sk = _sketch(self.doc, self.body, "Sketch")
        _rect(sk, 0, 0, W, H)
        pad = self.body.newObject("PartDesign::Pad", "Pad")
        pad.Profile = sk
        pad.Length = L
        self.doc.recompute()
        return pad

    def _fillet(self, pad, edges, size=2.0, after=None):
        f = _add(self.doc, self.body, "PartDesign::Fillet", "Fillet", after)
        f.Base = (pad, edges)
        f.Radius = size
        return f

    def _pad_with_fillet(self):
        pad = self._pad()
        edges = _line_edges(pad.Shape, V(0, 0, 1), (-W / 2, W / 2), (-H / 2, H / 2))
        self.assertEqual(len(edges), 4)
        fil = self._fillet(pad, edges)
        self.doc.recompute()
        self.assertNotIn("Invalid", fil.State)
        return pad, fil

    def _insert_hole(self, after, x=-10.0):
        hs = _sketch(self.doc, self.body, "HoleSketch", z=L, after=after)
        _circle(hs, x, 0.0, 2.0)
        hp = _add(self.doc, self.body, "PartDesign::Pocket", "HolePocket", hs)
        hp.Profile = hs
        hp.Length = 2 * L
        return hp

    def _events(self, since=0, **match):
        out = self.doc.getElementReferenceEvents(since)
        return [e for e in out if all(e[k] == v for k, v in match.items())]

    # -------------------------------------------------------------- the API
    def test_event_shape_and_empty_journal(self):
        self.assertEqual(self.doc.getElementReferenceEvents(), [])
        self.assertIsInstance(self.doc.lastElementReferenceEventSeq(), int)
        self._pad_with_fillet()
        for e in self.doc.getElementReferenceEvents():
            self.assertEqual(set(e), KEYS)

    # -------------------------------------------------------------- remapped
    def test_remap_when_the_element_map_renumbers_an_edge(self):
        """Splitting one side of the Pad's rectangle into two halves adds a vertex and a face, so the
        element map keeps the Fillet's edges but their indices shift and FreeCAD rewrites the Base sub."""
        pad = self._pad()
        edges = []
        for sx in (-W / 2, W / 2):
            for i, e in enumerate(pad.Shape.Edges):
                c = e.CenterOfMass
                if abs(c.x - sx) < 1e-6 and abs(c.y) < 1e-6 and abs(c.z - L) < 1e-6:
                    edges.append("Edge%d" % (i + 1))
        self.assertEqual(len(edges), 2)
        fil = self._fillet(pad, edges, 1.0)
        self.doc.recompute()
        self.assertNotIn("Invalid", fil.State)
        before = list(fil.Base[1])
        seq0 = self.doc.lastElementReferenceEventSeq()
        sk = self.doc.getObject("Sketch")
        i = next(i for i, g in enumerate(sk.Geometry) if abs(g.StartPoint.y + H / 2) < 1e-6 and abs(g.EndPoint.y + H / 2) < 1e-6)
        a, b = sk.Geometry[i].StartPoint, sk.Geometry[i].EndPoint
        m = (a + b) * 0.5
        sk.delGeometry(i)
        sk.addGeometry(Part.LineSegment(a, m))
        sk.addGeometry(Part.LineSegment(m, b))
        self.doc.recompute()
        after = list(fil.Base[1])
        self.assertNotEqual(before, after, "scenario must renumber the edges")
        evs = self._events(seq0, owner="Fillet", property="Base")
        self.assertTrue(evs, "renumbered references produced no event")
        for e in evs:
            self.assertEqual(e["kind"], "remapped")
            self.assertEqual(e["basis"], "element_map")
            self.assertEqual(e["target"], "Pad")
            self.assertIn(e["oldSub"], before)
            self.assertNotEqual(e["oldSub"], e["newSub"])
        self.assertEqual(sorted(e["newSub"] for e in evs), sorted(a for a, b in zip(after, before) if a != b))

    def test_no_event_when_nothing_changes(self):
        pad, fil = self._pad_with_fillet()
        seq0 = self.doc.lastElementReferenceEventSeq()
        pad.Length = L + 5.0
        self.doc.recompute()
        self.assertEqual(self._events(seq0), [])

    # -------------------------------------------------------------- lost
    def test_lost_exactly_once_over_two_recomputes(self):
        pad = self._pad()
        edge = _line_edges(pad.Shape, V(0, 0, 1), (W / 2,), (H / 2,))[0]
        fil = self._fillet(pad, [edge], 1.0)
        self.doc.recompute()
        seq0 = self.doc.lastElementReferenceEventSeq()
        # a chamfer inserted ahead of the Fillet consumes that very edge
        ch = _add(self.doc, self.body, "PartDesign::Chamfer", "Chamfer", pad)
        ch.Base = (pad, [edge])
        ch.Size = 3.0
        self.doc.recompute()
        lost = [e for e in self._events(seq0, owner="Fillet") if e["kind"] == "lost"]
        self.assertEqual(len(lost), 1, lost)
        self.assertTrue(any(s.startswith("?") for s in fil.Base[1]))
        # touching and recomputing again must not repeat the event
        fil.touch()
        self.doc.recompute()
        ch.touch()
        self.doc.recompute()
        lost = [e for e in self._events(seq0, owner="Fillet") if e["kind"] == "lost"]
        self.assertEqual(len(lost), 1, lost)

    # -------------------------------------------------------------- history / geometry rescue
    def test_rescue_after_insertion_is_a_remap_by_geometry_or_history(self):
        pad, fil = self._pad_with_fillet()
        seq0 = self.doc.lastElementReferenceEventSeq()
        self._insert_hole(pad)
        self.doc.recompute()
        self.assertNotIn("Invalid", fil.State)
        evs = self._events(seq0, owner="Fillet", property="Base")
        self.assertTrue(evs)
        for e in evs:
            self.assertEqual(e["kind"], "remapped", e)
            self.assertIn(e["basis"], ("geometry", "history", "anchored"), e)
            self.assertEqual(e["target"], "HolePocket", e)
        self.assertEqual(len(self._events(seq0, kind="lost")), 0)

    # -------------------------------------------------------------- restored
    def _chamfer_and_dressed_pad(self):
        pad = self._pad()
        b0 = self._fillet(pad, ["Edge1", "Edge7"], 0.3)
        self.doc.recompute()
        self.assertNotIn("Invalid", b0.State)
        return pad, b0

    def test_deleting_the_inserted_feature_gives_the_original_back(self):
        """A Chamfer inserted ahead of the Fillet takes its Edge1 (history rescue); deleting the
        Chamfer gives the original back from the rescue record."""
        pad, b0 = self._chamfer_and_dressed_pad()
        original = list(b0.Base[1])
        ins = _add(self.doc, self.body, "PartDesign::Chamfer", "Chamfer", pad)
        ins.Base = (pad, ["Edge2"])
        ins.Size = 0.5
        self.doc.recompute()
        self.assertIs(b0.Base[0], ins)
        seq0 = self.doc.lastElementReferenceEventSeq()
        self.body.removeObject(ins)
        self.doc.removeObject(ins.Name)
        self.doc.recompute()
        self.assertIs(b0.Base[0], pad)
        self.assertEqual(list(b0.Base[1]), original)
        restored = [e for e in self._events(seq0, owner="Fillet") if e["kind"] == "restored"]
        self.assertEqual(len(restored), 1, self._events(seq0))
        self.assertEqual(restored[0]["basis"], "record")
        self.assertEqual(restored[0]["target"], "Pad")
        self.assertEqual([e for e in self._events(seq0) if e["kind"] == "lost"], [])

    def test_undo_of_an_insertion_does_not_claim_a_loss(self):
        """Undo puts the property back directly, not through the element-reference update, so
        the journal has nothing to say about it; in particular it never reports a loss."""
        pad, fil = self._pad_with_fillet()
        original = list(fil.Base[1])
        self.doc.openTransaction("insert hole")
        self._insert_hole(pad)
        self.doc.commitTransaction()
        self.doc.recompute()
        self.assertIs(fil.Base[0], self.doc.getObject("HolePocket"))
        seq0 = self.doc.lastElementReferenceEventSeq()
        self.doc.undo()
        self.doc.recompute()
        self.assertIs(self.doc.getObject("Fillet").Base[0], self.doc.getObject("Pad"))
        self.assertEqual(list(self.doc.getObject("Fillet").Base[1]), original)
        self.assertEqual([e for e in self._events(seq0) if e["kind"] == "lost"], [])

    # -------------------------------------------------------------- document lifecycle
    def test_opening_a_saved_document_produces_no_events(self):
        pad, fil = self._pad_with_fillet()
        self._insert_hole(pad)
        self.doc.recompute()
        self.assertTrue(self.doc.getElementReferenceEvents(), "setup must have produced events")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "refevents.FCStd")
            self.doc.saveAs(path)
            App.closeDocument(self.doc.Name)
            doc2 = App.openDocument(path)
            try:
                self.assertEqual(doc2.getElementReferenceEvents(), [])
                doc2.recompute()
                self.assertEqual(doc2.getElementReferenceEvents(), [])
                for o in doc2.Objects:
                    o.touch()
                doc2.recompute()
                self.assertEqual(doc2.getElementReferenceEvents(), [], "an unchanged recompute must not log")
            finally:
                App.closeDocument(doc2.Name)

    def test_since_and_clear(self):
        pad, fil = self._pad_with_fillet()
        self._insert_hole(pad)
        self.doc.recompute()
        allev = self.doc.getElementReferenceEvents()
        self.assertGreaterEqual(len(allev), 2)
        seqs = [e["seq"] for e in allev]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), len(seqs))
        mid = seqs[0]
        self.assertEqual([e["seq"] for e in self.doc.getElementReferenceEvents(mid)], seqs[1:])
        self.assertEqual(self.doc.getElementReferenceEvents(seqs[-1]), [])
        last = self.doc.lastElementReferenceEventSeq()
        self.assertGreaterEqual(last, seqs[-1])
        self.doc.clearElementReferenceEvents()
        self.assertEqual(self.doc.getElementReferenceEvents(), [])
        self.assertEqual(self.doc.lastElementReferenceEventSeq(), last, "clear must not rewind seq")

    def test_events_are_per_document_and_closing_drops_them(self):
        pad, fil = self._pad_with_fillet()
        self._insert_hole(pad)
        self.doc.recompute()
        self.assertTrue(self.doc.getElementReferenceEvents())
        other = App.newDocument("RefEventsOther")
        try:
            self.assertEqual(other.getElementReferenceEvents(), [])
        finally:
            App.closeDocument(other.Name)
        name = self.doc.Name
        App.closeDocument(name)
        again = App.newDocument(name)
        try:
            self.assertEqual(again.getElementReferenceEvents(), [])
        finally:
            App.closeDocument(again.Name)
