# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run inside FreeCADCmd as a sealed agent process (what the F4 session worker
is): forgery probes against the apply token, then the legitimate approval.

Environment: AGENTCORE_SRC (src/Mod/AgentCore), PROBE_APPROVER (socket or ""),
PROBE_OUT (directory for ids.json and result.json).
Executed with exec(open(path).read()); ends with os._exit.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.environ["AGENTCORE_SRC"])
out = os.environ["PROBE_OUT"]
res = {"probes": {}}


def probe(name, fn):
    try:
        fn()
        res["probes"][name] = "accepted"
    except Exception as exc:
        res["probes"][name] = f"refused: {type(exc).__name__}: {exc}"[:300]


def _main():
    from AgentCore.diff import authority as A
    approver = os.environ.get("PROBE_APPROVER") or None
    A.prepare_agent_process(approver)          # exactly what session_worker does first

    import FreeCAD as App
    from AgentCore.diff import AgentTurn

    doc = App.newDocument("Sealed")
    doc.UndoMode = 1
    box = doc.addObject("Part::Box", "Box")
    doc.recompute()
    with AgentTurn(doc, label="sealed", verify=False) as t:
        box.Length = 77
    res["status_after_propose"] = t.status

    # 1. install a private authority in the worker
    def install():
        A.ApplyAuthority.install()
    probe("install", install)

    # 2. object.__new__ token with a made-up MAC
    def forged_new():
        tok = object.__new__(A.ApplyToken)
        tok.turn_id, tok.digest, tok.nonce, tok.mac = t.turn_id, t.digest(), "00" * 16, "ab" * 32
        t.accept(tok)
    probe("object_new", forged_new)

    # 3. getattr with a computed name, so no literal 'accept' appears
    def getattr_accept():
        tok = object.__new__(A.ApplyToken)
        tok.turn_id, tok.digest, tok.nonce, tok.mac = t.turn_id, t.digest(), "11" * 16, "cd" * 32
        getattr(t, "acc" + "ept")(tok)
    probe("getattr_concat", getattr_accept)

    # 4. key search: every 32-byte bytes value in any closure or module global, tried as the HMAC key
    def closure_and_gc():
        sys.path.insert(0, os.path.dirname(os.path.abspath(os.environ["PROBE_FILE"])))
        import forge
        ok, n = forge.try_forge(t, A)
        res["closure_cells"] = sorted({type(c.cell_contents).__name__ for c in (A._VERIFIER.__closure__ or ())})
        res["key_candidates"] = n
        if not ok:
            raise A.TokenRefused(f"none of {n} candidate keys produced a token the verifier honours")
    probe("closure_gc_key", closure_and_gc)
    res["status_after_probes"] = t.status

    # 5. residual: replace the verifier outright (documented in SECURITY.md).
    #    It fools this process only; the approver's record does not move.
    if approver:
        from AgentCore.diff import approver as ap
        saved = A._VERIFIER
        try:
            A._VERIFIER = lambda *a: None
            tok = object.__new__(A.ApplyToken)
            tok.turn_id, tok.digest, tok.nonce, tok.mac = t.turn_id, t.digest(), "33" * 16, "ef" * 32
            local = t.accept(tok)
            res["residual_local_accept"] = local["decision"]
            res["residual_approver_status"] = ap.request(approver, {"op": "status", "turn_id": t.turn_id,
                                                                     "digest": t.digest()})["status"]
            t.status = "proposed"               # back to pending for the legitimate path (state unchanged)
        finally:
            A._VERIFIER = saved

        # 6. the legitimate path: a human approves in the approver, the worker polls and accepts
        with open(os.path.join(out, "ids.json"), "w") as fh:
            json.dump({"turn_id": t.turn_id, "digest": t.digest()}, fh)
        deadline = time.time() + 60
        token = None
        while time.time() < deadline and token is None:
            token = ap.request(approver, {"op": "poll", "turn_id": t.turn_id, "digest": t.digest()}).get("token")
            if token is None:
                time.sleep(0.1)
        res["polled"] = token is not None
        if token is not None:
            r = t.accept(A.ApplyToken.from_wire(token))
            res["legit_decision"] = r["decision"]
            res["length_after"] = float(box.Length.Value)
            res["approver_status_after"] = ap.request(approver, {"op": "status", "turn_id": t.turn_id,
                                                                  "digest": t.digest()})["status"]
            try:
                t.status = "proposed"
                t.accept(A.ApplyToken.from_wire(token))
                res["replay"] = "accepted"
            except Exception as exc:
                res["replay"] = f"refused: {exc}"


try:
    _main()
except BaseException as exc:
    import traceback
    res["crash"] = traceback.format_exc()
with open(os.path.join(out, "result.json"), "w") as fh:
    json.dump(res, fh, indent=1)
sys.stdout.flush()
os._exit(0)
