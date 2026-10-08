from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta

import httpx

from .base import BaseAgent
from ..metrics import extract_position_rows
from ..models import AgentResult
from ..portfolio import PortfolioService


class SearchPositionsAgent(BaseAgent):
    name = "search_positions"

    async def _direct_query_rows(self, nm_ids: list[int], start: str, end: str) -> list[dict]:
        """Fetch factual query-level positions from official WB Analytics.

        wb_search_report is useful for product-level search visibility, but some
        connector versions do not expose the search-text rows. The official
        product/search-texts endpoint does, so use the same cabinet token directly
        in read-only mode. Failure remains non-fatal and never fabricates freshness.
        """
        token = str(getattr(self.ctx.settings, "wb_api_token", "") or "").strip()
        ids = sorted({int(x) for x in nm_ids if int(x) > 0})
        if not token or not ids:
            return []
        try:
            start_day = date.fromisoformat(start)
            end_day = date.fromisoformat(end)
        except ValueError:
            return []
        width = max(1, (end_day - start_day).days + 1)
        past_end = start_day - timedelta(days=1)
        past_start = past_end - timedelta(days=width - 1)
        url = "https://seller-analytics-api.wildberries.ru/api/v2/search-report/product/search-texts"
        semaphore = asyncio.Semaphore(3)

        async def fetch_chunk(chunk: list[int]) -> list[dict]:
            body = {
                "currentPeriod": {"start": start, "end": end},
                "pastPeriod": {"start": past_start.isoformat(), "end": past_end.isoformat()},
                "nmIds": chunk,
                "topOrderBy": "orders",
                "includeSubstitutedSKUs": True,
                "includeSearchTexts": True,
                "orderBy": {"field": "avgPosition", "mode": "asc"},
                "limit": 100,
            }
            async with semaphore:
                try:
                    async with httpx.AsyncClient(timeout=httpx.Timeout(35.0, connect=10.0)) as client:
                        resp = await client.post(
                            url,
                            json=body,
                            headers={"Authorization": token, "Accept": "application/json"},
                        )
                    if resp.status_code != 200:
                        return []
                    payload = resp.json()
                except Exception:
                    return []
            items = ((payload or {}).get("data") or {}).get("items") or []
            out: list[dict] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                nm = item.get("nmId")
                query = str(item.get("text") or "").strip()
                avg = item.get("avgPosition")
                pos = avg.get("current") if isinstance(avg, dict) else avg
                freq_obj = item.get("frequency")
                freq = freq_obj.get("current") if isinstance(freq_obj, dict) else freq_obj
                try:
                    nm_i = int(nm)
                    pos_f = float(pos)
                except (TypeError, ValueError):
                    continue
                if nm_i <= 0 or not query:
                    continue
                try:
                    freq_f = float(freq) if freq is not None else None
                except (TypeError, ValueError):
                    freq_f = None
                out.append({
                    "nm_id": nm_i,
                    "query": query,
                    "position": pos_f,
                    "frequency": freq_f,
                    "source": "wb_search_report",
                    "source_endpoint": "WB Analytics product/search-texts",
                })
            return out

        chunks = [ids[i:i + 20] for i in range(0, len(ids), 20)]
        results = await asyncio.gather(*(fetch_chunk(chunk) for chunk in chunks))
        rows: list[dict] = []
        seen: set[tuple[int, str]] = set()
        for part in results:
            for row in part:
                key = (int(row["nm_id"]), str(row["query"]).lower())
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
        return rows

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        cfg = self.ctx.policy.thresholds.get("search", {})
        start, end = self.dates(2)
        try:
            data = await self.call("wb_search_report", date_from=start, date_to=end, limit=1000)
        except Exception as e:
            out.events.append(self.event("info", "search_unavailable", "Поисковая аналитика недоступна", f"WB search report не отработал. Частая причина — нет подписки Джем или прав токена. {e}"))
            return out
        rows = extract_position_rows(data)
        source = "wb_search_report"
        current: dict[str, dict] = {}

        # The general report often returns product-level positions with an empty
        # query. Use those nmIds to fetch factual search texts + avgPosition.
        nm_ids = sorted({int(nm) for nm, _pos, _q in rows if int(nm) > 0})
        query_rows = await self._direct_query_rows(nm_ids, start, end)
        if query_rows:
            current = {
                f"{row['nm_id']}:{row['query']}": row
                for row in query_rows
            }
        elif rows:
            current = {
                f"{nm}:{q}": {"nm_id": nm, "query": q, "position": pos, "source": source}
                for nm, pos, q in rows
            }

        if not current:
            settings = getattr(self.ctx, "settings", None)
            portfolio = PortfolioService(settings).snapshot() if settings is not None else {}
            trusted = []
            for prod in ((portfolio.get("own_27") or {}).get("products") or []):
                if not isinstance(prod, dict):
                    continue
                nm = str(prod.get("sku") or "").strip()
                pos = prod.get("top_search_position")
                query = str(prod.get("top_search_query") or "").strip()
                if nm.isdigit() and pos is not None:
                    try:
                        trusted.append((int(nm), float(pos), query))
                    except Exception:
                        pass
            if trusted:
                current = {
                    f"{nm}:{q}": {"nm_id": nm, "query": q, "position": pos, "source": "trusted_sellmonitor_positions"}
                    for nm, pos, q in trusted
                }

        if not current:
            out.events.append(self.event(
                "info",
                "search_no_rows",
                "Поисковые позиции пока не получены",
                "WB search report и доверенная таблица позиций не вернули строк. Раздел помечен как «нет данных», а не как «штатно».",
            ))
        previous = self.ctx.db.latest_snapshot(self.name, "positions")
        warn = float(cfg.get("position_drop_warn", 5))
        critical = float(cfg.get("position_drop_critical", 12))
        if previous:
            old = previous["data"]
            for key, now in current.items():
                prev = old.get(key) if isinstance(old, dict) else None
                if not prev:
                    continue
                delta = float(now["position"]) - float(prev.get("position", now["position"]))
                if delta >= critical:
                    out.events.append(self.event("critical", f"position:{key}", "Сильная просадка позиции", f"nmID {now['nm_id']} {now['query'] or ''}: {prev.get('position')} → {now['position']} (−{delta:.0f} позиций).", {"before": prev, "after": now}))
                elif delta >= warn:
                    out.events.append(self.event("warning", f"position:{key}", "Просадка позиции", f"nmID {now['nm_id']} {now['query'] or ''}: {prev.get('position')} → {now['position']} (−{delta:.0f} позиций).", {"before": prev, "after": now}))
        out.snapshots.append(("positions", current))
        return out
