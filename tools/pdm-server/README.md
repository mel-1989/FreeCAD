---
tags: [type/method, for/agent, status/live]
---

# PDM server (G03 slice 4)

A small service that turns the single-site PDM vault (`src/Mod/PDM`) into a multi-user one. Document content stays in git, hosted by Forgejo over HTTP; this service owns what git cannot decide safely for a team: locks, roles, an audit log and a where-used index. The FreeCAD side is `PDM.remote.RemoteVault`, a subclass of `Vault` with the same interface, so the slice 5 panels and the slice 2 and 3 scenarios run unchanged against either backend.

## Design

| Concern | Where it lives |
|---|---|
| Users and tokens | Forgejo. A client sends its Forgejo access token (`Authorization: token ...`); the service asks Forgejo who it is |
| Roles | The user's permission on the vault repository in Forgejo: admin (admin or site admin), writer (write), reader (read). No second user table |
| Document content, revisions, history | git, in the Forgejo repository `main` branch. Lifecycle table, revision labels, link rewriting, `move`, `history`, `export_revision` are `PDM/vault.py`, imported, not copied |
| Locks | A row in PostgreSQL (`locks`, primary key on the document plus a unique index on its lower-case name), taken with `INSERT ... ON CONFLICT DO NOTHING`, so exactly one of two racing clients wins |
| Audit log | PostgreSQL `audit`: every lock, unlock, lock break and refused push |
| Where-used index | PostgreSQL `links` and `unreadable`, rebuilt when the vault head moves, from the service's own read-only clone, by `Vault._scan_xlinks` and `Vault._link_target` |

Only Forgejo credentials are shared with clients; the service's own Forgejo token and the hook secret never leave the server.

## How a check-in cannot bypass the lock

The repository carries a `pre-receive` hook (`hook/pre-receive`, installed by `install_hook.py`). Every push, from the library or from a plain `git push`, goes through it, and it asks the service (`POST /internal/pre-receive`, shared secret) before git accepts anything:

- Only `refs/heads/main` can be pushed, never deleted, never rewritten. Lock refs do not exist in this mode.
- The pusher must be a writer or admin (Forgejo itself already refuses a read-only collaborator).
- Each pushed commit names a document and an event in its `PDM-` trailers; the hook also reports which top-level folders the commit really changes. `checkin` and `relink` may change only their own document's folder and need the pusher to hold its lock; `move` needs the lock on the old name and may touch only the two folders; `transition` must change no files, needs the document unlocked and must be a step of the lifecycle table (`Vault.TRANSITIONS`, read from the previous state in the service's clone); `add` may add only its own folder; all but `transition` must leave the document WIP and unreleased; `init` is for an admin on an empty repository.
- The hook fails closed: if the service cannot be reached the push is refused, so a service outage can never let an unlocked change in. The tests also check at start-up that the hook is really running.

### Known gaps

- Revision labels are not checked on push: a writer who holds a document's lock can publish a check-in with a wrong `PDM-Revision` trailer (the library never does).
- The hook verifies trailers and changed paths, not the content of the document folder: a lock holder can publish any bytes for the document they hold (as they can in any vault).
- Lock expiry and stale-lock policy do not exist; an admin breaks a lock by hand.
- Access is per repository (three roles), not per document or per project; ECO approval is not part of this slice.
- Client access tokens travel over plain HTTP in the tests (127.0.0.1). A deployment needs TLS in front of Forgejo and the service.
- The service runs one process with a short-lived database connection per request, and serialises pushes and index rebuilds behind one mutex; it is sized for a small team.

## Configuration (environment variables only)

| Variable | Used by | Meaning |
|---|---|---|
| `PDM_SERVER_URL` | client | base URL of the service, e.g. `http://127.0.0.1:8000` |
| `PDM_TOKEN` | client | the user's Forgejo access token (never stored in the working copy; passed to git through the process environment) |
| `PDM_DATABASE_URL` | service | PostgreSQL URL |
| `PDM_FORGEJO_URL`, `PDM_FORGEJO_TOKEN` | service | Forgejo base URL and a service-account token (site admin or repository admin) |
| `PDM_REPO` | service | `owner/name` of the vault repository |
| `PDM_HOOK_SECRET` | service | secret shared with the repository's hook |
| `PDM_MIRROR_DIR` | service | directory for the service's read-only clone |
| `PDM_VAULT_LIB` | service | directory holding the `PDM` package (the FreeCAD tree's `src/Mod/PDM`) |
| `PDM_GIT_URL` | service | optional, the git URL clients are told to use (default `<forgejo>/<repo>.git`) |

Run the service from this directory: `python -m uvicorn pdm_server.app:app --host 127.0.0.1 --port 8000`. Install the hook once per repository: `python install_hook.py <repo>.git <service url> <PDM_HOOK_SECRET> <python>`.

A client opens a vault with `Vault.clone(<server url>, workdir, user)` (a URL instead of a path selects the remote backend) and later `Vault.open(workdir, user)`; the PDM workbench's Connect dialog accepts the URL the same way. With no `PDM_SERVER_URL` and no server working copy, nothing changes from the local vault.

## Tests

The suite is `TestPDMServer` (`src/Mod/PDM/PDMServerTests/`, 32 named scenarios, each driving two or more separate `FreeCADCmd` processes). `stack.py` starts and stops a throwaway PostgreSQL, Forgejo and service on 127.0.0.1 with free ports in a temp directory; nothing is installed as a service. The suite skips when PostgreSQL (Homebrew `postgresql@17`), Forgejo (Homebrew `forgejo`) or the service venv is missing.

```bash
python3 -m venv ~/src/cad-fork/venvs/pdm-server
~/src/cad-fork/venvs/pdm-server/bin/pip install -r tools/pdm-server/requirements.txt
source "$HOME/_agents/repos/app development/cad-fork/harness/env.sh"
FC_SRC=<worktree> harness/build.sh                       # copies src/Mod/PDM into the build tree
"$FC_BUILD_DIR/bin/FreeCADCmd" -t TestPDMServer          # about 2 minutes
python3 -I harness/run_tests.py --suites TestPDMServer --skip-ctest
```

Overrides: `PDM_PG_BIN` (directory with `initdb`, `pg_ctl`, `createdb`), `PDM_FORGEJO` (the binary), `PDM_SERVER_VENV`, `PDM_SERVER_SRC` (this directory, found from the build tree or `FC_SRC` by default). `python3 stack.py up` starts the same stack by hand and prints a token per test user.
