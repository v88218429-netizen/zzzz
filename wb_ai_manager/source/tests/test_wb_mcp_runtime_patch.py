from types import SimpleNamespace

import pytest
from wb_control_center.wb_mcp_runtime_patch import (
    _advert_clusters_list,
    _advert_clusters_stats,
    _advert_cluster_bids_get,
    apply_wb_mcp_hotfixes,
    apply_wb_mcp_server_hotfixes,
)
from wb_mcp.client import WBClient


@pytest.mark.asyncio
async def test_cluster_list_uses_exact_campaign_and_nm_ids():
    calls = []

    async def post(base, path, body):
        calls.append((base, path, body))
        return {"items": []}

    client = SimpleNamespace(_advert="promotion", _post=post)
    result = await _advert_clusters_list(client, 123, [456, 789, 456])

    assert result == {"items": []}
    assert calls == [(
        "promotion",
        "/adv/v0/normquery/list",
        {"items": [{"advertId": 123, "nmId": 456}, {"advertId": 123, "nmId": 789}]},
    )]


@pytest.mark.asyncio
async def test_cluster_stats_uses_current_wb_v1_schema_and_dates():
    calls = []

    async def post(base, path, body):
        calls.append((base, path, body))
        return {"items": []}

    client = SimpleNamespace(_advert="promotion", _post=post)
    await _advert_clusters_stats(client, 123, "2026-10-01", "2026-10-14", [456], daily=True)

    assert calls == [(
        "promotion",
        "/adv/v1/normquery/stats",
        {
            "from": "2026-10-01",
            "to": "2026-10-14",
            "items": [{"advertId": 123, "nmId": 456}],
        },
    )]


@pytest.mark.asyncio
async def test_get_cluster_bids_reads_exact_campaign_sku_pairs():
    calls = []

    async def post(base, path, body):
        calls.append((base, path, body))
        return {"bids": [{"advert_id": 123, "nm_id": 456, "norm_query": "фраза", "bid_kopecks": 12345}]}

    client = SimpleNamespace(_advert="promotion", _post=post)
    result = await _advert_cluster_bids_get(client, 123, [456, 789, 456])

    assert result["bids"][0]["bid_kopecks"] == 12345
    assert calls == [(
        "promotion",
        "/adv/v0/normquery/get-bids",
        {"items": [{"advert_id": 123, "nm_id": 456}, {"advert_id": 123, "nm_id": 789}]},
    )]


@pytest.mark.asyncio
async def test_cluster_stats_selects_v0_when_daily_rows_are_not_requested():
    calls = []

    async def post(base, path, body):
        calls.append((base, path, body))
        return {}

    client = SimpleNamespace(_advert="promotion", _post=post)
    await _advert_clusters_stats(client, 123, "2026-10-01", "2026-10-14", [456], daily=False)

    assert calls[0][1] == "/adv/v0/normquery/stats"
    assert calls[0][2]["items"] == [{"advertId": 123, "nmId": 456}]


@pytest.mark.asyncio
async def test_cluster_methods_reject_missing_or_invalid_product_scope():
    client = SimpleNamespace(_advert="promotion", _post=None)

    with pytest.raises(ValueError, match="nm_ids"):
        await _advert_clusters_list(client, 123, [])

    with pytest.raises(ValueError, match="positive integers"):
        await _advert_clusters_stats(client, 123, "2026-10-01", "2026-10-14", [0], daily=True)

    with pytest.raises(ValueError, match="date_from"):
        await _advert_clusters_stats(client, 123, "not-a-date", "2026-10-14", [456], daily=True)


@pytest.mark.asyncio
async def test_server_schema_and_dispatch_include_exact_nm_ids():
    tool = SimpleNamespace(
        name="wb_advert_clusters",
        inputSchema={"type": "object", "properties": {"advert_id": {"type": "integer"}}, "required": ["advert_id"]},
    )
    server = SimpleNamespace(TOOLS=[tool], CLIENT_DISPATCH={})

    apply_wb_mcp_server_hotfixes(server)

    assert tool.inputSchema["properties"]["nm_ids"] == {
        "type": "array",
        "items": {"type": "integer"},
        "description": "WB item IDs scoped to this campaign",
    }
    assert tool.inputSchema["required"] == ["advert_id"]

    calls = []

    class Client:
        async def advert_clusters_list(self, advert_id, nm_ids):
            calls.append((advert_id, nm_ids))
            return {"items": []}

    await server.CLIENT_DISPATCH["wb_advert_clusters"](Client(), {"advert_id": 123, "nm_ids": [456]})
    assert calls == [(123, [456])]

@pytest.mark.asyncio
async def test_server_exposes_read_only_cluster_bid_tool():
    cluster_tool = SimpleNamespace(
        name="wb_advert_clusters",
        inputSchema={"type": "object", "properties": {"advert_id": {"type": "integer"}}, "required": ["advert_id"]},
    )
    server = SimpleNamespace(TOOLS=[cluster_tool], CLIENT_DISPATCH={})
    apply_wb_mcp_server_hotfixes(server)

    tool = next(item for item in server.TOOLS if item.name == "wb_advert_cluster_bids_get")
    assert tool.inputSchema["required"] == ["advert_id", "nm_ids"]
    assert "Read-only" in tool.description

    calls = []

    class Client:
        async def advert_cluster_bids_get(self, advert_id, nm_ids):
            calls.append((advert_id, nm_ids))
            return {"bids": []}

    await server.CLIENT_DISPATCH[tool.name](Client(), {"advert_id": 321, "nm_ids": [654]})
    assert calls == [(321, [654])]


@pytest.mark.asyncio
async def test_runtime_patch_updates_wb_mcp_request_contracts():
    apply_wb_mcp_hotfixes()
    client = WBClient("x")
    calls = []

    async def fake_get(session, path, params=None):
        calls.append((str(session.base_url), path, params))
        return {"data": {"promotions": []}}

    client._get = fake_get
    await client.promotions_list("2026-09-21", "2026-09-27")
    await client.analytics_deductions(
        date_from="2026-09-21", date_to="2026-09-27", limit=1000
    )
    await client.documents_list(date_from="2026-09-21", date_to="2026-09-27", limit=100)
    await client.seller_rating()

    promo = calls[0]
    assert promo[2]["startDateTime"] == "2026-09-21T00:00:00Z"
    assert promo[2]["endDateTime"] == "2026-09-27T23:59:59Z"

    deductions = calls[1]
    assert deductions[2]["dateFrom"] == "2026-09-21T00:00:00Z"
    assert deductions[2]["limit"] == 1000 and deductions[2]["offset"] == 0

    docs = calls[2]
    assert docs[2]["beginTime"] == "2026-09-21"
    assert docs[2]["endTime"] == "2026-09-27"
    assert "dateFrom" not in docs[2] and "dateTo" not in docs[2]

    rating = calls[3]
    assert rating[0].startswith("https://feedbacks-api.wildberries.ru")
    assert rating[1] == "/api/common/v1/rating"
    await client.close()
