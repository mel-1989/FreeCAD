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

"""Planted conflicts and redundancies in one, two and all components of large
sketches (G10 slice 3a, check 7).

    PLANTED_OUT=planted.json FreeCADCmd SketchPlantedScale.py

For each size (1,000 and 5,000 constraints) and each placement (one
rectangle, two, the first and last, every one) and kind (conflict, redundant,
both) it builds the decoupled series sketch with the planted constraints,
solves it and compares the four lists with the analytic truth: the conflict
set of every conflict plant, and one reported redundant constraint inside
the pair of every redundancy plant.  At 1,000 constraints the whole-system
path (partition threshold 0) is run as well and must agree exactly; the
partitioned run uses threshold 1 there, because 1,000 constraints are below
the default threshold."""

import json
import os
import sys
import time

import FreeCAD

_here = os.path.dirname(os.path.abspath(__file__))
for _p in (_here, os.path.dirname(_here)):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from SketcherTests import SketchDiagnosisCorpus as Corpus

ENV = "FREECAD_GCS_PARTITION_THRESHOLD"


def rects(n):
    return n // 12


def placements(n):
    k = rects(n)
    return {
        "one": [k // 3],
        "two": [k // 8, (3 * k) // 4],
        "ends": [0, k - 1],
        "every": list(range(k)),
    }


def run_case(n, plants, threshold):
    old = os.environ.get(ENV)
    if threshold is not None:
        os.environ[ENV] = str(threshold)
    try:
        doc = FreeCAD.newDocument("Planted", "Planted", True, True)
        sk, info = Corpus.build_scale(doc, "planted", n, chained=False, plants=plants)
        t0 = time.perf_counter()
        code = sk.solve()
        elapsed = time.perf_counter() - t0
        rec = {
            "code": code,
            "dof": sk.DoF,
            "conflicting": sorted(sk.ConflictingConstraints),
            "redundant": sorted(sk.RedundantConstraints),
            "partial": sorted(sk.PartiallyRedundantConstraints),
            "seconds": elapsed,
        }
        FreeCAD.closeDocument(doc.Name)
        return rec, info
    finally:
        if threshold is not None:
            if old is None:
                os.environ.pop(ENV, None)
            else:
                os.environ[ENV] = old


def judge(rec, info):
    """Problems of `rec` against the analytic truth of the plants in `info`."""
    problems = []
    conflicts = [p for p in info["plants"] if p["kind"] == "conflict"]
    redundants = [p for p in info["plants"] if p["kind"] == "redundant"]
    truth = sorted(i + 1 for p in conflicts for i in p["set"])
    if rec["conflicting"] != truth:
        problems.append("conflicting %d, expected %d" % (len(rec["conflicting"]), len(truth)))
    pairs = [{i + 1 for i in p["set"]} for p in redundants]
    reported = set(rec["redundant"])
    if len(reported) != len(pairs) or any(len(reported & pair) != 1 for pair in pairs):
        problems.append("redundant %r does not hit each pair once" % sorted(reported)[:6])
    if (conflicts or redundants) and not (conflicts or rec["redundant"]):
        problems.append("nothing reported")
    return problems


def main():
    out = []
    for n in (1000, 5000):
        for where, rs in placements(n).items():
            for kind in ("conflict", "redundant", "both"):
                if kind == "both":
                    if len(rs) < 2:
                        continue
                    plants = [(m, "conflict" if i % 2 == 0 else "redundant") for i, m in enumerate(rs)]
                else:
                    plants = [(m, kind) for m in rs]
                rec, info = run_case(n, plants, 1 if n == 1000 else None)  # 1,000 constraints are below the default threshold
                row = {"constraints": n, "placement": where, "kind": kind, "plants": len(plants), "problems": judge(rec, info), "seconds": round(rec["seconds"], 3)}
                if n == 1000:
                    whole, _ = run_case(n, plants, 0)
                    row["matches_whole_system"] = {k: whole[k] for k in ("code", "dof", "conflicting", "redundant", "partial")} == {k: rec[k] for k in ("code", "dof", "conflicting", "redundant", "partial")}
                    row["whole_system_seconds"] = round(whole["seconds"], 3)
                out.append(row)
                print(json.dumps(row), flush=True)
    with open(os.environ.get("PLANTED_OUT", "planted_scale.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    bad = [r for r in out if r["problems"] or r.get("matches_whole_system") is False]
    print("cases %d, with problems %d" % (len(out), len(bad)), flush=True)
    os._exit(0)


# FreeCADCmd runs a script under its file name rather than "__main__".
if __name__ in ("__main__", os.path.splitext(os.path.basename(__file__))[0]):
    if os.environ.get("PLANTED_NO_RUN") != "1":
        main()
