# SPDX-License-Identifier: LGPL-2.1-or-later
"""Oracle tests for the clean-room lightening cutout (G07 slice 4a, acceptance check 5).

Expected values come from the profile only: removed volume = (profile area) x T x copies, with
circle area pi r^2, slot area (L - w) w + pi w^2 / 4 and rounded rectangle a b - (4 - pi) r^2;
the unfolded flat must carry the same number of inner loops with the profile's area and
perimeter, the pattern's pitch distances, and (next to a bend) the distance from the bend line:
developed distance = distance to the bend tangent edge + BA / 2.
"""

import itertools
import math
import unittest

import FreeCAD
import Part
from FreeCAD import Vector as V

import SheetMetalFlatPattern as FP
import SheetMetalLighteningCutout as LC
import SheetMetalUnfoldCmd
from SMTests.testOracle import Case, build_base, unfold_target_face
from SMTests.testUnfoldFeature import feature_unfold, refused


def circle_geo(cx, cy, r):
    return [Part.Circle(V(cx, cy, 0), V(0, 0, 1), r)], math.pi * r * r, 2 * math.pi * r


def slot_geo(cx, cy, ln, w):
    r, c = w / 2.0, (ln - w) / 2.0
    geo = [Part.LineSegment(V(cx - c, cy - r, 0), V(cx + c, cy - r, 0)),
           Part.LineSegment(V(cx + c, cy + r, 0), V(cx - c, cy + r, 0)),
           Part.ArcOfCircle(Part.Circle(V(cx + c, cy, 0), V(0, 0, 1), r), -math.pi / 2, math.pi / 2),
           Part.ArcOfCircle(Part.Circle(V(cx - c, cy, 0), V(0, 0, 1), r), math.pi / 2, 3 * math.pi / 2)]
    return geo, (ln - w) * w + math.pi * w * w / 4.0, 2 * (ln - w) + math.pi * w


def rrect_geo(cx, cy, a, b, r):
    x0, x1, y0, y1 = cx - a / 2, cx + a / 2, cy - b / 2, cy + b / 2
    geo = [Part.LineSegment(V(x0 + r, y0, 0), V(x1 - r, y0, 0)), Part.LineSegment(V(x1, y0 + r, 0), V(x1, y1 - r, 0)),
           Part.LineSegment(V(x1 - r, y1, 0), V(x0 + r, y1, 0)), Part.LineSegment(V(x0, y1 - r, 0), V(x0, y0 + r, 0))]
    for (px, py), a0 in (((x1 - r, y0 + r), -90), ((x1 - r, y1 - r), 0), ((x0 + r, y1 - r), 90), ((x0 + r, y0 + r), 180)):
        geo.append(Part.ArcOfCircle(Part.Circle(V(px, py, 0), V(0, 0, 1), r), math.radians(a0), math.radians(a0 + 90)))
    return geo, a * b - (4 - math.pi) * r * r, 2 * (a - 2 * r) + 2 * (b - 2 * r) + 2 * math.pi * r


SHAPES = {"circle": circle_geo, "slot": slot_geo, "rrect": rrect_geo}

# name, base ("plate" or a testOracle Case), shape and args, pattern, near-bend distance of the first copy
CASES = [
    dict(name="c01_plate_circle_T2", base="plate", T=2.0, shape=("circle", (6.0,)), pattern="None"),
    dict(name="c02_plate_slot_T1.5", base="plate", T=1.5, shape=("slot", (24.0, 8.0)), pattern="None"),
    dict(name="c03_plate_rrect_T3", base="plate", T=3.0, shape=("rrect", (26.0, 16.0, 4.0)), pattern="None"),
    dict(name="c04_L_circle_near_bend", base=Case("L", 2.0, 1.0, 0.42, [60.0, 30.0], [90.0]),
         shape=("circle", (5.0,)), pattern="None", near=12.0),
    dict(name="c05_U_web_slot_near_bend", base=Case("U", 2.0, 1.0, 0.42, [25.0, 50.0, 25.0], [90.0, 90.0]),
         shape=("slot", (20.0, 6.0)), pattern="None", near=10.0),
    dict(name="c06_135_rrect_near_bend", base=Case("B135", 1.0, 2.0, 0.5, [40.0, 40.0], [135.0]),
         shape=("rrect", (14.0, 10.0, 2.0)), pattern="None", near=10.0),
    dict(name="c07_plate_linear_4_circles", base="plate", T=2.0, shape=("circle", (3.0,)), pattern="Linear",
         cx=4, cy=1, px=15.0, py=0.0, centre=True),
    dict(name="c08_L_grid_3x2_circles", base=Case("L2", 2.0, 1.0, 0.42, [70.0, 30.0], [90.0]),
         shape=("circle", (2.5,)), pattern="Grid", cx=3, cy=2, px=12.0, py=12.0, near=10.0),
    dict(name="c09_U_web_grid_2x2_slots", base=Case("U3", 3.0, 1.0, 0.42, [30.0, 70.0, 30.0], [90.0, 90.0]),
         shape=("slot", (14.0, 5.0)), pattern="Grid", cx=2, cy=2, px=18.0, py=12.0, centre=True),
    dict(name="c10_plate_grid_3x3_circles_T1", base="plate", T=1.0, shape=("circle", (4.0,)), pattern="Grid",
         cx=3, cy=3, px=14.0, py=14.0, centre=True),
]

rows = []


def planar_big(shape):
    return max((f for f in shape.Faces if f.Surface.TypeId == "Part::GeomPlane"), key=lambda f: f.Area)


def face_name(shape, face):
    for i, f in enumerate(shape.Faces):
        if f.isSame(face):
            return "Face%d" % (i + 1)


def face_frame(face):
    """(origin, u, w, n): origin at the face's centre of mass, u along its longest straight edge."""
    n = face.normalAt(0, 0)
    n.normalize()
    edges = [e for e in face.Edges if isinstance(e.Curve, Part.Line)]
    e = max(edges, key=lambda e: e.Length)
    u = e.Vertexes[-1].Point - e.Vertexes[0].Point
    u.normalize()
    return face.CenterOfMass, u, n.cross(u), n


def tangent_edges(shape, face):
    out = []
    for e in face.Edges:
        if any(f.Surface.TypeId == "Part::GeomCylinder" for f in shape.ancestorsOfType(e, Part.Face)):
            out.append(e)
    return out


def line_dist(p, a, b, normal=None):
    """Distance from p to the line ab; with `normal`, measured in the plane normal to it."""
    d = b - a
    if normal is not None:
        d = d - normal * d.dot(normal)
    d.normalize()
    r = p - a
    if normal is not None:
        r = r - normal * r.dot(normal)
    return r.cross(d).Length


def make_sketch(doc, face, geo, du=0.0, dv=0.0, lift=0.0):
    origin, u, w, n = face_frame(face)
    sk = doc.addObject("Sketcher::SketchObject", "Profile")
    m = FreeCAD.Matrix(u.x, w.x, n.x, 0, u.y, w.y, n.y, 0, u.z, w.z, n.z, 0, 0, 0, 0, 1)
    sk.Placement = FreeCAD.Placement(origin + u * du + w * dv + n * lift, FreeCAD.Rotation(m))
    for g in geo:
        sk.addGeometry(g)
    doc.recompute()
    return sk


class Doc(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("CutoutTest")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def base(self, c):
        if c == "plate" or isinstance(c, dict):
            thick = c["T"] if isinstance(c, dict) else 2.0
            obj = self.doc.addObject("Part::Feature", "Plate")
            obj.Shape = Part.makeBox(90.0, 60.0, thick, V(0, 0, 0))
            self.doc.recompute()
            return obj
        return build_base(self.doc, c)


class TestLighteningCutout(Doc):
    def build(self, c):
        base = self.base(c) if c["base"] == "plate" else self.base(c["base"])
        face = planar_big(base.Shape)
        if c["base"] == "plate":
            face = [f for f in base.Shape.Faces if f.normalAt(0, 0).z > 0.9][0]
        origin, u, w, n = face_frame(face)
        kind, args = c["shape"]
        du = dv = 0.0
        direction = u
        if c.get("near") is not None:  # first copy `near` mm (to its centre) from the nearest bend tangent edge
            edges = tangent_edges(base.Shape, face)
            e = min(edges, key=lambda e: line_dist(origin, e.Vertexes[0].Point, e.Vertexes[-1].Point))
            d0 = line_dist(origin, e.Vertexes[0].Point, e.Vertexes[-1].Point)
            toward = (e.CenterOfMass - origin).dot(u)
            sign = 1.0 if toward > 0 else -1.0
            du = sign * (d0 - c["near"])
            direction = u * (-sign)
        elif c.get("centre"):
            du = -0.5 * (c["cx"] - 1) * c["px"]
            dv = -0.5 * (c["cy"] - 1) * c["py"]
            direction = u
        geo, area, perim = SHAPES[kind](0.0, 0.0, *args)
        sk = make_sketch(self.doc, face, geo, du, dv)
        props = dict(Pattern=c["pattern"])
        if c["pattern"] != "None":
            props.update(CountX=c["cx"], CountY=c["cy"], PitchX=c["px"], PitchY=c["py"], Direction=direction)
        feat = LC.make_lightening_cutout(self.doc, base, face_name(base.Shape, face), sk, **props)
        return base, face, feat, area, perim, (c["cx"] * (c["cy"] if c["pattern"] == "Grid" else 1)
                                               if c["pattern"] != "None" else 1)

    def test_cases(self):
        self.assertGreaterEqual(len(CASES), 8)
        for c in CASES:
            with self.subTest(case=c["name"]):
                self.run_case(c)

    def run_case(self, c):
        base, face, feat, area, perim, copies = self.build(c)
        self.assertNotIn("Invalid", feat.State, c["name"])
        shape = feat.Shape
        self.assertTrue(shape.isValid())
        self.assertEqual(len(shape.Solids), 1)
        thk = feat.thickness.Value
        removed = base.Shape.Volume - shape.Volume
        want = area * thk * copies
        rel = abs(removed - want) / want
        self.assertLess(rel, 1e-6, "removed volume")
        # unfold through the default unfolder
        flat = feature_unfold(self.doc, feat, unfold_target_face(shape), old=None)
        self.assertFalse(refused(flat), "unfold refused")
        problem = FP.bends_and_problem_from_json(flat.BendData)[2] if flat.BendData else ""
        self.assertEqual(problem, "")
        fs = flat.Shape
        big = planar_big(fs)
        loops = [Part.Face(w) for w in big.Wires if not w.isSame(big.OuterWire)]
        self.assertEqual(len(loops), copies, "inner loops")
        d_area = max(abs(l.Area - area) for l in loops)
        d_perim = max(abs(l.OuterWire.Length - perim) for l in loops)
        self.assertLess(d_perim, 1e-3)
        self.assertLess(d_area, 1e-3)
        cs = [l.CenterOfMass for l in loops]
        got = sorted(a.distanceToPoint(b) for a, b in itertools.combinations(cs, 2))
        d_pitch = 0.0
        if copies > 1:
            geo_offs = []
            nx = c["cx"]
            ny = c["cy"] if c["pattern"] == "Grid" else 1
            geo_offs = [(i * c["px"], j * c["py"]) for j in range(ny) for i in range(nx)]
            want_d = sorted(math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in itertools.combinations(geo_offs, 2))
            d_pitch = max(abs(g - w_) for g, w_ in zip(got, want_d))
            self.assertLess(d_pitch, 1e-3)
        d_bend = None
        if c.get("near") is not None:
            edges = tangent_edges(base.Shape, face)
            prof_c = [pf for pf in [Part.Face(w) for w in feat.Sketch.Shape.Wires]][0].CenterOfMass
            bends = SheetMetalUnfoldCmd.get_bend_lines(flat)
            self.assertTrue(bends)
            ba = bends[0].allowance
            want_b = min(line_dist(prof_c, e.Vertexes[0].Point, e.Vertexes[-1].Point) for e in edges) + ba / 2.0
            fn = big.normalAt(0, 0)
            # the pattern runs away from the bend, so the nearest loop is the first copy
            got_b = min(line_dist(cs[k], b.start, b.end, fn) for k in range(len(cs)) for b in bends)
            d_bend = abs(got_b - want_b)
            self.assertLess(d_bend, 1e-3, "distance from the bend line")
        rows.append((c["name"], copies, rel, d_perim, d_area, d_pitch, d_bend))

    def test_feature_refuses_readably(self):
        base = self.base("plate")
        face = [f for f in base.Shape.Faces if f.normalAt(0, 0).z > 0.9][0]
        sk = make_sketch(self.doc, face, circle_geo(0, 0, 40.0)[0])  # wider than the plate
        feat = LC.make_lightening_cutout(self.doc, base, face_name(base.Shape, face), sk)
        self.assertIn("Invalid", feat.State)


class TestCutoutRefusals(Doc):
    def plate(self, thick=2.0):
        base = self.base(dict(T=thick))
        face = [f for f in base.Shape.Faces if f.normalAt(0, 0).z > 0.9][0]
        return base, face, face_name(base.Shape, face)

    def assertRefused(self, fragment, base, name, sk, **kw):
        with self.assertRaises(LC.CutoutError) as cm:
            LC.build_cutout(base.Shape, name, sk.Shape, **kw)
        self.assertIn(fragment, str(cm.exception))

    def test_open_profile(self):
        base, face, name = self.plate()
        sk = make_sketch(self.doc, face, [Part.LineSegment(V(0, 0, 0), V(10, 0, 0)),
                                          Part.LineSegment(V(10, 0, 0), V(10, 10, 0)),
                                          Part.LineSegment(V(10, 10, 0), V(0, 10, 0))])
        self.assertRefused("open", base, name, sk)

    def test_self_intersecting_profile(self):
        base, face, name = self.plate()
        pts = [V(0, 0, 0), V(10, 10, 0), V(10, 0, 0), V(0, 10, 0)]
        sk = make_sketch(self.doc, face, [Part.LineSegment(a, b) for a, b in zip(pts, pts[1:] + pts[:1])])
        self.assertRefused("intersects itself", base, name, sk)

    def test_profile_not_on_the_face(self):
        base, face, name = self.plate()
        sk = make_sketch(self.doc, face, circle_geo(0, 0, 5.0)[0], lift=0.5)
        self.assertRefused("not in the plane", base, name, sk)
        sk2 = make_sketch(self.doc, face, circle_geo(0, 0, 5.0)[0])
        sk2.Placement = FreeCAD.Placement(sk2.Placement.Base, FreeCAD.Rotation(V(1, 0, 0), 10).multiply(sk2.Placement.Rotation))
        self.doc.recompute()
        self.assertRefused("not in the plane", base, name, sk2)

    def test_profile_crossing_the_face_edge(self):
        base, face, name = self.plate()
        sk = make_sketch(self.doc, face, circle_geo(0, 0, 5.0)[0], du=43.0)
        self.assertRefused("reaches beyond", base, name, sk)

    def test_profile_crossing_a_bend(self):
        base = build_base(self.doc, Case("Lx", 2.0, 1.0, 0.42, [60.0, 30.0], [90.0]))
        face = planar_big(base.Shape)
        origin, u, w, n = face_frame(face)
        e = tangent_edges(base.Shape, face)[0]
        d0 = line_dist(origin, e.Vertexes[0].Point, e.Vertexes[-1].Point)
        sign = 1.0 if (e.CenterOfMass - origin).dot(u) > 0 else -1.0
        sk = make_sketch(self.doc, face, circle_geo(0, 0, 6.0)[0], du=sign * (d0 - 2.0))
        self.assertRefused("reaches beyond", base, face_name(base.Shape, face), sk)

    def test_profile_on_a_bend_face_is_refused(self):
        base = build_base(self.doc, Case("Ly", 2.0, 1.0, 0.42, [60.0, 30.0], [90.0]))
        cyl = [f for f in base.Shape.Faces if f.Surface.TypeId == "Part::GeomCylinder"][0]
        sk = self.doc.addObject("Sketcher::SketchObject", "P")
        sk.addGeometry(Part.Circle(V(0, 0, 0), V(0, 0, 1), 3.0))
        self.doc.recompute()
        self.assertRefused("planar", base, face_name(base.Shape, cyl), sk)

    def test_pattern_copies_must_not_overlap(self):
        base, face, name = self.plate()
        sk = make_sketch(self.doc, face, circle_geo(0, 0, 5.0)[0], du=-10.0)
        self.assertRefused("overlap", base, name, sk, pattern="Linear", count_x=3, pitch_x=8.0,
                           direction=face_frame(face)[1])

    def test_pattern_copy_leaving_the_face(self):
        base, face, name = self.plate()
        sk = make_sketch(self.doc, face, circle_geo(0, 0, 5.0)[0], du=-30.0)
        self.assertRefused("copy 6 reaches beyond", base, name, sk, pattern="Linear", count_x=6, pitch_x=15.0,
                           direction=face_frame(face)[1])

    def test_min_edge_distance(self):
        base, face, name = self.plate()
        sk = make_sketch(self.doc, face, circle_geo(0, 0, 5.0)[0], du=-35.0)  # edge of the hole 5 mm from the face edge
        LC.build_cutout(base.Shape, name, sk.Shape, min_edge_distance=4.0)
        self.assertRefused("below the minimum", base, name, sk, min_edge_distance=2 * 2.0 + 2.0)

    def test_nested_profiles(self):
        base, face, name = self.plate()
        sk = make_sketch(self.doc, face, circle_geo(0, 0, 5.0)[0] + circle_geo(0, 0, 3.0)[0])
        self.assertRefused("overlap or are nested", base, name, sk)

    def test_bad_pattern_arguments(self):
        base, face, name = self.plate()
        sk = make_sketch(self.doc, face, circle_geo(0, 0, 2.0)[0])
        self.assertRefused("pitch", base, name, sk, pattern="Linear", count_x=3, pitch_x=0.0)
        self.assertRefused("direction", base, name, sk, pattern="Linear", count_x=2, pitch_x=9.0,
                           direction=V(0, 0, 1))

    def test_design_guide_clearance(self):
        c = LC.design_guide_clearance(2.0, 2.0)
        self.assertEqual((c["edge"], c["bend"], c["bend_slot"]), (4.0, 6.0, 10.0))


def print_rows():
    print("%-34s %6s %10s %9s %9s %9s %9s" % ("case", "copies", "dV rel", "d_perim", "d_area", "d_pitch", "d_bend"))
    for r in rows:
        print("%-34s %6d %10.2e %9.2e %9.2e %9.2e %9s" % (r[0], r[1], r[2], r[3], r[4], r[5],
                                                         "-" if r[6] is None else "%.2e" % r[6]))


if __name__ == "__main__":
    unittest.main()
