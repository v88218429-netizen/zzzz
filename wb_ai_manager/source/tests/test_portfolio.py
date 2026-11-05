from wb_control_center.config import Settings
from datetime import datetime, timezone

from wb_control_center.portfolio import PortfolioService, _assess_snapshot_freshness


def test_seed_has_real_portfolio_facts(tmp_path):
    s = Settings(data_dir=str(tmp_path), wb_api_token="")
    p = PortfolioService(s).snapshot()
    assert p["portfolio"]["profit_rub"] == 485257
    assert {x["id"] for x in p["stores"]} >= {"sanych", "air", "hozyushka"}
    assert p["own_27"]["wb_fbs_stock"] == 167634


def test_bridge_is_optional_and_connections_stay_read_only(tmp_path):
    s = Settings(data_dir=str(tmp_path), google_sheets_bridge_url="", google_sheets_bridge_key="", force_read_only=True)
    c = PortfolioService(s).connections()
    assert c["google_sheets"]["configured"] is False
    assert c["wb"]["read_only"] is True
    assert any(x["id"] == "own_27" for x in c["sources"])


def test_exact_crosswalk_maps_advertised_nm_id_to_copy_workbook_economics(tmp_path):
    service = PortfolioService(Settings(data_dir=str(tmp_path), wb_api_token=""))
    catalog_headers = [None] * 43
    header_values = {
        0: "Группа товаров", 1: "Товар", 2: "Технический ключ связки",
        3: "Магазин", 4: "Артикул продавца",
        8: "Себестоимость до передачи на маркетплейс",
        9: "Цена продажи до скидки", 10: "% скидки", 11: "Цена после скидки",
        12: "Цена WB после текущей скидки (из входа)", 13: "СПП, %",
        14: "Цена после СПП", 15: "Комиссия, %", 16: "Логистика, руб",
        19: "Платная приемка, руб", 21: "Конструктор тарифов, %",
        23: "Эквайринг, %", 24: "Эквайринг, руб", 25: "Налоговый режим",
        27: "Налоги всего, руб", 28: "Прибыль, руб", 29: "Маржинальность, %",
        30: "Рентабельность (ROI), %", 32: "План прибыль, руб",
        36: "План маржинальность, %", 40: "План ROI, %",
    }
    for column, value in header_values.items():
        catalog_headers[column] = value
    catalog_row = [None] * 43
    row_values = {
        0: "Ветошь", 1: "Ветошь 10 кг 40х60 — комплект", 2: "АА|Ветошь 10кг / АА|Ветошь ХПП 10кг",
        3: "АА", 4: "Ветошь 10кг / Ветошь ХПП 10кг", 8: None,
        9: "р.9 261,74", 10: "70,00%", 11: "р.2 778,52",
        12: "р.2 421,00", 13: "26,31%", 14: "р.2 047,49",
        15: "33,00%", 16: "р.38,00", 19: "р.5,00", 21: "2,50%",
        23: "2,31%", 24: "р.64,27", 25: "УСН 6% + НДС 5%",
        27: "р.220,35", 28: None, 29: None,
        30: "29,59%", 32: "р.78,29", 36: "12,00%", 40: "31,00%",
    }
    for column, value in row_values.items():
        catalog_row[column] = value
    catalog_rows = [[], catalog_headers, []] + [[None] * 43 for _ in range(5)] + [catalog_row]
    primary_row = [None] * 42
    primary_row_values = {
        0: "Ветошь", 1: "Ветошь 10 кг 40х60",
        5: "р.1 126,88", 8: "р.2 778,52", 10: "р.2 047,49",
        11: "33,00%", 12: "р.38,00", 15: "р.5,00",
        17: "2,50%", 19: "2,31%", 20: "р.64,27",
        21: "УСН 6% + НДС 5%", 23: "р.220,35",
        24: "р.333,42", 25: "12,00%", 26: "29,59%",
        28: "р.78,29", 32: "12,00%", 36: "31,00%",
    }
    for column, value in primary_row_values.items():
        primary_row[column] = value
    payload = {
        "generated_at": "2026-11-05T10:00:00+00:00",
        "sources": {
            "primary_unit_economics": {
                "spreadsheet_id": "primary-workbook",
                "read_at": "2026-11-05T10:00:00+00:00",
                "ranges": {"unit_economics": {"values": [[], [], [], primary_row]}},
            },
            "unit_economics_crosswalk": {
                "spreadsheet_id": "copy-workbook",
                "read_at": "2026-11-05T10:00:00+00:00",
                "ranges": {
                    "crosswalk": {"values": [
                        ["Ключ", "Строка юнитки", "Новая цена", "Новая скидка, %", "Товар в юнитке"],
                        ["АА|Ветошь 10кг", "9", "р.9 261,74", "70", "Ветошь 10 кг 40х60"],
                    ]},
                    "audit": {"values": [
                        ["Кабинет", "Артикул WB", "Артикул продавца", "Наименование", "Магазин", "Ключ", "Статус связки", "Строка юнитки", "Товар в юнитке", "Цена юнитки", "Скидка юнитки"],
                        ["2", "273197463", "Ветошь 10кг", "Ветошь обтирочная трикотажная ХБ для уборки и протирки 10кг", "АА", "АА|Ветошь 10кг", "ЕСТЬ СВЯЗКА", "9", "Ветошь 10 кг 40х60", "9261,738642", "70"],
                        ["2", "273197464", "Ветошь 10кг", "Ветошь обтирочная трикотажная ХБ для уборки и протирки 10кг", "АА", "АА|Ветошь 10кг", "ЕСТЬ СВЯЗКА", "9", "Ветошь 10 кг 40х60", "9261,738642", "70"],
                    ]},
                    "verification": {"values": [
                        ["Код кабинета", "Кабинет", "Артикул WB", "Артикул продавца из остатков", "Ключ Связка цен", "Есть в Связка цен", "Строка юнитки", "Товар в юнитке", "Есть в WB FBS новая"],
                        ["AA", "AIR", "273197463", "Ветошь 10кг", "АА|Ветошь 10кг", "ДА", "9", "Ветошь 10 кг 40х60", "НЕТ"],
                    ]},
                    "catalog": {"values": catalog_rows},
                },
            },
        },
    }
    out = {"own_27": {"products": []}}

    service._parse_primary_economics_axis(payload, out)

    mapped = out["own_27"]["unit_economics_by_shop_nm_id"]["aa"]["273197463"]
    assert mapped["source_row"] == 9
    assert mapped["verification_source_row"] == 2
    assert mapped["audit_source_row"] is None
    assert mapped["unit_economics_source"] == "Связка цен / Проверка товаров WB / 📊 WB FBS новая"
    assert out["own_27"]["unit_economics_by_shop_nm_id"]["aa"]["273197464"]["audit_source_row"] == 3
    assert mapped["cost_rub"] == 1126.88
    assert mapped["price_rub"] == 2778.52
    assert mapped["profit_rub"] == 333.42
    assert mapped["target_profit_rub"] == 78.29
    assert mapped["unit_economics_decision_ready"] is True
    assert mapped["unit_economics_mapping_notes"] == [
        "verification_catalog_display_flag_false_exact_identity_checked",
        "catalog_display_name_differs_from_linked_unit_name",
        "missing_copy_fields_filled_from_unique_primary_name",
    ]
    assert mapped["unit_economics_fallback_source"] == "primary_unit_economics"
    assert mapped["unit_economics_fallback_source_row"] == 4
    assert mapped["unit_economics_fallback_match"] == "exact_name"
    assert out["own_27"]["primary_unit_economics"]["snapshot_date"] == "2026-11-05"
    assert out["own_27"].get("unit_economics_mapping_diagnostics_by_shop", {}) == {}

    # A listed verification row is authoritative: a negative flag must not fall back
    # to a stale positive mapping in the audit sheet for the same nmID.
    payload["sources"]["unit_economics_crosswalk"]["ranges"]["verification"]["values"][1][5] = "НЕТ"
    blocked = {"own_27": {"products": []}}
    service._parse_primary_economics_axis(payload, blocked)
    assert "273197463" not in blocked["own_27"]["unit_economics_by_shop_nm_id"]["aa"]
    reasons = blocked["own_27"]["unit_economics_mapping_diagnostics_by_shop"]["aa"]["273197463"]["reason_codes"]
    assert "verification_crosswalk_link_not_confirmed" in reasons


def test_primary_name_key_normalizes_volume_and_pack_notation():
    service = PortfolioService(Settings(data_dir="/tmp/wb-econ-key-test", wb_api_token=""))
    assert service._economics_primary_name_key("Бидон 15 л — 1 шт") == service._economics_primary_name_key("Бидон 15")
    assert service._economics_primary_name_key("Бидон 15 л — 2 шт") == service._economics_primary_name_key("Бидон 15 2")
    assert service._economics_primary_name_key("Бидон 15 л — 1 шт") != service._economics_primary_name_key("Бидон 15 л — 2 шт")


def test_primary_unit_economics_reads_live_table_and_uses_subject_to_resolve_duplicate_names(tmp_path):
    service = PortfolioService(Settings(data_dir=str(tmp_path), wb_api_token=""))
    header = [None] * 42
    header[0], header[1] = "Группа товаров", "Товар"

    def plan(group, cost, profit):
        row = [None] * 42
        row[0], row[1] = group, "Ваза 17л ЧЕРНЫЕ"
        row[5], row[8], row[10] = cost, 100, 90
        row[11], row[12], row[15], row[16], row[17] = 30, 20, 5, 3, 2
        row[19], row[20], row[21], row[23] = 2, 2, "УСН 6%", 6
        row[24], row[25], row[26] = profit, 20, 25
        return row

    out = {"own_27": {"products": [{
        "sku": "123", "name": "Вазон 17л Черный", "subject": "Вазоны",
        "cabinet": "Аир", "cost_rub": 0, "price_rub": 100,
    }]}}
    payload = {"generated_at": "2026-11-02T14:00:00+00:00", "sources": {
        "primary_unit_economics": {
            "spreadsheet_id": "primary-sheet-id",
            "read_at": "2026-11-02T14:00:00+00:00",
            "ranges": {"unit_economics": {"values": [
                ["Данные о товарах"], header, [],
                plan("Вазоны", 25, 20), plan("Вазы", 50, 10),
            ]}},
        },
    }}

    service._parse_primary_economics_axis(payload, out)

    product = out["own_27"]["products"][0]
    assert product["primary_unit_source_row"] == 4
    assert product["cost_rub"] == 25
    assert product["price_rub"] == 100
    assert product["profit_rub"] == 20
    assert product["unit_economics_decision_ready"] is True
    assert product["legacy_unit_economics"]["cost_rub"] == 0
    economics = out["own_27"]["primary_unit_economics"]
    assert economics["source_spreadsheet_id"] == "primary-sheet-id"
    assert economics["snapshot_date"] == "2026-11-02"
    assert economics["snapshot_basis"] == "live_source_read"
    assert economics["semantics"] == "plan_model_only"


def test_primary_unit_economics_missing_required_cost_components_do_not_authorize_money_decisions(tmp_path):
    service = PortfolioService(Settings(data_dir=str(tmp_path), wb_api_token=""))
    row = [None] * 42
    row[0], row[1] = "Вазы", "Ваза керамическая"
    row[5], row[8], row[11], row[12], row[23], row[24] = 25, 100, 30, 20, 6, 20
    # Missing plan margin: a positive cost, price, and profit alone do not prove
    # that the modeled economics are complete enough for a monetary decision.
    out = {"own_27": {"products": [{"sku": "123", "name": "Ваза керамическая", "cabinet": "AIR"}]}}
    payload = {"generated_at": "2026-11-02T14:00:00+00:00", "sources": {
        "primary_unit_economics": {
            "read_at": "2026-11-02T14:00:00+00:00",
            "ranges": {"unit_economics": {"values": [["title"], ["group", "item"], [], row]}},
        },
    }}
    service._parse_primary_economics_axis(payload, out)
    product = out["own_27"]["products"][0]
    assert product["unit_economics_role"] == "primary_plan"
    assert product["unit_economics_decision_ready"] is False


def test_primary_unit_economics_zero_cost_rows_do_not_authorize_money_decisions(tmp_path):
    service = PortfolioService(Settings(data_dir=str(tmp_path), wb_api_token=""))
    row = [None] * 42
    row[0], row[1] = "Известь", "Известь 2кг"
    row[5], row[8], row[24] = 0, 375, 227.47
    out = {"own_27": {"products": [{"sku": "123", "name": "Известь 2 кг", "cabinet": "Дом Саныча"}]}}
    payload = {"generated_at": "2026-11-02T14:00:00+00:00", "sources": {
        "primary_unit_economics": {
            "read_at": "2026-11-02T14:00:00+00:00",
            "ranges": {"unit_economics": {"values": [["title"], ["group", "item"], [], row]}},
        },
    }}
    service._parse_primary_economics_axis(payload, out)
    product = out["own_27"]["products"][0]
    assert product["unit_economics_role"] == "primary_plan"
    assert product["cost_rub"] == 0
    assert product["unit_economics_decision_ready"] is False


def test_order_history_builds_real_short_vs_baseline_demand_trend(tmp_path):
    s = Settings(data_dir=str(tmp_path), wb_api_token="")
    svc = PortfolioService(s)
    out = {"own_27": {"products": [{"sku": "123", "name": "Товар", "orders_per_day": 10}]}}
    rows = [["Дата заказа", "Артикул WB"]]
    # 1–11 Sep: 10/day, 12–14 Sep: 20/day. 15 Sep is intentionally excluded as partial latest day.
    for day in range(1, 12):
        rows += [[f"2026-09-{day:02d} 12:00:00", "123"] for _ in range(10)]
    for day in range(12, 15):
        rows += [[f"2026-09-{day:02d} 12:00:00", "123"] for _ in range(20)]
    rows += [["2026-09-15 10:00:00", "123"] for _ in range(3)]
    payload = {"sources": {"own_27": {"ranges": {"orders_history": {"values": rows}}}}}
    svc._parse_own_27(payload, out)
    product = out["own_27"]["products"][0]
    assert product["orders_daily_3d"] == 20
    assert product["orders_daily_baseline_11d"] == 10
    assert product["orders_trend_pct"] == 100
    assert product["demand_history_asof"] == "2026-09-14"


def test_svodnaya_groups_by_wb_subject_category_and_current_k2_header(tmp_path):
    service = PortfolioService(Settings(data_dir=str(tmp_path), wb_api_token=""))
    headers = [
        "Артикул продавца WB", "Предмет WB", "Артикул WB", "Ссылка WB", "Баркод WB",
        "Артикул продавца Ozon", "Ozon артикул", "", "n", "Цена", "Цена для клиента", "% СПП",
        "FBS долг по заказам", "Остатки ФФ", "К2 ФФ", "ФФ Иваново", "", "Остатки FBW",
        "в пути к клиенту", "в пути от клиенту", "Остатки WB FBS", "Остатки Ozon FBS",
        "Продажи, шт", "Заказы, шт", "Заказов в день", "Заказы,руб",
    ]
    group = ["Бидон 5л", "", "", "", "", "", "", "", "", 0, "", "", 0, 100, 100, 0, "", 0, 0, 0, 0, 0]
    wb = ["Бидон 5л 1шт", "Бидоны", "123", "", "", "Бидон_5л", "456", "", "", 600, 390, "", 0, 0, "", 0, "", 0, 0, 0, 91, 88, 3, 7, 1, 2700]
    oz2 = ["Бидон 5л 1шт (Ozon каб.2)", "Бидоны · Ozon каб.2", "", "", "", "Бидон_5л", "789", "", "", 0, 427, "", 0, 0, "", 0, "", 0, 0, 0, 0, 0]
    payload = {"sources": {"own_27": {"ranges": {"summary": {"values": [headers, group, wb, oz2]}}}}}
    out = {}
    service._parse_own_27(payload, out)

    product = out["own_27"]["products"][0]
    assert product["sku"] == "123"
    assert product["group_name"] == "Бидоны"
    assert product["wb_fbs_stock"] == 91
    assert product["safe_stock"] == 91
    assert product["safe_stock_source"] == "WB FBS"
    assert product["ozon_seller_article"] == "Бидон_5л"
    assert product["ozon_sku"] == "456"

    ozon = {x["sku"]: x for x in out["own_27"]["ozon_products"]}
    assert ozon["789"]["cabinet"] == "Ozon каб.2"
    assert ozon["789"]["group_name"] == "Бидоны"
    group_row = out["own_27"]["product_groups"][0]
    assert group_row["name"] == "Бидоны"
    assert "123" in group_row["wb_nm_ids"]
    assert {"456", "789"} <= set(group_row["ozon_skus"])


def test_ozon_variants_are_reconciled_per_cabinet(tmp_path):
    service = PortfolioService(Settings(data_dir=str(tmp_path), wb_api_token=""))
    out = {
        "own_27": {
            "ozon_products": [
                {"sku": "456", "seller_article": "Бидон_5л", "cabinet": "Ozon каб.1"},
                {"sku": "789", "seller_article": "Бидон_5л", "cabinet": "Ozon каб.2"},
            ]
        }
    }
    cabinet_rows = [
        ["OZON"],
        ["Кабинет", "Название", "Статус API", "Карточек", "FBS позиций", "На складе", "Резерв", "Доступно", "Комментарий"],
        ["1", "Текущий Ozon", "ACTIVE", "195", "187", "1000", "2", "998", "ok"],
        ["2", "Новое ИП", "ORDERS_ACTIVE / MANUAL_PRODUCTS", "62", "62", "100", "0", "100", "manual"],
        [],
        ["", "", "", "", "", "", "", "", "", "", "", "КАБИНЕТ 1"],
        ["", "", "", "", "", "", "", "", "", "", "", "Артикул", "Наименование", "На складе", "Резерв", "Доступно"],
        ["", "", "", "", "", "", "", "", "", "", "", "Бидон_5л", "Бидон 5 литров", "884", "2", "882"],
        ["", "", "", "", "", "", "", "", "", "", "", "КАБИНЕТ 2"],
        ["", "", "", "", "", "", "", "", "", "", "", "Артикул", "Наименование", "На складе", "Резерв", "Доступно"],
        ["", "", "", "", "", "", "", "", "", "", "", "Цемент_5кг", "Цемент 5 кг", "89", "1", "88"],
    ]
    orders = [
        ["Кабинет", "Схема", "Номер отправления", "ID заказа", "Номер заказа", "Статус", "Подстатус", "Принят в обработку", "Дата отгрузки", "Дата доставки", "ID причины отмены", "Регион", "Город", "ID склада", "Склад", "SKU", "Артикул", "Товар", "Кол-во", "Цена", "Сумма строки", "Валюта"],
        ["1", "FBS", "x", "1", "1", "delivered", "", "2026-09-24T10:00:00Z", "", "", "", "", "", "", "", "456", "Бидон_5л", "Бидон 5 л", "2", "979", "1958", "RUB"],
    ]
    payload = {"sources": {"own_27": {"ranges": {
        "ozon_cabinets": {"values": cabinet_rows},
        "ozon_orders": {"values": orders},
    }}}}
    service._parse_ozon_operating(payload, out)

    by_cab = {x["cabinet"]: x for x in out["own_27"]["ozon_products"]}
    assert by_cab["Ozon каб.1"]["feed_present"] is True
    assert by_cab["Ozon каб.1"]["feed_available"] == 882
    assert by_cab["Ozon каб.1"]["orders_qty_period"] == 2
    assert by_cab["Ozon каб.1"]["operating_status"] == "selling"
    assert by_cab["Ozon каб.2"]["feed_present"] is False
    assert by_cab["Ozon каб.2"]["orders_qty_period"] == 0
    assert by_cab["Ozon каб.2"]["operating_status"] == "mapped_but_not_in_current_feed_or_orders"


def test_svodnaya_duplicate_orders_per_day_keeps_wb_velocity(tmp_path):
    service = PortfolioService(Settings(data_dir=str(tmp_path), wb_api_token=""))
    headers = [
        "Артикул продавца WB", "Предмет WB", "Артикул WB", "Артикул продавца Ozon", "Ozon артикул",
        "Остатки WB FBS", "Заказы, шт", "Заказов в день", "Заказы Ozon", "Заказов в день",
    ]
    group = ["Известь", "", "", "", "", 0, 1000, 143, 70, 10]
    row = ["Известь 4 кг", "Смеси", "566189858", "Известь_4кг", "3474252014", 999, 1070, 153, 52, 7]
    payload = {"sources": {"own_27": {"ranges": {"summary": {"values": [headers, group, row]}}}}}
    out = {}
    service._parse_own_27(payload, out)
    product = out["own_27"]["products"][0]
    assert product["orders_per_day"] == 153
    assert product["ozon_orders_per_day"] == 7


def _portfolio_snapshot(source_dates, generated_at="2026-10-31T11:30:00Z"):
    return {
        "mode": "live_google_sheets",
        "generated_at": generated_at,
        "source_health": [
            {"id": source_id, "name": source_id, "status": "fresh", "freshness": source_dates[source_id]}
            for source_id in ("own_27", "weekly_summary", "sanych_sellmonitor")
        ] + [
            {"id": "air_fbs", "name": "air_fbs", "status": "stale", "freshness": "21.08.2026"}
        ],
    }


def test_cached_portfolio_is_not_current_when_required_source_dates_are_old():
    live = _portfolio_snapshot({
        "own_27": "18.09.2026",
        "weekly_summary": "16.09.2026",
        "sanych_sellmonitor": "16.09.2026",
    })

    current, reason = _assess_snapshot_freshness(live, datetime(2026, 10, 31, 12, tzinfo=timezone.utc))

    assert current is False
    assert live["source_health"][0]["status"] == "stale"
    assert "18.09.2026" in reason
    assert "Текущие решения" in reason


def test_recent_core_sources_keep_snapshot_current_even_if_optional_source_is_stale():
    live = _portfolio_snapshot({
        "own_27": "30.10.2026",
        "weekly_summary": "30.10.2026",
        "sanych_sellmonitor": "30.10.2026",
    })

    current, reason = _assess_snapshot_freshness(live, datetime(2026, 10, 31, 12, tzinfo=timezone.utc))

    assert current is True
    assert reason is None
    assert live["source_health"][3]["status"] == "stale"


def test_recent_source_facts_do_not_hide_old_saved_snapshot():
    live = _portfolio_snapshot({
        "own_27": "30.10.2026",
        "weekly_summary": "30.10.2026",
        "sanych_sellmonitor": "30.10.2026",
    }, generated_at="2026-10-29T00:00:00Z")

    current, reason = _assess_snapshot_freshness(live, datetime(2026, 10, 31, 12, tzinfo=timezone.utc))

    assert current is False
    assert "снимок получен" in reason


def test_github_portfolio_snapshot_is_used_and_freshness_is_rechecked(tmp_path):
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    yesterday = (now - timedelta(days=1)).date().isoformat()
    snapshot = {
        "mode": "live",
        "generated_at": now.isoformat(),
        "source_health": [
            {"id": source_id, "name": source_id, "status": "fresh", "freshness": yesterday}
            for source_id in ("own_27", "weekly_summary", "sanych_sellmonitor")
        ],
        "stores": [],
    }
    service = PortfolioService(Settings(data_dir=str(tmp_path), wb_api_token=""))

    assert service.set_github_snapshot(snapshot) is True
    result = service.snapshot()

    assert result["data_origin"] == "github_encrypted_runtime"
    assert result["current_data"] is True
    assert result["data_status"] == "current"


def test_github_portfolio_snapshot_cannot_bypass_stale_source_gate(tmp_path):
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    old_date = (now - timedelta(days=10)).date().isoformat()
    snapshot = {
        "mode": "live",
        "generated_at": now.isoformat(),
        "source_health": [
            {"id": source_id, "name": source_id, "status": "fresh", "freshness": old_date}
            for source_id in ("own_27", "weekly_summary", "sanych_sellmonitor")
        ],
        "stores": [],
    }
    service = PortfolioService(Settings(data_dir=str(tmp_path), wb_api_token=""))

    assert service.set_github_snapshot(snapshot) is True
    result = service.snapshot()

    assert result["data_origin"] == "github_encrypted_runtime"
    assert result["current_data"] is False
    assert result["data_status"] == "stale_source_snapshot"
    assert "Текущие решения" in result["stale_reason"]
