from __future__ import annotations

import unittest
from types import SimpleNamespace

from wb_control_center.agents.buyer_chats import BuyerChatsAgent
from wb_control_center.agents.reviews import ReviewsQuestionsAgent


class _FakeDB:
    def latest_snapshot(self, *_args):
        return None


class _FakeLLM:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.calls = []

    async def complete(self, system, prompt, *, max_tokens):
        self.calls.append((system, prompt, max_tokens))
        return "Только по одной строке нельзя утверждать о повторяющейся проблеме."


def _context(*, llm_enabled=True):
    return SimpleNamespace(
        policy=SimpleNamespace(thresholds={"reviews": {}}),
        db=_FakeDB(),
        llm=_FakeLLM(llm_enabled),
    )


class _ReviewAgent(ReviewsQuestionsAgent):
    async def call(self, tool, **kwargs):
        if tool == "wb_new_feedbacks_questions":
            return {}
        if tool == "wb_seller_rating":
            return {"rating": 4.8, "feedbacksCount": 21}
        if tool == "wb_questions_list":
            return []
        if tool == "wb_feedbacks_list" and kwargs.get("is_answered") is False:
            return []
        if tool == "wb_feedbacks_list" and kwargs.get("is_answered") is True:
            return {"data": [{"id": "r1", "productDetails": {"nmId": 123}, "productValuation": 2,
                              "text": "Пластик треснул после первой недели."}]}
        raise AssertionError((tool, kwargs))


class _ChatAgent(BuyerChatsAgent):
    async def call(self, tool, **kwargs):
        assert tool == "wb_chat_events"
        return {"data": [{"chatId": "c1", "text": "У товара нет указанного размера?"}]}


class AISummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_answered_review_summary_is_saved_with_source_count_and_timestamp(self):
        agent = _ReviewAgent(_context())
        result = await agent.run()
        summary = dict(result.snapshots)["answered_feedback_insights"]
        self.assertEqual(summary["status"], "available")
        self.assertEqual(summary["source_count"], 1)
        self.assertEqual(summary["summary"], "Только по одной строке нельзя утверждать о повторяющейся проблеме.")
        self.assertIn("analyzed_at", summary)
        self.assertEqual(len(agent.ctx.llm.calls), 1)

    async def test_disabled_language_model_is_reported_instead_of_faking_analysis(self):
        result = await _ReviewAgent(_context(llm_enabled=False)).run()
        summary = dict(result.snapshots)["answered_feedback_insights"]
        self.assertEqual(summary["status"], "not_configured")
        self.assertIsNone(summary["summary"])

    async def test_chat_triage_is_saved_without_enabling_automatic_replies(self):
        agent = _ChatAgent(_context())
        result = await agent.run()
        summary = dict(result.snapshots)["chat_triage"]
        self.assertEqual(summary["status"], "available")
        self.assertEqual(summary["source_count"], 1)
        self.assertIn("analyzed_at", summary)
        self.assertEqual(len(agent.ctx.llm.calls), 1)


if __name__ == "__main__":
    unittest.main()
