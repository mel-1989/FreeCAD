# SPDX-License-Identifier: LGPL-2.1-or-later
"""G03 slice 6b acceptance checks 1, 2 and 4: the PDM web UI, its FreeCAD worker and cache, the 3D viewer.

Same stack as the slice 4 and 6a server tests (PostgreSQL, Forgejo and the service on 127.0.0.1, temp dirs), but the
service is started with ``PDM_FREECAD_CMD`` so it can run its worker. Fixture, built once through real clients (each its
own ``FreeCADCmd``): ``bracket`` A.1 and A.2 (pad 10 -> 15), ``machine`` A.1 (a link to ``bracket``, checked out by alice),
``bracket2`` A.1 and A.2; ECO-0001 (alice's, over bracket A.2) and ECO-0002 (bob's, over bracket2 A.2), both Submitted. The
browser is Chromium driven by ``tools/pdm-server/browse.py`` in the service venv (the FreeCAD interpreter cannot import
playwright). The suite skips when the stack, playwright, scikit-image or the headless Chromium is missing.

 1. every_page_renders_for_a_viewer          carol: documents, a document, a revision (BOM + 3D), ECOs, an ECO (diff + 3D); no JS errors
 2. viewer_has_no_write_buttons_and_forced_posts_are_refused   carol and alice (engineer): 403, the ECO is unchanged; bad or no CSRF: 403
 3. self_approval_from_the_web_is_refused    bob on his own ECO: no buttons, forced POST refused by the library's rule, state unchanged
 4. approval_by_another_user_from_the_web    bob approves alice's ECO by the button: Approved, history and the git commit say bob
 5. pages_show_the_numbers_of_the_python_api BOM (indented and flat), CSV, diff.json and the diff tables equal Vault.bom / bom_csv / eco_diff
 6. service_starts_with_no_web_assets        no static directory, no worker, no cache: the REST API works, the pages say what is missing
 7. screenshot_baselines                     three fixture pages, two runs each, SSIM; and against the stored baselines
 8. corrupt_cache_entry_is_rebuilt           a built entry missing bom.json: pending, then a rebuilt page, never a 500
 9. security_headers_and_logout_csrf         nosniff and CSP on pages and static files; logout with a bad CSRF is 403 and keeps the session
10. worker_failure_busy_and_sweep            a failing worker: generic line for a reader, detail for an admin; a full queue says busy; tmp-* swept at start
"""

import json
import os
import shutil
import glob
import stat
import subprocess
import sys
import time
import unittest

from PDM import vault as V
from PDMServerTests.test_server import CMD, HERE, SERVER_SRC, SKIP, ServerCase, S, call
from PDMServerTests.test_server import _collect  # noqa: F401

VENV_PY = os.path.join(S.VENV, "bin", "python") if S else None
BROWSE = os.path.join(SERVER_SRC, "browse.py") if SERVER_SRC else None
BASELINES = os.path.join(SERVER_SRC, "baselines") if SERVER_SRC else None
REL = 1e-9


def web_missing():
    """Why the browser tests cannot run here, or None."""
    if SKIP:
        return SKIP
    if not os.path.isfile(BROWSE):
        return "browse.py not found"
    probe = ("from playwright.sync_api import sync_playwright as s\nimport numpy, skimage, PIL\n"
             "p = s().start(); b = p.chromium.launch(); b.close(); p.stop()\n")
    r = subprocess.run([VENV_PY, "-c", probe], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if r.returncode != 0:
        return "playwright, scikit-image or the headless Chromium is missing in the service venv " \
               "(tools/pdm-server/requirements-test.txt): " + r.stdout.strip().splitlines()[-1][:200]
    return None


WEB_SKIP = web_missing()


def approx(a, b, path=""):
    """Deep equality with a tiny relative tolerance for floats (the worker and the client compute separately)."""
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            assert a == b, f"{path}: {a!r} != {b!r}"
        else:
            assert abs(a - b) <= REL * max(1.0, abs(a), abs(b)), f"{path}: {a!r} != {b!r}"
    elif isinstance(a, dict):
        assert isinstance(b, dict) and a.keys() == b.keys(), f"{path}: keys {sorted(a)} != {sorted(b)}"
        for k in a:
            approx(a[k], b[k], f"{path}/{k}")
    elif isinstance(a, list):
        assert isinstance(b, list) and len(a) == len(b), f"{path}: length {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            approx(x, y, f"{path}[{i}]")
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


@unittest.skipIf(WEB_SKIP, WEB_SKIP or "")
class TestWeb(ServerCase):
    fx = None  # fixture facts, built once

    @classmethod
    def setUpClass(cls):
        lib = os.path.dirname(os.path.dirname(V.__file__))
        cls.stack = S.Stack(lib, extra_env={"PDM_FREECAD_CMD": CMD})
        try:
            cls.stack.start()
        except BaseException:
            cls.stack.stop()
            raise

    # ---- helpers ------------------------------------------------------------------------------

    def run_as(self, user, ops, **kw):
        return self.client(user, ops, fresh=not os.path.isdir(self.wc(user)), **kw)

    def fixture(self):
        if TestWeb.fx:
            return TestWeb.fx
        ok = lambda r: self.assertTrue(all(x.get("ok", True) for x in r), r)  # noqa: E731
        ok(self.client("dora", [call("set_role", "bob", "approver")], fresh=True))
        for name, length in (("bracket", 15), ("bracket2", 12)):
            ok(self.run_as("alice", [{"op": "build_pad", "name": name, "length": 10}, call("add_document", name)]))
            ok(self.run_as("alice", [call("checkout", name), {"op": "set_pad", "name": name, "length": length},
                                     call("checkin", name, "longer")]))
        ok(self.run_as("alice", [{"op": "build_link", "name": "machine", "targets": ["bracket"]}, call("add_document", "machine")]))
        ok(self.run_as("alice", [call("checkout", "machine")]))
        a = self.run_as("alice", [call("eco_create", "Longer bracket", "fit", {"bracket": "A.2"})])
        self.assertTrue(a[0]["ok"], a)
        na = a[0]["value"]["number"]
        ok(self.run_as("alice", [call("eco_submit", na)]))
        b = self.run_as("bob", [call("eco_create", "Longer bracket 2", "fit", {"bracket2": "A.2"})])
        self.assertTrue(b[0]["ok"], b)
        nb = b[0]["value"]["number"]
        ok(self.run_as("bob", [call("eco_submit", nb)]))
        TestWeb.fx = {"eco_a": na, "eco_b": nb}
        return TestWeb.fx

    def browse(self, steps, user=None, viewport=(1280, 1000)):
        """Run browser steps (see browse.py); logs in as ``user`` first. Returns the list of step results."""
        steps = ([{"op": "login", "token": self.stack.tokens[user]}] if user else []) + steps
        plan = os.path.join(self.tmp, "plan.json")
        with open(plan, "w") as f:
            json.dump({"base": self.stack.url, "viewport": list(viewport), "steps": steps}, f)
        p = subprocess.run([VENV_PY, BROWSE, "run", plan], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=900)
        self.assertEqual(p.returncode, 0, p.stdout[-2000:])
        with open(plan + ".result.json") as f:
            out = json.load(f)
        self.console = out["console_errors"]
        res = out["steps"]
        if any((r.get("status") or 0) >= 500 for r in res):
            with open(os.path.join(self.stack.root, "service.out")) as f:
                self.fail("server error; service log tail:\n" + f.read()[-3000:])
        for r in res:
            self.assertNotIn("exception", r, r)
        return res[1:] if user else res

    def eco_rest(self, n):
        st, rec = self.api("GET", f"/ecos/{n}", "dora")
        self.assertEqual(st, 200, rec)
        return rec

    # ---- check 1: pages, roles, approval ------------------------------------------------------

    def test_01_every_page_renders_for_a_viewer(self):
        fx = self.fixture()
        r = self.browse([
            {"op": "goto", "path": "/web/", "extract": {"rows": {"table": "#docs"}}},
            {"op": "goto", "path": "/web/doc/bracket", "extract": {"rows": {"table": "#history"}}},
            {"op": "goto", "path": "/web/doc/bracket/rev/A.2", "wait": "rendered", "extract": {"bom": {"table": "#bom"}, "views": {"count": ".viewer canvas"}}},
            {"op": "goto", "path": "/web/ecos", "extract": {"rows": {"table": "#ecos"}}},
            {"op": "goto", "path": f"/web/ecos/{fx['eco_a']}", "wait": "rendered",
             "extract": {"hist": {"table": "#eco-history"}, "shapes": {"table": "#shapes-bracket"}, "views": {"count": ".viewer canvas"}}},
        ], user="carol")
        docs, doc, rev, ecos, eco = r
        for page in r:
            self.assertEqual(page["status"], 200, page)
            self.assertIsNone(page.get("wait_error"), page)
        self.assertEqual(self.console, [])
        names = {row[0]: row for row in docs["rows"]}
        self.assertEqual(sorted(names), ["bracket", "bracket2", "machine"])
        self.assertEqual(names["machine"][3], "alice")                     # lock holder
        self.assertEqual(names["bracket"][1:3], ["A.2", "WIP"])
        self.assertEqual([row[0] for row in doc["rows"]][:2], ["A.2", "A.1"])
        self.assertEqual(rev["views"], 1)                                   # the 3D viewer drew a canvas
        self.assertEqual([row[1] for row in rev["bom"]], ["bracket"])
        self.assertEqual({row[0] for row in ecos["rows"]}, {fx["eco_a"], fx["eco_b"]})
        self.assertEqual(eco["views"], 2)                                   # old and new, side by side
        self.assertEqual([h[1] for h in eco["hist"]], ["create", "submit"])
        self.assertTrue(eco["shapes"], eco)
        self.assertEqual(eco["buttons"], [])
        self.assertIn("Longer bracket", eco["body"])

    def test_02_viewer_has_no_write_buttons_and_forced_posts_are_refused(self):
        fx = self.fixture()
        n = fx["eco_a"]
        r = self.browse([
            {"op": "goto", "path": f"/web/ecos/{n}", "wait": "ready"},
            {"op": "post", "path": f"/web/ecos/{n}/approve", "form": {"comment": "forced"}},
            {"op": "post", "path": f"/web/ecos/{n}/reject", "form": {"comment": "forced"}},
        ], user="carol")
        self.assertEqual(r[0]["buttons"], [])
        self.assertEqual([r[1]["status"], r[2]["status"]], [403, 403], r)
        self.assertIn("viewer", r[1]["body"])
        self.assertEqual(self.eco_rest(n)["state"], "Submitted")
        # an engineer (alice) is not an approver either: no buttons, forced POST refused, nothing changed
        r = self.browse([
            {"op": "goto", "path": f"/web/ecos/{n}", "wait": "ready"},
            {"op": "post", "path": f"/web/ecos/{n}/approve", "form": {}},
        ], user="alice")
        self.assertEqual(r[0]["buttons"], [])
        self.assertEqual(r[1]["status"], 403, r)
        self.assertEqual(self.eco_rest(n)["state"], "Submitted")
        # an approver with a wrong or missing CSRF token, and an unauthenticated POST
        r = self.browse([
            {"op": "goto", "path": f"/web/ecos/{fx['eco_a']}", "wait": "ready"},
            {"op": "post", "path": f"/web/ecos/{n}/approve", "form": {}, "csrf": "bad"},
            {"op": "post", "path": f"/web/ecos/{n}/approve", "form": {}, "csrf": "none"},
        ], user="bob")
        self.assertEqual([r[1]["status"], r[2]["status"]], [403, 403], r)
        self.assertEqual(self.eco_rest(n)["state"], "Submitted")
        r = self.browse([{"op": "post", "path": f"/web/ecos/{n}/approve", "form": {"csrf": "x"}, "csrf": "none"}])
        self.assertEqual(r[0]["status"], 303)
        self.assertTrue(r[0]["location"].endswith("/web/login"), r)
        self.assertEqual(self.eco_rest(n)["state"], "Submitted")

    def test_03_self_approval_from_the_web_is_refused(self):
        fx = self.fixture()
        n = fx["eco_b"]                                                     # bob's own ECO
        r = self.browse([
            {"op": "goto", "path": f"/web/ecos/{n}", "wait": "ready"},
            {"op": "post", "path": f"/web/ecos/{n}/approve", "form": {"comment": "my own"}},
        ], user="bob")
        self.assertEqual(r[0]["buttons"], [], "an author is not offered the approve button")
        self.assertEqual(r[1]["status"], 403, r)
        self.assertIn("two-person", r[1]["body"])
        rec = self.eco_rest(n)
        self.assertEqual(rec["state"], "Submitted")
        self.assertEqual([h["action"] for h in rec["history"]], ["create", "submit"])

    def test_04_approval_by_another_user_from_the_web(self):
        fx = self.fixture()
        n = fx["eco_a"]
        r = self.browse([
            {"op": "goto", "path": f"/web/ecos/{n}", "wait": "ready", "extract": {"state": {"text": "#eco-state"}}},
        ], user="bob")
        self.assertEqual(r[0]["buttons"], ["Approve", "Reject"], r)
        r = self.browse([
            {"op": "goto", "path": f"/web/ecos/{n}", "wait": "ready"},
            {"op": "click", "selector": "#approve"},
            {"op": "goto", "path": f"/web/ecos/{n}", "wait": "ready", "extract": {"state": {"text": "#eco-state"}, "hist": {"table": "#eco-history"}}},
        ], user="bob")
        self.assertIn("Done: approve", r[1]["body"])
        self.assertIn("Approved", r[2]["state"])
        self.assertEqual([h[1:4] for h in r[2]["hist"]], [["create", "alice", "engineer"], ["submit", "alice", "engineer"], ["approve", "bob", "approver"]])
        # the library sees the same, and the commit in the vault is bob's (the hook bound author and committer to the pusher)
        hist = self.run_as("carol", [call("eco_history", n)])[0]["value"]
        self.assertEqual([(h["action"], h["actor"]) for h in hist][-1], ("approve", "bob"))
        log = subprocess.run(["git", "--git-dir", self.stack.repo_dir, "log", "-1", "--format=%an|%ae|%cn|%ce|%s", "main"],
                             stdout=subprocess.PIPE, text=True, check=True).stdout.strip()
        self.assertEqual(log, f"bob|bob@pdm.local|bob|bob@pdm.local|{n}: approve")
        st, audit = self.api("GET", "/audit", "dora")
        self.assertIn(("eco", "bob"), [(a["action"], a["actor"]) for a in audit])
        # nothing was pushed as the service or an admin: the audit holds no override
        self.assertNotIn("eco-override", [a["action"] for a in audit])

    # ---- the same numbers as the Python API ---------------------------------------------------------

    def test_05_pages_show_the_numbers_of_the_python_api(self):
        fx = self.fixture()
        n = fx["eco_a"]
        api = self.run_as("carol", [call("bom", "machine", "A.1"), call("bom_csv", "machine", "A.1"),
                                    call("bom_csv", "machine", "A.1", flat=True), call("bom_flat", "machine", "A.1"),
                                    call("eco_diff", n)])
        rows, csv_text, flat_csv, flat, eco_diff = [x["value"] for x in api]
        self.assertTrue(all(x["ok"] for x in api), api)
        dl = os.path.join(self.tmp, "dl")
        os.makedirs(dl)
        r = self.browse([
            {"op": "goto", "path": "/web/doc/machine/rev/A.1", "wait": "ready", "extract": {
                "bom": {"table": "#bom"}, "flat": {"table": "#bom-flat"}, "csv": {"count": "#csv-indented"}}},
            {"op": "goto", "path": f"/web/ecos/{n}", "wait": "ready", "extract": {"shapes": {"table": "#shapes-bracket"}}},
        ], user="carol")
        cell = lambda x: "" if x is None else str(x)  # noqa: E731
        self.assertEqual(r[0]["bom"], [[cell(x["level"]), x["name"], x["kind"], cell(x["revision"]), cell(x["quantity"]),
                                        x["material"], cell(x["document"])] for x in rows])
        self.assertEqual(r[0]["flat"], [[x["name"], x["kind"], cell(x["revision"]), cell(x["quantity"]), x["material"],
                                         cell(x["document"])] for x in flat])
        # the CSV downloads are byte-for-byte the library's text; the diff is the library's eco_diff
        cache = os.path.join(self.stack.root, "service", "cache")
        files = {}
        for d, _dirs, fs in os.walk(cache):
            for f in fs:
                files.setdefault(f, []).append(os.path.join(d, f))
        machine_csv = [p for p in files["bom.csv"] if os.sep + "machine" + os.sep in p]
        flat_csv_f = [p for p in files["bom-flat.csv"] if os.sep + "machine" + os.sep in p]
        self.assertEqual(open(machine_csv[0], newline="").read(), csv_text)
        self.assertEqual(open(flat_csv_f[0], newline="").read(), flat_csv)
        diff_json = [p for p in files["diff.json"] if f"eco-{n}-" in p]
        self.assertTrue(diff_json)
        approx(json.load(open(diff_json[-1])), json.loads(json.dumps(eco_diff)))
        # and the diff table on the page shows the library's volumes
        d = eco_diff[0]["diff"]
        want = [[s["object"], s["status"]] for s in d["shapes"]]
        self.assertEqual([row[:2] for row in r[1]["shapes"]], want)
        for row, s in zip(r[1]["shapes"], d["shapes"]):
            for cell_text, val in ((row[2], s["before"]["volume"] if s["before"] else None),
                                   (row[3], s["after"]["volume"] if s["after"] else None)):
                self.assertEqual(cell_text, "" if val is None else f"{val:.3f}".rstrip("0").rstrip("."))
        # the downloads also work through the browser session; an unknown commit is a 404
        sha = os.path.basename(os.path.dirname(os.path.dirname(machine_csv[0])))
        dls = self.browse([{"op": "download", "path": f"/web/artifact/{sha}/machine/bom.csv", "save": os.path.join(dl, "bom.csv")},
                           {"op": "download", "path": "/web/artifact/" + "0" * 40 + "/machine/bom.csv", "save": os.path.join(dl, "x")}],
                          user="carol")
        self.assertEqual([d["status"] for d in dls], [200, 404])
        self.assertEqual(open(os.path.join(dl, "bom.csv"), newline="").read(), csv_text)

    # ---- check 4: no web assets ----------------------------------------------------------------------

    def test_06_service_starts_with_no_web_assets(self):
        self.fixture()
        src = os.path.join(self.tmp, "svc-src")
        os.makedirs(src)
        shutil.copytree(os.path.join(SERVER_SRC, "pdm_server"), os.path.join(src, "pdm_server"),
                        ignore=shutil.ignore_patterns("__pycache__"))   # the package alone: no static/, no vendored three.js
        port = S.free_port()
        url = self.stack.start_service("bare", port, src, {"PDM_FREECAD_CMD": "", "PDM_CACHE_DIR": os.path.join(self.tmp, "nocache")})
        try:
            self.assertEqual(S.http("GET", url + "/health")[0], 200)
            st, me = S.http("GET", url + "/whoami", self.stack.tokens["carol"])
            self.assertEqual((st, me["role"]), (200, "reader"))
            self.assertEqual(S.http("GET", url + "/ecos", self.stack.tokens["carol"])[0], 200)
            self.assertEqual(S.http("GET", url + "/web/status")[1], {"static": False, "worker": False, "cache": False})
            self.assertEqual(S.http("GET", url + "/static/web.css")[0], 404)
            plan = os.path.join(self.tmp, "bare.json")
            with open(plan, "w") as f:
                json.dump({"base": url, "steps": [
                    {"op": "login", "token": self.stack.tokens["carol"]},
                    {"op": "goto", "path": "/web/", "extract": {"rows": {"table": "#docs"}}},
                    {"op": "goto", "path": "/web/doc/bracket/rev/A.2"},
                    {"op": "goto", "path": f"/web/ecos/{TestWeb.fx['eco_a']}"}]}, f)
            p = subprocess.run([VENV_PY, BROWSE, "run", plan], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=300)
            self.assertEqual(p.returncode, 0, p.stdout[-1500:])
            steps = json.load(open(plan + ".result.json"))["steps"]
            self.assertEqual([s["status"] for s in steps[1:]], [200, 200, 200], ascii(steps))
            self.assertEqual(len(steps[1]["rows"]), 3)                                  # the document list needs no cache
            self.assertIn("not built", steps[2]["body"])                                # revision page: says what is missing
            self.assertIn("not built", steps[3]["body"])                                # ECO page too, with its history intact
            self.assertIn(self.eco_rest(TestWeb.fx["eco_a"])["state"], steps[3]["body"])
            self.assertFalse(os.path.exists(os.path.join(self.tmp, "nocache")))         # and builds nothing
        finally:
            self.stack._kill("bare")

    # ---- check 2: screenshots -------------------------------------------------------------------------

    def test_07_screenshot_baselines(self):
        fx = self.fixture()
        pages = [("revision-bracket-A2", "/web/doc/bracket/rev/A.2", "#model"),
                 ("revision-machine-A1", "/web/doc/machine/rev/A.1", "#model"),
                 ("eco-diff-bracket", f"/web/ecos/{fx['eco_a']}", "#views-bracket")]
        shots = {}
        for run in (1, 2):
            out = os.path.join(self.tmp, f"run{run}")
            os.makedirs(out)
            steps = [{"op": "goto", "path": path, "wait": "rendered", "shot": {"selector": sel, "path": os.path.join(out, name + ".png")}}
                     for name, path, sel in pages]
            r = self.browse(steps, user="carol", viewport=(1280, 1000))
            for page in r:
                self.assertIsNone(page.get("wait_error"), page)
            shots[run] = out
        os.makedirs(BASELINES, exist_ok=True)
        report = {}
        for name, _p, _s in pages:
            a, b = (os.path.join(shots[i], name + ".png") for i in (1, 2))
            base = os.path.join(BASELINES, name + ".png")
            if not os.path.isfile(base) or os.environ.get("PDM_UPDATE_BASELINES"):
                shutil.copy(a, base)
            two = float(subprocess.run([VENV_PY, BROWSE, "ssim", a, b], stdout=subprocess.PIPE, text=True, check=True).stdout)
            stored = float(subprocess.run([VENV_PY, BROWSE, "ssim", base, b], stdout=subprocess.PIPE, text=True, check=True).stdout)
            report[name] = {"run1_vs_run2": two, "stored_vs_run2": stored}
        sys.stderr.write("\nweb screenshot SSIM: " + json.dumps(report) + "\n")
        for name, v in report.items():
            self.assertEqual(v["run1_vs_run2"], 1.0, f"{name}: two runs differ: {report}")
            self.assertGreaterEqual(v["stored_vs_run2"], 0.99, f"{name}: differs from the stored baseline: {report}")

    # ---- repairs after the run-032 verification --------------------------------------------------------

    def cache(self):
        return os.path.join(self.stack.root, "service", "cache")

    def test_08_corrupt_cache_entry_is_rebuilt(self):
        self.fixture()
        path = "/web/doc/bracket/rev/A.2"
        r = self.browse([{"op": "goto", "path": path, "wait": "ready"}], user="carol")
        self.assertEqual(r[0]["status"], 200, r)
        boms = glob.glob(os.path.join(self.cache(), "*", "bracket", "bom.json"))
        self.assertTrue(boms, "the first visit built the entry")
        os.remove(boms[0])
        r = self.browse([{"op": "goto", "path": path}, {"op": "goto", "path": path, "wait": "ready", "extract": {"bom": {"table": "#bom"}}}],
                        user="carol")                                       # browse() fails the test on any 5xx
        self.assertEqual([r[0]["status"], r[1]["status"]], [200, 200], r)
        self.assertIn("Pending", r[0]["body"])
        self.assertIsNone(r[1].get("wait_error"), r)
        self.assertEqual([row[1] for row in r[1]["bom"]], ["bracket"])
        self.assertTrue(os.path.isfile(boms[0]), "the entry was rebuilt")

    def test_09_security_headers_and_logout_csrf(self):
        self.fixture()
        r = self.browse([
            {"op": "goto", "path": "/web/doc/bracket/rev/A.2", "wait": "ready"},
            {"op": "download", "path": "/static/web.css"},
            {"op": "post", "path": "/web/logout", "csrf": "bad"},
            {"op": "goto", "path": "/web/", "extract": {"who": {"text": "#whoami"}}},
        ], user="carol")
        page, css, bad, after = r
        csp = page["headers"]["content-security-policy"]
        self.assertIn("default-src 'self'", csp)
        self.assertNotIn("unsafe-inline", csp)
        self.assertEqual(page["headers"]["x-content-type-options"], "nosniff")
        self.assertEqual(css["headers"]["x-content-type-options"], "nosniff")
        self.assertEqual(self.console, [])
        self.assertEqual(bad["status"], 403, bad)
        self.assertNotIn("set-cookie", bad["headers"])                      # the cookie is left alone
        self.assertEqual(after["status"], 200, after)                       # and the session survives
        self.assertIn("carol", after["who"])
        sha = os.path.basename(os.path.dirname(glob.glob(os.path.join(self.cache(), "*", "bracket"))[0]))
        r = self.browse([{"op": "download", "path": f"/web/artifact/{sha}/bracket/bom.csv"}], user="carol")
        self.assertEqual(r[0]["headers"]["x-content-type-options"], "nosniff")

    def test_10_worker_failure_busy_and_sweep(self):
        self.fixture()
        script = os.path.join(self.tmp, "fakecmd")
        with open(script, "w") as f:
            f.write("#!/bin/sh\nsleep 3\necho 'Traceback: File /Users/secret/path.py line 1: boom'\nexit 1\n")
        os.chmod(script, os.stat(script).st_mode | stat.S_IXUSR)
        cache = os.path.join(self.tmp, "failcache")
        os.makedirs(os.path.join(cache, "tmp-deadbeef"))
        open(os.path.join(cache, "job-old.json"), "w").close()
        url = self.stack.start_service("fail", S.free_port(), None, {"PDM_FREECAD_CMD": script, "PDM_CACHE_DIR": cache, "PDM_WEB_MAX_JOBS": "1"})
        try:
            self.assertEqual(sorted(os.listdir(cache)), [])                 # swept at start-up
            saved = self.stack.url
            self.stack.url = url
            try:
                r = self.browse([{"op": "goto", "path": "/web/doc/bracket/rev/A.2"},
                                 {"op": "goto", "path": "/web/doc/machine/rev/A.1"}], user="carol")
                self.assertIn("Pending", r[0]["body"])
                self.assertIn("Busy", r[1]["body"])                          # queue of one is full: no second job
                self.assertEqual(r[1]["status"], 200)
                body = ""
                for _ in range(30):
                    time.sleep(1)
                    body = self.browse([{"op": "goto", "path": "/web/doc/bracket/rev/A.2"}], user="carol")[0]["body"]
                    if "could not build" in body:
                        break
                self.assertIn("could not build", body)
                self.assertNotIn("Traceback", body)
                self.assertNotIn("/Users/secret", body)
                admin = self.browse([{"op": "goto", "path": "/web/doc/bracket/rev/A.2"}], user="dora")[0]["body"]
                self.assertIn("Traceback", admin)                            # an admin sees the detail
            finally:
                self.stack.url = saved
        finally:
            self.stack._kill("fail")
