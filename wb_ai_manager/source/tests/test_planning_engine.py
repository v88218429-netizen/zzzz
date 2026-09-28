from wb_control_center.planning_engine import build_query_core, build_product_plan, calculate_scenario


def _history(value=10, days=35):
    rows=[]
    for i in range(days):
        month=8 if i < 31 else 9
        day=i+1 if i < 31 else i-30
        rows.append({"date":f"2026-{month:02d}-{day:02d}","orders":value})
    return rows


def test_query_core_prefers_queries_that_generate_orders():
    product={
        "sku":"100",
        "search_queries":[
            {"query":"ведро 10 л","orders":40,"clicks":200,"frequency":5000,"position":8},
            {"query":"ведро","orders":5,"clicks":100,"frequency":50000,"position":20},
            {"query":"ведро с крышкой","orders":25,"clicks":100,"frequency":7000,"position":5},
        ],
    }
    core=build_query_core(product,{},top_n=2)
    assert [x["query"] for x in core]==["ведро 10 л","ведро с крышкой"]
    assert round(sum(x["weight_pct"] for x in core),1)==100.0


def test_auto_plan_uses_search_seasonality_on_top_of_backtested_demand():
    product={
        "sku":"100","seller_article":"BIDON-100",
        "orders_daily_history":_history(10),
        "search_queries":[
            {"query":"бидон 15 л","orders":50,"clicks":250,"frequency":10000,"frequency_trend_pct":50},
            {"query":"бидон пищевой","orders":20,"clicks":100,"frequency":5000,"frequency_trend_pct":40},
        ],
        "orders_per_day":10,
        "price_client_rub":1000,
        "profit_rub":200,
    }
    plan=build_product_plan(product,{})
    assert plan.seller_article=="BIDON-100"
    assert plan.auto_orders_day is not None and plan.auto_orders_day > 10
    assert plan.search_frequency_trend_pct is not None and plan.search_frequency_trend_pct >= 40
    assert plan.revenue_30d_rub is not None
    assert plan.profit_30d_rub is not None


def test_saved_plan_creates_fact_plan_deviation():
    product={"sku":"100","orders_daily_history":_history(10),"orders_per_day":7}
    plan=build_product_plan(product,{},saved_plan={"planned_orders_day":10})
    assert plan.plan_orders_day==10
    assert plan.deviation_pct==-30
    assert plan.deviation_status=="behind"


def test_manual_scenario_changes_frequency_conversion_position_and_paid_traffic():
    plan={"auto_orders_day":10,"conversion_pct":10,"price_rub":1000,"unit_profit_rub":200}
    result=calculate_scenario(plan,{
        "frequency_change_pct":20,
        "conversion_change_pct":10,
        "position_change_pct":10,
        "additional_ad_clicks_day":100,
        "buyout_pct":80,
    })
    assert result["orders_day"] > 10
    assert result["additional_paid_orders_day"]==11
    assert result["sold_units_30d"] > 0
