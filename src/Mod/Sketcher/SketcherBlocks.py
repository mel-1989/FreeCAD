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
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *

"""Sketch blocks (G10 slice 5a): named definitions of sketch geometry and
instances of them that move as one rigid body inside the same sketch.

A definition keeps the coordinates of its members in its own frame (origin at
the first point of the first member, angle 0).  An instance is a copy of the
members at a placement (x, y, angle).  The solver ties every instance member
to three unknowns per instance (X, Y, angle), so an instance has exactly three
degrees of freedom, can neither reflect nor deform, and is nonredundant by
construction.  The data live in the string property ``BlockData`` of the
sketch as JSON, keyed by each geometry's persistent id (``getGeometryId``), not
by GeoId, so deleting geometry shifts nothing.  Behaviour follows public
descriptions of sketch blocks (a definition placed many times, edited once,
exploded back to plain geometry); no layout, text or file format comes from
another product.

    define(sk, name, geo_ids)            register a definition from geometry
    insert(sk, name, x, y, angle)        place an instance, returns its name
    explode(sk, instance)                turn an instance into plain geometry
    delete_instance(sk, instance)        remove an instance with its members and their constraints
    delete_definition(sk, name)          remove a definition that has no instance
    update_definition(sk, name, changes) edit a definition, every instance follows
    blocks(sk)                           what the sketch holds
    insert_many(sk, name, placements)    place many instances in one undo entry and one solve
    library_folder(folder=None)          the folder of the block library
    library_list(folder=None)            the blocks in the library, bad files marked with their reason
    library_save(sk, name, ...)          write a definition to the library
    library_load(sk, name, ...)          copy a definition from the library into the sketch
    library_delete(name, folder=None)    remove a block file from the library

The block library (slice 5b1) is a plain folder holding one JSON file per block, ``<name>.json``, in a format of our
own.  The file is data only and is never executed or evaluated::

    {"format": "cadfork-sketch-block", "version": 1, "name": "<name>", "units": "mm",
     "members": {"<key>": {"t": "p|l|c|a", "g": [numbers], "c": true|false}, ...}}

``members`` is the definition's ``BlockData`` entry as the sketch keeps it, in the block's own frame: ``t`` is the kind
(point, line, circle, arc), ``g`` its numbers (point x, y; line x1, y1, x2, y2; circle x, y, r; arc x, y, r and the
start and end angles in radians), ``c`` the construction flag.  Keys are sorted, numbers are written with the shortest
text that reads back to the same double, so a save, a load and a second save are byte-identical.  The default folder is
``SketchBlocks`` in the user's data directory; the string parameter ``LibraryFolder`` under
``User parameter:BaseApp/Preferences/Mod/Sketcher/Blocks`` replaces it, and every library call takes ``folder=``.
Loading copies the definition into the sketch: there is no link back to the file, so changing or deleting the file
later changes nothing in a sketch.  A file with a newer ``version`` than 1 is refused.
Supported members: points, line segments, circles, arcs of circle.  Anything
else, an empty or degenerate set, a duplicate name and the like raise
:class:`SketchBlockError` before anything is touched."""

import contextlib
import json
import math
import numbers
import os
import unicodedata

import FreeCAD
import Part
import Sketcher

V = FreeCAD.Vector
TWO_PI = 2.0 * math.pi
_EPS = 1e-6  # two points closer than this are not distinct
_PLACE_TOL = 1e-7  # an instance's placement must hold to this (mm)

_TYPES = {
    "Part::GeomPoint": "p",
    "Part::GeomLineSegment": "l",
    "Part::GeomCircle": "c",
    "Part::GeomArcOfCircle": "a",
}


class SketchBlockError(Exception):
    """A block operation was refused; the sketch was not changed."""


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------


def _load(sk):
    text = sk.BlockData
    if not text:
        return {"v": 1, "defs": {}, "inst": {}, "n": 0}
    try:
        data = json.loads(text)
        data["defs"], data["inst"]
    except Exception as exc:
        raise SketchBlockError("the sketch's block data cannot be read (%s)" % exc)
    data.setdefault("n", 0)
    return data


def _dump(data):
    """Deterministic: sorted keys, no spaces, so a second save is byte-identical."""
    if not data["defs"] and not data["inst"]:
        return ""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _id_map(sk):
    """persistent id -> current GeoId"""
    return {sk.getGeometryId(i): i for i in range(len(sk.Geometry))}


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------


def _numbers(geo):
    """(type letter, numbers in world or local coordinates) of a supported geometry."""
    t = _TYPES.get(geo.TypeId)
    if t is None:
        raise SketchBlockError("a %s cannot be part of a block (points, lines, circles and arcs can)" % geo.TypeId.split("::")[-1].replace("Geom", "").lower())
    if t == "p":
        return t, [geo.X, geo.Y]
    if t == "l":
        return t, [geo.StartPoint.x, geo.StartPoint.y, geo.EndPoint.x, geo.EndPoint.y]
    if t == "c":
        return t, [geo.Center.x, geo.Center.y, geo.Radius]
    a0, a1 = geo.FirstParameter, geo.LastParameter
    if a1 <= a0:
        a1 += TWO_PI
    return t, [geo.Center.x, geo.Center.y, geo.Radius, a0, a1]


def _points(t, g):
    """The member's points, for the distinctness test and the placement."""
    if t == "p":
        return [(g[0], g[1])]
    if t == "l":
        return [(g[0], g[1]), (g[2], g[3])]
    if t == "c":
        return [(g[0], g[1])]
    return [(g[0], g[1]), (g[0] + g[2] * math.cos(g[3]), g[1] + g[2] * math.sin(g[3]))]


def _rotate(x, y, c, s):
    return x * c - y * s, x * s + y * c


def _to_local(t, g, ox, oy):
    g = list(g)
    g[0] -= ox
    g[1] -= oy
    if t == "l":
        g[2] -= ox
        g[3] -= oy
    return g


def _to_world(t, g, X, Y, th):
    c, s = math.cos(th), math.sin(th)
    out = list(g)
    out[0], out[1] = _rotate(g[0], g[1], c, s)
    out[0] += X
    out[1] += Y
    if t == "l":
        out[2], out[3] = _rotate(g[2], g[3], c, s)
        out[2] += X
        out[3] += Y
    elif t == "a":
        out[3] = g[3] + th
        out[4] = g[4] + th
    return out


def _make(t, g):
    if t == "p":
        return Part.Point(V(g[0], g[1], 0))
    if t == "l":
        return Part.LineSegment(V(g[0], g[1], 0), V(g[2], g[3], 0))
    if t == "c":
        return Part.Circle(V(g[0], g[1], 0), V(0, 0, 1), g[2])
    return Part.ArcOfCircle(Part.Circle(V(g[0], g[1], 0), V(0, 0, 1), g[2]), g[3], g[4])


def _text(value, what):
    """`value`, a string that comes from outside, can be stored in the block data.  A lone surrogate (``"\\ud800"``) can
    be written by Python's json but the sketch's reader refuses it and then ignores all block data, so the blocks would
    stop being rigid; refuse it where it enters."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise SketchBlockError("%s contains a character that cannot be stored (an unpaired surrogate)" % what)


def _real(v):
    """A placement number as a float; a bool, a string and anything that is not a real number is refused, and so is a
    number too large for a float.  Python ints, floats, numpy numbers and Fractions all pass."""
    if isinstance(v, bool) or not isinstance(v, numbers.Real):
        raise SketchBlockError("the placement must be numbers")
    try:
        return float(v)
    except (OverflowError, ValueError, TypeError):
        raise SketchBlockError("the placement must be finite numbers")


def _check_distinct(members):
    pts = []
    try:
        for m in members:
            pts += _points(m["t"], m["g"])
    except (IndexError, KeyError, TypeError, ValueError):
        raise SketchBlockError("a block definition is malformed: a member lacks its kind or its numbers")
    if not pts:
        raise SketchBlockError("a block needs at least one member")
    p0 = pts[0]
    if not any(math.hypot(p[0] - p0[0], p[1] - p0[1]) > _EPS for p in pts[1:]):
        raise SketchBlockError(
            "a block needs two distinct points; a lone point, a lone circle or concentric circles "
            "look the same in every rotation, so the instance's rotation could not be told"
        )


def _placement(defn, world_members):
    """(X, Y, angle) of an instance from the current coordinates of its members
    (a dict key -> numbers), taking the first point and the first point distinct from it."""
    pairs = []
    for key in sorted(defn["members"]):
        if key not in world_members:
            continue
        m = defn["members"][key]
        for lp, wp in zip(_points(m["t"], m["g"]), _points(m["t"], world_members[key])):
            pairs.append((lp, wp))
    l0, w0 = pairs[0]
    th = 0.0
    for lp, wp in pairs[1:]:
        if math.hypot(lp[0] - l0[0], lp[1] - l0[1]) > _EPS:
            th = math.atan2(wp[1] - w0[1], wp[0] - w0[0]) - math.atan2(lp[1] - l0[1], lp[0] - l0[0])
            break
    rx, ry = _rotate(l0[0], l0[1], math.cos(th), math.sin(th))
    return w0[0] - rx, w0[1] - ry, th


def _instance_world(sk, defn, inst, idmap=None):
    idmap = idmap or _id_map(sk)
    geo = sk.Geometry
    out = {}
    for key, gid in inst["m"].items():
        if gid not in idmap:
            raise SketchBlockError("block instance geometry is missing")
        out[key] = _numbers(geo[idmap[gid]])[1]
    return out


# ---------------------------------------------------------------------------
# transactions
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _transaction(sk, label):
    """One undo entry named `label`; an exception aborts it and the sketch is as it was."""
    doc = sk.Document
    nested = bool(getattr(doc, "HasPendingTransaction", False))
    snapshot = {p: sk.dumpPropertyContent(p) for p in ("Geometry", "Constraints", "BlockData")}
    if not nested:
        doc.openTransaction(label)
    # the data and the geometry change in several steps; the sketch must not judge them in between
    sk.setBlockGuard(True)
    try:
        yield
    except BaseException:
        sk.setBlockGuard(False)
        if not nested:
            doc.abortTransaction()
        # no undo recorded (UndoMode 0): put the properties back by hand
        for p, content in snapshot.items():
            if sk.dumpPropertyContent(p) != content:
                sk.restorePropertyContent(p, content)
        with contextlib.suppress(Exception):
            sk.solve()
        raise
    else:
        sk.setBlockGuard(False)
        if not nested:
            doc.commitTransaction()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def _name_ok(data, name):
    if not isinstance(name, str) or not name.strip():
        raise SketchBlockError("a block needs a name")
    _text(name, "the block's name")
    if name in data["defs"]:
        raise SketchBlockError("a block called '%s' already exists" % name)
    if name in data["inst"]:
        raise SketchBlockError("'%s' is the name of a block instance; a block needs a name of its own" % name)


def _members_in_instances(data, idmap_ids):
    used = set()
    for inst in data["inst"].values():
        used |= set(inst["m"].values())
    return used & set(idmap_ids)


def define(sk, name, geo_ids):
    """Register a definition from existing geometry (which stays as plain geometry).

    The definition's frame has its origin at the first point of the first member.
    Returns the name."""
    data = _load(sk)
    _name_ok(data, name)
    geo_ids = list(geo_ids)
    if not geo_ids:
        raise SketchBlockError("a block needs at least one member")
    if len(set(geo_ids)) != len(geo_ids):
        raise SketchBlockError("the same geometry is listed twice")
    n = len(sk.Geometry)
    for g in geo_ids:
        if not isinstance(g, int) or g < 0 or g >= n:
            raise SketchBlockError("geometry %r is not in the sketch (external geometry cannot be part of a block)" % (g,))
    ids = [sk.getGeometryId(g) for g in geo_ids]
    busy = set(ids) & {gid for inst in data["inst"].values() for gid in inst["m"].values()}
    if busy:
        raise SketchBlockError("geometry that already belongs to a block instance cannot be defined again")
    raw = []
    for g in geo_ids:
        t, nums = _numbers(sk.Geometry[g])
        raw.append((t, nums, bool(sk.getConstruction(g))))
    ox, oy = _points(raw[0][0], raw[0][1])[0]
    members = {}
    for i, (t, nums, constr) in enumerate(raw):
        members["m%04d" % i] = {"t": t, "g": _to_local(t, nums, ox, oy), "c": constr}
    _check_distinct(list(members.values()))
    data["defs"][name] = {"members": members}
    with _transaction(sk, "Define block"):
        sk.BlockData = _dump(data)
    return name


def _flagged(geo, flag):
    """`geo`, carrying the construction flag when `flag`.  The flag travels with the geometry into ``addGeometry``;
    ``setConstruction`` after the fact costs one solve per call."""
    if flag:
        ext = Sketcher.SketchGeometryExtension()
        ext.Construction = True
        geo.setExtension(ext)
    return geo


def _add_instance_geometry(sk, defn, X, Y, th, keys=None):
    """Add the members of `defn` at the placement; returns {key: persistent id}."""
    keys = sorted(defn["members"]) if keys is None else keys
    geos = []
    for key in keys:
        m = defn["members"][key]
        geos.append(_flagged(_make(m["t"], _to_world(m["t"], m["g"], X, Y, th)), bool(m.get("c"))))
    gids = sk.addGeometry(geos, False)
    if isinstance(gids, int):
        gids = [gids]
    return {key: sk.getGeometryId(gid) for key, gid in zip(keys, gids)}


def insert(sk, name, x=0.0, y=0.0, angle=0.0):
    """Place an instance of block `name` with its origin at (x, y), rotated by
    `angle` radians (any value, not wrapped).  Returns the instance's name.

    The origin is the frame's origin fixed when the block was defined (the first
    point of the first member then).  It does not follow member 0 if
    :func:`update_definition` later moves that member."""
    data = _load(sk)
    if name not in data["defs"]:
        raise SketchBlockError("there is no block called '%s'" % name)
    x, y, angle = _real(x), _real(y), _real(angle)
    if not all(map(math.isfinite, (x, y, angle))):
        raise SketchBlockError("the placement must be finite numbers")
    defn = data["defs"][name]
    try:
        _check_distinct(list(defn["members"].values()))
    except SketchBlockError:
        raise SketchBlockError("block '%s' has a degenerate definition and cannot be placed" % name)
    data["n"] += 1
    while "%s_%d" % (name, data["n"]) in data["inst"] or "%s_%d" % (name, data["n"]) in data["defs"]:
        data["n"] += 1  # a name already taken by an instance or by a block of that name is skipped
    iname = "%s_%d" % (name, data["n"])
    with _transaction(sk, "Insert block"):
        try:
            ids = _add_instance_geometry(sk, defn, x, y, angle)
        except SketchBlockError:
            raise
        except Exception as exc:
            raise SketchBlockError("block '%s' cannot be placed (%s)" % (name, exc))
        data["inst"][iname] = {"def": name, "m": ids}
        sk.BlockData = _dump(data)
        sk.solve()
    return iname


def insert_many(sk, name, placements):
    """Place an instance of block `name` at each of `placements` (a list of (x, y, angle), angle in radians) in one
    undo entry and one solve.  The result is what calling :func:`insert` once per placement, in order, gives: the same
    instance names, the same geometry order, the same coordinates.  Every placement is checked before anything changes;
    one bad placement refuses the whole call.  An empty list changes nothing and makes no undo entry.  Returns the
    instance names in order."""
    data = _load(sk)
    if name not in data["defs"]:
        raise SketchBlockError("there is no block called '%s'" % name)
    if isinstance(placements, (dict, set, frozenset, str, bytes)):
        raise SketchBlockError("the placements must be a list of (x, y, angle)")
    try:
        placements = list(placements)
    except TypeError:
        raise SketchBlockError("the placements must be a list of (x, y, angle)")
    clean = []
    for i, p in enumerate(placements):
        try:
            if isinstance(p, (dict, set, frozenset, str, bytes)) or len(p) != 3:
                raise SketchBlockError("")
            x, y, a = (_real(v) for v in p)
        except (SketchBlockError, TypeError):
            raise SketchBlockError("placement %d must be three numbers (x, y, angle); nothing was changed" % (i + 1))
        if not all(map(math.isfinite, (x, y, a))):
            raise SketchBlockError("placement %d must be finite numbers; nothing was changed" % (i + 1))
        clean.append((x, y, a))
    if not clean:
        return []
    defn = data["defs"][name]
    try:
        _check_distinct(list(defn["members"].values()))
    except SketchBlockError:
        raise SketchBlockError("block '%s' has a degenerate definition and cannot be placed" % name)
    names = []
    for _ in clean:
        data["n"] += 1
        while "%s_%d" % (name, data["n"]) in data["inst"] or "%s_%d" % (name, data["n"]) in data["defs"]:
            data["n"] += 1
        names.append("%s_%d" % (name, data["n"]))
        data["inst"][names[-1]] = None  # reserved, so the next name skips it
    keys = sorted(defn["members"])
    with _transaction(sk, "Insert blocks"):
        try:
            geos = []
            for x, y, a in clean:
                for key in keys:
                    m = defn["members"][key]
                    geos.append(_flagged(_make(m["t"], _to_world(m["t"], m["g"], x, y, a)), bool(m.get("c"))))
            gids = sk.addGeometry(geos, False)
            if isinstance(gids, int):
                gids = [gids]
            persistent = [sk.getGeometryId(g) for g in gids]
        except SketchBlockError:
            raise
        except Exception as exc:
            raise SketchBlockError("block '%s' cannot be placed (%s)" % (name, exc))
        for i, iname in enumerate(names):
            data["inst"][iname] = {"def": name, "m": dict(zip(keys, persistent[i * len(keys) : (i + 1) * len(keys)]))}
        sk.BlockData = _dump(data)
        sk.solve()
    return names


def explode(sk, instance, label="Explode block"):
    """Turn an instance into plain geometry: same coordinates, same external
    constraints, its three placement freedoms become the members' own.  `label`
    names the undo entry."""
    data = _load(sk)
    if instance not in data["inst"]:
        raise SketchBlockError("there is no block instance called '%s'" % instance)
    del data["inst"][instance]
    with _transaction(sk, label):
        sk.BlockData = _dump(data)
        sk.solve()


def delete_instance(sk, instance):
    """Remove an instance: exactly its members and the constraints on them, in one
    undo entry.  Other instances and plain geometry keep their coordinates.  Returns
    the names of the constraints removed (a constraint without a name is
    ``Constraint<number>`` by its number before the deletion)."""
    data = _load(sk)
    if instance not in data["inst"]:
        raise SketchBlockError("there is no block instance called '%s'" % instance)
    idmap = _id_map(sk)
    gone = sorted({idmap[gid] for gid in data["inst"][instance]["m"].values() if gid in idmap})
    cons = list(sk.Constraints)
    drop = [i for i, c in enumerate(cons) if {c.First, c.Second, c.Third} & set(gone)]
    names = [_constraint_label(cons[i], i) for i in drop]
    del data["inst"][instance]
    with _transaction(sk, "Delete block instance"):
        for i in reversed(drop):
            sk.delConstraint(i)
        # the instance is out of the data first, so deleting its members explodes nothing else
        sk.BlockData = _dump(data)
        if gone:
            sk.delGeometries(gone)
        sk.solve()
    return names


def delete_definition(sk, name):
    """Remove a definition.  Refused while any instance of it exists; the geometry
    of explicit members is not touched (a definition holds only numbers)."""
    data = _load(sk)
    if name not in data["defs"]:
        raise SketchBlockError("there is no block called '%s'" % name)
    n = sum(1 for i in data["inst"].values() if i["def"] == name)
    if n:
        raise SketchBlockError("block '%s' still has %d instance%s; delete or explode %s first" % (name, n, "" if n == 1 else "s", "it" if n == 1 else "them"))
    del data["defs"][name]
    with _transaction(sk, "Delete block definition"):
        sk.BlockData = _dump(data)


def blocks(sk):
    """What the sketch holds: {'definitions': {name: [member keys]},
    'instances': {name: {'definition': name, 'geo_ids': [GeoId per member]}}}."""
    data = _load(sk)
    idmap = _id_map(sk)
    try:
        return {
            "definitions": {n: sorted(d["members"]) for n, d in sorted(data["defs"].items())},
            "instances": {
                n: {
                    "definition": i["def"],
                    "geo_ids": [idmap.get(i["m"][k], -1) for k in sorted(i["m"])],
                }
                for n, i in sorted(data["inst"].items())
            },
        }
    except (TypeError, KeyError, IndexError, AttributeError) as exc:
        raise SketchBlockError("the sketch's block data is malformed (%s)" % exc)


def _constraint_label(c, index):
    return c.Name if c.Name else "Constraint%d" % (index + 1)


def update_definition(sk, name, changes):
    """Edit block `name` and make every instance follow, in one undo entry.

    `changes` maps a member key (see :func:`blocks`) to the member's new
    geometry in the block's own frame, a Part geometry of the same kind; a key
    not in the definition adds a member (a new key of your choosing); ``None``
    removes the member; a member may also be given as ``(geometry, construction)``
    to change the construction flag the definition carries.  Each instance keeps its placement.  Constraints on
    surviving members survive on the same element; constraints on removed
    members are removed and returned by name, never retargeted.  The construction
    flag is part of the definition: an instance member whose flag differs from
    the definition's is a misfit, and the instance explodes like any other.  Returns
    {'removed_constraints': [names], 'instances': [names]}."""
    data = _load(sk)
    if name not in data["defs"]:
        raise SketchBlockError("there is no block called '%s'" % name)
    if not isinstance(changes, dict) or not changes:
        raise SketchBlockError("nothing to change")
    defn = data["defs"][name]
    new_def = json.loads(json.dumps(defn))
    removed, added, moved = [], [], []
    flags = {}  # key -> construction flag, for a member given as (geometry, construction)
    for key, geo in changes.items():
        if isinstance(geo, tuple) and len(geo) == 2:
            geo, flags[key] = geo[0], bool(geo[1])
        if geo is not None and not hasattr(geo, "TypeId"):
            raise SketchBlockError("member '%s' must be a Part geometry or None" % (key,))
        if geo is None:
            if key not in defn["members"]:
                raise SketchBlockError("block '%s' has no member '%s'" % (name, key))
            removed.append(key)
            del new_def["members"][key]
            continue
        t, nums = _numbers(geo)
        if key in defn["members"]:
            if defn["members"][key]["t"] != t:
                raise SketchBlockError("member '%s' cannot change its kind" % key)
            new_def["members"][key]["g"] = nums
            if key in flags:
                new_def["members"][key]["c"] = flags[key]
            moved.append(key)
        else:
            new_def["members"][key] = {"t": t, "g": nums, "c": flags.get(key, False)}
            added.append(key)
    if not new_def["members"]:
        raise SketchBlockError("a block needs at least one member")
    _check_distinct(list(new_def["members"].values()))

    idmap = _id_map(sk)
    insts = {n: i for n, i in data["inst"].items() if i["def"] == name}
    placements = {}
    for n, inst in insts.items():
        if any(gid not in idmap for gid in inst["m"].values()):
            raise SketchBlockError("instance '%s' is missing geometry" % n)
        placements[n] = _placement(defn, _instance_world(sk, defn, inst, idmap))
    conflicts_before = list(sk.ConflictingConstraints)
    result = {"removed_constraints": [], "redundant_constraints": [], "instances": sorted(insts)}

    with _transaction(sk, "Edit block definition"):
        # 1. the constraints on removed members go, by name, before the geometry does
        gone = sorted({idmap[insts[n]["m"][k]] for n in insts for k in removed if k in insts[n]["m"]})
        cons = list(sk.Constraints)
        drop = [i for i, c in enumerate(cons) if {c.First, c.Second, c.Third} & set(gone)]
        result["removed_constraints"] = [_constraint_label(cons[i], i) for i in drop]
        for i in reversed(drop):
            sk.delConstraint(i)
        # 2. the new definition is in force before any geometry moves, so the solver does not pull
        #    a moved member back; removed members leave their instances (the geometry goes in step 5)
        data["defs"][name] = new_def
        for inst in insts.values():
            for k in removed:
                inst["m"].pop(k, None)
        sk.BlockData = _dump(data)
        # 3. moved members: the same geometry (so its id and constraints stay) with new numbers
        geo = sk.Geometry
        for n, inst in insts.items():
            X, Y, th = placements[n]
            for key in moved:
                m = new_def["members"][key]
                _reshape(geo[idmap[inst["m"][key]]], m["t"], _to_world(m["t"], m["g"], X, Y, th))
        if moved:
            sk.Geometry = geo
            for n, inst in insts.items():
                for key in moved:
                    if bool(defn["members"][key].get("c")) != bool(new_def["members"][key].get("c")):
                        sk.setConstruction(idmap[inst["m"][key]], bool(new_def["members"][key].get("c")))
        # 4. added members
        for n, inst in insts.items():
            X, Y, th = placements[n]
            inst["m"].update(_add_instance_geometry(sk, new_def, X, Y, th, keys=added))
        sk.BlockData = _dump(data)
        # 5. removed members' geometry
        if gone:
            sk.delGeometries(gone)
        status = sk.solve()
        # 6. the call fails, and with it the transaction, if the solver fails or the edit introduced a
        #    conflict; a redundancy that was already there stays and is reported
        introduced = set(sk.ConflictingConstraints) - set(conflicts_before)
        if status in (-1, -4, -5, -6) or introduced or (status == -3 and not conflicts_before):
            raise SketchBlockError("the edited block cannot be solved in the sketch; nothing was changed")
        result["redundant_constraints"] = [_constraint_label(c, i - 1) for i, c in ((i, sk.Constraints[i - 1]) for i in sk.RedundantConstraints)]
        idmap2 = _id_map(sk)
        for n, inst in insts.items():
            X, Y, th = placements[n]
            for k, gid in inst["m"].items():
                m = new_def["members"][k]
                want = _to_world(m["t"], m["g"], X, Y, th)
                have = _numbers(sk.Geometry[idmap2[gid]])[1]
                n_cmp = 3 if m["t"] == "a" else len(want)  # an arc's angles are compared mod a turn below
                bad = any(abs(a - b) > _PLACE_TOL for a, b in zip(want[:n_cmp], have[:n_cmp]))
                if m["t"] == "a":
                    bad = bad or abs(math.remainder(have[3] - want[3], TWO_PI)) > 1e-9
                    bad = bad or abs(math.remainder(have[4] - want[4], TWO_PI)) > 1e-9
                if bad:
                    raise SketchBlockError("an instance would move by more than %g mm; nothing was changed" % _PLACE_TOL)
    return result


def instance_placement(sk, instance):
    """(X, Y, angle) of an instance fitted to its members as they are now."""
    data = _load(sk)
    if instance not in data["inst"]:
        raise SketchBlockError("there is no block instance called '%s'" % instance)
    inst = data["inst"][instance]
    defn = data["defs"][inst["def"]]
    return _placement(defn, _instance_world(sk, defn, inst))


def to_local(t, g, X, Y, th):
    """The inverse of the placement (X, Y, th) on a member's numbers."""
    c, s = math.cos(-th), math.sin(-th)
    out = list(g)
    out[0], out[1] = _rotate(g[0] - X, g[1] - Y, c, s)
    if t == "l":
        out[2], out[3] = _rotate(g[2] - X, g[3] - Y, c, s)
    elif t == "a":
        out[3] = g[3] - th
        out[4] = g[4] - th
    return out


_SAME = 1e-9  # a member whose numbers moved by less than this has not changed


def _fit_points(t, g):
    """The member's points for the rigid fit: its defining points, an arc's far end too."""
    pts = _points(t, g)
    if t == "a":
        pts = pts + [(g[0] + g[2] * math.cos(g[4]), g[1] + g[2] * math.sin(g[4]))]
    return pts


def rigid_fit(defn, world, keys=None):
    """The one rigid placement (X, Y, angle) that takes every listed member of `defn` to the numbers in `world` (key ->
    numbers) within the fit tolerance, or None.  Needs at least one member and two distinct points among the fit points: a lone line or arc always has them,
    so a rigid move of it is a change of placement; a lone point or circle has none and gets None.  A lone line turned end
    for end is the rigid half turn about its midpoint: it is read as a new placement and the definition does not change."""
    keys = [k for k in sorted(world) if k in defn["members"]] if keys is None else keys
    if not keys:
        return None
    pairs, radii = [], []
    for k in keys:
        m = defn["members"][k]
        if m["t"] in "ca":
            radii.append((m["g"][2], world[k][2]))
        pairs += list(zip(_fit_points(m["t"], m["g"]), _fit_points(m["t"], world[k])))
    lp = [a for a, _ in pairs]
    if not any(math.hypot(p[0] - lp[0][0], p[1] - lp[0][1]) > _EPS for p in lp[1:]):
        return None
    n = float(len(pairs))
    lcx, lcy = sum(a[0] for a, _ in pairs) / n, sum(a[1] for a, _ in pairs) / n
    wcx, wcy = sum(b[0] for _, b in pairs) / n, sum(b[1] for _, b in pairs) / n
    dot = cross = 0.0
    for (ax, ay), (bx, by) in pairs:
        ax, ay, bx, by = ax - lcx, ay - lcy, bx - wcx, by - wcy
        dot += ax * bx + ay * by
        cross += ax * by - ay * bx
    th = math.atan2(cross, dot)
    c, s = math.cos(th), math.sin(th)
    X, Y = wcx - (lcx * c - lcy * s), wcy - (lcx * s + lcy * c)
    for (ax, ay), (bx, by) in pairs:
        if math.hypot(ax * c - ay * s + X - bx, ax * s + ay * c + Y - by) > _PLACE_TOL:
            return None
    if any(abs(a - b) > _PLACE_TOL for a, b in radii):
        return None
    return X, Y, th


def session_changes(sk, name, placement, member_ids, known_ids):
    """The `changes` of :func:`update_definition` that make the definition `name` say what the sketch's geometry says now.

    `placement` is the instance's (X, Y, angle) when the edit began and `member_ids` its member key -> persistent id.
    Members are read by persistent id, so GeoId shifts do not matter: a member whose geometry is gone is removed, a
    member whose numbers or construction flag moved is changed, and every geometry whose persistent id is not in
    `known_ids` (the ids that existed when the edit began) is a new member.  Returns
    {'changes': {...}, 'moved': [keys], 'removed': [keys], 'added': [keys], 'moved_together': bool, 'frame': (X, Y, angle)};
    an empty `changes` means nothing differs.  When the surviving members (at least one, with two distinct points among them) all
    fit one rigid placement within the fit tolerance they have moved together: that moves the instance and not the
    block, so they are read through that placement ('frame'), `moved_together` says whether it differs from `placement`,
    and an added member is read in that frame too."""
    data = _load(sk)
    if name not in data["defs"]:
        raise SketchBlockError("there is no block called '%s'" % name)
    defn = data["defs"][name]
    idmap = _id_map(sk)
    geo = sk.Geometry
    # Members that all keep one rigid pose relative to each other have moved together: that moves the instance, it does
    # not change the block.  Then they are read through that pose, so only what else changed shows (an added member).
    alive, kinds = {}, True
    for k, g in member_ids.items():
        if k in defn["members"] and g in idmap:
            t, nums = _numbers(geo[idmap[g]])
            alive[k] = nums
            kinds = kinds and t == defn["members"][k]["t"]
    fit = rigid_fit(defn, alive) if kinds else None
    together = False
    if fit is not None:
        together = abs(fit[0] - placement[0]) > _PLACE_TOL or abs(fit[1] - placement[1]) > _PLACE_TOL or abs(math.remainder(fit[2] - placement[2], TWO_PI)) > 1e-9
    X, Y, th = fit if fit is not None else placement
    changes, moved, removed, added = {}, [], [], []
    for key, gid in sorted(member_ids.items()):
        if key not in defn["members"]:
            continue
        if gid not in idmap:
            changes[key] = None
            removed.append(key)
            continue
        i = idmap[gid]
        t, nums = _numbers(geo[i])
        m = defn["members"][key]
        if t != m["t"]:
            raise SketchBlockError("member '%s' changed its kind; a block member cannot" % key)
        local = to_local(t, nums, X, Y, th)
        flag = bool(sk.getConstruction(i))
        n_cmp = 3 if t == "a" else len(local)
        same = all(abs(a - b) <= _SAME for a, b in zip(local[:n_cmp], m["g"][:n_cmp]))
        if fit is not None:
            same = True  # the fit has accepted every member's numbers within the tolerance
        elif t == "a":
            same = same and abs(math.remainder(local[3] - m["g"][3], TWO_PI)) <= _SAME and abs(math.remainder(local[4] - m["g"][4], TWO_PI)) <= _SAME
        if same and flag == bool(m.get("c")):
            continue
        changes[key] = (_make(t, local), flag)
        moved.append(key)
    nxt = 1 + max([int(k[1:]) for k in defn["members"] if k[1:].isdigit()] + [-1])
    for i in range(len(geo)):
        gid = sk.getGeometryId(i)
        if gid in known_ids:
            continue
        t, nums = _numbers(geo[i])
        key = "m%04d" % nxt
        nxt += 1
        changes[key] = (_make(t, to_local(t, nums, X, Y, th)), bool(sk.getConstruction(i)))
        added.append(key)
    return {"changes": changes, "moved": moved, "removed": removed, "added": added, "moved_together": together, "frame": (X, Y, th)}


_OUTSIDE_TOL = 1e-7  # plain geometry that moved by less than this (mm) has not moved


def _outside_numbers(geo):
    """The numbers that say where a plain geometry is: its own for the supported kinds, its bounding box and length otherwise."""
    try:
        return _numbers(geo)[1]
    except SketchBlockError:
        try:
            shape = geo.toShape()
            box = shape.BoundBox
            return [box.XMin, box.YMin, box.XMax, box.YMax, shape.Length]
        except Exception:
            return []


def _constraint_key(c, gid_of):
    """A constraint's identity that survives a GeoId shift: its kind and the persistent ids of what it names."""
    def ref(g, pos):
        return (("g", gid_of[g]) if g in gid_of else ("x", g), pos)

    return (c.Type, ref(c.First, c.FirstPos), ref(c.Second, c.SecondPos), ref(c.Third, c.ThirdPos))


# Which properties count as the user's own state when Finish or Cancel rolls the document back by undo.  A property is
# the user's input unless one of these holds: it is flagged Output, ReadOnly or Transient (a recompute writes those),
# it holds a shape (the result of a recompute), or it is one of the derived fields of a sketch named below.  The session's
# own sketch also leaves out its Geometry, Constraints and BlockData, which are compared member by member, not as a whole.  The same rule serves a dependent of the session's sketch
# (a Pad over it recomputes during the session and that recompute is rolled back legitimately: its Shape, Placement and
# preview carry the flags or types above and do not count) and every other object: a Pad's Length, a Box's Length,
# an expression or a link do not carry them and do count.
_COMPUTED_STATUS = ("Output", "ReadOnly", "Transient")
_DERIVED_FIELDS = frozenset({"FullyConstrained", "ExternalGeo", "ExternalTypes", "AttacherType", "_ElementMapVersion"})
_OWN_FIELDS = frozenset({"BlockData", "Constraints", "Geometry"})


def _is_input(obj, prop, own):
    if prop in _DERIVED_FIELDS or (own and prop in _OWN_FIELDS):
        return False
    try:
        if "Shape" in obj.getTypeIdOfProperty(prop):
            return False
        st = obj.getPropertyStatus(prop)
    except Exception:
        return False
    return not any(x in _COMPUTED_STATUS for x in st if isinstance(x, str))


def content_hash(obj, prop):
    """A hash of what a property serialises to.  dumpPropertyContent returns a zip archive whose entries carry the time they
    were written, so the same content differs from one second to the next; the entries' contents are hashed instead."""
    import io
    import zipfile

    raw = bytes(obj.dumpPropertyContent(prop))
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            return hash(tuple((n, z.read(n)) for n in sorted(z.namelist())))
    except Exception:
        return hash(raw)


def _object_state(obj, own=False):
    """{property: hash of its serialised content} over an object's input properties, plus the expressions by path."""
    out = {}
    for prop in obj.PropertiesList:
        if not _is_input(obj, prop, own):
            continue
        try:
            out[prop] = content_hash(obj, prop)
        except Exception:
            out[prop] = hash(repr(getattr(obj, prop, None)))
    out["\0expr"] = dict(getattr(obj, "ExpressionEngine", None) or [])
    if not own:
        _add_appearance(obj, out)
        _add_hand_shape(obj, out)
    return out


# Document undo also restores how another object looks.  Visibility is flagged Output on the object, so the rule above
# leaves it out; these few are compared by hand.  Per-face colours are left out on purpose, a recompute rewrites them.
_VIEW_FIELDS = ("Visibility", "ShapeColor", "LineColor", "PointColor", "Transparency", "DisplayMode")


def _add_appearance(obj, out):
    out["Visibility"] = hash(repr(getattr(obj, "Visibility", None)))
    vo = getattr(obj, "ViewObject", None)
    for prop in _VIEW_FIELDS if vo is not None else ():
        if hasattr(vo, prop):  # ShapeColor is an attribute over ShapeAppearance, not a listed property
            out["view " + prop] = hash(repr(getattr(vo, prop)))


def _add_hand_shape(obj, out):
    """A Part::Feature has no inputs: nothing recomputes its Shape, so a Shape in it is what somebody assigned, and
    undoing the session takes it away.  Every other object's Shape is a result and is left out (see _is_input)."""
    if obj.TypeId == "Part::Feature" and "Shape" in obj.PropertiesList:
        try:
            out["Shape"] = content_hash(obj, "Shape")
        except Exception:
            out["Shape"] = hash(repr(obj.Shape.BoundBox))


def _state_diff(before, now):
    """The properties that differ between two _object_state results; an expression change names its path."""
    props = []
    for p in sorted((set(before) | set(now)) - {"\0expr"}):
        if before.get(p) != now.get(p):
            if p == "ExpressionEngine":
                e0, e1 = before["\0expr"], now["\0expr"]
                paths = sorted(k.lstrip(".") for k in set(e0) | set(e1) if e0.get(k) != e1.get(k))
                p = "ExpressionEngine: %s" % ", ".join(paths) if paths else p
            props.append(p)
    return props


def _document_state(sk):
    """Every object of the document but the sketch itself: label, type and input properties."""
    return {o.Name: (o.Label, o.TypeId, _object_state(o)) for o in sk.Document.Objects if o.Name != sk.Name}


def _con_facts(c):
    return (c.Name, bool(c.Driving), bool(c.IsActive), bool(c.InVirtualSpace))


def session_outside_state(sk, member_gids):
    """What an edit session must not lose without saying so, taken before it begins: where every geometry that is not one
    of the instance's members is (by persistent id, with its construction flag), every constraint (by what it names, with
    its value, name, active, driving and virtual-space settings and a label), every other input property of the sketch
    and every other object of the document."""
    member_gids = set(member_gids)
    geo = sk.Geometry
    gid_of = {i: sk.getGeometryId(i) for i in range(len(geo))}
    plain = {gid_of[i]: (_outside_numbers(g), bool(sk.getConstruction(i))) for i, g in enumerate(geo) if gid_of[i] not in member_gids}
    cons = [(_constraint_key(c, gid_of), float(c.Value), _constraint_label(c, k), _con_facts(c)) for k, c in enumerate(sk.Constraints)]
    return {"plain": plain, "cons": cons, "sketch": _object_state(sk, True), "doc": _document_state(sk)}


def session_outside_changes(sk, state, member_gids, full=False):
    """What differs from `state` (see :func:`session_outside_state`) outside the instance's members, and which constraints are
    new.  Finish keeps only the block's own change, so everything listed here other than 'added_constraints' is undone by it.
    Returns {'moved': [current GeoIds], 'deleted': count of deleted plain geometries, 'construction': [current GeoIds whose
    construction flag changed], 'deleted_constraints': [labels], 'changed_constraints': [labels, value changed],
    'renamed_constraints': [labels], 'flag_constraints': [labels, active, driving or virtual-space setting changed],
    'added_constraints': count, 'sketch_props': [other sketch properties that changed], 'objects_added': [labels],
    'objects_deleted': [labels], 'objects_changed': ['Label (property, ...)']}.  The last four need a pass over the
    whole document and are filled only with full=True (at Finish and Cancel, never on a refresh).  A constraint that went
    with a deleted member is the block change's business and is not listed."""
    member_gids = set(member_gids)
    geo = sk.Geometry
    idmap = {sk.getGeometryId(i): i for i in range(len(geo))}
    gid_of = {i: g for g, i in idmap.items()}
    moved, deleted, construction = [], 0, []
    for gid, (nums, flag) in state["plain"].items():
        if gid not in idmap:
            deleted += 1
            continue
        i = idmap[gid]
        now = _outside_numbers(geo[i])
        if len(now) != len(nums) or any(abs(a - b) > _OUTSIDE_TOL for a, b in zip(now, nums)):
            moved.append(i)
        if bool(sk.getConstruction(i)) != flag:
            construction.append(i)
    pool = {}
    for k, c in enumerate(sk.Constraints):
        pool.setdefault(_constraint_key(c, gid_of), []).append((float(c.Value), _con_facts(c)))
    lost_members = {g for g in member_gids if g not in idmap}
    gone, changed, renamed, flagged = [], [], [], []
    for key, value, label, facts in state["cons"]:
        have = pool.get(key)
        if not have:
            if not any(r[0][0] == "g" and r[0][1] in lost_members for r in key[1:]):
                gone.append(label)
            continue
        v, f = have.pop(0)
        if abs(v - value) > 1e-9 * max(1.0, abs(value)):
            changed.append(label)
        if f[0] != facts[0]:
            renamed.append(label)
        if f[1:] != facts[1:]:
            flagged.append(label)
    out = {
        "moved": sorted(moved),
        "deleted": deleted,
        "construction": sorted(construction),
        "deleted_constraints": gone,
        "changed_constraints": changed,
        "renamed_constraints": renamed,
        "flag_constraints": flagged,
        "added_constraints": sum(len(v) for v in pool.values()),
        "sketch_props": [],
        "objects_added": [],
        "objects_deleted": [],
        "objects_changed": [],
    }
    if full:
        out["sketch_props"] = _state_diff(state["sketch"], _object_state(sk, True))
        before, now = state["doc"], _document_state(sk)
        out["objects_added"] = sorted(now[n][0] for n in now if n not in before)
        out["objects_deleted"] = sorted(before[n][0] for n in before if n not in now)
        for n in sorted(set(before) & set(now)):
            props = _state_diff(before[n][2], now[n][2])
            if before[n][0] != now[n][0]:
                props.insert(0, "Label")
            if props:
                out["objects_changed"].append("%s (%s)" % (now[n][0], ", ".join(props)))
    return out


def _reshape(geo, t, g):
    """Give `geo` (a geometry of the sketch, a copy) the numbers `g`, in place, so that
    its persistent id and other extensions stay."""
    if t == "p":
        geo.X, geo.Y = g[0], g[1]
    elif t == "l":
        geo.StartPoint, geo.EndPoint = V(g[0], g[1], 0), V(g[2], g[3], 0)
    else:
        geo.Center, geo.Radius = V(g[0], g[1], 0), g[2]
        if t == "a":
            geo.setParameterRange(g[3], g[4])


# ---------------------------------------------------------------------------
# library (slice 5b1)
# ---------------------------------------------------------------------------

LIBRARY_FORMAT = "cadfork-sketch-block"
LIBRARY_VERSION = 1
LIBRARY_PARAM = "User parameter:BaseApp/Preferences/Mod/Sketcher/Blocks"
_MAX_FILE = 2 * 1024 * 1024  # bytes; 200 members come to about 20 kB
_MAX_MEMBERS = 5000
_MAX_COORD = 1.0e6  # mm; a sketch is not a kilometre wide
_MAX_ANGLE = 1.0e3  # rad
_KINDS = {"p": 2, "l": 4, "c": 3, "a": 5}
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {"COM%d" % i for i in range(1, 10)} | {"LPT%d" % i for i in range(1, 10)}
_BAD_CHARS = set('/\\:*?"<>|')


def _norm(text):
    """A name as the case-insensitive, normalisation-insensitive filesystem sees it."""
    return unicodedata.normalize("NFC", text).casefold()


def library_folder(folder=None):
    """The folder in use: `folder` if given, else the parameter ``LibraryFolder``, else ``SketchBlocks`` in the user's
    data directory.  The folder is not created here."""
    if folder is not None:
        if not isinstance(folder, (str, os.PathLike)) or not str(folder).strip():
            raise SketchBlockError("the library folder must be a path")
        return os.fspath(folder)
    custom = FreeCAD.ParamGet(LIBRARY_PARAM).GetString("LibraryFolder", "").strip()
    return custom or os.path.join(FreeCAD.getUserAppDataDir(), "SketchBlocks")


def _is_default_folder(folder):
    """True when `folder` is the built-in default: no argument and no configured ``LibraryFolder``."""
    return folder is None and not FreeCAD.ParamGet(LIBRARY_PARAM).GetString("LibraryFolder", "").strip()


def _folder_for(folder, create):
    """The folder; only the built-in default one is created when missing and `create`: a folder somebody named, in an
    argument or in the parameter, that has gone is reported, not silently made again."""
    path = library_folder(folder)
    if not os.path.isdir(path):
        if os.path.exists(path):
            raise SketchBlockError("the block library folder is a file, not a folder")
        if create and _is_default_folder(folder):
            try:
                os.makedirs(path, exist_ok=True)
            except OSError as exc:
                raise SketchBlockError("the block library folder cannot be created (%s)" % exc.strerror)
        else:
            raise SketchBlockError("the block library folder does not exist (%s)" % path)
    return path


def _file_name_ok(name):
    """`name` may be the name of a file in the library folder, or raises."""
    if not isinstance(name, str) or not name.strip():
        raise SketchBlockError("a library block needs a name")
    _text(name, "the library name")
    if name != name.strip() or name.endswith("."):
        raise SketchBlockError("a library name cannot start or end with a space or end with a dot")
    if name.startswith("."):
        raise SketchBlockError("a library name cannot start with a dot")
    if any(ord(ch) < 32 or ord(ch) == 127 or ch in _BAD_CHARS for ch in name):
        raise SketchBlockError("a library name cannot contain a slash, a colon, a control character or any of * ? \" < > |")
    if len(name.encode("utf-8")) > 200:
        raise SketchBlockError("the library name is too long")
    if name.split(".")[0].upper() in _RESERVED:
        raise SketchBlockError("'%s' is a reserved file name on Windows and cannot name a library block" % name)


def _entries(path):
    try:
        return os.listdir(path)
    except OSError as exc:
        raise SketchBlockError("the block library folder cannot be read (%s)" % exc.strerror)


def _file_of(path, name):
    """The existing file of block `name` (the exact name, up to Unicode normalisation) or None."""
    want = unicodedata.normalize("NFC", name + ".json")
    for e in _entries(path):
        if unicodedata.normalize("NFC", e) == want:
            return os.path.join(path, e)
    return None


def _num(v, lo, hi, what):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise SketchBlockError("%s must be a number" % what)
    limit = "at most 1e6 mm either way" if hi == _MAX_COORD else "at most 1e3 rad either way"
    try:
        v = float(v)
    except OverflowError:
        raise SketchBlockError("%s is out of range (%s)" % (what, limit))
    if not math.isfinite(v) or not lo <= v <= hi:
        raise SketchBlockError("%s is out of range (%s)" % (what, limit))
    return v


def _check_members(members):
    """The members of a library file or a definition, checked and copied: {key: {'t', 'g', 'c'}}."""
    if not isinstance(members, dict) or not members:
        raise SketchBlockError("the block has no members")
    if len(members) > _MAX_MEMBERS:
        raise SketchBlockError("the block has too many members (%d at most)" % _MAX_MEMBERS)
    out = {}
    for key, m in members.items():
        if not isinstance(key, str) or not key or len(key) > 64 or any(ord(ch) < 32 for ch in key):
            raise SketchBlockError("a member key is not valid")
        _text(key, "a member key")
        if not isinstance(m, dict) or set(m) != {"t", "g", "c"}:
            raise SketchBlockError("member '%s' must have exactly t, g and c" % key)
        t, g, c = m["t"], m["g"], m["c"]
        if not isinstance(t, str) or t not in _KINDS:
            raise SketchBlockError("member '%s' has an unknown kind" % key)
        if not isinstance(c, bool):
            raise SketchBlockError("member '%s': c must be true or false" % key)
        if not isinstance(g, list) or len(g) != _KINDS[t]:
            raise SketchBlockError("member '%s' needs %d numbers" % (key, _KINDS[t]))
        nums = []
        for i, v in enumerate(g):
            angle = t == "a" and i >= 3
            nums.append(_num(v, -_MAX_ANGLE if angle else -_MAX_COORD, _MAX_ANGLE if angle else _MAX_COORD, "a number of member '%s'" % key))
        if t in "ca" and not nums[2] > 0:
            raise SketchBlockError("member '%s' needs a positive radius" % key)
        if t == "a" and not 0 < nums[4] - nums[3] <= TWO_PI + 1e-9:
            raise SketchBlockError("member '%s' has an arc that is empty or longer than a turn" % key)
        out[key] = {"t": t, "g": nums, "c": c}
    _check_distinct(list(out.values()))
    return out


def _no_duplicates(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise ValueError("the key '%s' appears twice" % k)
        d[k] = v
    return d


def _refuse_constant(text):
    raise ValueError("%s is not a number a sketch can hold" % text)


def _read_block(path, name):
    """Read and check one block file; returns its members.  Raises SketchBlockError and nothing else."""
    try:
        real = os.path.realpath(path)
        folder = os.path.realpath(os.path.dirname(path))
        if os.path.commonpath([real, folder]) != folder:
            raise SketchBlockError("the file of block '%s' is a link to somewhere outside the library folder" % name)
        if not os.path.isfile(real):
            raise SketchBlockError("block '%s' is not a file" % name)
        if os.path.getsize(real) > _MAX_FILE:
            raise SketchBlockError("the file of block '%s' is too large to be a block" % name)
        with open(real, "rb") as fh:
            raw = fh.read(_MAX_FILE + 1)
        if len(raw) > _MAX_FILE:
            raise SketchBlockError("the file of block '%s' is too large to be a block" % name)
        try:
            doc = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=_refuse_constant)
        except (ValueError, RecursionError) as exc:
            raise SketchBlockError("the file of block '%s' is not a block file (%s)" % (name, str(exc).splitlines()[0][:80]))
        if not isinstance(doc, dict) or doc.get("format") != LIBRARY_FORMAT:
            raise SketchBlockError("the file of block '%s' is not a sketch block file" % name)
        version = doc.get("version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise SketchBlockError("the file of block '%s' has no version number" % name)
        if version > LIBRARY_VERSION:
            raise SketchBlockError("the file of block '%s' is from a newer version of the format (%d); this one reads up to %d" % (name, version, LIBRARY_VERSION))
        if version < 1:
            raise SketchBlockError("the file of block '%s' has an unknown version" % name)
        if set(doc) != {"format", "version", "name", "units", "members"}:
            raise SketchBlockError("the file of block '%s' lacks or adds keys" % name)
        if not isinstance(doc["name"], str) or doc["units"] != "mm":
            raise SketchBlockError("the file of block '%s' must have a name and units 'mm'" % name)
        _text(doc["name"], "the name in the file of block '%s'" % name)
        return _check_members(doc["members"])
    except SketchBlockError:
        raise
    except Exception as exc:  # nothing but a refusal leaves this function
        raise SketchBlockError("the file of block '%s' cannot be read (%s)" % (name, type(exc).__name__))


def library_list(folder=None):
    """What the library holds: a list of {'name', 'members' (count or None), 'error' (reason or None)}, sorted by name.
    A file that cannot be read as a block is listed with its reason and costs no good entry.  A missing or empty folder
    gives an empty list; files that do not end in .json, hidden files and sub-folders are ignored."""
    path = library_folder(folder)
    if not os.path.isdir(path):
        return []
    out = []
    for e in _entries(path):
        stem, ext = os.path.splitext(e)
        full = os.path.join(path, e)
        if ext != ".json" or e.startswith(".") or os.path.isdir(full):
            continue
        try:
            out.append({"name": stem, "members": len(_read_block(full, stem)), "error": None})
        except SketchBlockError as exc:
            out.append({"name": stem, "members": None, "error": str(exc)})
    out.sort(key=lambda r: (_norm(r["name"]), r["name"]))
    return out


def library_save(sk, name, folder=None, overwrite=False):
    """Write the definition `name` of the sketch to the library as ``<name>.json`` and return the path.  Refuses an
    unknown block, a name that is not a plain file name, an existing file unless `overwrite`, and a file whose name
    differs from an existing one only by case.  The write is atomic: a failure leaves the old file whole and no
    temporary file.  The sketch and its undo stack are not touched."""
    data = _load(sk)
    if name not in data["defs"]:
        raise SketchBlockError("there is no block called '%s'" % (name if isinstance(name, str) else "?"))
    _file_name_ok(name)
    members = _check_members(data["defs"][name]["members"])
    path = _folder_for(folder, True)
    target = name + ".json"
    for e in _entries(path):
        if e != target and _norm(e) == _norm(target):
            raise SketchBlockError(
                "the library already has '%s', which differs from '%s' only by case or by how an accented letter is written" % (e[: -len(".json")], name)
            )
    full = os.path.join(path, target)
    if os.path.isdir(full):
        raise SketchBlockError("'%s' is a folder in the library, not a block file" % name)
    if os.path.lexists(full) and not overwrite:
        raise SketchBlockError("the library already has a block called '%s'; save with overwrite to replace it" % name)
    text = json.dumps(
        {"format": LIBRARY_FORMAT, "version": LIBRARY_VERSION, "name": name, "units": "mm", "members": members},
        sort_keys=True,
        indent=1,
        ensure_ascii=True,
    ) + "\n"
    import tempfile

    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".block-", suffix=".tmp", dir=path)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, full)
        tmp = None
    except OSError as exc:
        raise SketchBlockError("the block could not be saved (%s)" % (exc.strerror or exc))
    finally:
        if tmp is not None:
            with contextlib.suppress(OSError):
                os.remove(tmp)
    return full


def library_load(sk, name, folder=None, as_name=None):
    """Copy the block `name` from the library into the sketch as a definition and return its name in the sketch
    (`as_name` if given).  The sketch keeps its own copy: no link to the file remains.  A block of that name already in
    the sketch with the very same members is reused and nothing changes; with different members the call is refused,
    unless `as_name` gives it another name, which follows the rules of :func:`define`.  One undo entry."""
    _file_name_ok(name)
    path = _folder_for(folder, False)
    full = _file_of(path, name)
    if full is None:
        raise SketchBlockError("the library has no block called '%s'" % name)
    members = _read_block(full, name)
    data = _load(sk)
    if as_name is not None:
        _name_ok(data, as_name)
        target = as_name
    else:
        target = name
        if target in data["inst"]:
            raise SketchBlockError("'%s' is the name of a block instance; load it with as_name" % target)
        if target in data["defs"]:
            if data["defs"][target]["members"] == members:
                return target
            raise SketchBlockError("the sketch already has a different block called '%s'; load it with as_name to keep both" % target)
    data["defs"][target] = {"members": members}
    with _transaction(sk, "Load block"):
        sk.BlockData = _dump(data)
    return target


def library_delete(name, folder=None):
    """Remove the file of block `name` from the library.  Sketches that loaded it are unaffected."""
    _file_name_ok(name)
    path = _folder_for(folder, False)
    full = _file_of(path, name)
    if full is None:
        raise SketchBlockError("the library has no block called '%s'" % name)
    if os.path.isdir(full) and not os.path.islink(full):
        raise SketchBlockError("'%s' is a folder, not a block file" % name)
    try:
        os.remove(full)
    except OSError as exc:
        raise SketchBlockError("the block could not be deleted (%s)" % (exc.strerror or exc))
