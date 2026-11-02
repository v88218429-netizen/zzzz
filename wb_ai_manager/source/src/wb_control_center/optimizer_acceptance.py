from __future__ import annotations

from datetime import date, datetime, timezone
from math import isfinite
from typing import Any


SHOP_NAMES = {"ap": "Саныч", "aa": "AIR", "yv": "Хозяюшка"}
SHOP_ALIASES = {
    "ap": "ap", "sanych": "ap", "саныч": "ap", "дом саныча": "ap",
    "aa": "aa", "air": "aa", "аир": "aa", "аир wb": "aa",
    "yv": "yv", "hozyushka": "yv", "хозяюшка": "yv",
}


def _number(value: Any) -> float | None:
    try:
        number = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _shop_code(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return SHOP_ALIASES.get(text)


def _date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _campaign_nms(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key).lower()
            if name in {"nmid", "nm_id", "nm"} and str(item).strip().isdigit():
                found.add(str(item).strip())
            elif name in {"nmids", "nm_ids", "nms"} and isinstance(item, list):
                found.update(str(x).strip() for x in item if str(x).strip().isdigit())
            found.update(_campaign_nms(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_campaign_nms(item))
    return found


def evaluate_optimizer_acceptance(
    optimizer_state: dict[str, Any],
    portfolio: dict[str, Any],
    query_manager: dict[str, Any],
    cabinets: dict[str, Any],
    source_errors: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return evidence-based readiness; missing facts remain blockers, never zeros."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    blockers: list[str] = []
    shops = optimizer_state.get("shops") if isinstance(optimizer_state.get("shops"), dict) else {}
    sweep_rows: dict[str, dict[str, Any]] = {}
    if len(cabinets) != 3 or sum(1 for cab in cabinets.values() if cab.get("wb_connected") is True) != 3:
        blockers.append("Не подтверждены подключения всех трёх кабинетов WB.")

    for code, name in SHOP_NAMES.items():
        item = shops.get(code) if isinstance(shops.get(code), dict) else {}
        sweep = item.get("sweep") if isinstance(item.get("sweep"), dict) else {}
        campaigns_seen = int(sweep.get("sweep_campaigns_seen") or 0)
        campaigns_total = int(sweep.get("sweep_campaigns_total") or sweep.get("eligible_campaigns") or 0)
        products_seen = int(sweep.get("sweep_products_seen") or 0)
        products_total = int(sweep.get("sweep_products_total") or 0)
        complete = sweep.get("sweep_complete") is True
        sweep_rows[code] = {
            "name": name, "complete": complete,
            "campaigns_seen": campaigns_seen, "campaigns_total": campaigns_total,
            "products_seen": products_seen, "products_total": products_total,
        }
        if not item.get("wb_connected"):
            blockers.append(f"{name}: подключение кабинета не подтверждено.")
        if not item.get("observed_at"):
            blockers.append(f"{name}: нет времени последнего снимка оптимизатора.")
        if not complete or (campaigns_total and campaigns_seen < campaigns_total) or (products_total and products_seen < products_total):
            blockers.append(f"{name}: полный обход рекламы не завершён ({campaigns_seen}/{campaigns_total} кампаний, {products_seen}/{products_total} товаров).")

    own = portfolio.get("own_27") if isinstance(portfolio.get("own_27"), dict) else {}
    economics = own.get("primary_unit_economics") if isinstance(own.get("primary_unit_economics"), dict) else {}
    snapshot_date = _date(economics.get("snapshot_date"))
    age_days = (now.date() - snapshot_date).days if snapshot_date else None
    economics_ok = bool(
        portfolio.get("current_data") is True
        and economics.get("semantics") == "plan_model_plus_independent_fact"
        and snapshot_date is not None and 0 <= age_days <= 7
    )
    if not economics_ok:
        blockers.append("Юнит-экономика не подтверждена как текущая: нужен живой снимок модели с датой не старше 7 дней.")
    products = [p for p in own.get("products", []) if isinstance(p, dict)]
    product_by_shop: dict[str, dict[str, dict[str, Any]]] = {code: {} for code in SHOP_NAMES}
    for product in products:
        code = _shop_code(product.get("cabinet") or product.get("store_id") or product.get("shop"))
        sku = str(product.get("sku") or product.get("nm_id") or product.get("nmId") or "").strip()
        if code and sku.isdigit():
            product_by_shop[code][sku] = product

    economics_coverage: dict[str, dict[str, int]] = {}
    for code, name in SHOP_NAMES.items():
        campaign_nms: set[str] = set()
        for campaign in (shops.get(code) or {}).get("campaigns") or []:
            campaign_nms.update(_campaign_nms(campaign))
        matched = 0
        ready = 0
        for sku in campaign_nms:
            product = product_by_shop[code].get(sku)
            if product is None:
                continue
            matched += 1
            cost = _number(product.get("cost_rub"))
            price = _number(product.get("price_rub") or product.get("price_client_rub"))
            profit = _number(product.get("profit_rub"))
            if product.get("unit_economics_decision_ready") is True and cost is not None and cost > 0 and price is not None and price > 0 and profit is not None:
                ready += 1
        economics_coverage[code] = {
            "advertised_products": len(campaign_nms), "matched_products": matched, "decision_ready_products": ready,
        }
        if campaign_nms and ready < len(campaign_nms):
            blockers.append(f"{name}: юнит-экономика подтверждена для {ready}/{len(campaign_nms)} рекламируемых товаров по точному nmID и кабинету.")
        elif not campaign_nms and sweep_rows[code]["products_total"]:
            blockers.append(f"{name}: рекламируемые nmID не переданы для проверки связи с юнит-экономикой.")

    summary = query_manager.get("summary") if isinstance(query_manager.get("summary"), dict) else {}
    query_rows = int(summary.get("query_rows") or 0)
    fresh_rows = int(summary.get("fresh_query_rows") or 0)
    blocked_rows = int(summary.get("blocked_query_rows") or 0)
    if summary.get("facts_status") != "all_fresh" or query_rows <= 0 or blocked_rows > 0:
        blockers.append(f"Поисковые факты не полностью свежие: свежих {fresh_rows}/{query_rows}, заблокировано {blocked_rows}.")
    for error in source_errors or []:
        if isinstance(error, dict):
            blockers.append(f"Ошибка источника: {error.get('stage') or 'неизвестный этап'}.")

    deduplicated = list(dict.fromkeys(blockers))
    return {
        "ready": not deduplicated,
        "status": "ready" if not deduplicated else "not_ready",
        "blockers": deduplicated,
        "shops": sweep_rows,
        "economics": {
            "snapshot_date": snapshot_date.isoformat() if snapshot_date else None,
            "age_days": age_days,
            "coverage_by_shop": economics_coverage,
        },
        "query_facts": {
            "status": summary.get("facts_status") or "unknown",
            "rows": query_rows, "fresh_rows": fresh_rows, "blocked_rows": blocked_rows,
            "ready_query_actions": int(summary.get("ready_query_actions") or 0),
        },
    }
