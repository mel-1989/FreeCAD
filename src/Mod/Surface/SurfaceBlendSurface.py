# SPDX-License-Identifier: LGPL-2.1-or-later

"""G2 blend surface between an edge of one face and an edge of another.

``makeBlendSurface(face1, edge1, face2, edge2, continuity1=2, continuity2=2, ...)`` bridges the gap
between the two edges with a B-spline face that leaves each input face with the chosen continuity: G0
(touching), G1 (tangent) or G2 (equal normal curvature across the edge).

Construction. At the station of normalised arc length ``s`` (the same fraction of the arc length of
both edges) the point ``A`` of edge 1 and ``B`` of edge 2 are joined by a Bezier curve of degree
``d = continuity1 + continuity2 + 1`` fixed by Hermite end conditions: at ``A`` the curve leaves along
the unit direction ``D`` that lies in the tangent plane of face 1, is normal to the edge and points
away from the face, with the face's normal curvature ``kappa`` in that direction (second derivative
``lambda^2 kappa N``); the same at ``B`` seen from face 2. The Bezier poles follow from the Hermite
form of a Bezier curve (L. Piegl and W. Tiller, *The NURBS Book*, 2nd ed., 1997, chapters 1 and 2);
the normal curvature is the second fundamental form of the face in the direction ``D`` (M. do Carmo,
*Differential Geometry of Curves and Surfaces*, 1976, chapter 3). The poles of the sections are
interpolated station by station into one B-spline surface, and the number of stations grows
(5, 9, 17, 33, 65) until the continuity measured with ``SurfaceAnalysis.edge_continuity`` is within the
thresholds. Nothing is taken from CurvesWB or from the stock ``Surface::BlendCurve``.

``makeBlendSurfaceFeature(doc, edge1, edge2)`` adds a ``Part::FeaturePython`` whose ``Shape`` is that face.
"""

import math

import FreeCAD as App
import Part

import SurfaceAnalysis as SA
import SurfaceGordon as SG

__all__ = ["makeBlendSurface", "makeBlendSurfaceFeature", "BlendSurface", "resolve_edge_face", "build_blend"]

_STATIONS = (5, 9, 17, 33, 65)
_ANGLE_LIMIT = 0.05
_JUMP_LIMIT = 0.01
_CONTINUITY = ("G0", "G1", "G2")


# --------------------------------------------------------------------------- geometry


def _station_counts(max_sections):
    """5, 9, 17, ... up to ``max_sections``; below 5 a single try at ``max_sections`` itself."""
    if max_sections < _STATIONS[0]:
        return [max_sections]
    counts = [n for n in _STATIONS if n <= max_sections]
    n = _STATIONS[-1]
    while 2 * n - 1 <= max_sections:
        n = 2 * n - 1
        counts.append(n)
    return counts


def _dist(point, shape):
    return Part.Vertex(point).distToShape(shape)[0]


def _edge_points(edge, count):
    f, l = edge.FirstParameter, edge.LastParameter
    return [edge.valueAt(f + (l - f) * j / (count - 1.0)) for j in range(count)]


def _on_face(edge, face, tolerance):
    """Is ``edge`` one of the face's edges, or are 11 evenly spaced points of it within ``tolerance``?"""
    for e in face.Edges:
        if e.isSame(edge):
            return True
    return all(_dist(p, face) <= tolerance for p in _edge_points(edge, 11))


def _face_normal(face, point):
    """Unit normal of ``face`` (orientation applied) at the projection of ``point``."""
    u, v = face.Surface.parameter(point)
    n = face.normalAt(u, v)
    n.normalize()
    return n


def _frame(face, edge, param):
    """(point, D, N, kappa) of ``face`` at the edge's ``param``: D the unit vector N x T, signed to point
    away from the face; N the unit normal; kappa the normal curvature of the face in direction D."""
    p = edge.valueAt(param)
    t = edge.tangentAt(param)
    t.normalize()
    surf = face.Surface
    u, v = surf.parameter(p)
    n = face.normalAt(u, v)
    d = n.cross(t)
    d.normalize()
    h = 1e-3 * face.BoundBox.DiagonalLength
    uu, vv = surf.parameter(p - d * h)
    if not face.isPartOfDomain(uu, vv) or (surf.value(uu, vv) - (p - d * h)).Length > 1e-2 * h:
        d = d * -1.0
    su, sv = surf.getDN(u, v, 1, 0), surf.getDN(u, v, 0, 1)
    suu, suv, svv = surf.getDN(u, v, 2, 0), surf.getDN(u, v, 1, 1), surf.getDN(u, v, 0, 2)
    nn = su.cross(sv)
    nn.normalize()
    if nn.dot(n) < 0:
        nn = nn * -1.0
    big_e, big_f, big_g = su.dot(su), su.dot(sv), sv.dot(sv)
    r1, r2 = d.dot(su), d.dot(sv)
    det = big_e * big_g - big_f * big_f
    a = (r1 * big_g - r2 * big_f) / det
    b = (r2 * big_e - r1 * big_f) / det
    kappa = nn.dot(suu * (a * a) + suv * (2 * a * b) + svv * (b * b))
    return p, d, nn, kappa


def _bezier_poles(a, b, c1, c2, lam1, lam2, fa, fb):
    """Poles of the section: Hermite end conditions turned into the Bezier poles of degree c1 + c2 + 1."""
    d = c1 + c2 + 1
    poles = [None] * (d + 1)
    poles[0], poles[d] = App.Vector(a), App.Vector(b)
    k = float(d * (d - 1))
    if c1 >= 1:
        poles[1] = a + fa[1] * (lam1 / d)
        if c1 == 2:
            poles[2] = poles[1] * 2.0 - a + fa[2] * (lam1 * lam1 * fa[3] / k)
    if c2 >= 1:
        poles[d - 1] = b + fb[1] * (lam2 / d)
        if c2 == 2:
            poles[d - 2] = poles[d - 1] * 2.0 - b + fb[2] * (lam2 * lam2 * fb[3] / k)
    return poles


def _bernstein(n, i, t):
    return math.comb(n, i) * t**i * (1.0 - t) ** (n - i)


def _backtracks(poles, chord):
    """Is C'(t) . chord < 0 at any of 101 evenly spaced t of the Bezier section?"""
    d = len(poles) - 1
    for j in range(101):
        t = j / 100.0
        der = App.Vector()
        for i in range(d):
            der = der + (poles[i + 1] - poles[i]) * (d * _bernstein(d - 1, i, t))
        if der.dot(chord) < -1e-12:
            return True
    return False


def _surface(rows, params):
    """B-spline surface whose v-iso at station i is the Bezier section ``rows[i]`` (u along the edges)."""
    count = len(rows[0])
    curves = []
    for j in range(count):
        c = Part.BSplineCurve()
        c.interpolate(Points=[r[j] for r in rows], Parameters=params)
        curves.append(c)
    c0 = curves[0]
    pole_rows = [c.getPoles() for c in curves]
    grid = [[pole_rows[j][i] for j in range(count)] for i in range(len(pole_rows[0]))]
    s = Part.BSplineSurface()
    s.buildFromPolesMultsKnots(
        grid, c0.getMultiplicities(), [count, count], c0.getKnots(), [0.0, 1.0], False, False, c0.Degree, count - 1
    )
    return s


def _measure(input_face, edge, blend_face, point):
    """The side's measured maxima, the input face reversed first when its normal opposes the blend's."""
    face = input_face
    if _face_normal(input_face, point).dot(_face_normal(blend_face, point)) < 0:
        face = input_face.copy()
        face.reverse()
    r = SA.edge_continuity(None, edge, samples=41, faces=(face, blend_face))
    return {"gap": r["gap"], "angle": r["angle"], "curvature_jump": r["curvature_jump"]}


# ------------------------------------------------------------------------- validation


def _finite_positive(x):
    return not isinstance(x, bool) and isinstance(x, (int, float)) and math.isfinite(x) and x > 0


def _check_edge(face, edge, label, tolerance):
    if edge.isNull() or getattr(edge, "Degenerated", False) or edge.Length <= tolerance:
        raise ValueError("DegenerateEdge: %s is degenerate" % label)
    a, b = edge.valueAt(edge.FirstParameter), edge.valueAt(edge.LastParameter)
    if edge.isClosed() or (a - b).Length <= tolerance:
        raise ValueError("ClosedEdge: %s is closed" % label)
    if not _on_face(edge, face, tolerance):
        raise ValueError("EdgeNotOnFace: %s is not an edge of its face" % label)


def _check_args(face1, edge1, face2, edge2, c1, c2, size1, size2, tolerance, max_sections):
    for item, kind, name in ((face1, Part.Face, "face1"), (face2, Part.Face, "face2"), (edge1, Part.Edge, "edge1"), (edge2, Part.Edge, "edge2")):
        if not isinstance(item, kind):
            raise TypeError("TypeError: %s must be a Part.%s" % (name, kind.__name__))
    if not _finite_positive(tolerance):
        raise ValueError("InvalidTolerance: tolerance must be finite and greater than 0")
    for c in (c1, c2):
        if isinstance(c, bool) or c not in (0, 1, 2) or not isinstance(c, int):
            raise ValueError("InvalidContinuity: continuity must be 0, 1 or 2, not %r" % (c,))
    for s in (size1, size2):
        if not _finite_positive(s):
            raise ValueError("InvalidSize: size must be finite and greater than 0")
    if isinstance(max_sections, bool) or not isinstance(max_sections, int) or max_sections < 2:
        raise ValueError("InvalidMaxSections: max_sections must be an integer of at least 2")
    _check_edge(face1, edge1, "edge 1", tolerance)
    _check_edge(face2, edge2, "edge 2", tolerance)


# --------------------------------------------------------------------------- main API


def makeBlendSurface(
    face1,
    edge1,
    face2,
    edge2,
    continuity1=2,
    continuity2=2,
    size1=1.0,
    size2=1.0,
    tolerance=1e-4,
    approximate=False,
    max_sections=65,
):
    """B-spline blend between ``edge1`` of ``face1`` and ``edge2`` of ``face2``; see the module doc.

    Returns a dict: ``surface``, ``face``, ``approximate``, ``sections``, ``edge2_reversed``,
    ``backtracks`` and ``side1`` / ``side2`` (``gap``, ``angle``, ``curvature_jump`` maxima)."""
    _check_args(face1, edge1, face2, edge2, continuity1, continuity2, size1, size2, tolerance, max_sections)
    len1, len2 = edge1.Length, edge2.Length
    a0, a1 = edge1.valueAt(edge1.FirstParameter), edge1.valueAt(edge1.LastParameter)
    b0, b1 = edge2.valueAt(edge2.FirstParameter), edge2.valueAt(edge2.LastParameter)
    reversed2 = (a0 - b1).Length + (a1 - b0).Length < (a0 - b0).Length + (a1 - b1).Length
    cache = {}

    def frames(s):
        key = round(s, 12)
        if key not in cache:
            p1 = edge1.getParameterByLength(len1 * s)
            p2 = edge2.getParameterByLength(len2 * ((1.0 - s) if reversed2 else s))
            fa = _frame(face1, edge1, p1) if continuity1 >= 1 else (edge1.valueAt(p1),)
            fb = _frame(face2, edge2, p2) if continuity2 >= 1 else (edge2.valueAt(p2),)
            cache[key] = (fa, fb)
        return cache[key]

    def section(s):
        fa, fb = frames(s)
        a, b = fa[0], fb[0]
        chord = (b - a).Length
        if chord < tolerance:
            raise ValueError("DegenerateChord: the edges meet at s=%.4g (chord %.3g mm)" % (s, chord))
        poles = _bezier_poles(a, b, continuity1, continuity2, size1 * chord, size2 * chord, fa, fb)
        return poles

    p_mid1 = edge1.valueAt(edge1.getParameterByLength(0.5 * len1))
    p_mid2 = edge2.valueAt(edge2.getParameterByLength(0.5 * len2))
    last = None
    for n in _station_counts(max_sections):
        params = [i / (n - 1.0) for i in range(n)]
        sections = [section(s) for s in params]
        surface = _surface(sections, params)
        face = Part.Face(surface)
        if _face_normal(face1, p_mid1).dot(_face_normal(face, p_mid1)) < 0:
            face.reverse()
        side1 = _measure(face1, edge1, face, p_mid1)
        side2 = _measure(face2, edge2, face, p_mid2)
        ok = True
        for side, c in ((side1, continuity1), (side2, continuity2)):
            ok = ok and side["gap"] <= tolerance
            if c >= 1:
                ok = ok and side["angle"] <= _ANGLE_LIMIT
            if c == 2:
                ok = ok and side["curvature_jump"] <= _JUMP_LIMIT
        last = (n, surface, face, side1, side2, sections)
        if ok:
            break
    n, surface, face, side1, side2, sections = last
    if not ok:
        if not approximate:
            ratios = []
            for side, c in ((side1, continuity1), (side2, continuity2)):
                ratios.append(side["gap"] / tolerance)
                if c >= 1:
                    ratios.append(side["angle"] / _ANGLE_LIMIT)
                if c == 2:
                    ratios.append(side["curvature_jump"] / _JUMP_LIMIT)
            worst = max(ratios)
            raise ValueError(
                "DeviationExceeded: with %d stations the continuity misses its threshold (worst %.3g times the limit; "
                "gap %.3g/%.3g mm, angle %.3g/%.3g deg, curvature jump %.3g/%.3g)"
                % (n, worst, side1["gap"], side2["gap"], side1["angle"], side2["angle"], side1["curvature_jump"], side2["curvature_jump"])
            )
    backtracks = any(_backtracks(p, p[-1] - p[0]) for p in sections)
    return {
        "surface": surface,
        "face": face,
        "approximate": not ok,
        "sections": n,
        "edge2_reversed": bool(reversed2),
        "backtracks": bool(backtracks),
        "side1": side1,
        "side2": side2,
    }


# ----------------------------------------------------------------------------- feature


def _edge_sub(subs):
    for s in subs:
        if s.rsplit(".", 1)[-1].startswith("Edge"):
            return s
    return None


def resolve_edge_face(link, tolerance=1e-4):
    """``(face, edge)`` of one ``PropertyLinkSub`` value of the feature: the edge by the path rule (every
    placement on the path applied), the face the link names or else the one face of the linked shape that
    contains the edge (``EdgeNotOnFace`` for none, ``AmbiguousFace`` for several)."""
    if not link or link[0] is None:
        raise ValueError("InvalidInput: an edge link is not set")
    obj, subs = link[0], link[1]
    if isinstance(subs, str):
        subs = (subs,)
    esub = _edge_sub(subs)
    if esub is None:
        raise ValueError("InvalidInput: %s has no edge in its link" % obj.Label)
    edge = SG.resolve_curve(obj, esub)
    if edge.ShapeType != "Edge":
        raise ValueError("InvalidInput: %s.%s is not an edge" % (obj.Label, esub))
    fsubs = [s for s in subs if s.rsplit(".", 1)[-1].startswith("Face")]
    if fsubs:
        face = Part.getShape(obj, fsubs[0], needSubElement=True)
        if face is None or face.isNull() or face.ShapeType != "Face":
            raise ValueError("InvalidInput: %s.%s is not a face" % (obj.Label, fsubs[0]))
        return face, edge
    prefix = esub.rpartition(".")[0]
    shape = Part.getShape(obj, prefix + ".", needSubElement=False, transform=True) if prefix else getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        raise ValueError("InvalidInput: %s has no shape" % obj.Label)
    found = [f for f in shape.Faces if _on_face(edge, f, tolerance)]
    if not found:
        raise ValueError("EdgeNotOnFace: no face of %s contains the edge" % obj.Label)
    if len(found) > 1:
        raise ValueError("AmbiguousFace: %d faces of %s contain the edge; link a face too" % (len(found), obj.Label))
    return found[0], edge


def build_blend(obj):
    """``makeBlendSurface`` on the links and properties of a feature."""
    tol = obj.Tolerance
    face1, edge1 = resolve_edge_face(obj.Edge1, tol)
    face2, edge2 = resolve_edge_face(obj.Edge2, tol)
    return makeBlendSurface(
        face1,
        edge1,
        face2,
        edge2,
        continuity1=_CONTINUITY.index(obj.Continuity1),
        continuity2=_CONTINUITY.index(obj.Continuity2),
        size1=obj.Size1,
        size2=obj.Size2,
        tolerance=tol,
        approximate=obj.AllowApproximation,
    )


class BlendSurface:
    """Proxy of the blend surface feature."""

    def __init__(self, obj):
        obj.Proxy = self
        add = obj.addProperty
        add("App::PropertyLinkSub", "Edge1", "Blend", "Edge of the first face (and optionally the face)")
        add("App::PropertyLinkSub", "Edge2", "Blend", "Edge of the second face (and optionally the face)")
        add("App::PropertyEnumeration", "Continuity1", "Blend", "Continuity with the first face")
        add("App::PropertyEnumeration", "Continuity2", "Blend", "Continuity with the second face")
        add("App::PropertyFloat", "Size1", "Blend", "Tangent length at the first face, times the chord")
        add("App::PropertyFloat", "Size2", "Blend", "Tangent length at the second face, times the chord")
        add("App::PropertyFloat", "Tolerance", "Blend", "Largest gap between the blend and a face, in mm")
        add("App::PropertyBool", "AllowApproximation", "Blend", "Accept a surface that misses the continuity thresholds")
        add("App::PropertyBool", "IsApproximate", "Blend", "True if the continuity thresholds were waived")
        add("App::PropertyInteger", "Sections", "Blend", "Number of stations used")
        add("App::PropertyFloat", "MaxGap", "Blend", "Largest measured gap over both sides, in mm")
        add("App::PropertyFloat", "MaxAngle", "Blend", "Largest measured angle over both sides, in degrees")
        add("App::PropertyFloat", "MaxCurvatureJump", "Blend", "Largest measured relative curvature jump over both sides")
        obj.Continuity1 = list(_CONTINUITY)
        obj.Continuity2 = list(_CONTINUITY)
        obj.Continuity1 = "G2"
        obj.Continuity2 = "G2"
        obj.Size1 = 1.0
        obj.Size2 = 1.0
        obj.Tolerance = 1e-4
        obj.AllowApproximation = False
        obj.IsApproximate = False
        obj.Sections = 0
        obj.MaxGap = obj.MaxAngle = obj.MaxCurvatureJump = 0.0
        self._readonly(obj)
        SG.ensure_input_guard(obj)

    @staticmethod
    def _readonly(obj):
        for name in ("IsApproximate", "Sections", "MaxGap", "MaxAngle", "MaxCurvatureJump"):
            if name in obj.PropertiesList:
                obj.setPropertyStatus(name, "ReadOnly")

    def execute(self, obj):
        SG.check_inputs_alive(obj)
        result = build_blend(obj)
        obj.Shape = result["face"]
        obj.IsApproximate = bool(result["approximate"])
        obj.Sections = int(result["sections"])
        sides = (result["side1"], result["side2"])
        obj.MaxGap = max(s["gap"] for s in sides)
        obj.MaxAngle = max(s["angle"] for s in sides)
        obj.MaxCurvatureJump = max(s["curvature_jump"] for s in sides)
        SG.record_inputs(obj)

    def onChanged(self, obj, prop):
        SG.note_link_change(self, obj, prop)

    def onDocumentRestored(self, obj):
        obj.Proxy = self
        self._readonly(obj)
        SG.ensure_input_guard(obj)

    def dumps(self):
        return None

    def loads(self, state):
        return None


def makeBlendSurfaceFeature(doc, edge1, edge2, name="BlendSurface"):
    """Add a blend surface feature to ``doc`` and return it (not yet recomputed).

    Each of ``edge1`` and ``edge2`` is ``(object, "EdgeN")`` or ``(object, ["FaceM", "EdgeN"])``."""
    obj = doc.addObject("Part::FeaturePython", name)
    BlendSurface(obj)
    for prop, entry in (("Edge1", edge1), ("Edge2", edge2)):
        o, subs = SG._split(entry)
        setattr(obj, prop, (o, subs))
    SG.record_inputs(obj)
    if App.GuiUp:
        try:
            import SurfaceBlendSurfaceCommands

            SurfaceBlendSurfaceCommands.ViewProviderBlend(obj.ViewObject)
        except Exception:
            pass
    return obj
