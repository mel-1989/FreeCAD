// SPDX-License-Identifier: LGPL-2.1-or-later

#include <algorithm>
#include <cmath>
#include <cstring>
#include <map>
#include <numbers>
#include <memory>
#include <set>

#include <Mod/Sketcher/App/planegcs/GCS.h>

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

Result solve(const Spec& spec)
{
    validate(spec);
    const int np = static_cast<int>(spec.points.size());
    const int nc = static_cast<int>(spec.constraints.size());
    std::vector<double> coords(3 * np);
    for (int i = 0; i < np; ++i) {
        for (int a = 0; a < 3; ++a) {
            coords[3 * i + a] = spec.points[i].xyz[a];
        }
    }
    const std::vector<double> start = coords;
    const std::vector<PlaneData> planes = allPlanes(spec);

    std::set<double*> unknownSet;
    GCS::VEC_pD unknowns;
    for (int i = 0; i < np; ++i) {
        if (!spec.points[i].fixed) {
            for (int a = 0; a < 3; ++a) {
                unknowns.push_back(&coords[3 * i + a]);
                unknownSet.insert(&coords[3 * i + a]);
            }
        }
    }

    Result res;
    res.residuals.assign(nc, 0.0);
    std::vector<std::vector<std::unique_ptr<Rel>>> rowsOf(nc);  // static and driven relations
    std::vector<std::vector<Rel*>> inSystem(nc);
    std::vector<int> staticDriving;
    GCS::System sys;
    int systemRows = 0;

    for (int ci = 0; ci < nc; ++ci) {
        auto rows = buildRows(spec, ci, coords.data(), planes);
        bool touchesUnknown = false;
        for (double* p : rows.front()->allParams()) {
            if (unknownSet.count(p)) {
                touchesUnknown = true;
            }
        }
        if (!spec.constraints[ci].driving) {
            rowsOf[ci] = std::move(rows);
        }
        else if (!touchesUnknown) {
            rowsOf[ci] = std::move(rows);
            staticDriving.push_back(ci);
        }
        else {
            for (auto& r : rows) {
                inSystem[ci].push_back(r.get());
                sys.addConstraint(r.release());
                ++systemRows;
            }
        }
    }

    auto residualOf = [&](int ci) {
        double m = 0.0;
        if (!inSystem[ci].empty()) {
            for (Rel* r : inSystem[ci]) {
                const double e = std::fabs(r->rawError());
                m = std::isnan(e) ? HUGE_VAL : std::max(m, e);
            }
            return m;
        }
        return maxAbsRow(rowsOf[ci]);
    };
    auto maxDriving = [&]() {
        double m = 0.0;
        for (int ci = 0; ci < nc; ++ci) {
            if (spec.constraints[ci].driving) {
                m = std::max(m, residualOf(ci));
            }
        }
        return m;
    };

    std::vector<GCS::Algorithm> order;
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

    GCS::Algorithm used = order.front();
    GCS::SolveStatus status = GCS::SolveStatus::Success;
    double residual = maxDriving();
    int iterations = 0;
    bool diagnosed = false;

    if (systemRows > 0) {
        sys.declareUnknowns(unknowns);
        GCS::VEC_pD driven;
        sys.declareDrivenParams(driven);
        // best attempt so far: lowest residual
        std::vector<double> best = start;
        double bestRes = HUGE_VAL;
        GCS::SolveStatus bestStatus = GCS::SolveStatus::Failed;
        GCS::Algorithm bestAlg = order.front();
        int bestIter = 0;
        for (GCS::Algorithm alg : order) {
            coords = start;
            sys.initSolution(alg);
            std::vector<int> before;
            for (int ci = 0; ci < nc; ++ci) {
                for (Rel* r : inSystem[ci]) {
                    before.push_back(r->jacobianBuilds());
                }
            }
            const GCS::SolveStatus st = sys.solve(alg);
            int iters = 0;
            {
                size_t k = 0;
                for (int ci = 0; ci < nc; ++ci) {
                    for (Rel* r : inSystem[ci]) {
                        iters = std::max(iters, r->jacobianBuilds() - before[k++]);
                    }
                }
            }
            if (st == GCS::SolveStatus::Success || st == GCS::SolveStatus::Converged) {
                sys.applySolution();
            }
            const double rr = maxDriving();
            if (rr < bestRes) {
                best = coords;
                bestRes = rr;
                bestStatus = st;
                bestAlg = alg;
                bestIter = iters;
            }
            if (rr <= SuccessResidual) {
                break;
            }
        }
        coords = best;
        residual = maxDriving();
        status = bestStatus;
        used = bestAlg;
        iterations = bestIter;

        // diagnosis at the point reached
        sys.invalidatedDiagnosis();
        sys.initSolution(used);
        res.dof = sys.dofsNumber();
        diagnosed = res.dof >= 0;
        GCS::VEC_I conflicting, redundant, partial;
        sys.getConflicting(conflicting);
        sys.getRedundant(redundant);
        sys.getPartiallyRedundant(partial);
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
    if (!diagnosed) {
        res.dof = static_cast<int>(unknowns.size());
    }

    // relations with no unknown left: a wrong one conflicts, a right one is redundant
    {
        std::set<int> conf(res.conflicting.begin(), res.conflicting.end());
        std::set<int> red(res.redundant.begin(), res.redundant.end());
        for (int ci : staticDriving) {
            if (residualOf(ci) > SuccessResidual) {
                conf.insert(spec.constraints[ci].tag);
            }
            else {
                red.insert(spec.constraints[ci].tag);
            }
        }
        res.conflicting.assign(conf.begin(), conf.end());
        res.redundant.assign(red.begin(), red.end());
    }

    for (int ci = 0; ci < nc; ++ci) {
        res.residuals[ci] = residualOf(ci);
    }
    bool allFinite = true;
    for (double v : coords) {
        allFinite = allFinite && std::isfinite(v);
    }
    if (!allFinite) {
        coords = start;
        residual = HUGE_VAL;
    }
    if (residual <= SuccessResidual && allFinite) {
        res.code = 0;
        res.status = "success";
    }
    else if (allFinite && status == GCS::SolveStatus::Converged) {
        res.code = 1;
        res.status = "converged";
    }
    else {
        res.code = 2;
        res.status = "failed";
        if (!(residual < HUGE_VAL) || status == GCS::SolveStatus::Failed) {
            coords = start;
            for (int ci = 0; ci < nc; ++ci) {
                res.residuals[ci] = residualOf(ci);
            }
        }
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
