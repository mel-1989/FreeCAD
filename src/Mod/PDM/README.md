---
tags: [type/method, for/agent, status/live]
---

# PDM: local vault for cad-fork (G03 slice 2)

A library, no server and no GUI: a vault is a bare git repository on disk, each client has a working copy (a git clone) holding documents in the folder format (`doc.saveAsFolder(dir)`, one `<name>.FCStd.d` directory per document). Everything shells out to the `git` binary; there is no other dependency. Usage:

```python
from PDM import vault as V
V.Vault.create("/srv/vault.git")
a = V.Vault.clone("/srv/vault.git", "/home/a/wc", user="alice")
doc.saveAsFolder(a.doc_path("bracket")); a.add_document("bracket")   # A.1, WIP
a.checkout("bracket"); ...edit and doc.save()...; a.checkin("bracket", "longer pad")   # A.2
```

## Vault layout

- `refs/heads/main` holds the history; the tree root has one `<name>.FCStd.d` folder per document. Document names match `[A-Za-z0-9_-]{1,64}`.
- Each revision or lifecycle change is one commit. The subject is the user's message; the author is the user; trailers record `PDM-Document`, `PDM-Revision`, `PDM-State`, `PDM-Event` (`add`, `checkin`, `transition`) and `PDM-Released`. Lifecycle transitions are commits that change no files.
- `refs/locks/<name>` is one lock per document (below). The vault sets `receive.denyNonFastForwards` so `main` can never be rewritten.
- A working copy keeps, per document, the commit its files were taken from (`.git/pdm/base/<name>`). Check-in builds its commit with git plumbing from a temporary index, so it never touches the working copy's own index or other documents.

## Revision scheme

Labels are `<letters>.<number>`, modelled on the major.minor revisions of public PDM products. `add_document` makes `A.1`; each check-in increments the number (`A.2`, `A.3`). A check-in is always WIP. Releasing does not change the label (the released revision stays, say, `A.3`); the next check-in after a release starts the next letter, `B.1`. Letters run A to Z, then AA.

## Locks

- Check-out acquires the lock first, then fetches and copies the then-current revision into the working copy, so a check-in that lands in between is not missed.
- A lock is a commit on an empty tree whose message carries JSON (holder, host, UTC time, `new_revision`, a nonce), pushed to `refs/locks/<name>` without force. Git decides creation inside the vault's ref transaction, so of two racing clients exactly one wins; the other gets `LockError` naming the holder. The check is the same whether the clients run on one machine or share the vault over a network file system that honours git's lock files.
- Release deletes the ref with `--force-with-lease` on the exact value that was read, so a lock that changed hands is never removed by the old owner. `break_lock` is the explicit administrator override.
- The lock lives in the vault, so it survives a client restart. The same user name asking again gets the same lock (idempotent). Identity is the user name given to `Vault`; there is no authentication in this slice.
- `checkin` needs the lock; without it, or with someone else's, it raises `LockError`. `keep_lock=True` keeps the lock after the check-in.

## Integrity

- A check-in from a working copy whose base revision is behind the vault raises `StaleRevisionError` and changes nothing. A push that loses a race against a check-in of another document is retried after re-checking staleness (up to 8 times).
- The vault changes only when the final `git push` succeeds. Any earlier failure leaves the vault at the previous revision and the lock held, and the local edit intact, so the same check-in can be retried. Tests inject a failure with the environment variable `PDM_TEST_FAULT=before_push` or `after_commit`, read only in `vault.py`.
- Check-in refuses when nothing changed.

## Lifecycle

| From | Allowed to |
|---|---|
| WIP | InReview, Obsolete |
| InReview | WIP, Released, Obsolete |
| Released | Obsolete |
| Obsolete | none |

Transitions need the document unlocked (release your own lock first). WIP to Released is refused. Check-out rules: WIP can be checked out; InReview and Obsolete cannot; a Released document needs `checkout(name, new_revision=True)`, and the following check-in is the next letter's `.1`.

## Reading history

`history(name)` lists revisions and lifecycle events (revision, state, author, ISO time, message, commit). `open_revision(name, "A.1")` exports that revision to a temp directory (files read-only on disk, `cleanup_export` removes it) and opens it with `FreeCAD.openDocument`. A revision's change set is `git diff` between two commits of the folder format: a Pad length change touches a few `Document.xml` lines and the recomputed shape files.

## Not in this slice

No server, users or access control, no where-used or BOM, no link relocation (slice 3), no GUI (slice 5). A lock is not time-limited.

## Tests

`FreeCADCmd -t TestPDM` or `python3 -I harness/run_tests.py --suites TestPDM --skip-ctest`. Each client is a separate `FreeCADCmd` process running `PDMTests/client_worker.py`.
