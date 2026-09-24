from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from .models import DecisionCard


def _metric(card: DecisionCard, name: str) -> Any:
    for e in card.evidence:
        if isinstance(e, dict) and str(e.get("metric")) == name:
            return e.get("value")
    return None


def _n(v: Any) -> float | None:
    try:
        if v is None or isinstance(v, bool):
            return None
        return float(v)
    except Exception:
        return None


@dataclass
class ReviewResult:
    decision_key: str
    verdict: str
    critic: list[str]
    risk_checks: list[str]
    blockers_added: list[str]
    confidence_after: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DecisionReviewBoard:
    """Independent deterministic critic + risk controller.

    It intentionally does not reuse the DecisionEngine conclusions.  It inspects the
    evidence and prevents a money-changing recommendation from surviving when exact
    attribution, unit economics, cohort maturity or stock risk is missing/contradictory.
    """

    def __init__(self, policy: Any):
        self.policy = policy

    def review(self, cards: list[DecisionCard], portfolio: dict[str, Any], snapshots: dict[str, Any]) -> tuple[list[DecisionCard], list[ReviewResult]]:
        results: list[ReviewResult] = []
        for card in cards:
            critic: list[str] = []
            risk: list[str] = []
            added: list[str] = []
            confidence = card.confidence

            if card.scope == "campaign_sku" or card.decision_key.startswith("advert:"):
                exact = _metric(card, "sku_stats_exact")
                cur = _n(_metric(card, "current_bid_rub"))
                tgt = _n(_metric(card, "target_bid_rub"))
                drr = _n(_metric(card, "observed_drr_pct"))
                econ = _n(_metric(card, "economic_max_drr_pct"))
                stock = _n(_metric(card, "stock_days_forecast"))
                maturity = _n(_metric(card, "cohort_maturity_pct"))

                if exact is False:
                    added.append("нет точной статистики кампании по конкретному nmID")
                    critic.append("Общая статистика многотоварной кампании не доказывает эффективность конкретного товара.")
                if cur is not None and tgt is not None and tgt > cur:
                    if econ is None:
                        added.append("нет подтверждённого экономического потолка рекламы для SKU")
                        critic.append("Повышение ставки без полной юнит-экономики может купить убыточный спрос.")
                    elif drr is not None and drr > econ:
                        added.append("текущий ДРР уже выше экономического потолка SKU")
                        critic.append("Нельзя усиливать ставку, когда текущий рекламный расход уже выходит за экономику товара.")
                    min_stock = float(((self.policy.raw.get("runtime") or {}).get("advertising") or {}).get("min_stock_days_for_scale", 10) or 10)
                    if stock is not None and stock < min_stock:
                        added.append(f"прогноз запаса {stock:.1f} дн. ниже порога повышения рекламы {min_stock:.1f} дн.")
                        risk.append("Дополнительный спрос приблизит дефицит раньше, чем система подтверждает пополнение.")
                if maturity is not None and maturity < 50:
                    risk.append("Свежая когорта ещё незрелая; её текущий выкуп/прибыль нельзя использовать как доказательство качества новых заказов.")

                hard = float((self.policy.safety.get("limits") or {}).get("max_bid_change_pct", 10) or 10)
                if cur and tgt and cur > 0:
                    pct = abs(tgt / cur - 1.0) * 100
                    if pct > hard + 1e-9:
                        added.append(f"изменение ставки {pct:.1f}% превышает технический предел {hard:.1f}%")

            # Generic source/confidence guard.
            if card.blockers:
                critic.append("Исходное решение уже содержит незакрытые блокеры данных.")
            if card.confidence == "low" and any(a.get("mode") == "numeric_ad_plan" for a in card.recommended_actions if isinstance(a, dict)):
                risk.append("Низкая уверенность несовместима с автоматическим денежным действием.")

            for x in added:
                if x not in card.blockers:
                    card.blockers.append(x)

            # Any unresolved blocker makes a money-increasing recommendation non-executable.
            # This is intentionally stronger than merely blocking items added by the critic:
            # blockers discovered by the main DecisionEngine are just as important.
            has_blockers = bool(card.blockers)
            if added or has_blockers:
                confidence = "low"
                card.confidence = "low"
                frozen = False
                for action in card.recommended_actions:
                    if not isinstance(action, dict) or action.get("mode") != "numeric_ad_plan":
                        continue
                    value = action.get("value") if isinstance(action.get("value"), dict) else {}
                    cur = _n(value.get("current_bid_rub")); tgt = _n(value.get("target_bid_rub"))
                    if cur is not None and tgt is not None and tgt > cur:
                        value["target_bid_rub"] = cur
                        value["change_pct"] = 0.0
                        action["value"] = value
                        action["action"] = "Ставку не повышать, пока не закрыты блокеры данных и независимой проверки. Текущая версия ничего в кабинете WB не меняет."
                        frozen = True
                if frozen:
                    # Keep every representation of the recommendation consistent.
                    # The dashboard reads evidence while the action card reads action.value;
                    # both must show the same frozen bid/spend when a blocker exists.
                    current_spend = _n(_metric(card, "current_spend_24h_rub"))
                    for action in card.recommended_actions:
                        if not isinstance(action, dict) or action.get("mode") != "numeric_ad_plan":
                            continue
                        value = action.get("value") if isinstance(action.get("value"), dict) else {}
                        cap = _n(value.get("max_spend_24h_rub"))
                        if current_spend is not None and cap is not None and cap > current_spend:
                            value["max_spend_24h_rub"] = round(current_spend, 2)
                        action["value"] = value
                    for evidence in card.evidence:
                        if not isinstance(evidence, dict):
                            continue
                        if evidence.get("metric") == "target_bid_rub":
                            evidence["value"] = cur
                        elif evidence.get("metric") == "max_spend_next_24h_rub" and current_spend is not None:
                            cap = _n(evidence.get("value"))
                            if cap is not None and cap > current_spend:
                                evidence["value"] = round(current_spend, 2)
                        elif evidence.get("metric") == "решение":
                            evidence["value"] = "Ставку не повышать до закрытия блокеров"
                    if "повышение ставки заблокировано" not in card.title.lower():
                        card.title += " — повышение ставки заблокировано"
                    card.diagnosis += " · повышение ставки заблокировано до закрытия данных"

            verdict = "можно рекомендовать"
            if added:
                verdict = "заблокировано проверкой"
            elif card.blockers:
                verdict = "нужны данные"
            results.append(ReviewResult(card.decision_key, verdict, critic, risk, added, confidence))
            card.evidence.append({"source":"независимая проверка","metric":"review_verdict","value":verdict})
            for item in critic[:3]:
                card.evidence.append({"source":"критик","metric":"возражение","value":item})
            for item in risk[:3]:
                card.evidence.append({"source":"контроль риска","metric":"риск","value":item})
        return cards, results
