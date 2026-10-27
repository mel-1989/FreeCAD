# SPDX-License-Identifier: LGPL-2.1-or-later
"""Human-side apply authority.

Only the holder of the installed ``ApplyAuthority`` can mint the token that
``AgentTurn.accept`` needs, so a caller that merely drives turns (the MCP
server of F4) can propose changes but never approve them.

- The GUI (or a test) calls ``ApplyAuthority.install()`` once and keeps the
  returned object. It is never handed to the MCP layer.
- ``authority.mint(turn)`` returns a one-shot ``ApplyToken`` bound to the
  turn's id and the digest of its change set: an HMAC-SHA256 over
  ``turn id | change-set digest | nonce`` with a 32-byte secret that lives
  only inside the authority.
- ``AgentTurn.accept(token)`` asks the installed authority to verify it.
  Missing, hand-built, wrongly keyed, other-turn, stale-digest and replayed
  tokens are refused with ``TokenRefused``.

Limits: inside one Python interpreter nothing is truly private; code that
walks ``gc`` can reach the secret. The property delivered is that the MCP
layer never *receives* a token or the authority, and that every approval
path goes through one verifier. The hard boundary is the process boundary
(F4 runs the MCP server out of process and only forwards proposals).
"""

import hashlib
import hmac
import secrets
import threading

_SENTINEL = object()
_LOCK = threading.Lock()
_INSTALLED = None


class TokenRefused(PermissionError):
    """An accept() was attempted without a valid, unspent apply token."""


class ApplyToken:
    """Opaque, one-shot proof that a human approved one change set."""

    __slots__ = ("turn_id", "digest", "nonce", "mac")

    def __init__(self, turn_id, digest, nonce, mac, _key=None):
        if _key is not _SENTINEL:
            raise TypeError("ApplyToken is minted by ApplyAuthority.mint(), not constructed")
        self.turn_id = turn_id
        self.digest = digest
        self.nonce = nonce
        self.mac = mac

    def __repr__(self):
        return f"<ApplyToken turn={self.turn_id} digest={self.digest[:12]}>"

    def __reduce__(self):  # no pickling a token across a process boundary
        raise TypeError("ApplyToken cannot be serialised")


class ApplyAuthority:
    """Holds the signing secret. Create with ``install()``."""

    def __init__(self, _key=None):
        if _key is not _SENTINEL:
            raise TypeError("use ApplyAuthority.install()")
        self.__secret = secrets.token_bytes(32)
        self.__spent = set()

    # ------------------------------------------------------------ lifecycle
    @classmethod
    def install(cls):
        """Create the process-wide authority. Raises if one is installed."""
        global _INSTALLED
        with _LOCK:
            if _INSTALLED is not None:
                raise RuntimeError("an ApplyAuthority is already installed")
            _INSTALLED = cls(_key=_SENTINEL)
            return _INSTALLED

    def uninstall(self):
        """Remove this authority (only its holder can)."""
        global _INSTALLED
        with _LOCK:
            if _INSTALLED is self:
                _INSTALLED = None

    # --------------------------------------------------------------- tokens
    def _mac(self, turn_id, digest, nonce):
        msg = f"{turn_id}|{digest}|{nonce}".encode()
        return hmac.new(self.__secret, msg, hashlib.sha256).hexdigest()

    def mint(self, turn):
        """Token for the turn's *current* change set (human-side only)."""
        digest = turn.digest()
        nonce = secrets.token_hex(16)
        return ApplyToken(turn.turn_id, digest, nonce, self._mac(turn.turn_id, digest, nonce), _key=_SENTINEL)

    def _check(self, token, turn_id, digest):
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
        if not hmac.compare_digest(self._mac(t_id, t_digest, t_nonce), t_mac):
            raise TokenRefused("apply token signature is invalid (forged or from another authority)")
        if t_id != turn_id:
            raise TokenRefused(f"apply token is for turn {t_id}, not {turn_id}")
        if t_digest != digest:
            raise TokenRefused("apply token was minted for a different change set")
        with _LOCK:
            if t_nonce in self.__spent:
                raise TokenRefused("apply token already spent")
            self.__spent.add(t_nonce)


def verify_token(token, turn_id, digest):
    """Check ``token`` against the installed authority; raise TokenRefused."""
    auth = _INSTALLED
    if auth is None:
        raise TokenRefused("no ApplyAuthority installed: nothing can approve agent changes")
    auth._check(token, turn_id, digest)


def installed():
    return _INSTALLED is not None
