#!/usr/bin/env python3
"""Refresh the UDS/Ozon unit-economics workbook from the live Partner and Seller APIs."""

import base64
import datetime as dt
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import defaultdict
from zoneinfo import ZoneInfo


SPREADSHEET_ID = "1SGzwWBZITqTEECvDSWaAa-MdcqQk47Dm2VefpS9bDkc"
UDS_KEY_DOC_ID = "13VkLEBwiNfZT6coVumMl73rou1bLryfSdD8Lu_9NKvY"
UDS_COMPANY_ID = "549756222191"
UDS_BASE = "https://api.uds.app/partner/v2"
OZON_BASE = "https://api-seller.ozon.ru"
MOSCOW = ZoneInfo("Europe/Moscow")
UTC = dt.timezone.utc

LIVE_SHEETS = {
    "uds": "UDS — API LIVE",
    "ozon": "OZON — API LIVE",
    "orders": "OZON — ЗАКАЗЫ 30д",
    "sales": "OZON — ПРОДАЖИ 30д",
}


class HttpFailure(RuntimeError):
    def __init__(self, status, label):
        super().__init__(f"{label}: HTTP {status}")
        self.status = status


def log(name, value):
    print(f"{name}={value}", flush=True)


def gh_mask(value):
    if value:
        print("::add-mask::" + str(value), flush=True)


def http_bytes(url, method="GET", headers=None, body=None, timeout=45, label="HTTP"):
    request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise HttpFailure(exc.code, label) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise RuntimeError(f"{label}: transport {type(exc).__name__}") from None


def http_json(url, method="GET", headers=None, payload=None, timeout=45, label="HTTP"):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    merged = {"Accept": "application/json"}
    if headers:
        merged.update(headers)
    if data is not None:
        merged.setdefault("Content-Type", "application/json")
    raw = http_bytes(url, method=method, headers=merged, body=data, timeout=timeout, label=label)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError(f"{label}: invalid JSON response") from None
    if not isinstance(value, dict):
        raise RuntimeError(f"{label}: response is not an object")
    return value


def apps_script_call(operation, payload=None, label="Apps Script bridge"):
    deployment_id = os.environ.get("WEBAPP_DEPLOYMENT_ID", "").strip()
    webhook_secret = os.environ.get("SELLMONITOR_GITHUB_WEBHOOK_SECRET", "").strip()
    if not deployment_id or not webhook_secret:
        raise RuntimeError("Protected Apps Script bridge configuration is missing")
    url = f"https://script.google.com/macros/s/{deployment_id}/exec"
    body = {"action": "uds_sheet_bridge", "operation": operation, "token": webhook_secret}
    if payload:
        body.update(payload)
    result = http_json(url, method="POST", payload=body, timeout=180, label=label)
    if result.get("ok") is not True or result.get("action") != "uds_sheet_bridge":
        raise RuntimeError(f"{label}: protected bridge returned an invalid response")
    value = result.get("result")
    if not isinstance(value, dict):
        raise RuntimeError(f"{label}: protected bridge response has no result")
    return value


def google_auth_and_key():
    api_key = os.environ.get("UDS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("UDS_API_KEY secret is missing")
    if not os.environ.get("OZON_CLIENT_ID", "").strip() or not os.environ.get("OZON_API_KEY", "").strip():
        raise RuntimeError("Ozon Hozyayushka Seller API credentials are missing")
    gh_mask(api_key)
    gh_mask(os.environ.get("OZON_CLIENT_ID", ""))
    gh_mask(os.environ.get("OZON_API_KEY", ""))
    gh_mask(os.environ.get("SELLMONITOR_GITHUB_WEBHOOK_SECRET", ""))
    return "apps-script-bridge", api_key


def google_sheet_metadata(_access_token):
    return apps_script_call("metadata", label="Google Sheets metadata")


def google_values(_access_token, range_a1):
    result = apps_script_call("read", {"range": range_a1}, label="Google Sheets read")
    return result.get("values") or []


def google_values_batch(_access_token, data):
    if not data:
        return
    apps_script_call("write_values", {"data": data}, label="Google Sheets write")


def google_batch_update(_access_token, requests):
    if not requests:
        return
    apps_script_call("batch_update", {"requests": requests}, label="Google Sheets structure update")


def col_letter(n):
    out = ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def sheet_props(metadata):
    return {s.get("properties", {}).get("title"): s.get("properties", {}) for s in metadata.get("sheets", [])}


def ensure_live_sheets(access_token, metadata, row_targets):
    props = sheet_props(metadata)
    headers = {
        LIVE_SHEETS["uds"]: ["SKU UDS", "Товар UDS", "Вариант", "Текущая цена UDS API, ₽", "Цена до скидки UDS, ₽", "Остаток", "UDS goods ID", "External ID (Ozon product_id)", "Тип UDS", "Категория ID", "Offer ID Ozon", "Скрыт", "Заблокирован", "Статус API", "Последняя цена кабинета/CSV, ₽", "Обновлено, MSK"],
        LIVE_SHEETS["ozon"]: ["Ozon product_id", "offer_id", "SKU UDS", "Наименование Ozon", "Цена продавца API, ₽", "Цена на витрине/акции, ₽", "marketing_seller_price, ₽", "Старая цена, ₽", "min_price, ₽", "net_price, ₽", "Валюта", "НДС", "Вес/объёмный вес", "Статус получения", "Обновлено, MSK"],
        LIVE_SHEETS["orders"]: ["Кабинет", "Схема", "Дата заказа", "Дата отправления/в обработке", "Статус", "Подстатус", "Номер заказа", "Номер отправления", "offer_id", "Ozon SKU", "product_id", "SKU UDS", "Товар", "Количество", "Цена строки, ₽", "Сумма строки, ₽", "Причина отмены", "Обновлено, MSK"],
        LIVE_SHEETS["sales"]: ["SKU UDS", "offer_id", "product_id", "Товар", "Заказов за 30 дней", "Единиц в заказах", "Доставлено, шт", "Отменено, шт", "Возврат/на склад, шт", "Открыто/прочие статусы, шт", "Сумма строк заказов, ₽", "Сумма доставленных строк, ₽", "Текущая цена Ozon API, ₽", "Статусы за 30 дней", "Обновлено, MSK"],
    }
    adds = []
    for title, header in headers.items():
        if title not in props:
            adds.append({"addSheet": {"properties": {"title": title, "gridProperties": {"rowCount": max(1000, row_targets.get(title, 1000)), "columnCount": len(header), "frozenRowCount": 1}}}})
    if adds:
        google_batch_update(access_token, adds)
        metadata = google_sheet_metadata(access_token)
        props = sheet_props(metadata)

    structural = []
    for title, header in headers.items():
        p = props[title]
        needed_rows = max(1000, row_targets.get(title, 1000))
        needed_cols = len(header)
        grid = p.get("gridProperties", {})
        new_grid = {}
        if int(grid.get("rowCount", 0)) < needed_rows:
            new_grid["rowCount"] = needed_rows
        if int(grid.get("columnCount", 0)) < needed_cols:
            new_grid["columnCount"] = needed_cols
        if int(grid.get("frozenRowCount", 0)) < 1:
            new_grid["frozenRowCount"] = 1
        if new_grid:
            structural.append({"updateSheetProperties": {"properties": {"sheetId": p["sheetId"], "gridProperties": new_grid}, "fields": ",".join("gridProperties." + k for k in new_grid)}})
        structural.append({"repeatCell": {"range": {"sheetId": p["sheetId"], "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": needed_cols}, "cell": {"userEnteredFormat": {"textFormat": {"bold": True}, "wrapStrategy": "WRAP", "backgroundColor": {"red": 0.88, "green": 0.92, "blue": 0.96}}}, "fields": "userEnteredFormat(textFormat,wrapStrategy,backgroundColor)"}})
    google_batch_update(access_token, structural)
    return headers, sheet_props(google_sheet_metadata(access_token))


def write_live_table(access_token, sheet_title, rows, header_width):
    existing = google_values(access_token, f"'{sheet_title}'!A:A")
    old_row_count = len(existing)
    if not rows:
        rows = [[]]
    normalized = []
    for row in rows:
        values = ["" if item is None else item for item in row]
        normalized.append(values[:header_width] + [""] * max(0, header_width - len(values)))
    target_rows = max(old_row_count, len(normalized))
    while len(normalized) < target_rows:
        normalized.append([""] * header_width)
    range_a1 = f"'{sheet_title}'!A1:{col_letter(header_width)}{target_rows}"
    google_values_batch(access_token, [{"range": range_a1, "majorDimension": "ROWS", "values": normalized}])


def uds_get(api_key, path, params=None):
    query = "" if not params else "?" + urllib.parse.urlencode(params)
    url = UDS_BASE + path + query
    credentials = base64.b64encode(f"{UDS_COMPANY_ID}:{api_key}".encode("utf-8")).decode("ascii")
    last_status = None
    for attempt in range(5):
        headers = {
            "Authorization": "Basic " + credentials,
            "Accept": "application/json",
            "Accept-Charset": "utf-8",
            "X-Origin-Request-Id": str(uuid.uuid4()),
            "X-Timestamp": dt.datetime.now(UTC).isoformat(),
        }
        try:
            return http_json(url, headers=headers, timeout=45, label="UDS Partner API " + path)
        except HttpFailure as exc:
            last_status = exc.status
            if exc.status == 401:
                raise RuntimeError("UDS authentication returned HTTP 401; stored key/company pair rejected") from None
            if exc.status not in (408, 429, 500, 502, 503, 504) or attempt == 4:
                raise
            time.sleep(min(20, 2 ** attempt))
        except RuntimeError:
            if attempt == 4:
                raise
            time.sleep(min(20, 2 ** attempt))
    raise RuntimeError(f"UDS Partner API retries exhausted (HTTP {last_status})")


def fetch_uds_catalog(api_key):
    settings = uds_get(api_key, "/settings")
    if not isinstance(settings, dict):
        raise RuntimeError("UDS settings response is not an object")
    goods_by_id = {}
    categories = [None]
    visited = set()
    request_count = 1
    while categories:
        node_id = categories.pop(0)
        node_key = "ROOT" if node_id is None else str(node_id)
        if node_key in visited:
            continue
        visited.add(node_key)
        offset = 0
        while True:
            params = {"max": 50, "offset": offset}
            if node_id is not None:
                params["nodeId"] = node_id
            response = uds_get(api_key, "/goods", params)
            request_count += 1
            rows = response.get("rows")
            if not isinstance(rows, list):
                raise RuntimeError("UDS /goods response does not contain rows")
            for item in rows:
                if not isinstance(item, dict):
                    continue
                goods_id = item.get("id")
                if goods_id is not None:
                    goods_by_id[str(goods_id)] = item
                data = item.get("data") or {}
                if data.get("type") == "CATEGORY" and goods_id is not None:
                    categories.append(goods_id)
            offset += len(rows)
            total = response.get("total")
            if len(rows) < 50 or (isinstance(total, int) and offset >= total):
                break
            if offset > 10000:
                raise RuntimeError("UDS /goods pagination exceeded documented offset limit")
    categories_by_id = {str(item.get("id")): str(item.get("name") or "") for item in goods_by_id.values() if (item.get("data") or {}).get("type") == "CATEGORY"}
    flattened = []
    for item in goods_by_id.values():
        data = item.get("data") or {}
        item_type = str(data.get("type") or "UNKNOWN")
        if item_type == "CATEGORY":
            continue
        variants = data.get("variants") if item_type == "VARYING_ITEM" else None
        if not isinstance(variants, list) or not variants:
            variants = [data]
        for variant in variants:
            variant = variant if isinstance(variant, dict) else {}
            offer = variant.get("offer") or data.get("offer") or item.get("offer") or {}
            inventory = variant.get("inventory") or data.get("inventory") or item.get("inventory") or {}
            sku = variant.get("sku") or data.get("sku") or item.get("sku") or ""
            flattened.append({
                "sku": str(sku).strip(),
                "name": str(item.get("name") or data.get("name") or "").strip(),
                "variant": str(variant.get("name") or "").strip() if item_type == "VARYING_ITEM" else "",
                "price": variant.get("price", data.get("price")),
                "offer_price": offer.get("offerPrice"),
                "stock": inventory.get("inStock"),
                "goods_id": item.get("id"),
                "external_id": str(item.get("externalId") or "").strip(),
                "type": item_type,
                "node_id": item.get("nodeId"),
                "category": categories_by_id.get(str(item.get("nodeId") or ""), ""),
                "offer_id": str(item.get("offerId") or variant.get("offerId") or "").strip(),
                "hidden": item.get("hidden", variant.get("hidden")),
                "blocked": item.get("blocked", variant.get("blocked")),
                "raw": item,
            })
    if not flattened:
        raise RuntimeError("UDS API returned no product or variant rows; refusing to overwrite live data")
    return settings, flattened, request_count


def num(value):
    if value is None or isinstance(value, bool) or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().replace(" ", "").replace(",", "."))
    except (ValueError, TypeError):
        return None


def norm_id(value):
    if value is None or value == "":
        return ""
    s = str(value).strip()
    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]
    return s


def moscow_stamp(now):
    return now.astimezone(MOSCOW).strftime("%Y-%m-%d %H:%M:%S MSK")


def row_value(row, index, default=""):
    return row[index] if len(row) > index and row[index] is not None else default


def read_existing_workbook(access_token, metadata):
    props = sheet_props(metadata)
    required = ["UDS — КАТАЛОГ (88)", "UDS — Контроль цен", "OZON — ФАКТ SKU 19.10", "ЮНИТКА", "UDS ЮНИТКА", "UDS — СВОДКА"]
    missing = [title for title in required if title not in props]
    if missing:
        raise RuntimeError("Required workbook tabs missing: " + ", ".join(missing))
    def rows(title, end_col):
        count = int(props[title].get("gridProperties", {}).get("rowCount", 100))
        return google_values(access_token, f"'{title}'!A1:{end_col}{count}")
    return {
        "props": props,
        "catalog": rows("UDS — КАТАЛОГ (88)", "AE"),
        "control": rows("UDS — Контроль цен", "AH"),
        "facts": rows("OZON — ФАКТ SKU 19.10", "N"),
        "main": rows("ЮНИТКА", "AN"),
        "uds_unitka": rows("UDS ЮНИТКА", "Q"),
    }


def collect_ozon_ids(workbook, uds_rows):
    product_ids = set()
    offer_ids = set()
    product_id_by_sku = defaultdict(set)
    offer_by_sku = defaultdict(set)
    for source, sku_col, product_col, offer_col in [
        (workbook["catalog"], 0, 21, 20),
        (workbook["facts"], 0, 5, 1),
        (workbook["control"], 0, 27, 26),
    ]:
        for row in source[1:]:
            sku = norm_id(row_value(row, sku_col))
            product_id = norm_id(row_value(row, product_col))
            offer_id = str(row_value(row, offer_col)).strip()
            if product_id.isdigit():
                product_ids.add(product_id)
                if sku:
                    product_id_by_sku[sku].add(product_id)
            if offer_id:
                offer_ids.add(offer_id)
                if sku:
                    offer_by_sku[sku].add(offer_id)
    for row in uds_rows:
        external = norm_id(row.get("external_id"))
        if external.isdigit():
            product_ids.add(external)
            if row.get("sku"):
                product_id_by_sku[row["sku"]].add(external)
    return sorted(product_ids), sorted(offer_ids), product_id_by_sku, offer_by_sku


def ozon_post(client_id, api_key, endpoint, body, label):
    headers = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json", "Accept": "application/json"}
    last = ""
    for attempt in range(5):
        try:
            result = http_json(OZON_BASE + endpoint, method="POST", headers=headers, payload=body, timeout=45, label=label)
            return result
        except HttpFailure as exc:
            last = f"HTTP {exc.status}"
            if exc.status not in (408, 429, 500, 502, 503, 504) or attempt == 4:
                raise RuntimeError(f"{label}: request failed ({last})") from None
            time.sleep(min(20, 2 ** attempt))
        except RuntimeError as exc:
            last = str(exc).split(":", 1)[0]
            if attempt == 4:
                raise RuntimeError(f"{label}: request failed ({last})") from None
            time.sleep(min(20, 2 ** attempt))
    raise RuntimeError(f"{label}: retries exhausted ({last})")


def price_record(item):
    price = item.get("price") or {}
    return {
        "product_id": norm_id(item.get("product_id")),
        "offer_id": str(item.get("offer_id") or "").strip(),
        "price": num(price.get("price")),
        "marketing_price": num(price.get("marketing_price")),
        "marketing_seller_price": num(price.get("marketing_seller_price")),
        "old_price": num(price.get("old_price")),
        "min_price": num(price.get("min_price")),
        "net_price": num(price.get("net_price")),
        "currency": str(price.get("currency_code") or ""),
        "vat": num(price.get("vat")),
        "volume_weight": num(item.get("volume_weight")),
        "raw": item,
    }


def fetch_ozon_prices(client_id, api_key, product_ids, offer_ids):
    by_product = {}
    by_offer = {}
    request_count = 0

    def query_filter(field, ids):
        nonlocal request_count
        for start in range(0, len(ids), 100):
            chunk = ids[start:start + 100]
            cursor = ""
            seen_cursors = set()
            accumulated = 0
            while True:
                body = {"cursor": cursor, "filter": {field: chunk, "visibility": "ALL"}, "limit": 100}
                response = ozon_post(client_id, api_key, "/v5/product/info/prices", body, "Ozon current prices")
                request_count += 1
                result = response.get("result", response)
                if not isinstance(result, dict) or not isinstance(result.get("items"), list):
                    raise RuntimeError("Ozon /v5/product/info/prices response has no items")
                items = result["items"]
                for raw in items:
                    if not isinstance(raw, dict):
                        continue
                    record = price_record(raw)
                    if record["product_id"]:
                        prior = by_product.get(record["product_id"])
                        if prior and prior["price"] != record["price"]:
                            raise RuntimeError("Ozon returned conflicting current prices for one product_id")
                        by_product[record["product_id"]] = record
                    if record["offer_id"]:
                        prior = by_offer.get(record["offer_id"])
                        if prior and prior["price"] != record["price"]:
                            raise RuntimeError("Ozon returned conflicting current prices for one offer_id")
                        by_offer[record["offer_id"]] = record
                accumulated += len(items)
                total_items = result.get("total_items", response.get("total_items"))
                if total_items is None:
                    total_items = result.get("total", response.get("total"))
                next_cursor = str(result.get("cursor") or response.get("cursor") or "").strip()
                if not next_cursor or next_cursor == cursor or next_cursor in seen_cursors:
                    break
                if isinstance(total_items, int) and accumulated >= total_items:
                    break
                if not items:
                    raise RuntimeError("Ozon prices returned an empty page with a next cursor")
                seen_cursors.add(next_cursor)
                cursor = next_cursor
                if len(seen_cursors) > 100:
                    raise RuntimeError("Ozon price pagination exceeded safety limit")

    if product_ids:
        query_filter("product_id", product_ids)
    if offer_ids:
        query_filter("offer_id", offer_ids)
    return by_product, by_offer, request_count


def iso_parse(value):
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def fetch_ozon_orders(client_id, api_key, now):
    since_dt = now - dt.timedelta(days=30)
    since = since_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    until = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    lines = []
    posting_keys = set()
    request_count = 0
    seen_signatures = {}

    def fetch_flow(flow, endpoint):
        nonlocal request_count
        cursor = ""
        seen_cursors = set()
        while True:
            filt = {"since": since, "to": until}
            if flow == "FBS":
                filt["status"] = ""
            body = {"dir": "DESC", "filter": filt, "limit": 100, "cursor": cursor, "with": {"analytics_data": False, "financial_data": False}}
            response = ozon_post(client_id, api_key, endpoint, body, "Ozon " + flow + " orders")
            request_count += 1
            result = response.get("result", response)
            if not isinstance(result, dict) or not isinstance(result.get("postings"), list):
                raise RuntimeError(f"Ozon {flow} posting response has no postings list")
            postings = result["postings"]
            for posting in postings:
                if not isinstance(posting, dict):
                    continue
                created = str(posting.get("created_at") or "")
                in_process = str(posting.get("in_process_at") or "")
                timestamp = in_process or created
                parsed = iso_parse(timestamp)
                if parsed and (parsed < since_dt or parsed > now + dt.timedelta(hours=1)):
                    continue
                posting_number = str(posting.get("posting_number") or "").strip()
                order_id = str(posting.get("order_id") or "").strip()
                if not posting_number:
                    raise RuntimeError(f"Ozon {flow} posting is missing posting_number")
                signature = json.dumps([created, in_process, posting.get("status"), posting.get("substatus"), posting.get("products")], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                key = (flow, posting_number)
                prior = seen_signatures.get(key)
                if prior is not None:
                    if prior != signature:
                        raise RuntimeError(f"Ozon {flow} posting changed while pages were being read")
                    continue
                seen_signatures[key] = signature
                posting_keys.add((flow, posting_number))
                products = posting.get("products") if isinstance(posting.get("products"), list) else []
                cancellation = posting.get("cancellation") or {}
                cancel_reason = cancellation.get("cancel_reason") or cancellation.get("cancel_reason_name") or posting.get("cancellation_reason") or ""
                if not products:
                    lines.append({
                        "flow": flow, "created_at": created, "in_process_at": in_process,
                        "status": str(posting.get("status") or ""), "substatus": str(posting.get("substatus") or ""),
                        "order_id": order_id, "posting_number": posting_number,
                        "offer_id": "", "ozon_sku": "", "product_id": "", "uds_sku": "", "name": "",
                        "quantity": 0, "unit_price": None, "line_amount": None, "cancel_reason": str(cancel_reason or ""),
                    })
                    continue
                for product in products:
                    if not isinstance(product, dict):
                        continue
                    offer_id = str(product.get("offer_id") or "").strip()
                    ozon_sku = norm_id(product.get("sku"))
                    product_id = norm_id(product.get("product_id"))
                    quantity = num(product.get("quantity")) or 0
                    unit_price = num(product.get("price"))
                    if quantity < 0:
                        raise RuntimeError("Ozon returned a negative order line quantity")
                    lines.append({
                        "flow": flow, "created_at": created, "in_process_at": in_process,
                        "status": str(posting.get("status") or ""), "substatus": str(posting.get("substatus") or ""),
                        "order_id": order_id, "posting_number": posting_number,
                        "offer_id": offer_id, "ozon_sku": ozon_sku, "product_id": product_id, "uds_sku": "",
                        "name": str(product.get("name") or "").strip(), "quantity": quantity,
                        "unit_price": unit_price, "line_amount": (unit_price * quantity) if unit_price is not None else None,
                        "cancel_reason": str(cancel_reason or ""),
                    })
            has_next = bool(result.get("has_next", response.get("has_next", False)))
            if not has_next:
                break
            next_cursor = str(result.get("cursor") or result.get("next_cursor") or response.get("cursor") or response.get("next_cursor") or "").strip()
            if not next_cursor or next_cursor == cursor or next_cursor in seen_cursors:
                raise RuntimeError(f"Ozon {flow} pagination has a missing or repeated cursor")
            if not postings:
                raise RuntimeError(f"Ozon {flow} returned an empty page with has_next")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
            if len(seen_cursors) > 5000:
                raise RuntimeError(f"Ozon {flow} pagination exceeded safety limit")

    fetch_flow("FBO", "/v3/posting/fbo/list")
    fetch_flow("FBS", "/v4/posting/fbs/list")
    return lines, len(posting_keys), request_count, since, until


def cell_range(sheet, cell):
    return f"'{sheet}'!{cell}"


def add_value(data, sheet, cell, value):
    data.append({"range": cell_range(sheet, cell), "values": [["" if value is None else value]]})


def update_column(data, sheet, col, start_row, values):
    if not values:
        return
    end_row = start_row + len(values) - 1
    data.append({"range": f"'{sheet}'!{col}{start_row}:{col}{end_row}", "majorDimension": "ROWS", "values": [["" if value is None else value] for value in values]})


def pick_price(offer_id, product_id, by_offer, by_product):
    if offer_id and offer_id in by_offer:
        return by_offer[offer_id]
    pid = norm_id(product_id)
    if pid and pid in by_product:
        return by_product[pid]
    return None


def text_bool(value):
    if value is True:
        return "Да"
    if value is False:
        return "Нет"
    return "Не указано"


def main():
    now = dt.datetime.now(UTC).replace(microsecond=0)
    stamp = moscow_stamp(now)
    access_token, uds_key = google_auth_and_key()

    uds_settings, uds_rows, uds_requests = fetch_uds_catalog(uds_key)
    del uds_key
    log("UDS_AUTH", "ok")
    log("UDS_API_PRODUCTS", len(uds_rows))

    metadata = google_sheet_metadata(access_token)
    workbook = read_existing_workbook(access_token, metadata)
    product_ids, offer_ids, product_id_by_sku, offer_by_sku = collect_ozon_ids(workbook, uds_rows)

    client_id = os.environ.get("OZON_CLIENT_ID", "").strip()
    ozon_key = os.environ.get("OZON_API_KEY", "").strip()
    if not client_id or not ozon_key:
        raise RuntimeError("Ozon Hozyayushka Seller API credentials are missing")
    gh_mask(client_id)
    gh_mask(ozon_key)

    by_product, by_offer, price_requests = fetch_ozon_prices(client_id, ozon_key, product_ids, offer_ids)
    order_lines, posting_count, order_requests, since, until = fetch_ozon_orders(client_id, ozon_key, now)
    del ozon_key

    sku_by_product = defaultdict(set)
    sku_by_offer = defaultdict(set)
    for sku, ids in product_id_by_sku.items():
        for product_id in ids:
            sku_by_product[product_id].add(sku)
    for sku, offers in offer_by_sku.items():
        for offer_id in offers:
            sku_by_offer[offer_id].add(sku)
    for row in uds_rows:
        if row.get("sku") and row.get("external_id"):
            sku_by_product[norm_id(row["external_id"])].add(row["sku"])
    unique_sku_by_product = {key: next(iter(value)) for key, value in sku_by_product.items() if len(value) == 1}
    unique_sku_by_offer = {key: next(iter(value)) for key, value in sku_by_offer.items() if len(value) == 1}

    # Build one current Ozon price table, including unresolved old offer/product mappings.
    price_rows = []
    price_name_by_offer = {}
    for row in workbook["facts"][1:]:
        offer = str(row_value(row, 1)).strip()
        name = str(row_value(row, 2)).strip()
        if offer and name:
            price_name_by_offer[offer] = name
    used_price_keys = set()
    for record in sorted(by_product.values(), key=lambda item: (item["offer_id"], item["product_id"])):
        sku_set = set()
        if record["product_id"] in sku_by_product:
            sku_set |= sku_by_product[record["product_id"]]
        if record["offer_id"] in sku_by_offer:
            sku_set |= sku_by_offer[record["offer_id"]]
        uds_sku = next(iter(sku_set)) if len(sku_set) == 1 else ("AMBIGUOUS" if sku_set else "")
        price_rows.append([record["product_id"], record["offer_id"], uds_sku, price_name_by_offer.get(record["offer_id"], ""), record["price"], record["marketing_price"], record["marketing_seller_price"], record["old_price"], record["min_price"], record["net_price"], record["currency"], record["vat"], record["volume_weight"], "Цена API получена", stamp])
        used_price_keys.add((record["product_id"], record["offer_id"]))
    returned_offer_ids = set(by_offer)
    returned_product_ids = set(by_product)
    for offer in offer_ids:
        if offer not in returned_offer_ids:
            mapped = unique_sku_by_offer.get(offer, "")
            price_rows.append(["", offer, mapped, price_name_by_offer.get(offer, ""), "", "", "", "", "", "", "", "", "", "Ozon API не вернул offer_id", stamp])
    for product_id in product_ids:
        if product_id not in returned_product_ids:
            mapped = unique_sku_by_product.get(product_id, "")
            if not any(r[0] == product_id for r in price_rows):
                price_rows.append([product_id, "", mapped, "", "", "", "", "", "", "", "", "", "", "Ozon API не вернул product_id", stamp])

    # Current UDS catalog, including prior SKUs that the API did not return.
    catalog_values = workbook["catalog"]
    catalog_header = catalog_values[0] if catalog_values else []
    catalog_rows = catalog_values[1:]
    catalog_by_sku = defaultdict(list)
    last_catalog_row = 1
    for index, row in enumerate(catalog_rows, start=2):
        sku = norm_id(row_value(row, 0))
        if sku:
            catalog_by_sku[sku].append(index)
            last_catalog_row = index
    uds_by_sku = defaultdict(list)
    for row in uds_rows:
        if row["sku"]:
            uds_by_sku[row["sku"]].append(row)
    uds_unique = {sku: items[0] for sku, items in uds_by_sku.items() if len(items) == 1}
    uds_counts = {sku: len(items) for sku, items in uds_by_sku.items()}

    control_rows = workbook["control"][1:]
    control_by_sku = defaultdict(list)
    q_to_skus = defaultdict(list)
    for sheet_row, row in enumerate(control_rows, start=2):
        sku = norm_id(row_value(row, 0))
        q_value = num(row_value(row, 16))
        q = int(q_value) if q_value and q_value.is_integer() else None
        if sku:
            control_by_sku[sku].append({"sheet_row": sheet_row, "row": row, "q": q})
            if q:
                q_to_skus[q].append(sku)

    # Update only exact, existing UDS-unitka mappings. The offset is verified against the live row names.
    price_updates = []
    uds_input_updates = []
    updated_model_by_sku = {}
    mapped_ozon_by_sku = {}
    main_values = workbook["main"]
    uds_unitka_values = workbook["uds_unitka"]
    for sku, items in control_by_sku.items():
        if len(items) != 1:
            continue
        control = items[0]
        q = control["q"]
        if not q or q > len(uds_unitka_values):
            continue
        main_row_number = q - 13
        if main_row_number < 3 or main_row_number > len(main_values):
            continue
        unitka_name = str(row_value(uds_unitka_values[q - 1], 1)).strip()
        main_name = str(row_value(main_values[main_row_number - 1], 1)).strip()
        if not unitka_name or unitka_name != main_name:
            continue
        source_row = control["row"]
        offer_id = str(row_value(source_row, 26)).strip()
        product_id = norm_id(row_value(source_row, 27))
        if not product_id:
            api_item = uds_unique.get(sku)
            if api_item:
                product_id = norm_id(api_item.get("external_id"))
        current_ozon = pick_price(offer_id, product_id, by_offer, by_product)
        mapped_ozon_by_sku[sku] = current_ozon
        uds_item = uds_unique.get(sku)
        if current_ozon and current_ozon["price"] is not None:
            price_updates.append((main_row_number, current_ozon["price"], sku))
            updated_model_by_sku[sku] = q
        if uds_item and num(uds_item.get("price")) is not None:
            uds_input_updates.append((q, num(uds_item.get("price")), sku))

    # Repeated mappings may be updated only when all mapped SKUs agree on the same value.
    price_by_q = defaultdict(list)
    for row_number, price, sku in price_updates:
        q = row_number + 13
        price_by_q[q].append((price, sku, row_number))
    model_writes = []
    model_q_written = set()
    for q, values in price_by_q.items():
        distinct = {round(value[0], 8) for value in values}
        if len(distinct) != 1:
            for _, sku, _ in values:
                updated_model_by_sku.pop(sku, None)
            continue
        row_number = values[0][2]
        model_writes.append((row_number, values[0][0]))
        model_q_written.add(q)
    uds_by_q = defaultdict(list)
    for q, price, sku in uds_input_updates:
        uds_by_q[q].append((price, sku))
    uds_unitka_writes = []
    for q, values in uds_by_q.items():
        if len({round(item[0], 8) for item in values}) == 1:
            uds_unitka_writes.append((q, values[0][0]))

    # Refresh source tabs and model inputs before reading formulas for the updated profitability.
    uds_live_rows = []
    for row in uds_rows:
        sku = row["sku"]
        count = uds_counts.get(sku, 0) if sku else 0
        status = "SKU отсутствует" if not sku else ("Дубликат SKU — сверить вручную" if count > 1 else "В API")
        if row.get("hidden") is True:
            status = "Скрыт в UDS"
        if row.get("blocked") is True:
            status = "Заблокирован в UDS"
        catalog_match = catalog_by_sku.get(sku, [])
        previous = row_value(catalog_rows[catalog_match[0] - 2], 4) if sku and len(catalog_match) == 1 and catalog_match[0] - 2 < len(catalog_rows) else ""
        uds_live_rows.append([sku, row["name"], row["variant"], num(row["price"]), num(row["offer_price"]), ("Безлимит" if row["stock"] is None else num(row["stock"])), norm_id(row["goods_id"]), row["external_id"], row["type"], norm_id(row["node_id"]), row["offer_id"], text_bool(row["hidden"]), text_bool(row["blocked"]), status, num(previous), stamp])
    for sku, row_numbers in catalog_by_sku.items():
        if sku in uds_counts:
            continue
        if len(row_numbers) != 1:
            continue
        old_row = catalog_rows[row_numbers[0] - 2]
        uds_live_rows.append([sku, str(row_value(old_row, 2)), "", "", "", "", norm_id(row_value(old_row, 10)), norm_id(row_value(old_row, 11)), "", "", str(row_value(old_row, 20)), "", "", "SKU не вернулся из UDS API; сверить кабинет", num(row_value(old_row, 4)), stamp])

    order_to_uds = {}
    for offer, skus in sku_by_offer.items():
        if len(skus) == 1:
            order_to_uds[("offer", offer)] = next(iter(skus))
    for pid, skus in sku_by_product.items():
        if len(skus) == 1:
            order_to_uds[("product", pid)] = next(iter(skus))
    for line in order_lines:
        if line["offer_id"] and ("offer", line["offer_id"]) in order_to_uds:
            line["uds_sku"] = order_to_uds[("offer", line["offer_id"])]
        elif line["product_id"] and ("product", line["product_id"]) in order_to_uds:
            line["uds_sku"] = order_to_uds[("product", line["product_id"])]

    # Build per-offer sales aggregates from the order list; this is order status data, not payout accounting.
    aggregates = {}
    for line in order_lines:
        offer = line["offer_id"] or ("product:" + line["product_id"] if line["product_id"] else "unmapped")
        key = (line["flow"], offer)
        agg = aggregates.setdefault(key, {"flow": line["flow"], "offer": line["offer_id"], "product_id": line["product_id"], "uds_sku": line["uds_sku"], "name": line["name"], "orders": set(), "qty": 0.0, "delivered": 0.0, "cancelled": 0.0, "returned": 0.0, "open": 0.0, "amount": 0.0, "delivered_amount": 0.0, "statuses": set()})
        agg["orders"].add((line["flow"], line["posting_number"]))
        agg["qty"] += line["quantity"]
        status = (line["status"] or "").lower()
        agg["statuses"].add(line["status"] or "(пусто)")
        amount = line["line_amount"] or 0
        agg["amount"] += amount
        if "cancel" in status:
            agg["cancelled"] += line["quantity"]
        elif "return" in status:
            agg["returned"] += line["quantity"]
        elif status in {"delivered", "received", "delivered_to_customer"}:
            agg["delivered"] += line["quantity"]
            agg["delivered_amount"] += amount
        else:
            agg["open"] += line["quantity"]
    sales_rows = []
    for agg in sorted(aggregates.values(), key=lambda item: (-item["qty"], item["offer"])):
        current = pick_price(agg["offer"], agg["product_id"], by_offer, by_product)
        sales_rows.append([agg["uds_sku"], agg["offer"], agg["product_id"], agg["name"], len(agg["orders"]), agg["qty"], agg["delivered"], agg["cancelled"], agg["returned"], agg["open"], agg["amount"], agg["delivered_amount"], current["price"] if current else "", ", ".join(sorted(agg["statuses"])), stamp])

    order_rows = []
    for line in sorted(order_lines, key=lambda item: (item["created_at"], item["posting_number"], item["offer_id"])):
        order_rows.append(["Хозяюшка", line["flow"], line["created_at"], line["in_process_at"], line["status"], line["substatus"], line["order_id"], line["posting_number"], line["offer_id"], line["ozon_sku"], line["product_id"], line["uds_sku"], line["name"], line["quantity"], line["unit_price"], line["line_amount"], line["cancel_reason"], stamp])

    # Add new UDS SKUs to the existing catalog as unmapped rows; never infer an Ozon model from title similarity.
    new_skus = sorted(sku for sku in uds_unique if sku and sku not in catalog_by_sku)
    next_catalog_row = last_catalog_row + 1
    new_catalog_rows = []
    for sku in new_skus:
        uds = uds_unique[sku]
        product_id = norm_id(uds.get("external_id"))
        price = pick_price(uds.get("offer_id"), product_id, by_offer, by_product)
        mapped_product = price["product_id"] if price else product_id
        offer_id = price["offer_id"] if price else uds.get("offer_id", "")
        price_value = price["price"] if price else ""
        status = "Новый SKU из UDS API — сопоставить с юниткой" if sku not in control_by_sku else "Новый SKU из UDS API — проверить модель"
        hidden = uds.get("hidden") is True
        blocked = uds.get("blocked") is True
        if blocked:
            status = "Новый SKU; товар заблокирован в UDS"
        elif hidden:
            status = "Новый SKU; товар скрыт в UDS"
        row = [""] * 31
        row[0] = sku
        row[1] = uds.get("category", "")
        row[2] = (uds.get("name", "") + (" — " + uds.get("variant", "") if uds.get("variant") else "")).strip()
        row[3] = num(uds.get("price"))
        row[6] = price_value
        row[9] = status
        row[10] = norm_id(uds.get("goods_id"))
        row[11] = uds.get("external_id", "")
        row[13] = "Добавлен автоматически " + stamp
        row[14] = num(uds.get("offer_price"))
        row[15] = "Безлимит" if uds.get("stock") is None else num(uds.get("stock"))
        row[16] = uds.get("variant", "")
        row[17] = "UDS Partner API · " + stamp
        row[19] = "Ozon API: цена получена" if price_value != "" else "Нет точной привязки Ozon"
        row[20] = offer_id
        row[21] = mapped_product
        row[25] = "Нет строки юнитки — прибыль не рассчитана"
        row[27] = "Новая строка; точная модель не связана"
        row[30] = "Добавить точное соответствие в юнитку"
        new_catalog_rows.append((next_catalog_row, row))
        next_catalog_row += 1

    # Expand catalog grid only if needed.
    catalog_sheet_id = workbook["props"]["UDS — КАТАЛОГ (88)"]["sheetId"]
    current_catalog_grid = workbook["props"]["UDS — КАТАЛОГ (88)"].get("gridProperties", {})
    if next_catalog_row - 1 > int(current_catalog_grid.get("rowCount", 0)):
        google_batch_update(access_token, [{"updateSheetProperties": {"properties": {"sheetId": catalog_sheet_id, "gridProperties": {"rowCount": next_catalog_row + 50}}, "fields": "gridProperties.rowCount"}}])

    # Replace current API/model source values in the existing raw tabs.
    first_writes = []
    for row_number, price in model_writes:
        add_value(first_writes, "ЮНИТКА", f"J{row_number}", price)
    for q, price in uds_unitka_writes:
        add_value(first_writes, "UDS ЮНИТКА", f"N{q}", price)
    for index, row in enumerate(workbook["facts"][1:], start=2):
        sku = norm_id(row_value(row, 0))
        offer = str(row_value(row, 1)).strip()
        product_id = norm_id(row_value(row, 5))
        current = pick_price(offer, product_id, by_offer, by_product)
        if current:
            add_value(first_writes, "OZON — ФАКТ SKU 19.10", f"D{index}", current["price"])
            add_value(first_writes, "OZON — ФАКТ SKU 19.10", f"E{index}", current["old_price"])
            add_value(first_writes, "OZON — ФАКТ SKU 19.10", f"I{index}", "Ozon Seller API · " + stamp)
        else:
            add_value(first_writes, "OZON — ФАКТ SKU 19.10", f"D{index}", "")
            add_value(first_writes, "OZON — ФАКТ SKU 19.10", f"E{index}", "")
            add_value(first_writes, "OZON — ФАКТ SKU 19.10", f"I{index}", "Ozon API не вернул товар · " + stamp)
    add_value(first_writes, "OZON — ФАКТ SKU 19.10", "D1", "Цена Ozon по Seller API (live), ₽")
    add_value(first_writes, "OZON — ФАКТ SKU 19.10", "E1", "Старая цена Ozon API, ₽")
    add_value(first_writes, "OZON — ФАКТ SKU 19.10", "I1", "Источник / последнее обновление")
    for index, row in enumerate(workbook["control"][1:], start=2):
        sku = norm_id(row_value(row, 0))
        uds = uds_unique.get(sku)
        if uds and num(uds.get("price")) is not None:
            add_value(first_writes, "UDS — Контроль цен", f"H{index}", num(uds.get("price")))
            add_value(first_writes, "UDS — Контроль цен", f"W{index}", num(uds.get("price")))
        elif sku and sku in catalog_by_sku:
            add_value(first_writes, "UDS — Контроль цен", f"W{index}", "API не вернул SKU; H оставлена последней известной")

    # Prepare and persist live snapshots in the same run as current price inputs.
    metadata = google_sheet_metadata(access_token)
    row_targets = {
        LIVE_SHEETS["uds"]: len(uds_live_rows) + 1,
        LIVE_SHEETS["ozon"]: len(price_rows) + 1,
        LIVE_SHEETS["orders"]: len(order_rows) + 1,
        LIVE_SHEETS["sales"]: len(sales_rows) + 1,
    }
    headers, live_props = ensure_live_sheets(access_token, metadata, row_targets)
    live_data = [
        (LIVE_SHEETS["uds"], [headers[LIVE_SHEETS["uds"]]] + uds_live_rows),
        (LIVE_SHEETS["ozon"], [headers[LIVE_SHEETS["ozon"]]] + price_rows),
        (LIVE_SHEETS["orders"], [headers[LIVE_SHEETS["orders"]]] + order_rows),
        (LIVE_SHEETS["sales"], [headers[LIVE_SHEETS["sales"]]] + sales_rows),
    ]
    for title, rows in live_data:
        write_live_table(access_token, title, rows, len(headers[title]))
    google_values_batch(access_token, first_writes)

    # Read calculated model values back after source inputs changed.
    time.sleep(2)
    refreshed_metadata = google_sheet_metadata(access_token)
    refreshed_workbook = read_existing_workbook(access_token, refreshed_metadata)
    refreshed_unitka = refreshed_workbook["uds_unitka"]
    refreshed_main = refreshed_workbook["main"]
    model_readback_failures = []
    for row_number, expected in model_writes:
        actual = num(row_value(refreshed_main[row_number - 1], 9))
        if actual is None or abs(actual - expected) > 0.01:
            model_readback_failures.append(row_number)
    if model_readback_failures:
        raise RuntimeError("Ozon price input readback failed for unitka rows: " + ",".join(map(str, model_readback_failures[:20])))

    economics_by_sku = {}
    for sku, entries in control_by_sku.items():
        if len(entries) != 1 or not entries[0]["q"]:
            continue
        q = entries[0]["q"]
        if q not in model_q_written or sku not in updated_model_by_sku:
            continue
        if q > len(refreshed_unitka):
            continue
        row = refreshed_unitka[q - 1]
        current_ozon_price = num(row_value(row, 4))
        profit = num(row_value(row, 5))
        target = num(row_value(row, 12))
        current_uds = num(row_value(row, 13))
        compare_status = str(row_value(row, 15, ""))
        if current_ozon_price is not None or profit is not None or target is not None:
            economics_by_sku[sku] = {"q": q, "ozon": current_ozon_price, "profit": profit, "target": target, "uds": current_uds, "status": compare_status}

    # Reconcile current UDS facts into the original table, appending new SKUs without guessing model links.
    catalog_updates = defaultdict(dict)
    current_uds_for_sku = {}
    current_ozon_for_sku = {}
    profit_updated = 0
    hidden_count = 0
    blocked_count = 0
    duplicate_count = sum(1 for count in uds_counts.values() if count > 1)
    for sku, row_numbers in catalog_by_sku.items():
        if len(row_numbers) != 1:
            continue
        row_number = row_numbers[0]
        existing = catalog_rows[row_number - 2]
        api_item = uds_unique.get(sku)
        if not api_item:
            status = "UDS API не вернул SKU — проверить в кабинете; цены/остатки API очищены"
            catalog_updates["D"][row_number] = ""
            catalog_updates["O"][row_number] = ""
            catalog_updates["P"][row_number] = ""
            catalog_updates["J"][row_number] = status
            catalog_updates["R"][row_number] = "UDS API не вернул SKU · " + stamp
            catalog_updates["H"][row_number] = ""
            current_ozon = pick_price(str(row_value(existing, 20)).strip(), row_value(existing, 21), by_offer, by_product)
            if current_ozon and current_ozon["price"] is not None:
                current_ozon_for_sku[sku] = current_ozon["price"]
                catalog_updates["G"][row_number] = current_ozon["price"]
                old_model = num(row_value(existing, 23))
                catalog_updates["Y"][row_number] = current_ozon["price"] - old_model if old_model is not None else ""
            else:
                catalog_updates["Y"][row_number] = ""
            econ = economics_by_sku.get(sku)
            if econ and econ["profit"] is not None:
                catalog_updates["I"][row_number] = econ["profit"]
                catalog_updates["AC"][row_number] = econ["target"] if econ["target"] is not None else ""
                catalog_updates["AB"][row_number] = "Прибыль пересчитана по Ozon API; цена UDS не подтверждена API"
                catalog_updates["AE"][row_number] = "Целевая цена пересчитана; текущая цена UDS неизвестна"
                profit_updated += 1
            else:
                catalog_updates["I"][row_number] = ""
                catalog_updates["AC"][row_number] = ""
                catalog_updates["AD"][row_number] = ""
                catalog_updates["AB"][row_number] = "Нет текущей цены UDS API; прибыль/цель требуют проверки"
                catalog_updates["AE"][row_number] = "Не проверено: SKU отсутствует в ответе UDS API"
            continue

        if uds_counts.get(sku, 0) > 1:
            status = "Дубликат SKU в ответе UDS API — цену автоматически не сопоставляли"
            duplicate_count += 0
        elif api_item.get("blocked") is True:
            status = "Товар заблокирован в UDS"
            blocked_count += 1
        elif api_item.get("hidden") is True:
            status = "Товар скрыт в UDS"
            hidden_count += 1
        else:
            status = "Актуальный товар из UDS Partner API"
        current_uds = num(api_item.get("price"))
        if current_uds is not None:
            current_uds_for_sku[sku] = current_uds
            catalog_updates["D"][row_number] = current_uds
        else:
            catalog_updates["D"][row_number] = ""
        catalog_updates["C"][row_number] = (api_item.get("name", "") + (" — " + api_item.get("variant", "") if api_item.get("variant") else "")).strip()
        catalog_updates["J"][row_number] = status
        catalog_updates["K"][row_number] = norm_id(api_item.get("goods_id"))
        if api_item.get("external_id"):
            catalog_updates["L"][row_number] = api_item["external_id"]
        catalog_updates["O"][row_number] = num(api_item.get("offer_price"))
        catalog_updates["P"][row_number] = "Безлимит" if api_item.get("stock") is None else num(api_item.get("stock"))
        catalog_updates["R"][row_number] = "UDS Partner API · " + stamp
        product_id = norm_id(api_item.get("external_id")) or norm_id(row_value(existing, 21))
        offer_id = str(api_item.get("offer_id") or row_value(existing, 20) or "").strip()
        current_ozon = pick_price(offer_id, product_id, by_offer, by_product)
        econ = economics_by_sku.get(sku)
        if current_ozon and current_ozon["price"] is not None:
            current_ozon_for_sku[sku] = current_ozon["price"]
            catalog_updates["G"][row_number] = current_ozon["price"]
            catalog_updates["U"][row_number] = current_ozon["offer_id"] or offer_id
            catalog_updates["V"][row_number] = current_ozon["product_id"] or product_id
            catalog_updates["H"][row_number] = current_uds - current_ozon["price"] if current_uds is not None else ""
            old_model = num(row_value(existing, 23))
            catalog_updates["Y"][row_number] = current_ozon["price"] - old_model if old_model is not None else ""
        else:
            catalog_updates["G"][row_number] = ""
            catalog_updates["H"][row_number] = ""
            catalog_updates["Y"][row_number] = ""
        if econ and econ["profit"] is not None and current_ozon:
            catalog_updates["I"][row_number] = econ["profit"]
            catalog_updates["Z"][row_number] = "Модель пересчитана по текущей цене Ozon Seller API"
            catalog_updates["AB"][row_number] = "Прибыль по текущей цене Ozon; модель связана"
            catalog_updates["AC"][row_number] = econ["target"] if econ["target"] is not None else ""
            catalog_updates["AD"][row_number] = (econ["target"] - current_uds) if econ["target"] is not None and current_uds is not None else ""
            catalog_updates["AE"][row_number] = econ["status"] or "Цель обновлена; сравнить текущую цену UDS"
            profit_updated += 1
        else:
            catalog_updates["I"][row_number] = ""
            catalog_updates["Z"][row_number] = "Текущая цена Ozon есть; точная строка юнитки не связана"
            catalog_updates["AB"][row_number] = "Ozon API обновлён; прибыль не пересчитана — нет точной строки модели"
            catalog_updates["AC"][row_number] = ""
            catalog_updates["AD"][row_number] = ""
            catalog_updates["AE"][row_number] = "Нет точной модели в листе UDS ЮНИТКА"

    # Add the new catalog rows to the same column updates and use exact current economics only when mapped.
    for row_number, row in new_catalog_rows:
        sku = norm_id(row[0])
        uds = uds_unique[sku]
        current = pick_price(uds.get("offer_id"), uds.get("external_id"), by_offer, by_product)
        if current and current["price"] is not None:
            row[6] = current["price"]
            row[20] = current["offer_id"]
            row[21] = current["product_id"]
        econ = economics_by_sku.get(sku)
        if econ and econ["profit"] is not None and current:
            row[8] = econ["profit"]
            row[25] = "Модель пересчитана по текущей цене Ozon Seller API"
            row[27] = "Прибыль по текущей цене Ozon; модель связана"
            row[28] = econ["target"] if econ["target"] is not None else ""
            row[29] = (econ["target"] - num(uds.get("price"))) if econ["target"] is not None and num(uds.get("price")) is not None else ""
            row[30] = econ["status"] or "Цель обновлена"
            row[9] = "Новый SKU из API; модель найдена по точной привязке"
            profit_updated += 1
        elif uds.get("hidden") is not True and uds.get("blocked") is not True:
            row[25] = "Нет точной строки юнитки — прибыль не рассчитана"
            row[27] = "Новый SKU; сопоставить с юниткой"
            row[30] = "Добавить точное соответствие в юнитку"

    # Write live catalog values by columns; these columns are values, not formulas.
    final_writes = []
    existing_end = max(last_catalog_row, max((row for row, _ in new_catalog_rows), default=last_catalog_row))
    target_cols = sorted(catalog_updates.keys(), key=lambda c: ord(c[0]) - 64)
    for col in target_cols:
        values = []
        col_num = ord(col) - 64
        for row_number in range(2, existing_end + 1):
            if row_number in catalog_updates[col]:
                value = catalog_updates[col][row_number]
            elif row_number > last_catalog_row:
                value = ""
            else:
                value = row_value(catalog_rows[row_number - 2], col_num - 1, "")
            values.append(value)
        update_column(final_writes, "UDS — КАТАЛОГ (88)", col, 2, values)
    for row_number, row in new_catalog_rows:
        final_writes.append({"range": f"'UDS — КАТАЛОГ (88)'!A{row_number}:AE{row_number}", "values": [["" if v is None else v for v in row]]})

    # Update the live summary at the top; lower rows remain the October audit trail.
    returned_skus = {sku for sku, count in uds_counts.items() if sku}
    old_skus = set(catalog_by_sku)
    missing_skus = sorted(old_skus - returned_skus)
    new_sku_count = len(new_skus)
    current_price_count = sum(1 for record in by_product.values() if record["price"] is not None)
    delivered_units = sum(agg["delivered"] for agg in aggregates.values())
    cancelled_units = sum(agg["cancelled"] for agg in aggregates.values())
    returned_units = sum(agg["returned"] for agg in aggregates.values())
    amount_sum = sum(agg["amount"] for agg in aggregates.values())
    summary = [
        {"range": "'UDS — СВОДКА'!A1", "values": [["UDS × OZON — АВТООБНОВЛЕНИЕ"]]},
        {"range": "'UDS — СВОДКА'!A2", "values": [[f"Последний цикл: {stamp}. Источники: UDS Partner API, Ozon Seller API. Новые SKU добавляются в каталог автоматически; без точного соответствия строке юнитки прибыль не придумывается."]]},
        {"range": "'UDS — СВОДКА'!A3", "values": [[f"Ozon цены обновлены: {current_price_count}; строк юнитки с актуальной Ozon ценой обновлено: {len(model_writes)}; цен UDS по API записано: {len(uds_unitka_writes)}. Детализация новых товаров и статусов — лист {LIVE_SHEETS['uds']}."]]},
        {"range": "'UDS — СВОДКА'!A4:D4", "values": [["SKU UDS в текущем каталоге", "Вернул Partner API", "Не вернул API", "Новых SKU добавлено"]]},
        {"range": "'UDS — СВОДКА'!A5:D5", "values": [[len(old_skus | returned_skus), len(returned_skus), len(missing_skus), new_sku_count]]},
        {"range": "'UDS — СВОДКА'!A6", "values": [[f"Заказы Ozon, последние 30 дней: {posting_count} отправлений, {len(order_lines)} строк товара; доставлено {delivered_units:g} шт., отменено {cancelled_units:g} шт., возвраты/на склад {returned_units:g} шт. Сумма строк заказов: {amount_sum:.2f} ₽ (не выплаты Ozon)."]]},
        {"range": "'UDS — СВОДКА'!A7", "values": [["Историческая сверка 23–25.10 ниже; текущие API-статусы и новые SKU — на листах UDS — API LIVE и OZON — API LIVE."]]},
    ]
    final_writes.extend(summary)

    # Capture refreshed sales and order rows together with fresh source status.
    write_live_table(access_token, LIVE_SHEETS["uds"], [headers[LIVE_SHEETS["uds"]]] + uds_live_rows, 16)
    write_live_table(access_token, LIVE_SHEETS["ozon"], [headers[LIVE_SHEETS["ozon"]]] + price_rows, 15)
    write_live_table(access_token, LIVE_SHEETS["orders"], [headers[LIVE_SHEETS["orders"]]] + order_rows, 18)
    write_live_table(access_token, LIVE_SHEETS["sales"], [headers[LIVE_SHEETS["sales"]]] + sales_rows, 15)
    google_values_batch(access_token, final_writes)

    # Read back the written source and dashboard values before reporting success.
    readback = google_values(access_token, "'UDS — СВОДКА'!A1:D6")
    catalog_readback = google_values(access_token, "'UDS — КАТАЛОГ (88)'!A1:P5")
    live_uds_readback = google_values(access_token, f"'{LIVE_SHEETS['uds']}'!A1:P3")
    if not readback or not catalog_readback or not live_uds_readback:
        raise RuntimeError("Google Sheets readback was empty after update")
    if str(row_value(readback[0], 0)) != "UDS × OZON — АВТООБНОВЛЕНИЕ":
        raise RuntimeError("Summary readback title did not match the written value")
    if not live_uds_readback[0] or len(live_uds_readback[0]) < 14:
        raise RuntimeError("UDS live table readback did not contain its header")

    # GitHub job summary contains only non-secret totals.
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as out:
            out.write("## UDS / Ozon update\n\n")
            out.write(f"- Успешно: {stamp}\n")
            out.write(f"- UDS API: {len(returned_skus)} SKU, новых: {new_sku_count}, не вернул API: {len(missing_skus)}\n")
            out.write(f"- Ozon API: {current_price_count} цен; обновлено строк модели: {len(model_writes)}\n")
            out.write(f"- Заказы за 30 дней: {posting_count} отправлений, {len(order_lines)} строк товара, доставлено {delivered_units:g} шт., отменено {cancelled_units:g} шт.\n")
            out.write(f"- Таблица: https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit\n")

    log("SYNC_RESULT", "success")
    log("SYNC_UPDATED_MSK", stamp)
    log("UDS_SKU_RETURNED", len(returned_skus))
    log("UDS_SKU_NOT_RETURNED", len(missing_skus))
    log("UDS_SKU_NEW", new_sku_count)
    log("OZON_PRICE_RECORDS", current_price_count)
    log("UNITKA_MODEL_ROWS_UPDATED", len(model_writes))
    log("UDS_UNITKA_PRICE_INPUTS_UPDATED", len(uds_unitka_writes))
    log("UNITKA_PROFIT_ROWS_REFRESHED", profit_updated)
    log("UDS_HIDDEN", hidden_count)
    log("UDS_BLOCKED", blocked_count)
    log("UDS_DUPLICATE_SKUS", duplicate_count)
    log("OZON_POSTINGS_30D", posting_count)
    log("OZON_ORDER_LINES_30D", len(order_lines))
    log("OZON_DELIVERED_UNITS_30D", delivered_units)
    log("OZON_CANCELLED_UNITS_30D", cancelled_units)
    log("UDS_REQUESTS", uds_requests)
    log("OZON_PRICE_REQUESTS", price_requests)
    log("OZON_ORDER_REQUESTS", order_requests)


if __name__ == "__main__":
    main()
