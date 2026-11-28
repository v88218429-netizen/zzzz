from __future__ import annotations

from datetime import datetime, timezone

from .base import BaseAgent
from ..metrics import count_records, summarize_for_prompt
from ..models import AgentResult


class BuyerChatsAgent(BaseAgent):
    name = "buyer_chats"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        try:
            events = await self.call("wb_chat_events")
        except Exception as e:
            out.events.append(self.event("warning", "chat_events_failed", "Не удалось проверить чаты покупателей", str(e)))
            return out
        out.snapshots.append(("chat_events", events if isinstance(events, dict) else {"data": events}))
        n = count_records(events)
        previous = self.ctx.db.latest_snapshot(self.name, "chat_events")
        # Do not spam on a stable snapshot; only surface when AI can classify or the snapshot changed materially.
        changed = previous is None or previous.get("data") != (events if isinstance(events, dict) else {"data": events})
        if n and changed:
            if self.ctx.llm.enabled:
                try:
                    text = await self.ctx.llm.complete(
                        "Ты аналитик очереди поддержки Wildberries. Используй только текст переданных событий. Сгруппируй срочные жалобы, юридические/правообладательские риски, вопросы доставки/FBS, вопросы о характеристиках товара и обычные вопросы. Укажи повторяющиеся темы только если они реально повторяются в выборке. Не придумывай статус заказа или свойства товара. Не формируй готовое обещание покупателю и не отправляй сообщения.",
                        summarize_for_prompt(events),
                        max_tokens=700,
                    )
                    triage = {
                        "status": "available" if text else "empty", "source_count": n,
                        "summary": text or None, "note": "Сводный разбор языковой моделью по событиям в снимке; это не гарантирует полную историю каждого чата.",
                        "analyzed_at": datetime.now(timezone.utc).isoformat(),
                    }
                    msg = text or f"Новых/изменившихся событий чата: примерно {n}."
                except Exception as exc:
                    triage = {
                        "status": "failed", "source_count": n, "summary": None,
                        "note": "Разбор не выполнен: " + type(exc).__name__,
                        "analyzed_at": datetime.now(timezone.utc).isoformat(),
                    }
                    msg = f"Новых/изменившихся событий чата: примерно {n}; языковая модель вернула ошибку {type(exc).__name__}."
            else:
                msg = f"Новых/изменившихся событий чата: примерно {n}."
                triage = {
                    "status": "not_configured", "source_count": n, "summary": None,
                    "note": "Языковая модель не подключена; доступна только предварительная сортировка по правилам.",
                }
            out.snapshots.append(("chat_triage", triage))
            out.events.append(self.event("warning", "new_chat_events", "Новые события в чатах покупателей", msg, {"data": events}))
        elif not n:
            out.snapshots.append(("chat_triage", {
                "status": "empty", "source_count": 0, "summary": None,
                "note": "В текущем снимке нет событий чатов для разбора.",
            }))
        return out
