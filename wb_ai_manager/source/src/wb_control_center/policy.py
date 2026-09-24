from __future__ import annotations

from typing import Any
import math

from .config import PolicyConfig, Settings
from .models import ActionProposal


MONEY_WRITE_TOOLS = {
    "wb_advert_bids_set",
    "wb_advert_cluster_bids",
    "wb_advert_deposit",
    "wb_prices_set",
}

READ_ONLY_BUILD = True


IRREVERSIBLE_TOOLS = {
    "wb_advert_stop",
    "wb_advert_delete",
    "wb_cards_move_to_trash",
    "wb_supply_deliver",
    "wb_order_cancel",
    "wb_stocks_delete",
    "wb_warehouse_delete",
}


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out=float(value)
    return out if math.isfinite(out) else None


class PolicyEngine:
    """Deterministic safety boundary between any agent/LLM and Wildberries writes.

    The model may *propose* an action. This class decides whether the proposal may
    even enter the approval queue and whether it may execute. Hard limits are
    intentionally independent of the model and cannot be overridden by prompt text.
    """

    def __init__(self, settings: Settings, policy: PolicyConfig):
        self.settings = settings
        self.policy = policy

    @property
    def operation_mode(self) -> str:
        mode = getattr(self.settings, "operation_mode", "shadow").lower().strip()
        return mode if mode in {"shadow", "approval", "guarded_auto"} else "shadow"

    def can_auto_execute(self, action: ActionProposal) -> bool:
        if READ_ONLY_BUILD:
            return False
        if getattr(self.settings, "force_read_only", True):
            return False
        if self.operation_mode != "guarded_auto":
            return False
        safety = self.policy.safety
        if action.tool in set(safety.get("always_require_approval", [])):
            return False
        if action.tool in IRREVERSIBLE_TOOLS:
            return False
        if action.risk in {"high", "irreversible"}:
            return False
        return action.tool in set(safety.get("auto_execute_tools", []))

    def execution_allowed(self) -> tuple[bool, str]:
        if READ_ONLY_BUILD:
            return False, "read-only build fuse: WB writes are disabled in this release"
        if getattr(self.settings, "force_read_only", True):
            return False, "read-only safety fuse: WB writes are disabled in this build"
        if self.operation_mode == "shadow":
            return False, "shadow mode: writes are simulated only"
        return True, "ok"

    def _evidence_ok(self, action: ActionProposal) -> tuple[bool, str]:
        if action.tool not in MONEY_WRITE_TOOLS:
            return True, "ok"
        evidence = action.arguments.get("_evidence")
        if not isinstance(evidence, dict):
            return False, "money-changing action has no evidence packet"
        sources = evidence.get("sources")
        facts = evidence.get("facts")
        if not isinstance(sources, list) or not sources:
            return False, "money-changing action has no source references"
        if not isinstance(facts, dict) or not facts:
            return False, "money-changing action has no measured facts"
        return True, "ok"

    def validate_action(self, action: ActionProposal) -> tuple[bool, str]:
        limits: dict[str, Any] = self.policy.safety.get("limits", {})

        ok, why = self._evidence_ok(action)
        if not ok:
            return ok, why

        if action.tool == "wb_advert_bids_set":
            cap_rub = float(limits.get("absolute_bid_cap_rub", 1000))
            max_change = float(limits.get("max_bid_change_pct", 10))
            bids = action.arguments.get("bids")
            if not isinstance(bids, list) or not bids:
                return False, "bid action has no bids array"
            for campaign in bids:
                if not isinstance(campaign, dict):
                    return False, "invalid bid payload"
                nm_bids = campaign.get("nm_bids")
                if not isinstance(nm_bids, list) or not nm_bids:
                    return False, "bid action has no nm_bids"
                for row in nm_bids:
                    if not isinstance(row, dict):
                        return False, "invalid nm bid payload"
                    raw = _finite_number(row.get("bid_kopecks"))
                    if raw is None:
                        return False, "bid_kopecks must be a finite numeric value"
                    if raw < 0:
                        return False, "negative bid is forbidden"
                    rub = raw / 100.0
                    if rub > cap_rub:
                        return False, f"bid {rub:.2f} RUB exceeds absolute cap {cap_rub:.2f} RUB"
            change = _finite_number(action.arguments.get("_change_pct"))
            if change is None:
                return False, "bid change requires a finite numeric _change_pct"
            if abs(change) > max_change:
                return False, f"bid change {change:.2f}% exceeds hard cap {max_change:.2f}%"

        if action.tool == "wb_advert_cluster_bids":
            cap_rub = float(limits.get("absolute_cluster_bid_cap_rub", 1000))
            max_change = float(limits.get("max_bid_change_pct", 10))
            bids = action.arguments.get("bids")
            if not isinstance(bids, list) or not bids:
                return False, "cluster bid action has no bids array"
            for row in bids:
                if not isinstance(row, dict):
                    return False, "invalid cluster bid payload"
                raw = _finite_number(row.get("bid"))
                if raw is None:
                    return False, "cluster bid must be a finite numeric RUB/1000 views"
                if raw < 0:
                    return False, "negative cluster bid is forbidden"
                if raw > cap_rub:
                    return False, f"cluster bid {raw:.2f} RUB exceeds absolute cap {cap_rub:.2f} RUB"
            change = _finite_number(action.arguments.get("_change_pct"))
            if change is None:
                return False, "cluster bid change requires a finite numeric _change_pct"
            if abs(change) > max_change:
                return False, f"cluster bid change {change:.2f}% exceeds hard cap {max_change:.2f}%"

        if action.tool == "wb_advert_deposit":
            amount = _finite_number(action.arguments.get("amount"))
            cap = float(limits.get("max_single_ad_deposit_rub", 5000))
            if amount is None:
                return False, "deposit amount must be a finite numeric value"
            if amount <= 0 or amount > cap:
                return False, f"deposit {amount} RUB exceeds single-operation cap {cap:.2f} RUB"

        if action.tool == "wb_prices_set":
            change = _finite_number(action.arguments.get("_change_pct"))
            max_change = float(limits.get("max_price_change_pct", 5))
            if change is None:
                return False, "price change requires a finite numeric _change_pct"
            if abs(change) > max_change:
                return False, f"price change {change:.2f}% exceeds hard cap {max_change:.2f}%"

        if action.tool in IRREVERSIBLE_TOOLS and self.operation_mode == "guarded_auto":
            # It may still be staged and manually approved; it just can never be auto-executed.
            return True, "manual approval required"

        return True, "ok"
