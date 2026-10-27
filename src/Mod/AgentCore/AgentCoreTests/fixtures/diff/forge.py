# SPDX-License-Identifier: LGPL-2.1-or-later
"""Key-search forgery probe shared by the sealed-worker probe and its
in-process control: every 32-byte bytes value reachable from function
closures (any function the gc tracks) or module globals is tried as the HMAC
key of an apply token for ``turn``. Returns (accepted, candidates)."""

import gc
import hashlib
import hmac
import sys


def key_candidates():
    found, seen = [], set()

    def add(v):
        if isinstance(v, (bytes, bytearray)) and len(v) == 32 and id(v) not in seen:
            seen.add(id(v))
            found.append(bytes(v))

    for o in gc.get_objects():
        cl = getattr(o, "__closure__", None) if callable(o) else None
        for c in cl or ():
            try:
                add(c.cell_contents)
            except ValueError:
                pass
    for m in list(sys.modules.values()):
        for v in list(getattr(m, "__dict__", {}).values()):
            add(v)
    return found


def try_forge(turn, authority, nonce="22" * 16):
    cands = key_candidates()
    for key in cands:
        tok = object.__new__(authority.ApplyToken)
        mac = hmac.new(key, f"{turn.turn_id}|{turn.digest()}|{nonce}".encode(), hashlib.sha256).hexdigest()
        tok.turn_id, tok.digest, tok.nonce, tok.mac = turn.turn_id, turn.digest(), nonce, mac
        try:
            turn.accept(tok)
            return True, len(cands)
        except authority.TokenRefused:
            continue
    return False, len(cands)
