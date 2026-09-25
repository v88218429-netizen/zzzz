from __future__ import annotations

from .base import BaseAgent
from ..metrics import count_records
from ..models import AgentResult


class OrdersFBSAgent(BaseAgent):
    name = "orders_fbs"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        orders = await self.call("wb_orders_new")
        out.snapshots.append(("new_orders", orders if isinstance(orders, dict) else {"data": orders}))
        try:
            reship = await self.call("wb_supplies_reshipment")
            out.snapshots.append(("reshipment", reship if isinstance(reship, dict) else {"data": reship}))
            n = count_records(reship)
            if n:
                out.events.append(self.event("critical", "reshipment", "Есть заказы на повторную отгрузку", f"Количество: {n}. Требуется быстро проверить FBS-операцию.", {"data": reship}))
        except Exception as e:
            out.events.append(self.event("warning", "reshipment_failed", "Не удалось проверить повторную отгрузку", str(e)))
        if self.ctx.worker is not None and self.ctx.worker.configured:
            try:
                worker_fbs = await self.ctx.worker.fbs_bundle()
                out.snapshots.append(("worker_fbs", worker_fbs))
                states = {(worker_fbs.get("supply_health") or {}).get("status"), (worker_fbs.get("evidence_health") or {}).get("status")}
                if any(s not in {None, "FRESH"} for s in states):
                    out.events.append(self.event("warning", "worker_fbs_stale", "FBS-контур wb-api-worker не полностью свежий", str({"supply": worker_fbs.get("supply_health"), "evidence": worker_fbs.get("evidence_health")})))
            except Exception as e:
                out.events.append(self.event("warning", "worker_fbs_failed", "Не удалось получить FBS-контур wb-api-worker", str(e)))
        return out
