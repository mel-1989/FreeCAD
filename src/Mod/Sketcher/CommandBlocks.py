# SPDX-License-Identifier: LGPL-2.1-or-later
"""Sketcher_Blocks: opens the sketch blocks panel for the sketch in the editor (cad-fork G10 slice 5c).

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


class CommandBlocks:
    def GetResources(self):
        return {
            "Pixmap": "Sketcher_Copy",
            "MenuText": QT_TRANSLATE_NOOP("Sketcher_Blocks", "Sketch blocks"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "Sketcher_Blocks",
                "<p>Defines a block from the selected geometry, places it as many times as needed, by numbers or by a click in the view, "
                "and edits the definition once for every copy. Copies move as one rigid body.</p>",
            ),
            "StatusTip": QT_TRANSLATE_NOOP("Sketcher_Blocks", "Define, place, edit and explode blocks of sketch geometry"),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return editing_sketch() is not None

    def Activated(self):
        import SketcherBlocksPanel

        SketcherBlocksPanel.open_panel()


Gui.addCommand("Sketcher_Blocks", CommandBlocks())
