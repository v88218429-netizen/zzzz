from __future__ import annotations

from .base import BaseAgent
from ..models import AgentResult
from ..metrics import count_records


def _degradation_count(value) -> int:
    """Count only actual degradation evidence, not metadata-only OK responses."""
    if isinstance(value, list):
        return len(value)
    if not isinstance(value, dict):
        return 0
    status = str(value.get("status") or "").strip().lower()
    if status in {"ok", "healthy", "success", "normal"}:
        return 0
    for key in ("degradations", "problems", "issues", "items", "changes"):
        if key in value:
            return count_records(value.get(key))
    data = value.get("data")
    if isinstance(data, (list, dict)):
        nested = _degradation_count(data)
        if nested:
            return nested
    if status in {"degraded", "warning", "error", "failed", "down"} or value.get("error") is True:
        return 1
    return 0


class ApiHealthAgent(BaseAgent):
    name = "api_health"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        shops = await self.call("wb_list_shops")
        out.snapshots.append(("shops", shops if isinstance(shops, dict) else {"data": shops}))
        if self.ctx.worker is not None and self.ctx.worker.configured:
            try:
                worker_health = await self.ctx.worker.health_summary()
                out.snapshots.append(("worker_source_health", worker_health))
                if worker_health.get("status") != "FRESH":
                    out.events.append(self.event("warning", "worker_source_degraded", "Внутренний источник wb-api-worker не полностью свежий", str(worker_health.get("streams") or {}), worker_health))
            except Exception as e:
                out.events.append(self.event("warning", "worker_source_failed", "Не удалось проверить wb-api-worker", str(e)))
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
        n = _degradation_count(deg)
        if n:
            out.events.append(self.event("warning", "wb_api_degradation", "WB API: обнаружены деградации", f"MCP сообщил о {n} проблемных/изменившихся элементах API. Проверь диагностику.", {"count": n, "data": deg}))
        return out
