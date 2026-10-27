# SPDX-License-Identifier: LGPL-2.1-or-later
"""AgentCore.refactor (F6): geometry-invariant bulk refactors, macro scaffolds, static grounding.

- ``invariant(doc, fn)``: hash the geometry, run ``fn``, hash again, fail on any change.
- ``relabel``, ``lift_literal``, ``lift_all``, ``apply_across_bodies``, ``merge_redundant``:
  the refactor operations (see ``SAFETY.md`` for the merge rules awaiting human sign-off).
- ``scaffold``: generate ``.FCMacro`` files and ``FeaturePython`` modules.
- ``grounding``: run pyright against ``freecad-stubs`` before a macro executes.
"""

from .geomhash import Snapshot, compare, load_tolerances, snapshot
from .invariant import invariant
from .ops import (
    across_all_bodies,
    apply_across_bodies,
    find_redundant,
    lift_all,
    lift_candidates,
    lift_literal,
    merge_redundant,
    relabel,
    relabel_all,
    relabel_scheme,
)

__all__ = [
    "invariant", "snapshot", "compare", "load_tolerances", "Snapshot",
    "relabel", "relabel_all", "relabel_scheme", "lift_literal", "lift_all", "lift_candidates",
    "apply_across_bodies", "across_all_bodies", "find_redundant", "merge_redundant",
]
