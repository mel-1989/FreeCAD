# SPDX-License-Identifier: LGPL-2.1-or-later
"""One scripted PDM client against a PDM server, run inside its own FreeCADCmd process (never collected as a test).

Same protocol as ``PDMTests/client_worker.py`` (it is imported and extended here): ops from the JSON file named by
``PDM_OPS_FILE``, one ``PDMRESULT:`` line out. ``PDM_SERVER_URL`` and ``PDM_TOKEN`` in the environment say which
server and which user's token this process acts with. Extra ops: ``race_loop``, ``raw_push``, ``outage_checkin``,
``http``, ``whoami``.
"""

import importlib.util
import os
import time

import FreeCAD as App

from PDM import remote as R
from PDM import vault as V

HERE = os.environ["PDM_WORKER_DIR"]  # exec'd by -c, so there is no __file__
spec = importlib.util.spec_from_file_location("pdm_client_worker", os.path.join(HERE, "..", "PDMTests", "client_worker.py"))
cw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cw)
_base_run = cw.run


def run(op):
    kind = op["op"]
    v = cw.STATE.get("v")
    if kind == "whoami":
        return {"ok": True, "value": v.whoami()}
    if kind == "race_loop":
        # Per trial: all clients try to lock the same document together; the winner releases before the next.
        won = []
        for t in range(op["trials"]):
            cw.barrier(op["dir"], f"t{t}a", v.user, op["n"])
            try:
                v.lock(op["doc"])
                mine = True
            except V.LockError:
                mine = False
            won.append(mine)
            cw.barrier(op["dir"], f"t{t}b", v.user, op["n"])
            if mine:
                v.release_lock(op["doc"])
            cw.barrier(op["dir"], f"t{t}c", v.user, op["n"])
        return {"ok": True, "won": won}
    if kind == "raw_push":
        # A client that skips the library's own checks: build a vault commit by hand and push it.
        name = op["name"]
        folder = os.path.basename(v.doc_path(name))
        v._fetch()
        if op.get("materialize"):
            V.shutil.rmtree(v.doc_path(name), ignore_errors=True)
            v._git(["checkout", "-q", v._head(), "--", folder])
        if op.get("append"):
            with open(os.path.join(v.doc_path(name), "Document.xml"), "a") as f:
                f.write(op["append"])
        edit = None
        if op.get("also_touch"):
            other = os.path.basename(v.doc_path(op["also_touch"]))
            edit = lambda env: v._git(["add", "-A", "-f", "--", other], env=env)  # noqa: E731
        _t, sha = v._commit(v._head(), name, op.get("message", "raw"), op["revision"], op.get("state", "WIP"),
                            op["event"], False, v.doc_path(name) if op.get("with_dir", True) else None, edit=edit)
        try:
            return {"ok": True, "pushed": v._push_main(sha)}
        except V.PDMError as e:
            return cw.err(e)
    if kind == "plain_doc":
        d = v.doc_path(op["name"])
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "Document.xml"), "w") as f:
            f.write(op["xml"])
        return {"ok": True, "value": v.add_document(op["name"])}
    if kind == "git_push":
        # A plain `git push <refspec>` with the user's credentials (no fetch first; the branch may be gone).
        p = v._git(["push", "-q", "origin", op["refspec"]], check=False)
        return {"ok": True, "pushed": p.returncode == 0, "stderr": p.stderr.decode(errors="replace")[-400:]}
    if kind == "forge":
        # Hand-built vault commit with arbitrary trailers, parents, author, files, deletions and symlinks.
        if not op.get("nofetch"):
            v._fetch()
        head = v._head()
        parents = [head if p == "head" else v._git(["rev-parse", f"{head}~1"]).stdout.decode().strip()
                   for p in op.get("parents", ["head"])]
        idx = os.path.join(v.workdir, ".git", "pdm-forge-index")
        env = {"GIT_INDEX_FILE": idx}
        try:
            if parents:
                v._git(["read-tree", head], env=env)
            for path in op.get("delete", []):
                v._git(["rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", path], env=env)
            for path, text in op.get("files", {}).items():
                sha = v._git(["hash-object", "-w", "--stdin"], input=text.encode()).stdout.decode().strip()
                v._git(["update-index", "--add", "--cacheinfo", f"100644,{sha},{path}"], env=env)
            for path, target in op.get("symlink", {}).items():
                sha = v._git(["hash-object", "-w", "--stdin"], input=target.encode()).stdout.decode().strip()
                v._git(["update-index", "--add", "--cacheinfo", f"120000,{sha},{path}"], env=env)
            tree = v._git(["write-tree"], env=env).stdout.decode().strip()
        finally:
            if os.path.exists(idx):
                os.remove(idx)
        tr = op["trailers"]
        msg = "forged\n\n" + "".join(f"PDM-{k}: {val}\n" for k, val in tr.items())
        cenv = {}
        if op.get("author"):
            cenv = {"GIT_AUTHOR_NAME": op["author"], "GIT_COMMITTER_NAME": op["author"]}
        if op.get("email"):
            cenv.update(GIT_AUTHOR_EMAIL=op["email"], GIT_COMMITTER_EMAIL=op["email"])
        args = ["commit-tree", tree] + sum((["-p", p] for p in parents), []) + ["-m", msg]
        sha = v._git(args, env=cenv).stdout.decode().strip()
        try:
            return {"ok": True, "pushed": v._push_main(sha)}
        except V.PDMError as e:
            return cw.err(e)
    if kind == "outage_checkin":
        # Pause just before the push so the test can take the service down, then go on.
        orig = v._push_main

        def paused(sha):
            open(op["ready"], "w").close()
            end = time.time() + 60
            while not os.path.exists(op["go"]) and time.time() < end:
                time.sleep(0.05)
            return orig(sha)

        v._push_main = paused
        try:
            return {"ok": True, "value": v.checkin(op["name"], op["message"])}
        except V.PDMError as e:
            return cw.err(e)
        finally:
            v._push_main = orig
    return _base_run(op)


cw.run = run
if __name__ == "__main__":
    cw.main()
