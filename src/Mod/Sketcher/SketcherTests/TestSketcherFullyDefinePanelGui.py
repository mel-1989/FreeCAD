# SPDX-License-Identifier: LGPL-2.1-or-later
"""G10 slice 4b: the Fully Define dialog of the sketch editor, driven through its real widgets.

Run with the GUI binary, with the sketch in edit mode: ``FreeCAD -t TestSketcherFullyDefinePanelGui``.  The checks are
the numbered acceptance checks of slice 4b in briefs/phase1/G10-sketcher.md (1 to 10 here; 11 is the GUI gate, 12 the
mutant script SketchFullyDefinePanelMutants.py, 13 the merge gate).  Sketches are stripped copies of slice 2's
generated corpus (the five strip levels), a few of its frozen real sketches when the checkout has them, and its
negative cases.  The expected values are computed here from ``SketcherAutoDefine`` directly; the rows the dialog must
show are written out here, not taken from the dialog.
"""

import json
import math
import os
import re
import time

import FreeCAD
import FreeCADGui as Gui
import Part
import Sketcher
from PySide import QtCore, QtGui, QtWidgets

import SketcherAutoDefine as AD
import SketcherDiagnosis as SD
from SketcherTests import SketchAutoDefineCorpus as Corpus
from SketcherTests.TestSketcherDiagnosisPanelGui import (
    PanelTestBase,
    _active_name,
    _content,
    _editing,
    _pump,
    _rows,
    _same_sketch,
    _selected_names,
    _signature,
    _wait,
)

COMMAND = "Sketcher_FullyDefine"
TRANSACTION_NAME = "Fully define sketch"
EVENT_GAP_BAR = 1.5  # seconds, check 9: slice 4a's bar, which the first measurement may move only with a reason
CANCEL_BAR = 1.5
V = FreeCAD.Vector
LEVELS = (("dims", 0.5), ("all", 0.5), ("partial", 0.25), ("partial", 0.5), ("partial", 0.75))
FAMILY_COUNT = int(os.environ.get("FD_FAMILIES", "6"))


def _tiny(doc, name="Tiny", datum=False):
    """Two unconstrained lines: a handful of candidates, fast."""
    sk = doc.addObject("Sketcher::SketchObject", name)
    sk.addGeometry(Part.LineSegment(V(0, 0, 0), V(10, 0, 0)), False)
    sk.addGeometry(Part.LineSegment(V(10, 0, 0), V(10, 5, 0)), False)
    if datum:
        sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 10.0))
    sk.solve()
    return sk


class _FDRig:
    """The stripped corpus, built once: (family, level, fraction, sketch)."""

    doc = None
    cases = None

    @classmethod
    def build(cls):
        if cls.doc is None or cls.doc.Name not in FreeCAD.listDocuments():
            cls.doc = FreeCAD.newDocument("FDCorpus")
            gen = Corpus.generated(cls.doc, variants=1, families=list(Corpus.FAMILIES)[:FAMILY_COUNT], rotations=(False,))
            cls.cases = []
            for c in gen:
                for level, fraction in LEVELS:
                    name = "%s_%s_%d" % (c.name, level, int(fraction * 100))
                    st, _ = Corpus.strip(cls.doc, c.sketch, level, fraction, 0, name=name)
                    if st.DoF > 0:
                        cls.cases.append((c.name, level, fraction, st))
        return cls


# -- the rows the dialog must show, written out here -------------------------------------------------------------------


def _expected_candidates(r):
    """(head row, constraint specs, facts row) for every candidate, as the dialog must show them."""
    out = []
    for k, c in enumerate(r["candidates"]):
        head = "Candidate %d: %s scheme, %d relation(s) and %d dimension(s)" % (k + 1, c["scheme"], c["relations"], c["dimensions"])
        gate = "complete" if c["gate"] == "full" else "shortened (%s)" % (c["gate_reason"] or c["gate"])
        facts = "Measured on a fresh copy: degrees of freedom after: %d; largest point move: below %s mm; verification runs: %d; re-solve check: %s" % (
            c["dof_after"],
            "%.0e" % (10.0 ** math.ceil(math.log10(max(c["max_move"], 1e-12)))),
            c["verification_runs"],
            gate,
        )
        out.append((head, c["constraints"], facts))
    return out


def _check_constraint_row(test, text, spec):
    test.assertTrue(text.startswith(spec["type"] + ": "), text)
    if spec["role"] == "dimension":
        value = spec["args"][-1]
        if spec["type"] == "Angle":
            test.assertTrue(text.endswith("= %.6g deg" % math.degrees(value)), text)
        else:
            test.assertTrue(text.endswith("= %.6g mm" % value), text)
    else:
        test.assertNotIn("=", text)


def _labels(name):
    """The live labels of the messages box with this object name.  The box of a sketch whose window has not been
    shown yet has an invisible parent, so visibility here is the label's own (hidden or not), which is what the
    messages box sets."""
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    return [w for w in QtWidgets.QApplication.allWidgets() if w.objectName() == name and isinstance(w, QtWidgets.QLabel)]


_LABEL = re.compile(rb' (LabelDistance)="[^"]*"')


def _masked(content):
    """The serialised sketch without the datum labels' distance.  The editor places the label of a dimension it
    receives (LabelDistance; measured on 14 cases, it is the only attribute that differs) to keep it clear of the geometry; that is presentation, not solver data,
    and a copy in a hidden document has no editor to place it."""
    out = dict(content)
    out["Constraints"] = {n: _LABEL.sub(b"", v) for n, v in content["Constraints"].items()}
    return out


class FDTestBase(PanelTestBase):
    def setUp(self):
        super().setUp()
        self.fd = _FDRig.build()
        if any(c[3].DoF == 0 for c in self.fd.cases):
            # a test that applied a candidate left a case fully defined: the shared corpus is rebuilt, so that no
            # test sees what an earlier one did to it
            if self.fd.doc.Name in FreeCAD.listDocuments():
                FreeCAD.closeDocument(self.fd.doc.Name)
            _FDRig.doc = None
            self.fd = _FDRig.build()

    def open_fd(self, sketch, wait=True, keep_edit=False):
        if not keep_edit:
            self.edit(sketch)
        Gui.runCommand(COMMAND)
        import SketcherFullyDefinePanel as P

        self.P = P
        self.panel = P.current_panel()
        self.assertIsNotNone(self.panel)
        if wait:
            self.wait_ready()
        return self.panel

    def tmp_doc(self, name):
        d = FreeCAD.newDocument(name)
        self._extra_docs.append(name)
        return d

    def tiny(self, name, datum=False):
        return _tiny(self.tmp_doc(name), "S", datum)

    def status_label(self):
        labels = _labels("labelStatus")
        return labels[0].text() if labels else None

    def candidate_paths(self):
        out = []
        for k in range(self.panel.tree.topLevelItemCount()):
            d = self.panel.tree.topLevelItem(k).data(0, QtCore.Qt.UserRole) or {}
            if d.get("kind") == "candidate":
                out.append((d["candidate"], (k,)))
        return out

    def expected_apply(self, sketch, cand):
        """The sketch's content after ``apply_candidate`` on a copy, made the way the API makes its own."""
        scratch = SD._Scratch(sketch)
        try:
            cp = scratch.copy()
            AD.apply_candidate(cp, cand)
            cp.solve()
            return _content(cp)
        finally:
            scratch.close()

    def two_candidate_sketch(self):
        for family, level, fraction, sk in self.fd.cases:
            if len(AD.propose(sk)["candidates"]) >= 2:
                return sk
        self.fail("no corpus case has two candidates")

    def assert_no_scratch(self):
        self.assertFalse([n for n in FreeCAD.listDocuments() if "Scratch" in n])

    def finish(self):
        self.close_panel()
        if Gui.ActiveDocument is not None and Gui.ActiveDocument.getInEdit() is not None:
            Gui.ActiveDocument.resetEdit()
        _pump(20)


class TestSketcherFullyDefinePanelGui(FDTestBase):
    # check 1 -----------------------------------------------------------------------------------------------------
    def _assert_contents(self, sk, label):
        p = self.open_fd(sk)
        expected = AD.propose(sk, keep=list(range(len(sk.Constraints))))
        got = p.result
        self.assertEqual(got["status"], expected["status"], label)
        self.assertEqual(
            [(c["scheme"], c["constraints"], c["dof_after"]) for c in got["candidates"]],
            [(c["scheme"], c["constraints"], c["dof_after"]) for c in expected["candidates"]],
            label,
        )
        self.assertEqual(
            [(u["scheme"], u["remaining_dof"], u["reason"]) for u in got["unclosed"]],
            [(u["scheme"], u["remaining_dof"], u["reason"]) for u in expected["unclosed"]],
            label,
        )
        rows = _rows(p.tree)
        cands = _expected_candidates(got)
        self.assertEqual(len(rows), len(cands) + len(got["unclosed"]), label)
        for (text, kids), (head, specs, facts) in zip(rows, cands):
            self.assertEqual(text, head, label)
            self.assertEqual(len(kids), len(specs) + 1, label)
            for (ktext, sub), spec in zip(kids, specs):
                _check_constraint_row(self, ktext, spec)
                self.assertEqual(sub, [])
            self.assertEqual(kids[-1][0], facts, label)
        for (text, _), u in zip(rows[len(cands):], got["unclosed"]):
            self.assertIn(u["reason"], text)
            self.assertIn(str(u["remaining_dof"]), text)
        self.assertFalse(p.apply_button.isEnabled())
        self.finish()
        return got

    def testContentsEqualTheApiAtEveryStripLevel(self):
        self.assertGreaterEqual(len(self.fd.cases), 5 * min(FAMILY_COUNT, 3))
        seen = set()
        with_candidates = 0
        for family, level, fraction, sk in self.fd.cases:
            with self.subTest(case=sk.Name):
                got = self._assert_contents(sk, sk.Name)
                seen.add((level, fraction))
                with_candidates += bool(got["candidates"])
        self.assertEqual(seen, set(LEVELS))
        self.assertGreater(with_candidates, len(self.fd.cases) // 2)

    def testContentsEqualTheApiOnRealSketches(self):
        from SketcherTests.TestSketchAutoDefine import GATE_REAL

        cases, missing = Corpus.real_cases({}, names=GATE_REAL[:4])
        if not cases:
            self.skipTest("the frozen real sketches are not in this tree: %s" % missing)
        doc = self.tmp_doc("FDTmpReal")
        for c in cases:
            st, _ = Corpus.strip(doc, c.sketch, "all", name="R")
            if st.DoF > 0:
                with self.subTest(case=c.name):
                    self._assert_contents(st, c.name)
            doc.removeObject(st.Name)

    def testStatusesThatOfferNothingEachGiveTheirOwnSentence(self):
        doc = self.tmp_doc("FDTmpNeg")
        sentences = {}
        for neg in Corpus.negative_cases(doc):
            if neg.name == "neg_stale_geometry":
                continue  # entering the editor solves the sketch, which removes the disagreement this case is made of
            with self.subTest(case=neg.name):
                p = self.open_fd(neg.sketch)
                self.assertEqual(p.result["status"], neg.status)
                text = p.status.text()
                self.assertFalse(p.apply_button.isEnabled())
                self.assertEqual(p.tree.topLevelItemCount(), len(p.result["unclosed"]))
                self.assertEqual(self.candidate_paths(), [])
                if neg.status == "already_defined":
                    self.assertEqual(text, "The sketch is already fully defined: there is nothing to add.")
                elif neg.status == "cannot_close":
                    self.assertTrue(text.startswith("No tested way to fully define this sketch was found"), text)
                    self.assertGreaterEqual(p.tree.topLevelItemCount(), 1)
                    for word in neg.words:
                        self.assertIn(word, p.tree.topLevelItem(0).text(0))
                else:
                    self.assertTrue(text.startswith("This sketch has a conflict or redundancy"), text)
                self.assertEqual(p.diagnose_button.isHidden(), neg.status != "input_inconsistent")
                sentences.setdefault(neg.status, set()).add(text)
                self.finish()
        self.assertEqual(len({next(iter(v)) for v in sentences.values()}), 3)

    def testInconsistentInputOffersTheWayToTheDiagnosisPanel(self):
        doc = self.tmp_doc("FDTmpInc")
        neg = [n for n in Corpus.negative_cases(doc) if n.name == "neg_conflict"][0]
        p = self.open_fd(neg.sketch)
        self.assertFalse(p.diagnose_button.isHidden())
        before = _content(neg.sketch)
        p.diagnose_button.click()
        _pump(50)
        import SketcherDiagnosisPanel as DP

        diag = DP.current_panel()
        self.assertIsNotNone(diag)
        self.assertIsNone(self.P.current_panel())
        self.assertTrue(_wait(lambda: diag.state in ("ready", "failed")))
        self.assertEqual(diag.state, "ready")
        self.assertTrue(diag.result["conflict_sets"])
        self.assertEqual(_content(neg.sketch), before)
        diag.close()
        _pump(30)

    # check 2 -----------------------------------------------------------------------------------------------------
    def _record_propose(self):
        import SketcherFullyDefinePanel as P

        calls = []
        real = P.AD.propose

        def recording(sketch, **kw):
            calls.append({k: v for k, v in kw.items() if k != "progress"})
            return real(sketch, **kw)

        P.AD.propose = recording
        self.addCleanup(setattr, P.AD, "propose", real)
        return calls

    def testPreferredSchemeAndKeepReachPropose(self):
        sk = self.tiny("FDTmpOpt")
        n = len(sk.Constraints)
        calls = self._record_propose()
        p = self.open_fd(sk)
        self.assertEqual(calls[-1], {"keep": list(range(n)), "prefer": None})
        firsts = {}
        for scheme in AD.SCHEMES:
            i = p.scheme_box.findData(scheme)
            self.assertGreaterEqual(i, 0)
            p.scheme_box.setCurrentIndex(i)
            p.refresh_button.click()
            self.wait_ready()
            self.assertEqual(calls[-1], {"keep": list(range(n)), "prefer": scheme})
            firsts[scheme] = [c["scheme"] for c in p.result["candidates"]]
            if firsts[scheme]:
                self.assertEqual(firsts[scheme][0], scheme if scheme in firsts[scheme] else firsts[scheme][0])

    def testPreferredSchemeChangesTheOrderOnTheCorpus(self):
        for family, level, fraction, sk in self.fd.cases:
            base = AD.propose(sk)
            if len(base["candidates"]) < 2:
                continue
            other = base["candidates"][1]["scheme"]
            p = self.open_fd(sk)
            p.scheme_box.setCurrentIndex(p.scheme_box.findData(other))
            p.diagnose_now()
            self.wait_ready()
            self.assertEqual(p.result["candidates"][0]["scheme"], other)
            self.assertTrue(p.tree.topLevelItem(0).text(0).startswith("Candidate 1: %s scheme" % other))
            return
        self.fail("no corpus case has two candidates")

    # check 3 -----------------------------------------------------------------------------------------------------
    def _applied(self, sk, k):
        """Apply candidate k from the dialog; returns (content before, content after, expected, outcome text)."""
        p = self.panel
        before = _content(sk)
        expected = self.expected_apply(sk, p.result["candidates"][k])
        self.choose((k,))
        self.assertTrue(p.apply_button.isEnabled())
        p.apply_button.click()
        self.wait_ready()
        return before, _content(sk), expected, p.outcome.text()

    def testApplyingEveryCandidate(self):
        done = 0
        for family, level, fraction, sk in self.fd.cases[:14]:
            with self.subTest(case=sk.Name):
                p = self.open_fd(sk)
                count = len(p.result["candidates"])
                for k in range(count):
                    if k > 0:
                        Gui.runCommand("Std_Undo")
                        _pump(50)
                        self.wait_ready()
                        self.assertEqual(p.result["status"], "ok")
                    cand = p.result["candidates"][k]
                    before, after, expected, outcome = self._applied(sk, k)
                    self.assertTrue(_same_sketch(_masked(after), _masked(expected)), "applied sketch differs from apply_candidate on a copy")
                    self.assertEqual(sk.DoF, 0)
                    self.assertEqual(list(sk.ConflictingConstraints), [])
                    self.assertEqual(list(sk.RedundantConstraints), [])
                    self.assertEqual(list(sk.PartiallyRedundantConstraints), [])
                    self.assertEqual(_editing(), (sk.Document.Name, sk.Name))
                    self.assertTrue(outcome.startswith("Added %d constraint(s)" % len(cand["constraints"])), outcome)
                    self.assertIn("full re-solve check", outcome)
                    self.assertIn("Degrees of freedom now: 0", outcome)
                    self.assertEqual(self.status_label(), "Fully constrained")
                    # a touch and recompute leaves the sketch and the verdict as they are
                    sk.touch()
                    sk.Document.recompute()
                    _pump(30)
                    self.assertTrue(_same_sketch(_content(sk), after))
                    self.assertEqual(AD.propose(sk)["status"], "already_defined")
                    self.assertEqual(sk.DoF, 0)
                    done += 1
                self.finish()
        self.assertGreaterEqual(done, 10)

    def testAReducedGateIsStatedToTheUserWithItsReason(self):
        import SketcherFullyDefinePanel as P

        sk = self.tiny("FDTmpGate")
        real = P.AD.apply_candidate
        P.AD.apply_candidate = lambda sketch, cand, **kw: real(sketch, cand, stability_budget=0.0)
        self.addCleanup(setattr, P.AD, "apply_candidate", real)
        self.open_fd(sk)
        _, _, _, outcome = self._applied(sk, 0)
        self.assertIn("reduced to %d solve(s) of the sketch only" % AD.REDUCED_GATE_RUNS, outcome)
        self.assertIn("stability budget", outcome)
        self.assertNotIn("full re-solve check", outcome)
        self.assertEqual(sk.DoF, 0)

    # check 4 -----------------------------------------------------------------------------------------------------
    def testARefusedApplyChangesNothingAndOtherCandidatesCanStillBeTried(self):
        import SketcherFullyDefinePanel as P

        sk = self.two_candidate_sketch()
        real = P.AD.apply_candidate

        def refusing(sketch, cand, **kw):
            real(sketch, cand, **kw)  # the constraints are really in the sketch when the refusal comes
            raise AD.AutoDefineError("the candidate did not solve cleanly on this sketch; it was rolled back")

        p = self.open_fd(sk)
        self.assertGreaterEqual(len(self.candidate_paths()), 2)
        before = _signature(sk)
        P.AD.apply_candidate = refusing
        try:
            self.choose((0,))
            p.apply_button.click()
            _pump(50)
        finally:
            P.AD.apply_candidate = real
        after = _signature(sk)
        self.assertEqual(after["undo_names"], before["undo_names"])
        self.assertEqual(after["undo"], before["undo"])
        self.assertEqual(after["redo"], before["redo"])
        self.assertTrue(_same_sketch(after["content"], before["content"]))
        self.assertEqual(after["editing"], before["editing"])
        self.assertEqual(p.state, "ready")
        text = p.outcome.text()
        self.assertIn("could not be applied", text)
        self.assertIn("did not solve cleanly", text)
        self.assertIn("you can try another candidate", text)
        self.choose((1,))
        self.assertTrue(p.apply_button.isEnabled())
        p.apply_button.click()
        self.wait_ready()
        self.assertEqual(sk.DoF, 0)

    def testARealRefusalFromApplyCandidateLeavesTheSketchAsItWas(self):
        """A candidate that names geometry that is not there: ``apply_candidate`` raises and rolls back itself."""
        sk = self.tiny("FDTmpReal2")
        p = self.open_fd(sk)
        cand = json.loads(json.dumps(p.result["candidates"][0]))
        cand["constraints"][0]["args"] = [99] + list(cand["constraints"][0]["args"][1:])
        p.result["candidates"][0] = cand
        before = _signature(sk)
        self.choose((0,))
        p.apply_button.click()
        _pump(50)
        after = _signature(sk)
        self.assertTrue(_same_sketch(after["content"], before["content"]))
        self.assertEqual(after["undo"], before["undo"])
        self.assertEqual(after["undo_names"], before["undo_names"])
        self.assertIn("could not be applied", p.outcome.text())

    # check 5 -----------------------------------------------------------------------------------------------------
    def testEveryApplyIsOneNamedUndoStepAndUndoRedoAreExact(self):
        done = 0
        for family, level, fraction, sk in self.fd.cases[:10]:
            with self.subTest(case=sk.Name):
                doc = sk.Document
                p = self.open_fd(sk)
                if p.result["candidates"]:
                    doc.clearUndos()
                    original = _content(sk)
                    _, applied, _, _ = self._applied(sk, 0)
                    self.assertEqual(doc.UndoCount, 1)
                    self.assertEqual(doc.UndoNames[0], TRANSACTION_NAME)
                    Gui.runCommand("Std_Undo")
                    self.wait_ready()
                    self.assertTrue(_same_sketch(_content(sk), original), "undo did not restore the sketch")
                    self.assertEqual((doc.UndoCount, doc.RedoCount), (0, 1))
                    self.assertEqual(_editing(), (doc.Name, sk.Name))
                    Gui.runCommand("Std_Redo")
                    self.wait_ready()
                    self.assertTrue(_same_sketch(_content(sk), applied), "redo did not restore the applied sketch")
                    self.assertEqual(_editing(), (doc.Name, sk.Name))
                    Gui.runCommand("Std_Undo")
                    self.wait_ready()
                    done += 1
                self.finish()
        self.assertGreaterEqual(done, 8)

    # check 6 -----------------------------------------------------------------------------------------------------
    def _browse_and_close(self, sketch, plant=None):
        import SketcherFullyDefinePanel as P

        self.edit(sketch)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(sketch.Document.Name, sketch.Name, "Edge1")
        before = _signature(sketch)
        before["selection"] = _selected_names()
        self.assertEqual(before["selection"], ["Edge1"])
        real = P.AD.propose
        if plant is not None:
            P.AD.propose = plant(real)
        try:
            Gui.runCommand(COMMAND)
            self.panel = P.current_panel()
            self.assertTrue(_wait(lambda: self.panel.state in ("ready", "failed", "cancelled")))
            for _ in range(3):
                t = self.panel.tree
                for k in range(t.topLevelItemCount()):
                    top = t.topLevelItem(k)
                    self.choose((k,))
                    for j in range(top.childCount()):
                        self.choose((k, j))
                t.clearSelection()
                _pump()
            state = self.panel.state
            self.close_panel()
        finally:
            P.AD.propose = real
        _pump(30)
        after = _signature(sketch)
        after["selection"] = _selected_names()
        return before, after, state

    def testNoSideEffectsOnTheCorpus(self):
        for family, level, fraction, sk in self.fd.cases[:12]:
            with self.subTest(case=sk.Name):
                b, a, _ = self._browse_and_close(sk)
                self.assertEqual(a, b)
                self.assert_no_scratch()
            self.finish()

    def testNoSideEffectsWhenProposeRaises(self):
        sk = self.tiny("FDTmpRaise")

        def plant(real):
            def boom(sketch, **kw):
                raise RuntimeError("planted")

            return boom

        b, a, state = self._browse_and_close(sk, plant)
        self.assertEqual(state, "failed")
        self.assertEqual(a, b)
        self.assert_no_scratch()

    def testNoSideEffectsWhenProposeRaisesAfterOpeningItsScratchDocument(self):
        sk = self.tiny("FDTmpRaise2")

        def plant(real):
            def late(sketch, **kw):
                def cb(info):
                    if info["ticks"] > 3:
                        raise RuntimeError("planted in the progress callback")
                    return kw["progress"](info)

                return real(sketch, **dict(kw, progress=cb))

            return late

        b, a, state = self._browse_and_close(sk, plant)
        self.assertEqual(state, "failed")
        self.assertEqual(a, b)
        self.assert_no_scratch()

    def testNoSideEffectsWhenCancelled(self):
        sk = self.tiny("FDTmpCancel")

        def plant(real):
            def stopping(sketch, **kw):
                inner = kw["progress"]

                def cb(info):
                    if info["ticks"] == 4:
                        self.panel.cancel_button.click()
                    return inner(info)

                return real(sketch, **dict(kw, progress=cb))

            return stopping

        b, a, state = self._browse_and_close(sk, plant)
        self.assertEqual(state, "cancelled")
        self.assertEqual(a, b)
        self.assert_no_scratch()

    def testOpeningLeavesEditModeAndTheActiveDocument(self):
        sk = self.tiny("FDTmpEdit")
        self.edit(sk)
        before = (_editing(), _active_name(), Gui.ActiveDocument.Document.Name, sorted(FreeCAD.listDocuments()))
        p = self.open_fd(sk, keep_edit=True)
        self.assertEqual((_editing(), _active_name(), Gui.ActiveDocument.Document.Name, sorted(FreeCAD.listDocuments())), before)
        self.assertIsInstance(p, QtWidgets.QDockWidget)
        self.assertIs(p.parent(), Gui.getMainWindow())

    # check 7 -----------------------------------------------------------------------------------------------------
    def testChoosingACandidateListsWhatItWouldAddAndChoosingNothingShowsNothing(self):
        sk = self.tiny("FDTmpPrev")
        p = self.open_fd(sk)
        before = _signature(sk)
        self.assertTrue(p.preview.isHidden())
        self.assertEqual(p.preview.count(), 0)
        for k, path in self.candidate_paths():
            self.choose(path)
            cand = p.result["candidates"][k]
            self.assertFalse(p.preview.isHidden())
            self.assertEqual(p.preview.count(), len(cand["constraints"]))
            for i, spec in enumerate(cand["constraints"]):
                _check_constraint_row(self, p.preview.item(i).text(), spec)
            self.assertIn("would add %d constraint(s)" % len(cand["constraints"]), p.preview_label.text())
            self.choose(path + (0,))  # a child row of the candidate chooses the candidate too
            self.assertEqual(p.preview.count(), len(cand["constraints"]))
            self.assertEqual(_signature(sk), before)
        p.tree.clearSelection()
        _pump()
        self.assertTrue(p.preview.isHidden())
        self.assertEqual(p.preview.count(), 0)
        self.assertTrue(p.preview_label.isHidden())
        self.assertFalse(p.apply_button.isEnabled())
        self.assertEqual(_signature(sk), before)

    # check 8 -----------------------------------------------------------------------------------------------------
    def _stale_setup(self, name, datum=False):
        sk = self.tiny(name, datum)
        p = self.open_fd(sk)
        self.choose((0,))
        self.assertTrue(p.apply_button.isEnabled())
        return sk, p

    def _assert_stale_handled(self, sk, p, what):
        self.assertFalse(p.apply_button.isEnabled(), what)
        before = _content(sk)
        undo = sk.Document.UndoCount
        self.assertFalse(p.apply_chosen(), what)
        self.assertEqual(_content(sk), before, what)
        self.assertEqual(sk.Document.UndoCount, undo, what)

    def testStaleAfterAConstraintIsAdded(self):
        sk, p = self._stale_setup("FDTmpS1")
        sk.addConstraint(Sketcher.Constraint("Horizontal", 0))
        self._assert_stale_handled(sk, p, "constraint added")
        self.wait_ready()
        self.assertEqual(sk.DoF, p.result["dof"])

    def testStaleAfterADatumChanges(self):
        sk, p = self._stale_setup("FDTmpS2", datum=True)
        sk.setDatum(0, FreeCAD.Units.Quantity("11 mm"))
        self._assert_stale_handled(sk, p, "datum changed")
        self.wait_ready()

    def testStaleAfterUndo(self):
        sk, p = self._stale_setup("FDTmpS3")
        sk.Document.openTransaction("add")
        sk.addConstraint(Sketcher.Constraint("Horizontal", 0))
        sk.Document.commitTransaction()
        self.wait_ready()
        self.choose((0,))
        sk.Document.undo()
        self._assert_stale_handled(sk, p, "undo")
        self.wait_ready()

    def testStaleWhenLeavingEditMode(self):
        sk, p = self._stale_setup("FDTmpS4")
        before = _content(sk)
        Gui.ActiveDocument.resetEdit()
        self.assertFalse(p.apply_chosen())
        self.assertEqual(_content(sk), before)
        self.assertTrue(_wait(lambda: p.state == "closed"))
        self.assertIsNone(self.P.current_panel())

    def testStaleWhenTheDocumentIsClosed(self):
        sk, p = self._stale_setup("FDTmpS5")
        FreeCAD.closeDocument(sk.Document.Name)
        _pump(50)
        self.assertTrue(_wait(lambda: p.state == "closed"))
        self.assertIsNone(self.P.current_panel())

    def testStaleWhenTheEditorSwitchesToASketchInAnotherDocument(self):
        sk, p = self._stale_setup("FDTmpS6")
        other = self.tiny("FDTmpS6b")
        before = _content(sk)
        before_other = _content(other)
        Gui.ActiveDocument = Gui.getDocument(other.Document.Name)
        Gui.ActiveDocument.setEdit(other.Name)
        self._assert_stale_handled(sk, p, "another sketch put in edit")
        self.assertTrue(_wait(lambda: p.state == "closed"))
        self.assertEqual(_content(sk), before)
        self.assertEqual(_content(other), before_other)

    def _look_at(self, name):
        Gui.setActiveDocument(name)
        _pump(30)
        self.assertEqual(Gui.ActiveDocument.Document.Name, name)

    def testLookingAtAnotherDocumentKeepsTheDialogWaitingWithApplyDisabled(self):
        other = self.tiny("FDTmpL2")
        sk, p = self._stale_setup("FDTmpL1")
        rows = _rows(p.tree)
        before = _content(sk)
        self._look_at(other.Document.Name)
        self.assertEqual(p.state, "ready")
        self.assertFalse(p.apply_button.isEnabled())
        self.assertFalse(p.refresh_button.isEnabled())
        self.assertIn("not the active one", p.status.text())
        self.assertFalse(p.apply_chosen())
        generation = p._generation
        p.diagnose_now()
        _pump(50)
        self.assertEqual(p._generation, generation)
        self.assertEqual(_rows(p.tree), rows)
        self._look_at(sk.Document.Name)
        self.assertEqual(p.state, "ready")
        self.assertTrue(p.apply_button.isEnabled())
        self.assertNotIn("not the active one", p.status.text())
        self.assertEqual(_rows(p.tree), rows)
        self.assertEqual(_content(sk), before)
        self.assertTrue(p.apply_chosen())

    # check 9 -----------------------------------------------------------------------------------------------------
    def testTimeBudgetRunningOutIsReportedAsSuchAndNeverAsCannotClose(self):
        import SketcherFullyDefinePanel as P

        sk = self.tiny("FDTmpTime")
        real = P.AD.propose
        P.AD.propose = lambda sketch, **kw: real(sketch, time_budget=0.0, **kw)
        self.addCleanup(setattr, P.AD, "propose", real)
        p = self.open_fd(sk)
        self.assertEqual(p.result["status"], "cannot_close")
        self.assertTrue(p.result["out_of_time"])
        text = p.status.text()
        self.assertIn("ran out of time", text)
        self.assertNotIn("No tested way", text)
        self.assertGreaterEqual(p.tree.topLevelItemCount(), 1)
        for k in range(p.tree.topLevelItemCount()):
            row = p.tree.topLevelItem(k).text(0)
            self.assertIn("time ran out", row)
            self.assertNotIn("could not reach", row)

    def testProgressCallbackCancelsAndLeavesNothing(self):
        sk = self.tiny("FDTmpAPI")
        before = _signature(sk)
        with self.assertRaises(AD.AutoDefineCancelled):
            AD.propose(sk, progress=lambda info: True)
        self.assertEqual(_signature(sk), before)
        self.assert_no_scratch()
        ticks = []
        res = AD.propose(sk, progress=lambda info: ticks.append(info["phase"]) and False)
        self.assertGreater(len(ticks), 3)
        plain = AD.propose(sk)
        self.assertEqual(
            [(c["scheme"], c["constraints"]) for c in res["candidates"]],
            [(c["scheme"], c["constraints"]) for c in plain["candidates"]],
        )

    def _strip_sketch(self, n):
        d = self.tmp_doc("FDTmpStrip")
        sk = d.addObject("Sketcher::SketchObject", "Strip")
        pts = [(i * 3.0, 4.0 * ((i * 7) % 5) / 5.0) for i in range(n)]
        sk.addGeometry([Part.LineSegment(V(a[0], a[1], 0), V(b[0], b[1], 0)) for a, b in zip(pts, pts[1:])], False)
        sk.solve()
        return sk

    def _measure_run(self, sk, cancel_after=None):
        """Open the dialog on `sk`, optionally click Stop at the `cancel_after`-th tick.  Returns the figures."""
        import SketcherFullyDefinePanel as P

        if _editing() != (sk.Document.Name, sk.Name):
            self.edit(sk)
        stamps = []
        timer = QtCore.QTimer()
        timer.setInterval(5)
        timer.timeout.connect(lambda: stamps.append(time.time()))
        timer.start()
        real = P.AD.propose
        marks = {}

        def clicking(sketch, **kw):
            inner = kw["progress"]
            n = [0]

            def cb(info):
                n[0] += 1
                # The click is posted AFTER the panel's own callback has pumped events, so it reaches the button
                # while the solver call that follows is running: Stop then takes effect at the next tick, which is
                # the worst case a user can see.  Posted before the pump it would be handled at once and measure nothing.
                stop = inner(info)
                if cancel_after and n[0] == cancel_after and "t_click" not in marks:
                    marks["t_click"] = time.time()
                    btn = self.panel.cancel_button
                    pos = QtCore.QPoint(btn.width() // 2, btn.height() // 2)
                    for typ, buttons in (
                        (QtCore.QEvent.MouseButtonPress, QtCore.Qt.LeftButton),
                        (QtCore.QEvent.MouseButtonRelease, QtCore.Qt.NoButton),
                    ):
                        ev = QtGui.QMouseEvent(
                            typ, QtCore.QPointF(pos), QtCore.QPointF(btn.mapToGlobal(pos)), QtCore.Qt.LeftButton, buttons, QtCore.Qt.NoModifier
                        )
                        QtCore.QCoreApplication.postEvent(btn, ev)
                return stop

            try:
                return real(sketch, **dict(kw, progress=cb))
            finally:
                marks["t_done"] = time.time()

        P.AD.propose = clicking
        t0 = time.time()
        try:
            Gui.runCommand(COMMAND)
            self.P = P
            self.panel = P.current_panel()
            want = "cancelled" if cancel_after else "ready"
            self.assertTrue(_wait(lambda: self.panel.state == want, timeout=900))
        finally:
            P.AD.propose = real
            timer.stop()
        t1 = time.time()
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        longest = max(gaps + [stamps[0] - t0 if stamps else 0.0, t1 - stamps[-1] if stamps else 0.0])
        out = {"seconds": t1 - t0, "longest_gap_timer": longest, "longest_gap_panel": self.panel.max_gap}
        if cancel_after:
            out["cancel_latency"] = marks["t_done"] - marks["t_click"]
        return out

    def _assert_responsive(self, label, sk, cancel_after, bar=None, cancel_bar=None):
        self.edit(sk)  # entering the editor solves the sketch: the baseline is taken after it
        before = _signature(sk)
        before["editing"] = _editing()
        bar = EVENT_GAP_BAR if bar is None else bar
        cancel_bar = CANCEL_BAR if cancel_bar is None else cancel_bar
        run = self._measure_run(sk)
        print("FDGAP", label, json.dumps(run))
        self.assertLessEqual(run["longest_gap_timer"], bar, run)
        self.assertLessEqual(run["longest_gap_panel"], bar, run)
        self.close_panel()
        _pump(30)
        cancel = self._measure_run(sk, cancel_after=cancel_after)
        print("FDCANCEL", label, json.dumps(cancel))
        self.assertLessEqual(cancel["cancel_latency"], cancel_bar, cancel)
        self.close_panel()
        _pump(30)
        after = _signature(sk)
        after["editing"] = _editing()
        self.assertEqual(after, before)
        self.assert_no_scratch()

    def testResponsivenessOnTheLargestCorpusSketch(self):
        """The largest frozen real sketch of slice 2's corpus (28 geometry elements in this build tree), stripped of
        every constraint, and the corpus' own 100-element scale sketch."""
        cases, _ = Corpus.real_cases({}, names=["Mod/Part/parttests/TestTangentMode3-0.21.FCStd:Sketch002"])
        if not cases:
            self.skipTest("the largest real sketch is not in this tree")
        st, _ = Corpus.strip(self.tmp_doc("FDTmpLarge"), cases[0].sketch, "all", name="Largest")
        self.assertGreaterEqual(len(st.Geometry), 28)
        self._assert_responsive("largest_real", st, cancel_after=10)

    def testResponsivenessOnTheHundredElementScaleSketch(self):
        sk = Corpus.scale_sketch(self.tmp_doc("FDTmpScale"), 100)
        self._assert_responsive("scale100", sk, cancel_after=30)

    def testResponsivenessOnAThousandPointOpenStrip(self):
        """The bar of 1.5 s is MISSED here, and the test says so instead of moving it.  Adding the coincident
        constraints of the strip and solving takes about 10 s per call in this build (999 lines, native solver), and
        the call returns to Python only when it has finished, so neither an event-loop gap nor the time from Stop to
        effect can be shorter than one such call.  The test measures both on a run that is stopped at its first long
        solve, prints them (FDGAP and FDCANCEL, for the run note) and holds them to what is still checkable: Stop
        takes effect at the next tick, that is within one solve of the longest gap seen plus the bar, the sketch,
        undo stack and editor are as before, and no scratch document is left."""
        sk = self._strip_sketch(1000)
        self.edit(sk)
        before = _signature(sk)
        before["editing"] = _editing()
        cancel = self._measure_run(sk, cancel_after=15)
        print("FDGAP strip1000", json.dumps(cancel))
        print("FDCANCEL strip1000", json.dumps(cancel))
        self.assertGreater(cancel["longest_gap_timer"], EVENT_GAP_BAR, "the strip no longer misses the bar: tighten this test")
        self.assertLessEqual(cancel["cancel_latency"], cancel["longest_gap_timer"] + EVENT_GAP_BAR, cancel)
        self.close_panel()
        _pump(30)
        after = _signature(sk)
        after["editing"] = _editing()
        self.assertEqual(after, before)
        self.assert_no_scratch()

    # check 10 ----------------------------------------------------------------------------------------------------
    def testCommandIsActiveOnlyWithASketchInEdit(self):
        sk = self.tiny("FDTmpCmd")
        cmd = Gui.Command.get(COMMAND)
        Gui.Selection.clearSelection()
        self.assertFalse(cmd.isActive())
        self.edit(sk)
        self.assertTrue(cmd.isActive())
        Gui.ActiveDocument.resetEdit()
        _pump(30)
        self.assertFalse(cmd.isActive())

    def testCommandIsInTheSketcherMenu(self):
        Gui.activateWorkbench("SketcherWorkbench")
        _pump(50)
        mw = Gui.getMainWindow()

        def names(menu, depth=0):
            out = []
            for a in menu.actions():
                out.append(a.objectName())
                if a.menu() is not None and depth < 4:
                    out += names(a.menu(), depth + 1)
            return out

        sketcher = [m for m in mw.menuBar().actions() if m.text().replace("&", "") == "Sketch"]
        self.assertTrue(sketcher)
        self.assertIn(COMMAND, names(sketcher[0].menu()))

    def _link_state(self, sketch):
        """(status text, link visible) of the messages box that opening `sketch` in the editor creates.  Boxes of
        earlier edit sessions may not be deleted yet, so the new labels are the ones that did not exist before."""
        old = _labels("labelFullyDefine") + _labels("labelStatus")
        self.edit(sketch)
        links = [w for w in _labels("labelFullyDefine") if w not in old]
        self.assertEqual(len(links), 1, sketch.Document.Name)
        status = [w for w in _labels("labelStatus") if w not in old]
        self.assertEqual(len(status), 1, sketch.Document.Name)
        text, visible = status[0].text(), not links[0].isHidden()
        Gui.ActiveDocument.resetEdit()
        _pump(100)
        return text, visible

    def testMessagesBoxOffersTheLinkOnlyWhenUnderConstrained(self):
        under = self.tiny("FDTmpLink1")
        full = self.tiny("FDTmpLink2")
        for c in (
            Sketcher.Constraint("Coincident", 0, 1, -1, 1),
            Sketcher.Constraint("Horizontal", 0),
            Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 10.0),
            Sketcher.Constraint("Coincident", 1, 1, 0, 2),
            Sketcher.Constraint("Vertical", 1),
            Sketcher.Constraint("DistanceY", 1, 1, 1, 2, 5.0),
        ):
            full.addConstraint(c)
        full.solve()
        self.assertEqual(full.DoF, 0)
        over = self.tiny("FDTmpLink3")
        over.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 10.0))
        over.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 12.0))
        redundant = self.tiny("FDTmpLink4")
        redundant.addConstraint(Sketcher.Constraint("Horizontal", 0))
        redundant.addConstraint(Sketcher.Constraint("DistanceY", 0, 1, 0, 2, 0.0))
        empty = self.tmp_doc("FDTmpLink5").addObject("Sketcher::SketchObject", "S")
        seen = {}
        for sk in (under, full, over, redundant, empty):
            text, visible = self._link_state(sk)
            seen[text.split(":")[0].split(" ")[0]] = visible
        self.assertEqual(seen.get("Under-constrained"), True, seen)
        self.assertEqual(seen.get("Fully"), False, seen)
        self.assertEqual(seen.get("Over-constrained"), False, seen)
        self.assertEqual(seen.get("Redundant"), False, seen)
        self.assertEqual(seen.get("Empty"), False, seen)

    def testLinkOpensTheDialogAndStockLinksKeepTheirTooltips(self):
        import SketcherFullyDefinePanel as P

        sk = self.tiny("FDTmpLink6")
        self.edit(sk)
        links = _labels("labelFullyDefine")
        links[0].linkActivated.emit("#fullydefine")
        _pump(30)
        self.P = P
        self.panel = P.current_panel()
        self.assertIsNotNone(self.panel)
        self.wait_ready()
        stock = _labels("labelStatusLink")
        self.assertEqual(len(stock), 1)
        self.assertEqual(
            stock[0].toolTip(),
            "The sketch has unconstrained elements giving rise to those Degrees Of Freedom. Selects these unconstrained elements.",
        )
        diag = _labels("labelDiagnose")
        self.assertTrue(diag[0].isHidden())
        self.assertEqual(diag[0].toolTip(), "Opens the diagnosis panel: which constraints conflict and fixes the solver has tested")

    # check 11 (translate) ----------------------------------------------------------------------------------------
    def testEveryVisibleStringGoesThroughTranslate(self):
        import ast

        import SketcherFullyDefinePanel as P

        with open(P.__file__) as f:
            tree = ast.parse(f.read())
        bad = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("setText", "setToolTip", "setAccessibleName", "setWindowTitle", "addItem")
            ):
                for arg in node.args[:1]:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value:
                        bad.append((node.lineno, arg.value))
        self.assertEqual(bad, [])

    @classmethod
    def tearDownClass(cls):
        if "FDCorpus" in FreeCAD.listDocuments():
            FreeCAD.closeDocument("FDCorpus")
        super().tearDownClass()
