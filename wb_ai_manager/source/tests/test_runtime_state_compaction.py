from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.compact_github_runtime_state import compact_database


class RuntimeSnapshotCompactionTests(unittest.TestCase):
    def test_preserves_advertising_recovery_window_and_latest_runtime_facts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "control_center.sqlite3"
            con = sqlite3.connect(path)
            con.execute(
                "CREATE TABLE snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "created_at TEXT NOT NULL, source TEXT NOT NULL, "
                "snapshot_key TEXT NOT NULL, data_json TEXT NOT NULL)"
            )
            fixtures = [
                ("advertising_optimizer", "deep_scan", 510),
                ("advertising_optimizer", "deep_scan_cursor", 510),
                ("period_audit", "2026-11-01__2026-11-07", 20),
                ("decision_review", "latest", 20),
                ("cards", "card_catalog", 20),
            ]
            expected_latest_ids = {}
            next_id = 0
            for source, key, count in fixtures:
                for index in range(count):
                    next_id += 1
                    con.execute(
                        "INSERT INTO snapshots(created_at,source,snapshot_key,data_json) VALUES(?,?,?,?)",
                        (f"2026-11-11T00:{index % 60:02d}:00+00:00", source, key,
                         json.dumps({"sequence": index, "padding": "x" * 2048})),
                    )
                expected_latest_ids[(source, key)] = next_id
            con.commit()
            con.close()

            result = compact_database(path)

            self.assertEqual(result["snapshots_deleted"], 55)
            con = sqlite3.connect(path)
            for source, key, expected in (
                ("advertising_optimizer", "deep_scan", 500),
                ("advertising_optimizer", "deep_scan_cursor", 500),
                ("period_audit", "2026-11-01__2026-11-07", 10),
                ("decision_review", "latest", 3),
                ("cards", "card_catalog", 12),
            ):
                count, latest = con.execute(
                    "SELECT COUNT(*), MAX(id) FROM snapshots WHERE source=? AND snapshot_key=?",
                    (source, key),
                ).fetchone()
                self.assertEqual(count, expected, (source, key))
                self.assertEqual(latest, expected_latest_ids[(source, key)])
            con.close()


if __name__ == "__main__":
    unittest.main()
