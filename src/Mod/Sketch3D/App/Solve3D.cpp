// SPDX-License-Identifier: LGPL-2.1-or-later

#include <algorithm>
#include <cmath>
#include <functional>
#include <cstring>
#include <map>
#include <numbers>
#include <random>
#include <memory>
#include <set>

#include <Mod/Sketcher/App/planegcs/GCS.h>
#include <Mod/Sketcher/App/planegcs/SubSystem.h>

#include "Solve3D.h"

namespace Sketch3D
{

namespace
{

bool finite3(const std::array<double, 3>& v)
{
    return std::isfinite(v[0]) && std::isfinite(v[1]) && std::isfinite(v[2]);
}

std::string where(int position, const ConstraintSpec& c)
{
    return "constraint " + std::to_string(position) + " (" + kindInfo(c.kind).name + "): ";
}

PlaneData planeData(const PlaneSpec& s)
{
    PlaneData d {};
    const double len = std::sqrt(
        s.normal[0] * s.normal[0] + s.normal[1] * s.normal[1] + s.normal[2] * s.normal[2]
    );
    for (int i = 0; i < 3; ++i) {
        d.p[i] = s.point[i];
        d.n[i] = s.normal[i] / len;
    }
    return d;
}

// The rows of constraint ci on the coordinate array c.
std::vector<std::unique_ptr<Rel>> buildRows(
    const Spec& spec,
    int ci,
    double* c,
    const std::vector<PlaneData>& planes
)
{
    const ConstraintSpec& cs = spec.constraints[ci];
    const KindInfo& info = kindInfo(cs.kind);
    std::vector<double*> params;
    PlaneData plane {};
    int axis = 0;
    for (size_t k = 0; k < cs.operands.size(); ++k) {
        const int o = cs.operands[k];
        switch (info.operands[k]) {
            case 'p':
                for (int a = 0; a < 3; ++a) {
                    params.push_back(c + 3 * o + a);
                }
                break;
            case 'l':
                for (int e = 0; e < 2; ++e) {
                    for (int a = 0; a < 3; ++a) {
                        params.push_back(c + 3 * spec.lines[o][e] + a);
                    }
                }
                break;
            case 'P':
                plane = planes[o];
                break;
            default:
                axis = o;
                break;
        }
    }
    std::vector<std::unique_ptr<Rel>> rows;
    for (int r = 0; r < info.rows; ++r) {
        rows.push_back(std::make_unique<Rel>(cs.kind, r, axis, cs.value, plane, params, ci + 1));
    }
    return rows;
}

std::vector<PlaneData> allPlanes(const Spec& spec)
{
    std::vector<PlaneData> planes;
    for (const auto& p : spec.planes) {
        planes.push_back(planeData(p));
    }
    return planes;
}

double maxAbsRow(std::vector<std::unique_ptr<Rel>>& rows)
{
    double m = 0.0;
    for (auto& r : rows) {
        const double e = std::fabs(r->rawError());
        // a nan row must never read as a small residual
        m = std::isnan(e) ? HUGE_VAL : std::max(m, e);
    }
    return m;
}

// Lines that carry a direction relation: for each, the indices of those constraints. A line of
// length zero satisfies every one of them vacuously, so a solve that ends there has not solved
// the relation.
std::map<int, std::vector<int>> directionLines(const Spec& spec)
{
    std::map<int, std::vector<int>> out;
    for (int ci = 0; ci < static_cast<int>(spec.constraints.size()); ++ci) {
        const ConstraintSpec& c = spec.constraints[ci];
        if (!c.driving) {
            continue;
        }
        switch (c.kind) {
            case Kind::Parallel:
            case Kind::Perpendicular:
            case Kind::Angle:
                out[c.operands[0]].push_back(ci);
                out[c.operands[1]].push_back(ci);
                break;
            case Kind::AlongX:
            case Kind::AlongY:
            case Kind::AlongZ:
            case Kind::ParallelPlane:
            case Kind::NormalPlane:
                out[c.operands[0]].push_back(ci);
                break;
            case Kind::PointOnLine:
                out[c.operands[1]].push_back(ci);
                break;
            default:
                break;
        }
    }
    return out;
}

double lineLength(const Spec& spec, const std::vector<double>& c, int line)
{
    const int a = spec.lines[line][0];
    const int b = spec.lines[line][1];
    return std::sqrt(
        (c[3 * a] - c[3 * b]) * (c[3 * a] - c[3 * b])
        + (c[3 * a + 1] - c[3 * b + 1]) * (c[3 * a + 1] - c[3 * b + 1])
        + (c[3 * a + 2] - c[3 * b + 2]) * (c[3 * a + 2] - c[3 * b + 2])
    );
}

// Tags of the direction relations on lines that are shorter than CollapseLength at c and were
// longer at start; empty when no line collapsed.
std::vector<int> collapsedTags(
    const Spec& spec,
    const std::vector<double>& start,
    const std::vector<double>& c
)
{
    std::set<int> tags;
    for (const auto& [line, cons] : directionLines(spec)) {
        if (lineLength(spec, start, line) > CollapseLength && lineLength(spec, c, line) < CollapseLength) {
            for (int ci : cons) {
                tags.insert(spec.constraints[ci].tag);
            }
        }
    }
    return std::vector<int>(tags.begin(), tags.end());
}

bool isDirectionKind(Kind k)
{
    switch (k) {
        case Kind::Parallel:
        case Kind::Perpendicular:
        case Kind::Angle:
        case Kind::AlongX:
        case Kind::AlongY:
        case Kind::AlongZ:
        case Kind::ParallelPlane:
        case Kind::NormalPlane:
            return true;
        default:
            return false;
    }
}

}  // namespace

void validate(const Spec& spec)
{
    const int np = static_cast<int>(spec.points.size());
    const int nl = static_cast<int>(spec.lines.size());
    const int npl = static_cast<int>(spec.planes.size());
    for (int i = 0; i < np; ++i) {
        if (!finite3(spec.points[i].xyz)) {
            throw SpecError(-1, "point " + std::to_string(i) + ": coordinates must be finite");
        }
    }
    for (int i = 0; i < nl; ++i) {
        for (int e = 0; e < 2; ++e) {
            if (spec.lines[i][e] < 0 || spec.lines[i][e] >= np) {
                throw SpecError(-1, "line " + std::to_string(i) + ": point index out of range");
            }
        }
    }
    for (int i = 0; i < npl; ++i) {
        const PlaneSpec& p = spec.planes[i];
        if (!finite3(p.point) || !finite3(p.normal)) {
            throw SpecError(-1, "plane " + std::to_string(i) + ": point and normal must be finite");
        }
        const double len2 = p.normal[0] * p.normal[0] + p.normal[1] * p.normal[1]
            + p.normal[2] * p.normal[2];
        if (!(len2 > 0.0) || !std::isfinite(len2)) {
            throw SpecError(-1, "plane " + std::to_string(i) + ": normal must not be zero");
        }
    }
    if (!spec.algorithm.empty() && spec.algorithm != "dogleg" && spec.algorithm != "lm"
        && spec.algorithm != "bfgs") {
        throw SpecError(-1, "algorithm must be '', 'dogleg', 'lm' or 'bfgs'");
    }
    for (int ci = 0; ci < static_cast<int>(spec.constraints.size()); ++ci) {
        const ConstraintSpec& c = spec.constraints[ci];
        const KindInfo& info = kindInfo(c.kind);
        const std::string w = where(ci, c);
        if (c.operands.size() != std::strlen(info.operands)) {
            throw SpecError(
                ci,
                w + "expects " + std::to_string(std::strlen(info.operands)) + " operands, got "
                    + std::to_string(c.operands.size())
            );
        }
        for (size_t k = 0; k < c.operands.size(); ++k) {
            const int o = c.operands[k];
            int limit = 0;
            const char* what = "";
            switch (info.operands[k]) {
                case 'p':
                    limit = np;
                    what = "point";
                    break;
                case 'l':
                    limit = nl;
                    what = "line";
                    break;
                case 'P':
                    limit = npl;
                    what = "plane";
                    break;
                default:
                    limit = 3;
                    what = "axis";
                    break;
            }
            if (o < 0 || o >= limit) {
                throw SpecError(ci, w + what + " index " + std::to_string(o) + " out of range");
            }
        }
        if (info.hasValue) {
            if (!std::isfinite(c.value)) {
                throw SpecError(ci, w + "value must be finite");
            }
            const bool lengthLike = c.kind != Kind::FixedCoordinate && c.kind != Kind::Angle;
            if (lengthLike && c.value < 0.0) {
                throw SpecError(ci, w + "value must not be negative");
            }
            if (c.kind == Kind::Angle && (c.value < 0.0 || c.value > std::numbers::pi)) {
                throw SpecError(ci, w + "angle must lie in [0, pi] radians");
            }
        }
    }
}

namespace
{

const char* algorithmName(GCS::Algorithm a)
{
    switch (a) {
        case GCS::BFGS:
            return "BFGS";
        case GCS::LevenbergMarquardt:
            return "LevenbergMarquardt";
        default:
            return "DogLeg";
    }
}

}  // namespace

namespace
{

Result solveImpl(const Spec& spec, int depth, bool retry);

}  // namespace

namespace
{

// Unknowns from which the points and constraints are renumbered before the solve (the same size
// at which the diagnosis turns to a sparse QR).
constexpr int RenumberUnknowns = 250;

// A renumbering of the free points and of the constraints. pointNew[old] is the new index of a
// point, conNew[old] the new index of a constraint; the lists are the inverses.
struct Renumbering
{
    std::vector<int> pointNew, pointOld, conNew, conOld;
};

// The points that a constraint touches, through its point and line operands.
void touchedPoints(const Spec& spec, const ConstraintSpec& cs, std::vector<int>& out)
{
    out.clear();
    const KindInfo& info = kindInfo(cs.kind);
    for (size_t k = 0; k < cs.operands.size(); ++k) {
        const int o = cs.operands[k];
        if (info.operands[k] == 'p') {
            out.push_back(o);
        }
        else if (info.operands[k] == 'l') {
            out.push_back(spec.lines[o][0]);
            out.push_back(spec.lines[o][1]);
        }
    }
}

// A fill-reducing order of the free points and an order of the constraints that suits the sparse
// QR of the diagnosis, computed from the sketch's own structure so that the speed of a solve does
// not depend on how the user numbered the points and listed the relations.
//
// Points: nested dissection (George 1973) of the graph whose vertices are the free points and
// whose hyperedges are the constraints (a constraint joins all the free points it touches). A
// component is cut at the breadth-first level (Cuthill and McKee 1969, George 1971) that holds
// the median vertex, counted from a pseudo-peripheral start (George and Liu 1979); the pieces
// are ordered first and the cut last, recursively. Ties go to lower degree, then lower index.
// Plain reverse Cuthill-McKee for the columns was tried first and is 3.6 to 10 times slower on
// a grid of lines than the dissection (3 s, or 8.5 s in the worse of two modes, against 0.4 to
// 0.9 s at 3,069 unknowns). Fixed points have no unknowns and take the last positions.
//
// Constraints: sorted by the reverse Cuthill-McKee position of their points, then matched to
// columns (below). The cost is near linear in the number of incidences and the result depends on
// nothing but the input, so it is the same in every process.
Renumbering renumber(const Spec& spec)
{
    const int np = static_cast<int>(spec.points.size());
    const int nc = static_cast<int>(spec.constraints.size());
    std::vector<std::vector<int>> adj(np);
    std::vector<int> pts;
    for (int ci = 0; ci < nc; ++ci) {
        touchedPoints(spec, spec.constraints[ci], pts);
        std::vector<int> free;
        for (int p : pts) {
            if (!spec.points[p].fixed) {
                free.push_back(p);
            }
        }
        std::sort(free.begin(), free.end());
        free.erase(std::unique(free.begin(), free.end()), free.end());
        for (int a : free) {
            for (int b : free) {
                if (a != b) {
                    adj[a].push_back(b);
                }
            }
        }
    }
    for (auto& v : adj) {
        std::sort(v.begin(), v.end());
        v.erase(std::unique(v.begin(), v.end()), v.end());
    }
    auto byDegree = [&](int a, int b) {
        return adj[a].size() != adj[b].size() ? adj[a].size() < adj[b].size() : a < b;
    };
    for (auto& v : adj) {
        std::sort(v.begin(), v.end(), byDegree);
    }

    std::vector<int> level(np, 0), done(np, 0), ndOrder;
    {
        // nested dissection by level-set separators
        std::vector<int> inSet(np, -1);  // the call that owns a vertex
        int stamp = 1;
        std::vector<int> out;
        std::function<void(std::vector<int>)> dissect = [&](std::vector<int> verts) {
            // verts is connected. Order it: pieces first, separator last.
            if (verts.size() <= 24) {
                // small: plain breadth-first from the lowest degree vertex, reversed
                ++stamp;
                for (int v : verts) {
                    inSet[v] = stamp;
                }
                int root = verts[0];
                for (int v : verts) {
                    if (byDegree(v, root)) {
                        root = v;
                    }
                }
                std::vector<int> q(1, root);
                inSet[root] = -stamp;
                for (size_t h = 0; h < q.size(); ++h) {
                    for (int w : adj[q[h]]) {
                        if (inSet[w] == stamp) {
                            inSet[w] = -stamp;
                            q.push_back(w);
                        }
                    }
                }
                out.insert(out.end(), q.rbegin(), q.rend());
                return;
            }
            ++stamp;
            const int mine = stamp;
            for (int v : verts) {
                inSet[v] = mine;
            }
            auto sweep2 = [&](int root, std::vector<int>& ord) {
                ++stamp;
                ord.assign(1, root);
                level[root] = 0;
                inSet[root] = -mine;
                for (size_t h = 0; h < ord.size(); ++h) {
                    for (int w : adj[ord[h]]) {
                        if (inSet[w] == mine) {
                            inSet[w] = -mine;
                            level[w] = level[ord[h]] + 1;
                            ord.push_back(w);
                        }
                    }
                }
                for (int v : ord) {
                    inSet[v] = mine;
                }
            };
            std::vector<int> ord;
            int root = verts[0];
            for (int v : verts) {
                if (byDegree(v, root)) {
                    root = v;
                }
            }
            sweep2(root, ord);
            int depthL = level[ord.back()];
            for (int guard = 0; guard < 8; ++guard) {
                int cand = -1;
                for (int v : ord) {
                    if (level[v] == depthL && (cand < 0 || byDegree(v, cand))) {
                        cand = v;
                    }
                }
                std::vector<int> ord2;
                sweep2(cand, ord2);
                const int d2 = level[ord2.back()];
                if (d2 > depthL) {
                    depthL = d2;
                    ord = ord2;
                }
                else {
                    sweep2(ord.front(), ord);
                    break;
                }
            }
            if (depthL < 2) {
                out.insert(out.end(), ord.rbegin(), ord.rend());
                return;
            }
            // separator level: the one where the cumulative count first reaches half
            std::vector<int> count(depthL + 1, 0);
            for (int v : ord) {
                ++count[level[v]];
            }
            int acc = 0, sepL = 1;
            for (int l = 0; l <= depthL; ++l) {
                acc += count[l];
                if (2 * acc >= static_cast<int>(ord.size())) {
                    sepL = std::min(std::max(l, 1), depthL - 1);
                    break;
                }
            }
            std::vector<int> sep;
            for (int v : ord) {
                if (level[v] == sepL) {
                    sep.push_back(v);
                    inSet[v] = 0;
                }
            }
            // components of the rest
            for (int v : ord) {
                if (inSet[v] != mine) {
                    continue;
                }
                std::vector<int> comp(1, v);
                inSet[v] = -mine;
                for (size_t h = 0; h < comp.size(); ++h) {
                    for (int w : adj[comp[h]]) {
                        if (inSet[w] == mine) {
                            inSet[w] = -mine;
                            comp.push_back(w);
                        }
                    }
                }
                for (int c : comp) {
                    inSet[c] = -1;
                }
                dissect(comp);
            }
            out.insert(out.end(), sep.begin(), sep.end());
        };
        for (int sv = 0; sv < np; ++sv) {
            if (spec.points[sv].fixed || done[sv]) {
                continue;
            }
            std::vector<int> comp(1, sv);
            done[sv] = 1;
            for (size_t h = 0; h < comp.size(); ++h) {
                for (int w : adj[comp[h]]) {
                    if (!done[w]) {
                        done[w] = 1;
                        comp.push_back(w);
                    }
                }
            }
            dissect(comp);
        }
        ndOrder = out;
    }
    // Reverse Cuthill-McKee order, per component from a pseudo-peripheral vertex. It orders the
    // constraints (below): the dependent constraints of a closed loop are then found among
    // neighbours, so a redundant set stays small (a grid of lines names 36 constraints with this
    // key and 506 with the dissection order).
    std::vector<int> keyOrder;
    {
        std::vector<int> seenC(np, 0), lev(np, 0);
        auto bfsFrom = [&](int root, std::vector<int>& ord, std::vector<int>& visitedStamp, int st) {
            ord.assign(1, root);
            visitedStamp[root] = st;
            lev[root] = 0;
            for (size_t h = 0; h < ord.size(); ++h) {
                for (int w : adj[ord[h]]) {
                    if (visitedStamp[w] != st) {
                        visitedStamp[w] = st;
                        lev[w] = lev[ord[h]] + 1;
                        ord.push_back(w);
                    }
                }
            }
        };
        std::vector<int> stampv(np, 0), o1, o2;
        int st = 0;
        for (int sv = 0; sv < np; ++sv) {
            if (spec.points[sv].fixed || seenC[sv]) {
                continue;
            }
            bfsFrom(sv, o1, stampv, ++st);
            int root = sv;
            int dep = lev[o1.back()];
            for (int g = 0; g < 8; ++g) {
                int cand = -1;
                for (int v : o1) {
                    if (lev[v] == dep && (cand < 0 || byDegree(v, cand))) {
                        cand = v;
                    }
                }
                bfsFrom(cand, o2, stampv, ++st);
                const int d2 = lev[o2.back()];
                if (d2 > dep) {
                    dep = d2;
                    root = cand;
                    o1 = o2;
                }
                else {
                    break;
                }
            }
            bfsFrom(root, o1, stampv, ++st);
            for (int v : o1) {
                seenC[v] = 1;
            }
            keyOrder.insert(keyOrder.end(), o1.rbegin(), o1.rend());
        }
    }
    Renumbering r;
    r.pointOld = ndOrder;  // the nested dissection order of the free points
    for (int p = 0; p < np; ++p) {
        if (spec.points[p].fixed) {
            r.pointOld.push_back(p);
        }
    }
    r.pointNew.assign(np, 0);
    for (int k = 0; k < np; ++k) {
        r.pointNew[r.pointOld[k]] = k;
    }

    // Constraint order. Eigen's sparse QR takes row k to belong to column k (the Householder
    // vector of column k starts at row k), so a Jacobian whose first rows each touch "their"
    // column fills least. Rows are therefore matched to columns (a maximum bipartite matching on
    // the nonzero pattern of the Jacobian at the start: a greedy pass in wavefront order, then
    // augmenting paths, Duff 1981), and the constraints are sorted by the column their first
    // matched row took. Rows no column takes (the surplus of a tall system) and constraints with
    // no unknown go last in wavefront order. All of it is a heuristic about speed; any order is
    // a correct input.
    std::vector<int> kpos = r.pointNew;
    if (!keyOrder.empty()) {
        for (size_t k = 0; k < keyOrder.size(); ++k) {
            kpos[keyOrder[k]] = static_cast<int>(k);
        }
    }
    std::vector<double> c0(3 * np);
    for (int i = 0; i < np; ++i) {
        for (int a = 0; a < 3; ++a) {
            c0[3 * i + a] = spec.points[i].xyz[a];
        }
    }
    const std::vector<PlaneData> planes = allPlanes(spec);
    std::vector<int> colOfParam(3 * np, -1);
    int ncols = 0;
    for (int k = 0; k < np; ++k) {
        const int pt = r.pointOld[k];
        if (!spec.points[pt].fixed) {
            for (int a = 0; a < 3; ++a) {
                colOfParam[3 * pt + a] = 3 * k + a;
            }
            ++ncols;
        }
    }
    ncols *= 3;
    std::vector<std::array<int, 4>> keys(nc);  // wavefront key of a constraint
    std::vector<std::vector<int>> rowCols;     // column pattern of each row
    std::vector<int> rowCon;                   // constraint of each row
    for (int ci = 0; ci < nc; ++ci) {
        touchedPoints(spec, spec.constraints[ci], pts);
        int lo = np, hi = -1;
        for (int p : pts) {
            if (!spec.points[p].fixed) {
                lo = std::min(lo, kpos[p]);
                hi = std::max(hi, kpos[p]);
            }
        }
        // ties between constraints on the same points: the one with more rows first, then by
        // kind, so that the order does not depend on the order the user listed them in (a
        // direction relation's two rows ahead of a length's one measured 2.4 times faster on a
        // grid than the reverse)
        const int tie = -1000 * kindInfo(spec.constraints[ci].kind).rows
                        - static_cast<int>(spec.constraints[ci].kind);
        keys[ci] = {lo, hi, tie, ci};
        if (!spec.constraints[ci].driving || hi < 0) {
            continue;
        }
        auto rows = buildRows(spec, ci, c0.data(), planes);
        for (auto& row : rows) {
            std::vector<int> cols;
            for (double* q : row->allParams()) {
                const int col = colOfParam[q - c0.data()];
                if (col >= 0 && row->grad(q) != 0.0) {
                    cols.push_back(col);
                }
            }
            std::sort(cols.begin(), cols.end());
            cols.erase(std::unique(cols.begin(), cols.end()), cols.end());
            rowCols.push_back(std::move(cols));
            rowCon.push_back(ci);
        }
    }
    const int nrows = static_cast<int>(rowCols.size());
    std::vector<int> byKey(nrows);
    for (int i = 0; i < nrows; ++i) {
        byKey[i] = i;
    }
    std::stable_sort(byKey.begin(), byKey.end(), [&](int a, int b) {
        return keys[rowCon[a]] < keys[rowCon[b]];
    });
    std::vector<int> rowOfCol(ncols, -1), colOfRow(nrows, -1);
    for (int i : byKey) {
        for (int col : rowCols[i]) {
            if (rowOfCol[col] < 0) {
                rowOfCol[col] = i;
                colOfRow[i] = col;
                break;
            }
        }
    }
    {
        // augmenting paths for the rows left over, by depth-first search with a visit stamp; the
        // total work is capped so a hard structure costs at most a few passes
        std::vector<int> seen(ncols, -1);
        long budget = 40L * (static_cast<long>(nrows) + ncols) + 1000;
        std::vector<std::pair<int, size_t>> stack;
        for (int i : byKey) {
            if (colOfRow[i] >= 0 || budget <= 0) {
                continue;
            }
            stack.assign(1, {i, 0});
            std::vector<int> viaCol(1, -1);
            bool found = false;
            while (!stack.empty() && !found && budget > 0) {
                auto& [row, pos] = stack.back();
                if (pos >= rowCols[row].size()) {
                    stack.pop_back();
                    viaCol.pop_back();
                    continue;
                }
                const int col = rowCols[row][pos++];
                --budget;
                if (seen[col] == i) {
                    continue;
                }
                seen[col] = i;
                if (rowOfCol[col] < 0) {
                    // flip the path: every row on the stack takes the column it was exploring
                    int take = col;
                    for (size_t d = stack.size(); d-- > 0;) {
                        const int rr = stack[d].first;
                        const int prev = colOfRow[rr];
                        colOfRow[rr] = take;
                        rowOfCol[take] = rr;
                        take = prev;
                    }
                    found = true;
                }
                else {
                    stack.push_back({rowOfCol[col], 0});
                    viaCol.push_back(col);
                }
            }
        }
    }
    // a constraint sorts by the smallest matched column of its rows; unmatched ones follow
    std::vector<std::array<int, 5>> ckeys(nc);
    for (int ci = 0; ci < nc; ++ci) {
        ckeys[ci] = {1, keys[ci][0], keys[ci][1], keys[ci][2], ci};
    }
    for (int i = 0; i < nrows; ++i) {
        auto& k = ckeys[rowCon[i]];
        if (colOfRow[i] >= 0 && (k[0] == 1 || colOfRow[i] < k[1])) {
            k = {0, colOfRow[i], 0, keys[rowCon[i]][2], rowCon[i]};
        }
    }
    r.conOld.resize(nc);
    for (int ci = 0; ci < nc; ++ci) {
        r.conOld[ci] = ci;
    }
    std::sort(r.conOld.begin(), r.conOld.end(), [&](int a, int b) { return ckeys[a] < ckeys[b]; });
    r.conNew.assign(nc, 0);
    for (int k = 0; k < nc; ++k) {
        r.conNew[r.conOld[k]] = k;
    }
    return r;
}

}  // namespace

Result solve(const Spec& spec)
{
    validate(spec);
    int unknowns = 0;
    for (const auto& p : spec.points) {
        unknowns += p.fixed ? 0 : 3;
    }
    if (unknowns < RenumberUnknowns) {
        return solveImpl(spec, 0, true);
    }
    // Large systems are solved in a numbering of our own (see renumber) and the answer is mapped
    // back: coordinates by point, residuals by constraint. Conflict and redundancy lists carry
    // the user's tags, which do not move.
    const Renumbering rn = renumber(spec);
    Spec ordered = spec;
    for (size_t k = 0; k < rn.pointOld.size(); ++k) {
        ordered.points[k] = spec.points[rn.pointOld[k]];
    }
    for (auto& l : ordered.lines) {
        l = {rn.pointNew[l[0]], rn.pointNew[l[1]]};
    }
    for (size_t k = 0; k < rn.conOld.size(); ++k) {
        ordered.constraints[k] = spec.constraints[rn.conOld[k]];
        const KindInfo& info = kindInfo(ordered.constraints[k].kind);
        for (size_t j = 0; j < ordered.constraints[k].operands.size(); ++j) {
            if (info.operands[j] == 'p') {
                int& o = ordered.constraints[k].operands[j];
                o = rn.pointNew[o];
            }
        }
    }
    Result res = solveImpl(ordered, 0, true);
    Result out = res;
    for (size_t k = 0; k < rn.pointOld.size(); ++k) {
        out.coordinates[rn.pointOld[k]] = res.coordinates[k];
    }
    for (size_t k = 0; k < rn.conOld.size(); ++k) {
        out.residuals[rn.conOld[k]] = res.residuals[k];
    }
    return out;
}

namespace
{

// A System with its constraints built on one coordinate array. A fresh Model per attempt keeps
// the attempts independent: a failed run leaves no solver state behind for the next one.
struct Model
{
    const Spec& spec;
    std::vector<double> coords;
    GCS::System sys;
    GCS::VEC_pD unknowns;
    std::vector<std::vector<std::unique_ptr<Rel>>> own;  // driven relations and ones with no unknown
    std::vector<std::vector<Rel*>> inSystem;
    std::vector<int> staticDriving;
    std::vector<std::unique_ptr<Rel>> solverRows;  // when not handed to the System
    std::unique_ptr<GCS::SubSystem> sub;
    int systemRows = 0;
    double lmTau = 1e-3;

    Model(
        const Spec& specIn,
        const std::vector<double>& c0,
        const std::vector<PlaneData>& planes,
        bool forDiagnosis
    )
        : spec(specIn)
        , coords(c0)
    {
        const int np = static_cast<int>(spec.points.size());
        const int nc = static_cast<int>(spec.constraints.size());
        std::set<double*> unknownSet;
        for (int i = 0; i < np; ++i) {
            if (!spec.points[i].fixed) {
                for (int a = 0; a < 3; ++a) {
                    unknowns.push_back(&coords[3 * i + a]);
                    unknownSet.insert(&coords[3 * i + a]);
                }
            }
        }
        own.resize(nc);
        inSystem.resize(nc);
        for (int ci = 0; ci < nc; ++ci) {
            auto rows = buildRows(spec, ci, coords.data(), planes);
            bool touchesUnknown = false;
            for (double* p : rows.front()->allParams()) {
                touchesUnknown = touchesUnknown || unknownSet.count(p) > 0;
            }
            if (!spec.constraints[ci].driving) {
                own[ci] = std::move(rows);
            }
            else if (!touchesUnknown) {
                own[ci] = std::move(rows);
                staticDriving.push_back(ci);
            }
            else {
                for (auto& r : rows) {
                    inSystem[ci].push_back(r.get());
                    if (forDiagnosis) {
                        sys.addConstraint(r.release());
                    }
                    else {
                        solverRows.push_back(std::move(r));
                    }
                    ++systemRows;
                }
            }
        }
    }

    double residualOf(int ci)
    {
        double m = 0.0;
        if (!inSystem[ci].empty()) {
            for (Rel* r : inSystem[ci]) {
                const double e = std::fabs(r->rawError());
                m = std::isnan(e) ? HUGE_VAL : std::max(m, e);
            }
            return m;
        }
        return maxAbsRow(own[ci]);
    }

    double maxDriving()
    {
        double m = 0.0;
        for (int ci = 0; ci < static_cast<int>(spec.constraints.size()); ++ci) {
            if (spec.constraints[ci].driving) {
                m = std::max(m, residualOf(ci));
            }
        }
        return m;
    }

    double sumSquares()
    {
        double ss = 0.0;
        for (int ci = 0; ci < static_cast<int>(spec.constraints.size()); ++ci) {
            if (!spec.constraints[ci].driving) {
                continue;
            }
            for (Rel* r : inSystem[ci]) {
                ss += r->rawError() * r->rawError();
            }
            for (auto& r : own[ci]) {
                ss += r->rawError() * r->rawError();
            }
        }
        return ss;
    }

    int maxBuilds()
    {
        int b = 0;
        for (auto& v : inSystem) {
            for (Rel* r : v) {
                b = std::max(b, r->jacobianBuilds());
            }
        }
        return b;
    }

    // Solve without the System's own initialisation: that runs a diagnosis (a sparse QR of the
    // whole Jacobian, most of the time of a large solve) which is run once, at the point reached,
    // by diagnose().
    GCS::SolveStatus solveDirect(GCS::Algorithm alg)
    {
        tune();
        std::vector<GCS::Constraint*> list;
        for (auto& r : solverRows) {
            list.push_back(r.get());
        }
        sub = std::make_unique<GCS::SubSystem>(list, unknowns);
        sys.dogLegGaussStep = tallStep(list.size(), unknowns.size());
        const GCS::SolveStatus st = sys.solve(sub.get(), alg, false);
        sub->applySolution();
        return st;
    }

    // The Gauss-Newton step for a Jacobian of rows x cols. More rows than unknowns means closed
    // loops, so J is rank deficient and J J^T is singular: the sparse Cholesky of J J^T fails on
    // every step and the System falls back to a dense LDLT of the rows x rows matrix (225 ms a
    // step at 969 unknowns on a grid of lines). A sparse QR of J itself (Davis, "Algorithm 915,
    // SuiteSparseQR") handles the singular case and gives a least-squares step, which is the
    // exact one for the consistent systems this solver is given.
    static GCS::DogLegGaussStep tallStep(size_t rows, size_t cols)
    {
        return (rows > cols && cols >= 200) ? GCS::SparseNormalLdlt : GCS::LeastNormLdlt;
    }

    void tune()
    {
        // PlaneGCS stops at an error of 1e-10 by default, which leaves coordinates a few 1e-9
        // off an ill-conditioned solution, so ask for rounding level. 1e-15 is the rounding
        // floor of a residual made of lengths of order one. A smaller value (this was 1e-22)
        // can never be reached on a grid of lines, whose residual stalls near 1e-16: the solver
        // ran all 100 iterations, 600 ms at 297 unknowns and 20 s at 969, where 10 will do.
        // On a system of very large coordinates the floor is higher and the limit is the
        // iteration count, as before.
        sys.DL_tolf = 1e-15;
        sys.LM_eps = 1e-13;
        sys.LM_tau = lmTau;
        // sparse Jacobians pay from a few hundred unknowns on: every row touches at most 12
        sys.sparseSolveThreshold = 200;
        sys.partitionThreshold = 200;
        // J J^T with a sparse Cholesky: the sparse QR fills in on chains (0.6 s a step at 3000
        // unknowns). A rank-deficient J makes it fail and the System falls back to the dense step.
        sys.dogLegGaussStep = GCS::LeastNormLdlt;
    }

    void prepare(GCS::Algorithm alg)
    {
        tune();
        sys.declareUnknowns(unknowns);
        GCS::VEC_pD driven;
        sys.declareDrivenParams(driven);
        // The diagnosis of a large system: a sparse QR from 250 unknowns on (the System's default
        // is 1,000, below which a dense QR of the whole Jacobian takes 0.2 s at 1,000 unknowns),
        // with the columns in the order the points were given. COLAMD's order fills the triangle
        // on a grid of lines (10 s at 3,069 unknowns against 0.5 s) and points of a drawing come in
        // an order that has locality. spec.referenceDiagnosis asks for the System's own choice.
        // The ordering flag is process wide and the GIL is held for the whole call, so the 2D
        // sketcher cannot run in between; the guard clears it even if the diagnosis throws.
        struct OrderGuard
        {
            explicit OrderGuard(bool on)
            {
                GCS::diagnoseNaturalOrderFlag = on;
            }
            ~OrderGuard()
            {
                GCS::diagnoseNaturalOrderFlag = false;
            }
        } orderGuard(!spec.referenceDiagnosis);
        if (!spec.referenceDiagnosis) {
            sys.autoQRThreshold = 250;
            sys.diagnoseParallel = true;
        }
        sys.initSolution(alg);
    }
};

Result solveImpl(const Spec& spec, int depth, bool retry)
{
    const int np = static_cast<int>(spec.points.size());
    const int nc = static_cast<int>(spec.constraints.size());
    std::vector<double> start(3 * np);
    for (int i = 0; i < np; ++i) {
        for (int a = 0; a < 3; ++a) {
            start[3 * i + a] = spec.points[i].xyz[a];
        }
    }
    const std::vector<PlaneData> planes = allPlanes(spec);
    auto make = [&](const std::vector<double>& c0, bool forDiagnosis = false) {
        return std::make_unique<Model>(spec, c0, planes, forDiagnosis);
    };

    std::vector<GCS::Algorithm> order;
    std::vector<double> taus;
    if (spec.algorithm == "dogleg") {
        order = {GCS::DogLeg};
    }
    else if (spec.algorithm == "lm") {
        order = {GCS::LevenbergMarquardt};
    }
    else if (spec.algorithm == "bfgs") {
        order = {GCS::BFGS};
    }
    else {
        order = {GCS::DogLeg, GCS::LevenbergMarquardt, GCS::BFGS};
    }

    Result res;
    res.residuals.assign(nc, 0.0);
    auto m0 = make(start);
    const int systemRows = m0->systemRows;
    const int unknownCount = static_cast<int>(m0->unknowns.size());
    if (spec.algorithm.empty() && unknownCount < 200) {
        // Small systems: Levenberg-Marquardt with a heavy damping (tau 1) first. Its steps are
        // short, so it stays in the root nearest the start where the dogleg's first Newton step
        // sometimes jumps past it to another exact root (on the 5% corpus the known answer came
        // back for 98.6% of the rigid systems instead of 97.4%, on the 25% corpus 87.9% against
        // 80.4%). Heavy damping converges slowly, so a run that does not finish is followed by the
        // dogleg and then by LM with the usual tau and BFGS. Above this size the dense LM is too
        // slow and the order is the dogleg's.
        order = {GCS::LevenbergMarquardt, GCS::DogLeg, GCS::LevenbergMarquardt, GCS::BFGS};
        taus = {1.0, 0.0, 1e-3, 0.0};
    }

    std::vector<double> best = start;
    std::vector<double> leastSquares = start;
    double bestRes = m0->maxDriving();
    double bestSS = m0->sumSquares();
    GCS::SolveStatus bestStatus = GCS::SolveStatus::Failed;
    GCS::Algorithm used = order.front();
    int iterations = 0;
    bool bestIsCollapsed = false;
    if (systemRows > 0 && bestRes > SuccessResidual) {
        bool first = true;
        for (size_t oi = 0; oi < order.size(); ++oi) {
            const GCS::Algorithm alg = order[oi];
            auto m = make(start);
            if (oi < taus.size() && taus[oi] > 0.0) {
                m->lmTau = taus[oi];
            }
            // even a failed run leaves its least-squares point behind (applied by solveDirect);
            // the diagnosis of conflicts needs that point
            const GCS::SolveStatus st = m->solveDirect(alg);
            const int iters = m->maxBuilds();
            const double rr = m->maxDriving();
            const double ss = m->sumSquares();
            if (ss < bestSS) {
                bestSS = ss;
                leastSquares = m->coords;
            }
            // a "solution" with a direction-carrying line shrunk to nothing is not one: go on to
            // the next algorithm, which may reach a root that keeps the line
            const bool collapsedHere = rr <= SuccessResidual && !collapsedTags(spec, start, m->coords).empty();
            if (rr < bestRes || first || (bestIsCollapsed && rr <= SuccessResidual && !collapsedHere)) {
                first = false;
                if (rr < bestRes || !(bestRes < HUGE_VAL) || (bestIsCollapsed && !collapsedHere)) {
                    best = m->coords;
                    bestRes = rr;
                    bestIsCollapsed = collapsedHere;
                }
                bestStatus = st;
                used = alg;
                iterations = iters;
            }
            if (rr <= SuccessResidual && !collapsedHere) {
                break;
            }
        }
    }
    else if (systemRows > 0) {
        // already satisfied at the start
        bestStatus = GCS::SolveStatus::Success;
    }

    std::vector<double> coords = best;
    auto mf = make(coords);
    double residual = mf->maxDriving();

    // diagnosis at the point reached
    res.dof = spec.diagnose ? unknownCount : -1;
    if (systemRows > 0 && spec.diagnose) {
        auto md = make(coords, true);
        md->prepare(used);
        res.dof = md->sys.dofsNumber();
        if (res.dof < 0) {
            res.dof = unknownCount;
        }
        GCS::VEC_I conflicting, redundant, partial;
        md->sys.getConflicting(conflicting);
        md->sys.getRedundant(redundant);
        md->sys.getPartiallyRedundant(partial);
        auto userTags = [&](const GCS::VEC_I& v) {
            std::set<int> out;
            for (int t : v) {
                if (t >= 1 && t <= nc) {
                    out.insert(spec.constraints[t - 1].tag);
                }
            }
            return std::vector<int>(out.begin(), out.end());
        };
        res.conflicting = userTags(conflicting);
        res.redundant = userTags(redundant);
        res.partiallyRedundant = userTags(partial);
    }

    // relations with no unknown left: a wrong one conflicts, a right one is redundant. These
    // are facts about the input, not heuristics, and they survive the non-convergence check below.
    std::set<int> staticConf, staticRed;
    {
        std::set<int> conf(res.conflicting.begin(), res.conflicting.end());
        std::set<int> red(res.redundant.begin(), res.redundant.end());
        for (int ci : mf->staticDriving) {
            if (mf->residualOf(ci) > SuccessResidual) {
                conf.insert(spec.constraints[ci].tag);
                staticConf.insert(spec.constraints[ci].tag);
            }
            else {
                red.insert(spec.constraints[ci].tag);
                staticRed.insert(spec.constraints[ci].tag);
            }
        }
        res.conflicting.assign(conf.begin(), conf.end());
        res.redundant.assign(red.begin(), red.end());
    }

    // A failed solve does not make the system inconsistent: Newton type solvers also fail on
    // consistent systems that have a root. Before any conflict is named, solve again from a few
    // seeded perturbed starts; if one reaches a zero residual (without collapsing a line) a
    // solution exists, the failure is a non-convergence from this start, and naming a
    // conflicting set would be a false report.
    bool nonConvergence = false;
    if (residual > SuccessResidual && systemRows > 0 && depth == 0 && retry && std::isfinite(residual)
        && spec.constraints.size() > staticConf.size()) {
        std::vector<double> centre(3, 0.0);
        for (int i = 0; i < np; ++i) {
            for (int a = 0; a < 3; ++a) {
                centre[a] += start[3 * i + a] / std::max(1, np);
            }
        }
        double size2 = 0.0;
        for (int i = 0; i < np; ++i) {
            for (int a = 0; a < 3; ++a) {
                size2 += (start[3 * i + a] - centre[a]) * (start[3 * i + a] - centre[a]) / std::max(1, np);
            }
        }
        const double size = std::max(1e-6, std::sqrt(size2));
        // (seed, fraction of the size, algorithm): a small and a larger move, Newton and LM
        const struct
        {
            unsigned seed;
            double fraction;
            const char* algorithm;
        } tries[] = {{7919u, 0.03, "dogleg"}, {104729u, 0.08, "lm"}, {1299709u, 0.15, "dogleg"},
                    {15485863u, 0.05, "dogleg"}, {32452843u, 0.10, "lm"}, {49979687u, 0.20, "dogleg"}};
        for (const auto& t : tries) {
            std::mt19937 gen(t.seed);
            std::uniform_real_distribution<double> uni(-1.0, 1.0);
            Spec moved = spec;
            moved.diagnose = false;
            moved.algorithm = t.algorithm;
            for (int i = 0; i < np; ++i) {
                if (!spec.points[i].fixed) {
                    for (int a = 0; a < 3; ++a) {
                        moved.points[i].xyz[a] += t.fraction * size * uni(gen);
                    }
                }
            }
            const Result again = solveImpl(moved, depth + 1, false);
            if (again.code == 0) {
                std::vector<double> flatAgain(3 * np);
                for (int i = 0; i < np; ++i) {
                    for (int a = 0; a < 3; ++a) {
                        flatAgain[3 * i + a] = again.coordinates[i][a];
                    }
                }
                if (collapsedTags(spec, start, flatAgain).empty()) {
                    nonConvergence = true;
                    break;
                }
            }
        }
        if (nonConvergence) {
            res.conflicting.assign(staticConf.begin(), staticConf.end());
            res.redundant.assign(staticRed.begin(), staticRed.end());
            res.partiallyRedundant.clear();
            res.collapsed.clear();
            res.reason = "no zero-residual point was reached from this start, but one exists (a start "
                         "moved at random did reach it); the relations are consistent, so no conflict "
                         "is named";
        }
    }

    // A failed solve that ended with a line carrying a direction relation shrunk to nothing
    // (still shrinking when it stopped): say so
    if (residual > SuccessResidual && systemRows > 0 && depth == 0) {
        std::set<int> shrunk;
        for (const auto* c : {&coords, &leastSquares}) {
            for (int t : collapsedTags(spec, start, *c)) {
                shrunk.insert(t);
            }
        }
        res.collapsed.assign(shrunk.begin(), shrunk.end());
    }

    // A failed solve: name the constraints that the least-squares point leaves unsatisfied (a
    // contradiction between nonlinear relations has no dependent Jacobian rows to find), take
    // them out and solve the rest, repeating until the rest is solvable. The result names every
    // constraint taken out. This means "not satisfied together with the others at the point
    // reached"; it is not a proof that a smaller set could not do.
    if (residual > SuccessResidual && systemRows > 0 && depth < 4 && !nonConvergence && spec.diagnose) {
        auto ml = make(leastSquares);
        double worst = 0.0;
        for (int ci = 0; ci < nc; ++ci) {
            if (spec.constraints[ci].driving) {
                worst = std::max(worst, ml->residualOf(ci));
            }
        }
        const double threshold = std::max(1e-6, 1e-2 * worst);
        std::set<int> named;
        for (int ci = 0; ci < nc; ++ci) {
            if (spec.constraints[ci].driving && ml->residualOf(ci) > threshold) {
                named.insert(ci);
            }
        }
        if (!named.empty() && std::isfinite(worst)) {
            Spec rest = spec;
            rest.constraints.clear();
            for (int ci = 0; ci < nc; ++ci) {
                if (!named.count(ci)) {
                    rest.constraints.push_back(spec.constraints[ci]);
                }
            }
            const Result sub = solveImpl(rest, depth + 1, false);
            std::set<int> conf(res.conflicting.begin(), res.conflicting.end());
            for (int ci : named) {
                conf.insert(spec.constraints[ci].tag);
            }
            for (int t : sub.conflicting) {
                conf.insert(t);
            }
            res.conflicting.assign(conf.begin(), conf.end());
        }
    }

    bool allFinite = true;
    for (double v : coords) {
        allFinite = allFinite && std::isfinite(v);
    }
    if (!allFinite) {
        coords = start;
        residual = HUGE_VAL;
    }
    std::vector<int> collapsed;
    if (residual <= SuccessResidual && allFinite) {
        collapsed = collapsedTags(spec, start, coords);
    }
    if (!collapsed.empty() && retry) {
        // One retry that keeps the line from collapsing: first solve the direction relations
        // alone from the start (an under-determined system, whose smallest step leaves the
        // lines near where they were), then the whole system from the point reached.
        Spec dirs = spec;
        dirs.constraints.clear();
        for (const auto& c : spec.constraints) {
            if (c.driving && isDirectionKind(c.kind)) {
                dirs.constraints.push_back(c);
            }
        }
        dirs.diagnose = false;
        const Result first = solveImpl(dirs, depth + 1, false);
        if (first.code == 0) {
            Spec second = spec;
            for (int i = 0; i < np; ++i) {
                second.points[i].xyz = first.coordinates[i];
            }
            Result again = solveImpl(second, depth + 1, false);
            std::vector<double> flatAgain(3 * np);
            for (int i = 0; i < np; ++i) {
                for (int a = 0; a < 3; ++a) {
                    flatAgain[3 * i + a] = again.coordinates[i][a];
                }
            }
            if (again.code == 0 && collapsedTags(spec, start, flatAgain).empty()) {
                again.iterations += iterations;
                // the residuals and the coordinates are those of the retry; so is the diagnosis
                return again;
            }
        }
    }
    if (!collapsed.empty()) {
        res.code = 3;
        res.status = "collapsed";
        res.collapsed = collapsed;
        res.reason = "a line carrying a direction relation shrank to zero length; the relations "
                     "hold only there";
        coords = start;
    }
    else if (residual <= SuccessResidual && allFinite) {
        res.code = 0;
        res.status = "success";
    }
    else if (allFinite && bestStatus == GCS::SolveStatus::Converged) {
        res.code = 1;
        res.status = "converged";
    }
    else {
        res.code = 2;
        res.status = "failed";
        coords = start;  // a failed solve returns the input coordinates
    }
    if (res.code != 0 && res.reason.empty() && res.conflicting.empty()) {
        res.reason = "no zero-residual point was reached from this start, and no conflicting set was found";
    }
    auto mr = make(coords);
    for (int ci = 0; ci < nc; ++ci) {
        res.residuals[ci] = mr->residualOf(ci);
    }
    res.iterations = iterations;
    res.algorithm = systemRows > 0 ? algorithmName(used) : "none";
    res.coordinates.resize(np);
    for (int i = 0; i < np; ++i) {
        for (int a = 0; a < 3; ++a) {
            res.coordinates[i][a] = coords[3 * i + a];
        }
    }
    return res;
}

}  // namespace

std::vector<RowReport> rowReports(const Spec& spec)
{
    validate(spec);
    const int np = static_cast<int>(spec.points.size());
    std::vector<double> coords(3 * np);
    for (int i = 0; i < np; ++i) {
        for (int a = 0; a < 3; ++a) {
            coords[3 * i + a] = spec.points[i].xyz[a];
        }
    }
    const std::vector<PlaneData> planes = allPlanes(spec);
    std::vector<RowReport> out;
    for (int ci = 0; ci < static_cast<int>(spec.constraints.size()); ++ci) {
        auto rows = buildRows(spec, ci, coords.data(), planes);
        for (auto& r : rows) {
            RowReport rep;
            rep.constraint = ci;
            rep.row = r->rowIndex();
            rep.error = r->error();
            std::map<int, double*> idx;
            for (double* p : r->allParams()) {
                idx[static_cast<int>(p - coords.data())] = p;
            }
            for (auto& [i, p] : idx) {
                rep.grad.emplace_back(i, r->grad(p));
            }
            out.push_back(std::move(rep));
        }
    }
    return out;
}

}  // namespace Sketch3D
