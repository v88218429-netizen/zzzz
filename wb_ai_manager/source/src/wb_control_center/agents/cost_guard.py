from __future__ import annotations

from datetime import datetime, timedelta

from .base import BaseAgent
from ..metrics import count_records, summarize_for_prompt
from ..models import AgentResult


class CostGuardAgent(BaseAgent):
    name = "cost_guard"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        start, end = self.dates(7)
        checks = [
            ("wb_paid_storage", {"date_from": start, "date_to": end}, "paid_storage"),
            ("wb_analytics_measurement_penalties", {"date_from": start, "date_to": end}, "measurement_penalties"),
            ("wb_deductions", {"date_from": start, "date_to": end, "limit": 1000}, "deductions"),
            ("wb_analytics_acceptance", {"date_from": start, "date_to": end}, "paid_acceptance"),
        ]
        collected = {}
        for tool, args, key in checks:
            try:
                data = await self.call(tool, **args)
                collected[key] = data
                out.snapshots.append((key, data if isinstance(data, dict) else {"data": data}))
            except Exception as e:
                out.events.append(self.event("warning", f"{key}_failed", f"Не удалось проверить {key}", str(e)))
        if self.ctx.worker is not None and self.ctx.worker.configured:
            try:
                worker_fbs = await self.ctx.worker.fbs_bundle()
                evidence_snapshot = {
                    "source": worker_fbs.get("source"),
                    "retrieved_at": worker_fbs.get("retrieved_at"),
                    "evidence_health": worker_fbs.get("evidence_health"),
                    "evidence_status": worker_fbs.get("evidence_status"),
                    "penalty_evidence": worker_fbs.get("penalty_evidence", []),
                }
                out.snapshots.append(("worker_penalty_evidence", evidence_snapshot))
                collected["worker_penalty_evidence"] = evidence_snapshot
                if (worker_fbs.get("evidence_health") or {}).get("status") != "FRESH":
                    out.events.append(self.event("warning", "worker_penalty_evidence_stale", "FBS evidence wb-api-worker не свежий", str(worker_fbs.get("evidence_health") or {})))
            except Exception as e:
                out.events.append(self.event("warning", "worker_penalty_evidence_failed", "Не удалось получить FBS evidence wb-api-worker", str(e)))
        if self.ctx.llm.enabled and collected:
            text = await self.ctx.llm.complete(
                "Ты контролёр скрытых расходов Wildberries. Найди существенные платное хранение, платную приёмку, штрафы за габариты, подмены/вложения и другие удержания. Не выдумывай суммы. Сначала самые дорогие отклонения.",
                summarize_for_prompt(collected),
                max_tokens=800,
            )
            if text:
                out.events.append(self.event("info", "cost_analysis", "AI-контроль скрытых расходов", text))
        return out
