# SPDX-License-Identifier: LGPL-2.1-or-later
"""One scripted PDM client, run inside its own FreeCADCmd process by the tests (never collected as a test).

Reads a JSON list of ops from the file named by PDM_OPS_FILE, runs them in order and prints one line,
``PDMRESULT:<json list>``, with one result per op. A ``call`` op names a Vault method and records either
its return value or the error class, message and lock holder, so tests can assert on refusals.
"""

import json
import os
import sys
import time

import FreeCAD as App
import Part  # noqa: F401  (loads the Part module for PartDesign)
import Sketcher  # noqa: F401

from PDM import vault as V

STATE = {}


def build_pad(name, length):
    doc = App.newDocument(name)
    body = doc.addObject("PartDesign::Body", "Body")
    sk = body.newObject("Sketcher::SketchObject", "Sketch")
    xy = [o for o in body.Origin.OriginFeatures if o.Role == "XY_Plane"][0]
    sk.AttachmentSupport = [(xy, "")]
    sk.MapMode = "FlatFace"
    pts = [(0, 0), (20, 0), (20, 10), (0, 10)]
    for i in range(4):
        a, b = pts[i], pts[(i + 1) % 4]
        sk.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0)))
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = length
    doc.recompute()
    return doc


def metrics(doc):
    shape = doc.getObject("Body").Shape
    bb = shape.BoundBox
    return {"volume": shape.Volume, "area": shape.Area, "bbox": [bb.XLength, bb.YLength, bb.ZLength]}


def barrier(directory, tag, user, n, timeout=120):
    open(os.path.join(directory, f"{tag}.{user}"), "w").close()
    end = time.time() + timeout
    while time.time() < end:
        if len([f for f in os.listdir(directory) if f.startswith(tag + ".")]) >= n:
            return
        time.sleep(0.002)
    raise RuntimeError(f"barrier {tag} timed out")


def err(e):
    return {"error": type(e).__name__, "message": str(e), "holder": getattr(e, "holder", None)}


def run(op):
    kind = op["op"]
    if kind == "clone":
        STATE["v"] = V.Vault.clone(op["vault"], op["wc"], op["user"])
        return {"ok": True}
    if kind == "open":
        STATE["v"] = V.Vault.open(op["wc"], op["user"])
        return {"ok": True}
    v = STATE["v"]
    if kind == "build_pad":
        doc = build_pad(op["name"], op["length"])
        doc.saveAsFolder(v.doc_path(op["name"]))
        out = metrics(doc)
        App.closeDocument(doc.Name)
        return {"ok": True, "metrics": out}
    if kind == "set_pad":
        doc = App.openDocument(v.doc_path(op["name"]))
        doc.getObject("Pad").Length = op["length"]
        doc.recompute()
        doc.save()
        out = metrics(doc)
        App.closeDocument(doc.Name)
        return {"ok": True, "metrics": out}
    if kind == "revision_metrics":
        doc = v.open_revision(op["name"], op["revision"])
        out = metrics(doc)
        out["filename"] = doc.FileName
        App.closeDocument(doc.Name)
        return {"ok": True, "metrics": out}
    if kind == "call":
        try:
            r = getattr(v, op["m"])(*op.get("a", []), **op.get("k", {}))
            return {"ok": True, "value": r}
        except V.PDMError as e:
            return err(e)
    if kind == "race":
        won = {}
        for d in op["docs"]:
            barrier(op["dir"], d, v.user, 2)
            try:
                v.lock(d)
                won[d] = True
            except V.LockError as e:
                won[d] = False
        return {"ok": True, "won": won}
    raise RuntimeError(f"unknown op {kind}")


def main():
    with open(os.environ["PDM_OPS_FILE"]) as f:
        ops = json.load(f)
    results = []
    for op in ops:
        try:
            results.append(run(op))
        except Exception as e:  # report, do not hide, unexpected failures
            results.append({"crash": f"{type(e).__name__}: {e}"})
            break
    sys.stdout.write("PDMRESULT:" + json.dumps(results, default=str) + "\n")
    sys.stdout.flush()


main()
