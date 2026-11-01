# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD worker for the PDM web UI: run by ``FreeCADCmd -c "exec(open(...).read())"``, never imported by the service.

The web process does not run FreeCAD (tools/pdm-server/README.md). When a page needs a BOM, a 3D model or an ECO
diff that is not in the cache, the service starts this script as a separate process, which writes the artifacts
into the cache and exits. Everything it computes comes from the library: ``Vault.bom`` (through ``PDM.bom``),
``PDM.bom.flatten`` and ``to_csv``, ``PDM.eco.eco_diff`` (which is ``Vault.diff`` per document), and F3's
``AgentCore.diff.geometry.shape_diff`` for the added and removed material of the 3D diff; there is no second BOM
or differ here.

The job is a JSON file named by ``PDM_JOB``::

    {"kind": "revision", "name": "bracket", "revision": "A.2", "sha": "<record sha>", "cache": "<dir>", ...}
    {"kind": "eco", "number": "ECO-0001", "eco_commit": "<sha>", "key": "eco-ECO-0001-<hash>", ...}
    common keys: "git_url", "token_env" (name of the env var holding the Forgejo token), "lib" (the PDM package dir)

Revision job output, ``<cache>/<sha>/<name>/``: ``bom.json``, ``bom.csv``, ``bom-flat.csv``, ``model.glb``, ``meta.json``.
ECO job output, ``<cache>/<eco_commit>/<key>/``: ``diff.json`` (``Vault.eco_diff`` verbatim),
``<doc>-old.glb``, ``<doc>-new.glb``, ``<doc>-diff.glb`` (two nodes, ``added`` and ``removed``: the pieces F3's
overlay draws, coloured by the viewer), ``meta.json``. Output is built in a temporary directory beside the final
one and renamed into place, so a reader never sees a half-written entry. Exit status 0 only when the entry exists.
"""

import json
import os
import shutil
import sys
import traceback
import uuid

JOB = json.load(open(os.environ["PDM_JOB"]))
if JOB.get("lib") and JOB["lib"] not in sys.path:
    sys.path.insert(0, JOB["lib"])

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402

from PDM import bom as B  # noqa: E402
from PDM import diff as D  # noqa: E402
from PDM import eco as E  # noqa: E402
from PDM import vault as V  # noqa: E402

DEFLECTION_DIVISOR = 300.0  # mesh deflection = bounding-box diagonal / 300, the same for every export (deterministic)


class WorkerVault(V.Vault):
    """The worker's own clone of the vault branch (not the service's mirror: two fetches on one repository race)."""

    def _fetch_specs(self):
        return [f"+refs/heads/{V.BRANCH}:refs/remotes/origin/{V.BRANCH}"]


def open_vault():
    token = os.environ.get(JOB["token_env"], "")
    env = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader",
           "GIT_CONFIG_VALUE_0": f"Authorization: token {token}", "GIT_TERMINAL_PROMPT": "0"}
    wd = os.path.join(JOB["cache"], "_clone")
    if os.path.isdir(os.path.join(wd, ".git")):
        return WorkerVault(wd, "pdm-worker", JOB["git_url"], git_env=env)
    os.makedirs(JOB["cache"], exist_ok=True)
    return WorkerVault.clone(JOB["git_url"], wd, "pdm-worker", git_env=env)


# ---- glTF export -------------------------------------------------------------------------------------

def export_glb(shapes, path, name):
    """Write ``shapes`` as one node named ``name`` of a binary glTF. FreeCAD's own exporter (``Import.export``) writes
    only meshed shapes, so each is tessellated first with a deflection fixed by the model size."""
    import Import
    shapes = [s for s in shapes if s is not None and not s.isNull()]
    if not shapes:
        return False
    comp = Part.makeCompound(shapes)
    comp.tessellate(max(comp.BoundBox.DiagonalLength / DEFLECTION_DIVISOR, 1e-3))
    doc = App.newDocument("glb" + uuid.uuid4().hex[:6])
    try:
        feat = doc.addObject("Part::Feature", name)
        feat.Shape = comp
        Import.export([feat], path)
    finally:
        App.closeDocument(doc.Name)
    return os.path.isfile(path)


def doc_shapes(doc):
    """The shapes a viewer should draw for a document: its top-level objects (nothing else depends on them) that
    have a solid or face shape; links resolve through ``Part.getShape`` so assemblies come out placed."""
    out = []
    for o in doc.Objects:
        if o.InList or o.isDerivedFrom("App::Origin") or o.isDerivedFrom("App::OriginFeature"):
            continue
        if o.TypeId.startswith("Sketcher::") or o.TypeId.startswith("PartDesign::Plane") or o.TypeId == "App::DocumentObjectGroup":
            continue
        try:
            s = Part.getShape(o)
        except Exception:
            continue
        if s is not None and not s.isNull() and (s.Solids or s.Faces):
            out.append(s)
    return out


def publish(tmp, final):
    """Rename the finished directory into place (an existing entry is replaced)."""
    os.makedirs(os.path.dirname(final), exist_ok=True)
    if os.path.isdir(final):
        shutil.rmtree(final)
    os.rename(tmp, final)


def scratch(parent):
    d = os.path.join(parent, "tmp-" + uuid.uuid4().hex)
    os.makedirs(d)
    return d


# ---- jobs --------------------------------------------------------------------------------------------

def revision_job(v):
    name, rev, sha = JOB["name"], JOB["revision"], JOB["sha"]
    rec = v.find_revision(name, rev)
    if rec["sha"] != sha:
        raise RuntimeError(f"stale job: {name} {rev} is now at {rec['sha']}, not {sha}")
    final = os.path.join(JOB["cache"], sha, name)
    tmp = scratch(JOB["cache"])
    try:
        rows = v.bom(name, rev)
        with open(os.path.join(tmp, "bom.json"), "w") as f:
            json.dump({"rows": rows, "flat": B.flatten(rows)}, f, indent=1, sort_keys=True)
        for fn, flat in (("bom.csv", False), ("bom-flat.csv", True)):
            with open(os.path.join(tmp, fn), "w", newline="") as f:
                f.write(B.to_csv(rows, flat=flat))
        glb = False
        with B.Tree(v, sha) as tree:
            doc = tree.open(rec.get("document") or name)
            glb = export_glb(doc_shapes(doc), os.path.join(tmp, "model.glb"), "model")
        with open(os.path.join(tmp, "meta.json"), "w") as f:
            json.dump({"kind": "revision", "name": name, "revision": rev, "sha": sha, "glb": glb}, f, sort_keys=True)
        publish(tmp, final)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def eco_job(v):
    number = JOB["number"]
    last = v._git(["log", "-1", "--format=%H", f"refs/remotes/origin/{V.BRANCH}", "--", f"{E.ECO_DIR}{number}.json"]).stdout.decode().strip()
    if last != JOB["eco_commit"]:
        raise RuntimeError(f"stale job: {number} was last changed by {last}, not {JOB['eco_commit']}")
    result = E.eco_diff(v, number)
    final = os.path.join(JOB["cache"], JOB["eco_commit"], JOB["key"])
    tmp = scratch(JOB["cache"])
    try:
        with open(os.path.join(tmp, "diff.json"), "w") as f:
            json.dump(result, f, indent=1, sort_keys=True)
        files = {}
        for item in result:
            d, frm, to = item["document"], item["diff"]["from"], item["diff"]["to"]
            files[d] = glb_for_document(v, d, frm, to, item["diff"], tmp)
        with open(os.path.join(tmp, "meta.json"), "w") as f:
            json.dump({"kind": "eco", "number": number, "eco_commit": JOB["eco_commit"], "files": files}, f, sort_keys=True)
        publish(tmp, final)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def glb_for_document(v, name, frm, to, diff, out):
    """old/new/diff glbs of one document; the added and removed pieces are the ones F3's overlay draws
    (``AgentCore.diff.geometry.shape_diff``'s local booleans, whole shapes for added or removed objects)."""
    from AgentCore.diff import geometry as G
    with B.Tree(v, frm["sha"]) as ta, B.Tree(v, to["sha"]) as tb:
        da, db = ta.open(frm["document"]), tb.open(to["document"])
        old_ok = export_glb(doc_shapes(da), os.path.join(out, f"{name}-old.glb"), "model")
        new_ok = export_glb(doc_shapes(db), os.path.join(out, f"{name}-new.glb"), "model")
        names = {s["object"] for s in diff["shapes"]}
        consumed = set()  # an object another changed object consumes (a Pad in its Body) is drawn once, by its consumer
        for doc in (da, db):
            for n in names:
                o = doc.getObject(n)
                if o is not None:
                    consumed |= {x.Name for x in o.OutList if x.Name != n}
        added, removed = [], []
        for s in diff["shapes"]:
            n, status = s["object"], s["status"]
            if n in consumed:
                continue
            sa = D._object_shape(da, n) if status != "added" else None
            sb = D._object_shape(db, n) if status != "removed" else None
            if sa is None and sb is not None:
                added.append(sb)
            elif sb is None and sa is not None:
                removed.append(sa)
            elif sa is not None and sb is not None:
                keep = {}
                G.shape_diff(sa, sb, keep=keep)
                if keep.get("added") is not None or keep.get("removed") is not None:
                    added += [keep["added"]] if keep.get("added") is not None else []
                    removed += [keep["removed"]] if keep.get("removed") is not None else []
                elif sa.Solids and sb.Solids:  # no local pieces (the matcher saw no region): whole-shape booleans
                    try:
                        a, r = sb.cut(sa), sa.cut(sb)
                        added += [a] if a.Solids else []
                        removed += [r] if r.Solids else []
                    except Exception:
                        pass
        # one glb, two nodes: export "added" and "removed" into the same file via two features
        diff_ok = export_diff_glb(added, removed, os.path.join(out, f"{name}-diff.glb"))
    return {"old": old_ok, "new": new_ok, "diff": diff_ok, "added": len(added), "removed": len(removed)}


def export_diff_glb(added, removed, path):
    import Import
    doc = App.newDocument("dglb" + uuid.uuid4().hex[:6])
    try:
        feats = []
        everything = [s for s in added + removed if s is not None and not s.isNull()]
        if not everything:
            return False
        dia = Part.makeCompound(everything).BoundBox.DiagonalLength
        for label, shapes in (("added", added), ("removed", removed)):
            shapes = [s for s in shapes if s is not None and not s.isNull()]
            if not shapes:
                continue
            comp = Part.makeCompound(shapes)
            comp.tessellate(max(dia / DEFLECTION_DIVISOR, 1e-3))
            f = doc.addObject("Part::Feature", label)
            f.Shape = comp
            feats.append(f)
        Import.export(feats, path)
    finally:
        App.closeDocument(doc.Name)
    return os.path.isfile(path)


def main():
    code = 0
    try:
        v = open_vault()
        v._fetch()
        {"revision": revision_job, "eco": eco_job}[JOB["kind"]](v)
    except BaseException as exc:  # the service reads this to say why the entry is missing
        code = 1
        with open(os.environ["PDM_JOB"] + ".error", "w") as f:
            f.write("".join(traceback.format_exception_only(type(exc), exc)).strip() + "\n\n" + traceback.format_exc())
    sys.stdout.flush()
    os._exit(code)  # FreeCADCmd -c would otherwise drop into its interpreter loop or run teardown that can hang


main()
