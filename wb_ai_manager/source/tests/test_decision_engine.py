from wb_control_center.config import load_policy
from wb_control_center.decision_engine import DecisionEngine


def by_key(cards, key):
    return next((x for x in cards if x.decision_key == key), None)


def history(value=100, days=60):
    return [{"date": f"2026-07-{1+i:02d}" if i < 31 else f"2026-08-{i-30:02d}", "orders": value} for i in range(days)]


def test_incomplete_negative_unit_is_a_verification_signal_not_proven_loss():
    p={"source_health":[],"stores":[],"own_27":{"products":[{
        "sku":"1","name":"Тест","price_rub":300,"profit_rub":-10,"margin_pct":-3.3,"drr_pct":18,
    }]}}
    c=by_key(DecisionEngine(load_policy()).build(p),"sku:1:negative_unit")
    assert c is not None
    assert c.priority == "high"
    assert c.confidence == "low"
    assert c.blockers
    assert "не фактическая прибыль" in c.diagnosis.lower()
    assert c.analysis["metric_type"] == "calculated_unit_economics"
    assert any("не менять цену" in a["action"].lower() for a in c.recommended_actions)


def test_stockout_uses_concrete_qty_only_when_history_confirms_rate():
    p={"source_health":[],"stores":[],"own_27":{"products":[{
        "sku":"2","name":"Товар","orders_per_day":100,"orders_daily_history":history(100),
        "safe_stock":300,"safe_stock_source":"WB FBS","fbs_debt_orders":0,
    }]}}
    c=by_key(DecisionEngine(load_policy()).build(p),"sku:2:stockout")
    assert c is not None
    assert "1100" in c.diagnosis
    assert c.confidence == "high"
    assert c.analysis["backtest_mae"] == 0


def test_zero_forecast_conflict_blocks_exact_supply_quantity():
    p={"source_health":[],"stores":[],"own_27":{"products":[{
        "sku":"zero","name":"Таз","orders_per_day":3,"safe_stock":0,
        "orders_daily_history":[{"date":f"2026-09-{d:02d}","orders":0} for d in range(18,25)],
    }]}}
    c=by_key(DecisionEngine(load_policy()).build(p),"sku:zero:demand_conflict")
    assert c is not None
    assert c.confidence == "low"
    assert "точное количество" in c.title.lower()
    assert not any(a["mode"] == "supply_plan" for a in c.recommended_actions)


def test_k2_stock_source_is_high_confidence_when_demand_is_validated():
    p={"source_health":[],"stores":[],"own_27":{"products":[{
        "sku":"3","name":"Товар","orders_per_day":50,"orders_daily_history":history(50),
        "safe_stock":100,"safe_stock_source":"K2 SAFE","fbs_debt_orders":0,
    }]}}
    c=by_key(DecisionEngine(load_policy()).build(p),"sku:3:stockout")
    assert c is not None
    assert c.confidence == "high"


def test_calendar_week_low_buyout_does_not_create_same_cohort_ad_guard():
    p={"source_health":[],"own_27":{"products":[]},"stores":[{
        "id":"x","name":"Магазин","source":"weekly","groups":[{
            "name":"Группа","orders_qty":100,"buyout_pct":20,"profit_rub":1000,
        }]
    }]}
    c=by_key(DecisionEngine(load_policy()).build(p),"store:x:low_buyout")
    assert c is None


def test_broken_source_creates_data_quality_guard():
    p={"stores":[],"own_27":{"products":[]},"source_health":[{
        "id":"bad","status":"broken","freshness":"old","trust":"low",
    }]}
    c=by_key(DecisionEngine(load_policy()).build(p),"data:source_quality")
    assert c is not None
    assert c.priority == "high"

def test_stockout_limits_supply_only_when_negative_unit_is_complete():
    p={"source_health":[],"stores":[],"own_27":{"products":[{
        "sku":"4","name":"Убыточный дефицит","orders_per_day":100,"orders_daily_history":history(100),
        "safe_stock":100,"safe_stock_source":"K2 SAFE","fbs_debt_orders":0,
        "price_rub":300,"price_client_rub":250,"cost_rub":100,"profit_rub":-5,"margin_pct":-1.67,
        "drr_pct":20,"commission_pct":24,"logistics_total_rub":40,"tax_total_rub":18,
    }]}}
    c=by_key(DecisionEngine(load_policy()).build(p),"sku:4:stockout")
    assert c is not None
    assert "юнитка отрицательная" in c.title
    assert any(a["mode"] == "bridge_supply" for a in c.recommended_actions)
    assert not any(a["mode"] == "supply_plan" for a in c.recommended_actions)

def wrapped(data):
    return {"created_at":"2026-09-18T00:00:00Z","data":data}


def test_ad_spend_without_orders_becomes_action_plan_not_just_alert():
    snaps={
        "advertising_monitor":{
            "stats_7d":wrapped({"data":[{"advertId":501,"sum":1800,"orders":0,"sum_price":0}]}),
            "active_campaigns":wrapped({"campaigns":[{"advertId":501,"name":"Тестовая кампания"}]})
        }
    }
    c=by_key(DecisionEngine(load_policy()).build({"source_health":[],"stores":[],"own_27":{"products":[]}},snaps),"advert:501:zero_orders")
    assert c is not None
    assert c.priority == "critical"
    assert any("Не увеличивать ставку" in a["action"] for a in c.recommended_actions)
    assert c.blockers


def test_returns_chat_and_deduction_collapse_into_one_quality_root_cause():
    snaps={
        "returns_quality":{"open_claims":wrapped({"claims":[
            {"nmId":10,"reason":"Не соответствует описанию"},
            {"nmId":10,"reason":"Не соответствует описанию"},
            {"nmId":10,"reason":"Не соответствует описанию"},
        ]})},
        "buyer_chats":{"chat_events":wrapped({"events":[{"message":"Не тот товар в заказе"}]})},
        "cost_guard":{"deductions":wrapped({"data":[{"nmID":10,"amount":1500,"reason":"Неверное вложение"}]})},
    }
    c=by_key(DecisionEngine(load_policy()).build({"source_health":[],"stores":[],"own_27":{"products":[]}},snaps),"sku:10:quality_root_cause")
    assert c is not None
    assert c.priority == "critical"
    assert len(c.evidence) >= 3
    assert any("root" in a["mode"] or "quality" in a["mode"] for a in c.recommended_actions)


def test_funnel_weakness_blocks_buying_more_traffic():
    snaps={"funnel":{"funnel_7d":wrapped({"items":[{"nmID":10,"openCardCount":1500,"addToCartCount":60,"ordersCount":20,"conversions":{"addToCartPercent":4}}]})}}
    c=by_key(DecisionEngine(load_policy()).build({"source_health":[],"stores":[],"own_27":{"products":[]}},snaps),"sku:10:weak_card_conversion")
    assert c is not None
    assert any("Не покупать дополнительный трафик" in a["action"] for a in c.recommended_actions)


def test_promotion_without_unit_economics_is_blocked_by_evidence_gap():
    snaps={"price_margin":{"promotions":wrapped({"items":[{"nmID":10,"price":1000,"planPrice":800}]})}}
    c=by_key(DecisionEngine(load_policy()).build({"source_health":[],"stores":[],"own_27":{"products":[]}},snaps),"sku:10:promotion_guard")
    assert c is not None
    assert c.blockers
    assert any("Не входить" in a["action"] for a in c.recommended_actions)

def test_position_drop_event_becomes_cross_contour_action_plan():
    snaps={"_events":[{
        "agent":"search_positions","key":"position:123:ведро","severity":"warning","message":"просадка",
        "payload":{"before":{"nm_id":123,"query":"ведро","position":5},"after":{"nm_id":123,"query":"ведро","position":14}}
    }]}
    c=by_key(DecisionEngine(load_policy()).build({"source_health":[],"stores":[],"own_27":{"products":[]}},snaps),"sku:123:position_drop:ведро")
    assert c is not None
    assert any("остаток" in a["action"] for a in c.recommended_actions)
    assert any("не повышать" in a["action"].lower() for a in c.recommended_actions)


def test_stock_plan_uses_backtested_history_instead_of_static_average():
    h=history(100)
    for i in range(-7,0):
        h[i]["orders"]=140
    p={"source_health":[],"stores":[],"own_27":{"products":[{
        "sku":"trend","name":"Растущий спрос","orders_per_day":100,"orders_daily_history":h,
        "safe_stock":300,"safe_stock_source":"K2 SAFE","fbs_debt_orders":0,
    }]}}
    c=by_key(DecisionEngine(load_policy()).build(p),"sku:trend:stockout")
    assert c is not None
    forecast=next(e["value"] for e in c.evidence if e["metric"]=="forecast_orders_per_day")
    assert forecast > 100
    assert c.analysis["demand_model"]
    assert c.analysis["backtest_mae"] is not None


def test_runtime_target_stock_days_changes_supply_math_without_code_change(tmp_path):
    from wb_control_center.db import Database
    from wb_control_center.runtime_policy import RuntimePolicyStore
    policy=load_policy()
    store=RuntimePolicyStore(Database(tmp_path/'hot.sqlite3'),policy)
    store.update_advertising({"target_stock_days":18})
    p={"source_health":[],"stores":[],"own_27":{"products":[{
        "sku":"hot","name":"Товар","orders_per_day":100,"orders_daily_history":history(100),
        "safe_stock":300,"safe_stock_source":"K2 SAFE","fbs_debt_orders":0,
    }]}}
    c=by_key(DecisionEngine(policy).build(p),"sku:hot:stockout")
    assert c is not None
    assert "1500" in c.diagnosis  # 100*18 - 300
