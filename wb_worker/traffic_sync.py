"""Read-only WB advertising and funnel snapshots; no campaign mutations."""
import asyncio
import csv
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data")) / "traffic"
SHOPS = {
    "ap": ("ИП АП", "WB_API_TOKEN_AP"),
    "aa": ("ИП АА", "WB_API_TOKEN_AA"),
    "yv": ("ИП ЮВ", "WB_API_TOKEN_YV"),
}
PROMO = "https://advert-api.wildberries.ru"
ANALYTICS = "https://seller-analytics-api.wildberries.ru"
COLUMNS = ["shop", "date", "advert_id", "nm_id", "views", "clicks", "atbs", "orders", "spend_rub", "order_sum_rub", "source", "quality", "seller_article"]
FUNNEL_COLUMNS = ["shop", "date", "nm_id", "vendor_code", "open_count", "cart_count", "order_count", "order_sum_rub", "source"]
INTERVAL = max(60, int(os.environ.get("TRAFFIC_SYNC_INTERVAL_MIN", "360")))
_last_fullstats = 0.0


def _atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def _atomic_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)
    temp.replace(path)


async def _request(client, token, method, url, **kwargs):
    headers = {"Authorization": token}
    for attempt in range(5):
        response = await client.request(method, url, headers=headers, **kwargs)
        if response.status_code in (429, 500, 502, 503, 504) and attempt < 4:
            try:
                wait = float(response.headers.get("Retry-After") or response.headers.get("X-Ratelimit-Retry") or 2 ** attempt * 3)
            except ValueError:
                wait = 2 ** attempt * 3
            await asyncio.sleep(min(max(wait, 1), 120))
            continue
        response.raise_for_status()
        return [] if response.status_code == 204 else response.json()
    raise RuntimeError("retry exhausted")


def _campaign_ids(payload):
    ids = set()
    for group in payload.get("adverts", []):
        if int(group.get("status", 0)) not in (7, 9, 11):
            continue
        for item in group.get("advert_list", []):
            if item.get("advertId"):
                ids.add(int(item["advertId"]))
    return sorted(ids)


def _stats_rows(shop, payload):
    # Fullstats can split a product/day across appType platforms. The HUB
    # grain is campaign x product x date, so sum those disjoint counters.
    grouped = {}
    for campaign in payload if isinstance(payload, list) else []:
        aid = campaign.get("advertId")
        for day in campaign.get("days", []):
            date = str(day.get("date", ""))[:10]
            for app in day.get("apps", []):
                for item in app.get("nms", []):
                    nm = item.get("nmId") or item.get("nmID")
                    if not (aid and nm and date):
                        continue
                    key = (shop, date, aid, nm)
                    values = grouped.setdefault(key, [0] * 6)
                    for index, field in enumerate(("views", "clicks", "atbs", "orders", "sum", "sum_price")):
                        values[index] += item.get(field) or 0
    return [[*key, *values, "WB /adv/v3/fullstats",
             "EXACT_CAMPAIGN_NM_DAY", ""] for key, values in sorted(grouped.items())]


def _funnel_rows(shop, payload):
    rows = []
    for item in payload if isinstance(payload, list) else []:
        product = item.get("product") or {}
        nm = product.get("nmId")
        for day in item.get("history") or []:
            if nm and day.get("date"):
                rows.append([shop, str(day["date"])[:10], nm, product.get("vendorCode", ""),
                             day.get("openCount"), day.get("cartCount"), day.get("orderCount"),
                             day.get("orderSum"), "WB Analytics products/history"])
    return rows


async def sync_once():
    global _last_fullstats
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    begin = (now.date() - timedelta(days=6)).isoformat()
    end = now.date().isoformat()
    status = {"startedAt": now.isoformat(), "period": [begin, end], "shops": {}}
    ad_rows, funnel_rows = [], []
    async with httpx.AsyncClient(timeout=90) as client:
        for shop, (_, env_name) in SHOPS.items():
            token = os.environ.get(env_name, "").strip()
            if not token:
                status["shops"][shop] = {"ok": False, "error": "token not configured"}
                continue
            state = {"ok": True, "campaigns": 0, "ad_rows": 0, "funnel_rows": 0, "errors": {}}
            # Each source has independent health. An error never promotes stale data.
            try:
                listing = await _request(client, token, "GET", PROMO + "/adv/v1/promotion/count")
                ids = _campaign_ids(listing)
                state["campaigns"] = len(ids)
                shop_rows = []
                for start in range(0, len(ids), 50):
                    loop = asyncio.get_running_loop()
                    delay = 20 - (loop.time() - _last_fullstats)
                    if _last_fullstats and delay > 0:
                        await asyncio.sleep(delay)
                    raw = await _request(client, token, "GET", PROMO + "/adv/v3/fullstats",
                                         params={"ids": ",".join(map(str, ids[start:start + 50])),
                                                 "beginDate": begin, "endDate": end})
                    _last_fullstats = loop.time()
                    _atomic_json(DATA_DIR / shop / f"fullstats_{start // 50}.json", raw)
                    shop_rows.extend(_stats_rows(shop, raw))
                ad_rows.extend(shop_rows)
                state["ad_rows"] = len(shop_rows)
            except Exception as exc:
                state["ok"] = False
                state["errors"]["promotion"] = f"{type(exc).__name__}: {exc}"
            try:
                # History requires 1..20 explicit nmIds; [] is a WB 400.
                nm_ids = sorted({int(row[3]) for row in shop_rows})
                shop_funnel = []
                for start in range(0, len(nm_ids), 20):
                    if start:
                        await asyncio.sleep(20)
                    raw = await _request(
                        client, token, "POST",
                        ANALYTICS + "/api/analytics/v3/sales-funnel/products/history",
                        json={"selectedPeriod": {"start": begin, "end": end},
                              "nmIds": nm_ids[start:start + 20],
                              "skipDeletedNm": True, "aggregationLevel": "day"})
                    _atomic_json(DATA_DIR / shop / f"funnel_{start // 20}.json", raw)
                    shop_funnel.extend(_funnel_rows(shop, raw))
                funnel_rows.extend(shop_funnel)
                state["funnel_rows"] = len(shop_funnel)
                state["funnel_nm_ids"] = len(nm_ids)
            except Exception as exc:
                state["ok"] = False
                state["errors"]["funnel"] = f"{type(exc).__name__}: {exc}"
            status["shops"][shop] = state
    vendor_map = {(r[0], str(r[2])): r[3] for r in funnel_rows if r[3]}
    for row in ad_rows:
        row[-1] = vendor_map.get((row[0], str(row[3])), "")
    # Partial failures are explicit in status; never interpret missing rows as zero.
    _atomic_csv(DATA_DIR / "campaign_sku_day.csv", COLUMNS, ad_rows)
    _atomic_csv(DATA_DIR / "funnel_sku_day.csv", FUNNEL_COLUMNS, funnel_rows)
    status["finishedAt"] = datetime.now(ZoneInfo("Europe/Moscow")).isoformat()
    status["ok"] = all(s["ok"] for s in status["shops"].values())
    _atomic_json(DATA_DIR / "status.json", status)
    print("WB_TRAFFIC_SYNC_DONE " + json.dumps(status, ensure_ascii=False), flush=True)
    return status


async def sync_loop():
    while True:
        try:
            await sync_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _atomic_json(DATA_DIR / "status.json", {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        await asyncio.sleep(INTERVAL * 60)
