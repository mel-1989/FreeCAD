// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2011 Konstantinos Poulios <logari81@gmail.com>          *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public           *
 *   License as published by the Free Software Foundation; either          *
 *   version 2 of the License, or (at your option) any later version.      *
 *                                                                         *
 *   This library  is distributed in the hope that it will be useful,      *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
 *   GNU Library General Public License for more details.                  *
 *                                                                         *
 *   You should have received a copy of the GNU Library General Public     *
 *   License along with this library; see the file COPYING.LIB. If not,    *
 *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
 *   Suite 330, Boston, MA  02111-1307, USA                                *
 *                                                                         *
 ***************************************************************************/

#ifdef _MSC_VER
# pragma warning(disable : 4244)
#endif

#include <Eigen/QR>
#include <Eigen/SVD>
#include <atomic>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <mutex>
#include <vector>

#include "qp_eq.h"

using namespace Eigen;

static int qp_eq_dense(MatrixXd& H, VectorXd& g, MatrixXd& A, VectorXd& c, VectorXd& x, MatrixXd& Y, MatrixXd& Z);

// minimizes ( 0.5 * x^T * H * x + g^T * x ) under the condition ( A*x + c = 0 )
// it returns the solution in x, the row-space of A in Y, and the null space of A in Z
//
// Large systems whose Jacobian is a few dense columns beside many near-diagonal ones (the frame
// of a sketch block: each row of A holds one member parameter and at most X, Y and the angle)
// take a fast path that never forms Q. Everything else, and every doubt, takes the dense path
// below in the same call, so a refusal is always the dense path's refusal.
//
// Fast path, with A = [As | Ad] (As the sparse columns, Ad the few dense ones), Gram-free:
//   As is split into small connected blocks (columns that share a row); each block has a dense
//   pivoted QR and so a pseudo-inverse, and As^+ is the block-diagonal assembly of them.
//   G = As^+ Ad,  At = Ad - As G (the part of Ad outside the range of As), At = U S V^T.
//   rank(A) = n_s + rank(At) must equal m.  A right inverse is R = [As^+ - G Rd; Rd] with
//   Rd = V S^-1 U^T, a null basis is Z = [-G V2; V2] (orthonormalised), and Y = (I - Z Z^T) R
//   is the unique minimum-norm right inverse, i.e. A Y = I and Y lies in the row space of A.
//   (Nocedal and Wright, Numerical Optimization, ch. 16 null-space method and ch. 18 SQP; the
//   Schur complement of the bordered system [As Ad], which is what Sherman-Morrison-Woodbury
//   gives for the rank-n_d correction.)

namespace
{
constexpr Index kFastMinParams = 192;     // below this the dense path runs, untouched
constexpr int kDenseColumnNnz = 16;       // a column with more nonzeros is a dense column
constexpr Index kMaxDenseColumns = 32;
constexpr Index kMaxBlockColumns = 128;   // a bigger connected block of As sends the call to the dense path
constexpr double kBlockPivotRatio = 1e-9;
constexpr double kSvdRank = 1e-6;         // singular value of the scaled At above which it counts as rank
constexpr double kSvdNull = 1e-10;        // and below which it counts as zero; between is doubt
constexpr double kResidualTol = 1e-9;

std::atomic<long> gFast {0};
std::atomic<long> gFallback {0};
std::atomic<long> gDense {0};
std::atomic<bool> gAudit {false};
std::atomic<double> gNoise {0.0};
thread_local int gLastPath = 0;
thread_local int gLastWhy = 0;

std::atomic<bool>& forceFlag()
{
    static std::atomic<bool> flag {[] {
        const char* env = std::getenv("FREECAD_GCS_QP_FORCE_DENSE");
        return env != nullptr && env[0] != '\0' && env[0] != '0';
    }()};
    return flag;
}

std::mutex gAuditMutex;
QpEq::Audit gAuditData;

struct Block
{
    Index colStart, colCount, rowStart, rowCount;
    size_t pinvOffset;  // column-major colCount x rowCount
};

// Why the fast path gave up (Ok: it answered). Kept so that tests can tell which guard fired.
enum class Why : int
{
    Ok = 0,
    Input = 1,         // a non-finite entry, or sizes that disagree
    Structure = 2,     // not a few dense columns beside small blocks
    BlockRank = 3,     // a block of the sparse part is rank deficient or badly conditioned
    Doubt = 4,         // a singular value of the dense part is neither clearly zero nor clearly rank
    RankMismatch = 5,  // the rows are not independent, or over-determined
    Residual = 6       // an answer failed its residual check
};

// Fills the outputs and returns Ok when the fast path is sure of its answer.
Why qp_eq_fast(
    const MatrixXd& H,
    const VectorXd& g,
    const MatrixXd& A,
    const VectorXd& c,
    VectorXd& xOut,
    MatrixXd& YOut,
    MatrixXd& ZOut,
    bool& zWritten
)
{
    const Index m = A.rows();
    const Index n = A.cols();
    if (c.size() != m || g.size() != n || H.rows() != n || H.cols() != n) {
        return Why::Input;
    }
    if (!c.allFinite() || !g.allFinite()) {
        return Why::Input;
    }

    // one pass over A: nonzeros per column, entries kept (the sparse view used for every product)
    std::vector<Index> colStart(n + 1, 0);
    std::vector<int> entRow;
    std::vector<double> entVal;
    entRow.reserve(static_cast<size_t>(8 * n + 64));
    entVal.reserve(static_cast<size_t>(8 * n + 64));
    for (Index j = 0; j < n; ++j) {
        const double* col = A.data() + j * m;
        for (Index i = 0; i < m; ++i) {
            const double v = col[i];
            if (v != 0.0) {
                if (!std::isfinite(v)) {
                    return Why::Input;
                }
                entRow.push_back(static_cast<int>(i));
                entVal.push_back(v);
            }
        }
        colStart[j + 1] = static_cast<Index>(entRow.size());
    }

    std::vector<Index> sIdx, dIdx;
    std::vector<Index> localOf(n, -1);
    for (Index j = 0; j < n; ++j) {
        if (colStart[j + 1] - colStart[j] > kDenseColumnNnz) {
            localOf[j] = static_cast<Index>(dIdx.size());
            dIdx.push_back(j);
        }
        else {
            localOf[j] = static_cast<Index>(sIdx.size());
            sIdx.push_back(j);
        }
    }
    const Index nd = static_cast<Index>(dIdx.size());
    const Index ns = static_cast<Index>(sIdx.size());
    if (nd < 1 || nd > kMaxDenseColumns || m < ns) {
        return Why::Structure;
    }

    // connected blocks of the sparse columns (columns sharing a row)
    std::vector<Index> parent(ns);
    for (Index k = 0; k < ns; ++k) {
        parent[k] = k;
    }
    auto find = [&parent](Index k) {
        while (parent[k] != k) {
            parent[k] = parent[parent[k]];
            k = parent[k];
        }
        return k;
    };
    std::vector<Index> rowFirst(m, -1);
    for (Index k = 0; k < ns; ++k) {
        const Index j = sIdx[k];
        for (Index e = colStart[j]; e < colStart[j + 1]; ++e) {
            const int i = entRow[e];
            if (rowFirst[i] < 0) {
                rowFirst[i] = k;
            }
            else {
                const Index a = find(k);
                const Index b = find(rowFirst[i]);
                if (a != b) {
                    parent[a] = b;
                }
            }
        }
    }
    std::vector<Index> blockOfRoot(ns, -1);
    std::vector<Index> blockCols, blockRows;  // counts, then offsets
    Index nBlocks = 0;
    std::vector<Index> blockOfCol(ns);
    for (Index k = 0; k < ns; ++k) {
        const Index r = find(k);
        if (blockOfRoot[r] < 0) {
            blockOfRoot[r] = nBlocks++;
            blockCols.push_back(0);
            blockRows.push_back(0);
        }
        blockOfCol[k] = blockOfRoot[r];
        if (++blockCols[blockOfCol[k]] > kMaxBlockColumns) {
            return Why::Structure;
        }
    }
    for (Index i = 0; i < m; ++i) {
        if (rowFirst[i] >= 0) {
            ++blockRows[blockOfRoot[find(rowFirst[i])]];
        }
    }
    std::vector<Block> blocks(nBlocks);
    std::vector<Index> colsFlat(ns), rowsFlat;
    {
        Index cOff = 0, rOff = 0;
        size_t pOff = 0;
        for (Index b = 0; b < nBlocks; ++b) {
            if (blockRows[b] < blockCols[b]) {
                return Why::Structure;  // As cannot have full column rank
            }
            blocks[b] = {cOff, blockCols[b], rOff, blockRows[b], pOff};
            cOff += blockCols[b];
            rOff += blockRows[b];
            pOff += static_cast<size_t>(blockCols[b] * blockRows[b]);
        }
        rowsFlat.resize(rOff);
        std::vector<Index> cFill(nBlocks, 0), rFill(nBlocks, 0);
        for (Index k = 0; k < ns; ++k) {
            const Index b = blockOfCol[k];
            colsFlat[blocks[b].colStart + cFill[b]++] = k;
        }
        for (Index i = 0; i < m; ++i) {
            if (rowFirst[i] >= 0) {
                const Index b = blockOfRoot[find(rowFirst[i])];
                rowsFlat[blocks[b].rowStart + rFill[b]++] = i;
            }
        }
    }

    // pseudo-inverse of every block
    std::vector<double> pinvFlat;
    {
        size_t total = 0;
        for (const Block& b : blocks) {
            total += static_cast<size_t>(b.colCount * b.rowCount);
        }
        pinvFlat.resize(total);
    }
    for (const Block& b : blocks) {
        const Index s = b.colCount;
        const Index r = b.rowCount;
        const Index* cols = &colsFlat[b.colStart];
        const Index* rows = rowsFlat.empty() ? nullptr : &rowsFlat[b.rowStart];
        double* pv = pinvFlat.data() + b.pinvOffset;
        if (s == 1) {
            double nn = 0;
            const Index j = sIdx[cols[0]];
            for (Index ri = 0; ri < r; ++ri) {
                const double a = A(rows[ri], j);
                nn += a * a;
            }
            if (!(nn > 0.0) || !std::isfinite(nn)) {
                return Why::BlockRank;
            }
            for (Index ri = 0; ri < r; ++ri) {
                pv[ri] = A(rows[ri], j) / nn;
            }
        }
        else {
            MatrixXd M(r, s);
            for (Index ci = 0; ci < s; ++ci) {
                for (Index ri = 0; ri < r; ++ri) {
                    M(ri, ci) = A(rows[ri], sIdx[cols[ci]]);
                }
            }
            ColPivHouseholderQR<MatrixXd> qr(M);
            const VectorXd d = qr.matrixQR().diagonal().cwiseAbs();
            const double dmax = d.maxCoeff();
            if (!(dmax > 0.0) || !(d.minCoeff() >= kBlockPivotRatio * dmax)) {
                return Why::BlockRank;
            }
            const MatrixXd P = qr.solve(MatrixXd::Identity(r, r));
            if (!P.allFinite()) {
                return Why::BlockRank;
            }
            Map<MatrixXd>(pv, s, r) = P;
        }
    }

    // dense columns, G = As^+ Ad, At = Ad - As G
    MatrixXd Ad(m, nd);
    for (Index jj = 0; jj < nd; ++jj) {
        Ad.col(jj) = A.col(dIdx[jj]);
    }
    VectorXd dscale(nd);
    for (Index jj = 0; jj < nd; ++jj) {
        dscale(jj) = Ad.col(jj).norm();
        if (!(dscale(jj) > 0.0) || !std::isfinite(dscale(jj))) {
            return Why::Structure;
        }
    }
    MatrixXd G = MatrixXd::Zero(ns, nd);
    MatrixXd Atil = Ad;
    for (const Block& b : blocks) {
        const Index s = b.colCount;
        const Index r = b.rowCount;
        const Index* cols = &colsFlat[b.colStart];
        const Index* rows = rowsFlat.empty() ? nullptr : &rowsFlat[b.rowStart];
        const double* pv = pinvFlat.data() + b.pinvOffset;
        if (s == 1) {
            const Index j = sIdx[cols[0]];
            for (Index jj = 0; jj < nd; ++jj) {
                double acc = 0;
                for (Index ri = 0; ri < r; ++ri) {
                    acc += pv[ri] * Ad(rows[ri], jj);
                }
                G(cols[0], jj) = acc;
                for (Index ri = 0; ri < r; ++ri) {
                    Atil(rows[ri], jj) -= A(rows[ri], j) * acc;
                }
            }
        }
        else {
            MatrixXd Adr(r, nd);
            for (Index jj = 0; jj < nd; ++jj) {
                for (Index ri = 0; ri < r; ++ri) {
                    Adr(ri, jj) = Ad(rows[ri], jj);
                }
            }
            const MatrixXd Gb = Map<const MatrixXd>(pv, s, r) * Adr;
            MatrixXd M(r, s);
            for (Index ci = 0; ci < s; ++ci) {
                for (Index ri = 0; ri < r; ++ri) {
                    M(ri, ci) = A(rows[ri], sIdx[cols[ci]]);
                }
            }
            const MatrixXd MG = M * Gb;
            for (Index jj = 0; jj < nd; ++jj) {
                for (Index ci = 0; ci < s; ++ci) {
                    G(cols[ci], jj) = Gb(ci, jj);
                }
                for (Index ri = 0; ri < r; ++ri) {
                    Atil(rows[ri], jj) -= MG(ri, jj);
                }
            }
        }
    }
    for (Index jj = 0; jj < nd; ++jj) {
        Atil.col(jj) /= dscale(jj);
    }
    if (!Atil.allFinite() || !G.allFinite()) {
        return Why::Residual;
    }

    JacobiSVD<MatrixXd> svd(Atil, ComputeThinU | ComputeThinV);
    const VectorXd sv = svd.singularValues();
    Index kr = 0;
    for (Index i = 0; i < sv.size(); ++i) {
        if (sv(i) > kSvdRank) {
            ++kr;
        }
        else if (sv(i) >= kSvdNull) {
            return Why::Doubt;  // doubt: neither clearly rank nor clearly zero
        }
    }
    if (ns + kr != m) {
        return Why::RankMismatch;  // rank deficient, conflicting, or over-determined: the dense path decides
    }
    const Index nu = nd - kr;  // = n - m
    if (nu + m != n) {
        return Why::RankMismatch;
    }

    const VectorXd dinv = dscale.cwiseInverse();
    MatrixXd Rd = MatrixXd::Zero(nd, m);
    if (kr > 0) {
        Rd = dinv.asDiagonal()
             * (svd.matrixV().leftCols(kr) * sv.head(kr).cwiseInverse().asDiagonal()
                * svd.matrixU().leftCols(kr).transpose());
    }

    // null basis, orthonormalised
    MatrixXd Zo;
    MatrixXd Zos(ns, nu), Zod(nd, nu);
    if (nu > 0) {
        const MatrixXd Zd = dinv.asDiagonal() * svd.matrixV().rightCols(nu);
        const MatrixXd Zs = -(G * Zd);
        MatrixXd Zraw(n, nu);
        for (Index jj = 0; jj < nd; ++jj) {
            Zraw.row(dIdx[jj]) = Zd.row(jj);
        }
        for (Index k = 0; k < ns; ++k) {
            Zraw.row(sIdx[k]) = Zs.row(k);
        }
        HouseholderQR<MatrixXd> zq(Zraw);
        Zo = zq.householderQ() * MatrixXd::Identity(n, nu);
        for (Index jj = 0; jj < nd; ++jj) {
            Zod.row(jj) = Zo.row(dIdx[jj]);
        }
        for (Index k = 0; k < ns; ++k) {
            Zos.row(k) = Zo.row(sIdx[k]);
        }
        if (!Zo.allFinite()) {
            return Why::Residual;
        }
    }

    // Y = Left * Rhs + (block pseudo-inverses scattered into the sparse rows)
    MatrixXd Rhs(nd + nu, m);
    Rhs.topRows(nd) = Rd;
    MatrixXd Left(n, nd + nu);
    if (nu > 0) {
        MatrixXd TA = MatrixXd::Zero(nu, m);
        for (const Block& b : blocks) {
            const Index s = b.colCount;
            const Index r = b.rowCount;
            const Index* cols = &colsFlat[b.colStart];
            const Index* rows = rowsFlat.empty() ? nullptr : &rowsFlat[b.rowStart];
            const double* pv = pinvFlat.data() + b.pinvOffset;
            if (s == 1) {
                for (Index ri = 0; ri < r; ++ri) {
                    for (Index q = 0; q < nu; ++q) {
                        TA(q, rows[ri]) += Zos(cols[0], q) * pv[ri];
                    }
                }
            }
            else {
                MatrixXd Zb(s, nu);
                for (Index ci = 0; ci < s; ++ci) {
                    Zb.row(ci) = Zos.row(cols[ci]);
                }
                const MatrixXd TB = Zb.transpose() * Map<const MatrixXd>(pv, s, r);
                for (Index ri = 0; ri < r; ++ri) {
                    TA.col(rows[ri]) += TB.col(ri);
                }
            }
        }
        const MatrixXd W = Zos.transpose() * G - Zod.transpose();
        Rhs.bottomRows(nu) = TA - W * Rd;
    }
    Left.setZero();
    for (Index k = 0; k < ns; ++k) {
        Left.block(sIdx[k], 0, 1, nd) = -G.row(k);
        if (nu > 0) {
            Left.block(sIdx[k], nd, 1, nu) = -Zos.row(k);
        }
    }
    for (Index jj = 0; jj < nd; ++jj) {
        Left(dIdx[jj], jj) = 1.0;
        if (nu > 0) {
            Left.block(dIdx[jj], nd, 1, nu) = -Zod.row(jj);
        }
    }
    MatrixXd Y(n, m);
    Y.noalias() = Left * Rhs;
    for (const Block& b : blocks) {
        const Index s = b.colCount;
        const Index r = b.rowCount;
        const Index* cols = &colsFlat[b.colStart];
        const Index* rows = rowsFlat.empty() ? nullptr : &rowsFlat[b.rowStart];
        const double* pv = pinvFlat.data() + b.pinvOffset;
        for (Index ri = 0; ri < r; ++ri) {
            for (Index ci = 0; ci < s; ++ci) {
                Y(sIdx[cols[ci]], rows[ri]) += pv[ci + ri * s];
            }
        }
    }

    // products with the sparse view of A: out += A w, absOut += |A| |w|
    auto applyA = [&](const double* w, VectorXd& out, VectorXd& absOut) {
        out.setZero(m);
        absOut.setZero(m);
        for (Index j = 0; j < n; ++j) {
            const double wj = w[j];
            if (wj == 0.0) {
                continue;
            }
            for (Index e = colStart[j]; e < colStart[j + 1]; ++e) {
                out(entRow[e]) += entVal[e] * wj;
                absOut(entRow[e]) += std::fabs(entVal[e] * wj);
            }
        }
    };
    auto closeTo = [](const VectorXd& r, const VectorXd& scale, const VectorXd& ref) {
        for (Index i = 0; i < r.size(); ++i) {
            if (!(std::fabs(r(i)) <= kResidualTol * (std::fabs(ref(i)) + scale(i)) + 1e-14)) {
                return false;
            }
        }
        return true;
    };

    VectorXd out, absOut;
    // a probe vector: A Y v = v, and Y v lies in the row space (Z^T Y v = 0)
    {
        VectorXd v(m);
        unsigned int state = 2463534242u;
        for (Index i = 0; i < m; ++i) {
            state = state * 1664525u + 1013904223u;
            v(i) = (state & 0x100u) ? 1.0 : -1.0;
        }
        const VectorXd w = Y * v;
        if (!w.allFinite()) {
            return Why::Residual;
        }
        applyA(w.data(), out, absOut);
        if (!closeTo(out - v, absOut, v)) {
            return Why::Residual;
        }
        if (nu > 0) {
            const VectorXd zw = Zo.transpose() * w;
            if (!(zw.lpNorm<Infinity>() <= kResidualTol * (1.0 + w.lpNorm<Infinity>()))) {
                return Why::Residual;
            }
        }
    }
    for (Index q = 0; q < nu; ++q) {
        applyA(Zo.col(q).data(), out, absOut);
        if (!closeTo(out, absOut, VectorXd::Zero(m))) {
            return Why::Residual;
        }
    }

    // the QP
    const VectorXd Yc = Y * c;
    VectorXd x = -Yc;
    if (nu > 0) {
        const VectorXd HYc = H * Yc;
        const MatrixXd ZTHZ = Zo.transpose() * (H * Zo);
        const VectorXd rhs = Zo.transpose() * (HYc - g);
        ColPivHouseholderQR<MatrixXd> qz(ZTHZ);
        const VectorXd dz = qz.matrixQR().diagonal().cwiseAbs();
        const double dzmax = dz.maxCoeff();
        if (!(dzmax > 0.0) || !(dz.minCoeff() >= kBlockPivotRatio * dzmax)) {
            return Why::Residual;
        }
        const VectorXd y = qz.solve(rhs);
        if (!y.allFinite()) {
            return Why::Residual;
        }
        const VectorXd ry = ZTHZ * y - rhs;
        if (!(ry.lpNorm<Infinity>()
              <= kResidualTol * (rhs.lpNorm<Infinity>() + ZTHZ.cwiseAbs().rowwise().sum().maxCoeff() * y.lpNorm<Infinity>()) + 1e-14)) {
            return Why::Residual;
        }
        x += Zo * y;
    }
    if (!x.allFinite()) {
        return Why::Residual;
    }
    applyA(x.data(), out, absOut);
    if (!closeTo(out + c, absOut, c)) {
        return Why::Residual;
    }

    xOut = x;
    YOut = std::move(Y);
    if (nu > 0) {
        ZOut = std::move(Zo);
        zWritten = true;
    }
    return Why::Ok;
}

template<class Derived>
double maxAbs(const MatrixBase<Derived>& M)
{
    return M.size() == 0 ? 0.0 : M.cwiseAbs().maxCoeff();
}

// Fast against dense on the same input; used by the audit and by the probe.
QpEq::Audit compare(
    MatrixXd& H,
    VectorXd& g,
    MatrixXd& A,
    VectorXd& c,
    const VectorXd& x,
    const MatrixXd& Y,
    const MatrixXd& Z,
    bool haveZ,
    int& statusDense,
    VectorXd& xDense
)
{
    QpEq::Audit a;
    a.calls = 1;
    MatrixXd Yd, Zd;
    statusDense = qp_eq_dense(H, g, A, c, xDense, Yd, Zd);
    const Index m = A.rows();
    a.resAx = maxAbs(A * x + c);
    a.resAY = maxAbs(A * Y - MatrixXd::Identity(m, m));
    const VectorXd stat = H * x + g;
    if (haveZ && Z.cols() > 0) {
        a.resAZ = maxAbs(A * Z);
        a.resZ = maxAbs(Z.transpose() * stat);
        a.resYZ = maxAbs(Z.transpose() * Y);
    }
    if (statusDense == 0) {
        a.dx = maxAbs(x - xDense);
        a.dY = Yd.rows() == Y.rows() && Yd.cols() == Y.cols() ? maxAbs(Y - Yd) : 1e300;
        a.dLambda = maxAbs(Y.transpose() * stat - Yd.transpose() * (H * xDense + g));
    }
    return a;
}

void merge(QpEq::Audit& into, const QpEq::Audit& a)
{
    into.calls += a.calls;
    into.resAx = std::max(into.resAx, a.resAx);
    into.resAY = std::max(into.resAY, a.resAY);
    into.resAZ = std::max(into.resAZ, a.resAZ);
    into.resZ = std::max(into.resZ, a.resZ);
    into.resYZ = std::max(into.resYZ, a.resYZ);
    into.dx = std::max(into.dx, a.dx);
    into.dY = std::max(into.dY, a.dY);
    into.dLambda = std::max(into.dLambda, a.dLambda);
}

}  // namespace

// The reference: dense QR of A^T.
static int qp_eq_dense(MatrixXd& H, VectorXd& g, MatrixXd& A, VectorXd& c, VectorXd& x, MatrixXd& Y, MatrixXd& Z)
{
    FullPivHouseholderQR<MatrixXd> qrAT(A.transpose());
    MatrixXd Q = qrAT.matrixQ();

    size_t params_num = qrAT.rows();
    size_t constr_num = qrAT.cols();
    size_t rank = qrAT.rank();

    if (rank != constr_num || constr_num > params_num) {
        return -1;
    }

    // A^T = Q*R*P^T = Q1*R1*P^T
    // Q = [Q1,Q2], R=[R1;0]
    // Y = Q1 * inv(R^T) * P^T
    // Z = Q2
    Y = qrAT.matrixQR()
            .topRows(constr_num)
            .triangularView<Upper>()
            .transpose()
            .solve<OnTheRight>(Q.leftCols(rank))
        * qrAT.colsPermutation().transpose();
    if (params_num == rank) {
        x = -Y * c;
    }
    else {
        Z = Q.rightCols(params_num - rank);

        MatrixXd ZTHZ = Z.transpose() * H * Z;
        VectorXd rhs = Z.transpose() * (H * Y * c - g);

        VectorXd y = ZTHZ.colPivHouseholderQr().solve(rhs);

        x = -Y * c + Z * y;
    }

    return 0;
}

int qp_eq(MatrixXd& H, VectorXd& g, MatrixXd& A, VectorXd& c, VectorXd& x, MatrixXd& Y, MatrixXd& Z)
{
    const Index m = A.rows();
    const Index n = A.cols();
    if (n < kFastMinParams || m < 1 || m > n) {
        gLastPath = 0;
        gDense.fetch_add(1, std::memory_order_relaxed);
        return qp_eq_dense(H, g, A, c, x, Y, Z);
    }
    if (forceFlag().load(std::memory_order_relaxed)) {
        gLastPath = 0;
        gDense.fetch_add(1, std::memory_order_relaxed);
        const int rc = qp_eq_dense(H, g, A, c, x, Y, Z);
        const double noise = gNoise.load(std::memory_order_relaxed);
        if (rc == 0 && noise != 0.0) {
            // test hook: rounding-sized noise on the forced dense answer, to measure how far apart two
            // correct answers end up after a whole drag (the solver stops at |step| <= 1e-8)
            for (Index k = 0; k < x.size(); ++k) {
                x(k) *= 1.0 + noise * std::sin(12.9898 * static_cast<double>(k) + static_cast<double>(n));
            }
        }
        return rc;
    }
    VectorXd xf;
    MatrixXd Yf, Zf;
    bool zWritten = false;
    const Why why = qp_eq_fast(H, g, A, c, xf, Yf, Zf, zWritten);
    if (why == Why::Ok) {
        if (gAudit.load(std::memory_order_relaxed)) {
            int statusDense = 0;
            VectorXd xDense;
            const QpEq::Audit a = compare(H, g, A, c, xf, Yf, Zf, zWritten, statusDense, xDense);
            const std::lock_guard<std::mutex> lock(gAuditMutex);
            merge(gAuditData, a);
        }
        x = std::move(xf);
        Y = std::move(Yf);
        if (zWritten) {
            Z = std::move(Zf);
        }
        gLastPath = 1;
        gLastWhy = 0;
        gFast.fetch_add(1, std::memory_order_relaxed);
        return 0;
    }
    gLastPath = 2;
    gLastWhy = static_cast<int>(why);
    gFallback.fetch_add(1, std::memory_order_relaxed);
    return qp_eq_dense(H, g, A, c, x, Y, Z);
}

namespace QpEq
{
Stats stats()
{
    return {gFast.load(), gFallback.load(), gDense.load()};
}

void resetStats()
{
    gFast = 0;
    gFallback = 0;
    gDense = 0;
    const std::lock_guard<std::mutex> lock(gAuditMutex);
    gAuditData = Audit();
}

bool setForceDense(bool on)
{
    return forceFlag().exchange(on);
}

bool setAudit(bool on)
{
    return gAudit.exchange(on);
}

double setNoise(double relative)
{
    return gNoise.exchange(relative);
}

Audit audit()
{
    const std::lock_guard<std::mutex> lock(gAuditMutex);
    return gAuditData;
}

Probe probe(MatrixXd H, VectorXd g, MatrixXd A, VectorXd c)
{
    Probe p;
    VectorXd x;
    MatrixXd Y, Z;
    p.status = qp_eq(H, g, A, c, x, Y, Z);
    p.path = gLastPath;
    p.why = gLastWhy;
    p.x = x;
    VectorXd xd;
    MatrixXd Yd, Zd;
    p.statusDense = qp_eq_dense(H, g, A, c, xd, Yd, Zd);
    p.xDense = xd;
    if (p.path == 1 && p.status == 0) {
        int sd = 0;
        VectorXd xdd;
        p.metrics = compare(H, g, A, c, x, Y, Z, Z.cols() > 0 && A.cols() > A.rows(), sd, xdd);
    }
    return p;
}
}  // namespace QpEq
