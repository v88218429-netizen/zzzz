import asyncio

from wb_control_center.agents.cards import CardsAgent, _card_error_rows, _simple_problem_rows
from wb_control_center.models import AgentResult


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


class _PagedCardsAgent(CardsAgent):
    def __init__(self, pages):
        self.ctx = None
        self.pages = iter(pages)

    async def call(self, tool, **kwargs):
        page = next(self.pages)
        if isinstance(page, Exception):
            raise page
        return page


def _full_page(start=1, cursor_nm=100):
    return {
        "cards": [{"nmID": start + index, "vendorCode": f"sku-{start + index}"} for index in range(100)],
        "cursor": {"updatedAt": "2026-11-13T10:00:00Z", "nmID": cursor_nm},
    }


def _load(pages):
    out = AgentResult(agent="cards")
    payload, _ = asyncio.run(_PagedCardsAgent(pages)._load_catalog(out))
    return payload, out


def test_catalog_is_complete_only_after_a_short_last_page():
    payload, _ = _load([_full_page(), {"cards": [], "cursor": {}}])
    assert payload["complete"] is True
    assert payload["scan_reason"] == "end_of_catalog"
    assert payload["count"] == 100
    assert payload["pages_scanned"] == 2


def test_repeated_cursor_keeps_rows_but_marks_catalog_incomplete():
    payload, out = _load([_full_page(), _full_page(101, 100)])
    assert payload["complete"] is False
    assert payload["scan_reason"] == "repeated_cursor"
    assert payload["count"] == 200
    assert any(event.key == "card_catalog_partial" for event in out.events)


def test_full_page_without_cursor_is_incomplete():
    payload, _ = _load([{"cards": [{"nmID": i} for i in range(100)], "cursor": {}}])
    assert payload["complete"] is False
    assert payload["scan_reason"] == "missing_cursor"


def test_read_error_preserves_partial_catalog_and_reason():
    payload, _ = _load([_full_page(), RuntimeError("source down")])
    assert payload["complete"] is False
    assert payload["scan_reason"] == "read_error"
    assert payload["scan_error_type"] == "RuntimeError"
    assert payload["count"] == 100


def test_rows_without_nm_id_prevent_complete_status():
    payload, _ = _load([{"cards": [{"nmID": 123}, {"vendorCode": "orphan"}], "cursor": {}}])
    assert payload["complete"] is False
    assert payload["scan_reason"] == "rows_without_nm_id"
    assert payload["rows_without_nm_id"] == 1


def test_page_limit_is_not_reported_as_a_complete_catalog():
    pages = [_full_page(index * 100 + 1, index * 100 + 100) for index in range(50)]
    payload, _ = _load(pages)
    assert payload["complete"] is False
    assert payload["scan_reason"] == "page_limit"
    assert payload["pages_scanned"] == 50
    assert payload["count"] == 5000


def test_duplicate_nm_ids_prevent_complete_status():
    payload, _ = _load([{"cards": [{"nmID": 123}, {"nmID": 123}], "cursor": {}}])
    assert payload["complete"] is False
    assert payload["scan_reason"] == "duplicate_nm_id"
    assert payload["duplicate_nm_ids"] == 1

