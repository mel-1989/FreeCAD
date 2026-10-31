# SPDX-License-Identifier: LGPL-2.1-or-later
"""G03 slice 1 worker, run inside FreeCADCmd: FreeCADCmd folder_worker.py MODE SPEC.json OUT.json

MODE save: open each source document (zip), dump every persisted property by its persistence
content, save it twice as a folder with saveCopy and record whether the two trees are identical.
MODE load: in a fresh process open each folder, dump it the same way, save it again (saveCopy
and save()) and record whether the trees are identical to the first.
"""

import hashlib
import io
import json
import os
import re
import sys
import traceback
import zipfile

import FreeCAD as App

# Label is derived from the file name on open (zip and folder alike), so it is not a property
# of the content; FileName and TransientDir are transient.
SKIP_DOC_PROPS = {"FileName", "TransientDir", "Label"}
# Rewritten by every save with the current element-map version (zip and folder alike), so a
# document last saved by an older FreeCAD differs here by design.
SKIP_PROPS = {"_ElementMapVersion"}
# A Python proxy's state is whatever its class makes of it on load (ArchStructure reorders its
# serialised list), identical in the zip path; nothing the file format decides.
SKIP_PROPS |= {"Proxy"}
# A path into the document's transient directory (FreeCAD_Doc_<uuid>_<hash>_<pid>) changes per run
TRANSIENT_DIR = re.compile(r"FreeCAD_Doc_[0-9a-f-]+_[0-9a-f]+_\d+")


def tree(path):
    """Relative path -> sha256 of every regular file under path (everything FreeCAD wrote)."""
    out = {}
    for root, _dirs, files in os.walk(path):
        for f in files:
            full = os.path.join(root, f)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, path)] = hashlib.sha256(fh.read()).hexdigest()
    return out


NEG_ZERO = re.compile(rb"(?<=[ \n])-(0(?:\.0+)?)(?=[ \r\n]|$)")


def member_value(name, data):
    """One persisted member: its text if it is text (compared later with a tolerance on the
    decimal numbers), else a hash. Negative zero in BREP text is zero: the folder writer writes
    it as zero because OCCT flips the sign of rounding noise on every cycle."""
    if name.endswith(".brp"):
        data = NEG_ZERO.sub(rb"\1", data)
    try:
        return TRANSIENT_DIR.sub("FreeCAD_Doc_X", data.decode("utf-8"))
    except UnicodeDecodeError:
        return "sha256:" + hashlib.sha256(data).hexdigest()


def dump_container(c, skip=(), sink=None, prefix=""):
    props = {}
    for name in c.PropertiesList:
        if name in skip or name in SKIP_PROPS:
            continue
        status = c.getPropertyStatus(name)
        if "Transient" in status or "NoPersist" in status:
            continue
        raw = bytes(c.dumpPropertyContent(name, Compression=0))
        try:
            z = zipfile.ZipFile(io.BytesIO(raw))
            props[name] = {m: member_value(m, z.read(m)) for m in z.namelist()}
            for m in z.namelist():
                if m.endswith(".bin"):
                    # dumpPropertyContent writes shapes as binary BREP; compare the text form
                    # (negative zero as zero) so the check does not depend on the sign of noise
                    value = c.getPropertyByName(name)
                    if hasattr(value, "exportBrepToString"):
                        props[name][m] = "see-shapes"
                        if sink is not None:
                            sink[prefix + name] = value.exportBrepToString()
        except zipfile.BadZipFile:
            props[name] = {"": "sha256:" + hashlib.sha256(raw).hexdigest()}
    return props


def shape_metrics(obj):
    shape = getattr(obj, "Shape", None)
    if shape is None or not hasattr(shape, "isNull") or shape.isNull():
        return None
    out = {
        "type": shape.ShapeType,
        "solids": len(shape.Solids),
        "faces": len(shape.Faces),
        "edges": len(shape.Edges),
        "vertexes": len(shape.Vertexes),
    }
    try:
        bb = shape.BoundBox
        if bb.DiagonalLength > 1e50:
            return out  # unbounded geometry (infinite planes): topology counts only
        out["bbox"] = [bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax]
        out["diag"] = bb.DiagonalLength
        if shape.Solids:
            out["volume"] = shape.Volume  # a face's "volume" is rounding noise
        out["area"] = shape.Area
        com = shape.CenterOfMass
        out["com"] = [com.x, com.y, com.z]
    except Exception:  # noqa: BLE001  (wires, vertices: not every shape has every metric)
        pass
    return out


def dump_doc(doc, sink=None):
    objs = {}
    for o in doc.Objects:
        objs[o.Name] = {
            "type": o.TypeId,
            "props": dump_container(o, sink=sink, prefix=o.Name + "."),
            "metrics": shape_metrics(o),
        }
    return {"doc": dump_container(doc, SKIP_DOC_PROPS), "objects": objs}


def run_save(item, out):
    doc = App.openDocument(item["src"])
    os.makedirs(out, exist_ok=True)
    a = os.path.join(out, "A.FCStd.d")
    b = os.path.join(out, "B.FCStd.d")
    b2 = os.path.join(out, "B2.FCStd.d")
    sink = {}
    result = {"dump": dump_doc(doc, sink)}
    json.dump(sink, open(os.path.join(out, "shapes.json"), "w"))
    doc.saveCopy(b)
    doc.saveCopy(b2)
    result["twice_identical"] = tree(b) == tree(b2)
    doc.saveAsFolder(a)  # binds the document to the folder and labels it "A"
    result["files"] = len(tree(a))
    brp = [f for f in tree(a) if f.endswith(".brp")]
    heads = [open(os.path.join(a, f), "rb").read(40).lstrip() for f in brp]
    result["text_brep"] = all(h.startswith(b"CASCADE Topology V") or h == b"" for h in heads)
    result["brep_files"] = len(brp)
    App.closeDocument(doc.Name)
    return result


FLAGS = re.compile(r"^[01]{7}$")
NUMBER = re.compile(r"-?\d+\.\d+")
STATUS = re.compile(r' status="(\d+)"')


def brep_float_noise_only(path_a, path_b, tol=1e-12):
    """True when two BREP texts differ only in numeric tokens, each by at most tol (OCCT rebuilds
    rotation matrices on every read, so the last digits of Locations move on the first reload)."""
    ta = open(path_a, encoding="utf-8", errors="replace").read().split()
    tb = open(path_b, encoding="utf-8", errors="replace").read().split()
    if len(ta) != len(tb):
        return False
    for x, y in zip(ta, tb):
        if x == y:
            continue
        try:
            if abs(float(x) - float(y)) > tol:
                return False
        except ValueError:
            return False
    return True


def brep_text_close(a, b, tol=1e-12):
    ta, tb = a.split(), b.split()
    if len(ta) != len(tb):
        return False
    for x, y in zip(ta, tb):
        if x != y:
            if FLAGS.match(x) and FLAGS.match(y):
                continue  # TShape flags (free, modified, checked...): OCCT bookkeeping, not geometry
            try:
                if abs(float(x) - float(y)) > tol:
                    return False
            except ValueError:
                return False
    return True


def benign_difference(dir_a, dir_b, files, lenient_data=False):
    """True when every differing file is a BREP that differs by float noise only, or
    Document.xml where every differing line differs just in the Touched bit (bit 0) of a
    property status attribute."""
    for f in files:
        if lenient_data and (f == "Document.xml" or f == "FCStdFolder.txt"):
            continue  # FEM: data file names (Data.vtu, Data1.vtu...) move on reload
        if f.endswith(".brp"):
            if not brep_float_noise_only(os.path.join(dir_a, f), os.path.join(dir_b, f)):
                return False
        elif f == "Document.xml":
            if not xml_status_only(os.path.join(dir_a, f), os.path.join(dir_b, f)):
                return False
        elif not lenient_data:
            return False
    return True


def xml_status_only(path_a, path_b):
    la = open(path_a, encoding="utf-8").read().splitlines()
    lb = open(path_b, encoding="utf-8").read().splitlines()
    if len(la) != len(lb):
        return False
    for x, y in zip(la, lb):
        if x == y or ("<Python value=" in x and "<Python value=" in y):
            continue  # a Python proxy's own serialisation, see SKIP_PROPS
        mx, my = STATUS.search(x), STATUS.search(y)  # an absent status attribute means 0
        sx, sy = int(mx.group(1)) if mx else 0, int(my.group(1)) if my else 0
        if sx >> 1 != sy >> 1:
            return False
        rx, ry = STATUS.sub("", x), STATUS.sub("", y)
        if rx != ry and not xml_numbers_close(rx, ry):
            return False
    return True


def xml_numbers_close(x, y, tol=1e-12):
    """Same line apart from decimal numbers that differ by at most tol (quaternions, placements)."""
    if NUMBER.sub("#", x) != NUMBER.sub("#", y):
        return False
    return all(abs(float(a) - float(b)) <= tol for a, b in zip(NUMBER.findall(x), NUMBER.findall(y)))


def tree_files_differ(ta, tb):
    return sorted(k for k in set(ta) | set(tb) if ta.get(k) != tb.get(k))


def run_load(item, out):
    a = os.path.join(out, "A.FCStd.d")
    snap = os.path.join(out, "A.snapshot")
    before = tree(a)
    import shutil
    shutil.copytree(a, snap)
    doc = App.openDocument(a)
    sink = {}
    result = {"dump": dump_doc(doc, sink), "filename": doc.FileName}
    orig = json.load(open(os.path.join(out, "shapes.json")))
    # Shapes are compared as BREP text: same tokens, numbers within 1e-12 (negative zero as zero)
    result["shape_mismatch"] = sorted(k for k in set(orig) | set(sink)
                                      if k not in orig or k not in sink or not brep_text_close(orig[k], sink[k]))
    c = os.path.join(out, "C.FCStd.d")
    doc.saveCopy(c)
    result["reopen_saveCopy_identical"] = tree(c) == before
    doc.save()  # back to the folder it was opened from
    t1 = tree(a)
    result["reopen_save_identical"] = t1 == before
    differing = tree_files_differ(before, t1)
    result["reopen_differs"] = differing
    result["reopen_status_only"] = benign_difference(snap, a, differing, "Fem" in item["id"] or "FEM" in item["id"]) if differing else True
    App.closeDocument(doc.Name)
    snap1 = os.path.join(out, "A.snapshot1")
    shutil.copytree(a, snap1)
    doc = App.openDocument(a)  # second reload
    doc.save()
    t2 = tree(a)
    result["settled_identical"] = t2 == t1
    differing2 = tree_files_differ(t1, t2)
    result["settled_benign"] = benign_difference(snap1, a, differing2, "Fem" in item["id"] or "FEM" in item["id"]) if differing2 else True
    App.closeDocument(doc.Name)
    return result


def main():
    mode, spec_path, out_path = sys.argv[-3:]
    spec = json.load(open(spec_path))
    results = {}
    for item in spec["items"]:
        out = os.path.join(spec["out"], item["id"])
        try:
            results[item["id"]] = (run_save if mode == "save" else run_load)(item, out)
        except Exception:  # noqa: BLE001
            results[item["id"]] = {"error": traceback.format_exc()}
            for d in list(App.listDocuments()):
                try:
                    App.closeDocument(d)
                except Exception:  # noqa: BLE001
                    pass
    json.dump(results, open(out_path, "w"))
    print("FOLDER_WORKER_DONE", mode, len(results))


main()
