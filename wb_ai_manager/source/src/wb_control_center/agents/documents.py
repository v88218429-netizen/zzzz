from __future__ import annotations

from datetime import datetime, timedelta

from .base import BaseAgent
from ..metrics import count_records
from ..models import AgentResult


class DocumentsAgent(BaseAgent):
    name = "documents"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        today = datetime.now().date()
        start = (today - timedelta(days=6)).isoformat()
        try:
            docs = await self.call("wb_documents_list", date_from=start, date_to=today.isoformat(), limit=100)
        except Exception as e:
            out.events.append(self.event("warning", "documents_failed", "Не удалось проверить документы WB", str(e)))
            return out
        previous = self.ctx.db.latest_snapshot(self.name, "documents_7d")
        current = docs if isinstance(docs, dict) else {"data": docs}
        if previous and previous.get("data") != current:
            out.events.append(self.event("info", "documents_changed", "В WB появились/изменились финансовые документы", f"Документов в текущем окне: примерно {count_records(docs)}.", {"data": docs}))
        out.snapshots.append(("documents_7d", current))
        return out
