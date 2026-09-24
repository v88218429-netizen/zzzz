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
            retry = response.headers.get("X-Ratelimit-Retry") or response.headers.get("Retry-After") or "60"
            try:
                delay = max(float(retry), 1.0)
            except ValueError:
                delay = 60.0
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


def _write_csv(path: Path, columns: list[str], rows: list[list[Any]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(rows)
    tmp.replace(path)


def _write_combined_csv(all_rows: list[list[Any]]) -> None:
    _write_csv(DATA_DIR / "finance_all.csv", CSV_COLUMNS, all_rows)


def _is_financial_charge(row: list[Any]) -> bool:
    # K:N = penalty, deduction, storage, acceptance in the normalized 32-col schema.
    return any(abs(_money(row[idx])) > 0 for idx in (10, 11, 12, 13))


def _emit_export_batches(tag: str, rows: list[list[Any]], batch_size: int = 20) -> None:
    total = (len(rows) + batch_size - 1) // batch_size
    print(f"FINANCE_EXPORT_META tag={tag} rows={len(rows)} batches={total} batch_size={batch_size}", flush=True)
    for i in range(total):
        chunk = rows[i * batch_size:(i + 1) * batch_size]
        group = i // 400
        payload = json.dumps(chunk, ensure_ascii=False, separators=(",", ":"))
        print(f"FINANCE_EXPORT_{tag}_G{group} batch={i} data={payload}", flush=True)


async def sync_all() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    date_to = datetime.now().astimezone().date().isoformat()
    status: dict[str, Any] = {
        "startedAt": datetime.now().astimezone().isoformat(),
        "dateFrom": DATE_FROM,
        "dateTo": date_to,
        "shops": {},
    }

    # Only one large detailed response is held in memory at a time.
    detail_sem = asyncio.Semaphore(1)

    async with httpx.AsyncClient(timeout=180.0) as client:
        async def one(shop_id: str, shop_name: str, env_name: str):
            token = os.environ.get(env_name, "").strip()
            if not token:
                return shop_id, [], [], {"ok": False, "error": f"{env_name} is empty"}

            try:
                async with detail_sem:
                    details = await _detailed(client, token, DATE_FROM, date_to)
                    print(f"FINANCE_DETAIL shop={shop_id} rows={len(details)}", flush=True)

                    shop_path = DATA_DIR / f"{shop_id.lower()}_all.csv"
                    shop_tmp = DATA_DIR / f"{shop_id.lower()}_all.csv.tmp"
                    charge_rows: list[list[Any]] = []
                    with shop_tmp.open("w", encoding="utf-8-sig", newline="") as fh:
                        writer = csv.writer(fh)
                        writer.writerow(CSV_COLUMNS)
                        for item in details:
                            row = _normalize(shop_name, item)
                            writer.writerow(row)
                            if _is_financial_charge(row):
                                charge_rows.append(row)
                    shop_tmp.replace(shop_path)
                    del details

                daily = await _reports_list(client, token, DATE_FROM, date_to, "daily")
                print(f"FINANCE_REPORTS shop={shop_id} period=daily rows={len(daily)}", flush=True)
                weekly = await _reports_list(client, token, DATE_FROM, date_to, "weekly")
                print(f"FINANCE_REPORTS shop={shop_id} period=weekly rows={len(weekly)}", flush=True)

                _write_json(DATA_DIR / f"{shop_id.lower()}_reports_daily.json", daily)
                _write_json(DATA_DIR / f"{shop_id.lower()}_reports_weekly.json", weekly)

                report_rows: list[list[Any]] = []
                for period, reports in (("daily", daily), ("weekly", weekly)):
                    for report in reports:
                        report_rows.append([
                            shop_name,
                            report.get("reportId", ""),
                            period,
                            report.get("dateFrom", ""),
                            report.get("dateTo", ""),
                            report.get("createDate", ""),
                            _money(report.get("penaltySum")),
                            _money(report.get("deductionSum")),
                            _money(report.get("paidStorageSum")),
                            _money(report.get("paidAcceptanceSum")),
                            _money(report.get("forPaySum")),
                            "авто",
                            "WB Finance API",
                        ])

                return shop_id, charge_rows, report_rows, {
                    "ok": True,
                    "detailRows": sum(1 for _ in open(shop_path, encoding="utf-8-sig")) - 1,
                    "chargeRows": len(charge_rows),
                    "dailyReports": len(daily),
                    "weeklyReports": len(weekly),
                }
            except Exception as exc:
                print(f"FINANCE_ERROR shop={shop_id} error={type(exc).__name__}:{exc}", flush=True)
                return shop_id, [], [], {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        tasks = [
            one(shop_id, shop_name, env_name)
            for shop_id, (shop_name, env_name) in SHOPS.items()
        ]
        results = await asyncio.gather(*tasks)

    charge_rows: list[list[Any]] = []
    report_rows: list[list[Any]] = []
    total_detail_rows = 0
    for shop_id, shop_charges, shop_reports, shop_status in results:
        charge_rows.extend(shop_charges)
        report_rows.extend(shop_reports)
        status["shops"][shop_id] = shop_status
        total_detail_rows += int(shop_status.get("detailRows", 0) or 0)

    # Keep the complete normalized detail archive on Railway without loading it all in RAM.
    finance_tmp = DATA_DIR / "finance_all.csv.tmp"
    with finance_tmp.open("w", encoding="utf-8-sig", newline="") as out:
        writer = csv.writer(out)
        writer.writerow(CSV_COLUMNS)
        for shop_id in SHOPS:
            shop_path = DATA_DIR / f"{shop_id.lower()}_all.csv"
            if not shop_path.exists():
                continue
            with shop_path.open("r", encoding="utf-8-sig", newline="") as src:
                reader = csv.reader(src)
                next(reader, None)
                for row in reader:
                    writer.writerow(row)
    finance_tmp.replace(DATA_DIR / "finance_all.csv")

    charge_rows.sort(key=lambda r: (str(r[5]), str(r[0]), str(r[6])), reverse=True)
    _write_csv(DATA_DIR / "charges_all.csv", CSV_COLUMNS, charge_rows)

    report_columns = [
        "Магазин", "Report ID", "Период", "Отчет с", "Отчет по", "Дата создания",
        "Штрафы, ₽", "Удержания, ₽", "Хранение, ₽", "Приемка, ₽", "К выплате, ₽",
        "Статус", "Источник",
    ]
    report_rows.sort(key=lambda r: (str(r[5]), str(r[0]), str(r[1])), reverse=True)
    _write_csv(DATA_DIR / "reports_all.csv", report_columns, report_rows)

    status["rows"] = total_detail_rows
    status["chargeRows"] = len(charge_rows)
    status["reportRows"] = len(report_rows)
    status["finishedAt"] = datetime.now().astimezone().isoformat()
    _write_json(DATA_DIR / "status.json", status)
    _emit_export_batches("CHARGES", charge_rows)
    _emit_export_batches("REPORTS", report_rows)
    print("FINANCE_SYNC_DONE " + json.dumps(status, ensure_ascii=False, separators=(",", ":")), flush=True)
    return status


async def sync_loop() -> None:
    while True:
        try:
            await sync_all()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"FINANCE_LOOP_ERROR error={type(exc).__name__}:{exc}", flush=True)
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            _write_json(DATA_DIR / "status.json", {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "finishedAt": datetime.now().astimezone().isoformat(),
            })
        await asyncio.sleep(max(SYNC_INTERVAL_MIN, 60) * 60)
