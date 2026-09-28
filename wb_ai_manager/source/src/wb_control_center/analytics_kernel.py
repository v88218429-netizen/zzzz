from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .demand_forecast import DemandForecast, build_demand_forecast


def _n(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except Exception:
        return None


@dataclass
class QualityAssessment:
    score: int
    level: str
    ready: bool
    issues: list[str]
    missing: list[str]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _level(score: int) -> str:
    if score >= 85:
        return "high"
    if score >= 60:
        return "medium"
    return "low"


def economics_quality(product: dict[str, Any]) -> QualityAssessment:
    score = 100
    issues: list[str] = []
    missing: list[str] = []
    notes: list[str] = []

    required = {
        "price_rub": "цена WB",
        "cost_rub": "себестоимость",
        "profit_rub": "расчётная прибыль",
        "margin_pct": "расчётная маржа",
        "commission_pct": "комиссия WB",
        "tax_total_rub": "налоги",
    }
    supporting = {
        "logistics_total_rub": "логистика",
        "drr_pct": "ДРР",
        "historical_buyout_pct": "исторический выкуп",
        "price_client_rub": "цена клиента",
    }
    for key, label in required.items():
        if _n(product.get(key)) is None:
            missing.append(label)
            score -= 16
    for key, label in supporting.items():
        if _n(product.get(key)) is None:
            missing.append(label)
            score -= 7

    price = _n(product.get("price_rub"))
    client_price = _n(product.get("price_client_rub"))
    cost = _n(product.get("cost_rub"))
    profit = _n(product.get("profit_rub"))
    margin = _n(product.get("margin_pct"))

    if price is not None and price <= 0:
        issues.append("цена WB неположительная")
        score -= 30
    if cost is not None and cost < 0:
        issues.append("себестоимость отрицательная")
        score -= 30
    if price is not None and client_price is not None and client_price > price * 1.15:
        issues.append("цена клиента существенно выше цены WB — проверить смысл полей")
        score -= 10
    if price and profit is not None and margin is not None:
        implied = profit / price * 100.0
        if abs(implied - margin) > 2.0:
            issues.append(f"прибыль и маржа не сходятся: {implied:.1f}% против {margin:.1f}%")
            score -= 15

    logistics = _n(product.get("logistics_total_rub"))
    if profit is not None and logistics is None:
        issues.append("прибыль есть, но отдельная логистика отсутствует — формулу нельзя независимо проверить")
    drr = _n(product.get("drr_pct"))
    if profit is not None and drr is None:
        notes.append("ДРР отсутствует: вывод о базовой экономике возможен, рекламное решение — нет")

    score = max(0, min(100, score))
    ready = all(_n(product.get(k)) is not None for k in required) and logistics is not None
    return QualityAssessment(score, _level(score), ready, issues, missing, notes)


def demand_quality(product: dict[str, Any], forecast: DemandForecast | None = None) -> QualityAssessment:
    forecast = forecast or build_demand_forecast(product)
    score = 100
    issues: list[str] = []
    missing: list[str] = []
    notes: list[str] = []

    current = _n(product.get("orders_per_day"))
    predicted = _n(forecast.forecast_orders_1d)
    if predicted is None:
        missing.append("прогноз спроса")
        score -= 45
    if forecast.history_days < 14:
        issues.append(f"короткая дневная история: {forecast.history_days} дней")
        score -= 25
    if forecast.confidence == "low":
        issues.append("низкая статистическая уверенность прогноза")
        score -= 20
    if forecast.backtest_mae is None and forecast.history_days >= 14:
        issues.append("нет устойчивой ошибки rolling-backtest")
        score -= 10

    ratio = None
    if current is not None and predicted is not None and current > 0 and predicted > 0:
        ratio = max(current / predicted, predicted / current)
        if ratio >= 3:
            issues.append(f"источники темпа расходятся в {ratio:.1f}×: сводная {current:.2f}/день, модель {predicted:.2f}/день")
            score -= 30
    elif current is not None and current > 0 and predicted == 0:
        issues.append(f"сводная показывает {current:.2f}/день, модель — 0; количественное решение заблокировано")
        score -= 35

    if forecast.demand_type in {"intermittent", "lumpy"}:
        notes.append(f"спрос {forecast.demand_type}: дни покрытия нельзя трактовать как точный календарный срок")
    if forecast.backtest_mae is not None:
        notes.append(f"rolling MAE {forecast.backtest_mae:.2f} заказа/день; модель {forecast.model_name}")

    score = max(0, min(100, score))
    source_conflict = any("расходятся" in x or "заблокировано" in x for x in issues)
    ready = predicted is not None and forecast.history_days >= 14 and forecast.confidence != "low" and not source_conflict
    return QualityAssessment(score, _level(score), ready, issues, missing, notes)


def _snapshot_error(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("error", "errorText"):
        value = payload.get(key)
        if value:
            return str(value)
    text = payload.get("text")
    if isinstance(text, str) and text.strip().lower().startswith(("ошибка", "error")):
        return text.strip()
    if payload.get("wb_method_disabled"):
        return str(payload.get("detail") or payload.get("message") or "Метод WB временно недоступен")
    return None


def snapshot_quality(snapshots: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    total = 0
    healthy = 0
    for agent, group in (snapshots or {}).items():
        if str(agent).startswith("_") or not isinstance(group, dict):
            continue
        for key, wrapper in group.items():
            if not isinstance(wrapper, dict):
                continue
            total += 1
            payload = wrapper.get("data") if "data" in wrapper and "created_at" in wrapper else wrapper
            error = _snapshot_error(payload)
            if error:
                issues.append({
                    "agent": str(agent),
                    "dataset": str(key),
                    "status": "degraded",
                    "message": error[:300],
                    "created_at": wrapper.get("created_at"),
                })
            else:
                healthy += 1
    return {
        "datasets_total": total,
        "datasets_healthy": healthy,
        "datasets_degraded": len(issues),
        "issues": issues,
        "status": "healthy" if not issues else "degraded",
    }


def portfolio_quality(portfolio: dict[str, Any]) -> dict[str, Any]:
    products = ((portfolio.get("own_27") or {}).get("products") or []) if isinstance(portfolio, dict) else []
    econ_ready = 0
    demand_ready = 0
    types: dict[str, int] = {}
    conflicts = 0
    negative = 0
    for product in products:
        if not isinstance(product, dict):
            continue
        eq = economics_quality(product)
        if eq.ready:
            econ_ready += 1
        if (_n(product.get("profit_rub")) or 0) < 0:
            negative += 1
        fc = build_demand_forecast(product)
        dq = demand_quality(product, fc)
        if dq.ready:
            demand_ready += 1
        if any("расходятся" in x or "заблокировано" in x for x in dq.issues):
            conflicts += 1
        types[fc.demand_type] = types.get(fc.demand_type, 0) + 1
    total = len(products)
    return {
        "products_total": total,
        "economics_decision_ready": econ_ready,
        "economics_not_ready": max(0, total - econ_ready),
        "negative_unit_signals": negative,
        "demand_decision_ready": demand_ready,
        "demand_not_ready": max(0, total - demand_ready),
        "demand_signal_conflicts": conflicts,
        "demand_types": types,
    }


def measurement_contract() -> dict[str, Any]:
    return {
        "state": "состояние в конкретный момент: цена, остаток, ставка, позиция",
        "flow": "поток за интервал: показы, клики, корзины, заказы, расход",
        "cohort": "исходы одной и той же когорты заказов: выкуп, отмена, возврат",
        "realized": "созревший финансовый факт из отчёта реализации",
        "forecast": "оценка будущего с моделью, ошибкой backtest и уровнем доверия",
        "event": "изменение управляющей переменной: цена, ставка, карточка, поставка",
    }
