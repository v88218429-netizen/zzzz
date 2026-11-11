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

    def test_unmatched_nm_id_uses_shop_stock_identity_but_remains_blocked(self):
        args = fixture()
        args[0]["shops"]["yv"]["advertised_nm_ids"] = ["999"]
        args[1]["own_27"]["shop_products_by_shop_nm_id"] = {
            "yv": {
                "999": {
                    "nm_id": "999",
                    "product_name": "Корзина для белья белая с крышкой",
                    "brand": "Милих",
                    "seller_article": "",
                    "identity_source": "sellmonitor_shop_stocks",
                    "source": "hozyushka_sellmonitor.stocks",
                    "source_row": 47,
                    "observed_at": "2026-11-11T13:00:00+00:00",
                },
            },
        }

        result = evaluate_optimizer_acceptance(
            *args, now=datetime(2026, 11, 11, 14, tzinfo=timezone.utc),
        )

        diagnostic = result["economics_diagnostics_by_shop"]["yv"][0]
        self.assertEqual(diagnostic["stage"], "unit_economics_mapping")
        self.assertEqual(diagnostic["nm_id"], "999")
        self.assertEqual(diagnostic["product_name"], "Корзина для белья белая с крышкой")
        self.assertEqual(diagnostic["brand"], "Милих")
        self.assertEqual(diagnostic["identity_source"], "sellmonitor_shop_stocks")
        self.assertEqual(diagnostic["identity_source_row"], 47)
        self.assertEqual(diagnostic["reason_codes"], ["exact_unit_economics_mapping_missing"])
        self.assertIn("строка 47", diagnostic["reason_text"])
        self.assertEqual(result["economics"]["coverage_by_shop"]["yv"]["matched_products"], 0)
        self.assertFalse(result["ready"])

    def test_unmatched_nm_id_uses_public_product_identity_but_remains_blocked(self):
        args = fixture()
        args[0]["shops"]["aa"]["advertised_nm_ids"] = ["999"]
        args[0]["shops"]["aa"]["public_product_details_by_nm_id"] = {
            "999": {
                "name": "Корзина для белья белая с крышкой, 60 л",
                "brand": "Милих",
                "source": "wb_public_card",
                "product_url": "https://www.wildberries.ru/catalog/999/detail.aspx",
            },
        }

        result = evaluate_optimizer_acceptance(
            *args, now=datetime(2026, 11, 2, 8, tzinfo=timezone.utc),
        )

        diagnostic = result["economics_diagnostics_by_shop"]["aa"][0]
        self.assertEqual(diagnostic["stage"], "shop_nm_id_to_portfolio_product")
        self.assertEqual(diagnostic["nm_id"], "999")
        self.assertEqual(diagnostic["product_name"], "Корзина для белья белая с крышкой, 60 л")
        self.assertEqual(diagnostic["brand"], "Милих")
        self.assertEqual(diagnostic["identity_source"], "wb_public_card")
        self.assertIn("product_url", diagnostic)
        self.assertIn("юнит-экономика", diagnostic["reason_text"])
        self.assertFalse(result["ready"])

    def test_unmatched_nm_id_uses_live_seller_article_but_remains_blocked(self):
        args = fixture()
        args[0]["shops"]["aa"]["advertised_nm_ids"] = ["999"]
        args[0]["shops"]["aa"]["live_product_details_by_nm_id"] = {
            "999": {
                "seller_article": "test-basket-60l",
                "product_name": "Test basket with lid",
                "brand": "Test brand",
                "subject": "Laundry accessories",
                "source": "live_wb_cards",
                "observed_at": "2026-11-06T07:00:00+00:00",
            }
        }

        result = evaluate_optimizer_acceptance(*args)
        diagnostic = result["economics_diagnostics_by_shop"]["aa"][0]

        assert diagnostic["seller_article"] == "test-basket-60l"
        assert diagnostic["product_name"] == "Test basket with lid"
        assert diagnostic["identity_source"] == "live_wb_cards"
        assert "точная связка" in diagnostic["reason_text"]
        assert result["economics"]["coverage_by_shop"]["aa"]["matched_products"] == 0
        assert result["economics"]["coverage_by_shop"]["aa"]["decision_ready_products"] == 0
        assert not result["ready"]

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

    def test_exact_shop_nm_id_crosswalk_supplies_economics_without_portfolio_name_join(self):
        args = fixture()
        own = args[1]["own_27"]
        own["products"] = []
        own["unit_economics_by_shop_nm_id"] = {
            code: {
                str(100 + index): {
                    "sku": str(100 + index),
                    "seller_article": f"Товар {code}",
                    "name": f"Товар {code}",
                    "unit_economics_decision_ready": True,
                    "cost_rub": 10, "price_rub": 30, "profit_rub": 5,
                    "unit_economics_source": "exact crosswalk",
                },
            }
            for index, code in enumerate(("ap", "aa", "yv"))
        }

        result = evaluate_optimizer_acceptance(
            *args, now=datetime(2026, 11, 2, 8, tzinfo=timezone.utc),
        )

        self.assertTrue(result["ready"])
        for code in ("ap", "aa", "yv"):
            self.assertEqual(
                result["economics"]["coverage_by_shop"][code],
                {"advertised_products": 1, "matched_products": 1, "decision_ready_products": 1},
            )
            self.assertEqual(result["economics_diagnostics_by_shop"][code], [])

    def test_crosswalk_failure_reports_article_stage_and_reason(self):
        args = fixture()
        own = args[1]["own_27"]
        own["products"] = [x for x in own["products"] if x["sku"] != "101"]
        own["unit_economics_mapping_diagnostics_by_shop"] = {
            "aa": {
                "101": {
                    "nm_id": "101", "seller_article": "Ветошь 10кг",
                    "product_name": "Ветошь для уборки", "source": "crosswalk",
                    "source_row": 9, "audit_source_row": 114, "key": "АА|Ветошь 10кг",
                    "reason_codes": ["crosswalk_unit_row_mismatch"],
                },
            },
        }

        result = evaluate_optimizer_acceptance(
            *args, now=datetime(2026, 11, 2, 8, tzinfo=timezone.utc),
        )
        diagnostic = result["economics_diagnostics_by_shop"]["aa"][0]
        self.assertEqual(diagnostic["stage"], "unit_economics_mapping")
        self.assertEqual(diagnostic["seller_article"], "Ветошь 10кг")
        self.assertEqual(diagnostic["audit_source_row"], 114)
        self.assertEqual(diagnostic["mapping_key"], "АА|Ветошь 10кг")
        self.assertEqual(diagnostic["reason_codes"], ["crosswalk_unit_row_mismatch"])

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

        repository_root = next(
            (
                parent
                for parent in Path(__file__).resolve().parents
                if (parent / ".github/workflows/wb-github-runtime.yml").is_file()
            ),
            None,
        )
        if repository_root is None:
            self.skipTest("clean verification copies omit the repository workflow files")
        workflow = (repository_root / ".github/workflows/wb-github-runtime.yml").read_text(encoding="utf-8")
        continuation = workflow.index("Continue optimizer sweep when the latest run advanced it")
        orphan_checkout = workflow.index("Replace runtime branch with encrypted state only")
        self.assertLess(continuation, orphan_checkout)


def test_campaign_cache_retains_latest_endpoint_error_and_ignores_malformed_history():
    from wb_control_center.optimizer_acceptance import merge_campaign_cache

    previous = {
        "campaign_ids": ["11"],
        "campaigns": [],
        "scan_errors": "malformed legacy value",
    }
    result = merge_campaign_cache(previous, {
        "active_campaign_ids": [11],
        "catalog_complete": True,
        "campaigns": [],
        "scan_errors": [{
            "campaign_id": 11, "nm_id": 101,
            "endpoint": "wb_advert_bids_recommendations",
            "error": "HTTP 503", "observed_at": "2026-11-05T11:20:00+00:00",
        }],
    }, "2026-11-05T11:20:00+00:00")
    assert len(result["scan_errors"]) == 1
    assert result["scan_errors"][0]["nm_id"] == 101
    assert result["scan_errors"][0]["endpoint"] == "wb_advert_bids_recommendations"


if __name__ == "__main__":
    unittest.main()

    def test_recent_completed_sweep_remains_valid_during_next_partial_cycle(self):
        args = fixture()
        for shop in args[0]["shops"].values():
            shop["sweep"].update({
                "sweep_complete": False,
                "sweep_campaigns_seen": 0,
                "sweep_products_seen": 0,
                "sweep_pairs_total": 1,
                "catalog_fingerprint": "catalog-a",
                "last_completed_sweep": {
                    "completed_at": "2026-11-02T07:50:00+00:00",
                    "campaigns_total": 1,
                    "products_total": 1,
                    "pairs_total": 1,
                    "catalog_fingerprint": "catalog-a",
                },
            })
        result = evaluate_optimizer_acceptance(
            *args, now=datetime(2026, 11, 2, 8, tzinfo=timezone.utc),
        )
        self.assertTrue(result["ready"])
        self.assertTrue(all(row["acceptance_complete"] for row in result["shops"].values()))
        self.assertTrue(all(not row["complete"] for row in result["shops"].values()))

    def test_stale_or_changed_catalog_completion_does_not_satisfy_acceptance(self):
        for completed_at, fingerprint in (
            ("2026-11-02T05:59:00+00:00", "catalog-a"),
            ("2026-11-02T07:50:00+00:00", "catalog-old"),
        ):
            args = fixture()
            for shop in args[0]["shops"].values():
                shop["sweep"].update({
                    "sweep_complete": False,
                    "sweep_pairs_total": 1,
                    "catalog_fingerprint": "catalog-a",
                    "last_completed_sweep": {
                        "completed_at": completed_at,
                        "campaigns_total": 1,
                        "products_total": 1,
                        "pairs_total": 1,
                        "catalog_fingerprint": fingerprint,
                    },
                })
            result = evaluate_optimizer_acceptance(
                *args, now=datetime(2026, 11, 2, 8, tzinfo=timezone.utc),
            )
            self.assertFalse(result["ready"])
            self.assertTrue(any("подтверждённого полного обхода" in item for item in result["blockers"]))
