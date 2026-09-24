from pathlib import Path

from wb_control_center.metrics import extract_ad_nm_metrics
from wb_control_center.config import load_policy
from wb_control_center.decision_engine import DecisionEngine
from wb_control_center.decision_review import DecisionReviewBoard
from wb_control_center.models import DecisionCard
from wb_control_center.db import Database
from wb_control_center.change_tracker import ChangeTracker


def wrapped(data):
    return {"created_at":"2026-09-22T00:00:00+00:00","data":data}


def evmap(card):
    return {x.get("metric"):x.get("value") for x in card.evidence if isinstance(x,dict)}


def test_extract_exact_advert_nm_stats_from_nested_days():
    payload=[{
        "advertId":10,
        "days":[
            {"date":"2026-09-20","apps":[
                {"nmId":111,"sum":100,"orders":2,"sum_price":2000,"views":1000,"clicks":50},
                {"nmId":222,"sum":40,"orders":1,"sum_price":900,"views":500,"clicks":20},
            ]},
            {"date":"2026-09-21","apps":[
                {"nmId":111,"sum":120,"orders":3,"sum_price":3000,"views":1200,"clicks":60},
                {"nmId":222,"sum":60,"orders":0,"sum_price":0,"views":600,"clicks":22},
            ]},
        ],
    }]
    rows=extract_ad_nm_metrics(payload)
    assert rows[(10,111)]["sum"] == 220
    assert rows[(10,111)]["orders"] == 5
    assert rows[(10,222)]["sum"] == 100
    assert len(rows[(10,111)]["days"]) == 2


def test_multisku_campaign_uses_exact_sku_stats_and_separate_keys():
    p={"source_health":[],"stores":[],"own_27":{"products":[
        {"sku":"111","name":"A","price_rub":1000,"profit_rub":200,"drr_pct":5,"safe_stock":300,"orders_per_day":10},
        {"sku":"222","name":"B","price_rub":900,"profit_rub":150,"drr_pct":5,"safe_stock":300,"orders_per_day":10},
    ]}}
    deep={"campaigns":[{
        "campaign_id":10,
        "campaign":{"advertId":10,"name":"multi","nmIds":[111,222],"nmBids":[{"nmId":111,"bidKopecks":5000},{"nmId":222,"bidKopecks":6000}]},
        "nm_ids":[111,222],
        "stats":{"advertId":10,"sum":9999,"orders":999,"sum_price":999999},
        "stats_by_nm":{
            "111":{"advertId":10,"nmId":111,"sum":100,"orders":2,"sum_price":2000,"clicks":50,"views":1000},
            "222":{"advertId":10,"nmId":222,"sum":500,"orders":1,"sum_price":900,"clicks":80,"views":1200},
        },
        "recommendations":{},"budget":{},
    }]}
    cards=DecisionEngine(load_policy()).build(p,{"advertising_optimizer":{"deep_scan":wrapped(deep)}})
    keys={c.decision_key for c in cards}
    assert any(k.startswith("advert:10:111:") for k in keys)
    assert any(k.startswith("advert:10:222:") for k in keys)
    c=next(c for c in cards if c.decision_key.startswith("advert:10:111:"))
    assert evmap(c)["observed_drr_pct"] == 5.0
    assert evmap(c)["sku_stats_exact"] is True


def test_multisku_without_exact_stats_freezes_bid():
    p={"source_health":[],"stores":[],"own_27":{"products":[{"sku":"111","name":"A","price_rub":1000,"profit_rub":200,"drr_pct":5,"safe_stock":300,"orders_per_day":10}]}}
    deep={"campaigns":[{
        "campaign_id":10,"campaign":{"advertId":10,"name":"multi","nmIds":[111,222],"bidKopecks":5000},
        "nm_ids":[111,222],"stats":{"advertId":10,"sum":1000,"orders":20,"sum_price":20000},"stats_by_nm":{},"recommendations":{},"budget":{},
    }]}
    cards=DecisionEngine(load_policy()).build(p,{"advertising_optimizer":{"deep_scan":wrapped(deep)}})
    c=next(c for c in cards if c.decision_key.startswith("advert:10:111:"))
    assert any("отдельную статистику" in b for b in c.blockers)
    action=next(a for a in c.recommended_actions if a.get("mode")=="numeric_ad_plan")
    assert action["value"]["target_bid_rub"] == action["value"]["current_bid_rub"]


def test_decision_review_blocks_bid_increase_without_economics():
    c=DecisionCard(
        decision_key="advert:10:111:numeric_control", scope="campaign_sku", entity_id="10:111",
        title="x",diagnosis="x",confidence="high",
        recommended_actions=[{"mode":"numeric_ad_plan","action":"повысить","value":{"current_bid_rub":50,"target_bid_rub":55,"change_pct":10}}],
        evidence=[
            {"metric":"sku_stats_exact","value":True}, {"metric":"current_bid_rub","value":50},
            {"metric":"target_bid_rub","value":55}, {"metric":"stock_days_forecast","value":20},
        ],
    )
    cards,reviews=DecisionReviewBoard(load_policy()).review([c],{}, {})
    assert cards[0].confidence == "low"
    assert any("экономического потолка" in b for b in cards[0].blockers)
    assert cards[0].recommended_actions[0]["value"]["target_bid_rub"] == 50
    assert reviews[0].verdict == "заблокировано проверкой"


def test_decision_history_deduplicates_same_version(tmp_path: Path):
    db=Database(tmp_path/"x.sqlite3")
    c=DecisionCard(decision_key="k",scope="sku",entity_id="1",title="t",diagnosis="d",recommended_actions=[{"action":"a"}])
    db.sync_decision_history([c],{"k":{"x":1}})
    db.sync_decision_history([c],{"k":{"x":2}})
    rows=db.decision_history()
    assert len(rows)==1


def test_change_tracker_links_observed_bid_change_to_latest_decision(tmp_path: Path):
    db=Database(tmp_path/"x.sqlite3")
    c=DecisionCard(decision_key="advert:10:111:numeric_control",scope="campaign_sku",entity_id="10:111",title="t",diagnosis="d",recommended_actions=[{"mode":"numeric_ad_plan","value":{"current_bid_rub":50,"target_bid_rub":55}}])
    db.sync_decision_history([c],{})
    tr=ChangeTracker(db)
    s1={"advertising_optimizer":{"deep_scan":{"data":{"campaigns":[{"campaign_id":10,"campaign":{"advertId":10,"nmIds":[111],"bidKopecks":5000},"nm_ids":[111]}]}}}}
    s2={"advertising_optimizer":{"deep_scan":{"data":{"campaigns":[{"campaign_id":10,"campaign":{"advertId":10,"nmIds":[111],"bidKopecks":5500},"nm_ids":[111]}]}}}}
    assert tr.sync(s1)==[]
    ch=tr.sync(s2)
    assert len(ch)==1 and ch[0]["change_type"]=="advert_bid"
    assert db.decision_history()[0]["status"]=="observed_applied"


def test_card_gallery_is_hypothesis_not_causal_claim():
    snaps={
        "cards":{"card_catalog":wrapped({"cards":[
            {"nmID":1,"title":"weak","mediaFiles":[1,2,3]},
            {"nmID":2,"title":"strong","mediaFiles":list(range(12))},
        ]})},
        "funnel":{"funnel_7d":wrapped({"items":[
            {"nmID":1,"openCardCount":1000,"conversions":{"addToCartPercent":3}},
            {"nmID":2,"openCardCount":1000,"conversions":{"addToCartPercent":12}},
        ]})},
    }
    cards=DecisionEngine(load_policy()).build({"source_health":[],"stores":[],"own_27":{"products":[]}},snaps)
    c=next(c for c in cards if c.decision_key=="content:1:gallery_experiment")
    assert "корреляция" in c.diagnosis.lower()
    assert any(a.get("mode")=="experiment" for a in c.recommended_actions)


def test_unconfirmed_planned_incoming_not_counted_as_stock():
    p={"source_health":[],"stores":[],"own_27":{"products":[{
        "sku":"1","name":"SKU","safe_stock":10,"safe_stock_source":"K2 SAFE","orders_per_day":10,
        "orders_daily_history":[{"date":f"2026-09-{d:02d}","orders":10} for d in range(1,21)],
        "planned_incoming_qty":100,"planned_incoming_confirmed":False,"profit_rub":50,"margin_pct":10,
    }]}}
    cards=DecisionEngine(load_policy()).build(p,{})
    c=next(c for c in cards if c.decision_key=="sku:1:stockout")
    assert "не считается доступным" in c.diagnosis


def test_ad_evidence_contract_matches_dashboard_keys():
    p={"source_health":[],"stores":[],"own_27":{"products":[{"sku":"111","name":"A","price_rub":1000,"profit_rub":200,"drr_pct":5,"safe_stock":300,"orders_per_day":10}]}}
    deep={"campaigns":[{"campaign_id":10,"campaign":{"advertId":10,"nmIds":[111],"bidKopecks":5000},"nm_ids":[111],"stats":{"advertId":10,"sum":100,"orders":2,"sum_price":2000,"clicks":50,"views":1000},"stats_by_nm":{},"recommendations":{},"budget":{}}]}
    cards=DecisionEngine(load_policy()).build(p,{"advertising_optimizer":{"deep_scan":wrapped(deep)}})
    c=next(c for c in cards if c.decision_key.startswith("advert:10:111:"))
    ev=evmap(c)
    assert "target_bid_rub" in ev
    assert "max_spend_next_24h_rub" in ev
    assert "target_drr_pct" in ev

from wb_control_center.investigation import InvestigationEngine
from wb_control_center.model_validation import DemandModelValidator


def test_investigation_marks_missing_ad_economics():
    c=DecisionCard(
        decision_key="advert:10:111:numeric_control",scope="campaign_sku",entity_id="10:111",
        title="t",diagnosis="d",priority="high",confidence="medium",recommended_actions=[],
        evidence=[{"metric":"sku_stats_exact","value":True},{"metric":"stock_days_forecast","value":20}],
    )
    inv=InvestigationEngine().build([c])[0]
    assert inv.status=="частичное"
    assert "экономический потолок ДРР SKU" in inv.missing


def test_demand_model_backtest_constant_series_is_accurate():
    hist=[{"date":f"2026-08-{d:02d}","orders":10} for d in range(1,29)] + [{"date":f"2026-09-{d:02d}","orders":10} for d in range(1,21)]
    p={"own_27":{"products":[{"sku":"1","name":"x","orders_per_day":10,"orders_daily_history":hist}]}}
    r=DemandModelValidator().validate(p,max_windows=7)
    assert r["samples"]>0
    assert r["wape_pct"]==0.0


def test_existing_blocker_also_freezes_bid_increase():
    c=DecisionCard(
        decision_key="advert:10:111:numeric_control",scope="campaign_sku",entity_id="10:111",
        title="t",diagnosis="d",priority="high",confidence="high",
        blockers=["нет свежей позиции"],
        recommended_actions=[{"mode":"numeric_ad_plan","action":"повысить","value":{"current_bid_rub":50,"target_bid_rub":53,"change_pct":6}}],
        evidence=[
            {"metric":"sku_stats_exact","value":True},{"metric":"current_bid_rub","value":50},
            {"metric":"target_bid_rub","value":53},{"metric":"economic_max_drr_pct","value":15},
            {"metric":"observed_drr_pct","value":5},{"metric":"stock_days_forecast","value":30},
        ],
    )
    cards,reviews=DecisionReviewBoard(load_policy()).review([c],{}, {})
    assert cards[0].recommended_actions[0]["value"]["target_bid_rub"] == 50
    assert cards[0].confidence == "low"
    assert reviews[0].verdict == "нужны данные"


def test_change_tracker_does_not_learn_from_unrelated_manual_bid_change(tmp_path: Path):
    db=Database(tmp_path/"x.sqlite3")
    c=DecisionCard(
        decision_key="advert:10:111:numeric_control",scope="campaign_sku",entity_id="10:111",
        title="t",diagnosis="d",
        recommended_actions=[{"mode":"numeric_ad_plan","value":{"current_bid_rub":50,"target_bid_rub":55}}],
    )
    db.sync_decision_history([c],{})
    tr=ChangeTracker(db)
    s1={"advertising_optimizer":{"deep_scan":{"data":{"campaigns":[{"campaign_id":10,"campaign":{"advertId":10,"nmIds":[111],"bidKopecks":5000},"nm_ids":[111]}]}}}}
    # User/manual process changes to 60, not the recommended 55.
    s2={"advertising_optimizer":{"deep_scan":{"data":{"campaigns":[{"campaign_id":10,"campaign":{"advertId":10,"nmIds":[111],"bidKopecks":6000},"nm_ids":[111]}]}}}}
    tr.sync(s1)
    ch=tr.sync(s2)
    assert ch[0]["matched_recommendation"] is False
    assert db.decision_history()[0]["status"] == "recommended"


def test_change_tracker_learns_only_when_bid_matches_target(tmp_path: Path):
    db=Database(tmp_path/"x.sqlite3")
    c=DecisionCard(
        decision_key="advert:10:111:numeric_control",scope="campaign_sku",entity_id="10:111",
        title="t",diagnosis="d",
        recommended_actions=[{"mode":"numeric_ad_plan","value":{"current_bid_rub":50,"target_bid_rub":55}}],
    )
    db.sync_decision_history([c],{})
    tr=ChangeTracker(db)
    s1={"advertising_optimizer":{"deep_scan":{"data":{"campaigns":[{"campaign_id":10,"campaign":{"advertId":10,"nmIds":[111],"bidKopecks":5000},"nm_ids":[111]}]}}}}
    s2={"advertising_optimizer":{"deep_scan":{"data":{"campaigns":[{"campaign_id":10,"campaign":{"advertId":10,"nmIds":[111],"bidKopecks":5500},"nm_ids":[111]}]}}}}
    tr.sync(s1)
    ch=tr.sync(s2)
    assert ch[0]["matched_recommendation"] is True
    assert db.decision_history()[0]["status"] == "observed_applied"


def test_bid_extractor_keeps_parent_nm_context():
    from wb_control_center.advertising_controller import _extract_bid_rub
    campaign={
        "advertId":10,
        "nmBids":[
            {"nmId":111,"settings":{"bidKopecks":5000}},
            {"nmId":222,"settings":{"bidKopecks":7000}},
        ],
    }
    assert _extract_bid_rub(campaign,111) == 50.0
    assert _extract_bid_rub(campaign,222) == 70.0


def test_review_freeze_keeps_dashboard_evidence_and_spend_consistent():
    c=DecisionCard(
        decision_key="advert:10:111:numeric_control",scope="campaign_sku",entity_id="10:111",
        title="Кампания: ставку повысить",diagnosis="ставку повысить",priority="high",confidence="high",
        blockers=["нет свежей позиции"],
        recommended_actions=[{"mode":"numeric_ad_plan","action":"повысить","value":{
            "current_bid_rub":50,"target_bid_rub":55,"change_pct":10,
            "current_spend_24h_rub":1000,"max_spend_24h_rub":1120,
        }}],
        evidence=[
            {"metric":"решение","value":"Ставку повысить"},
            {"metric":"sku_stats_exact","value":True},
            {"metric":"current_bid_rub","value":50}, {"metric":"target_bid_rub","value":55},
            {"metric":"current_spend_24h_rub","value":1000}, {"metric":"max_spend_next_24h_rub","value":1120},
            {"metric":"economic_max_drr_pct","value":15}, {"metric":"observed_drr_pct","value":5},
            {"metric":"stock_days_forecast","value":30},
        ],
    )
    cards,_=DecisionReviewBoard(load_policy()).review([c],{}, {})
    card=cards[0]; ev=evmap(card)
    action=next(a for a in card.recommended_actions if a.get("mode")=="numeric_ad_plan")
    assert action["value"]["target_bid_rub"] == 50
    assert action["value"]["max_spend_24h_rub"] == 1000
    assert ev["target_bid_rub"] == 50
    assert ev["max_spend_next_24h_rub"] == 1000
    assert "не повышать" in str(ev["решение"]).lower()
    assert "заблокировано" in card.title.lower()


def test_outcome_evaluator_falls_back_to_same_entity_when_decision_key_changes():
    from wb_control_center.outcome_evaluator import OutcomeEvaluator

    class FakeDB:
        def __init__(self): self.completed=None
        def due_evaluations(self, limit=200):
            return [{
                "id":1,
                "decision_key":"advert:10:111:numeric_control",
                "entity_id":"10:111",
                "horizon":"3ч",
                "payload":{"evidence":[
                    {"metric":"observed_drr_pct","value":8},
                    {"metric":"search_position","value":20},
                ]},
            }]
        def complete_evaluation(self, evaluation_id, verdict, metrics, notes):
            self.completed=(evaluation_id,verdict,metrics,notes)

    current=DecisionCard(
        decision_key="advert:10:111:zero_orders",
        scope="campaign_sku",entity_id="10:111",title="t",diagnosis="d",
        evidence=[
            {"metric":"observed_drr_pct","value":6},
            {"metric":"search_position","value":15},
        ],
    )
    db=FakeDB()
    done=OutcomeEvaluator(db).evaluate_due([current])
    assert done and db.completed is not None
    assert db.completed[2]["observed_drr_pct"]["now"] == 6
    assert db.completed[2]["search_position"]["now"] == 15
    assert db.completed[1] == "ведущие сигналы улучшились"


def test_unchanged_recommended_bid_is_tracked_as_observed_hold(tmp_path: Path):
    db=Database(tmp_path/"x.sqlite3")
    c=DecisionCard(
        decision_key="advert:10:111:numeric_control",scope="campaign_sku",entity_id="10:111",
        title="t",diagnosis="d",
        recommended_actions=[{"mode":"numeric_ad_plan","value":{"current_bid_rub":50,"target_bid_rub":50}}],
    )
    db.sync_decision_history([c],{})
    tr=ChangeTracker(db)
    snap={"advertising_optimizer":{"deep_scan":{"data":{"campaigns":[{"campaign_id":10,"campaign":{"advertId":10,"nmIds":[111],"bidKopecks":5000},"nm_ids":[111]}]}}}}
    tr.sync(snap)  # baseline only
    assert db.decision_history()[0]["status"] == "recommended"
    tr.sync(snap)  # second observation proves no-change recommendation was followed
    row=db.decision_history()[0]
    assert row["status"] == "observed_hold"
    assert row["applied_at"] is not None
    assert row["applied_change_id"] is None


def test_outcome_evaluation_is_interrupted_by_later_change():
    from wb_control_center.outcome_evaluator import OutcomeEvaluator
    class FakeDB:
        def __init__(self): self.completed=None
        def due_evaluations(self,limit=200):
            return [{"id":1,"decision_key":"k","entity_id":"10:111","horizon":"3ч","applied_at":"2026-09-22T10:00:00+00:00","payload":{"evidence":[{"metric":"observed_drr_pct","value":8}]}}]
        def observed_changes_since(self,entity,since):
            return [{"change_type":"advert_bid","observed_at":"2026-09-22T11:00:00+00:00","new":{"value":60}}]
        def complete_evaluation(self,*args): self.completed=args
    card=DecisionCard(decision_key="k",scope="campaign_sku",entity_id="10:111",title="t",diagnosis="d",evidence=[{"metric":"observed_drr_pct","value":5}])
    db=FakeDB(); OutcomeEvaluator(db).evaluate_due([card])
    assert db.completed[1] == "оценка прервана новым изменением"
    assert db.completed[2]["interventions"]
