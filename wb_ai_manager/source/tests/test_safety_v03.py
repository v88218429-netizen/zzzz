from wb_control_center.config import Settings, load_policy
from wb_control_center.models import ActionProposal
from wb_control_center.policy import PolicyEngine


def engine(mode: str = "shadow", force_read_only: bool = True) -> PolicyEngine:
    return PolicyEngine(Settings(operation_mode=mode, force_read_only=force_read_only), load_policy())


def evidence():
    return {"sources": [{"name": "live_wb", "read_at": "2026-09-18T10:00:00Z"}], "facts": {"current_bid_rub": 50}}


def bid_action(rub: float, change_pct: float):
    return ActionProposal(
        agent="test",
        tool="wb_advert_bids_set",
        arguments={
            "bids": [{"advert_id": 7, "nm_bids": [{"nm_id": 11, "bid_kopecks": int(rub * 100), "placement": "combined"}]}],
            "_change_pct": change_pct,
            "_evidence": evidence(),
        },
        reason="test",
        risk="medium",
    )


def test_two_hundred_thousand_ruble_bid_is_blocked():
    ok, why = engine().validate_action(bid_action(200_000, 5))
    assert not ok
    assert "absolute cap" in why


def test_safe_bid_inside_step_and_absolute_cap_can_be_staged_as_recommendation():
    ok, why = engine().validate_action(bid_action(52, 4))
    assert ok, why


def test_big_percentage_jump_is_blocked_even_if_absolute_value_is_small():
    ok, why = engine().validate_action(bid_action(60, 20))
    assert not ok
    assert "hard cap" in why


def test_money_write_without_evidence_is_blocked():
    a = bid_action(52, 4)
    a.arguments.pop("_evidence")
    ok, why = engine().validate_action(a)
    assert not ok
    assert "evidence" in why


def test_cluster_bid_uses_rubles_and_is_capped():
    a = ActionProposal(
        agent="test",
        tool="wb_advert_cluster_bids",
        arguments={
            "bids": [{"advert_id": 7, "nm_id": 11, "norm_query": "ведро", "bid": 200_000}],
            "_change_pct": 5,
            "_evidence": evidence(),
        },
        reason="test",
        risk="medium",
    )
    ok, why = engine().validate_action(a)
    assert not ok
    assert "absolute cap" in why


def test_shadow_mode_never_executes():
    ok, why = engine("shadow").execution_allowed()
    assert not ok
    assert "read-only" in why


def test_read_only_fuse_blocks_even_if_mode_is_accidentally_approval():
    ok, why = engine("approval", force_read_only=True).execution_allowed()
    assert not ok
    assert "read-only" in why


def test_this_release_stays_read_only_even_if_env_fuse_is_manually_disabled():
    ok, why = engine("approval", force_read_only=False).execution_allowed()
    assert not ok
    assert "read-only build fuse" in why


import math


def test_non_finite_money_values_are_always_blocked():
    evidence={"sources":[{"name":"test"}],"facts":{"x":1}}
    cases=[
        ActionProposal(agent="x",tool="wb_advert_bids_set",arguments={"bids":[{"advert_id":1,"nm_bids":[{"nm_id":1,"bid_kopecks":math.nan,"placement":"combined"}]}],"_change_pct":0,"_evidence":evidence},reason="x",risk="medium"),
        ActionProposal(agent="x",tool="wb_advert_bids_set",arguments={"bids":[{"advert_id":1,"nm_bids":[{"nm_id":1,"bid_kopecks":100,"placement":"combined"}]}],"_change_pct":math.nan,"_evidence":evidence},reason="x",risk="medium"),
        ActionProposal(agent="x",tool="wb_advert_cluster_bids",arguments={"bids":[{"bid":math.nan}],"_change_pct":0,"_evidence":evidence},reason="x",risk="medium"),
        ActionProposal(agent="x",tool="wb_advert_deposit",arguments={"amount":math.nan,"_evidence":evidence},reason="x",risk="medium"),
        ActionProposal(agent="x",tool="wb_prices_set",arguments={"_change_pct":math.nan,"_evidence":evidence},reason="x",risk="medium"),
        ActionProposal(agent="x",tool="wb_prices_set",arguments={"_change_pct":math.inf,"_evidence":evidence},reason="x",risk="medium"),
    ]
    for action in cases:
        ok,_=engine().validate_action(action)
        assert ok is False
