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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true", help="publish previously collected exports")
    args = parser.parse_args()
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
