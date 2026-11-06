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

"""Planted defects for the fast path of ``qp_eq`` (G10 slice 5b3, check 9).

Each mutant is a byte-exact text replacement in the C++ source; the script patches, rebuilds through the harness
(it prints how long each build took, ccache makes them cheap), runs the classes of ``TestSketchBlockDrag`` and puts
the file back as it was.  A mutant is caught when a test fails or errors.  The table says which tests failed, and
for ``wrong_factor_falls_back`` also whether the answers stayed right (the residual check sends the call to the dense
path, so only the path counters notice).

    CADFORK_CTL=<cad-fork folder> python3 SketchBlockDragMutants.py <freecad worktree> [name ...]

The result table goes to ``block_drag_mutants.json`` in the current directory."""

import json
import os
import subprocess
import sys
import time

QP = "src/Mod/Sketcher/App/planegcs/qp_eq.cpp"
GCS = "src/Mod/Sketcher/App/planegcs/GCS.cpp"

# cheapest first; a mutant stops at the first failing class
TESTS = [
    "TestQpEqRefusals",
    "TestQpEqExact",
    "TestSmallSketchesUnchanged",
    "TestDragEqualsDense",
    "TestLargeBlockRigidity",
]
EXTRA = {"wrong_factor_falls_back": ["TestDragEqualsDense.test_same_return_code_as_often_as_a_noisy_dense_copy", "TestDragEqualsDense.test_same_geometry_as_often_as_a_noisy_dense_copy"]}

# (name, file, [(old, new), ...], what it plants)
MUTANTS = [
    ("threshold_off_by_one", QP, [("|| m < ns) {\n        return Why::Structure;", "|| m <= ns) {\n        return Why::Structure;")],
     "the fast path refuses the square case (as many rows as sparse columns), which is every plain block"),
    ("fallback_removed", QP, [("    gFallback.fetch_add(1, std::memory_order_relaxed);\n    return qp_eq_dense(H, g, A, c, x, Y, Z);", "    gFallback.fetch_add(1, std::memory_order_relaxed);\n    return -1;")],
     "a call the fast path gives up on is refused instead of going to the dense path"),
    ("rank_signal_ignored", QP, [("    if (ns + kr != m) {\n        return Why::RankMismatch;", "    if (false) {\n        return Why::RankMismatch;"), ("    if (nu + m != n) {\n        return Why::RankMismatch;", "    if (false) {\n        return Why::RankMismatch;")],
     "dependent rows are not noticed by the rank count"),
    ("residual_check_removed", QP, [("        if (!closeTo(out - v, absOut, v)) {\n            return Why::Residual;", "        if (false) {\n            return Why::Residual;"), ("    if (!closeTo(out + c, absOut, c)) {\n        return Why::Residual;", "    if (false) {\n        return Why::Residual;")],
     "neither A Y = I nor A x + c = 0 is checked"),
    ("dense_column_treated_as_sparse", QP, [("colStart[j + 1] - colStart[j] > kDenseColumnNnz", "colStart[j + 1] - colStart[j] > kDenseColumnNnz + 100000")],
     "no column counts as dense, so a frame column sits in a block of the sparse part"),
    ("schur_sign_flipped", QP, [("Atil(rows[ri], jj) -= A(rows[ri], j) * acc;", "Atil(rows[ri], jj) += A(rows[ri], j) * acc;")],
     "the Schur complement of the dense columns has a sign wrong"),
    ("permutation_dropped", QP, [("        Left.block(sIdx[k], 0, 1, nd) = -G.row(k);", "        Left.block(k, 0, 1, nd) = -G.row(k);")],
     "the rows of the sparse columns are placed without their permutation (caught only by systems whose dense columns are not last)"),
    ("null_column_wrong", QP, [("            Zraw.row(sIdx[k]) = Zs.row(k);", "            Zraw.row(sIdx[k]) = -Zs.row(k);")],
     "the sparse part of the null-space basis has the wrong sign"),
    ("y_left_stale", QP, [("        Y = std::move(Yf);\n        if (zWritten)", "        if (Y.rows() != n || Y.cols() != m) {\n            Y = std::move(Yf);\n        }\n        if (zWritten)")],
     "Y keeps the value of the first iteration, so every later lambda and correction uses a stale one"),
    ("lambda_from_wrong_y", GCS, [("lambda = Y.transpose() * (B * xdir + grad);", "lambda = Y.transpose() * (grad);")],
     "the multiplier estimate leaves out B x"),
    ("non_finite_passed_through", QP, [("                if (!std::isfinite(v)) {\n                    return Why::Input;\n                }\n", ""), ("    if (!c.allFinite() || !g.allFinite()) {\n        return Why::Input;\n    }\n", "")],
     "a nan or inf in A, c or g reaches the arithmetic"),
    ("small_systems_take_fast_path", QP, [("constexpr Index kFastMinParams = 192;", "constexpr Index kFastMinParams = 1;")],
     "the size threshold is gone, so small sketches try the fast path"),
    ("force_dense_ignored", QP, [("    if (forceFlag().load(std::memory_order_relaxed)) {", "    if (false && forceFlag().load(std::memory_order_relaxed)) {")],
     "the force-dense hook does nothing"),
    ("counter_wrong", QP, [("        gFast.fetch_add(1, std::memory_order_relaxed);\n        return 0;", "        gDense.fetch_add(1, std::memory_order_relaxed);\n        return 0;")],
     "a fast answer is counted as a dense one"),
    ("wrong_factor_falls_back", QP, [("pv[ri] = A(rows[ri], j) / nn;", "pv[ri] = A(rows[ri], j) / (nn * 1.0000001);")],
     "a planted wrong factor in the block pseudo-inverse: the residual check must send every call to the dense path"),
    ("qp_step_sign", QP, [("        x += Zo * y;", "        x -= Zo * y;")],
     "the null-space step is subtracted instead of added"),
]


def run(cmd, cwd, env=None, timeout=7200):
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def main(argv):
    tree = argv[1]
    only = set(argv[2:])
    ctl = os.environ["CADFORK_CTL"]
    env = dict(os.environ, FC_SRC=tree, CMAKE_BUILD_PARALLEL_LEVEL="4", QT_QPA_PLATFORM="offscreen")
    build = ["bash", "-c", 'source "%s/harness/env.sh" && nice "%s/harness/build.sh"' % (ctl, ctl)]
    binary = os.path.join(tree, "build", "release", "bin", "FreeCADCmd")
    rows = []

    def rebuild():
        t = time.time()
        b = run(build, ctl, env)
        return b.returncode == 0, time.time() - t

    def passes(case):
        r = run([binary, "-t", "SketcherTests.TestSketchBlockDrag." + case], "/tmp", env)
        return "\nOK" in r.stdout + r.stderr

    def tests():
        for t in TESTS:
            if not passes(t):
                return [t]
        return []

    rebuild()  # start from the unmutated source, whatever an earlier interrupted run left in the binary
    base_failed = [t for t in TESTS if not passes(t)]
    if base_failed:
        sys.exit("the unmutated tree fails %s; nothing was planted" % base_failed)

    for name, rel, edits, what in MUTANTS:
        if only and name not in only:
            continue
        path = os.path.join(tree, rel)
        with open(path, "rb") as fh:
            original = fh.read()
        mutated = original
        for old, new in edits:
            if old.encode() not in mutated:
                mutated = None
                break
            mutated = mutated.replace(old.encode(), new.encode(), 1)
        if mutated is None:
            rows.append({"mutant": name, "result": "pattern not found", "what": what})
            print(name, "PATTERN NOT FOUND", flush=True)
            continue
        with open(path, "wb") as fh:
            fh.write(mutated)
        try:
            ok, build_s = rebuild()
            if not ok:
                raise RuntimeError("the planted defect %s does not compile; a defect that cannot be built is not a result" % name)
            failed = tests()
            still_right = None
            if name in EXTRA:
                still_right = all(passes(c) for c in EXTRA[name])
        finally:
            with open(path, "wb") as fh:
                fh.write(original)
        row = {"mutant": name, "file": rel, "what": what, "build_seconds": round(build_s), "failing_tests": failed, "caught": bool(failed)}
        if still_right is not None:
            row["answers_still_equal_to_dense"] = still_right
        rows.append(row)
        print(name, "CAUGHT" if failed else "SURVIVED", failed, "build %ds" % build_s, "" if still_right is None else "answers equal: %s" % still_right, flush=True)
    rebuild()  # leave the tree built from the original source
    with open("block_drag_mutants.json", "w") as fh:
        json.dump(rows, fh, indent=1)
    print("caught %d of %d" % (sum(r.get("caught", False) for r in rows), len(rows)))


if __name__ == "__main__":
    main(sys.argv)
