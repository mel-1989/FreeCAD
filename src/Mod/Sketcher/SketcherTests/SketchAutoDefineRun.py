# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2026 The FreeCAD project association AISBL              *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful, but        *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU     *
# *   Lesser General Public License for more details.                      *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************

"""Runs strip-and-recover cases through ``propose`` and the oracle.

Imported by the tests (a small subset) and run as a script for the whole
corpus::

    AUTODEFINE_OUT=out.json AUTODEFINE_VARIANTS=9 AUTODEFINE_REAL=all FreeCADCmd SketchAutoDefineRun.py

(FreeCADCmd treats every command-line argument as a file to open, so the
options are environment variables.)

The script writes one JSON record per (case, strip level) and prints the
rates.  Every record is a plain dict, see ``evaluate``."""

import json
import os
import sys
import time

import FreeCAD

import SketcherAutoDefine as AD
from SketcherTests import SketchAutoDefineCorpus as Corpus
from SketcherTests import SketchAutoDefineOracle as O

LEVELS = (("dims", None), ("all", None))
PARTIAL = ((("partial"), 0.25), (("partial"), 0.5), (("partial"), 0.75))


def _drop(doc, *objs):
    for o in objs:
        try:
            doc.removeObject(o.Name)
        except Exception:
            pass


def evaluate(doc, case, level, fraction=0.5, seed=0, edit=True, max_candidates=3):
    """Strip `case`, propose, and judge every candidate.  Returns a record."""
    orig = case.sketch
    stripped, kept = Corpus.strip(doc, orig, level, fraction, seed)
    rec = {
        "case": case.name,
        "family": case.family,
        "real": case.real,
        "rotated": case.rotated,
        "level": level if level != "partial" else "partial%d" % round(fraction * 100),
        "geometry": len(orig.Geometry),
        "removed": len(orig.Constraints) - len(kept),
        "candidates": 0,
        "oracle_failures": [],
        "status": None,
        "seconds": 0.0,
    }
    clones = [stripped]
    try:
        stripped.solve()
        rows = [O.row(c) for c in stripped.Constraints]
        coords = O.coordinates(stripped)
        rec["dof0"] = stripped.DoF
        t0 = time.time()
        res = AD.propose(stripped, keep=list(range(len(rows))), max_candidates=max_candidates)
        rec["seconds"] = time.time() - t0
        rec["status"] = res["status"]
        rec["candidates"] = len(res["candidates"])
        rec["schemes"] = [c["scheme"] for c in res["candidates"]]
        rec["unclosed"] = res["unclosed"]
        rec["datums_original"] = sum(1 for c in orig.Constraints if c.Type in O.DATUMS and c.Driving)
        keys = set()
        for k, cand in enumerate(res["candidates"]):
            key = frozenset((c["type"], tuple(c["args"][:-1] if c["role"] == "dimension" else c["args"])) for c in cand["constraints"])
            if key in keys:
                rec["oracle_failures"].append("candidate %d duplicates an earlier one" % k)
            keys.add(key)
            t = Corpus.clone(doc, stripped, "Applied")
            clones.append(t)
            rows = [O.row(c) for c in t.Constraints]  # the clone's own values: a text round trip can change the last digit
            coords = O.coordinates(t)
            try:
                AD.apply_candidate(t, cand)
            except Exception as exc:
                rec["oracle_failures"].append("candidate %d: apply: %s" % (k, exc))
                continue
            problems = O.check_applied(t, rows, coords)
            if problems:
                rec["oracle_failures"].append("candidate %d: %s" % (k, "; ".join(problems)))
            if k == 0:
                rec["datums_candidate"] = cand["dimensions"]
                rec["relations_candidate"] = cand["relations"]
                if level == "all":
                    rec["medium"] = O.medium_report(list(orig.Constraints), list(t.Constraints)[len(rows):])
                if level == "all":
                    rel = O.original_relations(orig)
                    rec["relations_total"] = len(rel)
                    rec["relations_types"] = {}
                    if edit and not problems:
                        ch, ok, rest, held = O.perturb(t, _datums_of(t), rel)
                        rec["relations_enforced"] = sum(1 for v in held.values() if v)
                        rec["relations_missed"] = [orig.Constraints[i].Type for i, v in held.items() if not v]
                        rec["edit_changes"], rec["edit_solved"], rec["edit_restored"] = ch, ok, rest
                    elif edit:
                        rec["relations_enforced"] = 0
                if level in ("all", "dims") and edit and not problems and "edit_changes" not in rec:
                    ch, ok, rest, _ = O.perturb(t, _datums_of(t))
                    rec["edit_changes"], rec["edit_solved"], rec["edit_restored"] = ch, ok, rest
        if edit and level == "all":
            o2 = Corpus.clone(doc, orig, "OrigEdit")
            clones.append(o2)
            ch, ok, rest, _ = O.perturb(o2, _datums_of(o2))
            rec["orig_edit_changes"], rec["orig_edit_solved"] = ch, ok
    finally:
        _drop(doc, *clones)
    return rec


def _datums_of(sk):
    return O._driving_datums(sk)


def summarize(recs):
    """Rates per level and per real/generated for a list of records."""
    out = {}
    for level in sorted({r["level"] for r in recs}):
        for kind in ("generated", "real", "all"):
            rs = [r for r in recs if r["level"] == level and (kind == "all" or (r["real"] == (kind == "real")))]
            if not rs:
                continue
            rec_ok = sum(1 for r in rs if r["candidates"] and not r["oracle_failures"])
            out["%s/%s" % (level, kind)] = {
                "cases": len(rs),
                "recovered": rec_ok,
                "rate": round(rec_ok / len(rs), 4),
                "oracle_failures": sum(len(r["oracle_failures"]) for r in rs),
                "offered": sum(r["candidates"] for r in rs),
            }
    return out


def main():
    out_path = os.environ["AUTODEFINE_OUT"]
    variants = int(os.environ.get("AUTODEFINE_VARIANTS", "9"))
    real = os.environ.get("AUTODEFINE_REAL", "all") != "none"
    doc = FreeCAD.newDocument("AutoDefineRun")
    recs = []
    t0 = time.time()
    cases = Corpus.generated(doc, variants=variants) + Corpus.slice1_cases(doc)
    cache = {}
    if real:
        rc, missing = Corpus.real_cases(cache)
        cases += rc
        print("real sketches: %d opened, %d missing %s" % (len(rc), len(missing), missing))
    print("cases: %d (built in %.1f s)" % (len(cases), time.time() - t0), flush=True)
    for n, case in enumerate(cases):
        levels = [("dims", 0.0, 0), ("all", 0.0, 0)]
        levels += [("partial", f, s) for s, f in enumerate((0.25, 0.5, 0.75))]
        for level, frac, seed in levels:
            try:
                rec = evaluate(case.doc if case.real else doc, case, level, fraction=frac, seed=seed)
            except Exception as exc:  # recorded, never skipped
                rec = {"case": case.name, "family": case.family, "real": case.real, "rotated": case.rotated, "level": level if level != "partial" else "partial%d" % round(frac * 100), "candidates": 0, "oracle_failures": ["exception: %r" % (exc,)], "status": "exception"}
            recs.append(rec)
        if n % 25 == 0:
            print("  %d/%d  %.0f s" % (n, len(cases), time.time() - t0), flush=True)
        json.dump(recs, open(out_path, "w"))
    json.dump({"records": recs, "summary": summarize(recs), "seconds": time.time() - t0}, open(out_path, "w"))
    for k, v in summarize(recs).items():
        print(k, v)


if os.environ.get("AUTODEFINE_OUT") and os.path.basename(sys.argv[0] if sys.argv else "").startswith("SketchAutoDefineRun"):
    main()
