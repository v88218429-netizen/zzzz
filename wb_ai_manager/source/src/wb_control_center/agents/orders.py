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
        return out
