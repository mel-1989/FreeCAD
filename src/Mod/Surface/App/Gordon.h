// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *  Gordon surface through a network of profile and guide curves.          *
 *                                                                         *
 *  Public sources: W. J. Gordon, "Spline-blended surface interpolation    *
 *  through curve networks", J. Math. Mech. 18(10), 1969; the OCCT 8.0     *
 *  GeomFill_Gordon header documentation.                                  *
 ***************************************************************************/

#pragma once

#include <CXX/Objects.hxx>

namespace Surface
{

/// Python ``Surface.makeGordon(profiles, guides, tolerance=1e-4, approximate=False)``.
Py::Object makeGordon(const Py::Tuple& args, const Py::Dict& kwds);

}  // namespace Surface
