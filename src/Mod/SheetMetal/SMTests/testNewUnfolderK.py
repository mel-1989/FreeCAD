# SPDX-License-Identifier: LGPL-2.1-or-later
"""K-factor lookup of the new unfolder (cad-fork G07 slice 3a).

Two defects of the vendored SheetMetalNewUnfolder.BendAllowanceCalculator are pinned here:

* the interpolation loop of get_k_factor was inverted (a table at R/T = 3 gave K 0.51 against
  the correct 0.4208);
* the radius reaching the lookup was the radius of the cylindrical face next to the chosen root
  face, which is the OUTER radius when the root face is on the outside of the bends, so the
  table was read at the wrong R/T. The lookup now takes the bend's inside radius.

Formula and sources as in testOracle.py (Machinery's Handbook bend allowance).
"""

import unittest

import FreeCAD

import SheetMetalNewUnfolder as NU
from SMTests.testOracle import (  # noqa: F401
    BASE_CASES, HAVE_NEW, LEN_TOL, WIDTH, build_base, inner_root_face, make_k_sheet, measure,
)

BD = NU.BendDirection


def make_bac(rts, ks, standard="ansi"):
    bac = NU.BendAllowanceCalculator()
    bac.k_factor_standard = NU.BendAllowanceCalculator.KFactorStandard[standard.upper()]
    bac.radius_thickness_values = list(rts)
    bac.k_factor_values = list(ks)
    return bac


class TestGetKFactor(unittest.TestCase):
    """Pure arithmetic: no document, no networkx needed to build the calculator."""

    def test_interpolation_between_late_knots(self):
        bac = make_bac([1.0, 2.0, 99.0], [0.33, 0.42, 0.5])
        # the run 025 defect: R/T = 3 -> 0.51 (inverted loop); the line from (2, .42) to (99, .5) says:
        self.assertAlmostEqual(bac.get_k_factor(3.0, 1.0), 0.42 + 0.08 / 97.0, 12)
        self.assertAlmostEqual(bac.get_k_factor(1.5, 1.0), 0.375, 12)
        self.assertAlmostEqual(bac.get_k_factor(50.5, 1.0), 0.42 + 0.08 * 48.5 / 97.0, 12)

    def test_every_segment_of_a_longer_table(self):
        rts = [0.5, 1.0, 2.0, 4.0, 8.0]
        ks = [0.30, 0.34, 0.40, 0.46, 0.50]
        bac = make_bac(rts, ks)
        for i in range(len(rts) - 1):
            mid = 0.5 * (rts[i] + rts[i + 1])
            self.assertAlmostEqual(bac.get_k_factor(mid, 1.0), 0.5 * (ks[i] + ks[i + 1]), 12)
            q = rts[i] + 0.25 * (rts[i + 1] - rts[i])
            self.assertAlmostEqual(bac.get_k_factor(q, 1.0), ks[i] + 0.25 * (ks[i + 1] - ks[i]), 12)

    def test_knots_and_clamping(self):
        rts = [0.5, 1.0, 2.0, 4.0, 8.0]
        ks = [0.30, 0.34, 0.40, 0.46, 0.50]
        bac = make_bac(rts, ks)
        for rt, k in zip(rts, ks):
            self.assertAlmostEqual(bac.get_k_factor(rt * 2.0, 2.0), k, 12)  # R/T uses the ratio
        self.assertEqual(bac.get_k_factor(0.01, 1.0), 0.30)
        self.assertEqual(bac.get_k_factor(100.0, 1.0), 0.50)

    def test_single_value_and_din(self):
        bac = NU.BendAllowanceCalculator.from_single_value(0.4, "ansi")
        self.assertEqual(bac.get_k_factor(7.0, 2.0), 0.4)
        din = make_bac([1.0, 2.0], [0.66, 0.84], "din")
        self.assertAlmostEqual(din.get_k_factor(1.5, 1.0), 0.375, 12)  # halved to ANSI

    def test_inside_radius_from_face_radius(self):
        """UP: the face is the inner surface; DOWN: the outer one, so subtract T."""
        self.assertEqual(NU.BendAllowanceCalculator.inside_radius(BD.UP, 3.0, 2.0), 3.0)
        self.assertEqual(NU.BendAllowanceCalculator.inside_radius(BD.DOWN, 5.0, 2.0), 3.0)

    def test_allowance_is_the_same_from_either_surface(self):
        """BA = (R_in + K * T) * theta whichever surface's radius the unfolder was handed."""
        bac = make_bac([1.0, 2.0, 99.0], [0.33, 0.42, 0.5])
        t, r_in, theta = 2.0, 6.0, 2.0
        up = bac.get_bend_allowance(BD.UP, r_in, t, theta)
        down = bac.get_bend_allowance(BD.DOWN, r_in + t, t, theta)
        self.assertAlmostEqual(up, down, 12)
        k = bac.get_k_factor(r_in, t)
        self.assertAlmostEqual(up, (r_in + k * t) * theta, 12)


def outer_root_face(shape):
    """Largest planar face on the outside of the bends (adjacent to the largest-radius cylinders)."""
    cyls = [f for f in shape.Faces if f.Surface.TypeId == "Part::GeomCylinder"]
    r_max = max(f.Surface.Radius for f in cyls)
    outer = [f for f in cyls if abs(f.Surface.Radius - r_max) < 1e-9]
    best = None
    for i, f in enumerate(shape.Faces):
        if f.Surface.TypeId == "Part::GeomPlane" and any(f.distToShape(c)[0] < 1e-9 for c in outer):
            if best is None or f.Area > best[0]:
                best = (f.Area, i)
    return "Face%d" % (best[1] + 1)


@unittest.skipUnless(HAVE_NEW, "networkx is not installed; the new unfolder needs it")
class TestRootFaceIndependence(unittest.TestCase):
    """A table lookup must give the same flat length whichever face the user picks as root."""

    def setUp(self):
        self.doc = FreeCAD.newDocument("SMNewK")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def _flat(self, base, face, sheet):
        bac = NU.BendAllowanceCalculator.from_spreadsheet(sheet)
        return NU.getUnfold(bac, base, face)[1]

    def test_outer_root_face_reads_the_inside_radius(self):
        by_name = {c.name: c for c in BASE_CASES}
        for name in ("b12_table_90x2_T2_R1T", "b13_table_135_T3_R3T", "b14_table_45_T1_R1.5T"):
            case = by_name[name]
            with self.subTest(case=name):
                base = build_base(self.doc, case)
                sheet = make_k_sheet(self.doc, "KT_" + name, case.lookup)
                expected = case.developed_length("interp")
                face = outer_root_face(base.Shape)
                flat = self._flat(base, face, sheet)
                _from_vol, flat_len, _vol = measure(flat, case.T, WIDTH)
                self.assertAlmostEqual(flat_len, expected, delta=LEN_TOL, msg="outer root " + name)
                flat_in = self._flat(base, inner_root_face(base.Shape), sheet)
                _from_vol, flat_len_in, _vol = measure(flat_in, case.T, WIDTH)
                self.assertAlmostEqual(flat_len_in, expected, delta=LEN_TOL, msg="inner root " + name)
                self.assertAlmostEqual(flat_len, flat_len_in, delta=1e-6)


if __name__ == "__main__":
    unittest.main()
