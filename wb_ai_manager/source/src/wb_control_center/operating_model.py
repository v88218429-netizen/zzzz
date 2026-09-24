from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


def _n(v: Any) -> float | None:
    try:
        if v is None or isinstance(v, bool):
            return None
        return float(v)
    except Exception:
        return None


@dataclass
class OperatingFinding:
    key: str
    level: str
    title: str
    conclusion: str
    action: str
    evidence: list[dict[str, Any]]
    confidence: str = "high"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OperatingModel:
    """Interpret trusted business facts without mixing different order cohorts.

    Incoming orders are a leading signal. Buyouts and realised profit arrive later.
    Calendar-week order growth must never be labelled good/bad by comparing it with
    calendar-week buyouts/profit unless an explicit cohort model says the orders have
    matured. Fresh demand is analysed by ads/search/conversion; realised economics is
    used as a backward-looking control and for learning.
    """

    def build(self, portfolio: dict[str, Any]) -> list[OperatingFinding]:
        out: list[OperatingFinding] = []
        own = portfolio.get("own_27") if isinstance(portfolio, dict) else None
        cohort = own.get("cohort_lifecycle") if isinstance(own, dict) and isinstance(own.get("cohort_lifecycle"), dict) else {}
        global_c = cohort.get("global") if isinstance(cohort.get("global"), dict) else {}
        buyout_lag = global_c.get("buyout_lag") if isinstance(global_c.get("buyout_lag"), dict) else {}
        recent7 = global_c.get("recent_7d") if isinstance(global_c.get("recent_7d"), dict) else {}
        p50 = _n(buyout_lag.get("p50_days"))
        p90 = _n(buyout_lag.get("p90_days"))
        maturity = _n(recent7.get("maturity_pct"))
        orders7 = _n(recent7.get("orders"))
        open7 = _n(recent7.get("open_orders"))
        if orders7 and orders7 > 0 and maturity is not None:
            out.append(OperatingFinding(
                key="cohort:fresh_orders_maturity",
                level="medium" if maturity < 80 else "low",
                title="Свежие заказы нельзя оценивать по текущим выкупам без поправки на лаг",
                conclusion=(
                    f"Заказы последних 7 дней созрели примерно на {maturity:.0f}% по фактической скорости выкупа магазина. "
                    + (f"Медианный выкуп занимает {p50:.1f} дня, 90% выкупов происходят примерно до {p90:.1f} дня. " if p50 is not None and p90 is not None else "")
                    + (f"Незавершённых заказов в этом окне: {open7:.0f}." if open7 is not None else "")
                ),
                action="Использовать свежие заказы как сигнал спроса; качество этого роста оценивать только после созревания той же когорты по SRID.",
                evidence=[
                    {"metric":"recent_7d_maturity_pct","value":maturity},
                    {"metric":"buyout_p50_days","value":p50},
                    {"metric":"buyout_p90_days","value":p90},
                    {"metric":"recent_7d_open_orders","value":open7},
                ],
            ))

        # Weekly portfolio/store/group summaries remain useful, but only as realised
        # historical outcomes. They must never be attached causally to fresh orders.
        for store in portfolio.get("stores", []) if isinstance(portfolio, dict) else []:
            if not isinstance(store, dict):
                continue
            sid = str(store.get("id") or store.get("name") or "store")
            name = str(store.get("name") or sid)
            for g in store.get("groups", []) or []:
                if not isinstance(g, dict):
                    continue
                gn = str(g.get("name") or "группа")
                profit = _n(g.get("profit_rub"))
                margin = _n(g.get("margin_pct"))
                orders_ch = _n(g.get("orders_change_pct"))
                buyouts_ch = _n(g.get("buyouts_change_pct"))
                profit_ch = _n(g.get("profit_change_pct"))
                if profit is not None and profit < 0:
                    out.append(OperatingFinding(
                        key=f"group:{sid}:{gn}:realised_negative",
                        level="high",
                        title=f"{name} · {gn}: созревший финансовый срез отрицательный",
                        conclusion=(f"В уже реализованном срезе прибыль {profit:,.0f} ₽" + (f", маржа {margin:.1f}%" if margin is not None else "") + ".").replace(",", " "),
                        action=(
                            "Использовать это как сигнал для разбора прошлых когорт, но не как автоматический запрет рекламы свежих заказов. "
                            "Для текущего решения проверить юнитку SKU, кампанию, текущий спрос и зрелость новой когорты."
                        ),
                        evidence=[
                            {"metric":"realised_profit_rub","value":profit},
                            {"metric":"realised_margin_pct","value":margin},
                            {"metric":"orders_change_pct_leading","value":orders_ch},
                            {"metric":"buyouts_change_pct_lagging","value":buyouts_ch},
                            {"metric":"profit_change_pct_lagging","value":profit_ch},
                        ],
                    ))

        # Old unresolved orders beyond the SKU-specific p90 are a real operational
        # anomaly because they should normally have reached buyout/cancel by then.
        products = own.get("products", []) if isinstance(own, dict) else []
        old_unresolved = []
        for prod in products:
            if not isinstance(prod, dict):
                continue
            n = _n(prod.get("unresolved_older_than_buyout_p90"))
            if n and n > 0:
                old_unresolved.append((prod, int(n)))
        if old_unresolved:
            old_unresolved.sort(key=lambda x: x[1], reverse=True)
            examples = ", ".join(f"{x[0].get('name') or x[0].get('sku')}: {x[1]}" for x in old_unresolved[:5])
            out.append(OperatingFinding(
                key="cohort:stale_unresolved_orders",
                level="medium",
                title="Есть заказы старше обычного срока созревания",
                conclusion=f"Часть заказов остаётся незавершённой дольше p90 выкупа. Примеры: {examples}.",
                action="Проверить статусы/логистику этих SRID отдельно; не смешивать их с качеством свежей рекламной когорты.",
                evidence=[{"metric":"unresolved_examples","value":examples}],
                confidence="medium",
            ))
        return out
