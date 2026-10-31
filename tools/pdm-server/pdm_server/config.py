# SPDX-License-Identifier: LGPL-2.1-or-later
"""Configuration by environment variable only."""

import os


def _req(name):
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"environment variable {name} is required")
    return v


class Config:
    def __init__(self):
        self.database_url = _req("PDM_DATABASE_URL")        # postgresql://user@host:port/dbname
        self.forgejo_url = _req("PDM_FORGEJO_URL").rstrip("/")   # http://127.0.0.1:3000
        self.forgejo_token = _req("PDM_FORGEJO_TOKEN")      # service account token (site admin or repo admin)
        self.repo = _req("PDM_REPO")                         # owner/name of the vault repository
        self.hook_secret = _req("PDM_HOOK_SECRET")           # shared with the repository's pre-receive hook
        self.git_url = os.environ.get("PDM_GIT_URL") or f"{self.forgejo_url}/{self.repo}.git"
        self.mirror_dir = _req("PDM_MIRROR_DIR")             # where the service keeps its read-only clone
        # The vault library (PDM.vault) the service imports for lifecycle rules and the link scanner.
        self.vault_lib = os.environ.get("PDM_VAULT_LIB", "")
