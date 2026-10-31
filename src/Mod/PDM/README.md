---
tags: [type/method, for/agent, status/live]
---

# PDM: local vault for cad-fork (G03 slices 2 and 3)

A library, no server and no GUI: a vault is a bare git repository on disk, each client has a working copy (a git clone) holding documents in the folder format (`doc.saveAsFolder(dir)`, one `<name>.FCStd.d` directory per document). Everything shells out to the `git` binary; there is no other dependency. Usage:

```python
from PDM import vault as V
V.Vault.create("/srv/vault.git")
a = V.Vault.clone("/srv/vault.git", "/home/a/wc", user="alice")
doc.saveAsFolder(a.doc_path("bracket")); a.add_document("bracket")   # A.1, WIP
a.checkout("bracket"); ...edit and doc.save()...; a.checkin("bracket", "longer pad")   # A.2
```

## Vault layout

- `refs/heads/main` holds the history; the tree root has one `<name>.FCStd.d` folder per document. Document names match `[A-Za-z0-9_][A-Za-z0-9_-]{0,63}` (no leading `-`); a name that differs from an existing document only by case is refused, because case-insensitive file systems would alias the two. Messages may not contain lines starting with `PDM-`: those are the vault's own trailers, and history is read only from the trailer block each commit ends with, filtered to the document asked about.
- Each revision or lifecycle change is one commit. The subject is the user's message; the author is the user; trailers record `PDM-Document`, `PDM-Revision`, `PDM-State`, `PDM-Event` (`add`, `checkin`, `transition`) and `PDM-Released`. Lifecycle transitions are commits that change no files.
- `refs/locks/<name>` is one lock per document (below). The vault sets `receive.denyNonFastForwards` so `main` can never be rewritten.
- A working copy keeps, per document, the commit its files were taken from (`.git/pdm/base/<name>`). Check-in builds its commit with git plumbing from a temporary index, so it never touches the working copy's own index or other documents.

## Revision scheme

Labels are `<letters>.<number>`, modelled on the major.minor revisions of public PDM products. `add_document` makes `A.1`; each check-in increments the number (`A.2`, `A.3`). A check-in is always WIP. Releasing does not change the label (the released revision stays, say, `A.3`); the next check-in after a release starts the next letter, `B.1`. Letters run A to Z, then AA.

## Locks

- Check-out acquires the lock first, then fetches and copies the then-current revision into the working copy, so a check-in that lands in between is not missed.
- A lock is a commit on an empty tree whose message carries JSON (holder, host, UTC time, `new_revision`, a nonce), pushed to `refs/locks/<name>` without force. Git decides creation inside the vault's ref transaction, so of two racing clients exactly one wins; the other gets `LockError` naming the holder. The check is the same whether the clients run on one machine or share the vault over a network file system that honours git's lock files.
- Release deletes the ref with `--force-with-lease` on the exact value that was read, so a lock that changed hands is never removed by the old owner. `break_lock` is the explicit administrator override.
- The lock lives in the vault, so it survives a client restart. The owner is the user plus a random id of the working copy (`.git/pdm/client-id`): the same working copy asking again gets the same lock (idempotent), a second working copy of the same user is refused with an error naming the other host and folder. Identity is the user name given to `Vault`; there is no authentication in this slice.
- `checkin` needs the lock; without it, or with someone else's, it raises `LockError`. `keep_lock=True` keeps the lock after the check-in. If the check-in landed but releasing the lock failed, `checkin` still returns the revision, with a `warning` field. `checkout` never replaces working files that have uncommitted edits unless `discard_local=True`.

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

## Where-used and move (slice 3)

`where_used(name)` scans `Document.xml` of every document at the vault head with git alone (no FreeCAD document is opened) and returns `{document, object, property, file}` for each link to `name`. FreeCAD writes every path-carrying link, whatever the property type (`App::Link.LinkedObject` and `PropertyXLink`, `PropertyXLinkSub`, `PropertyXLinkSubList`, and the `XLinks` container of expressions), as an `<XLink file="..." stamp=".." name="..">` element, so one scan covers them all; an `<XLink>` without `file` is an object of the same export and is ignored. The links FreeCAD stores are relative to the linking document's folder, and vault documents sit side by side, so a vault link is exactly `<name>.FCStd.d` (also `./name.FCStd.d`, compared without case). URLs, absolute paths and paths through other directories are not vault links. The test suite checks the result against `App.getLinksTo` and `getDependentDocuments` on the same documents opened in FreeCAD.

`move(old, new)` renames a document and re-points every link to it:

- It takes the locks of `old` and of every referencing document, sorted by name, and refuses with `LockError` naming the holder when any is held by another client; nothing in the vault changes then, and the locks it took are released.
- It builds one commit for `new` (`PDM-Event: move`, `PDM-Moved-From: old`, the revision label and state carried over, so `history(new)` continues the old history) and one commit per referencing document (`PDM-Event: relink`, `PDM-Relink: old -> new`, the next revision number, WIP; the next letter if it was Released), chained, and publishes them with a single push. The vault has the whole move or none of it; a lost race recomputes everything under the locks it holds, and the fault hooks (`before_push`, `after_commit`) leave the vault and the locks as they were.
- Referencing documents that are InReview or Obsolete are refused as for check-out; documents with local edits in this working copy are refused; a link that names the document's folder through any other path (`../x/old.FCStd.d`, an absolute path) is refused rather than left behind, because it cannot be proven to be the same document. The new name must not exist (case-insensitively) and must have no history. The old name stays reserved: its history remains and `add_document` refuses it.
- The rewrite is textual and touches only the `file` attribute of matching `<XLink>` elements. The alternative, opening each referencing document in FreeCAD and re-saving it, would also rewrite touched flags and BREP numbers (run 021, Exhibit 2), recompute, and need the linked document loaded; the textual rewrite leaves every other byte alone, so the diff is the link lines only and the geometry cannot change. The rewrite is checked by rescanning the result for any surviving link to `old`.
- It works on vault objects (`git` plumbing), never on the working copy; clean working-copy folders of relinked documents are refreshed to the new revision and the old folder is dropped. The new folder is not materialised; `checkout(new)` it when needed.
- After a move the old name is gone: `lock`, `checkout`, `checkin` and `transition` on a name that is not in the vault head (moved away, also through a chain `A -> B -> C`, or never added) are refused with a message naming where it moved; `documents()` and `where_used` read the same head tree. `history(new)` and `export_revision(new, rev)` still reach revisions from before the move; the exported folder carries the name the document had then (`old.FCStd.d`).
- A referencing document that was Released gets a new WIP revision (the next letter, `.1`) with the relinked paths; its Released revision stays as it was and still links to the old name, which no longer exists at the head, so opening that Released revision shows broken links. Release the relinked revision again to get a Released revision that links to the new name.
- A link from the moved document to itself is re-pointed in the move commit. Documents whose `Document.xml` cannot be read safely (not well-formed, not UTF-8, or declaring a `DOCTYPE`/`ENTITY`, which FreeCAD never writes) are skipped by `where_used` (listed in the result's `unreadable`) and by `move` (listed in its `unreadable` and `warnings`); `move` refuses while any such document exists, because it might link to the moved document in a form the scan cannot see; `move(..., allow_unreadable=True)` proceeds and lists them in `unreadable` and `warnings`, except when a document's text mentions the moved name, which always refuses. Links written with a numeric character reference (`&#46;`) or a single-quoted attribute are not rewritten: `move` refuses with a message asking to re-save that document with FreeCAD.
- A local folder named like the old document is removed only if it is a clean checkout of it; an untracked or edited one is left in place and reported in `warnings`.

FreeCAD side (checked, no C++ change): for folder documents `PropertyXLink` behaves as for zip files. `saveAs` of an open linked document re-points the open linking documents and their next save records the new relative path; a linked pair moved together on disk keeps resolving; a linked folder moved alone leaves a link that reports itself broken (`LinkedObject` is None, `isValid()` False, state `Invalid`, "Link broken!" on recompute) and is never retargeted to a same-named folder elsewhere. Two upstream behaviours to know, both identical for zip documents: saving the *linking* document to another directory does not rewrite its stored relative paths (they stay relative to the old location), and when the document lives under a symlinked directory (macOS `/var` is `/private/var`) the stored path is computed from the canonical folder of the linking document, giving a `../../..` path, and a linked document may open twice, once per spelling of its path. Keep working copies under real paths; the tests use `os.path.realpath`.

## Not in this slice

No server, users or access control, no BOM, no GUI (slice 5). Where-used reads the head only (no history of links), and absolute-path links are not handled. A lock is not time-limited.

## Tests

`FreeCADCmd -t TestPDM` or `python3 -I harness/run_tests.py --suites TestPDM --skip-ctest`. Each client is a separate `FreeCADCmd` process running `PDMTests/client_worker.py`.
