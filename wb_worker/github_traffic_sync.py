"""One-shot GitHub Actions entry point for factual WB traffic collection/publish."""
import argparse
import asyncio
import csv
import json
import os
import sys
from pathlib import Path

import httpx

from traffic_sync import (ADS_POLL_COLUMNS, COLUMNS, DATA_DIR, FUNNEL_COLUMNS, ORGANIC_COLUMNS,
                          FUNNEL_POLL_COLUMNS, _public_status_summary, sync_once)
from query_clusters import collect as collect_query_clusters
from campaign_state import collect as collect_campaign_state


def _read_rows(path: Path, columns: list[str]) -> list[list[str]]:
    if not path.exists():
        raise RuntimeError(f"Required traffic export is missing: {path.name}")
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != columns:
            raise RuntimeError(f"Traffic export header mismatch: {path.name}")
        return [[row[name] for name in columns] for row in reader]



def _rows_in_window(rows: list[list[str]], date_index: int, window, label: str) -> list[list[str]]:
    if not isinstance(window, list) or len(window) != 2:
        raise RuntimeError(f"Verified source window is missing for {label}")
    start, end = (str(value or "")[:10] for value in window)
    if not start or not end or start > end:
        raise RuntimeError(f"Verified source window is invalid for {label}")
    selected = []
    for index, row in enumerate(rows):
        if len(row) <= date_index or not row[date_index]:
            raise RuntimeError(f"Traffic date is missing for {label} row {index}")
        day = str(row[date_index])[:10]
        if len(day) != 10 or day < start or day > end:
            continue
        selected.append(row)
    return selected

def _load_status() -> dict:
    path = DATA_DIR / "status.json"
    if not path.exists():
        raise RuntimeError("Traffic status is missing")
    return json.loads(path.read_text(encoding="utf-8"))


def _publish() -> None:
    """Validate the collected snapshot; encrypted GitHub branch publication is handled by Actions."""
    status = _load_status()
    if status.get("ok") is not True or not status.get("finishedAt"):
        raise RuntimeError("WB sources are not fully verified; refusing to publish")
    for shop in ("ap", "aa", "yv"):
        if (status.get("shops") or {}).get(shop, {}).get("ok") is not True:
            raise RuntimeError(f"WB source is not verified for {shop}; refusing to publish")

    datasets = {
        "ads": ("campaign_sku_day.csv", COLUMNS),
        "funnel": ("funnel_sku_day.csv", FUNNEL_COLUMNS),
        "organic": ("organic_sku_day.csv", ORGANIC_COLUMNS),
        "ads_poll": ("ads_poll_snapshot.csv", ADS_POLL_COLUMNS),
        "funnel_poll": ("funnel_poll_snapshot.csv", FUNNEL_POLL_COLUMNS),
    }
    totals = {}
    for name, (filename, columns) in datasets.items():
        rows = _read_rows(DATA_DIR / filename, columns)
        totals[name] = len(rows)
        key_columns = (0, 1, 2, 3) if name == "ads" else ((0, 1, 2) if name == "funnel" else tuple(range(min(4, len(columns)))))
        seen = set()
        for row in rows:
            key = tuple(row[index] for index in key_columns)
            if key in seen:
                raise RuntimeError(f"Duplicate natural key in verified {name} export")
            seen.add(key)

    extra = {}
    for filename, label in (("clusters_status.json", "clusters"), ("campaign_settings_status.json", "campaign_settings")):
        path = DATA_DIR / filename
        if not path.is_file():
            extra[label] = {"status": "UNAVAILABLE", "rows": 0}
            continue
        state = json.loads(path.read_text(encoding="utf-8"))
        extra[label] = {
            "status": "VERIFIED" if state.get("ok") is True else "PARTIAL",
            "rows": int(state.get("history_rows") or state.get("rows") or 0),
            "shops": {shop: bool((state.get("shops") or {}).get(shop, {}).get("ok")) for shop in ("ap", "aa", "yv")},
        }
    print("WB_GITHUB_TRAFFIC_VALIDATED " + json.dumps({
        "complete": True, "finishedAt": status.get("finishedAt"),
        "period": status.get("period"), "funnel_period": status.get("funnel_period"),
        "rows": totals, "additional_sources": extra,
    }, ensure_ascii=False, sort_keys=True))



def _publish_summaries() -> None:
    """Send verified per-store traffic facts to the existing Apps Script publisher."""
    import http.client
    import time
    import urllib.error
    import urllib.request

    status = _load_status()
    if status.get("ok") is not True or not status.get("finishedAt"):
        raise RuntimeError("WB traffic collection is not complete; summaries preserved")
    aliases = {"ap": "Саныч", "aa": "AIR", "yv": "Хозяюшка"}
    shops = status.get("shops") or {}
    if any((shops.get(alias) or {}).get("ok") is not True for alias in aliases):
        raise RuntimeError("A WB source is incomplete; summaries preserved")

    definitions = {
        "ads": ("campaign_sku_day.csv", COLUMNS),
        "funnel": ("funnel_sku_day.csv", FUNNEL_COLUMNS),
        "organic": ("organic_sku_day.csv", ORGANIC_COLUMNS),
    }
    by_shop = {alias: {name: [] for name in definitions} for alias in aliases}
    counts = {alias: {} for alias in aliases}
    for name, (filename, columns) in definitions.items():
        for row in _read_rows(DATA_DIR / filename, columns):
            alias = str(row[0] or "").strip()
            if alias not in by_shop:
                raise RuntimeError(f"Unknown store in {filename}: {alias}")
            by_shop[alias][name].append(row)
            counts[alias][name] = counts[alias].get(name, 0) + 1

    missing = [aliases[a] for a in aliases
               if not by_shop[a]["ads"] or not by_shop[a]["funnel"] or not by_shop[a]["organic"]]
    if missing:
        raise RuntimeError("Required verified traffic rows are missing for: " + ", ".join(missing))

    deployment_id = os.environ.get("WEBAPP_DEPLOYMENT_ID", "").strip()
    token = os.environ.get("WEBHOOK_SECRET", "").strip()
    if not deployment_id or not token:
        raise RuntimeError("Existing GitHub publisher credentials are not configured")
    url = "https://script.google.com/macros/s/" + deployment_id + "/exec"
    sync_status = {
        "ok": True,
        "finishedAt": status["finishedAt"],
        "shops": {alias: {"ok": True} for alias in aliases},
    }

    failures = []
    for alias, store_name in aliases.items():
        datasets = by_shop[alias]
        source_dates = [str(row[1])[:10] for rows in datasets.values() for row in rows]
        expected_max = max(source_dates)
        payload = {
            "token": token,
            "action": "publish_wb_traffic",
            "sync_status": sync_status,
            "datasets": datasets,
            "daily_view_store": True,
            "store_name": store_name,
        }
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        result = None
        last_error = ""
        for attempt in range(1, 4):
            request = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"}, method="POST"
            )
            try:
                with urllib.request.urlopen(request, timeout=420) as response:
                    result = json.loads(response.read().decode("utf-8"))
                view = (((result or {}).get("partitions") or {}).get("daily_view") or {})
                lite = view.get("lite_tables") or {}
                published_date = str(view.get("max_date") or "")[:10]
                if (isinstance(result, dict) and result.get("ok") is True
                        and view.get("ok") is True and lite.get("ok") is True
                        and store_name in (lite.get("stores") or [])
                        and published_date >= expected_max):
                    print(
                        "WB_TRAFFIC_SUMMARY_STORE=" + store_name
                        + " OK=yes ADS_ROWS=" + str(len(datasets["ads"]))
                        + " FUNNEL_ROWS=" + str(len(datasets["funnel"]))
                        + " ORGANIC_ROWS=" + str(len(datasets["organic"]))
                        + " FRESH_DAYS=" + str(view.get("fresh_rows"))
                        + " MAX_DATE=" + published_date
                        + " READBACK=yes"
                    )
                    break
                last_error = json.dumps({
                    "ok": result.get("ok") if isinstance(result, dict) else None,
                    "view_ok": view.get("ok"),
                    "lite_ok": lite.get("ok"),
                    "stores": lite.get("stores"),
                    "max_date": published_date,
                }, ensure_ascii=False)[:700]
            except (urllib.error.URLError, http.client.HTTPException, OSError,
                    TimeoutError, json.JSONDecodeError) as exc:
                last_error = type(exc).__name__ + ": " + str(exc)[:240]
            if attempt < 3:
                time.sleep(15 * attempt)
        else:
            failures.append(store_name + ": " + (last_error or "publisher readback did not match"))
            print("WB_TRAFFIC_SUMMARY_STORE=" + store_name + " OK=no ERROR=" + failures[-1])

    print("WB_TRAFFIC_SUMMARY_PUBLISH complete=" + str(not failures).lower()
          + " stores=" + str(len(aliases) - len(failures)) + "/3")
    if failures:
        raise RuntimeError("; ".join(failures))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true", help="validate previously collected exports")
    parser.add_argument("--publish-summaries", action="store_true", help="publish verified facts to the three summary books")
    args = parser.parse_args()
    if args.publish_summaries:
        _publish_summaries()
        return 0
    if args.publish:
        _publish()
        return 0
    status = asyncio.run(sync_once())
    # Same hourly job, same encrypted history, no duplicate worker/deployment.
    # Cluster errors are recorded per shop; publication gate verifies later.
    try:
        cluster_status = asyncio.run(collect_query_clusters())
    except Exception as exc:
        cluster_status = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print("WB_AD_CLUSTER_COLLECTION " + json.dumps({"ok": cluster_status.get("ok") is True}, ensure_ascii=False))
    try:
        campaign_status = asyncio.run(collect_campaign_state())
    except Exception as exc:
        campaign_status = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print("WB_CAMPAIGN_SETTINGS_COLLECTION " + json.dumps({"ok": campaign_status.get("ok") is True}, ensure_ascii=False))
    print(json.dumps(_public_status_summary(status), ensure_ascii=False, sort_keys=True))
    return 0 if status.get("ok") is True else 1


if __name__ == "__main__":
    sys.exit(main())
