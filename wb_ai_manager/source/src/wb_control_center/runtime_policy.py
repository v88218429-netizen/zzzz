from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from typing import Any


EDITABLE_DEFAULTS = {
    "strategy_mode": "balanced",
    "target_drr_pct": 100.0,  # 100 = не ограничивать сверх потолка, рассчитанного из юнит-экономики
    "min_profit_after_ads_rub": 0.0,  # жёсткий минимум по умолчанию: не уходить в минус
    "min_clicks_for_numeric_ad_decision": 50,
    "min_orders_for_scale_up": 5,
    "max_bid_step_pct": 7.0,
    "max_spend_step_pct": 12.0,
    "evaluation_window_hours": 3.0,
    "min_stock_days_for_scale": 10.0,
    "min_stock_days_for_hold": 7.0,
    "target_stock_days": 14.0,
    "trend_short_days": 3,
    "trend_long_days": 14,
    "max_trend_pct_for_forecast": 60.0,
    "organic_growth_hold_threshold_pct": 20.0,
    "weak_ctr_change_pct": -20.0,
    "weak_cr_change_pct": -20.0,
    "cpc_growth_warn_pct": 35.0,
    "zero_orders_spend_rub": 1500.0,
    "max_internal_spend_24h_rub": 20000.0,
}

ALLOWED_MODES = {"growth", "balanced", "profit"}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class RuntimePolicyStore:
    """Hot-reloadable operator policy stored in SQLite KV.

    This is intentionally separate from the safety policy. The operator may tune
    strategy live, but cannot raise hard write caps or disable read-only fuses here.
    """

    KV_KEY = "runtime_policy_v1"
    HISTORY_KEY = "runtime_policy_history_v1"

    def __init__(self, db: Any, policy: Any, remote: Any | None = None):
        self.db = db
        self.policy = policy
        self.remote = remote
        self._apply_saved()

    def _remote_payload(self) -> dict[str, Any]:
        if self.remote is None:
            return {}
        try:
            data = self.remote.get()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _remote_advertising(self) -> dict[str, Any]:
        data = self._remote_payload().get("advertising", {})
        return data if isinstance(data, dict) else {}

    def _read_json(self, key: str, default: Any) -> Any:
        raw = self.db.get_kv(key)
        if not raw:
            return copy.deepcopy(default)
        try:
            return json.loads(raw)
        except Exception:
            return copy.deepcopy(default)

    def _saved(self) -> dict[str, Any]:
        data = self._read_json(self.KV_KEY, {})
        return data if isinstance(data, dict) else {}

    def _apply_saved(self) -> None:
        saved = self._saved()
        base = _deep_merge(EDITABLE_DEFAULTS, self._remote_advertising())
        runtime = _deep_merge(base, saved.get("advertising", {}))
        rt = self.policy.raw.setdefault("runtime", {})
        rt["advertising"] = runtime
        remote_payload = self._remote_payload()
        rt["group_overrides"] = copy.deepcopy(remote_payload.get("group_overrides", {})) if isinstance(remote_payload.get("group_overrides", {}), dict) else {}
        rt["sku_overrides"] = copy.deepcopy(remote_payload.get("sku_overrides", {})) if isinstance(remote_payload.get("sku_overrides", {}), dict) else {}
        self.policy.raw["runtime"]["updated_at"] = saved.get("updated_at")
        self.policy.raw["runtime"]["updated_by"] = saved.get("updated_by")
        remote = self._remote_payload()
        self.policy.raw["runtime"]["remote_version"] = remote.get("version")
        self.policy.raw["runtime"]["remote_updated_at"] = remote.get("updated_at")

    def get(self) -> dict[str, Any]:
        self._apply_saved()
        return copy.deepcopy(self.policy.raw.get("runtime", {}))

    def advertising(self) -> dict[str, Any]:
        return self.get().get("advertising", copy.deepcopy(EDITABLE_DEFAULTS))

    def remote_status(self) -> dict[str, Any]:
        if self.remote is None:
            return {"enabled": False}
        try:
            return self.remote.status()
        except Exception as exc:
            return {"enabled": True, "error": str(exc)}

    def _validate_advertising(self, incoming: dict[str, Any]) -> dict[str, Any]:
        current = self.advertising()
        out = _deep_merge(current, incoming)
        if out.get("strategy_mode") not in ALLOWED_MODES:
            raise ValueError("strategy_mode must be growth, balanced or profit")

        numeric_bounds = {
            "target_drr_pct": (0, 100),
            "min_profit_after_ads_rub": (-100000, 1000000),
            "min_clicks_for_numeric_ad_decision": (1, 1000000),
            "min_orders_for_scale_up": (1, 1000000),
            "max_bid_step_pct": (0, 10),  # never exceed hard safety cap in policies.yaml
            "max_spend_step_pct": (0, 50),
            "evaluation_window_hours": (0.25, 168),
            "min_stock_days_for_scale": (0, 365),
            "min_stock_days_for_hold": (0, 365),
            "target_stock_days": (1, 365),
            "trend_short_days": (1, 30),
            "trend_long_days": (2, 120),
            "max_trend_pct_for_forecast": (0, 500),
            "organic_growth_hold_threshold_pct": (0, 500),
            "weak_ctr_change_pct": (-100, 100),
            "weak_cr_change_pct": (-100, 100),
            "cpc_growth_warn_pct": (0, 500),
            "zero_orders_spend_rub": (0, 10000000),
            "max_internal_spend_24h_rub": (0, 10000000),
        }
        for key, (lo, hi) in numeric_bounds.items():
            try:
                val = float(out[key])
            except Exception as exc:
                raise ValueError(f"{key} must be numeric") from exc
            if not lo <= val <= hi:
                raise ValueError(f"{key} must be between {lo} and {hi}")
            if key in {"min_clicks_for_numeric_ad_decision", "min_orders_for_scale_up", "trend_short_days", "trend_long_days"}:
                out[key] = int(round(val))
            else:
                out[key] = val
        if out["trend_long_days"] <= out["trend_short_days"]:
            raise ValueError("trend_long_days must be greater than trend_short_days")
        if out["target_stock_days"] < out["min_stock_days_for_hold"]:
            raise ValueError("target_stock_days must be >= min_stock_days_for_hold")
        if out["min_stock_days_for_scale"] < out["min_stock_days_for_hold"]:
            raise ValueError("min_stock_days_for_scale must be >= min_stock_days_for_hold")
        return out

    def update_advertising(self, incoming: dict[str, Any], updated_by: str = "operator") -> dict[str, Any]:
        validated = self._validate_advertising(incoming)
        now = datetime.now(timezone.utc).isoformat()
        old = self.advertising()
        payload = {"advertising": validated, "updated_at": now, "updated_by": updated_by}
        self.db.set_kv(self.KV_KEY, json.dumps(payload, ensure_ascii=False))
        history = self._read_json(self.HISTORY_KEY, [])
        if not isinstance(history, list):
            history = []
        history.insert(0, {"at": now, "by": updated_by, "before": old, "after": validated})
        self.db.set_kv(self.HISTORY_KEY, json.dumps(history[:50], ensure_ascii=False))
        self._apply_saved()
        return self.get()

    def history(self) -> list[dict[str, Any]]:
        h = self._read_json(self.HISTORY_KEY, [])
        return h if isinstance(h, list) else []

    def rollback(self, index: int = 0) -> dict[str, Any]:
        history = self.history()
        if index < 0 or index >= len(history):
            raise ValueError("history index out of range")
        before = history[index].get("before")
        if not isinstance(before, dict):
            raise ValueError("history entry has no rollback state")
        return self.update_advertising(before, updated_by="rollback")
