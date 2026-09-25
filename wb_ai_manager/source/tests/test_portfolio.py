from wb_control_center.config import Settings
from wb_control_center.portfolio import PortfolioService


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


def test_svodnaya_groups_variants_and_current_k2_header(tmp_path):
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
    assert product["group_name"] == "Бидон 5л"
    assert product["wb_fbs_stock"] == 91
    assert product["safe_stock"] == 91
    assert product["safe_stock_source"] == "WB FBS"
    assert product["ozon_seller_article"] == "Бидон_5л"
    assert product["ozon_sku"] == "456"

    ozon = {x["sku"]: x for x in out["own_27"]["ozon_products"]}
    assert ozon["789"]["cabinet"] == "Ozon каб.2"
    assert ozon["789"]["group_name"] == "Бидон 5л"
    group_row = out["own_27"]["product_groups"][0]
    assert group_row["name"] == "Бидон 5л"
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
