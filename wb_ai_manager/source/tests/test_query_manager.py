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
    cards=QueryManager(now=NOW).build({"own_27":{"products":[product(rows)]}})["cards"]
    assert len({c["cluster_id"] for c in cards})==1

def test_outcome_evaluator_is_non_causal():
    out=evaluate_query_outcome({"position":20,"drr_pct":15,"orders":10},{"position":12,"drr_pct":14,"orders":12})
    assert out["status"]=="success"
    assert out["causality"]=="not_proven"

def test_auto_execute_is_opt_in_only():
    cfg=QueryManagerConfig(auto_execute_bid_change_pct=11)
    c=QueryManager(config=cfg,now=NOW).build({"own_27":{"products":[product([row()])]}})["cards"][0]
    assert c["execution_mode"]=="AUTO_EXECUTE"


def test_unknown_economics_never_increases_bid():
    p={"sku":"2","seller_article":"Без юнитки","query_intelligence":[row()]}
    card=QueryManager(now=NOW).build({"own_27":{"products":[p]}})["cards"][0]
    assert card["economics"]["profitable_to_scale"] is None
    assert card["bid_decision"]["decision"]=="HOLD"
    assert card["execution_mode"]=="INFORMATION_ONLY"

def test_mixed_freshness_is_operational_but_stale_rows_stay_quarantined():
    rows=[
        row(query="ведро",snapshot_at="2026-10-03T08:00:00+00:00"),
        row(query="ведро строительное",snapshot_at="2026-09-20T00:00:00+00:00"),
    ]
    out=QueryManager(now=NOW).build({"own_27":{"products":[product(rows)]}})
    assert out["summary"]["query_status"]=="ready_guarded"
    assert out["summary"]["facts_status"]=="partial_refresh"
    assert out["summary"]["operational_ready"] is True
    assert out["summary"]["fresh_query_rows"]==1
    assert out["summary"]["blocked_query_rows"]==1
    stale=[c for c in out["cards"] if c["query"]=="ведро строительное"][0]
    assert stale["queue"]=="refresh_fact"
    assert stale["execution_mode"]=="INFORMATION_ONLY"



def test_runtime_wb_search_overlay_refreshes_only_exact_query_position():
    stale=row(snapshot_at="2026-09-20T00:00:00+00:00", bid_at="2026-10-03T08:00:00+00:00")
    snapshots={
        "search_positions":{
            "positions":{
                "created_at":"2026-10-03T09:30:00+00:00",
                "data":{
                    "566189858:побелка для деревьев садовая":{
                        "nm_id":566189858,
                        "query":"побелка для деревьев садовая",
                        "position":9,
                        "source":"wb_search_report",
                    }
                },
            }
        }
    }
    out=QueryManager(now=NOW).build({"own_27":{"products":[product([stale])]}},snapshots)
    c=out["cards"][0]
    assert c["position"]==9
    assert c["freshness"]["position"]["status"]=="fresh"
    assert "stale_position" not in c["blockers"]
    assert c["runtime_search_source"]=="wb_search_report"


def test_runtime_sellmonitor_fallback_does_not_fake_freshness():
    stale=row(snapshot_at="2026-09-20T00:00:00+00:00", bid_at="2026-10-03T08:00:00+00:00")
    snapshots={
        "search_positions":{
            "positions":{
                "created_at":"2026-10-03T09:30:00+00:00",
                "data":{
                    "566189858:побелка для деревьев садовая":{
                        "nm_id":566189858,
                        "query":"побелка для деревьев садовая",
                        "position":9,
                        "source":"trusted_sellmonitor_positions",
                    }
                },
            }
        }
    }
    c=QueryManager(now=NOW).build({"own_27":{"products":[product([stale])]}},snapshots)["cards"][0]
    assert c["position"]==22
    assert "stale_position" in c["blockers"]
