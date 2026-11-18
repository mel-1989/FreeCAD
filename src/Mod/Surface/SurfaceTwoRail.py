# SPDX-License-Identifier: LGPL-2.1-or-later

"""Two-rail sweep: one profile curve moved along two rail curves, as a B-spline face.

``makeTwoRailSweep(profile, rail1, rail2)`` places scaled copies of the profile at stations along the
rails and gives them, with the two rails, to ``Surface.makeGordon`` (Gordon surface through a curve
network), so the surface passes through both rails exactly and through every station section. The
number of stations grows (5, 9, 17, 33, 65) until the sections between the stations are within the
tolerance of the surface.

Placement rule: the station at normalised arc length ``s`` has the frame ``x`` = unit chord from the
point of rail 1 to the point of rail 2, ``T`` = sum of the rails' unit tangents, ``z`` = the part of
``T`` perpendicular to ``x``, ``y = z x x``. The profile is expressed in the frame at ``s = 0`` and
placed in the frame at ``s``, scaled by the chord ratio ``k(s)`` on all three local axes
(``Uniform``) or on ``x`` only (``Chord``).

``makeTwoRailFeature(doc, profile, rail1, rail2)`` adds a ``Part::FeaturePython`` with the links, the
tolerance and the scaling as properties; its ``Shape`` is the face.

Public sources (nothing is taken from CurvesWB): the birail construction as described in L. Piegl and
W. Tiller, *The NURBS Book*, 2nd ed., 1997 (chapter on swept surfaces); W. J. Gordon, "Spline-blended
surface interpolation through curve networks", J. Math. Mech. 18(10), 1969; OCCT 8.0 ``GeomFill_Gordon``
header documentation.
"""

import math

import FreeCAD as App
import Part

import SurfaceGordon as SG

__all__ = ["makeTwoRailSweep", "makeTwoRailFeature", "TwoRailSweep", "resolve_link", "build_two_rail"]

_STATIONS = (5, 9, 17, 33, 65)
_BIG = 1e50


# --------------------------------------------------------------------------- input curves


def _bounded(first, last):
    return math.isfinite(first) and math.isfinite(last) and abs(first) < _BIG and abs(last) < _BIG


def _nurbs_curve(edge):
    """The edge's 3D curve over its parameter range as a ``Part.BSplineCurve`` (placement applied)."""
    try:
        return edge.toNurbs().Edges[0].Curve
    except Exception:
        raise ValueError("InvalidInput: an edge has no 3D curve")


def _dir(edge, reverse, at_end):
    """Unit tangent of an edge at its start or end, in the direction the chain walks it."""
    last = at_end != reverse
    t = edge.tangentAt(edge.LastParameter if last else edge.FirstParameter)
    t.normalize()
    return -t if reverse else t


def _wire_to_edge(wire, label, smooth, tolerance):
    """The ordered edges of a wire joined into one B-spline edge (C0 joins allowed). With ``smooth`` (a
    rail) a join whose tangents differ by more than 1e-6 rad raises ``RailNotSmooth``, and the joined
    curve, whose parametrisation has a C0 knot at each join, is replaced by a non-rational B-spline
    within ``tolerance / 100`` of it."""
    edges = list(wire.OrderedEdges)
    if not edges:
        raise ValueError("InvalidInput: an empty wire")
    if len(edges) == 1:
        return Part.Edge(_nurbs_curve(edges[0]))
    ends = [(e.valueAt(e.FirstParameter), e.valueAt(e.LastParameter)) for e in edges]
    dist = lambda a, b: (a - b).Length  # noqa: E731
    fwd = min(dist(ends[0][1], ends[1][0]), dist(ends[0][1], ends[1][1]))
    bwd = min(dist(ends[0][0], ends[1][0]), dist(ends[0][0], ends[1][1]))
    flags = [bwd < fwd]
    cur = ends[0][0] if flags[0] else ends[0][1]
    for a, b in ends[1:]:
        rev = dist(cur, b) < dist(cur, a)
        flags.append(rev)
        cur = a if rev else b
    if smooth:
        for i in range(len(edges) - 1):
            t0 = _dir(edges[i], flags[i], True)
            t1 = _dir(edges[i + 1], flags[i + 1], False)
            angle = math.acos(max(-1.0, min(1.0, t0.dot(t1))))
            if angle > 1e-6:
                raise ValueError("RailNotSmooth: %s has a corner between its edges (%.3g rad)" % (label, angle))
    joined = None
    for e, rev in zip(edges, flags):
        c = _nurbs_curve(e)
        if rev:
            c.reverse()
        if joined is None:
            joined = c
        else:
            try:
                joined.join(c)
            except Exception:
                raise ValueError("InvalidInput: the edges of a wire could not be joined")
    edge = Part.Edge(joined)
    return _approximate_nonrational(edge, tolerance / 100) if smooth else edge


def _as_edge(item, label, smooth=False, tolerance=1e-4):
    """A bounded ``Part.Curve``, ``Part.Edge`` or ``Part.Wire`` as one edge."""
    if isinstance(item, Part.Wire):
        return _wire_to_edge(item, label, smooth, tolerance)
    if isinstance(item, Part.Edge):
        if item.isNull() or not _bounded(item.FirstParameter, item.LastParameter):
            raise ValueError("InvalidInput: %s is an empty or unbounded edge" % label)
        return item
    if all(hasattr(item, a) for a in ("FirstParameter", "LastParameter", "value", "toShape")):   # a Part.Curve
        if not _bounded(item.FirstParameter, item.LastParameter):
            raise ValueError("InvalidInput: %s is an unbounded curve" % label)
        return item.toShape()
    raise TypeError("TypeError: %s must be a bounded Part.Curve, a Part.Edge or a Part.Wire" % label)


# ------------------------------------------------------------------- rails and frames


class _Rail:
    """A rail walked by normalised arc length from its first to its last point."""

    def __init__(self, edge):
        self.edge = edge
        self.length = edge.Length

    def at(self, s):
        """(point, unit tangent) at the fraction ``s`` of the length."""
        param = self.edge.getParameterByLength(s * self.length)
        t = self.edge.tangentAt(param)
        t.normalize()
        return self.edge.valueAt(param), t


def _walking_edge(edge, backwards):
    """``edge``, or a copy running the other way when the profile meets its last point first."""
    if not backwards:
        return edge
    curve = _nurbs_curve(edge)
    curve.reverse()
    return Part.Edge(curve)


def _unit(v):
    w = App.Vector(v)
    w.normalize()
    return w


def _frame(rail1, rail2, s, tolerance):
    p1, t1 = rail1.at(s)
    p2, t2 = rail2.at(s)
    chord = p2 - p1
    length = chord.Length
    if length < tolerance:
        raise ValueError("DegenerateChord: the rails meet at s=%.4g (chord %.3g mm)" % (s, length))
    x = _unit(chord)
    t = t1 + t2
    perp = t - x * t.dot(x)
    if perp.Length < 1e-9:
        raise ValueError("DegenerateFrame: the rail tangents lie along the chord at s=%.4g" % s)
    z = _unit(perp)
    return p1, x, z.cross(x), z, length


def _matrix(o, x, y, z):
    return App.Matrix(x.x, y.x, z.x, o.x, x.y, y.y, z.y, o.y, x.z, y.z, z.z, o.z, 0, 0, 0, 1)


def _mapped_curve(curve, matrix):
    """``curve`` (a ``Part.BSplineCurve``) with every pole mapped through the affine ``matrix`` (exact
    for any affine map, which ``BSplineCurve.transform`` is not for a non-uniform scale)."""
    out = Part.BSplineCurve()
    out.buildFromPolesMultsKnots(
        [matrix.multiply(p) for p in curve.getPoles()],
        curve.getMultiplicities(),
        curve.getKnots(),
        curve.isPeriodic(),
        curve.Degree,
        curve.getWeights(),
    )
    return out


class _Placer:
    """Places the profile at any ``s`` by the frozen rule."""

    def __init__(self, profile_curve, rail1, rail2, tolerance, scaling):
        self.curve = profile_curve
        self.rail1, self.rail2, self.tol, self.scaling = rail1, rail2, tolerance, scaling
        o, x, y, z, length = _frame(rail1, rail2, 0.0, tolerance)
        self.inv0 = _matrix(o, x, y, z).inverse()
        self.length0 = length

    def section(self, s):
        o, x, y, z, length = _frame(self.rail1, self.rail2, s, self.tol)
        k = length / self.length0
        scale = App.Matrix()
        if self.scaling == "Uniform":
            scale.scale(k, k, k)
        else:
            scale.scale(k, 1.0, 1.0)
        return _mapped_curve(self.curve, _matrix(o, x, y, z).multiply(scale).multiply(self.inv0))


def _scan(rail1, rail2, tolerance):
    """Dense pre-check of the rails: a chord shorter than the tolerance, a chord that flips (the rails
    cross) or a frame that degenerates anywhere along them is refused before any surface is built."""
    prev = None
    count = 201
    for i in range(count):
        s = i / (count - 1.0)
        x = _frame(rail1, rail2, s, tolerance)[1]
        if prev is not None and prev.dot(x) < 0.0:
            raise ValueError("DegenerateChord: the rails cross near s=%.4g" % s)
        prev = x


# ----------------------------------------------------------------------- deviations


def _dist(point, shape):
    return Part.Vertex(point).distToShape(shape)[0]


def _curve_samples(curve, count):
    f, l = curve.FirstParameter, curve.LastParameter
    return [curve.value(f + (l - f) * j / (count - 1.0)) for j in range(count)]


def _edge_samples(edge, count):
    f, l = edge.FirstParameter, edge.LastParameter
    return [edge.valueAt(f + (l - f) * j / (count - 1.0)) for j in range(count)]


def _section_deviation(placer, face, n):
    """Largest distance from ``face`` of 21 points on each placed section at the n-1 station midpoints."""
    worst = 0.0
    for i in range(n - 1):
        section = placer.section((i + 0.5) / (n - 1.0))
        worst = max(worst, max(_dist(p, face) for p in _curve_samples(section, 21)))
    return worst


# ---------------------------------------------------------------- non-rational fallback


def _approximate_nonrational(edge, tolerance):
    """A non-rational B-spline edge within ``tolerance`` of ``edge`` (doubling the sample points)."""
    approx = edge
    for count in (101, 201, 401, 801):
        curve = Part.BSplineCurve()
        curve.approximate(Points=_edge_samples(edge, count), DegMax=5, Tolerance=tolerance / 20)
        approx = curve.toShape()
        mids = _edge_samples(edge, 2 * count - 1)[1::2]
        if max(_dist(p, approx) for p in mids) <= tolerance:
            break
    return approx


# --------------------------------------------------------------------------- main API


def _check_options(tolerance, scaling, max_sections):
    if (
        isinstance(tolerance, bool)
        or not isinstance(tolerance, (int, float))
        or not math.isfinite(tolerance)
        or tolerance <= 0
    ):
        raise ValueError("InvalidTolerance: tolerance must be finite and greater than 0")
    if scaling not in ("Uniform", "Chord"):
        raise ValueError("InvalidScaling: scaling must be 'Uniform' or 'Chord', not %r" % (scaling,))
    if isinstance(max_sections, bool) or not isinstance(max_sections, int) or max_sections < 2:
        raise ValueError("InvalidMaxSections: max_sections must be an integer of at least 2")


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


def _measure_edges(original, edge):
    """The edges the rail deviation is measured against: the original rail, wire edges included."""
    return list(original.Edges) if isinstance(original, Part.Wire) else [edge]


def _ends(edge):
    return edge.valueAt(edge.FirstParameter), edge.valueAt(edge.LastParameter)


def _orient(profile, r1, r2, tolerance):
    """(profile reversed?, rail 1 walked backwards?, rail 2 walked backwards?)."""
    pa, pb = _ends(profile)
    e1, e2 = _ends(r1), _ends(r2)

    def touch(p, ends):
        d0, d1 = (p - ends[0]).Length, (p - ends[1]).Length
        if min(d0, d1) > tolerance:
            return None
        return d1 < d0  # True: the profile touches the rail's last point

    for flip, (a, b) in ((False, (pa, pb)), (True, (pb, pa))):
        t1, t2 = touch(a, e1), touch(b, e2)
        if t1 is not None and t2 is not None:
            return flip, t1, t2
    raise ValueError("ProfileNotOnRails: the profile's ends are not within %.3g mm of an end of each rail" % tolerance)


def makeTwoRailSweep(profile, rail1, rail2, tolerance=1e-4, scaling="Uniform", approximate=False, max_sections=65):
    """B-spline surface of ``profile`` swept along ``rail1`` and ``rail2``; see the module doc.

    Returns a dict: ``surface``, ``approximate``, ``sections``, ``max_rail_deviation``,
    ``max_section_deviation`` and ``rails_reversed`` (two bools). ``max_sections`` below 5 means one
    try with exactly that many stations."""
    import Surface

    _check_options(tolerance, scaling, max_sections)
    prof = _as_edge(profile, "the profile")
    r1 = _as_edge(rail1, "rail 1", isinstance(rail1, Part.Wire), tolerance)
    r2 = _as_edge(rail2, "rail 2", isinstance(rail2, Part.Wire), tolerance)
    measured = [_measure_edges(rail1, r1), _measure_edges(rail2, r2)]
    if isinstance(rail1, Part.Wire) != isinstance(rail2, Part.Wire):
        # OCCT's Gordon construction refuses a rational guide next to a non-rational one: a wire rail was
        # replaced by a non-rational B-spline, so the other rail gets the same treatment
        if isinstance(rail1, Part.Wire):
            r2 = _approximate_nonrational(r2, tolerance / 100)
        else:
            r1 = _approximate_nonrational(r1, tolerance / 100)
    for edge, label in ((r1, "rail 1"), (r2, "rail 2")):
        a, b = _ends(edge)
        if (a - b).Length <= tolerance:
            raise ValueError("ClosedRail: %s is closed" % label)
    flip, back1, back2 = _orient(prof, r1, r2, tolerance)

    def attempt(e1, e2):
        e1, e2 = _walking_edge(e1, back1), _walking_edge(e2, back2)
        rail_a, rail_b = _Rail(e1), _Rail(e2)
        curve = _nurbs_curve(prof)
        if flip:
            curve.reverse()
        placer = _Placer(curve, rail_a, rail_b, tolerance, scaling)
        _scan(rail_a, rail_b, tolerance)
        best = None
        for n in _station_counts(max_sections):
            for j in range(2 * n - 1):  # stations and midpoints: a frame must exist at each
                _frame(rail_a, rail_b, j / (2.0 * n - 2.0), tolerance)
            sections = [placer.section(i / (n - 1.0)) for i in range(n)]
            result = Surface.makeGordon(sections, [e1, e2], tolerance=tolerance, approximate=approximate)
            face = Part.Face(result["surface"])
            best = (n, result, face, _section_deviation(placer, face, n))
            if best[3] <= tolerance:
                break
        return best

    try:
        n, result, face, dev = attempt(r1, r2)
    except ValueError as e:
        if not str(e).startswith("RationalDegreeOverflow"):
            raise
        n, result, face, dev = attempt(_approximate_nonrational(r1, tolerance / 10), _approximate_nonrational(r2, tolerance / 10))
    is_approx = bool(result["approximate"])
    if dev > tolerance:
        if not approximate:
            raise ValueError(
                "DeviationExceeded: sections between %d stations are %.3g mm off the surface (tolerance %.3g mm)"
                % (n, dev, tolerance)
            )
        is_approx = True
    rail_dev = max(max(_dist(p, face) for e in edges for p in _edge_samples(e, 51)) for edges in measured)
    return {
        "surface": result["surface"],
        "approximate": is_approx,
        "sections": n,
        "max_rail_deviation": rail_dev,
        "max_section_deviation": dev,
        "rails_reversed": (bool(back1), bool(back2)),
    }


# ----------------------------------------------------------------------------- feature


def resolve_link(link):
    """The curve one ``PropertyLinkSub`` value names (``None`` for an empty link)."""
    if not link:
        return None
    obj, subs = link[0], link[1]
    if isinstance(subs, str):
        subs = (subs,)
    return SG.resolve_curve(obj, subs[0] if subs else "")


def build_two_rail(obj):
    """``makeTwoRailSweep`` on the links, tolerance and scaling of a feature."""
    curves = [resolve_link(link) for link in (obj.Profile, obj.Rail1, obj.Rail2)]
    for c, label in zip(curves, ("Profile", "Rail1", "Rail2")):
        if c is None:
            raise ValueError("InvalidInput: %s is not set" % label)
    return makeTwoRailSweep(
        curves[0], curves[1], curves[2], tolerance=obj.Tolerance, scaling=obj.Scaling, approximate=obj.AllowApproximation
    )


class TwoRailSweep:
    """Proxy of the two-rail sweep feature."""

    def __init__(self, obj):
        obj.Proxy = self
        obj.addProperty("App::PropertyLinkSub", "Profile", "TwoRail", "Edge or wire of the profile curve")
        obj.addProperty("App::PropertyLinkSub", "Rail1", "TwoRail", "Edge or wire of the first rail")
        obj.addProperty("App::PropertyLinkSub", "Rail2", "TwoRail", "Edge or wire of the second rail")
        obj.addProperty(
            "App::PropertyFloat", "Tolerance", "TwoRail", "Largest miss between the stations and of the profile ends, in mm"
        )
        obj.addProperty(
            "App::PropertyEnumeration", "Scaling", "TwoRail", "How the profile scales with the chord between the rails"
        )
        obj.addProperty(
            "App::PropertyBool", "AllowApproximation", "TwoRail", "Accept a surface that misses the sections by more than the tolerance"
        )
        obj.addProperty("App::PropertyBool", "IsApproximate", "TwoRail", "True if the surface is not an exact interpolant")
        obj.addProperty(
            "App::PropertyFloat", "MaxDeviation", "TwoRail", "Largest miss of the surface from the rails and the sections, in mm"
        )
        obj.addProperty("App::PropertyInteger", "Sections", "TwoRail", "Number of stations used")
        obj.Scaling = ["Uniform", "Chord"]
        obj.Scaling = "Uniform"
        obj.Tolerance = 1e-4
        obj.AllowApproximation = False
        obj.IsApproximate = False
        obj.MaxDeviation = 0.0
        obj.Sections = 0
        for name in ("IsApproximate", "MaxDeviation", "Sections"):
            obj.setPropertyStatus(name, "ReadOnly")
        SG.ensure_input_guard(obj)

    def execute(self, obj):
        SG.check_inputs_alive(obj)
        result = build_two_rail(obj)
        obj.Shape = Part.Face(result["surface"])
        obj.IsApproximate = bool(result["approximate"])
        obj.MaxDeviation = max(result["max_rail_deviation"], result["max_section_deviation"])
        obj.Sections = int(result["sections"])
        SG.record_inputs(obj)

    def onChanged(self, obj, prop):
        SG.note_link_change(self, obj, prop)

    def onDocumentRestored(self, obj):
        obj.Proxy = self
        for name in ("IsApproximate", "MaxDeviation", "Sections"):
            if name in obj.PropertiesList:
                obj.setPropertyStatus(name, "ReadOnly")
        SG.ensure_input_guard(obj)

    def dumps(self):
        return None

    def loads(self, state):
        return None


def makeTwoRailFeature(doc, profile, rail1, rail2, name="TwoRailSweep"):
    """Add a two-rail sweep feature to ``doc`` and return it (not yet recomputed).

    Each of ``profile``, ``rail1`` and ``rail2`` is a document object or an ``(object, "SubName")`` pair."""
    obj = doc.addObject("Part::FeaturePython", name)
    TwoRailSweep(obj)
    for prop, entry in (("Profile", profile), ("Rail1", rail1), ("Rail2", rail2)):
        o, subs = SG._split(entry)
        setattr(obj, prop, (o, subs[0]))
    SG.record_inputs(obj)
    if App.GuiUp:
        try:
            import SurfaceTwoRailCommands

            SurfaceTwoRailCommands.ViewProviderTwoRail(obj.ViewObject)
        except Exception:
            pass
    return obj
