# SPDX-License-Identifier: LGPL-2.1-or-later
"""The decision behind the repository's pre-receive hook: may this user publish these commits?

The hook (``hook/pre-receive``) reads the pushed commits' trailers and changed top-level paths with git and sends
them here. Every vault commit must (a) come from a writer or admin, (b) touch only the folders its event allows
and (c) hold the lock the event needs, so a client that skips the library's own checks still cannot publish an
unlocked change to a document, edit a different document than it names, or force a lifecycle transition the
table does not allow."""

ZERO = "0" * 40
SUFFIX = ".FCStd.d"


def check_push(V, db, mirror_vault, pusher, role, updates):
    """Return None when the push is allowed, else the reason (one line) it is refused."""
    if role not in ("admin", "writer"):
        return f"{pusher} has the {role or 'no'} role on this vault; writing needs writer or admin"
    states = {}  # document -> state at the end of the commits processed so far in this push

    def state_before(doc, base):
        if doc in states:
            return states[doc]
        if base and base != ZERO and mirror_vault is not None:
            recs = mirror_vault._records(doc, ref=base)
            return recs[0]["state"] if recs else None
        return None

    for u in updates:
        ref, old, new = u["ref"], u["old"], u["new"]
        if ref != f"refs/heads/{V.BRANCH}":
            return f"only refs/heads/{V.BRANCH} may be pushed ({ref} refused); locks are held by the PDM server"
        if new == ZERO:
            return "deleting the vault branch is refused"
        if old != ZERO and not u.get("fast_forward", False):
            return "non-fast-forward pushes are refused (the vault history is append-only)"
        for c in u["commits"]:
            meta = c["meta"]
            event, doc = meta.get("Event"), meta.get("Document")
            short = c["sha"][:10]
            if event == "init":
                if old != ZERO or role != "admin":
                    return f"commit {short}: only an admin creates the vault (init event)"
                continue
            try:
                V._check_name(doc)
            except Exception:
                return f"commit {short}: missing or invalid PDM-Document trailer"
            folder = doc + SUFFIX
            changed = set(c["changed"])
            allowed = {folder}
            holds = [doc]
            if event == "add":
                holds = []
            elif event in ("checkin", "relink"):
                pass
            elif event == "move":
                src = meta.get("Moved-From")
                try:
                    V._check_name(src)
                except Exception:
                    return f"commit {short}: move without a valid PDM-Moved-From trailer"
                allowed = {folder, src + SUFFIX}
                holds = [src]
            elif event == "transition":
                allowed, holds = set(), []
                lk = db.lock(doc)
                if lk is not None:
                    return f"commit {short}: {doc} is locked by {lk['holder']}; transitions need the document unlocked"
                before = state_before(doc, old)
                after = meta.get("State")
                if before is None or after not in V.TRANSITIONS.get(before, ()):
                    return (f"commit {short}: illegal transition {before} -> {after} for {doc}; allowed: "
                            f"{', '.join(V.TRANSITIONS.get(before, ())) or 'none'}")
            else:
                return f"commit {short}: unknown PDM-Event {event!r}"
            if event in ("add", "checkin", "relink") and (meta.get("State") != "WIP" or meta.get("Released") != "0"):
                return f"commit {short} ({event} {doc}) must leave the document WIP and unreleased"
            extra = changed - allowed
            if extra:
                return f"commit {short} ({event} {doc}) changes {', '.join(sorted(extra))[:200]}, outside what that event may touch"
            for d in holds:
                lk = db.lock(d)
                if lk is None or lk["holder"] != pusher:
                    who = f"held by {lk['holder']}" if lk else "not held by anyone"
                    return f"commit {short} ({event} {doc}): the lock on {d} is {who}, not by {pusher}"
            if meta.get("State"):
                states[doc] = meta["State"]
    return None
