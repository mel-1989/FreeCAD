# SPDX-License-Identifier: LGPL-2.1-or-later
"""Sketcher_Relations: opens the relations panel for the sketch in the editor (cad-fork G10 slice 4c).

Registered at start-up by InitGui.py; the panel module (and Qt) is imported only when the command runs."""

import FreeCAD as App
import FreeCADGui as Gui

QT_TRANSLATE_NOOP = App.Qt.QT_TRANSLATE_NOOP


def editing_sketch():
    gdoc = Gui.ActiveDocument
    if gdoc is None:
        return None
    vp = gdoc.getInEdit()
    obj = getattr(vp, "Object", None) if vp is not None else None
    return obj if obj is not None and obj.TypeId == "Sketcher::SketchObject" else None


class CommandRelations:
    def GetResources(self):
        return {
            "Pixmap": "Sketcher_SelectElementsAssociatedWithConstraints",
            "MenuText": QT_TRANSLATE_NOOP("Sketcher_Relations", "Sketch relations"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "Sketcher_Relations",
                "<p>Lists the constraints on the selected element and the elements each one ties it to. Choosing a constraint highlights it and its elements; "
                "the panel can also hide the other constraints in the view. The sketch is not changed.</p>",
            ),
            "StatusTip": QT_TRANSLATE_NOOP("Sketcher_Relations", "List and filter the constraints on the selected element"),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return editing_sketch() is not None

    def Activated(self):
        import SketcherRelationsPanel

        SketcherRelationsPanel.open_panel()


Gui.addCommand("Sketcher_Relations", CommandRelations())
