from __future__ import annotations

from datetime import date, datetime, timezone
import json
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
    warnings: list[str] = []
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
    source_retrieved_at = str(economics.get("source_retrieved_at") or "").strip()
    source_read_fresh = False
    if economics.get("snapshot_basis") == "live_source_read" and source_retrieved_at:
        try:
            retrieved = datetime.fromisoformat(source_retrieved_at.replace("Z", "+00:00"))
            if retrieved.tzinfo is None:
                retrieved = retrieved.replace(tzinfo=timezone.utc)
            read_age_hours = (now.astimezone(timezone.utc) - retrieved.astimezone(timezone.utc)).total_seconds() / 3600
            source_read_fresh = -0.25 <= read_age_hours <= 24
        except ValueError:
            source_read_fresh = False
    # The PRIMARY unit-economics workbook is a separately timestamped live source.
    # Do not let an unrelated stale inventory/traffic source invalidate a fresh
    # economics read; those sources have their own decision gates.
    source_current = portfolio.get("current_data") is True or source_read_fresh
    economics_ok = bool(
        source_current
        and economics.get("semantics") in {"plan_model_only", "plan_model_plus_independent_fact"}
        and snapshot_date is not None and 0 <= age_days <= 7
    )
    if not economics_ok:
        blockers.append("Юнит-экономика не подтверждена как текущая: нужен живой снимок модели с датой не старше 7 дней.")
    products = [p for p in own.get("products", []) if isinstance(p, dict)]
    product_by_shop: dict[str, dict[str, dict[str, Any]]] = {code: {} for code in SHOP_NAMES}
    exact_by_shop = own.get("unit_economics_by_shop_nm_id") if isinstance(own.get("unit_economics_by_shop_nm_id"), dict) else {}
    mapping_diagnostics = own.get("unit_economics_mapping_diagnostics_by_shop") if isinstance(own.get("unit_economics_mapping_diagnostics_by_shop"), dict) else {}
    for product in products:
        code = _shop_code(product.get("cabinet") or product.get("store_id") or product.get("shop"))
        sku = str(product.get("sku") or product.get("nm_id") or product.get("nmId") or "").strip()
        if code and sku.isdigit():
            product_by_shop[code][sku] = product

    economics_coverage: dict[str, dict[str, int]] = {}
    economics_diagnostics: dict[str, list[dict[str, Any]]] = {}
    for code, name in SHOP_NAMES.items():
        advertised_nm_ids = (shops.get(code) or {}).get("advertised_nm_ids")
        if isinstance(advertised_nm_ids, list):
            campaign_nms = {
                str(nm).strip() for nm in advertised_nm_ids
                if str(nm).strip().isdigit() and int(str(nm).strip()) > 0
            }
        else:
            # Backward-compatible path for snapshots written before the live
            # campaign-product catalog was copied out of the durable cursor.
            campaign_nms: set[str] = set()
            for campaign in (shops.get(code) or {}).get("campaigns") or []:
                campaign_nms.update(_campaign_nms(campaign))
        matched = 0
        ready = 0
        diagnostics: list[dict[str, Any]] = []
        for sku in sorted(campaign_nms, key=int):
            exact_product = (exact_by_shop.get(code) or {}).get(sku)
            product = exact_product if isinstance(exact_product, dict) else product_by_shop[code].get(sku)
            if product is None:
                mapping_issue = (mapping_diagnostics.get(code) or {}).get(sku)
                mapping_issue = mapping_issue if isinstance(mapping_issue, dict) else {}
                diagnostic = {
                    "nm_id": sku,
                    "stage": "unit_economics_mapping" if mapping_issue else "shop_nm_id_to_portfolio_product",
                    "seller_article": mapping_issue.get("seller_article"),
                    "product_name": mapping_issue.get("product_name"),
                    "source": mapping_issue.get("source"),
                    "source_row": mapping_issue.get("source_row"),
                    "audit_source_row": mapping_issue.get("audit_source_row"),
                    "mapping_key": mapping_issue.get("key"),
                    "reason_codes": mapping_issue.get("reason_codes") or ["exact_shop_nm_id_match_missing"],
                }
                public_details_by_nm_id = (shops.get(code) or {}).get("public_product_details_by_nm_id")
                public_details_by_nm_id = public_details_by_nm_id if isinstance(public_details_by_nm_id, dict) else {}
                public_details = public_details_by_nm_id.get(sku)
                if isinstance(public_details, dict):
                    if public_details.get("name"):
                        diagnostic["product_name"] = str(public_details["name"])
                    if public_details.get("brand"):
                        diagnostic["brand"] = str(public_details["brand"])
                    if public_details.get("product_url"):
                        diagnostic["product_url"] = str(public_details["product_url"])
                    if public_details.get("source"):
                        diagnostic["identity_source"] = str(public_details["source"])
                    diagnostic["reason_text"] = (
                        "Рекламируемый товар отсутствует в списке товаров кабинета; "
                        "юнит-экономика и числовое решение не подтверждены."
                    )
                diagnostics.append({key: value for key, value in diagnostic.items() if value is not None})
                continue
            matched += 1
            cost = _number(product.get("cost_rub"))
            price = _number(product.get("price_rub") or product.get("price_client_rub"))
            profit = _number(product.get("profit_rub"))
            reasons = []
            if product.get("unit_economics_decision_ready") is not True:
                reasons.append("unit_economics_not_decision_ready")
            validation_reasons = product.get("unit_economics_validation_reasons")
            if isinstance(validation_reasons, list):
                reasons.extend(str(x) for x in validation_reasons if x)
            if cost is None:
                reasons.append("cost_rub_missing_or_unreadable")
            elif cost <= 0:
                reasons.append("cost_rub_not_positive")
            if price is None:
                reasons.append("price_rub_missing_or_unreadable")
            elif price <= 0:
                reasons.append("price_rub_not_positive")
            if profit is None:
                reasons.append("profit_rub_missing_or_unreadable")
            if reasons:
                diagnostics.append({
                    "nm_id": sku, "stage": "unit_economics_validation",
                    "seller_article": product.get("seller_article") or product.get("article"),
                    "product_name": product.get("name"),
                    "source": product.get("unit_economics_source"),
                    "source_row": product.get("unit_economics_source_row") or product.get("source_row"),
                    "audit_source_row": product.get("audit_source_row"),
                    "mapping_key": product.get("unit_economics_source_key"),
                    "reason_codes": list(dict.fromkeys(reasons)),
                })
            else:
                ready += 1
        economics_diagnostics[code] = diagnostics
        economics_coverage[code] = {
            "advertised_products": len(campaign_nms), "matched_products": matched, "decision_ready_products": ready,
        }
        if campaign_nms and ready < len(campaign_nms):
            blockers.append(f"{name}: юнит-экономика подтверждена для {ready}/{len(campaign_nms)} рекламируемых товаров по точному nmID и кабинету.")
        elif not campaign_nms and sweep_rows[code]["products_total"]:
            blockers.append(f"{name}: рекламируемые nmID не переданы для проверки связи с юнит-экономикой.")

    if economics.get("semantics") == "plan_model_only":
        warnings.append("Основная юнит-экономика содержит свежую плановую модель без отдельного факта продаж; фактические рекламные расходы, переходы и заказы проверяются по данным WB.")

    summary = query_manager.get("summary") if isinstance(query_manager.get("summary"), dict) else {}
    query_rows = int(summary.get("query_rows") or 0)
    fresh_rows = int(summary.get("fresh_query_rows") or 0)
    blocked_rows = int(summary.get("blocked_query_rows") or 0)
    if summary.get("facts_status") != "all_fresh" or query_rows <= 0 or blocked_rows > 0:
        # Organic search facts are useful context, but they are not required for
        # an independently evidenced ads decision based on exact campaign/SKU
        # statistics, current bids, and primary economics. Keep the limitation
        # visible without making it a global ad-optimizer gate.
        warnings.append(f"Поисковые факты неполные: свежих {fresh_rows}/{query_rows}, заблокировано {blocked_rows}; рекламные решения не используют их как обязательное условие.")
    for error in source_errors or []:
        if isinstance(error, dict):
            blockers.append(f"Ошибка источника: {error.get('stage') or 'неизвестный этап'}.")

    deduplicated = list(dict.fromkeys(blockers))
    return {
        "ready": not deduplicated,
        "status": "ready" if not deduplicated else "not_ready",
        "blockers": deduplicated,
        "warnings": warnings,
        "shops": sweep_rows,
        "economics": {
            "snapshot_date": snapshot_date.isoformat() if snapshot_date else None,
            "age_days": age_days,
            "coverage_by_shop": economics_coverage,
        },
        "economics_diagnostics_by_shop": economics_diagnostics,
        "query_facts": {
            "status": summary.get("facts_status") or "unknown",
            "rows": query_rows, "fresh_rows": fresh_rows, "blocked_rows": blocked_rows,
            "ready_query_actions": int(summary.get("ready_query_actions") or 0),
        },
    }



def merge_campaign_cache(
    previous: dict[str, Any] | None,
    deep_scan: dict[str, Any],
    observed_at: str | None,
) -> dict[str, Any]:
    """Accumulate bounded campaign scans without refreshing failed or old facts."""
    prior = previous if isinstance(previous, dict) else {}
    raw_ids = deep_scan.get("active_campaign_ids")
    catalog_known = deep_scan.get("catalog_complete") is True and isinstance(raw_ids, list)
    active_ids = sorted({str(x).strip() for x in (raw_ids or []) if str(x).strip().isdigit()}, key=int)
    no_active = catalog_known and not active_ids and deep_scan.get("note") == "no active campaigns"
    old_ids = prior.get("campaign_ids")
    old_ids = sorted({str(x) for x in old_ids if str(x).isdigit()}, key=int) if isinstance(old_ids, list) else []
    old_rows = prior.get("campaigns") if isinstance(prior.get("campaigns"), list) else []
    rows_by_id: dict[str, dict[str, Any]] = {}
    if (not catalog_known) or old_ids == active_ids:
        for row in old_rows:
            if isinstance(row, dict) and str(row.get("campaign_id") or "").isdigit():
                rows_by_id[str(row["campaign_id"])] = row

    def merge_value(old: Any, new: Any) -> Any:
        if isinstance(old, dict) and isinstance(new, dict):
            merged = dict(old)
            for key, value in new.items():
                merged[key] = merge_value(merged.get(key), value) if key in merged else value
            return merged
        if isinstance(old, list) and isinstance(new, list):
            merged = list(old)
            seen = {json.dumps(x, sort_keys=True, ensure_ascii=False, default=str) for x in merged}
            for value in new:
                marker = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
                if marker not in seen:
                    merged.append(value)
                    seen.add(marker)
            return merged
        return new

    scans = deep_scan.get("campaigns") if isinstance(deep_scan.get("campaigns"), list) else []
    for scan in scans:
        if not isinstance(scan, dict):
            continue
        cid = str(scan.get("campaign_id") or "").strip()
        if not cid.isdigit() or (catalog_known and cid not in active_ids):
            continue
        if scan.get("scan_complete") is not True:
            continue
        row = merge_value(rows_by_id.get(cid, {}), scan)
        if isinstance(scan.get("nm_ids"), list):
            # The scanned campaign catalog is authoritative; do not accumulate
            # products that are no longer attached to this active campaign.
            current_nms = sorted({
                int(value) for value in scan["nm_ids"]
                if str(value).strip().isdigit() and int(value) > 0
            })
            row["nm_ids"] = current_nms
            row["recommendation_nm_ids_scanned"] = sorted({
                int(value) for value in (scan.get("recommendation_nm_ids_scanned") or [])
                if str(value).strip().isdigit() and int(value) > 0
            })
            current_nm_keys = {str(nm) for nm in current_nms}
            for field in ("stats_by_nm", "recommendations"):
                values = row.get(field)
                if isinstance(values, dict):
                    row[field] = {
                        key: value for key, value in values.items()
                        if str(key).strip().isdigit() and str(int(key)) in current_nm_keys
                    }
        row["campaign_id"] = int(cid)
        row["observed_at"] = observed_at or row.get("observed_at")
        row["scan_complete"] = True
        rows_by_id[cid] = row

    if no_active:
        rows_by_id = {}
    campaigns = [rows_by_id[cid] for cid in sorted(rows_by_id, key=int) if not catalog_known or cid in active_ids]
    total = len(active_ids) if catalog_known else int(deep_scan.get("sweep_campaigns_total") or 0)
    cache_complete = bool(catalog_known and len(campaigns) == total and all(row.get("scan_complete") is True for row in campaigns))
    sweep_complete = bool(no_active or (deep_scan.get("sweep_complete") is True and cache_complete))
    prior_errors = prior.get("scan_errors") if isinstance(prior.get("scan_errors"), list) else []
    current_errors = deep_scan.get("scan_errors") if isinstance(deep_scan.get("scan_errors"), list) else []
    error_rows = [
        row for row in prior_errors + current_errors
        if isinstance(row, dict) and str(row.get("campaign_id") or "").isdigit()
    ]
    latest_errors: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in error_rows:
        cid = str(row.get("campaign_id"))
        if catalog_known and active_ids and cid not in active_ids:
            continue
        key = (cid, str(row.get("nm_id") or ""), str(row.get("endpoint") or ""))
        previous_error = latest_errors.get(key)
        if previous_error is None or str(row.get("observed_at") or "") >= str(previous_error.get("observed_at") or ""):
            latest_errors[key] = row
    scan_errors = sorted(
        latest_errors.values(),
        key=lambda row: str(row.get("observed_at") or ""),
        reverse=True,
    )[:100]
    return {
        "campaign_ids": active_ids if catalog_known else old_ids,
        "campaigns": campaigns,
        "campaign_cache_count": len(campaigns),
        "campaign_cache_total": total,
        "campaign_cache_complete": cache_complete,
        "catalog_complete": catalog_known,
        "sweep_complete": sweep_complete,
        "scan_errors": scan_errors,
        "sweep": {key: deep_scan.get(key) for key in (
            "sweep_id", "sweep_campaigns_seen", "sweep_campaigns_total",
            "sweep_products_seen", "sweep_products_total", "cursor_before",
            "cursor_after", "eligible_campaigns", "campaigns_scanned",
        ) if key in deep_scan},
    }
