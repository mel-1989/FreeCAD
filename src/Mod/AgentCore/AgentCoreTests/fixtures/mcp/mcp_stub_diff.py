# SPDX-License-Identifier: LGPL-2.1-or-later
"""Small in-test stand-in for F3's AgentTurn (CONTRACT.md: AgentTurn(doc) context
manager with .change_set(), .reject(), .accept(token)).

Used only by the F4 tests, through CADFORK_MCP_DIFF_MODULE=mcp_stub_diff, so the MCP
tool layer can be tested before F3 merges. It is deliberately simple: one document
transaction per turn and a change set computed from F1 dumps. It is not F3.
"""

import json

from AgentCore import state

STUB_SCHEMA = "mcp-stub-diff/1"
_GUI_TOKEN = None  # headless: nobody holds an apply token


def _props(obj):
    return {p["name"]: json.dumps(p.get("value"), sort_keys=True, default=str) for p in obj.get("properties", [])}


class AgentTurn:
    def __init__(self, doc):
        self.doc = doc
        self.before = None
        self.open = False

    def __enter__(self):
        self.before = state.dump(self.doc)
        try:
            self.doc.UndoMode = 1
        except Exception:
            pass
        self.doc.openTransaction("agent turn")
        self.open = True
        return self

    def __exit__(self, *exc):
        return False

    def change_set(self):
        after = state.dump(self.doc)
        b = {o["name"]: o for o in self.before["objects"]}
        a = {o["name"]: o for o in after["objects"]}
        modified = []
        for n in sorted(set(a) & set(b)):
            pa, pb = _props(a[n]), _props(b[n])
            changed = sorted(k for k in set(pa) | set(pb) if pa.get(k) != pb.get(k))
            if changed or a[n].get("shape") != b[n].get("shape"):
                modified.append({"name": n, "properties": changed})
        return {"schema": STUB_SCHEMA, "added": sorted(set(a) - set(b)), "removed": sorted(set(b) - set(a)),
                "modified": modified}

    def reject(self):
        if self.open:
            self.doc.abortTransaction()
            self.doc.recompute()
            self.open = False

    def accept(self, token):
        if _GUI_TOKEN is None or token != _GUI_TOKEN:
            raise PermissionError("apply token required (GUI only)")
        self.doc.commitTransaction()
        self.open = False
