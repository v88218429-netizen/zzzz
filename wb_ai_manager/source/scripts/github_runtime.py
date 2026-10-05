from __future__ import annotations

import asyncio
import json
import os
import traceback
from pathlib import Path
from typing import Any

from wb_control_center.config import Settings, load_policy
from wb_control_center.engine import ControlCenter
from wb_control_center.portfolio import PortfolioService
from wb_control_center.query_manager import QueryManager, QueryManagerConfig

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("GITHUB_RUNTIME_OUTPUT", ROOT / "github_site")).resolve()


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


async def run() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        data_dir=os.environ.get("DATA_DIR", str(ROOT / "data" / "github-runtime")),
        startup_audit_enabled=False,
        auto_update_enabled=False,
        force_read_only=True,
        operation_mode="shadow",
    )
    errors: list[dict[str, str]] = []
    portfolio = PortfolioService(settings)
    try:
        portfolio_snapshot = portfolio.refresh()
    except Exception as exc:
        portfolio_snapshot = portfolio.snapshot()
        errors.append({"stage": "portfolio.refresh", "error": str(exc)})

    center = ControlCenter(settings)
    wb_started = False
    try:
        try:
            await center.wb.start()
            wb_started = True
        except Exception as exc:
            errors.append({"stage": "wb.start", "error": str(exc)})
        run_results = await center.run_all_once()
        snap = snapshots(center)
        policy = load_policy()
        limits = policy.safety.get("limits", {}) if isinstance(policy.safety, dict) else {}
        qm = QueryManager(QueryManagerConfig(
            max_bid_change_pct=float(limits.get("max_bid_change_pct") or 15.0),
            absolute_bid_cap_rub=float(limits.get("absolute_bid_cap_rub") or 2000.0),
            auto_execute_bid_change_pct=0.0,
        )).build(portfolio.snapshot(), snap)

        decisions = center.db.current_decisions(limit=500) or center.refresh_decisions()
        payload = {
            "health": "ok" if not errors else "degraded",
            "read_only": True,
            "wb_started": wb_started,
            "errors": errors,
            "query_manager": qm,
            "decisions": decisions,
            "events": center.db.recent_events(hours=48, limit=500),
            "runs": run_results,
            "portfolio": portfolio.snapshot(),
        }
        dump("latest.json", payload)
        dump("operator-brief.json", qm.get("operator_brief") or {})
        dump("query-manager.json", qm)
        dump("portfolio.json", portfolio.snapshot())
        print(json.dumps({
            "health": payload["health"],
            "query_rows": (qm.get("summary") or {}).get("query_rows"),
            "ready_query_actions": (qm.get("summary") or {}).get("ready_query_actions"),
            "data_blockers": (qm.get("summary") or {}).get("data_blockers"),
            "decisions": len(decisions),
            "errors": errors,
        }, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        errors.append({"stage": "runtime", "error": str(exc)})
        dump("failure.json", {"errors": errors, "traceback": traceback.format_exc()})
        raise
    finally:
        try:
            await center.wb.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
