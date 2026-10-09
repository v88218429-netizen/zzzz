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
ADS_POLL_COLUMNS = ["shop", "observed_at", "date", "campaign_sku_rows", "campaigns_with_rows", "views", "clicks", "cart_adds", "orders", "spend_rub", "order_sum_rub", "ctr", "cpc_rub", "drr", "roas", "source", "measurement_grain"]
FUNNEL_POLL_COLUMNS = ["shop", "observed_at", "date", "sku_rows", "open_count", "cart_count", "order_count", "order_sum_rub", "open_to_cart", "cart_to_order", "open_to_order", "source", "measurement_grain"]
POLL_GRAIN = "DAILY_CUMULATIVE_OBSERVED_AT_POLL"
PRICES = "https://discounts-prices-api.wildberries.ru"
FULLSTATS_DAYS = 31
FUNNEL_BATCH_SIZE = 20
INTERVAL = max(60, int(os.environ.get("TRAFFIC_SYNC_INTERVAL_MIN", "60")))
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


def _read_csv_rows(path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        next(reader, None)
        return [row for row in reader if row]


def _upsert_rows(existing, incoming, key_columns):
    """Merge snapshots by their natural grain, retaining data outside API windows."""
    rows = {}
    for row in (*existing, *incoming):
        if len(row) <= max(key_columns):
            continue
        key = tuple(str(row[index]) for index in key_columns)
        if all(key):
            rows[key] = row
    return sorted(rows.values(), key=lambda row: tuple(str(row[i]) for i in key_columns))


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


def _is_no_statistics_response(payload):
    if isinstance(payload, dict):
        text = " ".join(str(payload.get(key) or "")
                         for key in ("detail", "message", "title", "error", "description"))
    else:
        text = str(payload or "")
    return "there are no statistics for this advertising period" in text.casefold()


async def _fullstats_batch(client, token, campaign_ids, begin, end,
                           no_statistics_campaign_ids=None):
    """Fetch fullstats, splitting transient server-failing batches without losing the cabinet."""
    global _last_fullstats
    if not campaign_ids:
        return []

    loop = asyncio.get_running_loop()
    delay = 20 - (loop.time() - _last_fullstats)
    if _last_fullstats and delay > 0:
        await asyncio.sleep(delay)

    no_statistics_campaign_ids = [] if no_statistics_campaign_ids is None else no_statistics_campaign_ids

    try:
        raw = await _request(
            client, token, "GET", PROMO + "/adv/v3/fullstats",
            params={"ids": ",".join(map(str, campaign_ids)),
                    "beginDate": begin, "endDate": end})
    except httpx.HTTPStatusError as exc:
        try:
            error_payload = exc.response.json()
        except ValueError:
            error_payload = exc.response.text
        if _is_no_statistics_response(error_payload):
            # WB uses this explicit response when the requested campaign set
            # has no facts in the selected window. Keep it as a known gap,
            # never synthesize zero-valued campaign/day rows.
            _last_fullstats = loop.time()
            no_statistics_campaign_ids.extend(campaign_ids)
            return []
        # _request has already exhausted its normal retries. WB may still fail
        # on a large request, so isolate the failing campaign by bisecting only
        # transient server errors. Do not fan out on rate limits or client/auth
        # errors, and never treat a failed leaf as a complete snapshot.
        _last_fullstats = loop.time()
        if exc.response.status_code not in (500, 502, 503, 504) or len(campaign_ids) == 1:
            raise
        middle = len(campaign_ids) // 2
        left = await _fullstats_batch(client, token, campaign_ids[:middle], begin, end,
                                      no_statistics_campaign_ids)
        right = await _fullstats_batch(client, token, campaign_ids[middle:], begin, end,
                                       no_statistics_campaign_ids)
        return [*left, *right]
    else:
        _last_fullstats = loop.time()
        if not isinstance(raw, list):
            if _is_no_statistics_response(raw):
                no_statistics_campaign_ids.extend(campaign_ids)
                return []
            keys = sorted(map(str, raw.keys()))[:12] if isinstance(raw, dict) else []
            detail = raw.get("detail") if isinstance(raw, dict) else None
            detail = str(detail)[:180] if detail is not None else ""
            raise RuntimeError(
                "WB advertising fullstats returned an invalid payload "
                f"(type={type(raw).__name__}, keys={keys}, detail={detail!r})"
            )
        return raw


def _campaign_ids(payload):
    ids = set()
    for group in payload.get("adverts", []):
        if int(group.get("status", 0)) not in (7, 9, 11):
            continue
        for item in group.get("advert_list", []):
            if item.get("advertId"):
                ids.add(int(item["advertId"]))
    return sorted(ids)


async def _catalog_nm_ids(client, token):
    """List every seller SKU; ads-only IDs omit organic products from the funnel."""
    url = PRICES + "/api/v2/list/goods/filter"
    limit = 1000
    offset = 0
    ids = set()
    while True:
        payload = await _request(client, token, "GET", url,
                                 params={"limit": limit, "offset": offset})
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        goods = (data.get("listGoods") or data.get("goods") or payload.get("listGoods") or []) \
            if isinstance(data, dict) and isinstance(payload, dict) else []
        if not isinstance(goods, list):
            raise RuntimeError("WB product catalog returned an invalid page")
        previous_count = len(ids)
        page_ids = set()
        for item in goods:
            if not isinstance(item, dict):
                continue
            try:
                nm_id = int(item.get("nmID") or item.get("nmId") or item.get("nm"))
            except (TypeError, ValueError):
                continue
            if nm_id > 0:
                page_ids.add(nm_id)
        ids.update(page_ids)
        if len(goods) < limit or not goods:
            break
        if len(ids) == previous_count:
            raise RuntimeError("WB product catalog pagination did not advance")
        offset += limit
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


def _number_sum(rows, index):
    return sum(float(row[index] or 0) for row in rows)


def _ratio(numerator, denominator):
    return round(numerator / denominator, 6) if denominator else ""


def _current_day_poll_rows(now, day, ad_rows, funnel_rows, successful_ads, successful_funnels):
    observed_at = now.isoformat()
    ads = []
    funnel = []
    for shop in SHOPS:
        if shop in successful_ads:
            day_rows = [r for r in ad_rows if r[0] == shop and r[1] == day]
            views, clicks = _number_sum(day_rows, 4), _number_sum(day_rows, 5)
            atbs, orders = _number_sum(day_rows, 6), _number_sum(day_rows, 7)
            spend, revenue = _number_sum(day_rows, 8), _number_sum(day_rows, 9)
            campaigns = len({str(r[2]) for r in day_rows})
            ads.append([shop, observed_at, day, len(day_rows), campaigns, views, clicks, atbs,
                        orders, spend, revenue, _ratio(clicks, views), _ratio(spend, clicks),
                        _ratio(spend, revenue), _ratio(revenue, spend), "WB /adv/v3/fullstats", POLL_GRAIN])
        if shop in successful_funnels:
            day_rows = [r for r in funnel_rows if r[0] == shop and r[1] == day]
            # If today's date is absent, keep the snapshot unknown rather than
            # turning a missing day into zero activity.
            if not day_rows:
                continue
            opens, carts = _number_sum(day_rows, 4), _number_sum(day_rows, 5)
            orders, revenue = _number_sum(day_rows, 6), _number_sum(day_rows, 7)
            funnel.append([shop, observed_at, day, len({str(r[2]) for r in day_rows}), opens,
                           carts, orders, revenue, _ratio(carts, opens), _ratio(orders, carts),
                           _ratio(orders, opens), "WB Analytics products/history", POLL_GRAIN])
    return ads, funnel


async def sync_once():
    global _last_fullstats
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    # Advertising fullstats supports a 31-day window. Re-read the overlap on
    # each poll, then merge it into the durable local history below.
    begin = (now.date() - timedelta(days=FULLSTATS_DAYS - 1)).isoformat()
    end = now.date().isoformat()
    funnel_begin = (now.date() - timedelta(days=6)).isoformat()
    status = {"startedAt": now.isoformat(), "period": [begin, end],
              "funnel_period": [funnel_begin, end], "shops": {}}
    ad_rows, funnel_rows = [], []
    successful_ads, successful_funnels = set(), set()
    async with httpx.AsyncClient(timeout=90) as client:
        for shop, (_, env_name) in SHOPS.items():
            token = os.environ.get(env_name, "").strip()
            if not token:
                status["shops"][shop] = {"ok": False, "error": "token not configured"}
                continue
            state = {"ok": True, "campaigns": 0, "ad_rows": 0, "funnel_rows": 0, "errors": {}}
            # Each source has independent health. An error never promotes stale data.
            shop_rows = []
            no_statistics_campaign_ids = []
            try:
                listing = await _request(client, token, "GET", PROMO + "/adv/v1/promotion/count")
                ids = _campaign_ids(listing)
                state["campaigns"] = len(ids)
                shop_rows = []
                for start in range(0, len(ids), 50):
                    batch = ids[start:start + 50]
                    raw = await _fullstats_batch(client, token, batch, begin, end,
                                                 no_statistics_campaign_ids)
                    _atomic_json(DATA_DIR / shop / f"fullstats_{start // 50}.json", raw)
                    shop_rows.extend(_stats_rows(shop, raw))
                ad_rows.extend(shop_rows)
                state["ad_rows"] = len(shop_rows)
                state["campaigns_without_period_statistics"] = len(no_statistics_campaign_ids)
                if no_statistics_campaign_ids:
                    state["campaigns_without_period_statistics_sample"] = no_statistics_campaign_ids[:20]
                successful_ads.add(shop)
            except Exception as exc:
                state["ok"] = False
                state["errors"]["promotion"] = f"{type(exc).__name__}: {exc}"
                state["campaigns_without_period_statistics"] = len(no_statistics_campaign_ids)
                if no_statistics_campaign_ids:
                    state["campaigns_without_period_statistics_sample"] = no_statistics_campaign_ids[:20]
            try:
                # The daily history endpoint accepts 1..20 nmIds per call and
                # only covers the recent seven days. Enumerate the whole seller
                # catalog so organic-only products are included as well.
                nm_ids = await _catalog_nm_ids(client, token)
                shop_funnel = []
                for start in range(0, len(nm_ids), FUNNEL_BATCH_SIZE):
                    if start:
                        await asyncio.sleep(20)
                    raw = await _request(
                        client, token, "POST",
                        ANALYTICS + "/api/analytics/v3/sales-funnel/products/history",
                        json={"selectedPeriod": {"start": funnel_begin, "end": end},
                              "nmIds": nm_ids[start:start + FUNNEL_BATCH_SIZE],
                              "skipDeletedNm": True, "aggregationLevel": "day"})
                    _atomic_json(DATA_DIR / shop / f"funnel_{start // 20}.json", raw)
                    shop_funnel.extend(_funnel_rows(shop, raw))
                funnel_rows.extend(shop_funnel)
                state["funnel_rows"] = len(shop_funnel)
                state["funnel_nm_ids"] = len(nm_ids)
                successful_funnels.add(shop)
            except Exception as exc:
                state["ok"] = False
                state["errors"]["funnel"] = f"{type(exc).__name__}: {exc}"
            status["shops"][shop] = state
    # Reuse labels learned from previous days, including products removed from
    # the current catalog, then let the fresh funnel snapshot take precedence.
    vendor_map = {}
    for shop in SHOPS:
        for row in _read_csv_rows(DATA_DIR / shop / "funnel_sku_day.csv"):
            if len(row) > 3 and row[3]:
                vendor_map[(row[0], str(row[2]))] = row[3]
    vendor_map.update({(r[0], str(r[2])): r[3] for r in funnel_rows if r[3]})
    for row in ad_rows:
        row[-1] = vendor_map.get((row[0], str(row[3])), "")
    # Keep verified last-good per-shop datasets on partial API failure.
    # The combined exports are reconstructed from these persisted datasets.
    for shop in SHOPS:
        shop_dir = DATA_DIR / shop
        if shop in successful_ads:
            current = _read_csv_rows(shop_dir / "campaign_sku_day.csv")
            merged = _upsert_rows(current, [r for r in ad_rows if r[0] == shop], (0, 1, 2, 3))
            _atomic_csv(shop_dir / "campaign_sku_day.csv", COLUMNS, merged)
            _atomic_json(shop_dir / "ad_refresh.json", {"refreshedAt": now.isoformat(),
                          "period": [begin, end], "quality": "LIVE_API",
                          "rows_in_refresh": sum(r[0] == shop for r in ad_rows),
                          "rows_in_history": len(merged)})
        if shop in successful_funnels:
            current = _read_csv_rows(shop_dir / "funnel_sku_day.csv")
            merged = _upsert_rows(current, [r for r in funnel_rows if r[0] == shop], (0, 1, 2))
            _atomic_csv(shop_dir / "funnel_sku_day.csv", FUNNEL_COLUMNS, merged)
        if shop in successful_ads or shop in successful_funnels:
            _atomic_json(shop_dir / "latest_poll.json",
                         {"polledAt": now.isoformat(), "state": status["shops"][shop]})
    def _persisted_rows(shop, filename):
        return _read_csv_rows(DATA_DIR / shop / filename)
    all_ads = [r for shop in SHOPS for r in _persisted_rows(shop, "campaign_sku_day.csv")]
    all_funnels = [r for shop in SHOPS for r in _persisted_rows(shop, "funnel_sku_day.csv")]
    _atomic_csv(DATA_DIR / "campaign_sku_day.csv", COLUMNS, all_ads)
    _atomic_csv(DATA_DIR / "funnel_sku_day.csv", FUNNEL_COLUMNS, all_funnels)
    # Poll timestamp is not the data grain: disclose the source windows explicitly.
    _atomic_json(DATA_DIR / "latest_poll.json", {"polledAt": now.isoformat(),
                  "coverage": status["shops"], "ad_rows": len(all_ads),
                  "funnel_rows": len(all_funnels), "grain": "campaign_x_sku_x_day",
                  "advertising_window_days": FULLSTATS_DAYS,
                  "funnel_window_days": 7,
                  "history_policy": "upsert; retain collected rows outside rolling source windows"})
    finished_at = datetime.now(ZoneInfo("Europe/Moscow"))
    status["finishedAt"] = finished_at.isoformat()
    ad_poll_rows, funnel_poll_rows = _current_day_poll_rows(
        finished_at, end, ad_rows, funnel_rows, successful_ads, successful_funnels
    )
    _atomic_csv(DATA_DIR / "ads_poll_snapshot.csv", ADS_POLL_COLUMNS, ad_poll_rows)
    _atomic_csv(DATA_DIR / "funnel_poll_snapshot.csv", FUNNEL_POLL_COLUMNS, funnel_poll_rows)
    status["ads_poll_rows"] = len(ad_poll_rows)
    status["funnel_poll_rows"] = len(funnel_poll_rows)
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
