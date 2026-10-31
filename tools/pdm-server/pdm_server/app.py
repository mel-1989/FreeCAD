# SPDX-License-Identifier: LGPL-2.1-or-later
"""PDM server: FastAPI app (``uvicorn pdm_server.app:app``). See ../README.md."""

import datetime
import threading
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from . import policy
from .config import Config
from .db import Db, DbDown
from .forgejo import Forgejo, ForgejoDown
from .mirror import Mirror

app = FastAPI(title="PDM server", docs_url=None, redoc_url=None, openapi_url=None)
_state = {}
_init_lock = threading.Lock()


def S():
    """Lazily built shared state (config, clients, mirror), so a database or Forgejo that is down at start-up
    does not stop the service from starting and answering 503 until they are back."""
    with _init_lock:
        if "cfg" not in _state:
            cfg = Config()
            _state.update(cfg=cfg, db=Db(cfg.database_url), fj=Forgejo(cfg.forgejo_url, cfg.forgejo_token, cfg.repo),
                          mirror=Mirror(cfg, cfg.forgejo_token), schema=False)
        if not _state["schema"]:
            _state["db"].init()
            _state["schema"] = True
    return _state


@app.exception_handler(DbDown)
async def _db_down(_req, exc):
    return JSONResponse(status_code=503, content={"detail": f"database unavailable: {exc}"})


@app.exception_handler(ForgejoDown)
async def _fj_down(_req, exc):
    return JSONResponse(status_code=503, content={"detail": f"identity provider unavailable: {exc}"})


class User:
    def __init__(self, login, role):
        self.login, self.role = login, role


def auth(authorization: str = Header(default="")) -> User:
    st = S()
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() not in ("token", "bearer"):
        raise HTTPException(401, "authentication required: send 'Authorization: token <Forgejo access token>'")
    who = st["fj"].login_of(parts[1].strip())
    if who is None:
        raise HTTPException(401, "the token was not accepted")
    role = st["fj"].role_of(*who)
    if role is None:
        raise HTTPException(403, f"{who[0]} has no access to the vault repository")
    return User(who[0], role)


def need(user: User, *roles):
    if user.role not in roles:
        raise HTTPException(403, f"{user.login} is a {user.role}; this needs {' or '.join(roles)}")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/whoami")
def whoami(user: User = Depends(auth)):
    return {"user": user.login, "role": user.role}


@app.get("/config")
def config(user: User = Depends(auth)):
    st = S()
    return {"git_url": st["cfg"].git_url, "repo": st["cfg"].repo, "user": user.login, "role": user.role}


@app.get("/locks")
def list_locks(user: User = Depends(auth)):
    return S()["db"].locks()


def _doc(name):
    V = S()["mirror"].V
    try:
        V._check_name(name)
    except V.PDMError as e:
        raise HTTPException(400, str(e))
    return name


@app.post("/locks/{name}")
async def acquire(name: str, request: Request, user: User = Depends(auth)):
    need(user, "writer", "admin")
    _doc(name)
    body = await request.json()
    payload = {k: body.get(k) for k in ("host", "workdir", "client", "new_revision", "nonce")}
    for k in ("host", "workdir", "client", "nonce"):
        if payload[k] is not None and (not isinstance(payload[k], str) or len(payload[k]) > 512):
            raise HTTPException(400, f"lock field {k} must be a string of at most 512 characters")
    if not isinstance(payload["new_revision"], (bool, type(None))):
        raise HTTPException(400, "lock field new_revision must be a boolean")
    payload["document"] = name
    payload["time"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    ok, info = S()["db"].acquire(name, uuid.uuid4().hex, user.login, payload)
    if ok:
        return info
    holder = info["holder"] if info else "someone"
    raise HTTPException(409, {"message": f"{name} is checked out by {holder}", "lock": info})


@app.delete("/locks/{name}")
def release(name: str, lock_id: str, user: User = Depends(auth)):
    need(user, "writer", "admin")
    _doc(name)
    db = S()["db"]
    cur = db.lock(name)
    if cur is None:
        return {"released": False}
    if cur["holder"] != user.login:
        raise HTTPException(403, f"the lock on {name} is held by {cur['holder']}, not {user.login}; only an admin can break it")
    if not db.release(name, lock_id, user.login):
        raise HTTPException(409, {"message": f"the lock on {name} changed hands", "lock": db.lock(name)})
    return {"released": True}


@app.post("/locks/{name}/break")
def break_lock(name: str, user: User = Depends(auth)):
    need(user, "admin")
    _doc(name)
    db = S()["db"]
    cur = db.lock(name)
    if cur is None:
        return {"broken": False}
    db.release(name, None, user.login, action="break", detail={"former_holder": cur["holder"], "former_lock": cur})
    return {"broken": True, "former": cur}


@app.get("/audit")
def audit(user: User = Depends(auth)):
    need(user, "admin")
    return S()["db"].audit()


def _refresh_index(st):
    """Bring the where-used index to the vault head (rows built by Vault's own scanner). Returns the mirror vault."""
    mirror = st["mirror"]
    V = mirror.V
    with mirror.lock:
        v, head = mirror.sync()
        if st["db"].index_head() != head:
            links, bad = [], []
            for doc in v._doc_names_at(head):
                xml = v._document_xml(head, doc)
                if xml is None:
                    continue
                try:
                    for obj, prop, f in v._scan_xlinks(xml):
                        links.append((doc, obj, prop, f))
                except V.DocumentError as e:
                    bad.append((doc, str(e), xml.decode("utf-8", "replace").lower()))
            st["db"].replace_index(head, links, bad)
        return v, head


@app.get("/where-used/{name}")
def where_used(name: str, user: User = Depends(auth)):
    st = S()
    _doc(name)
    V = st["mirror"].V
    v, _head = _refresh_index(st)
    try:
        with st["mirror"].lock:
            v._require_present(name)
    except V.DocumentError as e:
        raise HTTPException(404, str(e))
    links, bad = st["db"].all_links()
    used = []
    for r in links:
        if r["document"] == name:
            continue
        tgt = V.Vault._link_target(r["file"])
        if tgt is not None and tgt.lower() == name.lower():
            used.append({"document": r["document"], "object": r["object"], "property": r["property"], "file": r["file"]})
    unreadable = [{"document": b["document"], "reason": b["reason"], "mentions": name.lower() in b["lowered"]}
                  for b in bad]
    return {"used": used, "unreadable": unreadable}


@app.post("/internal/pre-receive")
async def pre_receive(request: Request, x_pdm_hook_secret: str = Header(default="")):
    """Called by the repository's pre-receive hook, never by clients; guarded by a shared secret."""
    st = S()
    import hmac
    if not hmac.compare_digest(x_pdm_hook_secret, st["cfg"].hook_secret):
        raise HTTPException(401, "bad hook secret")
    body = await request.json()
    pusher = body["pusher"]
    role = st["fj"].role_of(pusher, site_admin=st["fj"].is_site_admin(pusher))
    mirror = st["mirror"]
    db = st["db"]
    with mirror.lock:
        pinned, pending = db.pin()
        try:
            v, head = mirror.sync()
        except Exception:
            v, head = None, None
        reason = None
        if pinned is None:
            # Not initialised yet: only an admin's init push onto an empty repository may go through (policy).
            if v is not None:
                if pending and head == pending:
                    db.set_pin(head, None)
                    pinned = head
                else:
                    reason = ("the vault repository already has history but this service never approved it; "
                              "an admin must pin its head (see tools/pdm-server/README.md, Recovery)")
        elif v is None:
            reason = "the service cannot read the vault branch (renamed or deleted?); an admin must recover it"
        elif head == pinned or head == pending or (
                v._git(["merge-base", "--is-ancestor", pinned, head], check=False).returncode == 0):
            # Branch renames and re-creations through Forgejo's API run no hook, so the head the service last
            # approved is the anchor: the branch must still be that head or descend from it.
            if head != pinned:
                db.set_pin(head, None)
                pinned = head
        else:
            reason = (f"the vault branch ({head[:10]}) no longer descends from the last approved head "
                      f"({pinned[:10]}); an admin must recover it (see README, Recovery)")
        if reason is None:
            reason = policy.check_push(mirror.V, db, v, pusher, role, body["updates"], pinned=pinned,
                                       emails={f"{pusher}@pdm.local", st["fj"].email_of(pusher)} - {None})
        if reason is None:
            new = [u["new"] for u in body["updates"] if u["ref"] == f"refs/heads/{mirror.V.BRANCH}"]
            if new:
                db.set_pin(pinned, new[-1])
    if reason:
        st["db"].log(pusher, "push-refused", None, {"reason": reason})
        return {"allow": False, "reason": reason}
    return {"allow": True}
