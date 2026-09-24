from __future__ import annotations

from .base import BaseAgent
from ..metrics import count_records, summarize_for_prompt
from ..models import AgentResult


class ReturnsQualityAgent(BaseAgent):
    name = "returns_quality"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        cfg = self.ctx.policy.thresholds.get("returns", {})
        claims = await self.call("wb_returns_list", is_archive=False, limit=100)
        n = count_records(claims)
        out.snapshots.append(("open_claims", claims if isinstance(claims, dict) else {"data": claims}))
        if n >= int(cfg.get("open_claims_warn", 3)):
            out.events.append(self.event("warning", "open_returns", "Накопились заявки на возврат", f"Открытых заявок: примерно {n}.", {"data": claims}))
        if self.ctx.llm.enabled and n:
            text = await self.ctx.llm.complete(
                "Ты контролёр качества Wildberries. По возвратам сгруппируй повторяющиеся причины по товару и выдели то, что похоже на системную проблему товара, упаковки или описания. Не принимай решения по возвратам автоматически.",
                summarize_for_prompt(claims),
                max_tokens=700,
            )
            if text:
                out.events.append(self.event("info", "returns_analysis", "AI-разбор причин возвратов", text))
        return out
