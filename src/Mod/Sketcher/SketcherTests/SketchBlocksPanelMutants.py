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

REP = "TestBlocksPanelEditRepairs"
OUTSIDE = t(REP, "test_edits_outside_the_block_are_named_before_finish_and_undone_by_the_second_press")
REARM = t(REP, "test_a_further_change_after_the_warning_asks_again_and_cancel_still_restores")
NOTHING = t(REP, "test_nothing_had_changed_is_said_only_when_nothing_changed")
RIGID = t(REP, "test_members_moved_together_do_not_change_the_block_or_the_siblings")
RIGIDADD = t(REP, "test_a_rigid_move_with_a_new_member_gives_only_the_new_member")
ACT = t(REP, "test_the_panel_follows_document_activation_and_comes_back_with_its_status")
NAME = t(REP, "test_cancel_and_finish_count_from_the_start_when_a_later_entry_has_the_sessions_name")
FULL = t(REP, "test_a_stack_that_is_already_full_at_the_start_does_not_matter")
LONG = t(REP, "test_a_session_longer_than_the_undo_stack_ends_with_a_message_and_keeps_the_edits")
MODE0 = t(REP, "test_undo_mode_zero_changes_nothing_here_and_a_stack_of_nothing_refuses_the_edit")
OPEN = t(REP, "test_an_operation_left_open_refuses_the_start_and_the_finish")
NOEDIT = t(REP, "test_finish_does_nothing_when_the_sketch_is_no_longer_in_the_editor_even_if_no_event_arrived")
CPLAIN = t(REP, "test_a_constraint_to_plain_geometry_that_moves_a_member_leaves_placements_and_the_plain_geometry_exact")
ANGLE = t("TestBlocksPanelClick", "test_the_click_places_with_the_angle_field")
SOLVES = t("TestBlocksPanelActions", "test_an_insert_through_the_panel_solves_once")
HEAD = "SketcherTests.TestSketchBlocks.TestBlockSessionReading."
H_RIGID = HEAD + "test_members_moved_together_are_the_instances_move_not_a_change_of_the_block"
H_LONE = HEAD + "test_a_shape_change_a_lone_member_moved_and_a_first_member_turn_read_as_before"
H_NAMES = HEAD + "test_a_definition_cannot_take_an_instances_name_and_an_instance_name_never_collides"

M = []


def mutant(name, desc, patches, tests, profile=None):
    """`patches`: list of (file, old, new); each pattern must match its file exactly once.  `tests`: names in this module's test file,
    or full dotted names (SketcherTests....).  `profile`: a launch profile of the GUI runner (hidpi) for a defect only that shows."""
    M.append(dict(name=name, desc=desc, patches=patches, tests=tests, profile=profile))


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
    [(PANEL, "        if not self._rollback():\n            self._restore(final)\n            self._session_broken(tr(\"the undo steps of the edit could not be rolled back\"))\n            return False\n        text = tr(\"The edit was cancelled", "        if False and not self._rollback():\n            self._restore(final)\n            self._session_broken(tr(\"the undo steps of the edit could not be rolled back\"))\n            return False\n        text = tr(\"The edit was cancelled")],
    [CANCEL],
)
mutant(
    "finish_applies_before_rolling_back",
    "Finish gives the result to update_definition first and rolls back after, so the rollback undoes the result",
    [(
        PANEL,
        '        if not self._rollback():\n            self._restore(final)\n            self._session_broken(tr("the undo steps of the edit could not be rolled back"))\n            return False\n        removed_constraints = []\n        if changes:\n            try:\n                applied = B.update_definition(sk, s["definition"], changes)',
        '        removed_constraints = []\n        if changes:\n            try:\n                applied = B.update_definition(sk, s["definition"], changes)\n                self._rollback()',
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


mutant(
    "finish_does_not_name_what_it_undoes",
    "Finish rolls back moved plain geometry and deleted constraints without saying so first",
    [(PANEL, '        if undone and s["warned"] != self._signature(out):\n            # Finish keeps', '        if False and undone and s["warned"] != self._signature(out):\n            # Finish keeps')],
    [OUTSIDE, CPLAIN],
)
mutant(
    "the_warning_is_not_rearmed_by_a_further_change",
    "a second Finish goes on after any further change, because the state it compares never differs",
    [(PANEL, '        return (B.content_hash(sk, "Geometry"), B.content_hash(sk, "Constraints"), repr(sorted(out.items())))', "        return 0")],
    [REARM],
)
mutant(
    "nothing_had_changed_whatever_happened",
    "the status says nothing had changed whenever the block's definition did not",
    [(PANEL, '        elif not (undone or dropped or res["moved_together"]):', "        elif True:")],
    [NOTHING, OUTSIDE],
)
mutant(
    "moved_plain_geometry_is_not_seen",
    "the comparison with the start never finds plain geometry that moved",
    [(API, "        if len(now) != len(nums) or any(abs(a - b) > _OUTSIDE_TOL for a, b in zip(now, nums)):", "        if False:")],
    [OUTSIDE, CPLAIN],
)
mutant(
    "a_deleted_constraint_is_not_seen",
    "the comparison with the start never finds a constraint that was deleted",
    [(API, "                gone.append(label)\n", "                pass\n")],
    [OUTSIDE],
)
mutant(
    "a_rigid_move_is_absorbed_into_the_definition",
    "the members that moved together are read through the start placement, so the definition changes and every sibling shifts",
    [(API, "    fit = rigid_fit(defn, alive) if kinds else None", "    fit = None")],
    [RIGID, RIGIDADD, H_RIGID],
)
mutant(
    "a_lone_member_counts_as_a_rigid_move",
    "one member moved is read as the instance moving",
    [(API, "    if len(keys) < 2:", "    if len(keys) < 1:")],
    [H_LONE],
)
mutant(
    "the_discarded_move_is_not_said",
    "a rigid move that is not kept is not mentioned in the status",
    [(PANEL, '        if res["moved_together"]:\n            text +=', '        if False:\n            text +=')],
    [RIGID],
)
mutant(
    "rollback_depth_found_by_name",
    "the depth to roll back is found by the session entry's name on the stack, so a later entry with that name stops Cancel short",
    [(PANEL, '        n = s["depth"]\n', '        n = (names.index(s["entry"]) + 1) if s["entry"] in names else 0\n')],
    [NAME],
)
mutant(
    "commits_are_not_counted",
    "the session's depth does not grow with the steps committed during it",
    [(PANEL, '            self.session["depth"] += 1', '            self.session["depth"] += 0')],
    [FULL, NAME],
)
mutant(
    "a_session_beyond_the_stack_is_rolled_back_anyway",
    "the proof that the session's first entry is still on the stack is dropped",
    [
        (PANEL, '        if n < 1 or n > len(names) or names[n - 1] != s["entry"]:\n            return None\n', "        if n < 1:\n            return None\n"),
        (PANEL, '        if names[n:] != s["tail"][: len(names) - n]:\n            return None\n', ""),
    ],
    [LONG],
)
mutant(
    "a_stack_of_nothing_is_not_noticed",
    "a session starts although the undo history did not take its first step",
    [(PANEL, "        if self._stack_depth_to_start() is None:\n            # the undo history did not take", "        if False and self._stack_depth_to_start() is None:\n            # the undo history did not take")],
    [MODE0],
)
mutant(
    "an_open_operation_is_not_noticed",
    "Finish, Cancel and the start of an edit ignore a transaction a tool or macro still has open",
    [(PANEL, '        if getattr(self.sketch.Document, "HasPendingTransaction", False):\n            self.status.setText(tr("A tool or macro still has an operation open. Let it finish, then choose', '        if False:\n            self.status.setText(tr("A tool or macro still has an operation open. Let it finish, then choose'),
     (PANEL, '            if getattr(doc, "HasPendingTransaction", False):\n', "            if False:\n")],
    [OPEN],
)
mutant(
    "the_panel_ignores_document_activation",
    "the panel has no way to go quiet when the user looks at another document",
    [(PANEL, "    def sync_activation(self, active=None):", "    def sync_activation_unused(self, active=None):"),
     (PANEL, "        self.sync_activation()\n        if self._dormant or not self._alive():", "        if self._dormant or not self._alive():")],
    [ACT],
)
mutant(
    "finish_skips_the_editing_check",
    "Finish acts on a sketch the editor no longer has open",
    [(PANEL, "        if not self._editing_ok() or self._busy_elsewhere() or not self._session_valid():\n            return False\n        s = self.session\n        sk = self.sketch\n", "        if self._busy_elsewhere() or not self._session_valid():\n            return False\n        s = self.session\n        sk = self.sketch\n")],
    [NOEDIT],
)
mutant(
    "place_by_click_ignores_the_angle_field",
    "a placing click inserts at angle 0 whatever the form says",
    [(PANEL, '            return B.insert(self.sketch, d, x, y, parse_angle(self.angle_edit.text()))\n\n        res = self._run(go)\n        if res and res is not True:\n            self.status.setText(tr("Inserted %s.") % res)\n\n    # -- explode', '            return B.insert(self.sketch, d, x, y, 0.0)\n\n        res = self._run(go)\n        if res and res is not True:\n            self.status.setText(tr("Inserted %s.") % res)\n\n    # -- explode')],
    [ANGLE],
)
mutant(
    "a_definition_may_take_an_instances_name",
    "define accepts the name of an existing instance",
    [(API, "    if name in data[\"inst\"]:\n        raise SketchBlockError(\"'%s' is the name of a block instance; a block needs a name of its own\" % name)\n", "")],
    [H_NAMES],
)
mutant(
    "an_instance_name_may_collide",
    "insert numbers its instance without stepping over a name that is taken",
    [(API, "    while \"%s_%d\" % (name, data[\"n\"]) in data[\"inst\"] or \"%s_%d\" % (name, data[\"n\"]) in data[\"defs\"]:\n        data[\"n\"] += 1  # a name already taken by an instance or by a block of that name is skipped\n", "")],
    [H_NAMES],
)
mutant(
    "click_flipped_in_device_pixels",
    "a click's y is flipped in device pixels, one device pixel off at a pixel ratio of 2 (only the hidpi launch shows it)",
    [(PANEL, "    y = int(math.floor((gl.height() - pos.y() - 1.0) * dpr + 0.5))", "    y = int(math.floor(gl.height() * dpr - 1.0 - pos.y() * dpr + 0.5))")],
    [CLICK],
    profile="hidpi",
)
mutant(
    "insert_solves_twice",
    "the API's insert solves the sketch a second time",
    [(API, "        sk.BlockData = _dump(data)\n        sk.solve()\n    return iname", "        sk.BlockData = _dump(data)\n        sk.solve()\n        sk.solve()\n    return iname")],
    [SOLVES],
)


def target(fname, build):
    return os.path.join(build, "Mod", "Sketcher", fname)


# -- run 061, second verifier's repairs: what Finish and Cancel undo outside the block, and the message that stays ----------
OUTS = "TestBlocksPanelOutsideTheSession"
O_OBJECTS = t(OUTS, "test_other_objects_are_named_before_finish_and_before_cancel")
O_CANCEL1 = t(OUTS, "test_a_cancel_with_only_member_changes_is_one_press_and_restores_the_sketch")
O_PAD = t(OUTS, "test_a_recompute_of_a_dependent_is_not_a_change_but_an_edit_of_its_input_is")
O_CLEAN = t(OUTS, "test_a_recompute_in_a_session_on_an_untouched_document_raises_no_warning")
O_COST = t(OUTS, "test_the_whole_document_is_read_only_at_start_finish_and_cancel")
O_SKETCH = t(OUTS, "test_every_other_part_of_the_sketch_is_named_when_it_changes")
O_NET = t(OUTS, "test_a_change_between_two_presses_that_nets_to_zero_does_not_ask_again")
O_REASK = t(OUTS, "test_a_real_change_between_two_presses_asks_again")
O_STAY = t(OUTS, "test_the_message_of_a_broken_session_stays_until_the_next_action")
O_STAYLONG = t(OUTS, "test_the_message_of_a_session_longer_than_the_stack_stays")

mutant(
    "outside_objects_not_read",
    "Finish and Cancel do not look at the rest of the document",
    [(API, "    if full:\n        out[\"sketch_props\"]", "    if False:\n        out[\"sketch_props\"]")],
    [O_OBJECTS, O_SKETCH],
)
mutant(
    "outside_added_objects_not_named",
    "an object added during the session is not counted",
    [(API, "for n in now if n not in before)", "for n in now if False)")],
    [O_OBJECTS],
)
mutant(
    "outside_deleted_objects_not_named",
    "an object deleted during the session is not counted",
    [(API, "for n in before if n not in now)", "for n in before if False)")],
    [O_OBJECTS],
)
mutant(
    "outside_shapes_count_as_changes",
    "a recomputed shape of a dependent of the sketch counts as the user's change",
    [(API, "        if \"Shape\" in obj.getTypeIdOfProperty(prop):\n            return False\n", "        if False:\n            return False\n")],
    [O_PAD, O_CLEAN],
)
mutant(
    "outside_computed_flags_ignored",
    "properties flagged Output, ReadOnly or Transient count as the user's change",
    [(API, "_COMPUTED_STATUS = (\"Output\", \"ReadOnly\", \"Transient\")", "_COMPUTED_STATUS = ()")],
    [O_PAD, O_CLEAN],
)
mutant(
    "outside_input_of_a_dependent_ignored",
    "a Pad's Length, an input of an object over the sketch, is not compared",
    [(API, "    if prop in _DERIVED_FIELDS or (own and prop in _OWN_FIELDS):\n", "    if prop in _DERIVED_FIELDS or (own and prop in _OWN_FIELDS) or prop == \"Length\":\n")],
    [O_PAD, O_OBJECTS],
)
mutant(
    "outside_other_sketches_geometry_skipped",
    "the geometry of another sketch is left out like the session sketch's own",
    [(API, "(own and prop in _OWN_FIELDS)", "(prop in _OWN_FIELDS)")],
    [O_OBJECTS],
)
mutant(
    "cancel_does_not_warn",
    "Cancel rolls the document back without naming what is outside the block",
    [(PANEL, "if undone and s[\"warned\"] != self._signature(out):\n            # Cancel", "if False and s[\"warned\"] != self._signature(out):\n            # Cancel")],
    [O_OBJECTS],
)
mutant(
    "cancel_always_warns",
    "Cancel asks twice even when only the block's members changed",
    [(PANEL, "if undone and s[\"warned\"] != self._signature(out):\n            # Cancel", "if s[\"warned\"] != self._signature(out):\n            # Cancel")],
    [O_CANCEL1],
)
mutant(
    "outside_constraint_rename_not_compared",
    "a renamed constraint is not a change outside the block",
    [(API, "        if f[0] != facts[0]:", "        if False:")],
    [O_SKETCH],
)
mutant(
    "outside_constraint_flags_not_compared",
    "a constraint made inactive, reference or virtual is not a change outside the block",
    [(API, "        if f[1:] != facts[1:]:", "        if False:")],
    [O_SKETCH],
)
mutant(
    "outside_construction_not_compared",
    "the construction flag of plain geometry is not compared",
    [(API, "        if bool(sk.getConstruction(i)) != flag:", "        if False:")],
    [O_SKETCH],
)
mutant(
    "outside_sketch_properties_not_compared",
    "an expression, the placement or the external geometry of the sketch is not compared",
    [(API, "out[\"sketch_props\"] = _state_diff(", "out[\"sketch_props\"] = [] and _state_diff(")],
    [O_SKETCH],
)
mutant(
    "outside_hash_includes_the_clock",
    "a property's content is hashed with the second it was written in, so an untouched document reads as changed after a tick",
    [(API, "return hash(tuple((n, z.read(n)) for n in sorted(z.namelist())))", "return hash(raw)")],
    [O_CLEAN],
)
mutant(
    "outside_state_at_start_empty",
    "the document's state is not taken when the session starts",
    [(API, "\"doc\": _document_state(sk)}", "\"doc\": {}}")],
    [O_CLEAN],
)
mutant(
    "warning_signature_counts_undo_steps",
    "a change that nets to zero between two presses asks again because the undo stack moved",
    [(PANEL, "        return (B.content_hash(sk, \"Geometry\")", "        return (sk.Document.UndoCount, B.content_hash(sk, \"Geometry\")")],
    [O_NET],
)
mutant(
    "warning_signature_ignores_outside",
    "a change outside the sketch between two presses does not ask again",
    [(PANEL, "repr(sorted(out.items())))", "\"\")")],
    [O_REASK],
)
mutant(
    "document_read_on_every_refresh",
    "the whole document is read for the session label on every refresh, not only at start, Finish and Cancel",
    [(PANEL, "    def _outside(self, full=False):", "    def _outside(self, full=True):")],
    [O_COST],
)
mutant(
    "break_message_overwritten_by_refresh",
    "the list refresh puts the definition and instance count over the message that ended an edit",
    [(PANEL, "            if self._sticky is None:  # a message", "            if True:  # a message")],
    [O_STAY, O_STAYLONG],
)
mutant(
    "break_message_never_cleared",
    "a message that ended an edit stays after the user acts in the panel",
    [(PANEL, "        if self._sticky is not None:\n            self._sticky = None\n            self.schedule()", "        if False:\n            self._sticky = None\n            self.schedule()")],
    [O_STAY, O_STAYLONG],
)


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
            f.write(DRIVER % ([n if n.startswith("SketcherTests.") else MOD + "." + n for n in m["tests"]],))
        p = subprocess.Popen(
            [sys.executable, "-I", runner, "script", script, "--timeout", "300"] + (["--profile", m["profile"]] if m.get("profile") else []),
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
