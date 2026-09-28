from __future__ import annotations

import math
import zlib
from typing import Any


_FEATURES = {
    "price_rub": "цена WB",
    "orders_per_day": "текущий темп заказов",
    "orders_trend_7v21_pct": "изменение темпа 7д к 21д",
    "drr_pct": "ДРР",
    "margin_pct": "расчётная маржа",
    "historical_buyout_pct": "исторический выкуп",
    "safe_stock": "доступный остаток",
    "top_search_position": "позиция по главному запросу",
    "top_search_frequency": "частотность главного запроса",
    "recent_cohort_maturity_pct": "зрелость свежей когорты",
}

# Curated pairs avoid trivial formula identities such as profit<->margin.
_PAIRS = [
    ("price_rub", "orders_per_day"),
    ("price_rub", "orders_trend_7v21_pct"),
    ("price_rub", "drr_pct"),
    ("drr_pct", "orders_per_day"),
    ("drr_pct", "orders_trend_7v21_pct"),
    ("historical_buyout_pct", "orders_per_day"),
    ("historical_buyout_pct", "drr_pct"),
    ("top_search_position", "orders_per_day"),
    ("top_search_position", "orders_trend_7v21_pct"),
    ("top_search_frequency", "orders_per_day"),
    ("safe_stock", "orders_per_day"),
    ("margin_pct", "orders_per_day"),
]


def _n(v: Any) -> float | None:
    try:
        if v is None or isinstance(v, bool):
            return None
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _rank(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    out = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i + 1
        while j < len(indexed) and indexed[j][1] == indexed[i][1]:
            j += 1
        avg = (i + 1 + j) / 2.0
        for k in range(i, j):
            out[indexed[k][0]] = avg
        i = j
    return out


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    den = math.sqrt(sum(x*x for x in dx) * sum(y*y for y in dy))
    if den <= 0:
        return None
    return sum(a*b for a, b in zip(dx, dy)) / den


def _spearman(rows: list[tuple[float, float]]) -> float | None:
    if len(rows) < 3:
        return None
    xs = [x for x, _ in rows]
    ys = [y for _, y in rows]
    return _pearson(_rank(xs), _rank(ys))


def _bucket(sku: str) -> str:
    return "holdout" if zlib.crc32(sku.encode("utf-8")) % 5 == 0 else "discovery"


def _pair_rows(products: list[dict[str, Any]], a: str, b: str) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    train: list[tuple[float, float]] = []
    test: list[tuple[float, float]] = []
    for p in products:
        x = _n(p.get(a))
        y = _n(p.get(b))
        sku = str(p.get("sku") or "")
        if x is None or y is None or not sku:
            continue
        (test if _bucket(sku) == "holdout" else train).append((x, y))
    return train, test


def _direction(rho: float) -> str:
    return "растут вместе" if rho > 0 else "двигаются в противоположных направлениях"


def _next_test(a: str, b: str) -> str:
    pair = {a, b}
    if "price_rub" in pair and ("orders_per_day" in pair or "orders_trend_7v21_pct" in pair):
        return "Проверить связь во времени внутри сопоставимых SKU и, если она сохраняется, поставить малый ценовой тест с контрольной группой."
    if "drr_pct" in pair and ("orders_per_day" in pair or "orders_trend_7v21_pct" in pair):
        return "Разделить кампании/SKU по экономике и проверить инкрементальный эффект изменения рекламной нагрузки, контролируя органику."
    if "top_search_position" in pair:
        return "Проверить лаговую связь позиция→заказы по дневной истории и исключить обратную причинность, когда продажи сами улучшают позицию."
    if "top_search_frequency" in pair:
        return "Проверить, предсказывает ли изменение частотности будущие заказы на следующем временном окне, а не совпадает с ними постфактум."
    if "historical_buyout_pct" in pair:
        return "Проверить на зрелых когортах и отдельно по категориям, чтобы не смешивать разные сроки выкупа."
    if "safe_stock" in pair:
        return "Проверить по истории остатков и доступности; дни отсутствия товара исключить как цензурированные наблюдения спроса."
    return "Проверить связь на следующем временном окне и затем спроектировать минимальный контролируемый эксперимент."


def discover_cross_sku_patterns(portfolio: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    products = (((portfolio or {}).get("own_27") or {}).get("products") or [])
    products = [x for x in products if isinstance(x, dict)]
    out: list[dict[str, Any]] = []

    for a, b in _PAIRS:
        train, test = _pair_rows(products, a, b)
        if len(train) < 30 or len(test) < 8:
            continue
        r1 = _spearman(train)
        r2 = _spearman(test)
        if r1 is None or r2 is None:
            continue
        # Discovery must reproduce out-of-sample in the same direction. This is
        # intentionally stricter than showing every correlation found in one snapshot.
        if abs(r1) < 0.35 or abs(r2) < 0.18 or r1 * r2 <= 0:
            continue
        strength = min(abs(r1), abs(r2))
        score = round(min(100.0, strength * 100.0 + min(20.0, (len(train) + len(test)) / 20.0)), 1)
        out.append({
            "pattern_id": f"{a}__{b}",
            "feature_a": a,
            "feature_b": b,
            "label_a": _FEATURES[a],
            "label_b": _FEATURES[b],
            "discovery_n": len(train),
            "holdout_n": len(test),
            "rho_discovery": round(r1, 3),
            "rho_holdout": round(r2, 3),
            "strength_score": score,
            "statement": f"На текущем поперечном срезе «{_FEATURES[a]}» и «{_FEATURES[b]}» {_direction(r1)}; связь повторилась на отложенной части SKU.",
            "interpretation": "Это воспроизводимая ассоциация, но не доказанная причинность.",
            "next_test": _next_test(a, b),
        })

    out.sort(key=lambda x: (-float(x["strength_score"]), x["pattern_id"]))
    return out[:limit]
