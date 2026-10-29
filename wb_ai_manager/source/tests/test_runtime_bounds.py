from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from wb_control_center.config import Settings
from wb_control_center.engine import ControlCenter
from wb_control_center.mcp_client import WBMCPClient, WBMCPError


class HangingSession:
    async def call_tool(self, tool, arguments=None):
        await asyncio.sleep(10)


@pytest.mark.asyncio
async def test_mcp_call_timeout_resets_session(tmp_path: Path):
    c=WBMCPClient("token",str(tmp_path),call_timeout=0.02)
    c.call_timeout=0.02
    c.session=HangingSession()
    c.tools={"wb_test"}
    with pytest.raises(WBMCPError, match="timed out"):
        await c.call("wb_test",{})
    assert c.session is None


class HangingAgent:
    async def run(self):
        await asyncio.sleep(10)


@pytest.mark.asyncio
async def test_agent_timeout_marks_error_and_releases_lock(tmp_path: Path):
    s=Settings(wb_mode="demo",data_dir=str(tmp_path),remote_policy_url="",auto_update_enabled=False,agent_run_timeout_seconds=0.02)
    c=ControlCenter(s)
    c.settings.agent_run_timeout_seconds=0.02
    c.agents["api_health"]=HangingAgent()
    result=await c.run_agent("api_health")
    assert result["status"] == "error"
    assert "runtime limit" in result["error"]
    assert not c._agent_locks["api_health"].locked()


class RecordingSession:
    def __init__(self): self.arguments=None
    async def call_tool(self, tool, arguments=None):
        self.arguments=arguments
        class Result:
            isError=False
            structured_content={"ok":True}
            content=[]
        return Result()


@pytest.mark.asyncio
async def test_configured_shop_scope_cannot_be_overridden(tmp_path: Path):
    c=WBMCPClient("token",str(tmp_path),shop_id="shop-A")
    session=RecordingSession(); c.session=session; c.tools={"wb_prices"}
    with pytest.raises(WBMCPError, match="tenant/shop mismatch"):
        await c.call("wb_prices",{"shop_id":"shop-B"})
    out=await c.call("wb_prices",{})
    assert out == {"ok":True}
    assert session.arguments["shop_id"] == "shop-A"


@pytest.mark.asyncio
async def test_full_audit_is_single_flight(tmp_path: Path):
    s=Settings(wb_mode="demo",data_dir=str(tmp_path),remote_policy_url="",auto_update_enabled=False)
    c=ControlCenter(s)
    await c._run_all_lock.acquire()
    try:
        out=await c.run_all_once()
    finally:
        c._run_all_lock.release()
    assert out == [{"system":"run_all","status":"skipped","reason":"full audit already running"}]


@pytest.mark.asyncio
async def test_concurrent_action_apply_is_idempotent(tmp_path: Path):
    from wb_control_center.models import ActionProposal
    s=Settings(wb_mode="demo",data_dir=str(tmp_path),remote_policy_url="",auto_update_enabled=False)
    c=ControlCenter(s)
    proposal=ActionProposal(agent="test",tool="wb_test_write",arguments={"x":1},reason="test",risk="safe")
    action_id=c.db.create_action(proposal)
    calls=0
    async def fake_call(tool,args):
        nonlocal calls
        calls+=1
        await asyncio.sleep(0.03)
        return {"ok":True}
    c.wb.call=fake_call
    c.policy_engine.validate_action=lambda proposal: (True,"ok")
    c.policy_engine.execution_allowed=lambda: (True,"ok")
    a,b=await asyncio.gather(c.execute_action(action_id),c.execute_action(action_id))
    assert calls == 1
    assert a["status"] == "executed"
    assert b["status"] == "executed"


@pytest.mark.asyncio
async def test_campaign_pause_rechecks_live_active_state_before_write(tmp_path: Path):
    from wb_control_center.models import ActionProposal
    s=Settings(wb_mode="live",data_dir=str(tmp_path),remote_policy_url="",auto_update_enabled=False)
    c=ControlCenter(s)
    proposal=ActionProposal(agent="test",tool="wb_advert_pause",arguments={"advert_id":77},reason="test",risk="medium")
    action_id=c.db.create_action(proposal)
    calls=[]
    async def fake_call(tool,args):
        calls.append((tool,args))
        if tool == "wb_advert_list":
            return [{"advertId":77,"status":9}]
        return {"ok":True}
    c.wb.call=fake_call
    c.policy_engine.validate_action=lambda proposal:(True,"ok")
    c.policy_engine.execution_allowed=lambda:(True,"ok")
    result=await c.execute_action(action_id)
    assert result["status"] == "executed"
    assert calls[0] == ("wb_advert_list", {"statuses":[9]})
    assert calls[1][0] == "wb_advert_pause"


@pytest.mark.asyncio
async def test_campaign_pause_blocks_when_fresh_read_no_longer_shows_campaign(tmp_path: Path):
    from wb_control_center.models import ActionProposal
    s=Settings(wb_mode="live",data_dir=str(tmp_path),remote_policy_url="",auto_update_enabled=False)
    c=ControlCenter(s)
    proposal=ActionProposal(agent="test",tool="wb_advert_pause",arguments={"advert_id":77},reason="test",risk="medium")
    action_id=c.db.create_action(proposal)
    calls=[]
    async def fake_call(tool,args):
        calls.append((tool,args))
        return []
    c.wb.call=fake_call
    c.policy_engine.validate_action=lambda proposal:(True,"ok")
    c.policy_engine.execution_allowed=lambda:(True,"ok")
    result=await c.execute_action(action_id)
    assert result["status"] == "blocked"
    assert "fresh active-campaign read" in result["reason"]
    assert [x[0] for x in calls] == ["wb_advert_list"]


@pytest.mark.asyncio
async def test_card_recovery_requires_fresh_trash_presence_before_write(tmp_path: Path):
    from wb_control_center.models import ActionProposal
    s=Settings(wb_mode="live",data_dir=str(tmp_path),remote_policy_url="",auto_update_enabled=False)
    c=ControlCenter(s)
    proposal=ActionProposal(agent="dashboard",tool="wb_cards_recover",arguments={"nm_ids":[123]},reason="recover",risk="medium")
    action_id=c.db.create_action(proposal)
    calls=[]
    async def fake_call(tool,args):
        calls.append((tool,args))
        if tool == "wb_cards_trash":
            return {"cards":[{"nmID":123}],"complete":True}
        return {"ok":True}
    c.wb.call=fake_call
    c.policy_engine.validate_action=lambda proposal:(True,"ok")
    c.policy_engine.execution_allowed=lambda:(True,"ok")
    result=await c.execute_action(action_id)
    assert result["status"] == "executed"
    assert [x[0] for x in calls] == ["wb_cards_trash", "wb_cards_recover"]
    assert calls[1][1] == {"nm_ids":[123]}


@pytest.mark.asyncio
async def test_card_recovery_blocks_if_fresh_trash_read_is_incomplete(tmp_path: Path):
    from wb_control_center.models import ActionProposal
    s=Settings(wb_mode="live",data_dir=str(tmp_path),remote_policy_url="",auto_update_enabled=False)
    c=ControlCenter(s)
    proposal=ActionProposal(agent="dashboard",tool="wb_cards_recover",arguments={"nm_ids":[123]},reason="recover",risk="medium")
    action_id=c.db.create_action(proposal)
    calls=[]
    async def fake_call(tool,args):
        calls.append(tool)
        return {"cards":[{"nmID":123}],"complete":False}
    c.wb.call=fake_call
    c.policy_engine.validate_action=lambda proposal:(True,"ok")
    c.policy_engine.execution_allowed=lambda:(True,"ok")
    result=await c.execute_action(action_id)
    assert result["status"] == "blocked"
    assert "fresh complete trash check" in result["reason"]
    assert calls == ["wb_cards_trash"]
