# SPDX-License-Identifier: LGPL-2.1-or-later
"""Face-level geometry diff between a before shape and an after shape.

Recipe (the brief's definition of done, from the kernel review):

1. **Signature.** Each face gets a signature: surface kind, area, centre of
   mass, bounding box, plus the defining parameters of analytic surfaces
   (plane normal, cylinder/cone/sphere/torus radius and axis).
2. **Exact match.** Faces whose quantised signature keys are equal are
   paired first (same kind, area and centre within the tolerance).
3. **Nearest match.** Remaining faces are paired by mutual-nearest signature
   distance (centre distance plus a relative area term), same surface kind
   only, within a generous radius (a quarter of the model's diagonal), so a
   face that moved or grew is reported as *modified*, not removed plus added.
4. **Deviation.** Every matched pair is sampled: a UV grid on each face,
   kept where the point lies in the face's domain, plus its vertices; the
   distance of each sample to the other face (``distToShape``) is taken both
   ways, and the maximum is the pair's deviation. Pairs within the tolerance
   are *unchanged*.
5. **Local booleans.** Only if some face changed: the changed faces' bounding
   boxes are united and slightly enlarged, both solids are cut down to that
   box with ``common``, and the added and removed material is the ``cut`` of
   those two local pieces. Never a whole-solid cut of near-identical solids
   (the worst case for OCCT's General Fuse); ``region.fraction`` says how much
   of the model's box the changed region covers.
"""

import math

import FreeCAD as App

DEFAULT_TOL = 1e-4  # mm; sampled deviation below this is "unchanged"


def _kind(face):
    try:
        return face.Surface.TypeId
    except Exception:
        return type(face.Surface).__name__


def _vec(v):
    return (float(v.x), float(v.y), float(v.z))


def signature(face):
    kind = _kind(face)
    try:
        com = _vec(face.CenterOfMass)
    except Exception:
        bb = face.BoundBox
        com = (bb.Center.x, bb.Center.y, bb.Center.z)
    bb = face.BoundBox
    extra = ()
    s = face.Surface
    try:
        if kind.endswith("Plane"):
            u0, u1, v0, v1 = face.ParameterRange
            n = face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
            extra = ("n",) + tuple(round(c, 6) for c in _vec(n))
        elif hasattr(s, "Radius"):
            extra = ("r", round(float(s.Radius), 6))
    except Exception:
        pass
    return {"kind": kind, "area": float(face.Area), "com": com,
            "bbox": (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax), "extra": extra}


def _key(sig, tol):
    q = lambda x: int(round(x / tol))  # noqa: E731
    area_tol = max(tol, tol * math.sqrt(max(sig["area"], 0.0)))
    return (sig["kind"], int(round(sig["area"] / area_tol)), *map(q, sig["com"]), sig["extra"])


def _dist(sa, sb):
    d = math.dist(sa["com"], sb["com"])
    return d + abs(sa["area"] - sb["area"]) / max(math.sqrt(max(sa["area"], sb["area"])), 1e-9)


def _samples(face, n):
    pts = []
    try:
        u0, u1, v0, v1 = face.ParameterRange
        for i in range(n):
            for j in range(n):
                u = u0 + (u1 - u0) * (i + 0.5) / n
                v = v0 + (v1 - v0) * (j + 0.5) / n
                try:
                    if face.isPartOfDomain(u, v):
                        pts.append(face.valueAt(u, v))
                except Exception:
                    pass
    except Exception:
        pass
    pts.extend(vx.Point for vx in face.Vertexes)
    if not pts:
        pts.append(face.CenterOfMass)
    return pts


def deviation(fa, fb, n=4):
    """Max two-way sampled point-to-face distance between two faces."""
    import Part
    worst = 0.0
    for src, dst in ((fa, fb), (fb, fa)):
        for p in _samples(src, n):
            try:
                d = dst.distToShape(Part.Vertex(p))[0]
            except Exception:
                d = float("inf")
            worst = max(worst, d)
    return worst


def match_faces(faces_b, faces_a, tol=DEFAULT_TOL, radius=None):
    """Pair before faces with after faces. Returns (pairs, unmatched_b, unmatched_a, how)."""
    sb = [signature(f) for f in faces_b]
    sa = [signature(f) for f in faces_a]
    buckets = {}
    for i, s in enumerate(sb):
        buckets.setdefault(_key(s, tol * 10), []).append(i)
    pairs, how = [], {}
    used_b, used_a = set(), set()
    for j, s in enumerate(sa):
        cand = buckets.get(_key(s, tol * 10))
        while cand:
            i = cand.pop(0)
            if i not in used_b:
                pairs.append((i, j))
                how[(i, j)] = "exact"
                used_b.add(i)
                used_a.add(j)
                break
    if radius is None:
        radius = float("inf")
    left_b = [i for i in range(len(sb)) if i not in used_b]
    left_a = [j for j in range(len(sa)) if j not in used_a]
    cands = sorted((_dist(sb[i], sa[j]), i, j) for i in left_b for j in left_a
                   if sb[i]["kind"] == sa[j]["kind"])
    free_b, free_a = set(left_b), set(left_a)
    for d, i, j in cands:
        if d > radius:
            break
        if i in free_b and j in free_a:
            pairs.append((i, j))
            how[(i, j)] = "nearest"
            free_b.discard(i)
            free_a.discard(j)
    left_b, left_a = sorted(free_b), sorted(free_a)
    return pairs, left_b, left_a, how


def _bbox_union(boxes):
    bb = None
    for b in boxes:
        if bb is None:
            bb = App.BoundBox(b)
        else:
            bb.add(b)
    return bb


def _shape_ok(s):
    try:
        return s is not None and not s.isNull()
    except Exception:
        return False


def shape_diff(before, after, tol=DEFAULT_TOL, samples=4, booleans=True):
    """Face-level diff of two shapes (either may be None for added/removed)."""
    if not _shape_ok(before) and not _shape_ok(after):
        return {"status": "none"}
    if not _shape_ok(before) or not _shape_ok(after):
        s = after if _shape_ok(after) else before
        return {"status": "added" if _shape_ok(after) else "removed", "faces": len(s.Faces),
                "volume": float(s.Volume) if s.Solids else 0.0, "bbox": list(_bbox6(s.BoundBox))}
    fb, fa = before.Faces, after.Faces
    diag = max(_bbox_union([before.BoundBox, after.BoundBox]).DiagonalLength, 1e-9)
    pairs, ub, ua, how = match_faces(fb, fa, tol, radius=0.25 * diag)
    modified, unchanged, worst = [], 0, 0.0
    for i, j in pairs:
        n = samples if how[(i, j)] == "nearest" else max(2, samples - 1)
        d = deviation(fb[i], fa[j], n)
        worst = max(worst, d)
        if d > tol:
            modified.append({"before": f"Face{i + 1}", "after": f"Face{j + 1}", "match": how[(i, j)],
                             "max_deviation": _r(d)})
        else:
            unchanged += 1
    out = {
        "status": "modified" if (modified or ub or ua or len(fb) != len(fa)) else "unchanged",
        "faces_before": len(fb), "faces_after": len(fa), "matched": len(pairs), "unchanged_faces": unchanged,
        "modified_faces": modified,
        "removed_faces": [f"Face{i + 1}" for i in ub],
        "added_faces": [f"Face{j + 1}" for j in ua],
        "max_deviation": _r(worst),
        "volume_before": _r(before.Volume) if before.Solids else 0.0,
        "volume_after": _r(after.Volume) if after.Solids else 0.0,
    }
    if out["status"] == "modified" and booleans and before.Solids and after.Solids:
        out["region"] = _local_booleans(before, after, fb, fa, modified, ub, ua, tol, diag)
    return out


def _bbox6(bb):
    return (_r(bb.XMin), _r(bb.YMin), _r(bb.ZMin), _r(bb.XMax), _r(bb.YMax), _r(bb.ZMax))


def _r(x):
    return round(float(x), 6)


def _local_booleans(before, after, fb, fa, modified, ub, ua, tol, diag):
    import Part
    boxes = [fb[int(m["before"][4:]) - 1].BoundBox for m in modified]
    boxes += [fa[int(m["after"][4:]) - 1].BoundBox for m in modified]
    boxes += [fb[i].BoundBox for i in ub] + [fa[j].BoundBox for j in ua]
    region = _bbox_union(boxes)
    if region is None:
        return None
    pad = max(10 * tol, 1e-3 * diag)
    region.enlarge(pad)
    model = _bbox_union([before.BoundBox, after.BoundBox])
    vol = lambda b: max(b.XLength, 1e-12) * max(b.YLength, 1e-12) * max(b.ZLength, 1e-12)  # noqa: E731
    out = {"bbox": list(_bbox6(region)), "fraction": _r(min(1.0, vol(region) / vol(model)))}
    try:
        box = Part.makeBox(region.XLength, region.YLength, region.ZLength,
                           App.Vector(region.XMin, region.YMin, region.ZMin))
        lb = before.common(box)
        la = after.common(box)
        add = la.cut(lb)
        rem = lb.cut(la)
        out["added_volume"] = _r(add.Volume) if add.Solids else 0.0
        out["removed_volume"] = _r(rem.Volume) if rem.Solids else 0.0
        out["added_bbox"] = list(_bbox6(add.BoundBox)) if add.Solids else None
        out["removed_bbox"] = list(_bbox6(rem.BoundBox)) if rem.Solids else None
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out
