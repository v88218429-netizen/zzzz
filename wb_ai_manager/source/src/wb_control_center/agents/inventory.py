from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from .base import BaseAgent
from ..metrics import extract_nm_id, find_dicts_with_any_key, first_number
from ..models import AgentResult


class InventoryAgent(BaseAgent):
    name = "inventory"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        cfg = self.ctx.policy.thresholds.get("inventory", {})
        stocks = await self.call("wb_stats_stocks", limit=1000)
        start = (datetime.now().date() - timedelta(days=13)).isoformat()
        sales = await self.call("wb_stats_sales", date_from=start)
        stock_by_nm: dict[int, float] = defaultdict(float)
        for d in find_dicts_with_any_key(stocks, {"nmId", "nmID", "quantity", "quantityFull", "stock"}):
            nm = extract_nm_id(d)
            if nm is None:
                continue
            qty = first_number(d, {"quantityFull", "quantity_full", "quantity", "stock", "stocks"})
            if qty is not None:
                stock_by_nm[nm] += max(0.0, qty)
        sales_by_nm: dict[int, float] = defaultdict(float)
        for d in find_dicts_with_any_key(sales, {"nmId", "nmID", "quantity", "saleID", "saleId"}):
            nm = extract_nm_id(d)
            if nm is None:
                continue
            qty = first_number(d, {"quantity", "qty"})
            sales_by_nm[nm] += abs(qty) if qty not in (None, 0) else 1.0
        coverage: dict[str, Any] = {}
        critical = float(cfg.get("critical_days_cover", 2))
        warning = float(cfg.get("warning_days_cover", 5))
        over = float(cfg.get("overstock_days_cover", 75))
        for nm, stock in stock_by_nm.items():
            daily = sales_by_nm.get(nm, 0.0) / 14.0
            days = stock / daily if daily > 0 else None
            coverage[str(nm)] = {"nm_id": nm, "stock": stock, "sales_14d": sales_by_nm.get(nm, 0), "daily_sales": daily, "days_cover": days}
            if days is not None and days <= critical:
                out.events.append(self.event("critical", f"stockout:{nm}", "Критический остаток", f"nmID {nm}: запас примерно на {days:.1f} дня (остаток {stock:.0f}, темп {daily:.1f}/день).", coverage[str(nm)]))
            elif days is not None and days <= warning:
                out.events.append(self.event("warning", f"low_stock:{nm}", "Заканчивается товар", f"nmID {nm}: запас примерно на {days:.1f} дня.", coverage[str(nm)]))
            elif days is not None and days >= over:
                out.events.append(self.event("warning", f"overstock:{nm}", "Высокий запас / риск неликвида", f"nmID {nm}: примерно {days:.0f} дней запаса.", coverage[str(nm)]))
        out.snapshots.append(("coverage", coverage))
        return out
