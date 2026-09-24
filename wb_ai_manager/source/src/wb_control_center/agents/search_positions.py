from __future__ import annotations

from datetime import datetime, timedelta

from .base import BaseAgent
from ..metrics import extract_position_rows
from ..models import AgentResult


class SearchPositionsAgent(BaseAgent):
    name = "search_positions"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        cfg = self.ctx.policy.thresholds.get("search", {})
        today = datetime.now().date()
        start = (today - timedelta(days=1)).isoformat()
        try:
            data = await self.call("wb_search_report", date_from=start, date_to=today.isoformat(), limit=1000)
        except Exception as e:
            out.events.append(self.event("info", "search_unavailable", "Поисковая аналитика недоступна", f"WB search report не отработал. Частая причина — нет подписки Джем или прав токена. {e}"))
            return out
        rows = extract_position_rows(data)
        current = {f"{nm}:{q}": {"nm_id": nm, "query": q, "position": pos} for nm, pos, q in rows}
        previous = self.ctx.db.latest_snapshot(self.name, "positions")
        warn = float(cfg.get("position_drop_warn", 5))
        critical = float(cfg.get("position_drop_critical", 12))
        if previous:
            old = previous["data"]
            for key, now in current.items():
                prev = old.get(key) if isinstance(old, dict) else None
                if not prev:
                    continue
                delta = float(now["position"]) - float(prev.get("position", now["position"]))
                if delta >= critical:
                    out.events.append(self.event("critical", f"position:{key}", "Сильная просадка позиции", f"nmID {now['nm_id']} {now['query'] or ''}: {prev.get('position')} → {now['position']} (−{delta:.0f} позиций).", {"before": prev, "after": now}))
                elif delta >= warn:
                    out.events.append(self.event("warning", f"position:{key}", "Просадка позиции", f"nmID {now['nm_id']} {now['query'] or ''}: {prev.get('position')} → {now['position']} (−{delta:.0f} позиций).", {"before": prev, "after": now}))
        out.snapshots.append(("positions", current))
        return out
