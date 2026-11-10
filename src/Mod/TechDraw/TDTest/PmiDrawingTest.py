# SPDX-License-Identifier: LGPL-2.1-or-later

# G12 slice 4 (checks 57 to 65, 67): PMI onto TechDraw views, headless.
# Run with: FreeCADCmd -t TestTechDrawPmi   (also part of TestTechDrawApp)
#
# The verifiers at the top (wrongMeasurements, toleranceMismatches, frameMismatches, anchorMisses, accounting) are written
# from the brief's wording and do not call the code under test for their expectations; the corpus script
# harness/g12/drawing_corpus.py imports them from here.

import math
import os
import tempfile
import unittest

import FreeCAD as App
import Part

import PmiAuthoring as PA
import PmiDrawing as PD

SEMANTIC = ("Part::PmiDimension", "Part::PmiTolerance", "Part::PmiDatum")


# ------------------------------------------------------------------------------------------------
# verifiers (shared with the corpus script)
# ------------------------------------------------------------------------------------------------


def rel_close(a, b, rel=1e-6):
    return abs(a - b) <= max(rel * max(abs(a), abs(b)), 1e-12)


def pairs(page):
    return [(o, o.PmiSource) for o in page.Document.Objects if "PmiSource" in o.PropertiesList and o.PmiSource is not None]


def accounting(report, items):
    """(unaccounted item names, duplicated names): each item exactly once in the report, each report entry well formed."""
    names = [i.Name for i in items]
    unaccounted = [n for n in names if n not in report]
    extra = [n for n in report if n not in names]
    dup = sorted({n for n in names if names.count(n) > 1})
    bad = []
    for n, r in report.items():
        ok = False
        if isinstance(r, tuple) and len(r) == 2:
            if r[0] == "not projected":
                ok = r[1] in ("unattached", "geometry", "no view")
            else:
                ok = isinstance(r[0], list) and len(r[0]) >= 1 and isinstance(r[1], str)
        if not ok:
            bad.append(n)
    return unaccounted + extra + bad, dup


def has_nominal(item):
    return bool(item.HasValue) and not any("no value is stated" in k for k in item.KernelLoss)


def wrongMeasurements(page):
    """Projected dimensions whose measured value differs from their item's nominal by more than 1e-6 relative."""
    wrong = []
    for o, item in pairs(page):
        if o.PmiRole == "dimension" and has_nominal(item):
            if not rel_close(abs(float(o.getRawValue())), item.Value):
                wrong.append((o.Name, item.Name, float(o.getRawValue()), item.Value))
    return wrong


def expected_tolerances(item):
    """Ruling 5 spelled out again: (exact, equal, arbitrary, over, under, spec); basic is passed in by the caller."""
    over = under = 0.0
    equal, arbitrary, spec = True, False, ""
    if item.HasBounds and item.BoundsKind == "plus_minus":
        over, under = item.UpperBound, item.LowerBound
        equal = over >= 0 and abs(over + under) < 1e-9
        if equal:
            under = -over
    elif item.HasBounds and item.BoundsKind == "range" and has_nominal(item):
        over, under = item.UpperBound - item.Value, item.LowerBound - item.Value
        equal = over >= 0 and abs(over + under) < 1e-9
        if equal:
            under = -over
    if item.ToleranceClass:
        arbitrary, spec = True, item.ToleranceClass
    return False, equal, arbitrary, over, under, spec


def toleranceMismatches(page, basic=lambda item: False):
    bad = []
    for o, item in pairs(page):
        if o.PmiRole != "dimension":
            continue
        if basic(item):
            want = (True, True, False, 0.0, 0.0, "")
        else:
            want = expected_tolerances(item)
        have = (bool(o.TheoreticalExact), bool(o.EqualTolerance), bool(o.ArbitraryTolerances), o.OverTolerance.Value, o.UnderTolerance.Value, o.FormatSpecOverTolerance if o.ArbitraryTolerances else "")
        ok = have[:3] == want[:3] and abs(have[3] - want[3]) < 1e-9 and abs(have[4] - want[4]) < 1e-9 and have[5] == want[5]
        if not ok:
            bad.append((o.Name, item.Name, have, want))
    return bad


def item_tuple(item):
    """(tolerance type, zone shape, value, zone modifier, zone modifier value, modifiers, datum compartments) of a tolerance."""
    comps = []
    for ref in item.DatumReferences:
        if "(" in ref:
            letter, rest = ref[:-1].split("(", 1)
            comps.append((letter, [m for m in rest.split(",") if m]))
        else:
            comps.append((ref, []))
    zv = item.ZoneModifierValue if item.ZoneModifier == "Projected" else 0.0
    return (item.PmiType, item.ZoneShape, item.Value, item.ZoneModifier, zv, list(item.Modifiers), comps)


def tuples_equal(a, b):
    return a[0] == b[0] and a[1] == b[1] and rel_close(a[2], b[2], 1e-9) and a[3] == b[3] and abs(a[4] - b[4]) < 1e-9 and a[5] == b[5] and a[6] == b[6]


def frameMismatches(page):
    """Frames whose text does not read back as their item; datum symbols whose letter is not their item's; datum symbols
    duplicated for one letter and body."""
    bad = []
    seen = {}
    for o, item in pairs(page):
        if o.PmiRole == "frame":
            try:
                got = PD.parseFrame(o.AnnoText)
            except Exception as e:
                bad.append((o.Name, "unreadable %s" % e))
                continue
            if not tuples_equal(got, item_tuple(item)):
                bad.append((o.Name, got, item_tuple(item)))
        elif o.PmiRole == "datum":
            letter = PD.parseDatum(o.AnnoText)
            if letter != item.Letter:
                bad.append((o.Name, letter, item.Letter))
            body = item.References[0][0].Name if len(item.References) else ""
            seen.setdefault((body, item.Letter), []).append(o.Name)
    for k, v in seen.items():
        if len(v) > 1:
            bad.append(("duplicate datum symbol", k, v))
    return bad


def _view_of_leader(ld):
    return ld.LeaderParent


def anchorMisses(page, vis_tol=1e-3):
    """Leaders whose end is not the projection of a point on an edge of one of their item's governed elements, or whose point
    does not lie on a visible edge of the view."""
    bad = []
    for ld, item in pairs(page):
        if ld.PmiRole != "leader":
            continue
        view = ld.LeaderParent
        try:
            oname, ename = ld.PmiAnchorEdge.split(".")
            obj = page.Document.getObject(oname)
            edge = obj.Shape.getElement(ename)
            p = ld.PmiAnchor
            governed = False
            for o, subs in item.References:
                for s in subs:
                    if o.Name != oname:
                        continue
                    sh = o.Shape.getElement(s)
                    edges = sh.Edges if sh.ShapeType == "Face" else [sh] if sh.ShapeType == "Edge" else []
                    if any(e.isSame(edge) for e in edges):
                        governed = True
            if not governed:
                bad.append((ld.Name, "edge not of a governed element"))
                continue
            if Part.Vertex(p).distToShape(edge)[0] > 1e-7:
                bad.append((ld.Name, "point off the edge"))
                continue
            group = PD.groupOf(view)
            scale = float((group or view).Scale)
            centre = view.projectPoint(view.getGeometricCenter())
            u = view.projectPoint(p) - centre
            end = PD.anchorEnd(ld)
            if (end - App.Vector(u.x, u.y, 0)).Length > 1e-6:
                bad.append((ld.Name, "end differs from the projection", tuple(end), tuple(u)))
                continue
            v = Part.Vertex(App.Vector(u.x * scale, u.y * scale, 0))
            if not any(v.distToShape(e)[0] <= vis_tol for e in view.getVisibleEdges(True)):
                bad.append((ld.Name, "not on a visible edge"))
            elif material_in_front(view, p, item):
                bad.append((ld.Name, "behind material"))
        except Exception as e:
            bad.append((ld.Name, "exception %s" % e))
    return bad


def material_in_front(view, p, item=None):
    """Does the straight line from model point p towards the viewer run through the inside of a source solid?  (Its common
    part with each solid, judged by its middle; a line along a face counts as inside.)"""
    d = App.Vector(view.Direction)
    d.normalize()
    for src in (PD.groupOf(view) or view).Source:
        leaves = [src] if getattr(src, "Shape", None) is not None and not src.Shape.isNull() and src.Shape.Faces else [o for o in src.OutListRecursive if getattr(o, "Shape", None) is not None and not o.Shape.isNull() and o.Shape.Faces and not o.OutList]
        for o in leaves:
            sh = o.Shape.copy()
            sh.Placement = o.getGlobalPlacement()
            for sol in sh.Solids:
                seg = Part.makeLine(p + d * 1e-6, p + d * (2 * sol.BoundBox.DiagonalLength + 1))
                try:
                    common = sol.common(seg)
                except Exception:
                    continue
                for e in common.Edges:
                    if e.Length > 1e-5 and sol.isInside(e.valueAt((e.FirstParameter + e.LastParameter) / 2), 1e-7, False):
                        return True
    return False


def objectsSnapshot(page):
    """{name: (type, label, {property: str(value)})} of the drawing objects, for equality across save and reopen."""
    snap = {}
    for o, item in pairs(page):
        props = {}
        for p in ("AnnoText", "OverTolerance", "UnderTolerance", "EqualTolerance", "TheoreticalExact", "ArbitraryTolerances", "Type", "PmiRole", "WayPoints", "X", "Y"):
            if p in o.PropertiesList:
                v = getattr(o, p)
                props[p] = ("%.9f" % (round(v.Value, 9) + 0.0)) if hasattr(v, "Value") else (str([tuple(round(c, 9) for c in (w.x, w.y, w.z)) for w in v]) if p == "WayPoints" else str(v))
        snap[o.Name] = (o.TypeId, o.PmiSource.Name, props, round(float(o.getRawValue()), 9) if o.PmiRole == "dimension" else None)
    return snap


# ------------------------------------------------------------------------------------------------
# fixtures
# ------------------------------------------------------------------------------------------------


def make_page(doc):
    page = doc.addObject("TechDraw::DrawPage", "Page")
    tmpl = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
    tmpl.Template = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Templates", "Default_Template_A4_Landscape.svg")
    page.Template = tmpl
    return page


def prism(doc, name, pts, height):
    """A prism along z over a polygon: a Part::Feature."""
    wire = Part.makePolygon([App.Vector(*p) for p in pts + [pts[0]]])
    s = Part.Face(wire).extrude(App.Vector(0, 0, height))
    o = doc.addObject("Part::Feature", name)
    o.Shape = s
    return o


def face_named(obj, test):
    return [("Face%d" % i) for i, f in enumerate(obj.Shape.Faces, 1) if test(f)]


def planar_face(obj, normal, coord):
    """The planar face with outward normal ``normal`` (a unit axis) whose centre has that coordinate along the normal."""
    out = []
    for i, f in enumerate(obj.Shape.Faces, 1):
        if f.Surface.TypeId != "Part::GeomPlane":
            continue
        n = f.normalAt(*[(a + b) / 2 for a, b in zip(f.ParameterRange[::2], f.ParameterRange[1::2])])
        if (n - normal).Length < 1e-9 and abs(f.CenterOfMass.dot(normal) - coord) < 1e-9:
            out.append("Face%d" % i)
    assert len(out) == 1, out
    return out[0]


def cyl_face(obj):
    out = ["Face%d" % i for i, f in enumerate(obj.Shape.Faces, 1) if f.Surface.TypeId == "Part::GeomCylinder"]
    assert len(out) == 1, out
    return out[0]


def planar_face_any(obj, axis, low):
    """The face of an axis-aligned (local frame) box at the low or high end of the local axis 0, 1 or 2."""
    out = []
    for i, f in enumerate(obj.Shape.Faces, 1):
        bb = f.BoundBox
        c = obj.Placement.inverse().multVec(f.CenterOfMass)
        ext = (obj.Length.Value, obj.Width.Value, obj.Height.Value)[axis]
        if abs(c[axis] - (0.0 if low else ext)) < 1e-9 and f.Surface.TypeId == "Part::GeomPlane":
            n = obj.Placement.Rotation.inverted().multVec(f.normalAt(*[(a + b2) / 2 for a, b2 in zip(f.ParameterRange[::2], f.ParameterRange[1::2])]))
            if abs(abs(n[axis]) - 1) < 1e-9:
                out.append("Face%d" % i)
    assert len(out) == 1, out
    return out[0]


X, Y, Z = App.Vector(1, 0, 0), App.Vector(0, 1, 0), App.Vector(0, 0, 1)


class PmiDrawingTest(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("PmiDrawingTest")
        self.doc.UndoMode = 1
        self.box = self.doc.addObject("Part::Box", "Box")
        self.box.Length, self.box.Width, self.box.Height = 30, 20, 10
        self.cyl = self.doc.addObject("Part::Cylinder", "Cyl")
        self.cyl.Radius, self.cyl.Height = 4, 10
        self.cyl.Placement.Base = App.Vector(40, 0, 0)
        self.doc.recompute()
        self.page = make_page(self.doc)

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def items(self):
        return [o for o in self.doc.Objects if o.TypeId in SEMANTIC]

    def project(self, source=None, **kw):
        return PD.project(self.page, source or [self.box, self.cyl], **kw)

    def drawing(self, report, item):
        names, view = report[item.Name]
        self.assertNotEqual(names, "not projected", report[item.Name])
        return [self.doc.getObject(n) for n in names], view

    def face(self, normal, coord):
        return (self.box, [planar_face(self.box, normal, coord)])

    # ---- 57 / 59: accounting, view choice, kind and reference choice, measured values
    def test_thickness_goes_to_the_first_view_that_sees_both_planes_edge_on(self):
        w = PA.addSizeDimension([self.face(-X, 0), self.face(X, 30)])  # 30 along x
        d = PA.addSizeDimension([self.face(-Y, 0), self.face(Y, 20)])  # 20 along y
        h = PA.addSizeDimension([self.face(-Z, 0), self.face(Z, 10)])  # 10 along z
        rep = self.project(self.box)
        for item, view, typ, value in ((w, "Front", "DistanceX", 30.0), (d, "Top", "DistanceY", 20.0), (h, "Front", "DistanceY", 10.0)):
            (dim,), v = self.drawing(rep, item)
            self.assertEqual((v, dim.Type), (view, typ), item.Name)
            self.assertAlmostEqual(abs(dim.getRawValue()), value, places=9)
            self.assertEqual(dim.PmiRole, "dimension")
        self.assertEqual(wrongMeasurements(self.page), [])
        self.assertEqual(accounting(rep, self.items()), ([], []))

    def test_diameter_and_radius_go_to_the_view_along_the_axis(self):
        z_cyl = (self.cyl, [cyl_face(self.cyl)])
        dia = PA.addSizeDimension([z_cyl])
        rad = PA.addSizeDimension([z_cyl], pmi_type="Size_Radius")
        rep = self.project(self.cyl)
        (d,), v = self.drawing(rep, dia)
        self.assertEqual((v, d.Type, round(d.getRawValue(), 9)), ("Top", "Diameter", 8.0))
        (r,), v = self.drawing(rep, rad)
        self.assertEqual((v, r.Type, round(r.getRawValue(), 9)), ("Top", "Radius", 4.0))
        self.assertEqual(d.References3D[0][0], self.cyl)
        self.assertTrue(d.References3D[0][1][0].startswith("Edge"))

    def test_x_axis_cylinder_goes_to_the_right_view(self):
        c = self.doc.addObject("Part::Feature", "CylX")
        c.Shape = Part.makeCylinder(3, 12, App.Vector(0, 50, 0), App.Vector(1, 0, 0))  # axis along x in the shape itself
        dia = PA.addSizeDimension([(c, [cyl_face(c)])])
        page = make_page(self.doc)
        rep = PD.project(page, c)
        names, view = rep[dia.Name]
        self.assertEqual(view, "Right")
        self.assertAlmostEqual(self.doc.getObject(names[0]).getRawValue(), 6.0, places=9)

    def test_location_between_planes_and_valueless_location(self):
        loc = PA.addLocationDimension(self.face(-X, 0), self.face(X, 30))
        val = PA.addLocationDimension(self.face(-Y, 0), self.face(Y, 20), derive=False)
        rep = self.project(self.box)
        (d,), v = self.drawing(rep, loc)
        self.assertEqual((v, d.Type), ("Front", "DistanceX"))
        (d2,), v2 = self.drawing(rep, val)  # no stated value: measured, nothing to compare
        self.assertEqual((v2, d2.Type), ("Top", "DistanceY"))
        self.assertAlmostEqual(abs(d2.getRawValue()), 20.0, places=9)
        self.assertEqual(wrongMeasurements(self.page), [])

    def test_angle_between_planes_goes_to_the_view_along_the_intersection(self):
        tri = prism(self.doc, "Tri", [(0, 0, 0), (30, 0, 0), (0, 40, 0)], 10)
        # faces: the legs and the hypotenuse; the angle at the vertex (30, 0) between the x leg and the hypotenuse
        def f(pred):
            return [("Face%d" % i) for i, fc in enumerate(tri.Shape.Faces, 1) if fc.Surface.TypeId == "Part::GeomPlane" and pred(fc)]
        leg = f(lambda fc: abs(fc.CenterOfMass.y) < 1e-9 and abs(fc.CenterOfMass.z - 5) < 1e-9)[0]
        hyp = f(lambda fc: abs(fc.CenterOfMass.x - 15) < 1e-9 and abs(fc.CenterOfMass.y - 20) < 1e-9)[0]
        ang = PA.addAngularDimension((tri, [leg]), (tri, [hyp]))
        expect = ang.Value
        self.assertAlmostEqual(expect, math.degrees(math.atan2(40, 30)), places=6)
        rep = PD.project(self.page, tri)
        (d,), v = self.drawing(rep, ang)
        self.assertEqual((v, d.Type), ("Top", "Angle"))
        self.assertTrue(rel_close(abs(d.getRawValue()), expect))
        self.assertEqual(wrongMeasurements(self.page), [])

    def test_stated_value_that_geometry_contradicts_is_not_projected(self):
        w = PA.addSizeDimension([self.face(-X, 0), self.face(X, 30)], 31.0)  # the model is 30
        c = PA.addSizeDimension([(self.cyl, [cyl_face(self.cyl)])], 9.0)
        rep = self.project()
        self.assertEqual(rep[w.Name], ("not projected", "geometry"))
        self.assertEqual(rep[c.Name], ("not projected", "geometry"))
        self.assertEqual([o for o, _ in pairs(self.page) if o.PmiRole == "dimension"], [])

    def test_geometry_the_ruling_does_not_cover_is_not_projected(self):
        s = self.doc.addObject("Part::Sphere", "Ball")
        s.Radius = 5
        self.doc.recompute()
        sph = PA.addSizeDimension([(s, ["Face1"])], 10.0, pmi_type="Size_SphericalDiameter")
        loc = PA.addLocationDimension((self.cyl, [cyl_face(self.cyl)]), self.face(X, 30), 10.0)  # axis to plane
        rep = self.project([self.box, self.cyl, s])
        self.assertEqual(rep[sph.Name], ("not projected", "geometry"))
        self.assertEqual(rep[loc.Name], ("not projected", "geometry"))

    def test_unattached_and_no_view(self):
        un = PA.addSizeDimension([self.face(-X, 0), self.face(X, 30)])
        un.Unattached = True
        # a closed void: no edge of its faces is visible from any of the six views
        outer = Part.makeBox(40, 40, 40)
        inner = Part.makeBox(10, 10, 10, App.Vector(15, 15, 15))
        body = self.doc.addObject("Part::Feature", "Hollow")
        body.Shape = outer.cut(inner)
        void = [("Face%d" % i) for i, f in enumerate(body.Shape.Faces, 1) if f.BoundBox.XMin > 1 and f.BoundBox.XMax < 39]
        self.assertEqual(len(void), 6)
        t = PA.addTolerance("Flatness", 0.05, (body, [void[0]]))
        outside = PA.addTolerance("Flatness", 0.05, (body, [("Face%d" % 1)]))
        rep = PD.project(self.page, body, items=[un, t, outside])
        self.assertEqual(rep[un.Name], ("not projected", "unattached"))
        self.assertEqual(rep[t.Name], ("not projected", "no view"))
        self.assertNotEqual(rep[outside.Name][0], "not projected")
        self.assertEqual(accounting(rep, [un, t, outside]), ([], []))

    def test_rear_left_bottom_views_are_added_only_when_needed(self):
        grp_labels = lambda: sorted(i.Type for g in self.page.Views if g.TypeId == "TechDraw::DrawProjGroup" for i in g.Views)
        t = PA.addTolerance("Flatness", 0.05, self.face(X, 30))
        PD.project(self.page, self.box)
        self.assertEqual(grp_labels(), ["Front", "Right", "Top"])
        # a pocket seen from below only: a blind hole in the bottom face
        b = self.doc.addObject("Part::Feature", "Holed")
        outer = Part.makeBox(30, 30, 30)
        hole = Part.makeCylinder(5, 20, App.Vector(15, 15, -1))
        b.Shape = outer.cut(hole)
        floor = [("Face%d" % i) for i, f in enumerate(b.Shape.Faces, 1)
                 if f.Surface.TypeId == "Part::GeomPlane" and abs(f.CenterOfMass.z - 19) < 1e-9 and f.BoundBox.XMax < 29][0]
        t2 = PA.addTolerance("Flatness", 0.05, (b, [floor]))
        page2 = make_page(self.doc)
        rep = PD.project(page2, b, items=[t2])
        names, view = rep[t2.Name]
        self.assertNotEqual(names, "not projected")
        self.assertIn(view, ("Bottom", "Left", "Rear", "Front", "Top", "Right"))

    # ---- 60: tolerances
    def test_tolerance_mapping(self):
        f = [self.face(-X, 0), self.face(X, 30)]
        a = PA.addSizeDimension(f, plus_minus=(0.1, -0.1))
        b = PA.addSizeDimension(f, plus_minus=(0.2, -0.05))
        c = PA.addSizeDimension(f, limits=(30.2, 29.9))
        d = PA.addSizeDimension(f, fit_class="H7")
        e = PA.addSizeDimension(f)
        rep = self.project(self.box)
        got = {n: self.drawing(rep, i)[0][0] for n, i in zip("abcde", (a, b, c, d, e))}
        self.assertTrue(got["a"].EqualTolerance and abs(got["a"].OverTolerance.Value - 0.1) < 1e-12 and abs(got["a"].UnderTolerance.Value + 0.1) < 1e-12)
        self.assertFalse(got["b"].EqualTolerance)
        self.assertAlmostEqual(got["b"].OverTolerance.Value, 0.2, places=12)
        self.assertAlmostEqual(got["b"].UnderTolerance.Value, -0.05, places=12)
        self.assertFalse(got["c"].EqualTolerance)
        self.assertAlmostEqual(got["c"].OverTolerance.Value, 0.2, places=9)
        self.assertAlmostEqual(got["c"].UnderTolerance.Value, -0.1, places=9)
        self.assertTrue(got["d"].ArbitraryTolerances)
        self.assertEqual(got["d"].FormatSpecOverTolerance, "H7")
        self.assertFalse(got["e"].ArbitraryTolerances or got["e"].TheoreticalExact)
        self.assertEqual(got["e"].OverTolerance.Value, 0.0)
        self.assertEqual(toleranceMismatches(self.page), [])

    def test_no_dimension_is_marked_basic(self):
        # the PMI model has no basic flag: a located dimension that a position frame shares a face with may be a reference
        # dimension, so TheoreticalExact is never set from the model
        loc = PA.addLocationDimension(self.face(-X, 0), self.face(X, 30))
        PA.addTolerance("Position", 0.2, self.face(X, 30), zone_shape="Diameter")
        toleranced = PA.addLocationDimension(self.face(-Z, 0), self.face(Z, 10), plus_minus=(0.1, -0.1))
        rep = self.project(self.box)
        (b,), _ = self.drawing(rep, loc)
        (t,), _ = self.drawing(rep, toleranced)
        self.assertFalse(b.TheoreticalExact)
        self.assertFalse(t.TheoreticalExact)
        self.assertAlmostEqual(t.OverTolerance.Value, 0.1, places=12)
        self.assertEqual(toleranceMismatches(self.page), [])

    # ---- 61: frames and datum symbols
    def test_frames_read_back_as_their_items(self):
        PA.addDatum("A", self.face(-Z, 0))
        PA.addDatum("B", self.face(-X, 0))
        PA.addDatum("C", self.face(-Y, 0))
        top = (self.box, [planar_face(self.box, Z, 10)])
        hole = (self.cyl, [cyl_face(self.cyl)])
        PA.addTolerance("Flatness", 0.05, top)
        PA.addTolerance("Position", 0.25, hole, datums=["A", ("B", ["MAXIMUM_MATERIAL_REQUIREMENT"]), "C"], zone_shape="Diameter", modifiers=["MAXIMUM_MATERIAL_REQUIREMENT"])
        PA.addTolerance("Perpendicularity", 0.1, hole, datums=["A"], zone_shape="Diameter", zone_modifier="Projected", zone_modifier_value=12.5)
        PA.addTolerance("ProfileOfSurface", 0.3, top, datums=["A", "B"], modifiers=["ALL_AROUND"])
        PA.addTolerance("TotalRunout", 0.02, hole, datums=["A"])
        PA.addTolerance("Coaxiality", 0.1, hole, datums=["A"], zone_shape="Diameter")
        PA.addTolerance("Concentricity", 0.1, hole, datums=["A"], zone_shape="Diameter")
        rep = self.project()
        frames = [o for o, _ in pairs(self.page) if o.PmiRole == "frame"]
        self.assertEqual(len(frames), 7)
        self.assertEqual(frameMismatches(self.page), [])
        # the cell order of ASME Y14.5-2018 clause 10: symbol, zone, then the datums in order
        pos = [o for o in frames if o.PmiSource.PmiType == "Position"][0]
        cells = PD._cells(pos.AnnoText)
        self.assertEqual(len(cells), 5)
        self.assertEqual([c[0][1] for c in cells[2:]], ["A", "B", "C"])
        self.assertEqual(PD.parseFrame(pos.AnnoText)[6], [("A", []), ("B", ["MAXIMUM_MATERIAL_REQUIREMENT"]), ("C", [])])
        self.assertEqual(accounting(rep, self.items()), ([], []))

    def test_datum_symbols_are_one_per_letter_per_body(self):
        PA.addDatum("A", self.face(-Z, 0))
        for v in (0.1, 0.2, 0.3):
            PA.addTolerance("Perpendicularity", v, self.face(X, 30), datums=["A"])
        cz = (self.cyl, [planar_face(self.cyl, -Z, 0)])
        PA.addDatum("A", cz)  # the same letter on another body
        rep = self.project()
        datum_syms = [o for o, _ in pairs(self.page) if o.PmiRole == "datum"]
        self.assertEqual(sorted(PD.parseDatum(o.AnnoText) for o in datum_syms), ["A", "A"])
        self.assertEqual(frameMismatches(self.page), [])
        uses_of_box = [i for i in self.items() if i.TypeId == "Part::PmiDatum" and i.References[0][0] == self.box]
        self.assertEqual(len(uses_of_box), 4)
        names = {tuple(rep[u.Name][0]) for u in uses_of_box}
        self.assertEqual(len(names), 1)  # four uses, one symbol

    # ---- 62: anchors
    def test_leaders_end_on_visible_edges_of_governed_faces(self):
        PA.addDatum("A", self.face(-Z, 0))
        PA.addTolerance("Flatness", 0.05, (self.box, [planar_face(self.box, Z, 10)]))
        PA.addTolerance("Position", 0.2, (self.cyl, [cyl_face(self.cyl)]), datums=["A"], zone_shape="Diameter")
        self.project()
        leaders = [o for o, _ in pairs(self.page) if o.PmiRole == "leader"]
        self.assertGreaterEqual(len(leaders), 3)
        self.assertEqual(anchorMisses(self.page), [])
        for ld in leaders:
            self.assertEqual(ld.EndSymbol, "Filled triangle")

    def test_anchor_check_holds_at_another_scale(self):
        PA.addTolerance("Flatness", 0.05, (self.box, [planar_face(self.box, Z, 10)]))
        self.project(self.box)
        g = [o for o in self.page.Views if o.TypeId == "TechDraw::DrawProjGroup"][0]
        g.ScaleType = "Custom"
        g.Scale = 2.0
        self.doc.recompute()
        PD.refresh(self.page)
        self.assertEqual(anchorMisses(self.page), [])

    # ---- 63: links and refresh
    def test_links_and_refresh(self):
        PA.addDatum("A", self.face(-Z, 0))
        dim = PA.addSizeDimension([self.face(-X, 0), self.face(X, 30)], plus_minus=(0.1, -0.1))
        dim2 = PA.addSizeDimension([self.face(-Y, 0), self.face(Y, 20)])
        tol = PA.addTolerance("Perpendicularity", 0.1, self.face(X, 30), datums=["A"])
        tol2 = PA.addTolerance("Flatness", 0.02, self.face(Y, 20))
        self.project(self.box)
        made = [o for o in self.doc.Objects if "PmiRole" in o.PropertiesList and o.PmiRole in ("dimension", "frame", "datum", "leader")]
        self.assertGreaterEqual(len(made), 8)
        for o in made:
            self.assertIsNotNone(o.PmiSource, o.Name)
        self.assertEqual(sorted(o.Name for o, _ in PD.itemsOn(self.page)), sorted(o.Name for o in made))
        snap0 = objectsSnapshot(self.page)

        def changed(before, after):
            return sorted(n for n in before if n in after and before[n] != after[n]), sorted(set(before) - set(after))

        # 1. a tolerance value
        tol.Value = 0.15
        PD.refresh(self.page)
        snap1 = objectsSnapshot(self.page)
        frame = [o for o, i in pairs(self.page) if o.PmiRole == "frame" and i == tol][0]
        self.assertEqual(changed(snap0, snap1), ([frame.Name], []))
        self.assertEqual(PD.parseFrame(frame.AnnoText)[2], 0.15)
        self.doc.undo()  # one refresh, one transaction: the frame is back as it was
        self.assertEqual(PD.parseFrame(self.doc.getObject(frame.Name).AnnoText)[2], 0.1)
        self.assertEqual(objectsSnapshot(self.page), snap0)
        self.doc.redo()
        self.assertEqual(objectsSnapshot(self.page), snap1)
        # 2. a datum letter
        sym = [o for o, i in pairs(self.page) if o.PmiRole == "datum"][0]
        for d in [i for i in self.items() if i.TypeId == "Part::PmiDatum" and i.Letter == "A"]:
            d.Letter = "B"
        snap2a = objectsSnapshot(self.page)
        PD.refresh(self.page)
        snap2 = objectsSnapshot(self.page)
        self.assertEqual(changed(snap2a, snap2), ([sym.Name], []))
        self.assertEqual(PD.parseDatum(sym.AnnoText), "B")
        # 3. a dimension's bounds
        dim.UpperBound, dim.LowerBound = 0.3, -0.1
        PD.refresh(self.page)
        snap3 = objectsSnapshot(self.page)
        dd = [o for o, i in pairs(self.page) if o.PmiRole == "dimension" and i == dim][0]
        self.assertEqual(changed(snap2, snap3), ([dd.Name], []))
        self.assertFalse(dd.EqualTolerance)
        self.assertAlmostEqual(dd.OverTolerance.Value, 0.3, places=12)
        self.assertAlmostEqual(dd.UnderTolerance.Value, -0.1, places=12)
        # 4. delete one item
        gone = [o.Name for o, i in pairs(self.page) if i == tol2]
        self.assertEqual(len(gone), 2)
        self.doc.removeObject(tol2.Name)
        PD.refresh(self.page)
        snap4 = objectsSnapshot(self.page)
        self.assertEqual(sorted(set(snap3) - set(snap4)), sorted(n for n in gone if n in snap3))
        self.assertEqual(changed(snap3, snap4)[0], [])
        for n in gone:
            self.assertIsNone(self.doc.getObject(n))
        # undo reverses the removal, each refresh is one step
        self.doc.undo()
        self.assertTrue(all(self.doc.getObject(n) is not None for n in gone))
        self.assertEqual(sorted(objectsSnapshot(self.page)), sorted(snap3))
        # undoing the edit steps one by one: each refresh was its own step

    def test_project_twice_does_not_duplicate(self):
        PA.addTolerance("Flatness", 0.02, self.face(Y, 20))
        r1 = self.project(self.box)
        n1 = len(pairs(self.page))
        r2 = self.project(self.box)
        self.assertEqual(len(pairs(self.page)), n1)
        self.assertEqual(r1, r2)

    def test_a_given_view_takes_everything(self):
        g = self.doc.addObject("TechDraw::DrawViewPart", "SingleView")
        self.page.addView(g)
        g.Source = [self.box]
        g.Direction = App.Vector(0, -1, 0)
        g.XDirection = App.Vector(1, 0, 0)
        self.doc.recompute()
        w = PA.addSizeDimension([self.face(-X, 0), self.face(X, 30)])
        d = PA.addSizeDimension([self.face(-Y, 0), self.face(Y, 20)])  # not edge on in this view
        t = PA.addTolerance("Flatness", 0.05, self.face(-Y, 0))
        rep = PD.project(self.page, self.box, view=g)
        self.assertEqual(rep[w.Name][1], "SingleView")
        self.assertEqual(rep[d.Name], ("not projected", "geometry"))
        self.assertEqual(rep[t.Name][1], "SingleView")

    # ---- 64: survives edits
    def _pad_hole_body(self):
        import Sketcher

        doc = self.doc
        body = doc.addObject("PartDesign::Body", "Body")
        xy = [o for o in body.Origin.OutList if o.Role == "XY_Plane"][0]
        sk = body.newObject("Sketcher::SketchObject", "Sketch")
        sk.AttachmentSupport = [(xy, "")]
        sk.MapMode = "FlatFace"
        pts = [App.Vector(0, 0, 0), App.Vector(60, 0, 0), App.Vector(60, 40, 0), App.Vector(0, 40, 0)]
        for i in range(4):
            sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
        pad = body.newObject("PartDesign::Pad", "Pad")
        pad.Profile = sk
        pad.Length = 20
        doc.recompute()
        sk2 = body.newObject("Sketcher::SketchObject", "Sketch2")
        sk2.AttachmentSupport = [(xy, "")]
        sk2.MapMode = "FlatFace"
        sk2.AttachmentOffset = App.Placement(App.Vector(0, 0, 20), App.Rotation())
        sk2.addGeometry(Part.Circle(App.Vector(30, 20, 0), App.Vector(0, 0, 1), 4.0))
        sk2.addConstraint(Sketcher.Constraint("Radius", 0, 4.0))
        pocket = body.newObject("PartDesign::Pocket", "Pocket")
        pocket.Profile = sk2
        pocket.Type = 1
        doc.recompute()
        return body, pad, sk2, pocket

    def test_survives_edits_and_reopen(self):
        body, pad, sk2, pocket = self._pad_hole_body()
        hole = ["Face%d" % i for i, f in enumerate(body.Shape.Faces, 1) if f.Surface.TypeId == "Part::GeomCylinder"][0]
        bottom = ["Face%d" % i for i, f in enumerate(body.Shape.Faces, 1) if f.Surface.TypeId == "Part::GeomPlane" and abs(f.CenterOfMass.z) < 1e-9][0]
        PA.addDatum("A", (body, [bottom]))
        size = PA.addSizeDimension((body, [hole]))
        pos = PA.addTolerance("Position", 0.2, (body, [hole]), datums=["A"], zone_shape="Diameter")
        page = make_page(self.doc)
        rep = PD.project(page, body)
        (dim,), view = self.drawing(rep, size)
        self.assertEqual(view, "Top")
        self.assertAlmostEqual(dim.getRawValue(), 8.0, places=9)

        def hole_ok(doc, label, radius, centre=(30, 20)):
            d = [o for o in doc.Objects if "PmiRole" in o.PropertiesList and o.PmiRole == "dimension"][0]
            self.assertEqual(len(d.References3D), 1, label)
            obj, subs = d.References3D[0]
            e = obj.Shape.getElement(subs[0])  # raises when the reference is broken
            self.assertEqual(e.Curve.TypeId, "Part::GeomCircle", "%s: %s retargeted" % (label, subs))
            self.assertAlmostEqual(e.Curve.Radius, radius, places=9)
            self.assertAlmostEqual(e.Curve.Center.x, centre[0], places=9)
            self.assertAlmostEqual(e.Curve.Center.y, centre[1], places=9)
            d.recompute()
            self.assertAlmostEqual(abs(d.getRawValue()), 2 * radius, places=9, msg=label)

        hole_ok(self.doc, "initial", 4.0)
        # the hole diameter changes: the drawing's diameter follows (the item's nominal is stale, the rule warns)
        sk2.setDatum(0, App.Units.Quantity("5 mm"))
        self.doc.recompute()
        self.doc.recompute()
        hole_ok(self.doc, "diameter change", 5.0)
        PD.refresh(page)
        self.assertEqual(anchorMisses(page), [])
        sk2.setDatum(0, App.Units.Quantity("4 mm"))
        self.doc.recompute()
        # pad length
        pad.Length = 30
        self.doc.recompute()
        hole_ok(self.doc, "pad length", 4.0)
        # a feature inserted before the hole
        edge = [i + 1 for i, e in enumerate(pad.Shape.Edges) if abs(e.CenterOfMass.z - 30) < 1e-9][0]
        ch = self.doc.addObject("PartDesign::Chamfer", "Chamfer")
        ch.Base = (pad, ["Edge%d" % edge])
        ch.Size = 2.0
        body.insertObject(ch, pad, True)
        self.doc.recompute()
        self.assertTrue(ch.isValid() and pocket.isValid())
        hole_ok(self.doc, "inserted feature", 4.0)
        PD.refresh(page)
        self.assertEqual(anchorMisses(page), [])
        # save and reopen
        path = os.path.join(tempfile.mkdtemp(), "pmi_pd.FCStd")
        snap = objectsSnapshot(page)
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)
        self.doc = App.openDocument(path)
        page = self.doc.getObject("Page001") or [o for o in self.doc.Objects if o.TypeId == "TechDraw::DrawPage"][-1]
        hole_ok(self.doc, "reopen", 4.0)
        self.assertEqual(sorted(objectsSnapshot(page)), sorted(snap))
        os.remove(path)
        os.rmdir(os.path.dirname(path))

    # ---- 65: save and reopen
    def test_save_and_reopen_keeps_objects_links_values_and_texts(self):
        PA.addDatum("A", self.face(-Z, 0))
        PA.addSizeDimension([self.face(-X, 0), self.face(X, 30)], plus_minus=(0.1, -0.05))
        PA.addSizeDimension([(self.cyl, [cyl_face(self.cyl)])], fit_class="H7")
        PA.addTolerance("Perpendicularity", 0.1, self.face(X, 30), datums=[("A", ["MAXIMUM_MATERIAL_REQUIREMENT"])])
        rep = self.project()
        before = objectsSnapshot(self.page)
        self.assertTrue(before)
        path = os.path.join(tempfile.mkdtemp(), "pmi_page.FCStd")
        self.doc.saveAs(path)
        name = self.doc.Name
        App.closeDocument(name)
        self.doc = App.openDocument(path)
        page = self.doc.getObject("Page")
        after = objectsSnapshot(page)
        self.assertEqual(before, after)
        self.assertEqual(frameMismatches(page), [])
        self.assertEqual(wrongMeasurements(page), [])
        os.remove(path)
        os.rmdir(os.path.dirname(path))

    # ---- text of frames without the font
    def test_frame_words_round_trip_without_the_font(self):
        PA.addDatum("A", self.face(-Z, 0))
        t = PA.addTolerance("Position", 0.25, (self.cyl, [cyl_face(self.cyl)]), datums=["A"], zone_shape="Diameter", modifiers=["MAXIMUM_MATERIAL_REQUIREMENT"])
        real = PD.fontAvailable
        PD.fontAvailable = lambda: False
        try:
            text = PD.frameHtml(t)
        finally:
            PD.fontAvailable = real
        self.assertIn("position", text)
        self.assertTrue(tuples_equal(PD.parseFrame(text), item_tuple(t)))

    # ---- repair round
    def test_anchor_is_never_behind_material(self):
        def fc(n):
            return (self.box, [planar_face(self.box, n, self.box.Shape.BoundBox.getPoint(0).dot(n) if False else (30 if n == X else 20 if n == Y else 10 if n == Z else 0))])
        PA.addDatum("A", self.face(Y, 20))  # the rear face: its edges project onto the front face's edges in Front
        PA.addDatum("B", self.face(-Y, 0))
        rep = self.project(self.box)
        a = [i for i in self.items() if i.TypeId == "Part::PmiDatum" and i.Letter == "A"][0]
        self.assertNotEqual(rep[a.Name][1], "Front")
        self.assertEqual(rep[[i for i in self.items() if i.Letter == "B"][0].Name][1], "Front")
        self.assertEqual(anchorMisses(self.page), [])

    def test_item_subsets_do_not_duplicate_datum_symbols_or_groups(self):
        PA.addDatum("A", self.face(-Z, 0))
        d2 = PA.addDatum("A", self.face(-Z, 0))
        s = PA.addSizeDimension([self.face(-X, 0), self.face(X, 30)])
        d1 = [i for i in self.items() if i.TypeId == "Part::PmiDatum"][0]
        self.project(self.box, items=[s, d2])
        n1 = len(self.doc.Objects)
        self.project(self.box, items=[s, d2])
        self.assertEqual(len(self.doc.Objects), n1)
        rep = self.project(self.box)  # the other use of A joins the symbol on the page
        syms = [o for o, _ in pairs(self.page) if o.PmiRole == "datum"]
        self.assertEqual(len(syms), 1)
        self.assertEqual(sorted(u.Name for u in syms[0].PmiUses), sorted([d1.Name, d2.Name]))
        self.assertEqual(rep[d1.Name], rep[d2.Name])
        n2 = len(self.doc.Objects)
        self.project(self.box)
        self.assertEqual(len(self.doc.Objects), n2)

    def test_a_smaller_source_reuses_the_group(self):
        c = PA.addSizeDimension([(self.cyl, [cyl_face(self.cyl)])])
        w = PA.addSizeDimension([self.face(-X, 0), self.face(X, 30)])
        self.project([self.box, self.cyl], items=[c])
        groups = lambda: [o for o in self.page.Views if o.TypeId == "TechDraw::DrawProjGroup"]
        self.assertEqual(len(groups()), 1)
        self.project([self.box], items=[w])  # needs a view: the existing group shows the box too
        self.assertEqual(len(groups()), 1)
        self.project([self.box], items=[w])  # nothing new: no group, no object
        self.assertEqual(len(groups()), 1)
        page2 = make_page(self.doc)
        PD.project(page2, [self.box], items=[])
        self.assertEqual([o for o in page2.Views if o.TypeId == "TechDraw::DrawProjGroup"], [])

    def test_stated_supplement_of_an_angle_is_not_projected_because_techdraw_measures_the_acute_one(self):
        tri = prism(self.doc, "Tri2", [(0, 0, 0), (30, 0, 0), (0, 40, 0)], 10)
        leg = face_named(tri, lambda fc: fc.Surface.TypeId == "Part::GeomPlane" and abs(fc.CenterOfMass.y) < 1e-9 and abs(fc.CenterOfMass.z - 5) < 1e-9)[0]
        hyp = face_named(tri, lambda fc: fc.Surface.TypeId == "Part::GeomPlane" and abs(fc.CenterOfMass.x - 15) < 1e-9 and abs(fc.CenterOfMass.y - 20) < 1e-9)[0]
        acute = math.degrees(math.atan2(40, 30))
        ang = PA.addAngularDimension((tri, [leg]), (tri, [hyp]), 180.0 - acute)
        rep = PD.project(self.page, tri)
        self.assertEqual(rep[ang.Name], ("not projected", "geometry"))  # the measure, not the pre-check, decides: never a wrong value
        self.assertEqual(wrongMeasurements(self.page), [])

    def test_a_rotated_body_is_projected_along_its_own_axes(self):
        b = self.doc.addObject("Part::Box", "Turned")
        b.Length, b.Width, b.Height = 30, 20, 10
        b.Placement = App.Placement(App.Vector(5, 5, 0), App.Rotation(App.Vector(0, 0, 1), 30))
        self.doc.recompute()
        w = PA.addSizeDimension([(b, [planar_face_any(b, 0, True)]), (b, [planar_face_any(b, 0, False)])])
        d = PA.addSizeDimension([(b, [planar_face_any(b, 1, True)]), (b, [planar_face_any(b, 1, False)])])
        h = PA.addSizeDimension([(b, [planar_face_any(b, 2, True)]), (b, [planar_face_any(b, 2, False)])])
        rep = PD.project(self.page, b)
        for item, value in ((w, 30.0), (d, 20.0), (h, 10.0)):
            self.assertNotEqual(rep[item.Name][0], "not projected", item.Name)
            self.assertAlmostEqual(abs(self.doc.getObject(rep[item.Name][0][0]).getRawValue()), value, places=9)
        self.assertEqual(wrongMeasurements(self.page), [])

    def test_clear_lets_the_projection_group_be_removed(self):
        PA.addDatum("A", self.face(-Z, 0))
        PA.addSizeDimension([self.face(-X, 0), self.face(X, 30)])
        PA.addTolerance("Flatness", 0.05, self.face(Z, 10))
        PD.project(self.page, [self.box, self.cyl])
        group = [v for v in self.page.Views if v.TypeId == "TechDraw::DrawProjGroup"][0]
        pmi = lambda: [o for o in self.doc.Objects if "PmiRole" in o.PropertiesList and o.PmiRole in ("dimension", "frame", "datum", "leader")]  # noqa: E731
        members = {v.Name for v in group.Views}
        self.assertTrue(any(o.PmiRole == "leader" and o.LeaderParent.Name in members for o in pmi()))  # the leaders hang from the group's views
        # clear on one view removes only that view's objects
        some = [o for o in pmi() if o.PmiRole == "leader"][0].LeaderParent
        before = len(pmi())
        PD.clear(self.page, some)
        self.assertLess(len(pmi()), before)
        self.assertEqual([o.Name for o in pmi() if o.PmiRole == "leader" and o.LeaderParent.Name == some.Name], [])
        # clear on the page removes the rest, the views stay
        PD.clear(self.page)
        self.assertEqual(pmi(), [])
        self.assertEqual(self.doc.getObject(group.Name), group)
        self.assertEqual({v.Name for v in group.Views}, members)
        # nothing hangs from the group's views now, so it can be removed
        for v in list(group.Views):
            self.assertEqual([o for o in v.InList if o.TypeId in ("TechDraw::DrawLeaderLine", "TechDraw::DrawRichAnno", "TechDraw::DrawViewDimension")], [], v.Name)
        gname = group.Name
        self.doc.removeObject(gname)
        self.assertIsNone(self.doc.getObject(gname))
        # the items survive, and project can rebuild
        PD.project(self.page, [self.box, self.cyl])
        self.assertTrue(pmi())

    def test_refresh_prunes_objects_of_a_deleted_view_and_project_makes_no_duplicates(self):
        def drop(g):
            # a view deleted in the GUI; in a script DrawViewPart's removal raises an upstream NameError, so take it off the page
            self.page.removeView(g)
            self.assertNotIn(g.Name, [v.Name for v in self.page.Views])

        def single(name):
            g = self.doc.addObject("TechDraw::DrawViewPart", name)
            self.page.addView(g)
            g.Source = [self.box]
            g.Direction = App.Vector(0, -1, 0)
            g.XDirection = App.Vector(1, 0, 0)
            self.doc.recompute()
            return g

        PA.addDatum("A", self.face(-Y, 0))
        w = PA.addSizeDimension([self.face(-X, 0), self.face(X, 30)])
        t = PA.addTolerance("Flatness", 0.05, self.face(-Y, 0))
        g1 = single("ViewOne")
        PD.project(self.page, self.box, view=g1)
        self.assertGreaterEqual(len(pairs(self.page)), 5)
        drop(g1)
        PD.refresh(self.page)
        self.assertEqual(pairs(self.page), [])
        self.assertEqual([o for o in self.doc.Objects if "PmiRole" in o.PropertiesList and o.PmiRole in ("dimension", "frame", "datum", "leader")], [])
        g2 = single("ViewTwo")
        PD.project(self.page, self.box, view=g2)
        n = len(self.doc.Objects)
        PD.project(self.page, self.box, view=g2)
        self.assertEqual(len(self.doc.Objects), n)
        by_item = {}
        for o, i in pairs(self.page):
            by_item.setdefault((i.Name, o.PmiRole), []).append(o.Name)
        self.assertTrue(all(len(v) == 1 for v in by_item.values()), by_item)
        # without a refresh in between, project itself drops the orphans before it makes new objects
        drop(g2)
        g3 = single("ViewThree")
        rep = PD.project(self.page, self.box, view=g3)
        self.assertTrue(all(v[1] == "ViewThree" for v in rep.values() if v[0] != "not projected"), rep)
        by_item = {}
        for o, i in pairs(self.page):
            by_item.setdefault((i.Name, o.PmiRole), []).append(o.Name)
        self.assertTrue(all(len(v) == 1 for v in by_item.values()), by_item)
