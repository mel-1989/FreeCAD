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

"""Mutation check for ``SketcherAutoDefine`` (brief check 9).

Each planted defect is a text replacement in a copy of the module.  The copy
is put first on ``sys.path`` of a fresh ``FreeCADCmd`` run of
``TestSketchAutoDefine`` and the run stops at the first failing test.  A
mutant counts as caught only when a test method fails or raises; a crash in
``setUpClass`` (the mutant broke the corpus build before any test ran) is
reported separately as ``crash`` and does not count.  Options are environment
variables, because ``FreeCADCmd`` treats every argument as a file to open::

    MUT_OUT=out_dir [MUT_ONLY="name ..."] [MUT_DRY=1] FreeCADCmd SketchAutoDefineMutants.py

``MUT_DRY`` only checks that every pattern matches the module once.
"""

import json
import os
import subprocess
import sys
import tempfile

import FreeCAD

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(HERE)
M = []


def mutant(name, desc, old, new):
    if isinstance(old, str):
        old, new = [old], [new]
    M.append((name, desc, old, new))


GOOD_FACTS = '{"max_move": 0.0, "dof": 0, "batches": list(b.batches), "runs": 1}'

mutant("unverified_candidate", "a candidate is returned without the fresh-copy verification", "facts = _verify(scratch, b.specs, b.batches, keep)", "facts = " + GOOD_FACTS)
mutant(
    "verified_on_real_sketch",
    "the verification runs on the user's sketch instead of a fresh copy",
    ["    fresh = scratch.copy()\n    try:\n        start = list(fresh.Geometry)", "    finally:\n        _discard(fresh)\n"],
    ["    fresh = scratch._source\n    try:\n        start = list(fresh.Geometry)", "    finally:\n        pass\n"],
)
mutant("lock_every_point", "no relations are added, so dimensions pin every point", "            b.add_relations(rel[phase])", "            pass")
mutant("keep_constraint_dropped", "applying a candidate deletes the first existing constraint", '            return {"dof": st.dof, "added": len(specs)}', '            if n0 > 0:\n                sketch.delConstraint(0)\n            return {"dof": st.dof, "added": len(specs)}')
mutant("tangent_across_a_gap", "line-circle tangency is claimed up to 1e-5 mm", "if abs(_dist(foot, A.c) - A.r) <= LENGTH_TOLERANCE and", "if abs(_dist(foot, A.c) - A.r) <= 1e-5 and")
mutant(
    "redundant_relation_left_in",
    "redundant constraints pass the clean test and the DOF-drop test",
    ["    return st.code == 0 and not (st.conflicts or st.redundant or st.partial or st.malformed)", "            and st.dof <= self.dof - len(specs)\n"],
    ["    return st.code in (0, -2) and not (st.conflicts or st.malformed)", ""],
)
mutant("equal_beyond_tolerance", "equal lengths and radii are claimed up to 1e-5 mm", "            if abs(v1 - v2) <= LENGTH_TOLERANCE:", "            if abs(v1 - v2) <= 1e-5:")
mutant(
    "dof_off_by_one",
    "a candidate with one DOF left passes verification and is offered",
    ["        if not _clean(st) or st.dof != 0:", "        if not _clean(again) or again.dof != 0:"],
    ["        if not _clean(st) or st.dof > 1:", "        if not _clean(again) or again.dof > 1:"],
)
mutant("displaced_point_accepted", "relations 1e-4 mm off are claimed and a movement of 1e-3 mm is accepted", ["LENGTH_TOLERANCE = 1e-7", "MOVE_TOLERANCE = 1e-6"], ["LENGTH_TOLERANCE = 1e-4", "MOVE_TOLERANCE = 1e-3"])
mutant("duplicate_candidate", "identical constraint sets are offered twice", "                if key in seen:\n                    continue", "                if False:\n                    continue")
mutant(
    "false_candidate_when_unclosed",
    "a sketch that cannot be closed gets a candidate (DOF check removed in the build and the verification)",
    ['                if b.dof > 0:\n                    result["unclosed"]', "        if not _clean(st) or st.dof != 0:", "        if not _clean(again) or again.dof != 0:"],
    ['                if False:\n                    result["unclosed"]', "        if not _clean(st):", "        if not _clean(again):"],
)
mutant("construction_geometry_dimensioned", "construction geometry is dimensioned like drawn geometry", "    drawn = [m for m in model if not m.construction]\n    helper = [m for m in model if m.construction]", "    drawn = list(model)\n    helper = []")
mutant("partial_redundancy_ignored", "partially redundant constraints pass the clean test and the DOF-drop test", ["st.redundant or st.partial or st.malformed", "            and st.dof <= self.dof - len(specs)\n"], ["st.redundant or st.malformed", ""])
mutant("no_rollback", "a candidate that does not fit stays on the sketch", "        roll_back()\n    raise AutoDefineError", "        pass\n    raise AutoDefineError")
mutant("scratch_left_open", "the scratch document is not closed", "        scratch.close()", "        pass")
mutant(
    "propose_on_real_sketch",
    "propose works on the user's sketch instead of a copy",
    ["        probe = scratch.copy()\n        stored", "        drift = _max_move(stored, _geometry_signature(probe))\n        _discard(probe)\n"],
    ["        probe = sketch\n        stored", "        drift = _max_move(stored, _geometry_signature(probe))\n"],
)
mutant("keep_not_validated", "a keep index that does not exist is accepted", "    if any((not isinstance(k, int)) or k < 0 or k >= ncons for k in keep):", "    if False:")
mutant("prefer_ignored", "prefer is ignored", "        names = [prefer] + [s for s in SCHEMES if s != prefer]", "        names = list(SCHEMES)")
mutant("max_candidates_ignored", "max_candidates is ignored", '            if len(result["candidates"]) >= max_candidates:\n                break', '            if False:\n                break')
mutant("verify_skips_move", "verification ignores point movement", "        if move > MOVE_TOLERANCE:\n            return None\n        again", "        again")
mutant("verify_skips_rows", "verification ignores that existing rows are unchanged", "        if len(rows) != len(rows0) + len(specs) or rows[: len(rows0)] != rows0:\n            return None\n", "")
mutant("apply_ignores_dof", "apply_candidate accepts DOF>0", ["ok = _clean(st) and st.dof == 0 and", "        if not (_clean(st) and st.dof == 0):\n            return False\n    return True"], ["ok = _clean(st) and", "        if not _clean(st):\n            return False\n    return True"])
mutant("apply_ignores_move", "apply_candidate accepts moved geometry", " and _max_move(sig0, _geometry_signature(sketch)) <= MOVE_TOLERANCE\n            if ok and cheap", "\n            if ok and cheap")
mutant("no_parallel", "parallel detection removed", 'ph["parallel"].append(("Parallel", a.i, b.i))', "pass")
mutant("no_perpendicular", "perpendicular detection removed", 'ph["parallel"].append(("Perpendicular", groups[a][0].i, groups[b][0].i))', "pass")
mutant("no_equal", "equal detection removed", 'ph["equal"].append(("Equal", i1, i2))', "pass")
mutant("no_edge_tangent", "edge tangent detection removed", 'ph["tangent"].append(spec or ("Tangent", A.i, B.i))', "pass")
mutant("no_second_solve", "second solve agreement dropped", "        if not _clean(again) or again.dof != 0:\n            return None\n", "")
mutant("keep_unchecked", "verification of keep removed", "        if any(k >= len(rows0) or rows[k] != rows0[k] for k in keep):\n            return None\n", "")
mutant("no_symmetric", "symmetry detection removed", 'found.append(("Symmetric", g, p, g2, p2))', "pass")
mutant("no_stability_repeats", "a candidate is verified once", "STABILITY_RUNS = 3", "STABILITY_RUNS = 0")
mutant("apply_tries_once", "apply_candidate does not try again after a solver miss", "APPLY_ATTEMPTS = 3", "APPLY_ATTEMPTS = 1")
mutant("apply_retry_not_nudged", "the retry in apply_candidate starts from the same geometry as the first try", "NUDGE if attempt else 0.0", "0.0")
mutant(
    "verify_nudge_fallback_restored",
    "verification again accepts a plan that only solves from a nudged start",
    '    facts = _verify_once(scratch, specs, batches, keep, 0.0)\n    if facts is not None:\n        facts["nudge"] = 0.0\n    return facts',
    '    for nudge in (0.0, NUDGE):\n        facts = _verify_once(scratch, specs, batches, keep, nudge)\n        if facts is not None:\n            facts["nudge"] = nudge\n            return facts\n    return None',
)
mutant("verify_gate_removed", "verification skips the re-solve gate", "        if cheap and not (_stable(fresh) and _serialised_stable(fresh)):\n            return None", "        if False:\n            return None")
mutant("apply_gate_removed", "apply_candidate skips the re-solve gate", "            if ok and cheap:", "            if False:")
mutant("gate_serialised_copy_dropped", "the gate solves only the sketch itself, not a serialised copy", "ok = _stable(sketch) and _serialised_stable(sketch)", "ok = _stable(sketch)")
mutant("gate_single_solve", "the gate asks for one clean solve only", "GATE_RUNS = 10", "GATE_RUNS = 1")
mutant("gate_always_passes", "the gate accepts a -1 on any solve", "        if not (_clean(st) and st.dof == 0):\n            return False\n    return True", "        pass\n    return True")
mutant("nudge_not_applied", "the nudge moves nothing", "            g.translate(FreeCAD.Vector(dx, dy, 0))", "            pass")
mutant("console_left_silenced", "the console categories are not put back", "            FreeCAD.Console.SetStatus(name, kind, True)", "            pass")
mutant("console_not_silenced", "the console is not silenced during propose", '                    FreeCAD.Console.SetStatus(name, kind, False)\n', "                    pass\n")
mutant("stale_geometry_worked_on", "stored geometry that disagrees with its constraints is worked on", "        if drift > MOVE_TOLERANCE:\n", "        if False:\n")
mutant("zero_candidates_accepted", "max_candidates=0 is accepted", "or max_candidates < 1:", "or False:")
mutant("generic_reason_only", "the reason does not name the geometry kind", "    if parts:\n        return", "    if False:\n        return")
mutant("near_axis_length", "a nearly axis-parallel line gets a length again", "NEAR_AXIS = 0.03", "NEAR_AXIS = 0.0")

RUNNER = """
import sys, unittest, json
mutdir, srcdir, outfile = sys.argv[-3:]
sys.path.insert(0, mutdir)
sys.path.insert(1, srcdir)
import FreeCAD
import SketcherAutoDefine
assert SketcherAutoDefine.__file__.startswith(mutdir), SketcherAutoDefine.__file__
from SketcherTests.TestSketchAutoDefine import TestSketchAutoDefine
names = unittest.defaultTestLoader.getTestCaseNames(TestSketchAutoDefine)
suite = unittest.TestSuite(TestSketchAutoDefine(n) for n in names)
res = unittest.TextTestRunner(verbosity=0, stream=open("/dev/null", "w"), failfast=True).run(suite)
def name(t):
    return t.id().split(".")[-1] if hasattr(t, "id") else str(t)
setup = [str(t) for t, _ in res.errors if "setUpClass" in str(t) or "tearDownClass" in str(t)]
failed = [name(t) for t, _ in res.failures]
raised = [name(t) for t, _ in res.errors if not ("setUpClass" in str(t) or "tearDownClass" in str(t))]
json.dump({"ran": res.testsRun, "failed": failed, "raised": raised, "setup_crash": setup}, open(outfile, "w"))
"""


def main():
    out = os.environ["MUT_OUT"]
    only = os.environ.get("MUT_ONLY", "").split() or None
    fc = os.environ.get("FC") or os.path.join(FreeCAD.getHomePath(), "bin", "FreeCADCmd")
    src = open(os.path.join(SRC_DIR, "SketcherAutoDefine.py"), encoding="utf-8").read()
    os.makedirs(out, exist_ok=True)
    rows = []
    for name, desc, old, new in M:
        if only and name not in only:
            continue
        bad = [o for o in old if src.count(o) != 1]
        if bad:
            rows.append({"mutant": name, "description": desc, "status": "pattern", "error": "pattern found %d times: %r" % (src.count(bad[0]), bad[0][:60])})
            print(name, "PATTERN ERROR", flush=True)
            continue
        if os.environ.get("MUT_DRY"):
            print(name, "pattern ok", flush=True)
            continue
        text = src
        for o, n in zip(old, new):
            text = text.replace(o, n)
        d = tempfile.mkdtemp(prefix="mutant_")
        open(os.path.join(d, "SketcherAutoDefine.py"), "w", encoding="utf-8").write(text)
        open(os.path.join(d, "run.py"), "w").write(RUNNER)
        rf = os.path.join(d, "result.json")
        p = subprocess.run([fc, os.path.join(d, "run.py"), d, SRC_DIR, rf], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            r = json.load(open(rf))
        except Exception:
            r = {"ran": 0, "failed": [], "raised": [], "setup_crash": ["<run crashed> " + p.stdout[-200:]]}
        caught = bool(r["failed"] or r["raised"])
        status = "caught" if caught else ("crash" if r["setup_crash"] else "SURVIVED")
        rows.append({"mutant": name, "description": desc, "status": status, "failed": r["failed"], "raised": r["raised"], "setup_crash": r["setup_crash"]})
        print(name, status, r["failed"] or r["raised"] or r["setup_crash"], flush=True)
        json.dump(rows, open(os.path.join(out, "mutants.json"), "w"), indent=1)
    if os.environ.get("MUT_DRY"):
        return
    caught = sum(1 for r in rows if r["status"] == "caught")
    with open(os.path.join(out, "mutants.md"), "w") as fh:
        fh.write("| # | mutant | planted defect | result | first failing test |\n|---|---|---|---|---|\n")
        for i, r in enumerate(rows, 1):
            fh.write("| %d | %s | %s | %s | %s |\n" % (i, r["mutant"], r["description"], r["status"], ", ".join(r.get("failed", []) + r.get("raised", [])) or r.get("error", "")))
        fh.write("\n%d of %d mutants caught by a failing test; %d crashed the set-up only.\n" % (caught, len(rows), sum(1 for r in rows if r["status"] == "crash")))
    print("%d of %d caught" % (caught, len(rows)))


if os.environ.get("MUT_OUT") and __name__ in ("__main__", "SketchAutoDefineMutants"):  # FreeCADCmd names a script run from the command line after its file
    main()
