# SPDX-License-Identifier: LGPL-2.1-or-later

"""Control-point editing of B-spline faces: ``edit_poles`` and the ``PoleEdit`` feature.

``editable_surface(face)`` is the face's surface as a ``Part.BSplineSurface`` (a copy for a B-spline
face, ``Face.toNurbs()`` for a plane, cylinder, sphere, cone or torus); a face that does not cover its
whole surface is refused (``TrimmedFace``). ``edit_poles(face, moves, locks=None)`` adds an offset
vector to chosen poles (1-based ``(i, j)``, ``i`` along u); weights are never touched.

Locks. A side ``U0``, ``U1``, ``V0`` or ``V1`` (u or v at its first or last bound) is locked to
continuity ``k`` (0, 1 or 2) by freezing the first ``k + 1`` pole rows beside it. On a clamped
(non-periodic) direction the knot vector has multiplicity ``degree + 1`` at both ends, so the
derivatives of order ``k`` across the boundary depend only on the first ``k + 1`` rows and their
weights (Piegl and Tiller, *The NURBS Book*, 2nd ed., 1997, chapter 3, derivatives of B-spline
surfaces; the end-point property of clamped knot vectors); with those rows fixed the position, tangent
plane and curvature along the side are unchanged, so continuity of order ``k`` with a neighbouring face
is kept.

The surface of an edit is ``S'(u, v) = S(u, v) + sum R_ij(u, v) d_ij`` (the rational basis functions
times the offsets), the standard property of a B-spline surface that is linear in its poles.

``makePoleEditFeature(doc, source, moves=(), locks=None)`` adds a ``Part::FeaturePython`` whose
``Shape`` is the edited face. Offsets are stored in the source object's own frame (before its global
placement), so moving or rotating the source moves or rotates the result with it.
"""

import json
import math

import FreeCAD as App
import Part

import SurfaceGordon as SG

__all__ = [
    "editable_surface",
    "edit_poles",
    "locked_poles",
    "makePoleEditFeature",
    "PoleEdit",
    "resolve_face",
    "grid_signature",
    "SIDES",
]

SIDES = ("U0", "U1", "V0", "V1")
_LOCK_NAMES = ("Free", "G0", "G1", "G2")
_GRID = 21
_ISO_TOL = 1e-7
_RANGE_TOL = 1e-9


# ------------------------------------------------------------------ surface and scope


def _is_bspline_face(face):
    return face.Surface.TypeId == "Part::GeomBSplineSurface"


def _iso_curves(surface):
    u0, u1, v0, v1 = surface.bounds()
    return [surface.uIso(u0), surface.uIso(u1), surface.vIso(v0), surface.vIso(v1)]


def _edge_points(edge, count=11):
    f, l = edge.FirstParameter, edge.LastParameter
    return [edge.valueAt(f + (l - f) * k / (count - 1.0)) for k in range(count)]


def _check_whole(face, surface):
    """Raise ``TrimmedFace`` unless ``face`` covers the whole ``surface``: one wire, a parameter range
    equal to the surface's bounds, and every edge on one boundary iso-curve."""
    if len(face.Wires) != 1:
        raise ValueError("TrimmedFace: the face has %d wires, not one" % len(face.Wires))
    u0, u1, v0, v1 = surface.bounds()
    pr = face.ParameterRange
    for got, want_lo, want_hi in ((pr[0:2], u0, u1), (pr[2:4], v0, v1)):
        length = want_hi - want_lo
        if abs(got[0] - want_lo) > _RANGE_TOL * max(length, 1e-12) or abs(got[1] - want_hi) > _RANGE_TOL * max(length, 1e-12):
            raise ValueError("TrimmedFace: the face's parameter range is smaller than its surface")
    isos = []
    for c in _iso_curves(surface):
        try:
            shape = c.toShape()
            if shape.Length > 1e-9:  # a pole iso-curve collapses to a point: skipped
                isos.append(shape)
        except Exception:
            continue
    for edge in face.Edges:
        if edge.Degenerated or edge.Length <= 1e-9:
            continue
        pts = _edge_points(edge)
        if not any(max(Part.Vertex(p).distToShape(iso)[0] for p in pts) <= _ISO_TOL for iso in isos):
            raise ValueError("TrimmedFace: an edge of the face does not lie on a boundary of its surface")


def editable_surface(face):
    """The face's surface as a ``Part.BSplineSurface`` (see the module doc)."""
    if not isinstance(face, Part.Face):
        raise TypeError("TypeError: face must be a Part.Face")
    if _is_bspline_face(face):
        surface = face.Surface.copy()
        _check_whole(face, surface)
        return surface
    converted = face.toNurbs().Faces[0]
    surface = converted.Surface
    if surface.TypeId != "Part::GeomBSplineSurface":
        raise ValueError("TrimmedFace: the face could not be converted to a B-spline surface")
    _check_whole(converted, surface)
    return surface.copy()


# ------------------------------------------------------------------ locks and moves


def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _norm_locks(surface, locks):
    """``{side: k}`` for the locked sides; ``InvalidLock`` for anything else."""
    if locks is None:
        return {}
    if not isinstance(locks, dict):
        raise ValueError("InvalidLock: locks must be a dict of U0, U1, V0, V1")
    out = {}
    for side, k in locks.items():
        if side not in SIDES:
            raise ValueError("InvalidLock: %r is not a side (U0, U1, V0, V1)" % (side,))
        if k is None:
            continue
        if not _is_int(k) or k not in (0, 1, 2):
            raise ValueError("InvalidLock: the lock of %s must be None, 0, 1 or 2, not %r" % (side, k))
        periodic = surface.isUPeriodic() if side[0] == "U" else surface.isVPeriodic()
        if periodic:
            raise ValueError("InvalidLock: %s is a side of a periodic direction" % side)
        out[side] = k
    return out


def locked_poles(surface, locks):
    """The set of 1-based ``(i, j)`` poles the lock rule freezes: lock ``k`` on ``U0`` freezes rows
    ``i = 1 .. k + 1``, on ``U1`` rows ``i = NbUPoles - k .. NbUPoles``, and the same for ``V`` and ``j``."""
    nu, nv = surface.NbUPoles, surface.NbVPoles
    out = set()
    for side, k in _norm_locks(surface, locks).items():
        if side == "U0":
            rows = [(i, j) for i in range(1, k + 2) for j in range(1, nv + 1)]
        elif side == "U1":
            rows = [(i, j) for i in range(nu - k, nu + 1) for j in range(1, nv + 1)]
        elif side == "V0":
            rows = [(i, j) for j in range(1, k + 2) for i in range(1, nu + 1)]
        else:
            rows = [(i, j) for j in range(nv - k, nv + 1) for i in range(1, nu + 1)]
        out.update((i, j) for i, j in rows if 1 <= i <= nu and 1 <= j <= nv)
    return out


def _norm_moves(surface, moves):
    """``[((i, j), Vector), ...]`` validated against the pole grid of ``surface``."""
    if isinstance(moves, dict):
        items = list(moves.items())
    else:
        try:
            items = list(moves)
        except TypeError:
            raise ValueError("InvalidIndex: moves must be a list or a dict of ((i, j), offset)") from None
    nu, nv = surface.NbUPoles, surface.NbVPoles
    seen = set()
    out = []
    for item in items:
        try:
            index, offset = item
            i, j = index
        except (TypeError, ValueError):
            raise ValueError("InvalidIndex: a move must be ((i, j), offset)") from None
        if not (_is_int(i) and _is_int(j)) or not (1 <= i <= nu) or not (1 <= j <= nv):
            raise ValueError("InvalidIndex: pole (%r, %r) is outside the %d by %d grid" % (i, j, nu, nv))
        if (i, j) in seen:
            raise ValueError("DuplicatePole: pole (%d, %d) is given twice" % (i, j))
        seen.add((i, j))
        try:
            vec = App.Vector(offset)
        except Exception:
            raise ValueError("InvalidOffset: the offset of pole (%d, %d) is not a vector" % (i, j)) from None
        if not all(math.isfinite(c) for c in (vec.x, vec.y, vec.z)):
            raise ValueError("InvalidOffset: the offset of pole (%d, %d) is not finite" % (i, j))
        out.append(((i, j), vec))
    return out


def _apply(surface, moves, locks=None):
    """The edited surface and the count of non-zero offsets; ``surface`` itself is not changed."""
    items = _norm_moves(surface, moves)
    locked = locked_poles(surface, locks)
    for (i, j), vec in items:
        if (i, j) in locked and vec.Length > 0.0:
            raise ValueError("LockedPole: pole (%d, %d) is locked" % (i, j))
    edited = surface.copy()
    moved = 0
    for (i, j), vec in items:
        if vec.Length > 0.0:
            edited.setPole(i, j, surface.getPole(i, j) + vec)
            moved += 1
    return edited, moved


def _max_deviation(old, new):
    u0, u1, v0, v1 = old.bounds()
    worst = 0.0
    for a in range(_GRID):
        u = u0 + (u1 - u0) * a / (_GRID - 1.0)
        for b in range(_GRID):
            v = v0 + (v1 - v0) * b / (_GRID - 1.0)
            d = (new.value(u, v) - old.value(u, v)).Length
            if d > worst:
                worst = d
    return worst


def _face_of(surface, like):
    face = Part.Face(surface)
    if like is not None and like.Orientation == "Reversed":
        face.reverse()
    return face


def edit_poles(face, moves, locks=None):
    """Move poles of the face's surface; returns ``face``, ``surface``, ``moved`` and ``max_deviation``."""
    surface = editable_surface(face)
    edited, moved = _apply(surface, moves, locks)
    return {
        "face": _face_of(edited, face),
        "surface": edited,
        "moved": moved,
        "max_deviation": _max_deviation(surface, edited),
    }


# ------------------------------------------------------------------ grid signature


def grid_signature(surface):
    """The grid of ``surface`` as a string: degrees, pole counts, knots, multiplicities, rational and
    periodic flags (poles and weights are not part of it)."""
    sig = {
        "deg": [surface.UDegree, surface.VDegree],
        "poles": [surface.NbUPoles, surface.NbVPoles],
        "uknots": ["%.12g" % k for k in surface.getUKnots()],
        "vknots": ["%.12g" % k for k in surface.getVKnots()],
        "umults": list(surface.getUMultiplicities()),
        "vmults": list(surface.getVMultiplicities()),
        "rational": [bool(surface.isURational()), bool(surface.isVRational())],
        "periodic": [bool(surface.isUPeriodic()), bool(surface.isVPeriodic())],
    }
    return json.dumps(sig, sort_keys=True)


# ------------------------------------------------------------------------- feature


def resolve_face(link):
    """The face one ``Source`` link names, with every placement on its path applied."""
    if not link or link[0] is None:
        raise ValueError("InvalidInput: the source link is not set")
    obj, subs = link[0], link[1]
    if isinstance(subs, str):
        subs = (subs,)
    fsubs = [s for s in subs if s.rsplit(".", 1)[-1].startswith("Face")]
    if fsubs:
        face = Part.getShape(obj, fsubs[0], needSubElement=True)
        if face is None or face.isNull() or face.ShapeType != "Face":
            raise ValueError("InvalidInput: %s.%s is not a face" % (obj.Label, fsubs[0]))
        return face
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull() or len(shape.Faces) != 1:
        raise ValueError("InvalidInput: %s is not one face; link a face" % obj.Label)
    return shape.Faces[0]


def _flat(moves):
    """``(indices, offsets)`` of a moves list or dict, flattened for the feature's properties."""
    idx, vecs = [], []
    items = list(moves.items()) if isinstance(moves, dict) else list(moves)
    for (i, j), v in items:
        idx.extend((int(i), int(j)))
        vecs.append(App.Vector(v))
    return idx, vecs


def _lock_name(k):
    return _LOCK_NAMES[0 if k is None else k + 1]


def _lock_value(name):
    return None if name == "Free" else _LOCK_NAMES.index(name) - 1


def stored_moves(obj):
    """``[((i, j), local offset), ...]`` of the feature's properties."""
    idx = list(obj.PoleIndices)
    vecs = list(obj.PoleOffsets)
    if len(idx) != 2 * len(vecs):
        raise ValueError("InvalidIndex: PoleIndices and PoleOffsets disagree")
    return [((idx[2 * n], idx[2 * n + 1]), App.Vector(v)) for n, v in enumerate(vecs)]


def stored_locks(obj):
    return {side: _lock_value(getattr(obj, "Lock" + side)) for side in SIDES}


def store_edit(obj, moves, locks, local=True):
    """Write ``moves`` (offsets in the source's own frame) and ``locks`` into the feature's properties."""
    idx, vecs = _flat(moves)
    obj.PoleIndices = idx
    obj.PoleOffsets = vecs
    for side in SIDES:
        k = (locks or {}).get(side)
        setattr(obj, "Lock" + side, _lock_name(k))


def build_edit(obj):
    """``edit_poles`` on the source face and the stored edit of a feature."""
    face = resolve_face(obj.Source)
    surface = editable_surface(face)
    sig = grid_signature(surface)
    stored = obj.GridSignature
    if stored and stored != sig:
        raise ValueError("PoleGridChanged: the source's pole grid is no longer the one the edit was made on")
    rot = face.Placement.Rotation
    moves = [(index, rot.multVec(v)) for index, v in stored_moves(obj)]
    locks = stored_locks(obj)
    edited, moved = _apply(surface, moves, locks)
    return {
        "face": _face_of(edited, face),
        "surface": edited,
        "moved": moved,
        "max_deviation": _max_deviation(surface, edited),
        "signature": sig,
    }


class PoleEdit:
    """Proxy of the pole edit feature."""

    def __init__(self, obj):
        obj.Proxy = self
        add = obj.addProperty
        add("App::PropertyLinkSub", "Source", "PoleEdit", "The face whose control points are edited")
        add("App::PropertyIntegerList", "PoleIndices", "PoleEdit", "Moved poles, flattened as i, j pairs (1-based, i along u)")
        add("App::PropertyVectorList", "PoleOffsets", "PoleEdit", "Offset of each moved pole, in the source object's own frame")
        for side in SIDES:
            add("App::PropertyEnumeration", "Lock" + side, "PoleEdit", "Continuity kept along side %s" % side)
            setattr(obj, "Lock" + side, list(_LOCK_NAMES))
            setattr(obj, "Lock" + side, "Free")
        add("App::PropertyString", "GridSignature", "PoleEdit", "Pole grid of the source when the edit was stored")
        add("App::PropertyInteger", "MovedPoles", "PoleEdit", "Number of poles with a non-zero offset")
        add("App::PropertyFloat", "MaxDeviation", "PoleEdit", "Largest distance between the edited and the source surface, in mm")
        obj.MovedPoles = 0
        obj.MaxDeviation = 0.0
        self._status(obj)
        SG.ensure_input_guard(obj)

    @staticmethod
    def _status(obj):
        for name in ("MovedPoles", "MaxDeviation"):
            if name in obj.PropertiesList:
                obj.setPropertyStatus(name, "ReadOnly")
        if "GridSignature" in obj.PropertiesList:
            obj.setPropertyStatus("GridSignature", ["Hidden", "ReadOnly"])

    def execute(self, obj):
        SG.check_inputs_alive(obj)
        result = build_edit(obj)
        obj.Shape = result["face"]
        if not obj.GridSignature:
            obj.GridSignature = result["signature"]
        obj.MovedPoles = int(result["moved"])
        obj.MaxDeviation = float(result["max_deviation"])
        SG.record_inputs(obj)

    def onChanged(self, obj, prop):
        SG.note_link_change(self, obj, prop)

    def onDocumentRestored(self, obj):
        obj.Proxy = self
        self._status(obj)
        SG.ensure_input_guard(obj)
        _attach_view_provider(obj)

    def dumps(self):
        return None

    def loads(self, state):
        return None


def _attach_view_provider(obj):
    """Give the feature its view provider proxy (a double click opens the panel); nothing in a session
    without the GUI, and the document is saved without the proxy (it is not pickled)."""
    if not App.GuiUp:
        return
    try:
        import SurfacePoleEditCommands

        vobj = obj.ViewObject
        if vobj is not None and getattr(vobj, "Proxy", None) is None:
            SurfacePoleEditCommands.ViewProviderPoleEdit(vobj)
    except Exception:
        pass


def source_signature(source):
    """``grid_signature`` of the face a ``Source`` value names, or ``""`` when it cannot be resolved."""
    try:
        return grid_signature(editable_surface(resolve_face(source)))
    except Exception:
        return ""


def makePoleEditFeature(doc, source, moves=(), locks=None, name="PoleEdit"):
    """Add a pole edit feature to ``doc`` and return it (not yet recomputed).

    ``source`` is ``(object, "FaceN")``. ``moves`` (a list or dict of ``((i, j), offset)``) are stored
    as given, in the source object's own frame; ``locks`` is as for ``edit_poles``."""
    o, subs = SG._split(source)
    obj = doc.addObject("Part::FeaturePython", name)
    PoleEdit(obj)
    obj.Source = (o, subs)
    surface = None
    try:
        surface = editable_surface(resolve_face(obj.Source))
    except Exception:
        pass
    if surface is not None:  # a bad move or lock is refused before the feature stays in the document
        try:
            _apply(surface, moves, locks)
        except Exception:
            doc.removeObject(obj.Name)
            raise
    store_edit(obj, moves, _norm_locks(surface, locks) if surface is not None else (locks or {}))
    if surface is not None:
        obj.GridSignature = grid_signature(surface)
    SG.record_inputs(obj)
    _attach_view_provider(obj)
    return obj
