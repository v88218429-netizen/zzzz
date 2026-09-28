from __future__ import annotations

from dataclasses import dataclass, asdict
from statistics import mean
from typing import Any

from .demand_forecast import build_demand_forecast


def _n(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except Exception:
        return None


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return None


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _walk_query_rows(value: Any, inherited_nm: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            rows.extend(_walk_query_rows(item, inherited_nm))
        return rows
    if not isinstance(value, dict):
        return rows

    own_nm = _first(value, "nmId", "nmID", "nm_id", "nm", "sku")
    nm = str(own_nm) if own_nm is not None else inherited_nm
    query = _first(value, "query", "keyword", "searchQuery", "search_query", "text", "phrase")
    if query:
        row = dict(value)
        if nm is not None:
            row["_nm"] = nm
        row["_query"] = str(query).strip()
        rows.append(row)

    for nested in value.values():
        if isinstance(nested, (dict, list)):
            rows.extend(_walk_query_rows(nested, nm))
    return rows


def _query_metrics(row: dict[str, Any]) -> dict[str, Any]:
    orders = _n(_first(row, "orders", "ordersCount", "orderCount", "buyouts", "sales"))
    clicks = _n(_first(row, "clicks", "clicksCount", "openCardCount", "cardOpens"))
    frequency = _n(_first(row, "frequency", "searchFrequency", "search_frequency", "freq", "requestCount"))
    position = _n(_first(row, "position", "avgPosition", "averagePosition", "rank"))
    cvr = _n(_first(row, "conversion", "conversionRate", "conversion_rate", "cvr", "cr"))
    if cvr is None and orders is not None and clicks and clicks > 0:
        cvr = orders / clicks * 100.0
    trend = _n(_first(row, "frequencyTrendPct", "frequency_trend_pct", "trendPct", "trend_pct"))
    return {
        "query": str(row.get("_query") or ""),
        "orders": max(0.0, orders or 0.0),
        "clicks": max(0.0, clicks or 0.0),
        "frequency": max(0.0, frequency or 0.0),
        "position": position,
        "conversion_pct": cvr,
        "frequency_trend_pct": trend,
    }


def _product_query_rows(product: dict[str, Any], snapshots: dict[str, Any]) -> list[dict[str, Any]]:
    sku = str(product.get("sku") or product.get("nm_id") or product.get("nmID") or "")
    candidates: list[dict[str, Any]] = []
    for key in ("query_history", "search_queries", "queries", "keyword_history"):
        value = product.get(key)
        if isinstance(value, (list, dict)):
            candidates.extend(_walk_query_rows(value, sku))

    search = snapshots.get("search_positions") if isinstance(snapshots, dict) else None
    if isinstance(search, dict):
        for wrapped in search.values():
            value = wrapped.get("data") if isinstance(wrapped, dict) and "data" in wrapped else wrapped
            candidates.extend(_walk_query_rows(value))

    out: list[dict[str, Any]] = []
    seen: set[tuple[str, float | None, float | None]] = set()
    for raw in candidates:
        nm = str(raw.get("_nm") or "")
        if sku and nm and nm != sku:
            continue
        metric = _query_metrics(raw)
        if not metric["query"]:
            continue
        sig = (metric["query"].lower(), metric["frequency"], metric["position"])
        if sig in seen:
            continue
        seen.add(sig)
        out.append(metric)
    return out


def build_query_core(product: dict[str, Any], snapshots: dict[str, Any], top_n: int = 5) -> list[dict[str, Any]]:
    rows = _product_query_rows(product, snapshots)
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row["query"].strip().lower()
        agg = grouped.setdefault(key, {
            "query": row["query"], "orders": 0.0, "clicks": 0.0, "frequency": 0.0,
            "positions": [], "conversion_values": [], "trend_values": [],
        })
        agg["orders"] += float(row.get("orders") or 0.0)
        agg["clicks"] += float(row.get("clicks") or 0.0)
        agg["frequency"] = max(float(agg["frequency"]), float(row.get("frequency") or 0.0))
        if row.get("position") is not None:
            agg["positions"].append(float(row["position"]))
        if row.get("conversion_pct") is not None:
            agg["conversion_values"].append(float(row["conversion_pct"]))
        if row.get("frequency_trend_pct") is not None:
            agg["trend_values"].append(float(row["frequency_trend_pct"]))

    ranked: list[dict[str, Any]] = []
    for agg in grouped.values():
        clicks = float(agg["clicks"])
        orders = float(agg["orders"])
        cvr = orders / clicks * 100.0 if clicks > 0 else (mean(agg["conversion_values"]) if agg["conversion_values"] else None)
        ranked.append({
            "query": agg["query"],
            "orders": round(orders, 3),
            "clicks": round(clicks, 3),
            "frequency": round(float(agg["frequency"]), 3),
            "position": round(mean(agg["positions"]), 2) if agg["positions"] else None,
            "conversion_pct": round(cvr, 2) if cvr is not None else None,
            "frequency_trend_pct": round(mean(agg["trend_values"]), 2) if agg["trend_values"] else None,
        })

    ranked.sort(key=lambda x: (float(x["orders"]), float(x["frequency"]), -float(x["position"] or 9999)), reverse=True)
    core = ranked[:max(1, int(top_n))]
    total_orders = sum(float(x["orders"]) for x in core)
    total_freq = sum(float(x["frequency"]) for x in core)
    for row in core:
        if total_orders > 0:
            weight = float(row["orders"]) / total_orders
        elif total_freq > 0:
            weight = float(row["frequency"]) / total_freq
        else:
            weight = 1.0 / max(1, len(core))
        row["weight_pct"] = round(weight * 100.0, 2)
    return core


def _weighted(core: list[dict[str, Any]], key: str) -> float | None:
    vals = []
    for row in core:
        value = _n(row.get(key))
        if value is not None:
            vals.append((value, float(row.get("weight_pct") or 0.0) / 100.0))
    if not vals:
        return None
    total_w = sum(w for _, w in vals)
    return sum(v * w for v, w in vals) / total_w if total_w > 0 else mean(v for v, _ in vals)


def _current_conversion(product: dict[str, Any], core: list[dict[str, Any]]) -> float | None:
    core_cvr = _weighted(core, "conversion_pct")
    if core_cvr is not None:
        return core_cvr
    direct = _n(_first(product, "conversion_pct", "cvr_pct", "order_conversion_pct"))
    if direct is not None:
        return direct
    orders = _n(_first(product, "orders_7d", "orders"))
    clicks = _n(_first(product, "clicks_7d", "clicks", "open_card_count"))
    if orders is not None and clicks and clicks > 0:
        return orders / clicks * 100.0
    return None


def _season_factor(product: dict[str, Any], core: list[dict[str, Any]]) -> tuple[float, float | None, str]:
    trend = _weighted(core, "frequency_trend_pct")
    if trend is None:
        trend = _n(product.get("search_frequency_trend_pct"))
    if trend is None:
        return 1.0, None, "нет подтверждённого тренда поисковой частотности"
    bounded = _clamp(trend, -60.0, 120.0)
    factor = _clamp(1.0 + bounded / 100.0, 0.45, 2.20)
    return factor, trend, "взвешенный тренд ядра запросов"


def _seller_article(product: dict[str, Any]) -> str:
    value = _first(product, "seller_article", "vendor_code", "vendorCode", "article", "name", "sku")
    return str(value or product.get("sku") or "SKU")


@dataclass
class PlanningRow:
    sku: str
    seller_article: str
    query_core: list[dict[str, Any]]
    base_orders_day: float | None
    auto_orders_day: float | None
    auto_orders_7d: float | None
    auto_orders_30d: float | None
    revenue_30d_rub: float | None
    profit_30d_rub: float | None
    season_factor: float
    search_frequency_trend_pct: float | None
    conversion_pct: float | None
    plan_orders_day: float | None
    deviation_pct: float | None
    deviation_status: str
    forecast_confidence: str
    forecast_model: str
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_product_plan(
    product: dict[str, Any],
    snapshots: dict[str, Any],
    saved_plan: dict[str, Any] | None = None,
    top_n: int = 5,
) -> PlanningRow:
    sku = str(product.get("sku") or product.get("nm_id") or product.get("nmID") or "")
    core = build_query_core(product, snapshots, top_n=top_n)
    forecast = build_demand_forecast(product)
    base = _n(forecast.forecast_orders_1d)
    season_factor, freq_trend, season_source = _season_factor(product, core)
    cvr = _current_conversion(product, core)

    auto_daily = base
    if base is not None:
        # build_demand_forecast already includes a bounded share of search trend.
        # Apply only the residual seasonal signal to avoid double counting it.
        residual = _clamp((season_factor - 1.0) * 0.70, -0.35, 0.70)
        auto_daily = max(0.0, base * (1.0 + residual))

    price = _n(_first(product, "price_client_rub", "price_rub", "price"))
    unit_profit = _n(_first(product, "profit_rub", "unit_profit_rub"))
    auto30 = auto_daily * 30.0 if auto_daily is not None else None
    actual_daily = _n(_first(product, "orders_per_day", "fact_orders_per_day"))
    saved = saved_plan or {}
    plan_daily = _n(_first(saved, "planned_orders_day", "orders_day", "plan_orders_day"))
    if plan_daily is None:
        plan_daily = auto_daily

    deviation = None
    status = "no_fact"
    if plan_daily is not None and plan_daily > 0 and actual_daily is not None:
        deviation = (actual_daily / plan_daily - 1.0) * 100.0
        if deviation <= -15:
            status = "behind"
        elif deviation >= 15:
            status = "ahead"
        else:
            status = "on_plan"

    reasons = list(forecast.reasons or [])
    reasons.append(f"Сезонный коэффициент {season_factor:.2f}: {season_source}.")
    if core:
        reasons.append(f"Ядро: {len(core)} запросов, вес строится по заказам, затем по частотности.")
    else:
        reasons.append("Ядро запросов пока не подтверждено; прогноз держится на истории заказов и продуктовом тренде.")

    return PlanningRow(
        sku=sku,
        seller_article=_seller_article(product),
        query_core=core,
        base_orders_day=round(base, 3) if base is not None else None,
        auto_orders_day=round(auto_daily, 3) if auto_daily is not None else None,
        auto_orders_7d=round(auto_daily * 7.0, 2) if auto_daily is not None else None,
        auto_orders_30d=round(auto30, 2) if auto30 is not None else None,
        revenue_30d_rub=round(auto30 * price, 2) if auto30 is not None and price is not None else None,
        profit_30d_rub=round(auto30 * unit_profit, 2) if auto30 is not None and unit_profit is not None else None,
        season_factor=round(season_factor, 3),
        search_frequency_trend_pct=round(freq_trend, 2) if freq_trend is not None else None,
        conversion_pct=round(cvr, 2) if cvr is not None else None,
        plan_orders_day=round(plan_daily, 3) if plan_daily is not None else None,
        deviation_pct=round(deviation, 2) if deviation is not None else None,
        deviation_status=status,
        forecast_confidence=forecast.confidence,
        forecast_model=forecast.model_name,
        reasons=reasons,
    )


def build_planning_dashboard(
    portfolio: dict[str, Any],
    snapshots: dict[str, Any],
    saved_plans: dict[str, Any] | None = None,
    top_n: int = 5,
) -> dict[str, Any]:
    own = portfolio.get("own_27") if isinstance(portfolio, dict) else {}
    products = own.get("products") if isinstance(own, dict) else []
    saved = saved_plans or {}
    rows = []
    for product in products or []:
        if not isinstance(product, dict):
            continue
        sku = str(product.get("sku") or product.get("nm_id") or "")
        rows.append(build_product_plan(product, snapshots, saved.get(sku) if isinstance(saved, dict) else None, top_n).to_dict())
    rows.sort(key=lambda x: (x["deviation_status"] != "behind", -(abs(x["deviation_pct"]) if x["deviation_pct"] is not None else 0), x["seller_article"]))
    return {
        "rows": rows,
        "count": len(rows),
        "behind_count": sum(1 for x in rows if x["deviation_status"] == "behind"),
        "ahead_count": sum(1 for x in rows if x["deviation_status"] == "ahead"),
        "on_plan_count": sum(1 for x in rows if x["deviation_status"] == "on_plan"),
        "method": "история заказов + ядро запросов + частотность + конверсия + сезонный коэффициент",
        "query_core_top_n": top_n,
    }


def calculate_scenario(plan: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    base = _n(plan.get("auto_orders_day")) or _n(plan.get("plan_orders_day")) or 0.0
    freq_change = _clamp(_n(overrides.get("frequency_change_pct")) or 0.0, -80.0, 200.0)
    conversion_change = _clamp(_n(overrides.get("conversion_change_pct")) or 0.0, -80.0, 200.0)
    position_change = _clamp(_n(overrides.get("position_change_pct")) or 0.0, -80.0, 200.0)
    ad_clicks = max(0.0, _n(overrides.get("additional_ad_clicks_day")) or 0.0)
    current_cvr = _n(plan.get("conversion_pct")) or 0.0
    scenario_cvr = max(0.0, current_cvr * (1.0 + conversion_change / 100.0))

    organic_factor = (1.0 + freq_change / 100.0) * (1.0 + conversion_change / 100.0) * (1.0 + position_change / 100.0)
    organic_daily = max(0.0, base * organic_factor)
    paid_increment = ad_clicks * scenario_cvr / 100.0
    daily = organic_daily + paid_increment

    price = _n(overrides.get("price_rub"))
    if price is None:
        price = _n(plan.get("price_rub"))
    unit_profit = _n(overrides.get("unit_profit_rub"))
    if unit_profit is None:
        unit_profit = _n(plan.get("unit_profit_rub"))
    buyout = _clamp(_n(overrides.get("buyout_pct")) or 100.0, 0.0, 100.0)
    sold_daily = daily * buyout / 100.0

    return {
        "orders_day": round(daily, 3),
        "orders_7d": round(daily * 7.0, 2),
        "orders_30d": round(daily * 30.0, 2),
        "sold_units_30d": round(sold_daily * 30.0, 2),
        "revenue_30d_rub": round(sold_daily * 30.0 * price, 2) if price is not None else None,
        "profit_30d_rub": round(sold_daily * 30.0 * unit_profit, 2) if unit_profit is not None else None,
        "scenario_conversion_pct": round(scenario_cvr, 2),
        "additional_paid_orders_day": round(paid_increment, 3),
        "assumptions": {
            "frequency_change_pct": freq_change,
            "conversion_change_pct": conversion_change,
            "position_change_pct": position_change,
            "additional_ad_clicks_day": ad_clicks,
            "buyout_pct": buyout,
        },
    }
