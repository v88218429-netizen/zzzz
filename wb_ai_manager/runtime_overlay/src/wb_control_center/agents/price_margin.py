from __future__ import annotations

from datetime import datetime, timedelta

from .base import BaseAgent
from ..config import load_catalog
from ..metrics import extract_nm_id, find_dicts_with_any_key, first_number
from ..models import AgentResult


class PriceMarginAgent(BaseAgent):
    name = "price_margin"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        prices = await self.call("wb_prices_list", limit=1000, offset=0)
        out.snapshots.append(("prices", prices if isinstance(prices, dict) else {"data": prices}))
        catalog = load_catalog()
        if catalog:
            for d in find_dicts_with_any_key(prices, {"nmID", "nmId", "price", "discountedPrice", "discounted_price"}):
                nm = extract_nm_id(d)
                if nm is None or nm not in catalog:
                    continue
                current = first_number(d, {"discountedPrice", "discounted_price", "priceWithDiscount", "price_with_discount", "price"})
                floor = catalog[nm].min_price_rub
                if current is not None and floor is not None and current < floor:
                    out.events.append(self.event("critical", f"below_floor:{nm}", "Цена ниже минимальной", f"nmID {nm}: текущая цена ≈ {current:.0f} ₽, заданный минимум {floor:.0f} ₽.", {"row": d, "min_price": floor}))
        period = self.analysis_period()
        today = datetime.now().date()
        promo_start, promo_end = (self.dates(31) if period is not None else (today.isoformat(), (today + timedelta(days=30)).isoformat()))
        try:
            promos = await self.call("wb_promotions_audit", start=promo_start, end=promo_end, only_auto=False, max_promotions=50)
            out.snapshots.append(("promotions", promos if isinstance(promos, dict) else {"data": promos}))
        except Exception as e:
            out.events.append(self.event("warning", "promotions_failed", "Не удалось проверить акции", str(e)))
        return out
