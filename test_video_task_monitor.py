#!/usr/bin/env python3
"""Offline tests for the cloud video research worker."""
import json
import unittest
import tempfile
from pathlib import Path
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


    def test_get_transcript_prefers_saved_timestamped_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            body = "[0:00] " + ("verified transcript segment " * 15)
            (out / "2026-11-15_TEST_video123.md").write_text(
                "# Cached video\n\nTranscript method: FreeTranscriptAPI\n\n"
                "## Transcript\n\n" + body,
                encoding="utf-8",
            )
            with patch.object(worker, "OUT", out), \
                 patch.object(worker, "youtube_captions", side_effect=AssertionError("network called")), \
                 patch.object(worker, "transcript_ai", side_effect=AssertionError("network called")), \
                 patch.object(worker, "free_api", side_effect=AssertionError("network called")):
                transcript, method = worker.get_transcript("video123")
            self.assertEqual(method, "verified GitHub transcript cache")
            self.assertIn("verified transcript segment", transcript)

    def test_partial_request_remains_pending_after_transcript_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            req, out, base = root / "requests", root / "transcripts", root / "monitor_tasks"
            res, state, done = base / "results", base / "state", base / "processed"
            for directory in (req, out, res, state, done):
                directory.mkdir(parents=True, exist_ok=True)
            request = req / "TEST_20261115.json"
            request.write_text(json.dumps({
                "task_id": "TEST",
                "query": "test",
                "content": "видео",
                "limit_video": 2,
                "depth_days": 7,
            }), encoding="utf-8")
            items = [
                {"video_id": "video111111", "url": "https://www.youtube.com/watch?v=video111111",
                 "title": "Good video", "duration": 600, "timestamp": None, "query": "test"},
                {"video_id": "video222222", "url": "https://www.youtube.com/watch?v=video222222",
                 "title": "Blocked video", "duration": 600, "timestamp": None, "query": "test"},
            ]
            def transcript(vid, lang="ru"):
                if vid == "video111111":
                    return "[0:00] " + ("valid transcript " * 20), "test-cache"
                raise RuntimeError("temporarily blocked")
            with patch.object(worker, "REQ", req), \
                 patch.object(worker, "OUT", out), \
                 patch.object(worker, "ROOT", root), \
                 patch.object(worker, "BASE", base), \
                 patch.object(worker, "RES", res), \
                 patch.object(worker, "STATE", state), \
                 patch.object(worker, "DONE", done), \
                 patch.object(worker, "yt_search", return_value=items), \
                 patch.object(worker, "get_transcript", side_effect=transcript), \
                 patch.object(worker.time, "sleep", return_value=None):
                processed = worker.process_request(request)
            self.assertFalse(processed)
            self.assertFalse((done / request.name).exists())
            payload = json.loads((res / request.name).read_text(encoding="utf-8"))
            self.assertEqual(len(payload["videos"]), 1)
            self.assertEqual(len(payload["errors"]), 1)

if __name__ == "__main__":
    unittest.main()
