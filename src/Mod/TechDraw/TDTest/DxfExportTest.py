# SPDX-License-Identifier: LGPL-2.1-or-later

# G04 slice 7: layered DXF export of a page (DxfExport.write, DxfExport.export_page), headless.
# Run with: FreeCADCmd -t TestTechDrawApp.DxfExportTest   (part of TestTechDrawApp)
#
# Public sources: the AutoCAD DXF Reference (group codes, DIMENSION types, MTEXT codes), the documentation of ezdxf, the line-type
# CSV files in Mod/TechDraw/LineGroup (read again here by this test's own small parser) and FreeCAD's stock writer
# (TechDraw.writeDXFPage).  The truth for edges is each view's own getVisibleEdges(True) and getHiddenEdges(True).
# The GUI routes (TechDrawGui.export, the menu command) are exercised by the GUI script in harness/g04.

import hashlib
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

import FreeCAD as App
import Part
import TechDraw
from FreeCAD import Vector as V

import DxfExport as DX

from .TechDrawTestUtilities import createPageWithSVGTemplate

LG = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "LineGroup")
FILES = {"ISO": "ISO.128.20.1996", "ANSI": "ANSI.Y14.2M.1992", "ASME": "ASME.Y14.2.2008"}


def csv(path):
    rows = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and line[0] not in "#;":
            rows.append([c.strip() for c in line.split(",")])
    return rows


def expected_pattern(std, number, width):
    """The oracle of this test: dash pattern of line ``number`` from the CSV files, element by element."""
    el = {r[0]: float(r[1]) for r in csv(os.path.join(LG, FILES[std] + ".ElementDef.csv"))}
    for r in csv(os.path.join(LG, FILES[std] + ".LineDef.csv")):
        if int(r[0]) == number:
            if r[2] == "n/a":
                return None
            out = []
            for n in r[2:]:
                ln = el[n] * (width if std == "ISO" else 1.0)
                out.append(0.0 if n == "Dot" else (-ln if n in ("Gap", "Space") else ln))
            return [sum(abs(v) for v in out)] + out


def close(a, b, tol=1e-6):
    return abs(a - b) <= tol


class DxfExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import ezdxf

        cls.ezdxf = ezdxf
        cls.tmp = tempfile.mkdtemp()
        doc = App.newDocument("DxfExportTest")
        cls.doc = doc
        part = doc.addObject("Part::Feature", "Plate")
        part.Shape = Part.makeBox(60, 40, 25).cut(Part.makeCylinder(8, 25, V(30, 20, 0)))
        doc.recompute()
        cls.page = page = createPageWithSVGTemplate(doc)

        def view(name, rot, x, y, scale):
            v = doc.addObject("TechDraw::DrawViewPart", name)
            page.addView(v)
            v.Source = [part]
            v.Direction = V(0, 0, 1)
            v.XDirection = V(1, 0, 0)
            v.ScaleType = "Custom"
            v.Scale = scale
            v.Rotation = rot
            v.X = x
            v.Y = y
            v.HardHidden = True
            return v

        cls.v0 = view("V0", 0, 100, 150, 0.5)
        cls.v30 = view("V30", 30, 300, 150, 0.5)
        g = doc.addObject("TechDraw::DrawProjGroup", "G")
        page.addView(g)
        g.Source = [part]
        g.ScaleType = "Custom"
        g.Scale = 0.5
        g.addProjection("Front")
        g.addProjection("Top")
        g.X = 200
        g.Y = 60
        cls.group = g
        doc.recompute()
        for it in g.Views:
            it.HardHidden = True
        doc.recompute()
        cls.cos = {}
        for v in (cls.v0, cls.v30):
            v.makeCosmeticLine(V(-10, 5, 0), V(70, 12, 0))
            t2 = v.makeCosmeticLine(V(5, -5, 0), V(15, 45, 0))
            v.getCosmeticEdge(t2).Format = {"style": 1, "weight": 0.7, "color": (1.0, 0.0, 0.0, 1.0), "visible": True, "lineNumber": 4}
            v.makeCenterLine(["Edge0", "Edge2"], 1)
        doc.recompute()
        sec = doc.addObject("TechDraw::DrawViewSection", "Section")
        page.addView(sec)
        sec.BaseView = cls.v0
        sec.Source = [part]
        sec.SectionNormal = V(0, -1, 0)
        sec.SectionDirection = "Down"
        sec.SectionOrigin = V(30, 20, 12)
        sec.Scale = 1.0
        sec.X = 100
        sec.Y = 60
        det = doc.addObject("TechDraw::DrawViewDetail", "Detail")
        page.addView(det)
        det.BaseView = cls.v0
        det.Source = [part]
        det.AnchorPoint = V(30, 20, 0)
        det.Radius = 8
        det.Scale = 2.0
        det.X = 340
        det.Y = 60
        doc.recompute()
        hatch = doc.addObject("TechDraw::DrawHatch", "Hatch")
        hatch.Source = (sec, ["Face0"])
        hatch.HatchPattern = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Patterns", "general_steel.svg")
        edges = []
        for i in range(60):
            try:
                e = cls.v0.getEdgeByIndex(i)
            except Exception:
                break
            edges.append((i, type(e.Curve).__name__))
        lines = [i for i, c in edges if c == "Line"]
        circ = [i for i, c in edges if c == "Circle"][0]

        def dim(name, typ, refs, x, y, **props):
            d = doc.addObject("TechDraw::DrawViewDimension", name)
            page.addView(d)
            d.Type = typ
            d.References2D = refs
            d.X = x
            d.Y = y
            for k, val in props.items():
                setattr(d, k, val)
            return d

        e = lambda i: (cls.v0, "Edge%d" % i)  # noqa: E731
        cls.dims = {
            "Distance": dim("DimDist", "Distance", [e(lines[0])], -45, 0),
            "DistanceX": dim("DimX", "DistanceX", [e(lines[1])], 0, -35),
            "DistanceY": dim("DimY", "DistanceY", [e(lines[0])], 45, 0),
            "Radius": dim("DimRad", "Radius", [e(circ)], 20, 15),
            "Diameter": dim("DimDia", "Diameter", [e(circ)], -10, 20),
            "Angle": dim("DimAng", "Angle", [e(lines[0]), e(lines[1])], 35, 30),
            "Angle3Pt": dim("DimAng3", "Angle3Pt", [(cls.v0, "Vertex0"), (cls.v0, "Vertex1"), (cls.v0, "Vertex2")], -35, 30),
            "Equal": dim("DimEq", "DistanceX", [e(lines[1])], 0, -45, EqualTolerance=True, OverTolerance=0.1),
            "Unequal": dim("DimUn", "DistanceX", [e(lines[1])], 0, -55, EqualTolerance=False, OverTolerance=0.2, UnderTolerance=-0.1),
            "Arbitrary": dim("DimArb", "DistanceX", [e(lines[1])], 0, -65, Arbitrary=True, FormatSpec="ARB 25 H7"),
            "Exact": dim("DimExact", "DistanceX", [e(lines[1])], 0, -75, TheoreticalExact=True),
        }
        an = doc.addObject("TechDraw::DrawViewAnnotation", "Anno")
        page.addView(an)
        an.Text = ["MATERIAL: S235 ⌀ 16 ± 0.1", "SCALE 1:2 90° m²"]
        an.TextSize = 5
        an.X = 60
        an.Y = 30
        an.Rotation = 30
        cls.anno = an
        ra = doc.addObject("TechDraw::DrawRichAnno", "Note")
        page.addView(ra)
        ra.AnnoText = (
            "<html><head><style>p{margin:0}</style></head><body><p>NOTE:  <b>deburr</b>\n all edges<br />± 0.1 mm</p>"
            "<p>second &lt;para&gt; &amp; Ø</p></body></html>"
        )
        ra.X = 300
        ra.Y = 30
        cls.rich = ra
        b = doc.addObject("TechDraw::DrawViewBalloon", "Balloon")
        page.addView(b)
        b.SourceView = cls.v0
        b.Text = "7"
        b.X = 190
        b.Y = 230
        doc.recompute()
        cls.report = DX.write(page, os.path.join(cls.tmp, "iso.dxf"), "ISO")
        cls.dxf = ezdxf.readfile(os.path.join(cls.tmp, "iso.dxf"))

    @classmethod
    def tearDownClass(cls):
        App.closeDocument(cls.doc.Name)

    # -- helpers -------------------------------------------------------------------------------------------------------

    def msp(self, layer=None, kind=None):
        q = "*" if kind is None else kind
        out = list(self.dxf.modelspace().query(q))
        return [e for e in out if layer is None or e.dxf.layer == layer]

    def off(self, v):
        x, y = v.X.Value, v.Y.Value
        if v.Name in [i.Name for i in self.group.Views]:
            x += self.group.X.Value
            y += self.group.Y.Value
        return x, y

    def truth(self, view, hidden):
        """Line and circle geometry of a view's own edges, in page mm."""
        ox, oy = self.off(view)
        edges = list(view.getHiddenEdges(True)) if hidden else list(view.getVisibleEdges(True))
        if not hidden:
            tail = len(view.CosmeticEdges) + len(view.CenterLines)
            edges = edges[: len(edges) - tail]
        out = []
        for e in edges:
            c = e.Curve
            if type(c).__name__ == "Line":
                a, b = e.Vertexes[0].Point, e.Vertexes[-1].Point
                out.append(("L", frozenset({(round(a.x + ox, 4), round(a.y + oy, 4)), (round(b.x + ox, 4), round(b.y + oy, 4))})))
            elif type(c).__name__ == "Circle" and e.isClosed():
                out.append(("C", (round(c.Center.x + ox, 4), round(c.Center.y + oy, 4), round(c.Radius, 4))))
        return out

    def present(self, layer):
        out = []
        for e in self.msp(layer):
            if e.dxftype() == "LINE":
                out.append(("L", frozenset({(round(e.dxf.start.x, 4), round(e.dxf.start.y, 4)), (round(e.dxf.end.x, 4), round(e.dxf.end.y, 4))})))
            elif e.dxftype() == "CIRCLE":
                out.append(("C", (round(e.dxf.center.x, 4), round(e.dxf.center.y, 4), round(e.dxf.radius, 4))))
        return out

    # -- ruling 75: entry points ---------------------------------------------------------------------------------------

    def test_report_shape(self):
        self.assertEqual(self.report["standard"], "ISO")
        self.assertIsInstance(self.report["skipped"], list)
        for s in self.report["skipped"]:
            self.assertIn("name", s)
            self.assertIn("reason", s)
            self.assertTrue(s["reason"])

    def test_export_page_follows_the_preference_and_ezdxf_availability(self):
        warnings = []
        out = os.path.join(self.tmp, "ep.dxf")
        prefs = "User parameter:BaseApp/Preferences/Mod/TechDraw/"
        fake = mock.Mock()
        fake.GetBool = lambda key, default: False if key == "LayeredDxf" else default
        with mock.patch.object(DX, "_warn", warnings.append):
            DX.export_page(self.page, out)  # default: layered, one warning naming the objects not written
            self.assertIn(b"AC1027", open(out, "rb").read())
            self.assertEqual(len(warnings), 1)
            with mock.patch.object(DX, "_group", lambda name: fake if name == "General" else DX.App.ParamGet(prefs + name)):
                DX.export_page(self.page, out)
            self.assertIn(b"AC1014", open(out, "rb").read())  # stock writer: R14
            self.assertEqual(len(warnings), 2)
            self.assertIn("LayeredDxf", warnings[1])
            with mock.patch.dict(sys.modules, {"ezdxf": None}):
                DX.export_page(self.page, out)
            self.assertIn(b"AC1014", open(out, "rb").read())
            self.assertEqual(len(warnings), 3)
            self.assertIn("ezdxf", warnings[2])
        self.assertEqual(DX._group("General").GetBool("LayeredDxf", True), DX.layered_enabled())

    def test_stock_writer_is_unchanged(self):
        a, b = os.path.join(self.tmp, "s1.dxf"), os.path.join(self.tmp, "s2.dxf")
        TechDraw.writeDXFPage(self.page, a)
        time.sleep(1.1)
        TechDraw.writeDXFPage(self.page, b)
        self.assertEqual(open(a, "rb").read(), open(b, "rb").read())
        head = open(a, "rb").read()
        self.assertIn(b"AC1014", head)
        self.assertEqual(self.report["path"], os.path.join(self.tmp, "iso.dxf"))

    # -- ruling 76: format ---------------------------------------------------------------------------------------------

    def test_header_and_extents(self):
        h = self.dxf.header
        self.assertEqual(h["$ACADVER"], "AC1027")
        self.assertEqual(h["$INSUNITS"], 4)
        self.assertEqual(h["$MEASUREMENT"], 1)
        self.assertEqual(h["$LTSCALE"], 1.0)
        self.assertEqual(h["$CELTSCALE"], 1.0)
        lo, hi = h["$EXTMIN"], h["$EXTMAX"]
        self.assertLess(abs(lo[0]), 1e6)
        self.assertLess(abs(hi[0]), 1e6)
        xs, ys = [], []
        for e in self.msp(kind="LINE"):
            for p in (e.dxf.start, e.dxf.end):
                xs.append(p.x)
                ys.append(p.y)
        self.assertLessEqual(lo[0], min(xs) + 1e-6)
        self.assertLessEqual(lo[1], min(ys) + 1e-6)
        self.assertGreaterEqual(hi[0], max(xs) - 1e-6)
        self.assertGreaterEqual(hi[1], max(ys) - 1e-6)
        self.assertLess(hi[0] - lo[0], 1000)  # the page's size, not ezdxf's +-1e20
        self.assertLess(hi[1] - lo[1], 1000)

    def test_file_is_a_function_of_the_page(self):
        import ezdxf

        ezdxf.options.write_fixed_meta_data_for_testing = False
        before = False
        a, b = os.path.join(self.tmp, "d1.dxf"), os.path.join(self.tmp, "d2.dxf")
        DX.write(self.page, a, "ISO")
        time.sleep(1.2)
        DX.write(self.page, b, "ISO")
        self.assertEqual(hashlib.sha256(open(a, "rb").read()).hexdigest(), hashlib.sha256(open(b, "rb").read()).hexdigest())
        self.assertEqual(ezdxf.options.write_fixed_meta_data_for_testing, before)  # no global left changed
        self.assertEqual(open(a, "rb").read(), open(os.path.join(self.tmp, "iso.dxf"), "rb").read())

    def test_utf8_symbols_are_intact(self):
        raw = open(os.path.join(self.tmp, "iso.dxf"), "rb").read()
        text = raw.decode("utf-8")
        for ch in ("⌀", "°", "±", "²"):
            self.assertIn(ch, text)
        self.assertNotIn("âŒ€", text)  # no mojibake of the diameter sign
        dia = [e for e in self.msp(kind="DIMENSION") if e.dxf.dimtype & 7 == 3][0]
        self.assertTrue(dia.dxf.text.startswith("⌀"))

    # -- ruling 77: layers and line types ------------------------------------------------------------------------------

    def test_layers(self):
        names = {l.dxf.name: l for l in self.dxf.layers}
        for n in ("VISIBLE", "HIDDEN", "CENTER", "COSMETIC", "DIMENSIONS", "TEXT"):
            self.assertIn(n, names)
            self.assertEqual(names[n].dxf.color, 7)
        self.assertEqual(names["VISIBLE"].dxf.linetype.upper(), "CONTINUOUS")
        self.assertEqual(names["VISIBLE"].dxf.lineweight, 70)  # thick of the default group FC 0.70mm
        self.assertEqual(names["HIDDEN"].dxf.lineweight, 35)
        self.assertEqual(names["CENTER"].dxf.lineweight, 35)
        for layer in ("VISIBLE", "HIDDEN", "CENTER", "COSMETIC", "DIMENSIONS", "TEXT"):
            self.assertGreater(len(self.msp(layer)), 0, layer)

    def pattern(self, doc, name):
        """[total length, element, ...] of an LTYPE as written (group codes 40 and 49)."""
        tags = doc.linetypes.get(name).pattern_tags.tags
        return [t.value for t in tags if t.code == 40] + [t.value for t in tags if t.code == 49]

    def test_iso_hidden_and_center_line_types_from_the_csv(self):
        layers = {l.dxf.name: l.dxf.linetype for l in self.dxf.layers}
        w = 0.35
        self.assertTrue(close(self.pattern(self.dxf, layers["HIDDEN"])[0], expected_pattern("ISO", 2, w)[0]))
        for layer, number in (("HIDDEN", 2), ("CENTER", 4)):
            got = self.pattern(self.dxf, layers[layer])
            want = expected_pattern("ISO", number, w)
            self.assertEqual(len(got), len(want), layer)
            for a, b in zip(got, want):
                self.assertTrue(close(a, b), (layer, got, want))

    def test_widths_scale_iso_lengths_but_not_ansi_and_asme(self):
        out = {}
        for std in ("ISO", "ANSI", "ASME"):
            p = os.path.join(self.tmp, "std_%s.dxf" % std)
            DX.write(self.page, p, std)
            d = self.ezdxf.readfile(p)
            layers = {l.dxf.name: l.dxf.linetype for l in d.layers}
            out[std] = d
            for layer, number in (("HIDDEN", DX.HIDDEN_LINE[std]), ("CENTER", DX.CENTER_LINE[std])):
                got = self.pattern(d, layers[layer])
                want = expected_pattern(std, number, 0.35)
                self.assertEqual(len(got), len(want), (std, layer))
                for a, b in zip(got, want):
                    self.assertTrue(close(a, b), (std, layer, got, want))
        iso = self.pattern(out["ISO"], {l.dxf.name: l.dxf.linetype for l in out["ISO"].layers}["HIDDEN"])
        ansi = self.pattern(out["ANSI"], {l.dxf.name: l.dxf.linetype for l in out["ANSI"].layers}["HIDDEN"])
        self.assertTrue(close(max(abs(v) for v in iso[1:]), 12 * 0.35))  # dash 12 pen widths of the thin line, not 12 mm
        self.assertTrue(close(max(abs(v) for v in ansi[1:]), 3.0))  # ANSI dash 3 mm

    # -- ruling 78: edges ----------------------------------------------------------------------------------------------

    def test_every_edge_once_on_the_right_layer_with_scale_offset_and_rotation(self):
        for view in [self.v0, self.v30] + list(self.group.Views):
            for hidden, layer in ((False, "VISIBLE"), (True, "HIDDEN")):
                present = self.present(layer)
                for t in self.truth(view, hidden):
                    self.assertEqual(present.count(t), 1, (view.Name, layer, t))

    def test_rotated_view_is_not_rotated_twice(self):
        t = self.truth(self.v30, False)
        lines = [g for k, g in t if k == "L"]
        self.assertTrue(lines)
        # the unrotated 60 x 40 corner (-15, -10) rotated 30 degrees about the view centre lies at (-7.99, -16.16) from X, Y
        ox, oy = self.off(self.v30)
        self.assertTrue(any((round(ox - 7.9904, 2), round(oy - 16.1603, 2)) in {(round(a, 2), round(b, 2)) for a, b in g} for g in lines))

    def test_each_geometric_edge_once_duplicates_dropped(self):
        for layer in ("VISIBLE", "HIDDEN"):
            seen = self.present(layer)
            self.assertEqual(len(seen), len(set(seen)), layer)
        d = DX._Dedup()
        self.assertTrue(d.add(("L", (0.0, 0.0), (10.0, 0.0))))
        self.assertFalse(d.add(("L", (10.0, 0.0), (0.0, 1e-7))))  # reversed and within 1e-6
        self.assertTrue(d.add(("L", (10.0, 0.0), (0.0, 1e-4))))
        self.assertTrue(d.add(("C", (0.0, 0.0), 5.0)))
        self.assertFalse(d.add(("C", (0.0, 5e-7), 5.0)))
        self.assertEqual(d.dropped, 2)

    def test_hidden_edges_are_not_on_visible(self):
        visible = self.present("VISIBLE")
        hidden = self.present("HIDDEN")
        found = 0
        for view in [self.v0, self.v30] + list(self.group.Views):
            vis = self.truth(view, False)
            for h in self.truth(view, True):
                if h not in vis:  # a hidden edge that is not also a visible one
                    found += 1
                    self.assertIn(h, hidden)
                    self.assertNotIn(h, visible)
        self.assertGreater(found, 0)

    def test_curve_conversion(self):
        for shape, kinds in (
            (Part.makeCircle(5, V(1, 2, 0), V(0, 0, 1), 10, 100), {"ARC"}),
            (Part.makeCircle(5, V(1, 2, 0), V(0, 0, -1), 10, 100), {"ARC"}),
            (Part.makeCircle(5, V(1, 2, 0)), {"CIRCLE"}),
            (Part.Ellipse(V(0, 0, 0), 6, 3).toShape(), {"ELLIPSE"}),
            (Part.Ellipse(V(0, 0, 0), 6, 3).toShape(0.5, 2.5), {"ELLIPSE"}),
            (Part.BSplineCurve([V(0, 0, 0), V(3, 4, 0), V(6, -2, 0), V(9, 3, 0), V(12, 0, 0)], None, None, False, 3, None, False).toShape(), {"SPLINE"}),
            (Part.makeLine(V(0, 0, 0), V(3, 4, 0)), {"LINE"}),
        ):
            g = DX.edge_to_geom(shape, (10.0, 20.0))
            ez = self.ezdxf.new("R2013")
            e = DX._add_geom(ez.modelspace(), g, {})
            self.assertIn(e.dxftype(), kinds)
            # every sampled point of the FreeCAD edge lies on the written entity (offset 10, 20)
            if e.dxftype() == "ARC":
                self.assertTrue(close(e.dxf.radius, 5.0))
                start, end = e.start_point, e.end_point
                a, b = shape.valueAt(shape.FirstParameter), shape.valueAt(shape.LastParameter)
                pts = {(round(a.x + 10, 4), round(a.y + 20, 4)), (round(b.x + 10, 4), round(b.y + 20, 4))}
                self.assertEqual({(round(start.x, 4), round(start.y, 4)), (round(end.x, 4), round(end.y, 4))}, pts)
            elif e.dxftype() == "ELLIPSE":
                ends = {(round(e.start_point.x, 3), round(e.start_point.y, 3)), (round(e.end_point.x, 3), round(e.end_point.y, 3))}
                a, b = shape.valueAt(shape.FirstParameter), shape.valueAt(shape.LastParameter)
                self.assertEqual(ends, {(round(a.x + 10, 3), round(a.y + 20, 3)), (round(b.x + 10, 3), round(b.y + 20, 3))})
            elif e.dxftype() == "SPLINE":
                pts = list(e.construction_tool().approximate(3000))
                for t in (0.0, 0.25, 0.5, 0.75, 1.0):
                    p = shape.valueAt(shape.FirstParameter + t * (shape.LastParameter - shape.FirstParameter))
                    dmin = min(((q.x - p.x - 10) ** 2 + (q.y - p.y - 20) ** 2) ** 0.5 for q in pts)
                    self.assertLess(dmin, 0.01)

    # -- ruling 79: cosmetic edges and centre lines --------------------------------------------------------------------

    def test_cosmetic_and_center_lines(self):
        cos = self.msp("COSMETIC")
        self.assertEqual(len(cos), 4)  # two cosmetic lines on each of V0 and V30
        by_type = {}
        for e in cos:
            by_type.setdefault((e.dxf.linetype, e.dxf.lineweight, e.dxf.get("true_color", None)), []).append(e)
        # default cosmetic: ISO 1 (continuous), weight 0.5, black -> layer colour
        self.assertIn(("CONTINUOUS", 50, None), {(k[0].upper(), k[1], k[2]) for k in by_type})
        # the red one: ISO line 4 at weight 0.7 gets its own LTYPE, true colour red, lineweight 70
        red = [e for e in cos if e.dxf.get("true_color", None) == 0xFF0000]
        self.assertEqual(len(red), 2)
        for e in red:
            self.assertEqual(e.dxf.lineweight, 70)
            name = e.dxf.linetype
            want = expected_pattern("ISO", 4, 0.7)
            got = self.pattern(self.dxf, name)
            self.assertEqual(len(got), len(want))
            for a, b in zip(got, want):
                self.assertTrue(close(a, b))
            self.assertNotIn("ltscale", e.dxf.all_existing_dxf_attribs())  # never an entity line-type scale
        cen = self.msp("CENTER")
        self.assertEqual(len(cen), 2)
        for e in cen:
            self.assertEqual(e.dxf.lineweight, 50)  # Format.weight of the centre line

    def test_cosmetic_edges_are_placed_with_the_view_transform(self):
        for view in (self.v0, self.v30):
            ox, oy = self.off(view)
            for ce in view.CosmeticEdges:
                sx, sy, ex, ey = ce.Start.x * 0.5, ce.Start.y * 0.5, ce.End.x * 0.5, ce.End.y * 0.5
                r = -0.0 + view.Rotation.Value if hasattr(view.Rotation, "Value") else view.Rotation
                import math

                c, s = math.cos(math.radians(r)), math.sin(math.radians(r))
                a = (round(ox + c * sx - s * sy, 3), round(oy + s * sx + c * sy, 3))
                b = (round(ox + c * ex - s * ey, 3), round(oy + s * ex + c * ey, 3))
                found = [e for e in self.msp("COSMETIC", "LINE") if {(round(e.dxf.start.x, 3), round(e.dxf.start.y, 3)), (round(e.dxf.end.x, 3), round(e.dxf.end.y, 3))} == {a, b}]
                self.assertEqual(len(found), 1, (view.Name, a, b))

    # -- ruling 80: dimensions -----------------------------------------------------------------------------------------

    def dimension_of(self, name):
        d = self.dims[name]
        want = d.getText()
        out = [e for e in self.msp("DIMENSIONS", "DIMENSION") if e.dxf.text.startswith(want) or e.dxf.text.startswith(want.split(" ")[0])]
        return d, out

    def test_seven_types_are_dimension_entities_of_the_matching_type(self):
        want = {"Distance": 1, "DistanceX": 0, "DistanceY": 0, "Radius": 4, "Diameter": 3, "Angle": 2, "Angle3Pt": 5}
        dims = self.msp("DIMENSIONS", "DIMENSION")
        by_text = {}
        for e in dims:
            by_text.setdefault(e.dxf.text, []).append(e)
        for name, t in want.items():
            d = self.dims[name]
            hits = [e for e in by_text.get(d.getText(), []) if e.dxf.dimtype & 7 == t]
            self.assertTrue(hits, (name, d.getText(), sorted(by_text)))
            for e in hits:
                self.assertIsNotNone(e.dxf.get("geometry"))
                self.assertIn(e.dxf.geometry, self.dxf.blocks)
                self.assertGreater(len(self.dxf.blocks.get(e.dxf.geometry)), 0)
        self.assertEqual(len(dims), len(self.dims))

    def test_dimension_definition_points_in_page_mm(self):
        ox, oy = self.off(self.v0)
        d = self.dims["Distance"]
        p = d.getLinearPoints()
        a, b = (round(p[0].x + ox, 2), round(-p[0].y + oy, 2)), (round(p[1].x + ox, 2), round(-p[1].y + oy, 2))
        got = [(round(e.dxf.defpoint2.x, 2), round(e.dxf.defpoint2.y, 2), round(e.dxf.defpoint3.x, 2), round(e.dxf.defpoint3.y, 2)) for e in self.msp("DIMENSIONS", "DIMENSION") if e.dxf.dimtype & 7 == 1]
        self.assertEqual(got, [a + b])
        # radius and diameter: the centre of the arc in page mm
        ap = self.dims["Diameter"].getArcPoints()
        c = (round(ap[0].x + ox, 2), round(-ap[0].y + oy, 2))
        dia = [e for e in self.msp("DIMENSIONS", "DIMENSION") if e.dxf.dimtype & 7 == 3][0]
        mid = ((dia.dxf.defpoint.x + dia.dxf.defpoint4.x) / 2, (dia.dxf.defpoint.y + dia.dxf.defpoint4.y) / 2)
        self.assertEqual((round(mid[0], 2), round(mid[1], 2)), c)
        # angle: apex and both leg ends
        an = self.dims["Angle"].getAnglePoints()
        legs = {(round(q.x + ox, 2), round(-q.y + oy, 2)) for q in an}
        ang = [e for e in self.msp("DIMENSIONS", "DIMENSION") if e.dxf.dimtype & 7 == 2][0]
        got = {(round(ang.dxf.get(k).x, 2), round(ang.dxf.get(k).y, 2)) for k in ("defpoint", "defpoint2", "defpoint3", "defpoint4", "defpoint5")}
        self.assertTrue(legs <= got, (legs, got))
        # the text sits at the dimension's own position on the page
        tx, ty = self.dims["Distance"].X.Value + ox, self.dims["Distance"].Y.Value + oy
        e = [e for e in self.msp("DIMENSIONS", "DIMENSION") if e.dxf.dimtype & 7 == 1][0]
        self.assertLess(abs(e.dxf.text_midpoint.y - ty), 6.0)

    def test_one_sided_tolerance_zero_side_has_no_sign(self):
        d = self.dims["Unequal"]
        try:
            for over, under, want in ((0.3, 0.0, "\\S+0.3^ 0;"), (0.0, -0.25, "\\S 0^-0.25;")):
                d.OverTolerance, d.UnderTolerance = over, under
                self.doc.recompute()
                self.assertEqual(DX.dimension_text(d)[0], d.getText() + want)
        finally:
            d.OverTolerance, d.UnderTolerance = 0.2, -0.1
            self.doc.recompute()

    def test_text_is_get_text_and_tolerances_follow_the_gui(self):
        texts = [e.dxf.text for e in self.msp("DIMENSIONS", "DIMENSION")]
        for n in ("Distance", "Radius", "Diameter", "Angle", "Angle3Pt", "Arbitrary", "Equal", "Exact"):
            self.assertIn(self.dims[n].getText(), texts, n)
        self.assertEqual(self.dims["Arbitrary"].getText(), "ARB 25 H7")
        self.assertIn("±", self.dims["Equal"].getText())  # equal tolerance already in getText()
        self.assertEqual(self.dims["Unequal"].getText() + "\\S+0.2^-0.1;", [t for t in texts if "\\S" in t][0])
        self.assertEqual(len([t for t in texts if "\\S" in t]), 1)
        self.assertEqual(DX._tolerance_value("%+.2w", 0.2), "+0.2")
        self.assertEqual(DX._tolerance_value("%+.2w", -0.1), "-0.1")
        self.assertEqual(DX._tolerance_value("%+.3f", 0.25), "+0.250")
        self.assertEqual(DX._tolerance_value("pre %.1r post", 0.26), "pre 0.3 post")

    def test_exact_dimension_is_written_and_its_frame_named(self):
        names = [s["name"] for s in self.report["skipped"]]
        self.assertIn("DimExact", names)
        self.assertTrue(any("DimExact" == s["name"] and "frame" in s["reason"] for s in self.report["skipped"]))

    def test_other_dimension_types_are_named_not_written(self):
        d = self.doc.addObject("TechDraw::DrawViewDimension", "DimArea")
        self.page.addView(d)
        d.Type = "Area"
        d.References2D = [(self.v0, "Face0")]
        self.doc.recompute()
        rep = DX.write(self.page, os.path.join(self.tmp, "area.dxf"), "ISO")
        self.assertIn("DimArea", [s["name"] for s in rep["skipped"]])
        self.page.removeView(d)
        self.doc.removeObject("DimArea")

    # -- ruling 81: text -----------------------------------------------------------------------------------------------

    def mtexts(self):
        return self.msp("TEXT", "MTEXT")

    def test_annotation_all_lines_one_mtext(self):
        mt = [m for m in self.mtexts() if "MATERIAL" in m.text][0]
        self.assertEqual(mt.plain_text().split("\n"), ["MATERIAL: S235 ⌀ 16 ± 0.1", "SCALE 1:2 90° m²"])
        self.assertIn("\\P", mt.text)  # lines joined by the MTEXT paragraph code
        self.assertEqual(mt.dxf.attachment_point, 5)  # middle centre
        self.assertTrue(close(mt.dxf.insert.x, 60.0) and close(mt.dxf.insert.y, 30.0))
        self.assertTrue(close(mt.dxf.char_height, 5.0))
        self.assertTrue(close(mt.dxf.rotation, 30.0))
        self.assertIn("\\pxql;", mt.text)  # left-aligned paragraphs

    def test_annotation_second_line_and_escapes(self):
        self.assertEqual(DX.mtext_escape("a{b}\\c"), "a\\{b\\}\\\\c")
        self.assertEqual(DX.annotation_lines(self.anno), list(self.anno.Text))

    def test_rich_annotation_body_rules(self):
        mt = [m for m in self.mtexts() if "NOTE" in m.text][0]
        self.assertEqual(mt.text, "\\pxql;NOTE: deburr all edges\\P± 0.1 mm\\Psecond <para> & Ø")
        self.assertEqual(mt.dxf.attachment_point, 1)  # top left, OriginCentered is false
        self.assertTrue(close(mt.dxf.insert.x, 300.0) and close(mt.dxf.insert.y, 30.0))
        self.assertNotIn("p{margin", mt.text)  # head and style are dropped
        self.assertEqual(DX.rich_text_to_mtext("<p>a</p>"), "a")
        self.assertEqual(DX.rich_text_to_mtext("<body><p> a  \n b </p><p>c<br/>d</p></body>"), "a b\\Pc\\Pd")
        self.assertEqual(DX.rich_text_to_mtext("<p>x&nbsp;y z <b>bold</b> &lt;&amp;&gt;</p>"), "x y z bold <&>")

    def test_rich_annotation_qt_html_makes_no_empty_paragraphs(self):
        qt = (
            '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0//EN" "http://www.w3.org/TR/REC-html40/strict.dtd">\n<html><head>'
            '<meta name="qrichtext" content="1" /><meta charset="utf-8" /><style type="text/css">\np, li { white-space: pre-wrap; }\n'
            'hr { height: 1px; border-width: 0; }\n</style></head><body style=" font-family:\'.AppleSystemUIFont\'; font-size:13pt;">\n'
            '<p style="margin:0px;">First paragraph \u2300 16 \u00b1 0.1 \u00b0</p>\n'
            '<p style="margin:0px;">Second paragraph<br />after a break &lt;tag&gt; &amp; more</p>\n</body></html>'
        )
        self.assertEqual(DX.rich_text_to_mtext(qt), "First paragraph \u2300 16 \u00b1 0.1 \u00b0\\PSecond paragraph\\Pafter a break <tag> & more")
        self.assertEqual(DX.rich_text_to_mtext("hand written <b>bold</b> &amp; text"), "hand written bold & text")
        tbl = '<table border="1"><tr><td><span style="font-family:\'Y14.5-FreeCAD\';">&#184;</span></td><td><span>0.05</span></td></tr></table>'
        self.assertEqual(DX.rich_text_to_mtext(tbl), "\u00b8 0.05")

    def test_pmi_frames_are_named_and_the_text_still_written(self):
        ra = self.doc.addObject("TechDraw::DrawRichAnno", "PmiFrame")
        self.page.addView(ra)
        ra.addProperty("App::PropertyString", "PmiRole", "Pmi", "role")
        ra.PmiRole = "frame"
        ra.AnnoText = "<p>0.05</p>"
        ta = self.doc.addObject("TechDraw::DrawRichAnno", "PmiTable")
        self.page.addView(ta)
        ta.AnnoText = '<table border="1"><tr><td>A</td><td>0.05</td></tr></table>'
        self.doc.recompute()
        try:
            rep = DX.write(self.page, os.path.join(self.tmp, "pmi.dxf"), "ISO")
            for n in ("PmiFrame", "PmiTable"):
                self.assertIn((n, "PMI frame not drawn (its text is written)"), [(s["name"], s["reason"]) for s in rep["skipped"]])
        finally:
            self.page.removeView(ra)
            self.page.removeView(ta)
            self.doc.removeObject("PmiFrame")
            self.doc.removeObject("PmiTable")
            self.doc.recompute()

    def test_rich_annotation_centred_and_parent_offsets(self):
        self.rich.OriginCentered = True
        self.doc.recompute()
        try:
            rep = DX.write(self.page, os.path.join(self.tmp, "ctr.dxf"), "ISO")
            d = self.ezdxf.readfile(os.path.join(self.tmp, "ctr.dxf"))
            mt = [m for m in d.modelspace().query("MTEXT") if "NOTE" in m.text][0]
            self.assertEqual(mt.dxf.attachment_point, 5)
            self.assertTrue(rep["entities"]["MTEXT"] == 2)
            self.rich.OriginCentered = False
            self.rich.AnnoParent = self.v0
            self.rich.X = 20
            self.rich.Y = 10
            self.doc.recompute()
            DX.write(self.page, os.path.join(self.tmp, "par.dxf"), "ISO")
            d = self.ezdxf.readfile(os.path.join(self.tmp, "par.dxf"))
            mt = [m for m in d.modelspace().query("MTEXT") if "NOTE" in m.text][0]
            self.assertTrue(close(mt.dxf.insert.x, 120.0) and close(mt.dxf.insert.y, 160.0))
        finally:
            self.rich.AnnoParent = None
            self.rich.OriginCentered = False
            self.rich.X = 300
            self.rich.Y = 30
            self.doc.recompute()

    # -- ruling 82: never silent ---------------------------------------------------------------------------------------

    def test_skipped_objects_are_named_in_report_and_one_warning(self):
        names = {s["name"] for s in self.report["skipped"]}
        for n in ("Template", "Balloon", "Hatch", "Section", "Detail", "Note", "DimExact"):
            self.assertIn(n, names)
        self.assertTrue(any("smooth" in n for n in self.report["notes"]))
        warnings = []
        with mock.patch.object(DX, "_warn", warnings.append):
            DX.write(self.page, os.path.join(self.tmp, "w.dxf"), "ISO")
        self.assertEqual(len(warnings), 1)
        for n in ("Template", "Balloon", "Hatch", "Section", "Detail", "Note"):
            self.assertIn(n, warnings[0])

    def test_every_object_is_written_or_named(self):
        written_types = ("TechDraw::DrawViewPart", "TechDraw::DrawViewSection", "TechDraw::DrawViewDetail", "TechDraw::DrawProjGroupItem", "TechDraw::DrawProjGroup")
        names = {s["name"] for s in self.report["skipped"]}
        for o in self.doc.Objects:
            if o.TypeId.startswith("TechDraw::") and o.TypeId not in written_types and o.TypeId not in ("TechDraw::DrawPage",):
                if o.TypeId in ("TechDraw::DrawViewDimension", "TechDraw::DrawViewAnnotation", "TechDraw::DrawRichAnno"):
                    continue  # written (a dimension frame or annotation frame is named in addition)
                self.assertIn(o.Name, names, o.TypeId)

    def test_empty_page_writes_a_valid_file(self):
        doc = App.newDocument("DxfEmpty")
        try:
            page = createPageWithSVGTemplate(doc)
            rep = DX.write(page, os.path.join(self.tmp, "empty.dxf"), "ISO")
            d = self.ezdxf.readfile(os.path.join(self.tmp, "empty.dxf"))
            self.assertEqual(len(d.modelspace()), 0)
            self.assertEqual(len(d.audit().errors), 0)
            self.assertLess(abs(d.header["$EXTMAX"][0]), 1e6)
            self.assertEqual([s["name"] for s in rep["skipped"]], ["Template"])
        finally:
            App.closeDocument(doc.Name)

    # -- readers -------------------------------------------------------------------------------------------------------

    def test_audit_is_clean(self):
        a = self.dxf.audit()
        self.assertEqual(len(a.errors), 0)
        self.assertEqual(len(a.fixes), 0)

    def test_standard_argument_validated(self):
        with self.assertRaises(ValueError):
            DX.write(self.page, os.path.join(self.tmp, "bad.dxf"), "DIN")

    def test_default_standard_follows_the_preference(self):
        fake = mock.Mock()
        fake.GetInt = lambda key, default: 2 if key == "LineStandard" else default
        with mock.patch.object(DX, "_group", lambda name: fake if name == "Standards" else DX.App.ParamGet("User parameter:BaseApp/Preferences/Mod/TechDraw/" + name)):
            rep = DX.write(self.page, os.path.join(self.tmp, "asme.dxf"))
        self.assertEqual(rep["standard"], "ASME")
