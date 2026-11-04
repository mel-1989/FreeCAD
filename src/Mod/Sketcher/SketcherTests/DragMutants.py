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

"""Planted defects for the drag half of G10 slice 3b (check 11).

Each mutant is a text replacement in one C++ file of the tree; the script
applies it, builds through the harness, runs the drag tests and puts the file
back byte for byte.  A mutant is caught when a test fails or errors.

    python3 DragMutants.py <freecad worktree> [name ...]

The worktree is the one the harness builds (``FC_SRC``) and must be clean for
the files named below.  ``CADFORK_CTL`` names the cad-fork folder whose
``harness/build.sh`` builds it.  The table is printed and written to
``drag_mutants.json`` in the current directory."""

import json
import os
import subprocess
import sys

GCS = "src/Mod/Sketcher/App/planegcs/GCS.cpp"
SKETCH = "src/Mod/Sketcher/App/Sketch.cpp"
OPS = "src/Mod/Sketcher/App/SketchObjectOperations.cpp"
PYIMP = "src/Mod/Sketcher/App/SketchObjectPyImp.cpp"

TESTS = ["TestDragSession", "TestDragBfgsGuard", "TestDragFallback", "TestDragEscape", "TestDragRestart", "TestDragSilentSuccess"]

# (name, file, old text, new text, what it plants)
MUTANTS = [
    ("binding_ignores_relative", PYIMP, "this->getSketchObjectPtr()->moveGeometriesTemporary(geoEltIds, v1, (relative > 0))",
     "this->getSketchObjectPtr()->moveGeometriesTemporary(geoEltIds, v1, false)", "moveGeometriesTemporary drops the relative flag"),
    ("release_keeps_session", OPS, "    solvedSketch.resetInitMove();  // reset solver point moving mechanism",
     "    // solvedSketch.resetInitMove();", "the release leaves the move session alive"),
    ("drag_mode_never_set", SKETCH, "                system.dragSolve = true;", "                system.dragSolve = false;",
     "the drag SQP runs the stock iteration and the escape never runs"),
    ("update_not_cautious", GCS, "            if (guarded && !(yTh > 0.)) {", "            if (false && !(yTh > 0.)) {",
     "the BFGS update is applied whatever the sign of the curvature"),
    ("update_skipped_when_positive", GCS, "            if (guarded && !(yTh > 0.)) {", "            if (guarded && (yTh > 0.)) {",
     "the BFGS update is skipped when the curvature is good"),
    # Equivalent under the cautious update: with B positive definite and A's residual zero the
    # numerator of mu is not positive, so the quotient is never +inf. Kept as defence in depth.
    ("mu_divides_by_zero", GCS, "            if (!guarded || resA.lpNorm<1>() > 0.) {", "            if (true) {",
     "the penalty parameter is updated with a zero denominator"),
    ("no_stock_fallback", GCS, "    if (guarded && status != SolveStatus::Success) {\n        guarded = false;",
     "    if (false && status != SolveStatus::Success) {\n        guarded = false;", "a failed guarded run is not repeated with the stock iteration"),
    ("escape_never_called", GCS, "            if (dragSolve && !isRedundantsolving && subStatus == SolveStatus::Success) {",
     "            if (false && !isRedundantsolving && subStatus == SolveStatus::Success) {", "the escape is never tried"),
    ("escape_memory_ignored", GCS, "    if (dragStallValid && dragStallCursor.size() == cursor.size()) {",
     "    if (false && dragStallCursor.size() == cursor.size()) {", "an escape that found nothing is searched for again on every move"),
    ("escape_accepts_any_result", GCS, "            if (ok && E < accept * Ebest) {", "            if (ok && E < (accept + 1e9) * Ebest) {",
     "a probe result is kept even when the gap did not fall"),
    ("escape_ignores_sudden_gap", GCS, "        if (gap0 > 2. * dragPrevGap + travel + slack) {",
     "        if (false) {", "an escape runs on a landing whose gap appeared at once, as S18 after its failed moves"),
    # Dropped, run 054: "if (reached) { dragLockClean = true;" only matters when a drag meets a sudden gap
    # (which switches the escape off) and later a dead-point lock the escape must release. No sketch in
    # the 20 scenarios does both: S18 is the only one with a sudden gap and its path has no later lock,
    # and a grid of single cursor jumps from the start state of each of S01, S03, S05 to S08, S19 and S20
    # found no landing farther from the cursor than the jump (S01: three, by under 1 mm). Planting
    # it would need a sketch built for the purpose, which is not a defect any drag has shown.
    ("restart_never_tried", SKETCH, "    if (!stuck || dragRefIsLastGood || dragRetrying", "    if (!stuck || true || dragRefIsLastGood || dragRetrying",
     "a stuck move is never solved again from the last state that met the cursor"),
    ("restart_from_the_stuck_state", SKETCH, "        *Parameters[i] = dragLastGood[i];\n    }\n    if (initMove", "        *Parameters[i] = first[i];\n    }\n    if (initMove",
     "the restart saves the stuck state as its reference instead of the last good one"),
    # Dropped, run 054: keeping a second solve that ends farther from the cursor than the first
    # (the `gap2 < gap1` test of Sketch::moveGeometries) changes one cell of the 20 scenarios, and
    # for the better (S09 at 10 mm: 6 failures to 5); no scenario reaches a second solve that is worse.
    ("diverged_result_kept", SKETCH, "if (GCSsys.dragGap > stay + 10. * extent) {", "if (GCSsys.dragGap > stay + 1e12 * extent) {",
     "a move that ends ten sketch sizes from the cursor is kept"),
    ("failed_move_keeps_reference_state", SKETCH, "    if (status != GCS::SolveStatus::Success) {\n        for (size_t i = 0; i < Parameters.size(); ++i) {\n            *Parameters[i] = before[i];",
     "    if (false) {\n        for (size_t i = 0; i < Parameters.size(); ++i) {\n            *Parameters[i] = before[i];", "a failed move leaves the unknowns at the reference, not at the state before the move"),
    ("extent_is_largest_coordinate", SKETCH, "        GCSsys.dragExtent = std::max(hi[0] - lo[0], hi[1] - lo[1]);", "        GCSsys.dragExtent = std::max(std::abs(hi[0]), std::abs(hi[1]));",
     "the sketch's size is its largest coordinate, which a sketch far from the origin overstates"),
    ("origin_never_tried", GCS, "    if (!dragOriginFailed && !dragOrigin.empty()\n",
     "    if (false && !dragOriginFailed && !dragOrigin.empty()\n", "the state the drag began in is never tried before the probes"),
    # Dropped, run 055: "dragOriginFailed = true" after a failed try of the drag's first state
    # only saves one solve per search of a lock (a version that retried it as the cursor moved ran
    # S06 at 1.7 times the baseline's time per move, this one at 0.7); the trajectories are identical, so no test short of a
    # timing bar sees it, and the timing bars of check 10 are measured in the Exhibit 1 run.
    ("origin_replaced_by_every_init", SKETCH, "    GCSsys.dragOriginPending = !isInitMove;", "    GCSsys.dragOriginPending = true;",
     "the editor's re-initialisation while dragging replaces the state the drag began in"),
    ("origin_never_captured", SKETCH, "    GCSsys.dragOriginPending = !isInitMove;", "    GCSsys.dragOriginPending = false;",
     "the state the drag began in is never saved"),
    ("probes_held_back_whatever_the_gap", GCS, "    const bool settled = viaShortcut && Ebest == Eshortcut && dragProbeGap >= 0.\n        && gapNow > 0.5 * dragProbeGap;",
     "    const bool settled = viaShortcut && Ebest == Eshortcut;", "the probes stay off after a settled shortcut even when the cursor comes back toward reach"),
    ("probes_never_held_back", GCS, "    const bool settled = viaShortcut && Ebest == Eshortcut && dragProbeGap >= 0.\n        && gapNow > 0.5 * dragProbeGap;",
     "    const bool settled = false && viaShortcut && Ebest == Eshortcut;", "the probes run on every move after a shortcut, as before run 055"),
    ("escape_perturbs_nothing", GCS, "                xs[j] += fraction * extent * std::sin(2.399963229728653 * (j + 1));",
     "                xs[j] += 0. * fraction * extent * std::sin(2.399963229728653 * (j + 1));", "the probes do not move the unknowns"),
]


def run(cmd, cwd, env=None, timeout=3600):
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def main(argv):
    tree = argv[1]
    only = set(argv[2:])
    ctl = os.environ["CADFORK_CTL"]
    env = dict(os.environ, FC_SRC=tree, CMAKE_BUILD_PARALLEL_LEVEL="4", QT_QPA_PLATFORM="offscreen")
    build = ["bash", "-c", 'source "%s/harness/env.sh" && nice "%s/harness/build.sh"' % (ctl, ctl)]
    binary = os.path.join(tree, "build", "release", "bin", "FreeCADCmd")
    rows = []

    def build_and_test():
        b = run(build, ctl, env)
        if b.returncode != 0:
            return "build failed", []
        failed = []
        for t in TESTS:
            r = run([binary, "-t", "TestSketcherApp." + t], tree, env)
            if "\nOK" not in r.stdout + r.stderr:
                failed.append(t)
        return "ok", failed

    for name, rel, old, new, what in MUTANTS:
        if only and name not in only:
            continue
        path = os.path.join(tree, rel)
        # Bytes in and out, so the file comes back exactly as it was (CRLF files included).
        with open(path, "rb") as fh:
            original = fh.read()
        old_b, new_b = old.encode(), new.encode()
        if b"\r\n" in original:
            old_b, new_b = old_b.replace(b"\n", b"\r\n"), new_b.replace(b"\n", b"\r\n")
        if original.count(old_b) != 1:
            rows.append({"mutant": name, "result": "pattern not found once", "what": what})
            print(name, "PATTERN NOT FOUND ONCE", flush=True)
            continue
        with open(path, "wb") as fh:
            fh.write(original.replace(old_b, new_b, 1))
        try:
            status, failed = build_and_test()
        finally:
            with open(path, "wb") as fh:
                fh.write(original)
        rows.append({"mutant": name, "file": rel, "what": what, "build": status, "failing_tests": failed,
                     "caught": bool(failed)})
        print(name, "CAUGHT" if rows[-1]["caught"] else "SURVIVED", failed, flush=True)
    build_and_test()  # leave the tree built from the original source
    with open("drag_mutants.json", "w") as fh:
        json.dump(rows, fh, indent=1)
    print("caught %d of %d" % (sum(r.get("caught", False) for r in rows), len(rows)))


if __name__ == "__main__":
    main(sys.argv)
