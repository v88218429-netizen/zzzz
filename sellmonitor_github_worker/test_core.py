import unittest
from datetime import datetime, timezone
from pathlib import Path

from core import ClientState, Stage, acquire_lock, evaluate_qc, run_until_terminal


GOOD_QC = {
    "ok": True,
    "wb_connected": True,
    "sm_connected": True,
    "sku_count": 10,
    "backfill_complete": True,
    "d1_complete": True,
    "rnp_complete": True,
    "ads_complete": True,
    "traffic_complete": True,
    "search_complete": True,
    "stocks_complete": True,
    "duplicate_keys": 0,
    "formula_errors": 0,
    "stale_jobs": 0,
}


class CoreTests(unittest.TestCase):
    def test_dashboard_publisher_keeps_full_actions_and_diagnostics(self):
        source = (Path(__file__).resolve().parent / "SELLMONITOR_GITHUB_WORKER.gs").read_text(encoding="utf-8")
        self.assertIn("(dashboard.top_actions || []).forEach(function(x) {", source)
        self.assertIn("(dashboard.data_blockers || []).forEach(function(x) {", source)
        self.assertNotIn("(dashboard.top_actions || []).slice(0, 25)", source)
        self.assertNotIn("(dashboard.data_blockers || []).slice(0, 30)", source)
        self.assertIn("economics_diagnostics_by_shop", source)
        self.assertIn("if (currentRows < rows.length) sh.insertRowsAfter", source)

    def test_qc_fail_closed(self):
        bad = dict(GOOD_QC)
        bad["traffic_complete"] = False
        result = evaluate_qc(bad)
        self.assertFalse(result.ok)
        self.assertIn("traffic_complete", result.error)

    def test_lock(self):
        state = ClientState("c1", "s1")
        now = datetime(2026, 10, 3, tzinfo=timezone.utc)
        self.assertTrue(acquire_lock(state, "a", now=now))
        self.assertFalse(acquire_lock(state, "b", now=now))

    def test_full_state_machine_reaches_ready(self):
        state = ClientState("c1", "s1")
        handlers = {
            Stage.NEW: lambda _: {"ok": True},
            Stage.WB_CONNECTED: lambda _: {"ok": True},
            Stage.SM_CONNECTED: lambda _: {"ok": True},
            Stage.SKU_READY: lambda _: {"ok": True},
            Stage.BACKFILL: lambda _: {"ok": True},
            Stage.ANALYTICS: lambda _: {"ok": True},
            Stage.QC: lambda _: dict(GOOD_QC),
        }
        state, history = run_until_terminal(state, handlers, owner="test")
        self.assertEqual(state.stage, Stage.READY)
        self.assertEqual(state.qc_status, "PASS")
        self.assertTrue(state.ready_at)
        self.assertEqual(len(history), 7)

    def test_missing_handler_never_marks_ready(self):
        state = ClientState("c1", "s1")
        handlers = {Stage.NEW: lambda _: {"ok": True}}
        state, _ = run_until_terminal(state, handlers, owner="test")
        self.assertNotEqual(state.stage, Stage.READY)
        self.assertTrue(state.error)


if __name__ == "__main__":
    unittest.main()
