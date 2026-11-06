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
#pragma once

#include <Eigen/Dense>

int qp_eq(
    Eigen::MatrixXd& H,
    Eigen::VectorXd& g,
    Eigen::MatrixXd& A,
    Eigen::VectorXd& c,
    Eigen::VectorXd& x,
    Eigen::MatrixXd& Y,
    Eigen::MatrixXd& Z
);

// Test and measurement hooks of the fast path (G10 slice 5b3). None of them changes a result.
// The fast path serves large systems whose Jacobian is a few dense columns (the frame of a
// sketch block) beside many near-diagonal ones; every doubt falls back to the dense path in
// the same call.
namespace QpEq
{
struct Stats
{
    long fast = 0;      // calls answered by the fast path
    long fallback = 0;  // calls where the fast path was tried and gave up (dense path ran)
    long dense = 0;     // calls that never tried it (small, wrong shape, or forced dense)
};

// Largest deviations seen while auditing (fast path against the dense path on the same input).
struct Audit
{
    long calls = 0;
    double resAx = 0;     // max |A x + c|
    double resAY = 0;     // max |A Y - I|
    double resAZ = 0;     // max |A Z|
    double resZ = 0;      // max |Z^T (H x + g)|
    double resYZ = 0;     // max |Z^T Y| (Y lies in the row space of A)
    double dx = 0;        // max |x - x_dense|
    double dY = 0;        // max |Y - Y_dense|
    double dLambda = 0;   // max |Y^T (H x + g) - Y_dense^T (H x_dense + g)|
};

Stats stats();
void resetStats();
bool setForceDense(bool on);  // returns the previous value; initial value from FREECAD_GCS_QP_FORCE_DENSE
bool setAudit(bool on);       // returns the previous value
double setNoise(double relative);  // relative noise on the Hessian of forced-dense solves (default 0); returns the previous value
Audit audit();

struct Probe
{
    int status = 0;        // return code of qp_eq on the input (auto path)
    int statusDense = 0;   // return code of the dense path on the same input
    int path = 0;          // 0 dense (never tried), 1 fast, 2 fallback
    int why = 0;           // when path is 2: 1 input, 2 structure, 3 block rank, 4 doubt, 5 rank mismatch, 6 residual
    Audit metrics;         // only filled when path == 1
    Eigen::VectorXd x, xDense;
};
Probe probe(Eigen::MatrixXd H, Eigen::VectorXd g, Eigen::MatrixXd A, Eigen::VectorXd c);
}  // namespace QpEq
