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


def test_auto_execution_is_never_allowed_even_if_allowlisted():
    p = PolicyEngine(Settings(operation_mode="guarded_auto", auto_actions=True, force_read_only=False), policy())
    a = ActionProposal(agent="x", tool="wb_advert_pause", arguments={"advert_id": 1}, reason="x", risk="medium")
    assert not p.can_auto_execute(a)


def test_irreversible_never_auto():
    p = PolicyEngine(Settings(operation_mode="guarded_auto", auto_actions=True, force_read_only=False), policy())
    a = ActionProposal(agent="x", tool="wb_prices_set", arguments={}, reason="x", risk="safe")
    assert not p.can_auto_execute(a)


def test_manual_writes_require_approval_mode_auth_and_fuse_disabled():
    base = dict(force_read_only=False, dashboard_auth_required=True, dashboard_auth_user="owner", dashboard_auth_password="secret")
    assert PolicyEngine(Settings(operation_mode="approval", **base), policy()).execution_allowed() == (True, "ok")
    assert not PolicyEngine(Settings(operation_mode="shadow", **base), policy()).execution_allowed()[0]
    assert not PolicyEngine(Settings(operation_mode="approval", force_read_only=True, dashboard_auth_required=True, dashboard_auth_user="owner", dashboard_auth_password="secret"), policy()).execution_allowed()[0]
    assert not PolicyEngine(Settings(operation_mode="approval", force_read_only=False), policy()).execution_allowed()[0]


def test_live_write_gate_rejects_demo_and_unapproved_tools():
    from wb_control_center.policy import MANUAL_APPROVAL_TOOLS
    demo = Settings(wb_mode="demo", operation_mode="approval", force_read_only=False,
                    dashboard_auth_required=True, dashboard_auth_user="owner", dashboard_auth_password="secret")
    assert not PolicyEngine(demo, policy()).execution_allowed()[0]
    assert MANUAL_APPROVAL_TOOLS == {"wb_advert_pause", "wb_cards_recover"}
    engine = PolicyEngine(Settings(), policy())
    action = ActionProposal(agent="x", tool="wb_order_cancel", arguments={}, reason="test", risk="irreversible")
    assert engine.validate_action(action) == (False, "tool 'wb_order_cancel' is not enabled for human-approved writes")


def test_card_recovery_requires_one_positive_integer_article():
    engine = PolicyEngine(Settings(), policy())
    valid = ActionProposal(agent="dashboard", tool="wb_cards_recover", arguments={"nm_ids": [123456]}, reason="recover", risk="medium")
    assert engine.validate_action(valid) == (True, "ok")
    for ids in ([], [0], [-1], [True], [1, 2], ["123"]):
        action = ActionProposal(agent="dashboard", tool="wb_cards_recover", arguments={"nm_ids": ids}, reason="recover", risk="medium")
        assert not engine.validate_action(action)[0]
