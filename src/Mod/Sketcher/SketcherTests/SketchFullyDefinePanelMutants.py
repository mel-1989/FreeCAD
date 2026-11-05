# SPDX-License-Identifier: LGPL-2.1-or-later
"""Mutation check for the Fully Define dialog (G10 slice 4b, acceptance check 12).

Each planted defect is a byte-exact text replacement in one or more files of the *built* tree
(``$FC_BUILD_DIR/Mod/Sketcher/``, the copy the GUI loads; for the C++ mutant, the source file, followed by a rebuild).
The files are patched, the tests that must notice the defect run in a fresh GUI process, and the original bytes are
put back before the next mutant, also when the run fails or is interrupted.  A mutant is *caught* when a test method
fails or errors (an assertion, not a hang: a run that times out is reported as ``no-result``); a crash in
``setUpClass`` is reported as ``crash`` and does not count.  The runner, verdicts and restore check are slice 4a's
(``SketchDiagnosisPanelMutants``).

Plain Python, not run inside FreeCAD.  Needs the harness environment (``source harness/env.sh``)::

    python3 SketchFullyDefinePanelMutants.py [--only name ...] [--dry] [--list]

``--dry`` only checks that every pattern matches its file exactly once.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import SketchDiagnosisPanelMutants as base  # noqa: E402

SUITE = "TestSketcherFullyDefinePanelGui.TestSketcherFullyDefinePanelGui"
FD = "SketcherFullyDefinePanel.py"
DP = "SketcherDiagnosisPanel.py"
AUTO = "SketcherAutoDefine.py"
DIAG = "SketcherDiagnosis.py"
CPP = "TaskSketcherMessages.cpp"

M = []


def mutant(name, desc, patches, tests, runner="gui"):
    """`patches`: list of (file, old, new); each pattern must match its file exactly once."""
    M.append(dict(name=name, desc=desc, patches=patches, tests=tests, runner=runner, env={}, suite=SUITE, hang_catches=False))


mutant(
    "candidate_applied_to_stale_sketch",
    "document events never invalidate a result and the fingerprint guard is gone: a candidate computed earlier is applied",
    [
        (DP, '                p.invalidate(tr("The sketch changed."))\n', "                pass\n"),
        (DP, 'self.panel.invalidate(tr("Undo changed the document."))', "pass"),
        (FD, '        if self._fingerprint() != self.fingerprint:\n            self.invalidate(tr("The sketch changed."))\n            return False\n', ""),
    ],
    ["testStaleAfterAConstraintIsAdded", "testStaleAfterADatumChanges", "testStaleAfterUndo"],
)
mutant(
    "apply_outside_a_transaction",
    "Apply changes the sketch without opening a transaction, so there is no undo entry",
    [
        (FD, "            doc.openTransaction(name)\n", "            pass\n"),
        (FD, "                doc.commitTransaction()\n", "                pass\n"),
    ],
    ["testEveryApplyIsOneNamedUndoStepAndUndoRedoAreExact"],
)
mutant(
    "two_undo_entries",
    "Apply leaves a second transaction behind (a recompute of the document), so Undo needs two steps",
    [
        (
            FD,
            "                doc.commitTransaction()\n",
            "                doc.commitTransaction()\n                doc.openTransaction(name)\n                doc.recompute()\n                doc.commitTransaction()\n",
        )
    ],
    ["testEveryApplyIsOneNamedUndoStepAndUndoRedoAreExact"],
)
mutant(
    "candidates_out_of_order",
    "candidates are listed in reverse of the API's order",
    [(FD, '        for k, cand in enumerate(r["candidates"]):\n', '        for k, cand in enumerate(r["candidates"][::-1]):\n')],
    ["testContentsEqualTheApiAtEveryStripLevel"],
)
mutant(
    "value_from_the_wrong_candidate",
    "every candidate row lists the constraints of the first candidate",
    [(FD, '            for c in cand["constraints"]:\n                self._item(top,', '            for c in r["candidates"][0]["constraints"]:\n                self._item(top,')],
    ["testContentsEqualTheApiAtEveryStripLevel"],
)
mutant(
    "preferred_scheme_ignored",
    "the preferred scheme never reaches propose",
    [(FD, "prefer=self.preferred_scheme()", "prefer=None")],
    ["testPreferredSchemeAndKeepReachPropose", "testPreferredSchemeChangesTheOrderOnTheCorpus"],
)
mutant(
    "keep_not_passed",
    "the existing constraints to keep never reach propose",
    [(FD, "AD.propose(self.sketch, keep=keep,", "AD.propose(self.sketch, keep=None,")],
    ["testPreferredSchemeAndKeepReachPropose"],
)
mutant(
    "refused_apply_leaves_constraints_behind",
    "a refused Apply commits the transaction instead of aborting it, so the constraints stay",
    [(FD, "                doc.abortTransaction()\n                refused = exc\n", "                doc.commitTransaction()\n                refused = exc\n")],
    ["testARefusedApplyChangesNothingAndOtherCandidatesCanStillBeTried"],
)
mutant(
    "temporary_document_left_behind",
    "propose does not close its hidden scratch document",
    [(DIAG, "            FreeCAD.closeDocument(self.name)\n        finally:", "            pass\n        finally:")],
    ["testNoSideEffectsWhenCancelled", "testNoSideEffectsWhenProposeRaisesAfterOpeningItsScratchDocument"],
)
mutant(
    "cancel_ignored",
    "the Stop button sets nothing",
    [(DP, "    def request_cancel(self):\n        self._cancel = True\n", "    def request_cancel(self):\n        pass\n")],
    ["testNoSideEffectsWhenCancelled"],
)
mutant(
    "progress_cancel_never_raised",
    "a progress callback's request to stop is noticed and dropped",
    [(DIAG, '        raise DiagnosisCancelled("diagnosis cancelled")', "        pass")],
    ["testProgressCallbackCancelsAndLeavesNothing"],
)
mutant(
    "link_shown_when_fully_defined",
    "the messages box offers the Fully Define link in every state",
    [(CPP, 'labelFullyDefine->setVisible(state == QStringLiteral("under_constrained"));', 'labelFullyDefine->setVisible(true || state == QStringLiteral("under_constrained"));')],
    ["testMessagesBoxOffersTheLinkOnlyWhenUnderConstrained"],
    runner="gui+build",
)
mutant(
    "dialog_leaves_edit_mode",
    "the search leaves edit mode",
    [(FD, "        keep = list(range(len(self.sketch.Constraints)))\n", "        Gui.ActiveDocument.resetEdit()\n        keep = list(range(len(self.sketch.Constraints)))\n")],
    ["testOpeningLeavesEditModeAndTheActiveDocument", "testNoSideEffectsOnTheCorpus"],
)
mutant(
    "reduced_gate_shown_as_full",
    "the outcome always reports the full re-solve check",
    [(FD, '    if info["gate"] == "full":\n', "    if True:\n")],
    ["testAReducedGateIsStatedToTheUserWithItsReason"],
)
mutant(
    "out_of_time_shown_as_cannot_close",
    "a search that ran out of time is reported as having found no way",
    [(FD, '    if status == "cannot_close":\n        if r.get("out_of_time"):\n', '    if status == "cannot_close":\n        if False:\n')],
    ["testTimeBudgetRunningOutIsReportedAsSuchAndNeverAsCannotClose"],
)
mutant(
    "out_of_time_flag_dropped",
    "propose does not mark a result that ran out of time",
    [(AUTO, '                        result["out_of_time"] = True\n', "                        pass\n")],
    ["testTimeBudgetRunningOutIsReportedAsSuchAndNeverAsCannotClose"],
)
mutant(
    "unclosed_entries_not_shown",
    "the schemes that could not close the sketch are not listed",
    [(FD, '        for entry in r["unclosed"]:\n            self._item(self.tree, unclosed_text(entry), {"kind": "unclosed"})\n', "")],
    ["testStatusesThatOfferNothingEachGiveTheirOwnSentence", "testTimeBudgetRunningOutIsReportedAsSuchAndNeverAsCannotClose"],
)
mutant(
    "preview_shows_the_first_candidate",
    "the list of what would be added always shows the first candidate",
    [(FD, "        self.preview_label.show()\n        for c in cand[", '        self.preview_label.show()\n        cand = self.result["candidates"][0]\n        for c in cand[')],
    ["testChoosingACandidateListsWhatItWouldAddAndChoosingNothingShowsNothing"],
)
mutant(
    "apply_while_another_document_is_in_view",
    "Apply no longer asks whether the editor is on the sketch's document",
    [(FD, "        if not self._editing_here():\n            return False\n        if self._fingerprint()", "        if self._fingerprint()")],
    ["testLookingAtAnotherDocumentKeepsTheDialogWaitingWithApplyDisabled"],
)
mutant(
    "users_selection_lost",
    "the user's selection is not put back after the search",
    [(DP, "        self._restore_selection(kept)\n", "        pass\n")],
    ["testNoSideEffectsOnTheCorpus"],
)


def target(path_file, build, src):
    if path_file == CPP:
        return os.path.join(src, "src", "Mod", "Sketcher", "Gui", CPP)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        for m in M:
            print("%-40s %s" % (m["name"], m["desc"]))
        print(len(M), "mutants")
        return 0
    build, src, runner, ctl = base.paths()
    rows = []
    for m in M:
        if a.only and m["name"] not in a.only:
            continue
        files = patched_files(m, build, src)
        if files is None:
            print("%-40s PATTERN DOES NOT MATCH ONCE" % m["name"])
            rows.append((m["name"], "bad-pattern", []))
            continue
        if a.dry:
            print("%-40s pattern ok" % m["name"])
            continue
        t0 = time.time()
        try:
            for path, (_, mutated) in files.items():
                with open(path, "wb") as f:
                    f.write(mutated)
            if m["runner"] == "gui+build":
                base.rebuild(ctl)
            out = base.run_tests(m, build, runner)
            if base.verdict(out)[0] == "no-result":  # a GUI process that does not exit loses its buffered output: once more
                out = base.run_tests(m, build, runner)
        finally:
            for path, (original, _) in files.items():
                with open(path, "wb") as f:
                    f.write(original)
            if m["runner"] == "gui+build":
                base.rebuild(ctl)
        for path, (original, _) in files.items():
            assert base.sha(open(path, "rb").read()) == base.sha(original), "restore failed for %s" % path
        status, failed = base.verdict(out)
        if status == "no-result":
            print("\n".join("    | " + ln for ln in out.splitlines()[-8:]))
        rows.append((m["name"], status, failed))
        print("%-40s %-9s %-9s %5.0fs  %s" % (m["name"], status, base.kinds(out), time.time() - t0, ", ".join(failed)[:150]))
        sys.stdout.flush()
    if not a.dry:
        caught = sum(1 for r in rows if r[1] == "caught")
        print(json.dumps({"mutants": len(rows), "caught": caught, "not_caught": [r[0] for r in rows if r[1] != "caught"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
