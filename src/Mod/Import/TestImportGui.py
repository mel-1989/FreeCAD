# SPDX-License-Identifier: LGPL-2.1-or-later

# **************************************************************************
#   Copyright (c) 2024 Werner Mayer <wmayer[at]users.sourceforge.net>     *
#                                                                         *
#   This file is part of FreeCAD.                                         *
#                                                                         *
#   FreeCAD is free software: you can redistribute it and/or modify it    *
#   under the terms of the GNU Lesser General Public License as           *
#   published by the Free Software Foundation, either version 2.1 of the  *
#   License, or (at your option) any later version.                       *
#                                                                         *
#   FreeCAD is distributed in the hope that it will be useful, but        *
#   WITHOUT ANY WARRANTY; without even the implied warranty of            *
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
#   Lesser General Public License for more details.                       *
#                                                                         *
#   You should have received a copy of the GNU Lesser General Public      *
#   License along with FreeCAD. If not, see                               *
#   <https://www.gnu.org/licenses/>.                                      *
#                                                                         *
# **************************************************************************

import os
import tempfile
import re
import unittest
import FreeCAD as App
import FreeCADGui as Gui
import ImportGui
from pivy import coin
from PySide import QtCore, QtWidgets


class ExportImportTest(unittest.TestCase):
    def setUp(self):
        TempPath = tempfile.gettempdir()
        self.fileName = TempPath + os.sep + "ColorPerFaceTest.step"
        self.doc = App.newDocument()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def testSaveLoadStepFile(self):
        """
        Create a STEP file with color per face
        """
        part = self.doc.addObject("App::Part", "Part")
        box = part.newObject("Part::Box", "Box")
        self.doc.recompute()

        box.ViewObject.DiffuseColor = [
            (1.0, 0.0, 0.0, 1.0),
            (1.0, 0.0, 0.0, 1.0),
            (1.0, 0.0, 0.0, 1.0),
            (1.0, 0.0, 0.0, 1.0),
            (1.0, 1.0, 0.0, 1.0),
            (1.0, 1.0, 0.0, 1.0),
        ]

        ImportGui.export([part], self.fileName)

        self.doc.clearDocument()
        ImportGui.insert(name=self.fileName, docName=self.doc.Name, merge=False, useLinkGroup=True)

        part_features = list(filter(lambda x: x.isDerivedFrom("Part::Feature"), self.doc.Objects))
        self.assertEqual(len(part_features), 1)
        feature = part_features[0]

        self.assertEqual(len(feature.ViewObject.DiffuseColor), 6)
        self.assertEqual(feature.ViewObject.DiffuseColor[0], (1.0, 0.0, 0.0, 1.0))
        self.assertEqual(feature.ViewObject.DiffuseColor[1], (1.0, 0.0, 0.0, 1.0))
        self.assertEqual(feature.ViewObject.DiffuseColor[2], (1.0, 0.0, 0.0, 1.0))
        self.assertEqual(feature.ViewObject.DiffuseColor[3], (1.0, 0.0, 0.0, 1.0))
        self.assertEqual(feature.ViewObject.DiffuseColor[4], (1.0, 1.0, 0.0, 1.0))
        self.assertEqual(feature.ViewObject.DiffuseColor[5], (1.0, 1.0, 0.0, 1.0))

        sa = coin.SoSearchAction()
        sa.setType(coin.SoMaterialBinding.getClassTypeId())
        # We need an easier way to access nodes of a display mode
        sa.setInterest(coin.SoSearchAction.ALL)
        sa.apply(feature.ViewObject.RootNode)
        paths = sa.getPaths()

        bind = paths.get(1).getTail()
        self.assertEqual(bind.value.getValue(), bind.PER_PART)

        sa = coin.SoSearchAction()
        sa.setType(coin.SoMaterial.getClassTypeId())
        # We need an easier way to access nodes of a display mode
        sa.setInterest(coin.SoSearchAction.ALL)
        sa.apply(feature.ViewObject.RootNode)
        paths = sa.getPaths()

        mat = paths.get(1).getTail()
        self.assertEqual(mat.diffuseColor.getNum(), 6)


PREF = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Part/STEP")


class StepExportPanelTest(unittest.TestCase):
    """G13 slice 1b check 10: the STEP export options page (Preferences > Import-Export > STEP).

    The page is modal, so a timer inside its event loop reads and changes the widgets and then presses OK or Cancel.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="g13panel")
        self.scheme = PREF.GetString("Scheme") if "Scheme" in PREF.GetStrings() else None
        self.props = PREF.GetBool("WriteValidationProperties") if "WriteValidationProperties" in PREF.GetBools() else None
        PREF.RemString("Scheme")
        PREF.RemBool("WriteValidationProperties")
        self.doc = App.newDocument("Panel")
        self.box = self.doc.addObject("Part::Box", "Box")
        self.doc.recompute()
        self.seen = {}

    def tearDown(self):
        App.closeDocument(self.doc.Name)
        for name, old, rem, setter in (
            ("Scheme", self.scheme, PREF.RemString, PREF.SetString),
            ("WriteValidationProperties", self.props, PREF.RemBool, PREF.SetBool),
        ):
            if old is None:
                rem(name)
            else:
                setter(name, old)
        for f in os.listdir(self.dir):
            os.remove(os.path.join(self.dir, f))
        os.rmdir(self.dir)

    def export(self, tag):
        p = os.path.join(self.dir, tag + ".step")
        ImportGui.export([self.box], p)
        text = open(p, errors="replace").read()
        return re.search(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']*)'", text).group(1), "geometric validation property" in text

    def drive(self, choose_schema=None, props=None, press="Ok"):
        """Open the page, record what it shows, optionally change it, press a button."""

        def act():
            dlg = QtWidgets.QApplication.activeModalWidget()
            try:
                combo = dlg.findChild(QtWidgets.QComboBox, "comboBoxSchema")
                box = dlg.findChild(QtWidgets.QCheckBox, "checkBoxValidationProps")
                self.seen = {"schema": combo.currentIndex(), "text": combo.currentText(), "props": box.isChecked()}
                if choose_schema is not None:
                    combo.setCurrentIndex(choose_schema)
                if props is not None:
                    box.setChecked(props)
                getattr(dlg, "accept" if press == "Ok" else "reject")()
            except Exception as e:  # never leave the modal loop open
                self.seen["error"] = repr(e)
                dlg.reject()

        QtCore.QTimer.singleShot(1500, act)
        Gui.showPreferencesByName("Import-Export", "PartGui::DlgImportExportStep")
        self.assertNotIn("error", self.seen, self.seen.get("error"))

    def test_page_opens_with_ap242_and_validation_switch_on(self):
        self.drive(press="Cancel")
        self.assertEqual(self.seen["schema"], 4)  # AP242 Draft International Standard
        self.assertIn("AP242", self.seen["text"])
        self.assertTrue(self.seen["props"])

    def test_closing_without_saving_changes_nothing(self):
        self.drive(choose_schema=3, props=False, press="Cancel")
        self.assertNotIn("Scheme", PREF.GetStrings())
        self.assertNotIn("WriteValidationProperties", PREF.GetBools())
        ident, props = self.export("cancel")
        self.assertTrue(ident.startswith("AP242_MANAGED_MODEL_BASED_3D_ENGINEERING"), ident)
        self.assertTrue(props)

    def test_saving_a_change_changes_the_next_export(self):
        self.drive(choose_schema=3, props=False, press="Ok")  # AP214 International Standard, switch off
        ident, props = self.export("ap214")
        self.assertTrue(ident.startswith("AUTOMOTIVE_DESIGN {"), ident)
        self.assertFalse(props)
        self.drive(choose_schema=4, props=True, press="Ok")
        self.assertEqual(self.seen["schema"], 3)  # the page showed the saved AP214 choice ...
        self.assertFalse(self.seen["props"])  # ... and the saved, switched-off validation properties
        ident, props = self.export("ap242")
        self.assertTrue(ident.startswith("AP242_MANAGED_MODEL_BASED_3D_ENGINEERING"), ident)
        self.assertTrue(props)



class StepAttributesGuiTest(unittest.TestCase):
    """G13 slice 1c: the GUI import path (ImportGui.insert, ImportGui.open) carries attributes and layers like Import."""

    def setUp(self):
        import Part
        self.dir = tempfile.mkdtemp(prefix="g13attrgui")
        doc = App.newDocument("G13AttrGuiSrc")
        box = doc.addObject("Part::Feature", "Box")
        box.Shape = Part.makeBox(10, 20, 30)
        box.addProperty("App::PropertyMap", "StepAttributes", "STEP")
        box.StepAttributes = {"Part Number": "PN-9", "grade": "A", "w": "1.5 mm"}
        box.addProperty("App::PropertyStringList", "StepLayers", "STEP")
        box.StepLayers = ["red"]
        doc.recompute()
        self.path = os.path.join(self.dir, "attrs.step")
        import Import
        Import.export([box], self.path)
        App.closeDocument(doc.Name)
        self.docs = []

    def tearDown(self):
        for d in self.docs:
            App.closeDocument(d.Name)
        for f in os.listdir(self.dir):
            os.remove(os.path.join(self.dir, f))
        os.rmdir(self.dir)

    def held(self, doc):
        return [(dict(o.StepAttributes) if hasattr(o, "StepAttributes") else None,
                 list(o.StepLayers) if hasattr(o, "StepLayers") else None) for o in doc.Objects
                if hasattr(o, "StepAttributes") or hasattr(o, "StepLayers")]

    def test_insert_carries_attributes_and_layers(self):
        doc = App.newDocument("G13AttrGui")
        self.docs.append(doc)
        ImportGui.insert(self.path, doc.Name)
        got = self.held(doc)
        self.assertTrue(any(m == {"Part Number": "PN-9", "grade": "A", "w": "1.5 mm"} for (m, _) in got), got)
        self.assertTrue(any(l == ["red"] for (_, l) in got), got)

    def test_open_carries_attributes_and_layers(self):
        before = set(App.listDocuments())
        ImportGui.open(self.path)
        doc = [App.getDocument(n) for n in App.listDocuments() if n not in before][0]
        self.docs.append(doc)
        got = self.held(doc)
        self.assertTrue(any(m == {"Part Number": "PN-9", "grade": "A", "w": "1.5 mm"} for (m, _) in got))
        self.assertTrue(any(l == ["red"] for (_, l) in got))
