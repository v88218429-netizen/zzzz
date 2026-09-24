from __future__ import annotations

from typing import Any


class DemoWBClient:
    """In-memory WB cabinet for testing every layer without any external key."""

    def __init__(self):
        self.session = True
        self.tools = {
            "wb_list_shops", "wb_token_info", "wb_degradations", "wb_card_errors", "wb_cards_list", "wb_banned_products",
            "wb_prices_quarantine", "wb_advert_list", "wb_advert_stats", "wb_advert_pause", "wb_advert_budget", "wb_advert_bids_recommendations", "wb_advert_clusters", "wb_advert_clusters_stats", "wb_stats_stocks",
            "wb_stats_sales", "wb_acceptance_coefficients", "wb_analytics_detail", "wb_search_report", "wb_prices_list",
            "wb_promotions_audit", "wb_finance_balance", "wb_finance_report", "wb_paid_storage",
            "wb_analytics_measurement_penalties", "wb_deductions", "wb_analytics_acceptance",
            "wb_new_feedbacks_questions", "wb_seller_rating", "wb_feedbacks_list", "wb_questions_list",
            "wb_chat_events", "wb_orders_new", "wb_supplies_reshipment", "wb_returns_list", "wb_documents_list",
        }
        self.paused_campaigns: set[int] = set()

    async def start(self) -> None:
        self.session = True

    async def close(self) -> None:
        self.session = False

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        a = arguments or {}
        if tool == "wb_list_shops":
            return {"shops": [{"shop_id": "demo", "name": "DEMO WB cabinet"}]}
        if tool == "wb_token_info":
            return {"mode": "demo", "readOnly": False, "categories": ["content", "analytics", "advert", "finance", "orders"]}
        if tool == "wb_degradations":
            return []
        if tool == "wb_card_errors":
            return [{"nmID": 1003, "error": "Карточка отклонена: обязательная характеристика не заполнена"}]
        if tool == "wb_cards_list":
            return {"cards": [
                {"nmID": 1001, "vendorCode": "DEMO-1001", "title": "Ведро 5 л", "mediaFiles": [{"url": f"https://example.test/{i}.jpg"} for i in range(5)], "characteristics": [{"name":"Объем","value":"5 л"}]},
                {"nmID": 1002, "vendorCode": "DEMO-1002", "title": "Бидон 10 л", "mediaFiles": [{"url": f"https://example.test/b{i}.jpg"} for i in range(12)], "characteristics": [{"name":"Объем","value":"10 л"}]},
            ]}
        if tool == "wb_banned_products":
            return []
        if tool == "wb_prices_quarantine":
            return []
        if tool == "wb_advert_list":
            return {"campaigns": [
                {"advertId": 501, "status": 9, "name": "Ведро 5 л — поиск", "paymentType": "cpm", "nmIds": [769654698], "bidKopecks": 5200},
                {"advertId": 502, "status": 9, "name": "Бидон 10 л — поиск", "paymentType": "cpm", "nmIds": [566189857], "bidKopecks": 4600},
            ]}
        if tool == "wb_advert_stats":
            # Campaign 501 is deliberately bad; 502 is healthy and has rising demand.
            return [
                {"advertId": 501, "sum": 1840, "orders": 0, "sum_price": 0, "views": 15100, "clicks": 410, "ctr": 2.72, "cpc": 4.49,
                 "days": [
                    {"date":"2026-09-12","sum":220,"orders":0,"sum_price":0,"views":1900,"clicks":48},
                    {"date":"2026-09-13","sum":240,"orders":0,"sum_price":0,"views":2000,"clicks":52},
                    {"date":"2026-09-14","sum":255,"orders":0,"sum_price":0,"views":2100,"clicks":58},
                    {"date":"2026-09-15","sum":265,"orders":0,"sum_price":0,"views":2150,"clicks":60},
                    {"date":"2026-09-16","sum":280,"orders":0,"sum_price":0,"views":2250,"clicks":63},
                    {"date":"2026-09-17","sum":285,"orders":0,"sum_price":0,"views":2300,"clicks":64},
                    {"date":"2026-09-18","sum":295,"orders":0,"sum_price":0,"views":2400,"clicks":65}]},
                {"advertId": 502, "sum": 910, "orders": 18, "sum_price": 25800, "views": 12300, "clicks": 530, "ctr": 4.31, "cpc": 1.72,
                 "days": [
                    {"date":"2026-09-12","sum":105,"orders":1,"sum_price":1430,"views":1350,"clicks":55},
                    {"date":"2026-09-13","sum":110,"orders":2,"sum_price":2860,"views":1400,"clicks":58},
                    {"date":"2026-09-14","sum":120,"orders":2,"sum_price":2860,"views":1500,"clicks":62},
                    {"date":"2026-09-15","sum":125,"orders":2,"sum_price":2860,"views":1600,"clicks":68},
                    {"date":"2026-09-16","sum":135,"orders":3,"sum_price":4290,"views":1900,"clicks":80},
                    {"date":"2026-09-17","sum":150,"orders":4,"sum_price":5720,"views":2200,"clicks":94},
                    {"date":"2026-09-18","sum":165,"orders":4,"sum_price":5780,"views":2350,"clicks":113}]},
            ]
        if tool == "wb_advert_budget":
            aid = int(a.get("advert_id", 0))
            return {"advertId": aid, "cash": 12000 if aid == 501 else 18500, "netting": True}
        if tool == "wb_advert_bids_recommendations":
            aid = int(a.get("advert_id", 0)); nm = int(a.get("nm_id", 0))
            if aid == 501:
                return {"advertId": aid, "nmId": nm, "base": {"competitiveBid":{"bidKopecks":4800},"leadersBid":{"bidKopecks":6900}}, "normQueries": []}
            return {"advertId": aid, "nmId": nm, "base": {"competitiveBid":{"bidKopecks":5000},"leadersBid":{"bidKopecks":6600}}, "normQueries": [{"normQuery":"бидон 10 л","reachMin":{"bidKopecks":4700},"reachMedium":{"bidKopecks":5200},"reachMax":{"bidKopecks":6400}}]}
        if tool == "wb_advert_clusters":
            aid = int(a.get("advert_id", 0))
            return {"advertId": aid, "normQueries": [{"normQuery":"ведро 5 л" if aid==501 else "бидон 10 л"}]}
        if tool == "wb_advert_clusters_stats":
            aid = int(a.get("advert_id", 0))
            return {"advertId": aid, "items": [{"normQuery":"ведро 5 л" if aid==501 else "бидон 10 л", "views": 5200, "clicks": 210, "orders": 0 if aid==501 else 9, "sum": 760 if aid==501 else 430}]}
        if tool == "wb_advert_pause":
            aid = int(a.get("advert_id", 0))
            self.paused_campaigns.add(aid)
            return {"ok": True, "advert_id": aid, "status": "paused", "mode": "demo"}
        if tool == "wb_stats_stocks":
            return [
                {"nmId": 1001, "quantityFull": 8},
                {"nmId": 1002, "quantityFull": 420},
            ]
        if tool == "wb_stats_sales":
            return [
                {"nmId": 1001, "quantity": 70, "saleID": "demo-sale-1"},
                {"nmId": 1002, "quantity": 28, "saleID": "demo-sale-2"},
            ]
        if tool == "wb_acceptance_coefficients":
            return [
                {"warehouseID": 117986, "warehouseName": "Коледино", "coefficient": 0, "allowUnload": True, "date": "demo"},
                {"warehouseID": 120762, "warehouseName": "Электросталь", "coefficient": 3, "allowUnload": True, "date": "demo"},
            ]
        if tool == "wb_analytics_detail":
            return {"items": [
                {"nmID": 1001, "openCardCount": 2100, "addToCartCount": 115, "ordersCount": 31, "conversions": {"addToCartPercent": 5.5, "cartToOrderPercent": 27.0}},
                {"nmID": 1002, "openCardCount": 1900, "addToCartCount": 220, "ordersCount": 85, "conversions": {"addToCartPercent": 11.6, "cartToOrderPercent": 38.6}},
            ]}
        if tool == "wb_search_report":
            return {"items": [
                {"nmID": 1001, "searchText": "ведро пластиковое 5 л", "position": 18},
                {"nmID": 1002, "searchText": "бидон 10 л", "position": 6},
            ]}
        if tool == "wb_prices_list":
            return {"data": [
                {"nmID": 1001, "price": 1390, "discountedPrice": 1290},
                {"nmID": 1002, "price": 1890, "discountedPrice": 1790},
            ]}
        if tool == "wb_promotions_audit":
            return {"items": [{"promotionId": 9001, "nmID": 1001, "price": 1290, "planPrice": 1090, "type": "auto"}]}
        if tool == "wb_finance_balance":
            return {"forWithdraw": 243500, "inTransit": 52100}
        if tool == "wb_finance_report":
            return {"data": [
                {"nmID": 1001, "retail_amount": 62400, "ppvz_for_pay": 43800, "delivery_rub": 9200, "penalty": 0},
                {"nmID": 1002, "retail_amount": 87300, "ppvz_for_pay": 66400, "delivery_rub": 8700, "penalty": 1450},
            ]}
        if tool == "wb_paid_storage":
            return {"data": [{"nmID": 1002, "warehousePrice": 880}]}
        if tool == "wb_analytics_measurement_penalties":
            return []
        if tool == "wb_deductions":
            return [{"nmID": 1002, "amount": 1450, "reason": "Неверное вложение"}]
        if tool == "wb_analytics_acceptance":
            return {"data": [{"warehouseName": "Электросталь", "amount": 320}]}
        if tool == "wb_new_feedbacks_questions":
            return {"hasNewFeedbacks": True, "hasNewQuestions": True}
        if tool == "wb_seller_rating":
            return {"rating": 4.72}
        if tool == "wb_feedbacks_list":
            return {"feedbacks": [
                {"id": f"f{i}", "nmId": 1001, "productValuation": 2 if i < 2 else 5, "text": "demo feedback"} for i in range(6)
            ]}
        if tool == "wb_questions_list":
            return {"questions": [{"id": f"q{i}", "nmId": 1001, "text": "demo question"} for i in range(5)]}
        if tool == "wb_chat_events":
            return {"events": [{"id": "ce1", "eventType": "message", "message": "Не тот товар в заказе"}]}
        if tool == "wb_orders_new":
            return {"orders": [{"id": 7001, "article": "DEMO-1001"}, {"id": 7002, "article": "DEMO-1002"}]}
        if tool == "wb_supplies_reshipment":
            return [{"orderId": 6999, "reason": "reshipment"}]
        if tool == "wb_returns_list":
            return {"claims": [
                {"id": f"r{i}", "nmId": 1001, "reason": "Не соответствует описанию"} for i in range(4)
            ]}
        if tool == "wb_documents_list":
            return {"items": [{"id": "d1", "category": "weekly_report"}]}
        raise RuntimeError(f"Demo tool not implemented: {tool}")
