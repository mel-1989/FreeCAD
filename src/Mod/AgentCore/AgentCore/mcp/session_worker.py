# SPDX-License-Identifier: LGPL-2.1-or-later
"""Session worker entry, executed inside FreeCADCmd: exec(open(this_file).read()).

Environment (set by ``workers.SessionWorker``):
- ``CADFORK_MCP_SOCKET``     Unix socket path to listen on
- ``CADFORK_MCP_TOKEN``      bridge auth token (never on argv)
- ``CADFORK_MCP_PYTHONPATH`` os.pathsep list prepended to sys.path (AgentCore's parent first)
- ``CADFORK_MCP_POLICY``     exec_code policy mode (flag|strict)
- ``CADFORK_MCP_DIFF_MODULE`` optional test hook naming a module with AgentTurn
- ``CADFORK_MCP_APPROVER_SOCKET`` optional socket of the human-side approver

Before any agent code can run, the process is sealed
(``authority.prepare_agent_process``): ``ApplyAuthority.install()`` refuses,
and tokens are verified by the approver, whose key never enters this process.
Without an approver every accept is refused.

Serves one document until told to shut down or until its parent (the MCP
server) dies. Ends with os._exit so FreeCADCmd never falls into its console.
"""

import os
import sys

for _p in reversed([p for p in os.environ.get("CADFORK_MCP_PYTHONPATH", "").split(os.pathsep) if p]):
    if _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)


def _main():
    for _m in [m for m in sys.modules if m == "AgentCore" or m.startswith("AgentCore.")]:
        del sys.modules[_m]  # a build-installed AgentCore must not shadow the server's copy
    from AgentCore.diff import authority
    from AgentCore.mcp.bridge import Bridge
    from AgentCore.mcp.session import Session

    approver = os.environ.get("CADFORK_MCP_APPROVER_SOCKET") or None
    authority.prepare_agent_process(approver)
    parent = os.getppid()
    session = Session(policy_mode=os.environ.get("CADFORK_MCP_POLICY", "flag"), approver_socket=approver)
    bridge = Bridge(session.handle, os.environ["CADFORK_MCP_TOKEN"], os.environ["CADFORK_MCP_SOCKET"])
    bridge.start()
    print("CADFORK_MCP_WORKER_READY pid=%d" % os.getpid(), flush=True)
    def watchdog():
        if approver:
            try:
                session.poll_approval()
            except Exception:
                import traceback

                traceback.print_exc()
        return os.getppid() == parent

    bridge.serve_forever(poll=0.5, watchdog=watchdog)


try:
    _main()
except BaseException:
    import traceback

    traceback.print_exc()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(3)
sys.stdout.flush()
sys.stderr.flush()
os._exit(0)
