# SPDX-License-Identifier: LGPL-2.1-or-later
"""Component replace with joint reference remapping (G06 slice 2a): a JSON-returning Python API.

``plan(assembly, old, new)`` reports, without changing anything, what would happen to every
joint reference that reaches the part ``old`` if ``new`` took its place; ``apply(assembly, old,
new, choices=None, delete_old=False)`` does it in one undo transaction: it puts ``new`` in the
place of ``old``, rewrites the joint references that were matched, marks the joints that could
not be matched as broken (kept, suppressed, never solved against a guess), solves the assembly
and returns the report.

What "old" can be: a component of the assembly (a plain geometry object, an ``App::Link``, a link
array or one of its elements, or the same inside a sub-assembly) or the linked source object that
links of the assembly point to. A link or link array keeps its object and is repointed to ``new``
(so the element objects and every element placement of an array survive); a plain geometry
component is replaced by a new ``App::Link`` to ``new`` at the old placement, and the old object is
left in the document (hidden, out of the assembly) unless ``delete_old`` is set.

Matching. Every reference names one or two elements (face, edge, vertex) of the old part. They are
matched to elements of the new part by geometry in the part's local frame, after aligning the new
part to the old one (identity unless the principal axes of inertia and the centre of mass, tried in all four proper
sign combinations, overlap the old part better by 0.1 of the union volume; a tie between two such
alignments is reported as an ambiguous alignment, and a part that fits exactly only when mirrored is
reported as such and not matched). Within the aligned frame:

1. exact: same kind, same surface or curve type, same normal or axis (sign-free for axes), same
   radius, same size and a centre within 1e-6 mm; exactly one such element is a match (basis
   ``exact``);
2. adapted: otherwise the candidates are the elements of the same kind and type with the same
   normal or axis (within 1e-4 rad), the same concavity, a radius within 35 percent, an area or
   length ratio within 3 and at least 80 percent of the old element's neighbouring elements still
   around them; they are ranked
   by a cost (position relative to the part size, size change, radius change, neighbour change); a
   new element that is already the exact twin of another old element is not a candidate (a hole that
   was removed is not matched to the hole that stayed);
   the best one is a match (basis ``adapted``) unless a second one is within 10 percent of its
   cost (or 0.01), which makes the reference ``ambiguous``; no candidate makes it ``unmatched``
   with the reason.

The element index is never used to decide anything. A vertex or edge that belongs to a joint
reference together with a face is searched only among the sub-elements of the matched face.

Behaviour sources, all public: FreeCAD's App::Link and Assembly documentation (wiki.freecad.org),
OCCT shape and Boolean documentation; the F1 entity-id format of AgentCore.state; the idea of
signature-and-carrier matching comes from F1's own reference tracker, whose element-map basis does
not exist between two different parts.
"""

import json
import math
import re

import FreeCAD as App
import Part

SCHEMA = "cadfork.assembly.replace/1"

# stated tolerances
POS_TOL = 1e-6          # mm, centre of an exact match
SIZE_TOL = 1e-6         # relative, area or length of an exact match
ANG_EXACT = 1e-6        # rad, direction of an exact match
ANG_ADAPT = 1e-4        # rad, direction of an adapted match
RADIUS_ADAPT = 0.35     # largest relative radius change of an adapted match
SIZE_RATIO_MAX = 3.0    # largest area or length change factor of an adapted match
ADJ_MIN = 0.8           # smallest share of the old element's neighbours an adapted match must keep
AMBIG_ABS = 0.01        # second-best cost within this of the best is a tie
AMBIG_REL = 0.10        # or within this fraction of the best cost
IOU_SAME = 1e-6         # overlap difference below which two alignments are the same
IOU_EXACT = 1.0 - 1e-6  # overlap that counts as the same shape
IOU_FIT = 0.3           # identity is kept for a symmetric part when the two overlap at least this much
IOU_MARGIN = 0.1        # another alignment must overlap this much better than identity to replace it

_ELEMENT_RE = re.compile(r"^(Face|Edge|Vertex)(\d+)$")
_SKIP_GROUPS = ("App::Origin", "App::Line", "App::Plane", "App::Point", "Assembly::JointGroup",
                "Assembly::ViewGroup", "Assembly::BomGroup", "Assembly::SimulationGroup",
                "Assembly::SnapshotGroup")


class ReplaceError(Exception):
    """The replacement cannot be planned at all (unusable old or new part)."""


# ------------------------------------------------------------------ objects

def _entity(obj, sub=""):
    try:
        from AgentCore.verify.entities import entity
        return entity(obj, sub)
    except Exception:
        return "%s:%s" % (obj.Name, sub) if sub else obj.Name


def _is_link(obj):
    return obj.TypeId in ("App::Link", "App::LinkElement")


def _array_of(obj):
    """The array a LinkElement belongs to, else None."""
    if obj.TypeId != "App::LinkElement":
        return None
    for o in obj.InList:
        if o.TypeId == "App::Link" and obj in getattr(o, "ElementList", []):
            return o
    return None


def _in_tree(assembly, obj):
    seen = 0
    cur = obj
    while cur is not None and seen < 64:
        if cur == assembly:
            return True
        par = cur.getParentGeoFeatureGroup()
        if par is None:
            par = next((o for o in cur.InList if o.TypeId == "App::Link" and cur in getattr(o, "ElementList", [])), None)
        cur = par
        seen += 1
    return False


def _shape_leaf(obj, path=()):
    """(leaf object, tokens) of the geometry below ``obj``: the object itself if it carries a
    shape, else the single shape-carrying descendant of a part or group."""
    if obj.isDerivedFrom("Part::Feature") and obj.TypeId not in _SKIP_GROUPS:
        return obj, list(path)
    kids = [o for o in getattr(obj, "Group", []) if o.TypeId not in _SKIP_GROUPS]
    found = []
    for k in kids:
        if k.isDerivedFrom("Part::Feature") or getattr(k, "Group", None):
            try:
                found.append(_shape_leaf(k, tuple(path) + (k.Name,)))
            except ReplaceError:
                pass
    if len(found) != 1:
        raise ReplaceError("%s: cannot tell which single object carries its geometry" % obj.Label)
    return found[0]


def _source_leaf(host):
    """Geometry source of a link host (follows link chains)."""
    lo = host.LinkedObject
    while lo is not None and _is_link(lo) and getattr(lo, "LinkedObject", None) is not None:
        lo = lo.LinkedObject
    if lo is None:
        raise ReplaceError("%s links to nothing" % host.Label)
    return _shape_leaf(lo)


def _local_shape(leaf):
    shp = leaf.Shape.copy()
    shp.Placement = App.Placement()
    return shp


# -------------------------------------------------------------------- hosts

class _Host:
    """One component whose geometry is replaced."""

    def __init__(self, obj, mode):
        self.obj = obj
        self.mode = mode          # "repoint" (a link or array) or "adopt" (plain geometry)
        self.leaf = None
        self.old_tokens = []
        self.array_elements = []


def _find_hosts(assembly, old):
    hosts = []
    if old.TypeId == "App::LinkElement":
        arr = _array_of(old)
        if arr is None:
            raise ReplaceError("%s is a link element without an array" % old.Label)
        hosts = [_Host(arr, "repoint")]
    elif old.TypeId == "App::Link" and _in_tree(assembly, old):
        hosts = [_Host(old, "repoint")]
    elif _in_tree(assembly, old):
        hosts = [_Host(old, "adopt")]
    else:
        # the linked source object: every link of the assembly that points to it
        for o in old.Document.Objects:
            if o.TypeId == "App::Link" and _in_tree(assembly, o):
                lo = o.LinkedObject
                while lo is not None and _is_link(lo) and getattr(lo, "LinkedObject", None) is not None:
                    lo = lo.LinkedObject
                if lo == old:
                    hosts.append(_Host(o, "repoint"))
    if not hosts:
        raise ReplaceError("%s is not a component of %s and no link of it points there" % (old.Label, assembly.Label))
    for h in hosts:
        if h.mode == "repoint":
            h.leaf, h.old_tokens = _source_leaf(h.obj)
            h.array_elements = list(getattr(h.obj, "ElementList", []))
        else:
            h.leaf, h.old_tokens = _shape_leaf(h.obj)
            h.old_tokens = list(h.old_tokens)
    leaves = {h.leaf.Name for h in hosts}
    if len(leaves) != 1:
        raise ReplaceError("the components of %s do not share one geometry source" % old.Label)
    return hosts


# --------------------------------------------------------------- references

_JOINT_PROPS = ("Reference1", "Reference2")


def _is_joint(obj):
    return hasattr(obj, "JointType") and hasattr(obj, "Reference1") and hasattr(obj, "Reference2")


def _is_grounded(obj):
    return hasattr(obj, "ObjectToGround")


class _Ref:
    """One joint reference that reaches a host."""

    def __init__(self, joint, prop, host, comp, subs, chain_index, elements, mids, tail):
        self.joint = joint
        self.prop = prop
        self.host = host
        self.comp = comp
        self.subs = list(subs)
        self.chain_index = chain_index   # index of the host (or its element) in [comp] + path tokens
        self.elements = elements         # element names of the old part, one per sub ("" for none)
        self.mids = mids                 # object tokens between host and element, per sub
        self.tail = tail                 # array index tokens right after the host (kept)

    @property
    def key(self):
        return "%s.%s" % (self.joint.Name, self.prop)


def _split_sub(sub):
    """('a.b.', 'Face3') for 'a.b.Face3'; element '' when the sub ends in a dot."""
    head, _, last = sub.rpartition(".")
    return (head + "." if head else ""), last


def _host_matches(host, name_obj):
    if name_obj is None:
        return False
    if name_obj == host.obj:
        return True
    return host.mode == "repoint" and name_obj in host.array_elements


def _locate(host, comp, sub):
    """Position of the host in [comp] + tokens of ``sub``; None if not reached.

    Returns (index, mid tokens, tail tokens, element)."""
    prefix, element = _split_sub(sub)
    toks = [t for t in prefix.split(".") if t]
    chain = [comp.Name] + toks
    doc = comp.Document
    for i, name in enumerate(chain):
        if _host_matches(host, doc.getObject(name)):
            rest = chain[i + 1:]
            tail = []
            while rest and rest[0].isdigit():
                tail.append(rest.pop(0))
            return i, rest, tail, element
    return None


def _collect_refs(assembly, hosts):
    """Every joint reference that reaches one of the hosts."""
    out = []
    doc = assembly.Document
    for obj in doc.Objects:
        if not _is_joint(obj):
            continue
        for prop in _JOINT_PROPS:
            ref = getattr(obj, prop)
            if ref is None or len(ref) != 2 or ref[0] is None or not ref[1]:
                continue
            comp, subs = ref
            for host in hosts:
                loc = _locate(host, comp, subs[0])
                if loc is None:
                    continue
                idx, mids, tail, _el = loc
                elements, midl = [], []
                for s in subs:
                    l2 = _locate(host, comp, s)
                    if l2 is None:
                        elements.append("")
                        midl.append([])
                    else:
                        elements.append(l2[3])
                        midl.append(l2[1])
                out.append(_Ref(obj, prop, host, comp, subs, idx, elements, midl, tail))
                break
    return out


def _collect_grounded(assembly, hosts):
    out = []
    for obj in assembly.Document.Objects:
        if _is_grounded(obj) and obj.ObjectToGround is not None:
            for host in hosts:
                if host.mode == "adopt" and obj.ObjectToGround == host.obj:
                    out.append((obj, host))
    return out


# ----------------------------------------------------------------- geometry

class _El:
    __slots__ = ("kind", "name", "cls", "center", "size", "dir", "radius", "concave", "adj",
                 "pos_n", "parents", "children", "signed")

    def __init__(self, kind, name):
        self.kind = kind
        self.name = name
        self.cls = ""
        self.center = App.Vector()
        self.size = 0.0
        self.dir = None
        self.radius = None
        self.concave = None
        self.adj = ()
        self.pos_n = None
        self.parents = []
        self.children = []
        self.signed = False


def _unit(v):
    ln = v.Length
    return v * (1.0 / ln) if ln > 0 else v


def _sign_free(v):
    v = _unit(v)
    for c in (v.x, v.y, v.z):
        if abs(c) > 1e-9:
            return v * (1.0 if c > 0 else -1.0)
    return v


def _short(typeid):
    return typeid.split("::")[-1].replace("Geom", "")


class _Model:
    """Per-shape element table with sizes, directions and neighbours."""

    def __init__(self, shape):
        self.shape = shape
        bb = shape.BoundBox
        self.bmin = App.Vector(bb.XMin, bb.YMin, bb.ZMin)
        self.ext = App.Vector(bb.XLength, bb.YLength, bb.ZLength)
        self.diag = max(bb.DiagonalLength, 1e-12)
        self.els = {}
        self.order = {"Face": [], "Edge": [], "Vertex": []}
        for i, f in enumerate(shape.Faces, 1):
            self._add(self._face(f, i))
        for i, e in enumerate(shape.Edges, 1):
            self._add(self._edge(e, i))
        for i, v in enumerate(shape.Vertexes, 1):
            self._add(self._vertex(v, i))
        self._relations()
        for el in self.els.values():
            el.pos_n = self._norm(el.center)
        for el in self.els.values():
            el.adj = self._adjacency(el)

    def _norm(self, p):
        d = p - self.bmin
        return App.Vector(d.x / self.ext.x if self.ext.x > 1e-9 else 0.0,
                          d.y / self.ext.y if self.ext.y > 1e-9 else 0.0,
                          d.z / self.ext.z if self.ext.z > 1e-9 else 0.0)

    def _add(self, el):
        self.els[el.name] = el
        self.order[el.kind].append(el.name)

    def _face(self, f, i):
        el = _El("Face", "Face%d" % i)
        el.size = f.Area
        el.center = f.CenterOfMass
        try:
            s = f.Surface
            el.cls = _short(s.TypeId)
            u0, u1, v0, v1 = f.ParameterRange
            um, vm = (u0 + u1) / 2, (v0 + v1) / 2
            n = _unit(f.normalAt(um, vm))
            p = f.valueAt(um, vm)
            if el.cls == "Plane":
                el.dir, el.signed = n, True
            elif el.cls in ("Cylinder", "Cone", "Toroid", "Torus", "Sphere"):
                ax = getattr(s, "Axis", None)
                c = s.Center
                if el.cls == "Sphere":
                    el.concave = n.dot(p - c) < 0
                    el.radius = s.Radius
                else:
                    a = _unit(ax)
                    radial = (p - c) - a * a.dot(p - c)
                    el.concave = n.dot(radial) < 0
                    el.dir = _sign_free(a)
                    el.radius = getattr(s, "Radius", None)
                    if el.cls == "Cone":
                        el.radius = getattr(s, "SemiAngle", None)
        except Exception:
            el.cls = "Unknown"
        return el

    def _edge(self, e, i):
        el = _El("Edge", "Edge%d" % i)
        el.size = e.Length
        try:
            el.center = e.CenterOfMass
        except Exception:
            el.center = e.valueAt((e.FirstParameter + e.LastParameter) / 2)
        try:
            c = e.Curve
            el.cls = _short(c.TypeId)
            if el.cls in ("Line", "LineSegment"):
                el.cls = "Line"
                el.dir = _sign_free(e.Vertexes[-1].Point - e.Vertexes[0].Point)
            elif el.cls in ("Circle", "Ellipse"):
                el.dir = _sign_free(c.Axis)
                el.radius = getattr(c, "Radius", None)
        except Exception:
            el.cls = "Unknown"
        return el

    def _vertex(self, v, i):
        el = _El("Vertex", "Vertex%d" % i)
        el.cls = "Point"
        el.center = App.Vector(v.X, v.Y, v.Z)
        return el

    def _idx(self, sub):
        t, i = self.shape.findSubShape(sub)
        return "%s%d" % (t, i)

    def _relations(self):
        sh = self.shape
        for i, f in enumerate(sh.Faces, 1):
            fe = self.els["Face%d" % i]
            fe.children = [self._idx(e) for e in f.Edges] + [self._idx(v) for v in f.Vertexes]
        for i, e in enumerate(sh.Edges, 1):
            ee = self.els["Edge%d" % i]
            ee.children = [self._idx(v) for v in e.Vertexes]
            ee.parents = [self._idx(f) for f in sh.ancestorsOfType(e, Part.Face)]
        for i, v in enumerate(sh.Vertexes, 1):
            ve = self.els["Vertex%d" % i]
            ve.parents = [self._idx(e) for e in sh.ancestorsOfType(v, Part.Edge)]

    def _desc(self, name):
        el = self.els[name]
        d = ""
        if el.dir is not None:
            d = "%.3f,%.3f,%.3f" % tuple((c + 0.0) for c in (el.dir.x, el.dir.y, el.dir.z))
        return "%s:%s:%s" % (el.kind, el.cls, d)

    def _adjacency(self, el):
        if el.kind == "Face":
            names = set()
            for c in el.children:
                if c.startswith("Edge"):
                    names.update(p for p in self.els[c].parents if p != el.name)
            return tuple(sorted(self._desc(n) for n in names))
        if el.kind == "Edge":
            return tuple(sorted(self._desc(n) for n in el.parents))
        return tuple(sorted(self._desc(n) for n in el.parents))

    def edges_of_face_names(self, name):
        return [c for c in self.els[name].children if c.startswith("Edge")]


def _adj_similarity(a, b):
    """Share of the old element's neighbours (``a``) still present among the candidate's (``b``)."""
    if not a:
        return 1.0
    cb = {}
    for x in b:
        cb[x] = cb.get(x, 0) + 1
    kept = 0
    for x in a:
        if cb.get(x, 0) > 0:
            cb[x] -= 1
            kept += 1
    return kept / len(a)


def _angle(a, b, sign_free):
    if a is None and b is None:
        return 0.0
    if a is None or b is None:
        return math.pi
    d = a.dot(b)
    if sign_free:
        d = abs(d)
    return math.acos(max(-1.0, min(1.0, d)))


def _rel(a, b):
    m = max(abs(a), abs(b))
    return abs(a - b) / m if m > 0 else 0.0


def _compatible(o, n, ang_tol):
    """Why not, or None if the pair may match (kind and type, direction, concavity, radius)."""
    if o.kind != n.kind:
        return "different kind"
    if o.cls != n.cls:
        return "different type (%s vs %s)" % (o.cls, n.cls)
    if o.kind == "Vertex":
        return None
    if _angle(o.dir, n.dir, not o.signed) > ang_tol:
        return "different direction"
    if o.concave != n.concave:
        return "different concavity"
    if o.radius is not None and n.radius is not None and _rel(o.radius, n.radius) > RADIUS_ADAPT:
        return "radius changed by more than %d%%" % int(RADIUS_ADAPT * 100)
    return None


def _is_exact(o, n):
    if _compatible(o, n, ANG_EXACT) is not None:
        return False
    if (o.center - n.center).Length > POS_TOL:
        return False
    if o.kind != "Vertex" and _rel(o.size, n.size) > SIZE_TOL:
        return False
    if o.radius is not None and n.radius is not None and _rel(o.radius, n.radius) > SIZE_TOL:
        return False
    return True


def _cost(o, n, mo, mn):
    pos_abs = (o.center - n.center).Length / mo.diag
    pos_n = (o.pos_n - n.pos_n).Length
    cost = min(pos_abs, pos_n)
    if o.kind != "Vertex":
        cost += 0.5 * abs(math.log(max(n.size, 1e-12) / max(o.size, 1e-12)))
    if o.radius is not None and n.radius is not None:
        cost += _rel(o.radius, n.radius)
    if o.kind != "Vertex":
        cost += 0.5 * (1.0 - _adj_similarity(o.adj, n.adj))
    return cost


def _claims(mo, mn, kind):
    """New elements that exactly coincide with exactly one old element: {new name: old name}.

    An adapted match may not take an element another old element already owns (a hole that was
    removed must not be matched to the hole that stayed)."""
    out, seen = {}, {}
    for on in mo.order[kind]:
        o = mo.els[on]
        for nn in mn.order[kind]:
            if _is_exact(o, mn.els[nn]):
                seen[nn] = seen.get(nn, []) + [on]
    for nn, ons in seen.items():
        if len(ons) == 1:
            out[nn] = ons[0]
    return out


def _decide(o, pool, mo, mn, claims=None):
    """Match one old element against the new-part names in ``pool``.

    Returns dict(status, new, candidates, reason, basis, cost)."""
    exact = [n for n in pool if _is_exact(o, mn.els[n])]
    if len(exact) == 1:
        return {"status": "matched", "new": exact[0], "candidates": [], "basis": "exact", "cost": 0.0, "reason": ""}
    if len(exact) > 1:
        return {"status": "ambiguous", "new": None, "candidates": [(n, 0.0) for n in exact],
                "basis": "exact", "cost": 0.0, "reason": "several new elements coincide with the old one"}
    cands, why = [], {}
    for n in pool:
        ne = mn.els[n]
        if claims is not None and claims.get(n) not in (None, o.name):
            why["it is already the exact twin of %s" % claims[n]] = why.get("it is already the exact twin of %s" % claims[n], 0) + 1
            continue
        bad = _compatible(o, ne, ANG_ADAPT)
        if bad is None and o.kind != "Vertex":
            if max(ne.size, 1e-12) / max(o.size, 1e-12) > SIZE_RATIO_MAX or \
                    max(o.size, 1e-12) / max(ne.size, 1e-12) > SIZE_RATIO_MAX:
                bad = "size changed by more than a factor of %g" % SIZE_RATIO_MAX
            elif _adj_similarity(o.adj, ne.adj) < ADJ_MIN:
                bad = "its neighbouring elements changed"
        if bad is None:
            cands.append((_cost(o, ne, mo, mn), n))
        else:
            why[bad] = why.get(bad, 0) + 1
    if not cands:
        if why:
            reason = "no candidate: " + "; ".join("%s (%d)" % (k, v) for k, v in sorted(why.items()))
        else:
            reason = "the new part has no element of kind %s to compare" % o.kind
        return {"status": "unmatched", "new": None, "candidates": [], "basis": "", "cost": None, "reason": reason}
    cands.sort(key=lambda c: (c[0], c[1]))
    best = cands[0]
    tied = [c for c in cands[1:] if c[0] - best[0] <= max(AMBIG_ABS, AMBIG_REL * best[0])]
    if tied:
        allc = [best] + tied
        return {"status": "ambiguous", "new": None, "candidates": [(n, c) for c, n in allc],
                "basis": "adapted", "cost": best[0],
                "reason": "%d candidates are equally good" % len(allc)}
    return {"status": "matched", "new": best[1], "candidates": [], "basis": "adapted", "cost": best[0], "reason": ""}


def _match_element(o_name, mo, mn, pool_names, claims=None):
    """Hook for the mutants: decide one old element."""
    return _decide(mo.els[o_name], pool_names, mo, mn, claims)


# ---------------------------------------------------------------- alignment

def _frame(solid):
    """(centre, [axes]) of a solid from its inertia, right-handed, or None if degenerate."""
    pp = solid.PrincipalProperties
    if pp.get("SymmetryAxis") or pp.get("SymmetryPoint"):
        return None
    m = sorted(pp["Moments"])
    if any(abs(m[i + 1] - m[i]) <= 1e-5 * max(abs(m[i + 1]), 1e-300) for i in range(2)):
        return None   # two equal moments: the axes in their plane are arbitrary
    ax = [_unit(pp["FirstAxisOfInertia"]), _unit(pp["SecondAxisOfInertia"]), _unit(pp["ThirdAxisOfInertia"])]
    if ax[0].cross(ax[1]).dot(ax[2]) < 0:
        ax[2] = ax[2] * -1.0
    return solid.CenterOfMass, ax


def _frame_matrix(com, axes, signs=(1, 1, 1)):
    m = App.Matrix(axes[0].x * signs[0], axes[1].x * signs[1], axes[2].x * signs[2], com.x,
                   axes[0].y * signs[0], axes[1].y * signs[1], axes[2].y * signs[2], com.y,
                   axes[0].z * signs[0], axes[1].z * signs[1], axes[2].z * signs[2], com.z,
                   0, 0, 0, 1)
    return m


def _iou(a, b):
    try:
        va, vb = a.Volume, b.Volume
        vc = a.common(b).Volume
        u = va + vb - vc
        return vc / u if u > 0 else 0.0
    except Exception:
        return 0.0


def _single_solid(shape):
    sol = shape.Solids
    return sol[0] if len(sol) == 1 else None


def _choose_alignment(old_shape, new_shape, forced=None):
    """(Placement taking new-local to old-local, info dict, aligned new shape).

    The placement is None when no alignment can be chosen (info["method"] is "ambiguous" or
    "mirrored"). Identity keeps priority: another alignment replaces it only when its overlap with
    the old part is better by at least IOU_MARGIN.
    """
    info = {"method": "identity", "overlap": None, "candidates": 1}
    if forced is not None:
        shp = new_shape.copy()
        shp.transformShape(forced.toMatrix())
        info["method"] = "given"
        return forced, info, shp
    so, sn = _single_solid(old_shape), _single_solid(new_shape)
    if so is None or sn is None:
        return App.Placement(), info, new_shape.copy()
    iou_id = _iou(so, sn)
    info["overlap"] = iou_id
    if iou_id >= 1.0 - 1e-9:
        return App.Placement(), info, new_shape.copy()
    fo, fn = _frame(so), _frame(sn)
    if fo is None or fn is None:
        if iou_id >= IOU_FIT:
            info["note"] = "no principal axes (symmetric inertia), identity kept"
            return App.Placement(), info, new_shape.copy()
        info.update(method="ambiguous",
                    note="the part is symmetric about an axis or a point, so its inertia cannot fix its orientation")
        return None, info, None
    mo = _frame_matrix(*fo)
    proper = []
    for signs in ((1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1)):
        t = mo.multiply(_frame_matrix(*fn, signs=signs).inverse())
        shp = sn.copy()
        shp.transformShape(t)
        proper.append((_iou(so, shp), App.Placement(t), shp))
    best = max(proper, key=lambda c: c[0])
    info["candidates"] = 1 + len(proper)
    if best[0] < IOU_EXACT and iou_id < IOU_EXACT:
        # is the new part the mirror image of the old one?
        for _o, _plc, shp in proper:
            try:
                ms = shp.mirror(fo[0], fo[1][0])
            except Exception:
                continue
            if _iou(so, ms) >= IOU_EXACT:
                info.update(method="mirrored", overlap=1.0)
                return None, info, None
    if iou_id >= best[0] - IOU_MARGIN:
        return App.Placement(), info, new_shape.copy()
    close = [c for c in proper if c[0] >= best[0] - IOU_SAME]
    if len(close) > 1:
        info.update(method="ambiguous", overlap=best[0],
                    note="%d inertia alignments overlap equally well" % len(close))
        return None, info, None
    info.update(method="inertia", overlap=best[0])
    return best[1], info, best[2]


# --------------------------------------------------------------------- plan

def _worst(statuses):
    for s in ("unmatched", "ambiguous"):
        if s in statuses:
            return s
    return "matched"


def _kind_of(name):
    m = _ELEMENT_RE.match(name or "")
    return m.group(1) if m else ""


def _pool(kind, parent_match, mn):
    """Names of the new elements an element of ``kind`` may match, given the matched parent."""
    if parent_match is not None:
        pel = mn.els[parent_match]
        if pel.kind == "Face":
            names = [c for c in pel.children if c.startswith(kind)]
            if names:
                return sorted(set(names))
        elif pel.kind == "Edge":
            if kind == "Vertex":
                return sorted(set(pel.children))
            if kind == "Face":
                return sorted(set(pel.parents))
    return list(mn.order[kind])


def _plan_ref(rf, mo, mn, cache, choices, shape_status):
    """Fill rf.result: per sub the match decision, plus a status for the reference.

    A joint reference is a pair of sub-names. When the first is a face and the second an edge,
    FreeCAD numbers that edge within the face (``face.getElement("EdgeN")``), not within the
    shape; it is translated to the shape's own numbering for matching and back for the result."""
    subs_out = []
    matched_for = {}   # old element name (global) -> matched new name (global) within this reference
    first_old = rf.elements[0] if rf.elements else ""
    for pos, (sub, el) in enumerate(zip(rf.subs, rf.elements)):
        entry = {"sub": sub, "old_element": el, "new_element": None, "status": "matched",
                 "basis": "", "reason": "", "candidates": [], "old_global": el, "new_global": None}
        subs_out.append(entry)
        if not el:
            entry["basis"] = "whole part"
            continue
        if shape_status is not None:
            entry.update(status=shape_status[0], reason=shape_status[1])
            continue
        kind = _kind_of(el)
        local = pos == 1 and kind == "Edge" and _kind_of(first_old) == "Face" and first_old in mo.els
        gel = el
        if local:
            edges = mo.shape.getElement(first_old).Edges
            num = int(el[4:])
            if num < 1 or num > len(edges):
                entry.update(status="unmatched", reason="the old face has no edge %s" % el)
                continue
            gel = mo._idx(edges[num - 1])
            entry["old_global"] = gel
        if gel not in mo.els:
            entry.update(status="unmatched", reason="the old part has no element %s" % el)
            continue
        if gel in matched_for:
            entry["new_element"] = matched_for[gel]
            entry["new_global"] = matched_for[gel]
            entry["basis"] = "same element as the first sub-reference"
            continue
        parent = None
        first = subs_out[0]["new_global"] if pos == 1 else None
        if pos == 1 and first is not None and first != gel:
            parent = first
        res = _decide_cached(gel, kind, parent, mo, mn, cache)
        entry["basis"] = res["basis"]
        entry["cost"] = res["cost"]
        entry["reason"] = res["reason"]
        entry["status"] = res["status"]
        entry["candidates"] = [{"element": n, "cost": c} for n, c in res["candidates"]]
        if res["status"] == "ambiguous":
            pick = _choice_for(choices, rf.key, el)
            if pick is not None:
                if pick not in mn.els or _kind_of(pick) != kind:
                    raise ValueError("choice %r for %s is not an element of the new part" % (pick, rf.key))
                entry.update(status="matched", basis="user", reason="chosen by the caller", candidates=[])
                res = dict(res, status="matched", new=pick)
        if res["status"] == "matched":
            entry["new_global"] = res["new"]
            entry["new_element"] = _local_name(mn, subs_out[0]["new_global"], res["new"]) if local else res["new"]
            matched_for[gel] = res["new"]
    return subs_out


def _local_name(mn, face_name, edge_name):
    """The edge's number within the face (FreeCAD's convention for a face + edge reference)."""
    face = mn.shape.getElement(face_name)
    target = mn.shape.getElement(edge_name)
    for i, e in enumerate(face.Edges, 1):
        if e.isSame(target):
            return "Edge%d" % i
    return edge_name


def _choice_for(choices, key, old_el):
    if not choices or key not in choices:
        return None
    v = choices[key]
    if isinstance(v, dict):
        return v.get(old_el)
    return v


def _decide_cached(el, kind, parent, mo, mn, cache):
    ck = (el, parent)
    if ck not in cache:
        pool = _pool(kind, parent, mn)
        if ("claims", kind) not in cache:
            cache[("claims", kind)] = _claims(mo, mn, kind)
        cache[ck] = _match_element(el, mo, mn, pool, cache[("claims", kind)])
    return cache[ck]






def _json_safe(x):
    return json.loads(json.dumps(x))


def _resolve_new(new):
    """(leaf, tokens, top): the shape-carrying object, the path from ``top`` to it, the object to link."""
    top = new
    while top is not None and _is_link(top) and getattr(top, "LinkedObject", None) is not None:
        top = top.LinkedObject
    if top is None:
        raise ReplaceError("%s links to nothing" % new.Label)
    leaf, tokens = _shape_leaf(top)
    return leaf, list(tokens), top


def plan(assembly, old, new, choices=None, alignment=None):
    """What replacing ``old`` by ``new`` would do to every joint reference; changes nothing.

    ``choices`` maps ``"<joint>.<Reference1|Reference2>"`` to the new element for an ambiguous
    reference (a string, or a dict of old element to new element); ``alignment`` is an optional
    ``App.Placement`` taking the new part's local frame to the old part's.
    """
    return _plan(assembly, old, new, choices, alignment)[0]


def _plan(assembly, old, new, choices=None, alignment=None):
    hosts = _find_hosts(assembly, old)
    new_leaf, new_tokens, new_top = _resolve_new(new)
    old_leaf = hosts[0].leaf
    old_shape, new_shape = _local_shape(old_leaf), _local_shape(new_leaf)
    if old_shape.isNull() or new_shape.isNull():
        raise ReplaceError("the old or the new part has no shape (recompute the document first)")
    plc, ainfo, aligned = _choose_alignment(old_shape, new_shape, alignment)
    shape_status = None
    if plc is None:
        if ainfo["method"] == "mirrored":
            shape_status = ("unmatched", "the new part is the mirror image of the old one (opposite hand): "
                            "a remap would reverse the joint frames, pick the elements by hand")
        else:
            shape_status = ("ambiguous", "the alignment of the new part to the old one is ambiguous (%s): "
                            "pass an explicit alignment" % ainfo.get("note", ""))
    if plc is not None and any(h.array_elements for h in hosts) and not plc.isSame(App.Placement(), 1e-9):
        raise ReplaceError("a link array can only be repointed to a part with the old part's origin and orientation")
    refs = _collect_refs(assembly, hosts)
    mo = _Model(old_shape)
    mn = _Model(aligned) if aligned is not None else None
    cache = {}
    joints = {}
    for rf in refs:
        rf.result = _plan_ref(rf, mo, mn, cache, choices, shape_status)
        rf.status = _worst([s["status"] for s in rf.result])
        j = joints.setdefault(rf.joint.Name, {"joint": rf.joint.Name, "label": rf.joint.Label,
                                              "type": rf.joint.JointType, "references": []})
        j["references"].append({
            "reference": rf.prop,
            "component": rf.comp.Name,
            "component_entity": _entity(rf.comp),
            "old_part_entity": _entity(old_leaf),
            "status": rf.status,
            "elements": [
                {"old_sub": s["sub"], "old_element": s["old_element"],
                 "old_entity": _entity(old_leaf, s["old_global"]) if s["old_element"] else "",
                 "new_element": s["new_element"],
                 "new_entity": _entity(new_leaf, s["new_global"]) if s["new_global"] else "",
                 "status": s["status"], "basis": s["basis"], "reason": s["reason"],
                 "candidates": [{"element": c["element"], "entity": _entity(new_leaf, c["element"]),
                                 "cost": c["cost"]} for c in s["candidates"]]}
                for s in rf.result],
        })
    for j in joints.values():
        j["status"] = _worst([r["status"] for r in j["references"]])
        j["outcome"] = "remapped" if j["status"] == "matched" else "broken"
    counts = {"matched": 0, "ambiguous": 0, "unmatched": 0}
    for rf in refs:
        counts[rf.status] += 1
    report = {
        "schema": SCHEMA,
        "old": {"name": old.Name, "label": old.Label, "entity": _entity(old)},
        "new": {"name": new.Name, "label": new.Label, "entity": _entity(new)},
        "mode": hosts[0].mode,
        "hosts": [h.obj.Name for h in hosts],
        "alignment": {"method": ainfo["method"], "overlap": ainfo.get("overlap"),
                      "placement": _plc_json(plc) if plc is not None else None,
                      "note": ainfo.get("note", "")},
        "tolerances": {"position_mm": POS_TOL, "size_rel": SIZE_TOL, "angle_exact_rad": ANG_EXACT,
                       "angle_adapted_rad": ANG_ADAPT, "radius_change_max": RADIUS_ADAPT,
                       "size_ratio_max": SIZE_RATIO_MAX, "neighbours_min": ADJ_MIN},
        "joints": sorted(joints.values(), key=lambda j: j["joint"]),
        "grounded": [g.Name for g, _h in _collect_grounded(assembly, hosts)],
        "references": counts,
    }
    ctx = {"refs": refs, "hosts": hosts, "new_leaf": new_leaf, "new_tokens": new_tokens,
           "new_top": new_top, "plc": plc if plc is not None else App.Placement()}
    return _json_safe(report), ctx


def _plc_json(p):
    return {"base": [p.Base.x, p.Base.y, p.Base.z], "rotation": list(p.Rotation.Q)}


# -------------------------------------------------------------------- apply

def signature(doc):
    """Object-and-property signature of a document, for undo and redo comparisons."""
    out = {}
    skip = ("Shape", "Proxy", "ExpressionEngine", "_ElementMap", "AddSubShape")
    for obj in doc.Objects:
        props = {}
        for p in obj.PropertiesList:
            if p in skip:
                continue
            try:
                v = obj.getPropertyByName(p)
            except Exception:
                continue
            props[p] = _sig_value(v)
        out[obj.Name] = {"type": obj.TypeId, "label": obj.Label, "props": props}
    return out


def _sig_value(v):
    if isinstance(v, App.DocumentObject):
        return "@" + v.Name
    if isinstance(v, App.Placement):
        q = list(v.Rotation.Q)
        lead = next((c for c in q if abs(c) > 1e-9), 1.0)
        if lead < 0:
            q = [-c for c in q]   # q and -q are the same rotation
        return "P" + repr(tuple(round(x, 9) + 0.0 for x in v.Base)) + repr(tuple(round(x, 9) + 0.0 for x in q))
    if isinstance(v, App.Vector):
        return repr(tuple(round(x, 9) + 0.0 for x in v))
    if isinstance(v, (list, tuple)):
        return [_sig_value(x) for x in v]
    if isinstance(v, dict):
        return {k: _sig_value(x) for k, x in v.items()}
    if isinstance(v, float):
        return round(v, 9) + 0.0
    if isinstance(v, (int, str, bool)) or v is None:
        return v
    try:
        return round(float(v), 9) + 0.0
    except Exception:
        return str(type(v).__name__)


def _new_ref_value(rf, new_host, new_mids, elements):
    """(component object, subs) of a reference after the replace; ``elements`` are the new element names."""
    subs = []
    idx = rf.chain_index
    for sub, el in zip(rf.subs, elements):
        prefix, _old_el = _split_sub(sub)
        chain = [rf.comp.Name] + [t for t in prefix.split(".") if t]
        new_chain = chain[:idx + 1]
        if new_host is not None:
            new_chain[idx] = new_host.Name
        new_chain += list(rf.tail) + list(new_mids)
        path = ".".join(new_chain[1:])
        if el:
            subs.append(path + "." + el if path else el)
        else:
            subs.append(path + "." if path else "")
    comp = new_host if (new_host is not None and idx == 0) else rf.comp
    return comp, subs


def _new_link_placement(old_obj, plc):
    """Placement of the link that takes a plain component's place: its placement composed with the alignment."""
    return old_obj.Placement * plc


def _write_ref(joint, prop, comp, subs):
    setattr(joint, prop, (comp, list(subs)))


def _break_joint(doc, joint):
    """A joint with a reference that could not be matched: kept, suppressed, its reference marked."""
    if hasattr(joint, "Suppressed"):
        joint.Suppressed = True


def _solve(assembly):
    try:
        return assembly.solve()
    except Exception as exc:
        return "error: %s" % exc


def _apply_ref(rf, new_comp, new_tokens):
    new_host = new_comp.get(rf.host.obj.Name)
    ok = rf.status == "matched"
    elements = []
    for s in rf.result:
        if not s["old_element"]:
            elements.append("")
        else:
            elements.append(s["new_element"] if ok else s["old_element"])
    comp, subs = _new_ref_value(rf, new_host, new_tokens, elements)
    if not ok:
        # FreeCAD's broken-link marker: a leading "?" survives where a marked element name would be
        # stripped (the joint's execute() raises "Broken link" for any sub-name holding "?")
        subs = ["?" + s if s else s for s in subs]
    _write_ref(rf.joint, rf.prop, comp, subs)
    return ok


def apply(assembly, old, new, choices=None, delete_old=False, alignment=None):
    """Replace ``old`` by ``new`` in one undo transaction and return the report.

    Joints whose references could not all be matched are kept, suppressed and left with a
    ``?``-marked reference (FreeCAD's broken-link marker, a leading "?" on the sub-name); they are
    never solved against a guessed element. The old part's document object is kept unless ``delete_old``.
    """
    doc = assembly.Document
    report, ctx = _plan(assembly, old, new, choices=choices, alignment=alignment)
    refs, hosts, plc = ctx["refs"], ctx["hosts"], ctx["plc"]
    doc.openTransaction("Replace component")
    deleted = []
    try:
        new_comp = {}
        for h in hosts:
            if h.mode == "repoint":
                if not plc.isSame(App.Placement(), 1e-9):
                    h.obj.Placement = h.obj.Placement * plc
                h.obj.LinkedObject = ctx["new_top"]
            else:
                parent = h.obj.getParentGeoFeatureGroup()
                lk = parent.newObject("App::Link", h.obj.Label + "_replaced")
                lk.LinkedObject = ctx["new_top"]
                lk.Placement = _new_link_placement(h.obj, plc)
                new_comp[h.obj.Name] = lk
        broken = set()
        for rf in refs:
            if not _apply_ref(rf, new_comp, ctx["new_tokens"]):
                broken.add(rf.joint.Name)
        for name in sorted(broken):
            _break_joint(doc, doc.getObject(name))
        for g, h in _collect_grounded(assembly, hosts):
            if h.obj.Name in new_comp:
                g.ObjectToGround = new_comp[h.obj.Name]
        for h in hosts:
            if h.mode == "adopt":
                parent = h.obj.getParentGeoFeatureGroup()
                if parent is not None:
                    parent.removeObject(h.obj)
                h.obj.Visibility = False
        doc.recompute()
        solve_code = _solve(assembly)
        if delete_old:
            deleted = _delete_old(doc, hosts)
        doc.recompute()
    except Exception:
        doc.abortTransaction()
        raise
    doc.commitTransaction()
    report["applied"] = True
    report["solve"] = {"code": solve_code}
    report["deleted_old"] = deleted
    report["new_components"] = {k: v.Name for k, v in new_comp.items()}
    return _json_safe(report)


def _delete_old(doc, hosts):
    deleted = []
    for h in hosts:
        if h.mode == "adopt":
            name = h.obj.Name
            doc.removeObject(name)
            deleted.append(name)
    if not deleted:
        leaf = hosts[0].leaf
        users = [o for o in leaf.InList if not o.isDerivedFrom("App::DocumentObjectGroup")]
        if not users:
            name = leaf.Name
            doc.removeObject(name)
            deleted.append(name)
    return deleted
