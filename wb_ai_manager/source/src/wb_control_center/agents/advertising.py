from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
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


def _deep_scan_limits(runtime: dict[str, Any]) -> tuple[int, int, int]:
    """Hard-bound supplementary WB API calls per run."""
    def bounded(name: str, default: int, ceiling: int) -> int:
        try:
            value = int(runtime.get(name, default) or default)
        except (TypeError, ValueError):
            value = default
        return max(1, min(ceiling, value))

    return (
        bounded("max_campaigns_deep_scan", 2, 2),
        bounded("max_nms_per_campaign", 2, 2),
        bounded("deep_scan_concurrency", 2, 2),
    )


def _rotating_campaign_window(items: list[dict[str, Any]], limit: int, window: int) -> list[dict[str, Any]]:
    if not items or limit <= 0:
        return []
    count = min(limit, len(items))
    start = (max(0, int(window)) * count) % len(items)
    return (items + items)[start : start + count]


def _rotating_nm_window(items: list[int], limit: int, window: int, campaign_id: int) -> list[int]:
    """Rotate product recommendations within each campaign without raising WB call volume."""
    if not items or limit <= 0:
        return []
    count = min(limit, len(items))
    start = ((max(0, int(window)) + max(0, int(campaign_id))) * count) % len(items)
    doubled = items + items
    return doubled[start : start + count]


def _scan_cursor(previous: Any, campaign_ids: list[int]) -> dict[str, Any]:
    """Resume a fair per-cabinet sweep; reset only when the campaign catalog changes."""
    saved = previous.get("data") if isinstance(previous, dict) else previous
    saved = saved if isinstance(saved, dict) else {}
    expected = [int(x) for x in campaign_ids]
    if saved.get("campaign_ids") != expected:
        return {"campaign_ids": expected, "next_campaign_index": 0, "next_nm_index": {},
                "sweep_id": int(saved.get("sweep_id") or 0) + 1,
                "swept_campaign_ids": [], "swept_nm_ids": {}}
    return {
        "campaign_ids": expected,
        "next_campaign_index": max(0, int(saved.get("next_campaign_index") or 0)) % max(1, len(expected)),
        "next_nm_index": dict(saved.get("next_nm_index") or {}),
        "sweep_id": max(1, int(saved.get("sweep_id") or 1)),
        "swept_campaign_ids": [int(x) for x in saved.get("swept_campaign_ids") or [] if str(x).isdigit()],
        "swept_nm_ids": {str(k): [int(x) for x in v if str(x).isdigit()]
                          for k, v in (saved.get("swept_nm_ids") or {}).items() if isinstance(v, list)},
    }


def _rotating_slice(items: list[Any], limit: int, start: int) -> list[Any]:
    if not items or limit <= 0:
        return []
    count = min(limit, len(items))
    start = max(0, int(start)) % len(items)
    return (items + items)[start:start + count]


class AdvertisingMonitorAgent(BaseAgent):
    name = "advertising_monitor"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        cfg = self.ctx.policy.thresholds.get("advertising", {})
        campaigns = await self.call("wb_advert_list", statuses=[9])
        out.snapshots.append(("active_campaigns", campaigns if isinstance(campaigns, dict) else {"data": campaigns}))
        campaign_rows = _rows(campaigns)
        campaign_by_id = {cid: row for row in campaign_rows if (cid := _campaign_id(row))}
        ids = sorted(campaign_by_id)
        if not ids:
            return out
        start, end = self.dates(7)
        stats = await self.call("wb_advert_stats", advert_ids=ids, date_from=start, date_to=end)
        out.snapshots.append(("stats_7d", stats if isinstance(stats, dict) else {"data": stats}))
        # Fullstats contains nested day/app/SKU rows with spend but no campaign id.
        # Those are components of a campaign, not independent campaigns.
        rows = [r for r in extract_ad_metrics(stats) if r.get("advert_id") is not None]
        warn = float(cfg.get("warn_drr_pct", 12))
        critical = float(cfg.get("critical_drr_pct", 20))
        min_spend = float(cfg.get("min_spend_for_drr_rub", 1000))
        zero_orders_spend = float(cfg.get("zero_orders_spend_rub", 1500))
        for r in rows:
            aid = int(r.get("advert_id"))
            spend = float(r.get("spend") or 0)
            orders = float(r.get("orders") or 0)
            drr = r.get("drr_pct")
            campaign = campaign_by_id.get(aid) or {}
            payload = dict(r)
            payload["nm_ids"] = _nm_ids(campaign)
            payload["campaign_name"] = str((campaign.get("settings") or {}).get("name") or "").strip()
            campaign_label = payload["campaign_name"] or f"кампания {aid}"
            if spend >= zero_orders_spend and orders <= 0:
                out.events.append(self.event("critical", f"ad_zero_orders:{aid}", "Расход рекламы без заказов", f"{campaign_label}: расход около {spend:.0f} ₽, заказов 0 за анализируемый период.", payload))
            if drr is not None and spend >= min_spend and float(drr) >= critical:
                out.events.append(self.event("critical", f"ad_drr_critical:{aid}", "Критический ДРР рекламы", f"{campaign_label}: ДРР ≈ {float(drr):.1f}% при расходе {spend:.0f} ₽.", payload))
            elif drr is not None and spend >= min_spend and float(drr) >= warn:
                out.events.append(self.event("warning", f"ad_drr_warn:{aid}", "Повышенный ДРР рекламы", f"{campaign_label}: ДРР ≈ {float(drr):.1f}% при расходе {spend:.0f} ₽.", payload))

        if self.ctx.llm.enabled and rows:
            text = await self.ctx.llm.complete(
                "Ты агент ФАКТА рекламы Wildberries. Не называй рекламный ДРР плановым, целевым или прибылью: это только наблюдаемый рекламный расход/результат за окно. Ищи существенные аномалии максимум в 6 пунктах. Финальный денежный вывод делает центральный движок после сверки с PRIMARY-юниткой «Юнит-экономика вб / WB FBS новая» и финансовым фактом. Не придумывай отсутствующие цифры.",
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

        # Stable ID ordering plus a saved cursor prevents high-spend campaigns
        # from starving the rest of a cabinet during repeated bounded runs.
        campaigns = sorted(campaigns, key=lambda c: _campaign_id(c) or 0)
        runtime = (self.ctx.policy.raw.get("runtime") or {}).get("advertising", {})
        max_campaigns, max_nms, concurrency = _deep_scan_limits(runtime)
        old_cursor = self.ctx.db.latest_snapshot(self.name, "deep_scan_cursor")
        cursor = _scan_cursor(old_cursor, [_campaign_id(c) for c in campaigns if _campaign_id(c)])
        semaphore = asyncio.Semaphore(concurrency)

        async def limited(tool: str, **kwargs: Any) -> Any:
            async with semaphore:
                return await self._safe(tool, **kwargs)

        async def scan_campaign(campaign: dict[str, Any]) -> dict[str, Any] | None:
            cid = _campaign_id(campaign)
            if not cid:
                return None
            nms = _nm_ids(campaign)
            nm_start = max(0, int(cursor["next_nm_index"].get(str(cid), 0)))
            selected_nms = _rotating_slice(nms, max_nms, nm_start)
            budget_task = asyncio.create_task(limited("wb_advert_budget", advert_id=cid))
            rec_tasks = {
                str(nm): asyncio.create_task(
                    limited("wb_advert_bids_recommendations", nm_id=nm, advert_id=cid)
                )
                for nm in selected_nms
            }
            clusters_task = (
                asyncio.create_task(
                    limited("wb_advert_clusters", advert_id=cid, nm_ids=selected_nms)
                )
                if selected_nms else None
            )
            cluster_stats_task = (
                asyncio.create_task(
                    limited(
                        "wb_advert_clusters_stats",
                        advert_id=cid,
                        date_from=start,
                        date_to=end,
                        nm_ids=selected_nms,
                        daily=True,
                    )
                )
                if nms else None
            )
            budget = await budget_task
            recommendations = {
                nm: await task
                for nm, task in rec_tasks.items()
            }
            clusters = await clusters_task if clusters_task else {}
            cluster_stats = await cluster_stats_task if cluster_stats_task else {}
            responses = [budget, clusters, cluster_stats, *recommendations.values()]
            scan_complete = bool(responses) and all(
                not (isinstance(value, dict) and value.get("_error")) for value in responses
            )
            return {
                "campaign": campaign,
                "campaign_id": cid,
                "nm_ids": nms,
                "recommendation_nm_ids_scanned": selected_nms,
                "stats": stats_by_id.get(str(cid), {}),
                "stats_by_nm": {
                    str(nm): exact_nm_stats[(cid, nm)]
                    for nm in nms if (cid, nm) in exact_nm_stats
                },
                "budget": budget,
                "recommendations": recommendations,
                "clusters": clusters,
                "cluster_stats": cluster_stats,
                "scan_complete": scan_complete,
            }

        before = int(cursor["next_campaign_index"])
        selected_campaigns = _rotating_slice(campaigns, max_campaigns, before)
        cursor["next_campaign_index"] = (before + len(selected_campaigns)) % max(1, len(campaigns))
        results = await asyncio.gather(
            *(scan_campaign(c) for c in selected_campaigns),
            return_exceptions=True,
        )
        scanned: list[dict[str, Any]] = []
        for campaign, result in zip(selected_campaigns, results):
            if isinstance(result, BaseException):
                scanned.append({
                    "campaign": campaign,
                    "campaign_id": _campaign_id(campaign),
                    "nm_ids": _nm_ids(campaign),
                    "recommendation_nm_ids_scanned": [],
                    "_error": str(result),
                })
            elif result is not None:
                cid = _campaign_id(campaign)
                scanned_nms = result.get("recommendation_nm_ids_scanned") or []
                nms = result.get("nm_ids") or []
                if cid:
                    cursor["next_nm_index"][str(cid)] = (max(0, int(cursor["next_nm_index"].get(str(cid), 0))) + len(scanned_nms)) % max(1, len(nms))
                    if result.get("scan_complete") is True:
                        cursor["swept_campaign_ids"] = sorted(set(cursor["swept_campaign_ids"] + [cid]))
                        done = set(cursor["swept_nm_ids"].get(str(cid), [])) | {int(x) for x in scanned_nms}
                        cursor["swept_nm_ids"][str(cid)] = sorted(done)
                scanned.append(result)

        all_products_seen = all(
            set(cursor["swept_nm_ids"].get(str(_campaign_id(c)), [])) >= set(_nm_ids(c))
            for c in campaigns if _campaign_id(c)
        )
        swept_campaign_count = len(cursor["swept_campaign_ids"])
        swept_product_count = sum(len(x) for x in cursor["swept_nm_ids"].values())
        sweep_complete = (set(cursor["swept_campaign_ids"]) >= set(cursor["campaign_ids"])
                          and all_products_seen
                          and all(isinstance(item, dict) and item.get("scan_complete") is True for item in scanned))
        if sweep_complete:
            cursor["sweep_id"] += 1
            cursor["swept_campaign_ids"] = []
            cursor["swept_nm_ids"] = {}

        out.snapshots.append(("deep_scan", {
            "generated_for": {"from": start, "to": end},
            "sweep_id": cursor["sweep_id"],
            "sweep_complete": sweep_complete,
            "sweep_campaigns_seen": swept_campaign_count,
            "sweep_campaigns_total": len(cursor["campaign_ids"]),
            "sweep_products_seen": swept_product_count,
            "sweep_products_total": sum(len(_nm_ids(c)) for c in campaigns),
            "cursor_before": before,
            "cursor_after": cursor["next_campaign_index"],
            "eligible_campaigns": len(campaigns),
            "campaigns_scanned": len(scanned),
            "hard_limits": {"campaigns": max_campaigns, "nms_per_campaign": max_nms, "concurrent_calls": concurrency},
            "campaigns": scanned,
        }))
        out.snapshots.append(("deep_scan_cursor", cursor))
        out.snapshots.append(("stats_14d", stats_raw if isinstance(stats_raw, dict) else {"data": stats_raw}))
        return out
