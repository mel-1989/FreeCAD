# SPDX-License-Identifier: LGPL-2.1-or-later
"""Licence guard for the Weldment module (cad-fork hard rules 7 and 8).

Every .py and .csv file under src/Mod/Weldment carries the LGPL-2.1-or-later SPDX line, no file
carries a GPL-only notice, and nothing imports a weldment workbench or add-on (the module is a
clean-room implementation).
"""

import ast
import os
import unittest

MOD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPDX = "SPDX-License-Identifier: LGPL-2.1-or-later"
# Built piecewise so this file does not contain the phrase itself.
GPL_PHRASE = "GNU General " + "Public License"
#: Names of other weldment workbenches and add-ons that must never be imported.
FOREIGN = {"FrameForge", "frameforge", "Weldments", "WeldmentWB", "weldment", "FreeCAD_Weldment"}


def _files(suffixes):
    for root, _dirs, names in os.walk(MOD_DIR):
        if "__pycache__" in root:
            continue
        for name in sorted(names):
            if name.endswith(suffixes):
                yield os.path.join(root, name)


class TestWeldmentLicence(unittest.TestCase):
    def test_spdx_line_in_every_py_and_csv(self):
        bad = []
        for path in _files((".py", ".csv")):
            with open(path, encoding="utf-8", errors="replace") as fh:
                head = fh.read(600)
            if SPDX not in head:
                bad.append(os.path.relpath(path, MOD_DIR))
        self.assertEqual(bad, [], "files without the SPDX line: %s" % bad)

    def test_no_gpl_only_notice(self):
        bad = []
        for path in _files((".py", ".csv", ".md", ".txt")):
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            if GPL_PHRASE in text and "Lesser" not in text:
                bad.append(os.path.relpath(path, MOD_DIR))
        self.assertEqual(bad, [])

    def test_no_foreign_weldment_imports(self):
        bad = []
        for path in _files((".py",)):
            with open(path, encoding="utf-8", errors="replace") as fh:
                tree = ast.parse(fh.read(), filename=path)
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    names = [node.module.split(".")[0]]
                if FOREIGN & set(names):
                    bad.append((os.path.relpath(path, MOD_DIR), sorted(FOREIGN & set(names))))
        self.assertEqual(bad, [])

    def test_headless_import_without_pyside(self):
        import importlib
        import sys

        import FreeCAD

        if FreeCAD.GuiUp or any(m.startswith("PySide") for m in sys.modules):
            self.skipTest("a GUI or PySide is already loaded in this process")
        for name in ("WeldGeom", "WeldMember", "WeldTrim", "WeldCutList", "Profiles"):
            importlib.import_module(name)
        self.assertFalse([m for m in sys.modules if m.startswith("PySide")])


if __name__ == "__main__":
    unittest.main()
