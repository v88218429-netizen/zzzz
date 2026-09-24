from __future__ import annotations

from datetime import datetime, timedelta

from .base import BaseAgent
from ..metrics import summarize_for_prompt
from ..models import AgentResult


class FinanceAgent(BaseAgent):
    name = "finance"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        try:
            balance = await self.call("wb_finance_balance")
            out.snapshots.append(("balance", balance if isinstance(balance, dict) else {"data": balance}))
        except Exception as e:
            out.events.append(self.event("warning", "balance_failed", "Не удалось получить финансовый баланс WB", str(e)))
            balance = None
        today = datetime.now().date()
        start = (today - timedelta(days=6)).isoformat()
        try:
            report = await self.call("wb_finance_report", date_from=start, date_to=today.isoformat(), limit=100000, rrd_id=0)
            out.snapshots.append(("report_7d", report if isinstance(report, dict) else {"data": report}))
        except Exception as e:
            out.events.append(self.event("warning", "finance_report_failed", "Не удалось получить отчёт реализации", str(e)))
            return out
        if self.ctx.llm.enabled:
            text = await self.ctx.llm.complete(
                "Ты финансовый контролёр Wildberries. Найди необычные штрафы, резкие расходы на логистику/хранение/комиссии и риски отрицательной экономики. Только по данным, без домыслов. Максимум 7 пунктов.",
                summarize_for_prompt({"balance": balance, "report": report}),
                max_tokens=850,
            )
            if text:
                out.events.append(self.event("info", "finance_analysis", "AI-разбор финансов WB", text))
        return out
