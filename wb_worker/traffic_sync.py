"""Read-only WB advertising and funnel snapshots; no campaign mutations."""
import asyncio
import csv
import json
import os
from datetime import date, datetime, timedelta
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
ORGANIC_COLUMNS = ["shop", "date", "nm_id", "vendor_code", "total_open_count", "paid_clicks", "organic_clicks", "source_total", "source_paid", "formula", "coverage_status", "campaigns_checked", "campaigns_without_period_statistics", "campaigns_with_null_payload"]
ADS_POLL_COLUMNS = ["shop", "observed_at", "date", "campaign_sku_rows", "campaigns_with_rows", "views", "clicks", "cart_adds", "orders", "spend_rub", "order_sum_rub", "ctr", "cpc_rub", "drr", "roas", "source", "measurement_grain"]
FUNNEL_POLL_COLUMNS = ["shop", "observed_at", "date", "sku_rows", "open_count", "cart_count", "order_count", "order_sum_rub", "open_to_cart", "cart_to_order", "open_to_order", "source", "measurement_grain"]
POLL_GRAIN = "DAILY_CUMULATIVE_OBSERVED_AT_POLL"
PRICES = "https://discounts-prices-api.wildberries.ru"
MONITORING_START = date(2026, 10, 1)
FULLSTATS_DAYS = 31
# Bound retries for JSON-null batch isolation under WB's strict fullstats rate limit.
MAX_NULL_ISOLATION_DEPTH = 1
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


def _rows_from_monitoring_start(rows, date_index=1):
    """Keep only explicitly collected facts from the monitor's start date."""
    cutoff = MONITORING_START.isoformat()
    return [row for row in rows
            if len(row) > date_index and str(row[date_index])[:10] >= cutoff]


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
                           no_statistics_campaign_ids=None,
                           null_payload_campaign_ids=None,
                           null_retry_used=False,
                           null_isolation_depth=0):
    """Fetch fullstats, isolating server errors, empty windows, and bounded null groups."""
    global _last_fullstats
    if not campaign_ids:
        return []

    loop = asyncio.get_running_loop()
    delay = 20 - (loop.time() - _last_fullstats)
    if _last_fullstats and delay > 0:
        await asyncio.sleep(delay)

    no_statistics_campaign_ids = [] if no_statistics_campaign_ids is None else no_statistics_campaign_ids
    null_payload_campaign_ids = [] if null_payload_campaign_ids is None else null_payload_campaign_ids

    async def split_batch(next_null_depth=None):
        middle = len(campaign_ids) // 2
        child_depth = null_isolation_depth if next_null_depth is None else next_null_depth
        left = await _fullstats_batch(client, token, campaign_ids[:middle], begin, end,
                                      no_statistics_campaign_ids, null_payload_campaign_ids,
                                      False, child_depth)
        right = await _fullstats_batch(client, token, campaign_ids[middle:], begin, end,
                                       no_statistics_campaign_ids, null_payload_campaign_ids,
                                       False, child_depth)
        return [*left, *right]

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
            _last_fullstats = loop.time()
            if len(campaign_ids) == 1:
                # Record only a campaign that WB individually confirms has no
                # facts for this window; never synthesize zero-valued rows.
                no_statistics_campaign_ids.append(campaign_ids[0])
                return []
            return await split_batch()
        # _request has already exhausted its normal retries. WB may still fail
        # on a large request, so isolate the failing campaign by bisecting only
        # transient server errors. Do not fan out on rate limits or client/auth
        # errors, and never treat a failed leaf as a complete snapshot.
        _last_fullstats = loop.time()
        if exc.response.status_code not in (500, 502, 503, 504) or len(campaign_ids) == 1:
            raise
        return await split_batch()
    else:
        _last_fullstats = loop.time()
        if not isinstance(raw, list):
            if raw is None:
                # Retry the same group once. If WB still sends JSON null, do not
                # fan out into dozens of rate-limited requests or guess which
                # campaign lacks facts. Omit the unresolved group and disclose it.
                if not null_retry_used:
                    return await _fullstats_batch(
                        client, token, campaign_ids, begin, end,
                        no_statistics_campaign_ids, null_payload_campaign_ids, True,
                        null_isolation_depth
                    )
                if (len(campaign_ids) > 1
                        and null_isolation_depth < MAX_NULL_ISOLATION_DEPTH):
                    # A bad campaign can poison a mixed request. Split once to
                    # preserve valid siblings, then disclose any unresolved group.
                    return await split_batch(null_isolation_depth + 1)
                null_payload_campaign_ids.extend(campaign_ids)
                return []
            if _is_no_statistics_response(raw):
                if len(campaign_ids) == 1:
                    no_statistics_campaign_ids.append(campaign_ids[0])
                    return []
                return await split_batch()
            keys = sorted(map(str, raw.keys()))[:12] if isinstance(raw, dict) else []
            detail = raw.get("detail") if isinstance(raw, dict) else None
            detail = str(detail)[:180] if detail is not None else ""
            raise RuntimeError(
                "WB advertising fullstats returned an invalid payload "
                f"(type={type(raw).__name__}, keys={keys}, detail={detail!r})"
            )
        return raw


def _campaign_status_index(payload):
    """Map stats-eligible campaign IDs to WB's documented current status."""
    statuses = {}
    for group in payload.get("adverts", []):
        try:
            status = int(group.get("status", 0))
        except (TypeError, ValueError):
            continue
        if status not in (7, 9, 11):
            continue
        for item in group.get("advert_list", []):
            if item.get("advertId"):
                statuses[int(item["advertId"])] = status
    return statuses


def _campaign_status_counts(statuses):
    return {str(status): sum(value == status for value in statuses.values())
            for status in (7, 9, 11)}


def _public_status_summary(status):
    """Return only non-business outcome fields for public workflow logs."""
    return {
        "ok": status.get("ok") is True,
        "complete": status.get("complete") is True,
        "period": status.get("period"),
        "funnel_period": status.get("funnel_period"),
        "finishedAt": status.get("finishedAt"),
    }


def _campaign_scope_is_complete(store):
    if "business_scope_campaigns" in store:
        try:
            missing = int(store.get("business_scope_missing") or 0)
            unexpected = int(store.get("business_scope_unexpected_responses") or 0)
        except (TypeError, ValueError):
            return False
        return (store.get("business_scope_verified") is True
                and missing == 0 and unexpected == 0)
    try:
        return int(store.get("campaigns_with_null_payload") or 0) == 0
    except (TypeError, ValueError):
        return False


def _bootstrap_business_campaign_scope(previous_status, shop, campaign_ids,
                                      no_statistics_campaign_ids,
                                      null_payload_campaign_ids,
                                      status_counts, now):
    """Bootstrap AIR's private scope from two matching, recent verified polls."""
    if shop != "aa" or not isinstance(previous_status, dict):
        return None
    try:
        finished_at = datetime.fromisoformat(
            str(previous_status.get("finishedAt") or "").replace("Z", "+00:00")
        )
        age = now - finished_at
        previous = (previous_status.get("shops") or {}).get(shop) or {}
        previous_campaigns = int(previous.get("campaigns") or 0)
        previous_null = int(previous.get("campaigns_with_null_payload") or 0)
        previous_no_statistics = int(
            previous.get("campaigns_without_period_statistics") or 0
        )
    except (TypeError, ValueError):
        return None
    if (finished_at.tzinfo is None
            or age < -timedelta(minutes=5)
            or age > timedelta(hours=24)
            or previous.get("ok") is not True
            or previous.get("errors")
            or previous.get("campaign_status_counts") != status_counts
            or len(campaign_ids) != previous_campaigns
            or len(null_payload_campaign_ids) != previous_null
            or len(no_statistics_campaign_ids) != previous_no_statistics):
        return None
    previous_null_sample = {
        str(value)
        for value in previous.get("campaigns_with_null_payload_sample", [])
    }
    current_null_ids = {str(value) for value in null_payload_campaign_ids}
    if previous_null_sample and not previous_null_sample.issubset(current_null_ids):
        return None
    active_ids = (
        {int(value) for value in campaign_ids}
        - {int(value) for value in no_statistics_campaign_ids}
        - {int(value) for value in null_payload_campaign_ids}
    )
    expected_count = previous_campaigns - previous_null - previous_no_statistics
    if expected_count <= 0 or len(active_ids) != expected_count:
        return None
    return sorted(active_ids)


def _campaign_ids(payload):
    return sorted(_campaign_status_index(payload))


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
                        value = item.get(field)
                        if value is None or isinstance(value, bool) or str(value).strip() == "":
                            values[index] = None
                        elif values[index] is not None:
                            try:
                                values[index] += float(value)
                            except (TypeError, ValueError):
                                values[index] = None
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
    if not rows:
        return 0
    values = []
    for row in rows:
        value = row[index] if len(row) > index else None
        if value is None or isinstance(value, bool) or str(value).strip() == "":
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number < 0:
            return None
        values.append(number)
    total = sum(values)
    return int(total) if total.is_integer() else total


def _ratio(numerator, denominator):
    return round(numerator / denominator, 6) if numerator is not None and denominator not in (None, 0) else ""


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


def _count_value(value):
    """Return a non-negative integer count, or None for unknown/invalid input."""
    if value is None or isinstance(value, bool) or str(value).strip() == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not number.is_integer() or number < 0:
        return None
    return int(number)


def _derive_organic_rows(funnel_rows, ad_rows, coverage):
    """Derive total card opens minus paid clicks only under complete source coverage.

    A store with an unresolved null response inside its confirmed business scope
    remains partial. Out-of-scope nulls do not poison the derivation. Negative
    differences and missing funnel counts remain unknown, never clamped to zero.
    """
    paid_by_product_day = {}
    stores_with_unknown_clicks = set()
    for row in ad_rows:
        if len(row) <= 5:
            continue
        key = (str(row[0]), str(row[1])[:10], str(row[3]))
        clicks = _count_value(row[5])
        if clicks is not None:
            paid_by_product_day[key] = paid_by_product_day.get(key, 0) + clicks
        else:
            stores_with_unknown_clicks.add(key[0])

    result = []
    seen = set()
    for row in funnel_rows:
        if len(row) <= 4:
            continue
        shop, day, nm_id = str(row[0]), str(row[1])[:10], str(row[2])
        key = (shop, day, nm_id)
        if not all(key) or key in seen:
            continue
        seen.add(key)
        store = coverage.get(shop, {})
        complete_ads = (store.get("ok") is True
                        and _campaign_scope_is_complete(store)
                        and shop not in stores_with_unknown_clicks)
        total_opens = _count_value(row[4])
        paid_clicks = paid_by_product_day.get(key, 0) if complete_ads else None
        organic_clicks = None
        if total_opens is None:
            coverage_status = "TOTAL_OPEN_MISSING"
        elif not complete_ads:
            coverage_status = "ADVERTISING_COVERAGE_INCOMPLETE"
        elif paid_clicks > total_opens:
            coverage_status = "MEASUREMENT_MISMATCH"
        else:
            organic_clicks = total_opens - paid_clicks
            coverage_status = "DERIVED_COMPLETE"
        result.append([
            shop, day, nm_id, row[3] if len(row) > 3 else "",
            total_opens if total_opens is not None else "",
            paid_clicks if paid_clicks is not None else "",
            organic_clicks if organic_clicks is not None else "",
            "WB Analytics products/history", "WB /adv/v3/fullstats",
            "total_open_count-paid_clicks",
            coverage_status,
            int(store.get("campaigns") or 0),
            int(store.get("campaigns_without_period_statistics") or 0),
            int(store.get("campaigns_with_null_payload") or 0),
        ])
    return result


async def sync_once():
    global _last_fullstats
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    # Start factual monitoring on 1 October 2026. Keep the overlap within
    # WB's limits, and never retain or republish earlier dates.
    monitoring_start = min(MONITORING_START, now.date())
    begin = max(now.date() - timedelta(days=FULLSTATS_DAYS - 1), monitoring_start).isoformat()
    end = now.date().isoformat()
    funnel_begin = max(now.date() - timedelta(days=6), monitoring_start).isoformat()
    previous_status = {}
    previous_status_path = DATA_DIR / "status.json"
    if previous_status_path.exists():
        try:
            previous_status = json.loads(previous_status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous_status = {}
    scope_path = DATA_DIR / "air_active_campaign_scope.json"
    active_scope_ids = None
    scope_config_error = False
    if scope_path.exists():
        try:
            scope_config = json.loads(scope_path.read_text(encoding="utf-8"))
            raw_scope = scope_config.get("campaign_ids")
            if (scope_config.get("version") != 1 or not isinstance(raw_scope, list)
                    or not raw_scope):
                raise ValueError("invalid scope structure")
            active_scope_ids = sorted({int(value) for value in raw_scope})
            if any(value <= 0 for value in active_scope_ids):
                raise ValueError("invalid campaign id")
        except (OSError, TypeError, ValueError):
            scope_config_error = True
    status = {"startedAt": now.isoformat(), "monitoring_start": monitoring_start.isoformat(),
              "period": [begin, end], "funnel_period": [funnel_begin, end], "shops": {}}
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
            null_payload_campaign_ids = []
            campaign_status_index = {}
            ids = []
            try:
                listing = await _request(client, token, "GET", PROMO + "/adv/v1/promotion/count")
                campaign_status_index = _campaign_status_index(listing)
                ids = sorted(campaign_status_index)
                state["campaigns"] = len(ids)
                state["campaign_status_counts"] = _campaign_status_counts(campaign_status_index)
                shop_rows = []
                for start in range(0, len(ids), 50):
                    batch = ids[start:start + 50]
                    raw = await _fullstats_batch(client, token, batch, begin, end,
                                                 no_statistics_campaign_ids,
                                                 null_payload_campaign_ids)
                    _atomic_json(DATA_DIR / shop / f"fullstats_{start // 50}.json", raw)
                    shop_rows.extend(_stats_rows(shop, raw))
                state["campaigns_without_period_statistics"] = len(no_statistics_campaign_ids)
                if no_statistics_campaign_ids:
                    state["campaigns_without_period_statistics_sample"] = no_statistics_campaign_ids[:20]
                state["campaigns_with_null_payload"] = len(null_payload_campaign_ids)
                state["null_payload_status_counts"] = _campaign_status_counts({
                    campaign_id: campaign_status_index[campaign_id]
                    for campaign_id in null_payload_campaign_ids
                    if campaign_id in campaign_status_index
                })
                state["no_statistics_status_counts"] = _campaign_status_counts({
                    campaign_id: campaign_status_index[campaign_id]
                    for campaign_id in no_statistics_campaign_ids
                    if campaign_id in campaign_status_index
                })
                if null_payload_campaign_ids:
                    state["campaigns_with_null_payload_sample"] = null_payload_campaign_ids[:20]
                successful_ads.add(shop)
            except Exception as exc:
                state["ok"] = False
                state["errors"]["promotion"] = f"{type(exc).__name__}: {exc}"
                state["campaigns_without_period_statistics"] = len(no_statistics_campaign_ids)
                if no_statistics_campaign_ids:
                    state["campaigns_without_period_statistics_sample"] = no_statistics_campaign_ids[:20]
                state["campaigns_with_null_payload"] = len(null_payload_campaign_ids)
                state["null_payload_status_counts"] = _campaign_status_counts({
                    campaign_id: campaign_status_index[campaign_id]
                    for campaign_id in null_payload_campaign_ids
                    if campaign_id in campaign_status_index
                })
                state["no_statistics_status_counts"] = _campaign_status_counts({
                    campaign_id: campaign_status_index[campaign_id]
                    for campaign_id in no_statistics_campaign_ids
                    if campaign_id in campaign_status_index
                })
                if null_payload_campaign_ids:
                    state["campaigns_with_null_payload_sample"] = null_payload_campaign_ids[:20]
            if shop == "aa" and state.get("ok"):
                if scope_config_error:
                    state["ok"] = False
                    state["errors"]["campaign_scope"] = "Protected active campaign scope is unreadable."
                else:
                    if active_scope_ids is None:
                        active_scope_ids = _bootstrap_business_campaign_scope(
                            previous_status, shop, ids, no_statistics_campaign_ids,
                            null_payload_campaign_ids,
                            state.get("campaign_status_counts", {}), now
                        )
                        if active_scope_ids:
                            _atomic_json(scope_path, {
                                "version": 1,
                                "capturedAt": now.isoformat(),
                                "campaign_ids": active_scope_ids,
                            })
                    if active_scope_ids is None:
                        state["business_scope_campaigns"] = 0
                        state["business_scope_missing"] = 0
                        state["business_scope_unexpected_responses"] = 0
                        state["business_scope_verified"] = False
                        state["warnings"] = {
                            "campaign_scope": "Protected active campaign scope is not initialized."
                        }
                    else:
                        scope_ids = set(active_scope_ids)
                        listed_ids = {int(value) for value in ids}
                        null_ids = {int(value) for value in null_payload_campaign_ids}
                        missing_ids = (scope_ids - listed_ids) | (scope_ids & null_ids)
                        observed_row_ids = {
                            int(row[2]) for row in shop_rows
                            if len(row) > 2 and str(row[2]).isdigit()
                        }
                        unexpected_ids = observed_row_ids - scope_ids
                        state["business_scope_campaigns"] = len(scope_ids)
                        state["business_scope_missing"] = len(missing_ids)
                        state["business_scope_unexpected_responses"] = len(unexpected_ids)
                        state["business_scope_verified"] = not missing_ids and not unexpected_ids
                        shop_rows = [
                            row for row in shop_rows
                            if len(row) > 2 and str(row[2]).isdigit()
                            and int(row[2]) in scope_ids
                        ]
                        if state["business_scope_verified"]:
                            state.pop("warnings", None)
                        else:
                            state["warnings"] = {
                                "campaign_scope": "Protected active campaign coverage needs review."
                            }
            elif shop == "aa":
                state["business_scope_verified"] = False
            elif null_payload_campaign_ids:
                state["warnings"] = {
                    "promotion": "WB returned an unresolved fullstats payload."
                }
            if state.get("ok"):
                ad_rows.extend(shop_rows)
                state["ad_rows"] = len(shop_rows)
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
        current_ads = _read_csv_rows(shop_dir / "campaign_sku_day.csv")
        eligible_ads = _rows_from_monitoring_start(current_ads)
        if eligible_ads != current_ads:
            _atomic_csv(shop_dir / "campaign_sku_day.csv", COLUMNS, eligible_ads)
        current_funnels = _read_csv_rows(shop_dir / "funnel_sku_day.csv")
        eligible_funnels = _rows_from_monitoring_start(current_funnels)
        if eligible_funnels != current_funnels:
            _atomic_csv(shop_dir / "funnel_sku_day.csv", FUNNEL_COLUMNS, eligible_funnels)
        if shop in successful_ads:
            merged = _upsert_rows(eligible_ads, [r for r in ad_rows if r[0] == shop], (0, 1, 2, 3))
            _atomic_csv(shop_dir / "campaign_sku_day.csv", COLUMNS, merged)
            _atomic_json(shop_dir / "ad_refresh.json", {"refreshedAt": now.isoformat(),
                          "period": [begin, end], "quality": "LIVE_API",
                          "rows_in_refresh": sum(r[0] == shop for r in ad_rows),
                          "rows_in_history": len(merged)})
        if shop in successful_funnels:
            merged = _upsert_rows(eligible_funnels, [r for r in funnel_rows if r[0] == shop], (0, 1, 2))
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
    organic_rows = _derive_organic_rows(all_funnels, all_ads, status["shops"])
    _atomic_csv(DATA_DIR / "organic_sku_day.csv", ORGANIC_COLUMNS, organic_rows)
    # Poll timestamp is not the data grain: disclose the source windows explicitly.
    _atomic_json(DATA_DIR / "latest_poll.json", {"polledAt": now.isoformat(),
                  "coverage": status["shops"], "ad_rows": len(all_ads),
                  "funnel_rows": len(all_funnels), "grain": "campaign_x_sku_x_day",
                  "advertising_window_days": FULLSTATS_DAYS,
                  "funnel_window_days": 7,
                  "monitoring_start": MONITORING_START.isoformat(),
                  "history_policy": "upsert within source windows; retain collected rows from monitoring start"})
    finished_at = datetime.now(ZoneInfo("Europe/Moscow"))
    status["finishedAt"] = finished_at.isoformat()
    # Partial advertising refreshes may contribute verified rows to the daily
    # table, but must not create an undercounted or zero-filled poll observation.
    pollable_ads = {
        shop for shop in successful_ads
        if _campaign_scope_is_complete(status["shops"][shop])
    }
    ad_poll_rows, funnel_poll_rows = _current_day_poll_rows(
        finished_at, end, ad_rows, funnel_rows, pollable_ads, successful_funnels
    )
    _atomic_csv(DATA_DIR / "ads_poll_snapshot.csv", ADS_POLL_COLUMNS, ad_poll_rows)
    _atomic_csv(DATA_DIR / "funnel_poll_snapshot.csv", FUNNEL_POLL_COLUMNS, funnel_poll_rows)
    status["ads_poll_rows"] = len(ad_poll_rows)
    status["funnel_poll_rows"] = len(funnel_poll_rows)
    status["ok"] = all(s["ok"] for s in status["shops"].values())
    status["complete"] = status["ok"] and not any(
        shop.get("warnings") for shop in status["shops"].values()
    )
    _atomic_json(DATA_DIR / "status.json", status)
    print("WB_TRAFFIC_SYNC_DONE " + json.dumps(_public_status_summary(status), ensure_ascii=False), flush=True)
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
