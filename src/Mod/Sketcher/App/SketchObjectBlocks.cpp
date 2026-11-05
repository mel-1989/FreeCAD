// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2026 The FreeCAD project association AISBL              *
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

// Sketch blocks (G10 slice 5a): the JSON in SketchObject::BlockData names definitions (member
// coordinates in the block's own frame) and instances (which geometry, by persistent id, stands
// for which definition member). The solver ties each instance rigidly, see Sketch::addBlockInstances.
// The Python side, Sketcher.SketcherBlocks, writes the JSON; this file only reads it and removes
// instances whose geometry is gone.

#include <algorithm>
#include <set>
#include <unordered_map>

#include <nlohmann/json.hpp>

#include <App/Document.h>
#include <Base/Console.h>
#include <Mod/Part/App/Geometry.h>

#include "SketchObject.h"

using namespace Sketcher;

namespace
{
// layout of a definition member: type letter and its numbers (see Sketch::BlockMember)
bool memberFromJson(const nlohmann::json& d, Sketch::BlockMember& m)
{
    if (!d.is_object() || !d.contains("t") || !d.contains("g")) {
        return false;
    }
    const std::string t = d["t"].get<std::string>();
    const auto& g = d["g"];
    if (t.size() != 1 || !g.is_array()) {
        return false;
    }
    m.type = t[0];
    m.construction = d.contains("c") && d["c"].is_boolean() && d["c"].get<bool>();
    size_t need = m.type == 'p' ? 2 : m.type == 'l' ? 4 : m.type == 'c' ? 3 : m.type == 'a' ? 5 : 0;
    if (need == 0 || g.size() != need) {
        return false;
    }
    m.x0 = g[0].get<double>();
    m.y0 = g[1].get<double>();
    if (m.type == 'l') {
        m.x1 = g[2].get<double>();
        m.y1 = g[3].get<double>();
    }
    else if (m.type == 'c' || m.type == 'a') {
        m.r = g[2].get<double>();
        if (m.type == 'a') {
            m.a0 = g[3].get<double>();
            m.a1 = g[4].get<double>();
        }
    }
    return true;
}
}  // namespace

const std::vector<SketchObject::BlockInstanceData>& SketchObject::parsedBlocks()
{
    const std::string& src = BlockData.getValue();
    if (blockCacheValid && src == blockCacheSource) {
        return blockCache;
    }
    blockCache.clear();
    blockCacheSource = src;
    blockCacheValid = true;
    try {
        auto j = nlohmann::json::parse(src);
        const auto& defs = j.at("defs");
        for (const auto& [iname, idata] : j.at("inst").items()) {
            const auto& def = defs.at(idata.at("def").get<std::string>()).at("members");
            BlockInstanceData out;
            out.name = iname;
            out.defMembers = def.size();
            for (const auto& [key, d] : def.items()) {
                if (!idata.at("m").contains(key)) {
                    continue;  // a member the instance does not have (yet)
                }
                Sketch::BlockMember m;
                if (!memberFromJson(d, m)) {
                    throw std::runtime_error("bad member " + key);
                }
                out.ids.push_back(idata.at("m").at(key).get<long>());
                out.inst.members.push_back(m);
            }
            blockCache.push_back(std::move(out));
        }
    }
    catch (const std::exception& e) {
        Base::Console().warning("{}: block data is not readable and is ignored ({})\n", getNameInDocument() ? getNameInDocument() : "Sketch", e.what());
        blockCache.clear();
    }
    return blockCache;
}

namespace
{
constexpr double kFitLength = 1e-5;  // mm: a member that deviates more from its definition is not part of it
constexpr double kFitAngle = 1e-6;   // rad

struct Numbers
{
    char t = 0;
    double v[5] = {0, 0, 0, 0, 0};
};

bool numbersOf(const Part::Geometry* g, Numbers& n)
{
    if (auto p = dynamic_cast<const Part::GeomPoint*>(g)) {
        n.t = 'p';
        n.v[0] = p->getPoint().x;
        n.v[1] = p->getPoint().y;
    }
    else if (auto l = dynamic_cast<const Part::GeomLineSegment*>(g)) {
        n.t = 'l';
        n.v[0] = l->getStartPoint().x;
        n.v[1] = l->getStartPoint().y;
        n.v[2] = l->getEndPoint().x;
        n.v[3] = l->getEndPoint().y;
    }
    else if (auto a = dynamic_cast<const Part::GeomArcOfCircle*>(g)) {
        n.t = 'a';
        n.v[0] = a->getCenter().x;
        n.v[1] = a->getCenter().y;
        n.v[2] = a->getRadius();
        a->getRange(n.v[3], n.v[4], true);
    }
    else if (auto c = dynamic_cast<const Part::GeomCircle*>(g)) {
        n.t = 'c';
        n.v[0] = c->getCenter().x;
        n.v[1] = c->getCenter().y;
        n.v[2] = c->getRadius();
    }
    else {
        return false;
    }
    return true;
}

struct Pt
{
    double lx, ly, wx, wy;
};

// the member's points as (definition, current) pairs
void pointsOf(const Sketch::BlockMember& m, const Numbers& w, std::vector<Pt>& out)
{
    out.push_back({m.x0, m.y0, w.v[0], w.v[1]});
    if (m.type == 'l') {
        out.push_back({m.x1, m.y1, w.v[2], w.v[3]});
    }
    else if (m.type == 'a') {
        out.push_back(
            {m.x0 + m.r * std::cos(m.a0),
             m.y0 + m.r * std::sin(m.a0),
             w.v[0] + w.v[2] * std::cos(w.v[3]),
             w.v[1] + w.v[2] * std::sin(w.v[3])}
        );
    }
}

// does every member of the instance sit where one rigid placement of its definition puts it?
bool fitsDefinition(const std::vector<Sketch::BlockMember>& members, const std::vector<Numbers>& cur)
{
    std::vector<Pt> pts;
    for (size_t k = 0; k < members.size(); ++k) {
        if (members[k].type != cur[k].t) {
            return false;
        }
        pointsOf(members[k], cur[k], pts);
    }
    if (pts.empty()) {
        return false;
    }
    const Pt q0 = pts.front();
    bool distinct = false;
    double th = 0.0;
    double far = 1e-6;  // the farthest point gives the longest baseline and so the smallest angle error
    for (const auto& q : pts) {
        double d = std::hypot(q.lx - q0.lx, q.ly - q0.ly);
        if (d > far) {
            far = d;
            th = std::atan2(q.wy - q0.wy, q.wx - q0.wx) - std::atan2(q.ly - q0.ly, q.lx - q0.lx);
            distinct = true;
        }
    }
    if (!distinct) {
        return false;  // a definition that cannot tell its rotation
    }
    const double c = std::cos(th), s = std::sin(th);
    const double X = q0.wx - (q0.lx * c - q0.ly * s);
    const double Y = q0.wy - (q0.lx * s + q0.ly * c);
    auto close = [](double a, double b) { return std::abs(a - b) <= kFitLength; };
    auto place = [&](double x, double y, double& ox, double& oy) {
        ox = X + x * c - y * s;
        oy = Y + x * s + y * c;
    };
    for (size_t k = 0; k < members.size(); ++k) {
        const auto& m = members[k];
        const auto& w = cur[k].v;
        double x, y;
        place(m.x0, m.y0, x, y);
        if (!close(x, w[0]) || !close(y, w[1])) {
            return false;
        }
        if (m.type == 'l') {
            place(m.x1, m.y1, x, y);
            if (!close(x, w[2]) || !close(y, w[3])) {
                return false;
            }
        }
        else if (m.type == 'c' || m.type == 'a') {
            if (!close(m.r, w[2])) {
                return false;
            }
            if (m.type == 'a'
                && (std::abs(std::remainder(m.a0 + th - w[3], 2.0 * std::numbers::pi)) > kFitAngle
                    || std::abs(std::remainder(m.a1 + th - w[4], 2.0 * std::numbers::pi))
                        > kFitAngle)) {
                return false;
            }
        }
    }
    return true;
}
}  // namespace

std::vector<bool> SketchObject::validBlockInstances()
{
    const auto& parsed = parsedBlocks();
    std::vector<bool> ok(parsed.size(), false);
    std::unordered_map<long, int> idToGeo;
    const auto& geos = getInternalGeometry();
    idToGeo.reserve(geos.size());
    for (size_t i = 0; i < geos.size(); ++i) {
        idToGeo.emplace(GeometryFacade::getFacade(geos[i])->getId(), static_cast<int>(i));
    }
    std::set<long> used;
    for (size_t n = 0; n < parsed.size(); ++n) {
        const auto& d = parsed[n];
        if (d.inst.members.empty() || d.ids.size() != d.defMembers) {
            continue;  // the instance lacks members of its definition: it is not a whole copy
        }
        bool good = true;
        std::vector<Numbers> cur(d.ids.size());
        for (size_t k = 0; k < d.ids.size() && good; ++k) {
            auto it = idToGeo.find(d.ids[k]);
            good = it != idToGeo.end() && !used.count(d.ids[k]) && numbersOf(geos[it->second], cur[k])
                && GeometryFacade::getFacade(geos[it->second])->getConstruction() == d.inst.members[k].construction;
        }
        if (good) {
            good = fitsDefinition(d.inst.members, cur);
        }
        if (good) {
            for (long id : d.ids) {
                used.insert(id);
            }
        }
        ok[n] = good;
    }
    return ok;
}

void SketchObject::syncBlocks()
{
    if (BlockData.getStrValue().empty()) {
        solvedSketch.setBlockInstances({});
        return;
    }
    // an instance whose members no longer fit its definition (or whose geometry is gone) becomes plain
    // geometry before this solve can pull anything back
    if (blockGuard == 0) {
        explodeInvalidBlocks();
    }
    const auto& parsed = parsedBlocks();
    std::vector<bool> ok = blockGuard == 0 ? validBlockInstances() : std::vector<bool>(parsed.size(), true);
    std::unordered_map<long, int> idToGeo;
    const auto& geos = getInternalGeometry();
    idToGeo.reserve(geos.size());
    for (size_t i = 0; i < geos.size(); ++i) {
        idToGeo.emplace(GeometryFacade::getFacade(geos[i])->getId(), static_cast<int>(i));
    }
    std::vector<Sketch::BlockInstance> out;
    out.reserve(parsed.size());
    for (size_t n = 0; n < parsed.size(); ++n) {
        const auto& d = parsed[n];
        if (!ok[n]) {
            continue;
        }
        Sketch::BlockInstance inst = d.inst;
        bool complete = true;
        for (size_t k = 0; k < d.ids.size(); ++k) {
            auto it = idToGeo.find(d.ids[k]);
            if (it == idToGeo.end()) {
                complete = false;
                break;
            }
            inst.members[k].geoId = it->second;
        }
        if (complete && !inst.members.empty()) {
            out.push_back(std::move(inst));
        }
    }
    solvedSketch.setBlockInstances(std::move(out));
}

int SketchObject::explodeInvalidBlocks()
{
    if (blockGuard != 0 || BlockData.getStrValue().empty() || inExplodeBlocks) {
        return 0;
    }
    const auto* doc = getDocument();
    if (isRestoring() && !restoreChecksBlocks) {
        return 0;
    }
    if (doc && doc->isPerformingTransaction()) {
        return 0;  // properties are being put back one by one; the data may be momentarily out of step
    }
    const auto& parsed = parsedBlocks();
    std::vector<bool> ok = validBlockInstances();
    std::vector<std::string> bad;
    for (size_t n = 0; n < parsed.size(); ++n) {
        if (!ok[n]) {
            bad.push_back(parsed[n].name);
        }
    }
    if (bad.empty()) {
        return 0;
    }
    nlohmann::json j;
    try {
        j = nlohmann::json::parse(BlockData.getValue());
        for (const auto& n : bad) {
            j["inst"].erase(n);
        }
    }
    catch (const std::exception&) {
        return 0;
    }
    inExplodeBlocks = true;
    BlockData.setValue(j.dump());
    inExplodeBlocks = false;
    Base::Console().warning(
        "{}: {} block instance(s) no longer fit their definition or lost geometry and were exploded\n",
        getNameInDocument() ? getNameInDocument() : "Sketch",
        bad.size()
    );
    return static_cast<int>(bad.size());
}

int SketchObject::pruneBlocks()
{
    return explodeInvalidBlocks();
}

int SketchObject::explodeBlocksOf(const std::vector<int>& geoIds)
{
    if (BlockData.getStrValue().empty() || blockGuard != 0) {
        return 0;
    }
    const auto& geos = getInternalGeometry();
    std::set<long> ids;
    for (int g : geoIds) {
        if (g >= 0 && g < static_cast<int>(geos.size())) {
            ids.insert(GeometryFacade::getFacade(geos[g])->getId());
        }
    }
    nlohmann::json j;
    std::vector<std::string> hit;
    try {
        j = nlohmann::json::parse(BlockData.getValue());
        for (const auto& [iname, idata] : j.at("inst").items()) {
            for (const auto& [key, id] : idata.at("m").items()) {
                if (ids.count(id.get<long>())) {
                    hit.push_back(iname);
                    break;
                }
            }
        }
        for (const auto& n : hit) {
            j["inst"].erase(n);
        }
    }
    catch (const std::exception&) {
        return 0;
    }
    if (hit.empty()) {
        return 0;
    }
    inExplodeBlocks = true;
    BlockData.setValue(j.dump());
    inExplodeBlocks = false;
    Base::Console().warning(
        "{}: {} block instance(s) were exploded by an edit of one of their members\n",
        getNameInDocument() ? getNameInDocument() : "Sketch",
        hit.size()
    );
    return static_cast<int>(hit.size());
}
