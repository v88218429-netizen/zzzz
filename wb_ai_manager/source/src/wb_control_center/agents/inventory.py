from __future__ import annotations

from collections import defaultdict
from typing import Any
from datetime import datetime, timezone

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


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None and str(value).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _source_datetime(value: Any):
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:10], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
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
        for row in find_dicts_with_any_key(stocks, {"nmId", "nmID", "quantity", "quantityFull", "stock"}):
            nm = extract_nm_id(row)
            if nm is None:
                continue
            qty = first_number(row, {"quantityFull", "quantity_full", "quantity", "stock", "stocks"})
            if qty is not None:
                stock_by_nm[nm] += max(0.0, qty)

        sales_by_nm: dict[int, float] = defaultdict(float)
        for row in find_dicts_with_any_key(sales, {"nmId", "nmID", "quantity", "saleID", "saleId"}):
            if period is not None:
                row_date = _row_date(row)
                if row_date is None or row_date < start or row_date > end:
                    continue
            nm = extract_nm_id(row)
            if nm is None:
                continue
            qty = first_number(row, {"quantity", "qty"})
            sales_by_nm[nm] += abs(qty) if qty not in (None, 0) else 1.0

        # The physical product row is the stock-planning unit. Its safe K2/FF
        # balance and demand already combine the listing variants in the source
        # table, so a 2/4/6 kg listing does not become three separate supply alarms.
        settings = getattr(self.ctx, "settings", None)
        portfolio = PortfolioService(settings).snapshot() if settings is not None else {}
        own = portfolio.get("own_27") or {}
        own_health = next((x for x in (portfolio.get("source_health") or []) if isinstance(x, dict) and x.get("id") == "own_27"), {})
        source_updated_at = own_health.get("bridge_modified_at") or own.get("source_updated_at") or own_health.get("freshness")
        source_dt = _source_datetime(source_updated_at)
        max_source_age_hours = max(1.0, float(cfg.get("source_max_age_hours", 72)))
        source_age_hours = (datetime.now(timezone.utc) - source_dt).total_seconds() / 3600 if source_dt else None
        source_fresh = bool(source_age_hours is not None and source_age_hours <= max_source_age_hours)
        trusted_current = bool(portfolio.get("current_data") and source_fresh)
        legacy_test_context = settings is None
        trusted_rows = {
            str(item.get("sku")): item
            for item in (own.get("products") or [])
            if isinstance(item, dict) and item.get("sku")
        }

        critical = float(cfg.get("critical_days_cover", 2))
        warning = float(cfg.get("warning_days_cover", 5))
        over = float(cfg.get("overstock_days_cover", 75))

        physical_coverage: dict[str, Any] = {}
        physical_member_skus: set[str] = set()
        for index, item in enumerate((own.get("physical_inventory") or []) if trusted_current else []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("physical_position") or "").strip()
            members = [
                {"sku": str(member.get("sku") or ""), "name": str(member.get("name") or "")}
                for member in (item.get("member_skus") or [])
                if isinstance(member, dict) and member.get("sku")
            ]
            if not name or (not members and all(item.get(key) is None for key in ("k2_safe_stock", "ff_stock", "ivanovo_stock", "orders_per_day"))):
                continue

            stock = None
            stock_source = ""
            for key, label in (
                ("k2_safe_stock", "Сводная · К2 ФФ SAFE"),
                ("ff_stock", "Сводная · Остатки ФФ"),
                ("ivanovo_stock", "Сводная · ФФ Иваново"),
            ):
                candidate = _float_or_none(item.get(key))
                if candidate is not None:
                    stock = max(0.0, candidate)
                    stock_source = label
                    break
            daily = _float_or_none(item.get("orders_per_day"))
            if daily is not None:
                daily = max(0.0, daily)
            days = stock / daily if stock is not None and daily is not None and daily > 0 else None
            sheet_row = item.get("sheet_row")
            key = f"physical:{sheet_row if sheet_row is not None else index}"
            physical_coverage[key] = {
                "scope": "physical_product",
                "source_updated_at": source_updated_at,
                "physical_position": name,
                "member_skus": members,
                "stock": stock,
                "stock_source": stock_source or "Источник физического остатка не указан",
                "stock_verified": stock is not None,
                "daily_sales": daily,
                "velocity_source": "Сводная · темп физического товара" if daily is not None else "Нет подтверждённого темпа физического товара",
                "days_cover": days,
                "stock_scope": "current_snapshot",
                "velocity_scope": "source_physical_product",
                "sheet_row": sheet_row,
                "planned_order_qty": item.get("planned_order_qty"),
                "supplier_debt_qty": item.get("supplier_debt_qty"),
                "order_status": item.get("order_status"),
            }
            if stock is not None:
                physical_member_skus.update(member["sku"] for member in members)
            row = physical_coverage[key]
            if stock is None or days is None:
                continue
            if days <= critical:
                out.events.append(self.event(
                    "critical",
                    f"physical_stockout:{key}",
                    "Критический остаток физического товара",
                    f"{name}: запас примерно на {days:.1f} дня (остаток {stock:.0f}; темп {daily:.1f} физических единиц/день).",
                    row,
                ))
            elif days <= warning:
                out.events.append(self.event(
                    "warning",
                    f"physical_low_stock:{key}",
                    "Заканчивается физический товар",
                    f"{name}: запас примерно на {days:.1f} дня.",
                    row,
                ))
            elif days >= over:
                out.events.append(self.event(
                    "warning",
                    f"physical_overstock:{key}",
                    "Высокий физический запас / риск неликвида",
                    f"{name}: примерно {days:.0f} дней физического запаса.",
                    row,
                ))

        coverage: dict[str, Any] = {}
        trusted_nm = {int(x) for x in trusted_rows if x.isdigit()} if trusted_current else set()
        all_nm = set(stock_by_nm) | set(sales_by_nm) | trusted_nm
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
                stock_verified = legacy_test_context

            api_daily = sales_by_nm.get(nm, 0.0) / max(1.0, float(period_days))
            trusted_daily = _float_or_none(trusted.get("orders_per_day")) if isinstance(trusted, dict) else None
            if trusted_daily is not None and trusted_daily > 0:
                daily = trusted_daily
                velocity_source = "Сводная · WB Заказов в день"
            else:
                daily = api_daily
                velocity_source = "WB statistics · продажи за выбранный период"
            days = stock / daily if daily > 0 else None
            coverage[str(nm)] = {
                "nm_id": nm,
                "source_updated_at": source_updated_at,
                "stock": stock,
                "wb_stats_stock": wb_stats_stock,
                "stock_source": stock_source,
                "stock_verified": stock_verified,
                "sales_period": sales_by_nm.get(nm, 0),
                "sales_period_days": period_days,
                "sales_period_from": start,
                "sales_period_to": end,
                "daily_sales": daily,
                "api_daily_sales": api_daily,
                "trusted_orders_per_day": trusted_daily,
                "velocity_source": velocity_source,
                "days_cover": days,
                "stock_scope": "current_snapshot",
                "velocity_scope": "selected_period" if period is not None else "rolling_14d",
            }
            # Keep listing-level stock available for inspection, but don't repeat
            # family replenishment alarms for listings covered by physical stock.
            if not stock_verified or str(nm) in physical_member_skus:
                continue
            if days is not None and days <= critical:
                out.events.append(self.event("critical", f"stockout:{nm}", "Критический остаток карточки WB", f"nmID {nm}: запас примерно на {days:.1f} дня.", coverage[str(nm)]))
            elif days is not None and days <= warning:
                out.events.append(self.event("warning", f"low_stock:{nm}", "Низкий остаток карточки WB", f"nmID {nm}: запас примерно на {days:.1f} дня.", coverage[str(nm)]))
            elif days is not None and days >= over:
                out.events.append(self.event("warning", f"overstock:{nm}", "Высокий запас карточки WB", f"nmID {nm}: примерно {days:.0f} дней.", coverage[str(nm)]))

        if portfolio.get("current_data") and not source_fresh and not legacy_test_context:
            out.events.append(self.event(
                "warning",
                "inventory_source_stale",
                "Свежесть «Сводной» не подтверждена",
                f"Источник остатков обновлялся: {source_updated_at or 'дата неизвестна'}. Пороги дефицита не применяются к устаревшему или неподтверждённому срезу.",
                {"source": "Сводная", "source_updated_at": source_updated_at, "max_age_hours": max_source_age_hours},
            ))
        if not trusted_current and not legacy_test_context and not portfolio.get("current_data"):
            out.events.append(self.event(
                "warning",
                "inventory_source_unverified",
                "Остатки физического товара не сверены с рабочей «Сводной»",
                "До подключения свежей «Сводной» остаток WB statistics не считается подтверждённым остатком физического товара или FBS.",
                {"source": "WB statistics", "required_source": "Сводная · К2 ФФ SAFE / Остатки WB FBS"},
            ))
        physical_coverage["_meta"] = {
            "source_updated_at": source_updated_at,
            "source_fresh": source_fresh,
            "source_age_hours": source_age_hours,
            "source_max_age_hours": max_source_age_hours,
        }
        out.snapshots.append(("coverage", coverage))
        out.snapshots.append(("physical_coverage", physical_coverage))
        return out
