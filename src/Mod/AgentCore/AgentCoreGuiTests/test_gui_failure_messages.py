# SPDX-License-Identifier: LGPL-2.1-or-later
"""G08a slice 1: the tree tooltip of a failed feature is the readable sentence (needs FreeCADGui).

Builds failing features from the workshop corpus (fixtures/messages/cases.py), recomputes them in
the GUI session and reads the tooltip of each feature's tree item (DocumentObjectItem::data with
Qt::ToolTipRole). The stored status string must stay the raw text.
"""

import importlib.util
import json
import os
import re
import time
import unittest

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

CTL = os.environ.get("CADFORK_CTL") or os.path.join(
    os.path.expanduser("~"), "_agents", "repos", "app development", "cad-fork"
)
CORPUS = os.path.join(CTL, "fixtures", "messages")
TRACEBACK = re.compile(r"Traceback|File \".*\", line \d+")
INTERNAL = re.compile(r"BRep\w*_\w+|Standard_\w+|NCollection_\w+|gp_\w+|command not done|\.cpp\(\d+\)|<class '")


def pump(n=4):
    for _ in range(n):
        Gui.updateGui()


def load_cases():
    spec = importlib.util.spec_from_file_location("g08a_cases_gui", os.path.join(CORPUS, "cases.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.CASES


def tree_item(label, wait=3.0):
    """The tree item named label; the tree fills on a timer, so poll up to `wait` seconds."""
    deadline = time.monotonic() + wait
    while True:
        for tree in Gui.getMainWindow().findChildren(QtWidgets.QTreeWidget):
            found = tree.findItems(label, QtCore.Qt.MatchExactly | QtCore.Qt.MatchRecursive, 0)
            if found:
                return found[0]
        if time.monotonic() > deadline:
            return None
        QtWidgets.QApplication.processEvents()
        time.sleep(0.05)


# one case per catalogue family plus a Python feature and an unknown error
GUI_CASES = [
    "part_fillet_radius_too_big", "pd_pad_open_sketch", "sk_over_constrained", "py_value_error",
    "py_attribute_error", "weld_empty_path", "sm_hem_wrong_edge", "expr_division_by_zero",
    "draft_circle_zero_radius", "surface_filling_no_edges",
]


class TestGuiFailureTooltip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(CORPUS, "manifest.json")) as fh:
            cls.manifest = json.load(fh)["cases"]
        cls.cases = load_cases()

    def test_tooltip_is_the_readable_sentence(self):
        for cid in GUI_CASES:
            with self.subTest(case=cid):
                if "g08a" in App.listDocuments():
                    App.closeDocument("g08a")
                doc = App.newDocument("g08a")  # the corpus statuses name the document 'g08a'
                self.addCleanup(lambda: "g08a" in App.listDocuments() and App.closeDocument("g08a"))
                module, fn = self.cases[cid]
                obj = fn(doc)
                doc.recompute()
                pump()
                want = self.manifest[cid]
                self.assertEqual(obj.getStatusString(), want["stored_status"], "stored text changed")
                item = tree_item(want["label"])
                self.assertIsNotNone(item, "no tree item for %r" % want["label"])
                tip = item.toolTip(0)
                self.assertIn("'%s'" % want["label"], tip)
                self.assertIsNone(TRACEBACK.search(tip), tip)
                self.assertIsNone(INTERNAL.search(tip), tip)
                self.assertTrue(tip.endswith("."), tip)
                App.closeDocument(doc.Name)
                pump()

    def test_working_feature_keeps_its_tooltip(self):
        doc = App.newDocument("G08aGuiOK")
        self.addCleanup(lambda: "G08aGuiOK" in App.listDocuments() and App.closeDocument("G08aGuiOK"))
        box = doc.addObject("Part::Box", "Box")
        box.Label = "Fine box"
        doc.recompute()
        pump()
        item = tree_item("Fine box")
        self.assertIsNotNone(item)
        self.assertNotIn("could not", item.toolTip(0))
