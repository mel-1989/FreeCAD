# SPDX-License-Identifier: LGPL-2.1-or-later
from typing import Any, TypedDict

import FreeCAD

from .spec import StudySpecError as StudySpecError
from .spec import parse_quantity as parse_quantity
from .spec import parse_spec as parse_spec

class Warning_(TypedDict):
    code: str
    text: str

class Level(TypedDict):
    h: float
    nodes: int
    elements: int
    max_displacement: float
    energy: float
    max_von_mises: float
    reaction_sum: list[float]
    applied_sum: list[float]
    solve_seconds: float
    result: str
    rf_total: list[float]
    load_on_constrained: list[float]
    delivered_sum: list[float]

class StudyResult(TypedDict):
    verdict: str
    code: str
    reason: str
    units: dict[str, str]
    levels: list[Level]
    extrapolated: dict[str, float] | None
    order: float | None
    estimated_error: float | None
    warnings: list[Warning_]
    analysis: str
    stale: bool

def define_static(doc: FreeCAD.Document, spec: dict[str, Any]) -> str: ...
def run_static(doc: FreeCAD.Document, spec_or_name: dict[str, Any] | str) -> StudyResult: ...
def is_stale(doc: FreeCAD.Document, name: str) -> bool: ...
def stored_result(doc: FreeCAD.Document, name: str) -> StudyResult | None: ...
