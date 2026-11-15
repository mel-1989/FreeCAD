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

// Public sources: OCCT reference documentation for TopoDS_Shape, TopLoc_Location, gp_Trsf,
// TopoDS_Iterator and HLRBRep_Algo; FreeCAD's DrawViewPart.cpp, GeometryObject.cpp and
// ShapeExtractor.cpp (TechDraw).

#include "PreCompiled.h"
#ifndef _PreComp_
# include <algorithm>
# include <cstdlib>
# include <cstring>
# include <iostream>
# include <list>
# include <streambuf>
# include <mutex>
#endif

#include <BRep_Tool.hxx>
#include <BinTools.hxx>
#include <BinTools_FormatVersion.hxx>
#include <TopExp_Explorer.hxx>
#include <TopLoc_Location.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Iterator.hxx>
#include <TopoDS_Vertex.hxx>
#include <gp_Pnt.hxx>
#include <gp_Trsf.hxx>

#include <QByteArray>
#include <QCryptographicHash>

#include <App/Application.h>
#include <App/Document.h>
#include <App/PropertyPythonObject.h>
#include <Base/Interpreter.h>

#include "HlrCache.h"
#include "DrawProjGroupItem.h"
#include "DrawViewDetail.h"
#include "DrawViewSection.h"
#include "DrawBrokenView.h"
#include "DrawViewMulti.h"
#include "DrawViewPart.h"
#include "Preferences.h"

using namespace TechDraw;

namespace
{

inline std::uint64_t bitsOf(double d)
{
    std::uint64_t u;
    std::memcpy(&u, &d, sizeof(u));
    return u;
}

inline bool same(double a, double b)
{
    return bitsOf(a) == bitsOf(b);
}

inline void mix(std::uint64_t& h, std::uint64_t v)
{
    h ^= v + 0x9e3779b97f4a7c15ULL + (h << 6) + (h >> 2);
}

//! a stream buffer that feeds everything written to it into a SHA-256
class HashBuf: public std::streambuf
{
public:
    HashBuf() : hash(QCryptographicHash::Sha256) {}
    std::array<std::uint8_t, 32> result()
    {
        QByteArray r = hash.result();
        std::array<std::uint8_t, 32> out {};
        std::memcpy(out.data(), r.constData(), std::min<size_t>(32, r.size()));
        return out;
    }

protected:
    std::streamsize xsputn(const char* s, std::streamsize n) override
    {
        hash.addData(QByteArrayView(s, static_cast<qsizetype>(n)));
        written += n;
        return n;
    }
    int_type overflow(int_type c) override
    {
        if (c != traits_type::eof()) {
            char ch = traits_type::to_char_type(c);
            hash.addData(QByteArrayView(&ch, 1));
            written++;
        }
        return traits_type::not_eof(c);
    }
    pos_type seekoff(off_type off, std::ios_base::seekdir dir, std::ios_base::openmode) override
    {
        if (off == 0 && dir == std::ios_base::cur) {
            return pos_type(written);
        }
        return pos_type(off_type(-1));
    }

private:
    QCryptographicHash hash;
    std::streamoff written {0};
};

void digestShape(const TopoDS_Shape& s, HlrCacheKey& key)
{
    HashBuf buf;
    std::ostream os(&buf);
    BinTools::Write(s, os, false, false, BinTools_FormatVersion_CURRENT);
    os.flush();
    key.digest = buf.result();
    for (TopoDS_Iterator it(s, false, false); it.More(); it.Next()) {
        key.leafCount++;
    }
}

// deep copies of the TechDraw geometry; the OCC shapes inside are immutable and shared
BaseGeomPtr cloneGeom(const BaseGeomPtr& g)
{
    if (!g) {
        return g;
    }
    BaseGeomPtr c;
    if (auto p = std::dynamic_pointer_cast<AOC>(g)) {
        c = std::make_shared<AOC>(*p);
    }
    else if (auto p = std::dynamic_pointer_cast<AOE>(g)) {
        c = std::make_shared<AOE>(*p);
    }
    else if (auto p = std::dynamic_pointer_cast<Circle>(g)) {
        c = std::make_shared<Circle>(*p);
    }
    else if (auto p = std::dynamic_pointer_cast<Ellipse>(g)) {
        c = std::make_shared<Ellipse>(*p);
    }
    else if (auto p = std::dynamic_pointer_cast<BezierSegment>(g)) {
        c = std::make_shared<BezierSegment>(*p);
    }
    else if (auto p = std::dynamic_pointer_cast<BSpline>(g)) {
        c = std::make_shared<BSpline>(*p);
    }
    else if (auto p = std::dynamic_pointer_cast<Generic>(g)) {
        c = std::make_shared<Generic>(*p);
    }
    else {
        c = std::make_shared<BaseGeom>(*g);
    }
    c->renewTag();
    return c;
}

BaseGeomPtrVector cloneEdges(const BaseGeomPtrVector& in)
{
    BaseGeomPtrVector out;
    out.reserve(in.size());
    for (auto& g : in) {
        out.push_back(cloneGeom(g));
    }
    return out;
}

std::vector<VertexPtr> cloneVertices(const std::vector<VertexPtr>& in)
{
    std::vector<VertexPtr> out;
    out.reserve(in.size());
    for (auto& v : in) {
        out.push_back(std::make_shared<Vertex>(v.get()));
    }
    return out;
}

std::vector<FacePtr> cloneFaces(const std::vector<FacePtr>& in)
{
    std::vector<FacePtr> out;
    out.reserve(in.size());
    for (auto& f : in) {
        auto nf = std::make_shared<Face>();
        nf->setRepresentation(f->getRepresentation());
        for (auto* w : f->wires) {
            auto* nw = new Wire();
            nw->geoms = cloneEdges(w->geoms);
            nf->wires.push_back(nw);
        }
        out.push_back(nf);
    }
    return out;
}

std::uint64_t estimateGeomBytes(const BaseGeomPtrVector& edges)
{
    std::uint64_t n = 0;
    for (auto& g : edges) {
        n += 2500;  // the TechDraw geometry plus the OCC edge it keeps
        if (auto bs = std::dynamic_pointer_cast<BSpline>(g)) {
            n += 400 * bs->segments.size();
        }
    }
    return n;
}

std::uint64_t estimateFaceBytes(const std::vector<FacePtr>& faces)
{
    std::uint64_t n = 0;
    for (auto& f : faces) {
        n += 200;
        for (auto* w : f->wires) {
            n += 2500 * w->geoms.size();
        }
    }
    return n;
}

struct Entry
{
    const DrawViewPart* view;
    const App::Document* doc;
    HlrCacheKeyPtr key;
    std::uint64_t hash;
    // the stored HLR result, never handed out, only cloned
    BaseGeomPtrVector edges;
    std::vector<VertexPtr> vertices;
    std::vector<FacePtr> faces;
    bool hasFaces {false};
    std::array<TopoDS_Shape, 10> compounds;
    TopoDS_Shape saveShape;
    Base::Vector3d saveCentroid;
    std::uint64_t bytes {0};
};

struct State
{
    std::mutex mutex;
    std::list<Entry> lru;  // most recently used first
    HlrCacheStats stats;
    bool connected {false};
};

State& state()
{
    static State* s = new State();  // never destroyed: outlives static teardown of the signals
    return *s;
}

std::uint64_t capBytes()
{
    std::uint64_t mb = 512;
    if (const char* e = std::getenv("CADFORK_HLR_CACHE_MB")) {
        char* end = nullptr;
        double v = std::strtod(e, &end);
        if (end != e && v >= 0.0) {
            return static_cast<std::uint64_t>(v * 1024.0 * 1024.0);
        }
    }
    return mb * 1024ULL * 1024ULL;
}

void evict(State& s, const std::list<Entry>::iterator* keep)
{
    std::uint64_t cap = capBytes();
    while (s.stats.bytes > cap && s.lru.size() > 1) {
        auto last = std::prev(s.lru.end());
        if (keep && last == *keep) {
            break;
        }
        s.stats.bytes -= last->bytes;
        s.stats.evictions++;
        s.lru.erase(last);
    }
    s.stats.entries = s.lru.size();
}

void connectSignals(State& s)
{
    if (s.connected) {
        return;
    }
    s.connected = true;
    App::GetApplication().signalDeleteDocument.connect(
        [](const App::Document& d) { HlrCache::dropDocument(&d); });
}

std::list<Entry>::iterator findEntry(State& s, const DrawViewPart* view,
                                     const HlrCacheKeyPtr& key, std::uint64_t h)
{
    for (auto it = s.lru.begin(); it != s.lru.end(); ++it) {
        if (it->view == view && it->hash == h && (it->key == key || *it->key == *key)) {
            return it;
        }
    }
    return s.lru.end();
}

GeometryObjectPtr buildGeometry(DrawViewPart& view, const Entry& e, bool withFaces)
{
    auto go = std::make_shared<GeometryObject>(view.getNameInDocument(), &view);
    go->setIsoCount(view.IsoCount.getValue());
    go->isPerspective(view.Perspective.getValue());
    go->setFocus(view.Focus.getValue());
    go->usePolygonHLR(false);
    go->setScrubCount(view.ScrubCount.getValue());
    go->setHlrCompounds(e.compounds);
    go->setEdgeGeometry(cloneEdges(e.edges));
    go->setVertexGeometry(cloneVertices(e.vertices));
    if (withFaces) {
        for (auto& f : cloneFaces(e.faces)) {
            go->addFaceGeom(f);
        }
    }
    return go;
}

}  // namespace

// ---------------------------------------------------------------------------------------------

bool HlrCacheKey::operator==(const HlrCacheKey& o) const
{
    if (digest != o.digest || leafCount != o.leafCount || vertexCount != o.vertexCount
        || !same(coordSumX, o.coordSumX) || !same(coordSumY, o.coordSumY)
        || !same(coordSumZ, o.coordSumZ) || !same(tolSum, o.tolSum) || !same(scale, o.scale)
        || !same(rotation, o.rotation) || !same(focus, o.focus) || isoCount != o.isoCount
        || perspective != o.perspective || classFlags != o.classFlags
        || scrubCount != o.scrubCount || handleFaces != o.handleFaces
        || identifyVoids != o.identifyVoids || faceFinderVersion != o.faceFinderVersion
        || faceFinderPref != o.faceFinderPref || newFaceFinderPref != o.newFaceFinderPref
        || checkShapes != o.checkShapes || scrubPref != o.scrubPref
        || allowCrazyEdge != o.allowCrazyEdge) {
        return false;
    }
    for (size_t i = 0; i < cs.size(); i++) {
        if (!same(cs[i], o.cs[i])) {
            return false;
        }
    }
    return true;
}

std::uint64_t HlrCacheKey::hash() const
{
    std::uint64_t h = 1469598103934665603ULL;
    mix(h, leafCount);
    for (size_t i = 0; i < 4; i++) {
        std::uint64_t w;
        std::memcpy(&w, digest.data() + 8 * i, 8);
        mix(h, w);
    }
    mix(h, vertexCount);
    for (double d : {coordSumX, coordSumY, coordSumZ, tolSum, scale, rotation, focus}) {
        mix(h, bitsOf(d));
    }
    for (double d : cs) {
        mix(h, bitsOf(d));
    }
    mix(h, static_cast<std::uint64_t>(isoCount) * 31 + perspective);
    for (bool b : classFlags) {
        mix(h, b);
    }
    mix(h, static_cast<std::uint64_t>(scrubCount) * 7 + handleFaces * 2 + identifyVoids * 3
               + checkShapes * 5 + allowCrazyEdge * 11 + newFaceFinderPref * 13);
    mix(h, static_cast<std::uint64_t>(faceFinderVersion + 5) * 17 + (faceFinderPref + 5) * 19
               + scrubPref);
    return h;
}

// ---------------------------------------------------------------------------------------------

bool HlrCache::enabled()
{
    const char* e = std::getenv("CADFORK_HLR_CACHE");
    return !(e && std::strcmp(e, "0") == 0);
}

bool HlrCache::eligible(const DrawViewPart& view)
{
    if (!enabled() || view.CoarseView.getValue()) {
        return false;
    }
    // section, detail and broken views keep stock behaviour; their projected shape is not the
    // source shape the key describes
    if (dynamic_cast<const DrawViewSection*>(&view) || dynamic_cast<const DrawViewDetail*>(&view)
        || dynamic_cast<const DrawBrokenView*>(&view) || dynamic_cast<const DrawViewMulti*>(&view)) {
        return false;
    }
    // a Python getExplodedShape hook replaces the shape with arbitrary code
    Base::PyGILStateLocker lock;
    for (auto* obj : view.getAllSources()) {
        if (!obj) {
            continue;
        }
        auto* proxy = dynamic_cast<App::PropertyPythonObject*>(obj->getPropertyByName("Proxy"));
        if (proxy && proxy->getValue().hasAttr("getExplodedShape")) {
            return false;
        }
    }
    return true;
}

HlrCacheKeyPtr HlrCache::makeKey(const DrawViewPart& view, const TopoDS_Shape& source)
{
    auto key = std::make_shared<HlrCacheKey>();
    digestShape(source, *key);

    // fingerprint of the located source: vertex count, coordinate sum, tolerance sum
    for (TopExp_Explorer ex(source, TopAbs_VERTEX); ex.More(); ex.Next()) {
        const TopoDS_Vertex& v = TopoDS::Vertex(ex.Current());
        gp_Pnt p = BRep_Tool::Pnt(v);
        key->vertexCount++;
        key->coordSumX += p.X();
        key->coordSumY += p.Y();
        key->coordSumZ += p.Z();
        key->tolSum += BRep_Tool::Tolerance(v);
    }

    gp_Ax2 cs = view.getProjectionCS();
    key->cs = {cs.Location().X(), cs.Location().Y(), cs.Location().Z(),
               cs.Direction().X(), cs.Direction().Y(), cs.Direction().Z(),
               cs.XDirection().X(), cs.XDirection().Y(), cs.XDirection().Z()};
    key->scale = view.getScale();
    key->rotation = view.Rotation.getValue();
    key->isoCount = view.IsoCount.getValue();
    key->perspective = view.Perspective.getValue();
    key->focus = view.Focus.getValue();
    key->classFlags = {view.SmoothVisible.getValue(), view.SeamVisible.getValue(),
                       view.IsoVisible.getValue(),    view.HardHidden.getValue(),
                       view.SmoothHidden.getValue(),  view.SeamHidden.getValue(),
                       view.IsoHidden.getValue()};
    key->scrubCount = view.ScrubCount.getValue();

    auto general = Preferences::getPreferenceGroup("General");
    key->handleFaces = general->GetBool("HandleFaces", true);
    key->identifyVoids = general->GetBool("IdentifyVoids", true);
    key->faceFinderVersion = static_cast<int>(Preferences::faceFinderVersion());
    key->faceFinderPref = general->GetInt("FaceFinderVersion", -1);
    key->newFaceFinderPref = general->GetBool("NewFaceFinder", true);
    key->checkShapes = general->GetBool("CheckShapesBeforeUse", false);
    key->scrubPref = general->GetInt("ScrubCount", 1);
    key->allowCrazyEdge = Preferences::getPreferenceGroup("debug")->GetBool("allowCrazyEdge", false);
    return key;
}

bool HlrCache::lookup(DrawViewPart& view, const HlrCacheKeyPtr& key, HlrCacheHit& hit)
{
    State& s = state();
    std::lock_guard<std::mutex> lock(s.mutex);
    auto it = findEntry(s, &view, key, key->hash());
    if (it == s.lru.end()) {
        s.stats.misses++;
        return false;
    }
    s.stats.hits++;
    s.lru.splice(s.lru.begin(), s.lru, it);  // most recently used
    const Entry& e = s.lru.front();
    hit.hasFaces = e.hasFaces;
    hit.geometry = buildGeometry(view, e, e.hasFaces);
    hit.saveShape = e.saveShape;
    hit.saveCentroid = e.saveCentroid;
    return true;
}

void HlrCache::storeGeometry(const DrawViewPart& view, const HlrCacheKeyPtr& key,
                             const GeometryObject& go, const TopoDS_Shape& saveShape,
                             const Base::Vector3d& saveCentroid)
{
    State& s = state();
    std::lock_guard<std::mutex> lock(s.mutex);
    connectSignals(s);
    std::uint64_t h = key->hash();
    auto old = findEntry(s, &view, key, h);
    if (old != s.lru.end()) {
        s.stats.bytes -= old->bytes;
        s.lru.erase(old);
    }
    Entry e;
    e.view = &view;
    e.doc = view.getDocument();
    e.key = key;
    e.hash = h;
    e.edges = cloneEdges(go.getEdgeGeometry());
    e.vertices = cloneVertices(go.getVertexGeometry());
    e.compounds = go.getHlrCompounds();
    e.saveShape = saveShape;
    e.saveCentroid = saveCentroid;
    int nFaces = 0;
    for (TopExp_Explorer ex(saveShape, TopAbs_FACE); ex.More(); ex.Next()) {
        nFaces++;
    }
    e.bytes = estimateGeomBytes(e.edges) * 2  // the converted geometry and the HLR compounds
              + 400 * e.vertices.size() + 6000ULL * nFaces + 1024;
    s.lru.push_front(std::move(e));
    s.stats.bytes += s.lru.front().bytes;
    s.stats.stores++;
    auto keep = s.lru.begin();
    evict(s, &keep);
}

void HlrCache::attachFaces(const DrawViewPart& view, const HlrCacheKeyPtr& key,
                           const GeometryObject& go)
{
    State& s = state();
    std::lock_guard<std::mutex> lock(s.mutex);
    auto it = findEntry(s, &view, key, key->hash());
    if (it == s.lru.end() || it->hasFaces) {
        return;
    }
    it->faces = cloneFaces(go.getFaceGeometry());
    it->hasFaces = true;
    std::uint64_t extra = estimateFaceBytes(it->faces);
    it->bytes += extra;
    s.stats.bytes += extra;
    auto keep = it;
    evict(s, &keep);
}

void HlrCache::dropView(const DrawViewPart* view)
{
    State& s = state();
    std::lock_guard<std::mutex> lock(s.mutex);
    for (auto it = s.lru.begin(); it != s.lru.end();) {
        if (it->view == view) {
            s.stats.bytes -= it->bytes;
            it = s.lru.erase(it);
        }
        else {
            ++it;
        }
    }
    s.stats.entries = s.lru.size();
}

void HlrCache::dropDocument(const App::Document* doc)
{
    State& s = state();
    std::lock_guard<std::mutex> lock(s.mutex);
    for (auto it = s.lru.begin(); it != s.lru.end();) {
        if (it->doc == doc) {
            s.stats.bytes -= it->bytes;
            it = s.lru.erase(it);
        }
        else {
            ++it;
        }
    }
    s.stats.entries = s.lru.size();
}

void HlrCache::clear()
{
    State& s = state();
    std::lock_guard<std::mutex> lock(s.mutex);
    s.lru.clear();
    s.stats.bytes = 0;
    s.stats.entries = 0;
}

HlrCacheStats HlrCache::stats()
{
    State& s = state();
    std::lock_guard<std::mutex> lock(s.mutex);
    HlrCacheStats r = s.stats;
    r.entries = s.lru.size();
    r.capBytes = capBytes();
    return r;
}
