from pathlib import Path

from wb_control_center.advertising_controller import AdvertisingController
from wb_control_center.config import load_policy
from wb_control_center.db import Database
from wb_control_center.runtime_policy import RuntimePolicyStore


def cfg(**overrides):
    base = RuntimePolicyStore(Database(Path('/tmp/wb-ai-test-policy.sqlite3')), load_policy()).advertising()
    base.update(overrides)
    return base


def daily(orders, views=None, spend=None, revenue=None):
    views = views or [1000] * len(orders)
    spend = spend or [100] * len(orders)
    revenue = revenue or [2000] * len(orders)
    return [
        {"date": f"2026-09-{i+1:02d}", "orders": o, "views": v, "sum": s, "sum_price": r, "clicks": 60}
        for i, (o, v, s, r) in enumerate(zip(orders, views, spend, revenue))
    ]


def test_profitable_campaign_gets_numeric_scale_up_with_step_cap():
    c = AdvertisingController(cfg(max_bid_step_pct=7, organic_growth_hold_threshold_pct=99))
    campaign = {"advertId": 1, "nmIds": [123], "bidKopecks": 5000}
    stats = {"advertId": 1, "sum": 700, "orders": 14, "sum_price": 14000, "clicks": 420, "views": 9000,
             "days": daily([2,2,2,2,2,2,2])}
    product = {"sku":"123","price_client_rub":1000,"profit_rub":200,"safe_stock":500,"orders_per_day":20}
    reco = {"base":{"competitiveBid":{"bidKopecks":5500},"leadersBid":{"bidKopecks":7000}}}
    p = c.build_plan(campaign, stats, product, reco)
    assert p.decision == "SCALE_UP"
    assert p.current_bid_rub == 50
    assert 50 < p.target_bid_rub <= 53.5
    assert p.bid_change_pct is not None and 0 < p.bid_change_pct <= 7


def test_organic_demand_growth_holds_bid_instead_of_blind_scaling():
    c = AdvertisingController(cfg(organic_growth_hold_threshold_pct=15))
    campaign = {"advertId": 2, "nmIds": [124], "bidKopecks": 4600}
    orders = [2,2,2,2,2,4,5,6]
    views = [1000,1000,1000,1000,1000,1500,1800,2100]
    stats = {"advertId":2,"sum":800,"orders":25,"sum_price":20000,"clicks":500,"views":10400,
             "days":daily(orders, views=views)}
    product={"sku":"124","price_client_rub":1000,"profit_rub":220,"safe_stock":1000,"orders_per_day":30}
    reco={"base":{"competitiveBid":{"bidKopecks":5200}}}
    p=c.build_plan(campaign,stats,product,reco)
    assert p.decision == "HOLD"
    assert p.target_bid_rub == 46
    assert p.orders_trend_pct and p.orders_trend_pct > 15


def test_low_forecast_stock_caps_demand_but_does_not_cut_bid_blindly():
    c = AdvertisingController(cfg(min_stock_days_for_hold=7, min_stock_days_for_scale=10))
    campaign={"advertId":3,"nmIds":[125],"bidKopecks":6000}
    stats={"advertId":3,"sum":700,"orders":20,"sum_price":16000,"clicks":400,"views":8000,"days":daily([3,3,3,4,4,5,5])}
    product={"sku":"125","price_client_rub":1000,"profit_rub":200,"safe_stock":100,"orders_per_day":20}
    p=c.build_plan(campaign,stats,product,{})
    assert p.decision == "CAP_DEMAND"
    assert p.target_bid_rub == 60
    assert p.max_spend_next_24h_rub == p.current_spend_24h_rub


def test_high_drr_produces_numeric_scale_down():
    c=AdvertisingController(cfg(target_drr_pct=8,max_bid_step_pct=7))
    campaign={"advertId":4,"nmIds":[126],"bidKopecks":7000}
    stats={"advertId":4,"sum":2000,"orders":10,"sum_price":10000,"clicks":300,"views":7000,"days":daily([1,1,1,2,2,2,1])}
    product={"sku":"126","price_client_rub":1000,"profit_rub":100,"safe_stock":1000,"orders_per_day":10}
    p=c.build_plan(campaign,stats,product,{})
    assert p.decision == "SCALE_DOWN"
    assert p.target_bid_rub == 65.1
    assert p.bid_change_pct == -7.0


def test_negative_unit_always_blocks_scaling_and_reduces_pressure():
    c=AdvertisingController(cfg())
    campaign={"advertId":5,"nmIds":[127],"bidKopecks":5000}
    stats={"advertId":5,"sum":900,"orders":15,"sum_price":15000,"clicks":500,"views":9000,"days":daily([2,2,2,2,2,2,3])}
    product={"sku":"127","price_client_rub":1000,"profit_rub":-20,"safe_stock":900,"orders_per_day":20}
    p=c.build_plan(campaign,stats,product,{})
    assert p.decision == "SCALE_DOWN"
    assert p.target_bid_rub < p.current_bid_rub
    assert any("отрицательная" in x.lower() for x in p.reasons)


def test_runtime_policy_hot_reload_persists_and_is_bounded(tmp_path):
    db=Database(tmp_path/'policy.sqlite3')
    policy=load_policy()
    store=RuntimePolicyStore(db,policy)
    result=store.update_advertising({"target_drr_pct":6.5,"max_bid_step_pct":5,"evaluation_window_hours":2})
    assert result["advertising"]["target_drr_pct"] == 6.5
    # A new store instance reads the saved policy without restart/rebuild.
    store2=RuntimePolicyStore(db,load_policy())
    assert store2.advertising()["max_bid_step_pct"] == 5
    try:
        store2.update_advertising({"max_bid_step_pct":25})
        assert False, "must reject values above hard safety cap"
    except ValueError:
        pass


def test_negative_weekly_group_does_not_block_fresh_profitable_cohort_by_itself():
    c=AdvertisingController(cfg(organic_growth_hold_threshold_pct=99))
    campaign={"advertId":9,"nmIds":[555],"bidKopecks":5000}
    stats={"advertId":9,"sum":500,"orders":20,"sum_price":20000,"clicks":600,"views":12000,"days":daily([2,2,2,2,2,2,2])}
    product={
        "sku":"555","price_client_rub":1000,"profit_rub":180,"drr_pct":5,
        "safe_stock":1000,"orders_per_day":20,"group_profit_rub":-3500,
        "group_margin_pct":-12,"weekly_group":"Тестовая группа",
        "cohort":{"recent_7d":{"orders":100,"open_orders":70,"maturity_pct":28,"quality_ready":False},
                  "buyout_lag":{"p50_days":6.9,"p90_days":13.1}},
    }
    p=c.build_plan(campaign,stats,product,{"base":{"competitiveBid":{"bidKopecks":6500}}})
    assert p.decision == "SCALE_UP"
    assert p.target_bid_rub > 50
    assert any("созрела" in x.lower() and "не используются" in x.lower() for x in p.reasons)
    assert any("прошлый отчётный период" in x.lower() for x in p.reasons)


def test_missing_unit_economics_never_recommends_bid_increase():
    c=AdvertisingController(cfg(organic_growth_hold_threshold_pct=99))
    campaign={"advertId":10,"nmIds":[777],"bidKopecks":5000}
    stats={"advertId":10,"sum":400,"orders":12,"sum_price":12000,"clicks":500,"views":10000,"days":daily([2,2,2,2,2,2,2])}
    product={"sku":"777","safe_stock":1000,"orders_per_day":20}
    p=c.build_plan(campaign,stats,product,{"base":{"competitiveBid":{"bidKopecks":7000}}})
    assert p.decision != "SCALE_UP"
    assert p.target_bid_rub == 50
    assert any("юнит-эконом" in x.lower() for x in p.blockers)


def test_weekly_orders_and_profit_changes_are_context_not_same_cohort_gate():
    c=AdvertisingController(cfg(organic_growth_hold_threshold_pct=99))
    campaign={"advertId":11,"nmIds":[888],"bidKopecks":5000}
    stats={"advertId":11,"sum":450,"orders":18,"sum_price":18000,"clicks":600,"views":12000,"days":daily([2,2,2,2,2,2,2])}
    product={
        "sku":"888","price_client_rub":1000,"profit_rub":150,"drr_pct":5,
        "safe_stock":1200,"orders_per_day":20,"weekly_group":"Ведро пластиковое 12 л",
        "group_orders_change_pct":59.8,"group_buyouts_change_pct":-39.9,"group_profit_change_pct":-137.5,
        "group_profit_rub":-3819,"group_margin_pct":-7.1,
        "cohort":{"recent_7d":{"orders":120,"open_orders":85,"maturity_pct":31,"quality_ready":False},
                  "buyout_lag":{"p50_days":7.0,"p90_days":13.0}},
    }
    p=c.build_plan(campaign,stats,product,{"base":{"competitiveBid":{"bidKopecks":6500}}})
    assert p.decision == "SCALE_UP"
    assert not any("рост заказов не превращается" in x.lower() for x in p.reasons)
    assert any("не используются как оценка качества новых заказов" in x.lower() for x in p.reasons)



def test_business_drr_is_primary_and_wb_attribution_stays_separate():
    c=AdvertisingController(cfg(target_drr_pct=8,organic_growth_hold_threshold_pct=99))
    campaign={"advertId":21,"nmIds":[921],"bidKopecks":5000}
    stats={"advertId":21,"sum":500,"orders":10,"sum_price":10000,"clicks":300,"views":6000,
           "days":daily([1,1,1,1,2,2,2])}
    product={
        "sku":"921","price_client_rub":1000,"profit_rub":200,"drr_pct":5,
        "safe_stock":1000,"orders_per_day":20,
        "fact_ad_spend_rub":2000,"fact_sales_revenue_rub":10000,
    }
    p=c.build_plan(campaign,stats,product,{})
    assert p.wb_attributed_drr_pct == 5.0
    assert p.business_drr_pct == 20.0
    assert p.observed_drr_pct == 20.0
    assert p.decision == "HOLD"
    assert p.target_bid_rub == p.current_bid_rub
    assert any("Бизнес-ДРР" in x for x in p.reasons)
    assert "не доказана" in p.decision_label


def test_incrementality_proxy_blocks_buying_own_organic():
    c=AdvertisingController(cfg(target_drr_pct=100,organic_growth_hold_threshold_pct=99))
    campaign={"advertId":22,"nmIds":[922],"bidKopecks":5000}
    stats={"advertId":22,"sum":700,"orders":24,"sum_price":24000,"clicks":500,"views":10000,
           "days":daily([2,2,2,2,2,4,5,6])}
    history=[{"date":f"2026-08-{i+1:02d}","orders":10} for i in range(20)]
    product={
        "sku":"922","price_client_rub":1000,"profit_rub":250,"drr_pct":5,
        "safe_stock":1000,"orders_per_day":10,"orders_daily_history":history,
    }
    p=c.build_plan(campaign,stats,product,{"base":{"competitiveBid":{"bidKopecks":6000}}})
    assert p.incremental_capture_pct == 0.0
    assert p.cannibalization_risk == "high"
    assert p.decision == "HOLD"
    assert p.operating_mode == "organic_protection"


def test_cpc_ceiling_comes_from_conversion_and_unit_economics():
    c=AdvertisingController(cfg(target_drr_pct=100,organic_growth_hold_threshold_pct=99))
    campaign={"advertId":23,"nmIds":[923],"bidKopecks":6000}
    stats={"advertId":23,"sum":2000,"orders":10,"sum_price":100000,"clicks":300,"views":6000,
           "days":daily([1,1,1,1,1,1,1],spend=[280]*7,revenue=[14000]*7)}
    product={
        "sku":"923","price_client_rub":1000,"profit_rub":20,"drr_pct":5,
        "safe_stock":1000,"orders_per_day":10,
    }
    p=c.build_plan(campaign,stats,product,{})
    assert p.max_ad_cost_per_order_rub == 70.0
    assert 2.3 < p.max_cpc_rub < 2.4
    assert p.cpc_rub > p.max_cpc_rub
    assert p.decision == "SCALE_DOWN"
    assert any("CPC" in x for x in p.reasons)


def test_season_mode_can_scale_when_search_frequency_accelerates():
    c=AdvertisingController(cfg(target_drr_pct=100,organic_growth_hold_threshold_pct=15))
    campaign={"advertId":24,"nmIds":[924],"bidKopecks":5000}
    stats={"advertId":24,"sum":500,"orders":10,"sum_price":20000,"clicks":400,"views":8000,
           "days":daily([1,1,1,1,1,1,1])}
    product={
        "sku":"924","price_client_rub":1000,"profit_rub":250,"drr_pct":5,
        "safe_stock":1000,"orders_per_day":10,
        "orders_trend_pct":5,"search_frequency_trend_pct":30,
    }
    p=c.build_plan(campaign,stats,product,{"base":{"competitiveBid":{"bidKopecks":6000}}})
    assert p.operating_mode == "season"
    assert p.decision == "SCALE_UP"
    assert p.target_bid_rub > p.current_bid_rub
    assert any("сезон" in x.lower() for x in p.reasons)
