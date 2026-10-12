from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from typing import Any

import httpx

from .base import BaseAgent
from ..models import AgentResult


NEWS_URL = "https://common-api.wildberries.ru/api/communications/v2/news"
NEWS_LOOKBACK_DAYS = 7


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def _find_news_list(payload: Any) -> list[dict[str, Any]]:
    """Accept the documented array and common response wrappers, rejecting unknown shapes."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        raise ValueError("API новостей вернул неожиданный формат данных")
    for key in ("data", "news", "items", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            try:
                return _find_news_list(value)
            except ValueError:
                continue
    if any(key in payload for key in ("id", "ID", "newsID", "newsId")):
        return [payload]
    raise ValueError("API новостей вернул неизвестную структуру; данные не записаны как пустые")


def _news_id(item: dict[str, Any]) -> int | None:
    for key in ("id", "ID", "newsID", "newsId"):
        value = item.get(key)
        if value is None:
            continue
        try:
            parsed = int(value)
            return parsed if parsed >= 0 else None
        except (TypeError, ValueError):
            continue
    return None


def _title(item: dict[str, Any]) -> str:
    return unescape(str(item.get("header") or item.get("title") or item.get("name") or "Новость WB")).strip()


def _body(item: dict[str, Any]) -> str:
    value = unescape(str(item.get("content") or item.get("text") or item.get("description") or "")).strip()
    parser = _PlainText()
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        return value
    return " ".join(parser.parts) or value


def _relevance(item: dict[str, Any]) -> str:
    text = f"{_title(item)} {_body(item)}".casefold()
    operational = ("комисс", "тариф", "возврат", "заказ", "реклам", "склад", "остат", "карточ", "штраф", "логист", "достав", "продвиж", "продавц")
    education = ("вебинар", "обучен", "мероприят", "встреча", "курс")
    if any(word in text for word in operational):
        return "возможное влияние на работу магазина — проверить условия и дату"
    if any(word in text for word in education):
        return "обучение или мероприятие — низкий приоритет"
    return "общее сообщение WB — влияние на магазин пока не установлено"


class PortalNewsAgent(BaseAgent):
    """Read-only seller portal news watcher using the official Wildberries API."""

    name = "portal_news"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        token = str(getattr(self.ctx.settings, "wb_api_token", "") or "").strip()
        if not token:
            out.snapshots.append(("news_health", {"status": "unavailable", "checked_at": datetime.now(timezone.utc).isoformat(), "error": "WB_API_TOKEN не настроен"}))
            out.events.append(self.event("warning", "news_token_missing", "Новости WB не подключены", "Для чтения новостей нужен токен API WB."))
            return out

        previous = self.ctx.db.latest_snapshot(self.name, "news_cursor")
        cursor_data = (previous or {}).get("data") or {}
        previous_news_record = self.ctx.db.latest_snapshot(self.name, "news")
        previous_news_data = (previous_news_record or {}).get("data") or {}
        previous_items = previous_news_data.get("items", []) if isinstance(previous_news_data, dict) else []
        if not isinstance(previous_items, list):
            previous_items = []
        last_id = cursor_data.get("from_id")
        if isinstance(last_id, str) and last_id.isdigit():
            last_id = int(last_id)
        params: dict[str, Any]
        if isinstance(last_id, int) and last_id >= 0:
            params = {"fromID": last_id}
        else:
            from_date = (datetime.now(timezone.utc).date() - timedelta(days=NEWS_LOOKBACK_DAYS)).isoformat()
            params = {"from": from_date}

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(NEWS_URL, params=params, headers={"Authorization": token})
                response.raise_for_status()
                payload = response.json()
            items = _find_news_list(payload)
        except Exception as exc:
            out.snapshots.append(("news_health", {"status": "unavailable", "checked_at": datetime.now(timezone.utc).isoformat(), "error": str(exc), "source": NEWS_URL}))
            out.events.append(self.event("warning", "news_fetch_failed", "Не удалось получить новости WB", str(exc), {"params": params}))
            return out

        ids = [_news_id(item) for item in items]
        valid_ids = [value for value in ids if value is not None]
        newest_id = max(valid_ids) if valid_ids else last_id
        old_ids = set((cursor_data.get("seen_ids") or []) if isinstance(cursor_data, dict) else [])
        new_items = [item for item in items if _news_id(item) is not None and _news_id(item) not in old_ids and _news_id(item) != last_id]

        # On first contact establish a baseline without declaring old material new.
        is_baseline = not previous
        incoming_items = []
        for item in items:
            incoming_items.append({
                "id": _news_id(item),
                "title": _title(item),
                "text": _body(item),
                "published_at": item.get("date") or item.get("createdAt") or item.get("publishedAt"),
                "relevance": _relevance(item),
                "source": item,
            })

        # The API request is incremental after the first poll. Keep a compact
        # 100-item feed across polls, and don't append a large duplicate snapshot
        # every five minutes when WB returned no changes.
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        for item in [*previous_items, *incoming_items]:
            if not isinstance(item, dict):
                continue
            identity = ("id", str(item["id"])) if item.get("id") is not None else ("title-date", f"{item.get('title', '')}\0{item.get('published_at', '')}")
            merged[identity] = item
        snapshot_items = sorted(
            merged.values(),
            key=lambda item: (item.get("id") is not None, int(item["id"]) if str(item.get("id", "")).isdigit() else 0, str(item.get("published_at") or "")),
            reverse=True,
        )[:100]

        summary = None
        analysis_mode = "правила"
        if new_items and self.ctx.llm.enabled:
            compact = [{"title": _title(x), "text": _body(x)[:2500], "date": x.get("date") or x.get("createdAt")} for x in new_items[:20]]
            try:
                summary = await self.ctx.llm.complete(
                    "Ты аналитик новостей Wildberries для продавца. Кратко перескажи только факты из текста, отметь сроки и возможное влияние на работу магазина. Явно отделяй факт источника от предположения. Не давай правовых заключений, не выдумывай применимость к товарам и не предлагай менять платные услуги автоматически.",
                    str(compact),
                    max_tokens=700,
                )
                if summary:
                    analysis_mode = f"модель: {self.ctx.llm.label}"
            except Exception:
                # Keep the original source text even when the optional model is unavailable.
                summary = None

        fetched_at = datetime.now(timezone.utc).isoformat()
        out.snapshots.append(("news_health", {"status": "fresh", "checked_at": fetched_at, "received_count": len(items), "items_retained": len(snapshot_items), "new_count": len(new_items), "source": NEWS_URL}))
        if is_baseline or new_items or not previous_news_record:
            out.snapshots.append(("news", {
                "status": "fresh",
                "source": NEWS_URL,
                "fetched_at": fetched_at,
                "count": len(snapshot_items),
                "analysis_mode": analysis_mode,
                "summary": summary,
                "items": snapshot_items,
            }))
        seen_ids = sorted(set(old_ids).union(value for value in valid_ids))[-500:]
        out.snapshots.append(("news_cursor", {"from_id": newest_id, "seen_ids": seen_ids, "fetched_at": fetched_at}))

        if is_baseline:
            out.events.append(self.event("info", "news_baseline_loaded", "Первичная загрузка новостей WB завершена", f"Получено новостей за последние {NEWS_LOOKBACK_DAYS} дней: {len(items)}. Эти записи сохранены как исходный срез, без отметки о новых событиях.", {"count": len(items), "fetched_at": fetched_at}))
        elif new_items:
            notable = [_title(item) for item in new_items[:5]]
            relevant = any(_relevance(item).startswith("возможное влияние") for item in new_items)
            severity = "warning" if relevant else "info"
            title = "Новые новости WB могут затрагивать магазин" if relevant else "Появились новые новости WB"
            message = "; ".join(notable)
            if len(new_items) > len(notable):
                message += f"; ещё {len(new_items) - len(notable)}"
            if summary:
                message = f"{summary}\n\nЗаголовки источника: {message}"
            out.events.append(self.event(severity, "news_updates", title, message, {
                "news_ids": [_news_id(item) for item in new_items],
                "analysis_mode": analysis_mode,
                "summary": summary,
                "items": [item for item in snapshot_items if item["id"] in {_news_id(x) for x in new_items}],
                "source": NEWS_URL,
                "fetched_at": fetched_at,
            }))
        return out
