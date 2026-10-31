# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run 023: a close refused by App::Application::closeRefusal is vetoed in the GUI before any
prompt, so the window stays with its document (needs FreeCADGui; mini only)."""

import unittest

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtWidgets


def pump(n=4):
    for _ in range(n):
        Gui.updateGui()


class Closer:
    """FeaturePython proxy whose recompute tries to close the document's window."""

    def __init__(self, obj):
        obj.Proxy = self
        self.armed = False
        self.widget = None
        self.closed = []
        self.shown = []

    def execute(self, obj):
        if self.armed:
            self.armed = False
            self.closed.append(self.widget.close())
            # read at once: later GUI signals of the recompute overwrite the status message
            self.shown = [lab.text() for lab in Gui.getMainWindow().statusBar().findChildren(QtWidgets.QLabel)]

    def dumps(self):
        return None

    def loads(self, state):
        pass


class TestGuiCloseVeto(unittest.TestCase):
    def test_window_stays_while_a_recompute_is_running_then_closes(self):
        doc = App.newDocument("CloseVeto")
        self.addCleanup(lambda: "CloseVeto" in App.listDocuments() and App.closeDocument("CloseVeto"))
        obj = doc.addObject("App::FeaturePython", "Closer")
        proxy = Closer(obj)
        doc.recompute()
        pump()
        gdoc = Gui.getDocument("CloseVeto")
        view = gdoc.ActiveView
        self.assertIsNotNone(view)
        widgets = [w for w in Gui.getMainWindow().findChildren(QtWidgets.QMdiSubWindow)]
        self.assertTrue(widgets, "no MDI sub-window for the document")
        proxy.widget = widgets[-1].widget()
        gdoc.Modified = False          # an unmodified document: a close that is not vetoed would not prompt

        # Closing the window from inside a recompute of the same document is refused.
        proxy.armed = True
        obj.touch()
        doc.recompute()
        pump()
        self.assertEqual(proxy.closed, [False], "the window close was not vetoed during the recompute")
        self.assertIn("CloseVeto", App.listDocuments())
        self.assertIsNotNone(Gui.getDocument("CloseVeto").ActiveView, "the document lost its window")
        self.assertTrue(any("Cannot close document" in t for t in proxy.shown), proxy.shown)

        # Once the operation is over the veto is gone: the same close goes through.
        gdoc.Modified = False
        self.assertTrue(proxy.widget.close())
        pump()
        App.closeDocument("CloseVeto")
        self.assertNotIn("CloseVeto", App.listDocuments())
