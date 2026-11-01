# SPDX-License-Identifier: LGPL-2.1-or-later
"""Engineering change orders (ECO) and vault roles for the PDM vault (G03 slice 6a).

An ECO is a JSON record in the vault itself, ``plm/eco/ECO-<n>.json`` on the vault branch, so the library-only bare-repo
backend supports it and the server only mirrors it. Roles beyond the host's own permissions are ``plm/roles.json``.

One set of rules, used by both sides:

* ``apply_action`` is the whole state machine (who may do what, the two-person rule, the admin override, the
  history entry). The library calls it to build a record; the pre-receive policy (``tools/pdm-server``) calls
  ``verify_change`` which *replays* the same function on the old record and demands the pushed record equal the
  result, so a client that skips this library cannot publish anything the rules would not produce.
* ``check_context`` is the rule about the documents an ECO names (they exist, the target revision is not behind,
  no other open ECO holds them; for a release: the head is exactly the target revision, unlocked, WIP or InReview).

Releasing an ECO publishes, in one push (one atomic ref update), the ECO commit (state Released) followed by the
``transition`` commits of every affected document, each carrying ``PDM-Eco: <number>``. Either the whole chain lands
or nothing does. A document in an open ECO cannot be released by a plain ``Vault.transition``.

Roles: viewer < engineer < approver < admin. The host's ``reader`` is a viewer, ``writer`` an engineer, ``admin`` an
admin; ``plm/roles.json`` (written by an admin) can narrow a writer to viewer or widen it to approver. The library
alone has no authentication: the user name is the caller's claim, and a user without an entry is an engineer.
"""

import copy
import json
import re

from PDM import vault as V

ECO_DIR = "plm/eco/"
ROLES_PATH = "plm/roles.json"
NUM_RE = r"ECO-\d{4,8}"
STATES = ("Draft", "Submitted", "Approved", "Rejected", "Released")
OPEN = ("Draft", "Submitted", "Approved")
ROLES = ("viewer", "engineer", "approver", "admin")
RANK = {r: i for i, r in enumerate(ROLES)}
ALIAS = {"reader": "viewer", "writer": "engineer"}
# action -> (states it applies to, state after, least role)
ACTIONS = {
    "create": (None, "Draft", "engineer"),
    "edit": (("Draft",), "Draft", "engineer"),
    "submit": (("Draft",), "Submitted", "engineer"),
    "withdraw": (("Submitted", "Approved"), "Draft", "engineer"),
    "approve": (("Submitted",), "Approved", "approver"),
    "reject": (("Submitted",), "Rejected", "approver"),  # then back to Draft in the same commit
    "release": (("Approved",), "Released", "engineer"),
}
MAX_NUMBER = 99999999
SKEW_SECONDS = 300  # how far ahead of the clock that judges it (the server's, or the local machine's) a timestamp may be
MAX_PLM_BYTES = 262144  # the pre-receive hook does not forward plm/ files larger than this, so the library refuses them
_AT_RE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00")


class EcoError(V.PDMError):
    """An ECO rule refused the action."""


class AccessError(EcoError):
    """The user's role (or the two-person rule) does not allow the action."""


# ---- roles -----------------------------------------------------------------------------------------

def normalize_role(role):
    return ALIAS.get(role, role)


def effective_role(base, grants, user):
    """Role of ``user`` on a vault whose host says ``base`` (admin, writer, reader or None) with vault ``grants``."""
    if base is None:
        return None
    base = normalize_role(base)
    if base in ("admin", "viewer"):
        return base
    g = grants.get(user)
    return g if g in ("viewer", "engineer", "approver") else "engineer"


def parse_roles(data):
    """The grants dict of a ``roles.json`` body, or an EcoError when it is not ``{user: role}`` with known roles."""
    try:
        obj = json.loads(data) if isinstance(data, (bytes, str)) else data
    except ValueError:
        raise EcoError("roles.json is not valid JSON") from None
    if not isinstance(obj, dict) or not all(isinstance(u, str) and re.fullmatch(r"[A-Za-z0-9_.@-]{1,64}", u)
                                           and r in ROLES for u, r in obj.items()):
        raise EcoError("roles.json must map user names to viewer, engineer, approver or admin")
    return obj


def read_roles(v, commit):
    p = v._git(["cat-file", "blob", f"{commit}:{ROLES_PATH}"], check=False)
    if p.returncode != 0:
        return {}
    try:
        return parse_roles(p.stdout)
    except EcoError:
        return {}


# ---- the state machine -----------------------------------------------------------------------------

def _now():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def latest_at(skew=SKEW_SECONDS):
    """The latest timestamp (same fixed format as ``at``, so strings compare in time order) a change may carry now."""
    import datetime
    t = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=skew)
    return t.isoformat(timespec="seconds")


def next_number(ecos):
    """The one number a new ECO must take: one above the highest in use; if that is past the format, the lowest free."""
    top = max([int(n[4:]) for n in ecos] or [0])
    if top < MAX_NUMBER:
        return f"ECO-{top + 1:04d}"
    used = {int(n[4:]) for n in ecos}
    free = next((i for i in range(1, MAX_NUMBER + 1) if i not in used), None)
    if free is None:
        raise EcoError("every ECO number is in use")
    return f"ECO-{free:04d}"


def _docs(documents):
    if not isinstance(documents, list) or not documents:
        raise EcoError("an ECO needs at least one affected document")
    out, seen = [], set()
    for d in documents:
        if not isinstance(d, dict) or set(d) != {"name", "revision"}:
            raise EcoError("each affected document is {name, revision}")
        V._check_name(d["name"])
        V.parse_label(d["revision"])
        if d["name"].lower() in seen:
            raise EcoError(f"{d['name']} is listed twice")
        seen.add(d["name"].lower())
        out.append({"name": d["name"], "revision": d["revision"]})
    return sorted(out, key=lambda d: d["name"])


def _text(label, value, limit):
    if not isinstance(value, str) or not value.strip():
        raise EcoError(f"an ECO needs a {label}")
    if len(value) > limit:
        raise EcoError(f"the {label} is longer than {limit} characters")
    V._check_message(value)
    return value.strip()


def apply_action(rec, action, actor, role, comment, at, override=False, fields=None):
    """The ECO after ``action`` by ``actor`` (with effective ``role``); EcoError/AccessError when the rules refuse.

    ``rec`` is the record before (None for create), ``fields`` carries number/title/reason/documents for create and
    edit. ``override`` is the admin's explicit request to act outside the two-person rule; it is recorded in the
    history entry only when it was needed."""
    if action not in ACTIONS:
        raise EcoError(f"unknown ECO action {action!r}")
    if role not in RANK:
        raise AccessError(f"{actor} has no role on this vault")
    src, dst, need = ACTIONS[action]
    if not isinstance(at, str) or not _AT_RE.fullmatch(at):
        raise EcoError("bad timestamp")
    comment = (comment or "").strip()
    if len(comment) > 2000:
        raise EcoError("the comment is longer than 2000 characters")
    V._check_message(comment)
    if RANK[role] < RANK[need]:
        raise AccessError(f"{actor} is {role}; {action} needs {need} or higher")
    fields = fields or {}
    if action == "create":
        if rec is not None:
            raise EcoError("that ECO exists")
        if not re.fullmatch(NUM_RE, str(fields.get("number"))):
            raise EcoError("bad ECO number")
        new = {"number": fields["number"], "title": _text("title", fields.get("title"), 200),
               "reason": _text("reason", fields.get("reason"), 2000), "author": actor, "created": at,
               "documents": _docs(fields.get("documents")), "state": "Draft", "history": []}
        frm = None
    else:
        if rec is None:
            raise EcoError("no such ECO")
        if rec["state"] not in src:
            raise EcoError(f"{rec['number']} is {rec['state']}; {action} needs {' or '.join(src)}")
        new = copy.deepcopy(rec)
        frm = rec["state"]
        if new["history"] and at < new["history"][-1]["at"]:
            raise EcoError("timestamps must not go backwards")
    used = None
    if action in ("approve", "reject") and actor == new["author"]:
        used = "two-person rule"
    elif action in ("edit", "submit") and actor != new["author"]:
        used = "author-only rule"
    elif action == "withdraw" and actor != new["author"] and RANK[role] < RANK["approver"]:
        used = "author-only rule"
    if used:
        if not (override and role == "admin"):
            raise AccessError(f"{used}: {actor} may not {action} {new['number']} (authored by {new['author']})"
                              + ("; someone else must decide" if used == "two-person rule" else ""))
        if not comment:
            raise EcoError("an admin override needs a comment saying why")
    if action in ("reject", "withdraw") and not comment:
        raise EcoError(f"{action} needs a comment")
    if action == "edit":
        for k, cap in (("title", 200), ("reason", 2000)):
            if k in fields and fields[k] is not None:
                new[k] = _text(k, fields[k], cap)
        if fields.get("documents") is not None:
            new["documents"] = _docs(fields["documents"])
    elif action == "release":
        new["released_at"] = at
    entries = [(frm, "Rejected" if action == "reject" else dst, action)]
    if action == "reject":
        entries.append(("Rejected", "Draft", "reopen"))
        new["last_rejection"] = {"by": actor, "at": at, "comment": comment}
    new["state"] = "Draft" if action == "reject" else dst
    for f, t, a in entries:
        new["history"].append({"seq": len(new["history"]) + 1, "action": a, "from": f, "to": t, "actor": actor,
                               "role": role, "at": at, "override": bool(used) and a == action,
                               "comment": comment if a == action else f"returned to Draft after rejection"})
    return new


def verify_change(old, new, actor, role, ecos=None, latest=None):
    """Policy side: ``(None, action, entry)`` when the pushed record ``new`` is exactly what ``apply_action`` produces
    from ``old`` for the history entry ``new`` appends, else ``(reason, None, None)``. With ``ecos`` (number -> record
    at the old head) a create must take exactly ``next_number``; with ``latest`` (``latest_at()``) the entry may not be
    dated later than that."""
    if not isinstance(new, dict) or not isinstance(new.get("history"), list):
        return "the ECO record is malformed", None, None
    oh = old["history"] if old else []
    nh = new["history"]
    if nh[:len(oh)] != oh or len(nh) <= len(oh) or not isinstance(nh[len(oh)], dict):
        return "an ECO's history is append-only and the change must add an entry", None, None
    e = nh[len(oh)]
    if latest is not None and isinstance(e.get("at"), str) and e["at"] > latest:
        return f"the timestamp {e['at']} is ahead of the server clock (allowed up to {latest})", None, None
    if old is None and ecos is not None and e.get("action") == "create":
        try:
            want = next_number(ecos)
        except EcoError as x:
            return str(x), None, None
        if new.get("number") != want:
            return f"a new ECO must take the next free number {want}, not {new.get('number')!r}", None, None
    fields = {k: new.get(k) for k in ("number", "title", "reason", "documents")}
    try:
        expected = apply_action(copy.deepcopy(old), e.get("action"), actor, role, e.get("comment"), e.get("at"),
                                bool(e.get("override")), fields)
    except (V.PDMError, KeyError, TypeError) as x:
        return str(x), None, None
    if expected != new:
        return "the ECO record differs from what the ECO rules produce for that action", None, None
    return None, e["action"], e


def open_docs(ecos, exclude=None):
    """Document name -> number of the open ECO that holds it (``ecos``: number -> record)."""
    out = {}
    for n, r in ecos.items():
        if n != exclude and r["state"] in OPEN:
            for d in r["documents"]:
                out.setdefault(d["name"], n)
    return out


def check_context(action, rec, docs, locks, other_open, present=None):
    """The document rules of ``action`` on record ``rec``. ``docs``: name -> latest record (revision, state) or None;
    ``locks``: name -> lock or None; ``other_open``: ``open_docs`` of the other ECOs; ``present``: the document names
    in the vault tree (a document that was moved away still has records but is no longer there)."""
    if action not in ("create", "edit", "submit", "release"):
        return
    for d in rec["documents"]:
        n, target, cur = d["name"], d["revision"], docs.get(d["name"])
        if cur is None or (present is not None and n not in present):
            raise EcoError(f"{rec['number']}: no document {n!r} in the vault")
        if action == "release":
            if cur["revision"] != target:
                raise EcoError(f"{n} is at {cur['revision']}, not the ECO's target revision {target}")
            if cur["state"] not in ("WIP", "InReview"):
                raise V.LifecycleError(f"{n} is {cur['state']}; an ECO releases WIP or InReview documents")
            if locks.get(n):
                raise V.LockError(f"{n} is checked out by {locks[n]['holder']}; release needs it unlocked",
                                  locks[n]["holder"])
            continue
        if cur["state"] == "Obsolete":
            raise V.LifecycleError(f"{n} is Obsolete")
        if V.parse_label(target) < V.parse_label(cur["revision"]):
            raise EcoError(f"{n}: target revision {target} is behind the vault's {cur['revision']}")
        if n in other_open:
            raise EcoError(f"{n} is already part of the open {other_open[n]}")


# ---- reading ---------------------------------------------------------------------------------------

def read_all(v, commit):
    """number -> record of every ECO at ``commit``."""
    out = {}
    names = v._git(["ls-tree", "--name-only", "-r", commit, "--", ECO_DIR.rstrip("/")], check=False).stdout.decode()
    for path in names.split("\n"):
        m = re.fullmatch(re.escape(ECO_DIR) + "(" + NUM_RE + r")\.json", path)
        if m:
            rec = load(v, commit, m.group(1))
            if rec:
                out[m.group(1)] = rec
    return out


def load(v, commit, number):
    p = v._git(["cat-file", "blob", f"{commit}:{ECO_DIR}{number}.json"], check=False)
    return json.loads(p.stdout) if p.returncode == 0 else None


def _check_number(number):
    if not isinstance(number, str) or not re.fullmatch(NUM_RE, number):
        raise EcoError(f"bad ECO number {number!r}")
    return number


def get(v, number):
    _check_number(number)
    v._fetch()
    rec = load(v, v._head(), number)
    if rec is None:
        raise EcoError(f"no ECO {number}")
    return rec


def base_revision(v, name, target):
    """The revision an ECO's change to ``name`` starts from: the document's newest Released revision that is behind
    ``target``, else the first revision it ever had (when it was never released)."""
    hist = v.history(name)  # newest first, follows a move
    tgt = V.parse_label(target)
    released = [r["revision"] for r in hist if r["state"] == "Released" and V.parse_label(r["revision"]) < tgt]
    if released:
        return max(released, key=V.parse_label)
    contents = [r["revision"] for r in hist if r["event"] in V.CONTENT_EVENTS and r["revision"]]
    return contents[-1] if contents else target


def eco_diff(v, number):
    """The diff of every document an ECO changes: ``[{document, from, to, diff}]`` by document name, where ``diff`` is
    exactly ``Vault.diff(document, from, to)`` and ``from`` is ``base_revision``, ``to`` the ECO's target revision.
    Needs FreeCAD (it compares the documents), so it is computed on the client, never by the service."""
    rec = get(v, number)
    return [{"document": d["name"], "from": (b := base_revision(v, d["name"], d["revision"])), "to": d["revision"],
             "diff": v.diff(d["name"], b, d["revision"])} for d in rec["documents"]]


def list_ecos(v, state=None):
    """Summaries of every ECO, by number."""
    v._fetch()
    out = []
    for n, r in sorted(read_all(v, v._head()).items()):
        if state is None or r["state"] == state:
            out.append({"number": n, "title": r["title"], "state": r["state"], "author": r["author"],
                        "documents": r["documents"]})
    return out


def guard_move(v, name):
    """Refuse renaming a document an open ECO names (the ECO would point at a name that is gone)."""
    for n, num in sorted(open_docs(read_all(v, v._head())).items()):
        if n.lower() == name.lower():
            raise V.LifecycleError(f"{name} is part of the open {num}; edit the ECO to drop the document, or release it, before renaming it")


def guard_direct_release(v, name):
    """Refuse a plain transition to Released of a document that an open ECO holds (it moves only through the ECO)."""
    for n, num in sorted(open_docs(read_all(v, v._head())).items()):
        if n == name:
            raise V.LifecycleError(f"{name} is part of the open {num}; release the ECO instead of the document")


# ---- writing ---------------------------------------------------------------------------------------

def _put(v, parent, path, data, subject, event, trailers):
    """A commit on ``parent`` that sets blob ``path`` to ``data``, with PDM trailers (no PDM-Document)."""
    import os
    import uuid
    idx = os.path.join(v.workdir, ".git", f"pdm-index-{uuid.uuid4().hex}")
    env = {"GIT_INDEX_FILE": idx}
    try:
        v._git(["read-tree", parent], env=env)
        sha = v._git(["hash-object", "-w", "--stdin"], input=data).stdout.decode().strip()
        v._git(["update-index", "--add", "--cacheinfo", f"100644,{sha},{path}"], env=env)
        tree = v._git(["write-tree"], env=env).stdout.decode().strip()
    finally:
        if os.path.exists(idx):
            os.remove(idx)
    V._check_message(subject)
    msg = f"{subject}\n\n{V._TRAILER}Event: {event}\n" + "".join(f"{V._TRAILER}{k}: {val}\n" for k, val in trailers)
    return v._git(["commit-tree", tree, "-p", parent, "-m", msg]).stdout.decode().strip()


def _dump(rec):
    return (json.dumps(rec, indent=2, sort_keys=True) + "\n").encode()


def _docs_ctx(v, rec, ecos):
    docs, locks = {}, {}
    for d in rec["documents"]:
        recs = v._records(d["name"])
        docs[d["name"]] = recs[0] if recs else None
        locks[d["name"]] = v.lock_info(d["name"], refresh=False)
    return docs, locks, open_docs(ecos, exclude=rec["number"])


def write(v, number, action, comment=None, override=False, fields=None):
    """Perform ``action`` on ECO ``number`` (None to create) as ``v.user``; returns the new record."""
    V._check_message(comment or "")
    if number is not None:
        _check_number(number)
    lost_race = 0
    for _ in range(8):
        v._fetch()
        head = v._head()
        role = v._role()
        ecos = read_all(v, head)
        fields = dict(fields or {})
        if action == "create":
            number = next_number(ecos)
            fields["number"] = number
        old = ecos.get(number)
        if number is not None and action != "create" and old is None:
            raise EcoError(f"no ECO {number}")
        at = _now()
        if old is not None and old["history"]:
            last = old["history"][-1]["at"]
            if not getattr(v, "remote", False) and last > latest_at():
                raise EcoError(f"{number}'s last history entry is dated {last}, ahead of this machine's clock; "
                               "its timestamps would have to go backwards")
            at = max(at, last)  # a clock slightly behind the last entry's author must not jam the ECO
        new = apply_action(old, action, v.user, role, comment, at, override, fields)
        if action in ("create", "edit", "submit", "release"):
            check_context(action, new, *_docs_ctx(v, new, ecos), present=set(v._doc_names_at(head)))
        if len(_dump(new)) > MAX_PLM_BYTES:
            raise EcoError(f"{number} would be over {MAX_PLM_BYTES} bytes (the history or document list is too "
                           "long), more than the server checks")
        tip = _put(v, head, f"{ECO_DIR}{number}.json", _dump(new), f"{number}: {action}", "eco",
                   [("Eco", number), ("Eco-Action", action)])
        if action == "release":
            for i, d in enumerate(new["documents"]):
                cur = v._records(d["name"])[0]
                state = cur["state"]
                for step in (("InReview", "Released") if state == "WIP" else ("Released",)):
                    if i:
                        V._fault("eco_between")  # fault injection point: some documents' commits built, not pushed
                    _t, tip = v._commit(tip, d["name"], f"{number}: {state} -> {step}", cur["revision"], step,
                                        "transition", cur["released"] or step == "Released", None,
                                        extra=[("Eco", number)])
                    state = step
        V._fault("after_commit")
        try:
            pushed = v._push_main(tip)
        except V.PDMError as e:
            # a concurrent create took the number first and the server judged this push against the newer head:
            # retry once on the fresh head (a new number), then say so rather than report a permission failure
            if action != "create" or type(e).__name__ != "PermissionDenied" or "next free number" not in str(e):
                raise
            lost_race += 1
            if lost_race > 1:
                raise EcoError("lost the race for the ECO number twice; someone else is creating ECOs, retry") from e
            continue
        if pushed:
            return new
    raise V.GitError(f"vault kept moving; ECO {action} gave up after 8 attempts (nothing changed)")


def create(v, title, reason, documents, comment=None):
    """New Draft ECO naming ``documents`` ([{name, revision}] or {name: revision}). Returns the record."""
    if isinstance(documents, dict):
        documents = [{"name": n, "revision": r} for n, r in documents.items()]
    return write(v, None, "create", comment, fields={"title": title, "reason": reason, "documents": documents})


def edit(v, number, title=None, reason=None, documents=None, comment=None, override=False):
    if isinstance(documents, dict):
        documents = [{"name": n, "revision": r} for n, r in documents.items()]
    return write(v, number, "edit", comment, override,
                 {"title": title, "reason": reason, "documents": documents})


def local_role(v):
    """Role of ``v.user`` in a library-only vault: its ``roles.json`` entry, else engineer."""
    return read_roles(v, v._head()).get(v.user, "engineer")


def set_role(v, user, role, comment=None):
    """Admin: grant ``user`` a role (viewer, engineer, approver, admin; None removes the entry) in ``plm/roles.json``."""
    parse_roles({user: role or "engineer"})  # a valid user name and role
    if role is not None and role not in ROLES:
        raise EcoError(f"unknown role {role!r}")
    for _ in range(8):
        v._fetch()
        head = v._head()
        grants = read_roles(v, head)
        existed = v._git(["cat-file", "-e", f"{head}:{ROLES_PATH}"], check=False).returncode == 0
        me = v._role()
        if me != "admin" and existed:
            raise AccessError(f"{v.user} is {me}; changing roles needs admin")
        if grants.get(user) == role or (role is None and user not in grants):
            return grants  # no change: nothing to push
        new = dict(grants)
        if role is None:
            new.pop(user, None)
        else:
            new[user] = role
        parse_roles(new)
        if len(_dump(new)) > MAX_PLM_BYTES:
            raise EcoError(f"roles.json would be over {MAX_PLM_BYTES} bytes, more than the server checks")
        tip = _put(v, head, ROLES_PATH, _dump(new), f"roles: {user} -> {role or 'default'}", "roles",
                   [("Roles-User", user), ("Roles-Role", role or "")])
        if v._push_main(tip):
            return new
    raise V.GitError("vault kept moving; set_role gave up after 8 attempts")
