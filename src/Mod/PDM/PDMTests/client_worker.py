# SPDX-License-Identifier: LGPL-2.1-or-later
"""One scripted PDM client, run inside its own FreeCADCmd process by the tests (never collected as a test).

Reads a JSON list of ops from the file named by PDM_OPS_FILE, runs them in order and prints one line,
``PDMRESULT:<json list>``, with one result per op. A ``call`` op names a Vault method and records either
its return value or the error class, message and lock holder, so tests can assert on refusals.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
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


def stem(doc):
    return os.path.basename(doc.FileName.rstrip("/"))[:-len(V.DOC_SUFFIX)]


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
    if kind == "build_link":
        # A document with one App::Link per target (targets are documents already in this working copy).
        targets = [App.openDocument(v.doc_path(t)) for t in op["targets"]]
        doc = App.newDocument(op["name"])
        doc.saveAsFolder(v.doc_path(op["name"]))  # the link path is relative to the saved owner
        for i, t in enumerate(targets):
            lk = doc.addObject("App::Link", f"Link{i}")
            lk.LinkedObject = t.getObject("Body")
        doc.save()
        out = {t.Name: metrics(t) for t in targets}
        for d in [doc] + targets:
            App.closeDocument(d.Name)
        return {"ok": True, "metrics": out}
    if kind == "link_check":
        # Open a linking document in this fresh process and report what each link resolves to.
        doc = App.openDocument(v.doc_path(op["name"]))
        res = []
        for o in doc.Objects:
            if o.TypeId == "App::Link":
                tgt = o.LinkedObject
                res.append({"link": o.Name, "resolved": tgt is not None, "valid": bool(tgt and tgt.isValid()),
                            "target_doc": tgt.Document.Label if tgt else None,
                            "target_file": os.path.basename(tgt.Document.FileName) if tgt else None,
                            "metrics": metrics(tgt.Document) if tgt else None})
        out = {"ok": True, "links": res}
        for d in list(App.listDocuments().values()):
            App.closeDocument(d.Name)
        return out
    if kind == "fc_graph":
        # FreeCAD's own link graph: export the newest revision of every document side by side, open them all and
        # ask FreeCAD who links to each document's Body (getLinksTo) and which documents each depends on.
        base = os.path.realpath(tempfile.mkdtemp(prefix="pdm-graph-"))  # a symlinked path would open linked documents twice
        docs = {}
        for n in op["names"]:
            v.export_revision(n, v.history(n)[0]["revision"], dest=base)
        for n in op["names"]:
            docs[n] = App.openDocument(os.path.join(base, n + V.DOC_SUFFIX))
        used = {}
        for n, d in docs.items():
            body = d.getObject("Body")
            users = {stem(x.Document) for x in App.getLinksTo(body)} if body else set()
            used[n] = sorted(u for u in users if u != n)
        deps = {n: sorted(stem(x) for x in d.getDependentDocuments() if stem(x) != n) for n, d in docs.items()}
        for d in list(App.listDocuments().values()):
            App.closeDocument(d.Name)
        subprocess.run(["chmod", "-R", "u+w", base], check=False)
        shutil.rmtree(base, ignore_errors=True)
        return {"ok": True, "used": used, "deps": deps}
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
