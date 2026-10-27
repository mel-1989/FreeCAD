# SPDX-License-Identifier: LGPL-2.1-or-later
"""Human-side apply authority.

Only the holder of the installed ``ApplyAuthority`` can mint the token that
``AgentTurn.accept`` needs, so a caller that merely drives turns (the MCP
server of F4) can propose changes but never approve them.

- The GUI (or a test) calls ``ApplyAuthority.install()`` once and keeps the
  returned object. It is never handed to the MCP layer, and **nothing at
  module level refers to it**: the module keeps only a verify-only closure
  (``_VERIFIER``), which can check a token but has no way to mint one.
- ``authority.mint(turn)`` returns a one-shot ``ApplyToken`` bound to the
  turn's id and the digest of its change set: an HMAC-SHA256 over
  ``turn id | change-set digest | nonce`` with a 32-byte secret held in the
  closure cells of the authority's mint and verify functions.
- ``AgentTurn.accept(token)`` asks the installed verifier to check it.
  Missing, hand-built, wrongly keyed, other-turn, stale-digest and replayed
  tokens are refused with ``TokenRefused``.
- ``ApplyToken`` and ``ApplyAuthority`` cannot be constructed by calling
  them. That is a convenience, not the boundary: a token built any other
  way (``object.__new__``) still needs a valid MAC.

Limits, stated plainly: inside one Python interpreter nothing is private.
Code that deliberately introspects (``_VERIFIER.__closure__``, ``gc``
walks) can read the secret, and code that monkeypatches ``verify_token``
or ``AgentTurn.accept`` can skip the check. What this module guarantees is
that no *ordinary* attribute path from an importable module reaches a
minting capability, that the first installer wins (the GUI installs at
startup; a second ``install()`` raises and only the holder can uninstall),
and that every approval path goes through one verifier. The hard boundary
is the process boundary: F4 runs the MCP server out of process and only
forwards proposals, so agent code never shares an interpreter with this
object.
"""

import hashlib
import hmac
import secrets
import threading

_LOCK = threading.Lock()
_VERIFIER = None  # verify-only closure of the installed authority; it cannot mint


class TokenRefused(PermissionError):
    """An accept() was attempted without a valid, unspent apply token."""


class ApplyToken:
    """Opaque, one-shot proof that a human approved one change set."""

    __slots__ = ("turn_id", "digest", "nonce", "mac")

    def __init__(self, *args, **kwargs):
        raise TypeError("ApplyToken is minted by ApplyAuthority.mint(), not constructed")

    def __repr__(self):
        return f"<ApplyToken turn={getattr(self, 'turn_id', '?')}>"

    def __reduce__(self):  # no pickling a token across a process boundary
        raise TypeError("ApplyToken cannot be serialised")


def _make_core():
    """Secret, spent-nonce set and the mint/verify pair that share them."""
    secret = secrets.token_bytes(32)
    spent = set()

    def mac(turn_id, digest, nonce):
        return hmac.new(secret, f"{turn_id}|{digest}|{nonce}".encode(), hashlib.sha256).hexdigest()

    def mint(turn_id, digest):
        nonce = secrets.token_hex(16)
        tok = object.__new__(ApplyToken)
        tok.turn_id, tok.digest, tok.nonce, tok.mac = turn_id, digest, nonce, mac(turn_id, digest, nonce)
        return tok

    def check(token, turn_id, digest):
        if token is None:
            raise TokenRefused("no apply token: accept needs a token minted by the human-side authority")
        if type(token) is not ApplyToken:
            raise TokenRefused(f"not an ApplyToken: {type(token).__name__}")
        try:
            t_id, t_digest, t_nonce, t_mac = token.turn_id, token.digest, token.nonce, token.mac
        except AttributeError:
            raise TokenRefused("malformed apply token") from None
        if not all(isinstance(x, str) for x in (t_id, t_digest, t_nonce, t_mac)):
            raise TokenRefused("malformed apply token")
        if not hmac.compare_digest(mac(t_id, t_digest, t_nonce), t_mac):
            raise TokenRefused("apply token signature is invalid (forged or from another authority)")
        if t_id != turn_id:
            raise TokenRefused(f"apply token is for turn {t_id}, not {turn_id}")
        if t_digest != digest:
            raise TokenRefused("apply token was minted for a different change set")
        with _LOCK:
            if t_nonce in spent:
                raise TokenRefused("apply token already spent")
            spent.add(t_nonce)

    return mint, check


class ApplyAuthority:
    """Mints apply tokens. Create with ``install()`` and keep the object."""

    __slots__ = ("_ApplyAuthority__mint", "_ApplyAuthority__check")

    def __init__(self, *args, **kwargs):
        raise TypeError("use ApplyAuthority.install()")

    # ------------------------------------------------------------ lifecycle
    @classmethod
    def install(cls):
        """Create the process-wide authority. Raises if one is installed."""
        global _VERIFIER
        with _LOCK:
            if _VERIFIER is not None:
                raise RuntimeError("an ApplyAuthority is already installed")
            self = object.__new__(cls)
            self.__mint, self.__check = _make_core()
            _VERIFIER = self.__check
            return self

    def uninstall(self):
        """Remove this authority (only its holder can)."""
        global _VERIFIER
        with _LOCK:
            if _VERIFIER is self.__check:
                _VERIFIER = None

    def __reduce__(self):
        raise TypeError("ApplyAuthority cannot be serialised")

    # --------------------------------------------------------------- tokens
    def mint(self, turn):
        """Token for the turn's *current* change set (human-side only)."""
        return self.__mint(turn.turn_id, turn.digest())


def verify_token(token, turn_id, digest):
    """Check ``token`` against the installed authority; raise TokenRefused."""
    check = _VERIFIER
    if check is None:
        raise TokenRefused("no ApplyAuthority installed: nothing can approve agent changes")
    check(token, turn_id, digest)


def installed():
    return _VERIFIER is not None
