# SPDX-License-Identifier: LGPL-2.1-or-later
"""Local PDM vault: a bare git repository plus per-client working copies.

No server and no GUI. Every operation shells out to the ``git`` binary. See README.md for the
vault layout, revision scheme, lock semantics and lifecycle table.

Concurrency model:

* Document history lives on ``refs/heads/main`` of the bare vault; a check-in is a commit built
  with plumbing (temporary index) and pushed without force, so a vault that moved is detected.
* A lock is a ref ``refs/locks/<document>`` in the vault pointing at a small commit whose message
  is JSON (holder, host, time, new-revision flag). Creating the ref by ``git push`` is a
  create-if-absent compare-and-swap done by git's ref store: of two racing clients exactly one
  succeeds. Releasing deletes the ref with ``--force-with-lease`` against the exact value the
  releaser read, so a lock that changed hands is never deleted by mistake.
"""

import contextlib
import datetime
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import uuid

STATES = ("WIP", "InReview", "Released", "Obsolete")
TRANSITIONS = {
    "WIP": ("InReview", "Obsolete"),
    "InReview": ("WIP", "Released", "Obsolete"),
    "Released": ("Obsolete",),
    "Obsolete": (),
}
BRANCH = "main"
LOCK_NS = "refs/locks/"
LOCK_MIRROR = "refs/pdm-locks/"
DOC_SUFFIX = ".FCStd.d"
FAULT_ENV = "PDM_TEST_FAULT"  # read only here; values: "before_push", "after_commit"
_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$")  # no leading "-": never an option to git
_TRAILER = "PDM-"
_PERM_RE = re.compile(r"permission denied|insufficient permission|read-only|not permitted|unable to (?:create|write|append)",
                      re.I)


class PDMError(Exception):
    """Base class of every vault error."""


class GitError(PDMError):
    pass


class LockError(PDMError):
    """A lock is held by someone else (``holder``) or is not held by the caller."""

    def __init__(self, message, holder=None):
        super().__init__(message)
        self.holder = holder


class StaleRevisionError(PDMError):
    pass


class LifecycleError(PDMError):
    pass


class DocumentError(PDMError):
    pass


def revision_label(letter_index, number):
    """Letter index 0 is A, 25 is Z, 26 is AA ... The label is ``<letters>.<number>``."""
    letters = ""
    n = letter_index
    while True:
        letters = chr(ord("A") + n % 26) + letters
        n = n // 26 - 1
        if n < 0:
            return f"{letters}.{number}"


def parse_label(label):
    m = re.fullmatch(r"([A-Z]+)\.(\d+)", label or "")
    if not m:
        raise DocumentError(f"bad revision label {label!r}")
    idx = 0
    for ch in m.group(1):
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1, int(m.group(2))


def _check_name(name):
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise DocumentError(f"document name {name!r} must match {_NAME_RE.pattern}")
    return name


def _git(args, cwd=None, env=None, input=None, check=True):
    """Run git. The single choke point for subprocess use (tests may monkeypatch it)."""
    full_env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", LC_ALL="C")
    full_env.update(env or {})
    p = subprocess.run(["git"] + list(args), cwd=cwd, env=full_env, input=input, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
    if check and p.returncode != 0:
        raise GitError(f"git {' '.join(args[:3])} failed ({p.returncode}): {p.stderr.decode(errors='replace').strip()}")
    return p


def _check_message(text):
    """Refuse a user message with a line starting ``PDM-``: those lines are the vault's own trailers."""
    for line in str(text).splitlines():
        if line.strip().startswith(_TRAILER):
            raise DocumentError(f"message lines must not start with {_TRAILER!r} (reserved for vault trailers)")
    return text


def _warn(text):
    try:
        import FreeCAD
        FreeCAD.Console.PrintWarning(f"PDM: {text}\n")
    except Exception:
        pass


def _fault(point):
    if os.environ.get(FAULT_ENV) == point:
        raise GitError(f"injected fault at {point}")


class Vault:
    """A client's handle on a vault: ``Vault.create`` makes the bare repo, ``Vault.clone`` a working copy."""

    def __init__(self, workdir, user, vault_path=None):
        self.workdir = os.path.abspath(workdir)
        self.user = user
        if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,64}", user or ""):
            raise PDMError(f"bad user name {user!r}")
        self._env = {
            "GIT_AUTHOR_NAME": user, "GIT_AUTHOR_EMAIL": f"{user}@pdm.local",
            "GIT_COMMITTER_NAME": user, "GIT_COMMITTER_EMAIL": f"{user}@pdm.local",
        }
        self.vault_path = vault_path or self._git(["config", "--get", "remote.origin.url"]).stdout.decode().strip()

    # ---- construction -------------------------------------------------------------------------

    @staticmethod
    def create(vault_path):
        """Create an empty bare vault at ``vault_path`` (must not exist) and return its path."""
        vault_path = os.path.abspath(vault_path)
        if os.path.exists(vault_path) and os.listdir(vault_path):
            raise PDMError(f"{vault_path} exists and is not empty")
        os.makedirs(vault_path, exist_ok=True)
        _git(["init", "--bare", "-q", f"--initial-branch={BRANCH}", vault_path])
        for k, v in (("receive.denyNonFastForwards", "true"), ("receive.denyDeletes", "false"),
                     ("gc.auto", "0")):
            _git(["config", k, v], cwd=vault_path)
        env = {"GIT_AUTHOR_NAME": "pdm", "GIT_AUTHOR_EMAIL": "pdm@pdm.local",
               "GIT_COMMITTER_NAME": "pdm", "GIT_COMMITTER_EMAIL": "pdm@pdm.local"}
        tree = _git(["mktree"], cwd=vault_path, input=b"").stdout.decode().strip()
        msg = "Initialise vault\n\nPDM-Event: init\n"
        commit = _git(["commit-tree", tree, "-m", msg], cwd=vault_path, env=env).stdout.decode().strip()
        _git(["update-ref", f"refs/heads/{BRANCH}", commit], cwd=vault_path)
        return vault_path

    @classmethod
    def clone(cls, vault_path, workdir, user):
        """Clone the vault into a new working copy for ``user``."""
        vault_path = os.path.abspath(vault_path)
        workdir = os.path.abspath(workdir)
        _git(["clone", "-q", "--no-checkout", vault_path, workdir])
        os.makedirs(os.path.join(workdir, ".git", "pdm", "base"), exist_ok=True)
        return cls(workdir, user, vault_path)

    @classmethod
    def open(cls, workdir, user):
        return cls(workdir, user)

    # ---- plumbing helpers ---------------------------------------------------------------------

    def _git(self, args, **kw):
        env = dict(self._env)
        env.update(kw.pop("env", {}) or {})
        return _git(args, cwd=self.workdir, env=env, **kw)

    def _fetch(self):
        self._git(["fetch", "-q", "--no-tags", "origin", f"+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}",
                   f"+{LOCK_NS}*:{LOCK_MIRROR}*", "--prune"])

    def client_id(self):
        """Random id of this working copy (``.git/pdm/client-id``); with the user it identifies a lock owner."""
        path = os.path.join(self.workdir, ".git", "pdm", "client-id")
        with contextlib.suppress(FileNotFoundError):
            with open(path) as f:
                cid = f.read().strip()
            if cid:
                return cid
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cid = uuid.uuid4().hex
        with open(path, "w") as f:
            f.write(cid + "\n")
        return cid

    def _mine(self, info):
        return info is not None and info["holder"] == self.user and info.get("client") == self.client_id()

    def _held_by_other_msg(self, name, cur):
        if cur["holder"] == self.user:
            return (f"{name} is checked out by {cur['holder']} (you) in another working copy: "
                    f"{cur['host']}:{cur.get('workdir', '?')}")
        return f"{name} is checked out by {cur['holder']} on {cur['host']} since {cur['time']}"

    def doc_path(self, name):
        """Path of the document's folder inside this working copy."""
        return os.path.join(self.workdir, _check_name(name) + DOC_SUFFIX)

    def _base_file(self, name):
        return os.path.join(self.workdir, ".git", "pdm", "base", name)

    def _base(self, name):
        with contextlib.suppress(FileNotFoundError):
            with open(self._base_file(name)) as f:
                return f.read().strip()
        return None

    def _set_base(self, name, sha):
        os.makedirs(os.path.dirname(self._base_file(name)), exist_ok=True)
        with open(self._base_file(name), "w") as f:
            f.write(sha + "\n")

    # ---- revision records ---------------------------------------------------------------------

    def _records(self, name, ref=None):
        """All commits of one document, newest first, as dicts."""
        ref = ref or f"refs/remotes/origin/{BRANCH}"
        out = self._git(["log", "--format=%H%x1f%an%x1f%aI%x1f%B%x1e", "-E", f"--grep=^{_TRAILER}Document: {name}$", ref],
                        check=False)
        recs = []
        for chunk in out.stdout.decode().split("\x1e"):
            chunk = chunk.strip("\n")
            if not chunk:
                continue
            sha, author, when, body = chunk.split("\x1f", 3)
            lines = body.rstrip("\n").split("\n")
            # Trailers only from the last paragraph (the block _commit writes); a PDM- line elsewhere in a
            # message, or a message naming another document, never counts.
            i = len(lines)
            while i > 0 and lines[i - 1].strip() != "":
                i -= 1
            if i == 0:
                continue
            meta = {}
            for ln in lines[i:]:
                if ln.startswith(_TRAILER):
                    k, _, v = ln[len(_TRAILER):].partition(": ")
                    meta[k] = v
            if meta.get("Document") != name:
                continue
            message = "\n".join(lines[:i]).strip()
            recs.append({"sha": sha, "author": author, "time": when, "message": message,
                         "revision": meta.get("Revision"), "state": meta.get("State"),
                         "event": meta.get("Event"), "document": meta.get("Document"),
                         "released": meta.get("Released") == "1"})
        return recs

    def _head(self):
        return self._git(["rev-parse", "--verify", f"refs/remotes/origin/{BRANCH}"]).stdout.decode().strip()

    def _commit(self, parent, name, subject, revision, state, event, released, doc_dir):
        """Build a commit on top of ``parent`` with the working copy's folder for ``name`` (plumbing, temp index)."""
        idx = os.path.join(self.workdir, ".git", f"pdm-index-{uuid.uuid4().hex}")
        env = {"GIT_INDEX_FILE": idx}
        try:
            self._git(["read-tree", parent], env=env)
            if doc_dir is not None:
                self._git(["add", "-A", "-f", "--", os.path.basename(doc_dir)], env=env)
            tree = self._git(["write-tree"], env=env).stdout.decode().strip()
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.remove(idx)
        _check_message(subject)
        msg = (f"{subject}\n\n{_TRAILER}Document: {name}\n{_TRAILER}Revision: {revision}\n{_TRAILER}State: {state}\n"
               f"{_TRAILER}Event: {event}\n{_TRAILER}Released: {1 if released else 0}\n")
        return tree, self._git(["commit-tree", tree, "-p", parent, "-m", msg]).stdout.decode().strip()

    def _push_main(self, sha):
        _fault("before_push")
        p = self._git(["push", "-q", "origin", f"{sha}:refs/heads/{BRANCH}"], check=False)
        if p.returncode != 0:
            err = p.stderr.decode(errors="replace").strip()
            if _PERM_RE.search(err):
                raise GitError(f"the vault refused the push (permissions; is it read-only?): {err}")
            return False
        return True

    # ---- locks --------------------------------------------------------------------------------

    def lock_info(self, name, refresh=True):
        """The lock on ``name`` as a dict (``holder``, ``host``, ``time``, ``new_revision``, ``sha``) or None."""
        _check_name(name)
        if refresh:
            self._fetch()
        p = self._git(["rev-parse", "--verify", "-q", f"{LOCK_MIRROR}{name}"], check=False)
        if p.returncode != 0:
            return None
        sha = p.stdout.decode().strip()
        body = self._git(["log", "-1", "--format=%B", sha]).stdout.decode()
        info = json.loads(body.strip().split("\n")[-1])
        info["sha"] = sha
        return info

    def _lock_names(self):
        out = self._git(["for-each-ref", "--format=%(refname)", LOCK_MIRROR]).stdout.decode().split()
        return [r[len(LOCK_MIRROR):] for r in out]

    def locks(self):
        self._fetch()
        return {n: self.lock_info(n, refresh=False) for n in self._lock_names()}

    def lock(self, name, new_revision=False):
        """Acquire the lock on ``name`` (atomic create-if-absent in the vault). Idempotent for the holder.

        Raises LockError naming the holder when someone else has it. Does not touch working files.
        """
        _check_name(name)
        self._fetch()
        for other in self._lock_names():
            if other != name and other.lower() == name.lower():
                raise DocumentError(f"{name!r} differs only by case from the locked name {other!r}")
        cur = self.lock_info(name)
        if cur is not None:
            if self._mine(cur):
                return cur
            raise LockError(self._held_by_other_msg(name, cur), cur["holder"])
        payload = json.dumps({"document": name, "holder": self.user, "host": socket.gethostname(),
                              "workdir": self.workdir, "client": self.client_id(),
                              "time": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                              "new_revision": bool(new_revision), "nonce": uuid.uuid4().hex}, sort_keys=True)
        tree = self._git(["mktree"], input=b"").stdout.decode().strip()
        sha = self._git(["commit-tree", tree, "-m", f"lock {name}\n\n{payload}"]).stdout.decode().strip()
        # Without --force git refuses to update an existing ref to an unrelated commit, and creation is
        # decided by the vault's own ref transaction: one of two racing pushes fails.
        p = self._git(["push", "-q", "origin", f"{sha}:{LOCK_NS}{name}"], check=False)
        if p.returncode != 0:
            cur = self.lock_info(name)
            if cur is None:
                raise GitError(f"lock push for {name} failed: {p.stderr.decode(errors='replace').strip()}")
            raise LockError(self._held_by_other_msg(name, cur), cur["holder"])
        return self.lock_info(name)

    def release_lock(self, name):
        """Release this client's lock on ``name`` (the working files stay as they are)."""
        cur = self.lock_info(name)
        if cur is None:
            return False
        if not self._mine(cur):
            raise LockError(self._held_by_other_msg(name, cur), cur["holder"])
        self._delete_lock(name, cur["sha"])
        return True

    def break_lock(self, name):
        """Administrative: remove whoever's lock is on ``name``. Returns the former lock info."""
        cur = self.lock_info(name)
        if cur is not None:
            self._delete_lock(name, cur["sha"])
        return cur

    def _delete_lock(self, name, sha):
        self._git(["push", "-q", f"--force-with-lease={LOCK_NS}{name}:{sha}", "origin", f":{LOCK_NS}{name}"])

    def _require_lock(self, name):
        cur = self.lock_info(name)
        if cur is None:
            raise LockError(f"{name} is not checked out; check it out first")
        if not self._mine(cur):
            raise LockError(self._held_by_other_msg(name, cur), cur["holder"])
        return cur

    # ---- documents ----------------------------------------------------------------------------

    def documents(self):
        """Name -> latest record (revision, state, ...) of every document in the vault."""
        self._fetch()
        names = self._git(["ls-tree", "--name-only", f"refs/remotes/origin/{BRANCH}"]).stdout.decode().split("\n")
        out = {}
        for n in names:
            if n.endswith(DOC_SUFFIX):
                nm = n[:-len(DOC_SUFFIX)]
                recs = self._records(nm)
                if recs:
                    out[nm] = recs[0]
        return out

    def history(self, name):
        """Revision and lifecycle history of ``name``, newest first."""
        _check_name(name)
        self._fetch()
        recs = self._records(name)
        if not recs:
            raise DocumentError(f"no document {name!r} in the vault")
        return recs

    def status(self, name):
        """Latest record plus the lock and whether this client's base is current."""
        recs = self.history(name)
        return {"revision": recs[0]["revision"], "state": recs[0]["state"], "sha": recs[0]["sha"],
                "lock": self.lock_info(name, refresh=False), "base": self._base(name),
                "stale": self._base(name) not in (None, recs[0]["sha"])}

    def add_document(self, name, source=None, message="Initial revision"):
        """Add a new document (revision A.1, WIP). ``source`` is a folder-format directory to copy in;
        without it the folder must already exist at ``doc_path(name)``."""
        _check_name(name)
        _check_message(message)
        dest = self.doc_path(name)
        self._fetch()
        for n in self._git(["ls-tree", "--name-only", f"refs/remotes/origin/{BRANCH}"]).stdout.decode().split("\n"):
            if n.endswith(DOC_SUFFIX) and n[:-len(DOC_SUFFIX)] != name and n[:-len(DOC_SUFFIX)].lower() == name.lower():
                raise DocumentError(f"{name!r} differs only by case from existing document {n[:-len(DOC_SUFFIX)]!r}")
        if source is not None:
            if os.path.exists(dest):
                raise DocumentError(f"{dest} already exists in the working copy")
            shutil.copytree(source, dest)
        if not os.path.isdir(dest) or not os.path.isfile(os.path.join(dest, "Document.xml")):
            raise DocumentError(f"{dest} is not a folder-format document (no Document.xml)")
        for _ in range(8):
            self._fetch()
            if self._records(name):
                raise DocumentError(f"document {name!r} already exists in the vault")
            parent = self._head()
            _tree, sha = self._commit(parent, name, message, revision_label(0, 1), "WIP", "add", False, dest)
            _fault("after_commit")
            if self._push_main(sha):
                self._set_base(name, sha)
                return {"revision": revision_label(0, 1), "sha": sha}
        raise GitError("vault kept moving; add_document gave up after 8 attempts")

    def checkout(self, name, new_revision=False, discard_local=False):
        """Lock ``name`` and bring its current revision into the working copy. Returns the folder path.

        A Released document needs ``new_revision=True`` (the next check-in becomes the next letter, X.1);
        an InReview or Obsolete document cannot be checked out. A holder whose local edits sit on a stale base
        must pass ``discard_local=True`` to replace them with the current revision.
        """
        _check_name(name)
        self._fetch()
        recs = self._records(name)
        if not recs:
            raise DocumentError(f"no document {name!r} in the vault")
        self._check_checkout_allowed(name, recs[0], new_revision)
        mine = self.lock_info(name, refresh=False)
        already = self._mine(mine)
        info = self.lock(name, new_revision=new_revision)
        try:
            if already and self._base(name) == recs[0]["sha"] and os.path.isdir(self.doc_path(name)):
                return self.doc_path(name)  # idempotent re-checkout of my own up-to-date copy
            # Re-read after the lock: a check-in may have landed between the first fetch and the lock.
            self._fetch()
            recs = self._records(name)
            self._check_checkout_allowed(name, recs[0], new_revision or bool(info.get("new_revision")))
            dest = self.doc_path(name)
            if not discard_local and os.path.exists(dest) and (self._dirty(name) or self._base(name) is None):
                raise DocumentError(f"{name} has local edits (or an untracked folder) that checkout would overwrite; "
                                    "check in, or pass discard_local=True to discard them")
            shutil.rmtree(dest, ignore_errors=True)
            self._git(["checkout", "-q", recs[0]["sha"], "--", os.path.basename(dest)])
            self._set_base(name, recs[0]["sha"])
        except BaseException:
            if not already:
                with contextlib.suppress(PDMError):
                    self.release_lock(name)
            raise
        return self.doc_path(name)

    @staticmethod
    def _check_checkout_allowed(name, rec, new_revision):
        st = rec["state"]
        if st == "Obsolete":
            raise LifecycleError(f"{name} is Obsolete and cannot be checked out")
        if st == "InReview":
            raise LifecycleError(f"{name} is InReview; move it back to WIP before checking it out")
        if st == "Released" and not new_revision:
            raise LifecycleError(f"{name} is Released ({rec['revision']}); check out with new_revision=True to start the next revision")

    def _dirty(self, name):
        dest = self.doc_path(name)
        base = self._base(name)
        if not os.path.isdir(dest) or base is None:
            return False
        idx = os.path.join(self.workdir, ".git", f"pdm-index-{uuid.uuid4().hex}")
        env = {"GIT_INDEX_FILE": idx}
        try:
            self._git(["read-tree", base], env=env)
            self._git(["add", "-A", "-f", "--", os.path.basename(dest)], env=env)
            return self._git(["diff-index", "--cached", "--quiet", base, "--", os.path.basename(dest)],
                             env=env, check=False).returncode != 0
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.remove(idx)

    def has_changes(self, name):
        return self._dirty(name)

    def checkin(self, name, message, keep_lock=False):
        """Check in the working copy's folder for ``name`` as the next revision.

        Refused when the lock is not held by this client, when the client's base revision is behind the
        vault (StaleRevisionError), or when nothing changed. A failure at any point leaves the vault at
        the previous revision and the lock held.
        """
        _check_name(name)
        _check_message(message)
        lock = self._require_lock(name)
        dest = self.doc_path(name)
        if not os.path.isfile(os.path.join(dest, "Document.xml")):
            raise DocumentError(f"{dest} is not a folder-format document")
        if not self._dirty(name):
            raise DocumentError(f"{name} has no changes to check in")
        for _ in range(8):
            self._fetch()
            recs = self._records(name)
            if not recs:
                raise DocumentError(f"no document {name!r} in the vault")
            latest = recs[0]
            if self._base(name) != latest["sha"]:
                raise StaleRevisionError(
                    f"{name}: working copy is based on {self._base_label(name, recs)}, the vault head for it is "
                    f"{latest['revision']} {latest['state']} (commit {latest['sha'][:10]})")
            letter, number = parse_label(latest["revision"])
            if latest["released"]:
                letter, number = letter + 1, 1  # the next change after a release starts the next letter
            else:
                number += 1
            label = revision_label(letter, number)
            _tree, sha = self._commit(self._head(), name, message, label, "WIP", "checkin", False, dest)
            _fault("after_commit")
            if self._push_main(sha):
                self._set_base(name, sha)
                result = {"revision": label, "sha": sha}
                if not keep_lock:
                    try:
                        self.release_lock(name)
                    except Exception as e:  # the revision has landed; report, never raise
                        result["warning"] = f"revision {label} landed but releasing the lock on {name} failed: {e}"
                        _warn(result["warning"])
                return result
            # Push refused: the vault moved (maybe another document). Fetch and re-check staleness.
        raise GitError("vault kept moving; checkin gave up after 8 attempts (lock still held)")

    def _base_label(self, name, recs):
        b = self._base(name)
        for r in recs:
            if r["sha"] == b:
                return f"{r['revision']} {r['state']} (commit {r['sha'][:10]})"
        return "an unknown revision"

    def transition(self, name, new_state, message=None):
        """Move ``name`` to ``new_state`` per TRANSITIONS. Refused while another client holds its lock."""
        _check_name(name)
        if new_state not in STATES:
            raise LifecycleError(f"unknown state {new_state!r}")
        _check_message(message or "")
        for _ in range(8):
            self._fetch()
            recs = self._records(name)
            if not recs:
                raise DocumentError(f"no document {name!r} in the vault")
            cur = recs[0]
            if new_state not in TRANSITIONS[cur["state"]]:
                raise LifecycleError(f"illegal transition {cur['state']} -> {new_state} for {name}; allowed: "
                                     f"{', '.join(TRANSITIONS[cur['state']]) or 'none'}")
            lk = self.lock_info(name, refresh=False)
            if lk is not None:
                raise LockError(f"{name} is checked out by {lk['holder']}; transitions need the document unlocked", lk["holder"])
            released = cur["released"] or new_state == "Released"
            subject = message or f"{cur['state']} -> {new_state}"
            _tree, sha = self._commit(self._head(), name, subject, cur["revision"], new_state, "transition", released, None)
            _fault("after_commit")
            if self._push_main(sha):
                return {"revision": cur["revision"], "state": new_state, "sha": sha}
        raise GitError("vault kept moving; transition gave up after 8 attempts")

    # ---- reading old revisions ----------------------------------------------------------------

    def find_revision(self, name, revision):
        """The newest record of ``name`` with label ``revision`` that holds content (a check-in or add)."""
        for r in self.history(name):
            if r["revision"] == revision and r["event"] in ("add", "checkin"):
                return r
        raise DocumentError(f"{name} has no revision {revision}")

    def export_revision(self, name, revision, dest=None):
        """Write the folder of one revision to ``dest`` (a new temp dir by default). Returns the folder path,
        made read-only on disk."""
        rec = self.find_revision(name, revision)
        base = dest or tempfile.mkdtemp(prefix="pdm-rev-")
        os.makedirs(base, exist_ok=True)
        arch = _git(["archive", "--format=tar", rec["sha"], "--", name + DOC_SUFFIX], cwd=self.workdir)
        subprocess.run(["tar", "-x", "-C", base], input=arch.stdout, check=True)
        path = os.path.join(base, name + DOC_SUFFIX)
        subprocess.run(["chmod", "-R", "a-w", path], check=True)
        return path

    def open_revision(self, name, revision, dest=None):
        """Export an earlier revision to a temp dir and open it with FreeCAD. The files are read-only
        on disk; the returned document is a view of that revision, not of the working copy."""
        import FreeCAD
        return FreeCAD.openDocument(self.export_revision(name, revision, dest))


def cleanup_export(path):
    """Remove a folder made by ``export_revision`` (restoring write permission first)."""
    parent = os.path.dirname(path)
    subprocess.run(["chmod", "-R", "u+w", path], check=False)
    shutil.rmtree(parent, ignore_errors=True)
