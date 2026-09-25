from types import SimpleNamespace

import pytest

from wb_control_center.agents.advertising import AdvertisingMonitorAgent
from wb_control_center.agents.base import BaseAgent, reset_analysis_period, set_analysis_period
from wb_control_center.agents.finance import FinanceAgent
from wb_control_center.agents.inventory import InventoryAgent
from wb_control_center.engine import ControlCenter
from wb_control_center.models import PeriodContext


def test_period_context_normalizes_dates_and_builds_key():
    period = PeriodContext.from_strings("2026-09-07", "2026-09-01")
    assert period.from_date == "2026-09-01"
    assert period.to_date == "2026-09-07"
    assert period.days == 7
    assert period.key == "2026-09-01__2026-09-07"


def test_base_agent_dates_use_selected_period():
    period = PeriodContext.from_strings("2026-09-01", "2026-09-07")
    token = set_analysis_period(period)
    try:
        agent = BaseAgent(None)  # type: ignore[arg-type]
        assert agent.dates(30) == ("2026-09-01", "2026-09-07")
    finally:
        reset_analysis_period(token)


def test_period_scope_truth_model():
    assert ControlCenter._period_snapshot_scope("advertising_monitor", "stats_7d") == "selected_period"
    assert ControlCenter._period_snapshot_scope("inventory", "coverage") == "current_plus_period"
    assert ControlCenter._period_snapshot_scope("cards", "card_catalog") == "current_snapshot"
    assert ControlCenter._period_snapshot_scope("finance", "worker_finance") == "period_filterable_export"
    assert ControlCenter._period_event_scope("reviews_questions") == "current_snapshot"
    assert ControlCenter._period_event_scope("search_positions") == "selected_period"


@pytest.mark.asyncio
async def test_advertising_stats_receive_selected_period():
    ctx = SimpleNamespace(
        policy=SimpleNamespace(thresholds={"advertising": {}}),
        llm=SimpleNamespace(enabled=False),
    )
    agent = AdvertisingMonitorAgent(ctx)  # type: ignore[arg-type]
    calls = []

    async def fake_call(tool, **kwargs):
        calls.append((tool, kwargs))
        if tool == "wb_advert_list":
            return {"campaigns": [{"advertId": 77}]}
        if tool == "wb_advert_stats":
            return {"data": []}
        raise AssertionError(tool)

    agent.call = fake_call  # type: ignore[method-assign]
    token = set_analysis_period(PeriodContext.from_strings("2026-09-01", "2026-09-07"))
    try:
        await agent.run()
    finally:
        reset_analysis_period(token)

    stats = next(kwargs for tool, kwargs in calls if tool == "wb_advert_stats")
    assert stats["date_from"] == "2026-09-01"
    assert stats["date_to"] == "2026-09-07"


@pytest.mark.asyncio
async def test_finance_report_receives_selected_period():
    ctx = SimpleNamespace(
        llm=SimpleNamespace(enabled=False),
        worker=None,
    )
    agent = FinanceAgent(ctx)  # type: ignore[arg-type]
    calls = []

    async def fake_call(tool, **kwargs):
        calls.append((tool, kwargs))
        if tool == "wb_finance_balance":
            return {}
        if tool == "wb_finance_report":
            return {"data": []}
        raise AssertionError(tool)

    agent.call = fake_call  # type: ignore[method-assign]
    token = set_analysis_period(PeriodContext.from_strings("2026-09-03", "2026-09-09"))
    try:
        await agent.run()
    finally:
        reset_analysis_period(token)

    report = next(kwargs for tool, kwargs in calls if tool == "wb_finance_report")
    assert report["date_from"] == "2026-09-03"
    assert report["date_to"] == "2026-09-09"


@pytest.mark.asyncio
async def test_inventory_uses_current_stock_and_only_period_sales():
    ctx = SimpleNamespace(
        policy=SimpleNamespace(thresholds={"inventory": {}}),
    )
    agent = InventoryAgent(ctx)  # type: ignore[arg-type]

    async def fake_call(tool, **kwargs):
        if tool == "wb_stats_stocks":
            return {"data": [{"nmId": 101, "quantity": 14}]}
        if tool == "wb_stats_sales":
            return {
                "data": [
                    {"nmId": 101, "saleID": "in", "date": "2026-09-02", "quantity": 1},
                    {"nmId": 101, "saleID": "out", "date": "2026-09-10", "quantity": 1},
                ]
            }
        raise AssertionError(tool)

    agent.call = fake_call  # type: ignore[method-assign]
    token = set_analysis_period(PeriodContext.from_strings("2026-09-01", "2026-09-07"))
    try:
        result = await agent.run()
    finally:
        reset_analysis_period(token)

    coverage = dict(result.snapshots)["coverage"]["101"]
    assert coverage["stock"] == 14
    assert coverage["sales_period"] == 1
    assert coverage["sales_period_days"] == 7
    assert coverage["daily_sales"] == pytest.approx(1 / 7)
    assert coverage["stock_scope"] == "current_snapshot"
    assert coverage["velocity_scope"] == "selected_period"
