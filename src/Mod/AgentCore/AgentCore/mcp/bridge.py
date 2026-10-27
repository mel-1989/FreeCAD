# SPDX-License-Identifier: LGPL-2.1-or-later
"""Token-authenticated line-JSON bridge into a FreeCAD process.

One bridge runs inside each FreeCAD process the MCP server drives: a headless
``FreeCADCmd`` session worker (``session_worker.py``) or, later, the GUI
(``start_in_gui``). Socket I/O happens on background threads; every request is
handed to the thread that owns FreeCAD (the main thread) through a queue, because
the FreeCAD document API is not thread-safe. That is the main-thread marshalling.

Wire format: one JSON object per line.
request  ``{"id": n, "token": str, "method": str, "params": {}}``
response ``{"id": n, "result": ...}`` or ``{"id": n, "error": {"code", "message", ...}}``

Every request must carry the token (compared in constant time); a wrong token
gets an ``auth`` error and the connection is closed. The token reaches a
headless worker through its environment, never its argv.

Pure stdlib: the client half (``BridgeClient``) is used by the MCP server, which
does not import FreeCAD.
"""

import hmac
import json
import os
import queue
import socket
import stat
import threading
import traceback

BRIDGE_PROTOCOL = "agentcore.bridge/1"


class BridgeError(Exception):
    """Structured error raised by a handler; ``code`` is a short machine-readable tag."""

    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self):
        return {"code": self.code, "message": self.message, **self.details}


def _send(sock, obj):
    data = (json.dumps(obj, default=_json_default, sort_keys=True) + "\n").encode("utf-8")
    sock.sendall(data)


def _json_default(o):
    for attr in ("to_dict", "toDict"):
        if hasattr(o, attr):
            try:
                return getattr(o, attr)()
            except Exception:
                pass
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    return str(o)


class Bridge:
    """Server half: listens, authenticates, and queues calls for the main thread."""

    def __init__(self, handler, token, address):
        if not token or len(token) < 16:
            raise ValueError("bridge token must be at least 16 characters")
        self.handler = handler
        self.token = token
        self.address = address
        self.calls = queue.Queue()
        self.stopped = threading.Event()
        self._sock = None

    # -- socket side (background threads)
    def start(self):
        if isinstance(self.address, str):
            try:
                os.unlink(self.address)
            except FileNotFoundError:
                pass
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            old = os.umask(0o177)
            try:
                s.bind(self.address)
            finally:
                os.umask(old)
            os.chmod(self.address, stat.S_IRUSR | stat.S_IWUSR)
        else:
            host, port = self.address
            if host not in ("127.0.0.1", "::1", "localhost"):
                raise ValueError("the bridge listens on loopback only")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind((host, port))
            self.address = s.getsockname()
        s.listen(4)
        self._sock = s
        threading.Thread(target=self._accept_loop, name="bridge-accept", daemon=True).start()
        return self.address

    def _accept_loop(self):
        while not self.stopped.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._reader, args=(conn,), name="bridge-conn", daemon=True).start()

    def _reader(self, conn):
        try:
            f = conn.makefile("rb")
            for line in f:
                try:
                    req = json.loads(line)
                except ValueError:
                    _send(conn, {"id": None, "error": {"code": "parse", "message": "invalid JSON"}})
                    continue
                tok = req.get("token")
                if not isinstance(tok, str) or not hmac.compare_digest(tok.encode(), self.token.encode()):
                    _send(conn, {"id": req.get("id"), "error": {"code": "auth", "message": "bad or missing bridge token"}})
                    return
                slot = {"req": req, "done": threading.Event(), "resp": None}
                self.calls.put(slot)
                slot["done"].wait()
                _send(conn, slot["resp"])
                if req.get("method") == "shutdown":
                    return
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    # -- FreeCAD side (main thread)
    def _execute(self, slot):
        req = slot["req"]
        rid = req.get("id")
        try:
            method = req.get("method")
            params = req.get("params") or {}
            if not isinstance(method, str) or not isinstance(params, dict):
                raise BridgeError("bad_request", "method must be a string and params an object")
            result = self.handler(method, params)
            resp = {"id": rid, "result": result}
            json.dumps(resp, default=_json_default)
        except BridgeError as e:
            resp = {"id": rid, "error": e.to_dict()}
        except Exception as e:  # handler bug or FreeCAD exception: report, keep serving
            resp = {"id": rid, "error": {"code": "internal", "message": f"{type(e).__name__}: {e}",
                                         "traceback": traceback.format_exc(limit=8)}}
        slot["resp"] = resp
        slot["done"].set()
        if req.get("method") == "shutdown":
            self.stopped.set()

    def drain(self, block=False, timeout=None):
        """Execute queued calls on the calling thread; return how many ran."""
        n = 0
        while True:
            try:
                slot = self.calls.get(block=block and n == 0, timeout=timeout if block and n == 0 else None)
            except queue.Empty:
                return n
            self._execute(slot)
            n += 1

    def serve_forever(self, poll=0.5, watchdog=None):
        """Main-thread loop for a headless worker. ``watchdog()`` returning False ends it."""
        while not self.stopped.is_set():
            self.drain(block=True, timeout=poll)
            if watchdog is not None and not watchdog():
                break
        self.close()

    def close(self):
        self.stopped.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if isinstance(self.address, str):
            try:
                os.unlink(self.address)
            except OSError:
                pass

    def start_in_gui(self, interval_ms=50):
        """Drain the queue from a Qt timer on the GUI main thread.

        Untested on the host by design (no GUI launches there); covered by I4's VM.
        """
        from PySide import QtCore  # noqa: F401  (FreeCAD's PySide shim, GUI builds only)

        self.start()
        self._timer = QtCore.QTimer()
        self._timer.timeout.connect(lambda: self.drain(block=False))
        self._timer.start(interval_ms)
        return self.address


class BridgeClient:
    """Client half used by the MCP server. One call in flight at a time."""

    def __init__(self, address, token, connect_timeout=5.0):
        self.address = address
        self.token = token
        self._lock = threading.Lock()
        self._next = 0
        fam = socket.AF_UNIX if isinstance(address, str) else socket.AF_INET
        self._sock = socket.socket(fam, socket.SOCK_STREAM)
        self._sock.settimeout(connect_timeout)
        self._sock.connect(address if isinstance(address, str) else tuple(address))
        self._file = self._sock.makefile("rb")

    def call(self, method, params=None, timeout=60.0):
        """Return the result, or raise ``BridgeError`` (remote error) / ``ConnectionError``
        (peer died) / ``TimeoutError``."""
        with self._lock:
            self._next += 1
            rid = self._next
            self._sock.settimeout(timeout)
            try:
                _send(self._sock, {"id": rid, "token": self.token, "method": method, "params": params or {}})
                line = self._file.readline()
            except socket.timeout:
                raise TimeoutError(f"bridge call {method!r} exceeded {timeout}s")
            except OSError as e:
                raise ConnectionError(f"bridge connection lost during {method!r}: {e}")
            if not line:
                raise ConnectionError(f"bridge connection closed during {method!r}")
            resp = json.loads(line)
            if "error" in resp:
                err = dict(resp["error"])
                code = err.pop("code", "internal")
                msg = err.pop("message", "")
                raise BridgeError(code, msg, **err)
            return resp.get("result")

    def close(self):
        try:
            self._file.close()
            self._sock.close()
        except OSError:
            pass
