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
- Each pushed commit is read with git (trailers, parents, author, the changes against its first parent, the tree's top-level names) and checked against what it really does. Merge and root commits are refused (the vault history is linear; only an admin's empty `init` commit may be a root), as are symlinks and submodule entries, and a commit whose author or committer is not the authenticated pusher.
- `checkin` and `relink` need an existing document, the pusher's lock, may change only that document's folder, must leave it WIP and unreleased, and must carry the label `Vault`'s own rule gives next (`next_label`). `add` needs a name that does not exist (case-insensitively) and is not locked by someone else, and starts at A.1. `move` needs the lock on the old name, a destination that does not exist and is not locked, the source removed and the destination created in the same commit, and the source's revision, state and release flag. `transition` must change no files, needs the document unlocked, must be a step of `Vault.TRANSITIONS` and must carry the unchanged revision and the release flag the rule gives. `init` is for an admin on an empty repository.
- **The service pins the vault head.** Forgejo's branch rename and delete API calls run no hook and branch protection did not stop a writer from renaming `main` (checked on Forgejo 17.0.1), so a writer could hide `main` and push a chosen commit as a fresh `main`, where the hook sees an empty vault. The service therefore records the head it last approved (PostgreSQL `head_pin`). Once the vault exists it refuses any push whose old value is not that head (so creating or resetting `main` is refused), any push that adds no commits, and any push while its own clone cannot read `main` or `main` no longer descends from the pinned head. A renamed branch therefore stops all writes until an admin repairs it (see Recovery); nothing can be published meanwhile. Only an admin's `init` push onto an empty repository can start a vault, and only while the service has never approved one.
- Other hardening: the vault root may hold only `<valid name>.FCStd.d` folders (no case twins, no stray files); `add` and `move` refuse a name that has history (as the library does); a `move` must keep every file of the source byte for byte, and its `Document.xml` must equal the source's after the library's own `Vault._rewrite_links` (src -> dst), byte for byte; a `relink` changes only `Document.xml`, which must equal the old one after the same function with the rewrite named in its `PDM-Relink` trailer (any other link value, comment or attribute change is refused); check-in and relink follow the library's checkout rule (`Vault.checkout_refusal`: InReview and Obsolete are refused, Released needs a new-revision lock); `transition` needs an existing document; the author and committer e-mail must be `<user>@pdm.local` (what the library writes) or the user's Forgejo e-mail. Taking a lock (`POST /locks`) has no lifecycle check on purpose: `Vault.move` must lock documents in any state, and the rule is enforced when a revision is published.
- The hook fails closed: if the service cannot be reached the push is refused, so a service outage can never let an unlocked change in. The tests also check at start-up that the hook is really running.

### Known gaps

- The hook verifies trailers and changed paths, not the content of the document folder: a lock holder can publish any bytes for the document they hold (as they can in any vault).
- Lock expiry and stale-lock policy do not exist; an admin breaks a lock by hand.
- Forgejo lets a user register someone else's `<name>@pdm.local` address; that only affects how Forgejo displays commit-to-login links, not the policy (which binds author, committer and e-mail to the authenticated pusher).
- Access is per repository (three roles), not per document or per project; ECO approval is not part of this slice.
- Client access tokens travel over plain HTTP in the tests (127.0.0.1). A deployment needs TLS in front of Forgejo and the service.
- The service runs one process with a short-lived database connection per request, and serialises pushes and index rebuilds behind one mutex; it is sized for a small team.

## Recovery (administrator)

If the vault branch was renamed, deleted or reset (pushes then fail with "an admin must recover it"): restore `main` in Forgejo to the commit you trust (rename the branch back in the repository settings, or push the trusted commit with the repository owner's token), check that the head is right with `python -m pdm_server.admin show` (it prints the last approved head), and if the trusted head is not that one or descends from it, run `python -m pdm_server.admin pin <sha>` with the service's environment. The `pin` is recorded in the audit log. While `main` is renamed away every client operation fails (and every push is refused) until it is renamed back; the pin holds meanwhile, so nothing is lost or bypassed. Do not let writers be repository admins in Forgejo; writers can still rename branches through the API, which the pin turns into a stop rather than a bypass.

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
