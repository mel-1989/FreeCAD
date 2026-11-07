// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Geometric validation properties of STEP files (G13 slice 1a).         *
 *                                                                         *
 *   Behaviour follows the CAx-IF Recommended Practices for Geometric and  *
 *   Assembly Validation Properties, v4.2 (2014-10-09), sections 4.3, 4.6, *
 *   4.7 and 4.10.                                                         *
 ***************************************************************************/

#pragma once

#include <optional>
#include <string>
#include <vector>

#include <gp_Pnt.hxx>
#include <TopoDS_Shape.hxx>
#include <XSControl_WorkSession.hxx>
#include <Mod/Import/ImportGlobal.h>
#include <Base/FileInfo.h>

namespace Import
{

namespace StepValidationTolerance
{
// Ours; the practice gives none. Volume and area: relative difference.
// Centroid: distance as a fraction of the arrived shape's bounding-box diagonal.
constexpr double volumeRelative = 1e-3;
constexpr double areaRelative = 1e-3;
constexpr double centroidOfDiagonal = 1e-3;
}  // namespace StepValidationTolerance

/// Closed list, in order of file-level severity (worst first).
enum class StepVerdict
{
    failed,
    invalid_geometry,
    disagree,
    unusable_properties,
    no_properties,
    agree
};

const char* verdictName(StepVerdict v);

/// Return the worse of two verdicts (the file-level rule).
StepVerdict worseVerdict(StepVerdict a, StepVerdict b);

/// Pure comparison rules, separate from OCCT so they can be tested and mutated.
struct StepFigures
{
    std::optional<double> volume;     // mm^3
    std::optional<double> area;       // mm^2
    std::optional<gp_Pnt> centroid;   // mm
};

struct StepPartFinding
{
    std::string name;
    StepVerdict verdict {StepVerdict::failed};
    std::string reason;
    StepFigures stated;
    StepFigures measured;
    bool statedUnusable {false};    // some stated figure was zero, non-finite or in an unresolvable unit
    bool geometryValid {true};
    double diagonal {0.0};          // bounding-box diagonal of the arrived shape, mm
    std::optional<double> volumeDiff;    // measured - stated, mm^3
    std::optional<double> areaDiff;      // measured - stated, mm^2
    std::optional<double> centroidDist;  // |measured - stated|, mm
    bool volumeDisagrees {false};
    bool areaDisagrees {false};
    bool centroidDisagrees {false};
};

struct StepFileFinding
{
    StepVerdict verdict {StepVerdict::failed};
    std::string schema;
    std::string message;
    std::vector<StepPartFinding> parts;
};

/// Decide the verdict of one part from its stated and measured figures.
void judgePart(StepPartFinding& part, bool shapeFailed);

/// Fill 'measured' (and the geometry flags) from the part's own prototype shape.
void measureShape(StepPartFinding& part, const TopoDS_Shape& shape);

/// Judge every part of a work session that has transferred a STEP file: the stated figures are
/// read from the file's own entities (not through OCCT's ReadValProps, see the run note).
void validateWorkSession(const Handle(XSControl_WorkSession)& ws, StepFileFinding& out);

/// Read a STEP file (properties on) into a scratch document and validate it.
/// Never throws: a missing or garbage file gives verdict 'failed'.
StepFileFinding validateStepFile(const Base::FileInfo& file);

/// One-line report-view text for a part that disagrees or is invalid.
std::string describeFinding(const StepPartFinding& part);

}  // namespace Import
