# SPDX-License-Identifier: LGPL-2.1-or-later

# G04 slice 5a: chain, baseline and ordinate dimension sets (DimChain), headless.
# Run with: FreeCADCmd -t TestTechDrawApp.DimChainTest   (part of TestTechDrawApp)
#
# Expected numbers are written out by hand from how each plate is built (a 100 x 40 plate, holes on its centre line, view scale 1.5,
# the view centred on the plate), never read back from the set.  Layout conventions: TechDraw's own CommandExtensionDims.cpp and
# QGIViewDimension.cpp (line 0.6333 * (font + 1) mm from the label, measured in the GUI); ordinate dimensioning per the secondary
# descriptions in the G04 brief (values on extension lines, no dimension lines or arrowheads, base marked 0).

import os
import re
import shutil
import tempfile
import unittest

import FreeCAD as App
import Part
from FreeCAD import Vector as V

import DimChain as DC

A4 = os.path.join("ISO", "A4_Landscape_ISO5457_minimal.svg")
OFF = 0.63333 * (5.0 + 1.0)  # the stock label sits this far above its dimension line (font 5 mm, TechDraw's default)
S = 1.5


class Plate:
    """L x 40 plate (z 0 to 5), holes of radius 4 on y = 20; view scale 1.5, centred on the plate."""

    def __init__(self, doc, L=100.0, holes=(20.0, 50.0, 85.0), scale=S):
        self.doc, self.L, self.holes, self.scale = doc, L, list(holes), scale
        self.box = doc.addObject("Part::Box", "Plate")
        self.box.Length, self.box.Width, self.box.Height = L, 40.0, 5.0
        self.cyls = []
        cut = self.box
        for i, x in enumerate(holes):
            c = doc.addObject("Part::Cylinder", "H%d" % i)
            c.Radius, c.Height = 4.0, 5.0
            c.Placement.Base = V(x, 20, 0)
            n = doc.addObject("Part::Cut", "Cut%d" % i)
            n.Base, n.Tool = cut, c
            self.cyls.append(c)
            cut = n
        self.top = cut
        self.page = doc.addObject("TechDraw::DrawPage", "Page")
        t = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
        t.Template = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Templates", A4)
        self.page.Template = t
        self.view = doc.addObject("TechDraw::DrawViewPart", "View")
        self.page.addView(self.view)
        self.view.Source = [cut]
        self.view.Direction = V(0, 0, 1)
        self.view.XDirection = V(1, 0, 0)
        self.view.Scale = scale
        self.view.X, self.view.Y = 150.0, 110.0
        doc.recompute()

    # -- hand-written geometry of the view (page mm, y up)
    def px(self, x):
        return 150.0 + (x - self.L / 2.0) * self.view.Scale

    def py(self, y):
        return 110.0 + (y - 20.0) * self.view.Scale

    def outline(self):
        return (self.px(0), self.py(0), self.px(self.L), self.py(40))

    def vertex(self, x, y):
        """The VertexN name of the vertex at model point (x, y)."""
        v = self.view
        for i in range(60):
            try:
                p = v.getVertexByIndex(i)
            except Exception:
                break
            if abs(p.X - (x - self.L / 2.0)) < 1e-6 and abs(p.Y - (y - 20.0)) < 1e-6:
                return "Vertex%d" % i
        raise AssertionError("no vertex at %s,%s" % (x, y))

    def corner(self, right=False, top=False):
        return self.vertex(self.L if right else 0.0, 40.0 if top else 0.0)

    def hole(self, i):
        return self.vertex(self.holes[i], 20.0)


def members(d):
    return DC.members_of(d)


def refs(dim):
    return DC.DimChainDriver.pair(dim)


def svg_items(sym):
    """-> (paths [point list], circles [(cx, cy, r)], texts [(text, x, y, rotated, anchor, size)]) of an ordinate symbol, svg units."""
    s = sym.Symbol
    paths = [[tuple(float(v) for v in p.split(",")) for p in re.findall(r"[ML]([-\d.]+,[-\d.]+)", d)] for d in re.findall(r'<path d="([^"]*)"', s)]
    circles = [tuple(float(v) for v in m) for m in re.findall(r'<circle cx="([-\d.]+)" cy="([-\d.]+)" r="([-\d.]+)"', s)]
    texts = []
    for m in re.finditer(r"<text ([^>]*)>([^<]*)</text>", s):
        a = dict(re.findall(r'([\w-]+)="([^"]*)"', m.group(1)))
        texts.append((m.group(2), float(a["x"]), float(a["y"]), "transform" in a, a["text-anchor"], float(a["font-size"])))
    return paths, circles, texts


def to_page(sym, w):
    """svg unit (0.1 mm, y down, origin at the corner of the symbol's box) -> page mm (y up)."""
    m = re.search(r'width="([-\d.]+)" height="([-\d.]+)"', sym.Symbol)
    W, H = float(m.group(1)), float(m.group(2))
    return (sym.X.Value + (w[0] - W / 2.0) / 10.0, sym.Y.Value + (H / 2.0 - w[1]) / 10.0)


class DimChainTest(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("DimChainTest")
        self.doc.UndoMode = 1
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        App.closeDocument(self.doc.Name)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def plate(self, **kw):
        return Plate(self.doc, **kw)

    def settled(self):
        on = {v.Name for p in self.doc.findObjects(Type="TechDraw::DrawPage") for v in p.Views}
        # a member dimension parked off the page (its vertex is gone) keeps its error state, and is not drawn
        return [o.Name for o in self.doc.Objects if ("Touched" in o.State or "Invalid" in o.State) and not (o.TypeId == "TechDraw::DrawViewDimension" and o.Name not in on)]

    # ---- value format

    def test_format_is_the_stock_text(self):
        grp = App.ParamGet(DC.PARAMS)
        p = self.plate()
        for spec, units, val, want in (("%.2w", False, 20.0, "20"), ("%.2w", False, 33.456, "33.46"), ("%.2w", True, 12.5, "12.5 mm"), ("%.1f", False, 20.0, "20.0"), ("%.3f", False, 7.0, "7.000"), ("%.2w", False, 0.0, "0")):
            old = (grp.GetString("formatSpec", "%.2w"), grp.GetBool("ShowUnits", False))
            grp.SetString("formatSpec", spec)
            grp.SetBool("ShowUnits", units)
            try:
                self.assertEqual(DC.format_value(val), want, (spec, units, val))
            finally:
                grp.SetString("formatSpec", old[0])
                grp.SetBool("ShowUnits", old[1])
        # against a real stock dimension: its unaltered text carries the same number to the document's decimals
        d = DC.make(p.page, p.view, [p.corner(), p.hole(0)], "baseline")
        stock = members(d)[0]
        self.assertAlmostEqual(float(stock.getText().split()[0]), float(DC.format_value(stock.getRawValue())), places=2)
        self.assertEqual(DC.format_value(stock.getRawValue()), "20")

    # ---- chain

    def test_chain_layout_numbers(self):
        p = self.plate()
        d = DC.make(p.page, p.view, [p.hole(0), p.hole(1), p.hole(2)], "chain")
        m = members(d)
        self.assertEqual(len(m), 2)
        self.assertEqual({tuple(sorted(refs(x))) for x in m}, {tuple(sorted((p.hole(0), p.hole(1)))), tuple(sorted((p.hole(1), p.hole(2))))})
        by = {tuple(sorted(refs(x))): x for x in m}
        a, b = by[tuple(sorted((p.hole(0), p.hole(1))))], by[tuple(sorted((p.hole(1), p.hole(2))))]
        self.assertEqual(a.Type, "DistanceX")
        self.assertAlmostEqual(a.getRawValue(), 30.0, places=6)
        self.assertAlmostEqual(b.getRawValue(), 35.0, places=6)
        # all on one line, 10 mm below the outline (page y 80 - 10 = 70, i.e. -40 from the view's centre; the label sits OFF above it)
        for x in (a, b):
            self.assertAlmostEqual(x.Y.Value, -40.0 + OFF, places=3)
        # each label at its span's midpoint: (20 + 50) / 2 and (50 + 85) / 2 from the centre x = 50, scale 1.5
        self.assertAlmostEqual(a.X.Value, (35.0 - 50.0) * S, places=3)
        self.assertAlmostEqual(b.X.Value, (67.5 - 50.0) * S, places=3)
        self.assertEqual(self.settled(), [])

    def test_chain_sorts_by_position_and_sides(self):
        p = self.plate()
        d = DC.make(p.page, p.view, [p.hole(2), p.hole(0), p.hole(1)], "chain", side="above")
        ys = {round(x.Y.Value, 3) for x in members(d)}
        self.assertEqual(ys, {round(30.0 + 10.0 + OFF, 3)})  # outline top at +30, 10 mm above, label OFF above the line
        self.assertEqual(sorted(round(x.getRawValue(), 3) for x in members(d)), [30.0, 35.0])
        v = DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1)], "chain", direction="vertical")
        self.assertEqual(len(members(v)), 1)  # R15 (ruling 109): the holes share y, so there is no member between them (it was one of 0.0 in 5a)
        # vertical: the corner (y 0) to the holes is 20; the line is 10 mm left of the outline (x -75 - 10)
        for x in members(v):
            self.assertEqual(x.Type, "DistanceY")
            self.assertAlmostEqual(x.X.Value, -85.0 - OFF, places=3)

    # ---- baseline

    def test_baseline_layout_numbers(self):
        p = self.plate()
        d = DC.make(p.page, p.view, [p.corner(), p.hole(2), p.hole(0), p.hole(1)], "baseline")  # given out of order
        m = members(d)
        self.assertEqual(len(m), 3)
        spacing = DC.cascade_spacing()
        self.assertAlmostEqual(spacing, 7.0)
        by = {refs(x)[1]: x for x in m}
        self.assertTrue(all(refs(x)[0] == p.corner() for x in m))
        want = [(p.hole(0), 20.0), (p.hole(1), 50.0), (p.hole(2), 85.0)]
        for k, (hv, val) in enumerate(want):
            x = by[hv]
            self.assertAlmostEqual(x.getRawValue(), val, places=6)
            self.assertAlmostEqual(x.Y.Value, -40.0 - k * 7.0 + OFF, places=3, msg=hv)  # nearest innermost, first 10 mm below the outline
            self.assertAlmostEqual(x.X.Value, ((0 + (20, 50, 85)[k]) / 2.0 - 50.0) * S, places=3)  # span midpoint
        self.assertLess(max(x.Y.Value for x in m) - OFF, -30.0)  # no line inside the outline
        # text boxes of neighbouring lines (font 5) are apart
        ys = sorted(x.Y.Value for x in m)
        self.assertGreaterEqual(min(b - a for a, b in zip(ys, ys[1:])), 5.0)

    def test_baseline_spacing_follows_the_preference(self):
        grp = App.ParamGet(DC.PARAMS)
        had = "CascadeSpacing" in grp.GetFloats()
        old = grp.GetFloat("CascadeSpacing")
        grp.SetFloat("CascadeSpacing", 9.0)
        try:
            p = self.plate()
            d = DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1)], "baseline")
            ys = sorted(x.Y.Value for x in members(d))
            self.assertAlmostEqual(ys[1] - ys[0], 9.0, places=3)
        finally:
            if had:
                grp.SetFloat("CascadeSpacing", old)
            else:
                grp.RemFloat("CascadeSpacing")

    def test_baseline_datum_on_the_right_and_above(self):
        p = self.plate()
        d = DC.make(p.page, p.view, [p.corner(right=True), p.hole(2), p.hole(0)], "baseline", side="above")
        m = {refs(x)[1]: x for x in members(d)}
        self.assertAlmostEqual(m[p.hole(2)].getRawValue(), 15.0, places=6)  # nearest to the datum on the right: innermost
        self.assertAlmostEqual(m[p.hole(0)].getRawValue(), 80.0, places=6)
        self.assertAlmostEqual(m[p.hole(2)].Y.Value, 30.0 + 10.0 + OFF, places=3)
        self.assertAlmostEqual(m[p.hole(0)].Y.Value, 30.0 + 17.0 + OFF, places=3)

    # ---- associativity

    def check_layout(self, p, d):
        """Re-derive the expected layout of ``d`` from the plate's parameters and compare."""
        x0, y0, x1, y1 = p.outline()
        S_ = p.view.Scale
        vx, vy = p.view.X.Value, p.view.Y.Value
        a = 0 if d.Direction == "horizontal" else 1
        sign = -1.0 if d.Side in ("below", "left") else 1.0
        edge = {("horizontal", "below"): y0, ("horizontal", "above"): y1, ("vertical", "left"): x0, ("vertical", "right"): x1}[(d.Direction, d.Side)]
        if d.Kind == "ordinate":
            return self.check_ordinate(p, d)
        for x in members(d):
            r = refs(x)
            pts = [p.view.getVertexByIndex(int(n[6:])) for n in r]
            coord = [(q.X, q.Y)[a] for q in pts]
            self.assertAlmostEqual(x.getRawValue(), abs(coord[0] - coord[1]), places=4)
            along = (coord[0] + coord[1]) / 2.0 * S_
            line = (x.Y.Value + vy if a == 0 else x.X.Value + vx) + (-OFF if a == 0 else OFF)
            self.assertAlmostEqual(x.X.Value if a == 0 else x.Y.Value, along, places=3)
            if d.Kind == "chain":
                self.assertAlmostEqual(line, edge + sign * 10.0, places=3)
        if d.Kind == "baseline":
            ms = sorted(members(d), key=lambda x: x.getRawValue())
            for k, x in enumerate(ms):
                line = (x.Y.Value + vy - OFF) if a == 0 else (x.X.Value + vx + OFF)
                self.assertAlmostEqual(line, edge + sign * (10.0 + 7.0 * k), places=3)

    def test_edits_relay(self):
        p = self.plate()
        sets = [
            DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1), p.hole(2)], "baseline"),
            DC.make(p.page, p.view, [p.hole(0), p.hole(1), p.hole(2)], "chain", side="above"),
            DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1)], "baseline", direction="vertical", side="right"),
            DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1), p.hole(2)], "ordinate"),
        ]
        for d in sets:
            self.check_layout(p, d)
        edits = [
            ("move a hole", lambda: setattr(p.cyls[2].Placement, "Base", V(95, 20, 0)), lambda: p.holes.__setitem__(2, 95.0)),
            ("lengthen the plate", lambda: setattr(p.box, "Length", 110.0), lambda: setattr(p, "L", 110.0)),
            ("raise its width", lambda: setattr(p.box, "Width", 40.0), lambda: None),
            ("change the scale", lambda: setattr(p.view, "Scale", 2.0), lambda: None),
        ]
        for name, do, note in edits:
            do()
            note()
            self.doc.recompute()
            if "width" in name:
                continue
            self.assertEqual(self.settled(), [], name)
            for d in sets:
                self.check_layout(p, d)

    def test_a_taller_plate_moves_the_lines_out(self):
        p = self.plate()
        d = DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1)], "baseline")
        before = max(x.Y.Value for x in members(d))
        p.box.Width = 60.0  # the part grows downwards on the page too: the view recentres, the outline moves 15 mm out each side
        for c in p.cyls:
            c.Placement.Base = V(c.Placement.Base.x, 30, 0)
        self.doc.recompute()
        after = max(x.Y.Value for x in members(d))
        self.assertAlmostEqual(after, -(30.0 * S) - 10.0 + OFF + 0.0, places=3)
        self.assertLess(after, before)

    def test_rows_at_other_scales_vertical_and_datum_between(self):
        for sc in (0.5, 2.0):
            p = self.plate(scale=sc)
            v = DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1)], "baseline", direction="vertical")
            xs = sorted(round(x.X.Value, 3) for x in members(v))
            self.assertEqual(xs, [round(-50 * sc - 10.0 - 7.0 * k - OFF, 3) for k in (1, 0)])  # 10 mm out, then 7 mm steps; the label OFF left of its line
            b = DC.make(p.page, p.view, [p.hole(1), p.hole(2), p.hole(0)], "baseline")  # datum between features
            by = {refs(x)[1]: x for x in members(b)}
            self.assertAlmostEqual(by[p.hole(0)].Y.Value, -20 * sc - 10.0 + OFF, places=3)  # 30 from the datum: innermost
            self.assertAlmostEqual(by[p.hole(2)].Y.Value, -20 * sc - 17.0 + OFF, places=3)  # 35: next
            self.assertAlmostEqual(by[p.hole(0)].X.Value, (-15.0) * sc, places=3)
            self.assertAlmostEqual(by[p.hole(2)].X.Value, 17.5 * sc, places=3)
            self.doc.removeObject(b.Name)
            self.doc.removeObject(v.Name)
            for o in list(self.doc.Objects):
                if o.TypeId == "TechDraw::DrawViewDimension":
                    self.doc.removeObject(o.Name)

    # ---- ordinate

    def check_ordinate(self, p, d):
        sym = DC.symbol_of(d)
        paths, circles, texts = svg_items(sym)
        names = list(d.Vertices)
        a = 0 if d.Direction == "horizontal" else 1
        x0, y0, x1, y1 = p.outline()
        sign = -1.0 if d.Side in ("below", "left") else 1.0
        edge = {("horizontal", "below"): y0, ("horizontal", "above"): y1, ("vertical", "left"): x0, ("vertical", "right"): x1}[(d.Direction, d.Side)]
        pts = {n: p.view.getVertexByIndex(int(n[6:])) for n in names}
        d0 = (pts[names[0]].X, pts[names[0]].Y)[a]
        order = sorted(names, key=lambda n: (((pts[n].X, pts[n].Y)[a]), names.index(n)))
        self.assertEqual([t[0] for t in texts], ["0" if n == names[0] else DC.format_value(abs((pts[n].X, pts[n].Y)[a] - d0)) for n in order])
        self.assertEqual(len(circles), 1)
        self.assertAlmostEqual(circles[0][2], 15.0, places=2)  # 3 mm diameter in 0.1 mm units
        self.assertEqual(len(paths), len(names))
        for n, path, txt in zip(order, paths, texts):
            fx = p.view.X.Value + pts[n].X * p.view.Scale
            fy = p.view.Y.Value + pts[n].Y * p.view.Scale
            start = to_page(sym, path[0])
            last = to_page(sym, path[-1])
            feat = (fx, fy)
            # starts 1.5 mm off the feature, along the axis's normal, outward
            self.assertAlmostEqual(start[a], feat[a], delta=0.002)
            self.assertAlmostEqual(start[1 - a], feat[1 - a] + sign * 1.5, delta=0.002)
            ds = txt[0] == "0"
            if ds:
                self.assertAlmostEqual(last[1 - a], edge + sign * 10.0, delta=0.002)  # the datum line reaches 10 mm like the others
                cc = to_page(sym, circles[0][:2])  # the open circle is centred on that end
                self.assertAlmostEqual(cc[1 - a], last[1 - a], delta=0.002)
                self.assertAlmostEqual(cc[a], last[a], delta=0.002)
            else:
                self.assertAlmostEqual(last[1 - a], edge + sign * 10.0, delta=0.002)  # text end: 10 mm outside the outline
            # text anchor sits on the line's end, rotated for horizontal sets, upright for vertical ones
            self.assertEqual(txt[3], a == 0)
            self.assertAlmostEqual(txt[5], DC.font_size() * 10.0, places=2)
        # no arrow heads (filled shapes) and no polyline with a dimension line: every path is an extension line of 2 or 4 points
        self.assertNotIn("<polygon", sym.Symbol)
        self.assertTrue(all(len(pa) in (2, 4) for pa in paths))
        # text boxes do not overlap and keep 0.5 mm
        fs = DC.font_size()
        ends = []
        for n, path in zip(order, paths):
            ends.append(to_page(sym, path[-1])[a])
        for u, v in zip(ends, ends[1:]):
            self.assertGreaterEqual(v - u, DC.ORD_LINE_H * fs + DC.ORD_TEXT_GAP - 0.01)

    def test_ordinate_layout_numbers(self):
        p = self.plate()
        d = DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1), p.hole(2)], "ordinate")
        sym = DC.symbol_of(d)
        self.assertIsNotNone(sym)
        self.assertEqual(members(d), [])
        paths, circles, texts = svg_items(sym)
        self.assertEqual([t[0] for t in texts], ["0", "20", "50", "85"])
        self.check_ordinate(p, d)
        # the datum circle is at the end of the datum's line, the other lines are straight (holes 30 and 35 apart)
        self.assertEqual([len(pa) for pa in paths], [2, 2, 2, 2])
        self.assertEqual(sorted(t[4] for t in texts), ["end"] * 4)  # below the part: the value ends at the line

    def test_ordinate_above_and_vertical(self):
        p = self.plate()
        d = DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1), p.hole(2)], "ordinate", side="above")
        self.assertEqual([t[4] for t in svg_items(DC.symbol_of(d))[2]], ["start"] * 4)
        self.check_ordinate(p, d)
        v = DC.make(p.page, p.view, [p.corner(), p.corner(top=True), p.hole(1)], "ordinate", direction="vertical", side="right")
        self.check_ordinate(p, v)
        _, _, texts = svg_items(DC.symbol_of(v))
        self.assertEqual(sorted(t[0] for t in texts), ["0", "20", "40"])

    def test_ordinate_jogs_close_values(self):
        p = self.plate(holes=(20.0, 22.0, 24.0))  # 3 mm apart on the page: closer than a text box
        d = DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1), p.hole(2)], "ordinate")
        paths, circles, texts = svg_items(DC.symbol_of(d))
        self.assertEqual([t[0] for t in texts], ["0", "20", "22", "24"])
        self.assertTrue(any(len(pa) == 4 for pa in paths))  # at least one line jogs
        self.check_ordinate(p, d)
        # the jog is a 45 degree segment
        for pa in paths:
            if len(pa) == 4:
                dx, dy = pa[2][0] - pa[1][0], pa[2][1] - pa[1][1]
                self.assertAlmostEqual(abs(dx), abs(dy), places=2)

    def test_ordinate_values_are_unsigned(self):
        p = self.plate()
        d = DC.make(p.page, p.view, [p.hole(1), p.hole(0), p.hole(2)], "ordinate")  # datum in the middle
        texts = svg_items(DC.symbol_of(d))[2]
        self.assertEqual([t[0] for t in texts], ["30", "0", "35"])
        self.check_ordinate(p, d)

    # ---- membership

    def test_add_and_remove(self):
        p = self.plate()
        b = DC.make(p.page, p.view, [p.corner(), p.hole(0)], "baseline")
        DC.add(b, p.hole(2))
        self.assertEqual(len(members(b)), 2)
        self.check_layout(p, b)
        DC.remove(b, p.hole(0))
        self.assertEqual([refs(x)[1] for x in members(b)], [p.hole(2)])
        self.assertAlmostEqual(members(b)[0].Y.Value, -40.0 + OFF, places=3)  # re-packed to the first line
        with self.assertRaises(ValueError):
            DC.add(b, p.hole(2))
        with self.assertRaises(ValueError):
            DC.remove(b, p.corner())
        c = DC.make(p.page, p.view, [p.hole(0), p.hole(2)], "chain")
        self.assertEqual(len(members(c)), 1)
        DC.add(c, p.hole(1))  # splits the span
        self.assertEqual(sorted(round(x.getRawValue(), 3) for x in members(c)), [30.0, 35.0])
        self.check_layout(p, c)
        DC.remove(c, p.hole(1))  # closes it again
        self.assertEqual([round(x.getRawValue(), 3) for x in members(c)], [65.0])
        o = DC.make(p.page, p.view, [p.corner(), p.hole(0)], "ordinate")
        DC.add(o, p.hole(2))
        self.assertEqual([t[0] for t in svg_items(DC.symbol_of(o))[2]], ["0", "20", "85"])
        DC.remove(o, p.hole(0))
        self.assertEqual([t[0] for t in svg_items(DC.symbol_of(o))[2]], ["0", "85"])
        self.assertEqual(self.settled(), [])

    def test_user_deleted_member_leaves_the_set(self):
        p = self.plate()
        b = DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1), p.hole(2)], "baseline")
        mid = next(x for x in members(b) if refs(x)[1] == p.hole(1))
        self.doc.removeObject(mid.Name)
        self.doc.recompute()
        m = sorted(members(b), key=lambda x: x.getRawValue())
        self.assertEqual([round(x.getRawValue()) for x in m], [20, 85])
        self.assertEqual([round(x.Y.Value - OFF, 3) for x in m], [-40.0, -47.0])  # no gap in the stack
        self.assertEqual(list(b.Vertices), [p.corner(), p.hole(0), p.hole(2)])
        c = DC.make(p.page, p.view, [p.hole(0), p.hole(1), p.hole(2)], "chain")
        one = members(c)[0]
        self.doc.removeObject(one.Name)
        self.doc.recompute()
        self.assertEqual(len(members(c)), 1)
        self.assertAlmostEqual(members(c)[0].Y.Value, -40.0 + OFF, places=3)  # the other stays on its line

    def test_driver_delete_and_undo(self):
        p = self.plate()
        d = DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1)], "baseline")
        o = DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1)], "ordinate")
        n = len(self.doc.Objects)
        self.doc.openTransaction("Delete")
        self.doc.removeObject(d.Name)
        self.doc.removeObject(o.Name)
        self.doc.commitTransaction()
        self.assertEqual(len(self.doc.Objects), n - 2 - 2 - 1)
        self.assertEqual(DC.drivers_on(p.page), [])
        self.doc.undo()
        self.assertEqual(len(self.doc.Objects), n)
        d2 = self.doc.getObject(d.Name)
        self.assertEqual(len(members(d2)), 2)
        self.assertIsNotNone(DC.symbol_of(self.doc.getObject(o.Name)))
        self.doc.redo()
        self.assertEqual(len(self.doc.Objects), n - 5)
        self.doc.undo()
        p.box.Length = 120.0
        p.L = 120.0
        self.doc.recompute()
        self.check_layout(p, self.doc.getObject(d.Name))

    def test_make_is_one_undo_step(self):
        p = self.plate()
        n = len(self.doc.Objects)
        DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1)], "baseline")
        self.assertGreater(len(self.doc.Objects), n)
        self.doc.undo()
        self.assertEqual(len(self.doc.Objects), n)

    # ---- save and reopen

    def test_save_reopen_and_relay(self):
        p = self.plate()
        ids = [DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1), p.hole(2)], k).Name for k in ("baseline", "ordinate")]
        ids.append(DC.make(p.page, p.view, [p.hole(0), p.hole(1), p.hole(2)], "chain").Name)
        before = {o.Name: (o.X.Value, o.Y.Value) for o in self.doc.Objects if o.TypeId in ("TechDraw::DrawViewDimension", "TechDraw::DrawViewSymbol")}
        path = os.path.join(self.tmp, "dc.FCStd")
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)
        self.doc = App.openDocument(path)
        self.doc.UndoMode = 1
        for o in self.doc.Objects:
            if o.TypeId in ("TechDraw::DrawViewDimension", "TechDraw::DrawViewSymbol"):
                self.assertAlmostEqual(o.X.Value, before[o.Name][0], delta=0.001)
                self.assertAlmostEqual(o.Y.Value, before[o.Name][1], delta=0.001)
        drivers = [self.doc.getObject(n) for n in ids]
        self.assertTrue(all(isinstance(d.Proxy, DC.DimChainDriver) for d in drivers))
        pl = Plate.__new__(Plate)
        pl.doc, pl.L, pl.holes = self.doc, 100.0, [20.0, 50.0, 85.0]
        pl.page, pl.view, pl.box = self.doc.Page, self.doc.View, self.doc.Plate
        pl.cyls = [self.doc.getObject("H%d" % i) for i in range(3)]
        pl.box.Length = 115.0
        pl.L = 115.0
        self.doc.recompute()
        for d in drivers:
            pl.view = d.View
            self.check_layout(pl, d)

    def test_proxy_is_reattached_after_a_foreign_save(self):
        p = self.plate()
        d = DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(2)], "baseline")
        name = d.Name
        d.Proxy = None  # what a stable FreeCAD re-save leaves: the feature with its properties but without the Python driver
        path = os.path.join(self.tmp, "dc2.FCStd")
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)
        self.doc = App.openDocument(path)
        self.doc.UndoMode = 1
        drv = self.doc.getObject(name)
        self.assertIsInstance(drv.Proxy, DC.DimChainDriver)
        pl = Plate.__new__(Plate)
        pl.doc, pl.L, pl.holes = self.doc, 100.0, [20.0, 50.0, 85.0]
        pl.page, pl.view, pl.box = self.doc.Page, self.doc.View, self.doc.Plate
        pl.cyls = [self.doc.getObject("H%d" % i) for i in range(3)]
        pl.box.Length = 120.0
        pl.L = 120.0
        self.doc.recompute()
        self.check_layout(pl, drv)  # the next edit relays it

    # ---- errors

    def test_errors(self):
        p = self.plate()
        n = len(self.doc.Objects)
        good = [p.corner(), p.hole(0)]
        cases = [
            (dict(vertices=good, kind="spiral"), "spiral"),
            (dict(vertices=good, kind="chain", direction="diagonal"), "diagonal"),
            (dict(vertices=good, kind="chain", side="left"), "left"),
            (dict(vertices=good, kind="chain", direction="vertical", side="below"), "below"),
            (dict(vertices=[p.corner()], kind="baseline"), "at least 2"),
            (dict(vertices=[], kind="ordinate"), "at least 2"),
            (dict(vertices=[p.corner(), p.corner()], kind="baseline"), "duplicate"),
            (dict(vertices=[p.corner(), "Vertex999"], kind="baseline"), "Vertex999"),
            (dict(vertices=[p.corner(), "Edge3"], kind="ordinate"), "Edge3"),
        ]
        for kw, word in cases:
            with self.assertRaises(ValueError) as cm:
                DC.make(p.page, p.view, **kw)
            self.assertIn(word, str(cm.exception))
            self.assertEqual(len(self.doc.Objects), n, kw)
        p.view.Rotation = 30.0
        self.doc.recompute()
        n = len(self.doc.Objects)
        with self.assertRaises(ValueError) as cm:
            DC.make(p.page, p.view, good, "chain")
        self.assertIn("rotated", str(cm.exception))
        self.assertEqual(len(self.doc.Objects), n)

    # ---- R14: a vertex that vanishes from the view after a model edit (ruling 108)

    def _drop_last_hole(self, p):
        """The model edit: the plate loses its last hole (the view re-points at Cut1), inside one undo transaction."""
        self.doc.openTransaction("drop hole")
        p.view.Source = [self.doc.getObject("Cut1")]
        self.doc.recompute()
        self.doc.commitTransaction()

    class _Warnings:
        def __enter__(self):
            self.got = []
            self.old = App.Console.PrintWarning
            App.Console.PrintWarning = lambda text: self.got.append(text)
            return self.got

        def __exit__(self, *a):
            App.Console.PrintWarning = self.old

    def test_r14_ordinate_datum_vanishes(self):
        p = self.plate()
        names = [p.hole(2), p.corner(), p.hole(0)]
        d = DC.make(p.page, p.view, names, "ordinate")
        before = d.Proxy.symbols(d)[0].Symbol
        self._drop_last_hole(p)  # no exception reaches us
        self.assertIn("Invalid", d.State)
        self.assertEqual(
            d.getStatusString(), "datum %s of %s is no longer in %s; pick a new datum or delete the set" % (names[0], d.Label, p.view.Label)
        )
        self.assertEqual(list(d.Vertices), names)  # nothing was dropped or redrawn: the user decides
        self.assertEqual(d.Proxy.symbols(d)[0].Symbol, before)
        self.doc.undo()
        self.doc.recompute()
        self.assertEqual(self.settled(), [])
        self.assertEqual(list(d.Vertices), names)

    def test_r14_ordinate_non_datum_vanishes(self):
        p = self.plate()
        names = [p.corner(), p.hole(0), p.hole(2)]
        d = DC.make(p.page, p.view, names, "ordinate")
        with self._Warnings() as got:
            self._drop_last_hole(p)
        self.assertEqual(len([g for g in got if names[2] in g]), 1, got)
        self.assertEqual(list(d.Vertices), names[:2])
        self.assertEqual(self.settled(), [])
        texts = [t[0] for t in svg_items(d.Proxy.symbols(d)[0])[2]]
        self.assertEqual(sorted(texts), ["0", "20"])
        self.doc.undo()
        self.doc.recompute()
        self.assertEqual(list(d.Vertices), names)
        self.assertEqual(sorted(t[0] for t in svg_items(d.Proxy.symbols(d)[0])[2]), ["0", "20", "85"])

    # ---- set_datum (slice 10)

    def ordinate_texts(self, d):
        return sorted((t[0], round(t[1], 3), round(t[2], 3)) for t in svg_items(d.Proxy.symbols(d)[0])[2])

    def datum_cases(self):
        """-> [(label, new datum, start names, dead?)] on a fresh plate each time: dead and live datums, a hole centre and an outline corner."""
        return [
            ("dead, to the corner", lambda p: p.corner(), lambda p: [p.hole(2), p.corner(), p.hole(0)], True),
            ("dead, to a hole centre", lambda p: p.hole(0), lambda p: [p.hole(2), p.corner(), p.hole(0)], True),
            ("live, to a hole centre", lambda p: p.hole(1), lambda p: [p.corner(), p.hole(0), p.hole(1), p.hole(2)], False),
            ("live, to the far corner", lambda p: p.corner(right=True), lambda p: [p.corner(), p.hole(0), p.hole(1), p.corner(right=True)], False),
        ]

    def test_set_datum_ordinate_equals_a_fresh_make_and_moves_the_dxf_origin(self):
        for label, target, start, dead in self.datum_cases():
            p = self.plate()
            names = start(p)
            d = DC.make(p.page, p.view, names, "ordinate")
            if dead:
                self._drop_last_hole(p)
                self.assertIn("Invalid", d.State, label)
            new = target(p)
            n_undo = self.doc.UndoCount
            self.assertIs(DC.set_datum(d, new), d)
            self.assertEqual(self.doc.UndoCount, n_undo + 1, label)
            self.assertEqual(d.Vertices[0], new, label)
            self.assertEqual(self.settled(), [], label)
            self.assertNotIn("Invalid", d.State, label)
            after = list(d.Vertices)
            old = names[0]
            self.assertEqual(old in after, not dead, label)  # a live old datum stays as an ordinary vertex; a dead one leaves
            fresh = DC.make(p.page, p.view, after, "ordinate")
            self.assertEqual(self.ordinate_texts(d), self.ordinate_texts(fresh), label)
            self.assertEqual(d.Proxy.symbols(d)[0].Symbol, fresh.Proxy.symbols(fresh)[0].Symbol, label)
            axis, entries = DC.ordinate_dxf_entries(d)
            self.assertEqual(len(entries), len(after), label)
            frame = DC.Frame(p.view, after)
            want = frame.page(new)
            for e in entries:
                self.assertAlmostEqual(e["datum"][0], want[0], delta=0.001, msg=label)
                self.assertAlmostEqual(e["datum"][1], want[1], delta=0.001, msg=label)
            self.assertEqual([e["name"] for e in entries if e["is_datum"]], [new], label)
            App.closeDocument(self.doc.Name)
            self.doc = App.newDocument("DimChainTest")
            self.doc.UndoMode = 1

    def test_set_datum_ordinate_values_follow_the_new_datum(self):
        p = self.plate()
        d = DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1), p.hole(2)], "ordinate")
        DC.set_datum(d, p.hole(1))
        self.assertEqual(sorted(t[0] for t in svg_items(d.Proxy.symbols(d)[0])[2]), ["0", "30", "35", "50"])

    def test_set_datum_is_one_undo_step_and_survives_undo_redo_save_and_reopen(self):
        p = self.plate()
        names = [p.corner(), p.hole(0), p.hole(1), p.hole(2)]
        d = DC.make(p.page, p.view, names, "ordinate")
        sym0 = d.Proxy.symbols(d)[0].Symbol
        DC.set_datum(d, p.hole(1))
        sym1 = d.Proxy.symbols(d)[0].Symbol
        v1 = list(d.Vertices)
        self.assertNotEqual(sym0, sym1)
        self.doc.undo()
        self.doc.recompute()
        self.assertEqual(list(d.Vertices), names)
        self.assertEqual(d.Proxy.symbols(d)[0].Symbol, sym0)
        self.doc.redo()
        self.doc.recompute()
        self.assertEqual(list(d.Vertices), v1)
        self.assertEqual(d.Proxy.symbols(d)[0].Symbol, sym1)
        path, name = os.path.join(self.tmp, "sd.FCStd"), d.Name
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)
        self.doc = App.openDocument(path)
        self.doc.UndoMode = 1
        d = self.doc.getObject(name)
        self.assertEqual(list(d.Vertices), v1)
        self.assertEqual(d.Proxy.symbols(d)[0].Symbol, sym1)
        self.assertEqual(self.settled(), [])
        # a further model edit relays the set from the new datum
        box = self.doc.Plate
        box.Length = 115.0
        self.doc.recompute()
        self.assertEqual(self.settled(), [])
        self.assertEqual(d.Vertices[0], v1[0])
        self.assertEqual(sorted(t[0] for t in svg_items(d.Proxy.symbols(d)[0])[2]), ["0", "30", "35", "50"])

    def test_set_datum_refusals_change_nothing_and_a_repeat_is_a_no_op(self):
        p = self.plate()
        ordinate = DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1)], "ordinate")
        chain = DC.make(p.page, p.view, [p.hole(0), p.hole(1), p.hole(2)], "chain")
        n_obj, n_undo = len(self.doc.Objects), self.doc.UndoCount
        before = list(ordinate.Vertices)
        for drv, vertex, text in ((chain, p.hole(0), "a chain has no datum"), (ordinate, "Vertex999", "Vertex999 is not a vertex of")):
            with self.assertRaises(ValueError) as cm:
                DC.set_datum(drv, vertex)
            self.assertIn(text, str(cm.exception))
            self.assertNotIn("\n", str(cm.exception))
        self.assertIs(DC.set_datum(ordinate, p.corner()), ordinate)  # already the datum
        self.assertEqual((len(self.doc.Objects), self.doc.UndoCount, list(ordinate.Vertices)), (n_obj, n_undo, before))
        self.assertIsNone(App.getActiveTransaction())
        # a dead datum and one other vertex: making the other the datum would leave one vertex
        App.closeDocument(self.doc.Name)
        self.doc = App.newDocument("DimChainTest")
        self.doc.UndoMode = 1
        q = self.plate()
        stored = [q.hole(2), q.corner()]
        two = DC.make(q.page, q.view, stored, "ordinate")
        self._drop_last_hole(q)
        n_obj, n_undo = len(self.doc.Objects), self.doc.UndoCount
        with self.assertRaises(ValueError) as cm:
            DC.set_datum(two, stored[1])
        self.assertEqual(str(cm.exception), "a set keeps at least 2 vertices")
        self.assertEqual((len(self.doc.Objects), self.doc.UndoCount, list(two.Vertices)), (n_obj, n_undo, stored))

    def test_r14_baseline_feature_vanishes(self):
        p = self.plate()
        names = [p.corner(), p.hole(0), p.hole(1), p.hole(2)]
        d = DC.make(p.page, p.view, names, "baseline")
        with self._Warnings() as got:
            self._drop_last_hole(p)
            self.doc.recompute()
        self.assertEqual(len([g for g in got if names[3] in g]), 1, got)
        self.assertEqual(list(d.Vertices), names[:3])
        self.assertEqual(len(members(d)), 2)
        self.assertEqual(self.settled(), [])
        vals = sorted(round(m.getRawValue(), 3) for m in members(d))
        self.assertEqual(vals, [20.0, 50.0])
        edge, sign = DC._outside(DC.Frame(p.view, names[:3]), "horizontal", "below")
        ys = sorted(m.Y.Value + p.view.Y.Value - DC.line_offset() for m in members(d))
        self.assertAlmostEqual(ys[1], p.outline()[1] - 10.0, places=3)  # re-packed: the first line is still 10 mm out
        self.assertAlmostEqual(ys[0], p.outline()[1] - 17.0, places=3)
        self.doc.undo()
        self.doc.recompute()
        self.assertEqual(list(d.Vertices), names)
        self.assertEqual(len(members(d)), 3)
        self.assertEqual(self.settled(), [])

    def test_r14_chain_feature_vanishes(self):
        p = self.plate()
        names = [p.corner(), p.hole(0), p.hole(1), p.hole(2)]
        d = DC.make(p.page, p.view, names, "chain")
        with self._Warnings() as got:
            self._drop_last_hole(p)
            self.doc.recompute()
        self.assertEqual(len([g for g in got if names[3] in g]), 1, got)
        self.assertEqual(list(d.Vertices), names[:3])
        self.assertEqual(sorted(round(m.getRawValue(), 3) for m in members(d)), [20.0, 30.0])
        self.assertEqual(self.settled(), [])
        self.doc.undo()
        self.doc.recompute()
        self.assertEqual(list(d.Vertices), names)
        self.assertEqual(len(members(d)), 3)
        self.assertEqual(self.settled(), [])

    def test_r18_parked_members_are_up_to_date_after_the_recompute_that_parks_them(self):
        for kind, ix in (("chain", 3), ("baseline", 3)):
            p = self.plate()
            names = [p.corner(), p.hole(0), p.hole(1), p.hole(2)]
            d = DC.make(p.page, p.view, names, kind)
            self._drop_last_hole(p)
            self.doc.recompute()
            parked = DC.DimChainDriver.members(d, parked=True)
            self.assertGreaterEqual(len(parked), 1, kind)
            for m in parked:
                self.assertEqual([s for s in m.State if s in ("Touched", "Invalid")], [], "%s %s" % (kind, m.Name))
            self.assertIn(parked[0], d.Proxy.owned(d), kind)
            App.closeDocument(self.doc.Name)
            self.doc = App.newDocument("DimChainTest")
            self.doc.UndoMode = 1

    def test_r14_chain_middle_vanishes_is_bridged(self):
        p = self.plate()
        names = [p.corner(), p.hole(0), p.hole(1), p.hole(2)]
        d = DC.make(p.page, p.view, names, "chain")
        # the hole in the middle of the chain goes: a shorter chain is bridged over the gap (indices after it shift)
        gone = names[2]
        self.doc.openTransaction("edit")
        self.doc.getObject("Cut1").Tool = self.doc.getObject("H0")  # Cut1 no longer cuts H1: Cut2 = Cut1 minus H2
        self.doc.recompute()
        self.doc.commitTransaction()
        self.assertEqual(self.settled(), [])
        self.assertEqual(len(members(d)), len(d.Vertices) - 1)
        self.assertNotIn("Invalid", [o.State for o in members(d)])

    # ---- R15: no member for a span under 1e-4 mm (ruling 109)

    def test_r15_zero_span_makes_no_member_and_returns_when_features_part(self):
        for kind in ("chain", "baseline"):
            doc = App.newDocument("R15" + kind)
            doc.UndoMode = 1
            try:
                p = Plate(doc)
                names = [p.hole(0), p.hole(1), p.hole(2)]  # all three centres lie on y = 20
                d = DC.make(p.page, p.view, names, kind, direction="vertical")
                self.assertEqual(len(members(d)), 0, kind)
                self.assertEqual(sorted(d.Vertices), sorted(names))
                self.assertEqual([o.Name for o in doc.Objects if "Invalid" in o.State], [])
                doc.getObject("H1").Placement.Base = V(50, 25, 0)  # the middle hole moves 5 mm up
                doc.recompute()
                self.assertEqual(len(members(d)), 1, kind)
                self.assertAlmostEqual(members(d)[0].getRawValue(), 5.0, places=6)
                self.assertEqual(sorted(d.Vertices), sorted(names))
                doc.getObject("H1").Placement.Base = V(50, 20, 0)  # and back
                doc.recompute()
                self.assertEqual(len(members(d)), 0, kind)
                self.assertEqual(sorted(d.Vertices), sorted(names))
            finally:
                App.closeDocument(doc.Name)

    def test_r15_ordinate_keeps_entries_at_one_coordinate(self):
        p = self.plate()
        d = DC.make(p.page, p.view, [p.hole(0), p.hole(1), p.hole(2)], "ordinate", direction="vertical")
        texts = [t[0] for t in svg_items(d.Proxy.symbols(d)[0])[2]]
        self.assertEqual(sorted(texts), ["0", "0", "0"])

    # ---- DXF

    def test_dxf_ordinate_entities(self):
        try:
            import ezdxf
        except ImportError:
            self.skipTest("ezdxf is not installed")
        import DxfExport

        p = self.plate()
        o = DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(1), p.hole(2)], "ordinate")
        DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(2)], "baseline")
        DC.make(p.page, p.view, [p.corner(), p.hole(0), p.hole(2)], "ordinate", direction="vertical", side="right")
        path = os.path.join(self.tmp, "o.dxf")
        report = DxfExport.write(p.page, path)
        self.assertFalse([s for s in report["skipped"] if "symbol" in str(s).lower() or "ordinate" in str(s).lower()], report["skipped"])
        doc = ezdxf.readfile(path)
        self.assertEqual(len(doc.audit().errors), 0)
        dims = list(doc.modelspace().query("DIMENSION"))
        ords = [e for e in dims if e.dxf.dimtype & 6 == 6]
        lin = [e for e in dims if e.dxf.dimtype & 7 == 0]
        self.assertEqual(len(ords), 4 + 3)
        self.assertEqual(len(lin), 2)  # the baseline members stay linear dimensions
        xs = sorted((round(e.dxf.defpoint2.x, 3), round(e.dxf.defpoint3.y, 3), e.dxf.text) for e in ords if e.dxf.dimtype & 64)
        self.assertEqual(xs, [(75.0, 70.0, "0"), (105.0, 70.0, "20"), (150.0, 70.0, "50"), (202.5, 70.0, "85")])  # feature x, leader end y (outline 80 - 10)
        ys = sorted((round(e.dxf.defpoint2.y, 3), round(e.dxf.defpoint3.x, 3), e.dxf.text) for e in ords if not e.dxf.dimtype & 64)
        # the vertical set: feature y (corner 80, holes 110), leader end x (outline right 225 + 10); both holes are 20 from the corner's y
        self.assertEqual(ys, [(80.0, 235.0, "0"), (110.0, 235.0, "20"), (110.0, 235.0, "20")])
        for e in ords:
            self.assertEqual(e.dxf.layer, "DIMENSIONS")
            for m in doc.blocks.get(e.dxf.geometry).query("MTEXT"):  # libredwg's dxf2dwg rejects group 50 on MTEXT
                self.assertFalse(m.dxf.hasattr("rotation"))
        self.assertEqual(o.Kind, "ordinate")


if __name__ == "__main__":
    unittest.main()
