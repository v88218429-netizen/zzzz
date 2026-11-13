from __future__ import annotations

import json
from datetime import datetime, timezone

from .base import BaseAgent
from ..models import AgentResult


class ExperimentsAgent(BaseAgent):
    name = "experiments"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        raw = self.ctx.db.get_kv("video_workbench.experiments.v2", "[]") or "[]"
        try:
            records = json.loads(raw)
        except (TypeError, ValueError):
            out.events.append(self.event(
                "critical",
                "video_workbench_store_invalid",
                "Планы фото-тестов не читаются",
                "Запись не перезаписана; проверь файл базы панели и восстановление из резервной копии.",
            ))
            return out
        if not isinstance(records, list):
            out.events.append(self.event(
                "critical",
                "video_workbench_store_invalid",
                "Планы фото-тестов имеют неверный формат",
                "Запись не перезаписана; автоматическое исправление не выполнялось.",
            ))
            return out
        items = [item for item in records[:200] if isinstance(item, dict)]
        out.snapshots.append(("workbench_experiments", {
            "items": items,
            "count": len(items),
            "source": "local_panel_database",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "writes_to_wb": False,
        }))
        planned = sum(1 for item in items if item.get("status") == "planned")
        if planned:
            out.events.append(self.event(
                "info",
                "video_workbench_plans_waiting",
                "Фото-тесты сохранены как планы",
                f"Планов без записанных результатов: {planned}. Запуск и модерация выполняются вручную в кабинете WB.",
                {"count": planned},
            ))
        return out
