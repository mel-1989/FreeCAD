# SPDX-License-Identifier: LGPL-2.1-or-later
"""G03 slice 6b: push the engineering BOM of a Released revision to Odoo Community MRP over XML-RPC.

``sync_bom(vault, name, revision, url, db, user, key)`` reads the BOM with ``Vault.bom`` (slice 6a, weldment
cut-list rows included; there is no second BOM walk) and makes Odoo's ``product.product``, ``mrp.bom`` and
``mrp.bom.line`` records match it. Standard library only (``xmlrpc.client``); Odoo's PLM module is Enterprise-only
and is not used. Field names are those of Odoo Community 17 and 18 (``mrp.bom``: ``code``, ``product_tmpl_id``,
``product_qty``, ``type``, ``active``, ``bom_line_ids``; ``mrp.bom.line``: ``bom_id``, ``product_id``,
``product_qty``; ``product.product``: ``default_code``, ``name``, ``description``); the units of measure are left to
Odoo's default (Units), so quantities are exact counts.

Keys (what makes a second sync find the same records)
-----------------------------------------------------
* A product is found by ``default_code`` = the part key: the BOM row's name when it is the vault document's own
  name (``bracket``), else ``<document>:<name>`` (``machine:Plate``, and for a weldment's members, plates and
  welds ``frame:<cut-list description>``). The key does not contain the revision, so one product serves every
  revision of a part. The product's ``name`` is the row name and its ``description`` carries the cut-list text,
  material, profile family and size, length and mass of a weldment row.
* A BOM is found by ``code`` = ``<part key> <revision>`` on the product's template. Every row that has children
  (a sub-assembly, the weldment itself) gets its own BOM, so the BOM is multi-level; the choice is "own BOM, type
  normal", not a phantom kit, so Odoo can plan and cost each sub-assembly. Leaf parts, members, plates and welds
  are products without a BOM.
* A BOM's lines are its children rows: product and exact quantity per parent (identical children are already
  counted by ``Vault.bom``).

Versioning and idempotence
--------------------------
Existing records are read first and written only when a field differs, so syncing the same revision twice makes
no create or write call after the reads. Syncing makes the synced revision's BOM the only active one for each
product it builds a BOM for: other BOMs of that product (older or newer revisions) are archived with
``active=False``, never deleted, and a previously archived BOM with the same code is reactivated.

Credentials come from the arguments only (``credentials_from_env`` is a convenience for ``PDM_ODOO_*``) and are
never put in a message. A live Odoo is optional and never on a public host (gate D11).
"""

import os
import xmlrpc.client

from PDM import vault as V

RELEASED = "Released"


class ErpError(V.PDMError):
    """The sync could not run or the Odoo side refused it; nothing partial is hidden (see the message)."""


def credentials_from_env():
    """(url, db, user, key) from PDM_ODOO_URL, PDM_ODOO_DB, PDM_ODOO_USER, PDM_ODOO_KEY."""
    try:
        return tuple(os.environ[f"PDM_ODOO_{k}"] for k in ("URL", "DB", "USER", "KEY"))
    except KeyError as e:
        raise ErpError(f"{e.args[0]} is not set") from None


def part_key(row):
    n, d = row["name"], row["document"]
    return n if n == d or n.startswith(d + ":") else f"{d}:{n}"


def bom_code(row):
    return f"{part_key(row)} {row['revision']}"


def _description(row):
    ex = row.get("extra") or {}
    bits = [row["name"]]
    if row.get("material"):
        bits.append(f"material {row['material']}")
    if ex:
        bits.append(f"{row['kind']}; family {ex['family']}; size {ex['size']}; length {ex['length_mm']:.3f} mm; "
                    f"mass {ex['mass_kg']:.6g} kg")
    return "; ".join(bits)


def plan(rows):
    """(products, boms) wanted in Odoo for BOM ``rows``: ``products``: key -> {name, description}; ``boms``: list of
    {key, code, lines: [(child key, quantity)]} in tree order, one per row with children (first occurrence wins; a
    second occurrence must agree)."""
    products, boms, seen = {}, [], {}
    stack = []
    for i, r in enumerate(rows):
        key = part_key(r)
        want = {"name": r["name"], "description": _description(r)}
        if products.setdefault(key, want) != want:
            raise ErpError(f"two different rows share the product key {key!r}: {products[key]['description']!r} and {want['description']!r}")
        while len(stack) > r["level"]:
            stack.pop()
        if stack:
            stack[-1]["lines"].append((key, r["quantity"]))
        if i + 1 < len(rows) and rows[i + 1]["level"] > r["level"]:
            b = {"key": key, "code": bom_code(r), "lines": []}
            stack.append(b)
            boms.append(b)
    out = []
    for b in boms:
        prev = seen.get((b["key"], b["code"]))
        if prev is None:
            seen[(b["key"], b["code"])] = b
            out.append(b)
        elif sorted(prev["lines"]) != sorted(b["lines"]):
            raise ErpError(f"{b['code']} appears twice in the BOM with different contents")
    return products, out


class _Odoo:
    def __init__(self, url, db, user, key):
        self.db, self.user, self._key = db, user, key
        base = url.rstrip("/")
        self.where = base.split("@")[-1]
        try:
            self.common = xmlrpc.client.ServerProxy(base + "/xmlrpc/2/common", allow_none=True)
            self.uid = self.common.authenticate(db, user, key, {})
            self.models = xmlrpc.client.ServerProxy(base + "/xmlrpc/2/object", allow_none=True)
        except (OSError, xmlrpc.client.Error) as e:
            raise ErpError(f"cannot reach Odoo at {self.where}: {type(e).__name__}") from None
        if not self.uid:
            raise ErpError(f"Odoo at {self.where} refused the login for user {user!r} on database {db!r}")

    def call(self, model, method, *args, **kw):
        try:
            return self.models.execute_kw(self.db, self.uid, self._key, model, method, list(args), kw)
        except xmlrpc.client.Fault as e:
            raise ErpError(f"Odoo refused {model}.{method}: {str(e.faultString).strip().splitlines()[-1][:300]}") from None
        except (OSError, xmlrpc.client.Error) as e:
            raise ErpError(f"Odoo call {model}.{method} failed: {type(e).__name__}") from None


_ALL = {"context": {"active_test": False}}


def sync_bom(vault, name, revision, url, db, user, key):
    """Push the EBOM of ``name`` at Released ``revision`` to Odoo. Returns what changed::

        {"products": {"created": n, "updated": n}, "boms": {"created": n, "updated": n, "reactivated": n},
         "archived": [codes], "unchanged": bool}

    Raises ``ErpError`` for a revision that is not Released (before any call to Odoo), a failed login, or an Odoo
    error."""
    if not revision:
        raise ErpError("a revision is required: only a Released revision is synced")
    try:
        recs = [r for r in vault.history(name) if r["revision"] == revision]
    except V.DocumentError as e:
        raise ErpError(str(e)) from None
    if not recs:
        raise ErpError(f"{name} has no revision {revision}")
    if recs[0]["state"] != RELEASED:
        raise ErpError(f"{name} {revision} is {recs[0]['state']}, not Released; only Released revisions are synced to the ERP")
    rows = vault.bom(name, revision)
    products, boms = plan(rows)
    odoo = _Odoo(url, db, user, key)
    res = {"products": {"created": 0, "updated": 0}, "boms": {"created": 0, "updated": 0, "reactivated": 0}, "archived": []}

    # products
    have = {}
    for p in odoo.call("product.product", "search_read", [["default_code", "in", sorted(products)]],
                       fields=["default_code", "name", "description", "product_tmpl_id"], **_ALL):
        have.setdefault(p["default_code"], p)
    pid, tmpl = {}, {}
    for k in sorted(products):
        want, cur = products[k], have.get(k)
        if cur is None:
            new = odoo.call("product.product", "create", [{"default_code": k, **want}])
            new = new[0] if isinstance(new, list) else new
            cur = odoo.call("product.product", "read", [new], fields=["product_tmpl_id"])[0]
            cur["id"] = new
            res["products"]["created"] += 1
        elif cur["name"] != want["name"] or (cur.get("description") or "") != want["description"]:
            odoo.call("product.product", "write", [cur["id"]], want)
            res["products"]["updated"] += 1
        pid[k], tmpl[k] = cur["id"], cur["product_tmpl_id"][0] if isinstance(cur["product_tmpl_id"], (list, tuple)) else cur["product_tmpl_id"]

    # boms
    if boms:
        existing = odoo.call("mrp.bom", "search_read", [["product_tmpl_id", "in", sorted(set(tmpl[b["key"]] for b in boms))]],
                             fields=["code", "active", "product_tmpl_id", "product_qty", "type", "bom_line_ids"], **_ALL)
        by_tmpl = {}
        for e in existing:
            t = e["product_tmpl_id"][0] if isinstance(e["product_tmpl_id"], (list, tuple)) else e["product_tmpl_id"]
            by_tmpl.setdefault(t, []).append(e)
        for b in boms:
            t = tmpl[b["key"]]
            want_lines = sorted((pid[k], float(q)) for k, q in b["lines"])
            mine = [e for e in by_tmpl.get(t, []) if e["code"] == b["code"]]
            if mine:
                e = mine[0]
                lines = odoo.call("mrp.bom.line", "read", e["bom_line_ids"], fields=["product_id", "product_qty"]) if e["bom_line_ids"] else []
                got = sorted((l["product_id"][0] if isinstance(l["product_id"], (list, tuple)) else l["product_id"],
                              float(l["product_qty"])) for l in lines)
                vals = {}
                if got != want_lines:
                    vals["bom_line_ids"] = [[5, 0, 0]] + [[0, 0, {"product_id": p, "product_qty": q}] for p, q in want_lines]
                if not e["active"]:
                    vals["active"] = True
                if float(e["product_qty"]) != 1.0 or e["type"] != "normal":
                    vals.update(product_qty=1.0, type="normal")
                if vals:
                    odoo.call("mrp.bom", "write", [e["id"]], vals)
                    res["boms"]["reactivated" if "active" in vals else "updated"] += 1
            else:
                new = odoo.call("mrp.bom", "create", [{
                    "code": b["code"], "product_tmpl_id": t, "product_id": pid[b["key"]], "product_qty": 1.0, "type": "normal",
                    "bom_line_ids": [[0, 0, {"product_id": p, "product_qty": q}] for p, q in want_lines]}])
                new = new[0] if isinstance(new, list) else new
                e = {"id": new, "code": b["code"]}
                res["boms"]["created"] += 1
            stale = [o for o in by_tmpl.get(t, []) if o["code"] != b["code"] and o["active"]]
            if stale:
                odoo.call("mrp.bom", "write", [o["id"] for o in stale], {"active": False})
                res["archived"].extend(o["code"] for o in stale)
    res["unchanged"] = not (res["products"]["created"] or res["products"]["updated"] or any(res["boms"].values()) or res["archived"])
    return res
