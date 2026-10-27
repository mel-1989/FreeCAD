# SPDX-License-Identifier: LGPL-2.1-or-later
"""Worker entry, executed inside FreeCADCmd: exec(open(this_file).read()).

Reads the job from the JSON file named by CADFORK_VERIFY_JOB, runs one check on
the document, writes the result JSON atomically. A crash (abort, SIGKILL,
segfault) leaves no result file; the pool reports it as a structured crash.
"""

import json
import os
import sys
import traceback

_job_path = os.environ["CADFORK_VERIFY_JOB"]
with open(_job_path, encoding="utf-8") as _fh:
    _job = json.load(_fh)
if _job.get("pythonpath") and _job["pythonpath"] not in sys.path:
    sys.path.insert(0, _job["pythonpath"])


def _main():
    import FreeCAD

    from AgentCore.verify.checks import run_one

    spec = _job["check"]
    name = spec if isinstance(spec, str) else spec.get("check")
    try:
        doc = FreeCAD.openDocument(_job["fcstd"]) if _job.get("fcstd") else FreeCAD.newDocument("verify")
    except Exception:
        return {"check": name, "ok": False, "status": "error", "entities": [],
                "details": {"error": "cannot open document: " + traceback.format_exc(limit=4)}}
    return run_one(doc, spec)


_res = _main()
_tmp = _job["out"] + ".tmp"
with open(_tmp, "w", encoding="utf-8") as _fh:
    json.dump(_res, _fh)
os.replace(_tmp, _job["out"])
sys.stdout.flush()
sys.stderr.flush()
os._exit(0)
