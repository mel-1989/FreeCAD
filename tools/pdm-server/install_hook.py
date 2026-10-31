#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Install the PDM pre-receive hook into a Forgejo repository directory.

    install_hook.py <repo.git directory> <server url> <secret> [python interpreter]

Writes ``hooks/pre-receive.d/pdm`` (the hook, with the given interpreter, default this one, on its first line) and, in the repository
directory, ``pdm-hook.json`` (mode 600). The secret is the server's ``PDM_HOOK_SECRET``.
"""

import json
import os
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def install(repo_dir, url, secret, python=None):
    d = os.path.join(repo_dir, "hooks", "pre-receive.d")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(HERE, "hook", "pre-receive")) as f:
        text = f.read().split("\n", 1)[1]
    hook = os.path.join(d, "pdm")
    with open(hook, "w") as f:
        f.write(f"#!{python or sys.executable}\n{text}")
    os.chmod(hook, 0o755)
    cfg = os.path.join(repo_dir, "pdm-hook.json")
    with open(cfg, "w") as f:
        json.dump({"url": url, "secret": secret}, f)
    os.chmod(cfg, stat.S_IRUSR | stat.S_IWUSR)
    return hook


if __name__ == "__main__":
    print(install(*sys.argv[1:5]))
