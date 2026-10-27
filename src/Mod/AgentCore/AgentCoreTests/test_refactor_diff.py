# SPDX-License-Identifier: LGPL-2.1-or-later
"""F6 with F3: a refactor followed by reject leaves the document identical.

Skipped cleanly while ``AgentCore.diff.AgentTurn`` does not exist (F3 is built in parallel).
"""

import json
import unittest

import FreeCAD

from AgentCore import diff, refactor, state
from AgentCore.refactor import geomhash
from AgentCoreTests.fixtures.refactor import refactor_builders as rb

AgentTurn = getattr(diff, "AgentTurn", None)


@unittest.skipIf(AgentTurn is None, "AgentCore.diff.AgentTurn is not present yet (F3 pending)")
class TestRefactorThenReject(unittest.TestCase):
    def test_reject_restores_state_and_geometry(self):
        doc = FreeCAD.newDocument("F6reject")
        try:
            body, sk, pad, sk2, pk = rb.plate_with_slot(doc)
            rb.add_noop_pattern(body, pk, sk)
            state_before = json.dumps(state.dump(doc), sort_keys=True, default=str)
            snap_before = geomhash.snapshot(doc)
            with AgentTurn(doc) as turn:
                refactor.relabel_all(doc)
                refactor.lift_all(doc)
                refactor.merge_redundant(doc)
                doc.recompute(None, True)
            self.assertTrue(turn.change_set())
            res = turn.reject()
            self.assertTrue(res["equal"], res)
            doc.recompute(None, True)
            self.assertEqual(json.dumps(state.dump(doc), sort_keys=True, default=str), state_before)
            self.assertEqual(geomhash.compare(snap_before, geomhash.snapshot(doc)), [])
            self.assertEqual(doc.getObject("Pad").Label, "Pad")
            self.assertIsNotNone(doc.getObject("NoopPattern"))
            self.assertIsNone(doc.getObject("Parameters"))
        finally:
            FreeCAD.closeDocument(doc.Name)


class TestRejectViaTransactionFallback(unittest.TestCase):
    """Independent of F3: the invariant's own rollback restores an identical document."""

    def test_rollback_identical_state(self):
        doc = FreeCAD.newDocument("F6rollback")
        try:
            body, sk, pad, sk2, pk = rb.plate_with_slot(doc)
            state_before = json.dumps(state.dump(doc), sort_keys=True, default=str)

            def bad(d):
                refactor.relabel_all(d)
                refactor.lift_all(d)
                d.getObject("Pad").setExpression("Length", None)
                d.getObject("Pad").Length = 11
                return None

            r = refactor.invariant(doc, bad)
            self.assertFalse(r["ok"])
            self.assertTrue(r["restored_identical"])
            self.assertEqual(json.dumps(state.dump(doc), sort_keys=True, default=str), state_before)
        finally:
            FreeCAD.closeDocument(doc.Name)


if __name__ == "__main__":
    unittest.main()
