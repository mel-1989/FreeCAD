# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI tests for the shared tree icon with visibility badge (G05 slice 2): items in the same state
share one icon, every real state change gives a different icon, and the pixels are the badge
followed by the source icon."""

import time
import unittest

import FreeCAD
import FreeCADGui
from PySide import QtCore, QtGui, QtWidgets


def _items(label):
    out = []
    for tree in FreeCADGui.getMainWindow().findChildren(QtWidgets.QTreeWidget):
        it = QtWidgets.QTreeWidgetItemIterator(tree)
        while it.value():
            if it.value().text(0) == label:
                out.append(it.value())
            it += 1
    return out


def _item(label):
    # the tree fills and refreshes its items from queued events
    for _ in range(40):
        QtWidgets.QApplication.processEvents()
        found = _items(label)
        if found:
            return found[0]
        time.sleep(0.05)
    return None


def _tree_of(item):
    return item.treeWidget()


def _split(item, img):
    """Width of one half (the source icon) and the layout spacing between badge and icon."""
    spacing = _tree_of(item).style().pixelMetric(QtWidgets.QStyle.PM_LayoutHorizontalSpacing)
    return (img.width() - spacing) // 2, spacing


def _wait_image(label, other, state=QtGui.QIcon.Off):
    """The item's image once it differs from `other` (the tree refreshes icons from queued events)."""
    for _ in range(40):
        QtWidgets.QApplication.processEvents()
        img = _image(_items(label)[0], state)
        if img != other:
            return img
        time.sleep(0.05)
    return img


def _image(item, state=QtGui.QIcon.Off):
    return item.icon(0).pixmap(0xFFFF, 0xFFFF, QtGui.QIcon.Normal, state).toImage()


class VisibilityBadgeIconTest(unittest.TestCase):
    def setUp(self):
        # the badge is a preference (default on); the test runs with its own FREECAD_USER_HOME
        self.pref = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/TreeView")
        self.old_pref = self.pref.GetBool("VisibilityIcon", True)
        self.pref.SetBool("VisibilityIcon", True)
        self.doc = FreeCAD.newDocument("VisibilityBadgeIconTest")
        self.target = self.doc.addObject("App::FeatureTest", "Target")
        self.links = []
        for name in ("LinkA", "LinkB"):
            link = self.doc.addObject("App::Link", name)
            link.LinkedObject = self.target
            self.links.append(link)
        self.doc.recompute()

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)
        self.pref.SetBool("VisibilityIcon", self.old_pref)

    def _items(self):
        items = [_item(link.Label) for link in self.links]
        self.assertTrue(all(items), "tree items missing for %s" % [l.Label for l in self.links])
        return items

    def testEqualStatesShareIcon(self):
        a, b = self._items()
        self.assertEqual(_image(a), _image(b))
        self.assertEqual(_image(a, QtGui.QIcon.On), _image(b, QtGui.QIcon.On))
        # one composed icon object, not one per item
        self.assertEqual(a.icon(0).cacheKey(), b.icon(0).cacheKey())

    def testLayoutIsBadgePlusIcon(self):
        (a, _) = self._items()
        img = _image(a)
        w, spacing = _split(a, img)
        self.assertEqual(img.height(), w)
        left = img.copy(0, 0, w + min(spacing, 0), img.height())
        right = img.copy(w + spacing, 0, w, img.height())
        # a badge is drawn at the left, the source icon at the right
        self.assertTrue(any(left.pixelColor(x, y).alpha() > 0 for x in range(left.width()) for y in range(left.height())))
        self.assertTrue(any(right.pixelColor(x, y).alpha() > 0 for x in range(w) for y in range(w)))

    def testToggleSwapsBadgeAndBack(self):
        a, b = self._items()
        before = _image(a)
        w, spacing = _split(a, before)
        width = w + min(spacing, 0)

        def left(im):
            return im.copy(0, 0, width, im.height())

        vp = self.links[0].ViewObject
        vp.Visibility = not vp.Visibility
        shown = _wait_image(self.links[0].Label, before)
        self.assertEqual(shown.width(), before.width())
        self.assertNotEqual(left(shown), left(before))
        # the other link keeps its icon
        self.assertEqual(_image(_item(self.links[1].Label)), before)

        vp.Visibility = not vp.Visibility
        self.assertEqual(_wait_image(self.links[0].Label, shown), before)
