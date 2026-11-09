# SPDX-License-Identifier: LGPL-2.1-or-later

# G04 slice 1 (checks 1 to 3, 5, 10 to 12): drawing layout, headless.
# Run with: FreeCADCmd -t TestTechDrawLayout   (also part of TestTechDrawApp)
#
# These tests read the drawing objects' properties and the page geometry directly; the sizes of annotation boxes come from
# DrawingLayout.annotationBoxes (font metrics), the estimate the layout places with.  What the GUI draws is measured by
# harness/g04/layout_metrics.py, not here.

import math
import os
import tempfile
import unittest

import FreeCAD as App
import Part

import DrawingLayout as DL
import PmiAuthoring as PA
import PmiDrawing as PD

from TDTest.PmiDrawingTest import cyl_face, make_page, pairs, planar_face, prism

X, Y, Z = App.Vector(1, 0, 0), App.Vector(0, 1, 0), App.Vector(0, 0, 1)
PREFERRED = (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0)
ROLES = ("dimension", "frame", "datum", "leader")


def overlap(a, b, eps=1e-6):
    return min(a[2], b[2]) - max(a[0], b[0]) > eps and min(a[3], b[3]) - max(a[1], b[1]) > eps


def group_of(page):
    return [o for o in page.Views if o.TypeId == "TechDraw::DrawProjGroup"][0]


def view_boxes(page):
    """{view name: (x0, y0, x1, y1)} page mm, from the visible edges and the view's position."""
    g = group_of(page)
    out = {}
    for v in g.Views:
        es = v.getVisibleEdges(True)
        px, py = g.X.Value + v.X.Value, g.Y.Value + v.Y.Value
        out[v.Name] = (
            px + min(e.BoundBox.XMin for e in es),
            py + min(e.BoundBox.YMin for e in es),
            px + max(e.BoundBox.XMax for e in es),
            py + max(e.BoundBox.YMax for e in es),
        )
    return out


def pmi_objects(doc):
    return [o for o in doc.Objects if "PmiRole" in o.PropertiesList and o.PmiRole in ROLES]


def positions(page):
    """Every X, Y and way point the layout writes, as a comparable dict."""
    out = {}
    g = group_of(page)
    out["group"] = (round(g.X.Value, 9), round(g.Y.Value, 9), g.Scale, g.ScaleType)
    for v in g.Views:
        out[v.Label] = (round(v.X.Value, 9), round(v.Y.Value, 9))
    for o in pmi_objects(page.Document):
        r = (round(o.X.Value, 9), round(o.Y.Value, 9))
        if o.PmiRole == "leader":
            r += tuple(round(c, 9) for w in o.WayPoints for c in (w.x, w.y))
        out[o.Name] = r
    return out


def leader_ends(page):
    """[(leader, page-mm end point of its last way point, page-mm position of its anchor)]: way points are page mm with y down."""
    g = group_of(page)
    out = []
    for o, item in pairs(page):
        if o.PmiRole != "leader":
            continue
        v = o.LeaderParent
        vw = PD._View(v, v.Type, g)
        s = float(g.Scale)
        px, py = g.X.Value + v.X.Value, g.Y.Value + v.Y.Value
        wp = o.WayPoints
        a = vw.u(o.PmiAnchor)
        out.append((o, (px + o.X.Value * s + wp[-1].x, py + o.Y.Value * s - wp[-1].y), (px + a.x * s, py + a.y * s)))
    return out


def build(doc):
    box = doc.addObject("Part::Box", "Box")
    box.Length, box.Width, box.Height = 80, 50, 20
    cyl = doc.addObject("Part::Cylinder", "Cyl")
    cyl.Radius, cyl.Height = 6, 20
    cyl.Placement.Base = App.Vector(100, 0, 0)
    doc.recompute()

    def face(normal, coord):
        return (box, [planar_face(box, normal, coord)])

    PA.addDatum("A", face(-Z, 0))
    PA.addSizeDimension([face(-X, 0), face(X, 80)], plus_minus=(0.1, -0.1))
    PA.addSizeDimension([face(-Y, 0), face(Y, 50)])
    PA.addSizeDimension([(cyl, [cyl_face(cyl)])], plus_minus=(0.05, -0.02))
    PA.addTolerance("Flatness", 0.05, face(Z, 20))
    PA.addTolerance("Perpendicularity", 0.1, face(X, 80), datums=["A"])
    PA.addTolerance("Position", 0.2, (cyl, [cyl_face(cyl)]), datums=["A"])
    return box, cyl


class DrawingLayoutTest(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("DrawingLayoutTest")
        self.doc.UndoMode = 1
        self.box, self.cyl = build(self.doc)
        self.page = make_page(self.doc)
        PD.project(self.page, [self.box, self.cyl])

    def tearDown(self):
        for n in list(App.listDocuments()):
            App.closeDocument(n)

    def annos(self):
        return [o for o in pmi_objects(self.doc) if o.PmiRole != "leader"]

    # ---- check 1 and ruling 2: scale and views
    def test_scale_is_the_largest_preferred_one_and_the_views_fit_apart(self):
        g = group_of(self.page)
        self.assertEqual(g.ScaleType, "Custom")
        self.assertIn(g.Scale, PREFERRED)
        sheet, W, H = DL.sheetOf(self.page)
        boxes = view_boxes(self.page)
        for b in boxes.values():
            self.assertTrue(b[0] >= sheet[0] - 1e-6 and b[1] >= sheet[1] - 1e-6 and b[2] <= sheet[2] + 1e-6 and b[3] <= sheet[3] + 1e-6, b)
        names = list(boxes)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = boxes[names[i]], boxes[names[j]]
                self.assertFalse(overlap(a, b), (names[i], names[j]))
                self.assertGreaterEqual(max(b[0] - a[2], a[0] - b[2], b[1] - a[3], a[1] - b[3]), 10.0, "views too close")
        lay, group = DL._prepare(self.page)
        for s in [s for s in PREFERRED if s > g.Scale + 1e-12]:
            self.assertFalse(lay.viewPositions(s)[1], "scale %g would have fitted" % s)
        self.assertTrue(lay.viewPositions(g.Scale)[1])

    def test_a_part_too_big_for_every_preferred_scale_keeps_its_scale_and_says_so(self):
        big = prism(self.doc, "Big", [(0, 0), (40000, 0), (40000, 100), (0, 100)], 100)
        page = make_page(self.doc)
        PD.project(page, big, layout=False)
        g = [o for o in page.Views if o.TypeId == "TechDraw::DrawProjGroup"]
        PA.addSizeDimension([(big, [planar_face(big, -X, 0)]), (big, [planar_face(big, X, 40000)])])
        PD.project(page, big, layout=False)
        before = group_of(page).Scale
        rep = DL.layout(page)
        self.assertFalse(rep["fits"])
        self.assertEqual(group_of(page).Scale, before)

    def test_a_small_part_is_enlarged(self):
        small = prism(self.doc, "Small", [(0, 0), (6, 0), (6, 4), (0, 4)], 3)
        PA.addSizeDimension([(small, [planar_face(small, -X, 0)]), (small, [planar_face(small, X, 6)])])
        page = make_page(self.doc)
        PD.project(page, small)
        self.assertGreaterEqual(group_of(page).Scale, 2.0)

    # ---- check 3 and ruling 3: determinism, boxes, leaders
    def test_layout_is_deterministic_and_idempotent(self):
        first = positions(self.page)
        DL.layout(self.page)
        self.assertEqual(positions(self.page), first)
        doc2 = App.newDocument("DrawingLayoutTest2")
        box, cyl = build(doc2)
        page2 = make_page(doc2)
        PD.project(page2, [box, cyl])
        self.assertEqual(positions(page2), first)

    def test_annotation_boxes_are_clear_of_each_other_and_inside_the_sheet(self):
        boxes = DL.annotationBoxes(self.page)
        self.assertEqual(len(boxes), len(self.annos()))
        sheet, W, H = DL.sheetOf(self.page)
        names = sorted(boxes)
        for n in names:
            b = boxes[n]
            self.assertTrue(b[0] >= sheet[0] and b[1] >= sheet[1] and b[2] <= sheet[2] and b[3] <= sheet[3], (n, b))
        self.assertEqual([(a, b) for i, a in enumerate(names) for b in names[i + 1 :] if overlap(boxes[a], boxes[b])], [])

    def test_leaders_made_without_a_layout_end_on_their_anchor_too(self):
        doc2 = App.newDocument("DrawingLayoutTest3")
        box, cyl = build(doc2)
        page = make_page(doc2)
        PD.project(page, [box, cyl], layout=False)
        ends = leader_ends(page)
        self.assertGreaterEqual(len(ends), 4)
        for o, end, anchor in ends:
            self.assertAlmostEqual(end[0], anchor[0], places=6)
            self.assertAlmostEqual(end[1], anchor[1], places=6)

    def test_leaders_end_on_their_anchor_and_start_on_their_annotation(self):
        boxes = DL.annotationBoxes(self.page)
        ends = leader_ends(self.page)
        self.assertGreaterEqual(len(ends), 4)
        g = group_of(self.page)
        for o, end, anchor in ends:
            self.assertAlmostEqual(end[0], anchor[0], places=6)
            self.assertAlmostEqual(end[1], anchor[1], places=6)
            an = [x for x in o.InList if x.TypeId == "TechDraw::DrawRichAnno"][0]
            b = boxes[an.Name]
            v = o.LeaderParent
            ox, oy = g.X.Value + v.X.Value + o.X.Value * g.Scale, g.Y.Value + v.Y.Value + o.Y.Value * g.Scale
            d = math.hypot(max(b[0] - ox, 0, ox - b[2]), max(b[1] - oy, 0, oy - b[3]))
            self.assertLessEqual(d, 1.0, "leader of %s starts %.2f mm from its box" % (an.Name, d))

    def test_every_annotation_is_near_what_it_labels(self):
        boxes = DL.annotationBoxes(self.page)
        ds = []
        for o, end, anchor in leader_ends(self.page):
            an = [x for x in o.InList if x.TypeId == "TechDraw::DrawRichAnno"][0]
            b = boxes[an.Name]
            ds.append(math.hypot(max(b[0] - anchor[0], 0, anchor[0] - b[2]), max(b[1] - anchor[1], 0, anchor[1] - b[3])))
        self.assertLessEqual(max(ds), 80.0)
        self.assertLessEqual(sorted(ds)[len(ds) // 2], 25.0)

    # ---- check 10 and ruling 5: pins
    def test_moved_annotations_are_pinned_and_kept_by_layout_and_refresh(self):
        annos = sorted(self.annos(), key=lambda o: o.Name)
        dim = [o for o in annos if o.PmiRole == "dimension"][0]
        frame = [o for o in annos if o.PmiRole == "frame"][0]
        dim.X = dim.X.Value + 7.0
        frame.X = frame.X.Value - 5.0
        moved = (dim.X.Value, dim.Y.Value, frame.X.Value, frame.Y.Value)
        rep = DL.layout(self.page)
        self.assertEqual(rep["pinned"], 2)
        self.assertEqual((dim.X.Value, dim.Y.Value, frame.X.Value, frame.Y.Value), moved)
        self.assertTrue(dim.PmiPinned and frame.PmiPinned)
        self.assertTrue(all(not o.PmiPinned for o in annos if o not in (dim, frame)))
        for o, end, anchor in leader_ends(self.page):  # the leader of a pinned frame still ends on its anchor
            self.assertAlmostEqual(end[0], anchor[0], places=6)
            self.assertAlmostEqual(end[1], anchor[1], places=6)
        item = [i for i in self.doc.Objects if i.TypeId == "Part::PmiTolerance"][0]
        item.Value = item.Value + 0.01
        before = positions(self.page)
        PD.refresh(self.page)
        self.assertEqual(positions(self.page), before)  # refresh moves nothing: not the pins, not the leaders it keeps on their anchors
        self.assertEqual((dim.X.Value, dim.Y.Value, frame.X.Value, frame.Y.Value), moved)
        DL.layout(self.page)
        self.assertEqual((dim.X.Value, dim.Y.Value, frame.X.Value, frame.Y.Value), moved)

    def test_free_annotations_keep_clear_of_a_pin(self):
        boxes = DL.annotationBoxes(self.page)
        frame = [o for o in self.annos() if o.PmiRole == "frame"][0]
        dim = [o for o in self.annos() if o.PmiRole == "dimension"][0]
        fb, db = boxes[frame.Name], boxes[dim.Name]
        dim.X = dim.X.Value + ((fb[0] + fb[2]) - (db[0] + db[2])) / 2
        dim.Y = dim.Y.Value + ((fb[1] + fb[3]) - (db[1] + db[3])) / 2
        DL.layout(self.page)
        after = DL.annotationBoxes(self.page)
        self.assertTrue(dim.PmiPinned)
        for o in self.annos():
            if o is not dim:
                self.assertFalse(overlap(after[o.Name], after[dim.Name]), o.Name)

    def test_a_pin_keeps_the_scale_and_the_view_positions(self):
        g = group_of(self.page)
        g.Scale = 0.5
        self.doc.recompute()
        dim = [o for o in self.annos() if o.PmiRole == "dimension"][0]
        dim.X = dim.X.Value + 2.0
        views = [(v.X.Value, v.Y.Value) for v in g.Views]
        DL.layout(self.page)
        self.assertEqual(group_of(self.page).Scale, 0.5)
        self.assertEqual([(v.X.Value, v.Y.Value) for v in g.Views], views)

    def test_a_moved_leader_pins_its_frame(self):
        frame = [o for o in self.annos() if o.PmiRole == "frame"][0]
        ld = frame.AnnoParent
        ld.X = ld.X.Value + 3.0
        keep = (ld.X.Value, ld.Y.Value)
        PD.refresh(self.page)  # refresh alone, before any layout, leaves a moved leader where the user put it
        self.assertEqual((ld.X.Value, ld.Y.Value), keep)
        DL.layout(self.page)
        self.assertTrue(frame.PmiPinned)
        self.assertEqual((ld.X.Value, ld.Y.Value), keep)
        PD.refresh(self.page)
        self.assertEqual((ld.X.Value, ld.Y.Value), keep)

    def test_pins_and_positions_survive_save_and_reopen(self):
        dim = [o for o in self.annos() if o.PmiRole == "dimension"][0]
        dim.Y = dim.Y.Value + 4.0
        DL.layout(self.page)
        want = (dim.Name, dim.X.Value, dim.Y.Value)
        path = os.path.join(tempfile.mkdtemp(), "layout.FCStd")
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)
        self.doc = App.openDocument(path)
        d2 = self.doc.getObject(want[0])
        self.assertTrue(d2.PmiPinned)
        self.assertEqual((d2.X.Value, d2.Y.Value), want[1:])
        page = [o for o in self.doc.Objects if o.TypeId == "TechDraw::DrawPage"][0]
        DL.layout(page)
        self.assertEqual((d2.X.Value, d2.Y.Value), want[1:])

    def test_layout_is_one_undo_step(self):
        first = positions(self.page)
        g = group_of(self.page)
        g.Scale = 1.0  # a hand edit that the layout will undo
        self.doc.recompute()
        edited = positions(self.page)
        DL.layout(self.page)
        self.assertEqual(positions(self.page), first)
        self.doc.undo()
        self.assertEqual(positions(self.page), edited)

    # ---- check 11 and ruling 6: clear and delete
    def test_clear_removes_a_view_or_a_page_in_one_transaction_and_undo_restores(self):
        n = len(pmi_objects(self.doc))
        g = group_of(self.page)
        names = PD.clear(self.page, g.Views[0])
        self.assertGreater(len(names), 0)
        self.assertEqual(self.doc.UndoNames[0], "PMI clear")
        self.assertEqual(len(pmi_objects(self.doc)), n - len(names))
        self.assertGreater(len(pmi_objects(self.doc)), 0)  # the annotations of the other views stay
        self.doc.undo()
        self.assertEqual(len(pmi_objects(self.doc)), n)
        PD.clear(self.page)
        self.assertEqual(self.doc.UndoNames[0], "PMI clear")
        self.assertEqual(pmi_objects(self.doc), [])
        self.assertGreaterEqual(len(group_of(self.page).Views), 3)  # the views stay
        self.doc.undo()
        self.assertEqual(len(pmi_objects(self.doc)), n)

    def test_after_clear_the_group_can_be_removed_and_refresh_finds_nothing_orphaned(self):
        g = group_of(self.page)
        PD.clear(self.page)
        self.doc.removeObject(g.Name)
        PD.refresh(self.page)
        self.assertEqual(pmi_objects(self.doc), [])

    def test_a_view_with_a_dimension_deletes_without_a_name_error(self):
        # stock TechDraw, no PMI code: DrawViewPart::unsetupObject used {} placeholders with a printf-style call
        doc = self.doc
        page = make_page(doc)
        v = doc.addObject("TechDraw::DrawViewPart", "PlainView")
        page.addView(v)
        v.Source = [self.box]
        v.Direction = Z
        doc.recompute()
        d = doc.addObject("TechDraw::DrawViewDimension", "PlainDim")
        d.Type = "DistanceX"
        d.References2D = [(v, "Vertex0"), (v, "Vertex2")]
        page.addView(d)
        doc.recompute()
        doc.removeObject(v.Name)  # raised NameError: Unknown document '{}' before the fix
        self.assertIsNone(doc.getObject("PlainView"))
        self.assertIsNone(doc.getObject("PlainDim"))
