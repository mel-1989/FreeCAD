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
    vault_path, workdir = os.path.abspath(vault_path), os.path.abspath(workdir)
    if os.path.isdir(os.path.join(workdir, ".git")):
        v = V.Vault.open(workdir, user)
        if os.path.realpath(v.vault_path) != os.path.realpath(vault_path):
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
    """True when the document has unsaved changes (the GUI document's flag, or touched objects)."""
    try:
        import FreeCADGui
        gd = FreeCADGui.getDocument(doc.Name)
        if gd is not None and gd.Modified:
            return True
    except Exception:  # noqa: BLE001  (no GUI document: fall back to the touched state)
        pass
    return bool(doc.isTouched())


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
    mine = bool(info) and info.get("holder") == v.user and info.get("client") == v.client_id()
    st = {"info": info, "holder": info["holder"] if info else None, "mine": mine}
    _lock_cache[name] = (now, st)
    return st


def forget_locks():
    _lock_cache.clear()


# ---- actions ---------------------------------------------------------------------------------------

def checkout(name, new_revision=False):
    """Check ``name`` out (library call) and open it from the working copy. Raises the library's errors."""
    v = require_vault()
    opened = open_doc_for(name, v)
    if opened is not None and is_modified(opened) and not lock_state(name, refresh=True)["mine"]:
        raise V.DocumentError(f"{name} is open with unsaved changes; close or save it first")
    was_mine = lock_state(name, refresh=True)["mine"]
    path = v.checkout(name, new_revision=new_revision)
    forget_locks()
    if opened is not None and was_mine:
        return opened  # idempotent re-checkout: keep the open document and its edits
    if opened is not None:
        App.closeDocument(opened.Name)
    return App.openDocument(path)


def checkin(name, message, keep_lock=False):
    """Save the open document (when modified) and check it in with ``message``."""
    v = require_vault()
    doc = open_doc_for(name, v)
    if doc is not None and is_modified(doc):
        doc.save()
    rec = v.checkin(name, message, keep_lock=keep_lock)
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
    """Open ``name`` without locking it: the working copy's folder when it exists, else the head revision
    read-only."""
    v = require_vault()
    opened = open_doc_for(name, v)
    if opened is not None:
        return opened
    if os.path.isfile(os.path.join(v.doc_path(name), "Document.xml")):
        return App.openDocument(v.doc_path(name))
    return open_revision(name, v.history(name)[0]["revision"])


def cleanup_exports():
    for path in _exports:
        for d in list(App.listDocuments().values()):
            if _real(d.FileName) == _real(path):
                App.closeDocument(d.Name)
        V.cleanup_export(path)
    _exports.clear()


atexit.register(cleanup_exports)
