#!/usr/bin/env python3
# Read-only WB Content API sync for the WB API Manager spreadsheet.
# The token stays in GitHub Actions Secrets and is never written to the workbook or logs.
from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

SPREADSHEET_ID = os.environ.get("TARGET_SPREADSHEET_ID", "1--8PzvW7quVjolA5FmMS9tBNHA-IXGtFJyadWbt3eSA")
EXPECTED_TITLE = "WB API Manager — карточки и массовое редактирование"
CONTENT_URL = "https://content-api.wildberries.ru/content/v2/get/cards/list"
SHEETS_API = "https://sheets.googleapis.com/v4"
CABINET_SOURCE = "Текущий кабинет по API-ключу"
CABINET_STATUS = "Дом Саныча (AP)"
PAGE_SIZE = 100
MAX_PAGES = 200

SHEET_WIDTHS = {
    "Карточки WB": 17,
    "Характеристики": 11,
    "Документы": 20,
    "Полный API": 20,
}


class ApiError(RuntimeError):
    pass


class Formula:
    def __init__(self, value: str):
        self.value = value


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(part for part in (text(item) for item in value) if part)
    if isinstance(value, dict):
        return compact_json(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def norm_id(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def is_true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().upper() in {"TRUE", "1", "ДА"})


def cell_data(value: Any) -> dict[str, Any]:
    if isinstance(value, Formula):
        return {"userEnteredValue": {"formulaValue": value.value}}
    if isinstance(value, bool):
        return {"userEnteredValue": {"boolValue": value}}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"userEnteredValue": {"numberValue": value}}
    if value is None or value == "":
        return {}
    return {"userEnteredValue": {"stringValue": str(value)}}


def http_json(url: str, method: str = "GET", headers: dict[str, str] | None = None,
              payload: Any | None = None, timeout: int = 45) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        # Never emit response bodies or request headers; they can contain sensitive details.
        raise ApiError(f"{method} {urllib.parse.urlsplit(url).path} returned HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise ApiError(f"{method} {urllib.parse.urlsplit(url).path} network error ({type(exc.reason).__name__})") from None
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise ApiError(f"{method} {urllib.parse.urlsplit(url).path} returned invalid JSON") from None
    if not isinstance(value, dict):
        raise ApiError(f"{method} {urllib.parse.urlsplit(url).path} returned a non-object response")
    return value


def recursive_find(obj: Any, names: set[str]) -> str:
    if isinstance(obj, dict):
        for key, value in obj.items():
            normalized = str(key).lower().replace("_", "")
            if normalized in names and isinstance(value, str) and value:
                return value
        for value in obj.values():
            found = recursive_find(value, names)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = recursive_find(value, names)
            if found:
                return found
    return ""


def profile_list(config: dict[str, Any]) -> list[dict[str, Any]]:
    token_map = config.get("tokens")
    if isinstance(token_map, dict):
        profiles = [item for item in token_map.values() if isinstance(item, dict)]
    elif isinstance(token_map, list):
        profiles = [item for item in token_map if isinstance(item, dict)]
    else:
        profiles = []
    return profiles or [config]


def google_request(access_token: str, method: str, url: str, payload: Any | None = None) -> dict[str, Any]:
    return http_json(
        url,
        method=method,
        headers={"Authorization": "Bearer " + access_token},
        payload=payload,
        timeout=90,
    )


def probe_sheet_access(access_token: str) -> dict[str, Any]:
    url = f"{SHEETS_API}/spreadsheets/{urllib.parse.quote(SPREADSHEET_ID, safe='')}?fields=spreadsheetId,properties.title,sheets.properties"
    return google_request(access_token, "GET", url)


def refresh_google_token(profile: dict[str, Any], config: dict[str, Any]) -> str:
    refresh = recursive_find(profile, {"refreshtoken"})
    client_id = recursive_find(profile, {"clientid"}) or recursive_find(config, {"clientid"})
    client_secret = recursive_find(profile, {"clientsecret"}) or recursive_find(config, {"clientsecret"})
    if not (refresh and client_id and client_secret):
        return ""
    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh,
        "grant_type": "refresh_token",
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
        return ""
    return str(result.get("access_token") or "")


def google_access_token(config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    profiles = profile_list(config)
    profiles.sort(key=lambda item: 0 if "spreadsheets" in str(item.get("scope") or "").lower() else 1)
    last_code = ""
    for profile in profiles:
        candidates = [
            recursive_find(profile, {"accesstoken"}),
            refresh_google_token(profile, config),
        ]
        seen: set[str] = set()
        for token in candidates:
            if not token or token in seen:
                continue
            seen.add(token)
            try:
                metadata = probe_sheet_access(token)
                return token, metadata
            except ApiError as exc:
                last_code = str(exc)
                continue
    raise ApiError("CLASPRC_JSON could not read the target spreadsheet" + (f" ({last_code})" if last_code else ""))


def sheet_values(access: str, title: str, a1: str, render: str = "UNFORMATTED_VALUE") -> list[list[Any]]:
    quoted = urllib.parse.quote(a1, safe="")
    query = urllib.parse.urlencode({"majorDimension": "ROWS", "valueRenderOption": render})
    url = f"{SHEETS_API}/spreadsheets/{urllib.parse.quote(SPREADSHEET_ID, safe='')}/values/{quoted}?{query}"
    return google_request(access, "GET", url).get("values") or []


def wb_cards(token: str) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    cursor: dict[str, Any] = {"limit": PAGE_SIZE}
    for page in range(MAX_PAGES):
        if page:
            time.sleep(0.65)
        response = http_json(
            CONTENT_URL,
            method="POST",
            headers={"Authorization": token},
            payload={"settings": {"cursor": cursor, "filter": {"withPhoto": -1}}},
            timeout=90,
        )
        batch = response.get("cards")
        if batch is None and isinstance(response.get("data"), dict):
            batch = response["data"].get("cards")
        if not isinstance(batch, list):
            raise ApiError("WB response did not contain a cards array")
        cards.extend(card for card in batch if isinstance(card, dict))
        next_cursor = response.get("cursor") or (
            response.get("data", {}).get("cursor") if isinstance(response.get("data"), dict) else {}
        ) or {}
        if len(batch) < PAGE_SIZE or not next_cursor.get("updatedAt") or next_cursor.get("nmID") is None:
            break
        cursor = {"limit": PAGE_SIZE, "updatedAt": next_cursor["updatedAt"], "nmID": next_cursor["nmID"]}
    else:
        raise ApiError("WB pagination safety limit reached")
    unique: dict[str, dict[str, Any]] = {}
    for card in cards:
        nm = norm_id(card.get("nmID") or card.get("nmId"))
        if nm:
            unique[nm] = card
    return list(unique.values())


def docs_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {}
    if isinstance(value, list):
        return {"items": value}
    if not isinstance(value, dict):
        return {"items": []}
    if isinstance(value.get("items"), list):
        return value
    docs = value.get("documents")
    if isinstance(docs, dict) and isinstance(docs.get("items"), list):
        return docs
    if isinstance(docs, list):
        return {"items": docs, "overallVerdict": value.get("overallVerdict") or {}}
    return {"items": [], "overallVerdict": value.get("overallVerdict") or {}}


def map_by(rows: list[list[Any]], key_fn) -> dict[str, list[Any]]:
    result: dict[str, list[Any]] = {}
    for row in rows:
        key = key_fn(row)
        if key:
            result[key] = row
    return result


def existing_card_selection(rows: list[list[Any]]) -> dict[str, bool]:
    return map_by(rows, lambda row: norm_id(row[3] if len(row) > 3 else None)) | {
        norm_id(row[3]): is_true(row[0]) for row in rows if len(row) > 3 and norm_id(row[3])
    }


def build_card_rows(cards: list[dict[str, Any]], snapshot: str, selections: dict[str, bool]):
    rows: list[list[Any]] = []
    for card in cards:
        nm = norm_id(card.get("nmID") or card.get("nmId"))
        attrs = card.get("characteristics") or []
        if not isinstance(attrs, list):
            attrs = []
        docs = docs_obj(card.get("documents")).get("items") or []
        sizes = card.get("sizes") or []
        if not isinstance(sizes, list):
            sizes = []
        barcodes: list[str] = []
        for size in sizes:
            if isinstance(size, dict) and isinstance(size.get("skus"), list):
                barcodes.extend(text(sku) for sku in size["skus"])
        dimensions = card.get("dimensions") or {}
        if not isinstance(dimensions, dict):
            dimensions = {}
        photos = card.get("photos") or []
        first_photo = photos[0] if isinstance(photos, list) and photos and isinstance(photos[0], dict) else {}
        photo_url = first_photo.get("c246x328") or first_photo.get("big") or first_photo.get("hq") or first_photo.get("square") or ""
        photo = Formula('=IMAGE("' + str(photo_url).replace('"', '""') + '";4;100;75)') if photo_url else ""
        rows.append([
            selections.get(nm, False), photo, CABINET_SOURCE, nm,
            text(card.get("vendorCode")), text(card.get("title")),
            text(card.get("subjectName") or card.get("subject")), text(card.get("brand")),
            "size=" + str(len(sizes)) + "; barcodes=" + ", ".join(barcodes),
            "Получено из WB Content API", text(card.get("updatedAt")),
            "WB Content API / свежее чтение",
            "Выгружено: " + str(len(attrs)) + " характеристик, " + str(len(docs)) + " документов",
            "width=" + text(dimensions.get("width")) + "; height=" + text(dimensions.get("height")) +
            "; length=" + text(dimensions.get("length")) + "; weightBrutto=" + text(dimensions.get("weightBrutto")) +
            "; isValid=" + text(dimensions.get("isValid")),
            text(card.get("needKiz")), "Загружено: " + str(len(attrs)), snapshot,
        ])
    return rows


def build_attr_rows(cards: list[dict[str, Any]], controls: dict[str, dict[str, Any]]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for card in cards:
        nm = norm_id(card.get("nmID") or card.get("nmId"))
        attrs = card.get("characteristics") or []
        if not isinstance(attrs, list):
            continue
        for attr in attrs:
            if not isinstance(attr, dict):
                continue
            attr_id = text(attr.get("id"))
            control = controls.get(nm + "|" + attr_id, {})
            rows.append([
                bool(control.get("apply")), CABINET_SOURCE, nm,
                text(card.get("vendorCode")), text(card.get("title")),
                text(card.get("subjectName") or card.get("subject")),
                attr_id, text(attr.get("name")), text(attr.get("value")),
                control.get("newValue", ""), "Текущее значение из WB Content API",
            ])
    return rows


def build_doc_rows(cards: list[dict[str, Any]], controls: dict[str, dict[str, Any]]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for card in cards:
        nm = norm_id(card.get("nmID") or card.get("nmId"))
        docs = docs_obj(card.get("documents"))
        items = docs.get("items") if isinstance(docs.get("items"), list) else []
        if not items:
            items = [{}]
        for doc in items:
            if not isinstance(doc, dict):
                doc = {}
            doc_id = text(doc.get("id"))
            doc_type = text(doc.get("type"))
            doc_number = text(doc.get("number"))
            key = nm + "|" + (doc_id or ("type:" + doc_type + "|number:" + doc_number))
            control = controls.get(key) or controls.get(nm + "|none") or {}
            verdict = doc.get("verdict") if isinstance(doc.get("verdict"), dict) else {}
            verdict_parts: list[str] = []
            if "verified" in verdict:
                verdict_parts.append("проверен: " + text(verdict.get("verified")))
            if "status" in verdict:
                verdict_parts.append("статус: " + text(verdict.get("status")))
            if verdict.get("reason"):
                verdict_parts.append("причина: " + text(verdict.get("reason")))
            endless = "Да" if doc.get("isEndless") is True else ("Нет" if doc.get("isEndless") is False else "")
            rows.append([
                bool(control.get("apply")), CABINET_SOURCE, nm,
                text(card.get("vendorCode")), text(card.get("title")),
                text(card.get("subjectName") or card.get("subject")),
                doc_type, doc_number, text(doc.get("startDate")), text(doc.get("endDate")),
                "; ".join(verdict_parts), control.get("action", ""),
                control.get("newType", ""), control.get("newNumber", ""),
                control.get("newStart", ""), control.get("newEnd", ""),
                doc_id, text(doc.get("createdAt")), endless, compact_json(verdict),
            ])
    return rows


def build_raw_rows(cards: list[dict[str, Any]], snapshot: str) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for card in cards:
        rows.append([
            text(card.get("nmID") or card.get("nmId")), text(card.get("imtID")),
            text(card.get("subjectID")), text(card.get("subjectName") or card.get("subject")),
            text(card.get("vendorCode")), text(card.get("brand")), text(card.get("title")),
            text(card.get("description")), text(card.get("needKiz")), text(card.get("isKiz")),
            compact_json(card.get("dimensions") or {}), compact_json(card.get("sizes") or []),
            compact_json(card.get("characteristics") or []), compact_json(card.get("photos") or []),
            compact_json(docs_obj(card.get("documents"))), compact_json(card.get("tags") or []),
            text(card.get("video")), text(card.get("createdAt")), text(card.get("updatedAt")), snapshot,
        ])
    return rows


def attr_control_map(rows: list[list[Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        nm = norm_id(row[2] if len(row) > 2 else None)
        attr_id = text(row[6] if len(row) > 6 else "")
        if nm and attr_id:
            result[nm + "|" + attr_id] = {
                "apply": is_true(row[0] if row else False),
                "newValue": row[9] if len(row) > 9 else "",
            }
    return result


def doc_control_map(rows: list[list[Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        nm = norm_id(row[2] if len(row) > 2 else None)
        if not nm:
            continue
        doc_id = text(row[16] if len(row) > 16 else "")
        doc_type = text(row[6] if len(row) > 6 else "")
        doc_number = text(row[7] if len(row) > 7 else "")
        value = {
            "apply": is_true(row[0] if row else False),
            "action": row[11] if len(row) > 11 else "",
            "newType": row[12] if len(row) > 12 else "",
            "newNumber": row[13] if len(row) > 13 else "",
            "newStart": row[14] if len(row) > 14 else "",
            "newEnd": row[15] if len(row) > 15 else "",
        }
        secondary = "type:" + doc_type + "|number:" + doc_number
        result[nm + "|" + (doc_id or secondary)] = value
        if not doc_type and not doc_number:
            result[nm + "|none"] = value
    return result


def build_mass_preview(card_rows: list[list[Any]], attr_rows: list[list[Any]],
                       doc_rows: list[list[Any]], params_rows: list[list[Any]]):
    params = [str(row[0] if row and row[0] is not None else "").strip() for row in params_rows]
    params += [""] * max(0, 11 - len(params))
    shop_filter, category_filter = params[0], params[1]
    operation = params[2] or "Характеристика"
    attr_id, attr_name, new_value = params[3], params[4], params[5]
    doc_type, doc_number, doc_start, doc_end, doc_action = params[6], params[7], params[8], params[9], params[10]
    selected = []
    for card in card_rows:
        if not is_true(card[0] if card else False):
            continue
        shop = text(card[2] if len(card) > 2 else "")
        category = text(card[6] if len(card) > 6 else "")
        if shop_filter and shop_filter != "Все магазины" and shop != shop_filter:
            continue
        if category_filter and category_filter != "Все категории" and category != category_filter:
            continue
        selected.append(card)
    output: list[list[Any]] = []
    good = 0
    bad = 0
    for card in selected:
        nm = norm_id(card[3] if len(card) > 3 else None)
        shop = text(card[2] if len(card) > 2 else "")
        vendor = text(card[4] if len(card) > 4 else "")
        title = text(card[5] if len(card) > 5 else "")
        category = text(card[6] if len(card) > 6 else "")
        field = before = after = reason = ""
        if "характер" in operation.lower():
            match = None
            for attr in attr_rows:
                if norm_id(attr[2] if len(attr) > 2 else None) != nm:
                    continue
                same_id = bool(attr_id) and text(attr[6] if len(attr) > 6 else "") == attr_id
                same_name = (not attr_id and bool(attr_name) and
                             text(attr[7] if len(attr) > 7 else "").lower() == attr_name.lower())
                if same_id or same_name:
                    match = attr
                    break
            field = (text(match[7]) + " [ID " + text(match[6]) + "]") if match else (attr_name or attr_id or "Характеристика")
            before = text(match[8] if len(match) > 8 else "") if match else ""
            after = new_value
            if not attr_id and not attr_name:
                reason = "Укажи ID или название характеристики"
            elif not new_value:
                reason = "Не заполнено новое значение"
            elif not match:
                reason = "Характеристика не найдена в загруженных данных этой карточки"
        else:
            field = "Документ: тип " + (doc_type or "—") + ", номер " + (doc_number or "—")
            existing = [
                "тип " + text(doc[6] if len(doc) > 6 else "") + ", № " + text(doc[7] if len(doc) > 7 else "")
                for doc in doc_rows
                if norm_id(doc[2] if len(doc) > 2 else None) == nm
                and (text(doc[6] if len(doc) > 6 else "") or text(doc[7] if len(doc) > 7 else ""))
            ]
            before = "; ".join(existing[:3]) if existing else "Документов в снимке нет"
            after = "тип " + (doc_type or "—") + ", № " + (doc_number or "—")
            if doc_start:
                after += ", с " + doc_start
            if doc_end:
                after += ", до " + doc_end
            if doc_action:
                after += ", " + doc_action
            if not doc_type or not doc_number:
                reason = "Укажи тип и номер документа"
        result = reason or "Подходит для предпросмотра; запись в WB отключена"
        send = "НЕТ — запись в WB отключена"
        output.append([
            shop, nm, (vendor + " · " if vendor else "") + title,
            category, field, before, after, result, send,
        ])
        if reason:
            bad += 1
        else:
            good += 1
    if not output:
        output_for_sheet = [[None, None, None, None, None, None, None, None, "Нет выбранных карточек"]]
    else:
        output_for_sheet = output
    return len(selected), good, bad, output_for_sheet


def update_cells_request(sheet_id: int, row_index: int, column_index: int,
                         rows: list[list[Any]], width: int | None = None) -> dict[str, Any]:
    data = []
    for row in rows:
        values = [cell_data(value) for value in row]
        if width is not None and len(values) < width:
            values.extend({} for _ in range(width - len(values)))
        data.append({"values": values})
    return {
        "updateCells": {
            "start": {"sheetId": sheet_id, "rowIndex": row_index, "columnIndex": column_index},
            "rows": data,
            "fields": "userEnteredValue",
        }
    }


def clear_request(sheet_id: int, width: int, start_row: int = 1, end_row: int = 3000) -> dict[str, Any]:
    return {
        "updateCells": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": start_row,
                "endRowIndex": end_row,
                "startColumnIndex": 0,
                "endColumnIndex": width,
            },
            "fields": "userEnteredValue",
        }
    }


def chunked_update_requests(sheet_id: int, row_index: int, width: int,
                            rows: list[list[Any]], chunk_size: int = 300) -> list[dict[str, Any]]:
    requests = []
    for offset in range(0, len(rows), chunk_size):
        requests.append(update_cells_request(sheet_id, row_index + offset, 0, rows[offset:offset + chunk_size], width))
    return requests


def main() -> None:
    wb_token = os.environ.get("WB_API_TOKEN_AP", "").strip()
    clasp_raw = os.environ.get("CLASPRC_JSON", "").strip()
    if not wb_token:
        raise ApiError("WB_API_TOKEN_AP secret is empty")
    if not clasp_raw:
        raise ApiError("CLASPRC_JSON secret is empty")

    try:
        clasp_config = json.loads(clasp_raw)
    except json.JSONDecodeError:
        raise ApiError("CLASPRC_JSON is not valid JSON") from None
    if not isinstance(clasp_config, dict):
        raise ApiError("CLASPRC_JSON has an unexpected format")

    access_token, metadata = google_access_token(clasp_config)
    title = (metadata.get("properties") or {}).get("title")
    if title != EXPECTED_TITLE:
        raise ApiError("Google OAuth resolved a different spreadsheet; no data was changed")
    sheet_ids = {
        (sheet.get("properties") or {}).get("title"): (sheet.get("properties") or {}).get("sheetId")
        for sheet in metadata.get("sheets", [])
    }
    required = set(SHEET_WIDTHS) | {"Старт", "Массовая правка"}
    missing = sorted(required - set(sheet_ids))
    if missing:
        raise ApiError("Target spreadsheet is missing required tabs: " + ", ".join(missing))

    old_card_rows = sheet_values(access_token, "Карточки WB", "'Карточки WB'!A2:Q3000")
    old_ids = {
        norm_id(row[3]) for row in old_card_rows
        if len(row) > 3 and norm_id(row[3])
    }
    if not old_ids:
        raise ApiError("No existing nmID values found; refusing to guess the target WB cabinet")

    cards = wb_cards(wb_token)
    if not cards:
        raise ApiError("WB returned 0 cards; existing workbook data was left untouched")
    fresh_ids = {norm_id(card.get("nmID") or card.get("nmId")) for card in cards}
    overlap = len(old_ids & fresh_ids)
    minimum_overlap = min(len(old_ids), max(10, math.ceil(len(old_ids) * 0.5)))
    if overlap < minimum_overlap:
        raise ApiError(
            f"AP token matched only {overlap}/{len(old_ids)} existing cards "
            f"(minimum {minimum_overlap}); existing data was left untouched"
        )

    attr_control_rows = sheet_values(access_token, "Характеристики", "'Характеристики'!A2:K3000")
    doc_control_rows = sheet_values(access_token, "Документы", "'Документы'!A2:T3000")
    mass_params = sheet_values(access_token, "Массовая правка", "'Массовая правка'!B6:B17", "FORMATTED_VALUE")
    selection_map = {
        norm_id(row[3]): is_true(row[0] if row else False)
        for row in old_card_rows if len(row) > 3 and norm_id(row[3])
    }
    attr_controls = attr_control_map(attr_control_rows)
    doc_controls = doc_control_map(doc_control_rows)

    snapshot = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cards_rows = build_card_rows(cards, snapshot, selection_map)
    attrs_rows = build_attr_rows(cards, attr_controls)
    docs_rows = build_doc_rows(cards, doc_controls)
    raw_rows = build_raw_rows(cards, snapshot)
    if max(len(cards_rows), len(attrs_rows), len(docs_rows), len(raw_rows)) > 2999:
        raise ApiError("A dataset exceeds the target sheet's 2,999-row body capacity; no data was changed")

    selected_count, good_count, bad_count, preview_rows = build_mass_preview(
        cards_rows, attrs_rows, docs_rows, mass_params
    )
    status_b = f"Свежие данные: {len(cards_rows)} карточек · {len(attrs_rows)} характеристик · {len(docs_rows)} строк документов"
    status_c = f"WB Content API, только чтение · {CABINET_STATUS} · {snapshot}"

    requests: list[dict[str, Any]] = []
    for title_name, width in SHEET_WIDTHS.items():
        requests.append(clear_request(int(sheet_ids[title_name]), width))
    requests.append(clear_request(int(sheet_ids["Массовая правка"]), 9, start_row=23, end_row=3000))

    requests += chunked_update_requests(int(sheet_ids["Карточки WB"]), 1, 17, cards_rows)
    requests += chunked_update_requests(int(sheet_ids["Характеристики"]), 1, 11, attrs_rows)
    requests += chunked_update_requests(int(sheet_ids["Документы"]), 1, 20, docs_rows)
    requests += chunked_update_requests(int(sheet_ids["Полный API"]), 1, 20, raw_rows)

    requests.append(update_cells_request(
        int(sheet_ids["Массовая правка"]), 17, 1,
        [[Formula("=COUNTIF('Карточки WB'!A2:A3000;TRUE)")],
         [selected_count and good_count or 0],
         [selected_count and bad_count or 0],
         ["Предпросмотр обновлён. Отправка в WB отключена." if selected_count else "Отметь карточки на листе «Карточки WB»."]],
        width=1,
    ))
    requests.append(update_cells_request(int(sheet_ids["Массовая правка"]), 23, 0, preview_rows, width=9))
    requests.append(update_cells_request(
        int(sheet_ids["Старт"]), 3, 1,
        [[status_b, status_c]],
        width=2,
    ))
    requests.append(update_cells_request(
        int(sheet_ids["Старт"]), 4, 0,
        [["Ручное обновление"],
         [Formula('=HYPERLINK("https://github.com/v88218429-netizen/zzzz/actions/workflows/wb-content-manager-sync.yml";"Открыть GitHub Actions")'),
          "Автоматическое обновление каждые 6 часов"]],
        width=3,
    ))

    url = f"{SHEETS_API}/spreadsheets/{urllib.parse.quote(SPREADSHEET_ID, safe='')}:batchUpdate"
    google_request(access_token, "POST", url, {"requests": requests})
    print(
        "SYNC_OK"
        f" cabinet=AP"
        f" matched_existing={overlap}/{len(old_ids)}"
        f" cards={len(cards_rows)}"
        f" characteristics={len(attrs_rows)}"
        f" document_rows={len(docs_rows)}"
        f" selected={selected_count}"
        f" preview_ready={good_count}"
        f" preview_needs_review={bad_count}"
        f" snapshot_utc={snapshot}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Error messages are deliberately credential-free; never print headers, secrets, or response bodies.
        print(f"SYNC_FAILED {type(exc).__name__}: {str(exc)[:350]}")
        raise SystemExit(1)
