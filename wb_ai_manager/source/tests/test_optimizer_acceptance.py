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

    def test_partial_query_facts_block_acceptance(self):
        args = fixture()
        args[2]["summary"]["facts_status"] = "partial_refresh"
        args[2]["summary"]["blocked_query_rows"] = 1
        result = evaluate_optimizer_acceptance(
            *args, now=datetime(2026, 11, 2, 8, tzinfo=timezone.utc),
        )
        self.assertFalse(result["ready"])
        self.assertTrue(any("Поисковые факты" in item for item in result["blockers"]))


if __name__ == "__main__":
    unittest.main()
