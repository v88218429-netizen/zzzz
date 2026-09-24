from __future__ import annotations

from datetime import datetime, timedelta

from .base import BaseAgent
from ..metrics import count_records, summarize_for_prompt
from ..models import AgentResult


class CostGuardAgent(BaseAgent):
    name = "cost_guard"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        today = datetime.now().date()
        start = (today - timedelta(days=6)).isoformat()
        checks = [
            ("wb_paid_storage", {"date_from": start, "date_to": today.isoformat()}, "paid_storage"),
            ("wb_analytics_measurement_penalties", {"date_from": start, "date_to": today.isoformat()}, "measurement_penalties"),
            ("wb_deductions", {"date_from": start, "date_to": today.isoformat(), "limit": 1000}, "deductions"),
            ("wb_analytics_acceptance", {"date_from": start, "date_to": today.isoformat()}, "paid_acceptance"),
        ]
        collected = {}
        for tool, args, key in checks:
            try:
                data = await self.call(tool, **args)
                collected[key] = data
                out.snapshots.append((key, data if isinstance(data, dict) else {"data": data}))
            except Exception as e:
                out.events.append(self.event("warning", f"{key}_failed", f"Не удалось проверить {key}", str(e)))
        if self.ctx.llm.enabled and collected:
            text = await self.ctx.llm.complete(
                "Ты контролёр скрытых расходов Wildberries. Найди существенные платное хранение, платную приёмку, штрафы за габариты, подмены/вложения и другие удержания. Не выдумывай суммы. Сначала самые дорогие отклонения.",
                summarize_for_prompt(collected),
                max_tokens=800,
            )
            if text:
                out.events.append(self.event("info", "cost_analysis", "AI-контроль скрытых расходов", text))
        return out
