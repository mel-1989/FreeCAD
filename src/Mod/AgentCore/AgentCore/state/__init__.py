# SPDX-License-Identifier: LGPL-2.1-or-later
"""AgentCore.state (F1): agent-readable model state.

Entry points (see ../../CONTRACT.md): SCHEMA_VERSION, dump, entity_id,
describe_entity, track_references. Also: load_schema, validate_dump,
resolve_entity.
"""

import json
import os

from .entity import describe_entity, entity_id, resolve_entity
from .refs import ReferenceTracker, track_references
from .serialize import SCHEMA_VERSION, dump, dump_all_pages
from .validate import validate as _validate

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.json")
_SCHEMA = None


def load_schema():
    global _SCHEMA
    if _SCHEMA is None:
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            _SCHEMA = json.load(f)
    return _SCHEMA


def validate_dump(d):
    """List of schema errors for a dump (empty when valid)."""
    return _validate(d, load_schema())


__all__ = ["SCHEMA_VERSION", "dump", "dump_all_pages", "entity_id", "describe_entity", "resolve_entity",
           "track_references", "ReferenceTracker", "load_schema", "validate_dump"]
