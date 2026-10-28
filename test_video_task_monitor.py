#!/usr/bin/env python3
"""Offline tests for the cloud video research worker."""
import json
import unittest
from unittest.mock import patch
import video_task_monitor as worker


class WorkerTests(unittest.TestCase):
    def test_named_author_search_rejects_unrelated_travel(self):
        self.assertFalse(worker.relevant_to_task("IMRAM", {
            "title": "МОЙ ПЕРВЫЙ ДЕНЬ В ДАТЕ | ТЮРКИЕ", "channel": "Sezer Yılmaz"}))
        self.assertTrue(worker.relevant_to_task("IMRAM", {
            "title": "Как правильно отмечать день рождения", "channel": "Имрам Крийя - Йога"}))
        self.assertTrue(worker.relevant_to_task("WB_MONETIZATION", {
            "title": "Сервис для селлеров", "channel": "Marketplaces"}))

    def test_youtube_search_uses_supported_extractor(self):
        response = {"entries": [{"id": "video123", "title": "Test", "uploader": "Channel"}]}
        with patch.object(worker, "run", return_value=json.dumps(response)) as mocked:
            items = worker.yt_search("test query", 2)
        self.assertEqual(items[0]["video_id"], "video123")
        self.assertTrue(mocked.call_args.args[0][-1].startswith("ytsearch2:"))


if __name__ == "__main__":
    unittest.main()
