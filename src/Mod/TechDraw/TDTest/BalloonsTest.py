# SPDX-License-Identifier: LGPL-2.1-or-later

# G04 slice 2b: balloons tied to the rows of a parts list (PartsList.balloon), headless.
# Run with: FreeCADCmd -t TestTechDrawApp.BalloonsTest   (part of TestTechDrawApp)
#
# Expected texts and components are written from the case definitions, never read from the BOM.
# Convention: ISO 6433 (part references: leaders do not intersect, as short as practicable; public catalogue listing).

import math
import os
import tempfile
import unittest

import FreeCAD as App

try:
    from AssemblyTests import configuration_corpus as C
except Exception:  # the Assembly module is not built
    C = None

import PartsList as PL
import Balloons as BL

ISO_A4 = os.path.join("ISO", "A4_Landscape_ISO5457_minimal.svg")


def diam(text):
    return {1: 12.39, 2: 13.51}.get(len(text), 13.51 + 2.35 * (len(text) - 2))


def cross(a, b, c, d):
    def o(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    return o(a, b, c) * o(a, b, d) < 0 and o(c, d, a) * o(c, d, b) < 0


@unittest.skipIf(C is None, "Assembly module not available")
class BalloonsTest(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("BalloonsTest")
        self.doc.UndoMode = 1

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    # ---- helpers

    def setup_case(self, name):
        import Part

        b = C.build_case(self.doc, name)
        self.doc.recompute()
        page = self.doc.addObject("TechDraw::DrawPage", "Page")
        t = self.doc.addObject("TechDraw::DrawSVGTemplate", "Template")
        t.Template = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Templates", ISO_A4)
        page.Template = t
        v = self.doc.addObject("TechDraw::DrawViewPart", "RefView")
        page.addView(v)
        v.Source = [b.asm]
        v.Direction = App.Vector(1, 1, 1)
        v.ScaleType = "Custom"
        v.Scale = round(70.0 / Part.getShape(b.asm).BoundBox.DiagonalLength, 4)
        v.X, v.Y = float(t.Width) / 2 - 20, float(t.Height) / 2 + 10
        self.doc.recompute()
        table = PL.make(page, b.asm)
        self.doc.recompute()
        return b, page, v, table

    def balloons(self, view=None):
        return [o for o in self.doc.Objects if o.TypeId == "TechDraw::DrawViewBalloon"]

    def texts(self):
        return sorted(b.Text for b in self.balloons())

    def touched(self):
        return [o.Name for o in self.doc.Objects if "Touched" in o.State or "Invalid" in o.State]

    def page_pos(self, view, b):
        S = float(view.Scale)
        vx, vy = view.X.Value, view.Y.Value
        return (vx + b.X.Value * S, vy + b.Y.Value * S), (vx + b.OriginX.Value * S, vy + b.OriginY.Value * S)

    def comp_dist(self, view, asm, paths, q):
        """Distance from page point q (relative to the view) to the projected edges of a component, sampled at 0.05 mm."""
        import Part

        S, gc = float(view.Scale), view.getGeometricCenter()
        best = 1e9
        edges = [e for path in paths for e in Part.getShape(asm, path, transform=True).Edges]
        for e in edges:
            pts = [view.projectPoint(p - gc) for p in e.discretize(Distance=0.05 / S)]
            for a, b in zip(pts[:-1], pts[1:]):
                ax, ay, bx, by = a.x * S, a.y * S, b.x * S, b.y * S
                dx, dy = bx - ax, by - ay
                L2 = dx * dx + dy * dy
                t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((q[0] - ax) * dx + (q[1] - ay) * dy) / L2))
                best = min(best, math.hypot(ax + t * dx - q[0], ay + t * dy - q[1]))
        return best

    def vis_dist(self, view, q):
        best = 1e9
        for e in view.getVisibleEdges(True):
            pts = e.discretize(Distance=0.05)
            for a, b in zip(pts[:-1], pts[1:]):
                dx, dy = b.x - a.x, b.y - a.y
                L2 = dx * dx + dy * dy
                t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((q[0] - a.x) * dx + (q[1] - a.y) * dy) / L2))
                best = min(best, math.hypot(a.x + t * dx - q[0], a.y + t * dy - q[1]))
        return best

    def assertAnchors(self, view, asm, paths_by_text):
        """Ruling 19: tip within 0.5 mm of a visible edge and of its own component, every other component farther."""
        vx, vy = view.X.Value, view.Y.Value
        for b in self.balloons():
            q = (b.OriginX.Value * float(view.Scale), b.OriginY.Value * float(view.Scale))
            self.assertLessEqual(self.vis_dist(view, q), 0.5, b.Text)
            own = self.comp_dist(view, asm, paths_by_text[b.Text], q)
            self.assertLessEqual(own, 0.5, b.Text)
            for t, p in paths_by_text.items():
                if t != b.Text:
                    self.assertGreater(self.comp_dist(view, asm, p, q), own + 0.05, "%s vs %s" % (b.Text, t))

    def assertLayout(self, view, page):
        S = float(view.Scale)
        vis = [pt for e in view.getVisibleEdges(True) for pt in e.discretize(Distance=0.5)]
        vx, vy = view.X.Value, view.Y.Value
        box = (min(p.x for p in vis) + vx, min(p.y for p in vis) + vy, max(p.x for p in vis) + vx, max(p.y for p in vis) + vy)
        area, title = PL.page_areas(page)
        rects = BL.table_rects(page) + ([tuple(title)] if title else [])
        items = []
        for b in self.balloons():
            c, tip = self.page_pos(view, b)
            r = diam(b.Text) / 2
            self.assertTrue(area[0] <= c[0] - r and c[0] + r <= area[2] and area[1] <= c[1] - r and c[1] + r <= area[3], "outside the area")
            self.assertGreaterEqual(BL.rect_dist(box, *c), r, "bubble on the view")
            self.assertLessEqual(BL.rect_dist(box, *c), 40.0, "bubble too far")
            for rc in rects:
                self.assertGreaterEqual(BL.rect_dist(rc, *c), r, "bubble on the table or title block")
            d = math.hypot(tip[0] - c[0], tip[1] - c[1])
            start = (c[0] + (tip[0] - c[0]) / d * r, c[1] + (tip[1] - c[1]) / d * r)
            for rc in rects:
                self.assertFalse(BL.seg_rect((start[0], start[1], tip[0], tip[1]), rc), "leader through the table")
            items.append((c, r, start, tip))
        for i, a in enumerate(items):
            for j, b in enumerate(items):
                if i < j:
                    self.assertGreaterEqual(math.hypot(a[0][0] - b[0][0], a[0][1] - b[0][1]), a[1] + b[1], "bubbles overlap")
                    self.assertFalse(cross(a[2], a[3], b[2], b[3]), "leaders cross")
                if i != j:
                    dd = BL.seg_point_dist(a[2][0], a[2][1], a[3][0], a[3][1], b[0][0], b[0][1])[0]
                    self.assertGreaterEqual(dd, b[1], "leader through another bubble")

    def top_paths(self, asm):
        """Index text -> path of the row's first component, from the assembly's Group (independent of the BOM)."""
        out, seen, i = {}, {}, 0
        for ch in asm.Group:
            if ch.TypeId not in ("App::Link",):
                continue
            t = ch.LinkedObject
            if t.Name in seen:
                out[str(seen[t.Name])].append(ch.Name + ".")
                continue
            i += 1
            seen[t.Name] = i
            out[str(i)] = [ch.Name + "."]
        return out

    # ---- tests

    def test_one_balloon_per_top_level_row_with_the_row_item_as_text(self):
        for name, texts in (("suppress_LA_LB", ["1", "2", "3"]), ("sub_assembly_child", ["1", "2"]), ("array_element", ["1", "2"])):
            b, page, v, table = self.setup_case(name)
            made = PL.balloon(page, v, table)
            self.doc.recompute()
            self.assertEqual(sorted(x.Text for x in made), texts, name)
            self.assertEqual(self.texts(), texts, name)
            self.assertEqual([x.SourceView for x in made], [v] * len(texts))
            self.assertEqual(self.touched(), [], name)
            App.closeDocument(self.doc.Name)
            self.setUp()

    def test_arrows_sit_on_their_own_component_only(self):
        b, page, v, table = self.setup_case("three_configs")
        PL.balloon(page, v, table)
        self.assertAnchors(v, b.asm, self.top_paths(b.asm))

    def test_layout_is_clear_and_leaders_do_not_cross(self):
        for name in ("suppress_LA_LB", "three_configs", "sub_assembly_whole", "array_element"):
            b, page, v, table = self.setup_case(name)
            PL.balloon(page, v, table)
            self.assertLayout(v, page)
            App.closeDocument(self.doc.Name)
            self.setUp()

    def test_recompute_follows_edits_and_leaves_nothing_touched(self):
        b, page, v, table = self.setup_case("three_configs")
        PL.balloon(page, v, table)
        self.assertEqual(self.texts(), ["1", "2", "3"])
        extra = self.doc.addObject("Part::Box", "ExtraSrc")
        lk = b.asm.newObject("App::Link", "Extra")
        lk.LinkedObject = extra
        self.doc.recompute()
        self.assertEqual(self.texts(), ["1", "2", "3", "4"])
        self.assertEqual(self.touched(), [])
        self.assertLayout(v, page)
        b.asm.removeObject(lk)
        self.doc.recompute()
        self.assertEqual(self.texts(), ["1", "2", "3"])
        self.assertEqual(len(self.balloons()), 3)  # removed from the document, not hidden
        self.assertEqual(len([x for x in page.Views if x.TypeId == "TechDraw::DrawViewBalloon"]), 3)
        self.assertEqual(self.touched(), [])
        self.doc.getObject("PlateSrc").Label = "BasePlate"
        self.doc.recompute()
        self.assertEqual(self.texts(), ["1", "2", "3"])
        self.assertEqual(self.touched(), [])

    def test_removing_the_first_row_renumbers(self):
        b, page, v, table = self.setup_case("three_configs")
        PL.balloon(page, v, table)
        gl = self.doc.getObject("GL")
        b.asm.removeObject(gl)
        self.doc.recompute()
        self.assertEqual(self.texts(), ["1", "2"])
        self.assertEqual(self.touched(), [])

    def test_recompute_is_idempotent(self):
        b, page, v, table = self.setup_case("three_configs")
        PL.balloon(page, v, table)
        before = {x.Name: (x.OriginX.Value, x.OriginY.Value, x.X.Value, x.Y.Value, x.Text) for x in self.balloons()}
        n = len(self.doc.Objects)
        for _ in range(3):
            self.doc.recompute()
        after = {x.Name: (x.OriginX.Value, x.OriginY.Value, x.X.Value, x.Y.Value, x.Text) for x in self.balloons()}
        self.assertEqual(before, after)
        self.assertEqual(len(self.doc.Objects), n)

    def test_balloon_is_one_transaction_and_undo_leaves_nothing(self):
        b, page, v, table = self.setup_case("three_configs")
        names = {o.Name for o in self.doc.Objects}
        n = self.doc.UndoCount
        PL.balloon(page, v, table)
        self.assertEqual(self.doc.UndoCount, n + 1)
        self.doc.undo()
        self.assertEqual({o.Name for o in self.doc.Objects}, names)
        self.assertEqual([x for x in page.Views if x.TypeId == "TechDraw::DrawViewBalloon"], [])

    def test_an_edit_inside_a_transaction_undoes_to_the_earlier_balloons(self):
        b, page, v, table = self.setup_case("three_configs")
        PL.balloon(page, v, table)
        before = sorted((x.Text, round(x.X.Value, 6), round(x.OriginX.Value, 6)) for x in self.balloons())
        self.doc.openTransaction("edit")
        lk = b.asm.newObject("App::Link", "Extra")
        lk.LinkedObject = self.doc.addObject("Part::Box", "ExtraSrc")
        self.doc.recompute()
        self.doc.commitTransaction()
        self.assertEqual(len(self.balloons()), 4)
        self.doc.undo()
        self.doc.recompute()
        self.assertEqual(sorted((x.Text, round(x.X.Value, 6), round(x.OriginX.Value, 6)) for x in self.balloons()), before)

    def test_save_and_reopen_keep_balloons_and_edits_still_update(self):
        b, page, v, table = self.setup_case("three_configs")
        PL.balloon(page, v, table)
        fn = os.path.join(tempfile.mkdtemp(prefix="balloons"), "b.FCStd")
        self.doc.saveAs(fn)
        name = self.doc.Name
        App.closeDocument(name)
        self.doc = App.openDocument(fn)
        self.assertEqual(self.texts(), ["1", "2", "3"])
        asm = [o for o in self.doc.Objects if o.TypeId == "Assembly::AssemblyObject"][0]
        lk = asm.newObject("App::Link", "Extra")
        lk.LinkedObject = self.doc.addObject("Part::Box", "ExtraSrc")
        self.doc.recompute()
        self.assertEqual(self.texts(), ["1", "2", "3", "4"])
        self.assertEqual(self.touched(), [])

    def test_a_balloon_moved_by_hand_keeps_its_position_and_its_text_follows(self):
        b, page, v, table = self.setup_case("three_configs")
        PL.balloon(page, v, table)
        mine = sorted(self.balloons(), key=lambda x: x.Text)
        moved = mine[1]
        moved.X, moved.Y = 33.0, -44.0
        self.doc.recompute()
        self.assertAlmostEqual(moved.X.Value, 33.0, places=6)
        self.assertAlmostEqual(moved.Y.Value, -44.0, places=6)
        b.asm.removeObject(self.doc.getObject("GL"))  # the row above it goes, its text becomes "1"
        self.doc.recompute()
        self.assertAlmostEqual(moved.X.Value, 33.0, places=6)
        self.assertAlmostEqual(moved.Y.Value, -44.0, places=6)
        self.assertEqual(moved.Text, "1")
        lk = b.asm.newObject("App::Link", "Extra")
        lk.LinkedObject = self.doc.addObject("Part::Box", "ExtraSrc")
        self.doc.recompute()
        self.assertAlmostEqual(moved.X.Value, 33.0, places=6)

    def test_a_hand_made_balloon_is_never_touched(self):
        b, page, v, table = self.setup_case("three_configs")
        hand = self.doc.addObject("TechDraw::DrawViewBalloon", "HandBalloon")
        hand.SourceView = v
        page.addView(hand)
        hand.Text = "A"
        hand.OriginX, hand.OriginY, hand.X, hand.Y = 5.0, 6.0, 7.0, 8.0
        self.doc.recompute()
        PL.balloon(page, v, table)
        lk = b.asm.newObject("App::Link", "Extra")
        lk.LinkedObject = self.doc.addObject("Part::Box", "ExtraSrc")
        self.doc.recompute()
        b.asm.removeObject(lk)
        self.doc.recompute()
        h = self.doc.getObject("HandBalloon")
        self.assertIsNotNone(h)
        self.assertEqual((h.Text, h.OriginX.Value, h.OriginY.Value, h.X.Value, h.Y.Value), ("A", 5.0, 6.0, 7.0, 8.0))
        self.assertEqual(len(self.balloons()), 4)

    def test_balloon_twice_does_not_duplicate(self):
        b, page, v, table = self.setup_case("three_configs")
        PL.balloon(page, v, table)
        PL.balloon(page, v, table)
        self.assertEqual(self.texts(), ["1", "2", "3"])

    def test_partslist_none_means_the_only_table_on_the_page(self):
        b, page, v, table = self.setup_case("three_configs")
        made = PL.balloon(page, v)
        self.assertEqual(sorted(x.Text for x in made), ["1", "2", "3"])

    def test_bubble_diameter_model(self):
        self.assertAlmostEqual(BL.bubble_diameter("1"), 12.4, delta=0.1)
        self.assertAlmostEqual(BL.bubble_diameter("12"), 13.51, delta=0.1)
        self.assertAlmostEqual(BL.bubble_diameter("123456"), 22.91, delta=0.1)
