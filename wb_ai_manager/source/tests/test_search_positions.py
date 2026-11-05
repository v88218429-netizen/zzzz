from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

import wb_control_center.agents.search_positions as search_positions


class _Response:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload


class _Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, *, json, headers):
        self.calls.append({"url": url, "body": json, "headers": headers})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _agent():
    agent = object.__new__(search_positions.SearchPositionsAgent)
    agent.ctx = SimpleNamespace(settings=SimpleNamespace(wb_api_token="test-token"))
    return agent


@pytest.mark.asyncio
async def test_direct_search_requests_respect_wb_payload_and_pacing_limits(monkeypatch):
    ids = list(range(1000, 1101))
    responses = [
        _Response(payload={"data": {"items": [{
            "nmId": chunk_start,
            "text": f"query-{chunk_start}",
            "avgPosition": {"current": 4},
            "frequency": {"current": 12},
        }]}})
        for chunk_start in (1000, 1050, 1100)
    ]
    client = _Client(responses)
    monkeypatch.setattr(search_positions.httpx, "AsyncClient", lambda **_kwargs: client)
    sleep = AsyncMock()
    monkeypatch.setattr(search_positions.asyncio, "sleep", sleep)

    rows, diagnostic = await _agent()._direct_query_rows(ids, "2026-11-03", "2026-11-04")

    assert [len(call["body"]["nmIds"]) for call in client.calls] == [50, 50, 1]
    assert [call["body"]["limit"] for call in client.calls] == [30, 30, 30]
    assert sleep.await_args_list == [call(20.0), call(20.0)]
    assert len(rows) == 3
    assert diagnostic["status"] == "ok"
    assert diagnostic["chunks_total"] == 3
    assert diagnostic["chunks_failed"] == 0


@pytest.mark.asyncio
async def test_direct_search_retries_rate_limit_once_after_retry_after(monkeypatch):
    client = _Client([
        _Response(429, {"message": "too many requests"}, {"Retry-After": "5"}),
        _Response(payload={"data": {"items": [{
            "nmId": 12345,
            "text": "товар",
            "avgPosition": {"current": 2},
            "frequency": {"current": 10},
        }]}}),
    ])
    monkeypatch.setattr(search_positions.httpx, "AsyncClient", lambda **_kwargs: client)
    sleep = AsyncMock()
    monkeypatch.setattr(search_positions.asyncio, "sleep", sleep)

    rows, diagnostic = await _agent()._direct_query_rows([12345], "2026-11-03", "2026-11-04")

    assert len(client.calls) == 2
    assert sleep.await_args_list == [call(20.0)]
    assert rows[0]["query"] == "товар"
    assert diagnostic["status"] == "ok"
    assert diagnostic["requests"] == 2


@pytest.mark.asyncio
async def test_direct_search_preserves_http_reason_and_redacts_credentials(monkeypatch):
    client = _Client([
        _Response(400, {"message": "limit is invalid; authorization=secret-token-123456789012345678901234567890"}),
    ])
    monkeypatch.setattr(search_positions.httpx, "AsyncClient", lambda **_kwargs: client)
    monkeypatch.setattr(search_positions.asyncio, "sleep", AsyncMock())

    rows, diagnostic = await _agent()._direct_query_rows([12345], "2026-11-03", "2026-11-04")

    assert rows == []
    assert diagnostic["status"] == "error"
    assert diagnostic["failures"] == [{
        "chunk": 1,
        "nm_ids": [12345],
        "http_status": 400,
        "reason": "invalid_request",
        "detail": "limit is invalid; authorization=[скрыто]",
    }]


@pytest.mark.asyncio
async def test_direct_search_keeps_network_failure_type_without_exposing_exception_text(monkeypatch):
    client = _Client([RuntimeError("request failed with secret-token-123456789")])
    monkeypatch.setattr(search_positions.httpx, "AsyncClient", lambda **_kwargs: client)
    monkeypatch.setattr(search_positions.asyncio, "sleep", AsyncMock())

    rows, diagnostic = await _agent()._direct_query_rows([54321], "2026-11-03", "2026-11-04")

    assert rows == []
    assert diagnostic["failures"][0]["reason"] == "network_or_response_error"
    assert diagnostic["failures"][0]["error_type"] == "RuntimeError"
    assert "secret-token" not in str(diagnostic)
