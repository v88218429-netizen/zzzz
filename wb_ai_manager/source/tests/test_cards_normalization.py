from wb_control_center.agents.cards import _card_error_rows, _simple_problem_rows


def test_card_errors_are_deduplicated_to_latest_record():
    data = {
        "data": {
            "items": [
                {
                    "vendorCodes": ["Лоток кр 08 П"],
                    "subjects": {"Лоток кр 08 П": {"id": 2349, "name": "Лотки для метизов"}},
                    "errors": {"Лоток кр 08 П": ["Не более 60 символов"]},
                    "updatedAt": "2026-06-21T06:49:23Z",
                },
                {
                    "vendorCodes": ["Лоток кр 08 П"],
                    "subjects": {"Лоток кр 08 П": {"id": 2349, "name": "Лотки для метизов"}},
                    "errors": {"Лоток кр 08 П": ["Не более 60 символов"]},
                    "updatedAt": "2026-06-21T06:55:50Z",
                },
            ]
        }
    }
    rows = _card_error_rows(data)
    assert len(rows) == 1
    assert rows[0]["vendor_code"] == "Лоток кр 08 П"
    assert rows[0]["updated_at"] == "2026-06-21T06:55:50Z"


def test_empty_problem_wrappers_do_not_become_false_critical_items():
    assert _simple_problem_rows("banned_products", {"report": []}) == []
    assert _simple_problem_rows("price_quarantine", {"data": None, "error": False}) == []
    assert _simple_problem_rows("price_quarantine", {"data": {"items": []}, "error": False}) == []
