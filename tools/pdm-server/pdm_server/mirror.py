# SPDX-License-Identifier: LGPL-2.1-or-later
"""The service's read-only clone of the vault, driven by the existing ``PDM.vault.Vault`` class.

No vault rule is re-implemented here: document presence, the link scanner, the link-target rule and the
revision records all come from ``Vault``. The clone is a service-side cache of Forgejo's repository, used for
the where-used index and for the lifecycle check in the push hook."""

import os
import sys
import threading


def load_vault_module(lib_path):
    """Import ``PDM.vault`` from ``lib_path`` (the FreeCAD tree's src/Mod/PDM); it needs no FreeCAD import."""
    if lib_path and lib_path not in sys.path:
        sys.path.insert(0, lib_path)
    from PDM import vault
    return vault


class Mirror:
    def __init__(self, cfg, forgejo_token):
        self.V = load_vault_module(cfg.vault_lib)
        self.lock = threading.Lock()
        self.env = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader",
                    "GIT_CONFIG_VALUE_0": f"Authorization: token {forgejo_token}"}
        V = self.V

        class MirrorVault(V.Vault):
            def _fetch_specs(self):
                return [f"+refs/heads/{V.BRANCH}:refs/remotes/origin/{V.BRANCH}"]

        self.vault = None
        self.cfg = cfg
        self.cls = MirrorVault

    def _open(self):
        if self.vault is None:
            wd = os.path.join(self.cfg.mirror_dir, "mirror")
            if os.path.isdir(os.path.join(wd, ".git")):
                self.vault = self.cls(wd, "pdm-service", self.cfg.git_url, git_env=self.env)
            else:
                self.vault = self.cls.clone(self.cfg.git_url, wd, "pdm-service", git_env=self.env)
        return self.vault

    def sync(self):
        """Fetch the vault head; returns (vault, head_sha). Caller holds ``self.lock`` while using the vault."""
        v = self._open()
        v._fetch()
        return v, v._head()
