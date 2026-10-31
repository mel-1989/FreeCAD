# SPDX-License-Identifier: LGPL-2.1-or-later
"""Connection settings, the active vault, document-to-vault mapping and the actions the commands share.

Everything that changes the vault is a call into ``PDM.vault.Vault``; this module only finds the vault
and the document, and opens or closes FreeCAD documents around the call."""

import atexit
import os
import time

import FreeCAD as App

from PDM import vault as V

PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/PDM"
_vault = None
_lock_cache = {}
_exports = []
LOCK_TTL = 2.0


# ---- settings and connection ---------------------------------------------------------------------

def settings():
    p = App.ParamGet(PARAM_PATH)
    return {"vault": p.GetString("VaultPath", ""), "workdir": p.GetString("WorkingCopy", ""),
            "user": p.GetString("User", "")}


def save_settings(vault, workdir, user):
    p = App.ParamGet(PARAM_PATH)
    p.SetString("VaultPath", vault)
    p.SetString("WorkingCopy", workdir)
    p.SetString("User", user)


def connect(vault_path, workdir, user, save=True):
    """Use ``workdir`` as this user's working copy of the vault at ``vault_path`` (cloned when ``workdir`` is
    missing or empty). Raises a PDM error on a bad path or user."""
    global _vault
    remote = V._is_url(vault_path)  # a PDM server (slice 4): credentials come from PDM_TOKEN in the environment
    workdir = os.path.abspath(workdir)
    vault_path = vault_path.rstrip("/") if remote else os.path.abspath(vault_path)
    if os.path.isdir(os.path.join(workdir, ".git")):
        v = V.Vault.open(workdir, user)
        same = (getattr(v, "server_url", "").rstrip("/") == vault_path if remote
                else os.path.realpath(v.vault_path) == os.path.realpath(vault_path))
        if not same:
            raise V.PDMError(f"{workdir} is a working copy of {v.vault_path}, not of {vault_path}")
    else:
        if os.path.exists(workdir) and os.listdir(workdir):
            raise V.PDMError(f"{workdir} exists and is not a working copy")
        v = V.Vault.clone(vault_path, workdir, user)
    _vault = v
    _lock_cache.clear()
    if save:
        save_settings(vault_path, workdir, user)
    return v


def disconnect():
    global _vault
    _vault = None
    _lock_cache.clear()


def current_vault():
    """The connected vault, opening the saved working copy on first use; None when not configured."""
    global _vault
    if _vault is None:
        s = settings()
        if s["workdir"] and s["user"] and os.path.isdir(os.path.join(s["workdir"], ".git")):
            try:
                _vault = V.Vault.open(s["workdir"], s["user"])
            except Exception:
                return None
    return _vault


def require_vault():
    v = current_vault()
    if v is None:
        raise V.PDMError("not connected to a vault: use PDM > Connect first")
    return v


# ---- documents ------------------------------------------------------------------------------------

def _real(path):
    return os.path.realpath(path.rstrip("/\\")) if path else ""


def vault_name_of(path, v=None):
    """Vault document name for a file path inside this working copy, else None."""
    v = v or current_vault()
    if v is None or not path:
        return None
    rel = os.path.relpath(_real(path), _real(v.workdir))
    first = rel.split(os.sep)[0]
    if first.startswith("..") or not first.endswith(V.DOC_SUFFIX):
        return None
    return first[:-len(V.DOC_SUFFIX)]


def active_name():
    doc = App.ActiveDocument
    return vault_name_of(doc.FileName) if doc is not None else None


def open_doc_for(name, v=None):
    """The open FreeCAD document whose file is ``name`` in the working copy, or None."""
    v = v or current_vault()
    if v is None:
        return None
    want = _real(v.doc_path(name))
    for d in App.listDocuments().values():
        if _real(d.FileName) == want:
            return d
    return None


def is_modified(doc):
    """True when the document has unsaved changes: the GUI document's Modified flag (cleared by a GUI save),
    or, with no GUI document, the touched state."""
    try:
        import FreeCADGui
        gd = FreeCADGui.getDocument(doc.Name)
    except Exception:  # noqa: BLE001
        gd = None
    return bool(gd.Modified) if gd is not None else bool(doc.isTouched())


def save_document(doc):
    """Save through the GUI document so its Modified flag clears (an App-level save leaves it set)."""
    import FreeCADGui
    gd = FreeCADGui.getDocument(doc.Name)
    if gd is not None and hasattr(gd, "save"):
        gd.save()
    else:
        doc.save()
        if gd is not None:
            gd.Modified = False


def lock_state(name, refresh=False):
    """``{"info": lock dict or None, "holder": str or None, "mine": bool}``. Reads the local mirror of the
    vault's lock refs (cached for LOCK_TTL seconds) unless ``refresh``; it never blocks IsActive on the network."""
    v = current_vault()
    now = time.time()
    hit = _lock_cache.get(name)
    if hit and not refresh and now - hit[0] < LOCK_TTL:
        return hit[1]
    try:
        info = v.lock_info(name, refresh=refresh)
    except Exception:
        info = None
    mine = v.owns_lock(info)
    st = {"info": info, "holder": info["holder"] if info else None, "mine": mine}
    _lock_cache[name] = (now, st)
    return st


def forget_locks():
    _lock_cache.clear()


# ---- actions ---------------------------------------------------------------------------------------

def checkout(name, new_revision=False, discard_local=False):
    """Check ``name`` out (library call) and open it from the working copy. Raises the library's errors.

    ``discard_local`` replaces local edits in the working copy by the current revision (the library's flag)."""
    v = require_vault()
    opened = open_doc_for(name, v)
    was_mine = lock_state(name, refresh=True)["mine"]
    if opened is not None and not discard_local and not was_mine and is_modified(opened):
        raise V.DocumentError(f"{name} is open with unsaved changes; close or save it first")
    path = v.checkout(name, new_revision=new_revision, discard_local=discard_local)
    forget_locks()
    if opened is not None and was_mine and not discard_local:
        return opened  # idempotent re-checkout: keep the open document and its edits
    if opened is not None:
        App.closeDocument(opened.Name)
    return App.openDocument(path)


def checkin(name, message, keep_lock=False):
    """Save the open document when it has unsaved changes, then check it in with ``message``.

    A document that was not modified is not saved: re-saving would only rewrite view state (the camera in
    GuiDocument.xml) and the vault would get a revision with no model change. The returned record has
    ``saved`` set to True when this call saved the document."""
    v = require_vault()
    doc = open_doc_for(name, v)
    saved = False
    if doc is not None and is_modified(doc):
        save_document(doc)
        saved = True
    rec = dict(v.checkin(name, message, keep_lock=keep_lock))
    rec["saved"] = saved
    forget_locks()
    return rec


def transition(name, new_state, message=None):
    v = require_vault()
    rec = v.transition(name, new_state, message or None)
    forget_locks()
    return rec


def open_revision(name, revision):
    """Open one revision read-only (an export in a temp folder) as its own document."""
    v = require_vault()
    path = v.export_revision(name, revision)
    _exports.append(path)
    return App.openDocument(path)


def open_head(name):
    """Open the vault's newest revision of ``name`` read-only, without locking it. The working copy is never
    used for this (it can be behind the vault); a document this client has checked out and open is returned
    as it is."""
    v = require_vault()
    opened = open_doc_for(name, v)
    if opened is not None and lock_state(name, refresh=True)["mine"]:
        return opened
    return open_revision(name, v.history(name)[0]["revision"])


def cleanup_exports():
    for path in _exports:
        for d in list(App.listDocuments().values()):
            if _real(d.FileName) == _real(path):
                App.closeDocument(d.Name)
        V.cleanup_export(path)
    _exports.clear()


atexit.register(cleanup_exports)
