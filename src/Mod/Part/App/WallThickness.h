// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2026 FreeCAD Project Association                        *
 *                                                                         *
 *   This file is part of FreeCAD.                                         *
 *                                                                         *
 *   FreeCAD is free software: you can redistribute it and/or modify it    *
 *   under the terms of the GNU Lesser General Public License as           *
 *   published by the Free Software Foundation, either version 2.1 of the  *
 *   License, or (at your option) any later version.                       *
 *                                                                         *
 *   FreeCAD is distributed in the hope that it will be useful, but        *
 *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU     *
 *   Lesser General Public License for more details.                       *
 *                                                                         *
 *   You should have received a copy of the GNU Lesser General Public      *
 *   License along with FreeCAD. If not, see                               *
 *   <https://www.gnu.org/licenses/>.                                      *
 *                                                                         *
 **************************************************************************/

#pragma once

#include <TopoDS_Shape.hxx>
#include <gp_Pnt.hxx>
#include <Mod/Part/PartGlobal.h>

namespace Part
{

struct PartExport WallThicknessResult
{
    bool found = false;        // a finite minimum was measured
    double minimum = 0.0;      // minimum wall thickness (upper bound of the true minimum)
    gp_Pnt point;              // sample point on the face where the minimum was measured
    int faceIndex = 0;         // 1-based index into the shape's faces (TopExp::MapShapes order)
    int rays = 0;              // number of rays cast
    bool complete = true;      // false when the time budget stopped sampling early
};

/**
 * Minimum wall thickness by ray casting along inward face normals.
 *
 * For every solid of the shape, every face is sampled on an n x n grid of cell
 * centres in its UV bounds (n = samples; if no sample lies in the face domain a
 * grid of max(3n, 15) is used). A ray starts slightly outside each sample point
 * and runs inward; the thickness at the sample is the distance to the nearest
 * hit past the face itself on the same solid. The result is the minimum over all
 * samples, an upper bound of the true minimum (exact for plates, tubes, shells).
 * Shapes without solids give found == false and rays == 0.
 *
 * @param timeBudgetS stop sampling after this many seconds when > 0 (complete = false)
 * @throws Standard_NullObject for a null shape
 */
PartExport WallThicknessResult minWallThickness(const TopoDS_Shape& shape,
                                                int samples = 5,
                                                double timeBudgetS = -1.0);

}  // namespace Part
