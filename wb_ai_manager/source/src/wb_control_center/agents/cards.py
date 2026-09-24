from __future__ import annotations

from .base import BaseAgent
from ..models import AgentResult
from ..metrics import count_records


class CardsAgent(BaseAgent):
    name = "cards"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        checks = [
            ("wb_card_errors", "card_errors", "critical", "Ошибки/блокировки карточек"),
            ("wb_banned_products", "banned_products", "critical", "Заблокированные или скрытые товары"),
            ("wb_prices_quarantine", "price_quarantine", "critical", "Товары в ценовом карантине"),
        ]
        for tool, key, sev, title in checks:
            try:
                data = await self.call(tool)
            except Exception as e:
                out.events.append(self.event("warning", f"{key}_check_failed", f"Не удалось проверить: {title}", str(e)))
                continue
            out.snapshots.append((key, data if isinstance(data, dict) else {"data": data}))
            n = count_records(data)
            if n:
                out.events.append(self.event(sev, key, title, f"Найдено элементов: {n}. Это может напрямую влиять на продажи.", {"count": n, "data": data}))

        # Read the actual card catalogue as a source for content completeness and
        # media-change tracking. Failure is non-fatal because some tokens omit Content.
        try:
            cards = await self.call("wb_cards_list", limit=100)
            out.snapshots.append(("card_catalog", cards if isinstance(cards, dict) else {"data": cards}))
        except Exception as e:
            out.events.append(self.event("info", "card_catalog_unavailable", "Каталог карточек недоступен для контент-аудита", str(e)))
        return out
