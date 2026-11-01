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

Failed features: ``bom_rows`` (like ``WeldCutList.cut_list`` and ``WeldBead.weld_summary``) raises
``WeldmentError`` naming every invalid member, trim, gusset, end cap or bead (State holds Invalid or
Error) instead of returning rows that omit the part or carry its stale shape.

Reopened documents: a document reopened from a vault gives the same rows as the original in text,
counts and keys exactly and in every number within 1e-6 (mm, degrees, kg), because shapes reload
from BREP with last-digit noise.

Order: members (the cut list's own order), then plates (cut list order), then welds by ascending
leg size. Numbers are not rounded; two calls on the same document, and calls on a document and its
reopened copy, return equal lists.

Sources: members come from ``WeldCutList.cut_list`` (unchanged; it is called on the chain heads of
the document: every Member or EndTrim that no EndTrim uses as its Base). Welds come from
``WeldBead.weld_summary``.

Plates: gussets and end caps (``WeldGusset``, ``WeldEndCap``) come from ``WeldCutList.cut_list``
plate rows (``kind == "plate"``). A plate line has ``family`` = the plate type (``Gusset`` or
``End cap``), ``size`` = ``t=<thickness> mm``, ``length_mm`` = the long side of its outline,
``description`` = ``<type> plate t=.. mm, outline L x W mm``, ``material`` from the plate's Material
property and mass from its Density property (kg/m^3, default 7850).

Public sources: none beyond the cut-list and weld definitions cited in ``WeldCutList`` and
``WeldBead``. Clean room: no FrameForge, old Weldment add-on or other weldment workbench source
was opened.
"""

import os

import WeldBead
import WeldCutList
from WeldGeom import refuse_invalid
from WeldTrim import is_trim

KEYS = [
    "item", "kind", "description", "family", "size", "length_mm", "total_length_mm", "quantity",
    "mass_each_kg", "mass_kg", "material", "start_angle_deg", "end_angle_deg", "document",
]


def _is_member(obj):
    return getattr(obj, "Proxy", None).__class__.__name__ == "Member"


def _is_plate(obj):
    return WeldCutList.is_plate(obj)


def chain_heads(doc):
    """Members and EndTrims of the document that no EndTrim builds on, and its gussets and end caps
    (the cut list's inputs)."""
    objs = [o for o in doc.Objects if _is_member(o) or is_trim(o)]
    used = {t.Base.Name for t in objs if is_trim(t) and t.Base is not None}
    plates = [o for o in doc.Objects if _is_plate(o)]
    return sorted([o for o in objs if o.Name not in used] + plates, key=lambda o: o.Name)


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
    """A plate line from a cut-list plate row (see module docstring)."""
    length = r["length_mm"]
    each = r["mass_each_kg"]
    return {
        "kind": "plate",
        "description": "%s plate t=%.6g mm, outline %.3f x %.3f mm" % (r["type"], r["thickness_mm"], r["length_mm"], r["width_mm"]),
        "family": r["type"], "size": "t=%.6g mm" % r["thickness_mm"], "length_mm": length,
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
    """BOM lines (list of dicts with exactly the keys in ``KEYS``) for a weldment document.

    Raises ``WeldmentError`` naming the features if any member, trim, gusset, end cap or weld bead
    of the document is invalid (State holds Invalid or Error): a BOM built around a failed feature
    would show stale data or lack the part."""
    refuse_invalid([o for o in doc.Objects if _is_member(o) or is_trim(o) or _is_plate(o) or WeldBead.is_bead(o)],
                   "BOM")
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
