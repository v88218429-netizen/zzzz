from wb_control_center.metrics import extract_ad_metrics, extract_campaign_ids, fingerprint


def test_extract_campaign_ids():
    data = [{"advertId": 101, "nmId": 999}, {"advertId": 102}]
    assert extract_campaign_ids(data) == [101, 102]


def test_extract_ad_metrics():
    data = [{"advertId": 10, "sum": 1000, "orders": 5, "sum_price": 10000, "clicks": 100, "views": 1000}]
    rows = extract_ad_metrics(data)
    assert rows
    assert rows[0]["advert_id"] == 10
    assert round(rows[0]["drr_pct"], 1) == 10.0


def test_fingerprint_stable():
    assert fingerprint({"b": 2, "a": 1}) == fingerprint({"a": 1, "b": 2})
