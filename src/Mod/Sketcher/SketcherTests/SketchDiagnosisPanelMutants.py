# SPDX-License-Identifier: LGPL-2.1-or-later
"""Mutation check for the sketch diagnosis panel (G10 slice 4a, acceptance check 10).

Each planted defect is a byte-exact text replacement in one file of the *built* tree (``$FC_BUILD_DIR/Mod/Sketcher/``,
the copy the GUI loads; for the C++ mutant, the source file, followed by a rebuild).  The file is patched, the tests
that must notice it are run in a fresh GUI process (or, for the API mutant, a fresh ``FreeCADCmd``), and the original
bytes are put back before the next mutant, also when the run fails or is interrupted.  A mutant is *caught* when a test
method fails or errors; a crash in ``setUpClass`` (the defect broke the fixture before any test ran) is reported as
``crash`` and does not count.

Plain Python, not run inside FreeCAD.  Needs the harness environment (``source harness/env.sh``)::

    python3 SketchDiagnosisPanelMutants.py [--only name ...] [--dry] [--list]

``--dry`` only checks that every pattern matches its file exactly once.  The tests that kill each mutant are named
beside it; the survey tests are limited to a few corpus cases (``PANEL_SURVEY_CASES``) to keep a run short.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

SUITE = "TestSketcherDiagnosisPanelGui.TestSketcherDiagnosisPanelGui"
APP_SUITE = "SketcherTests.TestSketchDiagnosis.TestSketchDiagnosisProgress"
SURVEY = {"PANEL_SURVEY_CASES": "single_conflict,two_overlapping_conflicts,rectangle_width_conflict"}
PANEL = "SketcherDiagnosisPanel.py"
API = "SketcherDiagnosis.py"
CPP = "TaskSketcherMessages.cpp"

M = []


def mutant(name, desc, file, patches, tests, env=None, runner="gui"):
    M.append(dict(name=name, desc=desc, file=file, patches=patches, tests=tests, env=env or {}, runner=runner))


mutant(
    "selection_names_zero_based",
    "the selection names use the API's 0-based index instead of the editor's 1-based Constraint<n>",
    PANEL,
    [('return "Constraint%d" % (index + 1)', 'return "Constraint%d" % index')],
    ["testChoosingRowsSelectsExactlyTheirConstraints", "testChoosingRedundancyRowsSelects"],
)
mutant(
    "fix_applied_to_stale_sketch",
    "document events never invalidate a result and the fingerprint guard is gone: a fix computed earlier is applied",
    PANEL,
    [
        ('                p.invalidate(tr("The sketch changed."))\n', "                pass\n"),
        ('self.panel.invalidate(tr("Undo changed the document."))', "pass"),
        ("if not self._alive() or self._fingerprint() != self.fingerprint:", "if not self._alive():"),
    ],
    ["testStaleAfterAConstraintIsAdded", "testStaleAfterADatumChanges", "testStaleAfterUndo"],
)
mutant(
    "apply_outside_a_transaction",
    "Apply changes the sketch without opening a transaction, so there is no undo entry",
    PANEL,
    [
        ("            doc.openTransaction(name)\n", "            pass\n"),
        ("            doc.commitTransaction()\n", "            pass\n"),
    ],
    ["testEveryApplyIsOneNamedUndoStepAndUndoRedoAreExact"],
    SURVEY,
)
mutant(
    "two_undo_entries",
    "Apply leaves a second transaction behind (a recompute of the document), so Undo needs two steps",
    PANEL,
    [
        (
            "            doc.commitTransaction()\n",
            "            doc.commitTransaction()\n            doc.openTransaction(name)\n            doc.recompute()\n            doc.commitTransaction()\n",
        )
    ],
    ["testEveryApplyIsOneNamedUndoStepAndUndoRedoAreExact"],
    SURVEY,
)
mutant(
    "set_rows_out_of_order",
    "conflict sets are listed in reverse of the API's order",
    PANEL,
    [("        for k, cs in enumerate(sets):\n", "        for k, cs in enumerate(sets[::-1]):\n")],
    ["testContentsEqualTheApiOnEveryFaultyCase"],
)
mutant(
    "dof_from_the_wrong_fix",
    "every fix row states the degrees of freedom of the set's first fix",
    PANEL,
    [
        (
            "                    fix_row_text(sk, fix),\n",
            '                    fix_row_text(sk, dict(fix, dof_after=cs["fixes"][0]["dof_after"])),\n',
        )
    ],
    ["testContentsEqualTheApiOnEveryFaultyCase"],
)
mutant(
    "temporary_document_left_behind",
    "diagnose does not close its hidden scratch document",
    API,
    [("            FreeCAD.closeDocument(self.name)\n        finally:", "            pass\n        finally:")],
    ["testNoSideEffectsWhenCancelled", "testNoSideEffectsWhenDiagnoseRaises"],
)
mutant(
    "cancel_ignored",
    "the Cancel button's request is dropped, so the diagnosis runs to its end",
    PANEL,
    [("    def request_cancel(self):\n        self._cancel = True\n", "    def request_cancel(self):\n        pass\n")],
    ["testNoSideEffectsWhenCancelled"],
)
mutant(
    "link_shown_in_the_ok_state",
    "the messages box offers the Diagnose link in every state",
    CPP,
    [("    labelDiagnose->setVisible(\r\n        state ==", "    labelDiagnose->setVisible(true || \r\n        state ==")],
    ["testMessagesBoxOffersTheLinkOnlyInTheConflictingAndRedundantStates"],
    runner="gui+build",
)
mutant(
    "panel_leaves_edit_mode",
    "the panel ends the sketch edit when a diagnosis is shown",
    PANEL,
    [
        (
            '        self.fingerprint = fp\n        self.state = "ready"\n',
            '        self.fingerprint = fp\n        self.state = "ready"\n        Gui.ActiveDocument.resetEdit()\n',
        )
    ],
    ["testDiagnosisDoesNotLeaveEditModeOrChangeTheActiveDocument", "testPanelIsADockNotATaskDialog"],
)
mutant(
    "redundancy_suggestion_dropped",
    "a redundancy group is listed without the constraint suggested for removal",
    PANEL,
    [('            rem = g["suggested_removal"]\n', '            rem = g["suggested_removal"]\n            continue\n')],
    ["testContentsEqualTheApiOnEveryFaultyCase", "testChoosingRedundancyRowsSelects"],
)
mutant(
    "wrong_fix_applied",
    "Apply always applies the first fix of the chosen set",
    PANEL,
    [('["fixes"][data["fix"]]', '["fixes"][0]')],
    ["testApplyingEveryOfferedFix"],
    SURVEY,
)
mutant(
    "leaving_edit_mode_not_noticed",
    "the editor's reset-edit event does not invalidate the panel",
    PANEL,
    [
        (
            '                self.panel.invalidate(tr("The sketch is no longer being edited."), close=True)\n        except Exception:',
            "                pass\n        except Exception:",
        )
    ],
    ["testStaleWhenLeavingEditMode"],
)
mutant(
    "choosing_nothing_clears_everything",
    "choosing nothing clears the whole selection, not just what the panel selected",
    PANEL,
    [
        (
            "                Gui.Selection.removeSelection(self.doc_name, self.sketch_name, name)\n",
            "                Gui.Selection.clearSelection()\n",
        )
    ],
    ["testChoosingNothingClearsOnlyWhatThePanelSelected"],
)
mutant(
    "api_cancel_ignored",
    "diagnose does not stop when its progress callback asks it to",
    API,
    [('        raise DiagnosisCancelled("diagnosis cancelled")', "        pass")],
    ["testCancelRaisesAndLeavesNothingBehind"],
    runner="cmd",
)


def sha(b):
    return hashlib.sha256(b).hexdigest()[:12]


def paths():
    build = os.environ.get("FC_BUILD_DIR")
    src = os.environ.get("FC_SRC")
    runner = os.environ.get("CADFORK_GUI_RUNNER")
    ctl = os.environ.get("CADFORK_CTL")
    if not (build and src and runner and ctl):
        sys.exit("source harness/env.sh first (FC_BUILD_DIR, FC_SRC, CADFORK_GUI_RUNNER, CADFORK_CTL)")
    return build, src, runner, ctl


def target(m, build, src):
    if m["file"] == CPP:
        return os.path.join(src, "src", "Mod", "Sketcher", "Gui", CPP)
    return os.path.join(build, "Mod", "Sketcher", m["file"])


def patched(raw, m):
    out = raw
    for old, new in m["patches"]:
        o, n = old.encode(), new.encode()
        if out.count(o) != 1:
            return None
        out = out.replace(o, n)
    return out


def run_tests(m, build, runner):
    env = dict(os.environ, **m["env"])
    if m["runner"] == "cmd":
        cmd = [os.path.join(build, "bin", "FreeCADCmd"), "-t"] + ["%s.%s" % (APP_SUITE, t) for t in m["tests"]]
        env["QT_QPA_PLATFORM"] = "offscreen"
    else:
        cmd = [sys.executable, "-I", runner, "exec", "--timeout", "300", "--", "-t"] + [
            "%s.%s" % (SUITE, t) for t in m["tests"]
        ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    return p.stdout


def verdict(out):
    lines = out.splitlines()
    failed = [ln.split(":", 1)[1].split("(")[0].strip() for ln in lines if ln.startswith(("FAIL: test", "ERROR: test"))]
    crash = any(ln.startswith(("ERROR: setUpClass", "ERROR: setUpModule")) for ln in lines)
    ran_ok = any(ln.strip() == "OK" for ln in lines)
    if failed:
        return "caught", sorted(set(failed))
    if crash:
        return "crash", []
    return ("survived" if ran_ok else "no-result"), []


def rebuild(ctl):
    subprocess.run(
        ["bash", os.path.join(ctl, "harness", "build.sh")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        for m in M:
            print("%-36s %s" % (m["name"], m["desc"]))
        print(len(M), "mutants")
        return 0
    build, src, runner, ctl = paths()
    rows = []
    for m in M:
        if a.only and m["name"] not in a.only:
            continue
        path = target(m, build, src)
        original = open(path, "rb").read()
        mutated = patched(original, m)
        if mutated is None:
            print("%-36s PATTERN DOES NOT MATCH ONCE in %s" % (m["name"], path))
            rows.append((m["name"], "bad-pattern", []))
            continue
        if a.dry:
            print("%-36s pattern ok" % m["name"])
            continue
        t0 = time.time()
        try:
            with open(path, "wb") as f:
                f.write(mutated)
            if m["runner"] == "gui+build":
                rebuild(ctl)
            out = run_tests(m, build, runner)
            if verdict(out)[0] == "no-result":  # a GUI process that does not exit loses its buffered output: once more
                out = run_tests(m, build, runner)
        finally:
            with open(path, "wb") as f:
                f.write(original)
            if m["runner"] == "gui+build":
                rebuild(ctl)
        assert sha(open(path, "rb").read()) == sha(original), "restore failed for %s" % path
        status, failed = verdict(out)
        if status == "no-result":
            print("\n".join("    | " + ln for ln in out.splitlines()[-8:]))
        rows.append((m["name"], status, failed))
        print("%-36s %-9s %5.0fs  %s" % (m["name"], status, time.time() - t0, ", ".join(failed)[:150]))
        sys.stdout.flush()
    if not a.dry:
        caught = sum(1 for r in rows if r[1] == "caught")
        print(json.dumps({"mutants": len(rows), "caught": caught, "not_caught": [r[0] for r in rows if r[1] != "caught"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
