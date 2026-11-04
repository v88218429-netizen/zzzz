from wb_control_center.agents.advertising import (
    _scan_cursor,
    _sweep_pair_progress,
)


def test_catalog_delta_preserves_unchanged_pairs_and_cursor_target():
    previous = {
        "data": {
            "campaign_ids": [11, 22],
            "campaign_nm_ids": {"11": [101, 102], "22": [201]},
            "next_campaign_index": 1,
            "next_campaign_id": 22,
            "next_nm_index": {"11": 0, "22": 0},
            "sweep_id": 8,
            "swept_campaign_ids": [11, 22],
            "swept_nm_ids": {"11": [101, 102], "22": [201]},
        }
    }

    cursor = _scan_cursor(
        previous,
        [5, 11, 22],
        {"5": [51], "11": [101, 103], "22": [201]},
    )

    assert cursor["catalog_changed"] is True
    assert cursor["sweep_id"] == 9
    assert cursor["next_campaign_index"] == 2
    assert cursor["next_campaign_id"] == 22
    assert cursor["swept_nm_ids"] == {"11": [101], "22": [201]}
    assert cursor["swept_campaign_ids"] == [22]
    assert _sweep_pair_progress(
        {"5": [51], "11": [101, 103], "22": [201]},
        cursor["swept_nm_ids"],
    ) == (2, 4, False)
