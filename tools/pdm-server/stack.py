#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Throwaway PostgreSQL + Forgejo + PDM server for tests and demos. Standard library only.

Everything binds to 127.0.0.1 on free high ports and lives in one temporary directory; nothing is installed as a
service (no brew services, no launchd). ``Stack.stop()`` (or leaving the ``with`` block) kills what ``start()``
started and removes the directory. Users and tokens are generated on every start and never written anywhere but
that directory.

    python3 stack.py up        # start, print the environment a client needs, wait for Ctrl-C

Binaries are found from ``PDM_PG_BIN`` (default: Homebrew's postgresql@17), ``PDM_FORGEJO`` (default: Homebrew's
forgejo) and ``PDM_SERVER_VENV`` (the venv holding requirements.txt, default ~/src/cad-fork/venvs/pdm-server).
"""

import contextlib
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PG_BIN = os.environ.get("PDM_PG_BIN", "/opt/homebrew/opt/postgresql@17/bin")
FORGEJO = os.environ.get("PDM_FORGEJO", "/opt/homebrew/opt/forgejo/bin/forgejo")
VENV = os.environ.get("PDM_SERVER_VENV", os.path.expanduser("~/src/cad-fork/venvs/pdm-server"))
OWNER, REPO = "pdmadmin", "vault"
USERS = {"alice": "write", "bob": "write", "carol": "read", "dora": "admin"}  # Forgejo collaborator permission


def missing():
    """Why the stack cannot run here, or None."""
    for what, path in (("postgres", os.path.join(PG_BIN, "pg_ctl")), ("initdb", os.path.join(PG_BIN, "initdb")),
                       ("forgejo", FORGEJO), ("the service venv", os.path.join(VENV, "bin", "python"))):
        if not os.path.exists(path):
            return f"{what} not found at {path}"
    p = subprocess.run([os.path.join(VENV, "bin", "python"), "-c", "import fastapi, uvicorn, psycopg"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if p.returncode != 0:
        return f"the venv {VENV} lacks the packages of tools/pdm-server/requirements.txt"
    return None


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http(method, url, token=None, body=None, timeout=10):
    req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"token {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except ValueError:
            return e.code, None


def wait_http(url, seconds=60):
    end = time.time() + seconds
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except urllib.error.HTTPError:
            return
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    raise RuntimeError(f"{url} did not come up in {seconds}s")


class Stack:
    def __init__(self, vault_lib):
        """``vault_lib``: the directory holding the ``PDM`` package (the FreeCAD tree's src/Mod/PDM, or its build copy)."""
        self.vault_lib = vault_lib
        self.root = tempfile.mkdtemp(prefix="pdms", dir="/tmp")
        self.procs = {}
        self.tokens = {}
        self.hook_secret = secrets.token_hex(16)
        self.pg_port, self.fj_port, self.svc_port = free_port(), free_port(), free_port()
        self.url = f"http://127.0.0.1:{self.svc_port}"
        self.fj_url = f"http://127.0.0.1:{self.fj_port}"
        self.repo_dir = os.path.join(self.root, "forgejo", "repos", OWNER, REPO + ".git")

    # ---- lifecycle ---------------------------------------------------------------------------

    def __enter__(self):
        try:
            self.start()
        except BaseException:
            self.stop()
            raise
        return self

    def __exit__(self, *exc):
        self.stop()

    def start(self):
        self.start_pg(initialise=True)
        self.start_forgejo()
        self.bootstrap_forgejo()
        self.start_service()
        self.push_init_commit()
        self.verify_hook_enforces()

    def stop(self):
        for name in ("service", "forgejo"):
            self._kill(name)
        with contextlib.suppress(Exception):
            self.stop_pg()
        self._kill("postgres")
        shutil.rmtree(self.root, ignore_errors=True)

    def _kill(self, name):
        p = self.procs.pop(name, None)
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()

    # ---- postgres ----------------------------------------------------------------------------

    @property
    def database_url(self):
        return f"postgresql://pdm@127.0.0.1:{self.pg_port}/pdm"

    def _pg(self, name, *args, **kw):
        # PostgreSQL on macOS aborts at start-up ("became multithreaded") unless the locale is valid.
        env = dict(os.environ, LC_ALL="en_US.UTF-8", LANG="en_US.UTF-8")
        return subprocess.run([os.path.join(PG_BIN, name)] + list(args), check=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, env=env, **kw)

    def start_pg(self, initialise=False):
        data = os.path.join(self.root, "pgdata")
        if initialise:
            self._pg("initdb", "-D", data, "-U", "pdm", "--auth=trust", "-E", "UTF8", "--no-sync")
        sock = os.path.join(self.root, "pgsock")
        os.makedirs(sock, exist_ok=True)
        self._pg("pg_ctl", "-D", data, "-w", "-l", os.path.join(self.root, "pg.log"), "-o",
                 f"-p {self.pg_port} -c listen_addresses=127.0.0.1 -c unix_socket_directories={sock} -c fsync=on",
                 "start")
        if initialise:
            self._pg("createdb", "-h", "127.0.0.1", "-p", str(self.pg_port), "-U", "pdm", "pdm")

    def stop_pg(self, mode="fast"):
        self._pg("pg_ctl", "-D", os.path.join(self.root, "pgdata"), "-m", mode, "-w", "stop")

    def restart_pg(self, mode="immediate"):
        """Stop PostgreSQL (``immediate`` is a crash: no shutdown checkpoint) and start it again on the same port."""
        self.stop_pg(mode)
        self.start_pg()

    # ---- forgejo -----------------------------------------------------------------------------

    def _fj(self, *args, **kw):
        base = os.path.join(self.root, "forgejo")
        env = dict(os.environ, HOME=self.root, GITEA_WORK_DIR=base)
        return subprocess.run([FORGEJO, "--config", os.path.join(base, "app.ini"), "--work-path", base] + list(args),
                              env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw)

    def start_forgejo(self):
        base = os.path.join(self.root, "forgejo")
        for d in ("data", "repos", "log", "work"):
            os.makedirs(os.path.join(base, d), exist_ok=True)
        gen = lambda kind: subprocess.run([FORGEJO, "generate", "secret", kind], check=True, stdout=subprocess.PIPE,
                                          text=True).stdout.strip()
        with open(os.path.join(base, "app.ini"), "w") as f:
            f.write(f"""APP_NAME = pdm-test
RUN_MODE = prod
WORK_PATH = {base}
[database]
DB_TYPE = sqlite3
PATH = {base}/data/forgejo.db
[repository]
ROOT = {base}/repos
[server]
HTTP_ADDR = 127.0.0.1
HTTP_PORT = {self.fj_port}
DOMAIN = 127.0.0.1
ROOT_URL = {self.fj_url}/
DISABLE_SSH = true
START_SSH_SERVER = false
OFFLINE_MODE = true
LFS_START_SERVER = false
APP_DATA_PATH = {base}/data
[security]
INSTALL_LOCK = true
SECRET_KEY = {gen('SECRET_KEY')}
INTERNAL_TOKEN = {gen('INTERNAL_TOKEN')}
[oauth2]
JWT_SECRET = {gen('JWT_SECRET')}
[log]
MODE = file
LEVEL = Warn
ROOT_PATH = {base}/log
[service]
DISABLE_REGISTRATION = true
[mailer]
ENABLED = false
[cron]
ENABLED = false
[other]
SHOW_FOOTER_VERSION = false
""")
        self._fj("migrate")
        env = dict(os.environ, HOME=self.root, GITEA_WORK_DIR=base)
        self.procs["forgejo"] = subprocess.Popen(
            [FORGEJO, "web", "--config", os.path.join(base, "app.ini"), "--work-path", base], env=env,
            stdin=subprocess.DEVNULL, stdout=open(os.path.join(self.root, "forgejo.out"), "w"), stderr=subprocess.STDOUT)
        wait_http(f"{self.fj_url}/api/v1/version")

    def bootstrap_forgejo(self):
        pw = secrets.token_hex(12) + "Aa1!"
        for user, admin in [(OWNER, True)] + [(u, False) for u in USERS]:
            self._fj("admin", "user", "create", "--username", user, "--password", pw,
                     "--email", f"{user}@example.invalid", "--must-change-password=false", *(["--admin"] if admin else []))
            self.tokens[user] = self._fj("admin", "user", "generate-access-token", "--username", user,
                                         "--token-name", "pdm-test", "--scopes", "all", "--raw").stdout.strip()
        st, _ = http("POST", f"{self.fj_url}/api/v1/user/repos", self.tokens[OWNER],
                     {"name": REPO, "private": True, "default_branch": "main"})
        assert st == 201, f"repo creation failed: {st}"
        for user, perm in USERS.items():
            st, _ = http("PUT", f"{self.fj_url}/api/v1/repos/{OWNER}/{REPO}/collaborators/{user}", self.tokens[OWNER],
                         {"permission": perm})
            assert st == 204, f"collaborator {user}: {st}"
        sys.path.insert(0, HERE)
        import install_hook
        install_hook.install(self.repo_dir, self.url, self.hook_secret, os.path.join(VENV, "bin", "python"))  # never sys.executable: inside FreeCAD that is FreeCADCmd

    def push_init_commit(self):
        """The admin publishes the vault's initial commit (made by ``Vault.create``), through the hook."""
        sys.path.insert(0, self.vault_lib)
        from PDM import vault as V
        local = V.Vault.create(os.path.join(self.root, "init.git"))
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.extraHeader",
                   GIT_CONFIG_VALUE_0=f"Authorization: token {self.tokens[OWNER]}", HOME=self.root)
        p = subprocess.run(["git", "--git-dir", local, "push", "-q", self.git_url, "main:refs/heads/main"], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if p.returncode != 0:
            raise RuntimeError(f"initial push failed: {p.stderr}")

    def verify_hook_enforces(self):
        """Fail loudly if the hook is not running: a push with no PDM trailers must be refused (a hook that is
        silently skipped would otherwise leave every later test passing against an unguarded vault)."""
        work = os.path.join(self.root, "hookcheck")
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.extraHeader",
                   GIT_CONFIG_VALUE_0=f"Authorization: token {self.tokens['alice']}", HOME=self.root,
                   GIT_AUTHOR_NAME="x", GIT_AUTHOR_EMAIL="x@x", GIT_COMMITTER_NAME="x", GIT_COMMITTER_EMAIL="x@x")
        run = lambda *a: subprocess.run(["git"] + list(a), cwd=work, env=env, stdout=subprocess.PIPE,  # noqa: E731
                                        stderr=subprocess.PIPE, text=True)
        subprocess.run(["git", "clone", "-q", self.git_url, work], env=env, check=True, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
        run("commit", "-q", "--allow-empty", "-m", "no trailers")
        p = run("push", "-q", "origin", "HEAD:refs/heads/main")
        if p.returncode == 0:
            raise RuntimeError("the pre-receive hook is not enforcing: an unguarded push was accepted")

    @property
    def git_url(self):
        return f"{self.fj_url}/{OWNER}/{REPO}.git"

    # ---- service -----------------------------------------------------------------------------

    def start_service(self):
        env = dict(os.environ, PDM_DATABASE_URL=self.database_url, PDM_FORGEJO_URL=self.fj_url,
                   PDM_FORGEJO_TOKEN=self.tokens[OWNER], PDM_REPO=f"{OWNER}/{REPO}", PDM_HOOK_SECRET=self.hook_secret,
                   PDM_MIRROR_DIR=os.path.join(self.root, "service"), PDM_VAULT_LIB=self.vault_lib, HOME=self.root,
                   PYTHONPATH=HERE)
        os.makedirs(env["PDM_MIRROR_DIR"], exist_ok=True)
        self.procs["service"] = subprocess.Popen(
            [os.path.join(VENV, "bin", "python"), "-m", "uvicorn", "pdm_server.app:app", "--host", "127.0.0.1",
             "--port", str(self.svc_port), "--log-level", "warning"], cwd=HERE, env=env, stdin=subprocess.DEVNULL,
            stdout=open(os.path.join(self.root, "service.out"), "a"), stderr=subprocess.STDOUT)
        wait_http(f"{self.url}/health")

    def stop_service(self):
        self._kill("service")

    def restart_service(self):
        self.stop_service()
        self.start_service()

    def env_for(self, user):
        """The environment a client process needs to act as ``user``."""
        return {"PDM_SERVER_URL": self.url, "PDM_TOKEN": self.tokens[user]}


if __name__ == "__main__" and sys.argv[1:] == ["up"]:
    reason = missing()
    if reason:
        sys.exit(f"cannot start: {reason}")
    lib = os.environ.get("PDM_VAULT_LIB") or os.path.join(HERE, "..", "..", "src", "Mod", "PDM")
    with Stack(os.path.abspath(lib)) as st:
        print(f"PDM_SERVER_URL={st.url}")
        for u, t in st.tokens.items():
            print(f"token {u}={t}")
        signal.pause()
