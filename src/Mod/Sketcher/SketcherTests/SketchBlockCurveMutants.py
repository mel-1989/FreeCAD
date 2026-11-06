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

"""Planted defects for ellipses, arcs of ellipse and B-splines as block members (G10 slice 5b2, check 15).

Each Python mutant is a byte-exact text replacement in the installed copy of ``SketcherBlocks.py`` in the build tree; each
C++ mutant is one in a source file of the worktree followed by ``harness/build.sh`` (C++ mutants are only run with
``--cpp``, they rebuild twice each).  The file comes back as it was, checked by hash.  A mutant is caught when one of
the tests named for it fails or errors; the run of the unmutated tree has to pass first.

    python3 -I SketchBlockCurveMutants.py <freecad worktree> [name ...]
    python3 -I SketchBlockCurveMutants.py --cpp <freecad worktree> [name ...]
    python3 -I SketchBlockCurveMutants.py --list

Needs ``QT_QPA_PLATFORM=offscreen`` and the build tree's ``bin/FreeCADCmd``; the C++ mode also needs ``FC_SRC`` and
the harness environment (``source harness/env.sh``)."""

import hashlib
import os
import subprocess
import sys
import time

MOD = "SketcherTests.TestSketchBlockCurves."
PYFILE = "build/release/Mod/Sketcher/SketcherBlocks.py"
DEF = ("TestCurveDefine",)
INT = ("TestCurveInternal",)
LIB = ("TestCurveLibrary",)
EDIT = ("TestCurveEdit",)
FIT = ("TestCurveStructureFit",)
PLACE = ("TestCurvePlacement", "TestCurveRigidity")

# (name, [(old, new), ...], tests, what it plants)
MUTANTS = [
    ("aoe_parameters_turn_with_the_instance", [('    if t == "a":\n        out[3] = g[3] + th', '    if t in "ao":\n        out[3] = g[3] + th')], PLACE, "an arc of ellipse's parameters are turned like a circular arc's when the geometry is made"),
    ("foci_not_rotated", [('    if t in "leo":\n        return [(0, 1), (2, 3)]', '    if t == "l":\n        return [(0, 1), (2, 3)]\n    if t in "eo":\n        return [(0, 1)]')], PLACE, "an ellipse's focus is not carried by the placement"),
    ("last_pole_not_carried", [("    return [(3 + 2 * i, 4 + 2 * i) for i in range(int(g[2]))]", "    return [(3 + 2 * i, 4 + 2 * i) for i in range(int(g[2]) - 1)]")], PLACE, "the last pole of a spline is not carried by the placement"),
    ("second_multiplicity_list_unread", [("mults = [int(v) for v in g[4 + 3 * n + m : 4 + 3 * n + 2 * m]]", "mults = [1] * m")], PLACE, "the multiplicities of a spline are not read"),
    ("internal_geometry_kept_as_member", [("            left_out.append(el)", "            pass")], DEF, "internal geometry listed with its curve becomes a member"),
    ("internal_geometry_without_curve_accepted", [("            if cur not in listed:", "            if False:")], DEF, "internal geometry whose curve is not listed is not refused"),
    ("left_out_not_reported", [('    return {"name": name, "left_out": sorted(left_out)}', '    return {"name": name, "left_out": []}')], DEF, "what was left out is not reported"),
    ("weight_change_read_as_rigid", [('        if t == "s" and _differs("s", a, b, float("inf")):\n            return None', "        if False:\n            return None")], EDIT, "a weight changed on a lone spline is read as a placement"),
    ("aoe_parameter_change_read_as_rigid", [('        if t == "o" and any(abs(math.remainder(x - y, TWO_PI)) > 1e-9 for x, y in zip(a[5:], b[5:])):', '        if False:')], EDIT, "an arc of ellipse's parameter change is read as a placement"),
    ("minor_radius_change_read_as_rigid", [('            radii.append((m["g"][4], world[k][4]))', "            pass")], EDIT, "an ellipse's minor radius change is read as a placement"),
    ("replaced_member_geometry_kept", [('for n in insts for k in removed + replaced if k in insts[n]["m"]}', 'for n in insts for k in removed if k in insts[n]["m"]}')], EDIT, "the old curve of a replaced member stays beside the new one"),
    ("new_kinds_always_version_1", [('"version": 2 if any(m["t"] in _CURVE_KINDS for m in members.values()) else 1,', '"version": 1,')], LIB, "a file with an ellipse is written as version 1"),
    ("old_kinds_written_as_version_2", [('"version": 2 if any(m["t"] in _CURVE_KINDS for m in members.values()) else 1,', '"version": 2,')], LIB, "a file with only the old kinds is written as version 2"),
    ("version_1_file_may_hold_new_kinds", [("        if version < 2 and any(", "        if False and any(")], LIB, "a version 1 file holding a spline is read"),
    ("weight_zero_accepted", [("    if not all(w > 0 for w in weights):", "    if False:")], LIB, "a weight of zero is accepted"),
    ("knot_order_unchecked", [("    if any(b <= a for a, b in zip(knots, knots[1:])):", "    if False:")], LIB, "knots out of order are accepted"),
    ("multiplicity_sum_unchecked", [('    if not ok:\n        raise SketchBlockError("%s has multiplicities', '    if False:\n        raise SketchBlockError("%s has multiplicities')], LIB, "multiplicities that do not fit the poles are accepted"),
    ("circle_like_arc_of_ellipse_accepted", [("            if math.hypot(out[2] - out[0], out[3] - out[1]) < _MIN_FOCAL:", "            if False:")], LIB, "an arc of ellipse whose focus is on its centre is accepted"),
    ("overlong_axis_accepted", [("        if math.hypot(math.hypot(out[2] - out[0], out[3] - out[1]), out[4]) > _MAX_COORD:", "        if False:")], LIB, "a major axis longer than the limit is accepted"),
    ("negative_minor_radius_accepted", [("        if not out[4] > 0:", "        if False:")], LIB, "a negative minor radius is accepted"),
    ("empty_arc_of_ellipse_accepted", [("            if not 0 < out[6] - out[5] <= TWO_PI + 1e-9:", "            if False:")], LIB, "an arc of ellipse with no length is accepted"),
    ("ellipse_focus_read_as_centre", [("        out = [cx, cy, cx + f * d.x, cy + f * d.y, b]", "        out = [cx, cy, cx, cy, b]")], PLACE + DEF, "the focus of an ellipse is stored as its centre"),
    ("knots_not_used_when_made", [("[V(x, y, 0) for x, y in poles], mults, knots, bool(periodic)", "[V(x, y, 0) for x, y in poles], mults, [float(i) for i in range(len(knots))], bool(periodic)")], PLACE, "a spline is made with uniform knots whatever the definition says"),
    ("structure_never_differs", [("def _same_structure(t, a, b):\n    return", "def _same_structure(t, a, b):\n    return True or")], EDIT, "a spline of another degree is moved, not replaced"),
    ("placement_quantity_dimension_unchecked", [("            if v.Unit != want[0]:", "            if False:")], ("TestPlacementQuantities",), "a mass is read as a length"),
    ("ellipse_axis_always_along_x", [("        d = geo.XAxis\n        out = [cx, cy, cx + f * d.x, cy + f * d.y, b]", "        out = [cx, cy, cx + f, cy, b]")], PLACE, "the focus of a turned ellipse is read on the x axis"),
]

# C++ mutants: (name, file under the worktree, [(old, new)], tests, what it plants)
CPP = [
    ("aoe_parameters_tied_as_circular", "src/Mod/Sketcher/App/Sketch.cpp", [("                    constantTie(r.ang0, r.m->a0);\n                    constantTie(r.ang1, r.m->a1);", "                    angleTie(r.ang0, r.m->a0);\n                    angleTie(r.ang1, r.m->a1);")], PLACE, "an arc of ellipse's parameters follow the instance's angle"),
    ("weight_tie_dropped", "src/Mod/Sketcher/App/Sketch.cpp", [("                    GCSsys.addConstraintBlockPlacement(\n                        Mode::Constant, r.wts[i], nullptr, nullptr, nullptr, 0.0, 0.0, r.m->weights[i]\n                    );", "")], PLACE + ("TestCurveDOF",), "a spline's weights are free"),
    ("one_pole_tie_dropped", "src/Mod/Sketcher/App/Sketch.cpp", [("                for (size_t i = 0; i < r.poles.size(); ++i) {\n                    point(r.poles[i]", "                for (size_t i = 0; i + 1 < r.poles.size(); ++i) {\n                    point(r.poles[i]")], PLACE + ("TestCurveDOF",), "the last pole of a spline is free"),
    ("focus_tie_dropped", "src/Mod/Sketcher/App/Sketch.cpp", [("            if (r.m->type == 'l' || r.m->type == 'e' || r.m->type == 'o') {\n                point(r.p1, r.m->x1, r.m->y1);", "            if (r.m->type == 'l') {\n                point(r.p1, r.m->x1, r.m->y1);")], PLACE + ("TestCurveDOF",), "an ellipse's focus is free"),
    ("second_multiplicity_list_not_compared", "src/Mod/Sketcher/App/SketchObjectBlocks.cpp", [("            || m.weights.size() != cur.weights.size() || m.knots.size() != cur.knots.size()\n            || m.mult != cur.mult) {", "            || m.weights.size() != cur.weights.size() || m.knots.size() != cur.knots.size()) {")], FIT, "a changed multiplicity list still fits"),
    ("weights_not_compared", "src/Mod/Sketcher/App/SketchObjectBlocks.cpp", [("            if (std::abs(m.weights[i] - cur.weights[i]) > kFitWeight * std::max(1.0, m.weights[i])) {\n                return false;\n            }", "")], FIT, "a changed weight still fits"),
]


def sha(b):
    return hashlib.sha256(b).hexdigest()


def run(binary, target, env):
    r = subprocess.run([binary, "-t", "SketcherTests.TestSketchBlockCurves." + target if False else "TestSketcherApp." + target], capture_output=True, text=True, env=env, cwd="/tmp", timeout=900)
    return "\nOK" in r.stdout + r.stderr


def main(argv):
    if "--list" in argv:
        for name, _, tests, what in MUTANTS:
            print("%-44s %s" % (name, what))
        for name, _, _, tests, what in CPP:
            print("%-44s (C++) %s" % (name, what))
        print(len(MUTANTS), "Python mutants,", len(CPP), "C++ mutants")
        return 0
    cpp = "--cpp" in argv
    argv = [a for a in argv if a != "--cpp"]
    tree = argv[1]
    only = set(argv[2:])
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    binary = os.path.join(tree, "build", "release", "bin", "FreeCADCmd")
    todo = []
    if not cpp:
        path = os.path.join(tree, PYFILE)
        todo = [(n, path, e, t, w) for n, e, t, w in MUTANTS]
    else:
        todo = [(n, os.path.join(tree, f), e, t, w) for n, f, e, t, w in CPP]
    wanted = sorted({t for _, _, _, tests, _ in todo for t in tests} if not only else {t for n, _, _, tests, _ in todo if n in only for t in tests})
    broken = [t for t in wanted if not run(binary, t, env)]
    if broken:
        sys.exit("the unmutated tree fails %s; nothing was planted" % broken)
    caught = ran = 0
    harness = os.path.expanduser(os.environ.get("CADFORK_HARNESS", "~/_agents/repos/app development/cad-fork/harness"))
    for name, path, edits, tests, what in todo:
        if only and name not in only:
            continue
        with open(path, "rb") as fh:
            original = fh.read()
        mutated = original
        for old, new in edits:
            if mutated.count(old.encode()) != 1:
                print("%-44s PATTERN DOES NOT MATCH ONCE" % name)
                mutated = None
                break
            mutated = mutated.replace(old.encode(), new.encode(), 1)
        if mutated is None:
            ran += 1
            continue
        t0 = time.time()
        failed = []
        try:
            with open(path, "wb") as fh:
                fh.write(mutated)
            if cpp:
                subprocess.run([os.path.join(harness, "build.sh")], check=True, capture_output=True, env=dict(os.environ, CMAKE_BUILD_PARALLEL_LEVEL="8"))
            for t in tests:
                if not run(binary, t, env):
                    failed.append(t)
                    break
        finally:
            with open(path, "wb") as fh:
                fh.write(original)
            if cpp:
                subprocess.run([os.path.join(harness, "build.sh")], check=True, capture_output=True, env=dict(os.environ, CMAKE_BUILD_PARALLEL_LEVEL="8"))
        assert sha(open(path, "rb").read()) == sha(original), "restore failed"
        ran += 1
        caught += bool(failed)
        print("%-44s %-8s %5.1fs  %s" % (name, "CAUGHT" if failed else "SURVIVED", time.time() - t0, ", ".join(failed)), flush=True)
    print("caught %d of %d" % (caught, ran))
    return 0 if caught == ran else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
