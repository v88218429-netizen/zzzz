from datetime import datetime, timezone
from wb_control_center.query_manager import QueryManager, QueryManagerConfig, evaluate_query_outcome

NOW=datetime(2026,10,3,10,0,tzinfo=timezone.utc)

def product(rows):
    return {"sku":"566189858","seller_article":"Известь 4 кг","price_rub":500,"profit_rub":100,
            "plan_drr_pct":10,"query_intelligence":rows}

def row(**kw):
    base={"query":"побелка для деревьев садовая","frequency":10000,"position":22,"target_position":10,
          "organic_clicks":55,"paid_clicks":45,"orders":10,"clicks":100,"cr_pct":10,
          "campaign_id":"36632705","current_search_bid_rub":390,
          "snapshot_at":"2026-10-03T08:00:00+00:00","bid_at":"2026-10-03T08:00:00+00:00","drr_pct":12}
    base.update(kw); return base

def test_full_query_becomes_actionable_growth():
    out=QueryManager(now=NOW).build({"own_27":{"products":[product([row()])]}})
    c=out["cards"][0]
    assert out["summary"]["query_rows"]==1
    assert c["role"]=="core" and c["queue"]=="fix_gap"
    assert c["bid_decision"]["decision"]=="INCREASE"
    assert c["bid_decision"]["target_bid_rub"]==430
    assert c["execution_mode"]=="NEED_APPROVAL"
    assert [x["after_days"] for x in c["control_checkpoints"]]==[1,3,7]

def test_stale_position_blocks_monetary_action():
    c=QueryManager(now=NOW).build({"own_27":{"products":[product([row(snapshot_at="2026-09-20T00:00:00+00:00")])]}})["cards"][0]
    assert c["queue"]=="refresh_fact"
    assert "stale_position" in c["blockers"]
    assert c["bid_decision"]["decision"]=="REFRESH_FACT"
    assert c["execution_mode"]=="INFORMATION_ONLY"

def test_paid_share_over_55_reduces_bid():
    c=QueryManager(now=NOW).build({"own_27":{"products":[product([row(organic_clicks=20,paid_clicks=80)])]}})["cards"][0]
    assert c["queue"]=="avoid_overbuy"
    assert c["bid_decision"]["decision"]=="DECREASE"
    assert c["bid_decision"]["target_bid_rub"]==350

def test_break_even_guard_reduces_bid():
    c=QueryManager(now=NOW).build({"own_27":{"products":[product([row(drr_pct=35)])]}})["cards"][0]
    assert c["economics"]["break_even_drr_pct"]==30
    assert c["economics"]["profitable_to_scale"] is False
    assert c["bid_decision"]["decision"]=="DECREASE"

def test_bid_requires_independent_fresh_timestamp():
    c=QueryManager(now=NOW).build({"own_27":{"products":[product([row(bid_at=None)])]}})["cards"][0]
    assert c["bid_decision"]["decision"]=="REFRESH_FACT"
    assert c["execution_mode"]=="INFORMATION_ONLY"

def test_legacy_top_query_fallback():
    p={"sku":"1","top_search_query":"цемент 5 кг","top_search_frequency":3000,
       "top_search_position":8,"top_search_target_position":10,"search_snapshot_at":"2026-10-03T09:00:00+00:00"}
    out=QueryManager(now=NOW).build({"own_27":{"products":[p]}})
    assert out["summary"]["query_rows"]==1
    assert out["cards"][0]["source"]=="legacy_top_query"

def test_similar_queries_cluster():
    rows=[row(query="побелка для деревьев садовая"),row(query="садовая побелка для деревьев",frequency=9000)]
    cards=QueryManager(now=NOW).build({"own_27":{"products":[product(rows)}})["cards"]
    assert len({c["cluster_id"] for c in cards})==1

def test_outcome_evaluator_is_non_causal():
    out=evaluate_query_outcome({"position":20,"drr_pct":15,"orders":10},{"position":12,"drr_pct":14,"orders":12})
    assert out["status"]=="success"
    assert out["causality"]=="not_proven"

def test_auto_execute_is_opt_in_only():
    cfg=QueryManagerConfig(auto_execute_bid_change_pct=11)
    c=QueryManager(config=cfg,now=NOW).build({"own_27":{"products":[product([row()])]}})["cards"][0]
    assert c["execution_mode"]=="AUTO_EXECUTE"
