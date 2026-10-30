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

// Algorithm: ray casting with IntCurvesFace_ShapeIntersector (OCCT, "Modeling
// Algorithms": line/curve to face intersection) and in-domain tests with
// BRepTopAdaptor_FClass2d (OCCT face classifier). No third-party code.

#include <algorithm>
#include <chrono>
#include <cmath>
#include <vector>

#include <BRepAdaptor_Surface.hxx>
#include <BRepBndLib.hxx>
#include <BRep_Tool.hxx>
#include <BRepTools.hxx>
#include <BRepTopAdaptor_FClass2d.hxx>
#include <Bnd_Box.hxx>
#include <IntCurvesFace_ShapeIntersector.hxx>
#include <Precision.hxx>
#include <Standard_Failure.hxx>
#include <Standard_NullObject.hxx>
#include <TopAbs.hxx>
#include <TopExp.hxx>
#include <TopExp_Explorer.hxx>
#include <TopTools_IndexedMapOfShape.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Face.hxx>
#include <gp_Dir.hxx>
#include <gp_Lin.hxx>
#include <gp_Pnt2d.hxx>

#include "Tools.h"
#include "WallThickness.h"

namespace Part
{

namespace
{

constexpr int maxSamples = 1000;

// Returns false in `expired` when the deadline passed while building the grid.
template<typename Expired>
std::vector<gp_Pnt2d> faceSamples(const TopoDS_Face& face, int n, Expired expired, bool& timedOut)
{
    std::vector<gp_Pnt2d> pts;
    double u0 = 0, u1 = 0, v0 = 0, v1 = 0;
    BRepTools::UVBounds(face, u0, u1, v0, v1);
    BRepTopAdaptor_FClass2d classifier(face, Precision::Confusion());
    for (int grid : {n, std::max(3 * n, 15)}) {
        for (int i = 0; i < grid; ++i) {
            if (expired()) {
                timedOut = true;
                return {};
            }
            for (int j = 0; j < grid; ++j) {
                double u = u0 + (u1 - u0) * (i + 0.5) / grid;
                double v = v0 + (v1 - v0) * (j + 0.5) / grid;
                TopAbs_State st = classifier.Perform(gp_Pnt2d(u, v));
                if (st == TopAbs_IN || st == TopAbs_ON) {
                    pts.emplace_back(u, v);
                }
            }
        }
        if (!pts.empty()) {
            break;
        }
    }
    return pts;
}

}  // namespace

WallThicknessResult minWallThickness(const TopoDS_Shape& shape, int samples, double timeBudgetS)
{
    if (shape.IsNull()) {
        throw Standard_NullObject("Cannot measure wall thickness of a null shape");
    }
    using Clock = std::chrono::steady_clock;
    const auto t0 = Clock::now();
    samples = std::clamp(samples, 1, maxSamples);
    // timeBudgetS: NaN or negative = unlimited, 0 = stop at once, > 0 = seconds.
    const bool limited = !std::isnan(timeBudgetS) && timeBudgetS >= 0.0;
    auto expired = [&]() {
        return limited && std::chrono::duration<double>(Clock::now() - t0).count() > timeBudgetS;
    };

    WallThicknessResult best;
    Bnd_Box box;
    BRepBndLib::Add(shape, box);
    if (box.IsVoid()) {
        return best;
    }
    double x0, y0, z0, x1, y1, z1;
    box.Get(x0, y0, z0, x1, y1, z1);
    const double diag = std::max(gp_Pnt(x0, y0, z0).Distance(gp_Pnt(x1, y1, z1)), 1.0);
    const double reach = diag * 1.01;
    // The start face is skipped by identity, never by distance: the ray starts at w == lift
    // above the sample and meets the start face there again. Only that one hit (same face,
    // at w == lift within the intersector's accuracy) is dropped; a wall of any thickness
    // behind it is a different face (or, on a curved face, a hit far from w == lift).
    const double startTol = 1e-9 * std::max(1.0, diag);
    const double lift = 1e-3 * std::max(1.0, diag);

    TopTools_IndexedMapOfShape allFaces;
    TopExp::MapShapes(shape, TopAbs_FACE, allFaces);

    for (TopExp_Explorer se(shape, TopAbs_SOLID); se.More() && best.complete; se.Next()) {
        const TopoDS_Shape& solid = se.Current();
        IntCurvesFace_ShapeIntersector inter;
        try {
            inter.Load(solid, Precision::Confusion());
        }
        catch (Standard_Failure&) {
            continue;
        }
        TopTools_IndexedMapOfShape solidFaces;
        TopExp::MapShapes(solid, TopAbs_FACE, solidFaces);
        for (int fi = 1; fi <= solidFaces.Extent() && best.complete; ++fi) {
            const TopoDS_Face face = TopoDS::Face(solidFaces(fi));
            std::vector<gp_Pnt2d> pts;
            BRepAdaptor_Surface surf;
            try {
                bool timedOut = false;
                pts = faceSamples(face, samples, expired, timedOut);
                if (timedOut) {
                    best.complete = false;
                    break;
                }
                surf.Initialize(face);
            }
            catch (Standard_Failure&) {
                continue;
            }
            const int gi = allFaces.FindIndex(face);
            for (const gp_Pnt2d& uv : pts) {
                if (expired()) {
                    best.complete = false;
                    break;
                }
                try {
                    gp_Pnt p = surf.Value(uv.X(), uv.Y());
                    gp_Dir n;
                    Standard_Boolean done = Standard_False;
                    // Tools::getNormal flips the natural normal for REVERSED faces, so the
                    // normal points out of the material (into the void for cavity faces).
                    Part::Tools::getNormal(face, uv.X(), uv.Y(), Precision::Confusion(), n, done);
                    if (!done) {
                        continue;
                    }
                    gp_Pnt a(p.XYZ() + n.XYZ() * lift);
                    gp_Lin ray(a, gp_Dir(-n.X(), -n.Y(), -n.Z()));
                    inter.Perform(ray, 0.0, lift + reach);
                    ++best.rays;
                    // The start hit is the same-face hit nearest to w == lift, if it lies
                    // within the face tolerance of it. Exactly one hit is excluded.
                    const double faceTol = std::max(BRep_Tool::Tolerance(face), Precision::Confusion());
                    const double sameTol = std::max(10.0 * faceTol, startTol);
                    int startHit = 0;
                    double startDist = sameTol;
                    for (int k = 1; k <= inter.NbPnt(); ++k) {
                        double d = std::abs(inter.WParameter(k) - lift);
                        if (d <= startDist && inter.Face(k).IsSame(face)) {
                            startDist = d;
                            startHit = k;
                        }
                    }
                    double nearest = -1.0;
                    for (int k = 1; k <= inter.NbPnt(); ++k) {
                        double w = inter.WParameter(k);
                        if (k != startHit && w > lift && (nearest < 0.0 || w < nearest)) {
                            nearest = w;
                        }
                    }
                    if (nearest < 0.0) {
                        continue;
                    }
                    double t = nearest - lift;
                    if (!best.found || t < best.minimum) {
                        best.found = true;
                        best.minimum = t;
                        best.point = p;
                        best.faceIndex = gi;
                    }
                }
                catch (Standard_Failure&) {
                    continue;
                }
            }
        }
    }
    return best;
}

}  // namespace Part
