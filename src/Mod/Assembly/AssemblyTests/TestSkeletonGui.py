# SPDX-License-Identifier: LGPL-2.1-or-later
"""G06 slice 3b: the skeleton commands and the Skeleton panel.

Run with the GUI binary: ``FreeCAD -t TestAssemblySkeletonGui``. The baseline document is ``skeleton_gui_fixture`` (one
skeleton, two subscriber parts, one frozen and one broken subscription); cross-document cases are built here with the 3a
corpus kit. Every panel action is compared with the same 3a API call on a twin document, and Cancel is compared with the
property-and-shape signature plus the state of every subscription of every open document. No test opens a modal dialog:
the questions the panel asks are replaced by scripted answers, and an unexpected question fails the test.
"""

import gc
import os
import shutil
import time
import unittest

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtGui, QtWidgets

import AssemblySkeleton as AS
import SkeletonPanel as SP
from AssemblyTests import skeleton_corpus as K
from AssemblyTests import skeleton_gui_fixture as F


class Prompts:
    """Scripted answers for the questions the GUI asks; an unexpected question fails the test."""

    def __init__(self):
        self.asked = []
        self.names = []             # answers to ask_name, in order ("<default>" accepts the proposal)
        self.confirms = []          # answers to confirm, in order
        self.items = []             # answers to ask_item, in order
        self.reported = []          # what report_problems was given

    def ask_name(self, parent, title, text, default=""):
        self.asked.append(("name", title, default))
        if not self.names:
            raise AssertionError("unexpected question: %s" % title)
        answer = self.names.pop(0)
        return default if answer == "<default>" else answer

    def confirm(self, parent, title, text):
        self.asked.append(("confirm", title))
        if not self.confirms:
            raise AssertionError("unexpected question: %s" % title)
        return self.confirms.pop(0)

    def ask_item(self, parent, candidates):
        self.asked.append(("item", sorted(AS.describe_item(i)["item"] for i in candidates)))
        if not self.items:
            raise AssertionError("unexpected question: which item")
        return self.items.pop(0)

    def report_problems(self, parent, problems):
        self.reported.append(list(problems))


def live_panels():
    return [o for o in gc.get_objects() if isinstance(o, SP.TaskSkeleton) and not o.done]


class GuiCase(unittest.TestCase):
    def setUp(self):
        self.rig = None
        self.prompts = Prompts()
        self._saved = (SP.ask_name, SP.confirm, SP.ask_item, SP.report_problems)
        SP.ask_name, SP.confirm, SP.ask_item, SP.report_problems = (
            self.prompts.ask_name, self.prompts.confirm, self.prompts.ask_item, self.prompts.report_problems)

    def tearDown(self):
        SP.ask_name, SP.confirm, SP.ask_item, SP.report_problems = self._saved
        SP._current = None
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        Gui.Selection.clearSelection()
        for n in list(App.listDocuments()):
            App.closeDocument(n)

    def load(self, name="SkelGui"):
        self.rig = F.build(name)
        self.doc, self.skel = self.rig.doc, self.rig.skel
        self.activate(self.doc)
        return self.rig

    def reset(self):
        """Between the sub-tests of a loop: close everything and start from fresh prompts."""
        self.tearDown()
        self.setUp()

    @staticmethod
    def activate(doc):
        App.setActiveDocument(doc.Name)
        Gui.ActiveDocument = Gui.getDocument(doc.Name)
        Gui.updateGui()

    def open_panel(self, part):
        self.panel = SP.TaskSkeleton(part)
        Gui.Control.showDialog(self.panel)
        Gui.updateGui()
        return self.panel

    def close_panel(self, accept=False):
        (self.panel.accept if accept else self.panel.reject)()
        Gui.Control.closeDialog()
        Gui.updateGui()

    def snap(self):
        return F.world_snapshot(list(App.listDocuments().values()))

    def twin(self, fn):
        """The normalized signature and states of a twin document after the same 3a call(s)."""
        other = F.build("SkelTwin")
        try:
            fn(other)
            return F.normalized(other.doc), {k: v[:3] for k, v in F.states(other.doc).items()}
        finally:
            other.close()
            self.activate(self.doc)

    @staticmethod
    def differences(got, want):
        """The (object, property, got, want) entries where two normalized signatures differ."""
        out = []
        for name in sorted(set(got) | set(want)):
            a, b = got.get(name), want.get(name)
            if a == b:
                continue
            if a is None or b is None:
                out.append((name, "<object>", a is not None, b is not None))
                continue
            for k in sorted(set(a["props"]) | set(b["props"])):
                if a["props"].get(k) != b["props"].get(k):
                    out.append((name, k, a["props"].get(k), b["props"].get(k)))
            for k in ("type", "label", "shape"):
                if a[k] != b[k]:
                    out.append((name, k, a[k], b[k]))
        return out

    def mine(self):
        return F.normalized(self.doc), {k: v[:3] for k, v in F.states(self.doc).items()}

    def item(self, name):
        return AS.find_item(self.skel, name)

    @staticmethod
    def settle(seconds=0.4):
        t = time.time()
        while time.time() - t < seconds:
            Gui.updateGui()
            time.sleep(0.03)

    def candidate(self, key):
        tgt, sub = K.target_of(self.skel, key)
        return SP.Candidate(tgt, sub, self.skel)


# What each panel action does: (the part the panel is opened on, staging through the panel, the same 3a call on a twin).
ACTIONS = {
    "publish": ("Skel",
                lambda t, p: p.publish(name="Corner", candidates=[t.candidate("vertex")]),
                lambda r: AS.publish(r.skel, "Corner", *K.target_of(r.skel, "vertex"))),
    "rename": ("Skel",
               lambda t, p: p.rename("Top_Plate", name="Top_Face"),
               lambda r: AS.rename(r.skel, "Top_Face", "Top_Plate")),
    "unpublish": ("Skel",
                  lambda t, p: p.unpublish("Edge_Y"),
                  lambda r: AS.unpublish(r.skel, "Edge_Y")),
    "subscribe": ("PartA",
                  lambda t, p: p.subscribe(AS.find_item(t.skel, "Edge_Y")),
                  lambda r: AS.subscribe(r.parts["PartA"], AS.find_item(r.skel, "Edge_Y"))),
    "unsubscribe": ("PartB",
                    lambda t, p: p.unsubscribe(t.rig.binder("PartB", "Edge_Y").Name),
                    lambda r: AS.unsubscribe(r.binder("PartB", "Edge_Y"))),
    "freeze": ("PartB",
               lambda t, p: p.freeze(t.rig.binder("PartB", "Profile").Name),
               lambda r: AS.freeze(r.binder("PartB", "Profile"))),
    "unfreeze": ("PartA",
                 lambda t, p: p.unfreeze(t.rig.binder("PartA", "Mid_Plane").Name),
                 lambda r: AS.unfreeze(r.binder("PartA", "Mid_Plane"))),
}


class TestSkeletonCommands(GuiCase):
    """The two commands: registration, when they are active, what they stage."""

    def cmd(self, name):
        return Gui.Command.get(name)

    def test_commands_are_registered_with_icons_and_placed(self):
        Gui.activateWorkbench("AssemblyWorkbench")
        Gui.updateGui()
        for name in ("Assembly_PublishSkeletonGeometry", "Assembly_SubscribeSkeleton"):
            info = self.cmd(name).getInfo()
            for k in ("menuText", "toolTip", "statusTip", "pixmap"):
                self.assertTrue(info[k], "%s %s" % (name, k))
            self.assertTrue(os.path.isfile(info["pixmap"]), info["pixmap"])
            self.assertFalse(QtGui.QIcon(info["pixmap"]).isNull())
            self.assertEqual(self.cmd(name).getShortcut(), "")
            acts = [a for a in Gui.getMainWindow().findChildren(QtGui.QAction) if a.objectName() == name]
            self.assertTrue(acts, name)
            self.assertTrue(any(isinstance(w, QtWidgets.QMenu) for a in acts for w in a.associatedObjects()), name)
            self.assertTrue(any(isinstance(w, QtWidgets.QToolBar) for a in acts for w in a.associatedObjects()), name)

    def test_publish_is_active_only_with_geometry_selected(self):
        self.load()
        Gui.activateWorkbench("AssemblyWorkbench")
        cmd = self.cmd("Assembly_PublishSkeletonGeometry")
        self.assertFalse(cmd.isActive())
        Gui.Selection.addSelection(self.doc.Name, "Skel")                    # the part itself is not geometry
        self.assertFalse(cmd.isActive())
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, "Skel", "Pad.Face3")
        self.assertTrue(cmd.isActive())
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, "Sketch")
        self.assertTrue(cmd.isActive())
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, "Skel", "Pad.Edge1")
        self.open_panel(self.skel)
        self.assertFalse(cmd.isActive(), "active while another panel is open")

    def test_selection_through_a_link_and_a_sub_assembly_finds_the_owning_skeleton_and_element(self):
        self.load()
        link = self.doc.addObject("App::Link", "Lnk")
        link.LinkedObject = self.skel
        asm = self.doc.addObject("Assembly::AssemblyObject", "Asm")
        inner = asm.newObject("App::Link", "InAsm")
        inner.LinkedObject = self.skel
        self.doc.recompute()
        for obj, sub, want_target, want_sub in (
                ("Skel", "Pad.Face3", "Pad", "Face3"),
                ("Lnk", "Pad.Face3", "Pad", "Face3"),
                ("Asm", "InAsm.Pad.Face3", "Pad", "Face3"),
                ("Lnk", "Sketch.", "Sketch", ""),
                ("Asm", "InAsm.DPlane.", "DPlane", "")):
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(self.doc.Name, obj, sub)
            cands, why = SP.resolve_selection()
            self.assertEqual([(c.target.Name, c.sub, c.skeleton.Name) for c in cands],
                             [(want_target, want_sub, "Skel")], (obj, sub, why))
        # an element of the link itself (a face of the whole linked body) resolves to the linked body
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, "Lnk", "Face3")
        cands, why = SP.resolve_selection()
        self.assertEqual([(c.target.Name, c.skeleton.Name) for c in cands], [("Skel", "Skel")], why)

    def test_things_that_cannot_be_published_are_explained(self):
        self.load()
        for obj, sub in (("Skel", ""), ("Skel", "Pad.Wire1")):
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(self.doc.Name, obj, sub)
            cands, why = SP.resolve_selection()
            self.assertEqual(cands, [], (obj, sub))
            self.assertTrue(why and all(isinstance(w, str) and w for w in why), (obj, sub))

    def test_publish_opens_the_panel_with_the_publication_staged_and_changes_nothing(self):
        self.load()
        before = self.snap()
        Gui.Selection.addSelection(self.doc.Name, "Skel", "Pad.Face3")
        self.prompts.names = ["<default>"]
        Gui.runCommand("Assembly_PublishSkeletonGeometry")
        Gui.updateGui()
        panels = live_panels()
        self.assertEqual(len(panels), 1)
        panel = panels[0]
        self.assertEqual(self.prompts.asked, [("name", "Publish", "Pad_Face3")])
        (op,) = panel.ops
        pad = self.doc.getObject("Pad")
        self.assertEqual((op["op"], op["name"], op["sub"], op["itemkind"]), ("publish", "Pad_Face3", "Face3", "face"))
        self.assertEqual((op["target"]["uid"], op["target"]["id"], op["target"]["name"]), (self.doc.Uid, pad.ID, "Pad"))
        self.assertTrue(op["fingerprint"] and op["mapped"])
        self.assertEqual(self.snap(), before)
        panel.accept()
        Gui.Control.closeDialog()
        self.assertEqual(AS.find_item(self.skel, "Pad_Face3").Kind, "face")

    def test_subscribe_with_an_item_selected_stages_it_for_the_selected_part(self):
        self.load()
        before = self.snap()
        cmd = self.cmd("Assembly_SubscribeSkeleton")
        self.assertFalse(cmd.isActive(), "active with no part")
        Gui.Selection.addSelection(self.doc.Name, "PartA")
        self.assertTrue(cmd.isActive())
        Gui.Selection.addSelection(self.doc.Name, self.item("Edge_Y").Name)
        Gui.runCommand("Assembly_SubscribeSkeleton")
        Gui.updateGui()
        panels = live_panels()
        self.assertEqual(len(panels), 1)
        self.assertEqual(panels[0].part.Name, "PartA")
        self.assertEqual([o["op"] for o in panels[0].ops], ["subscribe"])
        self.assertEqual(self.snap(), before)
        panels[0].reject()
        Gui.Control.closeDialog()

    def test_subscribe_without_an_item_asks_which_one(self):
        self.load()
        Gui.Selection.addSelection(self.doc.Name, "PartB")
        self.prompts.items = [self.item("Top_Face")]
        Gui.runCommand("Assembly_SubscribeSkeleton")
        Gui.updateGui()
        panels = live_panels()
        self.assertEqual(self.prompts.asked[0][0], "item")
        self.assertIn("Top_Face", self.prompts.asked[0][1])
        self.assertEqual([o["op"] for o in panels[0].ops], ["subscribe"])
        panels[0].reject()
        Gui.Control.closeDialog()

    def test_running_a_command_twice_opens_one_panel(self):
        self.load()
        Gui.Selection.addSelection(self.doc.Name, "Skel", "Pad.Face3")
        self.prompts.names = ["a", "b"]
        Gui.runCommand("Assembly_PublishSkeletonGeometry")
        Gui.runCommand("Assembly_PublishSkeletonGeometry")
        Gui.updateGui()
        panels = live_panels()
        self.assertEqual(len(panels), 1)
        self.assertEqual(len(self.prompts.asked), 1)
        panels[0].reject()
        Gui.Control.closeDialog()


class TestSkeletonGui(GuiCase):
    """The Skeleton panel."""

    # ---- what it lists

    def test_a_skeleton_lists_its_items_with_kind_and_subscribers_with_state(self):
        self.load()
        p = self.open_panel(self.skel)
        items, _subs = p.rows()
        self.assertEqual([(r["name"], r["itemkind"]) for r in items],
                         [("Top_Face", "face"), ("Edge_Y", "edge"), ("Mid_Plane", "object"), ("Profile", "object")])
        got = {r["name"]: sorted((s["label"], s["state"]) for s in r["subs"]) for r in items}
        self.assertEqual(got, {"Top_Face": [("Top_Face", "following")], "Edge_Y": [("Edge_Y", "following")],
                               "Mid_Plane": [("Mid_Plane", "frozen")], "Profile": [("Profile", "following")]})
        self.assertNotIn("Spare", [r["name"] for r in items], "a withdrawn item is listed")
        self.assertEqual(p.items_tree.topLevelItemCount(), 4)
        self.assertEqual(p.items_tree.topLevelItem(0).childCount(), 1)

    def test_a_part_lists_its_subscriptions_with_state_and_reason(self):
        self.load()
        p = self.open_panel(self.rig.parts["PartB"])
        _items, subs = p.rows()
        self.assertEqual({s["item"]: s["state"] for s in subs}, {"Edge_Y": "following", "Profile": "following", "Spare": "broken"})
        broken = next(s for s in subs if s["item"] == "Spare")
        self.assertIn("withdrawn", broken["reason"])
        self.assertTrue(p.select_subscription(broken["object"]))
        self.assertIn("withdrawn", p.detail.text())
        self.close_panel()
        p = self.open_panel(self.rig.parts["PartA"])
        _items, subs = p.rows()
        self.assertEqual({s["item"]: s["state"] for s in subs}, {"Top_Face": "following", "Mid_Plane": "frozen"})

    def test_an_empty_part_says_so(self):
        self.load()
        empty = self.doc.addObject("PartDesign::Body", "Empty")
        p = self.open_panel(empty)
        self.assertEqual(p.rows(), ([], []))
        self.assertIn("publishes nothing", p.items_tree.topLevelItem(0).text(0))
        self.assertIn("no subscriptions", p.subs_tree.topLevelItem(0).text(0))
        self.assertFalse(p.buttons["rename"].isEnabled())
        self.assertFalse(p.buttons["goto"].isEnabled())

    def test_buttons_follow_the_selection(self):
        self.load()
        p = self.open_panel(self.skel)
        for b in ("rename", "unpublish", "unsubscribe", "freeze", "unfreeze", "goto"):
            self.assertFalse(p.buttons[b].isEnabled(), b)
        self.assertTrue(p.buttons["publish"].isEnabled())
        p.select_item("Top_Face")
        self.assertTrue(p.buttons["rename"].isEnabled() and p.buttons["unpublish"].isEnabled() and p.buttons["goto"].isEnabled())
        self.assertFalse(p.buttons["freeze"].isEnabled())
        p.select_subscriber("Mid_Plane")
        self.assertTrue(p.buttons["unfreeze"].isEnabled())
        self.assertFalse(p.buttons["freeze"].isEnabled())
        self.assertFalse(p.buttons["rename"].isEnabled())
        p.select_subscriber("Profile")
        self.assertTrue(p.buttons["freeze"].isEnabled())
        self.assertFalse(p.buttons["unfreeze"].isEnabled())

    # ---- every action equals its API twin; each is one transaction

    def test_each_action_equals_the_api_twin(self):
        for kind, (part, stage, api) in ACTIONS.items():
            with self.subTest(action=kind):
                self.prompts.confirms = [True]
                self.load()
                p = self.open_panel(self.doc.getObject(part))
                self.assertTrue(stage(self, p), kind)
                self.assertEqual(len(p.ops), 1)
                self.close_panel(accept=True)
                self.assertEqual(p.problems, [])
                want = self.twin(api)
                got = self.mine()
                self.assertEqual(self.differences(got[0], want[0]), [], kind)
                self.assertEqual(got[1], want[1], kind)
                self.reset()

    def test_ok_makes_one_undo_step_per_action_and_undo_restores_exactly(self):
        self.load()
        before = F.normalized(self.doc)
        p = self.open_panel(self.skel)
        self.prompts.confirms = [True]
        p.publish(name="Corner", candidates=[self.candidate("vertex")])
        p.rename("Top_Plate", name="Top_Face")
        p.unpublish("Edge_Y")
        n = self.doc.UndoCount
        self.close_panel(accept=True)
        self.assertEqual(self.doc.UndoCount - n, 3)
        for _ in range(3):
            self.doc.undo()
        self.assertEqual(F.normalized(self.doc), before)

    # ---- Cancel changes nothing

    def test_cancel_after_each_action_changes_nothing(self):
        for kind, (part, stage, _api) in ACTIONS.items():
            with self.subTest(action=kind):
                self.prompts.confirms = [True]
                self.load()
                before = self.snap()
                p = self.open_panel(self.doc.getObject(part))
                self.assertTrue(stage(self, p), kind)
                self.assertEqual(len(p.ops), 1, kind)
                self.assertEqual(self.snap(), before, "staging %s touched a document" % kind)
                self.close_panel(accept=False)
                self.assertEqual(self.snap(), before, kind)
                self.assertEqual(p.ops, [])
                self.reset()

    def test_cancel_after_every_action_together_changes_nothing(self):
        self.load()
        before = self.snap()
        self.prompts.confirms = [True, True]
        p = self.open_panel(self.skel)
        for kind in ("publish", "rename", "unpublish"):
            ACTIONS[kind][1](self, p)
        self.assertEqual(len(p.ops), 3)
        self.close_panel()
        p = self.open_panel(self.rig.parts["PartB"])
        for kind in ("unsubscribe", "freeze"):
            ACTIONS[kind][1](self, p)
        self.assertEqual(len(p.ops), 2)
        self.close_panel()
        self.assertEqual(self.snap(), before)

    def test_closing_the_dialog_without_a_button_changes_nothing(self):
        self.load()
        before = self.snap()
        p = self.open_panel(self.skel)
        p.publish(name="Corner", candidates=[self.candidate("vertex")])
        Gui.Control.closeDialog()
        Gui.updateGui()
        self.assertEqual(self.snap(), before)
        self.assertEqual(p.ops, [])

    def test_cancel_across_documents_and_with_a_go_to_changes_no_document(self):
        w = K.World(xdoc=True, tag="X")
        try:
            w.both("face", "edge")
            AS.freeze(w.binders["edge"])
            w.recompute()
            w.save()
            self.activate(w.doc)
            before = self.snap()
            self.panel = p = self.open_panel(w.holder)
            p.select_subscription("S_face")
            self.assertEqual(p.goto()["went"], "source")
            p.unsubscribe("S_face", assume_yes=True)
            p.unfreeze("S_edge")
            p.subscribe(AS.find_item(w.body, "face"))
            self.assertEqual(len(p.ops), 3)
            self.close_panel(accept=False)
            self.assertEqual(self.snap(), before)
        finally:
            w.close()

    def test_an_unrelated_edit_made_while_the_panel_is_open_survives_cancel_and_ok(self):
        for accept in (False, True):
            with self.subTest(accept=accept):
                self.prompts.confirms = [True]
                self.load()
                p = self.open_panel(self.skel)
                ACTIONS["publish"][1](self, p)
                ACTIONS["rename"][1](self, p)
                box = self.doc.addObject("Part::Box", "UserBox")          # the user's own edits, none of them the panel's
                box.Length = 33.0
                self.rig.parts["PartA"].Label = "Renamed by the user"
                self.doc.recompute()
                mid = self.snap()
                self.close_panel(accept=accept)
                self.assertEqual(self.doc.getObject("UserBox").Length.Value, 33.0)
                self.assertEqual(self.rig.parts["PartA"].Label, "Renamed by the user")
                if not accept:
                    self.assertEqual(self.snap(), mid)
                else:
                    self.assertEqual(p.problems, [])
                    self.assertEqual(AS.find_item(self.skel, "Top_Plate").Kind, "face")
                    self.assertEqual(AS.find_item(self.skel, "Corner").Kind, "vertex")
                self.reset()

    def test_ok_reports_an_action_the_document_no_longer_allows_and_does_the_rest(self):
        self.load()
        p = self.open_panel(self.skel)
        p.publish(name="Corner", candidates=[self.candidate("vertex")])
        sketch = self.doc.getObject("Sketch")
        p.publish(name="Prof2", candidates=[SP.Candidate(sketch, "", self.skel)])
        p.rename("Top_Plate", name="Top_Face")
        self.doc.removeObject("Sketch")                         # the user deletes the target of the second staged publish
        self.close_panel(accept=True)
        self.assertEqual(len(p.problems), 1, p.problems)
        self.assertIn("Prof2", p.problems[0])
        self.assertEqual(self.prompts.reported, [p.problems])
        self.assertEqual(AS.find_item(self.skel, "Corner").Kind, "vertex")
        self.assertEqual(AS.find_item(self.skel, "Top_Plate").Kind, "face")

    # ---- names

    def test_names_are_checked_when_they_are_staged(self):
        self.load()
        p = self.open_panel(self.skel)
        self.doc.addObject("Part::Feature", "Clash").Label = AS.item_label(self.skel, "Taken")
        for what, name in (("empty", ""), ("blank", "   "), ("duplicate", "Edge_Y"), ("duplicate with spaces", "  Edge_Y "),
                           ("label FreeCAD would suffix", "Taken"), ("control character", "a\tb")):
            self.assertEqual(p.publish(name=name, candidates=[self.candidate("vertex")]), 0, what)
            self.assertTrue(p.error.text(), what)
            self.assertEqual(p.ops, [], what)
            self.assertFalse(p.rename(name, name="Top_Face"), what)
            self.assertEqual(p.ops, [], what)
        # a name staged once cannot be staged twice, and a rename onto a staged name is refused
        self.assertEqual(p.publish(name="Corner", candidates=[self.candidate("vertex")]), 1)
        self.assertEqual(p.publish(name="Corner", candidates=[self.candidate("face")]), 0)
        self.assertIn("staged", p.error.text())
        self.assertFalse(p.rename("Corner", name="Top_Face"))
        self.assertEqual(len(p.ops), 1)

    def test_the_default_name_comes_from_the_element(self):
        self.load()
        p = self.open_panel(self.skel)
        Gui.Selection.addSelection(self.doc.Name, "Skel", "Pad.Face3")
        self.prompts.names = ["<default>"]
        self.assertEqual(p.publish(), 1)
        self.assertEqual(self.prompts.asked, [("name", "Publish", "Pad_Face3")])
        self.assertEqual(p.ops[0]["name"], "Pad_Face3")
        self.prompts.names = ["<default>"]
        self.assertEqual(p.publish(), 1)                      # the same element again: the proposal is made unique
        self.assertEqual(self.prompts.asked[-1][2], "Pad_Face3_2")

    def test_a_rename_to_a_refused_name_is_not_staged_and_a_rename_of_a_staged_item_edits_it(self):
        self.load()
        p = self.open_panel(self.skel)
        self.prompts.names = ["Profile", "  "]
        p.select_item("Top_Face")
        self.assertFalse(p.rename())
        self.assertFalse(p.rename())
        self.assertEqual(p.ops, [])
        p.publish(name="Corner", candidates=[self.candidate("vertex")])
        self.assertTrue(p.rename("Corner2", name="Corner"))
        self.assertEqual([o["name"] for o in p.ops], ["Corner2"])
        self.assertTrue(p.rename("Top_Plate", name="Top_Face"))
        self.assertTrue(p.rename("Top_Plate2", name="Top_Plate"))
        self.assertEqual([o["op"] for o in p.ops], ["publish", "rename"])
        self.assertEqual({k: v for k, v in p.ops[1].items() if k != "op_id"}, {"op": "rename", "name": "Top_Face", "new": "Top_Plate2"})
        items, _ = p.rows()
        self.assertIn("Top_Plate2", [r["name"] for r in items])

    # ---- confirmations go through the hook

    def test_unpublish_with_subscribers_and_unsubscribe_ask_and_a_refusal_stages_nothing(self):
        self.load()
        p = self.open_panel(self.skel)
        self.prompts.confirms = [False]
        self.assertFalse(p.unpublish("Edge_Y"))
        self.assertEqual(p.ops, [])
        self.assertEqual(self.prompts.asked, [("confirm", "Unpublish")])
        self.close_panel()
        p = self.open_panel(self.rig.parts["PartB"])
        self.prompts.confirms = [False]
        self.assertFalse(p.unsubscribe(self.rig.binder("PartB", "Profile").Name))
        self.assertEqual(p.ops, [])
        self.assertEqual(self.prompts.asked[-1], ("confirm", "Unsubscribe"))
        self.prompts.confirms = [True]
        self.assertTrue(p.unsubscribe(self.rig.binder("PartB", "Profile").Name))
        self.assertEqual([o["op"] for o in p.ops], ["unsubscribe"])

    def test_unpublish_of_an_item_nobody_follows_is_not_asked_about(self):
        self.load()
        AS.publish(self.skel, "Lonely", *K.target_of(self.skel, "vertex"))
        p = self.open_panel(self.skel)
        self.assertTrue(p.unpublish("Lonely"))
        self.assertEqual(self.prompts.asked, [])

    def test_the_lists_show_the_document_as_it_will_be_after_ok(self):
        self.load()
        self.prompts.confirms = [True]
        p = self.open_panel(self.skel)
        p.publish(name="Corner", candidates=[self.candidate("vertex")])
        p.rename("Top_Plate", name="Top_Face")
        p.unpublish("Edge_Y")
        items, _ = p.rows()
        by = {r["name"]: r for r in items}
        self.assertEqual(by["Corner"]["pending"], "publish")
        self.assertEqual(by["Top_Plate"]["pending"], "rename")
        self.assertEqual(by["Top_Plate"]["old_name"], "Top_Face")
        self.assertEqual(by["Edge_Y"]["pending"], "withdraw")
        self.assertEqual([s["pending"] for s in by["Edge_Y"]["subs"]], ["will break"])
        self.assertNotIn("Top_Face", by)
        p.select_item("Edge_Y")
        self.assertFalse(p.buttons["rename"].isEnabled() or p.buttons["unpublish"].isEnabled())

    def test_freeze_then_unfreeze_cancel_out_and_a_staged_subscribe_can_be_taken_back(self):
        self.load()
        p = self.open_panel(self.rig.parts["PartB"])
        name = self.rig.binder("PartB", "Profile").Name
        self.assertTrue(p.freeze(name))
        self.assertEqual([o["op"] for o in p.ops], ["freeze"])
        self.assertTrue(p.unfreeze(name))
        self.assertEqual(p.ops, [])
        self.close_panel()
        p = self.open_panel(self.rig.parts["PartA"])
        self.assertTrue(p.subscribe(AS.find_item(self.skel, "Profile")))
        self.assertFalse(p.subscribe(AS.find_item(self.skel, "Profile")), "the same subscription staged twice")
        _i, subs = p.rows()
        staged = next(s for s in subs if s["object"] is None)
        self.assertEqual(staged["pending"], "subscribe")
        p.select_subscription("Profile")
        self.prompts.confirms = [True]
        self.assertTrue(p.unsubscribe())
        self.assertEqual(p.ops, [])

    def test_a_subscription_that_would_make_a_cycle_or_has_nothing_to_follow_is_refused(self):
        self.load()
        p = self.open_panel(self.skel)
        self.assertFalse(p.subscribe(AS.find_item(self.skel, "Top_Face")))
        self.assertIn("cycle", p.error.text())
        self.close_panel()
        p = self.open_panel(self.rig.parts["PartA"])
        spare = AS.find_item(self.skel, "Spare", include_withdrawn=True)
        self.assertFalse(p.subscribe(spare))
        self.assertIn("not a published item", p.error.text())

    # ---- go to

    def test_go_to_an_item_selects_its_source(self):
        self.load()
        p = self.open_panel(self.skel)
        p.select_item("Mid_Plane")
        r = p.goto()
        self.assertEqual(r["went"], "source")
        self.assertEqual([o.Name for o in Gui.Selection.getSelection(self.doc.Name)], ["DPlane"])
        p.select_item("Top_Face")
        p.goto()
        ex = Gui.Selection.getSelectionEx(self.doc.Name, 0)
        self.assertEqual(len(ex), 1)
        self.assertTrue(ex[0].SubElementNames[0].endswith(AS.describe_item(self.item("Top_Face"))["source"]["sub"]))
        self.assertTrue(Gui.Control.activeDialog())

    def test_go_to_a_broken_subscription_selects_the_source_of_its_withdrawn_item(self):
        self.load()
        p = self.open_panel(self.rig.parts["PartB"])
        broken = self.rig.binder("PartB", "Spare")
        self.assertTrue(p.select_subscription(broken.Name))
        self.assertEqual(AS.describe_subscription(broken)["state"], "broken")
        self.assertTrue(p.buttons["goto"].isEnabled())
        r = p.goto()
        self.assertEqual(r["went"], "source")
        self.assertEqual([o.Name for o in Gui.Selection.getSelection(self.doc.Name)], ["DPoint"])

    def test_go_to_when_the_item_object_is_gone_selects_the_skeleton_and_says_so(self):
        self.load()
        p = self.open_panel(self.rig.parts["PartB"])
        self.doc.removeObject(AS.find_item(self.skel, "Spare", include_withdrawn=True).Name)
        self.doc.recompute()
        broken = self.rig.binder("PartB", "Spare")
        self.assertEqual(AS.describe_subscription(broken)["state"], "broken")
        p.select_subscription(broken.Name)
        r = p.goto()
        self.assertEqual(r["went"], "skeleton")
        self.assertEqual([o.Name for o in Gui.Selection.getSelection(self.doc.Name)], ["Skel"])
        self.assertIn("gone", r["message"])

    def test_cross_document_unloaded_broken_and_go_to(self):
        w = K.World(xdoc=True, tag="G")
        try:
            w.both("face", "sketch", "plane")
            AS.freeze(w.binders["sketch"])
            AS.unpublish(w.body, "plane")
            w.recompute()
            w.save()
            self.activate(w.doc)
            # broken in another document: go to selects the withdrawn item's source in the skeleton's document and activates it
            p = self.open_panel(w.holder)
            _i, subs = p.rows()
            self.assertEqual({s["item"]: s["state"] for s in subs}, {"face": "following", "sketch": "frozen", "plane": "broken"})
            p.select_subscription("S_plane")
            self.assertEqual(Gui.ActiveDocument.Document.Name, w.doc.Name)
            r = p.goto()
            self.assertEqual(r["went"], "source")
            self.assertEqual(Gui.ActiveDocument.Document.Name, w.sdoc.Name)
            self.assertEqual([(o.Document.Name, o.Name) for o in Gui.Selection.getSelection(w.sdoc.Name)],
                             [(w.sdoc.Name, "DPlane")])
            # FreeCAD shows a task dialog only while its own document is the active one; the panel is still open and
            # comes back with that document
            self.assertFalse(p.done)
            self.activate(w.doc)
            self.assertTrue(Gui.Control.activeDialog(), "the panel is gone after returning to its document")
            self.close_panel()
            # unloaded: the skeleton's document is closed
            App.closeDocument(w.sdoc.Name)
            self.activate(w.doc)
            p = self.open_panel(w.holder)
            _i, subs = p.rows()
            states = {s["item"]: s["state"] for s in subs}
            self.assertEqual(states["face"], "unloaded", states)
            self.assertEqual(states["sketch"], "frozen")
            row = next(s for s in subs if s["item"] == "face")
            self.assertIn("not open", row["reason"])
            p.select_subscription("S_face")
            before_docs = sorted(App.listDocuments())
            r = p.goto()
            self.assertIsNone(r["went"])
            self.assertIn("not open", r["message"])
            self.assertIn("not open", p.error.text())
            self.assertEqual(sorted(App.listDocuments()), before_docs, "go to opened a document")
        finally:
            w.close()

    def test_go_to_for_a_new_item_says_so(self):
        self.load()
        p = self.open_panel(self.skel)
        p.publish(name="Corner", candidates=[self.candidate("vertex")])
        p.select_item("Corner")
        self.assertIsNone(p.goto()["went"])
        self.assertIn("not made yet", p.error.text())

    # ---- the rest

    def test_the_list_follows_changes_made_while_it_is_open(self):
        self.load()
        p = self.open_panel(self.skel)
        AS.publish(self.skel, "Late", *K.target_of(self.skel, "vertex"))
        self.settle()
        self.assertIn("Late", [r["name"] for r in p.rows()[0]])
        self.assertEqual(p.items_tree.topLevelItemCount(), 5)
        AS.unpublish(self.skel, "Late")
        self.settle()
        self.assertEqual(p.items_tree.topLevelItemCount(), 4)

    def test_a_second_panel_is_refused(self):
        self.load()
        self.open_panel(self.skel)
        with self.assertRaises(RuntimeError):
            SP.TaskSkeleton(self.rig.parts["PartA"])

    def test_the_panel_buttons_have_tooltips_and_accessible_names(self):
        self.load()
        p = self.open_panel(self.skel)
        for key, b in p.buttons.items():
            self.assertTrue(b.toolTip(), key)
        self.assertTrue(p.items_tree.accessibleName() and p.subs_tree.accessibleName())


def _save_skeleton_file(path, first):
    """A saved skeleton document (named after the file) that publishes the top face and the corner, ``first`` listed first."""
    d = App.newDocument("Skel")
    body = K.build_skeleton(d, "Skel")
    keys = ("face", "vertex") if first == "face" else ("vertex", "face")
    for k in keys:
        AS.publish(body, {"face": "Top", "vertex": "Corner"}[k], *K.target_of(body, k))
    d.saveAs(path)
    App.closeDocument(d.Name)


class TestSkeletonStagedIdentity(GuiCase):
    """A staged action names a thing by a stable identity (document Uid, object ID, geometric fingerprint). OK re-checks it
    and reports and skips an action whose thing is gone or has become another one; it never applies to a different one."""

    def test_a_staged_publish_is_refused_when_an_undo_renumbers_the_faces(self):
        doc = App.newDocument("StaleH1")
        skel = K.build_skeleton(doc, "Skel")
        doc.openTransaction("hole")
        K.insert_hole(doc)
        doc.commitTransaction()
        self.activate(doc)
        tgt, sub = K.target_of(skel, "face")
        p = self.open_panel(skel)
        p.publish(name="Top", candidates=[SP.Candidate(tgt, sub, skel)])
        doc.undo()                       # the hole goes; the same index name now belongs to the bottom face
        doc.recompute()
        self.close_panel(accept=True)
        self.assertEqual(len(p.problems), 1, p.problems)
        self.assertIn("Top", p.problems[0])
        self.assertEqual([i.ItemName for i in AS.items(skel)], [])

    def test_a_staged_publish_still_goes_through_when_the_element_comes_back(self):
        doc = App.newDocument("StaleH1b")
        skel = K.build_skeleton(doc, "Skel")
        doc.openTransaction("hole")
        K.insert_hole(doc)
        doc.commitTransaction()
        self.activate(doc)
        tgt, sub = K.target_of(skel, "face")
        want = AS.shape_digest(tgt.Shape.getElement(sub))
        p = self.open_panel(skel)
        p.publish(name="Top", candidates=[SP.Candidate(tgt, sub, skel)])
        doc.undo()
        doc.recompute()
        doc.redo()
        doc.recompute()
        self.close_panel(accept=True)
        self.assertEqual(p.problems, [])
        self.assertEqual(AS.shape_digest(AS.find_item(skel, "Top").Shape), want)

    def test_a_staged_subscribe_is_refused_when_the_document_is_swapped_for_another_under_the_same_name(self):
        root = os.path.realpath(K.tmpdir())
        try:
            os.makedirs(os.path.join(root, "v1"))
            os.makedirs(os.path.join(root, "v2"))
            _save_skeleton_file(os.path.join(root, "v1", "Skel.FCStd"), "face")
            _save_skeleton_file(os.path.join(root, "v2", "Skel.FCStd"), "vertex")
            asm = App.newDocument("AsmH2")
            part = asm.addObject("PartDesign::Body", "PartA")
            asm.saveAs(os.path.join(root, "Asm.FCStd"))
            v1 = App.openDocument(os.path.join(root, "v1", "Skel.FCStd"))
            self.activate(asm)
            p = self.open_panel(part)
            self.assertTrue(p.subscribe(AS.find_item(v1.getObject("Skel"), "Top")))
            App.closeDocument(v1.Name)
            App.openDocument(os.path.join(root, "v2", "Skel.FCStd"))
            self.activate(asm)
            self.close_panel(accept=True)
            self.assertEqual(len(p.problems), 1, p.problems)
            self.assertEqual(AS.subscriptions(asm), [])
        finally:
            for n in list(App.listDocuments()):
                App.closeDocument(n)
            shutil.rmtree(root, ignore_errors=True)

    def test_a_staged_subscribe_is_refused_when_the_item_object_name_is_reused_by_another_item(self):
        doc = App.newDocument("StaleH2b")
        skel = K.build_skeleton(doc, "Skel")
        part = doc.addObject("PartDesign::Body", "PartA")
        AS.publish(skel, "Top", *K.target_of(skel, "face"))
        x = AS.find_item(skel, "Top")
        self.activate(doc)
        p = self.open_panel(part)
        p.subscribe(x)
        doc.undo()                       # the publication is undone, its object name is free again
        AS.publish(skel, "Corner", *K.target_of(skel, "vertex"))
        self.close_panel(accept=True)
        self.assertEqual(len(p.problems), 1, p.problems)
        self.assertEqual(AS.subscriptions(doc), [])

    def _two_skeleton_documents(self):
        root = os.path.realpath(K.tmpdir())
        self.addCleanup(shutil.rmtree, root, True)
        docs = []
        for n, key in (("SkA", "face"), ("SkB", "vertex")):
            d = App.newDocument(n)
            b = K.build_skeleton(d, "Skel")
            AS.publish(b, "Top", *K.target_of(b, key))
            d.saveAs(os.path.join(root, n + ".FCStd"))
            docs.append(d)
        hold = App.newDocument("Hold")
        part = hold.addObject("PartDesign::Body", "PartH")
        hold.saveAs(os.path.join(root, "Hold.FCStd"))
        self.activate(hold)
        return root, docs, hold, part

    def test_unsubscribing_a_staged_subscription_removes_that_one_when_two_items_share_a_label(self):
        _root, docs, _hold, part = self._two_skeleton_documents()
        p = self.open_panel(part)
        p.subscribe(AS.find_item(docs[0].getObject("Skel"), "Top"))
        p.subscribe(AS.find_item(docs[1].getObject("Skel"), "Top"))
        self.assertEqual(len(p.ops), 2)
        p.subs_tree.setCurrentItem(p.subs_tree.topLevelItem(1))
        p.unsubscribe(assume_yes=True)
        self.assertEqual([r["item_object"].Document.Name for r in p.rows()[1] if r.get("staged")], [docs[0].Name])
        self.close_panel()

    def test_freeze_and_unfreeze_of_same_named_subscribers_in_two_documents_do_not_cancel_each_other(self):
        root = os.path.realpath(K.tmpdir())
        self.addCleanup(shutil.rmtree, root, True)
        s = App.newDocument("SkelD")
        body = K.build_skeleton(s, "Skel")
        AS.publish(body, "Top", *K.target_of(body, "face"))
        pa = s.addObject("PartDesign::Body", "PartA")
        s.saveAs(os.path.join(root, "SkelD.FCStd"))
        AS.subscribe(pa, AS.find_item(body, "Top"), object_name="Sub")
        o = App.newDocument("Other")
        o.saveAs(os.path.join(root, "Other.FCStd"))
        pb = o.addObject("PartDesign::Body", "PartB")
        r = AS.subscribe(pb, AS.find_item(body, "Top"), object_name="Sub")
        AS.freeze(o.getObject(r["object"]))
        self.activate(s)
        p = self.open_panel(body)

        def pick(docname):
            for i in range(p.items_tree.topLevelItemCount()):
                top = p.items_tree.topLevelItem(i)
                for j in range(top.childCount()):
                    if top.child(j).data(0, SP.ROLE_ROW)["document"] == docname:
                        p.items_tree.setCurrentItem(top.child(j))
                        return
            self.fail("no subscriber row in " + docname)
        pick("SkelD")
        self.assertTrue(p.freeze())
        pick("Other")
        self.assertTrue(p.unfreeze())
        self.assertEqual([x["op"] for x in p.ops], ["freeze", "unfreeze"])
        self.close_panel(accept=True)
        self.assertEqual(p.problems, [])
        self.assertTrue(AS.describe_subscription(s.getObject("Sub"))["frozen"])
        self.assertFalse(AS.describe_subscription(o.getObject("Sub"))["frozen"])

    def test_a_second_panel_is_refused_after_a_go_to_hides_the_first(self):
        import CommandSkeleton as CS
        w = K.World(xdoc=True, tag="M2")
        try:
            w.both("face")
            w.save()
            self.activate(w.doc)
            p = self.open_panel(w.holder)
            p.select_subscription("S_face")
            self.assertEqual(p.goto()["went"], "source")
            self.assertEqual(Gui.ActiveDocument.Document.Name, w.sdoc.Name)     # the first panel is hidden now
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(w.sdoc.Name, "Skel", "Pad.Face3")
            self.assertFalse(Gui.Command.get("Assembly_PublishSkeletonGeometry").isActive())
            self.assertIsNone(CS.open_panel(w.body))
            with self.assertRaises(RuntimeError):
                SP.TaskSkeleton(w.body)
            self.activate(w.doc)
            self.close_panel()
            self.assertFalse(SP.panel_open())
            second = CS.open_panel(w.body)                  # released on Cancel
            self.assertIsNotNone(second)
            second.reject()
            Gui.Control.closeDialog()
            self.assertFalse(SP.panel_open())
        finally:
            w.close()

    def test_the_open_panel_is_released_on_ok_and_when_its_document_is_closed(self):
        self.load()
        self.open_panel(self.skel)
        self.assertTrue(SP.panel_open())
        self.close_panel(accept=True)
        self.assertFalse(SP.panel_open())
        self.open_panel(self.skel)
        self.assertTrue(SP.panel_open())
        App.closeDocument(self.doc.Name)
        self.settle()
        self.assertFalse(SP.panel_open())

    def test_a_name_freed_by_a_staged_rename_or_withdrawal_can_be_used_again_before_ok(self):
        self.load()
        p = self.open_panel(self.skel)
        self.assertTrue(p.rename("Top_Plate", name="Top_Face"))
        self.assertEqual(p.publish(name="Top_Face", candidates=[self.candidate("vertex")]), 1)
        self.assertTrue(p.unpublish("Edge_Y", assume_yes=True))
        self.assertEqual(p.publish(name="Edge_Y", candidates=[self.candidate("face")]), 1)
        self.assertEqual(p.publish(name="Mid_Plane", candidates=[self.candidate("edge")]), 0)      # still taken
        self.close_panel(accept=True)
        self.assertEqual(p.problems, [])
        self.assertEqual(AS.find_item(self.skel, "Top_Plate").Kind, "face")
        self.assertEqual(AS.find_item(self.skel, "Top_Face").Kind, "vertex")
        self.assertEqual(AS.find_item(self.skel, "Edge_Y").Kind, "face")
