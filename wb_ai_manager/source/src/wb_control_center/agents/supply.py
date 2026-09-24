from __future__ import annotations

from .base import BaseAgent
from ..metrics import find_dicts_with_any_key, first_number
from ..models import AgentResult


class SupplyAgent(BaseAgent):
    name = "supply"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        try:
            data = await self.call("wb_acceptance_coefficients")
        except Exception as e:
            out.events.append(self.event("warning", "acceptance_failed", "Не удалось получить коэффициенты приёмки", str(e)))
            return out
        out.snapshots.append(("acceptance", data if isinstance(data, dict) else {"data": data}))
        good = []
        for d in find_dicts_with_any_key(data, {"coefficient", "allowUnload", "warehouseName", "warehouseID"}):
            coef = first_number(d, {"coefficient"})
            allowed = d.get("allowUnload", d.get("allow_unload", True))
            if coef is not None and coef <= 1 and allowed is not False:
                good.append(d)
        if good:
            out.events.append(self.event("info", "cheap_acceptance", "Есть выгодные окна приёмки", f"Найдено {len(good)} вариантов с коэффициентом 0–1. Можно сверить с дефицитами и планом поставки.", {"options": good[:50]}))
        return out
