"""Regression checks for traffic collection aggregation and campaign selection."""
import unittest

from traffic_sync import _campaign_ids, _stats_rows


class TrafficSyncTests(unittest.TestCase):
    def test_campaign_status_filter(self):
        payload = {"adverts": [
            {"status": 9, "advert_list": [{"advertId": 42}]},
            {"status": 4, "advert_list": [{"advertId": 43}]},
            {"status": 11, "advert_list": [{"advertId": 42}]},
        ]}
        self.assertEqual(_campaign_ids(payload), [42])

    def test_aggregate_platforms_without_double_count(self):
        payload = [{"advertId": 42, "days": [{"date": "2026-10-08",
            "apps": [{"nms": [{"nmId": 9, "views": 100, "clicks": 5,
                               "atbs": 2, "orders": 1, "sum": 20,
                               "sum_price": 100}]},
                     {"nms": [{"nmId": 9, "views": 200, "clicks": 10,
                               "atbs": 4, "orders": 2, "sum": 30,
                               "sum_price": 200}]}]}]}]
        rows = _stats_rows("ap", payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][:10],
                         ["ap", "2026-10-08", 42, 9, 300, 15, 6, 3, 50, 300])
        self.assertEqual(rows[0][11], "EXACT_CAMPAIGN_NM_DAY")


if __name__ == "__main__":
    unittest.main()
