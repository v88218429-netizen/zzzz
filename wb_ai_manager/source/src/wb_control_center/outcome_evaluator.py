from __future__ import annotations

from typing import Any


def _ev_map(payload: dict[str, Any]) -> dict[str, Any]:
    out={}
    for e in payload.get("evidence",[]) or []:
        if isinstance(e,dict) and e.get("metric") is not None:
            out[str(e.get("metric"))]=e.get("value")
    return out


def _n(v: Any) -> float | None:
    try:
        if v is None or isinstance(v,bool): return None
        return float(v)
    except Exception: return None


class OutcomeEvaluator:
    """Evaluate observed outcomes without claiming causality that the data cannot prove."""
    def __init__(self, db: Any): self.db=db

    def evaluate_due(self, current_cards: list[Any]) -> list[dict[str, Any]]:
        current={c.decision_key:c for c in current_cards}
        # A decision can legitimately change its key while remaining about the same
        # real-world entity (for example numeric_control -> zero_orders).  Evaluating
        # only by decision_key would silently lose the post-action measurements.
        by_entity={}
        for c in current_cards:
            by_entity.setdefault(str(c.entity_id), c)
        done=[]
        for row in self.db.due_evaluations(limit=200):
            old=_ev_map(row.get("payload") or {})
            card=current.get(row.get("decision_key")) or by_entity.get(str(row.get("entity_id") or ""))
            now=_ev_map({"evidence":card.evidence}) if card else {}
            horizon=str(row.get("horizon") or "")
            metrics={}
            for k in ("observed_drr_pct","orders_trend_pct","traffic_trend_pct","stock_days_forecast","cohort_maturity_pct","search_position"):
                a=_n(old.get(k)); b=_n(now.get(k))
                if a is not None or b is not None: metrics[k]={"before":a,"now":b}
            maturity=_n(now.get("cohort_maturity_pct"))
            verdict="наблюдение"
            notes="Рекомендация была замечена как соблюдённая; это оценка последующих метрик, а не доказанная причинность."
            interventions=[]
            if row.get("applied_at") and hasattr(self.db,"observed_changes_since"):
                interventions=self.db.observed_changes_since(str(row.get("entity_id") or ""), str(row.get("applied_at")))
            if interventions:
                verdict="оценка прервана новым изменением"
                notes="После начала контрольного окна объект снова изменился, поэтому результат нельзя приписывать исходному решению."
                metrics["interventions"]=[{"type":x.get("change_type"),"at":x.get("observed_at"),"new":x.get("new")} for x in interventions[:10]]
            elif horizon in {"7д","14д"}:
                if maturity is None or maturity < 80:
                    verdict="когорта ещё не созрела"
                    notes="Финансовый итог не оценивается, пока соответствующая когорта не созрела минимум на 80%."
                else:
                    verdict="созревший результат требует сверки прибыли"
                    notes="Когорта достаточно зрелая; результат можно использовать для обучения только вместе с фактической прибылью/выкупом этой же когорты."
            else:
                old_drr=_n(old.get("observed_drr_pct")); now_drr=_n(now.get("observed_drr_pct"))
                old_pos=_n(old.get("search_position")); now_pos=_n(now.get("search_position"))
                better=0; worse=0
                if old_drr is not None and now_drr is not None:
                    better += now_drr < old_drr; worse += now_drr > old_drr
                if old_pos is not None and now_pos is not None:
                    better += now_pos < old_pos; worse += now_pos > old_pos
                if better>worse: verdict="ведущие сигналы улучшились"
                elif worse>better: verdict="ведущие сигналы ухудшились"
            self.db.complete_evaluation(int(row["id"]), verdict, metrics, notes)
            done.append({"evaluation_id":row["id"],"decision_key":row["decision_key"],"horizon":horizon,"verdict":verdict})
        return done
