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
import posixpath
import re
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from xml.sax import saxutils

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
CONTENT_EVENTS = ("add", "checkin", "move", "relink")  # commits whose tree is a full revision of the document
_XLINK_RE = re.compile(r'(<XLink\s+file=")([^"]*)(")')
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


class UsedList(list):
    """Result of ``Vault.where_used``; ``unreadable`` lists the documents that could not be scanned."""
    unreadable = ()


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
    """Refuse a user message with a line starting ``PDM-`` (the vault's own trailers) or with control
    characters other than newline and tab (they could pose as record separators in ``git log`` output)."""
    if re.search(r"[\x00-\x08\x0b-\x1f\x7f\x85\u2028\u2029]", str(text)):
        raise DocumentError("message must not contain control characters")
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
        # -z ends each commit with NUL, which a commit message cannot contain; sha, author and date are
        # one line each (user names are restricted), and the body is everything after them.
        out = self._git(["log", "-z", "--format=%H%n%an%n%aI%n%B", "-E", f"--grep=^{_TRAILER}Document: {name}$", ref],
                        check=False)
        recs = []
        for chunk in out.stdout.decode().split("\x00"):
            chunk = chunk.strip("\n")
            if not chunk:
                continue
            parts = chunk.split("\n", 3)
            if len(parts) < 4 or not re.fullmatch(r"[0-9a-f]{40}", parts[0]):
                continue
            sha, author, when, body = parts
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
                         "released": meta.get("Released") == "1", "moved_from": meta.get("Moved-From")})
        return recs

    def _head(self):
        return self._git(["rev-parse", "--verify", f"refs/remotes/origin/{BRANCH}"]).stdout.decode().strip()

    @staticmethod
    def _trailer_meta(chunk):
        lines = chunk.strip("\n").split("\n")
        i = len(lines)
        while i > 0 and lines[i - 1].strip() != "":
            i -= 1
        meta = {}
        for ln in lines[i:]:
            if ln.startswith(_TRAILER):
                k, _, v = ln[len(_TRAILER):].partition(": ")
                meta[k] = v
        return meta

    def _moved_to(self, name, head, present):
        """Where a document that is no longer in the head tree went (following a chain A -> B -> C), or None."""
        cur = name
        seen = {name}
        while True:
            out = self._git(["log", "-z", "--format=%B", "-E", f"--grep=^{_TRAILER}Moved-From: {cur}$", head],
                            check=False).stdout.decode()
            nxt = None
            for chunk in out.split("\x00"):
                meta = self._trailer_meta(chunk)
                if meta.get("Event") == "move" and meta.get("Moved-From") == cur and meta.get("Document"):
                    nxt = meta["Document"]
                    break
            if not nxt or nxt in seen:
                return cur if cur != name else None
            if nxt in present:
                return nxt
            seen.add(nxt)
            cur = nxt

    def _require_present(self, name):
        """Refuse a name that is not in the head tree (moved away, or never added). Callers have just fetched."""
        head = self._head()
        present = self._doc_names_at(head)
        if name in present:
            return
        to = self._moved_to(name, head, present)
        if to:
            raise DocumentError(f"{name!r} was moved to {to!r} and no longer exists in the vault; use {to!r}")
        raise DocumentError(f"no document {name!r} in the vault")

    def _commit(self, parent, name, subject, revision, state, event, released, doc_dir, edit=None, extra=()):
        """Build a commit on top of ``parent`` with the working copy's folder for ``name`` (plumbing, temp index).

        ``edit(env)``, if given, edits the temporary index instead (used by move/relink, which change the
        tree without a working copy); ``extra`` is a list of additional ``(key, value)`` trailers."""
        idx = os.path.join(self.workdir, ".git", f"pdm-index-{uuid.uuid4().hex}")
        env = {"GIT_INDEX_FILE": idx}
        try:
            self._git(["read-tree", parent], env=env)
            if doc_dir is not None:
                self._git(["add", "-A", "-f", "--", os.path.basename(doc_dir)], env=env)
            if edit is not None:
                edit(env)
            tree = self._git(["write-tree"], env=env).stdout.decode().strip()
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.remove(idx)
        _check_message(subject)
        msg = (f"{subject}\n\n{_TRAILER}Document: {name}\n{_TRAILER}Revision: {revision}\n{_TRAILER}State: {state}\n"
               f"{_TRAILER}Event: {event}\n{_TRAILER}Released: {1 if released else 0}\n")
        for k, v in extra:
            msg += f"{_TRAILER}{k}: {v}\n"
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
        self._require_present(name)
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
        # A moved document keeps its history: follow the Moved-From trailer to the commits before the move.
        out = list(recs)
        seen = {name}
        cur = recs
        while True:
            mv = [r for r in cur if r["moved_from"] and r["event"] == "move"]
            if not mv or mv[-1]["moved_from"] in seen:
                break
            old = mv[-1]["moved_from"]
            seen.add(old)
            cur = self._records(old, ref=mv[-1]["sha"] + "^")
            out.extend(cur)
        return out

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
        self._require_present(name)
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
            self._require_present(name)
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
        self._fetch()
        self._require_present(name)
        lock = self._require_lock(name)
        dest = self.doc_path(name)
        if not os.path.isfile(os.path.join(dest, "Document.xml")):
            raise DocumentError(f"{dest} is not a folder-format document")
        if not self._dirty(name):
            raise DocumentError(f"{name} has no changes to check in")
        for _ in range(8):
            self._fetch()
            self._require_present(name)
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
            self._require_present(name)
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

    # ---- where-used and move (slice 3) ---------------------------------------------------------

    @staticmethod
    def _link_target(file_attr):
        """Document name a stored link path points at, or None when it is not a root-level vault document.

        FreeCAD stores a link as a path relative to the linking document's own folder (``PropertyXLink`` writes
        ``DocInfo::getDocPath(.., relative=true)``); vault documents all sit side by side in the vault root, so a
        link into the vault is ``<name>.FCStd.d``. URLs, absolute paths and anything that leaves the root are not
        vault links. The comparison ignores case because macOS and Windows resolve the link that way and the vault
        forbids names that differ only by case."""
        p = file_attr.replace("\\", "/")
        if p.startswith("https://") or p.startswith("/") or re.match(r"^[A-Za-z]:", p):
            return None
        norm = posixpath.normpath(p)
        if norm.startswith("..") or "/" in norm or not norm.lower().endswith(DOC_SUFFIX.lower()):
            return None
        return norm[:-len(DOC_SUFFIX)]

    def _doc_names_at(self, commit):
        out = self._git(["ls-tree", "--name-only", commit]).stdout.decode().split("\n")
        return sorted(n[:-len(DOC_SUFFIX)] for n in out if n.endswith(DOC_SUFFIX))

    def _document_xml(self, commit, name):
        p = self._git(["cat-file", "blob", f"{commit}:{name}{DOC_SUFFIX}/Document.xml"], check=False)
        return p.stdout if p.returncode == 0 else None

    @staticmethod
    def _scan_xlinks(xml_bytes):
        """Every ``<XLink file=..>`` in a folder-format ``Document.xml`` as ``(object, property, file)``.

        FreeCAD writes the path-carrying form from ``PropertyXLink`` (also the base of ``App::Link``'s
        ``LinkedObject``, ``PropertyXLinkSub`` and, per element, ``PropertyXLinkList``, ``PropertyXLinkSubList`` and
        the ``ExpressionEngine``'s ``XLinks`` container) always as ``<XLink file="..." stamp=".." name="..">``; the
        element without ``file`` names an object in the same export and is not a path."""
        found = []
        try:
            text = xml_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise DocumentError("Document.xml is not UTF-8")
        if "<!DOCTYPE" in text or "<!ENTITY" in text:
            # FreeCAD never writes either; refusing them before parsing rules out entity-expansion bombs.
            raise DocumentError("Document.xml declares a DOCTYPE or ENTITY, which FreeCAD never writes")
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as e:
            raise DocumentError(f"Document.xml is not well-formed: {e}")

        def walk(el, objname):
            for child in el:
                if child.tag == "Property":
                    for x in child.iter("XLink"):
                        f = x.get("file")
                        if f:
                            found.append((objname, child.get("name"), f))
                else:
                    walk(child, child.get("name") if child.tag == "Object" and child.get("name") else objname)

        walk(root, "(document)")
        return found

    def _where_used_at(self, commit, name, suspects=None, unreadable=None):
        """Links to ``name`` at ``commit``. A link whose last component is ``name``'s folder but whose path is not a
        plain root-level one (``../x/name.FCStd.d``, an absolute path) cannot be proven to be this document, so it is
        appended to ``suspects`` when a list is given (``move`` refuses rather than leave it behind). A document whose
        ``Document.xml`` cannot be read safely (malformed, DOCTYPE/ENTITY, not UTF-8) is skipped and appended to
        ``unreadable`` as ``{document, reason, mentions}`` when a list is given (``mentions``: its raw text contains
        ``name``, so it might link to it); without the list it raises ``DocumentError``."""
        used = []
        for other in self._doc_names_at(commit):
            if other == name:
                continue
            xml = self._document_xml(commit, other)
            if xml is None:
                continue
            try:
                links = self._scan_xlinks(xml)
            except DocumentError as e:
                if unreadable is None:
                    raise
                unreadable.append({"document": other, "reason": str(e),
                                   "mentions": name.lower() in xml.decode("utf-8", "replace").lower()})
                continue
            for obj, prop, f in links:
                tgt = self._link_target(f)
                if tgt is not None and tgt.lower() == name.lower():
                    used.append({"document": other, "object": obj, "property": prop, "file": f})
                elif (tgt is None and suspects is not None and not f.startswith("https://")
                      and posixpath.basename(f.replace("\\", "/")).lower() == (name + DOC_SUFFIX).lower()):
                    suspects.append({"document": other, "object": obj, "property": prop, "file": f})
        return used

    def where_used(self, name):
        """Documents at the vault head that link to ``name``: a list of ``{document, object, property, file}``.

        The list has an ``unreadable`` attribute: documents skipped because their ``Document.xml`` is malformed or
        declares a DOCTYPE/ENTITY (one bad file never fails the query). Read from the committed ``Document.xml`` files with git only; no FreeCAD document is opened. Covers every
        property type that stores a file path (``App::Link``, ``PropertyXLink`` and its Sub/List variants, and
        expression links), because they all use the one ``<XLink file=..>`` element."""
        _check_name(name)
        self._fetch()
        self._require_present(name)
        unreadable = []
        used = UsedList(self._where_used_at(self._head(), name, unreadable=unreadable))
        used.unreadable = unreadable
        for u in unreadable:
            _warn(f"where_used({name}): skipped unreadable {u['document']}: {u['reason']}")
        return used

    def _rewrite_links(self, xml_bytes, old, new):
        """Replace, in the ``file`` attribute of every ``<XLink>`` that resolves to ``old``, the path by ``new``'s.

        Textual on purpose: only the matched attribute values change, so the rest of the file (and every shape file
        beside it) stays byte for byte, the diff is the link lines alone, and nothing is opened, recomputed or
        re-serialised (re-saving through FreeCAD would also rewrite touched flags and BREP numbers, see run 021
        Exhibit 2). Returns ``(new_bytes, count)``."""
        text = xml_bytes.decode("utf-8")
        count = 0

        def sub(m):
            nonlocal count
            raw = m.group(2)
            tgt = self._link_target(saxutils.unescape(raw, {"&quot;": '"', "&apos;": "'"}))
            if tgt is None or tgt.lower() != old.lower():
                return m.group(0)
            count += 1
            return m.group(1) + new + DOC_SUFFIX + m.group(3)

        return _XLINK_RE.sub(sub, text).encode("utf-8"), count

    def move(self, old, new, message=None):
        """Rename document ``old`` to ``new`` in the vault and re-point every link that names it.

        Takes the locks of ``old`` and of every document that links to it (refused with ``LockError`` naming the
        holder, and nothing changed, if another client holds any of them), then publishes one vault update that
        holds one commit per document: a ``move`` commit for ``new`` (same revision label and state, history
        follows the rename) and a ``relink`` commit per referencing document (next revision number, WIP; the next
        letter if it was Released). The commits go out in a single push, so the vault either has the whole move or
        none of it. Referencing documents that are InReview or Obsolete are refused, as for check-out; local edits
        in any involved working-copy folder are refused. Locks taken by the move are released at the end; the lock
        on ``old`` is always deleted since that name no longer exists. Returns ``{new, revision, sha, relinked}``."""
        _check_name(old)
        _check_name(new)
        _check_message(message or "")
        if old == new:
            raise DocumentError("old and new names are the same")
        held = {}  # name -> True when taken by this call (to be released on failure)
        try:
            for _ in range(8):
                self._fetch()
                head = self._head()
                names = self._doc_names_at(head)
                if old not in names:
                    raise DocumentError(f"no document {old!r} in the vault")
                for n in names:
                    if n.lower() == new.lower():
                        raise DocumentError(f"cannot move to {new!r}: document {n!r} already exists (names are case-insensitive)")
                if self._records(new):
                    raise DocumentError(f"cannot move to {new!r}: that name has history in the vault")
                suspects = []
                unreadable = []
                refs = sorted({u["document"] for u in self._where_used_at(head, old, suspects, unreadable)})
                risky = [u for u in unreadable if u["mentions"] and u["document"] != old]
                if risky:
                    raise DocumentError(
                        f"{risky[0]['document']} cannot be read ({risky[0]['reason']}) and its text mentions {old!r}, so it "
                        f"might link to it; repair or remove that document before moving {old}")
                if suspects:
                    s0 = suspects[0]
                    raise DocumentError(
                        f"{s0['document']} ({s0['object']}.{s0['property']}) links to {s0['file']!r}, which is not a plain "
                        f"vault-relative path to {old}; re-link it (e.g. save the pair from one folder) before moving")
                missing = [n for n in [old] + refs if n not in held]
                for n in missing:  # lock in sorted order; any refusal aborts before anything changed
                    mine_before = self._mine(self.lock_info(n, refresh=False))
                    self.lock(n)
                    held[n] = not mine_before
                if missing:
                    continue  # the head or the reference set may have moved while locking; recompute under the locks
                # Under all locks and the set is stable at this head.
                recs_old = self._records(old)
                for n in refs:
                    self._check_checkout_allowed(n, self._records(n)[0], True)
                    if self._dirty(n):
                        raise DocumentError(f"{n} has local edits in this working copy; check them in first")
                if self._dirty(old):
                    raise DocumentError(f"{old} has local edits in this working copy; check them in first")
                mv_rec = recs_old[0]
                parent = head
                relinked = []
                subject = message or f"Move {old} -> {new}"
                _tree, parent = self._commit(
                    parent, new, subject, mv_rec["revision"], mv_rec["state"], "move", mv_rec["released"], None,
                    edit=lambda env: self._edit_rename(env, head, old, new), extra=[("Moved-From", old)])
                for n in refs:
                    rec = self._records(n)[0]
                    letter, number = parse_label(rec["revision"])
                    letter, number = (letter + 1, 1) if rec["released"] else (letter, number + 1)
                    label = revision_label(letter, number)
                    _tree, parent = self._commit(
                        parent, n, f"Relink {n}: {old} -> {new}", label, "WIP", "relink", False, None,
                        edit=lambda env, n=n: self._edit_relink(env, head, n, old, new),
                        extra=[("Relink", f"{old} -> {new}")])
                    relinked.append({"document": n, "revision": label, "sha": parent})
                _fault("after_commit")
                if not self._push_main(parent):
                    continue  # vault moved; recompute everything under the locks we hold
                self._fetch()
                warnings = [f"skipped unreadable {u['document']}: {u['reason']}" for u in unreadable]
                warnings += self._after_move(old, new, refs, parent)
                for w in warnings:
                    _warn(w)
                for n, mine in held.items():
                    if n == old or mine:
                        with contextlib.suppress(PDMError):
                            self._delete_lock_if_mine(n)
                held.clear()
                return {"new": new, "revision": mv_rec["revision"], "sha": parent, "relinked": relinked,
                        "warnings": warnings, "unreadable": [u["document"] for u in unreadable]}
            raise GitError("vault kept moving; move gave up after 8 attempts (nothing changed)")
        except BaseException:
            for n, mine in held.items():
                if mine:
                    with contextlib.suppress(Exception):
                        self.release_lock(n)
            raise

    def _delete_lock_if_mine(self, name):
        cur = self.lock_info(name)
        if cur is not None and self._mine(cur):
            self._delete_lock(name, cur["sha"])

    def _edit_rename(self, env, head, old, new):
        """Index edit: every entry under ``old``'s folder moves to ``new``'s folder."""
        listing = self._git(["ls-tree", "-r", "-z", head, "--", old + DOC_SUFFIX]).stdout
        lines = []
        for ent in listing.split(b"\0"):
            if not ent:
                continue
            meta, _, path = ent.partition(b"\t")
            mode, _typ, sha = meta.decode().split()
            path = path.decode("utf-8", "surrogateescape")
            rel = path[len(old + DOC_SUFFIX):]
            lines.append(f"0 {'0' * 40}\t{path}\0")
            lines.append(f"{mode} {sha}\t{new}{DOC_SUFFIX}{rel}\0")
        self._git(["update-index", "-z", "--index-info"], env=env,
                  input="".join(lines).encode("utf-8", "surrogateescape"))
        # A link from the document to itself names the old folder; re-point it in the same commit.
        xml = self._document_xml(head, old)
        if xml is not None:
            try:
                self._scan_xlinks(xml)
            except DocumentError:
                return  # unreadable: left as it is (move reports it)
            out, count = self._rewrite_links(xml, old, new)
            if count:
                path = f"{new}{DOC_SUFFIX}/Document.xml"
                if self._where_used_at_bytes(out, old):
                    raise DocumentError(self._odd_link_msg(old, old))
                sha = self._git(["hash-object", "-w", "--stdin"], input=out).stdout.decode().strip()
                mode = self._git(["ls-tree", head, "--", f"{old}{DOC_SUFFIX}/Document.xml"]).stdout.decode().split()[0]
                self._git(["update-index", "--add", "--cacheinfo", f"{mode},{sha},{path}"], env=env)
            elif self._where_used_at_bytes(xml, old):
                raise DocumentError(self._odd_link_msg(old, old))

    @staticmethod
    def _odd_link_msg(name, old):
        return (f"{name}: a link to {old} is written with a numeric character reference or a single-quoted attribute, "
                f"which move does not rewrite; re-save {name} with FreeCAD (it writes plain double-quoted paths) and retry")

    def _edit_relink(self, env, head, name, old, new):
        path = f"{name}{DOC_SUFFIX}/Document.xml"
        xml = self._document_xml(head, name)
        out, count = self._rewrite_links(xml, old, new)
        if self._where_used_at_bytes(out, old):
            raise DocumentError(self._odd_link_msg(name, old))
        if count == 0:
            raise DocumentError(f"{name}: no link to {old} found to rewrite (the document changed under the lock?)")
        sha = self._git(["hash-object", "-w", "--stdin"], input=out).stdout.decode().strip()
        mode = self._git(["ls-tree", head, "--", path]).stdout.decode().split()[0]
        self._git(["update-index", "--add", "--cacheinfo", f"{mode},{sha},{path}"], env=env)

    def _where_used_at_bytes(self, xml, name):
        return [1 for _o, _p, f in self._scan_xlinks(xml)
                if (self._link_target(f) or "").lower() == name.lower()]

    def _after_move(self, old, new, refs, sha):
        """Bring this working copy in line: clean folders of relinked documents are refreshed to the new
        revision, the old folder is dropped, the new one is not materialised (check it out when needed)."""
        warnings = []
        for n in refs:
            dest = self.doc_path(n)
            if os.path.isdir(dest) and self._base(n) is not None:
                shutil.rmtree(dest, ignore_errors=True)
                self._git(["checkout", "-q", sha, "--", os.path.basename(dest)])
                self._set_base(n, sha)
        if os.path.isdir(self.doc_path(old)):
            if self._base(old) is None:
                warnings.append(f"left the untracked local folder {self.doc_path(old)} in place (it is not a checkout of {old})")
            elif self._dirty(old):
                warnings.append(f"left {self.doc_path(old)} in place: it has local edits")
            else:
                shutil.rmtree(self.doc_path(old), ignore_errors=True)
        if not os.path.isdir(self.doc_path(old)):
            with contextlib.suppress(FileNotFoundError):
                os.remove(self._base_file(old))
        return warnings

    # ---- reading old revisions ----------------------------------------------------------------

    def find_revision(self, name, revision):
        """The newest record of ``name`` with label ``revision`` that holds content (a check-in or add)."""
        for r in self.history(name):
            if r["revision"] == revision and r["event"] in CONTENT_EVENTS:
                return r
        raise DocumentError(f"{name} has no revision {revision}")

    def export_revision(self, name, revision, dest=None):
        """Write the folder of one revision to ``dest`` (a new temp dir by default). Returns the folder path,
        made read-only on disk."""
        rec = self.find_revision(name, revision)
        base = dest or tempfile.mkdtemp(prefix="pdm-rev-")
        os.makedirs(base, exist_ok=True)
        folder = (rec.get("document") or name) + DOC_SUFFIX  # the name at that commit (before a move: the old one)
        arch = _git(["archive", "--format=tar", rec["sha"], "--", folder], cwd=self.workdir)
        subprocess.run(["tar", "-x", "-C", base], input=arch.stdout, check=True)
        path = os.path.join(base, folder)
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
