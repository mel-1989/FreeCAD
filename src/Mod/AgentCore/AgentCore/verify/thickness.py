# SPDX-License-Identifier: LGPL-2.1-or-later
"""Minimum wall thickness by ray casting along inward face normals (pure Python).

The check library talks only to ``min_wall_thickness``. A later C++ kernel (Part
module, IntCurvesFace) replaces the implementation either by exposing
``Part.Shape.minWallThickness`` (picked up automatically) or via ``set_kernel``.
Kernel contract: ``kernel(shape, samples=5, time_budget_s=None) -> dict`` with
keys ``min`` (float or None), ``point`` ([x,y,z]), ``face`` (1-based index into
``shape.Faces``), ``rays`` (int), ``complete`` (bool), ``kernel`` (str).

Accuracy: rays start at sampled face points, so the result is the minimum over
the samples, an upper bound of the true minimum. It is exact for plates, tubes and
shells (constant wall, rays normal to both walls).
"""

import time

import FreeCAD
import Part

_kernel = None


def set_kernel(fn):
    """Install a replacement kernel (None restores the default)."""
    global _kernel
    _kernel = fn


def _face_samples(face, n):
    u0, u1, v0, v1 = face.ParameterRange
    pts = []
    for grid in (n, max(3 * n, 15)):
        for i in range(grid):
            for j in range(grid):
                u = u0 + (u1 - u0) * (i + 0.5) / grid
                v = v0 + (v1 - v0) * (j + 0.5) / grid
                try:
                    if face.isPartOfDomain(u, v):
                        pts.append((u, v))
                except Exception:
                    pass
        if pts:
            break
    return pts


def _python_kernel(shape, samples=5, time_budget_s=None, per_face=False):
    t0 = time.time()
    bb = shape.BoundBox
    diag = max(bb.DiagonalLength, 1.0)
    reach = diag * 1.01
    eps = 1e-5 * max(1.0, diag)  # ray-origin match tolerance
    lift = 1e-3 * max(1.0, diag)  # start the ray just outside the surface
    faces = list(shape.Faces)
    index = {}
    for gi, f in enumerate(faces, 1):
        index[f.hashCode()] = gi
    best = {"min": None, "point": None, "face": None}
    face_min = [None] * len(faces)
    rays = 0
    complete = True
    for solid in shape.Solids:
        for face in solid.Faces:
            gi = index.get(face.hashCode())
            for (u, v) in _face_samples(face, samples):
                if time_budget_s is not None and time.time() - t0 > time_budget_s:
                    complete = False
                    break
                try:
                    p = face.valueAt(u, v)
                    nrm = face.normalAt(u, v)
                except Exception:
                    continue
                if nrm.Length < 1e-12:
                    continue
                nrm.normalize()
                a = p + nrm * lift
                b = p - nrm * reach
                try:
                    seg = solid.common(Part.makeLine(a, b))
                except Exception:
                    continue
                rays += 1
                for e in seg.Edges:
                    d0 = (e.Vertexes[0].Point - p).Length
                    d1 = (e.Vertexes[-1].Point - p).Length
                    if min(d0, d1) <= eps:
                        t = e.Length
                        if gi is not None and (face_min[gi - 1] is None or t < face_min[gi - 1]):
                            face_min[gi - 1] = t
                        if best["min"] is None or t < best["min"]:
                            best = {"min": t, "point": [p.x, p.y, p.z], "face": gi}
                        break
            if not complete:
                break
        if not complete:
            break
    best.update(rays=rays, complete=complete, kernel="python-raycast/1", elapsed_s=time.time() - t0)
    if per_face:
        best["faces"] = face_min
    return best


def thickness_map(shape, samples=5, time_budget_s=None, kernel=None):
    """Per-face thickness: ``(faces, result)`` where ``faces`` has one entry per face of ``shape.Faces``
    (the minimum thickness measured from rays starting on that face, ``None`` when none of its rays hit)
    and ``result`` is the usual measurement dict (``min``, ``kernel``, ``complete``, ...).

    The native kernel (``Part.Shape.minWallThickness(perFace=True)``) is used when present, else the Python
    reference kernel; ``kernel="python"`` forces the reference. A kernel installed with ``set_kernel`` that
    does not return ``faces`` is bypassed for this call."""
    native = getattr(shape, "minWallThickness", None)
    if kernel != "python" and callable(native):
        try:
            r = native(samples, time_budget_s, True)
        except TypeError:  # an older build without perFace
            r = None
        if r is not None and "faces" in r:
            r.setdefault("kernel", "part-cpp/1")
            return list(r["faces"]), r
        if kernel == "native":
            raise RuntimeError("this FreeCAD build has no Part.Shape.minWallThickness(perFace=True)")
    r = _python_kernel(shape, samples=samples, time_budget_s=time_budget_s, per_face=True)
    return list(r["faces"]), r


def min_wall_thickness(shape, samples=5, time_budget_s=None, kernel=None):
    """Measure the minimum wall thickness.

    ``kernel="python"`` forces the pure-Python reference path (used to compare
    against the C++ kernel); ``kernel="native"`` requires ``shape.minWallThickness``.
    """
    if kernel == "python":
        return _python_kernel(shape, samples=samples, time_budget_s=time_budget_s)
    if _kernel is not None and kernel != "native":
        return _kernel(shape, samples=samples, time_budget_s=time_budget_s)
    native = getattr(shape, "minWallThickness", None)
    if callable(native):  # C++ kernel, Part/App/WallThickness.cpp
        r = native(samples, time_budget_s)
        r.setdefault("kernel", "part-cpp/1")
        return r
    if kernel == "native":
        raise RuntimeError("this FreeCAD build has no Part.Shape.minWallThickness")
    return _python_kernel(shape, samples=samples, time_budget_s=time_budget_s)
