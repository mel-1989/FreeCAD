// SPDX-License-Identifier: LGPL-2.1-or-later

#include <algorithm>
#include <cmath>

#include "Relations.h"

namespace Sketch3D
{

namespace
{

const KindInfo kinds[static_cast<int>(Kind::Count)] = {
    {"fixed_coordinate", "pa", true, 1},
    {"coincident", "pp", false, 3},
    {"point_on_line", "pl", false, 2},
    {"point_on_plane", "pP", false, 1},
    {"distance_pp", "pp", true, 1},
    {"distance_pl", "pl", true, 1},
    {"distance_pplane", "pP", true, 1},
    {"midpoint", "pl", false, 3},
    {"length", "l", true, 1},
    {"equal_length", "ll", false, 1},
    {"along_x", "l", false, 2},
    {"along_y", "l", false, 2},
    {"along_z", "l", false, 2},
    {"parallel", "ll", false, 2},
    {"perpendicular", "ll", false, 1},
    {"parallel_plane", "lP", false, 1},
    {"normal_plane", "lP", false, 2},
    {"angle", "ll", true, 1},
};

// Smoothing floor under every norm: sqrt(x.x + EPS2). It keeps error and gradient finite at a
// zero-length line or coincident points (the gradient of a norm is undefined there) and moves a
// length by less than 1e-12 mm.
constexpr double EPS2 = 1e-24;

// Forward-mode dual number with one derivative direction. The analytic gradient of a row is the
// derivative of the same expression that gives its value.
struct D
{
    double v;
    double d;
};

inline D operator+(D a, D b)
{
    return {a.v + b.v, a.d + b.d};
}
inline D operator-(D a, D b)
{
    return {a.v - b.v, a.d - b.d};
}
inline D operator*(D a, D b)
{
    return {a.v * b.v, a.v * b.d + a.d * b.v};
}
inline D operator/(D a, D b)
{
    return {a.v / b.v, (a.d * b.v - a.v * b.d) / (b.v * b.v)};
}
inline D operator*(double a, D b)
{
    return {a * b.v, a * b.d};
}
inline D operator+(D a, double b)
{
    return {a.v + b, a.d};
}
inline D operator-(D a, double b)
{
    return {a.v - b, a.d};
}
inline D dsqrt(D a)
{
    const double s = std::sqrt(a.v);
    return {s, a.d / (2.0 * s)};
}
inline double dsqrt(double a)
{
    return std::sqrt(a);
}
inline double val(double a)
{
    return a;
}
inline double val(D a)
{
    return a.v;
}

template<class T>
struct V3
{
    T x, y, z;
};

template<class T>
V3<T> sub(const V3<T>& a, const V3<T>& b)
{
    return {a.x - b.x, a.y - b.y, a.z - b.z};
}
template<class T>
T dot(const V3<T>& a, const V3<T>& b)
{
    return a.x * b.x + a.y * b.y + a.z * b.z;
}
template<class T>
V3<T> cross(const V3<T>& a, const V3<T>& b)
{
    return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x};
}
template<class T>
T nrm(const V3<T>& a)
{
    return dsqrt(dot(a, a) + EPS2);
}
template<class T>
V3<T> unit(const V3<T>& a)
{
    const T n = nrm(a);
    return {a.x / n, a.y / n, a.z / n};
}
template<class T>
T comp(const V3<T>& a, int k)
{
    return k == 0 ? a.x : (k == 1 ? a.y : a.z);
}

// Orthonormal pair spanning the plane perpendicular to a. The pair is rebuilt from a on every
// call, so a relation that uses it (parallel, point on line) stays exactly "zero iff the vectors
// are parallel" wherever the lines have rotated to; there is no stale reference direction.
// The auxiliary axis e is y, or z when a points mostly along y. The switch happens where
// a.y^2 = 0.6631 |a|^2, away from the directions a drawing is built from (axes, diagonals,
// 3-4-5 slopes), and turns the pair in its plane there: the residual vector turns, its length
// does not.
template<class T>
void perpBasis(const V3<T>& a, V3<T>& u1, V3<T>& u2)
{
    const double ay = val(a.y);
    const double all = val(a.x) * val(a.x) + ay * ay + val(a.z) * val(a.z);
    const T zero = a.x - a.x;
    const T one = zero + 1.0;
    V3<T> e {zero, zero, zero};
    if (ay * ay > 0.6631 * all) {
        e.z = one;
    }
    else {
        e.y = one;
    }
    u1 = unit(cross(a, e));
    u2 = unit(cross(a, u1));
}

template<class T>
V3<T> pt(const T* x, int i)
{
    return {x[3 * i], x[3 * i + 1], x[3 * i + 2]};
}

}  // namespace

const KindInfo& kindInfo(Kind kind)
{
    return kinds[static_cast<int>(kind)];
}

bool kindFromName(const std::string& name, Kind& kind)
{
    for (int i = 0; i < static_cast<int>(Kind::Count); ++i) {
        if (name == kinds[i].name) {
            kind = static_cast<Kind>(i);
            return true;
        }
    }
    return false;
}

namespace
{

// One row of a relation. x holds the point coordinates in operand order. Equation counts and
// the geometry behind each row are in the comment above the case.
template<class T>
T evalRow(Kind kind, int row, int axis, double value, const PlaneData& pl, const T* x)
{
    const T zero = x[0] - x[0];
    const V3<T> n {zero + pl.n[0], zero + pl.n[1], zero + pl.n[2]};
    const V3<T> q0 {zero + pl.p[0], zero + pl.p[1], zero + pl.p[2]};
    switch (kind) {
        case Kind::FixedCoordinate:
            // 1 row: coordinate of the point minus the value
            return x[axis] - value;
        case Kind::Coincident:
            // 3 rows: p - q
            return x[row] - x[3 + row];
        case Kind::PointOnLine: {
            // 2 rows: components of p - a on an orthonormal basis of the plane perpendicular
            // to the line direction; both vanish exactly when p - a is parallel to the line.
            const V3<T> p = pt(x, 0), a = pt(x, 1), b = pt(x, 2);
            V3<T> u1, u2;
            perpBasis(sub(b, a), u1, u2);
            return dot(row == 0 ? u1 : u2, sub(p, a));
        }
        case Kind::PointOnPlane:
            // 1 row: signed distance of the point from the plane
            return dot(n, sub(pt(x, 0), q0));
        case Kind::DistancePP:
            // 1 row: |p - q| - d
            return nrm(sub(pt(x, 0), pt(x, 1))) - value;
        case Kind::DistancePL: {
            // 1 row: |(p - a) x (b - a)| / |b - a| - d
            const V3<T> p = pt(x, 0), a = pt(x, 1), b = pt(x, 2);
            const V3<T> d = sub(b, a);
            return nrm(cross(sub(p, a), d)) / nrm(d) - value;
        }
        case Kind::DistancePPlane: {
            // 1 row: |n.(p - p0)| - d; the smoothed absolute value keeps both sides reachable
            const T s = dot(n, sub(pt(x, 0), q0));
            return dsqrt(s * s + EPS2) - value;
        }
        case Kind::Midpoint:
            // 3 rows: p - (a + b) / 2
            return x[row] - 0.5 * (x[3 + row] + x[6 + row]);
        case Kind::Length:
            // 1 row: |b - a| - L
            return nrm(sub(pt(x, 1), pt(x, 0))) - value;
        case Kind::EqualLength:
            // 1 row: |b - a| - |d - c|
            return nrm(sub(pt(x, 1), pt(x, 0))) - nrm(sub(pt(x, 3), pt(x, 2)));
        case Kind::AlongX:
        case Kind::AlongY:
        case Kind::AlongZ: {
            // 2 rows: the two components of b - a off the axis
            const int k = kind == Kind::AlongX ? 0 : (kind == Kind::AlongY ? 1 : 2);
            const int c = row < k ? row : row + 1;
            return comp(sub(pt(x, 1), pt(x, 0)), c);
        }
        case Kind::Parallel: {
            // 2 rows: components of the second direction on a basis of the plane perpendicular
            // to the first. Rank 2 on the parallel set, so the pair is never redundant with
            // itself (the three components of the cross product have rank 2 there).
            V3<T> u1, u2;
            perpBasis(sub(pt(x, 1), pt(x, 0)), u1, u2);
            return dot(row == 0 ? u1 : u2, sub(pt(x, 3), pt(x, 2)));
        }
        case Kind::Perpendicular: {
            // 1 row: (w . v) / |v|, in mm
            const V3<T> v = sub(pt(x, 1), pt(x, 0));
            const V3<T> w = sub(pt(x, 3), pt(x, 2));
            return dot(v, w) / nrm(v);
        }
        case Kind::ParallelPlane:
            // 1 row: direction . plane normal
            return dot(sub(pt(x, 1), pt(x, 0)), n);
        case Kind::NormalPlane: {
            // 2 rows: components of the direction on a (constant) basis of the plane
            V3<T> w1, w2;
            perpBasis(n, w1, w2);
            return dot(row == 0 ? w1 : w2, sub(pt(x, 1), pt(x, 0)));
        }
        case Kind::Angle: {
            // 1 row: cos of the angle between the directions minus cos(target). Finite at zero
            // length (the norms are smoothed); the row is flat at 0 and 180 degrees.
            const V3<T> v = sub(pt(x, 1), pt(x, 0));
            const V3<T> w = sub(pt(x, 3), pt(x, 2));
            return dot(v, w) / (nrm(v) * nrm(w)) - std::cos(value);
        }
        case Kind::Count:
            break;
    }
    return zero;
}

}  // namespace

Rel::Rel(
    Kind kind,
    int rowIn,
    int axisIn,
    double valueIn,
    const PlaneData& planeIn,
    const std::vector<double*>& params,
    int tagIn
)
    : kindValue(kind)
    , row(rowIn)
    , axis(axisIn)
    , value(valueIn)
    , plane(planeIn)
{
    pvec = params;
    origpvec = pvec;
    tag = tagIn;
    rescale();
}

double Rel::evalAt(const GCS::VEC_pD& params) const
{
    double x[12];
    const size_t n = std::min<size_t>(params.size(), 12);
    for (size_t i = 0; i < n; ++i) {
        x[i] = *params[i];
    }
    return evalRow<double>(kindValue, row, axis, value, plane, x);
}

double Rel::rawError()
{
    // the real coordinates, whatever the solver has redirected pvec to
    return evalAt(origpvec);
}

double Rel::error()
{
    lastWasGrad = false;
    return scale * evalAt(pvec);
}

double Rel::grad(double* param)
{
    if (findParamInPvec(param) == -1) {
        return 0.0;
    }
    if (!lastWasGrad) {
        ++builds;
    }
    lastWasGrad = true;
    D x[12];
    const size_t n = std::min<size_t>(pvec.size(), 12);
    for (size_t i = 0; i < n; ++i) {
        x[i] = {*pvec[i], pvec[i] == param ? 1.0 : 0.0};
    }
    return scale * evalRow<D>(kindValue, row, axis, value, plane, x).d;
}

}  // namespace Sketch3D
