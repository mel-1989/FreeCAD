# SPDX-License-Identifier: LGPL-2.1-or-later
"""G10 slice 4c, relation display: the relations panel and the view-only constraint filter, driven through real widgets.

Run with the GUI binary: ``FreeCAD -t TestSketcherRelationsPanelGui``.  The checks are the numbered acceptance checks of
slice 4c in briefs/phase1/G10-sketcher.md that belong to the relation display: 5 (the list equals ``Sketch.Constraints``),
6 (focus changes nothing in the document), 7 (the filter hides and restores exactly, and says so), 8 (stale events),
9 (responsiveness); 11 is the mutant script SketchRelationsPanelMutants.py.  Sketches are slice 1's synthetic corpus plus
a few built here.  The rows the panel must show are computed here, from ``sketch.Constraints`` directly, by code that does
not call the panel's: its own element names, its own definition of "on an element".
"""

import time

import FreeCAD
import FreeCADGui as Gui
import Part
import Sketcher
import SketcherGui
from PySide import QtGui, QtWidgets

import SketcherDiagnosis as SD
from SketcherTests import SketchDiagnosisCorpus as Corpus
from SketcherTests.TestSketcherDiagnosisPanelGui import (
    PanelTestBase,
    _find,
    _pump,
    _rows,
    _selected_names,
    _signature,
    _wait,
)

COMMAND = "Sketcher_Relations"
V = FreeCAD.Vector
UNDEF = -2000
RESPONSE_BAR = 0.1  # seconds, check 9
DASH = "—"


# -- the rows the panel must show, built here -------------------------------------------------------------------------


def _name_of(geo, pos):
    """The words for an element, written out here: Edge numbers are 1-based, -1 is the x axis and its point 1 the origin."""
    if geo == -1 and pos == 1:
        return "Origin"
    if geo == -1:
        return "X axis"
    if geo == -2:
        return "Y axis"
    base = "Edge%d" % (geo + 1) if geo >= 0 else "External edge %d" % (-geo - 2)
    return base + (" " + {1: "start", 2: "end", 3: "centre"}[pos] if pos else "")


def expected_rows(sk, geo, pos):
    """[(head, [tied labels])] for the element (geo, pos): `pos` None means the element with all its own points."""
    out = []
    for i, c in enumerate(sk.Constraints):
        slots = [s for s in ((c.First, c.FirstPos), (c.Second, c.SecondPos), (c.Third, c.ThirdPos)) if s[0] != UNDEF]
        hits = []
        for s in slots:
            if pos is not None:
                hit = s == (geo, pos)
            else:
                hit = s[0] == geo and not (geo == -1 and s[1] != 0)
            hits.append(hit)
        if not any(hits):
            continue
        on = []
        tied = []
        for s, hit in zip(slots, hits):
            label = _name_of(*s)
            bucket = on if hit else tied
            if label not in bucket:
                bucket.append(label)
        head = "%d: %s" % (i + 1, c.Type)
        if c.Name:
            head += " '%s'" % c.Name
        if c.Type in SD._DATUM_TYPES:
            head += " = %.6g %s" % (SD._value_in_unit(c.Value, c.Type), "deg" if c.Type == "Angle" else "mm")
        head += " " + DASH + " " + ", ".join(on)
        out.append((head, tied))
    return out


def element_names(sk):
    """[(selection name, (geo, pos or None))] for every element of the sketch, from the sketch's own tables."""
    out = [("H_Axis", (-1, None)), ("V_Axis", (-2, None)), ("RootPoint", (-1, 1))]
    for g in range(sk.GeometryCount):
        out.append(("Edge%d" % (g + 1), (g, None)))
    for k in range(len(sk.ExternalGeo) - 2):
        out.append(("ExternalEdge%d" % (k + 1), (-k - 3, None)))
    v = 0
    while True:
        try:
            g, p = sk.getGeoVertexIndex(v)
        except Exception:
            break
        if g == UNDEF:
            break
        out.append(("Vertex%d" % (v + 1), (g, p)))
        v += 1
    return out


def vertex_number(sk, geo, pos):
    v = 0
    while True:
        g, p = sk.getGeoVertexIndex(v)
        if (g, p) == (geo, pos):
            return v + 1
        v += 1


def _panel_rows(panel):
    return [(h, [c for c, _ in kids]) for h, kids in _rows(panel.tree)]


def _grab(view, path, size=(480, 360)):
    view.saveImage(path, size[0], size[1], "White")
    return QtGui.QImage(path)


def _length(v):
    return FreeCAD.Units.Quantity(v, FreeCAD.Units.Length)


class RelationsTestBase(PanelTestBase):
    def open_rel(self, sketch):
        self.edit(sketch)
        Gui.Selection.clearSelection()
        Gui.runCommand(COMMAND)
        import SketcherRelationsPanel as R

        self.R = R
        self.panel = R.current_panel()
        self.assertIsNotNone(self.panel)
        return self.panel

    def select(self, sk, *names):
        Gui.Selection.clearSelection()
        for n in names:
            Gui.Selection.addSelection(sk.Document.Name, sk.Name, n)
        _pump()

    def new_sketch(self, docname, build):
        d = FreeCAD.newDocument(docname)
        self._extra_docs.append(docname)
        sk = d.addObject("Sketcher::SketchObject", "S")
        build(sk)
        d.recompute()
        return sk

    @staticmethod
    def corner(sk):
        """A rectangle tied to the origin with two datums (fully constrained, nothing redundant)."""
        sk.addGeometry(Part.LineSegment(V(0, 0, 0), V(20, 0, 0)))
        sk.addGeometry(Part.LineSegment(V(20, 0, 0), V(20, 10, 0)))
        sk.addGeometry(Part.LineSegment(V(20, 10, 0), V(0, 10, 0)))
        sk.addGeometry(Part.LineSegment(V(0, 10, 0), V(0, 0, 0)))
        sk.addConstraint(Sketcher.Constraint("Coincident", 0, 2, 1, 1))  # 0
        sk.addConstraint(Sketcher.Constraint("Coincident", 1, 2, 2, 1))  # 1
        sk.addConstraint(Sketcher.Constraint("Coincident", 2, 2, 3, 1))  # 2
        sk.addConstraint(Sketcher.Constraint("Coincident", 3, 2, 0, 1))  # 3
        sk.addConstraint(Sketcher.Constraint("Horizontal", 0))  # 4
        sk.addConstraint(Sketcher.Constraint("Horizontal", 2))  # 5
        sk.addConstraint(Sketcher.Constraint("Vertical", 1))  # 6
        sk.addConstraint(Sketcher.Constraint("Vertical", 3))  # 7
        sk.addConstraint(Sketcher.Constraint("Coincident", 0, 1, -1, 1))  # 8
        sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 20.0))  # 9
        sk.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, 10.0))  # 10


DATUM_X = 9  # the DistanceX of `corner`


class TestSketcherRelationsPanelGui(RelationsTestBase):
    # -- check 5: the list equals Sketch.Constraints ---------------------------------------------------------------------
    def _check_every_element(self, sk, cap=None):
        p = self.open_rel(sk)
        els = element_names(sk)
        if cap and len(els) > cap:
            step = len(els) / float(cap)
            els = [els[int(k * step)] for k in range(cap)]
        n = 0
        for name, (geo, pos) in els:
            self.select(sk, name)
            self.assertEqual(_panel_rows(p), expected_rows(sk, geo, pos), "%s of %s" % (name, sk.Name))
            n += 1
        self.close_panel()
        return n

    def testRelationsEqualConstraintsOnTheWholeCorpus(self):
        checked = rows = 0
        for c in self.rig.cases:
            if c.sketch.ConstraintCount > 1200:
                continue
            checked += self._check_every_element(c.sketch, cap=40)
            rows += sum(len(expected_rows(c.sketch, g, p)) for _, (g, p) in element_names(c.sketch)[:40])
        self.assertGreater(checked, 500)
        self.assertGreater(rows, 500)

    def testThirdSlotsAndInternalAlignmentAreFound(self):
        def build(sk):
            sk.addGeometry(Part.LineSegment(V(0, 0, 0), V(10, 0, 0)))  # 0
            sk.addGeometry(Part.LineSegment(V(0, 5, 0), V(10, 5, 0)))  # 1 mirror axis
            sk.addGeometry(Part.LineSegment(V(0, -4, 0), V(10, -6, 0)))  # 2
            sk.addGeometry(Part.LineSegment(V(0, 20, 0), V(5, 30, 0)))  # 3
            sk.addGeometry(Part.LineSegment(V(5, 30, 0), V(15, 25, 0)))  # 4
            sk.addGeometry(Part.Ellipse(V(50, 0, 0), V(40, 4, 0), V(40, 0, 0)))  # 5
            sk.addConstraint(Sketcher.Constraint("Symmetric", 0, 1, 0, 2, 1))
            sk.addConstraint(Sketcher.Constraint("AngleViaPoint", 3, 4, 3, 2, 0.5))
            sk.exposeInternalGeometry(5)

        sk = self.new_sketch("RelThird", build)
        self.assertIn("InternalAlignment", {c.Type for c in sk.Constraints})
        n = self._check_every_element(sk)
        self.assertGreater(n, 10)
        p = self.open_rel(sk)
        third_only = [
            (i, c) for i, c in enumerate(sk.Constraints) if c.Third != UNDEF and c.Third not in (c.First, c.Second)
        ]
        self.assertTrue(third_only, "the sketch has no constraint that reaches an element only through Third")
        for i, c in third_only:
            name = "Edge%d" % (c.Third + 1) if not c.ThirdPos else "Vertex%d" % vertex_number(sk, c.Third, c.ThirdPos)
            self.select(sk, name)
            heads = [h for h, _ in _panel_rows(p)]
            self.assertTrue(any(h.startswith("%d: %s" % (i + 1, c.Type)) for h in heads), (i, heads))
        self.select(sk, "Edge6")
        aligned = [r for r in _panel_rows(p) if "InternalAlignment" in r[0]]
        self.assertGreaterEqual(len(aligned), 2)
        self.assertTrue(all(r[1] for r in aligned))

    def testPointsAndEdgesAreDifferentElements(self):
        sk = self.new_sketch("RelPoints", self.corner)
        p = self.open_rel(sk)
        self.select(sk, "Vertex2")  # Edge1 end
        heads = [h for h, _ in _panel_rows(p)]
        self.assertFalse(any("Horizontal" in h for h in heads), heads)
        self.assertTrue(any("Coincident" in h for h in heads))
        self.select(sk, "Edge1")
        heads = [h for h, _ in _panel_rows(p)]
        self.assertTrue(any("Horizontal" in h for h in heads))
        self.assertTrue(any(h.endswith("Edge1 end") for h in heads), heads)
        self.assertTrue(any(h.endswith("Edge1 start, Edge1 end") for h in heads), heads)
        self.select(sk, "H_Axis")
        self.assertEqual(_panel_rows(p), [])
        self.select(sk, "RootPoint")
        self.assertEqual(len(_panel_rows(p)), 1)
        self.assertEqual(_panel_rows(p)[0][1], ["Edge1 start"])

    def testCoincidentChainsShowOnlyWhatTheDataSays(self):
        def build(sk):
            for k in range(3):
                sk.addGeometry(Part.LineSegment(V(10 * k, 0, 0), V(10 * k + 5, 0, 0)))
            sk.addConstraint(Sketcher.Constraint("Coincident", 0, 2, 1, 1))
            sk.addConstraint(Sketcher.Constraint("Coincident", 1, 1, 2, 1))
            sk.addConstraint(Sketcher.Constraint("DistanceX", 2, 1, 2, 2, 5.0))

        sk = self.new_sketch("RelChain", build)
        p = self.open_rel(sk)
        self.select(sk, "Vertex2")  # Edge1 end: coincident with Edge2 start, which is coincident with Edge3 start
        self.assertEqual(_panel_rows(p), [("1: Coincident " + DASH + " Edge1 end", ["Edge2 start"])])
        self.select(sk, "Vertex3")  # Edge2 start: both Coincidents
        rows = _panel_rows(p)
        self.assertEqual([h for h, _ in rows], ["1: Coincident " + DASH + " Edge2 start", "2: Coincident " + DASH + " Edge2 start"])
        self.assertEqual([t for _, t in rows], [["Edge1 end"], ["Edge3 start"]])
        self.select(sk, "Vertex5")  # Edge3 start: the datum is on it; Edge1 end is not reached
        rows = _panel_rows(p)
        self.assertEqual([h for h, _ in rows], ["2: Coincident " + DASH + " Edge3 start", "3: DistanceX = 5 mm " + DASH + " Edge3 start"])
        self.assertEqual(rows[1][1], ["Edge3 end"])

    def testNothingSelectedOrAConstraintSelectedLeavesNoRows(self):
        sk = self.new_sketch("RelNone", self.corner)
        p = self.open_rel(sk)
        self.select(sk, "Edge1")
        self.assertTrue(_panel_rows(p))
        Gui.Selection.clearSelection()
        _pump()
        self.assertEqual(_panel_rows(p), [])
        self.select(sk, "Constraint1")
        self.assertEqual(_panel_rows(p), [])

    # -- check 6: focus -------------------------------------------------------------------------------------------------
    def _row(self, i):
        item = self.panel.tree.topLevelItem(i)
        self.panel.tree.setCurrentItem(item)
        _pump()
        return item

    @staticmethod
    def _number(item):
        return int(item.text(0).split(":")[0])

    def testChoosingARelationHighlightsItAndChangesNothingInTheDocument(self):
        sk = self.new_sketch("RelFocus", self.corner)
        p = self.open_rel(sk)
        self.select(sk, "Edge1")
        before_sel = _selected_names()
        sig = _signature(sk)
        touched = sk.Document.isTouched()
        self.assertGreater(p.tree.topLevelItemCount(), 3)
        for i in range(p.tree.topLevelItemCount()):
            item = self._row(i)
            idx = self._number(item)
            c = sk.Constraints[idx - 1]
            names = set(_selected_names())
            self.assertIn("Constraint%d" % idx, names)
            self.assertIn("Edge1", names)  # the user's own selection stays
            for s in ((c.First, c.FirstPos), (c.Second, c.SecondPos), (c.Third, c.ThirdPos)):
                if s[0] == UNDEF:
                    continue
                want = {(-1, 1): "RootPoint", (-1, 0): "H_Axis"}.get(s) or (
                    "Edge%d" % (s[0] + 1) if not s[1] else "Vertex%d" % vertex_number(sk, *s)
                )
                self.assertIn(want, names, (idx, s))
            self.assertEqual(_signature(sk), sig)
            self.assertEqual(sk.Document.isTouched(), touched)
        p.tree.clearSelection()
        _pump()
        self.assertEqual(_selected_names(), before_sel)

    def testFocusClearsOnLeavingAndKeepsTheUsersOwnSelection(self):
        sk = self.new_sketch("RelFocusLeave", self.corner)
        p = self.open_rel(sk)
        self.select(sk, "Edge1", "Edge2")  # Edge2 is also a partner of the first row
        mine = _selected_names()
        self._row(0)
        self.assertNotEqual(_selected_names(), mine)
        item = self._row(1)
        self.assertEqual([n for n in _selected_names() if n.startswith("Constraint")], ["Constraint%d" % self._number(item)])
        self.close_panel()
        _pump(30)
        self.assertEqual(_selected_names(), mine)
        p = self.open_rel(sk)
        self.select(sk, "Edge1")
        self._row(0)
        Gui.ActiveDocument.resetEdit()
        self.assertTrue(_wait(lambda: p.state == "closed"))
        self.assertFalse([n for n in _selected_names() if n.startswith("Constraint")])

    def testFocusOnAVanishedConstraintIsRefused(self):
        sk = self.new_sketch("RelFocusGone", self.corner)
        p = self.open_rel(sk)
        self.select(sk, "Edge1")
        n = len(sk.Constraints)
        self.assertFalse(p.focus(n))
        self.assertFalse(p.focus(n + 50))
        self.assertNotIn("Constraint%d" % (n + 1), _selected_names())

    # -- check 7: the filter ---------------------------------------------------------------------------------------------
    def _view(self, sk):
        self.edit(sk)
        view = Gui.ActiveDocument.ActiveView
        view.setCameraType("Orthographic")
        view.fitAll()
        _pump(80)
        return view

    def _shot(self, view, cam, path):
        _pump(120)
        view.setCamera(cam)
        _pump(60)
        return _grab(view, path)

    def testFilterHidesAndRestoresPixelForPixel(self):
        sk = self.new_sketch("RelPixels", self.corner)
        view = self._view(sk)
        cam = view.getCamera()
        p = self.open_rel(sk)
        base = self._shot(view, cam, "/tmp/rel_base.png")
        self.select(sk, "Edge1")
        p.filter_to_element()
        Gui.Selection.clearSelection()
        hidden = self._shot(view, cam, "/tmp/rel_hidden.png")
        self.assertNotEqual(base, hidden, "the filter hid nothing")
        self.assertTrue(p.banner.isVisible())
        p.clear_filter()
        self.assertEqual(base, self._shot(view, cam, "/tmp/rel_again.png"))
        self.assertFalse(p.banner.isVisible())
        p.filter_to_type("Coincident")
        self.assertNotEqual(base, self._shot(view, cam, "/tmp/rel_type.png"))
        p.clear_filter()
        self.assertEqual(base, self._shot(view, cam, "/tmp/rel_again2.png"))

    def testAConstraintInStocksVirtualSpaceStaysHiddenWhenTheFilterClears(self):
        sk = self.new_sketch("RelVirtual", self.corner)
        sk.setVirtualSpace(DATUM_X, True)
        view = self._view(sk)
        cam = view.getCamera()
        p = self.open_rel(sk)
        base = self._shot(view, cam, "/tmp/rel_v_base.png")
        self.assertTrue(p.filter_to_type("DistanceX"))  # lists the virtual-space constraint among the ids to show
        self.assertIn(DATUM_X, SketcherGui.getConstraintFilter())
        self.assertNotEqual(base, self._shot(view, cam, "/tmp/rel_v_on.png"))
        p.clear_filter()
        self.assertEqual(base, self._shot(view, cam, "/tmp/rel_v_off.png"))
        self.assertTrue(sk.Constraints[DATUM_X].InVirtualSpace)

    def testAMaskedMemberOfAMergedIconIsNotDrawn(self):
        def build(sk):
            sk.addGeometry(Part.LineSegment(V(0, 0, 0), V(20, 0, 0)))
            sk.addGeometry(Part.LineSegment(V(0, 10, 0), V(20, 10, 0)))
            sk.addConstraint(Sketcher.Constraint("Horizontal", 0))  # 0
            sk.addConstraint(Sketcher.Constraint("Horizontal", 1))  # 1
            sk.addConstraint(Sketcher.Constraint("Coincident", 0, 1, -1, 1))  # 2
            sk.addConstraint(Sketcher.Constraint("DistanceY", 0, 1, 1, 1, 10.0))  # 3
            sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 20.0))  # 4
            sk.addConstraint(Sketcher.Constraint("Equal", 0, 1))  # 5 shares the lines' icon spots with the Horizontals

        full = self.new_sketch("RelMergeFull", build)
        hidden = self.new_sketch("RelMergeHidden", build)
        hidden.setVirtualSpace(5, True)  # stock's own way of leaving a constraint out of the normal view and its merged icons
        hidden.Document.recompute()
        view = self._view(full)
        cam = view.getCamera()
        shown_all = self._shot(view, cam, "/tmp/rel_m_all.png")
        SketcherGui.setConstraintFilter([0, 1, 2, 3, 4])
        masked = self._shot(view, cam, "/tmp/rel_m_masked.png")
        self.assertNotEqual(shown_all, masked, "the mask changed nothing: the Equal icon was not on show")
        SketcherGui.setConstraintFilter(None)
        Gui.ActiveDocument.resetEdit()
        _pump(40)
        view = self._view(hidden)
        stock = self._shot(view, cam, "/tmp/rel_m_stock.png")
        self.assertEqual(masked, stock)

    def testAMaskedConstraintIsNotPickableAndStaysMaskedThroughRedraws(self):
        sk = self.new_sketch("RelPick", self.corner)
        self._view(sk)
        n = len(sk.Constraints)

        view = Gui.ActiveDocument.ActiveView
        corners = [view.getPointOnViewport(V(x, y, 0)) for x in (-15, 35) for y in (-15, 30)]
        x0, x1 = int(min(c[0] for c in corners)), int(max(c[0] for c in corners))
        y0, y1 = int(min(c[1] for c in corners)), int(max(c[1] for c in corners))

        def picks(index):
            name = "Constraint%d" % (index + 1)
            hits = 0
            for y in range(y0, y1, 4):
                for x in range(x0, x1, 4):
                    info = SketcherGui.getActiveSketchPreselection((x, y))
                    if info and name in (info.get("SubElementNames") or []):
                        hits += 1
            return hits

        self.assertGreater(picks(DATUM_X), 0, "the scan found no pick on the constraint: the test cannot see it")
        keep = [i for i in range(n) if i != DATUM_X]
        SketcherGui.setConstraintFilter(keep)
        _pump(80)
        self.assertEqual(picks(DATUM_X), 0)
        sk.moveGeometry(0, 2, V(1, 0, 0), True)  # moves a point and solves: every icon is placed again
        sk.solve()
        _pump(80)
        self.assertEqual(picks(DATUM_X), 0)
        sk.addConstraint(Sketcher.Constraint("Distance", 0, 20.0))  # a rebuild of every constraint node
        _pump(80)
        self.assertEqual(SketcherGui.getConstraintFilter(), keep, "the rebuild forgot or changed the mask")
        self.assertEqual(picks(DATUM_X), 0)
        SketcherGui.setConstraintFilter(None)
        _pump(80)
        self.assertGreater(picks(DATUM_X), 0)

    def testFilterContentsAreTheRelationsOrTheType(self):
        sk = self.new_sketch("RelFilterIds", self.corner)
        p = self.open_rel(sk)
        self.select(sk, "Edge2")
        self.assertTrue(p.filter_to_element())
        want = [int(h.split(":")[0]) - 1 for h, _ in expected_rows(sk, 1, None)]
        self.assertEqual(SketcherGui.getConstraintFilter(), want)
        self.assertIn("%d of %d" % (len(want), len(sk.Constraints)), p.banner.text())
        want = [i for i, c in enumerate(sk.Constraints) if c.Type == "Coincident"]
        self.assertTrue(p.filter_to_type("Coincident"))
        self.assertEqual(SketcherGui.getConstraintFilter(), want)
        self.assertEqual(p.type_box.currentData(), "Coincident")
        p.type_box.activated.emit(0)  # "All types"
        self.assertIsNone(SketcherGui.getConstraintFilter())
        self.assertFalse(p.banner.isVisible())

    def testFilterAnnouncesItselfAndNeverOutlivesThePanelOrTheEditor(self):
        sk = self.new_sketch("RelAnnounce", self.corner)
        p = self.open_rel(sk)
        self.select(sk, "Edge1")
        self.assertFalse(p.banner.isVisible())
        p.filter_to_element()
        self.assertTrue(p.banner.isVisible())
        self.assertIn("hidden, not removed", p.banner.text())
        self.assertTrue(p.all_button.isEnabled())
        self.close_panel()
        self.assertIsNone(SketcherGui.getConstraintFilter(), "closing the panel left the filter on")
        p = self.open_rel(sk)
        self.select(sk, "Edge1")
        p.filter_to_element()
        Gui.ActiveDocument.resetEdit()
        self.assertTrue(_wait(lambda: p.state == "closed"))
        self.edit(sk)
        self.assertIsNone(SketcherGui.getConstraintFilter(), "a new edit session started filtered")

    def testFilteringChangesNothingAboutTheSketchItsSolutionOrItsMessages(self):
        sk = self.new_sketch("RelSolve", self.corner)
        p = self.open_rel(sk)

        def state():
            labels = _find("labelStatus", QtWidgets.QLabel)
            return (sk.solve(), sk.DoF, labels[0].text() if labels else None, [sk.Constraints[i].Value for i in (DATUM_X, 10)])

        sig = _signature(sk)
        before = state()
        self.select(sk, "Edge1")
        p.filter_to_element()
        _pump(60)
        self.assertEqual(state(), before)
        self.assertEqual(_signature(sk), sig)
        p.filter_to_type("Vertical")
        _pump(60)
        self.assertEqual(state(), before)
        p.clear_filter()
        _pump(60)
        self.assertEqual(state(), before)
        self.assertEqual(_signature(sk), sig)

    # -- check 8: stale -------------------------------------------------------------------------------------------------------
    def _stale_setup(self, name):
        sk = self.new_sketch(name, self.corner)
        p = self.open_rel(sk)
        self.select(sk, "Edge1")
        self._row(0)
        p.filter_to_element()
        return sk, p

    def _settled(self, sk, p, geo=0):
        """After an event the rows are those of the sketch as it is now, and nothing selected is gone."""
        self.assertTrue(_wait(lambda: not p._resync_pending))
        _pump(40)
        n = len(sk.Constraints)
        for s in _selected_names():
            if s.startswith("Constraint"):
                self.assertLessEqual(int(s[len("Constraint"):]), n, s)

    def testStaleWhenAConstraintIsAdded(self):
        sk, p = self._stale_setup("RelStaleAdd")
        sk.addConstraint(Sketcher.Constraint("Distance", 0, 20.0))
        self.assertIsNone(SketcherGui.getConstraintFilter())  # cleared at once, not after a timer
        self.assertEqual(p.tree.topLevelItemCount(), 0)
        self._settled(sk, p)
        self.assertEqual(_panel_rows(p), expected_rows(sk, 0, None))
        self.assertIsNone(SketcherGui.getConstraintFilter())
        self.assertFalse(p.banner.isVisible())
        self.assertIn("filter was cleared", p.status.text())

    def testStaleWhenAConstraintIsDeleted(self):
        sk, p = self._stale_setup("RelStaleDel")
        sk.delConstraint(4)  # Horizontal on Edge1
        self.assertEqual(p.tree.topLevelItemCount(), 0)
        self._settled(sk, p)
        self.assertEqual(_panel_rows(p), expected_rows(sk, 0, None))
        self.assertNotIn("Horizontal", " ".join(h for h, _ in _panel_rows(p)))
        self.assertIsNone(SketcherGui.getConstraintFilter())

    def testStaleWhenAConstraintChangesKeepsTheFilterBecauseNothingMoved(self):
        sk, p = self._stale_setup("RelStaleSet")
        want = SketcherGui.getConstraintFilter()
        sk.setDatum(DATUM_X, _length(25.0))
        self.assertEqual(p.tree.topLevelItemCount(), 0)
        self._settled(sk, p)
        self.assertIn("= 25 mm", " ".join(h for h, _ in _panel_rows(p)))
        self.assertEqual(_panel_rows(p), expected_rows(sk, 0, None))
        self.assertEqual(SketcherGui.getConstraintFilter(), want)
        self.assertTrue(p.banner.isVisible())

    def testStaleAfterUndoAndRedo(self):
        sk, p = self._stale_setup("RelStaleUndo")
        doc = sk.Document
        doc.openTransaction("test change")
        sk.addConstraint(Sketcher.Constraint("Distance", 0, 20.0))
        doc.commitTransaction()
        self._settled(sk, p)
        self.select(sk, "Edge1")
        self._row(0)
        p.filter_to_element()
        doc.undo()
        self.assertEqual(p.tree.topLevelItemCount(), 0)
        self.assertIsNone(SketcherGui.getConstraintFilter())
        self._settled(sk, p)
        self.assertEqual(_panel_rows(p), expected_rows(sk, 0, None))
        doc.redo()
        self._settled(sk, p)
        self.assertEqual(_panel_rows(p), expected_rows(sk, 0, None))

    def testStaleWhenElementsAreDeleted(self):
        sk, p = self._stale_setup("RelStaleGeo")
        sk.delGeometry(3)
        self.assertEqual(p.tree.topLevelItemCount(), 0)
        self._settled(sk, p)
        self.assertIsNone(SketcherGui.getConstraintFilter())

    def testStaleWhenLeavingEditMode(self):
        sk, p = self._stale_setup("RelStaleLeave")
        Gui.ActiveDocument.resetEdit()
        self.assertTrue(_wait(lambda: p.state == "closed"))
        self.assertIsNone(self.R.current_panel())
        self.assertIsNone(SketcherGui.getConstraintFilter())

    def testStaleWhenTheDocumentIsClosed(self):
        sk, p = self._stale_setup("RelStaleClose")
        FreeCAD.closeDocument("RelStaleClose")
        self.assertFalse(p.focus(0))
        self.assertTrue(_wait(lambda: p.state == "closed"))
        self.assertIsNone(self.R.current_panel())
        FreeCAD.setActiveDocument(self.rig.doc.Name)

    def testObserversAreRemovedOnClose(self):
        sk = self.new_sketch("RelObservers", self.corner)
        p = self.open_rel(sk)
        calls = []
        spies = (
            (p._dw, ("slotChangedObject", "slotUndoDocument", "slotRedoDocument", "slotDeletedObject", "slotDeletedDocument")),
            (p._sw, ("addSelection", "removeSelection", "clearSelection", "setSelection")),
            (p._gw, ("slotResetEdit", "slotInEdit", "slotActivateDocument", "slotDeletedDocument")),
        )
        for obj, names in spies:
            for n in names:
                setattr(obj, n, lambda *a, _n=n, **k: calls.append(_n))
        self.close_panel()
        calls.clear()
        sk.addConstraint(Sketcher.Constraint("Distance", 0, 20.0))
        Gui.Selection.addSelection(sk.Document.Name, sk.Name, "Edge1")
        Gui.Selection.clearSelection()
        sk.Document.undo()
        Gui.ActiveDocument.resetEdit()
        _pump(40)
        self.assertEqual(calls, [])

    def testReopeningAfterCloseStartsClean(self):
        sk = self.new_sketch("RelReopen", self.corner)
        p = self.open_rel(sk)
        self.select(sk, "Edge1")
        p.filter_to_element()
        self.close_panel()
        p2 = self.open_rel(sk)
        self.assertIsNot(p, p2)
        self.assertEqual(p2.tree.topLevelItemCount(), 0)
        self.assertIsNone(SketcherGui.getConstraintFilter())

    # -- entry point ------------------------------------------------------------------------------------------------------
    def testTheCommandIsActiveOnlyInTheEditor(self):
        sk = self.new_sketch("RelCmd", self.corner)
        cmd = Gui.Command.get(COMMAND)
        Gui.ActiveDocument = Gui.getDocument(sk.Document.Name)
        if Gui.ActiveDocument.getInEdit() is not None:
            Gui.ActiveDocument.resetEdit()
        _pump(30)
        self.assertFalse(cmd.isActive())
        self.edit(sk)
        self.assertTrue(cmd.isActive())

    # -- check 9: responsiveness ----------------------------------------------------------------------------------------------
    def testResponseAt5000Constraints(self):
        d = FreeCAD.newDocument("RelBig")
        self._extra_docs.append("RelBig")
        sk, _ = Corpus.build_scale(d, "big", 5000)
        d.recompute()
        self.assertEqual(len(sk.Constraints), 5000)
        p = self.open_rel(sk)
        worst = {"show": 0.0, "on": 0.0, "off": 0.0}
        for name, (geo, pos) in (("Edge1", (0, None)), ("Edge40", (39, None)), ("RootPoint", (-1, 1)), ("H_Axis", (-1, None))):
            self.select(sk, name)
            t = time.time()
            p.show_element(geo, pos)
            worst["show"] = max(worst["show"], time.time() - t)
            self.assertEqual(_panel_rows(p), expected_rows(sk, geo, pos))
            if p.tree.topLevelItemCount():
                t = time.time()
                p.filter_to_element()
                worst["on"] = max(worst["on"], time.time() - t)
                t = time.time()
                p.clear_filter()
                worst["off"] = max(worst["off"], time.time() - t)
        t = time.time()
        p.filter_to_type("Coincident")
        worst["on"] = max(worst["on"], time.time() - t)
        t = time.time()
        p.clear_filter()
        worst["off"] = max(worst["off"], time.time() - t)
        print(
            "RELATIONS at 5000 constraints: worst show %.0f ms, filter on %.0f ms, off %.0f ms"
            % tuple(1000 * worst[k] for k in ("show", "on", "off"))
        )
        for k, v in worst.items():
            self.assertLess(v, RESPONSE_BAR, "%s took %.0f ms" % (k, v * 1000))
