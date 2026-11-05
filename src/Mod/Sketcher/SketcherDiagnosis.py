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
# *   Lesser General Public License for more details.                      *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************

"""Conflict and redundancy diagnosis for sketches.

``diagnose(sketch)`` answers "which constraints conflict and what should I
change" with solver-tested answers.  It does not reimplement detection: the
PlaneGCS solver is asked, through ``SketchObject.solve()``,
``ConflictingConstraints``, ``RedundantConstraints``,
``PartiallyRedundantConstraints``, ``DoF`` and ``setActive``, on a throwaway
copy of the sketch that lives in a hidden temporary document.  The user's
sketch and document are never touched (constraint list, geometry,
expressions, undo stack, touched state and the active document are the same
afterwards, also when the diagnosis raises).

Index conventions: the solver lists (``ConflictingConstraints`` and friends)
are 1-based; every index in this module's results is the 0-based position in
``sketch.Constraints``.

Method
------
Minimal conflict sets.  A set S of constraints "conflicts" when, with only S
active among the candidate constraints, the solver reports a non-empty
``ConflictingConstraints``.  Candidates are the active, driving constraints
that are not internal alignment.  All minimal conflict sets are enumerated
with a Reiter hitting-set tree: each node removes a set of constraints, asks
the solver whether the rest still conflicts, seeds a search from the solver's
own conflict list (grown through shared geometry when the list alone is not
yet a conflict) and shrinks it with a chunked deletion filter, which ends in
a set from which no single member can be dropped.  The solver's
detection depends on the geometry it starts from and every successful solve
moves the geometry, so every probe starts from the geometry the sketch was
drawn with (it is put back after each successful solve).  Every returned set
is then re-verified on brand-new copies of the user's sketch, one per subset
tested up to 300 constraints (conflicting, and consistent without any one
member); a set that fails is shrunk again or dropped, supersets of other sets
are dropped, and a missing set is searched for so that the sets together still
cover the sketch.  The
number of sets and the run time are capped; ``complete`` says whether the
enumeration finished.

Redundancy groups.  The same machinery with the predicate "the solver
reports redundant or partially redundant constraints and no conflicts".  Each
round shrinks to a minimal redundant group, picks one member to remove and
deactivates it before looking for the next group.  The member suggested for
removal is the newest one (highest index) whose removal keeps the DOF, moves no
solved point by more than 1e-9 and leaves none of the group's other members
redundant.  When no member does (overlapping groups: three equal distances on one
line, where any two are redundant) a member is taken whose removal keeps the DOF
and the geometry and leaves strictly fewer redundancy groups; the group is then
marked ``overlaps`` and "apply, diagnose again, apply" reaches a clean sketch.
The removal is also returned as ``fix``, an ``apply_fix`` dict tested like a
conflict fix on a fresh copy of the sketch *as it is*, the conflicts in place:
applied for real and recomputed, then it is offered only if no conflict appears
beyond the sketch's own, the solver code is acceptable, nothing redundant
appears that was not there, the DOF is the sketch's, and (with no conflict) the
number of redundancy groups is smaller.  ``remaining_conflicts`` is what that
copy reports and ``dof_after`` is stated only when none is left (``None``
otherwise: with a conflict in place the solver's count is not the sketch's).
When no removal can be offered the group carries ``no_fix_reason`` (a code) and
``no_fix_text`` (plain words).  A fix to a conflict is judged against the
redundancy that was already in the sketch: only redundancy it creates counts
against it.

Recompute stability.  Every fix that is offered is applied to a fresh copy, the
copy is solved, touched and recomputed, and the fix is dropped if the recompute
changes anything a fix is meant to leave: a datum, a driving state, an
expression, or the solver's verdict (code, DOF, conflicting and redundant
lists).  A recompute solves again from the geometry the first solve left, and
the solver does not always give the same answer from there (a sketch that solves
cleanly from the drawn geometry can come back redundant, with more DOF, from the
solved one), so a certification from the drawn start alone is not enough.  This
runs on every sketch up to 300 constraints (``_CONFIRM_ALL_LIMIT``), with or
without expressions.  The one exception is larger sketches without expressions:
there the fixes are tested by deactivating constraints on the working copy and
only the combined plan is applied to a fresh copy, because one native solve of
the 1,000-constraint case takes 1.2 s and a recompute per fix would break the
1.5 s event-loop bar of the panel (G10 slice 4a, check 7); on those sketches a
fix is not certified against a recompute.  A fix that is dropped is listed in
``not_offered`` with ``reason`` (a code) and ``reason_text`` (plain words).

Other objects.  The throwaway copy carries no other object.  Before a fix is
offered the real document is searched for expressions of other objects that read
this sketch's constraints (``Sketch.Constraints.name`` or
``Sketch.Constraints[3]``); a fix that deletes, shifts (any delete shifts every
later index), makes non-driving or re-values a constraint they read is not
offered and is listed with the reason ``read_by_another_object``.  A datum bound
by an expression to another object (a spreadsheet cell) cannot be tested honestly
on a copy that cannot carry the binding, so no value change is offered on it
(reason ``bound_outside_sketch``).

Fixes.  For every minimal conflict set the module proposes, per member, to
delete it and, for datum constraints, to make it non-driving; a change of
the datum to the value the rest of the set forces is generated too.  Delete
and non-driving candidates are first tested on the working copy by
deactivating the constraint (to the solver an inactive, a deleted and a
reference constraint are the same) and re-solving.  The fix is offered only
if its own set is gone, no new conflict and no new redundancy appeared and the
solver accepts it.  On sketches up to 300 constraints every offered fix is
then applied for real (``delConstraint`` / ``setDriving``, the code that
``apply_fix`` runs on the real sketch) to a fresh copy and re-solved, and the
fix is dropped if that disagrees with the trial; on larger sketches the
combined ``plan`` of all top-ranked fixes is the part applied to a fresh copy.
``applied_to_fresh_copy`` says which.  If a delete or non-driving fix leaves a
redundant constraint behind, the removal of that redundant constraint is
appended to the fix (a multi-operation fix) and the whole thing is tested
again; redundancy that was already there (``redundancy_members``) is not
counted as caused by the fix.  The reported ``dof_after`` is the DOF measured
on the copy, and ``None`` when a conflict is left.

A change-datum fix cannot be a clean fix: a conflict the solver detects is a
rank deficiency, a new datum value cannot remove it, and FreeCAD's
``setDatum`` rejects the result as redundant.  Such candidates are tested the
same way and listed under ``alternatives`` only if the solver accepts them
(it never did on the corpus).

Ranking rule (documented because it is a heuristic).  Fixes of one conflict
set are sorted by, in order:

1. the number of conflict sets the fix resolves, most first;
2. the number of operations, fewest first;
3. how far the drawn geometry is from satisfying the constrained member
   (``calculateConstraintError`` on an untouched copy, relative to the datum
   or to the sketch extent), largest first: the constraint that disagrees most
   with the drawing is the likeliest mistake;
4. the kind of change: change datum, then make non-driving, then delete;
5. the newest constraint (highest index).

The working copy.  The sketch is rebuilt in the scratch document from the
serialised ``Geometry``, ``ExternalGeo`` and ``Constraints`` properties plus
its expressions (read-only on the user's sketch).  Links to other objects are
not followed, so the copy needs neither the support nor the referenced
objects; expressions that point at other objects are not re-bound, and the
copy is solved but never recomputed (except by the stability check above), so
datum values are those already evaluated in the user's sketch.

Limits.  Detection is the solver's rank-based detection: infeasible but
full-rank systems (a triangle with sides 3, 4 and 10) and solver failures
without a conflict list are reported as ``solver_failed`` without conflict
sets.  Conflict sets are minimal with respect to the solver's predicate, which
is monotone in practice but not proven to be.  Block constraints that the raw
solver does not flag are reported as solved (faithful to the solver).  Because
detection is rank-based it can depend on the drawn geometry (two lines drawn
exactly horizontal, with a distance of 12 against a horizontal extent of 10,
are flagged as a conflict; drawn slightly off they might not be): the sets are
those of the sketch as drawn.  Fix ranking does not know what a designer
wants.
"""

import itertools
import time

import FreeCAD

__all__ = ["diagnose", "apply_fix", "DiagnosisError", "DiagnosisCancelled"]

_DATUM_TYPES = {
    "Distance",
    "DistanceX",
    "DistanceY",
    "Radius",
    "Diameter",
    "Angle",
    "Weight",
    "SnellsLaw",
}
_FIXED_TYPES = {"InternalAlignment"}
_KIND_RANK = {"set_datum": 0, "set_driving": 1, "delete": 2}
_STATUS_TEXT = {
    0: "solved",
    -1: "solver_failed",
    -2: "redundant",
    -3: "conflicting",
    -4: "conflicting",
    -5: "malformed",
}
_POSITION_TOLERANCE = 1e-9


class DiagnosisError(RuntimeError):
    """Raised for input the diagnosis cannot work with."""


class DiagnosisCancelled(DiagnosisError):
    """Raised by ``diagnose`` when its progress callback asked to stop.  The
    scratch document is closed and the user's sketch is untouched, as after
    any other exit."""


# The progress callback of the diagnosis running now, if any.  Diagnosis runs
# on the main thread (document operations are not thread-safe), so a caller
# that must stay responsive gets control back through this hook at every
# solver call, copy and long loop step.
_progress = None
_progress_ticks = 0


def _tick(phase="solve"):
    """Give the caller's progress callback a turn; it cancels by returning
    a true value.  Without a callback this does nothing."""
    global _progress_ticks
    if _progress is None:
        return
    _progress_ticks += 1
    if _progress({"phase": phase, "ticks": _progress_ticks}):
        raise DiagnosisCancelled("diagnosis cancelled")


# ---------------------------------------------------------------------------
# Throwaway copies
# ---------------------------------------------------------------------------


_scratch_ids = itertools.count(1)


class _Scratch:
    """A hidden temporary document that holds throwaway copies of one sketch."""

    def __init__(self, sketch):
        self._source = sketch
        self._previous = FreeCAD.ActiveDocument
        self._previous_name = self._previous.Name if self._previous is not None else None
        name = "SketchDiagnosisScratch%d" % next(_scratch_ids)  # unique, so a nested use closes its own document
        self.doc = FreeCAD.newDocument(name, name, True, True)
        self.name = self.doc.Name
        self.dropped_expressions = []  # paths of expressions the copy could not carry (bound outside the sketch)
        try:
            self.doc.UndoMode = 0
        except Exception:
            pass

    def copy(self, recompute=False):
        """Rebuild the user's sketch in the scratch document.

        Geometry, external geometry and constraints are copied through the
        properties' own serialisation (a read-only operation on the user's
        sketch; ``Document.copyObject`` would add a UUID property to it), the
        expressions are re-bound by path.  Links to other objects (support,
        external references) are not needed by the solver and are not
        followed: the cached external geometry travels with the copy.  The
        copy is never recomputed, only solved."""
        src = self._source
        _tick("copy")
        n = self.doc.addObject("Sketcher::SketchObject", "Copy")
        n.Placement = src.Placement
        for prop in ("Geometry", "ExternalGeo", "Constraints"):
            n.restorePropertyContent(prop, src.dumpPropertyContent(prop))
        for path, expr in src.ExpressionEngine:
            try:
                n.setExpression(path.lstrip("."), expr)
            except Exception:
                # bound to another object, which the copy does not have: remembered, so that no fix is certified
                # on a datum whose binding the copy cannot honour
                if path.lstrip(".") not in self.dropped_expressions:
                    self.dropped_expressions.append(path.lstrip("."))
        return n

    def close(self):
        try:
            FreeCAD.closeDocument(self.name)
        finally:
            names = FreeCAD.listDocuments()
            if self._previous_name is not None and self._previous_name in names:
                FreeCAD.setActiveDocument(self._previous_name)


def _discard(sketch_copy):
    """Drop a finished copy so scratch documents do not grow."""
    try:
        sketch_copy.Document.removeObject(sketch_copy.Name)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Solver probe
# ---------------------------------------------------------------------------


class _Status:
    __slots__ = ("code", "conflicts", "redundant", "partial", "malformed", "dof")

    def __init__(self, sk):
        _tick("solve")
        self.code = sk.solve()
        self.conflicts = [i - 1 for i in sk.ConflictingConstraints]
        self.redundant = [i - 1 for i in sk.RedundantConstraints]
        self.partial = [i - 1 for i in sk.PartiallyRedundantConstraints]
        self.malformed = [i - 1 for i in sk.MalformedConstraints]
        self.dof = sk.DoF

    @property
    def has_conflict(self):
        return bool(self.conflicts)

    @property
    def redundancy(self):
        return sorted(set(self.redundant) | set(self.partial))

    @property
    def only_redundant(self):
        return not self.conflicts and bool(self.redundancy)


class _Probe:
    """Toggle candidate constraints on a copy and ask the solver."""

    def __init__(self, sk):
        self.sk = sk
        cons = sk.Constraints
        self.cons = cons
        self.n = len(cons)
        self.types = [c.Type for c in cons]
        self.driving = [bool(c.Driving) for c in cons]
        self.geo = [
            {g for g in (c.First, c.Second, c.Third) if g != -2000 and g >= 0} for c in cons
        ]
        self.inactive = [i for i, c in enumerate(cons) if not c.IsActive]
        self.candidates = [
            i
            for i, c in enumerate(cons)
            if c.IsActive and c.Driving and c.Type not in _FIXED_TYPES
        ]
        self.cand_set = set(self.candidates)
        self.state = {i: True for i in self.candidates}
        self.solves = 0
        # The solver's conflict detection depends on the geometry it starts
        # from, and every solve moves the geometry.  Each probe therefore
        # starts from the geometry the sketch had when the probe was built.
        self.start_geometry = list(sk.Geometry)
        self.moved = False

    def status(self):
        self.solves += 1
        if self.moved:
            self.sk.Geometry = self.start_geometry
        st = _Status(self.sk)
        # a solve that fails (conflict, solver failure, malformed) leaves the
        # geometry alone; one that succeeds moves it
        self.moved = st.code in (0, -2)
        return st

    def activate_only(self, keep):
        """Activate exactly `keep` among the candidates (others inactive)."""
        keep = set(keep)
        for i in self.candidates:
            want = i in keep
            if self.state[i] != want:
                _tick("activate")
                self.sk.setActive(i, want)
                self.state[i] = want

    def check(self, keep):
        self.activate_only(keep)
        return self.status()

    def conflicts_on(self, keep):
        return self.check(keep).has_conflict

    def redundant_on(self, keep):
        return self.check(keep).only_redundant


def _shrink(items, predicate):
    """Chunked deletion filter: shrink `items` to a set that satisfies the
    monotone `predicate` and from which no single element can be dropped."""
    items = list(items)
    chunk = max(1, len(items) // 2)
    while True:
        i = 0
        while i < len(items):
            trial = items[:i] + items[i + chunk :]
            if trial and predicate(trial):
                items = trial
            else:
                i += chunk
        if chunk == 1:
            break
        chunk = max(1, chunk // 2)
    return items


def _expand_seed(probe, seed, universe, predicate):
    """Grow `seed` through shared geometry until the predicate holds; fall
    back to the whole universe."""
    seed = sorted(set(seed) & set(universe))
    if seed and predicate(seed):
        return seed
    universe = sorted(universe)
    current = set(seed)
    for _ in range(4):
        geo = set()
        for i in current:
            geo |= probe.geo[i]
        grown = current | {i for i in universe if probe.geo[i] & geo}
        if grown == current:
            break
        current = grown
        if predicate(sorted(current)):
            return sorted(current)
    if predicate(universe):
        return universe
    return None


# ---------------------------------------------------------------------------
# Conflict sets
# ---------------------------------------------------------------------------


def _enumerate_conflict_sets(probe, max_sets, deadline):
    """Reiter hitting-set tree over minimal conflict sets."""
    found = []
    complete = True
    visited = set()
    stack = [frozenset()]
    while stack:
        removed = stack.pop()
        if removed in visited:
            continue
        visited.add(removed)
        if time.time() > deadline:
            complete = False
            break
        known = next((m for m in found if not (m & removed)), None)
        if known is None:
            universe = [i for i in probe.candidates if i not in removed]
            st = probe.check(universe)
            if not st.has_conflict:
                continue
            seed = _expand_seed(probe, st.conflicts, universe, probe.conflicts_on)
            if seed is None:
                complete = False
                continue
            members = _shrink(seed, probe.conflicts_on)
            known = frozenset(members)
            if known not in found:
                if len(found) >= max_sets:
                    complete = False
                    break
                found.append(known)
        for m in sorted(known, reverse=True):
            stack.append(removed | {m})
    return found, complete


_FRESH_COPY_LIMIT = 300


class _FreshTester:
    """The reference predicate for the final verification: does a subset of
    the candidates conflict, asked from the geometry the user drew and with no
    state left over from earlier solves?

    Up to ``_FRESH_COPY_LIMIT`` constraints every test gets a brand-new copy
    of the user's sketch.  On larger sketches a copy costs one full solve, so
    one new copy serves all tests of a set and the drawn geometry is put back
    before each test (the geometry is the only solver input that earlier
    solves change)."""

    def __init__(self, diagnoser):
        self.d = diagnoser
        self.per_test = diagnoser.n <= _FRESH_COPY_LIMIT
        self.sk = None

    def open_group(self):
        """Start a group of tests that may share one copy (large sketches)."""
        self.close_group()

    def close_group(self):
        if self.sk is not None:
            _discard(self.sk)
            self.sk = None

    def conflicts(self, subset):
        keep = set(subset)
        sk = self.sk
        fresh = sk is None or self.per_test
        if fresh:
            self.close_group()
            sk = self.d._copy()
            if not self.per_test:
                self.sk = sk
                self.geo = list(sk.Geometry)
        else:
            sk.Geometry = self.geo
        try:
            for i in self.d.probe.candidates:
                _tick("activate")
                sk.setActive(i, i in keep)
            _tick("solve")
            sk.solve()
            return bool(sk.ConflictingConstraints)
        finally:
            if self.per_test:
                _discard(sk)


def _verify_minimal(tester, members):
    """Conflicting, and consistent without any one member."""
    members = sorted(members)
    tester.open_group()
    try:
        if not tester.conflicts(members):
            return False
        return all(not tester.conflicts([x for x in members if x != m]) for m in members)
    finally:
        tester.close_group()


# ---------------------------------------------------------------------------
# Redundancy groups
# ---------------------------------------------------------------------------


def _geometry_signature(sk):
    sig = []
    for g in sk.Geometry:
        vals = []
        for attr in (
            "StartPoint",
            "EndPoint",
            "Center",
            "Location",
            "X",
            "Y",
            "Radius",
            "MajorRadius",
            "MinorRadius",
            "FirstParameter",
            "LastParameter",
        ):
            try:
                v = getattr(g, attr)
            except Exception:
                continue
            if hasattr(v, "x"):
                vals.extend((v.x, v.y, v.z))
            else:
                vals.append(float(v))
        try:
            vals.extend(c for p in g.getPoles() for c in (p.x, p.y, p.z))
        except Exception:
            pass
        sig.append(vals)
    return sig


def _max_move(before, after):
    if len(before) != len(after):
        return float("inf")
    worst = 0.0
    for a, b in zip(before, after):
        if len(a) != len(b):
            return float("inf")
        for x, y in zip(a, b):
            worst = max(worst, abs(x - y))
    return worst


def _redundancy_groups(probe, base_active, deadline, max_groups=16, first_code=None, overlap_fallback=True):
    """Find redundancy groups among `base_active` candidates.

    Returns (groups, removed) where each group is a dict with the members and
    the suggested removal.  ``first_code``, a list, receives the solver code of
    the first status (the code a sketch with this redundancy returns)."""
    groups = []
    active = list(base_active)
    for round_ in range(max_groups):
        if time.time() > deadline:
            break
        st = probe.check(active)
        if round_ == 0 and first_code is not None:
            first_code.append(st.code)
        if not st.only_redundant:
            break
        before = _geometry_signature(probe.sk)
        dof_before = st.dof
        seed = _expand_seed(probe, st.redundancy, active, probe.redundant_on)
        if seed is None:
            break
        members = _shrink(seed, probe.redundant_on)
        chosen = None
        report = None
        for m in sorted(members, reverse=True):
            rest = [i for i in active if i != m]
            st2 = probe.check(rest)
            after = _geometry_signature(probe.sk)
            move = _max_move(before, after)
            still = [i for i in members if i != m and i in st2.redundancy]
            if st2.has_conflict or st2.dof != dof_before or move > _POSITION_TOLERANCE or still:
                continue
            chosen = m
            report = (st2.dof, move)
            break
        overlaps = False
        if chosen is None and overlap_fallback:
            # Overlapping groups (three equal distances on one line: any two are
            # redundant): no removal clears this group on its own, but one that
            # keeps the DOF and the geometry and leaves strictly less redundancy
            # is a step that "apply, diagnose again" repeats to a clean sketch.
            # (the solver's list names one constraint per redundancy, not the
            # rank deficiency, so progress is counted in groups)
            n_before = None
            for m in sorted(members, reverse=True):
                rest = [i for i in active if i != m]
                st2 = probe.check(rest)
                after = _geometry_signature(probe.sk)
                move = _max_move(before, after)
                if st2.has_conflict or st2.dof != dof_before or move > _POSITION_TOLERANCE:
                    continue
                if n_before is None:
                    n_before = len(_redundancy_groups(probe, active, deadline, overlap_fallback=False)[0])
                if len(_redundancy_groups(probe, rest, deadline, overlap_fallback=False)[0]) >= n_before:
                    continue
                probe.check(rest)  # leave the probe where the loop expects it
                chosen = m
                report = (st2.dof, move)
                overlaps = True
                break
        if chosen is None:
            chosen = max(members)
            report = (None, None)
        group = {
            "members": sorted(members),
            "suggested_removal": chosen,
            "dof_after": report[0],
            "max_point_move": report[1],
        }
        if overlaps:
            group["overlaps"] = True
        groups.append(group)
        active = [i for i in active if i != chosen]
    return groups, active


# ---------------------------------------------------------------------------
# Fixes
# ---------------------------------------------------------------------------


def _expression_paths(sk, index):
    """Expression paths that bind constraint `index` (its own datum) or read it."""
    name = sk.Constraints[index].Name
    own, readers = [], []
    own_path = "Constraints[%d]" % index
    for path, expr in sk.ExpressionEngine:
        p = path.lstrip(".")
        if name and p == "Constraints." + name or p == own_path:
            own.append(path)
        elif name and ("Constraints." + name) in expr:
            readers.append(path)
    return own, readers


def _has_own_expression(sk, index):
    return bool(_expression_paths(sk, index)[0])


def _datum_state(sk):
    """What a recompute must leave alone: each constraint's value, driving flag
    and activity, and the expressions."""
    return (
        # a reference constraint's value is measured, not set: only the driving
        # values (and the flags) are the result
        [
            (c.Value if c.Driving else 0.0, bool(c.Driving), bool(c.IsActive))
            for c in sk.Constraints
        ],
        sorted((str(p).lstrip("."), str(e)) for p, e in sk.ExpressionEngine),
    )


def _same_datum_state(a, b):
    if a[1] != b[1] or len(a[0]) != len(b[0]):
        return False
    for (v1, d1, a1), (v2, d2, a2) in zip(a[0], b[0]):
        if d1 != d2 or a1 != a2 or abs(v1 - v2) > 1e-9 * max(1.0, abs(v1)):
            return False
    return True


def _solver_state(sk):
    """What the solver says about a sketch: code, degrees of freedom and the conflicting and redundant lists."""
    st = _Status(sk)
    return (st.code, st.dof, tuple(st.conflicts), tuple(st.redundancy))


def _recompute_changes(sk):
    """Touch and recompute a throwaway copy; True if that changed anything a
    fix is meant to leave: a datum, a driving state, an expression or the
    solver's verdict (``_solver_state``).  A recompute solves again from the
    geometry the first solve left, which is a different start than the drawn
    one the certification solved from."""
    _tick("solve")
    sk.solve()  # reference values are measured by a solve; a recompute reads them
    before = _datum_state(sk)
    solver_before = _solver_state(sk)
    sk.touch()
    sk.Document.recompute()
    return not _same_datum_state(before, _datum_state(sk)) or solver_before != _solver_state(sk)


def _apply_ops(sk, ops):
    """Apply fix operations to a sketch.  Used for throwaway copies and for
    the real sketch alike, so a tested fix behaves the same when applied."""
    ordered = [o for o in ops if o["op"] != "delete"]
    ordered += sorted((o for o in ops if o["op"] == "delete"), key=lambda o: -o["index"])
    for o in ordered:
        i = o["index"]
        if o["op"] == "set_datum":
            q = o["value"]
            unit = o.get("unit", "mm")
            sk.setDatum(i, FreeCAD.Units.Quantity(q, unit) if unit else q)
        elif o["op"] == "set_driving":
            for path in _expression_paths(sk, i)[0]:
                sk.clearExpression(path)
            sk.setDriving(i, bool(o["value"]))
        elif o["op"] == "delete":
            own, readers = _expression_paths(sk, i)
            for path in own + readers:
                sk.clearExpression(path)
            sk.delConstraint(i)
        else:
            raise DiagnosisError("unknown fix operation %r" % (o["op"],))


_READ = None


def _external_readers(sketch):
    """Which of this sketch's constraints other objects' expressions read:
    (names, indices).  An expression that reads ``Sketch.Constraints.name`` or
    ``Sketch.Constraints[3]`` breaks, or changes its value, when the fix
    deletes or re-values what it reads, and the throwaway copy carries no
    other object to show that."""
    global _READ
    import re

    if _READ is None:
        _READ = re.compile(
            r"(?<![\w.>])(<<[^>]+>>|[A-Za-z_]\w*)\s*\.\s*Constraints\s*(?:\.\s*([A-Za-z_]\w*)|\[\s*(\d+)\s*\])"
        )
    names, indices = set(), set()
    me = {sketch.Name, sketch.Label}
    try:
        others = list(sketch.Document.Objects)
    except Exception:
        return names, indices
    for obj in others:
        if obj.Name == sketch.Name:
            continue
        try:
            engine = obj.ExpressionEngine
        except Exception:
            continue
        for _path, expr in engine:
            for tok, name, idx in _READ.findall(str(expr)):
                if tok.strip("<>") in me:
                    if name:
                        names.add(name)
                    else:
                        indices.add(int(idx))
    return names, indices


def _external_bindings(sketch):
    """Paths of this sketch's own expressions that read another object (a
    spreadsheet cell, another sketch): the throwaway copy has no such object,
    so a fix that changes one of these datums cannot be tested honestly."""
    import re

    out = []
    doc = sketch.Document
    me = {sketch.Name, sketch.Label}
    for path, expr in sketch.ExpressionEngine:
        for tok in re.findall(r"(?<![\w.>])(<<[^>]+>>|[A-Za-z_]\w*)\s*\.", str(expr)):
            name = tok.strip("<>")
            if name in me:
                continue
            if doc.getObject(name) is not None or doc.getObjectsByLabel(name):
                out.append(str(path).lstrip("."))
                break
    return out


_REASON_TEXT = {
    "recompute_changes_result": "a recompute would undo it",
    "read_by_another_object": "another object's expression reads the constraint",
    "bound_outside_sketch": "its value is bound to an expression outside the sketch, which the test copy cannot carry",
    "result_not_reproduced": "applied for real and recomputed, the sketch does not end up as the test showed",
    "leaves_new_conflict": "removing it would leave a new conflict",
    "leaves_new_redundancy": "removing it would leave other redundancy that was not there",
    "changes_dof": "removing it would change the degrees of freedom",
    "does_not_shrink": "removing it would not reduce the redundancy",
    "no_safe_member": "no member of the group can be removed without changing the sketch",
    "solver_rejected": "the solver rejects the result",
    "out_of_time": "the time budget ran out",
}


def reason_text(code):
    """Plain words for a ``not_offered`` reason code."""
    return _REASON_TEXT.get(code, str(code))


def apply_fix(sketch, fix):
    """Apply a fix returned by ``diagnose`` to a real sketch."""
    _apply_ops(sketch, fix["ops"])


def _unit_of(ctype):
    return "deg" if ctype == "Angle" else "mm"


def _value_in_unit(value_rad_or_mm, ctype):
    if ctype == "Angle":
        return float(FreeCAD.Units.Quantity(value_rad_or_mm, "rad").getValueAs("deg"))
    return float(value_rad_or_mm)


def _describe(c, index, error=None):
    d = {
        "index": index,
        "type": c.Type,
        "name": c.Name,
        "geometry": [g for g in (c.First, c.Second, c.Third) if g != -2000],
    }
    if c.Type in _DATUM_TYPES:
        d["value"] = _value_in_unit(c.Value, c.Type)
        d["unit"] = _unit_of(c.Type)
        d["driving"] = bool(c.Driving)
    if error is not None:
        d["drawing_error"] = error
    return d


def _fix_text(ops, desc):
    parts = []
    for o in ops:
        d = desc[o["index"]]
        label = "%s %s" % (d["type"], repr(d["name"]) if d["name"] else "#%d" % o["index"])
        if o["op"] == "delete":
            parts.append("Delete %s" % label)
        elif o["op"] == "set_driving":
            parts.append("Make %s non-driving (reference)" % label)
        else:
            parts.append(
                "Change %s from %.6g to %.6g %s"
                % (label, d.get("value", 0.0), o["value"], o.get("unit", ""))
            )
    return "; ".join(parts)


# Sketches up to this many constraints get every offered fix re-applied to a
# fresh copy with the real operations; larger ones are tested by deactivating
# the constraints on the working copy and only the combined plan is re-applied.
_CONFIRM_ALL_LIMIT = 300
# Datum-change alternatives need a fresh copy each; skipped on large sketches.
_ALTERNATIVES_LIMIT = 300


class _Diagnoser:
    def __init__(self, sketch, max_sets, max_fixes, time_budget, with_fixes):
        self.user = sketch
        self.max_sets = max_sets
        self.max_fixes = max_fixes
        self.deadline = time.time() + time_budget
        self.with_fixes = with_fixes
        self.copies = 0
        self.not_offered = []
        self.readers = (set(), set())
        self.names = []

    # -- helpers ----------------------------------------------------------
    def _copy(self):
        self.copies += 1
        return self.scratch.copy()

    def _note(self, ops, reason):
        self.not_offered.append({"ops": [dict(o) for o in ops], "reason": reason, "reason_text": reason_text(reason)})

    def _reader_block(self, ops):
        """The reason code if another object reads what these operations delete, shift or re-value."""
        names, indices = self.readers
        if not names and not indices:
            return None
        for o in ops:
            i = o["index"]
            if i < len(self.names) and self.names[i] and self.names[i] in names:
                return "read_by_another_object"
            if o["op"] == "delete":
                if any(j >= i for j in indices):
                    return "read_by_another_object"  # a delete shifts every later index
            elif i in indices:
                return "read_by_another_object"
        return None

    def _verified_sets(self, found):
        """Re-verify every found set on fresh copies.  A set that fails is
        shrunk again against the fresh predicate (or dropped if it does not
        conflict fresh), supersets of other sets are dropped, and if anything
        changed the sets are checked to cover the sketch: with one member of
        each set removed no conflict may remain, else the missing set is
        searched for.  Returns (sets, changed)."""
        probe = self.probe
        tester = _FreshTester(self)
        fresh = tester.conflicts
        out = []
        changed = False
        for members in found:
            members = sorted(members)
            if _verify_minimal(tester, members):
                out.append(members)
                continue
            changed = True
            tester.open_group()
            if fresh(members):
                out.append(sorted(_shrink(members, fresh)))
            tester.close_group()
        out = [m for m in out if not any(o != m and set(o) < set(m) for o in out)]
        out = [list(m) for m in sorted({tuple(m) for m in out})]
        self._covered = True
        if changed:
            for _ in range(self.max_sets):
                hitting = set()
                for m in sorted(out, key=len):
                    if not (set(m) & hitting):
                        hitting.add(max(m))
                rest = [i for i in probe.candidates if i not in hitting]
                tester.open_group()
                if not fresh(rest):
                    tester.close_group()
                    break
                if time.time() > self.deadline or len(out) >= self.max_sets:
                    tester.close_group()
                    self._covered = False
                    break
                new = sorted(_shrink(rest, fresh))
                tester.close_group()
                if new in out:
                    self._covered = False
                    break
                out.append(new)
            out.sort()
        return out, changed

    def _drawing_errors(self, probe):
        """Relative error of every candidate at the drawn (not yet solved)
        geometry.  Must run before the first solve of the working copy."""
        sk = probe.sk
        span = 1.0
        for sig in _geometry_signature(sk):
            for v in sig:
                span = max(span, abs(v))
        out = {}
        for i in probe.candidates:
            _tick("errors")
            try:
                err = abs(float(sk.calculateConstraintError(i)))
            except Exception:
                err = 0.0
            c = probe.cons[i]
            if c.Type in _DATUM_TYPES:
                ref = abs(float(c.Value)) if c.Type != "Angle" else 1.0
                out[i] = err / max(ref, 1e-3)
            else:
                out[i] = err / span
        return out

    def _forced_value(self, probe, members, m):
        """Value the rest of the set forces onto datum constraint m (or None)."""
        probe.activate_only([x for x in members if x != m])
        st = probe.status()
        if st.code not in (0, -2) or st.has_conflict:
            return None
        try:
            err = float(probe.sk.calculateConstraintError(m))
        except Exception:
            return None
        c = probe.cons[m]
        value = float(c.Value) + err
        if c.Type in ("Distance", "Radius", "Diameter", "Weight") and value <= 0:
            return None
        return _value_in_unit(value, c.Type)

    def _report(self, st, keep_map=None):
        keep_map = keep_map or list(range(self.n))
        return {
            "conflicts": sorted(keep_map[i] for i in st.conflicts if i < len(keep_map)),
            "redundancy": sorted(keep_map[i] for i in st.redundancy if i < len(keep_map)),
            "dof": st.dof,
            "code": st.code,
        }

    def _try_ops(self, ops, settle=True):
        """Apply the real operations to a fresh copy and re-solve it.  When the
        sketch has expressions the copy is also recomputed, and the fix is
        rejected (``None`` and a reason) if the recompute changes any datum,
        driving state or expression: a fix that a recompute undoes would be
        certified and then lost.  ``settle`` is False for the plan, which only
        reports."""
        sk = self._copy()
        try:
            try:
                _apply_ops(sk, ops)
                if settle:
                    if _recompute_changes(sk):
                        self._note(ops, "recompute_changes_result")
                        return None, "a recompute changes the result"
            except Exception as exc:  # solver rejected the change
                return None, repr(exc)
            probe = _Probe(sk)
            st = probe.status()
            deleted = {o["index"] for o in ops if o["op"] == "delete"}
            keep_map = [i for i in range(self.n) if i not in deleted]
            res = self._report(st, keep_map)
            if st.only_redundant and all(o["op"] in ("delete", "set_driving") for o in ops):
                groups, _ = _redundancy_groups(probe, probe.candidates, self.deadline, 4)
                res["closure"] = [
                    ([keep_map[i] for i in g["members"]], keep_map[g["suggested_removal"]])
                    for g in groups
                ]
            return res, None
        finally:
            _discard(sk)

    def _trial(self, ops):
        """Test deletions and non-driving changes on the working copy by
        deactivating the constraints: the solver treats an inactive, a deleted
        and a reference constraint alike."""
        probe = self.probe
        off = {o["index"] for o in ops}
        keep = [i for i in probe.candidates if i not in off]
        st = probe.check(keep)
        res = self._report(st)
        if st.only_redundant:
            groups, _ = _redundancy_groups(probe, keep, self.deadline, 4)
            res["closure"] = [(g["members"], g["suggested_removal"]) for g in groups]
        probe.activate_only(probe.candidates)
        return res

    # -- main -------------------------------------------------------------
    def run(self):
        t0 = time.time()
        self.scratch = _Scratch(self.user)
        try:
            return self._run(t0)
        finally:
            self.scratch.close()

    def _run(self, t0):
        self.has_expr = bool(self.user.ExpressionEngine)
        work = self._copy()
        probe = _Probe(work)
        self.probe = probe
        self.n = probe.n
        self.errors = self._drawing_errors(probe)
        base = probe.status()
        self.base = base
        self.names = [c.Name for c in probe.cons]
        self.readers = _external_readers(self.user)
        # constraints bound by an expression the copy could not carry
        self.unbound = set()
        for path in list(self.scratch.dropped_expressions) + _external_bindings(self.user):
            for i, nm in enumerate(self.names):
                if (nm and path == "Constraints." + nm) or path == "Constraints[%d]" % i:
                    self.unbound.add(i)
        result = {
            "sketch": self.user.Name,
            "constraint_count": probe.n,
            "geometry_count": len(work.Geometry),
            "dof": base.dof,
            "solve_code": base.code,
            "status": _STATUS_TEXT.get(base.code, "unknown"),
            "solver_conflicting": sorted(base.conflicts),
            "solver_redundant": sorted(base.redundant),
            "solver_partially_redundant": sorted(base.partial),
            "solver_malformed": sorted(base.malformed),
            "reference_constraints": [
                i for i in range(probe.n) if not probe.driving[i] and probe.types[i] in _DATUM_TYPES
            ],
            "inactive_constraints": probe.inactive,
            "candidate_constraints": list(probe.candidates),
            "conflict_sets": [],
            "redundancy_groups": [],
            "not_offered": self.not_offered,
            "complete": True,
        }
        sets = []
        if base.has_conflict:
            found, complete = _enumerate_conflict_sets(probe, self.max_sets, self.deadline)
            result["complete"] = complete
            sets, changed = self._verified_sets(found)
            if changed:
                result["complete"] = result["complete"] and self._covered
        sets.sort()
        self.conflict_union = set().union(*sets) if sets else set()

        # Redundancy on the sketch with one hitting set of the conflicts removed.
        hitting = set()
        for members in sorted(sets, key=len):
            if not (set(members) & hitting):
                hitting.add(max(members))
        base_active = [i for i in probe.candidates if i not in hitting]
        first = []
        groups, _ = _redundancy_groups(probe, base_active, self.deadline, first_code=first)
        # The code the solver returns for the redundancy that was already there
        # (a redundant sketch can come back as -1, not only -2): a fix that
        # leaves only that redundancy behind is judged against it.
        self.base_red_code = first[0] if (first and groups) else None
        result["redundancy_groups"] = groups
        self.n_groups = len(groups)
        result["redundancy_assumes_conflicts_resolved"] = bool(sets)
        # redundancy that was already there does not count as caused by a fix
        self.base_red = {i for g in groups for i in g["members"]}
        result["redundancy_members"] = sorted(self.base_red)

        self.desc = {}

        def desc(i):
            if i not in self.desc:
                self.desc[i] = _describe(probe.cons[i], i, self.errors.get(i))
            return self.desc[i]

        self.describe = desc
        for g in groups:
            g["constraints"] = [desc(i) for i in g["members"]]
            g["suggested_removal_constraint"] = desc(g["suggested_removal"])
            g["fix"] = self._removal_fix(g, hitting) if self.with_fixes else None
        probe.activate_only(probe.candidates)
        for k, members in enumerate(sets):
            result["conflict_sets"].append(
                {
                    "id": k,
                    "members": members,
                    "constraints": [desc(i) for i in members],
                    "minimal": True,
                    "fixes": [],
                    "alternatives": [],
                }
            )
        if self.with_fixes and sets:
            self._fixes(sets, result)
        result["solves"] = probe.solves
        result["copies"] = self.copies
        result["seconds"] = time.time() - t0
        return result

    def _fixes(self, sets, result):
        probe = self.probe
        # facts that trials would change: which datums are bound by their own expression
        own_expr = {}
        for members in sets:
            for m in members:
                if m not in own_expr and probe.types[m] in _DATUM_TYPES:
                    own_expr[m] = _has_own_expression(probe.sk, m) if self.has_expr else False
        confirm_all = self.n <= _CONFIRM_ALL_LIMIT
        alternatives_ok = self.n <= _ALTERNATIVES_LIMIT
        by_key = {}
        noted = set()
        for k, members in enumerate(sets):
            for m in members:
                options = [[{"op": "delete", "index": m}]]
                if probe.types[m] in _DATUM_TYPES:
                    options.append([{"op": "set_driving", "index": m, "value": False}])
                    if alternatives_ok and m in self.unbound:
                        if m not in noted:
                            noted.add(m)
                            self._note([{"op": "set_datum", "index": m, "value": None}], "bound_outside_sketch")
                    elif alternatives_ok and not own_expr[m]:
                        v = self._forced_value(probe, members, m)
                        if v is not None and abs(v - self.describe(m)["value"]) > 1e-12:
                            options.append(
                                [
                                    {
                                        "op": "set_datum",
                                        "index": m,
                                        "value": v,
                                        "unit": _unit_of(probe.types[m]),
                                    }
                                ]
                            )
                for ops in options:
                    by_key.setdefault(repr(ops), (ops, []))[1].append(k)
        probe.activate_only(probe.candidates)
        tested = {}
        for key, (ops, owners) in by_key.items():
            if time.time() > self.deadline:
                result["complete"] = False
                break
            tested[key] = self._evaluate(ops, sets, confirm_all)
        for k in range(len(sets)):
            offered = []
            for key, (ops, owners) in by_key.items():
                fx = tested.get(key)
                if k not in owners or fx is None or k not in fx["resolves"]:
                    continue
                m = ops[0]["index"]
                rank_key = (
                    -len(fx["resolves"]),
                    len(fx["ops"]),
                    -self.errors.get(m, 0.0),
                    _KIND_RANK[ops[0]["op"]],
                    -m,
                )
                offered.append((rank_key, fx))
            offered.sort(key=lambda t: t[0])
            clean = [fx for _, fx in offered if not fx["leaves_redundancy"]]
            alternatives = [fx for _, fx in offered if fx["leaves_redundancy"]]
            for name, group in (("fixes", clean), ("alternatives", alternatives)):
                out = []
                for rank, fx in enumerate(group[: self.max_fixes], 1):
                    item = dict(fx)
                    item["rank"] = rank
                    item["resolves"] = list(fx["resolves"])
                    out.append(item)
                result["conflict_sets"][k][name] = out
        # combined plan: the top fix of every conflict set, applied together to a fresh copy
        plan_ops, seen = [], set()
        for cs in result["conflict_sets"]:
            if cs["fixes"]:
                for o in cs["fixes"][0]["ops"]:
                    if repr(o) not in seen:
                        seen.add(repr(o))
                        plan_ops.append(o)
        if plan_ops:
            res, err = self._try_ops(plan_ops, settle=False)
            result["plan"] = {
                "ops": plan_ops,
                "description": _fix_text(plan_ops, {o["index"]: self.describe(o["index"]) for o in plan_ops}),
                "dof_after": res["dof"] if res else None,
                "clean": bool(
                    res
                    and not res["conflicts"]
                    and not [i for i in res["redundancy"] if i not in self.base_red]
                ),
            }

    def _removal_fix(self, group, hitting):
        """The suggested removal of a redundancy group as an ``apply_fix``
        dict, or None with the reason in ``group["no_fix_reason"]``.  It is
        offered only after it has been applied, with the real operation, to a
        fresh copy of the sketch as it is (the conflicts, if any, in place) and
        the copy recomputed and solved: no conflict beyond the sketch's own,
        no redundancy that was not there, strictly less redundancy than before
        (and, for a group that overlaps no other, none of its members left),
        and the degrees of freedom the sketch had.  ``dof_after`` is stated
        only when no conflict is left."""

        def no(reason):
            group["no_fix_reason"] = reason
            group["no_fix_text"] = reason_text(reason)
            return None

        if time.time() > self.deadline:
            return no("out_of_time")
        if group.get("dof_after") is None:
            return no("no_safe_member")
        m = group["suggested_removal"]
        ops = [{"op": "delete", "index": m}]
        blocked = self._reader_block(ops)
        if blocked:
            self._note(ops, blocked)
            return no(blocked)
        sk = self._copy()
        try:
            try:
                _apply_ops(sk, ops)
                if _recompute_changes(sk):
                    self._note(ops, "recompute_changes_result")
                    return no("recompute_changes_result")
            except Exception:
                return no("solver_rejected")
            st = _Probe(sk).status()
            keep_map = [i for i in range(self.n) if i != m]
            left = [keep_map[i] for i in st.redundancy if i < len(keep_map)]
            conflicts = sorted(keep_map[i] for i in st.conflicts if i < len(keep_map))
            if set(conflicts) - self.conflict_union:
                return no("leaves_new_conflict")
            if not self._code_ok({"conflicts": conflicts, "code": st.code, "redundancy": left}):
                return no("solver_rejected")
            if st.dof != self.base.dof:
                return no("changes_dof")
            if [i for i in left if i not in self.base_red]:
                return no("leaves_new_redundancy")
            if not conflicts:
                probe_after = _Probe(sk)
                after_groups, _ = _redundancy_groups(probe_after, probe_after.candidates, self.deadline)
                if len(after_groups) >= self.n_groups:
                    return no("does_not_shrink")
            if not group.get("overlaps") and [i for i in group["members"] if i != m and i in left]:
                return no("does_not_shrink")
        finally:
            _discard(sk)
        return {
            "ops": ops,
            "kind": "delete",
            "constraint": m,
            "description": _fix_text(ops, {m: self.describe(m)}),
            "resolves": [],
            "remaining_conflicts": conflicts,
            "dof_after": None if conflicts else st.dof,
            "leaves_redundancy": [],
            "verified": True,
            "applied_to_fresh_copy": True,
        }

    def _code_ok(self, res):
        """The solver accepts the result: solved or redundant, or the code the
        sketch already returned for its own pre-existing redundancy, with no
        redundancy beyond it."""
        if res["conflicts"] or res["code"] in (0, -2):
            return True
        return (
            self.base_red_code is not None
            and res["code"] == self.base_red_code
            and bool(res["redundancy"])
            and all(i in self.base_red for i in res["redundancy"])
        )

    def _evaluate(self, ops, sets, confirm):
        """Test one fix; return the fix dict or None when it does not work."""
        datum = ops[0]["op"] == "set_datum"
        if datum:
            res, err = self._try_ops(ops)
        else:
            res = self._trial(ops)
        if res is None:
            return None
        ops = list(ops)
        if not self._code_ok(res):
            return None
        fresh = [i for i in res["redundancy"] if i not in self.base_red]
        if fresh and not res["conflicts"] and res.get("closure") and not datum:
            extra = [r for members, r in res["closure"] if set(members) & set(fresh)]
            ops = ops + [{"op": "delete", "index": i} for i in extra]
            res = self._trial(ops)
            if not self._code_ok(res) or res["conflicts"]:
                return None
        touched = {o["index"] for o in ops}
        resolves, unresolved = [], []
        for k, members in enumerate(sets):
            if not (set(members) & touched):
                unresolved.append(k)
            elif datum and set(res["conflicts"]) >= set(members):
                unresolved.append(k)
            else:
                resolves.append(k)
        allowed = set()
        for k in unresolved:
            allowed |= set(sets[k])
        if set(res["conflicts"]) - allowed:
            return None
        leaves = [i for i in res["redundancy"] if i not in self.base_red]
        if leaves and not datum:
            return None
        if not res["conflicts"] and unresolved:
            return None
        blocked = self._reader_block(ops)
        if blocked:
            self._note(ops, blocked)
            return None
        confirmed = datum
        if (confirm or self.has_expr) and not datum:
            real, err = self._try_ops(ops)
            if (
                real is None
                or not self._code_ok(real)  # the code the recompute leaves, not only the one the trial gave
                or real["conflicts"] != res["conflicts"]
                or [i for i in real["redundancy"] if i not in self.base_red]
                or real["dof"] != res["dof"]
            ):
                if real is not None:
                    # settled by a recompute, the fix does not give what the trial showed
                    self._note(ops, "result_not_reproduced")
                return None
            confirmed = True
        return {
            "ops": ops,
            "kind": ops[0]["op"],
            "constraint": ops[0]["index"],
            "description": _fix_text(ops, {o["index"]: self.describe(o["index"]) for o in ops}),
            "resolves": resolves,
            "remaining_conflicts": sorted(res["conflicts"]),
            # with a conflict left the solver's count is not the sketch's: not stated
            "dof_after": None if res["conflicts"] else res["dof"],
            "leaves_redundancy": leaves,
            "verified": True,
            "applied_to_fresh_copy": confirmed,
        }


def diagnose(sketch, max_sets=12, max_fixes=6, time_budget=120.0, with_fixes=True, progress=None):
    """Diagnose a sketch and return a JSON-serialisable dict.

    Keys: ``dof``, ``status``, ``solve_code``, the solver's own lists
    (``solver_conflicting``, ``solver_redundant``, ...), ``reference_constraints``
    and ``inactive_constraints``, ``conflict_sets`` (each with ``members``,
    ``constraints``, ranked solver-tested ``fixes`` and ``alternatives`` that
    resolve the conflict but leave a redundancy), ``redundancy_groups`` (each
    with ``members``, ``suggested_removal`` and ``fix``: the removal as an
    ``apply_fix``-compatible dict, offered only after it was applied to a copy
    and solved, or ``None``), ``not_offered`` (fixes that passed the solver
    but were dropped because a recompute would undo them, each with its
    ``ops`` and a ``reason``), ``plan`` (the top fix of every
    set applied together to a fresh copy) and ``complete``.  All constraint
    indices are 0-based.

    ``progress`` is an optional callable.  It is called on the calling thread
    before every solver call, copy and long loop step with a dict (``phase``
    and a running ``ticks`` count) and may keep the interface alive (process
    events) in the call.  If it returns a true value the diagnosis stops and
    raises ``DiagnosisCancelled`` after closing its scratch document.  An
    exception raised by the callback propagates the same way.  Without a
    callback nothing changes.
    """
    global _progress, _progress_ticks
    if getattr(sketch, "TypeId", None) != "Sketcher::SketchObject":
        raise DiagnosisError("diagnose() needs a Sketcher::SketchObject")
    outer = (_progress, _progress_ticks)
    _progress, _progress_ticks = progress, 0
    try:
        return _Diagnoser(sketch, max_sets, max_fixes, time_budget, with_fixes).run()
    finally:
        _progress, _progress_ticks = outer
