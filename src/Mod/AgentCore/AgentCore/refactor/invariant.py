# SPDX-License-Identifier: LGPL-2.1-or-later
"""``invariant(doc, fn)``: run an edit and prove the geometry did not change (F6)."""

import traceback

from . import geomhash


def invariant(doc, fn, *, tol=None, rollback=True, force_recompute=True, budget=150,
              max_deviation_bodies=40, measure_deviation=True):
    """Hash the geometry, run ``fn(doc)``, recompute, hash again, and fail on any change.

    Returns ``{"ok", "status", "before", "after", "violations", "result", "error",
    "rolled_back", "restored_identical", "max_deviation"}``.

    - ``fn`` may return a dict with ``removed`` (names of objects it deleted on
      purpose, never top-level bodies) and ``added`` (shape objects it created on
      purpose); everything else that changes is a violation.
    - With ``rollback`` (default), a failing or raising edit is undone through a
      document transaction and the restored geometry is verified against the
      "before" snapshot (``restored_identical``).
    - Comparison uses I3's tolerance policy and deviation measure; ``tol``
      overrides the policy dict (tests only).
    """
    tol = tol or geomhash.load_tolerances()
    before = geomhash.snapshot(doc, max_deviation_bodies=max_deviation_bodies if measure_deviation else 0)
    saved_undo = doc.UndoMode
    if rollback:
        doc.UndoMode = 1
        doc.openTransaction("agentcore.refactor.invariant")
    result, error = None, None
    try:
        result = fn(doc)
    except Exception:
        error = traceback.format_exc(limit=8)
    if force_recompute:
        doc.recompute(None, True)
    else:
        doc.recompute()
    after = geomhash.snapshot(doc, max_deviation_bodies=max_deviation_bodies if measure_deviation else 0)
    meta = result if isinstance(result, dict) else {}
    violations = geomhash.compare(
        before, after, tol, removed=meta.get("removed", ()), added=meta.get("added", ()), budget=budget,
    )
    ok = error is None and not violations
    out = {
        "ok": ok, "status": "pass" if ok else ("error" if error else "fail"),
        "before": before.summary(), "after": after.summary(),
        "violations": violations, "result": result, "error": error,
        "rolled_back": False, "restored_identical": None,
        "max_deviation": geomhash.max_deviation(before, after, budget) if (ok and measure_deviation) else None,
    }
    if rollback:
        if ok:
            doc.commitTransaction()
        else:
            doc.abortTransaction()
            doc.recompute(None, True)
            restored = geomhash.snapshot(doc, max_deviation_bodies=max_deviation_bodies if measure_deviation else 0)
            out["rolled_back"] = True
            out["restored_identical"] = not geomhash.compare(before, restored, tol, budget=budget)
        doc.UndoMode = saved_undo
    return out
