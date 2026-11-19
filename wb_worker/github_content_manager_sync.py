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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import subprocess
from typing import Any

from PIL import Image

CONTENT_URL = "https://content-api.wildberries.ru/content/v2/get/cards/list"
TARGET_SPREADSHEET_ID = "1--8PzvW7quVjolA5FmMS9tBNHA-IXGtFJyadWbt3eSA"
PAGE_SIZE = 20
MAX_PAGES = 200
IMAGE_ASSET_DIR = Path("wb-content-manager-images/AP")
IMAGE_RAW_BASE = "https://raw.githubusercontent.com/v88218429-netizen/zzzz/mainggg/wb-content-manager-images/AP"
MAX_IMAGE_BYTES = 5 * 1024 * 1024


class SyncError(RuntimeError):
    pass


def request_json(url: str, method: str, headers: dict[str, str], payload: Any | None = None,
                 timeout: int = 60, retries: int = 2) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request_headers = {"Accept": "application/json", **headers}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    retryable_statuses = {408, 429, 500, 502, 503, 504}
    raw = ""
    response_status = "unknown"
    response_content_type = "unknown"
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_status = str(response.status)
                response_content_type = response.headers.get("Content-Type", "unknown").split(";")[0]
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
    raw = raw.lstrip("\ufeff\r\n\t ")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        plain = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
        plain = re.sub(r"<[^>]*>", " ", plain)
        lowered = plain.lower()
        if "authorization is required" in lowered or "sign in" in lowered:
            category = "AUTH_REQUIRED"
        elif "script function not found" in lowered:
            category = "ENTRYPOINT_NOT_FOUND"
        elif "service invoked too many times" in lowered or "quota" in lowered:
            category = "GOOGLE_QUOTA_OR_RATE_LIMIT"
        elif "maximum execution time" in lowered or "timed out" in lowered:
            category = "SCRIPT_TIMEOUT"
        elif "service unavailable" in lowered or "internal error" in lowered:
            category = "GOOGLE_SERVICE_ERROR"
        elif raw.lstrip().startswith("<"):
            category = "HTML_RESPONSE"
        else:
            category = "NON_JSON_BODY"
        title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
        summary = title_match.group(1) if title_match else plain
        summary = re.sub(r"<[^>]*>", " ", summary)
        summary = re.sub(r"https?://\S+", "[url]", summary)
        summary = re.sub(r"\b[A-Za-z0-9_+/=-]{28,}\b", "[redacted]", summary)
        summary = re.sub(r"\b\d{7,}\b", "[number]", summary)
        summary = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[email]", summary)
        summary = re.sub(r"\s+", " ", summary).strip()[:180]
        raise SyncError(
            f"{method} {urllib.parse.urlsplit(url).path} returned invalid JSON "
            f"(status={response_status}; content_type={response_content_type}; category={category}; summary={summary or 'empty'})"
        ) from None
    if not isinstance(value, dict):
        raise SyncError(f"{method} {urllib.parse.urlsplit(url).path} returned a non-object response")
    return value

def fetch_cards(api_token: str) -> list[dict[str, Any]]:
    all_cards: list[dict[str, Any]] = []
    cursor: dict[str, Any] = {"limit": PAGE_SIZE}
    for page in range(MAX_PAGES):
        if page:
            time.sleep(0.65)
        try:
            response = request_json(
                CONTENT_URL,
                "POST",
                {"Authorization": api_token},
                {"settings": {"cursor": cursor, "filter": {"withPhoto": -1}}},
            )
        except SyncError as exc:
            raise SyncError(f"WB_PAGE_{page + 1}_{exc}") from None
        batch = response.get("cards")
        if batch is None and isinstance(response.get("data"), dict):
            batch = response["data"].get("cards")
        if not isinstance(batch, list):
            raise SyncError("WB response did not contain a cards array")
        all_cards.extend(card for card in batch if isinstance(card, dict))
        print(f"WB_PAGE_OK page={page + 1} cards={len(batch)}")
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



def photo_source(card: dict[str, Any]) -> str:
    photos = card.get("photos")
    if not isinstance(photos, list) or not photos or not isinstance(photos[0], dict):
        return ""
    photo = photos[0]
    for key in ("c246x328", "big", "hq", "square"):
        value = photo.get(key)
        if isinstance(value, str) and value.startswith("https://"):
            return value
    return ""


def convert_photo_to_jpeg(nm_id: str, source_url: str) -> str:
    request = urllib.request.Request(
        source_url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; WBContentSheet/1.0)",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        },
    )
    raw = b""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read(MAX_IMAGE_BYTES + 1)
            break
        except urllib.error.HTTPError as exc:
            if exc.code in {408, 429, 500, 502, 503, 504} and attempt < 2:
                time.sleep(1 + attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt < 2:
                time.sleep(1 + attempt)
                continue
            raise
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("image_size")
    with Image.open(BytesIO(raw)) as source:
        source.seek(0)
        rgba = source.convert("RGBA")
        rgba.thumbnail((246, 328), Image.Resampling.LANCZOS)
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        output = BytesIO()
        background.save(output, format="JPEG", quality=84, optimize=True, progressive=True)
    target = IMAGE_ASSET_DIR / (nm_id + ".jpg")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(output.getvalue())
    return IMAGE_RAW_BASE + "/" + nm_id + ".jpg"


def publish_photo_assets() -> None:
    added = subprocess.run(
        ["git", "add", "-f", str(IMAGE_ASSET_DIR)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if added.returncode:
        raise SyncError("WB_PHOTO_ASSETS_GIT_ADD_FAILED")
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if staged.returncode == 0:
        return
    if staged.returncode != 1:
        raise SyncError("WB_PHOTO_ASSETS_GIT_DIFF_FAILED")
    for args in (
        ["git", "config", "user.name", "github-actions[bot]"],
        ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
        ["git", "commit", "-m", "Refresh WB card photo thumbnails"],
    ):
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode:
            raise SyncError("WB_PHOTO_ASSETS_GIT_COMMIT_FAILED")
    pushed = subprocess.run(
        ["git", "push", "origin", "HEAD:refs/heads/mainggg"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if pushed.returncode:
        raise SyncError("WB_PHOTO_ASSETS_GIT_PUSH_FAILED")
    print("WB_PHOTO_ASSETS_PUBLISHED=yes")


def prepare_photo_assets(cards: list[dict[str, Any]]) -> dict[str, str]:
    sources: dict[str, str] = {}
    for card in cards:
        nm_id = str(card.get("nmID") or card.get("nmId") or "")
        url = photo_source(card)
        if re.fullmatch(r"[0-9]{1,20}", nm_id) and url:
            sources[nm_id] = url
    if not sources:
        raise SyncError("WB_PHOTO_URLS_NOT_FOUND")

    urls: dict[str, str] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(convert_photo_to_jpeg, nm_id, url): nm_id
            for nm_id, url in sources.items()
        }
        for future in as_completed(futures):
            nm_id = futures[future]
            try:
                urls[nm_id] = future.result()
            except Exception as exc:
                errors.append(type(exc).__name__)
    if errors:
        raise SyncError("WB_PHOTO_ASSET_CONVERSION_FAILED count=" + str(len(errors)))

    for card in cards:
        nm_id = str(card.get("nmID") or card.get("nmId") or "")
        image_url = urls.get(nm_id, "")
        if image_url:
            card["_smcm_image_url"] = image_url
        else:
            card.pop("_smcm_image_url", None)

    publish_photo_assets()
    print("WB_PHOTO_ASSETS_READY count=" + str(len(urls)) + " missing=" + str(len(cards) - len(urls)))
    return urls

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
    photo_urls = prepare_photo_assets(cards)

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
        f" photo_assets={len(photo_urls)}"
        f" preview_trigger_ready={str(payload.get('preview_trigger_ready') is True).lower()}"
        f" snapshot_utc={snapshot}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"SYNC_FAILED {type(exc).__name__}: {str(exc)[:300]}")
        raise SystemExit(1)
