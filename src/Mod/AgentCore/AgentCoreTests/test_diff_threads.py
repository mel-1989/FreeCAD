# SPDX-License-Identifier: LGPL-2.1-or-later
"""F3: lock order GIL, then turn mutex (run 020). Each scenario runs in a child FreeCADCmd under a
wall-clock limit, so a deadlock fails the test instead of hanging the suite, and a crash (use after
free) shows as a negative return code."""

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

import FreeCAD as App

HAVE_API = hasattr(App.Document, "getAgentTurnChanges")
LIMIT = 150

PRELUDE = '''
import sys, threading, time, faulthandler, FreeCAD as App
faulthandler.enable(all_threads=True)
sys.setswitchinterval(1e-5)
class Prox:
    def __init__(self, o): o.Proxy = self
    def onChanged(self, o, p):
        s = 0
        for i in range(100): s += i
    def execute(self, o): pass
    def dumps(self): return {"n": 0}
    def loads(self, s): pass
'''

# V9 p_fp1: Python-object properties compared while a second thread sets properties.
PY_OBJECT_COMPARE = PRELUDE + '''
d = App.newDocument("FP")
objs = []
for i in range(300):
    o = d.addObject("App::FeaturePython", "O%d" % i); Prox(o)
    o.addProperty("App::PropertyPythonObject", "PO"); o.addProperty("App::PropertyLength", "L")
    o.PO = {"i": i}; objs.append(o)
d.recompute()
d.beginAgentTurn("t")
for i, o in enumerate(objs):
    o.PO = {"i": i, "big": list(range(50))}; o.L = i + 1
stop = False; n = [0]
def worker():
    while not stop:
        d.getAgentTurnChanges(True); n[0] += 1
t = threading.Thread(target=worker); t.start()
t0 = time.time(); k = 0
while time.time() - t0 < 3:
    o = objs[k % 300]; o.L = 1000 + k; o.PO = {"k": k}; k += 1
stop = True; t.join()
assert n[0] > 0
d.endAgentTurn(False); App.closeDocument("FP")
print("OK", n[0], k)
'''

# V9 p_abort: a nested abort fires onChanged Python while a second thread reads changes.
NESTED_ABORT = PRELUDE + '''
d = App.newDocument("AB")
objs = []
for i in range(100):
    o = d.addObject("App::FeaturePython", "O%d" % i); Prox(o); o.addProperty("App::PropertyLength", "L"); objs.append(o)
d.recompute()
d.beginAgentTurn("t")
objs[0].L = 5
stop = False; n = [0]
def worker():
    while not stop:
        d.getAgentTurnChanges(True); n[0] += 1
t = threading.Thread(target=worker); t.start()
t0 = time.time(); k = 0
while time.time() - t0 < 3:
    d.openTransaction("n")
    for o in objs[2:]: o.L = 100 + k
    d.abortTransaction(); k += 1
stop = True; t.join()
assert n[0] > 0 and k > 0
d.endAgentTurn(False); App.closeDocument("AB")
print("OK", n[0], k)
'''

# V10 q_eq2: two readers whose __eq__ gives up the GIL, a setter, and a closer.
CLOSE_RACE = PRELUDE + '''
class V:
    def __init__(self, k): self.k = k
    def __eq__(self, other):
        time.sleep(0.0005)
        return isinstance(other, V) and other.k == self.k
    __hash__ = None
for rnd in range(4):
    d = App.newDocument("EQ")
    objs = []
    for i in range(60):
        o = d.addObject("App::FeaturePython", "O%d" % i)
        o.addProperty("App::PropertyPythonObject", "PO"); o.addProperty("App::PropertyLength", "L")
        o.PO = V(i); objs.append(o)
    d.recompute()
    d.beginAgentTurn("t")
    for i, o in enumerate(objs): o.PO = V(i)
    stop = False
    def getter(js):
        while not stop:
            try: d.getAgentTurnChanges(js)
            except Exception: pass
    def setter():
        k = 0
        while not stop:
            try:
                o = objs[k % 60]; o.L = k; o.PO = V(k % 60)
            except Exception: pass
            k += 1
    ts = [threading.Thread(target=getter, args=(True,)), threading.Thread(target=getter, args=(False,)),
          threading.Thread(target=setter)]
    [t.start() for t in ts]
    time.sleep(0.7)
    try: App.closeDocument("EQ")
    except Exception: pass
    time.sleep(0.3)
    stop = True; [t.join() for t in ts]
print("OK")
'''


def _freecadcmd():
    home = App.getHomePath()
    for cand in (os.path.join(home, "bin", "FreeCADCmd"), os.path.join(os.path.dirname(sys.executable), "FreeCADCmd")):
        if os.path.exists(cand):
            return cand
    return None


@unittest.skipUnless(HAVE_API, "built without doc.getAgentTurnChanges")
class TestTurnMutexLockOrder(unittest.TestCase):
    def _run(self, code, scribble=False):
        exe = _freecadcmd()
        if not exe:
            self.skipTest("FreeCADCmd not found")
        env = dict(os.environ)
        if scribble:
            env["MallocScribble"] = "1"
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "scenario.py")
            with open(script, "w") as f:
                f.write(textwrap.dedent(code))
            try:
                r = subprocess.run([exe, script], cwd=tmp, env=env, capture_output=True, text=True, timeout=LIMIT)
            except subprocess.TimeoutExpired as e:
                self.fail("deadlock: the scenario did not finish in %d s\n%s" % (LIMIT, (e.stdout or "")[-2000:]))
        self.assertEqual(r.returncode, 0, "child failed (negative: killed by a signal)\n%s\n%s" % (r.stdout[-2000:], r.stderr[-2000:]))
        return r.stdout

    def test_python_object_compare_does_not_deadlock_a_setter(self):
        self.assertIn("OK", self._run(PY_OBJECT_COMPARE))

    def test_nested_abort_does_not_deadlock_a_reader(self):
        self.assertIn("OK", self._run(NESTED_ABORT))

    def test_closing_the_document_while_threads_wait_does_not_crash(self):
        self.assertIn("OK", self._run(CLOSE_RACE, scribble=True))


if __name__ == "__main__":
    unittest.main()
