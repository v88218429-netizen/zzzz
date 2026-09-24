from wb_control_center.config import PolicyConfig, Settings
from wb_control_center.models import ActionProposal
from wb_control_center.policy import PolicyEngine


def policy():
    return PolicyConfig({
        "safety": {
            "auto_execute_tools": ["wb_advert_pause"],
            "always_require_approval": ["wb_prices_set"],
            "limits": {"max_bid_change_pct": 10, "max_price_change_pct": 5},
        }
    })


def test_auto_off():
    p = PolicyEngine(Settings(operation_mode="shadow", auto_actions=False), policy())
    a = ActionProposal(agent="x", tool="wb_advert_pause", arguments={"advert_id": 1}, reason="x", risk="medium")
    assert not p.can_auto_execute(a)


def test_read_only_build_never_auto_executes_even_if_allowlisted():
    p = PolicyEngine(Settings(operation_mode="guarded_auto", auto_actions=True, force_read_only=False), policy())
    a = ActionProposal(agent="x", tool="wb_advert_pause", arguments={"advert_id": 1}, reason="x", risk="medium")
    assert not p.can_auto_execute(a)


def test_irreversible_never_auto():
    p = PolicyEngine(Settings(operation_mode="guarded_auto", auto_actions=True, force_read_only=False), policy())
    a = ActionProposal(agent="x", tool="wb_prices_set", arguments={}, reason="x", risk="safe")
    assert not p.can_auto_execute(a)
