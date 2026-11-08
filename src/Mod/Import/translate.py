# SPDX-License-Identifier: LGPL-2.1-or-later

# **************************************************************************
#   Batch translation between the formats FreeCAD reads and writes         *
#   (G13 slice 7).                                                         *
#   Built on FreeCAD's own import/export registry (FreeCAD.getImportType,  *
#   FreeCAD.getExportType) and Python's subprocess module: one child       *
#   FreeCADCmd per input, so a crash or a hang loses one file.             *
# **************************************************************************

"""Batch translation command.

Run it with FreeCADCmd; ``--pass`` hands the rest of the command line to the script::

    FreeCADCmd <install>/Mod/Import/translate.py --pass to=step out=outdir part1.iges models/ "cad/*.brep"

FreeCAD's own command-line parser accepts only the first unknown ``--option`` after ``--pass`` (a second one
stops FreeCADCmd with "unrecognised option" before this script runs), so every option can also be written
as ``name=value``, and that is the form to use with more than one option.
``--to step`` is accepted when it is the only option or comes first, and so is ``--help``.

Inputs are files, folders (searched for files FreeCAD can read, not recursively) or glob patterns.

Options::

    to=FORMAT       step, iges, brep, stl, obj, 3mf, dxf or fcstd (required)
    out=DIR         folder for the results (default: the current folder)
    timeout=SEC     seconds allowed per file (default 300, 0 means no limit)
    report=FILE     write a JSON report: one entry per file and a summary
    --help          this text

One line is printed per file, then a summary.
Status ``ok``: converted, and a B-rep output read back with the same solid count, volume and area
(relative 1e-6, 1e-9 for brep).
Status ``changed``: converted, but the read-back differs; both sets of figures are shown.
Status ``failed`` or ``timeout``: no usable output.

Exit code 0 when every file is ok or changed, 1 when any file failed or timed out, 2 for a usage error.
The command reads the user's preferences and writes none.
"""

import glob
import json
import os
import signal
import subprocess
import sys
import tempfile
import time

TARGETS = ("step", "iges", "brep", "stl", "obj", "3mf", "dxf", "fcstd")
EXTENSION = {"step": "step", "iges": "iges", "brep": "brep", "stl": "stl", "obj": "obj", "3mf": "3mf",
             "dxf": "dxf", "fcstd": "FCStd"}
SOLID_TARGETS = ("step", "iges", "brep", "fcstd")
TOLERANCE = {"brep": 1e-9}
DEFAULT_TOLERANCE = 1e-6
READABLE = ("step", "stp", "iges", "igs", "brep", "brp", "stl", "obj", "3mf", "dxf", "dwg", "fcstd")

# Test hook for the batch command's own tests: "crash:<text>" aborts the child and "hang:<text>" makes it
# sleep, for inputs whose file name contains <text>. Unset in normal use.
FAULT_VARIABLE = "FREECAD_TRANSLATE_TEST_FAULT"


# --------------------------------------------------------------------------- child

def _leaf_shape_objects(doc):
    """Objects that carry a shape and are not used by another shape-carrying object."""
    leaves = []
    for obj in doc.Objects:
        # containers (App::Part) and origin planes also expose a Shape; only Part features count
        if not obj.isDerivedFrom("Part::Feature") or obj.Shape.isNull():
            continue
        if any(parent.isDerivedFrom("Part::Feature") for parent in obj.InList):
            continue
        leaves.append(obj)
    return leaves


def _figures(doc):
    solids = 0
    volume = 0.0
    area = 0.0
    for obj in _leaf_shape_objects(doc):
        shape = obj.Shape
        solids += len(shape.Solids)
        volume += sum(s.Volume for s in shape.Solids)
        area += shape.Area
    return {"solids": solids, "volume": volume, "area": area}


def _read(path, fmt):
    """Open `path` in a new document and return it."""
    import FreeCAD

    if fmt == "fcstd":
        return FreeCAD.openDocument(path)
    modules = FreeCAD.getImportType(fmt)
    if not modules:
        raise RuntimeError("FreeCAD has no importer for ." + fmt)
    doc = FreeCAD.newDocument("Translate")
    errors = []
    for name in dict.fromkeys(modules):
        try:
            module = __import__(name, fromlist=["insert"])
            module.insert(path, doc.Name)
            return doc
        except Exception as exc:  # try the next registered importer
            errors.append("%s: %s" % (name, exc))
    FreeCAD.closeDocument(doc.Name)
    raise RuntimeError("; ".join(errors))


def _write(doc, path, fmt):
    import FreeCAD

    if fmt == "fcstd":
        doc.saveAs(path)
        return
    objects = [o for o in doc.Objects if not o.InList] if fmt == "step" else _leaf_shape_objects(doc)
    if not objects:
        objects = [o for o in doc.Objects if getattr(o, "Mesh", None) is not None] or list(doc.Objects)
    errors = []
    for name in dict.fromkeys(FreeCAD.getExportType(fmt)):
        try:
            module = __import__(name, fromlist=["export"])
            module.export(objects, path)
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                return
            errors.append("%s wrote no file" % name)
        except Exception as exc:
            errors.append("%s: %s" % (name, exc))
    raise RuntimeError("; ".join(errors) or "no exporter for ." + fmt)


def _inject_fault(source):
    spec = os.environ.get(FAULT_VARIABLE, "")
    kind, _, text = spec.partition(":")
    if not text or text not in os.path.basename(source):
        return
    if kind == "crash":
        os.abort()
    if kind == "hang":
        time.sleep(3600)


def _differs(a, b, tolerance):
    return abs(a - b) > tolerance * max(abs(a), abs(b), 1e-300)


def child(source, target_path, fmt, result_path):
    """Convert one file inside this FreeCADCmd and write the outcome as JSON to `result_path`."""
    import FreeCAD

    result = {"status": "failed", "messages": []}
    try:
        _inject_fault(source)
        extension = os.path.splitext(source)[1].lstrip(".").lower()
        doc = _read(source, {"stp": "step", "igs": "iges", "brp": "brep"}.get(extension, extension))
        before = _figures(doc)
        if not doc.Objects:
            result["messages"].append("the file gave no objects")
        _write(doc, target_path, fmt)
        FreeCAD.closeDocument(doc.Name)
        result.update({"solids_in": before["solids"], "volume_in": before["volume"], "area_in": before["area"]})
        if fmt in SOLID_TARGETS:
            check = _read(target_path, fmt)
            after = _figures(check)
            FreeCAD.closeDocument(check.Name)
            result.update({"solids_out": after["solids"], "volume_out": after["volume"], "area_out": after["area"]})
            tolerance = TOLERANCE.get(fmt, DEFAULT_TOLERANCE)
            same = (before["solids"] == after["solids"]
                    and not _differs(before["volume"], after["volume"], tolerance)
                    and not _differs(before["area"], after["area"], tolerance))
            result["status"] = "ok" if same else "changed"
            if not same:
                result["messages"].append(
                    "read back: solids %d -> %d, volume %.9g -> %.9g, area %.9g -> %.9g"
                    % (before["solids"], after["solids"], before["volume"], after["volume"],
                       before["area"], after["area"]))
        else:
            result["status"] = "ok"
    except Exception as exc:
        result["status"] = "failed"
        result["messages"].append(str(exc) or type(exc).__name__)
    with open(result_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle)


# --------------------------------------------------------------------------- parent

def _collect(inputs):
    """Expand files, folders and globs to a list of files; None plus a message when one matches nothing."""
    files = []
    for item in inputs:
        if os.path.isdir(item):
            found = sorted(os.path.join(item, n) for n in os.listdir(item)
                           if os.path.isfile(os.path.join(item, n))
                           and os.path.splitext(n)[1].lstrip(".").lower() in READABLE)
        elif os.path.isfile(item):
            found = [item]
        else:
            found = sorted(f for f in glob.glob(item) if os.path.isfile(f))
        if not found:
            return None, "no file matches " + item
        files.extend(found)
    return list(dict.fromkeys(files)), ""


def _unique(path, used):
    base, ext = os.path.splitext(path)
    candidate, number = path, 1
    while candidate in used:
        number += 1
        candidate = "%s_%d%s" % (base, number, ext)
    used.add(candidate)
    return candidate


def _run_child(binary, source, target, fmt, timeout):
    """Run one child; return (result dict, seconds)."""
    with tempfile.TemporaryDirectory(prefix="translate") as scratch:
        result_path = os.path.join(scratch, "result.json")
        command = [binary, os.path.abspath(__file__), "--pass", "--child", source, target, fmt, result_path]
        start = time.monotonic()
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        timed_out = False
        try:
            output, _ = process.communicate(timeout=timeout or None)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                process.kill()
            output, _ = process.communicate()
        seconds = time.monotonic() - start
        tail = (output or b"").decode("utf-8", "replace").strip().splitlines()
        if timed_out:
            return {"status": "timeout", "messages": ["no result within %g s; the child was killed" % timeout]}, seconds
        if os.path.isfile(result_path):
            with open(result_path, encoding="utf-8") as handle:
                return json.load(handle), seconds
        reason = "the child ended with signal %d" % -process.returncode if process.returncode < 0 \
            else "the child ended with code %d" % process.returncode
        message = reason + (": " + tail[-1] if tail else "")
        return {"status": "failed", "messages": [message]}, seconds


def _line(entry):
    text = "%-8s %7.2fs  %s" % (entry["status"], entry["seconds"], entry["input"])
    if entry.get("output") and entry["status"] in ("ok", "changed"):
        text += " -> " + entry["output"]
    if entry["status"] == "ok" and entry.get("solids_out") is not None:
        text += "  (solids %d, volume %.6g, area %.6g)" % (entry["solids_out"], entry["volume_out"],
                                                         entry["area_out"])
    for message in entry["messages"]:
        text += "\n           " + message
    return text


def _usage_error(message):
    sys.stderr.write("translate.py: %s\n(see --help)\n" % message)
    return 2


def _spell_options(arguments):
    """Turn the name=value spelling of the options into --name value."""
    spelled = []
    for argument in arguments:
        name, equals, value = argument.partition("=")
        if equals and name in ("to", "out", "timeout", "report"):
            spelled += ["--" + name, value]
        else:
            spelled.append(argument)
    return spelled


def main(arguments):
    import argparse

    arguments = _spell_options(arguments)
    parser = argparse.ArgumentParser(prog="FreeCADCmd translate.py --pass", add_help=False)
    parser.add_argument("inputs", nargs="*")
    parser.add_argument("--to")
    parser.add_argument("--out", default=".")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--report")
    parser.add_argument("--help", "-h", action="store_true")
    try:
        # argparse exits with 2 on a malformed command line, which is the usage-error code
        options = parser.parse_args(arguments)
    except SystemExit:
        return 2
    if options.help:
        print(__doc__)
        return 0
    if options.to is None or options.to.lower() not in TARGETS:
        return _usage_error("to= must be one of " + ", ".join(TARGETS))
    if not options.inputs:
        return _usage_error("no input files, folders or patterns given")
    if options.timeout < 0:
        return _usage_error("timeout must not be negative")
    fmt = options.to.lower()
    files, problem = _collect(options.inputs)
    if files is None:
        return _usage_error(problem)
    try:
        os.makedirs(options.out, exist_ok=True)
    except OSError as exc:
        return _usage_error("cannot create --out folder: %s" % exc)

    binary = sys.executable
    entries = []
    used = set()
    for source in files:
        stem = os.path.splitext(os.path.basename(source))[0]
        target = _unique(os.path.join(os.path.abspath(options.out), stem + "." + EXTENSION[fmt]), used)
        entry = {"input": source, "output": target, "status": "failed", "seconds": 0.0,
                 "solids_in": None, "volume_in": None, "area_in": None,
                 "solids_out": None, "volume_out": None, "area_out": None, "messages": []}
        if os.path.realpath(target) == os.path.realpath(source):
            entry["messages"].append("the output would overwrite the input; choose another --out")
            entry["output"] = None
        else:
            result, seconds = _run_child(binary, source, target, fmt, options.timeout)
            entry.update(result)
            entry["seconds"] = round(seconds, 3)
            if entry["status"] in ("failed", "timeout") and os.path.isfile(target):
                os.remove(target)  # never leave a half-written output next to a failure line
        entries.append(entry)
        print(_line(entry))
        sys.stdout.flush()

    counts = {name: sum(1 for e in entries if e["status"] == name) for name in ("ok", "changed", "failed", "timeout")}
    summary = dict(counts, files=len(entries), seconds=round(sum(e["seconds"] for e in entries), 3))
    print("%d files: %d ok, %d changed, %d failed, %d timeout"
          % (len(entries), counts["ok"], counts["changed"], counts["failed"], counts["timeout"]))
    if options.report:
        with open(options.report, "w", encoding="utf-8") as handle:
            json.dump({"to": fmt, "files": entries, "summary": summary}, handle, indent=2)
    return 1 if counts["failed"] or counts["timeout"] else 0


def _entry():
    arguments = sys.argv[1:]
    # FreeCADCmd puts its own binary and the script first; keep what follows "--pass" (or the script)
    if "--pass" in arguments:
        arguments = arguments[arguments.index("--pass") + 1:]
    else:
        arguments = [a for a in arguments if not a.endswith("translate.py")]
    if arguments[:1] == ["--child"]:
        child(*arguments[1:5])
        sys.stdout.flush()
        os._exit(0)
    code = main(arguments)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


# FreeCADCmd runs a script under its file name as module name, not "__main__"
if __name__ in ("__main__", "translate") and "--pass" in sys.argv:
    _entry()
