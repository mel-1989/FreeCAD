// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Sketch3D relations for the PlaneGCS system (cad-fork G10 slice 6a).   *
 *                                                                         *
 *   Each row of a relation is one GCS::Constraint. A relation with more   *
 *   than one row is several Rel objects that share one tag.               *
 *                                                                         *
 *   Where each relation's equations come from (check 13). All of them are  *
 *   textbook vector geometry, derived here from the definitions:            *
 *   - norms, differences and the midpoint: Euclidean vector algebra;       *
 *   - point to line distance |(p - a) x d| / |d|: the cross-product        *
 *     formula for the distance of a point from a line;                     *
 *   - point on plane and point to plane distance n.(p - q): the Hesse      *
 *     normal form of a plane, with a unit normal;                          *
 *   - perpendicular (v.w = 0) and angle (cos t = v.w / |v||w|): the dot-   *
 *     product definition of the angle between two vectors;                 *
 *   - parallel, point on line, along an axis, normal to a plane: a vector  *
 *     is parallel to d exactly when its components on an orthonormal       *
 *     basis of the plane perpendicular to d vanish (two rows, built by     *
 *     two cross products, as in Gram-Schmidt);                             *
 *   - parallel to a plane: d.n = 0.                                        *
 *   Which relations exist (the 18 types) is the list in check 2 of the    *
 *   brief. An earlier commit says it follows public descriptions of 3D    *
 *   sketch relations without giving titles; no title is recorded in the   *
 *   tree, so none is claimed here. No commercial product's documentation, *
 *   UI text, icons or binaries were used for any equation above.          *
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

    // value of this row at the real coordinates (not the solver's working copies), unscaled
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
    double evalAt(const GCS::VEC_pD& params) const;
    Kind kindValue;
    int row;
    int axis;
    double value;
    PlaneData plane;
    int builds = 0;
    bool lastWasGrad = false;
};

}  // namespace Sketch3D
