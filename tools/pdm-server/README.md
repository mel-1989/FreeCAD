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
- Other hardening: the vault root may hold only `<valid name>.FCStd.d` folders (no case twins, no stray files); `add` and `move` refuse a name that has history (as the library does); a `move` must keep every file of the source byte for byte, and its `Document.xml` must equal the source's after the library's own `Vault._rewrite_links` (src -> dst), byte for byte; a `relink` changes only `Document.xml`, which must equal the old one after the same function with the rewrite named in its `PDM-Relink` trailer (any other link value, comment or attribute change is refused), and the trailer's `src -> dst` must match a `move` commit earlier in the same push, as `Vault.move` writes them (a move commit, then one relink per referencing document); a `move` of a document whose `Document.xml` `Vault._scan_xlinks` refuses (not UTF-8, a DOCTYPE or ENTITY, not well-formed) must leave that file byte for byte, which is what `Vault._edit_rename` does with it; an `add` must carry a `Document.xml` in its new folder; check-in and relink follow the library's checkout rule (`Vault.checkout_refusal`: InReview and Obsolete are refused, Released needs a new-revision lock); `transition` needs an existing document; the author and committer e-mail must be `<user>@pdm.local` (what the library writes) or the user's Forgejo e-mail. Taking a lock (`POST /locks`) has no lifecycle check on purpose: `Vault.move` must lock documents in any state, and the rule is enforced when a revision is published.
- The hook fails closed: if the service cannot be reached the push is refused, so a service outage can never let an unlocked change in. The tests also check at start-up that the hook is really running.

### Known gaps

- A library-only vault (no server) does not enforce the viewer role: the library checks it, but nothing stops a client that skips the library. On a server vault a viewer is refused by the hook for every push. The one thing a viewer-narrowed writer can still do is release a lock it already holds (`DELETE /locks/<doc>`); releasing one's own lock changes no vault content, so it is left open.
- The hook verifies trailers and changed paths, not the content of the document folder: a lock holder can publish any bytes for the document they hold (as they can in any vault).
- Lock expiry and stale-lock policy do not exist; an admin breaks a lock by hand.
- Forgejo lets a user register someone else's `<name>@pdm.local` address; that only affects how Forgejo displays commit-to-login links, not the policy (which binds author, committer and e-mail to the authenticated pusher).
- Access is per repository, not per document or per project.
- Client access tokens travel over plain HTTP in the tests (127.0.0.1). A deployment needs TLS in front of Forgejo and the service.
- The service runs one process with a short-lived database connection per request, and serialises pushes and index rebuilds behind one mutex; it is sized for a small team.

## ECOs and roles (G03 slice 6a)

An engineering change order is a JSON record in the vault (`plm/eco/ECO-<n>.json` on `main`), not a database row, so the library-only vault supports it too. The rules are one module, `src/Mod/PDM/PDM/eco.py`: the library builds a record with `apply_action`, and this service's pre-receive policy replays the same function on the old record and refuses a push whose record is not exactly its result (so a client that skips the library cannot self-approve, claim a role or edit the history). The service holds no ECO state of its own: `GET /ecos`, `/ecos/{number}` and `/ecos/{number}/history` read its clone of the vault branch, and a restart loses nothing.

- **Commit kinds.** `PDM-Event: eco` (one ECO file, with `PDM-Eco` and `PDM-Eco-Action`) and `roles` (`plm/roles.json`, admin only); the single-document rule for every other kind is untouched. A release is one push holding the ECO commit (state Released) followed by the `transition` commit of each affected document (WIP is taken through InReview), each carrying `PDM-Eco`. The policy keeps the list of documents the ECO commit promised and refuses the push if any is missing, if a transition names a document or revision the ECO does not, or if any ordinary lifecycle, lock or revision rule fails. A plain `transition` to Released (or a `move`) of a document held by a Submitted or Approved ECO is refused, by `Vault.transition` and by the policy; a Draft ECO, including one withdrawn or rejected back to Draft, holds nothing (`eco.HOLDING`), so two Drafts may name one document and the second cannot be submitted while the first is Submitted or Approved.
- **Roles.** viewer, engineer, approver, admin. The host's `reader` is a viewer, `writer` an engineer, `admin` an admin; `plm/roles.json` (admin only) can narrow a writer to viewer or widen one to approver, and never raises anyone above their host permission. `GET /access` returns `{user, base, role}`; `GET /whoami` still returns the host role. An engineer creates, edits, submits and releases; an approver (or admin) approves and rejects, never their own ECO (two-person rule); an admin may pass `override=True` with a comment to act outside the two-person rule or the author-only rule, which is flagged in the history entry and logged in `/audit` as `eco-override`.
- **Audit.** Each ECO step and role change is logged (`eco`, `eco-override`, `roles`) only after the ref update succeeded: the pre-receive approval holds the records (table `pending_notes`, keyed by the pushed head) and the `post-receive` hook (`hook/post-receive`, installed by `install_hook.py` into `hooks/post-receive.d/pdm`) asks the service (`POST /internal/post-receive`) to move them into `audit`. A push that was approved but lost the race on the ref leaves no entry; its held records are dropped after a day. If the post-receive hook is not installed, ECO audit entries do not appear. Known gap: if the server goes down after the pre-receive approval and before the post-receive call, the push lands (the ECO file keeps its own history) but its `audit` entries are never written, and the held records are dropped after a day. A push attempted while the server is already down is refused, so it cannot leave this gap.
- **Numbers and clocks.** A pushed `create` must take exactly the next free number (one above the highest in use at the old head; if that would leave the `ECO-<4 to 8 digits>` format, the lowest free one), so a forged `ECO-99999999` cannot exhaust the numbers. A history entry may not be dated more than 5 minutes ahead of the server clock; the library stamps `max(now, last entry)` so a slightly slow clock never jams an ECO. A document held by an open ECO cannot be moved, and an ECO may only name documents that are in the tree.
- **ECO diff.** `Vault.eco_diff(number)` returns, per affected document, `Vault.diff(name, base, target)` with `base` the document's newest Released revision behind the ECO's target (its first revision if it was never released). The service has no `GET /ecos/{n}/diff`: comparing documents needs FreeCAD, which the service does not run, so viewers fetch the vault and compute it on the client.
- **Create race.** A create that loses the number to a concurrent creator is retried once on the fresh head; a second loss raises an `EcoError` ("lost the race ... retry"). The policy marks that refusal with the code `[eco-number-taken]` (`eco.NUMBER_TAKEN`), the client turns the code into `remote.EcoNumberTaken` (a `PermissionDenied` subclass), and the retry keys on that type, never on the wording.
- **Size.** The hook forwards `plm/` files up to 256 KB; the library refuses to publish an ECO or roles file larger than that (a clear error before pushing), because the policy could not replay a larger one.

## Web UI (G03 slice 6b)

Server-rendered pages under `/web` (the root `/` redirects there), served by the same FastAPI process: `pdm_server/web.py`, Jinja2 templates in `pdm_server/templates/`, static files in `static/` (a small ES module `viewer.js`, and three.js r187 vendored unmodified under `static/vendor/three/` with its MIT `LICENSE` and `VERSION.txt`; no Node toolchain). Pages: the document list (state and lock holder), a document's revision history, a revision (3D model, indented and flat BOM, CSV downloads), the ECO list, and an ECO (state, history, per-document diff, old and new models side by side with added material green and removed material red, the colours and the 0.55 transparency of F3's `AgentCore.gui.overlay`).

- **Login.** The user pastes a Forgejo access token into a form; the service checks it with Forgejo (the identity it already trusts) and keeps it in an in-memory server-side session behind an HttpOnly, SameSite cookie. The token never appears in a URL or a page. Every request re-checks the token and the user's effective role (host permission plus `plm/roles.json`), so a revoked token or removed collaborator loses access at once. The login form and every POST carry a CSRF token, and a POST with a foreign `Origin` is refused. Sessions live in the service process (a restart logs everyone out); the pages need TLS in front of the service like the REST API does.
- **Approve and reject.** Offered only to an approver or admin on a Submitted ECO they did not author, and refused server-side for anyone else (viewer, engineer, author). The click calls `PDM.eco.write` from a per-user working copy of the vault (`<mirror dir>/web/<login>`), pushing with *that user's* token, so the pre-receive hook binds author and committer to them and checks the two-person rule against the pusher; the service token never writes. A refusal by the library or the hook is shown as the error (HTTP 403 for a role or two-person refusal, 409 otherwise).
- **Worker and cache.** The web process never runs FreeCAD. A page that needs a BOM, a glTF model or an ECO diff that is not cached starts `pdm_server/fcworker.py` in a separate `FreeCADCmd` (one at a time, in the background) and shows "pending" with a meta refresh. The worker has its own clone of the vault and calls the library: `Vault.bom`, `PDM.bom.flatten`/`to_csv`, `PDM.eco.eco_diff` (so `Vault.diff` per document) and F3's `shape_diff` for the added and removed pieces; `Import.export` writes the glb after tessellating (without a mesh OCCT writes an empty node). Cache layout under `PDM_CACHE_DIR` (default `<mirror dir>/cache`): `<record sha>/<document>/{bom.json, bom.csv, bom-flat.csv, model.glb, meta.json}` for a revision, and `<sha of the last commit that touched the ECO file>/eco-<number>-<hash of the from/to record shas>/{diff.json, <doc>-old.glb, <doc>-new.glb, <doc>-diff.glb, meta.json}` for an ECO, so an entry is immutable for its key and a changed base revision gets a new key. Entries are built in a temp directory and renamed into place. The cache is never pruned (delete it freely; it is rebuilt on demand).
- **Without the assets.** With `PDM_FREECAD_CMD` unset, no cache directory and no `static/` directory the service starts and the REST API is unchanged; the lists, histories and ECO pages render, and the BOM, 3D and diff parts say they are not built. Without `jinja2` installed the web layer is skipped entirely. `GET /web/status` reports `{static, worker, cache}`.

| Variable | Meaning |
|---|---|
| `PDM_FREECAD_CMD` | path of the `FreeCADCmd` the worker uses (optional) |
| `PDM_CACHE_DIR` | artifact cache (default `<PDM_MIRROR_DIR>/cache`) |
| `PDM_GOLDEN_DIR` or `CADFORK_CTL` | passed through to the worker so `Vault.diff` has I3's shape metrics |
| `PDM_WORKER_TOKEN` | the credential the worker clones the vault with; give it a token of a read-only collaborator, because the worker opens untrusted `.FCStd` files. When unset the worker falls back to the service's owner token and the service logs a warning once |
| `PDM_WEB_MAX_JOBS` | running plus queued worker jobs (default 16); beyond it a page says "busy" and starts nothing |
| `PDM_TRUST_PROXY` | `1` makes the session cookie's `Secure` flag follow `X-Forwarded-Proto`; otherwise that header is ignored (run uvicorn with `--no-proxy-headers` unless a trusted proxy sets it) |

**Hardening notes.** Every `/web` and `/static` response carries `X-Content-Type-Options: nosniff`, and every page a `Content-Security-Policy` (`default-src 'self'`; scripts only from this origin plus a per-response nonce for the three.js import map; no inline styles; `blob:` allowed for images, fetches and workers because the glTF loader uses it). A cache entry counts as built only when its `meta.json` parses and every file it lists exists; otherwise it is removed and rebuilt. `tmp-*` build directories and `job-*` files left by a killed worker are removed when the service starts. A failed worker job shows non-admins one generic line; the traceback is shown to admins only. `POST /web/logout` with a wrong CSRF token is refused with 403 and keeps the session.

**Known gaps.** The CSV downloads are the library's `PDM.bom.to_csv` bytes unchanged, so a material or name starting with `=`, `+`, `-` or `@` is not neutralised against spreadsheet formula injection; the library is the source of truth for those bytes and changing it is a separate decision. Without `PDM_WORKER_TOKEN` the worker holds the owner token (see above).

Tests: `src/Mod/PDM/PDMServerTests/test_web.py` (part of `TestPDMServer`; run alone with `FreeCADCmd -t PDMServerTests.test_web.TestWeb`). It drives Chromium through `browse.py` in the service venv (`pip install -r requirements-test.txt`, then `python -m playwright install chromium-headless-shell`) and skips when playwright, scikit-image or the browser is missing. The three screenshot baselines are in `baselines/`; set `PDM_UPDATE_BASELINES=1` to regenerate them after an intended change to the viewer or the fixture.

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
| `PDM_FREECAD_CMD`, `PDM_CACHE_DIR` | service | optional, the web UI's worker and cache (see Web UI) |

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
