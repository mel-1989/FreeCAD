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

"""Planted defects for the sketch-block library, batch insert and the one-member rule (G10 slice 5b1, check 13).

Each mutant is a byte-exact text replacement in the installed copy of ``SketcherBlocks.py`` in the build tree (all of
5b1 is Python, so nothing rebuilds).  The file comes back as it was, checked by hash.  A mutant is caught when one of
the tests named for it fails or errors; the run of the unmutated tree has to pass first.

    python3 -I SketchBlockLibraryMutants.py <freecad worktree> [name ...]
    python3 -I SketchBlockLibraryMutants.py --list

Needs ``QT_QPA_PLATFORM=offscreen`` and the build tree's ``bin/FreeCADCmd``."""

import hashlib
import os
import subprocess
import sys
import time

MOD = "SketcherTests.TestSketchBlockLibrary."
FILE = "build/release/Mod/Sketcher/SketcherBlocks.py"
RT = ("TestLibraryRoundTrip",)
SAVE = ("TestLibrarySave",)
LOAD = ("TestLibraryLoad",)
LIST = ("TestLibraryList",)
MANY = ("TestInsertMany.test_equals_repeated_insert_on_sets_of_1_2_50_and_500", "TestInsertMany.test_a_bad_placement_anywhere_refuses_the_whole_call_before_anything_changes", "TestInsertMany.test_at_most_two_solves_per_call")
ONE = ("TestOneMemberRigid",)

# (name, [(old, new), ...], tests, what it plants)
MUTANTS = [
    ("float_rounded_on_save", [('out[key] = {"t": t, "g": nums, "c": c}', 'out[key] = {"t": t, "g": [round(v, 9) for v in nums], "c": c}')], RT, "every number is rounded to nine places"),
    ("keys_renumbered", [('        out[key] = {"t": t, "g": nums, "c": c}', '        out["k%d" % len(out)] = {"t": t, "g": nums, "c": c}')], RT, "member keys are renumbered"),
    ("construction_flag_dropped", [('out[key] = {"t": t, "g": nums, "c": c}', 'out[key] = {"t": t, "g": nums, "c": False}')], RT, "the construction flag is lost"),
    ("traversal_check_removed", [("    if any(ord(ch) < 32 or ord(ch) == 127 or ch in _BAD_CHARS for ch in name):", "    if False:")], SAVE, "a name with a slash or a NUL is accepted as a file name"),
    ("leading_dot_allowed", [('    if name.startswith("."):', "    if False:")], SAVE, "a name starting with a dot is accepted"),
    ("reserved_names_allowed", [('    if name.split(".")[0].upper() in _RESERVED:', "    if False:")], SAVE, "a Windows reserved name is accepted"),
    ("overwrite_ignored", [("    if os.path.lexists(full) and not overwrite:", "    if False:")], SAVE, "an existing file is replaced without overwrite"),
    ("write_not_atomic", [("        os.replace(tmp, full)\n        tmp = None", "        shutil_copy(tmp, full)")], SAVE, "the file is written in place, so a failure leaves half a file"),
    ("temp_file_left_behind", [("            with contextlib.suppress(OSError):\n                os.remove(tmp)\n    return full", "            pass\n    return full")], SAVE, "a failed save leaves its temporary file"),
    ("case_collision_unseen", [("        if e != target and _norm(e) == _norm(target):", "        if False:")], SAVE, "a name differing only by case replaces the other file"),
    ("version_check_removed", [("        if version > LIBRARY_VERSION:", "        if False:")], LOAD, "a file of a newer version is read"),
    ("nan_accepted", [("    raise ValueError(\"%s is not a number a sketch can hold\" % text)", "    return float(text)"), ("    if not math.isfinite(v) or not lo <= v <= hi:", "    if False:")], LOAD, "NaN, Infinity and huge numbers are accepted"),
    ("duplicate_keys_accepted", [("object_pairs_hook=_no_duplicates, ", "")], LOAD, "a key given twice is accepted, the last wins"),
    ("symlink_out_accepted", [("        if os.path.commonpath([real, folder]) != folder:", "        if False:")], LOAD, "a link to a file outside the folder is followed"),
    ("size_limit_removed", [("        if os.path.getsize(real) > _MAX_FILE:\n            raise SketchBlockError(\"the file of block '%s' is too large to be a block\" % name)\n", ""), ("        if len(raw) > _MAX_FILE:", "        if False:"), ("raw = fh.read(_MAX_FILE + 1)", "raw = fh.read()")], LOAD, "a file of many megabytes is read and parsed"),
    ("bad_file_aborts_the_list", [("        except SketchBlockError as exc:\n            out.append({\"name\": stem, \"members\": None, \"error\": str(exc)})", "        except ZeroDivisionError as exc:\n            out.append({\"name\": stem, \"members\": None, \"error\": str(exc)})")], LIST, "one bad file stops the list"),
    ("reuse_with_different_members", [("            if data[\"defs\"][target][\"members\"] == members:", "            if True:")], LOAD, "a block of that name with other members is taken as the same"),
    ("as_name_skips_the_rules", [("        _name_ok(data, as_name)\n        target = as_name", "        target = as_name")], LOAD, "as_name may be a taken name or an instance's name"),
    ("load_outside_a_transaction", [("    with _transaction(sk, \"Load block\"):", "    if True:")], LOAD, "loading is not one undo entry"),
    ("live_link_kept", [("    data[\"defs\"][target] = {\"members\": members}\n    with _transaction(sk, \"Load block\")", "    data[\"defs\"][target] = {\"members\": members, \"file\": full}\n    with _transaction(sk, \"Load block\")")], LOAD, "the definition remembers its file"),
    ("a_bad_placement_refused_late", [("        if not all(map(math.isfinite, (x, y, a))):\n            raise SketchBlockError(\"placement %d must be finite", "        if False:\n            raise SketchBlockError(\"placement %d must be finite")], MANY, "NaN and Infinity placements reach the sketch"),
    ("a_solve_per_instance", [("            data[\"inst\"][iname] = {\"def\": name, \"m\": dict(zip(keys, persistent[i * len(keys) : (i + 1) * len(keys)]))}", "            data[\"inst\"][iname] = {\"def\": name, \"m\": dict(zip(keys, persistent[i * len(keys) : (i + 1) * len(keys)]))}\n            sk.solve()")], MANY, "batch insert solves once per instance"),
    ("names_in_the_wrong_order", [("        names.append(\"%s_%d\" % (name, data[\"n\"]))", "        names.insert(0, \"%s_%d\" % (name, data[\"n\"]))")], MANY, "batch insert numbers the instances in reverse"),
    ("batch_insert_not_one_entry", [("    with _transaction(sk, \"Insert blocks\"):", "    if True:")], ("TestInsertMany.test_one_undo_entry_and_undo_redo_restore_exactly",), "batch insert is not one undo entry"),
    ("rigid_fit_on_a_lone_line_off", [("    if not keys:\n        return None", "    if len(keys) < 2:\n        return None")], ONE, "a rigid move of a lone line is read as a change of the block"),
    ("lone_line_length_change_read_as_rigid", [("        if math.hypot(ax * c - ay * s + X - bx, ax * s + ay * c + Y - by) > _PLACE_TOL:\n            return None", "        if math.hypot(ax * c - ay * s + X - bx, ax * s + ay * c + Y - by) > (_PLACE_TOL if len(keys) > 1 else 1e9):\n            return None")], ONE, "a lone line that changed length is read as a rigid move"),
    ("shape_naming_removed", [("        _add_hand_shape(obj, out)\n", "")], ("TestHandAssignedShape.test_a_shape_assigned_to_a_feature_with_no_inputs_is_named",), "a shape assigned to a feature with no inputs is not named"),
    ("surrogate_check_removed", [('        value.encode("utf-8")\n', "        pass\n")], ("TestRepairRound.test_an_unpaired_surrogate_is_refused_wherever_a_string_enters",), "a name or key with an unpaired surrogate is accepted and the C++ reader then drops all block data"),
    ("a_setconstruction_call_per_member", [("    if flag:\n        ext = Sketcher.SketchGeometryExtension()", "    if False:\n        ext = Sketcher.SketchGeometryExtension()"), ("            persistent = [sk.getGeometryId(g) for g in gids]", "            for g, k in zip(gids, keys * len(clean)):\n                if defn[\"members\"][k].get(\"c\"):\n                    sk.setConstruction(g, True)\n            persistent = [sk.getGeometryId(g) for g in gids]")], ("TestRepairRound.test_construction_members_cost_no_solve_per_member_and_land_where_insert_puts_them",), "batch insert sets the construction flag member by member, one solve each"),
    ("a_configured_folder_is_made_again", [("    if create and _is_default_folder(folder):", "    if create and folder is None:")], SAVE, "a configured library folder that has gone is silently recreated"),
    ("element_map_version_counted_as_the_shape", [('"AttacherType", "_ElementMapVersion"})', '"AttacherType"})')], ("TestHandAssignedShape.test_a_shape_assigned_to_a_feature_with_no_inputs_is_named",), "a bookkeeping property is named beside the shape"),
]


def sha(b):
    return hashlib.sha256(b).hexdigest()


def run(binary, target, env):
    r = subprocess.run([binary, "-t", MOD + target], capture_output=True, text=True, env=env, cwd="/tmp", timeout=900)
    return "\nOK" in r.stdout + r.stderr


def main(argv):
    if "--list" in argv:
        for name, _, tests, what in MUTANTS:
            print("%-42s %s" % (name, what))
        print(len(MUTANTS), "mutants")
        return 0
    tree = argv[1]
    only = set(argv[2:])
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    binary = os.path.join(tree, "build", "release", "bin", "FreeCADCmd")
    path = os.path.join(tree, FILE)
    with open(path, "rb") as fh:
        original = fh.read()
    # the helper some mutants call
    helper = b"\n\ndef shutil_copy(a, b):\n    import shutil\n\n    shutil.copyfile(a, b)\n"
    # the unmutated tree has to pass every test a mutant is judged by, or "caught" means nothing
    wanted = sorted({t for _, _, tests, _ in MUTANTS for t in tests} if not only else {t for n, _, tests, _ in MUTANTS if n in only for t in tests})
    broken = [t for t in wanted if not run(binary, t, env)]
    if broken:
        sys.exit("the unmutated tree fails %s; nothing was planted" % broken)
    caught = ran = 0
    for name, edits, tests, what in MUTANTS:
        if only and name not in only:
            continue
        mutated = original
        for old, new in edits:
            if mutated.count(old.encode()) != 1:
                print("%-42s PATTERN DOES NOT MATCH ONCE" % name)
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
                fh.write(mutated + helper)
            for t in tests:
                if not run(binary, t, env):
                    failed.append(t)
                    break
        finally:
            with open(path, "wb") as fh:
                fh.write(original)
        assert sha(open(path, "rb").read()) == sha(original), "restore failed"
        ran += 1
        caught += bool(failed)
        print("%-42s %-8s %4.1fs  %s" % (name, "CAUGHT" if failed else "SURVIVED", time.time() - t0, ", ".join(failed)), flush=True)
    print("caught %d of %d" % (caught, ran))
    return 0 if caught == ran else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
