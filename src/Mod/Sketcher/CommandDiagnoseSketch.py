# SPDX-License-Identifier: LGPL-2.1-or-later
"""Sketcher_DiagnoseSketch: opens the diagnosis panel for the sketch in the editor (cad-fork G10 slice 4a).

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


class CommandDiagnoseSketch:
    def GetResources(self):
        return {
            "Pixmap": "Sketcher_ValidateSketch",
            "MenuText": QT_TRANSLATE_NOOP("Sketcher_DiagnoseSketch", "Diagnose sketch"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "Sketcher_DiagnoseSketch",
                "<p>Shows which constraints conflict or are redundant in the sketch being edited, "
                "with fixes the solver has tested. Choosing a conflict set or a fix selects its constraints.</p>",
            ),
            "StatusTip": QT_TRANSLATE_NOOP(
                "Sketcher_DiagnoseSketch", "Find conflicting and redundant constraints and fix them"
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return editing_sketch() is not None

    def Activated(self):
        import SketcherDiagnosisPanel

        SketcherDiagnosisPanel.open_panel()


Gui.addCommand("Sketcher_DiagnoseSketch", CommandDiagnoseSketch())
