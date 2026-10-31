# SPDX-License-Identifier: LGPL-2.1-or-later
"""The decision behind the repository's pre-receive hook: may this user publish these commits?

The hook (``hook/pre-receive``) reads each pushed commit with git (trailers, parents, author, the changes against
its first parent, the top-level names of its tree) and sends them here. The trailers are only a claim; the rules
check the claim against what the commit really does, so a client that skips the library cannot publish an unlocked
change, overwrite or move onto an existing document, edit a document other than the one it names, slip a change in
through a merge or a symlink, sign as someone else, or force a lifecycle step or revision label the library would
never make."""

ZERO = "0" * 40
BAD_MODES = ("120000", "160000")  # symlinks and gitlinks never belong in a vault


def check_push(V, db, mirror_vault, pusher, role, updates):
    """Return None when the push is allowed, else the reason (one line) it is refused."""
    if role not in ("admin", "writer"):
        return f"{pusher} has the {role or 'no'} role on this vault; writing needs writer or admin"
    suffix = V.DOC_SUFFIX

    for u in updates:
        ref, old, new = u["ref"], u["old"], u["new"]
        if ref != f"refs/heads/{V.BRANCH}":
            return f"only refs/heads/{V.BRANCH} may be pushed ({ref} refused); locks are held by the PDM server"
        if new == ZERO:
            return "deleting the vault branch is refused"
        if old != ZERO and not u.get("fast_forward", False):
            return "non-fast-forward pushes are refused (the vault history is append-only)"
        if old != ZERO and mirror_vault is None:
            return "the service cannot read the vault head, so it cannot check this push; try again"
        docs = set()    # documents in the tree before the commit being checked (case kept)
        last = {}       # document -> latest record {revision, state, released} before this commit
        if old != ZERO and mirror_vault is not None:
            docs = set(mirror_vault._doc_names_at(old))

        def record(doc):
            if doc not in last:
                recs = mirror_vault._records(doc, ref=old) if (mirror_vault is not None and old != ZERO) else []
                last[doc] = ({"revision": recs[0]["revision"], "state": recs[0]["state"],
                              "released": recs[0]["released"]} if recs else None)
            return last[doc]

        for c in u["commits"]:
            meta = c["meta"]
            event, doc = meta.get("Event"), meta.get("Document")
            short = c["sha"][:10]
            if len(c["parents"]) > 1:
                return f"commit {short} is a merge; the vault history is linear and merge commits are refused"
            if not c["parents"]:
                if not (event == "init" and old == ZERO and role == "admin" and not docs):
                    return f"commit {short} is a root commit; only an admin's init commit on an empty vault may be"
            if any(m in BAD_MODES for _s, _p, m in c["changes"]):
                return f"commit {short} adds a symlink or submodule entry, which a vault never holds"
            if event != "init" and (c["author"] != pusher or c["committer"] != pusher):
                return (f"commit {short} is authored by {c['author']!r} / committed by {c['committer']!r}, "
                        f"not by the authenticated pusher {pusher!r}")
            top = set(c["top"])
            after = {n[:-len(suffix)] for n in top if n.endswith(suffix)}
            if event == "init":
                if c["changes"] or after:
                    return f"commit {short}: the init commit must be empty"
                continue
            try:
                V._check_name(doc)
            except Exception:
                return f"commit {short}: missing or invalid PDM-Document trailer"
            folder = doc + suffix
            changed = {p.split("/", 1)[0] for _s, p, _m in c["changes"]}
            allowed = {folder}
            holds = [doc]
            prev = None if event in ("add", "move") else record(doc)
            if event == "add":
                holds = []
                if any(d.lower() == doc.lower() for d in docs):
                    return f"commit {short} (add {doc}): the document already exists in the vault"
                lk = db.lock(doc)
                if lk is not None and lk["holder"] != pusher:
                    return f"commit {short} (add {doc}): {doc} is locked by {lk['holder']}"
                if (meta.get("Revision"), meta.get("State"), meta.get("Released")) != (V.revision_label(0, 1), "WIP", "0"):
                    return f"commit {short} (add {doc}): a new document starts at A.1, WIP, unreleased"
            elif event in ("checkin", "relink"):
                if prev is None or doc not in docs:
                    return f"commit {short} ({event} {doc}): no such document in the vault"
                if (meta.get("State"), meta.get("Released")) != ("WIP", "0"):
                    return f"commit {short} ({event} {doc}) must leave the document WIP and unreleased"
                if meta.get("Revision") != V.next_label(prev):
                    return (f"commit {short} ({event} {doc}): revision {meta.get('Revision')!r} is not the one after "
                            f"{prev['revision']} ({V.next_label(prev)})")
                if doc not in after:
                    return f"commit {short} ({event} {doc}) deletes the document"
            elif event == "move":
                src = meta.get("Moved-From")
                try:
                    V._check_name(src)
                except Exception:
                    return f"commit {short}: move without a valid PDM-Moved-From trailer"
                allowed, holds = {folder, src + suffix}, [src]
                sprev = record(src)
                if src not in docs or sprev is None:
                    return f"commit {short} (move {src} -> {doc}): {src} does not exist"
                if any(d.lower() == doc.lower() for d in docs):
                    return f"commit {short} (move {src} -> {doc}): {doc} already exists and would be overwritten"
                lk = db.lock(doc)
                if lk is not None:
                    return f"commit {short} (move {src} -> {doc}): {doc} is locked by {lk['holder']}"
                if src in after or doc not in after:
                    return f"commit {short} (move {src} -> {doc}) must remove {src} and create {doc}"
                if (meta.get("Revision"), meta.get("State"), meta.get("Released")) != (
                        sprev["revision"], sprev["state"], "1" if sprev["released"] else "0"):
                    return f"commit {short} (move {src} -> {doc}) must keep {src}'s revision, state and release flag"
            elif event == "transition":
                allowed, holds = set(), []
                lk = db.lock(doc)
                if lk is not None:
                    return f"commit {short}: {doc} is locked by {lk['holder']}; transitions need the document unlocked"
                before = prev["state"] if prev else None
                after_state = meta.get("State")
                if before is None or after_state not in V.TRANSITIONS.get(before, ()):
                    return (f"commit {short}: illegal transition {before} -> {after_state} for {doc}; allowed: "
                            f"{', '.join(V.TRANSITIONS.get(before, ())) or 'none'}")
                released = prev["released"] or after_state == "Released"
                if meta.get("Revision") != prev["revision"] or meta.get("Released") != ("1" if released else "0"):
                    return f"commit {short} (transition {doc}): Revision/Released trailers do not match the lifecycle rule"
            else:
                return f"commit {short}: unknown PDM-Event {event!r}"
            extra = changed - allowed
            if extra:
                return (f"commit {short} ({event} {doc}) changes {', '.join(sorted(extra))[:200]}, "
                        "outside what that event may touch")
            for d in holds:
                lk = db.lock(d)
                if lk is None or lk["holder"] != pusher:
                    who = f"held by {lk['holder']}" if lk else "not held by anyone"
                    return f"commit {short} ({event} {doc}): the lock on {d} is {who}, not by {pusher}"
            # the commit is accepted: carry its effect into the state seen by the next commit of the push
            docs = {d for d in docs if d in after} | after
            last[doc] = {"revision": meta.get("Revision"), "state": meta.get("State"),
                         "released": meta.get("Released") == "1"}
            if event == "move":
                last.pop(src, None)
    return None
