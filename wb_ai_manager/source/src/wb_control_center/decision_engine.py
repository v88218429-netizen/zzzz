from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any

from .models import DecisionCard
from .advertising_controller import AdvertisingController
from .analytics_kernel import demand_quality, economics_quality
from .demand_forecast import build_demand_forecast
from .metrics import extract_ad_nm_metrics
from .planning_engine import build_product_plan


def _n(v: Any) -> float | None:
    try:
        if v is None: return None
        return float(v)
    except Exception:
        return None


def _ev(source: str, metric: str, value: Any, note: str = "") -> dict[str, Any]:
    return {"source": source, "metric": metric, "value": value, "note": note}


def _snap(snapshots: dict[str, Any], source: str, key: str) -> Any:
    """Return latest snapshot payload with wrapper layers removed conservatively."""
    try:
        item = snapshots[source][key]
    except Exception:
        return None
    if isinstance(item, dict) and "data" in item and "created_at" in item:
        return item.get("data")
    return item


def _rows(value: Any, preferred: tuple[str, ...] = ("data", "items", "orders", "claims", "events", "campaigns")) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if not isinstance(value, dict):
        return []
    for key in preferred:
        candidate=value.get(key)
        if isinstance(candidate,list):
            return [x for x in candidate if isinstance(x,dict)]
    return []

def _pick_num(row: dict[str, Any], *keys: str) -> float | None:
    for k in keys:
        if k in row:
            n=_n(row.get(k))
            if n is not None:
                return n
    return None


def _direct_num(row: dict[str, Any], *keys: str) -> float | None:
    lowered = {str(k).lower(): v for k, v in row.items()}
    for key in keys:
        if key.lower() in lowered:
            value = _n(lowered[key.lower()])
            if value is not None:
                return value
    return None


def _pack_qty(value: Any) -> int:
    text = str(value or "").lower().replace("×", "x").replace("х", "x")
    for pattern in (r"(\d+)\s*шт\b", r"\bx\s*(\d+)\b"):
        m = __import__("re").search(pattern, text)
        if m:
            try:
                return max(1, int(m.group(1)))
            except Exception:
                pass
    return 1


def _funnel_fact_index(obj: Any) -> dict[str, dict[str, float]]:
    """Pick the most complete current-period funnel row for each exact nmID."""
    out: dict[str, dict[str, float]] = {}

    def walk(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if not isinstance(value, dict):
            return
        nm = _direct_num(value, "nmId", "nmID", "nm_id", "nm")
        if nm is not None:
            current = value.get("currentPeriod") or value.get("current_period") or value.get("current")
            row = current if isinstance(current, dict) else value
            order_sum = _direct_num(row, "orderSum", "order_sum")
            buyout_sum = _direct_num(row, "buyoutSum", "buyout_sum")
            order_count = _direct_num(row, "orderCount", "order_count")
            buyout_count = _direct_num(row, "buyoutCount", "buyout_count")
            if any(v is not None for v in (order_sum, buyout_sum, order_count, buyout_count)):
                key = str(int(nm))
                candidate = {
                    "order_sum_rub": float(order_sum or 0.0),
                    "buyout_sum_rub": float(buyout_sum or 0.0),
                    "order_count": float(order_count or 0.0),
                    "buyout_count": float(buyout_count or 0.0),
                }
                score = sum(1 for v in (order_sum, buyout_sum, order_count, buyout_count) if v is not None)
                old = out.get(key)
                old_score = int(old.get("_score", 0)) if old else -1
                if score > old_score or (score == old_score and candidate["buyout_sum_rub"] > float((old or {}).get("buyout_sum_rub") or 0)):
                    candidate["_score"] = score
                    out[key] = candidate
        for nested in value.values():
            if isinstance(nested, (dict, list)):
                walk(nested)

    walk(obj)
    for row in out.values():
        row.pop("_score", None)
    return out


def _live_economics_fact_index(snapshots: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Build period-aligned factual DRR from exact SKU ad spend and funnel revenue."""
    stats = _snap(snapshots, "advertising_monitor", "stats_7d")
    funnel = _snap(snapshots, "funnel", "funnel_7d")
    if stats is None or funnel is None:
        return {}
    ad_buckets = extract_ad_nm_metrics(stats)
    ads: dict[str, dict[str, Any]] = {}
    for (_, nm), row in ad_buckets.items():
        key = str(int(nm))
        agg = ads.setdefault(key, {"spend_rub": 0.0, "days": set()})
        agg["spend_rub"] += float(row.get("sum") or 0.0)
        for day in row.get("days") or []:
            dt = str(day.get("date") or "")[:10]
            if dt:
                agg["days"].add(dt)
    funnels = _funnel_fact_index(funnel)
    out: dict[str, dict[str, Any]] = {}
    for sku, ad in ads.items():
        fr = funnels.get(sku)
        if not fr:
            continue
        sales = float(fr.get("buyout_sum_rub") or 0.0)
        orders = float(fr.get("order_sum_rub") or 0.0)
        spend = float(ad.get("spend_rub") or 0.0)
        days = sorted(ad.get("days") or [])
        out[sku] = {
            "ad_spend_rub": spend,
            "sales_revenue_rub": sales if sales > 0 else None,
            "sales_qty": float(fr.get("buyout_count") or 0.0) if float(fr.get("buyout_count") or 0.0) > 0 else None,
            "orders_revenue_rub": orders if orders > 0 else None,
            "fact_drr_sales_pct": (spend / sales * 100.0) if sales > 0 else None,
            "fact_drr_orders_pct": (spend / orders * 100.0) if orders > 0 else None,
            "period_from": days[0] if days else None,
            "period_to": days[-1] if days else None,
            "days": len(days),
            "source": "WB Promotion exact nmID + WB funnel",
            "quality": "LIVE_EXACT_SKU",
        }
    return out


def _product_economics_reconciliation(product: dict[str, Any], live_fact: dict[str, Any] | None = None) -> dict[str, Any]:
    plan_drr = _n(product.get("plan_drr_pct"))
    plan_profit = _n(product.get("profit_rub"))
    plan_margin = _n(product.get("margin_pct"))
    price = _n(product.get("price_rub"))
    client_price = _n(product.get("price_client_rub"))

    fact = live_fact or {}
    if _n(fact.get("fact_drr_sales_pct")) is None:
        fact = {
            "ad_spend_rub": _n(product.get("fact_ad_spend_rub")),
            "sales_revenue_rub": _n(product.get("fact_sales_revenue_rub")),
            "sales_qty": _n(product.get("fact_sales_qty")),
            "fact_drr_sales_pct": _n(product.get("fact_drr_sales_pct")),
            "period_from": product.get("fact_economics_period_from"),
            "period_to": product.get("fact_economics_period_to"),
            "days": _n(product.get("fact_economics_days")),
            "source": product.get("fact_economics_source"),
            "quality": product.get("fact_economics_quality"),
        }

    actual_drr = _n(fact.get("fact_drr_sales_pct"))
    ad_spend = _n(fact.get("ad_spend_rub"))
    sales_revenue = _n(fact.get("sales_revenue_rub"))
    sales_qty = _n(fact.get("sales_qty"))
    scenario_profit = None
    scenario_margin = None
    break_even_drr = None
    actual_ad_cost_per_sale = None
    actual_revenue_per_sale = None
    plan_ad_cost_per_unit = None
    drr_if_plan_revenue = None
    ad_efficiency_gap_pp = None
    revenue_gap_pp = None
    expected_ad_spend_at_plan = None
    excess_ad_spend = None

    if price and price > 0 and plan_profit is not None and plan_drr is not None:
        break_even_drr = plan_drr + plan_profit / price * 100.0
        plan_ad_cost_per_unit = price * plan_drr / 100.0
        if actual_drr is not None:
            scenario_profit = plan_profit - (actual_drr - plan_drr) / 100.0 * price
            scenario_margin = scenario_profit / price * 100.0

    if sales_qty and sales_qty > 0:
        if ad_spend is not None:
            actual_ad_cost_per_sale = ad_spend / sales_qty
        if sales_revenue is not None:
            actual_revenue_per_sale = sales_revenue / sales_qty
    if sales_revenue is not None and plan_drr is not None:
        expected_ad_spend_at_plan = sales_revenue * plan_drr / 100.0
        if ad_spend is not None:
            excess_ad_spend = ad_spend - expected_ad_spend_at_plan
    if actual_ad_cost_per_sale is not None and client_price and client_price > 0 and plan_drr is not None and actual_drr is not None:
        drr_if_plan_revenue = actual_ad_cost_per_sale / client_price * 100.0
        ad_efficiency_gap_pp = max(0.0, drr_if_plan_revenue - plan_drr)
        revenue_gap_pp = max(0.0, actual_drr - drr_if_plan_revenue)

    root_causes: list[dict[str, Any]] = []
    if ad_efficiency_gap_pp is not None and ad_efficiency_gap_pp >= 1.0:
        root_causes.append({
            "cause": "дорогая реклама на одну фактическую продажу",
            "impact_pp": ad_efficiency_gap_pp,
            "detail": f"даже при плановой выручке на продажу ДРР был бы около {drr_if_plan_revenue:.1f}%",
        })
    if revenue_gap_pp is not None and revenue_gap_pp >= 1.0:
        root_causes.append({
            "cause": "фактическая выручка на продажу ниже плановой",
            "impact_pp": revenue_gap_pp,
            "detail": f"факт ≈{actual_revenue_per_sale:.0f} ₽/продажу против плана после СПП ≈{client_price:.0f} ₽" if actual_revenue_per_sale is not None else "",
        })
    root_causes.sort(key=lambda x: float(x.get("impact_pp") or 0), reverse=True)

    return {
        "plan_drr_pct": plan_drr,
        "plan_profit_rub": plan_profit,
        "plan_margin_pct": plan_margin,
        "price_after_discount_rub": price,
        "price_after_spp_rub": client_price,
        "fact_drr_sales_pct": actual_drr,
        "fact_ad_spend_rub": ad_spend,
        "fact_sales_revenue_rub": sales_revenue,
        "fact_sales_qty": sales_qty,
        "fact_period_from": fact.get("period_from"),
        "fact_period_to": fact.get("period_to"),
        "fact_days": _n(fact.get("days")),
        "fact_source": fact.get("source"),
        "fact_quality": fact.get("quality"),
        "scenario_profit_rub": scenario_profit,
        "scenario_margin_pct": scenario_margin,
        "break_even_drr_pct": break_even_drr,
        "actual_ad_cost_per_sale_rub": actual_ad_cost_per_sale,
        "actual_revenue_per_sale_rub": actual_revenue_per_sale,
        "plan_ad_cost_per_unit_rub": plan_ad_cost_per_unit,
        "drr_if_plan_revenue_pct": drr_if_plan_revenue,
        "ad_efficiency_gap_pp": ad_efficiency_gap_pp,
        "revenue_gap_pp": revenue_gap_pp,
        "expected_ad_spend_at_plan_rub": expected_ad_spend_at_plan,
        "excess_ad_spend_vs_plan_rub": excess_ad_spend,
        "root_causes": root_causes,
    }


@dataclass
class DecisionEngine:
    """Cross-contour rule engine.

    The important rule is architectural: detector agents may emit facts, but the final
    recommendation is produced only here after economics, demand, stock, advertising,
    search and data-quality evidence are considered together.
    """
    policy: Any

    def build(self, portfolio: dict[str, Any], snapshots: dict[str, Any] | None = None) -> list[DecisionCard]:
        snapshots = snapshots or {}
        out: list[DecisionCard] = []
        current_portfolio = bool(portfolio.get("current_data", portfolio.get("data_origin") != "seeded_real_facts"))
        safe_portfolio = portfolio if current_portfolio else {
            "data_origin": portfolio.get("data_origin"),
            "current_data": False,
            "source_health": portfolio.get("source_health") or [],
            "stores": [],
            "own_27": {},
            "portfolio": {},
        }
        out += self._source_quality(portfolio)
        if not current_portfolio:
            out.append(DecisionCard(
                decision_key="data:portfolio_historical_only",
                scope="system",
                entity_id="portfolio",
                title="Портфельный срез устарел — текущие решения по нему заблокированы",
                diagnosis=str(portfolio.get("stale_reason") or "Доступен только исторический срез портфеля."),
                priority="high",
                confidence="high",
                recommended_actions=[{
                    "step": 1,
                    "action": "Не использовать архивный срез для текущих цен, рекламы и поставок. Работать только по свежим WB/FBS/finance источникам до восстановления живого портфеля.",
                    "mode": "automatic_policy",
                }],
                evidence=[_ev("portfolio", "period", portfolio.get("period")), _ev("portfolio", "origin", portfolio.get("data_origin"))],
                blockers=["Нет свежего live-среза портфеля по выбранному периоду."],
                follow_up="Снять блокировку автоматически после появления свежего live-среза.",
            ))
        if current_portfolio:
            out += self._operating_findings(snapshots)
            out += self._store_decisions(portfolio)
            out += self._product_decisions(portfolio, snapshots)
            out += self._inventory_decisions(portfolio)
        out += self._planning_decisions(safe_portfolio, snapshots)
        out += self._advertising_control_decisions(safe_portfolio, snapshots)
        out += self._card_content_decisions(safe_portfolio, snapshots)
        out += self._operational_decisions(safe_portfolio, snapshots)
        out += self._event_decisions(safe_portfolio, snapshots)
        out += self._search_market_decisions(safe_portfolio, snapshots)
        # Stable de-dup by key, retaining the highest priority version.
        rank={"low":1,"medium":2,"high":3,"critical":4}
        best: dict[str, DecisionCard] = {}
        for d in out:
            if d.decision_key not in best or rank[d.priority] > rank[best[d.decision_key].priority]:
                best[d.decision_key]=d
        return sorted(best.values(), key=lambda d:(-rank[d.priority], d.title))


    def _planning_decisions(self, portfolio: dict[str, Any], snapshots: dict[str, Any]) -> list[DecisionCard]:
        """Turn saved sales plans into fact-vs-plan operating decisions."""
        raw = _snap(snapshots, "planning", "plans")
        plans = raw if isinstance(raw, dict) else {}
        if not plans:
            return []
        own = portfolio.get("own_27") if isinstance(portfolio, dict) else {}
        products = own.get("products") if isinstance(own, dict) else []
        out: list[DecisionCard] = []
        for product in products or []:
            if not isinstance(product, dict):
                continue
            sku = str(product.get("sku") or product.get("nm_id") or "")
            saved = plans.get(sku)
            if not isinstance(saved, dict):
                continue
            row = build_product_plan(product, snapshots, saved).to_dict()
            deviation = _n(row.get("deviation_pct"))
            if deviation is None or abs(deviation) < 15:
                continue
            behind = deviation < 0
            article = str(row.get("seller_article") or product.get("name") or sku)
            plan_daily = _n(row.get("plan_orders_day"))
            fact_daily = _n(product.get("orders_per_day"))
            auto_daily = _n(row.get("auto_orders_day"))
            freq = _n(row.get("search_frequency_trend_pct"))
            cvr = _n(row.get("conversion_pct"))
            core = row.get("query_core") if isinstance(row.get("query_core"), list) else []
            core_text = ", ".join(str(x.get("query") or "") for x in core[:5] if isinstance(x, dict) and x.get("query"))
            diagnosis = (
                f"План {plan_daily:.2f} заказа/день, факт {fact_daily:.2f}; отклонение {deviation:+.1f}%."
                if plan_daily is not None and fact_daily is not None
                else f"Отклонение от сохранённого плана {deviation:+.1f}%."
            )
            diagnosis += (
                " Агент должен разложить отклонение по частотности ядра, конверсии, позиции, рекламе и наличию, а не менять один фактор вслепую."
                if behind
                else " Рост выше плана нужно проверить на устойчивость и заранее обеспечить рекламу/остаток без потери экономики."
            )
            actions = [
                {
                    "step": 1,
                    "action": (
                        "Разложить недобор по ядру запросов: частотность → позиция → CTR/CVR → рекламный вклад → наличие; определить главный драйвер."
                        if behind
                        else "Проверить, какой драйвер дал превышение плана: сезонная частотность, позиция, конверсия или реклама; подтвердить, что рост не разовый."
                    ),
                    "mode": "plan_variance_root_cause",
                },
                {
                    "step": 2,
                    "action": (
                        f"Пересчитать потребность в трафике и остатке от текущего автоматического прогноза {auto_daily:.2f} заказа/день."
                        if auto_daily is not None
                        else "Пересчитать трафик и остаток от обновлённого прогноза спроса."
                    ),
                    "mode": "cross_contour_replan",
                },
                {
                    "step": 3,
                    "action": "После следующего дневного окна повторно сравнить факт с планом; менять план только после подтверждённого сдвига спроса.",
                    "mode": "plan_control",
                },
            ]
            out.append(DecisionCard(
                decision_key=f"plan:{sku}:variance",
                scope="sku",
                entity_id=sku,
                title=f"{article}: {'ниже' if behind else 'выше'} плана на {abs(deviation):.1f}%",
                diagnosis=diagnosis,
                priority="high" if abs(deviation) >= 25 else "medium",
                confidence="high" if row.get("forecast_confidence") == "high" else "medium",
                recommended_actions=actions,
                evidence=[
                    _ev("Планирование", "План заказов/день", plan_daily),
                    _ev("Факт", "Факт заказов/день", fact_daily),
                    _ev("Планирование", "Отклонение, %", deviation),
                    _ev("Автопрогноз", "Прогноз заказов/день", auto_daily),
                    _ev("Поиск", "Тренд частотности ядра, %", freq),
                    _ev("Воронка", "Конверсия ядра, %", cvr),
                    _ev("Поиск", "Ядро запросов", core_text or "нет подтверждённого ядра"),
                ],
                follow_up="Автоматически пересчитать после следующего обновления спроса, поиска, рекламы и остатков.",
                analysis={
                    "metric_type": "plan_vs_fact",
                    "formula": "отклонение = факт заказов/день ÷ план заказов/день − 1",
                    "demand_model": row.get("forecast_model"),
                    "source": "история заказов + ядро запросов + поиск + сохранённый план",
                },
            ))
        return out


    def _operating_findings(self, snapshots: dict[str, Any]) -> list[DecisionCard]:
        out: list[DecisionCard] = []
        rows = snapshots.get("_operating_findings") or []
        if not isinstance(rows, list):
            return out
        for r in rows:
            if not isinstance(r, dict):
                continue
            level = str(r.get("level") or "medium")
            if level not in {"low","medium","high","critical"}:
                level="medium"
            out.append(DecisionCard(
                decision_key=str(r.get("key") or "operating:finding"),
                scope="operating_model", entity_id=str(r.get("key") or "model"),
                title=str(r.get("title") or "Операционная закономерность"),
                diagnosis=str(r.get("conclusion") or ""), priority=level,
                confidence=str(r.get("confidence") or "high") if str(r.get("confidence") or "high") in {"low","medium","high"} else "high",
                recommended_actions=[{"step":1,"action":str(r.get("action") or "Разобрать причину по связанным данным"),"mode":"operating_model"}],
                evidence=[_ev("trusted_tables", str(x.get("metric") or "metric"), x.get("value")) for x in (r.get("evidence") or []) if isinstance(x,dict)],
                follow_up="Пересчитать после следующего обновления trusted-таблиц."
            ))
        return out

    def _source_quality(self, p: dict[str, Any]) -> list[DecisionCard]:
        out: list[DecisionCard] = []
        bad = [x for x in p.get("source_health", []) if x.get("status") in {"broken", "stale"}]
        if bad:
            critical = [x for x in bad if x.get("status") == "broken"]
            out.append(DecisionCard(
                decision_key="data:source_quality", scope="system", entity_id="sources",
                title="Слабые источники не участвуют в денежных решениях",
                diagnosis=(
                    "Часть источников устарела или возвращает некорректный срез. "
                    "Плановая экономика берётся только из PRIMARY «Юнит-экономика вб / WB FBS новая», "
                    "факт — из WB/Sellmonitor/закрытой реализации. Старый 27/Юнитка — только диагностический fallback."
                ),
                priority="high" if critical else "medium", confidence="high",
                recommended_actions=[
                    {"step": 1, "action": "Автоматически исключать broken/stale источники из денежного вывода.", "mode": "automatic_policy"},
                    {"step": 2, "action": "Не заменять отсутствующий факт нулём и не подменять его плановым значением.", "mode": "automatic_policy"},
                    {"step": 3, "action": "Возвращать источник в расчёт только после проверки свежести и согласованности.", "mode": "maintenance"},
                ],
                evidence=[_ev(x.get("id", "source"), "Статус источника", x.get("status"), f"freshness={x.get('freshness')}; trust={x.get('trust')}") for x in bad[:8]],
                follow_up="Перепроверять свежесть и семантику источников при каждом цикле импорта.",
            ))

        own = p.get("own_27") or {}
        axis = own.get("primary_unit_economics") or {}
        total_products = len([x for x in own.get("products", []) if isinstance(x, dict) and str(x.get("sku") or "").isdigit()])
        mapped = int(axis.get("mapped_products") or 0)
        unmatched = int(axis.get("unmatched_products") or max(0, total_products - mapped))
        if axis and total_products and unmatched:
            coverage = mapped / total_products * 100.0
            priority = "high" if coverage < 50 else "medium"
            out.append(DecisionCard(
                decision_key="data:primary_unit_mapping", scope="system", entity_id="primary_unit_economics",
                title="PRIMARY-юнитка привязана не ко всем WB SKU",
                diagnosis=(
                    f"Безопасно сопоставлено {mapped} из {total_products} WB SKU ({coverage:.1f}%). "
                    f"Ещё {unmatched} SKU не получают денежные решения, пока соответствие основной юнитке неоднозначно или отсутствует. "
                    "Это одна задача идентичности данных, а не отдельные тревоги по каждому товару."
                ),
                priority=priority, confidence="high",
                recommended_actions=[
                    {"step": 1, "action": "Достраивать таблицу идентичности seller article/nmID → строка PRIMARY-юнитки только детерминированными правилами.", "mode": "identity_reconciliation"},
                    {"step": 2, "action": "Не использовать fuzzy-совпадение и не откатываться на 27/Юнитка для непривязанных SKU.", "mode": "data_guard"},
                    {"step": 3, "action": "В первую очередь привязывать SKU с фактической рекламой, высокой выручкой или критичным запасом — там ценность корректной экономики выше.", "mode": "information_gain"},
                ],
                evidence=[
                    _ev("PRIMARY-юнитка", "Плановых строк", axis.get("plan_records")),
                    _ev("Идентичность SKU", "Безопасно привязано", mapped),
                    _ev("Идентичность SKU", "Не привязано", unmatched),
                    _ev("Идентичность SKU", "Покрытие, %", round(coverage, 1)),
                ],
                follow_up="Пересчитывать покрытие после каждого обновления таблицы идентичности.",
            ))
        return out

    def _store_decisions(self, p: dict[str, Any]) -> list[DecisionCard]:
        out: list[DecisionCard] = []
        adcfg = self.policy.thresholds.get("advertising", {})
        warn = float(adcfg.get("warn_drr_pct", 12))

        for s in p.get("stores", []) or []:
            groups = [g for g in (s.get("groups") or []) if isinstance(g, dict)]
            if not groups:
                continue
            drr = _n(s.get("drr_pct"))
            margin = _n(s.get("margin_pct"))
            profit = _n(s.get("profit_rub"))
            buyouts = _n(s.get("buyouts_qty"))
            orders = _n(s.get("orders_qty"))
            buyout_rate = (buyouts / orders * 100.0) if orders and buyouts is not None else None

            hard_stop: list[dict[str, Any]] = []
            hold: list[dict[str, Any]] = []
            winners: list[dict[str, Any]] = []
            for g in groups:
                gp = _n(g.get("profit_rub")) or 0.0
                gm = _n(g.get("margin_pct"))
                gb = _n(g.get("buyout_pct"))
                go = _n(g.get("orders_rub")) or 0.0
                if gp < 0:
                    if gp <= -500 or (gm is not None and gm <= -10) or (gb is not None and gb < 20 and go >= 5000):
                        hard_stop.append(g)
                    else:
                        hold.append(g)
                elif gp > 0 and (gm is None or gm >= 10) and (gb is None or gb >= 20):
                    winners.append(g)

            if not hard_stop and not hold and not (drr is not None and drr >= warn):
                continue

            hard_stop.sort(key=lambda x: _n(x.get("profit_rub")) or 0)
            hold.sort(key=lambda x: _n(x.get("profit_rub")) or 0)
            winners.sort(key=lambda x: _n(x.get("profit_rub")) or 0, reverse=True)

            hard_names = ", ".join(str(x.get("name")) for x in hard_stop[:6]) or "нет"
            hold_names = ", ".join(str(x.get("name")) for x in hold[:6]) or "нет"
            winner_names = ", ".join(str(x.get("name")) for x in winners[:6]) or "нет"
            historical_loss = sum((_n(x.get("profit_rub")) or 0.0) for x in hard_stop + hold)

            diagnosis_parts = []
            if drr is not None:
                diagnosis_parts.append(f"ДРР магазина {drr:.1f}%")
            if margin is not None:
                diagnosis_parts.append(f"маржа {margin:.1f}%")
            if buyout_rate is not None:
                diagnosis_parts.append(f"выкуп по срезу ≈{buyout_rate:.1f}%")
            diagnosis_parts.append(f"красная зона: {hard_names}")
            if hold:
                diagnosis_parts.append(f"удержание без расширения: {hold_names}")
            if winners:
                diagnosis_parts.append(f"положительные группы: {winner_names}")
            diagnosis_parts.append(
                f"суммарный реализованный минус отрицательных групп ≈{historical_loss:.0f} ₽"
            )

            actions: list[dict[str, Any]] = []
            if hard_stop:
                actions.append({
                    "step": 1,
                    "action": (
                        "Не добавлять новый платный трафик в группы: "
                        + hard_names
                        + ". Причина уже определена по зрелому финансовому срезу: отрицательная реализованная прибыль; "
                          "для групп с низким выкупом реклама усиливает не тот участок воронки."
                    ),
                    "mode": "portfolio_ad_guard",
                })
            if hold:
                actions.append({
                    "step": len(actions) + 1,
                    "action": (
                        "Оставить без расширения рекламной нагрузки группы: "
                        + hold_names
                        + ". Убыток мал/нестабилен, поэтому дополнительный бюджет не давать до свежего полного окна."
                    ),
                    "mode": "portfolio_hold",
                })
            if winners:
                actions.append({
                    "step": len(actions) + 1,
                    "action": (
                        "Новый рекламный бюджет рассматривать в первую очередь для: "
                        + winner_names
                        + ". Это не автоматическое повышение ставки: допуск действует только если свежий SKU-факт "
                          "по ДРР и PRIMARY-юнитке остаётся положительным."
                    ),
                    "mode": "portfolio_scale_pool",
                })
            actions.append({
                "step": len(actions) + 1,
                "action": "Следующий прогон сам пересоберёт эти три корзины по новому зрелому факту; ручное «разделить группы» больше не требуется.",
                "mode": "automatic_follow_up",
            })

            priority = "critical" if hard_stop and (margin is not None and margin <= 0) else ("high" if hard_stop else "medium")
            evidence = [
                _ev(s.get("source", "weekly"), "ДРР магазина, %", drr),
                _ev(s.get("source", "weekly"), "Маржа магазина, %", margin),
                _ev(s.get("source", "weekly"), "Прибыль магазина, ₽", profit),
                _ev(s.get("source", "weekly"), "Выкуп магазина, %", buyout_rate),
            ]
            for label, rows in (("Красная зона", hard_stop), ("Удержание", hold), ("Положительная группа", winners)):
                for g in rows[:8]:
                    evidence.append(_ev(
                        s.get("source", "weekly"),
                        f"{label}: {g.get('name')}",
                        _n(g.get("profit_rub")),
                        f"прибыль, ₽; маржа={g.get('margin_pct')}; выкуп={g.get('buyout_pct')}",
                    ))

            out.append(DecisionCard(
                decision_key=f"store:{s.get('id')}:portfolio_ads",
                scope="store",
                entity_id=str(s.get("id")),
                title=f"{s.get('name')}: рекламный портфель уже разделён по экономике",
                diagnosis=". ".join(diagnosis_parts) + ".",
                priority=priority,
                confidence="high",
                recommended_actions=actions,
                evidence=evidence,
                follow_up="Автоматически пересобрать корзины после следующего зрелого финансового среза и свежего рекламного окна.",
                analysis={
                    "metric_type": "store_portfolio_allocation",
                    "time_semantics": "mature_realised_group_economics",
                    "hard_stop_groups": [x.get("name") for x in hard_stop],
                    "hold_groups": [x.get("name") for x in hold],
                    "scale_pool_groups": [x.get("name") for x in winners],
                    "warning": "Зрелый групповой финансовый срез не заменяет свежий campaign×SKU факт; он задаёт guardrail, а не прямую ставку.",
                },
            ))
        return out

    def _product_decisions(self, p: dict[str, Any], snapshots: dict[str, Any] | None = None) -> list[DecisionCard]:
        out: list[DecisionCard] = []
        own = p.get("own_27") or {}
        live_facts = _live_economics_fact_index(snapshots or {})

        for x in own.get("products", [])[:500]:
            sku = str(x.get("sku") or "")
            if not sku:
                continue

            # The old 27/Юнитка is retained for diagnostics only.  If a SKU is not
            # confidently mapped to the primary model, do not manufacture a money task.
            if str(x.get("unit_economics_role") or "") != "primary_plan":
                continue

            eq = economics_quality(x)
            if not eq.ready:
                continue

            rec = _product_economics_reconciliation(x, live_facts.get(sku))
            plan_profit = _n(rec.get("plan_profit_rub"))
            plan_margin = _n(rec.get("plan_margin_pct"))
            plan_drr = _n(rec.get("plan_drr_pct"))
            actual_drr = _n(rec.get("fact_drr_sales_pct"))
            scenario_profit = _n(rec.get("scenario_profit_rub"))
            scenario_margin = _n(rec.get("scenario_margin_pct"))
            break_even = _n(rec.get("break_even_drr_pct"))
            price = _n(rec.get("price_after_discount_rub"))

            evidence = [
                _ev("Основная юнитка", "Цена после скидки, ₽", price, "плановая модель"),
                _ev("Основная юнитка", "Себестоимость, ₽", _n(x.get("cost_rub")), "до передачи на маркетплейс"),
                _ev("Основная юнитка", "Комиссия WB, %", _n(x.get("commission_pct"))),
                _ev("Основная юнитка", "Логистика с учётом выкупа, ₽", _n(x.get("logistics_total_rub"))),
                _ev("Основная юнитка", "Платная приёмка, ₽", _n(x.get("acceptance_rub"))),
                _ev("Основная юнитка", "Налоги, ₽", _n(x.get("tax_total_rub"))),
                _ev("Основная юнитка", "Плановый ДРР, %", plan_drr, "это допущение модели, не факт WB"),
                _ev("Основная юнитка", "Плановая прибыль, ₽/шт", plan_profit),
                _ev("Основная юнитка", "Плановая маржа, %", plan_margin),
            ]
            if actual_drr is not None:
                period = " → ".join(str(v) for v in (rec.get("fact_period_from"), rec.get("fact_period_to")) if v)
                evidence += [
                    _ev(str(rec.get("fact_source") or "Факт WB"), "Фактический расход рекламы, ₽", _n(rec.get("fact_ad_spend_rub")), period),
                    _ev(str(rec.get("fact_source") or "Факт WB"), "Фактическая выручка продаж, ₽", _n(rec.get("fact_sales_revenue_rub")), period),
                    _ev(str(rec.get("fact_source") or "Факт WB"), "Фактические продажи, шт", _n(rec.get("fact_sales_qty")), period),
                    _ev(str(rec.get("fact_source") or "Факт WB"), "Фактический ДРР продаж, %", actual_drr, period),
                    _ev("Авторазложение", "Реклама на одну продажу, ₽", _n(rec.get("actual_ad_cost_per_sale_rub"))),
                    _ev("Авторазложение", "Выручка на одну продажу, ₽", _n(rec.get("actual_revenue_per_sale_rub"))),
                    _ev("Авторазложение", "Лишний рекламный расход против плана, ₽", _n(rec.get("excess_ad_spend_vs_plan_rub")), period),
                    _ev("Сверка план ↔ факт", "Модельная прибыль при фактическом ДРР, ₽/шт", scenario_profit, "не реализованная прибыль; остальные параметры юнитки зафиксированы"),
                    _ev("Сверка план ↔ факт", "Модельная маржа при фактическом ДРР, %", scenario_margin),
                ]
                for cause in rec.get("root_causes") or []:
                    evidence.append(_ev("Авторазложение причины", str(cause.get("cause") or "Причина"), round(float(cause.get("impact_pp") or 0), 2), str(cause.get("detail") or "")))

            basis = {
                "time_semantics": "plan_vs_fact",
                "metric_type": "economics_reconciliation",
                "source": "Юнит-экономика вб / WB FBS новая + независимый факт WB/Sellmonitor",
                "source_updated_at": (own.get("primary_unit_economics") or {}).get("snapshot_date"),
                "period": f"{rec.get('fact_period_from') or 'нет'}–{rec.get('fact_period_to') or 'нет'}",
                "formula": "модельная прибыль при фактическом ДРР = плановая прибыль − (факт ДРР − план ДРР) × цена после скидки",
                "quality_score": eq.score,
                "quality_level": eq.level,
                "fact_quality": rec.get("fact_quality"),
                "warning": "Модельная прибыль при фактическом ДРР не равна закрытой реализованной прибыли WB.",
            }

            # A primary plan that is already negative is a planning problem.  Without
            # independent fact it is never presented as a proven realised loss.
            if plan_profit is not None and plan_profit < 0 and actual_drr is None:
                out.append(DecisionCard(
                    decision_key=f"sku:{sku}:negative_plan_model", scope="sku", entity_id=sku,
                    title=f"{x.get('name')}: основная юнитка показывает отрицательную плановую экономику",
                    diagnosis=(
                        f"По основной юнитке прибыль {plan_profit:.2f} ₽/шт"
                        + (f", маржа {plan_margin:.1f}%" if plan_margin is not None else "")
                        + ". Фактический ДРР за сопоставимый период пока не подтверждён, поэтому это проблема плановой модели, а не доказанный убыток WB."
                    ),
                    priority="high", confidence="medium",
                    recommended_actions=[
                        {"step": 1, "action": "Не масштабировать рекламу до появления сопоставимого факта по этому nmID.", "mode": "data_guard"},
                        {"step": 2, "action": "Проверить цену, себестоимость, комиссию, логистику, налоги и плановый ДРР в основной юнитке.", "mode": "unit_reconciliation"},
                        {"step": 3, "action": "После загрузки факта пересчитать модель с фактическим ДРР и затем сверить с закрытой реализацией WB.", "mode": "finance_reconciliation"},
                    ],
                    evidence=evidence, blockers=["Нет независимого фактического ДРР за сопоставимый период."],
                    follow_up="Пересчитать автоматически после появления факта.", analysis=basis,
                ))
                continue

            if actual_drr is None or scenario_profit is None or scenario_margin is None:
                continue

            drr_gap = actual_drr - float(plan_drr or 0.0)
            if scenario_profit < 0:
                target_5_margin = (break_even - 5.0) if break_even is not None else None
                sales_revenue = _n(rec.get("fact_sales_revenue_rub"))
                current_spend = _n(rec.get("fact_ad_spend_rub"))
                target_spend = (sales_revenue * target_5_margin / 100.0) if sales_revenue is not None and target_5_margin is not None and target_5_margin >= 0 else None
                cut_rub = max(0.0, (current_spend or 0.0) - target_spend) if target_spend is not None and current_spend is not None else None
                cut_pct = (cut_rub / current_spend * 100.0) if cut_rub is not None and current_spend and current_spend > 0 else None
                causes = rec.get("root_causes") or []
                cause_text = "; ".join(f"{x.get('cause')} ≈+{float(x.get('impact_pp') or 0):.1f} п.п." for x in causes[:2]) or "главный драйвер не удалось отделить по имеющемуся окну"
                actions = [
                    {"step": 1, "action": (
                        f"Снизить рекламный расход по этому SKU до ≤{target_spend:.0f} ₽ на сопоставимое окно "
                        f"(примерно −{cut_rub:.0f} ₽, −{cut_pct:.0f}%) и держать ДРР ≤{target_5_margin:.1f}% для модельной маржи ≥5%."
                        if target_spend is not None and cut_rub is not None and cut_pct is not None
                        else "Остановить дальнейшее увеличение рекламного расхода по этому SKU до возврата модели в положительную маржу."
                    ), "mode": "advertising_target"},
                    {"step": 2, "action": f"Система уже разложила отклонение: {cause_text}. Работать сначала с крупнейшим вкладом, а не менять цену вслепую.", "mode": "root_cause_resolved"},
                    {"step": 3, "action": "После изменения сравнить фактический ДРР, выручку на продажу, общий спрос и закрытую прибыль; если продажи падают быстрее расхода, откатить изменение.", "mode": "measured_follow_up"},
                ]
                out.append(DecisionCard(
                    decision_key=f"sku:{sku}:actual_ads_negative", scope="sku", entity_id=sku,
                    title=f"{x.get('name')}: фактический ДРР выводит модель в минус",
                    diagnosis=(
                        f"Плановый ДРР {plan_drr:.1f}%, фактический {actual_drr:.1f}%"
                        f". При неизменных остальных параметрах основной юнитки прибыль меняется с {plan_profit:.2f} до {scenario_profit:.2f} ₽/шт"
                        f", модельная маржа — {scenario_margin:.1f}%. Главный вклад в отклонение: {cause_text}."
                    ),
                    priority="critical", confidence="high" if str(rec.get("fact_quality") or "").startswith(("COMPLETE", "LIVE")) else "medium",
                    recommended_actions=actions, evidence=evidence,
                    follow_up="После изменения рекламы сравнить общий спрос, органику и закрытую прибыль на сопоставимом окне.", analysis=basis,
                ))
                continue

            # Positive but thin factual economics: this is the useful warning case for
            # products such as the beige toilet bucket — not 'loss', but very little room.
            if scenario_margin < 5.0 or drr_gap >= 5.0:
                target_5_margin = (break_even - 5.0) if break_even is not None else None
                sales_revenue = _n(rec.get("fact_sales_revenue_rub"))
                current_spend = _n(rec.get("fact_ad_spend_rub"))
                target_spend = (sales_revenue * target_5_margin / 100.0) if sales_revenue is not None and target_5_margin is not None and target_5_margin >= 0 else None
                cut_rub = max(0.0, (current_spend or 0.0) - target_spend) if target_spend is not None and current_spend is not None else None
                cut_pct = (cut_rub / current_spend * 100.0) if cut_rub is not None and current_spend and current_spend > 0 else None
                causes = rec.get("root_causes") or []
                cause_text = "; ".join(f"{x.get('cause')} ≈+{float(x.get('impact_pp') or 0):.1f} п.п." for x in causes[:2]) or "основной вклад не отделяется на текущем окне"
                actions = [
                    {"step": 1, "action": (
                        f"Ограничить расход по SKU до ≤{target_spend:.0f} ₽ на сопоставимое окно "
                        f"(сейчас {current_spend:.0f} ₽; снижение ≈{cut_rub:.0f} ₽ / {cut_pct:.0f}%) — это даёт ДРР ≤{target_5_margin:.1f}% и модельную маржу ≥5%."
                        if target_spend is not None and current_spend is not None and cut_rub is not None and cut_pct is not None and actual_drr > target_5_margin
                        else "Не увеличивать расход: текущая рекламная нагрузка уже съедает плановый запас маржи."
                    ), "mode": "advertising_target"},
                    {"step": 2, "action": f"Причина уже рассчитана: {cause_text}. Приоритет — крупнейший вклад в расхождение.", "mode": "root_cause_resolved"},
                    {"step": 3, "action": "Не менять цену только ради ДРР. Сначала измерить эффект изменения рекламы на общую выручку и органику, затем подтвердить результат закрытой реализацией WB.", "mode": "measured_follow_up"},
                ]
                out.append(DecisionCard(
                    decision_key=f"sku:{sku}:plan_fact_economics_gap", scope="sku", entity_id=sku,
                    title=f"{x.get('name')}: фактическая реклама съедает запас плановой маржи",
                    diagnosis=(
                        f"План ДРР {plan_drr:.1f}%, факт {actual_drr:.1f}%."
                        f" Плановая прибыль {plan_profit:.2f} ₽/шт; при фактической рекламной нагрузке модель даёт {scenario_profit:.2f} ₽/шт"
                        f" и маржу {scenario_margin:.1f}%. Главный вклад в отклонение: {cause_text}."
                    ),
                    priority="high" if scenario_margin < 5.0 else "medium",
                    confidence="high" if str(rec.get("fact_quality") or "").startswith(("COMPLETE", "LIVE")) else "medium",
                    recommended_actions=actions, evidence=evidence,
                    follow_up="Повторить сверку после следующего полного фактического окна и закрытия финансового отчёта.", analysis=basis,
                ))

        return out

    def _inventory_decisions(self, p: dict[str, Any]) -> list[DecisionCard]:
        out=[]; own=p.get('own_27') or {}
        invcfg=self.policy.thresholds.get('inventory',{})
        runtime_ad=((self.policy.raw.get('runtime') or {}).get('advertising') or {})
        critical=float(invcfg.get('critical_days_cover',2))
        default_warning=float(runtime_ad.get('min_stock_days_for_hold', invcfg.get('warning_days_cover',5)))
        default_target=float(runtime_ad.get('target_stock_days', invcfg.get('target_days_cover',14)))
        max_trend=float(runtime_ad.get('max_trend_pct_for_forecast',60))
        over=float(invcfg.get('overstock_days_cover',75))

        # Supply decisions belong to the physical inventory object, not to every
        # marketplace bundle that consumes it.  Example: "Бидон 15л 1шт" and
        # "Бидон 15л 2шт" are two sales formats of one stock pool.
        products = [x for x in own.get("products", [])[:500] if isinstance(x, dict)]
        members_by_physical: dict[str, list[dict[str, Any]]] = {}
        for prod in products:
            physical = str(prod.get("physical_position") or "").strip()
            if physical:
                members_by_physical.setdefault(physical, []).append(prod)
        physical_rows = {
            str(x.get("physical_position") or "").strip(): x
            for x in (own.get("physical_inventory") or [])
            if isinstance(x, dict) and str(x.get("physical_position") or "").strip()
        }
        grouped_skus: set[str] = set()

        for physical, row in physical_rows.items():
            members = members_by_physical.get(physical) or []
            if len(members) < 2:
                continue
            grouped_skus.update(str(x.get("sku") or "") for x in members)

            base_daily = 0.0
            exact_members = 0
            variant_notes: list[str] = []
            for prod in members:
                demand = build_demand_forecast(prod, max_growth_pct=max_trend)
                dq = demand_quality(prod, demand)
                daily = _n(demand.forecast_orders_1d)
                if not dq.ready or daily is None or daily <= 0:
                    daily = _n(prod.get("orders_per_day"))
                if daily is None or daily <= 0:
                    continue
                pack = _pack_qty(prod.get("name"))
                base_daily += daily * pack
                exact_members += 1
                variant_notes.append(f"{prod.get('name')}: ≈{daily:.2f} заказа/день × {pack} шт")

            if base_daily <= 0:
                aggregate_daily = _n(row.get("orders_per_day"))
                if aggregate_daily is not None and aggregate_daily > 0:
                    # This is order count, not guaranteed base-unit demand; use only
                    # as a low-confidence floor rather than inventing bundle mix.
                    base_daily = aggregate_daily

            if base_daily <= 0:
                continue

            stock = _n(row.get("k2_safe_stock"))
            stock_source = "К2 ФФ"
            if stock is None:
                stock = _n(row.get("ff_stock"))
                stock_source = "Остатки ФФ"
            if stock is None:
                stock = _n(row.get("ivanovo_stock"))
                stock_source = "ФФ Иваново"
            if stock is None:
                stock = 0.0
                stock_source = "нет подтверждённого ФФ-остатка"

            target = float(_n(row.get("supply_target_days")) or default_target)
            warning = float(_n(row.get("reorder_point_days")) or default_warning)
            debt = max(0.0, _n(row.get("fbs_debt_orders")) or 0.0)
            planned = max(0.0, _n(row.get("planned_order_qty")) or 0.0)
            supplier_debt = max(0.0, _n(row.get("supplier_debt_qty")) or 0.0)
            active_incoming = max(planned, supplier_debt)
            order_status = str(row.get("order_status") or "")
            active_order = ("ЗАКАЗ" in order_status.upper()) and active_incoming > 0
            cover_days = stock / base_daily if base_daily > 0 else None
            raw_need = max(0, ceil(base_daily * target + debt - stock))
            remaining_need = max(0, ceil(raw_need - active_incoming)) if active_order else raw_need
            variants = ", ".join(str(x.get("name") or x.get("sku")) for x in members)

            common_evidence = [
                _ev("Сводная · физический товар", "Физический товар", physical),
                _ev("Сводная · физический товар", "Доступный запас ФФ, шт", stock, stock_source),
                _ev("Прогноз вариантов", "Суммарный спрос базовых единиц, шт/день", round(base_daily, 3), "; ".join(variant_notes[:6])),
                _ev("Сводная · физический товар", "FBS-долг, шт", debt),
                _ev("Сводная · физический товар", "Целевой горизонт, дней", target),
                _ev("Сводная · физический товар", "Расчётная потребность до учёта заказа, шт", raw_need),
                _ev("Сводная · физический товар", "Активный заказ/долг поставщика, шт", active_incoming if active_order else 0),
                _ev("Сводная · физический товар", "Варианты WB", variants),
            ]

            # If an already placed physical-product order covers the calculated gap,
            # there is no owner decision.  Do not emit two fake SKU shortages.
            if active_order and remaining_need <= 0:
                continue

            if cover_days is not None and (cover_days <= warning or stock <= 0):
                if active_order:
                    title = f"{physical}: текущий заказ не полностью закрывает общий дефицит"
                    diagnosis = (
                        f"Общий спрос вариантов ≈{base_daily:.1f} базовых шт/день, запас {stock:.0f} шт. "
                        f"До горизонта {target:.0f} дней нужно ≈{raw_need} шт.; уже размещено/числится у поставщика ≈{active_incoming:.0f} шт. "
                        f"Остаётся незакрыто ≈{remaining_need} шт. Это одна задача по физическому товару, а не по SKU-комплектам."
                    )
                    action = f"Дозаказать только оставшиеся ≈{remaining_need} шт. физического товара; отдельные заказы для {variants} не создавать."
                else:
                    title = f"{physical}: дефицит общего физического запаса"
                    diagnosis = (
                        f"Общий спрос вариантов ≈{base_daily:.1f} базовых шт/день, запас {stock:.0f} шт. "
                        f"До горизонта {target:.0f} дней требуется ≈{raw_need} шт. SKU-комплекты используют один и тот же запас."
                    )
                    action = f"Сформировать одну поставку физического товара ≈{raw_need} шт.; распределение между карточками выполнять из общего пула."

                out.append(DecisionCard(
                    decision_key=f"physical:{physical}:stockout",
                    scope="physical_product",
                    entity_id=physical,
                    title=title,
                    diagnosis=diagnosis,
                    priority="critical" if stock <= 0 else "high",
                    confidence="high" if exact_members >= 2 else "medium",
                    recommended_actions=[
                        {"step": 1, "action": action, "mode": "physical_supply_plan"},
                        {"step": 2, "action": "После прихода пересчитать доступность всех комплектностей из одного физического остатка автоматически.", "mode": "automatic_follow_up"},
                    ],
                    evidence=common_evidence,
                    follow_up="Пересчитать при изменении общего физического остатка, спроса вариантов или активного заказа.",
                    analysis={
                        "metric_type": "physical_inventory_risk",
                        "time_semantics": "forecast",
                        "formula": "base_demand = Σ(SKU demand × pack_qty); need = base_demand × target_days + FBS debt − physical_stock − active_incoming",
                        "member_skus": [str(x.get("sku") or "") for x in members],
                    },
                ))
            elif cover_days is not None and cover_days >= over and not active_order:
                excess = max(0.0, stock - base_daily * target)
                out.append(DecisionCard(
                    decision_key=f"physical:{physical}:overstock",
                    scope="physical_product",
                    entity_id=physical,
                    title=f"{physical}: избыточный общий физический запас",
                    diagnosis=(
                        f"Запас {stock:.0f} шт. при общем спросе вариантов ≈{base_daily:.1f} базовых шт/день — "
                        f"около {cover_days:.0f} дней покрытия. Сверх горизонта {target:.0f} дней ≈{excess:.0f} шт."
                    ),
                    priority="medium",
                    confidence="high",
                    recommended_actions=[
                        {"step": 1, "action": "Не пополнять физический товар до возврата общего запаса к рабочему горизонту; отдельные комплектности не считать независимыми запасами.", "mode": "physical_supply_guard"},
                    ],
                    evidence=common_evidence,
                    follow_up="Автоматически пересчитать общий запас после следующего снимка.",
                    analysis={"metric_type": "physical_inventory_excess", "member_skus": [str(x.get("sku") or "") for x in members]},
                ))

        for x in products:
            sku=str(x.get('sku') or '')
            if sku in grouped_skus:
                continue
            sku=str(x.get('sku') or '')
            stock=_n(x.get('safe_stock'))
            if not sku or stock is None:
                continue
            daily=_n(x.get('orders_per_day'))
            planned_incoming=max(0.0, _n(x.get('planned_incoming_qty')) or 0.0)
            incoming_confirmed=bool(x.get('planned_incoming_confirmed'))
            effective_stock=stock + (planned_incoming if incoming_confirmed else 0.0)
            demand=build_demand_forecast(x, max_growth_pct=max_trend)
            dq=demand_quality(x, demand)
            forecast_daily=_n(demand.forecast_orders_1d)
            warning=float(_n(x.get('reorder_point_days')) or default_warning)
            target=float(_n(x.get('supply_target_days')) or default_target)
            debt=max(0,_n(x.get('fbs_debt_orders')) or 0)
            eq=economics_quality(x)

            analysis={
                "time_semantics":"forecast",
                "metric_type":"inventory_risk",
                "source":"27/Сводная + дневная история заказов + adaptive demand model",
                "period":f"история до {demand.as_of or 'неизвестно'}; прогноз вперёд",
                "formula":"inventory_position = on_hand + confirmed_inbound - committed; supply_need = forecast_daily × target_horizon + debt - inventory_position",
                "demand_model":demand.model_name,
                "demand_type":demand.demand_type,
                "adi":demand.adi,
                "cv2":demand.cv2,
                "backtest_mae":demand.backtest_mae,
                "backtest_bias":demand.backtest_bias,
                "quality_score":dq.score,
                "quality_level":dq.level,
                "quality_issues":dq.issues,
            }
            demand_evidence=[
                _ev('Сводная','Доступный остаток, шт',stock,x.get('safe_stock_source','')),
                _ev('Сводная','Заказы в день',daily,'операционный показатель; дневная история используется отдельно'),
                _ev('Прогноз спроса','Прогноз заказов в день',round(forecast_daily,3) if forecast_daily is not None else None,demand.model_name),
                _ev('Прогноз спроса','Тип спроса',demand.demand_type,f"ADI={demand.adi}; CV²={demand.cv2}"),
                _ev('Прогноз спроса','Ошибка модели MAE',demand.backtest_mae,'заказов/день на исторической проверке'),
                _ev('Качество данных','Оценка качества спроса',dq.score,'100 = согласованные и проверяемые сигналы'),
            ]

            # Conflicting / low-quality demand evidence stays in the analytics quality
            # layer. It must not become an operator Decision card: Decisions are reserved
            # for actions backed by enough evidence to be useful.
            if not dq.ready:
                continue

            if forecast_daily is None or forecast_daily <= 0:
                continue
            days=effective_stock/forecast_daily
            order_trend=demand.order_acceleration_pct
            freq_trend=demand.search_frequency_trend_pct
            trend_note=(f"; модель {demand.model_name} ≈{forecast_daily:.2f}/день")
            semantics="ожидаемое покрытие" if demand.demand_type in {"intermittent","lumpy"} else "прогнозное покрытие"

            if days <= warning:
                profit=_n(x.get('profit_rub')); margin=_n(x.get('margin_pct')); drr=_n(x.get('drr_pct'))
                pri='critical' if days<=critical or stock<=0 else 'high'
                source_conf='high' if str(x.get('safe_stock_source','')).upper() in {'K2 SAFE','FF','K2','WB FBS'} and dq.level=='high' else 'medium'
                need=max(0,ceil(forecast_daily*target + debt - effective_stock))

                if profit is not None and profit < 0 and eq.ready:
                    bridge_days=max(3.0,critical+1.0)
                    bridge_need=max(0,ceil(forecast_daily*bridge_days + debt - effective_stock))
                    title=f"{x.get('name')}: риск дефицита подтверждён, но подтверждённая юнитка отрицательная"
                    diagnosis=(
                        f"{semantics.capitalize()} ≈{days:.1f} дня при {trend_note.lstrip('; ')}. "
                        f"Расчётная прибыль {profit:.2f} ₽/шт"
                        + (f", маржа {margin:.1f}%" if margin is not None else "")
                        + f". Полное пополнение до {target:.0f} дней увеличит экспозицию убыточной модели."
                    )
                    actions=[
                        {"step":1,"action":"Не закупать полный горизонт до устранения причины отрицательной юнитки","mode":"unit_economics_guard"},
                        {"step":2,"action":f"Если наличие критично, мостовой ориентир — не более {bridge_need} шт. примерно до {bridge_days:.0f} дней ожидаемого спроса","mode":"bridge_supply"},
                        {"step":3,"action":"После подтверждения новой экономики пересчитать нормальный горизонт поставки","mode":"follow_up"},
                    ]
                else:
                    title=f"{x.get('name')}: подтверждён риск дефицита"
                    diagnosis=(
                        f"Остаток {stock:.0f} шт.; {semantics} ≈{days:.1f} дня при модели {demand.model_name} ≈{forecast_daily:.2f}/день. "
                        f"Для текущего целевого горизонта {target:.0f} дней расчётная потребность ≈{need} шт."
                    )
                    if planned_incoming and not incoming_confirmed:
                        diagnosis += f" План на {planned_incoming:.0f} шт. не считается доступным остатком, пока не подтверждены дата и приёмка."
                    if not eq.ready and profit is not None and profit < 0:
                        diagnosis += " Юнитка также показывает минус, но её качество недостаточно для запрета поставки; сначала сверить экономику."
                    actions=[
                        {"step":1,"action":f"Ориентир поставки: {need} шт. до рабочего горизонта {target:.0f} дней. Система пересчитает количество при следующем снимке остатка/поступления.","mode":"supply_plan"},
                        {"step":2,"action":"Проверить срок производства/доставки: при длинном lead time заменить фиксированный горизонт на риск дефицита до даты прихода","mode":"lead_time_check"},
                        {"step":3,"action":"После нового снимка остатков и заказов пересчитать прогноз и количество","mode":"follow_up"},
                    ]
                evidence=demand_evidence + [
                    _ev('derived','forecast_days_cover',round(days,1),semantics),
                    _ev('derived','supply_need_qty',need,f"целевой горизонт {target:.0f} дней"),
                    _ev('27/Сводная','fbs_debt_orders',debt),
                    _ev('27/Юнитка','profit_rub',profit,'использовать как жёсткий guard только при подтверждённой юнитке'),
                    _ev('27/Юнитка','margin_pct',margin),
                ]
                blockers=[]
                if planned_incoming and not incoming_confirmed:
                    blockers.append(f"запланировано {planned_incoming:.0f} шт., но дата/приёмка не подтверждены")
                out.append(DecisionCard(
                    decision_key=f"sku:{sku}:stockout", scope='sku', entity_id=sku,
                    title=title, diagnosis=diagnosis, priority=pri, confidence=source_conf,
                    recommended_actions=actions, evidence=evidence, blockers=blockers,
                    follow_up='Пересчитывать при каждом новом снимке остатков, спроса и подтверждённой поставки.',
                    analysis=analysis,
                ))
            elif days >= over:
                target_stock=forecast_daily*target
                excess=max(0.0,effective_stock-target_stock)
                cost=_n(x.get('cost_rub'))
                frozen=excess*cost if cost is not None else None
                money_note=(f" Это ≈{frozen:.0f} ₽ капитала по указанной себестоимости." if frozen is not None else "")
                out.append(DecisionCard(
                    decision_key=f"sku:{sku}:overstock", scope='sku', entity_id=sku,
                    title=f"{x.get('name')}: подтверждён избыточный запас",
                    diagnosis=(
                        f"{semantics.capitalize()} ≈{days:.0f} дней при модели {demand.model_name} ≈{forecast_daily:.2f}/день. "
                        f"Сверх текущего горизонта {target:.0f} дней находится ориентировочно {excess:.0f} шт.{money_note}"
                    ),
                    priority='medium', confidence='high' if dq.level=='high' else 'medium',
                    recommended_actions=[
                        {"step":1,"action":"Не пополнять этот SKU, пока запас не приблизится к рабочему горизонту","mode":"supply_guard"},
                        {"step":2,"action":"Оценивать разгрузку запаса через прибыльный дополнительный спрос, а не через автоматический демпинг","mode":"profit_guard"},
                        {"step":3,"action":"Проверить каннибализацию между близкими фасовками/комплектами прежде чем усиливать рекламу именно этого SKU","mode":"portfolio_check"},
                    ],
                    evidence=demand_evidence + [
                        _ev('derived','forecast_days_cover',round(days,1),semantics),
                        _ev('derived','excess_units_vs_target',round(excess,1),f"горизонт {target:.0f} дней"),
                        _ev('derived','frozen_capital_rub',round(frozen,2) if frozen is not None else None,'по указанной себестоимости'),
                    ],
                    follow_up='Пересчитать после заметного изменения спроса, цены, рекламы или остатка.',
                    analysis=analysis,
                ))
        return out

    def _advertising_control_decisions(self, p: dict[str, Any], snapshots: dict[str, Any]) -> list[DecisionCard]:
        """Numerical advertising plans for an exact campaign + nmID pair.

        Campaign totals are acceptable only for a single-SKU campaign.  In a multi-SKU
        campaign an exact SKU statistic is mandatory; otherwise the plan is a data
        blocker rather than a guessed money recommendation.
        """
        out: list[DecisionCard] = []
        runtime = self.policy.raw.get("runtime") or {}
        base_cfg = dict(runtime.get("advertising") or {})
        group_overrides = runtime.get("group_overrides") if isinstance(runtime.get("group_overrides"), dict) else {}
        sku_overrides = runtime.get("sku_overrides") if isinstance(runtime.get("sku_overrides"), dict) else {}
        products = {str(x.get("sku")): x for x in (p.get("own_27") or {}).get("products", []) if isinstance(x, dict) and x.get("sku")}
        live_economics = _live_economics_fact_index(snapshots)

        deep = _snap(snapshots, "advertising_optimizer", "deep_scan")
        deep_rows = []
        if isinstance(deep, dict):
            deep_rows = [x for x in deep.get("campaigns", []) if isinstance(x, dict)]

        if not deep_rows:
            camps = _rows(_snap(snapshots, "advertising_monitor", "active_campaigns"))
            stats = _rows(_snap(snapshots, "advertising_monitor", "stats_7d"))
            smap = {str(x.get("advertId") or x.get("advert_id") or x.get("id") or ""): x for x in stats}
            for c in camps:
                cid = str(c.get("advertId") or c.get("advert_id") or c.get("id") or "")
                nm_ids = []
                for key in ("nmIds", "nm_ids", "nms"):
                    if isinstance(c.get(key), list):
                        nm_ids = [int(x) for x in c[key] if str(x).isdigit()]
                        break
                deep_rows.append({"campaign": c, "campaign_id": cid, "nm_ids": nm_ids, "stats": smap.get(cid, {}), "stats_by_nm": {}, "recommendations": {}, "budget": {}})

        for row in deep_rows:
            campaign = row.get("campaign") if isinstance(row.get("campaign"), dict) else {}
            cid = str(row.get("campaign_id") or campaign.get("advertId") or campaign.get("advert_id") or campaign.get("id") or "")
            nm_ids = [int(x) for x in row.get("nm_ids", []) if str(x).isdigit()] if isinstance(row.get("nm_ids"), list) else []
            campaign_stats = row.get("stats") if isinstance(row.get("stats"), dict) else {}
            stats_by_nm = row.get("stats_by_nm") if isinstance(row.get("stats_by_nm"), dict) else {}
            recs = row.get("recommendations") if isinstance(row.get("recommendations"), dict) else {}
            budget = row.get("budget") if isinstance(row.get("budget"), dict) else {}

            # If a campaign exposes no nm list, retain one diagnostic card, but do not
            # manufacture a SKU association.
            targets: list[int | None] = nm_ids if nm_ids else [None]
            for nm in targets:
                product = products.get(str(nm)) if nm is not None else None
                if product is not None:
                    product = dict(product)
                    live_fact = live_economics.get(str(nm)) or {}
                    if live_fact:
                        product.update({
                            "fact_ad_spend_rub": live_fact.get("ad_spend_rub"),
                            "fact_sales_revenue_rub": live_fact.get("sales_revenue_rub"),
                            "fact_sales_qty": live_fact.get("sales_qty"),
                            "fact_drr_sales_pct": live_fact.get("fact_drr_sales_pct"),
                            "fact_economics_period_from": live_fact.get("period_from"),
                            "fact_economics_period_to": live_fact.get("period_to"),
                            "fact_economics_days": live_fact.get("days"),
                            "fact_economics_quality": live_fact.get("quality"),
                            "fact_economics_source": live_fact.get("source"),
                        })
                exact_stats = stats_by_nm.get(str(nm)) if nm is not None and isinstance(stats_by_nm.get(str(nm)), dict) else None
                stats = exact_stats or (campaign_stats if len(nm_ids) <= 1 else {})
                cfg = dict(base_cfg)
                if product is not None:
                    group_name = str(product.get("weekly_group") or "")
                    if group_name and isinstance(group_overrides.get(group_name), dict):
                        cfg.update(group_overrides[group_name])
                    if isinstance(sku_overrides.get(str(nm)), dict):
                        cfg.update(sku_overrides[str(nm)])
                controller = AdvertisingController(cfg)
                reco = recs.get(str(nm), {}) if nm is not None else {}
                plan = controller.build_plan(campaign, stats, product=product, bid_recommendation=reco, campaign_budget=budget)

                extra_blockers = list(plan.blockers)
                if nm is None:
                    extra_blockers.append("кампания не содержит подтверждённой связи с nmID")
                if len(nm_ids) > 1 and exact_stats is None:
                    extra_blockers.append("WB не дал отдельную статистику этого nmID внутри многотоварной кампании; общие цифры кампании к товару не приписываются")
                if nm is not None and product is None:
                    extra_blockers.append("nmID отсутствует в доверенной юнитке/товарном реестре")
                extra_blockers = list(dict.fromkeys(x for x in extra_blockers if x))

                # A multi-SKU card without exact stats is diagnostic only. Never surface
                # a numerical bid change derived from zero/foreign campaign totals.
                cur = plan.current_bid_rub
                tgt = plan.target_bid_rub
                action_text = plan.action_text
                confidence = plan.confidence
                if len(nm_ids) > 1 and exact_stats is None:
                    tgt = cur
                    action_text = "Ставку этого товара не менять до получения отдельной статистики nmID внутри кампании."
                    confidence = "low"

                actions: list[dict[str, Any]] = [{
                    "step": 1,
                    "action": action_text + " Текущая версия ничего в кабинете WB не меняет.",
                    "mode": "numeric_ad_plan",
                    "value": {"current_bid_rub": cur, "target_bid_rub": tgt, "change_pct": (0.0 if tgt == cur and cur is not None else plan.bid_change_pct), "current_spend_24h_rub": plan.current_spend_24h_rub, "max_spend_24h_rub": plan.max_spend_next_24h_rub},
                }]
                if plan.reasons:
                    actions.append({"step":2,"action":"Основание: " + " ".join(plan.reasons[:3]),"mode":"reasoning"})
                actions.append({"step":3,"action":plan.observation_rule,"mode":"recheck_rule"})
                if extra_blockers:
                    actions.append({"step":4,"action":"Для точного решения не хватает: " + " ".join(extra_blockers),"mode":"data_guard"})

                name = str(campaign.get("name") or f"Кампания {cid}")
                seller_article = str((product or {}).get("seller_article") or (product or {}).get("name") or "").strip()
                sku_label = f" · nmID {nm}" if nm is not None else ""
                diagnosis_bits = [plan.decision_label]
                if len(nm_ids) > 1 and exact_stats is None:
                    diagnosis_bits = ["нет подтверждённой SKU-статистики — денежное решение заблокировано"]
                if plan.business_drr_pct is not None:
                    diagnosis_bits.append(f"бизнес-ДРР {plan.business_drr_pct:.1f}% при допустимом потолке ≤{plan.target_drr_pct:.1f}%")
                elif plan.observed_drr_pct is not None:
                    diagnosis_bits.append(f"атрибуционный ДРР {plan.observed_drr_pct:.1f}% при допустимом потолке ≤{plan.target_drr_pct:.1f}%")
                if plan.wb_attributed_drr_pct is not None and plan.business_drr_pct is not None:
                    diagnosis_bits.append(f"WB-атрибуция кампании {plan.wb_attributed_drr_pct:.1f}%")
                if plan.incremental_capture_pct is not None:
                    diagnosis_bits.append(f"приростность ≈{plan.incremental_capture_pct:.0f}%")
                if plan.cannibalization_risk != "unknown":
                    diagnosis_bits.append(f"риск перепокупки органики: {plan.cannibalization_risk}")
                if plan.orders_trend_pct is not None: diagnosis_bits.append(f"тренд заказов {plan.orders_trend_pct:+.1f}%")
                if plan.traffic_trend_pct is not None: diagnosis_bits.append(f"тренд трафика {plan.traffic_trend_pct:+.1f}%")
                if plan.stock_days_forecast is not None: diagnosis_bits.append(f"прогноз покрытия {plan.stock_days_forecast:.1f} дн.")
                if plan.cohort_maturity_pct is not None: diagnosis_bits.append(f"свежая когорта созрела примерно на {plan.cohort_maturity_pct:.0f}%")
                if plan.economic_max_drr_pct is not None: diagnosis_bits.append(f"экономический потолок ДРР ≈{plan.economic_max_drr_pct:.1f}%")

                priority = "critical" if plan.decision == "PAUSE_REVIEW" else ("high" if plan.decision in {"SCALE_DOWN","CAP_DEMAND"} else "medium")
                if extra_blockers and confidence == "low" and plan.decision != "PAUSE_REVIEW":
                    priority = "medium"
                evidence = [
                    _ev("WB Promotion", "campaign_id", cid, name),
                    _ev("WB Promotion", "nm_id", nm),
                    _ev("WB Promotion", "sku_stats_exact", bool(exact_stats) or len(nm_ids) <= 1),
                    _ev("расчёт", "решение", plan.decision_label),
                    _ev("WB Promotion", "current_bid_rub", cur),
                    _ev("расчёт", "target_bid_rub", tgt),
                    _ev("WB Promotion", "current_spend_24h_rub", plan.current_spend_24h_rub),
                    _ev("расчёт", "max_spend_next_24h_rub", plan.max_spend_next_24h_rub),
                    _ev("расчёт", "target_drr_pct", plan.target_drr_pct),
                    _ev("расчёт", "max_ad_cost_per_order_rub", plan.max_ad_cost_per_order_rub),
                    _ev("расчёт", "economic_max_drr_pct", plan.economic_max_drr_pct),
                    _ev("бизнес-факт SKU", "business_drr_pct", plan.business_drr_pct),
                    _ev("бизнес-факт SKU", "business_ad_spend_rub", plan.business_ad_spend_rub),
                    _ev("бизнес-факт SKU", "business_revenue_rub", plan.business_revenue_rub),
                    _ev("WB Promotion", "wb_attributed_drr_pct", plan.wb_attributed_drr_pct),
                    _ev("расчёт", "observed_drr_pct", plan.observed_drr_pct),
                    _ev("приростность", "incremental_capture_pct", plan.incremental_capture_pct),
                    _ev("приростность", "paid_order_share_pct", plan.paid_order_share_pct),
                    _ev("приростность", "cannibalization_risk", plan.cannibalization_risk),
                    _ev("экономика трафика", "max_cpc_rub", plan.max_cpc_rub),
                    _ev("экономика трафика", "conversion_rate_pct", plan.conversion_rate_pct),
                    _ev("план трафика", "required_clicks_24h", plan.required_clicks_24h),
                    _ev("режим", "operating_mode", plan.operating_mode),
                    _ev("trend", "orders_trend_pct", plan.orders_trend_pct),
                    _ev("trend", "traffic_trend_pct", plan.traffic_trend_pct),
                    _ev("27/Сводная", "stock_days_forecast", plan.stock_days_forecast),
                    _ev("27/_WB_ORDER_FEED", "cohort_maturity_pct", plan.cohort_maturity_pct),
                    _ev("27/_WB_ORDER_FEED", "cohort_open_orders_7d", plan.cohort_open_orders_7d),
                    _ev("27/_WB_ORDER_FEED", "buyout_lag_p50_days", plan.buyout_lag_p50_days),
                    _ev("27/_WB_ORDER_FEED", "buyout_lag_p90_days", plan.buyout_lag_p90_days),
                    _ev("WB recommendations", "competitive_bid_rub", plan.wb_competitive_bid_rub),
                ]
                for reason in plan.reasons:
                    evidence.append(_ev("расчёт", "причина", reason))

                suffix = "zero_orders" if plan.decision == "PAUSE_REVIEW" else "numeric_control"
                if nm is None:
                    decision_key = f"advert:{cid}:{suffix}"
                    scope = "campaign"
                    entity_id = cid
                else:
                    decision_key = f"advert:{cid}:{nm}:{suffix}"
                    scope = "campaign_sku"
                    entity_id = f"{cid}:{nm}"
                out.append(DecisionCard(
                    decision_key=decision_key,
                    scope=scope, entity_id=entity_id,
                    title=f"{seller_article + ' · ' if seller_article else ''}{name}{sku_label}: {diagnosis_bits[0]}",
                    diagnosis=" · ".join(diagnosis_bits),
                    priority=priority, confidence=confidence,
                    recommended_actions=actions,
                    evidence=evidence,
                    blockers=extra_blockers,
                    follow_up=plan.observation_rule,
                ))
        return out


    def _card_content_decisions(self, p: dict[str, Any], snapshots: dict[str, Any]) -> list[DecisionCard]:
        """Find content/card opportunities without pretending that more photos cause sales.

        A content recommendation appears only when a weak funnel signal and an observable
        content gap coexist.  The next step is an experiment, not an asserted causal fix.
        """
        cards = _rows(_snap(snapshots, "cards", "card_catalog"), ("cards","data","items"))
        funnel = _rows(_snap(snapshots, "funnel", "funnel_7d"), ("items","data"))
        if not cards or not funnel:
            return []
        funnel_by_nm: dict[str, dict[str, Any]] = {}
        convs=[]
        for r in funnel:
            nm = str(r.get("nmID") or r.get("nmId") or r.get("nm_id") or "")
            if not nm: continue
            funnel_by_nm[nm]=r
            conv = r.get("conversions") if isinstance(r.get("conversions"),dict) else {}
            atc=_pick_num(conv,"addToCartPercent","add_to_cart_percent") or _pick_num(r,"addToCartPercent","add_to_cart_percent")
            if atc is not None: convs.append(atc)
        if not convs:
            return []
        convs_sorted=sorted(convs)
        median_conv=convs_sorted[len(convs_sorted)//2]
        media_counts=[]
        parsed=[]
        for c in cards:
            nm=str(c.get("nmID") or c.get("nmId") or c.get("nm_id") or "")
            if not nm: continue
            media=None
            for key in ("mediaFiles","photos","media"):
                if isinstance(c.get(key),list): media=len(c[key]); break
            if media is not None: media_counts.append(media)
            parsed.append((nm,c,media))
        if not media_counts:
            return []
        media_sorted=sorted(media_counts)
        median_media=media_sorted[len(media_sorted)//2]
        high_media=max(media_counts)
        out=[]
        for nm,c,media in parsed:
            f=funnel_by_nm.get(nm)
            if not f or media is None: continue
            conv=f.get("conversions") if isinstance(f.get("conversions"),dict) else {}
            atc=_pick_num(conv,"addToCartPercent","add_to_cart_percent") or _pick_num(f,"addToCartPercent","add_to_cart_percent")
            opens=_pick_num(f,"openCardCount","open_card_count","views")
            if atc is None or opens is None or opens < 200: continue
            if atc >= median_conv or media >= median_media:
                continue
            title=str(c.get("title") or c.get("vendorCode") or f"nmID {nm}")
            out.append(DecisionCard(
                decision_key=f"content:{nm}:gallery_experiment",
                scope="sku", entity_id=nm,
                title=f"{title}: проверить фотогалерею как причину слабой карточки",
                diagnosis=f"Переход в корзину {atc:.1f}% ниже медианы наблюдаемого портфеля {median_conv:.1f}%, при этом в карточке {media} медиа против медианы {median_media} (максимум среди наблюдаемых карточек {high_media}). Это корреляция, не доказанная причина.",
                priority="medium", confidence="medium",
                recommended_actions=[
                    {"step":1,"action":"Собрать недостающие полезные слайды по возражениям покупателя: размеры, комплект, применение, материал, упаковка, сравнение вариантов. Не добавлять дубли ради количества.","mode":"content_hypothesis"},
                    {"step":2,"action":"Провести А/Б-тест главного фото/галереи и заранее зафиксировать метрики: CTR, переход в корзину, заказ. Менять одну гипотезу за тест.","mode":"experiment"},
                    {"step":3,"action":"Если конверсия не улучшилась на достаточной выборке, не считать число фото причиной и перейти к цене/офферу/отзывам/качеству.","mode":"recheck_rule"},
                ],
                evidence=[_ev("WB Content","media_count",media),_ev("портфель","median_media_count",median_media),_ev("WB Analytics","add_to_cart_pct",atc),_ev("портфель","median_add_to_cart_pct",median_conv),_ev("WB Analytics","open_card_count",opens)],
                follow_up="Оценивать только по результату А/Б-теста и достаточной выборке, а не по факту заполнения слотов.",
            ))
        return out

    def _operational_decisions(self, p: dict[str, Any], snapshots: dict[str, Any]) -> list[DecisionCard]:
        """Turn the remaining detector snapshots into cross-contour operational plans.

        These rules are deliberately conservative: when the missing link (for example
        campaign→SKU or SKU unit economics) is not proven, the card says exactly what
        must be resolved before a money-changing recommendation can be trusted.
        """
        out: list[DecisionCard] = []

        # Advertising money decisions are produced by _advertising_control_decisions().

        # --- Funnel: enough traffic but weak card→cart conversion ---
        funnel=_rows(_snap(snapshots,'funnel','funnel_7d'))
        for row in funnel:
            sku=str(row.get('nmID') or row.get('nmId') or row.get('nm_id') or '')
            views=_pick_num(row,'openCardCount','views','open_card_count') or 0
            carts=_pick_num(row,'addToCartCount','carts','add_to_cart') or 0
            orders=_pick_num(row,'ordersCount','orders','orders_count') or 0
            conv=row.get('conversions') if isinstance(row.get('conversions'),dict) else {}
            cart_pct=_n(conv.get('addToCartPercent')) or ((carts/views*100) if views else None)
            if views>=500 and cart_pct is not None and cart_pct<7:
                claims=[x for x in _rows(_snap(snapshots,'returns_quality','open_claims')) if str(x.get('nmId') or x.get('nmID') or '')==sku]
                out.append(DecisionCard(
                    decision_key=f'sku:{sku}:weak_card_conversion',scope='sku',entity_id=sku,
                    title=f'nmID {sku}: трафик есть, но карточка слабо переводит в корзину',
                    diagnosis=f"За окно карточку открыли ≈{views:.0f} раз, добавление в корзину ≈{cart_pct:.1f}%, заказов {orders:.0f}. Это больше похоже на проблему оффера/карточки/цены, чем на нехватку показов." + (f" Параллельно открыто возвратных претензий: {len(claims)}." if claims else ''),
                    priority='high',confidence='high' if views>=1000 else 'medium',
                    recommended_actions=[
                        {'step':1,'action':'Не покупать дополнительный трафик, пока не исправлена конверсия карточки','mode':'advertising_guard'},
                        {'step':2,'action':'Сравнить цену, рейтинг, отзывы и первый экран карточки с релевантной выдачей WB','mode':'market_card_analysis'},
                        {'step':3,'action':'Если возвраты повторяют одну причину — сначала исправить описание/комплектацию/качество, затем провести один A/B-тест карточки','mode':'experiment'},
                    ],
                    evidence=[_ev('WB Funnel','card_views',views),_ev('WB Funnel','cart_conversion_pct',round(cart_pct,1)),_ev('WB Funnel','orders',orders),_ev('WB Returns','open_claims_same_sku',len(claims))],
                    follow_up='Оценить новую конверсию после достаточного числа просмотров, не раньше.'
                ))

        # --- Product/content blocking errors ---
        card_errors=_rows(_snap(snapshots,'cards','card_errors'))
        for row in card_errors[:20]:
            sku=str(row.get('nmID') or row.get('nmId') or row.get('id') or '')
            err=str(row.get('error') or row.get('message') or 'Ошибка карточки')
            out.append(DecisionCard(
                decision_key=f'sku:{sku}:card_error',scope='sku',entity_id=sku,
                title=f'nmID {sku}: сначала исправить карточку, потом масштабировать продажи',
                diagnosis=err,
                priority='critical',confidence='high',
                recommended_actions=[
                    {'step':1,'action':'Исправить указанную обязательную характеристику/ошибку карточки','mode':'content_fix'},
                    {'step':2,'action':'До исправления не увеличивать рекламный бюджет этой карточки','mode':'advertising_guard'},
                    {'step':3,'action':'После прохождения проверки WB перепроверить индексацию и поисковую видимость','mode':'follow_up'},
                ],evidence=[_ev('WB Content','card_error',err)],follow_up='Контроль после следующей синхронизации карточек.'
            ))

        # --- Quality incident: returns + chats + deductions become one root-cause plan ---
        claims=_rows(_snap(snapshots,'returns_quality','open_claims'))
        chats=_rows(_snap(snapshots,'buyer_chats','chat_events'))
        deductions=_rows(_snap(snapshots,'cost_guard','deductions'))
        claim_by_sku: dict[str,list[dict[str,Any]]] = {}
        for x in claims:
            sku=str(x.get('nmId') or x.get('nmID') or x.get('sku') or '')
            if sku: claim_by_sku.setdefault(sku,[]).append(x)
        ret_warn=int(self.policy.thresholds.get('returns',{}).get('open_claims_warn',3))
        quality_words=('не тот','не соответствует','комплект','вложен','вложение','перепут')
        chat_quality=[x for x in chats if any(w in str(x.get('message') or x.get('text') or '').lower() for w in quality_words)]
        deduction_quality=[x for x in deductions if any(w in str(x.get('reason') or '').lower() for w in quality_words)]
        for sku,rows in claim_by_sku.items():
            if len(rows)<ret_warn: continue
            reasons=[str(x.get('reason') or '') for x in rows]
            main_reason=max(set(reasons),key=reasons.count) if reasons else 'повторяющиеся возвраты'
            extra=[]
            if chat_quality: extra.append(f"чатов с похожей жалобой: {len(chat_quality)}")
            if deduction_quality: extra.append(f"удержаний/штрафов с похожей причиной: {len(deduction_quality)}")
            out.append(DecisionCard(
                decision_key=f'sku:{sku}:quality_root_cause',scope='sku',entity_id=sku,
                title=f'nmID {sku}: повторяющаяся проблема качества — нужен root-cause, а не больше рекламы',
                diagnosis=f"Открыто {len(rows)} претензии; основная причина: «{main_reason}»." + (" Дополнительные сигналы: "+', '.join(extra)+'.' if extra else ''),
                priority='critical',confidence='high',
                recommended_actions=[
                    {'step':1,'action':'Не масштабировать рекламу SKU до устранения повторяющейся причины','mode':'advertising_guard'},
                    {'step':2,'action':'Сверить карточку и фактическую комплектацию; отдельно проверить сборку/маркировку на ФФ','mode':'quality_audit'},
                    {'step':3,'action':'Выбрать 5–10 последних проблемных заказов и найти общий этап ошибки: контент → комплектация → упаковка → сборка → маркировка','mode':'root_cause_analysis'},
                    {'step':4,'action':'После исправления контролировать долю возвратов/жалоб на следующей когорте заказов','mode':'follow_up'},
                ],
                evidence=[_ev('WB Returns','open_claims',len(rows),main_reason),_ev('Buyer chats','quality_messages',len(chat_quality)),_ev('WB deductions','quality_deductions',len(deduction_quality))],
                follow_up='Закрывать инцидент только после новой когорты без повторения причины.'
            ))

        # Deductions/penalties that are material even without a matching quality incident.
        fincfg=self.policy.thresholds.get('finance',{}); penalty_warn=float(fincfg.get('penalty_alert_rub',1000))
        material=[x for x in deductions if (_pick_num(x,'amount','sum','penalty') or 0)>=penalty_warn]
        if material:
            total=sum((_pick_num(x,'amount','sum','penalty') or 0) for x in material)
            out.append(DecisionCard(
                decision_key='finance:material_deductions',scope='finance',entity_id='deductions',
                title='Есть материальные удержания — их нужно превратить в устранимую операционную причину',
                diagnosis=f"Найдено {len(material)} удержаний выше порога; сумма по текущему срезу ≈{total:.0f} ₽.",
                priority='high',confidence='high',
                recommended_actions=[
                    {'step':1,'action':'Разложить удержания по причине и SKU/заказу, а не списывать одной строкой расходов','mode':'finance_audit'},
                    {'step':2,'action':'Если причина повторяется — создать операционный контроль на ФФ/карточку/маркировку','mode':'root_cause_analysis'},
                    {'step':3,'action':'Проверить возможность претензии/оспаривания только там, где есть доказательства','mode':'claims_review'},
                ],evidence=[_ev('WB deductions','material_total_rub',total),*[_ev('WB deductions',str(x.get('nmID') or x.get('nmId') or 'item'),_pick_num(x,'amount','sum','penalty'),str(x.get('reason') or '')) for x in material[:5]]],
                follow_up='Сравнить сумму удержаний на следующей неделе после исправления причины.'
            ))

        # FBS reshipment is an operational exception that should be resolved explicitly.
        reship=_rows(_snap(snapshots,'orders_fbs','reshipment'))
        if reship:
            ids=[str(x.get('orderId') or x.get('id') or '') for x in reship[:10]]
            out.append(DecisionCard(
                decision_key='fbs:reshipment',scope='fbs',entity_id='reshipment',
                title='Есть FBS-переотгрузки — проверить, чтобы заказ не потерялся и остаток не списался неверно',
                diagnosis=f"Система видит {len(reship)} заказ(а/ов), требующих сценария повторной отгрузки.",
                priority='high',confidence='high',
                recommended_actions=[
                    {'step':1,'action':'Проверить статус каждого заказа и факт наличия товара на ФФ','mode':'fbs_check'},
                    {'step':2,'action':'Сверить, не был ли товар уже списан/зарезервирован первой попыткой','mode':'stock_reconcile'},
                    {'step':3,'action':'После повторной отгрузки убедиться, что заказ исчез из очереди исключений','mode':'follow_up'},
                ],evidence=[_ev('WB FBS','reshipment_orders',', '.join(ids))],follow_up='Повторная проверка на следующем 10-минутном цикле.'
            ))

        # Promotion price must never be accepted without unit-economics floor.
        promos=_rows(_snap(snapshots,'price_margin','promotions'))
        for row in promos[:30]:
            sku=str(row.get('nmID') or row.get('nmId') or '')
            current=_pick_num(row,'price','currentPrice','discountedPrice'); plan=_pick_num(row,'planPrice','promoPrice','priceWithDiscount')
            if sku and plan is not None and current is not None and plan<current:
                own=next((x for x in (p.get('own_27') or {}).get('products',[]) if str(x.get('sku'))==sku),None)
                if own:
                    profit=_n(own.get('profit_rub')); margin=_n(own.get('margin_pct'))
                    diagnosis=f"Акция предлагает цену {plan:.0f} ₽ вместо {current:.0f} ₽. Текущая юнитка: прибыль {profit if profit is not None else '—'} ₽, маржа {margin if margin is not None else '—'}%."
                    blockers=[]
                else:
                    diagnosis=f"Акция предлагает цену {plan:.0f} ₽ вместо {current:.0f} ₽, но подтверждённой юнитки этого SKU в trusted-источнике нет."
                    blockers=['Нет подтверждённой себестоимости/юнитки SKU — участие в акции нельзя рекомендовать.']
                out.append(DecisionCard(
                    decision_key=f'sku:{sku}:promotion_guard',scope='sku',entity_id=sku,
                    title=f'nmID {sku}: акция снижает цену — сначала пересчитать прибыль',diagnosis=diagnosis,
                    priority='high',confidence='high' if own else 'medium',
                    recommended_actions=[
                        {'step':1,'action':'Не входить в акцию автоматически','mode':'price_guard'},
                        {'step':2,'action':'Пересчитать прибыль/маржу/ROI на акционной цене с комиссией, логистикой, налогом и рекламой','mode':'unit_economics'},
                        {'step':3,'action':'Рекомендовать участие только если абсолютная прибыль и минимальная маржа остаются допустимыми','mode':'read_only_recommendation'},
                    ],evidence=[_ev('WB Promotions','current_price',current),_ev('WB Promotions','promo_price',plan),_ev('27/Юнитка','profit_rub',own.get('profit_rub') if own else None)],blockers=blockers,
                    follow_up='Пересчитать после изменения условий акции или юнитки.'
                ))

        # Supply acceptance: choose the economically safer available warehouse, but do not ignore localisation.
        acceptance=_rows(_snap(snapshots,'supply','acceptance'))
        available=[x for x in acceptance if x.get('allowUnload') is not False and _pick_num(x,'coefficient') is not None]
        if len(available)>=2:
            best=min(available,key=lambda x:_pick_num(x,'coefficient') or 0); worst=max(available,key=lambda x:_pick_num(x,'coefficient') or 0)
            bc=_pick_num(best,'coefficient') or 0; wc=_pick_num(worst,'coefficient') or 0
            if wc>bc:
                out.append(DecisionCard(
                    decision_key='supply:acceptance_choice',scope='supply',entity_id='warehouses',
                    title='Есть разница в коэффициентах приёмки — учитывать её в плане поставки',
                    diagnosis=f"{best.get('warehouseName') or best.get('warehouse_name')}: коэффициент {bc:g}; {worst.get('warehouseName') or worst.get('warehouse_name')}: {wc:g}. Но дешёвая приёмка не должна ухудшать локализацию/скорость доставки.",
                    priority='medium',confidence='high',
                    recommended_actions=[
                        {'step':1,'action':f"Использовать {best.get('warehouseName') or best.get('warehouse_name')} как базовый дешёвый вариант приёмки","mode":"supply_candidate"},
                        {'step':2,'action':'Перед переносом объёма сверить региональный спрос, локализацию и стоимость последней мили','mode':'localisation_guard'},
                        {'step':3,'action':'Разделить поставку, если более дорогой склад нужен для продаж в своём регионе','mode':'supply_plan'},
                    ],evidence=[_ev('WB Acceptance',str(best.get('warehouseName') or 'best'),'coefficient='+str(bc)),_ev('WB Acceptance',str(worst.get('warehouseName') or 'worst'),'coefficient='+str(wc))],
                    follow_up='Проверять коэффициенты перед каждой новой поставкой.'
                ))
        return out

    def _event_decisions(self, p: dict[str, Any], snapshots: dict[str, Any]) -> list[DecisionCard]:
        """Translate detector-only events that need historical context into action plans."""
        out: list[DecisionCard] = []
        events=snapshots.get('_events') or []
        if not isinstance(events,list):
            return out
        for e in events:
            if not isinstance(e,dict): continue
            agent=str(e.get('agent') or ''); key=str(e.get('key') or ''); severity=str(e.get('severity') or 'info')
            payload=e.get('payload') if isinstance(e.get('payload'),dict) else {}
            if agent=='search_positions' and key.startswith('position:') and severity in {'warning','critical'}:
                after=payload.get('after') if isinstance(payload.get('after'),dict) else {}
                before=payload.get('before') if isinstance(payload.get('before'),dict) else {}
                sku=str(after.get('nm_id') or before.get('nm_id') or key.split(':',1)[-1])
                query=str(after.get('query') or before.get('query') or '')
                old=_n(before.get('position')); now=_n(after.get('position'))
                out.append(DecisionCard(
                    decision_key=f'sku:{sku}:position_drop:{query}',scope='sku',entity_id=sku,
                    title=f'nmID {sku}: позиция просела — сначала найти причину, а не повышать ставку',
                    diagnosis=(f"По запросу «{query}» позиция изменилась {old:.0f} → {now:.0f}. " if old is not None and now is not None else f"По запросу «{query}» зафиксирована просадка позиции. ")+'Одной рекламной ставкой это объяснять нельзя: причина может быть в остатке, цене, CR, карточке или рынке.',
                    priority='critical' if severity=='critical' else 'high',confidence='high',
                    recommended_actions=[
                        {'step':1,'action':'Зафиксировать ставку и не повышать её автоматически','mode':'advertising_guard'},
                        {'step':2,'action':'Сверить остаток/дни покрытия, цену, юнитку, CR карточки и публичную выдачу конкурентов','mode':'cross_contour_analysis'},
                        {'step':3,'action':'После определения причины менять один фактор и измерять позицию + прибыль, а не только место в выдаче','mode':'experiment'},
                    ],
                    evidence=[_ev('WB Search Analytics','query',query),_ev('WB Search Analytics','position_before',old),_ev('WB Search Analytics','position_after',now)],
                    follow_up='Повторный замер позиции после одного полного окна выдачи/рекламы.'
                ))
            elif agent=='reviews_questions' and key in {'unanswered_feedbacks','unanswered_questions'} and severity in {'warning','critical'}:
                kind='отзывы' if 'feedback' in key else 'вопросы'
                out.append(DecisionCard(
                    decision_key=f'customer:{key}',scope='customer',entity_id=kind,
                    title=f'Накопились неотвеченные {kind} — это уже операционная задача',
                    diagnosis=str(e.get('message') or e.get('title') or ''),priority='high',confidence='high',
                    recommended_actions=[
                        {'step':1,'action':f'Разобрать новые {kind}: сначала негатив/проблемы товара, затем обычные обращения','mode':'customer_triage'},
                        {'step':2,'action':'Повторяющиеся жалобы связать с возвратами и карточкой соответствующего SKU','mode':'quality_link'},
                        {'step':3,'action':'После ответа проверить, нет ли одной причины, требующей изменения товара/контента/ФФ','mode':'root_cause_analysis'},
                    ],evidence=[_ev('WB Feedbacks',key,e.get('message'))],follow_up='Перепроверить очередь на следующем 15-минутном цикле.'
                ))
            elif agent=='api_health' and key in {'wb_api_degradation','token_info_failed','degradations_check_failed'} and severity in {'warning','critical'}:
                out.append(DecisionCard(
                    decision_key=f'system:{key}',scope='system',entity_id='wb_api',
                    title='Источник WB API нестабилен — денежные выводы нужно временно ограничить',
                    diagnosis=str(e.get('message') or e.get('title') or ''),priority='high',confidence='high',
                    recommended_actions=[
                        {'step':1,'action':'Не принимать новые денежные решения на неполных/устаревших Seller API данных','mode':'data_guard'},
                        {'step':2,'action':'Продолжить мониторинг по trusted Sheets и публичным read-only источникам, где это допустимо','mode':'fallback'},
                        {'step':3,'action':'Вернуть Seller API в decision graph только после успешного health-check','mode':'recovery'},
                    ],evidence=[_ev('API Health',key,e.get('message'))],follow_up='Автоматический health-check каждые 10 минут.'
                ))
        return out

    def _search_market_decisions(self, p: dict[str, Any], snapshots: dict[str, Any]) -> list[DecisionCard]:
        out=[]
        market=snapshots.get('competitors',{})
        pos=snapshots.get('search_positions',{}).get('positions',{}).get('data',{}) if isinstance(snapshots.get('search_positions'),dict) else {}
        # Competitor agent stores one snapshot per query; API dashboard passes them when available.
        for query, snap in market.items() if isinstance(market,dict) else []:
            data=snap.get('data',{}) if isinstance(snap,dict) else {}
            analysis=data.get('analysis') or {}
            if analysis.get('own_position') and analysis.get('own_position')>20 and analysis.get('price_vs_median_pct') is not None:
                delta=float(analysis['price_vs_median_pct'])
                rating_delta=_n(analysis.get('rating_vs_median'))
                if delta>10:
                    action='Проверить цену и оффер относительно релевантных конкурентов'
                elif rating_delta is not None and rating_delta < -0.15:
                    action='Сначала разобрать рейтинг/отзывы и карточку: цена не выглядит главной причиной слабой выдачи'
                else:
                    action='Проверить CTR/карточку/релевантность запроса; цена не выглядит главной причиной'
                out.append(DecisionCard(
                    decision_key=f"market:{query}:visibility", scope='market', entity_id=query,
                    title=f"По запросу «{query}» видимость слабая — есть рыночный контекст",
                    diagnosis=f"Позиция собственного товара около {analysis['own_position']}; отклонение цены от медианы релевантной выдачи {delta:+.1f}%.",
                    priority='high', confidence='medium',
                    recommended_actions=[{"step":1,"action":action,"mode":"analysis"},{"step":2,"action":"Не повышать ставку, пока не исключены цена/карточка/остаток как причина","mode":"advertising_guard"},{"step":3,"action":"После изменения измерить позицию, CTR, CR и прибыль","mode":"experiment"}],
                    evidence=[_ev('WB public search','own_position',analysis.get('own_position')),_ev('WB public search','price_vs_median_pct',delta),_ev('WB public search','own_rating',analysis.get('own_rating')),_ev('WB public search','rating_vs_median',analysis.get('rating_vs_median')),_ev('WB public search','competitors',analysis.get('competitor_count'))],
                    follow_up='Повторить рыночный срез через 2–6 часов для быстрых запросов или на следующий день для медленных.'
                ))
        return out
