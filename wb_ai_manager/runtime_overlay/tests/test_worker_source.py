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
