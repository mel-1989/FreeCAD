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

#include <cmath>
#include <numbers>

#include <BRep_Tool.hxx>
#include <BRepTools.hxx>
#include <BRepTools_Modification.hxx>
#include <BRepTools_Modifier.hxx>
#include <Geom2d_Curve.hxx>
#include <Geom_Circle.hxx>
#include <Geom_Curve.hxx>
#include <Geom_RectangularTrimmedSurface.hxx>
#include <Geom_SphericalSurface.hxx>
#include <Geom_Surface.hxx>
#include <Geom_SurfaceOfRevolution.hxx>
#include <Geom_TrimmedCurve.hxx>
#include <gp_Ax3.hxx>
#include <gp_Circ.hxx>
#include <gp_Lin.hxx>
#include <gp_Ax2d.hxx>
#include <gp_Pnt2d.hxx>
#include <gp_Trsf2d.hxx>
#include <gp_Vec2d.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Edge.hxx>
#include <TopoDS_Vertex.hxx>

#include <App/Application.h>
#include <Base/Parameter.h>

#include "StepSphereLunes.h"

using namespace Part;

namespace
{

constexpr double pi = std::numbers::pi;

/// How a surface of revolution of a circle about a line through its centre maps onto the sphere
/// of the same centre and radius.
///
/// Take the axis A, the circle's normal Z and B = Z x A, so that (A, B, Z) is right handed. A
/// circle point at parameter t, with s = t - a and a the angle of A in the circle's frame, is
/// r (cos s A + sin s B), and the revolution by u about A carries B to cos u B + sin u Z. On the
/// sphere with pole A and prime meridian B this is longitude u and latitude pi/2 - s where sin s
/// is positive, and longitude u + pi and latitude s' - pi/2 (s' = s - pi) where it is negative.
/// A face whose t range stays inside one half circle therefore maps by a reflection or a
/// translation in the parameter plane, and its pcurves are transformed by the same map.
struct Lune
{
    occ::handle<Geom_SphericalSurface> sphere;
    gp_Trsf2d map;       // old (u, t) to new (longitude, latitude)
    bool reversesNormal;  // the new surface normal, relative to the old, points the other way
};

bool analyse(const TopoDS_Face& face, Lune* out)
{
    TopLoc_Location loc;
    occ::handle<Geom_Surface> surf = BRep_Tool::Surface(face, loc);
    while (!surf.IsNull()) {
        auto trimmed = occ::down_cast<Geom_RectangularTrimmedSurface>(surf);
        if (trimmed.IsNull()) {
            break;
        }
        surf = trimmed->BasisSurface();
    }
    auto rev = occ::down_cast<Geom_SurfaceOfRevolution>(surf);
    if (rev.IsNull()) {
        return false;
    }
    occ::handle<Geom_Curve> basis = rev->BasisCurve();
    while (!basis.IsNull()) {
        auto trimmed = occ::down_cast<Geom_TrimmedCurve>(basis);
        if (trimmed.IsNull()) {
            break;
        }
        basis = trimmed->BasisCurve();
    }
    auto circle = occ::down_cast<Geom_Circle>(basis);
    if (circle.IsNull()) {
        return false;
    }
    const gp_Circ c = circle->Circ();
    const gp_Ax1 axis = rev->Axis();
    const double radius = c.Radius();
    if (radius <= 0.0) {
        return false;
    }
    // the axis passes through the centre, and lies in the circle's plane. The offset is absolute:
    // measured by sweeping it (r = 5 and 100), conversion reads back exactly up to 3e-8 and is worse
    // than leaving the face alone from 1e-7 up, whatever the radius
    if (gp_Lin(axis).Distance(c.Location()) > 3e-8) {
        return false;
    }
    if (std::abs(axis.Direction().Dot(c.Axis().Direction())) > 1e-9) {
        return false;
    }
    if (out == nullptr) {
        return true;
    }

    // the face's parameter box decides the half circle
    double u0 {};
    double u1 {};
    double t0 {};
    double t1 {};
    BRepTools::UVBounds(face, u0, u1, t0, t1);
    const gp_Dir& dirA = axis.Direction();
    const double a = std::atan2(dirA.Dot(c.YAxis().Direction()), dirA.Dot(c.XAxis().Direction()));
    const double eps = 1e-6;
    const double mid = 0.5 * (t0 + t1) - a;
    const double m = std::floor(mid / pi);
    if (t0 - a < m * pi - eps || t1 - a > (m + 1.0) * pi + eps) {
        return false;  // the face spans both halves of the circle
    }
    const bool even = std::fmod(std::abs(m), 2.0) < 0.5;

    const gp_Dir dirB = c.Axis().Direction().Crossed(dirA);
    // sphere: pole A, prime meridian B, so its Y is A x B = the circle's normal
    out->sphere = new Geom_SphericalSurface(gp_Ax3(c.Location(), dirA, even ? dirB : dirB.Reversed()), radius);

    // old (u, t): s' = t - a - m pi in [0, pi]
    //   even m: longitude u, latitude pi/2 - s'    = -t + (a + m pi + pi/2)
    //   odd m:  longitude u + pi, latitude s' - pi/2 =  t - (a + m pi + pi/2)
    const double shift = a + m * pi + 0.5 * pi;
    gp_Trsf2d trsf;
    if (even) {
        trsf.SetMirror(gp_Ax2d(gp_Pnt2d(0.0, 0.5 * shift), gp_Dir2d(1.0, 0.0)));
        // a mirror about t = shift/2 sends t to shift - t
        out->map = trsf;
    }
    else {
        gp_Trsf2d move;
        move.SetTranslation(gp_Vec2d(0.0, -shift));  // the half turn is in the sphere's prime meridian
        out->map = move;
    }
    out->reversesNormal = even;
    return true;
}

/// SweptToElementary in effect, limited to the faces analyse() accepts, with the pcurve map
/// worked out from the geometry above
class SphereLuneModification: public BRepTools_Modification
{
public:
    DEFINE_STANDARD_RTTI_INLINE(SphereLuneModification, BRepTools_Modification)

    bool NewSurface(
        const TopoDS_Face& F,
        occ::handle<Geom_Surface>& S,
        TopLoc_Location& L,
        double& Tol,
        bool& RevWires,
        bool& RevFace
    ) override
    {
        Lune lune;
        if (!analyse(F, &lune)) {
            return false;
        }
        BRep_Tool::Surface(F, L);
        S = lune.sphere;
        Tol = BRep_Tool::Tolerance(F);
        RevWires = lune.reversesNormal;
        RevFace = lune.reversesNormal;
        return true;
    }

    bool NewCurve(const TopoDS_Edge&, occ::handle<Geom_Curve>&, TopLoc_Location&, double&) override
    {
        return false;  // the 3D geometry of every edge stays as it is
    }

    bool NewPoint(const TopoDS_Vertex&, gp_Pnt&, double&) override
    {
        return false;
    }

    bool NewCurve2d(
        const TopoDS_Edge& E,
        const TopoDS_Face& F,
        const TopoDS_Edge&,
        const TopoDS_Face&,
        occ::handle<Geom2d_Curve>& C,
        double& Tol
    ) override
    {
        Lune lune;
        if (!analyse(F, &lune)) {
            return false;
        }
        double first {};
        double last {};
        occ::handle<Geom2d_Curve> old = BRep_Tool::CurveOnSurface(E, F, first, last);
        if (old.IsNull()) {
            return false;
        }
        C = occ::down_cast<Geom2d_Curve>(old->Transformed(lune.map));
        Tol = BRep_Tool::Tolerance(E);
        return !C.IsNull();
    }

    bool NewParameter(const TopoDS_Vertex&, const TopoDS_Edge&, double&, double&) override
    {
        return false;
    }

    GeomAbs_Shape Continuity(
        const TopoDS_Edge&,
        const TopoDS_Face&,
        const TopoDS_Face&,
        const TopoDS_Edge& NewE,
        const TopoDS_Face& NewF1,
        const TopoDS_Face& NewF2
    ) override
    {
        return BRep_Tool::Continuity(NewE, NewF1, NewF2);
    }
};

}  // namespace

bool StepSphereLunes::isSphereLune(const TopoDS_Face& face)
{
    Lune lune;
    return analyse(face, &lune);
}

bool StepSphereLunes::hasSphereLune(const TopoDS_Shape& shape)
{
    for (TopExp_Explorer ex(shape, TopAbs_FACE); ex.More(); ex.Next()) {
        if (isSphereLune(TopoDS::Face(ex.Current()))) {
            return true;
        }
    }
    return false;
}

bool StepSphereLunes::enabled()
{
    return App::GetApplication()
        .GetUserParameter()
        .GetGroup("BaseApp")
        ->GetGroup("Preferences")
        ->GetGroup("Mod/Part")
        ->GetGroup("STEP")
        ->GetBool("WriteSphereLunes", true);
}

bool StepSphereLunes::convert(const TopoDS_Shape& shape, BRepTools_Modifier& modifier)
{
    if (shape.IsNull() || !enabled() || !hasSphereLune(shape)) {
        return false;
    }
    modifier.Init(shape);
    modifier.Perform(new SphereLuneModification());
    return modifier.IsDone();
}

TopoDS_Shape StepSphereLunes::forWriting(const TopoDS_Shape& shape)
{
    BRepTools_Modifier modifier;
    if (!convert(shape, modifier)) {
        return shape;
    }
    return modifier.ModifiedShape(shape);
}
