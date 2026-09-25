from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .base import BaseAgent
from ..metrics import extract_ad_metrics, extract_ad_nm_metrics, extract_campaign_ids, summarize_for_prompt, to_number
from ..models import AgentResult


def _rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if not isinstance(value, dict):
        return []
    for key in ("campaigns", "adverts", "data", "items"):
        v = value.get(key)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
    return []


def _campaign_id(row: dict[str, Any]) -> int | None:
    for key in ("advertId", "advert_id", "id"):
        n = to_number(row.get(key))
        if n and n > 0:
            return int(n)
    return None


def _nm_ids(row: dict[str, Any]) -> list[int]:
    ids: set[int] = set()
    def walk(x: Any) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                lk = str(k).lower()
                if lk in {"nmid", "nm_id", "nm"}:
                    n = to_number(v)
                    if n and n > 0: ids.add(int(n))
                elif lk in {"nmids", "nm_ids", "nms"} and isinstance(v, list):
                    for item in v:
                        n = to_number(item)
                        if n and n > 0: ids.add(int(n))
                walk(v)
        elif isinstance(x, list):
            for item in x: walk(item)
    walk(row)
    return sorted(ids)


class AdvertisingMonitorAgent(BaseAgent):
    name = "advertising_monitor"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        cfg = self.ctx.policy.thresholds.get("advertising", {})
        campaigns = await self.call("wb_advert_list", statuses=[9])
        out.snapshots.append(("active_campaigns", campaigns if isinstance(campaigns, dict) else {"data": campaigns}))
        ids = extract_campaign_ids(campaigns)
        if not ids:
            return out
        start, end = self.dates(7)
        stats = await self.call("wb_advert_stats", advert_ids=ids, date_from=start, date_to=end)
        out.snapshots.append(("stats_7d", stats if isinstance(stats, dict) else {"data": stats}))
        rows = extract_ad_metrics(stats)
        warn = float(cfg.get("warn_drr_pct", 12))
        critical = float(cfg.get("critical_drr_pct", 20))
        min_spend = float(cfg.get("min_spend_for_drr_rub", 1000))
        zero_orders_spend = float(cfg.get("zero_orders_spend_rub", 1500))
        for r in rows:
            aid = r.get("advert_id")
            spend = float(r.get("spend") or 0)
            orders = float(r.get("orders") or 0)
            drr = r.get("drr_pct")
            if spend >= zero_orders_spend and orders <= 0:
                out.events.append(self.event("critical", f"ad_zero_orders:{aid}", f"Реклама #{aid}: расход без заказов", f"Расход около {spend:.0f} ₽, заказов 0 за анализируемый период.", r))
            if drr is not None and spend >= min_spend and float(drr) >= critical:
                out.events.append(self.event("critical", f"ad_drr_critical:{aid}", f"Реклама #{aid}: критический ДРР", f"ДРР ≈ {float(drr):.1f}% при расходе {spend:.0f} ₽.", r))
            elif drr is not None and spend >= min_spend and float(drr) >= warn:
                out.events.append(self.event("warning", f"ad_drr_warn:{aid}", f"Реклама #{aid}: повышенный ДРР", f"ДРР ≈ {float(drr):.1f}% при расходе {spend:.0f} ₽.", r))

        if self.ctx.llm.enabled and rows:
            text = await self.ctx.llm.complete(
                "Ты рекламный аналитик Wildberries. Не придумывай цифры. Найди только существенные аномалии и причины, максимум 6 пунктов.",
                summarize_for_prompt({"campaigns": campaigns, "stats": stats}),
                max_tokens=700,
            )
            if text:
                out.events.append(self.event("info", "ad_llm_analysis", "AI-разбор рекламы", text, {"campaign_ids": ids}))
        return out


class AdvertisingOptimizerAgent(BaseAgent):
    """Deep read-only scan used by the numerical advertising controller.

    No write proposal is created here. The agent collects the exact inputs needed to
    calculate a bid/spend plan: campaign settings, 14d stats, budget, WB bid guidance
    and search-cluster performance. DecisionEngine turns them into a numerical plan.
    """

    name = "advertising_optimizer"

    async def _safe(self, tool: str, **kwargs: Any) -> Any:
        try:
            return await self.call(tool, **kwargs)
        except Exception as exc:
            return {"_error": str(exc), "_tool": tool}

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        campaigns_raw = await self._safe("wb_advert_list", statuses=[9])
        campaigns = _rows(campaigns_raw)
        ids = [x for x in (_campaign_id(c) for c in campaigns) if x]
        if not ids:
            out.snapshots.append(("deep_scan", {"campaigns": [], "note": "no active campaigns"}))
            return out

        start, end = self.dates(14)
        stats_raw = await self._safe("wb_advert_stats", advert_ids=ids, date_from=start, date_to=end)
        stat_rows = _rows(stats_raw)
        if not stat_rows and isinstance(stats_raw, list):
            stat_rows = [x for x in stats_raw if isinstance(x, dict)]
        stats_by_id = {str(_campaign_id(x) or ""): x for x in stat_rows}
        exact_nm_stats = extract_ad_nm_metrics(stats_raw)

        # Deep scan highest-spend campaigns first to keep API load bounded.
        def spend(c: dict[str, Any]) -> float:
            row = stats_by_id.get(str(_campaign_id(c) or ""), {})
            return float(to_number(row.get("sum") or row.get("spend") or row.get("cost")) or 0)
        campaigns = sorted(campaigns, key=spend, reverse=True)
        runtime = (self.ctx.policy.raw.get("runtime") or {}).get("advertising", {})
        max_campaigns = int(runtime.get("max_campaigns_deep_scan", 8) or 8)
        scanned: list[dict[str, Any]] = []
        for c in campaigns[:max_campaigns]:
            cid = _campaign_id(c)
            if not cid:
                continue
            nms = _nm_ids(c)
            row: dict[str, Any] = {
                "campaign": c,
                "campaign_id": cid,
                "nm_ids": nms,
                "stats": stats_by_id.get(str(cid), {}),
                "stats_by_nm": {str(nm): exact_nm_stats[(cid, nm)] for nm in nms if (cid, nm) in exact_nm_stats},
                "budget": await self._safe("wb_advert_budget", advert_id=cid),
            }
            recommendations: dict[str, Any] = {}
            cluster_stats: dict[str, Any] = {}
            clusters: dict[str, Any] = {}
            # Bid guidance is SKU-specific. Scan several SKUs, but keep API load bounded.
            max_nms = int(runtime.get("max_nms_per_campaign", 8) or 8)
            for nm in nms[:max_nms]:
                recommendations[str(nm)] = await self._safe("wb_advert_bids_recommendations", nm_id=nm, advert_id=cid)
            if nms:
                clusters = await self._safe("wb_advert_clusters", advert_id=cid)
                cluster_stats = await self._safe(
                    "wb_advert_clusters_stats", advert_id=cid, date_from=start, date_to=end, nm_ids=nms[:10], daily=True
                )
            row["recommendations"] = recommendations
            row["clusters"] = clusters
            row["cluster_stats"] = cluster_stats
            scanned.append(row)

        out.snapshots.append(("deep_scan", {"generated_for": {"from": start, "to": end}, "campaigns": scanned}))
        out.snapshots.append(("stats_14d", stats_raw if isinstance(stats_raw, dict) else {"data": stats_raw}))
        return out
