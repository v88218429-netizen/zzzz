import pytest

from wb_control_center.wb_mcp_runtime_patch import apply_wb_mcp_hotfixes
from wb_mcp.client import WBClient


@pytest.mark.asyncio
async def test_runtime_patch_updates_wb_mcp_request_contracts():
    apply_wb_mcp_hotfixes()
    c=WBClient("x")
    calls=[]
    async def fake_get(client,path,params=None):
        calls.append((str(client.base_url),path,params))
        return {"data":{"promotions":[]}}
    c._get=fake_get

    await c.promotions_list("2026-09-21","2026-09-27")
    await c.analytics_deductions(date_from="2026-09-21",date_to="2026-09-27",limit=1000)
    await c.documents_list(date_from="2026-09-21",date_to="2026-09-27",limit=100)
    await c.seller_rating()

    promo=calls[0]
    assert promo[2]["startDateTime"] == "2026-09-21T00:00:00Z"
    assert promo[2]["endDateTime"] == "2026-09-27T23:59:59Z"

    deductions=calls[1]
    assert deductions[2]["dateFrom"] == "2026-09-21T00:00:00Z"
    assert "limit" not in deductions[2] and "offset" not in deductions[2]

    docs=calls[2]
    assert docs[2]["beginTime"] == "2026-09-21"
    assert docs[2]["endTime"] == "2026-09-27"
    assert "dateFrom" not in docs[2] and "dateTo" not in docs[2]

    rating=calls[3]
    assert rating[0].startswith("https://common-api.wildberries.ru")
    assert rating[1] == "/api/common/v1/rating"
    await c.close()
