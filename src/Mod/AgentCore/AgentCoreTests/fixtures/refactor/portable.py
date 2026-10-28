# SPDX-License-Identifier: LGPL-2.1-or-later
"""Make committed eval results machine-independent.

Result files under ``results/`` are public. ``scrub`` rewrites every absolute path that points into the
FreeCAD source tree to ``$FC_SRC/...`` and every path inside the run's scratch directory to ``$WORK/...``,
so no home directory, user name or temp path reaches the repository. Nothing reads these path fields
back: they only document which document a row came from.
"""
import json
import os
import re

# A claude-code scratch dir (/private/tmp/claude-<uid>/<project>/<session>/scratchpad/) is also a scratch root.
_SCRATCH = re.compile(r"/private/tmp/claude-\d+/[^/\"\\]+/[^/\"\\]+/scratchpad/")


def scrub_text(text, fc_src=None, work_dirs=()):
    roots = [(os.path.realpath(fc_src) if fc_src else None, "$FC_SRC/"), (fc_src, "$FC_SRC/")]
    roots += [(w, "$WORK/") for d in work_dirs if d for w in (os.path.abspath(d), os.path.realpath(d))]
    # Longest root first, so a work dir nested under the source tree still maps to $WORK.
    for root, token in sorted((r for r in roots if r[0]), key=lambda r: -len(r[0])):
        text = text.replace(root.rstrip("/") + "/", token)
    return _SCRATCH.sub("$WORK/", text)


def dump_portable(obj, path, fc_src=None, work_dirs=(), **kw):
    """json.dump with the paths scrubbed (the JSON escaping of '/' is the identity, so text replace is safe)."""
    text = scrub_text(json.dumps(obj, **kw), fc_src, work_dirs)
    with open(path, "w") as fh:
        fh.write(text)
