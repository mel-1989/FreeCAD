# SPDX-License-Identifier: LGPL-2.1-or-later
"""Human-side approver process: the only holder of the apply key.

The approver owns the ``ApplyAuthority`` (and so the HMAC key). Agent
workers, sealed with ``authority.prepare_agent_process(socket)``, reach it on
a Unix socket that answers exactly three requests, none of which mints:

- ``check``  (turn_id, digest, token): is this token valid for this change
  set? A valid token is spent. This is how the worker's ``accept`` verifies.
- ``poll``   (turn_id, digest): the token for this exact change set if the
  human approved it, else ``null``. The worker polls and then accepts.
- ``status`` (turn_id, digest): ``pending`` | ``approved`` | ``denied`` |
  ``spent``. The approver's record, not a worker's ``turn.status``, says what
  a human approved.

Approval itself comes only from the human: ``Approver.approve(turn_id,
digest)``, called by the GUI change-set panel, or a line ``approve <turn_id>
<digest>`` typed on this process's stdin when run headless::

    python3 approver.py --socket /path/to/approver.sock

Pure stdlib, no FreeCAD; runs under any Python 3.8+, or FreeCADCmd.
"""

import json
import os
import socket
import stat
import sys
import threading

try:
    from . import authority as _authority
except ImportError:  # run as a script: load the sibling file without the FreeCAD-importing package
    import importlib.util

    _spec = importlib.util.spec_from_file_location(
        "_agentcore_authority", os.path.join(os.path.dirname(os.path.abspath(__file__)), "authority.py"))
    _authority = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_authority)

PROTOCOL = "agentcore.approver/1"
OPS = ("check", "poll", "status")


class Approver:
    def __init__(self):
        self._auth = _authority.ApplyAuthority.install()
        self._lock = threading.Lock()
        self._approved = {}   # (turn_id, digest) -> ApplyToken
        self._denied = set()
        self._spent = set()

    # -- human side (GUI or stdin), never on the socket
    def approve(self, turn_id, digest):
        key = (str(turn_id), str(digest))
        with self._lock:
            if key in self._spent:
                raise ValueError(f"turn {turn_id} with this change set was already applied")
            tok = self._auth.mint_for(*key)
            self._approved[key] = tok
            self._denied.discard(key)
            return tok.to_wire()

    def deny(self, turn_id, digest):
        key = (str(turn_id), str(digest))
        with self._lock:
            self._approved.pop(key, None)
            self._denied.add(key)

    # -- socket side
    def status(self, turn_id, digest):
        key = (turn_id, digest)
        with self._lock:
            if key in self._spent:
                return "spent"
            if key in self._approved:
                return "approved"
            if key in self._denied:
                return "denied"
            return "pending"

    def poll(self, turn_id, digest):
        with self._lock:
            tok = self._approved.get((turn_id, digest))
            return tok.to_wire() if tok is not None else None

    def check(self, wire, turn_id, digest):
        tok = _authority.ApplyToken.from_wire(wire)
        self._auth.check(tok, turn_id, digest)  # raises TokenRefused; spends the nonce
        with self._lock:
            self._approved.pop((turn_id, digest), None)
            self._spent.add((turn_id, digest))

    def handle(self, req):
        op = req.get("op") if isinstance(req, dict) else None
        if op not in OPS:
            return {"ok": False, "reason": f"unknown approver op {op!r}; approval is not available on this channel"}
        turn_id, digest = req.get("turn_id"), req.get("digest")
        if not isinstance(turn_id, str) or not isinstance(digest, str):
            return {"ok": False, "reason": "turn_id and digest must be strings"}
        if op == "status":
            return {"ok": True, "status": self.status(turn_id, digest)}
        if op == "poll":
            return {"ok": True, "token": self.poll(turn_id, digest)}
        try:
            self.check(req.get("token"), turn_id, digest)
        except _authority.TokenRefused as exc:
            return {"ok": False, "reason": str(exc)}
        return {"ok": True}


class ApproverServer:
    """Line-JSON Unix socket (mode 0600) in front of an ``Approver``."""

    def __init__(self, approver, address):
        self.approver = approver
        self.address = address
        self._sock = None
        self.stopped = threading.Event()

    def start(self):
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
        s.listen(8)
        self._sock = s
        threading.Thread(target=self._loop, name="approver-accept", daemon=True).start()
        return self.address

    def _loop(self):
        while not self.stopped.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            f = conn.makefile("rb")
            for line in f:
                try:
                    resp = self.approver.handle(json.loads(line))
                except ValueError:
                    resp = {"ok": False, "reason": "invalid JSON"}
                conn.sendall((json.dumps(resp) + "\n").encode())
        except OSError:
            pass
        finally:
            conn.close()

    def close(self):
        self.stopped.set()
        if self._sock is not None:
            self._sock.close()
        try:
            os.unlink(self.address)
        except OSError:
            pass


def request(address, req, timeout=2.0):
    """One request to an approver socket (used by workers and tests)."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(address)
        s.sendall((json.dumps(req) + "\n").encode())
        return json.loads(s.makefile("rb").readline())


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(prog="agentcore-approver", description=__doc__.split("\n")[0])
    ap.add_argument("--socket", required=True, help="Unix socket path to listen on")
    args = ap.parse_args(argv)
    approver = Approver()
    server = ApproverServer(approver, os.path.abspath(args.socket))
    server.start()
    print(f"CADFORK_APPROVER_READY {server.address}", flush=True)
    try:
        for line in sys.stdin:
            parts = line.split()
            if not parts:
                continue
            cmd = parts[0]
            try:
                if cmd == "approve" and len(parts) == 3:
                    approver.approve(parts[1], parts[2])
                    print(f"APPROVED {parts[1]}", flush=True)
                elif cmd == "deny" and len(parts) == 3:
                    approver.deny(parts[1], parts[2])
                    print(f"DENIED {parts[1]}", flush=True)
                elif cmd == "status" and len(parts) == 3:
                    print(f"STATUS {parts[1]} {approver.status(parts[1], parts[2])}", flush=True)
                elif cmd == "quit":
                    break
                else:
                    print("ERROR usage: approve|deny|status <turn_id> <digest>, or quit", flush=True)
            except Exception as exc:
                print(f"ERROR {type(exc).__name__}: {exc}", flush=True)
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    rc = main()
    sys.stdout.flush()
    os._exit(rc)
