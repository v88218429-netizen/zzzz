from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .base import BaseAgent
from ..metrics import extract_ad_metrics, extract_ad_nm_metrics, extract_campaign_ids, summarize_for_prompt, to_number
from ..models import AgentResult

log = logging.getLogger(__name__)


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
    """Keep every run bounded while allowing the complete active catalog to finish promptly."""
    def bounded(name: str, default: int, ceiling: int) -> int:
        try:
            value = int(runtime.get(name, default) or default)
        except (TypeError, ValueError):
            value = default
        return max(1, min(ceiling, value))

    return (
        bounded("max_campaigns_deep_scan", 12, 12),
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


def _campaign_nm_catalog(
    campaigns: list[dict[str, Any]],
    exact_nm_stats: dict[tuple[int, int], dict[str, Any]],
) -> dict[int, list[int]]:
    """Union campaign settings with exact product IDs observed in WB ad statistics."""
    catalog = {
        cid: set(_nm_ids(campaign))
        for campaign in campaigns
        if (cid := _campaign_id(campaign))
    }
    for campaign_id, nm_id in exact_nm_stats:
        if campaign_id in catalog and nm_id > 0:
            catalog[campaign_id].add(int(nm_id))
    return {cid: sorted(nms) for cid, nms in catalog.items()}


def _sweep_progress(
    campaign_nm_ids: dict[int, list[int]],
    swept_nm_ids: dict[str, list[int]],
) -> tuple[int, int, bool]:
    """Count only actually scanned unique products and verify every campaign/SKU pair."""
    expected = {cid: set(nms) for cid, nms in campaign_nm_ids.items()}
    swept = {str(cid): set(ids) for cid, ids in swept_nm_ids.items()}
    total_products = len(set().union(*expected.values())) if expected else 0
    seen_products = len(
        set().union(*(swept.get(str(cid), set()) & nms for cid, nms in expected.items()))
    ) if expected else 0
    complete = all(swept.get(str(cid), set()) >= nms for cid, nms in expected.items())
    return seen_products, total_products, complete


def _sweep_pair_progress(
    campaign_nm_ids: dict[int, list[int]],
    swept_nm_ids: dict[str, list[int]],
) -> tuple[int, int, bool]:
    """Count campaign×product pairs, the actual unit of sweep completion."""
    expected = {cid: set(nms) for cid, nms in campaign_nm_ids.items()}
    swept = {str(cid): set(ids) for cid, ids in swept_nm_ids.items()}
    total = sum(len(nms) for nms in expected.values())
    seen = sum(len(swept.get(str(cid), set()) & nms) for cid, nms in expected.items())
    return seen, total, seen == total


def _completed_sweep_campaign_ids(
    campaign_nm_ids: dict[int, list[int]],
    swept_nm_ids: dict[str, list[int]],
    successful_campaign_ids: list[int],
) -> list[int]:
    """Count campaigns only after every advertised product in that campaign was scanned."""
    swept = {str(cid): set(ids) for cid, ids in swept_nm_ids.items()}
    successful = {int(cid) for cid in successful_campaign_ids}
    completed = []
    for cid, nms in campaign_nm_ids.items():
        expected = {int(nm) for nm in nms}
        if expected and swept.get(str(cid), set()) >= expected:
            completed.append(int(cid))
        elif not expected and int(cid) in successful:
            completed.append(int(cid))
    return sorted(completed)


def _scan_cursor(
    previous: Any,
    campaign_ids: list[int],
    campaign_nm_ids: dict[int, list[int]] | None = None,
) -> dict[str, Any]:
    """Resume a fair sweep; reset when either campaign IDs or their product set changes."""
    saved = previous.get("data") if isinstance(previous, dict) else previous
    saved = saved if isinstance(saved, dict) else {}
    expected = [int(x) for x in campaign_ids]
    expected_nms = {str(cid): sorted({int(nm) for nm in nms}) for cid, nms in (campaign_nm_ids or {}).items()}
    catalog_changed = saved.get("campaign_nm_ids") != expected_nms
    if saved.get("campaign_ids") != expected or catalog_changed:
        return {"campaign_ids": expected, "campaign_nm_ids": expected_nms,
                "next_campaign_index": 0, "next_nm_index": {},
                "sweep_id": int(saved.get("sweep_id") or 0) + 1,
                "swept_campaign_ids": [], "swept_nm_ids": {}}
    return {
        "campaign_ids": expected,
        "campaign_nm_ids": expected_nms,
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


_TRANSIENT_AD_READ_MARKERS = (
    "remoteprotocolerror",
    "server disconnected without sending a response",
    "readtimeout",
    "connecttimeout",
    "connecterror",
    "brokenresourceerror",
    "closedresourceerror",
    "endofstream",
    "broken pipe",
    "connection reset",
    "connection aborted",
    "temporarily unavailable",
    "timed out",
    "http 429",
    "status code 429",
    "status_code=429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
)


def _transient_ad_read_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _TRANSIENT_AD_READ_MARKERS)


def _broken_ad_transport(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in (
        "remoteprotocolerror", "server disconnected without sending a response",
        "readtimeout", "connecttimeout", "connecterror", "brokenresourceerror",
        "closedresourceerror", "endofstream", "broken pipe", "connection reset",
        "connection aborted",
    ))


async def _safe_ad_read(agent: BaseAgent, tool: str, **kwargs: Any) -> Any:
    """Retry transient failures only for the advertising agents' read-only calls."""
    for attempt in range(3):
        try:
            return await agent.call(tool, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if attempt == 2 or not _transient_ad_read_error(exc):
                return {"_error": str(exc), "_tool": tool}
            log.warning(
                "Temporary WB advertising read failure: tool=%s attempt=%s error=%s",
                tool, attempt + 1, type(exc).__name__,
            )
            if _broken_ad_transport(exc):
                try:
                    await agent.ctx.wb.close()
                except Exception as close_exc:
                    log.warning("Could not reset WB MCP session: %s", type(close_exc).__name__)
            await asyncio.sleep(2 ** (attempt + 1))
    return {"_error": "WB advertising read failed after retry limit", "_tool": tool}


class AdvertisingMonitorAgent(BaseAgent):
    name = "advertising_monitor"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        cfg = self.ctx.policy.thresholds.get("advertising", {})
        campaigns = await _safe_ad_read(self, "wb_advert_list", statuses=[9])
        out.snapshots.append(("active_campaigns", campaigns if isinstance(campaigns, dict) else {"data": campaigns}))
        if isinstance(campaigns, dict) and campaigns.get("_error"):
            out.events.append(self.event("warning", "ad_campaign_list_unavailable", "Не удалось получить список рекламных кампаний", str(campaigns["_error"]), {"tool": campaigns.get("_tool")}))
            return out
        campaign_rows = _rows(campaigns)
        campaign_by_id = {cid: row for row in campaign_rows if (cid := _campaign_id(row))}
        ids = sorted(campaign_by_id)
        if not ids:
            return out
        start, end = self.dates(7)
        stats = await _safe_ad_read(self, "wb_advert_stats", advert_ids=ids, date_from=start, date_to=end)
        out.snapshots.append(("stats_7d", stats if isinstance(stats, dict) else {"data": stats}))
        if isinstance(stats, dict) and stats.get("_error"):
            out.events.append(self.event("warning", "ad_stats_unavailable", "Не удалось получить статистику рекламы", str(stats["_error"]), {"tool": stats.get("_tool"), "campaign_ids": ids}))
            return out
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
        return await _safe_ad_read(self, tool, **kwargs)

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        campaigns_raw = await self._safe("wb_advert_list", statuses=[9])
        campaigns = _rows(campaigns_raw)
        ids = [x for x in (_campaign_id(c) for c in campaigns) if x]
        catalog_error = isinstance(campaigns_raw, dict) and bool(campaigns_raw.get("_error"))
        catalog_complete = (
            isinstance(campaigns_raw, list) and not catalog_error
        ) or (
            isinstance(campaigns_raw, dict) and not catalog_error
            and any(isinstance(campaigns_raw.get(key), list) for key in ("campaigns", "adverts", "data", "items"))
        )
        if not ids:
            empty_catalog = catalog_complete
            out.snapshots.append(("deep_scan", {
                "campaigns": [], "active_campaign_ids": [], "catalog_complete": empty_catalog,
                "note": "no active campaigns" if empty_catalog else "campaign catalog unavailable",
                "sweep_id": 0, "sweep_complete": empty_catalog,
                "sweep_campaigns_seen": 0, "sweep_campaigns_total": 0,
                "sweep_products_seen": 0, "sweep_products_total": 0,
                "eligible_campaigns": 0, "campaigns_scanned": 0,
            }))
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
        campaign_nm_ids = _campaign_nm_catalog(campaigns, exact_nm_stats)
        runtime = (self.ctx.policy.raw.get("runtime") or {}).get("advertising", {})
        max_campaigns, max_nms, concurrency = _deep_scan_limits(runtime)
        old_cursor = self.ctx.db.latest_snapshot(self.name, "deep_scan_cursor")
        cursor = _scan_cursor(
            old_cursor,
            [_campaign_id(c) for c in campaigns if _campaign_id(c)],
            campaign_nm_ids,
        )
        semaphore = asyncio.Semaphore(concurrency)

        async def limited(tool: str, **kwargs: Any) -> Any:
            async with semaphore:
                return await self._safe(tool, **kwargs)

        async def scan_campaign(campaign: dict[str, Any]) -> dict[str, Any] | None:
            cid = _campaign_id(campaign)
            if not cid:
                return None
            nms = campaign_nm_ids.get(cid, [])
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
            cluster_bids_task = (
                asyncio.create_task(
                    limited("wb_advert_cluster_bids_get", advert_id=cid, nm_ids=selected_nms)
                )
                if selected_nms else None
            )
            budget = await budget_task
            recommendations = {
                nm: await task
                for nm, task in rec_tasks.items()
            }
            clusters = await clusters_task if clusters_task else {}
            cluster_stats = await cluster_stats_task if cluster_stats_task else {}
            cluster_bids = await cluster_bids_task if cluster_bids_task else {}
            cluster_bids_observed_at = datetime.now(timezone.utc).isoformat() if cluster_bids_task else None
            responses = [budget, clusters, cluster_stats, *recommendations.values()]
            if cluster_bids_task:
                responses.append(cluster_bids)
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
                "cluster_bids": cluster_bids,
                "cluster_bids_observed_at": cluster_bids_observed_at,
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
                    "nm_ids": campaign_nm_ids.get(_campaign_id(campaign) or 0, []),
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

        cursor["swept_campaign_ids"] = _completed_sweep_campaign_ids(
            campaign_nm_ids,
            cursor["swept_nm_ids"],
            cursor["swept_campaign_ids"],
        )
        swept_campaign_count = len(cursor["swept_campaign_ids"])
        swept_product_count, total_product_count, all_campaign_products_seen = _sweep_progress(
            campaign_nm_ids, cursor["swept_nm_ids"]
        )
        swept_pair_count, total_pair_count, all_campaign_pairs_seen = _sweep_pair_progress(
            campaign_nm_ids, cursor["swept_nm_ids"]
        )
        sweep_complete = (set(cursor["swept_campaign_ids"]) >= set(cursor["campaign_ids"])
                          and all_campaign_products_seen
                          and all_campaign_pairs_seen
                          and all(isinstance(item, dict) and item.get("scan_complete") is True for item in scanned))
        if sweep_complete:
            cursor["sweep_id"] += 1
            cursor["swept_campaign_ids"] = []
            cursor["swept_nm_ids"] = {}

        out.snapshots.append(("deep_scan", {
            "active_campaign_ids": [int(x) for x in cursor["campaign_ids"]],
            "catalog_complete": catalog_complete,
            "generated_for": {"from": start, "to": end},
            "sweep_id": cursor["sweep_id"],
            "sweep_complete": sweep_complete,
            "sweep_campaigns_seen": swept_campaign_count,
            "sweep_campaigns_total": len(cursor["campaign_ids"]),
            "sweep_products_seen": swept_product_count,
            "sweep_products_total": total_product_count,
            "sweep_pairs_seen": swept_pair_count,
            "sweep_pairs_total": total_pair_count,
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
