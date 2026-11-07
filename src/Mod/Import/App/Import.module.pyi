# SPDX-License-Identifier: LGPL-2.1-or-later

"""Typed public signatures for the ``Import`` application module.

This source-adjacent stub file carries the manual import/export helpers for
OCAF-backed CAD formats together with the DXF utility surface.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, TypeAlias, TypedDict, overload

from FreeCAD import DocumentObject
from Part import Feature, Shape

_ColorRGBA: TypeAlias = tuple[float, float, float, float]
_DxfVersion: TypeAlias = Literal[12, 14]
_ImportExportObject: TypeAlias = DocumentObject | tuple[DocumentObject, Sequence[_ColorRGBA]]
_ImportColorAssignments: TypeAlias = list[tuple[Feature, list[_ColorRGBA]]]
_UnsupportedFeatureOccurrences: TypeAlias = dict[str, list[tuple[int, str]]]
_StepVerdict: TypeAlias = Literal[
    "failed", "invalid_geometry", "disagree", "unusable_properties", "no_properties", "agree"
]
_Point: TypeAlias = tuple[float, float, float]
DxfImportStats = TypedDict(
    "DxfImportStats",
    {
        "dxfVersion": str,
        "dxfEncoding": str,
        "scalingSource": str,
        "fileUnits": str,
        "finalScalingFactor": float,
        "importTimeSeconds": float,
        "totalEntitiesCreated": int,
        "entityCounts": dict[str, int],
        "importSettings": dict[str, str],
        "unsupportedFeatures": _UnsupportedFeatureOccurrences,
        "systemBlockCounts": dict[str, int],
    },
)

class _StepFigures(TypedDict):
    volume: float | None
    area: float | None
    wetted_area: float | None  # measured: area of the solids' outer shells (voids left out)
    independent_area: float | None
    centroid: _Point | None
    independent_centroid: _Point | None

class _StepDifference(TypedDict):
    volume: float | None
    area: float | None
    independent_area: float | None
    centroid: float | None
    independent_centroid: float | None

class _StepDisagrees(TypedDict):
    volume: bool
    area: bool
    independent_area: bool
    centroid: bool
    independent_centroid: bool

class StepPartFinding(TypedDict):
    name: str
    verdict: _StepVerdict
    reason: str
    stated_area_is_wetted: bool  # the stated area was named 'wetted area measure'
    stated: _StepFigures  # mm^3, mm^2, mm; None when the file does not state it
    measured: _StepFigures  # from the shape that arrived, in the part's own frame
    difference: _StepDifference  # measured - stated; the centroid entry is a distance
    diagonal: float  # bounding-box diagonal of the arrived shape, mm
    valid: bool
    disagrees: _StepDisagrees

class StepFileFinding(TypedDict):
    verdict: _StepVerdict
    schema: str
    message: str
    parts: list[StepPartFinding]

def validateStep(path: str) -> StepFileFinding:
    """Compare the geometric validation properties a STEP file states with what arrives.

    One finding per part; a missing or garbage file gives the verdict ``failed``.
    """
    ...

# OCAF-backed file I/O
def open(
    name: str,
    docName: str | None = None,
    importHidden: bool | None = None,
    merge: bool | None = None,
    useLinkGroup: bool | None = None,
    mode: int = -1,
) -> _ImportColorAssignments | None:
    """Open one STEP, IGES, or glTF file and optionally return imported face colors."""
    ...

def insert(
    name: str,
    docName: str | None = None,
    importHidden: bool | None = None,
    merge: bool | None = None,
    useLinkGroup: bool | None = None,
    mode: int = -1,
) -> _ImportColorAssignments | None:
    """Insert one STEP, IGES, or glTF file into a document and optionally return face colors."""
    ...

def export(
    obj: Sequence[_ImportExportObject],
    name: str,
    exportHidden: bool | None = None,
    legacy: bool | None = None,
    keepPlacement: bool | None = None,
) -> None:
    """Export document objects to one STEP, IGES, or glTF file."""
    ...

# DXF helpers
def readDXF(
    filename: str,
    document: str | None = None,
    ignore_errors: bool = True,
    option_source: str | None = None,
    /,
) -> DxfImportStats:
    """Import one DXF file and return detailed import statistics."""
    ...

@overload
def writeDXFShape(
    shape: Shape,
    filename: str,
    version: _DxfVersion | int = -1,
    usePolyline: bool = False,
    optionSource: str | None = None,
    /,
) -> None:
    """Export one shape to a DXF file."""
    ...

@overload
def writeDXFShape(
    shape: Sequence[Shape],
    filename: str,
    version: _DxfVersion | int = -1,
    usePolyline: bool = False,
    optionSource: str | None = None,
    /,
) -> None:
    """Export a shape sequence to a DXF file."""
    ...

@overload
def writeDXFObject(
    obj: DocumentObject,
    filename: str,
    version: _DxfVersion | int = -1,
    usePolyline: bool = False,
    optionSource: str | None = None,
    /,
) -> None:
    """Export one document object to a DXF file."""
    ...

@overload
def writeDXFObject(
    obj: Sequence[DocumentObject],
    filename: str,
    version: _DxfVersion | int = -1,
    usePolyline: bool = False,
    optionSource: str | None = None,
    /,
) -> None:
    """Export a document-object sequence to a DXF file."""
    ...
