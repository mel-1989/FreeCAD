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

#include <Base/Console.h>

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

void SketchObject::syncBlocks()
{
    if (BlockData.getStrValue().empty()) {
        solvedSketch.setBlockInstances({});
        return;
    }
    const auto& parsed = parsedBlocks();
    std::unordered_map<long, int> idToGeo;
    const auto& geos = getInternalGeometry();
    idToGeo.reserve(geos.size());
    for (size_t i = 0; i < geos.size(); ++i) {
        idToGeo.emplace(GeometryFacade::getFacade(geos[i])->getId(), static_cast<int>(i));
    }
    std::vector<Sketch::BlockInstance> out;
    out.reserve(parsed.size());
    for (const auto& d : parsed) {
        Sketch::BlockInstance inst = d.inst;
        bool complete = true;
        for (size_t k = 0; k < d.ids.size(); ++k) {
            auto it = idToGeo.find(d.ids[k]);
            if (it == idToGeo.end()) {
                complete = false;  // a member is gone: the instance is plain geometry
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

int SketchObject::pruneBlocks()
{
    const std::string src = BlockData.getValue();
    if (src.empty()) {
        return 0;
    }
    nlohmann::json j;
    try {
        j = nlohmann::json::parse(src);
    }
    catch (const std::exception&) {
        return 0;
    }
    std::set<long> ids;
    for (const auto* g : getInternalGeometry()) {
        ids.insert(GeometryFacade::getFacade(g)->getId());
    }
    std::vector<std::string> stale;
    try {
        for (const auto& [iname, idata] : j.at("inst").items()) {
            for (const auto& [key, id] : idata.at("m").items()) {
                if (!ids.count(id.get<long>())) {
                    stale.push_back(iname);
                    break;
                }
            }
        }
        for (const auto& n : stale) {
            j["inst"].erase(n);
        }
    }
    catch (const std::exception&) {
        return 0;
    }
    if (!stale.empty()) {
        BlockData.setValue(j.dump());
    }
    return static_cast<int>(stale.size());
}
