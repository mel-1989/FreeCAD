# SPDX-License-Identifier: LGPL-2.1-or-later
"""The GUI process's approver: the one holder of the apply key.

The GUI is the human side (``diff/SECURITY.md``). It creates one ``Approver`` lazily, the first time a
human presses Accept or Reject, and keeps it in this module's closure. The key is not exposed by any
public attribute, and neither the panel nor the backends hold the approver object: they get its bound
``approve`` and ``deny`` methods. Tokens are produced only in ``Approver.approve``, called from a
backend's ``accept`` and so from the panel's Accept button; there is no other caller.
"""

from ..diff.approver import Approver


def _make():
    box = []

    def get():
        if not box:
            box.append(Approver())
        return box[0]

    def reset():
        """Test hook: forget the approver and release the authority (a fresh one is built on demand)."""
        if box:
            box[0]._auth.uninstall()
            box.clear()

    return get, reset


approver, _reset = _make()
