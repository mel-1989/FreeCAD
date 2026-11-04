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

"""Planted defects for the solver core of G10 slice 3a (check 10).

Each mutant is a text replacement in one C++ file of the tree; the script
applies it, builds through the harness, runs the solver-core tests and puts the
file back.  A mutant is caught when at least one test fails or errors.

    python3 SolverCoreMutants.py <freecad worktree> [name ...]

The worktree is the one the harness builds (``FC_SRC``); it must be clean for
the files named below.  Builds run under ``nice`` through ``harness/build.sh``
of the cad-fork folder named by ``CADFORK_CTL``.  The result table is printed
and written to ``solver_core_mutants.json`` in the current directory."""

import json
import os
import subprocess
import sys

GCS = "src/Mod/Sketcher/App/planegcs/GCS.cpp"
SUB = "src/Mod/Sketcher/App/planegcs/SubSystem.cpp"
SKETCH = "src/Mod/Sketcher/App/Sketch.cpp"

TESTS = ["TestSketchSolverCore", "TestDegenerateLine", "TestPartitionedDiagnosis", "TestSubsystemStatus", "TestSolverScale"]

# (name, file, old text, new text, what it plants)
MUTANTS = [
    ("component_dropped", GCS, "        const int nr = static_cast<int>(comp.rows.size());\n        done[c].paramsNum = np;",
     "        const int nr = (c == 1) ? 0 : static_cast<int>(comp.rows.size());\n        done[c].paramsNum = np;",
     "the constraints of the second component are left out of the diagnosis"),
    ("wrong_column_map", GCS, "Jc(r, localCol[col]) = row.constr->grad(pdiag[col]);",
     "Jc(r, (localCol[col] + 7) % np) = row.constr->grad(pdiag[col]);",
     "a Jacobian entry goes to the neighbouring column"),
    ("threshold_inverted", GCS, "    if (static_cast<int>(pdiag.size()) < partitionThreshold) {\n        return false;",
     "    if (static_cast<int>(pdiag.size()) >= partitionThreshold) {\n        return false;",
     "small systems are partitioned and large ones are not"),
    ("groups_of_first_component_only", GCS, "        for (auto& g : groups) {\n            allGroups.push_back(std::move(g));",
     "        for (auto& g : groups) {\n            if (c > 0) { continue; }\n            allGroups.push_back(std::move(g));",
     "conflict groups of later components are lost"),
    ("dofs_off_by_one", GCS, "        dofs += done[c].paramsNum - done[c].rank;\n    }\n\n    if (!allGroups.empty()) {",
     "        dofs += done[c].paramsNum - done[c].rank + (c == 0 ? 1 : 0);\n    }\n\n    if (!allGroups.empty()) {",
     "the degrees of freedom are one too many"),
    ("status_overwritten", GCS, "        if (subStatus != SolveStatus::Success && status == SolveStatus::Success) {\n            status = subStatus;\n        }",
     "        status = subStatus;",
     "System::solve keeps only the last subsystem's status again"),
    ("degenerate_rule_on_any_line", SKETCH, "        if (oldLength < Precision::Confusion() && oldLength > 0.0) {\n            const double scale = std::max({std::abs(p1.x), std::abs(p1.y), 1.0});\n            const double length = std::max(oldLength, 8.0 * std::numeric_limits<double>::epsilon() * scale);\n            if (length < Precision::Confusion()) {",
     "        if (oldLength > 0.0) {\n            const double scale = std::max({std::abs(p1.x), std::abs(p1.y), 1.0});\n            const double length = std::max(oldLength, 8.0 * std::numeric_limits<double>::epsilon() * scale);\n            if (true) {",
     "the degenerate rule is applied to a line that was not degenerate"),
    ("order_by_address", SUB, "        SET_pD seen;\n        for (double* p : params) {\n            if (s2.count(p) != 0 && seen.insert(p).second) {\n                tmpplist.push_back(p);\n            }\n        }",
     "        SET_pD s1(params.begin(), params.end());\n        std::set_intersection(s1.begin(), s1.end(), s2.begin(), s2.end(), std::back_inserter(tmpplist));",
     "unknowns follow heap addresses again"),
    ("sparse_step_ignores_a_row", GCS, "    Jx.makeCompressed();\n    Eigen::VectorXd h_gn;",
     "    Eigen::SparseMatrix<double> Jy = Jx.topRows(Jx.rows() - 1);\n    Eigen::VectorXd fy = fx.head(fx.size() - 1);\n    Jx = Jy;\n    Jx.makeCompressed();\n    Eigen::VectorXd h_gn;\n    if (false) { (void)fy; }",
     "the sparse Gauss-Newton step drops the last row"),
    ("sparse_threshold_inverted", GCS, "    if (sparseSolveThreshold > 0 && xsize >= sparseSolveThreshold) {",
     "    if (sparseSolveThreshold > 0 && xsize < sparseSolveThreshold) {", "small subsystems take the sparse step and large ones the dense one"),
    ("sparse_jacobian_wrong_sign", SUB, "entries.emplace_back(i, static_cast<int>(p - pvals.data()), g);",
     "entries.emplace_back(i, static_cast<int>(p - pvals.data()), -g);", "the sparse Jacobian has the wrong sign"),
    ("full_rank_shortcut_always", GCS, "            if (dep.rank == nr) {", "            if (dep.rank >= nr - 1) {",
     "the transposed QR is skipped when one row is dependent"),
    ("elimination_always_skipped", GCS, "    if (!(skipEliminationWhenFullRank && rank == qrJ.cols())) {", "    if (skipEliminationWhenFullRank && false) {",
     "the elimination over R is skipped even when a column lies beyond the rank"),
    ("redundancy_solve_skipped", GCS, "            if (!part.hasGroups) {\n                continue;\n            }\n            std::vector<Constraint*> clistTmp;",
     "            if (true) {\n                continue;\n            }\n            std::vector<Constraint*> clistTmp;",
     "the per-component redundancy solve never runs"),
    ("zero_row_dropped", GCS, "            if (constr->getTag() >= 0) {\n                return false;  // a row of zeros: the whole-system path decides what that means\n            }\n            continue;",
     "            continue;", "a driving constraint with no unknowns is silently dropped from the partitioned diagnosis"),
    ("degenerate_direction_flipped", SKETCH, "p2 = p1 + oldDir * (length / oldLength);", "p2 = p1 - oldDir * (length / oldLength);",
     "a kept degenerate line points the opposite way"),
]


def run(cmd, cwd, env=None, timeout=3600):
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def main(argv):
    tree = argv[1]
    only = set(argv[2:])
    ctl = os.environ["CADFORK_CTL"]
    env = dict(os.environ, FC_SRC=tree, CMAKE_BUILD_PARALLEL_LEVEL="4")
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
            text = r.stdout + r.stderr
            if "\nOK" not in text:
                failed.append(t)
        return "ok", failed

    for name, rel, old, new, what in MUTANTS:
        if only and name not in only:
            continue
        path = os.path.join(tree, rel)
        # Bytes in and out, so the file comes back exactly as it was (Sketch.cpp has CRLF line ends).
        with open(path, "rb") as fh:
            original = fh.read()
        old_b, new_b = old.encode(), new.encode()
        if b"\r\n" in original:
            old_b, new_b = old_b.replace(b"\n", b"\r\n"), new_b.replace(b"\n", b"\r\n")
        if old_b not in original:
            rows.append({"mutant": name, "result": "pattern not found", "what": what})
            continue
        with open(path, "wb") as fh:
            fh.write(original.replace(old_b, new_b, 1))
        try:
            status, failed = build_and_test()
        finally:
            with open(path, "wb") as fh:
                fh.write(original)
        rows.append({"mutant": name, "file": rel, "what": what, "build": status, "failing_tests": failed, "caught": bool(failed) or status != "ok"})
        print(name, "CAUGHT" if rows[-1]["caught"] else "SURVIVED", failed, flush=True)
    build_and_test()  # leave the tree built from the original source
    with open("solver_core_mutants.json", "w") as fh:
        json.dump(rows, fh, indent=1)
    print("caught %d of %d" % (sum(r.get("caught", False) for r in rows), len(rows)))


if __name__ == "__main__":
    main(sys.argv)
