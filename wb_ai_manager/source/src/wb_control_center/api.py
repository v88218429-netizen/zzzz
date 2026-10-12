from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import logging
import hashlib
import csv
import io
import json
import httpx
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .dashboard_auth import DashboardBasicAuthMiddleware
from .engine import ControlCenter
from .models import PeriodContext
from .portfolio import PortfolioService
from .photo_analyzer import PhotoAnalyzer
from .source_discovery import SourceDiscovery
from .auto_sheets import AutoSheets
from .analytics_kernel import measurement_contract, portfolio_quality, snapshot_quality
from .decision_groups import decision_group_summary, group_decisions
from .discovery_engine import discover_cross_sku_patterns
from .research_engine import build_research_agenda
from .query_manager import QueryManager, QueryManagerConfig
from .updater import UpdateManager, current_version

settings = Settings()
center = ControlCenter(settings)
portfolio = PortfolioService(settings)
photo_analyzer = PhotoAnalyzer(settings)
discovery = SourceDiscovery()
UI_DIR = Path(__file__).resolve().parent / "ui"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
update_manager = UpdateManager(PROJECT_ROOT, settings.auto_update_manifest_url, settings.auto_update_channel)
log = logging.getLogger(__name__)
_background_tasks: set[asyncio.Task] = set()
_photo_csv_cache: dict[str, dict[str, Any]] = {}
_photo_csv_inflight: set[str] = set()
_photo_csv_sem = asyncio.Semaphore(1)


def _iso_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


async def _sheets_refresh_loop() -> None:
    # Refresh trusted Sheets independently of any open browser. A dashboard client
    # will automatically invalidate/re-run its selected-period audit when a newer
    # portfolio snapshot appears.
    delay = max(300, int(settings.google_sheets_auto_refresh_seconds or 900))
    while True:
        try:
            await asyncio.to_thread(portfolio.refresh)
            log.info("automatic Google Sheets refresh completed")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("automatic Google Sheets refresh failed")
        await asyncio.sleep(delay)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await center.start()
    if settings.google_sheets_bridge_url and settings.google_sheets_bridge_key:
        task = asyncio.create_task(_sheets_refresh_loop())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    try:
        yield
    finally:
        # Stop background full-audit tasks before closing the WB connector/DB-facing
        # runtime. Otherwise shutdown can tear resources out from under a live audit.
        tasks=list(_background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        _background_tasks.clear()
        await center.stop()


app = FastAPI(title="Менеджер WB", version=current_version(PROJECT_ROOT), lifespan=lifespan)
app.add_middleware(
    DashboardBasicAuthMiddleware,
    required=settings.dashboard_auth_required,
    username=settings.dashboard_auth_user,
    password=settings.dashboard_auth_password,
)
app.mount("/assets", StaticFiles(directory=str(UI_DIR)), name="assets")


def _health_payload() -> dict[str, Any]:
    return {
        "status": "ok",
        "wb_connected": bool(center.wb.session),
        "agents": sorted(center.agents),
        "pending_actions": len(center.db.pending_actions(limit=1000)),
        "reasoning": center.llm.label,
        "wb_mode": settings.wb_mode,
        "telegram": center.notifier.enabled,
        "auto_actions": bool(settings.auto_actions),
        "operation_mode": center.policy_engine.operation_mode,
        "read_only": not center.policy_engine.execution_allowed()[0],
        "sheets_connected": bool(portfolio.live_path.exists() or (settings.google_sheets_bridge_url and settings.google_sheets_bridge_key)),
        "app_version": current_version(PROJECT_ROOT),
        "auto_update": update_manager.status(),
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    if settings.dashboard_auth_required:
        return {"status": "ok"}
    return _health_payload()


def _photo_csv_row(result: dict[str, Any]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="")
    damage_types = result.get("damage_types") or []
    if not isinstance(damage_types, list):
        damage_types = []
    writer.writerow([
        result.get("display_status")
        or (
            "ПРОАНАЛИЗИРОВАНО"
            if result.get("status") == "analyzed"
            else "ОБРАБАТЫВАЕТСЯ"
        ),
        result.get("verdict") or "",
        result.get("confidence_pct")
        if result.get("confidence_pct") is not None
        else "",
        result.get("substitution") or "",
        result.get("damage") or "",
        result.get("damage_severity") or "",
        ", ".join(str(x) for x in damage_types),
        result.get("visible_evidence") or "",
        "ДА" if result.get("human_review") else "НЕТ",
        datetime.now(ZoneInfo(settings.app_timezone)).strftime("%d.%m.%Y %H:%M"),
    ])
    return buf.getvalue()


async def _photo_csv_run(
    key: str,
    payload: dict[str, Any],
) -> None:
    async with _photo_csv_sem:
        try:
            result = await photo_analyzer.analyze(
                expected=str(payload.get("expected") or ""),
                reported_received=str(payload.get("reported_received") or ""),
                category=str(payload.get("category") or ""),
                shop=str(payload.get("shop") or ""),
                sticker=str(payload.get("sticker") or ""),
                nm_id=str(payload.get("nm_id") or ""),
                photo_urls=[str(x) for x in payload.get("photo_urls") or []],
            )
            if result.get("status") == "analyzed":
                result["display_status"] = "ПРОАНАЛИЗИРОВАНО"
            else:
                result["display_status"] = "НЕДОСТАТОЧНО ДАННЫХ"
            _photo_csv_cache[key] = result
        except Exception as exc:
            log.exception("background photo csv analysis failed")
            _photo_csv_cache[key] = {
                "status": "insufficient",
                "display_status": "ОШИБКА VISION",
                "verdict": "НЕДОСТАТОЧНО ДАННЫХ",
                "confidence_pct": "",
                "substitution": "ВОЗМОЖНО",
                "damage": "ВОЗМОЖНО",
                "damage_severity": "НЕИЗВЕСТНО",
                "damage_types": [],
                "visible_evidence": str(exc)[:350],
                "human_review": True,
            }
        finally:
            _photo_csv_inflight.discard(key)


@app.get("/api/photo-analysis.csv")
async def photo_analysis_csv(
    expected: str = "",
    received: str = "",
    category: str = "",
    shop: str = "",
    sticker: str = "",
    nm: str = "",
    p1: str = "",
    p2: str = "",
    p3: str = "",
    p4: str = "",
    p5: str = "",
    t: str = "",
) -> Response:
    # Public read-only endpoint intentionally accepts only WB static-basket image URLs.
    # It is designed for Google Sheets IMPORTDATA formulas; t is a harmless cache-buster.
    photo_urls = [x for x in [p1, p2, p3, p4, p5] if x]
    canonical = json.dumps(
        {
            "expected": expected,
            "received": received,
            "category": category,
            "shop": shop,
            "sticker": sticker,
            "nm": nm,
            "photo_urls": photo_urls,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    cached = _photo_csv_cache.get(key)
    if cached is not None:
        return Response(
            _photo_csv_row(cached),
            media_type="text/csv; charset=utf-8",
            headers={"Cache-Control": "public, max-age=120"},
        )

    if key not in _photo_csv_inflight:
        _photo_csv_inflight.add(key)
        payload = {
            "expected": expected,
            "reported_received": received,
            "category": category,
            "shop": shop,
            "sticker": sticker,
            "nm_id": nm,
            "photo_urls": photo_urls,
        }
        task = asyncio.create_task(_photo_csv_run(key, payload))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    pending = {
        "status": "pending",
        "display_status": "ОБРАБАТЫВАЕТСЯ",
        "verdict": "",
        "confidence_pct": "",
        "substitution": "",
        "damage": "",
        "damage_severity": "",
        "damage_types": [],
        "visible_evidence": "Фото поставлены в очередь на локальный анализ.",
        "human_review": False,
    }
    return Response(
        _photo_csv_row(pending),
        media_type="text/csv; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/photo-analysis/status")
async def photo_analysis_status() -> dict[str, Any]:
    return photo_analyzer.status()


@app.post("/api/photo-analysis")
async def photo_analysis(
    payload: dict[str, Any],
    x_bridge_key: str | None = Header(default=None),
) -> dict[str, Any]:
    expected_key = str(settings.google_sheets_bridge_key or "").strip()
    if expected_key and x_bridge_key != expected_key:
        raise HTTPException(status_code=401, detail="unauthorized")

    photo_urls = payload.get("photo_urls") or []
    if not isinstance(photo_urls, list):
        raise HTTPException(status_code=400, detail="photo_urls must be an array")

    try:
        return await photo_analyzer.analyze(
            expected=str(payload.get("expected") or ""),
            reported_received=str(payload.get("reported_received") or ""),
            category=str(payload.get("category") or ""),
            shop=str(payload.get("shop") or ""),
            sticker=str(payload.get("sticker") or ""),
            nm_id=str(payload.get("nm_id") or ""),
            photo_urls=[str(x) for x in photo_urls],
        )
    except httpx.HTTPStatusError as exc:
        body = ""
        try:
            body = exc.response.text[:600]
        except Exception:
            pass
        raise HTTPException(
            status_code=502,
            detail=f"vision provider error: {exc.response.status_code} {body}",
        )
    except Exception as exc:
        log.exception("photo analysis failed")
        raise HTTPException(status_code=502, detail=f"photo analysis failed: {exc}")


@app.get("/api/update-status")
async def update_status() -> dict[str, Any]:
    return update_manager.status()


@app.post("/api/update-check")
async def update_check() -> dict[str, Any]:
    result = await asyncio.to_thread(update_manager.check)
    result.pop("manifest", None)
    return result


@app.get("/agents")
async def agents() -> dict[str, Any]:
    return {
        name: {
            "interval_minutes": center.policy.schedules.get(name),
            "scheduled": center.policy.schedules.get(name) is not None,
        }
        for name in sorted(center.agents)
    }


@app.post("/agents/{name}/run")
async def run_agent(name: str):
    if name not in center.agents:
        raise HTTPException(404, "unknown agent")
    return await center.run_agent(name)


async def _run_all_background() -> None:
    try:
        await center.run_all_once()
    except Exception:
        log.exception("background run-all failed")


@app.post("/run-all")
async def run_all(background: bool = False):
    # Read-only release: the build fuse blocks WB writes regardless of runtime settings.
    if background:
        task=asyncio.create_task(_run_all_background())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        return {"status": "started", "background": True}
    return await center.run_all_once()


@app.get("/events")
async def events(hours: int = 24, limit: int = 200):
    return center.db.recent_events(hours=max(1, min(hours, 720)), limit=max(1, min(limit, 1000)))


@app.get("/actions")
async def actions():
    # Historical endpoint kept for compatibility. In the UI these are recommendations only.
    return center.db.pending_actions(limit=500)


@app.post("/actions/{action_id}/approve")
async def approve(action_id: int):
    # Hard-disabled in the read-only test build. Recommendations can be reviewed, but
    # no endpoint exposed by this app is allowed to apply them to Wildberries.
    raise HTTPException(409, "read-only build: WB writes are disabled")


@app.post("/actions/{action_id}/reject")
async def reject(action_id: int):
    try:
        return await center.reject_action(action_id)
    except KeyError:
        raise HTTPException(404, "action not found")


SNAPSHOT_SPEC: dict[str, list[str]] = {
    "api_health": ["shops", "token_info", "degradations", "worker_source_health"],
    "cards": ["card_errors", "banned_products", "price_quarantine", "card_catalog"],
    "advertising_monitor": ["active_campaigns", "stats_7d"],
    "inventory": ["coverage"],
    "supply": ["acceptance"],
    "funnel": ["funnel_7d"],
    "search_positions": ["positions"],
    "price_margin": ["prices", "promotions"],
    "finance": ["balance", "report_7d", "worker_finance"],
   