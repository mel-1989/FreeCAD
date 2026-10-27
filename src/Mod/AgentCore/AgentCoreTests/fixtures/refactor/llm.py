# SPDX-License-Identifier: LGPL-2.1-or-later
"""Minimal OpenRouter client for the F6 evals (same endpoint and key as the openrouter MCP relay).

The key is read from the shared ``.env`` (never printed). Every call returns its usage and cost, which the
eval drivers sum into a transparency footer (model, tokens in and out, cost).
"""
import json
import os
import time
import urllib.request

ENV = os.path.expanduser(os.environ.get("CADFORK_ENV", "~/_agents/anth/.env"))
MODEL = os.environ.get("CADFORK_F6_MODEL", "deepseek/deepseek-v4-flash")


def _env(key, default=""):
    if os.environ.get(key):
        return os.environ[key]
    try:
        for line in open(ENV, encoding="utf-8"):
            line = line.strip()
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return default


def chat(messages, *, model=MODEL, max_tokens=2500, temperature=0.7, retries=3):
    base = _env("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
            "reasoning": {"enabled": False}}
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": "Bearer " + (_env("OPEN_ROUTER_API") or _env("OPENROUTER_API_KEY")),
                                              "Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                d = json.load(r)
            msg = (d.get("choices") or [{}])[0].get("message") or {}
            u = d.get("usage") or {}
            return {"text": msg.get("content") or "", "tokens_in": u.get("prompt_tokens", 0),
                    "tokens_out": u.get("completion_tokens", 0), "cost": float(u.get("cost") or 0.0),
                    "seconds": round(time.time() - t0, 2), "model": d.get("model", model)}
        except Exception as exc:  # network or HTTP error: back off and retry
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"openrouter call failed after {retries} tries: {last!r}")


class Meter:
    """Thread-safe running total of tokens and cost."""

    def __init__(self):
        import threading
        self.lock = threading.Lock()
        self.calls = self.tin = self.tout = 0
        self.cost = 0.0
        self.model = None

    def add(self, r):
        with self.lock:
            self.calls += 1
            self.tin += r["tokens_in"]
            self.tout += r["tokens_out"]
            self.cost += r["cost"]
            self.model = r["model"]

    def footer(self):
        return (f"model {self.model} | calls {self.calls} | tokens in {self.tin:,} out {self.tout:,} | "
                f"cost ${self.cost:.4f}")
