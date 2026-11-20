# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI tests for the lazily built placement indicator of ViewProviderDragger (G05 slice 2): no
SoFCPlacementIndicatorKit exists until ShowPlacement is shown, and then it is identical to before."""

import os
import shutil
import tempfile
import unittest

import FreeCAD
import FreeCADGui
from pivy import coin

KIT = "SoFCPlacementIndicatorKit"


def _kits(vp):
    sa = coin.SoSearchAction()
    sa.setType(coin.SoType.fromName(KIT))
    sa.setInterest(coin.SoSearchAction.ALL)
    sa.setSearchingAll(True)
    sa.apply(vp.RootNode)
    return sa.getPaths().getLength()


def _switch_state(vp):
    """whichChild of the switch that holds the indicator, or None when there is none."""
    sa = coin.SoSearchAction()
    sa.setType(coin.SoSwitch.getClassTypeId())
    sa.setInterest(coin.SoSearchAction.ALL)
    sa.setSearchingAll(True)
    sa.apply(vp.RootNode)
    paths = sa.getPaths()
    for i in range(paths.getLength()):
        sw = paths.get(i).getTail()
        if sw.getNumChildren() == 1 and sw.getChild(0).getTypeId().getName().getString() == (
            "So3DAnnotation"
        ):
            return sw.whichChild.getValue()
    return None


class PlacementIndicatorTest(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("PlacementIndicatorTest")
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def testBuiltOnFirstShowOnly(self):
        vp = self.doc.addObject("Part::Box", "Box").ViewObject
        self.assertEqual(_kits(vp), 0)
        # hiding or toggling visibility alone does not build it
        vp.Visibility = False
        vp.Visibility = True
        self.assertEqual(_kits(vp), 0)

        vp.ShowPlacement = True
        self.assertEqual(_kits(vp), 1)
        self.assertEqual(_switch_state(vp), coin.SO_SWITCH_ALL)

        # visibility still gates the switch, and the kit is built once and reused
        vp.Visibility = False
        self.assertEqual(_switch_state(vp), coin.SO_SWITCH_NONE)
        vp.Visibility = True
        self.assertEqual(_switch_state(vp), coin.SO_SWITCH_ALL)
        vp.ShowPlacement = False
        self.assertEqual(_switch_state(vp), coin.SO_SWITCH_NONE)
        vp.ShowPlacement = True
        self.assertEqual(_kits(vp), 1)
        self.assertEqual(_switch_state(vp), coin.SO_SWITCH_ALL)

    def testShownWhenHiddenObjectBecomesVisible(self):
        vp = self.doc.addObject("Part::Box", "Box").ViewObject
        vp.Visibility = False
        vp.ShowPlacement = True
        # hidden: nothing is built and nothing is shown
        self.assertEqual(_kits(vp), 0)
        vp.Visibility = True
        self.assertEqual(_kits(vp), 1)
        self.assertEqual(_switch_state(vp), coin.SO_SWITCH_ALL)

    def testReopenSavedDocument(self):
        shown = self.doc.addObject("Part::Box", "Shown")
        plain = self.doc.addObject("Part::Box", "Plain")
        shown.ViewObject.ShowPlacement = True
        self.doc.recompute()
        path = os.path.join(self.tmp, "indicator.FCStd")
        self.doc.saveAs(path)
        FreeCAD.closeDocument(self.doc.Name)

        self.doc = FreeCAD.openDocument(path)
        shown = self.doc.getObject("Shown").ViewObject
        plain = self.doc.getObject("Plain").ViewObject
        self.assertTrue(shown.ShowPlacement)
        self.assertEqual(_kits(shown), 1)
        self.assertEqual(_switch_state(shown), coin.SO_SWITCH_ALL)
        self.assertFalse(plain.ShowPlacement)
        self.assertEqual(_kits(plain), 0)
        plain.ShowPlacement = True
        self.assertEqual(_kits(plain), 1)
        self.assertEqual(_switch_state(plain), coin.SO_SWITCH_ALL)

    def testLinkDoesNotBuildIt(self):
        box = self.doc.addObject("Part::Box", "Box")
        link = self.doc.addObject("App::Link", "Link")
        link.LinkedObject = box
        self.doc.recompute()
        self.assertEqual(_kits(link.ViewObject), 0)
