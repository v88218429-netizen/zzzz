import asyncio
import csv
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://finance-api.wildberries.ru"
OUT_DIR = Path(os.environ.get(
    "FINANCE_GITHUB_OUT",
    str(Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "wb-finance"),
)).resolve()
LOOKBACK_DAYS = max(2, min(31, int(os.environ.get("FINANCE_LOOKBACK_DAYS", "30"))))
MIN_REQUEST_INTERVAL_SEC = float(os.environ.get("FINANCE_MIN_REQUEST_INTERVAL_SEC", "61"))

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


def _money(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


async def _post(client: httpx.AsyncClient, token: str, body: dict) -> tuple[int, Any]:
    global _last_request_at
    for attempt in range(5):
        async with _request_lock:
            loop = asyncio.get_running_loop()
            elapsed = loop.time() - _last_request_at
            if _last_request_at and elapsed < MIN_REQUEST_INTERVAL_SEC:
                await asyncio.sleep(MIN_REQUEST_INTERVAL_SEC - elapsed)
            response = await client.post(
                BASE_URL + "/api/finance/v1/sales-reports/detailed",
                headers={"Authorization": token, "Content-Type": "application/json"},
                json=body,
            )
            _last_request_at = loop.time()

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
        response.raise_for_status()
        return response.status_code, response.json()
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
    penalty = _money(row.get("penalty"))
    deduction = _money(row.get("deduction"))
    storage = _money(row.get("paidStorage"))
    acceptance = _money(row.get("paidAcceptance"))
    extra = _money(row.get("additionalPayment"))
    logistics = _money(row.get("deliveryService"))

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

    total_debits = penalty + deduction + storage + acceptance
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
        round(total_debits, 2),
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
        _money(row.get("forPay")),
        "Авто GitHub",
        "",
    ]


def _is_charge(row: list[Any]) -> bool:
    return any(abs(_money(row[idx])) > 0 for idx in (10, 11, 12, 13))


def _to_penalty_sheet_row(row: list[Any]) -> list[Any]:
    amount = round(sum(_money(row[idx]) for idx in (10, 11, 12, 13)), 2)
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


async def main() -> int:
    today = dt.datetime.now(dt.timezone.utc).date()
    date_from = (today - dt.timedelta(days=LOOKBACK_DAYS - 1)).isoformat()
    date_to = today.isoformat()

    missing = [env_name for _shop, (_name, env_name) in SHOPS.items() if not os.environ.get(env_name, "").strip()]
    if missing:
        print("MISSING_GITHUB_SECRETS=" + ",".join(missing), flush=True)
        return 2

    all_rows: list[list[Any]] = []
    shop_stats: dict[str, Any] = {}

    async with httpx.AsyncClient(timeout=180.0) as client:
        for shop_id, (shop_name, env_name) in SHOPS.items():
            token = os.environ[env_name].strip()
            details = await _detailed(client, token, date_from, date_to)
            normalized = [_normalize(shop_name, row) for row in details]
            charges = [row for row in normalized if _is_charge(row)]
            all_rows.extend(charges)
            shop_stats[shop_id] = {"detailRows": len(details), "chargeRows": len(charges)}
            print(f"GITHUB_FINANCE shop={shop_id} details={len(details)} charges={len(charges)}", flush=True)

    all_rows.sort(key=lambda r: (str(r[5]), str(r[0]), str(r[6])), reverse=True)
    _write_sheet_csv(OUT_DIR / "charges_all.csv", all_rows)
    penalty_rows = [_to_penalty_sheet_row(row) for row in all_rows]
    _write_csv(OUT_DIR / "penalties_sheet.csv", PENALTY_SHEET_COLUMNS, penalty_rows)

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
