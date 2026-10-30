from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path

from wb_control_center.worker_source import WorkerSource


def test_worker_stream_meta_fresh():
    now = datetime.now(timezone.utc)
    meta = WorkerSource.stream_meta({"updatedAt": now.isoformat()}, 120)
    assert meta["status"] == "FRESH"
    assert meta["age_minutes"] is not None


def test_worker_stream_meta_stale():
    old = datetime.now(timezone.utc) - timedelta(hours=5)
    meta = WorkerSource.stream_meta({"updatedAt": old.isoformat()}, 120)
    assert meta["status"] == "STALE"


def test_worker_stream_meta_partial_when_one_shop_failed():
    now = datetime.now(timezone.utc)
    meta = WorkerSource.stream_meta({
        "finishedAt": now.isoformat(),
        "phase": "done",
        "shops": {"AP": {"ok": True}, "AA": {"ok": False}},
    }, 480)
    assert meta["status"] == "PARTIAL"


def test_worker_stream_meta_unreliable_without_fact_time():
    meta = WorkerSource.stream_meta({"phase": "done"}, 120)
    assert meta["status"] == "UNRELIABLE"


def test_optimizer_bundle_uses_separate_encrypted_github_state_branch():
    import inspect
    source = inspect.getsource(WorkerSource.optimizer_bundle)
    assert "optimizer-state/state" in source
    assert "optimizer-dashboard.tar.gz.enc" in source
    assert "optimizer-dashboard.key.enc" in source
    assert "schema_version" in source


def test_optimizer_sweep_progress_contains_aggregate_counts_only():
    runtime_path = Path(__file__).resolve().parents[1] / "scripts" / "github_runtime.py"
    spec = importlib.util.spec_from_file_location("github_runtime_test", runtime_path)
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    optimizer_sweep_progress = runtime.optimizer_sweep_progress

    result = optimizer_sweep_progress({"shops": {
        "ap": {"sweep": {"sweep_id": 2, "sweep_campaigns_seen": 8, "sweep_campaigns_total": 45,
                         "sweep_products_seen": 9, "sweep_products_total": 52, "sweep_complete": False,
                         "campaign_ids": [123], "product_ids": [456]}},
        "aa": {"sweep": {"sweep_id": 1, "sweep_campaigns_seen": 10, "sweep_campaigns_total": 10,
                         "sweep_products_seen": 10, "sweep_products_total": 10, "sweep_complete": True}},
    }})
    assert result == {
        "ap": {"sweep_id": 2, "campaigns_seen": 8, "campaigns_total": 45,
               "products_seen": 9, "products_total": 52, "complete": False},
        "aa": {"sweep_id": 1, "campaigns_seen": 10, "campaigns_total": 10,
               "products_seen": 10, "products_total": 10, "complete": True},
    }
