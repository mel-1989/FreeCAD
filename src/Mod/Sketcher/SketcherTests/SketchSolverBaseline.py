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

"""Solver-core baseline and equivalence record (G10 slice 3a, checks 1 and 6).

Run it with ``FreeCADCmd SketchSolverBaseline.py``.  Environment variables:

``BASELINE_OUT``     output JSON (default ``solver_baseline.json`` in the current directory)
``BASELINE_GROUPS``  comma list of ``s1``, ``s2``, ``real2``, ``golden`` (default all)
``BASELINE_GOLDEN``  manifest file listing FCStd paths relative to ``BASELINE_ROOT`` (for ``golden``)
``BASELINE_ROOT``    root of the manifest paths
``BASELINE_EXTRA``   directories whose ``*.FCStd`` files are added to the ``golden`` group
``BASELINE_SCALE``   comma list of scale sizes for ``s1`` (default ``10,100,1000,5000``)

Per sketch the record holds the return code of ``SketchObject.solve()``, the
DOF, the sorted conflicting, redundant, partially redundant and malformed
constraint lists, and every solved coordinate.  ``compare(a, b)`` applies the
equivalence bar of check 6: same code, DOF and lists exactly, coordinates
within 1e-9 mm.
"""

import json
import os
import sys
import time

try:
    import FreeCAD
except ImportError:  # compare() also works in a plain Python
    FreeCAD = None

_here = os.path.dirname(os.path.abspath(__file__))
for _p in (_here, os.path.dirname(_here)):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

COORD_TOLERANCE = 1e-9

_ATTRS = (
    "Radius",
    "MajorRadius",
    "MinorRadius",
    "FirstParameter",
    "LastParameter",
    "X",
    "Y",
)


def _vec(v):
    return [v.x, v.y, v.z]


def geometry_record(sketch):
    """Every solved coordinate of every geometry, as plain lists."""
    out = []
    for g in sketch.Geometry:
        rec = {"t": type(g).__name__}
        for name in ("StartPoint", "EndPoint", "Center", "Location"):
            if hasattr(g, name):
                rec[name] = _vec(getattr(g, name))
        for name in _ATTRS:
            if hasattr(g, name):
                rec[name] = float(getattr(g, name))
        if hasattr(g, "getPoles"):
            rec["poles"] = [_vec(p) for p in g.getPoles()]
            rec["weights"] = [float(w) for w in g.getWeights()]
        if hasattr(g, "MajorAxis"):
            rec["MajorAxis"] = _vec(g.MajorAxis)
        out.append(rec)
    return out


def record(sketch):
    t0 = time.perf_counter()
    code = sketch.solve()
    elapsed = time.perf_counter() - t0
    return {
        "code": code,
        "dof": sketch.DoF,
        "conflicting": sorted(sketch.ConflictingConstraints),
        "redundant": sorted(sketch.RedundantConstraints),
        "partial": sorted(sketch.PartiallyRedundantConstraints),
        "malformed": sorted(sketch.MalformedConstraints),
        "geometry": geometry_record(sketch),
        "constraints": len(sketch.Constraints),
        "seconds": elapsed,
    }


def _flat(rec):
    for k, v in rec.items():
        if isinstance(v, list):
            for i, x in enumerate(v):
                if isinstance(x, list):
                    for j, y in enumerate(x):
                        yield (k, i, j), y
                else:
                    yield (k, i), x
        elif isinstance(v, float):
            yield (k,), v


def compare(a, b, tol=COORD_TOLERANCE):
    """Differences between two baseline JSON dicts, as a list of
    ``(label, what, detail)``.  A missing or extra label is a difference."""
    diffs = []
    for label in sorted(set(a) | set(b)):
        if label not in a or label not in b:
            diffs.append((label, "missing", "in " + ("b" if label in a else "a")))
            continue
        x, y = a[label], b[label]
        for key in ("code", "dof", "conflicting", "redundant", "partial", "malformed", "constraints"):
            if x.get(key) != y.get(key):
                diffs.append((label, key, "%r -> %r" % (x.get(key), y.get(key))))
        gx, gy = x.get("geometry", []), y.get("geometry", [])
        if len(gx) != len(gy):
            diffs.append((label, "geometry count", "%d -> %d" % (len(gx), len(gy))))
            continue
        worst = 0.0
        for i, (p, q) in enumerate(zip(gx, gy)):
            if p["t"] != q["t"]:
                diffs.append((label, "geometry type", "%d" % i))
                continue
            fp, fq = dict(_flat(p)), dict(_flat(q))
            if fp.keys() != fq.keys():
                diffs.append((label, "geometry shape", "%d" % i))
                continue
            for k in fp:
                worst = max(worst, abs(fp[k] - fq[k]))
        if worst > tol:
            diffs.append((label, "coordinates", "max %.3g" % worst))
    return diffs


# ---------------------------------------------------------------------------
# Case sources
# ---------------------------------------------------------------------------


def _s1(sizes):
    from SketcherTests import SketchDiagnosisCorpus as C

    doc = FreeCAD.newDocument("BaseS1")
    cases, extra = C.build_corpus(doc, FreeCAD.newDocument, scale_sizes=sizes, real=True)
    for c in cases:
        yield "s1:" + c.name, c.sketch


def _s2():
    from SketcherTests import SketchAutoDefineCorpus as A

    doc = FreeCAD.newDocument("BaseS2")
    for c in A.generated(doc):
        yield "s2:" + c.name, c.sketch


def _real2():
    from SketcherTests import SketchAutoDefineCorpus as A

    cases, missing = A.real_cases({})
    for c in cases:
        yield "real2:" + c.name, c.sketch


def _golden():
    paths = []
    manifest, root = os.environ.get("BASELINE_GOLDEN"), os.environ.get("BASELINE_ROOT", "")
    if manifest:
        for line in open(manifest):
            line = line.strip()
            if line and not line.startswith("#") and line.lower().endswith(".fcstd"):
                paths.append(os.path.join(root, line))
    for d in [x for x in os.environ.get("BASELINE_EXTRA", "").split(os.pathsep) if x]:
        paths += [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.lower().endswith(".fcstd")]
    for path in paths:
        try:
            doc = FreeCAD.openDocument(path, True)
        except Exception as exc:  # noqa: BLE001
            print("golden: cannot open %s: %s" % (os.path.basename(path), exc))
            continue
        for o in doc.Objects:
            if o.TypeId == "Sketcher::SketchObject":
                yield "golden:%s:%s" % (os.path.basename(path), o.Name), o
        # the documents stay open: closing them frees the sketches that were yielded


def collect(groups, sizes):
    out = {}
    for g in groups:
        src = {"s1": lambda: _s1(sizes), "s2": _s2, "real2": _real2, "golden": _golden}[g]()
        for label, sk in src:
            try:
                out[label] = record(sk)
            except Exception as exc:  # noqa: BLE001
                out[label] = {"error": str(exc)}
            print("baseline", label, out[label].get("code"), flush=True)
    return out


def main():
    groups = [g for g in os.environ.get("BASELINE_GROUPS", "s1,s2,real2,golden").split(",") if g]
    sizes = tuple(int(x) for x in os.environ.get("BASELINE_SCALE", "10,100,1000,5000").split(","))
    result = collect(groups, sizes)
    path = os.environ.get("BASELINE_OUT", "solver_baseline.json")
    with open(path, "w") as f:
        json.dump(result, f, sort_keys=True)
    print("baseline: %d sketches -> %s" % (len(result), os.path.basename(path)), flush=True)
    sys.stdout.flush()
    os._exit(0)


# FreeCADCmd runs a script under its file name rather than "__main__".
if FreeCAD is not None and __name__ in ("__main__", os.path.splitext(os.path.basename(__file__))[0]):
    if os.environ.get("BASELINE_NO_RUN") != "1":
        main()
