// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2026 cad-fork contributors                              *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public           *
 *   License as published by the Free Software Foundation; either          *
 *   version 2 of the License, or (at your option) any later version.      *
 *                                                                         *
 ***************************************************************************/

//! An in-memory, process-wide cache of the hidden-line (HLR) result of a TechDraw view.
//!
//! Stock TechDraw reruns the whole projection of a view whenever any property of the view is
//! touched.  The result of the projection is a pure function of the shapes the view sees and of
//! a short list of properties and preferences.  This cache keys the result on exactly those
//! inputs (see HlrCacheKey) and hands out deep copies, so a hit reproduces what a miss computes.
//!
//! Public sources: OCCT reference documentation for TopoDS_Shape, TopLoc_Location, gp_Trsf and
//! HLRBRep_Algo; FreeCAD's DrawViewPart.cpp, GeometryObject.cpp and ShapeExtractor.cpp.

#pragma once

#include <array>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include <TopoDS_Shape.hxx>

#include <Base/Vector3D.h>

#include <Mod/TechDraw/TechDrawGlobal.h>

#include "Geometry.h"
#include "GeometryObject.h"

namespace App
{
class Document;
}

namespace TechDraw
{
class DrawViewPart;

//! The exact and complete cache key (ruling 128 of the G04 brief).
class TechDrawExport HlrCacheKey
{
public:
    // identity of the source shape the view projects.  ShapeExtractor deep-copies the shape of
    // every App::Link on each call (BRepBuilderAPI_Copy), so a leaf TShape pointer never repeats
    // for a link; the content digest below is what identifies the source.  It is the SHA-256 of
    // the binary BRep of the located source compound (raw doubles, locations, orientations,
    // tolerances, no triangulation), computed on every lookup, so an in-place mutation of a
    // shape cannot give a stale hit.
    std::array<std::uint8_t, 32> digest {};
    std::uint64_t leafCount {0};
    // cheap fingerprint of the same compound: vertex count, coordinate sum and tolerance sum
    std::uint64_t vertexCount {0};
    double coordSumX {0.0};
    double coordSumY {0.0};
    double coordSumZ {0.0};
    double tolSum {0.0};
    // the projection coordinate system, Ax2: location, main direction, x direction
    std::array<double, 9> cs {};
    double scale {0.0};
    double rotation {0.0};
    int isoCount {0};
    bool perspective {false};
    double focus {0.0};
    std::array<bool, 7> classFlags {};  //!< Smooth/Seam/Iso visible, Hard/Smooth/Seam/Iso hidden
    int scrubCount {0};
    // preferences read on the path at execute time
    bool handleFaces {false};
    bool identifyVoids {false};
    int faceFinderVersion {0};
    int faceFinderPref {0};
    bool newFaceFinderPref {false};
    bool checkShapes {false};
    int scrubPref {0};
    bool allowCrazyEdge {false};

    std::uint64_t hash() const;
    bool operator==(const HlrCacheKey& other) const;
};
using HlrCacheKeyPtr = std::shared_ptr<const HlrCacheKey>;

//! What a lookup returns: a private deep copy the caller may mutate freely.
struct HlrCacheHit
{
    GeometryObjectPtr geometry;       //!< HLR edges and vertices, and the faces when hasFaces
    TopoDS_Shape saveShape;           //!< the centred copy of the source (m_saveShape)
    Base::Vector3d saveCentroid;      //!< centroid before centring (m_saveCentroid)
    bool hasFaces {false};            //!< faces are present in geometry (face finding is done)
};

struct HlrCacheStats
{
    std::uint64_t hits {0};
    std::uint64_t misses {0};
    std::uint64_t stores {0};
    std::uint64_t evictions {0};
    std::uint64_t entries {0};
    std::uint64_t bytes {0};
    std::uint64_t capBytes {0};
};

class TechDrawExport HlrCache
{
public:
    //! false when CADFORK_HLR_CACHE=0 is in the environment
    static bool enabled();

    //! true when this view may use the cache: enabled, a plain DrawViewPart or
    //! DrawProjGroupItem, no CoarseView, no Python getExplodedShape hook on a source.
    static bool eligible(const DrawViewPart& view);

    //! build the key for a view and the source shape the view is about to project
    static HlrCacheKeyPtr makeKey(const DrawViewPart& view, const TopoDS_Shape& source);

    //! on a hit, fills hit with deep copies and counts a hit; otherwise counts a miss
    static bool lookup(DrawViewPart& view, const HlrCacheKeyPtr& key, HlrCacheHit& hit);

    //! store the HLR geometry of a view (before cosmetic geometry is added to it)
    static void storeGeometry(const DrawViewPart& view, const HlrCacheKeyPtr& key,
                              const GeometryObject& geometry, const TopoDS_Shape& saveShape,
                              const Base::Vector3d& saveCentroid);

    //! add the faces found for the stored geometry to its entry
    static void attachFaces(const DrawViewPart& view, const HlrCacheKeyPtr& key,
                            const GeometryObject& geometry);

    static void dropView(const DrawViewPart* view);
    static void dropDocument(const App::Document* doc);
    static void clear();
    static HlrCacheStats stats();
};

}  // namespace TechDraw
