from wb_control_center.config import load_policy
from wb_control_center.decision_engine import DecisionEngine


def by_key(cards, key):
    return next((x for x in cards if x.decision_key == key), None)


def history(value=100, days=60):
    return [{"date": f"2026-07-{1+i:02d}" if i < 31 else f"2026-08-{i-30:02d}", "orders": value} for i in range(days)]


def test_legacy_negative_unit_cannot_create_money_decision():
    p={"source_health":[],"stores":[],"own_27":{"products":[{
        "sku":"1","name":"Тест","price_rub":300,"profit_rub":-10,"margin_pct":-3.3,"drr_pct":18,
        "unit_economics_role":"legacy_fallback_diagnostic",
    }]}}
    cards=DecisionEngine(load_policy()).build(p)
    assert not any(c.entity_id=="1" and "negative" in c.decision_key for c in cards)


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


def test_zero_forecast_conflict_is_suppressed_from_operator_decisions():
    p={"source_health":[],"stores":[],"own_27":{"products":[{
        "sku":"zero","name":"Таз","orders_per_day":3,"safe_stock":0,
        "orders_daily_history":[{"date":f"2026-09-{d:02d}","orders":0} for d in range(18,25)],
    }]}}
    cards=DecisionEngine(load_policy()).build(p)
    assert not any(c.decision_key=="sku:zero:demand_conflict" for c in cards)
    assert not any(c.entity_id=="zero" and any(a.get("mode")=="supply_plan" for a in c.recommended_actions) for c in cards)


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
        "drr_pct":20,"plan_drr_pct":20,"commission_pct":24,"logistics_total_rub":40,"tax_total_rub":18,
        "buyout_plan_pct":90,"unit_economics_role":"primary_plan","unit_economics_source":"Юнит-экономика вб / WB FBS новая",
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
    forecast=next(e["value"] for e in c.evidence if e["metric"]=="Прогноз заказов в день")
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


def test_physical_bidon_order_cover_suppresses_bundle_shortage_duplicates():
    p={"source_health":[],"stores":[],"own_27":{
        "products":[
            {"sku":"1588048269","name":"Бидон 15л 1шт","physical_position":"Бидон 15л",
             "orders_per_day":4,"orders_daily_history":history(4),"safe_stock":0,"safe_stock_source":"WB FBS"},
            {"sku":"1588048270","name":"Бидон 15л 2шт","physical_position":"Бидон 15л",
             "orders_per_day":10,"orders_daily_history":history(10),"safe_stock":0,"safe_stock_source":"WB FBS"},
        ],
        "physical_inventory":[{
            "physical_position":"Бидон 15л","k2_safe_stock":0,"fbs_debt_orders":91,
            "reorder_point_days":15,"supply_target_days":21,"order_status":"✅ЗАКАЗ",
            "planned_order_qty":800,"supplier_debt_qty":500,
        }]
    }}
    cards=DecisionEngine(load_policy()).build(p)
    assert not any(c.decision_key in {"sku:1588048269:stockout","sku:1588048270:stockout"} for c in cards)
    assert not any(c.decision_key=="physical:Бидон 15л:stockout" for c in cards)


def test_physical_bidon_without_order_creates_one_supply_decision():
    p={"source_health":[],"stores":[],"own_27":{
        "products":[
            {"sku":"1588048269","name":"Бидон 15л 1шт","physical_position":"Бидон 15л",
             "orders_per_day":4,"orders_daily_history":history(4),"safe_stock":0,"safe_stock_source":"WB FBS"},
            {"sku":"1588048270","name":"Бидон 15л 2шт","physical_position":"Бидон 15л",
             "orders_per_day":10,"orders_daily_history":history(10),"safe_stock":0,"safe_stock_source":"WB FBS"},
        ],
        "physical_inventory":[{
            "physical_position":"Бидон 15л","k2_safe_stock":0,"fbs_debt_orders":0,
            "reorder_point_days":15,"supply_target_days":21,"order_status":"",
            "planned_order_qty":0,"supplier_debt_qty":0,
        }]
    }}
    cards=DecisionEngine(load_policy()).build(p)
    c=by_key(cards,"physical:Бидон 15л:stockout")
    assert c is not None
    assert not any(x.decision_key in {"sku:1588048269:stockout","sku:1588048270:stockout"} for x in cards)
    assert "одну поставку" in c.recommended_actions[0]["action"].lower()


def test_beige_toilet_bucket_gets_computed_ad_target_and_root_cause():
    p={"source_health":[],"stores":[],"own_27":{"products":[{
        "sku":"1442769822","name":"ведро туалет 17л беж",
        "unit_economics_role":"primary_plan","unit_economics_source":"Юнит-экономика вб / WB FBS новая",
        "price_rub":1414.068,"price_client_rub":1131.2544,"cost_rub":468.12,
        "profit_rub":282.8136,"margin_pct":20,"plan_drr_pct":3,"drr_pct":3,
        "commission_pct":32,"logistics_total_rub":40,"acceptance_rub":5,
        "tax_total_rub":90.5004,"buyout_plan_pct":95,
        "fact_ad_spend_rub":5452.8,"fact_sales_revenue_rub":26215,"fact_sales_qty":29,
        "fact_drr_sales_pct":20.8003,"fact_economics_period_from":"2026-09-21",
        "fact_economics_period_to":"2026-09-25","fact_economics_quality":"COMPLETE_5D",
        "fact_economics_source":"Sellmonitor trusted raw",
    }]}}
    c=by_key(DecisionEngine(load_policy()).build(p),"sku:1442769822:plan_fact_economics_gap")
    assert c is not None
    target=next(a for a in c.recommended_actions if a["mode"]=="advertising_target")
    assert "4719" in target["action"]
    causes=[e for e in c.evidence if e["source"]=="Авторазложение причины"]
    assert causes
    assert "дорогая реклама" in causes[0]["metric"].lower()


def test_store_ad_card_contains_resolved_buckets_not_analysis_chore():
    p={"source_health":[],"own_27":{"products":[]},"stores":[{
        "id":"hozyushka","name":"Хозяюшка","source":"weekly","drr_pct":15,"margin_pct":11.1,
        "profit_rub":15788,"orders_qty":442,"buyouts_qty":102,
        "groups":[
            {"name":"Цемент","profit_rub":-3364,"margin_pct":-35.9,"buyout_pct":9.5,"orders_rub":177735},
            {"name":"Гипс","profit_rub":-3594,"margin_pct":-335.6,"buyout_pct":17.6,"orders_rub":15171},
            {"name":"Ведро с крышкой 8 л","profit_rub":-47,"margin_pct":-1.0,"buyout_pct":40,"orders_rub":14951},
            {"name":"Ветошь ХПП","profit_rub":15071,"margin_pct":26.8,"buyout_pct":123.5,"orders_rub":42819},
            {"name":"Ведро с крышкой 10 л","profit_rub":3586.78,"margin_pct":22.5,"buyout_pct":25,"orders_rub":59435},
        ]
    }]}
    c=by_key(DecisionEngine(load_policy()).build(p),"store:hozyushka:portfolio_ads")
    assert c is not None
    text=" ".join(a["action"] for a in c.recommended_actions).lower()
    assert "цемент" in text and "гипс" in text
    assert "ветошь" in text
    assert "отделить" not in text and "разобрать" not in text
