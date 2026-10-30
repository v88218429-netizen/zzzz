from datetime import datetime, timezone

from wb_control_center.cluster_advisor import analyze_cluster_rows


NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def source_row(**overrides):
    row = {
        "shop": "yv", "date": "2026-10-28", "advert_id": "15", "nm_id": "123",
        "norm_query": "таз строительный", "seller_article": "ТАZ-90",
        "campaign_name": "Поиск ручная", "views": "1000", "clicks": "30",
        "atbs": "5", "orders": "1", "spend_rub": "240",
        "cluster_bid_kopecks": "1000", "cluster_state": "active",
        "bid_observed_at": "2026-10-29T11:00:00+03:00",
        "source": "WB /adv/v1/normquery/stats",
        "quality": "FACT_AD_CLUSTER_DAY_NOT_SEARCH_PHRASE",
    }
    row.update(overrides)
    return row


def settings(**overrides):
    row = {"shop": "yv", "advert_id": "15", "nm_id": "123", "payment_type": "cpm", "bid_type": "manual",
           "status": "9", "currency": "RUB", "search_bid_kopecks": "1200", "search_placement": "True",
           "observed_at": "2026-10-29T11:00:00+03:00"}
    row.update(overrides)
    return [row]


def decisions(cap=200):
    return [{
        "decision_key": "advert:15:123:numeric_control",
        "evidence": [{"metric": "max_ad_cost_per_order_rub", "value": cap}],
    }]


def test_exact_cluster_exposes_metrics_and_capped_test_bid_only_with_full_evidence():
    position = {"cards": [{
        "store_id": "yv", "sku": "123", "query": "таз строительный", "position": 18,
        "freshness": {"position": {"status": "fresh"}},
    }]}
    result = analyze_cluster_rows([source_row()], settings(), decisions(), position, now=NOW,
                                 economics_observed_at="2026-10-29T11:00:00+03:00")
    row = result["rows"][0]
    assert row["clicks"] == 30
    assert row["cpa_rub"] == 240
    assert row["current_bid_rub"] == 10
    assert row["current_bid_source"] == "cluster"
    assert row["campaign_search_bid_rub"] == 12
    assert row["target_bid_rub"] == 9
    assert row["action"] == "ТЕСТОВО СНИЗИТЬ СТАВКУ"
    assert row["organic_position"] == 18
    assert row["manual_only"] is True
    assert result["write_mode"] == "read_only_recommendations"


def test_missing_exact_bid_or_economics_blocks_numeric_recommendation():
    result = analyze_cluster_rows([source_row(cluster_bid_kopecks="")], settings(search_bid_kopecks="2000"), [], now=NOW,
                                 economics_observed_at="2026-10-29T11:00:00+03:00")
    row = result["rows"][0]
    assert row["target_bid_rub"] is None
    assert row["current_bid_rub"] is None
    assert row["campaign_search_bid_rub"] == 20
    assert row["action"] == "СОВЕТ ПО СТАВКЕ ЗАБЛОКИРОВАН"
    assert any("не подменяет её" in reason for reason in row["blockers"])
    assert len(row["blockers"]) >= 2


def test_campaign_search_rate_is_shown_separately_and_never_claimed_as_cluster_bid():
    result = analyze_cluster_rows(
        [source_row(cluster_bid_kopecks="", bid_observed_at="")],
        settings(search_bid_kopecks="1550"), decisions(), now=NOW,
        economics_observed_at="2026-10-29T11:00:00+03:00",
    )
    row = result["rows"][0]
    assert row["campaign_search_bid_rub"] == 15.5
    assert row["current_bid_rub"] is None
    assert row["target_bid_rub"] is None
    assert row["action"] == "СОВЕТ ПО СТАВКЕ ЗАБЛОКИРОВАН"


def test_campaign_search_rate_is_hidden_for_paused_or_non_rub_campaigns():
    for overrides in ({"status": "11"}, {"currency": "KZT"}):
        result = analyze_cluster_rows([source_row()], settings(**overrides), [], now=NOW,
                                     economics_observed_at="2026-10-29T11:00:00+03:00")
        assert result["rows"][0]["campaign_search_bid_rub"] is None


def test_stale_bid_and_non_cpm_campaign_block_change():
    result = analyze_cluster_rows(
        [source_row(bid_observed_at="2026-10-27T12:00:00+03:00")],
        settings(payment_type="cpc"), decisions(), now=NOW,
        economics_observed_at="2026-10-29T11:00:00+03:00",
    )
    row = result["rows"][0]
    assert row["target_bid_rub"] is None
    assert row["blockers"]


def test_automatic_bid_mode_blocks_cluster_bid_advice():
    result = analyze_cluster_rows(
        [source_row()], settings(bid_type="unified"), decisions(), now=NOW,
        economics_observed_at="2026-10-29T11:00:00+03:00",
    )
    row = result["rows"][0]
    assert row["target_bid_rub"] is None
    assert any("ручную ставку CPM" in x for x in row["blockers"])


def test_missing_or_stale_campaign_settings_block_even_with_fresh_cluster_metrics():
    result = analyze_cluster_rows(
        [source_row()], settings(observed_at="2026-10-27T12:00:00+03:00"), decisions(), now=NOW,
        economics_observed_at="2026-10-29T11:00:00+03:00",
    )
    row = result["rows"][0]
    assert row["target_bid_rub"] is None
    assert any("настроек кампании устарел" in x for x in row["blockers"])


def test_unknown_metrics_are_not_treated_as_zero_and_cross_store_position_is_never_joined():
    position = {"cards": [{
        "store_id": "ap", "sku": "123", "query": "таз строительный", "position": 2,
        "freshness": {"position": {"status": "fresh"}},
    }]}
    result = analyze_cluster_rows([source_row(spend_rub="")], settings(), decisions(), position, now=NOW,
                                 economics_observed_at="2026-10-29T11:00:00+03:00")
    row = result["rows"][0]
    assert row["spend_rub"] is None
    assert row["organic_position"] is None
    assert row["target_bid_rub"] is None


def test_excluded_cluster_is_not_advised_for_a_bid_change():
    result = analyze_cluster_rows([source_row(cluster_state="excluded")], settings(), decisions(), now=NOW,
                                 economics_observed_at="2026-10-29T11:00:00+03:00")
    row = result["rows"][0]
    assert row["target_bid_rub"] is None
    assert any("исключён" in x for x in row["blockers"])


def test_stale_unit_economics_block_any_bid_advice():
    result = analyze_cluster_rows(
        [source_row()], settings(), decisions(), now=NOW,
        economics_observed_at="2026-10-27T12:00:00+03:00",
    )
    row = result["rows"][0]
    assert row["target_bid_rub"] is None
    assert any("свежий точный допустимый расход" in x for x in row["blockers"])


def test_exact_fresh_wb_bid_bands_join_by_campaign_sku_and_normalized_query():
    recommendations = [{
        "campaign_id": 15,
        "recommendations": {"123": {"data": {
            "base": {"competitiveBid": {"bidKopecks": 8000}, "leadersBid": {"bidKopecks": 15000}},
            "normQueries": [{"normQuery": "Таз   строительный", "reachMin": {"bidKopecks": 5000},
                             "reachMedium": {"bidKopecks": 7000}, "reachMax": {"bidKopecks": 9000}}],
        }}},
    }]
    result = analyze_cluster_rows(
        [source_row()], settings(), decisions(), now=NOW,
        bid_recommendations=recommendations,
        bid_recommendations_observed_at="2026-10-29T11:30:00+03:00",
        economics_observed_at="2026-10-29T11:00:00+03:00",
    )
    guidance = result["rows"][0]["wb_bid_guidance"]
    assert guidance["competitive_bid_rub"] == 80
    assert guidance["leaders_bid_rub"] == 150
    assert guidance["reach_min_bid_rub"] == 50
    assert guidance["reach_medium_bid_rub"] == 70
    assert guidance["reach_max_bid_rub"] == 90


def test_wb_bid_guidance_does_not_cross_campaign_sku_query_or_freshness():
    recommendations = [{"campaign_id": 999, "recommendations": {"123": {"data": {
        "normQueries": [{"normQuery": "таз строительный", "reachMedium": {"bidKopecks": 7000}}],
    }}}}]
    result = analyze_cluster_rows(
        [source_row()], settings(), decisions(), now=NOW,
        bid_recommendations=recommendations,
        bid_recommendations_observed_at="2026-10-29T11:30:00+03:00",
        economics_observed_at="2026-10-29T11:00:00+03:00",
    )
    assert result["rows"][0]["wb_bid_guidance"] is None
    result = analyze_cluster_rows(
        [source_row()], settings(), decisions(), now=NOW,
        bid_recommendations=[{"campaign_id": 15, "recommendations": {"123": {"data": {
            "normQueries": [{"normQuery": "другой запрос", "reachMedium": {"bidKopecks": 7000}}],
        }}}}],
        bid_recommendations_observed_at="2026-10-27T12:00:00+03:00",
        economics_observed_at="2026-10-29T11:00:00+03:00",
    )
    assert result["rows"][0]["wb_bid_guidance"] is None
