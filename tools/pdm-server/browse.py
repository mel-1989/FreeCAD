#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Headless-browser driver for the PDM web UI tests (runs in the service venv, which has playwright; the FreeCAD
test process cannot import it). ``browse.py run <plan.json>`` executes the steps and writes ``<plan>.result.json``;
``browse.py ssim <a.png> <b.png>`` prints the structural similarity of two images (1.0 = identical).

Plan: ``{"base": url, "viewport": [w, h], "steps": [...]}``. One browser context is used for all steps, so the login
session carries over. Steps (each yields one result object, in order):

* ``{"op": "login", "token": t}``: log in through the form.
* ``{"op": "goto", "path": p, "wait": "ready"|"rendered"|null, "timeout": s, "extract": {...}, "shot": {...}}``:
  open a page; ``ready`` reloads while the page says "pending"; ``rendered`` also waits for body[data-rendered]
  (the 3D viewers). ``extract`` maps a name to ``{"table": css}`` (rows of cell texts), ``{"text": css}`` or
  ``{"count": css}``. ``shot`` is ``{"selector": css, "path": png}``: a screenshot of that element.
* ``{"op": "click", "selector": css}``: click a button, wait for the navigation, report URL and page text.
* ``{"op": "post", "path": p, "form": {...}, "csrf": "page"|"bad"|"none"}``: a form POST as the logged-in browser, with the
  CSRF token read from the current page, a wrong one, or none.
* ``{"op": "download", "path": p, "save": file}``: fetch with the session; saves the body.
"""

import json
import sys
import time

CHROMIUM_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--disable-gpu", "--ignore-gpu-blocklist",
                 "--force-color-profile=srgb", "--disable-lcd-text", "--font-render-hinting=none",
                 "--disable-skia-runtime-opts", "--disable-features=Vulkan"]


def ssim(a, b):
    import numpy as np
    from PIL import Image
    from skimage.metrics import structural_similarity
    ia, ib = np.asarray(Image.open(a).convert("RGB")), np.asarray(Image.open(b).convert("RGB"))
    if ia.shape != ib.shape:
        return 0.0
    return float(structural_similarity(ia, ib, channel_axis=2, data_range=255))


def extract(page, spec):
    out = {}
    for name, q in spec.items():
        if "table" in q:
            out[name] = page.eval_on_selector_all(
                q["table"] + " tbody tr", "rows => rows.map(r => [...r.cells].map(c => c.innerText.trim()))")
        elif "text" in q:
            loc = page.locator(q["text"])
            out[name] = loc.first.inner_text().strip() if loc.count() else None
        elif "count" in q:
            out[name] = page.locator(q["count"]).count()
    return out


def wait_page(page, wait, timeout):
    end = time.time() + timeout
    while page.locator("#pending").count():
        if time.time() > end:
            return "timeout: still pending"
        page.wait_for_timeout(1500)
        page.reload(wait_until="load")
    if wait == "rendered" and page.locator(".viewer").count():
        remain = max(1000, int((end - time.time()) * 1000))
        # polled with evaluate (CDP): wait_for_function evaluates a string in the page and the page's CSP forbids eval
        deadline = time.time() + remain / 1000
        while not page.evaluate("document.body.dataset.rendered || ''"):
            if time.time() > deadline:
                return "timeout: nothing rendered"
            page.wait_for_timeout(100)
        if page.evaluate("document.body.dataset.rendered") != "1":
            return "render error: " + str(page.evaluate("document.body.dataset.renderError"))
        page.wait_for_timeout(300)
    return None


def run(plan_path):
    from playwright.sync_api import sync_playwright
    with open(plan_path) as f:
        plan = json.load(f)
    base = plan["base"].rstrip("/")
    w, h = plan.get("viewport", [1280, 1000])
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=CHROMIUM_ARGS)
        ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1, reduced_motion="reduce",
                                  locale="en-US", timezone_id="UTC", color_scheme="light")
        page = ctx.new_page()
        console = []
        page.on("console", lambda m: console.append(m.text + " @ " + str(m.location.get("url"))) if m.type == "error" else None)
        page.on("pageerror", lambda e: console.append(str(e)))
        page.on("response", lambda r: console.append(f"{r.status} {r.url}") if r.status >= 400 and not r.url.endswith("/favicon.ico") and r.request.resource_type != "document" else None)
        for step in plan["steps"]:
            op = step["op"]
            r = {"op": op}
            try:
                if op == "login":
                    page.goto(base + "/web/login")
                    page.fill("#token", step["token"])
                    with page.expect_navigation():
                        page.click("button[type=submit]")
                    r.update(url=page.url,
                             error=(page.locator("#login-error").inner_text() if page.locator("#login-error").count() else None),
                             whoami=(page.locator("#whoami").inner_text() if page.locator("#whoami").count() else None))
                elif op == "goto":
                    resp = page.goto(base + step["path"], wait_until="load")
                    r["status"] = resp.status if resp else None
                    r["headers"] = dict(resp.headers) if resp else {}
                    if step.get("wait"):
                        r["wait_error"] = wait_page(page, step["wait"], step.get("timeout", 240))
                    r["url"] = page.url
                    r["title"] = page.title()
                    r["body"] = page.inner_text("body")[:4000]
                    r["buttons"] = page.eval_on_selector_all("main button", "bs => bs.map(b => b.innerText.trim())")
                    r.update(extract(page, step.get("extract", {})))
                    if step.get("shot"):
                        page.locator(step["shot"]["selector"]).first.screenshot(path=step["shot"]["path"], animations="disabled")
                elif op == "click":
                    with page.expect_navigation():
                        page.click(step["selector"])
                    r.update(url=page.url, body=page.inner_text("body")[:2000])
                elif op == "post":
                    token = {"page": lambda: page.input_value("header input[name=csrf]"), "bad": lambda: "bad",
                             "none": lambda: None}[step.get("csrf", "page")]()
                    form = dict(step.get("form", {}))
                    if token is not None:
                        form["csrf"] = token
                    resp = ctx.request.post(base + step["path"], form=form, max_redirects=0)
                    r.update(status=resp.status, body=resp.text()[:1500], location=resp.headers.get("location"), headers=dict(resp.headers))
                elif op == "download":
                    resp = ctx.request.get(base + step["path"])
                    r.update(status=resp.status, bytes=len(resp.body()), headers=dict(resp.headers))
                    if resp.status == 200 and step.get("save"):
                        with open(step["save"], "wb") as f:
                            f.write(resp.body())
                else:
                    r["error"] = f"unknown op {op}"
            except Exception as exc:
                r["exception"] = f"{type(exc).__name__}: {exc}"
            results.append(r)
        browser.close()
    with open(plan_path + ".result.json", "w") as f:
        json.dump({"steps": results, "console_errors": console}, f)


if __name__ == "__main__":
    if sys.argv[1:2] == ["ssim"]:
        print(ssim(sys.argv[2], sys.argv[3]))
    elif sys.argv[1:2] == ["run"]:
        run(sys.argv[2])
    else:
        sys.exit(__doc__)
