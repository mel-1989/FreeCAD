// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Sketch3D relations for the PlaneGCS system (cad-fork G10 slice 6a).   *
 *                                                                         *
 *   Each row of a relation is one GCS::Constraint. A relation with more   *
 *   than one row is several Rel objects that share one tag.               *
 *                                                                         *
 *   Equations come from textbook vector geometry (dot and cross products, *
 *   distances of points to lines and planes); the public descriptions of  *
 *   3D sketch relations only fix which relations exist.                   *
 ***************************************************************************/

#pragma once

#include <string>
#include <vector>

#include <Mod/Sketcher/App/planegcs/Constraints.h>

#include "Sketch3DGlobal.h"

namespace Sketch3D
{

enum class Kind
{
    FixedCoordinate,
    Coincident,
    PointOnLine,
    PointOnPlane,
    DistancePP,
    DistancePL,
    DistancePPlane,
    Midpoint,
    Length,
    EqualLength,
    AlongX,
    AlongY,
    AlongZ,
    Parallel,
    Perpendicular,
    ParallelPlane,
    NormalPlane,
    Angle,
    Count
};

// Operand letters: p point, l line, P reference plane, a axis (0, 1, 2).
struct KindInfo
{
    const char* name;
    const char* operands;
    bool hasValue;
    int rows;  // rows added on a generic configuration
};

const KindInfo& kindInfo(Kind kind);
bool kindFromName(const std::string& name, Kind& kind);

struct PlaneData
{
    double p[3];
    double n[3];  // unit normal
};

class Rel: public GCS::Constraint
{
public:
    // params: the coordinates the relation reads, three per point in the order of its operands
    // (a line contributes its two end points).
    Rel(Kind kind,
        int row,
        int axis,
        double value,
        const PlaneData& plane,
        const std::vector<double*>& params,
        int tag);

    double error() override;
    double grad(double* param) override;

    // value of this row at the current parameters, without the constraint's scale
    double rawError();
    // Jacobian builds seen by this row (a build is a run of grad calls after an error call)
    int jacobianBuilds() const
    {
        return builds;
    }
    int rowIndex() const
    {
        return row;
    }
    Kind kind() const
    {
        return kindValue;
    }
    const GCS::VEC_pD& allParams() const
    {
        return origpvec;
    }

private:
    Kind kindValue;
    int row;
    int axis;
    double value;
    PlaneData plane;
    int builds = 0;
    bool lastWasGrad = false;
};

}  // namespace Sketch3D
