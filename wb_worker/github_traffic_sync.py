"""One-shot GitHub Actions entry point for factual WB traffic collection/publish."""
import argparse
import asyncio
import csv
import json
import os
import sys
from pathlib import Path

import httpx

from traffic_sync import (ADS_POLL_COLUMNS, COLUMNS, DATA_DIR, FUNNEL_COLUMNS,
                          FUNNEL_POLL_COLUMNS, sync_once)

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


def _load_status() -> dict:
    path = DATA_DIR / "status.json"
    if not path.exists():
        raise RuntimeError("Traffic status is missing")
    return json.loads(path.read_text(encoding="utf-8"))


def _publish() -> None:
    status = _load_status()
    if status.get("ok") is not True or not status.get("finishedAt"):
        raise RuntimeError("Traffic sources are not all verified; refusing to publish")
    for shop in ("ap", "aa", "yv"):
        if (status.get("shops") or {}).get(shop, {}).get("ok") is not True:
            raise RuntimeError(f"Traffic source is not verified for {shop}; refusing to publish")

    ads = _read_rows(DATA_DIR / "campaign_sku_day.csv", COLUMNS)
    funnel = _read_rows(DATA_DIR / "funnel_sku_day.csv", FUNNEL_COLUMNS)
    ads_poll = _read_rows(DATA_DIR / "ads_poll_snapshot.csv", ADS_POLL_COLUMNS)
    funnel_poll = _read_rows(DATA_DIR / "funnel_poll_snapshot.csv", FUNNEL_POLL_COLUMNS)
    key = os.environ.get("GOOGLE_SHEETS_BRIDGE_KEY", "").strip()
    if not key:
        raise RuntimeError("Google Sheets bridge key is not configured")
    payload = {
        "token": key,
        "action": "publish_wb_traffic",
        "sync_status": status,
        "datasets": {
            "ads": ads,
            "funnel": funnel,
            "ads_poll": ads_poll,
            "funnel_poll": funnel_poll,
        },
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_REQUEST_BYTES:
        raise RuntimeError(f"Traffic publication payload exceeds safe limit: {len(encoded)} bytes")

    with httpx.Client(timeout=httpx.Timeout(180.0, connect=15.0), follow_redirects=True) as client:
        response = client.post(BRIDGE_URL, json=payload)
        response.raise_for_status()
        result = response.json()
    if not isinstance(result, dict) or result.get("ok") is not True or result.get("action") != "publish_wb_traffic":
        raise RuntimeError("Google Sheets bridge rejected traffic publication")
    counts = result.get("datasets") or {}
    for name, expected in (("ads", len(ads)), ("funnel", len(funnel)),
                           ("ads_poll", len(ads_poll)), ("funnel_poll", len(funnel_poll))):
        item = counts.get(name) or {}
        if expected and (item.get("written") != expected or item.get("skipped")):
            raise RuntimeError(f"Google Sheets bridge row-count mismatch for {name}")
        if not expected and not item.get("skipped"):
            raise RuntimeError(f"Google Sheets bridge did not preserve empty {name} snapshot")
    print(json.dumps({
        "published": True,
        "sync_finished_at": status["finishedAt"],
        "ads_rows": len(ads),
        "ads_max_date": counts.get("ads", {}).get("max_date", ""),
        "funnel_rows": len(funnel),
        "funnel_max_date": counts.get("funnel", {}).get("max_date", ""),
        "ads_poll_rows": len(ads_poll),
        "funnel_poll_rows": len(funnel_poll),
        "ads_poll_observations_added": counts.get("ads_poll", {}).get("added", 0),
        "funnel_poll_observations_added": counts.get("funnel_poll", {}).get("added", 0),
    }, ensure_ascii=False, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true", help="publish previously collected exports")
    args = parser.parse_args()
    if args.publish:
        _publish()
        return 0
    status = asyncio.run(sync_once())
    print(json.dumps({
        "ok": status.get("ok"),
        "period": status.get("period"),
        "funnel_period": status.get("funnel_period"),
        "shops": {
            shop: {
                "ok": value.get("ok"),
                "campaigns": value.get("campaigns"),
                "ad_rows": value.get("ad_rows"),
                "funnel_rows": value.get("funnel_rows"),
                "funnel_nm_ids": value.get("funnel_nm_ids"),
                "errors": value.get("errors"),
            }
            for shop, value in (status.get("shops") or {}).items()
        },
    }, ensure_ascii=False, sort_keys=True))
    return 0 if status.get("ok") is True else 1


if __name__ == "__main__":
    sys.exit(main())
