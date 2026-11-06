// SPDX-License-Identifier: LGPL-2.1-or-later

#include <algorithm>
#include <cmath>
#include <cstring>
#include <map>
#include <numbers>
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

Result solveImpl(const Spec& spec, int depth);

}  // namespace

Result solve(const Spec& spec)
{
    validate(spec);
    return solveImpl(spec, 0);
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
        const GCS::SolveStatus st = sys.solve(sub.get(), alg, false);
        sub->applySolution();
        return st;
    }

    void tune()
    {
        // PlaneGCS stops at an error of 1e-10 by default, which leaves coordinates a few 1e-9
        // off an ill-conditioned solution; the rows here are cheap, so ask for rounding level.
        sys.DL_tolf = 1e-22;
        sys.LM_eps = 1e-13;
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
        sys.initSolution(alg);
    }
};

Result solveImpl(const Spec& spec, int depth)
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

    std::vector<double> best = start;
    std::vector<double> leastSquares = start;
    double bestRes = m0->maxDriving();
    double bestSS = m0->sumSquares();
    GCS::SolveStatus bestStatus = GCS::SolveStatus::Failed;
    GCS::Algorithm used = order.front();
    int iterations = 0;
    if (systemRows > 0 && bestRes > SuccessResidual) {
        bool first = true;
        for (GCS::Algorithm alg : order) {
            auto m = make(start);
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
            if (rr < bestRes || first) {
                first = false;
                if (rr < bestRes || !(bestRes < HUGE_VAL)) {
                    best = m->coords;
                    bestRes = rr;
                }
                bestStatus = st;
                used = alg;
                iterations = iters;
            }
            if (rr <= SuccessResidual) {
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

    // relations with no unknown left: a wrong one conflicts, a right one is redundant
    {
        std::set<int> conf(res.conflicting.begin(), res.conflicting.end());
        std::set<int> red(res.redundant.begin(), res.redundant.end());
        for (int ci : mf->staticDriving) {
            if (mf->residualOf(ci) > SuccessResidual) {
                conf.insert(spec.constraints[ci].tag);
            }
            else {
                red.insert(spec.constraints[ci].tag);
            }
        }
        res.conflicting.assign(conf.begin(), conf.end());
        res.redundant.assign(red.begin(), red.end());
    }

    // A failed solve: name the constraints that the least-squares point leaves unsatisfied (a
    // contradiction between nonlinear relations has no dependent Jacobian rows to find), take
    // them out and solve the rest, repeating until the rest is solvable. The result names every
    // constraint taken out. This means "not satisfied together with the others at the point
    // reached"; it is not a proof that a smaller set could not do.
    if (residual > SuccessResidual && systemRows > 0 && depth < 4) {
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
            const Result sub = solveImpl(rest, depth + 1);
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
    if (residual <= SuccessResidual && allFinite) {
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
