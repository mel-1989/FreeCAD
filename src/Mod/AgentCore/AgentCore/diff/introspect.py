# SPDX-License-Identifier: LGPL-2.1-or-later
"""The active turn's changes as FreeCAD's own transaction records report them.

A thin view over ``Document.getAgentTurnChanges()``: the same object and property sets that
``changeset.object_changes`` derives from two F1 dumps, read instead from the stored old values
of the document's undo transaction. The diff engine still takes its change set from the dumps;
this view is the independent source the agreement test holds it against.
"""


def turn_changes(doc, **kwargs):
    """Raw ``doc.getAgentTurnChanges()`` (raises RuntimeError outside a guarded turn)."""
    return doc.getAgentTurnChanges(**kwargs)


def summary(changes, drop_maybe_unchanged=True):
    """``{"added": {names}, "removed": {names}, "properties": {(object, property): record}}``.

    ``properties`` covers modified objects only; an added object is reported whole. Entries the
    API flags ``maybe_unchanged`` (equal once serialised, but ``Property.isSame`` said no, as it
    does for a rebuilt shape) are left out unless ``drop_maybe_unchanged`` is False.
    """
    out = {"added": set(), "removed": set(), "properties": {}}
    for o in changes["objects"]:
        if o["status"] == "added":
            out["added"].add(o["name"])
        elif o["status"] == "removed":
            out["removed"].add(o["name"])
        else:
            for p in o["properties"]:
                if drop_maybe_unchanged and p.get("maybe_unchanged"):
                    continue
                out["properties"][(o["name"], p["name"])] = p
    return out
