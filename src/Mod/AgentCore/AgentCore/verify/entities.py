# SPDX-License-Identifier: LGPL-2.1-or-later
"""Entity ids for verify results.

Uses AgentCore.state.entity_id (F1) when importable; otherwise the same
"<ObjectName>:<SubElement>" format (just "<ObjectName>" for a whole object).
"""


def entity(obj, sub=""):
    try:
        from AgentCore.state import entity_id  # F1

        return entity_id(obj, sub)
    except Exception:
        name = getattr(obj, "Name", str(obj))
        return f"{name}:{sub}" if sub else name
