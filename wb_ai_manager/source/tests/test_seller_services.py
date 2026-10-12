from __future__ import annotations

import asyncio
from types import SimpleNamespace

from wb_control_center.agents import seller_services
from wb_control_center.agents.seller_services import JAM_URL, TARIFF_OPTIONS_URL, SellerServicesAgent


class _Response:
    def __init__(self, payload):
        self.payload = payload
        self.text = "" if payload is None else "{}"

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Client:
    def __init__(self, payloads, **kwargs):
        self.payloads = payloads
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def get(self, url, *, headers, params=None):
        self.requests.append((url, headers, params))
        payload = self.payloads[url]
        if isinstance(payload, Exception):
            raise payload
        return _Response(payload)


class _DB:
    def __init__(self, previous=None):
        self.previous = previous or {}

    def latest_snapshot(self, source, key):
        assert source == "seller_services"
        return self.previous.get(key)


def test_services_agent_reads_jam_and_selected_options_without_writes(monkeypatch):
    client = _Client({
        JAM_URL: {"state": "active", "level": "premium", "since": "2026-08-01T00:00:00Z", "till": "2026-10-30T00:00:00Z"},
        TARIFF_OPTIONS_URL: {"activeOptionCount": 1, "activePackageCount": 0, "totalCommissionRate": 2.5, "options": [{"id": 7, "name": "Тест"}], "packages": []},
    })
    monkeypatch.setattr(seller_services.httpx, "AsyncClient", lambda **kw: client)
    ctx = SimpleNamespace(settings=SimpleNamespace(wb_api_token="token"), db=_DB())

    result = asyncio.run(SellerServicesAgent(ctx).run())

    snapshots = dict(result.snapshots)
    assert [request[0] for request in client.requests] == [JAM_URL, TARIFF_OPTIONS_URL]
    assert all(request[1] == {"Authorization": "token"} for request in client.requests)
    assert client.requests[0][2] is None
    assert client.requests[1][2] == {"locale": "ru"}
    assert snapshots["jam_subscription"]["data"]["state"] == "active"
    assert snapshots["tariff_options"]["data"]["options"][0]["id"] == 7
    assert snapshots["service_health"]["tariff_options"]["status"] == "fresh"
    assert not result.events


def test_services_agent_flags_changed_options_without_enabling_anything(monkeypatch):
    previous = {
        "jam_subscription": {"data": {"status": "fresh", "data": {"state": "active", "level": "premium"}}},
        "tariff_options": {"data": {"status": "fresh", "data": {"activeOptionCount": 0, "options": [], "packages": []}}},
    }
    client = _Client({
        JAM_URL: {"state": "active", "level": "premium"},
        TARIFF_OPTIONS_URL: {"activeOptionCount": 1, "totalCommissionRate": 3, "options": [{"id": 7}], "packages": []},
    })
    monkeypatch.setattr(seller_services.httpx, "AsyncClient", lambda **kw: client)
    ctx = SimpleNamespace(settings=SimpleNamespace(wb_api_token="token"), db=_DB(previous))

    result = asyncio.run(SellerServicesAgent(ctx).run())

    assert [event.key for event in result.events] == ["tariff_options_changed"]
    assert "автоматического изменения нет" in result.events[0].message


def test_services_agent_does_not_flag_identical_wrapped_snapshots(monkeypatch):
    jam = {"state": "active", "level": "premium", "till": "2026-10-30T00:00:00Z"}
    tariffs = {"activeOptionCount": 1, "options": [{"id": 7}], "packages": []}
    previous = {
        "jam_subscription": {"data": {"status": "fresh", "data": jam}},
        "tariff_options": {"data": {"status": "fresh", "data": tariffs}},
    }
    client = _Client({JAM_URL: jam, TARIFF_OPTIONS_URL: tariffs})
    monkeypatch.setattr(seller_services.httpx, "AsyncClient", lambda **kw: client)
    ctx = SimpleNamespace(settings=SimpleNamespace(wb_api_token="token"), db=_DB(previous))

    result = asyncio.run(SellerServicesAgent(ctx).run())

    assert result.events == []


def test_services_agent_keeps_last_success_when_one_wb_source_fails(monkeypatch):
    client = _Client({JAM_URL: {"state": "active"}, TARIFF_OPTIONS_URL: RuntimeError("403 service token required")})
    monkeypatch.setattr(seller_services.httpx, "AsyncClient", lambda **kw: client)
    ctx = SimpleNamespace(settings=SimpleNamespace(wb_api_token="token"), db=_DB({"tariff_options": {"data": {"data": {"options": [{"id": 7}]}}}}))

    result = asyncio.run(SellerServicesAgent(ctx).run())

    snapshots = dict(result.snapshots)
    assert "tariff_options" not in snapshots
    assert snapshots["service_health"]["tariff_options"]["status"] == "unavailable"
    assert result.events[0].key == "tariff_options_unavailable"
