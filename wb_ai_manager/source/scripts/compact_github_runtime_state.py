#!/usr/bin/env python3
"""Bound GitHub Runtime snapshot history before each encrypted checkpoint.

Only snapshot history that no runtime consumer reads beyond the newest rows is
trimmed. Advertising sweep recovery keeps its full 500-snapshot lookback.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_KEEP = 12
PERIOD_AUDIT_KEEP = 10
DECISION_REVIEW_KEEP = 3
AD_SWEEP_KEEP = 500
VACUUM_MIN_FREE_PAGES = 2048
VACUUM_FREE_RATIO = 0.02


def _database_bytes(path: Path) -> int:
    return sum(
        candidate.stat().st_size
        for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm"))
        if candidate.exists()
    )


def compact_database(path: Path) -> dict[str, Any]:
    """Retain required checkpoints and reclaim large obsolete snapshot pages."""
    before_bytes = _database_bytes(path)
    con = sqlite3.connect(path, timeout=120)
    try:
        con.execute("PRAGMA busy_timeout=120000")
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='snapshots'"
        ).fetchone()
        if not exists:
            return {
                "database": path.name,
                "snapshots_before": 0,
                "snapshots_deleted": 0,
                "bytes_before": before_bytes,
                "bytes_after": _database_bytes(path),
                "vacuumed": False,
            }

        before_rows = int(con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0])
        deleted = con.execute(
            """
            DELETE FROM snapshots
            WHERE id IN (
                SELECT id FROM (
                    SELECT id, source, snapshot_key,
                           ROW_NUMBER() OVER (
                               PARTITION BY source, snapshot_key ORDER BY id DESC
                           ) AS row_number
                    FROM snapshots
                )
                WHERE row_number > CASE
                    WHEN source = 'advertising_optimizer'
                         AND snapshot_key IN ('deep_scan', 'deep_scan_cursor')
                        THEN ?
                    WHEN source = 'period_audit' THEN ?
                    WHEN source = 'decision_review' AND snapshot_key = 'latest' THEN ?
                    ELSE ?
                END
            )
            """,
            (AD_SWEEP_KEEP, PERIOD_AUDIT_KEEP, DECISION_REVIEW_KEEP, DEFAULT_KEEP),
        ).rowcount
        con.commit()

        page_count = int(con.execute("PRAGMA page_count").fetchone()[0])
        free_pages = int(con.execute("PRAGMA freelist_count").fetchone()[0])
        should_vacuum = bool(
            deleted
            and free_pages >= max(VACUUM_MIN_FREE_PAGES, int(page_count * VACUUM_FREE_RATIO))
        )
        checkpoint = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint and int(checkpoint[0]) != 0:
            raise RuntimeError(f"SQLite WAL checkpoint is busy for {path.name}")
    finally:
        con.close()

    if should_vacuum:
        vacuum = sqlite3.connect(path, timeout=120)
        try:
            vacuum.execute("PRAGMA busy_timeout=120000")
            vacuum.execute("VACUUM")
            checkpoint = vacuum.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint and int(checkpoint[0]) != 0:
                raise RuntimeError(f"SQLite WAL checkpoint is busy after VACUUM for {path.name}")
        finally:
            vacuum.close()

    return {
        "database": path.name,
        "snapshots_before": before_rows,
        "snapshots_deleted": int(deleted),
        "bytes_before": before_bytes,
        "bytes_after": _database_bytes(path),
        "vacuumed": should_vacuum,
    }


def compact_runtime_state(data_dir: Path) -> list[dict[str, Any]]:
    databases = sorted(data_dir.rglob("control_center.sqlite3"))
    if not databases:
        raise FileNotFoundError(f"No runtime databases found under {data_dir}")
    return [compact_database(path) for path in databases]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data/github-runtime"))
    args = parser.parse_args()
    results = compact_runtime_state(args.data_dir)
    print(json.dumps({"databases_compacted": len(results), "results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
