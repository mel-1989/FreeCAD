# SPDX-License-Identifier: LGPL-2.1-or-later
"""AgentCore.diff (F3): transactional agent turns, change sets, snapshots.

Entry points (see ../../CONTRACT.md):

- ``AgentTurn(doc, label=..., store=None, checks=None)`` context manager:
  one FreeCAD transaction per agent turn, nested transactions detected and
  flattened; ``.change_set()``, ``.reject()``, ``.accept(token)``.
- ``ApplyAuthority.install()`` (human side only) and ``.mint(turn)``; the
  token is the only way through ``accept``.
- ``SnapshotStore(root)``: before-state ``.FCStd`` copies; ``.revert(doc, turn)``.
- ``shape_diff(before, after)``: face-level geometry diff.
"""

from .authority import ApplyAuthority, ApplyToken, TokenRefused
from .compare import differences, snapshot_dump
from .geometry import match_faces, shape_diff
from .snapshots import SnapshotStore
from .turn import SCHEMA, AgentTurn, FlattenError, RejectFailed, TurnError, TurnStale

__all__ = ["AgentTurn", "ApplyAuthority", "ApplyToken", "TokenRefused", "SnapshotStore", "shape_diff",
           "match_faces", "snapshot_dump", "differences", "SCHEMA", "TurnError", "TurnStale", "RejectFailed", "FlattenError"]
