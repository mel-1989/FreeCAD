# SPDX-License-Identifier: LGPL-2.1-or-later
"""Oracle tests for the faceted lofted bend (G07 slice 4b, checks 2 to 5).

The oracle is written here without calling the feature's code. From the mid-surface triangles
(read back as shapes) it works out, for every triangle, the signed turning angle and fold sense
at each hinge, moves each edge line of the triangle by -s * d * tan(theta / 2) at height d (s =
+1 when the fold is concave toward +d; boundary edges do not move), and integrates the section
area, which is quadratic in d, with Simpson's rule on three heights. Heights run over
[-T/2, T/2] (mid), [0, T] (inside, toward the profiles' centroid) or [-T, 0] (outside).

What is exact and what is not. The sum of the facet prisms is the part's volume only when the
prisms do not overlap. Prisms of facets that meet at a fan vertex (not neighbours) do overlap on
the concave side of a corner by a T^3 term: measured 1.8e-5 of the volume for a 100 mm frustum at
T = 2 (mid), 2e-4 for inside. So the solid is the union, and the tests assert

- the closed-form sum the feature reports equals this file's Simpson sum within 1e-9 (every case);
- the solid's volume equals that sum within 1e-6 when the thickness goes toward the convex side
  (outside), where the prisms cannot overlap, and for every case is the sum minus an overlap
  that is non-negative, below 1e-2 of the volume and grows faster than T^2 and slower than T^3;
- the points at mid-thickness over each triangle's centroid lie inside the solid.

Flat pattern: edge lengths of the developed triangles equal the 3D mid-surface edges within 1e-9
mm, the planar face's area equals the mid-surface area within 1e-9 relative, the union of the
developed triangles has the sum of their areas (no self-overlap), and the bend lines have the
hinges' lengths, dihedral angles and fold senses. Regenerate the two goldens with
``regenerate()`` (see its docstring).
"""

import math
import os
import tempfile
import unittest

import FreeCAD
import Part
from FreeCAD import Vector as V

import SheetMetalFlatPattern as FP
import SheetMetalLoftedBend as LB
import SheetMetalUnfoldCmd as UC
from SMTests.quiet import expected_recompute_errors
from SMTests.testFlatDxf import compare_entities, entities

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "lofted_dxf")


###################################################################################################
# Profiles
###################################################################################################

def open_rect(w, h, z, cx=0.0, cy=0.0, gap=2.0):
    """Four sides, counter-clockwise from the lower left corner; the last side stops `gap` short."""
    p = [V(cx - w / 2, cy - h / 2, z), V(cx + w / 2, cy - h / 2, z), V(cx + w / 2, cy + h / 2, z),
         V(cx - w / 2, cy + h / 2, z), V(cx - w / 2, cy - h / 2 + gap, z)]
    return Part.Compound([Part.makeLine(p[i], p[i + 1]) for i in range(4)])


def open_round(r, z, cx=0.0, cy=0.0, gap_deg=2.0, start=225.0):
    """Four quarter arcs, counter-clockwise from `start`; the last stops gap_deg short."""
    arcs = []
    for i in range(4):
        a0 = math.radians(start + 90 * i)
        a1 = math.radians(start + 90 * (i + 1) - (gap_deg if i == 3 else 0.0))
        arcs.append(Part.ArcOfCircle(Part.Circle(V(cx, cy, z), V(0, 0, 1), r), a0, a1).toShape())
    return Part.Compound(arcs)


def centroid_of(shapes, facets):
    pts = []
    for sh in shapes:
        for seg in LB.read_profile(sh, facets, "x"):
            pts.extend(seg["pts"])
    # same centroid as a mean of the unique profile points
    uniq = []
    for p in pts:
        if not any((p - q).Length < 1e-6 for q in uniq):
            uniq.append(p)
    c = V(0, 0, 0)
    for p in uniq:
        c = c + p
    return c * (1.0 / len(uniq))


# name, shape A, shape B, T, side, facets
CASES = [
    ("L01_frustum_100_60_T2_mid", lambda: open_rect(100, 100, 0), lambda: open_rect(60, 60, 80), 2.0, "mid", 4),
    ("L02_hopper_120x80_60x40_T1.5_inside", lambda: open_rect(120, 80, 0), lambda: open_rect(60, 40, 70, 15, 10), 1.5, "inside", 4),
    ("L03_sq2round_centred_T1_outside_4", lambda: open_rect(100, 100, 0), lambda: open_round(40, 80), 1.0, "outside", 4),
    ("L04_sq2round_offset_T3_mid_8", lambda: open_rect(140, 140, 0), lambda: open_round(45, 90, 12, -9), 3.0, "mid", 8),
    ("L05_sq2round_centred_T2_inside_12", lambda: open_rect(100, 100, 0), lambda: open_round(40, 80), 2.0, "inside", 12),
    ("L06_round2round_eccentric_T1.5_mid_6", lambda: open_round(50, 0, start=0.0), lambda: open_round(30, 60, 20, 0, start=0.0), 1.5, "mid", 6),
    ("L07_rect2round_T2_outside_5", lambda: open_rect(160, 100, 0), lambda: open_round(35, 70, 0, 0), 2.0, "outside", 5),
    ("L08_sq2rect_offset_T3_inside_4", lambda: open_rect(100, 100, 0), lambda: open_rect(70, 40, 60, 12, -8), 3.0, "inside", 4),
    ("L09_round2sq_T1_outside_12", lambda: open_round(45, 0, start=225.0), lambda: open_rect(90, 90, 70), 1.0, "outside", 12),
    ("L10_sq2round_offset_T2_outside_6", lambda: open_rect(120, 100, 0), lambda: open_round(40, 75, 10, 5), 2.0, "outside", 6),
]


def make_profiles(doc, case):
    _name, fa, fb, _T, _side, _facets = case
    a = doc.addObject("Part::Feature", "ProfileA")
    a.Shape = fa()
    b = doc.addObject("Part::Feature", "ProfileB")
    b.Shape = fb()
    return a, b


def make_case(doc, case, **over):
    name, _fa, _fb, T, side, facets = case
    a, b = make_profiles(doc, case)
    props = dict(thickness=T, side=side, facets=facets)
    props.update(over)
    return LB.make_lofted_bend(doc, a, b, name="LB", **props), a, b


###################################################################################################
# Independent oracle
###################################################################################################

def mid_triangles(feat):
    """Triangles (3 points) of the feature's mid-surface compound, oriented consistently."""
    tris = []
    for f in feat.midSurface.Faces:
        pts = [v.Point for v in f.OuterWire.OrderedVertexes]
        uniq = []
        for p in pts:
            if not any((p - q).Length < 1e-9 for q in uniq):
                uniq.append(p)
        assert len(uniq) == 3
        tris.append(uniq)

    def key(p):
        return tuple(round(c, 6) for c in (p.x, p.y, p.z))

    # make neighbouring triangles traverse their shared edge in opposite directions
    changed = True
    done = {0}
    order = [0]
    while changed:
        changed = False
        for i in list(done):
            edges_i = {(key(tris[i][j]), key(tris[i][(j + 1) % 3])) for j in range(3)}
            for k in range(len(tris)):
                if k in done:
                    continue
                edges_k = {(key(tris[k][j]), key(tris[k][(j + 1) % 3])) for j in range(3)}
                shared = [e for e in edges_k if (e[1], e[0]) in edges_i or e in edges_i]
                if not shared:
                    continue
                if any(e in edges_i for e in shared):  # same direction: flip k
                    tris[k] = [tris[k][0], tris[k][2], tris[k][1]]
                done.add(k)
                order.append(k)
                changed = True
    assert len(done) == len(tris)
    return tris


def unit(v):
    u = V(v)
    u.normalize()
    return u


def oracle(tris, centroid, T, side, invert=False):
    """(sum of the facet prism volumes by Simpson, hinge list). A hinge is (i, k, length,
    dihedral angle, up) for the facet pair i < k."""
    n = [unit((t[1] - t[0]).cross(t[2] - t[0])) for t in tris]
    areas = [0.5 * (t[1] - t[0]).cross(t[2] - t[0]).Length for t in tris]
    score = sum(a * ni.dot(centroid - (t[0] + t[1] + t[2]) * (1.0 / 3.0))
                for a, ni, t in zip(areas, n, tris))
    sign = 1.0 if score > 0 else -1.0
    if invert:
        sign = -sign
    n = [ni * sign for ni in n]

    def key(p):
        return tuple(round(c, 6) for c in (p.x, p.y, p.z))

    emap = {}
    for i, t in enumerate(tris):
        for j in range(3):
            emap.setdefault(frozenset((key(t[j]), key(t[(j + 1) % 3]))), []).append(i)
    lo, hi = {"mid": (-T / 2, T / 2), "inside": (0.0, T), "outside": (-T, 0.0)}[side]
    hinges, coef = [], {}
    for e, ks in emap.items():
        if len(ks) != 2:
            continue
        i, k = sorted(ks)
        th = math.atan2(n[i].cross(n[k]).Length, n[i].dot(n[k]))
        pts = [p for p in tris[i] if key(p) in e]
        other = [p for p in tris[k] if key(p) not in e][0]
        up = (other - pts[0]).dot(n[i]) > 0
        c = 0.0 if th < 1e-9 else (-math.tan(th / 2) if up else math.tan(th / 2))
        coef[(i, e)] = coef[(k, e)] = c
        if th >= 1e-9:
            hinges.append((i, k, (pts[0] - pts[1]).Length, th, up))

    def area(i, d):
        t = tris[i]
        e1 = unit(t[1] - t[0])
        e2 = n[i].cross(e1)
        xy = [((p - t[0]).dot(e1), (p - t[0]).dot(e2)) for p in t]
        cen = (sum(x for x, _ in xy) / 3, sum(y for _, y in xy) / 3)
        lines = []
        for j in range(3):
            (x0, y0), (x1, y1) = xy[j], xy[(j + 1) % 3]
            m = (y1 - y0, x0 - x1)
            ln = math.hypot(*m)
            m = (m[0] / ln, m[1] / ln)
            if m[0] * (cen[0] - x0) + m[1] * (cen[1] - y0) > 0:
                m = (-m[0], -m[1])
            e = frozenset((key(t[j]), key(t[(j + 1) % 3])))
            lines.append((m, m[0] * x0 + m[1] * y0 + coef.get((i, e), 0.0) * d))
        verts = []
        for j in range(3):
            (a1, b1), r1 = lines[j]
            (a2, b2), r2 = lines[(j + 1) % 3]
            det = a1 * b2 - a2 * b1
            verts.append(((r1 * b2 - r2 * b1) / det, (a1 * r2 - a2 * r1) / det))
        s = 0.0
        for j in range(3):
            s += verts[j][0] * verts[(j + 1) % 3][1] - verts[(j + 1) % 3][0] * verts[j][1]
        return abs(s) / 2.0

    total = 0.0
    for i in range(len(tris)):
        total += (hi - lo) / 6.0 * (area(i, lo) + 4.0 * area(i, (lo + hi) / 2) + area(i, hi))
    return total, hinges, n, sum(areas)


class Doc(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("LoftedBend")
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)
        self.tmp.cleanup()

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def unfold(self, feat):
        u = self.doc.addObject("Part::FeaturePython", "Unfold")
        UC.SMUnfold(u, feat, ["Face1"])
        u.KFactor = 0.4
        u.GenerateSketch = False
        self.doc.recompute()
        return u


###################################################################################################
# Check 3: volume oracle
###################################################################################################

class TestLoftedVolume(Doc):
    report = []

    def test_cases_against_the_oracle(self):
        self.assertGreaterEqual(len(CASES), 8)
        for case in CASES:
            with self.subTest(case=case[0]):
                doc = FreeCAD.newDocument("LV")
                try:
                    feat, a, b = make_case(doc, case)
                    self.assertNotIn("Invalid", feat.State, feat.getStatusString())
                    shape = feat.Shape
                    self.assertTrue(shape.isValid())
                    self.assertEqual(len(shape.Solids), 1)
                    T, side = case[3], case[4]
                    centroid = centroid_of([a.Shape, b.Shape], case[5])
                    vsum, hinges, normals, area = oracle(mid_triangles(feat), centroid, T, side)
                    self.assertAlmostEqual(feat.facetVolume / vsum, 1.0, delta=1e-9)
                    self.assertAlmostEqual(feat.midSurfaceArea / area, 1.0, delta=1e-9)
                    overlap = (vsum - shape.Volume) / vsum
                    if side == "outside":
                        self.assertLess(abs(overlap), 1e-6)
                    else:
                        self.assertGreater(overlap, -1e-7)
                    self.assertLess(overlap, 1e-2)
                    self.assertAlmostEqual(feat.overlapVolume, vsum - shape.Volume,
                                           delta=1e-6 * vsum)
                    self.assertEqual(feat.bendCount, len(hinges))
                    self.report.append((case[0], len(mid_triangles(feat)), len(hinges), overlap))
                finally:
                    FreeCAD.closeDocument(doc.Name)

    def test_mid_thickness_points_lie_inside_the_solid(self):
        for case in CASES[::3]:
            with self.subTest(case=case[0]):
                feat, a, b = make_case(self.doc, case)
                T, side = case[3], case[4]
                centroid = centroid_of([a.Shape, b.Shape], case[5])
                tris = mid_triangles(feat)
                _v, _h, normals, _a = oracle(tris, centroid, T, side)
                lo, hi = {"mid": (-T / 2, T / 2), "inside": (0.0, T), "outside": (-T, 0.0)}[side]
                for t, n in zip(tris, normals):
                    c = (t[0] + t[1] + t[2]) * (1.0 / 3.0)
                    self.assertTrue(feat.Shape.isInside(c + n * (0.5 * (lo + hi)), 1e-6, True))
                    self.assertFalse(feat.Shape.isInside(c + n * (hi + 0.5 * T + 5.0), 1e-6, True))

    def test_overlap_grows_like_the_square_of_the_thickness(self):
        ratios = []
        for T in (1.0, 2.0):
            doc = FreeCAD.newDocument("LV2")
            try:
                feat, _a, _b = make_case(doc, CASES[0], thickness=T, side="inside")
                ratios.append(feat.overlapVolume / feat.facetVolume)
            finally:
                FreeCAD.closeDocument(doc.Name)
        self.assertGreater(ratios[0], 0.0)
        self.assertTrue(3.5 < ratios[1] / ratios[0] < 9.0, ratios)  # T^2 and T^3 terms

    def test_recomputes_when_a_profile_changes(self):
        feat, a, b = make_case(self.doc, CASES[0])
        v0 = feat.Shape.Volume
        b.Shape = open_rect(40, 40, 80)
        self.doc.recompute()
        self.assertNotIn("Invalid", feat.State)
        self.assertNotAlmostEqual(feat.Shape.Volume, v0, delta=1.0)
        self.assertTrue(feat.Shape.isValid())

    def test_sketch_profiles_work(self):
        import Sketcher
        sk = []
        for z, w in ((0.0, 50.0), (60.0, 30.0)):
            s = self.doc.addObject("Sketcher::SketchObject", "Sk%d" % int(z))
            s.Placement = FreeCAD.Placement(V(0, 0, z), FreeCAD.Rotation())
            pts = [V(-w, -w, 0), V(w, -w, 0), V(w, w, 0), V(-w, w, 0), V(-w, -w + 2, 0)]
            for i in range(4):
                s.addGeometry(Part.LineSegment(pts[i], pts[i + 1]))
            sk.append(s)
        self.doc.recompute()
        feat = LB.make_lofted_bend(self.doc, sk[0], sk[1], thickness=2.0)
        self.assertNotIn("Invalid", feat.State, feat.getStatusString())
        self.assertTrue(feat.Shape.isValid())

    def test_save_and_restore(self):
        feat, _a, _b = make_case(self.doc, CASES[2])
        vol, name = feat.Shape.Volume, feat.Name
        path = self.path("lb.FCStd")
        self.doc.saveAs(path)
        FreeCAD.closeDocument(self.doc.Name)
        self.doc = FreeCAD.openDocument(path)
        obj = self.doc.getObject(name)
        self.assertAlmostEqual(obj.Shape.Volume, vol, delta=1e-9 * vol)
        self.doc.recompute()
        self.assertAlmostEqual(obj.Shape.Volume, vol, delta=1e-9 * vol)
        self.assertEqual(obj.bendCount, 19)

    def test_zz_report(self):
        for name, n, h, ov in self.report:
            print("lofted volume %-40s facets %3d folds %3d  prism overlap %.2e" % (name, n, h, ov))


###################################################################################################
# Check 4: flat pattern
###################################################################################################

def edge_lengths(face):
    return sorted(e.Length for e in face.Edges)


def check_flat(testcase, doc, feat, case, centroid):
    unf = testcase.unfold(feat)
    testcase.assertNotIn("Invalid", unf.State, unf.getStatusString())
    bends, normal, problem = FP.bends_and_problem_from_json(unf.BendData)
    testcase.assertEqual(problem, "")
    T, side = case[3], case[4]
    tris = mid_triangles(feat)
    _v, hinges, _n, area = oracle(tris, centroid, T, side)
    # triangles developed: true edge lengths
    worst_len = 0.0
    flat_faces, mid_faces = feat.flatTriangles.Faces, feat.midSurface.Faces
    testcase.assertEqual(len(flat_faces), len(mid_faces))
    for ff, mf in zip(flat_faces, mid_faces):
        a, b = edge_lengths(ff), edge_lengths(mf)
        worst_len = max(worst_len, max(abs(x - y) for x, y in zip(a, b)))
    testcase.assertLess(worst_len, 1e-9)
    # flat solid: one planar face of thickness T, area = mid-surface area
    shape = unf.Shape
    testcase.assertEqual(len(shape.Solids), 1)
    top = [f for f in shape.Faces if f.Surface.TypeId == "Part::GeomPlane"
           and abs(f.normalAt(0, 0).z) > 1 - 1e-9]
    testcase.assertEqual(len(top), 2)
    area_err = max(abs(f.Area / area - 1.0) for f in top)
    testcase.assertLess(area_err, 1e-9)
    testcase.assertAlmostEqual(shape.BoundBox.ZLength, T, delta=1e-9)
    # no self-overlap: the union of the developed triangles has the sum of their areas
    union = flat_faces[0].fuse(flat_faces[1:]).removeSplitter()
    union_area = sum(f.Area for f in union.Faces)
    sum_area = sum(f.Area for f in flat_faces)
    testcase.assertLess(abs(union_area / sum_area - 1.0), 1e-9)
    # bend lines: one per fold, with the hinge's length, dihedral angle and fold sense
    testcase.assertEqual(len(bends), len(hinges))
    want = sorted((round(h[2], 7), round(math.degrees(h[3]), 6), "UP" if h[4] else "DOWN")
                  for h in hinges)
    got = sorted((round(b.length, 7), round(b.angle, 6), b.direction) for b in bends)
    for (l1, a1, d1), (l2, a2, d2) in zip(want, got):
        testcase.assertLess(abs(l1 - l2), 1e-6)
        testcase.assertLess(abs(a1 - a2), 1e-5)
        testcase.assertEqual(d1, d2)
    # the flat check accepted it with the exact term; a flat checked without it would not be
    residual = abs((shape.Volume - feat.Shape.Volume)
                   - (FP.expected_bend_volume_change(bends) + feat.flatVolumeOffset)) \
        / feat.Shape.Volume
    testcase.assertLess(residual, 1e-9)
    verts = [v.Point for v in shape.Vertexes]
    for b in bends:
        for p in (b.start, b.end):
            testcase.assertLess(min(min((p - q).Length for q in verts), 1e-9), 1.01e-9)
    return unf, worst_len, area_err, residual


class TestLoftedFlat(Doc):
    report = []

    def test_cases_flat(self):
        for case in CASES:
            with self.subTest(case=case[0]):
                doc = FreeCAD.newDocument("LF")
                saved = self.doc
                self.doc = doc
                try:
                    feat, a, b = make_case(doc, case)
                    centroid = centroid_of([a.Shape, b.Shape], case[5])
                    _u, wl, ae, res = check_flat(self, doc, feat, case, centroid)
                    self.report.append((case[0], wl, ae, res))
                finally:
                    self.doc = saved
                    FreeCAD.closeDocument(doc.Name)

    def test_flat_check_needs_the_exact_term(self):
        feat, _a, _b = make_case(self.doc, CASES[7])
        unf = self.unfold(feat)
        bends, normal, _p = FP.bends_and_problem_from_json(unf.BendData)
        ok = FP.flat_problem(unf.Shape, normal, bends, 3.0, len(bends), feat.Shape.Volume, (),
                             feat.flatVolumeOffset)
        self.assertEqual(ok, "")
        self.assertNotEqual(FP.flat_problem(unf.Shape, normal, bends, 3.0, len(bends),
                                            feat.Shape.Volume), "")
        self.assertNotEqual(FP.flat_problem(unf.Shape, normal, bends, 3.0, len(bends),
                                            feat.Shape.Volume, (), feat.flatVolumeOffset * 1.01), "")

    def test_down_bends_exist_and_use_their_layer(self):
        # a round-to-round eccentric piece folds both ways
        feat, _a, _b = make_case(self.doc, CASES[5])
        unf = self.unfold(feat)
        dirs = {b.direction for b in UC.get_bend_lines(unf)}
        self.assertTrue(dirs <= {"UP", "DOWN"} and "UP" in dirs)
        path = self.path("x.dxf")
        UC.export_flat_dxf(unf, path)
        layers = {e["layer"] for e in entities(path)}
        self.assertIn("OUTLINE", layers)
        self.assertTrue(layers & {"BEND_UP", "BEND_DOWN"})

    def test_dxf_is_deterministic(self):
        feat, _a, _b = make_case(self.doc, CASES[2])
        unf = self.unfold(feat)
        p1, p2 = self.path("1.dxf"), self.path("2.dxf")
        UC.export_flat_dxf(unf, p1)
        self.doc.recompute()
        unf.touch()
        self.doc.recompute()
        UC.export_flat_dxf(unf, p2)
        with open(p1) as f1, open(p2) as f2:
            self.assertEqual(f1.read(), f2.read())

    def test_zz_report(self):
        for name, wl, ae, res in self.report:
            print("lofted flat %-40s edge length dev %.1e  area dev %.1e  volume residual %.1e"
                  % (name, wl, ae, res))


GOLDEN_CASES = [
    ("l01_square_to_round", CASES[2]),
    ("l02_hopper_offset", CASES[1]),
]


def build_golden(doc, case):
    feat, _a, _b = make_case(doc, case)
    u = doc.addObject("Part::FeaturePython", "Unfold")
    UC.SMUnfold(u, feat, ["Face1"])
    u.KFactor = 0.4
    u.GenerateSketch = False
    doc.recompute()
    return u


def regenerate(out_dir=FIXTURE_DIR):
    """Rebuild the lofted-bend goldens. Run with the SheetMetal folder on sys.path:

        FreeCADCmd -c "import sys; sys.path.insert(0, '.'); \\
            from SMTests import testLoftedBend as T; T.regenerate()"
    """
    os.makedirs(out_dir, exist_ok=True)
    for name, case in GOLDEN_CASES:
        doc = FreeCAD.newDocument("Regen")
        try:
            UC.export_flat_dxf(build_golden(doc, case), os.path.join(out_dir, name + ".dxf"))
            print("wrote", name)
        finally:
            FreeCAD.closeDocument(doc.Name)


class TestLoftedGoldens(Doc):
    def test_goldens_match_entity_by_entity(self):
        self.assertGreaterEqual(len(GOLDEN_CASES), 2)
        for name, case in GOLDEN_CASES:
            with self.subTest(golden=name):
                gold = os.path.join(FIXTURE_DIR, name + ".dxf")
                self.assertTrue(os.path.exists(gold), name)
                doc = FreeCAD.newDocument("LG")
                try:
                    u = build_golden(doc, case)
                    path = self.path(name + ".dxf")
                    UC.export_flat_dxf(u, path)
                    worst, problems = compare_entities(entities(gold), entities(path))
                    self.assertEqual(problems, [])
                    self.assertLess(worst, 1e-6)
                    layers = {e["layer"] for e in entities(path)}
                    self.assertIn("OUTLINE", layers)
                    self.assertTrue(layers & {"BEND_UP", "BEND_DOWN"})
                finally:
                    FreeCAD.closeDocument(doc.Name)

    def test_only_the_lofted_goldens_are_here(self):
        have = {n[:-4] for n in os.listdir(FIXTURE_DIR) if n.endswith(".dxf")}
        self.assertEqual(have, {n for n, _ in GOLDEN_CASES})


###################################################################################################
# Check 5: refusals
###################################################################################################

class TestLoftedRefusals(Doc):
    def refused(self, feat, *words):
        self.assertIn("Invalid", feat.State)
        msg = feat.getStatusString().lower()
        for w in words:
            self.assertIn(w, msg, msg)

    def make(self, a_shape, b_shape, **props):
        a = self.doc.addObject("Part::Feature", "PA")
        a.Shape = a_shape
        b = self.doc.addObject("Part::Feature", "PB")
        b.Shape = b_shape
        obj = self.doc.addObject("Part::FeaturePython", "LBr")
        LB.SMLoftedBend(obj, a, b)
        for k, v in props.items():
            setattr(obj, k, v)
        with expected_recompute_errors():
            self.doc.recompute()
        return obj

    def test_non_parallel_planes(self):
        b = open_rect(60, 60, 80)
        b.rotate(V(0, 0, 80), V(1, 0, 0), 10.0)
        self.refused(self.make(open_rect(100, 100, 0), b), "not parallel")

    def test_same_plane(self):
        self.refused(self.make(open_rect(100, 100, 0), open_rect(60, 60, 0)), "one plane")

    def test_closed_profile(self):
        closed = Part.makePolygon([V(-30, -30, 80), V(30, -30, 80), V(30, 30, 80), V(-30, 30, 80),
                                   V(-30, -30, 80)])
        self.refused(self.make(open_rect(100, 100, 0), closed), "closed", "seam gap")
        circle = Part.makeCircle(30, V(0, 0, 80))
        self.refused(self.make(open_rect(100, 100, 0), circle), "closed")

    def test_smooth_method(self):
        self.refused(self.make(open_rect(100, 100, 0), open_rect(60, 60, 80), method="smooth"),
                     "smooth")

    def test_spline_profile(self):
        spl = Part.BSplineCurve()
        spl.interpolate([V(-30, -30, 80), V(0, 10, 80), V(30, -30, 80)])
        edge = spl.toShape()
        self.refused(self.make(Part.makePolygon([V(-50, -50, 0), V(0, 0, 0), V(50, -50, 0)]), edge),
                     "straight segments and circular arcs")

    def test_segment_counts_differ(self):
        three = Part.Compound([Part.makeLine(V(-30, -30, 80), V(30, -30, 80)),
                               Part.makeLine(V(30, -30, 80), V(30, 30, 80)),
                               Part.makeLine(V(30, 30, 80), V(-30, 30, 80))])
        self.refused(self.make(open_rect(100, 100, 0), three), "4 segments", "3")

    def test_twisted_alignment_folds_over(self):
        # B reversed against A while the alignment is forced to "same": the strips cross
        a = open_rect(100, 100, 0)
        b = Part.Compound(list(reversed(open_rect(60, 60, 80).Edges)))
        feat = self.make(a, open_rect(60, 60, 80), alignment="reversed")
        self.refused(feat, "folds over")
        self.assertNotIn("Invalid", self.make(a, b, alignment="auto").State)

    # run 028 verifier finding MEDIUM-1: a twisted loft with a self-intersecting mid-surface was
    # accepted at any thickness because the only guard was a prism-overlap limit scaling with T
    def test_twisted_start_angle_is_refused_at_any_thickness(self):
        for start in (315.0, 0.0, 45.0, 90.0, 135.0):
            for t in (0.2, 2.0):
                feat = self.make(open_rect(100, 100, 0), open_round(40, 100, start=start),
                                 thickness=t)
                self.refused(feat, "intersects itself", "start point")
                self.doc.removeObject(feat.Name)

    def test_untwisted_start_angles_are_accepted(self):
        for start in (225.0, 270.0, 180.0):
            for t in (0.2, 2.0):
                feat = self.make(open_rect(100, 100, 0), open_round(40, 100, start=start),
                                 thickness=t)
                self.assertNotIn("Invalid", feat.State, (start, t))
                self.assertTrue(feat.Shape.isValid())

    def test_reversed_alignment_and_clockwise_rectangle_are_refused(self):
        feat = self.make(open_rect(100, 100, 0), open_round(40, 100), thickness=0.2,
                         alignment="reversed")
        self.refused(feat, "intersects itself", "alignment")
        cw = Part.Compound([Part.makeLine(V(x0, y0, 0), V(x1, y1, 0)) for (x0, y0), (x1, y1) in [
            ((-50, 50), (50, 50)), ((50, 50), (50, -50)), ((50, -50), (-50, -50)),
            ((-50, -50), (-50, 48))]])
        feat = self.make(cw, open_round(40, 100), thickness=0.2, alignment="same")
        self.refused(feat, "intersects itself")

    def test_thickness_too_large_inverts_a_facet(self):
        feat = self.make(open_rect(100, 100, 0), open_rect(60, 60, 80), thickness=60.0,
                         side="inside")
        self.refused(feat, "too large")

    def test_ambiguous_inside(self):
        flat_a = Part.Compound([Part.makeLine(V(-50, 0, 0), V(50, 0, 0))])
        flat_b = Part.Compound([Part.makeLine(V(-30, 0, 40), V(30, 0, 40))])
        a = self.doc.addObject("Part::Feature", "FA")
        a.Shape = flat_a
        b = self.doc.addObject("Part::Feature", "FB")
        b.Shape = flat_b
        obj = self.doc.addObject("Part::FeaturePython", "LBf")
        LB.SMLoftedBend(obj, a, b)
        with expected_recompute_errors():
            self.doc.recompute()
        self.refused(obj, "sketch")  # two single lines do not define their planes

    def test_unfold_after_another_feature_is_refused(self):
        feat, _a, _b = make_case(self.doc, CASES[0])
        cut = self.doc.addObject("Part::Feature", "Moved")
        cut.Shape = feat.Shape.copy()
        self.assertFalse(LB.in_chain(cut))
        box = self.doc.addObject("Part::Cut", "Cut")
        tool = self.doc.addObject("Part::Box", "Tool")
        tool.Length = tool.Width = tool.Height = 5
        box.Base, box.Tool = feat, tool
        self.doc.recompute()
        u = self.doc.addObject("Part::FeaturePython", "Unfold")
        UC.SMUnfold(u, box, ["Face1"])
        with expected_recompute_errors():
            self.doc.recompute()
        self.refused(u, "lofted bend")

    def test_default_untouched(self):
        import SheetMetalTools
        self.assertTrue(SheetMetalTools.params.GetBool("UseOldUnfolder", True))


if __name__ == "__main__":
    unittest.main()
