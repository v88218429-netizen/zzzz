from wb_control_center.research_engine import build_research_agenda


def test_research_agenda_turns_groups_into_hypothesis_programs():
    groups=[
        {
            "group_id":"economics","title":"Экономика товара","question":"Почему экономика отрицательная?",
            "priority":"critical","decision_count":8,"affected_count":7,"needs_review_count":3,
            "frozen_capital_rub":None,
        },
        {
            "group_id":"inventory_excess","title":"Избыточный запас","question":"Где лишний капитал?",
            "priority":"medium","decision_count":12,"affected_count":12,"needs_review_count":0,
            "frozen_capital_rub":120000,
        },
    ]
    out=build_research_agenda(groups,{"products_total":259},{"datasets_degraded":4})
    by={x["program_id"]:x for x in out["programs"]}
    assert "research:economics" in by
    assert len(by["research:economics"]["hypotheses"]) >= 2
    assert len(by["research:economics"]["tests"]) >= 2
    assert "research:cross_sku_discovery" in by
    assert "research:information_gain" in by
    assert "стартовая модель мира" in out["principle"]


def test_information_gain_program_only_when_data_gaps_exist():
    out=build_research_agenda([],{"products_total":10},{"datasets_degraded":0})
    ids={x["program_id"] for x in out["programs"]}
    assert "research:information_gain" not in ids
    assert "research:cross_sku_discovery" not in ids
