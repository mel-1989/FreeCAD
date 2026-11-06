# SPDX-License-Identifier: LGPL-2.1-or-later
"""Mutation check for the sketch blocks panel (G10 slice 5c, acceptance check 13).

Each planted defect is a byte-exact text replacement in the *built* tree's Python (``$FC_BUILD_DIR/Mod/Sketcher/``, the copy
the GUI loads).  The tests that must notice the defect run in a fresh GUI process; the original bytes are put back before
the next mutant, also when the run fails or is interrupted, and the restore is checked by hash.  A mutant is *caught* when
a named test method fails or errors; a run that prints no result is repeated once and then reported as ``no-result``,
which does not count.  The API-level defects of this slice (a delete that leaves its instance in the data, a refused
definition delete that changes the sketch, and the rest) are in SketchBlocksMutants.py beside slice 5a's.
Plain Python, not run inside FreeCAD.  Needs the harness environment (``source harness/env.sh``)::

    python3 SketchBlocksPanelMutants.py [--only name ...] [--dry] [--list]

``--dry`` only checks that every pattern matches its file exactly once.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import SketchDiagnosisPanelMutants as base  # noqa: E402

MOD = "SketcherTests.TestSketcherBlocksPanelGui"
PANEL = "SketcherBlocksPanel.py"
API = "SketcherBlocks.py"


def t(cls, name):
    return "%s.%s" % (cls, name)


CLICK = t("TestBlocksPanelClick", "test_one_click_places_at_that_point_on_every_kind_of_sketch")
OUT = t("TestBlocksPanelClick", "test_every_way_out_of_the_mode_changes_nothing_and_frees_the_next_click")
MODE = t("TestBlocksPanelClick", "test_the_click_does_not_also_select_and_navigation_still_works")
TOOL = t("TestBlocksPanelClick", "test_a_stock_tool_started_during_the_mode_ends_it")
STALE = t("TestBlocksPanelContents", "test_a_stale_row_refuses_with_a_sentence_and_changes_nothing")
SEL = t("TestBlocksPanelSelection", "test_choosing_a_row_selects_exactly_its_members_with_the_right_names")
SELBACK = t("TestBlocksPanelSelection", "test_selecting_a_member_in_the_view_chooses_its_row_and_plain_geometry_clears_it")
DEFINE = t("TestBlocksPanelActions", "test_define_gives_the_apis_result_in_one_undo_entry")
NUMBER = t("TestBlocksPanelActions", "test_insert_by_number_lands_on_analytic_coordinates")
EXPLODE = t("TestBlocksPanelActions", "test_explode_gives_the_apis_result_and_delete_removes_exactly_the_instance")
OBSERVERS = t("TestBlocksPanelOthers", "test_nothing_remains_after_close_and_fifty_openings_leave_nothing_behind")
SPEED = t("TestBlocksPanelSpeed", "test_open_refresh_select_and_drag_at_200_by_10")
MOVED = t("TestBlocksPanelEditResults", "test_a_moved_member")
ADDED = t("TestBlocksPanelEditResults", "test_an_added_member")
REMOVED = t("TestBlocksPanelEditResults", "test_a_removed_member_returns_the_constraints_on_it_by_name")
FIRST = t("TestBlocksPanelEditResults", "test_a_removed_first_member_shifts_the_ids_and_is_read_by_persistent_id")
TURNED = t("TestBlocksPanelEditResults", "test_a_session_that_turns_the_first_member_keeps_the_start_placement")
FINISH = t("TestBlocksPanelEditUndo", "test_finish_is_one_undo_entry_and_redo_gives_the_result")
CANCEL = t("TestBlocksPanelEditUndo", "test_cancel_restores_the_sketch_and_the_depth_with_redo_empty")
REFUSED = t("TestBlocksPanelEditUndo", "test_a_refused_finish_stays_in_the_session_with_its_work_intact")
LEAVE = t("TestBlocksPanelEditLeaving", "test_leaving_the_editor_keeps_the_edits_with_the_instance_exploded")
SECOND = t("TestBlocksPanelEditLeaving", "test_a_second_session_is_refused")

M = []


def mutant(name, desc, patches, tests):
    """`patches`: list of (file, old, new); each pattern must match its file exactly once."""
    M.append(dict(name=name, desc=desc, patches=patches, tests=tests))


mutant(
    "click_not_mapped_through_the_editing_placement",
    "a click is mapped through the sketch's global placement, not the editor's own placement (wrong inside a Body)",
    [(PANEL, "    gp = _editing_placement(sketch)\n    normal =", "    gp = sketch.getGlobalPlacement()\n    normal =")],
    [CLICK],
)
mutant(
    "click_also_selects",
    "the placing click is not taken, so the editor also selects what is under it",
    [
        (PANEL, "        if t == E.MouseButtonPress or t == E.MouseButtonDblClick:\n            self._pressed = True\n            return True", "        if t == E.MouseButtonPress or t == E.MouseButtonDblClick:\n            self._pressed = True\n            return False"),
        (PANEL, "        self.place_at(ev.position())\n        return True", "        self.place_at(ev.position())\n        return False"),
    ],
    [MODE],
)
mutant(
    "place_mode_left_on_after_cancel",
    "ending the placement leaves the mode on",
    [(PANEL, "        was = self._placing\n        self._placing = None\n", "        was = self._placing\n")],
    [OUT],
)
mutant(
    "row_keyed_by_position",
    "an instance row is named by its position among its definition's instances, so a gap in the numbering shifts it",
    [(PANEL, '{"kind": "inst", "name": iname, "def": dname}', '{"kind": "inst", "name": "%s_%d" % (dname, insts.index(iname) + 1), "def": dname}')],
    [SEL, EXPLODE],
)
mutant(
    "stale_row_acts_on_another_instance",
    "a row that has gone explodes the first instance that is left",
    [(PANEL, "        name = c[1]\n        return self._run(lambda: B.explode(self.sketch, name)", "        name = c[1] if c[1] in B.blocks(self.sketch)[\"instances\"] else next(iter(B.blocks(self.sketch)[\"instances\"]))\n        return self._run(lambda: B.explode(self.sketch, name)")],
    [STALE],
)
mutant(
    "finish_reads_members_by_position",
    "Finish reads each member by its place in the instance's list instead of by persistent id",
    [(API, "        i = idmap[gid]\n        t, nums = _numbers(geo[i])\n        m = defn", "        i = list(member_ids.values()).index(gid)\n        t, nums = _numbers(geo[i])\n        m = defn")],
    [FIRST],
)
mutant(
    "finish_uses_the_placement_at_finish",
    "Finish fits the instance's placement to the geometry as it is at Finish, not as it was at the start",
    [(PANEL, '            res = B.session_changes(sk, s["definition"], s["placement"], s["members"], s["known"])', '            _d = B._load(sk)\n            _m = B._id_map(sk)\n            _w = {k: B._numbers(sk.Geometry[_m[g]])[1] for k, g in s["members"].items() if g in _m}\n            res = B.session_changes(sk, s["definition"], B._placement(_d["defs"][s["definition"]], _w), s["members"], s["known"])')],
    [TURNED],
)
mutant(
    "added_geometry_ignored",
    "geometry that did not exist when the session began is not made a member",
    [(API, "    for i in range(len(geo)):\n        gid = sk.getGeometryId(i)\n        if gid in known_ids:", "    for i in range(0):\n        gid = sk.getGeometryId(i)\n        if gid in known_ids:")],
    [ADDED],
)
mutant(
    "removed_members_not_removed",
    "a member whose geometry was deleted in the session stays in the definition",
    [(API, "            changes[key] = None\n            removed.append(key)\n            continue\n", "            continue\n")],
    [REMOVED, FIRST],
)
mutant(
    "rollback_one_step_short",
    "Cancel and Finish undo one step too few",
    [(PANEL, "            for _ in range(n):\n                doc.undo()", "            for _ in range(n - 1):\n                doc.undo()")],
    [CANCEL, FINISH],
)
mutant(
    "rollback_one_step_long",
    "Cancel and Finish undo one step too many, into the user's own work",
    [(PANEL, "            for _ in range(n):\n                doc.undo()", "            for _ in range(n + 1):\n                doc.undo()")],
    [CANCEL, FINISH],
)
mutant(
    "cancel_leaves_the_session_entry",
    "Cancel ends the session without rolling its undo entries back",
    [(PANEL, "        if not self._rollback():\n            self._restore(final)\n            self._session_broken(tr(\"the undo steps of the edit could not be rolled back\"))\n            return False\n        self._end_session(tr(\"The edit was cancelled", "        if False and not self._rollback():\n            self._restore(final)\n            self._session_broken(tr(\"the undo steps of the edit could not be rolled back\"))\n            return False\n        self._end_session(tr(\"The edit was cancelled")],
    [CANCEL],
)
mutant(
    "finish_applies_before_rolling_back",
    "Finish gives the result to update_definition first and rolls back after, so the rollback undoes the result",
    [(
        PANEL,
        '        if not self._rollback():\n            self._restore(final)\n            self._session_broken(tr("the undo steps of the edit could not be rolled back"))\n            return False\n        removed_constraints = []\n        if changes:\n            try:\n                out = B.update_definition(sk, s["definition"], changes)',
        '        removed_constraints = []\n        if changes:\n            try:\n                out = B.update_definition(sk, s["definition"], changes)\n                self._rollback()',
    )],
    [FINISH, MOVED],
)
mutant(
    "refused_finish_loses_the_session",
    "a Finish that update_definition would refuse ends the session",
    [(PANEL, '                self.status.setText(_sentence(why) + " " + tr("The edit goes on; nothing was lost."))\n                return False', '                self.status.setText(_sentence(why) + " " + tr("The edit goes on; nothing was lost."))\n                self.session = None\n                return False')],
    [REFUSED],
)
mutant(
    "leaving_the_editor_rolls_back_silently",
    "leaving the editor during a session rolls the edits back without a word",
    [(PANEL, '            name = self.session["instance"]\n            self.session = None\n            self._say(tr("The edit of %s ended: %s The instance stays exploded', '            name = self.session["instance"]\n            self._rollback()\n            self.session = None\n            self._say(tr("The edit of %s ended: %s The instance stays exploded')],
    [LEAVE],
)
mutant(
    "second_session_allowed",
    "a second edit can be started while one runs",
    [(PANEL, '        if self.session is not None:\n            self.status.setText(tr("An edit is already going on; finish or cancel it first."))\n            return False\n', "")],
    [SECOND],
)
mutant(
    "selection_observer_left_after_close",
    "closing the panel leaves its selection observer registered",
    [(PANEL, ', (Gui.Selection.removeObserver, "_sw")', "")],
    [OBSERVERS],
)
mutant(
    "drag_refreshes_the_panel",
    "every change to the geometry, a drag move included, refreshes the list",
    [(PANEL, "            if self.sketch.GeometryCount != self._geo_count:\n                self.schedule()", "            self.schedule()")],
    [SPEED],
)
mutant(
    "selecting_makes_an_undo_entry",
    "choosing an instance row opens a transaction on the sketch",
    [(PANEL, "        self._own_change = True\n        try:\n            Gui.Selection.clearSelection(self.doc_name)", "        self.sketch.Document.openTransaction(\"Select\")\n        self.sketch.Placement = self.sketch.Placement\n        self.sketch.Document.commitTransaction()\n        self._own_change = True\n        try:\n            Gui.Selection.clearSelection(self.doc_name)")],
    [SEL],
)
mutant(
    "delete_only_explodes",
    "Delete on an instance explodes it and leaves its members",
    [(PANEL, "return self._run(lambda: B.delete_instance(self.sketch, name) or [], done)", "return self._run(lambda: B.explode(self.sketch, name) or [], done)")],
    [EXPLODE],
)
mutant(
    "define_takes_the_geometry_in_id_order",
    "Define takes the selected geometry in GeoId order, not in the order it was selected",
    [(PANEL, "                if geo not in out:\n                    out.append(geo)\n        return out", "                if geo not in out:\n                    out.append(geo)\n        return sorted(out)")],
    [DEFINE],
)
mutant(
    "angle_taken_as_radians",
    "a bare angle in the form is read as radians instead of degrees",
    [(PANEL, "        return math.radians(float(q.Value))\n    if q.Unit == App.Units.Angle", "        return float(q.Value)\n    if q.Unit == App.Units.Angle")],
    [NUMBER],
)
mutant(
    "x_and_y_swapped",
    "the form's X and Y fields are read the other way round",
    [(PANEL, "            x, y = parse_length(self.x_edit.text()), parse_length(self.y_edit.text())", "            x, y = parse_length(self.y_edit.text()), parse_length(self.x_edit.text())")],
    [NUMBER],
)
mutant(
    "member_click_chooses_the_first_row",
    "selecting a member in the view chooses the first instance's row",
    [(PANEL, "                        found = self.member_of[el[0]]", "                        found = sorted(self.info[\"instances\"])[0]")],
    [SELBACK],
)
mutant(
    "plain_geometry_keeps_the_row",
    "selecting plain geometry leaves the instance row chosen",
    [(PANEL, "                if c is not None and c[0] == \"inst\":  # a definition stays chosen: the user may be about to place it\n                    self.tree.clearSelection()\n                    self.tree.setCurrentItem(None)", "                pass")],
    [SELBACK],
)
mutant(
    "stock_tool_does_not_end_the_mode",
    "starting a stock tool leaves the placement on",
    [(PANEL, "        if shape == QtCore.Qt.BitmapCursor:\n            self.cancel_place", "        if False:\n            self.cancel_place")],
    [TOOL],
)
mutant(
    "navigation_ends_the_mode",
    "the cursor changes of navigation end the placement as a tool's would",
    [(PANEL, "        if self._pointer_busy:  # navigation sets its own cursors while the pointer is being dragged\n            return\n", "")],
    [MODE],
)


def target(fname, build):
    return os.path.join(build, "Mod", "Sketcher", fname)


def patched_files(m, build):
    """{path: (original bytes, mutated bytes)} or None when a pattern does not match exactly once."""
    out = {}
    for fname, old, new in m["patches"]:
        path = target(fname, build)
        raw = out[path][1] if path in out else open(path, "rb").read()
        orig = out[path][0] if path in out else raw
        o, n = old.encode(), new.encode()
        if raw.count(o) != 1:
            return None
        out[path] = (orig, raw.replace(o, n))
    return out


DRIVER = """import sys, unittest
import FreeCADGui
names = %r
suite = unittest.TestSuite()
for n in names:
    suite.addTests(unittest.defaultTestLoader.loadTestsFromName(n))
unittest.TextTestRunner(stream=sys.stdout, verbosity=1).run(suite)
"""


def run_tests(m, runner):
    with tempfile.TemporaryDirectory() as tmp:
        script = os.path.join(tmp, "run_selected.py")
        with open(script, "w") as f:
            f.write(DRIVER % ([MOD + "." + n for n in m["tests"]],))
        p = subprocess.Popen(
            [sys.executable, "-I", runner, "script", script, "--timeout", "300"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        out, _ = p.communicate(timeout=700)
    return out


def verdict(out):
    lines = out.splitlines()
    failed = [ln.split(":", 1)[1].split("(")[0].strip() for ln in lines if ln.startswith(("FAIL: test", "ERROR: test"))]
    ran = any(ln.startswith("Ran ") for ln in lines)
    if failed:
        return "caught", sorted(set(failed))
    return ("survived" if any(ln.strip() == "OK" for ln in lines) else "no-result" if not ran else "crash"), []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        for m in M:
            print("%-52s %s" % (m["name"], m["desc"]))
        print(len(M), "mutants")
        return 0
    build, src, runner, ctl = base.paths()
    rows = []
    for m in M:
        if a.only and m["name"] not in a.only:
            continue
        files = patched_files(m, build)
        if files is None:
            print("%-52s PATTERN DOES NOT MATCH ONCE" % m["name"])
            rows.append((m["name"], "bad-pattern", []))
            continue
        if a.dry:
            print("%-52s pattern ok" % m["name"])
            continue
        t0 = time.time()
        try:
            for path, (_, mutated) in files.items():
                with open(path, "wb") as f:
                    f.write(mutated)
            out = run_tests(m, runner)
            if verdict(out)[0] == "no-result":
                out = run_tests(m, runner)
        finally:
            for path, (original, _) in files.items():
                with open(path, "wb") as f:
                    f.write(original)
        for path, (original, _) in files.items():
            assert base.sha(open(path, "rb").read()) == base.sha(original), "restore failed for %s" % path
        status, failed = verdict(out)
        rows.append((m["name"], status, failed))
        print("%-52s %-9s %5.0fs  %s" % (m["name"], status, time.time() - t0, ", ".join(failed)[:140]))
        sys.stdout.flush()
    if not a.dry:
        caught = sum(1 for r in rows if r[1] == "caught")
        print(json.dumps({"mutants": len(rows), "caught": caught, "not_caught": [r[0] for r in rows if r[1] != "caught"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
