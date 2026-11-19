# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI tests for the tree icon of App::Link objects (G05 slice 1): the overlay and the composed
icon are cached, so equal states must give pixel-identical icons and every real state change must
still change the icon."""

import os
import shutil
import stat
import tempfile
import unittest

import FreeCAD
import FreeCADGui
from PySide import QtCore, QtGui


def _image(icon, size=16, mode=QtGui.QIcon.Normal, state=QtGui.QIcon.Off):
    return icon.pixmap(size, size, mode, state).toImage()


def _icons(obj):
    """The 16 and 32 px, normal and disabled images of the object's view provider icon."""
    icon = obj.ViewObject.Icon
    return [
        _image(icon, size, mode)
        for size in (16, 32)
        for mode in (QtGui.QIcon.Normal, QtGui.QIcon.Disabled)
    ]


def _same(a, b):
    return all(x == y for x, y in zip(a, b))


class LinkIconTest(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("LinkIconTest")
        self.box = self.doc.addObject("App::FeatureTest", "Target")
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)
        for name in list(FreeCAD.listDocuments()):
            if name.startswith("LinkIconRO"):
                FreeCAD.closeDocument(name)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _link(self, name="Link"):
        link = self.doc.addObject("App::Link", name)
        link.LinkedObject = self.box
        self.doc.recompute()
        return link

    def _readonly_target(self):
        doc = FreeCAD.newDocument("LinkIconROSrc")
        doc.addObject("App::FeatureTest", "ROTarget")
        path = os.path.join(self.tmp, "ro.FCStd")
        doc.saveAs(path)
        FreeCAD.closeDocument(doc.Name)
        os.chmod(path, stat.S_IRUSR)
        ro = FreeCAD.openDocument(path)
        return ro, ro.getObject("ROTarget")

    def testEqualStatesGiveEqualIcons(self):
        a = self._link("A")
        b = self._link("B")
        self.assertTrue(_same(_icons(a), _icons(b)))
        self.assertTrue(_same(_icons(a), _icons(a)))
        # the overlay is real: a link icon differs from the plain target icon
        self.assertFalse(_same(_icons(a), _icons(self.box)))

    def testArrayFlipFollowsAndRestores(self):
        link = self._link()
        plain = _icons(link)
        link.ElementCount = 3
        self.doc.recompute()
        array = _icons(link)
        self.assertFalse(_same(plain, array))
        link.ElementCount = 0
        self.doc.recompute()
        self.assertTrue(_same(plain, _icons(link)))
        link.ElementCount = 3
        self.doc.recompute()
        self.assertTrue(_same(array, _icons(link)))

    def testSubElementAndSubNameDifferFromPlain(self):
        link = self._link()
        plain = _icons(link)
        link.setLink(self.box, "Face1")
        self.doc.recompute()
        subelement = _icons(link)
        self.assertFalse(_same(plain, subelement))
        link.setLink(self.box, "Child.")
        self.doc.recompute()
        subname = _icons(link)
        self.assertFalse(_same(plain, subname))
        self.assertFalse(_same(subelement, subname))
        link.setLink(self.box)
        self.doc.recompute()
        self.assertTrue(_same(plain, _icons(link)))

    def testReadOnlyFlip(self):
        self.doc.saveAs(os.path.join(self.tmp, "owner.FCStd"))
        link = self._link()
        plain = _icons(link)
        ro, target = self._readonly_target()
        link.LinkedObject = target
        self.doc.recompute()
        readonly = _icons(link)
        self.assertFalse(_same(plain, readonly))
        # a second link to the read-only target gets the same icon from the cache
        other = self.doc.addObject("App::Link", "Other")
        other.LinkedObject = target
        self.doc.recompute()
        self.assertTrue(_same(readonly, _icons(other)))
        link.LinkedObject = self.box
        self.doc.recompute()
        self.assertTrue(_same(plain, _icons(link)))

    def testIconSearchPathIsHonoured(self):
        link = self._link()
        plain = _icons(link)
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">'
            '<rect width="64" height="64" fill="#ff0000"/></svg>'
        )
        with open(os.path.join(self.tmp, "LinkOverlay.svg"), "w") as f:
            f.write(svg)
        old = QtCore.QDir.searchPaths("icons")
        try:
            QtCore.QDir.setSearchPaths("icons", [self.tmp] + list(old))
            other = self._link("Other")
            self.assertFalse(_same(plain, _icons(other)))
        finally:
            QtCore.QDir.setSearchPaths("icons", old)
        again = self._link("Again")
        self.assertTrue(_same(plain, _icons(again)))
