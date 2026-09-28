from wb_control_center.decision_groups import decision_group_summary, group_decisions


def d(key, title, priority="high", confidence="high", entity="1", evidence=None, blockers=None):
    return {
        "decision_key": key,
        "title": title,
        "priority": priority,
        "confidence": confidence,
        "entity_id": entity,
        "evidence": evidence or [],
        "blockers": blockers or [],
        "recommended_actions": [],
    }


def test_groups_many_cards_into_few_meanings():
    rows = [
        d("sku:1:negative_unit", "A: расчётная юнитка отрицательная", "critical"),
        d("sku:2:negative_unit", "B: юнитка показывает убыток, но требует проверки", blockers=["нет логистики"], entity="2"),
        d("sku:3:overstock", "C: подтверждён избыточный запас", "medium", entity="3",
          evidence=[{"metric":"frozen_capital_rub","value":15000}]),
        d("sku:4:stockout", "D: подтверждён риск дефицита", "critical", entity="4"),
        d("fbs:reshipment", "FBS", "high", entity="fbs"),
    ]
    portfolio={"own_27":{"products":[
        {"sku":"1","category_name":"Кашпо"},
        {"sku":"2","category_name":"Кашпо"},
        {"sku":"3","category_name":"Ветошь"},
        {"sku":"4","category_name":"Бидоны"},
    ]}}
    groups=group_decisions(rows, portfolio)
    assert len(groups) == 4
    econ=next(x for x in groups if x["group_id"]=="economics")
    assert econ["decision_count"] == 2
    assert econ["affected_count"] == 2
    assert econ["needs_review_count"] == 1
    excess=next(x for x in groups if x["group_id"]=="inventory_excess")
    assert excess["frozen_capital_rub"] == 15000


def test_hold_advertising_status_is_not_operator_task():
    rows=[
        d("advert:1:100:numeric_control", "Ставку оставить без изменения", "medium"),
        d("advert:2:200:numeric_control", "Ставку снизить", "high", entity="200"),
    ]
    rows[0]["recommended_actions"]=[{"action":"Оставить ставку без изменения"}]
    rows[1]["recommended_actions"]=[{"action":"Снизить ставку на 10%"}]
    groups=group_decisions(rows, {})
    ads=next(x for x in groups if x["group_id"]=="advertising")
    assert ads["decision_count"] == 1
    assert ads["passive_count"] == 1
    assert ads["decision_keys"] == ["advert:2:200:numeric_control"]
    summary=decision_group_summary(groups)
    assert summary["passive_hidden"] == 1
