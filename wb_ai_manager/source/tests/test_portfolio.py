from wb_control_center.config import Settings
from wb_control_center.portfolio import PortfolioService


def test_seed_has_real_portfolio_facts(tmp_path):
    s = Settings(data_dir=str(tmp_path), wb_api_token="")
    p = PortfolioService(s).snapshot()
    assert p["portfolio"]["profit_rub"] == 485257
    assert {x["id"] for x in p["stores"]} >= {"sanych", "air", "hozyushka"}
    assert p["own_27"]["wb_fbs_stock"] == 167634


def test_bridge_is_optional_and_connections_stay_read_only(tmp_path):
    s = Settings(data_dir=str(tmp_path), google_sheets_bridge_url="", google_sheets_bridge_key="", force_read_only=True)
    c = PortfolioService(s).connections()
    assert c["google_sheets"]["configured"] is False
    assert c["wb"]["read_only"] is True
    assert any(x["id"] == "own_27" for x in c["sources"])


def test_order_history_builds_real_short_vs_baseline_demand_trend(tmp_path):
    s = Settings(data_dir=str(tmp_path), wb_api_token="")
    svc = PortfolioService(s)
    out = {"own_27": {"products": [{"sku": "123", "name": "Товар", "orders_per_day": 10}]}}
    rows = [["Дата заказа", "Артикул WB"]]
    # 1–11 Sep: 10/day, 12–14 Sep: 20/day. 15 Sep is intentionally excluded as partial latest day.
    for day in range(1, 12):
        rows += [[f"2026-09-{day:02d} 12:00:00", "123"] for _ in range(10)]
    for day in range(12, 15):
        rows += [[f"2026-09-{day:02d} 12:00:00", "123"] for _ in range(20)]
    rows += [["2026-09-15 10:00:00", "123"] for _ in range(3)]
    payload = {"sources": {"own_27": {"ranges": {"orders_history": {"values": rows}}}}}
    svc._parse_own_27(payload, out)
    product = out["own_27"]["products"][0]
    assert product["orders_daily_3d"] == 20
    assert product["orders_daily_baseline_11d"] == 10
    assert product["orders_trend_pct"] == 100
    assert product["demand_history_asof"] == "2026-09-14"
