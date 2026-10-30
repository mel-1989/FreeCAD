# SPDX-License-Identifier: LGPL-2.1-or-later
"""The two sides of the review panel behind one small interface.

``LocalTurnBackend``  an ``AgentTurn`` running in this process. Accept mints a token through the GUI's
                      approver for the change set the human was shown, and hands it to ``turn.accept``.
``RemoteTurnBackend`` a turn running in an F4 MCP worker. The GUI never sees its shapes or its document;
                      Accept calls ``Approver.approve(turn_id, digest)`` and the worker polls for the token.
                      Reject calls ``Approver.deny``.

Both bind the decision to the digest of the change set that was shown. If the change set changed
afterwards, Accept refuses (``StaleChangeSet``) before any token exists; and a token minted for the
shown digest is refused by ``turn.accept`` or by the approver's check if the change set is another one.
"""

from ..diff import compare
from ..diff.authority import ApplyToken
from . import session


class StaleChangeSet(RuntimeError):
    """The change set or the document differs from what the human reviewed."""


def digest_of(cs):
    """Same digest ``AgentTurn.digest`` computes: everything but the timing-bound ``verify`` block."""
    cs = dict(cs)
    cs.pop("verify", None)
    return compare.digest(cs)


class LocalTurnBackend:
    kind = "local"

    def __init__(self, turn):
        if turn.status != "proposed":
            raise ValueError(f"only a proposed turn can be reviewed, this one is {turn.status}")
        self._turn = turn
        self.turn_id, self.label, self.document = turn.turn_id, turn.label, turn.doc.Name
        self._shown = turn.digest()
        self._cs = turn.change_set()

    def change_set(self):
        return self._cs

    def shown_digest(self):
        return self._shown

    def overlay_pieces(self):
        return self._turn.overlay_pieces()

    def status(self):
        return self._turn.status

    def check_current(self):
        """Why Accept would be refused now, or an empty list."""
        out = []
        if self._turn.status != "proposed":
            return [f"the turn is {self._turn.status}"]
        if self._turn.digest() != self._shown:
            out.append("the change set changed after it was shown")
        out += self._turn.live_differences()[:5]
        return out

    def accept(self):
        turn = self._turn
        if turn.digest() != self._shown:
            raise StaleChangeSet("the change set changed after it was shown; review it again")
        wire = session.approver().approve(turn.turn_id, self._shown)
        try:
            return turn.accept(ApplyToken.from_wire(wire))
        except BaseException:
            session.approver().deny(turn.turn_id, self._shown)   # nothing stays approved after a refusal
            raise

    def reject(self):
        return self._turn.reject()


class RemoteTurnBackend:
    kind = "remote"

    def __init__(self, turn_id, fetch_change_set, label=None):
        self.turn_id = str(turn_id)
        self._fetch = fetch_change_set
        self._cs = fetch_change_set()
        self._shown = digest_of(self._cs)
        self.label = label or self._cs.get("label", self.turn_id)
        self.document = self._cs.get("document")
        self._decision = "proposed"

    def change_set(self):
        return self._cs

    def shown_digest(self):
        return self._shown

    def overlay_pieces(self):
        return []   # shapes stay in the worker; the overlay is local-turn only

    def status(self):
        return self._decision

    def check_current(self):
        try:
            return [] if digest_of(self._fetch()) == self._shown else ["the change set changed after it was shown"]
        except Exception as exc:
            return [f"the worker's change set could not be read: {exc}"]

    def accept(self):
        if self.check_current():
            raise StaleChangeSet("the change set changed after it was shown; review it again")
        wire = session.approver().approve(self.turn_id, self._shown)
        self._decision = "approved"
        return {"decision": "approved", "turn_id": self.turn_id, "digest": self._shown,
                "token_released_to_worker": bool(wire)}

    def reject(self):
        session.approver().deny(self.turn_id, self._shown)
        self._decision = "denied"
        return {"decision": "denied", "turn_id": self.turn_id}
