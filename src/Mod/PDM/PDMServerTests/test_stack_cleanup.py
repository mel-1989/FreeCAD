# SPDX-License-Identifier: LGPL-2.1-or-later
"""Test-stack hygiene (run 033): the throwaway PostgreSQL / Forgejo / service processes of ``stack.Stack`` never
outlive their owner, and ``stack.sweep`` clears what a SIGKILL leaves while touching nothing that is not ours.

 1. sigterm_stops_the_whole_stack          SIGTERM to the owning process: every recorded process is gone, the directory too
 2. normal_exit_without_stop_stops_it      the owner exits without calling stop(): atexit stops the stack
 3. failed_start_stops_what_it_started     an exception after PostgreSQL came up (as in setUpClass) leaves nothing
 4. stop_is_idempotent
 5. sigkill_leak_is_swept                  a SIGKILLed owner leaks PostgreSQL; sweep() stops it and removes the directory
 6. sweep_leaves_foreign_and_live_alone    a foreign process, a stack of a live owner and an unmarked directory survive
 7. rest_api_sends_nosniff                 see test_server.TestAccess (REST responses carry X-Content-Type-Options)
 8. ascii_locale_prints_failures           test_web imports and prints non-ASCII under LC_ALL=C
"""

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from PDMServerTests.test_server import CMD, SERVER_SRC, SKIP, S

PY = os.path.join(S.VENV, "bin", "python") if S else None

DRIVER = """
import os, sys
sys.path.insert(0, {src!r})
import stack
st = stack.Stack({lib!r})
mode = sys.argv[1]
st.start_pg(initialise=True)
if mode == "forgejo":
    st.start_forgejo()
print("READY", st.root, flush=True)
if mode == "exit":
    sys.exit(0)
sys.stdin.read()  # wait to be killed
"""


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_dead(pids, seconds=20):
    end = time.time() + seconds
    while time.time() < end:
        if not any(alive(p) for p in pids):
            return True
        time.sleep(0.1)
    return False


@unittest.skipIf(SKIP, SKIP or "")
class TestStackCleanup(unittest.TestCase):
    def setUp(self):
        self.lib = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.cleanup = []

    def tearDown(self):
        for f in reversed(self.cleanup):
            with contextlib.suppress(Exception):
                f()

    def driver(self, mode):
        """Start the driver script (a stack with PostgreSQL, plus Forgejo for mode 'forgejo'); returns (proc, root, pids)."""
        p = subprocess.Popen([PY, "-c", DRIVER.format(src=SERVER_SRC, lib=self.lib), mode], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.cleanup.append(lambda: (p.poll() is None and p.kill(), p.wait()))
        line = p.stdout.readline()
        self.assertTrue(line.startswith("READY"), line + p.stderr.read()[-1500:] if p.poll() is not None else line)
        root = line.split()[1]
        self.cleanup.append(lambda: S.sweep(log=lambda m: None))
        with open(os.path.join(root, S.MARKER)) as f:
            marks = json.load(f)["procs"]
        self.assertIn("postgres", marks)
        return p, root, [m["pid"] for m in marks.values()]

    def test_01_sigterm_stops_the_whole_stack(self):
        p, root, pids = self.driver("forgejo")
        self.assertTrue(all(alive(x) for x in pids))
        p.send_signal(signal.SIGTERM)
        p.wait(60)
        self.assertEqual(p.returncode, -signal.SIGTERM)  # it still dies of the signal
        self.assertTrue(wait_dead(pids), f"survivors among {pids}")
        self.assertFalse(os.path.exists(root))

    def test_02_normal_exit_without_stop_stops_it(self):
        p, root, pids = self.driver("exit")
        p.wait(60)
        self.assertTrue(wait_dead(pids), f"survivors among {pids}")
        self.assertFalse(os.path.exists(root))

    def test_03_failed_start_stops_what_it_started(self):
        seen = []

        class Boom(S.Stack):
            def start_forgejo(self):
                seen.append(self._pg_pid())
                raise RuntimeError("boom after postgres")

        st = Boom(self.lib)
        self.cleanup.append(st.stop)
        with self.assertRaises(RuntimeError):
            st.start()
        self.assertTrue(seen and seen[0], "PostgreSQL was not up before the failure")
        self.assertTrue(wait_dead(seen))
        self.assertFalse(os.path.exists(st.root))

    def test_04_stop_is_idempotent(self):
        st = S.Stack(self.lib)
        st.start_pg(initialise=True)
        pid = st._pg_pid()
        self.assertTrue(alive(pid))
        st.stop()
        st.stop()
        self.assertTrue(wait_dead([pid]))
        self.assertFalse(os.path.exists(st.root))

    def test_05_sigkill_leak_is_swept(self):
        p, root, pids = self.driver("exit-not")
        p.kill()
        p.wait(30)
        self.assertTrue(any(alive(x) for x in pids), "SIGKILL should leave PostgreSQL behind (the leak under test)")
        removed = S.sweep(log=lambda m: None)
        self.assertIn(root, removed)
        self.assertTrue(wait_dead(pids), f"survivors among {pids}")
        self.assertFalse(os.path.exists(root))

    def test_06_sweep_leaves_foreign_and_live_alone(self):
        foreign = subprocess.Popen(["sleep", "300"], start_new_session=True)
        self.cleanup.append(lambda: (foreign.kill(), foreign.wait()))
        # a marker of a dead owner that names the foreign pid with a start time that does not match: not ours
        dead = tempfile.mkdtemp(prefix=S.PREFIX, dir="/tmp")
        with open(os.path.join(dead, S.MARKER), "w") as f:
            json.dump({"owner": 2 ** 22 + 12345, "owner_lstart": "Mon Jan  1 00:00:00 2001", "root": dead,
                       "procs": {"service": {"pid": foreign.pid, "pgid": foreign.pid, "lstart": "Mon Jan  1 00:00:00 2001"}}}, f)
        self.cleanup.append(lambda: shutil.rmtree(dead, ignore_errors=True))
        unmarked = tempfile.mkdtemp(prefix=S.PREFIX, dir="/tmp")
        self.cleanup.append(lambda: shutil.rmtree(unmarked, ignore_errors=True))
        live = S.Stack(self.lib)  # owner = this process, alive
        self.cleanup.append(live.stop)
        removed = S.sweep(log=lambda m: None)
        self.assertIn(dead, removed)
        self.assertNotIn(unmarked, removed)
        self.assertNotIn(live.root, removed)
        self.assertTrue(os.path.isdir(unmarked) and os.path.isdir(live.root))
        self.assertIsNone(foreign.poll(), "the foreign process was killed")

    def test_08_ascii_locale_prints_failures(self):
        env = dict(os.environ, LC_ALL="C", LANG="C", QT_QPA_PLATFORM="offscreen")
        code = "import PDMServerTests.test_web, sys\nsys.stderr.write('caf\\u00e9 \\u2713\\n')\n"
        p = subprocess.run([CMD, "-c", code], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-1500:])
        self.assertIn(b"caf", p.stderr)
        self.assertNotIn(b"UnicodeEncodeError", p.stderr + p.stdout)
