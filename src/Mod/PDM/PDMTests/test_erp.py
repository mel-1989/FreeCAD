# SPDX-License-Identifier: LGPL-2.1-or-later
"""Slice 6b, check 3 of G03: the Odoo Community BOM sync, against a recorded in-process XML-RPC fake.

The fake (``FakeOdoo``) implements ``authenticate`` and ``execute_kw`` for ``product.product``, ``product.template``,
``mrp.bom`` and ``mrp.bom.line`` (search, search_read, read, create, write, name_search) over an in-memory store
and records every model call. No Odoo is needed; a live Odoo is optional and never on a public host (gate D11).
The BOM is the slice 6a fixture (``bom_fixture``): ``machine`` (a sub-assembly used twice, a link array, a
weldment frame), released at A.1 and again at B.1 after a Cover sub-assembly is added.
"""

import itertools
import os
import shutil
import tempfile
import threading
import unittest
import xmlrpc.client
from xmlrpc.server import SimpleXMLRPCRequestHandler, SimpleXMLRPCServer

import FreeCAD as App

from PDM import erp
from PDM import vault as V
from PDMTests import bom_fixture as F

CTL = os.path.expanduser("~/_agents/repos/app development/cad-fork")
DB, USER, KEY = "testdb", "sync@example.com", "s3cret-api-key"
M2O = {"product.product": {"product_tmpl_id": "product.template"},
       "mrp.bom": {"product_tmpl_id": "product.template", "product_id": "product.product"},
       "mrp.bom.line": {"product_id": "product.product", "bom_id": "mrp.bom"}}
HAS_ACTIVE = ("product.product", "mrp.bom")


class _Handler(SimpleXMLRPCRequestHandler):
    rpc_paths = ("/xmlrpc/2/common", "/xmlrpc/2/object")

    def log_message(self, *a):
        pass


class FakeOdoo:
    """Just enough Odoo 17/18 for the sync, in memory. ``log``: (model, method, args, kwargs) of every execute_kw."""

    def __init__(self):
        self.store = {m: {} for m in ("product.template", "product.product", "mrp.bom", "mrp.bom.line")}
        self.ids = itertools.count(1)
        self.log = []
        self.server = SimpleXMLRPCServer(("127.0.0.1", 0), requestHandler=_Handler, allow_none=True, logRequests=False)
        self.server.register_function(self.authenticate, "authenticate")
        self.server.register_function(self.execute_kw, "execute_kw")
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def authenticate(self, db, user, key, ctx):
        return 7 if (db, user, key) == (DB, USER, KEY) else False

    def calls(self, *methods, since=0):
        return [c for c in self.log[since:] if not methods or c[1] in methods]

    # ---- the model layer -------------------------------------------------------------------------

    def _out(self, model, rec, fields):
        out = {"id": rec["id"]}
        for f in (fields or rec):
            if f == "id" or f not in rec:
                continue
            v = rec[f]
            tgt = M2O.get(model, {}).get(f)
            out[f] = [v, self.store[tgt][v].get("name") or str(v)] if tgt and v else v
        return out

    def _match(self, model, rec, dom, ctx):
        if model in HAS_ACTIVE and ctx.get("active_test", True) and not rec["active"]:
            return False
        for f, op, val in dom:
            cur = rec.get(f) if f != "id" else rec["id"]
            if op == "=" and cur != val:
                return False
            if op == "in" and cur not in val:
                return False
        return True

    def _create(self, model, vals):
        vals = dict(vals)
        i = next(self.ids)
        rec = {"id": i}
        if model == "product.product":
            t = next(self.ids)
            self.store["product.template"][t] = {"id": t, "name": vals.get("name"), "description": vals.get("description")}
            rec.update(product_tmpl_id=t, active=True, description=False)
        if model == "mrp.bom":
            rec.update(active=True, type="normal", product_qty=1.0, bom_line_ids=[])
        if model == "mrp.bom.line":
            rec.update(product_qty=1.0)
        lines = vals.pop("bom_line_ids", [])
        rec.update(vals)
        self.store[model][i] = rec
        if model == "mrp.bom":
            self._commands(rec, lines)
        return i

    def _commands(self, bom, cmds):
        for c in cmds:
            if c[0] == 5:
                for l in bom["bom_line_ids"]:
                    del self.store["mrp.bom.line"][l]
                bom["bom_line_ids"] = []
            elif c[0] == 0:
                l = self._create("mrp.bom.line", dict(c[2], bom_id=bom["id"]))
                bom["bom_line_ids"].append(l)
            else:
                raise xmlrpc.client.Fault(1, "fake: unsupported command %r" % (c,))

    def execute_kw(self, db, uid, key, model, method, args, kw):
        if uid != 7 or key != KEY:
            raise xmlrpc.client.Fault(2, "AccessDenied")
        self.log.append((model, method, args, kw))
        recs = self.store[model]
        ctx = kw.get("context", {})
        if method in ("search", "search_read"):
            found = [r for r in recs.values() if self._match(model, r, args[0], ctx)]
            if method == "search":
                return [r["id"] for r in found]
            return [self._out(model, r, kw.get("fields")) for r in found]
        if method == "read":
            return [self._out(model, recs[i], kw.get("fields")) for i in args[0]]
        if method == "name_search":
            return [[r["id"], r.get("name")] for r in recs.values() if args[0].lower() in (r.get("name") or "").lower()]
        if method == "create":
            if isinstance(args[0], list):
                return [self._create(model, v) for v in args[0]]
            return self._create(model, args[0])
        if method == "write":
            for i in args[0]:
                vals = dict(args[1])
                cmds = vals.pop("bom_line_ids", None)
                recs[i].update(vals)
                if cmds is not None:
                    self._commands(recs[i], cmds)
            return True
        raise xmlrpc.client.Fault(3, "fake: no method " + method)

    # ---- views for the assertions ----------------------------------------------------------------

    def product_names(self):
        return {r["default_code"]: r["name"] for r in self.store["product.product"].values()}

    def boms(self):
        """code -> (active, product default_code, sorted [(line default_code, qty)])."""
        prod = self.store["product.product"]
        out = {}
        for b in self.store["mrp.bom"].values():
            lines = sorted((prod[self.store["mrp.bom.line"][l]["product_id"]]["default_code"],
                            self.store["mrp.bom.line"][l]["product_qty"]) for l in b["bom_line_ids"])
            out[b["code"]] = (b["active"], prod[b["product_id"]]["default_code"], lines)
        return out


W = "frame:EN SHS %s, cut %s mm, ends square/square"
MEMBERS = [(W % ("125x125x12", "2500.000"), 4), (W % ("90x90x6", "3000.000"), 4), (W % ("90x90x6", "2000.000"), 4)]
# hand-written from the fixture's construction (see bom_fixture and test_bom)
BOMS_A1 = {
    "machine A.1": (True, "machine", [("bracket", 1), ("frame", 1), ("machine:Sub", 2)]),
    "machine:Sub A.1": (True, "machine:Sub", [("bracket", 4), ("machine:Plate", 1)]),
    "frame A.1": (True, "frame", sorted(MEMBERS)),
}
PRODUCTS_A1 = {"machine", "machine:Sub", "machine:Plate", "bracket", "frame"} | {m[0] for m in MEMBERS}
BOMS_B1 = {
    "machine B.1": (True, "machine", [("bracket", 1), ("frame", 1), ("machine:Cover", 1), ("machine:Sub", 3)]),
    "machine:Sub B.1": (True, "machine:Sub", [("bracket", 4), ("machine:Plate", 1)]),
    "machine:Cover B.1": (True, "machine:Cover", [("machine:CoverPlate", 1)]),
}


@unittest.skipIf(F.frames is None, "the Weldment test frames are not installed")
class TestErpSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("CADFORK_CTL", CTL)
        cls.tmp = os.path.realpath(tempfile.mkdtemp(prefix="pdmerp"))
        F.close_all()
        cls.fx = F.Fixture(cls.tmp)
        cls.fx.build()
        cls.v = cls.fx.v
        for d in ("machine",):
            cls.v.transition(d, "InReview")
            cls.v.transition(d, "Released")
        cls.v.checkout("machine", new_revision=True)
        doc = App.openDocument(cls.v.doc_path("machine"))
        c = doc.addObject("App::Part", "Cover")
        c.addObject(F.pad_body(doc, "CoverPlate", 2.0, width=40.0, height=40.0, material="Polycarbonate"))
        doc.addObject("App::Link", "CoverLink").LinkedObject = c
        doc.addObject("App::Link", "SubC").LinkedObject = doc.getObject("Sub")
        doc.recompute()
        doc.save()
        assert cls.v.checkin("machine", "cover")["revision"] == "B.1"
        F.close_all()
        cls.v.transition("machine", "InReview")
        cls.v.transition("machine", "Released")

    @classmethod
    def tearDownClass(cls):
        F.close_all()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.odoo = FakeOdoo()
        self.addCleanup(self.odoo.close)

    def sync(self, rev, key=KEY, name="machine", user=USER):
        return erp.sync_bom(self.v, name, rev, self.odoo.url, DB, user, key)

    # ---- check 3 -----------------------------------------------------------------------------------

    def test_first_sync_creates_exactly_the_hand_written_products_and_boms(self):
        r = self.sync("A.1")
        self.assertEqual(r["products"], {"created": 8, "updated": 0})
        self.assertEqual(r["boms"], {"created": 3, "updated": 0, "reactivated": 0})
        self.assertEqual(r["archived"], [])
        self.assertEqual(set(self.odoo.product_names()), PRODUCTS_A1)
        self.assertEqual(self.odoo.boms(), BOMS_A1)
        creates = self.odoo.calls("create")
        self.assertEqual([c[0] for c in creates], ["product.product"] * 8 + ["mrp.bom"] * 3)  # products before BOMs
        self.assertEqual(self.odoo.calls("write", "unlink"), [])
        self.assertEqual({c[0] for c in self.odoo.log}, {"product.product", "mrp.bom"})  # no model outside the three MRP ones
        for c in creates[8:]:
            self.assertEqual((c[2][0][0]["product_qty"], c[2][0][0]["type"]), (1.0, "normal"))

    def test_weldment_rows_become_products_with_their_cut_list_descriptions(self):
        self.sync("A.1")
        prods = {p["default_code"]: p for p in self.odoo.store["product.product"].values()}
        for key, qty in MEMBERS:
            p = prods[key]
            self.assertEqual(p["name"], key.split(":", 1)[1])  # the cut-list description
            self.assertTrue(p["description"].startswith(p["name"]), p["description"])
            self.assertIn("material Structural steel", p["description"])
            self.assertIn("length ", p["description"])
            self.assertIn("mass ", p["description"])
        self.assertIn("length 2500.000 mm", prods[MEMBERS[0][0]]["description"])
        self.assertEqual(prods["frame"]["name"], "frame")
        self.assertEqual(prods["machine:Plate"]["description"], "Plate; material Aluminium 6061")

    def test_second_sync_of_the_same_revision_makes_no_create_or_write_call(self):
        self.sync("A.1")
        mark = len(self.odoo.log)
        before = (dict(self.odoo.store["product.product"]), dict(self.odoo.store["mrp.bom"]))
        r = self.sync("A.1")
        self.assertTrue(r["unchanged"], r)
        self.assertEqual(self.odoo.calls("create", "write", "unlink", since=mark), [])
        self.assertGreater(len(self.odoo.log), mark)  # it did read
        self.assertEqual((self.odoo.store["product.product"], self.odoo.store["mrp.bom"]), before)
        self.assertEqual(self.odoo.boms(), BOMS_A1)

    def test_a_new_released_revision_creates_a_new_bom_and_archives_the_old_one(self):
        self.sync("A.1")
        mark = len(self.odoo.log)
        r = self.sync("B.1")
        self.assertEqual(r["products"]["created"], 2)  # machine:Cover, machine:CoverPlate; the rest exist
        self.assertEqual(sorted(r["archived"]), ["machine A.1", "machine:Sub A.1"])
        boms = self.odoo.boms()
        for code, want in BOMS_B1.items():
            self.assertEqual(boms[code], want, code)
        self.assertEqual(boms["machine A.1"], (False, "machine", BOMS_A1["machine A.1"][2]))  # kept, not deleted
        self.assertEqual(boms["machine:Sub A.1"][0], False)
        self.assertEqual(boms["frame A.1"], BOMS_A1["frame A.1"])  # untouched: the frame did not change
        self.assertEqual([c[2][1] for c in self.odoo.calls("write", since=mark)], [{"active": False}] * 2)  # the two archives only
        mark = len(self.odoo.log)
        self.assertTrue(self.sync("B.1")["unchanged"])
        self.assertEqual(self.odoo.calls("create", "write", since=mark), [])
        r = self.sync("A.1")  # going back reactivates the old BOM and archives the newer one
        self.assertEqual((r["boms"]["reactivated"], sorted(r["archived"])), (2, ["machine B.1", "machine:Sub B.1"]))
        self.assertEqual(self.odoo.boms()["machine A.1"][0], True)

    def test_a_revision_that_is_not_released_is_refused_before_any_call(self):
        for name, rev in (("bracket", "A.1"), ("machine", "C.1"), ("nope", "A.1")):
            with self.assertRaises(erp.ErpError):
                self.sync(rev, name=name)
        with self.assertRaises(erp.ErpError) as cm:
            self.sync("A.1", name="bracket")
        self.assertIn("not Released", str(cm.exception))
        self.assertEqual(self.odoo.log, [])
        self.assertEqual(self.odoo.store["product.product"], {})

    def test_a_wrong_password_fails_cleanly_and_never_echoes_the_credentials(self):
        for bad in ("wrong-password-123", ""):
            with self.assertRaises(erp.ErpError) as cm:
                self.sync("A.1", key=bad)
            self.assertNotIn("wrong-password-123", str(cm.exception))
            self.assertNotIn(KEY, str(cm.exception))
        with self.assertRaises(erp.ErpError):
            self.sync("A.1", user="someone@example.com")
        self.assertEqual(self.odoo.log, [])
        self.assertEqual(self.odoo.store["mrp.bom"], {})

    def test_an_unreachable_odoo_is_an_erp_error(self):
        url = self.odoo.url
        self.odoo.close()
        self.odoo = FakeOdoo.__new__(FakeOdoo)  # setUp's cleanup closes the (already closed) real one once more
        self.odoo.close = lambda: None
        with self.assertRaises(erp.ErpError) as cm:
            erp.sync_bom(self.v, "machine", "A.1", url, DB, USER, KEY)
        self.assertNotIn(KEY, str(cm.exception))

    def test_plan_is_pure_and_matches_the_vault_bom(self):
        products, boms = erp.plan(self.v.bom("machine", "A.1"))
        self.assertEqual(set(products), PRODUCTS_A1)
        self.assertEqual({b["code"]: sorted(b["lines"]) for b in boms}, {c: v[2] for c, v in BOMS_A1.items()})

    def test_credentials_from_env(self):
        env = {f"PDM_ODOO_{k}": v for k, v in (("URL", "http://x"), ("DB", "d"), ("USER", "u"), ("KEY", "k"))}
        old = {k: os.environ.get(k) for k in env}
        try:
            os.environ.update(env)
            self.assertEqual(erp.credentials_from_env(), ("http://x", "d", "u", "k"))
            del os.environ["PDM_ODOO_KEY"]
            with self.assertRaises(erp.ErpError):
                erp.credentials_from_env()
        finally:
            for k, v in old.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
