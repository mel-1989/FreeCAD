# SPDX-License-Identifier: LGPL-2.1-or-later
"""The PDM web UI (G03 slice 6b): server-rendered pages under ``/web``, served by the same FastAPI app.

Reads: the service's mirror of the vault (documents, revisions, ECOs, history; the rules are ``PDM.vault`` and
``PDM.eco``), the lock table (holders) and the artifact cache. The web process never runs FreeCAD: a BOM, a glTF
model or an ECO diff that is not in the cache is built by ``fcworker.py`` in a separate ``FreeCADCmd`` process
(``Jobs`` below starts it, one at a time) and the page says "pending" and refreshes until it is there.

Writes: the approve and reject buttons of an ECO. They call ``PDM.eco.write`` (the library's own path) from a working
copy of the vault owned by the logged-in user, pushing with *that user's* Forgejo token, so the pre-receive hook sees
the real pusher, binds author and committer to them and checks the two-person rule against them. The service's own
token is never used to write.

Login: the user pastes a Forgejo access token (the identity the service already trusts); it is checked with Forgejo
and kept in a server-side session behind an HttpOnly cookie, never in a URL or a page. Every POST carries a CSRF token.
"""

import hashlib
import hmac
import html
import json
import os
import re
import secrets
import subprocess
import tempfile
import threading
import time
import urllib.parse

import jinja2
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(os.path.dirname(HERE), "static")
COOKIE = "pdm_session"
LOGIN_COOKIE = "pdm_login"
SESSION_TTL = 8 * 3600
JOB_TIMEOUT = 900
SHA_RE = re.compile(r"[0-9a-f]{40}")
KEY_RE = re.compile(r"eco-ECO-\d{4,8}-[0-9a-f]{10}")
REV_RE = re.compile(r"[A-Z]+\.\d+")
FILE_RE = re.compile(r"(model|[A-Za-z0-9_.-]+-(old|new|diff))\.glb|bom\.csv|bom-flat\.csv|diff\.json")
OVERLAY = {"added": (0.10, 0.75, 0.20), "removed": (0.90, 0.15, 0.15)}  # AgentCore.gui.overlay.COLORS (pivy-free copy)
OVERLAY_OPACITY = 0.45  # 1 - AgentCore.gui.overlay.TRANSPARENCY


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(round(c * 255) for c in rgb)


env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(HERE, "templates")),
                         autoescape=jinja2.select_autoescape(["html"]), undefined=jinja2.StrictUndefined)
env.filters["num"] = lambda x, p=3: "" if x is None else (f"{x:.{p}f}".rstrip("0").rstrip(".") if isinstance(x, float) else str(x))


class Jobs:
    """Runs the FreeCAD worker for cache misses, one job at a time, in the background."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.running = {}   # final directory -> start time
        self.failed = {}    # final directory -> (time, message)
        self.slot = threading.Semaphore(1)

    @property
    def enabled(self):
        return bool(self.cfg.freecad_cmd) and os.path.isfile(self.cfg.freecad_cmd)

    def state(self, final, job):
        """'ready', 'pending', 'failed' or 'disabled' for the cache entry ``final``; starts the worker when it is missing."""
        if os.path.isdir(final):
            return "ready", None
        if not self.enabled:
            return "disabled", "no FreeCAD worker is configured (set PDM_FREECAD_CMD)"
        with self.lock:
            if final in self.running:
                return "pending", None
            bad = self.failed.get(final)
            if bad and time.time() - bad[0] < 30:  # do not hammer a failing job; retry on a later load
                return "failed", bad[1]
            self.failed.pop(final, None)
            self.running[final] = time.time()
        threading.Thread(target=self._run, args=(final, job), daemon=True).start()
        return "pending", None

    def _run(self, final, job):
        err = None
        try:
            with self.slot:
                if os.path.isdir(final):
                    return
                os.makedirs(self.cfg.cache_dir, exist_ok=True)
                fd, path = tempfile.mkstemp(prefix="job-", suffix=".json", dir=self.cfg.cache_dir)
                job = dict(job, cache=self.cfg.cache_dir, git_url=self.cfg.git_url, token_env="PDM_WORKER_TOKEN",
                           lib=self.cfg.vault_lib)
                with os.fdopen(fd, "w") as f:
                    json.dump(job, f)
                worker = os.path.join(HERE, "fcworker.py")
                env_ = dict(os.environ, PDM_JOB=path, PDM_WORKER_TOKEN=self.cfg.forgejo_token, QT_QPA_PLATFORM="offscreen")
                try:
                    p = subprocess.run([self.cfg.freecad_cmd, "-c", f"exec(open({worker!r}).read())"], env=env_,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, timeout=JOB_TIMEOUT)
                    if not os.path.isdir(final):
                        try:
                            err = open(path + ".error").read()[:1500]
                        except OSError:
                            err = (p.stdout or "")[-800:] or f"worker exited {p.returncode} without output"
                except subprocess.TimeoutExpired:
                    err = f"worker timed out after {JOB_TIMEOUT} s"
                finally:
                    for x in (path, path + ".error"):
                        try:
                            os.remove(x)
                        except OSError:
                            pass
        except Exception as exc:  # pragma: no cover
            err = f"{type(exc).__name__}: {exc}"
        finally:
            with self.lock:
                self.running.pop(final, None)
                if err and not os.path.isdir(final):
                    self.failed[final] = (time.time(), err)


def install(app, S, effective_role, User):
    sessions = {}
    slock = threading.Lock()
    jobs_holder = {}
    webvault_lock = threading.Lock()  # one write at a time per service: the per-user clones share nothing but are few

    if os.path.isdir(STATIC):
        app.mount("/static", StaticFiles(directory=STATIC, check_dir=False), name="static")
    else:  # the service must start with no web assets
        pass

    def jobs(st):
        if "j" not in jobs_holder:
            jobs_holder["j"] = Jobs(st["cfg"])
        return jobs_holder["j"]

    # ---- sessions and CSRF ----------------------------------------------------------------------

    def page(request, _tpl, status=200, **ctx):
        sess = ctx.pop("sess", None)
        ctx.update(csrf=sess["csrf"] if sess else "", me=ctx.get("me"), flash=ctx.get("flash"))
        resp = HTMLResponse(env.get_template(_tpl).render(**ctx), status_code=status)
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        return resp

    def get_session(request):
        sid = request.cookies.get(COOKIE)
        with slock:
            s = sessions.get(sid) if sid else None
            if s and time.time() - s["created"] > SESSION_TTL:
                sessions.pop(sid, None)
                s = None
        return s

    def whom(st, sess):
        """Identify the session's token afresh (a revoked token or a removed collaborator loses access at once)."""
        who = st["fj"].login_of(sess["token"])
        if who is None:
            return None
        role = st["fj"].role_of(*who)
        if role is None:
            return None
        user = User(who[0], role)
        eff = effective_role(st, user)
        return {"login": user.login, "base": role, "role": eff["role"]}

    def need_login(request):
        """(sess, me, st) for a logged-in request, else a redirect response."""
        st = S()
        sess = get_session(request)
        me = whom(st, sess) if sess else None
        if me is None:
            return None, None, st
        return sess, me, st

    def same_origin(request):
        origin = request.headers.get("origin")
        if not origin or origin == "null":
            return origin is None
        return urllib.parse.urlsplit(origin).netloc == request.headers.get("host", "")

    async def form(request):
        body = (await request.body()).decode("utf-8", "replace")
        return {k: v[0] for k, v in urllib.parse.parse_qs(body, keep_blank_values=True).items()}

    def to_login():
        return RedirectResponse("/web/login", status_code=303)

    # ---- login / logout -------------------------------------------------------------------------

    @app.get("/")
    def root():
        return RedirectResponse("/web/", status_code=303)

    @app.get("/web/login")
    def login_form(request: Request):
        tok = secrets.token_urlsafe(24)
        resp = page(request, "login.html", login_csrf=tok, message=None, me=None)
        resp.set_cookie(LOGIN_COOKIE, tok, httponly=True, samesite="strict", path="/web")
        return resp

    @app.post("/web/login")
    async def login(request: Request):
        st = S()
        f = await form(request)
        cookie = request.cookies.get(LOGIN_COOKIE, "")
        if not same_origin(request) or not cookie or not hmac.compare_digest(cookie, f.get("login_csrf", "")):
            return page(request, "login.html", 403, login_csrf="", message="The login form expired; reload and try again.", me=None)
        token = f.get("token", "").strip()
        who = st["fj"].login_of(token) if token else None
        role = st["fj"].role_of(*who) if who else None
        if who is None or role is None:
            tok = secrets.token_urlsafe(24)
            resp = page(request, "login.html", 401, login_csrf=tok, me=None,
                        message="That token was not accepted, or its user has no access to the vault.")
            resp.set_cookie(LOGIN_COOKIE, tok, httponly=True, samesite="strict", path="/web")
            return resp
        sid = secrets.token_urlsafe(32)
        with slock:
            for k in [k for k, s in sessions.items() if time.time() - s["created"] > SESSION_TTL]:
                sessions.pop(k, None)
            sessions[sid] = {"token": token, "login": who[0], "csrf": secrets.token_urlsafe(24), "created": time.time()}
        resp = RedirectResponse("/web/", status_code=303)
        resp.set_cookie(COOKIE, sid, httponly=True, samesite="lax", path="/", max_age=SESSION_TTL,
                        secure=request.url.scheme == "https")
        resp.delete_cookie(LOGIN_COOKIE, path="/web")
        return resp

    @app.post("/web/logout")
    async def logout(request: Request):
        sess = get_session(request)
        f = await form(request)
        if sess and same_origin(request) and hmac.compare_digest(sess["csrf"], f.get("csrf", "")):
            with slock:
                sessions.pop(request.cookies.get(COOKIE), None)
        resp = RedirectResponse("/web/login", status_code=303)
        resp.delete_cookie(COOKIE, path="/")
        return resp

    # ---- read helpers (git only; the mirror lock is held while the clone is read) ----------------

    def vault_read(st, fn):
        with st["mirror"].lock:
            v, head = st["mirror"].sync()
            return fn(v, head)

    def eco_mod(st):
        import importlib
        return importlib.import_module(st["mirror"].V.__name__.rsplit(".", 1)[0] + ".eco")

    # ---- pages ----------------------------------------------------------------------------------

    @app.get("/web/")
    def documents(request: Request):
        sess, me, st = need_login(request)
        if me is None:
            return to_login()
        locks = st["db"].locks()
        docs = vault_read(st, lambda v, head: v.documents())
        rows = [{"name": n, "revision": r["revision"], "state": r["state"], "author": r["author"], "time": r["time"],
                 "lock": locks.get(n, {}).get("holder")} for n, r in sorted(docs.items())]
        return page(request, "docs.html", sess=sess, me=me, rows=rows, nav="docs")

    def revision_view(st, name, v):
        st["mirror"].V._check_name(name)
        return v.history(name)

    @app.get("/web/doc/{name}")
    def document(request: Request, name: str):
        sess, me, st = need_login(request)
        if me is None:
            return to_login()
        V = st["mirror"].V
        try:
            recs = vault_read(st, lambda v, head: revision_view(st, name, v))
        except V.PDMError as e:
            return page(request, "error.html", 404, sess=sess, me=me, message=str(e), nav="docs")
        lock = st["db"].lock(name)
        return page(request, "doc.html", sess=sess, me=me, name=name, recs=recs, content=V.CONTENT_EVENTS, lock=lock, nav="docs")

    def revision_entry(st, v, name, revision):
        V = st["mirror"].V
        if not REV_RE.fullmatch(revision):
            raise V.DocumentError(f"bad revision label {revision!r}")
        rec = v.find_revision(name, revision)
        return rec

    @app.get("/web/doc/{name}/rev/{revision}")
    def revision(request: Request, name: str, revision: str):
        sess, me, st = need_login(request)
        if me is None:
            return to_login()
        V = st["mirror"].V
        try:
            rec = vault_read(st, lambda v, head: (V._check_name(name), revision_entry(st, v, name, revision))[1])
        except V.PDMError as e:
            return page(request, "error.html", 404, sess=sess, me=me, message=str(e), nav="docs")
        final = os.path.join(st["cfg"].cache_dir, rec["sha"], name)
        state, why = jobs(st).state(final, {"kind": "revision", "name": name, "revision": revision, "sha": rec["sha"]})
        bom = glb = None
        if state == "ready":
            with open(os.path.join(final, "bom.json")) as f:
                bom = json.load(f)
            glb = os.path.isfile(os.path.join(final, "model.glb"))
        base = f"/web/artifact/{rec['sha']}/{urllib.parse.quote(name)}"
        return page(request, "rev.html", sess=sess, me=me, name=name, rec=rec, state=state, why=why, bom=bom,
                    glb=glb, base=base, nav="docs")

    def artifact_path(st, *parts):
        path = os.path.realpath(os.path.join(st["cfg"].cache_dir, *parts))
        root = os.path.realpath(st["cfg"].cache_dir)
        if not path.startswith(root + os.sep) or not os.path.isfile(path):
            raise HTTPException(404, "not in the cache (yet)")
        return path

    @app.get("/web/artifact/{sha}/{name}/{file}")
    def artifact(request: Request, sha: str, name: str, file: str):
        sess, me, st = need_login(request)
        if me is None:
            return to_login()
        V = st["mirror"].V
        try:
            V._check_name(name)
        except V.PDMError:
            raise HTTPException(404, "no such artifact") from None
        if not SHA_RE.fullmatch(sha) or not FILE_RE.fullmatch(file):
            raise HTTPException(404, "no such artifact")
        return serve(artifact_path(st, sha, name, file), file, name)

    @app.get("/web/eco-artifact/{sha}/{key}/{file}")
    def eco_artifact(request: Request, sha: str, key: str, file: str):
        sess, me, st = need_login(request)
        if me is None:
            return to_login()
        if not SHA_RE.fullmatch(sha) or not KEY_RE.fullmatch(key) or not FILE_RE.fullmatch(file):
            raise HTTPException(404, "no such artifact")
        return serve(artifact_path(st, sha, key, file), file, key)

    def serve(path, file, stem):
        if file.endswith(".csv"):
            return FileResponse(path, media_type="text/csv; charset=utf-8",
                                headers={"Content-Disposition": f'attachment; filename="{stem}-{file}"'})
        if file.endswith(".glb"):
            return FileResponse(path, media_type="model/gltf-binary")
        return FileResponse(path, media_type="application/json")

    @app.get("/web/ecos")
    def ecos(request: Request):
        sess, me, st = need_login(request)
        if me is None:
            return to_login()
        E = eco_mod(st)
        recs = vault_read(st, lambda v, head: E.read_all(v, head))
        rows = [{"number": n, "title": r["title"], "state": r["state"], "author": r["author"],
                 "documents": ", ".join(f"{d['name']} {d['revision']}" for d in r["documents"])}
                for n, r in sorted(recs.items())]
        return page(request, "ecos.html", sess=sess, me=me, rows=rows, nav="ecos")

    def eco_context(st, number):
        """Everything the ECO page shows, read from git: the record, and per document the revisions the diff compares."""
        E, V = eco_mod(st), st["mirror"].V

        def read(v, head):
            if not re.fullmatch(E.NUM_RE, number):
                raise V.PDMError(f"bad ECO number {number!r}")
            rec = E.load(v, head, number)
            if rec is None:
                raise V.PDMError(f"no ECO {number}")
            last = v._git(["log", "-1", "--format=%H", head, "--", f"{E.ECO_DIR}{number}.json"]).stdout.decode().strip()
            docs, ident = [], []
            for d in rec["documents"]:
                item = {"name": d["name"], "to": d["revision"], "from": None, "error": None}
                try:
                    frm = E.base_revision(v, d["name"], d["revision"])
                    a, b = v.find_revision(d["name"], frm), v.find_revision(d["name"], d["revision"])
                    item.update({"from": frm})
                    ident.append(f"{d['name']}:{a['sha']}:{b['sha']}")
                except V.PDMError as e:
                    item["error"] = str(e)
                docs.append(item)
            key = f"eco-{number}-" + hashlib.sha1("|".join(ident).encode()).hexdigest()[:10]
            return rec, last, key, docs
        return vault_read(st, read)

    @app.get("/web/ecos/{number}")
    def eco(request: Request, number: str):
        sess, me, st = need_login(request)
        if me is None:
            return to_login()
        return render_eco(request, st, sess, me, number)

    def render_eco(request, st, sess, me, number, status=200, flash=None):
        V = st["mirror"].V
        try:
            rec, last, key, docs = eco_context(st, number)
        except V.PDMError as e:
            return page(request, "error.html", 404, sess=sess, me=me, message=str(e), nav="ecos")
        final = os.path.join(st["cfg"].cache_dir, last, key)
        state, why = jobs(st).state(final, {"kind": "eco", "number": number, "eco_commit": last, "key": key})
        diffs = {}
        files = {}
        if state == "ready":
            with open(os.path.join(final, "diff.json")) as f:
                for item in json.load(f):
                    diffs[item["document"]] = item
            with open(os.path.join(final, "meta.json")) as f:
                files = json.load(f)["files"]
        for d in docs:
            d["diff"] = diffs.get(d["name"])
            d["files"] = files.get(d["name"], {})
        can_review = me["role"] in ("approver", "admin") and rec["state"] == "Submitted" and (
            rec["author"] != me["login"] or me["role"] == "admin")
        base = f"/web/eco-artifact/{last}/{key}"
        return page(request, "eco.html", status, sess=sess, me=me, rec=rec, docs=docs, state=state, why=why, base=base,
                    can_review=can_review, flash=flash, done=request.query_params.get("done"), nav="ecos",
                    colors={k: _hex(c) for k, c in OVERLAY.items()}, opacity=OVERLAY_OPACITY)

    # ---- the one write: approve and reject, pushed as the logged-in user --------------------------

    def user_vault(st, sess, me):
        """A working copy of the vault for this user, pushing with their own token (the same library path as a client)."""
        from PDM import remote as R
        V = st["mirror"].V
        cfg = st["cfg"]
        wd = os.path.join(cfg.mirror_dir, "web", me["login"])
        env_ = R._auth_env(sess["token"])
        if not os.path.isdir(os.path.join(wd, ".git")):
            os.makedirs(os.path.dirname(wd), exist_ok=True)
            V._git(["clone", "-q", "--no-checkout", cfg.git_url, wd], env=dict(env_, GIT_TERMINAL_PROMPT="0"))
            os.makedirs(os.path.join(wd, ".git", "pdm", "base"), exist_ok=True)
        role = me["role"]

        class WebVault(R.RemoteVault):
            """RemoteVault's push and error mapping; its role is the one the service computed, and a fetch is plain git."""

            def _fetch(self):
                V.Vault._fetch(self)

            def _role(self):
                return role

        # server_url is only required by RemoteVault's constructor; this subclass never calls the service's REST API
        return WebVault(wd, me["login"], cfg.git_url, server_url="http://unused.invalid", token=sess["token"],
                        git_env={"GIT_TERMINAL_PROMPT": "0"})

    @app.post("/web/ecos/{number}/{action}")
    async def eco_action(request: Request, number: str, action: str):
        sess, me, st = need_login(request)
        if me is None:
            return to_login()
        f = await form(request)
        if not same_origin(request) or not hmac.compare_digest(sess["csrf"], f.get("csrf", "")):
            return page(request, "error.html", 403, sess=sess, me=me, message="The form's security token did not match; reload the page and try again.", nav="ecos")
        if action not in ("approve", "reject"):
            raise HTTPException(404, "no such action")
        if me["role"] not in ("approver", "admin"):
            return page(request, "error.html", 403, sess=sess, me=me, nav="ecos",
                        message=f"{me['login']} is a {me['role']}; approving or rejecting needs an approver or an admin.")
        E, V = eco_mod(st), st["mirror"].V
        comment = f.get("comment", "").strip() or None
        try:
            import anyio
            result = await anyio.to_thread.run_sync(do_write, st, sess, me, number, action, comment)
        except V.PDMError as e:
            code = 403 if type(e).__name__ in ("AccessError", "PermissionDenied") else 409
            return page(request, "error.html", code, sess=sess, me=me, message=f"{action} refused: {e}", nav="ecos")
        return RedirectResponse(f"/web/ecos/{urllib.parse.quote(number)}?done={action}", status_code=303)

    def do_write(st, sess, me, number, action, comment):
        E = eco_mod(st)
        with webvault_lock:
            v = user_vault(st, sess, me)
            return E.write(v, number, action, comment)

    # ---- health of the web layer ----------------------------------------------------------------

    @app.get("/web/status")
    def web_status():
        """No login: whether the web layer has what it needs (assets and worker); never anything about the vault."""
        st = S()
        j = jobs(st)
        return {"static": os.path.isdir(STATIC), "worker": j.enabled, "cache": os.path.isdir(st["cfg"].cache_dir)}
