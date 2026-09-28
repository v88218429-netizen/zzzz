from __future__ import annotations

from collections import Counter
from typing import Any


_PRIORITY = {"low": 0, "medium": 1, "high": 2, "critical": 3}


_GROUPS: dict[str, dict[str, str]] = {
    "economics": {
        "title": "Экономика товара",
        "question": "Где экономика действительно отрицательная, где она только выглядит отрицательной из-за качества исходных данных и что именно съедает прибыль?",
        "meaning": "Все сигналы по юнит-экономике, тонкой марже и прошлой реализованной экономике сведены в один исследовательский кейс.",
    },
    "inventory_excess": {
        "title": "Избыточный запас и замороженный капитал",
        "question": "Где запас существенно выше подтверждённого спроса и сколько денег можно высвободить без демпинга?",
        "meaning": "Не отдельные уведомления по каждому SKU, а единая задача по перераспределению капитала и остановке лишнего пополнения.",
    },
    "inventory_shortage": {
        "title": "Риск дефицита и потерянных продаж",
        "question": "Какие товары могут закончиться раньше следующего пополнения и сколько нужно довезти с учётом реального спроса и lead time?",
        "meaning": "Дефициты объединены в одну очередь снабжения, а не показаны десятками независимых тревог.",
    },
    "advertising": {
        "title": "Реклама и экономика",
        "question": "Где реклама масштабирует убыток или требует изменения ставки, а где лучше ничего не менять?",
        "meaning": "Активные рекламные решения отделены от пассивного статуса «оставить как есть».",
    },
    "fulfillment": {
        "title": "FBS и исполнение заказов",
        "question": "Где операционный сбой может привести к потере заказа, неверному списанию или повторной отгрузке?",
        "meaning": "Операционные исключения FBS сведены в один кейс исполнения.",
    },
    "data_quality": {
        "title": "Качество данных и источников",
        "question": "Какие источники мешают делать надёжные денежные выводы и что нужно восстановить в первую очередь?",
        "meaning": "Технические дыры данных не смешиваются с бизнес-решениями по товарам.",
    },
    "other": {
        "title": "Прочие решения",
        "question": "Какие действия не попали в основные причинные контуры?",
        "meaning": "Резервная группа для новых типов решений до их явной классификации.",
    },
}


def _group_id(row: dict[str, Any]) -> str:
    key = str(row.get("decision_key") or "")
    if key == "data:source_quality":
        return "data_quality"
    if ":negative_unit" in key or ":thin_margin_ads" in key or ":negative_groups" in key or ":ads_economics" in key:
        return "economics"
    if ":overstock" in key:
        return "inventory_excess"
    if ":stockout" in key:
        return "inventory_shortage"
    if key.startswith("advert:"):
        return "advertising"
    if "reshipment" in key or key.startswith("fbs:"):
        return "fulfillment"
    return "other"


def _is_passive(row: dict[str, Any]) -> bool:
    key = str(row.get("decision_key") or "")
    if not key.startswith("advert:"):
        return False
    text = " ".join(
        [
            str(row.get("title") or ""),
            str(row.get("diagnosis") or ""),
            " ".join(str(x.get("action") or "") for x in (row.get("recommended_actions") or []) if isinstance(x, dict)),
        ]
    ).lower()
    return "оставить без изменения" in text or "оставить ставку" in text or "ничего не менять" in text


def _portfolio_index(portfolio: dict[str, Any]) -> dict[str, dict[str, Any]]:
    own = (portfolio or {}).get("own_27") or {}
    rows = own.get("products") or []
    return {str(x.get("sku")): x for x in rows if isinstance(x, dict) and x.get("sku") is not None}


def _money_from_evidence(row: dict[str, Any], metric: str) -> float:
    total = 0.0
    for e in row.get("evidence") or []:
        if not isinstance(e, dict) or str(e.get("metric")) != metric:
            continue
        try:
            total += float(e.get("value") or 0)
        except Exception:
            pass
    return total


def group_decisions(decisions: list[dict[str, Any]], portfolio: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    products = _portfolio_index(portfolio or {})
    buckets: dict[str, list[dict[str, Any]]] = {}

    for row in decisions or []:
        if not isinstance(row, dict):
            continue
        gid = _group_id(row)
        buckets.setdefault(gid, []).append(row)

    result: list[dict[str, Any]] = []
    for gid, rows in buckets.items():
        meta = _GROUPS[gid]
        active = [x for x in rows if not _is_passive(x)]
        passive = [x for x in rows if _is_passive(x)]
        if not active and gid == "advertising":
            # "Hold" is useful context, not an operator task.
            continue

        priority = max((str(x.get("priority") or "low") for x in active or rows), key=lambda p: _PRIORITY.get(p, 0))
        conf = Counter(str(x.get("confidence") or "unknown") for x in active or rows)
        entities = []
        categories = Counter()
        verified = 0
        needs_review = 0
        for row in active:
            entity = str(row.get("entity_id") or "")
            if entity and entity not in entities:
                entities.append(entity)
            product = products.get(entity) or {}
            category = str(product.get("category_name") or product.get("group_name") or "").strip()
            if category:
                categories[category] += 1
            if row.get("confidence") == "low" or row.get("blockers"):
                needs_review += 1
            else:
                verified += 1

        frozen = sum(_money_from_evidence(x, "frozen_capital_rub") for x in active)
        result.append(
            {
                "group_id": gid,
                "title": meta["title"],
                "question": meta["question"],
                "meaning": meta["meaning"],
                "priority": priority,
                "decision_count": len(active),
                "passive_count": len(passive),
                "affected_count": len(entities),
                "affected_entities": entities,
                "confidence": dict(conf),
                "verified_count": verified,
                "needs_review_count": needs_review,
                "top_categories": [{"name": k, "count": v} for k, v in categories.most_common(6)],
                "frozen_capital_rub": round(frozen, 2) if frozen else None,
                "decision_keys": [str(x.get("decision_key") or "") for x in active],
                "examples": [
                    {
                        "decision_key": x.get("decision_key"),
                        "entity_id": x.get("entity_id"),
                        "title": x.get("title"),
                        "priority": x.get("priority"),
                        "confidence": x.get("confidence"),
                    }
                    for x in sorted(active, key=lambda r: -_PRIORITY.get(str(r.get("priority") or "low"), 0))[:6]
                ],
            }
        )

    result.sort(key=lambda g: (-_PRIORITY.get(g["priority"], 0), -int(g["decision_count"])))
    return result


def decision_group_summary(groups: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "groups": len(groups),
        "active_decisions": sum(int(x.get("decision_count") or 0) for x in groups),
        "passive_hidden": sum(int(x.get("passive_count") or 0) for x in groups),
        "critical_groups": sum(1 for x in groups if x.get("priority") == "critical"),
        "high_groups": sum(1 for x in groups if x.get("priority") == "high"),
    }
