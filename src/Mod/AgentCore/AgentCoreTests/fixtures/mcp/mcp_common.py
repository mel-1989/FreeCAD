# SPDX-License-Identifier: LGPL-2.1-or-later
"""Shared helpers for the F4 MCP tests (not collected: name does not start with test_)."""

import json
import os
import sys
import tempfile

_FIX = os.path.dirname(os.path.abspath(__file__))
_VERIFY_FIX = os.path.join(os.path.dirname(_FIX), "verify")
for _p in (_FIX, _VERIFY_FIX):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MCP_FIX = _FIX
AGENTCORE_SRC = os.path.dirname(os.path.dirname(os.path.dirname(_FIX)))  # src/Mod/AgentCore
GOLDEN_SESSION = os.path.join(_FIX, "golden_session.json")
STUB_ENV = {"CADFORK_MCP_DIFF_MODULE": "mcp_stub_diff", "CADFORK_MCP_PYTHONPATH": _FIX}


def freecadcmd():
    from AgentCore.mcp.workers import find_freecadcmd

    return find_freecadcmd()


def save_fixtures(names, directory):
    """Save F2's verify fixtures (box, pair_overlap, plate, ...) as .FCStd; return {name: path}."""
    import verify_builders

    return {n: verify_builders.save(n, directory) for n in names}


def make_server(stub=True, **kw):
    from AgentCore.mcp.server import Server

    return Server(freecadcmd=freecadcmd(), worker_env=STUB_ENV if stub else {}, **kw)


def call(server, name, **args):
    """tools/call through the JSON-RPC layer; returns (structuredContent, isError)."""
    resp = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    if "error" in resp:
        raise AssertionError(f"JSON-RPC error from {name}: {resp['error']}")
    res = resp["result"]
    assert json.loads(res["content"][0]["text"]) == res["structuredContent"]
    return res["structuredContent"], res["isError"]


# The scripted agent session replayed by the golden test.
SCRIPT_EDIT = """
box = doc.getObject("Box")
box.Length = 30
hole = doc.addObject("Part::Cylinder", "Hole")
hole.Radius, hole.Height = 2.5, 50
hole.Placement.Base = App.Vector(15, 5, -1)
plate = doc.addObject("Part::Cut", "Plate")
plate.Base, plate.Tool = box, hole
doc.recompute()
result = {"volume": round(plate.Shape.Volume, 6), "faces": len(plate.Shape.Faces)}
"""


def scripted_session(server, fcstd):
    """read state, begin change, edit, verify, propose; return the transcript and final state."""
    log = []

    def step(name, **a):
        out, err = call(server, name, **a)
        log.append({"tool": name, "isError": err})
        if err:
            raise AssertionError(f"{name} failed: {out}")
        return out

    opened = step("open_document", path=fcstd)
    doc = opened["doc"]
    before = step("get_state", doc=doc)
    step("begin_change", doc=doc)
    ex = step("exec_code", doc=doc, code=SCRIPT_EDIT)
    ver = step("verify", doc=doc, checks=["recompute", "validity", "mass"])
    prop = step("propose_change", doc=doc, summary="lengthen Box, cut a hole")
    after = step("get_state", doc=doc)
    return {"log": log, "before": before, "exec": ex, "verify": ver, "proposal": prop, "after": after}


def tmpdir(prefix):
    return tempfile.TemporaryDirectory(prefix=prefix)
