from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from .models import DecisionCard


def _emap(card: DecisionCard) -> dict[str, Any]:
    out={}
    for e in card.evidence:
        if isinstance(e,dict) and e.get("metric") is not None:
            out[str(e.get("metric"))]=e.get("value")
    return out


def _n(v: Any) -> float | None:
    try:
        if v is None or isinstance(v,bool): return None
        return float(v)
    except Exception: return None


@dataclass
class Investigation:
    decision_key: str
    entity_id: str
    status: str
    checks: list[dict[str, Any]]
    hypotheses: list[str]
    missing: list[str]

    def to_dict(self) -> dict[str, Any]: return asdict(self)


class InvestigationEngine:
    """Build an explicit investigation checklist before high-impact recommendations."""

    def build(self, cards: list[DecisionCard]) -> list[Investigation]:
        out=[]
        for card in cards:
            if card.priority not in {"high","critical"} and not card.decision_key.startswith("advert:"):
                continue
            e=_emap(card); checks=[]; hyp=[]; missing=[]
            if card.decision_key.startswith("advert:"):
                exact=e.get("sku_stats_exact")
                checks.append({"check":"атрибуция кампании к SKU","ok":exact is not False,"value":exact})
                if exact is False: missing.append("точная статистика кампании по nmID")
                econ=_n(e.get("economic_max_drr_pct")); drr=_n(e.get("observed_drr_pct"))
                checks.append({"check":"юнит-экономика рекламы","ok":econ is not None,"value":econ})
                if econ is None: missing.append("экономический потолок ДРР SKU")
                elif drr is not None and drr>econ: hyp.append("рекламный расход выше экономически допустимого уровня")
                stock=_n(e.get("stock_days_forecast")); checks.append({"check":"запас по прогнозному спросу","ok":stock is not None,"value":stock})
                if stock is None: missing.append("прогноз покрытия запасом")
                pos=_n(e.get("search_position")); delta=_n(e.get("search_position_delta"))
                checks.append({"check":"поисковая позиция","ok":pos is not None,"value":pos})
                if delta is not None and delta>0: hyp.append("видимость в поиске ухудшается")
                tr=_n(e.get("orders_trend_pct")); traffic=_n(e.get("traffic_trend_pct"))
                if tr is not None and tr<0 and traffic is not None and traffic<0: hyp.append("падение заказов сопровождается падением рекламного трафика")
                maturity=_n(e.get("cohort_maturity_pct"))
                checks.append({"check":"зрелость свежей когорты","ok":maturity is not None,"value":maturity})
            elif ":stockout" in card.decision_key:
                checks.append({"check":"прогноз спроса","ok":_n(e.get("forecast_orders_per_day")) is not None,"value":e.get("forecast_orders_per_day")})
                checks.append({"check":"безопасный остаток","ok":_n(e.get("safe_stock")) is not None,"value":e.get("safe_stock")})
                if _n(e.get("profit_rub")) is not None and _n(e.get("profit_rub")) < 0: hyp.append("пополнение полного горизонта масштабирует отрицательную юнитку")
            elif card.decision_key.startswith("content:"):
                checks.append({"check":"достаточная посещаемость карточки","ok":(_n(e.get("open_card_count")) or 0)>=200,"value":e.get("open_card_count")})
                checks.append({"check":"фотогалерея измерена","ok":_n(e.get("media_count")) is not None,"value":e.get("media_count")})
                hyp.append("контент может быть одним из факторов слабой конверсии; нужна проверка А/Б, а не причинный вывод")

            for b in card.blockers:
                if b not in missing: missing.append(b)
            status="полное" if not missing else "частичное"
            out.append(Investigation(card.decision_key,card.entity_id,status,checks,hyp,missing))
        return out
