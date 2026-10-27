# SPDX-License-Identifier: LGPL-2.1-or-later
# Vendored from cad-fork harness/golden/deviation.py (brief I3, sampled point-to-surface deviation).
# The fork must not import from the control repo at runtime; re-copy when I3 changes the measure.
# Sampled point-to-surface deviation (kernel review, cad-study 12-review-kernel.md
# line 105, replacing tessellation Hausdorff). Runs inside FreeCADCmd.
#
# Sample points on the faces of shape A, measure each point's distance to the
# faces of shape B with BRepExtrema (Shape.distToShape), report max and RMS; do
# it both ways so a missing or extra feature on either side shows up. Distances
# go to B's faces, never to B's solid, so interior points do not read zero.
import math

import Part


def sample_points(shp, budget):
    """Deterministic points on the faces: a UV grid per face, thinned to the budget."""
    faces = shp.Faces
    if not faces:
        # wires or edges: sample along edges instead
        pts = []
        for e in shp.Edges:
            n = 8
            a, b = e.ParameterRange
            pts += [e.valueAt(a + (b - a) * i / (n - 1)) for i in range(n)]
        return pts[:: max(1, len(pts) // budget)]
    k = max(2, int(math.sqrt(max(4, budget // len(faces)))))
    pts = []
    for f in faces:
        u0, u1, v0, v1 = f.ParameterRange
        for i in range(k):
            for j in range(k):
                # strictly inside the parameter box so seam and boundary points are avoided
                u = u0 + (u1 - u0) * (i + 0.5) / k
                v = v0 + (v1 - v0) * (j + 0.5) / k
                try:
                    p = f.valueAt(u, v)
                    # keep only points that lie on the trimmed face (not in a hole or
                    # outside the boundary); Face.isInside and face centres of mass
                    # are not reliable for this
                    if Part.Vertex(p).distToShape(f)[0] < 1e-6:
                        pts.append(p)
                except Exception:
                    pass
    return pts[:: max(1, len(pts) // budget)] if len(pts) > budget * 1.5 else pts


def one_way(a, b, budget):
    target = Part.Compound(b.Faces) if b.Faces else b
    dists = []
    for p in sample_points(a, budget):
        try:
            d = Part.Vertex(p).distToShape(target)[0]
        except Exception:
            continue
        dists.append(d)
    if not dists:
        return 0.0, 0.0, 0
    return max(dists), math.sqrt(sum(d * d for d in dists) / len(dists)), len(dists)


def measure(cand, gold, budget=150):
    """Deviation of cand from gold: max and RMS in both directions, plus point counts."""
    mab, rab, nab = one_way(cand, gold, budget)
    mba, rba, nba = one_way(gold, cand, budget)
    return {
        "max_cand_to_gold": float(f"{mab:.6g}"),
        "rms_cand_to_gold": float(f"{rab:.6g}"),
        "max_gold_to_cand": float(f"{mba:.6g}"),
        "rms_gold_to_cand": float(f"{rba:.6g}"),
        "points": [nab, nba],
    }
