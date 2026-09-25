from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from .base import BaseAgent
from ..metrics import extract_nm_id, find_dicts_with_any_key, first_number
from ..models import AgentResult
from ..portfolio import PortfolioService


def _row_date(row: dict[str, Any]) -> str | None:
    for key in ("date", "saleDate", "sale_date", "lastChangeDate", "last_change_date", "dateTo", "date_to"):
        value = row.get(key)
        if value:
            text = str(value)
            if len(text) >= 10:
                return text[:10]
    return None


class InventoryAgent(BaseAgent):
    name = "inventory"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        cfg = self.ctx.policy.thresholds.get("inventory", {})
        stocks = await self.call("wb_stats_stocks", limit=1000)
        start, end = self.dates(14)
        sales = await self.call("wb_stats_sales", date_from=start)
        period = self.analysis_period()
        period_days = period.days if period is not None else 14
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
            if period is not None:
                row_date = _row_date(d)
                if row_date is None or row_date < start or row_date > end:
                    continue
            nm = extract_nm_id(d)
            if nm is None:
                continue
            qty = first_number(d, {"quantity", "qty"})
            sales_by_nm[nm] += abs(qty) if qty not in (None, 0) else 1.0
        # WB statistics stock is not a reliable FBS availability source for this
        # operating model. Reconcile it with the live trusted "Сводная" snapshot.
        portfolio = PortfolioService(self.ctx.settings).snapshot()
        trusted_current = bool(portfolio.get("current_data"))
        trusted_rows = {
            str(x.get("sku")): x
            for x in ((portfolio.get("own_27") or {}).get("products") or [])
            if isinstance(x, dict) and x.get("sku")
        }

        coverage: dict[str, Any] = {}
        critical = float(cfg.get("critical_days_cover", 2))
        warning = float(cfg.get("warning_days_cover", 5))
        over = float(cfg.get("overstock_days_cover", 75))
        all_nm = set(stock_by_nm) | set(sales_by_nm)
        for nm in all_nm:
            wb_stats_stock = stock_by_nm.get(nm, 0.0)
            trusted = trusted_rows.get(str(nm)) if trusted_current else None
            trusted_fbs = trusted.get("wb_fbs_stock") if isinstance(trusted, dict) else None
            if trusted_fbs is not None:
                stock = max(0.0, float(trusted_fbs))
                stock_source = "Сводная · Остатки WB FBS"
                stock_verified = True
            else:
                stock = wb_stats_stock
                stock_source = "WB statistics · не подтверждено как FBS"
                stock_verified = False

            daily = sales_by_nm.get(nm, 0.0) / max(1.0, float(period_days))
            days = stock / daily if daily > 0 else None
            coverage[str(nm)] = {
                "nm_id": nm,
                "stock": stock,
                "wb_stats_stock": wb_stats_stock,
                "stock_source": stock_source,
                "stock_verified": stock_verified,
                "sales_period": sales_by_nm.get(nm, 0),
                "sales_period_days": period_days,
                "sales_period_from": start,
                "sales_period_to": end,
                "daily_sales": daily,
                "days_cover": days,
                "stock_scope": "current_snapshot",
                "velocity_scope": "selected_period" if period is not None else "rolling_14d",
            }
            # Do not manufacture stockout alarms from a source that is not the
            # seller's trusted FBS availability. Until Sheets is live, expose a
            # data-quality warning instead.
            if not stock_verified:
                continue
            if days is not None and days <= critical:
                out.events.append(self.event("critical", f"stockout:{nm}", "Критический остаток", f"nmID {nm}: запас примерно на {days:.1f} дня (остаток {stock:.0f}, темп {daily:.1f}/день).", coverage[str(nm)]))
            elif days is not None and days <= warning:
                out.events.append(self.event("warning", f"low_stock:{nm}", "Заканчивается товар", f"nmID {nm}: запас примерно на {days:.1f} дня.", coverage[str(nm)]))
            elif days is not None and days >= over:
                out.events.append(self.event("warning", f"overstock:{nm}", "Высокий запас / риск неликвида", f"nmID {nm}: примерно {days:.0f} дней запаса.", coverage[str(nm)]))

        if not trusted_current:
            out.events.append(self.event(
                "warning",
                "inventory_source_unverified",
                "Остатки FBS не сверены с рабочей «Сводной»",
                "WB statistics сам по себе не считается подтверждением FBS-остатка. Подключи живую «Сводную»/K2 — до этого дефицит по товарам не помечается как критический.",
                {"source": "WB statistics", "required_source": "Сводная · Остатки WB FBS / K2"},
            ))
        out.snapshots.append(("coverage", coverage))
        return out
