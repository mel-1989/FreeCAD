// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <array>
#include <stdexcept>
#include <string>
#include <vector>

#include "Relations.h"
#include "Sketch3DGlobal.h"

namespace Sketch3D
{

// A malformed spec. position is the index of the offending constraint, or -1 when the fault is
// not in a constraint (a point, a line, a plane, the algorithm name).
class SpecError: public std::runtime_error
{
public:
    SpecError(int positionIn, const std::string& what)
        : std::runtime_error(what)
        , position(positionIn)
    {}
    int position;
};

struct PointSpec
{
    std::array<double, 3> xyz {};
    bool fixed = false;
};

struct PlaneSpec
{
    std::array<double, 3> point {};
    std::array<double, 3> normal {};
};

struct ConstraintSpec
{
    Kind kind = Kind::Coincident;
    std::vector<int> operands;
    double value = 0.0;
    bool driving = true;
    int tag = 0;
};

struct Spec
{
    std::vector<PointSpec> points;
    std::vector<std::array<int, 2>> lines;
    std::vector<PlaneSpec> planes;
    std::vector<ConstraintSpec> constraints;
    std::string algorithm;  // "", "dogleg", "lm" or "bfgs"
    // false skips the diagnosis (a sparse QR of the Jacobian, most of the time of a large solve):
    // dof is then -1 and the redundant lists stay empty. For interactive solves.
    bool diagnose = true;
};

struct Result
{
    int code = 2;  // 0 success, 1 converged without zero residual, 2 failed
    std::string status;
    std::vector<std::array<double, 3>> coordinates;
    int dof = 0;
    std::vector<int> conflicting;
    std::vector<int> redundant;
    std::vector<int> partiallyRedundant;
    std::vector<double> residuals;  // per constraint, largest absolute row value
    int iterations = 0;
    std::string algorithm;
};

// One row of one constraint at the spec's own coordinates (test hook).
struct RowReport
{
    int constraint = 0;
    int row = 0;
    double error = 0.0;
    std::vector<std::pair<int, double>> grad;  // coordinate index (3 * point + axis) and value
};

// Residual under which a solve counts as a success.
constexpr double SuccessResidual = 1e-9;

// Throws SpecError. Does not change the spec.
void validate(const Spec& spec);

Result solve(const Spec& spec);

std::vector<RowReport> rowReports(const Spec& spec);

}  // namespace Sketch3D
