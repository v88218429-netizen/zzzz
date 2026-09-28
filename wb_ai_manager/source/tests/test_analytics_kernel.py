from wb_control_center.analytics_kernel import demand_quality, economics_quality, snapshot_quality
from wb_control_center.demand_forecast import build_demand_forecast


def intermittent_history(days=60):
    out=[]
    for i in range(days):
        month="07" if i < 31 else "08"
        day=i+1 if i < 31 else i-30
        out.append({"date":f"2026-{month}-{day:02d}","orders":4 if i % 7 == 0 else 0})
    return out


def test_intermittent_demand_exposes_profile_and_adaptive_model():
    product={"sku":"x","orders_per_day":0.6,"orders_daily_history":intermittent_history()}
    fc=build_demand_forecast(product)
    assert fc.demand_type in {"intermittent","lumpy"}
    assert fc.model_name in {"croston_sba","tsb","recent_7d","ewma","weekday"}
    assert fc.adi is not None and fc.adi > 1.32
    assert fc.backtest_mae is not None


def test_demand_quality_detects_three_x_source_conflict():
    product={"orders_per_day":30,"orders_daily_history":[
        {"date":f"2026-07-{1+i:02d}" if i < 31 else f"2026-08-{i-30:02d}","orders":2}
        for i in range(60)
    ]}
    fc=build_demand_forecast(product)
    q=demand_quality(product,fc)
    assert q.ready is False
    assert any("расходятся" in x for x in q.issues)


def test_economics_quality_does_not_treat_profit_column_as_self_proving():
    product={"price_rub":522.75,"price_client_rub":392,"cost_rub":468.12,"profit_rub":-143.16,
             "margin_pct":-27.39,"commission_pct":24,"tax_total_rub":42.19,"drr_pct":None,
             "logistics_total_rub":None}
    q=economics_quality(product)
    assert q.ready is False
    assert any("логистика" in x for x in q.missing)
    assert q.level != "high"


def test_snapshot_quality_separates_agent_completion_from_dataset_health():
    snapshots={
        "price_margin":{"promotions":{"created_at":"2026-09-27T00:00:00Z","data":{"text":"Ошибка: HTTP 400"}}},
        "finance":{"balance":{"created_at":"2026-09-27T00:00:00Z","data":{"current":100}}},
        "supply":{"acceptance":{"created_at":"2026-09-27T00:00:00Z","data":{"wb_method_disabled":True,"detail":"disabled"}}},
    }
    q=snapshot_quality(snapshots)
    assert q["datasets_total"] == 3
    assert q["datasets_degraded"] == 2
    assert q["datasets_healthy"] == 1
    assert q["status"] == "degraded"
