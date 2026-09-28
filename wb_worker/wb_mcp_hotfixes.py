from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

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
    # Current WB API accepts date-time plus sort/order. limit/offset from older
    # client versions cause a 400 and are intentionally not sent.
    return await self._get(
        self._analytics,
        "/api/analytics/v1/deductions",
        {
            "dateFrom": _rfc3339_day(date_from),
            "dateTo": _rfc3339_not_future(date_to),
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
    # WB renamed dateFrom/dateTo to beginTime/endTime. Pagination arguments are
    # not part of the current endpoint contract.
    params: dict[str, Any] = {"locale": "ru", "sort": "date", "order": "desc"}
    if date_from:
        params["beginTime"] = str(date_from)[:10]
    if date_to:
        params["endTime"] = str(date_to)[:10]
    return await self._get(self._documents, "/api/v1/documents/list", params)


async def _seller_rating(self: WBClient) -> dict:
    # Seller rating uses the Feedbacks API host; the token needs the
    # Feedbacks and Questions category.
    return await self._get(self._feedbacks, "/api/common/v1/rating")


def apply_wb_mcp_hotfixes() -> None:
    WBClient.promotions_list = _promotions_list
    WBClient.analytics_deductions = _analytics_deductions
    WBClient.documents_list = _documents_list
    WBClient.seller_rating = _seller_rating
