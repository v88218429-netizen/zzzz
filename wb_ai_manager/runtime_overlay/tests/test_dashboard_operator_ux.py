from pathlib import Path

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
