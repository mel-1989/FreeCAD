# SPDX-License-Identifier: LGPL-2.1-or-later
"""Bend lines and DXF export of the flat pattern (cad-fork run 026, G07 slice 3b, checks 3 to 5).

Check 3: the Unfold feature exposes one bend line per bend (direction, angle, inside radius,
allowance used) and a bend note; check 4: DXF R12 export on named layers, deterministic, compared
entity by entity with the goldens in ``fixtures/flat_dxf/`` (type, layer and coordinates to 1e-6
mm, not byte by byte) and against the developed-length formula (the bend centreline lies at
flange length + BA / 2 from the edge, BA = theta * (R + K * T), within 1e-3 mm). The DXF is read
back with a small R12 group-code parser (``SheetMetalFlatPattern.read_dxf_entities``).
Regenerate the goldens with ``flat_dxf_cases.regenerate()``.
"""

import math
import os
import tempfile
import unittest

import FreeCAD

import SheetMetalFlatPattern as FP
import SheetMetalTools
import SheetMetalUnfoldCmd as UC
from SMTests import flat_dxf_cases as C
from SMTests.testOracle import HAVE_NEW, LEN_TOL, Case, build_base, unfold_target_face
from SMTests.testUnfoldFeature import feature_unfold

GOLDEN_TOL = 1e-6
LAYER_NAMES = {"OUTLINE", "INNER", "BEND_UP", "BEND_DOWN", "NOTES"}


def entities(path):
    return FP.read_dxf_entities(path)


def geometry(ent):
    """Numeric tuple of an entity (group codes sorted), for matching."""
    return tuple(v for k, v in sorted(ent["codes"].items()) if not isinstance(v, str))


def strings(ent):
    return tuple(v for k, v in sorted(ent["codes"].items()) if isinstance(v, str))


def line_ends(ent):
    c = ent["codes"]
    return (c[10], c[20]), (c[11], c[21])


def compare_entities(golden, actual):
    """Entity-by-entity comparison. Returns (max coordinate deviation, list of problems)."""
    problems, worst = [], 0.0
    if len(golden) != len(actual):
        problems.append("entity count %d != %d" % (len(actual), len(golden)))
        return worst, problems
    pool = list(actual)
    for g in golden:
        best = None
        for i, a in enumerate(pool):
            if a["type"] != g["type"] or a["layer"] != g["layer"] or strings(a) != strings(g):
                continue
            ga, aa = geometry(g), geometry(a)
            if len(ga) != len(aa):
                continue
            dev = max([abs(x - y) for x, y in zip(ga, aa)] or [0.0])
            if g["type"] == "LINE":  # either direction
                (x0, y0), (x1, y1) = line_ends(a)
                swapped = max(abs(g["codes"][10] - x1), abs(g["codes"][20] - y1),
                              abs(g["codes"][11] - x0), abs(g["codes"][21] - y0))
                dev = min(dev, swapped)
            if best is None or dev < best[0]:
                best = (dev, i)
        if best is None or best[0] > GOLDEN_TOL:
            problems.append("no match within %g for %s %s %s (closest %s)"
                            % (GOLDEN_TOL, g["type"], g["layer"], geometry(g),
                               None if best is None else best[0]))
            if best is not None:
                pool.pop(best[1])
            continue
        worst = max(worst, best[0])
        pool.pop(best[1])
    return worst, problems


def bend_positions(ents):
    """Bend centrelines as (direction vector, perpendicular coordinate list, per-line length)
    plus the outline's extent along that perpendicular, for parallel bends."""
    lines = [line_ends(e) for e in ents if e["layer"] in ("BEND_UP", "BEND_DOWN")]
    outline = [line_ends(e) for e in ents if e["layer"] == "OUTLINE" and e["type"] == "LINE"]
    return lines, outline


def perp_coordinate(point, direction):
    return -point[0] * direction[1] + point[1] * direction[0]


def unit(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    return (dx / n, dy / n), n


class Doc(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("SMFlatDxf")
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)
        self.tmp.cleanup()

    def path(self, name):
        return os.path.join(self.tmp.name, name)


class TestBendLines(Doc):
    """Check 3."""

    def test_every_case_exposes_its_bends(self):
        for case in C.all_cases():
            with self.subTest(case=case.name):
                doc = FreeCAD.newDocument("BL" + case.name[:3])
                try:
                    feat, exp = C.build_flat(doc, case)
                    bends = UC.get_bend_lines(feat)
                    self.assertEqual(len(bends), len(exp["ba"]), "one bend line per bend")
                    self.assertEqual(sorted(round(b.angle, 6) for b in bends),
                                     sorted(round(a, 6) for a in exp["angles"]))
                    for b in bends:
                        self.assertIn(b.direction, ("UP", "DOWN"))
                        self.assertAlmostEqual(b.inside_radius, exp["radius"], delta=1e-9)
                        self.assertGreater(len(b.segments), 0)
                        # the allowance used is the one the oracle uses, and BA = theta (R + K T)
                        self.assertTrue(any(abs(b.allowance - ba) < 1e-6 for ba in exp["ba"]),
                                        "%s: %g not in %s" % (b, b.allowance, exp["ba"]))
                        self.assertAlmostEqual(b.allowance, math.radians(b.angle) * (
                            b.inside_radius + b.k_factor * b.thickness), delta=1e-9)
                    self.assertEqual(list(feat.BendNotes), [b.note for b in bends])
                    for b in bends:
                        self.assertEqual(b.note, "%s %g° R%g BA %.3f" % (
                            b.direction, round(b.angle, 2), round(b.inside_radius, 3), b.allowance))
                finally:
                    FreeCAD.closeDocument(doc.Name)

    def test_note_text(self):
        self.assertEqual(FP.bend_note("up", 90.0, 2.0, 3.456), "UP 90° R2 BA 3.456")
        self.assertEqual(FP.bend_note("DOWN", 135.0, 9.0, 23.5384), "DOWN 135° R9 BA 23.538")

    def test_direction_flips_with_the_viewing_side(self):
        """Same part unfolded from the outer root face is DOWN, from the inner root face UP."""
        case = Case("dir", 2.0, 1.0, 0.42, [20.0, 40.0, 20.0], [90.0, 90.0])
        base = build_base(self.doc, case)
        from SMTests.testOracle import inner_root_face
        down = UC.get_bend_lines(feature_unfold(self.doc, base, unfold_target_face(base.Shape), k=0.42, old=None))
        up = UC.get_bend_lines(feature_unfold(self.doc, base, inner_root_face(base.Shape), k=0.42, old=None))
        self.assertEqual({b.direction for b in down}, {"DOWN"})
        self.assertEqual({b.direction for b in up}, {"UP"})

    def test_mixed_directions_on_a_zigzag(self):
        """A Z profile (left turn then right turn) has one UP and one DOWN bend."""
        case = Case("zig", 2.0, 1.0, 0.42, [20.0, 30.0, 20.0], [90.0, -90.0])
        base = build_base(self.doc, case)
        feat = feature_unfold(self.doc, base, unfold_target_face(base.Shape), k=0.42, old=None)
        self.assertNotIn("Invalid", feat.State)
        self.assertEqual(sorted(b.direction for b in UC.get_bend_lines(feat)), ["DOWN", "UP"])

    def test_bend_lines_survive_save_and_restore(self):
        case = C.all_cases()[2]
        feat, _exp = C.build_flat(self.doc, case)
        before = [(b.note, [(tuple(a), tuple(c)) for a, c in b.segments]) for b in UC.get_bend_lines(feat)]
        path = self.path("r.FCStd")
        self.doc.saveAs(path)
        name = feat.Name
        FreeCAD.closeDocument(self.doc.Name)
        self.doc = FreeCAD.openDocument(path)  # tearDown closes it
        obj = self.doc.getObject(name)
        after = [(b.note, [(tuple(a), tuple(c)) for a, c in b.segments]) for b in UC.get_bend_lines(obj)]
        self.assertEqual(before, after)
        self.doc.recompute()
        self.assertEqual(after, [(b.note, [(tuple(a), tuple(c)) for a, c in b.segments])
                                 for b in UC.get_bend_lines(obj)])

    @unittest.skipUnless(HAVE_NEW, "networkx is not installed")
    def test_opt_in_new_unfolder_has_no_bend_data_and_refuses_the_export(self):
        case = C.all_cases()[0]
        obj, face, material, k = case.build(self.doc)
        feat = feature_unfold(self.doc, obj, face, material=material, k=k, old=False)
        try:
            self.assertEqual(UC.get_bend_lines(feat), [])
            with self.assertRaises(ValueError):
                UC.export_flat_dxf(feat, self.path("n.dxf"))
        finally:
            self.assertNotIn("UseOldUnfolder", SheetMetalTools.params.GetBools())


class TestFlatDxf(Doc):
    """Check 4."""

    report = []  # (case, entities, max deviation vs golden, bend position errors)

    def export(self, case):
        feat, exp = C.build_flat(self.doc, case)
        path = self.path(case.name + ".dxf")
        drawn = UC.export_flat_dxf(feat, path)
        return feat, exp, path, drawn

    def test_file_structure_and_layers(self):
        feat, _exp, path, drawn = self.export(C.all_cases()[2])
        with open(path, encoding="ascii") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("  0\nSECTION\n  2\nHEADER\n"))
        self.assertIn("AC1009", text)  # DXF R12
        self.assertTrue(text.rstrip("\n").endswith("EOF"))
        self.assertNotIn("\r", text)
        ents = entities(path)
        self.assertEqual({e["layer"] for e in ents}, LAYER_NAMES - {"BEND_UP", "INNER"})
        self.assertEqual(sum(e["type"] == "TEXT" for e in ents), 2)
        for e in ents:
            self.assertIn(e["type"], ("LINE", "ARC", "CIRCLE", "TEXT"))
        notes = [e["codes"][1] for e in ents if e["type"] == "TEXT"]
        self.assertEqual(sorted(notes), sorted(b.note.replace("°", "%%d") for b in drawn))

    def test_up_and_down_bends_use_their_own_layers(self):
        case = Case("zig", 2.0, 1.0, 0.42, [20.0, 30.0, 20.0], [90.0, -90.0])
        base = build_base(self.doc, case)
        feat = feature_unfold(self.doc, base, unfold_target_face(base.Shape), k=0.42, old=None)
        path = self.path("zig.dxf")
        UC.export_flat_dxf(feat, path)
        ents = entities(path)
        self.assertEqual(sum(e["layer"] == "BEND_UP" for e in ents if e["type"] == "LINE"), 1)
        self.assertEqual(sum(e["layer"] == "BEND_DOWN" for e in ents if e["type"] == "LINE"), 1)

    def test_export_is_deterministic(self):
        for case in C.all_cases():
            with self.subTest(case=case.name):
                doc = FreeCAD.newDocument("DT" + case.name[:3])
                try:
                    feat, _exp = C.build_flat(doc, case)
                    a, b = self.path(case.name + "_a.dxf"), self.path(case.name + "_b.dxf")
                    UC.export_flat_dxf(feat, a)
                    doc.recompute()
                    UC.export_flat_dxf(feat, b)
                    with open(a, "rb") as fa, open(b, "rb") as fb:
                        self.assertEqual(fa.read(), fb.read())
                finally:
                    FreeCAD.closeDocument(doc.Name)

    def test_goldens_exist_for_every_case_and_there_are_at_least_eight(self):
        cases = C.all_cases()
        self.assertGreaterEqual(len(cases), 8)
        for case in cases:
            self.assertTrue(os.path.exists(os.path.join(C.FIXTURE_DIR, case.name + ".dxf")), case.name)
        have = {n[:-4] for n in os.listdir(C.FIXTURE_DIR) if n.endswith(".dxf")}
        self.assertEqual(have, {c.name for c in cases}, "a golden without a case, or a case without one")

    def test_against_goldens_and_the_developed_length_formula(self):
        for case in C.all_cases():
            with self.subTest(case=case.name):
                doc = FreeCAD.newDocument("GD" + case.name[:3])
                try:
                    feat, exp = C.build_flat(doc, case)
                    path = self.path(case.name + ".dxf")
                    UC.export_flat_dxf(feat, path)
                    ents = entities(path)
                    golden = entities(os.path.join(C.FIXTURE_DIR, case.name + ".dxf"))
                    worst, problems = compare_entities(golden, ents)
                    self.assertEqual(problems, [], case.name)
                    err = self.check_formula(case, exp, ents)
                    self.report.append((case.name, len(ents), worst, err))
                finally:
                    FreeCAD.closeDocument(doc.Name)

    def check_formula(self, case, exp, ents):
        """Bend centreline positions against the formula; returns the largest error."""
        lines, outline = bend_positions(ents)
        self.assertEqual(len(lines), len(exp["ba"]), "bend lines on the BEND layers")
        if "pairs" in exp:  # tray: opposite bends are BA + the bottom side apart
            groups = {}
            for a, b in lines:
                (d, n) = unit(a, b)
                key = "x" if abs(d[0]) > abs(d[1]) else "y"
                groups.setdefault(key, []).append((perp_coordinate(a, d if d[0] > 0 or (d[0] == 0 and d[1] > 0) else (-d[0], -d[1])), n))
            got = sorted(abs(g[1][0] - g[0][0]) for g in (sorted(v) for v in groups.values()))
            self.assertEqual(len(got), 2)
            err = 0.0
            for g, e in zip(got, exp["pairs"]):
                err = max(err, abs(g - e))
                self.assertAlmostEqual(g, e, delta=1e-3)
            lengths = sorted(unit(a, b)[1] for a, b in lines)
            for g, e in zip(lengths, exp["bend_len"]):
                self.assertAlmostEqual(g, e, delta=1e-3)
            return err
        d, _n = unit(*lines[0])
        for a, b in lines:
            dd, _ = unit(a, b)
            self.assertAlmostEqual(abs(dd[0] * d[0] + dd[1] * d[1]), 1.0, delta=1e-9, msg="parallel bends")
        pts = [p for seg in outline for p in seg]
        lo = min(perp_coordinate(p, d) for p in pts)
        hi = max(perp_coordinate(p, d) for p in pts)
        self.assertAlmostEqual(hi - lo, exp["length"], delta=LEN_TOL, msg="flat length")
        got = sorted(perp_coordinate(a, d) - lo for a, _b in lines)
        want = exp["strip"]
        mirrored = sorted(exp["length"] - p for p in want)
        err_a = max(abs(g - w) for g, w in zip(got, want))
        err_b = max(abs(g - w) for g, w in zip(got, mirrored))
        err = min(err_a, err_b)
        self.assertLess(err, LEN_TOL, "bend positions %s vs formula %s" % (got, want))
        if exp.get("hole"):
            self.check_hole(ents, d, lo, got)
        return err

    def check_hole(self, ents, d, lo, bend_pos):
        circles = [e for e in ents if e["type"] == "CIRCLE"]
        self.assertEqual(len(circles), 1)
        self.assertEqual(circles[0]["layer"], "INNER")
        self.assertAlmostEqual(circles[0]["codes"][40], 5.0, delta=1e-6)
        mid = 0.5 * (bend_pos[0] + bend_pos[1])
        c = (circles[0]["codes"][10], circles[0]["codes"][20])
        self.assertAlmostEqual(perp_coordinate(c, d) - lo, mid, delta=1e-3)
        slot = [line_ends(e) for e in ents if e["layer"] == "INNER" and e["type"] == "LINE"]
        self.assertEqual(len(slot), 4)
        offs = sorted(abs(perp_coordinate(p, d) - lo - mid) for seg in slot for p in seg)
        self.assertAlmostEqual(offs[0], 7.0, delta=1e-3)
        self.assertAlmostEqual(offs[-1], 13.0, delta=1e-3)

    def test_outline_and_inner_layers(self):
        feat, _exp, path, _drawn = self.export(C.all_cases()[10])  # hole and slot
        ents = entities(path)
        inner = [e for e in ents if e["layer"] == "INNER"]
        self.assertEqual(sorted(e["type"] for e in inner), ["CIRCLE", "LINE", "LINE", "LINE", "LINE"])
        outline = [e for e in ents if e["layer"] == "OUTLINE"]
        self.assertEqual({e["type"] for e in outline}, {"LINE"})
        xs = [v for e in outline for v in (e["codes"][10], e["codes"][11])]
        ys = [v for e in outline for v in (e["codes"][20], e["codes"][21])]
        self.assertAlmostEqual(min(xs), 0.0, delta=1e-9)
        self.assertAlmostEqual(min(ys), 0.0, delta=1e-9)

    def test_zz_report(self):
        for name, n, worst, err in self.report:
            print("flat dxf %-34s entities %3d  golden max dev %.2e  bend position error %.2e"
                  % (name, n, worst, err))


class TestDefaultUntouched(unittest.TestCase):
    """Check 5: the default unfolder and the stored parameter are unchanged by this slice."""

    def test_default_is_v1_and_parameter_not_persisted(self):
        self.assertTrue(SheetMetalTools.use_old_unfolder())
        self.assertNotIn("UseOldUnfolder", SheetMetalTools.params.GetBools())


if __name__ == "__main__":
    unittest.main()
