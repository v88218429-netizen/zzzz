from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timedelta

import httpx

from .base import BaseAgent
from ..metrics import extract_position_rows
from ..models import AgentResult
from ..portfolio import PortfolioService


class SearchPositionsAgent(BaseAgent):
    name = "search_positions"

    async def _direct_query_rows(self, nm_ids: list[int], start: str, end: str) -> tuple[list[dict], dict]:
        """Fetch factual query-level positions and retain safe source diagnostics."""
        endpoint = "WB Analytics product/search-texts"
        token = str(getattr(self.ctx.settings, "wb_api_token", "") or "").strip()
        ids = sorted({int(x) for x in nm_ids if int(x) > 0})
        if not token:
            return [], {"endpoint": endpoint, "status": "skipped", "reason": "missing_token", "requests": 0}
        if not ids:
            return [], {"endpoint": endpoint, "status": "skipped", "reason": "no_nm_ids", "requests": 0}
        try:
            start_day = date.fromisoformat(start)
            end_day = date.fromisoformat(end)
        except ValueError:
            return [], {"endpoint": endpoint, "status": "error", "reason": "invalid_period", "requests": 0}
        if end_day < start_day:
            return [], {"endpoint": endpoint, "status": "error", "reason": "invalid_period", "requests": 0}

        width = max(1, (end_day - start_day).days + 1)
        past_end = start_day - timedelta(days=1)
        past_start = past_end - timedelta(days=width - 1)
        url = "https://seller-analytics-api.wildberries.ru/api/v2/search-report/product/search-texts"
        chunks = [ids[i:i + 50] for i in range(0, len(ids), 50)]
        failures: list[dict] = []
        rows: list[dict] = []
        request_count = 0

        def error_reason(status: int) -> str:
            return {
                400: "invalid_request",
                401: "unauthorized",
                402: "payment_required",
                403: "access_or_subscription_denied",
                429: "rate_limited",
            }.get(status, "wb_server_error" if status >= 500 else "http_error")

        def safe_detail(response) -> str:
            try:
                payload = response.json()
            except Exception:
                return ""
            if not isinstance(payload, dict):
                return ""
            for key in ("detail", "message", "errorText", "error", "title"):
                value = payload.get(key)
                if not isinstance(value, (str, int, float)):
                    continue
                detail = re.sub(r"\s+", " ", str(value)).strip()
                detail = re.sub(
                    r"(?i)(authorization|token|api[_ -]?key)(\s*[:=]\s*)[^\s,;]+",
                    r"\1\2[скрыто]",
                    detail,
                )
                detail = re.sub(r"\b[A-Za-z0-9._-]{32,}\b", "[скрыто]", detail)
                return detail[:240]
            return ""

        async with httpx.AsyncClient(timeout=httpx.Timeout(35.0, connect=10.0)) as client:
            for chunk_index, chunk in enumerate(chunks):
                if request_count:
                    # WB Analytics permits one request every 20 seconds per seller profile.
                    await asyncio.sleep(20.0)
                body = {
                    "currentPeriod": {"start": start, "end": end},
                    "pastPeriod": {"start": past_start.isoformat(), "end": past_end.isoformat()},
                    "nmIds": chunk,
                    "topOrderBy": "orders",
                    "includeSubstitutedSKUs": True,
                    "includeSearchTexts": True,
                    "orderBy": {"field": "avgPosition", "mode": "asc"},
                    # 30 is valid on every tariff; 100 is restricted to Advanced/Premium Jam.
                    "limit": 30,
                }
                request_count += 1
                try:
                    response = await client.post(
                        url,
                        json=body,
                        headers={"Authorization": token, "Accept": "application/json"},
                    )
                    if response.status_code != 200:
                        failures.append({
                            "chunk": chunk_index + 1,
                            "nm_ids": chunk,
                            "http_status": int(response.status_code),
                            "reason": error_reason(int(response.status_code)),
                            "detail": safe_detail(response),
                        })
                        continue
                    payload = response.json()
                except Exception as exc:
                    failures.append({
                        "chunk": chunk_index + 1,
                        "nm_ids": chunk,
                        "reason": "network_or_response_error",
                        "error_type": type(exc).__name__,
                    })
                    continue

                items = ((payload or {}).get("data") or {}).get("items") or []
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
                    rows.append({
                        "nm_id": nm_i,
                        "query": query,
                        "position": pos_f,
                        "frequency": freq_f,
                        "source": "wb_search_report",
                        "source_endpoint": endpoint,
                    })

        deduped: list[dict] = []
        seen: set[tuple[int, str]] = set()
        for row in rows:
            key = (int(row["nm_id"]), str(row["query"]).lower())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(row)
        status = "error" if failures and not deduped else "partial" if failures else "ok" if deduped else "empty"
        return deduped, {
            "endpoint": endpoint,
            "status": status,
            "requests": request_count,
            "chunks_total": len(chunks),
            "chunks_failed": len(failures),
            "rows": len(deduped),
            "failures": failures,
        }

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
        query_rows, query_diagnostics = await self._direct_query_rows(nm_ids, start, end)
        if query_diagnostics.get("failures"):
            failure_text = "; ".join(
                f"пакет {item.get('chunk')}, nmID {', '.join(str(x) for x in item.get('nm_ids', []))}: "
                f"HTTP {item.get('http_status', 'нет ответа')} ({item.get('reason')})"
                + (f" — {item['detail']}" if item.get("detail") else "")
                + (f" [{item['error_type']}]" if item.get("error_type") else "")
                for item in query_diagnostics["failures"]
            )
            out.events.append(self.event(
                "warning",
                "search_texts_source_error",
                "WB не обновил поисковые запросы",
                f"{query_diagnostics.get('endpoint')}: {failure_text}",
                {"search_diagnostics": query_diagnostics},
            ))
        elif not query_rows and query_diagnostics.get("status") == "empty":
            out.events.append(self.event(
                "info",
                "search_texts_empty",
                "WB вернул пустой список поисковых запросов",
                f"{query_diagnostics.get('endpoint')}: {query_diagnostics.get('requests', 0)} запросов завершились без ошибок, но строки не найдены.",
                {"search_diagnostics": query_diagnostics},
            ))
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
