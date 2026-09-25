import unittest
from traffic_sync import _campaign_ids, _stats_rows, _funnel_rows


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


if __name__ == "__main__":
    unittest.main()
