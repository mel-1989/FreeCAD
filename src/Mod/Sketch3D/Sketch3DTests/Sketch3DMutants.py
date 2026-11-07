# SPDX-License-Identifier: LGPL-2.1-or-later

"""Planted defects for Sketch3D (G10 slice 6a, check 11).

Each mutant is a text replacement in one C++ file of the module. The script applies it (bytes in,
bytes out), builds through the harness, runs the Sketch3D tests without their two slow corpora
(SKETCH3D_QUICK) and puts the file back. A mutant is caught when at least one test fails or
errors. A mutant that does not build is reported as such and not counted as caught.

    CADFORK_CTL=<cad-fork folder> python3 Sketch3DMutants.py <freecad worktree> [name ...]

The result table is printed and written to sketch3d_mutants.json in the current directory."""

import json
import os
import subprocess
import sys

REL = "src/Mod/Sketch3D/App/Relations.cpp"
SOLVE = "src/Mod/Sketch3D/App/Solve3D.cpp"
APP = "src/Mod/Sketch3D/App/AppSketch3D.cpp"
HDR = "src/Mod/Sketch3D/App/Solve3D.h"
GCS = "src/Mod/Sketcher/App/planegcs/GCS.cpp"

# (name, file, old text, new text, what it plants)
MUTANTS = [
    ("fixed_coordinate_sign", REL, "return x[axis] - value;", "return x[axis] + value;", "fixed coordinate: sign of the value"),
    ("coincident_wrong_axis", REL, "return x[row] - x[3 + row];", "return x[row] - x[3 + (row + 1) % 3];", "coincident: second point read on the next axis"),
    ("point_on_line_offset_from_direction", REL, "return dot(row == 0 ? u1 : u2, sub(p, a));", "return dot(row == 0 ? u1 : u2, sub(p, sub(b, a)));", "point on line: offset taken from the line direction instead of its start"),
    ("midpoint_operand_swap", REL, "return x[row] - 0.5 * (x[3 + row] + x[6 + row]);", "return x[3 + row] - 0.5 * (x[row] + x[6 + row]);", "midpoint: point and line end swapped"),
    ("point_on_plane_dropped_term", REL, "return dot(n, sub(pt(x, 0), q0));", "return dot(n, pt(x, 0));", "point on plane: the plane's point dropped"),
    ("distance_pp_sign", REL, "return nrm(sub(pt(x, 0), pt(x, 1))) - value;", "return nrm(sub(pt(x, 0), pt(x, 1))) + value;", "point distance: sign of the value"),
    ("distance_pl_dropped_term", REL, "return nrm(cross(sub(p, a), d)) / nrm(d) - value;", "return nrm(cross(sub(p, a), d)) - value;", "point to line distance: division by the line length dropped"),
    ("distance_pplane_one_sided", REL, "return dsqrt(s * s + EPS2) - value;", "return s - value;", "point to plane distance: only the positive side"),
    ("midpoint_dropped_term", REL, "return x[row] - 0.5 * (x[3 + row] + x[6 + row]);", "return x[row] - 0.5 * x[3 + row];", "midpoint: one end dropped"),
    ("length_dropped_term", REL, "return nrm(sub(pt(x, 1), pt(x, 0))) - value;", "return nrm(sub(pt(x, 1), pt(x, 0)));", "length: the value dropped"),
    ("equal_length_wrong_point", REL, "- nrm(sub(pt(x, 3), pt(x, 2)));", "- nrm(sub(pt(x, 3), pt(x, 1)));", "equal length: second line starts at the wrong point"),
    ("along_axis_wrong_axis", REL, "const int c = row < k ? row : row + 1;", "const int c = row + 0 * k;", "along axis: the axis itself is constrained"),
    ("parallel_one_row", REL, "perpBasis(sub(pt(x, 1), pt(x, 0)), u1, u2);\n            return dot(row == 0 ? u1 : u2, sub(pt(x, 3), pt(x, 2)));", "perpBasis(sub(pt(x, 1), pt(x, 0)), u1, u2);\n            return dot(u1, sub(pt(x, 3), pt(x, 2)));", "parallel: both rows are the first basis vector"),
    ("parallel_three_rows", REL, '{"parallel", "ll", false, 2},', '{"parallel", "ll", false, 3},', "parallel: declared with three rows"),
    ("perpendicular_self_dot", REL, "return dot(v, w) / nrm(v);", "return dot(v, v) / nrm(v) + 0.0 * dot(w, w);", "perpendicular: dot of the first line with itself"),
    ("parallel_plane_wrong_vector", REL, "return dot(sub(pt(x, 1), pt(x, 0)), n);", "return dot(sub(pt(x, 1), pt(x, 0)), q0);", "line parallel to plane: dot with the plane point"),
    ("normal_plane_one_row", REL, "return dot(row == 0 ? w1 : w2, sub(pt(x, 1), pt(x, 0)));", "return dot(w1, sub(pt(x, 1), pt(x, 0)));", "line normal to plane: both rows are the first basis vector"),
    ("angle_sin_for_cos", REL, "- std::cos(value);", "- std::sin(value);", "angle: sine of the target"),
    ("grad_doubled", REL, "x[i] = {*pvec[i], pvec[i] == param ? 1.0 : 0.0};", "x[i] = {*pvec[i], pvec[i] == param ? 2.0 : 0.0};", "gradient: derivative direction doubled"),
    ("grad_second_point_lost", REL, "x[i] = {*pvec[i], pvec[i] == param ? 1.0 : 0.0};", "x[i] = {*pvec[i], (pvec[i] == param && i < 3) ? 1.0 : 0.0};", "gradient: nothing for any point after the first"),
    ("grad_scale_dropped", REL, "return scale * evalRow<D>(kindValue, row, axis, value, plane, x).d;", "return -evalRow<D>(kindValue, row, axis, value, plane, x).d;", "gradient: sign flipped"),
    ("plane_normal_not_normalised", SOLVE, "d.n[i] = s.normal[i] / len;", "d.n[i] = s.normal[i] + 0.0 * len;", "plane normal used as given"),
    ("driving_ignored", SOLVE, "if (!spec.constraints[ci].driving) {\n                own[ci] = std::move(rows);", "if (false) {\n                own[ci] = std::move(rows);", "a constraint with driving false is solved like any other"),
    ("tag_lost", SOLVE, "std::make_unique<Rel>(cs.kind, r, axis, cs.value, plane, params, ci + 1)", "std::make_unique<Rel>(cs.kind, r, axis, cs.value, plane, params, 1)", "every row carries tag 1"),
    ("user_tag_lost", SOLVE, "out.insert(spec.constraints[t - 1].tag);", "out.insert(t);", "diagnosis tags returned as positions, not as the user's tags"),
    ("return_code_ignored", SOLVE, "else if (residual <= SuccessResidual && allFinite) {", "else if (allFinite) {", "success reported whatever the residual"),
    ("static_conflict_as_redundant", SOLVE, "if (mf->residualOf(ci) > SuccessResidual) {\n                conf.insert", "if (false) {\n                conf.insert", "a wrong relation between fixed points counts as redundant"),
    ("dof_from_row_count", SOLVE, "res.dof = md->sys.dofsNumber();", "res.dof = unknownCount - systemRows;", "dof taken as unknowns minus rows"),
    ("axis_operand_reversed", SOLVE, "default:\n                axis = o;", "default:\n                axis = 2 - o;", "fixed coordinate: axis x and z swapped"),
    ("collapse_refusal_off", SOLVE, "if (!collapsed.empty()) {\n        res.code = 3;", "if (false) {\n        res.code = 3;", "a collapsed line is reported as a success"),
    ("collapse_tags_not_named", SOLVE, "res.collapsed = collapsed;\n", "", "code 3 without the tags of the collapsed line's relations"),
    ("collapse_start_not_restored", SOLVE, "res.reason = \"a line carrying a direction relation shrank to zero length; the relations \"\n                     \"hold only there\";\n        coords = start;", "res.reason = \"a line carrying a direction relation shrank to zero length; the relations \"\n                     \"hold only there\";", "code 3 returns the collapsed coordinates"),
    ("collapse_threshold_zero", HDR, "constexpr double CollapseLength = 1e-6;", "constexpr double CollapseLength = 1e-300;", "a line has to reach exactly zero to count as collapsed"),
    ("collapse_retry_off", SOLVE, "if (first.code == 0) {\n            Spec second = spec;", "if (false) {\n            Spec second = spec;", "the retry from the direction rows alone never runs"),
    ("nonconvergence_check_off", SOLVE, "                    nonConvergence = true;\n                    break;", "                    break;", "a conflict is named although a perturbed start solves the system"),
    ("nonconvergence_ignores_collapse", SOLVE, "if (collapsedTags(spec, start, flatAgain).empty()) {\n                    nonConvergence = true;", "if (true) {\n                    nonConvergence = true;", "a perturbed start that collapsed a line counts as a solution"),
    ("nonconvergence_reason_dropped", SOLVE, "    if (res.code != 0 && res.reason.empty() && res.conflicting.empty()) {", "    if (false) {", "a failure with no conflict named gives no reason"),
    ("strict_flag_off", APP, "if (!o || !PyBool_Check(o)) {\n        raiseSpec(position, what + \" must be True or False\");", "if (!o) {\n        raiseSpec(position, what + \" must be True or False\");", "'yes' accepted for a flag"),
    ("strict_keys_off", APP, "        if (!known) {\n            raiseSpec(position, what + \": unknown key", "        if (false) {\n            raiseSpec(position, what + \": unknown key", "an unknown key accepted"),
    ("strict_negative_tag_off", APP, "if (cs.tag < 0) {", "if (cs.tag < -1000000000) {", "a negative tag accepted"),
    # grid speed (check 9): the tall Gauss-Newton step, the diagnosis route and its guards
    ("grid_tall_step_off", SOLVE, "return (rows > cols && cols >= 200) ? GCS::SparseNormalLdlt : GCS::LeastNormLdlt;", "return (rows > cols + 100000 && cols >= 200) ? GCS::SparseNormalLdlt : GCS::LeastNormLdlt;", "a tall Jacobian takes the J J^T step again (a dense fallback on every step of a grid)"),
    ("grid_diagnosis_order_off", SOLVE, "} orderGuard(!spec.referenceDiagnosis);", "} orderGuard(false);", "the diagnosis keeps COLAMD's column order on a grid (10 s at 3,069 unknowns)"),
    ("grid_diagnosis_threshold_default", SOLVE, "sys.autoQRThreshold = 250;", "sys.autoQRThreshold = 1000;", "a system under 1,000 unknowns is diagnosed with the dense QR (0.6 s at 969)"),
    ("grid_diagnosis_serial", SOLVE, "sys.diagnoseParallel = true;", "sys.diagnoseParallel = false;", "the two sparse QRs of the diagnosis run one after the other (0.9 s to 1.2 s at 3,069)"),
    ("grid_normal_pivot_test_off", GCS, "if (d.minCoeff() > 1e-12 * d.maxCoeff()) {", "if (true) {", "the sparse normal equations are trusted whatever their pivots"),
    ("grid_normal_equations_drop_term", GCS, "h_gn = ldlt.solve(-(Jx.adjoint() * fx));", "h_gn = ldlt.solve(-fx.head(Jx.cols()));", "the right side of the normal equations is not J^T f"),
    ("grid_elimination_skips_negatives", GCS, "if (R(i, col) != 0.0) {", "if (R(i, col) > 0.0) {", "the pivot row's negative entries are left out of the elimination that names redundant sets"),
    ("grid_reference_flag_ignored", APP, "spec.referenceDiagnosis = flag(rd, -1, \"'reference_diagnosis'\");", "spec.referenceDiagnosis = false;", "reference_diagnosis is never read"),
]


def run(cmd, cwd, env=None, timeout=3600):
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def main(argv):
    tree = argv[1]
    only = set(argv[2:])
    ctl = os.environ["CADFORK_CTL"]
    env = dict(os.environ, FC_SRC=tree, CMAKE_BUILD_PARALLEL_LEVEL="4", SKETCH3D_QUICK="1", SKETCH3D_SCALE="0.05")
    build = ["bash", "-c", 'source "%s/harness/env.sh" && nice "%s/harness/build.sh"' % (ctl, ctl)]
    binary = os.path.join(tree, "build", "release", "bin", "FreeCADCmd")
    rows = []

    def build_and_test():
        b = run(build, ctl, env)
        if b.returncode != 0:
            return "build failed", None
        try:
            r = run([binary, "-t", "TestSketch3DApp"], tree, env, timeout=300)  # a clean quick run takes about 15 s
        except subprocess.TimeoutExpired:
            # A defect that makes thousands of solves fail can make the whole run slow (a failing
            # solve tries perturbed starts before it names a conflict). Look for an assertion in
            # the classes that do not solve thousands of systems.
            names = ["TestSketch3DApp." + c for c in ("TestClosedForms", "TestConstraintSet", "TestGradients", "TestEntryPoint")]
            try:
                r = run([binary, "-t"] + names, tree, env, timeout=300)
            except subprocess.TimeoutExpired:
                return "ok", ["timeout (a clean run takes about 15 s)"]
        text = r.stdout + r.stderr
        failed = [l.split(" (")[0].split(": ")[-1] for l in text.splitlines() if l.startswith(("FAIL: ", "ERROR: "))]
        if "\nOK" not in text and not failed:
            failed = ["crash or timeout"]
        return "ok", failed

    for name, rel, old, new, what in MUTANTS:
        if only and name not in only:
            continue
        path = os.path.join(tree, rel)
        with open(path, "rb") as fh:
            original = fh.read()
        old_b, new_b = old.encode(), new.encode()
        if original.count(old_b) != 1:
            rows.append({"mutant": name, "result": "pattern not found or not unique (%d)" % original.count(old_b), "what": what})
            print(name, "PATTERN", original.count(old_b), flush=True)
            continue
        with open(path, "wb") as fh:
            fh.write(original.replace(old_b, new_b, 1))
        try:
            status, failed = build_and_test()
        finally:
            with open(path, "wb") as fh:
                fh.write(original)
        caught = bool(failed)
        rows.append({"mutant": name, "file": rel, "what": what, "build": status, "failing_tests": failed, "caught": caught})
        print(name, "BUILD FAILED" if failed is None else ("CAUGHT" if caught else "SURVIVED"), (failed or [])[:3], flush=True)
    build_and_test()  # leave the tree built from the original source
    with open("sketch3d_mutants.json", "w") as fh:
        json.dump(rows, fh, indent=1)
    caught = sum(r.get("caught", False) for r in rows)
    print("caught %d, survived %d, of %d" % (caught, sum(1 for r in rows if r.get("caught") is False and r.get("build") == "ok"), len(rows)))


if __name__ == "__main__":
    main(sys.argv)
