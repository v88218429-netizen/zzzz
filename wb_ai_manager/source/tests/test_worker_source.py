from datetime import datetime, timedelta, timezone

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

def test_runtime_persists_and_restores_optimizer_scan_cursor():
    import inspect
    from scripts import github_runtime

    source = inspect.getsource(github_runtime.run)
    assert "optimizer_reader.optimizer_bundle()" in source
    assert "previous_optimizer_state" in source
    assert '"scan_cursor": optimizer_scan_cursor(cabinet_snapshots_for_shop)' in inspect.getsource(github_runtime.run)
    assert '"scan_cursor": cursor' in inspect.getsource(github_runtime.restore_optimizer_scan_cursors)
