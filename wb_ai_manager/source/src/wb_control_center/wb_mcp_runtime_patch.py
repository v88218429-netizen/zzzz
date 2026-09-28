from __future__ import annotations

from typing import Any

from wb_mcp.client import WBClient


def _rfc3339_day(value: str, *, end: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        return text
    if "T" in text:
        return text
    return f"{text}T{'23:59:59' if end else '00:00:00'}Z"


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
            "dateTo": _rfc3339_day(date_to, end=True),
            "sort": "dtBonus",
            "order": "desc",
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
    return await self._get(self._tariffs, "/api/common/v1/rating")


def apply_wb_mcp_hotfixes() -> None:
    WBClient.promotions_list = _promotions_list
    WBClient.analytics_deductions = _analytics_deductions
    WBClient.documents_list = _documents_list
    WBClient.seller_rating = _seller_rating
