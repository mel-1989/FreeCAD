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

"""Tests of SketcherAutoDefine (G10 slice 2): strip-and-recover on a fast
subset of the corpus.  The whole corpus (all generated variants, all 61 real
sketches, five strip levels) runs from ``SketchAutoDefineRun.py``; the rates
quoted in the run note come from there.

Every check of the slice's brief has at least one test here.  The oracle is in
``SketchAutoDefineOracle.py`` and shares no detection code with the
generator."""

import io
import json
import math
import os
import re
import time
import unittest
import zipfile

import FreeCAD
import Part
import Sketcher

import SketcherAutoDefine as AD
from SketcherTests import SketchAutoDefineCorpus as Corpus
from SketcherTests import SketchAutoDefineOracle as O
from SketcherTests import SketchAutoDefineRun as Run

V = FreeCAD.Vector

# check 3 and 4 and 5 bars
RECOVER_DIMS = {"generated": 0.95, "real": 0.85}
RECOVER_ALL = {"generated": 0.90, "real": 0.75}
RELATIONS = {"generated": 0.90, "real": 0.75}
EDIT_RATE = 0.90
SPEED_LIMIT = 30.0

# real sketches of the gate subset: fast ones from four different files families
GATE_REAL = [
    "examples/BIMExample.FCStd:Sketch",
    "examples/BIMExample.FCStd:Sketch007",
    "examples/PartDesignExample.FCStd:Sketch",
    "examples/PartDesignExample.FCStd:Sketch002",
    "Mod/CAM/CAMTests/test_geomop.fcstd:Sketch006",
    "Mod/CAM/CAMTests/test_geomop.fcstd:Sketch",
    "Mod/CAM/CAMTests/test_geomop.fcstd:Sketch003",
    "Mod/CAM/CAMTests/test_holes00.fcstd:Sketch",
    "Mod/CAM/Tools/Shape/radius.fcstd:Sketch",
    "Mod/CAM/Tools/Shape/taperedballnose.fcstd:Sketch",
    "Mod/CAM/Tools/Shape/tap.fcstd:Sketch",
    "Mod/CAM/Tools/Shape/dovetail.fcstd:Sketch",
    "Mod/CAM/Tools/Shape/v-bit.fcstd:Sketch",
    "Mod/CAM/Tools/Shape/ballend.fcstd:Sketch",
    "Mod/CAM/Tools/Shape/chamfer.fcstd:Sketch",
    "Mod/CAM/Tools/Shape/endmill.fcstd:Sketch",
    "Mod/CAM/Tools/Shape/probe.fcstd:Sketch",
    "Mod/Sketcher/SketcherTests/TestSketchCarbonCopyReverseMapping.FCStd:Sketch",
    "Mod/BIM/bimtests/test_structure_column_flip_v1_0_v1_1.FCStd:Sketch",
]


def _active_name():
    return FreeCAD.ActiveDocument.Name if FreeCAD.ActiveDocument is not None else None


def _content(sketch):
    z = zipfile.ZipFile(io.BytesIO(bytes(sketch.dumpContent())))
    return {n: z.read(n) for n in z.namelist()}


def _signature(sketch):
    doc = sketch.Document
    return {
        "content": _content(sketch),
        "undo": doc.UndoCount,
        "redo": doc.RedoCount,
        "states": [(o.Name, tuple(o.State)) for o in doc.Objects],
        "documents": sorted(FreeCAD.listDocuments().keys()),
        "active": _active_name(),
        "constraints": [O.row(c) for c in sketch.Constraints],
        "geometry": [str(g) for g in sketch.Geometry],
        "expressions": list(sketch.ExpressionEngine),
    }


def _bare(doc, name, geometry, construction=()):
    sk = doc.addObject("Sketcher::SketchObject", name)
    sk.addGeometry(geometry, False)
    for i in construction:
        sk.setConstruction(i, True)
    sk.solve()
    return sk


def _line(a, b):
    return Part.LineSegment(V(a[0], a[1], 0), V(b[0], b[1], 0))


def _circle(c, r):
    return Part.Circle(V(c[0], c[1], 0), V(0, 0, 1), r)


def _checked(doc, sketch, **kw):
    """propose, then apply every candidate to a clone and judge it with the
    oracle.  Returns (result, problems)."""
    res = AD.propose(sketch, **kw)
    problems = []
    for k, cand in enumerate(res["candidates"]):
        t = Corpus.clone(doc, sketch, "Applied")
        rows = [O.row(c) for c in t.Constraints]
        coords = O.coordinates(t)
        try:
            AD.apply_candidate(t, cand)
            problems += ["candidate %d: %s" % (k, p) for p in O.check_applied(t, rows, coords)]
        except Exception as exc:
            problems.append("candidate %d: %s" % (k, exc))
        finally:
            doc.removeObject(t.Name)
    return res, problems


def _pairs(res, kind):
    """Geometry-id pairs joined by a relation of type `kind` in any candidate."""
    out = []
    for cand in res["candidates"]:
        for c in cand["constraints"]:
            if c["type"] == kind:
                a = c["args"]
                out.append(tuple(sorted({a[0], a[2]} if kind == "Coincident" else {a[0], a[1]})))
    return out


class TestSketchAutoDefine(unittest.TestCase):
    """The gate corpus is built and run once; the tests share the records."""

    records = None
    cases = None
    stats = {}

    @classmethod
    def setUpClass(cls):
        cls.previous = _active_name()
        cls.doc = FreeCAD.newDocument("AutoDefineCorpus")
        t0 = time.time()
        cls.generated = Corpus.generated(cls.doc, variants=2)
        cls.slice1 = Corpus.slice1_cases(cls.doc)
        cls.cache = {}
        cls.real, cls.missing = Corpus.real_cases(cls.cache, names=GATE_REAL)
        cls.cases = cls.generated + cls.slice1 + cls.real
        cls.records = []
        for case in cls.cases:
            doc = case.doc if case.real else cls.doc
            for level, frac, seed in (("dims", 0.0, 0), ("all", 0.0, 0), ("partial", 0.5, 1)):
                cls.records.append(Run.evaluate(doc, case, level, fraction=frac, seed=seed))
        cls.seconds = time.time() - t0

    @classmethod
    def tearDownClass(cls):
        names = [cls.doc.Name] + [d.Name for d in cls.cache.values()]
        for n in names:
            if n in FreeCAD.listDocuments():
                FreeCAD.closeDocument(n)
        if cls.previous is not None and cls.previous in FreeCAD.listDocuments():
            FreeCAD.setActiveDocument(cls.previous)

    def rows(self, level, kind=None):
        out = [r for r in self.records if r["level"] == level]
        if kind == "generated":
            out = [r for r in out if r["family"] not in ("real", "slice1")]
        elif kind == "real":
            out = [r for r in out if r["real"]]
        return out

    # -- check 1: corpus -----------------------------------------------------
    def testCorpusShape(self):
        self.assertGreaterEqual(len(Corpus.FAMILIES), 10)
        self.assertGreaterEqual(len(Corpus.FAMILIES) * 2 * 9, 200)
        self.assertEqual(len(Corpus.REAL_SKETCHES), 61)
        self.assertEqual(self.missing, [])
        self.assertEqual(len(self.real), len(GATE_REAL))
        from SketcherTests import SketchDiagnosisCorpus as Slice1

        # the build tree does not install the CAM demo parts or data/tests; the source tree has them all
        not_installed = {"Mod/CAM/DemoParts/hole_puzzle.fcstd", "Mod/CAM/DemoParts/motor_mount_inch.fcstd", "tests/PadTest.fcstd", "tests/PocketTest.fcstd"}
        missing = {rel for rel, _ in Corpus.REAL_SKETCHES if Slice1.find_data_file(rel) is None}
        self.assertLessEqual(missing, not_installed, "frozen real sketches that are not in the tree")
        self.assertGreaterEqual(len(self.slice1), 10)
        rotated = [c for c in self.generated if c.rotated]
        self.assertEqual(len(rotated) * 2, len(self.generated))

    def testGeneratedCasesAreFullyConstrainedAndDeterministic(self):
        for case in self.generated:
            sk = case.sketch
            self.assertEqual((sk.solve(), sk.DoF), (0, 0), case.name)
            self.assertFalse(sk.ConflictingConstraints or sk.RedundantConstraints, case.name)
        d2 = FreeCAD.newDocument("AutoDefineAgain")
        try:
            a = Corpus.build_generated(d2, "plate_holes", True, 3)
            b = Corpus.build_generated(d2, "plate_holes", True, 3)
            self.assertLess(O.moved(O.coordinates(a), O.coordinates(b)), 1e-9)
            c = Corpus.build_generated(d2, "plate_holes", True, 4)
            self.assertGreater(O.moved(O.coordinates(a), O.coordinates(c)), 1e-3)
        finally:
            FreeCAD.closeDocument(d2.Name)
            if self.previous in FreeCAD.listDocuments():
                FreeCAD.setActiveDocument(self.previous)

    def testStripLevels(self):
        sk = self.generated[0].sketch
        d, _ = Corpus.strip(self.doc, sk, "dims")
        a, _ = Corpus.strip(self.doc, sk, "all")
        try:
            kinds = lambda s: {c.Type for c in s.Constraints}
            self.assertFalse(kinds(d) & set(Corpus.DATUM_TYPES))
            self.assertTrue(kinds(d) & {"Coincident"})
            self.assertFalse(kinds(a) & (set(Corpus.DATUM_TYPES) | set(Corpus.RELATION_TYPES)))
            self.assertEqual(O.coordinates(sk), O.coordinates(a))
        finally:
            self.doc.removeObject(d.Name)
            self.doc.removeObject(a.Name)

    # -- check 2: the hard bar --------------------------------------------------
    def testNoOfferedCandidateFailsTheOracle(self):
        bad = [(r["case"], r["level"], r["oracle_failures"]) for r in self.records if r["oracle_failures"]]
        self.assertEqual(bad, [])
        self.assertGreater(sum(r["candidates"] for r in self.records), 100)

    def testUnclosedSketchGetsAnHonestReport(self):
        # a B-spline whose poles are not exposed has nothing to dimension them with
        cur = Part.BSplineCurve()
        cur.buildFromPoles([V(0, 0, 0), V(5, 4, 0), V(11, -3, 0), V(17, 2, 0)], False, 3)
        sk = _bare(self.doc, "BareSpline", [cur])
        try:
            res, problems = _checked(self.doc, sk)
            self.assertEqual(problems, [])
            self.assertEqual(res["candidates"], [])
            self.assertEqual(res["status"], "cannot_close")
            self.assertTrue(res["unclosed"])
            self.assertTrue(all(u["remaining_dof"] > 0 for u in res["unclosed"]))
        finally:
            self.doc.removeObject(sk.Name)

    def testAlreadyDefinedAndInconsistentInput(self):
        done = self.generated[0].sketch
        self.assertEqual(AD.propose(done)["status"], "already_defined")
        sk = _bare(self.doc, "Conflict", [_line((0, 0), (10, 0))])
        try:
            sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 10.0))
            sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 12.0))
            res = AD.propose(sk)
            self.assertEqual(res["status"], "input_inconsistent")
            self.assertEqual(res["candidates"], [])
        finally:
            self.doc.removeObject(sk.Name)

    # -- check 3: recovery rates -----------------------------------------------
    def _rate(self, level, kind):
        rs = self.rows(level, kind)
        ok = sum(1 for r in rs if r["candidates"] and not r["oracle_failures"])
        self.stats["recover/%s/%s" % (level, kind)] = (ok, len(rs))
        return ok / len(rs), [r["case"] for r in rs if not r["candidates"]]

    def testRecoveryDimensionsStripped(self):
        for kind in ("generated", "real"):
            rate, misses = self._rate("dims", kind)
            self.assertGreaterEqual(rate, RECOVER_DIMS[kind], misses)

    def testRecoveryEverythingStripped(self):
        for kind in ("generated", "real"):
            rate, misses = self._rate("all", kind)
            self.assertGreaterEqual(rate, RECOVER_ALL[kind], misses)

    # -- check 4: relations, not locks --------------------------------------------
    def testRelationsAreEnforced(self):
        for kind in ("generated", "real"):
            rs = [r for r in self.rows("all", kind) if "relations_total" in r]
            total = sum(r["relations_total"] for r in rs)
            held = sum(r.get("relations_enforced", 0) for r in rs)
            self.stats["relations/%s" % kind] = (held, total)
            self.assertGreater(total, 100)
            self.assertGreaterEqual(held / total, RELATIONS[kind], [(r["case"], r.get("relations_missed")) for r in rs if r.get("relations_missed")])

    def testDatumCountStaysNearTheOriginals(self):
        over = []
        for r in self.rows("all", "generated"):
            if "datums_candidate" in r and r["datums_candidate"] > math.ceil(1.25 * r["datums_original"]):
                over.append((r["case"], r["datums_candidate"], r["datums_original"]))
        self.assertEqual(over, [])

    # -- check 5: editability ----------------------------------------------------------
    def testEditability(self):
        for kind in ("generated", "real"):
            rs = [r for r in self.rows("all", kind) if "edit_changes" in r]
            ch = sum(r["edit_changes"] for r in rs)
            ok = sum(r["edit_solved"] for r in rs)
            restored = sum(r["edit_restored"] for r in rs)
            self.stats["edit/%s" % kind] = (ok, ch)
            self.assertGreater(ch, 100)
            self.assertGreaterEqual(ok / ch, EDIT_RATE)
            self.assertGreaterEqual(restored / ch, EDIT_RATE)
            # sketches whose own constraints fail a change do not count against the candidate
            ordinary = [r for r in rs if r.get("orig_edit_changes") and r["orig_edit_solved"] / r["orig_edit_changes"] >= EDIT_RATE]
            if ordinary:
                self.assertGreaterEqual(sum(r["edit_solved"] for r in ordinary) / sum(r["edit_changes"] for r in ordinary), EDIT_RATE)

    # -- check 6: keep, existing constraints, reference and construction --------------------
    def testPartlyStrippedKeepsEveryRetainedConstraint(self):
        rs = self.rows("partial50")
        self.assertEqual([r for r in rs if r["oracle_failures"]], [])
        self.assertGreater(sum(1 for r in rs if r["candidates"]), len(rs) * 0.9)

    def testKeepIsVerifiedAndRetainedRowsAreIdentical(self):
        case = self.generated[2]
        stripped, kept = Corpus.strip(self.doc, case.sketch, "partial", 0.5, 7)
        try:
            rows = [O.row(c) for c in stripped.Constraints]
            res = AD.propose(stripped, keep=list(range(0, len(rows), 2)))
            self.assertTrue(res["candidates"])
            for cand in res["candidates"]:
                t = Corpus.clone(self.doc, stripped, "Applied")
                try:
                    AD.apply_candidate(t, cand)
                    self.assertEqual([O.row(c) for c in t.Constraints][: len(rows)], rows)
                finally:
                    self.doc.removeObject(t.Name)
            with self.assertRaises(AD.AutoDefineError):
                AD.propose(stripped, keep=[len(rows) + 5])
        finally:
            self.doc.removeObject(stripped.Name)

    def testReferenceConstructionExpressionAndExternalGeometrySurvive(self):
        other = self.doc.addObject("Sketcher::SketchObject", "ExternalSource")
        other.addGeometry(_line((-30, -10), (-30, 40)), False)
        self.doc.recompute()
        sk = self.doc.addObject("Sketcher::SketchObject", "Mixed")
        try:
            sk.addGeometry(
                [_line((0, 0), (20, 0)), _line((20, 0), (20, 10)), _line((20, 10), (0, 10)), _line((0, 10), (0, 0)), _line((0, 0), (20, 10))],
                False,
            )
            sk.setConstruction(4, True)
            for i in range(4):
                sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
            w = sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 20.0))
            sk.renameConstraint(w, "Width")
            h = sk.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, 10.0))
            sk.renameConstraint(h, "Height")
            sk.setExpression("Constraints.Height", "Constraints.Width / 2")
            ref = sk.addConstraint(Sketcher.Constraint("Distance", 4, 22.3606797749979))
            sk.setDriving(ref, False)
            try:
                sk.addExternal("ExternalSource", "Edge1")
            except Exception:
                pass
            sk.solve()
            self.doc.recompute()
            before = [O.row(c) for c in sk.Constraints]
            externals = len(sk.ExternalGeo)
            res, problems = _checked(self.doc, sk)
            self.assertEqual(problems, [])
            self.assertTrue(res["candidates"])
            for cand in res["candidates"]:
                # nothing may dimension the construction diagonal: its end points are the corners
                for c in cand["constraints"]:
                    if c["role"] == "dimension":
                        self.assertNotIn(4, [c["args"][0]] + ([c["args"][2]] if len(c["args"]) == 5 else []), c["text"])
            AD.apply_candidate(sk, res["candidates"][0])
            self.assertEqual([O.row(c) for c in sk.Constraints][: len(before)], before)
            self.assertFalse(sk.Constraints[ref].Driving)
            self.assertTrue(sk.getConstruction(4))
            self.assertEqual(len(sk.ExternalGeo), externals)
            self.assertEqual(len(sk.ExpressionEngine), 1)
            self.assertEqual(sk.DoF, 0)
        finally:
            self.doc.removeObject(sk.Name)
            self.doc.removeObject(other.Name)

    def testConstructionGeometryIsNotDimensionedWhenARelationWillDo(self):
        # a construction diagonal across a rectangle: its end points are the corners
        sk = _bare(
            self.doc,
            "Diagonal",
            [_line((0, 0), (20, 0)), _line((20, 0), (20, 10)), _line((20, 10), (0, 10)), _line((0, 10), (0, 0)), _line((0, 0), (20, 10))],
            construction=[4],
        )
        try:
            res, problems = _checked(self.doc, sk)
            self.assertEqual(problems, [])
            self.assertTrue(res["candidates"])
            for cand in res["candidates"]:
                for c in cand["constraints"]:
                    self.assertFalse(c["role"] == "dimension" and c["args"][0] == 4, c["text"])
        finally:
            self.doc.removeObject(sk.Name)

    # -- check 7: schemes -----------------------------------------------------------------------
    def testSchemesGiveDistinctVerifiedCandidates(self):
        multi = 0
        for r in self.rows("all", "generated"):
            self.assertEqual(len(r["schemes"]), len(set(r["schemes"])), r["case"])
            if r["candidates"] >= 2:
                multi += 1
        self.stats["multiple_candidates"] = (multi, len(self.rows("all", "generated")))
        self.assertGreaterEqual(multi, len(self.rows("all", "generated")) * 0.3)

    def testPreferAndMaxCandidates(self):
        stripped, _ = Corpus.strip(self.doc, self.generated[0].sketch, "all")
        try:
            for scheme in AD.SCHEMES:
                res = AD.propose(stripped, prefer=scheme)
                self.assertEqual(res["candidates"][0]["scheme"], scheme)
            res = AD.propose(stripped, prefer="chain")
            keys = [frozenset(json.dumps([c["type"], c["args"][:-1] if c["role"] == "dimension" else c["args"]]) for c in cand["constraints"]) for cand in res["candidates"]]
            self.assertEqual(len(keys), len(set(keys)))
            self.assertEqual(len(AD.propose(stripped, max_candidates=1)["candidates"]), 1)
            with self.assertRaises(AD.AutoDefineError):
                AD.propose(stripped, prefer="nonsense")
        finally:
            self.doc.removeObject(stripped.Name)

    def testMediumOracleIsReported(self):
        per = {}
        for r in self.rows("all"):
            m = r.get("medium")
            if m:
                fam = r["family"] if not r["real"] else "real"
                a = per.setdefault(fam, [0, 0, 0])
                a[0] += m["datums"]
                a[1] += m["kind_hits"]
                a[2] += m["target_hits"]
        self.stats["medium"] = per
        self.assertIn("real", per)
        self.assertGreaterEqual(len(per), 8)

    # -- check 8: no side effects and speed -------------------------------------------------------
    def testProposeLeavesTheSketchAndDocumentUntouched(self):
        stripped, _ = Corpus.strip(self.doc, self.generated[4].sketch, "all")
        try:
            before = _signature(stripped)
            AD.propose(stripped)
            self.assertEqual(_signature(stripped), before)
            self.assertEqual([n for n in FreeCAD.listDocuments() if n.startswith("SketchDiagnosisScratch")], [])
        finally:
            self.doc.removeObject(stripped.Name)

    def testProposeLeavesNoTraceWhenItRaises(self):
        stripped, _ = Corpus.strip(self.doc, self.generated[4].sketch, "all")
        original = AD._verify
        try:
            before = _signature(stripped)

            def boom(*a, **k):
                raise RuntimeError("planted")

            AD._verify = boom
            with self.assertRaises(RuntimeError):
                AD.propose(stripped)
            AD._verify = original
            self.assertEqual(_signature(stripped), before)
            self.assertEqual([n for n in FreeCAD.listDocuments() if n.startswith("SketchDiagnosisScratch")], [])
            with self.assertRaises(AD.AutoDefineError):
                AD.propose(stripped, keep=[999])
            self.assertEqual(_signature(stripped), before)
        finally:
            AD._verify = original
            self.doc.removeObject(stripped.Name)

    def testHundredGeometriesWithinThirtySeconds(self):
        for n in (10, 100):
            sk = Corpus.scale_sketch(self.doc, n)
            try:
                t0 = time.time()
                res, problems = _checked(self.doc, sk)
                dt = time.time() - t0
                self.stats["seconds/%d" % n] = dt
                self.assertEqual(problems, [])
                self.assertTrue(res["candidates"], res["unclosed"])
                self.assertLess(dt, SPEED_LIMIT)
            finally:
                self.doc.removeObject(sk.Name)

    # -- check 9 helpers: tolerances ---------------------------------------------------------------
    def testNoRelationIsClaimedAcrossAGapBelowTheMovementBar(self):
        # a line 5e-7 mm off tangent to a circle, two radii 5e-7 apart, end points 5e-7 apart,
        # a point 5e-7 beside a line: all closer than the movement bar of 1e-6 mm, farther than the
        # relation tolerance of 1e-7 mm.  Claiming any of them would still pass the movement check.
        gap = 5e-7
        sk = _bare(
            self.doc,
            "NearMiss",
            [
                _circle((10, 10), 4.0),  # 0
                _line((0, 14 + gap), (20, 14 + gap)),  # 1 tangent on top, gap above
                _circle((30, 10), 4.0 + gap),  # 2 radius 4+gap
                _line((40, 0), (50, 0)),  # 3
                _line((50 + gap, 0), (50, 10)),  # 4 starts gap away from the end of 3
                _line((60, 0), (70, 0)),  # 5
                _line((65, gap), (65, 8)),  # 6 starts gap above the middle of 5
            ],
        )
        try:
            res, problems = _checked(self.doc, sk)
            self.assertEqual(problems, [])
            self.assertTrue(res["candidates"])
            self.assertNotIn((0, 1), _pairs(res, "Tangent"))
            self.assertNotIn((0, 2), _pairs(res, "Equal"))
            self.assertNotIn((3, 4), _pairs(res, "Coincident"))
            for cand in res["candidates"]:
                for c in cand["constraints"]:
                    if c["type"] == "PointOnObject":
                        self.assertNotEqual((c["args"][0], c["args"][2]), (6, 5))
                    if c["type"] == "Equal":
                        self.assertNotEqual(sorted(c["args"]), [0, 2])
        finally:
            self.doc.removeObject(sk.Name)

    def testRelationsThatHoldAreClaimed(self):
        sk = _bare(
            self.doc,
            "Exact",
            [
                _circle((10, 10), 4.0),
                _line((0, 14), (20, 14)),
                _circle((30, 10), 4.0),
                _line((40, 0), (50, 0)),
                _line((50, 0), (50, 10)),
            ],
        )
        try:
            res, problems = _checked(self.doc, sk)
            self.assertEqual(problems, [])
            first = res["candidates"][0]
            types = {(c["type"], tuple(sorted(c["args"][:2]))) for c in first["constraints"]}
            self.assertIn(("Tangent", (0, 1)), types)
            self.assertIn(("Equal", (0, 2)), types)
            self.assertIn("Coincident", {c["type"] for c in first["constraints"]})
        finally:
            self.doc.removeObject(sk.Name)

    def testApplyCandidateRollsBackWhenItDoesNotFit(self):
        stripped, _ = Corpus.strip(self.doc, self.generated[0].sketch, "all")
        try:
            res = AD.propose(stripped)
            cand = res["candidates"][0]
            target = Corpus.clone(self.doc, stripped, "Target")
            target.addConstraint(Sketcher.Constraint("Distance", 0, 1.0))  # conflicts with the candidate's length
            before = _signature(target)
            with self.assertRaises(AD.AutoDefineError):
                AD.apply_candidate(target, cand)
            after = _signature(target)
            self.assertEqual(after["constraints"], before["constraints"])
            self.assertEqual(after["geometry"], before["geometry"])
            self.doc.removeObject(target.Name)
        finally:
            self.doc.removeObject(stripped.Name)

    def testResultIsJsonSerialisable(self):
        stripped, _ = Corpus.strip(self.doc, self.generated[0].sketch, "all")
        try:
            json.dumps(AD.propose(stripped))
        finally:
            self.doc.removeObject(stripped.Name)

    # -- check 10: licence and housekeeping ---------------------------------------------------------
    def testNewFilesCarrySPDXAndNoMachinePaths(self):
        here = os.path.dirname(os.path.abspath(__file__))
        files = [os.path.join(here, f) for f in (
            "SketchAutoDefineCorpus.py", "SketchAutoDefineOracle.py", "SketchAutoDefineRun.py", "TestSketchAutoDefine.py")]
        files.append(AD.__file__)
        for path in files:
            text = open(path, encoding="utf-8").read()
            self.assertIn("SPDX-License-Identifier: LGPL-2.1-or-later", text.splitlines()[0], path)
            self.assertIsNone(re.search("/" + "Users/|/" + "home/[a-z]", text), path)
