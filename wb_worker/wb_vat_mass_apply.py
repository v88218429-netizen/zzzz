#!/usr/bin/env python3
"""Set supported WB product cards to VAT rate 5 and verify by reading cards back."""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

BASE = "https://content-api.wildberries.ru"
CARDS_URL = BASE + "/content/v2/get/cards/list"
UPDATE_URL = BASE + "/content/v2/cards/update"
VAT_DIRECTORY_URL = BASE + "/content/v2/directory/vat"
VAT_CHAR_ID = 15001405
PAGE_LIMIT = 100
MAX_PAGES = 200


class WBError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {safe_error_body(body)}")
        self.status = status
        self.body = body


def safe_error_body(raw: str) -> str:
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        parsed = {"text": raw}
    if isinstance(parsed, dict):
        allowed = ("title", "detail", "code", "status", "statusText", "requestId", "origin", "error", "errorText", "additionalErrors", "text")
        parsed = {key: parsed[key] for key in allowed if key in parsed}
    else:
        parsed = {"body_type": type(parsed).__name__}
    text = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]", text)
    text = re.sub(r"\b[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b", "[redacted-jwt]", text)
    text = re.sub(r"\b[A-Za-z0-9_+/=-]{40,}\b", "[redacted]", text)
    return text[:1000]


def request_json(token: str, method: str, url: str, payload: Any | None = None, timeout: int = 90) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {"Authorization": token, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise WBError(exc.code, raw) from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"{method} {urllib.parse.urlsplit(url).path} network error ({type(exc).__name__})") from None
    try:
        value = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        raise RuntimeError(f"{method} {urllib.parse.urlsplit(url).path} returned non-JSON status=200") from None
    if not isinstance(value, dict):
        raise RuntimeError(f"{method} {urllib.parse.urlsplit(url).path} returned non-object JSON")
    return value


def fetch_cards(token: str) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    cursor: dict[str, Any] = {"limit": PAGE_LIMIT}
    for page in range(MAX_PAGES):
        if page:
            time.sleep(0.65)
        result = request_json(token, "POST", CARDS_URL, {"settings": {"sort": {"ascending": True}, "filter": {"withPhoto": -1}, "cursor": cursor}})
        batch = result.get("cards")
        next_cursor = result.get("cursor") or {}
        if not isinstance(batch, list):
            raise RuntimeError("WB cards response has no cards array")
        cards.extend(card for card in batch if isinstance(card, dict))
        if len(batch) < PAGE_LIMIT or not next_cursor.get("updatedAt") or next_cursor.get("nmID") is None:
            break
        cursor = {"limit": PAGE_LIMIT, "updatedAt": next_cursor["updatedAt"], "nmID": next_cursor["nmID"]}
    else:
        raise RuntimeError("WB cards pagination safety limit reached")
    return cards


def values_as_strings(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return [str(item).strip() for item in values if item is not None]


def has_vat_five(card: dict[str, Any]) -> bool:
    chars = card.get("characteristics")
    if not isinstance(chars, list):
        return False
    for char in chars:
        if isinstance(char, dict) and int(char.get("id") or 0) == VAT_CHAR_ID:
            vals = values_as_strings(char.get("value"))
            return vals == ["5"]
    return False


def get_subject_has_vat(token: str, subject_id: int, memo: dict[int, bool]) -> bool:
    if subject_id in memo:
        return memo[subject_id]
    url = BASE + "/content/v2/object/charcs/" + str(subject_id)
    response = request_json(token, "GET", url)
    data = response.get("data")
    if isinstance(data, dict):
        data = data.get("characteristics") or data.get("charcs")
    if not isinstance(data, list):
        raise RuntimeError(f"subject metadata is missing characteristics for subjectID={subject_id}")
    found = any(isinstance(item, dict) and int(item.get("charcID") or item.get("id") or 0) == VAT_CHAR_ID for item in data)
    memo[subject_id] = found
    return found


def project_card(card: dict[str, Any], *, append_vat: bool) -> dict[str, Any]:
    required = ("nmID", "vendorCode")
    for key in required:
        if card.get(key) in (None, ""):
            raise RuntimeError(f"card missing required field {key}")
    chars = card.get("characteristics")
    if not isinstance(chars, list):
        raise RuntimeError(f"card {card.get('nmID')} has no characteristics array")
    projected_chars: list[dict[str, Any]] = []
    vat_found = False
    for item in chars:
        if not isinstance(item, dict) or item.get("id") is None:
            raise RuntimeError(f"card {card.get('nmID')} has malformed characteristic")
        char_id = int(item["id"])
        value = item.get("value")
        if char_id == VAT_CHAR_ID:
            vat_found = True
            value = ["5"]
        projected_chars.append({"id": char_id, "value": value})
    if append_vat and not vat_found:
        projected_chars.append({"id": VAT_CHAR_ID, "value": ["5"]})
    sizes = card.get("sizes")
    if not isinstance(sizes, list):
        raise RuntimeError(f"card {card.get('nmID')} has no sizes array")
    projected_sizes = []
    for size in sizes:
        if not isinstance(size, dict):
            raise RuntimeError(f"card {card.get('nmID')} has malformed size")
        projected_sizes.append({key: size[key] for key in ("chrtID", "techSize", "wbSize", "skus", "price") if key in size})
    result: dict[str, Any] = {
        "nmID": int(card["nmID"]),
        "vendorCode": str(card["vendorCode"]),
        "characteristics": projected_chars,
        "sizes": projected_sizes,
    }
    for key in ("kizMarked", "brand", "title", "description", "gtin"):
        if key in card:
            result[key] = card[key]
    dimensions = card.get("dimensions")
    if isinstance(dimensions, dict):
        result["dimensions"] = {key: dimensions[key] for key in ("length", "width", "height", "weightBrutto") if key in dimensions}
    documents = card.get("documents")
    if isinstance(documents, dict):
        docs: dict[str, Any] = {}
        if isinstance(documents.get("items"), list):
            allowed = ("id", "type", "number", "productNumber", "tradeName", "applicant", "startDate", "endDate", "isEndless")
            docs["items"] = [{key: item[key] for key in allowed if key in item} for item in documents["items"] if isinstance(item, dict)]
        if "excludeDocuments" in documents:
            docs["excludeDocuments"] = documents["excludeDocuments"]
        result["documents"] = docs
    return result


def verify_cards(token: str, nmids: set[int]) -> dict[int, bool]:
    current = fetch_cards(token)
    by_id = {int(card.get("nmID") or 0): card for card in current if card.get("nmID") is not None}
    return {nm_id: bool(by_id.get(nm_id) and has_vat_five(by_id[nm_id])) for nm_id in nmids}


def main() -> int:
    read_token = os.environ.get("WB_API_TOKEN_AP", "").strip()
    configured_write_token = os.environ.get("WB_API_TOKEN_AP_WRITE", "").strip()
    write_token = configured_write_token or read_token
    if not read_token:
        raise RuntimeError("WB_API_TOKEN_AP secret is empty")
    if not write_token:
        raise RuntimeError("No WB API token is configured")
    token_parts = write_token.split(".")
    if len(token_parts) != 3:
        raise RuntimeError("WB write token is not a JWT")
    encoded_payload = token_parts[1] + "=" * (-len(token_parts[1]) % 4)
    token_claims = json.loads(base64.urlsafe_b64decode(encoded_payload.encode("ascii")))
    token_flags = int(token_claims.get("s") or 0)
    personal = token_claims.get("acc") == 3 and token_claims.get("for") == "self" and token_claims.get("t") is False
    readonly_bit_29 = bool(token_flags & (1 << 29))
    readonly_bit_30 = bool(token_flags & (1 << 30))
    print("WB_TOKEN_TYPE_PERSONAL=" + ("yes" if personal else "no"))
    print("WB_TOKEN_READ_ONLY_BIT_29=" + ("yes" if readonly_bit_29 else "no"))
    print("WB_TOKEN_READ_ONLY_BIT_30=" + ("yes" if readonly_bit_30 else "no"))
    print("WB_WRITE_TOKEN_SOURCE=" + ("WB_API_TOKEN_AP_WRITE" if configured_write_token else "WB_API_TOKEN_AP"))
    if readonly_bit_29 or readonly_bit_30:
        raise RuntimeError("WB token metadata marks this credential read-only; no write was attempted")
    vat_dir = request_json(read_token, "GET", VAT_DIRECTORY_URL)
    vat_values = values_as_strings(vat_dir.get("data"))
    if "5" not in vat_values:
        raise RuntimeError("WB VAT directory does not contain the value 5")
    cards = fetch_cards(read_token)
    if not cards:
        raise RuntimeError("WB returned no cards; no writes were made")
    current_with_vat = sum(1 for card in cards if has_vat_five(card))
    changes: list[dict[str, Any]] = []
    skipped: list[tuple[int, str]] = []
    subject_support: dict[int, bool] = {}
    for card in cards:
        if has_vat_five(card):
            continue
        nm_id = int(card.get("nmID") or 0)
        chars = card.get("characteristics") if isinstance(card.get("characteristics"), list) else []
        vat_present = any(isinstance(item, dict) and int(item.get("id") or 0) == VAT_CHAR_ID for item in chars)
        append_vat = not vat_present
        if append_vat:
            subject_id = int(card.get("subjectID") or 0)
            if not subject_id or not get_subject_has_vat(read_token, subject_id, subject_support):
                skipped.append((nm_id, "VAT_CHARACTERISTIC_NOT_SUPPORTED_BY_SUBJECT"))
                continue
        changes.append(project_card(card, append_vat=append_vat))
    print(f"WB_CARDS_TOTAL={len(cards)}")
    print(f"WB_CARDS_ALREADY_5={current_with_vat}")
    print(f"WB_CARDS_TO_UPDATE={len(changes)}")
    print(f"WB_CARDS_SKIPPED={len(skipped)}")
    if skipped:
        print("WB_SKIPPED_IDS=" + ",".join(str(nm_id) + ":" + reason for nm_id, reason in skipped))
    if not changes:
        result = verify_cards(read_token, {int(card["nmID"]) for card in cards})
        failed = [str(nm_id) for nm_id, ok in result.items() if not ok]
        if failed:
            raise RuntimeError("No cards were written but VAT 5 readback is incomplete")
        print("WB_VAT_5_VERIFIED=yes")
        return 0
    body = json.dumps(changes, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > 10 * 1024 * 1024:
        raise RuntimeError("WB update request exceeds the 10 MB API limit")
    try:
        response = request_json(write_token, "POST", UPDATE_URL, changes, timeout=120)
    except WBError as exc:
        print(f"WB_UPDATE_HTTP_STATUS={exc.status}")
        print(f"WB_UPDATE_ERROR={safe_error_body(exc.body)}")
        raise RuntimeError("WB rejected direct cards update") from None
    print("WB_UPDATE_HTTP_STATUS=200")
    if response.get("error") is True or str(response.get("errorText") or "").strip():
        print("WB_UPDATE_ERROR=" + safe_error_body(json.dumps(response, ensure_ascii=False)))
        raise RuntimeError("WB returned an application-level card update error")
    target_ids = {int(card["nmID"]) for card in changes}
    deadline = time.time() + 150
    readback: dict[int, bool] = {}
    while time.time() < deadline:
        readback = verify_cards(read_token, target_ids)
        passed = sum(readback.values())
        print(f"WB_READBACK={passed}/{len(target_ids)}")
        if passed == len(target_ids):
            break
        time.sleep(15)
    failed = sorted(nm_id for nm_id, ok in readback.items() if not ok)
    if failed:
        print("WB_NOT_VERIFIED_IDS=" + ",".join(map(str, failed)))
        raise RuntimeError("WB accepted the request but readback did not confirm every update")
    print("WB_VAT_5_VERIFIED=yes")
    print(f"WB_VAT_5_UPDATED={len(target_ids)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"WB_VAT_APPLY_FAILED={type(exc).__name__}: {str(exc)[:500]}")
        raise SystemExit(1)
