#!/usr/bin/env python3
"""Fetch one cabinet's WB Content API cards and publish a read-only snapshot through the existing authenticated Apps Script bridge."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

CONTENT_URL = "https://content-api.wildberries.ru/content/v2/get/cards/list"
TARGET_SPREADSHEET_ID = "1--8PzvW7quVjolA5FmMS9tBNHA-IXGtFJyadWbt3eSA"
PAGE_SIZE = 100
MAX_PAGES = 200


class SyncError(RuntimeError):
    pass


def request_json(url: str, method: str, headers: dict[str, str], payload: Any | None = None,
                 timeout: int = 120, retries: int = 2) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request_headers = {"Accept": "application/json", **headers}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    retryable_statuses = {408, 429, 500, 502, 503, 504}
    raw = ""
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            if exc.code in retryable_statuses and attempt < retries:
                time.sleep(min(8, 2 ** attempt))
                continue
            raise SyncError(f"{method} {urllib.parse.urlsplit(url).path} returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < retries:
                time.sleep(min(8, 2 ** attempt))
                continue
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            raise SyncError(f"{method} {urllib.parse.urlsplit(url).path} network error ({type(reason).__name__})") from None
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise SyncError(f"{method} {urllib.parse.urlsplit(url).path} returned invalid JSON") from None
    if not isinstance(value, dict):
        raise SyncError(f"{method} {urllib.parse.urlsplit(url).path} returned a non-object response")
    return value

def fetch_cards(api_token: str) -> list[dict[str, Any]]:
    all_cards: list[dict[str, Any]] = []
    cursor: dict[str, Any] = {"limit": PAGE_SIZE}
    for page in range(MAX_PAGES):
        if page:
            time.sleep(0.65)
        response = request_json(
            CONTENT_URL,
            "POST",
            {"Authorization": api_token},
            {"settings": {"cursor": cursor, "filter": {"withPhoto": -1}}},
        )
        batch = response.get("cards")
        if batch is None and isinstance(response.get("data"), dict):
            batch = response["data"].get("cards")
        if not isinstance(batch, list):
            raise SyncError("WB response did not contain a cards array")
        all_cards.extend(card for card in batch if isinstance(card, dict))
        next_cursor = response.get("cursor") or (
            response.get("data", {}).get("cursor") if isinstance(response.get("data"), dict) else {}
        ) or {}
        if len(batch) < PAGE_SIZE or not next_cursor.get("updatedAt") or next_cursor.get("nmID") is None:
            break
        cursor = {"limit": PAGE_SIZE, "updatedAt": next_cursor["updatedAt"], "nmID": next_cursor["nmID"]}
    else:
        raise SyncError("WB pagination safety limit reached")

    unique: dict[str, dict[str, Any]] = {}
    for card in all_cards:
        nm_id = card.get("nmID") or card.get("nmId")
        if nm_id:
            unique[str(nm_id)] = card
    return list(unique.values())


def main() -> None:
    wb_token = os.environ.get("WB_API_TOKEN_AP", "").strip()
    webhook_secret = os.environ.get("WEBHOOK_SECRET", "").strip()
    deployment_id = os.environ.get("WEBAPP_DEPLOYMENT_ID", "").strip()
    if not wb_token:
        raise SyncError("WB_API_TOKEN_AP secret is empty")
    if not webhook_secret:
        raise SyncError("SELLMONITOR_GITHUB_WEBHOOK_SECRET secret is empty")
    if not deployment_id:
        raise SyncError("WEBAPP_DEPLOYMENT_ID is empty")

    cards = fetch_cards(wb_token)
    if not cards:
        raise SyncError("WB returned 0 cards; existing workbook data will remain unchanged")

    snapshot = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = {
        "token": webhook_secret,
        "action": "wb_content_manager_sync",
        "spreadsheet_id": TARGET_SPREADSHEET_ID,
        "cabinet": "AP",
        "snapshot": snapshot,
        "cards": cards,
    }
    bridge_url = "https://script.google.com/macros/s/" + deployment_id + "/exec"
    result = request_json(
        bridge_url,
        "POST",
        {"Accept": "application/json"},
        body,
        timeout=180,
        retries=1,
    )
    payload = result.get("result") if isinstance(result.get("result"), dict) else result
    if result.get("ok") is not True or payload.get("readback") != "pass":
        # Emit only a strict machine error code; never log card data or secrets.
        error_fields = [result.get("error"), payload.get("error"), result.get("message"), payload.get("message")]
        safe_codes: list[str] = []
        for field in error_fields:
            if isinstance(field, str):
                safe_codes.extend(re.findall(r"WB_CONTENT_MANAGER_[A-Z0-9_]{1,80}", field))
        if safe_codes:
            print("BRIDGE_ERROR_CODE=" + safe_codes[0])
        for field in error_fields:
            if isinstance(field, str) and field:
                hint = re.sub(r"https?://\S+", "[url]", field)
                hint = re.sub(r"(?i)(token|secret|authorization|api[_-]?key)(\s*[:=]\s*|\s+)[^\s,;|]+", r"\1=[redacted]", hint)
                hint = re.sub(r"[A-Za-z0-9_-]{40,}", "[opaque]", hint)
                hint = re.sub(r"\b[0-9]{6,}\b", "[number]", hint)
                hint = re.sub(r"[^\w\s:./()!?=-]", "?", hint)[:180]
                print("BRIDGE_ERROR_HINT=" + hint)
                break
        print("BRIDGE_ACTION=" + str(result.get("action") or "unknown"))
        print("BRIDGE_OK=" + str(result.get("ok") is True).lower())
        print("BRIDGE_RESPONSE_KEYS=" + ",".join(sorted(str(key) for key in result.keys())))
        print("BRIDGE_PAYLOAD_KEYS=" + ",".join(sorted(str(key) for key in payload.keys())))
        raise SyncError("Authenticated Apps Script bridge did not confirm a matching readback")
    print(
        "SYNC_OK"
        " cabinet=AP"
        f" cards={int(payload.get('cards') or len(cards))}"
        f" characteristics={int(payload.get('characteristics') or 0)}"
        f" document_rows={int(payload.get('document_rows') or 0)}"
        f" matched_existing={int(payload.get('matched_existing') or 0)}/{int(payload.get('existing_cards') or 0)}"
        f" snapshot_utc={snapshot}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"SYNC_FAILED {type(exc).__name__}: {str(exc)[:300]}")
        raise SystemExit(1)
