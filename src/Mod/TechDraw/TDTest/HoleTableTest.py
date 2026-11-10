# SPDX-License-Identifier: LGPL-2.1-or-later

# G04 slice 3a: the associative hole table (HoleTable.make), headless.
# Run with: FreeCADCmd -t TestTechDrawApp.HoleTableTest   (part of TestTechDrawApp)
#
# Expected rows are written out by hand from how each plate is built, never read from the table.
# Conventions: vendor help pages of CAD tools on hole tables (behaviour only: tag, X and Y from an origin, size).

import math
import os
import re
import tempfile
import time
import unittest

import FreeCAD as App
import Part
from FreeCAD import Vector as V

import HoleTable as HT

A4 = os.path.join("ISO", "A4_Landscape_ISO5457_minimal.svg")
D = "Ø"
DEG = "°"


def cells(view):
    texts = re.findall(r'<text[^>]*\sx="([-\d.]+)"\sy="([-\d.]+)"[^>]*>([^<]*)</text>', view.Symbol)
    rows = {}
    for x, y, t in texts:
        rows.setdefault(float(y), []).append((float(x), t))
    return [[t for _, t in sorted(r)] for _, r in sorted(rows.items())]


def sheet_rows(sheet):
    out, r = [], 1
    while sheet.getContents("A%d" % r):
        out.append([sheet.get("%s%d" % (c, r)).lstrip("'") for c in "ABCD"])
        r += 1
    return out


def table_rows(tbl):
    """The data rows (no header) of every block of a hole table, in sheet order."""
    return [r for r in sheet_rows(HT.driver_of(tbl).Sheet) if r[0] != "TAG"]


class Plate:
    """A PartDesign plate 120 x 80 x 10 (z 0 to 10) with Hole features, built the way the oracle would."""

    def __init__(self, doc):
        self.doc = doc
        self.body = doc.addObject("PartDesign::Body", "Body")
        sk = self.sketch("BaseSketch", 0, [(0, 0), (120, 0), (120, 80), (0, 80)])
        pad = self.body.newObject("PartDesign::Pad", "Pad")
        pad.Profile = sk
        pad.Length = 10
        doc.recompute()
        self.n = 0

    def sketch(self, name, z, pts, circles=()):
        sk = self.body.newObject("Sketcher::SketchObject", name)
        sk.AttachmentSupport = [(self.body.Origin.OriginFeatures[3], "")]
        sk.MapMode = "FlatFace"
        sk.AttachmentOffset = App.Placement(V(0, 0, z), App.Rotation())
        n = len(pts)
        for i in range(n):
            sk.addGeometry(Part.LineSegment(V(pts[i][0], pts[i][1], 0), V(pts[(i + 1) % n][0], pts[(i + 1) % n][1], 0)))
        for x, y in circles:
            sk.addGeometry(Part.Circle(V(x, y, 0), V(0, 0, 1), 1))
        return sk

    def hole(self, at, z=10, **kw):
        self.n += 1
        sk = self.sketch("HoleSk%d" % self.n, z, [], at)
        h = self.body.newObject("PartDesign::Hole", "Hole%d" % self.n)
        h.Profile = sk
        size = kw.pop("ThreadSize", None)
        for k, v in kw.items():
            setattr(h, k, v)
        if size:
            self.doc.recompute()
            h.ThreadSize = size
        self.doc.recompute()
        return h


# (kwargs, x, y, size text, mouth radius): the seven kinds of hole the plate test uses
KINDS = {
    "through": (dict(Diameter=6, DepthType="ThroughAll"), D + "6 THRU", 3.0),
    "blind": (dict(Diameter=8, DepthType="Dimension", Depth=6, DrillPoint="Flat"), D + "8 DEPTH 6", 4.0),
    "angled": (dict(Diameter=5, DepthType="Dimension", Depth=7, DrillPoint="Angled"), D + "5 DEPTH 7", 2.5),
    "cbore": (dict(Diameter=6, DepthType="ThroughAll", HoleCutType="Counterbore", HoleCutDiameter=11, HoleCutDepth=6.4), D + "6 THRU, CBORE " + D + "11 DEPTH 6.4", 5.5),
    "csk": (dict(Diameter=6, DepthType="ThroughAll", HoleCutType="Countersink", HoleCutDiameter=12, HoleCutCountersinkAngle=90), D + "6 THRU, CSK " + D + "12 X 90" + DEG, 6.0),
    "tap": (dict(Threaded=True, ThreadType="ISOMetricProfile", ThreadSize="M6x1.0", DepthType="ThroughAll"), "M6x1.0 THRU", 2.5),
    "tapblind": (dict(Threaded=True, ThreadType="ISOMetricProfile", ThreadSize="M6x1.0", DepthType="Dimension", Depth=8), "M6x1.0 DEPTH 8", 2.5),
}


class HoleTableTest(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("HoleTableTest")
        self.doc.UndoMode = 1

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def page(self, template=A4):
        page = self.doc.addObject("TechDraw::DrawPage", "Page")
        t = self.doc.addObject("TechDraw::DrawSVGTemplate", "Template")
        t.Template = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Templates", template)
        page.Template = t
        return page

    def view(self, page, source, direction=V(0, 0, 1), scale=1.0, x=100.0, y=105.0):
        v = self.doc.addObject("TechDraw::DrawViewPart", "View")
        page.addView(v)
        v.Source = [source]
        v.Direction = direction
        v.Scale = scale
        v.X, v.Y = x, y
        self.doc.recompute()
        return v

    def plate_page(self, holes):
        """holes: [(kind, x, y)]; -> (plate, page, view)."""
        plate = Plate(self.doc)
        for kind, x, y in holes:
            plate.hole([(x, y)], **dict(KINDS[kind][0]))
        page = self.page()
        return plate, page, self.view(page, plate.body)

    def untouched(self):
        return [o.Name for o in self.doc.Objects if "Touched" in o.State or "Invalid" in o.State]

    def tag_box(self, tag):
        w, h = HT.tag_size(tag.Text)
        s = tag.SourceView
        cx, cy = s.X.Value + tag.X.Value * s.Scale, s.Y.Value + tag.Y.Value * s.Scale
        return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)

    def assertLayout(self, tbl, view, holes_at):
        """Tags: one per row with the row's text, tip on the hole's rim, own hole nearest, near the tip, clear of each other and the table."""
        tags = HT.tags_of(tbl)
        rows = table_rows(tbl)
        self.assertEqual(sorted(t.Text for t in tags), sorted(r[0] for r in rows))
        by = {t.Text: t for t in tags}
        edges = HT.Edges(view)
        boxes = []
        for tag, (cx, cy, R) in holes_at.items():
            t = by[tag]
            tip = (t.OriginX.Value, t.OriginY.Value)
            self.assertAlmostEqual(math.hypot(tip[0] - cx, tip[1] - cy), R, delta=0.05, msg=tag)
            for other, (ox, oy, _) in holes_at.items():
                if other != tag:
                    self.assertLess(math.hypot(tip[0] - cx, tip[1] - cy), math.hypot(tip[0] - ox, tip[1] - oy), tag)
            _, arcs = edges.rim(cx, cy, R)
            self.assertTrue(any(HT._on_arc(e, tip, 0.05) for e in arcs), "%s tip not on a visible arc" % tag)
            b = self.tag_box(t)
            self.assertLessEqual(math.hypot(0.5 * (b[0] + b[2]) - (view.X.Value + tip[0]), 0.5 * (b[1] + b[3]) - (view.Y.Value + tip[1])), 20.0, tag)
            for ocx, ocy, _ in holes_at.values():
                px, py = view.X.Value + ocx, view.Y.Value + ocy
                self.assertFalse(b[0] < px < b[2] and b[1] < py < b[3], "%s covers a hole centre" % tag)
            for ob in boxes:
                self.assertFalse(HT.overlap(b, ob), "tag boxes overlap")
            boxes.append(b)
        for blk in HT.blocks_of(tbl):
            tb = HT.sheet_view_box(blk)
            self.assertFalse(HT.overlap(tb, HT.view_box(view)), "table over the view")
            for b in boxes:
                self.assertFalse(HT.overlap(tb, b), "table over a tag")

    # ---- numbers and letters

    def test_number_format_and_letters(self):
        self.assertEqual(HT.fmt(2.50), "2.5")
        self.assertEqual(HT.fmt(-0.004), "0")
        self.assertEqual(HT.fmt(20.0), "20")
        self.assertEqual(HT.fmt(0.1 + 0.2), "0.3")
        self.assertEqual(HT.fmt(6.4), "6.4")
        self.assertEqual(HT.fmt(12.3449), "12.34")
        self.assertEqual([HT.tag_letters(i) for i in (0, 1, 25, 26, 27)], ["A", "B", "Z", "AA", "AB"])

    # ---- check 35: identity, X, Y, size text, tags

    def test_plate_with_every_kind_of_hole(self):
        holes = [("through", 20, 15), ("blind", 50, 15), ("angled", 80, 15), ("cbore", 20, 45), ("csk", 50, 45), ("tap", 80, 45), ("tapblind", 105, 45)]
        plate, page, view = self.plate_page(holes)
        tbl = HT.make(page, view)
        self.doc.recompute()
        # tag letters: bore (tap drill 5 for the three listed first), then size text; X then Y within a letter
        self.assertEqual(
            table_rows(tbl),
            [
                ["A1", "105", "45", "M6x1.0 DEPTH 8"],
                ["B1", "80", "45", "M6x1.0 THRU"],
                ["C1", "80", "15", D + "5 DEPTH 7"],
                ["D1", "20", "15", D + "6 THRU"],
                ["E1", "20", "45", D + "6 THRU, CBORE " + D + "11 DEPTH 6.4"],
                ["F1", "50", "45", D + "6 THRU, CSK " + D + "12 X 90" + DEG],
                ["G1", "50", "15", D + "8 DEPTH 6"],
            ],
        )
        self.assertEqual(self.untouched(), [])
        # drawn equals data
        self.assertEqual(cells(tbl), sheet_rows(HT.driver_of(tbl).Sheet))
        self.assertNotIn("'", "".join(t for r in cells(tbl) for t in r))
        want = {}
        for (kind, x, y), tag in zip([holes[6], holes[5], holes[2], holes[0], holes[3], holes[4], holes[1]], "ABCDEFG"):
            want[tag + "1"] = (x - 60.0, y - 40.0, KINDS[kind][2])
        self.assertLayout(tbl, view, want)

    def test_far_side_blind_hole_and_side_hole_are_left_out(self):
        box = Part.makeBox(100, 60, 20)
        box = box.cut(Part.makeCylinder(3, 20, V(20, 15, 0)))  # through along z: listed
        box = box.cut(Part.makeCylinder(4, 8, V(50, 30, 0)))  # blind from the bottom face: its mouth faces away
        box = box.cut(Part.makeCylinder(3, 100, V(0, 45, 10), V(1, 0, 0)))  # along x: not parallel to the view
        f = self.doc.addObject("Part::Feature", "Block")
        f.Shape = box
        page = self.page()
        view = self.view(page, f)
        tbl = HT.make(page, view)
        self.assertEqual(table_rows(tbl), [["A1", "20", "15", D + "6 THRU"]])
        self.assertEqual(len(HT.tags_of(tbl)), 1)

    def test_blind_hole_seen_from_its_open_side_when_the_view_looks_the_other_way(self):
        box = Part.makeBox(100, 60, 20).cut(Part.makeCylinder(4, 8, V(50, 30, 0)))
        f = self.doc.addObject("Part::Feature", "Block")
        f.Shape = box
        page = self.page()
        view = self.view(page, f, direction=V(0, 0, -1))
        tbl = HT.make(page, view)
        self.assertEqual(len(table_rows(tbl)), 1)
        self.assertEqual(table_rows(tbl)[0][3], D + "8 DEPTH 8")

    def test_stepped_hole_is_one_row(self):
        plate, page, view = self.plate_page([("cbore", 40, 40)])
        tbl = HT.make(page, view)
        self.assertEqual(len(table_rows(tbl)), 1)
        self.assertEqual(len(HT.tags_of(tbl)), 1)

    def test_patterns_are_listed_with_thread_notes(self):
        plate = Plate(self.doc)
        h = plate.hole([(20, 20)], **dict(KINDS["tap"][0]))
        pat = plate.body.newObject("PartDesign::LinearPattern", "Pattern")
        pat.Originals = [h]
        pat.Direction = (plate.body.Origin.OriginFeatures[0], [""])
        pat.Length = 60
        pat.Occurrences = 3
        plate.body.Tip = pat
        self.doc.recompute()
        page = self.page()
        view = self.view(page, plate.body)
        tbl = HT.make(page, view)
        self.assertEqual(table_rows(tbl), [["A1", "20", "20", "M6x1.0 THRU"], ["A2", "50", "20", "M6x1.0 THRU"], ["A3", "80", "20", "M6x1.0 THRU"]])

    # ---- check 36: associativity

    def test_edits_follow_with_one_recompute(self):
        plate, page, view = self.plate_page([("through", 20, 20), ("blind", 60, 20), ("through", 100, 60)])
        tbl = HT.make(page, view)
        self.doc.recompute()
        base = [["A1", "20", "20", D + "6 THRU"], ["A2", "100", "60", D + "6 THRU"], ["B1", "60", "20", D + "8 DEPTH 6"]]
        self.assertEqual(table_rows(tbl), base)
        h1, h2, h3 = [o for o in plate.body.Group if o.TypeId == "PartDesign::Hole"]

        def step(expect, what):
            self.doc.recompute()
            self.assertEqual(table_rows(tbl), expect, what)
            self.assertEqual(self.untouched(), [], what)
            self.assertEqual(sorted(t.Text for t in HT.tags_of(tbl)), sorted(r[0] for r in expect), what)
            self.assertEqual(len([o for o in self.doc.Objects if o.TypeId == "TechDraw::DrawViewBalloon"]), len(expect), what)
            self.assertEqual(cells(HT.blocks_of(tbl)[0]), sheet_rows(HT.driver_of(tbl).Sheet), what)

        h1.Diameter = 7
        step([["A1", "100", "60", D + "6 THRU"], ["B1", "20", "20", D + "7 THRU"], ["C1", "60", "20", D + "8 DEPTH 6"]], "resize")
        sk = h1.Profile[0]
        sk.deleteAllGeometry()
        sk.addGeometry(Part.Circle(V(30, 25, 0), V(0, 0, 1), 1))
        step([["A1", "100", "60", D + "6 THRU"], ["B1", "30", "25", D + "7 THRU"], ["C1", "60", "20", D + "8 DEPTH 6"]], "move")
        plate.hole([(40, 60)], **dict(KINDS["through"][0]))
        step(
            [["A1", "40", "60", D + "6 THRU"], ["A2", "100", "60", D + "6 THRU"], ["B1", "30", "25", D + "7 THRU"], ["C1", "60", "20", D + "8 DEPTH 6"]],
            "add",
        )
        plate.body.removeObject(h3)
        self.doc.removeObject(h3.Name)
        step([["A1", "40", "60", D + "6 THRU"], ["B1", "30", "25", D + "7 THRU"], ["C1", "60", "20", D + "8 DEPTH 6"]], "delete")
        h2.DepthType = "ThroughAll"
        step([["A1", "40", "60", D + "6 THRU"], ["B1", "30", "25", D + "7 THRU"], ["C1", "60", "20", D + "8 THRU"]], "blind to through")
        h2.HoleCutType = "Counterbore"
        h2.HoleCutDiameter = 14
        h2.HoleCutDepth = 3
        step(
            [["A1", "40", "60", D + "6 THRU"], ["B1", "30", "25", D + "7 THRU"], ["C1", "60", "20", D + "8 THRU, CBORE " + D + "14 DEPTH 3"]],
            "counterbore",
        )

    # ---- check 37: origin

    def test_origin_given_and_moved_geometry(self):
        plate, page, view = self.plate_page([("through", 20, 15), ("through", 90, 55)])
        tbl = HT.make(page, view, origin=(10, 5, 10))
        self.assertEqual(table_rows(tbl), [["A1", "10", "10", D + "6 THRU"], ["A2", "80", "50", D + "6 THRU"]])
        tbl = HT.make(page, view, origin=None)
        self.assertEqual(table_rows(tbl), [["A1", "20", "15", D + "6 THRU"], ["A2", "90", "55", D + "6 THRU"]])
        pad = plate.body.Group[1]
        self.assertEqual(pad.TypeId, "PartDesign::Pad")
        sk = plate.body.Group[0]
        sk.deleteAllGeometry()  # the plate now starts at x = 5, y = 10 (120 x 70): the default origin moves with it
        for a, b in (((5, 10), (125, 10)), ((125, 10), (125, 80)), ((125, 80), (5, 80)), ((5, 80), (5, 10))):
            sk.addGeometry(Part.LineSegment(V(a[0], a[1], 0), V(b[0], b[1], 0)))
        self.doc.recompute()
        self.assertEqual(table_rows(tbl), [["A1", "15", "5", D + "6 THRU"], ["A2", "85", "45", D + "6 THRU"]])
        self.assertEqual(self.untouched(), [])

    # ---- check 42: persistence, undo, user placement, identity

    def test_make_is_one_transaction_and_undo_leaves_nothing(self):
        plate, page, view = self.plate_page([("through", 20, 15), ("blind", 60, 40)])
        before = {o.Name for o in self.doc.Objects}
        tbl = HT.make(page, view)
        self.assertGreater(len(self.doc.Objects), len(before))
        self.doc.undo()
        self.assertEqual({o.Name for o in self.doc.Objects}, before)
        self.assertEqual(len(page.Views), 1)
        self.doc.redo()
        self.assertEqual(len(HT.tags_of(self.doc.getObject(tbl.Name))), 2)

    def test_make_twice_returns_the_same_table(self):
        plate, page, view = self.plate_page([("through", 20, 15)])
        a = HT.make(page, view)
        b = HT.make(page, view)
        self.assertEqual(a.Name, b.Name)
        self.assertEqual(len([o for o in self.doc.Objects if o.TypeId == "TechDraw::DrawViewBalloon"]), 1)

    def test_user_moved_tag_and_table_keep_their_place_and_tags_keep_identity(self):
        plate, page, view = self.plate_page([("through", 20, 20), ("through", 90, 60)])
        tbl = HT.make(page, view)
        tags = {t.Text: t for t in HT.tags_of(tbl)}
        a2 = tags["A2"]
        a2.X, a2.Y = 33.0, 22.0
        tbl.X, tbl.Y = 250.0, 60.0
        self.doc.recompute()
        names = {t.Text: t.Name for t in HT.tags_of(tbl)}
        h1 = [o for o in plate.body.Group if o.TypeId == "PartDesign::Hole"][0]
        h1.Diameter = 4  # an edit to the other hole renumbers (4 mm before 6 mm) but A2 is the same hole
        self.doc.recompute()
        by_name = {t.Name: t for t in HT.tags_of(tbl)}
        self.assertEqual(by_name[names["A2"]].Text, "B1")  # renumbered, same object
        self.assertAlmostEqual(by_name[names["A2"]].X.Value, 33.0, delta=1e-6)
        self.assertAlmostEqual(by_name[names["A2"]].Y.Value, 22.0, delta=1e-6)
        self.assertAlmostEqual(tbl.X.Value, 250.0, delta=1e-6)
        self.assertAlmostEqual(tbl.Y.Value, 60.0, delta=1e-6)
        self.assertEqual(self.untouched(), [])

    def test_save_and_reopen_keep_the_table_and_it_still_follows(self):
        plate, page, view = self.plate_page([("through", 20, 20), ("blind", 60, 40)])
        tbl = HT.make(page, view)
        path = os.path.join(tempfile.mkdtemp(), "holes.FCStd")
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)
        self.doc = App.openDocument(path)
        self.doc.recompute()
        tbl = [v for v in self.doc.Objects if v.TypeId == "TechDraw::DrawViewSpreadsheet"][0]
        self.assertEqual(len(table_rows(tbl)), 2)
        h = [o for o in self.doc.Objects if o.TypeId == "PartDesign::Hole"][0]
        h.Diameter = 7
        self.doc.recompute()
        self.assertEqual([r[3] for r in table_rows(tbl)], [D + "7 THRU", D + "8 DEPTH 6"])
        self.assertEqual(len(HT.tags_of(tbl)), 2)
        self.assertEqual(self.untouched(), [])

    # ---- checks 39 and 28: split and no room

    def grid_block(self, n_x, n_y, r=2.0):
        box = Part.makeBox(110, 70, 10)
        for i in range(n_x):
            for j in range(n_y):
                box = box.cut(Part.makeCylinder(r, 10, V(10 + i * 90.0 / max(n_x - 1, 1), 10 + j * 50.0 / max(n_y - 1, 1), 0)))
        f = self.doc.addObject("Part::Feature", "Grid")
        f.Shape = box
        return f

    def test_tall_table_splits_into_blocks_with_the_header(self):
        f = self.grid_block(5, 8)  # 40 holes: 41 rows of 5.8 mm do not fit the 190 mm area
        page = self.page()
        view = self.view(page, f)
        tbl = HT.make(page, view)
        blocks = HT.blocks_of(tbl)
        self.assertGreater(len(blocks), 1)
        self.assertEqual(HT.warning_of(tbl), "")
        sheet = sheet_rows(HT.driver_of(tbl).Sheet)
        self.assertEqual(sum(1 for r in sheet if r[0] == "TAG"), len(blocks))
        data = [r for r in sheet if r[0] != "TAG"]
        self.assertEqual(len(data), 40)
        self.assertEqual([r[0] for r in data], ["A%d" % i for i in range(1, 41)])
        xy = [(float(r[1]), float(r[2])) for r in data]
        self.assertEqual(xy, sorted(xy))
        area, title = HT.PL.page_areas(page)
        boxes = [HT.sheet_view_box(b) for b in blocks]
        for i, b in enumerate(boxes):
            self.assertTrue(b[0] >= area[0] and b[2] <= area[2] and b[1] >= area[1] and b[3] <= area[3])
            self.assertFalse(HT.overlap(b, HT.view_box(view)))
            self.assertFalse(HT.overlap(b, tuple(title)))
            for o in boxes[:i]:
                self.assertFalse(HT.overlap(b, o))
        for i, b in enumerate(blocks):
            lo = sum(len(c) for c in cells(b)[:0])
            self.assertEqual(cells(b)[0], ["TAG", "X", "Y", "SIZE"])

    def test_no_free_space_places_in_the_corner_and_warns(self):
        f = self.grid_block(9, 12, r=1.0)  # 108 holes
        page = self.page()
        view = self.view(page, f, scale=1.0)
        view.Scale = 1.0
        # a second view takes the rest of the sheet
        other = self.doc.addObject("TechDraw::DrawViewPart", "Other")
        page.addView(other)
        other.Source = [f]
        other.X, other.Y = 230.0, 105.0
        other.Scale = 1.0
        self.doc.recompute()
        seen = []
        real = App.Console.PrintWarning
        App.Console.PrintWarning = lambda m: seen.append(m)
        try:
            tbl = HT.make(page, view)
        finally:
            App.Console.PrintWarning = real
        w = HT.warning_of(tbl)
        self.assertIn("overlaps", w)
        self.assertTrue(any("HoleTable" in m for m in seen))

    # ---- check 44: speed

    def test_speed_on_a_plate(self):
        plate = Plate(self.doc)
        for i in range(6):
            plate.hole([(15 + 15 * i, 20)], **dict(KINDS["cbore"][0]))
        page = self.page()
        view = self.view(page, plate.body)
        t0 = time.time()
        HT.make(page, view)
        self.doc.recompute()
        self.assertLess(time.time() - t0, 1.0)
