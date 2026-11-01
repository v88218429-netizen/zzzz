from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from wb_control_center.db import Database
from wb_control_center.engine import ControlCenter


def test_interrupt_unfinished_runs_only_closes_stale_running_rows(tmp_path):
    db = Database(tmp_path / "control.sqlite3")
    now = datetime.now(timezone.utc)
    stale_id = db.start_run("stale", (now - timedelta(hours=2)).isoformat())
    recent_id = db.start_run("recent", (now - timedelta(seconds=30)).isoformat())
    finished_id = db.start_run("finished", (now - timedelta(hours=3)).isoformat())
    db.finish_run(finished_id, "ok", (now - timedelta(hours=3) + timedelta(seconds=1)).isoformat())

    cutoff = (now - timedelta(minutes=5)).isoformat()
    finished_at = now.isoformat()
    assert db.interrupt_unfinished_runs(cutoff, finished_at, "recovered") == 1

    rows = {row["id"]: row for row in db.recent_runs(hours=24, limit=20)}
    assert rows[stale_id]["status"] == "interrupted"
    assert rows[stale_id]["finished_at"] == finished_at
    assert rows[stale_id]["error"] == "recovered"
    assert rows[recent_id]["status"] == "running"
    assert rows[finished_id]["status"] == "ok"


@pytest.mark.asyncio
async def test_cancelled_agent_run_is_recorded_as_interrupted(tmp_path):
    class BlockedAgent:
        async def run(self):
            await asyncio.Event().wait()

    center = object.__new__(ControlCenter)
    center.db = Database(tmp_path / "cancelled.sqlite3")
    center.agents = {"probe": BlockedAgent()}
    center._agent_locks = {"probe": asyncio.Lock()}
    center.settings = SimpleNamespace(agent_run_timeout_seconds=60)

    task = asyncio.create_task(center.run_agent("probe"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    row = center.db.recent_runs(hours=24, limit=1)[0]
    assert row["agent"] == "probe"
    assert row["status"] == "interrupted"
    assert row["finished_at"]
    assert "cancelled" in row["error"].lower()
