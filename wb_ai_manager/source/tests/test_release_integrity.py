from __future__ import annotations

import json
import os
import re
import stat
import tomllib
from pathlib import Path

import wb_control_center
from wb_control_center.updater import current_version

ROOT = Path(__file__).resolve().parents[1]


def test_release_versions_are_aligned():
    version = current_version(ROOT)
    assert version == wb_control_center.__version__
    assert tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"] == version
    assert json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))["version"] == version


def test_auto_launcher_resets_live_state_and_never_sources_dotenv():
    text = (ROOT / "scripts/launch/auto_readonly.command").read_text(encoding="utf-8")
    assert "DATA_DIR='./data'" in text
    assert "WB_MODE='live'" in text
    assert "source .env" not in text
    assert "p.chmod(0o600)" in text


def test_bootstrap_migrates_before_blank_env_and_recovers_broken_venv():
    text = (ROOT / "scripts/bootstrap_macos.sh").read_text(encoding="utf-8")
    migrate = text.index("scripts/migrate_previous_env.py")
    blank = text.index("cp .env.example .env")
    assert migrate < blank
    assert "chmod 600 .env" in text
    assert "[ ! -x .venv/bin/python ]" in text
    assert "rm -rf .venv" in text


def test_plugin_mcp_is_local_and_has_no_production_placeholder():
    cfg = json.loads((ROOT / "mcp.json").read_text(encoding="utf-8"))
    server = cfg["mcpServers"]["wb-ai-manager"]
    assert server == {"type": "stdio", "command": "./scripts/plugin_mcp_stdio.sh"}
    assert "YOUR-PRODUCTION-DOMAIN" not in (ROOT / "mcp.json").read_text(encoding="utf-8")
    if os.name != "nt":
        mode = (ROOT / "scripts/plugin_mcp_stdio.sh").stat().st_mode
        assert mode & stat.S_IXUSR


def test_health_payload_is_not_hardcoded_to_shadow_labels():
    text = (ROOT / "src/wb_control_center/api.py").read_text(encoding="utf-8")
    assert '"operation_mode": center.policy_engine.operation_mode' in text
    assert '"read_only": not center.policy_engine.execution_allowed()[0]' in text



def test_raw_mcp_example_does_not_bypass_safe_gateway():
    cfg = json.loads((ROOT / ".mcp.json.example").read_text(encoding="utf-8"))
    server = cfg["mcpServers"]["wb-ai-manager"]
    assert server["command"] == "./scripts/plugin_mcp_stdio.sh"
    assert "wb-mcp" not in json.dumps(cfg)


def test_read_only_build_fuse_is_code_level_not_only_env_level():
    from wb_control_center.policy import READ_ONLY_BUILD
    assert READ_ONLY_BUILD is True


def test_launchers_write_secret_values_without_regex_replacement_interpretation():
    for rel in ["scripts/launch/live_readonly.command", "scripts/launch/configure_connections.command"]:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "lambda _m: f'{k}={v}'" in text


def test_auto_first_run_surfaces_connection_setup_when_wb_token_missing():
    text = (ROOT / "scripts/launch/auto_readonly.command").read_text(encoding="utf-8")
    assert "HAS_WB_TOKEN=" in text
    assert "./scripts/launch/configure_connections.command" in text
    menu = (ROOT / "START_WB_AI_MANAGER.command").read_text(encoding="utf-8")
    assert "5  Подключения" in menu


def test_blocking_sheet_and_drive_operations_are_offloaded_from_api_event_loop():
    text = (ROOT / "src/wb_control_center/api.py").read_text(encoding="utf-8")
    assert "await asyncio.to_thread(discovery.discover)" in text
    assert "await asyncio.to_thread(portfolio.refresh)" in text
    assert "await asyncio.to_thread(AutoSheets().refresh_core_via_browser)" in text


def test_mcp_install_error_references_existing_main_launcher():
    text = (ROOT / "src/wb_control_center/mcp_client.py").read_text(encoding="utf-8")
    assert "SETUP.command" not in text
    assert "START_WB_AI_MANAGER.command" in text


def test_auto_sheets_is_not_blocked_by_unrelated_chrome_downloads():
    text = (ROOT / "src/wb_control_center/auto_sheets.py").read_text(encoding="utf-8")
    assert "glob('*.crdownload')" not in text



def test_demo_is_network_independent_and_disables_public_storefront():
    cli = (ROOT / "src/wb_control_center/cli.py").read_text(encoding="utf-8")
    launcher = (ROOT / "scripts/launch/demo.command").read_text(encoding="utf-8")
    assert 'enable_public_wb_search=False' in cli
    assert 'remote_policy_url=""' in cli
    assert "'ENABLE_PUBLIC_WB_SEARCH':'false'" in launcher


def test_optional_public_search_has_bounded_failure_behavior():
    public = (ROOT / "src/wb_control_center/public_wb.py").read_text(encoding="utf-8")
    competitors = (ROOT / "src/wb_control_center/agents/competitors.py").read_text(encoding="utf-8")
    assert "AsyncClient(timeout=8" in public
    assert "consecutive_failures >= 2" in competitors



def test_auto_refresh_prefers_configured_bridge_before_browser_exports():
    text = (ROOT / "scripts/auto_refresh_sheets.py").read_text(encoding="utf-8")
    assert "settings.google_sheets_bridge_url and settings.google_sheets_bridge_key" in text
    assert "portfolio.refresh()" in text
    assert text.index("portfolio.refresh()") < text.index("refresh_core_via_browser()")


def test_runtime_supervisor_handles_duplicate_instance_and_dead_child():
    text = (ROOT / "scripts/runtime_supervisor.py").read_text(encoding="utf-8")
    assert "def health_info(" in text
    assert "child.poll() is not None" in text
    assert "WB AI Manager уже запущен" in text
    assert "wait_health(settings.app_port,child)" in text



def test_duplicate_instance_check_happens_before_any_startup_update():
    text = (ROOT / "scripts/runtime_supervisor.py").read_text(encoding="utf-8")
    main=text[text.index("def main()->int:"):]
    assert main.index("existing=health_info(boot_port)") < main.index("preflight_restore_interrupted_update()")
    assert main.index("existing=health_info(boot_port)") < main.index("if settings.auto_update_enabled:")


def test_both_live_launchers_restore_public_search_after_demo():
    auto = (ROOT / "scripts/launch/auto_readonly.command").read_text(encoding="utf-8")
    live = (ROOT / "scripts/launch/live_readonly.command").read_text(encoding="utf-8")
    assert "ENABLE_PUBLIC_WB_SEARCH='true'" in auto
    assert "'ENABLE_PUBLIC_WB_SEARCH':'true'" in live
    assert "DATA_DIR='./data'" in auto
    assert "'DATA_DIR':'./data'" in live


def test_dashboard_version_is_runtime_derived_not_stale_hardcode():
    html=(ROOT / "src/wb_control_center/ui/dashboard.html").read_text(encoding="utf-8")
    js=(ROOT / "src/wb_control_center/ui/dashboard.js").read_text(encoding="utf-8")
    assert 'v1.1.2' not in html
    assert 'id="app-version-banner"' in html
    assert "$('app-version-banner').textContent=`v${ver}`" in js


def test_supervisor_recovers_interrupted_update_before_checking_new_update():
    text=(ROOT / "scripts/runtime_supervisor.py").read_text(encoding="utf-8")
    assert "preflight_restore_interrupted_update()" in text
    assert "recover_interrupted_update()" in text
    assert text.index("preflight_restore_interrupted_update()") < text.index("from wb_control_center.config import Settings")
    assert text.index("recover_interrupted_update()") < text.index("check=manager.check()")


def test_supervisor_kicks_initial_audit_in_background():
    sup=(ROOT / "scripts/runtime_supervisor.py").read_text(encoding="utf-8")
    api=(ROOT / "src/wb_control_center/api.py").read_text(encoding="utf-8")
    assert "/run-all?background=true" in sup
    assert "async def run_all(background: bool = False)" in api
    assert "asyncio.create_task(_run_all_background())" in api


def test_blocking_admin_network_calls_are_offloaded_from_api_event_loop():
    text=(ROOT / "src/wb_control_center/api.py").read_text(encoding="utf-8")
    assert "await asyncio.to_thread(update_manager.check)" in text
    assert "await asyncio.to_thread(center.remote_policy.get, True)" in text
    assert "runtime_policy = await asyncio.to_thread(center.runtime_policy.get)" in text
    assert "result = await asyncio.to_thread(center.runtime_policy.update_advertising, payload, \"dashboard\")" in text


def test_every_agent_has_schedule_and_run_all_covers_every_agent():
    import ast
    from wb_control_center.agents import AGENT_CLASSES
    from wb_control_center.config import load_policy
    policy=load_policy()
    assert set(policy.schedules) == set(AGENT_CLASSES)
    source=(ROOT / "src/wb_control_center/engine.py").read_text(encoding="utf-8")
    tree=ast.parse(source)
    order=None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            if node.name == "_run_all_once_locked":
                for stmt in node.body:
                    if isinstance(stmt, ast.Assign) and any(isinstance(t,ast.Name) and t.id=="order" for t in stmt.targets):
                        order=ast.literal_eval(stmt.value)
    assert order is not None
    assert set(order) == set(AGENT_CLASSES)
    assert len(order) == len(AGENT_CLASSES) == 19


def test_all_read_only_status_surfaces_include_the_code_level_build_fuse():
    api=(ROOT / "src/wb_control_center/api.py").read_text(encoding="utf-8")
    portfolio=(ROOT / "src/wb_control_center/portfolio.py").read_text(encoding="utf-8")
    assert api.count('"read_only": not center.policy_engine.execution_allowed()[0]') >= 2
    assert '"read_only": bool(READ_ONLY_BUILD or self.settings.force_read_only)' in portfolio


def test_auto_sheets_browser_launcher_is_bounded():
    from pathlib import Path
    source=(Path(__file__).parents[1]/'src/wb_control_center/auto_sheets.py').read_text(encoding='utf-8')
    assert "check=False,timeout=10" in source
    assert "except subprocess.TimeoutExpired" in source


def test_api_shutdown_drains_background_full_audits_before_center_stop():
    from pathlib import Path
    source=(Path(__file__).parents[1]/'src/wb_control_center/api.py').read_text(encoding='utf-8')
    life=source[source.index('async def lifespan'):source.index('app = FastAPI')]
    assert 'task.cancel()' in life
    assert 'await asyncio.gather(*tasks, return_exceptions=True)' in life
    assert life.index('await asyncio.gather') < life.index('await center.stop()')
