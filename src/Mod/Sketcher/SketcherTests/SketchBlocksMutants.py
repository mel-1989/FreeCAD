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

"""Planted defects for sketch blocks (G10 slice 5a, check 12).

Each mutant is a byte-exact text replacement.  C++ mutants patch the source
tree and rebuild through the harness (the script prints how long each build
took), Python mutants patch the installed copy of the module in the build
tree, so they need no rebuild.  Every file comes back as it was.  A mutant is
caught when a test of the block suites fails or errors.

    CADFORK_CTL=<cad-fork folder> python3 SketchBlocksMutants.py <freecad worktree> [name ...]

The result table goes to ``block_mutants.json`` in the current directory."""

import json
import os
import subprocess
import sys
import time

CON = "src/Mod/Sketcher/App/planegcs/Constraints.cpp"
SKETCH = "src/Mod/Sketcher/App/Sketch.cpp"
BLOCKS = "src/Mod/Sketcher/App/SketchObjectBlocks.cpp"
SKOBJ = "src/Mod/Sketcher/App/SketchObject.cpp"
SKGEO = "src/Mod/Sketcher/App/SketchObjectGeometry.cpp"
PYBLK = "build/release/Mod/Sketcher/SketcherBlocks.py"
PYDIAG = "build/release/Mod/Sketcher/SketcherDiagnosis.py"

# cheap classes first; the run of a mutant stops at the first failing class
TESTS = [
    "TestBlockConstraints",
    "TestBlockPersistence",
    "TestBlockEditing",
    "TestBlockShape",
    "TestBlockDOF",
    "TestBlockPlacement",
]

# (name, kind, file, [(old, new), ...], what it plants)
MUTANTS = [
    ("rotation_sign_wrong", "cpp", CON, [
        ("(*px() + lx * std::cos(*pa()) - ly * std::sin(*pa()))", "(*px() + lx * std::cos(*pa()) + ly * std::sin(*pa()))"),
        ("deriv += lx * std::sin(*pa()) + ly * std::cos(*pa());", "deriv += lx * std::sin(*pa()) - ly * std::cos(*pa());"),
    ], "the x equation has the wrong sign on the sine term, in the error and in its gradient"),
    ("cos_sin_swapped", "cpp", CON, [
        ("(*py() + lx * std::sin(*pa()) + ly * std::cos(*pa()))", "(*py() + lx * std::cos(*pa()) + ly * std::sin(*pa()))"),
    ], "cosine and sine are swapped in the y equation"),
    ("y_equation_dropped", "cpp", SKETCH, [
        ("            GCSsys.addConstraintBlockPlacement(Mode::Y, p.y, pX, pY, pA, lx, ly, 0.0);\n", ""),
    ], "a point's y equation is not added"),
    ("radius_left_free", "cpp", SKETCH, [
        ("            if (r.rad) {\n                GCSsys.addConstraintBlockPlacement(", "            if (false && r.rad) {\n                GCSsys.addConstraintBlockPlacement("),
    ], "a circle's or arc's radius is not tied"),
    ("arc_end_angle_untied", "cpp", SKETCH, [
        ("                angleTie(r.ang1, r.m->a1);\n", ""),
    ], "an arc's end angle is not tied"),
    ("angle_wrapped", "cpp", CON, [
        ("return scale * (*p() - (*pa() + c));", "return scale * (*p() - (std::remainder(*pa(), 6.283185307179586) + c));"),
    ], "the instance angle is wrapped into one turn inside the arc angle equation"),
    ("ids_keyed_by_geoid", "cpp", BLOCKS, [
        ("idToGeo.emplace(GeometryFacade::getFacade(geos[i])->getId(), static_cast<int>(i));", "idToGeo.emplace(static_cast<long>(i), static_cast<int>(i));"),
    ], "the solver finds a member by its GeoId instead of its persistent id"),
    ("member_deleted_cluster_left", "cpp", SKOBJ, [
        ("        pruneBlocks();\r\n    }\r\n\r\n    if (isRestoring() && checkMigration(Geometry)) {", "    }\r\n\r\n    if (isRestoring() && checkMigration(Geometry)) {"),
    ], "deleting a member leaves the instance's data in place"),
    ("stale_id_accepted", "cpp", SKOBJ, [
        ("if (int exploded = pruneBlocks()) {", "if (int exploded = 0) {"),
    ], "ids that match no geometry are kept on load"),
    ("cluster_left_after_explode", "py", PYBLK, [
        ('    del data["inst"][instance]\n', "    pass\n"),
    ], "explode leaves the instance's cluster in the data"),
    ("block_data_not_copied_to_scratch", "py", PYDIAG, [
        ('("Geometry", "ExternalGeo", "Constraints", "BlockData")', '("Geometry", "ExternalGeo", "Constraints")'),
    ], "the diagnosis copy of a sketch has no block data"),
    ("update_outside_transaction", "py", PYBLK, [
        ("        doc.openTransaction(label)\n", "        pass\n"),
    ], "no transaction is opened around an edit"),
    ("removed_member_constraints_not_reported", "py", PYBLK, [
        ("        drop = [i for i, c in enumerate(cons) if {c.First, c.Second, c.Third} & set(gone)]", "        drop = []"),
    ], "constraints on a removed member are left to the stock delete, which neither names nor refuses them"),
    ("diagnosis_solves_the_real_sketch", "py", PYDIAG, [
        ("            n.restorePropertyContent(prop, src.dumpPropertyContent(prop))\n        for path, expr", "            n.restorePropertyContent(prop, src.dumpPropertyContent(prop))\n        n = src\n        for path, expr"),
    ], "the diagnosis works on the user's sketch instead of a copy"),
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

    def rebuild():
        t = time.time()
        b = run(build, ctl, env)
        return b.returncode == 0, time.time() - t

    def tests():
        failed = []
        for t in TESTS:
            r = run([binary, "-t", "SketcherTests.TestSketchBlocks." + t], "/tmp", env)
            if "\nOK" not in r.stdout + r.stderr:
                failed.append(t)
                break
        return failed

    # the unmutated tree must pass every class first, or "caught" means nothing
    base_failed = []
    for t in TESTS[:4]:
        r = run([binary, "-t", "SketcherTests.TestSketchBlocks." + t], "/tmp", env)
        if "\nOK" not in r.stdout + r.stderr:
            base_failed.append(t)
    if base_failed:
        sys.exit("the unmutated tree fails %s; nothing was planted" % base_failed)

    for name, kind, rel, edits, what in MUTANTS:
        if only and name not in only:
            continue
        path = os.path.join(tree, rel)
        with open(path, "rb") as fh:
            original = fh.read()
        mutated = original
        crlf = b"\r\n" in original
        for old, new in edits:
            old_b, new_b = old.encode(), new.encode()
            if crlf:
                old_b = old_b.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                new_b = new_b.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            if old_b not in mutated:
                mutated = None
                break
            mutated = mutated.replace(old_b, new_b, 1)
        if mutated is None:
            rows.append({"mutant": name, "result": "pattern not found", "what": what})
            print(name, "PATTERN NOT FOUND", flush=True)
            continue
        build_s = 0.0
        with open(path, "wb") as fh:
            fh.write(mutated)
        try:
            ok = True
            if kind == "cpp":
                ok, build_s = rebuild()
            failed = tests() if ok else []
        finally:
            with open(path, "wb") as fh:
                fh.write(original)
        rows.append({"mutant": name, "kind": kind, "file": rel, "what": what, "build": "ok" if ok else "failed", "build_seconds": round(build_s), "failing_tests": failed, "caught": bool(failed)})
        print(name, "CAUGHT" if failed else "SURVIVED", failed, "build %ds" % build_s, flush=True)
    rebuild()  # leave the tree built from the original source
    with open("block_mutants.json", "w") as fh:
        json.dump(rows, fh, indent=1)
    print("caught %d of %d" % (sum(r.get("caught", False) for r in rows), len(rows)))


if __name__ == "__main__":
    main(sys.argv)
