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

Supported members: points, line segments, circles, arcs of circle.  Anything
else, an empty or degenerate set, a duplicate name and the like raise
:class:`SketchBlockError` before anything is touched."""

import contextlib
import json
import math

import FreeCAD
import Part

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


def _check_distinct(members):
    pts = []
    for m in members:
        pts += _points(m["t"], m["g"])
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


def _add_instance_geometry(sk, defn, X, Y, th, keys=None):
    """Add the members of `defn` at the placement; returns {key: persistent id}."""
    keys = sorted(defn["members"]) if keys is None else keys
    geos, flags = [], []
    for key in keys:
        m = defn["members"][key]
        geos.append(_make(m["t"], _to_world(m["t"], m["g"], X, Y, th)))
        flags.append(bool(m.get("c")))
    gids = sk.addGeometry(geos, False)
    if isinstance(gids, int):
        gids = [gids]
    out = {}
    for key, gid, flag in zip(keys, gids, flags):
        if flag:
            sk.setConstruction(gid, True)
        out[key] = sk.getGeometryId(gid)
    return out


def insert(sk, name, x=0.0, y=0.0, angle=0.0):
    """Place an instance of block `name` with its origin at (x, y), rotated by
    `angle` radians (any value, not wrapped).  Returns the instance's name.

    The origin is the frame's origin fixed when the block was defined (the first
    point of the first member then).  It does not follow member 0 if
    :func:`update_definition` later moves that member."""
    data = _load(sk)
    if name not in data["defs"]:
        raise SketchBlockError("there is no block called '%s'" % name)
    try:
        x, y, angle = float(x), float(y), float(angle)
    except (TypeError, ValueError):
        raise SketchBlockError("the placement must be numbers")
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
    numbers) within the fit tolerance, or None.  Needs at least two members and two distinct points; a rigid move of a
    lone member cannot be told from a change of the definition, so it is not read as one."""
    keys = [k for k in sorted(world) if k in defn["members"]] if keys is None else keys
    if len(keys) < 2:
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
    an empty `changes` means nothing differs.  When the surviving members (at least two, with two distinct points) all
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


def session_outside_state(sk, member_gids):
    """What an edit session must not lose without saying so, taken before it begins: where every geometry that is not one
    of the instance's members is (by persistent id), and every constraint (by what it names, with its value and a label)."""
    member_gids = set(member_gids)
    geo = sk.Geometry
    gid_of = {i: sk.getGeometryId(i) for i in range(len(geo))}
    plain = {gid_of[i]: _outside_numbers(g) for i, g in enumerate(geo) if gid_of[i] not in member_gids}
    cons = [(_constraint_key(c, gid_of), float(c.Value), _constraint_label(c, k)) for k, c in enumerate(sk.Constraints)]
    return {"plain": plain, "cons": cons}


def session_outside_changes(sk, state, member_gids):
    """What differs from `state` (see :func:`session_outside_state`) outside the instance's members, and which constraints are
    new.  Finish keeps only the block's own change, so everything listed here other than 'added' is undone by it.
    Returns {'moved': [current GeoIds], 'deleted': [count of deleted plain geometries], 'deleted_constraints': [labels],
    'changed_constraints': [labels], 'added_constraints': count}.  A constraint that went with a deleted member is the
    block change's business and is not listed."""
    member_gids = set(member_gids)
    geo = sk.Geometry
    idmap = {sk.getGeometryId(i): i for i in range(len(geo))}
    gid_of = {i: g for g, i in idmap.items()}
    moved, deleted = [], 0
    for gid, nums in state["plain"].items():
        if gid not in idmap:
            deleted += 1
            continue
        now = _outside_numbers(geo[idmap[gid]])
        if len(now) != len(nums) or any(abs(a - b) > _OUTSIDE_TOL for a, b in zip(now, nums)):
            moved.append(idmap[gid])
    pool = {}
    for k, c in enumerate(sk.Constraints):
        pool.setdefault(_constraint_key(c, gid_of), []).append(float(c.Value))
    lost_members = {g for g in member_gids if g not in idmap}
    gone, changed = [], []
    for key, value, label in state["cons"]:
        have = pool.get(key)
        if not have:
            if not any(r[0][0] == "g" and r[0][1] in lost_members for r in key[1:]):
                gone.append(label)
            continue
        v = have.pop(0)
        if abs(v - value) > 1e-9 * max(1.0, abs(value)):
            changed.append(label)
    return {
        "moved": sorted(moved),
        "deleted": deleted,
        "deleted_constraints": gone,
        "changed_constraints": changed,
        "added_constraints": sum(len(v) for v in pool.values()),
    }


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
