# SPDX-License-Identifier: LGPL-2.1-or-later
"""PDM GUI (G03 slice 5): commands and panels over the headless ``PDM.vault`` library.

Imported only from ``InitGui.py`` and the GUI tests, never from ``PDM`` itself, so FreeCADCmd keeps
working without PySide. Vault logic lives in ``PDM.vault``; this package only calls it."""
