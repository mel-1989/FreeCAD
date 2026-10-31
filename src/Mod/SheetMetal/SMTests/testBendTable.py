# SPDX-License-Identifier: LGPL-2.1-or-later
"""Bend allowance / bend deduction tables (cad-fork G07 slice 3a, check 2).

Formulas (public: Machinery's Handbook, sheet-metal bend allowance; Wikipedia, "Bending
(metalworking)"): BA = pi/180 * theta * (R + K * T), OSSB = tan(theta / 2) * (R + T),
BD = 2 * OSSB - BA. The shipped tables are generated from these with the K-factor rule named in
their headers; the tests recompute every row.
"""

import csv
import math
import os
import tempfile
import unittest

import FreeCAD

import SheetMetalBendTable as BT
import SheetMetalNewUnfolder
import SheetMetalUnfolder
from SMTests.testOracle import HAVE_NEW, LEN_TOL, WIDTH, Case, build_base, measure, unfold_target_face

BA_FILE = os.path.join(BT.TABLE_DIR, "mild_steel_ba.csv")
BD_FILE = os.path.join(BT.TABLE_DIR, "mild_steel_gauge_bd.csv")


def rule_k(radius, thickness):
    ratio = radius / thickness
    return 0.38 if ratio < 1 else (0.43 if ratio <= 3 else 0.50)


def all_rows(table):
    for m in table.materials:
        for t in table.thicknesses(m):
            _radii, _angles, cell = table._grids[m][t]
            for (r, a), v in sorted(cell.items()):
                yield m, t, r, a, v


def write_table(rows, column=BT.BA_COLUMN, comments=("test table",)):
    fd, path = tempfile.mkstemp(suffix=".csv", prefix="smbt")
    with os.fdopen(fd, "w", newline="") as fh:
        for c in comments:
            fh.write("# %s\n" % c)
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(BT.KEY_COLUMNS + [column])
        w.writerows(rows)
    return path


class TestBendTable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ba = BT.BendTable.from_file(BA_FILE)
        cls.bd = BT.BendTable.from_file(BD_FILE)

    def test_both_kinds_ship_and_cite_their_source(self):
        self.assertEqual(self.ba.kind, "BA")
        self.assertEqual(self.bd.kind, "BD")
        for table in (self.ba, self.bd):
            text = " ".join(table.comments)
            self.assertIn("Machinery's Handbook", text)
            self.assertIn("BA = pi/180", text)
        self.assertGreaterEqual(sum(1 for _ in all_rows(self.ba)), 200)

    def test_shipped_tables_match_the_formula(self):
        worst = 0.0
        for m, t, r, a, v in all_rows(self.ba):
            worst = max(worst, abs(v - BT.bend_allowance_from_k(t, r, a, rule_k(r, t))))
        self.assertLess(worst, 1e-6)  # rounding of the stored 6 decimals
        worst = 0.0
        for m, t, r, a, v in all_rows(self.bd):
            ba = BT.bend_allowance_from_k(t, r, a, rule_k(r, t))
            worst = max(worst, abs(v - BT.ba_to_bd(ba, t, r, a)))
        self.assertLess(worst, 1e-6)
        # hand check: T = 2, R = 2 (R/T = 1 so K = .43), 90 deg: BA = pi/2 * 2.66, OSSB = 4, BD = 8 - BA
        self.assertAlmostEqual(self.ba.allowance(2.0, 2.0, 90.0), math.pi / 2 * 2.86, 5)
        self.assertAlmostEqual(self.ba.deduction(2.0, 2.0, 90.0), 8.0 - math.pi / 2 * 2.86, 5)

    def test_exact_at_listed_points(self):
        for table in (self.ba, self.bd):
            for m, t, r, a, v in all_rows(table):
                self.assertEqual(table.value(t, r, a, m), v)

    def test_linear_interpolation_between_listed_points(self):
        t, ra, rb, a_lo, a_hi = 2.0, 2.0, 3.0, 45.0, 60.0
        v = self.ba.value
        # radius midpoint, angle on the grid
        self.assertAlmostEqual(v(t, 2.5, 45.0), 0.5 * (v(t, ra, 45.0) + v(t, rb, 45.0)), 12)
        self.assertAlmostEqual(v(t, 2.25, 45.0), 0.75 * v(t, ra, 45.0) + 0.25 * v(t, rb, 45.0), 12)
        # angle quarter point, radius on the grid
        self.assertAlmostEqual(v(t, 2.0, 48.75), 0.75 * v(t, 2.0, a_lo) + 0.25 * v(t, 2.0, a_hi), 12)
        # bilinear
        corners = [v(t, r, a) for r in (ra, rb) for a in (a_lo, a_hi)]
        self.assertAlmostEqual(v(t, 2.5, 52.5), sum(corners) / 4.0, 12)
        # thickness between 1.5 and 2.0
        self.assertAlmostEqual(v(1.75, 2.0, 90.0), 0.5 * (v(1.5, 2.0, 90.0) + v(2.0, 2.0, 90.0)), 12)
        self.assertAlmostEqual(v(1.6, 2.0, 90.0), 0.8 * v(1.5, 2.0, 90.0) + 0.2 * v(2.0, 2.0, 90.0), 12)
        # a single-K stretch is exactly the formula, linear in theta: R/T < 1 -> K .38 for T = 3, R = 1 and 1.5
        t3 = 3.0
        want = BT.bend_allowance_from_k(t3, 1.25, 52.5, 0.38)
        self.assertAlmostEqual(self.ba.allowance(t3, 1.25, 52.5), want, 5)

    def test_interpolation_of_a_bd_table_is_in_bd(self):
        """The stored quantity is the one interpolated; the BA a BD table gives follows from it."""
        t = 1.519
        mid = 0.5 * (self.bd.value(t, 2.0, 90.0) + self.bd.value(t, 3.0, 90.0))
        self.assertAlmostEqual(self.bd.value(t, 2.5, 90.0), mid, 12)
        self.assertAlmostEqual(self.bd.deduction(t, 2.5, 90.0), mid, 12)
        self.assertAlmostEqual(self.bd.allowance(t, 2.5, 90.0), BT.bd_to_ba(mid, t, 2.5, 90.0), 12)

    def test_out_of_range_is_a_readable_error_never_extrapolated(self):
        cases = [
            ((0.9, 2.0, 90.0), "thickness"),
            ((3.1, 2.0, 90.0), "thickness"),
            ((2.0, 0.4, 90.0), "inside radius"),
            ((2.0, 8.5, 90.0), "inside radius"),
            ((2.0, 2.0, 10.0), "bend angle"),
            ((2.0, 2.0, 160.0), "bend angle"),
        ]
        for args, word in cases:
            with self.subTest(args=args):
                with self.assertRaises(BT.BendTableRangeError) as cm:
                    self.ba.allowance(*args)
                msg = str(cm.exception)
                self.assertIn(word, msg)
                self.assertIn("outside the table range", msg)
                self.assertIn("not extrapolated", msg)
        self.assertTrue(issubclass(BT.BendTableRangeError, ValueError))

    def test_ba_bd_round_trip_to_1e_9(self):
        as_bd = self.ba.as_kind("BD")
        self.assertEqual(as_bd.kind, "BD")
        back = as_bd.as_kind("BA")
        worst = 0.0
        for (m, t, r, a, v), (m2, t2, r2, a2, v2) in zip(all_rows(self.ba), all_rows(back)):
            self.assertEqual((m, t, r, a), (m2, t2, r2, a2))
            worst = max(worst, abs(v - v2))
        self.assertLess(worst, 1e-9)
        # and BD -> BA -> BD on the shipped gauge table
        worst = 0.0
        for (_m, _t, _r, _a, v), (_m2, _t2, _r2, _a2, v2) in zip(
            all_rows(self.bd), all_rows(self.bd.as_kind("BA").as_kind("BD"))
        ):
            worst = max(worst, abs(v - v2))
        self.assertLess(worst, 1e-9)
        # BD = 2 * OSSB - BA at every listed point of the BA table, through the lookup API
        for m, t, r, a, v in all_rows(self.ba):
            ossb = math.tan(math.radians(a) / 2.0) * (r + t)
            self.assertAlmostEqual(self.ba.deduction(t, r, a, m), 2.0 * ossb - v, 9)
            self.assertAlmostEqual(as_bd.value(t, r, a, m), 2.0 * ossb - v, 9)

    def test_csv_round_trip_through_a_file(self):
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        try:
            self.bd.to_csv(path)
            again = BT.BendTable.from_file(path)
        finally:
            os.remove(path)
        self.assertEqual(list(all_rows(again)), list(all_rows(self.bd)))
        self.assertEqual(again.gauges("mild_steel"), self.bd.gauges("mild_steel"))

    def test_gauge_keys(self):
        self.assertEqual(self.bd.gauges("mild_steel")["16ga"], 1.519)
        self.assertEqual(self.bd.thickness_of_gauge(None, "11ga"), 3.038)
        lk = self.bd.lookup("mild_steel", "16ga")
        self.assertEqual(lk.deduction(1.519, 2.0, 90.0), self.bd.value(1.519, 2.0, 90.0))
        with self.assertRaises(BT.BendTableRangeError) as cm:  # gauge says 1.519, part is 2 mm
            lk.allowance(2.0, 2.0, 90.0)
        self.assertIn("16ga", str(cm.exception))
        with self.assertRaises(BT.BendTableError) as cm:
            self.bd.thickness_of_gauge("mild_steel", "99ga")
        self.assertIn("18ga", str(cm.exception))

    def test_material_errors_are_readable(self):
        with self.assertRaises(BT.BendTableError) as cm:
            self.ba.allowance(2.0, 2.0, 90.0, material="unobtainium")
        self.assertIn("mild_steel", str(cm.exception))
        rows = [("a", "", 1.0, 1.0, 90.0, 2.0), ("b", "", 1.0, 1.0, 90.0, 2.5)]
        path = write_table(rows)
        try:
            multi = BT.BendTable.from_file(path)
        finally:
            os.remove(path)
        with self.assertRaises(BT.BendTableError) as cm:
            multi.allowance(1.0, 1.0, 90.0)
        self.assertIn("name one", str(cm.exception))
        self.assertEqual(multi.allowance(1.0, 1.0, 90.0, "b"), 2.5)

    def test_malformed_files_are_refused(self):
        bad = {
            "not a full grid": [("m", "", 1.0, 1.0, 90.0, 2.0), ("m", "", 1.0, 2.0, 90.0, 2.0),
                                ("m", "", 1.0, 1.0, 45.0, 1.0)],
            "duplicate row": [("m", "", 1.0, 1.0, 90.0, 2.0), ("m", "", 1.0, 1.0, 90.0, 2.1)],
            "impossible angle": [("m", "", 1.0, 1.0, 0.0, 0.0)],
        }
        for label, rows in bad.items():
            path = write_table(rows)
            try:
                with self.subTest(label), self.assertRaises(BT.BendTableError):
                    BT.BendTable.from_file(path)
            finally:
                os.remove(path)
        path = write_table([("m", "", 1.0, 1.0, 90.0, 2.0)], column="bend_thing_mm")
        try:
            with self.assertRaises(BT.BendTableError) as cm:
                BT.BendTable.from_file(path)
            self.assertIn("bend_allowance_mm", str(cm.exception))
        finally:
            os.remove(path)

    def test_references(self):
        refs = BT.list_refs()
        self.assertIn("bt_mild_steel_ba/mild_steel", refs)
        self.assertIn("bt_mild_steel_gauge_bd/mild_steel", refs)
        self.assertEqual(BT.parse_ref("bt_x"), ("x", None, None))
        self.assertEqual(BT.parse_ref("bt_x/steel/16ga"), ("x", "steel", "16ga"))
        lk = BT.lookup_from_ref("bt_mild_steel_ba")
        self.assertEqual(lk.allowance(2.0, 2.0, 90.0), self.ba.allowance(2.0, 2.0, 90.0))
        with self.assertRaises(BT.BendTableError) as cm:
            BT.lookup_from_ref("bt_missing")
        self.assertIn("mild_steel_ba", str(cm.exception))
        with self.assertRaises(BT.BendTableError):
            BT.parse_ref("csv_mild_steel")

    def test_equivalent_k_factor_reproduces_the_allowance(self):
        for m, t, r, a, v in list(all_rows(self.ba))[::17]:
            k = self.ba.equivalent_k_factor(t, r, a, m)
            self.assertAlmostEqual(BT.bend_allowance_from_k(t, r, a, k), v, 9)
            self.assertAlmostEqual(k, rule_k(r, t), 4)  # recovers the rule's K at the listed points


# Parts for the unfold tests: (name, T, R, segment lengths, angles). All inside the tables' ranges;
# the second sits between listed radii and angles (interpolated), the third is a two-bend part.
UNFOLD_PARTS = [
    ("t1_listed_90", 2.0, 2.0, [40.0, 25.0], [90.0]),
    ("t2_interp_75", 1.5, 2.5, [35.0, 30.0], [75.0]),
    ("t3_two_bends", 1.0, 1.5, [25.0, 40.0, 25.0], [90.0, 45.0]),
]
GAUGE_PARTS = [
    ("g1_16ga_90", 1.519, 2.0, [40.0, 25.0], [90.0]),
    ("g2_11ga_two", 3.038, 4.0, [60.0, 70.0, 60.0], [90.0, 60.0]),
]


class TestBendTableUnfold(unittest.TestCase):
    """An unfold driven by a table is as long as the flanges plus the table's allowances."""

    def setUp(self):
        self.doc = FreeCAD.newDocument("SMBendTable")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    @staticmethod
    def _case(name, t, r, segs, angles):
        return Case(name, t, r / t, 0.5, segs, angles)

    def _check(self, lookup, parts, unfolders=("old", "new")):
        for name, t, r, segs, angles in parts:
            case = self._case(name, t, r, segs, angles)
            expected = case.straight_total() + sum(lookup.allowance(t, r, a) for a in angles)
            base = build_base(self.doc, case)
            for which in unfolders:
                with self.subTest(part=name, unfolder=which):
                    if which == "old":
                        flat = SheetMetalUnfolder.getUnfold(lookup, base, unfold_target_face(base.Shape), "ansi")[0]
                    else:
                        bac = SheetMetalNewUnfolder.BendAllowanceCalculator.from_bend_table(lookup)
                        flat = SheetMetalNewUnfolder.getUnfold(bac, base, unfold_target_face(base.Shape))[1]
                    self.assertIsNotNone(flat)
                    len_from_vol, flat_len, _vol = measure(flat, t, WIDTH)
                    self.assertAlmostEqual(flat_len, expected, delta=LEN_TOL)
                    self.assertAlmostEqual(len_from_vol, expected, delta=LEN_TOL)

    def test_ba_table_old_unfolder(self):
        self._check(BT.lookup_from_ref("bt_mild_steel_ba"), UNFOLD_PARTS, ("old",))

    @unittest.skipUnless(HAVE_NEW, "networkx is not installed; the new unfolder needs it")
    def test_ba_table_new_unfolder(self):
        self._check(BT.lookup_from_ref("bt_mild_steel_ba"), UNFOLD_PARTS, ("new",))

    def test_bd_gauge_table_old_unfolder(self):
        self._check(BT.lookup_from_ref("bt_mild_steel_gauge_bd/mild_steel/16ga"), GAUGE_PARTS[:1], ("old",))
        self._check(BT.lookup_from_ref("bt_mild_steel_gauge_bd"), GAUGE_PARTS, ("old",))

    @unittest.skipUnless(HAVE_NEW, "networkx is not installed; the new unfolder needs it")
    def test_bd_gauge_table_new_unfolder(self):
        self._check(BT.lookup_from_ref("bt_mild_steel_gauge_bd"), GAUGE_PARTS, ("new",))

    def test_part_names_a_table_instead_of_a_k_factor(self):
        """The Unfold feature's MaterialSheet "bt_<table>" routes to the table in both unfolders."""
        import types

        try:
            import SheetMetalUnfoldCmd
        except ImportError as exc:  # pragma: no cover
            self.skipTest("SheetMetalUnfoldCmd is not importable here: %s" % exc)
        name, t, r, segs, angles = UNFOLD_PARTS[1]
        case = self._case(name, t, r, segs, angles)
        base = build_base(self.doc, case)
        face = unfold_target_face(base.Shape)
        lookup = BT.lookup_from_ref("bt_mild_steel_ba")
        expected = case.straight_total() + lookup.allowance(t, r, angles[0])
        obj = types.SimpleNamespace(MaterialSheet="bt_mild_steel_ba", KFactor=0.9, KFactorStandard="ansi",
                                    GenerateSketch=False)  # KFactor 0.9 would be far off if used
        fns = ["oldUnfolder"] + (["newUnfolder"] if HAVE_NEW else [])
        for fn in fns:
            with self.subTest(unfolder=fn):
                flat = getattr(SheetMetalUnfoldCmd.SMUnfold, fn)(None, obj, base, face)[0]
                self.assertAlmostEqual(measure(flat, t, WIDTH)[1], expected, delta=LEN_TOL)

    def test_out_of_range_part_is_a_readable_error_in_both_unfolders(self):
        lookup = BT.lookup_from_ref("bt_mild_steel_ba")
        case = self._case("oor", 5.0, 5.0, [40.0, 40.0], [90.0])  # 5 mm is thicker than the table
        base = build_base(self.doc, case)
        with self.assertRaises(BT.BendTableRangeError) as cm:
            SheetMetalUnfolder.getUnfold(lookup, base, unfold_target_face(base.Shape), "ansi")
        self.assertIn("outside the table range", str(cm.exception))
        if HAVE_NEW:
            bac = SheetMetalNewUnfolder.BendAllowanceCalculator.from_bend_table(lookup)
            face = unfold_target_face(base.Shape)
            with self.assertRaises(BT.BendTableRangeError):
                SheetMetalNewUnfolder.getUnfold(bac, base, face)


if __name__ == "__main__":
    unittest.main()
