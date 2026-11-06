from datetime import datetime, timezone
import unittest

from wb_control_center.engine import agent_run_due


class AgentRunDueTests(unittest.TestCase):
    def test_first_attempt_is_due(self):
        self.assertTrue(agent_run_due(None, 30, datetime(2026, 11, 6, 8, tzinfo=timezone.utc)))

    def test_attempt_is_not_due_before_interval(self):
        now = datetime(2026, 11, 6, 8, 29, tzinfo=timezone.utc)
        self.assertFalse(agent_run_due("2026-11-06T08:00:00+00:00", 30, now))

    def test_attempt_is_due_at_interval_boundary(self):
        now = datetime(2026, 11, 6, 8, 30, tzinfo=timezone.utc)
        self.assertTrue(agent_run_due("2026-11-06T08:00:00+00:00", 30, now))

    def test_offsets_are_normalized_to_utc(self):
        now = datetime(2026, 11, 6, 11, 30, tzinfo=timezone.utc)
        self.assertTrue(agent_run_due("2026-11-06T13:00:00+03:00", 30, now))

    def test_naive_legacy_timestamp_is_treated_as_utc(self):
        now = datetime(2026, 11, 6, 8, 30, tzinfo=timezone.utc)
        self.assertTrue(agent_run_due("2026-11-06T08:00:00", 30, now))

    def test_invalid_timestamp_does_not_permanently_block_agent(self):
        now = datetime(2026, 11, 6, 8, tzinfo=timezone.utc)
        self.assertTrue(agent_run_due("not-a-timestamp", 30, now))

    def test_interval_has_one_minute_floor(self):
        now = datetime(2026, 11, 6, 8, 1, tzinfo=timezone.utc)
        self.assertTrue(agent_run_due("2026-11-06T08:00:00+00:00", 0, now))

    def test_campaign_cursor_preserves_recent_completion_only_for_same_catalog(self):
        from wb_control_center.agents.advertising import _campaign_catalog_fingerprint, _scan_cursor

        marker = {
            "sweep_id": 7,
            "completed_at": "2026-11-06T08:00:00+00:00",
            "campaigns_total": 1,
            "products_total": 2,
            "pairs_total": 2,
            "catalog_fingerprint": _campaign_catalog_fingerprint([10], {10: [100, 101]}),
        }
        previous = {
            "campaign_ids": [10],
            "campaign_nm_ids": {"10": [100, 101]},
            "swept_campaign_ids": [],
            "swept_nm_ids": {},
            "sweep_id": 7,
            "last_completed_sweep": marker,
        }
        same = _scan_cursor(previous, [10], {10: [100, 101]})
        changed = _scan_cursor(previous, [10], {10: [100, 102]})
        self.assertEqual(same["last_completed_sweep"], marker)
        self.assertIsNone(changed["last_completed_sweep"])
        self.assertNotEqual(same["catalog_fingerprint"], changed["catalog_fingerprint"])

    def test_recent_snapshot_history_returns_newest_first(self):
        import tempfile
        from pathlib import Path
        from wb_control_center.db import Database

        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "state.sqlite3")
            db.save_snapshot("agent", "key", {"value": 1}, "2026-11-06T08:00:00+00:00")
            db.save_snapshot("agent", "key", {"value": 2}, "2026-11-06T08:01:00+00:00")
            rows = db.recent_snapshots("agent", "key", limit=2)
            self.assertEqual([row["data"]["value"] for row in rows], [2, 1])

    def test_campaign_cache_retains_and_invalidates_last_completion(self):
        from wb_control_center.optimizer_acceptance import merge_campaign_cache

        marker = {
            "completed_at": "2026-11-06T08:00:00+00:00",
            "campaigns_total": 1,
            "products_total": 1,
            "pairs_total": 1,
            "catalog_fingerprint": "catalog-a",
        }
        previous = {
            "campaign_ids": ["10"],
            "campaigns": [{"campaign_id": 10, "nm_ids": [100], "scan_complete": True}],
            "last_completed_sweep": marker,
        }
        partial = {
            "active_campaign_ids": [10],
            "catalog_complete": True,
            "catalog_changed": False,
            "catalog_fingerprint": "catalog-a",
            "last_completed_sweep": marker,
            "sweep_complete": False,
            "sweep_campaigns_total": 1,
            "campaigns": [],
        }
        kept = merge_campaign_cache(previous, partial, "2026-11-06T08:10:00+00:00")
        self.assertEqual(kept["last_completed_sweep"], marker)
        changed = merge_campaign_cache(previous, {
            **partial, "catalog_changed": True, "catalog_fingerprint": "catalog-b",
        }, "2026-11-06T08:10:00+00:00")
        self.assertIsNone(changed["last_completed_sweep"])
