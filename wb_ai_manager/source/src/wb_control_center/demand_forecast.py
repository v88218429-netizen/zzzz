from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime
from statistics import mean, pstdev
from typing import Any, Callable


def _n(v: Any) -> float | None:
    try:
        if v is None or isinstance(v, bool):
            return None
        return float(v)
    except Exception:
        return None


def _parse_date(v: Any) -> date | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v)[:10]).date()
    except Exception:
        return None


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass
class DemandForecast:
    as_of: str | None
    recent_3d: float | None
    recent_7d: float | None
    baseline_14d: float | None
    same_weekday_avg: float | None
    order_acceleration_pct: float | None
    search_frequency_trend_pct: float | None
    forecast_orders_1d: float | None
    forecast_orders_3d: float | None
    forecast_orders_7d: float | None
    method: str
    confidence: str
    reasons: list[str]
    demand_type: str = "unknown"
    adi: float | None = None
    cv2: float | None = None
    nonzero_days: int = 0
    history_days: int = 0
    model_name: str = "fallback"
    backtest_mae: float | None = None
    backtest_bias: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _profile(values: list[float]) -> tuple[str, float | None, float | None, int]:
    nonzero = [v for v in values if v > 0]
    if not values:
        return "unknown", None, None, 0
    if not nonzero:
        return "zero", None, None, 0
    adi = len(values) / len(nonzero)
    cv2 = 0.0
    if len(nonzero) > 1 and mean(nonzero) > 0:
        cv2 = (pstdev(nonzero) / mean(nonzero)) ** 2
    if adi < 1.32 and cv2 < 0.49:
        kind = "smooth"
    elif adi >= 1.32 and cv2 < 0.49:
        kind = "intermittent"
    elif adi < 1.32 and cv2 >= 0.49:
        kind = "erratic"
    else:
        kind = "lumpy"
    return kind, round(adi, 3), round(cv2, 3), len(nonzero)


def _recent_mean(values: list[float], n: int = 7) -> float:
    x = values[-n:] if len(values) >= n else values
    return mean(x) if x else 0.0


def _ewma(values: list[float], alpha: float = 0.30) -> float:
    if not values:
        return 0.0
    level = values[0]
    for v in values[1:]:
        level = alpha * v + (1.0 - alpha) * level
    return max(0.0, level)


def _croston_sba(values: list[float], alpha: float = 0.15) -> float:
    nonzero_idx = [i for i, v in enumerate(values) if v > 0]
    if not nonzero_idx:
        return 0.0
    first = nonzero_idx[0]
    z = values[first]
    p = float(first + 1)
    last = first
    for i in nonzero_idx[1:]:
        interval = float(i - last)
        z = z + alpha * (values[i] - z)
        p = p + alpha * (interval - p)
        last = i
    return max(0.0, (1.0 - alpha / 2.0) * z / max(p, 1e-9))


def _tsb(values: list[float], alpha: float = 0.15, beta: float = 0.10) -> float:
    if not values:
        return 0.0
    nonzero = [v for v in values if v > 0]
    if not nonzero:
        return 0.0
    z = nonzero[0]
    p = 1.0 if values[0] > 0 else 0.5
    for v in values[1:]:
        occurred = 1.0 if v > 0 else 0.0
        p = p + beta * (occurred - p)
        if occurred:
            z = z + alpha * (v - z)
    return max(0.0, p * z)


def _weekday_mean(rows: list[tuple[date, float]]) -> float:
    if not rows:
        return 0.0
    next_weekday = (rows[-1][0].weekday() + 1) % 7
    vals = [v for d, v in rows if d.weekday() == next_weekday]
    return mean(vals[-6:]) if vals else _recent_mean([v for _, v in rows], 7)


def _candidate_forecasters() -> dict[str, Callable[[list[tuple[date, float]]], float]]:
    return {
        "recent_7d": lambda rows: _recent_mean([v for _, v in rows], 7),
        "ewma": lambda rows: _ewma([v for _, v in rows]),
        "weekday": _weekday_mean,
        "croston_sba": lambda rows: _croston_sba([v for _, v in rows]),
        "tsb": lambda rows: _tsb([v for _, v in rows]),
    }


def _choose_model(rows: list[tuple[date, float]], demand_type: str) -> tuple[str, float, float | None, float | None]:
    candidates = _candidate_forecasters()
    if demand_type in {"intermittent", "lumpy"}:
        preferred = ["croston_sba", "tsb", "recent_7d", "ewma", "weekday"]
    else:
        preferred = ["weekday", "ewma", "recent_7d", "croston_sba", "tsb"]

    if len(rows) < 15:
        name = preferred[0] if demand_type in {"intermittent", "lumpy"} else "recent_7d"
        return name, candidates[name](rows), None, None

    start = max(14, len(rows) - 21)
    scores: list[tuple[float, float, str]] = []
    for name in preferred:
        fn = candidates[name]
        errors: list[float] = []
        signed: list[float] = []
        for i in range(start, len(rows)):
            train = rows[:i]
            if len(train) < 7:
                continue
            pred = fn(train)
            actual = rows[i][1]
            errors.append(abs(pred - actual))
            signed.append(pred - actual)
        if errors:
            scores.append((mean(errors), abs(mean(signed)), name))
    if not scores:
        name = preferred[0]
        return name, candidates[name](rows), None, None

    scores.sort()
    mae, _, name = scores[0]
    fn = candidates[name]
    bias_values: list[float] = []
    for i in range(start, len(rows)):
        train = rows[:i]
        if len(train) >= 7:
            bias_values.append(fn(train) - rows[i][1])
    bias = mean(bias_values) if bias_values else None
    return name, fn(rows), round(mae, 3), round(bias, 3) if bias is not None else None


def build_demand_forecast(product: dict[str, Any], max_growth_pct: float = 60.0) -> DemandForecast:
    hist = product.get("orders_daily_history") if isinstance(product, dict) else None
    rows: list[tuple[date, float]] = []
    if isinstance(hist, list):
        for x in hist:
            if not isinstance(x, dict):
                continue
            d = _parse_date(x.get("date"))
            v = _n(x.get("orders"))
            if d is not None and v is not None:
                rows.append((d, max(0.0, v)))
    rows.sort(key=lambda x: x[0])
    reasons: list[str] = []

    if len(rows) < 4:
        fallback = _n(product.get("orders_per_day")) if isinstance(product, dict) else None
        order_trend = _n(product.get("orders_trend_pct")) if isinstance(product, dict) else None
        freq_trend = _n(product.get("search_frequency_trend_pct")) if isinstance(product, dict) else None
        weighted = []
        if order_trend is not None:
            order_trend = _clamp(order_trend, -max_growth_pct, max_growth_pct)
            weighted.append((order_trend, 0.70))
        if freq_trend is not None:
            freq_trend = _clamp(freq_trend, -max_growth_pct, max_growth_pct)
            weighted.append((freq_trend, 0.30))
        growth = (sum(v * w for v, w in weighted) / sum(w for _, w in weighted)) if weighted else 0.0
        forecast = (fallback * (1 + growth / 100.0)) if fallback is not None else None
        return DemandForecast(
            as_of=str(product.get("demand_history_asof") or "") or None,
            recent_3d=fallback,
            recent_7d=fallback,
            baseline_14d=None,
            same_weekday_avg=None,
            order_acceleration_pct=round(order_trend, 2) if order_trend is not None else None,
            search_frequency_trend_pct=round(freq_trend, 2) if freq_trend is not None else None,
            forecast_orders_1d=round(forecast, 3) if forecast is not None else None,
            forecast_orders_3d=round(forecast * 3, 3) if forecast is not None else None,
            forecast_orders_7d=round(forecast * 7, 3) if forecast is not None else None,
            method="fallback: текущий темп + доступные тренды",
            confidence="low",
            reasons=["Дневной истории мало; прогноз временно опирается на текущий темп и доступные тренды."],
            history_days=len(rows),
        )

    values = [v for _, v in rows]
    demand_type, adi, cv2, nonzero_days = _profile(values)
    recent3 = mean(values[-3:])
    recent7 = mean(values[-7:]) if len(values) >= 7 else mean(values)
    prior = values[:-3]
    baseline = mean(prior[-14:]) if prior else None
    acceleration = ((recent3 / baseline) - 1) * 100 if baseline and baseline > 0 else None
    if acceleration is not None:
        acceleration = _clamp(acceleration, -max_growth_pct, max_growth_pct)

    next_weekday = (rows[-1][0].weekday() + 1) % 7
    weekday_vals = [v for d, v in rows[:-1] if d.weekday() == next_weekday][-6:]
    same_weekday = mean(weekday_vals) if weekday_vals else None

    model_name, base, backtest_mae, backtest_bias = _choose_model(rows, demand_type)
    reasons.append(
        f"Тип спроса: {demand_type}; ненулевых дней {nonzero_days}/{len(rows)}"
        + (f", ADI {adi:.2f}, CV² {cv2:.2f}." if adi is not None and cv2 is not None else ".")
    )
    if backtest_mae is not None:
        reasons.append(f"Модель {model_name} выбрана по rolling-backtest; MAE ≈ {backtest_mae:.2f} заказа/день.")
    else:
        reasons.append(f"Истории недостаточно для устойчивого backtest; используется {model_name}.")

    freq_trend = _n(product.get("search_frequency_trend_pct")) if isinstance(product, dict) else None
    freq_adj = 0.0
    if freq_trend is not None:
        freq_trend = _clamp(freq_trend, -max_growth_pct, max_growth_pct)
        freq_adj = _clamp(freq_trend * 0.20, -12.0, 12.0)
        reasons.append(f"Поисковая частотность {freq_trend:+.1f}% учтена как слабый внешний сигнал, а не как заказ.")

    trend_adj = 0.0
    if acceleration is not None and demand_type in {"smooth", "erratic"}:
        trend_adj = _clamp(acceleration * 0.20, -12.0, 12.0)
        reasons.append(f"Краткосрочное ускорение заказов {acceleration:+.1f}% учтено ограниченно.")

    forecast = max(0.0, base * (1.0 + (trend_adj + freq_adj) / 100.0))
    if backtest_mae is None:
        confidence = "low" if demand_type in {"intermittent", "lumpy"} else "medium"
    elif len(rows) >= 42 and nonzero_days >= 8:
        confidence = "high" if backtest_mae <= max(1.0, recent7 * 0.6) else "medium"
    else:
        confidence = "medium" if nonzero_days >= 5 else "low"

    return DemandForecast(
        as_of=rows[-1][0].isoformat(),
        recent_3d=round(recent3, 3),
        recent_7d=round(recent7, 3),
        baseline_14d=round(baseline, 3) if baseline is not None else None,
        same_weekday_avg=round(same_weekday, 3) if same_weekday is not None else None,
        order_acceleration_pct=round(acceleration, 2) if acceleration is not None else None,
        search_frequency_trend_pct=round(freq_trend, 2) if freq_trend is not None else None,
        forecast_orders_1d=round(forecast, 3),
        forecast_orders_3d=round(forecast * 3, 3),
        forecast_orders_7d=round(forecast * 7, 3),
        method=f"adaptive model selection: {model_name}",
        confidence=confidence,
        reasons=reasons,
        demand_type=demand_type,
        adi=adi,
        cv2=cv2,
        nonzero_days=nonzero_days,
        history_days=len(rows),
        model_name=model_name,
        backtest_mae=backtest_mae,
        backtest_bias=backtest_bias,
    )
