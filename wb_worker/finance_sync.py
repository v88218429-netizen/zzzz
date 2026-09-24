import asyncio
import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://finance-api.wildberries.ru"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data")) / "finance"
SYNC_INTERVAL_MIN = int(os.environ.get("FINANCE_SYNC_INTERVAL_MIN", "360"))
DATE_FROM = os.environ.get("FINANCE_DATE_FROM", "2026-09-01")

SHOPS = {
    "AP": ("ИП АП", "WB_API_TOKEN_AP"),
    "AA": ("ИП АА", "WB_API_TOKEN_AA"),
    "YV": ("ИП ЮВ", "WB_API_TOKEN_YV"),
}

CSV_COLUMNS = [
    "Магазин", "Report ID", "Отчет с", "Отчет по", "Дата создания",
    "RR Date", "RRD ID", "Тип документа", "Операция", "Категория",
    "Штраф, ₽", "Удержание, ₽", "Хранение, ₽", "Приемка, ₽", "Доплата, ₽",
    "Всего списаний, ₽", "Причина WB", "nmId", "Артикул продавца", "Товар",
    "Склад", "Схема", "Order ID", "Order UID", "SRID", "Sticker ID",
    "Дата заказа", "Дата продажи", "Логистика, ₽", "К выплате, ₽",
    "Статус проверки", "Комментарий",
]


def _money(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


async def _post(client: httpx.AsyncClient, token: str, path: str, body: dict) -> tuple[int, Any]:
    for attempt in range(4):
        response = await client.post(
            BASE_URL + path,
            headers={"Authorization": token, "Content-Type": "application/json"},
            json=body,
        )
        if response.status_code == 204:
            return 204, []
        if response.status_code == 429 and attempt < 3:
            await asyncio.sleep(65)
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
            "/api/finance/v1/sales-reports/detailed",
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


async def _reports_list(client: httpx.AsyncClient, token: str, date_from: str, date_to: str, period: str) -> list[dict]:
    out: list[dict] = []
    offset = 0
    while True:
        status, page = await _post(
            client,
            token,
            "/api/finance/v1/sales-reports/list",
            {
                "dateFrom": date_from,
                "dateTo": date_to,
                "period": period,
                "limit": 1000,
                "offset": offset,
            },
        )
        if status == 204 or not page:
            break
        out.extend(page)
        if len(page) < 1000:
            break
        offset += len(page)
        await asyncio.sleep(65)
    return out


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
        "Авто",
        "",
    ]


def _write_json(path: Path, value: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _write_combined_csv(all_rows: list[list[Any]]) -> None:
    path = DATA_DIR / "finance_all.csv"
    tmp = DATA_DIR / "finance_all.csv.tmp"
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        writer.writerows(all_rows)
    tmp.replace(path)


async def sync_all() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    date_to = datetime.now().astimezone().date().isoformat()
    status: dict[str, Any] = {
        "startedAt": datetime.now().astimezone().isoformat(),
        "dateFrom": DATE_FROM,
        "dateTo": date_to,
        "shops": {},
    }
    combined: list[list[Any]] = []

    async with httpx.AsyncClient(timeout=180.0) as client:
        async def one(shop_id: str, shop_name: str, env_name: str):
            token = os.environ.get(env_name, "").strip()
            if not token:
                return shop_id, [], {"ok": False, "error": f"{env_name} is empty"}

            try:
                details = await _detailed(client, token, DATE_FROM, date_to)
                # WB Finance limit is per seller account; keep calls for the same seller apart.
                await asyncio.sleep(65)
                daily = await _reports_list(client, token, DATE_FROM, date_to, "daily")
                await asyncio.sleep(65)
                weekly = await _reports_list(client, token, DATE_FROM, date_to, "weekly")

                _write_json(DATA_DIR / f"{shop_id.lower()}_details.json", details)
                _write_json(DATA_DIR / f"{shop_id.lower()}_reports_daily.json", daily)
                _write_json(DATA_DIR / f"{shop_id.lower()}_reports_weekly.json", weekly)
                normalized = [_normalize(shop_name, row) for row in details]
                return shop_id, normalized, {
                    "ok": True,
                    "detailRows": len(details),
                    "dailyReports": len(daily),
                    "weeklyReports": len(weekly),
                }
            except Exception as exc:
                return shop_id, [], {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        tasks = [
            one(shop_id, shop_name, env_name)
            for shop_id, (shop_name, env_name) in SHOPS.items()
        ]
        results = await asyncio.gather(*tasks)

    for shop_id, rows, shop_status in results:
        combined.extend(rows)
        status["shops"][shop_id] = shop_status

    # Stable de-duplication by shop + rrdId, then newest rrDate/reportId.
    dedup: dict[tuple[str, str], list[Any]] = {}
    for row in combined:
        key = (str(row[0]), str(row[6]))
        dedup[key] = row
    combined = sorted(
        dedup.values(),
        key=lambda r: (str(r[5]), str(r[0]), str(r[6])),
        reverse=True,
    )

    _write_combined_csv(combined)
    status["rows"] = len(combined)
    status["finishedAt"] = datetime.now().astimezone().isoformat()
    _write_json(DATA_DIR / "status.json", status)
    return status


async def sync_loop() -> None:
    while True:
        try:
            await sync_all()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            _write_json(DATA_DIR / "status.json", {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "finishedAt": datetime.now().astimezone().isoformat(),
            })
        await asyncio.sleep(max(SYNC_INTERVAL_MIN, 60) * 60)
