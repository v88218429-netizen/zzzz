from __future__ import annotations

from .base import BaseAgent
from ..metrics import count_records, summarize_for_prompt
from ..models import AgentResult


class ReviewsQuestionsAgent(BaseAgent):
    name = "reviews_questions"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        cfg = self.ctx.policy.thresholds.get("reviews", {})
        flags = await self.call("wb_new_feedbacks_questions")
        try:
            rating = await self.call("wb_seller_rating")
            out.snapshots.append(("seller_rating", rating if isinstance(rating, dict) else {"data": rating}))
        except Exception:
            rating = None
        out.snapshots.append(("flags", flags if isinstance(flags, dict) else {"data": flags}))
        feedbacks = await self.call("wb_feedbacks_list", is_answered=False, take=100)
        questions = await self.call("wb_questions_list", is_answered=False, take=100)
        out.snapshots.append(("feedbacks", feedbacks if isinstance(feedbacks, dict) else {"data": feedbacks}))
        out.snapshots.append(("questions", questions if isinstance(questions, dict) else {"data": questions}))
        n_feedback = count_records(feedbacks)
        n_questions = count_records(questions)
        warn = int(cfg.get("unanswered_warn", 5))
        if n_feedback >= warn:
            out.events.append(self.event("warning", "unanswered_feedbacks", "Накопились неотвеченные отзывы", f"Неотвеченных отзывов: примерно {n_feedback}.", {"data": feedbacks}))
        if n_questions >= warn:
            out.events.append(self.event("warning", "unanswered_questions", "Накопились вопросы покупателей", f"Неотвеченных вопросов: примерно {n_questions}.", {"data": questions}))
        if self.ctx.llm.enabled and (n_feedback or n_questions):
            text = await self.ctx.llm.complete(
                "Ты менеджер Wildberries. Кратко классифицируй новые отзывы и вопросы: критичный негатив, повторяющаяся проблема товара, вопрос о характеристиках, обычный позитив. Не пиши ответы от лица продавца, только приоритеты.",
                summarize_for_prompt({"feedbacks": feedbacks, "questions": questions}),
                max_tokens=700,
            )
            if text:
                out.events.append(self.event("info", "feedback_triage", "AI-сортировка отзывов и вопросов", text))
        return out
