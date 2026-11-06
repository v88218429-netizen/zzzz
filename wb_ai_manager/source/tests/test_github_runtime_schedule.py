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
