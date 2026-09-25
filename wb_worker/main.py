import asyncio
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from wb_mcp import settings as cfg
from wb_mcp.app import fastapi_app as wb_app

from finance_sync import DATA_DIR as FINANCE_DIR, sync_loop
from fbs_supply_sync import DATA_DIR as FBS_DIR, sync_loop as fbs_supply_sync_loop
from penalty_evidence_sync import sync_loop as penalty_evidence_sync_loop
from traffic_sync import DATA_DIR as TRAFFIC_DIR, sync_loop as traffic_sync_loop

ROOT_DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
EXPORT_TOKEN = os.environ.get("FINANCE_EXPORT_TOKEN", "").strip()


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
        fbs_task = asyncio.create_task(fbs_supply_sync_loop())
        evidence_task = asyncio.create_task(penalty_evidence_sync_loop())
        traffic_task = asyncio.create_task(traffic_sync_loop())
        try:
            yield
        finally:
            task.cancel()
            fbs_task.cancel()
            for bg_task in (task, fbs_task, evidence_task, traffic_task):
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


app.mount("/", wb_app)
