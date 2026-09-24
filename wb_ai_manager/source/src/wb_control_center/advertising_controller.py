from __future__ import annotations

from dataclasses import dataclass, asdict
from math import isfinite
from statistics import mean
from typing import Any

from .demand_forecast import build_demand_forecast


def _n(v: Any) -> float | None:
    try:
        if v is None or isinstance(v, bool):
            return None
        return float(v)
    except Exception:
        return None


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _first(d: dict[str, Any] | None, *keys: str) -> Any:
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _extract_bid_rub(campaign: dict[str, Any] | None, nm_id: int | None = None) -> float | None:
    """Extract the current bid without crossing SKU branches.

    WB payloads sometimes nest the bid one or two levels below the dict that carries
    ``nmId``.  The previous recursive parser lost that parent context and could read a
    neighbouring SKU's bid in a multi-SKU campaign.  Keep the inherited nm context and
    prefer an exact SKU-scoped bid over a campaign-wide fallback.
    """
    if not isinstance(campaign, dict):
        return None
    candidates: list[tuple[int, int, float, bool]] = []

    def direct_nm(x: dict[str, Any]) -> int | None:
        for key in ("nmId", "nmID", "nm_id", "nm"):
            n = _n(x.get(key))
            if n is not None and n > 0:
                return int(n)
        return None

    def walk(x: Any, inherited_nm: int | None = None, depth: int = 0):
        if isinstance(x, dict):
            own_nm = direct_nm(x)
            effective_nm = own_nm if own_nm is not None else inherited_nm
            if nm_id is None:
                scope = 1
                matches = True
            elif effective_nm is None:
                scope = 1  # campaign-wide fallback
                matches = True
            elif int(effective_nm) == int(nm_id):
                scope = 3  # exact SKU branch
                matches = True
            else:
                scope = 0
                matches = False
            for k, v in x.items():
                lk = str(k).lower()
                val = _n(v)
                if val is not None and val > 0 and matches and ("bid" in lk or lk in {"cpm", "cpc"}):
                    explicit_kopecks = "kopeck" in lk or "kopecks" in lk
                    candidates.append((scope, -depth, val, explicit_kopecks))
                if isinstance(v, (dict, list)):
                    walk(v, effective_nm, depth + 1)
        elif isinstance(x, list):
            for y in x:
                walk(y, inherited_nm, depth + 1)

    walk(campaign)
    if not candidates:
        return None
    # Exact SKU scope first; for equal scope prefer the shallower/current setting.
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    _, _, val, kopecks = candidates[0]
    if kopecks:
        return val / 100.0
    if val >= 10000:
        return val / 100.0
    return val


def _extract_reco_rub(reco: Any) -> dict[str, float | None]:
    out = {"competitive": None, "leaders": None, "reach_min": None, "reach_medium": None, "reach_max": None}
    if not isinstance(reco, dict):
        return out
    base = reco.get("base") if isinstance(reco.get("base"), dict) else {}
    pairs = {
        "competitive": base.get("competitiveBid"),
        "leaders": base.get("leadersBid"),
    }
    for key, obj in pairs.items():
        if isinstance(obj, dict):
            n = _n(obj.get("bidKopecks"))
            out[key] = n / 100.0 if n is not None else None
    norm = reco.get("normQueries")
    if isinstance(norm, list) and norm:
        vals: dict[str, list[float]] = {"reach_min": [], "reach_medium": [], "reach_max": []}
        for row in norm:
            if not isinstance(row, dict):
                continue
            for key, src in (("reach_min", "reachMin"), ("reach_medium", "reachMedium"), ("reach_max", "reachMax")):
                obj = row.get(src)
                if isinstance(obj, dict):
                    n = _n(obj.get("bidKopecks"))
                    if n is not None and n > 0:
                        vals[key].append(n / 100.0)
        for key, arr in vals.items():
            out[key] = mean(arr) if arr else None
    return out


def _daily_rows(stats_row: dict[str, Any]) -> list[dict[str, Any]]:
    days = stats_row.get("days")
    return [x for x in days if isinstance(x, dict)] if isinstance(days, list) else []


def _metric(row: dict[str, Any], *keys: str) -> float:
    for k in keys:
        n = _n(row.get(k))
        if n is not None:
            return n
    return 0.0


def _trend_pct(daily: list[dict[str, Any]], metric_keys: tuple[str, ...], short_days: int, long_days: int) -> float | None:
    """Compare the recent window with a preceding baseline window.

    `short_days=3, long_days=14` means last 3 days vs up to 14 days immediately
    before them. This avoids diluting acceleration by including the recent spike in
    its own baseline. If history is shorter, use whatever prior days are available.
    """
    if len(daily) < max(2, short_days + 1):
        return None
    vals = [_metric(x, *metric_keys) for x in daily]
    short = vals[-short_days:]
    prior = vals[:-short_days]
    baseline = prior[-min(long_days, len(prior)):] if prior else []
    if not baseline:
        return None
    long_base = mean(baseline)
    if long_base <= 0:
        return None
    return (mean(short) / long_base - 1.0) * 100.0


def _derived_trend_pct(daily: list[dict[str, Any]], fn, short_days: int, long_days: int) -> float | None:
    if len(daily) < max(2, short_days + 1):
        return None
    vals=[]
    for row in daily:
        try:
            v=fn(row)
        except Exception:
            v=None
        vals.append(v if v is not None else 0.0)
    short=vals[-short_days:]
    prior=vals[:-short_days]
    baseline=prior[-min(long_days,len(prior)):] if prior else []
    if not baseline:
        return None
    b=mean(baseline)
    if b<=0:
        return None
    return (mean(short)/b-1.0)*100.0


def _campaign_nm_ids(campaign: dict[str, Any] | None) -> list[int]:
    if not isinstance(campaign, dict):
        return []
    ids: set[int] = set()
    def walk(x: Any):
        if isinstance(x, dict):
            for k, v in x.items():
                lk = str(k).lower()
                if lk in {"nmid", "nmid", "nm_id", "nm"}:
                    n = _n(v)
                    if n and n > 0:
                        ids.add(int(n))
                elif lk in {"nmids", "nm_ids", "nms"} and isinstance(v, list):
                    for y in v:
                        if isinstance(y, (int, float, str)):
                            n = _n(y)
                            if n and n > 0: ids.add(int(n))
                walk(v)
        elif isinstance(x, list):
            for y in x: walk(y)
    walk(campaign)
    return sorted(ids)


@dataclass
class AdvertisingControlPlan:
    campaign_id: str
    nm_id: str | None
    decision: str
    decision_label: str
    current_bid_rub: float | None
    target_bid_rub: float | None
    bid_change_pct: float | None
    current_spend_24h_rub: float | None
    max_spend_next_24h_rub: float | None
    target_drr_pct: float
    economic_max_drr_pct: float | None
    observed_drr_pct: float | None
    clicks: float
    orders: float
    ctr_pct: float | None
    cpc_rub: float | None
    orders_trend_pct: float | None
    traffic_trend_pct: float | None
    search_frequency_trend_pct: float | None
    demand_forecast_growth_pct: float | None
    stock_days_now: float | None
    stock_days_forecast: float | None
    wb_competitive_bid_rub: float | None
    wb_leaders_bid_rub: float | None
    max_ad_cost_per_order_rub: float | None
    break_even_drr_pct: float | None
    forecast_orders_24h: float | None
    forecast_revenue_24h_rub: float | None
    economic_spend_ceiling_24h_rub: float | None
    ctr_trend_pct: float | None
    cpc_trend_pct: float | None
    conversion_trend_pct: float | None
    search_query: str | None
    search_position: float | None
    search_target_position: float | None
    search_position_delta: float | None
    cohort_maturity_pct: float | None
    cohort_open_orders_7d: float | None
    buyout_lag_p50_days: float | None
    buyout_lag_p90_days: float | None
    action_text: str
    observation_rule: str
    reasons: list[str]
    blockers: list[str]
    confidence: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AdvertisingController:
    """Numerical read-only advertising controller.

    It does not execute WB writes. It answers: hold / scale up / scale down / pause,
    with a numerical bid and internal 24h spend ceiling whenever evidence is sufficient.
    """

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg

    def build_plan(
        self,
        campaign: dict[str, Any],
        stats_row: dict[str, Any],
        product: dict[str, Any] | None = None,
        bid_recommendation: dict[str, Any] | None = None,
        campaign_budget: dict[str, Any] | None = None,
    ) -> AdvertisingControlPlan:
        cid = str(_first(campaign, "advertId", "advert_id", "id") or _first(stats_row, "advertId", "advert_id", "id") or "")
        nm_ids = _campaign_nm_ids(campaign)
        nm = int(product.get("sku")) if isinstance(product, dict) and str(product.get("sku") or "").isdigit() else (nm_ids[0] if len(nm_ids) == 1 else None)

        spend = _metric(stats_row, "sum", "spend", "cost")
        orders = _metric(stats_row, "orders", "ordersCount", "orders_count")
        revenue = _metric(stats_row, "sum_price", "revenue", "sales", "orderSum")
        clicks = _metric(stats_row, "clicks")
        views = _metric(stats_row, "views", "impressions")
        ctr = _n(stats_row.get("ctr")) or ((clicks / views * 100) if views > 0 else None)
        cpc = _n(stats_row.get("cpc")) or ((spend / clicks) if clicks > 0 else None)
        drr = (spend / revenue * 100) if revenue > 0 else None
        daily = _daily_rows(stats_row)

        short_days = int(self.cfg.get("trend_short_days", 3))
        long_days = int(self.cfg.get("trend_long_days", 14))
        campaign_order_trend = _trend_pct(daily, ("orders", "ordersCount", "orders_count"), short_days, long_days)
        traffic_trend = _trend_pct(daily, ("views", "impressions"), short_days, long_days)
        ctr_trend = _derived_trend_pct(
            daily,
            lambda r: ((_metric(r, "clicks") / _metric(r, "views", "impressions") * 100) if _metric(r, "views", "impressions") > 0 else 0),
            short_days, long_days,
        )
        cpc_trend = _derived_trend_pct(
            daily,
            lambda r: ((_metric(r, "sum", "spend", "cost") / _metric(r, "clicks")) if _metric(r, "clicks") > 0 else 0),
            short_days, long_days,
        )
        conversion_trend = _derived_trend_pct(
            daily,
            lambda r: ((_metric(r, "orders", "ordersCount", "orders_count") / _metric(r, "clicks") * 100) if _metric(r, "clicks") > 0 else 0),
            short_days, long_days,
        )

        max_trend = float(self.cfg.get("max_trend_pct_for_forecast", 60))
        demand = build_demand_forecast(product or {}, max_growth_pct=max_trend)
        product_order_trend = demand.order_acceleration_pct
        search_frequency_trend = demand.search_frequency_trend_pct
        order_trend = product_order_trend if product_order_trend is not None else campaign_order_trend
        if order_trend is not None:
            order_trend = _clamp(order_trend, -max_trend, max_trend)
        if traffic_trend is not None:
            traffic_trend = _clamp(traffic_trend, -max_trend, max_trend)
        if search_frequency_trend is not None:
            search_frequency_trend = _clamp(search_frequency_trend, -max_trend, max_trend)
        demand_forecast_growth = demand.order_acceleration_pct if demand.order_acceleration_pct is not None else 0.0

        current_bid = _extract_bid_rub(campaign, nm)
        rec = _extract_reco_rub(bid_recommendation or {})
        mode = str(self.cfg.get("strategy_mode", "balanced"))
        target_drr = float(self.cfg.get("target_drr_pct", 8))
        min_profit_cfg = float(self.cfg.get("min_profit_after_ads_rub", 0))
        min_clicks = int(self.cfg.get("min_clicks_for_numeric_ad_decision", 50))
        min_orders = int(self.cfg.get("min_orders_for_scale_up", 5))
        max_bid_step = float(self.cfg.get("max_bid_step_pct", 7)) / 100.0
        max_spend_step = float(self.cfg.get("max_spend_step_pct", 12)) / 100.0
        stock_scale = float(self.cfg.get("min_stock_days_for_scale", 10))
        stock_hold = float(self.cfg.get("min_stock_days_for_hold", 7))
        eval_hours = float(self.cfg.get("evaluation_window_hours", 3))
        max_internal_24 = float(self.cfg.get("max_internal_spend_24h_rub", 20000))
        weak_cr = float(self.cfg.get("weak_cr_change_pct", -20))
        weak_ctr = float(self.cfg.get("weak_ctr_change_pct", -20))
        cpc_warn = float(self.cfg.get("cpc_growth_warn_pct", 35))
        emergency_spend = float(self.cfg.get("zero_orders_spend_rub", 1500))

        economics_price = _n(product.get("price_rub")) if isinstance(product, dict) else None
        buyer_price = _n(product.get("price_client_rub")) if isinstance(product, dict) else None
        if economics_price is None:
            economics_price = buyer_price
        if buyer_price is None:
            buyer_price = economics_price
        profit = _n(product.get("profit_rub")) if isinstance(product, dict) else None
        planned_profit = _n(product.get("target_profit_rub")) if isinstance(product, dict) else None
        min_profit = planned_profit if planned_profit is not None and planned_profit > 0 else min_profit_cfg
        unit_drr = _n(product.get("drr_pct")) if isinstance(product, dict) else None
        historical_buyout = _n(product.get("historical_buyout_pct")) if isinstance(product, dict) else None
        # Weekly group buyouts/profit are deliberately NOT used as same-period gates for
        # fresh advertising demand. They describe older, already maturing cohorts. Current
        # order quality is judged only through lifecycle maturity, not by lining up calendar
        # weeks that contain different order cohorts.
        group_profit = _n(product.get("group_profit_rub")) if isinstance(product, dict) else None
        group_margin = _n(product.get("group_margin_pct")) if isinstance(product, dict) else None
        weekly_group = str(product.get("weekly_group") or "") if isinstance(product, dict) else ""
        cohort = product.get("cohort") if isinstance(product, dict) and isinstance(product.get("cohort"), dict) else {}
        cohort7 = cohort.get("recent_7d") if isinstance(cohort.get("recent_7d"), dict) else {}
        cohort_maturity = _n(cohort7.get("maturity_pct"))
        cohort_quality_ready = bool(cohort7.get("quality_ready"))
        cohort_open = _n(cohort7.get("open_orders"))
        cohort_orders = _n(cohort7.get("orders"))
        buyout_lag = cohort.get("buyout_lag") if isinstance(cohort.get("buyout_lag"), dict) else {}
        buyout_p50 = _n(buyout_lag.get("p50_days"))
        buyout_p90 = _n(buyout_lag.get("p90_days"))
        stock = _n(product.get("safe_stock")) if isinstance(product, dict) else None
        incoming = max(0.0, _n(product.get("planned_incoming_qty")) or 0.0) if isinstance(product, dict) else 0.0
        incoming_confirmed = bool(product.get("planned_incoming_confirmed")) if isinstance(product, dict) else False
        effective_stock = (stock + incoming) if stock is not None and incoming_confirmed else stock
        fallback_daily = _n(product.get("orders_per_day")) if isinstance(product, dict) else None
        forecast_daily = demand.forecast_orders_1d if demand.forecast_orders_1d is not None else fallback_daily
        stock_days_now = (stock / fallback_daily) if stock is not None and fallback_daily and fallback_daily > 0 else None
        stock_days_forecast = (effective_stock / forecast_daily) if effective_stock is not None and forecast_daily and forecast_daily > 0 else stock_days_now

        # Unit economics gives a hard ceiling, not a target. We recover the
        # contribution before ads from the current modeled unit profit and current
        # unit DRR. The controller never recommends spending above this ceiling.
        max_ad_cost_per_order = None
        break_even_drr = None
        pre_ad_contribution = None
        if economics_price and economics_price > 0 and profit is not None:
            model_drr = unit_drr if unit_drr is not None else drr
            current_ad_cost_model = economics_price * (model_drr or 0) / 100.0
            pre_ad_contribution = profit + current_ad_cost_model
            max_ad_cost_per_order = max(0.0, pre_ad_contribution - min_profit)
            break_even_drr = max_ad_cost_per_order / economics_price * 100.0
        economic_max_drr = break_even_drr
        effective_target_drr = min(target_drr, break_even_drr) if break_even_drr is not None else target_drr

        # Estimate how much of SKU demand this campaign currently carries. This keeps
        # the 24h spend ceiling tied to forecasted demand rather than an arbitrary
        # percentage of yesterday's spend.
        campaign_daily_orders = None
        if daily:
            sample = [_metric(x, "orders", "ordersCount", "orders_count") for x in daily[-min(7, len(daily)):]]
            if sample:
                campaign_daily_orders = mean(sample)
        if campaign_daily_orders is None and orders > 0:
            campaign_daily_orders = orders / max(1, len(daily) or 7)
        sku_recent = demand.recent_7d or fallback_daily
        campaign_share = None
        if campaign_daily_orders is not None and sku_recent and sku_recent > 0:
            campaign_share = _clamp(campaign_daily_orders / sku_recent, 0.0, 1.0)
        forecast_campaign_orders = None
        if forecast_daily is not None:
            forecast_campaign_orders = forecast_daily * campaign_share if campaign_share is not None else forecast_daily
        elif campaign_daily_orders is not None:
            forecast_campaign_orders = campaign_daily_orders
        avg_campaign_order_revenue = revenue / orders if orders > 0 and revenue > 0 else buyer_price
        forecast_revenue_24 = (forecast_campaign_orders * avg_campaign_order_revenue) if forecast_campaign_orders is not None and avg_campaign_order_revenue else None
        economic_spend_ceiling = (forecast_campaign_orders * max_ad_cost_per_order) if forecast_campaign_orders is not None and max_ad_cost_per_order is not None else None

        blockers: list[str] = []
        reasons: list[str] = []
        if nm is None:
            blockers.append("Нет однозначной связи рекламной кампании с товаром; точную ставку нельзя рекомендовать до сопоставления.")
        if clicks < min_clicks:
            blockers.append(f"Недостаточно кликов для изменения ставки: {clicks:.0f} из требуемых {min_clicks}.")
        if product is None:
            blockers.append("Нет связанной юнит-экономики и остатка товара.")
        elif economics_price is None or profit is None or break_even_drr is None:
            blockers.append("Нет полной юнит-экономики товара: без цены и прибыли нельзя безопасно повышать рекламную ставку.")

        # Current 24h spend: prefer last daily row, fall back to period average.
        spend_24 = None
        if daily:
            spend_24 = _metric(daily[-1], "sum", "spend", "cost")
        if spend_24 is None or spend_24 <= 0:
            days_in_window = max(1, len(daily) or 7)
            spend_24 = spend / days_in_window if spend > 0 else 0.0
        step_spend_ceiling = min(max_internal_24, spend_24 * (1 + max_spend_step)) if spend_24 and spend_24 > 0 else max_internal_24
        ceiling_candidates = [x for x in (economic_spend_ceiling, step_spend_ceiling, max_internal_24) if isinstance(x, (int, float)) and x >= 0]
        max_spend_24 = min(ceiling_candidates) if ceiling_candidates else None

        search_query = str(product.get("top_search_query") or "") if isinstance(product, dict) else ""
        search_position = _n(product.get("top_search_position")) if isinstance(product, dict) else None
        search_target = _n(product.get("top_search_target_position")) if isinstance(product, dict) else None
        search_delta = _n(product.get("search_position_delta")) if isinstance(product, dict) else None
        position_gap = (search_position - search_target) if search_position is not None and search_target is not None else None

        decision = "HOLD"
        label = "Ставку оставить без изменения"
        target_bid = current_bid

        enough_data = clicks >= min_clicks and orders >= min_orders and nm is not None and product is not None and break_even_drr is not None and drr is not None
        profitable = profit is not None and profit >= min_profit
        stock_ok = stock_days_forecast is None or stock_days_forecast >= stock_scale
        drr_ok = drr is None or drr <= effective_target_drr

        if orders <= 0 and spend >= emergency_spend:
            decision, label = "PAUSE_REVIEW", "Кампанию остановить на разбор"
            target_bid = current_bid
            max_spend_24 = 0.0
            reasons.append(f"Расход {spend:.0f} ₽ достиг порога {emergency_spend:.0f} ₽ при нуле заказов.")
        elif profit is not None and profit < 0:
            decision, label = "SCALE_DOWN", "Ставку снизить: товар сейчас убыточен"
            reasons.append(f"Юнит-экономика отрицательная: текущая прибыль {profit:.2f} ₽ на единицу.")
            if current_bid is not None:
                target_bid = current_bid * (1 - max_bid_step)
            if spend_24:
                max_spend_24 = min(max_spend_24 if max_spend_24 is not None else spend_24, spend_24 * (1 - max_spend_step))
        elif drr is not None and drr > effective_target_drr + 0.5 and clicks >= min_clicks:
            decision, label = "SCALE_DOWN", "Ставку снизить: реклама вышла за допустимую экономику"
            reasons.append(f"ДРР кампании {drr:.1f}% выше текущего допустимого уровня {effective_target_drr:.1f}%.")
            if break_even_drr is not None:
                reasons.append(f"По текущей юнитке предельный ДРР до заданной прибыли ≈{break_even_drr:.1f}%.")
            if current_bid is not None:
                ratio = effective_target_drr / max(drr, 0.01)
                target_bid = current_bid * max(1 - max_bid_step, ratio)
            if spend_24:
                max_spend_24 = min(max_spend_24 if max_spend_24 is not None else spend_24, spend_24 * (1 - min(max_spend_step, max(0.0, (drr-effective_target_drr)/max(drr,1)))))
        elif stock_days_forecast is not None and stock_days_forecast < stock_hold:
            decision, label = "CAP_DEMAND", "Ставку оставить; расход не увеличивать до восстановления запаса"
            reasons.append(f"При прогнозном спросе товара хватит примерно на {stock_days_forecast:.1f} дня.")
            target_bid = current_bid
            if spend_24 is not None:
                max_spend_24 = min(max_spend_24 if max_spend_24 is not None else spend_24, spend_24)
        elif conversion_trend is not None and conversion_trend <= weak_cr and clicks >= min_clicks:
            decision, label = "HOLD", "Ставку оставить; сначала разобраться с падением конверсии"
            reasons.append(f"Конверсия клика в заказ ухудшилась примерно на {conversion_trend:.1f}% к базовому окну.")
            target_bid = current_bid
            if spend_24 is not None:
                max_spend_24 = min(max_spend_24 if max_spend_24 is not None else spend_24, spend_24)
        elif cpc_trend is not None and cpc_trend >= cpc_warn and (ctr_trend is None or ctr_trend <= 0) and clicks >= min_clicks:
            decision, label = "SCALE_DOWN", "Ставку немного снизить: клик дорожает без улучшения отклика"
            reasons.append(f"CPC вырос примерно на {cpc_trend:.1f}%, а CTR не улучшился.")
            if current_bid is not None:
                target_bid = current_bid * (1 - min(max_bid_step, 0.05))
        elif enough_data and profitable and stock_ok and drr_ok and current_bid is not None:
            organic_hot = (
                (order_trend or 0) >= float(self.cfg.get("organic_growth_hold_threshold_pct", 20))
                and (search_frequency_trend is None or search_frequency_trend >= 0)
                and (search_delta is None or search_delta <= 0)
                and mode != "growth"
            )
            # If all-market orders are accelerating while position/frequency are not
            # worsening, paying more immediately is unnecessary. Keep exact bid and
            # re-evaluate after the evidence window.
            if organic_hot:
                decision, label = "HOLD", "Ставку оставить: спрос и так ускоряется"
                reasons.append(f"Общий темп заказов товара ускорился примерно на {order_trend:.1f}%.")
                if search_frequency_trend is not None:
                    reasons.append(f"Частотность спроса изменилась на {search_frequency_trend:+.1f}%.")
                if search_delta is not None:
                    reasons.append(f"Позиция изменилась на {search_delta:+.0f} мест (минус — улучшение).")
                target_bid = current_bid
            else:
                # Increase only when there is an actual visibility problem or the
                # campaign is clearly below its economic corridor. WB's recommendation
                # is a market reference, never an instruction by itself.
                visibility_problem = position_gap is not None and position_gap >= 5
                economics_room = drr is None or drr <= effective_target_drr * 0.9
                ceiling_refs = [x for x in (rec.get("competitive"), rec.get("reach_medium"), rec.get("leaders")) if isinstance(x,(int,float)) and x>0]
                market_ceiling = min(ceiling_refs) if ceiling_refs else current_bid * (1 + max_bid_step)
                if visibility_problem and economics_room:
                    # Размер повышения выводим из двух независимых сигналов: насколько
                    # позиция отстаёт от цели и сколько экономического запаса осталось.
                    visibility_pressure = _clamp(position_gap / 30.0, 0.0, 1.0)
                    headroom = _clamp((effective_target_drr - drr) / max(effective_target_drr, 0.01), 0.0, 1.0)
                    derived_step = max_bid_step * _clamp(0.30 + 0.70 * visibility_pressure, 0.30, 1.0) * _clamp(0.35 + 0.65 * headroom, 0.35, 1.0)
                    proposed = min(current_bid * (1 + derived_step), market_ceiling)
                    if proposed > current_bid * 1.005:
                        decision, label = "SCALE_UP", "Ставку повысить ограниченным шагом для проверки поисковой позиции"
                        target_bid = proposed
                        reasons.append(f"По запросу «{search_query or 'ключевой запрос'}» позиция {search_position:.0f}, цель {search_target:.0f}; отставание {position_gap:.0f} мест.")
                        reasons.append(f"Текущий ДРР {drr:.1f}% ниже допустимого потолка {effective_target_drr:.1f}%; размер шага рассчитан из отставания позиции и запаса экономики.")
                    else:
                        decision, label = "HOLD", "Ставку оставить: повышение уже не даёт безопасного коридора"
                elif drr is not None and drr <= effective_target_drr * 0.7 and (order_trend is None or order_trend <= 10):
                    economic_headroom = _clamp((effective_target_drr - drr) / max(effective_target_drr, 0.01), 0.0, 1.0)
                    derived_step = max_bid_step * _clamp(0.25 + 0.50 * economic_headroom, 0.25, 0.75)
                    proposed = min(current_bid * (1 + derived_step), market_ceiling)
                    if proposed > current_bid * 1.005:
                        decision, label = "SCALE_UP", "Ставку повысить небольшим проверочным шагом"
                        target_bid = proposed
                        reasons.append(f"ДРР {drr:.1f}% заметно ниже экономического потолка {effective_target_drr:.1f}%, а общий спрос сам не ускоряется; можно проверить, даст ли дополнительная видимость прирост заказов и прибыли.")
                else:
                    decision, label = "HOLD", "Ставку оставить без изменения"
                    target_bid = current_bid
                    reasons.append("Нет доказательства, что дополнительная ставка сейчас даст больше прибыли, а не просто больше расхода.")
        elif blockers:
            reasons.append("Точную ставку не меняем, пока не закрыты обязательные данные.")

        # Explain cohort maturity, but do not turn immature buyouts into a stop signal.
        if cohort_orders and cohort_orders > 0:
            if cohort_maturity is not None and not cohort_quality_ready:
                lag_text = ""
                if buyout_p50 is not None and buyout_p90 is not None:
                    lag_text = f" Типичный выкуп: медиана {buyout_p50:.1f} дня, 90% — примерно до {buyout_p90:.1f} дня."
                reasons.append(
                    f"Когорта заказов последних 7 дней созрела лишь примерно на {cohort_maturity:.0f}%; "
                    f"наблюдаемые сейчас выкупы не используются как оценка качества новых заказов.{lag_text}"
                )
            elif cohort_quality_ready:
                reasons.append("Когорта достаточно созрела, поэтому её фактический результат можно использовать как контроль качества спроса.")
            if cohort_open is not None and cohort_open > 0:
                reasons.append(f"В свежей 7-дневной когорте ещё {cohort_open:.0f} заказов находятся в незавершённом состоянии.")
        if group_profit is not None:
            reasons.append(
                (f"Фактическая прибыль группы «{weekly_group or 'товарная группа'}» за прошлый отчётный период: {group_profit:.0f} ₽"
                 + (f" при марже {group_margin:.1f}%" if group_margin is not None else "")
                 + ". Это контроль уже созревшего результата, а не прямой результат свежих заказов.")
            )

        if current_bid is not None and target_bid is not None:
            target_bid = _clamp(target_bid, current_bid * (1-max_bid_step), current_bid * (1+max_bid_step))
            target_bid = round(target_bid, 2)
            bid_change = (target_bid/current_bid - 1)*100 if current_bid > 0 else None
        else:
            bid_change = None

        if max_spend_24 is not None:
            max_spend_24 = round(_clamp(max_spend_24, 0, max_internal_24), 2)
        if economic_spend_ceiling is not None:
            economic_spend_ceiling = round(max(0.0, economic_spend_ceiling), 2)

        def money(v: float | None) -> str:
            return "—" if v is None else f"{v:,.0f}".replace(",", " ")
        if decision == "PAUSE_REVIEW":
            action_text = f"Не увеличивать ставку и бюджет. Кампанию пока остановить вручную. Новый расход: 0 ₽ до разбора причины отсутствия заказов."
        elif current_bid is not None and target_bid is not None and abs(target_bid-current_bid) >= 0.01:
            verb = "повысить" if target_bid > current_bid else "снизить"
            action_text = f"Ставку {verb}: {current_bid:.2f} → {target_bid:.2f} ₽. Расход на следующие 24 часа — не выше {money(max_spend_24)} ₽."
        elif current_bid is not None:
            action_text = f"Ставку оставить {current_bid:.2f} ₽. Расход на следующие 24 часа — не выше {money(max_spend_24)} ₽."
        else:
            action_text = "Ставку не менять: сначала получить текущую ставку и однозначно связать кампанию с товаром."

        confidence = "high"
        if blockers:
            confidence = "low" if len(blockers) >= 2 else "medium"
        elif not daily or demand.confidence == "low":
            confidence = "medium"

        return AdvertisingControlPlan(
            campaign_id=cid,
            nm_id=str(nm) if nm is not None else None,
            decision=decision,
            decision_label=label,
            current_bid_rub=round(current_bid,2) if current_bid is not None else None,
            target_bid_rub=target_bid,
            bid_change_pct=round(bid_change,2) if bid_change is not None and isfinite(bid_change) else None,
            current_spend_24h_rub=round(spend_24,2) if spend_24 is not None else None,
            max_spend_next_24h_rub=max_spend_24,
            target_drr_pct=round(effective_target_drr,2),
            economic_max_drr_pct=round(economic_max_drr,2) if economic_max_drr is not None else None,
            observed_drr_pct=round(drr,2) if drr is not None else None,
            clicks=clicks,
            orders=orders,
            ctr_pct=round(ctr,2) if ctr is not None else None,
            cpc_rub=round(cpc,2) if cpc is not None else None,
            orders_trend_pct=round(order_trend,2) if order_trend is not None else None,
            traffic_trend_pct=round(traffic_trend,2) if traffic_trend is not None else None,
            search_frequency_trend_pct=round(search_frequency_trend,2) if search_frequency_trend is not None else None,
            demand_forecast_growth_pct=round(demand_forecast_growth,2) if demand_forecast_growth is not None else None,
            stock_days_now=round(stock_days_now,2) if stock_days_now is not None else None,
            stock_days_forecast=round(stock_days_forecast,2) if stock_days_forecast is not None else None,
            wb_competitive_bid_rub=round(rec.get("competitive"),2) if rec.get("competitive") is not None else None,
            wb_leaders_bid_rub=round(rec.get("leaders"),2) if rec.get("leaders") is not None else None,
            max_ad_cost_per_order_rub=round(max_ad_cost_per_order,2) if max_ad_cost_per_order is not None else None,
            break_even_drr_pct=round(break_even_drr,2) if break_even_drr is not None else None,
            forecast_orders_24h=round(forecast_campaign_orders,2) if forecast_campaign_orders is not None else None,
            forecast_revenue_24h_rub=round(forecast_revenue_24,2) if forecast_revenue_24 is not None else None,
            economic_spend_ceiling_24h_rub=economic_spend_ceiling,
            ctr_trend_pct=round(ctr_trend,2) if ctr_trend is not None else None,
            cpc_trend_pct=round(cpc_trend,2) if cpc_trend is not None else None,
            conversion_trend_pct=round(conversion_trend,2) if conversion_trend is not None else None,
            search_query=search_query or None,
            search_position=round(search_position,2) if search_position is not None else None,
            search_target_position=round(search_target,2) if search_target is not None else None,
            search_position_delta=round(search_delta,2) if search_delta is not None else None,
            cohort_maturity_pct=round(cohort_maturity,2) if cohort_maturity is not None else None,
            cohort_open_orders_7d=round(cohort_open,2) if cohort_open is not None else None,
            buyout_lag_p50_days=round(buyout_p50,2) if buyout_p50 is not None else None,
            buyout_lag_p90_days=round(buyout_p90,2) if buyout_p90 is not None else None,
            action_text=action_text,
            observation_rule=f"Пересчитать не раньше чем через {eval_hours:g} ч и после накопления минимум {min_clicks} новых кликов либо при резком изменении цены/остатка/позиции.",
            reasons=reasons + demand.reasons[:3],
            blockers=blockers,
            confidence=confidence,
        )

