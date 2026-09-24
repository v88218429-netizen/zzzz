from __future__ import annotations

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
                text = await self.ctx.llm.complete(
                    "Ты менеджер поддержки Wildberries. Классифицируй новые события чатов: срочная жалоба, правообладатель/юридический риск, доставка/FBS, обычный вопрос. Не отправляй ответы автоматически.",
                    summarize_for_prompt(events),
                    max_tokens=600,
                )
                msg = text or f"Новых/изменившихся событий чата: примерно {n}."
            else:
                msg = f"Новых/изменившихся событий чата: примерно {n}."
            out.events.append(self.event("warning", "new_chat_events", "Новые события в чатах покупателей", msg, {"data": events}))
        return out
