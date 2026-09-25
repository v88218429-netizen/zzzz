from __future__ import annotations

from datetime import datetime, timedelta

from .base import BaseAgent
from ..metrics import summarize_for_prompt
from ..models import AgentResult


class FunnelAgent(BaseAgent):
    name = "funnel"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        start, end = self.dates(7)
        try:
            data = await self.call("wb_analytics_detail", date_from=start, date_to=end, limit=1000)
        except Exception as e:
            out.events.append(self.event("warning", "funnel_failed", "Не удалось получить воронку продаж", str(e)))
            return out
        out.snapshots.append(("funnel_7d", data if isinstance(data, dict) else {"data": data}))
        if self.ctx.llm.enabled:
            text = await self.ctx.llm.complete(
                "Ты аналитик Wildberries. Ищи только значимые падения этапов воронки и объясняй, на каком этапе проблема: показы, карточка, корзина, заказ. Не выдумывай отсутствующие цифры.",
                summarize_for_prompt(data),
                max_tokens=700,
            )
            if text:
                out.events.append(self.event("info", "funnel_analysis", "AI-разбор воронки", text))
        return out
