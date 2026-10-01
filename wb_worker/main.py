import asyncio
import csv
import datetime as dt
import io
import os
import secrets
from typing import Any

import httpx
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from wb_mcp import settings as cfg
from wb_mcp_hotfixes import apply_wb_mcp_hotfixes

apply_wb_mcp_hotfixes()

from wb_mcp.app import fastapi_app as wb_app

from finance_sync import DATA_DIR as FINANCE_DIR, sync_loop
from deductions_sync import DATA_DIR as DEDUCTIONS_DIR, sync_loop as deductions_sync_loop
from fbs_supply_sync import DATA_DIR as FBS_DIR, sync_loop as fbs_supply_sync_loop
from penalty_evidence_sync import sync_loop as penalty_evidence_sync_loop
from traffic_sync import DATA_DIR as TRAFFIC_DIR, sync_loop as traffic_sync_loop

ROOT_DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
EXPORT_TOKEN = os.environ.get("FINANCE_EXPORT_TOKEN", "").strip()
DEDUCTIONS_SHEET_TOKEN = os.environ.get("DEDUCTIONS_SHEET_TOKEN", "").strip()
PRICE_EXPORT_KEY = os.environ.get("PRICE_EXPORT_KEY", "").strip()
PRICE_EXPORT_LIMIT = int(os.environ.get("PRICE_EXPORT_LIMIT", "1000"))


def bootstrap_shops() -> None:
    shops = cfg.load_shops(ROOT_DATA_DIR)
    mapping = {
        "ap": ("ИП АП", "WB_API_TOKEN_AP"),
        "aa": ("ИП АА", "WB_API_TOKEN_AA"),
        "yv": ("ИП ЮВ", "WB_API_TOKEN_YV"),
    }
    changed = False
    for shop_id, (label, env_name) in mapping.items():
        token = os.environ.get(env_name, "").strip()
        if token and (
            shop_id not in shops
            or shops[shop_id].get("wb_api_token") != token
            or shops[shop_id].get("name") != label
        ):
            shops[shop_id] = {"name": label, "wb_api_token": token}
            changed = True
    if changed:
        cfg.save_shops(ROOT_DATA_DIR, shops)


def authorize(request: Request) -> None:
    if not EXPORT_TOKEN:
        raise HTTPException(status_code=503, detail="FINANCE_EXPORT_TOKEN is not configured")
    supplied = request.query_params.get("token", "")
    if not secrets.compare_digest(supplied, EXPORT_TOKEN):
        raise HTTPException(status_code=401, detail="Unauthorized")


@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap_shops()
    async with wb_app.router.lifespan_context(wb_app):
        task = asyncio.create_task(sync_loop())
        deductions_task = asyncio.create_task(deductions_sync_loop())
        fbs_task = asyncio.create_task(fbs_supply_sync_loop())
        evidence_task = asyncio.create_task(penalty_evidence_sync_loop())
        traffic_task = asyncio.create_task(traffic_sync_loop())
        try:
            yield
        finally:
            for bg_task in (task, deductions_task, fbs_task, evidence_task, traffic_task):
                bg_task.cancel()
            for bg_task in (task, deductions_task, fbs_task, evidence_task, traffic_task):
                try:
                    await bg_task
                except asyncio.CancelledError:
                    pass


app = FastAPI(lifespan=lifespan)


@app.get("/api/finance/status")
async def finance_status(request: Request):
    authorize(request)
    path = FINANCE_DIR / "status.json"
    if not path.exists():
        return JSONResponse({"state": "syncing"})
    try:
        import json
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:
        return JSONResponse({"state": "error", "error": str(exc)}, status_code=500)


@app.get("/api/finance/export.csv")
async def finance_export(request: Request):
    authorize(request)
    path = FINANCE_DIR / "finance_all.csv"
    if not path.exists():
        raise HTTPException(status_code=503, detail="Initial finance sync is still running")
    return FileResponse(
        path,
        media_type="text/csv; charset=utf-8",
        filename="wb_finance_all.csv",
    )


@app.get("/api/finance/charges.csv")
async def finance_charges_export(request: Request):
    authorize(request)
    path = FINANCE_DIR / "charges_all.csv"
    if not path.exists():
        raise HTTPException(status_code=503, detail="Initial finance sync is still running")
    return FileResponse(
        path,
        media_type="text/csv; charset=utf-8",
        filename="wb_finance_charges.csv",
    )


@app.get("/api/finance/reports.csv")
async def finance_reports_export(request: Request):
    authorize(request)
    path = FINANCE_DIR / "reports_all.csv"
    if not path.exists():
        raise HTTPException(status_code=503, detail="Initial finance sync is still running")
    return FileResponse(
        path,
        media_type="text/csv; charset=utf-8",
        filename="wb_finance_reports.csv",
    )


@app.get("/api/deductions/status")
async def deductions_status(request: Request):
    authorize(request)
    path = DEDUCTIONS_DIR / "status.json"
    if not path.exists():
        return JSONResponse({"state": "syncing"}, status_code=503)
    try:
        import json
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:
        return JSONResponse({"state": "error", "error": str(exc)}, status_code=500)


@app.get("/api/deductions/export.csv")
async def deductions_export(request: Request):
    authorize(request)
    path = DEDUCTIONS_DIR / "deductions_all.csv"
    if not path.exists():
        raise HTTPException(status_code=503, detail="Initial deductions sync is still running")
    return FileResponse(
        path,
        media_type="text/csv; charset=utf-8",
        filename="wb_deductions_all.csv",
    )


@app.get("/api/deductions/sheet.csv")
async def deductions_sheet_export(request: Request, shop: str):
    if not DEDUCTIONS_SHEET_TOKEN:
        raise HTTPException(status_code=503, detail="DEDUCTIONS_SHEET_TOKEN is not configured")
    supplied = request.query_params.get("key", "")
    if not secrets.compare_digest(supplied, DEDUCTIONS_SHEET_TOKEN):
        raise HTTPException(status_code=401, detail="Unauthorized")

    shop_id = str(shop or "").strip().upper()
    shop_names = {"AP": "Саныч", "AA": "AIR", "YV": "Хозяюшка"}
    if shop_id not in shop_names:
        raise HTTPException(status_code=404, detail="Unknown shop")

    path = DEDUCTIONS_DIR / "deductions_all.csv"
    if not path.exists():
        raise HTTPException(status_code=503, detail="Initial deductions sync is still running")

    out = io.StringIO(newline="")
    writer = csv.writer(out)
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if not header or len(header) < 16:
            raise HTTPException(status_code=500, detail="Deductions export schema is invalid")
        writer.writerow(header[1:16])
        expected_shop = shop_names[shop_id]
        for row in reader:
            if row and str(row[0]).strip() == expected_shop:
                writer.writerow(row[1:16])

    return Response(
        content="\ufeff" + out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/api/fbs/supplies.csv")
async def fbs_supplies_export(request: Request):
    authorize(request)
    path = FBS_DIR / "supplies_ap.csv"
    if not path.exists():
        raise HTTPException(status_code=503, detail="Initial FBS supply sync is still running")
    return FileResponse(
        path,
        media_type="text/csv; charset=utf-8",
        filename="wb_fbs_supplies_ap.csv",
    )


@app.get("/api/fbs/status")
async def fbs_status(request: Request):
    authorize(request)
    path = FBS_DIR / "status.json"
    if not path.exists():
        return JSONResponse({"state": "syncing"})
    try:
        import json
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:
        return JSONResponse({"state": "error", "error": str(exc)}, status_code=500)


@app.get("/api/fbs/penalty-evidence.csv")
async def fbs_penalty_evidence_export(request: Request):
    authorize(request)
    path = FBS_DIR / "penalty_evidence_all.csv"
    if not path.exists():
        raise HTTPException(status_code=503, detail="Initial penalty evidence sync is still running")
    return FileResponse(
        path,
        media_type="text/csv; charset=utf-8",
        filename="wb_fbs_penalty_evidence.csv",
    )


@app.get("/api/fbs/penalty-evidence-status")
async def fbs_penalty_evidence_status(request: Request):
    authorize(request)
    path = FBS_DIR / "evidence_status.json"
    if not path.exists():
        return JSONResponse({"state": "syncing"})
    try:
        import json
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:
        return JSONResponse({"state": "error", "error": str(exc)}, status_code=500)


@app.get("/api/traffic/status")
async def traffic_status(request: Request):
    authorize(request)
    path = TRAFFIC_DIR / "status.json"
    if not path.exists():
        return JSONResponse({"state": "syncing"}, status_code=503)
    import json
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@app.get("/api/traffic/{dataset}.csv")
async def traffic_export(dataset: str, request: Request):
    authorize(request)
    if dataset not in {"campaign_sku_day", "funnel_sku_day"}:
        raise HTTPException(status_code=404, detail="Unknown dataset")
    path = TRAFFIC_DIR / f"{dataset}.csv"
    if not path.exists():
        raise HTTPException(status_code=503, detail="Initial traffic sync is still running")
    return FileResponse(path, media_type="text/csv; charset=utf-8", filename=path.name)


PRICE_SHOPS = {
    "AP": ("Саныч", "WB_API_TOKEN_AP"),
    "AA": ("AIR", "WB_API_TOKEN_AA"),
    "YV": ("Хозяюшка", "WB_API_TOKEN_YV"),
}


def authorize_price_export(request: Request) -> None:
    expected = PRICE_EXPORT_KEY or EXPORT_TOKEN
    if not expected:
        raise HTTPException(status_code=503, detail="PRICE_EXPORT_KEY/FINANCE_EXPORT_TOKEN is not configured")
    supplied = request.query_params.get("key") or request.query_params.get("token") or ""
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _as_number(value: Any) -> Any:
    if value is None or value == "":
        return ""
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _computed_after_discount(price: Any, discount: Any) -> str:
    try:
        p = float(price)
        d = float(discount or 0)
        return str(round(p * (100 - d) / 100, 2))
    except (TypeError, ValueError):
        return ""


def _price_error_row(snapshot_at: str, shop_id: str, shop_name: str, status: str, error: str) -> list[Any]:
    # Header has 18 columns. Columns 4..16 are product/price fields.
    return [snapshot_at, shop_id, shop_name, "", "", "", "", "", "", "", "", "", "", "", "", "", status, error]


async def _fetch_wb_price_rows_for_shop(
    client: httpx.AsyncClient,
    shop_id: str,
    shop_name: str,
    token: str,
    snapshot_at: str,
) -> list[list[Any]]:
    rows: list[list[Any]] = []
    url = "https://discounts-prices-api.wildberries.ru/api/v2/list/goods/filter"
    limit = max(1, min(1000, PRICE_EXPORT_LIMIT))
    offset = 0
    headers_plain = {"Authorization": token}
    headers_bearer = {"Authorization": f"Bearer {token}"}

    while True:
        params = {"limit": limit, "offset": offset}
        try:
            response = await client.get(url, headers=headers_plain, params=params)
            if response.status_code in {401, 403}:
                retry = await client.get(url, headers=headers_bearer, params=params)
                if retry.status_code != response.status_code:
                    response = retry
            if response.status_code != 200:
                rows.append(_price_error_row(
                    snapshot_at, shop_id, shop_name,
                    f"HTTP_{response.status_code}", response.text[:300].replace("\n", " "),
                ))
                break
            payload = response.json()
            data = payload.get("data", payload) if isinstance(payload, dict) else {}
            goods = data.get("listGoods") or data.get("goods") or payload.get("listGoods", []) if isinstance(payload, dict) else []
            if not goods:
                break
            for good in goods:
                if not isinstance(good, dict):
                    continue
                nm_id = good.get("nmID") or good.get("nmId") or good.get("nm") or ""
                vendor_code = good.get("vendorCode") or good.get("vendor_code") or ""
                currency = good.get("currencyIsoCode4217") or good.get("currency") or ""
                discount = good.get("discount") or ""
                club_discount = good.get("clubDiscount") or ""
                editable_size_price = good.get("editableSizePrice")
                is_bad_turnover = good.get("isBadTurnover")
                sizes = good.get("sizes") if isinstance(good.get("sizes"), list) else []
                if not sizes:
                    sizes = [{}]
                for size in sizes:
                    if not isinstance(size, dict):
                        size = {}
                    price = size.get("price", good.get("price", ""))
                    discounted_price = size.get("discountedPrice", good.get("discountedPrice", ""))
                    club_discounted_price = size.get("clubDiscountedPrice", good.get("clubDiscountedPrice", ""))
                    rows.append([
                        snapshot_at,
                        shop_id,
                        shop_name,
                        nm_id,
                        vendor_code,
                        currency,
                        discount,
                        club_discount,
                        editable_size_price,
                        is_bad_turnover,
                        size.get("sizeID") or size.get("sizeId") or "",
                        size.get("techSizeName") or size.get("techSize") or "",
                        price,
                        discounted_price,
                        club_discounted_price,
                        _computed_after_discount(price, discount),
                        "OK",
                        "",
                    ])
            if len(goods) < limit:
                break
            offset += limit
        except Exception as exc:
            rows.append(_price_error_row(
                snapshot_at, shop_id, shop_name,
                "ERROR", str(exc)[:300].replace("\n", " "),
            ))
            break
    return rows


@app.get("/api/prices/export.csv")
async def prices_export(request: Request):
    authorize_price_export(request)
    requested_shop = str(request.query_params.get("shop", "all")).strip().upper()
    selected = PRICE_SHOPS.items() if requested_shop in {"", "ALL"} else [(requested_shop, PRICE_SHOPS.get(requested_shop))]
    selected = [(code, meta) for code, meta in selected if meta]
    if not selected:
        raise HTTPException(status_code=404, detail="Unknown shop")

    snapshot_at = dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()
    header = [
        "snapshot_at_utc", "shop_id", "shop_name", "nmID", "vendorCode", "currency",
        "discount_pct", "club_discount_pct", "editable_size_price", "is_bad_turnover",
        "sizeID", "techSizeName", "price_before_discount", "discounted_price",
        "club_discounted_price", "computed_after_discount", "source_status", "source_error",
    ]
    all_rows: list[list[Any]] = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for shop_id, (shop_name, env_name) in selected:
            token = os.environ.get(env_name, "").strip()
            if not token:
                all_rows.append(_price_error_row(snapshot_at, shop_id, shop_name, "NO_TOKEN", env_name))
                continue
            all_rows.extend(await _fetch_wb_price_rows_for_shop(client, shop_id, shop_name, token, snapshot_at))

    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(header)
    writer.writerows(all_rows)
    return Response(
        content="\ufeff" + out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/api/prices/status")
async def prices_status(request: Request):
    authorize_price_export(request)
    return JSONResponse({
        "ok": True,
        "configured_shops": [code for code, (_name, env_name) in PRICE_SHOPS.items() if os.environ.get(env_name, "").strip()],
        "endpoint": "/api/prices/export.csv",
    })


app.mount("/", wb_app)
