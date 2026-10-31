# SPDX-License-Identifier: LGPL-2.1-or-later
"""Administrator recovery commands (run on the server with the service's environment).

    python -m pdm_server.admin show            print the pinned (last approved) vault head and any pending push
    python -m pdm_server.admin pin <sha>       declare <sha> the vault head after an admin has checked it

Use ``pin`` after repairing the vault branch by hand (see the README, Recovery).
"""

import re
import sys

from .config import Config
from .db import Db


def main(argv):
    db = Db(Config().database_url)
    db.init()
    if argv[:1] == ["show"]:
        print("pinned, pending =", db.pin())
    elif len(argv) == 2 and argv[0] == "pin" and re.fullmatch(r"[0-9a-f]{40}", argv[1]):
        db.set_pin(argv[1], None)
        db.log("admin-cli", "pin-head", None, {"head": argv[1]})
        print("pinned", argv[1])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
