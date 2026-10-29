from pathlib import Path
from datetime import date

import pytest

from wb_control_center.config import Settings, load_policy
from wb_control_center.decision_engine import DecisionEngine
from wb_control_center.portfolio import PortfolioService


def test_seed_portfolio_is_explicitly_historical(tmp_path):
    settings = Settings(data_dir=str(tmp_path))
    snap = PortfolioService(settings).snapshot()
    assert snap["data_origin"] == "seeded_real_facts"
    assert snap["current_data"] is False
    assert snap["historical_only"] is True
    assert "не используется" in snap["stale_reason"]


def test_historical_portfolio_cannot_generate_current_sku_money_decisions():
    p = {
        "data_origin": "seeded_real_facts",
        "current_data": False,
        "period": "09.09–16.09.2026",
        "stale_reason": "архив",
        "source_health": [],
        "stores": [{"id": "x", "name": "X", "profit_rub": -100, "margin_pct": -10, "drr_pct": 30}],
        "own_27": {
            "products": [{
                "sku": "123",
                "name": "TD-TEST",
                "profit_rub": -5,
                "margin_pct": -2,
                "drr_pct": 20,
                "price_rub": 300,
            }]
        },
    }
    cards = DecisionEngine(load_policy()).build(p, {})
    keys = {x.decision_key for x in cards}
    assert "data:portfolio_historical_only" in keys
    assert not any(k.startswith("sku:123:") for k in keys)
    assert not any(k.startswith("store:x:") for k in keys)


def test_dashboard_renderers_do_not_turn_missing_values_into_zero():
    js = (Path(__file__).parents[1] / "src" / "wb_control_center" / "ui" / "dashboard.js").read_text(encoding="utf-8")
    assert "function missing(v)" in js
    assert "if(missing(v)) return '—'" in js
    assert "Number(null)" not in js


def test_dashboard_uses_seller_article_and_drilldowns():
    js = (Path(__file__).parents[1] / "src" / "wb_control_center" / "ui" / "dashboard.js").read_text(encoding="utf-8")
    html = (Path(__file__).parents[1] / "src" / "wb_control_center" / "ui" / "dashboard.html").read_text(encoding="utf-8")
    assert "function entityName(id)" in js
    assert "Артикул продавца" in html
    assert 'data-event-id=' in js
    assert 'data-decision-key=' in js
    assert 'id="period-from"' in html
    assert 'id="period-to"' in html


def test_dashboard_russian_operator_labels():
    html = (Path(__file__).parents[1] / "src" / "wb_control_center" / "ui" / "dashboard.html").read_text(encoding="utf-8")
    assert "AI DIRECTOR" not in html
    assert ">SHADOW<" not in html
    assert "NO WRITES" not in html
    assert "19 MODULES" not in html


def test_dashboard_has_multistore_advertising_funnel_and_price_scenario_tools():
    root = Path(__file__).parents[1]
    html = (root / "src" / "wb_control_center" / "ui" / "dashboard.html").read_text(encoding="utf-8")
    js = (root / "src" / "wb_control_center" / "ui" / "dashboard.js").read_text(encoding="utf-8")
    worker = (root / "src" / "wb_control_center" / "worker_source.py").read_text(encoding="utf-8")
    assert 'data-page-panel="funnel"' in html and 'data-page-panel="price"' in html
    assert 'id="traffic-store-select"' in html and 'id="funnel-store-select"' in html
    assert 'id="traffic-ads-table"' in html and 'id="funnel-product-table"' in html
    assert "function renderFunnel()" in js and "function renderPriceCalculator()" in js
    assert "wb-price-scenario-v1:" in js
    assert "async def traffic_bundle" in worker and "organic_sku_day.csv" in worker


def test_live_card_identity_overrides_historical_fallback():
    from wb_control_center.api import _entity_map
    portfolio = {"own_27": {"products": [{"sku": "111", "name": "СТАРЫЙ-АРТИКУЛ"}]}}
    snapshots = {
        "cards": {
            "card_catalog": {
                "created_at": "2026-09-25T00:00:00+00:00",
                "data": {"cards": [{"nmID": 111, "vendorCode": "TD-LIVE", "title": "Живой товар"}]},
            }
        }
    }
    mapped = _entity_map(portfolio, snapshots)
    assert mapped["111"]["seller_article"] == "TD-LIVE"
    assert mapped["111"]["display"] == "TD-LIVE"
    assert mapped["111"]["source"] == "live_wb_cards"


def test_worker_traffic_dashboard_keeps_shop_breakdown_and_uses_wb_attribution():
    from wb_control_center.api import _traffic_dashboard_payload

    result = _traffic_dashboard_payload({
        "source": "wb-api-worker",
        "retrieved_at": "2026-10-28T10:00:00+00:00",
        "source_health": {"status": "FRESH"},
        "status": {"shops": {"AP": {"ok": True}, "AA": {"ok": False}}},
        "ads": [
            {"shop": "ap", "date": "2026-10-27", "nm_id": "123", "views": "100", "clicks": "10", "atbs": "3", "orders": "2", "spend_rub": "50", "order_sum_rub": "500"},
            {"shop": "aa", "date": "2026-10-27", "views": "bad", "clicks": "0", "orders": "0", "spend_rub": "0", "order_sum_rub": "0"},
            {"shop": "yv", "date": "2026-10-20", "views": "999", "clicks": "9"},
        ],
        "funnel": [{"shop": "ap", "date": "2026-10-27", "nm_id": "123", "open_count": "20", "cart_count": "5", "order_count": "2"}],
        "organic": [],
    }, {"from": "2026-10-27", "to": "2026-10-28"})
    stores = {x["id"]: x for x in result["shops"]}
    assert set(stores) == {"sanych", "air", "hozyushka"}
    assert stores["sanych"]["summary"]["spend_rub"] == 50
    assert stores["sanych"]["summary"]["ctr_pct"] == 10
    assert stores["sanych"]["summary"]["drr_pct"] == 10
    assert stores["air"]["summary"]["views"] is None
    assert len(result["ads"]) == 2
    assert result["funnel"][0]["cart_count"] == "5"
    assert result["product_day_comparison"][0]["status"] == "both_sources"
    assert result["product_day_summary"]["matched_pairs"] == 1


def test_traffic_product_day_comparison_joins_exact_keys_without_claiming_attribution():
    from wb_control_center.api import _traffic_product_day_comparison

    rows, summary = _traffic_product_day_comparison(
        ads=[
            {"shop": "ap", "date": "2026-10-27", "nm_id": "123", "vendor_code": "SKU-A", "advert_id": "9", "views": "50", "clicks": "5", "orders": "1", "spend_rub": "30", "order_sum_rub": "200"},
            {"shop": "ap", "date": "2026-10-27", "nm_id": "123", "vendor_code": "SKU-A", "advert_id": "10", "views": "50", "clicks": "5", "orders": "1", "spend_rub": "20", "order_sum_rub": "300"},
            {"shop": "aa", "date": "2026-10-27", "nm_id": "123", "views": "10", "clicks": "1", "orders": "0", "spend_rub": "2", "order_sum_rub": "0"},
            {"shop": "ap", "date": "2026-10-27", "vendor_code": "NO-NMID", "views": "7"},
        ],
        funnel=[
            {"shop": "ap", "date": "27.10.2026", "nm_id": "123", "vendor_code": "SKU-A", "open_count": "20", "cart_count": "5", "order_count": "2", "order_sum_rub": "500"},
            {"shop": "yv", "date": "2026-10-27", "nm_id": "777", "vendor_code": "SKU-B", "open_count": "8", "cart_count": "2", "order_count": "1", "order_sum_rub": "100"},
        ],
    )

    by_key = {(row["store_id"], row["nm_id"]): row for row in rows}
    matched = by_key[("sanych", "123")]
    assert matched["status"] == "both_sources"
    assert matched["ad_views"] == 100
    assert matched["ad_spend_rub"] == 50
    assert matched["ad_orders"] == 2
    assert matched["funnel_orders"] == 2
    assert by_key[("air", "123")]["status"] == "ads_only"
    assert by_key[("hozyushka", "777")]["status"] == "funnel_only"
    assert summary == {"matched_pairs": 1, "ads_only": 1, "funnel_only": 1, "unlinked_ads": 1, "unlinked_funnel": 0}


def test_funnel_page_explains_separate_ad_and_funnel_measures():
    root = Path(__file__).parents[1]
    html = (root / "src" / "wb_control_center" / "ui" / "dashboard.html").read_text(encoding="utf-8")
    js = (root / "src" / "wb_control_center" / "ui" / "dashboard.js").read_text(encoding="utf-8")
    assert 'id="funnel-traffic-join-table"' in html
    assert "точные совпадения магазин × день × nmID" in html
    assert "Числа заказов из двух источников не складываются." in js


def test_sheets_ads_hub_traffic_fallback_keeps_store_and_source_status():
    from wb_control_center.portfolio import _traffic_bundle_from_bridge

    bundle = _traffic_bundle_from_bridge({
        "generated_at": "2026-10-28T10:00:00+03:00",
        "sources": {"ads_hub": {"read_at": "2026-10-28T10:00:00+03:00", "ranges": {
            "campaign_day": {"values": [
                ["store_id", "date", "advert_id", "nmId", "views", "clicks", "atbs", "orders", "spend_rub", "order_sum_rub", "source", "quality", "seller_article", "load_timestamp", "coverage_status"],
                ["SANYCH", "2026-10-27", "9", "123", "100", "10", "3", "2", "50", "500", "WB", "FACT", "SKU-A", "2026-10-28T09:00:00+03:00", "SOURCE_OK"],
            ]},
            "funnel_day": {"values": [
                ["store_id", "date", "nmId", "vendorCode", "open_count", "cart_count", "order_count", "order_sum_rub", "source", "load_timestamp", "coverage_status"],
                ["SANYCH", "27.10.2026", "123", "SKU-A", "20", "5", "2", "500", "WB", "28.10.2026", "SOURCE_OK"],
            ]},
            "health": {"values": [
                ["Магазин", "Кампаний / SKU строк", "Последняя дата рекламы", "Строк воронки", "Последняя дата воронки", "Статус", "Комментарий"],
                ["ap", "1436", "27.10.2026", "1098", "27.10.2026", "АКТУАЛЬНО", "проверено"],
                ["aa", "750", "12.10.2026", "819", "12.10.2026", "УСТАРЕЛО", "проверено"],
            ]},
        }}}
    })
    assert bundle["source"] == "google_sheets_ads_hub"
    assert bundle["ads"][0]["shop"] == "ap"
    assert bundle["ads"][0]["nm_id"] == "123"
    assert bundle["ads"][0]["date"] == "2026-10-27"
    assert bundle["status"]["shops"]["ap"]["ok"] is True
    assert bundle["status"]["shops"]["aa"]["status"] == "STALE"


@pytest.mark.asyncio
async def test_mcp_json_payload_survives_human_note(tmp_path):
    import json
    from types import SimpleNamespace
    from wb_control_center.mcp_client import WBMCPClient

    class FakeSession:
        async def call_tool(self, tool, arguments=None):
            return SimpleNamespace(
                isError=False,
                structured_content=None,
                content=[
                    SimpleNamespace(text=json.dumps({"cards": [{"nmID": 111, "vendorCode": "TD-1"}]})),
                    SimpleNamespace(text="Вернулось ровно 100 записей — данные неполные."),
                ],
            )

    client = WBMCPClient("token", str(tmp_path))
    client.session = FakeSession()
    client.tools = {"wb_cards_list"}
    payload = await client.call("wb_cards_list", {"limit": 100})
    assert payload["cards"][0]["vendorCode"] == "TD-1"
    assert payload["_mcp_notes"]


def test_production_image_contains_version_file():
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")
    assert "VERSION" in dockerfile


def test_dashboard_period_defaults_to_last_completed_week_and_exposes_date_limit(monkeypatch):
    from wb_control_center import api as api_module

    period = api_module._dashboard_period(days=7)
    start = date.fromisoformat(period["from"])
    end = date.fromisoformat(period["to"])
    max_day = date.fromisoformat(period["max_selectable"])

    assert (end - start).days == 6
    assert end < max_day
    assert period["default_mode"] == "last_completed_7_days"


def test_dashboard_uses_inline_favicon_to_avoid_browser_404():
    html = (Path(__file__).parents[1] / "src" / "wb_control_center" / "ui" / "dashboard.html").read_text(encoding="utf-8")
    assert '<link rel="icon" href="data:," />' in html


def test_dashboard_js_blocks_future_periods_and_sets_calendar_max():
    js = (Path(__file__).parents[1] / "src" / "wb_control_center" / "ui" / "dashboard.js").read_text(encoding="utf-8")
    assert "max_selectable" in js
    assert "Будущие даты выбрать нельзя." in js
    assert "$('period-from').max=maxDate" in js
    assert "$('period-to').max=maxDate" in js
