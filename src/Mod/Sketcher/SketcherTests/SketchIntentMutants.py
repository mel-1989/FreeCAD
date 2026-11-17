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

"""Mutation check for ``SketcherIntent`` (F11 slice 1).

Each planted defect is a text replacement in a copy of ``SketcherIntent.py``
(or of ``SketcherAutoDefine.py`` for the hook into it).  The copy is put first
on ``sys.path`` of a fresh ``FreeCADCmd`` run of ``TestSketchIntent``; a mutant
is caught when a test fails or raises.  Options are environment variables::

    MUT_OUT=out_dir [MUT_ONLY="name ..."] [MUT_DRY=1] FreeCADCmd SketchIntentMutants.py
"""

import json
import os
import subprocess
import sys
import tempfile

import FreeCAD

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(HERE)
M = []


def mutant(name, desc, old, new, target="SketcherIntent.py"):
    M.append((name, desc, target, old, new))


mutant("ranker_reversed", "the ranker orders the kinds backwards", "key=lambda t: t[0])]", "key=lambda t: -t[0])]")
mutant("ranker_off", "the ranker keeps generation order", "key=lambda t: t[0])]", "key=lambda t: 0)]")
mutant("hook_ignored", "the intent schemes fall through to the baseline order", '    if scheme in _Intent.INTENT_SCHEMES:\n        # design-intent order first', '    if False:\n        # design-intent order first', "SketcherAutoDefine.py")
mutant("no_nested_offsets", "pieces nested inside another are never offset from it", "        nested = _nested_ids(model)\n        movers &= nested", "        nested = _nested_ids(model)\n        movers = set()")
mutant("no_overall_size_for_frames", "the outer outline of a frame gets no overall width and height", "        if nested:\n            # a frame or wall profile", "        if False:\n            # a frame or wall profile")
mutant("no_gap_dimension", "a small unjoined gap is not dimensioned", "    pool += _gap_proposals(model)", "    pass")
mutant("no_carrier", "construction lines carry no dimension", '        if m.construction and _is_line(m) and _is_hv(m) and first(m):', '        if False:')
mutant("no_sizes", "circle diameters and arc radii are not proposed first", '        if m.kind == "circle" and first(m):', '        if False:')
mutant("equal_group_sized_twice", "every member of an equal group is sized", "        root = find(m.i)\n        if root in sized:\n            return False", "        root = m.i\n        if root in sized:\n            return False")
mutant("angle_sign_flipped", "the neighbour angle is measured the other way round", "    return v\n\n\ndef _angle_neighbour", "    return -v\n\n\ndef _angle_neighbour")
mutant("angle_never_offered", "no angle against a neighbouring line", "    for m in lines:\n        if _is_hv(m):\n            continue\n        best = None", "    for m in []:\n        best = None")
mutant("extent_wrong_axis", "the overall height is measured along x", '    for axis, kind in ((0, "DistanceX"), (1, "DistanceY")):\n        ext = _extent_points', '    for axis, kind in ((0, "DistanceX"), (1, "DistanceX")):\n        ext = _extent_points')
mutant("duplicates_kept", "the same dimension in two spellings is proposed twice", "        if key in seen:\n            continue\n        seen.add(key)\n        out.append(spec)", "        out.append(spec)")
mutant("construction_dimensioned_as_drawn", "construction geometry counts as drawn geometry", "    return [m for m in model if not m.construction and m.pts and not m.internal]", "    return [m for m in model if m.pts and not m.internal]")

RUNNER = """
import sys, unittest, json
mutdir, srcdir, outfile = sys.argv[-3:]
sys.path.insert(0, mutdir)
sys.path.insert(1, srcdir)
import FreeCAD
import SketcherIntent
import SketcherAutoDefine
assert SketcherIntent.__file__.startswith(mutdir) or SketcherAutoDefine.__file__.startswith(mutdir), (SketcherIntent.__file__, SketcherAutoDefine.__file__)
from SketcherTests.TestSketchIntent import TestSketchIntent
names = unittest.defaultTestLoader.getTestCaseNames(TestSketchIntent)
suite = unittest.TestSuite(TestSketchIntent(n) for n in names)
res = unittest.TextTestRunner(verbosity=0, stream=open("/dev/null", "w")).run(suite)
def name(t):
    return t.id().split(".")[-1] if hasattr(t, "id") else str(t)
json.dump({"ran": res.testsRun, "failed": [name(t) for t, _ in res.failures], "raised": [name(t) for t, _ in res.errors]}, open(outfile, "w"))
"""


def main():
    out = os.environ["MUT_OUT"]
    only = os.environ.get("MUT_ONLY", "").split() or None
    fc = os.environ.get("FC") or os.path.join(FreeCAD.getHomePath(), "bin", "FreeCADCmd")
    os.makedirs(out, exist_ok=True)
    rows = []
    for name, desc, target, old, new in M:
        if only and name not in only:
            continue
        sources = {t: open(os.path.join(SRC_DIR, t), encoding="utf-8").read() for t in ("SketcherIntent.py", "SketcherAutoDefine.py")}
        if sources[target].count(old) != 1:
            rows.append({"mutant": name, "description": desc, "status": "pattern", "failed": [], "raised": []})
            print(name, "PATTERN ERROR (%d matches)" % sources[target].count(old), flush=True)
            continue
        if os.environ.get("MUT_DRY"):
            print(name, "pattern ok", flush=True)
            continue
        sources[target] = sources[target].replace(old, new)
        d = tempfile.mkdtemp(prefix="intent_mutant_")
        for t, text in sources.items():
            open(os.path.join(d, t), "w", encoding="utf-8").write(text)
        open(os.path.join(d, "run.py"), "w").write(RUNNER)
        rf = os.path.join(d, "result.json")
        subprocess.run([fc, os.path.join(d, "run.py"), d, SRC_DIR, rf], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            r = json.load(open(rf))
        except Exception:
            r = {"ran": 0, "failed": [], "raised": ["<run crashed>"]}
        caught = bool(r["failed"] or r["raised"])
        rows.append({"mutant": name, "description": desc, "status": "caught" if caught else "SURVIVED", "failed": r["failed"], "raised": r["raised"]})
        print(name, rows[-1]["status"], r["failed"] or r["raised"], flush=True)
        json.dump(rows, open(os.path.join(out, "mutants.json"), "w"), indent=1)
    if not os.environ.get("MUT_DRY"):
        print("%d of %d caught" % (sum(1 for r in rows if r["status"] == "caught"), len(rows)))


if os.environ.get("MUT_OUT") and __name__ in ("__main__", "SketchIntentMutants"):
    main()
