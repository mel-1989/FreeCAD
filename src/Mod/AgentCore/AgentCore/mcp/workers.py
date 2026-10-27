# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCADCmd processes the MCP server drives (pure stdlib, no FreeCAD).

- ``SessionWorker``: one long-lived FreeCADCmd per open document, reached through
  a token-authenticated bridge on a private Unix socket.
- ``load_pool()``: F2's ``verify.Pool`` (isolated per-check workers) loaded from
  its file, so this process never imports FreeCAD through ``AgentCore.verify``.
"""

import importlib.util
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from .bridge import BridgeClient

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKER = os.path.join(_HERE, "session_worker.py")
AGENTCORE_PARENT = os.path.dirname(os.path.dirname(_HERE))  # dir that contains the AgentCore package
_POOL_PATH = os.path.join(os.path.dirname(_HERE), "verify", "pool.py")


def find_freecadcmd(explicit=None):
    """Explicit path, else CADFORK_FREECADCMD, else FreeCADCmd beside sys.executable or on PATH."""
    for cand in (explicit, os.environ.get("CADFORK_FREECADCMD")):
        if cand:
            return cand
    cand = os.path.join(os.path.dirname(sys.executable), "FreeCADCmd")
    if os.path.exists(cand):
        return cand
    found = shutil.which("FreeCADCmd")
    if found:
        return found
    raise FileNotFoundError("FreeCADCmd not found: pass --freecadcmd or set CADFORK_FREECADCMD")


def short_tempdir(prefix):
    """A temp dir whose path fits a Unix socket name (104 bytes on macOS)."""
    base = tempfile.gettempdir()
    if len(base) > 60 and os.path.isdir("/tmp"):
        base = "/tmp"
    return tempfile.mkdtemp(prefix=prefix, dir=base)


_POOL_MOD = None


def load_pool():
    global _POOL_MOD
    if _POOL_MOD is None:
        spec = importlib.util.spec_from_file_location("_agentcore_verify_pool", _POOL_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _POOL_MOD = mod
    return _POOL_MOD


def _tail(path, n=3000):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - n))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


class WorkerStartError(RuntimeError):
    pass


class SessionWorker:
    def __init__(self, freecadcmd, extra_env=None, policy_mode="flag", start_timeout=90.0):
        self.freecadcmd = freecadcmd
        self.dir = short_tempdir("acmcp-")
        self.socket = os.path.join(self.dir, "s")
        self.log = os.path.join(self.dir, "worker.log")
        self.token = secrets.token_hex(24)
        env = dict(os.environ)
        env.update(extra_env or {})
        pypath = [AGENTCORE_PARENT] + [p for p in env.get("CADFORK_MCP_PYTHONPATH", "").split(os.pathsep) if p]
        env.update({
            "QT_QPA_PLATFORM": "offscreen",
            "CADFORK_MCP_SOCKET": self.socket,
            "CADFORK_MCP_TOKEN": self.token,
            "CADFORK_MCP_PYTHONPATH": os.pathsep.join(pypath),
            "CADFORK_MCP_POLICY": policy_mode,
        })
        with open(self.log, "wb") as lf:
            self.proc = subprocess.Popen(
                [freecadcmd, "-c", f"exec(open({_WORKER!r}).read())"],
                stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT, env=env,
                start_new_session=True,
            )
        self.client = None
        deadline = time.time() + start_timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise WorkerStartError(f"session worker exited with {self.proc.returncode}: {_tail(self.log)}")
            if os.path.exists(self.socket):
                try:
                    self.client = BridgeClient(self.socket, self.token)
                    break
                except OSError:
                    pass
            time.sleep(0.05)
        if self.client is None:
            self.kill()
            raise WorkerStartError(f"session worker did not start in {start_timeout}s: {_tail(self.log)}")

    @property
    def pid(self):
        return self.proc.pid

    def alive(self):
        return self.proc.poll() is None

    def call(self, method, params=None, timeout=120.0):
        return self.client.call(method, params, timeout=timeout)

    def log_tail(self):
        return _tail(self.log)

    def exit_info(self):
        rc = self.proc.poll()
        return {"returncode": rc, "signal": -rc if rc is not None and rc < 0 else None}

    def kill(self):
        if self.client is not None:
            self.client.close()
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except OSError:
                self.proc.kill()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass

    def shutdown(self, timeout=10.0):
        if self.alive():
            try:
                self.call("shutdown", timeout=timeout)
                self.proc.wait(timeout=timeout)
            except Exception:
                pass
        self.kill()
        shutil.rmtree(self.dir, ignore_errors=True)
