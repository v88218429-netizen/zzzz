from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from wb_control_center.agents.advertising import AdvertisingOptimizerAgent, _safe_ad_read


def _load_runtime_supervisor():
    source = Path(__file__).resolve().parents[1] / "scripts" / "runtime_supervisor.py"
    spec = importlib.util.spec_from_file_location("runtime_supervisor_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeWB:
    def __init__(self):
        self.close_calls = 0

    async def close(self):
        self.close_calls += 1


@pytest.mark.asyncio
async def test_optimizer_read_retries_transient_disconnect_and_resets_session(monkeypatch):
    wb = _FakeWB()
    agent = AdvertisingOptimizerAgent(SimpleNamespace(wb=wb))
    calls = 0
    delays = []

    async def fake_call(tool, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("RemoteProtocolError: Server disconnected without sending a response.")
        return {"ok": True}

    async def fake_sleep(delay):
        delays.append(delay)

    agent.call = fake_call
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    result = await agent._safe("wb_advert_stats", advert_ids=[101])

    assert result == {"ok": True}
    assert calls == 2
    assert wb.close_calls == 1
    assert delays == [2]


@pytest.mark.asyncio
async def test_optimizer_read_does_not_retry_permanent_errors(monkeypatch):
    wb = _FakeWB()
    agent = AdvertisingOptimizerAgent(SimpleNamespace(wb=wb))
    calls = 0

    async def fake_call(tool, **kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("invalid campaign id")

    agent.call = fake_call
    monkeypatch.setattr(asyncio, "sleep", lambda _delay: None)

    result = await agent._safe("wb_advert_stats", advert_ids=[101])

    assert calls == 1
    assert wb.close_calls == 0
    assert "invalid campaign id" in result["_error"]


def test_runtime_supervisor_watchdog_restarts_only_after_consecutive_health_failures():
    supervisor = _load_runtime_supervisor()
    watchdog = supervisor.HealthWatchdog(interval_seconds=10, failures_before_restart=3)

    assert watchdog.due(0)
    assert watchdog.record(False, 0) is False
    assert watchdog.due(10)
    assert watchdog.record(False, 10) is False
    assert not watchdog.due(19.9)
    assert watchdog.due(20)
    assert watchdog.record(False, 20) is True


def test_runtime_supervisor_watchdog_resets_after_a_healthy_response():
    supervisor = _load_runtime_supervisor()
    watchdog = supervisor.HealthWatchdog(interval_seconds=10, failures_before_restart=3)

    watchdog.record(False, 0)
    watchdog.record(False, 10)
    assert watchdog.record(True, 20) is False
    assert watchdog.record(False, 30) is False
    assert watchdog.record(False, 40) is False
    assert watchdog.record(False, 50) is True


def test_runtime_supervisor_accepts_minimal_authenticated_health(monkeypatch):
    supervisor = _load_runtime_supervisor()

    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b'{"status":"ok"}'

    monkeypatch.setattr(supervisor.urllib.request, "urlopen", lambda *args, **kwargs: Response())

    assert supervisor.health_info(8787) == {"status": "ok"}


def test_background_runtime_does_not_open_a_visible_browser(monkeypatch):
    supervisor = _load_runtime_supervisor()
    opened = []
    monkeypatch.setenv("WB_OPEN_BROWSER", "false")
    monkeypatch.setattr(supervisor.webbrowser, "open", lambda url: opened.append(url))

    assert supervisor.open_dashboard_if_enabled(8787) is False
    assert opened == []


@pytest.mark.asyncio
async def test_shared_ad_read_helper_retries_for_both_advertising_agents(monkeypatch):
    wb = _FakeWB()
    calls = 0
    delays = []

    async def fake_call(tool, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("ReadTimeout: temporary source timeout")
        return {"rows": []}

    async def fake_sleep(delay):
        delays.append(delay)

    agent = SimpleNamespace(ctx=SimpleNamespace(wb=wb), call=fake_call)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    result = await _safe_ad_read(agent, "wb_advert_list", statuses=[9])

    assert result == {"rows": []}
    assert calls == 2
    assert delays == [2]
    assert wb.close_calls == 1
