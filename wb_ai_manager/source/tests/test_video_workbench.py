from __future__ import annotations

import unittest

from wb_control_center.video_workbench import (
    _promotion_recommendations,
    _review_candidates,
    _chat_queue,
    build_video_workbench,
    evaluate_experiment_results,
    validate_experiment_payload,
)


def promotion_snapshot(price: float) -> dict:
    return {
        "price_margin": {
            "promotions": {
                "created_at": "2026-11-13T10:00:00+00:00",
                "data": [{"nmID": 12345678, "actionName": "Осенняя акция", "priceAfterPromotion": price}],
            }
        }
    }


def portfolio(*, ready: bool = True, target: float | None = 50) -> dict:
    item = {
        "own_27": {
            "unit_economics_by_shop_nm_id": {
                "ap": {
                    "12345678": {
                        "nm_id": "12345678",
                        "shop_code": "ap",
                        "name": "Тестовый товар",
                        "price_rub": 1000,
                        "cost_rub": 400,
                        "profit_rub": 120,
                        "logistics_total_rub": 100,
                        "acceptance_rub": 0,
                        "commission_pct": 20,
                        "acquiring_pct": 2,
                        "tax_total_rub": 60,
                        "plan_drr_pct": 10,
                        "unit_economics_decision_ready": ready,
                    }
                }
            }
        }
    }
    if target is not None:
        item["own_27"]["unit_economics_by_shop_nm_id"]["ap"]["12345678"]["target_profit_rub"] = target
    return item


class PromotionTests(unittest.TestCase):
    def test_promotion_uses_exact_product_price_and_unit_economics(self):
        result = _promotion_recommendations(promotion_snapshot(950), portfolio())
        row = result["rows"][0]
        self.assertEqual(row["recommendation"], "consider")
        self.assertEqual(row["nm_id"], "12345678")
        self.assertEqual(row["shop_code"], "ap")
        self.assertEqual(row["estimated_profit_rub"], 89)
        self.assertFalse(result["automatic_entry"])

    def test_promotion_blocks_when_price_is_missing_or_estimate_is_not_ready(self):
        result = _promotion_recommendations(
            {"price_margin": {"promotions": {"data": [{"nmID": 12345678, "actionName": "Акция"}]}}},
            portfolio(),
        )
        self.assertEqual(result["rows"][0]["recommendation"], "blocked")
        self.assertIn("точную цену", result["rows"][0]["reason"])

    def test_promotion_blocks_when_unit_economics_is_not_decision_ready(self):
        result = _promotion_recommendations(promotion_snapshot(950), portfolio(ready=False))
        self.assertEqual(result["rows"][0]["recommendation"], "blocked")
        self.assertIn("не подтверждена", result["rows"][0]["reason"])

    def test_promotion_needs_target_instead_of_guessing(self):
        result = _promotion_recommendations(promotion_snapshot(950), portfolio(target=None))
        self.assertEqual(result["rows"][0]["recommendation"], "needs_target")


class PhotoExperimentTests(unittest.TestCase):
    def test_ab_test_is_two_variants_without_audience_and_requires_duration(self):
        result = validate_experiment_payload(
            {
                "experiment_type": "ab_photo_test",
                "title": "Фото A/B",
                "shop_code": "ap",
                "nm_id": "12345678",
                "duration_days": 14,
                "metric": "ctr",
                "variants": [{"name": "Текущее"}, {"name": "Новое"}],
                "photo_validation": {"name": "new.webp", "mime": "image/webp", "width": 700, "height": 900, "size_bytes": 500000},
            },
            experiment_id="e1",
            created_at="2026-11-13T10:00:00Z",
        )
        self.assertEqual(len(result["variants"]), 2)
        self.assertEqual(result["audience"], {})
        self.assertEqual(result["execution_mode"], "manual_in_wb")
        self.assertFalse(result["write_to_wb"])

    def test_ab_test_rejects_three_variants_and_invalid_duration(self):
        base = {
            "experiment_type": "ab_photo_test", "title": "Фото", "shop_code": "ap",
            "nm_id": "12345678", "metric": "ctr", "duration_days": 14,
        }
        with self.assertRaises(ValueError):
            validate_experiment_payload({**base, "variants": [{"name": "A"}, {"name": "B"}, {"name": "C"}]},
                                        experiment_id="e1", created_at="now")
        with self.assertRaises(ValueError):
            validate_experiment_payload({**base, "duration_days": 6, "variants": [{"name": "A"}, {"name": "B"}]},
                                        experiment_id="e1", created_at="now")

    def test_audience_photo_requires_all_four_audience_dimensions(self):
        base = {"experiment_type": "audience_photo", "title": "Сегментное фото",
                "shop_code": "aa", "nm_id": "12345678", "photo_name": "Фото для семей с детьми",
                "photo_validation": {"name": "target.png", "mime": "image/png", "width": 700, "height": 933, "size_bytes": 600000},
                "audience": {"gender": "Женский", "age": "25–34", "family_status": "С детьми", "region": "Москва"}}
        result = validate_experiment_payload(base, experiment_id="e2", created_at="now")
        self.assertEqual(result["experiment_type"], "audience_photo")
        self.assertEqual(len(result["variants"]), 1)
        self.assertIn("не А/Б-тест", result["guidance"])
        invalid = {**base, "audience": {**base["audience"], "region": ""}}
        with self.assertRaises(ValueError):
            validate_experiment_payload(invalid, experiment_id="e3", created_at="now")

    def test_audience_photo_results_use_wb_sample_thresholds_without_claiming_significance(self):
        experiment = {"experiment_type": "audience_photo", "variants": [{"name": "Фото"}]}
        small = evaluate_experiment_results(experiment, {"impressions": 2500, "clicks": 70, "carts": 5, "orders": 1})
        self.assertEqual(small["sample_status"], "insufficient")
        enough = evaluate_experiment_results(experiment, {"impressions": 5000, "clicks": 240, "carts": 30, "orders": 8})
        self.assertEqual(enough["sample_status"], "usable_but_not_significance")
        self.assertFalse(enough["statistical_significance_checked"])
        self.assertIsNone(enough["winner"])

    def test_ab_result_is_descriptive_and_never_declares_winner(self):
        experiment = {"experiment_type": "ab_photo_test", "metric": "ctr",
                      "variants": [{"name": "A"}, {"name": "B"}]}
        result = evaluate_experiment_results(experiment, [
            {"name": "A", "impressions": 1000, "clicks": 50, "carts": 5, "orders": 2},
            {"name": "B", "impressions": 1000, "clicks": 60, "carts": 5, "orders": 2},
        ])
        self.assertEqual(result["descriptive_leader"], "B")
        self.assertIsNone(result["winner"])
        self.assertFalse(result["statistical_significance_checked"])

    def test_image_dimensions_and_size_are_checked_before_saving_a_plan(self):
        payload = {"experiment_type": "audience_photo", "title": "Фото", "shop_code": "ap", "nm_id": "12345678",
                   "photo_name": "Файл", "audience": {"gender": "М", "age": "25–34", "family_status": "В браке", "region": "Москва"},
                   "photo_validation": {"name": "small.jpg", "mime": "image/jpeg", "width": 600, "height": 700, "size_bytes": 1000}}
        with self.assertRaisesRegex(ValueError, "Разрешение"):
            validate_experiment_payload(payload, experiment_id="e", created_at="now")
        payload["photo_validation"] = {"name": "large.png", "mime": "image/png", "width": 900, "height": 1200, "size_bytes": 11 * 1024 * 1024}
        with self.assertRaisesRegex(ValueError, "10 МБ"):
            validate_experiment_payload(payload, experiment_id="e", created_at="now")

    def test_unknown_test_type_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_experiment_payload({"title": "x", "shop_code": "ap", "nm_id": "12345678"},
                                        experiment_id="e", created_at="now")


class CustomerAndCatalogTests(unittest.TestCase):
    def test_review_candidates_do_not_assume_pin_status_or_use_unanswered_fallback(self):
        snapshots = {"reviews_questions": {"answered_feedbacks": {
            "created_at": "now", "data": [{"id": "r1", "productValuation": 5,
                "text": "Отличный товар, использую каждый день и доволен качеством."}]
        }, "feedbacks": {"data": [{"id": "unanswered", "productValuation": 5,
            "text": "Положительный отзыв, который ещё не обработан."}]}}}
        result = _review_candidates(snapshots)
        self.assertEqual(result["rows"][0]["pin_status"], "unknown")
        self.assertEqual(result["rows"][0]["id"], "r1")
        self.assertNotIn("unanswered", [x["id"] for x in result["rows"]])

    def test_chat_drafts_are_never_sent_and_legal_cases_have_no_template(self):
        snapshots = {"buyer_chats": {"chat_events": {"data": [
            {"chatId": "c1", "text": "Где мой заказ, когда привезут?"},
            {"chatId": "c2", "text": "Направьте претензию юристу."},
        ]}}}
        result = _chat_queue(snapshots)
        self.assertFalse(result["automatic_replies"])
        self.assertFalse(any(row["reply_sent"] for row in result["rows"]))
        legal = next(row for row in result["rows"] if row["chat_id"] == "c2")
        self.assertIsNone(legal["draft"])
        self.assertIn("юридический риск", legal["category"])

    def test_card_catalog_does_not_claim_unreturned_fields_are_missing(self):
        result = build_video_workbench(
            {"cards": {"card_catalog": {"created_at": "now", "data": {
                "complete": False,
                "cards": [{"nmID": 12345678, "vendorCode": "sku-1", "photos": [], "sizes": []}],
            }}}},
            portfolio(),
            [],
        )
        card = result["card_catalog"]["rows"][0]
        self.assertEqual(result["card_catalog"]["complete"], False)
        self.assertEqual(card["field_visibility"]["photos"], "empty")
        self.assertEqual(card["field_visibility"]["dimensions"], "not_returned")
        self.assertIn("неполный", result["card_catalog"]["note"])


if __name__ == "__main__":
    unittest.main()
