from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))

from wb_control_center.config import Settings, load_policy
from wb_control_center.cohort import analyze_order_lifecycle
from wb_control_center.runtime_policy import EDITABLE_DEFAULTS
from wb_control_center.updater import current_version
from wb_control_center.decision_review import DecisionReviewBoard
from wb_control_center.change_tracker import ChangeTracker
from wb_control_center.outcome_evaluator import OutcomeEvaluator
from wb_control_center.investigation import InvestigationEngine
from wb_control_center.model_validation import DemandModelValidator

assert current_version(ROOT)
assert Settings(force_read_only=True).force_read_only is True
assert isinstance(load_policy().raw,dict)
assert 'max_bid_step_pct' in EDITABLE_DEFAULTS
assert callable(analyze_order_lifecycle)
assert DecisionReviewBoard and ChangeTracker and OutcomeEvaluator and InvestigationEngine and DemandModelValidator
print('update smoke: ok')
