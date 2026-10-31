# SPDX-License-Identifier: LGPL-2.1-or-later
"""Remote backend of the PDM vault (G03 slice 4): the ``Vault`` interface against a PDM server.

Document content stays in git, hosted by Forgejo over HTTP; this class fetches and pushes with the user's
Forgejo access token. What the server owns is the part git cannot decide safely for a team: locks (a database
row with a unique constraint, so exactly one of two racing clients wins), the audit log, role checks and the
where-used index. Every rule that is not about those (lifecycle table, revision labels, link rewriting, move,
history, export) is the ``Vault`` code, unchanged; this subclass only swaps the lock store and the transport.

Configuration is by environment variable, never by file: ``PDM_SERVER_URL`` (the server's base URL) and
``PDM_TOKEN`` (the user's Forgejo access token). The URL of a cloned working copy is remembered in
``.git/pdm/server``; the token never is (it reaches git through the process environment only).

Python standard library only (no ``requests``), so the FreeCAD side gains no dependency.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from PDM import vault as V
from PDM.vault import DocumentError, GitError, LockError, PDMError  # noqa: F401  (re-exported for callers)

TIMEOUT = 30


class AuthError(PDMError):
    """The server did not accept the credentials (missing, wrong or expired token)."""


class PermissionDenied(PDMError):
    """The credentials are valid but the user's role does not allow the operation."""


class ServerUnavailable(PDMError):
    """The server (or its database) could not be reached or answered with a 5xx; nothing was changed."""


def _auth_env(token):
    """Environment that makes git send the token to the vault's HTTP remote without storing it anywhere."""
    if not token:
        return {}
    return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: token {token}"}


class Api:
    """Minimal JSON client of the PDM server."""

    def __init__(self, base_url, token):
        self.base = base_url.rstrip("/")
        self.token = token

    def call(self, method, path, body=None, auth=True):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        if auth and self.token:
            req.add_header("Authorization", f"token {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                data = json.loads(e.read() or b"null")
            except ValueError:
                data = None
            return e.code, data
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise ServerUnavailable(f"PDM server {self.base} is unreachable: {e}") from None

    def ok(self, method, path, body=None):
        """Call and raise the matching error for a failure status; returns the decoded body on 2xx."""
        status, data = self.call(method, path, body)
        if 200 <= status < 300:
            return data
        detail = data.get("detail") if isinstance(data, dict) else None
        msg = detail if isinstance(detail, str) else json.dumps(detail or data)
        if status == 401:
            raise AuthError(msg or "not authenticated")
        if status == 403:
            raise PermissionDenied(msg or "forbidden")
        if status == 404:
            raise DocumentError(msg or "not found")
        if status == 409 and isinstance(detail, dict) and "lock" in detail:
            raise LockError(detail.get("message") or "lock conflict", (detail["lock"] or {}).get("holder"))
        if status >= 500:
            raise ServerUnavailable(f"PDM server error {status}: {msg}")
        raise PDMError(f"PDM server answered {status}: {msg}")


class RemoteVault(V.Vault):
    """``Vault`` whose git remote is the server's Forgejo repository and whose locks live on the server."""

    def __init__(self, workdir, user, vault_path=None, server_url=None, token=None, git_env=None):
        server_url = server_url or os.environ.get("PDM_SERVER_URL") or self._saved_server(workdir)
        token = token if token is not None else os.environ.get("PDM_TOKEN", "")
        if not server_url:
            raise PDMError("no PDM server: set PDM_SERVER_URL")
        self.server_url = server_url
        self.api = Api(server_url, token)
        self._lock_cache = {}
        self._identity_checked = False
        env = dict(_auth_env(token))
        env.update(git_env or {})
        super().__init__(workdir, user, vault_path, git_env=env)

    @staticmethod
    def _saved_server(workdir):
        try:
            with open(os.path.join(workdir, ".git", "pdm", "server")) as f:
                return f.read().strip()
        except FileNotFoundError:
            return None

    @classmethod
    def clone(cls, vault_path, workdir, user, git_env=None):
        """Clone from the server named by ``vault_path`` (its base URL); credentials from ``PDM_TOKEN``."""
        token = os.environ.get("PDM_TOKEN", "")
        api = Api(vault_path, token)
        cfg = api.ok("GET", "/config")
        env = dict(_auth_env(token))
        env.update(git_env or {})
        workdir = os.path.abspath(workdir)
        V._git(["clone", "-q", "--no-checkout", cfg["git_url"], workdir], env=env)
        os.makedirs(os.path.join(workdir, ".git", "pdm", "base"), exist_ok=True)
        with open(os.path.join(workdir, ".git", "pdm", "server"), "w") as f:
            f.write(vault_path.rstrip("/") + "\n")
        v = cls(workdir, user, cfg["git_url"], server_url=vault_path, token=token)
        v._check_identity()
        return v

    @classmethod
    def open(cls, workdir, user):
        return cls(workdir, user)

    # ---- identity ----------------------------------------------------------------------------

    def whoami(self):
        """``{user, role}`` as the server sees the token (the role is admin, writer or reader)."""
        return self.api.ok("GET", "/whoami")

    def _check_identity(self):
        if not self._identity_checked:
            me = self.whoami()
            if me["user"] != self.user:
                raise AuthError(f"the token belongs to {me['user']!r}, not {self.user!r}")
            self._identity_checked = True

    # ---- fetch / lock store -------------------------------------------------------------------

    def _fetch_specs(self):
        return [f"+refs/heads/{V.BRANCH}:refs/remotes/origin/{V.BRANCH}"]

    def _fetch(self):
        self._check_identity()
        locks = self.api.ok("GET", "/locks")
        try:
            super()._fetch()
        except GitError as e:
            raise self._git_failure(e) from None
        self._lock_cache = locks

    @staticmethod
    def _git_failure(e):
        text = str(e)
        if "401" in text or "Authentication failed" in text or "could not read Username" in text:
            return AuthError(f"the vault host refused the credentials: {text}")
        if "403" in text or "denied" in text:
            return PermissionDenied(f"the vault host refused the request: {text}")
        if "Could not resolve host" in text or "Failed to connect" in text or "Connection refused" in text:
            return ServerUnavailable(f"the vault host is unreachable: {text}")
        return e

    def _lock_get(self, name):
        info = self._lock_cache.get(name)
        return dict(info) if info else None

    def _lock_names(self):
        return sorted(self._lock_cache)

    def _lock_create(self, name, payload):
        body = json.loads(payload)
        status, data = self.api.call("POST", f"/locks/{urllib.parse.quote(name)}", body)
        if 200 <= status < 300:
            self._lock_cache[name] = data
            return None
        if status == 409:
            return "lock held"
        self.api.ok("POST", f"/locks/{urllib.parse.quote(name)}", body)  # raises the mapped error
        return f"server answered {status}"

    def _delete_lock(self, name, sha):
        self.api.ok("DELETE", f"/locks/{urllib.parse.quote(name)}?lock_id={urllib.parse.quote(sha)}")
        self._lock_cache.pop(name, None)

    def break_lock(self, name):
        """Administrative (admin role only, recorded in the audit log): remove whoever's lock is on ``name``."""
        cur = self.lock_info(name)
        if cur is not None:
            self.api.ok("POST", f"/locks/{urllib.parse.quote(name)}/break")
            self._lock_cache.pop(name, None)
        return cur

    # ---- git transport errors -----------------------------------------------------------------

    def _push_main(self, sha):
        V._fault("before_push")
        p = self._git(["push", "-q", "origin", f"{sha}:refs/heads/{V.BRANCH}"], check=False)
        if p.returncode == 0:
            return True
        err = p.stderr.decode(errors="replace").strip()
        if "could not be asked" in err:  # the hook failed closed because the PDM server is down
            raise ServerUnavailable(f"the PDM server is unreachable, so the vault refused the push: {err}")
        if "pre-receive hook declined" in err or "remote rejected" in err or "denied" in err or "403" in err:
            if "non-fast-forward" in err or "fetch first" in err:
                return False
            raise PermissionDenied(f"the vault refused the push: {err}")
        if "401" in err or "Authentication failed" in err:
            raise AuthError(f"the vault host refused the credentials: {err}")
        if "Could not resolve host" in err or "Failed to connect" in err or "Connection refused" in err:
            raise ServerUnavailable(f"the vault host is unreachable: {err}")
        return False

    # ---- where-used from the server's index ---------------------------------------------------

    def where_used(self, name):
        """Documents at the vault head that link to ``name``, from the server's where-used index (built with the
        same ``Vault`` scanner; see tools/pdm-server). Same shape as the local ``where_used``."""
        V._check_name(name)
        data = self.api.ok("GET", f"/where-used/{urllib.parse.quote(name)}")
        used = V.UsedList(data["used"])
        used.unreadable = data.get("unreadable", [])
        for u in used.unreadable:
            V._warn(f"where_used({name}): skipped unreadable {u['document']}: {u['reason']}")
        return used
