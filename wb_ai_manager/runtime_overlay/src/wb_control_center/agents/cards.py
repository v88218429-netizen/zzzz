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

        # Read the complete live card catalogue. WB Content API is cursor-paginated
        # and effectively pages by 100; one page is not a complete seller catalogue.
        try:
            all_cards: list[dict] = []
            cursor: dict | None = None
            seen_cursors: set[tuple[str, str]] = set()
            last_cursor: dict = {}
            for _page in range(50):
                page = await self.call("wb_cards_list", limit=100, cursor=cursor)
                if not isinstance(page, dict):
                    break
                rows = page.get("cards")
                if not isinstance(rows, list):
                    break
                all_cards.extend(x for x in rows if isinstance(x, dict))
                raw_cursor = page.get("cursor") if isinstance(page.get("cursor"), dict) else {}
                updated = str(raw_cursor.get("updatedAt") or "")
                nm = str(raw_cursor.get("nmID") or raw_cursor.get("nmId") or "")
                last_cursor = raw_cursor
                marker = (updated, nm)
                if len(rows) < 100 or not updated or not nm or marker in seen_cursors:
                    break
                seen_cursors.add(marker)
                cursor = {"limit": 100, "updatedAt": updated, "nmID": int(nm)}
            # De-duplicate defensively by nmID; WB cursor boundaries can overlap.
            by_nm: dict[str, dict] = {}
            for row in all_cards:
                nm = row.get("nmID", row.get("nmId"))
                if nm is not None:
                    by_nm[str(nm)] = row
            payload = {
                "cards": list(by_nm.values()),
                "count": len(by_nm),
                "complete": bool(all_cards) and (len(all_cards) % 100 != 0 or len(by_nm) < 5000),
                "cursor": last_cursor,
                "source": "live_wb_cards",
            }
            out.snapshots.append(("card_catalog", payload))
        except Exception as e:
            out.events.append(self.event("warning", "card_catalog_unavailable", "Каталог карточек WB не удалось прочитать полностью", str(e)))
        return out
