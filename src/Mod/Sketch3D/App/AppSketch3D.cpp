// SPDX-License-Identifier: LGPL-2.1-or-later

#include <cmath>
#include <cstring>
#include <initializer_list>
#include <string>

#include <Python.h>

#include <Base/Console.h>
#include <Base/Interpreter.h>
#include <Base/PyObjectBase.h>

#include "Solve3D.h"

namespace Sketch3D
{

namespace
{

PyObject* specErrorType = nullptr;

[[noreturn]] void raiseSpec(int position, const std::string& message)
{
    PyObject* exc = PyObject_CallFunction(specErrorType, "s", message.c_str());
    if (exc) {
        PyObject* pos = PyLong_FromLong(position);
        PyObject_SetAttrString(exc, "position", pos);
        Py_XDECREF(pos);
        PyErr_SetObject(specErrorType, exc);
        Py_DECREF(exc);
    }
    throw Py::Exception();
}

// Owned reference helper
struct Ref
{
    PyObject* p;
    explicit Ref(PyObject* q = nullptr)
        : p(q)
    {}
    ~Ref()
    {
        Py_XDECREF(p);
    }
    Ref(const Ref&) = delete;
    Ref& operator=(const Ref&) = delete;
};

double number(PyObject* o, int position, const std::string& what)
{
    if (!o || PyBool_Check(o) || !(PyFloat_Check(o) || PyLong_Check(o))) {
        raiseSpec(position, what + " must be a number");
    }
    const double v = PyFloat_AsDouble(o);
    if (PyErr_Occurred()) {
        PyErr_Clear();
        raiseSpec(position, what + " is not a finite number");
    }
    if (!std::isfinite(v)) {
        raiseSpec(position, what + " must be finite");
    }
    return v;
}

int integer(PyObject* o, int position, const std::string& what)
{
    if (!o || PyBool_Check(o) || !PyLong_Check(o)) {
        raiseSpec(position, what + " must be an integer");
    }
    const long v = PyLong_AsLong(o);
    if (PyErr_Occurred()) {
        PyErr_Clear();
        raiseSpec(position, what + " is out of range");
    }
    if (v < -1000000000L || v > 1000000000L) {
        raiseSpec(position, what + " is out of range");
    }
    return static_cast<int>(v);
}

std::array<double, 3> vec3(PyObject* o, int position, const std::string& what)
{
    Ref seq(PySequence_Fast(o, "sequence expected"));
    if (!seq.p) {
        PyErr_Clear();
        raiseSpec(position, what + " must be a sequence of three numbers");
    }
    if (PySequence_Fast_GET_SIZE(seq.p) != 3) {
        raiseSpec(position, what + " must have three numbers");
    }
    std::array<double, 3> v {};
    for (int i = 0; i < 3; ++i) {
        v[i] = number(PySequence_Fast_GET_ITEM(seq.p, i), position, what);
    }
    return v;
}

// Every key of dict must be one of the allowed ones: a misspelt key would otherwise be dropped
// silently and the solver would run on a different spec than the one written.
void onlyKeys(PyObject* dict, std::initializer_list<const char*> allowed, int position, const std::string& what)
{
    PyObject* key = nullptr;
    PyObject* value = nullptr;
    Py_ssize_t pos = 0;
    while (PyDict_Next(dict, &pos, &key, &value)) {
        const char* name = PyUnicode_Check(key) ? PyUnicode_AsUTF8(key) : nullptr;
        if (!name) {
            PyErr_Clear();
            raiseSpec(position, what + ": every key must be a string");
        }
        bool known = false;
        for (const char* a : allowed) {
            known = known || std::strcmp(a, name) == 0;
        }
        if (!known) {
            raiseSpec(position, what + ": unknown key '" + name + "'");
        }
    }
}

// A flag is a bool: "yes", 1 and None are errors, not true.
bool flag(PyObject* o, int position, const std::string& what)
{
    if (!o || !PyBool_Check(o)) {
        raiseSpec(position, what + " must be True or False");
    }
    return o == Py_True;
}

PyObject* item(PyObject* dict, const char* key)
{
    return PyDict_GetItemString(dict, key);  // borrowed
}

Ref listOf(PyObject* spec, const char* key)
{
    PyObject* o = item(spec, key);
    if (!o) {
        return Ref(PyList_New(0));
    }
    PyObject* s = PySequence_Fast(o, "sequence expected");
    if (!s) {
        PyErr_Clear();
        raiseSpec(-1, std::string("'") + key + "' must be a list");
    }
    return Ref(s);
}

Spec parseSpec(PyObject* obj)
{
    if (!PyDict_Check(obj)) {
        raiseSpec(-1, "spec must be a dict");
    }
    Spec spec;
    onlyKeys(obj, {"points", "lines", "planes", "constraints", "algorithm", "diagnose"}, -1, "spec");
    {
        Ref pts = listOf(obj, "points");
        const Py_ssize_t n = PySequence_Fast_GET_SIZE(pts.p);
        for (Py_ssize_t i = 0; i < n; ++i) {
            PyObject* e = PySequence_Fast_GET_ITEM(pts.p, i);
            const std::string w = "point " + std::to_string(i);
            PointSpec ps;
            if (PyDict_Check(e)) {
                PyObject* xyz = item(e, "xyz");
                if (!xyz) {
                    raiseSpec(-1, w + ": missing 'xyz'");
                }
                onlyKeys(e, {"xyz", "fixed"}, -1, w);
                ps.xyz = vec3(xyz, -1, w);
                PyObject* fx = item(e, "fixed");
                if (fx) {
                    ps.fixed = flag(fx, -1, w + ": 'fixed'");
                }
            }
            else {
                ps.xyz = vec3(e, -1, w);
            }
            spec.points.push_back(ps);
        }
    }
    {
        Ref lines = listOf(obj, "lines");
        const Py_ssize_t n = PySequence_Fast_GET_SIZE(lines.p);
        for (Py_ssize_t i = 0; i < n; ++i) {
            Ref pair(PySequence_Fast(PySequence_Fast_GET_ITEM(lines.p, i), "sequence expected"));
            if (!pair.p || PySequence_Fast_GET_SIZE(pair.p) != 2) {
                PyErr_Clear();
                raiseSpec(-1, "line " + std::to_string(i) + " must be two point indices");
            }
            spec.lines.push_back(
                {integer(PySequence_Fast_GET_ITEM(pair.p, 0), -1, "line index"),
                 integer(PySequence_Fast_GET_ITEM(pair.p, 1), -1, "line index")}
            );
        }
    }
    {
        Ref planes = listOf(obj, "planes");
        const Py_ssize_t n = PySequence_Fast_GET_SIZE(planes.p);
        for (Py_ssize_t i = 0; i < n; ++i) {
            PyObject* e = PySequence_Fast_GET_ITEM(planes.p, i);
            const std::string w = "plane " + std::to_string(i);
            if (!PyDict_Check(e) || !item(e, "point") || !item(e, "normal")) {
                raiseSpec(-1, w + " must be a dict with 'point' and 'normal'");
            }
            onlyKeys(e, {"point", "normal"}, -1, w);
            PlaneSpec pl;
            pl.point = vec3(item(e, "point"), -1, w);
            pl.normal = vec3(item(e, "normal"), -1, w);
            spec.planes.push_back(pl);
        }
    }
    {
        Ref cons = listOf(obj, "constraints");
        const Py_ssize_t n = PySequence_Fast_GET_SIZE(cons.p);
        for (Py_ssize_t i = 0; i < n; ++i) {
            const int pos = static_cast<int>(i);
            PyObject* e = PySequence_Fast_GET_ITEM(cons.p, i);
            const std::string w = "constraint " + std::to_string(i) + ": ";
            if (!PyDict_Check(e)) {
                raiseSpec(pos, w + "must be a dict");
            }
            ConstraintSpec cs;
            onlyKeys(e, {"type", "operands", "value", "driving", "tag"}, pos, w);
            PyObject* type = item(e, "type");
            if (!type || !PyUnicode_Check(type)) {
                raiseSpec(pos, w + "missing 'type'");
            }
            const char* tname = PyUnicode_AsUTF8(type);
            if (!tname) {
                PyErr_Clear();
                raiseSpec(pos, w + "bad 'type'");
            }
            if (!kindFromName(tname, cs.kind)) {
                raiseSpec(pos, w + "unknown type '" + tname + "'");
            }
            PyObject* ops = item(e, "operands");
            if (!ops) {
                raiseSpec(pos, w + "missing 'operands'");
            }
            Ref opSeq(PySequence_Fast(ops, "sequence expected"));
            if (!opSeq.p) {
                PyErr_Clear();
                raiseSpec(pos, w + "'operands' must be a list");
            }
            for (Py_ssize_t k = 0; k < PySequence_Fast_GET_SIZE(opSeq.p); ++k) {
                cs.operands.push_back(integer(PySequence_Fast_GET_ITEM(opSeq.p, k), pos, w + "operand"));
            }
            PyObject* value = item(e, "value");
            if (kindInfo(cs.kind).hasValue) {
                if (!value || value == Py_None) {
                    raiseSpec(pos, w + "missing 'value'");
                }
                cs.value = number(value, pos, w + "value");
            }
            PyObject* drv = item(e, "driving");
            if (drv) {
                cs.driving = flag(drv, pos, w + "'driving'");
            }
            PyObject* tag = item(e, "tag");
            // A tag names a constraint in the conflict and redundancy lists. Several constraints
            // may share one: the lists then name the group once, by that tag (a user relation
            // made of several constraints is reported as one). The default is the position.
            // Negative tags are refused: PlaneGCS gives negative tags a meaning of their own.
            cs.tag = tag && tag != Py_None ? integer(tag, pos, w + "tag") : pos;
            if (cs.tag < 0) {
                raiseSpec(pos, w + "tag must not be negative");
            }
            spec.constraints.push_back(std::move(cs));
        }
    }
    PyObject* dg = item(obj, "diagnose");
    if (dg) {
        spec.diagnose = flag(dg, -1, "'diagnose'");
    }
    PyObject* alg = item(obj, "algorithm");
    if (alg && alg != Py_None) {
        const char* a = PyUnicode_Check(alg) ? PyUnicode_AsUTF8(alg) : nullptr;
        if (!a) {
            PyErr_Clear();
            raiseSpec(-1, "algorithm must be a string");
        }
        spec.algorithm = a;
    }
    return spec;
}

PyObject* intList(const std::vector<int>& v)
{
    PyObject* l = PyList_New(static_cast<Py_ssize_t>(v.size()));
    for (size_t i = 0; i < v.size(); ++i) {
        PyList_SET_ITEM(l, i, PyLong_FromLong(v[i]));
    }
    return l;
}

PyObject* toDict(const Result& r)
{
    PyObject* d = PyDict_New();
    auto set = [&](const char* k, PyObject* v) {
        PyDict_SetItemString(d, k, v);
        Py_DECREF(v);
    };
    set("code", PyLong_FromLong(r.code));
    set("status", PyUnicode_FromString(r.status.c_str()));
    PyObject* pts = PyList_New(static_cast<Py_ssize_t>(r.coordinates.size()));
    for (size_t i = 0; i < r.coordinates.size(); ++i) {
        PyList_SET_ITEM(
            pts,
            i,
            Py_BuildValue("[ddd]", r.coordinates[i][0], r.coordinates[i][1], r.coordinates[i][2])
        );
    }
    set("coordinates", pts);
    set("dof", PyLong_FromLong(r.dof));
    set("conflicting", intList(r.conflicting));
    set("redundant", intList(r.redundant));
    set("partially_redundant", intList(r.partiallyRedundant));
    set("collapsed", intList(r.collapsed));
    set("reason", PyUnicode_FromString(r.reason.c_str()));
    PyObject* res = PyList_New(static_cast<Py_ssize_t>(r.residuals.size()));
    for (size_t i = 0; i < r.residuals.size(); ++i) {
        PyList_SET_ITEM(res, i, PyFloat_FromDouble(r.residuals[i]));
    }
    set("residuals", res);
    set("iterations", PyLong_FromLong(r.iterations));
    set("algorithm", PyUnicode_FromString(r.algorithm.c_str()));
    return d;
}

PyObject* rowsToList(const std::vector<RowReport>& rows)
{
    PyObject* l = PyList_New(static_cast<Py_ssize_t>(rows.size()));
    for (size_t i = 0; i < rows.size(); ++i) {
        PyObject* g = PyDict_New();
        for (auto& [k, v] : rows[i].grad) {
            PyObject* key = PyLong_FromLong(k);
            PyObject* val = PyFloat_FromDouble(v);
            PyDict_SetItem(g, key, val);
            Py_DECREF(key);
            Py_DECREF(val);
        }
        PyObject* d = Py_BuildValue(
            "{s:i,s:i,s:d,s:N}",
            "constraint",
            rows[i].constraint,
            "row",
            rows[i].row,
            "error",
            rows[i].error,
            "grad",
            g
        );
        PyList_SET_ITEM(l, i, d);
    }
    return l;
}

class Module: public Py::ExtensionModule<Module>
{
public:
    Module()
        : Py::ExtensionModule<Module>("Sketch3D")
    {
        add_varargs_method(
            "solve",
            &Module::solveMethod,
            "solve(spec) -> dict. Solves a 3D sketch given as plain Python data."
        );
        add_varargs_method(
            "constraint_types",
            &Module::typesMethod,
            "constraint_types() -> dict of type name to operands, value flag and row count."
        );
        add_varargs_method(
            "_test_rows",
            &Module::rowsMethod,
            "Test hook: per-row error and analytic gradient at the spec's own coordinates."
        );
        initialize("3D sketch relations on the PlaneGCS system.");
    }

private:
    Py::Object guarded(const Py::Tuple& args, bool rows)
    {
        if (args.length() != 1) {
            throw Py::TypeError("expected exactly one argument, the spec");
        }
        try {
            const Spec spec = parseSpec(args[0].ptr());
            if (rows) {
                return Py::asObject(rowsToList(rowReports(spec)));
            }
            return Py::asObject(toDict(solve(spec)));
        }
        catch (const SpecError& e) {
            raiseSpec(e.position, e.what());
        }
        catch (const Py::Exception&) {
            throw;
        }
        catch (const std::exception& e) {
            throw Py::RuntimeError(std::string("Sketch3D internal error: ") + e.what());
        }
        catch (...) {
            throw Py::RuntimeError("Sketch3D internal error");
        }
    }

    Py::Object solveMethod(const Py::Tuple& args)
    {
        return guarded(args, false);
    }

    Py::Object rowsMethod(const Py::Tuple& args)
    {
        return guarded(args, true);
    }

    Py::Object typesMethod(const Py::Tuple&)
    {
        Py::Dict out;
        for (int i = 0; i < static_cast<int>(Kind::Count); ++i) {
            const KindInfo& k = kindInfo(static_cast<Kind>(i));
            Py::Dict e;
            e.setItem("operands", Py::String(k.operands));
            e.setItem("value", Py::Boolean(k.hasValue));
            e.setItem("rows", Py::Long(k.rows));
            out.setItem(k.name, e);
        }
        return out;
    }
};

}  // namespace

}  // namespace Sketch3D

PyMOD_INIT_FUNC(Sketch3D)
{
    try {
        Base::Interpreter().runString("import Sketcher");
    }
    catch (const Base::Exception& e) {
        PyErr_SetString(PyExc_ImportError, e.what());
        PyMOD_Return(nullptr);
    }
    Sketch3D::specErrorType = PyErr_NewExceptionWithDoc(
        "Sketch3D.SpecError",
        "A malformed 3D sketch spec; .position is the index of the offending constraint or -1.",
        PyExc_ValueError,
        nullptr
    );
    PyObject* mod = Base::Interpreter().addModule(new Sketch3D::Module);
    Py_INCREF(Sketch3D::specErrorType);
    PyModule_AddObject(mod, "SpecError", Sketch3D::specErrorType);
    PyMOD_Return(mod);
}
