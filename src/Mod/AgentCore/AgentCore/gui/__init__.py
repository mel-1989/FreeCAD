# SPDX-License-Identifier: LGPL-2.1-or-later
"""AgentCore.gui (F3): the change-set review panel and its 3D overlay.

GUI only: nothing outside ``InitGui.py`` imports this package, so ``FreeCADCmd`` and every
headless AgentCore test run without ``FreeCADGui``, PySide or pivy.

- ``review(turn)``: open the panel for an in-process ``AgentTurn`` that is ``proposed``.
- ``review_remote(approver_turn_id, fetch_change_set)``: open it for a turn that runs in an F4 worker.
- ``current()``: the open panel, or None.

Design models (behaviour only, no code, text or icons copied): the keep/undo review of agent edits in
the public VS Code documentation, and the public Onshape documentation of its Compare view.
"""

__all__ = ["review", "review_remote", "current"]


def review(turn, **kw):
    from .panel import review as _review
    return _review(turn, **kw)


def review_remote(turn_id, fetch_change_set, **kw):
    from .panel import review_remote as _review_remote
    return _review_remote(turn_id, fetch_change_set, **kw)


def current():
    from .panel import current as _current
    return _current()
