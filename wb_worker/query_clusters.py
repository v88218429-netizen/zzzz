"""Read-only daily WB advertising search-cluster facts (no JEM required).

Stats are campaign x SKU x *cluster* x day, not individual search phrases.
Unavailable CPC impressions/CTR/CPM remain blank. Never infer a search rank
from advertising avgPos (different measurement).
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from traffic_sync import DATA_DIR, PROMO, SHOPS, _atomic_csv, _atomic_json, _request, _upsert_rows

COLUMNS = [
    "shop", "date", "advert_id", "nm_id", "norm_query", "views", "clicks",
    "atbs", "orders", "ordered_units", "spend_rub", "ad_avg_pos",
    "ctr_percent", "cpc_rub", "cpm_rub", "source", "quality",
    "cluster_bid_kopecks", "cluster_state", "bid_observed_at",
]
LEGACY_COLUMNS = COLUMNS[:-3]
SOURCE = "WB /adv/v1/normquery/stats"
QUALITY = "FACT_AD_CLUSTER_DAY_NOT_SEARCH_PHRASE"
MIN_INTERVAL_SECONDS = 6.2  # Official limit: max 10 requests per minute.
_last_request = 0.0


def _norm_query(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def parse_bid_map(payload: object) -> dict[tuple[int, int, str], int]:
    """Read exact current cluster bids; absent bids remain absent, never zero-filled."""
    if not isinstance(payload, dict) or not isinstance(payload.get("bids"), list):
        raise ValueError("WB cluster bids returned an unexpected response structure")
    result = {}
    for row in payload["bids"]:
        if not isinstance(row, dict):
            raise ValueError("WB cluster bid item is invalid")
        try:
            advert, nm = int(row.get("advert_id") or row.get("advertId")), int(row.get("nm_id") or row.get("nmId"))
            bid = int(row.get("bid_kopecks") if row.get("bid_kopecks") is not None else row.get("bidKopecks"))
        except (TypeError, ValueError):
            raise ValueError("WB cluster bid lacks valid campaign, SKU, or kopeck amount") from None
        query = _norm_query(row.get("norm_query") or row.get("normQuery"))
        if advert <= 0 or nm <= 0 or bid < 0 or not query:
            raise ValueError("WB cluster bid has invalid identifiers, amount, or query")
        key = (advert, nm, query)
        if key in result:
            raise ValueError("Duplicate WB cluster bid")
        result[key] = bid
    return result


def parse_state_map(payload: object) -> dict[tuple[int, int, str], str]:
    """Read WB active/excluded state for exact campaign, SKU, and cluster."""
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("WB cluster list returned an unexpected response structure")
    result = {}
    for item in payload["items"]:
        if not isinstance(item, dict):
            raise ValueError("WB cluster list item is invalid")
        try:
            advert = int(item.get("advert_id") or item.get("advertId"))
            nm = int(item.get("nm_id") or item.get("nmId"))
        except (TypeError, ValueError):
            raise ValueError("WB cluster list lacks campaign or SKU") from None
        queries = item.get("normQueries") or item.get("norm_queries") or {}
        if not isinstance(queries, dict):
            continue
        for state, names in (("active", ("active",)), ("excluded", ("excluded",))):
            for field in names:
                values = queries.get(field) or []
                if not isinstance(values, list):
                    raise ValueError("WB cluster state list is invalid")
                for entry in values:
                    query = entry if isinstance(entry, str) else (entry.get("normQuery") or entry.get("norm_query") or entry.get("query") or "") if isinstance(entry, dict) else ""
                    key = (advert, nm, _norm_query(query))
                    if key[2]:
                        if key in result and result[key] != state:
                            raise ValueError("WB cluster appears as both active and excluded")
                        result[key] = state
    return result


def parse_rows(shop: str, payload: object,
               bid_map: dict[tuple[int, int, str], int] | None = None,
               state_map: dict[tuple[int, int, str], str] | None = None,
               bid_observed_at: str = "") -> list[list]:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("WB clusters returned an unexpected response structure")
    rows = []
    seen = set()
    for item in payload["items"]:
        if not isinstance(item, dict):
            raise ValueError("WB cluster item is invalid")
        aid, nm = item.get("advertId"), item.get("nmId")
        if not aid or not nm:
            raise ValueError("WB cluster item lacks advertId or nmId")
        for record in item.get("dailyStats") or []:
            stat = record.get("stat") or {}
            day = str(record.get("date") or "")[:10]
            query = str(stat.get("normQuery") or "").strip()
            if not query or not day:
                continue
            key = (shop, day, str(aid), str(nm), query.casefold())
            if key in seen:
                raise ValueError("Duplicate WB cluster/date: " + "|".join(key))
            seen.add(key)
            metrics = [
                stat.get("views"), stat.get("clicks"), stat.get("atbs"),
                stat.get("orders"), stat.get("shks"), stat.get("spend"),
                stat.get("avgPos"), stat.get("ctr"), stat.get("cpc"), stat.get("cpm"),
            ]
            # Preserve unavailable values as blanks; 0 is a real source value.
            numbers = []
            for value in metrics:
                if value is None or value == "":
                    numbers.append("")
                    continue
                number = float(value)
                if number < 0 or not -1e15 < number < 1e15:
                    raise ValueError("Invalid cluster statistic")
                numbers.append(value)
            key = (int(aid), int(nm), _norm_query(query))
            rows.append([shop, day, aid, nm, query, *numbers, SOURCE, QUALITY,
                         (bid_map or {}).get(key, ""), (state_map or {}).get(key, ""),
                         bid_observed_at if key in (bid_map or {}) or key in (state_map or {}) else ""])
    return rows


def active_pairs(shop: str, begin: str, end: str) -> list[dict[str, int]]:
    path = DATA_DIR / shop / "campaign_sku_day.csv"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        pairs = {
            (int(r["advert_id"]), int(r["nm_id"]))
            for r in reader
            if begin <= r.get("date", "") <= end
            and r.get("advert_id", "").isdigit() and r.get("nm_id", "").isdigit()
        }
    return [{"advertId": ad, "nmId": nm} for ad, nm in sorted(pairs)]


def existing_rows(shop: str) -> list[list[str]]:
    path = DATA_DIR / shop / "ad_clusters_day.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames == LEGACY_COLUMNS:
            return [[r[name] for name in LEGACY_COLUMNS] + ["", "", ""] for r in reader]
        if reader.fieldnames != COLUMNS:
            raise ValueError("Cluster history schema mismatch")
        return [[r[name] for name in COLUMNS] for r in reader]


async def collect() -> dict:
    global _last_request
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    begin = (now.date() - timedelta(days=6)).isoformat()
    end = now.date().isoformat()
    state = {"observed_at": now.isoformat(), "window": [begin, end],
             "source": SOURCE, "shops": {}}
    async with httpx.AsyncClient(timeout=90) as client:
        for shop, (_, key_name) in SHOPS.items():
            token = os.getenv(key_name, "").strip()
            info = {"ok": False, "pairs": 0, "new_rows": 0, "history_rows": 0}
            try:
                if not token:
                    raise RuntimeError("WB promotion token unavailable")
                pairs = active_pairs(shop, begin, end)
                info["pairs"] = len(pairs)
                if not pairs:
                    # Empty campaign stats are not evidence of absent search traffic.
                    raise RuntimeError("No campaign x SKU pairs in current verified ads window")
                incoming = []
                successful_batches = 0
                batch_errors = []
                for i in range(0, len(pairs), 100):
                    loop = asyncio.get_running_loop()
                    if _last_request:
                        await asyncio.sleep(max(0, MIN_INTERVAL_SECONDS - (loop.time() - _last_request)))
                    _last_request = loop.time()
                    try:
                        batch = pairs[i:i + 100]
                        snake = [{"advert_id": x["advertId"], "nm_id": x["nmId"]} for x in batch]
                        camel = [{"advertId": x["advertId"], "nmId": x["nmId"]} for x in batch]
                        bid_map, state_map = {}, {}
                        bid_time = datetime.now(ZoneInfo("Europe/Moscow")).isoformat()
                        # Auxiliary reads are allowed to fail independently. The
                        # statistics still publish, but the panel will block any
                        # numeric bid recommendation without a fresh exact bid.
                        for endpoint, body, parser, target in (
                            ("/adv/v0/normquery/get-bids", {"items": snake}, parse_bid_map, bid_map),
                            ("/adv/v0/normquery/list", {"items": camel}, parse_state_map, state_map),
                        ):
                            try:
                                loop = asyncio.get_running_loop()
                                await asyncio.sleep(max(0, MIN_INTERVAL_SECONDS - (loop.time() - _last_request)))
                                _last_request = loop.time()
                                target.update(parser(await _request(client, token, "POST", PROMO + endpoint, json=body)))
                            except Exception as aux_error:
                                info.setdefault("bid_state_warnings", []).append(f"{endpoint}: {type(aux_error).__name__}")
                        loop = asyncio.get_running_loop()
                        await asyncio.sleep(max(0, MIN_INTERVAL_SECONDS - (loop.time() - _last_request)))
                        _last_request = loop.time()
                        payload = await _request(
                            client, token, "POST", PROMO + "/adv/v1/normquery/stats",
                            json={"from": begin, "to": end, "items": camel})
                        incoming.extend(parse_rows(shop, payload, bid_map, state_map, bid_time))
                        successful_batches += 1
                    except Exception as batch_error:
                        # Preserve successful prior chunks; never pretend to have
                        # complete campaign coverage when rate-limited.
                        batch_errors.append(f"{type(batch_error).__name__}: {batch_error}")
                        # Stop after a rate or API failure; further calls can
                        # worsen a seller's quota or create repeat delays.
                        break
                if successful_batches:
                    prior = existing_rows(shop)
                    merged = _upsert_rows(prior, incoming, (0, 1, 2, 3, 4))
                    _atomic_csv(DATA_DIR / shop / "ad_clusters_day.csv", COLUMNS, merged)
                    info.update({"ok": not batch_errors, "new_rows": len(incoming),
                                 "history_rows": len(merged), "factual": bool(incoming),
                                 "batches_collected": successful_batches,
                                 "batches_expected": (len(pairs)+99)//100,
                                 "bid_rows": sum(1 for r in incoming if len(r) > len(LEGACY_COLUMNS) and r[-3] != ""),
                                 "clusters_without_fresh_bid": sum(1 for r in incoming if len(r) > len(LEGACY_COLUMNS) and r[-3] == ""),
                                 "note": "Empty valid API response retained as empty; not imputed"})
                if batch_errors:
                    info["error"] = batch_errors[0]
                    info["coverage"] = "PARTIAL_SOURCE_WINDOW"
                elif not successful_batches:
                    raise RuntimeError("No verified cluster API batches")
            except Exception as exc:
                info["error"] = f"{type(exc).__name__}: {exc}"
            state["shops"][shop] = info
    all_rows = []
    for shop in SHOPS:
        all_rows.extend(existing_rows(shop))
    _atomic_csv(DATA_DIR / "ad_clusters_day.csv", COLUMNS, all_rows)
    state["ok"] = all(v["ok"] for v in state["shops"].values())
    state["history_rows"] = len(all_rows)
    _atomic_json(DATA_DIR / "clusters_status.json", state)
    return state


def publish(bridge_url: str, bridge_key: str) -> dict:
    """Publish only freshly verified cluster facts into the shared WB Sheets bridge."""
    if not bridge_url or not bridge_key:
        raise RuntimeError("Authenticated Sheets bridge configuration missing")
    status_path = DATA_DIR / "clusters_status.json"
    if not status_path.exists():
        raise RuntimeError("Cluster collection status missing")
    status = json.loads(status_path.read_text(encoding="utf-8"))
    # Publish independently per shop; an inaccessible cabinet must not erase
    # or prevent publication of another cabinet's verified cluster facts.
    window = status.get("window") or []
    if len(window) != 2:
        raise RuntimeError("Cluster window missing")
    counts = {}
    failed = {}
    # Prioritize the cabinet without JEM and the most recent observations.
    for shop in ("yv", "aa", "ap"):
        shop_state = status.get("shops", {}).get(shop) or {}
        if not shop_state.get("factual"):
            counts[shop] = {"published_rows": 0, "skipped": True,
                            "reason": "no_verified_current_batch"}
            continue
        rows = sorted((r for r in existing_rows(shop) if window[0] <= r[1] <= window[1]),
                      key=lambda r: r[1], reverse=True)
        counts[shop] = {"source_rows": len(rows), "published_rows": 0, "chunks": 0}
        if not rows:
            continue
        try:
            with httpx.Client(timeout=httpx.Timeout(170.0, connect=15.0), follow_redirects=True) as client:
                for start in range(0, len(rows), 250):
                    chunk = rows[start:start+250]
                    payload = {
                        "token": bridge_key, "action": "publish_ad_clusters",
                        "cabinet": shop, "source": SOURCE, "quality": QUALITY,
                        "observed_at": status["observed_at"], "rows": chunk,
                    }
                    data = None
                    for attempt in range(4):
                        try:
                            response = client.post(bridge_url, json=payload)
                            if response.status_code in (404, 408, 429, 500, 502, 503, 504):
                                raise RuntimeError(f"GAS_TRANSIENT_HTTP_{response.status_code}")
                            if response.status_code >= 400:
                                raise RuntimeError(f"GAS_BRIDGE_HTTP_{response.status_code}")
                            try:
                                data = response.json()
                            except ValueError:
                                raise RuntimeError("GAS_BRIDGE_RETURNED_NON_JSON")
                            break
                        except (httpx.TransportError, RuntimeError) as exc:
                            retryable = isinstance(exc, httpx.TransportError) or str(exc).startswith("GAS_TRANSIENT_HTTP_")
                            if not retryable or attempt == 3:
                                raise RuntimeError(f"{shop} chunk {start}: {type(exc).__name__}: {exc}") from None
                            time.sleep([2, 5, 10][attempt])
                    if not isinstance(data, dict) or data.get("ok") is not True or data.get("action") != "publish_ad_clusters":
                        if isinstance(data, dict):
                            detail = {name: data.get(name) for name in ("ok", "action", "error", "code", "reason", "written")
                                      if data.get(name) is not None}
                        else:
                            detail = {"response_type": type(data).__name__}
                        raise RuntimeError(
                            f"{shop}: clusters bridge rejected publication: "
                            f"{json.dumps(detail, ensure_ascii=False)[:600]}"
                        )
                    if int(data.get("written", -1)) != len(chunk):
                        raise RuntimeError(
                            f"{shop}: cluster row-count mismatch: expected={len(chunk)} actual={data.get('written')}"
                        )
                    counts[shop]["published_rows"] += len(chunk)
                    counts[shop]["chunks"] += 1
        except Exception as exc:
            # Keep publishing verified rows for other cabinets. No one shop
            # is allowed to erase or starve the others after a bridge failure.
            detail = f"{type(exc).__name__}: {exc}"
            counts[shop]["error"] = detail
            failed[shop] = detail
    print(json.dumps({"published": counts, "period": window}, ensure_ascii=False))
    if failed:
        raise RuntimeError("Incomplete cluster publication for: " + ", ".join(sorted(failed)))
    return counts


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--require-all", action="store_true")
    args = parser.parse_args()
    if args.require_all:
        path = DATA_DIR / "clusters_status.json"
        if not path.exists():
            raise RuntimeError("Cluster collection status missing")
        status = json.loads(path.read_text(encoding="utf-8"))
        missing = [shop for shop in SHOPS if
                   not all((status.get("shops", {}).get(shop) or {}).get(k) for k in ("factual", "ok"))]
        if missing:
            raise RuntimeError("Missing factual cluster rows in: " + ", ".join(missing))
        print("WB_AD_CLUSTERS_VERIFIED_ALL_CABINETS")
        return 0
    if args.publish:
        publish(os.getenv("GOOGLE_SHEETS_BRIDGE_URL", ""),
                os.getenv("GOOGLE_SHEETS_BRIDGE_KEY", ""))
        return 0
    status = asyncio.run(collect())
    print(json.dumps(status, ensure_ascii=False))
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
