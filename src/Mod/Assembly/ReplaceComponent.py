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
part to the old one. The alignment is one decision per part, never mixed: identity unless the
principal axes of inertia and the centre of mass, tried in all four proper sign combinations,
overlap the old part better by 0.1 of the union volume; a tie between two such alignments and a part
that fits exactly only when mirrored are reported (ambiguous alignment, or mirrored) and not matched:
every reference of that part is then ambiguous or unmatched, and the caller resolves it by passing
the alignment. Two more situations make the alignment ambiguous although identity would fit.

The symmetry rule (``_symmetry_verdict``). If the old part is symmetric under a proper turn T (its
overlap with itself turned is at least 1 - 1e-6; T is a 180 degree turn about a principal axis of
inertia through the centre of mass, or a 90, 120, 180, 240 or 270 degree turn about the distinct axis
where two moments are equal, or a quarter or half turn about a world axis where all three are) and the
new part is not geometrically the old one, geometry alone cannot say whether the new part is placed as
it is or turned by T: the old part cannot tell the two readings apart, so any change (a hole added off
centre, a face moved, a feature removed) reads either way. Every reference of the part is planned under
identity and under T applied to the alignment. If the two readings send a reference to different new
elements, or one matches it and the other does not, the reference is ambiguous and so is the part's
alignment, with "as placed" and the turned reading as candidates; if every reference goes to the same
new element under both (a face the turn maps onto itself, such as the cylinder of a centred hole), the
part stays matched as placed. That includes a doubly symmetric plate that gains an off-centre hole
without being turned: nothing in the geometry says it was not, so it is reported unless every referenced
element is invariant. This is the D6 rule: ambiguous is reported, never picked silently, whatever kind
of element the reference holds (face, edge, vertex, face and edge, face and vertex). A turn under which
the new part is symmetric about its own centre of mass is left out: the new part as placed and turned
is then one body, and the joint ends up on the same place of the assembly either way (a resize that
keeps the symmetry, from any corner, stays matched). A resize that keeps the topology (same face, edge
and vertex counts and adjacency, every face with a twin of the same type and normal or axis as placed)
stands as placed whatever the symmetry: position and size are free, features are not.

The near twin rule, for parts that are not symmetric. A part turned and also changed (a bracket turned
180 degrees that gains a small hole or boss, or loses an end) has no exact twins left, so the question is
asked with near twins: a face is a near twin of an old face when it has the same kind,
surface type and concavity, a direction within 0.02 rad, a radius within 10 percent, an area within
25 percent and a centre within 3 percent of the old part's diagonal. An alternative that overlaps the
old part within 0.1 of identity (or better) and recognises by near twins an old face that identity
does not (for instance the hole the joint is on), at least as many faces in all, makes the alignment
ambiguous; one that recognises only faces identity recognises too, or fewer, does not, so a plain
revision with a small added feature stays unambiguous. A part that fits about as well as placed as
turned and explains a different set of the old faces by exact twins is ambiguous too. The
alternatives are the four proper inertia sign combinations; their translation comes
from the centre of mass, so it is approximate when the part changed, and the elements are then
matched by the adapted rules. Within the aligned frame:

1. exact: same kind, same surface or curve type, same normal or axis (sign-free for axes), same
   radius, same size and a centre within 1e-6 mm; exactly one such element is a match (basis
   ``exact``);
2. adapted: otherwise the candidates are the elements of the same kind and type with the same
   normal or axis (within 1e-4 rad), the same concavity, a radius within 35 percent, an area or
   length ratio within 3 and at least 80 percent of the old element's neighbouring elements still
   around them; a surface or circle with a radius may be moved or resized but not both (an axis
   shift of more than 2 percent of the part's diagonal together with a radius change of more than
   5 percent is a different feature, not a revision of this one); the candidates are ranked by a
   cost (position relative to the part size, size change, radius change, neighbour change);
3. one to one: a new element belongs to one old element. Every old element of the same kind (not
   only the referenced ones) that is eligible for a candidate is its rival: a rival that is an
   exact twin, or whose cost is lower by more than half again and 0.01 beyond the candidate's own
   (cost_o >= 2 * cost_rival + 0.01), owns it, and the candidate drops out; a rival whose cost is
   within that band makes the candidate contested and the reference ``ambiguous`` (both old elements
   see the conflict, neither is given the element); a rival clearly worse than the candidate is
   ignored. The best remaining candidate is a match (basis ``adapted``) unless a second one is
   within 10 percent of its cost (or 0.01), which makes the reference ``ambiguous``; no candidate
   makes it ``unmatched`` with the reason, naming the old element that owns the candidates that
   dropped out.

Report contract for a panel (JSON, ``plan()`` and ``apply()`` return it). ``alignment`` always has
``method`` ("identity", "inertia", "given", "ambiguous" or "mirrored"), ``overlap``, ``placement``
(the chosen placement, or null), ``note`` and ``alternatives`` (placements). When ``method`` is
"ambiguous", ``alignment.candidates`` lists the readings the user can pick, "as placed" first:
``{"label": "as placed" | "turned 180\u00b0 about Y" | ..., "placement": {"base": [x, y, z], "rotation":
[qx, qy, qz, qw]}, "counts": {"matched": n, "ambiguous": n, "unmatched": n}, "error": ""}``. ``counts``
counts references (not elements) and come from re-planning with that placement as ``alignment=``, so
``plan(..., alignment=App.Placement(App.Vector(*p["base"]), App.Rotation(*p["rotation"])))`` (and
``apply`` with the same) reproduces them exactly; ``counts`` is null and ``error`` set when the
alignment cannot be used (a link array needs identity). A mirrored part has
only the "as placed" candidate (or none). While the alignment is ambiguous every
reference is ambiguous with no element candidates: pick an alignment first, re-plan, then resolve
the remaining ambiguous references. Each element of an ambiguous reference carries ``candidates``:
``[{"element": "Face7", "entity": ..., "cost": 0.12, "basis": "adapted"}]``, element names being
the new part's own numbering (``Face``, ``Edge`` or ``Vertex`` of the whole shape, also for the
edge of a face and edge reference). To pick one, pass ``choices={"<Joint>.<Reference1|Reference2>":
choice}`` to ``plan`` or ``apply``, where ``choice`` is either one element name, applied to every
ambiguous element of that reference, or a dict ``{"<old element>": "<new element>"}`` (the old
element as reported in ``old_element``) for a reference with several. A choice must be an element of
the same kind on the new part (any, not only the listed candidates; otherwise ``ValueError``); the
element then reports status "matched" with basis "user". A choice for a reference that is not
ambiguous is ignored.

Broken joints. A joint with a reference that was not matched is never deleted and never solved
against a guess: it is suppressed, its unmatched sub-names get FreeCAD's broken-link marker (a leading
"?", which Joint.execute turns into a recompute error and UtilsAssembly.findPlacement into a
BrokenReferenceError instead of an identity placement), and it carries a persistent string property
``ReplaceBroken`` that says what the matcher found (empty when the joint is sound; ``is_broken(joint)``
reads it). The suppression the user had set is remembered in ``ReplaceWasSuppressed`` and given back by ``repair``.
Unsuppressing such a joint is refused with a console error (JointObject.Joint.onChanged) because
the solver would skip it without a word. ``repair(joint, reference, elements)`` sets the chosen elements
(checked against the part), clears the marker, unsuppresses and solves.

Deleting the old part. ``delete_old`` removes only what nothing else uses: an object that anything outside
the replaced instance still uses (``InList``, a link or an expression, in any open document) is kept and
listed in the report's ``kept_old`` with the users; a PartDesign body goes with its own features and
origin, or stays whole.

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
MOVE_TOL = 0.02         # axis shift (fraction of the part's diagonal) above which a feature counts as moved
RESIZE_TOL = 0.05       # relative radius change above which a feature counts as resized
CLEAR_REL = 1.0         # a rival must beat a candidate by this fraction (plus AMBIG_ABS) to own it
IN_PLACE_IOU = 0.02       # an in-place half turn is a candidate only when it overlaps the old part this close to identity
IN_PLACE_MIN = 0.5        # ... and only when the new part still overlaps the old one this much as placed
IN_PLACE_TURNS = True  # also try the half turns about the old part's own axes as alignments (see _choose_alignment)
NEAR_IOU = 0.1          # an alternative alignment this much below identity's overlap (or better) is compared by near twins
NEAR_ANG = 0.02         # rad, largest direction difference of a near twin face (about 1 degree)
NEAR_SIZE = 0.25        # relative, largest area difference of a near twin face
NEAR_RADIUS = 0.10      # relative, largest radius difference of a near twin face
NEAR_POS = 0.03         # largest distance between the centres of near twin faces, as a fraction of the part's diagonal

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
                 "pos_n", "parents", "children", "signed", "axpt")

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
        self.axpt = None      # a point on the axis (cylinder, circle) or the centre (sphere)


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
                    el.axpt = c
                else:
                    a = _unit(ax)
                    radial = (p - c) - a * a.dot(p - c)
                    el.concave = n.dot(radial) < 0
                    el.dir = _sign_free(a)
                    el.radius = getattr(s, "Radius", None)
                    if el.cls == "Cylinder":
                        el.axpt = c
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
                if el.cls == "Circle":
                    el.axpt = c.Center
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
            # round before formatting: a rotated part has -1e-16 where the original has 0, and "-0.000" is not "0.000"
            d = "%.3f,%.3f,%.3f" % tuple((round(c, 3) + 0.0) for c in (el.dir.x, el.dir.y, el.dir.z))
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


def _axis_offset(o, n):
    """Distance between the axes (cylinder, circle) or the centres (sphere) of two elements, or None."""
    if o.axpt is None or n.axpt is None:
        return None
    v = n.axpt - o.axpt
    if o.dir is not None:
        v = v - o.dir * o.dir.dot(v)
    return v.Length


def _adapt_reason(o, n, mo):
    """Why ``n`` cannot be an adapted match of ``o``, or None if it may be."""
    bad = _compatible(o, n, ANG_ADAPT)
    if bad is not None:
        return bad
    if o.kind != "Vertex":
        if max(n.size, 1e-12) / max(o.size, 1e-12) > SIZE_RATIO_MAX or \
                max(o.size, 1e-12) / max(n.size, 1e-12) > SIZE_RATIO_MAX:
            return "size changed by more than a factor of %g" % SIZE_RATIO_MAX
        if _adj_similarity(o.adj, n.adj) < ADJ_MIN:
            return "its neighbouring elements changed"
    if o.radius is not None and n.radius is not None:
        off = _axis_offset(o, n)
        if off is not None and off > MOVE_TOL * mo.diag and _rel(o.radius, n.radius) > RESIZE_TOL:
            return "moved by %.3g mm and resized by %d%% (a different feature, not a revision of this one)" % (
                off, int(round(100 * _rel(o.radius, n.radius))))
    return None


class _Table:
    """Who else wants a new element: for each new element of one kind, the old elements that are
    eligible for it with their cost (0 for an exact twin). Built lazily, per new element."""

    def __init__(self, mo, mn, kind):
        self.mo, self.mn, self.kind = mo, mn, kind
        self._rivals = {}

    def rivals(self, n_name):
        if n_name not in self._rivals:
            ne = self.mn.els[n_name]
            out = []
            for on in self.mo.order[self.kind]:
                o = self.mo.els[on]
                if o.cls != ne.cls:
                    continue
                if _is_exact(o, ne):
                    out.append((0.0, on))
                elif _adapt_reason(o, ne, self.mo) is None:
                    out.append((_cost(o, ne, self.mo, self.mn), on))
            self._rivals[n_name] = out
        return self._rivals[n_name]

    def verdict(self, o_name, n_name, cost):
        """("free",), ("lost", other, why) or ("contested", other, why) for old element ``o_name`` taking ``n_name``."""
        contested = None
        for c2, o2 in self.rivals(n_name):
            if o2 == o_name:
                continue
            if c2 == 0.0:
                return ("lost", o2, "it is the exact twin of %s" % o2)
            if cost >= (1.0 + CLEAR_REL) * c2 + AMBIG_ABS:
                return ("lost", o2, "it is a better fit for %s" % o2)
            if c2 >= (1.0 + CLEAR_REL) * cost + AMBIG_ABS:
                continue
            if contested is None:
                contested = ("contested", o2, "%s fits it about as well" % o2)
        return contested or ("free",)


def _claims(mo, mn, kind):
    """The one-to-one table for one kind of element (see the module docstring)."""
    return _Table(mo, mn, kind)


def _decide(o, pool, mo, mn, table=None):
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
        bad = _adapt_reason(o, ne, mo)
        if bad is None:
            cands.append((_cost(o, ne, mo, mn), n))
        else:
            why[bad] = why.get(bad, 0) + 1
    contested = {}
    if table is not None and cands:
        kept = []
        for c, n in cands:
            v = table.verdict(o.name, n, c)
            if v[0] == "lost":
                key = "%s is not available: %s" % (n, v[2])
                why[key] = why.get(key, 0) + 1
            else:
                kept.append((c, n))
                if v[0] == "contested":
                    contested[n] = v[1]
        cands = kept
    if not cands:
        if why:
            reason = "no candidate: " + "; ".join("%s (%d)" % (k, v) for k, v in sorted(why.items()))
        else:
            reason = "the new part has no element of kind %s to compare" % o.kind
        return {"status": "unmatched", "new": None, "candidates": [], "basis": "", "cost": None, "reason": reason}
    cands.sort(key=lambda c: (c[0], c[1]))
    best = cands[0]
    tied = [c for c in cands[1:] if c[0] - best[0] <= max(AMBIG_ABS, AMBIG_REL * best[0])]
    if tied or best[1] in contested:
        allc = [best] + tied
        reason = "%d candidates are equally good" % len(allc) if tied else \
            "%s is also wanted by %s of the old part, which fits it %s" % (
                best[1], contested[best[1]], "about as well")
        return {"status": "ambiguous", "new": None, "candidates": [(n, c) for c, n in allc],
                "basis": "adapted", "cost": best[0], "reason": reason}
    return {"status": "matched", "new": best[1], "candidates": [], "basis": "adapted", "cost": best[0], "reason": ""}


def _match_element(o_name, mo, mn, pool_names, table=None):
    """Hook for the mutants: decide one old element."""
    return _decide(mo.els[o_name], pool_names, mo, mn, table)


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


def _face_twins(mo, shape):
    """{old face: frozenset of the faces of ``shape`` that are its exact twins} (empty entries left out)."""
    mn = _Model(shape)
    out = {}
    for on in mo.order["Face"]:
        o = mo.els[on]
        hits = frozenset(nn for nn in mn.order["Face"] if _is_exact(o, mn.els[nn]))
        if hits:
            out[on] = hits
    return out


def _is_near(o, n, diag):
    """Are two faces the same face of a part that gained or lost a small feature elsewhere?

    Same kind and surface type, direction within NEAR_ANG, same concavity, radius within NEAR_RADIUS,
    area within NEAR_SIZE and centres within NEAR_POS of the old part's diagonal. The tolerances are
    loose on purpose: an alternative alignment comes from the inertia frame, which a small added hole
    or boss shifts by a fraction of a millimetre."""
    if o.kind != n.kind or o.cls != n.cls or o.concave != n.concave:
        return False
    if _angle(o.dir, n.dir, not o.signed) > NEAR_ANG:
        return False
    if o.radius is not None and n.radius is not None and _rel(o.radius, n.radius) > NEAR_RADIUS:
        return False
    if _rel(o.size, n.size) > NEAR_SIZE:
        return False
    return (o.center - n.center).Length <= NEAR_POS * diag


def _near_twins(mo, shape):
    """The old faces that have a near twin (``_is_near``) among the faces of ``shape``."""
    mn = _Model(shape)
    return frozenset(on for on in mo.order["Face"]
                     if any(_is_near(mo.els[on], mn.els[nn], mo.diag) for nn in mn.order["Face"]))


def _readings_agree(probe, readings, tol=None):
    """Do all readings, [(shape, placement)], send every reference to the same new element?

    With no ``probe`` (no reference to ask about) nothing is known and the answer is False, so the
    caller keeps its ambiguous verdict. The first reading is the one the others are compared with."""
    if probe is None:
        return False
    first = probe(*readings[0])
    return all(not _readings_differ(first, probe(*r), tol=tol) for r in readings[1:])


def _near_conflict(mo, new_shape, comp, probe=None):
    """Alternatives in ``comp`` that may be the reading the designer meant, as [(candidate, faces)].

    Without ``probe``: the part fits as placed and as turned, and the turned reading recognises faces
    (for instance the hole the joint is on) that the unturned reading sees changed or gone, and
    recognises at least as many faces in all (a turned reading that recognises other faces than
    identity, as many or more, is as good an explanation as identity: nothing says which one is meant).
    An alternative that explains only faces identity explains too, or fewer faces than identity does,
    is no conflict: identity stays.

    With ``probe`` the references decide: an alternative that recognises at least as many old faces as
    identity is a candidate when it sends some reference to another new element than identity does
    (``_readings_differ``), whether or not the faces it recognises are the same ones (a new part that
    is symmetric under the turn recognises the same old faces turned and as placed, and still puts the
    top rim of a hole on the bottom one), and is no candidate when every reference lands on the same
    element. Parts that are symmetric under a turn never get here (``_symmetry_verdict`` decides them):
    this is the rule for parts that are not."""
    near_id = _near_twins(mo, new_shape)
    ref_id = probe(new_shape, App.Placement()) if probe is not None else None
    out, seen = [], []
    for c in comp:
        near_c = _near_twins(mo, c[2])
        extra = near_c - near_id
        if len(near_c) < len(near_id):
            continue
        if probe is not None:
            # a reading that recognises faces identity does not is a candidate when any reference differs;
            # one that recognises the same faces only when a reference both readings match goes to two places
            if _readings_differ(ref_id, probe(c[2], c[1]), both=not extra, tol=None if not extra else NEAR_POS * mo.diag):
                out.append((c, extra))
        elif extra and extra not in seen:
            seen.append(extra)
            out.append((c, extra))
    return out


def _alignment_conflict(old_shape, new_shape, iou_id, proper, probe=None):
    """Is identity, which the overlap alone would keep, contradicted by an alternative alignment?

    The alternatives that count overlap the old part at least as well as identity does (within
    IOU_SAME) and no better than IOU_MARGIN above it (better than that replaces identity before we
    get here). Faces are compared by exact twins: if identity and an alternative both explain some old
    faces exactly but by different new faces, the part fits as placed and as turned and nothing in
    the geometry says which one the designer meant: ("ambiguous", (note, alternatives)). If identity
    explains no old face and an alternative explains some, the alternative is adopted:
    ("adopt", (overlap, placement, shape)). A part that is the same under both (the same twins) is not
    a conflict. Last, near twins (``_near_conflict``): an alternative that overlaps within NEAR_IOU of
    identity (or better) and recognises, by near twins, an old face that identity does not is also
    ambiguous; this is the part turned that also gained or lost a small hole or boss, whose exact twins
    are gone. Otherwise None. A part symmetric under a proper turn is decided before this, by
    ``_symmetry_verdict``.

    With ``probe`` every reference to the part is planned under each reading and compared by
    ``_readings_differ``, the comparison ``_symmetry_verdict`` uses. A reading that explains the same
    faces as one kept (the same twins; a new part that is symmetric under the turn) is dropped only when
    no reference that both match lands on different new elements; an alternative that no reference
    tells from identity is no conflict.
    If no reference goes to a different element under any reading, the alignment does not matter for any
    joint: identity stands (None), or for several turned alignments that tie, the first. A single
    reference that lands on different elements keeps the verdict ambiguous. A reading is never preferred
    on a tie."""
    comp = [c for c in proper if c[0] >= iou_id - IOU_SAME]
    mo = _Model(old_shape)
    twins_id = _face_twins(mo, new_shape)
    amb, adopt, rest = [], [], []
    seen = []
    for c in comp:
        tw = _face_twins(mo, c[2])
        if not tw:
            continue
        if tw == twins_id or tw in seen:
            rest.append((c, tw))
            continue
        seen.append(tw)
        (adopt if not twins_id else amb).append((c, tw))
    if twins_id and probe is not None:
        ref_id = probe(new_shape, App.Placement())
        amb = [(c, tw) for c, tw in amb if _readings_differ(ref_id, probe(c[2], c[1]), tol=NEAR_POS * mo.diag)] + [
            (c, tw) for c, tw in rest if _readings_differ(ref_id, probe(c[2], c[1]), both=True)]
    if amb:
        alts = [_plc_json(c[1]) for c, _tw in amb]
        return "ambiguous", ("the new part overlaps the old one about as well as placed (%.4f) as turned (%.4f), "
                             "and the two alignments match different faces exactly (%d old faces explained as placed, "
                             "%d when turned): pass the alignment" % (iou_id, max(c[0] for c, _t in amb),
                                                                      len(twins_id), len(amb[0][1])), alts)
    if adopt:
        pool = sorted(adopt + (rest if probe is not None else []), key=lambda x: -x[0][0])
        tied = [x for x in pool if pool[0][0][0] - x[0][0] <= IOU_SAME]
        if len(tied) > 1 and not _readings_agree(probe, [(c[2], c[1]) for c, _tw in tied],
                                                           NEAR_POS * mo.diag):
            alts = [_plc_json(c[1]) for c, _tw in tied]
            return "ambiguous", ("several turned alignments explain faces exactly and overlap equally well: "
                                 "pass the alignment", alts)
        c = pool[0][0]
        return "adopt", (c[0], c[1], c[2])
    near = _near_conflict(mo, new_shape, [c for c in proper if c[0] >= iou_id - NEAR_IOU], probe)
    if near:
        alts = [_plc_json(c[1]) for c, _f in near]
        faces = max(len(f) for _c, f in near)
        why = ("turned it recognises %d old faces (within %d%% of the part's diagonal, same type, direction and "
               "size) that it does not as placed, so it may have been turned and changed" % (faces, int(NEAR_POS * 100))
               if faces else "turned it recognises the same old faces but sends a referenced element to another "
               "place of the new part, so it may have been turned and changed")
        return "ambiguous", ("the new part overlaps the old one about as well as placed (%.4f) as turned (%.4f), "
                             "and %s: pass the alignment" % (iou_id, max(c[0] for c, _f in near), why), alts)
    return None


def _symmetry_turns(so, sn, old_only=False):
    """The proper turns T (placements about the old part's centre of mass) under which the old solid
    ``so`` is its own image (overlap with itself turned at least IOU_EXACT) and the new solid ``sn`` is
    not (so the part turned by T is a different body from the part as placed), each with the new part
    turned: [(Placement, shape)].

    The turns tried are the 180 degree turns about the three principal axes of inertia; where two
    moments are equal, the turns by 90, 120, 180, 240 and 270 degrees about the distinct axis and the
    180 degree turns about axes in the plane of the equal moments (the world axes projected into it,
    and those turned by 45 degrees); where all three are equal, the quarter and half turns about the
    world axes. A candidate is kept only if the overlap test says the old part really has it. With
    ``old_only`` the new part is not looked at: the turns of the old part alone, [(Placement, None)]."""
    pp = so.PrincipalProperties
    com = so.CenterOfMass
    mom = pp["Moments"]
    ax = [_unit(pp["FirstAxisOfInertia"]), _unit(pp["SecondAxisOfInertia"]), _unit(pp["ThirdAxisOfInertia"])]
    eq = [(i, j) for i, j in ((0, 1), (0, 2), (1, 2))
          if abs(mom[i] - mom[j]) <= 1e-5 * max(abs(mom[i]), abs(mom[j]), 1e-300)]
    world = [App.Vector(1, 0, 0), App.Vector(0, 1, 0), App.Vector(0, 0, 1)]
    cands = []
    if not eq:
        cands = [(a, 180.0) for a in ax]
    elif len(eq) == 1:
        a = ax[({0, 1, 2} - set(eq[0])).pop()]
        cands = [(a, ang) for ang in (90.0, 120.0, 180.0, 240.0, 270.0)]
        for w in world:
            d = w - a * w.dot(a)
            if d.Length > 1e-6:
                d = _unit(d)
                cands.append((d, 180.0))
                cands.append((App.Rotation(a, 45.0).multVec(d), 180.0))
    else:
        cands = [(w, ang) for w in world for ang in (90.0, 180.0, 270.0)]
    out = []
    for axis, ang in cands:
        plc = App.Placement(App.Vector(0, 0, 0), App.Rotation(axis, ang), com)
        if any(plc.isSame(o[0], 1e-6) for o in out):
            continue
        so_t = so.copy()
        so_t.transformShape(plc.toMatrix())
        if _iou(so, so_t) < IOU_EXACT:
            continue
        if old_only:
            out.append((plc, None))
            continue
        # the new part turned in place (about its own centre of mass): if that is the same body, the part
        # as placed and the part turned are one reading (a resize that keeps the symmetry, from any corner)
        in_place = sn.copy()
        in_place.transformShape(App.Placement(App.Vector(0, 0, 0), plc.Rotation, sn.CenterOfMass).toMatrix())
        if _iou(sn, in_place) >= IOU_EXACT:
            continue
        sn_t = sn.copy()
        sn_t.transformShape(plc.toMatrix())
        out.append((plc, sn_t))
    return out


SAME_POS_N = 1e-3   # bounding-box-relative position of interchangeable faces under a resize (fraction of the extent)


def _same_topology(old_shape, new_shape):
    """Is the new part the old one resized: the same faces, edges and vertices in the same adjacency?

    Equal face, edge and vertex counts, and every old face has a twin of the same surface type, concavity
    and normal or axis (as placed, no turn tried) with the same set of neighbouring faces, matched one to
    one (position and size, radius included, may differ; faces that look alike, the holes of a plate, must
    also keep their place in the bounding box to within SAME_POS_N). Features added or removed change the counts or
    the adjacency, so they never pass; a bar lengthened, a cube made a plate, a plate scaled all do."""
    mo, mn = _Model(old_shape), _Model(new_shape)
    if any(len(mo.order[k]) != len(mn.order[k]) for k in ("Face", "Edge", "Vertex")):
        return False

    def key(m, n):
        return (m._desc(n), m.els[n].concave is True, m.els[n].adj)
    groups = {}
    for n in mo.order["Face"]:
        groups.setdefault(key(mo, n), []).append(mo.els[n])
    new_groups = {}
    for n in mn.order["Face"]:
        new_groups.setdefault(key(mn, n), []).append(mn.els[n])
    if {k: len(v) for k, v in groups.items()} != {k: len(v) for k, v in new_groups.items()}:
        return False
    # faces that cannot be told apart by type and direction (the two holes of a plate) are paired by where they
    # sit in the part's bounding box, which a resize keeps and a feature moved against its twin does not
    for k, olds in groups.items():
        if len(olds) > 1:
            for o in olds:
                if min((o.pos_n - n.pos_n).Length for n in new_groups[k]) > SAME_POS_N:
                    return False
    return True


def _resize_stands(so, sn):
    """The old part is symmetric under a proper turn and the new part is an ordinary resize of it.

    An ordinary resize is "as placed": the new part keeps the old part's topology (``_same_topology``) and
    stays where the old one was (the bounding boxes overlap; a copy moved elsewhere or turned onto another
    axis away from the old place is not a resize). Then neither the symmetry rule nor the near twin rule
    has anything to ask: identity stands."""
    if not so.BoundBox.intersect(sn.BoundBox):
        return False
    if not _symmetry_turns(so, sn, old_only=True):
        return False
    return _same_topology(so, sn)


def _readings_differ(first, second, both=False, tol=None):
    """The references that two readings of the alignment send to different places.

    ``first`` and ``second`` are probe results ({(reference key, position): (status, new element,
    its centre in the old part's frame)}), one per reading. A reference differs when either reading
    matches it and the statuses or the new elements are not the same, or, with ``tol`` (mm), the two
    elements' centres are further apart than that. Returns [(key, element under first, element under second)]. Empty means
    the alignment does not matter for any joint: whichever reading was meant, every reference lands
    on the same new element (or neither reading matches it). With ``both`` only a reference that
    both readings match, to different elements, counts: a reading that finds no match for a reference
    that the other matches explains the part worse, which is no reason to doubt the other."""
    moved = []
    for k in sorted(first):
        (s0, n0, p0), (s1, n1, p1) = first[k], second[k]
        # the same element name is not the same place: an adapted match (a hole moved) can put one new
        # face at two places of the old part's frame
        far = tol is not None and p0 is not None and p1 is not None and (p0 - p1).Length > tol
        if (s0 == "matched" and s1 == "matched" and (n0 != n1 or far)) if both else (
                (s0 == "matched" or s1 == "matched") and (s0 != s1 or n0 != n1 or far)):
            moved.append((k, n0, n1))
    return moved


def _symmetry_verdict(so, sn, new_shape, iou_id, probe):
    """The symmetry rule: None when it does not apply, ("stand",) or ("ambiguous", note, alternatives).

    If the old part is symmetric under a proper turn T and the new part is not geometrically the old
    one, geometry alone cannot say whether the new part was placed as it is or turned by T: the old
    part cannot tell the two apart, so any difference between them (a hole added off centre, a face
    moved) reads either way. Every reference to the part is therefore planned under identity and under
    T applied to the alignment. If the two readings send a reference to different new elements, or one
    matches it and the other does not, the reference is ambiguous and so is the part's alignment, with
    "as placed" and the turned reading as candidates. If every reference goes to the same element under
    both (for instance a face the turn maps onto itself) the part stays matched as placed. This
    includes the doubly symmetric plate that gains an off-centre hole without being turned: nothing in
    the geometry says it was not, unless every referenced element is invariant. A turn under which the
    new part is symmetric too is left out (both readings give the same body), which keeps a resize that
    keeps the symmetry matched. A new part with the old part's topology (``_same_topology``: an ordinary
    resize) is no change of the kind the rule is about: it stands as placed."""
    if probe is None:
        return None
    if _resize_stands(so, sn):
        return ("stand",)
    turns = _symmetry_turns(so, sn)
    if not turns:
        return None
    as_placed = probe(new_shape, App.Placement())
    diff = []
    for plc, shp in turns:
        other = probe(shp, plc)
        moved = _readings_differ(as_placed, other, tol=NEAR_POS * so.BoundBox.DiagonalLength)
        if moved:
            diff.append((plc, moved))
    if not diff:
        return ("stand",)
    alts = [_plc_json(plc) for plc, _m in diff]
    (key, _pos), a, b = diff[0][1][0]
    return "ambiguous", ("the old part is symmetric under a turn of %s, so the new part, which is changed, may "
                         "have been placed as it is or turned: the reference %s goes to %s as placed and to %s "
                         "when turned (%d referenced elements differ): pass the alignment" % (
                             _turn_label(_plc_json(diff[0][0])), key, a, b, max(len(m) for _p, m in diff)), alts)


def _choose_alignment(old_shape, new_shape, forced=None, probe=None):
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
        if _resize_stands(so, sn):
            info["note"] = "same topology (a resize), identity kept"
            return App.Placement(), info, new_shape.copy()
        if iou_id >= IOU_FIT:
            sv = _symmetry_verdict(so, sn, new_shape, iou_id, probe)
            if sv is not None and sv[0] == "ambiguous":
                info.update(method="ambiguous", overlap=iou_id, note=sv[1][0], alternatives=sv[1][1])
                return None, info, None
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
    # the new part's own axes of inertia move when a big feature is added, so the turn it was given may not be among
    # the candidates above; also try the half turns about the old part's axes, the turn in place
    # (a bracket turned and then given a boss), about the centre of mass and about the centre of the bounding box
    # (a feature on one side moves the first, not the second), with the inertia axes and the world axes
    bb_o = so.BoundBox
    pivots = (so.CenterOfMass, App.Vector(bb_o.Center))
    axes_o = list(fo[1]) + [App.Vector(1, 0, 0), App.Vector(0, 1, 0), App.Vector(0, 0, 1)]
    diag_o = so.BoundBox.DiagonalLength
    for axis in (axes_o if IN_PLACE_TURNS and iou_id >= IN_PLACE_MIN else []):
        for pivot in pivots:
            t = App.Placement(App.Vector(), App.Rotation(axis, 180.0), pivot).toMatrix()
            if any(App.Placement(t).isSame(c[1], NEAR_POS * diag_o) for c in proper):
                continue
            shp = sn.copy()
            shp.transformShape(t)
            iou_t = _iou(so, shp)
            # only a turn that explains the new part as well as identity does is another reading of it, and not one
            # that is the same reading as a candidate already there (the same overlap, about the same turn)
            if iou_t >= iou_id - IN_PLACE_IOU and not any(
                    abs(c[0] - iou_t) < 1e-4 and App.Placement(t).isSame(c[1], 0.1 * diag_o) for c in proper):
                proper.append((iou_t, App.Placement(t), shp))
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
        sv = _symmetry_verdict(so, sn, new_shape, iou_id, probe)
        if sv is not None:
            if sv[0] == "stand":
                return App.Placement(), info, new_shape.copy()
            info.update(method="ambiguous", overlap=iou_id, note=sv[1][0], alternatives=sv[1][1])
            return None, info, None
        verdict = _alignment_conflict(old_shape, new_shape, iou_id, proper, probe)
        if verdict is None:
            return App.Placement(), info, new_shape.copy()
        kind, payload = verdict
        if kind == "adopt":
            info.update(method="inertia", overlap=payload[0],
                        note="identity explains none of the old faces by an exact twin, the turned part explains some")
            return payload[1], info, payload[2]
        info.update(method="ambiguous", overlap=iou_id, note=payload[0], alternatives=payload[1])
        return None, info, None
    close = [c for c in proper if c[0] >= best[0] - IOU_SAME]
    if len(close) > 1:
        info.update(method="ambiguous", overlap=best[0], alternatives=[_plc_json(c[1]) for c in close],
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


def _plc_from_json(d):
    return App.Placement(App.Vector(*d["base"]), App.Rotation(*d["rotation"]))


def _turn_label(d):
    """A short human label for a placement taking the new part's frame to the old part's.

    The rotation of an inertia alignment is not exact when the part changed, so an axis within 3
    degrees of X, Y or Z is named by it and the angle is rounded to whole degrees."""
    plc = _plc_from_json(d)
    if plc.isSame(App.Placement(), 1e-9):
        return "as placed"
    axis, ang = plc.Rotation.Axis, math.degrees(plc.Rotation.Angle)
    if ang < 1e-6:
        return "shifted by %.3g mm" % plc.Base.Length
    for name, v in (("X", App.Vector(1, 0, 0)), ("Y", App.Vector(0, 1, 0)), ("Z", App.Vector(0, 0, 1))):
        if abs(axis.dot(v)) > math.cos(math.radians(3.0)):
            sign = 1 if axis.dot(v) > 0 or abs(ang - 180.0) < 3.0 else -1
            return "turned %.0f\u00b0 about %s%s" % (ang, "" if sign > 0 else "-", name)
    return "turned %.0f\u00b0 about (%.2f, %.2f, %.2f)" % (ang, axis.x, axis.y, axis.z)


def _alignment_candidates(assembly, old, new, ainfo, choices):
    """The candidate alignments of a part whose alignment is ambiguous, each with the counts it gives.

    "As placed" (identity) comes first, then the alternatives the check found. The counts come from
    re-planning with the candidate placement as ``alignment=`` (after a round trip through its JSON
    form), so passing that placement back to plan or apply gives exactly these counts."""
    entries = [("as placed", _plc_json(App.Placement()))]
    for d in ainfo.get("alternatives", []):
        if not any(d == e[1] for e in entries):
            entries.append((_turn_label(d), d))
    out = []
    for label, d in entries:
        try:
            rep = _plan(assembly, old, new, choices, _plc_from_json(d))[0]
            counts, err = rep["references"], ""
        except ReplaceError as e:
            counts, err = None, str(e)
        out.append({"label": label, "placement": d, "counts": counts, "error": err})
    return out


def _reading_differences(new_shape, ainfo, probe, diag):
    """The referenced elements that the readings of an ambiguous alignment send to different places.

    Compares "as placed" with each alternative reading by element and by place (the comparison the symmetry
    rule uses), and returns [{"reference": "Joint.Reference1", "index": k, "as_placed": element, "alternative":
    element}], one per differing reference element and alternative. A reference not listed lands on the same
    element, at the same place, under every reading."""
    base = probe(new_shape, App.Placement())
    out = []
    for d in ainfo.get("alternatives", []):
        plc_ = _plc_from_json(d)
        shp = new_shape.copy()
        shp.transformShape(plc_.toMatrix())
        for (key, k), a, b in _readings_differ(base, probe(shp, plc_), tol=NEAR_POS * diag):
            out.append({"reference": key, "index": k, "as_placed": a, "alternative": b})
    return out


def _plan(assembly, old, new, choices=None, alignment=None):
    hosts = _find_hosts(assembly, old)
    new_leaf, new_tokens, new_top = _resolve_new(new)
    old_leaf = hosts[0].leaf
    old_shape, new_shape = _local_shape(old_leaf), _local_shape(new_leaf)
    if old_shape.isNull() or new_shape.isNull():
        raise ReplaceError("the old or the new part has no shape (recompute the document first)")
    refs = _collect_refs(assembly, hosts)
    mo = _Model(old_shape)

    def probe(shape, plc_):
        """{(reference key, position): (status, new element)} of every referenced element if ``shape``
        (the new part placed by ``plc_``) were the aligned new part."""
        mn_, cache_, out = _Model(shape), {}, {}
        for rf_ in refs:
            for k, e in enumerate(_plan_ref(rf_, mo, mn_, cache_, None, None)):
                g = e["new_global"]
                out[(rf_.key, k)] = (e["status"], g, mn_.els[g].center if g in mn_.els else None)
        return out

    plc, ainfo, aligned = _choose_alignment(old_shape, new_shape, alignment, probe if refs else None)
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
    mn = _Model(aligned) if aligned is not None else None
    differ = _reading_differences(new_shape, ainfo, probe, mo.diag) if refs and plc is None else []
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
                                 "cost": c["cost"], "basis": s["basis"]} for c in s["candidates"]]}
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
                      "note": ainfo.get("note", ""),
                      "alternatives": ainfo.get("alternatives", []),
                      "readings_differ": differ,
                      "candidates": _alignment_candidates(assembly, old, new, ainfo, choices)
                      if ainfo["method"] == "ambiguous" else []},
        "tolerances": {"position_mm": POS_TOL, "size_rel": SIZE_TOL, "angle_exact_rad": ANG_EXACT,
                       "angle_adapted_rad": ANG_ADAPT, "radius_change_max": RADIUS_ADAPT,
                       "size_ratio_max": SIZE_RATIO_MAX, "neighbours_min": ADJ_MIN,
                       "move_tol_diag": MOVE_TOL, "resize_tol": RESIZE_TOL, "clear_rel": CLEAR_REL,
                       "ambiguity_abs": AMBIG_ABS, "ambiguity_rel": AMBIG_REL,
                       "alignment_margin": IOU_MARGIN},
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


BROKEN_PROP = "ReplaceBroken"
WAS_SUPPRESSED_PROP = "ReplaceWasSuppressed"


def is_broken(joint):
    """True if a replace left ``joint`` with a reference it could not remap and nobody has repaired it."""
    return bool(getattr(joint, BROKEN_PROP, ""))


def _break_joint(doc, joint, reason=""):
    """A joint with a reference that could not be matched: kept, suppressed, its references marked with a
    leading "?" and a persistent, visible ``ReplaceBroken`` string property that says why. The property
    stays (empty) after a repair; ``is_broken`` reads it."""
    if not hasattr(joint, BROKEN_PROP):
        joint.addProperty("App::PropertyString", BROKEN_PROP, "Replace",
                          "Set by Replace component when a reference could not be remapped; "
                          "empty when the joint is sound. Repair it with ReplaceComponent.repair.")
    if not hasattr(joint, WAS_SUPPRESSED_PROP):
        joint.addProperty("App::PropertyBool", WAS_SUPPRESSED_PROP, "Replace",
                          "Whether the joint was suppressed by the user before Replace component broke it; "
                          "ReplaceComponent.repair restores that state.")
    if not is_broken(joint):
        setattr(joint, WAS_SUPPRESSED_PROP, bool(getattr(joint, "Suppressed", False)))
    setattr(joint, BROKEN_PROP, reason or "a reference could not be remapped")
    if hasattr(joint, "Suppressed"):
        joint.Suppressed = True


def _solve(assembly):
    try:
        return assembly.solve()
    except Exception as exc:
        return "error: %s" % exc


def _why_broken(rf, old, new):
    """One line for the ReplaceBroken property: the reference, the old elements, what the matcher said."""
    parts = []
    for r in rf.result:
        if r["status"] == "matched":
            continue
        cand = ", ".join(c["element"] for c in r["candidates"])
        parts.append("%s %s: %s%s%s" % (rf.prop, r["old_element"] or r["sub"], r["status"],
                                        (" (" + r["reason"] + ")") if r["reason"] else "",
                                        (" candidates: " + cand) if cand else ""))
    return "replace %s -> %s: %s" % (old.Label, new.Label, "; ".join(parts))


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


def _forget_copy_marks(src_doc, had_uuid):
    """Take back the hidden ``_ObjectUUID`` that ``Document.copyObject`` writes onto the objects it copies from.

    FreeCAD marks the source objects of any copy with a UUID so that the copy can say where it came from; the
    property is saved with the source document, so copying out of a document the user has open would change what
    their next save writes. A part taken from another document is to be used as it is: remove the mark from the
    objects that did not carry one before (removing it touches nothing and leaves no undo step)."""
    for o in src_doc.Objects:
        if o.Name not in had_uuid and "_ObjectUUID" in o.PropertiesList:
            try:
                o.removeProperty("_ObjectUUID")
            except Exception:
                pass


def _tree_path(assembly, obj):
    """The instance path of ``obj`` in ``assembly`` ("Sub.Old"): object names from the top group down."""
    names = [obj.Name]
    cur = obj.getParentGeoFeatureGroup()
    while cur is not None and cur != assembly:
        names.insert(0, cur.Name)
        cur = cur.getParentGeoFeatureGroup()
    return ".".join(names)


def apply(assembly, old, new, choices=None, delete_old=False, alignment=None, import_foreign=False):
    """Replace ``old`` by ``new`` in one undo transaction and return the report.

    Joints whose references could not all be matched are kept, suppressed and left with a
    ``?``-marked reference (FreeCAD's broken-link marker, a leading "?" on the sub-name); they are
    never solved against a guessed element. The old part's document object is kept unless ``delete_old``.

    ``new`` may belong to another document (a vault revision, a file). By default the links then point into that
    document (a cross-document link, the slice 2a behaviour). With ``import_foreign=True`` the part is instead copied
    with what it depends on into the assembly's document inside the same transaction (hidden, listed in the report's
    ``imported``), planned again, and the copy must give the same remap as the original or ``ReplaceError`` is raised
    and nothing changes; the panel uses this so that a temporary document can be closed afterwards.
    """
    doc = assembly.Document
    report, ctx = _plan(assembly, old, new, choices=choices, alignment=alignment)
    foreign = import_foreign and new.Document is not doc
    doc.openTransaction("Replace component")
    deleted, kept = [], []
    try:
        imported = []
        if foreign:
            # a part of another document (a file, a vault revision) is copied into this one inside the same
            # transaction, so one undo takes the copy and the replacement away together; the copy is then
            # planned again and must read exactly as the original did
            src_doc = new.Document
            had_uuid = {o.Name for o in src_doc.Objects if "_ObjectUUID" in o.PropertiesList}
            try:
                copied = doc.copyObject(ctx["new_top"], True, True)
            finally:
                _forget_copy_marks(src_doc, had_uuid)
            imported = list(copied) if isinstance(copied, (list, tuple)) else [copied]
            roots = [o for o in imported if not any(u in imported for u in o.InList)]   # nothing copied depends on the copy of the part itself
            if len(roots) != 1:
                raise ReplaceError("could not tell which copied object is the new part")
            top_copy = roots[0]
            for o in imported:
                o.Visibility = False
            doc.recompute()
            again, ctx2 = _plan(assembly, old, top_copy, choices=choices, alignment=alignment)
            if _readout(again) != _readout(report):
                raise ReplaceError("the copy of the new part in this document does not read as the original did; "
                                   "nothing was changed")
            report, ctx = again, ctx2
            report["imported"] = [o.Name for o in imported]
        refs, hosts, plc = ctx["refs"], ctx["hosts"], ctx["plc"]
        new_comp = {}
        paths = {h.obj.Name: _tree_path(assembly, h.obj) for h in hosts if h.mode == "adopt"}
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
        broken = {}
        for rf in refs:
            if not _apply_ref(rf, new_comp, ctx["new_tokens"]):
                broken.setdefault(rf.joint.Name, []).append(_why_broken(rf, old, new))
        for name in sorted(broken):
            _break_joint(doc, doc.getObject(name), "; ".join(broken[name]))
        for g, h in _collect_grounded(assembly, hosts):
            if h.obj.Name in new_comp:
                g.ObjectToGround = new_comp[h.obj.Name]
        for h in hosts:
            if h.mode == "adopt":
                parent = h.obj.getParentGeoFeatureGroup()
                if parent is not None:
                    parent.removeObject(h.obj)
                h.obj.Visibility = False
        # objects of the assembly that keep component selectors (assembly features) retarget them
        retargeted = []
        replaced = {h.obj.Name: (h.obj, new_comp[h.obj.Name], paths[h.obj.Name],
                                 (paths[h.obj.Name].rsplit(".", 1)[0] + "." if "." in paths[h.obj.Name] else "")
                                 + new_comp[h.obj.Name].Name)
                    for h in hosts if h.obj.Name in new_comp}
        if replaced:
            for member in list(assembly.Group):
                hook = getattr(getattr(member, "Proxy", None), "onComponentReplaced", None)
                if hook is not None:
                    retargeted.extend(hook(member, replaced))
        doc.recompute()
        solve_code = _solve(assembly)
        if delete_old:
            deleted, kept = _delete_old(doc, hosts)
        doc.recompute()
        for member in list(assembly.Group):
            hook = getattr(getattr(member, "Proxy", None), "scopeProblems", None)
            if hook is not None:
                retargeted.extend(hook(member))
    except Exception:
        doc.abortTransaction()
        raise
    doc.commitTransaction()
    report["applied"] = True
    report["solve"] = {"code": solve_code}
    report["deleted_old"] = deleted
    report["kept_old"] = kept
    report["new_components"] = {k: v.Name for k, v in new_comp.items()}
    report["feature_updates"] = retargeted
    return _json_safe(report)


def _readout(report):
    """What a remap decided, without the names of the new part: for comparing two plans of the same part."""
    return (report["alignment"]["method"], report["references"],
            [(j["joint"], r["reference"], [(e["old_element"], e["status"], e["new_element"]) for e in r["elements"]])
             for j in report["joints"] for r in j["references"]])


def _deletion_set(obj):
    """``obj`` and what only it owns: a PartDesign body takes its features and origin with it."""
    members = [obj]
    if obj.TypeId == "PartDesign::Body" or (hasattr(obj, "Tip") and hasattr(obj, "Group")):
        stack = list(obj.Group)
        while stack:
            m = stack.pop(0)
            if m not in members:
                members.append(m)
                stack.extend(getattr(m, "Group", []))
        origin = getattr(obj, "Origin", None)
        if origin is not None:
            for m in [origin] + list(origin.OutList):
                if m not in members:
                    members.append(m)
    return members


def _outside_users(members):
    """Objects, in any open document, that use one of ``members`` and are not among them: links and
    other references (``InList`` and a scan of every open document, since an external link does not
    always show in ``InList``) and expressions that name the object."""
    inside = {(m.Document.Name, m.Name) for m in members}
    users = {}
    docs = list(App.listDocuments().values())
    for m in members:
        for o in m.InList:
            users[(o.Document.Name, o.Name)] = o
        pats = [re.compile(r"(?<![\w])%s(?![\w])" % re.escape(x)) for x in {m.Name, m.Label}]
        for d in docs:
            for o in d.Objects:
                if o in members:
                    continue
                if m in o.OutList:
                    users[(d.Name, o.Name)] = o
                    continue
                for ent in getattr(o, "ExpressionEngine", []) or []:
                    if any(p.search(str(ent[1])) for p in pats):
                        users[(d.Name, o.Name)] = o
                        break
    return [o for k, o in sorted(users.items())
            if k not in inside and not o.isDerivedFrom("App::DocumentObjectGroup")]


def _remove_set(doc, members):
    """Remove ``members`` (the object first in the list) from the document, dependants first."""
    for m in reversed(members):
        if m.Document.getObject(m.Name) is not None:
            doc.removeObject(m.Name)


def _delete_old(doc, hosts):
    """Delete what the replaced instance left behind, but never an object anything else still uses.

    Returns (deleted names, kept: [{"name", "label", "reason", "users"}])."""
    deleted, kept = [], []
    targets = [h.obj for h in hosts if h.mode == "adopt"]
    if not targets:
        targets = [hosts[0].leaf]
    for obj in targets:
        members = _deletion_set(obj)
        users = _outside_users(members)
        if users:
            names = ["%s:%s" % (u.Document.Name, u.Name) for u in users]
            kept.append({"name": obj.Name, "label": obj.Label,
                         "reason": "still used by %s" % ", ".join(names), "users": names})
            continue
        names = [m.Name for m in members]
        _remove_set(doc, members)
        deleted.extend(names)
    return deleted, kept


# ------------------------------------------------------------------- repair

def _check_element(comp, subs, index, element):
    """Raise ReplaceError unless ``element`` exists on the part that sub-reference ``index`` reaches."""
    if not element:
        return
    path, _el = _split_sub(subs[index])
    m = _ELEMENT_RE.match(element)
    if not m:
        raise ReplaceError("%r is not a face, edge or vertex name" % element)
    try:
        if index == 1 and m.group(1) == "Edge" and _kind_of(_split_sub(subs[0])[1]) == "Face":
            face = Part.getShape(comp, path + _split_sub(subs[0])[1], needSubElement=True)
            ok = 1 <= int(m.group(2)) <= len(face.Edges)
        else:
            shp = Part.getShape(comp, path + element, needSubElement=True)
            ok = not shp.isNull()
    except Exception as exc:
        raise ReplaceError("%s is not an element of the part this reference reaches (%s)" % (element, exc))
    if not ok:
        raise ReplaceError("%s is not an element of the part this reference reaches" % element)


def repair(joint, reference, elements):
    """Set the chosen new element(s) of a joint reference a replace left broken, and clear the marker.

    ``reference`` is ``"Reference1"`` or ``"Reference2"``; ``elements`` is one element name for every
    sub-name of the reference (a string), or a list with one name per sub-name (a face and an edge:
    the edge is numbered within the face, as FreeCAD holds it). The names are checked against the part
    the reference reaches. When no reference of the joint is broken any more the ``ReplaceBroken`` text is
    cleared, the joint gets back the suppression state it had before the replace broke it (a joint the user had
    suppressed on purpose stays suppressed) and the assembly is solved; otherwise the text is rewritten to
    name what is left. One undo transaction. Returns ``{"joint", "reference", "subs", "still_broken", "solve"}``."""
    if reference not in _JOINT_PROPS:
        raise ValueError("reference must be Reference1 or Reference2, not %r" % (reference,))
    comp, subs = getattr(joint, reference)
    if comp is None or not any("?" in x for x in subs):
        raise ReplaceError("%s.%s is not a broken reference" % (joint.Name, reference))
    names = [elements] * len(subs) if isinstance(elements, str) else list(elements)
    if len(names) != len(subs):
        raise ValueError("%s.%s has %d sub-names, %d elements given" % (joint.Name, reference, len(subs), len(names)))
    new_subs = []
    for sub, el in zip(subs, names):
        path, old_el = _split_sub(sub.replace("?", ""))
        old_kind, new_kind = old_el.rstrip("0123456789"), el.rstrip("0123456789")
        if old_kind and old_kind != new_kind:
            raise ReplaceError(
                "%s.%s: %s replaces a %s; give an element of the same kind" % (joint.Name, reference, el, old_kind)
            )
        new_subs.append(path + el)
    for i, el in enumerate(names):
        _check_element(comp, new_subs, i, el)
    doc = joint.Document
    doc.openTransaction("Repair joint reference")
    try:
        _write_ref(joint, reference, comp, new_subs)
        left = [p for p in _JOINT_PROPS if getattr(joint, p)[0] is not None and any("?" in x for x in getattr(joint, p)[1])]
        solve_code = None
        if left:
            setattr(joint, BROKEN_PROP, "%s still broken (repaired %s)" % (", ".join(left), reference))
        else:
            setattr(joint, BROKEN_PROP, "")
            joint.Suppressed = bool(getattr(joint, WAS_SUPPRESSED_PROP, False))   # the user's own choice, not always False
            doc.recompute()
            asm = joint.Proxy.getAssembly(joint) if hasattr(joint.Proxy, "getAssembly") else None
            if asm is not None:
                solve_code = _solve(asm)
        doc.recompute()
    except Exception:
        doc.abortTransaction()
        raise
    doc.commitTransaction()
    return _json_safe({"joint": joint.Name, "reference": reference, "subs": list(getattr(joint, reference)[1]),
                       "still_broken": left, "solve": solve_code})
