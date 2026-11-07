# SPDX-License-Identifier: LGPL-2.1-or-later

"""Typed public signatures for the ``Sketch3D`` module (3D sketch relations on the PlaneGCS system)."""

from __future__ import annotations

from typing import Any, Sequence, TypedDict

class SpecError(ValueError):
    """A malformed spec. ``position`` is the index of the offending constraint, or -1."""

    position: int

class PointSpec(TypedDict, total=False):
    xyz: Sequence[float]
    fixed: bool

class PlaneSpec(TypedDict):
    point: Sequence[float]
    normal: Sequence[float]

class ConstraintSpec(TypedDict, total=False):
    type: str
    operands: Sequence[int]
    value: float
    driving: bool
    tag: int

class Spec(TypedDict, total=False):
    points: Sequence[Sequence[float] | PointSpec]
    lines: Sequence[Sequence[int]]
    planes: Sequence[PlaneSpec]
    constraints: Sequence[ConstraintSpec]
    algorithm: str
    diagnose: bool
    """False skips the diagnosis: ``dof`` is -1 and the redundancy lists are empty."""
    reference_diagnosis: bool
    """True diagnoses as the 2D sketcher does (dense QR below 1,000 unknowns, COLAMD column order above) instead of the faster route used by default from 250 unknowns; for comparing the two."""

class Result(TypedDict):
    code: int
    """0 success, 1 converged without a zero residual, 2 failed, 3 refused: a line carrying a direction relation collapsed."""
    status: str
    coordinates: list[list[float]]
    dof: int
    conflicting: list[int]
    redundant: list[int]
    partially_redundant: list[int]
    collapsed: list[int]
    """Code 3 only: tags of the direction relations on the lines that shrank to zero length."""
    reason: str
    """Why a failed solve names no conflict; empty on success."""
    residuals: list[float]
    iterations: int
    algorithm: str

def solve(spec: Spec, /) -> Result:
    """Solve a 3D sketch. Never changes ``spec``; raises ``SpecError`` on a malformed one."""
    ...

def constraint_types() -> dict[str, dict[str, Any]]:
    """Constraint type name to ``{"operands": str, "value": bool, "rows": int}``."""
    ...

def _test_rows(spec: Spec, /) -> list[dict[str, Any]]:
    """Test hook: per-row ``error`` and analytic ``grad`` at the spec's own coordinates."""
    ...
