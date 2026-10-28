# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run inside the FreeCAD GUI (offscreen): one cross-document agent turn, accepted, then ONE Std_Undo.

Started by test_diff_xlink.TestXLinkGuiUndo, which reads the JSON this writes to $PROBE_OUT.
"""
import json
import os
import sys
import tempfile
import traceback

out = {}
try:
    src_root, fixtures = os.environ["PROBE_SRC"], os.environ["PROBE_FIX"]
    sys.path[:0] = [src_root, fixtures]
    import FreeCAD as App
    import FreeCADGui as Gui
    import diff_builders as db
    from AgentCore.diff import AgentTurn, ApplyAuthority

    auth = ApplyAuthority.install()
    src, asm = db.build_xlink_pair(tempfile.mkdtemp(prefix="f3g"))
    src.clearUndos()
    asm.clearUndos()
    out["gui_documents"] = [Gui.getDocument(d.Name) is not None for d in (src, asm)]
    before = (src.SrcBox.Length.Value, asm.LinkBox.Placement.Base.y)
    with AgentTurn(asm, label="gui undo", verify=False) as t:
        asm.LinkBox.Placement.Base = App.Vector(1, 2, 3)
        asm.LinkBox.LinkedObject.Length = 20                  # lands in the source document
    res = t.accept(auth.mint(t))
    out["shared_transaction"] = res["shared_transaction"]
    out["undo_counts_after_accept"] = [src.UndoCount, asm.UndoCount]
    out["changed"] = [src.SrcBox.Length.Value, asm.LinkBox.Placement.Base.y]
    out["before"] = list(before)
    Gui.setActiveDocument(asm.Name)
    Gui.runCommand("Std_Undo")                                # the application-level undo, once
    out["undo_counts_after_undo"] = [src.UndoCount, asm.UndoCount]
    out["after_undo"] = [src.SrcBox.Length.Value, asm.LinkBox.Placement.Base.y]
except Exception:
    out["error"] = traceback.format_exc()
with open(os.environ["PROBE_OUT"], "w") as fh:
    json.dump(out, fh)
os._exit(0)
