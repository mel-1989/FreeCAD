# SPDX-License-Identifier: LGPL-2.1-or-later
"""Bill-of-materials rows for a weldment document (cad-fork G07 slice 5b, check 5).

This is the contract that G03 slice 6 (the PDM BOM) consumes. ``bom_rows(doc)`` takes an open
FreeCAD document (for a vaulted document: the one opened from the working copy's folder) and
returns a deterministic list of plain dicts, one per BOM line, so a vaulted weldment's BOM can be
read off any checked-out revision without a GUI.

Row schema (every row has exactly these keys, in this order)
------------------------------------------------------------
============== ===========================================================================
``item``       1-based line number in the returned order
``kind``       ``"member"``, ``"plate"`` or ``"weld"``
``description``  human text: profile and size with cut length and end treatment (member),
               plate outline and thickness (plate), ``Fillet weld z=.. a=..`` (weld)
``family``     profile table family (``EN SHS``...), plate family, or ``Fillet weld``
``size``       profile size name; for a weld ``z=<leg> mm`` (leg as ``%.6g``)
``length_mm``  member: cut length of one piece; plate: long side of its outline; weld: total
               weld length of that size (not per bead)
``total_length_mm``  ``quantity * length_mm`` for members and plates; the weld total for welds
``quantity``   number of identical pieces; for a weld the number of bead objects of that size
``mass_each_kg``  mass of one piece (member: table linear mass x cut length); weld: mass of the
               weld metal of that size divided by ``quantity``
``mass_kg``    total mass of the line (``quantity * mass_each_kg``)
``material``   material text of the member (``Material`` property); ``Weld metal`` for welds
``start_angle_deg``, ``end_angle_deg``  members: end angles of the cut list; otherwise ``None``
``document``   the source document's name: the file stem of a saved document (``frame`` for
               ``frame.FCStd.d`` or ``frame.FCStd``, which is its name in a PDM vault), the
               ``Label`` of an unsaved one
============== ===========================================================================

Order: members (the cut list's own order), then plates (cut list order), then welds by ascending
leg size. Numbers are not rounded; two calls on the same document, and calls on a document and its
reopened copy, return equal lists.

Sources: members come from ``WeldCutList.cut_list`` (unchanged; it is called on the chain heads of
the document: every Member or EndTrim that no EndTrim uses as its Base). Welds come from
``WeldBead.weld_summary``.

Plate hook: ``WeldCutList.cut_list`` currently returns member rows only and no ``kind`` field.
When it starts returning plate items (gussets, end caps), give those rows ``"kind": "plate"`` and
the keys read in ``_plate_row`` (``family``, ``size``, ``material``, ``length_mm`` or
``cut_length_mm``, ``quantity``, ``mass_each_kg``, optionally ``description``); they are then
picked up here without changes to this module. Until then no plate rows are produced.

Public sources: none beyond the cut-list and weld definitions cited in ``WeldCutList`` and
``WeldBead``. Clean room: no FrameForge, old Weldment add-on or other weldment workbench source
was opened.
"""

import os

import WeldBead
import WeldCutList
from WeldTrim import is_trim

KEYS = [
    "item", "kind", "description", "family", "size", "length_mm", "total_length_mm", "quantity",
    "mass_each_kg", "mass_kg", "material", "start_angle_deg", "end_angle_deg", "document",
]


def _is_member(obj):
    return getattr(obj, "Proxy", None).__class__.__name__ == "Member"


def chain_heads(doc):
    """Members and EndTrims of the document that no EndTrim builds on (the cut list's inputs)."""
    objs = [o for o in doc.Objects if _is_member(o) or is_trim(o)]
    used = {t.Base.Name for t in objs if is_trim(t) and t.Base is not None}
    return sorted((o for o in objs if o.Name not in used), key=lambda o: o.Name)


def _fmt(v):
    return "none" if v is None else "%.4g" % v


def _member_row(r, doc):
    desc = "%s %s, cut %.3f mm, ends %s/%s" % (r["family"], r["size"], r["cut_length_mm"], r["start_end"], r["end_end"])
    if r["start_angle_deg"] or r["end_angle_deg"]:
        desc += " (%s/%s deg)" % (_fmt(r["start_angle_deg"]), _fmt(r["end_angle_deg"]))
    return {
        "kind": "member", "description": desc, "family": r["family"], "size": r["size"],
        "length_mm": r["cut_length_mm"], "total_length_mm": r["cut_length_mm"] * r["quantity"],
        "quantity": r["quantity"], "mass_each_kg": r["mass_each_kg"], "mass_kg": r["mass_total_kg"],
        "material": r["material"], "start_angle_deg": r["start_angle_deg"], "end_angle_deg": r["end_angle_deg"],
    }


def _plate_row(r, doc):
    """HOOK: used only when the cut list returns rows with kind == 'plate' (see module docstring)."""
    length = r.get("length_mm", r.get("cut_length_mm"))
    each = r["mass_each_kg"]
    return {
        "kind": "plate", "description": r.get("description") or "%s %s" % (r["family"], r["size"]),
        "family": r["family"], "size": r["size"], "length_mm": length,
        "total_length_mm": length * r["quantity"], "quantity": r["quantity"], "mass_each_kg": each,
        "mass_kg": each * r["quantity"], "material": r["material"], "start_angle_deg": None,
        "end_angle_deg": None,
    }


def _weld_row(w):
    return {
        "kind": "weld",
        "description": "Fillet weld z=%.6g mm a=%.6g mm, %d bead(s)" % (w["leg_z_mm"], w["throat_a_mm"], w["bead_count"]),
        "family": "Fillet weld", "size": "z=%.6g mm" % w["leg_z_mm"], "length_mm": w["total_length_mm"],
        "total_length_mm": w["total_length_mm"], "quantity": w["bead_count"],
        "mass_each_kg": w["mass_kg"] / w["bead_count"], "mass_kg": w["mass_kg"], "material": "Weld metal",
        "start_angle_deg": None, "end_angle_deg": None,
    }


def document_name(doc):
    """Stable source name: the saved file's stem (the vault document name), else the Label."""
    fn = (doc.FileName or "").rstrip("/\\")
    if fn:
        base = os.path.basename(fn)
        for suffix in (".FCStd.d", ".FCStd"):
            if base.endswith(suffix):
                return base[: -len(suffix)]
        return base
    return doc.Label


def bom_rows(doc):
    """BOM lines (list of dicts with exactly the keys in ``KEYS``) for a weldment document."""
    heads = chain_heads(doc)
    cut = WeldCutList.cut_list(heads) if heads else []
    parts = []
    for r in cut:
        if r.get("kind", "member") == "plate":
            continue
        parts.append(_member_row(r, doc))
    for r in cut:
        if r.get("kind") == "plate":
            parts.append(_plate_row(r, doc))
    parts.extend(_weld_row(w) for w in WeldBead.weld_summary(doc))
    rows = []
    for n, p in enumerate(parts, 1):
        row = {"item": n}
        row.update(p)
        row["document"] = document_name(doc)
        rows.append({k: row[k] for k in KEYS})
    return rows
