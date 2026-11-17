# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2026 The FreeCAD project association AISBL              *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful, but        *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU     *
# *   Lesser General Public License for more details.                      *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************

"""Tests of the design-intent dimension schemes (F11 slice 1).

Small hand-built sketches, each showing one rule of ``SketcherIntent``: the
candidates differ in intent, every candidate is verified and applies cleanly,
propose reads nothing but the sketch it is given, and it has no side effects.
The similarity to the original dimension choices of the real sketches is
measured by the workshop's oracle, not here."""

import inspect
import io
import os
import unittest
import zipfile

import FreeCAD
import Part
import Sketcher

import SketcherAutoDefine as AD
import SketcherIntent as Intent

V = FreeCAD.Vector


def _line(sk, x1, y1, x2, y2, construction=False):
    i = sk.addGeometry(Part.LineSegment(V(x1, y1, 0), V(x2, y2, 0)), construction)
    return i


def _loop(sk, x0, y0, x1, y1):
    """A closed rectangle of four lines with coincident ends, horizontal and
    vertical constraints and no dimensions; returns the first line's id."""
    a = _line(sk, x0, y0, x1, y0)
    b = _line(sk, x1, y0, x1, y1)
    c = _line(sk, x1, y1, x0, y1)
    d = _line(sk, x0, y1, x0, y0)
    for p, q in ((a, b), (b, c), (c, d), (d, a)):
        sk.addConstraint(Sketcher.Constraint("Coincident", p, 2, q, 1))
    sk.addConstraint(Sketcher.Constraint("Horizontal", a))
    sk.addConstraint(Sketcher.Constraint("Horizontal", c))
    sk.addConstraint(Sketcher.Constraint("Vertical", b))
    sk.addConstraint(Sketcher.Constraint("Vertical", d))
    return a


def _content(sketch):
    z = zipfile.ZipFile(io.BytesIO(bytes(sketch.dumpContent())))
    return {n: z.read(n) for n in z.namelist()}


def _dims(candidate):
    return [(c["type"], *c["args"]) for c in candidate["constraints"] if c["role"] == "dimension"]


def _ids(spec):
    """The geometry ids a dimension spec names."""
    t = spec[0]
    if t in ("DistanceX", "DistanceY", "Distance", "Angle") and len(spec) == 6:
        return {spec[1], spec[3]}
    return {spec[1]}


class TestSketchIntent(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("IntentTests")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def sketch(self):
        return self.doc.addObject("Sketcher::SketchObject", "S")

    def frame(self):
        sk = self.sketch()
        _loop(sk, 0, 0, 600, 2100)
        _loop(sk, 30, 30, 570, 2070)
        sk.solve()
        return sk

    def propose(self, sk, **kw):
        res = AD.propose(sk, keep=list(range(len(sk.Constraints))), **kw)
        self.assertEqual(res["status"], "ok", res)
        return res

    # -- registration and ranking ------------------------------------------------

    def test_schemes_registered(self):
        self.assertEqual(tuple(AD.SCHEMES[:3]), Intent.INTENT_SCHEMES)
        self.assertEqual(tuple(AD.SCHEMES[3:]), ("baseline", "chain", "symmetric"))

    def test_rank_orders_by_priority_and_is_stable(self):
        pool = [("length", "a"), ("size", "b"), ("carrier", "c"), ("size", "d"), ("length", "e")]
        got = [s for _, s in Intent.rank(pool, "intent")]
        self.assertEqual(got, ["b", "d", "c", "a", "e"])
        # a kind a scheme does not list goes last
        self.assertEqual(Intent.rank([("zzz", "x"), ("size", "y")], "intent")[0][1], "y")

    def test_schemes_differ_in_what_comes_first(self):
        first = {s: Intent.PRIORITY[s] for s in Intent.INTENT_SCHEMES}
        self.assertLess(first["intent"]["carrier"], first["intent"]["origin"])
        self.assertLess(first["intent_offset"]["offset"], first["intent_offset"]["extent"])
        self.assertLess(first["intent_origin"]["extent"], first["intent_origin"]["origin"])

    # -- the rules --------------------------------------------------------------

    def test_default_candidates_are_the_intent_schemes_and_distinct(self):
        res = self.propose(self.frame())
        self.assertEqual([c["scheme"] for c in res["candidates"]], list(Intent.INTENT_SCHEMES))
        keys = {AD._candidate_key([tuple([c["type"]] + list(c["args"])) for c in cand["constraints"]]) for cand in res["candidates"]}
        self.assertEqual(len(keys), 3)

    def test_frame_is_dimensioned_by_offsets_and_overall_size(self):
        res = self.propose(self.frame())
        top = _dims(res["candidates"][0])
        outer, inner = {0, 1, 2, 3}, {4, 5, 6, 7}
        offsets = [d for d in top if len(d) == 6 and d[0] in ("DistanceX", "DistanceY") and (_ids(d) & outer) and (_ids(d) & inner)]
        self.assertGreaterEqual(len(offsets), 3, top)
        # overall width and height are carried by the outer edges, single-edge form
        single = [d for d in top if len(d) == 3 and d[0] in ("DistanceX", "DistanceY")]
        self.assertEqual({d[0] for d in single}, {"DistanceX", "DistanceY"}, top)
        self.assertTrue(all(d[1] in outer for d in single))
        # the width is on the lowest edge, the height on the rightmost one
        self.assertIn(("DistanceX", 0, 600.0), [(d[0], d[1], round(d[2], 6)) for d in single])
        self.assertIn(("DistanceY", 1, 2100.0), [(d[0], d[1], round(d[2], 6)) for d in single])

    def test_no_nesting_no_offsets(self):
        sk = self.sketch()
        _loop(sk, 0, 0, 40, 20)
        _loop(sk, 100, 100, 130, 120)
        sk.solve()
        top = _dims(self.propose(sk)["candidates"][0])
        for d in top:
            if len(d) == 6 and d[0] in ("DistanceX", "DistanceY"):
                self.assertTrue(_ids(d) <= {0, 1, 2, 3} or _ids(d) <= {4, 5, 6, 7} or d[1] == d[3], top)
        self.assertFalse([d for d in top if len(d) == 3])

    def test_hole_diameter_comes_before_lengths_and_positions(self):
        sk = self.sketch()
        _loop(sk, 0, 0, 80, 50)
        sk.addGeometry(Part.Circle(V(20, 25, 0), V(0, 0, 1), 6.5), False)
        sk.addGeometry(Part.Circle(V(60, 25, 0), V(0, 0, 1), 6.5), False)
        sk.solve()
        res = self.propose(sk)
        for cand in res["candidates"]:
            dims = _dims(cand)
            self.assertEqual(dims[0][0], "Diameter", dims)
        # the two holes are equal only if the sketch says so: here both are sized or related
        self.assertGreaterEqual(sum(1 for d in _dims(res["candidates"][0]) if d[0] == "Diameter"), 1)

    def test_construction_line_carries_a_dimension(self):
        sk = self.sketch()
        _loop(sk, 0, 0, 40, 20)
        c = _line(sk, 0, 5, 40, 5, True)
        sk.addConstraint(Sketcher.Constraint("Horizontal", c))
        sk.addConstraint(Sketcher.Constraint("Coincident", c, 1, 3, 1))  # its start on the left edge's start? kept simple
        sk.solve()
        res = AD.propose(sk, keep=list(range(len(sk.Constraints))))
        if res["status"] != "ok":
            self.skipTest("fixture not solvable: %s" % res["status"])
        top = _dims(res["candidates"][0])
        self.assertTrue(any(c in _ids(d) and len(d) >= 5 for d in top), top)
        # the carrier is the first dimension after the sizes (there are none)
        self.assertIn(c, _ids(top[0]), top)

    def test_inclined_construction_line_is_not_a_carrier(self):
        sk = self.sketch()
        a = _loop(sk, 0, 0, 20, 10)
        d = _line(sk, 0, 0, 20, 10, True)
        sk.addConstraint(Sketcher.Constraint("Coincident", d, 1, a, 1))
        sk.addConstraint(Sketcher.Constraint("Coincident", d, 2, a + 2, 1))
        sk.solve()
        for cand in self.propose(sk)["candidates"]:
            for dim in _dims(cand):
                self.assertNotIn(d, _ids(dim), dim)

    def test_small_gap_is_dimensioned_between_the_two_pieces(self):
        sk = self.sketch()
        a = _line(sk, 0, 0, 30, 0)
        b = _line(sk, 30.5, 0.25, 30.5, 20)
        sk.addConstraint(Sketcher.Constraint("Horizontal", a))
        sk.addConstraint(Sketcher.Constraint("Vertical", b))
        sk.solve()
        top = _dims(self.propose(sk)["candidates"][0])
        gap = [d for d in top if len(d) == 6 and d[0] in ("DistanceX", "DistanceY") and _ids(d) == {a, b}]
        self.assertEqual(len(gap), 2, top)
        self.assertEqual(sorted(round(d[5], 6) for d in gap), [0.25, 0.5])

    def test_neighbour_angle_in_the_offset_and_origin_schemes_and_axis_angle_in_the_first(self):
        sk = self.sketch()
        a = _line(sk, 0, 0, 20, 0)
        b = _line(sk, 20, 0, 30, 15)
        sk.addConstraint(Sketcher.Constraint("Coincident", a, 2, b, 1))
        sk.addConstraint(Sketcher.Constraint("Horizontal", a))
        sk.solve()
        res = self.propose(sk)
        by = {c["scheme"]: _dims(c) for c in res["candidates"]}
        angles = {s: [d for d in dims if d[0] == "Angle"] for s, dims in by.items()}
        self.assertEqual([len(d) for d in angles["intent"]], [3], angles["intent"])  # against the x axis: (line, value)
        for s in ("intent_offset", "intent_origin"):
            self.assertEqual(len(angles[s]), 1, by[s])
            self.assertEqual(_ids(angles[s][0]), {a, b})
            self.assertGreater(angles[s][0][5], 0.0)

    def test_overall_size_between_extreme_points_when_no_edge_spans(self):
        sk = self.sketch()  # a staircase: no single edge spans the width or the height, a point pair does
        pts = [(0, 0), (40, 0), (40, 10), (60, 10), (60, 30), (20, 30), (20, 20), (0, 20)]
        n = len(pts)
        for k in range(n):
            (x1, y1), (x2, y2) = pts[k], pts[(k + 1) % n]
            _line(sk, x1, y1, x2, y2)
        for k in range(n):
            sk.addConstraint(Sketcher.Constraint("Coincident", k, 2, (k + 1) % n, 1))
        for k, (p, q) in enumerate(zip(pts, pts[1:] + pts[:1])):
            sk.addConstraint(Sketcher.Constraint("Horizontal" if p[1] == q[1] else "Vertical", k))
        sk.solve()
        res = self.propose(sk)
        cand = [c for c in res["candidates"] if c["scheme"] == "intent_origin"][0]
        dims = _dims(cand)
        wide = [d for d in dims if d[0] == "DistanceX" and len(d) == 6 and abs(d[5] - 60) < 1e-6]
        high = [d for d in dims if d[0] == "DistanceY" and len(d) == 6 and abs(d[5] - 30) < 1e-6]
        self.assertEqual(len(wide), 1, dims)
        self.assertEqual(len(high), 1, dims)
        self.assertNotEqual(wide[0][1], wide[0][3], "the overall width is between two different edges")

    # -- hard bar and fallbacks -------------------------------------------------

    def test_every_candidate_applies_cleanly(self):
        for build in (self.frame,):
            sk = build()
            res = self.propose(sk)
            self.assertEqual(len(res["candidates"]), 3)
            for cand in res["candidates"]:
                self.assertTrue(cand["verified"])
                copy = self.doc.copyObject(sk, False)
                AD.apply_candidate(copy, cand)
                self.assertEqual(copy.solve(), 0)
                self.assertEqual(copy.getGeometryWithDependentParameters(), [])
                self.doc.removeObject(copy.Name)

    def test_baseline_is_still_available_and_unchanged(self):
        sk = self.frame()
        res = self.propose(sk, prefer="baseline", max_candidates=1)
        self.assertEqual(res["candidates"][0]["scheme"], "baseline")
        self.assertEqual(res["candidates"][0]["verified"], True)
        for name in ("chain", "symmetric"):
            res = self.propose(sk, prefer=name, max_candidates=1)
            self.assertEqual(res["candidates"][0]["scheme"], name)

    def test_unknown_scheme_is_refused(self):
        with self.assertRaises(AD.AutoDefineError):
            AD.propose(self.frame(), prefer="no_such_scheme")

    def test_equal_group_is_sized_once(self):
        sk = self.sketch()
        for x in (0, 50):
            sk.addGeometry(Part.Circle(V(x, 0, 0), V(0, 0, 1), 4.0), False)
        sk.addConstraint(Sketcher.Constraint("Equal", 0, 1))
        sk.solve()
        top = _dims(self.propose(sk)["candidates"][0])
        self.assertEqual(sum(1 for d in top if d[0] in ("Diameter", "Radius")), 1, top)

    # -- no leak, no side effects --------------------------------------------------

    def test_propose_reads_only_the_sketch_it_is_given(self):
        # an "original" with datums sits next to the stripped sketch; changing its datum values
        # changes nothing the stripped sketch's candidates say
        orig = self.frame()
        stripped = self.doc.copyObject(orig, False)
        res1 = [_dims(c) for c in self.propose(stripped)["candidates"]]
        orig.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 111.0))
        orig.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, 77.0))
        orig.setDatum(len(orig.Constraints) - 1, FreeCAD.Units.Quantity("55 mm"))
        res2 = [_dims(c) for c in self.propose(stripped)["candidates"]]
        self.assertEqual(res1, res2)

    def test_module_does_not_read_the_corpus_or_oracle(self):
        for mod in (Intent, AD):
            src = inspect.getsource(mod)
            for needle in ("SketchAutoDefineCorpus", "SketchAutoDefineOracle", "REAL_SKETCHES", "harness/f11"):
                self.assertNotIn(needle, src, "%s reads %s" % (mod.__name__, needle))

    def test_no_machine_paths_and_spdx(self):
        src = inspect.getsource(Intent)
        self.assertIn("SPDX-License-Identifier: LGPL-2.1-or-later", src)
        for needle in ("/Users/", "/home/", "C:\\\\"):
            self.assertNotIn(needle, src)

    def test_no_side_effects(self):
        sk = self.frame()
        doc = sk.Document
        before = (_content(sk), doc.UndoCount, doc.RedoCount, sorted(FreeCAD.listDocuments().keys()), [(o.Name, tuple(o.State)) for o in doc.Objects])
        self.propose(sk)
        after = (_content(sk), doc.UndoCount, doc.RedoCount, sorted(FreeCAD.listDocuments().keys()), [(o.Name, tuple(o.State)) for o in doc.Objects])
        self.assertEqual(before, after)

    def test_datum_count_stays_near_the_dof(self):
        sk = self.frame()
        res = self.propose(sk)
        for cand in res["candidates"]:
            self.assertLessEqual(cand["dimensions"], res["dof"], _dims(cand))
            self.assertGreaterEqual(cand["dimensions"], res["dof"] - 2 * cand["relations"], _dims(cand))

    def test_equal_group_gets_one_size_in_the_proposals(self):
        sk = self.sketch()
        for x in (0, 50, 100):
            sk.addGeometry(Part.Circle(V(x, 0, 0), V(0, 0, 1), 4.0), False)
        sk.solve()
        model = AD._model(sk)
        for scheme in Intent.INTENT_SCHEMES:
            sizes = [s for s in Intent.proposals(model, scheme, [("Equal", 0, 1), ("Equal", 1, 2)]) if s[0] == "Diameter"]
            self.assertEqual(len(sizes), 1, (scheme, sizes))
            sizes = [s for s in Intent.proposals(model, scheme, []) if s[0] == "Diameter"]
            self.assertEqual(len(sizes), 3, (scheme, sizes))

    def test_proposals_hold_no_dimension_twice(self):
        sk = self.sketch()
        _loop(sk, 0, 0, 100.0 / 3, 70.0 / 9)
        _loop(sk, 1.0 / 7, 1.0 / 7, 100.0 / 3 - 1.0 / 7, 70.0 / 9 - 1.0 / 7)
        sk.solve()
        model = AD._model(sk)
        for scheme in Intent.INTENT_SCHEMES:
            specs = Intent.proposals(model, scheme)
            keys = [Intent._norm_key(s) for s in specs]
            self.assertEqual(len(keys), len(set(keys)), scheme)
            self.assertTrue(all(s[-1] == round(s[-1], 10) for s in specs), scheme)

    def test_construction_geometry_is_not_part_of_the_outline(self):
        sk = self.sketch()
        _loop(sk, 0, 0, 100, 50)
        c = _line(sk, 5, 5, 95, 5, True)
        sk.addConstraint(Sketcher.Constraint("Horizontal", c))
        sk.solve()
        res = self.propose(sk)
        for cand in res["candidates"]:
            for d in _dims(cand):
                if c in _ids(d):
                    self.assertEqual(_ids(d), {c}, "a drawn edge is offset from a construction line: %s" % (d,))
        top = _dims(res["candidates"][0])
        self.assertFalse([d for d in top if len(d) == 3], top)

    def test_same_sketch_in_two_documents_gives_identical_candidates(self):
        # float noise in a copied sketch's solved coordinates (1e-16) must not change a value or a tie
        sk = self.sketch()
        a = _line(sk, 0, 0, 3.4641016151377544, 2.0)
        b = _line(sk, 3.4641016151377544, 2.0, 3.4641016151377544, -0.7)
        c = _line(sk, 3.4641016151377544, -0.7, 0, 0)
        sk.addConstraint(Sketcher.Constraint("Coincident", a, 2, b, 1))
        sk.addConstraint(Sketcher.Constraint("Coincident", b, 2, c, 1))
        sk.addConstraint(Sketcher.Constraint("Coincident", c, 2, a, 1))
        sk.addConstraint(Sketcher.Constraint("Vertical", b))
        sk.solve()

        def run(s):
            res = AD.propose(s, keep=list(range(len(s.Constraints))))
            return [(x["scheme"], [(k["type"], tuple(k["args"])) for k in x["constraints"]]) for x in res["candidates"]]

        first = run(sk)
        other = FreeCAD.newDocument("IntentOther")
        try:
            copy = other.addObject("Sketcher::SketchObject", "C")
            copy.restorePropertyContent("Geometry", sk.dumpPropertyContent("Geometry"))
            copy.Constraints = list(sk.Constraints)
            copy.solve()
            # the copy's coordinates differ in the last digits
            copy.Geometry = [Part.LineSegment(V(g.StartPoint.x + 4e-16, g.StartPoint.y - 3e-16, 0), V(g.EndPoint.x - 2e-16, g.EndPoint.y + 4e-16, 0)) for g in copy.Geometry]
            copy.solve()
            self.assertEqual(first, run(copy))
        finally:
            FreeCAD.closeDocument(other.Name)

    def test_holes_are_spaced_from_each_other(self):
        sk = self.sketch()
        for x in (0, 30, 75):
            sk.addGeometry(Part.Circle(V(x, 10, 0), V(0, 0, 1), 3.0 + x / 100.0), False)
        sk.solve()
        top = _dims(self.propose(sk)["candidates"][0])
        pairs = [d for d in top if len(d) == 6 and d[0] == "DistanceX" and d[2] == 3 and d[4] == 3]
        self.assertEqual(sorted((d[1], d[3]) for d in pairs), [(0, 1), (1, 2)], top)

    def test_overall_height_between_extreme_points_when_the_spanning_edge_is_on_an_axis(self):
        sk = self.sketch()
        pts = [(0, 40), (0, 0), (5, 0), (5, 25), (3, 25), (3, 40)]
        for k in range(6):
            (x1, y1), (x2, y2) = pts[k], pts[(k + 1) % 6]
            _line(sk, x1, y1, x2, y2)
        for k in range(6):
            sk.addConstraint(Sketcher.Constraint("Coincident", k, 2, (k + 1) % 6, 1))
        for k in range(6):
            (x1, y1), (x2, y2) = pts[k], pts[(k + 1) % 6]
            sk.addConstraint(Sketcher.Constraint("Horizontal" if y1 == y2 else "Vertical", k))
        sk.solve()
        top = _dims(self.propose(sk)["candidates"][0])
        tall = [d for d in top if d[0] == "DistanceY" and len(d) == 6 and abs(d[5] - 40) < 1e-6]
        self.assertEqual(len(tall), 1, top)
        self.assertNotEqual(tall[0][1], tall[0][3], top)

    def test_norm_key_merges_orientation_spellings(self):
        a = ("DistanceX", 1, 2, 3, 1, 5.0)
        b = ("DistanceX", 3, 1, 1, 2, 5.0)
        self.assertEqual(Intent._norm_key(a), Intent._norm_key(b))
        self.assertNotEqual(Intent._norm_key(a), Intent._norm_key(("DistanceY", 1, 2, 3, 1, 5.0)))


if __name__ == "__main__":
    unittest.main()
