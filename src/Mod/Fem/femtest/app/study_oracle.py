# SPDX-License-Identifier: LGPL-2.1-or-later
"""Oracle for the G11 static study corpus (run 067): states pass or fail per brief checks 3 and 4, and why.

`probe_result(...)` runs inside FreeCAD (it reads a FemResultObject); `judge(...)` is plain Python and works on the JSON lines the driver wrote.
A `converged` verdict outside tolerance is reported as SILENT_WRONG, never as an ordinary failed case (brief check 4: a failed slice).

A run record (one JSON line) is {"case", "mode", "seconds", "results": [result dict, ...], "probes": [[{name: value} per level], ...], "error", "crash"}.
`results` has one entry per study run (four for group G, one otherwise); a result dict follows femstudy/SPEC.md.
"""

import json
import math
import sys

REACTION_TOL = 1e-3  # check 5
UNITS = {"length": "mm", "force": "N", "stress": "MPa", "energy": "mJ"}  # SPEC.md `units`
SINGULAR_GROWTH = 1.30  # check: max von Mises growth per refinement above this marks a mesh artefact
ALLOWED_UNCONVERGED_FRACTION = 0.15  # check 3


# ------------------------------------------------------------------------------------------------ probing (inside FreeCAD)
def _nodes(res):
    mesh = res.Mesh.FemMesh
    pts = mesh.Nodes
    ids = list(res.NodeNumbers)
    return ids, pts


def _tensor(res, i):
    return (res.NodeStressXX[i], res.NodeStressYY[i], res.NodeStressZZ[i], res.NodeStressXY[i], res.NodeStressYZ[i], res.NodeStressXZ[i])


def probe_result(case, doc, result_name, h):
    """Sample the case's stress probes from the result object `result_name` (a FemResultObject). Returns {probe name: value in MPa}."""
    out = {}
    probes = [s for s in case.truth.get("sigma", [])]
    if not probes:
        return out
    res = doc.getObject(result_name)
    ids, pts = _nodes(res)
    for s in probes:
        pr = s["probe"]
        vals, xs = [], []
        if pr["kind"] == "bend":
            ax = 2 if pr["load_axis"] == "z" else 1
            c = pr["c"]
            band = 0.5 * h if pr.get("support") else 1.0 * h
            for i, nid in enumerate(ids):
                p = pts[nid]
                co = (p.x, p.y, p.z)[ax]
                if abs(co) < 0.8 * c:
                    continue
                dx = p.x - pr["x"]
                if abs(dx) > (1e-6 * pr["x"] if pr.get("support") else band):
                    continue
                if pr.get("side") and co * pr["side"] < 0:
                    continue  # only the fibre opposite the load: the loaded fibre carries a local stress concentration
                sxx = res.NodeStressXX[i]
                vals.append(abs(sxx * c / co))
                xs.append(dx)
            if not vals:
                out[s["name"]] = None
                continue
            if pr.get("support") or len(vals) < 3 or max(xs) - min(xs) < 1e-9:
                out[s["name"]] = sum(vals) / len(vals)
            else:  # least-squares line in dx, evaluated at dx = 0
                n = len(vals)
                mx, my = sum(xs) / n, sum(vals) / n
                sxx_ = sum((x - mx) ** 2 for x in xs)
                slope = sum((x - mx) * (y - my) for x, y in zip(xs, vals)) / sxx_ if sxx_ > 0 else 0.0
                out[s["name"]] = my - slope * mx
        elif pr["kind"] == "hoop":
            R = pr["r"]
            for i, nid in enumerate(ids):
                p = pts[nid]
                if abs(p.z - pr["z"]) > 1e-6 or abs(math.hypot(p.x, p.y) - R) > 1e-3 * R:
                    continue
                th = math.atan2(p.y, p.x)
                sxx, syy, szz, sxy, syz, sxz = _tensor(res, i)
                vals.append(sxx * math.sin(th) ** 2 + syy * math.cos(th) ** 2 - 2 * sxy * math.sin(th) * math.cos(th))
            out[s["name"]] = sum(vals) / len(vals) if vals else None
        else:
            out[s["name"]] = None
    return out


# ------------------------------------------------------------------------------------------------ judging (plain Python)
def _rel(a, b):
    return abs(a - b) / abs(b) if b else abs(a - b)


def _vec_close(R, A, tol):
    na = math.sqrt(sum(x * x for x in A))
    d = math.sqrt(sum((r + a) ** 2 for r, a in zip(R, A)))
    return (d <= tol * na) if na > 0 else d <= tol, (d / na if na > 0 else d)


def primary(case):
    """Name and truth of the quantity used for the convergence and accuracy comparison."""
    t = case.truth
    if "disp" in t:
        return "max_displacement", t["disp"], t["tol_disp"]
    if t.get("sigma"):
        pick = next((s for s in t["sigma"] if s["name"] == t.get("primary")), t["sigma"][0])
        return pick["name"], pick["value"], pick["tol"]
    return None, None, None


def measure(case, run, level, which):
    """The case's quantity `which` on level index `level` of the first result."""
    res = run["results"][0]
    if which == "max_displacement":
        return res["levels"][level]["max_displacement"]
    pr = run.get("probes") or []
    return pr[level].get(which) if level < len(pr) else None


def within_tolerance(case, run):
    """True when every truth quantity of the finest level is within its stated tolerance (no verdict considered). Returns (ok, [details])."""
    res = run["results"][0]
    fine = len(res["levels"]) - 1
    det, ok = [], True
    t = case.truth
    if "disp" in t:
        v = res["levels"][fine]["max_displacement"]
        e = _rel(v, t["disp"])
        det.append(f"max_displacement {v:.6g} vs {t['disp']:.6g}: {e:.2e} (tol {t['tol_disp']:g})")
        ok &= e <= t["tol_disp"]
    if "max_vm" in t:
        v = res["levels"][fine]["max_von_mises"]
        e = _rel(v, t["max_vm"])
        det.append(f"max_von_mises {v:.6g} vs {t['max_vm']:.6g}: {e:.2e} (tol {t['tol_vm']:g})")
        ok &= e <= t["tol_vm"]
    for s in t.get("sigma", []):
        v = measure(case, run, fine, s["name"])
        if v is None:
            det.append(f"{s['name']}: not measurable")
            ok = False
            continue
        e = _rel(abs(v), s["value"])
        det.append(f"{s['name']} {abs(v):.6g} vs {s['value']:.6g}: {e:.2e} (tol {s['tol']:g})")
        ok &= e <= s["tol"]
    return ok, det


def richardson_error(u1, u2, u3, ratio=2.0):
    """Relative error of u3 against the Richardson limit for three levels with refinement ratio `ratio`; observed order p from the differences
    (clamped to [0.5, 4] when the sequence is monotone, 1.0 otherwise)."""
    d12, d23 = u2 - u1, u3 - u2
    if d23 == 0:
        return 0.0
    p = 1.0
    if d12 * d23 > 0 and abs(d23) < abs(d12):
        p = min(max(math.log(abs(d12) / abs(d23)) / math.log(ratio), 0.5), 4.0)
    limit = u3 + d23 / (ratio**p - 1)
    return abs(limit - u3) / abs(limit) if limit else 0.0


def _fail(chk, name, ok, detail):
    chk.append({"name": name, "ok": bool(ok), "detail": detail})
    return bool(ok)


def judge(case, run):
    """Returns {"case", "status": PASS | FAIL | SILENT_WRONG | MISS_ALLOWED, "why": str, "checks": [...]}."""
    chk = []
    out = {"case": case.id, "checks": chk}
    if run.get("crash") or run.get("error") or not run.get("results"):
        out["status"], out["why"] = "FAIL", f"no result: {run.get('error') or run.get('crash') or 'empty'}"
        return out
    res = run["results"][0]
    g = case.group
    v, code = res.get("verdict"), res.get("code")

    # units (check 4): every result carries the unit table, and it is the documented one
    if not _fail(chk, "units", res.get("units") == UNITS, f"units={res.get('units')}"):
        out["status"] = "SILENT_WRONG" if v == "converged" else "FAIL"
        out["why"] = "result units missing or wrong"
        return out

    if g in "ABCDEF":
        ok_tol, det = within_tolerance(case, run)
        _fail(chk, "accuracy", ok_tol, "; ".join(det))
        if v == "converged" and not ok_tol:
            out["status"], out["why"] = "SILENT_WRONG", "converged verdict outside tolerance: " + "; ".join(det)
            return out
        ok_v = _fail(chk, "verdict", v == "converged", f"verdict={v} code={code}")
        name, truth, tol = primary(case)
        if len(res["levels"]) >= 2 and name:
            e0 = abs(measure(case, run, 0, name) or 0) if name != "max_displacement" else res["levels"][0]["max_displacement"]
            e1 = abs(measure(case, run, len(res["levels"]) - 1, name) or 0) if name != "max_displacement" else res["levels"][-1]["max_displacement"]
            err0, err1 = _rel(e0, truth), _rel(e1, truth)
            _fail(chk, "error_shrinks", err1 <= max(err0, 0.1 * tol), f"err h={err0:.2e} err h/4={err1:.2e}")
        # the estimate itself: recompute the Richardson estimate of max_displacement from the three levels and compare (catches a wrong pair of levels)
        if len(res["levels"]) >= 3:
            u1, u2, u3 = (lv["max_displacement"] for lv in res["levels"][-3:])
            mine = richardson_error(u1, u2, u3)
            rep = res.get("estimated_error")
            _fail(chk, "estimate_recomputed", rep is not None and (abs(rep - mine) <= 0.35 * max(rep, mine) or max(rep, mine) < 2e-4), f"reported {rep}, recomputed {mine:.3e}")
        if v == "converged":
            _fail(chk, "estimate_within_target", res.get("estimated_error") is not None and res["estimated_error"] <= 0.01, f"estimated_error={res.get('estimated_error')}")
        if g in "ABCDE":
            od = res.get("order")
            ds = [lv["max_displacement"] for lv in res["levels"]]
            exact = len(ds) >= 2 and abs(ds[-1] - ds[-2]) <= 1e-9 * abs(ds[-1])  # an exact case (bars with nu = 0) has no order to report
            _fail(chk, "order>=1", exact or (od is not None and od >= 1.0), f"order={od}, exact={exact}")
        # equilibrium (check 5), every level
        for i, lv in enumerate(res["levels"]):
            okr, rel = _vec_close(lv["reaction_sum"], lv["applied_sum"], REACTION_TOL)
            _fail(chk, f"equilibrium_L{i}", okr, f"|R+A|/|A|={rel:.2e}")
        bad = [c for c in chk if not c["ok"] and c["name"] not in ("order>=1", "error_shrinks")]
        soft = [c for c in chk if not c["ok"] and c["name"] in ("order>=1", "error_shrinks")]
        if bad:
            out["status"], out["why"] = ("MISS_ALLOWED" if (not ok_v and ok_tol and not [c for c in bad if c["name"] != "verdict"]) else "FAIL"), "; ".join(c["name"] + ": " + c["detail"] for c in bad)
        elif soft:
            out["status"], out["why"] = "FAIL", "; ".join(c["name"] + ": " + c["detail"] for c in soft)
        else:
            out["status"], out["why"] = "PASS", "within tolerance, converged, equilibrium holds"
        return out

    if g == "G":
        if len(run["results"]) != 4:
            out["status"], out["why"] = "FAIL", f"{len(run['results'])} results, need 4"
            return out
        verdicts = {r.get("verdict") for r in run["results"]}
        _fail(chk, "all_converged", verdicts == {"converged"}, f"verdicts={sorted(map(str, verdicts))}")
        worst = 0.0
        for q in case.truth["quantities"]:
            vals = []
            for r in run["results"]:
                lv = r["levels"][-1]
                vals.append(lv[q])
            spread = (max(vals) - min(vals)) / abs(vals[0]) if vals[0] else 0.0
            worst = max(worst, spread)
            _fail(chk, f"agree_{q}", spread <= case.truth["agree_rel"], f"spread {spread:.2e}")
        bad = [c for c in chk if not c["ok"]]
        if bad:
            silent = verdicts == {"converged"}
            out["status"], out["why"] = ("SILENT_WRONG" if silent else "FAIL"), "; ".join(c["name"] + ": " + c["detail"] for c in bad)
        else:
            out["status"], out["why"] = "PASS", f"four spellings agree to {worst:.1e}"
        return out

    if g == "H":
        ok_v = _fail(chk, "verdict", v == "stress_singular" and code == "stress_singular", f"verdict={v} code={code}")
        lv = res["levels"]
        growth = [lv[i + 1]["max_von_mises"] / lv[i]["max_von_mises"] for i in range(len(lv) - 1) if lv[i]["max_von_mises"] > 0]
        _fail(chk, "stress_grows_last_step", bool(growth) and growth[-1] > SINGULAR_GROWTH, f"growth per refinement {[round(x, 3) for x in growth]}")
        est = res.get("estimated_error")
        tgt = 0.01
        _fail(chk, "displacement_converged", est is not None and est <= tgt, f"estimated_error={est}")
        if v == "converged":
            out["status"], out["why"] = "SILENT_WRONG", "singular case ended converged"
        elif ok_v and all(c["ok"] for c in chk):
            out["status"], out["why"] = "PASS", "stress_singular, displacement converged"
        else:
            out["status"], out["why"] = "FAIL", "; ".join(c["name"] + ": " + c["detail"] for c in chk if not c["ok"])
        return out

    if g == "I":
        ev, ec = case.expect["verdict"], case.expect["code"]
        ok = _fail(chk, "verdict_and_code", v == ev and code == ec, f"got {v}/{code}, want {ev}/{ec}")
        _fail(chk, "reason", bool(res.get("reason")), f"reason={res.get('reason')!r}")
        if v == "converged":
            out["status"], out["why"] = "SILENT_WRONG", f"expected {ev}/{ec}, ended converged"
        elif ok and all(c["ok"] for c in chk):
            out["status"], out["why"] = "PASS", f"{v}/{code}"
        else:
            out["status"], out["why"] = "FAIL", "; ".join(c["name"] + ": " + c["detail"] for c in chk if not c["ok"])
        return out

    if g == "J":
        if v == "refused":
            ok = _fail(chk, "names_lost_reference", code == "reference_lost" and bool(res.get("reason")), f"code={code} reason={res.get('reason')!r}")
            out["status"], out["why"] = ("PASS", "refused, naming the lost reference") if ok else ("FAIL", f"refused with code {code}")
            return out
        okt, det = within_tolerance(case, run)
        _fail(chk, "follows_geometry", okt, "; ".join(det))
        if okt and v == "converged":
            out["status"], out["why"] = "PASS", "followed the geometry: " + "; ".join(det)
        elif v == "converged":
            out["status"], out["why"] = "SILENT_WRONG", "solved a moved or wrong load: " + "; ".join(det)
        else:
            out["status"], out["why"] = "FAIL", f"verdict {v}/{code}"
        return out

    out["status"], out["why"] = "FAIL", "unknown group"
    return out


def summarize(rows, cases):
    by = {c.id: c for c in cases}
    judged = [judge(by[r["case"]], r) for r in rows if r["case"] in by]
    cnt = {}
    for j in judged:
        cnt[j["status"]] = cnt.get(j["status"], 0) + 1
    ab = [j for j in judged if by[j["case"]].group in "ABCDEF"]
    miss = [j for j in ab if j["status"] == "MISS_ALLOWED"]
    return judged, cnt, miss, len(ab)


def baseline_rows(rows, cases):
    """Per case: did the stock pipeline (no verdicts) answer within tolerance at the finest h? Only groups with a numeric truth are scored."""
    by = {c.id: c for c in cases}
    out = []
    for r in rows:
        c = by.get(r["case"])
        if not c:
            continue
        lv = (r.get("results") or [{}])[0].get("levels") or [{}]
        last = lv[-1]
        row = {"case": c.id, "group": c.group, "elements": last.get("elements"), "seconds": r.get("wall_seconds"), "error": r.get("error") or r.get("crash")}
        if c.group in "ABCDEF" or (c.group == "J" and not r.get("error")):
            if last.get("max_displacement") is not None:
                ok, det = within_tolerance(c, r)
                row.update(within=ok, detail="; ".join(det))
            else:
                row.update(within=False, detail=str(row["error"])[:120])
        out.append(row)
    return out


def main(argv):
    if len(argv) > 2 and argv[2] == "--baseline":
        sys.path.insert(0, __file__.rsplit("/", 1)[0])
        import study_corpus

        rows = [json.loads(x) for x in open(argv[1]) if x.strip()]
        for b in baseline_rows(rows, study_corpus.all_cases()):
            print(f"{b['case']}\t{b.get('within')}\tel={b.get('elements')}\t{b.get('seconds')}s\t{b.get('detail') or b.get('error') or ''}"[:260])
        return 0
    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    import study_corpus

    cases = study_corpus.all_cases()
    rows = {}
    for line in open(argv[1]):
        if line.strip():
            r = json.loads(line)
            if r.get("mode") == "study":
                rows[r["case"]] = r
    judged, cnt, miss, nab = summarize(list(rows.values()), cases)
    for j in judged:
        print(f"{j['case']}\t{j['status']}\t{j['why'][:160]}")
    print(cnt)
    print(f"A to F: {len(miss)} of {nab} judged cases are correct but not converged (allowed: {ALLOWED_UNCONVERGED_FRACTION:.0%})")
    return 0 if not cnt.get("SILENT_WRONG") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
