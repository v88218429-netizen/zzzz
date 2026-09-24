import asyncio
import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://marketplace-api.wildberries.ru"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data")) / "fbs"
SYNC_INTERVAL_MIN = int(os.environ.get("FBS_SUPPLY_SYNC_INTERVAL_MIN", "60"))

COLUMNS = [
    "ID поставки", "createdAt", "closedAt", "scanDt", "done",
    "name", "cargoType", "crossBorderType", "destinationOfficeId",
    "shippingDt", "shippingPointId", "shippingType",
]


def _targets() -> set[str]:
    raw = os.environ.get("FBS_SUPPLY_TARGET_IDS", "")
    return {x.strip() for x in raw.split(",") if x.strip()}


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


async def _get_page(client: httpx.AsyncClient, token: str, cursor: int) -> dict:
    for attempt in range(5):
        r = await client.get(
            BASE_URL + "/api/v3/supplies",
            headers={"Authorization": token},
            params={"limit": 1000, "next": cursor},
        )
        if r.status_code == 429 and attempt < 4:
            retry = r.headers.get("Retry-After") or "2"
            try:
                delay = max(float(retry), 1.0)
            except ValueError:
                delay = 2.0
            await asyncio.sleep(delay)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("WB Marketplace supplies rate limit retries exhausted")


async def sync_once() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("WB_API_TOKEN_AP", "").strip() or os.environ.get("WB_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("WB_API_TOKEN_AP is empty")

    targets = _targets()
    found: dict[str, dict] = {}
    all_count = 0
    cursor = 0
    seen_cursors: set[int] = set()

    async with httpx.AsyncClient(timeout=60.0) as client:
        while True:
            if cursor in seen_cursors:
                break
            seen_cursors.add(cursor)
            payload = await _get_page(client, token, cursor)
            supplies = payload.get("supplies") or []
            if not supplies:
                break
            all_count += len(supplies)

            for s in supplies:
                sid = str(s.get("id") or "")
                if not targets or sid in targets:
                    found[sid] = s

            if targets and len(found) >= len(targets):
                break

            nxt = payload.get("next")
            try:
                nxt = int(nxt or 0)
            except (TypeError, ValueError):
                nxt = 0
            if not nxt or nxt == cursor:
                break
            cursor = nxt

    rows = []
    for sid in sorted(found):
        s = found[sid]
        rows.append([
            sid,
            s.get("createdAt", ""),
            s.get("closedAt", ""),
            s.get("scanDt", ""),
            s.get("done", ""),
            s.get("name", ""),
            s.get("cargoType", ""),
            s.get("crossBorderType", ""),
            s.get("destinationOfficeId", ""),
            s.get("shippingDt", ""),
            s.get("shippingPointId", ""),
            s.get("shippingType", ""),
        ])

    _write_csv(DATA_DIR / "supplies_ap.csv", rows)
    missing = sorted(targets.difference(found))
    status = {
        "updatedAt": datetime.now().astimezone().isoformat(),
        "allSuppliesScanned": all_count,
        "targetCount": len(targets),
        "foundCount": len(found),
        "missingCount": len(missing),
        "missing": missing,
    }
    _write_json(DATA_DIR / "status.json", status)

    print("FBS_SUPPLY_SYNC_DONE " + json.dumps(status, ensure_ascii=False, separators=(",", ":")), flush=True)
    for i in range(0, len(rows), 20):
        chunk = rows[i:i + 20]
        print(
            f"FBS_SUPPLY_EXPORT batch={i // 20} data="
            + json.dumps(chunk, ensure_ascii=False, separators=(",", ":")),
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
            print(f"FBS_SUPPLY_SYNC_ERROR {type(exc).__name__}: {exc}", flush=True)
        await asyncio.sleep(max(SYNC_INTERVAL_MIN, 5) * 60)
