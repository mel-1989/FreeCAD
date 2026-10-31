# SPDX-License-Identifier: LGPL-2.1-or-later
"""Licence guard for the vendored SheetMetal subset (cad-fork hard rule 8).

Fails if any file under src/Mod/SheetMetal carries a GPL (not Lesser) licence notice, or if
a Python file imports a module that was left out because its source is GPL-licensed.
"""

import ast
import os
import unittest

MOD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Modules of shaise/FreeCAD_SheetMetal that are GPL (or import GPL modules) and are not vendored.
LEFT_OUT = {
    "SheetMetalCmd",
    "SheetMetalHem",
    "SheetMetalFromSolid",
    "SheetMetalCornerReliefCmd",
    "SheetMetalJunction",
    "SheetMetalSketch",
    "SheetMetalText",
    "ExtrudedCutout",
    "current",
    "SheetMetalBaseShapeCmd",
    "SketchOnSheetMetalCmd",
}

# Built piecewise so this file does not contain the phrase itself.
GPL_PHRASE = "GNU General " + "Public License"
TEXT_SUFFIXES = (".py", ".md", ".csv", ".ui", ".svg", ".txt", ".cmake", ".json")


def _files(suffixes):
    for root, _dirs, names in os.walk(MOD_DIR):
        for name in sorted(names):
            if name.endswith(suffixes):
                yield os.path.join(root, name)


def _imported_modules(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        tree = ast.parse(fh.read(), filename=path)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


class TestSheetMetalLicence(unittest.TestCase):
    def test_no_gpl_only_header(self):
        bad = []
        for path in _files(TEXT_SUFFIXES):
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            if GPL_PHRASE in text and "Lesser" not in text:
                bad.append(os.path.relpath(path, MOD_DIR))
        self.assertEqual(bad, [], "files with a GPL-only notice: %s" % bad)

    def test_no_import_of_left_out_modules(self):
        bad = []
        for path in _files((".py",)):
            hit = _imported_modules(path) & LEFT_OUT
            if hit:
                bad.append((os.path.relpath(path, MOD_DIR), sorted(hit)))
        self.assertEqual(bad, [], "imports of left-out GPL modules: %s" % bad)

    def test_headless_import_without_pyside(self):
        """The four core modules import in headless FreeCADCmd without pulling in PySide."""
        import importlib
        import sys

        import FreeCAD

        if FreeCAD.GuiUp or any(m.startswith("PySide") for m in sys.modules):
            self.skipTest("a GUI or PySide is already loaded in this process")
        for name in ("SheetMetalTools", "SheetMetalUnfolder", "SheetMetalNewUnfolder", "SheetMetalKfactor"):
            importlib.import_module(name)
        self.assertFalse([m for m in sys.modules if m.startswith("PySide")])

    def test_left_out_files_absent(self):
        present = [m for m in sorted(LEFT_OUT) if os.path.exists(os.path.join(MOD_DIR, m + ".py"))]
        self.assertEqual(present, [])

    def test_vendored_files_keep_headers(self):
        """Every vendored add-on file keeps a copyright line and the LGPL text."""
        missing = []
        for path in _files((".py",)):
            name = os.path.basename(path)
            with open(path, encoding="utf-8", errors="replace") as fh:
                head = fh.read(3000)
            if "SPDX-License-Identifier: LGPL-2.1-or-later" in head:
                continue
            if "Copyright" not in head or "Lesser" not in head:
                missing.append(name)
        self.assertEqual(missing, [], "files without a header: %s" % missing)


if __name__ == "__main__":
    unittest.main()
