# SPDX-License-Identifier: LGPL-2.1-or-later
"""The decision behind the repository's pre-receive hook: may this user publish these commits?

The hook (``hook/pre-receive``) reads each pushed commit with git (trailers, parents, author, the changes against
its first parent, the top-level names of its tree) and sends them here. The trailers are only a claim; the rules
check the claim against what the commit really does, so a client that skips the library cannot publish an unlocked
change, overwrite or move onto an existing document, edit a document other than the one it names, slip a change in
through a merge or a symlink, sign as someone else, or force a lifecycle step or revision label the library would
never make."""

import base64
import importlib
import json
import re

ZERO = "0" * 40
BAD_MODES = ("120000", "160000")  # symlinks and gitlinks never belong in a vault


def rewrite_mismatch(V, c, old, new, need_change, unreadable_stays=False):
    """None when the commit's new Document.xml is, byte for byte, the old one after the library's own link rewrite
    ``old`` -> ``new`` (``Vault._rewrite_links``, the function ``Vault.move`` uses); otherwise why not. With
    ``unreadable_stays`` (a move) a Document.xml that ``Vault._scan_xlinks`` refuses (not UTF-8, a DOCTYPE or ENTITY,
    not well-formed) must stay byte for byte, which is what ``Vault._edit_rename`` does with it."""
    for n in (old, new):
        try:
            V._check_name(n)
        except Exception:
            return f"{n!r} is not a document name"
    pair = c.get("xml_pair")
    if not pair:
        return "Document.xml could not be read"
    before, after = base64.b64decode(pair[0]), base64.b64decode(pair[1])
    try:
        if unreadable_stays:
            try:
                V.Vault._scan_xlinks(before)
            except V.DocumentError:
                return None if before == after else (
                    f"Document.xml cannot be scanned for links, so a move must leave it unchanged ({old} -> {new})")
        expected, count = V.Vault._rewrite_links(V.Vault, before, old, new)
    except Exception as e:
        return f"Document.xml cannot be rewritten ({e})"
    if need_change and count == 0:
        return f"the old Document.xml has no link to {old} to rewrite"
    if expected != after:
        return f"Document.xml differs from the old one by more than the link rewrite {old} -> {new}"
    return None


def _blob(c, path, i):
    pair = (c.get("blobs") or {}).get(path)
    if not pair or pair[i] is None:
        return None
    return base64.b64decode(pair[i])


def check_push(V, db, mirror_vault, pusher, role, updates, pinned=None, emails=(), notes=None):
    """Return None when the push is allowed, else the reason (one line) it is refused. ``notes``, if given, collects
    ``(action, document, detail)`` audit records for the pushes it allows (ECO steps, role changes, admin overrides)."""
    if role not in ("admin", "writer"):
        return f"{pusher} has the {role or 'no'} role on this vault; writing needs writer or admin"
    E = importlib.import_module(V.__name__.rsplit(".", 1)[0] + ".eco")
    notes = notes if notes is not None else []
    suffix = V.DOC_SUFFIX

    for u in updates:
        ref, old, new = u["ref"], u["old"], u["new"]
        if ref != f"refs/heads/{V.BRANCH}":
            return f"only refs/heads/{V.BRANCH} may be pushed ({ref} refused); locks are held by the PDM server"
        if new == ZERO:
            return "deleting the vault branch is refused"
        if pinned is not None and old != pinned:
            return (f"the push expects the branch at {old[:10]} but the last approved head is {pinned[:10]}; "
                    "creating or resetting the vault branch is refused once the vault exists")
        if not u["commits"]:
            return "the push adds no new commits"
        if old != ZERO and not u.get("fast_forward", False):
            return "non-fast-forward pushes are refused (the vault history is append-only)"
        if old != ZERO and mirror_vault is None:
            return "the service cannot read the vault head, so it cannot check this push; try again"
        grants = E.read_roles(mirror_vault, old) if (mirror_vault is not None and old != ZERO) else {}
        eff = E.effective_role(role, grants, pusher)  # viewer, engineer, approver or admin
        if eff == "viewer":
            return f"{pusher} is a viewer on this vault; a viewer reads and changes nothing"
        ecos = None     # number -> ECO record at the head being checked, kept current across the commits of this push
        pending = {}    # ECO number -> {document: target revision} still to be released by later commits of this push
        docs = set()    # documents in the tree before the commit being checked (case kept)
        last = {}       # document -> latest record {revision, state, released} before this commit
        if old != ZERO and mirror_vault is not None:
            docs = set(mirror_vault._doc_names_at(old))

        moves = set()    # (src, dst) pairs of the move commits accepted earlier in this push
        touched = set()  # names added or moved to earlier in this push (they have history from then on)

        def has_history(doc):
            if any(t.lower() == doc.lower() for t in touched):
                return True
            return mirror_vault is not None and old != ZERO and bool(mirror_vault._records(doc, ref=old))

        def all_ecos():
            nonlocal ecos
            if ecos is None:
                ecos = E.read_all(mirror_vault, old) if (mirror_vault is not None and old != ZERO) else {}
            return ecos

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
            if any(ch[2] in BAD_MODES for ch in c["changes"]):
                return f"commit {short} adds a symlink or submodule entry, which a vault never holds"
            if event != "init" and (c["author"] != pusher or c["committer"] != pusher):
                return (f"commit {short} is authored by {c['author']!r} / committed by {c['committer']!r}, "
                        f"not by the authenticated pusher {pusher!r}")
            top = set(c["top"])
            after = {n[:-len(suffix)] for n in top if n.endswith(suffix)}
            if event != "init":
                lowered = [d.lower() for d in after]
                if len(set(lowered)) != len(lowered):
                    return f"commit {short}: two documents differ only by case"
                for n in top:
                    ok = n.endswith(suffix) or n == "plm"
                    if ok and n != "plm":
                        try:
                            V._check_name(n[:-len(suffix)])
                        except Exception:
                            ok = False
                    if not ok:
                        return f"commit {short}: the vault root may hold only <name>{suffix} folders and plm/, not {n[:60]!r}"
            if c.get("author_email") is not None and event != "init" and (
                    c["author_email"] not in emails or c["committer_email"] not in emails):
                return f"commit {short}: author or committer e-mail is not one of {pusher}'s ({', '.join(sorted(emails))})"
            if event == "init":
                if c["changes"] or after:
                    return f"commit {short}: the init commit must be empty"
                continue
            if event in ("eco", "roles"):
                bad = eco_commit(V, E, db, c, event, meta, short, pusher, eff, all_ecos(), pending, record, notes, docs)
                if bad:
                    return bad
                continue
            try:
                V._check_name(doc)
            except Exception:
                return f"commit {short}: missing or invalid PDM-Document trailer"
            folder = doc + suffix
            changed = {ch[1].split("/", 1)[0] for ch in c["changes"]}
            allowed = {folder}
            holds = [doc]
            prev = None if event in ("add", "move") else record(doc)
            if event == "add":
                holds = []
                if any(d.lower() == doc.lower() for d in docs):
                    return f"commit {short} (add {doc}): the document already exists in the vault"
                if has_history(doc):
                    return f"commit {short} (add {doc}): {doc} has history in the vault (moved away?) and cannot be re-added"
                lk = db.lock(doc)
                if lk is not None and lk["holder"] != pusher:
                    return f"commit {short} (add {doc}): {doc} is locked by {lk['holder']}"
                if (meta.get("Revision"), meta.get("State"), meta.get("Released")) != (V.revision_label(0, 1), "WIP", "0"):
                    return f"commit {short} (add {doc}): a new document starts at A.1, WIP, unreleased"
                if not any(ch[1] == f"{folder}/Document.xml" and ch[0] != "D" for ch in c["changes"]):
                    return f"commit {short} (add {doc}): the new folder has no Document.xml"
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
                lk0 = db.lock(doc)
                new_rev = True if event == "relink" else bool(lk0 and lk0.get("new_revision"))
                why = V.Vault.checkout_refusal(doc, prev, new_rev)
                if why:
                    return f"commit {short} ({event} {doc}): {why}"
                if event == "relink":
                    if [ch[1] for ch in c["changes"]] != [f"{folder}/Document.xml"]:
                        return f"commit {short} (relink {doc}) may change only its Document.xml"
                    m = re.fullmatch(r"(\S+) -> (\S+)", meta.get("Relink", ""))
                    if not m:
                        return f"commit {short} (relink {doc}): missing PDM-Relink trailer"
                    bad = rewrite_mismatch(V, c, m.group(1), m.group(2), need_change=True)
                    if bad:
                        return f"commit {short} (relink {doc}): {bad}"
                    if (m.group(1), m.group(2)) not in moves:
                        return (f"commit {short} (relink {doc}): {m.group(1)} -> {m.group(2)} does not match a move "
                                "commit earlier in this push")
            elif event == "move":
                src = meta.get("Moved-From")
                try:
                    V._check_name(src)
                except Exception:
                    return f"commit {short}: move without a valid PDM-Moved-From trailer"
                held = E.open_docs(all_ecos()).get(src)
                if held:
                    return (f"commit {short} (move {src} -> {doc}): {src} is part of the open {held}; "
                            "edit the ECO to drop the document, or release it, before renaming it")
                allowed, holds = {folder, src + suffix}, [src]
                sprev = record(src)
                if src not in docs or sprev is None:
                    return f"commit {short} (move {src} -> {doc}): {src} does not exist"
                if any(d.lower() == doc.lower() for d in docs):
                    return f"commit {short} (move {src} -> {doc}): {doc} already exists and would be overwritten"
                if has_history(doc):
                    return f"commit {short} (move {src} -> {doc}): {doc} has history in the vault and cannot be reused"
                lk = db.lock(doc)
                if lk is not None:
                    return f"commit {short} (move {src} -> {doc}): {doc} is locked by {lk['holder']}"
                if src in after or doc not in after:
                    return f"commit {short} (move {src} -> {doc}) must remove {src} and create {doc}"
                if c.get("move_equal") is not True:
                    return (f"commit {short} (move {src} -> {doc}): the new folder must equal {src}'s, file for file, "
                            "apart from Document.xml")
                bad = rewrite_mismatch(V, c, src, doc, need_change=False, unreadable_stays=True)
                if bad:
                    return f"commit {short} (move {src} -> {doc}): {bad}"
                if (meta.get("Revision"), meta.get("State"), meta.get("Released")) != (
                        sprev["revision"], sprev["state"], "1" if sprev["released"] else "0"):
                    return f"commit {short} (move {src} -> {doc}) must keep {src}'s revision, state and release flag"
            elif event == "transition":
                allowed, holds = set(), []
                if doc not in docs:
                    return f"commit {short} (transition {doc}): no such document in the vault"
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
                eco_no = meta.get("Eco")
                if eco_no:
                    todo = pending.get(eco_no)
                    if todo is None or doc not in todo or todo[doc] != prev["revision"]:
                        return (f"commit {short} (transition {doc}): {eco_no} does not release {doc} at "
                                f"{prev['revision']} in this push")
                    if after_state == "Released":
                        del todo[doc]
                        if not todo:
                            del pending[eco_no]
                elif after_state == "Released":
                    held = E.open_docs(all_ecos()).get(doc) or next((n for n, t in pending.items() if doc in t), None)
                    if held:
                        return (f"commit {short} (transition {doc}): {doc} is part of the open {held}; "
                                "it is released by releasing the ECO")
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
            if event in ("add", "move"):
                touched.add(doc)
            if event == "move":
                touched.add(src)
                moves.add((src, doc))
            docs = {d for d in docs if d in after} | after
            last[doc] = {"revision": meta.get("Revision"), "state": meta.get("State"),
                         "released": meta.get("Released") == "1"}
            if event == "move":
                last.pop(src, None)
        if pending:
            n0 = sorted(pending)[0]
            return (f"the push releases {n0} but not every affected document ({', '.join(sorted(pending[n0]))} "
                    "left); an ECO release is all of its documents or nothing")
    return None


def eco_commit(V, E, db, c, event, meta, short, pusher, eff, ecos, pending, record, notes, present):
    """Check one ``eco`` or ``roles`` commit against what it really changes; None when it is allowed. Updates ``ecos``
    and ``pending`` (the release chain the following ``transition`` commits must complete)."""
    changes = c["changes"]
    if event == "roles":
        if [ch[1] for ch in changes] != [E.ROLES_PATH] or changes[0][2] != "100644":
            return f"commit {short} (roles) may change only {E.ROLES_PATH}"
        if eff != "admin":
            return f"commit {short}: {pusher} is {eff}; changing roles needs admin"
        raw = _blob(c, E.ROLES_PATH, 1)
        try:
            new = E.parse_roles(raw)
            old = E.parse_roles(_blob(c, E.ROLES_PATH, 0)) if _blob(c, E.ROLES_PATH, 0) is not None else {}
        except Exception as e:
            return f"commit {short} (roles): {e}"
        user, role = meta.get("Roles-User", ""), meta.get("Roles-Role", "")
        want = dict(old)
        if role:
            want[user] = role
        else:
            want.pop(user, None)
        if want != new or new == old:
            return f"commit {short} (roles): the file is not the old one with the single change {user} -> {role or 'default'}"
        notes.append(("roles", None, {"actor": pusher, "user": user, "role": role or None}))
        return None
    number, action = meta.get("Eco", ""), meta.get("Eco-Action", "")
    if not re.fullmatch(E.NUM_RE, number):
        return f"commit {short}: missing or invalid PDM-Eco trailer"
    path = f"{E.ECO_DIR}{number}.json"
    if [ch[1] for ch in changes] != [path] or changes[0][2] != "100644":
        return f"commit {short} (eco {number}) may change only {path}"
    try:
        raw_old, raw_new = _blob(c, path, 0), _blob(c, path, 1)
        if raw_new is None:
            return f"commit {short} (eco {number}): the record could not be read"
        old = json.loads(raw_old) if raw_old is not None else None
        new = json.loads(raw_new)
    except ValueError:
        return f"commit {short} (eco {number}): the record is not valid JSON"
    if (changes[0][0] == "A") != (old is None) or old != ecos.get(number) or new.get("number") != number:
        return f"commit {short} (eco {number}): the record's base is not the vault's current one"
    bad, act, entry = E.verify_change(old, new, pusher, eff, ecos, E.latest_at())
    if bad:
        return f"commit {short} (eco {number}): {bad}"
    if act != action:
        return f"commit {short} (eco {number}): PDM-Eco-Action {action!r} is not the {act!r} the record records"
    names = [d["name"] for d in new["documents"]]
    try:
        E.check_context(act, new, {n: record(n) for n in names}, {n: db.lock(n) for n in names},
                        E.open_docs(ecos, exclude=number), present)
    except V.PDMError as e:
        return f"commit {short} (eco {number}): {e}"
    if act == "release":
        pending[number] = {d["name"]: d["revision"] for d in new["documents"]}
    ecos[number] = new
    detail = {"actor": pusher, "role": eff, "action": act, "to": new["state"], "override": entry["override"]}
    notes.append(("eco-override" if entry["override"] else "eco", number, dict(detail, comment=entry["comment"])))
    return None
