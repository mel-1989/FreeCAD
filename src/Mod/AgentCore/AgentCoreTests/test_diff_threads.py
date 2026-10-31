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

# V11 w.py list nested / list end: a thread is refused (RuntimeError) while another thread's nested
# abort or end() close runs Python onChanged outside the turn mutex. The refused set must leave the
# property fully usable: recorded by transactions and signalled again.
REFUSED_SET = PRELUDE + """
WHERE = "__WHERE__"; PROP = "__PROP__"
ev_in = threading.Event(); ev_out = threading.Event()
state = {"armed": False, "fired": 0, "changed": 0}
class P2:
    def __init__(self, o): o.Proxy = self
    def onChanged(self, o, p):
        if p == PROP: state["changed"] += 1
        if state["armed"] and p == "L" and o.Name == "A":
            state["armed"] = False; state["fired"] += 1
            ev_in.set()
            state["released"] = ev_out.wait(20)       # GIL released while waiting
    def execute(self, o): pass
    def dumps(self): return {"n": 0}
    def loads(self, s): pass
d = App.newDocument("RS"); d.UndoMode = 1
o = {}
for n in ("A", "B", "C"):
    o[n] = d.addObject("App::FeaturePython", n); P2(o[n])
    o[n].addProperty("App::PropertyLength", "L")
o["B"].addProperty("App::PropertyFloatList", "FL"); o["B"].addProperty("App::PropertyLinkList", "LL")
d.recompute()
def get(): return list(o["B"].FL) if PROP == "FL" else [x.Name for x in o["B"].LL]
INIT = __INIT__; NEW = __NEW__; CHK = __CHK__; COMMIT = __COMMIT__
setattr(o["B"], PROP, INIT)
res = {}
def thread_b():
    if not ev_in.wait(30): res["b"] = "never signalled"; return
    try:
        setattr(o["B"], PROP, NEW); res["b"] = "ok"
    except Exception as e:
        res["b"] = "%s: %s" % (type(e).__name__, e)
    finally:
        ev_out.set()
tb = threading.Thread(target=thread_b); tb.start()
d.beginAgentTurn("t")
o["A"].L = 1
if WHERE == "nested":
    d.openTransaction("n"); o["A"].L = 5; state["armed"] = True
    d.abortTransaction()
else:
    o["A"].L = 5; state["armed"] = True
    d.endAgentTurn(False)
tb.join()
assert state["fired"] == 1, state
assert res["b"].startswith("RuntimeError"), res          # B was refused
if WHERE == "nested":
    d.endAgentTurn(False)
before = get()
assert before == [x if isinstance(x, float) or isinstance(x, str) else x.Name for x in INIT], before
n0 = state["changed"]
d.openTransaction("chk"); setattr(o["B"], PROP, CHK); d.abortTransaction()
assert get() == before, ("set+abort did not restore", get(), before)
assert state["changed"] - n0 >= 2, ("onChanged did not fire", state["changed"] - n0)
d.openTransaction("chk2"); setattr(o["B"], PROP, COMMIT); d.commitTransaction()
d.undo()
assert get() == before, ("set+commit+undo did not restore", get(), before)
d.beginAgentTurn("t2"); setattr(o["B"], PROP, CHK); d.endAgentTurn(False)
assert get() == before, ("turn reject did not restore", get(), before)
App.closeDocument("RS")
print("OK", res["b"])
"""

REFUSED_SET_FLOAT = ("[1.0]", "[2.0, 3.0]", "[7.0, 8.0, 9.0]", "[5.0]")
REFUSED_SET_LINK = ("[o['C']]", "[o['A'], o['C']]", "[o['A']]", "[o['A'], o['B']]")

# V11 del_probe: a Python object's __del__ (which gives up the GIL) must not run under the turn mutex.
DEL_UNDER_MUTEX = PRELUDE + """
dels = [0]
class V:
    def __init__(self, k): self.k = k
    def __del__(self):
        dels[0] += 1
        time.sleep(0.2)
d = App.newDocument("DEL"); d.UndoMode = 1
o = d.addObject("App::FeaturePython", "O"); o.addProperty("App::PropertyPythonObject", "PO")
o.addProperty("App::PropertyLength", "L")
o2 = d.addObject("App::FeaturePython", "O2"); o2.addProperty("App::PropertyLength", "L")
d.recompute()
o.PO = V(1)
d.openTransaction("t1"); o.PO = V(2); d.commitTransaction()
d.undo()
d.beginAgentTurn("t")
go = threading.Event(); stop = [False]; n = [0]
def setter():
    go.wait()
    while not stop[0]:
        try: o2.L = 1 + n[0] % 5
        except Exception: pass
        n[0] += 1
t = threading.Thread(target=setter, daemon=True); t.start()
go.set(); time.sleep(0.001)
o.L = 3
time.sleep(0.5)
stop[0] = True; t.join(20)
assert not t.is_alive()
d.endAgentTurn(True)
App.closeDocument("DEL")
print("OK", n[0], dels[0])
"""

# Close guard (run 021): another thread closing a document while one of its operations is mid-way
# must get a RuntimeError, never a crash. The operation's Python callback (onChanged) parks the
# running thread on the GIL, so the closer gets in while the document is half-rolled-back.
CLOSE_DURING = PRELUDE + '''
MODE = "__MODE__"
class Slow:
    def __init__(self, o): self.arm = False; o.Proxy = self
    def onChanged(self, o, p):
        if self.arm and p == "L":
            started.set(); time.sleep(0.4)
    def execute(self, o): pass
    def dumps(self): return {"n": 0}
    def loads(self, s): pass
started = threading.Event()
d = App.newDocument("CL")
objs = []
for i in range(3):
    o = d.addObject("App::FeaturePython", "O%d" % i); o.addProperty("App::PropertyLength", "L"); objs.append(o)
slow = Slow(objs[0])
d.recompute()
if MODE in ("nested", "end"):
    d.beginAgentTurn("t")
    objs[0].L = 5
    if MODE == "nested":
        d.openTransaction("n"); objs[0].L = 9
else:
    d.openTransaction("plain"); objs[0].L = 9
errs = []
def closer():
    started.wait(10)
    try: App.closeDocument("CL"); errs.append(None)
    except Exception as e: errs.append(e)
t = threading.Thread(target=closer); t.start()
slow.arm = True
if MODE == "end":
    d.endAgentTurn(False)
else:
    d.abortTransaction()
t.join()
slow.arm = False
assert len(errs) == 1 and isinstance(errs[0], RuntimeError), errs
assert "in progress" in str(errs[0]) or "busy" in str(errs[0]), errs
assert App.getDocument("CL") is not None
if MODE == "nested":
    d.endAgentTurn(False)
# A refused close must not brick the document: the closing flag is lifted again.
d.openTransaction("after"); objs[1].L = 3; d.commitTransaction(); d.recompute()
d.beginAgentTurn("u"); objs[1].L = 4; d.endAgentTurn(False)
assert objs[1].L == 3, objs[1].L
App.closeDocument("CL")
assert "CL" not in App.listDocuments()
print("OK", errs[0])
'''

# Run 023, the reverse race: thread A closes (a __del__ run while the document is destroyed
# sleeps); thread B starts an operation meanwhile. B must get a RuntimeError, not write into the
# freed DocumentP. A wrapper that outlives the close must give a ReferenceError.
CLOSE_TOCTOU = PRELUDE + '''
indel = threading.Event(); res = {}
class Dying:
    def __del__(self):
        indel.set(); time.sleep(0.5)
d = App.newDocument("TC")
o = d.addObject("App::FeaturePython", "O"); Prox(o)
o.addProperty("App::PropertyPythonObject", "PO"); o.PO = Dying()
o.addProperty("App::PropertyLength", "L")
d.recompute()
bound = d.recompute          # a bound method captured before the close
del o
OPS = {
    "openTransaction": lambda: d.openTransaction("x"),
    "commitTransaction": lambda: d.commitTransaction(),
    "abortTransaction": lambda: d.abortTransaction(),
    "recompute": lambda: d.recompute(),
    "beginAgentTurn": lambda: d.beginAgentTurn("t"),
}
def worker():
    indel.wait(10)
    for name, op in OPS.items():
        try: op(); res[name] = "ok"
        except Exception as e: res[name] = type(e).__name__ + ":" + str(e)
t = threading.Thread(target=worker); t.start()
App.closeDocument("TC")
t.join()
assert "TC" not in App.listDocuments()
for name in OPS:
    assert res[name].startswith(("RuntimeError:", "ReferenceError:")), (name, res[name])
    assert "closed" in res[name] or "deleted" in res[name], (name, res[name])
# After the close finished: the wrapper is invalid, every route gives a Python error.
for name, op in list(OPS.items()) + [("bound", bound)]:
    try: op(); raise SystemExit("no error from %s after the close" % name)
    except ReferenceError: pass
late = []
def late_worker():
    try: d.openTransaction("late")
    except ReferenceError as e: late.append("ReferenceError")
t = threading.Thread(target=late_worker); t.start(); t.join()
assert late == ["ReferenceError"], late
print("OK", res)
'''

# A close from inside signalDeleteDocument (slotDeletedDocument in Python) is refused: it used to
# free the document twice.
CLOSE_DOUBLE = PRELUDE + '''
res = []
d = App.newDocument("DC")
o = d.addObject("App::FeaturePython", "O"); Prox(o)
class Obs:
    def slotDeletedDocument(self, doc):
        try: App.closeDocument(doc.Name); res.append("inner closed")
        except Exception as e: res.append("inner refused:%s" % e)
        try: App.closeDocument(doc); res.append("inner closed")
        except Exception as e: res.append("inner refused:%s" % e)
obs = Obs(); App.addDocumentObserver(obs)
App.closeDocument("DC")
App.removeDocumentObserver(obs)
assert "DC" not in App.listDocuments()
assert len(res) == 2 and all(r.startswith("inner refused:") and "being closed" in r for r in res), res
print("OK", res)
'''

# A proxy __del__ that closes its own document while the redo stack is cleared (also crashes upstream).
CLOSE_IN_DEL = PRELUDE + '''
state = []
class Del:
    def __del__(self):
        try: App.closeDocument("DL"); state.append("closed")
        except Exception as e: state.append(type(e).__name__)
d = App.newDocument("DL")
o = d.addObject("App::FeaturePython", "O"); Prox(o)
o.addProperty("App::PropertyPythonObject", "PO"); o.addProperty("App::PropertyLength", "L")
o.PO = {"a": 1}
d.openTransaction("t1"); o.PO = Del(); d.commitTransaction()
d.undo()          # the redo stack now holds the Del instance
assert not state
d.openTransaction("t2"); o.L = 3   # clearing the redo stack frees Del -> __del__
d.commitTransaction()
assert state == ["RuntimeError"], state
assert App.getDocument("DL") is not None
App.closeDocument("DL")
print("OK", state)
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

    def _refused(self, where, kind):
        init, new, chk, commit = REFUSED_SET_FLOAT if kind == "FL" else REFUSED_SET_LINK
        code = (REFUSED_SET.replace("__WHERE__", where).replace("__PROP__", kind).replace("__INIT__", init)
                .replace("__NEW__", new).replace("__CHK__", chk).replace("__COMMIT__", commit))
        self.assertIn("OK", self._run(code, scribble=True))

    def test_refused_set_leaves_float_list_usable_nested_abort(self):
        self._refused("nested", "FL")

    def test_refused_set_leaves_float_list_usable_end_close(self):
        self._refused("end", "FL")

    def test_refused_set_leaves_link_list_usable_nested_abort(self):
        self._refused("nested", "LL")

    def test_refused_set_leaves_link_list_usable_end_close(self):
        self._refused("end", "LL")

    def test_python_del_does_not_run_under_the_turn_mutex(self):
        self.assertIn("OK", self._run(DEL_UNDER_MUTEX))

    def test_close_is_refused_during_a_nested_abort(self):
        self.assertIn("OK", self._run(CLOSE_DURING.replace("__MODE__", "nested"), scribble=True))

    def test_close_is_refused_during_end_of_turn(self):
        self.assertIn("OK", self._run(CLOSE_DURING.replace("__MODE__", "end"), scribble=True))

    def test_close_is_refused_during_a_plain_abort(self):
        self.assertIn("OK", self._run(CLOSE_DURING.replace("__MODE__", "plain"), scribble=True))

    def test_del_that_closes_its_document_during_a_redo_clear_is_refused(self):
        self.assertIn("OK", self._run(CLOSE_IN_DEL, scribble=True))

    def test_operation_that_starts_during_a_close_is_refused_and_a_late_wrapper_errors(self):
        for _ in range(3):
            self.assertIn("OK", self._run(CLOSE_TOCTOU, scribble=True))

    def test_close_from_inside_the_delete_observer_is_refused(self):
        self.assertIn("OK", self._run(CLOSE_DOUBLE, scribble=True))


if __name__ == "__main__":
    unittest.main()
