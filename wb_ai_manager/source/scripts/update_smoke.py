from pathlib import Path
import json
import os
import sys
import tomllib

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))

from wb_control_center import __version__
from wb_control_center.config import Settings, load_policy
from wb_control_center.cohort import analyze_order_lifecycle
from wb_control_center.runtime_policy import EDITABLE_DEFAULTS
from wb_control_center.updater import current_version
from wb_control_center.decision_review import DecisionReviewBoard
from wb_control_center.change_tracker import ChangeTracker
from wb_control_center.outcome_evaluator import OutcomeEvaluator
from wb_control_center.investigation import InvestigationEngine
from wb_control_center.model_validation import DemandModelValidator

version=current_version(ROOT)
assert version
assert __version__ == version, (__version__, version)
pyproject=tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))
assert pyproject['project']['version'] == version
plugin=json.loads((ROOT/'plugin.json').read_text(encoding='utf-8'))
assert plugin.get('version') == version
mcp_cfg=json.loads((ROOT/'mcp.json').read_text(encoding='utf-8'))
server=mcp_cfg.get('mcpServers',{}).get('wb-ai-manager',{})
assert server.get('type') == 'stdio'
assert server.get('command') == './scripts/plugin_mcp_stdio.sh'
if os.name != 'nt':
    assert os.access(ROOT/'START_WB_AI_MANAGER.command', os.X_OK)
    assert os.access(ROOT/'scripts/plugin_mcp_stdio.sh', os.X_OK)
assert Settings(force_read_only=True).force_read_only is True
assert isinstance(load_policy().raw,dict)
assert 'max_bid_step_pct' in EDITABLE_DEFAULTS
assert callable(analyze_order_lifecycle)
assert DecisionReviewBoard and ChangeTracker and OutcomeEvaluator and InvestigationEngine and DemandModelValidator
print('update smoke: ok')
