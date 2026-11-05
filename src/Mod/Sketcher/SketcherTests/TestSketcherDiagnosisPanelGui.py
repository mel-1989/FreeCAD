# SPDX-License-Identifier: LGPL-2.1-or-later
"""G10 slice 4a: the diagnosis panel of the sketch editor, driven through its real widgets.

Run with the GUI binary, with the sketch in edit mode: ``FreeCAD -t TestSketcherDiagnosisPanelGui``.  The checks are
the numbered acceptance checks of slice 4a in briefs/phase1/G10-sketcher.md (1 to 8 here; 9 is the GUI gate, 10 the
mutant script SketchDiagnosisPanelMutants.py).  Sketches come from slice 1's synthetic corpus; the expected values are
computed here from ``SketcherDiagnosis`` directly, on the sketch the panel was not run on.
"""

import io
import json
import os
import re
import time
import unittest
import zipfile

import FreeCAD
import FreeCADGui as Gui
import Part
import Sketcher
from PySide import QtCore, QtGui, QtWidgets

import SketcherDiagnosis as SD
from SketcherTests import SketchDiagnosisCorpus as Corpus
from SketcherTests.GuiTestCase import SketcherGuiTestCase

COMMAND = "Sketcher_DiagnoseSketch"
# Check 7 bars: 1.5 s, moved from 1.0 s by the orchestrator (see the brief): one native solve of the 1000-constraint
# sketch takes 1.2 s and the panel cannot hand control back from inside it, so 1.0 s could never hold.
EVENT_GAP_BAR = 1.5  # seconds, check 7
CANCEL_BAR = 1.5  # seconds, check 7
TRANSACTION_NAME = "Apply sketch fix"
V = FreeCAD.Vector


def _active_name():
    return FreeCAD.ActiveDocument.Name if FreeCAD.ActiveDocument is not None else None


def _content(sketch):
    """The sketch's own serialised data (the properties that define it); the zip wrapper carries timestamps."""
    out = {}
    for prop in ("Geometry", "ExternalGeo", "Constraints"):
        z = zipfile.ZipFile(io.BytesIO(bytes(sketch.dumpPropertyContent(prop))))  # the wrapper carries timestamps
        out[prop] = {n: z.read(n) for n in z.namelist()}
    out["Expressions"] = [(str(p), str(e)) for p, e in sketch.ExpressionEngine]
    out["Placement"] = str(sketch.Placement)
    return out


def _editing():
    gdoc = Gui.ActiveDocument
    vp = gdoc.getInEdit() if gdoc is not None else None
    return (vp.Object.Document.Name, vp.Object.Name) if vp is not None else None


def _signature(sketch):
    doc = sketch.Document
    return {
        "content": _content(sketch),
        "undo": doc.UndoCount,
        "redo": doc.RedoCount,
        "undo_names": list(doc.UndoNames),
        "states": [(o.Name, tuple(o.State)) for o in doc.Objects],
        "documents": sorted(FreeCAD.listDocuments().keys()),
        "active": _active_name(),
        "gui_active": Gui.ActiveDocument.Document.Name if Gui.ActiveDocument is not None else None,
        "editing": _editing(),
    }


def _pump(ms=0):
    QtWidgets.QApplication.processEvents()
    Gui.updateGui()
    if ms:
        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(ms, loop.quit)
        loop.exec_()


def _wait(predicate, timeout=30.0, step_ms=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if predicate():
            return True
        _pump(step_ms)
    return predicate()


def _find(name, cls=QtWidgets.QWidget):
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)  # widgets of an earlier edit session
    found = [w for w in QtWidgets.QApplication.allWidgets() if w.objectName() == name and isinstance(w, cls)]
    live = [w for w in found if w.parentWidget() is None or w.parentWidget().isVisible()]
    return live


def _rows(tree):
    """The tree as nested (text, [children]) read back from the widgets."""

    def walk(item):
        return (item.text(0), [walk(item.child(i)) for i in range(item.childCount())])

    return [walk(tree.topLevelItem(i)) for i in range(tree.topLevelItemCount())]


def _selected_names():
    out = []
    for s in Gui.Selection.getSelectionEx():
        out += list(s.SubElementNames)
    return sorted(out)


def _stable(result):
    r = json.loads(json.dumps(result))
    for k in ("seconds", "solves", "copies"):
        r.pop(k, None)
    return r


def _old_index(i, ops):
    """The index a constraint had before the fix's deletions (`i` is its index after them)."""
    for d in sorted(o["index"] for o in ops if o["op"] == "delete"):
        if i >= d:
            i += 1
    return i


def _sub(c):
    return "Constraint%d" % (c + 1)


_NUM = re.compile(r"-?\d+\.\d+(?:[eE][-+]?\d+)?")


def _skeleton(text):
    text = text.replace(' refIndex="0"', "").replace(' RefIndex="0"', "")  # external geometry's bookkeeping attribute
    return _NUM.sub("#", text), [float(x) for x in _NUM.findall(text)]


def _same_sketch(a, b, tol=1e-9):
    """Equal serialised content, coordinates within `tol` (the editor and a copy reach the same solution by
    different solver histories, so the last bits of a coordinate can differ)."""
    if a.keys() != b.keys():
        return False
    for k in a:
        if isinstance(a[k], dict):
            if a[k].keys() != b[k].keys():
                return False
            for n in a[k]:
                sa, na = _skeleton(a[k][n].decode())
                sb, nb = _skeleton(b[k][n].decode())
                if sa != sb or len(na) != len(nb) or any(abs(x - y) > tol for x, y in zip(na, nb)):
                    return False
        elif a[k] != b[k]:
            return False
    return True


class _Rig:
    """One shared GUI document with the synthetic corpus, built once."""

    doc = None
    cases = None

    @classmethod
    def build(cls):
        if cls.doc is None or cls.doc.Name not in FreeCAD.listDocuments():
            cls.doc = FreeCAD.newDocument("PanelCorpus")
            cls.cases = Corpus.build_synthetic(cls.doc)
        return cls


# the reasons in the user's words, written out here and not taken from the panel
_REASON_WORDS = {
    "recompute_changes_result": "a recompute would undo it",
    "result_not_reproduced": "applied for real it does not give the result that was tested",
    "read_by_another_object": "another object's expression reads the constraint",
    "bound_outside_sketch": "its value is bound to an expression outside the sketch",
    "leaves_new_conflict": "it would leave a new conflict",
    "leaves_new_redundancy": "it would leave redundancy that was not there",
    "changes_dof": "it would change the degrees of freedom",
    "does_not_shrink": "it would not reduce the redundancy",
    "no_safe_member": "no member of the group can be removed without changing the sketch",
    "solver_rejected": "the solver rejects the result",
    "out_of_time": "the time budget ran out",
}


def _expected_not_offered_line(r):
    """The line the panel must add under its summary for the fixes the API dropped, or None."""
    if not r["not_offered"]:
        return None
    reasons = []
    for n in r["not_offered"]:
        if _REASON_WORDS[n["reason"]] not in reasons:
            reasons.append(_REASON_WORDS[n["reason"]])
    return "%d fix(es) not offered: %s." % (len(r["not_offered"]), "; ".join(reasons))


def _expected_rows(sketch, r):
    """The panel's tree as the API's result says it must read (rows written out here, not taken from the panel)."""

    def con(i):
        c = sketch.Constraints[i]
        text = "%d: %s" % (i + 1, c.Type)
        if c.Name:
            text += " '%s'" % c.Name
        if c.Type in SD._DATUM_TYPES:
            text += " = %.6g %s" % (SD._value_in_unit(c.Value, c.Type), "deg" if c.Type == "Angle" else "mm")
        return text

    def fix(f):
        parts = []
        for o in f["ops"]:
            c = sketch.Constraints[o["index"]]
            label = c.Type + (" '%s'" % c.Name if c.Name else "") + " [%d]" % (o["index"] + 1)
            if o["op"] == "delete":
                parts.append("Delete " + label)
            elif o["op"] == "set_driving":
                parts.append("Make " + label + " non-driving (reference)")
            else:
                parts.append("Change %s to %.6g %s" % (label, o["value"], o.get("unit", "")))
        if f["dof_after"] is None:
            return "%s (the conflict remains)" % "; ".join(parts)
        return "%s (degrees of freedom after: %d)" % ("; ".join(parts), f["dof_after"])

    rows = []
    for k, cs in enumerate(r["conflict_sets"]):
        kids = [(con(m), []) for m in cs["members"]] + [(fix(f), []) for f in cs["fixes"]]
        rows.append(("Conflict %d: constraints %s" % (k + 1, ", ".join(str(m + 1) for m in cs["members"])), kids))
    for k, g in enumerate(r["redundancy_groups"]):
        kids = [(con(m), []) for m in g["members"]]
        removal = "Suggested removal: constraint " + con(g["suggested_removal"])
        if g.get("fix") is None and g.get("no_fix_reason"):
            removal += " (cannot be applied: %s)" % _REASON_WORDS[g["no_fix_reason"]]
        elif g.get("fix") and g["fix"]["remaining_conflicts"]:
            removal += " (the conflict remains)"
        kids.append((removal, []))
        rows.append(("Redundant group %d: constraints %s" % (k + 1, ", ".join(str(m + 1) for m in g["members"])), kids))
    return rows


class PanelTestBase(SketcherGuiTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Gui.activateWorkbench("SketcherWorkbench")  # the editor is entered from it; stock commands need it loaded
        _pump(50)
        _Rig.build()

    def setUp(self):
        super().setUp()
        self.rig = _Rig.build()
        self.doc = None  # the shared document outlives a test
        self.panel = None
        self._extra_docs = []

    def tearDown(self):
        try:
            self.close_panel()
            gdoc = Gui.ActiveDocument
            if gdoc is not None and gdoc.getInEdit() is not None:
                gdoc.resetEdit()
            _pump(30)
            Gui.Selection.clearSelection()
            for name in self._extra_docs:
                if name in FreeCAD.listDocuments():
                    FreeCAD.closeDocument(name)
            if self.rig.doc.Name in FreeCAD.listDocuments():
                FreeCAD.setActiveDocument(self.rig.doc.Name)
        finally:
            super().tearDown()

    # -- helpers -------------------------------------------------------------
    def case(self, name):
        return [c for c in self.rig.cases if c.name == name][0]

    def faulty_cases(self):
        return [c for c in self.rig.cases if c.truth.get("conflicting") or c.truth.get("redundant")]

    def edit(self, sketch):
        Gui.ActiveDocument = Gui.getDocument(sketch.Document.Name)
        Gui.ActiveDocument.setEdit(sketch.Name)
        _pump(30)
        self.assertEqual(_editing(), (sketch.Document.Name, sketch.Name))

    def open_panel(self, sketch, wait=True):
        self.edit(sketch)
        Gui.runCommand(COMMAND)
        import SketcherDiagnosisPanel as P

        self.P = P
        self.panel = P.current_panel()
        self.assertIsNotNone(self.panel)
        if wait:
            self.wait_ready()
        return self.panel

    def wait_ready(self, panel=None):
        """Wait until the panel shows a diagnosis of the sketch as it is now."""
        panel = panel or self.panel
        for _ in range(20):
            _wait(lambda: panel.state in ("ready", "failed", "closed"))
            self.assertEqual(panel.state, "ready", panel.status.text())
            if panel._fingerprint() == panel.fingerprint:
                return
            _pump(100)
        self.fail("the sketch keeps changing under the panel")

    def close_panel(self):
        p = getattr(self, "panel", None)
        if p is not None:
            try:
                p.close()
            except RuntimeError:
                pass
            _pump(30)
        self.panel = None

    def tree(self):
        return self.panel.tree

    def choose(self, row_path):
        """Choose a row of the tree by index path, like a click."""
        item = self.panel.tree.topLevelItem(row_path[0])
        for i in row_path[1:]:
            item = item.child(i)
        self.panel.tree.setCurrentItem(item)
        _pump()
        return item

    def fix_rows(self):
        """(set position, fix position, path) of every fix row, read from the widgets."""
        out = []
        t = self.panel.tree
        for k in range(t.topLevelItemCount()):
            top = t.topLevelItem(k)
            data = top.data(0, QtCore.Qt.UserRole) or {}
            if data.get("kind") != "set":
                continue
            for j in range(top.childCount()):
                d = top.child(j).data(0, QtCore.Qt.UserRole) or {}
                if d.get("kind") == "fix":
                    out.append((k, d["fix"], (k, j)))
        return out

    def removal_rows(self):
        """(group position, path) of every removal row, read from the widgets."""
        out = []
        t = self.panel.tree
        for k in range(t.topLevelItemCount()):
            top = t.topLevelItem(k)
            for j in range(top.childCount()):
                d = top.child(j).data(0, QtCore.Qt.UserRole) or {}
                if d.get("kind") == "removal":
                    out.append((d["group"], (k, j)))
        return out

    def status_label(self):
        labels = _find("labelStatus", QtWidgets.QLabel)
        return labels[0].text() if labels else None

    def copy_with_fix(self, sketch, fix):
        """The sketch's content after ``apply_fix`` on a copy.  The copy is made the way the API makes its own
        (serialised properties into a hidden document), which leaves the real sketch alone."""
        scratch = SD._Scratch(sketch)
        try:
            cp = scratch.copy()
            SD.apply_fix(cp, fix)
            cp.solve()  # the editor solves after each change; this also refreshes reference datums
            return _content(cp)
        finally:
            scratch.close()


# ---------------------------------------------------------------------------
# Checks 1 to 3: contents, applying, undo
# ---------------------------------------------------------------------------


class TestSketcherDiagnosisPanelGui(PanelTestBase):
    _survey = None

    def testPanelIsADockNotATaskDialog(self):
        c = self.case("single_conflict")
        p = self.open_panel(c.sketch)
        self.assertIsInstance(p, QtWidgets.QDockWidget)
        self.assertIs(p.parent(), Gui.getMainWindow())
        self.assertEqual(_editing(), (c.sketch.Document.Name, c.sketch.Name))
        self.assertTrue(Gui.Control.activeDialog())  # the editor's own dialog is still the task dialog
        # reopening reuses the one panel
        Gui.runCommand(COMMAND)
        self.assertIs(self.P.current_panel(), p)

    # check 1 ---------------------------------------------------------------
    def testContentsEqualTheApiOnEveryFaultyCase(self):
        cases = self.faulty_cases()
        self.assertGreaterEqual(len(cases), 20)
        for c in cases:
            sk = c.sketch
            expected = SD.diagnose(sk)
            p = self.open_panel(sk)
            with self.subTest(case=c.name):
                self.assertEqual(_stable(p.result), _stable(expected))
                self.assertEqual(_rows(p.tree), _expected_rows(sk, expected))
                line = _expected_not_offered_line(expected)
                lines = p.status.text().split("\n")
                self.assertEqual(lines[1] if len(lines) > 1 else None, line)
                self.assertEqual(p.state, "ready")
                self.assertFalse(p.apply_button.isEnabled())  # nothing chosen yet
            self.close_panel()
            Gui.ActiveDocument.resetEdit()
            _pump(20)

    def testSketchWithNothingWrongSaysSoAndOffersNothing(self):
        done = 0
        for c in self.rig.cases:
            if c.cls != "full" or c.truth.get("dof") != 0:
                continue
            p = self.open_panel(c.sketch)
            with self.subTest(case=c.name):
                self.assertEqual(p.tree.topLevelItemCount(), 0)
                self.assertFalse(p.apply_button.isEnabled())
                self.assertTrue(p.status.text().startswith("Nothing is wrong"), p.status.text())
                self.assertEqual(p.result["conflict_sets"], [])
                self.assertEqual(p.result["redundancy_groups"], [])
            self.close_panel()
            Gui.ActiveDocument.resetEdit()
            _pump(20)
            done += 1
            if done >= 6:
                break
        self.assertGreaterEqual(done, 3)

    # checks 2 and 3 --------------------------------------------------------
    @classmethod
    def _apply_survey(cls, test):
        """Apply, undo, redo and undo again every fix of every faulty case, once; the measurements are shared by
        the checks that read them."""
        if cls._survey is not None:
            return cls._survey
        survey = []
        only = os.environ.get("PANEL_SURVEY_CASES", "").split(",") if os.environ.get("PANEL_SURVEY_CASES") else None
        for c in test.faulty_cases():
            if only is not None and c.name not in only:
                continue
            sk = c.sketch
            doc = sk.Document
            p = test.open_panel(sk)
            doc.clearUndos()  # the document's undo limit would otherwise swallow the count after a few cases
            base_red = set(p.result["redundancy_members"])
            rows = [(k, j, path, p.result["conflict_sets"][k]["fixes"][j]) for k, j, path in test.fix_rows()]
            rows += [(None, g, path, p.result["redundancy_groups"][g]["fix"]) for g, path in test.removal_rows()]
            for k, j, path, fix in rows:
                if fix is None:
                    continue
                removal = k is None
                original = _content(sk)
                expected = test.copy_with_fix(sk, fix)
                test.choose(path)
                rec = {"case": c.name, "set": k, "fix": j, "removal": removal, "applyable": p.apply_button.isEnabled()}
                u0, r0 = doc.UndoCount, doc.RedoCount
                p.apply_button.click()
                test.wait_ready()
                rec["applied"] = _content(sk)
                rec["expected"] = expected
                rec["original"] = original
                rec["undo_delta"] = doc.UndoCount - u0
                rec["undo_name"] = doc.UndoNames[0] if doc.UndoNames else None
                rec["editing"] = _editing() == (doc.Name, sk.Name)
                rec["conflicts"] = sorted(_old_index(i - 1, fix["ops"]) for i in sk.ConflictingConstraints)
                rec["remaining"] = fix["remaining_conflicts"]  # the API's statement, removal or conflict fix alike
                red = {
                    _old_index(i - 1, fix["ops"])
                    for i in list(sk.RedundantConstraints) + list(sk.PartiallyRedundantConstraints)
                }
                rec["new_redundancy"] = sorted(red - base_red) if not fix["leaves_redundancy"] else []
                rec["dof"] = sk.DoF
                rec["dof_stated"] = fix["dof_after"]
                rec["label"] = test.status_label()
                rec["fresh"] = _stable(SD.diagnose(sk)) == _stable(p.result)
                # check 2, stricter: a recompute leaves the serialised sketch and the diagnosis as they are
                shown = _stable(p.result)
                sk.touch()
                doc.recompute()
                test.wait_ready()
                rec["recompute_same_sketch"] = _same_sketch(_content(sk), rec["applied"])
                rec["recompute_same_diagnosis"] = _stable(SD.diagnose(sk)) == shown
                rec["tree_fresh"] = _rows(p.tree) == _expected_rows(sk, p.result)
                Gui.runCommand("Std_Undo")
                test.wait_ready()
                rec["after_undo"] = _content(sk)
                rec["undo_editing"] = _editing() == (doc.Name, sk.Name)
                rec["undo_counts"] = (doc.UndoCount - u0, doc.RedoCount)
                Gui.runCommand("Std_Redo")
                test.wait_ready()
                rec["after_redo"] = _content(sk)
                rec["redo_editing"] = _editing() == (doc.Name, sk.Name)
                Gui.runCommand("Std_Undo")
                test.wait_ready()
                rec["restored"] = _content(sk) == original
                rec["undo_exact"] = rec["after_undo"] == original
                rec["redo_exact"] = rec["after_redo"] == rec["applied"]
                survey.append(rec)
            test.close_panel()
            Gui.ActiveDocument.resetEdit()
            _pump(20)
        cls._survey = survey
        return survey

    def testApplyingEveryOfferedFix(self):
        survey = self._apply_survey(self)
        self.assertGreaterEqual(len(survey), 30 if not os.environ.get("PANEL_SURVEY_CASES") else 1)
        if not os.environ.get("PANEL_SURVEY_CASES"):
            self.assertGreaterEqual(len([r for r in survey if r["removal"]]), 8)  # redundancy removals go through Apply too
        for r in survey:
            with self.subTest(case=r["case"], set=r["set"], fix=r["fix"], removal=r["removal"]):
                self.assertTrue(r["applyable"])
                self.assertTrue(_same_sketch(r["applied"], r["expected"]), "applied sketch differs from apply_fix on a copy")
                # every stated value is the real one, removals on a conflicted sketch included
                self.assertEqual(r["conflicts"], r["remaining"])
                self.assertEqual(r["new_redundancy"], [])
                if r["dof_stated"] is None:
                    self.assertTrue(r["conflicts"], "no degrees of freedom stated, yet no conflict is left")
                else:
                    self.assertEqual(r["dof"], r["dof_stated"])
                self.assertTrue(r["recompute_same_sketch"], "a recompute changed the sketch the panel certified")
                self.assertTrue(r["recompute_same_diagnosis"], "a recompute changed the diagnosis")
                self.assertTrue(r["editing"])
                self.assertTrue(r["fresh"], "the panel's new diagnosis is not the API's for the changed sketch")
                self.assertTrue(r["tree_fresh"])
                self.assertIsNotNone(r["label"])
                expect_over = bool(r["conflicts"])
                self.assertEqual(r["label"].startswith("Over-constrained"), expect_over, r["label"])

    def testEveryApplyIsOneNamedUndoStepAndUndoRedoAreExact(self):
        survey = self._apply_survey(self)
        print(
            "SURVEY",
            json.dumps(
                {
                    "fixes": len(survey),
                    "undo_bit_exact": sum(r["undo_exact"] for r in survey),
                    "redo_bit_exact": sum(r["redo_exact"] for r in survey),
                }
            ),
        )
        for r in survey:
            with self.subTest(case=r["case"], set=r["set"], fix=r["fix"], removal=r["removal"]):
                self.assertEqual(r["undo_delta"], 1)
                self.assertEqual(r["undo_name"], TRANSACTION_NAME)
                self.assertTrue(_same_sketch(r["after_undo"], r["original"]), "undo did not restore the sketch")
                self.assertEqual(r["undo_counts"], (0, 1))
                self.assertTrue(_same_sketch(r["after_redo"], r["applied"]), "redo did not restore the applied sketch")
                self.assertTrue(r["undo_editing"] and r["redo_editing"])
                self.assertTrue(r["restored"])

    # check 4 ----------------------------------------------------------------
    def _browse_and_close(self, sketch, plant=None):
        """Open, diagnose, browse every row, close; no Apply."""
        before = _signature(sketch)
        self.edit(sketch)
        before["editing"] = _editing()
        if plant is not None:
            import SketcherDiagnosisPanel as P

            orig = P.SD.diagnose
            P.SD.diagnose = plant(orig)
        try:
            Gui.runCommand(COMMAND)
            import SketcherDiagnosisPanel as P

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
            self.close_panel()
        finally:
            if plant is not None:
                P.SD.diagnose = orig
        _pump(30)
        after = _signature(sketch)
        Gui.Selection.clearSelection()
        return before, after

    def testNoSideEffectsOnAnyFaultyCase(self):
        for c in self.faulty_cases()[:12]:
            with self.subTest(case=c.name):
                b, a = self._browse_and_close(c.sketch)
                self.assertEqual(a, b)
            Gui.ActiveDocument.resetEdit()
            _pump(20)

    def testNoSideEffectsOnASketchDrawnOffItsConstraints(self):
        """A clean, consistent sketch whose stored geometry sits off its constraints (an unmanaged ``Geometry``
        write, no solve after it).  A diagnosis that solved the real sketch would pull the point onto the
        constraints; the baseline is taken after the write and no recompute, so the pull would show."""
        d = FreeCAD.newDocument("PanelOffDrawn")
        self._extra_docs.append("PanelOffDrawn")
        sk = d.addObject("Sketcher::SketchObject", "S")
        sk.addGeometry(Part.LineSegment(V(0, 0, 0), V(10, 0, 0)))
        sk.addConstraint(Sketcher.Constraint("Coincident", 0, 1, -1, 1))
        sk.addConstraint(Sketcher.Constraint("Horizontal", 0))
        d.recompute()
        self.edit(sk)
        sk.Geometry = [Part.LineSegment(V(0, 0, 0), V(9.7, 0.3, 0))]  # off the Horizontal constraint, unsolved
        _pump(10)
        drawn = [str(g) for g in sk.Geometry]
        self.assertIn("9.7", drawn[0])
        self.assertIn("0.3", drawn[0])
        before = _signature(sk)
        Gui.runCommand(COMMAND)
        import SketcherDiagnosisPanel as P

        self.panel = P.current_panel()
        self.assertTrue(_wait(lambda: self.panel.state in ("ready", "failed", "cancelled")))
        self.assertEqual(self.panel.state, "ready", self.panel.status.text())
        self.close_panel()
        _pump(30)
        self.assertEqual([str(g) for g in sk.Geometry], drawn)
        self.assertEqual(_signature(sk), before)

    def testNoSideEffectsWhenDiagnoseRaises(self):
        c = self.case("single_conflict")

        def plant(orig):
            def raiser(sketch, **kw):
                n = [0]
                inner = kw.get("progress")

                def cb(info):
                    n[0] += 1
                    if inner is not None:
                        inner(info)
                    if n[0] == 7:
                        raise RuntimeError("planted failure in diagnose")

                return orig(sketch, progress=cb)

            return raiser

        b, a = self._browse_and_close(c.sketch, plant)
        self.assertEqual(a, b)
        self.assertFalse([n for n in FreeCAD.listDocuments() if "Scratch" in n])

    def testNoSideEffectsWhenCancelled(self):
        c = self.case("two_overlapping_conflicts") if any(x.name == "two_overlapping_conflicts" for x in self.rig.cases) else self.case("single_conflict")
        self.edit(c.sketch)
        before = _signature(c.sketch)
        import SketcherDiagnosisPanel as P

        orig = P.SD.diagnose

        def cancelling(sketch, **kw):
            n = [0]
            inner = kw["progress"]

            def cb(info):
                n[0] += 1
                if n[0] == 9:
                    self.panel.request_cancel()
                return inner(info)

            return orig(sketch, progress=cb)

        P.SD.diagnose = cancelling
        try:
            Gui.runCommand(COMMAND)
            self.panel = P.current_panel()
            self.assertTrue(_wait(lambda: self.panel.state == "cancelled"))
            self.assertEqual(self.panel.tree.topLevelItemCount(), 0)
            self.assertFalse(self.panel.apply_button.isEnabled())
            self.close_panel()
        finally:
            P.SD.diagnose = orig
        _pump(30)
        self.assertEqual(_signature(c.sketch), before)
        self.assertFalse([n for n in FreeCAD.listDocuments() if "Scratch" in n])

    def testDiagnosisDoesNotLeaveEditModeOrChangeTheActiveDocument(self):
        c = self.case("single_conflict")
        self.edit(c.sketch)
        seen = []
        orig_docs = sorted(FreeCAD.listDocuments())
        import SketcherDiagnosisPanel as P

        real = P.SD.diagnose

        def watching(sketch, **kw):
            inner = kw["progress"]

            def cb(info):
                seen.append((_editing(), _active_name(), Gui.ActiveDocument.Document.Name))
                return inner(info)

            return real(sketch, progress=cb)

        P.SD.diagnose = watching
        try:
            Gui.runCommand(COMMAND)
            self.panel = P.current_panel()
            self.wait_ready()
        finally:
            P.SD.diagnose = real
        self.assertGreater(len(seen), 5)
        want = ((c.sketch.Document.Name, c.sketch.Name), c.sketch.Document.Name, c.sketch.Document.Name)
        self.assertTrue(all(s == want for s in seen), set(seen))
        self.assertEqual(sorted(FreeCAD.listDocuments()), orig_docs)

    # check 5 ----------------------------------------------------------------
    def fresh_sketch(self, docname):
        """A new document with a line that has two conflicting lengths, so a test can change it freely."""
        d = FreeCAD.newDocument(docname)
        self._extra_docs.append(docname)
        sk = d.addObject("Sketcher::SketchObject", "S")
        sk.addGeometry(Part.LineSegment(V(0, 0, 0), V(10, 0, 0)))
        sk.addConstraint(Sketcher.Constraint("Horizontal", 0))
        sk.addConstraint(Sketcher.Constraint("Distance", 0, 10.0))
        sk.addConstraint(Sketcher.Constraint("Distance", 0, 12.0))
        d.recompute()

        class _C:
            sketch = sk

        return _C

    def _stale_setup(self, docname="PanelStale"):
        c = self.fresh_sketch(docname)
        p = self.open_panel(c.sketch)
        rows = self.fix_rows()
        self.assertTrue(rows)
        self.choose(rows[0][2])
        self.assertTrue(p.apply_button.isEnabled())
        return c, p

    def _assert_stale_handled(self, c, p, reason):
        """Right after the event, before any click or pump: no stale fix can be applied; later the panel is
        consistent again or gone."""
        self.assertFalse(p.apply_button.isEnabled(), reason)
        self.assertNotEqual(p.state, "ready", reason)
        before = _content(c.sketch) if c.sketch.Document.Name in FreeCAD.listDocuments() else None
        applied = p.apply_chosen()
        self.assertFalse(applied, reason)
        if before is not None:
            self.assertEqual(_content(c.sketch), before, reason)

    def testStaleAfterAConstraintIsAdded(self):
        c, p = self._stale_setup()
        c.sketch.addConstraint(Sketcher.Constraint("Horizontal", 0))
        self._assert_stale_handled(c, p, "constraint added")
        self.wait_ready()
        self.assertEqual(_stable(p.result), _stable(SD.diagnose(c.sketch)))
        self.assertEqual(_rows(p.tree), _expected_rows(c.sketch, p.result))

    def _stale_removal_setup(self, docname="PanelStaleRemoval"):
        """A line with the same length twice (redundant), a removal row chosen."""
        d = FreeCAD.newDocument(docname)
        self._extra_docs.append(docname)
        sk = d.addObject("Sketcher::SketchObject", "S")
        sk.addGeometry(Part.LineSegment(V(0, 0, 0), V(10, 0, 0)))
        sk.addConstraint(Sketcher.Constraint("Horizontal", 0))
        sk.addConstraint(Sketcher.Constraint("Distance", 0, 10.0))
        sk.addConstraint(Sketcher.Constraint("Distance", 0, 10.0))
        d.recompute()

        class _C:
            sketch = sk

        p = self.open_panel(sk)
        rows = self.removal_rows()
        self.assertTrue(rows)
        self.choose(rows[0][1])
        self.assertTrue(p.apply_button.isEnabled())
        return _C, p

    def testStaleRemovalIsNotApplied(self):
        c, p = self._stale_removal_setup()
        c.sketch.addConstraint(Sketcher.Constraint("Distance", 0, 10.0))
        self._assert_stale_handled(c, p, "constraint added under a chosen removal")
        self.wait_ready()
        self.assertEqual(_stable(p.result), _stable(SD.diagnose(c.sketch)))

    def testARedundantSketchIsRepairedFromThePanel(self):
        c, p = self._stale_removal_setup("PanelRepairRedundant")
        doc = c.sketch.Document
        u0 = doc.UndoCount
        p.apply_button.click()
        self.wait_ready()
        self.assertEqual(doc.UndoCount - u0, 1)
        self.assertEqual(p.result["redundancy_groups"], [])
        self.assertTrue(p.status.text().startswith("Nothing is wrong"), p.status.text())

    def testStaleAfterADatumChanges(self):
        c, p = self._stale_setup()
        datum = [i for i, k in enumerate(c.sketch.Constraints) if k.Type in SD._DATUM_TYPES and k.Driving][-1]
        # setDatum refuses a conflicting sketch, so the datum is changed through the property
        cons = c.sketch.Constraints
        cons[datum] = Sketcher.Constraint("Distance", 0, abs(cons[datum].Value) + 1.0)
        c.sketch.Constraints = cons
        self._assert_stale_handled(c, p, "datum changed")
        self.wait_ready()
        self.assertEqual(_stable(p.result), _stable(SD.diagnose(c.sketch)))

    def testStaleAfterUndo(self):
        c = self.fresh_sketch("PanelStaleUndo")
        doc = c.sketch.Document
        self.open_panel(c.sketch)
        p = self.panel
        doc.openTransaction("test change")
        c.sketch.addConstraint(Sketcher.Constraint("Vertical", 0))
        doc.commitTransaction()
        self.wait_ready()
        rows = self.fix_rows()
        self.choose(rows[0][2])
        self.assertTrue(p.apply_button.isEnabled())
        doc.undo()
        self._assert_stale_handled(c, p, "undo")
        self.wait_ready()
        self.assertEqual(_stable(p.result), _stable(SD.diagnose(c.sketch)))

    def testStaleWhenLeavingEditMode(self):
        c, p = self._stale_setup()
        Gui.ActiveDocument.resetEdit()
        self._assert_stale_handled(c, p, "left edit mode")
        self.assertTrue(_wait(lambda: p.state == "closed"))
        self.assertIsNone(self.P.current_panel())
        self.assertEqual(_editing(), None)

    def testStaleWhenTheDocumentIsClosed(self):
        sk = self.fresh_sketch("PanelCloseMe").sketch
        p = self.open_panel(sk)
        self.choose(self.fix_rows()[0][2])
        self.assertTrue(p.apply_button.isEnabled())
        FreeCAD.closeDocument("PanelCloseMe")
        self.assertFalse(p.apply_button.isEnabled())
        self.assertFalse(p.apply_chosen())
        self.assertTrue(_wait(lambda: p.state == "closed"))
        self.assertIsNone(self.P.current_panel())
        FreeCAD.setActiveDocument(self.rig.doc.Name)

    # check 6 ----------------------------------------------------------------
    def testChoosingRowsSelectsExactlyTheirConstraints(self):
        for name in ("single_conflict", "two_overlapping_conflicts") if any(x.name == "two_overlapping_conflicts" for x in self.rig.cases) else ("single_conflict",):
            c = self.case(name)
            p = self.open_panel(c.sketch)
            r = p.result
            t = p.tree
            checked = 0
            for k, cs in enumerate(r["conflict_sets"]):
                self.choose((k,))
                self.assertEqual(_selected_names(), sorted(_sub(m) for m in cs["members"]))
                for j, m in enumerate(cs["members"]):
                    self.choose((k, j))
                    self.assertEqual(_selected_names(), [_sub(m)])
                for f, fix in enumerate(cs["fixes"]):
                    self.choose((k, len(cs["members"]) + f))
                    self.assertEqual(_selected_names(), sorted(_sub(o["index"]) for o in fix["ops"]))
                    checked += 1
            self.assertGreater(checked, 0)
            # the constraint list of the editor shows the same rows selected
            self.choose((0,))
            lists = _find("listWidgetConstraints", QtWidgets.QListWidget)
            if lists:
                self.assertEqual(len(lists[0].selectedItems()), len(r["conflict_sets"][0]["members"]))
            self.close_panel()
            Gui.ActiveDocument.resetEdit()
            _pump(20)

    def testChoosingRedundancyRowsSelects(self):
        c = [x for x in self.faulty_cases() if x.truth.get("redundant")][0]
        p = self.open_panel(c.sketch)
        r = p.result
        self.assertTrue(r["redundancy_groups"])
        base = len(r["conflict_sets"])
        for k, g in enumerate(r["redundancy_groups"]):
            self.choose((base + k,))
            self.assertEqual(_selected_names(), sorted(_sub(m) for m in g["members"]))
            self.choose((base + k, len(g["members"])))
            self.assertEqual(_selected_names(), [_sub(g["suggested_removal"])])

    def testOpeningThePanelKeepsTheUsersSelection(self):
        c = self.case("two_overlapping_conflicts")
        self.edit(c.sketch)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(c.sketch.Document.Name, c.sketch.Name, "Constraint1")
        Gui.Selection.addSelection(c.sketch.Document.Name, c.sketch.Name, "Constraint3")
        self.assertEqual(_selected_names(), ["Constraint1", "Constraint3"])
        Gui.runCommand(COMMAND)  # not open_panel(): that enters edit mode again, which is not what is under test
        import SketcherDiagnosisPanel as P

        self.P = P
        self.panel = P.current_panel()
        self.wait_ready()
        self.assertEqual(self.panel.state, "ready")
        self.assertEqual(_selected_names(), ["Constraint1", "Constraint3"])

    def testChoosingNothingClearsOnlyWhatThePanelSelected(self):
        c = self.case("single_conflict")
        p = self.open_panel(c.sketch)
        self.choose((0,))
        mine = _selected_names()
        self.assertTrue(mine)
        Gui.Selection.addSelection(c.sketch.Document.Name, c.sketch.Name, "Edge1")  # the user's own pick
        self.assertIn("Edge1", _selected_names())
        p.tree.clearSelection()
        _pump()
        self.assertEqual(_selected_names(), ["Edge1"])
        self.assertFalse(p.apply_button.isEnabled())

    # check 5, the sixth event: the editor moves to a sketch in another document ---------------------------------
    def testStaleWhenTheEditorSwitchesToASketchInAnotherDocument(self):
        c, p = self._stale_setup("PanelTwoDocsA")
        other = self.fresh_sketch("PanelTwoDocsB").sketch
        before_other = _content(other)
        Gui.ActiveDocument = Gui.getDocument(other.Document.Name)
        Gui.ActiveDocument.setEdit(other.Name)  # no pump: the check is of the instant after the event
        self._assert_stale_handled(c, p, "the editor switched to another document's sketch")
        self.assertTrue(_wait(lambda: p.state == "closed"))
        self.assertIsNone(self.P.current_panel())
        self.assertEqual(_content(other), before_other)
        self.assertEqual(_editing(), (other.Document.Name, other.Name))

    def testApplyRefusesWhenAnotherDocumentsSketchIsInEditEvenIfNoEventArrived(self):
        c, p = self._stale_setup("PanelTwoDocsC")
        before = _content(c.sketch)
        p._unwatch()  # no observer tells the panel: Apply must look at the editor itself
        other = self.fresh_sketch("PanelTwoDocsD").sketch
        self.edit(other)
        before_other = _content(other)
        self.assertFalse(p.apply_chosen())
        self.assertEqual(_content(c.sketch), before)
        self.assertEqual(_content(other), before_other)

    # looking at another document while the sketch stays in edit in its own -------------------------------------
    def _look_elsewhere_setup(self, tag):
        """The panel's sketch in edit in document A with a fix chosen; document B exists and has no edit going.
        Returns (c, p, b_document_name, rows) with the user still on A."""
        other = self.fresh_sketch("PanelLookB" + tag).sketch
        c, p = self._stale_setup("PanelLookA" + tag)
        return c, p, other.Document.Name, _rows(p.tree)

    def _look_at(self, docname):
        """What clicking the document's tab does: the real activation, which tells the observers (assigning
        ``Gui.ActiveDocument`` only rebinds the module attribute)."""
        Gui.setActiveDocument(docname)
        _pump(30)
        self.assertEqual(Gui.ActiveDocument.Document.Name, docname)

    def testLookingAtAnotherDocumentAndComingBackKeepsThePanel(self):
        c, p, b, rows = self._look_elsewhere_setup("1")
        before = _content(c.sketch)
        a = c.sketch.Document.Name
        self._look_at(b)
        self.assertNotEqual(p.state, "closed")
        self.assertEqual(p.state, "ready")
        self.assertFalse(p.apply_button.isEnabled())
        self.assertFalse(p.refresh_button.isEnabled())
        self.assertIn("not the active one", p.status.text())
        self.assertEqual(_rows(p.tree), rows)
        self._look_at(a)
        self.assertEqual(p.state, "ready")
        self.assertTrue(p.apply_button.isEnabled())
        self.assertTrue(p.refresh_button.isEnabled())
        self.assertNotIn("not the active one", p.status.text())
        self.assertEqual(_rows(p.tree), rows)
        self.assertEqual(_content(c.sketch), before)
        self.assertTrue(p.apply_chosen())  # and it works without a click

    def testApplyIsRefusedWhileLookingAtAnotherDocument(self):
        for watched in (True, False):
            c, p, b, rows = self._look_elsewhere_setup("2%s" % watched)
            before = _signature(c.sketch)
            if not watched:
                p._unwatch()  # no event arrives: Apply must look at the editor itself
            self._look_at(b)
            before_b = _signature(c.sketch)
            self.assertFalse(p.apply_chosen())
            self.assertEqual(_signature(c.sketch)["content"], before["content"])
            self.assertEqual(_signature(c.sketch)["undo"], before["undo"])
            self.assertNotEqual(p.state, "closed")
            self.assertEqual(p.state, "ready")
            self.assertEqual(_rows(p.tree), rows)
            self.assertNotIn("changed", p.status.text())
            self.assertIn("not the active one", p.status.text())
            self.assertEqual(_signature(c.sketch), before_b)
            self.close_panel()
            self._look_at(c.sketch.Document.Name)

    def testRefreshIsRefusedWhileLookingAtAnotherDocument(self):
        c, p, b, rows = self._look_elsewhere_setup("3")
        self._look_at(b)
        generation = p._generation
        p.diagnose_now()
        _pump(50)
        self.assertEqual(p.state, "ready")
        self.assertEqual(p._generation, generation)
        self.assertEqual(_rows(p.tree), rows)
        self.assertIn("not the active one", p.status.text())
        self.assertNotIn("no longer being edited", p.status.text())
        self._look_at(c.sketch.Document.Name)
        self.assertEqual(p.state, "ready")
        self.assertTrue(p.refresh_button.isEnabled())

    def testEditingASketchInTheOtherDocumentStillClosesThePanel(self):
        c, p, b, rows = self._look_elsewhere_setup("4")
        self._look_at(b)
        self.assertEqual(p.state, "ready")
        sk = FreeCAD.getDocument(b).getObject("S")
        Gui.ActiveDocument.setEdit(sk.Name)
        _pump(30)
        self.assertTrue(_wait(lambda: p.state == "closed"))
        self.assertIsNone(self.P.current_panel())

    def testUsersTabChoiceDuringARunIsNotUndoneWhenTheRunEnds(self):
        """The panel pumps events while it diagnoses.  A document tab clicked meanwhile stays the active document
        after the run: the scratch document's close puts back only a change that was its own doing."""
        other = self.fresh_sketch("PanelTabB").sketch
        b = other.Document.Name
        c = self.fresh_sketch("PanelTabA")
        a = c.sketch.Document.Name
        p = self.open_panel(c.sketch)
        fired = []
        orig = p._on_progress

        def hook(info):
            if not fired:
                fired.append(1)
                Gui.setActiveDocument(b)
            return orig(info)

        p._on_progress = hook
        p.diagnose_now()
        self.assertTrue(_wait(lambda: p.state in ("ready", "failed", "closed")))
        self.assertTrue(fired)
        self.assertEqual(Gui.ActiveDocument.Document.Name, b)
        self.assertEqual(_active_name(), b)
        self.assertNotEqual(a, b)
        self.assertFalse([n for n in FreeCAD.listDocuments() if "Scratch" in n])

    def testAResultArrivingWhileThePanelWaitsIsHeldBackAndShownOnReturn(self):
        """The guard in ``_show``: a diagnosis that finishes while the sketch's document is out of view fills the
        tree but leaves it disabled and the status line saying why; coming back shows the result."""
        other = self.fresh_sketch("PanelLateB").sketch
        b = other.Document.Name
        c = self.fresh_sketch("PanelLateA")
        a = c.sketch.Document.Name
        p = self.open_panel(c.sketch)
        self.choose(self.fix_rows()[0][2])
        real = self.P.SD.diagnose

        def late(*args, **kw):
            r = real(*args, **kw)
            Gui.setActiveDocument(b)  # the click lands after the scratch document is gone
            return r

        self.P.SD.diagnose = late
        try:
            p.diagnose_now()
            self.assertTrue(_wait(lambda: p.state in ("ready", "failed", "closed")))
        finally:
            self.P.SD.diagnose = real
        self.assertEqual(p.state, "ready")
        self.assertTrue(p._dormant)
        self.assertFalse(p.apply_button.isEnabled())
        self.assertFalse(p.refresh_button.isEnabled())
        self.assertFalse(p.tree.isEnabled())
        self.assertIn("not the active one", p.status.text())
        self.assertTrue(self.fix_rows())
        self._look_at(a)
        self.assertNotIn("not the active one", p.status.text())
        self.assertIn("Found", p.status.text())
        self.assertTrue(p.refresh_button.isEnabled())
        self.assertTrue(p.tree.isEnabled())
        self.choose(self.fix_rows()[0][2])
        self.assertTrue(p.apply_button.isEnabled())

    # check 6, the user's selection -----------------------------------------------------------------------------
    def testClosingThePanelPutsTheUsersSelectionBack(self):
        c = self.case("two_overlapping_conflicts")
        self.edit(c.sketch)
        Gui.Selection.clearSelection()
        doc, name = c.sketch.Document.Name, c.sketch.Name
        Gui.Selection.addSelection(doc, name, "Constraint1")
        Gui.Selection.addSelection(doc, name, "Edge1")
        mine = _selected_names()
        Gui.runCommand(COMMAND)
        import SketcherDiagnosisPanel as P

        self.P = P
        self.panel = P.current_panel()
        self.wait_ready()
        self.choose((0,))
        self.assertNotEqual(_selected_names(), mine)  # the row replaced it
        self.choose((0, 0))
        self.close_panel()
        _pump(30)
        self.assertEqual(_selected_names(), mine)

    def testClosingWithNothingSelectedBeforeLeavesNothingSelected(self):
        c = self.case("single_conflict")
        p = self.open_panel(c.sketch)
        Gui.Selection.clearSelection()
        self.choose((0,))
        self.assertTrue(_selected_names())
        self.close_panel()
        _pump(30)
        self.assertEqual(_selected_names(), [])

    def testRestoredSelectionLeavesOutNamesThatNoLongerExist(self):
        c = self.fresh_sketch("PanelSelStale")
        sk = c.sketch
        self.edit(sk)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(sk.Document.Name, sk.Name, "Constraint3")
        Gui.Selection.addSelection(sk.Document.Name, sk.Name, "Constraint1")
        Gui.runCommand(COMMAND)
        import SketcherDiagnosisPanel as P

        self.P = P
        self.panel = P.current_panel()
        self.wait_ready()
        p = self.panel
        n = len(sk.Constraints)
        row = [
            path for k, j, path in self.fix_rows()
            if p.result["conflict_sets"][k]["fixes"][j]["ops"][0]["op"] == "delete"
            and p.result["conflict_sets"][k]["fixes"][j]["ops"][0]["index"] == n - 1
        ][0]
        self.choose(row)
        p.apply_button.click()
        self.wait_ready()
        self.assertEqual(len(sk.Constraints), n - 1)
        self.close_panel()  # Constraint3 does not exist any more: restoring must not raise
        _pump(30)
        self.assertEqual(_selected_names(), ["Constraint1"])

    # check 1 again: what was not offered, and why -------------------------------------------------------------
    def testThePanelSaysWhatWasNotOffered(self):
        doc = FreeCAD.newDocument("PanelNotOffered")
        self._extra_docs.append(doc.Name)
        sk, _reader = Corpus.repair_reader_pair(doc, "Sk.Constraints.w2 + 1 mm")
        expected = SD.diagnose(sk)
        self.assertTrue(expected["not_offered"])
        p = self.open_panel(sk)
        self.assertEqual(_stable(p.result), _stable(expected))
        line = _expected_not_offered_line(expected)
        self.assertEqual(p.status.text().split("\n")[1:], [line])
        self.assertIn("another object's expression reads the constraint", line)
        self.assertEqual(_rows(p.tree), _expected_rows(sk, expected))

    def testARemovalThatCannotBeOfferedGivesItsReasonAndCannotBeApplied(self):
        doc = FreeCAD.newDocument("PanelNoRemoval")
        self._extra_docs.append(doc.Name)
        sk = Corpus.repair_equal_distances(doc, "S", n=1, expr=True)
        expected = SD.diagnose(sk)
        no = [g for g in expected["redundancy_groups"] if not g["fix"]]
        self.assertTrue(no)
        p = self.open_panel(sk)
        rows = self.removal_rows()
        self.assertTrue(rows)
        self.assertEqual(_rows(p.tree), _expected_rows(sk, expected))
        k, path = [r for r in rows if not expected["redundancy_groups"][r[0]]["fix"]][0]
        self.choose(path)
        self.assertIn("(cannot be applied: ", p.tree.currentItem().text(0))
        self.assertFalse(p.apply_button.isEnabled())
        self.assertFalse(p.apply_chosen())

    # check 2, overlapping redundancy groups ------------------------------------------------------------------
    def testOverlappingRedundancyIsRepairedByApplyingAndDiagnosingAgain(self):
        doc = FreeCAD.newDocument("PanelTriple")
        self._extra_docs.append(doc.Name)
        sk = Corpus.repair_equal_distances(doc, "S", n=2)
        p = self.open_panel(sk)
        doc.clearUndos()
        steps = 0
        while p.result["redundancy_groups"]:
            self.assertLess(steps, 4, "applying removals does not converge")
            rows = self.removal_rows()
            self.choose(rows[0][1])
            self.assertTrue(p.apply_button.isEnabled(), p.tree.currentItem().text(0))
            n = len(sk.Constraints)
            p.apply_button.click()
            self.wait_ready()
            self.assertEqual(len(sk.Constraints), n - 1)
            steps += 1
            self.assertEqual(doc.UndoCount, steps)
        self.assertEqual(steps, 2)
        self.assertTrue(p.status.text().startswith("Nothing is wrong"), p.status.text())

    # check 8 ----------------------------------------------------------------
    def testCommandIsActiveOnlyWithASketchInEdit(self):
        c = self.case("single_conflict")
        cmd = Gui.Command.get(COMMAND)
        Gui.Selection.clearSelection()
        self.assertFalse(cmd.isActive())
        self.edit(c.sketch)
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
        self.edit(sketch)
        links = _find("labelDiagnose", QtWidgets.QLabel)
        self.assertEqual(len(links), 1)
        text = self.status_label()
        visible = not links[0].isHidden()
        Gui.ActiveDocument.resetEdit()
        _pump(30)
        return text, visible

    def testMessagesBoxOffersTheLinkOnlyInTheConflictingAndRedundantStates(self):
        seen = {}
        for c in self.rig.cases:
            if c.cls == "limit":
                continue
            text, visible = self._link_state(c.sketch)
            key = text.split(":")[0].strip() if text else text
            seen.setdefault(key, set()).add(visible)
            offers = key in ("Over-constrained", "Redundant constraints", "Partially redundant")
            with self.subTest(case=c.name, text=text):
                self.assertEqual(visible, offers)
        for key in ("Over-constrained", "Redundant constraints", "Fully constrained", "Under-constrained"):
            self.assertIn(key, seen)
        self.assertEqual(seen["Fully constrained"], {False})
        self.assertEqual(seen["Over-constrained"], {True})

    def testPartiallyRedundantStateOffersTheLink(self):
        """Two lines, PointOnObject on (0,2) and (1,1) to the x-axis, Coincident (0,2)-(1,1) and DistanceY 0 between them, in that order: the solver reports one constraint as partially redundant."""
        d = FreeCAD.newDocument("PanelPartial")
        self._extra_docs.append("PanelPartial")
        sk = d.addObject("Sketcher::SketchObject", "S")
        sk.addGeometry(Part.LineSegment(V(1, 0, 0), V(5, 0.1, 0)))
        sk.addGeometry(Part.LineSegment(V(5, 0, 0), V(9, 3, 0)))
        sk.addConstraint(Sketcher.Constraint("PointOnObject", 0, 2, -1))
        sk.addConstraint(Sketcher.Constraint("Coincident", 0, 2, 1, 1))
        sk.addConstraint(Sketcher.Constraint("PointOnObject", 1, 1, -1))
        sk.addConstraint(Sketcher.Constraint("DistanceY", 0, 2, 1, 1, 0.0))
        d.recompute()
        sk.solve()
        self.assertEqual(list(sk.PartiallyRedundantConstraints), [2])
        text, visible = self._link_state(sk)
        self.assertTrue(text.startswith("Partially redundant"), text)
        self.assertTrue(visible)

    def testLinkOpensThePanelAndStockLinksKeepTheirTooltips(self):
        c = self.case("single_conflict")
        self.edit(c.sketch)
        links = _find("labelDiagnose", QtWidgets.QLabel)
        links[0].linkActivated.emit("#diagnose")
        _pump(30)
        import SketcherDiagnosisPanel as P

        self.panel = P.current_panel()
        self.assertIsNotNone(self.panel)
        self.wait_ready()
        stock = _find("labelStatusLink", QtWidgets.QLabel)
        self.assertEqual(len(stock), 1)
        self.assertEqual(stock[0].toolTip(), "Selects these conflicting constraints")
        self.close_panel()
        Gui.Selection.clearSelection()
        # the stock click still selects the conflicting constraints
        for typ, buttons in (
            (QtCore.QEvent.MouseButtonPress, QtCore.Qt.LeftButton),
            (QtCore.QEvent.MouseButtonRelease, QtCore.Qt.NoButton),
        ):
            pos = QtCore.QPoint(3, 3)
            ev = QtGui.QMouseEvent(typ, pos, stock[0].mapToGlobal(pos), QtCore.Qt.LeftButton, buttons, QtCore.Qt.NoModifier)
            QtWidgets.QApplication.sendEvent(stock[0], ev)
        _pump(30)
        self.assertEqual(
            _selected_names(), sorted("Constraint%d" % i for i in c.sketch.ConflictingConstraints)
        )

    # check 7 ----------------------------------------------------------------
    @classmethod
    def _planted_1000(cls):
        if getattr(cls, "_p1000", None) is None:
            d = FreeCAD.newDocument("Panel1000")
            sk, info = Corpus.build_scale(d, "planted_1000", 1000, planted=True)
            d.recompute()
            cls._p1000 = (d, sk, info)
        return cls._p1000

    @classmethod
    def tearDownClass(cls):
        for name in ("Panel1000", "PanelCorpus"):
            if name in FreeCAD.listDocuments():
                FreeCAD.closeDocument(name)
        super().tearDownClass()

    def testResponsivenessAtAThousandConstraints(self):
        d, sk, info = self._planted_1000()
        self.edit(sk)
        # an independent witness of the event loop: a 5 ms timer, the gaps between its firings
        stamps = []
        timer = QtCore.QTimer()
        timer.setInterval(5)
        timer.timeout.connect(lambda: stamps.append(time.time()))
        timer.start()
        t0 = time.time()
        Gui.runCommand(COMMAND)
        import SketcherDiagnosisPanel as P

        self.panel = P.current_panel()
        self.assertTrue(_wait(lambda: self.panel.state == "ready", timeout=120))
        t1 = time.time()
        timer.stop()
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        edges = [stamps[0] - t0] if stamps else []
        longest = max(gaps + edges + [t1 - stamps[-1] if stamps else 0.0])
        found = [cs["members"] for cs in self.panel.result["conflict_sets"]]
        stats = {
            "seconds": t1 - t0,
            "longest_gap_timer": longest,
            "longest_gap_panel": self.panel.max_gap,
            "members": found,
        }
        print("PANEL1000", json.dumps(stats))
        self.assertEqual(found, [info["conflict_set"]])
        self.assertLessEqual(longest, EVENT_GAP_BAR, stats)
        self.assertLessEqual(self.panel.max_gap, EVENT_GAP_BAR, stats)

    def testCancelTakesEffectQuicklyAtAThousandConstraints(self):
        d, sk, info = self._planted_1000()
        self.edit(sk)
        before = _signature(sk)
        import SketcherDiagnosisPanel as P

        real = P.SD.diagnose
        marks = {}

        def clicking(sketch, **kw):
            inner = kw["progress"]
            n = [0]

            def cb(info_):
                n[0] += 1
                if n[0] == 40 and "t_click" not in marks:
                    # a click that arrives just after an event-loop turn: the worst case for the wait
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
                return inner(info_)

            try:
                return real(sketch, progress=cb)
            finally:
                marks["t_done"] = time.time()

        P.SD.diagnose = clicking
        try:
            Gui.runCommand(COMMAND)
            self.panel = P.current_panel()
            self.assertTrue(_wait(lambda: self.panel.state == "cancelled", timeout=120))
        finally:
            P.SD.diagnose = real
        latency = marks["t_done"] - marks["t_click"]
        print("CANCEL1000", json.dumps({"latency": latency}))
        self.assertLessEqual(latency, CANCEL_BAR)
        self.close_panel()
        _pump(30)
        self.assertEqual(_signature(sk), before)
        self.assertFalse([n for n in FreeCAD.listDocuments() if "Scratch" in n])

