from __future__ import annotations

from .base import BaseAgent
from ..models import AgentResult
from ..metrics import count_records


class ApiHealthAgent(BaseAgent):
    name = "api_health"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        shops = await self.call("wb_list_shops")
        out.snapshots.append(("shops", shops if isinstance(shops, dict) else {"data": shops}))
        try:
            token_info = await self.call("wb_token_info")
            out.snapshots.append(("token_info", token_info if isinstance(token_info, dict) else {"data": token_info}))
        except Exception as e:
            out.events.append(self.event("warning", "token_info_failed", "Не удалось проверить токен WB", str(e)))
        try:
            deg = await self.call("wb_degradations")
        except Exception as e:
            out.events.append(self.event("warning", "degradations_check_failed", "Не удалось проверить состояние WB API", str(e)))
            return out
        out.snapshots.append(("degradations", deg if isinstance(deg, dict) else {"data": deg}))
        n = count_records(deg)
        if n:
            out.events.append(self.event("warning", "wb_api_degradation", "WB API: обнаружены деградации", f"MCP сообщил о {n} проблемных/изменившихся элементах API. Проверь диагностику.", {"count": n, "data": deg}))
        return out
