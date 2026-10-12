from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from wb_control_center.agents import portal_news
from wb_control_center.agents.portal_news import PortalNewsAgent, _find_news_list, _news_id


def test_news_response_parser_keeps_records_and_rejects_unknown_shapes():
    assert _find_news_list({"data": {"news": [{"id": 1}]}}) == [{"id": 1}]
    assert _news_id({"newsID": "42"}) == 42
    with pytest.raises(ValueError):
        _find_news_list({"status": "OK"})


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _FakeClient:
    def __init__(self, payload, **kwargs):
        self.payload = payload
        self.kwargs = kwargs
        self.request = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def get(self, url, *, params, headers):
        self.request = (url, params, headers)
        return _FakeResponse(self.payload)


class _FakeDB:
    def __init__(self, previous):
        self.previous = previous

    def latest_snapshot(self, source, key):
        assert source == "portal_news"
        if isinstance(self.previous, dict) and key in self.previous:
            return self.previous[key]
        return self.previous if key == "news_cursor" else None


def test_news_agent_uses_inclusive_id_cursor_and_deduplicates(monkeypatch):
    client = _FakeClient({"data": [
        {"id": 104, "header": "Старая новость", "content": "Текст"},
        {"id": 105, "header": "Новая новость", "content": "Изменение комиссии"},
    ]})
    monkeypatch.setattr(portal_news.httpx, "AsyncClient", lambda **kw: client)
    ctx = SimpleNamespace(
        settings=SimpleNamespace(wb_api_token="token"),
        db=_FakeDB({"data": {"from_id": 104, "seen_ids": [103, 104]}}),
        llm=SimpleNamespace(enabled=False, label="rules-only"),
    )

    result = asyncio.run(PortalNewsAgent(ctx).run())

    assert client.request[0] == portal_news.NEWS_URL
    assert client.request[1] == {"fromID": 104}
    assert client.request[2] == {"Authorization": "token"}
    snapshots = dict(result.snapshots)
    assert snapshots["news_cursor"]["from_id"] == 105
    assert [x["id"] for x in snapshots["news"]["items"]] == [105, 104]
    assert len(result.events) == 1
    assert result.events[0].key == "news_updates"
    assert result.events[0].severity == "warning"
    assert result.events[0].payload["news_ids"] == [105]


def test_first_news_fetch_is_saved_as_baseline_without_false_new_alert(monkeypatch):
    client = _FakeClient({"data": [{"id": 7, "header": "WB", "content": "Общее сообщение"}]})
    monkeypatch.setattr(portal_news.httpx, "AsyncClient", lambda **kw: client)
    ctx = SimpleNamespace(
        settings=SimpleNamespace(wb_api_token="token"),
        db=_FakeDB(None),
        llm=SimpleNamespace(enabled=False, label="rules-only"),
    )
    result = asyncio.run(PortalNewsAgent(ctx).run())
    assert dict(result.snapshots)["news_cursor"]["from_id"] == 7
    assert result.events[0].key == "news_baseline_loaded"
    assert result.events[0].severity == "info"


def test_incremental_empty_poll_keeps_existing_feed_and_avoids_duplicate_snapshot(monkeypatch):
    retained = {"id": 104, "title": "Старая новость", "text": "Текст", "published_at": "2026-10-10", "source": {"id": 104}}
    previous = {
        "news_cursor": {"data": {"from_id": 104, "seen_ids": [103, 104]}},
        "news": {"data": {"items": [retained], "count": 1, "source": portal_news.NEWS_URL}},
    }
    client = _FakeClient({"data": [{"id": 104, "header": "Старая новость", "content": "Текст"}]})
    monkeypatch.setattr(portal_news.httpx, "AsyncClient", lambda **kw: client)
    ctx = SimpleNamespace(
        settings=SimpleNamespace(wb_api_token="token"),
        db=_FakeDB(previous),
        llm=SimpleNamespace(enabled=False, label="rules-only"),
    )

    result = asyncio.run(PortalNewsAgent(ctx).run())

    snapshots = dict(result.snapshots)
    assert "news" not in snapshots
    assert snapshots["news_health"]["new_count"] == 0
    assert snapshots["news_health"]["items_retained"] == 1
    assert result.events == []
