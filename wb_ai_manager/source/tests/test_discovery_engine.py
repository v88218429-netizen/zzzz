from wb_control_center.discovery_engine import discover_cross_sku_patterns


def test_cross_sku_discovery_requires_holdout_replication():
    products=[]
    for i in range(100):
        products.append({
            "sku":str(1000+i),
            "price_rub":100+i,
            "orders_per_day":200-i,
            "drr_pct":10+(i%7),
        })
    out=discover_cross_sku_patterns({"own_27":{"products":products}})
    price_orders=next((x for x in out if x["pattern_id"]=="price_rub__orders_per_day"),None)
    assert price_orders is not None
    assert price_orders["rho_discovery"] < -0.9
    assert price_orders["rho_holdout"] < -0.9
    assert "не доказанная причинность" in price_orders["interpretation"]


def test_random_or_missing_pairs_are_not_forced_into_discoveries():
    products=[{"sku":str(i),"price_rub":100+i} for i in range(80)]
    assert discover_cross_sku_patterns({"own_27":{"products":products}}) == []
