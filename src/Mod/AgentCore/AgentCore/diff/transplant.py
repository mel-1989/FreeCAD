# SPDX-License-Identifier: LGPL-2.1-or-later
"""Replay a captured document state into a document, property by property.

This is how a turn that FreeCAD split into several undo entries is flattened
into one, and how a before-state snapshot is brought back in place. It is
not a second undo mechanism: ``capture`` reads FreeCAD's own per-property
serialisation (``dumpPropertyContent``) and ``apply`` writes it back with
``restorePropertyContent`` while one FreeCAD transaction is open, so the
result is a single, ordinary undo entry recorded by ``App::Transaction``.

``apply`` makes the document hold exactly the captured objects:

1. create the missing objects by type and name, in capture order (a
   created ``PartDesign::Body`` makes its own Origin; extra auto-created
   objects are removed with the rest at the end);
2. add or remove dynamic properties so the property sets match;
3. restore every property whose serialised content differs (two passes,
   because restoring one property can trigger ``onChanged`` on another);
4. set expressions with ``setExpression`` (``ExpressionEngine``'s own Restore
   only queues them for ``onDocumentRestored``, so its raw content is not
   replayed);
5. recompute, then remove objects that are not in the capture (dependants
   first), so owners that tear children down on delete see restored inputs.

Properties with ``Transient`` or ``NoPersist`` status are skipped (FreeCAD
does not save them either). XLink properties are compared by their F1 value
rather than their raw bytes, because the raw form stores a file path relative
to the document it was dumped from, which differs for a snapshot file.
"""

from ..state.values import property_value

# Property status integers as returned by getPropertyStatus().
_STATUS_TRANSIENT = {4, 25}
_STATUS_NOPERSIST = {22}
_STATUS_DYNAMIC = 21
_XLINK_PREFIX = "App::PropertyXLink"


class TransplantError(RuntimeError):
    pass


def _status(obj, name):
    try:
        return set(int(s) for s in obj.getPropertyStatus(name) if isinstance(s, int))
    except Exception:
        return set()


def capture(doc):
    """List of ``{name, type, label, dynamic, props}`` for every object."""
    out = []
    for o in doc.Objects:
        props, dyn = {}, []
        for p in o.PropertiesList:
            st = _status(o, p)
            if _STATUS_DYNAMIC in st:
                try:
                    dyn.append({"name": p, "type": o.getTypeIdOfProperty(p), "group": o.getGroupOfProperty(p) or "",
                                "doc": o.getDocumentationOfProperty(p) or ""})
                except Exception:
                    pass
            if st & (_STATUS_TRANSIENT | _STATUS_NOPERSIST):
                continue
            try:
                ptype = o.getTypeIdOfProperty(p)
            except Exception:
                ptype = ""
            try:
                raw = o.dumpPropertyContent(p)
            except Exception:
                continue
            entry = {"type": ptype, "raw": raw}
            if ptype.startswith(_XLINK_PREFIX):
                entry["value"] = property_value(o, p, ptype)
            props[p] = entry
        props.pop("ExpressionEngine", None)  # its Restore defers to onDocumentRestored; replayed below
        try:
            exprs = {str(p): str(e) for p, e in o.ExpressionEngine}
        except Exception:
            exprs = {}
        out.append({"name": o.Name, "type": o.TypeId, "dynamic": dyn, "props": props, "expressions": exprs})
    return out


def _remove(doc, names):
    """Remove objects, dependants first, tolerating cascades."""
    pending = [n for n in names if doc.getObject(n) is not None]
    for _ in range(len(pending) + 1):
        if not pending:
            return
        nxt = []
        for n in pending:
            o = doc.getObject(n)
            if o is None:
                continue
            if any(p.Name in pending for p in o.InList if p.Document == doc and p.Name != n):
                nxt.append(n)
                continue
            doc.removeObject(n)
        if nxt == pending:  # cycle: remove anyway
            for n in nxt:
                if doc.getObject(n) is not None:
                    doc.removeObject(n)
            return
        pending = nxt


def apply(doc, cap, rounds=3):
    """Make ``doc`` hold the captured state. Returns a list of problems.

    The sequence runs again while it still creates or removes objects (a
    delete that cascades to children the capture keeps, for example).
    """
    for _ in range(rounds):
        problems, work = _apply_once(doc, cap)
        if not work:
            break
    return problems


def _apply_once(doc, cap):
    problems = []
    work = 0
    want = [c["name"] for c in cap]
    wanted = set(want)
    for c in cap:
        if doc.getObject(c["name"]) is None:
            o = doc.addObject(c["type"], c["name"])
            work += 1
            if o.Name != c["name"]:
                problems.append(f"{c['name']}: re-created as {o.Name}")
    for c in cap:
        o = doc.getObject(c["name"])
        if o is None:
            continue
        have_dyn = {p for p in o.PropertiesList if _STATUS_DYNAMIC in _status(o, p)}
        want_dyn = {d["name"] for d in c["dynamic"]}
        for p in sorted(have_dyn - want_dyn):
            try:
                o.removeProperty(p)
            except Exception as exc:
                problems.append(f"{c['name']}.{p}: remove failed: {exc}")
        for d in c["dynamic"]:
            if d["name"] not in o.PropertiesList:
                try:
                    o.addProperty(d["type"], d["name"], d["group"], d["doc"])
                except Exception as exc:
                    problems.append(f"{c['name']}.{d['name']}: add failed: {exc}")
    for _pass in range(2):
        changed = 0
        for c in cap:
            o = doc.getObject(c["name"])
            if o is None:
                continue
            for p, entry in c["props"].items():
                if p not in o.PropertiesList:
                    if _pass:
                        problems.append(f"{c['name']}.{p}: property missing")
                    continue
                try:
                    if "value" in entry:
                        if property_value(o, p, entry["type"]) == entry["value"]:
                            continue
                    elif o.dumpPropertyContent(p) == entry["raw"]:
                        continue
                    o.restorePropertyContent(p, entry["raw"])
                    changed += 1
                except Exception as exc:
                    if _pass:
                        problems.append(f"{c['name']}.{p}: restore failed: {exc}")
        if not changed:
            break
    for c in cap:
        o = doc.getObject(c["name"])
        if o is None:
            continue
        try:
            have = {str(p): str(e) for p, e in o.ExpressionEngine}
        except Exception:
            have = {}
        want_e = c.get("expressions", {})
        for path in sorted(set(have) - set(want_e)):
            o.setExpression(path, None)
        for path, expr in sorted(want_e.items()):
            if have.get(path) != expr:
                try:
                    o.setExpression(path, expr)
                except Exception as exc:
                    problems.append(f"{c['name']}.{path}: expression failed: {exc}")
    # Removal last: owners such as a TechDraw page tear their children down
    # on delete, which must see the already-restored inputs.
    doc.recompute()
    extra = [o.Name for o in doc.Objects if o.Name not in wanted]
    _remove(doc, extra)
    work += len(extra)
    return problems, work
