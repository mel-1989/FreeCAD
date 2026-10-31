# SPDX-License-Identifier: LGPL-2.1-or-later
"""Oracle tests for the clean-room bend corner relief (G07 slice 4a, check 4).

The part is an open tray: a 40 x 30 plate with edge flanges on the +x and +y edges, so the two
bends meet at the corner edge (40, 30). The relief shape S sits on the flat pattern, centred on
that corner; the plate is xi <= 0, eta <= 0, flange 1 (+x) xi >= 0 and flange 2 (+y) eta >= 0.
Expected values come from closed forms only:
- bend zone length BA = theta (R + K T) (Machinery's Handbook); wall start at BA
- removed volume = T (A_plate + A_wall1 + A_wall2) + T (R + T/2) / (R + K T) (A_bend1 + A_bend2),
  the A_* the areas of S clipped to each region (disc, stadium and polygon area by closed forms or
  polygon clipping, never by OCCT)
- flat area = plate + strips - area of S on the three quadrants, and the flat's relief outline is
  S itself, translated to the corner of the flat (the plate keeps its place in the flat)
"""

import math
import os
import tempfile
import unittest

import FreeCAD
import Part
from FreeCAD import Vector as V

import SheetMetalBendCornerRelief as CR
import SheetMetalEdgeFlange as EF
import SheetMetalFlatPattern as FP
import SheetMetalUnfoldCmd as UC
from SMTests.testEdgeFlange import PL, W, find_edge, make_box
from SMTests.testOracle import unfold_target_face
from SMTests.testUnfoldFeature import feature_unfold


def _case(name, T, r_t, angle, kind, size, mode="Absolute", ratio=2.0, rot=0.0, k=0.42, L=15.0):
    return dict(name=name, T=T, R=r_t * T, angle=angle, kind=kind, size=size, mode=mode, ratio=ratio,
                rot=rot, k=k, L=L)


CASES = [
    _case("r01_round_T1_R1T_d3", 1.0, 1.0, 90.0, "Round", 3.0),
    _case("r02_round_T2_R1T_d2T", 2.0, 1.0, 90.0, "Round", 2.0, mode="Thickness"),
    _case("r03_square_T2_R2T_s6", 2.0, 2.0, 90.0, "Square", 6.0, k=0.5),
    _case("r04_square_T1_R.5T_s2T", 1.0, 0.5, 90.0, "Square", 2.0, mode="Thickness", k=0.33),
    _case("r05_obround_T2_R1T_w4_x2.5", 2.0, 1.0, 90.0, "Obround", 4.0, ratio=2.5),
    _case("r06_obround_T1.5_R1T_w3_x3_rot90", 1.5, 1.0, 90.0, "Obround", 3.0, ratio=3.0, rot=90.0),
    _case("r07_round_T1_R1T_d9_reaches_wall", 1.0, 1.0, 90.0, "Round", 9.0),
    _case("r08_round_T2_R1T_d5_a75", 2.0, 1.0, 75.0, "Round", 5.0),
    _case("r09_square_T2_R1T_s5_rot45", 2.0, 1.0, 90.0, "Square", 5.0, rot=45.0),
    _case("r10_obround_T1_R2T_w2T_x4_a45_rot30", 1.0, 2.0, 45.0, "Obround", 2.0, mode="Thickness",
          ratio=4.0, rot=30.0, k=0.45),
    # an oblique cut leaves bend-face slivers; they are part of the bend, not bends of their own
    _case("r11_obround_T1_R1T_w2T_x5_rot135", 1.0, 1.0, 90.0, "Obround", 2.0, mode="Thickness",
          ratio=5.0, rot=135.0, k=0.45),
]


# --- closed-form areas of the relief shape clipped to a box ------------------------------------
def _clip_halfplane(poly, point, normal):
    """Convex polygon clipped to {p : (p - point) . normal >= 0} (Sutherland-Hodgman)."""
    def side(p):
        return (p[0] - point[0]) * normal[0] + (p[1] - point[1]) * normal[1]

    out = []
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]
        sp, sq = side(p), side(q)
        if (sp >= 0) != (sq >= 0):
            t = sp / (sp - sq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
        if sq >= 0:
            out.append(q)
    return out


def _box_poly(x0, x1, y0, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _poly_area(pts):
    if len(pts) < 3:
        return 0.0
    return 0.5 * abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1])))


def _tri_circle(a, b, r):
    """Signed area of (triangle origin, a, b) intersected with the disc of radius r about the origin."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    qa = dx * dx + dy * dy
    ts = [0.0, 1.0]
    if qa > 0.0:
        qb = 2.0 * (a[0] * dx + a[1] * dy)
        qc = a[0] * a[0] + a[1] * a[1] - r * r
        disc = qb * qb - 4.0 * qa * qc
        if disc > 0.0:
            sq = math.sqrt(disc)
            ts += [t for t in ((-qb - sq) / (2 * qa), (-qb + sq) / (2 * qa)) if 0.0 < t < 1.0]
    ts.sort()
    total = 0.0
    for t0, t1 in zip(ts, ts[1:]):
        p = (a[0] + t0 * dx, a[1] + t0 * dy)
        q = (a[0] + t1 * dx, a[1] + t1 * dy)
        m = (0.5 * (p[0] + q[0]), 0.5 * (p[1] + q[1]))
        cross = p[0] * q[1] - p[1] * q[0]
        if m[0] * m[0] + m[1] * m[1] <= r * r:
            total += 0.5 * cross
        else:
            total += 0.5 * r * r * math.atan2(cross, p[0] * q[0] + p[1] * q[1])
    return total


def disc_polygon_area(poly, r, center=(0.0, 0.0)):
    """Area of a convex polygon inside the disc of radius r about `center` (closed form)."""
    pts = [(p[0] - center[0], p[1] - center[1]) for p in poly]
    if len(pts) < 3:
        return 0.0
    return abs(sum(_tri_circle(a, b, r) for a, b in zip(pts, pts[1:] + pts[:1])))


def polygon_box_area(poly, x0, x1, y0, y1):
    """Area of a convex polygon clipped to the box."""
    pts = list(poly)
    for point, normal in (((x0, 0.0), (1.0, 0.0)), ((x1, 0.0), (-1.0, 0.0)),
                          ((0.0, y0), (0.0, 1.0)), ((0.0, y1), (0.0, -1.0))):
        pts = _clip_halfplane(pts, point, normal)
        if not pts:
            return 0.0
    return _poly_area(pts)


def _rot(p, deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return (p[0] * c - p[1] * s, p[0] * s + p[1] * c)


def shape_area(c, size, box):
    """Area of the case's relief shape in box = (xi0, xi1, eta0, eta1); never uses OCCT."""
    x0, x1, y0, y1 = box
    ext = c["ratio"] * size + size if c["kind"] == "Obround" else 2.0 * size
    x0, x1, y0, y1 = max(x0, -ext), min(x1, ext), max(y0, -ext), min(y1, ext)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    boxp = _box_poly(x0, x1, y0, y1)
    if c["kind"] == "Round":
        return disc_polygon_area(boxp, size / 2.0)
    if c["kind"] == "Square":
        h = size / 2.0
        poly = [_rot(p, c["rot"]) for p in ((-h, -h), (h, -h), (h, h), (-h, h))]
        return polygon_box_area(poly, x0, x1, y0, y1)
    half = (c["ratio"] * size - size) / 2.0
    w = size / 2.0
    rect = [_rot(p, c["rot"]) for p in ((-half, -w), (half, -w), (half, w), (-half, w))]
    total = polygon_box_area(rect, x0, x1, y0, y1)
    for sx in (-1.0, 1.0):  # the half disc beyond each rectangle end
        centre = _rot((sx * half, 0.0), c["rot"])
        normal = _rot((sx, 0.0), c["rot"])
        poly = _clip_halfplane(boxp, centre, normal)
        total += disc_polygon_area(poly, w, centre)
    return total


def expected_areas(c, T):
    size = c["size"] * T if c["mode"] == "Thickness" else c["size"]
    th = math.radians(c["angle"])
    rho = c["R"] + c["k"] * T
    ba = th * rho
    big = 1e3
    a = {
        "plate": shape_area(c, size, (-big, 0.0, -big, 0.0)),
        "b1": shape_area(c, size, (0.0, ba, -big, 0.0)),
        "w1": shape_area(c, size, (ba, big, -big, 0.0)),
        "b2": shape_area(c, size, (-big, 0.0, 0.0, ba)),
        "w2": shape_area(c, size, (-big, 0.0, ba, big)),
    }
    return size, ba, rho, a


def corner_edge(shape):
    for i, e in enumerate(shape.Edges):
        a, b = e.Vertexes[0].Point, e.Vertexes[-1].Point
        if all(abs(p.x - PL) < 1e-6 and abs(p.y - W) < 1e-6 for p in (a, b)):
            return "Edge%d" % (i + 1)
    raise AssertionError("corner edge not found")


def corner_edges(shape):
    """Every straight edge with both ends on the plate corner line (x, y) = (PL, W)."""
    out = []
    for i, e in enumerate(shape.Edges):
        a, b = e.Vertexes[0].Point, e.Vertexes[-1].Point
        if all(abs(p.x - PL) < 1e-6 and abs(p.y - W) < 1e-6 for p in (a, b)):
            out.append("Edge%d" % (i + 1))
    return out


def thickness_corner_edge(shape, T):
    return next(n for n in corner_edges(shape) if abs(shape.getElement(n).Length - T) < 1e-6)


def raw_tray(doc, T, R, a1, a2, length=15.0):
    """A tray whose +x and +y flanges have their own angles (the Edge Flange feature refuses
    some of these); a plain Part feature."""
    box = make_box(doc, T)
    res = box.Shape
    for which, a in (("+x", a1), ("+y", a2)):
        p0, along, w, out, nsheet, thk = EF._edge_frame(box.Shape, box.Shape.getElement(find_edge(box, which)))
        fl = EF.make_flange_solid(p0, along, w, out, nsheet, R, thk, a, EF.straight_length(length, a, R, thk))
        res = res.fuse(fl).removeSplitter()
    tray = doc.addObject("Part::Feature", "Tray")
    tray.Shape = Part.Solid(res.Solids[0])
    return tray


class Doc(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("ReliefTest")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def tray(self, c):
        box = make_box(self.doc, c["T"])
        tray = EF.make_edge_flange(self.doc, box, [find_edge(box, "+x"), find_edge(box, "+y")],
                                   length=c["L"], angle=c["angle"], radius=c["R"], kfactor=c["k"])
        return tray

    def feature(self, c):
        tray = self.tray(c)
        edge = corner_edge(tray.Shape)
        rel = CR.make_corner_relief(self.doc, tray, edge, reliefType=c["kind"], size=c["size"],
                                    sizeMode=c["mode"], ratio=c["ratio"], rotation=c["rot"],
                                    kfactor=c["k"])
        return tray, rel


def full_flat_face(c, T):
    th = math.radians(c["angle"])
    ba = th * (c["R"] + c["k"] * T)
    s = EF.straight_length(c["L"], c["angle"], c["R"], T)
    rects = [(0, 0, PL, W), (PL, 0, PL + ba + s, W), (0, W, PL, W + ba + s)]
    faces = [Part.Face(Part.makePolygon([V(x0, y0, 0), V(x1, y0, 0), V(x1, y1, 0), V(x0, y1, 0), V(x0, y0, 0)]))
             for x0, y0, x1, y1 in rects]
    fused = faces[0].fuse(faces[1:]).removeSplitter()
    return fused.Faces[0], PL * W + (ba + s) * (W + PL)


class TestCornerRelief(Doc):
    report = []  # (case, outline deviation, flat area error)

    def test_oracle_cases(self):
        self.assertGreaterEqual(len(CASES), 8)
        for c in CASES:
            with self.subTest(case=c["name"]):
                tray, rel = self.feature(c)
                self.assertNotIn("Invalid", rel.State)
                sh = rel.Shape
                self.assertTrue(sh.isValid())
                self.assertEqual(len(sh.Solids), 1)
                T = c["T"]
                size, ba, rho, a = expected_areas(c, T)
                self.assertAlmostEqual(rel.sizeUsed.Value, size, 9)
                f = (c["R"] + T / 2.0) / rho
                removed = T * (a["plate"] + a["w1"] + a["w2"]) + T * f * (a["b1"] + a["b2"])
                self.assertGreater(removed, 0.0)
                got = tray.Shape.Volume - sh.Volume
                self.assertAlmostEqual(got / removed - 1.0, 0.0, delta=1e-6, msg="removed volume %g vs %g" % (got, removed))
                if c["name"].startswith("r07"):
                    self.assertGreater(a["w1"] + a["w2"], 0.0, "this case must reach the walls")

    def test_flat_pattern_keeps_the_relief_outline(self):
        for c in CASES:
            with self.subTest(case=c["name"]):
                tray, rel = self.feature(c)
                T = c["T"]
                feat = feature_unfold(self.doc, rel, unfold_target_face(rel.Shape), k=c["k"], old=None)
                self.assertNotIn("Invalid", feat.State)
                flat = feat.Shape
                self.assertTrue(flat.isValid())
                self.assertEqual(len(flat.Solids), 1)
                top = max((f for f in flat.Faces if f.Surface.TypeId == "Part::GeomPlane"), key=lambda f: f.Area)
                self.assertEqual(len(top.Wires), 1, "one flat face without holes")
                self.assertTrue(top.isValid())
                size, ba, rho, a = expected_areas(c, T)
                full, full_area = full_flat_face(c, T)
                cut_area = sum(a.values())
                self.assertAlmostEqual(top.Area, full_area - cut_area, delta=1e-3, msg="flat area")
                # the unfolder rebuilds the cut walls inside the bends approximately (about 1e-5
                # relative on the solid); the flat face above is what carries the outline
                self.assertAlmostEqual(flat.Volume / T / top.Area, 1.0, delta=5e-5, msg="flat solid volume")
                # expected flat: the full flat minus S at the corner point (the plate keeps its place)
                shape2d = CR.make_shape_2d(c["kind"], size, c["ratio"], c["rot"])
                shape2d.translate(V(PL, W, 0))
                expected = full.cut(shape2d).Faces[0]
                self.assertAlmostEqual(expected.Area, top.Area, delta=1e-3)
                got_wire = top.OuterWire.copy()
                got_wire.translate(V(0, 0, -top.CenterOfMass.z))  # into the plane z = 0
                exp_wire = expected.OuterWire
                dev = 0.0
                for src, ref in ((got_wire, exp_wire), (exp_wire, got_wire)):
                    for e in src.Edges:
                        for p in e.discretize(Number=60):
                            dev = max(dev, ref.distToShape(Part.Vertex(p))[0])
                self.assertLess(dev, 1e-3, "relief outline deviation %g mm" % dev)
                self.report.append((c["name"], dev, top.Area - (full_area - cut_area)))
                bends = UC.get_bend_lines(feat)
                self.assertEqual(len(bends), 2)

    def test_oblique_cut_keeps_one_bend_per_axis(self):
        """The oblique obround leaves slivers of the bend faces (34 degree arcs of a 90 degree
        bend); they must not show as bends of their own, in BendData or in the DXF."""
        T, R, k = 1.0, 1.0, 0.45
        for a1, a2 in ((90.0, 90.0), (45.0, 135.0), (135.0, 45.0)):
            with self.subTest(angles=(a1, a2)):
                doc = FreeCAD.newDocument("Oblique")
                try:
                    tray = raw_tray(doc, T, R, a1, a2)
                    rel = CR.make_corner_relief(doc, tray, thickness_corner_edge(tray.Shape, T), reliefType="Obround",
                                                size=2.0, sizeMode="Thickness", ratio=5.0,
                                                rotation=135.0, kfactor=k)
                    self.assertNotIn("Invalid", rel.State)
                    feat = feature_unfold(doc, rel, unfold_target_face(rel.Shape), k=k, old=None)
                    self.assertNotIn("Invalid", feat.State)
                    bends = UC.get_bend_lines(feat)
                    self.assertEqual(sorted(round(b.angle, 6) for b in bends), sorted([a1, a2]))
                    for b in bends:
                        self.assertGreater(b.length, 20.0, "a bend line is a full-length line, not a fragment")
                    path = os.path.join(tempfile.mkdtemp(), "oblique.dxf")
                    drawn = UC.export_flat_dxf(feat, path)
                    self.assertEqual(len(drawn), 2)
                finally:
                    FreeCAD.closeDocument(doc.Name)

    def test_merge_axis_pieces_unit(self):
        ax = [[0.0, 1.0, 0.0], [5.0, 0.0, 0.0]]
        other = [[0.0, 1.0, 0.0], [9.0, 0.0, 0.0]]
        base = dict(direction="UP", inside_radius=1.0, allowance=2.0, k_factor=0.4, thickness=1.0)
        main = dict(base, angle=1.5, axis=ax, segments=[(V(3, 0, 0), V(3, 10, 0))])
        piece = dict(base, angle=0.5, axis=[[0.0, -1.0, 0.0], ax[1]], segments=[(V(3.7, 4, 0), V(3.7, 5, 0))])
        far = dict(base, angle=0.5, axis=other, segments=[(V(8, 0, 0), V(8, 10, 0))])
        out = FP.merge_axis_pieces([piece, main, far])
        self.assertEqual([r["angle"] for r in out], [1.5, 0.5])
        self.assertEqual(len(out[0]["segments"]), 2)
        for a, b in out[0]["segments"][1:]:
            self.assertAlmostEqual(a.x, 3.0)
            self.assertAlmostEqual(b.x, 3.0)

    def test_relief_takes_k_from_the_part(self):
        c = dict(CASES[0])
        c["k"] = 0.42
        tray = self.tray(c)
        edge = corner_edge(tray.Shape)
        rel = CR.make_corner_relief(self.doc, tray, edge, size=3.0, kfactor=0.30)
        self.assertAlmostEqual(rel.kUsed, 0.42, 12)
        ref = CR.make_corner_relief(self.doc, tray, edge, name="Ref", size=3.0, kfactor=0.42, kfromPart=False)
        self.assertAlmostEqual(ref.kUsed, 0.42, 12)
        self.assertAlmostEqual(rel.Shape.Volume, ref.Shape.Volume, 9)
        own = CR.make_corner_relief(self.doc, tray, edge, name="Own", size=3.0, kfactor=0.30, kfromPart=False)
        self.assertAlmostEqual(own.kUsed, 0.30, 12)
        self.assertNotAlmostEqual(own.Shape.Volume, rel.Shape.Volume, 6)

    def test_k_mismatch_message(self):
        c = dict(CASES[0])
        c["k"] = 0.42
        tray = self.tray(c)
        edge = corner_edge(tray.Shape)
        # a relief on a bare solid (no K in the chain) keeps its own K
        bare = self.doc.addObject("Part::Feature", "Bare")
        bare.Shape = tray.Shape
        rel = CR.make_corner_relief(self.doc, bare, edge, size=3.0, kfactor=0.42)
        same = feature_unfold(self.doc, rel, unfold_target_face(rel.Shape), k=0.42, old=None)
        self.assertIsNone(CR.relief_k_mismatch(same))
        diff = feature_unfold(self.doc, rel, unfold_target_face(rel.Shape), k=0.30, old=None)
        msg = CR.relief_k_mismatch(diff)
        self.assertIn("0.42", msg)
        self.assertIn("0.3", msg)
        # the mismatch is a flat-pattern problem, so the DXF export refuses it
        self.assertIn("K-factor", FP.bends_and_problem_from_json(diff.BendData)[2])
        with self.assertRaises(ValueError):
            UC.export_flat_dxf(diff, os.path.join(tempfile.gettempdir(), "r027_kmismatch.dxf"))
        # a relief that takes K from the flanges' K and an unfold with another K
        rel2 = CR.make_corner_relief(self.doc, tray, edge, name="R2", size=3.0)
        self.assertIsNone(CR.relief_k_mismatch(feature_unfold(self.doc, rel2, unfold_target_face(rel2.Shape), k=0.42, old=None)))
        self.assertIsNotNone(CR.relief_k_mismatch(feature_unfold(self.doc, rel2, unfold_target_face(rel2.Shape), k=0.31, old=None)))

    def test_relief_flats_pass_the_volume_check_exactly(self):
        """The relief's flatVolumeOffset makes the flat's volume match the bends and the cut
        exactly, so no legitimate relief is refused (cad-fork run 027 repair)."""
        worst = 0.0
        for c in CASES:
            with self.subTest(case=c["name"]):
                tray, rel = self.feature(c)
                feat = feature_unfold(self.doc, rel, unfold_target_face(rel.Shape), k=c["k"], old=None)
                bends, _n, problem = FP.bends_and_problem_from_json(feat.BendData)
                self.assertEqual(problem, "")
                part = rel.Shape.Volume
                bare = FP.expected_bend_volume_change(bends)
                offset = CR.relief_volume_offset(feat)
                self.assertAlmostEqual(offset, rel.flatVolumeOffset, 12)
                if abs(c["k"] - 0.5) > 1e-9:  # at K = 0.5 the zones lose the same volume
                    self.assertNotEqual(offset, 0.0)
                residual = abs((feat.Shape.Volume - part) - (bare + offset)) / part
                worst = max(worst, residual)
                self.assertLess(residual, 2e-5)
                # without the offset the 1e-4 bound is exceeded for the larger reliefs
                if abs((feat.Shape.Volume - part) - bare) > FP.VOLUME_RESIDUAL * part:
                    self.assertIn("does not match the part's bends",
                                  FP.flat_problem(feat.Shape, (0, 0, 1), bends, c["T"], 2, part))
                    self.assertNotIn("formed feature",
                                     FP.flat_problem(feat.Shape, (0, 0, 1), bends, c["T"], 2, part))
                out = os.path.join(tempfile.mkdtemp(), "relief.dxf")
                UC.export_flat_dxf(feat, out)
                self.assertTrue(os.path.exists(out))
        print("corner relief worst volume residual %.2e of the part volume" % worst)

    def test_offset_without_relief_is_zero_and_formed_parts_stay_flagged(self):
        c = dict(CASES[0])
        tray = self.tray(c)
        feat = feature_unfold(self.doc, tray, unfold_target_face(tray.Shape), k=c["k"], old=None)
        self.assertEqual(CR.relief_volume_offset(feat), 0.0)
        bends, _n, problem = FP.bends_and_problem_from_json(feat.BendData)
        self.assertEqual(problem, "")
        # a wrong offset (or a stray volume change) is refused with a message that names the residual
        msg = FP.flat_problem(feat.Shape, (0, 0, 1), bends, c["T"], 2, tray.Shape.Volume,
                              volume_offset=0.01 * tray.Shape.Volume)
        self.assertIn("residual", msg)

    def test_k_mismatch_under_a_bend_table_unfold(self):
        from SMTests import flat_dxf_cases as DC
        c = dict(CASES[0])
        c["k"] = 0.33
        tray = self.tray(c)
        edge = corner_edge(tray.Shape)
        rel = CR.make_corner_relief(self.doc, tray, edge, size=3.0)
        self.assertAlmostEqual(rel.kUsed, 0.33, 12)
        self.assertEqual(len(rel.bendRadii), 2)
        feat = feature_unfold(self.doc, rel, unfold_target_face(rel.Shape), material=DC.TABLE_REF,
                              old=None)
        bends, _n, problem = FP.bends_and_problem_from_json(feat.BendData)
        self.assertAlmostEqual(bends[0].k_factor, 0.43, 6)
        self.assertIn("K-factor 0.33", problem)
        self.assertIn("uses 0.43", problem)
        with self.assertRaises(ValueError):
            UC.export_flat_dxf(feat, os.path.join(tempfile.gettempdir(), "r027_table_kmismatch.dxf"))
        # the same relief built with the K the table gives (as recorded on the bends) is not flagged
        rel2 = CR.make_corner_relief(self.doc, tray, edge, name="R2", size=3.0, kfromPart=False,
                                     kfactor=bends[0].k_factor)
        feat2 = feature_unfold(self.doc, rel2, unfold_target_face(rel2.Shape), material=DC.TABLE_REF,
                               old=None)
        self.assertEqual(FP.bends_and_problem_from_json(feat2.BendData)[2], "")
        self.assertIsNone(CR.relief_k_mismatch(feat2, FP.bends_and_problem_from_json(feat2.BendData)[0]))
        # the table's listed K (0.43), given by hand, matches its back-computed K within K_TOLERANCE
        rel3 = CR.make_corner_relief(self.doc, tray, edge, name="R3", size=3.0, kfromPart=False,
                                     kfactor=0.43)
        feat3 = feature_unfold(self.doc, rel3, unfold_target_face(rel3.Shape), material=DC.TABLE_REF,
                               old=None)
        self.assertEqual(FP.bends_and_problem_from_json(feat3.BendData)[2], "")
        UC.export_flat_dxf(feat3, os.path.join(tempfile.gettempdir(), "r027_table_kmatch.dxf"))

    def test_over_90_degree_corner_refusal_names_the_cause(self):
        tray = raw_tray(self.doc, 1.0, 1.0, 135.0, 135.0)
        long_edge = next(n for n in corner_edges(tray.Shape)
                         if abs(tray.Shape.getElement(n).Length - 1.0) > 1e-3)
        with self.assertRaises(CR.ReliefError) as cm:
            CR.build_corner_relief(tray.Shape, long_edge, "Round", 3.0)
        self.assertIn("one thickness long", str(cm.exception))
        self.assertNotIn("not one thickness apart", str(cm.exception))

    def test_zz_report(self):
        for name, dev, err in self.report:
            print("corner relief %-40s outline deviation %.2e mm  flat area error %.2e mm2" % (name, dev, err))

    def test_recompute_and_property_change(self):
        c = CASES[1]
        tray, rel = self.feature(c)
        rel.reliefType = "Square"
        rel.size = 3.0
        rel.sizeMode = "Absolute"
        self.doc.recompute()
        self.assertNotIn("Invalid", rel.State)
        c2 = dict(c, kind="Square", size=3.0, mode="Absolute")
        size, ba, rho, a = expected_areas(c2, c["T"])
        f = (c["R"] + c["T"] / 2.0) / rho
        removed = c["T"] * (a["plate"] + a["w1"] + a["w2"]) + c["T"] * f * (a["b1"] + a["b2"])
        self.assertAlmostEqual((tray.Shape.Volume - rel.Shape.Volume) / removed - 1.0, 0.0, delta=1e-6)

    def test_refusals(self):
        c = CASES[0]
        tray = self.tray(c)
        edge = corner_edge(tray.Shape)
        base = dict(relief_type="Round", size=3.0, size_mode="Absolute", k_factor=0.4)

        def build(shape=tray.Shape, name=edge, **kw):
            args = dict(base)
            args.update(kw)
            return CR.build_corner_relief(shape, name, **args)

        flat_edge = next("Edge%d" % (i + 1) for i, e in enumerate(tray.Shape.Edges)
                         if isinstance(e.Curve, Part.Line) and abs(e.Length - W) < 1e-6)
        cases = [
            ("zero size", dict(size=0.0), "greater than zero"),
            ("negative size", dict(size=-2.0), "greater than zero"),
            ("nan size", dict(size=float("nan")), "finite"),
            ("inf size", dict(size=float("inf")), "finite"),
            ("below thickness", dict(size=0.5), "smaller than the thickness"),
            ("below thickness in T", dict(size=0.5, size_mode="Thickness"), "smaller than the thickness"),
            ("bad shape", dict(relief_type="Triangle"), "relief type"),
            ("bad mode", dict(size_mode="inches"), "size mode"),
            ("bad ratio", dict(relief_type="Obround", ratio=1.0), "ratio"),
            ("bad K", dict(k_factor=1.5), "K-factor"),
            ("not a corner edge", dict(name=flat_edge), "corner"),
        ]
        for name, kw, text in cases:
            with self.subTest(case=name):
                with self.assertRaises(CR.ReliefError) as cm:
                    build(**kw)
                self.assertIn(text, str(cm.exception))
        # a single flange has no corner edge: the plate corner edge of a one-flange part
        box = make_box(self.doc, 1.0, "Single")
        one = EF.make_edge_flange(self.doc, box, [find_edge(box, "+x")], length=10.0, angle=90.0,
                                  radius=1.0)
        vertical = next("Edge%d" % (i + 1) for i, e in enumerate(one.Shape.Edges)
                        if isinstance(e.Curve, Part.Line) and abs(e.Length - 1.0) < 1e-6)
        with self.assertRaises(CR.ReliefError):
            CR.build_corner_relief(one.Shape, vertical, "Round", 3.0)
        # a feature with a refusal is Invalid and recovers
        rel = CR.make_corner_relief(self.doc, tray, edge, size=0.5)
        self.assertIn("Invalid", rel.State)
        rel.size = 3.0
        self.doc.recompute()
        self.assertNotIn("Invalid", rel.State)

    def test_helper_areas(self):
        r = 2.0
        big = _box_poly(-9, 9, -9, 9)
        self.assertAlmostEqual(disc_polygon_area(big, r), math.pi * r * r, delta=1e-12)
        self.assertAlmostEqual(disc_polygon_area(_box_poly(-9, 0, -9, 0), r), math.pi * r * r / 4, delta=1e-12)
        # strip 0 <= x <= 1, y <= 0 of the disc: integral of sqrt(r^2 - x^2) over [0, 1]
        exact = 0.5 * (1.0 * math.sqrt(r * r - 1.0) + r * r * math.asin(1.0 / r))
        self.assertAlmostEqual(disc_polygon_area(_box_poly(0, 1, -9, 0), r), exact, delta=1e-12)
        self.assertAlmostEqual(polygon_box_area([(-1, -1), (1, -1), (1, 1), (-1, 1)], 0, 5, 0, 5), 1.0, delta=1e-12)
        c = dict(kind="Obround", ratio=3.0, rot=0.0)  # stadium area = 2 w (L - w) + pi w^2
        self.assertAlmostEqual(shape_area(c, 2.0, (-99, 99, -99, 99)), 2 * 1.0 * (6.0 - 2.0) + math.pi, delta=1e-12)
        c["rot"] = 30.0
        self.assertAlmostEqual(shape_area(c, 2.0, (-99, 99, -99, 99)), 2 * 1.0 * (6.0 - 2.0) + math.pi, delta=1e-12)


if __name__ == "__main__":
    unittest.main()
