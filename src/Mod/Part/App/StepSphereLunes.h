// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2026 FreeCAD fork contributors                          *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public           *
 *   License as published by the Free Software Foundation; either          *
 *   version 2 of the License, or (at your option) any later version.      *
 *                                                                         *
 *   This library  is distributed in the hope that it will be useful,      *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
 *   GNU Library General Public License for more details.                  *
 *                                                                         *
 *   You should have received a copy of the GNU Library General Public     *
 *   License along with this library; see the file COPYING.LIB. If not,    *
 *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
 *   Suite 330, Boston, MA  02111-1307, USA                                *
 *                                                                         *
 ***************************************************************************/

#pragma once

#include <Mod/Part/PartGlobal.h>
#include <TopoDS_Face.hxx>
#include <TopoDS_Shape.hxx>

class BRepTools_Modifier;

namespace Part
{

/**
 * Sphere lunes written as spheres.
 *
 * A round-corner sweep of a circle leaves a face that is a Geom_SurfaceOfRevolution of that
 * circle about a line through its centre, lying in the circle's plane: a part of a sphere with two
 * poles. STEP carries it as SURFACE_OF_REVOLUTION (ISO 10303-42) and the file keeps the two
 * meridians but not the degenerate pole edges, so OCCT's reader builds the complementary lune.
 * The same face on a SPHERICAL_SURFACE reads back exactly. This class finds such faces and
 * converts only those to Geom_SphericalSurface (OCCT ShapeCustom_SweptToElementary, restricted),
 * on a copy: callers hand the copy to the STEP writer and leave the document's shape alone.
 */
class PartExport StepSphereLunes
{
public:
    /// true when the face is a surface of revolution of a circle about a line through its
    /// centre that lies in the circle's plane (a part of a sphere)
    static bool isSphereLune(const TopoDS_Face& face);

    /// true when any face of the shape is a sphere lune
    static bool hasSphereLune(const TopoDS_Shape& shape);

    /// Performs the conversion into the modifier (Init and Perform are done here). Returns false
    /// and leaves the modifier untouched when the shape has no such face.
    static bool convert(const TopoDS_Shape& shape, BRepTools_Modifier& modifier);

    /// The shape to hand to a STEP writer: a converted copy, or the shape itself (same
    /// TShape) when no face needs the change.
    static TopoDS_Shape forWriting(const TopoDS_Shape& shape);
};

}  // namespace Part
