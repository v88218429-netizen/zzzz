"""Read-only current WB campaign settings and SKU bids; separate from daily facts."""
from __future__ import annotations

import asyncio
import csv
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from traffic_sync import DATA_DIR, PROMO, SHOPS, _atomic_csv, _atomic_json, _request

COLUMNS = [
    "shop", "advert_id", "nm_id", "campaign_name", "status",
    "bid_type", "payment_type", "currency", "search_bid_kopecks",
    "recommendation_bid_kopecks", "search_placement", "recommendation_placement",
    "updated_at", "observed_at", "source", "quality",
]
SOURCE = "WB /api/advert/v2/adverts"
QUALITY = "FACT_CURRENT_CAMPAIGN_SKU_SETTINGS"


def parse(shop: str, obj: object, observed_at: str) -> list[list]:
    if not isinstance(obj, dict) or not isinstance(obj.get("adverts"), list):
        raise ValueError("Unexpected campaign settings response")
    result, seen = [], set()
    for advert in obj["adverts"]:
        if not isinstance(advert, dict):
            raise ValueError("Invalid campaign settings item")
        advert_id = advert.get("id")
        status = advert.get("status")
        settings = advert.get("settings") or {}
        timestamps = advert.get("timestamps") or {}
        placements = settings.get("placements") or {}
        try:
            ad_id = int(advert_id)
            state = int(status)
            if ad_id <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError("Campaign id/status is missing or invalid")
        for item in advert.get("nm_settings") or []:
            try:
                nm = int(item["nm_id"])
                if nm <= 0:
                    raise ValueError()
            except (KeyError, ValueError, TypeError):
                raise ValueError("Campaign SKU is invalid")
            key = (shop, ad_id, nm)
            if key in seen:
                raise ValueError("Duplicate campaign SKU")
            seen.add(key)
            bids = item.get("bids_kopecks") or {}
            def bid(field):
                raw = bids.get(field)
                if raw is None or raw == "":
                    return ""
                value = int(raw)
                if value < 0:
                    raise ValueError("Negative bid")
                return value
            result.append([
                shop, ad_id, nm, str(settings.get("name") or ""), state,
                str(advert.get("bid_type") or ""),
                str(settings.get("payment_type") or ""),
                str(advert.get("currency") or ""),
                bid("search"), bid("recommendations"),
                bool(placements.get("search")) if "search" in placements else "",
                bool(placements.get("recommendations")) if "recommendations" in placements else "",
                str(timestamps.get("updated") or ""), observed_at, SOURCE, QUALITY
            ])
    return result


def read_shop(shop: str) -> list[list[str]]:
    path = DATA_DIR / shop / "campaign_settings_current.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != COLUMNS:
            raise ValueError("Campaign settings schema mismatch")
        return [[r[c] for c in COLUMNS] for r in reader]


async def collect() -> dict:
    now = datetime.now(ZoneInfo("Europe/Moscow")).isoformat()
    status = {"observed_at": now, "shops": {}}
    async with httpx.AsyncClient(timeout=90) as client:
        for shop, (_, token_name) in SHOPS.items():
            token = os.environ.get(token_name, "").strip()
            info = {"ok": False, "new_rows": 0}
            try:
                if not token:
                    raise RuntimeError("WB promotion token unavailable")
                # One call per cabinet; the endpoint can have a one-hour
                # restriction for Basic tokens. No aggressive retry loops.
                obj = await _request(client, token, "GET",
                                     PROMO + "/api/advert/v2/adverts",
                                     params={"statuses": "4,7,9,11"})
                rows = parse(shop, obj, now)
                if not rows:
                    raise RuntimeError("Campaign settings returned zero product rows")
                _atomic_csv(DATA_DIR / shop / "campaign_settings_current.csv", COLUMNS, rows)
                info.update({"ok": True, "new_rows": len(rows)})
            except Exception as exc:
                info["error"] = f"{type(exc).__name__}: {exc}"
                info["retained_previous_rows"] = len(read_shop(shop))
            status["shops"][shop] = info
    all_rows = [row for shop in SHOPS for row in read_shop(shop)]
    _atomic_csv(DATA_DIR / "campaign_settings_current.csv", COLUMNS, all_rows)
    status["ok"] = all(v["ok"] for v in status["shops"].values())
    status["rows"] = len(all_rows)
    _atomic_json(DATA_DIR / "campaign_settings_status.json", status)
    return status


def publish(bridge_url: str, key: str) -> dict:
    if not key or not bridge_url:
        raise RuntimeError("Authenticated Sheets bridge configuration missing")
    path = DATA_DIR / "campaign_settings_status.json"
    if not path.is_file():
        raise RuntimeError("Campaign settings collection status missing")
    state = json.loads(path.read_text(encoding="utf-8"))
    result = {}
    with httpx.Client(timeout=httpx.Timeout(160.0, connect=15.0), follow_redirects=True) as client:
        for shop in SHOPS:
            info = state.get("shops", {}).get(shop) or {}
            if not info.get("ok"):
                result[shop] = {"published": False, "reason": "source_not_verified"}
                continue
            rows = read_shop(shop)
            resp = client.post(bridge_url, json={
                "token": key, "action": "publish_campaign_settings",
                "shop": shop, "source": SOURCE, "quality": QUALITY,
                "rows": rows, "observed_at": state["observed_at"],
            })
            resp.raise_for_status()
            data = resp.json()
            if data.get("ok") is not True or data.get("action") != "publish_campaign_settings" or data.get("written") != len(rows):
                raise RuntimeError(f"Campaign settings publication failed for {shop}")
            result[shop] = {"published": True, "rows": len(rows)}
    return result
