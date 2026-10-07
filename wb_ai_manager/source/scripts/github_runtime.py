from __future__ import annotations

import asyncio
import json
import os
import shutil
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from wb_control_center.config import Settings, load_policy
from wb_control_center.engine import ControlCenter
from wb_control_center.portfolio import PortfolioService
from wb_control_center.query_manager import QueryManager, QueryManagerConfig

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("GITHUB_RUNTIME_OUTPUT", ROOT / "github_site")).resolve()
DATA_ROOT = Path(os.environ.get("DATA_DIR", ROOT / "data" / "github-runtime")).resolve()

CABINETS = (
    ("sanych", "Саныч", "WB_API_TOKEN_AP"),
    ("air", "AIR", "WB_API_TOKEN_AA"),
    ("hozyushka", "Хозяюшка", "WB_API_TOKEN_YV"),
)


def dump(name: str, value: Any) -> None:
    path = OUT / "data" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def snapshots(center: ControlCenter) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for source in center.agents:
        rows: dict[str, Any] = {}
        for key in center.db.snapshot_keys(source):
            item = center.db.latest_snapshot(source, key)
            if item:
                rows[key] = item
        if rows:
            out[source] = rows
    return out


def query_manager(portfolio: dict[str, Any], snap: dict[str, Any]) -> dict[str, Any]:
    policy = load_policy()
    limits = policy.safety.get("limits", {}) if isinstance(policy.safety, dict) else {}
    return QueryManager(
        QueryManagerConfig(
            max_bid_change_pct=float(limits.get("max_bid_change_pct") or 10.0),
            absolute_bid_cap_rub=float(limits.get("absolute_bid_cap_rub") or 1000.0),
            auto_execute_bid_change_pct=0.0,
        )
    ).build(portfolio, snap)


def compact_decision(row: dict[str, Any], cabinet: str) -> dict[str, Any]:
    return {
        "cabinet": cabinet,
        "priority": row.get("priority"),
        "confidence": row.get("confidence"),
        "scope": row.get("scope"),
        "entity_id": row.get("entity_id"),
        "title": row.get("title"),
        "diagnosis": row.get("diagnosis"),
        "follow_up": row.get("follow_up"),
        "recommended_actions": (row.get("recommended_actions") or [])[:3],
        "blockers": (row.get("blockers") or [])[:5],
    }


def owner_dashboard(payload: dict[str, Any]) -> dict[str, Any]:
    cabinets = payload.get("cabinets") or {}
    priority_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    actions: list[dict[str, Any]] = []
    rows = []
    for slug, cab in cabinets.items():
        rows.append({
            "id": slug,
            "name": cab.get("name"),
            "wb_connected": cab.get("wb_connected"),
            "status": cab.get("status"),
            "decisions": len(cab.get("decisions") or []),
            "critical": cab.get("critical_events"),
            "warnings": cab.get("warning_events"),
            "agent_errors": cab.get("agent_errors"),
        })
        for d in cab.get("decisions") or []:
            if isinstance(d, dict):
                actions.append(compact_decision(d, str(cab.get("name") or slug)))
    actions.sort(key=lambda x: (priority_rank.get(str(x.get("priority")), 9), str(x.get("cabinet")), str(x.get("title"))))
    q = payload.get("query_manager") or {}
    return {
        "generated_at": payload.get("generated_at"),
        "overall_status": payload.get("health"),
        "cabinets": rows,
        "top_actions": actions[:25],
        "query_summary": q.get("summary") or {},
        "query_tasks": (q.get("operator_brief") or {}).get("tasks") or [],
        "data_blockers": (q.get("operator_brief") or {}).get("data_blockers") or [],
        "source_errors": payload.get("source_errors") or [],
        "note": "GitHub is the scheduler/runtime. Google Apps Script is only the authenticated Google Sheets adapter.",
    }


def publish_private_dashboard(dashboard: dict[str, Any]) -> dict[str, Any]:
    url = (os.environ.get("GOOGLE_SHEETS_BRIDGE_URL") or "").strip()
    key = (os.environ.get("GOOGLE_SHEETS_BRIDGE_KEY") or "").strip()
    if not url or not key:
        return {"ok": False, "error": "dashboard bridge not configured"}
    body = {"token": key, "action": "publish_dashboard", "dashboard": dashboard}
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0), follow_redirects=True) as client:
        resp = client.post(url, json=body)
        resp.raise_for_status()
        result = resp.json()
    return result if isinstance(result, dict) else {"ok": False, "error": "invalid dashboard response"}


async def run_cabinet(
    slug: str,
    name: str,
    token: str,
    portfolio_snapshot: dict[str, Any],
    shared_live_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    cab_dir = DATA_ROOT / "cabinets" / slug
    cab_dir.mkdir(parents=True, exist_ok=True)
    if shared_live_path.exists():
        shutil.copy2(shared_live_path, cab_dir / "portfolio_live.json")

    settings = Settings(
        data_dir=str(cab_dir),
        wb_mode="live",
        wb_api_token=token,
        startup_audit_enabled=False,
        auto_update_enabled=False,
        force_read_only=True,
        operation_mode="shadow",
    )
    center = ControlCenter(settings)
    errors: list[dict[str, str]] = []
    wb_connected = False
    run_results: list[dict[str, Any]] = []
    snap: dict[str, Any] = {}
    try:
        try:
            await center.wb.start()
            wb_connected = True
        except Exception as exc:
            errors.append({"stage": "wb.start", "error": str(exc)})
        run_results = await center.run_all_once()
        snap = snapshots(center)
        decisions = center.db.current_decisions(limit=500) or center.refresh_decisions()
        events = center.db.recent_events(hours=48, limit=500)
        critical = sum(1 for e in events if str(e.get("severity")) == "critical")
        warnings = sum(1 for e in events if str(e.get("severity")) == "warning")
        agent_errors = sum(1 for r in run_results if isinstance(r, dict) and r.get("status") == "error")
        payload = {
            "id": slug,
            "name": name,
            "status": "ok" if wb_connected and agent_errors == 0 else "degraded",
            "wb_connected": wb_connected,
            "errors": errors,
            "agent_errors": agent_errors,
            "critical_events": critical,
            "warning_events": warnings,
            "decisions": decisions,
            "events": events,
            "runs": run_results,
        }
        return payload, snap
    except Exception as exc:
        errors.append({"stage": "runtime", "error": str(exc)})
        return {
            "id": slug,
            "name": name,
            "status": "error",
            "wb_connected": wb_connected,
            "errors": errors,
            "agent_errors": 1,
            "critical_events": 0,
            "warning_events": 0,
            "decisions": [],
            "events": [],
            "runs": run_results,
            "traceback": traceback.format_exc(),
        }, snap
    finally:
        try:
            await center.wb.close()
        except Exception:
            pass


async def run() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    source_errors: list[dict[str, str]] = []

    shared_dir = DATA_ROOT / "shared"
    shared_dir.mkdir(parents=True, exist_ok=True)
    shared_settings = Settings(
        data_dir=str(shared_dir),
        wb_mode="live",
        wb_api_token="",
        startup_audit_enabled=False,
        auto_update_enabled=False,
        force_read_only=True,
        operation_mode="shadow",
    )
    portfolio_service = PortfolioService(shared_settings)
    try:
        portfolio_snapshot = portfolio_service.refresh()
    except Exception as exc:
        portfolio_snapshot = portfolio_service.snapshot()
        source_errors.append({"stage": "portfolio.refresh", "error": str(exc)})

    cabinets: dict[str, Any] = {}
    cabinet_snapshots: dict[str, dict[str, Any]] = {}
    jobs: list[tuple[str, str, str, asyncio.Task]] = []
    for slug, name, env_name in CABINETS:
        token = (os.environ.get(env_name) or "").strip()
        if not token:
            cabinets[slug] = {
                "id": slug,
                "name": name,
                "status": "missing_token",
                "wb_connected": False,
                "errors": [{"stage": "config", "error": f"{env_name} is empty"}],
                "agent_errors": 0,
                "critical_events": 0,
                "warning_events": 0,
                "decisions": [],
                "events": [],
                "runs": [],
            }
            continue
        jobs.append((
            slug,
            name,
            env_name,
            asyncio.create_task(run_cabinet(
                slug,
                name,
                token,
                portfolio_snapshot,
                portfolio_service.live_path,
            )),
        ))
    if jobs:
        results = await asyncio.gather(*(x[3] for x in jobs), return_exceptions=True)
        for (slug, name, env_name, _task), result in zip(jobs, results):
            if isinstance(result, BaseException):
                cabinets[slug] = {
                    "id": slug,
                    "name": name,
                    "status": "error",
                    "wb_connected": False,
                    "errors": [{"stage": "cabinet_task", "error": str(result)}],
                    "agent_errors": 1,
                    "critical_events": 0,
                    "warning_events": 0,
                    "decisions": [],
                    "events": [],
                    "runs": [],
                }
                continue
            cab, snap = result
            cabinets[slug] = cab
            cabinet_snapshots[slug] = snap

    q = query_manager(portfolio_snapshot, cabinet_snapshots.get("sanych") or {})
    healthy_cabs = sum(1 for c in cabinets.values() if c.get("wb_connected"))
    clean_cabs = sum(1 for c in cabinets.values() if c.get("status") == "ok")
    health = "ok" if healthy_cabs == len(CABINETS) and clean_cabs == len(CABINETS) and not source_errors else "degraded"
    payload = {
        "generated_at": generated_at,
        "health": health,
        "read_only": True,
        "connected_cabinets": healthy_cabs,
        "clean_cabinets": clean_cabs,
        "expected_cabinets": len(CABINETS),
        "source_errors": source_errors,
        "portfolio": portfolio_snapshot,
        "query_manager": q,
        "cabinets": cabinets,
    }
    dashboard = owner_dashboard(payload)
    try:
        dashboard_publish = publish_private_dashboard(dashboard)
    except Exception as exc:
        dashboard_publish = {"ok": False, "error": str(exc)}
        source_errors.append({"stage": "dashboard.publish", "error": str(exc)})
        payload["health"] = "degraded"
    payload["dashboard_publish"] = dashboard_publish

    dump("latest.json", payload)
    dump("owner-dashboard.json", dashboard)
    dump("query-manager.json", q)
    dump("operator-brief.json", q.get("operator_brief") or {})
    for slug, cab in cabinets.items():
        dump(f"cabinets/{slug}.json", cab)

    public_health = {
        "generated_at": generated_at,
        "health": payload["health"],
        "connected_cabinets": healthy_cabs,
        "clean_cabinets": clean_cabs,
        "expected_cabinets": len(CABINETS),
        "sheets_live": not any(x.get("stage") == "portfolio.refresh" for x in source_errors),
        "dashboard_published": bool(dashboard_publish.get("ok")),
        "query_status": (q.get("summary") or {}).get("query_status"),
        "query_facts_status": (q.get("summary") or {}).get("facts_status"),
        "query_operational_ready": bool((q.get("summary") or {}).get("operational_ready")),
        "query_rows": (q.get("summary") or {}).get("query_rows"),
        "fresh_query_rows": (q.get("summary") or {}).get("fresh_query_rows"),
        "blocked_query_rows": (q.get("summary") or {}).get("blocked_query_rows"),
        "ready_query_actions": (q.get("summary") or {}).get("ready_query_actions"),
        "source_error_stages": [x.get("stage") for x in source_errors],
    }
    dump("health.json", public_health)
    print(json.dumps(public_health, ensure_ascii=False, indent=2))
    return 0 if healthy_cabs == len(CABINETS) else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
