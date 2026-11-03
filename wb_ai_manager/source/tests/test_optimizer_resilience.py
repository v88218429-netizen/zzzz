from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from wb_control_center.agents.advertising import AdvertisingOptimizerAgent, _safe_ad_read



def test_advertising_deep_scan_limits_allow_twelve_campaigns_without_unbounded_calls():
    from wb_control_center.agents.advertising import _deep_scan_limits

    assert _deep_scan_limits({}) == (12, 2, 2)
    assert _deep_scan_limits({"max_campaigns_deep_scan": 999}) == (12, 2, 2)
    assert _deep_scan_limits({"max_campaigns_deep_scan": 3}) == (3, 2, 2)


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



def test_optimizer_product_catalog_includes_exact_nmids_seen_in_campaign_statistics():
    from wb_control_center.agents.advertising import _campaign_nm_catalog

    campaigns = [
        {"advertId": 101, "nmIds": [1001]},
        {"advertId": 202, "nmIds": [2001, 2002]},
    ]
    exact_nm_stats = {
        (101, 1001): {"nmId": 1001, "orders": 1},
        (101, 1002): {"nmId": 1002, "orders": 2},
        (202, 2003): {"nmId": 2003, "orders": 1},
        (999, 9999): {"nmId": 9999, "orders": 1},
    }

    assert _campaign_nm_catalog(campaigns, exact_nm_stats) == {
        101: [1001, 1002],
        202: [2001, 2002, 2003],
    }


def test_optimizer_progress_counts_only_scanned_unique_products_and_requires_each_campaign_sku():
    from wb_control_center.agents.advertising import _sweep_progress

    expected = {101: [1001, 1002], 202: [1002, 2001]}
    partially_scanned = {"101": [1001], "202": [1002]}

    assert _sweep_progress(expected, partially_scanned) == (2, 3, False)
    assert _sweep_progress(expected, {"101": [1001, 1002], "202": [1002, 2001]}) == (3, 3, True)



def test_optimizer_progress_counts_campaign_product_pairs_and_only_complete_campaigns():
    from wb_control_center.agents.advertising import (
        _completed_sweep_campaign_ids,
        _sweep_pair_progress,
        _sweep_progress,
    )

    expected = {101: [1001, 1002], 202: [1001, 1002]}
    partial = {"101": [1001, 1002], "202": [1001]}

    # Every unique nmID has appeared, but campaign 202 still lacks one required pair.
    assert _sweep_progress(expected, partial) == (2, 2, False)
    assert _sweep_pair_progress(expected, partial) == (3, 4, False)
    assert _completed_sweep_campaign_ids(expected, partial, [101, 202]) == [101]

    complete = {"101": [1001, 1002], "202": [1001, 1002]}
    assert _sweep_pair_progress(expected, complete) == (4, 4, True)
    assert _completed_sweep_campaign_ids(expected, complete, [101, 202]) == [101, 202]


def test_optimizer_cursor_resets_when_product_catalog_changes():
    from wb_control_center.agents.advertising import _scan_cursor

    previous = _scan_cursor(None, [101], {101: [1001]})
    previous["next_campaign_index"] = 1
    previous["swept_campaign_ids"] = [101]
    previous["swept_nm_ids"] = {"101": [1001]}

    updated = _scan_cursor({"data": previous}, [101], {101: [1001, 1002]})

    assert updated["sweep_id"] == previous["sweep_id"] + 1
    assert updated["next_campaign_index"] == 0
    assert updated["swept_campaign_ids"] == []
    assert updated["swept_nm_ids"] == {}
