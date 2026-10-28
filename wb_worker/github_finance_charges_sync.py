import asyncio
import argparse
import csv
import datetime as dt
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://finance-api.wildberries.ru"
DEDUCTIONS_URL = "https://seller-analytics-api.wildberries.ru/api/analytics/v1/deductions"
DEDUCTIONS_PAGE_SIZE = 1000
DEDUCTIONS_PAGE_INTERVAL_SEC = 61.0
YANDEX_PUBLIC_RESOURCES_URL = "https://cloud-api.yandex.net/v1/disk/public/resources"
OUT_DIR = Path(os.environ.get(
    "FINANCE_GITHUB_OUT",
    str(Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "wb-finance"),
)).resolve()
LOOKBACK_DAYS = max(2, min(31, int(os.environ.get("FINANCE_LOOKBACK_DAYS", "30"))))
MIN_REQUEST_INTERVAL_SEC = float(os.environ.get("FINANCE_MIN_REQUEST_INTERVAL_SEC", "61"))
BRIDGE_URL = os.environ.get(
    "GOOGLE_SHEETS_BRIDGE_URL",
    "https://script.google.com/macros/s/AKfycbyU_OXpFYqvBx0KDuGCgEHsnzkts_fnJzVe8DM8crRDD1A_fr5DqOfVfW_PYJriaXU_jw/exec",
).strip()
MAX_REQUEST_BYTES = 15_000_000

SHOPS = {
    "AP": ("Саныч", "WB_API_TOKEN_AP"),
    "AA": ("AIR", "WB_API_TOKEN_AA"),
    "YV": ("Хозяюшка", "WB_API_TOKEN_YV"),
}

PENALTY_SHEET_COLUMNS = [
    "Магазин", "Дата штрафа", "Причина / категория", "Артикул продавца", "Артикул WB",
    "Сумма штрафа, ₽", "Стикер МП", "Что было заказано", "Что пришло",
    "Фото 1", "Фото 2", "Фото 3", "Фото 4", "Фото 5", "Order ID", "Report ID", "RRD ID",
]

CSV_COLUMNS = [
    "Магазин", "Report ID", "Отчет с", "Отчет по", "Дата создания",
    "RR Date", "RRD ID", "Тип документа", "Операция", "Категория",
    "Штраф, ₽", "Удержание, ₽", "Хранение, ₽", "Приемка, ₽", "Доплата, ₽",
    "Всего списаний, ₽", "Причина WB", "nmId", "Артикул продавца", "Товар",
    "Склад", "Схема", "Order ID", "Order UID", "SRID", "Sticker ID",
    "Дата заказа", "Дата продажи", "Логистика, ₽", "К выплате, ₽",
    "Статус проверки", "Комментарий",
]

_last_request_at = 0.0
_request_lock = asyncio.Lock()


def _money_optional(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid numeric value in WB finance source") from exc


async def _post(client: httpx.AsyncClient, token: str, body: dict) -> tuple[int, Any]:
    global _last_request_at
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            async with _request_lock:
                loop = asyncio.get_running_loop()
                elapsed = loop.time() - _last_request_at
                if _last_request_at and elapsed < MIN_REQUEST_INTERVAL_SEC:
                    await asyncio.sleep(MIN_REQUEST_INTERVAL_SEC - elapsed)
                try:
                    response = await client.post(
                        BASE_URL + "/api/finance/v1/sales-reports/detailed",
                        headers={"Authorization": token, "Content-Type": "application/json"},
                        json=body,
                    )
                finally:
                    _last_request_at = loop.time()
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_error = exc
            if attempt >= 4:
                break
            delay = min(60, 5 * (2 ** attempt))
            print(f"WB finance transport retry {attempt + 1}/4 after {type(exc).__name__}; wait {delay}s")
            await asyncio.sleep(delay)
            continue

        if response.status_code == 204:
            return 204, []
        if response.status_code == 429 and attempt < 4:
            retry = response.headers.get("X-Ratelimit-Retry") or response.headers.get("Retry-After") or "60"
            try:
                delay = max(float(retry), MIN_REQUEST_INTERVAL_SEC)
            except ValueError:
                delay = MIN_REQUEST_INTERVAL_SEC
            await asyncio.sleep(delay + 1)
            continue
        if response.status_code in {500, 502, 503, 504} and attempt < 4:
            delay = min(60, 5 * (2 ** attempt))
            print(f"WB finance HTTP {response.status_code} retry {attempt + 1}/4; wait {delay}s")
            await asyncio.sleep(delay)
            continue
        response.raise_for_status()
        return response.status_code, response.json()
    if last_error is not None:
        raise RuntimeError(f"WB finance transport retries exhausted: {type(last_error).__name__}") from last_error
    raise RuntimeError("WB API rate limit retries exhausted")


async def _detailed(client: httpx.AsyncClient, token: str, date_from: str, date_to: str) -> list[dict]:
    rows: list[dict] = []
    rrd_id = 0
    while True:
        status, page = await _post(
            client,
            token,
            {
                "dateFrom": date_from,
                "dateTo": date_to,
                "period": "daily",
                "limit": 100000,
                "rrdId": rrd_id,
            },
        )
        if status == 204 or not page:
            break
        rows.extend(page)
        if len(page) < 100000:
            break
        next_rrd = int(page[-1].get("rrdId") or 0)
        if next_rrd <= rrd_id:
            break
        rrd_id = next_rrd
        await asyncio.sleep(65)
    return rows


def _normalize(shop_name: str, row: dict) -> list[Any]:
    penalty = _money_optional(row.get("penalty"))
    deduction = _money_optional(row.get("deduction"))
    storage = _money_optional(row.get("paidStorage"))
    acceptance = _money_optional(row.get("paidAcceptance"))
    extra = _money_optional(row.get("additionalPayment"))
    logistics = _money_optional(row.get("deliveryService"))
    payout = _money_optional(row.get("forPay"))

    if penalty:
        category = "Штраф"
    elif deduction:
        category = "Удержание"
    elif storage:
        category = "Хранение"
    elif acceptance:
        category = "Приемка"
    else:
        category = "Начисление"

    total_debits = (
        round(penalty + deduction + storage + acceptance, 2)
        if all(value is not None for value in (penalty, deduction, storage, acceptance))
        else None
    )
    return [
        shop_name,
        row.get("reportId", ""),
        row.get("dateFrom", ""),
        row.get("dateTo", ""),
        row.get("createDate", ""),
        row.get("rrDate", ""),
        row.get("rrdId", ""),
        row.get("docTypeName", ""),
        row.get("sellerOperName", ""),
        category,
        penalty,
        deduction,
        storage,
        acceptance,
        extra,
        total_debits,
        row.get("bonusTypeName", "") or row.get("sellerOperName", ""),
        row.get("nmId", ""),
        row.get("vendorCode", ""),
        row.get("title", ""),
        row.get("officeName", ""),
        row.get("deliveryMethod", ""),
        row.get("orderId", ""),
        row.get("orderUid", ""),
        row.get("srid", ""),
        row.get("stickerId", ""),
        row.get("orderDt", ""),
        row.get("saleDt", ""),
        logistics,
        payout,
        "Авто GitHub",
        "",
    ]


def _is_charge(row: list[Any]) -> bool:
    return any(row[idx] not in (None, "") and abs(float(row[idx])) > 0 for idx in (10, 11, 12, 13, 14))


def _to_penalty_sheet_row(row: list[Any]) -> list[Any]:
    components = [row[idx] for idx in (10, 11, 12, 13)]
    amount = round(sum(float(value) for value in components), 2) if all(value not in (None, "") for value in components) else None
    reason = row[16] or row[8] or row[9]
    return [
        row[0],   # Магазин
        row[5],   # RR Date
        reason,
        row[18],  # Артикул продавца
        row[17],  # nmId
        amount,
        row[25],  # Sticker ID
        row[18] or row[19],
        "",
        "", "", "", "", "",
        row[22],  # Order ID
        row[1],   # Report ID
        row[6],   # RRD ID
    ]


async def _resolve_deduction_photo_urls(
    client: httpx.AsyncClient, rows: list[dict[str, Any]]
) -> None:
    """Replace public Yandex folder links with direct image preview URLs."""
    links = {
        str(url).strip()
        for item in rows
        if "подмен" in str(item.get("bonusType") or "").casefold()
        for url in (item.get("photoUrls") or [])
        if isinstance(url, str) and url.strip()
    }
    yandex_links = {
        url for url in links
        if "disk.yandex." in url.casefold() or "yadi.sk/" in url.casefold()
    }
    semaphore = asyncio.Semaphore(5)

    async def resolve(url: str) -> tuple[str, str | None, list[str]]:
        async with semaphore:
            try:
                response = await client.get(
                    YANDEX_PUBLIC_RESOURCES_URL,
                    params={"public_key": url, "limit": 100},
                )
                response.raise_for_status()
                resource = response.json() or {}
                image_files: list[dict[str, Any]] = []
                if resource.get("type") == "file":
                    image_files = [resource] if str(resource.get("mime_type") or "").startswith("image/") else []
                elif resource.get("type") == "dir":
                    image_files = [
                        entry for entry in ((resource.get("_embedded") or {}).get("items") or [])
                        if entry.get("type") == "file"
                        and str(entry.get("mime_type") or "").startswith("image/")
                    ]
                previews = [
                    str(entry.get("preview") or "").strip()
                    for entry in image_files
                    if str(entry.get("preview") or "").strip().startswith("https://")
                ][:5]
                return url, str(resource.get("name") or "").strip() or None, previews
            except Exception:
                return url, None, []

    resolved = dict(await asyncio.gather(*(resolve(url) for url in yandex_links)))
    for item in rows:
        bonus_type = str(item.get("bonusType") or "").casefold()
        if "подмен" not in bonus_type:
            continue
        photos = item.get("photoUrls") or []
        if not isinstance(photos, list):
            continue
        output: list[str] = []
        folder_sticker = ""
        for raw_url in photos:
            url = str(raw_url or "").strip()
            if not url:
                continue
            if url in resolved:
                name, previews = resolved[url][1], resolved[url][2]
                if name and name.isdigit():
                    folder_sticker = name
                output.extend(previews)
            elif "disk.yandex." not in url.casefold() and "yadi.sk/" not in url.casefold():
                output.append(url)
        if folder_sticker:
            item["photoFolderSticker"] = folder_sticker
        item["photoUrls"] = list(dict.fromkeys(output))[:5]


async def _fetch_deductions(
    client: httpx.AsyncClient, token: str, date_from: str, date_to: str
) -> list[dict[str, Any]]:
    """Load official substitution records and their photo URLs for a bounded reporting window."""
    rows: list[dict[str, Any]] = []
    offset = 0
    total: int | None = None
    while True:
        if offset:
            # The official deductions endpoint allows one request per minute per seller account.
            await asyncio.sleep(DEDUCTIONS_PAGE_INTERVAL_SEC)
        response = None
        payload = None
        for attempt in range(3):
            response = await client.get(
                DEDUCTIONS_URL,
                headers={"Authorization": token},
                params={
                    "dateFrom": date_from + "T00:00:00Z",
                    "dateTo": date_to + "T23:59:59Z",
                    "sort": "dtBonus",
                    "order": "desc",
                    "limit": DEDUCTIONS_PAGE_SIZE,
                    "offset": offset,
                },
            )
            if response.status_code == 204:
                break
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 2:
                    delay = DEDUCTIONS_PAGE_INTERVAL_SEC if response.status_code == 429 else 5 * (2 ** attempt)
                    print(f"WB deductions HTTP {response.status_code}; retry {attempt + 1}/2 after {int(delay)}s", flush=True)
                    await asyncio.sleep(delay)
                    continue
            response.raise_for_status()
            try:
                payload = response.json()
                break
            except ValueError:
                if attempt >= 2:
                    content_type = response.headers.get("content-type", "unknown")
                    raise ValueError(
                        f"WB deductions returned invalid JSON (HTTP {response.status_code}, "
                        f"content-type {content_type}, bytes {len(response.content)})"
                    )
                delay = 5 * (2 ** attempt)
                print(f"WB deductions invalid JSON; retry {attempt + 1}/2 after {delay}s", flush=True)
                await asyncio.sleep(delay)
        if response is None or response.status_code == 204:
            break
        data = (payload or {}).get("data") or {}
        if not isinstance(data, dict):
            raise ValueError("WB deductions response data is not an object")
        reports = data.get("reports") or []
        try:
            total = int(data.get("total")) if data.get("total") is not None else total
        except (TypeError, ValueError):
            pass
        rows.extend(item for item in reports if isinstance(item, dict))
        offset += len(reports)
        if not reports or len(reports) < DEDUCTIONS_PAGE_SIZE or (total is not None and offset >= total):
            break
    await _resolve_deduction_photo_urls(client, rows)
    return rows


def _substitution_key(row: list[Any]) -> tuple[str, str, str, int] | None:
    reason = str(row[16] or row[8] or "").casefold()
    if "подмен" not in reason:
        return None
    try:
        amount = round(sum(float(row[index] or 0) for index in (10, 11, 12, 13)) * 100)
    except (TypeError, ValueError):
        return None
    day = str(row[5] or "")[:10]
    nm_id = str(row[17] or "").strip()
    if not day or not nm_id or amount <= 0:
        return None
    return str(row[0] or "").strip(), day, nm_id, amount


def _attach_substitution_photos(
    finance_rows: list[list[Any]],
    deductions_by_shop: dict[str, list[dict[str, Any]]],
) -> tuple[list[list[Any]], dict[str, int]]:
    """Attach only unique shop, item, and amount matches; report aggregate key coverage."""
    finance_groups: dict[tuple[str, str, int], list[tuple[int, dt.date]]] = defaultdict(list)
    finance_stickers: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, row in enumerate(finance_rows):
        key = _substitution_key(row)
        if key is None:
            continue
        shop, day, nm_id, amount = key
        try:
            day_value = dt.date.fromisoformat(day)
        except ValueError:
            continue
        finance_groups[(shop, nm_id, amount)].append((index, day_value))
        sticker = str(row[25] or "").strip()
        if sticker:
            finance_stickers[(shop, sticker)].append(index)

    deduction_groups: dict[tuple[str, str, int], dict[tuple[str, ...], tuple[dt.date, list[str]]]] = defaultdict(dict)
    deduction_stickers: dict[tuple[str, str], dict[tuple[str, ...], list[str]]] = defaultdict(dict)
    diagnostic_records: dict[str, list[tuple[str, dt.date, int, str, list[str]]]] = defaultdict(list)
    for shop, records in deductions_by_shop.items():
        for item in records:
            bonus_type = str(item.get("bonusType") or "").casefold()
            if "подмен" not in bonus_type:
                continue
            raw_photos = item.get("photoUrls") or []
            photos = [
                str(url).strip() for url in raw_photos
                if isinstance(url, str) and url.strip().startswith(("https://", "http://"))
            ] if isinstance(raw_photos, list) else []
            if not photos:
                continue
            try:
                amount = round(float(item.get("bonusSumm") or 0) * 100)
                day_value = dt.date.fromisoformat(str(item.get("dtBonus") or "")[:10])
            except (TypeError, ValueError):
                continue
            nm_id = str(item.get("nmId") or "").strip()
            sticker_ids = [
                str(item.get("oldShkId") or "").strip(),
                str(item.get("newShkId") or "").strip(),
                str(item.get("photoFolderSticker") or "").strip(),
            ]
            base_key = (shop, nm_id, amount)
            identity = (day_value.isoformat(), *sticker_ids, *photos)
            deduction_groups[base_key][identity] = (day_value, photos)
            diagnostic_records[shop].append((nm_id, day_value, amount, bonus_type, photos))
            for photo in photos:
                basename = photo.split("?", 1)[0].rsplit("/", 1)[-1]
                for sticker in sticker_ids:
                    if sticker and basename.startswith(sticker + "-"):
                        deduction_stickers[(shop, sticker)][identity] = photos

    output = [_to_penalty_sheet_row(row) for row in finance_rows]
    stats = {
        "apiRecords": sum(map(len, deductions_by_shop.values())),
        "photoSubstitutionRecords": sum(map(len, diagnostic_records.values())),
        "matched": 0,
        "stickerMatched": 0,
        "dateAdjusted": 0,
        "ambiguous": 0,
        "unmatched": 0,
        "diagnosticRows": 0,
        "sameArticle": 0,
        "sameAmount": 0,
        "sameDate": 0,
        "sameArticleAmount": 0,
        "sameArticleDate": 0,
        "sameAmountDate": 0,
    }

    assigned: set[int] = set()
    for sticker_key, indices in finance_stickers.items():
        candidates = deduction_stickers.get(sticker_key, {})
        if len(indices) != 1 or len(candidates) > 1:
            stats["ambiguous"] += len(indices)
            continue
        if not candidates:
            continue
        index = indices[0]
        photos = next(iter(candidates.values()))[:5]
        output[index][9:14] = photos + [""] * (5 - len(photos))
        assigned.add(index)
        stats["matched"] += 1
        stats["stickerMatched"] += 1

    for base_key, finance_matches in finance_groups.items():
        finance_matches = [(index, day) for index, day in finance_matches if index not in assigned]
        if not finance_matches:
            continue
        if len(finance_matches) != 1:
            stats["ambiguous"] += len(finance_matches)
            continue
        index, finance_day = finance_matches[0]
        shop, nm_id, amount = base_key
        candidates = list(deduction_groups.get(base_key, {}).values())
        if len(candidates) > 1:
            stats["ambiguous"] += 1
        elif candidates:
            matched_day, photos = candidates[0]
            output[index][9:14] = photos[:5] + [""] * max(0, 5 - len(photos[:5]))
            stats["matched"] += 1
            if matched_day != finance_day:
                stats["dateAdjusted"] += 1
        else:
            stats["unmatched"] += 1

        comparisons = diagnostic_records.get(shop, [])
        same_article = [r for r in comparisons if r[0] == nm_id]
        same_amount = [r for r in comparisons if r[2] == amount]
        same_date = [r for r in comparisons if abs((r[1] - finance_day).days) <= 1]
        stats["diagnosticRows"] += 1
        stats["sameArticle"] += bool(same_article)
        stats["sameAmount"] += bool(same_amount)
        stats["sameDate"] += bool(same_date)
        stats["sameArticleAmount"] += bool([r for r in same_article if r[2] == amount])
        stats["sameArticleDate"] += bool([r for r in same_article if abs((r[1] - finance_day).days) <= 1])
        stats["sameAmountDate"] += bool([r for r in same_amount if abs((r[1] - finance_day).days) <= 1])
    return output, stats


def _write_csv(path: Path, columns: list[str], rows: list[list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for row in rows:
            localized = []
            for value in row:
                if isinstance(value, float):
                    localized.append(f"{value:.2f}".replace(".", ","))
                else:
                    localized.append(value)
            writer.writerow(localized)
    tmp.replace(path)


def _write_sheet_csv(path: Path, rows: list[list[Any]]) -> None:
    _write_csv(path, CSV_COLUMNS, rows)



def _publish_legacy_penalties(key: str, sync_run_id: str) -> None:
    path = OUT_DIR / "penalties_sheet.csv"
    if not path.exists():
        raise RuntimeError("Penalty export is missing: penalties_sheet.csv")
    rows: list[list[str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        header = next(reader, None)
        if header != PENALTY_SHEET_COLUMNS:
            raise RuntimeError("Penalty export header mismatch")
        for row in reader:
            if len(row) != len(PENALTY_SHEET_COLUMNS):
                raise RuntimeError("Penalty export row width mismatch")
            rows.append(row)
    if not rows:
        raise RuntimeError("Penalty export is empty; refusing to erase the live sheet")
    payload = {
        "token": key,
        "action": "publish_wb_penalties_snapshot",
        "sync_run_id": sync_run_id,
        "rows": rows,
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_REQUEST_BYTES:
        raise RuntimeError("Penalty publication payload exceeds safe limit")
    with httpx.Client(timeout=httpx.Timeout(180.0, connect=15.0), follow_redirects=True) as client:
        result = None
        for attempt in range(2):
            response = client.post(BRIDGE_URL, json=payload)
            response.raise_for_status()
            try:
                result = response.json()
                break
            except json.JSONDecodeError:
                if attempt:
                    raise RuntimeError("Google Sheets bridge returned an empty or invalid response twice")
                import time
                time.sleep(5)
    if not isinstance(result, dict) or result.get("ok") is not True or result.get("action") != "publish_wb_penalties_snapshot":
        detail = str((result or {}).get("error") or (result or {}).get("message") or "unknown bridge response")[:240]
        raise RuntimeError(f"Google Sheets bridge rejected penalty publication: {detail}")
    if result.get("written") != len(rows) or result.get("distinct_keys") != len(rows):
        raise RuntimeError("Penalty publication row-count mismatch")
    print(json.dumps({
        "penalty_snapshot": "OK",
        "spreadsheet_id": result.get("spreadsheet_id"),
        "sheet": result.get("sheet"),
        "rows": result.get("written"),
        "min_date": result.get("min_date"),
        "max_date": result.get("max_date"),
        "sync_run_id": sync_run_id,
    }, ensure_ascii=False, sort_keys=True), flush=True)

def _publish() -> None:
    path = OUT_DIR / "charges_all.csv"
    if not path.exists():
        raise RuntimeError("Finance export is missing: charges_all.csv")
    groups: dict[tuple[str, str], list[list[str]]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != CSV_COLUMNS:
            raise RuntimeError("Finance export header mismatch")
        for index, item in enumerate(reader, start=1):
            row = [item[name] or "" for name in CSV_COLUMNS]
            shop = row[0]
            day = row[5][:10]
            if shop not in {name for name, _token in SHOPS.values()}:
                raise RuntimeError(f"Finance row has unknown shop at {index}")
            try:
                valid_day = dt.date.fromisoformat(day).isoformat() == day
            except ValueError:
                valid_day = False
            if not valid_day:
                raise RuntimeError(f"Finance row has missing or invalid RR Date at {index}")
            if not row[6].isdigit():
                raise RuntimeError(f"Finance row has missing or invalid RRD ID at {index}")
            groups.setdefault((shop, day[:7]), []).append(row)

    key = os.environ.get("GOOGLE_SHEETS_BRIDGE_KEY", "").strip()
    if not key:
        raise RuntimeError("Google Sheets bridge key is not configured")
    if not BRIDGE_URL:
        raise RuntimeError("Google Sheets bridge URL is not configured")

    sync_run_id = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with httpx.Client(timeout=httpx.Timeout(180.0, connect=15.0), follow_redirects=True) as client:
        for (shop, month), rows in sorted(groups.items()):
            natural_keys = [(row[5][:10], row[6]) for row in rows]
            if len(set(natural_keys)) != len(natural_keys):
                raise RuntimeError(f"Duplicate WB finance natural key in {shop}/{month}")
            payload = {
                "token": key,
                "action": "publish_wb_finance",
                "shop": shop,
                "month": month,
                "sync_run_id": sync_run_id,
                "rows": rows,
            }
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > MAX_REQUEST_BYTES:
                raise RuntimeError(f"Finance publication payload exceeds safe limit for {shop}/{month}")
            response = client.post(BRIDGE_URL, json=payload)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or result.get("ok") is not True or result.get("action") != "publish_wb_finance":
                detail = str((result or {}).get("error") or (result or {}).get("message") or "unknown bridge response")[:240]
                raise RuntimeError(f"Google Sheets bridge rejected finance publication for {shop}/{month}: {detail}")
            if result.get("written") != len(rows) or result.get("distinct_keys") != len(rows):
                raise RuntimeError(f"WB finance partition row-count mismatch for {shop}/{month}")
            print(json.dumps({
                "finance_partition": "OK",
                "shop": shop,
                "month": month,
                "rows": len(rows),
                "added": result.get("added"),
                "updated": result.get("updated"),
                "min_date": result.get("min_date"),
                "max_date": result.get("max_date"),
            }, ensure_ascii=False, sort_keys=True), flush=True)

    _publish_legacy_penalties(key, sync_run_id)

async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true", help="publish collected finance facts to monthly Sheets parts")
    args = parser.parse_args()
    if args.publish:
        _publish()
        return 0
    today = dt.datetime.now(dt.timezone.utc).date()
    date_from = (today - dt.timedelta(days=LOOKBACK_DAYS - 1)).isoformat()
    date_to = today.isoformat()

    missing = [env_name for _shop, (_name, env_name) in SHOPS.items() if not os.environ.get(env_name, "").strip()]
    if missing:
        print("MISSING_GITHUB_SECRETS=" + ",".join(missing), flush=True)
        return 2

    all_rows: list[list[Any]] = []
    shop_stats: dict[str, Any] = {}
    deductions_by_shop: dict[str, list[dict[str, Any]]] = {}

    async with httpx.AsyncClient(timeout=180.0) as client:
        for shop_id, (shop_name, env_name) in SHOPS.items():
            token = os.environ[env_name].strip()
            details = await _detailed(client, token, date_from, date_to)
            normalized = [_normalize(shop_name, row) for row in details]
            charges = [row for row in normalized if _is_charge(row)]
            all_rows.extend(charges)
            try:
                deductions_by_shop[shop_name] = await _fetch_deductions(client, token, date_from, date_to)
                shop_stats[shop_id] = {
                    "detailRows": len(details),
                    "chargeRows": len(charges),
                    "deductionRows": len(deductions_by_shop[shop_name]),
                }
            except Exception as exc:
                # Photo enrichment is supplementary; keep the finance snapshot flowing if WB's
                # separate deductions endpoint is unavailable or lacks the required token scope.
                deductions_by_shop[shop_name] = []
                shop_stats[shop_id] = {
                    "detailRows": len(details),
                    "chargeRows": len(charges),
                    "deductionsError": f"{type(exc).__name__}: {str(exc)[:180]}",
                }
            print(f"GITHUB_FINANCE shop={shop_id} details={len(details)} charges={len(charges)} deductions={len(deductions_by_shop[shop_name])}", flush=True)

    all_rows.sort(key=lambda r: (str(r[5]), str(r[0]), str(r[6])), reverse=True)
    _write_sheet_csv(OUT_DIR / "charges_all.csv", all_rows)
    penalty_rows, photo_stats = _attach_substitution_photos(all_rows, deductions_by_shop)
    _write_csv(OUT_DIR / "penalties_sheet.csv", PENALTY_SHEET_COLUMNS, penalty_rows)
    print("GITHUB_PHOTO_ENRICHMENT " + json.dumps(photo_stats, ensure_ascii=False), flush=True)

    latest_rr = max((str(row[5]) for row in all_rows if row[5]), default="")
    print("GITHUB_FINANCE_DONE " + json.dumps({
        "dateFrom": date_from,
        "dateTo": date_to,
        "chargeRows": len(all_rows),
        "latestRRDate": latest_rr,
        "shops": shop_stats,
    }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

