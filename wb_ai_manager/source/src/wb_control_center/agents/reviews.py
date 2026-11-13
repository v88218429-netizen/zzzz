from __future__ import annotations

from datetime import datetime, timezone

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
        try:
            answered = await self.call("wb_feedbacks_list", is_answered=True, take=100)
            answered_data = answered if isinstance(answered, dict) else {"data": answered}
            out.snapshots.append(("answered_feedbacks", answered_data))
        except Exception as exc:
            answered_data = None
            out.snapshots.append(("answered_feedbacks", {
                "source_status": "unavailable",
                "reason": "WB API не вернул список отвеченных отзывов (" + type(exc).__name__ + ").",
            }))
        answered_count = count_records(answered_data) if answered_data is not None else 0
        if answered_data is None:
            ai_review_summary = {
                "status": "unavailable", "source_count": 0, "summary": None,
                "note": "WB не вернул список отвеченных отзывов.",
            }
        elif not answered_count:
            ai_review_summary = {
                "status": "empty", "source_count": 0, "summary": None,
                "note": "В снимке нет отвеченных отзывов для разбора.",
            }
        else:
            previous_answered = self.ctx.db.latest_snapshot(self.name, "answered_feedbacks")
            previous_summary = self.ctx.db.latest_snapshot(self.name, "answered_feedback_insights")
            changed = previous_answered is None or previous_answered.get("data") != answered_data
            previous_data = previous_summary.get("data") if isinstance(previous_summary, dict) else None
            if not changed and isinstance(previous_data, dict):
                ai_review_summary = previous_data
            elif not self.ctx.llm.enabled:
                ai_review_summary = {
                    "status": "not_configured", "source_count": answered_count, "summary": None,
                    "note": "Языковая модель не подключена; доступны только фактические оценки и примеры отзывов.",
                }
            else:
                try:
                    text = await self.ctx.llm.complete(
                        "Ты аналитик отзывов Wildberries. Используй только переданные отзывы. Кратко выдели повторяющиеся положительные и отрицательные темы, вопросы о характеристиках и конкретные замечания к товару. Не выдумывай частоты, свойства товара или причины. Если nmID указан, не меняй его. Если отзывов недостаточно для вывода, скажи это явно. Не предлагай вознаграждение за положительный отзыв и не пиши ответ покупателю.",
                        summarize_for_prompt(answered_data),
                        max_tokens=900,
                    )
                    ai_review_summary = {
                        "status": "available" if text else "empty", "source_count": answered_count,
                        "summary": text or None, "note": "Разбор языковой моделью только по строкам отвеченных отзывов в снимке.",
                        "analyzed_at": datetime.now(timezone.utc).isoformat(),
                    }
                except Exception as exc:
                    ai_review_summary = {
                        "status": "failed", "source_count": answered_count, "summary": None,
                        "note": "Разбор не выполнен: " + type(exc).__name__,
                        "analyzed_at": datetime.now(timezone.utc).isoformat(),
                    }
        out.snapshots.append(("answered_feedback_insights", ai_review_summary))
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
