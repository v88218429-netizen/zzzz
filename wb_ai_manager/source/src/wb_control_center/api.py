from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .engine import ControlCenter
from .models import PeriodContext
from .portfolio import PortfolioService
from .source_discovery import SourceDiscovery
from .auto_sheets import AutoSheets
from .updater import UpdateManager, current_version

settings = Settings()
center = ControlCenter(settings)
portfolio = PortfolioService(settings)
discovery = SourceDiscovery()
UI_DIR = Path(__file__).resolve().parent / "ui"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
update_manager = UpdateManager(PROJECT_ROOT, settings.auto_update_manifest_url, settings.auto_update_channel)
log = logging.getLogger(__name__)
_background_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await center.start()
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
    return _health_payload()


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
    "cost_guard": ["paid_storage", "measurement_penalties", "deductions", "paid_acceptance", "worker_penalty_evidence"],
    "reviews_questions": ["seller_rating", "flags", "feedbacks", "questions"],
    "buyer_chats": ["chat_events"],
    "orders_fbs": ["new_orders", "reshipment", "worker_fbs"],
    "returns_quality": ["open_claims"],
    "documents": ["documents_7d"],
}


def _dashboard_snapshots(period_key: str | None = None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for source, keys in SNAPSHOT_SPEC.items():
        rows: dict[str, Any] = {}
        for key in keys:
            db_key = f"period::{period_key}::{key}" if period_key else key
            item = center.db.latest_snapshot(source, db_key)
            if item:
                rows[key] = item
        if rows:
            out[source] = rows
    return out




def _dashboard_period(days: int = 7, from_date: str | None = None, to_date: str | None = None) -> dict[str, Any]:
    tz = ZoneInfo(settings.app_timezone)
    today = datetime.now(tz).date()
    default_end = today - timedelta(days=1)
    window_days = max(1, min(days, 90))
    try:
        end_day = date.fromisoformat(to_date) if to_date else default_end
        start_day = date.fromisoformat(from_date) if from_date else end_day - timedelta(days=window_days - 1)
    except ValueError:
        end_day = default_end
        start_day = end_day - timedelta(days=window_days - 1)
    # The dashboard is retrospective. Never expose dates that have not happened yet.
    if end_day > today:
        end_day = today
    if start_day > today:
        start_day = today
    if end_day < start_day:
        start_day, end_day = end_day, start_day
    if (end_day - start_day).days > 90:
        start_day = end_day - timedelta(days=90)
    start_local = datetime.combine(start_day, time.min, tzinfo=tz)
    end_local = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=tz)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)
    return {
        "from": start_day.isoformat(),
        "to": end_day.isoformat(),
        "start_utc": start_utc.isoformat(),
        "end_utc": end_utc.isoformat(),
        "days": (end_day - start_day).days + 1,
        "label": f"{start_day.strftime('%d.%m.%Y')}–{end_day.strftime('%d.%m.%Y')}",
        "max_selectable": today.isoformat(),
        "default_mode": "last_completed_7_days" if not from_date and not to_date else "custom",
    }


def _entity_map(portfolio_snapshot: dict[str, Any], snapshots: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Canonical UI identity map.

    Product identity is not a financial fact, so a live WB card catalogue is the
    authoritative source for nmID -> seller article. Trusted tables may enrich/fill
    missing identity fields, but historical economics never overrides a live card.
    """
    out: dict[str, dict[str, Any]] = {}

    def walk(value: Any):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk(child)

    card_snapshot = snapshots.get("cards", {}).get("card_catalog", {})
    card_data = card_snapshot.get("data") if isinstance(card_snapshot, dict) else card_snapshot
    for row in walk(card_data):
        nm = row.get("nmID", row.get("nmId", row.get("nm_id")))
        try:
            nm_s = str(int(nm)) if nm is not None else ""
        except (TypeError, ValueError):
            nm_s = str(nm or "").strip()
        if not nm_s or not nm_s.isdigit():
            continue
        article = str(
            row.get("vendorCode")
            or row.get("vendor_code")
            or row.get("supplierArticle")
            or row.get("supplier_article")
            or ""
        ).strip()
        title = str(row.get("title") or row.get("name") or "").strip()
        if not article and not title:
            continue
        prev = out.get(nm_s) or {}
        out[nm_s] = {
            "nm_id": nm_s,
            "seller_article": article or prev.get("seller_article"),
            "display": article or title or prev.get("display") or f"Товар WB {nm_s}",
            "title": title or prev.get("title"),
            "source": "live_wb_cards",
            "source_at": card_snapshot.get("created_at") if isinstance(card_snapshot, dict) else None,
        }

    own = portfolio_snapshot.get("own_27") or {}
    for row in own.get("products") or []:
        if not isinstance(row, dict):
            continue
        nm = str(row.get("sku") or "").strip()
        article = str(row.get("name") or "").strip()
        if not nm:
            continue
        prev = out.get(nm) or {}
        out[nm] = {
            "nm_id": nm,
            "seller_article": prev.get("seller_article") or article or None,
            "display": prev.get("display") or article or f"Товар WB {nm}",
            "title": prev.get("title"),
            "cabinet": row.get("cabinet"),
            "weekly_group": row.get("weekly_group"),
            "source": prev.get("source") or "portfolio_identity_fallback",
            "source_at": prev.get("source_at"),
        }
    return out


def _store_name(snapshots: dict[str, dict[str, Any]]) -> str:
    shops = snapshots.get("api_health", {}).get("shops", {}).get("data", {})
    candidates: list[dict[str, Any]] = []
    if isinstance(shops, dict):
        for k in ("shops", "data", "items"):
            if isinstance(shops.get(k), list):
                candidates = [x for x in shops[k] if isinstance(x, dict)]
                break
    elif isinstance(shops, list):
        candidates = [x for x in shops if isinstance(x, dict)]
    if candidates:
        return str(candidates[0].get("name") or candidates[0].get("title") or "Wildberries")
    return "DEMO WB cabinet" if settings.wb_mode.lower() == "demo" else "Wildberries"


@app.get("/api/dashboard-data")
async def dashboard_data(days: int = 7, from_date: str | None = None, to_date: str | None = None) -> dict[str, Any]:
    period = _dashboard_period(days, from_date, to_date)
    period_ctx = PeriodContext.from_strings(period["from"], period["to"])
    audit = center.period_audit_status(period_ctx)
    audit_ready = audit.get("status") == "completed"
    if audit_ready:
        snapshots = _dashboard_snapshots(period_ctx.key)
        events_period = list(audit.get("events") or [])
        runs = dict(audit.get("agents") or {})
        recommendations = []
        decisions = list(audit.get("decisions") or [])
    else:
        snapshots = _dashboard_snapshots()
        events_period = center.db.events_between(period["start_utc"], period["end_utc"], limit=1000)
        runs = center.db.latest_runs_by_agent(hours=720)
        recommendations = center.db.pending_actions(limit=100)
        decisions = center.db.current_decisions(limit=200) or center.refresh_decisions()
    completed_runs = [r for r in runs.values() if isinstance(r, dict) and r.get("finished_at")]
    last_run_at = max((str(r["finished_at"]) for r in completed_runs), default=None)
    if audit_ready:
        period_count_events = [
            e for e in events_period
            if (((e.get("payload") or {}).get("_analysis") or {}).get("scope") != "current_snapshot")
        ]
        current_snapshot_events = [
            e for e in events_period
            if (((e.get("payload") or {}).get("_analysis") or {}).get("scope") == "current_snapshot")
        ]
    else:
        period_count_events = events_period
        current_snapshot_events = []
    runtime_policy = await asyncio.to_thread(center.runtime_policy.get)
    portfolio_snapshot = portfolio.snapshot()
    entity_map = _entity_map(portfolio_snapshot, snapshots)
    return {
        "health": _health_payload(),
        "store": {"name": _store_name(snapshots), "mode": settings.wb_mode},
        "summary": {
            "critical_24h": sum(1 for e in period_count_events if e.get("severity") == "critical"),
            "warning_24h": sum(1 for e in period_count_events if e.get("severity") == "warning"),
            "info_24h": sum(1 for e in period_count_events if e.get("severity") == "info"),
            "current_snapshot_alerts": sum(1 for e in current_snapshot_events if e.get("severity") in {"critical", "warning"}),
            "recommendations": len(recommendations),
            "agent_errors_24h": sum(1 for r in center.db.recent_runs(hours=24, limit=1000) if r.get("status") == "error"),
            "last_run_at": last_run_at,
        },
        "agents": {
            name: {
                "interval_minutes": center.policy.schedules.get(name),
                "scheduled": center.policy.schedules.get(name) is not None,
            }
            for name in sorted(center.agents)
        },
        "runs": runs,
        "events": events_period[:300],
        "period": {k: v for k, v in period.items() if k not in {"start_utc", "end_utc"}},
        "period_audit": {
            "status": audit.get("status"),
            "ready": audit_ready,
            "period": period_ctx.to_dict(),
            "agents_done": sum(1 for x in (audit.get("agents") or {}).values() if isinstance(x, dict) and x.get("status") in {"ok", "error"}),
            "agents_total": 19,
            "errors": audit.get("errors") or [],
            "started_at": audit.get("started_at"),
            "finished_at": audit.get("finished_at"),
        },
        "entity_map": entity_map,
        "data_quality": {
            "portfolio_current": bool(portfolio_snapshot.get("current_data")),
            "portfolio_status": portfolio_snapshot.get("data_status"),
            "portfolio_origin": portfolio_snapshot.get("data_origin"),
            "portfolio_period": portfolio_snapshot.get("period"),
            "portfolio_warning": portfolio_snapshot.get("stale_reason"),
            "period_audit_status": audit.get("status"),
            "period_snapshot_mode": "selected_period" if audit_ready else "operational_fallback",
        },
        "recommendations": recommendations,
        "decisions": decisions,
        "snapshots": snapshots,
        "decision_control": {
            "history": center.db.decision_history(limit=80),
            "changes": center.db.recent_observed_changes(limit=80),
            "evaluations": center.db.recent_evaluations(limit=80),
            "latest_review": center.db.latest_snapshot("decision_review", "latest"),
            "demand_model_validation": center.db.latest_snapshot("model_validation", "demand"),
        },
        "knowledge": {
            "policy_version": center.policy.raw.get("version"),
            "source_priority": ["live_wb", "trusted_sheets", "store_history", "official_wb", "tenant_policy", "generic_heuristics"],
        },
        "runtime_policy": runtime_policy,
        "runtime_policy_history": center.runtime_policy.history()[:10],
        "remote_policy": center.runtime_policy.remote_status(),
        "policy_safety": {"hard_max_bid_change_pct": center.policy.safety.get("limits",{}).get("max_bid_change_pct"), "absolute_bid_cap_rub": center.policy.safety.get("limits",{}).get("absolute_bid_cap_rub")},
        "portfolio": portfolio_snapshot,
        "connections": portfolio.connections(),
    }





@app.get("/api/period-audit")
async def period_audit_status(from_date: str, to_date: str) -> dict[str, Any]:
    try:
        period = PeriodContext.from_strings(from_date, to_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return center.period_audit_status(period)


@app.post("/api/period-audit")
async def start_period_audit(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        period = PeriodContext.from_strings(str(payload.get("from_date") or ""), str(payload.get("to_date") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Некорректный период: {exc}")
    return center.start_period_audit(period)


@app.get("/api/decisions")
async def decisions() -> list[dict[str, Any]]:
    return center.db.current_decisions(limit=200) or center.refresh_decisions()

@app.post("/api/decisions/refresh")
async def refresh_decisions() -> dict[str, Any]:
    rows=center.refresh_decisions()
    return {"ok": True, "count": len(rows), "decisions": rows}


@app.get("/api/decision-control")
async def decision_control() -> dict[str, Any]:
    return {
        "history": center.db.decision_history(limit=300),
        "changes": center.db.recent_observed_changes(limit=300),
        "evaluations": center.db.recent_evaluations(limit=300),
        "latest_review": center.db.latest_snapshot("decision_review", "latest"),
        "demand_model_validation": center.db.latest_snapshot("model_validation", "demand"),
    }


@app.get("/api/source-discovery")
async def source_discovery() -> dict[str, Any]:
    return await asyncio.to_thread(discovery.discover)

@app.get("/api/connections")
async def connections() -> dict[str, Any]:
    return portfolio.connections()


@app.get("/api/portfolio")
async def portfolio_data() -> dict[str, Any]:
    return portfolio.snapshot()


@app.post("/api/sheets/refresh")
async def refresh_sheets() -> dict[str, Any]:
    try:
        if settings.google_sheets_bridge_url and settings.google_sheets_bridge_key:
            data = await asyncio.to_thread(portfolio.refresh)
            mode = "apps_script_bridge"
        else:
            payload = await asyncio.to_thread(AutoSheets().refresh_core_via_browser)
            if not payload.get("sources"):
                raise RuntimeError("Не удалось получить Google Sheets через текущую браузерную сессию. Если Google попросил вход — войди один раз и нажми обновить снова.")
            data = portfolio.merge_payload(payload, "auto_browser_sheets")
            mode = "auto_browser_session"
        center.refresh_decisions()
        return {"ok": True, "mode": mode, "generated_at": data.get("generated_at"), "sources": data.get("bridge", {}).get("sources_received", [])}
    except Exception as exc:
        raise HTTPException(502, f"Google Sheets refresh failed: {exc}")



@app.get("/api/policy-studio")
async def policy_studio() -> dict[str, Any]:
    policy = await asyncio.to_thread(center.runtime_policy.get)
    return {
        "policy": policy,
        "history": center.runtime_policy.history()[:20],
        "remote_policy": center.runtime_policy.remote_status(),
        "safety": {
            "read_only": not center.policy_engine.execution_allowed()[0],
            "hard_max_bid_change_pct": center.policy.safety.get("limits", {}).get("max_bid_change_pct"),
            "absolute_bid_cap_rub": center.policy.safety.get("limits", {}).get("absolute_bid_cap_rub"),
        },
    }


@app.post("/api/policy-studio/remote-refresh")
async def refresh_remote_policy() -> dict[str, Any]:
    await asyncio.to_thread(center.remote_policy.get, True)
    policy = await asyncio.to_thread(center.runtime_policy.get)
    decisions = center.refresh_decisions()
    return {"ok": True, "remote_policy": center.runtime_policy.remote_status(), "policy": policy, "decisions": len(decisions)}


@app.post("/api/policy-studio/advertising")
async def update_policy_studio(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        result = await asyncio.to_thread(center.runtime_policy.update_advertising, payload, "dashboard")
        decisions = center.refresh_decisions()
        return {"ok": True, "policy": result, "decisions": len(decisions)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/policy-studio/rollback/{index}")
async def rollback_policy_studio(index: int) -> dict[str, Any]:
    try:
        result = await asyncio.to_thread(center.runtime_policy.rollback, index)
        decisions = center.refresh_decisions()
        return {"ok": True, "policy": result, "decisions": len(decisions)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/advertising-control-plans")
async def advertising_control_plans() -> list[dict[str, Any]]:
    rows = center.db.current_decisions(limit=500) or center.refresh_decisions()
    return [
        x for x in rows
        if str(x.get("decision_key", "")).startswith("advert:")
        and (
            str(x.get("decision_key", "")).endswith(":numeric_control")
            or str(x.get("decision_key", "")).endswith(":zero_orders")
        )
    ]


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return FileResponse(UI_DIR / "dashboard.html", media_type="text/html")


@app.get("/")
async def root():
    return FileResponse(UI_DIR / "dashboard.html", media_type="text/html")
