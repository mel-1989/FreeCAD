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
        return HT.tag_box(tag.SourceView, tag)

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
            self.assertTrue(any(HT._on_arc(e, tip, 0.05, edges.k) for e in arcs), "%s tip not on a visible arc" % tag)
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

    # ---- scale, letters by diameter, tag size, pocket floor, polar pattern

    def test_scaled_view_and_letters_follow_the_diameter_not_the_text(self):
        plate = Plate(self.doc)
        plate.hole([(80, 20)], Diameter=10, DepthType="ThroughAll")
        plate.hole([(30, 20)], Diameter=9, DepthType="ThroughAll")
        page = self.page()
        view = self.view(page, plate.body, scale=0.5, x=90.0, y=110.0)
        tbl = HT.make(page, view)
        self.assertEqual(table_rows(tbl), [["A1", "30", "20", D + "9 THRU"], ["B1", "80", "20", D + "10 THRU"]])  # 9 before 10 although "10" < "9" as text
        tags = {t.Text: t for t in HT.tags_of(tbl)}
        for tag, (cx, cy, r) in {"A1": (-30.0, -20.0, 4.5), "B1": (20.0, -20.0, 5.0)}.items():
            t = tags[tag]
            self.assertAlmostEqual(math.hypot(t.OriginX.Value - cx, t.OriginY.Value - cy), r, delta=0.05)
        self.assertEqual(self.untouched(), [])
        area, title = HT.PL.page_areas(page)
        vb = HT.view_box(view)
        self.assertAlmostEqual(vb[2] - vb[0], 60.0, delta=0.5)  # the 120 mm plate at 1:2
        for blk in HT.blocks_of(tbl):
            self.assertFalse(HT.overlap(HT.sheet_view_box(blk), vb))
        for t in tags.values():
            b = self.tag_box(t)
            self.assertTrue(b[0] >= area[0] and b[2] <= area[2] and b[1] >= area[1] and b[3] <= area[3])
            self.assertFalse(HT.overlap(b, tuple(title)))

    def test_tag_box_is_at_least_the_measured_drawn_width(self):
        # drawn widths of a rectangular bubble's text measured in the GUI in run 088 (page mm): the box must not be narrower
        for text, drawn in (("A1", 7.204), ("A12", 10.326), ("B12", 10.110), ("AA3", 12.246)):
            self.assertGreaterEqual(HT.tag_size(text)[0], drawn, text)

    def test_hole_on_a_pocket_floor(self):
        box = Part.makeBox(100, 60, 20).cut(Part.makeBox(60, 40, 12, V(20, 10, 8)))  # pocket 12 deep, floor at z = 8
        box = box.cut(Part.makeCylinder(3, 8, V(50, 30, 0)))  # hole from the pocket floor through the rest
        f = self.doc.addObject("Part::Feature", "Pocket")
        f.Shape = box
        page = self.page()
        view = self.view(page, f)
        tbl = HT.make(page, view)
        self.assertEqual(table_rows(tbl), [["A1", "50", "30", D + "6 THRU"]])

    def test_polar_pattern(self):
        plate = Plate(self.doc)
        sk = plate.body.Group[0]
        sk.deleteAllGeometry()  # centre the plate on the origin so the Z axis is a pattern axis through its middle
        for a, b in (((-60, -40), (60, -40)), ((60, -40), (60, 40)), ((60, 40), (-60, 40)), ((-60, 40), (-60, -40))):
            sk.addGeometry(Part.LineSegment(V(a[0], a[1], 0), V(b[0], b[1], 0)))
        self.doc.recompute()
        h = plate.hole([(30, 0)], **dict(KINDS["through"][0]))
        pat = plate.body.newObject("PartDesign::PolarPattern", "Polar")
        pat.Originals = [h]
        pat.Axis = (plate.body.Origin.OriginFeatures[2], [""])
        pat.Angle = 360
        pat.Occurrences = 4
        plate.body.Tip = pat
        self.doc.recompute()
        page = self.page()
        view = self.view(page, plate.body)
        tbl = HT.make(page, view)
        self.assertEqual(
            table_rows(tbl),
            [["A1", "30", "40", D + "6 THRU"], ["A2", "60", "10", D + "6 THRU"], ["A3", "60", "70", D + "6 THRU"], ["A4", "90", "40", D + "6 THRU"]],
        )

    def test_listing_is_independent_of_scale_type_and_scale(self):
        # run 088 oracle: with ScaleType Custom getVisibleEdges(True) is multiplied by Scale while projectPoint is not
        for stype, scale in (("Page", 1.0), ("Custom", 0.5), ("Custom", 0.2), ("Custom", 2.0)):
            plate = Plate(self.doc)
            plate.hole([(20, 15)], **dict(KINDS["through"][0]))
            plate.hole([(90, 55)], **dict(KINDS["cbore"][0]))
            page = self.page()
            v = self.doc.addObject("TechDraw::DrawViewPart", "View")
            page.addView(v)
            v.Source = [plate.body]
            v.Direction = V(0, 0, 1)
            v.ScaleType = stype
            v.Scale = scale
            v.X, v.Y = 100.0, 105.0
            self.doc.recompute()
            tbl = HT.make(page, v)
            what = "%s %s" % (stype, scale)
            self.assertEqual(
                table_rows(tbl), [["A1", "20", "15", D + "6 THRU"], ["B1", "90", "55", D + "6 THRU, CBORE " + D + "11 DEPTH 6.4"]], what
            )
            self.assertEqual(len(HT.tags_of(tbl)), 2, what)
            edges = HT.Edges(v)
            self.assertAlmostEqual(edges.box[2] - edges.box[0], 120.0, delta=0.01, msg=what)  # unscaled whatever the view did
            vb = HT.view_box(v)
            self.assertAlmostEqual(vb[2] - vb[0], 120.0 * scale, delta=0.05, msg=what)
            t = {x.Text: x for x in HT.tags_of(tbl)}["B1"]
            self.assertAlmostEqual(math.hypot(t.OriginX.Value - 30.0, t.OriginY.Value - 15.0), 5.5, delta=0.05, msg=what)
            _, arcs = edges.rim(30.0, 15.0, 5.5)
            self.assertTrue(any(HT._on_arc(e, (t.OriginX.Value, t.OriginY.Value), 0.05, edges.k) for e in arcs), what)
            self.assertEqual(self.untouched(), [], what)
            self.doc.removeObject(tbl.Name)

    def test_undo_of_make_leaves_a_page_made_after_an_earlier_table_edit(self):
        # run 088 oracle (check 42): an edit that removes a tag removes it from the observer, outside any transaction; the implicit
        # transaction that opens must not stay pending and swallow the next page, or one undo of make deletes that page
        plate, page, view = self.plate_page([("through", 20, 15), ("blind", 60, 40)])
        HT.make(page, view)
        self.doc.recompute()
        holes = [o for o in plate.body.Group if o.TypeId == "PartDesign::Hole"]
        plate.body.removeObject(holes[1])
        self.doc.removeObject(holes[1].Name)
        self.doc.recompute()
        self.assertFalse(self.doc.HasPendingTransaction)
        page2 = self.page()
        view2 = self.view(page2, plate.body)
        before = {o.Name for o in self.doc.Objects}
        HT.make(page2, view2)
        self.doc.recompute()
        self.doc.undo()
        self.doc.recompute()
        self.assertEqual({o.Name for o in self.doc.Objects}, before)
        HT.make(page2, view2)  # page and view are still alive
        self.assertEqual(len(table_rows(HT.blocks_of(page2.Views[-1])[0])), 1)

    # ---- run 089: transactions, near-parallel holes, rotation, tag placement, and the run 088 mutant survivors

    def solid_page(self, shape, scale=1.0):
        obj = self.doc.addObject("Part::Feature", "Solid")
        obj.Shape = shape
        page = self.page()
        return obj, page, self.view(page, obj, scale=scale)

    def test_make_joins_a_callers_open_transaction(self):
        # check 47: neither commit nor abort the caller's transaction; one undo undoes the caller's whole macro
        plate, page, view = self.plate_page([("through", 20, 15)])
        before = {o.Name for o in self.doc.Objects}
        self.doc.openTransaction("macro")
        page2 = self.page()
        view2 = self.view(page2, plate.body)
        HT.make(page2, view2)
        self.assertTrue(self.doc.HasPendingTransaction, "make closed the caller's transaction")
        self.doc.commitTransaction()
        self.assertGreater(len(self.doc.Objects), len(before))
        self.doc.undo()
        self.assertEqual({o.Name for o in self.doc.Objects}, before)

    def test_make_does_not_abort_a_callers_transaction_on_failure(self):
        plate, page, view = self.plate_page([("through", 20, 15)])
        self.doc.openTransaction("macro")
        real = HT.drivers_on

        def boom(page):
            raise RuntimeError("boom")

        HT.drivers_on = boom
        try:
            self.assertRaises(RuntimeError, HT.make, page, view)
        finally:
            HT.drivers_on = real
        self.assertIsNotNone(App.getActiveTransaction())
        self.doc.abortTransaction()

    def tilted(self, eps):
        box = Part.makeBox(100, 60, 20)
        cyl = Part.makeCylinder(3, 40, V(50, 30, -5), V(math.sin(eps), 0, math.cos(eps)))
        return self.solid_page(box.cut(cyl))

    def test_near_parallel_holes_are_listed_up_to_the_axis_tolerance(self):
        # check 48: ruling 23's 1e-6; the rim of a hole a hair off the view direction is an ellipse to TechDraw
        for eps in (1e-10, 1e-8, 5e-7):
            self.setUp()
            obj, page, view = self.tilted(eps)
            tbl = HT.make(page, view)
            self.assertEqual(len(table_rows(tbl)), 1, "tilt %g" % eps)
            self.assertEqual(len(HT.tags_of(tbl)), 1, "tilt %g" % eps)
            t = HT.tags_of(tbl)[0]
            self.assertAlmostEqual(math.hypot(t.OriginX.Value, t.OriginY.Value), 3.0, delta=0.05)  # the hole is at the view's centre
            self.tearDown()
        for eps in (1.5e-6, 1e-5):
            self.setUp()
            obj, page, view = self.tilted(eps)
            self.assertEqual(len(table_rows(HT.make(page, view))), 0, "tilt %g" % eps)
            self.tearDown()
        self.setUp()

    def test_cover_threshold_is_half_the_circumference(self):
        # ruling 23 (amended): a rim counts when its visible arcs sum to at least 50% of the circumference
        obj, page, view = self.solid_page(Part.makeBox(100, 60, 20).cut(Part.makeCylinder(3, 40, V(50, 30, -5))))

        class Stub:
            def __init__(self, cover):
                self.cover = cover

            def rim(self, cx, cy, r):
                return self.cover, []

        for cover, listed in ((0.0, 0), (0.49, 0), (0.4999, 0), (0.5, 1), (0.51, 1), (1.0, 1)):
            self.assertEqual(len(HT.listed_holes(view, Stub(cover))), listed, cover)

    def csk_plate(self, included):
        half = math.radians(included / 2.0)
        h = 3.0 / math.tan(half)  # cone from the mouth (r 6) down to the bore (r 3)
        box = Part.makeBox(100, 60, 20)
        cyl = Part.makeCylinder(3, 40, V(50, 30, -5))
        cone = Part.makeCone(3, 6, h, V(50, 30, 20 - h), V(0, 0, 1))
        return self.solid_page(box.cut(cyl).cut(cone))

    def test_countersink_angle_is_rounded_to_a_whole_degree(self):
        for included, text in ((90.0, "90"), (100.4, "100"), (100.6, "101"), (118.0, "118")):
            self.setUp()
            obj, page, view = self.csk_plate(included)
            rows = table_rows(HT.make(page, view))
            self.assertEqual(rows[0][3], D + "6 THRU, CSK " + D + "12 X " + text + DEG, included)
            self.tearDown()
        self.setUp()

    def test_back_bore_clause(self):
        # a wider bore opening on the far side of the base bore: "BACK Ø{d} DEPTH {its own length}"
        box = Part.makeBox(100, 60, 20)
        shape = box.cut(Part.makeCylinder(3, 40, V(50, 30, -5))).cut(Part.makeCylinder(5, 4, V(50, 30, 0)))
        obj, page, view = self.solid_page(shape)
        self.assertEqual(table_rows(HT.make(page, view))[0][3], D + "6 THRU, BACK " + D + "10 DEPTH 4")

    def test_a_tag_stays_with_its_hole_below_half_a_millimetre_and_is_new_above(self):
        # ruling 33
        plate, page, view = self.plate_page([("through", 20, 20), ("through", 90, 60)])
        tbl = HT.make(page, view)
        names = {t.Text: t.Name for t in HT.tags_of(tbl)}
        sk = [o for o in plate.body.Group if o.TypeId == "Sketcher::SketchObject" and o.Name.startswith("HoleSk")][0]  # the (20, 20) hole
        sk.AttachmentOffset = App.Placement(V(0.3, 0, 10), App.Rotation())
        self.doc.recompute()
        now = {t.Text: t.Name for t in HT.tags_of(HT.blocks_of(tbl)[0])}
        self.assertEqual(now, names, "moved 0.3 mm: same tags")
        sk.AttachmentOffset = App.Placement(V(0.8, 0, 10), App.Rotation())
        self.doc.recompute()
        now = {t.Text: t.Name for t in HT.tags_of(HT.blocks_of(tbl)[0])}
        self.assertEqual(sorted(now), sorted(names))
        self.assertNotEqual(now["A1"], names["A1"], "moved 0.8 mm: a new tag for the hole")
        self.assertEqual(now["A2"], names["A2"])
        self.assertEqual(self.untouched(), [])

    def test_rotation_leaves_the_rows_and_the_tips_follow_the_drawn_view(self):
        # check 50 (ruling 34)
        plate, page, view = self.plate_page([("through", 20, 15), ("blind", 90, 50)])
        tbl = HT.make(page, view)
        want = table_rows(tbl)
        for rot in (30.0, 90.0):
            view.Rotation = rot
            self.doc.recompute()
            self.assertEqual(table_rows(tbl), want, rot)
            edges = HT.Edges(view)
            by = {t.Text: t for t in HT.tags_of(tbl)}
            self.assertEqual(sorted(by), sorted(r[0] for r in want))
            for tag, (px, py, R) in {"A1": (-40.0, -25.0, 3.0), "B1": (30.0, 10.0, 4.0)}.items():
                cx, cy = HT._rot((px, py), rot)
                t = by[tag]
                self.assertAlmostEqual(math.hypot(t.OriginX.Value - cx, t.OriginY.Value - cy), R, delta=0.05, msg="%s at %s" % (tag, rot))
                _, arcs = edges.rim(cx, cy, R)
                self.assertTrue(any(HT._on_arc(e, (t.OriginX.Value, t.OriginY.Value), 0.05, edges.k) for e in arcs), "%s tip not on the drawn rim at %s" % (tag, rot))
            self.assertEqual(self.untouched(), [], rot)

    def test_tags_are_clear_of_the_tables_and_of_each_other_on_a_dense_part(self):
        # check 38 in miniature: pairs of 3 mm holes 6 mm apart in a row of four (tags 13 mm tall), every tag inside the drawing area, clear of the others,
        # the table and the title block, covering no hole centre, within 35 mm of its tip, no two leaders crossing
        box = Part.makeBox(60, 30, 10)
        for i in range(4):
            for j in range(2):
                box = box.cut(Part.makeCylinder(1.5, 30, V(10 + i * 14, 12 + j * 6, -5)))
        obj, page, view = self.solid_page(box)
        tbl = HT.make(page, view)
        self.assertEqual(HT.warning_of(tbl), "")
        tags = HT.tags_of(tbl)
        self.assertEqual(len(tags), 8)
        self.assertEqual(HT.driver_of(tbl).Violations, 0)
        area, title = HT.PL.page_areas(page)
        edges = HT.Edges(view)
        vb = HT.view_box(view)
        S = view.Scale
        rects = [(self.tag_box(t), t) for t in tags]
        centres = [(view.X.Value + t.HoleX * S, view.Y.Value + t.HoleY * S) for t in tags]
        legs = []
        for i, (b, t) in enumerate(rects):
            self.assertTrue(b[0] >= area[0] - 0.01 and b[2] <= area[2] + 0.01 and b[1] >= area[1] - 0.01 and b[3] <= area[3] + 0.01, t.Text)
            self.assertFalse(HT.overlap(b, tuple(title)), t.Text)
            for blk in HT.blocks_of(tbl):
                self.assertFalse(HT.overlap(b, HT.sheet_view_box(blk)), t.Text)
            for c in centres:
                self.assertFalse(b[0] < c[0] < b[2] and b[1] < c[1] < b[3], "%s covers a hole centre" % t.Text)
            for b2, t2 in rects[i + 1 :]:
                self.assertFalse(HT.overlap(b, b2), "%s over %s" % (t.Text, t2.Text))
            tip = (view.X.Value + t.OriginX.Value * S, view.Y.Value + t.OriginY.Value * S)
            self.assertLessEqual(math.hypot(0.5 * (b[0] + b[2]) - tip[0], 0.5 * (b[1] + b[3]) - tip[1]), 35.0, t.Text)
            side = 1.0 if tip[0] > 0.5 * (b[0] + b[2]) else -1.0
            legs.append(((0.5 * (b[0] + b[2]) + side * (0.5 * (b[2] - b[0]) + HT.TAG_LEAD_MM), 0.5 * (b[1] + b[3]) + HT.TAG_ANCHOR_MM), tip))
        for i, (a1, a2) in enumerate(legs):
            for b1, b2 in legs[i + 1 :]:
                self.assertFalse(HT._seg_cross(a1, a2, b1, b2), "leaders cross")

    def test_layout_is_the_same_whatever_the_noise_in_the_inputs(self):
        # inputs are rounded to 0.001 mm in one place: nudging them by less than that changes nothing
        items = [{"tag": "A%d" % i, "c": (100.0 + 7 * i, 100.0), "R": 3.0, "tips": [(100.0 + 7 * i + 3 * math.cos(a), 100.0 + 3 * math.sin(a)) for a in (0, 1.5, 3, 4.5)]} for i in range(5)]
        area = (10.0, 10.0, 280.0, 200.0)
        a, _, _ = HT.plan_tags(items, area, [], (120.0, 100.0), 20.0)
        self.assertEqual(HT.r3(100.0004), HT.r3(100.0001))
        b, _, _ = HT.plan_tags(items, area, [], (120.0, 100.0), 20.0)
        self.assertEqual(a, b)

    # ---- run 089 repair round: survivors of the verifier's mutants

    def test_edge_signature_follows_positions_not_only_counts(self):
        class Stub:
            Scale = 1.0
            Rotation = 0.0

            def __init__(self, x):
                self.e = [Part.makeCircle(3, V(x, 0, 0)), Part.makeLine(V(0, 0, 0), V(10, 0, 0))]

            def getVisibleEdges(self, conv=False):
                return self.e

        a, b, c = HT.Edges(Stub(20.0)).signature(), HT.Edges(Stub(20.0)).signature(), HT.Edges(Stub(25.0)).signature()
        self.assertEqual(a, b)
        self.assertNotEqual(a, c, "same count and lengths, the circle moved")

    def test_nearly_round_ellipse_counts_as_a_rim_and_a_real_one_does_not(self):
        class Stub:
            Scale = 1.0
            Rotation = 0.0

            def __init__(self, minor):
                el = Part.Ellipse(V(20, 0, 0), 3.0, minor)
                self.e = [el.toShape()]

            def getVisibleEdges(self, conv=False):
                return self.e

        self.assertEqual(sum(len(v) for v in HT.Edges(Stub(3.0 - 1e-6)).circles.values()), 1)
        self.assertEqual(sum(len(v) for v in HT.Edges(Stub(2.99)).circles.values()), 0)

    def test_a_half_round_notch_on_the_edge_is_not_a_hole(self):
        # a concave cylinder face covering half the circle (a notch cut at the plate's edge) is under the 95% a hole needs
        shape = Part.makeBox(100, 60, 20).cut(Part.makeCylinder(3, 40, V(0, 30, -5)))
        obj, page, view = self.solid_page(shape)
        self.assertEqual(table_rows(HT.make(page, view)), [])

    def test_tag_box_size_is_pinned_to_the_measurement(self):
        w, h = HT.tag_size("A1")
        self.assertEqual(h, 13.0)
        self.assertTrue(9.37 <= w <= 10.5, w)  # measured 7.4 to 9.4 for two characters
        self.assertTrue(11.66 <= HT.tag_size("C28")[0] <= 14.0)
        self.assertEqual(HT.TAG_ANCHOR_MM, 1.15)
        self.assertEqual(HT.TAG_LEAD_MM, 1.5)

    def test_distance_bounds_stay_inside_the_checked_20_and_35(self):
        self.assertEqual((HT.MAX_TAG_DIST, HT.MAX_TAG_DIST_DENSE), (20.0, 35.0))  # ruling 27 as it stands, no slack (ruling 43)
        items = [{"tag": "A%d" % i, "c": (100.0 + 20 * i, 100.0), "R": 3.0, "tips": [(100.0 + 20 * i + 3.0, 100.0)]} for i in range(4)]
        for limit in (HT.MAX_TAG_DIST, HT.MAX_TAG_DIST_DENSE):
            plan, bad, conflicts = HT.plan_tags(items, (10.0, 10.0, 280.0, 200.0), [], (130.0, 100.0), limit)
            self.assertEqual((bad, conflicts), (0, []))
            for (c, tip) in plan:
                self.assertLessEqual(math.hypot(c[0] - tip[0], c[1] - tip[1]), limit)

    def test_a_failed_make_aborts_its_own_transaction_only(self):
        plate, page, view = self.plate_page([("through", 20, 15)])
        before = {o.Name for o in self.doc.Objects}
        real = HT.drivers_on

        def boom(page):
            raise RuntimeError("boom")

        HT.drivers_on = boom
        try:
            self.assertRaises(RuntimeError, HT.make, page, view)
        finally:
            HT.drivers_on = real
        self.assertIsNone(App.getActiveTransaction())
        self.assertEqual({o.Name for o in self.doc.Objects}, before)

    def test_centre_follows_a_moved_part(self):
        # the cached centre is keyed on the placement too
        obj, page, view = self.solid_page(Part.makeBox(100, 60, 20).cut(Part.makeCylinder(3, 40, V(50, 30, -5))))
        a = HT.gc_of(view)
        obj.Placement = App.Placement(V(10, 0, 0), App.Rotation())
        self.doc.recompute()
        b = HT.gc_of(view)
        self.assertAlmostEqual(b.x - a.x, 10.0, delta=1e-6)

    def test_overfull_dense_layout_is_named_in_the_warning_and_stays_in_the_area(self):
        box = Part.makeBox(56, 46, 10)  # 80 holes 5 mm apart: more than 35 mm round the part can hold
        for i in range(10):
            for j in range(8):
                box = box.cut(Part.makeCylinder(1.0, 30, V(6 + i * 5, 6 + j * 5, -5)))
        obj, page, view = self.solid_page(box)
        tbl = HT.make(page, view)
        area, _ = HT.PL.page_areas(page)
        for t in HT.tags_of(tbl):
            b = self.tag_box(t)
            self.assertTrue(b[0] >= area[0] - 0.01 and b[2] <= area[2] + 0.01 and b[1] >= area[1] - 0.01 and b[3] <= area[3] + 0.01, t.Text)
        self.assertIn("no clean tag layout", HT.warning_of(tbl))

    def test_blocks_stand_side_by_side_in_reading_order(self):
        box = Part.makeBox(60, 40, 10)
        for i in range(8):
            for j in range(9):
                box = box.cut(Part.makeCylinder(1.0, 30, V(4 + i * 7, 4 + j * 4, -5)))
        obj, page, view = self.solid_page(box)
        tbl = HT.make(page, view)
        blocks = HT.blocks_of(tbl)
        self.assertGreaterEqual(len(blocks), 2)
        boxes = [HT.sheet_view_box(b) for b in sorted(blocks, key=lambda b: int(b.CellStart[1:]))]
        for a, b in zip(boxes, boxes[1:]):
            self.assertTrue(b[0] >= a[2] or b[3] <= a[1], "block %s not after %s" % (b, a))

    def test_tags_follow_when_an_edit_grows_the_table_into_more_blocks(self):
        plate, page, view = self.plate_page([("through", 20 + 6 * i, 15 + 6 * j) for i in range(5) for j in range(5)])
        tbl = HT.make(page, view)
        n0 = len(HT.blocks_of(tbl))
        for k in range(3):
            plate.hole([(100 + 4 * k, 70)], **dict(KINDS["cbore"][0]))
        self.doc.recompute()
        self.assertGreaterEqual(len(HT.blocks_of(tbl)), n0)
        tb = [HT.sheet_view_box(b) for b in HT.blocks_of(tbl)]
        for t in HT.tags_of(tbl):
            for b in tb:
                self.assertFalse(HT.overlap(self.tag_box(t), b), t.Text)

    # ---- run 090: the audit on the drawn geometry, the repair pass, tags and grown tables, block order (checks 51 to 54)

    @staticmethod
    def ring_item(tag, c, R=3.0):
        return {"tag": tag, "c": c, "R": R, "tips": [(c[0] + R * math.cos(k * math.pi / 12), c[1] + R * math.sin(k * math.pi / 12)) for k in range(24)]}

    AREA = (20.0, 10.0, 287.0, 200.0)

    def test_leader_starts_where_techdraw_draws_it(self):
        # QGIViewBalloon::drawBalloon with BubbleShape None and KinkLength 0: one segment from (anchor x +- (text width / 2 + 2 mm), anchor y) to
        # the tip, on the side facing the tip; the anchor is 1.15 mm above the box centre and the box is the text plus 0.5 mm each side
        w = HT.tag_size("A13")[0]
        right = HT.tag_tails("A13", (100.0, 50.0), (130.0, 50.0))
        left = HT.tag_tails("A13", (100.0, 50.0), (70.0, 50.0))
        self.assertAlmostEqual(right[0][0], 100.0 + w / 2 + 1.5, places=9)  # the widest the text can be
        self.assertAlmostEqual(left[0][0], 100.0 - w / 2 - 1.5, places=9)
        self.assertTrue(all(abs(t[1] - 51.15) < 1e-9 for t in right + left))
        self.assertLess(right[-1][0], right[0][0])  # the narrowest: the start is nearer the box

    def test_audit_is_exact_at_the_bound_and_names_each_fault_with_the_tag_texts(self):
        a, b = self.ring_item("A1", (100.0, 100.0)), self.ring_item("A2", (110.0, 100.0))
        items = [a, b]
        far = (100.0, 120.0)  # the box centre 20.4 mm from a tip on the rim at (103, 100)... measured below
        tip = (a["c"][0] + 3.0, a["c"][1])
        ok = [((tip[0] + 19.9, tip[1]), tip), ((110.0 + 3.0 + 19.9, 100.0), (113.0, 100.0))]
        self.assertEqual(HT.tag_conflicts(items, ok, self.AREA, [], 20.0), [])
        slack = [((tip[0] + 20.1, tip[1]), tip), ok[1]]  # 20.1 mm: the old 1.5 mm of slack let this through
        self.assertEqual(HT.tag_conflicts(items, slack, self.AREA, [], 20.0), ["A1 more than 20 mm from its hole"])
        self.assertEqual(HT.tag_conflicts(items, slack, self.AREA, [], 35.0), [])
        # outside the area, over an obstacle, covering a hole centre, over another tag, crossing leaders
        out = [((24.0, 100.0), (103.0, 100.0)), ok[1]]
        self.assertIn("A1 outside the drawing area", HT.tag_conflicts(items, out, self.AREA, [], 100.0))
        over = [((110.0, 130.0), (103.0, 100.0)), ((150.0, 130.0), (113.0, 100.0))]
        self.assertIn("A1 over hole table", HT.tag_conflicts(items, over, self.AREA, [("hole table", (100.0, 120.0, 120.0, 140.0))], 100.0))
        cover = [((101.0, 100.0), (103.0, 100.0)), ((150.0, 130.0), (113.0, 100.0))]
        self.assertIn("A1 covers a hole centre", HT.tag_conflicts(items, cover, self.AREA, [], 100.0))
        both = [((120.0, 130.0), (103.0, 100.0)), ((124.0, 130.0), (113.0, 100.0))]
        self.assertIn("A1 over A2", HT.tag_conflicts(items, both, self.AREA, [], 100.0))
        cross = [((100.0, 140.0), (103.0, 100.0)), ((140.0, 140.0), (107.0, 100.0))]  # A2's leader runs from the right of its box left to the hole
        items2 = [a, self.ring_item("A2", (110.0, 100.0))]
        crossed = [((60.0, 140.0), (97.0, 100.0)), ((150.0, 140.0), (107.0, 100.0))]  # fans from (60, 140) to (97, 100) and from (150, 140) to (107, 100)
        self.assertEqual([w for w in HT.tag_conflicts(items2, crossed, self.AREA, [], 100.0) if "cross" in w], [])
        swap = [((150.0, 140.0), (97.0, 100.0)), ((60.0, 140.0), (107.0, 100.0))]
        self.assertIn("leaders of A1 and A2 cross", HT.tag_conflicts(items2, swap, self.AREA, [], 100.0))

    def test_a_hairline_crossing_that_was_drawn_in_run_090_is_found(self):
        # tags A13 and A14 of a dense grid as the GUI drew them: the leaders crossed by 0.01 mm, which a leader modelled at one text width missed
        a13, a14 = self.ring_item("A13", (105.5, 91.0)), self.ring_item("A14", (108.0, 100.0))
        plan = [((110.423, 71.451), (104.876, 89.102)), ((111.926, 84.908), (105.6, 97.402))]
        self.assertIn("leaders of A13 and A14 cross", HT.tag_conflicts([a13, a14], plan, self.AREA, [], 100.0))

    def dense_grid_items(self, pitch, nx=6, ny=4, x0=75.0, y0=90.0):
        pts = [(x0 + pitch * i, y0 + pitch * j) for i in range(nx) for j in range(ny)]
        return [self.ring_item("A%d" % (n + 1), c) for n, c in enumerate(pts)]

    TITLE = ("the title block", (107.0, 10.0, 287.0, 46.0))

    def test_dense_grids_get_no_fault_at_10_and_7_mm_pitch_with_tables_in_place(self):
        # the corpus' v06 (10 mm pitch, 20 mm bound) and v07 (7 mm, 35 mm): clean in the first fit or after the repair, tables on the right
        tables = [("hole table", (166.1, 47.7, 208.4, 129.8)), ("hole table", (210.4, 47.7, 252.8, 123.9))]
        for pitch, limit in ((10.0, 20.0), (7.0, 35.0)):
            plan, bad, words = HT.plan_tags(self.dense_grid_items(pitch), self.AREA, [self.TITLE] + tables, (100.0, 105.0), limit)
            self.assertEqual((bad, words), (0, []), pitch)

    def test_the_repair_untangles_what_the_first_fit_left_and_never_makes_it_worse(self):
        tables = [("hole table", (25.0, 141.7, 108.9, 188.8)), ("hole table", (110.9, 141.7, 194.7, 188.8)), ("hole table", (152.1, 93.2, 236.0, 140.3)), ("hole table", (152.1, 62.1, 236.0, 91.7))]
        items = self.dense_grid_items(10.0)
        obs = [self.TITLE] + tables
        first = HT.plan_tags(items, self.AREA, obs, (100.0, 105.0), 20.0)  # includes the repair
        pl = HT._Plan(items, first[0], self.AREA, obs, 20.0)
        self.assertEqual(sorted(pl.audit()[0]), sorted(first[2]))  # the words returned are the audit of the plan returned
        # a plan with two crossed leaders: the repair swaps the boxes
        two = [self.ring_item("A1", (100.0, 100.0)), self.ring_item("A2", (140.0, 100.0))]
        start = [((150.0, 130.0), HT._best_tip(two[0], (150.0, 130.0))), ((90.0, 130.0), HT._best_tip(two[1], (90.0, 130.0)))]
        self.assertIn("leaders of A1 and A2 cross", HT.tag_conflicts(two, start, self.AREA, [], 100.0))
        fixed = HT.repair(two, start, self.AREA, [], (120.0, 100.0), 100.0)
        self.assertEqual(HT.tag_conflicts(two, fixed, self.AREA, [], 100.0), [])

    def test_the_repair_is_deterministic_and_leaves_a_tag_the_user_moved(self):
        items = self.dense_grid_items(10.0)
        items[5]["fixed"] = (items[5]["c"][0] + 14.0, items[5]["c"][1] + 14.0)
        obs = [self.TITLE, ("hole table", (166.1, 47.7, 208.4, 129.8))]
        runs = []
        for _ in range(2):
            HT._PLAN_MEMO.clear()
            fresh = [dict(it) for it in items]
            runs.append(HT.plan_tags(fresh, self.AREA, obs, (100.0, 105.0), 20.0))
        self.assertEqual(runs[0], runs[1])
        self.assertEqual(runs[0][0][5][0], items[5]["fixed"])

    def test_every_fault_left_is_named_and_none_is_dropped_from_the_warning(self):
        # an impossible page: 24 tags with a 40 mm wide area; more than 12 faults must all be in the words
        items = self.dense_grid_items(5.0, 6, 4, 100.0, 100.0)
        area = (90.0, 90.0, 140.0, 140.0)
        plan, bad, words = HT.plan_tags(items, area, [], (120.0, 120.0), 20.0)
        self.assertGreater(len(words), 12)
        self.assertEqual(sorted(words), sorted(HT.tag_conflicts(items, plan, area, [], 20.0)))
        self.assertTrue(all(any(k in w for k in ("outside the drawing area", " over ", "covers a hole centre", "more than 20 mm from its hole", "leaders of")) for w in words))

    def grid_part(self, nx, ny, pitch, cbore_at=()):
        box = Part.makeBox(nx * pitch + 40, ny * pitch + 40, 10)
        for i in range(nx):
            for j in range(ny):
                p = V(20 + i * pitch, 20 + j * pitch, 0)
                box = box.cut(Part.makeCylinder(3.0, 10, p))
                if (i, j) in cbore_at:
                    box = box.cut(Part.makeCylinder(5.0, 4, V(p.x, p.y, 6)))
        f = self.doc.addObject("Part::Feature", "Grid")
        f.Shape = box
        return f

    def test_no_tag_lies_over_a_table_that_grew_with_an_edit(self):
        # run 090 check 53: v06 / v01 at add_counterbore, where a new size text widened the table and a tag ended over it
        f = self.grid_part(6, 4, 10.0)
        page = self.page()
        view = self.view(page, f, x=95.0, y=100.0)
        tbl = HT.make(page, view)
        for n in range(3):
            self.assertEqual(self.tags_over_tables(tbl), [])
            shape = self.grid_part(6, 4, 10.0, cbore_at={(1, 1), (2, 2), (3, 1), (4, 2), (5, 3), (0, 3)}).Shape if n == 0 else None
            if shape is None:
                break
            f.Shape = shape
            self.doc.recompute()
        self.assertEqual(self.tags_over_tables(tbl), [])
        self.assertEqual(self.untouched(), [])

    def tags_over_tables(self, tbl):
        out = []
        for t in HT.tags_of(tbl):
            b = self.tag_box(t)
            for blk in HT.blocks_of(tbl):
                if HT.overlap(b, HT.sheet_view_box(blk)):
                    out.append(t.Text)
        return out

    def test_three_blocks_stand_left_to_right_with_aligned_tops_and_continuous_rows(self):
        # check 54: 60 holes on A4 split into at least three blocks (ctc_02's 84 on A3 splits the same way)
        box = Part.makeBox(80, 60, 10)
        for i in range(10):
            for j in range(6):
                box = box.cut(Part.makeCylinder(1.0, 30, V(6 + i * 7.5, 6 + j * 9.5, -5)))
        obj, page, view = self.solid_page(box)
        view.X, view.Y = 75.0, 105.0
        self.doc.recompute()
        tbl = HT.make(page, view)
        blocks = sorted(HT.blocks_of(tbl), key=lambda b: int(b.CellStart[1:]))
        self.assertGreaterEqual(len(blocks), 3)
        boxes = [HT.sheet_view_box(b) for b in blocks]
        for a, b in zip(boxes, boxes[1:]):
            self.assertGreaterEqual(b[0], a[2], "blocks are not left to right")  # reading order
        for b in boxes:
            self.assertAlmostEqual(b[3], boxes[0][3], delta=0.01)  # tops aligned
        sheet = sheet_rows(HT.driver_of(tbl).Sheet)
        self.assertEqual(sum(1 for r in sheet if r[0] == "TAG"), len(blocks))
        self.assertEqual([r[0] for r in sheet if r[0] != "TAG"], ["A%d" % i for i in range(1, 61)])  # rows continuous across the blocks, in order
        per = [len(cells(b)) - 1 for b in blocks]  # data rows each block draws
        self.assertEqual(sum(per), 60)
        self.assertEqual(per[:-1], [per[0]] * (len(per) - 1))  # an even split, the short block last
        self.assertLessEqual(per[-1], per[0])
        for b in blocks:
            self.assertEqual(cells(b)[0], ["TAG", "X", "Y", "SIZE"])
        first_tags = [cells(b)[1][0] for b in blocks]
        self.assertEqual(first_tags, ["A%d" % (1 + sum(per[:k])) for k in range(len(per))])
        for b in boxes:
            self.assertFalse(HT.overlap(b, HT.view_box(view)))

    def test_a_last_resort_tag_may_lie_over_a_view_but_never_over_a_table(self):
        # ruling 46: no free place within reach; the tag takes one over the view box (soft) rather than over the table (hard)
        item = self.ring_item("A1", (150.0, 100.0))
        obs = [("View", (95.0, 40.0, 205.0, 160.0)), ("hole table", (150.0, 40.0, 205.0, 160.0))]
        plan, bad, words = HT.plan_tags([item], self.AREA, obs, (100.0, 100.0), 20.0, {"View"})
        self.assertIn("A1 over View", words)
        self.assertNotIn("A1 over hole table", words)
        b = HT.tag_size("A1")
        box = (plan[0][0][0] - b[0] / 2, plan[0][0][1] - b[1] / 2, plan[0][0][0] + b[0] / 2, plan[0][0][1] + b[1] / 2)
        self.assertFalse(HT.overlap(box, obs[1][1]))

    def test_the_repair_returns_the_best_plan_it_saw_not_the_last(self):
        tables = [("hole table", (25.0, 141.7, 108.9, 188.8)), ("hole table", (110.9, 141.7, 194.7, 188.8)), ("hole table", (152.1, 93.2, 236.0, 140.3)), ("hole table", (152.1, 62.1, 236.0, 91.7))]
        items = self.dense_grid_items(10.0)
        obs = [self.TITLE] + tables
        for it in items:
            others = [i2["c"] for i2 in items if i2 is not it]
            it["tips"] = [q for q in it["tips"] if all(math.hypot(q[0] - c[0], q[1] - c[1]) > it["R"] + 0.02 for c in others)] or it["tips"]
        start, _ = HT._plan_once(items, self.AREA, obs, (100.0, 105.0), 20.0, HT.ATTEMPTS[0], set(), {"View"})
        before = len(HT.tag_conflicts(items, start, self.AREA, obs, 20.0))
        saved = (HT.REPAIR_KICK, HT.REPAIR_RESTARTS, HT.REPAIR_PASSES)
        try:
            HT.REPAIR_KICK, HT.REPAIR_RESTARTS, HT.REPAIR_PASSES = 2, 3, 30  # a kick every second pass makes the walk end somewhere worse than its best
            after = len(HT.tag_conflicts(items, HT.repair(items, start, self.AREA, obs, (100.0, 105.0), 20.0), self.AREA, obs, 20.0))
        finally:
            HT.REPAIR_KICK, HT.REPAIR_RESTARTS, HT.REPAIR_PASSES = saved
        self.assertLessEqual(after, before)

    def test_a_cavity_that_is_no_listed_hole_is_still_kept_clear_and_named(self):
        # run 090 repair: two holes that run into each other leave an arc no listed hole explains; a tag over its centre is a fault
        item = self.ring_item("A1", (100.0, 100.0))
        plan = [((150.0, 130.0), (103.0, 100.0))]
        self.assertEqual(HT.tag_conflicts([item], plan, self.AREA, [], 100.0), [])
        self.assertIn("A1 covers a hole centre", HT.tag_conflicts([item], plan, self.AREA, [], 100.0, avoid=[(151.0, 130.0)]))
        p2, bad, words = HT.plan_tags([self.ring_item("A1", (100.0, 100.0))], self.AREA, [], (90.0, 100.0), 20.0, None, avoid=[(112.0, 100.0)])
        self.assertEqual(words, [])
        b = HT.tag_size("A1")
        self.assertFalse(p2[0][0][0] - b[0] / 2 < 112.0 < p2[0][0][0] + b[0] / 2 and p2[0][0][1] - 6.5 < 100.0 < p2[0][0][1] + 6.5)

    def test_the_tag_is_within_the_bound_from_its_box_centre_and_from_its_anchor(self):
        item = self.ring_item("A1", (100.0, 100.0))
        tip = (103.0, 100.0)
        far = (tip[0] + 5.0, tip[1] - 19.95 + 0.0)  # straight below the tip: the anchor is nearer, the centre is the far one
        self.assertEqual(HT.tag_reach(far, tip), math.hypot(far[0] - tip[0], far[1] - tip[1]))
        above = (tip[0] + 5.0, tip[1] + 19.0)  # above: the anchor, 1.15 mm higher, is the far one
        self.assertAlmostEqual(HT.tag_reach(above, tip), math.hypot(5.0, 19.0 + 1.15), places=9)
        self.assertEqual(HT.tag_conflicts([item], [(above, tip)], self.AREA, [], 20.0), ["A1 more than 20 mm from its hole"])

    # ---- run 091: the detector repair (rulings 48 and 49, checks 56 to 58) ----

    def plate_with(self, cuts, w=60.0, h=40.0, t=20.0):
        shape = Part.makeBox(w, h, t)
        for c in cuts:
            shape = shape.cut(c)
        return self.solid_page(shape)

    @staticmethod
    def bore(r, x, y, z0=-5.0, length=40.0):
        return Part.makeCylinder(r, length, V(x, y, z0))

    def test_a_counterbore_that_cuts_a_neighbouring_through_hole_leaves_both_listed(self):
        # run 090 f02: the neighbour's counterbore takes part of the through hole's wall (area cover 0.94) and the hole is still a hole
        cuts = [self.bore(2.5, 20, 16), self.bore(3, 15, 11, 11.0, 30.0), self.bore(6, 15, 11, 16.0, 10.0)]
        obj, page, view = self.plate_with(cuts, 40.0, 40.0)
        tbl = HT.make(page, view)
        self.assertEqual(table_rows(tbl), [["A1", "20", "16", D + "5 THRU"], ["B1", "15", "11", D + "6 DEPTH 9, CBORE " + D + "12 DEPTH 4"]])
        self.assertNotIn("short", HT.warning_of(tbl))
        self.assertEqual(len(HT.tags_of(tbl)), 2)

    def test_two_overlapping_through_holes_are_both_listed(self):
        # centres 0.7 of the sum of the radii apart: each wall is a quarter gone, taken by the other
        obj, page, view = self.plate_with([self.bore(3, 20, 20), self.bore(3, 24.2, 20)])
        tbl = HT.make(page, view)
        self.assertEqual(table_rows(tbl), [["A1", "20", "20", D + "6 THRU"], ["A2", "24.2", "20", D + "6 THRU"]])
        self.assertNotIn("short", HT.warning_of(tbl))

    def test_a_slot_a_keyhole_and_a_filleted_pocket_list_only_the_round_hole(self):
        # no threshold separates these from holes (covers 0.5, 0.25 and 0.87); only the missing surface does, and it is not in another cavity
        slot = Part.makeBox(10, 6, 40, V(70, 7, -5)).fuse(self.bore(3, 70, 10)).fuse(self.bore(3, 80, 10))
        key = self.bore(5, 30, 38).fuse(Part.makeBox(30, 4, 40, V(30, 36, -5))).fuse(self.bore(2, 60, 38))
        blank = Part.makeBox(24, 16, 40, V(70, 25, -5))
        pocket = blank.makeFillet(3.0, [e for e in blank.Edges if abs(e.Vertexes[0].Z - e.Vertexes[1].Z) > 1])
        cuts = [slot, key, pocket, self.bore(2, 15, 15)]
        obj, page, view = self.plate_with(cuts, 100.0, 60.0)
        tbl = HT.make(page, view)
        self.assertEqual(table_rows(tbl), [["A1", "15", "15", D + "4 THRU"]])
        self.assertNotIn("short", HT.warning_of(tbl))  # a left-out segment that touches no listed hole adds no warning

    def test_a_wall_left_out_is_named_in_the_warning(self):
        # ruling 49: a counterbore that breaks out of the plate's edge is not a full segment; the through hole under it is listed without it, and says so
        cuts = [self.bore(3, 5, 20), self.bore(7, 5, 20, 17.0, 10.0)]
        obj, page, view = self.plate_with(cuts)
        tbl = HT.make(page, view)
        self.assertEqual(table_rows(tbl), [["A1", "5", "20", D + "6 THRU"]])
        w = HT.warning_of(tbl)
        self.assertIn("A1 may be shown short", w)
        self.assertIn(D + "14", w)

    def keyhole_beside_a_hole(self):
        cuts = [self.bore(2, 50, 25), self.bore(5, 56, 25), Part.makeBox(18, 4, 40, V(56, 23, -5)), self.bore(2, 74, 25)]
        return self.plate_with(cuts, 80.0, 50.0)

    def test_a_cavity_that_is_still_no_hole_is_named_and_the_tag_keeps_off_its_centre(self):
        # a keyhole's round end (cover 0.77) cuts into the Ø4 hole: the hole is listed, the end stays out; its arc is long enough that a tag over its centre is a fault
        obj, page, view = self.keyhole_beside_a_hole()
        tbl = HT.make(page, view)
        self.assertEqual(table_rows(tbl), [["A1", "50", "25", D + "4 THRU"]])
        w = HT.warning_of(tbl)
        self.assertIn("A1 may be shown short", w)
        self.assertIn(D + "10", w)
        bb = obj.Shape.BoundBox
        S = float(view.Scale)
        cx, cy = view.X.Value + (56.0 - 0.5 * (bb.XMin + bb.XMax)) * S, view.Y.Value + (25.0 - 0.5 * (bb.YMin + bb.YMax)) * S
        t = HT.tags_of(tbl)[0]
        b = self.tag_box(t)
        self.assertFalse(b[0] - 0.05 < cx < b[2] + 0.05 and b[1] - 0.05 < cy < b[3] + 0.05, "the tag lies over the keyhole's centre")

    def forced_repair(self):
        items = self.dense_grid_items(7.0)
        tables = [("hole table", (25.0, 141.7, 108.9, 188.8)), ("hole table", (110.9, 141.7, 194.7, 188.8)), ("hole table", (152.1, 93.2, 236.0, 140.3)), ("hole table", (152.1, 62.1, 236.0, 91.7))]
        obs = [self.TITLE] + tables
        for it in items:
            others = [i2["c"] for i2 in items if i2 is not it]
            it["tips"] = [q for q in it["tips"] if all(math.hypot(q[0] - c[0], q[1] - c[1]) > it["R"] + 0.02 for c in others)] or it["tips"]
        start, _ = HT._plan_once(items, self.AREA, obs, (100.0, 105.0), 20.0, HT.ATTEMPTS[0], set(), {"View"})
        return items, obs, start

    def test_the_repair_walks_clear_what_the_tabu_pass_cannot_and_do_not_depend_on_chance(self):
        # 7 mm pitch with the tables in place: the first fit leaves 18 faults, the tabu pass alone 8, the walks from keyed starts 0
        import random

        items, obs, start = self.forced_repair()
        self.assertGreater(len(HT.tag_conflicts(items, start, self.AREA, obs, 20.0)), 8)
        plans = []
        for seed in (1, 2, 3):
            random.seed(seed)  # anything drawn from `random` would make these differ
            plans.append(HT.repair(items, start, self.AREA, obs, (100.0, 105.0), 20.0))
        self.assertEqual(plans[0], plans[1])
        self.assertEqual(plans[0], plans[2])
        self.assertEqual(HT.tag_conflicts(items, plans[0], self.AREA, obs, 20.0), [])

    def test_a_cavity_a_fraction_of_a_millimetre_short_of_the_missing_surface_does_not_explain_it(self):
        # LOW-2: the explanation tolerance is 1e-4 mm.  A Ø10 hole is cut by a Ø6 neighbour (the lens is explained) and by a small notch 0.24 mm
        # beyond the neighbour's wall (not explained), so the Ø10 stays out and is named; a tolerance of 1 mm would list it.
        cuts = [self.bore(5, 20, 20), self.bore(3, 27, 20), Part.makeBox(0.3, 0.3, 40, V(24.4, 22.0, -5))]
        obj, page, view = self.plate_with(cuts, 50.0, 40.0)
        tbl = HT.make(page, view)
        self.assertEqual(table_rows(tbl), [["A1", "27", "20", D + "6 THRU"]])
        self.assertIn(D + "10", HT.warning_of(tbl))

    def test_a_hole_with_under_half_its_rim_visible_is_not_dropped_silently(self):
        # HIGH-1: a Ø4 through hole beside a Ø10 one, centres 4.2 apart: the large hole takes more than half of the small one's rim
        obj, page, view = self.plate_with([self.bore(2, 19.2, 15), self.bore(5, 15, 15)], 40.0, 30.0)
        tbl = HT.make(page, view)
        w = HT.warning_of(tbl)
        rows = table_rows(tbl)
        self.assertTrue(any(r[3] == D + "10 THRU" for r in rows))
        self.assertFalse(any(r[3] == D + "4 THRU" for r in rows))
        self.assertIn(D + "4 hole at X 19.2 Y 15 is not listed", w)
