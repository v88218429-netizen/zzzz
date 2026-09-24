from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from statistics import mean, median
from typing import Any


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
        weighted=[]
        if order_trend is not None:
            order_trend=_clamp(order_trend,-max_growth_pct,max_growth_pct); weighted.append((order_trend,0.70))
        if freq_trend is not None:
            freq_trend=_clamp(freq_trend,-max_growth_pct,max_growth_pct); weighted.append((freq_trend,0.30))
        growth=(sum(v*w for v,w in weighted)/sum(w for _,w in weighted)) if weighted else 0.0
        forecast=(fallback*(1+growth/100.0)) if fallback is not None else None
        fallback_reasons=["Дневной истории пока мало; прогноз опирается на текущий темп и уже рассчитанные тренды."]
        if order_trend is not None: fallback_reasons.append(f"Темп заказов изменился примерно на {order_trend:+.1f}%.")
        if freq_trend is not None: fallback_reasons.append(f"Частотность поискового спроса изменилась примерно на {freq_trend:+.1f}%.")
        return DemandForecast(
            as_of=str(product.get("demand_history_asof") or "") or None,
            recent_3d=fallback, recent_7d=fallback, baseline_14d=None, same_weekday_avg=None,
            order_acceleration_pct=round(order_trend,2) if order_trend is not None else None,
            search_frequency_trend_pct=round(freq_trend,2) if freq_trend is not None else None,
            forecast_orders_1d=round(forecast,3) if forecast is not None else None,
            forecast_orders_3d=(round(forecast*3,3) if forecast is not None else None),
            forecast_orders_7d=(round(forecast*7,3) if forecast is not None else None),
            method="текущий темп + доступные тренды; дневная история ещё накапливается",
            confidence="low", reasons=fallback_reasons,
        )

    values=[v for _,v in rows]
    last_date=rows[-1][0]
    recent3=mean(values[-3:])
    recent7=mean(values[-7:]) if len(values)>=7 else mean(values)
    prior=values[:-3]
    baseline=mean(prior[-14:]) if prior else None
    acceleration=((recent3/baseline)-1)*100 if baseline and baseline>0 else None
    if acceleration is not None:
        acceleration=_clamp(acceleration,-max_growth_pct,max_growth_pct)

    # Day-of-week correction from previous occurrences of the next weekday.
    next_day=last_date+timedelta(days=1)
    weekday_vals=[v for d,v in rows[:-1] if d.weekday()==next_day.weekday()][-6:]
    same_weekday=mean(weekday_vals) if weekday_vals else None

    # Robust base: median of short, weekly and weekday signals. This keeps one-day
    # spikes from dominating while still reacting when several independent signals rise.
    candidates=[recent3,recent7]
    if same_weekday is not None:
        candidates.append(same_weekday)
    base=float(median(candidates))

    freq_trend=_n(product.get("search_frequency_trend_pct")) if isinstance(product, dict) else None
    freq_adj=0.0
    if freq_trend is not None:
        freq_trend=_clamp(freq_trend,-max_growth_pct,max_growth_pct)
        # Frequency is a market-demand signal, not a direct order forecast. Give it a
        # partial influence and cap its effect so a noisy query snapshot cannot dominate.
        freq_adj=_clamp(freq_trend*0.25,-15.0,15.0)
        reasons.append(f"Частотность поисковых запросов изменилась примерно на {freq_trend:+.1f}%; в прогноз вошла только четверть этого сигнала.")

    trend_adj=0.0
    if acceleration is not None:
        trend_adj=_clamp(acceleration*0.35,-20.0,20.0)
        reasons.append(f"Средний темп последних 3 дней против предыдущего окна: {acceleration:+.1f}%.")

    forecast=max(0.0, base*(1.0+(trend_adj+freq_adj)/100.0))
    if same_weekday is not None:
        reasons.append(f"Для следующего дня учтён средний спрос этого дня недели: {same_weekday:.1f} заказа/день.")
    reasons.append(f"База прогноза — медиана сигналов 3 дня / 7 дней / день недели: {base:.1f} заказа/день.")

    confidence="high" if len(rows)>=28 else "medium"
    return DemandForecast(
        as_of=last_date.isoformat(), recent_3d=round(recent3,3), recent_7d=round(recent7,3),
        baseline_14d=round(baseline,3) if baseline is not None else None,
        same_weekday_avg=round(same_weekday,3) if same_weekday is not None else None,
        order_acceleration_pct=round(acceleration,2) if acceleration is not None else None,
        search_frequency_trend_pct=round(freq_trend,2) if freq_trend is not None else None,
        forecast_orders_1d=round(forecast,3), forecast_orders_3d=round(forecast*3,3),
        forecast_orders_7d=round(forecast*7,3), method="робастный прогноз: 3д + 7д + день недели + частотность",
        confidence=confidence, reasons=reasons,
    )
