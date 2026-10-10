import unittest
from campaign_state import parse, SOURCE, QUALITY


class CampaignStateTest(unittest.TestCase):
    def test_parse_status_payment_bids_and_timestamp(self):
        obj={"adverts":[{"id":123,"status":9,"bid_type":"manual",
                "currency":"RUB","settings":{"name":"Реклама тазы","payment_type":"cpc",
                   "placements":{"search":True,"recommendations":False}},
                "timestamps":{"updated":"2026-10-10T18:00:00+03:00"},
                "nm_settings":[{"nm_id":456,
                    "bids_kopecks":{"search":1100,"recommendations":0}},
                   {"nm_id":789,"bids_kopecks":{"search":None}}]}]}
        rows=parse("yv",obj,"2026-10-10T18:02:00+03:00")
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0][0:9],["yv",123,456,"Реклама тазы",9,
                                        "manual","cpc","RUB",1100])
        self.assertEqual(rows[0][9],0)
        self.assertEqual(rows[1][8],"")
        self.assertEqual(rows[0][-2:], [SOURCE,QUALITY])

    def test_reject_duplicate_bad_or_unknown_settings(self):
        with self.assertRaises(ValueError):
            parse("ap",None,"x")
        with self.assertRaises(ValueError):
            parse("ap",{"adverts":[{"id":1,"status":9,
                    "nm_settings":[{"nm_id":2},{"nm_id":2}]}]},"x")
        with self.assertRaises(ValueError):
            parse("ap",{"adverts":[{"id":1,"status":9,
                    "nm_settings":[{"nm_id":2,"bids_kopecks":{"search":-1}}]}]},"x")


if __name__=="__main__":
    unittest.main()
