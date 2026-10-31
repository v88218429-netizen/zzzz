"""Evidence-gated review of Wildberries advertising search-cluster performance.

The output is advisory only. It never changes Wildberries settings. A numerical
test bid requires exact campaign/SKU economics, an exact current cluster bid,
manual CPM campaign settings, and fresh evidence.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from math import isfinite
from typing import Any


STORE_NAMES = {"ap": "Саныч", "aa": "AIR", "yv": "Хозяюшка"}


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(str(value).strip().replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _key(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _decision_caps(
    decisions: list[dict[str, Any]], economics_observed_at: Any, now: datetime, freshness_hours: float,
) -> dict[tuple[str, str, str], float]:
    caps: dict[tuple[str, str, str], float] = {}
    for card in decisions:
        if card.get("blockers") or str(card.get("confidence") or "").lower() == "low":
            continue
        key = str(card.get("decision_key") or "").split(":")
        if len(key) < 4 or key[0] != "advert" or not key[1].isdigit() or not key[2].isdigit():
            continue
        explicit_shop = str(card.get("shop") or card.get("store_id") or "").lower()
        # Economics must carry the cabinet identity at the source. A unique
        # campaign×SKU match is not proof of which seller token produced it.
        if explicit_shop not in STORE_NAMES:
            continue
        observed_value = economics_observed_at.get(explicit_shop) if isinstance(economics_observed_at, dict) else economics_observed_at
        observed = _time(observed_value)
        age = (now - observed).total_seconds() if observed else None
        if age is None or age < 0 or age > freshness_hours * 3600:
            continue
        cap = None
        for item in card.get("evidence") or []:
            if isinstance(item, dict) and item.get("metric") == "max_ad_cost_per_order_rub":
                cap = _num(item.get("value"))
                break
        if cap is not None and cap >= 0:
            caps[(explicit_shop, key[1], key[2])] = cap
    return caps


def _kopecks(value: Any) -> float | None:
    number = _num(value)
    return round(number / 100.0, 2) if number is not None and number >= 0 else None


def _wb_bid_guidance(
    campaigns: list[dict[str, Any]], observed_at: Any, now: datetime, freshness_hours: float,
) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    """Read only exact SKU × campaign WB recommendation bands from the optimizer snapshot."""
    output: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in campaigns:
        if not isinstance(item, dict):
            continue
        advert = str(item.get("campaign_id") or "")
        recommendations = item.get("recommendations") or {}
        if not advert.isdigit() or not isinstance(recommendations, dict):
            continue
        for nm, raw in recommendations.items():
            if not str(nm).isdigit() or not isinstance(raw, dict) or raw.get("_error"):
                continue
            explicit_shop = str(item.get("shop") or item.get("store_id") or "").lower()
            # The caller must preserve the cabinet/token scope from collection.
            if explicit_shop not in STORE_NAMES:
                continue
            observed_value = item.get("observed_at") or (
                observed_at.get(explicit_shop) if isinstance(observed_at, dict) else observed_at
            )
            observed = _time(observed_value)
            age = (now - observed).total_seconds() if observed else None
            if age is None or age < 0 or age > freshness_hours * 3600:
                continue
            data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
            base = data.get("base") if isinstance(data.get("base"), dict) else {}
            competitive = base.get("competitiveBid") if isinstance(base.get("competitiveBid"), dict) else {}
            leaders = base.get("leadersBid") if isinstance(base.get("leadersBid"), dict) else {}
            cluster_bands: dict[str, dict[str, float]] = {}
            norm_queries = data.get("normQueries") or []
            if isinstance(norm_queries, dict):
                norm_queries = norm_queries.get("items") or norm_queries.get("normQueries") or []
            if isinstance(norm_queries, list):
                for query in norm_queries:
                    if not isinstance(query, dict):
                        continue
                    label = str(query.get("normQuery") or query.get("query") or "").strip()
                    if not label:
                        continue
                    values = {}
                    for band in ("reachMin", "reachMedium", "reachMax"):
                        band_data = query.get(band) if isinstance(query.get(band), dict) else {}
                        value = _kopecks(band_data.get("bidKopecks"))
                        if value is not None:
                            values[band] = value
                    if values:
                        cluster_bands[_key(label)] = values
            for query, bands in cluster_bands.items():
                # A recommendation is a whole product×campaign snapshot. Do not
                # create fresh-looking partial bands from an old individual query.
                target = output.setdefault((explicit_shop, advert, str(nm), query), {})
                target.update({
                    "competitive_bid_rub": _kopecks(competitive.get("bidKopecks")),
                    "leaders_bid_rub": _kopecks(leaders.get("bidKopecks")),
                    "reach_min_bid_rub": bands.get("reachMin"),
                    "reach_medium_bid_rub": bands.get("reachMedium"),
                    "reach_max_bid_rub": bands.get("reachMax"),
                    "observed_at": observed.isoformat(),
                    "age_hours": round(age / 3600, 2),
                    "source": "WB wb_advert_bids_recommendations",
                })
    return output



def _current_cluster_bid_facts(
    campaigns: list[dict[str, Any]], observed_at: Any, now: datetime, freshness_hours: float,
) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    """Index exact current WB bids by seller × campaign × SKU × normalized query."""
    output: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in campaigns:
        if not isinstance(item, dict):
            continue
        shop = str(item.get("shop") or item.get("store_id") or "").lower()
        advert = str(item.get("campaign_id") or "")
        raw = item.get("cluster_bids")
        if shop not in STORE_NAMES or not advert.isdigit() or not isinstance(raw, (dict, list)):
            continue
        if isinstance(raw, dict) and raw.get("_error"):
            continue
        if isinstance(raw, list):
            bid_rows = raw
            response_currency = None
        else:
            bid_rows = raw.get("bids") or raw.get("data") or raw.get("items") or []
            response_currency = raw.get("currency")
        if not isinstance(bid_rows, list):
            continue
        observed_value = item.get("cluster_bids_observed_at") or (
            observed_at.get(shop) if isinstance(observed_at, dict) else observed_at
        )
        for bid in bid_rows:
            if not isinstance(bid, dict):
                continue
            bid_advert = str(bid.get("advert_id") or bid.get("advertId") or advert)
            nm = str(bid.get("nm_id") or bid.get("nmId") or "")
            query = str(bid.get("norm_query") or bid.get("normQuery") or "").strip()
            currency = str(bid.get("currency") or response_currency or "").upper()
            if bid_advert != advert or not nm.isdigit() or not query or currency != "RUB":
                continue
            observed = _time(bid.get("observed_at") or bid.get("observedAt") or observed_value)
            age = (now - observed).total_seconds() if observed else None
            if age is None or age < 0 or age > freshness_hours * 3600:
                continue
            minor = None
            for field in ("id_kopecks", "bid_kopecks", "bidKopecks", "bid_minor_units"):
                minor = _num(bid.get(field))
                if minor is not None:
                    break
            if minor is not None and minor >= 0:
                rub = round(minor / 100.0, 2)
                units = "kopecks"
            else:
                # The official legacy bid field is whole RUB.
                direct = _num(bid.get("bid"))
                if direct is None or direct < 0:
                    continue
                rub = round(direct, 2)
                units = "RUB"
            output[(shop, advert, nm, _key(query))] = {
                "current_bid_rub": rub,
                "observed_at": observed.isoformat(),
                "age_hours": round(age / 3600, 2),
                "source": "WB /adv/v0/normquery/get-bids",
                "currency": currency,
                "units_source": units,
            }
    return output



def analyze_cluster_rows(
    rows: list[dict[str, Any]],
    campaign_settings: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    query_manager: dict[str, Any] | None = None,
    *,
    bid_recommendations: list[dict[str, Any]] | None = None,
    bid_recommendations_observed_at: Any = None,
    now: datetime | None = None,
    freshness_hours: float = 36.0,
    economics_observed_at: Any = None,
    cluster_data_observed_at: Any = None,
) -> dict[str, Any]:
    """Aggregate exact store × campaign × SKU × WB cluster rows and gate advice."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    settings = {}
    for item in campaign_settings:
        if not isinstance(item, dict):
            continue
        identity = (str(item.get("shop") or "").lower(), str(item.get("advert_id") or ""), str(item.get("nm_id") or ""))
        if all(identity):
            settings[identity] = item
    row_shops = {str(row.get("shop") or "").lower() for row in rows if isinstance(row, dict)}
    # A single timestamp cannot establish which cabinet's economics were read
    # when the result combines several shops. The live API path supplies a
    # per-cabinet timestamp map; ambiguous shared inputs fail closed.
    economics_time = economics_observed_at
    if len(row_shops) > 1 and not isinstance(economics_observed_at, dict):
        economics_time = None
    caps = _decision_caps(decisions, economics_time, now, freshness_hours)
    wb_guidance = _wb_bid_guidance(
        bid_recommendations or [], bid_recommendations_observed_at, now, freshness_hours
    )
    exact_cluster_bids = _current_cluster_bid_facts(
        bid_recommendations or [], bid_recommendations_observed_at, now, freshness_hours
    )
    positions: dict[tuple[str, str, str], dict[str, Any]] = {}
    for card in ((query_manager or {}).get("cards") or []):
        if not isinstance(card, dict):
            continue
        store = str(card.get("store_id") or card.get("shop") or "").lower()
        nm = str(card.get("sku") or card.get("nm_id") or "")
        query = _key(card.get("query"))
        if store and nm and query:
            positions[(store, nm, query)] = card

    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            continue
        shop = str(row.get("shop") or row.get("store_id") or "").lower()
        advert = str(row.get("advert_id") or "")
        nm = str(row.get("nm_id") or row.get("nmId") or "")
        query = str(row.get("norm_query") or row.get("query") or "").strip()
        if shop in STORE_NAMES and advert.isdigit() and nm.isdigit() and query:
            grouped[(shop, advert, nm, _key(query))].append(row)

    output = []
    for (shop, advert, nm, query_key), day_rows in grouped.items():
        day_rows.sort(key=lambda x: str(x.get("date") or ""))
        def total(field: str) -> float | None:
            values = [_num(r.get(field)) for r in day_rows]
            return round(sum(v for v in values if v is not None), 3) if values and all(v is not None for v in values) else None
        clicks, spend, orders = total("clicks"), total("spend_rub"), total("orders")
        views = total("views")
        atbs = total("atbs")
        last = day_rows[-1]
        matching_bids = [r for r in day_rows if _num(r.get("cluster_bid_kopecks")) is not None and r.get("bid_observed_at")]
        bid_row = max(matching_bids, key=lambda r: _time(r.get("bid_observed_at")) or datetime.min.replace(tzinfo=timezone.utc), default=None)
        bid_time = _time(bid_row.get("bid_observed_at")) if bid_row else None
        bid_kopecks = _num(bid_row.get("cluster_bid_kopecks")) if bid_row else None
        api_bid = exact_cluster_bids.get((shop, advert, nm, query_key))
        api_bid_time = _time((api_bid or {}).get("observed_at"))
        if api_bid and (bid_time is None or (api_bid_time and api_bid_time > bid_time)):
            bid_time = api_bid_time
            bid_kopecks = round(float(api_bid["current_bid_rub"]) * 100, 0)
        state_rows = [r for r in day_rows if r.get("cluster_state") and r.get("cluster_state_observed_at")]
        state_row = max(state_rows, key=lambda r: _time(r.get("cluster_state_observed_at")) or datetime.min.replace(tzinfo=timezone.utc), default=None)
        state_time = _time((state_row or {}).get("cluster_state_observed_at"))
        state_age = (now - state_time).total_seconds() if state_time else None
        state_fresh = bool(state_age is not None and 0 <= state_age <= freshness_hours * 3600)
        state = str((state_row or bid_row or last).get("cluster_state") or "").lower()
        setting = settings.get((shop, advert, nm), {})
        payment_type = str(setting.get("payment_type") or "").lower()
        bid_type = str(setting.get("bid_type") or "").lower()
        settings_time = _time(setting.get("observed_at"))
        setting_age = (now - settings_time).total_seconds() if settings_time else None
        setting_fresh = bool(setting_age is not None and 0 <= setting_age <= freshness_hours * 3600)
        search_bid_kopecks = _num(setting.get("search_bid_kopecks"))
        search_placement = str(setting.get("search_placement") or "").strip().lower() in {"true", "1", "yes"}
        campaign_status = str(setting.get("status") or "")
        campaign_search_bid_available = bool(
            setting_fresh and campaign_status == "9"
            and payment_type == "cpm" and bid_type == "manual" and search_placement
            and str(setting.get("currency") or "").upper() == "RUB"
            and search_bid_kopecks is not None and search_bid_kopecks > 0
        )
        bid_source = "cluster" if bid_kopecks is not None else None
        max_cpa = caps.get((shop, advert, nm))
        cpa = round(spend / orders, 2) if spend is not None and orders is not None and orders > 0 else None
        ctr = round(clicks / views * 100, 2) if clicks is not None and views is not None and views > 0 else None
        cpc = round(spend / clicks, 2) if spend is not None and clicks is not None and clicks > 0 else None
        position = positions.get((shop, nm, query_key))
        wb_bid_guidance = wb_guidance.get((shop, advert, nm, query_key))
        if api_bid:
            wb_bid_guidance = dict(wb_bid_guidance or {})
            wb_bid_guidance["current_bid_rub"] = api_bid.get("current_bid_rub")
            wb_bid_guidance["current_bid_observed_at"] = api_bid.get("observed_at")
            wb_bid_guidance["current_bid_source"] = api_bid.get("source")
        cluster_observed_value = cluster_data_observed_at.get(shop) if isinstance(cluster_data_observed_at, dict) else cluster_data_observed_at
        cluster_observed = _time(cluster_observed_value)
        cluster_age = (now - cluster_observed).total_seconds() if cluster_observed else None
        cluster_fresh = bool(cluster_age is not None and 0 <= cluster_age <= freshness_hours * 3600)
        position_value = _num(position.get("position")) if position else None
        position_freshness = ((position or {}).get("freshness") or {}).get("position") or {}
        blockers = []
        if not cluster_fresh:
            blockers.append("Время получения фактической статистики WB по кластерам отсутствует, устарело или находится в будущем.")
        if clicks is None or spend is None or orders is None:
            blockers.append("Нет полных расходов, переходов и заказов по кластеру за выбранный период.")
        if max_cpa is None:
            blockers.append("Не найден свежий точный допустимый расход на заказ для этой кампании и товара.")
        elif max_cpa <= 0:
            blockers.append("Точная экономика товара не допускает рекламных расходов; ставку по кластеру не предлагаем менять.")
        if bid_kopecks is None or bid_kopecks <= 0:
            blockers.append("Индивидуальная ставка этого кластера не получена; ставка поиска товара в кампании показана отдельно и не подменяет её.")
        elif bid_time is None or not 0 <= (now - bid_time).total_seconds() <= freshness_hours * 3600:
            blockers.append("Снимок текущей ставки устарел; обновить рекламу перед решением.")
        if state != "active" or not state_fresh:
            blockers.append("Текущее активное состояние кластера WB не подтверждено.")
        if settings_time is None or not 0 <= (now - settings_time).total_seconds() <= freshness_hours * 3600:
            blockers.append("Снимок настроек кампании устарел или отсутствует; обновить рекламу перед решением.")
        # WB exposes writable cluster bids only for manual CPM campaigns; require
        # the exact live per-cluster bid and a fresh campaign payment setting.
        if payment_type != "cpm" or bid_type != "manual":
            blockers.append("Настройки кампании не подтверждают ручную ставку CPM, необходимую для изменения ставки кластера.")
        if state == "excluded":
            blockers.append("WB пометил кластер как исключённый; предложение изменить его ставку заблокировано.")

        action, reason, target_bid = "ТРЕБУЕТ ПРОВЕРКИ", "Недостаточно подтверждённых данных для решения.", None
        if not blockers:
            current = bid_kopecks / 100.0
            if orders == 0 and clicks >= 20 and spend >= max(250.0, (max_cpa or 0) * 0.5):
                action = "ПРОВЕРИТЬ ИСКЛЮЧЕНИЕ КЛАСТЕРА"
                reason = "Есть заметный расход и не менее 20 переходов без заказа; исключение требует ручной проверки состава кластера."
            elif cpa is not None and max_cpa is not None and cpa > max_cpa * 1.15:
                reduction = min(0.15, max(0.0, 1.0 - max_cpa / cpa))
                candidate = round(current * (1.0 - reduction), 2)
                if candidate < current:
                    action = "ТЕСТОВО СНИЗИТЬ СТАВКУ"
                    target_bid = candidate
                    reason = (f"CPA {cpa:.2f} ₽ выше лимита {max_cpa:.2f} ₽; расчётный шаг −{reduction * 100:.1f}% "
                              "ограничен 15% за одну проверку. Это пробный шаг, а не обещание линейного снижения CPA; "
                              "сверить расход и заказы через 1 и 3 дня.")
                else:
                    action = "СОБРАТЬ БОЛЬШЕ ДАННЫХ"
                    reason = "Текущая ставка слишком мала для различимого денежного шага с точностью до 0,01 ₽."
            elif orders is not None and orders >= 2 and cpa is not None and max_cpa is not None and cpa <= max_cpa:
                action = "ОСТАВИТЬ СТАВКУ"
                target_bid = round(current, 2)
                reason = f"CPA {cpa:.2f} ₽ укладывается в лимит {max_cpa:.2f} ₽; повышение без подтверждённой дополнительной прибыли не предлагается."
            else:
                action = "СОБРАТЬ БОЛЬШЕ ДАННЫХ"
                reason = "Наблюдений пока недостаточно, чтобы безопасно менять ставку."
        else:
            action = "СОВЕТ ПО СТАВКЕ ЗАБЛОКИРОВАН"

        output.append({
            "shop": shop, "store_name": STORE_NAMES[shop], "advert_id": advert, "nm_id": nm,
            "seller_article": str(last.get("seller_article") or ""), "campaign_name": str(last.get("campaign_name") or ""),
            "norm_query": str(last.get("norm_query") or last.get("query") or ""),
            "date_from": min(str(r.get("date") or "") for r in day_rows), "date_to": max(str(r.get("date") or "") for r in day_rows),
            "stats_observed_at": cluster_observed.isoformat() if cluster_observed else None,
            "stats_age_hours": round(cluster_age / 3600, 2) if cluster_age is not None else None,
            "views": views, "clicks": clicks, "atbs": atbs, "orders": orders, "spend_rub": spend,
            "ctr_pct": ctr, "cpc_rub": cpc, "cpa_rub": cpa, "max_cpa_rub": max_cpa,
            "current_bid_rub": round(bid_kopecks / 100, 2) if bid_kopecks is not None else None,
            "current_bid_source": bid_source,
            "current_bid_source_detail": (api_bid or {}).get("source") if api_bid else (str(last.get("source") or "") if bid_kopecks is not None else None),
            "wb_bid_guidance": wb_bid_guidance,
            "campaign_search_bid_rub": round(search_bid_kopecks / 100, 2) if campaign_search_bid_available else None,
            "campaign_search_bid_at": settings_time.isoformat() if campaign_search_bid_available and settings_time else None,
            "target_bid_rub": target_bid, "bid_observed_at": bid_time.isoformat() if bid_time else None,
            "cluster_state": state or None,
            "cluster_state_observed_at": state_time.isoformat() if state_time else None,
            "cluster_state_age_hours": round(state_age / 3600, 2) if state_age is not None else None,
            "organic_position": position_value,
            "organic_position_status": position_freshness.get("status", "missing"),
            "action": action, "reason": reason, "blockers": blockers,
            "source": str(last.get("source") or "WB /adv/v1/normquery/stats"),
            "quality": str(last.get("quality") or ""), "manual_only": True,
        })
    output.sort(key=lambda x: (x["shop"], x["action"], -(x["spend_rub"] or 0)))
    return {
        "source": "WB /adv/v1/normquery/stats + /adv/v0/normquery/get-bids + /adv/v0/normquery/list",
        "generated_at": now.isoformat(), "window_rows": len(rows), "cluster_rows": len(output),
        "advice_count": sum(1 for x in output if x["action"] not in {"ТРЕБУЕТ ПРОВЕРКИ", "СОВЕТ ПО СТАВКЕ ЗАБЛОКИРОВАН", "СОБРАТЬ БОЛЬШЕ ДАННЫХ"}),
        "blocked_count": sum(1 for x in output if x["blockers"]), "rows": output,
        "write_mode": "read_only_recommendations",
    }
