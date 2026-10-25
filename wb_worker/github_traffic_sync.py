"""One-shot GitHub Actions entry point for factual WB traffic collection/publish."""
import argparse
import asyncio
import csv
import json
import os
import sys
import time
from pathlib import Path

import httpx

from traffic_sync import (ADS_POLL_COLUMNS, COLUMNS, DATA_DIR, FUNNEL_COLUMNS, ORGANIC_COLUMNS,
                          FUNNEL_POLL_COLUMNS, _public_status_summary, sync_once)
from query_clusters import collect as collect_query_clusters, publish as publish_query_clusters
from campaign_state import collect as collect_campaign_state, publish as publish_campaign_state

BRIDGE_URL = "https://script.google.com/macros/s/AKfycbyU_OXpFYqvBx0KDuGCgEHsnzkts_fnJzVe8DM8crRDD1A_fr5DqOfVfW_PYJriaXU_jw/exec"
MAX_REQUEST_BYTES = 15_000_000


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


def _post_bridge(client: httpx.Client, payload: dict) -> dict:
    # Apps Script may briefly return 404 while its active deployment propagates.
    # Retry that explicit no-route response, plus narrowly defined transient
    # gateway/rate-limit statuses. Permanent HTTP and bridge errors still fail.
    retry_statuses = {404, 408, 429, 500, 502, 503, 504}
    last_retry_reason = "Google Sheets bridge retry limit reached"
    for attempt in range(4):
        response = client.post(BRIDGE_URL, json=payload)
        if response.status_code == 401:
            last_retry_reason = "HTTP 401"
            if attempt < 3:
                time.sleep(3 * (attempt + 1))
                continue
        elif response.status_code in retry_statuses:
            last_retry_reason = f"HTTP {response.status_code}"
            if attempt < 3:
                time.sleep(3 * (attempt + 1))
                continue
        response.raise_for_status()
        result = response.json()
        if isinstance(result, dict) and result.get("error") == "UNAUTHORIZED":
            last_retry_reason = "bridge UNAUTHORIZED"
            if attempt < 3:
                time.sleep(3 * (attempt + 1))
                continue
        return result
    raise RuntimeError(f"Google Sheets bridge retries exhausted: {last_retry_reason}")


def _publish() -> None:
    status = _load_status()
    if status.get("ok") is not True or not status.get("finishedAt"):
        raise RuntimeError("Traffic sources are not all verified; refusing to publish")
    for shop in ("ap", "aa", "yv"):
        if (status.get("shops") or {}).get(shop, {}).get("ok") is not True:
            raise RuntimeError(f"Traffic source is not verified for {shop}; refusing to publish")

    ads = _rows_in_window(_read_rows(DATA_DIR / "campaign_sku_day.csv", COLUMNS), 1, status.get("period"), "ads")
    funnel = _rows_in_window(_read_rows(DATA_DIR / "funnel_sku_day.csv", FUNNEL_COLUMNS), 1, status.get("funnel_period"), "funnel")
    organic = _rows_in_window(_read_rows(DATA_DIR / "organic_sku_day.csv", ORGANIC_COLUMNS), 1, status.get("period"), "organic")
    ads_poll = _read_rows(DATA_DIR / "ads_poll_snapshot.csv", ADS_POLL_COLUMNS)
    funnel_poll = _read_rows(DATA_DIR / "funnel_poll_snapshot.csv", FUNNEL_POLL_COLUMNS)
    key = os.environ.get("GOOGLE_SHEETS_BRIDGE_KEY", "").strip()
    if not key:
        raise RuntimeError("Google Sheets bridge key is not configured")

    source_datasets = {
        "ads": ads,
        "funnel": funnel,
        "organic": organic,
        "ads_poll": ads_poll,
        "funnel_poll": funnel_poll,
    }
    totals = {name: 0 for name in source_datasets}
    partitions_published = {}
    # Each cabinet/dataset pair gets its own short bridge request. The bridge
    # reads and rewrites full monthly partitions, so bundling datasets still
    # exceeds Apps Script's response window even after splitting by cabinet.
    with httpx.Client(timeout=httpx.Timeout(180.0, connect=15.0), follow_redirects=True) as client:
        for shop in ("ap", "aa", "yv"):
            for dataset_name, source_rows in source_datasets.items():
                rows = [row for row in source_rows if str(row[0] or "").strip() == shop]
                datasets = {name: [] for name in source_datasets}
                datasets[dataset_name] = rows
                payload = {
                    "token": key,
                    "action": "publish_wb_traffic",
                    "sync_status": status,
                    "partition_only": True,
                    "skip_daily_view": True,
                    "datasets": datasets,
                }
                encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                if len(encoded) > MAX_REQUEST_BYTES:
                    raise RuntimeError(f"Traffic publication payload exceeds safe limit for {shop}/{dataset_name}: {len(encoded)} bytes")

                result = _post_bridge(client, payload)
                if not isinstance(result, dict) or result.get("ok") is not True or result.get("action") != "publish_wb_traffic":
                    detail = str(result.get("error", "unknown bridge response"))[:500] if isinstance(result, dict) else "invalid bridge response"
                    raise RuntimeError(f"Google Sheets bridge rejected traffic publication for {shop}/{dataset_name}: {detail}")

                partition_counts = (result.get("partitions") or {}).get("datasets") or {}
                for name, expected_rows in datasets.items():
                    expected = len(expected_rows)
                    if expected:
                        totals[name] += expected
                    item = partition_counts.get(name) or {}
                    if item.get("written") != expected:
                        raise RuntimeError(f"Monthly-partition row-count mismatch for {shop}/{dataset_name}/{name}")
                    if expected and (item.get("distinct_keys") != expected or item.get("skipped")):
                        raise RuntimeError(f"Monthly-partition key-count mismatch for {shop}/{dataset_name}/{name}")
                    if not expected and not item.get("skipped"):
                        raise RuntimeError(f"Monthly-partition empty {shop}/{dataset_name}/{name} result was not preserved")
                partitions_published[f"{shop}/{dataset_name}"] = {
                    name: {
                        "written": (partition_counts.get(name) or {}).get("written"),
                        "distinct_keys": (partition_counts.get(name) or {}).get("distinct_keys"),
                        "skipped": (partition_counts.get(name) or {}).get("skipped"),
                    }
                    for name in source_datasets
                }

        for name, rows in source_datasets.items():
            if totals[name] != len(rows):
                raise RuntimeError(f"Traffic publication total-count mismatch for {name}")

        # First publish the canonical daily archive, then refresh each cabinet's
        # light table and dashboard in its own bounded request. The old combined
        # request exceeded Apps Script's response window after all 15 partitions
        # had already been published.
        view_payload = {
            "token": key,
            "action": "publish_wb_traffic",
            "sync_status": status,
            "skip_partitions": True,
            "daily_view_only": True,
            "datasets": source_datasets,
        }
        encoded = json.dumps(view_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES:
            raise RuntimeError(f"Traffic daily-view payload exceeds safe limit: {len(encoded)} bytes")
        view_result = _post_bridge(client, view_payload)
        if not isinstance(view_result, dict) or view_result.get("ok") is not True or view_result.get("action") != "publish_wb_traffic":
            detail = str(view_result.get("error", "unknown bridge response"))[:500] if isinstance(view_result, dict) else "invalid bridge response"
            raise RuntimeError(f"Google Sheets bridge rejected daily-view archive publication: {detail}")
        daily_view = ((view_result.get("partitions") or {}).get("daily_view") or {})
        has_daily_source_rows = any(source_datasets[name] for name in ("ads", "funnel", "organic"))
        if (daily_view.get("archive_only") is not True or
                (int(daily_view.get("rows") or 0) <= 0 and has_daily_source_rows)):
            raise RuntimeError("WB_TRAFFIC_DAILY_VIEW_ARCHIVE_INCOMPLETE")

        store_publications = {}
        store_errors = []
        shop_aliases = {"Саныч": "yv", "AIR": "ap", "Хозяюшка": "aa"}
        for shop_name in ("Саныч", "AIR", "Хозяюшка"):
            try:
                shop_alias = shop_aliases[shop_name]
                # The Apps Script builds one store view per request. Sending all
                # three stores each time made the bridge payload and execution
                # slow enough to time out for AIR and Sanych.
                store_datasets = {
                    name: [row for row in rows if str(row[0] or "").strip() == shop_alias]
                    for name, rows in source_datasets.items()
                }
                store_payload = {
                    "token": key,
                    "action": "publish_wb_traffic",
                    "sync_status": status,
                    "skip_partitions": True,
                    "daily_view_store": True,
                    "store_name": shop_name,
                    "datasets": store_datasets,
                }
                encoded = json.dumps(store_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                if len(encoded) > MAX_REQUEST_BYTES:
                    raise RuntimeError(f"Traffic store-view payload exceeds safe limit for {shop_name}: {len(encoded)} bytes")
                store_result = _post_bridge(client, store_payload)
                if not isinstance(store_result, dict) or store_result.get("action") != "publish_wb_traffic":
                    detail = str(store_result.get("error", "unknown bridge response"))[:500] if isinstance(store_result, dict) else "invalid bridge response"
                    raise RuntimeError(f"Google Sheets bridge rejected store-view publication for {shop_name}: {detail}")
                store_view = ((store_result.get("partitions") or {}).get("daily_view") or {})
                lite_tables = store_view.get("lite_tables") or {}
                dashboard = store_view.get("dashboard") or {}
                daily_source_rows = sum(len(store_datasets[name]) for name in ("ads", "funnel", "organic"))
                no_daily_facts = daily_source_rows == 0 and store_view.get("no_daily_facts") is True
                wrote_store_table = (lite_tables.get("written") == 1 and lite_tables.get("stores") == [shop_name])
                explicitly_skipped_empty = (lite_tables.get("written") == 0 and shop_name in (lite_tables.get("skipped") or []))
                if (store_result.get("ok") is not True or lite_tables.get("ok") is not True or lite_tables.get("failed") or
                        (not wrote_store_table and not (no_daily_facts and explicitly_skipped_empty))):
                    raise RuntimeError(f"WB_TRAFFIC_STORE_LIGHT_TABLE_INCOMPLETE:{shop_name}: {json.dumps(lite_tables, ensure_ascii=False)[:800]}")
                dashboard_error = None
                if dashboard.get("skipped") is not True and (
                        dashboard.get("written") != 1 or dashboard.get("failed_stores")):
                    dashboard_error = (
                        f"WB_TRAFFIC_STORE_DASHBOARD_INCOMPLETE:{shop_name}: "
                        f"written={dashboard.get('written')} failed={dashboard.get('failed_stores')} "
                        f"partial={dashboard.get('partial_stores')}"
                    )
                    store_errors.append(dashboard_error)
                store_publications[shop_name] = {
                    "status": "NO_DAILY_SOURCE_FACTS; prior rows preserved" if no_daily_facts else "PUBLISHED",
                    "source_rows": daily_source_rows,
                    "fresh_rows": store_view.get("fresh_rows"),
                    "lite_table": lite_tables,
                    "dashboard": dashboard,
                    "dashboard_error": dashboard_error,
                }
            except Exception as exc:
                error = f"{shop_name}: {type(exc).__name__}: {str(exc)[:800]}"
                store_errors.append(error)
                store_publications[shop_name] = {"error": error}
        daily_view["store_publications"] = store_publications
        print("WB_TRAFFIC_STORE_PUBLICATIONS " + json.dumps({
            "stores_attempted": list(store_publications),
            "store_publications": store_publications,
            "errors": store_errors,
        }, ensure_ascii=False, sort_keys=True))
        if store_errors:
            raise RuntimeError("WB_TRAFFIC_STORE_PUBLICATION_INCOMPLETE: " + " | ".join(store_errors))

    print(json.dumps({
        "published": True,
        "publication_target": "monthly_partitions_plus_light_store_tables",
        "partition_batches": len(partitions_published),
        "partition_writes": partitions_published,
        "daily_view": daily_view,
    }, ensure_ascii=False, sort_keys=True))

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true", help="publish previously collected exports")
    args = parser.parse_args()
    if args.publish:
        # Advertising/funnel publication succeeds independently. A failure to
        # publish search clusters still fails this run rather than masking it.
        _publish()
        key = os.environ.get("GOOGLE_SHEETS_BRIDGE_KEY", "").strip()
        errors = []
        try:
            publish_query_clusters(BRIDGE_URL, key)
            print("WB_AD_CLUSTER_PUBLISHED " + json.dumps({"completed": True}, ensure_ascii=False))
        except Exception as exc:
            errors.append(f"clusters: {type(exc).__name__}")
        try:
            settings_published = publish_campaign_state(BRIDGE_URL, key)
            print("WB_CAMPAIGN_SETTINGS_PUBLISHED " + json.dumps({"completed": True}, ensure_ascii=False))
            missing_settings = [shop for shop, row in settings_published.items()
                                if not row.get("published")]
            if missing_settings:
                errors.append("campaign settings publication incomplete")
        except Exception as exc:
            errors.append(f"campaign settings: {type(exc).__name__}")
        if errors:
            raise RuntimeError("WB_PUBLISH_INCOMPLETE: " + "; ".join(errors))
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
