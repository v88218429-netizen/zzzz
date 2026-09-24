import asyncio
import csv
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://marketplace-api.wildberries.ru"
ROOT_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DATA_DIR = ROOT_DIR / "fbs"
FINANCE_CHARGES_PATH = ROOT_DIR / "finance" / "charges_all.csv"
FINANCE_STATUS_PATH = ROOT_DIR / "finance" / "status.json"
DATE_FROM = os.environ.get("FBS_EVIDENCE_DATE_FROM", "2026-09-01")
SYNC_INTERVAL_MIN = int(os.environ.get("FBS_EVIDENCE_SYNC_INTERVAL_MIN", "60"))

SHOPS = {
    "AP": ("ИП АП", "WB_API_TOKEN_AP"),
    "AA": ("ИП АА", "WB_API_TOKEN_AA"),
    "YV": ("ИП ЮВ", "WB_API_TOKEN_YV"),
}

COLUMNS = [
    "Магазин", "Order ID", "ID поставки", "Дата заказа WB", "closedAt поставки",
    "scanDt WB", "Заказ→scan, ч", "Цена заказа, ₽", "cargoType",
    "Штраф WB факт, ₽", "Report ID", "RRD ID", "Причина WB",
    "Ставка по правилу", "Штрафных часов (консервативно)",
    "Макс. штраф по правилу, ₽", "Завышение WB, ₽", "Вердикт",
    "Артикул продавца", "nmId",
]


def _num(value: Any) -> float:
    try:
        return float(str(value or "0").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def _dt(value: Any):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _write_csv(path: Path, rows: list[list[Any]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNS)
        w.writerows(rows)
    tmp.replace(path)


def _write_json(path: Path, value: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _penalties() -> dict[str, list[dict[str, str]]]:
    out = {key: [] for key in SHOPS}
    if not FINANCE_CHARGES_PATH.exists():
        return out
    label_to_id = {label: key for key, (label, _) in SHOPS.items()}
    with FINANCE_CHARGES_PATH.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("Причина WB") != "Штраф за нарушение срока передачи товара":
                continue
            shop_id = label_to_id.get(row.get("Магазин", ""))
            order_id = str(row.get("Order ID") or "").strip()
            if shop_id and order_id and order_id != "0":
                out[shop_id].append(row)
    return out


async def _get_json(client: httpx.AsyncClient, token: str, path: str, params: dict) -> dict:
    for attempt in range(5):
        r = await client.get(
            BASE_URL + path,
            headers={"Authorization": token},
            params=params,
        )
        if r.status_code == 429 and attempt < 4:
            await asyncio.sleep(1.5)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("WB Marketplace rate limit retries exhausted")


async def _orders(client: httpx.AsyncClient, token: str, date_to: str) -> list[dict]:
    start_ts = int(datetime.fromisoformat(DATE_FROM).replace(tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc).timestamp()) + 86399
    out = []
    cursor = 0
    seen = set()
    while True:
        if cursor in seen:
            break
        seen.add(cursor)
        payload = await _get_json(
            client, token, "/api/v3/orders",
            {"limit": 1000, "next": cursor, "dateFrom": start_ts, "dateTo": end_ts},
        )
        page = payload.get("orders") or []
        if not page:
            break
        out.extend(page)
        nxt = int(payload.get("next") or 0)
        if not nxt or nxt == cursor:
            break
        cursor = nxt
        await asyncio.sleep(0.25)
    return out


async def _supplies(client: httpx.AsyncClient, token: str) -> list[dict]:
    out = []
    cursor = 0
    seen = set()
    while True:
        if cursor in seen:
            break
        seen.add(cursor)
        payload = await _get_json(
            client, token, "/api/v3/supplies",
            {"limit": 1000, "next": cursor},
        )
        page = payload.get("supplies") or []
        if not page:
            break
        out.extend(page)
        nxt = int(payload.get("next") or 0)
        if not nxt or nxt == cursor:
            break
        cursor = nxt
        await asyncio.sleep(0.25)
    return out


async def _wait_for_finance_ready() -> None:
    for _ in range(240):
        if FINANCE_STATUS_PATH.exists() and FINANCE_CHARGES_PATH.exists():
            try:
                status = json.loads(FINANCE_STATUS_PATH.read_text(encoding="utf-8"))
                shops = status.get("shops") or {}
                phase = status.get("phase")
                if (
                    phase in {"charges_ready", "reports_syncing", "done"}
                    and all((shops.get(key) or {}).get("ok") for key in SHOPS)
                ):
                    return
            except Exception:
                pass
        await asyncio.sleep(5)
    raise RuntimeError("Finance charges were not ready in time")


async def sync_once() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    await _wait_for_finance_ready()
    penalties = _penalties()
    date_to = datetime.now(timezone.utc).date().isoformat()
    all_rows = []
    summary = {}

    async with httpx.AsyncClient(timeout=90.0) as client:
        for shop_id, (label, env_name) in SHOPS.items():
            token = os.environ.get(env_name, "").strip()
            fines = penalties.get(shop_id) or []
            if not token:
                summary[shop_id] = {"ok": False, "fineRows": len(fines), "error": "token missing"}
                continue

            try:
                orders = await _orders(client, token, date_to)
                supplies = await _supplies(client, token)
                omap = {str(x.get("id")): x for x in orders}
                smap = {str(x.get("id")): x for x in supplies}

                rows = []
                counts = {
                    "fineRows": len(fines),
                    "ordersFetched": len(orders),
                    "suppliesFetched": len(supplies),
                    "withScan": 0,
                    "scanInTime": 0,
                    "scanLate": 0,
                    "noScan": 0,
                }
                actual_sum = 0.0
                max_rule_sum = 0.0
                verified_excess = 0.0

                for fine in fines:
                    order_id = str(fine.get("Order ID") or "").strip()
                    order = omap.get(order_id) or {}
                    supply_id = str(order.get("supplyId") or "")
                    supply = smap.get(supply_id) or {}

                    created = _dt(order.get("createdAt"))
                    scan = _dt(supply.get("scanDt"))
                    elapsed = None
                    if created and scan:
                        elapsed = (scan - created).total_seconds() / 3600.0

                    price = _num(order.get("price"))
                    cargo = int(order.get("cargoType") or supply.get("cargoType") or 0)
                    actual = _num(fine.get("Штраф, ₽"))
                    actual_sum += actual

                    rate = 0.0
                    penalty_hours = 0
                    max_rule = 0.0
                    verdict = "НЕТ SCAN DT"

                    if elapsed is None or scan is None:
                        counts["noScan"] += 1
                    else:
                        counts["withScan"] += 1
                        if cargo != 1:
                            verdict = "СГТ/НЕСТАНДАРТ — отдельная формула"
                        elif elapsed <= 48:
                            counts["scanInTime"] += 1
                            verdict = "НЕ СООТВЕТСТВУЕТ: scanDt ≤ 48 ч"
                            verified_excess += actual
                        else:
                            counts["scanLate"] += 1
                            if elapsed <= 54:
                                rate = 0.003
                            elif elapsed <= 60:
                                rate = 0.0035
                            else:
                                rate = 0.0045
                            penalty_hours = max(0, math.ceil(elapsed) - 48)
                            max_rule = round(price * penalty_hours * rate, 2)
                            max_rule_sum += max_rule
                            if actual <= max_rule + 0.05:
                                verdict = "СООТВЕТСТВУЕТ/НЕ ВЫШЕ РАСЧЁТА"
                            else:
                                verdict = "НЕ СООТВЕТСТВУЕТ: WB выше расчёта"
                                verified_excess += max(0.0, actual - max_rule)

                    over = ""
                    if scan is not None and cargo == 1:
                        over = round(actual - max_rule, 2)

                    rows.append([
                        label, order_id, supply_id, order.get("createdAt", ""),
                        supply.get("closedAt", ""), supply.get("scanDt", ""),
                        round(elapsed, 2) if elapsed is not None else "",
                        price, cargo, actual, fine.get("Report ID", ""),
                        fine.get("RRD ID", ""), fine.get("Причина WB", ""),
                        rate, penalty_hours, max_rule, over, verdict,
                        fine.get("Артикул продавца", ""), fine.get("nmId", ""),
                    ])

                rows.sort(key=lambda x: (str(x[17]), -float(x[9] or 0), str(x[1])))
                _write_csv(DATA_DIR / f"penalty_evidence_{shop_id.lower()}.csv", rows)
                all_rows.extend(rows)

                summary[shop_id] = {
                    "ok": True,
                    **counts,
                    "actualFineSum": round(actual_sum, 2),
                    "maxRuleSum": round(max_rule_sum, 2),
                    "verifiedExcess": round(verified_excess, 2),
                }
                print(
                    f"FBS_EVIDENCE_SUMMARY shop={shop_id} "
                    + json.dumps(summary[shop_id], ensure_ascii=False, separators=(",", ":")),
                    flush=True,
                )
                if rows:
                    print(
                        f"FBS_EVIDENCE_SAMPLE shop={shop_id} "
                        + json.dumps(rows[:3], ensure_ascii=False, separators=(",", ":")),
                        flush=True,
                    )
            except Exception as exc:
                summary[shop_id] = {
                    "ok": False, "fineRows": len(fines),
                    "error": f"{type(exc).__name__}: {exc}",
                }
                print(
                    f"FBS_EVIDENCE_ERROR shop={shop_id} "
                    f"error={type(exc).__name__}:{exc}",
                    flush=True,
                )

    _write_csv(DATA_DIR / "penalty_evidence_all.csv", all_rows)
    status = {
        "updatedAt": datetime.now().astimezone().isoformat(),
        "dateFrom": DATE_FROM,
        "dateTo": date_to,
        "rows": len(all_rows),
        "shops": summary,
    }
    _write_json(DATA_DIR / "evidence_status.json", status)
    print(
        "FBS_EVIDENCE_DONE "
        + json.dumps(status, ensure_ascii=False, separators=(",", ":")),
        flush=True,
    )
    return status


async def sync_loop() -> None:
    while True:
        try:
            await sync_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"FBS_EVIDENCE_FATAL {type(exc).__name__}: {exc}", flush=True)
        await asyncio.sleep(max(SYNC_INTERVAL_MIN, 5) * 60)
