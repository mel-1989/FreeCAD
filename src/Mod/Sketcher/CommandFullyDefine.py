# SPDX-License-Identifier: LGPL-2.1-or-later
"""Sketcher_FullyDefine: opens the Fully Define dialog for the sketch in the editor (cad-fork G10 slice 4b).

Registered at start-up by InitGui.py; the dialog module (and Qt) is imported only when the command runs."""

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


class CommandFullyDefine:
    def GetResources(self):
        return {
            "Pixmap": "Sketcher_ValidateSketch",
            "MenuText": QT_TRANSLATE_NOOP("Sketcher_FullyDefine", "Fully define sketch"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "Sketcher_FullyDefine",
                "<p>Proposes constraint sets that remove the remaining degrees of freedom of the sketch being edited, "
                "each already tested on a copy. Choosing a candidate shows what it would add; applying it is one undoable step.</p>",
            ),
            "StatusTip": QT_TRANSLATE_NOOP("Sketcher_FullyDefine", "Add relations and dimensions until the sketch is fully defined"),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return editing_sketch() is not None

    def Activated(self):
        import SketcherFullyDefinePanel

        SketcherFullyDefinePanel.open_panel()


Gui.addCommand("Sketcher_FullyDefine", CommandFullyDefine())
