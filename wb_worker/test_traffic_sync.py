import unittest
import csv
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import traffic_sync
from traffic_sync import _campaign_ids, _stats_rows, _funnel_rows, _upsert_rows


class TrafficParsingTest(unittest.TestCase):
    def test_campaign_status_filter(self):
        payload = {"adverts": [
            {"status": 9, "advert_list": [{"advertId": 12}]},
            {"status": 7, "advert_list": [{"advertId": 13}]},
            {"status": 4, "advert_list": [{"advertId": 14}]}
        ]}
        self.assertEqual(_campaign_ids(payload), [12, 13])

    def test_only_explicit_sku_and_no_zone_from_app_type(self):
        data = [{"advertId": 12, "days": [{"date": "2026-09-24",
                 "apps": [{"appType": 32, "nms": [
                     {"nmId": 42, "views": 100, "clicks": 3, "sum": 9},
                     {"views": 20, "clicks": 1}]}]}]}]
        rows = _stats_rows("ap", data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][3], 42)
        self.assertEqual(rows[0][4:6], [100, 3])
        self.assertEqual(rows[0][-2], "EXACT_CAMPAIGN_NM_DAY")
        self.assertEqual(len(rows[0]), 13)

    def test_platform_rows_are_aggregated(self):
        data = [{"advertId": 12, "days": [{"date": "2026-09-24", "apps": [
            {"appType": 1, "nms": [{"nmId": 42, "views": 100, "clicks": 3, "sum": 9}]},
            {"appType": 32, "nms": [{"nmId": 42, "views": 20, "clicks": 1, "sum": 2}]}
        ]}]}]
        rows = _stats_rows("ap", data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][4:6], [120, 4])
        self.assertEqual(rows[0][8], 11)

    def test_funnel_product_history(self):
        data = [{"product": {"nmId": 42, "vendorCode": "MY-42"},
                 "history": [{"date": "2026-09-24", "openCount": 12,
                              "cartCount": 2, "orderCount": 1, "orderSum": 500}]}]
        rows = _funnel_rows("ap", data)
        self.assertEqual(rows[0][2:7], [42, "MY-42", 12, 2, 1])

    def test_upsert_retains_older_history_and_replaces_overlap(self):
        old = [
            ["ap", "2026-09-01", "10", "20", "1", "2"],
            ["ap", "2026-10-08", "10", "20", "3", "4"],
        ]
        new = [["ap", "2026-10-08", "10", "20", "30", "40"]]
        merged = _upsert_rows(old, new, (0, 1, 2, 3))
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0][1], "2026-09-01")
        self.assertEqual(merged[1][4:], ["30", "40"])

    def test_poll_snapshots_are_daily_cumulative_not_hourly_facts(self):
        day = "2026-10-09"
        observed = datetime.fromisoformat("2026-10-09T13:00:00+03:00")
        ads = [["ap", day, 1, 20, 100, 10, 2, 1, 50, 200,
                "WB /adv/v3/fullstats", "EXACT_CAMPAIGN_NM_DAY", "SKU20"]]
        funnel = [["ap", day, 20, "SKU20", 10, 2, 1, 50,
                   "WB Analytics products/history"]]
        ad_poll, funnel_poll = traffic_sync._current_day_poll_rows(
            observed, day, ads, funnel, {"ap"}, {"ap"}
        )
        self.assertEqual(len(ad_poll), 1)
        self.assertEqual(ad_poll[0][11:15], [0.1, 5.0, 0.25, 4.0])
        self.assertEqual(ad_poll[0][-1], "DAILY_CUMULATIVE_OBSERVED_AT_POLL")
        self.assertEqual(funnel_poll[0][8:11], [0.2, 0.5, 0.1])
        # A missing source date stays absent instead of being filled with zero.
        _, no_day_funnel = traffic_sync._current_day_poll_rows(observed, day, ads, [], {"ap"}, {"ap"})
        self.assertEqual(no_day_funnel, [])


class TrafficSyncPersistenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_funnel_and_incremental_history_persistence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "traffic"
            shop_dir = data_dir / "ap"
            shop_dir.mkdir(parents=True)
            traffic_sync._atomic_csv(shop_dir / "campaign_sku_day.csv", traffic_sync.COLUMNS, [
                ["ap", "2026-09-01", 1, 20, 10, 1, 0, 0, 0, 0,
                 "WB /adv/v3/fullstats", "EXACT_CAMPAIGN_NM_DAY", "SKU20"]
            ])
            traffic_sync._atomic_csv(shop_dir / "funnel_sku_day.csv", traffic_sync.FUNNEL_COLUMNS, [
                ["ap", "2026-09-01", 20, "SKU20", 4, 1, 0, 0, "WB Analytics products/history"]
            ])

            def rows(path):
                with path.open("r", encoding="utf-8-sig", newline="") as stream:
                    return list(csv.reader(stream))[1:]

            poll_date = datetime.now(traffic_sync.ZoneInfo("Europe/Moscow")).date().isoformat()

            async def fake_request(client, token, method, url, **kwargs):
                if url.endswith("/adv/v1/promotion/count"):
                    return {"adverts": [{"status": 9, "advert_list": [{"advertId": 7}]}]}
                if url.endswith("/adv/v3/fullstats"):
                    return [{"advertId": 7, "days": [{"date": poll_date, "apps": [{
                        "nms": [{"nmId": 20, "views": 5, "clicks": 1}]
                    }]}]}]
                if url.endswith("/api/v2/list/goods/filter"):
                    return {"data": {"listGoods": [{"nmID": 20}, {"nmID": 30}]}}
                if url.endswith("/api/analytics/v3/sales-funnel/products/history"):
                    requested_ids = kwargs["json"]["nmIds"]
                    return [{"product": {"nmId": nm_id, "vendorCode": f"SKU{nm_id}"},
                             "history": [{"date": poll_date, "openCount": 10,
                                          "cartCount": 2, "orderCount": 1, "orderSum": 50}]}
                            for nm_id in requested_ids]
                raise AssertionError(f"Unexpected URL: {url}")

            with patch.object(traffic_sync, "DATA_DIR", data_dir), \
                 patch.object(traffic_sync, "SHOPS", {"ap": ("ИП АП", "WB_API_TOKEN_AP")}), \
                 patch.object(traffic_sync, "_request", side_effect=fake_request), \
                 patch.dict(os.environ, {"WB_API_TOKEN_AP": "test-token"}):
                traffic_sync._last_fullstats = 0
                first = await traffic_sync.sync_once()
                self.assertTrue(first["ok"])
                self.assertEqual((date.fromisoformat(first["period"][1]) -
                                  date.fromisoformat(first["period"][0])).days, 30)
                self.assertEqual((date.fromisoformat(first["funnel_period"][1]) -
                                  date.fromisoformat(first["funnel_period"][0])).days, 6)
                self.assertEqual(first["shops"]["ap"]["funnel_nm_ids"], 2)
                self.assertEqual(first["ads_poll_rows"], 1)
                self.assertEqual(first["funnel_poll_rows"], 1)
                self.assertEqual(len(rows(shop_dir / "campaign_sku_day.csv")), 2)
                self.assertEqual(len(rows(shop_dir / "funnel_sku_day.csv")), 3)
                ads_poll = rows(data_dir / "ads_poll_snapshot.csv")
                funnel_poll = rows(data_dir / "funnel_poll_snapshot.csv")
                self.assertEqual(len(ads_poll), 1)
                self.assertEqual(len(funnel_poll), 1)
                self.assertEqual(ads_poll[0][16], "DAILY_CUMULATIVE_OBSERVED_AT_POLL")

                traffic_sync._last_fullstats = 0
                second = await traffic_sync.sync_once()
                self.assertTrue(second["ok"])
                self.assertEqual(len(rows(shop_dir / "campaign_sku_day.csv")), 2)
                self.assertEqual(len(rows(shop_dir / "funnel_sku_day.csv")), 3)


if __name__ == "__main__":
    unittest.main()
