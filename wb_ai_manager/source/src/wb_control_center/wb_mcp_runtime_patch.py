from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mcp.types import Tool
from wb_mcp.client import WBClient


def _rfc3339_day(value: str, *, end: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        return text
    if "T" in text:
        return text
    return f"{text}T{'23:59:59' if end else '00:00:00'}Z"


def _rfc3339_not_future(value: str) -> str:
    text = _rfc3339_day(value, end=True)
    try:
        requested = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    now = datetime.now(timezone.utc)
    if requested > now:
        return now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return text


async def _promotions_list(
    self: WBClient,
    start: str,
    end: str,
    all_promo: bool = False,
    limit: int = 1000,
    offset: int = 0,
    promo_type: str | None = None,
) -> dict:
    resp = await self._get(
        self._calendar,
        "/api/v1/calendar/promotions",
        {
            "startDateTime": _rfc3339_day(start),
            "endDateTime": _rfc3339_day(end, end=True),
            "allPromo": str(all_promo).lower(),
            "limit": limit,
            "offset": offset,
        },
    )
    if promo_type in ("auto", "regular"):
        promos = (resp.get("data") or {}).get("promotions") or resp.get("promotions") or []
        filtered = [p for p in promos if p.get("type") == promo_type]
        return {"promotions": filtered, "total": len(filtered), "filteredBy": promo_type}
    return resp


async def _analytics_deductions(
    self: WBClient,
    date_to: str,
    date_from: str,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    return await self._get(
        self._analytics,
        "/api/analytics/v1/deductions",
        {
            "dateFrom": _rfc3339_day(date_from),
            "dateTo": _rfc3339_not_future(date_to),
            "sort": "dtBonus",
            "order": "desc",
            "limit": limit,
            "offset": offset,
        },
    )


async def _documents_list(
    self: WBClient,
    date_from: str | None = None,
    date_to: str | None = None,
    category_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    params: dict[str, Any] = {"locale": "ru", "sort": "date", "order": "desc"}
    if date_from:
        params["beginTime"] = str(date_from)[:10]
    if date_to:
        params["endTime"] = str(date_to)[:10]
    return await self._get(self._documents, "/api/v1/documents/list", params)


async def _seller_rating(self: WBClient) -> dict:
    return await self._get(self._feedbacks, "/api/common/v1/rating")


def _campaign_and_nm_ids(advert_id: Any, nm_ids: Any) -> tuple[int, list[int]]:
    if isinstance(advert_id, bool):
        raise ValueError("advert_id must be a positive integer")
    try:
        campaign_id = int(advert_id)
    except (TypeError, ValueError):
        raise ValueError("advert_id must be a positive integer") from None
    if campaign_id <= 0:
        raise ValueError("advert_id must be a positive integer")
    if not isinstance(nm_ids, (list, tuple)):
        raise ValueError("nm_ids must contain one or more positive integers")
    normalized: list[int] = []
    for value in nm_ids:
        if isinstance(value, bool):
            raise ValueError("nm_ids must contain only positive integers")
        try:
            nm_id = int(value)
        except (TypeError, ValueError):
            raise ValueError("nm_ids must contain only positive integers") from None
        if nm_id <= 0:
            raise ValueError("nm_ids must contain only positive integers")
        if nm_id not in normalized:
            normalized.append(nm_id)
    if not normalized:
        raise ValueError("nm_ids must contain one or more positive integers")
    if len(normalized) > 100:
        raise ValueError("nm_ids cannot contain more than 100 items")
    return campaign_id, normalized


def _iso_date(value: Any, field: str) -> str:
    text = str(value or "").strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise ValueError(f"{field} must use YYYY-MM-DD") from None


async def _advert_clusters_list(self: WBClient, advert_id: int, nm_ids: list[int]) -> dict:
    campaign_id, item_ids = _campaign_and_nm_ids(advert_id, nm_ids)
    body = {"items": [{"advertId": campaign_id, "nmId": nm_id} for nm_id in item_ids]}
    return await self._post(self._advert, "/adv/v0/normquery/list", body)


async def _advert_clusters_stats(
    self: WBClient,
    advert_id: int,
    date_from: str,
    date_to: str,
    nm_ids: list[int] | None = None,
    daily: bool = False,
) -> dict:
    campaign_id, item_ids = _campaign_and_nm_ids(advert_id, nm_ids)
    start, end = _iso_date(date_from, "date_from"), _iso_date(date_to, "date_to")
    if start > end:
        raise ValueError("date_from must not be later than date_to")
    body = {
        "from": start,
        "to": end,
        "items": [{"advertId": campaign_id, "nmId": nm_id} for nm_id in item_ids],
    }
    path = "/adv/v1/normquery/stats" if daily else "/adv/v0/normquery/stats"
    return await self._post(self._advert, path, body)



async def _advert_cluster_bids_get(self: WBClient, advert_id: int, nm_ids: list[int]) -> dict:
    """POST /adv/v0/normquery/get-bids: read current bid per campaign × item × cluster."""
    campaign_id, item_ids = _campaign_and_nm_ids(advert_id, nm_ids)
    body = {"items": [{"advert_id": campaign_id, "nm_id": nm_id} for nm_id in item_ids]}
    return await self._post(self._advert, "/adv/v0/normquery/get-bids", body)


async def _cluster_bids_get_handler(client: WBClient, arguments: dict[str, Any]) -> Any:
    return await client.advert_cluster_bids_get(arguments["advert_id"], arguments["nm_ids"])


async def _clusters_tool_handler(client: WBClient, arguments: dict[str, Any]) -> Any:
    return await client.advert_clusters_list(arguments["advert_id"], arguments.get("nm_ids"))


def apply_wb_mcp_server_hotfixes(server: Any) -> None:
    """Expose the exact product scope needed by the current WB cluster API."""
    tool = next((item for item in server.TOOLS if item.name == "wb_advert_clusters"), None)
    if tool is None:
        raise RuntimeError("wb_advert_clusters tool is missing from the installed WB MCP server")
    schema = dict(tool.inputSchema)
    properties = dict(schema.get("properties") or {})
    properties["nm_ids"] = {
        "type": "array",
        "items": {"type": "integer"},
        "description": "WB item IDs scoped to this campaign",
    }
    schema["properties"] = properties
    tool.inputSchema = schema
    server.CLIENT_DISPATCH["wb_advert_clusters"] = _clusters_tool_handler

    bids_tool_name = "wb_advert_cluster_bids_get"
    if not any(item.name == bids_tool_name for item in server.TOOLS):
        server.TOOLS.append(Tool(
            name=bids_tool_name,
            description="Read current per-cluster bids by campaign and product. Read-only; no bid changes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "shop_id": {"type": "string"},
                    "advert_id": {"type": "integer", "minimum": 1},
                    "nm_ids": {
                        "type": "array", "items": {"type": "integer", "minimum": 1},
                        "minItems": 1, "maxItems": 100,
                    },
                },
                "required": ["advert_id", "nm_ids"],
            },
        ))
    server.CLIENT_DISPATCH[bids_tool_name] = _cluster_bids_get_handler


def apply_wb_mcp_hotfixes() -> None:
    WBClient.promotions_list = _promotions_list
    WBClient.analytics_deductions = _analytics_deductions
    WBClient.documents_list = _documents_list
    WBClient.seller_rating = _seller_rating
    WBClient.advert_clusters_list = _advert_clusters_list
    WBClient.advert_clusters_stats = _advert_clusters_stats
    WBClient.advert_cluster_bids_get = _advert_cluster_bids_get
