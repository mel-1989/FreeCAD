# SPDX-License-Identifier: LGPL-2.1-or-later
"""Mutation check for the relations panel and the view-only filter (G10 slice 4c, acceptance check 11).

Each planted defect is a byte-exact text replacement in one or more files: the *built* tree's Python
(``$FC_BUILD_DIR/Mod/Sketcher/``, the copy the GUI loads) or, for the C++ mutants, the source file in ``$FC_SRC`` followed
by a rebuild.  The tests that must notice the defect run in a fresh GUI process; the original bytes are put back before
the next mutant, also when the run fails or is interrupted, and the restore is checked by hash.  A mutant is *caught* when
a named test method fails or errors, and ``assertion`` in the kinds column says it failed on an assertion, which is what
shows a test noticed the defect.  A run that prints no result is reported as ``no-result`` and does not count.

The tests are run through a small driver script (``unittest`` with the named methods only) because the GUI binary's ``-t``
takes one test name.  Plain Python, not run inside FreeCAD.  Needs the harness environment (``source harness/env.sh``)::

    python3 SketchRelationsPanelMutants.py [--only name ...] [--dry] [--list]

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

CLASS = "SketcherTests.TestSketcherRelationsPanelGui.TestSketcherRelationsPanelGui"
RP = "SketcherRelationsPanel.py"
ECC = "EditModeConstraintCoinManager.cpp"

CH = "testChoosingARelationHighlightsItAndChangesNothingInTheDocument"
FC = "testFocusClearsOnLeavingAndKeepsTheUsersOwnSelection"
VANISHED = "testFocusOnAVanishedConstraintIsRefused"
PIXELS = "testFilterHidesAndRestoresPixelForPixel"
MERGED = "testAMaskedMemberOfAMergedIconIsNotDrawn"
PICK = "testAMaskedConstraintIsNotPickableAndStaysMaskedThroughRedraws"
CONTENT = "testFilterContentsAreTheRelationsOrTheType"
ANNOUNCE = "testFilterAnnouncesItselfAndNeverOutlivesThePanelOrTheEditor"
SOLVE = "testFilteringChangesNothingAboutTheSketchItsSolutionOrItsMessages"
ADDED = "testStaleWhenAConstraintIsAdded"
DELETED = "testStaleWhenAConstraintIsDeleted"
OBSERVERS = "testObserversAreRemovedOnClose"
POINTS = "testPointsAndEdgesAreDifferentElements"
CHAIN = "testCoincidentChainsShowOnlyWhatTheDataSays"
THIRD = "testThirdSlotsAndInternalAlignmentAreFound"

M = []


def mutant(name, desc, patches, tests, runner="gui"):
    """`patches`: list of (file, old, new); each pattern must match its file exactly once."""
    M.append(dict(name=name, desc=desc, patches=patches, tests=tests, runner=runner))


mutant(
    "stale_index_after_a_change",
    "after a constraint is added or deleted the panel keeps reading its old index, so rows name constraints that moved",
    [(RP, "        self.index = None\n        self.source = None\n        self._rows_current = False\n        self.tree.blockSignals(True)\n        self.tree.clear()", "        self.source = None\n        self._rows_current = False\n        self.tree.blockSignals(True)\n        self.tree.clear()")],
    [ADDED, DELETED],
)
mutant(
    "relation_listed_on_the_wrong_element",
    "an edge's list is read from the next element",
    [(RP, "        ids = idx.ids_on(geo, pos)\n", "        ids = idx.ids_on(geo + 1 if geo >= 0 else geo, pos)\n")],
    [POINTS, CHAIN],
)
mutant(
    "filter_hides_without_saying",
    "the filter notice is never shown",
    [(RP, "        self.banner.setVisible(on)\n", "        self.banner.setVisible(False)\n")],
    [ANNOUNCE, PIXELS],
)
mutant(
    "filter_survives_closing_the_panel",
    "closing the panel leaves the view filter on",
    [(RP, "        try:\n            self._set_view_filter(None)\n        except Exception:\n            pass\n        global _panel", "        global _panel")],
    [ANNOUNCE],
)
mutant(
    "highlight_left_behind",
    "taking the highlight down removes nothing from the selection",
    [(RP, "                    Gui.Selection.removeSelection(self.doc_name, self.sketch_name, n)\n", "                    pass\n")],
    [FC, CH],
)
mutant(
    "filter_changes_the_sketch",
    "showing a filter also puts the hidden constraints in the sketch's own virtual space, as stock's filter does",
    [(RP, "        self.filter_spec = spec\n        self.filter_count = len(ids)\n", "        self.sketch.setVirtualSpace([i for i in range(self.sketch.ConstraintCount) if i not in ids], True)\n        self.filter_spec = spec\n        self.filter_count = len(ids)\n")],
    [SOLVE, PIXELS],
)
mutant(
    "filter_kept_through_a_change",
    "a change to the constraints leaves the old filter in the view until the panel reads the sketch again",
    [(RP, "            self._resume_filter = None\n        self._set_view_filter(None)\n        self.filter_spec = None\n", "            self._resume_filter = None\n        self.filter_spec = None\n")],
    [ADDED],
)
mutant(
    "focus_replaces_the_users_selection",
    "choosing a relation clears the user's own selection first",
    [(RP, "        have = self._selected_names()\n", "        Gui.Selection.clearSelection()\n        have = self._selected_names()\n")],
    [CH, FC],
)
mutant(
    "observer_left_on_close",
    "closing the panel leaves its selection observer registered",
    [(RP, ', (Gui.Selection.removeObserver, "_sw")', "")],
    [OBSERVERS],
)
mutant(
    "document_observer_left_on_close",
    "closing the panel leaves its document observer registered",
    [(RP, '(App.removeDocumentObserver, "_dw"), ', "")],
    [OBSERVERS],
)
mutant(
    "editor_observer_left_on_close",
    "closing the panel leaves its editor observer registered",
    [(RP, '(Gui.removeDocumentObserver, "_gw"), ', "")],
    [OBSERVERS],
)
mutant(
    "point_lists_whole_element_constraints",
    "a vertex lists the constraints on its whole element",
    [(RP, "        if pos:\n            return self.by_point.get((geo, pos), [])\n        return self.by_geo.get(geo, [])\n", "        return self.by_geo.get(geo, [])\n")],
    [POINTS, CHAIN],
)
mutant(
    "coincident_chain_followed",
    "a point also lists the constraints of the points it is coincident with",
    [(RP, "            return self.by_point.get((geo, pos), [])\n", "            got = set(self.by_point.get((geo, pos), []))\n            for i in list(got):\n                for g, p in self.refs[i]:\n                    if p and self.types[i] == 'Coincident':\n                        got |= set(self.by_point.get((g, p), []))\n            return sorted(got)\n")],
    [CHAIN],
)
mutant(
    "third_slot_ignored",
    "a constraint is not found through its Third slot",
    [(RP, "            if third != GEO_UNDEF:\n", "            if False:\n")],
    [THIRD],
)
mutant(
    "origin_part_of_the_x_axis",
    "the x axis also lists the constraints on the origin",
    [(RP, "                if geo == -1 and pos:\n                    continue\n", "")],
    [POINTS],
)
mutant(
    "show_all_leaves_the_view_filtered",
    "Show all clears the panel's state but not the view's filter",
    [(RP, "    def clear_filter(self, *_):\n        self._set_view_filter(None)\n", "    def clear_filter(self, *_):\n")],
    [PIXELS, ANNOUNCE],
)
mutant(
    "focus_on_a_vanished_constraint",
    "focus selects a constraint number without checking it still exists",
    [(RP, "        if idx is None or not 0 <= i < idx.count or i >= len(self.sketch.Constraints):\n", "        if idx is None:\n")],
    [VANISHED],
)
mutant(
    "filter_ids_off_by_one",
    "the filter shows the constraints after the ones it lists",
    [(RP, "        return bool(SketcherGui.setConstraintFilter(None if ids is None else list(ids)))", "        return bool(SketcherGui.setConstraintFilter(None if ids is None else [i + 1 for i in ids]))")],
    [CONTENT],
)
mutant(
    "masked_member_of_a_merged_icon_mapped_to_the_wrong_constraint",
    "the icon queue asks the filter about the next constraint, so a merged icon shows a masked member",
    [(ECC, "            && constraint->isVisible && !isFilteredOut(constrId);\r\n", "            && constraint->isVisible && !isFilteredOut(constrId + 1);\r\n")],
    [MERGED],
    runner="gui+build",
)
mutant(
    "masked_constraint_still_pickable",
    "the filter hides the icon but leaves the constraint's node switched on, so it can still be picked",
    [(ECC, "                && !isFilteredOut(static_cast<int>(i));\r\n", "                ;\r\n")],
    [PICK],
    runner="gui+build",
)
mutant(
    "mask_forgotten_by_a_rebuild",
    "rebuilding the constraint nodes clears the filter",
    [(ECC, "rebuildConstraintNodes(const GeoListFacade& geolistfacade)\r\n{\r\n    const std::vector<Sketcher::Constraint*>& constrlist\r\n", "rebuildConstraintNodes(const GeoListFacade& geolistfacade)\r\n{\r\n    clearConstraintFilter();\r\n    const std::vector<Sketcher::Constraint*>& constrlist\r\n")],
    [PICK],
    runner="gui+build",
)
mutant(
    "mask_forgotten_by_a_redraw",
    "updating the virtual space clears the filter",
    [(ECC, "    bool isshownvirtualspace = ViewProviderSketchCoinAttorney::isShownVirtualSpace(viewProvider);\r\n\r\n    if (constrlist.size() == vConstrType.size()) {\r\n", "    bool isshownvirtualspace = ViewProviderSketchCoinAttorney::isShownVirtualSpace(viewProvider);\r\n    clearConstraintFilter();\r\n\r\n    if (constrlist.size() == vConstrType.size()) {\r\n")],
    [PIXELS, PICK],
    runner="gui+build",
)


def target(path_file, build, src):
    if path_file.endswith(".cpp"):
        return os.path.join(src, "src", "Mod", "Sketcher", "Gui", path_file)
    return os.path.join(build, "Mod", "Sketcher", path_file)


def patched_files(m, build, src):
    """{path: (original bytes, mutated bytes)} or None when a pattern does not match exactly once."""
    out = {}
    for fname, old, new in m["patches"]:
        path = target(fname, build, src)
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
    suite.addTests(unittest.defaultTestLoader.loadTestsFromName(%r + "." + n))
unittest.TextTestRunner(stream=sys.stdout, verbosity=1).run(suite)
"""


def run_tests(m, runner):
    """The named test methods in one GUI process; returns its output."""
    with tempfile.TemporaryDirectory() as tmp:
        script = os.path.join(tmp, "run_selected.py")
        with open(script, "w") as f:
            f.write(DRIVER % (list(m["tests"]), CLASS))
        p = subprocess.Popen(
            [sys.executable, "-I", runner, "script", script, "--timeout", "420"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        out, _ = p.communicate(timeout=900)
    return out


def verdict(out):
    """caught / survived / crash / no-result, from the unittest text."""
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
            print("%-62s %s" % (m["name"], m["desc"]))
        print(len(M), "mutants")
        return 0
    build, src, runner, ctl = base.paths()
    rows = []
    for m in M:
        if a.only and m["name"] not in a.only:
            continue
        files = patched_files(m, build, src)
        if files is None:
            print("%-62s PATTERN DOES NOT MATCH ONCE" % m["name"])
            rows.append((m["name"], "bad-pattern", []))
            continue
        if a.dry:
            print("%-62s pattern ok" % m["name"])
            continue
        t0 = time.time()
        try:
            for path, (_, mutated) in files.items():
                with open(path, "wb") as f:
                    f.write(mutated)
            if m["runner"] == "gui+build":
                base.rebuild(ctl)
            out = run_tests(m, runner)
            if verdict(out)[0] == "no-result":
                out = run_tests(m, runner)
        finally:
            for path, (original, _) in files.items():
                with open(path, "wb") as f:
                    f.write(original)
            if m["runner"] == "gui+build":
                base.rebuild(ctl)
        for path, (original, _) in files.items():
            assert base.sha(open(path, "rb").read()) == base.sha(original), "restore failed for %s" % path
        status, failed = verdict(out)
        rows.append((m["name"], status, failed))
        print("%-62s %-9s %-9s %5.0fs  %s" % (m["name"], status, base.kinds(out), time.time() - t0, ", ".join(failed)[:120]))
        sys.stdout.flush()
    if not a.dry:
        caught = sum(1 for r in rows if r[1] == "caught")
        print(json.dumps({"mutants": len(rows), "caught": caught, "not_caught": [r[0] for r in rows if r[1] != "caught"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
