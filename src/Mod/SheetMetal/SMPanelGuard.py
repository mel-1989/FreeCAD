# SPDX-License-Identifier: LGPL-2.1-or-later
"""Shared guard of the SheetMetal task panels: has an object the panel holds been deleted since it was picked?

A panel keeps the objects the user picked (base part, sketch, profiles) as plain attributes. When one is deleted while
the panel is open, touching it raises ``ReferenceError`` with a message about "deleted object"; the panels call
``deleted_target_message`` before reading any of them and show the sentence it returns instead."""

import FreeCAD

#: panel attribute -> what to call it in the message
NOUNS = {"base": "part", "src": "solid", "source": "solid", "sketch": "sketch", "profile_a": "profile A",
         "profile_b": "profile B", "face": "face", "fixed": "fixed face"}

#: attributes that are not user picks
_SKIP = ("obj", "doc", "form")


def is_alive(o):
    """True when the document object ``o`` still exists in its document."""
    try:
        doc = o.Document
        return doc is not None and doc.getObject(o.Name) is not None
    except Exception:    # noqa: BLE001  (a deleted object raises ReferenceError)
        return False


def _objects(value, depth=0):
    if isinstance(value, FreeCAD.DocumentObject):
        yield value
    elif isinstance(value, (list, tuple)) and depth < 3:
        for item in value:
            yield from _objects(item, depth + 1)


def deleted_target_message(panel):
    """A readable sentence when a picked object was deleted, else None."""
    for name, value in vars(panel).items():
        if name in _SKIP or name.startswith("_"):
            continue
        for o in _objects(value):
            if not is_alive(o):
                return "The selected %s was deleted; pick it again." % NOUNS.get(name, "object")
    return None
