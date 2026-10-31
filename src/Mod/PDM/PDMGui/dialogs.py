# SPDX-License-Identifier: LGPL-2.1-or-later
"""Message dialogs that never block.

Every message is appended to ``shown`` and, unless a test installed ``handler``, put up as a non-modal
QMessageBox (kept in ``boxes`` until closed), so an unattended run cannot hang on a dialog and a test can read
exactly what the user would see."""

import FreeCADGui as Gui
from PySide import QtWidgets

shown = []      # (kind, title, text) of every message, oldest first
boxes = []      # the live message boxes
handler = None  # tests may set a callable(kind, title, text); then no box is made


def clear():
    """Forget the log and close any open box (tests call this in setUp/tearDown)."""
    shown.clear()
    for b in list(boxes):
        b.close()
        b.deleteLater()
    boxes.clear()


def _show(kind, title, text):
    shown.append((kind, title, text))
    if handler is not None:
        handler(kind, title, text)
        return None
    icon = QtWidgets.QMessageBox.Critical if kind == "error" else QtWidgets.QMessageBox.Information
    box = QtWidgets.QMessageBox(icon, title, text, QtWidgets.QMessageBox.Ok, Gui.getMainWindow())
    box.setObjectName("PDMMessage")
    box.setModal(False)
    box.finished.connect(lambda *_: boxes.remove(box) if box in boxes else None)
    boxes.append(box)
    box.show()
    return box


def error(title, text):
    return _show("error", title, text)


def info(title, text):
    return _show("info", title, text)


def report(title, exc):
    """Show a vault error. A lock conflict names the holder."""
    text = str(exc)
    holder = getattr(exc, "holder", None)
    if holder and holder not in text:
        text += f"\n\nChecked out by: {holder}"
    return error(title, text)
