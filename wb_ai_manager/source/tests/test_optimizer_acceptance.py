from datetime import datetime, timezone
import unittest

from wb_control_center.optimizer_acceptance import evaluate_optimizer_acceptance


def fixture():
    shops = {}
    cabinets = {}
    for index, code in enumerate(("ap", "aa", "yv")):
        shops[code] = {
            "wb_connected": True,
            "observed_at": "2026-11-02T07:00:00+00:00",
            "sweep": {
                "sweep_complete": True, "sweep_campaigns_seen": 1, "sweep_campaigns_total": 1,
                "sweep_products_seen": 1, "sweep_products_total": 1,
            },
            "campaigns": [{"nm_ids": [str(100 + index)]}],
        }
        cabinets[code] = {"wb_connected": True, "status": "ok"}
    products = []
    labels = {"ap": "Дом Саныча", "aa": "Аир", "yv": "Хозяюшка"}
    for index, code in enumerate(("ap", "aa", "yv")):
        products.append({
            "cabinet": labels[code], "sku": str(100 + index),
            "unit_economics_decision_ready": True, "cost_rub": 10, "price_rub": 30, "profit_rub": 5,
        })
    portfolio = {
        "current_data": True,
        "own_27": {
            "primary_unit_economics": {
                "semantics": "plan_model_plus_independent_fact", "snapshot_date": "2026-11-02",
            },
            "products": products,
        },
    }
    query = {
        "summary": {
            "facts_status": "all_fresh", "query_rows": 3, "fresh_query_rows": 3,
            "blocked_query_rows": 0, "ready_query_actions": 0,
        },
    }
    return {"shops": shops}, portfolio, query, cabinets


class OptimizerAcceptanceTests(unittest.TestCase):
    def test_complete_evidence_is_ready_even_without_a_recommended_change(self):
        result = evaluate_optimizer_acceptance(
            *fixture(), now=datetime(2026, 11, 2, 8, tzinfo=timezone.utc),
        )
        self.assertTrue(result["ready"])
        self.assertEqual(result["query_facts"]["ready_query_actions"], 0)

    def test_live_primary_economics_is_not_blocked_by_unrelated_stale_portfolio_source(self):
        args = fixture()
        args[1]["current_data"] = False
        economics = args[1]["own_27"]["primary_unit_economics"]
        economics.update({
            "snapshot_basis": "live_source_read",
            "source_retrieved_at": "2026-11-02T07:55:00+00:00",
        })
        result = evaluate_optimizer_acceptance(
            *args, now=datetime(2026, 11, 2, 8, tzinfo=timezone.utc),
        )
        self.assertTrue(result["ready"])
        self.assertFalse(any("Юнит-экономика не подтверждена" in item for item in result["blockers"]))

    def test_unverified_primary_read_does_not_override_stale_portfolio(self):
        args = fixture()
        args[1]["current_data"] = False
        result = evaluate_optimizer_acceptance(
            *args, now=datetime(2026, 11, 2, 8, tzinfo=timezone.utc),
        )
        self.assertFalse(result["ready"])
        self.assertTrue(any("Юнит-экономика не подтверждена" in item for item in result["blockers"]))

    def test_economics_coverage_uses_live_advertised_catalog_over_stale_campaign_cache(self):
        args = fixture()
        # The cached campaign list is missing one nmID that the durable live sweep
        # catalog still expects. The missing product must remain in the denominator.
        args[0]["shops"]["aa"]["advertised_nm_ids"] = ["101", "102"]
        args[0]["shops"]["aa"]["campaigns"] = [{"nm_ids": ["101"]}]

        result = evaluate_optimizer_acceptance(
            *args, now=datetime(2026, 11, 2, 8, tzinfo=timezone.utc),
        )

        self.assertEqual(
            result["economics"]["coverage_by_shop"]["aa"],
            {"advertised_products": 2, "matched_products": 1, "decision_ready_products": 1},
        )
        self.assertFalse(result["ready"])
        self.assertTrue(any("AIR" in item and "1/2" in item for item in result["blockers"]))

    def test_economics_diagnostics_names_unmatched_campaign_nm_id_and_stage(self):
        args = fixture()
        args[0]["shops"]["aa"]["advertised_nm_ids"] = ["101", "999"]
        result = evaluate_optimizer_acceptance(
            *args, now=datetime(2026, 11, 2, 8, tzinfo=timezone.utc),
        )
        self.assertEqual(result["economics_diagnostics_by_shop"]["aa"], [{
            "nm_id": "999",
            "stage": "shop_nm_id_to_portfolio_product",
            "reason_codes": ["exact_shop_nm_id_match_missing"],
        }])

    def test_economics_diagnostics_names_missing_fields_for_matched_product(self):
        args = fixture()
        product = args[1]["own_27"]["products"][1]
        product["unit_economics_decision_ready"] = False
        product.pop("profit_rub")
        result = evaluate_optimizer_acceptance(
            *args, now=datetime(2026, 11, 2, 8, tzinfo=timezone.utc),
        )
        diagnostic = result["economics_diagnostics_by_shop"]["aa"][0]
        self.assertEqual(diagnostic["nm_id"], "101")
        self.assertEqual(diagnostic["stage"], "unit_economics_validation")
        self.assertEqual(
            diagnostic["reason_codes"],
            ["unit_economics_not_decision_ready", "profit_rub_missing_or_unreadable"],
        )

    def test_partial_campaign_sweep_blocks_acceptance(self):
        args = fixture()
        args[0]["shops"]["aa"]["sweep"]["sweep_complete"] = False
        result = evaluate_optimizer_acceptance(
            *args, now=datetime(2026, 11, 2, 8, tzinfo=timezone.utc),
        )
        self.assertFalse(result["ready"])
        self.assertTrue(any("AIR" in item and "обход рекламы" in item for item in result["blockers"]))

    def test_stale_unit_economics_blocks_acceptance(self):
        args = fixture()
        args[1]["own_27"]["primary_unit_economics"]["snapshot_date"] = "2026-10-14"
        result = evaluate_optimizer_acceptance(
            *args, now=datetime(2026, 11, 2, 8, tzinfo=timezone.utc),
        )
        self.assertFalse(result["ready"])
        self.assertTrue(any("Юнит-экономика" in item for item in result["blockers"]))

    def test_partial_query_facts_are_reported_without_blocking_ads_acceptance(self):
        args = fixture()
        args[2]["summary"]["facts_status"] = "partial_refresh"
        args[2]["summary"]["blocked_query_rows"] = 1
        result = evaluate_optimizer_acceptance(
            *args, now=datetime(2026, 11, 2, 8, tzinfo=timezone.utc),
        )
        self.assertTrue(result["ready"])
        self.assertFalse(any("Поисковые факты" in item for item in result["blockers"]))
        self.assertTrue(any("неполные" in item for item in result["warnings"]))

    def test_fresh_primary_plan_can_gate_ads_when_actual_ad_facts_are_separate(self):
        args = fixture()
        args[1]["own_27"]["primary_unit_economics"]["semantics"] = "plan_model_only"
        result = evaluate_optimizer_acceptance(
            *args, now=datetime(2026, 11, 2, 8, tzinfo=timezone.utc),
        )
        self.assertTrue(result["ready"])
        self.assertTrue(any("без отдельного факта продаж" in item for item in result["warnings"]))

    def test_campaign_cache_accumulates_bounded_runs_and_preserves_observation_times(self):
        from wb_control_center.optimizer_acceptance import merge_campaign_cache
        first = merge_campaign_cache({}, {
            "active_campaign_ids": [11, 22], "catalog_complete": True,
            "campaigns": [{"campaign_id": 11, "scan_complete": True, "nm_ids": [101], "recommendations": {"101": {"bid": 45}}}],
            "sweep_complete": False, "sweep_campaigns_total": 2,
        }, "2026-11-02T08:00:00+00:00")
        self.assertFalse(first["campaign_cache_complete"])
        second = merge_campaign_cache(first, {
            "active_campaign_ids": [11, 22], "catalog_complete": True,
            "campaigns": [{"campaign_id": 22, "scan_complete": True, "nm_ids": [202], "recommendations": {"202": {"bid": 60}}}],
            "sweep_complete": True, "sweep_campaigns_total": 2,
            "sweep_campaigns_seen": 2, "sweep_products_seen": 2, "sweep_products_total": 2,
        }, "2026-11-02T08:10:00+00:00")
        self.assertTrue(second["campaign_cache_complete"])
        self.assertTrue(second["sweep_complete"])
        self.assertEqual([x["campaign_id"] for x in second["campaigns"]], [11, 22])
        self.assertEqual(second["campaigns"][0]["observed_at"], "2026-11-02T08:00:00+00:00")

    def test_campaign_cache_does_not_refresh_failed_scan_and_resets_changed_catalog(self):
        from wb_control_center.optimizer_acceptance import merge_campaign_cache
        prior = merge_campaign_cache({}, {
            "active_campaign_ids": [11], "catalog_complete": True,
            "campaigns": [{"campaign_id": 11, "scan_complete": True, "budget": 500}],
        }, "2026-11-02T08:00:00+00:00")
        failed = merge_campaign_cache(prior, {
            "active_campaign_ids": [11], "catalog_complete": True,
            "campaigns": [{"campaign_id": 11, "scan_complete": False, "budget": {"_error": "source"}}],
            "sweep_complete": False,
        }, "2026-11-02T08:15:00+00:00")
        self.assertEqual(failed["campaigns"][0]["observed_at"], "2026-11-02T08:00:00+00:00")
        changed = merge_campaign_cache(prior, {
            "active_campaign_ids": [33], "catalog_complete": True,
            "campaigns": [{"campaign_id": 33, "scan_complete": True}],
        }, "2026-11-02T08:20:00+00:00")
        self.assertEqual([x["campaign_id"] for x in changed["campaigns"]], [33])

    def test_confirmed_empty_catalog_is_complete_but_unavailable_catalog_is_not(self):
        from wb_control_center.optimizer_acceptance import merge_campaign_cache
        empty = merge_campaign_cache({}, {
            "active_campaign_ids": [], "catalog_complete": True, "note": "no active campaigns",
            "campaigns": [], "sweep_complete": True,
        }, "2026-11-02T08:00:00+00:00")
        unavailable = merge_campaign_cache({}, {
            "active_campaign_ids": [], "catalog_complete": False,
            "note": "campaign catalog unavailable", "campaigns": [],
        }, "2026-11-02T08:00:00+00:00")
        self.assertTrue(empty["sweep_complete"])
        self.assertFalse(unavailable["sweep_complete"])


def test_campaign_cache_replaces_stale_nmids_and_prunes_removed_sku_facts():
    from wb_control_center.optimizer_acceptance import merge_campaign_cache

    previous = merge_campaign_cache({}, {
        "active_campaign_ids": [11], "catalog_complete": True,
        "campaigns": [{
            "campaign_id": 11, "scan_complete": True,
            "nm_ids": [101, 102], "recommendation_nm_ids_scanned": [101, 102],
            "stats_by_nm": {"101": {"orders": 1}, "102": {"orders": 2}},
            "recommendations": {"101": {"bid": 10}, "102": {"bid": 20}},
        }],
    }, "2026-11-02T08:00:00+00:00")

    refreshed = merge_campaign_cache(previous, {
        "active_campaign_ids": [11], "catalog_complete": True,
        "campaigns": [{
            "campaign_id": 11, "scan_complete": True,
            "nm_ids": [101, 103], "recommendation_nm_ids_scanned": [103],
            "stats_by_nm": {"101": {"orders": 3}, "103": {"orders": 4}},
            "recommendations": {"103": {"bid": 30}},
        }],
    }, "2026-11-02T08:10:00+00:00")

    campaign = refreshed["campaigns"][0]
    assert campaign["nm_ids"] == [101, 103]
    assert campaign["recommendation_nm_ids_scanned"] == [103]
    assert set(campaign["stats_by_nm"]) == {"101", "103"}
    assert set(campaign["recommendations"]) == {"101", "103"}


class TestRuntimeWorkflowContinuationOrder(unittest.TestCase):
    def test_continuation_runs_before_runtime_state_orphan_checkout(self):
        from pathlib import Path

        repository_root = Path(__file__).resolve().parents[3]
        workflow = (repository_root / ".github/workflows/wb-github-runtime.yml").read_text(encoding="utf-8")
        continuation = workflow.index("Continue optimizer sweep when the latest run advanced it")
        orphan_checkout = workflow.index("Replace runtime branch with encrypted state only")
        self.assertLess(continuation, orphan_checkout)


if __name__ == "__main__":
    unittest.main()
