# SPDX-License-Identifier: LGPL-2.1-or-later
"""Sketched bend oracle (cad-fork G07 slice 4a, check 2).

The vendored ``SheetMetalFoldCmd`` (``SMFoldWall``) folds a flat plate along a line drawn in a
sketch on the plate's top face; that is the sketched bend. This suite pins what the Fold does
against the sheet-metal formulas (public: Oberg, Jones, Horton, Ryffel, "Machinery's Handbook",
Industrial Press, bend allowance; Wikipedia, "Bending (metalworking)"):

    BA = theta * (R + K * T)                 flat length consumed by the bend
    V  = (flange lengths) * T * W + theta * T * (R + T / 2) * W

where theta is in radians, R is the inside radius, T the thickness and W the plate width.

Fold convention (read from SheetMetalFoldCmd.smFold; the numbers below confirm it). The plate
is PL long along X, the sketch line crosses it at x = d. The Fold removes a strip of the plate
exactly BA wide, bends a new cylindrical segment into the gap (the ring sector has volume
theta * T * (R + T / 2) * W) and rotates one flange by theta about the bend axis:

* the flange that stays in place is the one on the x < d side (``invertbend`` False) or the
  x > d side (``invertbend`` True); the other flange moves;
* ``Position`` says where the removed strip lies relative to the line: "middle" centred on the
  line, "forward" wholly on the moved side of the line (the line is the fixed flange's end),
  "backward" wholly on the fixed side (the line is the moved flange's end), and
  "intersection of planes" with the line as the mould-line apex of the two flanges on the top
  face: the strip then starts R * tan(theta / 2) before the line, and the Fold ignores its
  ``kfactor`` property and uses K = (R * tan(theta / 2) / (theta / 2) - R) / T;
* so the two flanges are L_fixed and L_moved with L_fixed + L_moved + BA = PL, and the bend
  centreline sits L_fixed + BA / 2 from the fixed end on the flat.

Unfolded through the default unfolder the part is the plate again (PL x W x T) with one bend
line at that position, the bend's angle, inside radius and allowance in ``BendData``.
"""

import math
import os
import tempfile
import unittest

import FreeCAD
import Part
from FreeCAD import Vector as V

import SheetMetalFlatPattern as FP
import SheetMetalFoldCmd
import SheetMetalUnfoldCmd as UC

from SMTests.testOracle import LEN_TOL, WIDTH, build_plate, inner_root_face, measure, unfold_target_face
from SMTests.testUnfoldFeature import feature_unfold

VOL_TOL = 1e-6  # relative, the check-2 bound

# name, T, R/T, K, angle, line position d (mm from x = 0 of an 80 mm plate), Position,
# invertbend, invert (bend the other way)
_ROWS = [
    ("s01_30_T1_R0.5T_mid", 1.0, 0.5, 0.42, 30.0, 40.0, "middle", False, False),
    ("s02_45_T2_R1T_offc", 2.0, 1.0, 0.33, 45.0, 25.0, "middle", False, False),
    ("s03_90_T3_R2T_fwd", 3.0, 2.0, 0.50, 90.0, 50.0, "forward", False, False),
    ("s04_135_T2_R3T_inv", 2.0, 3.0, 0.42, 135.0, 40.0, "middle", True, False),
    ("s05_60_T3_R1T_bwd", 3.0, 1.0, 0.38, 60.0, 20.0, "backward", False, False),
    ("s06_120_T2_R0.5T_flip", 2.0, 0.5, 0.40, 120.0, 55.0, "middle", True, True),
    ("s07_90_T2_R1T_iop", 2.0, 1.0, 0.42, 90.0, 30.0, "intersection of planes", False, False),
    ("s08_135_T1_R3T_fwd_inv", 1.0, 3.0, 0.43, 135.0, 45.0, "forward", True, False),
]
PL = 80.0


def make_cfg(row):
    name, t, r_t, k, ang, d, pos, inv, flip = row
    return dict(name=name, T=t, r_t=r_t, K=k, angle=ang, PL=PL, d=d, Position=pos, invertbend=inv,
                invert=flip)


CASES = [make_cfg(r) for r in _ROWS]


def expected(cfg):
    """Analytic folded part for `cfg`: K used, BA, flange lengths, bend centre, volume."""
    t, r, th = cfg["T"], cfg["r_t"] * cfg["T"], math.radians(cfg["angle"])
    k = cfg["K"]
    iop = cfg["Position"] == "intersection of planes"
    if iop:
        k = (r * math.tan(th / 2.0) / (th / 2.0) - r) / t
    ba = th * (r + k * t)
    sigma = 1.0 if cfg["invertbend"] else -1.0  # side of the line the fixed flange lies on
    d = cfg["d"]
    if cfg["Position"] == "middle":
        fixed_end = d + sigma * ba / 2.0
    elif cfg["Position"] == "forward":
        fixed_end = d
    elif cfg["Position"] == "backward":
        fixed_end = d + sigma * ba
    else:  # the apex is at the line; the strip starts at the tangent line of the inside radius
        r_apex = r + t if cfg["invert"] else r
        fixed_end = d + sigma * r_apex * math.tan(th / 2.0)
    l_fixed = fixed_end if sigma < 0 else PL - fixed_end
    l_moved = PL - ba - l_fixed
    centre_from_fixed = l_fixed + ba / 2.0
    volume = ((l_fixed + l_moved) * t + th * t * (r + t / 2.0)) * WIDTH
    return dict(k=k, ba=ba, l_fixed=l_fixed, l_moved=l_moved, centre=centre_from_fixed,
                volume=volume, radius=r, angle=cfg["angle"])


def build_sketched_bend(doc, cfg):
    """Plate PL x W x T, a sketch line across it at x = d on the top face, vendored Fold.

    Returns (fold, plate)."""
    import Sketcher  # noqa: F401

    plate = build_plate(doc, cfg)
    t, w = cfg["T"], WIDTH
    bl = doc.addObject("Sketcher::SketchObject", "BendLine_" + cfg["name"])
    bl.Placement = FreeCAD.Placement(V(0, 0, t), FreeCAD.Rotation())  # the plate's top face
    bl.addGeometry(Part.LineSegment(V(cfg["d"], -5.0, 0), V(cfg["d"], w + 5.0, 0)))
    doc.recompute()
    faces = plate.Shape.Faces
    top = max((i for i, f in enumerate(faces)
               if f.normalAt(0, 0).z > 0.99 and abs(f.CenterOfMass.z - t) < 1e-6),
              key=lambda i: faces[i].Area)
    fold = doc.addObject("Part::FeaturePython", "Fold_" + cfg["name"])
    SheetMetalFoldCmd.SMFoldWall(fold, plate, ["Face%d" % (top + 1)], bl)
    fold.radius = cfg["r_t"] * t
    fold.angle = cfg["angle"]
    fold.kfactor = cfg["K"]
    fold.Position = cfg["Position"]
    fold.invertbend = cfg["invertbend"]
    fold.invert = cfg["invert"]
    doc.recompute()
    return fold, plate


def outer_root_face(shape):
    """Largest planar face next to the outside (largest-radius) bend cylinder."""
    cyls = [f for f in shape.Faces if f.Surface.TypeId == "Part::GeomCylinder"]
    r_max = max(f.Surface.Radius for f in cyls)
    outer = [f for f in cyls if abs(f.Surface.Radius - r_max) < 1e-9]
    best = None
    for i, f in enumerate(shape.Faces):
        if f.Surface.TypeId == "Part::GeomPlane" and any(f.distToShape(c)[0] < 1e-9 for c in outer):
            if best is None or f.Area > best[0]:
                best = (f.Area, i)
    return "Face%d" % (best[1] + 1)


def fixed_flange_length(shape, plate_length):
    """Length of the flange that did not move, read from the folded solid: the planar faces
    parallel to the plate that reach one end of the plate."""
    spans = []
    for f in shape.Faces:
        bb = f.BoundBox
        if f.Surface.TypeId == "Part::GeomPlane" and bb.ZLength < 1e-9 and (bb.XMin < 1e-9 or bb.XMax > plate_length - 1e-9):
            spans.append(bb.XLength)
    return max(spans)


class TestSketchedBend(unittest.TestCase):
    geo = []  # (case, analytic volume, volume, relative error, fixed flange length error)
    rows = []  # (case, K, BA, flat volume / fold volume - 1, flat length error, bend position error, direction)

    def setUp(self):
        self.doc = FreeCAD.newDocument("SMSketchedBend")
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)
        self.tmp.cleanup()

    def test_there_are_enough_cases(self):
        self.assertGreaterEqual(len(CASES), 6)
        angles = [c["angle"] for c in CASES]
        self.assertTrue(min(angles) <= 30.0 and max(angles) >= 135.0)
        self.assertTrue(any(c["invertbend"] for c in CASES))
        self.assertTrue(any(c["d"] != PL / 2 for c in CASES) and any(c["d"] == PL / 2 for c in CASES))
        self.assertTrue(0.5 <= min(c["r_t"] for c in CASES) and max(c["r_t"] for c in CASES) <= 3.0)
        self.assertTrue(1.0 <= min(c["T"] for c in CASES) and max(c["T"] for c in CASES) <= 3.0)

    def test_folded_volume_flanges_and_bend_geometry(self):
        for cfg in CASES:
            with self.subTest(case=cfg["name"]):
                exp = expected(cfg)
                fold, plate = build_sketched_bend(self.doc, cfg)
                shape = fold.Shape
                self.assertTrue(shape.isValid() and shape.isClosed(), cfg["name"])
                self.assertEqual(len(shape.Solids), 1)
                rel = shape.Volume / exp["volume"] - 1.0
                self.assertAlmostEqual(rel, 0.0, delta=VOL_TOL, msg="folded volume")
                lf = fixed_flange_length(shape, PL)
                TestSketchedBend.geo.append((cfg["name"], exp["volume"], shape.Volume, rel, lf - exp["l_fixed"]))
                self.assertAlmostEqual(lf, exp["l_fixed"], delta=1e-6, msg="fixed flange length")
                cyl = [f for f in shape.Faces if f.Surface.TypeId == "Part::GeomCylinder"]
                self.assertEqual(len(cyl), 2)
                self.assertAlmostEqual(min(f.Surface.Radius for f in cyl), exp["radius"], delta=1e-6)
                self.assertAlmostEqual(max(f.Surface.Radius for f in cyl), exp["radius"] + cfg["T"], delta=1e-6)

    def unfold(self, fold, k):
        face = inner_root_face(fold.Shape)
        return feature_unfold(self.doc, fold, face, material="_manual", k=k, old=None)

    def test_unfolds_to_the_plate_with_one_bend_line(self):
        for cfg in CASES:
            with self.subTest(case=cfg["name"]):
                exp = expected(cfg)
                fold, _plate = build_sketched_bend(self.doc, cfg)
                feat = self.unfold(fold, exp["k"])
                flat = feat.Shape
                self.assertTrue(flat.isValid())
                _len_vol, flat_len, vol = measure(flat, cfg["T"], WIDTH)
                self.assertAlmostEqual(flat_len, PL, delta=LEN_TOL, msg="flat length (outline)")
                self.assertAlmostEqual(_len_vol, PL, delta=LEN_TOL, msg="flat volume / (T * W)")
                bends = UC.get_bend_lines(feat)
                self.assertEqual(len(bends), 1, "one bend")
                b = bends[0]
                self.assertAlmostEqual(b.angle, exp["angle"], delta=1e-6)
                self.assertAlmostEqual(b.inside_radius, exp["radius"], delta=1e-6)
                self.assertAlmostEqual(b.allowance, exp["ba"], delta=1e-6)
                self.assertAlmostEqual(b.k_factor, exp["k"], delta=1e-9)
                # the bend centreline sits L_fixed + BA / 2 from one end of the flat and
                # L_moved + BA / 2 from the other
                pts = [p for seg in b.segments for p in seg]
                ends = [e.CenterOfMass for e in flat.Faces if abs(e.Area - cfg["T"] * WIDTH) < 1e-6]
                self.assertEqual(len(ends), 2, "the two end faces of the strip")
                axis = (ends[1] - ends[0])
                axis.normalize()
                mid = pts[0]
                d0 = abs((mid - ends[0]).dot(axis))
                d1 = abs((mid - ends[1]).dot(axis))
                want = sorted([exp["l_fixed"] + exp["ba"] / 2.0, exp["l_moved"] + exp["ba"] / 2.0])
                self.assertAlmostEqual(sorted([d0, d1])[0], want[0], delta=LEN_TOL, msg="bend position")
                self.assertAlmostEqual(sorted([d0, d1])[1], want[1], delta=LEN_TOL, msg="bend position")
                TestSketchedBend.rows.append(
                    (cfg["name"], exp["k"], exp["ba"], vol / exp["volume"] - 1.0, flat_len - PL,
                     sorted([d0, d1])[0] - want[0], b.direction))

    def test_bend_direction_and_side_follow_invert(self):
        """Which side the bend is on follows `invert`: the inside radius is on the top face
        (axis above the plate) normally and on the bottom face when inverted. The bend line's
        UP / DOWN is relative to the root face chosen: inner root face gives UP, outer DOWN."""
        for cfg in CASES:
            with self.subTest(case=cfg["name"]):
                exp = expected(cfg)
                fold, _plate = build_sketched_bend(self.doc, cfg)
                inner = min((f for f in fold.Shape.Faces if f.Surface.TypeId == "Part::GeomCylinder"),
                            key=lambda f: f.Surface.Radius)
                axis_z = inner.Surface.Center.z
                want_z = -exp["radius"] if cfg["invert"] else cfg["T"] + exp["radius"]
                self.assertAlmostEqual(axis_z, want_z, delta=1e-6, msg="bend axis side")
                dirs = {}
                faces = [("inner", inner_root_face(fold.Shape))]
                if cfg["angle"] != 60.0:
                    # Known V1 unfolder limitation (run 026 note, "partial flat on some 60-degree
                    # shapes"): from the outer root face a 60 degree bend is not recorded
                    # ("recorded 0 of the part's 1 bends"; the same happens on a base flange of the
                    # same dimensions, so it is not the Fold). The 60 degree case is checked from
                    # the inner face only.
                    faces.append(("outer", outer_root_face(fold.Shape)))
                for label, face in faces:
                    feat = feature_unfold(self.doc, fold, face, material="_manual", k=exp["k"], old=None)
                    dirs[label] = UC.get_bend_lines(feat)[0].direction
                self.assertEqual(dirs, {"inner": "UP", "outer": "DOWN"} if len(dirs) == 2 else {"inner": "UP"})

    def test_dxf_bend_line_sits_at_the_developed_length_position(self):
        from SMTests import testFlatDxf as T

        for cfg in CASES:
            with self.subTest(case=cfg["name"]):
                exp = expected(cfg)
                fold, _plate = build_sketched_bend(self.doc, cfg)
                feat = self.unfold(fold, exp["k"])
                path = os.path.join(self.tmp.name, cfg["name"] + ".dxf")
                UC.export_flat_dxf(feat, path)
                ents = FP.read_dxf_entities(path)
                lines, outline = T.bend_positions(ents)
                self.assertEqual(len(lines), 1)
                d, n = T.unit(*lines[0])
                self.assertAlmostEqual(n, WIDTH, delta=LEN_TOL, msg="bend line spans the width")
                pts = [p for seg in outline for p in seg]
                lo = min(T.perp_coordinate(p, d) for p in pts)
                hi = max(T.perp_coordinate(p, d) for p in pts)
                self.assertAlmostEqual(hi - lo, PL, delta=LEN_TOL, msg="outline length")
                pos = T.perp_coordinate(lines[0][0], d)
                want = sorted([exp["l_fixed"] + exp["ba"] / 2.0, exp["l_moved"] + exp["ba"] / 2.0])
                got = sorted([pos - lo, hi - pos])
                self.assertAlmostEqual(got[0], want[0], delta=LEN_TOL)
                self.assertAlmostEqual(got[1], want[1], delta=LEN_TOL)

    @classmethod
    def tearDownClass(cls):
        if os.environ.get("SM_ORACLE_TABLE"):
            import sys

            for r in cls.geo:
                sys.stderr.write("SKETCHED_BEND_GEO %s\n" % (r,))
            for r in cls.rows:
                sys.stderr.write("SKETCHED_BEND %s\n" % (r,))


if __name__ == "__main__":
    unittest.main()
