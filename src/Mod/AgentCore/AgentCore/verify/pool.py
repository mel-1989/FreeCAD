# SPDX-License-Identifier: LGPL-2.1-or-later
"""Pool of isolated FreeCADCmd worker processes for verify checks.

Each check runs in its own process (started in its own session so a timeout kills
the whole tree), so an OCCT segfault or abort costs one check, never the caller or
the pool. The pool is stateless between runs: it keeps serving after any crash.
"""

import concurrent.futures
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKER = os.path.join(_HERE, "worker.py")
_PYPATH = os.path.dirname(os.path.dirname(_HERE))  # dir containing the AgentCore package


def find_freecadcmd():
    """CADFORK_FREECADCMD, else FreeCADCmd beside sys.executable, else on PATH."""
    env = os.environ.get("CADFORK_FREECADCMD")
    if env:
        return env
    cand = os.path.join(os.path.dirname(sys.executable), "FreeCADCmd")
    if os.path.exists(cand):
        return cand
    found = shutil.which("FreeCADCmd")
    if found:
        return found
    raise FileNotFoundError("FreeCADCmd not found: set CADFORK_FREECADCMD")


def _tail(path, n=2000):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - n))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


class Pool:
    def __init__(self, size=2, timeout=120.0, freecadcmd=None):
        self.size = int(size)
        self.timeout = float(timeout)
        self.freecadcmd = freecadcmd or find_freecadcmd()
        self._live = {}
        self._lock = threading.Lock()
        self._exec = concurrent.futures.ThreadPoolExecutor(max_workers=self.size)

    # -- lifecycle
    def close(self):
        self._exec.shutdown(wait=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def live_pids(self):
        with self._lock:
            return list(self._live)

    # -- one check
    def run_check(self, fcstd_path, spec, timeout=None):
        name = spec if isinstance(spec, str) else spec.get("check")
        timeout = self.timeout if timeout is None else float(timeout)
        if not isinstance(spec, str) and "timeout" in spec:
            timeout = float(spec["timeout"])
        t0 = time.time()
        with tempfile.TemporaryDirectory(prefix="verify_") as tmp:
            out = os.path.join(tmp, "result.json")
            job = os.path.join(tmp, "job.json")
            with open(job, "w", encoding="utf-8") as fh:
                json.dump({"fcstd": fcstd_path, "check": spec, "out": out, "pythonpath": _PYPATH}, fh)
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", CADFORK_VERIFY_JOB=job)
            log = os.path.join(tmp, "worker.log")
            with open(log, "wb") as lf:
                proc = subprocess.Popen(
                    [self.freecadcmd, "-c", f"exec(open({_WORKER!r}).read())"],
                    stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT,
                    env=env, start_new_session=True,
                )
                with self._lock:
                    self._live[proc.pid] = name
                status = None
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    status = "timeout"
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except OSError:
                        proc.kill()
                    proc.wait()
                finally:
                    with self._lock:
                        self._live.pop(proc.pid, None)
            elapsed = round(time.time() - t0, 3)
            if status == "timeout":
                return {"check": name, "ok": False, "status": "timeout", "entities": [],
                        "details": {"timeout_s": timeout, "elapsed_s": elapsed, "log_tail": _tail(log)}}
            if os.path.exists(out):
                with open(out, encoding="utf-8") as fh:
                    res = json.load(fh)
                res["elapsed_s"] = elapsed
                return res
            rc = proc.returncode
            return {"check": name, "ok": False, "status": "crash", "entities": [],
                    "details": {"returncode": rc, "signal": -rc if rc < 0 else None,
                                "elapsed_s": elapsed, "log_tail": _tail(log)}}

    # -- many checks
    def run(self, fcstd_path, checks):
        """Run every check in its own worker (up to ``size`` at once).

        Returns ``{"status": "ok"|"crash"|"timeout"|"error", "ok": bool, "path",
        "results": [per-check result, input order]}``; status is the worst per-check status.
        """
        t0 = time.time()
        futs = [self._exec.submit(self.run_check, fcstd_path, s) for s in checks]
        results = [f.result() for f in futs]
        worst = "ok"
        for rank in ("error", "timeout", "crash"):
            if any(r["status"] == rank for r in results):
                worst = rank
        return {"status": worst, "ok": all(r["ok"] for r in results), "path": fcstd_path,
                "results": results, "elapsed_s": round(time.time() - t0, 3)}
