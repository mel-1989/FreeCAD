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
        a, _, _ = HT.plan_tags(items, area, [], (120.0, 100.0), 18.5)
        self.assertEqual(HT.r3(100.0004), HT.r3(100.0001))
        b, _, _ = HT.plan_tags(items, area, [], (120.0, 100.0), 18.5)
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
        self.assertTrue(15.0 < HT.MAX_TAG_DIST <= 19.5, HT.MAX_TAG_DIST)
        self.assertTrue(28.0 < HT.MAX_TAG_DIST_DENSE <= 34.5, HT.MAX_TAG_DIST_DENSE)
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
        box = Part.makeBox(66, 46, 10)
        for i in range(6):
            for j in range(4):
                box = box.cut(Part.makeCylinder(1.5, 30, V(6 + i * 6, 6 + j * 6, -5)))
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
