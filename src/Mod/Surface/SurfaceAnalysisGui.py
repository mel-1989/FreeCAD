# SPDX-License-Identifier: LGPL-2.1-or-later

"""Analysis overlays in the 3D view: curvature colour map, draft map and zebra stripes.

An overlay is one ``SoSeparator`` named ``G09_OVERLAY_<kind>`` appended under the
view's ``SoFCUnifiedSelection`` node. It is unlit, unpickable and drawn with a
polygon offset, so it sits over the shape without taking part in selection. It
never creates a document object, never touches a view provider property and is
never saved with the document. The input shapes are never modified.

Values are computed at exactly the rendered vertices: each face is tessellated, the
(u, v) of every vertex is found from the surface (B-spline faces match
``getUVNodes()`` by position, which is much cheaper than projecting), and the
curvature, draft angle or face normal is evaluated there with the sign and
orientation conventions of :mod:`SurfaceAnalysis` (outward positive, face
orientation and location honoured).

Conventions
-----------
Ramp
    Five stops at s = 0, 0.25, 0.5, 0.75, 1: blue, cyan, green, yellow, red, linear
    in RGB between stops.
Range
    Without a user range, ``lo = -m`` and ``hi = +m`` with ``m`` the 95th
    percentile (linear interpolation) of the absolute finite values of all vertices
    of the overlay, floored at 1e-9. ``s = clamp((value - lo) / (hi - lo), 0, 1)``.
NaN
    A vertex whose value is not finite (a pole, an apex) is coloured (0.5, 0.5, 0.5).
Draft
    Slice 1's classes at ``min_draft``: positive (0, 2/3, 0), negative
    (0.8627, 0, 0), insufficient (1, 0.7843, 0).
Zebra
    ``t = acos(clamp(n . d, -1, 1)) / pi`` with ``n`` the orientation-aware face
    normal and ``d`` the unit direction, stored as the texture coordinate. The
    texture is 256 x 1; band ``k`` (0-based, at increasing ``t``) is black for even
    ``k`` and white for odd ``k``. Stripe edges lie where ``n . d = cos(k pi / N)``.

Sources (clean room): the published reflection-line and zebra technique, Klass,
"Correction of local surface irregularities using reflection lines",
Computer-Aided Design 12(2), 1980; the public Coin3D (Open Inventor) API
documentation for the scene-graph nodes; FreeCAD's own ``Part`` bindings and
:mod:`SurfaceAnalysis`.
"""

import math
import struct

import FreeCAD
import Part

import SurfaceAnalysis as SA

__all__ = ["show_curvature", "show_draft", "show_zebra", "remove", "clear", "Overlay"]

_PREFIX = "G09_OVERLAY_"
_NAN = float("nan")
_GREY = (0.5, 0.5, 0.5)
_RAMP = [
    (0.0, (0.0, 0.0, 1.0)),
    (0.25, (0.0, 1.0, 1.0)),
    (0.5, (0.0, 1.0, 0.0)),
    (0.75, (1.0, 1.0, 0.0)),
    (1.0, (1.0, 0.0, 0.0)),
]
_DRAFT_COLOR = {
    "positive": (0.0, 2.0 / 3.0, 0.0),
    "negative": (0.8627, 0.0, 0.0),
    "insufficient": (1.0, 0.7843, 0.0),
}
_KINDS = ("gauss", "mean", "kmax", "kmin")
_TEX_W = 256
_RANGE_FLOOR = 1e-9
_DEFAULT_DEFLECTION_FRACTION = 2e-3


class Overlay:
    """Handle of one overlay: ``node``, ``kind``, ``range`` and ``vertices``."""

    def __init__(self, node, kind, range_, vertices, view):
        self.node = node
        self.kind = kind
        self.range = range_
        self.vertices = vertices
        self._view = view

    def __repr__(self):
        return "<Overlay %s, %d vertices>" % (self.kind, len(self.vertices))


# ---------------------------------------------------------------- helpers


def _coin():
    from pivy import coin

    return coin


def _f32(p):
    """The vertex as the scene graph stores it: Coin coordinates are single-precision floats."""
    x, y, z = struct.unpack("<3f", struct.pack("<3f", p.x, p.y, p.z))
    return FreeCAD.Vector(x, y, z)


def _isnan(x):
    return x != x


def ramp_color(s):
    """Colour of the five-stop ramp at s in [0, 1]."""
    s = max(0.0, min(1.0, s))
    for i in range(len(_RAMP) - 1):
        s0, c0 = _RAMP[i]
        s1, c1 = _RAMP[i + 1]
        if s <= s1:
            f = (s - s0) / (s1 - s0)
            return tuple(c0[k] + f * (c1[k] - c0[k]) for k in range(3))
    return _RAMP[-1][1]


def _percentile(values, q):
    """numpy.percentile's default linear interpolation, ``q`` in [0, 100]."""
    vals = sorted(values)
    n = len(vals)
    if n == 0:
        return 0.0
    pos = (n - 1) * q / 100.0
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    return vals[lo] + (pos - lo) * (vals[hi] - vals[lo])


def _shape_of(target):
    if isinstance(target, Part.Shape):
        shape = target
    else:
        shape = getattr(target, "Shape", None)
        if not isinstance(shape, Part.Shape):
            raise ValueError("target must be a document object with a Shape, a Part.Shape or a Part.Face")
    if shape.isNull():
        raise ValueError("target has a null shape")
    if not shape.Faces:
        raise ValueError("target has no faces")
    return shape


def _check_deflection(deflection):
    if deflection is None:
        return None
    try:
        d = float(deflection)
    except (TypeError, ValueError):
        raise ValueError("deflection must be a positive number") from None
    if not (d > 0.0) or math.isinf(d):
        raise ValueError("deflection must be a positive number")
    return d


def _unit_vector(v, name):
    try:
        vec = FreeCAD.Vector(v)
    except Exception:
        raise ValueError("%s must be a vector" % name) from None
    if not (vec.Length > 0.0) or math.isinf(vec.Length):
        raise ValueError("%s must not be zero" % name)
    return vec * (1.0 / vec.Length)


def _find_selection_node(view):
    """The ``SoFCUnifiedSelection`` child of the view's scene graph."""
    _coin()  # getSceneGraph needs pivy's SWIG library loaded
    try:
        root = view.getSceneGraph()
    except Exception:
        root = None
    if root is None:
        raise RuntimeError("no 3D view available")
    for i in range(root.getNumChildren()):
        ch = root.getChild(i)
        if ch.getTypeId().getName().getString() == "SoFCUnifiedSelection":
            return ch
    raise RuntimeError("the view has no SoFCUnifiedSelection node")


def _active_view(view):
    if view is not None:
        return view
    try:
        import FreeCADGui as Gui

        doc = Gui.ActiveDocument
        v = doc.ActiveView if doc is not None else None
    except Exception:
        v = None
    if v is None:
        raise RuntimeError("no active 3D view")
    return v


def _tessellate(shape, deflection):
    """Per face: (index, face, vertices, triangles) with the tessellation of ``deflection``."""
    # tessellating a copy keeps the triangulation off the caller's shared TShapes (it would otherwise
    # show up in their BREP export)
    shape = shape.copy()
    faces = shape.Faces
    if deflection is None:
        bb = shape.BoundBox
        deflection = max(bb.DiagonalLength * _DEFAULT_DEFLECTION_FRACTION, 1e-6)
    out = []
    for i, f in enumerate(faces):
        verts, tris = f.tessellate(deflection)
        out.append((i + 1, f, verts, tris))
    return out


def _is_spline(face):
    return face.Surface.TypeId in ("Part::GeomBSplineSurface", "Part::GeomBezierSurface")


def _uv_for(face, verts):
    """(u, v) of each vertex: getUVNodes matched by position on splines, else parameter()."""
    surf = face.Surface
    uvs = [None] * len(verts)
    if _is_spline(face):
        table = {}
        try:
            nodes = face.getUVNodes()
        except Exception:
            nodes = []
        for u, v in nodes:
            p = face.valueAt(u, v)
            table.setdefault((round(p.x, 5), round(p.y, 5), round(p.z, 5)), (u, v))
        for i, p in enumerate(verts):
            uvs[i] = table.get((round(p.x, 5), round(p.y, 5), round(p.z, 5)))
    for i, p in enumerate(verts):
        if uvs[i] is None:
            uvs[i] = surf.parameter(p)
    return uvs


def _curv_value(ctx, kind, u, v):
    """Outward-positive curvature of ``kind`` at (u, v), NaN where undefined (as SurfaceAnalysis)."""
    if ctx.degenerate(u, v):
        return _NAN
    surf = ctx.surf
    try:
        if kind == "gauss":
            return surf.curvature(u, v, "Gauss")
        if kind == "mean":
            return ctx.sign * surf.curvature(u, v, "Mean") + 0.0
        # kmax/kmin: the flip of a forward face swaps and negates the bare values
        if ctx.sign < 0:
            return 0.0 - surf.curvature(u, v, "Min" if kind == "kmax" else "Max")
        return surf.curvature(u, v, "Max" if kind == "kmax" else "Min")
    except Exception:
        return _NAN


# ------------------------------------------------------------ scene graph


def _build_node(kind, per_face, colors, texcoords):
    """The overlay separator. ``per_face`` is a list of (vertices, triangles)."""
    coin = _coin()
    sep = coin.SoSeparator()
    sep.setName(_PREFIX + kind)

    ps = coin.SoPickStyle()
    ps.style = coin.SoPickStyle.UNPICKABLE
    sep.addChild(ps)
    lm = coin.SoLightModel()
    lm.model = coin.SoLightModel.BASE_COLOR
    sep.addChild(lm)
    po = coin.SoPolygonOffset()
    po.factor = -1.0
    po.units = -1.0
    po.styles = coin.SoPolygonOffset.FILLED
    sep.addChild(po)

    pts = []
    idx = []
    base = 0
    for verts, tris in per_face:
        pts.extend((p.x, p.y, p.z) for p in verts)
        for a, b, c in tris:
            idx.extend((base + a, base + b, base + c, -1))
        base += len(verts)

    if colors is not None:
        mat = coin.SoMaterial()
        mat.diffuseColor.setValues(0, len(colors), colors)
        sep.addChild(mat)
        mb = coin.SoMaterialBinding()
        mb.value = coin.SoMaterialBinding.PER_VERTEX_INDEXED
        sep.addChild(mb)
    else:
        mat = coin.SoMaterial()
        mat.diffuseColor = (1.0, 1.0, 1.0)
        sep.addChild(mat)

    if texcoords is not None:
        tx = coin.SoTexture2()
        tx.image.setValue(coin.SbVec2s(_TEX_W, 1), 3, texcoords["image"])
        tx.model = coin.SoTexture2.REPLACE
        tx.wrapS = coin.SoTexture2.CLAMP
        tx.wrapT = coin.SoTexture2.CLAMP
        sep.addChild(tx)
        tc = coin.SoTextureCoordinate2()
        tc.point.setValues(0, len(texcoords["t"]), [(t, 0.5) for t in texcoords["t"]])
        sep.addChild(tc)

    cd = coin.SoCoordinate3()
    cd.point.setValues(0, len(pts), pts)
    sep.addChild(cd)
    fs = coin.SoIndexedFaceSet()
    fs.coordIndex.setValues(0, len(idx), idx)
    if texcoords is not None:
        fs.textureCoordIndex.setValues(0, len(idx), idx)
    sep.addChild(fs)
    return sep


def _finish(kind, per_face, records, colors, range_, view, texcoords=None):
    view = _active_view(view)
    ufs = _find_selection_node(view)  # fail before building when there is no view
    node = _build_node(kind, per_face, colors, texcoords)
    ufs.addChild(node)
    return Overlay(node, kind, range_, records, view)


def _band_image(bands):
    texels = _TEX_W // bands
    data = bytearray()
    for i in range(_TEX_W):
        k = i // texels
        val = 255 if k % 2 else 0
        data += bytes((val, val, val))
    return bytes(data)


# ------------------------------------------------------------------- API


def show_curvature(target, kind="mean", range=None, view=None, deflection=None):
    """Colour the target by curvature (``gauss``, ``mean``, ``kmax`` or ``kmin``).

    Returns an :class:`Overlay`. ``range=(lo, hi)`` replaces the automatic symmetric
    range; the range used is in ``handle.range``.
    """
    if not isinstance(kind, str) or kind.lower() not in _KINDS:
        raise ValueError("kind must be one of %s" % ", ".join(_KINDS))
    kind = kind.lower()
    user = None
    if range is not None:
        try:
            lo, hi = float(range[0]), float(range[1])
        except Exception:
            raise ValueError("range must be (lo, hi)") from None
        if not (lo < hi) or math.isinf(lo) or math.isinf(hi):
            raise ValueError("range needs lo < hi")
        user = (lo, hi)
    deflection = _check_deflection(deflection)
    shape = _shape_of(target)
    view = _active_view(view)
    _find_selection_node(view)

    per_face, records = [], []
    for fi, face, verts, tris in _tessellate(shape, deflection):
        ctx = SA._FaceCtx(face)
        uvs = _uv_for(face, verts)
        for p, (u, v) in zip(verts, uvs):
            records.append(
                {"point": p, "face": fi, "u": u, "v": v, "value": _curv_value(ctx, kind, u, v)}
            )
        per_face.append(([_f32(p) for p in verts], tris))

    if user is None:
        finite = [abs(r["value"]) for r in records if not _isnan(r["value"]) and not math.isinf(r["value"])]
        m = max(_percentile(finite, 95.0), _RANGE_FLOOR)
        lo, hi = -m, m
    else:
        lo, hi = user
    for r in records:
        val = r["value"]
        if _isnan(val):
            r["color"] = _GREY
        else:
            r["color"] = ramp_color((val - lo) / (hi - lo))
    colors = [r["color"] for r in records]
    return _finish("curvature", per_face, records, colors, (lo, hi), view)


def show_draft(target, pull, min_draft=1.0, view=None, deflection=None):
    """Colour the target by draft class (positive, negative, insufficient) about ``pull``."""
    p = _unit_vector(pull, "pull")
    try:
        min_draft = float(min_draft)
    except (TypeError, ValueError):
        raise ValueError("min_draft must be a number") from None
    if _isnan(min_draft):
        raise ValueError("min_draft must be a number")
    deflection = _check_deflection(deflection)
    shape = _shape_of(target)
    view = _active_view(view)
    _find_selection_node(view)

    per_face, records = [], []
    for fi, face, verts, tris in _tessellate(shape, deflection):
        uvs = _uv_for(face, verts)
        for pt, (u, v) in zip(verts, uvs):
            try:
                angle = SA._draft_from_normal(face.normalAt(u, v), p)
            except Exception:
                angle = _NAN
            if _isnan(angle):
                color = _GREY
            else:
                color = _DRAFT_COLOR[SA._classify(angle, min_draft)]
            records.append(
                {"point": pt, "face": fi, "u": u, "v": v, "value": angle, "color": color}
            )
        per_face.append(([_f32(p) for p in verts], tris))
    colors = [r["color"] for r in records]
    return _finish("draft", per_face, records, colors, None, view)


def show_zebra(target, direction=(1, 0, 0), bands=16, view=None, deflection=None):
    """Draw zebra stripes: ``bands`` bands of the angle between the face normal and ``direction``."""
    d = _unit_vector(direction, "direction")
    if isinstance(bands, bool) or not isinstance(bands, int) or bands < 2 or bands > _TEX_W or _TEX_W % bands:
        raise ValueError("bands must be at least 2 and divide %d" % _TEX_W)
    deflection = _check_deflection(deflection)
    shape = _shape_of(target)
    view = _active_view(view)
    _find_selection_node(view)

    per_face, records, ts = [], [], []
    for fi, face, verts, tris in _tessellate(shape, deflection):
        uvs = _uv_for(face, verts)
        for pt, (u, v) in zip(verts, uvs):
            try:
                n = face.normalAt(u, v)
                t = math.acos(max(-1.0, min(1.0, n.dot(d)))) / math.pi
            except Exception:
                t = _NAN
            if _isnan(t):
                color, tex = _GREY, 0.0
            else:
                k = min(bands - 1, int(math.floor(t * bands)))
                val = 1.0 if k % 2 else 0.0
                color, tex = (val, val, val), t
            ts.append(tex)
            records.append(
                {"point": pt, "face": fi, "u": u, "v": v, "value": t, "color": color}
            )
        per_face.append(([_f32(p) for p in verts], tris))
    return _finish(
        "zebra", per_face, records, None, None, view,
        texcoords={"t": ts, "image": _band_image(bands)},
    )


def remove(handle):
    """Take one overlay out of its view; a second call does nothing."""
    node = getattr(handle, "node", None)
    view = getattr(handle, "_view", None)
    if node is None or view is None:
        return
    try:
        ufs = _find_selection_node(view)
    except Exception:
        return
    i = ufs.findChild(node)
    if i >= 0:
        ufs.removeChild(i)


def clear(view=None):
    """Take every G09 overlay out of the view."""
    ufs = _find_selection_node(_active_view(view))
    for i in reversed(range(ufs.getNumChildren())):
        ch = ufs.getChild(i)
        if ch.getName().getString().startswith(_PREFIX):
            ufs.removeChild(i)
