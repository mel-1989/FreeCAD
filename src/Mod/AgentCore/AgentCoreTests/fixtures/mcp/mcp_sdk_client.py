# SPDX-License-Identifier: LGPL-2.1-or-later
"""Conformance client: drives cadfork-mcp with the official MCP Python SDK.

Run outside FreeCAD: uv run --no-project --with mcp python mcp_sdk_client.py CONFIG.json
CONFIG names the server env, the fixture and an output dir. Prints one JSON summary
line prefixed MCP_SDK_SUMMARY. Each connect mode ("auto": server/discover then the
initialize fallback, "legacy": initialize only) gets a fresh server process.
"""

import asyncio
import json
import os
import sys

from mcp import Client
from mcp.client.stdio import StdioServerParameters

EXPECTED_TOOLS = ["list_documents", "open_document", "get_state", "describe_entity", "get_selection", "exec_code",
                  "verify", "begin_change", "propose_change", "render", "import_export", "search_parts"]


async def session(cfg, mode):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "AgentCore.mcp", "--freecadcmd", cfg["freecadcmd"], "--parts-path", cfg["parts"]],
        env=cfg["env"],
    )
    rep = {"mode": mode, "calls": {}, "errors": []}
    async with Client(params, mode=mode, read_timeout_seconds=180) as c:
        rep["protocol_version"] = c.protocol_version
        rep["server"] = c.server_info.name if c.server_info else None
        tools = await c.list_tools()
        rep["tools"] = [t.name for t in tools.tools]

        async def call(name, **args):
            r = await c.call_tool(name, args)
            sc = r.structured_content
            text = json.loads(r.content[0].text)
            if sc != text:
                rep["errors"].append(f"{name}: structuredContent != text content")
            rep["calls"].setdefault(name, []).append({"is_error": r.is_error, "error": (sc or {}).get("error")})
            return sc, r.is_error

        out, _ = await call("open_document", path=cfg["fixture"])
        doc = out["doc"]
        await call("list_documents")
        st, _ = await call("get_state", doc=doc, page=0, page_size=10)
        rep["state_schema"] = st.get("schema")
        await call("describe_entity", doc=doc, entity="Box:Face1")
        await call("get_selection", doc=doc)
        await call("begin_change", doc=doc)
        ex, _ = await call("exec_code", doc=doc, code="doc.getObject('Box').Length = 15\nresult = doc.getObject('Box').Length")
        rep["exec_result"] = ex.get("result")
        v, _ = await call("verify", doc=doc, checks=["validity", "mass"])
        rep["verify_ok"] = v.get("ok")
        p, _ = await call("propose_change", doc=doc, summary="sdk")
        rep["proposal_status"] = p.get("status")
        await call("approve_change", proposal_id=p.get("proposal_id"))
        await call("render", doc=doc)
        await call("import_export", doc=doc, action="export", path=os.path.join(cfg["out"], f"sdk-{mode}.step"))
        await call("search_parts", query="box")

        res = await c.list_resources()
        rep["resource_count"] = len(res.resources)
        tpl = await c.list_resource_templates()
        rep["templates"] = [t.uri_template for t in tpl.resource_templates]
        reads = {}
        for uri in ("cadfork://cookbook", "cadfork://api/index", "cadfork://api/App/DocumentObject", "cadfork://state/schema"):
            r = await c.read_resource(uri)
            reads[uri] = len(r.contents[0].text)
        rep["reads"] = reads
        try:
            await c.read_resource("cadfork://api/No/Such")
            rep["missing_resource"] = "no error"
        except Exception as e:  # the SDK raises MCPError for a JSON-RPC error
            rep["missing_resource"] = type(e).__name__
        await c.send_ping()
    return rep


async def main():
    with open(sys.argv[1], encoding="utf-8") as fh:
        cfg = json.load(fh)
    import importlib.metadata as md

    out = {"sdk_version": md.version("mcp"), "python": sys.version.split()[0], "sessions": []}
    for mode in ("auto", "legacy"):
        out["sessions"].append(await session(cfg, mode))
    print("MCP_SDK_SUMMARY " + json.dumps(out, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
