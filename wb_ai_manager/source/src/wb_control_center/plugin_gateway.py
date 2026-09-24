from __future__ import annotations

"""ChatGPT/Codex-facing MCP gateway.

This module intentionally exposes high-level read/stage tools rather than the raw
Wildberries write surface. The LLM never receives a Seller API token and never gets
an unrestricted bid/price endpoint.
"""

from datetime import datetime, timedelta, timezone
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import Settings
from .engine import ControlCenter
from .metrics import extract_ad_metrics, extract_campaign_ids
from .models import ActionProposal

settings = Settings()
center = ControlCenter(settings)
mcp = FastMCP("WB AI Manager", stateless_http=True, json_response=True)
_started = False


async def _ensure_started() -> None:
    global _started
    if not _started:
        await center.start()
        _started = True


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@mcp.tool(description="Get WB AI Manager health, operation mode, agents and pending staged changes.")
async def get_account_status() -> dict[str, Any]:
    await _ensure_started()
    return {
        "status": "ok",
        "operation_mode": center.policy_engine.operation_mode,
        "wb_mode": settings.wb_mode,
        "agents": sorted(center.agents),
        "pending_changes": len(center.db.pending_actions(limit=1000)),
        "writes_reach_wb": center.policy_engine.execution_allowed()[0],
        "timestamp": _utcnow(),
    }


@mcp.tool(description="Run the full deterministic WB audit and return only the summarized run results. No writes are executed by this tool.")
async def run_full_audit() -> list[dict[str, Any]]:
    await _ensure_started()
    return await center.run_all_once()


@mcp.tool(description="Get recent WB AI Manager incidents/events with source payloads. Read-only.")
async def get_recent_incidents(hours: int = 24, limit: int = 100) -> list[dict[str, Any]]:
    await _ensure_started()
    return center.db.recent_events(hours=max(1, min(hours, 720)), limit=max(1, min(limit, 500)))


@mcp.tool(description="Get staged changes waiting for human approval. Read-only.")
async def get_pending_changes(limit: int = 100) -> list[dict[str, Any]]:
    await _ensure_started()
    return center.db.pending_actions(limit=max(1, min(limit, 500)))


@mcp.tool(description="Read live advertising campaigns and recent performance from Wildberries. Read-only; does not change bids or budgets.")
async def get_advertising_overview(days: int = 7) -> dict[str, Any]:
    await _ensure_started()
    days = max(1, min(days, 31))
    campaigns = await center.wb.call("wb_advert_list", {"statuses": [9]})
    ids = extract_campaign_ids(campaigns)
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days - 1)
    stats: Any = {"data": []}
    if ids:
        stats = await center.wb.call(
            "wb_advert_stats",
            {"advert_ids": ids, "date_from": start.isoformat(), "date_to": end.isoformat()},
        )
    return {
        "timestamp": _utcnow(),
        "window": {"date_from": start.isoformat(), "date_to": end.isoformat()},
        "campaign_ids": ids,
        "metrics": extract_ad_metrics(stats),
        "source": "Wildberries Seller API via internal WB connector",
    }


@mcp.tool(description="Stage a reversible pause for a live WB advertising campaign. This creates a preview only; it does not apply the pause.")
async def stage_campaign_pause(campaign_id: int, reason: str) -> dict[str, Any]:
    await _ensure_started()
    campaigns = await center.wb.call("wb_advert_list", {"ids": [int(campaign_id)]})
    ids = extract_campaign_ids(campaigns)
    if int(campaign_id) not in ids:
        return {"status": "blocked", "reason": "campaign_id was not returned by the live tenant campaign read"}

    proposal = ActionProposal(
        agent="chatgpt_plugin",
        tool="wb_advert_pause",
        arguments={
            "advert_id": int(campaign_id),
            "_decision_card": {
                "kind": "campaign_pause",
                "before": "active",
                "after": "paused",
                "sources": [{"name": "wb_advert_list", "read_at": _utcnow()}],
                "reason": reason,
                "rollback": "wb_advert_start after a fresh live read and approval",
            },
        },
        reason=reason,
        risk="medium",
    )
    ok, why = center.policy_engine.validate_action(proposal)
    if not ok:
        return {"status": "blocked", "reason": why}
    action_id = center.db.create_action(proposal)
    return {
        "status": "staged",
        "change_id": action_id,
        "preview": {"campaign_id": campaign_id, "before": "active", "after": "paused"},
        "note": "No Wildberries write has been executed. Approval/apply is separate.",
    }


@mcp.tool(description="Check a proposed campaign-level bid against backend hard limits without applying it. Values are semantic RUB; raw WB units are never accepted here.")
async def validate_bid_proposal(current_bid_rub: float, proposed_bid_rub: float) -> dict[str, Any]:
    await _ensure_started()
    if current_bid_rub <= 0 or proposed_bid_rub < 0:
        return {"status": "blocked", "reason": "bids must be non-negative and current bid must be positive"}
    change_pct = (proposed_bid_rub / current_bid_rub - 1.0) * 100.0
    synthetic = ActionProposal(
        agent="chatgpt_plugin",
        tool="wb_advert_bids_set",
        arguments={
            "bids": [{"advert_id": 1, "nm_bids": [{"nm_id": 1, "bid_kopecks": round(proposed_bid_rub * 100), "placement": "combined"}]}],
            "_change_pct": change_pct,
            "_evidence": {
                "sources": [{"name": "proposal_validation_only", "read_at": _utcnow()}],
                "facts": {"current_bid_rub": current_bid_rub, "proposed_bid_rub": proposed_bid_rub},
            },
        },
        reason="proposal validation only",
        risk="medium",
    )
    ok, why = center.policy_engine.validate_action(synthetic)
    return {
        "status": "allowed" if ok else "blocked",
        "reason": why,
        "current_bid_rub": current_bid_rub,
        "proposed_bid_rub": proposed_bid_rub,
        "change_pct": round(change_pct, 3),
        "note": "Validation only. This tool cannot write a bid to Wildberries.",
    }


def main() -> None:
    transport = os.getenv("WB_AI_MCP_TRANSPORT", "stdio").strip().lower()
    if transport in {"http", "streamable-http"}:
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
