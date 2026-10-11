"""Offline checks for private, cabinet-independent WB search publishing."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import github_search_snapshot_sync as search


def factual_snapshot(cabinet: str) -> dict:
    return {
        "created_at": "2026-10-09T10:00:00+00:00",
        "period_end": "2026-10-08",
        "trust_status": "FACTUAL_WB_ANALYTICS",
        "data": {
            f"100|{cabinet} query": {
                "source": "wb_search_report",
                "nm_id": 100,
                "name": "Товар",
                "query": f"{cabinet} query",
                "position": 4.0,
                "frequency": 100.0,
            }
        },
    }


class SearchSnapshotTests(unittest.TestCase):
    def test_blocked_cabinet_does_not_discard_air_facts(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "WB_API_TOKEN_AA": "air-test-token",
            "WB_API_TOKEN_YV": "hoz-test-token",
        }):
            def fake_ids(token):
                return [100]

            def fake_snapshot(cabinet, _token, _ids):
                if cabinet == "hozyushka":
                    raise RuntimeError("JAM_REQUIRED_PUBLIC_SERP_BLOCKED")
                return factual_snapshot(cabinet)

            with patch.object(search, "nm_ids", side_effect=fake_ids), patch.object(search, "build_snapshot", side_effect=fake_snapshot):
                result = search.collect_snapshots(Path(tmp))

            saved = json.loads((Path(tmp) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(result["available_cabinets"], 1)
            self.assertEqual(saved["cabinets"]["air"]["status"], "FACTUAL")
            self.assertEqual(saved["cabinets"]["hozyushka"]["reason_code"], "JAM_REQUIRED_PUBLIC_SERP_BLOCKED")
            self.assertEqual(json.loads((Path(tmp) / "hozyushka.json").read_text())["trust_status"], "UNAVAILABLE")

    def test_publish_sends_only_verified_cabinet_rows_to_private_bridge(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "WB_API_TOKEN_AA": "air-test-token",
            "WB_API_TOKEN_YV": "hoz-test-token",
        }):
            def fake_ids(_token):
                return [100]

            def fake_snapshot(cabinet, _token, _ids):
                if cabinet == "hozyushka":
                    raise RuntimeError("JAM_REQUIRED_PUBLIC_SERP_BLOCKED")
                return factual_snapshot(cabinet)

            with patch.object(search, "nm_ids", side_effect=fake_ids), patch.object(search, "build_snapshot", side_effect=fake_snapshot):
                search.collect_snapshots(Path(tmp))

            received = []

            def fake_bridge(request):
                received.append(request)
                if len(received) == 1:
                    return httpx.Response(
                        302,
                        headers={"Location": "https://bridge.invalid/exec/echo"},
                        request=request,
                    )
                return httpx.Response(
                    200,
                    json={"ok": True, "search_rows": 1},
                    request=request,
                )

            real_client = httpx.Client

            def fake_client(**kwargs):
                self.assertTrue(kwargs["follow_redirects"])
                return real_client(transport=httpx.MockTransport(fake_bridge), **kwargs)

            with patch.object(search.httpx, "Client", side_effect=fake_client):
                counts = search.publish_snapshots(Path(tmp), "https://bridge.invalid/exec", "bridge-test-key")

            self.assertEqual(counts, {"air": 1})
            self.assertEqual(len(received), 2)
            self.assertEqual(received[0].method, "POST")
            payload = json.loads(received[0].content.decode("utf-8"))
            self.assertEqual(payload["action"], "publish_search_positions")
            self.assertEqual(payload["cabinet"], "air")
            self.assertEqual(payload["token"], "bridge-test-key")
            self.assertEqual(received[1].method, "GET")
            self.assertEqual(received[1].url.path, "/exec/echo")

    def test_full_acceptance_stays_red_when_one_source_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "WB_API_TOKEN_AA": "air-test-token",
            "WB_API_TOKEN_YV": "hoz-test-token",
        }):
            with patch.object(search, "nm_ids", return_value=[100]), patch.object(
                search, "build_snapshot", side_effect=[factual_snapshot("air"), RuntimeError("JAM_REQUIRED_PUBLIC_SERP_BLOCKED")]
            ):
                search.collect_snapshots(Path(tmp))
            with self.assertRaisesRegex(RuntimeError, "hozyushka:JAM_REQUIRED_PUBLIC_SERP_BLOCKED"):
                search.require_all_sources(Path(tmp))


if __name__ == "__main__":
    unittest.main()
