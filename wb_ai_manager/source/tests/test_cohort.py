from wb_control_center.cohort import analyze_order_lifecycle


def test_lifecycle_estimates_real_lag_and_marks_fresh_cohort_immature():
    rows=[["Кабинет","Дата заказа","Статус","Артикул продавца","Артикул WB","SRID","Дата изменения","Тип отмены","Склад","Город","Цена продавца","Выгружено"]]
    # Historical resolved rows: buyouts mature between 5 and 10 days.
    for i, lag in enumerate([5,6,6,7,7,7,8,8,9,10]*3):
        day=1 + (i % 10)
        rows.append(["1",f"2026-08-{day:02d}T10:00:00+03:00","buyout","","123",f"b{i}",f"2026-08-{day+lag:02d}T10:00:00+03:00","","","","","2026-09-18T18:00:00+03:00"])
    # Fresh last-3-day cohort is mostly unresolved.
    for i in range(20):
        rows.append(["1","2026-09-17T10:00:00+03:00","created","","123",f"o{i}","2026-09-17T10:00:00+03:00","","","","","2026-09-18T18:00:00+03:00"])
    data=analyze_order_lifecycle(rows)
    sku=data["by_sku"]["123"]
    assert 6 <= sku["buyout_lag"]["p50_days"] <= 8
    assert sku["recent_3d"]["orders"] == 20
    assert sku["recent_3d"]["quality_ready"] is False
    assert sku["recent_3d"]["maturity_pct"] < 50


def test_old_unresolved_after_p90_is_flagged_separately_from_fresh_quality():
    rows=[["Кабинет","Дата заказа","Статус","Артикул продавца","Артикул WB","SRID","Дата изменения","Тип отмены","Склад","Город","Цена продавца","Выгружено"]]
    for i, lag in enumerate([5,6,7,7,8,9,10]*4):
        rows.append(["1","2026-08-01T10:00:00+03:00","buyout","","321",f"b{i}",f"2026-08-{1+lag:02d}T10:00:00+03:00","","","","","2026-09-18T18:00:00+03:00"])
    rows.append(["1","2026-08-20T10:00:00+03:00","created","","321","stale","2026-08-20T10:00:00+03:00","","","","","2026-09-18T18:00:00+03:00"])
    data=analyze_order_lifecycle(rows)
    assert data["by_sku"]["321"]["unresolved_older_than_p90"] == 1
