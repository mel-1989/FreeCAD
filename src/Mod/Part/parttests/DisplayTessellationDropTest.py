# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI tests for G05 slice 3b: once the view provider has built the Coin nodes from a triangulation it
made itself, it drops that triangulation from the shape (unless KeepDisplayTessellation is set); a
triangulation the shape carried before display is kept."""

import unittest

import FreeCAD
import FreeCADGui
import Part

PREF = "User parameter:BaseApp/Preferences/Mod/Part/General"


def _nodes(shape):
    return sum(f.countNodes() for f in shape.Faces)


def _coin_points(obj):
    from pivy import coin

    vp = obj.ViewObject
    sg = vp.RootNode
    found = []

    def walk(n):
        if isinstance(n, coin.SoCoordinate3):
            found.append(n.point.getNum())
        if isinstance(n, coin.SoGroup):
            for i in range(n.getNumChildren()):
                walk(n.getChild(i))

    walk(sg)
    return sum(found)


class DisplayTessellationDropTest(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("DisplayTessDrop")
        self.pref = FreeCAD.ParamGet(PREF)
        self.old = self.pref.GetBool("KeepDisplayTessellation", False)

    def tearDown(self):
        self.pref.SetBool("KeepDisplayTessellation", self.old)
        FreeCAD.closeDocument(self.doc.Name)

    def _box(self, name, hidden=False):
        box = self.doc.addObject("Part::Box", name)
        if hidden:
            box.ViewObject.Visibility = False
        self.doc.recompute()
        return box

    def test_dropped_after_display(self):
        self.pref.SetBool("KeepDisplayTessellation", False)
        box = self._box("BoxDropped")
        box.ViewObject.show()
        FreeCADGui.updateGui()
        self.assertEqual(_nodes(box.Shape), 0)
        self.assertGreater(_coin_points(box), 0)

    def test_python_tessellation_kept(self):
        self.pref.SetBool("KeepDisplayTessellation", False)
        box = self._box("BoxTessellated", hidden=True)
        box.Shape.tessellate(0.1)
        before = _nodes(box.Shape)
        self.assertGreater(before, 0)
        box.ViewObject.show()
        FreeCADGui.updateGui()
        self.assertGreater(_nodes(box.Shape), 0)
        self.assertGreater(_coin_points(box), 0)

    def test_preference_on_keeps_everything(self):
        self.pref.SetBool("KeepDisplayTessellation", True)
        box = self._box("BoxKept")
        box.ViewObject.show()
        FreeCADGui.updateGui()
        self.assertGreater(_nodes(box.Shape), 0)
        self.assertGreater(_coin_points(box), 0)
