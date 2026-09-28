import asyncio
import csv
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx


BASE_URL = "https://seller-analytics-api.wildberries.ru"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data")) / "deductions"
SYNC_INTERVAL_MIN = int(os.environ.get("DEDUCTIONS_SYNC_INTERVAL_MIN", "60"))
DATE_FROM_OVERRIDE = os.environ.get("DEDUCTIONS_DATE_FROM", "").strip()
LOOKBACK_DAYS = int(os.environ.get("DEDUCTIONS_LOOKBACK_DAYS", "3650"))
PAGE_SIZE = 1000
PAGE_INTERVAL_SEC = float(os.environ.get("DEDUCTIONS_PAGE_INTERVAL_SEC", "61"))

SHOPS = {
    "AP": ("Саныч", "WB_API_TOKEN_AP"),
    "AA": ("AIR", "WB_API_TOKEN_AA"),
    "YV": ("Хозяюшка", "WB_API_TOKEN_YV"),
}

CSV_COLUMNS = [
    "Магазин",
    "Дата штрафа",
    "Тип",
    "Артикул WB",
    "Старый ШК",
    "Старый цвет",
    "Старый размер",
    "Старый баркод",
    "Старый артикул",
    "Новый ШК",
    "Новый цвет",
    "Новый размер",
    "Новый баркод",
    "Новый артикул",
    "Сумма штрафа",
    "Фото замеров",
]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _write_csv(path: Path, rows: list[list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_COLUMNS)
        writer.writerows(rows)
    tmp.replace(path)


def _date_from_param() -> str:
    value = DATE_FROM_OVERRIDE
    if value:
        if "T" in value:
            return value
        return value + "T00:00:00Z"
    dt = datetime.now(timezone.utc) - timedelta(days=max(1, LOOKBACK_DAYS))
    return dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")


def _date_to_param() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


async def _fetch_shop(
    client: httpx.AsyncClient,
    shop_id: str,
    shop_name: str,
    token: str,
) -> tuple[list[list[Any]], dict[str, Any]]:
    offset = 0
    out: list[list[Any]] = []
    total_reported: int | None = None

    while True:
        response = await client.get(
            BASE_URL + "/api/analytics/v1/deductions",
            headers={"Authorization": token},
            params={
                "dateFrom": _date_from_param(),
                "dateTo": _date_to_param(),
                "sort": "dtBonus",
                "order": "desc",
                "limit": PAGE_SIZE,
                "offset": offset,
            },
        )

        if response.status_code == 204:
            break
        response.raise_for_status()

        payload = response.json() or {}
        data = payload.get("data") or {}
        reports = data.get("reports") or []
        try:
            total_reported = int(data.get("total")) if data.get("total") is not None else total_reported
        except (TypeError, ValueError):
            pass

        for item in reports:
            photos = item.get("photoUrls") or []
            if not isinstance(photos, list):
                photos = []
            out.append([
                shop_name,
                item.get("dtBonus", ""),
                item.get("bonusType", ""),
                item.get("nmId", ""),
                item.get("oldShkId", ""),
                item.get("oldColor", ""),
                item.get("oldSize", ""),
                item.get("oldSku", ""),
                item.get("oldVendorCode", ""),
                item.get("newShkId", ""),
                item.get("newColor", ""),
                item.get("newSize", ""),
                item.get("newSku", ""),
                item.get("newVendorCode", ""),
                item.get("bonusSumm", ""),
                "\n".join(str(url) for url in photos if url),
            ])

        offset += len(reports)
        if not reports or len(reports) < PAGE_SIZE:
            break
        if total_reported is not None and offset >= total_reported:
            break

        await asyncio.sleep(PAGE_INTERVAL_SEC)

    return out, {
        "ok": True,
        "rows": len(out),
        "totalReported": total_reported,
    }


async def sync_all() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    status: dict[str, Any] = {
        "ok": True,
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "dateFrom": _date_from_param(),
        "dateTo": _date_to_param(),
        "shops": {},
    }
    all_rows: list[list[Any]] = []

    async with httpx.AsyncClient(timeout=90.0) as client:
        tasks = []
        task_meta = []
        for shop_id, (shop_name, env_name) in SHOPS.items():
            token = os.environ.get(env_name, "").strip()
            if not token:
                status["ok"] = False
                status["shops"][shop_id] = {
                    "ok": False,
                    "error": f"{env_name} is empty",
                }
                continue
            tasks.append(_fetch_shop(client, shop_id, shop_name, token))
            task_meta.append(shop_id)

        results = await asyncio.gather(*tasks, return_exceptions=True)

    for shop_id, result in zip(task_meta, results):
        if isinstance(result, Exception):
            status["ok"] = False
            status["shops"][shop_id] = {
                "ok": False,
                "error": f"{type(result).__name__}: {result}",
            }
            continue
        rows, shop_status = result
        status["shops"][shop_id] = shop_status
        all_rows.extend(rows)

    all_rows.sort(
        key=lambda row: (str(row[1]), str(row[0]), str(row[4])),
        reverse=True,
    )
    _write_csv(DATA_DIR / "deductions_all.csv", all_rows)

    status["rows"] = len(all_rows)
    status["finishedAt"] = datetime.now(timezone.utc).isoformat()
    _write_json(DATA_DIR / "status.json", status)
    print(
        "DEDUCTIONS_SYNC_DONE " +
        json.dumps(status, ensure_ascii=False, separators=(",", ":")),
        flush=True,
    )
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
                "finishedAt": datetime.now(timezone.utc).isoformat(),
            })
            print(
                f"DEDUCTIONS_LOOP_ERROR error={type(exc).__name__}:{exc}",
                flush=True,
            )
        await asyncio.sleep(max(SYNC_INTERVAL_MIN, 60) * 60)
