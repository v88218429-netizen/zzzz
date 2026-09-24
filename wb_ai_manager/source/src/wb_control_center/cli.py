from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import uvicorn

from .config import Settings
from .engine import ControlCenter


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wb-control", description="WB AI Control Center")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="Run API + scheduler")
    sub.add_parser("doctor", help="Check WB data source and tools")
    one = sub.add_parser("run-agent", help="Run one agent")
    one.add_argument("name")
    sub.add_parser("run-all", help="Run all agents once")
    sub.add_parser("status", help="Show local status")
    sub.add_parser("demo", help="Run a complete rules-only demo with no WB/OpenAI/Claude API keys")
    return p


async def doctor(settings: Settings) -> int:
    c = ControlCenter(settings)
    try:
        await c.wb.start()
        shops = await c.wb.call("wb_list_shops", {})
        print(json.dumps({
            "ok": True,
            "wb_mode": settings.wb_mode,
            "reasoning": c.llm.label,
            "tools": len(c.wb.tools),
            "shops": shops,
        }, ensure_ascii=False, indent=2, default=str))
        return 0
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
        return 1
    finally:
        await c.wb.close()


async def run_one(settings: Settings, name: str) -> int:
    c = ControlCenter(settings)
    try:
        await c.wb.start()
        print(json.dumps(await c.run_agent(name), ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        await c.wb.close()


async def run_all(settings: Settings) -> int:
    c = ControlCenter(settings)
    try:
        await c.wb.start()
        print(json.dumps(await c.run_all_once(), ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        await c.wb.close()


async def run_demo() -> int:
    settings = Settings(wb_mode="demo", llm_provider="rules", data_dir="./data/demo", enable_public_wb_search=False, remote_policy_url="", auto_update_enabled=False)
    # Each demo is clean and deterministic. It never touches the live database.
    demo_db = settings.data_path / "control_center.sqlite3"
    for suffix in ("", "-wal", "-shm"):
        try:
            (settings.data_path / ("control_center.sqlite3" + suffix)).unlink()
        except FileNotFoundError:
            pass
    c = ControlCenter(settings)
    # Seed a previous search position so the position agent demonstrates change detection on first run.
    old = {
        "1001:ведро пластиковое 5 л": {"nm_id": 1001, "query": "ведро пластиковое 5 л", "position": 5.0},
        "1002:бидон 10 л": {"nm_id": 1002, "query": "бидон 10 л", "position": 6.0},
    }
    c.db.save_snapshot("search_positions", "positions", old, (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat())
    try:
        await c.wb.start()
        results = await c.run_all_once()
        events = c.db.recent_events(hours=24, limit=100)
        actions = c.db.pending_actions(limit=100)
        summary = {
            "mode": "DEMO / RULES-ONLY / NO CLOUD API",
            "agents_run": sum(1 for item in results if isinstance(item, dict) and item.get("agent")),
            "decision_engine_run": any(isinstance(item, dict) and item.get("system") == "decision_engine" for item in results),
            "critical": sum(1 for e in events if e.get("severity") == "critical"),
            "warnings": sum(1 for e in events if e.get("severity") == "warning"),
            "pending_actions": [{"id": a["id"], "tool": a["tool"], "reason": a["reason"]} for a in actions],
            "top_events": [
                {"agent": e["agent"], "severity": e["severity"], "title": e["title"], "message": e["message"]}
                for e in events if e.get("severity") in {"critical", "warning"}
            ][:15],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        await c.wb.close()


def main() -> None:
    args = build_parser().parse_args()
    settings = Settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.cmd == "run":
        uvicorn.run("wb_control_center.api:app", host=settings.app_host, port=settings.app_port, reload=False)
    elif args.cmd == "doctor":
        raise SystemExit(asyncio.run(doctor(settings)))
    elif args.cmd == "run-agent":
        raise SystemExit(asyncio.run(run_one(settings, args.name)))
    elif args.cmd == "run-all":
        raise SystemExit(asyncio.run(run_all(settings)))
    elif args.cmd == "demo":
        raise SystemExit(asyncio.run(run_demo()))
    elif args.cmd == "status":
        c = ControlCenter(settings)
        print(asyncio.run(c.status_text()))


if __name__ == "__main__":
    main()
