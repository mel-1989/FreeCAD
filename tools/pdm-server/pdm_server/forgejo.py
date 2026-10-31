# SPDX-License-Identifier: LGPL-2.1-or-later
"""Forgejo as the source of user identity and repository roles (standard library HTTP only)."""

import json
import urllib.error
import urllib.parse
import urllib.request


class ForgejoDown(Exception):
    pass


class Forgejo:
    def __init__(self, base, service_token, repo):
        self.base = base
        self.service_token = service_token
        self.repo = repo

    def _get(self, path, token):
        req = urllib.request.Request(self.base + "/api/v1" + path, headers={"Authorization": f"token {token}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, None
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise ForgejoDown(str(e)) from None

    def login_of(self, token):
        """The Forgejo login the token belongs to, or None when Forgejo rejects it."""
        status, data = self._get("/user", token)
        if status != 200 or not data:
            return None
        return data["login"], bool(data.get("is_admin"))

    def is_site_admin(self, login):
        status, data = self._get(f"/users/{urllib.parse.quote(login)}", self.service_token)
        return status == 200 and bool(data and data.get("is_admin"))

    def role_of(self, login, site_admin=False):
        """admin, writer, reader or None, from the user's permission on the vault repository."""
        if site_admin:
            return "admin"
        status, data = self._get(
            f"/repos/{self.repo}/collaborators/{urllib.parse.quote(login)}/permission", self.service_token)
        if status != 200 or not data:
            return None
        perm = data.get("permission")
        return {"owner": "admin", "admin": "admin", "write": "writer", "read": "reader"}.get(perm)
