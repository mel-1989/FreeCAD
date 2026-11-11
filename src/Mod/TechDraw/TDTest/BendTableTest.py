# SPDX-License-Identifier: LGPL-2.1-or-later

# G04 slice 3b: the associative bend table (BendTable.flat_view, BendTable.make), headless.
# Run with: FreeCADCmd -t TestTechDrawApp.BendTableTest   (part of TestTechDrawApp)
#
# Expected values come from the build parameters of the SheetMetal test cases (angle, radius, bend allowance, K) and from the
# DXF frame of the flat (SheetMetalFlatPattern.Frame2D), never from the table.  Edits are checked against a fresh table made in a
# new document from the same edited model.
# Conventions: vendor help pages of CAD tools on sheet metal bend tables (behaviour only: tag, UP or DOWN, angle, inside radius).

import json
import math
import os
import tempfile
import time
import unittest

import FreeCAD as App
from FreeCAD import Vector as V

import BendTable as BT
import HoleTable as HT
import PartsList as PL

A4 = os.path.join("ISO", "A4_Landscape_ISO5457_minimal.svg")
DEG = "°"
UP_LINE, DOWN_LINE = 10, 2


def _sm():
    """The SheetMetal modules the test needs (imported when a test runs, so TechDraw's suite loads without SheetMetal)."""
    import SheetMetalFlatPattern as FP
    from SMTests import flat_dxf_cases as C
    from SMTests.testUnfoldFeature import feature_unfold

    return FP, C, feature_unfold


def sheet_rows(sheet):
    out, r = [], 1
    while sheet.getContents("A%d" % r):
        out.append([sheet.get("%s%d" % (c, r)).lstrip("'") for c in "ABCDEF"])
        r += 1
    return out


def table_rows(tbl):
    return [r for r in sheet_rows(BT.driver_of(tbl).Sheet) if r[0] != "TAG"]


def cosmetics(view):
    return [(ce.Start.x, ce.Start.y, ce.End.x, ce.End.y, int(ce.Format["lineNumber"])) for ce in view.CosmeticEdges]


class Built:
    """One flat-pattern case in its own document with a page, view and bend table."""

    def __init__(self, prefix, doc=None, table=True):
        FP, C, feature_unfold = _sm()
        self.case = next(c for c in C.all_cases() if c.name.startswith(prefix))
        self.doc = doc or App.newDocument("BendTableTest")
        self.doc.UndoMode = 1
        obj, face, material, k = self.case.build(self.doc)
        self.k = k if material == "_manual" else None  # a table material sets its own K: implied from the allowance
        self.feat = feature_unfold(self.doc, obj, face, material=material, k=k, old=None)
        self.exp = self.case.expect(self.doc)
        self.page = self.doc.addObject("TechDraw::DrawPage", "Page")
        t = self.doc.addObject("TechDraw::DrawSVGTemplate", "Template")
        t.Template = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Templates", A4)
        self.page.Template = t
        self.view = BT.flat_view(self.page, self.feat)
        self.table = BT.make(self.page, self.view) if table else None

    def obj(self, prefix, prop=None):
        return next(o for o in self.doc.Objects if o.Name.startswith(prefix) and (prop is None or hasattr(o, prop)))

    def bends(self):
        FP = _sm()[0]
        return FP.bends_from_json(self.feat.BendData)

    def expected_lines(self):
        """The bend lines in the view's frame: the DXF frame of the flat minus the flat's centre, from the stored segments."""
        FP = _sm()[0]
        bends, normal = self.bends()
        fr = FP.Frame2D(normal)
        c = fr.point(self.feat.Shape.BoundBox.Center)
        out = []
        for b in bends:
            for a, e in b.segments:
                pa, pe = fr.point(a), fr.point(e)
                out.append((pa[0] - c[0], pa[1] - c[1], pe[0] - c[0], pe[1] - c[1], UP_LINE if b.direction == "UP" else DOWN_LINE))
        return out

    def expected_rows(self):
        """The rows from the case's build parameters, in the order of the bend midpoints in the view's axes (0.1 mm)."""
        FP = _sm()[0]
        bends, normal = self.bends()
        fr = FP.Frame2D(normal)
        c = fr.point(self.feat.Shape.BoundBox.Center)
        exp = self.exp
        # build parameters paired with the bends by direction-free index: the case lists them in one order, the stored bends in another,
        # so pair by allowance and angle (the build parameters) and take the position from the stored segments
        want = []
        for ang, ba in zip(exp["angles"], exp["ba"]):
            want.append((abs(ang), ba))
        out = []
        rest = list(want)
        for b in bends:
            i = next(i for i, (ang, ba) in enumerate(rest) if abs(ang - b.angle) < 1e-6 and abs(ba - b.allowance) < 1e-3)
            ang, ba = rest.pop(i)
            seg = max(b.segments, key=lambda s: (s[1] - s[0]).Length)
            m = fr.point((seg[0] + seg[1]) * 0.5)
            m = (m[0] - c[0], m[1] - c[1])
            out.append((round(m[0] / 0.1), round(m[1] / 0.1), m, b, ang, ba))
        out.sort(key=lambda t: (t[0], t[1], t[2]))
        rows = []
        for n, (_, _, m, b, ang, ba) in enumerate(out, start=1):
            k = self.k if self.k is not None else (ba / math.radians(ang) - exp["radius"]) / b.thickness
            angle = ("%d" % round(ang) if abs(ang - round(ang)) < 1e-6 else "%.2f" % ang) + DEG
            rows.append(["B%d" % n, b.direction, angle, "R" + HT.fmt(exp["radius"]), "%.2f" % k, HT.fmt(ba)])
        return rows, [t[2] for t in out]

    def snapshot(self):
        d = BT.driver_of(self.table)
        return {
            "rows": [r[:] for r in table_rows(self.table)],
            "lines": sorted(tuple(round(v, 4) for v in l) for l in cosmetics(self.view)),
            "tags": [(t.Text, round(t.OriginX.Value, 4), round(t.OriginY.Value, 4)) for t in BT.tags_of(self.table)],
            "warning": d.Warning,
        }

    def close(self):
        App.closeDocument(self.doc.Name)


# edits: name -> function(Built)
def _set(prefix, prop, val):
    def f(b):
        setattr(b.obj(prefix, prop), prop, val)

    return f


def _del_segment(b):
    sk = b.obj("Sketch_")
    sk.delGeometry(sk.GeometryCount - 1)


def _add_segment(b):
    from FreeCAD import Rotation

    import Part

    sk = b.obj("Sketch_")
    g = sk.Geometry[-1]
    p = g.EndPoint
    d = g.EndPoint - g.StartPoint
    d.normalize()
    sk.addGeometry(Part.LineSegment(p, p + Rotation(V(0, 0, 1), 90).multVec(d) * 20))


EDITS = {
    "g03": [("radius", _set("Base_", "Radius", 3.0)), ("thickness", _set("Base_", "Thickness", 3.0)), ("k", _set("Unfold", "KFactor", 0.3))],
    "g05": [("radius", _set("Base_", "Radius", 2.0)), ("remove flange", _del_segment), ("add flange", _add_segment)],
    "g07": [("angle", _set("EdgeFlange", "angle", 90.0)), ("radius", _set("EdgeFlange", "radius", 3.0))],
    "g16": [("thickness", _set("Base_", "Thickness", 1.5)), ("remove flange", _del_segment)],
}


class BendTableTest(unittest.TestCase):
    def setUp(self):
        self.built = []

    def tearDown(self):
        for b in self.built:
            try:
                b.close()
            except Exception:
                pass

    def build(self, prefix, **kw):
        b = Built(prefix, **kw)
        self.built.append(b)
        return b

    def all_names(self):
        FP, C, _ = _sm()
        return [c.name[:3] for c in C.all_cases()]

    # -- rows, order, formats ---------------------------------------------------------------

    def test_rows_on_all_cases(self):
        for name in self.all_names():
            b = self.build(name)
            rows, _ = b.expected_rows()
            self.assertEqual(table_rows(b.table), rows, name)
            self.assertEqual(sheet_rows(BT.driver_of(b.table).Sheet)[0], ["TAG", "DIRECTION", "ANGLE", "RADIUS", "K", "ALLOWANCE"], name)
            b.close()
            self.built.remove(b)

    def test_formats(self):
        b = self.build("g06")  # 30, 45 and 90 degrees at R4, allowance 2.534, 3.801, 7.603
        rows = table_rows(b.table)
        self.assertEqual(sorted(r[2] for r in rows), ["30" + DEG, "45" + DEG, "90" + DEG])
        self.assertTrue(all(r[3] == "R4" and r[4] == "0.42" for r in rows))
        self.assertEqual(sorted(r[5] for r in rows), ["2.53", "3.8", "7.6"])
        self.assertEqual(BT.angle_text(37.5), "37.50" + DEG)
        self.assertEqual(BT.angle_text(89.999999), "90" + DEG)
        self.assertEqual(BT.radius_text(0.2), "R0.2")
        self.assertEqual(BT.k_text(0.5), "0.50")

    def test_order_follows_midpoints_not_list_order(self):
        for name in ("g17", "g20", "g16", "g15"):  # the stored list order differs from the order along the view's x axis here; g15 has bends in both axes
            b = self.build(name)
            tags = BT.tags_of(b.table)
            tips = [(t.OriginX.Value, t.OriginY.Value) for t in tags]
            keys = [(round(x / 0.1), round(y / 0.1)) for x, y in tips]
            self.assertEqual(keys, sorted(keys), name)
            self.assertEqual([t.Text for t in tags], ["B%d" % (i + 1) for i in range(len(tags))])
            # each row is tied to its bend by the tag tip: the bend with that midpoint has that direction
            _, mids = b.expected_rows()
            bends, _n = b.bends()
            for (x, y), m in zip(tips, mids):
                self.assertLess(math.hypot(x - m[0], y - m[1]), 0.05, name)
            b.close()
            self.built.remove(b)

    def test_directions_and_tags_on_mixed_cases(self):
        b = self.build("g17")
        self.assertEqual([r[1] for r in table_rows(b.table)].count("UP"), 2)
        b = self.build("g20")
        # the jog: a DOWN and an UP bend, tags at their own bend lines
        rows = table_rows(b.table)
        self.assertEqual(sorted(r[1] for r in rows), ["DOWN", "UP"])
        lines = b.expected_lines()
        for t, r in zip(BT.tags_of(b.table), rows):
            near = min(lines, key=lambda l: math.hypot(t.OriginX.Value - 0.5 * (l[0] + l[2]), t.OriginY.Value - 0.5 * (l[1] + l[3])))
            self.assertEqual(near[4], UP_LINE if r[1] == "UP" else DOWN_LINE)

    # -- the view and the lines -------------------------------------------------------------

    def test_flat_view(self):
        FP = _sm()[0]
        b = self.build("g03", table=False)
        bends, normal = b.bends()
        self.assertLess((b.view.Direction - normal).Length, 1e-9)
        self.assertLess((b.view.XDirection - FP.Frame2D(normal).ex).Length, 1e-9)
        self.assertIn(round(b.view.Scale, 6), (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0))
        self.assertEqual(list(b.view.Source), [b.feat])
        g6 = self.build("g06", table=False)
        self.assertEqual(g6.view.Scale, 0.5)  # 160.5 mm wide does not fit 60 % of 267 mm at 1:1

    def test_lines_on_all_cases(self):
        for name in self.all_names():
            b = self.build(name)
            got = sorted(tuple(round(v, 6) for v in l[:4]) + (l[4],) for l in cosmetics(b.view))
            want = sorted(tuple(round(v, 6) for v in l[:4]) + (l[4],) for l in b.expected_lines())
            self.assertEqual(len(got), len(want), name)
            for g, w in zip(got, want):
                self.assertTrue(all(abs(x - y) < 1e-5 for x, y in zip(g[:4], w[:4])) and g[4] == w[4], (name, g, w))
            b.close()
            self.built.remove(b)

    def test_dash_choice(self):
        b = self.build("g16")  # one UP and one DOWN bend
        by = {r[1]: None for r in table_rows(b.table)}
        self.assertEqual(sorted(l[4] for l in cosmetics(b.view)), [DOWN_LINE, UP_LINE])
        self.assertEqual(set(by), {"UP", "DOWN"})
        mids = {int(l[4]): 0.5 * (l[1] + l[3]) for l in cosmetics(b.view)}
        for t, r in zip(BT.tags_of(b.table), table_rows(b.table)):
            self.assertAlmostEqual(t.OriginY.Value, mids[UP_LINE if r[1] == "UP" else DOWN_LINE], places=3)

    def test_bend_lines_do_not_disturb_the_flat(self):
        b = self.build("g03")
        self.assertEqual(len(b.view.CosmeticEdges), 2)
        self.assertEqual(len(b.view.getVisibleEdges(True)), 4 + 2)  # the outline and the cosmetic lines

    # -- layout and warnings ----------------------------------------------------------------

    def test_layout_on_all_cases(self):
        for name in self.all_names():
            b = self.build(name)
            area, title = PL.page_areas(b.page)
            sv = b.view
            S = float(sv.Scale)
            boxes = []
            for t in BT.tags_of(b.table):
                box = HT.tag_box(sv, t)
                self.assertGreaterEqual(box[0], area[0] - 0.01, name)
                self.assertLessEqual(box[2], area[2] + 0.01, name)
                self.assertGreaterEqual(box[1], area[1] - 0.01, name)
                self.assertLessEqual(box[3], area[3] + 0.01, name)
                tip = (sv.X.Value + t.OriginX.Value * S, sv.Y.Value + t.OriginY.Value * S)
                cen = (0.5 * (box[0] + box[2]), 0.5 * (box[1] + box[3]))
                self.assertLess(math.hypot(cen[0] - tip[0], cen[1] - tip[1]), 20.0, name)
                boxes.append(box)
            blocks = [HT.sheet_view_box(v) for v in BT.blocks_of(b.table)]
            for i, a in enumerate(boxes):
                for c in boxes[i + 1 :] + blocks + ([tuple(title)] if title else []):
                    self.assertFalse(HT.overlap(a, c, -0.01), (name, a, c))
            for i, a in enumerate(blocks):
                for c in blocks[i + 1 :] + ([tuple(title)] if title else []) + [HT.view_box(sv)]:
                    self.assertFalse(HT.overlap(a, c, -0.01), (name, a, c))
            self.assertEqual(BT.warning_of(b.table), "", name)
            vb = HT.view_box(sv)
            self.assertGreaterEqual(vb[0], area[0] - 0.01, name)
            self.assertFalse(title and HT.overlap(vb, tuple(title), -0.01), name)  # the flat itself stands clear of the title block
            b.close()
            self.built.remove(b)

    def fake(self, b, text):
        """A stand-in for an unfold whose BendData is ``text``, with the real one's shape, as the source of a second view."""
        f = b.doc.addObject("Part::Feature", "FakeUnfold")
        f.Shape = b.feat.Shape
        f.addProperty("App::PropertyString", "BendData", "Base", "")
        f.BendData = text
        v = b.doc.addObject("TechDraw::DrawViewPart", "FakeView")
        b.page.addView(v)
        v.Source = [f]
        v.Direction = V(0, 0, 1)
        v.X, v.Y = 80, 100
        b.doc.recompute()
        return v

    def test_flat_view_without_bend_data(self):
        """R6: a V2 unfold (no BendData) gets a flat view along the largest planar face, a warning and an empty table, not a ValueError."""
        FP, C, feature_unfold = _sm()
        for prefix in ("g01", "g06", "g11", "g17"):
            b = self.build(prefix, table=False)
            ref = HT.Edges(b.view).box
            case = next(c for c in C.all_cases() if c.name.startswith(prefix))
            doc = App.newDocument("BendTableV2")
            self.addCleanup(App.closeDocument, doc.Name)
            obj, face, material, k = case.build(doc)
            feat = feature_unfold(doc, obj, face, material=material, k=k, old=False)
            if getattr(feat, "BendData", "x"):  # the new unfolder is not available in this build: fake the V2 outcome
                feat.BendData = ""
            page = doc.addObject("TechDraw::DrawPage", "Page")
            t = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
            t.Template = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Templates", A4)
            page.Template = t
            view = BT.flat_view(page, feat)  # raises nothing
            box = HT.Edges(view).box
            self.assertIsNotNone(box, prefix)
            # the outline matches the V1 view's (boxes are in unscaled units; the axes may swap)
            w1, h1 = ref[2] - ref[0], ref[3] - ref[1]
            w2, h2 = box[2] - box[0], box[3] - box[1]
            self.assertTrue(
                (abs(w1 - w2) < 0.01 and abs(h1 - h2) < 0.01) or (abs(w1 - h2) < 0.01 and abs(h1 - w2) < 0.01), "%s %s %s" % (prefix, (w1, h1), (w2, h2))
            )
            tbl = BT.make(page, view)
            self.assertIn("BendTable: no bend data", BT.warning_of(tbl))
            self.assertEqual(table_rows(tbl), [])

    def test_warning_no_bend_data(self):
        b = self.build("g03", table=False)
        v = self.fake(b, "")
        t = BT.make(b.page, v)
        self.assertIn("BendTable: no bend data", BT.warning_of(t))
        self.assertEqual(table_rows(t), [])
        self.assertEqual(len(sheet_rows(BT.driver_of(t).Sheet)), 1)
        self.assertEqual(len(v.CosmeticEdges), 0)
        self.assertEqual(BT.tags_of(t), [])

    def test_warning_problem(self):
        b = self.build("g03", table=False)
        data = json.loads(b.feat.BendData)
        data["problem"] = "flat pattern has 3 bends but the model has 2"
        v = self.fake(b, json.dumps(data))
        t = BT.make(b.page, v)
        self.assertIn("flat pattern has 3 bends but the model has 2", BT.warning_of(t))
        self.assertEqual(len(table_rows(t)), 2)

    def test_warning_long_table(self):
        b = self.build("g03", table=False)
        data = json.loads(b.feat.BendData)
        seg = data["bends"][0]["segments"][0]
        bends = []
        for i in range(34):
            d = json.loads(json.dumps(data["bends"][0]))
            d["segments"] = [[[seg[0][0], seg[0][1] + 3 * i, seg[0][2]], [seg[1][0], seg[1][1] + 3 * i, seg[1][2]]]]
            bends.append(d)
        data["bends"] = bends
        v = self.fake(b, json.dumps(data))
        t = BT.make(b.page, v)
        self.assertIn("34 bends listed, the table is long", BT.warning_of(t))
        self.assertEqual(len(table_rows(t)), 34)
        self.assertEqual(len(v.CosmeticEdges), 34)
        self.assertEqual(len(BT.tags_of(t)), 34)

    # -- following the model ------------------------------------------------------------------

    def fresh_snapshot(self, prefix, edits):
        """A new document with the same case, the same edits, and one bend table made once at the end."""
        b = Built(prefix, table=False)
        for _name, fn in edits:
            fn(b)
            b.doc.recompute()
        b.table = BT.make(b.page, b.view)
        s = b.snapshot()
        b.close()
        return s

    def test_follow_the_model(self):
        for prefix, edits in EDITS.items():
            b = self.build(prefix)
            done = []
            for name, fn in edits:
                before = {t.Name: (t.Text, t.HoleSize) for t in BT.tags_of(b.table)}
                tips = [(t.OriginX.Value, t.OriginY.Value) for t in BT.tags_of(b.table)]
                fn(b)
                t0 = time.time()
                b.doc.recompute()
                self.assertLess(time.time() - t0, 1.0, (prefix, name))
                done.append((name, fn))
                snap = b.snapshot()
                fresh = self.fresh_snapshot(prefix, done)
                self.assertEqual(snap["rows"], fresh["rows"], (prefix, name))
                self.assertEqual(snap["lines"], fresh["lines"], (prefix, name))
                self.assertEqual(snap["tags"], fresh["tags"], (prefix, name))
                self.assertEqual(len(cosmetics(b.view)), sum(len(x.segments) for x in b.bends()[0]), (prefix, name))
                # the lines are the expected ones, none left from before
                got = sorted(tuple(round(v, 4) for v in l) for l in b.expected_lines())
                self.assertEqual(snap["lines"], got, (prefix, name))
                self.assertEqual(BT.warning_of(b.table), "", (prefix, name))
                self.assertEqual([o.Name for o in b.doc.Objects if "Touched" in o.State], [], (prefix, name))
                moved = [(t.OriginX.Value, t.OriginY.Value) for t in BT.tags_of(b.table)]
                small = len(moved) == len(tips) and all(math.hypot(p[0] - q[0], p[1] - q[1]) < 0.45 for p, q in zip(tips, moved))
                if name in ("radius", "thickness", "k") and small:  # midpoints move by a fraction of a millimetre: every tag stays with its bend
                    self.assertEqual({t.Name: (t.Text, t.HoleSize) for t in BT.tags_of(b.table)}, before, (prefix, name))
            b.close()
            self.built.remove(b)

    def test_tag_identity(self):
        b = self.build("g05")
        names = {t.Text: t.Name for t in BT.tags_of(b.table)}
        self.assertEqual(len(set(names.values())), 3)
        _set("Base_", "Radius", 2.0)(b)
        b.doc.recompute()
        self.assertEqual({t.Text: t.Name for t in BT.tags_of(b.table)}, names)
        # a user-moved tag keeps its place through an edit
        t1 = BT.tags_of(b.table)[0]
        t1.X = t1.X.Value + 3.0
        x = t1.X.Value
        _set("Base_", "Radius", 1.0)(b)
        b.doc.recompute()
        self.assertAlmostEqual(next(t for t in BT.tags_of(b.table) if t.Name == t1.Name).X.Value, x, places=3)
        # a bend whose angle changed is a new bend: its tag object is replaced
        c = self.build("g07")
        before = {t.Name for t in BT.tags_of(c.table)}
        _set("EdgeFlange", "angle", 90.0)(c)
        c.doc.recompute()
        after = {t.Name for t in BT.tags_of(c.table)}
        self.assertEqual(len(after), 1)
        self.assertFalse(before & after)

    def test_remove_flange_drops_its_tag_and_line(self):
        b = self.build("g05")
        n0 = {t.Name for t in BT.tags_of(b.table)}
        _del_segment(b)
        b.doc.recompute()
        n1 = {t.Name for t in BT.tags_of(b.table)}
        self.assertEqual(len(n1), 2)
        self.assertEqual(len(b.view.CosmeticEdges), 2)
        for gone in n0 - n1:
            self.assertIsNone(b.doc.getObject(gone))

    # -- undo, save and reopen --------------------------------------------------------------

    def test_undo_is_one_step(self):
        b = self.build("g16", table=False)
        n_objs = len(b.doc.Objects)
        self.assertEqual(len(b.view.CosmeticEdges), 0)
        BT.make(b.page, b.view)
        self.assertEqual(len(b.view.CosmeticEdges), 2)
        b.doc.undo()
        self.assertEqual(len(b.view.CosmeticEdges), 0)
        self.assertEqual(len(b.doc.Objects), n_objs)
        self.assertEqual([v.TypeId for v in b.page.Views], ["TechDraw::DrawViewPart"])
        b.doc.redo()
        self.assertEqual(len(b.view.CosmeticEdges), 2)
        b.doc.recompute()
        self.assertEqual(len(b.view.CosmeticEdges), 2)

    def test_save_and_reopen(self):
        for prefix in ("g16", "g05", "g20", "g11"):
            b = self.build(prefix)
            snap = b.snapshot()
            names = {t.Name: t.Text for t in BT.tags_of(b.table)}
            path = os.path.join(tempfile.mkdtemp(), "bend.FCStd")
            b.doc.saveAs(path)
            App.closeDocument(b.doc.Name)
            doc = App.openDocument(path)
            b.doc = doc
            b.page = doc.getObject("Page")
            b.view = doc.getObject("FlatView")
            b.feat = next(o for o in doc.Objects if hasattr(o, "BendData"))
            b.table = next(o for o in doc.Objects if o.TypeId == "TechDraw::DrawViewSpreadsheet" and o.Label == "Bend table")
            doc.recompute()
            self.assertEqual(b.snapshot(), snap, prefix)
            self.assertEqual({t.Name: t.Text for t in BT.tags_of(b.table)}, names, prefix)
            # an edit after reopening still updates rows, lines and tags
            edit = {"g16": ("Base_", "Thickness", 1.5), "g05": ("Base_", "Radius", 2.0), "g20": ("Unfold", "KFactor", 0.3), "g11": ("Unfold", "KFactor", 0.3)}[prefix]
            setattr(b.obj(edit[0]), edit[1], edit[2])
            doc.recompute()
            got = sorted(tuple(round(v, 4) for v in l) for l in b.expected_lines())
            self.assertEqual(b.snapshot()["lines"], got, prefix)
            self.assertEqual(len(cosmetics(b.view)), len(got), prefix)
            self.assertEqual(len(table_rows(b.table)), len(b.bends()[0]), prefix)

    def test_warning_wording_names_bends(self):
        item = {"tag": "B1", "c": (100.0, 100.0), "R": 0.0, "tips": [(100.0, 100.0)], "noun": "bend line", "of": "its bend"}
        area = (25.0, 25.0, 280.0, 190.0)
        far = [((100.0 + 20.1, 100.0), (100.0, 100.0))]
        self.assertEqual(HT.tag_conflicts([item], far, area, [], 20.0), ["B1 more than 20 mm from its bend"])
        cover = [((101.0, 100.0), (100.0, 100.0))]
        self.assertIn("B1 covers a bend line centre", HT.tag_conflicts([item], cover, area, [], 100.0))
        # and the driver hands the placer items with those words
        b = self.build("g16", table=False)
        seen = []
        real = HT.plan_tags
        HT.plan_tags = lambda items, *a, **k: (seen.extend(items), real(items, *a, **k))[1]
        try:
            BT.make(b.page, b.view)
        finally:
            HT.plan_tags = real
        self.assertTrue(seen)
        self.assertTrue(all(i["noun"] == "bend line" and i["of"] == "its bend" for i in seen))
        hole = dict(item, tag="A1")
        del hole["noun"], hole["of"]
        self.assertEqual(HT.tag_conflicts([hole], far, area, [], 20.0), ["A1 more than 20 mm from its hole"])

    def test_undo_and_redo_leave_exactly_the_bend_lines(self):
        for prefix, edit in (("g16", ("Unfold", "KFactor", 0.5)), ("g05", ("Base_", "Radius", 2.0))):
            b = self.build(prefix)
            n = len(b.expected_lines())
            user = b.view.makeCosmeticLine(V(1, 1, 0), V(5, 5, 0), 1, 0.35, (0.0, 0.0, 1.0, 1.0), UP_LINE)  # a user's blue line with a bend's line number
            b.doc.recompute()
            self.assertEqual(len(b.view.CosmeticEdges), n + 1)
            before = b.snapshot()
            b.doc.openTransaction("edit")
            setattr(b.obj(edit[0], edit[1]), edit[1], edit[2])
            b.doc.recompute()
            b.doc.commitTransaction()
            edited = b.snapshot()
            self.assertEqual(len(b.view.CosmeticEdges), n + 1, prefix)
            b.doc.undo()
            b.doc.recompute()
            self.assertEqual(b.snapshot(), before, prefix)
            self.assertEqual(len(b.view.CosmeticEdges), n + 1, prefix)
            b.doc.redo()
            b.doc.recompute()
            self.assertEqual(b.snapshot(), edited, prefix)
            self.assertEqual(len(b.view.CosmeticEdges), n + 1, prefix)
            self.assertTrue(any(ce.Tag == user for ce in b.view.CosmeticEdges), prefix)  # the user's line survives every step
            b.close()
            self.built.remove(b)

    # -- a hole table on the same page --------------------------------------------------------

    def test_with_a_hole_table(self):
        for hole_first in (True, False):
            b = self.build("g11", table=False)
            if hole_first:
                ht = HT.make(b.page, b.view)
                bt = BT.make(b.page, b.view)
            else:
                bt = BT.make(b.page, b.view)
                ht = HT.make(b.page, b.view)
            self.assertEqual([t.Text for t in HT.tags_of(ht)], ["A1"])
            self.assertEqual([t.Text for t in BT.tags_of(bt)], ["B1", "B2"])
            self.assertEqual(len(table_rows(bt)), 2)
            self.assertEqual(HT.warning_of(ht), "")
            self.assertEqual(BT.warning_of(bt), "")
            self.assertEqual(len(b.view.CosmeticEdges), 2)
            tags = HT.tags_of(ht) + BT.tags_of(bt)
            boxes = [HT.tag_box(b.view, t) for t in tags]
            for i, a in enumerate(boxes):
                for c in boxes[i + 1 :]:
                    self.assertFalse(HT.overlap(a, c, -0.01))
            tabs = [HT.sheet_view_box(v) for v in HT.blocks_of(ht) + BT.blocks_of(bt)]
            for a in boxes:
                for c in tabs:
                    self.assertFalse(HT.overlap(a, c, -0.01))
            self.assertFalse(HT.overlap(tabs[0], tabs[1], -0.01))
            # both follow an edit
            _set("Unfold", "KFactor", 0.3)(b)
            b.doc.recompute()
            self.assertEqual(len(HT.tags_of(ht)), 1)
            self.assertEqual(len(table_rows(bt)), 2)
            self.assertEqual(table_rows(bt)[0][4], "0.30")
            b.close()
            self.built.remove(b)

    # -- speed --------------------------------------------------------------------------------

    def test_speed(self):
        for name in self.all_names():
            b = self.build(name, table=False)
            t0 = time.time()
            BT.make(b.page, b.view)
            b.doc.recompute()
            self.assertLess(time.time() - t0, 1.0, name)
            b.close()
            self.built.remove(b)
