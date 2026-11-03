# SPDX-License-Identifier: LGPL-2.1-or-later
"""Assembly_PublishSkeletonGeometry and Assembly_SubscribeSkeleton (cad-fork G06 slice 3b).

Both commands open the Skeleton panel (``SkeletonPanel``) on a part with the action already staged; nothing in a document
changes until the panel's OK. Publish stages the publication of the selected face, edge, vertex, sketch or datum on the
skeleton that carries it (resolved by FreeCAD's own sub-name resolution, so a selection made through an ``App::Link`` or a
sub-assembly finds the owning skeleton and element). Subscribe stages a subscription of the selected or active part to the
selected published item, or asks which item when none is selected. The icons are SVGs installed next to this file (the
compiled icon resource would need a rebuild of the GUI library), referenced by path as the other fork modules do.
"""

import os

import FreeCAD as App
import FreeCADGui as Gui
from PySide.QtCore import QT_TRANSLATE_NOOP

ICONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")


def open_panel(part):
    """Open the panel on ``part`` unless another task dialog is open. Returns the panel or None."""
    if Gui.Control.activeDialog():   # Gui.runCommand ignores IsActive: never build a second, hidden panel
        return None
    from SkeletonPanel import TaskSkeleton

    panel = TaskSkeleton(part)
    dialog = Gui.Control.showDialog(panel)
    if dialog is not None:
        dialog.setAutoCloseOnDeletedDocument(True)
        dialog.setDocumentName(part.Document.Name)
    return panel


def publishable():
    """(candidates, why): the selected geometry that belongs to one skeleton, the first skeleton found if several are selected."""
    from SkeletonPanel import resolve_selection

    cands, why = resolve_selection()
    if cands:
        first = cands[0].skeleton
        cands = [c for c in cands if c.skeleton == first]
    return cands, why


class CommandPublishSkeletonGeometry:
    def GetResources(self):
        return {
            "Pixmap": os.path.join(ICONS, "Assembly_PublishSkeletonGeometry.svg"),
            "MenuText": QT_TRANSLATE_NOOP("Assembly_PublishSkeletonGeometry", "Publish skeleton geometry"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_PublishSkeletonGeometry",
                "<p>Publishes the selected face, edge, vertex, sketch or datum of a skeleton part under a name, so that "
                "other parts can subscribe to it instead of to a raw face number.</p>"
                "<p>The change is listed in the Skeleton panel and made when you press OK.</p>",
            ),
            "StatusTip": QT_TRANSLATE_NOOP(
                "Assembly_PublishSkeletonGeometry", "Publish the selected skeleton geometry for other parts to subscribe to"
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        if App.ActiveDocument is None or Gui.Control.activeDialog():
            return False
        return bool(publishable()[0])

    def Activated(self):
        cands, why = publishable()
        if not cands or Gui.Control.activeDialog():
            return
        panel = open_panel(cands[0].skeleton)
        if panel is not None:
            panel.publish(candidates=cands)


class CommandSubscribeSkeleton:
    def GetResources(self):
        return {
            "Pixmap": os.path.join(ICONS, "Assembly_SubscribeSkeleton.svg"),
            "MenuText": QT_TRANSLATE_NOOP("Assembly_SubscribeSkeleton", "Subscribe to skeleton item"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_SubscribeSkeleton",
                "<p>Subscribes the active or selected part to a published item of a skeleton. The part gets a copy of the "
                "item's geometry that follows the skeleton, can be frozen, and reports when the item is gone.</p>"
                "<p>Select a published item first, or choose one in the Skeleton panel. The change is made when you press OK.</p>",
            ),
            "StatusTip": QT_TRANSLATE_NOOP(
                "Assembly_SubscribeSkeleton", "Subscribe the active or selected part to a published skeleton item"
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        if App.ActiveDocument is None or Gui.Control.activeDialog():
            return False
        from SkeletonPanel import published_items, selected_part

        return selected_part() is not None and bool(published_items())

    def Activated(self):
        from SkeletonPanel import selected_items, selected_part

        part = selected_part()
        if part is None or Gui.Control.activeDialog():
            return
        chosen = selected_items()
        panel = open_panel(part)
        if panel is None:
            return
        if chosen:
            for item in chosen:
                panel.subscribe(item)
        else:
            panel.subscribe()


Gui.addCommand("Assembly_PublishSkeletonGeometry", CommandPublishSkeletonGeometry())
Gui.addCommand("Assembly_SubscribeSkeleton", CommandSubscribeSkeleton())
