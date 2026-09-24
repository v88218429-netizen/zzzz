from __future__ import annotations

from collections import Counter

from .base import BaseAgent
from ..metrics import summarize_for_prompt
from ..models import AgentResult
from ..portfolio import PortfolioService


class SupervisorAgent(BaseAgent):
    name = "supervisor"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        events = self.ctx.db.recent_events(hours=6, limit=150)
        important = [e for e in events if e.get("severity") in {"warning", "critical"} and e.get("agent") != self.name]
        pending = self.ctx.db.pending_actions(limit=100)
        portfolio = PortfolioService(self.ctx.settings).snapshot()
        has_portfolio = bool(portfolio.get("stores") or portfolio.get("own_27"))
        if not important and not pending and not has_portfolio:
            return out

        if self.ctx.llm.enabled:
            text = await self.ctx.llm.complete(
                "Ты главный операционный AI-менеджер Wildberries. Сведи события разных агентов, убери дубли, найди причинно-следственные связи. Формат: 1) что критично, 2) вероятная причина только если подтверждается данными, 3) что система уже предложила, 4) что требует решения владельца. Максимум 8 пунктов.",
                summarize_for_prompt({"events": important, "pending_actions": pending, "trusted_sheets": portfolio}),
                max_tokens=900,
            )
        else:
            # Deterministic supervisor: useful summary even with zero LLM/API.
            crit = [e for e in important if e.get("severity") == "critical"]
            warn = [e for e in important if e.get("severity") == "warning"]
            by_agent = Counter(e.get("agent", "unknown") for e in important)
            lines = [
                f"Режим: RULES — облачная LLM не используется.",
                f"За 6 часов: критических {len(crit)}, предупреждений {len(warn)}, действий ждут подтверждения {len(pending)}.",
            ]
            pf = portfolio.get("portfolio") or {}
            if pf:
                lines.append(
                    "Портфель trusted sheets: "
                    f"заказы {pf.get('orders_rub', 0):,.0f} ₽; выкупы {pf.get('buyouts_rub', 0):,.0f} ₽; "
                    f"прибыль {pf.get('profit_rub', 0):,.0f} ₽; маржа {pf.get('margin_pct', 0):.1f}%; ДРР {pf.get('drr_pct', 0):.1f}%."
                )
            for store in portfolio.get("stores", []):
                if store.get("status") in {"critical", "watch"}:
                    lines.append(
                        f"• [SHEETS] {store.get('name')}: прибыль {store.get('profit_rub', 0):,.0f} ₽; "
                        f"маржа {store.get('margin_pct', 0):.1f}%; ДРР {store.get('drr_pct', 0):.1f}%."
                    )
            neg = [x for x in (portfolio.get("own_27", {}).get("economy_examples") or []) if (x.get("profit_rub") or 0) < 0]
            for x in neg[:3]:
                lines.append(f"• [ЮНИТКА] {x.get('name')}: прибыль/ед. {x.get('profit_rub')} ₽, маржа {x.get('margin_pct')}%.")
            for e in (crit + warn)[:6]:
                lines.append(f"• [{str(e.get('severity')).upper()}] {e.get('title')}: {e.get('message')}")
            if pending:
                lines.append("Предложенные действия: " + "; ".join(f"#{a['id']} {a['tool']} — {a['reason']}" for a in pending[:4]))
            if by_agent:
                lines.append("Источники сигналов: " + ", ".join(f"{k}={v}" for k, v in by_agent.most_common(6)))
            text = "\n".join(lines)

        if text:
            out.events.append(self.event("info", "supervisor_digest", "Сводка Supervisor", text, {"source_events": len(important), "pending_actions": len(pending)}))
        return out
