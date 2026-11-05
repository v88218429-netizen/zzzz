from wb_control_center.agents.advertising import _scan_error_record


def test_scan_error_record_identifies_campaign_product_and_redacts_secrets():
    result = _scan_error_record(
        987654,
        "wb_advert_bids_recommendations",
        {"_error": "HTTP 403 token=secret Bearer private https://example.test/path?token=value"},
        273197463,
    )

    assert result["campaign_id"] == 987654
    assert result["nm_id"] == 273197463
    assert result["endpoint"] == "wb_advert_bids_recommendations"
    assert "secret" not in result["error"]
    assert "private" not in result["error"]
    assert "value" not in result["error"]
    assert "[REDACTED]" in result["error"]


def test_scan_error_record_ignores_successful_responses():
    assert _scan_error_record(1, "wb_advert_budget", {"data": []}) is None
