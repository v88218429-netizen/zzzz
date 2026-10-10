import csv
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

import query_clusters as qc


class QueryClustersTest(unittest.TestCase):
    def test_day_cluster_parses_as_ad_facts(self):
        payload = {"items": [{"advertId": 42, "nmId": 123,
            "dailyStats": [
                {"date": "2026-10-09", "stat": {"normQuery": "таз строительный 90 л",
                    "views": 100, "clicks": 12, "atbs": 3,
                    "orders": 1, "shks": 2, "spend": 45.5, "avgPos": 7.9}},
                {"date": "2026-10-10", "stat": {"normQuery": "таз строительный 90 л",
                    "views": None, "clicks": 6, "spend": 12}},
            ]}]}
        rows = qc.parse_rows("yv", payload)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][:5], ["yv", "2026-10-09", 42, 123, "таз строительный 90 л"])
        self.assertEqual(rows[0][5:12], [100, 12, 3, 1, 2, 45.5, 7.9])
        self.assertEqual(rows[0][15:], [qc.SOURCE, qc.QUALITY])
        self.assertEqual(rows[1][5], "")  # CPC payment can omit impressions
        self.assertEqual(rows[1][12:15], ["", "", ""])  # no CTR/CPC/CPM fabricated

    def test_duplicate_cluster_day_must_fail(self):
        stat = {"date":"2026-10-09", "stat":{"normQuery":"таз"}}
        obj = {"items":[{"advertId":1,"nmId":2,"dailyStats":[stat,stat]}]}
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            qc.parse_rows("ap",obj)

    def test_invalid_payload_is_not_zero_statistics(self):
        for payload in (None, {}, {"items":None}, [], {"items":"bad"}):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    qc.parse_rows("aa",payload)

    def test_partial_rate_limited_collection_retains_verified_chunk(self):
        from datetime import datetime
        class StubClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
        now=datetime.now().date()
        with tempfile.TemporaryDirectory() as temp:
            tmp=Path(temp)/"traffic"
            base_pairs=[{"advertId":i,"nmId":10+i} for i in range(101)]
            response={"items":[{"advertId":1,"nmId":11,"dailyStats":[
                {"date":now.isoformat(),"stat":{"normQuery":"таз","clicks":4,"spend":19}}]}]}
            calls=0
            async def fake_request(*args,**kwargs):
                nonlocal calls
                calls+=1
                if calls==1:
                    return response
                raise RuntimeError("HTTP 429 basic token hourly limit")
            def pairs(shop,_start,_end):
                return base_pairs if shop=="yv" else []
            with patch.object(qc,"DATA_DIR",tmp), \
                 patch.object(qc,"SHOPS",{"yv":("test","TOKEN")}), \
                 patch.dict("os.environ",{"TOKEN":"test-token"}), \
                 patch.object(qc,"active_pairs",side_effect=pairs), \
                 patch.object(qc.httpx,"AsyncClient",return_value=StubClient()), \
                 patch.object(qc,"_request",side_effect=fake_request), \
                 patch.object(qc,"MIN_INTERVAL_SECONDS",0.0), \
                 patch.object(qc,"_last_request",0.0):
                result=asyncio.run(qc.collect())
                self.assertFalse(result["ok"])
                self.assertEqual(result["shops"]["yv"]["coverage"],"PARTIAL_SOURCE_WINDOW")
                self.assertEqual(result["shops"]["yv"]["batches_collected"],1)
                self.assertEqual(result["shops"]["yv"]["batches_expected"],2)
                self.assertEqual(result["shops"]["yv"]["new_rows"],1)
                self.assertEqual(len(qc.existing_rows("yv")),1)
                self.assertEqual(calls,2)

    def test_pairs_are_scoped_to_correct_shop_and_period(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)/"traffic"/"yv"
            folder.mkdir(parents=True)
            with (folder/"campaign_sku_day.csv").open("w",encoding="utf-8-sig",newline="") as stream:
                writer=csv.DictWriter(stream,fieldnames=["shop","date","advert_id","nm_id"])
                writer.writeheader()
                writer.writerows([
                    {"shop":"yv","date":"2026-10-10","advert_id":"111","nm_id":"222"},
                    {"shop":"yv","date":"2026-10-10","advert_id":"111","nm_id":"222"},
                    {"shop":"yv","date":"2026-10-01","advert_id":"333","nm_id":"444"},
                ])
            with patch.object(qc, "DATA_DIR", Path(temp)/"traffic"):
                self.assertEqual(qc.active_pairs("yv","2026-10-04","2026-10-10"),
                                 [{"advertId":111,"nmId":222}])
                self.assertEqual(qc.active_pairs("aa","2026-10-04","2026-10-10"),[])


if __name__ == "__main__":
    unittest.main()
