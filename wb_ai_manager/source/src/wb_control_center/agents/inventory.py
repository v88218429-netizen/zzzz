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

        # Read the physical article from the FF total, but treat old or partial
        # snapshots as references only. Marketplace card stock stays separate.
        settings = getattr(self.ctx, "settings", None)
        portfolio = PortfolioService(settings).snapshot() if settings is not None else {}
        own = portfolio.get("own_27") or {}
        own_health = next((x for x in (portfolio.get("source_health") or []) if isinstance(x, dict) and x.get("id") == "own_27"), {})
        # The stock snapshot date is more meaningful than the bridge refresh time:
        # a newly generated file can still contain month-old balances.
        source_updated_at = own.get("ff_snapshot_date") or own_health.get("bridge_modified_at") or own.get("source_updated_at") or own_health.get("freshness")
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
        trusted_ozon_rows = {
            str(item.get("sku")): item
            for item in (own.get("ozon_products") or [])
            if isinstance(item, dict) and item.get("sku")
        }

        # A physical stock signal is valid only after the operator/source has
        # explicitly confirmed that every shop/cabinet is included. No implicit
        # assumption that one workbook covers the whole business.
        all_store_scope_complete = (
            own.get("all_store_scope_complete") is True
            or portfolio.get("all_store_scope_complete") is True
        )
        inventory_mapping = own.get("inventory_mapping") or {}
        global_mapping_complete = (
            _float_or_none(inventory_mapping.get("unmapped_listings")) == 0
        )
        scope_note = str(
            own.get("inventory_scope_note")
            or portfolio.get("inventory_scope_note")
            or "Охват всех магазинов и кабинетов не подтверждён."
        )
        if not (own.get("inventory_scope_note") or portfolio.get("inventory_scope_note")):
            health_by_id = {
                str(source.get("id") or ""): source
                for source in (portfolio.get("source_health") or [])
                if isinstance(source, dict)
            }
            other_stock_sources = (
                ("sanych_sellmonitor", "магазин Саныч"),
                ("air_fbs", "магазин AIR"),
                ("hozyushka_fbs", "магазин Хозяюшка"),
                ("dublinsky_legacy", "старый склад Дублинский"),
            )
            source_gaps = []
            for source_id, label in other_stock_sources:
                health = health_by_id.get(source_id)
                if not health:
                    source_gaps.append(f"{label}: источник не подключён")
                    continue
                freshness = str(health.get("freshness") or "").strip()
                status = str(health.get("status") or "").strip().lower()
                updated = health.get("bridge_modified_at") or health.get("modified_at") or freshness
                updated_dt = _source_datetime(updated)
                age_hours = (datetime.now(timezone.utc) - updated_dt).total_seconds() / 3600 if updated_dt else None
                if status != "fresh" or age_hours is None or age_hours > max_source_age_hours:
                    detail = freshness or status or "дата неизвестна"
                    source_gaps.append(f"{label}: {detail}")
            if source_gaps:
                scope_note = "Нет свежего подтверждённого складского охвата: " + "; ".join(source_gaps) + "."

        critical = float(cfg.get("critical_days_cover", 2))
        warning = float(cfg.get("warning_days_cover", 5))
        over = float(cfg.get("overstock_days_cover", 75))

        physical_coverage: dict[str, Any] = {}
        # Even a stale physical mapping is enough to stop duplicate WB-card alarms;
        # it is not enough to produce a current shortage or overstock signal.
        physical_member_skus: set[str] = {
            sku for sku, product in trusted_rows.items()
            if product.get("physical_position")
        }
        physical_rows = [
            item for item in (own.get("physical_inventory") or [])
            if isinstance(item, dict)
        ]
        for index, item in enumerate(physical_rows):
            name = str(item.get("physical_position") or "").strip()
            if not name or item.get("is_composite"):
                # A set is a sales package, not a separate warehouse article.
                continue
            raw_members = [
                member for member in (item.get("member_skus") or [])
                if isinstance(member, dict) and member.get("sku")
            ]
            known_ozon_skus = {
                str(member.get("sku")) for member in raw_members
                if str(member.get("platform") or "").lower().startswith("ozon")
            }
            members = []
            for raw_member in raw_members:
                member = dict(raw_member)
                sku = str(member.get("sku"))
                if not member.get("platform") and sku in trusted_rows:
                    # Older saved snapshots carried only WB sku/name links. Rebuild
                    # reference demand from both channels when the same source row
                    # contains an explicit Ozon counterpart.
                    product = trusted_rows[sku]
                    units = PortfolioService._physical_units_per_package(name, product.get("name"))
                    member.update({
                        "platform": "WB",
                        "units_per_order": units,
                        "orders_per_day": product.get("orders_per_day"),
                        "stock": product.get("wb_fbs_stock"),
                        "fbw_stock": product.get("fbw_stock"),
                        "mapping_complete": units is not None,
                    })
                    ozon_sku = str(product.get("ozon_sku") or "")
                    if ozon_sku.isdigit() and ozon_sku not in known_ozon_skus:
                        ozon_article = str(product.get("ozon_seller_article") or product.get("name") or "")
                        recipe, unresolved = PortfolioService._physical_components(ozon_article, name, physical_rows)
                        ozon_units = sum(
                            float(component.get("units_per_order") or 0)
                            for component in recipe
                            if component.get("physical_position") == name
                        ) if not unresolved else None
                        members.append({
                            "platform": str(product.get("cabinet") or "Ozon"),
                            "sku": ozon_sku,
                            "name": ozon_article,
                            "units_per_order": ozon_units if ozon_units and ozon_units > 0 else None,
                            "orders_per_day": product.get("ozon_orders_per_day"),
                            "stock": product.get("ozon_fbs_stock"),
                            "fbw_stock": None,
                            "mapping_complete": bool(ozon_units and ozon_units > 0 and not unresolved),
                        })
                        known_ozon_skus.add(ozon_sku)
                elif not member.get("platform") and sku in trusted_ozon_rows:
                    member["platform"] = str(trusted_ozon_rows[sku].get("cabinet") or "Ozon")
                members.append(member)
                if str(member.get("platform") or "").upper() == "WB":
                    physical_member_skus.add(sku)

            # Only the combined physical warehouse balance belongs here.
            # K2 SAFE is a subset and must never replace Остатки ФФ.
            stock = _float_or_none(item.get("ff_stock"))
            stock = max(0.0, stock) if stock is not None else None
            stock_source = "Сводная · Остатки ФФ (К2 + Иваново)" if stock is not None else "Нет подтверждённого общего остатка ФФ"

            daily_by_platform: dict[str, float] = defaultdict(float)
            marketplace_stock_units: dict[str, float] = defaultdict(float)
            marketplace_counts: dict[str, int] = defaultdict(int)
            stock_pool_seen: set[str] = set()
            mapping_complete = bool(members) and not bool(item.get("unresolved_components"))
            demand_coverage_complete = bool(members)
            known_demand_members = 0
            for member in members:
                platform = str(member.get("platform") or "Неизвестный кабинет")
                units = _float_or_none(member.get("units_per_order"))
                rate = _float_or_none(member.get("orders_per_day"))
                if member.get("mapping_complete") is False or units is None or units <= 0:
                    mapping_complete = False
                if units is None or units <= 0 or rate is None:
                    demand_coverage_complete = False
                else:
                    daily_by_platform[platform] += max(0.0, rate) * units
                    known_demand_members += 1
                if platform.upper() == "WB":
                    marketplace_counts["WB"] += 1
                    for source_key, pool_name in (("stock", "WB FBS"), ("fbw_stock", "WB FBW")):
                        quantity = _float_or_none(member.get(source_key))
                        if quantity is not None and units is not None and units > 0:
                            marketplace_stock_units[pool_name] += max(0.0, quantity) * units
                            stock_pool_seen.add(pool_name)
                elif platform.lower().startswith("ozon"):
                    marketplace_counts["Ozon"] += 1
                    quantity = _float_or_none(member.get("stock"))
                    if quantity is not None and units is not None and units > 0:
                        marketplace_stock_units["Ozon FBS"] += max(0.0, quantity) * units
                        stock_pool_seen.add("Ozon FBS")

            if known_demand_members == 0:
                demand_coverage_complete = False
            if len(members) != known_demand_members:
                demand_coverage_complete = False
            daily = sum(daily_by_platform.values()) if known_demand_members else None
            days = stock / daily if stock is not None and daily is not None and daily > 0 else None
            is_composite = bool(item.get("is_composite"))
            signal_eligible = bool(
                trusted_current
                and all_store_scope_complete
                and global_mapping_complete
                and mapping_complete
                and demand_coverage_complete
                and stock is not None
                and not is_composite
            )
            no_demand = bool(signal_eligible and stock is not None and stock > 0 and daily == 0)
            orders_qty = _float_or_none(item.get("orders_qty"))
            sheet_row = item.get("sheet_row")
            key = f"physical:{sheet_row if sheet_row is not None else index}"
            row = {
                "scope": "physical_product",
                "source_updated_at": source_updated_at,
                "source_fresh": source_fresh,
                "physical_position": name,
                "is_composite": is_composite,
                "member_skus": members,
                "marketplace_counts": dict(marketplace_counts),
                "marketplace_stock_units": dict(marketplace_stock_units) if stock_pool_seen else {},
                "stock": stock,
                "stock_source": stock_source,
                "stock_available": stock is not None,
                "stock_verified": bool(stock is not None and source_fresh),
                "daily_sales": daily,
                "daily_demand_by_platform": dict(daily_by_platform),
                "velocity_source": "Заказы по карточкам, пересчитанные в единицы складского артикула" if daily is not None else "Нет полного темпа по связанным карточкам",
                "days_cover": days,
                "reference_days_cover": days,
                "orders_qty": orders_qty,
                "no_demand": no_demand,
                "signal_eligible": signal_eligible,
                "mapping_complete": mapping_complete,
                "demand_coverage_complete": demand_coverage_complete,
                "all_store_scope_complete": all_store_scope_complete,
                "scope_note": scope_note,
                "stock_scope": "current_snapshot" if source_fresh else "historical_snapshot",
                "velocity_scope": "weighted_mapped_listings",
                "sheet_row": sheet_row,
                "planned_order_qty": item.get("planned_order_qty"),
                "supplier_debt_qty": item.get("supplier_debt_qty"),
                "order_status": item.get("order_status"),
                "unresolved_components": item.get("unresolved_components") or [],
            }
            physical_coverage[key] = row
            if stock is None or not signal_eligible:
                continue
            if row.get("no_demand"):
                out.events.append(self.event(
                    "warning",
                    f"physical_no_demand:{key}",
                    "Запас без заказов · проверить риск неликвида",
                    f"{name}: остаток {stock:.0f}, подтверждённых заказов нет.",
                    row,
                ))
                continue
            if days is None:
                continue
            if days <= critical:
                out.events.append(self.event(
                    "critical",
                    f"physical_stockout:{key}",
                    "Критический остаток физического товара",
                    f"{name}: запас примерно на {days:.1f} дня (остаток {stock:.0f}; расход {daily:.1f} складских единиц/день).",
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
        non_composite_rows = [x for x in physical_coverage.values() if x.get("scope") == "physical_product"]
        inventory_mapping = own.get("inventory_mapping") or {}
        physical_coverage["_meta"] = {
            "source_updated_at": source_updated_at,
            "source_fresh": source_fresh,
            "source_age_hours": source_age_hours,
            "source_max_age_hours": max_source_age_hours,
            "all_store_scope_complete": all_store_scope_complete,
            "global_mapping_complete": global_mapping_complete,
            "scope_note": scope_note,
            "unmapped_listings": inventory_mapping.get("unmapped_listings"),
            "mapped_listings": inventory_mapping.get("mapped_listings"),
            "source_listing_total": inventory_mapping.get("marketplace_listings"),
            "physical_groups": len(non_composite_rows),
            "incomplete_mapping_groups": sum(not x.get("mapping_complete") for x in non_composite_rows),
            "incomplete_demand_groups": sum(not x.get("demand_coverage_complete") for x in non_composite_rows),
            "signal_eligible_groups": sum(bool(x.get("signal_eligible")) for x in non_composite_rows),
            "coverage_complete": bool(
                trusted_current
                and all_store_scope_complete
                and global_mapping_complete
                and non_composite_rows
                and all(x.get("mapping_complete") and x.get("demand_coverage_complete") for x in non_composite_rows)
            ),
        }
        out.snapshots.append(("coverage", coverage))
        out.snapshots.append(("physical_coverage", physical_coverage))
        return out
