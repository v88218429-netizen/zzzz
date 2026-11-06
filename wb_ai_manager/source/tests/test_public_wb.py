from wb_control_center.public_wb import PublicWBClient


def test_market_analysis_combines_position_price_rating_reviews():
    products=[
        {"id":1,"price_rub":120,"rating":4.4,"reviews":100},
        {"id":2,"price_rub":100,"rating":4.8,"reviews":500},
        {"id":3,"price_rub":110,"rating":4.7,"reviews":300},
    ]
    a=PublicWBClient.analyze(products,[1])
    assert a["own_position"] == 1
    assert a["competitor_median_price_rub"] == 105
    assert round(a["price_vs_median_pct"],1) == 14.3
    assert a["rating_vs_median"] < 0
    assert a["own_review_count"] == 100


def test_public_search_destination_is_explicit():
    c=PublicWBClient(dest='-999')
    try:
        assert c.dest == '-999'
    finally:
        import asyncio
        asyncio.run(c.close())

def test_competitor_snapshot_records_search_destination():
    from pathlib import Path
    source=(Path(__file__).parents[1]/'src/wb_control_center/agents/competitors.py').read_text(encoding='utf-8')
    assert "'search_dest':client.dest or None" in source
    assert 'PublicWBClient(dest=self.ctx.settings.public_wb_dest)' in source

def test_public_card_lookup_returns_identity_without_economics():
    import asyncio

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"products": [{
                "id": 1994573765,
                "name": "Корзина для белья белая с крышкой пластиковая в ванную",
                "brand": "Милих",
                "subjectName": "Аксессуары для стирки",
            }]}}

    class FakeClient:
        def __init__(self):
            self.request = None

        async def get(self, url, params):
            self.request = (url, params)
            return Response()

        async def aclose(self):
            return None

    client = PublicWBClient(dest="-1257786")
    fake = FakeClient()
    client.client = fake
    try:
        card = asyncio.run(client.product_card_by_nm_id("1994573765"))
    finally:
        asyncio.run(client.close())

    assert fake.request[0] == "https://card.wb.ru/cards/v2/detail"
    assert fake.request[1]["nm"] == "1994573765"
    assert card["name"] == "Корзина для белья белая с крышкой пластиковая в ванную"
    assert card["brand"] == "Милих"
    assert card["source"] == "wb_public_card"
    assert "price" not in card and "cost" not in card and "profit" not in card


def test_public_card_lookup_ignores_invalid_nm_id():
    import asyncio

    client = PublicWBClient()
    try:
        assert asyncio.run(client.product_card_by_nm_id("not-a-number")) is None
        assert asyncio.run(client.product_card_by_nm_id("0")) is None
    finally:
        asyncio.run(client.close())
