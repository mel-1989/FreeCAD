// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2008 Jürgen Riegel <juergen.riegel@web.de>              *
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


#include <App/Application.h>
#include <App/Document.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Base/FileInfo.h>
#include <Base/PyObjectBase.h>

#include "SketchObjectSF.h"
#include "planegcs/qp_eq.h"


namespace Sketcher
{
class Module: public Py::ExtensionModule<Module>
{
public:
    Module()
        : Py::ExtensionModule<Module>("Sketcher")
    {
        add_varargs_method("open", &Module::open);
        add_varargs_method("insert", &Module::insert);
        add_varargs_method(
            "qpEqStats",
            &Module::qpEqStats,
            "qpEqStats() -> dict: calls answered by the fast path, tried and fallen back, never tried; audit maxima"
        );
        add_varargs_method("qpEqReset", &Module::qpEqReset, "qpEqReset(): zero the counters and the audit");
        add_varargs_method(
            "qpEqForceDense",
            &Module::qpEqForceDense,
            "qpEqForceDense(on) -> previous: send every solve down the dense path (test hook)"
        );
        add_varargs_method(
            "qpEqAudit",
            &Module::qpEqAudit,
            "qpEqAudit(on) -> previous: run the dense path beside every fast answer and record the deviations (test hook)"
        );
        add_varargs_method(
            "qpEqNoise",
            &Module::qpEqNoise,
            "qpEqNoise(relative) -> previous: rounding-sized noise on forced-dense answers (test hook)"
        );
        add_varargs_method(
            "qpEq",
            &Module::qpEq,
            "qpEq(H, g, A, c) -> dict: one QP through qp_eq with the dense path beside it (test hook)"
        );
        initialize("This module is the Sketcher module.");  // register with Python
    }

    ~Module() override
    {}

private:
    static Py::Dict auditDict(const QpEq::Audit& a)
    {
        Py::Dict d;
        d["calls"] = Py::Long(a.calls);
        d["resAx"] = Py::Float(a.resAx);
        d["resAY"] = Py::Float(a.resAY);
        d["resAZ"] = Py::Float(a.resAZ);
        d["resZ"] = Py::Float(a.resZ);
        d["resYZ"] = Py::Float(a.resYZ);
        d["dx"] = Py::Float(a.dx);
        d["dY"] = Py::Float(a.dY);
        d["dLambda"] = Py::Float(a.dLambda);
        return d;
    }

    static Py::List vectorList(const Eigen::VectorXd& v)
    {
        Py::List l;
        for (Eigen::Index i = 0; i < v.size(); ++i) {
            l.append(Py::Float(v(i)));
        }
        return l;
    }

    static Eigen::VectorXd toVector(const Py::Object& o)
    {
        Py::Sequence seq(o);
        Eigen::VectorXd v(static_cast<Eigen::Index>(seq.size()));
        for (Py::Sequence::size_type i = 0; i < seq.size(); ++i) {
            v(static_cast<Eigen::Index>(i)) = static_cast<double>(Py::Float(seq[i]));
        }
        return v;
    }

    static Eigen::MatrixXd toMatrix(const Py::Object& o, Eigen::Index cols)
    {
        Py::Sequence rows(o);
        Eigen::MatrixXd M(static_cast<Eigen::Index>(rows.size()), cols);
        for (Py::Sequence::size_type i = 0; i < rows.size(); ++i) {
            Py::Sequence row(rows[i]);
            if (static_cast<Eigen::Index>(row.size()) != cols) {
                throw Py::ValueError("matrix rows differ in length");
            }
            for (Py::Sequence::size_type j = 0; j < row.size(); ++j) {
                M(static_cast<Eigen::Index>(i), static_cast<Eigen::Index>(j)) =
                    static_cast<double>(Py::Float(row[j]));
            }
        }
        return M;
    }

    Py::Object qpEqStats(const Py::Tuple&)
    {
        const QpEq::Stats s = QpEq::stats();
        Py::Dict d = auditDict(QpEq::audit());
        d["fast"] = Py::Long(s.fast);
        d["fallback"] = Py::Long(s.fallback);
        d["dense"] = Py::Long(s.dense);
        return d;
    }

    Py::Object qpEqReset(const Py::Tuple&)
    {
        QpEq::resetStats();
        return Py::None();
    }

    Py::Object qpEqForceDense(const Py::Tuple& args)
    {
        int on = 0;
        if (!PyArg_ParseTuple(args.ptr(), "p", &on)) {
            throw Py::Exception();
        }
        return Py::Boolean(QpEq::setForceDense(on != 0));
    }

    Py::Object qpEqAudit(const Py::Tuple& args)
    {
        int on = 0;
        if (!PyArg_ParseTuple(args.ptr(), "p", &on)) {
            throw Py::Exception();
        }
        return Py::Boolean(QpEq::setAudit(on != 0));
    }

    Py::Object qpEqNoise(const Py::Tuple& args)
    {
        double relative = 0.0;
        if (!PyArg_ParseTuple(args.ptr(), "d", &relative)) {
            throw Py::Exception();
        }
        return Py::Float(QpEq::setNoise(relative));
    }

    Py::Object qpEq(const Py::Tuple& args)
    {
        PyObject *pH, *pg, *pA, *pc;
        if (!PyArg_ParseTuple(args.ptr(), "OOOO", &pH, &pg, &pA, &pc)) {
            throw Py::Exception();
        }
        const Eigen::VectorXd g = toVector(Py::Object(pg));
        const Eigen::VectorXd c = toVector(Py::Object(pc));
        const Eigen::MatrixXd H = toMatrix(Py::Object(pH), g.size());
        const Eigen::MatrixXd A = toMatrix(Py::Object(pA), g.size());
        if (H.rows() != g.size() || A.rows() != c.size()) {
            throw Py::ValueError("shapes of H, g, A, c disagree");
        }
        const QpEq::Probe p = QpEq::probe(H, g, A, c);
        Py::Dict d = auditDict(p.metrics);
        d["status"] = Py::Long(p.status);
        d["statusDense"] = Py::Long(p.statusDense);
        d["path"] = Py::Long(p.path);
        d["why"] = Py::Long(p.why);
        d["x"] = vectorList(p.x);
        d["xDense"] = vectorList(p.xDense);
        return d;
    }

    Py::Object open(const Py::Tuple& args)
    {
        char* Name;
        if (!PyArg_ParseTuple(args.ptr(), "et", "utf-8", &Name)) {
            throw Py::Exception();
        }
        std::string EncodedName = std::string(Name);
        PyMem_Free(Name);

        Base::FileInfo file(EncodedName.c_str());

        // extract extension
        if (file.extension().empty()) {
            throw Py::RuntimeError("No file extension");
        }

        throw Py::RuntimeError("Unknown file extension");
        // return Py::None();
    }

    Py::Object insert(const Py::Tuple& args)
    {
        char* Name;
        const char* DocName;
        if (!PyArg_ParseTuple(args.ptr(), "ets", "utf-8", &Name, &DocName)) {
            throw Py::Exception();
        }
        std::string EncodedName = std::string(Name);
        PyMem_Free(Name);

        try {
            Base::FileInfo file(EncodedName.c_str());

            // extract extension
            if (file.extension().empty()) {
                throw Py::RuntimeError("No file extension");
            }

            App::Document* pcDoc = App::GetApplication().getDocument(DocName);
            if (!pcDoc) {
                pcDoc = App::GetApplication().newDocument(DocName);
            }

            if (file.hasExtension("skf")) {
                auto filename = file.fileNamePure();
                auto* pcFeature = pcDoc->addObject<Sketcher::SketchObjectSF>(filename.c_str());
                pcFeature->SketchFlatFile.setValue(EncodedName.c_str());

                pcDoc->recompute();
            }
            else {
                throw Py::RuntimeError("Unknown file extension");
            }
        }
        catch (const Base::Exception& e) {
            throw Py::RuntimeError(e.what());
        }
        return Py::None();
    }
};

/// @cond DOXERR
PyObject* initModule()
{
    return Base::Interpreter().addModule(new Module);
}
/// @endcond

}  // namespace Sketcher
